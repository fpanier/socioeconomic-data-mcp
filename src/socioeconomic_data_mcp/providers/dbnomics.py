"""DBnomics provider — one API federating ~50 official providers. No API key.

- series:    https://api.db.nomics.world/v22/series/{provider}/{dataset}?dimensions={json}&observations=1
             or .../series?series_ids=PROVIDER/DATASET/SERIES,...&observations=1
- search:    https://api.db.nomics.world/v22/search?q=...   (returns datasets)

Each returned series has parallel period[]/value[] arrays plus a dimensions dict. We emit
long rows; geo is taken from a geo-like dimension when present. Missing → value=null, flag="na".
DBnomics is itself an aggregator — the metadata names the underlying provider.
"""

from __future__ import annotations

import json as _json
from typing import Any

from .. import common

_BASE = "https://api.db.nomics.world/v22"
_GEO_KEYS = ("geo", "GEO", "ref_area", "REF_AREA", "country", "COUNTRY", "REF_AREA_DETAIL")


def get(
    provider: str | None = None,
    dataset: str | None = None,
    dimensions: dict | None = None,
    series_ids: list[str] | str | None = None,
    max_series: int = 50,
) -> dict:
    params: dict[str, Any] = {"observations": "1", "limit": max(1, min(int(max_series), 200))}
    if series_ids:
        ids = series_ids if isinstance(series_ids, list) else [series_ids]
        params["series_ids"] = ",".join(ids)
        url = f"{_BASE}/series"
        ds_label = ",".join(ids)
        prov = None
    elif provider and dataset:
        if dimensions:
            params["dimensions"] = _json.dumps(dimensions)
        url = f"{_BASE}/series/{provider.strip()}/{dataset.strip()}"
        ds_label = f"{provider}/{dataset}"
        prov = provider
    else:
        return {
            "metadata": {
                "provider": "dbnomics",
                "error": "provide either series_ids, or provider + dataset",
                "extracted_utc": common.utc_now_iso(),
            },
            "data": [],
            "csv": "",
        }

    doc, final_url = common.fetch_json(url, params)
    docs = (doc.get("series") or {}).get("docs", []) if isinstance(doc, dict) else []
    multi = len(docs) > 1
    rows: list[dict] = []
    units: set[str] = set()
    for s in docs:
        dims = s.get("dimensions") or {}
        sid = s.get("series_code") or s.get("series_id")
        u = dims.get("unit") or dims.get("UNIT")
        if u:
            units.add(u)
        geo = next((dims[k] for k in _GEO_KEYS if k in dims), None)
        for period, value in zip(s.get("period") or [], s.get("value") or []):
            val = value if isinstance(value, (int, float)) else None
            row: dict[str, Any] = {}
            if geo is not None:
                row["geo"] = geo
                row["geo_label"] = ""
            if multi:
                row["series"] = sid
            row["time"] = period
            row["value"] = val
            row["flag"] = "" if val is not None else "na"
            rows.append(row)

    return common.build_result(
        provider="dbnomics",
        dataset=ds_label,
        request_url=final_url,
        params={k: v for k, v in (("provider", provider), ("dataset", dataset),
                                   ("dimensions", dimensions), ("series_ids", series_ids)) if v},
        unit=next(iter(units)) if len(units) == 1 else None,
        rows=rows,
        notes=f"DBnomics (federated; {len(docs)} series). Underlying source: {prov or 'see series IDs'}",
    )


def search(query: str, limit: int = 40) -> dict:
    doc, final_url = common.fetch_json(f"{_BASE}/search", {"q": query, "limit": limit})
    docs = (doc.get("results") or {}).get("docs", []) if isinstance(doc, dict) else []
    matches: list[dict] = []
    for x in docs[:limit]:
        prov, ds = x.get("provider_code"), x.get("code")
        if prov and ds:
            matches.append({"code": f"{prov}/{ds}", "title": x.get("name"), "provider": prov})
    return {
        "metadata": {
            "provider": "dbnomics",
            "query": query,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": "use 'code' (provider/dataset) with dbnomics_get; add dimensions={...} to filter series",
        },
        "results": matches,
    }
