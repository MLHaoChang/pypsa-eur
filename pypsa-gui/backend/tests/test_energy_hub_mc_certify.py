"""
P11 — mc_certify stage (spec decisions 1–2, §4 amendment; plan P11).

Certifying fixtures run ≥ 168 h with every unit's MTTR (gas default 50 h)
inside the horizon — the MVP-B 4 h / 12 h fixtures are refused by design (Q2).
"""
from __future__ import annotations

import queue
import threading
import time

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    EH_PIPELINE_STAGES,
    AvailabilityTarget,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import archetypes as A
from services.adequacy import eh_study as S
from services.adequacy import mc as MC
from services.solver_service import SolverConfig

CERT_STAGES = ("apply_pack", "ens_solve", "mc_certify", "assemble")


def _cert_network(*, hours: int = 168, units: int = 4, unit_mw: float = 40.0,
                  load_mw: float = 100.0, tag: str = "role") -> pypsa.Network:
    """Hub (bus `hub`) behind one grid-import Link from `grid`.

    `units` local gas units (carrier default: EFORd 0.05, MTTR 50 h); a 300 MW
    `remote` gas plant on the far side — which the copper-plate MC must NOT
    count once the import boundary is applied.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=hours, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Carrier", "AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "hub_load", bus="hub", p_set=load_mw)
    for i in range(units):
        n.add("Generator", f"local{i}", bus="hub", carrier="gas",
              p_nom=unit_mw, marginal_cost=50.0)
    n.add("Generator", "remote", bus="grid", carrier="gas",
          p_nom=300.0, marginal_cost=10.0)
    n.add("Link", "import", bus0="grid", bus1="hub", p_nom=100.0,
          p_nom_max=100.0, efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.buses["eh_poc"] = False
    n.buses["eh_critical"] = False
    n.buses.at["hub", "eh_critical"] = True
    if tag == "role":
        n.links.at["import", "eh_role"] = "grid_import"
    elif tag == "poc":
        n.buses.at["grid", "eh_poc"] = True
    return n


def _pack(kind: str = "off_grid", *, target: float | None = 3.0,
          ens: float = 5000.0):
    factory = {"off_grid": default_off_grid_pack,
               "weak_flexible": default_weak_flexible_pack,
               "strong_grid": default_strong_grid_pack}[kind]
    return factory().model_copy(update={
        "availability": AvailabilityTarget(
            ens_cap_permyriad=ens, target_lole_h=target,
            certification_metric="mc_lole" if target is not None else "none"),
    })


def _run(n, pack, *, stages=CERT_STAGES, cfg=None, **kw):
    from services.pypsa_service import PyPSAService
    PyPSAService.set_network(n)
    kw.setdefault("stop_event", threading.Event())
    return S.run_eh_study(
        n, pack, cfg or SolverConfig(voll=5000.0),
        lock=PyPSAService.get_lock(), log_queue=queue.SimpleQueue(),
        stages=stages, **kw)


def _cert(report):
    return report.sections["certification"]


# ── fleet boundary (B1 / R1 / Q7) ───────────────────────────────────────────


@pytest.mark.live_solve
def test_off_grid_certifies_on_the_hub_side_only():
    n = _cert_network()
    report = _run(n, _pack())
    sec = _cert(report)
    assert sec.status == "ok", sec.note
    fb = sec.payload["fleet_boundary"]
    assert "remote" in fb["removed_generators"]
    assert fb["hub_buses"] == ["hub"]
    # Same LOLE as the network with the grid side deleted by hand.
    manual = _cert_network()
    manual.remove("Link", ["import"])
    manual.remove("Generator", ["remote"])
    manual.remove("Bus", ["grid"])
    inputs = MC.snapshot_inputs(manual)
    assert "remote" not in {u.name for u in inputs.units}
    ref = MC.mc_adequacy(inputs, draws=S.DEFAULT_MC_DRAWS,
                         seed=S.DEFAULT_MC_SEED,
                         cov_target=S.DEFAULT_MC_COV_TARGET)
    assert sec.payload["lole_h_per_horizon"] == pytest.approx(ref["lole_hours"])


@pytest.mark.live_solve
def test_eh_poc_tagging_establishes_the_same_boundary():
    report = _run(_cert_network(tag="poc"), _pack())
    fb = _cert(report).payload["fleet_boundary"]
    assert fb["rule"] == "eh_poc"
    assert fb["removed_buses"] == ["grid"]


@pytest.mark.live_solve
def test_carrier_only_selection_is_refused_with_a_tagging_instruction():
    report = _run(_cert_network(tag="none"), _pack())
    sec = _cert(report)
    assert sec.status == "not_established"
    assert "eh_role" in (sec.note or "") and "eh_poc" in (sec.note or "")
    assert report.certified is None


def test_every_component_holding_a_poc_bus_is_ambiguous():
    n = _cert_network(tag="poc")
    n.buses.at["hub", "eh_poc"] = True
    with pytest.raises(A.HubBoundaryError, match="ambiguous"):
        A.hub_boundary_copy(n, _pack())


def test_import_link_without_outage_data_is_excluded_from_the_mc_fleet():
    mc, info = A.hub_boundary_copy(_cert_network(), _pack("weak_flexible"))
    assert info["import_units"] == []
    assert info["excluded_import_links"][0]["link"] == "import"
    assert "decision 6" in info["excluded_import_links"][0]["reason"]
    assert not any(str(g).startswith(A.IMPORT_UNIT_PREFIX)
                   for g in mc.generators.index)


def test_import_link_with_outage_data_enters_as_one_two_state_unit():
    n = _cert_network()
    n.links["outage_rate_value"] = float("nan")
    n.links["mttr_hours"] = float("nan")
    n.links.at["import", "outage_rate_value"] = 0.02
    n.links.at["import", "mttr_hours"] = 10.0
    pack = _pack("weak_flexible")
    undo = A.apply_archetype_pack(n, pack)          # import capped at 50 MW
    try:
        mc, info = A.hub_boundary_copy(n, pack)
    finally:
        undo()
    (unit,) = info["import_units"]
    assert unit["capacity_mw"] == pytest.approx(50.0)
    assert unit["bus"] == "hub"
    names = {u.name for u in MC.snapshot_inputs(mc).units}
    assert f"{A.IMPORT_UNIT_PREFIX}import" in names
    assert "remote" not in names


def test_off_grid_closed_import_never_enters_even_with_outage_data():
    n = _cert_network()
    n.links["outage_rate_value"] = 0.02
    n.links["mttr_hours"] = 10.0
    pack = _pack("off_grid")
    undo = A.apply_archetype_pack(n, pack)
    try:
        _, info = A.hub_boundary_copy(n, pack)
    finally:
        undo()
    assert info["import_units"] == []
    assert "closed" in info["excluded_import_links"][0]["reason"]


# ── verdict (Q1) and decision 2 ─────────────────────────────────────────────


@pytest.mark.live_solve
def test_lole_failure_fails_certification_even_when_ens_is_met():
    # 4 × 40 MW gas for 100 MW: two outages already short → LOLE ≫ 3 h/yr.
    report = _run(_cert_network(units=4), _pack(target=3.0))
    assert report.completeness["target"] == "ok"            # ENS met
    sec = _cert(report)
    assert sec.status == "ok"
    assert sec.payload["verdict"] == "fail"
    assert sec.payload["lole_ci"][0] > sec.payload["target_lole_h_per_horizon"]
    assert report.certified is False
    assert report.mc_lole_h == pytest.approx(
        sec.payload["lole_h_per_horizon"] / sec.payload["horizon_years"])


@pytest.mark.live_solve
def test_a_comfortable_fleet_passes_with_ci_under_the_target():
    report = _run(_cert_network(units=8), _pack(target=3.0))
    sec = _cert(report)
    assert sec.payload["verdict"] == "pass", sec.payload
    assert sec.payload["confident"] is True
    assert report.certified is True


@pytest.mark.live_solve
def test_a_target_below_the_resolution_floor_is_inconclusive():
    report = _run(_cert_network(units=8), _pack(target=1e-9))
    sec = _cert(report)
    assert sec.payload["verdict"] == "inconclusive"
    assert "resolution floor" in (sec.note or "")
    assert report.certified is False


@pytest.mark.live_solve
def test_no_lole_target_reports_lole_without_a_verdict():
    pack = _pack(target=None)
    report = _run(_cert_network(), pack)
    sec = _cert(report)
    assert sec.status == "ok"
    assert sec.payload["verdict"] is None
    assert report.certified is None
    assert report.mc_lole_h is not None


# ── time basis (Q2) ─────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_a_horizon_shorter_than_the_largest_mttr_is_refused():
    report = _run(_cert_network(hours=12), _pack())
    sec = _cert(report)
    assert sec.status == "not_established"
    assert "MTTR" in (sec.note or "")
    assert report.certified is None and report.mc_lole_h is None


@pytest.mark.live_solve
def test_target_is_compared_per_horizon():
    report = _run(_cert_network(units=4), _pack(target=3.0))
    p = _cert(report).payload
    assert p["target_basis"] == "h_per_year"
    assert p["target_unit"] == "h"
    assert p["target_lole_h_per_horizon"] == pytest.approx(
        3.0 * p["horizon_years"])
    assert p["horizon_years"] == pytest.approx(168 / 8760)


# ── gating, budget, abort, determinism (B3 / B5) ────────────────────────────


@pytest.mark.live_solve
def test_budget_one_still_certifies():
    report = _run(_cert_network(), _pack(), stages=None, budget_solves=1)
    assert report.pipeline.solves_consumed == 1
    assert _cert(report).status == "ok"
    mc = next(r for r in report.pipeline.stages if r.stage == "mc_certify")
    assert mc.status == "run" and mc.solves_charged == 0


@pytest.mark.live_solve
def test_mc_charges_no_lp_solve():
    report = _run(_cert_network(), _pack())
    assert report.pipeline.solves_consumed == 1


@pytest.mark.live_solve
def test_certification_is_deterministic_for_a_pinned_seed():
    a = _cert(_run(_cert_network(), _pack(), mc_seed=7)).payload
    b = _cert(_run(_cert_network(), _pack(), mc_seed=7)).payload
    assert a["lole_h_per_horizon"] == b["lole_h_per_horizon"]
    assert a["seed"] == 7


@pytest.mark.live_solve
def test_abort_during_mc_leaves_no_verdict(monkeypatch):
    stop = threading.Event()
    real = MC.mc_adequacy

    def aborting(inputs, **kw):
        stop.set()
        return real(inputs, **kw)

    monkeypatch.setattr(MC, "mc_adequacy", aborting)
    report = _run(_cert_network(), _pack(), stages=None, stop_event=stop)
    assert report.pipeline.aborted is True
    assert _cert(report).status == "not_established"
    assert report.certified is None
    statuses = {r.stage: r.status for r in report.pipeline.stages}
    assert statuses["mc_certify"] == "aborted"
    assert "pending" not in statuses.values()


@pytest.mark.live_solve
def test_default_pipeline_certifies_only_packs_that_ask_for_it():
    strong = _run(_cert_network(), _pack("strong_grid", target=None),
                  stages=None)
    assert strong.completeness["certification"] == "skipped"
    assert strong.certified is None
    off = _run(_cert_network(), _pack("off_grid"), stages=None)
    assert off.completeness["certification"] == "ok"
    # off_grid has no SCR gate — no stale "mc not implemented" text there.
    assert off.completeness["gates"] == "skipped"


@pytest.mark.live_solve
def test_stages_execute_in_decision_18_order(monkeypatch):
    order: list[str] = []
    for name, fn in list(S._STAGE_HANDLERS.items()):
        def wrapped(st, _fn=fn, _name=name):
            order.append(_name)
            return _fn(st)
        monkeypatch.setitem(S._STAGE_HANDLERS, name, wrapped)
    _run(_cert_network(), _pack("off_grid"), stages=None)
    assert order[:3] == ["apply_pack", "ens_solve", "mc_certify"]
    assert order == sorted(order, key=EH_PIPELINE_STAGES.index)


@pytest.mark.live_solve
def test_dsr_on_adds_the_pessimism_note():
    n = _cert_network(units=8)
    report = _run(n, _pack("weak_flexible", ens=5000.0), dsr_buses=["hub"])
    assert _cert(report).payload["dsr_note"]


def test_redundancy_discloses_that_finalists_are_not_mc_certified():
    from services.adequacy import redundancy as R
    from services.pypsa_service import PyPSAService
    from tests.test_energy_hub_study import _ens_bind_network

    n = _ens_bind_network()
    PyPSAService.set_network(n)
    table = R.compare_redundancy_scenarios(
        n, SolverConfig(voll=150.0), lock=PyPSAService.get_lock(),
        stop_event=threading.Event(), log_queue=queue.SimpleQueue(),
        scenarios=["base"],
        availability=AvailabilityTarget(ens_cap_permyriad=1000.0))
    assert table["finalists_mc_certified"] is False
    assert R.MC_CERTIFY_CADENCE == "finalists_only"      # pin unchanged


# ── HTTP (unstubbed) ────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_http_off_grid_record_carries_the_certification(client, install_network):
    install_network(_cert_network())
    r = client.put("/api/simulation/solver_config",
                   json={"solver_name": "highs", "voll": 5000.0})
    assert r.status_code == 200, r.text
    r = client.post("/api/results/eh_study", json={"archetype": "off_grid"})
    assert r.status_code == 200, r.text
    deadline = time.time() + 120
    while time.time() < deadline:
        body = client.get("/api/results/eh_study").json()
        if body["status"] != "running":
            break
        time.sleep(0.1)
    assert body["status"] == "done", body.get("error")
    rep = body["report"]
    assert rep["completeness"]["certification"] == "ok"
    assert rep["certified"] is False          # 4 × 40 MW vs 3 h/yr
    exported = client.get("/api/results/eh_reference_design").json()
    assert "certified" in exported and exported["certified"] is False


# ── P11 gate B1: the boundary must never silently include the grid ──────────


@pytest.mark.parametrize("parallel", ["line", "untagged_link"])
def test_import_links_that_do_not_split_the_network_are_refused(parallel):
    n = _cert_network()
    if parallel == "line":
        n.add("Line", "tie", bus0="grid", bus1="hub", x=0.1, s_nom=100.0)
    else:
        n.add("Link", "second", bus0="grid", bus1="hub", p_nom=50.0,
              carrier="DC")
    with pytest.raises(A.HubBoundaryError, match="does not separate"):
        A.hub_boundary_copy(n, _pack())


def test_import_links_that_do_not_split_the_network_refused_without_critical():
    n = _cert_network()
    n.buses["eh_critical"] = False
    n.add("Line", "tie", bus0="grid", bus1="hub", x=0.1, s_nom=100.0)
    with pytest.raises(A.HubBoundaryError, match="does not separate"):
        A.hub_boundary_copy(n, _pack())


def test_poc_tagged_on_the_hub_side_is_refused_not_inverted():
    """eh_poc on the HUB bus would make the grid the 'hub' — and certify the
    remote plant with zero local load (gate probe P4: certified=True)."""
    n = _cert_network(tag="poc")
    n.buses.at["grid", "eh_poc"] = False
    n.buses.at["hub", "eh_poc"] = True
    with pytest.raises(A.HubBoundaryError):
        A.hub_boundary_copy(n, _pack())


def test_a_hub_with_no_load_is_refused():
    n = _cert_network(tag="poc")
    n.buses.at["hub", "eh_critical"] = False
    n.loads.at["hub_load", "bus"] = "grid"          # all demand beyond the PoC
    with pytest.raises(A.HubBoundaryError, match="no load"):
        A.hub_boundary_copy(n, _pack())


@pytest.mark.live_solve
def test_wrong_side_poc_never_certifies_end_to_end():
    n = _cert_network(tag="poc")
    n.buses.at["grid", "eh_poc"] = False
    n.buses.at["hub", "eh_poc"] = True
    report = _run(n, _pack())
    assert report.completeness["certification"] == "not_established"
    assert report.certified is None


# ── non-binding gate items ──────────────────────────────────────────────────


@pytest.mark.live_solve
def test_a_ci_straddling_the_target_is_inconclusive():
    first = _cert(_run(_cert_network(units=4), _pack(target=3.0))).payload
    # Target exactly at the mean: the CI (non-degenerate here) straddles it.
    assert first["lole_ci"][0] < first["lole_h_per_horizon"] < first["lole_ci"][1]
    report = _run(_cert_network(units=4),
                  _pack(target=first["lole_h_per_year"]))
    sec = _cert(report)
    assert sec.payload["verdict"] == "inconclusive"
    assert "straddles" in (sec.note or "")
    assert report.certified is False


@pytest.mark.live_solve
@pytest.mark.parametrize("gate", ["metric_only", "target_only", "required_only"])
def test_default_pipeline_certification_gating(gate):
    base = _pack("strong_grid", target=None)
    if gate == "metric_only":
        pack = base.model_copy(update={"availability": AvailabilityTarget(
            ens_cap_permyriad=5000.0, certification_metric="mc_lole")})
    elif gate == "target_only":
        pack = base.model_copy(update={"availability": AvailabilityTarget(
            ens_cap_permyriad=5000.0, target_lole_h=3.0,
            certification_metric="none")})
    else:
        pack = base.model_copy(update={"mc_certify_required": True})
    assert S.certification_wanted(pack)
    report = _run(_cert_network(), pack, stages=None)
    assert "mc_certify" in [r.stage for r in report.pipeline.stages
                            if r.status == "run"]


@pytest.mark.live_solve
def test_a_different_seed_draws_a_different_sample():
    a = _cert(_run(_cert_network(), _pack(), mc_seed=1)).payload
    b = _cert(_run(_cert_network(), _pack(), mc_seed=2)).payload
    assert a["lole_h_per_horizon"] != b["lole_h_per_horizon"]


@pytest.mark.live_solve
def test_an_unexpected_mc_error_degrades_to_not_established(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(MC, "mc_adequacy", boom)
    report = _run(_cert_network(), _pack(), stages=None)
    sec = _cert(report)
    assert sec.status == "not_established"
    assert "engine exploded" in (sec.note or "")
    # The rest of the study still stands.
    assert report.completeness["target"] == "ok"


@pytest.mark.live_solve
def test_abort_before_mc_certify_leaves_a_reason_on_the_section():
    class _AbortAfterSolve(threading.Event):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 2        # apply_pack + ens_solve pass

    report = _run(_cert_network(), _pack(), stages=None,
                  stop_event=_AbortAfterSolve())
    assert report.pipeline.aborted is True
    sec = _cert(report)
    assert sec.status == "not_established"
    assert "abort" in (sec.note or "")


# ── template-e2e 21: the floor and the fewest draws (GK A V1) ───────────────
# `_cert_network` is 168 hourly snapshots (weight 1 h): the horizon is
# 168/8760 yr, a 3 h/yr goal is 0.05753 h on it, and one draw resolves 1 h,
# so 1/18 = 0.0556 ≤ 0.0575 < 1/17 = 0.0588: 18 draws, as on a 168 h template.


@pytest.mark.live_solve
def test_pre_run_weights_match_the_runs_floor():
    """The study start's w_min (`copt.fleet_weights`) is what the run's floor
    reports: floor × n_samples."""
    from services.adequacy.copt import fleet_weights
    n = _cert_network(units=8)
    w = fleet_weights(n)
    report = _run(n, _pack(target=3.0), mc_draws=1)
    p = _cert(report).payload
    assert float(w[w > 0].min()) == 1.0
    assert p["resolution_floor_h"] * p["n_samples"] == pytest.approx(1.0)
    # Owner decision on #1927: 1 draw below the floor still runs (above);
    # only a goal 2000 draws cannot resolve is refused: 1 h / 2000 on the
    # 168 h horizon is 0.0261 h/yr.
    assert S.draws_short_of_target(n, _pack(target=3.0), 1) is None
    assert S.draws_short_of_target(n, _pack(target=0.01), 1) == (
        "Simulation runs per check: a check that finds no shortfall cannot "
        "show that the 0.01 h/yr goal is met, and even 2000 runs (the most "
        "allowed) could not decide it (its smallest step there is 0.0261 "
        "h/yr): allow more shortfall in the goal.")
    assert S.draws_short_of_target(n, _pack(target=0.03), 1) is None


@pytest.mark.live_solve
@pytest.mark.parametrize("draws,below,min_draws", [(17, True, 18), (18, False, None)])
def test_the_minimum_draws_put_the_floor_at_the_target(draws, below, min_draws):
    report = _run(_cert_network(units=8), _pack(target=3.0), mc_draws=draws)
    p = _cert(report).payload
    assert p["n_samples"] == draws          # an all-zero first batch converges
    assert (p["resolution_floor_h"] > p["target_lole_h_per_horizon"]) is below
    assert p["below_floor"] is below
    assert p["min_draws"] == min_draws
    if below:
        assert p["verdict"] == "inconclusive"
        assert _cert(report).note == (
            "target 3 h/yr is below the MC resolution floor 3.06723 h/yr at "
            "17 draws: at least 18 draws resolve it")


# GK A ask 1 and the owner decision on #1927: the start refuses only past
# the 2000-draw cap, and then names no draw count. A weight of 20 h on one
# year resolves 0.01 h/yr at exactly 2000 draws; 20.01 h needs 2001.
@pytest.mark.parametrize("w_min,refused", [(20.0, False), (20.01, True),
                                           (168.0, True)])
@pytest.mark.parametrize("draws", [1, 2000])
def test_the_start_refuses_only_a_goal_past_the_draw_cap(w_min, refused, draws):
    msg = S.draws_short_for((w_min, 1.0), _pack(target=0.01), draws)
    if not refused:
        assert msg is None
        return
    assert msg.startswith("Simulation runs per check: a check that finds no "
                          "shortfall cannot show that the 0.01 h/yr goal is met, "
                          "and even 2000 runs (the most allowed) could not "
                          "decide it")
    assert msg.endswith(": allow more shortfall in the goal.")
    assert "16800" not in msg and "set at least" not in msg


# GK A R3 (owner decision on #1927): a study requested below the floor is not
# refused at the start; it runs, and the verdict, payload and Improve name the
# floor and the fewest draws.
@pytest.mark.live_solve
def test_a_study_started_below_the_floor_runs_and_ends_with_the_floor_note(
        client, install_network):
    import time
    install_network(_cert_network(units=8))
    r = client.put("/api/simulation/solver_config",
                   json={"solver_name": "highs", "voll": 5000.0, "mc_draws": 17})
    assert r.status_code == 200, r.text
    r = client.post("/api/results/eh_study", json={
        "archetype": "off_grid", "stages": list(CERT_STAGES),
        "pack_overrides": {"ens_cap_permyriad": 5000.0, "target_lole_h": 3.0}})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 180
    while (body := client.get("/api/results/eh_study").json()).get("status") == "running":
        assert time.monotonic() < deadline, "eh_study never finished"
        time.sleep(0.05)
    assert body["status"] == "done", body.get("error")
    p = body["report"]["sections"]["certification"]["payload"]
    assert (p["n_samples"], p["verdict"]) == (17, "inconclusive")
    assert (p["below_floor"], p["min_draws"]) == (True, 18)
    review = client.get("/api/results/eh_review").json()
    f = next(f for f in review["findings"] if f["id"] == "certification_inconclusive")
    assert "at least 18 draws" in f["recommendation"]
    assert f["actions"][0]["args"]["mc"] == {"draws": 34}


# GK A ask 3: a target equal to the floor is resolved; its verdict follows
# the interval (here a straddle), not the below-floor branch.
def test_a_target_at_the_floor_is_a_straddle_not_below_the_floor():
    assert S.below_floor(0.05, 0.05) is False
    assert S.below_floor(0.05, 0.0500001) is True
    assert S.below_floor(None, 0.05) is False
    assert S.below_floor(0.05, None) is False
    assert S.certification_verdict(
        lole_ci=(0.0, 0.1), target_h=0.05, resolution_floor_h=0.05,
        n_samples=20) == ("inconclusive", "the LOLE 95% CI straddles the target")
    verdict, note = S.certification_verdict(
        lole_ci=(0.0, 0.1), target_h=0.05, resolution_floor_h=0.0500001,
        n_samples=20)
    assert verdict == "inconclusive" and "below the MC resolution floor" in note


@pytest.mark.parametrize("target", [0.0, None])
def test_a_zero_or_absent_goal_is_not_refused_at_the_start(target):
    """GK A V3: a 0 h/yr goal stays unrefused before the run (the verdict
    names the floor); no goal has nothing to resolve."""
    assert S.draws_short_of_target(_cert_network(), _pack(target=target), 1) is None
