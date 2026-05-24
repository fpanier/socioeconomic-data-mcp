# Publishing to the MCP ecosystem

The server is public at **`https://YOUR_DOMAIN/mcp`** (free, no‑auth, rate‑limited) and the
code is on GitHub. Below is everything needed to list it in the MCP directories. Positioning to use
everywhere: **"unified MCP access to the world's official socio‑economic data"** (not "a Eurostat tool").

## One‑line description (paste everywhere)
> Unified MCP access to the world's official socio‑economic data — Eurostat, World Bank, OECD, IMF,
> FRED, ECB, ILOSTAT, UN SDG, WHO, OWID, JRC ARDECO, + DBnomics (~50 more) and any SDMX endpoint —
> one interface, full provenance, no invented values.

**Tags:** `data`, `economics`, `statistics`, `socioeconomic`, `eurostat`, `world-bank`, `oecd`, `imf`,
`fred`, `ecb`, `sdmx`, `dbnomics`, `regional`, `nuts`, `remote-mcp`.

## 1) Official MCP Registry (registry.modelcontextprotocol.io)
Uses the included [`server.json`](server.json). Install the publisher CLI and publish:
```bash
# https://github.com/modelcontextprotocol/registry  → mcp-publisher
mcp-publisher login github         # authenticates repo ownership (io.github.OWNER/*)
mcp-publisher publish               # validates + publishes server.json
```
(The `name` must stay namespaced to the GitHub owner: `io.github.OWNER/…`.)

## 2) awesome‑mcp‑servers (GitHub, highest visibility)
Repo: `punkpeye/awesome-mcp-servers`. Fork → add one line under **"📊 Data Platforms"** (or
"Finance / Data") → open a PR:
```
- [OWNER/socioeconomic-data-mcp](https://github.com/OWNER/socioeconomic-data-mcp) 🐍 ☁️ — Unified access to the world's official socio‑economic data (Eurostat, World Bank, OECD, IMF, FRED, ECB, ILO, UN, WHO, OWID, ARDECO + DBnomics/SDMX) with provenance and no invented values.
```
(🐍 = Python, ☁️ = cloud/remote service.) Also consider `wong2/awesome-mcp-servers`.

## 3) Directory sites (submit URL + the blurb above)
- **mcp.so** — submit at mcp.so (Submit/Add server) with the GitHub URL.
- **Glama** — glama.ai/mcp/servers → it auto‑indexes public GitHub MCP repos; ensure topics/README are set.
- **Smithery** — smithery.ai → add server (supports remote servers; point to the GitHub repo / URL).
- **PulseMCP** — pulsemcp.com → submit server.

## 4) GitHub repo hygiene (helps auto‑indexers rank it)
- Set repo **Description** to the one‑liner and add the **topics/tags** above.
- Keep `README.md` (done), `LICENSE` (MIT, done), `server.json` (done).
- A short demo GIF (ask Claude "compare GDP/capita Wallonia vs Flanders vs EU‑27") boosts click‑through.

## Caveats before promoting hard
- The hosted endpoint runs on one server with a shared **FRED** key (daily‑capped) — heavy use should
  self‑host with their own key. The README says so.
- Watch the daily usage report (in your server access/usage logs) after each post; tighten the nginx rate limit
  if needed.
