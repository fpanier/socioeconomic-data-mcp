"""FRED provider — Federal Reserve Economic Data (St. Louis Fed).

Requires a free API key in env ``MCP_FRED_API_KEY``. Endpoints (verified live):
- observations: https://api.stlouisfed.org/fred/series/observations?series_id=&api_key=&file_type=json
- series meta:  https://api.stlouisfed.org/fred/series?series_id=&...
- search:       https://api.stlouisfed.org/fred/series/search?search_text=&...

Each FRED series is a single time series. Missing observations (FRED sends ".") →
value=None, flag="na". The api_key is redacted from the request_url we return.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import os
import re
import threading
from typing import Any

from .. import common

_BASE = "https://api.stlouisfed.org/fred"

# Global per-day call ceiling protecting the shared API key on a public instance.
# Set via env MCP_FRED_DAILY_CAP (0/unset = no cap). Resets at 00:00 UTC.
_daily = {"day": "", "count": 0}
_daily_lock = threading.Lock()


def _under_daily_cap() -> bool:
    cap = int(os.environ.get("MCP_FRED_DAILY_CAP", "0") or 0)
    if cap <= 0:
        return True
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    with _daily_lock:
        if _daily["day"] != today:
            _daily["day"], _daily["count"] = today, 0
        if _daily["count"] >= cap:
            return False
        _daily["count"] += 1
        return True


def _key() -> str | None:
    return (os.environ.get("MCP_FRED_API_KEY") or "").strip() or None


def _no_key_result() -> dict:
    return {
        "metadata": {
            "provider": "fred",
            "error": "FRED API key not configured (set MCP_FRED_API_KEY).",
            "extracted_utc": common.utc_now_iso(),
        },
        "data": [],
        "csv": "",
    }


def _redact(url: str) -> str:
    return re.sub(r"(api_key=)[^&]+", r"\1REDACTED", url)


def _to_float(v: str | None) -> float | None:
    if v in (".", "", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(period: str | None, *, end: bool) -> str | None:
    """FRED needs full YYYY-MM-DD. Expand YYYY and YYYY-MM (month-end aware for `end`)."""
    if not period:
        return period
    p = str(period).strip()
    if re.fullmatch(r"\d{4}", p):
        return f"{p}-12-31" if end else f"{p}-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", p):
        y, m = int(p[:4]), int(p[5:7])
        return f"{p}-{calendar.monthrange(y, m)[1]:02d}" if end else f"{p}-01"
    return p


def _series_meta(sid: str, key: str) -> dict:
    try:
        doc, _ = common.fetch_json(f"{_BASE}/series", {"series_id": sid, "api_key": key, "file_type": "json"})
        s = (doc.get("seriess") or [{}])[0]
        return {"title": s.get("title", ""), "units": s.get("units"), "frequency": s.get("frequency")}
    except common.ProviderError:
        return {"title": "", "units": None}


def get(series_id: str | list[str], start: str | None = None, end: str | None = None) -> dict:
    key = _key()
    if not key:
        return _no_key_result()
    if not _under_daily_cap():
        return common.build_result(
            provider="fred",
            dataset=series_id if isinstance(series_id, str) else ",".join(series_id),
            request_url="",
            params={},
            unit=None,
            rows=[],
            notes="FRED daily limit reached on this shared public instance (resets 00:00 UTC). "
            "Self-host with your own MCP_FRED_API_KEY for unlimited use.",
        )

    ids = [series_id] if isinstance(series_id, str) else list(series_id)
    ids = [s.strip() for s in ids if s and s.strip()]
    multi = len(ids) > 1

    obs_start, obs_end = _norm(start, end=False), _norm(end, end=True)
    rows: list[dict] = []
    units: set[str] = set()
    titles: dict[str, str] = {}
    first_url = ""

    for sid in ids:
        meta = _series_meta(sid, key)
        titles[sid] = meta.get("title", "")
        if meta.get("units"):
            units.add(meta["units"])
        params: dict[str, Any] = {"series_id": sid, "api_key": key, "file_type": "json"}
        if obs_start:
            params["observation_start"] = obs_start
        if obs_end:
            params["observation_end"] = obs_end
        doc, final_url = common.fetch_json(f"{_BASE}/series/observations", params)
        if not first_url:
            first_url = final_url
        for o in doc.get("observations", []):
            val = _to_float(o.get("value"))
            row: dict[str, Any] = {"time": o.get("date"), "value": val, "flag": "na" if val is None else ""}
            if multi:
                row = {"series": sid, "series_label": titles[sid], **row}
            rows.append(row)

    notes = (
        "multiple FRED series; see series/series_label columns"
        if multi
        else (f"{ids[0]}: {titles.get(ids[0], '')}" if ids else "no series requested")
    )
    return common.build_result(
        provider="fred",
        dataset=",".join(ids) if multi else (ids[0] if ids else ""),
        request_url=_redact(first_url),
        params={k: v for k, v in (("series", ids), ("start", start), ("end", end)) if v},
        unit=next(iter(units)) if len(units) == 1 else None,
        rows=rows,
        notes=notes,
    )


def search(query: str, limit: int = 40) -> dict:
    key = _key()
    if not key:
        return _no_key_result()
    doc, final_url = common.fetch_json(
        f"{_BASE}/series/search",
        {"search_text": query, "api_key": key, "file_type": "json", "limit": limit},
    )
    results = [
        {"code": s.get("id"), "title": s.get("title"), "units": s.get("units"), "frequency": s.get("frequency")}
        for s in doc.get("seriess", [])
    ]
    return {
        "metadata": {
            "provider": "fred",
            "query": query,
            "request_url": _redact(final_url),
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(results),
            "notes": f"up to {limit} FRED series",
        },
        "results": results,
    }


def describe(series_id: str) -> dict:
    key = _key()
    if not key:
        return _no_key_result()
    doc, final_url = common.fetch_json(
        f"{_BASE}/series", {"series_id": series_id.strip(), "api_key": key, "file_type": "json"}
    )
    s = (doc.get("seriess") or [{}])[0]
    return {
        "metadata": {
            "provider": "fred",
            "dataset": series_id,
            "request_url": _redact(final_url),
            "extracted_utc": common.utc_now_iso(),
        },
        "series": {
            "id": s.get("id"),
            "title": s.get("title"),
            "units": s.get("units"),
            "frequency": s.get("frequency"),
            "seasonal_adjustment": s.get("seasonal_adjustment"),
            "observation_start": s.get("observation_start"),
            "observation_end": s.get("observation_end"),
            "notes": s.get("notes"),
        },
    }
