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
