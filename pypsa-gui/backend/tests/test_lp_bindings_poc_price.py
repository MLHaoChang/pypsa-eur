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
    terms = L.materialise_poc_prices(n, _commercial(_tariff()))
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


def test_materialisation_is_idempotent_and_keeps_the_static_cost():
    n = build_edge_15min()
    n.links.loc["import", "marginal_cost"] = 3.0
    L.materialise_poc_prices(n, _commercial(_tariff()))
    L.materialise_poc_prices(n, _commercial(_tariff()))
    mc = n.links_t.marginal_cost["import"].to_numpy()
    assert np.allclose(mc, _expected_eur_per_mwh(n.snapshots) + 3.0)


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
    terms = L.materialise_poc_prices(n, _commercial(_tariff(cost, rev), export_link="export"))
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

    tiered = _tou("tiered", tiers=[Tier(threshold=0, rate=0.1), Tier(threshold=100, rate=0.2)])
    fixed = TariffItem(id="standing", kind="fixed", unit="per_month",
                       periods=[TariffPeriod(name="all", rate=10.0)])
    n = build_edge_15min()
    terms = L.materialise_poc_prices(n, _commercial(_tariff(_tou(), tiered, fixed)))
    assert terms["energy_items"] == ["energy"]
    assert terms["not_in_lp"] == {"tiered": "tiers_WP1.5c", "standing": "fixed_not_in_lp"}


def test_no_commercial_config_is_a_no_op():
    n = build_edge_15min()
    assert L.materialise_poc_prices(n, None) is None
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
def test_after_a_solve_the_poc_price_is_persisted_on_the_network():
    n = _tariff_only_site()
    sink = _solve(n, _commercial(_tariff()))
    assert np.allclose(n.links_t.marginal_cost["import"], _expected_eur_per_mwh(n.snapshots))
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
    lp_cost = float((w * p0 * n.links_t.marginal_cost["import"].to_numpy()).sum())
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
    imp = float((w * n.links_t.p0["import"] * n.links_t.marginal_cost["import"]).sum())
    exp = float((w * n.links_t.p0["export"] * n.links_t.marginal_cost["export"]).sum())
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
        "poc_link": "import", "export_link": "export", "export_price_ref": ref}})
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
