"""
The import Link is a SAMPLED unit in the hub's MC / COPT fleet, not a firm
block (plan docs/superpowers/plans/2026-09-27-eh-zonal-mc-import-outages.md,
WP1).

After the 2026-09-26 fleet fix the hub side was certified with the import
counted as a deterministic firm block at the Link's planning cap. The Link
itself fails, though — the fixture's PoC Link carries ``outage_rate_value``
/ ``mttr_hours`` that the Class-B sweep already reads — so a firm block is
optimistic. Now: a Link with resolvable occurrence data joins the hub fleet
as a two-state unit (UP = the cap per snapshot), a Link without stays a firm
block, an islanded Link contributes nothing either way, and ``fleet_scope``
says which applied.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pytest

from models.energy_hub import (
    AvailabilityTarget,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import eh_stages as ST
from services.adequacy import eh_study as S
from services.adequacy.mc import mc_adequacy
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import (
    VOLL,
    certifiable_weak_network,
    firm_vs_sampled_import_pair,
    islanded_certify_network,
)

OVERLAY = default_strong_grid_pack().import_overlay
SEED = 7
DRAWS = 400


def _weak_capped(cap: float = 50.0):
    """
    The weak_flexible fixture with the pack's import cap applied by hand
    (no LP needed: every unit is non-extendable, so the plan IS the input).
    """
    n = certifiable_weak_network()
    n.links.at["import_poc", "p_nom"] = cap
    return n


def _freeze(n, **kw):
    return ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                overlay=OVERLAY, **kw)


def _lole(inputs) -> float:
    return float(mc_adequacy(inputs, draws=DRAWS, seed=SEED, cov_target=0.0,
                             max_draws=DRAWS)["lole_hours"])


# ── scope + snapshot ──────────────────────────────────────────────────────

def test_link_with_occurrence_data_is_a_sampled_unit_at_its_planning_cap():
    frozen = _freeze(_weak_capped(), import_model="sampled_unit")
    scope = frozen.scope
    assert scope["mode"] == "hub_side"
    assert scope["import_model"] == "sampled_unit"
    assert scope["import_firmness"] == "outage_sampled"
    assert scope["import_cap_mw_max"] == pytest.approx(50.0)
    # Nothing is a firm block: null, not 0 (ADR-0001).
    assert scope["import_firm_mw_max"] is None
    [lm] = scope["import_link_models"]
    assert lm["name"] == "import_poc" and lm["model"] == "sampled_unit"
    assert lm["q"] == pytest.approx(0.03)
    assert lm["mttr_hours"] == pytest.approx(48.0)
    assert lm["source"] == "asset"
    units = frozen.mc_inputs.units
    # Appended AFTER the hub's generators: every generator keeps its
    # positional CRN substream.
    assert [u.name for u in units] == ["base", "peaker", "link:import_poc"]
    link_u = units[-1]
    assert link_u.capacity_mw == pytest.approx(50.0)
    assert link_u.q == pytest.approx(0.03)
    assert link_u.capacity_series is None  # constant cap → scalar path
    # The residual is the hub's raw residual: nothing subtracted as firm.
    load = certifiable_weak_network().loads_t.p_set["hub_load"].to_numpy()
    np.testing.assert_allclose(frozen.mc_inputs.residual, load)
    assert frozen.zonal_inputs is None


def test_sampled_link_raises_lole_against_the_firm_block_on_the_same_seed():
    sampled = _freeze(_weak_capped(), import_model="sampled_unit")
    firm = _freeze(_weak_capped(), import_model="firm_block")
    assert firm.scope["import_model"] == "firm_block"
    assert firm.scope["import_firmness"] == "planning_limit_only"
    assert firm.scope["import_firm_mw_max"] == pytest.approx(50.0)
    assert [u.name for u in firm.mc_inputs.units] == ["base", "peaker"]
    assert _lole(sampled.mc_inputs) > _lole(firm.mc_inputs)


def test_link_without_occurrence_data_stays_a_firm_block():
    n = _weak_capped()
    for c in ("outage_rate_value", "outage_rate_basis", "mttr_hours"):
        n.links[c] = np.nan if c != "outage_rate_basis" else ""
    # v1 only: this fixture's grid side carries occurrence data, so "auto"
    # would add the zonal grid area in front of the firm Link (covered in
    # test_energy_hub_zonal_mc.py).
    auto = _freeze(n, import_model="sampled_unit")
    assert auto.scope["import_model"] == "firm_block"
    assert auto.scope["import_firmness"] == "planning_limit_only"
    assert auto.scope["import_link_models"][0]["model"] == "firm_block"
    assert "no occurrence data" in auto.scope["note"]
    forced = _freeze(_weak_capped(), import_model="firm_block")
    # Same fleet, same residual as the pre-change firm block → same LOLE.
    np.testing.assert_array_equal(auto.mc_inputs.residual,
                                  forced.mc_inputs.residual)
    assert _lole(auto.mc_inputs) == _lole(forced.mc_inputs)


def test_islanded_link_contributes_nothing_and_is_not_sampled():
    n = _weak_capped()
    n.links.at["import_poc", "p_max_pu"] = 0.0
    n.links.at["import_poc", "p_min_pu"] = 0.0
    auto = _freeze(n)
    assert auto.scope["import_model"] == "islanded"
    assert auto.scope["import_firm_mw_max"] == pytest.approx(0.0)
    assert auto.scope["import_link_models"][0]["model"] == "islanded"
    assert [u.name for u in auto.mc_inputs.units] == ["base", "peaker"]
    assert auto.zonal_inputs is None
    n2 = _weak_capped()
    n2.links.at["import_poc", "p_max_pu"] = 0.0
    n2.links.at["import_poc", "p_min_pu"] = 0.0
    forced = _freeze(n2, import_model="firm_block")
    assert _lole(auto.mc_inputs) == _lole(forced.mc_inputs)


def test_the_copt_screening_sees_the_same_fleet_as_the_mc():
    frozen = _freeze(_weak_capped(), import_model="sampled_unit")
    screened = {r["name"] for r in frozen.copt_rows}
    assert screened == {u.name for u in frozen.mc_inputs.units}
    assert frozen.scope["import_units"] == {"link:import_poc": "import_poc"}


def test_an_unusable_link_rate_refuses_the_snapshot_with_the_reason():
    n = _weak_capped()
    n.links.at["import_poc", "outage_rate_value"] = 1.5
    frozen = _freeze(n)
    assert frozen.mc_inputs is None
    assert "import_poc" in (frozen.mc_error or "")
    assert "[0, 1)" in frozen.mc_error


def test_the_fixture_pair_firm_vs_sampled_and_the_islanded_control():
    """
    The QA fixture pair: a reliable Link with q > 0 raises LOLE against
    the same hub with the Link's occurrence data cleared; islanding makes
    the two identical.
    """
    sampled_n, firm_n = firm_vs_sampled_import_pair()
    s = _freeze(sampled_n)
    f = _freeze(firm_n)
    assert s.scope["import_model"] in ("sampled_unit", "zonal")
    assert f.scope["import_model"] == "firm_block"
    assert _lole(s.mc_inputs) > _lole(f.mc_inputs)
    s_isl, f_isl = firm_vs_sampled_import_pair(islanded=True)
    a, b = _freeze(s_isl), _freeze(f_isl)
    assert a.scope["import_model"] == b.scope["import_model"] == "islanded"
    assert _lole(a.mc_inputs) == _lole(b.mc_inputs)


# ── through the driver ────────────────────────────────────────────────────

def _run(n, pack, *, stages, budget=30):
    from services.pypsa_service import PyPSAService

    PyPSAService.set_network(n)
    return S.run_eh_study(
        n, pack, SolverConfig(voll=VOLL),
        lock=PyPSAService.get_lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=stages,
        budget_solves=budget,
    )


def _weak_pack():
    return default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=10.0, target_lole_h=3.0,
            certification_metric="mc_lole"),
        "mc_draws": 100,
    })


@pytest.mark.live_solve
def test_weak_flexible_certification_samples_the_import():
    report = _run(certifiable_weak_network(), _weak_pack(),
                  stages=("apply_pack", "ens_solve", "mc_certify", "assemble"))
    cert = report.sections["certification"].payload
    assert cert["import_model"] in ("sampled_unit", "zonal")
    assert cert["import_firmness"] != "planning_limit_only"
    assert cert["fleet_scope"]["import_cap_mw_max"] == pytest.approx(50.0)
    assert report.mc_lole_h is not None


@pytest.mark.live_solve
def test_fmea_top_ranks_the_import_link_once_by_its_class_b_row():
    report = _run(certifiable_weak_network(), _weak_pack(),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"))
    sec = report.sections["fmea_top"].payload
    # Merge 2026-09-28: Class-B ranking in ``rows``, class A in ``class_a``.
    modes = sec["rows"] + sec["class_a"]["rows"]
    link_rows = [m for m in modes if m["name"] in ("import_poc",
                                                   "link:import_poc")]
    assert len(link_rows) == 1
    assert link_rows[0]["failure_class"] == "B"
    assert sec["class_a"]["import_link_ranking"] == {"import_poc": "class_b"}


@pytest.mark.live_solve
def test_fmea_top_keeps_the_class_a_link_row_when_class_b_cannot_run():
    report = _run(certifiable_weak_network(), _weak_pack(),
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"),
                  budget=2)
    sec = report.sections["fmea_top"].payload
    # Merge 2026-09-28: the Class-B sweep that does not fit is withheld (P12,
    # not_established); the class-A block still ranks the Link — once.
    assert report.completeness["fmea_top"] == "not_established"
    assert sec["rows"] == []
    link_rows = [m for m in sec["class_a"]["rows"]
                 if m["component_class"] == "Link"]
    assert len(link_rows) == 1
    row = link_rows[0]
    assert row["failure_class"] == "A" and row["name"] == "import_poc"
    assert row["mode_id"] == "link:import_poc:forced_outage"
    assert sec["class_a"]["import_link_ranking"] == {"import_poc": "class_a"}


@pytest.mark.live_solve
def test_off_grid_islanded_hub_certification_is_unchanged():
    from tests.test_energy_hub_certify_scope import (
        _islanded_hub_with_grid_behind_poc,
        _off_grid_pack,
    )
    report = _run(_islanded_hub_with_grid_behind_poc(), _off_grid_pack(),
                  stages=("apply_pack", "ens_solve", "mc_certify", "assemble"))
    cert = report.sections["certification"].payload
    assert cert["import_model"] == "islanded"
    assert cert["verdict"] == "fail"   # P11 vocabulary (merge 2026-09-28)


def test_islanded_certify_network_link_has_no_occurrence_data():
    """Premise of the off_grid fixture: nothing to sample on its PoC."""
    n = islanded_certify_network()
    frozen = _freeze(n)
    assert frozen.scope["import_model"] == "firm_block"
