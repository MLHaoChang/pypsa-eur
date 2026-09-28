"""
P3b — Discrete outer-loop redundancy selection (TDD).

Plan Phase 3b: small integer domains per asset class; least-cost option
meeting the ENS target; MC certify cadence pinned to finalists-only.
Reuse coupling-loop *control-flow* only — not continuous bisection math.
Out of scope: joint MILP with UC + redundancy.
"""
from __future__ import annotations

import pytest

from services.adequacy import redundancy as R


def test_mc_certify_cadence_is_pinned_finalists_only():
    """Plan P3b: pin MC certify cadence (every candidate vs finalists only)."""
    assert R.MC_CERTIFY_CADENCE == "finalists_only"


def test_max_trains_domain_is_small_integers():
    """Plan P3b: small integer domains per asset class."""
    assert R.MAX_TRAINS_BY_CLASS["parallel_storage"] >= 2
    assert R.MAX_TRAINS_BY_CLASS["parallel_storage"] <= 4
    assert R.MAX_TRAINS_BY_CLASS["n1_generation"] == 1
    assert R.MAX_TRAINS_BY_CLASS["n1_conversion"] == 1


def test_expand_redundancy_domain_emits_train_counts_for_storage():
    domain = R.expand_redundancy_domain(("base", "parallel_storage"))
    assert "base" in domain
    assert "parallel_storage@1" in domain
    assert "parallel_storage@2" in domain
    assert "parallel_storage" not in domain  # expanded forms replace bare kind
    gen_domain = R.expand_redundancy_domain(("n1_generation",))
    assert gen_domain == ("n1_generation",)


def test_parse_scenario_id_splits_kind_and_trains():
    assert R.parse_scenario_id("base") == ("base", 1)
    assert R.parse_scenario_id("parallel_storage@2") == ("parallel_storage", 2)
    assert R.parse_scenario_id("n1_generation") == ("n1_generation", 1)


def test_apply_parallel_storage_trains_scales_headroom_and_undo():
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "b")
    n.add("Load", "l", bus="b", p_set=1.0)
    undo1, mut1 = R.apply_redundancy_scenario(n, "parallel_storage@1")
    assert mut1.get("trains") == 1
    max1 = float(n.storage_units.at["eh_spare_storage", "p_nom_max"])
    undo1()
    assert "eh_spare_storage" not in n.storage_units.index

    undo2, mut2 = R.apply_redundancy_scenario(n, "parallel_storage@2")
    max2 = float(n.storage_units.at["eh_spare_storage", "p_nom_max"])
    assert max2 == pytest.approx(2.0 * max1)
    assert mut2["trains"] == 2
    undo2()
    assert "eh_spare_storage" not in n.storage_units.index


def _table(*, options):
    return {
        "certify_method": "ens",
        "ens_cap_permyriad": 100.0,
        "options": options,
        "solves_attempted": len(options),
        "aborted": False,
        "comparable_solved": sum(
            1 for o in options if o.get("status") in ("ok", "optimal")
        ),
    }


def test_select_least_cost_meeting_target_and_losers_fail_metric():
    """Acceptance 3b: selected meets target; losers fail the same ENS metric."""
    table = _table(options=[
        {
            "scenario_id": "base",
            "status": "ok",
            "cost_at_target_eur": 100.0,
            "meets_target": False,
            "not_applicable": False,
        },
        {
            "scenario_id": "parallel_storage@1",
            "status": "ok",
            "cost_at_target_eur": 500.0,
            "meets_target": True,
            "not_applicable": False,
        },
        {
            "scenario_id": "parallel_storage@2",
            "status": "ok",
            "cost_at_target_eur": 300.0,
            "meets_target": True,
            "not_applicable": False,
        },
        {
            "scenario_id": "n1_generation",
            "status": "ok",
            "cost_at_target_eur": 50.0,
            "meets_target": False,
            "not_applicable": False,
        },
    ])
    sel = R.select_redundancy_option(table)
    assert sel["selected_id"] == "parallel_storage@2"
    assert sel["selected"]["meets_target"] is True
    assert sel["selection_rule"] == "least_cost_meeting_ens_target"
    assert sel["mc_certify_cadence"] == "finalists_only"
    loser_ids = {o["scenario_id"] for o in sel["losers"]}
    assert loser_ids == {"base", "n1_generation"}
    assert all(o["meets_target"] is False for o in sel["losers"])
    dominated = {o["scenario_id"] for o in sel["dominated"]}
    assert dominated == {"parallel_storage@1"}


def test_select_raises_when_no_feasible_option():
    table = _table(options=[
        {
            "scenario_id": "base",
            "status": "ok",
            "cost_at_target_eur": 10.0,
            "meets_target": False,
            "not_applicable": False,
        },
    ])
    with pytest.raises(R.RedundancyScenarioError, match="no feasible"):
        R.select_redundancy_option(table)


def test_compare_attaches_selection_when_requested():
    """compare_redundancy_scenarios exposes select= and expands domain."""
    domain = R.expand_redundancy_domain(None)
    assert any(s.startswith("parallel_storage@") for s in domain)
    import inspect
    sig = inspect.signature(R.compare_redundancy_scenarios)
    assert "select" in sig.parameters
    assert sig.parameters["select"].default is True


@pytest.mark.live_solve
def test_run_eh_study_redundancy_selection_on_lever():
    """Orchestrated redundancy stage writes selection under store."""
    import queue
    import threading

    import pandas as pd
    import pypsa

    from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "cheap", bus="b", carrier="gas",
          p_nom=60.0, marginal_cost=10.0)
    n.add("Generator", "peak", bus="b", carrier="gas",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=80.0,
          capital_cost=200.0, marginal_cost=200.0)

    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
        "mc_certify_required": False,
        "levers": default_strong_grid_pack().levers.model_copy(update={
            "redundancy": True,
        }),
    })
    PyPSAService.set_network(n)
    store: dict = {}
    report = S.run_eh_study(
        n, pack, SolverConfig(voll=150.0, ens_cap_permyriad=1000.0),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "redundancy", "assemble"),
        store=store,
    )
    assert "eh_redundancy_comparison" in store
    table = store["eh_redundancy_comparison"]
    assert "selection" in table
    # Domain expansion must have run (storage train variants present or attempted).
    ids = [o["scenario_id"] for o in table["options"]]
    assert any(i.startswith("parallel_storage") for i in ids)
    if table["selection"] is not None:
        sel = table["selection"]
        assert sel["selected"]["meets_target"] is True
        assert sel["mc_certify_cadence"] == "finalists_only"
        assert all(o["meets_target"] is False for o in sel["losers"])
    assert report.completeness["redundancy"] in ("ok", "not_established")
