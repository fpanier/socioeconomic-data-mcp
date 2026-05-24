"""Generic SDMX 2.1 REST connector — point at any SDMX-CSV endpoint. Usually no key.

Best-effort for standard SDMX 2.1 REST services (national statistical offices, central
banks). Requests `{base}/data/{flow}/{key}` as SDMX-CSV and parses with common.parse_sdmx_csv.
The key's dimension order is provider-specific (the caller supplies it; empty = all).

Examples of base URLs:
  https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1   (Eurostat)
  https://data-api.ecb.europa.eu/service                      (ECB)
  https://sdmx.ilo.org/rest                                   (ILOSTAT)
"""

from __future__ import annotations

from .. import common

_CSV = "application/vnd.sdmx.data+csv"


def get(base_url: str, flow: str, key: str = "", start: str | None = None, end: str | None = None) -> dict:
    base = base_url.strip().rstrip("/")
    flow = flow.strip()
    key = (key or "").strip()
    params: dict = {}
    if start:
        params["startPeriod"] = start
    if end:
        params["endPeriod"] = end
    path = f"{base}/data/{flow}/{key}" if key else f"{base}/data/{flow}"
    text, final_url = common.fetch_text(path, params, headers={"Accept": _CSV})
    rows, unit = common.parse_sdmx_csv(text, drop_cols=frozenset({"DATAFLOW", "KEY"}), unit_col="UNIT_MEASURE")
    if not rows:
        # some servers (e.g. Eurostat SDMX 2.1) want an explicit format param, not the Accept header
        text, final_url = common.fetch_text(path, {**params, "format": "SDMX-CSV"}, headers={"Accept": _CSV})
        rows, unit = common.parse_sdmx_csv(text, drop_cols=frozenset({"DATAFLOW", "KEY"}), unit_col="UNIT_MEASURE")
    return common.build_result(
        provider="sdmx",
        dataset=f"{flow} @ {base}",
        request_url=final_url,
        params={"base_url": base, "flow": flow, "key": key or "all",
                **({"start": start} if start else {}), **({"end": end} if end else {})},
        unit=unit,
        rows=rows,
        notes="Generic SDMX 2.1 (SDMX-CSV). Key dimension order is provider-specific; empty key = all.",
    )
