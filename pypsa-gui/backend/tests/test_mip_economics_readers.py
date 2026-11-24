"""The economics readers of a MIP's bus prices (assistant-assurance 17, slice 2a).

On an integer (MIP) solve HiGHS returns no duals and PyPSA writes 0.0 to every
bus price. Revenue, the storage charge cost and every figure that holds them
(the storage carrier's opex and LCOE, an LCOS, a Link's LCOE) are then not
established: each reader returns None with the gate's reason, never 0. An LP
of the same network is the control: its figures are still read.
"""

from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from harness.solve_validity import DUALS_NOT_MARGINAL, mip_price_limit
from services.solver_service import SolverConfig

CFG = SolverConfig()


def _rdf(*args, **kwargs):
    from routers.results import _result_df

    return _result_df(*args, **kwargs)


def _solved(committable: bool) -> pypsa.Network:
    """A committable (MIP) or plain (LP) generator, a battery that charges in the
    cheap hour, and an electrolyser Link: one row per asset class."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "d", bus="el", p_set=[2.0, 12.0, 3.0, 12.0])
    n.add("Load", "h2_demand", bus="h2", p_set=1.0)
    n.add(
        "Generator",
        "cheap",
        bus="el",
        carrier="wind",
        p_nom=8,
        marginal_cost=5,
        committable=committable,
        p_min_pu=0.1 if committable else 0.0,
    )
    n.add(
        "Generator",
        "peak",
        bus="el",
        carrier="gas",
        p_nom_extendable=True,
        p_nom_max=20,
        capital_cost=1.0,
        marginal_cost=60,
    )
    n.add(
        "StorageUnit",
        "battery",
        bus="el",
        carrier="battery",
        p_nom_extendable=True,
        p_nom_max=4,
        max_hours=2,
        capital_cost=10.0,
        efficiency_store=0.95,
        efficiency_dispatch=0.95,
    )
    n.add(
        "Link",
        "electrolysis",
        bus0="el",
        bus1="h2",
        carrier="H2 electrolysis",
        p_nom_extendable=True,
        p_nom_max=5,
        efficiency=0.7,
        capital_cost=100.0,
    )
    status, _ = n.optimize(solver_name="highs")
    assert status == "ok"
    return n


@pytest.fixture(scope="module")
def mip():
    n = _solved(committable=True)
    assert mip_price_limit(n, CFG) == DUALS_NOT_MARGINAL
    assert (n.buses_t.marginal_price == 0.0).all().all()
    assert (n.storage_units_t.p["battery"] < -1e-6).any()  # it charges
    return n


@pytest.fixture(scope="module")
def lp():
    n = _solved(committable=False)
    assert mip_price_limit(n, CFG) is None
    assert (n.buses_t.marginal_price["el"] > 0).all()
    assert (n.storage_units_t.p["battery"] < -1e-6).any()
    return n


def _economics(n, price_limit=None):
    from services.compare.economics import _compute_economics_summary

    return _compute_economics_summary(
        n, [], False, True, prices_from_state=False, cfg=CFG, price_limit=price_limit
    )


# ── Compare / Results economics (by carrier) ─────────────────────────────


def test_compare_economics_of_a_mip_withholds_every_figure_priced_at_its_duals(mip):
    econ = _economics(mip, mip_price_limit(mip, CFG))
    assert econ.available is True
    assert econ.unavailable_reason == DUALS_NOT_MARGINAL
    assert econ.per_asset_lcoh == []
    for carrier, row in econ.by_carrier.items():
        assert row.revenue_meur is None, carrier
        assert row.storage_charge_cost_meur is None, carrier
    battery = econ.by_carrier["battery"]
    assert battery.opex_meur is None  # it holds the charge cost
    assert battery.lcoe_eur_per_mwh is None
    gas = econ.by_carrier["gas"]  # no charge cost: its opex and LCOE stand
    assert gas.opex_meur is not None and gas.opex_meur.total > 0
    assert gas.lcoe_eur_per_mwh is not None
    assert gas.capex_meur is not None and gas.dispatch_gwh.total > 0


def test_compare_economics_of_an_lp_keeps_its_figures_and_its_json(lp):
    econ = _economics(lp)
    assert econ.unavailable_reason is None
    assert "unavailable_reason" not in econ.model_dump()
    assert "unavailable_reason" not in econ.model_dump_json()
    assert econ.by_carrier["gas"].revenue_meur.total > 0
    assert econ.by_carrier["battery"].storage_charge_cost_meur.total > 0
    assert econ.by_carrier["battery"].opex_meur is not None
    assert econ.per_asset_lcoh  # the electrolyser's row


@pytest.mark.parametrize("which", ["mip", "lp"])
def test_compare_judges_a_saved_project_s_economics_by_its_own_solve(
    tmp_path, which, request
):
    """S3: the Compare summary's economics take the project's own limit, the one its prices take."""
    from types import SimpleNamespace

    from routers.compare import get_results_summary

    n = request.getfixturevalue(which)
    n.export_to_netcdf(str(tmp_path / "network.nc"))
    summary = get_results_summary(project=SimpleNamespace(name="p", directory=tmp_path))
    econ = summary.economics
    if which == "mip":
        assert econ.unavailable_reason == DUALS_NOT_MARGINAL
        assert summary.prices.unavailable_reason == DUALS_NOT_MARGINAL
        assert all(r.revenue_meur is None for r in econ.by_carrier.values())
    else:
        assert econ.unavailable_reason is None
        assert econ.by_carrier["gas"].revenue_meur.total > 0


def test_economics_by_carrier_names_the_reason_only_on_a_mip(mip, lp):
    from services.results.economics_by_carrier import compute_economics_by_carrier

    out = compute_economics_by_carrier(
        mip, CFG, None, result_df=_rdf, price_limit=DUALS_NOT_MARGINAL
    )
    assert out["unavailable_reason"] == DUALS_NOT_MARGINAL
    assert all(row["revenue_meur"] is None for row in out["by_carrier"].values())
    assert out["by_carrier"]["battery"]["opex_meur"] is None
    out_lp = compute_economics_by_carrier(lp, CFG, None, result_df=_rdf)
    assert "unavailable_reason" not in out_lp
    assert out_lp["by_carrier"]["gas"]["revenue_meur"] is not None


def test_a_workbook_opex_total_over_a_withheld_carrier_is_not_a_partial_sum(mip, lp):
    """S1: a carrier's withheld opex makes the Σ-carriers total unknown, never the
    smaller sum of the rest."""
    from services.results_workbook.overview import _econ_sum
    from services.results.economics_by_carrier import compute_economics_by_carrier

    by_mip = compute_economics_by_carrier(
        mip, CFG, None, result_df=_rdf, price_limit=DUALS_NOT_MARGINAL
    )["by_carrier"]
    assert _econ_sum(by_mip, "opex_meur", []) == (None, {})
    assert _econ_sum(by_mip, "revenue_meur", []) == (None, {})
    assert _econ_sum(by_mip, "capex_meur", [])[0] is not None
    by_lp = compute_economics_by_carrier(lp, CFG, None, result_df=_rdf)["by_carrier"]
    assert _econ_sum(by_lp, "opex_meur", [])[0] > 0


# ── asset economics (per asset) ──────────────────────────────────────────


def _assets(n, price_limit=None):
    from services.results.asset_economics import compute_asset_economics

    return compute_asset_economics(n, CFG, result_df=_rdf, price_limit=price_limit)


def _row(payload, cls, name):
    return next(r for r in payload[cls] if r["name"] == name)


def test_asset_economics_of_a_mip_withholds_its_priced_keys(mip):
    from services.results.asset_economics import PRICED_KEYS

    ae = _assets(mip, DUALS_NOT_MARGINAL)
    assert ae["price_limit"] == DUALS_NOT_MARGINAL
    assert ae["reading_notes"] == []
    for cls, name in (
        ("generators", "peak"),
        ("storage_units", "battery"),
        ("links", "electrolysis"),
    ):
        rows = [r for r in ae[cls] if r["name"] == name]
        for row in rows:
            for entry in [row, *(row.get("by_period") or [])]:
                for key in PRICED_KEYS[cls]:
                    if key in entry:
                        assert entry[key] is None, (cls, name, key)
    battery = _row(ae, "storage_units", "battery")
    assert battery["discharge_revenue_eur"] is None
    assert battery["charge_cost_eur"] is None
    assert battery["lcos_eur_per_mwh"] is None
    assert battery["discharge_mwh"] > 0  # physical quantities stand
    peak = _row(ae, "generators", "peak")
    assert peak["revenue_eur"] is None and peak["net_profit_eur"] is None
    assert peak["energy_mwh"] > 0
    link = _row(ae, "links", "electrolysis")
    assert link["lcoe_eur_per_mwh"] is None and link["input_cost_eur"] is None


def test_asset_economics_of_an_lp_is_unchanged(lp):
    ae = _assets(lp)
    assert "price_limit" not in ae
    battery = _row(ae, "storage_units", "battery")
    assert battery["charge_cost_eur"] > 0
    assert battery["lcos_eur_per_mwh"] is not None
    assert _row(ae, "generators", "peak")["revenue_eur"] > 0
    assert _assets(lp) == _assets(lp, None)


# ── carrier KPIs ─────────────────────────────────────────────────────────


def test_carrier_kpis_of_a_mip_withhold_revenue_and_market_value(mip, lp):
    from services.results.carrier_kpis import compute_carrier_kpis

    out = compute_carrier_kpis(mip, result_df=_rdf, price_limit=DUALS_NOT_MARGINAL)
    assert out["price_limit"] == DUALS_NOT_MARGINAL
    assert out["rows"]
    for row in out["rows"]:
        assert row["revenue_eur"] is None and row["market_value_eur_per_mwh"] is None
    assert any((row["energy_mwh"] or 0) > 0 for row in out["rows"])
    out_lp = compute_carrier_kpis(lp, result_df=_rdf)
    assert "price_limit" not in out_lp
    assert any((row["revenue_eur"] or 0) > 0 for row in out_lp["rows"])


# ── the live routes read the solve's own record (S3) ─────────────────────


def test_the_live_routes_gate_by_the_solve_s_record(mip, lp, monkeypatch):
    import routers.results as results
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(results, "_dispatch_ready", lambda n: True)
    for n, record, expect in (
        (mip, {"price_limit": DUALS_NOT_MARGINAL}, DUALS_NOT_MARGINAL),
        # The record wins over today's judgement of the network: an LP network
        # under a MIP solve's record is withheld (re-judging it would read None).
        (lp, {"price_limit": DUALS_NOT_MARGINAL}, DUALS_NOT_MARGINAL),
        # No record: the network is judged with the current config.
        (mip, {}, DUALS_NOT_MARGINAL),
        (lp, {}, None),
    ):
        monkeypatch.setattr(
            PyPSAService, "get_network", classmethod(lambda cls, n=n: n)
        )
        monkeypatch.setitem(results._state, "last_solve_settings", record)
        monkeypatch.setitem(results._state, "solver_config", CFG)
        ebc = results.get_economics_by_carrier()
        assert ebc.get("unavailable_reason") == expect
        ae = results.get_asset_economics()
        assert ae.get("price_limit") == expect
        kp = results.get_carrier_kpis()
        assert kp.get("price_limit") == expect


def test_a_price_independent_reading_note_survives_on_a_mip(mip, monkeypatch):
    """GK: only the notes that rest on the LP's duals go; any other note stands."""
    from services.results import economics_caveats

    monkeypatch.setattr(
        economics_caveats,
        "interior_optimum_notes",
        lambda n, names: [economics_caveats.ZERO_PROFIT_BY_CONSTRUCTION, "other"],
    )
    assert _assets(mip, DUALS_NOT_MARGINAL)["reading_notes"] == ["other"]
    assert _assets(mip)["reading_notes"] == [
        economics_caveats.ZERO_PROFIT_BY_CONSTRUCTION,
        "other",
    ]


# ── the study case: market revenue at duals (GK ruling c) ────────────────


def test_the_study_case_withholds_market_revenue_on_a_mip_record():
    """The run record's `price_limit` makes the case's market revenue None with the reason,
    and each operating year's excluded cash-flow column too. Year 0 is the build year for
    every case (no bill and no saving counted), so its 0.0 is a judged value."""
    from tests.test_engine_adapter_case_golden import OPTIONS, _bundle

    lp_values = [_assert_case_withholds_market_revenue(_bundle, o) for o in OPTIONS]
    assert any(v for v in lp_values)  # at least one case earns a market revenue


def _assert_case_withholds_market_revenue(_bundle, option):
    record = {
        "generators": [],
        "storage_units": [],
        "stores": [],
        "links": [],
        "price_limit": DUALS_NOT_MARGINAL,
    }
    view = _bundle(option, asset_economics=record).view
    mr = view.market_revenue_at_duals
    assert mr.annual_value is None and mr.unavailable == {
        "annual_value": DUALS_NOT_MARGINAL
    }
    y0, *rest = view.years
    assert y0.bill_option is None and y0.unavailable.get("bill_option") == "build_year"
    assert y0.savings == 0.0 and y0.market_revenue_at_duals == 0.0
    assert rest
    for y in rest:
        assert y.market_revenue_at_duals is None
        assert y.unavailable["market_revenue_at_duals"] == DUALS_NOT_MARGINAL
    lp_view = _bundle(option).view
    assert lp_view.market_revenue_at_duals.annual_value is not None
    assert all(y.market_revenue_at_duals is not None for y in lp_view.years)
    assert all("market_revenue_at_duals" not in y.unavailable for y in lp_view.years)
    # An LP's operating years carry the case's own market revenue, not a stand-in 0.0.
    lp_value = lp_view.market_revenue_at_duals.annual_value
    assert all(y.market_revenue_at_duals == lp_value for y in lp_view.years[1:])
    return lp_value


def test_the_study_case_s_live_frames_are_judged_by_the_network(mip, lp):
    """Without a run record, `option_case` reads the live frames under the network's
    own limit (`engine_adapter._econ_rows`): a MIP's rows carry the reason."""
    from services.study import engine_adapter

    econ, source = engine_adapter._econ_rows(mip, CFG, None)
    assert source == "live_frames" and econ["price_limit"] == DUALS_NOT_MARGINAL
    econ, source = engine_adapter._econ_rows(lp, CFG, None)
    assert source == "live_frames" and econ.get("price_limit") is None


@pytest.mark.parametrize("limit", [DUALS_NOT_MARGINAL, None])
def test_the_study_runner_records_the_option_s_price_limit(limit, monkeypatch):
    """`runner._read_option` judges the fork's solve, passes the limit to asset
    economics and copies it into the run record; an LP's record has no key."""
    import harness.solve_validity as sv
    from services.results import asset_economics as AE
    from services.study import packs
    from services.study import questions as Q
    from services.study import runner as R
    from tests.golden import site_fixture as sf
    from tests.u2_targets import bound_option

    seen = {}

    def fake(n, cfg, *, result_df=None, price_limit=None):
        seen["price_limit"] = price_limit
        out = {cls: [] for cls in R._ECON_CLASSES}
        out["capital_costs_available"] = True
        if price_limit:
            out["price_limit"] = price_limit
        return out

    monkeypatch.setattr(sv, "mip_price_limit", lambda n, cfg: limit)
    monkeypatch.setattr(AE, "compute_asset_economics", fake)
    ledger = sf.site_ledger()
    m, compiled = bound_option(sf.site_intake(), ledger, "none", sf.site_library())
    d = R._read_option(
        m,
        packs.option_solver_config(ledger, compiled),
        Q.option(Q.BESS_AT_SITE, "none"),
        "full_study",
        2020,
        compiled=compiled,
    )
    assert seen == {"price_limit": limit}
    assert d.get("price_limit") == limit
    assert d["asset_economics"].get("price_limit") == limit
    if limit is None:
        assert "price_limit" not in d and "price_limit" not in d["asset_economics"]


def test_the_chat_results_tool_carries_the_reason_on_a_mip(mip, monkeypatch):
    """S6: `get_results` hands the model the routes' payloads, so on a MIP the
    revenue it reads is None with the reason, never a 0.0 at the zero duals."""
    import routers.results as results
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(results, "_dispatch_ready", lambda n: True)
    monkeypatch.setattr(PyPSAService, "get_network", classmethod(lambda cls: mip))
    monkeypatch.setitem(
        results._state, "last_solve_settings", {"price_limit": DUALS_NOT_MARGINAL}
    )
    monkeypatch.setitem(results._state, "solver_config", CFG)
    ebc = chat_tools.get_results("economics_by_carrier")
    assert ebc["unavailable_reason"] == DUALS_NOT_MARGINAL
    assert all(b["revenue_meur"] is None for b in ebc["by_carrier"].values())
    ae = chat_tools.get_results("asset_economics")
    assert ae["price_limit"] == DUALS_NOT_MARGINAL
    assert all(row["revenue_eur"] is None for row in ae["generators"])
    kp = chat_tools.get_results("carrier_kpis")
    assert kp["price_limit"] == DUALS_NOT_MARGINAL
    assert all(row["revenue_eur"] is None for row in kp["rows"])


def test_a_mip_withholds_the_priced_keys_of_every_period_too():
    """`_withhold_priced` nulls the priced keys in each `by_period` entry, not only
    the row's totals; price-independent keys stay."""
    from services.results.asset_economics import PRICED_KEYS, _withhold_priced

    key = PRICED_KEYS["generators"][0]
    row = {
        "name": "g",
        key: 5.0,
        "energy_mwh": 3.0,
        "by_period": [{key: 2.0, "energy_mwh": 1.0}, {key: 3.0, "energy_mwh": 2.0}],
    }
    flat = {"name": "h", key: 1.0}  # a single-period row carries no by_period
    _withhold_priced([row, flat], [], [], [])
    assert flat[key] is None
    assert row[key] is None and row["energy_mwh"] == 3.0
    assert [e[key] for e in row["by_period"]] == [None, None]
    assert [e["energy_mwh"] for e in row["by_period"]] == [1.0, 2.0]


def test_a_case_with_no_economics_rows_is_not_read_as_withheld(lp, monkeypatch):
    """When the live frames answer no rows (`econ` None), the case reads market revenue
    as not established, never as a price-limit withholding, and does not fail."""
    from services.study import engine_adapter
    from tests.test_engine_adapter_case_golden import OPTIONS, _bundle

    mr = engine_adapter._market(lp, None)
    assert "annual_value" not in (mr.unavailable or {}) or (
        mr.unavailable["annual_value"] != DUALS_NOT_MARGINAL
    )
    monkeypatch.setattr(engine_adapter, "_econ_rows", lambda *a, **k: (None, None))
    view = _bundle(OPTIONS[0]).view
    assert view.market_revenue_at_duals.unavailable.get("annual_value") != (
        DUALS_NOT_MARGINAL
    )
    assert all("market_revenue_at_duals" not in y.unavailable for y in view.years)
