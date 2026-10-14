"""A connection energized in steps, in absolute MW (gatekeeper review of #266).

The schedule E(t) composes with whatever p_nom a consumer writes: the
import_cap lever's rungs at 20, 30 and 60 MW read 20/20/20 in the 20 MW year
and 20/30/40 later, never a rescaled fraction. The MC import credit takes the
horizon minimum.
"""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pytest

from services.adequacy import energization as E

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

YEARS = (2027, 2030, 2035)


def _staged():
    return T.BUILDERS["eh_datacenter_staged"]()


def _cap_by_period(n, link="grid_import"):
    p_nom = float(n.links.at[link, "p_nom"])
    pu = n.links_t.p_max_pu[link]
    return {y: round(float((pu.loc[y] * p_nom).max()), 6) for y in YEARS}


def test_the_template_stores_the_schedule_in_mw_not_as_a_fraction():
    n = _staged()
    assert "grid_import" not in n.links_t.p_max_pu.columns
    e = E.energization_mw(n, "grid_import")
    by = {y: set(np.round(e[n.snapshots.get_level_values(0) == y], 6)) for y in YEARS}
    assert by == {2027: {20.0}, 2030: {40.0}, 2035: {40.0}}


def test_applied_for_a_solve_and_undone_after():
    n = _staged()
    undo = E.apply_energization(n)
    assert _cap_by_period(n) == {2027: 20.0, 2030: 40.0, 2035: 40.0}
    undo()
    undo()                                         # idempotent
    assert "grid_import" not in n.links_t.p_max_pu.columns


@pytest.mark.parametrize("rung, want", [
    (20.0, {2027: 20.0, 2030: 20.0, 2035: 20.0}),
    (30.0, {2027: 20.0, 2030: 30.0, 2035: 30.0}),
    (60.0, {2027: 20.0, 2030: 40.0, 2035: 40.0}),
])
def test_a_rewritten_p_nom_composes_as_min_of_schedule_and_cap(rung, want):
    """The import_cap lever writes p_nom = rung; the solve hook then caps at
    min(E, rung)."""
    from services.adequacy.levers import apply_lever_scenario

    n = _staged()
    undo_lever, _mutation = apply_lever_scenario(n, "import_cap", value=rung)
    undo = E.apply_energization(n)
    assert _cap_by_period(n) == want
    undo()
    undo_lever()


def test_an_existing_tighter_profile_still_wins():
    n = _staged()
    n.links_t.p_max_pu["grid_import"] = 0.25           # 10 MW of 40, everywhere
    undo = E.apply_energization(n)
    assert _cap_by_period(n) == {2027: 10.0, 2030: 10.0, 2035: 10.0}
    undo()
    assert np.allclose(n.links_t.p_max_pu["grid_import"], 0.25)


def test_the_firm_credit_is_the_horizon_minimum():
    n = _staged()
    assert E.firm_energized_mw(n, "grid_import") == pytest.approx(20.0)
    assert E.firm_energized_mw(n, "site_transformer") is None


def test_a_network_without_a_schedule_is_untouched():
    n = T.BUILDERS["eh_datacenter"]()
    before = n.links_t.p_max_pu.copy()
    E.apply_energization(n)()
    assert E.energized_links(n) == []
    assert n.links_t.p_max_pu.equals(before)


def test_the_schedule_survives_the_netcdf_round_trip(tmp_path):
    import pypsa

    n = _staged()
    n.export_to_netcdf(str(tmp_path / "n.nc"))
    m = pypsa.Network(str(tmp_path / "n.nc"))
    assert np.allclose(E.energization_mw(m, "grid_import"), E.energization_mw(n, "grid_import"))


def test_the_mc_import_credit_is_the_horizon_minimum_with_a_label():
    """MC certification credits the import as one two-state unit; a staged
    connection is firm only at its 20 MW minimum (not the 33 MW mean)."""
    from services.adequacy.archetypes import _import_capacity_to_hub

    n = _staged()
    far = {str(n.links.at["grid_import", "bus0"])}
    assert _import_capacity_to_hub(n, "grid_import", far) == pytest.approx(
        20.0 * float(n.links.at["grid_import", "efficiency"]))
    flat = T.BUILDERS["eh_datacenter"]()
    assert _import_capacity_to_hub(flat, "grid_import", far) == pytest.approx(
        float(flat.links.at["grid_import", "p_nom"]) * float(flat.links.at["grid_import", "efficiency"]))


@pytest.mark.live_solve
def test_run_simulation_applies_the_schedule_and_undoes_it():
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    n = _staged()
    PyPSAService.set_network(n)
    cfg = SolverConfig(**{k: v for k, v in T.solver_config("eh_datacenter_staged").items()
                          if k != "island_constraints"})
    status, _ = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                               queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal")
    flow = n.links_t.p0["grid_import"]
    assert flow.loc[2027].max() <= 20.0 + 1e-6
    assert flow.loc[2035].max() > 20.0 + 1e-6        # the full 40 MW once energized
    assert "grid_import" not in n.links_t.p_max_pu.columns   # undone after the solve


def test_readiness_reports_the_firm_import_beside_the_rating():
    from services.adequacy.eh_readiness import _import_firm_mw

    n = _staged()
    assert _import_firm_mw(n, ["grid_import"]) == pytest.approx(20.0)
    assert _import_firm_mw(T.BUILDERS["eh_datacenter"](), ["grid_import"]) is None
