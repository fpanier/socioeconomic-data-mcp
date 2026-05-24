"""ILOSTAT provider — ILO SDMX REST (sdmx.ilo.org/rest), SDMX-CSV. No API key.

- data:      https://sdmx.ilo.org/rest/data/{dataflow}/{key}   (Accept: SDMX-CSV)
- dataflows: https://sdmx.ilo.org/rest/dataflow/ILO            (SDMX-JSON structure)

Plain SDMX-CSV parsed by common.parse_sdmx_csv. geo = REF_AREA (ISO3); dimensions like
SEX/AGE/MEASURE become columns when they vary. Missing → value=None, flag="na".
"""

from __future__ import annotations

import json

from .. import common

_BASE = "https://sdmx.ilo.org/rest"
_CSV = "application/vnd.sdmx.data+csv"
_STRUCT_JSON = "application/vnd.sdmx.structure+json"


def _name(f: dict) -> str:
    n = f.get("name")
    if isinstance(n, str):
        return n
    names = f.get("names")
    return (names.get("en") if isinstance(names, dict) else "") or ""


def get(dataflow: str, key: str = "", start: str | None = None, end: str | None = None) -> dict:
    dataflow = dataflow.strip()
    key = (key or "").strip()
    params: dict = {}
    if start:
        params["startPeriod"] = start
    if end:
        params["endPeriod"] = end
    text, final_url = common.fetch_text(f"{_BASE}/data/{dataflow}/{key}", params, headers={"Accept": _CSV})
    rows, unit = common.parse_sdmx_csv(text, drop_cols=frozenset({"DATAFLOW"}), unit_col="UNIT_MEASURE")
    return common.build_result(
        provider="ilostat",
        dataset=dataflow,
        request_url=final_url,
        params={"key": key or "all", **({"start": start} if start else {}), **({"end": end} if end else {})},
        unit=unit,
        rows=rows,
        notes="ILOSTAT SDMX (some series are ILO modelled estimates)",
    )


def _flows() -> tuple[list[dict], str]:
    text, url = common.fetch_text(f"{_BASE}/dataflow/ILO", None, headers={"Accept": _STRUCT_JSON})
    try:
        return json.loads(text).get("data", {}).get("dataflows", []), url
    except ValueError:
        return [], url


def search(query: str, limit: int = 40) -> dict:
    flows, url = _flows()
    matches: list[dict] = []
    for f in flows:
        fid, name = f.get("id") or "", _name(f)
        if common.matches_query(query, fid, name):
            matches.append({"code": fid, "title": name})
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "ilostat",
            "query": query,
            "request_url": url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": f"up to {limit}; use 'code' as the dataflow with ilostat_get (key is dot-separated)",
        },
        "results": matches,
    }


def describe(dataflow: str) -> dict:
    flows, url = _flows()
    want = dataflow.strip().lower()
    hit = next((f for f in flows if (f.get("id") or "").lower() == want), None)
    return {
        "metadata": {
            "provider": "ilostat",
            "dataset": dataflow,
            "request_url": url,
            "extracted_utc": common.utc_now_iso(),
            "notes": "ILOSTAT keys are dot-separated dimension filters; empty key = all",
        },
        "dataflow": {"id": hit.get("id"), "name": _name(hit)} if hit else {},
    }
