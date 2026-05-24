"""Shared infrastructure for all providers.

Holds the HTTP client (timeouts + polite retry/backoff + identifying User-Agent), a tiny
in-process TTL cache, the UTC provenance stamp, and the single output-contract
builder every tool returns. Keeping the contract in one place guarantees every
provider emits identical ``{metadata, data, csv}`` shapes.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import random
import threading
import time
from typing import Any

import httpx

USER_AGENT = (
    "SocioEcon-Data-MCP/0.1 (official statistics tooling for the Socio-Economic Data MCP; "
    "contact: noreply@example.invalid)"
)

# Statuses worth retrying. 413 is deliberately NOT here: it means "too large",
# which a retry cannot fix — the caller must narrow the query instead.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ProviderError(RuntimeError):
    """Unrecoverable error talking to a provider API (surfaced to the caller)."""


class RequestTooLargeError(ProviderError):
    """Provider refused the request as too large (e.g. Eurostat HTTP 413 async)."""


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 'Z' timestamp (provenance stamp)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TTLCache:
    """Thread-safe in-memory TTL cache keyed by the fully-resolved request URL.

    Deliberately tiny and dependency-free: enough to be polite to provider APIs
    within a session without pulling in requests-cache/hishel (one less thing to
    keep working on a server / new Python).
    """

    def __init__(self, ttl_seconds: float = 3600.0, maxsize: int = 256) -> None:
        self._ttl = ttl_seconds
        self._maxsize = maxsize
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < time.monotonic():
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._store) >= self._maxsize:
                now = time.monotonic()
                for k in [k for k, (e, _) in self._store.items() if e < now]:
                    self._store.pop(k, None)
                if len(self._store) >= self._maxsize:
                    self._store.pop(next(iter(self._store)), None)
            self._store[key] = (time.monotonic() + self._ttl, value)


_cache = TTLCache()
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=_DEFAULT_TIMEOUT,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
    return _client


def _sleep_backoff(attempt: int, resp: httpx.Response | None = None) -> None:
    if resp is not None and "Retry-After" in resp.headers:
        try:
            time.sleep(min(float(resp.headers["Retry-After"]), 30.0))
            return
        except ValueError:
            pass
    time.sleep(min(2.0**attempt + random.uniform(0, 0.5), 30.0))


def resolve_url(url: str, params: dict | None = None) -> str:
    """The exact URL that will be requested (for cache key + provenance)."""
    return str(get_client().build_request("GET", url, params=params).url)


def fetch_json(url: str, params: dict | None = None, *, use_cache: bool = True) -> tuple[Any, str]:
    """GET JSON with retry/backoff. Returns ``(parsed_json, resolved_url)``.

    ``params`` values may be lists to repeat a key (e.g. ``{"geo": ["BE1", "BE2"]}``).
    Raises :class:`RequestTooLargeError` on HTTP 413 and :class:`ProviderError`
    on other unrecoverable failures.
    """
    final_url = resolve_url(url, params)
    if use_cache:
        cached = _cache.get(final_url)
        if cached is not None:
            return cached, final_url

    client = get_client()
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.get(url, params=params)
        except httpx.RequestError as exc:
            last_exc = exc
            _sleep_backoff(attempt)
            continue
        if resp.status_code == 413:
            raise RequestTooLargeError(
                "Provider refused the request as too large (HTTP 413). "
                "Narrow the query (fewer geos/periods/dimensions)."
            )
        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES - 1:
            _sleep_backoff(attempt, resp)
            continue
        if resp.status_code >= 400:
            raise ProviderError(
                f"{resp.status_code} from {final_url}: {resp.text[:300]}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"Non-JSON response from {final_url}: {exc}") from exc
        if use_cache:
            _cache.set(final_url, data)
        return data, final_url

    raise ProviderError(f"Network error after {_MAX_RETRIES} attempts for {final_url}: {last_exc}")


def fetch_text(
    url: str, params: dict | None = None, *, headers: dict | None = None, use_cache: bool = True
) -> tuple[str, str]:
    """GET text (e.g. the Eurostat catalogue TSV, OECD SDMX-CSV). Returns ``(text, resolved_url)``."""
    final_url = resolve_url(url, params)
    if use_cache:
        cached = _cache.get(final_url)
        if cached is not None:
            return cached, final_url
    client = get_client()
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.get(url, params=params, headers=headers)
        except httpx.RequestError as exc:
            last_exc = exc
            _sleep_backoff(attempt)
            continue
        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES - 1:
            _sleep_backoff(attempt, resp)
            continue
        if resp.status_code >= 400:
            raise ProviderError(f"{resp.status_code} from {final_url}: {resp.text[:300]}")
        text = resp.text
        if use_cache:
            _cache.set(final_url, text)
        return text, final_url
    raise ProviderError(f"Network error after {_MAX_RETRIES} attempts for {final_url}: {last_exc}")


def matches_query(query: str, *fields: str) -> bool:
    """True if every whitespace-separated token of ``query`` appears (case-insensitive)
    somewhere in the combined ``fields``. More forgiving than a single-substring match,
    so word order / extra words don't cause misses in dataset search."""
    hay = " ".join(f.lower() for f in fields if f)
    return all(tok in hay for tok in query.lower().split())


def post_json(url: str, json_body: dict, *, use_cache: bool = True) -> tuple[Any, str]:
    """POST a JSON body and return ``(parsed_json, url)``. Used for GraphQL endpoints."""
    cache_key = url + "|" + json.dumps(json_body, sort_keys=True)
    if use_cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached, url
    client = get_client()
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.post(url, json=json_body)
        except httpx.RequestError as exc:
            last_exc = exc
            _sleep_backoff(attempt)
            continue
        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES - 1:
            _sleep_backoff(attempt, resp)
            continue
        if resp.status_code >= 400:
            raise ProviderError(f"{resp.status_code} from {url}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"Non-JSON response from {url}: {exc}") from exc
        if use_cache:
            _cache.set(cache_key, data)
        return data, url
    raise ProviderError(f"Network error after {_MAX_RETRIES} attempts for {url}: {last_exc}")


def rows_to_csv(rows: list[dict]) -> str:
    """Tidy long-format CSV. ``None`` values render empty (so na rows are blank cells)."""
    if not rows:
        return ""
    preferred = ["geo", "geo_label", "series", "series_label", "time", "value", "flag"]
    seen = list(dict.fromkeys([k for r in rows for k in r]))
    fieldnames = [k for k in preferred if k in seen] + [k for k in seen if k not in preferred]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})
    return buf.getvalue()


def _obs_float(x: str | None) -> float | None:
    if x in (None, ""):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_sdmx_csv(
    text: str,
    *,
    drop_cols: frozenset[str] = frozenset(),
    series_col: str | None = None,
    unit_col: str | None = None,
    title_col: str | None = None,
) -> tuple[list[dict], str | None]:
    """Parse plain SDMX-CSV (single code columns, no paired labels — ECB/ILOSTAT style).

    Columns before TIME_PERIOD are the series-key dimensions; TIME_PERIOD/OBS_VALUE/OBS_STATUS
    are the observation; later columns are attributes. geo is taken from REF_AREA when present;
    dimensions that vary become extra row columns. Returns ``(rows, unit)``. Missing
    observations → value=None, flag="na"; OBS_STATUS kept in flag (normal/empty/"A" → "").
    """
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    records = list(reader)
    if not records or "TIME_PERIOD" not in fields:
        return [], None
    ti = fields.index("TIME_PERIOD")
    # geo column name varies across SDMX providers
    geo_col = next((c for c in fields[:ti] if c in ("REF_AREA", "ref_area", "geo", "GEO", "LOCATION", "location")), None)
    drop = set(drop_cols) | {series_col, unit_col, title_col, geo_col}
    dim_cols = [c for c in fields[:ti] if c not in drop]
    varying = [c for c in dim_cols if len({r.get(c) for r in records}) > 1]
    units = {r.get(unit_col) for r in records} if (unit_col and unit_col in fields) else set()

    rows: list[dict] = []
    for r in records:
        val = _obs_float(r.get("OBS_VALUE"))
        status = (r.get("OBS_STATUS") or "").strip()
        flag = "na" if val is None else ("" if status in ("", "A") else status)
        row: dict[str, Any] = {}
        if geo_col:
            row["geo"] = r.get(geo_col)
            row["geo_label"] = ""
        if series_col and series_col in fields:
            row["series"] = r.get(series_col)
        if title_col and title_col in fields:
            row["series_label"] = r.get(title_col)
        row["time"] = r.get("TIME_PERIOD")
        row["value"] = val
        row["flag"] = flag
        for c in varying:
            row[c] = r.get(c)
        rows.append(row)

    rows.sort(key=lambda x: (str(x.get("geo") or ""), str(x.get("series") or ""), str(x.get("time") or "")))
    return rows, (next(iter(units)) if len(units) == 1 else None)


def build_result(
    *,
    provider: str,
    dataset: str,
    request_url: str,
    params: dict,
    unit: str | None,
    rows: list[dict],
    notes: str = "",
) -> dict:
    """Assemble the standard provenance + data contract returned by every tool."""
    return {
        "metadata": {
            "provider": provider,
            "dataset": dataset,
            "request_url": request_url,
            "params": params,
            "unit": unit,
            "extracted_utc": utc_now_iso(),
            "n_rows": len(rows),
            "notes": notes,
        },
        "data": rows,
        "csv": rows_to_csv(rows),
    }
