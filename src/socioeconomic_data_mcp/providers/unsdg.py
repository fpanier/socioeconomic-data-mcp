"""UN SDG provider — UN Statistics SDG API (unstats.un.org/sdgapi). No API key.

- data:   https://unstats.un.org/sdgapi/v1/sdg/Series/Data?seriesCode=&areaCode=&...
- series: https://unstats.un.org/sdgapi/v1/sdg/Series/List

Areas are UN **M49 numeric** codes (Belgium=56, Germany=276, World=1); omit for all.
Rows: geo (M49), geo_label (area name), time (year), value, flag. Missing/non-numeric → na.
"""

from __future__ import annotations

from typing import Any

from .. import common

_BASE = "https://unstats.un.org/sdgapi/v1/sdg"
_MAX_PAGES = 10


def _val(x: Any) -> float | None:
    if x in (None, "", "NaN"):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def get(
    series: str,
    areas: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    series = series.strip()
    base_params: dict[str, Any] = {"seriesCode": series, "pageSize": 2000}
    if areas:
        base_params["areaCode"] = [str(a).strip() for a in areas if str(a).strip()]
    if start:
        base_params["timePeriodStart"] = start
    if end:
        base_params["timePeriodEnd"] = end

    rows: list[dict] = []
    first_url = ""
    page = 1
    while page <= _MAX_PAGES:
        doc, url = common.fetch_json(f"{_BASE}/Series/Data", {**base_params, "page": page})
        if not first_url:
            first_url = url
        data = doc.get("data") or [] if isinstance(doc, dict) else []
        for r in data:
            tp = r.get("timePeriodStart")
            time = str(int(tp)) if isinstance(tp, (int, float)) else (str(tp) if tp else None)
            val = _val(r.get("value"))
            rows.append({
                "geo": str(r.get("geoAreaCode")),
                "geo_label": r.get("geoAreaName") or "",
                "time": time,
                "value": val,
                "flag": "" if val is not None else "na",
            })
        if not isinstance(doc, dict) or page >= int(doc.get("totalPages", 1) or 1):
            break
        page += 1

    rows.sort(key=lambda x: (str(x.get("geo_label") or ""), str(x.get("time") or "")))
    meta_params: dict[str, Any] = {"series": series}
    if areas:
        meta_params["areas_m49"] = areas
    if start:
        meta_params["start"] = start
    if end:
        meta_params["end"] = end

    return common.build_result(
        provider="unsdg",
        dataset=series,
        request_url=first_url,
        params=meta_params,
        unit=None,
        rows=rows,
        notes="UN SDG; geo=M49 code; some values are estimated/modelled (see source)",
    )


def search(query: str, limit: int = 40) -> dict:
    doc, url = common.fetch_json(f"{_BASE}/Series/List")
    items = doc if isinstance(doc, list) else []
    matches: list[dict] = []
    for it in items:
        code, desc = it.get("code") or "", it.get("description") or ""
        if common.matches_query(query, code, desc):
            matches.append({"code": code, "title": desc, "goal": it.get("goal")})
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "unsdg",
            "query": query,
            "request_url": url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": f"up to {limit}; use 'code' as the series with unsdg_get (areas are M49 codes)",
        },
        "results": matches,
    }


def describe(series: str) -> dict:
    doc, url = common.fetch_json(f"{_BASE}/Series/List")
    items = doc if isinstance(doc, list) else []
    want = series.strip().lower()
    hit = next((it for it in items if (it.get("code") or "").lower() == want), None)
    return {
        "metadata": {
            "provider": "unsdg",
            "dataset": series,
            "request_url": url,
            "extracted_utc": common.utc_now_iso(),
            "notes": "areas are UN M49 numeric codes",
        },
        "series": {
            "code": hit.get("code"),
            "description": hit.get("description"),
            "goal": hit.get("goal"),
            "release": hit.get("release"),
        } if hit else {},
    }
