"""
Lost-load capture weighting (Phase 0 Task 3) + shed-hours metric (Task 4).

Design: docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md
§§5.1, 6.3. Two divergent lost-load totals existed: the capture in
solver_service computed `total_mwh * voll` UNWEIGHTED ("assumes hourly
snapshots"), while the solve-log cost decomposition weighted by
`snapshot_weights(n, "objective")`. Under tsam representative snapshots the
two disagree — and the Phase 1 ENS cap constrains the weighted integral, so
an unweighted headline would not mean what the target means. Canonical
decision: capture totals are SNAPSHOT-WEIGHTED ("generators" column for
energy, "objective" for cost, per period_utils.snapshot_weights); the
per-snapshot frame `lost_load_t` stays unweighted MW (it is a power series —
consumers weight it).
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from services.adequacy import metrics
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

WEIGHT = 3.0
N_SNAPSHOTS = 4
LOAD_MW = 100.0
GEN_MW = 60.0
SHED_MW = LOAD_MW - GEN_MW          # 40 MW short every snapshot
VOLL = 3000.0


def _short_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT   # non-unit on ALL columns
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=GEN_MW,
          marginal_cost=10.0)
    return n


def _solve_with_capture(n: pypsa.Network) -> dict:
    PyPSAService.set_network(n)
    sink: dict = {}
    cfg = SolverConfig(voll=VOLL)
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw),
    )
    assert status in ("ok", "optimal"), (status, condition)
    cap = sink.get("last_lost_load")
    assert isinstance(cap, dict) and cap.get("lost_load_t") is not None, (
        "solve shed load but produced no capture"
    )
    return cap


def test_capture_totals_are_snapshot_weighted():
    """40 MW shed × 4 snapshots × weight 3 = 480 MWh, not the unweighted 160."""
    cap = _solve_with_capture(_short_network())
    expected_mwh = SHED_MW * N_SNAPSHOTS * WEIGHT
    assert cap["lost_load_total_mwh"] == pytest.approx(expected_mwh, rel=1e-6), (
        f"capture total {cap['lost_load_total_mwh']} — unweighted would be "
        f"{SHED_MW * N_SNAPSHOTS}, weighted must be {expected_mwh}"
    )
    assert cap["lost_load_cost_eur"] == pytest.approx(expected_mwh * VOLL, rel=1e-6)
    # The per-snapshot frame stays unweighted MW.
    ll = cap["lost_load_t"]
    assert float(ll.max().max()) == pytest.approx(SHED_MW, rel=1e-4)
    # Explicit VoLL key — consumers must not have to re-derive it from a
    # cost/energy ratio that would skew if the two weight columns differ.
    assert cap["voll_eur_per_mwh"] == pytest.approx(VOLL)


def test_capture_cost_matches_economics_summary_cross_surface():
    """The frontier and the worksheet both hang off this number — the capture
    and the Economics roll-up must agree under non-unit weights.

    This carried a `strict=True` xfail until `_compute_economics_summary`
    grew its `lost_load_cap` parameter on master (07b32c2, PR #4). Before
    that the roll-up read a `n.meta` key nothing ever wrote, so it reported
    zero VOLL cost and the two surfaces disagreed silently."""
    n = _short_network()
    cap = _solve_with_capture(n)
    from routers.compare import _compute_economics_summary
    result = _compute_economics_summary(
        n, [], False, True, prices_from_state=False, lost_load_cap=cap,
    )
    summary_meur = sum(
        c.lost_load_cost_meur.total for c in result.by_carrier.values()
        if c.lost_load_cost_meur is not None
    )
    assert summary_meur * 1e6 == pytest.approx(cap["lost_load_cost_eur"], rel=1e-6)


# ── the pure helper (unit level) ──────────────────────────────────────────

def _ll_frame(values, cols=("b1",)) -> pd.DataFrame:
    idx = pd.date_range("2030-01-01", periods=len(values), freq="h")
    return pd.DataFrame({c: values for c in cols}, index=idx)


def test_lost_load_totals_weights_and_clips():
    ll = _ll_frame([10.0, 0.0, -0.001, 5.0])     # tiny negative = LP dust
    w = pd.Series(2.0, index=ll.index)
    out = metrics.lost_load_totals(ll, energy_weights=w, cost_weights=w, voll=1000.0)
    assert out["total_mwh"] == pytest.approx((10.0 + 5.0) * 2.0)
    assert out["cost_eur"] == pytest.approx((10.0 + 5.0) * 2.0 * 1000.0)


def test_lost_load_totals_empty_frame():
    ll = pd.DataFrame()
    w = pd.Series(dtype=float)
    out = metrics.lost_load_totals(ll, energy_weights=w, cost_weights=w, voll=1000.0)
    assert out["total_mwh"] == 0.0 and out["cost_eur"] == 0.0


# ── shed-hours (Task 4) ───────────────────────────────────────────────────

def test_shed_hours_zero_frame():
    out = metrics.shed_hours(pd.DataFrame(), weights=pd.Series(dtype=float))
    assert out["total"] == 0.0 and out["by_period"] == {}


def test_shed_hours_counts_weighted_snapshots():
    """One shedding snapshot with weight 3 = 3 shed-hours: the metric is the
    weighted duration of shortfall, not a row count."""
    ll = _ll_frame([0.0, 12.0, 0.0, 0.0])
    w = pd.Series(3.0, index=ll.index)
    out = metrics.shed_hours(ll, weights=w)
    assert out["total"] == pytest.approx(3.0)
    assert out["by_period"] == {"ALL": pytest.approx(3.0)}


def test_shed_hours_ignores_numerical_dust():
    ll = _ll_frame([1e-7, 1e-9, 0.0, 0.0])
    w = pd.Series(1.0, index=ll.index)
    assert metrics.shed_hours(ll, weights=w)["total"] == 0.0
    # ...but the threshold is an argument, not a buried constant.
    assert metrics.shed_hours(ll, weights=w, threshold_mw=1e-8)["total"] == pytest.approx(1.0)


def test_shed_hours_sums_buses_before_thresholding():
    """Two buses each below threshold but jointly above must count — the
    definition is TOTAL shed power per snapshot."""
    idx = pd.date_range("2030-01-01", periods=1, freq="h")
    ll = pd.DataFrame({"b1": [6e-4], "b2": [6e-4]}, index=idx)
    w = pd.Series(1.0, index=idx)
    assert metrics.shed_hours(ll, weights=w)["total"] == pytest.approx(1.0)


def test_shed_hours_splits_by_period():
    base = pd.date_range("2030-01-01", periods=2, freq="h")
    idx = pd.MultiIndex.from_product([[2030, 2035], base],
                                     names=["period", "timestep"])
    ll = pd.DataFrame({"b": [10.0, 0.0, 10.0, 10.0]}, index=idx)
    w = pd.Series(2.0, index=idx)
    out = metrics.shed_hours(ll, weights=w)
    assert out["total"] == pytest.approx(6.0)
    assert out["by_period"] == {2030: pytest.approx(2.0), 2035: pytest.approx(4.0)}


def test_electrical_columns_filters_on_bus_carrier():
    n = pypsa.Network()
    n.add("Bus", "b_el", carrier="AC")
    n.add("Bus", "b_h2", carrier="H2")
    n.add("Bus", "b_blank", carrier="")
    cols = metrics.electrical_columns(n, ["b_el", "b_h2", "b_blank", "ghost"])
    # Blank carrier defaults electrical (matches _canonical_load_carrier_key);
    # a column with no matching bus is kept — the metric must not silently
    # drop shed energy it cannot classify.
    assert cols == ["b_el", "b_blank", "ghost"]
