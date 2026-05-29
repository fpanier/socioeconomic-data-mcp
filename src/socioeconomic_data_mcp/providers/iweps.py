"""IWEPS provider — Wallonia statistics via the WalStat open data API. No API key.

Wallonia-only subnational statistics (commune / arrondissement / province / région wallonne).
Useful for indicators that don't appear in Eurostat or where Eurostat's NUTS-1 BE3 needs
finer breakdowns (sub-provincial detail) for policy work in the Walloon Region.

- data:    https://opendata.iweps.be/api/data/{csv|json}/{indicator}/{options}
- catalog: https://opendata.iweps.be/statdcat-ap/walstat   (DCAT-RDF, refreshed twice/year)
- doc:     https://www.iweps.be/outils/open-data/

The ``indicator`` code is the WalStat number with an optional sub-index (e.g. ``200300_0``
for the default variant of "Population"). Options compose geographic levels with ``+`` —
e.g. ``com+arr+prov+reg`` to get all four levels in one CSV, or ``ins=3000`` for the
Walloon Region only, ``period=last`` for the most recent observation only.

CSV format: ``ins,type_entite,entite,periode,<value>`` (no header for the value column).
Missing observation → ``value=None, flag="na"`` (never invented).
Period is normalised: "année YYYY" → "YYYY", "JJ/MM/AAAA" → "AAAA".

INS reference codes commonly used:
- 3000  = Région wallonne (entire Walloon Region)
- 20001 = Province de Brabant wallon
- 20002–20005 = autres provinces wallonnes (Hainaut, Liège, Luxembourg, Namur)
- 21000 = Région de Bruxelles-Capitale (not officially in WalStat)
- 2000  = Région flamande (idem)
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from typing import Any

from .. import common

_BASE = "https://opendata.iweps.be/api/data"
_CATALOG = "https://opendata.iweps.be/statdcat-ap/walstat"
# DCAT-RDF namespaces
_NS = {
    "dcat":   "http://www.w3.org/ns/dcat#",
    "dct":    "http://purl.org/dc/terms/",
    "rdf":    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs":   "http://www.w3.org/2000/01/rdf-schema#",
    "skos":   "http://www.w3.org/2004/02/skos/core#",
    "foaf":   "http://xmlns.com/foaf/0.1/",
    "stdc":   "http://data.europa.eu/m8g/StatDCAT-AP#",
}

# Pattern pour normaliser "année 2024" → "2024" et "01/01/2024" → "2024".
_RE_YEAR = re.compile(r"\b(\d{4})\b")


def _norm_period(s: str | None) -> str:
    """Extrait une année 4 chiffres de chaînes IWEPS variées."""
    if not s:
        return ""
    m = _RE_YEAR.search(s)
    return m.group(1) if m else s.strip()


def _parse_csv(text: str) -> tuple[list[dict], str | None]:
    """Parse l'API CSV IWEPS — l'en-tête est ``ins,type_entite,entite,periode`` puis
    une colonne de valeur sans nom. Renvoie ``(rows, unit_hint)``."""
    reader = csv.reader(io.StringIO(text))
    rows: list[dict] = []
    header: list[str] = []
    for i, raw in enumerate(reader):
        if i == 0:
            header = [c.strip().strip('"') for c in raw]
            continue
        if not raw or len(raw) < 4:
            continue
        rec = {h: (raw[j] if j < len(raw) else "") for j, h in enumerate(header)}
        # 5e colonne = valeur (sans nom)
        val_raw = raw[4] if len(raw) >= 5 else ""
        # Parfois en colonne 5 il y a un en-tête vide ; valeur essentielle prise par index.
        val = common._obs_float(val_raw)
        rows.append({
            "geo": str(rec.get("ins") or "").strip(),
            "geo_label": (rec.get("entite") or "").strip(),
            "geo_level": (rec.get("type_entite") or "").strip(),  # Commune/Province/Arrondissement/Région
            "time": _norm_period(rec.get("periode")),
            "value": val,
            "flag": "" if val is not None else "na",
        })
    return rows, None


def _build_options(levels: list[str] | None, ins: list[str] | None, period: str | None) -> str:
    """Build the IWEPS option path. The API does NOT allow mixing ``ins=`` with a level
    keyword in the same URL — those need separate calls (we handle that in ``get``)."""
    parts: list[str] = []
    if ins:
        parts.append("ins=" + ",".join(s.strip() for s in ins if s.strip()))
    elif levels:
        parts.append("+".join(levels))
    if period:
        parts.append(f"period={period}")
    return "+".join(parts) if parts else "prov"


def get(
    indicator: str,
    levels: list[str] | None = None,
    ins: list[str] | None = None,
    period: str | None = None,
) -> dict:
    """Fetch a WalStat indicator slice as the standard tidy result.

    Args:
        indicator: WalStat indicator code, e.g. "200300_0" (population). The trailing
            "_N" selects one of several variants (call ``describe_dataset`` to list).
        levels: list of geographic levels — any of {"com","arr","prov","reg"}. ``reg``
            is a shortcut for the Walloon Region as a whole (it is fetched via
            ``ins=3000`` because the API has no ``reg`` keyword) and is automatically
            merged with the other levels. Default: ``["reg","prov"]``.
        ins: optional list of INS codes to keep only specific entities
            (e.g. ``["3000"]`` = Région wallonne only). When ``ins`` is provided,
            ``levels`` is ignored to match the API behaviour.
        period: ``"last"`` for most-recent observation only, or a year like ``"2024"``.
    """
    indicator = indicator.strip()
    if not indicator:
        raise common.ProviderError("IWEPS indicator code is required (e.g. '200300_0').")

    # Normalise levels: separate the synthetic "reg" (= region as a whole) from
    # the genuine API keywords {com, arr, prov}.
    if ins:
        # Explicit INS takes precedence; one call.
        opts = _build_options(None, ins, period)
        url = f"{_BASE}/csv/{indicator}/{opts}"
        text, final_url = common.fetch_text(url, None)
        rows, unit = _parse_csv(text)
        urls_used = [final_url]
    else:
        if levels is None:
            levels = ["reg", "prov"]
        valid = {"com", "arr", "prov", "reg"}
        bad = [l for l in levels if l not in valid]
        if bad:
            raise common.ProviderError(
                f"Unknown geographic level(s) {bad}. Valid: com|arr|prov|reg."
            )
        want_region = "reg" in levels
        api_levels = [l for l in levels if l != "reg"]

        rows: list[dict] = []
        urls_used: list[str] = []
        unit: str | None = None

        if api_levels:
            opts = _build_options(api_levels, None, period)
            url = f"{_BASE}/csv/{indicator}/{opts}"
            text, final_url = common.fetch_text(url, None)
            r1, _ = _parse_csv(text)
            rows.extend(r1)
            urls_used.append(final_url)
        if want_region:
            opts = _build_options(None, ["3000"], period)
            url = f"{_BASE}/csv/{indicator}/{opts}"
            text, final_url = common.fetch_text(url, None)
            r2, _ = _parse_csv(text)
            rows.extend(r2)
            urls_used.append(final_url)

        # Stable sort: region first, then provinces by INS, etc.
        rows.sort(key=lambda r: (0 if r.get("geo") == "3000" else 1, str(r.get("geo") or ""), str(r.get("time") or "")))
        final_url = urls_used[0] if urls_used else f"{_BASE}/csv/{indicator}/"

    return common.build_result(
        provider="iweps",
        dataset=indicator,
        request_url=final_url,
        params={"indicator": indicator, "levels": levels, "ins": ins, "period": period},
        unit=unit,
        rows=rows,
        notes=(
            "IWEPS / WalStat (CC0). Wallonia subnational levels: commune (com), "
            "arrondissement (arr), province (prov). 'reg' is a synthetic keyword "
            "translating to ins=3000 (Région wallonne) — fetched in a separate API "
            "call and merged client-side, since the IWEPS API rejects mixing ins= "
            "with a level keyword in the same URL. "
            "Catalog: opendata.iweps.be/statdcat-ap/walstat — licence CC0."
        ),
    )


def _catalog() -> tuple[list[dict], str]:
    """Parse the DCAT-RDF catalog into [{code, title, description, theme}]."""
    text, url = common.fetch_text(_CATALOG, None, headers={"Accept": "application/rdf+xml"})
    out: list[dict] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return out, url
    # Look for dcat:Dataset elements.
    for ds in root.iter("{%s}Dataset" % _NS["dcat"]):
        identifier = ""
        title_fr = ""
        descr_fr = ""
        theme = ""
        for child in ds:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "identifier":
                identifier = (child.text or "").strip()
            elif tag == "title":
                lang = child.get("{http://www.w3.org/XML/1998/namespace}lang", "")
                if lang in ("fr", "") and not title_fr:
                    title_fr = (child.text or "").strip()
            elif tag == "description":
                lang = child.get("{http://www.w3.org/XML/1998/namespace}lang", "")
                if lang in ("fr", "") and not descr_fr:
                    descr_fr = (child.text or "").strip()
            elif tag == "theme":
                res = child.get("{%s}resource" % _NS["rdf"])
                if res:
                    theme = res.rsplit("/", 1)[-1]
        if identifier or title_fr:
            out.append({
                "code": identifier,
                "title": title_fr,
                "description": descr_fr,
                "theme": theme,
            })
    return out, url


def _normalise_code(code: str) -> str:
    """The DCAT catalog publishes codes with a dash (e.g. ``201111-0``) but the API
    accepts only the underscore form (``201111_0``). Normalise on the way out so
    users can paste the code straight into ``iweps_get``."""
    if not code:
        return code
    return code.replace("-", "_", 1) if "-" in code else code


def search(query: str, limit: int = 40) -> dict:
    """Search the WalStat catalog by free text in code/title/description."""
    items, url = _catalog()
    matches = []
    for it in items:
        if common.matches_query(query, it.get("code") or "", it.get("title") or "",
                                it.get("description") or ""):
            matches.append({
                "code": _normalise_code(it.get("code") or ""),
                "title": it.get("title"),
                "description": it.get("description"),
                "theme": it.get("theme"),
            })
            if len(matches) >= limit:
                break
    return {
        "metadata": {
            "provider": "iweps",
            "query": query,
            "request_url": url,
            "extracted_utc": common.utc_now_iso(),
            "n_results": len(matches),
            "notes": ("Use the returned 'code' (underscore form) with iweps_get. "
                      "Indicators may have variants — e.g. 200300_0..200300_14."),
        },
        "results": matches,
    }


def describe(indicator: str) -> dict:
    """Describe a WalStat indicator (best-effort: match by code or prefix in catalog)."""
    items, url = _catalog()
    want = indicator.strip()
    # exact match first, then prefix (without _N suffix).
    base = want.split("_", 1)[0]
    hits = [it for it in items if it.get("code") == want]
    if not hits:
        hits = [it for it in items if (it.get("code") or "").startswith(base)]
    return {
        "metadata": {
            "provider": "iweps",
            "dataset": indicator,
            "request_url": url,
            "extracted_utc": common.utc_now_iso(),
            "notes": ("Try /api/data/csv/<code>/reg+prov+arr+com for full breakdown. "
                      "WalStat is published in French; CC0 licence."),
        },
        "variants": hits[:40],
    }
