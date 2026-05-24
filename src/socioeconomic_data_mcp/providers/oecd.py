"""OECD provider — modern SDMX REST API, SDMX-CSV output (no API key).

Endpoints (verified live, see ENDPOINTS.md):
- data:     https://sdmx.oecd.org/public/rest/data/{agency,dataflow,version}/{key}?format=csvfilewithlabels
- dataflows: https://sdmx.oecd.org/public/rest/dataflow/all  (Accept: SDMX-JSON structure)

The dataflow ref is "{agency},{id},{version}" (from search_datasets). The key is a
dot-separated dimension filter; an empty segment means "all" for that dimension.
Missing observations → value=None, flag="na". OBS_STATUS is kept as flag (normal "A"
is treated as no flag, matching the Eurostat convention).
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from .. import common

_DATA = "https://sdmx.oecd.org/public/rest/data"
_DATAFLOWS = "https://sdmx.oecd.org/public/rest/dataflow/all"
_STRUCT_JSON = "application/vnd.sdmx.structure+json"

_CORE = {"REF_AREA", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"}
_DROP = {"STRUCTURE", "STRUCTURE_ID", "STRUCTURE_NAME", "ACTION", "UNIT_MULT", "DECIMALS", "BASE_PER"}
_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]*$")


def _to_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_csv(text: str) -> tuple[list[dict], str | None]:
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    records = list(reader)
    if not records:
        return [], None

    code_cols = [c for c in fields if _CODE_RE.fullmatch(c or "")]
    # the human label column for a code column is the immediately following non-code column
    label_of: dict[str, str] = {}
    for i, c in enumerate(fields):
        if c in code_cols and i + 1 < len(fields) and not _CODE_RE.fullmatch(fields[i + 1] or ""):
            label_of[c] = fields[i + 1]
    geo_label_col = label_of.get("REF_AREA")

    dim_cols = [c for c in code_cols if c not in _CORE and c not in _DROP]
    varying = [c for c in dim_cols if len({r.get(c) for r in records}) > 1]
    units = {r.get("UNIT_MEASURE") for r in records} if "UNIT_MEASURE" in fields else set()

    rows: list[dict] = []
    for r in records:
        val = _to_float(r.get("OBS_VALUE"))
        status = (r.get("OBS_STATUS") or "").strip()
        flag = "na" if val is None else ("" if status in ("", "A") else status)
        row: dict[str, Any] = {
            "geo": r.get("REF_AREA"),
            "geo_label": r.get(geo_label_col, "") if geo_label_col else "",
            "time": r.get("TIME_PERIOD"),
            "value": val,
            "flag": flag,
        }
        for c in varying:
            row[c] = r.get(c)
        rows.append(row)

    rows.sort(key=lambda x: (str(x.get("geo") or ""), str(x.get("time") or "")))
    unit = next(iter(units)) if len(units) == 1 else None
    return rows, unit


def get(dataflow: str, key: str = "", start: str | None = None, end: str | None = None) -> dict:
    dataflow = dataflow.strip()
    key = (key or "").strip()
    params: dict[str, Any] = {"format": "csvfilewithlabels"}
    if start:
        params["startPeriod"] = start
    if end:
        params["endPeriod"] = end
    path = f"{_DATA}/{dataflow}/{key if key else 'all'}"
    text, final_url = common.fetch_text(path, params)
    rows, unit = _parse_csv(text)
    return common.build_result(
        provider="oecd",
        dataset=dataflow,
        request_url=final_url,
        params={"key": key or "all", **({"start": start} if start else {}), **({"end": end} if end else {})},
        unit=unit,
        rows=rows,
        notes="OECD SDMX; OBS_STATUS kept in flag (normal value = empty)",
    )


def search(query: str, limit: int = 40) -> dict:
    text, final_url = common.fetch_text(_DATAFLOWS, None, headers={"Accept": _STRUCT_JSON})
    import json

    data = json.loads(text)
    flows = data.get("data", {}).get("dataflows", [])
    matches: list[dict] = []
    for f in flows:
        name = f.get("name") or ""
        fid = f.get("id") or ""
        if common.matches_query(query, fid, name):
            ref = f"{f.get('agencyID')},{fid},{f.get('version')}"
            matches.append({"code": ref, "title": name, "agency": f.get("agencyID")})
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "oecd",
            "query": query,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": f"showing up to {limit}; use the 'code' (agency,dataflow,version) with oecd_get",
        },
        "results": matches,
    }


def describe(dataflow: str) -> dict:
    """Look up a dataflow's name/description from the catalogue. OECD dimension keys
    are dot-separated; call oecd_get with an empty key to return all dimensions."""
    text, final_url = common.fetch_text(_DATAFLOWS, None, headers={"Accept": _STRUCT_JSON})
    import json

    flows = json.loads(text).get("data", {}).get("dataflows", [])
    want = dataflow.strip().lower()
    hit = None
    for f in flows:
        ref = f"{f.get('agencyID')},{f.get('id')},{f.get('version')}".lower()
        if want in (f.get("id", "").lower(), ref) or want == f.get("id", "").lower():
            hit = f
            break
    return {
        "metadata": {
            "provider": "oecd",
            "dataset": dataflow,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "notes": "OECD keys are dot-separated dimension filters; empty key = all dimensions",
        },
        "dataflow": {
            "id": hit.get("id") if hit else None,
            "ref": f"{hit.get('agencyID')},{hit.get('id')},{hit.get('version')}" if hit else None,
            "name": hit.get("name") if hit else None,
            "description": hit.get("description") if hit else None,
        } if hit else {},
    }
