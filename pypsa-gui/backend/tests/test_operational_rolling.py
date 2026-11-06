"""DC grid readiness 07, slice S1: the operational run (rolling on fixed sizes).

`services/solver/operational.py` and its wiring in `run_simulation`. The
conditions (C1-C7) are GK A's plan verdict, recorded in
`.scratch/datacenter-grid-readiness/issues/07-rolling-operations.md`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver import operational as OP
from tests.test_fixed_sizes import _errors, _install, _plan_net, _solve, _warnings

TOL = 1e-5


def _flat_net(*, cyclic: bool = False) -> pypsa.Network:
    """`_plan_net` with the battery's cyclic flag as given and a starting
    charge, so a window-1 start and the carry between windows are visible."""
    n = _plan_net()
    n.storage_units["cyclic_state_of_charge"] = cyclic
    n.storage_units["state_of_charge_initial"] = 4.0
    return n


def _soc_balance_gaps(n, name: str = "bess") -> np.ndarray:
    """|soc(t) − soc(t−1)·(1−loss)^w − η_s·w·p_store + w·p_dispatch/η_d| at
    every t > 0, from the stitched results."""
    su = n.storage_units.loc[name]
    t = n.storage_units_t
    soc = t.state_of_charge[name].to_numpy()
    w = n.snapshot_weightings.stores.to_numpy()
    store = t.p_store[name].to_numpy()
    disp = t.p_dispatch[name].to_numpy()
    prev = soc[:-1] * (1 - su.standing_loss) ** w[1:]
    want = prev + su.efficiency_store * w[1:] * store[1:] - w[1:] * disp[1:] / su.efficiency_dispatch
    return np.abs(soc[1:] - want)


# ── refusals and labels ─────────────────────────────────────────────────────

def test_rolling_on_fixed_sizes_is_no_longer_refused(reset_backend):
    _install(_flat_net())
    errs = _errors(fixed_sizes="nameplate", solve_strategy="rolling")
    assert not {e for e in errs if e.startswith(("fixed_sizes_", "operational_"))}


def test_issues_are_empty_for_every_other_run():
    n = _flat_net()
    n.generators["run_hours_cap"] = np.nan
    n.generators.loc["gas", "run_hours_cap"] = 100.0
    for cfg in ({"solve_strategy": "rolling"}, {"fixed_sizes": "nameplate"}, {}):
        assert OP.issues(n, cfg) == []
    assert [c for _, c, _, _ in OP.issues(
        n, {"solve_strategy": "rolling", "fixed_sizes": "nameplate"})] == [
        "operational_rolling_run_hours"]


def test_a_run_hour_cap_is_refused_under_the_operational_run(reset_backend):
    n = _flat_net()
    n.generators["run_hours_cap"] = np.nan
    n.generators.loc["gas", "run_hours_cap"] = 100.0
    _install(n)
    assert "operational_rolling_run_hours" in _errors(
        fixed_sizes="nameplate", solve_strategy="rolling")
    assert "operational_rolling_run_hours" not in _errors(solve_strategy="rolling")


def test_user_code_is_warned_per_window(reset_backend):
    _install(_flat_net())
    code = "def extra_functionality(n, snapshots):\n    pass\n"
    assert "operational_rolling_user_code" in _warnings(
        fixed_sizes="nameplate", solve_strategy="rolling", extra_functionality_code=code)
    assert "operational_rolling_user_code" not in _warnings(
        solve_strategy="rolling", extra_functionality_code=code)


@pytest.mark.parametrize("cfg,sclopf,kind", [
    ({"multi_investment_periods": True}, False, "operational_rolling_multi_period"),
    ({}, True, "operational_rolling_sclopf"),
])
def test_a_full_horizon_fallback_is_refused_never_relabelled(cfg, sclopf, kind):
    op = {"solve_strategy": "rolling", "fixed_sizes": "nameplate", **cfg}
    with pytest.raises(OP.OperationalRefused) as exc:
        OP.fallback_refusal(op, sclopf=sclopf)
    assert exc.value.kind == kind
    OP.fallback_refusal({"solve_strategy": "rolling", **cfg}, sclopf=sclopf)   # plain: no raise


def test_a_multi_period_operational_run_is_refused_at_run_time(reset_backend, monkeypatch):
    """Past preflight (``rolling_with_multi_period``), the solver refuses
    instead of falling back to one full-horizon solve (C6)."""
    import services.solver_service as S

    _install(_plan_net(periods=(2030, 2040)))
    monkeypatch.setattr(S, "validate_for_run", lambda *a, **k: [])
    status, cond, _, _ = _solve(fixed_sizes="nameplate", solve_strategy="rolling",
                                multi_investment_periods=True)
    assert status == "error"
    assert "operational_rolling_multi_period" in cond


def test_still_sizeable_names_what_the_freeze_left_open():
    from services.adequacy.sweep import freeze_capacities

    n = _flat_net()
    assert set(OP.still_sizeable(n)) == {"Generator/pv", "Generator/gas", "StorageUnit/bess"}
    undo = freeze_capacities(n, source="nom")
    assert OP.still_sizeable(n) == []
    n.generators.loc["gas", "p_nom_max"] = n.generators.at["gas", "p_nom_min"] + 1.0
    assert OP.still_sizeable(n) == ["Generator/gas"]
    for lo, hi in ((5.0, np.inf), (np.nan, 5.0), (5.0, np.nan), (-np.inf, 5.0)):
        n.generators.loc["gas", ["p_nom_min", "p_nom_max"]] = [lo, hi]
        assert OP.still_sizeable(n) == ["Generator/gas"], (lo, hi)
    undo()


# ── windows (C2) and the cyclic flags (C1) ─────────────────────────────────

def test_windows_match_pypsas_cut():
    sns = pd.RangeIndex(24)
    assert OP.windows(sns, 8, 4)[:2] == [(0, 0, 8), (1, 4, 12)]
    assert OP.windows(sns, 8, 4)[-1] == (5, 20, 24)
    assert OP.windows(sns, 24, 0) == [(0, 0, 24)]
    assert OP.windows(sns, 1, 0)[-1] == (23, 23, 24)
    for h, o in ((8, 8), (8, 9), (8, -1), (0, 0)):
        with pytest.raises(ValueError, match="rolling_overlap"):
            OP.windows(sns, h, o)


def test_an_unset_sizing_source_is_not_operational():
    assert not OP.is_operational({"solve_strategy": "rolling", "fixed_sizes": None})
    assert OP.is_operational({"solve_strategy": "rolling", "fixed_sizes": "nameplate"})


def test_non_cyclic_turns_every_flag_off_and_restores_flags_and_initials():
    n = _flat_net(cyclic=True)
    n.storage_units["cyclic_state_of_charge_per_period"] = True
    n.add("Store", "tank", bus="site", e_nom=5.0, e_cyclic=True, e_initial=1.0)
    with OP.non_cyclic(n) as off:
        assert sorted(off) == ["StorageUnit/bess", "Store/tank"]
        assert not n.storage_units.cyclic_state_of_charge.any()
        assert not n.storage_units.cyclic_state_of_charge_per_period.any()
        assert not n.stores.e_cyclic.any() and not n.stores.e_cyclic_per_period.any()
        n.storage_units["state_of_charge_initial"] = 99.0
        n.stores["e_initial"] = 99.0
    assert n.storage_units.at["bess", "cyclic_state_of_charge"]
    assert n.storage_units.at["bess", "cyclic_state_of_charge_per_period"]
    assert n.stores.at["tank", "e_cyclic"]
    assert n.storage_units.at["bess", "state_of_charge_initial"] == 4.0
    assert n.stores.at["tank", "e_initial"] == 1.0


def test_a_failed_window_stops_the_run_and_reads_not_solved():
    """Window 2 of 3 cannot serve its load: the run stops there, windows 2
    and 3 read NaN, window 1 keeps its dispatch, and window 3 never runs."""
    from services.adequacy.sweep import freeze_capacities

    n = _flat_net()
    n.loads_t.p_set.iloc[12, 0] = 1e4
    freeze_capacities(n, source="nom", transient=False)
    with OP.non_cyclic(n), pytest.raises(OP.OperationalWindowFailed) as exc:
        OP.run_windows(n, horizon=8, overlap=0, solver_name="highs")
    assert exc.value.kind == "operational_window_failed"
    assert exc.value.window["index"] == 1
    gen = n.generators_t.p
    assert gen.iloc[:8].notna().all().all()
    assert gen.iloc[8:].isna().all().all()
    assert n.storage_units_t.state_of_charge.iloc[8:].isna().all().all()
    # Only solver outputs read not solved; the inputs stay as given.
    assert n.loads_t.p_set.iloc[8:].notna().all().all()
    assert n.generators_t.p_max_pu.iloc[8:].notna().all().all()


# ── through run_simulation (C1, C4, C5) ────────────────────────────────────

def _full_reference():
    """The full-horizon fixed-size dispatch on the same network, cyclic off:
    (generator dispatch, battery charge, opex)."""
    n = _install(_flat_net())
    status, cond, _, lines = _solve(fixed_sizes="nameplate")
    assert status in ("ok", "optimal"), (cond, lines[-5:])
    return (n.generators_t.p.copy(), n.storage_units_t.state_of_charge["bess"].copy(),
            float(n.statistics.opex(aggregate_time="sum").sum()))


def test_one_window_over_the_whole_horizon_equals_the_full_dispatch(reset_backend):
    """C5(i): H ≥ len(snapshots) is one window: the same dispatch and opex
    as the full-horizon fixed-size run, cyclic off on both sides."""
    ref_p, ref_soc, ref_opex = _full_reference()
    n = _install(_flat_net())
    status, cond, _, lines = _solve(fixed_sizes="nameplate", solve_strategy="rolling",
                                    rolling_horizon=48, rolling_overlap=0)
    assert status == "ok", (cond, lines[-5:])
    rec = n.meta[OP.META_KEY]
    assert len(rec["windows"]) == 1
    pd.testing.assert_frame_equal(n.generators_t.p, ref_p, atol=TOL)
    np.testing.assert_allclose(n.storage_units_t.state_of_charge["bess"], ref_soc, atol=TOL)
    assert rec["opex"] == pytest.approx(ref_opex, rel=1e-6)


def test_windows_with_overlap_carry_the_charge_and_price_the_stitched_dispatch(reset_backend):
    """C5(ii) and C4: H 8, O 4 over 24 h. The storage balance holds at every
    snapshot of the stitched results (so each window starts from the charge
    the previous one left), and the opex is the stitched dispatch priced
    once, not the window objectives summed."""
    n = _install(_flat_net())
    status, cond, _, lines = _solve(fixed_sizes="nameplate", solve_strategy="rolling",
                                    rolling_horizon=8, rolling_overlap=4)
    assert status == "ok", (cond, lines[-5:])
    rec = n.meta[OP.META_KEY]
    assert [w["index"] for w in rec["windows"]] == list(range(6))
    assert rec["label"] == "operational (rolling 8/4) on plan sizes"
    assert _soc_balance_gaps(n).max() < TOL
    first = n.storage_units_t.state_of_charge["bess"].iloc[0]
    su = n.storage_units.loc["bess"]
    p0 = n.storage_units_t
    assert first == pytest.approx(4.0 + su.efficiency_store * p0.p_store["bess"].iloc[0]
                                  - p0.p_dispatch["bess"].iloc[0] / su.efficiency_dispatch,
                                  abs=TOL)
    w = n.snapshot_weightings.generators
    by_hand = float((n.generators_t.p.mul(w, axis=0) * n.generators.marginal_cost).sum().sum())
    assert rec["opex"] == pytest.approx(by_hand, rel=1e-9)


def test_the_cyclic_flags_and_the_initial_charge_come_back(reset_backend):
    """C1: the run turns the cyclic flag off and says so, and the network
    keeps its own flag and starting charge afterwards."""
    n = _install(_flat_net(cyclic=True))
    status, cond, _, lines = _solve(fixed_sizes="nameplate", solve_strategy="rolling",
                                    rolling_horizon=8, rolling_overlap=4)
    assert status == "ok", (cond, lines[-5:])
    assert n.meta[OP.META_KEY]["cyclic_off"] == ["StorageUnit/bess"]
    assert any("[operational_cyclic_off]" in line for line in lines)
    assert n.storage_units.at["bess", "cyclic_state_of_charge"]
    assert n.storage_units.at["bess", "state_of_charge_initial"] == 4.0
    assert set(OP.still_sizeable(n)) == {"Generator/pv", "Generator/gas", "StorageUnit/bess"}


def test_a_failed_window_fails_the_run_through_run_simulation(reset_backend):
    n = _flat_net()
    n.loads_t.p_set.iloc[12, 0] = 1e4
    n = _install(n)
    status, cond, _, _ = _solve(fixed_sizes="nameplate", solve_strategy="rolling",
                                rolling_horizon=8, rolling_overlap=0)
    assert status != "ok"
    assert cond.startswith("operational_window_failed: window 2")
    assert OP.META_KEY not in n.meta
    assert n.generators_t.p.iloc[8:].isna().all().all()


def test_plain_rolling_takes_pypsas_loop_and_writes_no_record(reset_backend):
    n = _install(_flat_net())
    status, cond, _, _ = _solve(solve_strategy="rolling", rolling_horizon=8, rolling_overlap=4)
    assert status == "ok", cond
    assert OP.META_KEY not in n.meta


# ── staleness (C7) ─────────────────────────────────────────────────────────

def test_the_sizes_digest_keys_only_an_operational_capacity_run():
    n = _flat_net()
    n.meta["sizing_record"] = {"sha256": "abc"}
    op = {"solve_strategy": "rolling", "fixed_sizes": "capacity_run"}
    assert OP.sizes_digest(n, op) == "abc"
    assert OP.sizes_digest(n, {**op, "fixed_sizes": "nameplate"}) is None
    assert OP.sizes_digest(n, {**op, "solve_strategy": "full"}) is None
    n.meta.pop("sizing_record")
    assert OP.sizes_digest(n, op) == "missing"


def test_a_new_plan_makes_the_operational_fingerprint_move(reset_backend):
    from types import SimpleNamespace

    from services.solver_service import SolverConfig
    from services.workflow_tools import _network_input_fingerprint

    n = _install(_flat_net())
    n.meta["sizing_record"] = {"sha256": "plan-1"}

    def fp(**cfg):
        ctx = SimpleNamespace(network=n, solver_state={"solver_config": SolverConfig(**cfg)},
                              user_ts={}, storage_dir=None)
        return _network_input_fingerprint(ctx)

    op = {"solve_strategy": "rolling", "fixed_sizes": "capacity_run"}
    before, plain = fp(**op), fp(solve_strategy="rolling")
    n.meta["sizing_record"] = {"sha256": "plan-2"}
    assert fp(**op) != before
    assert fp(solve_strategy="rolling") == plain


# ── the DC-06 reserve floor in every window (C5 iii) ───────────────────────

def test_the_reserve_floor_holds_in_every_window():
    """The DC-06 rows run in each window: the backup floor holds at every
    snapshot on both sides (soc(t) and soc(t−1)), across window boundaries
    where soc(t−1) is the charge the previous window left."""
    from tests import test_reserve_products as T

    n = T._site(snaps=8)
    n.storage_units["cyclic_state_of_charge"] = False
    n.storage_units["state_of_charge_initial"] = 22.0
    up = T._product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
    T._bind(n, [up], {"afrr_up": np.arange(8.0) + 1})
    fn = T.RP._wrap_with_reserve_products(
        n, None, T._cfg([up]), backup=({T.STRING: 20.0}, frozenset({T.STRING})))
    with OP.non_cyclic(n):
        recs = OP.run_windows(n, horizon=3, overlap=1, solver_name="highs",
                              extra_functionality=fn, include_objective_constant=False)
    assert len(recs) == 4
    assert T.RP.persist_solution(n)
    frame, flags = T.RP.solved(n)
    assert flags == []
    soc = n.storage_units_t.state_of_charge[T.STRING]
    need = frame[f"{T.STRING}|afrr_up"]
    prev = soc.shift(1).fillna(22.0)
    assert (soc - need >= 20.0 - T.TOL).all()
    assert (prev - need >= 20.0 - T.TOL).all()
    assert need.max() > T.TOL                     # the rows earned something


def test_an_operational_run_dispatches_the_capacity_runs_sizes(reset_backend):
    """A sizing run, then the operational run on its recorded sizes: the
    windows dispatch within those sizes, the record keeps its digest and is
    not rewritten, and the bounds come back."""
    n = _install(_flat_net())
    status, cond, _, _ = _solve()
    assert status in ("ok", "optimal"), cond
    record = dict(n.meta["sizing_record"])
    plan = {**n.generators.p_nom_opt.to_dict(), **n.storage_units.p_nom_opt.to_dict()}
    status, cond, _, lines = _solve(fixed_sizes="capacity_run", solve_strategy="rolling",
                                    rolling_horizon=8, rolling_overlap=4)
    assert status == "ok", (cond, lines[-5:])
    assert n.meta["sizing_record"] == record
    rec = n.meta[OP.META_KEY]
    assert rec["sizes_source"] == "capacity_run" and rec["sizes_digest"] == record["sha256"]
    for g in ("pv", "gas"):
        assert (n.generators_t.p[g] <= plan[g] * n.generators_t.p_max_pu.get(g, 1.0) + TOL).all()
    assert (n.storage_units_t.p_dispatch["bess"] <= plan["bess"] + TOL).all()
    assert set(OP.still_sizeable(n)) == {"Generator/pv", "Generator/gas", "StorageUnit/bess"}
