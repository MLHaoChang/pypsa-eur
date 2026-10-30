"""
AS-03 (`.scratch/asset-sizing-loop/issues/03-envelope-lp-floors-headroom.md`): the dynamic envelope becomes LP
floors and headroom on the fast-tier split storage, plus the backup reserve outside island mode and the genset
run-hour cap (`services/solver/envelope_floors.py`). Every expected value is hand-computed here as literals; each
of the six row families has a test that fails when that row alone is dropped. Limits are generic engineering,
"assumed" (SAGC-1).
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver import envelope_floors as EF
from services.solver.split_storage import _wrap_with_split_storage, add_split_storage

HOURS = 24

#: The prototype envelope (RESULTS.md): swing 30 MW, oscillation 10 MW at 2 s, checkpoint dip 20 MW for 30 s.
PROTOTYPE = {"phases": [{"swing_mw": 30.0, "checkpoint": {"depth_mw": 20.0, "duration_s": 30.0},
                         "oscillation": {"amplitude_mw": 10.0, "period_s": 2.0}}]}
RAMP = 10.0                                                     # MW/min
#: 2·(step + checkpoint) + oscillation, the SUM of the events, by hand.
E_FLOOR = 2 * (30**2 / (2 * 10 * 60) + min(20 * 30 / 3600, 20**2 / (2 * 10 * 60))) + 10 * 2 / (math.pi * 3600)


def _site(price) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(len(price)))
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=50.0)
    n.add("Generator", "import", bus="ac", p_nom=1e4, marginal_cost=pd.Series(np.asarray(price, float), n.snapshots))
    return n


def _flat() -> pypsa.Network:
    return _site([50.0] * HOURS)


def _peaks() -> pypsa.Network:
    """Two expensive hours in every six: arbitrage wants all the power it can get."""
    return _site([300.0 if t % 6 < 2 else 10.0 for t in range(HOURS)])


def _fast(n, **kw) -> None:
    add_split_storage(n, "fast", "ac", "high_c_bess", **{"power_capital_cost": 50.0, "energy_capital_cost": 20.0,
                                                          **kw})
    EF.tag_store(n, "fast", smoothing_tier=True)


def _solve(n, **floors) -> pypsa.Network:
    fn = EF._wrap_with_envelope_floors(n, _wrap_with_split_storage(n, None), SimpleNamespace(envelope_floors=floors))
    status, cond = n.optimize(solver_name="highs", extra_functionality=fn)
    assert status == "ok", cond
    return n


def _sol(n, var: str) -> float:
    return float(n.model.variables[var].solution.sum())


# ── the floors from the envelope ─────────────────────────────────────────────


def test_the_power_floor_is_s_plus_a_and_the_energy_floor_sums_the_events():
    f = EF.floors_from_envelope(PROTOTYPE, RAMP)
    assert f["power_floor_mw"] == pytest.approx(40.0)              # S + A, not S = 30
    assert E_FLOOR == pytest.approx(1.835, abs=5e-4)               # 2·(0.75 + 0.16667) + 0.00177
    assert f["energy_floor_mwh"] == pytest.approx(E_FLOOR, rel=1e-12)
    assert f["energy_floor_mwh"] > 1.8                              # not the max form, 2·0.75 = 1.5


def test_the_floors_are_the_max_over_the_phases():
    small = {"swing_mw": 10.0, "checkpoint": {}, "oscillation": {"amplitude_mw": 1.0, "period_s": 2.0}}
    f = EF.floors_from_envelope({"phases": [small, *PROTOTYPE["phases"]]}, RAMP)
    assert f == EF.floors_from_envelope(PROTOTYPE, RAMP)


def test_intent_a_backup_is_critical_load_times_four_hours():
    from models.design_intent import DesignIntentSpec

    assert EF.backup_mwh(DesignIntentSpec(intent="A"), 80.0) == pytest.approx(320.0)
    assert EF.backup_mwh(DesignIntentSpec(intent="D"), 80.0) == pytest.approx(20.0)     # 80 x 0.25 h
    assert EF.backup_mwh(DesignIntentSpec(intent="B"), 80.0) is None
    assert EF.backup_mwh(None, 80.0) is None


# ── one measured value per row family (the mutation tests reuse them) ────────


def _power_floor_mw() -> float:
    n = _flat()
    _fast(n)
    return _sol(_solve(n, power_floor_mw=40.0), "EnvelopeFloor-p_smooth")


def _energy_floor_mwh() -> float:
    n = _flat()
    _fast(n)
    return float(_solve(n, energy_floor_mwh=E_FLOOR).stores.at["fast", "e_nom_opt"])


def _peak_dispatch_mw() -> float:
    """60 MW fast store, 40 MW floor: the largest AC charge or discharge in any hour."""
    n = _peaks()
    _fast(n, p_nom=60.0)
    _solve(n, power_floor_mw=40.0)
    eta = float(n.links.at["fast discharger", "efficiency"])
    return max(float((eta * n.links_t.p0["fast discharger"]).max()), float(n.links_t.p0["fast charger"].max()))


def _windowed_energy_mwh() -> float:
    n = _flat()
    _fast(n)
    n.stores.loc["fast", ["e_min_pu", "e_max_pu"]] = [0.1, 0.9]
    return float(_solve(n, energy_floor_mwh=E_FLOOR).stores.at["fast", "e_nom_opt"])


def _backup_min_soc_mwh() -> float:
    n = _peaks()
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=50.0, energy_capital_cost=20.0)
    EF.tag_store(n, "bess", backup_reserve=True)
    return float(_solve(n, backup_mwh=320.0).stores_t.e["bess"].min())


def _spiked(year: bool = True) -> pypsa.Network:
    """200 snapshots, a 10 MW load, an import outage price spike in the first 150, a 10 MW genset at
    300 EUR/MWh. `year` weights the snapshots up to 8760 h (43.8 each), else 1 h each (a 200 h horizon)."""
    n = _site([1000.0 if t < 150 else 20.0 for t in range(200)])
    n.snapshot_weightings.loc[:, "generators"] = 8760 / 200 if year else 1.0
    n.loads.loc["load", "p_set"] = 10.0
    n.add("Generator", "genset", bus="ac", p_nom=10.0, marginal_cost=300.0)
    return n


def _genset_hours(cap: float | None = 100.0, year: bool = True) -> float:
    """The genset's weighted run hours, sum_t w_t p(t) / p_nom."""
    n = _spiked(year)
    EF.set_run_hours_cap(n, "genset", cap)
    _solve(n)
    return float((n.generators_t.p["genset"] * n.snapshot_weightings.generators).sum()) / 10.0


# ── each row, by hand ────────────────────────────────────────────────────────


def test_a_40_mw_floor_gives_40_mw_of_smoothing_power():
    assert _power_floor_mw() == pytest.approx(40.0, rel=1e-6)


def test_the_energy_floor_sizes_1_835_mwh_of_usable_energy():
    assert _energy_floor_mwh() == pytest.approx(E_FLOOR, rel=1e-6)


def test_arbitrage_never_takes_the_smoothing_headroom():
    assert _peak_dispatch_mw() == pytest.approx(60.0 - 40.0, abs=1e-5)


def test_the_soc_window_grows_the_store_by_one_over_the_window():
    assert _windowed_energy_mwh() == pytest.approx(E_FLOOR / 0.8, rel=1e-6)


def test_the_backup_reserve_applies_outside_island_mode_through_the_discharger():
    assert _backup_min_soc_mwh() == pytest.approx(320.0 / 0.96, rel=1e-6)


def test_a_100_h_run_hour_cap_binds_through_a_150_h_price_spike():
    assert _genset_hours(100.0) == pytest.approx(100.0, rel=1e-6)          # a full year: 100 h
    assert _genset_hours(None) == pytest.approx(150.0 * 8760 / 200, rel=1e-6)   # uncapped: the whole spike


def test_the_run_hour_cap_is_prorated_on_a_short_horizon():
    # 200 h of a year: 100 h/yr x 200 / 8760 = 2.2831 h of the 150 h spike.
    assert _genset_hours(100.0, year=False) == pytest.approx(100.0 * 200 / 8760, rel=1e-6)
    assert _genset_hours(None, year=False) == pytest.approx(150.0, rel=1e-6)


#: Each row family's own measurement and its pinned value.
MUTANTS = {
    "power_floor": (_power_floor_mw, 40.0),
    "energy_floor": (_energy_floor_mwh, E_FLOOR),
    "headroom": (_peak_dispatch_mw, 20.0),
    "soc_band": (_windowed_energy_mwh, E_FLOOR / 0.8),
    "backup": (_backup_min_soc_mwh, 320.0 / 0.96),
    "run_hours": (_genset_hours, 100.0),
}


@pytest.mark.parametrize("kind", sorted(MUTANTS))
def test_dropping_one_row_breaks_its_own_test(kind, monkeypatch):
    assert set(MUTANTS) == set(EF.ROWS)
    monkeypatch.setitem(EF.ROWS, kind, lambda m, ctx: 0)
    measure, pinned = MUTANTS[kind]
    assert measure() != pytest.approx(pinned, rel=1e-4)


# ── multi-period run hours ───────────────────────────────────────────────────


def test_the_run_hour_cap_holds_per_investment_period_with_generator_weightings():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(20))
    n.set_investment_periods([2030, 2040])
    n.snapshot_weightings.loc[:, "generators"] = 438.0               # 20 snapshots x 438 = 8760 h per period
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=20.0)                    # both gensets' caps bind in 2040
    price = pd.Series([1000.0 if t < 15 else 20.0 for t in range(20)] * 2, n.snapshots)  # 6570 h of spike
    n.add("Generator", "import", bus="ac", p_nom=1e4, marginal_cost=price)
    n.add("Generator", "genset", bus="ac", p_nom=10.0, marginal_cost=300.0, build_year=2030, lifetime=50)
    n.add("Generator", "late", bus="ac", p_nom=10.0, marginal_cost=300.0, build_year=2040, lifetime=50)
    EF.set_run_hours_cap(n, "genset", 100.0)
    EF.set_run_hours_cap(n, "late", 100.0)
    _solve(n)
    w = n.snapshot_weightings.generators
    for per in (2030, 2040):
        gen = (n.generators_t.p.loc[per, ["genset", "late"]].sum(axis=1) * w.loc[per]).sum()
        cap = 100.0 * (10.0 if per == 2030 else 20.0)
        assert gen == pytest.approx(cap, rel=1e-6)
    names = set(n.model.constraints)
    assert {"envelope_floors_run_hours-genset-2030", "envelope_floors_run_hours-genset-2040",
            "envelope_floors_run_hours-late-2040"} <= names
    assert "envelope_floors_run_hours-late-2030" not in names        # not active in 2030


# ── refusals, no-op, persistence ─────────────────────────────────────────────


def test_a_flagged_store_without_the_split_links_is_refused_before_the_solve():
    n = _flat()
    n.add("Bus", "s", carrier="x")
    n.add("Store", "plain", bus="s", e_nom_extendable=True)
    EF.tag_store(n, "plain", smoothing_tier=True)
    with pytest.raises(EF.EnvelopeFloorsError, match="'plain'.*smoothing_tier"):
        EF._wrap_with_envelope_floors(n, None, SimpleNamespace(envelope_floors=None))
    EF.tag_store(n, "plain", smoothing_tier=False, backup_reserve=True)
    with pytest.raises(EF.EnvelopeFloorsError, match="'plain'.*backup_reserve"):
        EF._wrap_with_envelope_floors(n, None, SimpleNamespace(envelope_floors=None))


def test_a_floor_with_no_flagged_store_is_refused():
    with pytest.raises(EF.EnvelopeFloorsError, match="no Store is flagged smoothing_tier"):
        EF._wrap_with_envelope_floors(_flat(), None, SimpleNamespace(envelope_floors={"power_floor_mw": 40.0}))
    with pytest.raises(EF.EnvelopeFloorsError, match="no Store is flagged backup_reserve"):
        EF._wrap_with_envelope_floors(_flat(), None, SimpleNamespace(envelope_floors={"backup_mwh": 320.0}))


def test_a_non_finite_floor_is_refused():
    with pytest.raises(EF.EnvelopeFloorsError, match="finite"):
        EF._wrap_with_envelope_floors(_flat(), None, SimpleNamespace(envelope_floors={"backup_mwh": math.inf}))


def test_the_wrapper_is_a_no_op_without_floors_or_flags():
    user = object()
    assert EF._wrap_with_envelope_floors(_flat(), user, SimpleNamespace(envelope_floors=None)) is user
    assert EF._wrap_with_envelope_floors(_flat(), user, SimpleNamespace()) is user
    assert EF._wrap_with_envelope_floors(_flat(), user, SimpleNamespace(envelope_floors={})) is user


def test_the_flags_and_cap_survive_a_netcdf_round_trip(tmp_path):
    from services.pypsa_service import PyPSAService

    n = _spiked()
    _fast(n)
    EF.set_run_hours_cap(n, "genset", 100.0)
    _solve(n, power_floor_mw=40.0)                                    # a solve must not break the columns
    path = tmp_path / "n.nc"
    PyPSAService.export_network_to_netcdf(n, path)
    back = pypsa.Network()
    PyPSAService.import_network_from_netcdf(back, path)
    assert EF._flagged(back.stores, "smoothing_tier") == ["fast"]
    assert EF._capped(back) == {"genset": 100.0}
    _solve(back, power_floor_mw=40.0)                                 # the reloaded network keeps its rows
    assert float(back.objective) == pytest.approx(float(n.objective), rel=1e-9)
    assert _sol(back, "EnvelopeFloor-p_smooth") == pytest.approx(40.0, rel=1e-6)


def test_run_simulation_applies_the_rows_with_island_mode_off():
    import queue
    import threading

    from services import solver_service
    from services.pypsa_service import PyPSAService

    n = _peaks()
    add_split_storage(n, "bess", "ac", "energy_bess", power_capital_cost=50.0, energy_capital_cost=20.0,
                      p_nom_max=200.0, e_nom_max=2000.0)
    EF.tag_store(n, "bess", backup_reserve=True)
    PyPSAService.set_network(n)
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    cfg = solver_service.SolverConfig(island_constraints=False, envelope_floors={"backup_mwh": 320.0})
    status, condition = solver_service.run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(), log_q,
                                                      state_update=lambda **kw: None)
    lines = []
    while not log_q.empty():
        lines.append(str(log_q.get()))
    assert status == "ok", (condition, [ln for ln in lines if "ERROR" in ln or "TRACEBACK" in ln])
    assert any("[ENVELOPE]" in ln and "backup 1" in ln for ln in lines)
    assert float(n.stores_t.e["bess"].min()) == pytest.approx(320.0 / 0.96, rel=1e-6)


# ── fingerprints ─────────────────────────────────────────────────────────────


def test_the_columns_enter_the_fingerprint_only_when_present(session_ctx, client, install_network):
    from services.workflow_tools import _network_input_fingerprint

    n = install_network(_spiked())
    ctx = session_ctx(client)
    before = (_network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True))
    EF.set_run_hours_cap(ctx.network, "genset", 100.0)
    after = (_network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True))
    assert after[0] != before[0] and after[1] != before[1]
    ctx.network.generators.drop(columns="run_hours_cap", inplace=True)
    assert (_network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True)) == before
    assert n is not None


def test_the_config_field_moves_the_fingerprint_and_the_investment_case_digest(
        client, install_network, session_ctx):
    from services.finance.investment_case_runner import assumptions_digest
    from services.workflow_tools import _network_input_fingerprint

    install_network(_flat())
    ctx = session_ctx(client)
    before = _network_input_fingerprint(ctx)
    r = client.put("/api/simulation/solver_config", json={"envelope_floors": {"power_floor_mw": 40.0}})
    assert r.status_code == 200, r.text
    assert r.json()["envelope_floors"]["power_floor_mw"] == 40.0
    assert _network_input_fingerprint(ctx) != before

    def digest(solver):
        return assumptions_digest(finance=None, commercial=None, dispatch=None, packs={}, solver=solver)

    plain, parts = digest({"envelope_floors": None})
    moved, moved_parts = digest({"envelope_floors": {"power_floor_mw": 40.0}})
    assert moved != plain and moved_parts["solver_config"] != parts["solver_config"]


@pytest.mark.parametrize("bad", [{"power_floor_mw": -1.0}, {"backup_mwh": "inf"}, {"energy_floor_mwh": "nan"},
                                 {"unknown_mw": 1.0}])
def test_the_api_refuses_a_negative_non_finite_or_unknown_floor(client, install_network, bad):
    install_network(_flat())
    r = client.put("/api/simulation/solver_config", json={"envelope_floors": bad})
    assert r.status_code == 422, r.text
