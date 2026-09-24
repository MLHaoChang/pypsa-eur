"""
P6(a) — dedicated-bus multi-energy ENS.

Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md Phase 6
Spike: docs/superpowers/findings/2026-09-19-eh-p6-multi-energy-spike.md
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest


def _sector_coupled_network(*, starve_h2: bool = True) -> pypsa.Network:
    """Electrical + H₂ loads on dedicated buses; optional H₂ supply starvation.

    When starved, omit the H₂ generator entirely (``p_nom=0`` is a hard
    ``generator_p_nom_invalid``). VOLL on the H₂ bus sheds the unmet load —
    same pattern as ``test_adequacy_ens_cap`` sector side-bus.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "AC")
    n.add("Carrier", "H2")
    n.add("Carrier", "gas")
    n.add("Bus", "elec", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "l_elec", bus="elec", carrier="AC", p_set=50.0)
    n.add("Load", "l_h2", bus="h2", carrier="H2", p_set=40.0)
    n.add("Generator", "gas_elec", bus="elec", carrier="gas",
          p_nom=80.0, marginal_cost=20.0)
    if not starve_h2:
        n.add("Generator", "h2_src", bus="h2", carrier="H2",
              p_nom=80.0, marginal_cost=10.0)
    return n


def test_dedicated_carrier_violations_detect_shared_bus():
    from services.adequacy import multi_energy as ME

    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "e", bus="b", carrier="AC", p_set=1.0)
    n.add("Load", "h", bus="b", carrier="H2", p_set=1.0)
    v = ME.dedicated_carrier_violations(n)
    assert v and "mixed" in v[0]


def test_dedicated_carrier_ok_on_sector_fixture():
    from services.adequacy import multi_energy as ME

    assert ME.dedicated_carrier_violations(_sector_coupled_network()) == []


def test_ens_by_carrier_from_bus_period_frame():
    from services.adequacy import multi_energy as ME

    n = _sector_coupled_network()
    bp = pd.DataFrame(
        {"elec": [0.0, 0.0], "h2": [10.0, 5.0]},
        index=pd.Index([0, 1], name="period"),
    )
    by = ME.ens_by_carrier_mwh(n, bp)
    assert by.get("hydrogen") == pytest.approx(15.0)
    assert by.get("electrical", 0.0) == pytest.approx(0.0)


def test_multi_energy_section_fail_closed_on_shared_bus():
    from services.adequacy import multi_energy as ME

    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "e", bus="b", carrier="AC", p_set=1.0)
    n.add("Load", "h", bus="b", carrier="H2", p_set=1.0)
    status, payload, note = ME.multi_energy_section_from_capture(n, {})
    assert status == "not_established"
    assert payload["ens_by_carrier_mwh"] is None
    assert "shared" in (note or "").lower() or payload["violations"]


def test_multi_energy_section_skipped_when_electrical_only():
    from services.adequacy import multi_energy as ME

    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "e", bus="b", carrier="AC", p_set=1.0)
    status, payload, note = ME.multi_energy_section_from_capture(n, {})
    assert status == "skipped"
    assert payload is None


def test_carrier_eue_helper_scopes_to_h2_columns():
    from services.adequacy import multi_energy as ME

    n = _sector_coupled_network()
    bp = pd.DataFrame({"elec": [3.0], "h2": [7.0]}, index=[0])
    cap = {"lost_load_bus_period_mwh": bp}
    assert ME.carrier_eue_mwh(n, cap, "hydrogen") == pytest.approx(7.0)
    assert ME.carrier_eue_mwh(n, cap, "electrical") == pytest.approx(3.0)


def test_eh_study_fills_multi_energy_when_h2_starved():
    """Live mini-solve: H₂ unmet appears in report.sections.multi_energy."""
    import queue
    import threading

    from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
    from services.adequacy import eh_study as study
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = _sector_coupled_network(starve_h2=True)
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=500.0),
    })
    cfg = SolverConfig(voll=500.0, ens_cap_permyriad=500.0)
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
    by = payload.get("ens_by_carrier_mwh") or {}
    assert by.get("hydrogen", 0.0) > 0.0
    # P6(b): live solves always emit per-Load capture → per_load_slack honesty.
    honesty = payload.get("honesty") or []
    assert (
        "per_load_slack" in honesty
        or "dedicated_bus_by_carrier" in honesty
    )
    assert payload.get("attribution") in (
        "per_load_slack", "dedicated_bus_by_carrier",
    )
    assert report.completeness.get("target") == "ok"


def test_electrical_default_unchanged_without_h2():
    import queue
    import threading

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
