"""
P1 — archetype pack apply / undo / solver-config patch (TDD).

Design: docs/superpowers/specs/2026-09-14-eh-reference-design.md §3, §6
Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md Phase 1

No SCR. Import overlays only + SolverConfig patch + DSR double-count preflight.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    ArchetypePack,
    AvailabilityTarget,
    ImportOverlaySpec,
    OptimizationLevers,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import archetypes as A
from services.solver_service import SolverConfig


def _hub_network(*, eh_role: bool = True) -> pypsa.Network:
    """Grid bus --import link--> hub bus with local load + optional local gen."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "demand", bus="hub", p_set=40.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="gas",
          p_nom=200.0, marginal_cost=5.0)
    n.add("Generator", "hub_peak", bus="hub", carrier="gas",
          p_nom=10.0, marginal_cost=80.0, p_nom_extendable=True,
          p_nom_max=100.0, capital_cost=50.0)
    kwargs = dict(bus0="grid", bus1="hub", p_nom=100.0, efficiency=1.0)
    if eh_role:
        kwargs["eh_role"] = "grid_import"
    n.add("Link", "import", **kwargs)
    return n


def test_select_import_links_by_eh_role():
    n = _hub_network(eh_role=True)
    assert A.select_import_links(n, ImportOverlaySpec()) == ["import"]


def test_select_import_links_by_eh_poc_bus():
    n = _hub_network(eh_role=False)
    n.buses.at["grid", "eh_poc"] = True
    assert A.select_import_links(n, ImportOverlaySpec()) == ["import"]


def test_select_import_links_by_carrier_fallback():
    n = _hub_network(eh_role=False)
    # PyPSA default link carrier is often empty; set explicitly.
    n.links.at["import", "carrier"] = "AC"
    assert A.select_import_links(
        n, ImportOverlaySpec(import_carriers=["AC"])) == ["import"]


def test_strong_grid_apply_is_noop_and_undo_safe():
    n = _hub_network()
    before = float(n.links.at["import", "p_nom"])
    pack = default_strong_grid_pack()
    undo = A.apply_archetype_pack(n, pack)
    assert float(n.links.at["import", "p_nom"]) == before
    undo()
    assert float(n.links.at["import", "p_nom"]) == before


def test_off_grid_zeros_import_and_undo_restores():
    n = _hub_network()
    pack = default_off_grid_pack()
    undo = A.apply_archetype_pack(n, pack)
    # Class-B discipline: flow blocked, nominal capacity retained.
    assert float(n.links.at["import", "p_nom"]) == 100.0
    assert float(n.links.at["import", "p_nom"]) > 0.0  # link_p_nom_invalid safe
    assert float(n.links.at["import", "p_max_pu"]) == 0.0
    assert float(n.links.at["import", "p_min_pu"]) == 0.0
    undo()
    assert float(n.links.at["import", "p_nom"]) == 100.0
    assert float(n.links.at["import", "p_max_pu"]) == 1.0


def test_energy_import_field_warns_and_stays_power_only():
    n = _hub_network()
    pack = default_weak_flexible_pack().model_copy(update={
        "import_overlay": ImportOverlaySpec(
            import_p_nom_mw=40.0,
            import_energy_mwh_per_year=1000.0,
        ),
    })
    result = A.apply_archetype_pack_detailed(n, pack)
    assert any("import_energy_mwh_per_year" in w for w in result.warnings)
    assert float(n.links.at["import", "p_nom"]) == 40.0
    # No GlobalConstraint side effects in P1.
    gc = getattr(n, "global_constraints", None)
    assert gc is None or gc.empty or "eh_import_energy" not in list(gc.index)
    result.undo()


def test_weak_flexible_splits_import_p_nom():
    n = _hub_network()
    # Second import link sharing the role.
    n.add("Link", "import_b", bus0="grid", bus1="hub", p_nom=80.0,
          efficiency=1.0, eh_role="grid_import")
    pack = default_weak_flexible_pack()
    assert pack.import_overlay.import_p_nom_mw == 50.0
    undo = A.apply_archetype_pack(n, pack)
    assert float(n.links.at["import", "p_nom"]) == 25.0
    assert float(n.links.at["import_b", "p_nom"]) == 25.0
    undo()
    assert float(n.links.at["import", "p_nom"]) == 100.0
    assert float(n.links.at["import_b", "p_nom"]) == 80.0


def test_weak_off_grid_without_import_links_preflight_errors():
    n = pypsa.Network()
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "demand", bus="hub", p_set=10.0)
    with pytest.raises(A.ArchetypePackError, match="import"):
        A.apply_archetype_pack(n, default_off_grid_pack())
    with pytest.raises(A.ArchetypePackError, match="import"):
        A.apply_archetype_pack(n, default_weak_flexible_pack())


def test_solver_config_patch_from_strong_grid_pack():
    pack = default_strong_grid_pack()
    patch = A.solver_config_patch(pack)
    assert patch["ens_cap_permyriad"] == 10.0
    assert "dsr_price_eur_per_mwh" not in patch or patch.get(
        "dsr_price_eur_per_mwh", 0) == 0


def test_solver_config_patch_dsr_opt_in_requires_buses():
    """Decision 15: never silently global — patch must not enable DSR
    without an explicit bus list; helper returns warnings instead."""
    pack = default_weak_flexible_pack()
    assert pack.dsr_opt_in is True
    patch, warnings = A.solver_config_patch_with_preflight(
        pack, network=_hub_network())
    # Without caller-supplied dsr_buses, DSR stays off and a warning fires.
    assert patch.get("dsr_price_eur_per_mwh", 0) == 0
    assert any("dsr" in w.lower() or "double" in w.lower()
               or "opt-in" in w.lower() or "bus" in w.lower()
               for w in warnings)


def test_dsr_double_count_preflight_when_buses_host_links():
    n = _hub_network()
    warnings = A.dsr_double_count_warnings(n, dsr_buses=["hub"])
    assert warnings
    assert any("hub" in w for w in warnings)


def test_apply_returns_pack_hash_metadata():
    n = _hub_network()
    pack = default_strong_grid_pack()
    result = A.apply_archetype_pack_detailed(n, pack)
    assert callable(result.undo)
    assert isinstance(result.pack_hash, str) and len(result.pack_hash) >= 8
    assert result.archetype == "strong_grid"
    result.undo()


@pytest.mark.live_solve
def test_strong_grid_ens_cap_binds_on_mini_network():
    """Acceptance: strong_grid path can bind ENS (not smoke-only).

    Economics from test_adequacy_ens_cap: voll below backup MC so uncapped
    LP sheds; ENS cap forces the backup on and binds. Pack apply is a no-op
    on strong_grid (spec §6) even with no import Links.
    """
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    weight, snaps, load_mw, cheap_mw = 3.0, 4, 100.0, 60.0
    voll, backup_mc = 150.0, 200.0
    cap_permyriad = 1000.0
    demand_mwh = load_mw * snaps * weight
    cap_mwh = cap_permyriad / 1e4 * demand_mwh

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=snaps, freq="h"))
    n.snapshot_weightings.loc[:, :] = weight
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=load_mw)
    n.add("Generator", "cheap", bus="b", carrier="gas",
          p_nom=cheap_mw, marginal_cost=10.0)
    n.add("Generator", "backup", bus="b", carrier="gas",
          p_nom=load_mw - cheap_mw, marginal_cost=backup_mc)

    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=cap_permyriad),
    })
    undo = A.apply_archetype_pack(n, pack)
    patch = A.solver_config_patch(pack)
    assert patch["ens_cap_permyriad"] == cap_permyriad

    PyPSAService.set_network(n)
    sink: dict = {}
    cfg = SolverConfig(voll=voll, ens_cap_permyriad=patch["ens_cap_permyriad"])
    try:
        status, condition = run_simulation(
            cfg, n, PyPSAService.get_lock(), threading.Event(),
            queue.SimpleQueue(), state_update=lambda **kw: sink.update(kw),
        )
    finally:
        undo()
    assert status in ("ok", "optimal"), (status, condition)
    report = sink.get("adequacy_report")
    assert report is not None
    assert report["target"]["energy_target_set"] is True
    assert report["target"]["binding"] == "system_cap"
    achieved = float(report["target"]["system"]["achieved_ens_mwh"])
    assert abs(achieved - cap_mwh) / cap_mwh < 1e-3


@pytest.mark.live_solve
def test_off_grid_and_weak_packs_still_solve_after_overlay():
    """Each archetype pack must leave a feasible mini-network (acceptance)."""
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    def _solve(n, pack):
        undo = A.apply_archetype_pack(n, pack)
        patch = A.solver_config_patch(pack)
        PyPSAService.set_network(n)
        cfg = SolverConfig(
            voll=3000.0,
            ens_cap_permyriad=patch.get("ens_cap_permyriad"),
        )
        try:
            status, condition = run_simulation(
                cfg, n, PyPSAService.get_lock(), threading.Event(),
                queue.SimpleQueue(), state_update=lambda **kw: None,
            )
        finally:
            undo()
        assert status in ("ok", "optimal"), (pack.archetype, status, condition)

    # Local firm capacity covers load so zeroing/capping import stays feasible.
    for pack in (default_off_grid_pack(), default_weak_flexible_pack()):
        n = _hub_network()
        n.generators.at["hub_peak", "p_nom"] = 80.0
        n.generators.at["hub_peak", "p_nom_extendable"] = False
        _solve(n, pack)
