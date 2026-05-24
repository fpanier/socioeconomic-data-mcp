"""Our World in Data (OWID) provider — grapher CSV API. No API key.

AGGREGATOR / secondary source: OWID re-publishes data from primary providers
(WB/WHO/UN/Eurostat/…) plus its own long-run, curated and modelled series. We surface
OWID's per-series source citation in metadata.notes so the original source stays visible.

- data:     https://ourworldindata.org/grapher/{slug}.csv?csvType=full&useColumnShortNames=true
            → columns: entity, code (ISO3, empty for aggregates), year, <value column(s)>
- metadata: https://ourworldindata.org/grapher/{slug}.metadata.json (title, unit, citation)

No public chart-search API, so a slug is required (the last segment of a chart URL).
"""

from __future__ import annotations

import csv
import io

from .. import common

_BASE = "https://ourworldindata.org/grapher"


def _meta(slug: str) -> dict:
    try:
        doc, _ = common.fetch_json(f"{_BASE}/{slug}.metadata.json")
    except common.ProviderError:
        return {"title": slug, "unit": None, "citation": ""}
    cols = doc.get("columns", {}) if isinstance(doc, dict) else {}
    first = next(iter(cols.values()), {}) if cols else {}
    return {
        "title": first.get("titleShort") or first.get("titleLong") or slug,
        "unit": first.get("unit"),
        "citation": first.get("citationShort") or first.get("attributionShort") or "",
    }


def get(
    slug: str,
    entities: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    slug = slug.strip()
    meta = _meta(slug)
    text, final_url = common.fetch_text(f"{_BASE}/{slug}.csv", {"csvType": "full", "useColumnShortNames": "true"})
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    lower = {f.lower(): f for f in fields}
    ent_c, code_c, year_c = lower.get("entity"), lower.get("code"), lower.get("year")
    value_cols = [f for f in fields if f not in (ent_c, code_c, year_c)]
    multi = len(value_cols) > 1
    wanted = {e.strip().lower() for e in entities} if entities else set()

    rows: list[dict] = []
    for r in reader:
        code = (r.get(code_c) or "").strip()
        entity = (r.get(ent_c) or "").strip()
        if wanted and code.lower() not in wanted and entity.lower() not in wanted:
            continue
        yr = (r.get(year_c) or "").strip()
        if start and yr.isdigit() and int(yr) < int(start):
            continue
        if end and yr.isdigit() and int(yr) > int(end):
            continue
        geo = code or entity
        for vc in value_cols:
            val = common._obs_float(r.get(vc))
            row = {"geo": geo, "geo_label": entity, "time": yr, "value": val, "flag": "" if val is not None else "na"}
            if multi:
                row = {"variable": vc, **row}
            rows.append(row)

    rows.sort(key=lambda x: (str(x.get("geo") or ""), str(x.get("time") or "")))
    notes = "OWID is an aggregator/secondary source."
    if meta["citation"]:
        notes += f" Underlying source: {meta['citation']}"

    meta_params: dict = {}
    if entities:
        meta_params["entities"] = entities
    if start:
        meta_params["start"] = start
    if end:
        meta_params["end"] = end

    return common.build_result(
        provider="owid",
        dataset=slug,
        request_url=final_url,
        params=meta_params,
        unit=meta["unit"],
        rows=rows,
        notes=notes,
    )


def describe(slug: str) -> dict:
    slug = slug.strip()
    meta = _meta(slug)
    return {
        "metadata": {
            "provider": "owid",
            "dataset": slug,
            "request_url": f"{_BASE}/{slug}.metadata.json",
            "extracted_utc": common.utc_now_iso(),
            "notes": "OWID is an aggregator; 'citation' names the underlying primary source(s)",
        },
        "indicator": {"slug": slug, "title": meta["title"], "unit": meta["unit"], "citation": meta["citation"]},
    }


def search(query: str, limit: int = 40) -> dict:
    return {
        "metadata": {
            "provider": "owid",
            "query": query,
            "extracted_utc": common.utc_now_iso(),
            "n_results": 0,
            "notes": (
                "OWID has no public search API. Find a chart at ourworldindata.org/grapher/<slug> "
                "and pass <slug> (the last URL segment) to owid_get / describe_dataset."
            ),
        },
        "results": [],
    }
