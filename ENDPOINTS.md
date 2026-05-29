# ENDPOINTS.md — Step 0 verification

**Verified:** 2026-05-23 (live HTTP probes + official docs). Re-verify before relying on any
provider marked _UNVERIFIED / re-verify_. Treat letter-flag and dimension details for
non-Eurostat providers as provisional until their implementation step.

Legend: ✅ verified live this session · 📄 confirmed from official docs only · ⚠️ to re-verify at implementation.

---

## MCP Python SDK (FastMCP) ✅/📄

- **Package:** `mcp` (install `mcp[cli]` for the CLI helpers). Min Python ≥ 3.10; we run 3.14.
- **Import:** `from mcp.server.fastmcp import FastMCP`
- **Tool definition:** `@mcp.tool()` on a typed function. Type hints + docstring auto-generate the
  input schema and tool description. Return type annotation drives structured output.
- **Run / transports:** `mcp.run(transport="stdio")` or `mcp.run(transport="streamable-http")`.
  Host/port come from `FastMCP(... )` settings (env `FASTMCP_HOST`/`FASTMCP_PORT` or constructor
  `host=`, `port=`); default `127.0.0.1:8000`. claude.ai custom connectors require
  **streamable-http**.
- **Auth:** SDK ships an OAuth 2.1 resource-server path (`TokenVerifier` + `AuthSettings`,
  RFC 9728). The brief wants a *static bearer token* first. ⚠️ Implementation decision: wrap the
  Starlette ASGI app (`mcp.streamable_http_app()`) with a small middleware that rejects requests
  whose `Authorization: Bearer <token>` ≠ `MCP_AUTH_TOKEN`, and serve it under uvicorn. OAuth is a
  later upgrade. Verify `streamable_http_app()` exists in the installed version at build time.
- Source: https://github.com/modelcontextprotocol/python-sdk

---

## 5.1 Eurostat — PRIORITY ✅ (probed live)

- **Base (data):**
  `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{DATASET}?format=JSON&lang=EN&<dim>=<val>...`
- **Filtering:** any dimension = `&<dim_code>=<value_code>`, **repeatable** for multiple values
  (e.g. `&geo=BE1&geo=BE2&geo=BE3`). Param order doesn't matter. `lang` ∈ {EN, FR, DE}.
- **Time params** (use only ONE, except since+until together):
  `time=2024`, `sinceTimePeriod=2019`, `untilTimePeriod=2024`, `lastTimePeriod=N`.
- **Decision: raw REST + httpx** (NOT the `eurostat` PyPI pkg) — avoids a 3.14 dependency risk and
  gives exact-request-URL provenance for free. Brief explicitly allows this.

### JSON-stat 2.0 response shape (confirmed by probe)
Top-level keys: `version` ("2.0"), `class` ("dataset"), `label` (title), `source`, `updated`,
`value`, `id`, `size`, `dimension`, `extension`.
- `id`: ordered dimension list, e.g. `["freq","unit","sex","age","geo","time"]`.
- `size`: parallel sizes, e.g. `[1,1,1,12,1,1]`. **A requested value that matches no category →
  that dimension's size is 0 and `value` is empty** (this is how we caught `age=Y25-54` being
  invalid — see below).
- `dimension[d].category.index`: `{code: position}`; `.label`: `{code: human label}`.
- `value`: **sparse object** `{flat_index_str: number}`. Missing observations are simply absent.
- `status`: **sparse object** `{flat_index_str: flag}` — present only when flags exist.
  Observed `'p'` (provisional) on 2024 GDP. `':'` = not available. An index may appear in `status`
  but not in `value` (→ na), or in both (real value carrying a flag like `p`/`e`/`b`).
- **Flat index = row-major (C-order)** over `size`: last dimension varies fastest.
  `index = Σ_d position_d × (Π sizes after d)`. Decode by reversing this.
- `extension`: carries `datastructure` id/version, annotations, `positions-with-no-data`.
- Large requests → **HTTP 413** body `ASYNCHRONOUS_RESPONSE` — handle by narrowing the query.

### Parser rules (→ output contract)
Enumerate the full cross-product of returned dimension positions (in practice only geo×time are
multi-valued after filtering). For each cell: value = `value[idx]` or `null`; flag = `status[idx]`
if present else `""`; if value is null set flag to `na` (unless status gives a more specific code).
Never drop or invent rows. For `latest_only`: per geo, keep the row with the max `time` whose value
is not null (report that period per row; do **not** force a common year).

### Discovery
- **search_datasets** → catalogue TOC (TSV, ~2 MB, cache it):
  `https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en`
  Columns: `title, code, type, last update of data, last table structure change, data start,
  data end, values`. Filter rows where `type=="dataset"` and query matches title/code. (Titles are
  indented with leading spaces — strip them.)
- **describe_dataset** → query the dataset with `lastTimePeriod=1` (minimal) and read every
  `dimension[d].category` (codes+labels incl. full geo list), units, and the latest period.

### Datasets validated this session
- `lfst_r_lfe2emprt` "Employment rates by NUTS 2 region", data 1999–2025.
  **Valid `age` codes:** `Y15-24, Y15-64, Y15-74, Y_GE15, Y20-64, Y25-34, Y25-64, Y_GE25, Y35-44,
  Y45-54, Y55-64, Y_GE65`. ⚠️ **The brief's T1 uses `age=Y25-54`, which does NOT exist here.**
  Use `Y20-64` (canonical EU employment-rate band) for T1; `Y25-64`/`Y_GE25` are the nearest
  prime-age options. dims: `freq,unit,sex,age,geo,time`.
- `nama_10r_2gdp` "GDP by NUTS 2 region". **Units:** `MIO_EUR, EUR_HAB, EUR_HAB_EU27_2020,
  MIO_NAC, MIO_PPS_EU27_2020, PPS_EU27_2020_HAB, PPS_HAB_EU27_2020`.
  **GDP/capita = `EUR_HAB` (EUR) and `PPS_EU27_2020_HAB` (PPS).** 2024 came back flagged `p`.
- Still to validate via `describe_dataset` when used: `nama_10_pc`, `nama_10r_2hhinc`,
  `ilc_li02`, `ilc_li41`.

Sources: https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/api-statistics
· https://ec.europa.eu/eurostat/web/main/data/web-services

---

## 5.2 OECD (SDMX) 📄 — implement after review

- **Base:** `https://sdmx.oecd.org/public/rest/data/{agency},{dataflow},{version}/{key}?...`
  e.g. `https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/.M.LI...AA...H?startPeriod=2023-02&dimensionAtObservation=AllDimensions&format=csvfilewithlabels`
- **Dataflow list:** `https://sdmx.oecd.org/public/rest/dataflow/all`
- **Key:** dot-separated dimension values; empty segment = wildcard. `+` = latest version.
- **Formats:** `format=jsondata` (SDMX-JSON), `csvfile`/`csvfilewithlabels` (SDMX-CSV — easiest to
  parse tidy), `genericdata` (XML). Params: `startPeriod`, `endPeriod`, `dimensionAtObservation`.
- **Auth:** none (public). **Client:** `sdmx1` (pandasdmx successor) has an OECD source. ⚠️ check
  3.14 wheels; SDMX-CSV via httpx is the dependency-free fallback.
- Source: https://sdmx.oecd.org/ · OECD data API explainer (oecd.org/en/data/insights).

## 5.3 IMF 📄 — implement after review

- **Current/recommended:** SDMX 3.0 at `https://api.imf.org/external/sdmx/3.0` (verify exact data
  path + dataflow IDs at build).
- **Legacy SDMX 2.1:** `dataservices.imf.org` (older; confirm still alive before use).
- **DataMapper API v2** (simple, indicator/country oriented): `https://www.imf.org/external/datamapper/api/v1/...`
  (help: imf.org/external/datamapper/api/help) — good for headline series & comparisons.
- **Auth:** none documented. **Clients:** `imfp`, `sdmx1`. ⚠️ Re-verify which endpoint is live in
  2026 — IMF has migrated; do live probes first.
- Source: https://data.imf.org/en/Resource-Pages/IMF-API · imf.org/external/datamapper/api/help

## 5.4 World Bank 📄 — implement after review

- **Base:** `https://api.worldbank.org/v2/country/{codes}/indicator/{indicator}?format=json`
  (multiple economies: `;`-separated, e.g. `BE;DE;FR`). **No API key.**
- **Pagination:** `page`, `per_page`; response is `[meta, [rows...]]`. Latest value helpers:
  `mrv=N` (most recent N), `mrnev=N` (most recent non-empty). ⚠️ A probe combining
  `per_page&mrnev` returned an error page — re-verify exact param combo + JSON vs default XML at
  implementation (always send `format=json`).
- **Client:** `wbgapi` (preferred). ⚠️ check 3.14 wheels; REST via httpx is the fallback.
- Source: https://api.worldbank.org/v2/ · https://pypi.org/project/wbgapi/

## 5.5 Belgium (OPTIONAL, last) ⚠️ UNVERIFIED

- NBB.Stat (SDMX) and Statbel open data. Confirm public SDMX/REST base URLs by live probe before
  building. Mark all output clearly as Belgian-source (methodology differs from Eurostat).
- Source: https://www.nbb.be/ (NBB.Stat) · https://statbel.fgov.be/en/open-data

---

## Phase 2 — verified live 2026-05-23 (OECD / World Bank / IMF / FRED)

### World Bank ✅ (probed)
- Data: `https://api.worldbank.org/v2/country/{economies}/indicator/{indicator}?format=json`
  economies are `;`-separated ISO codes or aggregates (`EUU`=EU, `OED`=OECD, `WLD`=World), or `all`.
- Response: JSON array `[meta, [rows]]`. meta has `page,pages,per_page,total`. Each row:
  `{indicator{id,value}, country{id,value}, countryiso3code, date, value, unit, obs_status, decimal}`.
  `value` may be null → na. Paginate via `page`/`per_page` (use a big per_page + loop).
- Latest: `mrv=N` (most recent N) / `mrnev=N` (most recent non-empty). Range: `date=2010:2024`.
- Discovery: `/v2/indicator?format=json&per_page=...` (all indicators; filter id/name) ·
  `/v2/indicator/{id}?format=json` (metadata: name, source, sourceNote, topics). No key.

### OECD ✅ (probed)
- Data: `https://sdmx.oecd.org/public/rest/data/{agency},{dataflow},{ver}/{key}?startPeriod=&endPeriod=&format=csvfilewithlabels`
  e.g. dataflow `OECD.SDD.STES,DSD_STES@DF_CLI`, key `.M.LI...AA...H` (dot-separated, blank=wildcard).
- `format=csvfilewithlabels` → tidy CSV with paired CODE,Label columns incl. **REF_AREA / TIME_PERIOD
  / OBS_VALUE / OBS_STATUS** (OBS_STATUS = flag). Parse straight to long rows. No key.
- Dataflow discovery: `https://sdmx.oecd.org/public/rest/dataflow/all` (large).

### IMF ⚠️ (DataMapper reachable; finalise at implementation)
- DataMapper v1: `https://www.imf.org/external/datamapper/api/v1/{indicator}/{country}` →
  `{"values":{INDICATOR:{COUNTRY:{year:value}}}}`. Indicators list: `/v1/indicators` (200).
  CAVEAT: a country-path probe returned a different country's series — confirm correct
  country-filter syntax (likely `/v1/{indicator}/{ISO3}` with specific casing, or query all + filter)
  before building. SDMX 3.0 base `https://api.imf.org/external/sdmx/3.0` as alternative.

### FRED (needs free API key — not yet built)
- `https://api.stlouisfed.org/fred/series/observations?series_id=&api_key=&file_type=json`
  Key from env MCP_FRED_API_KEY. Search: `/fred/series/search?search_text=`. US + intl macro series.

---

## Phase 3 — "others" feasibility sweep, verified live 2026-05-24

- **ECB Data Portal ✅** `https://data-api.ecb.europa.eu/service/data/{flow}/{key}?format=csvdata`
  CSV columns: KEY, <dimensions…>, TIME_PERIOD, OBS_VALUE, OBS_STATUS, …, TITLE, UNIT.
  Dimensions are the columns BEFORE TIME_PERIOD (skip KEY); attributes after. No key.
  Dataflows: `…/service/dataflow/ECB` (SDMX structure).
- **ILOSTAT ✅** SDMX at `https://sdmx.ilo.org/rest/` (`/dataflow/ILO` → 200). Confirm data CSV format + a real flow at build.
- **UN SDG ✅** `https://unstats.un.org/sdgapi/v1/sdg/` — `/Indicator/List` (JSON), data via `/Series/Data` or `/Indicator/Data`. Areas use UN M49 codes (Belgium=056). No key.
- **WHO GHO ✅** OData `https://ghoapi.azureedge.net/api/` — `/Indicator` (list), `/{IndicatorCode}` (data: SpatialDim ISO3, TimeDim, NumericValue). `$filter`/`$top` supported. No key.
- **NBB.Stat ❌ (investigated 2026-05-24)** `stat.nbb.be` returns 000 from TWO independent networks
  (local + the production server) — refuses programmatic clients; only the browser Data Explorer
  works. Legacy `restsdmx/sdmx.ashx` also 000. Not integrable without scraping → DROPPED.
- **Statbel ❌** `data.gov.be` serves an HTML portal (not a JSON/CKAN API at the action path);
  Statbel is per-dataset file downloads. No generic query API → DROPPED.
- **WID ⚠️ (investigated 2026-05-24)** Real API: `https://rfap9nitz6.execute-api.eu-west-1.amazonaws.com/prod/`
  endpoints `countries-available-variables` and `get_data_variables`, but it's UNDOCUMENTED, requires
  an `x-api-key` header (base64) embedded in their R/Stata tools, and returns percentile-coded data.
  Buildable best-effort only; inequality is otherwise covered by World Bank `SI.POV.GINI` + Eurostat
  `ilc_*`. Pending user decision.

---

## Phase 4 — high-leverage additions, verified live 2026-05-24

- **DBnomics ✅** `https://api.db.nomics.world/v22/`. Series:
  `/series/{provider}/{dataset}?dimensions={json}&observations=1&limit=N` →
  `series.docs[]` each with `series_code`, `dimensions{}`, parallel `period[]` + `value[]`.
  Search datasets: `/search?q=...`. Providers: `/providers`. No key. **Federates ~50 providers**
  (INSEE, Bundesbank, BIS, IMF, Eurostat, BLS…) — one integration, huge coverage.
- **Generic SDMX ✅** `GET {base}/data/{flow}/{key}?startPeriod=&endPeriod=` with
  `Accept: application/vnd.sdmx.data+csv` → plain SDMX-CSV (common.parse_sdmx_csv). geo column
  varies by source (REF_AREA / geo / LOCATION) → parser now auto-detects. Best-effort for standard
  SDMX 2.1 REST (national stat offices, central banks). Key dimension order is the caller's job.
- **OECD regional ✅** reachable via the EXISTING `oecd_get` — regional dataflows live under
  `OECD.CFE.*` (e.g. `DSD_REG_*`); `search_datasets(provider="oecd", query="regional")` finds them.
  No new tool needed; surfaced in docs.
- **ARDECO (JRC) ✅** GraphQL `https://territorial.ec.europa.eu/ardeco-api-v2/graphql`
  (`variableList{code description}`; `variable(id:"X"){nutsVersionList datasets{dimensions{key value}}}`).
  REST export `https://territorial.ec.europa.eu/ardeco-api-v2/rest/export/{variable}?unit={unit}&version={nutsVersion}`
  → **CSV** `VERSIONS,LEVEL_ID,TERRITORY_ID,NAME_HTML,YEAR,DATE,UNIT,VALUE` (ALL regions/years,
  back to 1960). Server-side level/year/nutscode filters return empty → **filter client-side**.

- **IWEPS WalStat ✅** REST `https://opendata.iweps.be/api/data/{csv|json}/{indicator}/{options}`
  → CSV `ins,type_entite,entite,periode,<value>` (value column unnamed). Wallonia subnational only
  (commune / arrondissement / province / Région wallonne = ins=3000). Options use `+` as separator:
  `com+arr+prov` selects three levels at once, `ins=3000+period=last` filters by entity AND period.
  **Quirk**: the API **rejects mixing `ins=` with a level keyword** in the same URL (returns 404),
  so requesting `reg+prov` requires two calls (region via `ins=3000`, provinces via `prov`) merged
  client-side. Catalog `https://opendata.iweps.be/statdcat-ap/walstat` (DCAT-RDF, twice/year).
  **Code quirk**: catalog publishes codes with a dash (`201111-0`) but the API only accepts the
  underscore form (`201111_0`) — the provider normalises on the way out of `search`.
  Licence CC0. Public docs at `https://www.iweps.be/outils/open-data/`.
