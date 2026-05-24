"""World Bank Open Data provider — REST v2 (no API key).

Endpoints (verified live, see ENDPOINTS.md):
- data:     https://api.worldbank.org/v2/country/{economies}/indicator/{indicator}?format=json
- indicator list / metadata: https://api.worldbank.org/v2/indicator[/{id}]?format=json

Same no-invention rules as Eurostat: a published-but-empty observation comes back as
value=None, flag="na". World Bank observation status (when present) is kept in flag.
"""

from __future__ import annotations

from typing import Any

from .. import common

_BASE = "https://api.worldbank.org/v2"
_MAX_PAGES = 20  # safety cap (per_page=1000 → up to 20k rows)


def _fetch_pages(url: str, params: dict) -> tuple[list[dict], dict, str]:
    """Follow World Bank pagination. Returns (rows, first_meta, first_url)."""
    rows: list[dict] = []
    first_meta: dict = {}
    first_url = ""
    page = 1
    while page <= _MAX_PAGES:
        doc, final_url = common.fetch_json(url, {**params, "page": page})
        if not isinstance(doc, list) or len(doc) < 2:
            # error payload, e.g. [{"message":[...]}] or [meta] with no data
            meta = doc[0] if isinstance(doc, list) and doc else {}
            return rows, (meta if isinstance(meta, dict) else {}), (first_url or final_url)
        meta, batch = doc[0], doc[1] or []
        if page == 1:
            first_meta, first_url = meta, final_url
        rows.extend(batch)
        if page >= int(meta.get("pages", 1) or 1):
            break
        page += 1
    return rows, first_meta, first_url


def get(
    indicator: str,
    economies: list[str] | str | None = None,
    time: str | None = None,
    latest_only: bool = False,
) -> dict:
    indicator = indicator.strip()
    econ = economies or ["all"]
    if isinstance(econ, str):
        econ = [econ]
    econ = [e.strip() for e in econ if e.strip()]
    econ_path = ";".join(econ) if econ else "all"

    params: dict[str, Any] = {"format": "json", "per_page": 1000}
    if time:
        params["date"] = time
    if latest_only and not time:
        params["mrv"] = 1

    url = f"{_BASE}/country/{econ_path}/indicator/{indicator}"
    raw, meta0, final_url = _fetch_pages(url, params)

    notes: list[str] = []
    message = meta0.get("message") if isinstance(meta0, dict) else None
    if message:
        try:
            notes.append("; ".join(m.get("value", "") for m in message))
        except Exception:  # noqa: BLE001
            notes.append("World Bank returned a message instead of data")

    order = {e: i for i, e in enumerate(econ)}
    rows: list[dict] = []
    unit_seen: set[str] = set()
    for r in raw:
        geo = r.get("countryiso3code") or (r.get("country") or {}).get("id")
        val = r.get("value")
        flag = (r.get("obs_status") or "").strip()
        if val is None:
            flag = "na"
        if r.get("unit"):
            unit_seen.add(r["unit"])
        rows.append({
            "geo": geo,
            "geo_label": (r.get("country") or {}).get("value", ""),
            "time": r.get("date"),
            "value": val,
            "flag": flag,
        })

    rows.sort(key=lambda x: (order.get(x["geo"], len(order)), str(x["geo"] or ""), str(x["time"] or "")))
    if latest_only:
        notes.append("latest_only=true; most recent value per economy (mrv=1)")

    meta_params: dict[str, Any] = {"economies": econ}
    if time:
        meta_params["time"] = time

    return common.build_result(
        provider="worldbank",
        dataset=indicator,
        request_url=final_url,
        params=meta_params,
        unit=next(iter(unit_seen)) if len(unit_seen) == 1 else None,
        rows=rows,
        notes="; ".join(n for n in notes if n),
    )


def describe(indicator: str) -> dict:
    """Indicator metadata: name, source, definition note, topics."""
    indicator = indicator.strip()
    doc, final_url = common.fetch_json(f"{_BASE}/indicator/{indicator}", {"format": "json"})
    info = (doc[1][0] if isinstance(doc, list) and len(doc) > 1 and doc[1] else {})
    return {
        "metadata": {
            "provider": "worldbank",
            "dataset": indicator,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
        },
        "indicator": {
            "id": info.get("id"),
            "name": info.get("name"),
            "source": (info.get("source") or {}).get("value"),
            "source_note": info.get("sourceNote"),
            "source_organization": info.get("sourceOrganization"),
            "topics": [t.get("value") for t in info.get("topics", []) if isinstance(t, dict)],
        },
    }


def search(query: str, limit: int = 40) -> dict:
    """Search the World Bank indicator catalogue by id or name."""
    doc, final_url = common.fetch_json(f"{_BASE}/indicator", {"format": "json", "per_page": 25000})
    items = doc[1] if isinstance(doc, list) and len(doc) > 1 and doc[1] else []
    matches: list[dict] = []
    for it in items:
        code = (it.get("id") or "")
        name = (it.get("name") or "")
        if common.matches_query(query, code, name):
            matches.append({
                "code": code,
                "title": name,
                "source": (it.get("source") or {}).get("value"),
            })
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "worldbank",
            "query": query,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": f"showing up to {limit} indicator matches",
        },
        "results": matches,
    }
