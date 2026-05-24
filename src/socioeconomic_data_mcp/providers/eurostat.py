"""Eurostat provider — raw REST against the dissemination API (JSON-stat 2.0).

Endpoints (verified live, see ENDPOINTS.md):
- data:      https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{DATASET}
- catalogue: https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en

No interpolation, no invention: a requested cell with no published observation is
returned with ``value=None`` and ``flag="na"``. Eurostat observation flags
(``p`` provisional, ``e`` estimate, ``b`` break, …) are preserved in ``flag`` on
present values.
"""

from __future__ import annotations

from typing import Any

from .. import common

_DATA_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
_TOC_URL = "https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt"

# Time selectors the dissemination API accepts (only one may be used, except
# sinceTimePeriod + untilTimePeriod together). If the caller supplies any of
# these in `filters`, we respect it instead of injecting our own window.
_TIME_PARAMS = {"time", "time_period", "sinceTimePeriod", "untilTimePeriod", "lastTimePeriod"}

# When latest_only is requested and the caller gave no time selector, bound the
# response to the most recent N periods, then pick the latest non-missing per geo.
_LATEST_WINDOW = 12


# --------------------------------------------------------------------------- #
# JSON-stat 2.0 decoding
# --------------------------------------------------------------------------- #
def _invert_index(index: dict[str, int]) -> list[str]:
    """category.index is ``{code: position}``; return ``[code]`` ordered by position."""
    out: list[str | None] = [None] * len(index)
    for code, pos in index.items():
        out[pos] = code
    return [c for c in out if c is not None]


def _strides(size: list[int]) -> list[int]:
    """Row-major (C-order) strides: last dimension varies fastest."""
    strides = [1] * len(size)
    acc = 1
    for i in range(len(size) - 1, -1, -1):
        strides[i] = acc
        acc *= size[i]
    return strides


def _decode_jsonstat(doc: dict) -> tuple[list[dict], list[str], dict[str, str], list[str]]:
    """Decode a JSON-stat 2.0 dataset into long-format cells.

    Returns ``(cells, dim_ids, geo_labels, zero_dims)`` where each cell is a dict
    of ``{dim_id: code, ..., "value": float|None, "flag": str}``. ``zero_dims``
    lists any dimension whose filter matched no category (size 0) — i.e. an
    invalid filter value.
    """
    dim_ids: list[str] = list(doc.get("id", []))
    size: list[int] = list(doc.get("size", []))
    dims = doc.get("dimension", {})
    value = doc.get("value") or {}
    status = doc.get("status") or {}

    zero_dims = [d for d, s in zip(dim_ids, size) if s == 0]

    pos2code = {d: _invert_index(dims[d]["category"]["index"]) for d in dim_ids}
    geo_labels: dict[str, str] = dict(dims.get("geo", {}).get("category", {}).get("label", {}))

    strides = _strides(size)
    total = 1
    for s in size:
        total *= s

    cells: list[dict] = []
    for n in range(total):
        rem = n
        codes: dict[str, Any] = {}
        for i, d in enumerate(dim_ids):
            codes[d] = pos2code[d][rem // strides[i]]
            rem %= strides[i]
        key = str(n)
        val = value.get(key)
        flag = status.get(key, "")
        if val is None:
            # Missing observation — explicit na, never a guess. (':' / absent.)
            flag = "na"
        codes["value"] = val
        codes["flag"] = flag
        cells.append(codes)
    return cells, dim_ids, geo_labels, zero_dims


# --------------------------------------------------------------------------- #
# eurostat_get
# --------------------------------------------------------------------------- #
def get(
    dataset: str,
    filters: dict | None = None,
    geos: list[str] | None = None,
    latest_only: bool = False,
) -> dict:
    filters = dict(filters or {})
    dataset = dataset.strip()

    # Assemble query params (httpx repeats keys for list values).
    params: dict[str, Any] = {k: v for k, v in filters.items()}
    if geos:
        existing = params.get("geo")
        merged: list[str] = []
        if existing:
            merged += [existing] if isinstance(existing, str) else list(existing)
        merged += list(geos)
        params["geo"] = list(dict.fromkeys(merged))
    params.setdefault("format", "JSON")
    params.setdefault("lang", "EN")

    caller_set_time = any(k in filters for k in _TIME_PARAMS)
    if latest_only and not caller_set_time:
        params["lastTimePeriod"] = _LATEST_WINDOW

    url = f"{_DATA_BASE}/{dataset}"
    doc, final_url = common.fetch_json(url, params)

    cells, dim_ids, geo_labels, zero_dims = _decode_jsonstat(doc)

    notes_parts: list[str] = []
    if zero_dims:
        notes_parts.append(
            "no categories matched the filter for dimension(s) "
            f"{zero_dims}; use describe_dataset to list valid codes"
        )

    # Which dimensions vary (besides geo/time)? Those become row columns; the
    # fixed ones (single selected value) go into metadata.params instead.
    has_geo = "geo" in dim_ids
    has_time = "time" in dim_ids
    free_dims = [
        d
        for d in dim_ids
        if d not in ("geo", "time") and _distinct(cells, d) > 1
    ]

    rows = _build_rows(cells, free_dims, geo_labels, has_geo, has_time)

    if latest_only and has_time:
        rows = _latest_per_group(rows, free_dims, has_geo)
        notes_parts.append("latest_only=true; most recent non-missing period reported per geo")

    rows = _sort_rows(rows, geos, free_dims, has_geo, has_time)

    unit = _single_unit(cells, dim_ids)
    meta_params = {k: v for k, v in filters.items() if k not in ("format", "lang")}
    if geos:
        meta_params["geos"] = list(geos)

    return common.build_result(
        provider="eurostat",
        dataset=dataset,
        request_url=final_url,
        params=meta_params,
        unit=unit,
        rows=rows,
        notes="; ".join(notes_parts),
    )


def _distinct(cells: list[dict], dim: str) -> int:
    return len({c[dim] for c in cells}) if cells else 0


def _build_rows(
    cells: list[dict],
    free_dims: list[str],
    geo_labels: dict[str, str],
    has_geo: bool,
    has_time: bool,
) -> list[dict]:
    rows: list[dict] = []
    for c in cells:
        row: dict[str, Any] = {}
        if has_geo:
            row["geo"] = c.get("geo")
            row["geo_label"] = geo_labels.get(c.get("geo", ""), "")
        if has_time:
            row["time"] = c.get("time")
        for d in free_dims:
            row[d] = c.get(d)
        row["value"] = c["value"]
        row["flag"] = c["flag"]
        rows.append(row)
    return rows


def _group_key(row: dict, free_dims: list[str], has_geo: bool) -> tuple:
    key = tuple(row.get(d) for d in free_dims)
    return (row.get("geo"),) + key if has_geo else key


def _latest_per_group(rows: list[dict], free_dims: list[str], has_geo: bool) -> list[dict]:
    """For each (geo + free-dim) group, keep the most recent period whose value is
    not None. If a group has no data at all, keep one na row at its latest period."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(_group_key(r, free_dims, has_geo), []).append(r)

    out: list[dict] = []
    for members in groups.values():
        present = [r for r in members if r.get("value") is not None]
        if present:
            out.append(max(present, key=lambda r: str(r.get("time", ""))))
        else:
            out.append(max(members, key=lambda r: str(r.get("time", ""))))
    return out


def _sort_rows(
    rows: list[dict],
    geos: list[str] | None,
    free_dims: list[str],
    has_geo: bool,
    has_time: bool,
) -> list[dict]:
    geo_order = {g: i for i, g in enumerate(geos)} if geos else {}

    def key(r: dict):
        parts: list[Any] = []
        if has_geo:
            parts.append(geo_order.get(r.get("geo"), len(geo_order)))
            parts.append(str(r.get("geo") or ""))
        parts.extend(str(r.get(d) or "") for d in free_dims)
        if has_time:
            parts.append(str(r.get("time") or ""))
        return tuple(parts)

    return sorted(rows, key=key)


def _single_unit(cells: list[dict], dim_ids: list[str]) -> str | None:
    if "unit" not in dim_ids:
        return None
    units = {c.get("unit") for c in cells}
    return next(iter(units)) if len(units) == 1 else None


# --------------------------------------------------------------------------- #
# describe_dataset
# --------------------------------------------------------------------------- #
def describe(dataset: str) -> dict:
    """Return a dataset's dimensions, their codes+labels (incl. full geo list),
    units, and the latest available period."""
    dataset = dataset.strip()
    url = f"{_DATA_BASE}/{dataset}"
    params = {"format": "JSON", "lang": "EN", "lastTimePeriod": 1}
    try:
        doc, final_url = common.fetch_json(url, params)
    except common.RequestTooLargeError:
        return {
            "metadata": {
                "provider": "eurostat",
                "dataset": dataset,
                "extracted_utc": common.utc_now_iso(),
                "notes": "dataset too large to describe in one call; query with a "
                "geo filter to inspect a slice",
            },
            "dimensions": {},
        }

    dim_ids = list(doc.get("id", []))
    dims = doc.get("dimension", {})
    out_dims: dict[str, Any] = {}
    for d in dim_ids:
        cat = dims[d]["category"]
        codes = _invert_index(cat["index"])
        labels = cat.get("label", {})
        out_dims[d] = {
            "n": len(codes),
            "codes": codes,
            "labels": {c: labels.get(c, "") for c in codes},
        }

    time_codes = out_dims.get("time", {}).get("codes", [])
    return {
        "metadata": {
            "provider": "eurostat",
            "dataset": dataset,
            "title": doc.get("label", ""),
            "source": doc.get("source", ""),
            "updated": doc.get("updated", ""),
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "latest_period": time_codes[-1] if time_codes else None,
            "dimension_order": dim_ids,
        },
        "dimensions": out_dims,
    }


# --------------------------------------------------------------------------- #
# search_datasets
# --------------------------------------------------------------------------- #
def search(query: str, limit: int = 40) -> dict:
    """Search the Eurostat catalogue (table of contents) for datasets matching
    ``query`` in their code or title."""
    text, final_url = common.fetch_text(_TOC_URL, {"lang": "en"})
    matches: list[dict] = []
    for line in text.splitlines():
        cols = [c.strip().strip('"') for c in line.split("\t")]
        if len(cols) < 3 or cols[2] != "dataset":
            continue
        title, code = cols[0].strip(), cols[1]
        if common.matches_query(query, code, title):
            entry = {"code": code, "title": title}
            if len(cols) >= 8:
                entry.update({"data_start": cols[5], "data_end": cols[6], "values": cols[7]})
            matches.append(entry)
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "eurostat",
            "query": query,
            "request_url": final_url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": f"showing up to {limit} dataset matches",
        },
        "results": matches,
    }
