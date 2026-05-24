"""WHO Global Health Observatory provider — OData API (ghoapi.azureedge.net). No key.

- data:       https://ghoapi.azureedge.net/api/{IndicatorCode}?$filter=SpatialDim eq 'BEL'
- indicators: https://ghoapi.azureedge.net/api/Indicator

Rows: geo (SpatialDim, ISO3), time (TimeDim year), value (NumericValue), flag; plus Dim1/2/3
(e.g. SEX) when they vary. Missing NumericValue → value=None, flag="na".
"""

from __future__ import annotations

from typing import Any

from .. import common

_BASE = "https://ghoapi.azureedge.net/api"


def get(
    indicator: str,
    countries: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    indicator = indicator.strip()
    clauses: list[str] = []
    if countries:
        codes = [c.strip().upper() for c in countries if c.strip()]
        if codes:
            clauses.append("(" + " or ".join(f"SpatialDim eq '{c}'" for c in codes) + ")")
    if start and str(start).isdigit():
        clauses.append(f"TimeDim ge {int(start)}")
    if end and str(end).isdigit():
        clauses.append(f"TimeDim le {int(end)}")
    params: dict[str, Any] = {}
    if clauses:
        params["$filter"] = " and ".join(clauses)

    doc, final_url = common.fetch_json(f"{_BASE}/{indicator}", params)
    vals = doc.get("value", []) if isinstance(doc, dict) else []

    dims_present = [d for d in ("Dim1", "Dim2", "Dim3") if any(v.get(d) for v in vals)]
    order = {c.strip().upper(): i for i, c in enumerate(countries)} if countries else {}

    rows: list[dict] = []
    for v in vals:
        num = v.get("NumericValue")
        row: dict[str, Any] = {
            "geo": v.get("SpatialDim"),
            "geo_label": "",
            "time": str(v.get("TimeDim")) if v.get("TimeDim") is not None else None,
            "value": num,
            "flag": "" if num is not None else "na",
        }
        for d in dims_present:
            if v.get(d):
                row[d] = v.get(d)
        rows.append(row)

    rows.sort(key=lambda x: (order.get(x["geo"], len(order)), str(x["geo"] or ""), str(x["time"] or "")))

    meta_params: dict[str, Any] = {}
    if countries:
        meta_params["countries"] = countries
    if start:
        meta_params["start"] = start
    if end:
        meta_params["end"] = end

    return common.build_result(
        provider="who",
        dataset=indicator,
        request_url=final_url,
        params=meta_params,
        unit=None,
        rows=rows,
        notes="WHO GHO (NumericValue; SpatialDim=ISO3; some values are modelled/estimated)",
    )


def search(query: str, limit: int = 40) -> dict:
    doc, final_url = common.fetch_json(f"{_BASE}/Indicator")
    vals = doc.get("value", []) if isinstance(doc, dict) else []
    matches: list[dict] = []
    for x in vals:
        code, name = x.get("IndicatorCode") or "", x.get("IndicatorName") or ""
        if common.matches_query(query, code, name):
            matches.append({"code": code, "title": name})
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "who",
            "query": query,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": f"up to {limit}; use 'code' as the indicator with who_get",
        },
        "results": matches,
    }


def describe(indicator: str) -> dict:
    doc, final_url = common.fetch_json(f"{_BASE}/Indicator", {"$filter": f"IndicatorCode eq '{indicator.strip()}'"})
    vals = doc.get("value", []) if isinstance(doc, dict) else []
    info = vals[0] if vals else {}
    return {
        "metadata": {
            "provider": "who",
            "dataset": indicator,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
        },
        "indicator": {"code": info.get("IndicatorCode"), "name": info.get("IndicatorName")},
    }
