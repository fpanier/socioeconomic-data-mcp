# Socio‑Economic Data MCP

**One MCP connector to the world's official socio‑economic data — straight inside Claude.**

A self‑hosted [Model Context Protocol](https://modelcontextprotocol.io) server that unifies the
major official statistics providers behind a single, consistent interface, so Claude (via a
claude.ai custom connector, or locally in Claude Code / Desktop) can pull **primary‑source** economic,
social, labour, health and regional data — with **full provenance** and **no invented values**.

It speaks **13 providers** directly and federates **~50 more** through DBnomics, plus a **generic
SDMX** connector for any national statistical office or central bank — all returning the *same*
tidy `{metadata, data, csv}` shape.

> Try it instantly (free, hosted): add the remote MCP server **`https://YOUR_DOMAIN/mcp`** as a
> custom connector in claude.ai — **no login required**. (Rate‑limited; for heavy or production use,
> self‑host with your own keys.)

## Why it's different

- **Breadth under one contract** — global macro, trade, labour, prices, development, health and
  inequality from many sources, all in one identical record shape (most MCP data servers are
  single‑source).
- **No invention, ever** — a missing series/geo/period returns `value: null, flag: "na"`. Never a guess.
- **Provenance on every response** — provider, dataset, the exact request URL + params, unit,
  reference period (per row), and a UTC timestamp. Aggregators (OWID, DBnomics) carry the underlying
  source in the notes.
- **Regional depth** — true subnational coverage: Eurostat **NUTS** (e.g. Belgian regions BE1/BE2/BE3),
  OECD regional, and JRC **ARDECO** long series back to 1960 — alongside countries and aggregates in
  one call.
- **Self‑hosted & open** — you own the endpoint, the auth, the rate‑limiting.

## Providers & tools (15 tools)

| Tool | Source | Coverage |
| --- | --- | --- |
| `eurostat_get` | Eurostat | EU, incl. NUTS regions |
| `worldbank_get` | World Bank | global, WDI |
| `oecd_get` | OECD (SDMX) | OECD + regional (TL2/TL3) |
| `imf_get` | IMF DataMapper | global macro (+ projections) |
| `fred_get` | FRED (St. Louis Fed) | US + international macro |
| `ecb_get` | ECB Data Portal | euro‑area HICP, rates, FX, finance |
| `ilostat_get` | ILOSTAT (ILO) | global labour market |
| `unsdg_get` | UN SDG | Sustainable Development indicators |
| `who_get` | WHO GHO | global health |
| `owid_get` | Our World in Data | curated/long‑run (aggregator) |
| `dbnomics_get` | **DBnomics** | **federates ~50 providers** (INSEE, Bundesbank, BIS, BLS…) |
| `sdmx_get` | **any SDMX 2.1 endpoint** | national stat offices / central banks |
| `ardeco_get` | JRC **ARDECO** | EU regional (NUTS) long series since 1960 |
| `search_datasets(provider, query)` | — | find dataset/indicator codes |
| `describe_dataset(provider, dataset)` | — | dimensions, valid codes, units, latest period |

### Output contract (identical across providers)

```json
{
  "metadata": {"provider": "...", "dataset": "...", "request_url": "...",
               "params": {}, "unit": "...", "extracted_utc": "...Z",
               "n_rows": 5, "notes": "..."},
  "data": [{"geo": "BE2", "geo_label": "Vlaams Gewest", "time": "2024",
            "value": 78.1, "flag": ""}],
  "csv": "geo,geo_label,time,value,flag\n..."
}
```
Observation flags (`p` provisional, `e` estimate, `b` break, …) are preserved; missing → `flag: "na"`.

## Self‑host

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**Local (stdio)** — Claude Code / Desktop, no hosting, no auth:
```bash
MCP_TRANSPORT=stdio python -m socioeconomic_data_mcp
claude mcp add socioeco -- python -m socioeconomic_data_mcp     # (venv active)
```

**Remote (streamable‑http)** — for a claude.ai custom connector. Pick an auth mode:
```bash
export MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8000 MCP_PUBLIC_HOST=your.domain
# A) OAuth (claude.ai web/mobile):  export MCP_OAUTH_PASSWORD=…   (sign‑in gate)
# B) Public/free:                   export MCP_ALLOW_PUBLIC=1     (rate‑limit at your proxy)
# Optional: FRED needs a free key:  export MCP_FRED_API_KEY=…  MCP_FRED_DAILY_CAP=500
python -m socioeconomic_data_mcp
```
Served at `/mcp` (+ open `/healthz`). Put it behind a TLS reverse proxy (nginx/Caddy) with a per‑IP
rate limit. A `Dockerfile` is included. See [`ENDPOINTS.md`](ENDPOINTS.md) for every provider's
verified API details and quirks.

## Tests

```bash
pytest              # offline (parsers, contract, auth, na/flag rules)
pytest -m live      # live acceptance tests against the real provider APIs
```

## Principles

Source of record only · no scraping of mirrors · no invention/extrapolation · provenance on every
response · machine‑readable long‑format + CSV. Data is fetched live from official APIs and is subject
to each provider's terms (attribution is preserved in provenance).

## License

MIT — see [`LICENSE`](LICENSE). The data itself belongs to its providers under their respective terms.
