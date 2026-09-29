"""
Merge of master into the EH branch (2026-09-28) — the ported capabilities.

Decisions: docs/superpowers/qa/2026-09-28-merge-master-decisions.md.

The branch's stage contract (P11/P12: verdict on the CI, private copies,
budget share, decision-6 hub boundary) stays; master's PR #53/#55 capabilities
come in on top of it:

* mc_certify samples a counted import Link at its HOURLY cap and, when the
  grid behind it carries occurrence data, the grid as its own area (zonal
  MC); a Link without its own outage data is NOT counted (decision 6) — never
  a firm block;
* fmea_top carries a class-A COPT screening block beside the Class-B ranking;
* the frontier sweeps the pack's ``frontier_ladder`` around the target;
* the pack's ``mc_*`` fields drive the MC unless the request overrides them;
* the TEA carries LCOH.
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
from services.adequacy import archetypes as A
from services.adequacy import eh_stages as ST
from services.adequacy import eh_study as S
from services.solver_service import SolverConfig
from tests.eh_stage_fixtures import (
    VOLL,
    certifiable_weak_network,
    firm_vs_sampled_import_pair,
)

CERT = ("apply_pack", "ens_solve", "mc_certify", "assemble")


def _weak(target: float = 8760.0, **upd):
    return default_weak_flexible_pack().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=10.0, target_lole_h=target,
            certification_metric="mc_lole"),
        "mc_draws": 50, **upd})


def _run(n, pack, *, stages=CERT, cfg=None, **kw):
    from services.pypsa_service import PyPSAService

    PyPSAService.set_network(n)
    kw.setdefault("stop_event", threading.Event())
    return S.run_eh_study(
        n, pack, cfg or SolverConfig(voll=VOLL), lock=PyPSAService.get_lock(),
        log_queue=queue.SimpleQueue(), stages=stages, **kw)


# ── the fleet: the P11 boundary decides, PR #55 samples ─────────────────────


def _boundary(n, pack):
    undo = A.apply_archetype_pack(n, pack)
    try:
        _mc, info = A.hub_boundary_copy(n, pack)
        scope = ST.hub_fleet_scope(n, pack.import_overlay, boundary=info)
    finally:
        undo()
    return info, scope


def test_a_link_without_outage_data_is_not_counted_never_firm():
    _with, without = firm_vs_sampled_import_pair()
    info, scope = _boundary(without, _weak())
    assert info["excluded_import_links"][0]["link"] == "import_poc"
    (m,) = scope.link_models
    assert m.model == "excluded" and "decision 6" in (m.reason or "")
    assert scope.import_model() == "excluded"
    assert scope.import_firmness() == "not_counted"
    # Nothing of it reaches the MC: no firm block netted, no unit, no cap.
    assert float(np.max(scope.import_firm_mw)) == 0.0
    assert float(np.max(scope.import_cap_mw)) == 0.0
    assert scope.import_units == []
    # Master's default (no boundary) still reads it as a firm block.
    assert ST.hub_fleet_scope(without, _weak().import_overlay).import_model() \
        == "firm_block"


def test_a_link_with_its_own_outage_data_is_a_sampled_unit():
    with_data, _without = firm_vs_sampled_import_pair()
    _info, scope = _boundary(with_data, _weak())
    (m,) = scope.link_models
    assert m.model == "sampled_unit"
    assert [u.name for u in scope.import_units] == ["link:import_poc"]


def test_an_energy_limited_import_is_not_counted():
    with_data, _without = firm_vs_sampled_import_pair()
    pack = _weak()
    pack = pack.model_copy(update={"import_overlay": pack.import_overlay.model_copy(
        update={"import_energy_mwh_per_year": 1000.0})})
    info, scope = _boundary(with_data, pack)
    assert "energy budget" in info["excluded_import_links"][0]["reason"]
    assert scope.link_models[0].model == "excluded"
    assert scope.import_units == []


def test_the_boundary_sides_are_the_p11_ones():
    n = certifiable_weak_network()
    info, scope = _boundary(n, _weak())
    assert scope.mode == "hub_side"
    assert scope.excluded_buses == info["removed_buses"] == ["grid"]


@pytest.mark.live_solve
def test_weak_flexible_certifies_through_the_grid_area():
    """The PoC Link carries outage data and so does the grid behind it:
    the two-area engine certifies (PR #55) with the P11 verdict contract."""
    report = _run(certifiable_weak_network(), _weak())
    sec = report.sections["certification"]
    assert sec.status == "ok", sec.note
    p = sec.payload
    assert p["import_model"] == "zonal"
    assert p["import_firmness"] == "outage_and_grid_sampled"
    assert p["engine"] == "mc_zonal"
    assert p["verdict"] in ("pass", "fail", "inconclusive")
    assert p["mc_lole_h"] == report.mc_lole_h == p["lole_h_per_year"]
    assert p["fleet_boundary"]["rule"] == "eh_role"
    assert p["ens_met"] is True
    assert report.pipeline.solves_consumed == 1


@pytest.mark.live_solve
def test_a_hub_without_link_data_certifies_on_its_own_fleet_only():
    _with, without = firm_vs_sampled_import_pair()
    report = _run(without, _weak())
    p = report.sections["certification"].payload
    assert p["import_model"] == "excluded"
    assert p["engine"] == "mc"


# ── fmea_top: class-A block beside the Class-B ranking ──────────────────────


@pytest.mark.live_solve
def test_fmea_top_carries_the_class_a_screening_beside_class_b():
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=10.0)})
    report = _run(certifiable_weak_network(), pack,
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"))
    sec = report.sections["fmea_top"]
    assert sec.status == "ok", sec.note
    p = sec.payload
    assert all(r["failure_class"] == "B" for r in p["rows"])
    a = p["class_a"]
    assert a["status"] == "ok" and a["engine"] == "copt"
    assert a["solves_charged"] == 0
    assert a["rows"] and all(r["failure_class"] == "A" for r in a["rows"])
    # The sampled PoC Link is ranked ONCE — by its Class-B row.
    assert a["import_link_ranking"] == {"import_poc": "class_b"}
    assert not any(r["name"] in ("import_poc", "link:import_poc")
                   for r in a["rows"])
    # The class-A block charges nothing: base + 1 Link, no restore.
    rec = next(s for s in report.pipeline.stages if s.stage == "fmea_top")
    assert rec.solves_charged == 2


@pytest.mark.live_solve
def test_the_pack_top_n_bounds_both_rankings():
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=10.0),
        "fmea_top_n": 1})
    report = _run(certifiable_weak_network(), pack,
                  stages=("apply_pack", "ens_solve", "fmea_top", "assemble"))
    p = report.sections["fmea_top"].payload
    assert p["top_n"] == 1 and len(p["rows"]) <= 1
    assert len(p["class_a"]["rows"]) <= 1


# ── frontier ladder / MC options / LCOH ─────────────────────────────────────


def test_frontier_targets_come_from_the_pack_ladder():
    assert S.frontier_targets(10.0, 5) == pytest.approx([10.0, 20.0, 5.0, 40.0, 2.5])
    assert S.frontier_targets(10.0, 2, ladder=(3.0, 1.0, 0.5)) == pytest.approx(
        [10.0, 5.0])
    # ×1 is always a point, whether or not the ladder lists it.
    assert S.frontier_targets(10.0, 3, ladder=(2.0, 0.5)) == pytest.approx(
        [10.0, 20.0, 5.0])


@pytest.mark.live_solve
def test_the_request_mc_options_override_the_pack():
    pack = _weak(mc_draws=40, mc_seed=3)
    by_pack = _run(certifiable_weak_network(), pack)
    assert by_pack.sections["certification"].payload["draws"] == 40
    assert by_pack.sections["certification"].payload["seed"] == 3
    by_req = _run(certifiable_weak_network(), pack, mc_draws=60, mc_seed=5)
    assert by_req.sections["certification"].payload["draws"] == 60
    assert by_req.sections["certification"].payload["seed"] == 5


def test_default_mc_draws_are_the_p13_study_default():
    assert default_weak_flexible_pack().mc_draws == S.DEFAULT_MC_DRAWS == 500


@pytest.mark.live_solve
def test_the_tea_carries_the_lcoh_flag_on_the_private_copy():
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=10.0)})
    report = _run(certifiable_weak_network(), pack,
                  stages=("apply_pack", "ens_solve", "assemble"))
    tea = report.sections["tea"].payload
    assert tea["lcoh_eur_per_kg"] is None
    assert tea["lcoh_status"] == "skipped"
