"""Tests for the World Bank, OECD and IMF providers.

Offline tests (default `pytest`) exercise parsing / na-handling with fixtures or a
monkeypatched HTTP layer. Live tests (`pytest -m live`) hit the real APIs and assert
structure/invariants (no fabricated values; missing → na).
"""

from __future__ import annotations

import os

import pytest

from socioeconomic_data_mcp import common
from socioeconomic_data_mcp.providers import (
    ardeco,
    dbnomics,
    ecb,
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


# --------------------------------------------------------------------------- #
# common.matches_query
# --------------------------------------------------------------------------- #
def test_matches_query_all_tokens():
    assert common.matches_query("gdp per capita", "NGDPDPC", "GDP per capita, current prices")
    assert common.matches_query("per capita gdp", "X", "GDP per capita")  # order-independent
    assert not common.matches_query("gdp wages", "NGDPDPC", "GDP per capita")
    assert common.matches_query("", "anything")  # empty query matches


# --------------------------------------------------------------------------- #
# OECD SDMX-CSV parser (offline)
# --------------------------------------------------------------------------- #
_OECD_CSV = (
    "REF_AREA,Reference area,MEASURE,Measure,UNIT_MEASURE,Unit of measure,"
    "TIME_PERIOD,Time period,OBS_VALUE,Observation value,OBS_STATUS,Observation status\n"
    "BEL,Belgium,GDP,Gross domestic product,USD,US dollars,2023,,100.5,,A,Normal value\n"
    "BEL,Belgium,GDP,Gross domestic product,USD,US dollars,2024,,,,M,Missing\n"
    "DEU,Germany,GDP,Gross domestic product,USD,US dollars,2023,,110.0,,A,Normal value\n"
)


def test_oecd_parse_csv():
    rows, unit = oecd._parse_csv(_OECD_CSV)
    assert unit == "USD"
    by = {(r["geo"], r["time"]): r for r in rows}
    assert by[("BEL", "2023")]["value"] == 100.5 and by[("BEL", "2023")]["flag"] == ""  # A -> normal
    assert by[("BEL", "2024")]["value"] is None and by[("BEL", "2024")]["flag"] == "na"
    assert by[("DEU", "2023")]["geo_label"] == "Germany"


# --------------------------------------------------------------------------- #
# World Bank pipeline (offline, monkeypatched HTTP)
# --------------------------------------------------------------------------- #
def test_worldbank_pipeline_na(monkeypatch):
    doc = [
        {"page": 1, "pages": 1, "per_page": 1000, "total": 2},
        [
            {"countryiso3code": "BEL", "country": {"id": "BE", "value": "Belgium"}, "date": "2024", "value": 73514.0},
            {"countryiso3code": "BEL", "country": {"id": "BE", "value": "Belgium"}, "date": "2023", "value": None},
        ],
    ]
    monkeypatch.setattr(common, "fetch_json", lambda url, params=None, **k: (doc, "http://wb/test"))
    res = worldbank.get("NY.GDP.PCAP.PP.CD", ["BEL"])
    assert res["metadata"]["n_rows"] == 2
    rows = {r["time"]: r for r in res["data"]}
    assert rows["2024"]["value"] == 73514.0 and rows["2024"]["flag"] == ""
    assert rows["2023"]["value"] is None and rows["2023"]["flag"] == "na"


# --------------------------------------------------------------------------- #
# IMF pipeline (offline, monkeypatched HTTP)
# --------------------------------------------------------------------------- #
def test_imf_pipeline_na(monkeypatch):
    def fake(url, params=None, **k):
        if url.endswith("/countries"):
            return ({"countries": {"BEL": {"label": "Belgium"}}}, url)
        return ({"values": {"NGDPDPC": {"BEL": {"2023": 50.0, "2024": None}}}}, url)

    imf._country_cache = None  # reset module cache
    monkeypatch.setattr(common, "fetch_json", fake)
    res = imf.get("NGDPDPC", ["BEL"], start="2022", end="2024")
    rows = {r["time"]: r for r in res["data"]}
    assert rows["2023"]["value"] == 50.0 and rows["2023"]["geo_label"] == "Belgium"
    assert rows["2024"]["value"] is None and rows["2024"]["flag"] == "na"
    assert "projections" in res["metadata"]["notes"]


# --------------------------------------------------------------------------- #
# Live
# --------------------------------------------------------------------------- #
@pytest.mark.live
def test_live_worldbank_latest():
    res = worldbank.get("NY.GDP.PCAP.PP.CD", ["BEL", "DEU"], latest_only=True)
    assert {r["geo"] for r in res["data"]} == {"BEL", "DEU"}
    for r in res["data"]:
        assert isinstance(r["value"], (int, float)) or (r["value"] is None and r["flag"] == "na")


@pytest.mark.live
def test_live_oecd_cli():
    res = oecd.get("OECD.SDD.STES,DSD_STES@DF_CLI", ".M.LI...AA...H", start="2025-01")
    assert res["metadata"]["n_rows"] > 0
    for r in res["data"]:
        assert isinstance(r["value"], (int, float)) or (r["value"] is None and r["flag"] == "na")


@pytest.mark.live
def test_live_imf_ngdppc():
    res = imf.get("NGDPDPC", ["BEL", "FRA"], start="2022", end="2024")
    assert {r["geo"] for r in res["data"]} == {"BEL", "FRA"}
    assert all(isinstance(r["value"], (int, float)) for r in res["data"])


@pytest.mark.live
def test_live_searches_find_codes():
    assert any(m["code"] == "NY.GDP.PCAP.PP.CD" for m in worldbank.search("GDP per capita PPP")["results"])
    assert oecd.search("composite leading indicator")["results"]
    # token match across word order/extra words (substring match used to miss this)
    assert any(m["code"] == "NGDPDPC" for m in imf.search("GDP per capita")["results"])


# --------------------------------------------------------------------------- #
# IWEPS (Wallonia subnational, CC0)
# --------------------------------------------------------------------------- #
def test_iweps_parse_csv_minimal():
    """Offline: parser maps the IWEPS CSV shape into the standard row contract."""
    sample = (
        '"ins","type_entite","entite","periode"\n'
        '3000,Région,Wallonie,année 2025,3704990\n'
        '20002,Province,Brabant Wallon,année 2025,415381\n'
        '99999,Commune,Inconnue,année 2025,\n'  # missing observation
    )
    rows, _unit = iweps._parse_csv(sample)
    assert len(rows) == 3
    by_geo = {r["geo"]: r for r in rows}
    assert by_geo["3000"]["value"] == 3704990.0
    assert by_geo["3000"]["geo_label"] == "Wallonie"
    assert by_geo["3000"]["geo_level"] == "Région"
    assert by_geo["3000"]["time"] == "2025"  # "année 2025" → "2025"
    # Empty observation → na (never invented)
    assert by_geo["99999"]["value"] is None
    assert by_geo["99999"]["flag"] == "na"


def test_iweps_normalise_code():
    """Catalog publishes 201111-0, API only accepts 201111_0 — search must convert."""
    assert iweps._normalise_code("201111-0") == "201111_0"
    assert iweps._normalise_code("200300_0") == "200300_0"  # already in API form
    assert iweps._normalise_code("") == ""


def _iweps_skip_if_down(res: dict) -> None:
    """The IWEPS API occasionally returns an empty CSV when its DB is unreachable.
    Skip live tests gracefully rather than fail the suite on a third-party outage."""
    if res["metadata"]["n_rows"] == 0:
        pytest.skip("IWEPS WalStat API returned no rows (transient outage upstream).")


@pytest.mark.live
def test_live_iweps_population_wallonie():
    """End-to-end: population of the Walloon Region (ins=3000), latest year."""
    res = iweps.get("200300_0", ins=["3000"], period="last")
    assert res["metadata"]["provider"] == "iweps"
    _iweps_skip_if_down(res)
    assert res["metadata"]["n_rows"] == 1
    row = res["data"][0]
    assert row["geo"] == "3000"
    assert row["geo_label"] == "Wallonie"
    assert isinstance(row["value"], (int, float))
    assert row["value"] > 3_000_000  # sanity (Wallonia ~3.7M)


@pytest.mark.live
def test_live_iweps_reg_plus_prov_merged():
    """When 'reg' and 'prov' are both requested, the provider makes two API
    calls and merges them — the API itself rejects mixing ins= with a level."""
    res = iweps.get("200300_0", levels=["reg", "prov"], period="last")
    _iweps_skip_if_down(res)
    geos = {r["geo"] for r in res["data"]}
    assert "3000" in geos  # Walloon Region
    assert {"20002", "50000", "60000", "80000", "90000"} <= geos  # 5 provinces


@pytest.mark.live
def test_live_iweps_search_returns_normalised_codes():
    res = iweps.search("population", limit=5)
    if res["metadata"]["n_results"] == 0:
        pytest.skip("IWEPS catalog unreachable (transient).")
    for r in res["results"]:
        assert "-" not in r["code"], f"search returned unconverted code: {r['code']}"


# --------------------------------------------------------------------------- #
# FRED
# --------------------------------------------------------------------------- #
def test_fred_norm_dates():
    assert fred._norm("2024", end=False) == "2024-01-01"
    assert fred._norm("2024", end=True) == "2024-12-31"
    assert fred._norm("2024-02", end=True) == "2024-02-29"  # leap year month-end
    assert fred._norm("2024-10", end=False) == "2024-10-01"
    assert fred._norm("2024-03-15", end=False) == "2024-03-15"
    assert fred._norm(None, end=False) is None


def test_fred_no_key(monkeypatch):
    monkeypatch.delenv("MCP_FRED_API_KEY", raising=False)
    res = fred.get("UNRATE")
    assert res["metadata"].get("error") and res["data"] == []


def test_fred_pipeline_na_and_redaction(monkeypatch):
    monkeypatch.setenv("MCP_FRED_API_KEY", "testkey")

    def fake(url, params=None, **k):
        if url.endswith("/series"):
            return ({"seriess": [{"id": "X", "title": "Test Series", "units": "Percent"}]}, url + "?api_key=testkey")
        if url.endswith("/series/observations"):
            return (
                {"observations": [{"date": "2024-01-01", "value": "4.1"}, {"date": "2024-02-01", "value": "."}]},
                url + "?series_id=X&api_key=testkey&file_type=json",
            )
        return ({}, url)

    monkeypatch.setattr(common, "fetch_json", fake)
    res = fred.get("X", start="2024")
    rows = {r["time"]: r for r in res["data"]}
    assert rows["2024-01-01"]["value"] == 4.1 and rows["2024-01-01"]["flag"] == ""
    assert rows["2024-02-01"]["value"] is None and rows["2024-02-01"]["flag"] == "na"
    assert res["metadata"]["unit"] == "Percent"
    assert "api_key=REDACTED" in res["metadata"]["request_url"]
    assert "testkey" not in res["metadata"]["request_url"]


def test_fred_daily_cap(monkeypatch):
    monkeypatch.setenv("MCP_FRED_API_KEY", "testkey")
    monkeypatch.setenv("MCP_FRED_DAILY_CAP", "2")
    fred._daily.update(day="", count=0)

    def fake(url, params=None, **k):
        if url.endswith("/series"):
            return ({"seriess": [{"title": "X", "units": "u"}]}, url)
        return ({"observations": [{"date": "2024-01-01", "value": "1.0"}]}, url)

    monkeypatch.setattr(common, "fetch_json", fake)
    assert fred.get("A")["metadata"]["n_rows"] == 1
    assert fred.get("B")["metadata"]["n_rows"] == 1
    capped = fred.get("C")  # 3rd call exceeds cap of 2
    assert capped["data"] == [] and "daily limit" in capped["metadata"]["notes"].lower()


@pytest.mark.live
def test_live_fred():
    if not os.environ.get("MCP_FRED_API_KEY"):
        pytest.skip("MCP_FRED_API_KEY not set")
    res = fred.get("UNRATE", start="2024", end="2024")
    assert res["metadata"]["unit"] == "Percent" and res["metadata"]["n_rows"] == 12
    for r in res["data"]:
        assert isinstance(r["value"], (int, float)) or (r["value"] is None and r["flag"] == "na")


# --------------------------------------------------------------------------- #
# ECB / ILOSTAT / WHO / UN SDG
# --------------------------------------------------------------------------- #
_ILO_CSV = (
    "DATAFLOW,REF_AREA,FREQ,MEASURE,SEX,AGE,TIME_PERIOD,OBS_VALUE,OBS_STATUS,UNIT_MEASURE\n"
    "ILO:DF_X(1.0),BEL,A,M,SEX_T,AGE_YGE15,2023,5.5,,PT\n"
    "ILO:DF_X(1.0),BEL,A,M,SEX_T,AGE_YGE15,2024,,,PT\n"
    "ILO:DF_X(1.0),FRA,A,M,SEX_T,AGE_YGE15,2023,7.2,,PT\n"
)


def test_parse_sdmx_csv_geo_and_na():
    rows, unit = common.parse_sdmx_csv(_ILO_CSV, drop_cols=frozenset({"DATAFLOW"}), unit_col="UNIT_MEASURE")
    assert unit == "PT"
    by = {(r["geo"], r["time"]): r for r in rows}
    assert by[("BEL", "2023")]["value"] == 5.5 and by[("BEL", "2023")]["flag"] == ""
    assert by[("BEL", "2024")]["value"] is None and by[("BEL", "2024")]["flag"] == "na"
    assert "SEX" not in by[("BEL", "2023")]  # constant dimension is dropped, not a column


@pytest.mark.live
def test_live_ecb():
    res = ecb.get("EXR", "D.USD.EUR.SP00.A", start="2026-01")
    assert res["metadata"]["n_rows"] > 0
    for r in res["data"]:
        assert isinstance(r["value"], (int, float)) or (r["value"] is None and r["flag"] == "na")


@pytest.mark.live
def test_live_ilostat():
    res = ilostat.get("DF_UNE_2EAP_SEX_AGE_RT", "", start="2023")
    assert res["metadata"]["n_rows"] > 0


@pytest.mark.live
def test_live_who():
    res = who.get("WHOSIS_000001", ["BEL"], start="2015", end="2020")
    assert {r["geo"] for r in res["data"]} == {"BEL"}
    assert all(isinstance(r["value"], (int, float)) or r["flag"] == "na" for r in res["data"])


@pytest.mark.live
def test_live_unsdg():
    res = unsdg.get("SI_POV_DAY1", ["76"], start="2015")  # Brazil (M49=76)
    assert res["metadata"]["n_rows"] > 0
    assert all(r["geo"] == "76" for r in res["data"])


def test_owid_pipeline_na_and_citation(monkeypatch):
    csv_text = "entity,code,year,life_expectancy_0\nBelgium,BEL,2018,81.5\nBelgium,BEL,2019,\nFrance,FRA,2018,82.0\n"
    monkeypatch.setattr(common, "fetch_text", lambda url, params=None, **k: (csv_text, url))
    monkeypatch.setattr(
        common, "fetch_json",
        lambda url, params=None, **k: ({"columns": {"Life expectancy": {"titleShort": "Life expectancy", "unit": "years", "citationShort": "HMD (2025)"}}}, url),
    )
    res = owid.get("life-expectancy", ["BEL"], start="2018", end="2019")
    by = {r["time"]: r for r in res["data"]}
    assert by["2018"]["value"] == 81.5 and by["2018"]["geo"] == "BEL"
    assert by["2019"]["value"] is None and by["2019"]["flag"] == "na"
    assert "FRA" not in {r["geo"] for r in res["data"]}  # entity filter applied
    assert res["metadata"]["unit"] == "years"
    assert "aggregator" in res["metadata"]["notes"] and "HMD (2025)" in res["metadata"]["notes"]


@pytest.mark.live
def test_live_owid():
    res = owid.get("life-expectancy", ["BEL"], start="2015", end="2020")
    assert {r["geo"] for r in res["data"]} == {"BEL"} and res["metadata"]["unit"] == "years"
    assert all(isinstance(r["value"], (int, float)) or r["flag"] == "na" for r in res["data"])


# --------------------------------------------------------------------------- #
# DBnomics / generic SDMX / ARDECO
# --------------------------------------------------------------------------- #
def test_dbnomics_pipeline(monkeypatch):
    doc = {"series": {"docs": [{"series_code": "S1", "dimensions": {"geo": "BE", "unit": "PC"},
                                "period": ["2020", "2021"], "value": [5.0, None]}]}}
    monkeypatch.setattr(common, "fetch_json", lambda url, params=None, **k: (doc, "http://db"))
    r = dbnomics.get("Eurostat", "une_rt_a", {"geo": ["BE"]})
    by = {x["time"]: x for x in r["data"]}
    assert by["2020"]["value"] == 5.0 and by["2020"]["geo"] == "BE"
    assert by["2021"]["value"] is None and by["2021"]["flag"] == "na"
    assert r["metadata"]["unit"] == "PC"


def test_sdmx_generic_geo_autodetect(monkeypatch):
    csv_text = "DATAFLOW,geo,unit,TIME_PERIOD,OBS_VALUE,OBS_STATUS\nX,BE,PC,2022,5.6,\nX,BE,PC,2023,,\n"
    monkeypatch.setattr(common, "fetch_text", lambda url, params=None, **k: (csv_text, url))
    r = sdmx.get("https://x/sdmx", "FLOW", "")
    by = {x["time"]: x for x in r["data"]}
    assert by["2022"]["geo"] == "BE" and by["2022"]["value"] == 5.6
    assert by["2023"]["value"] is None and by["2023"]["flag"] == "na"


def test_ardeco_pipeline(monkeypatch):
    info = {"data": {"variable": {"nutsVersionList": [2021, 2024],
                                  "datasets": [{"dimensions": [{"key": "unit", "value": "NR"}]}]}}}
    csv_text = (
        "VERSIONS,LEVEL_ID,TERRITORY_ID,NAME_HTML,YEAR,DATE,UNIT,VALUE\n"
        "2024,2,BE10,Brussels,2020,2020,NR,1219300\n"
        "2024,2,BE21,Antwerp,2020,2020,NR,1800000\n"
        "2024,0,BE,Belgium,2020,2020,NR,11500000\n"
    )
    monkeypatch.setattr(common, "post_json", lambda url, body, **k: (info, url))
    monkeypatch.setattr(common, "fetch_text", lambda url, params=None, **k: (csv_text, url))
    r = ardeco.get("SNPTD", regions=["BE1", "BE2"], nuts_level="2")
    assert {x["geo"] for x in r["data"]} == {"BE10", "BE21"}  # prefix match + level filter
    assert r["metadata"]["unit"] == "NR"


@pytest.mark.live
def test_live_dbnomics():
    r = dbnomics.get("Eurostat", "une_rt_a", {"geo": ["BE"], "sex": ["T"], "age": ["Y15-74"], "unit": ["PC_ACT"]})
    assert r["metadata"]["n_rows"] > 0 and any(x["geo"] == "BE" for x in r["data"])


@pytest.mark.live
def test_live_sdmx_generic():
    r = sdmx.get("https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1", "une_rt_a", "A.Y15-74.PC_ACT.T.BE", start="2022")
    assert r["metadata"]["n_rows"] > 0


@pytest.mark.live
def test_live_ardeco():
    r = ardeco.get("SNPTD", regions=["BE"], nuts_level="2", start="2020", end="2021")
    assert r["metadata"]["n_rows"] > 0 and all(x["geo"].startswith("BE") for x in r["data"])
