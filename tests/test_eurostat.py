"""Tests for the Eurostat provider and shared infrastructure.

Two layers:
- Offline unit tests (default `pytest`): JSON-stat decoding, na/flag rules, the
  output contract, CSV rendering, and the bearer-auth ASGI middleware. No network.
- Live acceptance tests T1–T5 (`pytest -m live`): hit the real Eurostat API and
  assert the brief's acceptance criteria. Values change over time, so these assert
  structure and invariants (no fabricated rows, na for missing) rather than exact
  numbers — except T1's cross-check, which re-fetches a cell and compares it to the
  latest_only result.
"""

from __future__ import annotations

import asyncio

import pytest

from socioeconomic_data_mcp import common
from socioeconomic_data_mcp.providers import eurostat

# --------------------------------------------------------------------------- #
# Offline: a hand-built JSON-stat 2.0 fixture
# --------------------------------------------------------------------------- #
# size [2,2] over (geo, time). Flat index = geo_pos*2 + time_pos.
#   0 BE2/2023=78.1   1 BE2/2024=79.0 (provisional 'p')
#   2 BE3/2023=missing  3 BE3/2024=67.9
FIXTURE = {
    "version": "2.0",
    "class": "dataset",
    "label": "Test employment",
    "source": "Eurostat",
    "updated": "2026-04-17",
    "id": ["geo", "time"],
    "size": [2, 2],
    "dimension": {
        "geo": {"category": {
            "index": {"BE2": 0, "BE3": 1},
            "label": {"BE2": "Vlaams Gewest", "BE3": "Région wallonne"},
        }},
        "time": {"category": {
            "index": {"2023": 0, "2024": 1},
            "label": {"2023": "2023", "2024": "2024"},
        }},
    },
    "value": {"0": 78.1, "1": 79.0, "3": 67.9},
    "status": {"1": "p"},
}


def test_decode_jsonstat_flatindex_value_and_flag():
    cells, dim_ids, geo_labels, zero_dims = eurostat._decode_jsonstat(FIXTURE)
    assert dim_ids == ["geo", "time"]
    assert zero_dims == []
    assert geo_labels["BE3"] == "Région wallonne"
    by_key = {(c["geo"], c["time"]): c for c in cells}
    assert by_key[("BE2", "2023")]["value"] == 78.1
    assert by_key[("BE2", "2023")]["flag"] == ""
    # provisional flag preserved on a present value
    assert by_key[("BE2", "2024")]["value"] == 79.0
    assert by_key[("BE2", "2024")]["flag"] == "p"
    # missing observation -> explicit na, never a guess
    assert by_key[("BE3", "2023")]["value"] is None
    assert by_key[("BE3", "2023")]["flag"] == "na"
    assert by_key[("BE3", "2024")]["value"] == 67.9


def test_decode_detects_invalid_filter_zero_dim():
    doc = {
        "id": ["age", "geo"],
        "size": [0, 1],
        "dimension": {
            "age": {"category": {"index": {}, "label": {}}},
            "geo": {"category": {"index": {"BE": 0}, "label": {"BE": "Belgium"}}},
        },
        "value": {},
    }
    cells, _, _, zero_dims = eurostat._decode_jsonstat(doc)
    assert cells == []          # nothing fabricated
    assert zero_dims == ["age"]  # surfaced as an invalid filter


def test_get_pipeline_latest_only(monkeypatch):
    monkeypatch.setattr(common, "fetch_json", lambda url, params=None, **k: (FIXTURE, "http://test/url"))
    res = eurostat.get("test_ds", {}, geos=["BE2", "BE3"], latest_only=True)
    rows = {r["geo"]: r for r in res["data"]}
    assert res["metadata"]["n_rows"] == 2
    # latest non-missing per geo
    assert rows["BE2"]["time"] == "2024" and rows["BE2"]["value"] == 79.0 and rows["BE2"]["flag"] == "p"
    assert rows["BE3"]["time"] == "2024" and rows["BE3"]["value"] == 67.9
    assert res["metadata"]["request_url"] == "http://test/url"


def test_get_pipeline_full_series_keeps_na(monkeypatch):
    monkeypatch.setattr(common, "fetch_json", lambda url, params=None, **k: (FIXTURE, "http://test/url"))
    res = eurostat.get("test_ds", {}, geos=["BE2", "BE3"], latest_only=False)
    missing = [r for r in res["data"] if r["geo"] == "BE3" and r["time"] == "2023"]
    assert len(missing) == 1 and missing[0]["value"] is None and missing[0]["flag"] == "na"
    assert res["metadata"]["n_rows"] == 4  # no rows dropped


def test_output_contract_and_csv(monkeypatch):
    monkeypatch.setattr(common, "fetch_json", lambda url, params=None, **k: (FIXTURE, "http://test/url"))
    res = eurostat.get("test_ds", {}, geos=["BE2", "BE3"], latest_only=False)
    for key in ("provider", "dataset", "request_url", "params", "unit", "extracted_utc", "n_rows", "notes"):
        assert key in res["metadata"]
    assert res["metadata"]["extracted_utc"].endswith("Z")
    header, *lines = res["csv"].strip().split("\n")
    assert header == "geo,geo_label,time,value,flag"
    # na row renders with empty value cell and flag 'na'
    assert any(line.endswith(",2023,,na") for line in lines)


# --------------------------------------------------------------------------- #
# Offline: OAuth authorization-server provider
# --------------------------------------------------------------------------- #
from mcp.server.auth.provider import AuthorizationParams  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402

from socioeconomic_data_mcp.oauth import MCPOAuthProvider  # noqa: E402


def _client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="c1",
        redirect_uris=["https://cb.example/cb"],
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_method="client_secret_post",
    )


def test_oauth_register_and_get_client():
    p = MCPOAuthProvider(password="pw", admin_token="admintok")
    asyncio.run(p.register_client(_client()))
    got = asyncio.run(p.get_client("c1"))
    assert got is not None and got.client_id == "c1"


def test_oauth_full_login_and_token_flow():
    p = MCPOAuthProvider(password="pw", admin_token="admintok")
    client = _client()
    asyncio.run(p.register_client(client))
    params = AuthorizationParams(
        state="st", scopes=[], code_challenge="abc",
        redirect_uri="https://cb.example/cb", redirect_uri_provided_explicitly=True, resource=None,
    )
    url = asyncio.run(p.authorize(client, params))
    assert url.startswith("/oauth/login?txn=")
    txn = url.split("txn=", 1)[1]
    assert p.login_is_valid_txn(txn)
    assert not p.check_password("nope")
    assert p.check_password("pw")
    redirect = p.complete_login(txn)
    assert redirect.startswith("https://cb.example/cb?") and "code=" in redirect and "state=st" in redirect
    code = redirect.split("code=", 1)[1].split("&", 1)[0]
    ac = asyncio.run(p.load_authorization_code(client, code))
    assert ac is not None and ac.code_challenge == "abc"
    token = asyncio.run(p.exchange_authorization_code(client, ac))
    assert token.access_token and token.refresh_token
    assert asyncio.run(p.load_authorization_code(client, code)) is None  # one-time use
    at = asyncio.run(p.load_access_token(token.access_token))
    assert at is not None and at.client_id == "c1"


def test_oauth_admin_token_accepted_and_bad_rejected():
    p = MCPOAuthProvider(password="pw", admin_token="admintok")
    at = asyncio.run(p.load_access_token("admintok"))
    assert at is not None and at.client_id == "admin-static"
    assert asyncio.run(p.load_access_token("not-a-token")) is None


def test_oauth_invalid_txn_rejected():
    p = MCPOAuthProvider(password="pw")
    assert not p.login_is_valid_txn("nope")
    assert p.complete_login("nope") is None


def test_oauth_state_persists_across_instances(tmp_path):
    path = str(tmp_path / "state.json")
    p = MCPOAuthProvider(password="pw", state_path=path)
    asyncio.run(p.register_client(_client()))
    p2 = MCPOAuthProvider(password="pw", state_path=path)
    got = asyncio.run(p2.get_client("c1"))
    assert got is not None and got.client_id == "c1"


def test_oauth_register_notifies_once_per_new_client(monkeypatch):
    from socioeconomic_data_mcp import notify

    calls: list[str] = []
    monkeypatch.setattr(notify, "send_async", lambda subj, body: calls.append(subj))
    p = MCPOAuthProvider(password="pw")
    asyncio.run(p.register_client(_client()))
    asyncio.run(p.register_client(_client()))  # same client_id again
    assert len(calls) == 1  # only the first (new) registration notifies


def test_notify_noop_when_unconfigured(monkeypatch):
    from socioeconomic_data_mcp import notify

    monkeypatch.delenv("MCP_SMTP_HOST", raising=False)
    monkeypatch.delenv("MCP_ALERT_EMAIL", raising=False)
    notify._send("subject", "body")  # must not raise when unconfigured


# --------------------------------------------------------------------------- #
# Live acceptance tests T1–T5 (pytest -m live)
# --------------------------------------------------------------------------- #
EMP = "lfst_r_lfe2emprt"
GDP = "nama_10r_2gdp"
POV = "ilc_li41"


@pytest.mark.live
def test_T1_employment_five_geos_latest_only():
    geos = ["BE1", "BE2", "BE3", "BE", "EU27_2020"]
    # Brief used age=Y25-54, which is NOT a valid code for this dataset (verified in
    # ENDPOINTS.md). Y20-64 is the canonical EU employment-rate band.
    res = eurostat.get(EMP, {"unit": "PC", "sex": "T", "age": "Y20-64"}, geos, latest_only=True)
    assert res["metadata"]["unit"] == "PC"
    assert res["metadata"]["n_rows"] == 5
    assert {r["geo"] for r in res["data"]} == set(geos)  # no missing/fabricated geos
    for r in res["data"]:
        assert r["time"], "every row must report a period"
        assert (isinstance(r["value"], (int, float)) and r["flag"] != "na") or (
            r["value"] is None and r["flag"] == "na"
        )


@pytest.mark.live
def test_T1_crosscheck_cell_against_direct_fetch():
    """Re-fetch one geo's latest cell directly and confirm it equals latest_only."""
    geos = ["BE2"]
    latest = eurostat.get(EMP, {"unit": "PC", "sex": "T", "age": "Y20-64"}, geos, latest_only=True)
    row = latest["data"][0]
    direct = eurostat.get(
        EMP, {"unit": "PC", "sex": "T", "age": "Y20-64", "time": row["time"]}, geos, latest_only=False
    )
    assert direct["data"][0]["value"] == row["value"]


@pytest.mark.live
def test_T2_gdp_per_capita_pps_and_eur():
    units = ["EUR_HAB", "PPS_EU27_2020_HAB"]
    geos = ["BE1", "BE2", "BE3", "EU27_2020"]
    res = eurostat.get(GDP, {"unit": units}, geos, latest_only=True)
    assert res["metadata"]["unit"] is None          # multiple units -> per-row column
    assert {r["unit"] for r in res["data"]} == set(units)
    assert res["metadata"]["n_rows"] <= len(geos) * len(units)
    for r in res["data"]:
        # no fabricated rows: either a real number, or an explicit na
        assert isinstance(r["value"], (int, float)) or (r["value"] is None and r["flag"] == "na")


@pytest.mark.live
def test_T3_poverty_be_regions_present_or_na():
    geos = ["BE1", "BE2", "BE3"]
    res = eurostat.get(POV, {}, geos, latest_only=True)
    assert {r["geo"] for r in res["data"]} == set(geos)
    for r in res["data"]:
        assert isinstance(r["value"], (int, float)) or (r["value"] is None and r["flag"] == "na")


@pytest.mark.live
def test_T4_full_provenance_metadata():
    res = eurostat.get(POV, {}, ["BE2"], latest_only=True)
    m = res["metadata"]
    for key in ("provider", "dataset", "request_url", "params", "unit", "extracted_utc", "n_rows", "notes"):
        assert key in m
    assert m["provider"] == "eurostat"
    assert m["request_url"].startswith(
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
    )
    assert m["extracted_utc"].endswith("Z")


@pytest.mark.live
def test_T5_no_extrapolation_guard():
    """A long series with gap years must return na for the gaps — never a guess."""
    res = eurostat.get(POV, {"sinceTimePeriod": "2005"}, ["BE1"], latest_only=False)
    na_rows = [r for r in res["data"] if r["flag"] == "na"]
    assert na_rows, "expected at least one missing (na) period in the long series"
    for r in na_rows:
        assert r["value"] is None       # missing is null, not interpolated
    for r in res["data"]:
        if r["value"] is not None:
            assert isinstance(r["value"], (int, float)) and r["flag"] != "na"


@pytest.mark.live
def test_search_and_describe_discovery():
    found = eurostat.search("employment rates by nuts")
    assert any(r["code"] == EMP for r in found["results"])
    desc = eurostat.describe(EMP)
    assert "geo" in desc["dimensions"] and "age" in desc["dimensions"]
    assert "Y20-64" in desc["dimensions"]["age"]["codes"]
    assert desc["metadata"]["latest_period"]
