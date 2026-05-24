"""IMF provider — DataMapper API v1 (no API key).

Endpoints (verified live, see ENDPOINTS.md):
- data:       https://www.imf.org/external/datamapper/api/v1/{indicator}
              → {"values": {indicator: {ISO3: {year: value}}}} (all countries; filter client-side)
- indicators: https://www.imf.org/external/datamapper/api/v1/indicators
- countries:  https://www.imf.org/external/datamapper/api/v1/countries

Note: DataMapper series are IMF's published figures and may include IMF
estimates/projections for recent or future years (e.g. WEO). They are surfaced as-is
(official IMF data) and this is flagged in the result notes — we never add our own.
"""

from __future__ import annotations

from typing import Any

from .. import common

_BASE = "https://www.imf.org/external/datamapper/api/v1"
_country_cache: dict[str, str] | None = None


def _country_labels() -> dict[str, str]:
    global _country_cache
    if _country_cache is None:
        try:
            doc, _ = common.fetch_json(f"{_BASE}/countries")
            countries = doc.get("countries", {}) if isinstance(doc, dict) else {}
            _country_cache = {
                k: (v.get("label", "") if isinstance(v, dict) else str(v)) for k, v in countries.items()
            }
        except common.ProviderError:
            _country_cache = {}
    return _country_cache


def get(
    indicator: str,
    countries: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    indicator = indicator.strip()
    doc, final_url = common.fetch_json(f"{_BASE}/{indicator}")
    series_all = (doc.get("values") or {}).get(indicator, {}) if isinstance(doc, dict) else {}
    labels = _country_labels()

    wanted = [c.strip().upper() for c in countries] if countries else None
    order = {c: i for i, c in enumerate(wanted)} if wanted else {}

    rows: list[dict] = []
    for ccode, series in series_all.items():
        if wanted and ccode not in wanted:
            continue
        if not isinstance(series, dict):
            continue
        for yr in sorted(series.keys()):
            if start and yr < str(start):
                continue
            if end and yr > str(end):
                continue
            val = series.get(yr)
            rows.append({
                "geo": ccode,
                "geo_label": labels.get(ccode, ""),
                "time": yr,
                "value": val,
                "flag": "" if val is not None else "na",
            })

    rows.sort(key=lambda x: (order.get(x["geo"], len(order)), str(x["geo"]), str(x["time"])))

    meta_params: dict[str, Any] = {}
    if countries:
        meta_params["countries"] = countries
    if start:
        meta_params["start"] = start
    if end:
        meta_params["end"] = end

    return common.build_result(
        provider="imf",
        dataset=indicator,
        request_url=final_url,
        params=meta_params,
        unit=None,
        rows=rows,
        notes="IMF DataMapper; series may include IMF estimates/projections for recent or future years",
    )


def search(query: str, limit: int = 40) -> dict:
    doc, final_url = common.fetch_json(f"{_BASE}/indicators")
    inds = doc.get("indicators", {}) if isinstance(doc, dict) else {}
    matches: list[dict] = []
    for code, meta in inds.items():
        label = (meta.get("label", "") if isinstance(meta, dict) else "") or ""
        if common.matches_query(query, code, label):
            matches.append({
                "code": code,
                "title": label,
                "unit": meta.get("unit") if isinstance(meta, dict) else None,
                "source": meta.get("source") if isinstance(meta, dict) else None,
            })
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "imf",
            "query": query,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": f"up to {limit} IMF DataMapper indicators",
        },
        "results": matches,
    }


def describe(indicator: str) -> dict:
    doc, final_url = common.fetch_json(f"{_BASE}/indicators")
    meta = (doc.get("indicators", {}) if isinstance(doc, dict) else {}).get(indicator.strip(), {})
    info = meta if isinstance(meta, dict) else {}
    return {
        "metadata": {
            "provider": "imf",
            "dataset": indicator,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "notes": "IMF DataMapper indicator",
        },
        "indicator": {
            "code": indicator,
            "label": info.get("label"),
            "description": info.get("description"),
            "unit": info.get("unit"),
            "source": info.get("source"),
            "dataset": info.get("dataset"),
        },
    }
