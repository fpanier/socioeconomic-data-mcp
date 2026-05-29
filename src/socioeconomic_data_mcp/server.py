"""FastMCP server: registers the data tools, configures auth, switches transport.

- stdio            → local dev / Claude Desktop / Claude Code (no hosting, no auth).
- streamable-http  → public deployment for a claude.ai custom connector.

Auth (streamable-http): OAuth 2.1 so claude.ai web/mobile can connect — the browser
sign-in is gated by MCP_OAUTH_PASSWORD (see oauth.py). The static admin token
MCP_AUTH_TOKEN is also accepted as a bearer for header-based clients (Claude
Code/Desktop, scripts). DNS-rebinding protection requires the public host to be
allow-listed via MCP_PUBLIC_HOST.

Security posture: tools return data only. Text fetched from a provider API is never
interpreted as instructions and never triggers further tool calls.
"""

from __future__ import annotations

import html
import logging
import os

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from . import common
from .oauth import LOGIN_PATH, MCPOAuthProvider
from .providers import (
    ardeco,
    dbnomics,
    ecb,
    eurostat,
    fred,
    ilostat,
    imf,
    iweps,
    oecd,
    owid,
    sdmx,
    unsdg,
    who,
    worldbank,
)

logger = logging.getLogger("socioeconomic_data_mcp")

_SUPPORTED_PROVIDERS = (
    "eurostat", "worldbank", "oecd", "imf", "fred", "ecb", "ilostat", "unsdg", "who", "owid",
    "dbnomics", "ardeco", "iweps",
)


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection config for streamable-http.

    When behind a reverse proxy the public hostname must be allow-listed or the
    MCP transport rejects requests with 421 'Invalid Host header'. Driven by env:
      MCP_PUBLIC_HOST     e.g. YOUR_DOMAIN
      MCP_ALLOWED_ORIGINS comma-separated; defaults to https://<public host>.
    Origin is only checked when present (server-to-server requests omit it).
    """
    port = os.environ.get("MCP_PORT", "8000")
    allowed_hosts = [f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"]
    public_host = os.environ.get("MCP_PUBLIC_HOST", "").strip()
    if public_host:
        allowed_hosts += [public_host, f"{public_host}:*"]
    origins_env = os.environ.get("MCP_ALLOWED_ORIGINS", "").strip()
    allowed_origins = [o.strip() for o in origins_env.split(",") if o.strip()]
    if not allowed_origins and public_host:
        allowed_origins = [f"https://{public_host}"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(set(allowed_hosts)),
        allowed_origins=allowed_origins,
    )


def _public_base_url() -> str:
    """Public base URL (OAuth issuer + resource identifier)."""
    explicit = os.environ.get("MCP_PUBLIC_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("MCP_PUBLIC_HOST", "").strip()
    if host:
        return f"https://{host}"
    return f"http://localhost:{os.environ.get('MCP_PORT', '8000')}"


def _build_server() -> tuple[FastMCP, MCPOAuthProvider | None]:
    """Build the FastMCP app, enabling OAuth when MCP_OAUTH_PASSWORD is set."""
    kwargs: dict = {
        "instructions": (
            "Pulls official statistics straight from provider APIs (currently Eurostat, "
            "including regional NUTS data) with full provenance and no invented values. "
            "Use search_datasets to find a dataset code and describe_dataset to learn its "
            "valid dimension codes (geo list, units, latest period) before calling a *_get "
            "tool. Missing observations come back as value=null with flag='na' — never a guess."
        ),
        "transport_security": _transport_security(),
    }
    password = os.environ.get("MCP_OAUTH_PASSWORD", "").strip()
    provider: MCPOAuthProvider | None = None
    if password:
        state_dir = os.environ.get("MCP_STATE_DIR", "").strip()
        provider = MCPOAuthProvider(
            password=password,
            admin_token=os.environ.get("MCP_AUTH_TOKEN"),
            state_path=os.path.join(state_dir, "oauth_state.json") if state_dir else None,
        )
        base = _public_base_url()
        kwargs["auth_server_provider"] = provider
        kwargs["auth"] = AuthSettings(
            issuer_url=base,
            resource_server_url=f"{base}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
    return FastMCP("socioeconomic-data-mcp", **kwargs), provider


mcp, _oauth_provider = _build_server()


@mcp.tool()
def eurostat_get(
    dataset: str,
    filters: dict | None = None,
    geos: list[str] | None = None,
    latest_only: bool = False,
) -> dict:
    """Fetch a slice of a Eurostat dataset as tidy long-format rows with full provenance.

    Returns {metadata, data, csv}. `metadata` carries provider, dataset, the exact
    request_url, params, unit, a UTC extraction timestamp, n_rows and notes. Each data
    row has geo, geo_label, time, value, flag (plus a column for any other dimension
    that varies). Missing observations are value=null, flag="na" — never interpolated.
    Eurostat observation flags (p provisional, e estimate, b break, …) are preserved.

    Args:
        dataset: Eurostat dataset code, e.g. "nama_10r_2gdp" (see search_datasets).
        filters: dimension code -> value(s), e.g. {"unit": "PPS_EU27_2020_HAB"}; a value
            may be a list. Time selectors are accepted here too (time, sinceTimePeriod,
            untilTimePeriod, lastTimePeriod). Use describe_dataset to find valid codes.
        geos: geographies to return in one call — NUTS regions ("BE1","BE2","BE3"),
            countries ("BE","DE"), and EU aggregates ("EU27_2020") together.
        latest_only: if true, return each geo's most recent NON-missing period (the
            period is reported per row; years are not forced to match across geos).
    """
    return eurostat.get(dataset, filters, geos, latest_only)


@mcp.tool()
def worldbank_get(
    indicator: str,
    economies: list[str] | None = None,
    time: str | None = None,
    latest_only: bool = False,
) -> dict:
    """Fetch a World Bank indicator as tidy long-format rows with full provenance.

    Returns {metadata, data, csv}; rows have geo (ISO3), geo_label, time (year), value, flag.
    Missing observations are value=null, flag="na" — never interpolated.

    Args:
        indicator: World Bank indicator code, e.g. "NY.GDP.PCAP.PP.CD" (see search_datasets).
        economies: ISO codes/aggregates in one call — countries ("BE","DE"), EU ("EUU"),
            OECD ("OED"), World ("WLD"); omit for all economies.
        time: a year "2024" or range "2010:2024"; omit for the full series.
        latest_only: if true, return each economy's most recent value (mrv=1).
    """
    return worldbank.get(indicator, economies, time, latest_only)


@mcp.tool()
def oecd_get(dataflow: str, key: str = "", start: str | None = None, end: str | None = None) -> dict:
    """Fetch an OECD SDMX dataflow slice as tidy long-format rows with full provenance.

    Returns {metadata, data, csv}; rows have geo (REF_AREA), geo_label, time, value, flag,
    plus a column for any other dimension that varies. Missing observations → value=null,
    flag="na". OBS_STATUS is preserved in flag.

    Args:
        dataflow: full ref "agency,dataflow,version" from search_datasets, e.g.
            "OECD.SDD.STES,DSD_STES@DF_CLI" (version optional).
        key: dot-separated dimension filter; empty segment = all for that dimension,
            empty string = all dimensions. Use describe_dataset / a wildcard key to explore.
        start: start period (e.g. "2015" or "2024-01"). end: end period.
    """
    return oecd.get(dataflow, key, start, end)


@mcp.tool()
def imf_get(
    indicator: str,
    countries: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Fetch an IMF DataMapper indicator as tidy long-format rows with full provenance.

    Returns {metadata, data, csv}; rows have geo (ISO3), geo_label, time (year), value, flag.
    NOTE: IMF series may include IMF estimates/projections for recent or future years —
    surfaced as-is (official IMF figures), flagged in metadata.notes; never our own guesses.

    Args:
        indicator: IMF DataMapper code, e.g. "NGDPDPC" (GDP per capita) — see search_datasets.
        countries: ISO3 codes, e.g. ["BEL","DEU","FRA"]; omit for all.
        start: first year (e.g. "2015"). end: last year (e.g. "2024").
    """
    return imf.get(indicator, countries, start, end)


@mcp.tool()
def fred_get(series_id: list[str] | str, start: str | None = None, end: str | None = None) -> dict:
    """Fetch FRED (St. Louis Fed) time series as tidy long-format rows with full provenance.

    Returns {metadata, data, csv}; rows have time (date), value, flag (and series/series_label
    when more than one series is requested). Missing observations → value=null, flag="na".

    Args:
        series_id: a FRED series id or list of ids, e.g. "UNRATE" or ["UNRATE","CPIAUCSL"]
            (see search_datasets provider="fred").
        start: first date "YYYY" or "YYYY-MM-DD". end: last date.
    """
    return fred.get(series_id, start, end)


@mcp.tool()
def ecb_get(flow: str, key: str = "", start: str | None = None, end: str | None = None) -> dict:
    """Fetch an ECB Data Portal SDMX dataflow slice as tidy long-format rows with provenance.

    Returns {metadata, data, csv}; rows have series (the ECB key), time, value, flag (plus geo
    when the flow has REF_AREA, and a column for any other dimension that varies). Missing →
    value=null, flag="na". Good for euro-area HICP inflation, interest/exchange rates, finance.

    Args:
        flow: ECB dataflow id from search_datasets, e.g. "EXR" (exchange rates), "ICP" (HICP).
        key: dot-separated dimension filter, e.g. "D.USD.EUR.SP00.A"; empty = all.
        start: start period (e.g. "2020" or "2020-01"). end: end period.
    """
    return ecb.get(flow, key, start, end)


@mcp.tool()
def ilostat_get(dataflow: str, key: str = "", start: str | None = None, end: str | None = None) -> dict:
    """Fetch an ILOSTAT (ILO) SDMX dataflow slice as tidy long-format rows with provenance.

    Returns {metadata, data, csv}; rows have geo (REF_AREA, ISO3), time, value, flag, plus
    dimensions (SEX, AGE, …) that vary. Missing → value=null, flag="na". International
    labour-market data (employment, unemployment, wages, informality).

    Args:
        dataflow: ILO dataflow id from search_datasets, e.g. "DF_UNE_2EAP_SEX_AGE_RT".
        key: dot-separated dimension filter; empty = all.
        start: start year. end: end year.
    """
    return ilostat.get(dataflow, key, start, end)


@mcp.tool()
def unsdg_get(series: str, areas: list[str] | None = None, start: str | None = None, end: str | None = None) -> dict:
    """Fetch a UN SDG series as tidy long-format rows with provenance.

    Returns {metadata, data, csv}; rows have geo (UN M49 code), geo_label (area name), time
    (year), value, flag. Missing/non-numeric → value=null, flag="na".

    Args:
        series: SDG series code from search_datasets, e.g. "SI_POV_DAY1".
        areas: UN M49 numeric codes (Belgium=56, Germany=276, World=1); omit for all areas.
        start: first year. end: last year.
    """
    return unsdg.get(series, areas, start, end)


@mcp.tool()
def who_get(indicator: str, countries: list[str] | None = None, start: str | None = None, end: str | None = None) -> dict:
    """Fetch a WHO Global Health Observatory indicator as tidy long-format rows with provenance.

    Returns {metadata, data, csv}; rows have geo (ISO3), time (year), value, flag, plus
    dimensions (e.g. SEX) when they vary. Missing → value=null, flag="na".

    Args:
        indicator: WHO GHO indicator code from search_datasets, e.g. "WHOSIS_000001" (life expectancy).
        countries: ISO3 codes, e.g. ["BEL","FRA"]; omit for all.
        start: first year. end: last year.
    """
    return who.get(indicator, countries, start, end)


@mcp.tool()
def owid_get(slug: str, entities: list[str] | None = None, start: str | None = None, end: str | None = None) -> dict:
    """Fetch an Our World in Data chart as tidy long-format rows with provenance.

    NOTE: OWID is an AGGREGATOR (secondary source) — it re-publishes primary data plus its own
    long-run/curated series. The underlying source citation is included in metadata.notes; prefer
    the primary providers (eurostat/worldbank/who/…) when the series exists there.

    Returns {metadata, data, csv}; rows have geo (ISO3 or entity), geo_label, time (year), value,
    flag (and a 'variable' column for multi-series charts). Missing → value=null, flag="na".

    Args:
        slug: OWID chart slug — the last segment of an ourworldindata.org/grapher/<slug> URL,
            e.g. "life-expectancy", "gdp-per-capita-worldbank". (No search API; use the website.)
        entities: ISO3 codes or entity names, e.g. ["BEL","FRA"] or ["World"]; omit for all.
        start: first year. end: last year.
    """
    return owid.get(slug, entities, start, end)


@mcp.tool()
def dbnomics_get(
    provider: str | None = None,
    dataset: str | None = None,
    dimensions: dict | None = None,
    series_ids: list[str] | None = None,
    max_series: int = 50,
) -> dict:
    """Fetch series from DBnomics, which federates ~50 official providers (INSEE, Bundesbank,
    BIS, IMF, Eurostat, BLS…) behind one API. Returns {metadata, data, csv}; rows have time,
    value, flag (+ geo/series when present). Missing → value=null, flag="na".

    Args:
        provider: DBnomics provider code, e.g. "IMF", "Eurostat", "BIS" (with `dataset`).
        dataset: dataset code within that provider (see search_datasets provider="dbnomics").
        dimensions: optional filter, e.g. {"geo": ["BE"], "unit": ["PC_ACT"]}.
        series_ids: alternatively, explicit IDs ["PROVIDER/DATASET/SERIES", …].
        max_series: cap on series returned (default 50).
    """
    return dbnomics.get(provider, dataset, dimensions, series_ids, max_series)


@mcp.tool()
def sdmx_get(base_url: str, flow: str, key: str = "", start: str | None = None, end: str | None = None) -> dict:
    """Fetch from ANY standard SDMX 2.1 REST endpoint as tidy long-format rows (generic connector).

    Returns {metadata, data, csv}. Best-effort: requests SDMX-CSV from {base_url}/data/{flow}/{key}.
    Use this for national statistical offices / central banks not covered by a dedicated tool.

    Args:
        base_url: SDMX service base, e.g. "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1".
        flow: dataflow id (sometimes "AGENCY,FLOW,VERSION").
        key: dot-separated dimension filter (order is provider-specific); empty = all.
        start: start period. end: end period.
    """
    return sdmx.get(base_url, flow, key, start, end)


@mcp.tool()
def ardeco_get(
    variable: str,
    unit: str | None = None,
    version: str | None = None,
    nuts_level: str | None = None,
    regions: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Fetch a JRC ARDECO regional variable — long EU NUTS series (GDP, GVA, employment,
    population), back to 1960. Returns {metadata, data, csv}; rows have geo (NUTS code),
    geo_label, time (year), value, flag.

    Args:
        variable: ARDECO variable code (see search_datasets provider="ardeco"), e.g. "SNPTD".
        unit / version: defaults to the variable's first unit + latest NUTS version (see describe_dataset).
        nuts_level: filter by NUTS level (0 country … 3); regions: NUTS codes/prefixes e.g. ["BE"].
        start / end: year range.
    """
    return ardeco.get(variable, unit, version, nuts_level, regions, start, end)


@mcp.tool()
def iweps_get(
    indicator: str,
    levels: list[str] | None = None,
    ins: list[str] | None = None,
    period: str | None = None,
) -> dict:
    """Fetch an IWEPS WalStat indicator — Wallonia subnational statistics (CC0).
    Returns {metadata, data, csv}; rows have geo (INS code), geo_label, geo_level,
    time (year), value, flag.

    Args:
        indicator: WalStat code with sub-index, e.g. "200300_0" (population) — see
            search_datasets provider="iweps". The base code "200300" usually has 1-15
            variants (200300_0..200300_14) with different breakdowns (sex, age, etc.).
        levels: geographic levels — any of {"com","arr","prov","reg"}. Defaults to
            ["reg","prov"] (Walloon Region + 5 provinces).
        ins: optional list of INS codes to keep only specific entities (e.g. ["3000"]
            for Walloon Region only).
        period: "last" for the most-recent observation only, or a year like "2024".
    """
    return iweps.get(indicator, levels, ins, period)


_DESCRIBE = {
    "eurostat": eurostat.describe,
    "worldbank": worldbank.describe,
    "oecd": oecd.describe,
    "imf": imf.describe,
    "fred": fred.describe,
    "ecb": ecb.describe,
    "ilostat": ilostat.describe,
    "unsdg": unsdg.describe,
    "who": who.describe,
    "owid": owid.describe,
    "ardeco": ardeco.describe,
    "iweps": iweps.describe,
}
_SEARCH = {
    "eurostat": eurostat.search,
    "worldbank": worldbank.search,
    "oecd": oecd.search,
    "imf": imf.search,
    "fred": fred.search,
    "ecb": ecb.search,
    "ilostat": ilostat.search,
    "unsdg": unsdg.search,
    "who": who.search,
    "owid": owid.search,
    "dbnomics": dbnomics.search,
    "ardeco": ardeco.search,
    "iweps": iweps.search,
}


@mcp.tool()
def describe_dataset(provider: str, dataset: str) -> dict:
    """Describe a dataset/indicator's structure so codes never have to be guessed.

    For Eurostat: dimensions with every valid code + label (incl. geo list), units, latest
    period. For World Bank: the indicator's name, source, definition and topics. Call this
    before a *_get tool to choose correct codes.

    Args:
        provider: data provider — "eurostat", "worldbank", "oecd", or "imf".
        dataset: dataset/indicator code, e.g. "lfst_r_lfe2emprt" or "NY.GDP.PCAP.PP.CD".
    """
    fn = _DESCRIBE.get(provider)
    return fn(dataset) if fn else _unsupported(provider)


@mcp.tool()
def search_datasets(provider: str, query: str) -> dict:
    """Find candidate dataset/indicator codes + titles matching a query (no hardcoding needed).

    Returns matches (code, title, and source/coverage where available). Use the returned
    code with describe_dataset / a *_get tool.

    Args:
        provider: data provider — "eurostat", "worldbank", "oecd", or "imf".
        query: free text matched against code and title, e.g. "employment region" or "GDP per capita".
    """
    fn = _SEARCH.get(provider)
    return fn(query) if fn else _unsupported(provider)


def _unsupported(provider: str) -> dict:
    return {
        "metadata": {
            "error": "unsupported_provider",
            "provider": provider,
            "supported": list(_SUPPORTED_PROVIDERS),
            "extracted_utc": common.utc_now_iso(),
        },
        "data": [],
    }


# --------------------------------------------------------------------------- #
# Public custom routes (NOT behind MCP auth): health check + OAuth sign-in page
# --------------------------------------------------------------------------- #
@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _login_html(txn: str, error: str = "") -> str:
    err = f'<p style="color:#b00020">{html.escape(error)}</p>' if error else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Socio-Economic Data — sign in</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{font-family:system-ui,-apple-system,sans-serif;max-width:22rem;margin:4rem auto;"
        "padding:0 1rem;color:#222}input,button{font-size:1rem;padding:.55rem;width:100%;"
        "box-sizing:border-box;margin:.35rem 0}button{background:#1a7f4b;color:#fff;border:0;"
        "border-radius:.35rem;cursor:pointer}</style></head><body>"
        "<h2>Socio-Economic Data connector</h2><p>Enter the access password to connect Claude.</p>"
        f"{err}"
        f"<form method='post' action='{LOGIN_PATH}'>"
        f"<input type='hidden' name='txn' value='{html.escape(txn)}'>"
        "<input type='password' name='password' placeholder='Password' autofocus required>"
        "<button type='submit'>Sign in</button></form></body></html>"
    )


@mcp.custom_route(LOGIN_PATH, methods=["GET", "POST"])
async def oauth_login(request: Request):
    if _oauth_provider is None:
        return PlainTextResponse("OAuth is not enabled on this server.", status_code=404)

    expired = "This sign-in link is invalid or expired. Start the connection again from Claude."
    if request.method == "GET":
        txn = request.query_params.get("txn", "")
        if not _oauth_provider.login_is_valid_txn(txn):
            return HTMLResponse(_login_html("", expired), status_code=400)
        return HTMLResponse(_login_html(txn))

    form = await request.form()
    txn = str(form.get("txn", ""))
    password = str(form.get("password", ""))
    if not _oauth_provider.login_is_valid_txn(txn):
        return HTMLResponse(_login_html("", expired), status_code=400)
    if not _oauth_provider.check_password(password):
        return HTMLResponse(_login_html(txn, "Incorrect password."), status_code=401)
    url = _oauth_provider.complete_login(txn)
    if not url:
        return HTMLResponse(_login_html("", expired), status_code=400)
    return RedirectResponse(url, status_code=302)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def _run_http() -> None:
    import uvicorn

    public = os.environ.get("MCP_ALLOW_PUBLIC", "").strip().lower() in ("1", "true", "yes")
    if _oauth_provider is None and not public:
        raise SystemExit(
            "streamable-http needs auth: set MCP_OAUTH_PASSWORD (OAuth) "
            "or MCP_ALLOW_PUBLIC=1 to run intentionally open (rate-limit at the proxy)."
        )
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8000"))
    mode = "OAuth enabled" if _oauth_provider is not None else "PUBLIC (no auth — protect at the proxy)"
    if _oauth_provider is None:
        logger.warning("socioeconomic-data-mcp running PUBLIC (no auth); MCP_ALLOW_PUBLIC is set")
    logger.info("socioeconomic-data-mcp on http://%s:%s%s (%s)", host, port, mcp.settings.streamable_http_path, mode)
    uvicorn.run(mcp.streamable_http_app(), host=host, port=port, log_level="info")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip()
    if transport == "streamable-http":
        _run_http()
    elif transport == "stdio":
        mcp.run(transport="stdio")
    else:
        raise SystemExit(f"Unknown MCP_TRANSPORT={transport!r} (use 'stdio' or 'streamable-http')")


if __name__ == "__main__":
    main()
