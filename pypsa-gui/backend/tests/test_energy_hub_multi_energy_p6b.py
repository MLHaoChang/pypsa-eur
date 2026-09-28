"""
P6(b) — per-Load involuntary VOLL slacks (shared-bus attribution).

Spike: docs/superpowers/findings/2026-09-24-eh-p6b-multislack-spike.md
Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md Phase 6
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest


def _shared_bus_ac_h2(*, starve_h2: bool = True) -> pypsa.Network:
    """AC + H₂ Loads on the SAME bus — P6(a) fail-closed; P6(b) must attribute."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "AC")
    n.add("Carrier", "H2")
    n.add("Carrier", "gas")
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "l_elec", bus="hub", carrier="AC", p_set=50.0)
    n.add("Load", "l_h2", bus="hub", carrier="H2", p_set=40.0)
    n.add("Generator", "gas", bus="hub", carrier="gas",
          p_nom=80.0, marginal_cost=20.0)
    if not starve_h2:
        n.add("Generator", "h2_src", bus="hub", carrier="H2",
              p_nom=80.0, marginal_cost=10.0)
    return n


def _shared_bus_industrial_residential() -> pypsa.Network:
    """Two electrical Loads on one bus; supply covers only one."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "industrial", bus="b", carrier="AC", p_set=40.0)
    n.add("Load", "residential", bus="b", carrier="AC", p_set=40.0)
    # Only 40 MW firm — LP sheds 40 MW total across the two Load slacks.
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=40.0, marginal_cost=10.0)
    return n


def test_voll_slack_name_is_load_scoped():
    from services.adequacy import slack as S

    assert S.voll_slack_name("industrial") == f"{S.VOLL_SLACK_PREFIX}industrial"
    assert S.strip_slack_prefix(S.voll_slack_name("industrial")) == "industrial"


def test_shared_bus_ac_h2_is_dedicated_violation_without_capture():
    from services.adequacy import multi_energy as ME

    n = _shared_bus_ac_h2()
    assert ME.dedicated_carrier_violations(n)
    status, payload, _note = ME.multi_energy_section_from_capture(n, {})
    assert status == "not_established"
    assert payload["ens_by_carrier_mwh"] is None


def test_multi_energy_ok_on_shared_bus_with_per_load_capture():
    from services.adequacy import multi_energy as ME

    n = _shared_bus_ac_h2()
    lp = pd.DataFrame(
        {"l_elec": [0.0, 0.0], "l_h2": [10.0, 5.0]},
        index=pd.Index([0, 1], name="period"),
    )
    status, payload, note = ME.multi_energy_section_from_capture(
        n, {"lost_load_load_period_mwh": lp})
    assert status == "ok"
    assert "per_load_slack" in (payload.get("honesty") or [])
    assert payload["attribution"] == "per_load_slack"
    by = payload["ens_by_carrier_mwh"] or {}
    assert by.get("hydrogen") == pytest.approx(15.0)
    by_load = payload.get("ens_by_load_mwh") or {}
    assert by_load.get("l_h2") == pytest.approx(15.0)
    assert "l_elec" not in by_load or by_load.get("l_elec", 0) == 0


def test_shared_bus_industrial_residential_distinct_shed_columns():
    """Live solve: two Load slacks on one bus — capture columns are Load ids."""
    from services.adequacy import slack as S
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    n = _shared_bus_industrial_residential()
    cfg = SolverConfig(voll=500.0)  # no ENS cap — pure VoLL shed split
    PyPSAService.set_network(n)
    sink: dict = {}
    st, cond = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw),
    )
    assert st in ("ok", "optimal"), (st, cond)
    cap = sink["last_lost_load"]
    ll = cap["lost_load_t"]
    assert "industrial" in ll.columns
    assert "residential" in ll.columns
    assert S.VOLL_SLACK_PREFIX + "industrial" not in ll.columns
    # Both loads can shed; total shed ≈ 80 MWh (40 MW × 2 h).
    total = float(ll.clip(lower=0).to_numpy().sum())
    assert total == pytest.approx(80.0, rel=1e-2)
    # Bus roll-up still present for DtC / electrical consumers.
    bp = cap["lost_load_bus_period_mwh"]
    assert "b" in bp.columns
    assert float(bp["b"].sum()) == pytest.approx(80.0, rel=1e-2)
    lp = cap["lost_load_load_period_mwh"]
    assert "industrial" in lp.columns and "residential" in lp.columns



def test_eh_study_shared_bus_h2_fills_multi_energy():
    from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
    from services.adequacy import eh_study as study
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = _shared_bus_ac_h2(starve_h2=True)
    # Loose ENS cap: both Loads sit on an AC bus so both count toward the
    # electrical demand denominator; a tight cap makes the LP infeasible.
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0),
    })
    cfg = SolverConfig(voll=500.0, ens_cap_permyriad=5000.0)
    PyPSAService.set_network(n)
    report = study.run_eh_study(
        n, pack, cfg,
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
        budget_solves=5,
    )
    assert report.completeness.get("multi_energy") == "ok"
    payload = report.sections["multi_energy"].payload or {}
    assert payload.get("attribution") == "per_load_slack"
    assert "per_load_slack" in (payload.get("honesty") or [])
    by = payload.get("ens_by_carrier_mwh") or {}
    # At least one non-electrical Load carrier reported, or Load-level shed.
    by_load = payload.get("ens_by_load_mwh") or {}
    assert by.get("hydrogen", 0.0) > 0.0 or by_load.get("l_h2", 0.0) > 0.0 or sum(by_load.values()) > 0.0


def test_electrical_only_still_skips_multi_energy_section():
    from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
    from services.adequacy import eh_study as study
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", carrier="AC", p_set=30.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=50.0, marginal_cost=10.0)
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=100.0),
    })
    cfg = SolverConfig(voll=500.0, ens_cap_permyriad=100.0)
    PyPSAService.set_network(n)
    report = study.run_eh_study(
        n, pack, cfg,
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=("apply_pack", "ens_solve", "assemble"),
        budget_solves=5,
    )
    assert report.completeness.get("multi_energy") == "skipped"
    assert report.completeness.get("target") == "ok"
