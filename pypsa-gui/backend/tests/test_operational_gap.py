"""DC 07 S3: the plan-vs-operation gap (``services/solver/operational_gap.py``)
and its read route. Conditions C12–C14 and C22–C28 in
``.scratch/datacenter-grid-readiness/issues/07-rolling-operations.md``."""

from __future__ import annotations

import copy
import dataclasses
import functools

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.solver import operational as OP
from services.solver import operational_gap as G
from tests.test_fixed_sizes import _install, _solve

TOL = 1e-5
HOURS = 24
#: Cheap early, dear late: a window that cannot see the dear hours never
#: charges, so the operation costs more than the full-horizon dispatch.
PRICE = np.array([10.0] * 4 + [50.0] * 16 + [100.0] * 4)


def _net(*, storage: bool = True, init: float = 5.0) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.add("Carrier", ["AC", "grid", "battery"])
    n.add("Bus", "site", carrier="AC")
    n.add("Load", "load", bus="site", p_set=pd.Series(10.0, index=n.snapshots))
    n.add(
        "Generator",
        "grid",
        bus="site",
        carrier="grid",
        p_nom=50.0,
        marginal_cost=pd.Series(PRICE, index=n.snapshots),
    )
    if storage:
        n.add(
            "StorageUnit",
            "bess",
            bus="site",
            carrier="battery",
            p_nom=10.0,
            max_hours=4.0,
            efficiency_store=0.95,
            efficiency_dispatch=0.95,
            cyclic_state_of_charge=True,
            state_of_charge_initial=init,
        )
    return n


def _frozen(n):
    from services.adequacy.sweep import freeze_capacities

    freeze_capacities(n, source="nom", transient=False)
    return n


def _direct(n, *, horizon=4, overlap=0, **kw):
    with OP.non_cyclic(n):
        return OP.run_windows(n, horizon=horizon, overlap=overlap, solver_name="highs", **kw)


def _val(rec, side, name):
    return rec[f"{side}_metrics"][name]["value"]


def _op_solve(**kw):
    cfg = {"fixed_sizes": "nameplate", "solve_strategy": "rolling", "rolling_horizon": 4,
           "rolling_overlap": 0, **kw}
    return _solve(**cfg)


def _live():
    from routers.operational_gap import _live_fingerprint

    return _live_fingerprint()


def _read(n):
    from routers.simulation import _state

    return G.read(n, _state.get("solver_config"), live_fingerprint=_live())


# ── the reference (C12, C24) ────────────────────────────────────────────────


def test_the_reference_is_the_full_horizon_dispatch_on_the_same_sizes():
    """C12: the reference equals one full-horizon solve of the same frozen
    network, cyclic off, from the configured initial charge. The operation,
    blind past each 4 h window, costs more."""
    n = _frozen(_net())
    _direct(n)
    rec = n.meta[G.GAP_KEY]

    m = _frozen(_net())
    with OP.non_cyclic(m):
        status, _ = m.optimize(m.snapshots, solver_name="highs")
    assert status == "ok"
    want = G.metrics(m)
    for name in G.DEFINITIONS:
        assert _val(rec, "reference", name) == pytest.approx(want[name]["value"], abs=TOL)
    assert m.storage_units_t.state_of_charge["bess"].iloc[0] != pytest.approx(5.0)  # it moved
    assert _val(rec, "operational", "opex_eur") > _val(rec, "reference", "opex_eur") + 1.0
    assert rec["reference"] == {"status": "ok", "condition": "optimal"}
    assert rec["windows"] == {"total": 6, "solved": 6, "failed": 0, "failed_index": None}
    # The configured initial charge is back for the next run.
    assert n.storage_units.at["bess", "cyclic_state_of_charge"]


def test_one_window_over_the_horizon_has_no_gap():
    n = _frozen(_net())
    _direct(n, horizon=HOURS)
    rec = n.meta[G.GAP_KEY]
    for name in G.DEFINITIONS:
        assert _val(rec, "operational", name) == pytest.approx(
            _val(rec, "reference", name), abs=TOL
        )


def test_the_opex_is_each_sides_own_dispatch_never_the_objective(reset_backend):
    """C4/C24: after the run n.objective is the last window's objective; the
    operational opex is the stitched dispatch priced over the horizon."""
    n = _install(_net())
    status, cond, _, lines = _op_solve(rolling_overlap=2)
    assert status == "ok", (cond, lines[-5:])
    rec = n.meta[G.GAP_KEY]
    op = _val(rec, "operational", "opex_eur")
    assert op == pytest.approx(n.meta[OP.META_KEY]["opex"], rel=1e-9)
    by_hand = float((n.generators_t.p["grid"] * PRICE).sum())
    assert op == pytest.approx(by_hand, rel=1e-9)
    assert abs(float(n.objective) - op) > 1.0


# ── the record and the read (C12, C14, C25, C26, C27) ───────────────────────


def test_a_fresh_read_defines_every_metric_and_counts_the_windows(reset_backend):
    n = _install(_net())
    status, cond, _, lines = _op_solve()
    assert status == "ok", (cond, lines[-5:])
    out = _read(n)
    assert out["flag"] == G.FRESH
    assert out["label"] == "full-horizon dispatch on the same sizes"
    assert out["windows"] == {"total": 6, "solved": 6, "failed": 0, "failed_index": None}
    assert out["reference"]["status"] == "ok"
    assert (out["horizon"], out["overlap"]) == (4, 0)
    assert set(out["metrics"]) == set(G.DEFINITIONS) == set(out["definitions"])
    for name, row in out["metrics"].items():
        assert row["definition"] == G.DEFINITIONS[name]
    opex = out["metrics"]["opex_eur"]
    assert opex["gap"] == pytest.approx(opex["operational"] - opex["reference"])
    assert opex["gap"] > 1.0
    assert opex["gap_pct"] == pytest.approx(100 * opex["gap"] / abs(opex["reference"]))
    assert "LP-only objective terms excluded" in out["opex_basis"]
    assert out["excluded_terms"] and out["claim_limit"]
    assert "Store p > 0" in out["definitions"]["storage_discharge_mwh"]


def test_a_failed_window_is_counted_and_survives_its_own_run(reset_backend):
    """C14/C25: window 2 of 6 cannot serve its load. The run fails, and the
    read says operational_window_failed, with the windows counted and no
    metric."""
    n = _net()
    n.loads_t.p_set.iloc[5, 0] = 1e4
    n = _install(n)
    status, cond, _, _ = _op_solve()
    assert status != "ok"
    rec = n.meta[G.GAP_KEY]
    assert rec["windows"] == {"total": 6, "solved": 1, "failed": 1, "failed_index": 1}
    assert rec["operational_metrics"] is None
    out = _read(n)
    assert out["flag"] in (G.WINDOW_FAILED, G.STALE)
    assert out["metrics"] is None
    # Read against the record directly with a matching fingerprint: failed, not missing.
    n.meta["assistant_solve_input_fingerprint"] = _live()
    out = _read(n)
    assert out["flag"] == G.WINDOW_FAILED
    assert out["windows"]["failed_index"] == 1 and out["metrics"] is None


def test_a_later_full_solve_leaves_no_gap(reset_backend):
    """C25: the record is cleared at every solve start."""
    n = _install(_net())
    assert _op_solve()[0] == "ok"
    assert G.GAP_KEY in n.meta
    status, cond, _, _ = _solve(fixed_sizes="nameplate")
    assert status in ("ok", "optimal"), cond
    assert G.GAP_KEY not in n.meta
    assert _read(n)["flag"] == G.MISSING
    assert _read(n)["metrics"] is None


def test_never_run_reads_missing(reset_backend):
    n = _install(_net())
    out = _read(n)
    assert out["flag"] == G.MISSING and out["metrics"] is None and out["windows"] is None
    assert out["definitions"] == G.DEFINITIONS  # defined even when missing


@pytest.mark.parametrize("change", ["load", "horizon", "weightings", "no_live", "no_solved"])
def test_an_input_change_reads_stale(reset_backend, change):
    """C12/C26: stale reads None + flag; an unknown fingerprint is stale too."""
    from routers.simulation import _state

    n = _install(_net())
    assert _op_solve()[0] == "ok"
    assert _read(n)["flag"] == G.FRESH
    live = _live()
    if change == "load":
        n.loads_t.p_set.iloc[0, 0] = 11.0
        live = _live()
    elif change == "horizon":
        _state["solver_config"] = dataclasses.replace(_state["solver_config"], rolling_horizon=6)
        live = _live()
    elif change == "weightings":
        n.snapshot_weightings.loc[n.snapshots[0], :] = 2.0
        live = _live()
    elif change == "no_live":
        live = None
    else:
        n.meta.pop("assistant_solve_input_fingerprint")
    out = G.read(n, _state["solver_config"], live_fingerprint=live)
    assert out["flag"] == G.STALE
    assert out["metrics"] is None and out["windows"] is None


def test_the_key_alone_makes_a_record_stale():
    """The record's own key is checked, not only the fingerprint."""
    n = _frozen(_net())
    _direct(n)
    n.meta["assistant_solve_input_fingerprint"] = "fp"
    cfg = {"fixed_sizes": "nameplate", "solve_strategy": "rolling", "rolling_horizon": 4,
           "rolling_overlap": 0}
    assert G.read(n, cfg, live_fingerprint="fp")["flag"] == G.FRESH
    assert G.read(n, {**cfg, "rolling_overlap": 1}, live_fingerprint="fp")["flag"] == G.STALE
    assert G.read(n, {**cfg, "rolling_horizon": 5}, live_fingerprint="fp")["flag"] == G.STALE
    assert G.read(n, {**cfg, "fixed_sizes": "off"}, live_fingerprint="fp")["flag"] == G.MISSING
    n.snapshot_weightings.iloc[0, :] = 3.0
    assert G.read(n, cfg, live_fingerprint="fp")["flag"] == G.STALE


def test_a_reference_that_did_not_solve_reads_none_and_the_run_goes_on(
    reset_backend, monkeypatch
):
    def _unsolved(n, **kw):
        return {"status": "warning", "condition": "infeasible", "metrics": None}

    monkeypatch.setattr(G, "reference", _unsolved)
    n = _install(_net())
    status, cond, _, _ = _op_solve()
    assert status == "ok", cond
    rec = n.meta[G.GAP_KEY]
    assert rec["key"]["reference_status"] == "warning"
    out = _read(n)
    assert out["flag"] == G.REFERENCE_NOT_SOLVED
    assert out["metrics"] is None
    assert out["reference"] == {"status": "warning", "condition": "infeasible"}


def test_a_missing_column_is_none_with_its_reason_never_zero():
    """C26: no storage, no curtailment: those metrics are None with a reason,
    and their gap is None; a true zero needs the column."""
    n = _frozen(_net(storage=False))
    _direct(n)
    rec = n.meta[G.GAP_KEY]
    for name in ("storage_discharge_mwh", "storage_end_mwh", "curtailed_energy_mwh",
                 "unserved_energy_mwh"):
        assert rec["operational_metrics"][name]["value"] is None
        assert rec["operational_metrics"][name]["reason"]
    rows = G._gap_rows(rec["operational_metrics"], rec["reference_metrics"])
    assert rows["storage_end_mwh"]["gap"] is None and rows["storage_end_mwh"]["gap_pct"] is None
    assert rows["storage_end_mwh"]["operational_reason"]
    # Only the defined metrics are reported; a true zero is a value, not None.
    rows = G._gap_rows({"x": {"value": 0.0}}, {"x": {"value": 0.0}})
    assert set(rows) == set(G.DEFINITIONS) and "x" not in rows
    zero = G._gap_rows({"opex_eur": {"value": 0.0}}, {"opex_eur": {"value": 0.0}})
    assert zero["opex_eur"]["gap"] == 0.0 and zero["opex_eur"]["gap_pct"] is None


def test_storage_discharge_counts_storage_units_and_stores_on_the_stores_weighting():
    """C27: StorageUnit p > 0 and Store p > 0, each x the stores weighting."""
    n = _net()
    n.add("Bus", "h", carrier="AC")
    n.add("Store", "tank", bus="site", e_nom=20.0, e_initial=10.0, e_cyclic=False)
    n.snapshot_weightings["stores"] = 2.0
    n = _frozen(n)
    _direct(n)
    rec = n.meta[G.GAP_KEY]
    want = 2.0 * (
        n.storage_units_t.p["bess"].clip(lower=0).sum() + n.stores_t.p["tank"].clip(lower=0).sum()
    )
    assert _val(rec, "operational", "storage_discharge_mwh") == pytest.approx(want, abs=TOL)
    end = n.storage_units_t.state_of_charge["bess"].iloc[-1] + n.stores_t.e["tank"].iloc[-1]
    assert _val(rec, "operational", "storage_end_mwh") == pytest.approx(end, abs=TOL)


# ── no side effect on the operation (C22, C23) ──────────────────────────────


def _without_reference(monkeypatch):
    monkeypatch.setattr(OP, "run_windows", functools.partial(OP.run_windows, reference=False))


def _outputs(n):
    return {
        "gen_p": n.generators_t.p.copy(),
        "su_soc": n.storage_units_t.state_of_charge.copy(),
        "su_p": n.storage_units_t.p.copy(),
        "link_p0": n.links_t.p0.copy(),
        "store_e": n.stores_t.e.copy(),
    }


def _meta_without_gap(n):
    meta = copy.deepcopy(dict(n.meta))
    meta.pop(G.GAP_KEY, None)
    rep = meta.get(OP.META_KEY)
    return meta, rep


def _assert_same(a, b):
    for key in a:
        pd.testing.assert_frame_equal(a[key], b[key], atol=TOL, check_like=True)


def test_the_reference_leaves_the_operation_unchanged_with_curtailment_on(
    reset_backend, monkeypatch
):
    """C22: with curtailment on and O > 0, the stitched dispatch, the S1
    report and n.meta are the same with and without the reference solve."""
    from services.solver import dc_curtailment as C
    from tests import test_dc_curtailment as DC

    def run():
        n = DC._network()
        n.add("StorageUnit", "bess", bus="dc_mv", p_nom=10.0, max_hours=2.0,
              state_of_charge_initial=5.0)
        n.generators_t.marginal_cost["grid"] = pd.Series(
            np.r_[np.full(6, 20.0), np.full(6, 80.0)], index=n.snapshots)
        status, cond, _, lines = DC._solve(
            n, monkeypatch, DC._terms(), fixed_sizes="nameplate", solve_strategy="rolling",
            rolling_horizon=4, rolling_overlap=2)
        assert status == "ok", (cond, lines[-5:])
        return n

    with_ref = run()
    assert C.META_KEY in with_ref.meta  # curtailment was on
    assert with_ref.meta[G.GAP_KEY]["operational_metrics"]["curtailed_energy_mwh"]["value"] > 0
    out_a, (meta_a, rep_a) = _outputs(with_ref), _meta_without_gap(with_ref)
    _without_reference(monkeypatch)
    without = run()
    assert G.GAP_KEY in without.meta and without.meta[G.GAP_KEY]["reference"] is None
    _assert_same(out_a, _outputs(without))
    meta_b, rep_b = _meta_without_gap(without)
    assert rep_a == rep_b
    for k in set(meta_a) | set(meta_b):
        if k in ("assistant_solve_input_fingerprint", "assistant_solve_exact_input_fingerprint"):
            continue
        assert repr(meta_a.get(k)) == repr(meta_b.get(k)), k


def test_the_reference_leaves_the_reserve_rows_unchanged():
    """C22: DC-06 reserves on, O > 0: the stitched dispatch and the persisted
    reserve solution are the same with and without the reference."""
    from tests import test_reserve_products as T

    def run(reference):
        n = T._site(snaps=8)
        n.storage_units["cyclic_state_of_charge"] = False
        n.storage_units["state_of_charge_initial"] = 22.0
        up = T._product("afrr_up", kind="secondary", direction="up", sustain_h=1.0)
        T._bind(n, [up], {"afrr_up": np.arange(8.0) + 1})
        fn = T.RP._wrap_with_reserve_products(
            n, None, T._cfg([up]), backup=({T.STRING: 20.0}, frozenset({T.STRING})))
        with OP.non_cyclic(n):
            OP.run_windows(n, horizon=3, overlap=1, reference=reference, solver_name="highs",
                           extra_functionality=fn, include_objective_constant=False)
        assert T.RP.persist_solution(n)
        return n

    a, b = run(True), run(False)
    assert a.meta[G.GAP_KEY]["reference"]["status"] == "ok"
    _assert_same(_outputs(a), _outputs(b))
    fa, flags_a = T.RP.solved(a)
    fb, flags_b = T.RP.solved(b)
    assert flags_a == flags_b
    pd.testing.assert_frame_equal(fa, fb, atol=TOL)


def test_a_window_is_seeded_by_the_operation_never_by_the_reference(monkeypatch):
    """C23: each window i > 0 starts from the stitched charge at the snapshot
    before it, which differs from the reference's charge there."""
    n = _frozen(_net())
    accessor = type(n.optimize)
    real = accessor.__call__
    seen: list[tuple[pd.Index, float, pd.Series]] = []

    def spy(self, snapshots=None, **kw):
        init = float(n.storage_units.at["bess", "state_of_charge_initial"])
        out = real(self, snapshots, **kw)
        seen.append((snapshots, init, n.storage_units_t.state_of_charge["bess"].copy()))
        return out

    monkeypatch.setattr(accessor, "__call__", spy)
    _direct(n)
    (ref_sns, ref_init, ref_soc), wins = seen[0], seen[1:]
    assert len(ref_sns) == HOURS and ref_init == pytest.approx(5.0)
    assert wins[0][1] == pytest.approx(5.0)  # window 1: the configured initial
    soc = n.storage_units_t.state_of_charge["bess"]
    differs = 0
    for sns, init, _ in wins[1:]:
        before = n.snapshots[n.snapshots.get_loc(sns[0]) - 1]
        assert init == pytest.approx(soc[before], abs=TOL)
        differs += abs(ref_soc[before] - soc[before]) > 1e-3
    assert differs  # the reference's charge would have seeded something else


# ── the route (C13, C28) ────────────────────────────────────────────────────


def test_the_route_reads_the_gap_and_never_solves(client, monkeypatch):
    calls = []
    monkeypatch.setattr(G, "reference", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(OP, "run_windows", lambda *a, **k: calls.append(1))
    r = client.get("/api/results/operational_gap")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flag"] == G.MISSING and body["metrics"] is None
    assert set(body["definitions"]) == set(G.DEFINITIONS)
    assert not calls


def test_the_route_is_409_while_a_solve_runs(client, monkeypatch):
    import routers.results as R

    monkeypatch.setattr(R, "_solver_in_flight", lambda: True)
    r = client.get("/api/results/operational_gap")
    assert r.status_code == 409
    assert r.json()["detail"]["error_kind"] == "solver_in_flight"


def test_the_route_is_get_only(client):
    assert client.post("/api/results/operational_gap").status_code == 405
