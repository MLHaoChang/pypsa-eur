"""
Point-of-connection price binding (Edge Investment Case P1 WP1.3).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.3
Spec §5 (normative mapping) and §5.1 (where the bindings live).

Energy tariff items become a time-varying `marginal_cost` on the PoC import
Link. The export price and export items land on a second Link `poc→grid`, as
static cost − price. Both are written into `links_t.marginal_cost` before the
LP is built and PERSIST with the network, so the costs are reload-safe. The LP's
energy cost must equal `tariff_engine.rate(...)` on the same dispatch
(convex, same resolution ⇒ exact), and the objective gap stays 0.
"""
from __future__ import annotations

import queue
import threading
from datetime import date

import numpy as np
import pandas as pd
import pytest

from models.commercial import Tariff, TariffItem, TariffPeriod
from services.commercial import lp_bindings as L
from services.commercial.tariff_engine import rate
from tests.fixtures.investment_case.edge_15min import build_edge_15min

NIGHT, DAY, PEAK = 0.05, 0.20, 0.40  # €/kWh
_REF = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}


def _tou(item_id="energy", *, direction="cost", measured_on="import", tiers=None) -> TariffItem:
    return TariffItem(
        id=item_id, kind="energy", unit="per_kwh", measured_on=measured_on,
        direction=direction, tiers=tiers, periods=[
            TariffPeriod(name="night", rate=NIGHT, start_hour=0, end_hour=6),
            TariffPeriod(name="peak", rate=PEAK, start_hour=17, end_hour=21),
            TariffPeriod(name="day", rate=DAY),
        ])


def _tariff(*items) -> Tariff:
    return Tariff(id="t1", name="TOU", jurisdiction="DE", valid_from=date(2030, 1, 1),
                  items=list(items) or [_tou()])


def _commercial(tariff=None, **kw) -> dict:
    out = {"poc_link": "import"}
    if tariff is not None:
        out["import_tariff"] = tariff.model_dump(mode="json")
    out.update(kw)
    return out


def _expected_eur_per_mwh(idx, tz=None) -> np.ndarray:
    local = idx.tz_localize("UTC").tz_convert(tz) if tz else idx
    h = np.asarray(local.hour)
    r = np.where(h < 6, NIGHT, np.where((h >= 17) & (h < 21), PEAK, DAY))
    return r * 1000.0


# ── materialisation (no solve) ─────────────────────────────────────────────


def test_tou_rates_land_on_the_poc_link_in_eur_per_mwh():
    n = build_edge_15min()
    terms = L.materialise_poc_prices(n, _commercial(_tariff())).facts
    mc = n.links_t.marginal_cost["import"].to_numpy()
    assert np.allclose(mc, _expected_eur_per_mwh(n.snapshots))
    assert terms["poc_link"] == "import" and terms["energy_items"] == ["energy"]


def test_rates_follow_the_site_clock_when_snapshots_are_utc():
    n = build_edge_15min()
    L.materialise_poc_prices(n, _commercial(_tariff(), timezone="Europe/Berlin"))
    mc = n.links_t.marginal_cost["import"].to_numpy()
    assert np.allclose(mc, _expected_eur_per_mwh(n.snapshots, "Europe/Berlin"))
    # 16:00 UTC is 17:00 in Berlin in January: peak.
    at = n.snapshots.get_loc(pd.Timestamp("2030-01-07 16:00"))
    assert mc[at] == pytest.approx(PEAK * 1000)


def test_apply_adds_to_the_static_cost_and_undo_puts_it_back():
    n = build_edge_15min()
    n.links.loc["import", "marginal_cost"] = 3.0
    first = L.materialise_poc_prices(n, _commercial(_tariff()))
    assert np.allclose(n.links_t.marginal_cost["import"], _expected_eur_per_mwh(n.snapshots) + 3.0)
    first.undo()
    assert "import" not in n.links_t.marginal_cost.columns
    L.materialise_poc_prices(n, _commercial(_tariff()))  # a re-solve starts from the base again
    assert np.allclose(n.links_t.marginal_cost["import"], _expected_eur_per_mwh(n.snapshots) + 3.0)


def _with_export(n, price=None):
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC",
          marginal_cost=0.01)
    # The external grid absorbs what the site exports (the export revenue is
    # the export Link's negative cost, not the supply generator's).
    n.generators.loc["grid_supply", "p_min_pu"] = -1.0
    if price is not None:
        n.links_t[L.EXPORT_PRICE_ATTR] = pd.DataFrame(
            {"export": price}, index=n.snapshots)
    return n


def test_export_price_and_export_items_land_on_the_export_link():
    n = _with_export(build_edge_15min(), price=np.full(len(build_edge_15min().snapshots), 50.0))
    credit = TariffItem(id="feed_in_fee", kind="energy", unit="per_kwh", measured_on="export",
                        periods=[TariffPeriod(name="all", rate=0.002)])
    L.materialise_poc_prices(n, _commercial(_tariff(_tou(), credit), export_link="export",
                                            export_price_ref=_REF))
    assert np.allclose(n.links_t.marginal_cost["export"], 0.01 + 2.0 - 50.0)


def test_net_items_bill_import_when_cost_and_pay_export_when_revenue():
    n = _with_export(build_edge_15min(), price=np.zeros(672))
    cost = _tou("net_cost", measured_on="net")
    rev = TariffItem(id="net_rev", kind="energy", unit="per_kwh", measured_on="net",
                     direction="revenue", periods=[TariffPeriod(name="all", rate=0.03)])
    terms = L.materialise_poc_prices(n, _commercial(_tariff(cost, rev), export_link="export")).facts
    assert np.allclose(n.links_t.marginal_cost["import"], _expected_eur_per_mwh(n.snapshots))
    assert np.allclose(n.links_t.marginal_cost["export"], 0.01 - 30.0)
    assert "net_split_by_direction" in terms["notes"]


def test_a_missing_export_price_is_refused_not_zero():
    price = np.full(672, 50.0)
    price[10] = np.nan
    n = _with_export(build_edge_15min(), price=price)
    with pytest.raises(L.CommercialBindingError, match="export price"):
        L.materialise_poc_prices(n, _commercial(export_link="export", export_price_ref=_REF))


def test_an_unknown_poc_link_is_refused():
    with pytest.raises(L.CommercialBindingError, match="poc_link"):
        L.materialise_poc_prices(build_edge_15min(), {"poc_link": "nope"})


def test_tiered_and_non_energy_items_are_left_to_later_bindings_and_reported():
    from models.commercial import Tier

    # A windowed tiered item carries its rates per period (P2 WP2.1a-ii).
    tiered = TariffItem(
        id="tiered", kind="energy", unit="per_kwh",
        tiers=[Tier(threshold=0, rate=0.0), Tier(threshold=100, rate=0.0)],
        periods=[TariffPeriod(name="night", rate=0.0, start_hour=0, end_hour=6,
                              tier_rates=[0.1, 0.2]),
                 TariffPeriod(name="day", rate=0.0, tier_rates=[0.15, 0.25])])
    fixed = TariffItem(id="standing", kind="fixed", unit="per_month",
                       periods=[TariffPeriod(name="all", rate=10.0)])
    n = build_edge_15min()
    terms = L.materialise_poc_prices(n, _commercial(_tariff(_tou(), tiered, fixed))).facts
    assert terms["energy_items"] == ["energy"]
    # Windowed tiers are LP terms since WP2.1c-ii; fixed items never are.
    assert terms["tiered_items"] == ["tiered"]
    assert terms["not_in_lp"] == {"standing": "fixed_not_in_lp"}


def test_no_commercial_config_is_a_no_op():
    n = build_edge_15min()
    assert L.materialise_poc_prices(n, None).facts == {}
    assert "import" not in n.links_t.marginal_cost.columns


# ── through run_simulation (live solve) ────────────────────────────────────


def _solve(n, commercial):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    sink: dict = {}
    status, condition = run_simulation(
        SolverConfig(commercial=commercial), n, PyPSAService.get_lock(),
        threading.Event(), queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw))
    assert status in ("ok", "optimal"), (status, condition)
    return sink


def _tariff_only_site(pv=True):
    n = build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0  # every € of import is the tariff
    if not pv:
        n.remove("Generator", "pv")
    return n


@pytest.mark.live_solve
def test_after_a_solve_the_poc_price_is_persisted_and_the_users_cost_untouched():
    n = _tariff_only_site()
    sink = _solve(n, _commercial(_tariff()))
    assert np.allclose(n.links_t[L.ENERGY_PRICE_ATTR]["import"], _expected_eur_per_mwh(n.snapshots))
    assert "import" not in n.links_t.marginal_cost.columns  # applied for the solve, undone
    record = dict(n.meta[L.META_LINKS])
    assert record.pop("energy_hash")  # the drift check's content hash
    assert record.pop("agreement_recorded") is True  # this solve records its agreement (WP2.0)
    assert record.pop("hash_version") == 2  # the recipe of energy_hash (WP2.0 condition 1)
    assert record.pop("lp_recipe") == L.LP_RECIPE  # the LP recipe that solved (WP2.1c)
    assert record == {"import": "import", "export": None,
                      "import_members": ["import"], "priced": ["import"]}
    assert sink["last_commercial_terms"]["poc_link"] == "import"


@pytest.mark.live_solve
def test_the_bess_charges_in_the_cheapest_window():
    n = _tariff_only_site(pv=False)
    _solve(n, _commercial(_tariff()))
    charge = (-n.storage_units_t.p["bess"]).clip(lower=0.0)
    night = np.asarray(n.snapshots.hour) < 6
    assert charge.sum() > 0
    assert charge[night].sum() >= 0.9 * charge.sum()


@pytest.mark.live_solve
def test_lp_import_cost_equals_the_tariff_engine_energy_total():
    n = _tariff_only_site()
    _solve(n, _commercial(_tariff()))
    w = n.snapshot_weightings.objective.to_numpy()
    p0 = n.links_t.p0["import"].to_numpy()
    lp_cost = float((w * p0 * n.links_t[L.ENERGY_PRICE_ATTR]["import"].to_numpy()).sum())
    dispatch = pd.DataFrame({"import_mw": p0, "export_mw": 0.0}, index=n.snapshots)
    billed = rate(dispatch, _tariff(), step_hours=0.25, timezone=None)
    assert billed.per_item["energy"] == pytest.approx(lp_cost, rel=1e-6)


@pytest.mark.live_solve
@pytest.mark.parametrize("price,exports", [(50.0, True), (0.0, False)])
def test_pv_exports_when_the_price_pays_and_not_when_it_does_not(price, exports):
    n = _tariff_only_site()
    n.generators.loc["pv", "p_nom"] = 120.0
    _with_export(n, price=np.full(len(n.snapshots), price))
    _solve(n, _commercial(_tariff(), export_link="export", export_price_ref=_REF))
    exported = float((n.links_t.p0["export"] * n.snapshot_weightings.objective).sum())
    assert (exported > 1.0) is exports, exported


@pytest.mark.live_solve
def test_objective_gap_is_zero_and_energy_rows_match_the_lp():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition
    from services.solver_service import SolverConfig

    n = _tariff_only_site()
    _with_export(n, price=np.full(len(n.snapshots), 50.0))
    n.generators.loc["pv", "p_nom"] = 120.0
    commercial = _commercial(_tariff(), export_link="export", export_price_ref=_REF)
    _solve(n, commercial)
    cb = compute_cost_breakdown(n, SolverConfig(commercial=commercial))
    dec = compute_objective_decomposition(n, cb)
    assert abs(dec["gap_pct"]) < 1e-6, dec
    w = n.snapshot_weightings.objective
    price = n.links_t[L.ENERGY_PRICE_ATTR]
    imp = float((w * n.links_t.p0["import"] * price["import"]).sum())
    exp = float((w * n.links_t.p0["export"] * price["export"]).sum())
    rows = cb["commercial"]
    assert rows["energy_import"] == pytest.approx(imp, rel=1e-9)
    assert rows["energy_export"] == pytest.approx(exp, rel=1e-9)
    assert rows["included_in_total"] is True


# ── typed config at the API boundary ───────────────────────────────────────


def test_config_route_refuses_a_poc_link_that_is_not_a_link(client, install_network):
    install_network(build_edge_15min())
    r = client.put("/api/simulation/solver_config", json={"commercial": {"poc_link": "pv"}})
    assert r.status_code == 422, r.text


def test_config_route_round_trips_the_commercial_block(client, install_network, session_ctx):
    from dataclasses import asdict

    from routers.projects import _solver_config_from_dict

    install_network(build_edge_15min())
    body = {"commercial": {"poc_link": "import",
                           "import_tariff": _tariff().model_dump(mode="json"),
                           "timezone": "Europe/Berlin"}}
    r = client.put("/api/simulation/solver_config", json=body)
    assert r.status_code == 200, r.text
    cfg = session_ctx(client).solver_state["solver_config"]
    assert cfg.commercial["poc_link"] == "import"
    again = _solver_config_from_dict(asdict(cfg))
    assert again.commercial == cfg.commercial
    # Clearing it is an explicit null.
    r = client.put("/api/simulation/solver_config", json={"commercial": None})
    assert r.status_code == 200 and r.json()["commercial"] is None


def test_config_route_materialises_a_library_export_price(client, install_network,
                                                          session_ctx):
    n = _with_export(build_edge_15min())
    install_network(n)
    idx = n.snapshots.tz_localize("UTC")
    ref = client.post("/api/library/series", json={
        "name": "da_price", "timestamps": [t.isoformat() for t in idx],
        "values": [float(i % 96) for i in range(len(idx))], "meta": {"source": "t"}}).json()
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "export_link": "export", "export_price_ref": ref,
        "timezone": "UTC"}})
    assert r.status_code == 200, r.text
    live = session_ctx(client).network
    got = live.links_t[L.EXPORT_PRICE_ATTR]["export"].to_numpy()
    assert np.allclose(got, [float(i % 96) for i in range(len(idx))])


def test_an_unresolvable_export_ref_is_refused_at_the_route(client, install_network):
    install_network(_with_export(build_edge_15min()))
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "export_link": "export",
        "export_price_ref": {"id": "ghost", "version": 1, "hash": "b" * 64, "source": "t"}}})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "library_ref_stale"


def test_a_bare_library_tariff_id_is_refused_in_p1():
    with pytest.raises(L.CommercialBindingError, match="P2"):
        L.materialise_poc_prices(build_edge_15min(),
                                 {"poc_link": "import", "import_tariff_id": "de_tou"})


# ── Review round 1 (FAIL) — each finding pinned before its fix ─────────────


def _plain_objective(n_builder):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    n = n_builder()
    PyPSAService.set_network(n)
    run_simulation(SolverConfig(), n, PyPSAService.get_lock(), threading.Event(),
                   queue.SimpleQueue(), state_update=lambda **kw: None)
    return float(n.objective)


def _solve_cfg(n, commercial):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    sink: dict = {}
    status, condition = run_simulation(
        SolverConfig(commercial=commercial), n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw))
    return status, condition, sink


@pytest.mark.live_solve
def test_clearing_the_commercial_config_restores_the_plain_solve():
    """#1: the materialised column must not outlive the config."""
    n = _tariff_only_site()
    assert _solve_cfg(n, _commercial(_tariff()))[0] in ("ok", "optimal")
    assert _solve_cfg(n, None)[0] in ("ok", "optimal")
    assert float(n.objective) == pytest.approx(_plain_objective(_tariff_only_site), rel=1e-9)
    assert "import" not in n.links_t.marginal_cost.columns
    # The plain solve's commit cleared the previous config's price frame.
    assert L.META_LINKS not in n.meta


def test_repointing_poc_link_restores_the_old_link():
    """#1: the previously priced Link goes back to its base cost."""
    n = build_edge_15min()
    n.add("Link", "import2", bus0="grid", bus1="poc", p_nom=80.0, carrier="AC")
    L.materialise_poc_prices(n, _commercial(_tariff())).undo()
    L.materialise_poc_prices(n, {"poc_link": "import2",
                                 "import_tariff": _tariff().model_dump(mode="json")})
    assert "import" not in n.links_t.marginal_cost.columns
    assert np.allclose(n.links_t.marginal_cost["import2"], _expected_eur_per_mwh(n.snapshots))


def test_dropping_export_items_restores_the_export_link():
    n = _with_export(build_edge_15min(), price=np.zeros(672))
    credit = TariffItem(id="fee", kind="energy", unit="per_kwh", measured_on="export",
                        periods=[TariffPeriod(name="all", rate=0.002)])
    first = L.materialise_poc_prices(n, _commercial(_tariff(_tou(), credit), export_link="export"))
    assert "export" in n.links_t.marginal_cost.columns
    first.undo()
    L.materialise_poc_prices(n, _commercial(_tariff(), export_link="export"))
    assert "export" not in n.links_t.marginal_cost.columns


def test_an_uploaded_price_series_on_the_poc_link_is_the_base():
    """#2: an indexed price uploaded on the import Link is kept, the tariff added on top."""
    n = build_edge_15min()
    spot = np.linspace(10.0, 100.0, len(n.snapshots))
    n.links_t.marginal_cost["import"] = spot
    applied = L.materialise_poc_prices(n, _commercial(_tariff()))
    assert np.allclose(n.links_t.marginal_cost["import"], spot + _expected_eur_per_mwh(n.snapshots))
    applied.undo()
    assert np.allclose(n.links_t.marginal_cost["import"], spot)


def test_a_newly_uploaded_base_replaces_the_remembered_one():
    """#2: `_reapply_ts` rewriting the column between solves is the new base."""
    n = build_edge_15min()
    L.materialise_poc_prices(n, _commercial(_tariff())).undo()
    new_spot = np.full(len(n.snapshots), 7.0)
    n.links_t.marginal_cost["import"] = new_spot
    L.materialise_poc_prices(n, _commercial(_tariff()))
    assert np.allclose(n.links_t.marginal_cost["import"], 7.0 + _expected_eur_per_mwh(n.snapshots))


@pytest.mark.parametrize("dynamic", [False, True])
def test_a_two_way_poc_link_is_refused(dynamic):
    """#3: reverse flow on the import Link would be paid the import tariff."""
    n = build_edge_15min()
    if dynamic:
        n.links_t.p_min_pu["import"] = -0.5
    else:
        n.links.loc["import", "p_min_pu"] = -1.0
    with pytest.raises(L.CommercialBindingError, match="p_min_pu"):
        L.materialise_poc_prices(n, _commercial(_tariff()))


def test_snapshots_where_export_pays_more_than_import_costs_are_flagged():
    """#4: simultaneous import/export would lower cost; say so."""
    n = _with_export(build_edge_15min(), price=np.full(672, 300.0))
    terms = L.materialise_poc_prices(n, _commercial(_tariff(), export_link="export",
                                                    export_price_ref=_REF)).facts
    # Export pays 300 €/MWh; import costs 50/200/400 — every snapshot outside
    # the 400 €/MWh evening peak (4 h × 4 × 7 days = 112) is a circulation risk.
    assert terms["simultaneous_flow_risk_snapshots"] == 672 - 112


@pytest.mark.live_solve
def test_simultaneous_import_and_export_is_flagged_in_the_rows():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.solver_service import SolverConfig

    n = _tariff_only_site()
    _with_export(n, price=np.full(len(n.snapshots), 300.0))
    commercial = _commercial(_tariff(), export_link="export", export_price_ref=_REF)
    assert _solve_cfg(n, commercial)[0] in ("ok", "optimal")
    rows = compute_cost_breakdown(n, SolverConfig(commercial=commercial))["commercial"]
    assert "simultaneous_import_export" in rows["flags"]


def test_align_averages_a_finer_series():
    """#10: a 15-min price on hourly snapshots is the hour's mean, not its :00 sample."""
    idx = pd.date_range("2030-01-01", periods=8, freq="15min")
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], index=idx)
    out = L.align_to_snapshots(s, pd.date_range("2030-01-01", periods=2, freq="h"))
    assert out.tolist() == [2.5, 6.5]


def test_an_export_price_aligned_to_another_axis_is_refused():
    """#10: resampling the snapshots invalidates the materialised price."""
    n = _with_export(build_edge_15min())
    L.write_export_price(n, "export", pd.Series(50.0, index=n.snapshots))
    n.set_snapshots(n.snapshots[::4])  # resample to hourly points: no NaN appears
    with pytest.raises(L.CommercialBindingError, match="re-apply"):
        L.materialise_poc_prices(n, _commercial(export_link="export", export_price_ref=_REF))


def test_rows_follow_the_links_the_last_solve_priced():
    """#7: after re-pointing poc_link without a re-solve, rows are flagged, not relabelled."""
    n = build_edge_15min()
    n.add("Link", "import2", bus0="grid", bus1="poc", p_nom=80.0, carrier="AC")
    applied = L.materialise_poc_prices(n, _commercial(_tariff()))
    applied.commit()
    applied.undo()
    n.links_t.p0 = pd.DataFrame({"import": 1.0, "import2": 0.0}, index=n.snapshots)
    rows = L.energy_cost_rows(n, {"poc_link": "import2"})
    assert rows["energy_import"] is not None  # still the solved Link's cost
    assert "config_changed_since_solve" in rows["flags"]


# ── route (review #3, #5, #6, #8, #9) ──────────────────────────────────────


def _put_cfg(client, commercial):
    return client.put("/api/simulation/solver_config", json={"commercial": commercial})


def test_route_refuses_a_two_way_poc_link_with_a_code(client, install_network):
    n = build_edge_15min()
    n.links.loc["import", "p_min_pu"] = -1.0
    install_network(n)
    r = _put_cfg(client, {"poc_link": "import"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "commercial_binding_invalid"


def test_route_refuses_an_export_item_without_an_export_link(client, install_network):
    install_network(build_edge_15min())
    credit = TariffItem(id="fee", kind="energy", unit="per_kwh", measured_on="export",
                        periods=[TariffPeriod(name="all", rate=0.002)])
    r = _put_cfg(client, _commercial(_tariff(_tou(), credit)))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "commercial_binding_invalid"


def test_route_refuses_periods_that_leave_snapshots_unrated(client, install_network):
    install_network(build_edge_15min())
    gappy = TariffItem(id="gappy", kind="energy", unit="per_kwh",
                       periods=[TariffPeriod(name="night", rate=0.1, start_hour=0, end_hour=6)])
    r = _put_cfg(client, _commercial(_tariff(gappy)))
    assert r.status_code == 422


def test_route_needs_a_timezone_for_a_zoned_series(client, install_network):
    """#5: a tz-aware Library series on a site-clock axis with no zone is ambiguous."""
    n = _with_export(build_edge_15min())
    install_network(n)
    idx = n.snapshots.tz_localize("Europe/Berlin")
    ref = client.post("/api/library/series", json={
        "name": "berlin", "timestamps": [t.isoformat() for t in idx],
        "values": [1.0] * len(idx), "meta": {"source": "t"}}).json()
    r = _put_cfg(client, {"poc_link": "import", "export_link": "export", "export_price_ref": ref})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "timezone_required"


def test_route_aligns_a_zoned_series_on_utc_snapshots(client, install_network, session_ctx):
    """#5: with `timezone` set, snapshots are UTC; a Berlin 12:00 price lands at 11:00."""
    n = _with_export(build_edge_15min())
    install_network(n)
    local = pd.DatetimeIndex(n.snapshots).tz_localize("UTC").tz_convert("Europe/Berlin")
    vals = [float(t.hour) for t in local]  # price = local hour
    ref = client.post("/api/library/series", json={
        "name": "berlin2", "timestamps": [t.isoformat() for t in local],
        "values": vals, "meta": {"source": "t"}}).json()
    r = _put_cfg(client, {"poc_link": "import", "export_link": "export",
                          "export_price_ref": ref, "timezone": "Europe/Berlin"})
    assert r.status_code == 200, r.text
    got = session_ctx(client).network.links_t[L.EXPORT_PRICE_ATTR]["export"]
    assert got.loc[pd.Timestamp("2030-01-07 11:00")] == 12.0


def test_route_coverage_refusal_leaves_the_previous_price_intact(client, install_network,
                                                                 session_ctx):
    """#6: align first, write only when fully covered."""
    n = _with_export(build_edge_15min())
    install_network(n)
    full = n.snapshots.tz_localize("UTC")
    ok = client.post("/api/library/series", json={
        "name": "full", "timestamps": [t.isoformat() for t in full],
        "values": [5.0] * len(full), "meta": {"source": "t"}}).json()
    assert _put_cfg(client, {"poc_link": "import", "export_link": "export",
                             "export_price_ref": ok, "timezone": "UTC"}).status_code == 200
    short = full[:10]
    bad = client.post("/api/library/series", json={
        "name": "short", "timestamps": [t.isoformat() for t in short],
        "values": [9.0] * 10, "meta": {"source": "t"}}).json()
    r = _put_cfg(client, {"poc_link": "import", "export_link": "export", "export_price_ref": bad,
                          "timezone": "UTC"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "export_price_coverage"
    col = session_ctx(client).network.links_t[L.EXPORT_PRICE_ATTR]["export"]
    assert not col.isna().any() and (col == 5.0).all()



# ── Review round 2 (FAIL) — the tariff never lives in the user's cost ──────


def test_an_uncommitted_apply_leaves_no_price_frame():
    """#5: a failed solve undoes without commit; no rows from a mixed run."""
    n = build_edge_15min()
    L.materialise_poc_prices(n, _commercial(_tariff())).undo()
    assert L.META_LINKS not in n.meta
    assert n.links_t.get(L.ENERGY_PRICE_ATTR) is None or \
        "import" not in n.links_t[L.ENERGY_PRICE_ATTR].columns


@pytest.mark.live_solve
def test_editing_the_poc_link_between_solves_does_not_double_the_tariff(client, install_network,
                                                                       session_ctx):
    """#1: a Properties edit (remove + add) must not adopt a tariffed column."""
    n = _tariff_only_site()
    n.links.loc["import", "marginal_cost"] = 3.0
    install_network(n)
    body = {"commercial": _commercial(_tariff())}
    assert client.put("/api/simulation/solver_config", json=body).status_code == 200
    live = session_ctx(client).network
    assert _solve_cfg(live, _commercial(_tariff()))[0] in ("ok", "optimal")
    r = client.put("/api/network/links/import",
                   json={"name": "import", "bus0": "grid", "bus1": "poc", "p_nom": 90.0,
                         "marginal_cost": 3.0, "carrier": "AC"})
    assert r.status_code == 200, r.text
    live = session_ctx(client).network
    assert "import" not in live.links_t.marginal_cost.columns
    assert _solve_cfg(live, _commercial(_tariff()))[0] in ("ok", "optimal")
    assert np.allclose(live.links_t[L.ENERGY_PRICE_ATTR]["import"],
                       _expected_eur_per_mwh(live.snapshots))


@pytest.mark.live_solve
def test_changing_the_snapshot_axis_between_solves_does_not_double_the_tariff():
    """#2: extend the axis; the second solve prices from the base again."""
    n = _tariff_only_site()
    assert _solve_cfg(n, _commercial(_tariff()))[0] in ("ok", "optimal")
    longer = pd.date_range(n.snapshots[0], periods=len(n.snapshots) + 96, freq="15min")
    n.set_snapshots(longer)
    n.snapshot_weightings.loc[:, :] = 0.25
    n.loads_t.p_set = n.loads_t.p_set.ffill()
    n.generators_t.p_max_pu = n.generators_t.p_max_pu.fillna(0.0)
    assert _solve_cfg(n, _commercial(_tariff()))[0] in ("ok", "optimal")
    assert "import" not in n.links_t.marginal_cost.columns
    assert np.allclose(n.links_t[L.ENERGY_PRICE_ATTR]["import"], _expected_eur_per_mwh(n.snapshots))


def test_averaging_does_not_run_across_a_gap():
    """#3: the last snapshot before a gap averages one step, not the gap."""
    fine = pd.Series(np.arange(24 * 4 * 40, dtype=float),
                     index=pd.date_range("2030-01-01", periods=24 * 4 * 40, freq="15min"))
    snaps = pd.DatetimeIndex(list(pd.date_range("2030-01-01", periods=24, freq="h"))
                             + list(pd.date_range("2030-02-01", periods=24, freq="h")))
    out = L.align_to_snapshots(fine, snaps)
    at = snaps.get_loc(pd.Timestamp("2030-01-01 23:00"))
    assert out.iloc[at] == pytest.approx(fine.loc["2030-01-01 23:00":"2030-01-01 23:45"].mean())


def test_ic_frames_are_not_listed_as_user_time_series(client, install_network):
    """#4: internal frames never reach the Time-Series tab or its GET."""
    n = _with_export(build_edge_15min(), price=np.full(672, 5.0))
    n.links_t[L.ENERGY_PRICE_ATTR] = pd.DataFrame({"import": 1.0}, index=n.snapshots)
    install_network(n)
    listed = {(e["component"], e["attribute"]) for e in client.get("/api/network/timeseries").json()}
    assert not [a for c, a in listed if a.startswith("ic_")]
    assert client.get(f"/api/network/timeseries/links/{L.EXPORT_PRICE_ATTR}").status_code == 404
    assert client.put(f"/api/network/timeseries/links/{L.EXPORT_PRICE_ATTR}",
                      json={}).status_code == 404


# ── Review round 3 (PASS WITH CONDITIONS) ──────────────────────────────────


def test_a_priced_link_that_lost_its_record_is_not_established_not_zero():
    """#1 ADR-0001: only a Link the solve deliberately left unpriced is 0.0."""
    n = build_edge_15min()
    applied = L.materialise_poc_prices(n, _commercial(_tariff()))
    applied.commit()
    applied.undo()
    n.links_t.p0 = pd.DataFrame({"import": 1.0}, index=n.snapshots)
    n.links_t[L.ENERGY_PRICE_ATTR] = pd.DataFrame(index=n.snapshots)  # the column is gone
    rows = L.energy_cost_rows(n, _commercial(_tariff()))
    assert rows["energy_import"] is None
    assert "energy_import_not_established" in rows["flags"]


def test_the_block_is_weighted_like_the_component_table():
    """#2: `commercial.energy_import` == Σ Commercial energy items × years."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = build_edge_15min()
    applied = L.materialise_poc_prices(n, _commercial(_tariff()))
    applied.commit()
    applied.undo()
    n.links_t.p0 = pd.DataFrame({"import": 1.0}, index=n.snapshots)
    terms = commercial_cost_terms(n, _commercial(_tariff()), years=lambda p: 3.0)
    items = sum(ox for label, _, _, ox in terms["items"] if label == "energy_import")
    assert terms["block"]["energy_import"] == pytest.approx(items * 1.0)  # flat: period None → ×1


def test_the_upload_route_refuses_ic_attributes(client, install_network):
    """#3: an upload must not spoof the pinned Library price."""
    install_network(build_edge_15min())
    r = client.post(f"/api/network/timeseries/upload?component=links&attribute={L.EXPORT_PRICE_ATTR}",
                    files={"file": ("p.csv", b"snapshot,export\n2030-01-07 00:00,1\n", "text/csv")})
    assert r.status_code == 404


def test_vintage_bounds_on_the_poc_link_are_refused():
    """#5: per-period vintage clones would carry dispatch the rows do not read."""
    n = build_edge_15min()
    n.meta["vintage_bounds"] = {"Link": {"import": {"2030": {"p_nom_max": 50.0}}}}
    with pytest.raises(L.CommercialBindingError, match="vintage"):
        L.materialise_poc_prices(n, _commercial(_tariff()))


# ── Phase 1 gate binding condition 2 ───────────────────────────────────────


def _committed(n, commercial):
    applied = L.materialise_poc_prices(n, commercial)
    applied.undo()
    applied.commit()


def _drift_flags(n, commercial):
    from services.commercial.cost_rows import commercial_cost_terms

    return commercial_cost_terms(n, commercial)["flags"]


def _tou_tariff(rate_night=0.07):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2030-01-01",
            "items": [{"id": "e", "kind": "energy", "unit": "per_kwh", "periods": [
                {"name": "night", "rate": rate_night, "start_hour": 0, "end_hour": 6},
                {"name": "day", "rate": 0.2}]}]}


def test_a_changed_energy_rate_after_the_solve_is_drift():
    """Gate finding #1: energy rates are hashed like demand and tier items."""
    n = build_edge_15min()
    base = {"poc_link": "import", "import_tariff": _tou_tariff()}
    _committed(n, base)
    assert "config_changed_since_solve" not in _drift_flags(n, base)
    assert "config_changed_since_solve" in _drift_flags(n, {**base, "import_tariff": _tou_tariff(0.09)})
    assert "config_changed_since_solve" in _drift_flags(n, {**base, "timezone": "Europe/Berlin"})


def test_a_changed_export_price_ref_after_the_solve_is_drift():
    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    n.links_t[L.EXPORT_PRICE_ATTR] = pd.DataFrame({"export": np.full(len(n.snapshots), 40.0)},
                                                  index=n.snapshots)
    ref = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}
    base = {"poc_link": "import", "export_link": "export", "export_price_ref": ref,
            "import_tariff": _tou_tariff()}
    _committed(n, base)
    assert "config_changed_since_solve" not in _drift_flags(n, base)
    newer = {**base, "export_price_ref": {**ref, "version": 2, "hash": "b" * 64}}
    assert "config_changed_since_solve" in _drift_flags(n, newer)
