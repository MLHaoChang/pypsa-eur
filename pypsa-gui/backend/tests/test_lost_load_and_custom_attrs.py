"""
Regression tests for two defects found while assessing the FMEA/adequacy work.

1. ``lost_load_cost_meur`` was always 0.0. ``_compute_economics_summary`` read
   the VOLL slack capture from ``n.meta["last_lost_load"]``, but nothing in the
   backend ever writes that key — solver_service emits the capture onto the
   solver STATE (``_emit_state(last_lost_load=...)``), which is persisted to
   ``results_state.pkl``. The read therefore returned ``None`` on every code
   path and the whole lost-load accumulation block was dead code.

2. ``_merge_partial_update`` dropped custom columns. It built ``input_cols``
   from PyPSA's ``components.<attr>.defaults`` index, which by construction
   excludes GUI-added columns like ``curtailment_cost``. Since the update path
   is remove+add, any partial PUT that didn't happen to resend the custom
   column silently reset it.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.compare as CMP
import routers.network as NET


# ── Defect 2: custom columns survive a partial update ──────────────────────

def _net_with_custom_col() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "g", bus="b", p_nom=100.0, marginal_cost=10.0,
          curtailment_cost=42.0)
    return n


def test_merge_partial_update_preserves_custom_columns():
    """A partial PUT that omits `curtailment_cost` must not erase it."""
    n = _net_with_custom_col()
    assert "curtailment_cost" in n.generators.columns

    merged = NET._merge_partial_update(n, "generators", "g", {"p_nom": 5.0})

    assert merged["p_nom"] == 5.0, "the submitted field must win"
    assert "curtailment_cost" in merged, (
        "custom column dropped from the merge — the remove+add cycle in "
        "_update_component would reset it to the schema default"
    )
    assert merged["curtailment_cost"] == 42.0


def test_merge_partial_update_still_lets_caller_override_custom_column():
    """Preserving must not shadow an explicitly submitted value."""
    n = _net_with_custom_col()
    merged = NET._merge_partial_update(n, "generators", "g",
                                       {"curtailment_cost": 7.0})
    assert merged["curtailment_cost"] == 7.0


def test_merge_partial_update_keeps_standard_columns():
    """The original partial-PUT guarantee is unchanged."""
    n = _net_with_custom_col()
    merged = NET._merge_partial_update(n, "generators", "g", {"p_nom": 5.0})
    assert merged["marginal_cost"] == 10.0


# ── Defect 1: lost-load cost reaches the economics payload ─────────────────

VOLL = 3000.0          # EUR/MWh
SHED_MW = 2.0          # per snapshot, on the one bus
N_SNAPSHOTS = 4


def _solved_net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=10.0)
    # Minimal "solved" dispatch so has_solve=True is honest.
    n.generators_t.p = pd.DataFrame(10.0, index=n.snapshots, columns=["g"])
    n.generators["p_nom_opt"] = 100.0
    return n


def _capture() -> dict:
    total_mwh = SHED_MW * N_SNAPSHOTS
    n = _solved_net()
    return {
        "lost_load_t": pd.DataFrame(SHED_MW, index=n.snapshots, columns=["b"]),
        "lost_load_total_mwh": total_mwh,
        "lost_load_cost_eur": total_mwh * VOLL,
    }


def _lost_load_meur(result) -> float:
    return sum(
        c.lost_load_cost_meur.total
        for c in result.by_carrier.values()
        if c.lost_load_cost_meur is not None
    )


def test_economics_summary_counts_lost_load_when_capture_supplied():
    n = _solved_net()
    result = CMP._compute_economics_summary(
        n, [], False, True, prices_from_state=False,
        lost_load_cap=_capture(),
    )
    expected_meur = SHED_MW * N_SNAPSHOTS * VOLL / 1e6
    got = _lost_load_meur(result)
    assert got == pytest.approx(expected_meur, rel=1e-6), (
        f"expected {expected_meur} MEUR of lost-load cost, got {got}"
    )


def test_economics_summary_reports_zero_without_capture():
    """No capture (solved without VOLL, or nothing shed) => no cost."""
    n = _solved_net()
    result = CMP._compute_economics_summary(
        n, [], False, True, prices_from_state=False, lost_load_cap=None,
    )
    assert _lost_load_meur(result) == pytest.approx(0.0)


def test_network_meta_is_not_a_capture_source():
    """
    Pins the root cause. `n.meta["last_lost_load"]` is not written by anything
    in the backend; if a future refactor reintroduces a read from there, the
    field silently goes back to always-zero. Populating meta must NOT be enough
    to produce a cost — only the threaded `lost_load_cap` may.
    """
    n = _solved_net()
    n.meta = {"last_lost_load": _capture()}
    result = CMP._compute_economics_summary(
        n, [], False, True, prices_from_state=False,
    )
    assert _lost_load_meur(result) == pytest.approx(0.0)
