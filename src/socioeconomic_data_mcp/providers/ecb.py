"""ECB Data Portal provider — SDMX REST, SDMX-CSV (csvdata). No API key.

- data:      https://data-api.ecb.europa.eu/service/data/{flow}/{key}?format=csvdata
- dataflows: https://data-api.ecb.europa.eu/service/dataflow/ECB (SDMX-ML structure; XML only)

Good for euro-area HICP inflation (flow ICP), interest/exchange rates (EXR), finance.
Plain SDMX-CSV is parsed by common.parse_sdmx_csv; missing → value=None, flag="na".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .. import common

_DATA = "https://data-api.ecb.europa.eu/service/data"
_DATAFLOW = "https://data-api.ecb.europa.eu/service/dataflow/ECB"
_XML = "application/vnd.sdmx.structure+xml;version=2.1"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def get(flow: str, key: str = "", start: str | None = None, end: str | None = None) -> dict:
    flow = flow.strip()
    key = (key or "").strip()
    params: dict[str, Any] = {"format": "csvdata"}
    if start:
        params["startPeriod"] = start
    if end:
        params["endPeriod"] = end
    path = f"{_DATA}/{flow}/{key}" if key else f"{_DATA}/{flow}"
    text, final_url = common.fetch_text(path, params)
    rows, unit = common.parse_sdmx_csv(
        text, drop_cols=frozenset({"KEY"}), series_col="KEY", unit_col="UNIT", title_col="TITLE"
    )
    return common.build_result(
        provider="ecb",
        dataset=flow,
        request_url=final_url,
        params={"key": key or "all", **({"start": start} if start else {}), **({"end": end} if end else {})},
        unit=unit,
        rows=rows,
        notes="ECB Data Portal SDMX",
    )


def _flows() -> tuple[list[dict], str]:
    """Parse the ECB dataflow list (SDMX-ML 2.1) into [{id, name}]."""
    text, url = common.fetch_text(_DATAFLOW, None, headers={"Accept": _XML})
    flows: list[dict] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return flows, url
    for el in root.iter():
        if _local(el.tag) != "Dataflow":
            continue
        fid = el.get("id")
        if not fid:
            continue
        name = ""
        for ch in el:
            if _local(ch.tag) == "Name" and (name == "" or ch.get(_XML_LANG) == "en"):
                name = ch.text or name
        flows.append({"id": fid, "name": name})
    return flows, url


def search(query: str, limit: int = 40) -> dict:
    flows, url = _flows()
    matches: list[dict] = []
    for f in flows:
        if common.matches_query(query, f["id"], f.get("name") or ""):
            matches.append({"code": f["id"], "title": f.get("name")})
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "ecb",
            "query": query,
            "request_url": url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": f"up to {limit}; use 'code' as the flow with ecb_get (key is a dot-separated filter)",
        },
        "results": matches,
    }


def describe(flow: str) -> dict:
    flows, url = _flows()
    want = flow.strip().lower()
    hit = next((f for f in flows if f["id"].lower() == want), None)
    return {
        "metadata": {
            "provider": "ecb",
            "dataset": flow,
            "request_url": url,
            "extracted_utc": common.utc_now_iso(),
            "notes": "ECB keys are dot-separated dimension filters; empty key = all",
        },
        "dataflow": {"id": hit["id"], "name": hit.get("name")} if hit else {},
    }
