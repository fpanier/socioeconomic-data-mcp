"""ARDECO provider — JRC Annual Regional Database of the European Commission. No API key.

Long EU regional (NUTS) series — GDP, GVA, employment, population — back to 1960.

- variables:  GraphQL https://territorial.ec.europa.eu/ardeco-api-v2/graphql
              query{variableList{code description}}
              query{variable(id:"X"){nutsVersionList datasets{dimensions{key value}}}}
- data:       https://territorial.ec.europa.eu/ardeco-api-v2/rest/export/{variable}?unit=&version=
              → CSV: VERSIONS,LEVEL_ID,TERRITORY_ID,NAME_HTML,YEAR,DATE,UNIT,VALUE (all regions/years)

The export's server-side level/year/nutscode filters are unreliable, so we fetch the whole
variable (cached) and filter client-side. Missing → value=None, flag="na".
"""

from __future__ import annotations

import csv
import io
from typing import Any

from .. import common

_GQL = "https://territorial.ec.europa.eu/ardeco-api-v2/graphql"
_EXPORT = "https://territorial.ec.europa.eu/ardeco-api-v2/rest/export"


def _variables() -> list[dict]:
    doc, _ = common.post_json(_GQL, {"query": "query{variableList{code description}}"})
    return (doc.get("data") or {}).get("variableList", []) if isinstance(doc, dict) else []


def _variable_info(variable: str) -> dict:
    q = 'query{variable(id:"%s"){nutsVersionList datasets{dimensions{key value}}}}' % variable
    doc, _ = common.post_json(_GQL, {"query": q})
    return ((doc.get("data") or {}).get("variable") or {}) if isinstance(doc, dict) else {}


def _units(info: dict) -> list[str]:
    out: list[str] = []
    for d in info.get("datasets") or []:
        for dim in d.get("dimensions") or []:
            if dim.get("key") == "unit" and dim.get("value"):
                out.append(dim["value"])
    return out


def get(
    variable: str,
    unit: str | None = None,
    version: str | int | None = None,
    nuts_level: str | int | None = None,
    regions: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    variable = variable.strip()
    info = _variable_info(variable)
    versions = info.get("nutsVersionList") or []
    units = _units(info)
    if unit is None:
        unit = units[0] if units else None
    if version is None:
        version = max(versions) if versions else None
    if not unit or not version:
        return {
            "metadata": {
                "provider": "ardeco",
                "dataset": variable,
                "error": "could not resolve unit/version; call describe_dataset for valid units and NUTS versions",
                "extracted_utc": common.utc_now_iso(),
            },
            "data": [],
            "csv": "",
        }

    text, final_url = common.fetch_text(f"{_EXPORT}/{variable}", {"unit": unit, "version": version})
    want = {r.strip().upper() for r in regions} if regions else None
    rows: list[dict] = []
    for r in csv.DictReader(io.StringIO(text)):
        terr = (r.get("TERRITORY_ID") or "").strip()
        if want and not any(terr.upper() == w or terr.upper().startswith(w) for w in want):
            continue
        if nuts_level is not None and str(r.get("LEVEL_ID")) != str(nuts_level):
            continue
        yr = (r.get("YEAR") or "").strip()
        if start and yr.isdigit() and int(yr) < int(start):
            continue
        if end and yr.isdigit() and int(yr) > int(end):
            continue
        val = common._obs_float(r.get("VALUE"))
        rows.append({
            "geo": terr,
            "geo_label": r.get("NAME_HTML") or "",
            "time": yr,
            "value": val,
            "flag": "" if val is not None else "na",
        })

    rows.sort(key=lambda x: (str(x["geo"]), str(x["time"])))
    return common.build_result(
        provider="ardeco",
        dataset=variable,
        request_url=final_url,
        params={k: v for k, v in (("unit", unit), ("version", version), ("nuts_level", nuts_level),
                                   ("regions", regions), ("start", start), ("end", end)) if v is not None},
        unit=unit,
        rows=rows,
        notes=f"ARDECO/JRC (NUTS {version}); filtered client-side. Long regional series.",
    )


def search(query: str, limit: int = 40) -> dict:
    variables = _variables()
    matches = [
        {"code": v.get("code"), "title": v.get("description")}
        for v in variables
        if common.matches_query(query, v.get("code") or "", v.get("description") or "")
    ][:limit]
    return {
        "metadata": {
            "provider": "ardeco",
            "query": query,
            "request_url": _GQL,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": "use 'code' as the variable with ardeco_get",
        },
        "results": matches,
    }


def describe(variable: str) -> dict:
    info = _variable_info(variable.strip())
    return {
        "metadata": {
            "provider": "ardeco",
            "dataset": variable,
            "request_url": _GQL,
            "extracted_utc": common.utc_now_iso(),
        },
        "variable": {
            "code": variable,
            "nuts_versions": info.get("nutsVersionList"),
            "units": sorted(set(_units(info))),
        },
    }
