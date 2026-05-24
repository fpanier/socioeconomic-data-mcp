# AKT Statistical-Data MCP Server — Build Brief for Claude Code

**Generated:** 2026-05-23 17:53 (CEST, UTC+2) by Claude (Opus 4.7), for Frédéric Panier / AKT.
**Status:** Specification — not yet built. Hand this file to Claude Code.

**How to use this file**
1. On your laptop, create an empty repo folder and drop this file in as `BUILD_BRIEF.md`.
2. Open Claude Code in that folder.
3. Prompt: *"Read BUILD_BRIEF.md and implement it. Do Step 0 (verify endpoints) and the Eurostat provider first, write its tests, then stop for my review before adding the other providers."*
4. Review, then let it continue provider by provider.

---

## 1. Goal

Build a **self-hosted remote MCP server** that exposes official statistical APIs as Claude tools, so that — via a custom connector in claude.ai (web / desktop / mobile) — Claude can pull **primary-source** data directly into analysis and Excel workbooks, **including regional NUTS data** (Wallonia / Flanders / Brussels) that off-the-shelf connectors do not reliably provide.

Primary use case: country-vs-region comparison workbooks (GDP/capita, household disposable income, employment rates by age, at-risk-of-poverty) across BE regions, EU-27, EU averages, and OECD where available.

---

## 2. Non-negotiable principles (these match AKT's rigor standard — enforce them in code)

- **Source of record only.** Tools fetch exclusively from official provider APIs (Eurostat, OECD, IMF, World Bank, and optionally NBB/Statbel). No scraping of third-party mirrors.
- **No invention, no extrapolation.** Never estimate, interpolate, or fill gaps. If a series / geography / year is unavailable, return an explicit `"na"` marker — never a guessed value.
- **Provenance on every response.** Each tool result must carry: provider, dataset code, exact request URL + parameters, unit, reference period, and a UTC extraction timestamp.
- **Machine-readable output.** Return tidy long-format records (and a CSV string) so results flow straight into `pandas` / `openpyxl` without manual transcription.

---

## 3. Tech stack (recommended defaults — change only with reason)

- **Python 3.11+**, official **MCP Python SDK (FastMCP)**.
- **Dual transport:**
  - `stdio` — local dev/testing with Claude Code or Claude Desktop (no public hosting needed).
  - `streamable-http` — public deployment for the claude.ai custom connector. (claude.ai requires the **streamable HTTP** transport.)
- **HTTP auth:** static bearer token read from env `MCP_AUTH_TOKEN` (the server rejects requests without a matching `Authorization: Bearer` header). OAuth is an option later.
- **HTTP client:** `httpx` (timeouts, retries/backoff). Caching with `requests-cache`/`hishel` to be polite to the APIs.
- **Parsing:** prefer maintained provider packages where they exist (`eurostat`, `wbgapi`, `sdmx1`/`pandasdmx`); otherwise raw REST + `pandas`.

---

## 4. STEP 0 — Verify endpoints BEFORE writing tools (do not skip)

Provider APIs change (OECD and IMF in particular have migrated). For **each** provider in §5, use web search/fetch to open the official API documentation, confirm the **current base URL, request syntax, and auth**, identify any official Python client, and write your findings to `ENDPOINTS.md`. Implement tools only after this is recorded. Treat the URLs in §5 as *starting hypotheses to confirm*, not facts.

---

## 5. Providers & tools (build in this priority order)

For every provider implement a thin, well-described tool. Also implement two cross-provider discovery helpers so codes/dimensions never have to be hardcoded by the user:
- `search_datasets(provider, query)` → candidate dataset codes + titles.
- `describe_dataset(provider, dataset)` → dimensions, available dimension values (incl. geo list), units, latest period.

### 5.1 Eurostat — **PRIORITY**, region-aware
- Likely simplest path: the `eurostat` PyPI package (`get_data_df`, `get_toc_df`, filtering). Confirm its current API.
- REST fallback (confirm): dissemination API base
  `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}?format=JSON&lang=EN&<dim>=<val>...`
  and SDMX-CSV at `.../sdmx/2.1/data/{dataset}/{key}?format=SDMX-CSV`.
- **Tool:** `eurostat_get(dataset, filters: dict = {}, geos: list = None, latest_only: bool = False)`
  - Must return **NUTS** geographies (`BE1` Brussels, `BE2` Flanders, `BE3` Wallonia), country totals (`BE`, `DE`, …) and EU aggregates (`EU27_2020`) in a single call when requested.
  - `latest_only=True` → for each geo, the most recent non-missing period (report the period per row; do **not** force a common year).
- Datasets already identified for the current workbook (validate dimensions via `describe_dataset`):
  `nama_10r_2gdp`, `nama_10_pc`, `nama_10r_2hhinc`, `lfst_r_lfe2emprt`, `ilc_li02`, `ilc_li41`.

### 5.2 OECD (SDMX)
- New SDMX REST (confirm): `https://sdmx.oecd.org/public/rest/data/{dataflow}/{key}?...`. Consider `sdmx1` with an OECD source config.
- **Tool:** `oecd_get(dataflow, key, start=None, end=None)`.

### 5.3 IMF
- Confirm current API (legacy SDMX-JSON at `dataservices.imf.org`; DataMapper API at `imf.org/external/datamapper/api/`). Consider an existing wrapper.
- **Tool:** `imf_get(dataset, key, start=None, end=None)`.

### 5.4 World Bank — stable, no API key
- Official-style client `wbgapi` (preferred) or REST `https://api.worldbank.org/v2/country/{codes}/indicator/{indicator}?format=json`.
- **Tool:** `worldbank_get(indicator, economies, time=None)`.

### 5.5 Belgium — **OPTIONAL**, add last
- NBB.Stat (SDMX) and Statbel open data, for figures finer than Eurostat publishes. Confirm endpoints; mark clearly as Belgian-source (different methodology from Eurostat).

---

## 6. Tool output contract (identical across providers)

Return an object like:

```json
{
  "metadata": {
    "provider": "eurostat",
    "dataset": "lfst_r_lfe2emprt",
    "request_url": "https://.../data/lfst_r_lfe2emprt?...",
    "params": {"unit": "PC", "sex": "T", "age": "Y25-54"},
    "unit": "PC",
    "extracted_utc": "2026-05-23T15:53:50Z",
    "n_rows": 5,
    "notes": "latest_only=true; period reported per row"
  },
  "data": [
    {"geo": "BE2", "geo_label": "Vlaams Gewest", "time": "2024", "value": 78.1, "flag": ""},
    {"geo": "BE3", "geo_label": "Région wallonne", "time": "2024", "value": null, "flag": "na"}
  ],
  "csv": "geo,geo_label,time,value,flag\nBE2,Vlaams Gewest,2024,78.1,\n..."
}
```

Rules: missing data → `value: null`, `flag: "na"`. Preserve Eurostat observation flags (`p`, `e`, `b`, …) in `flag`. Never drop or invent rows silently.

---

## 7. Suggested project structure

```
socioeconomic-data-mcp/
  pyproject.toml
  README.md
  ENDPOINTS.md            # output of Step 0
  src/socioeconomic_data_mcp/
    __init__.py
    server.py             # FastMCP app; registers tools; transport switch
    providers/
      eurostat.py
      oecd.py
      imf.py
      worldbank.py
      belgium.py          # optional
    common.py             # httpx client, cache, output-contract helper, provenance stamp
  tests/
    test_eurostat.py ...
  .env.example            # MCP_AUTH_TOKEN=...
  Dockerfile              # optional, for deployment
```

Illustrative skeleton (confirm FastMCP + `eurostat` APIs before relying on it):

```python
# server.py
import os, datetime
from mcp.server.fastmcp import FastMCP
from .providers import eurostat

mcp = FastMCP("socioeconomic-data-mcp")

@mcp.tool()
def eurostat_get(dataset: str, filters: dict = {}, geos: list = None, latest_only: bool = False) -> dict:
    """Fetch a Eurostat dataset slice. Returns the standard provenance + data contract.
    No interpolation: missing cells are flagged 'na'."""
    return eurostat.get(dataset, filters, geos, latest_only)  # TODO implement per ENDPOINTS.md

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")  # 'stdio' | 'streamable-http'
    mcp.run(transport=transport)
```

---

## 8. Run & test locally (Claude Code)

- **stdio dev:** `MCP_TRANSPORT=stdio python -m socioeconomic_data_mcp` and register with `claude mcp add` (HTTP) or Claude Desktop's config (stdio) for an interactive smoke test.
- **Acceptance tests** (pytest + one manual cross-check each against the Eurostat data browser):
  - **T1** `eurostat_get("lfst_r_lfe2emprt", {"unit":"PC","sex":"T","age":"Y25-54"}, geos=["BE1","BE2","BE3","BE","EU27_2020"], latest_only=True)` → 5 rows, unit `PC`, a period on every row; manually verify one cell against the databrowser.
  - **T2** GDP/capita in **both** PPS and EUR for all EU-27 + BE regions, latest → no fabricated rows; any missing geo flagged `na`.
  - **T3** `ilc_li41` for BE regions → values present or explicit `na`.
  - **T4** Every response includes full `metadata` provenance.
  - **T5 (no-extrapolation guard):** request a period with no data → returns `na`, never a guess.
- Confirm the tool list is small and each description is crisp (helps Claude route correctly).

---

## 9. Deploy to your server (so it works in claude.ai web/mobile)

1. Run with `MCP_TRANSPORT=streamable-http` under `uvicorn`, behind a reverse proxy (Caddy or nginx) terminating **TLS** on a real domain.
2. Set `MCP_AUTH_TOKEN`; the server must require `Authorization: Bearer <token>` (or wire OAuth later).
3. The endpoint must be reachable from the **public internet / Anthropic's IP ranges** — claude.ai connects from Anthropic's cloud, not your laptop, so `localhost` will not work for the web/mobile app.
4. In claude.ai: **Settings → Connectors → Add custom connector →** paste the `https://…` URL → *Advanced* to set OAuth or the bearer header. (Custom connectors are in beta; available on Pro/Max/Team/Enterprise, with Free limited to one.)
5. Verify the tools appear, then have Claude run T1 through the connector.

---

## 10. Guardrails

- **Transport:** claude.ai requires streamable HTTP for remote use.
- **Security / prompt-injection:** tools return data only; never let text fetched from an API trigger actions or tool calls. Validate inputs; do not interpolate untrusted strings into shell or SQL.
- **API etiquette:** caching, exponential backoff on 429/5xx, and a `User-Agent` identifying AKT.
- **Keep scope tight:** a few high-quality tools beat many noisy ones.

---

## 11. References (re-verify as of the build date — see Step 0)

- Anthropic — custom connectors (remote MCP): https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- MCP — connect to remote servers: https://modelcontextprotocol.io/docs/develop/connect-remote-servers
- Claude Code — MCP: https://code.claude.com/docs/en/mcp
- MCP Python SDK (FastMCP): https://github.com/modelcontextprotocol/python-sdk
- Eurostat — web services / API: https://ec.europa.eu/eurostat/web/main/data/web-services
- World Bank — API: https://api.worldbank.org/v2/  ·  `wbgapi`: https://pypi.org/project/wbgapi/
- OECD — SDMX API: https://sdmx.oecd.org/
- IMF — data: https://www.imf.org/en/Data

---

*End of brief. Build Eurostat first, prove it with T1–T5, then expand.*
