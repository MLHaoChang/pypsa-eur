"""
P12 — frontier + fmea_top stages (plan 2026-09-25 P12; decisions Q3/Q4).
"""
from __future__ import annotations

import pathlib
import queue
import re
import threading

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    AvailabilityTarget,
    DtcConfig,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import eh_study as S
from services.adequacy import sweep as SW
from services.solver_service import SolverConfig


def _feeder_hub(*, feeders: int = 5, hours: int = 12,
                link_outage: bool = True) -> pypsa.Network:
    """Hub behind a grid import; each local unit on its own feeder bus behind
    an outage-rated Link (K = `feeders` Class-B contingencies). A battery and
    an extendable peaker give the storage-duration lever something to trade."""
    # Low half 60 MW, high half 130 MW: 100 MW local leaves a 30 MW peak
    # deficit that a longer battery (20 MW) covers for more hours → the
    # storage-duration lever changes cost@target.
    load = [60.0 if h % 12 < 6 else 130.0 for h in range(hours)]
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=hours, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    for c in ("gas", "AC", "battery"):
        n.add("Carrier", c)
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "hub_load", bus="hub", p_set=load)
    for i in range(feeders):
        n.add("Bus", f"f{i}", carrier="AC")
        n.add("Generator", f"g{i}", bus=f"f{i}", carrier="gas",
              p_nom=20.0, marginal_cost=50.0 + i)
        kw = dict(outage_rate_value=0.02, mttr_hours=24.0) if link_outage else {}
        n.add("Link", f"feed{i}", bus0=f"f{i}", bus1="hub", p_nom=30.0,
              efficiency=1.0, carrier="AC", **kw)
    n.add("Generator", "peaker", bus="hub", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=200.0, capital_cost=400.0,
          marginal_cost=150.0)
    n.add("StorageUnit", "bat", bus="hub", carrier="battery", p_nom=20.0,
          max_hours=4.0, efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)
    n.add("Generator", "remote", bus="grid", carrier="gas", p_nom=300.0,
          marginal_cost=10.0)
    n.add("Link", "import", bus0="grid", bus1="hub", p_nom=100.0,
          p_nom_max=100.0, efficiency=1.0, carrier="AC")
    n.links["eh_role"] = ""
    n.links.at["import", "eh_role"] = "grid_import"
    n.buses["eh_poc"] = False
    n.buses["eh_critical"] = False
    n.buses.at["hub", "eh_critical"] = True
    return n


def _ens_net() -> pypsa.Network:
    from tests.test_energy_hub_study import _ens_bind_network
    return _ens_bind_network()


def _strong(cap: float = 1000.0):
    return default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=cap)})


def _run(n, pack, *, stages=None, cfg=None, **kw):
    from services.pypsa_service import PyPSAService
    PyPSAService.set_network(n)
    kw.setdefault("stop_event", threading.Event())
    return S.run_eh_study(
        n, pack, cfg or SolverConfig(voll=150.0), lock=PyPSAService.get_lock(),
        log_queue=queue.SimpleQueue(), stages=stages, **kw)


# ── P12a frontier ───────────────────────────────────────────────────────────


def test_frontier_targets_keep_the_pack_cap_and_its_neighbours():
    t = S.frontier_targets(7.0, 3)
    assert t[0] == 7.0 and len(t) == 3
    assert set(t[1:]) == {5.0, 10.0}          # nearest in log distance


@pytest.mark.live_solve
def test_strong_grid_default_frontier_is_monotone_and_carries_the_pack_cap():
    report = _run(_ens_net(), _strong(20.0))
    sec = report.sections["frontier"]
    assert sec.status == "ok", sec.note
    pts = [p for p in sec.payload["points"] if p["status"] == "ok"]
    assert 20.0 in [p["target_permyriad"] for p in pts]
    costs = [p["point"]["total_system_cost_eur"] for p in pts]  # loosest first
    assert costs == sorted(costs)
    assert sec.payload["restore_skipped_on_private_copy"] is True
    fr = next(r for r in report.pipeline.stages if r.stage == "frontier")
    assert fr.solves_charged == len(sec.payload["points"])
    assert report.pipeline.solves_consumed == 1 + fr.solves_charged
    assert "knee_index" in sec.payload


@pytest.mark.live_solve
def test_frontier_takes_its_budget_share():
    report = _run(_ens_net(), _strong(20.0), budget_solves=5)
    pts = report.sections["frontier"].payload["points"]
    assert len(pts) == 2                       # max(2, floor(0.4 × 5))
    assert 20.0 in [p["target_permyriad"] for p in pts]


@pytest.mark.live_solve
def test_frontier_without_room_for_two_points_is_skipped_for_budget():
    report = _run(_ens_net(), _strong(20.0), budget_solves=2)
    sec = report.sections["frontier"]
    assert sec.status == "not_established"
    assert "budget" in (sec.note or "")
    assert report.pipeline.solves_consumed <= 2


@pytest.mark.live_solve
def test_weak_and_off_grid_run_the_frontier_only_when_asked():
    for factory in (default_weak_flexible_pack, default_off_grid_pack):
        assert "frontier" not in S.default_stages_for(factory())
    assert "frontier" in S.default_stages_for(default_strong_grid_pack())


@pytest.mark.live_solve
def test_frontier_does_not_disturb_the_plan_later_stages_read():
    from tests.test_energy_hub_mc_certify import _cert_network, _pack
    base = ("apply_pack", "ens_solve", "mc_certify", "assemble")
    with_fr = ("apply_pack", "ens_solve", "frontier", "mc_certify", "assemble")
    a = _run(_cert_network(), _pack(), stages=base, cfg=SolverConfig(voll=5000.0))
    b = _run(_cert_network(), _pack(), stages=with_fr,
             cfg=SolverConfig(voll=5000.0))
    assert b.completeness["frontier"] in ("ok", "not_established")
    pa = a.sections["certification"].payload
    pb = b.sections["certification"].payload
    assert pa["lole_h_per_horizon"] == pb["lole_h_per_horizon"]
    assert a.sections["sizing"].payload == b.sections["sizing"].payload


@pytest.mark.live_solve
def test_abort_between_frontier_points_keeps_what_was_swept(monkeypatch):
    stop = threading.Event()
    real = SW._solve_once
    calls = {"n": 0}

    def once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:            # during the 2nd frontier point
            stop.set()
        return real(*a, **k)

    monkeypatch.setattr(SW, "_solve_once", once)
    report = _run(_ens_net(), _strong(20.0), stop_event=stop)
    sec = report.sections["frontier"]
    assert sec.status == "not_established"
    assert sec.payload["aborted"] is True
    assert 0 < len(sec.payload["points"]) < 8
    assert report.pipeline.aborted is True
    assert "pending" not in {r.status for r in report.pipeline.stages}


# ── P12b fmea_top ───────────────────────────────────────────────────────────

FMEA = ("apply_pack", "ens_solve", "fmea_top", "assemble")


@pytest.mark.live_solve
def test_fmea_top_ranks_by_the_worksheet_rule():
    report = _run(_feeder_hub(), _strong(5000.0), stages=FMEA,
                  cfg=SolverConfig(voll=3000.0))
    sec = report.sections["fmea_top"]
    assert sec.status == "ok", sec.note
    p = sec.payload
    assert p["k_links"] == 5 and p["basis"] == "pack_applied_ens_plan"
    keys = [(-r["criticality_eur_per_year"], r["mode_id"]) for r in p["rows"]]
    assert keys == sorted(keys)
    assert len(p["rows"]) <= S.FMEA_TOP_N
    assert "Link-primary" in (sec.note or "")
    rec = next(r for r in report.pipeline.stages if r.stage == "fmea_top")
    assert rec.solves_charged == 5 + 1            # frozen base + K, no restore
    assert report.pipeline.solves_consumed == 1 + 6


@pytest.mark.live_solve
def test_fmea_top_with_no_eligible_links_is_not_established():
    report = _run(_feeder_hub(link_outage=False), _strong(5000.0), stages=FMEA,
                  cfg=SolverConfig(voll=3000.0))
    sec = report.sections["fmea_top"]
    assert sec.status == "not_established"
    assert "no Class-B-eligible Links" in (sec.note or "")
    assert report.pipeline.solves_consumed == 1


@pytest.mark.live_solve
def test_fmea_top_over_the_link_cap_is_not_established():
    report = _run(_feeder_hub(feeders=SW.MAX_CLASS_B_LINKS + 1), _strong(5000.0),
                  stages=FMEA, cfg=SolverConfig(voll=3000.0))
    sec = report.sections["fmea_top"]
    assert sec.status == "not_established"
    assert str(SW.MAX_CLASS_B_LINKS) in (sec.note or "")


@pytest.mark.live_solve
def test_fmea_top_that_does_not_fit_the_budget_is_skipped_not_truncated():
    report = _run(_feeder_hub(), _strong(5000.0), stages=FMEA, budget_solves=4,
                  cfg=SolverConfig(voll=3000.0))
    sec = report.sections["fmea_top"]
    assert sec.status == "not_established"
    assert "partial ranking" in (sec.note or "")
    assert report.pipeline.solves_consumed == 1


@pytest.mark.live_solve
def test_fmea_top_excludes_import_links_the_pack_closed():
    n = _feeder_hub()
    n.links.at["import", "outage_rate_value"] = 0.02
    n.links.at["import", "mttr_hours"] = 24.0
    pack = default_off_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0)})
    report = _run(n, pack, stages=FMEA, cfg=SolverConfig(voll=3000.0))
    p = report.sections["fmea_top"].payload
    assert p["excluded_closed_import_links"] == ["import"]
    assert p["k_links"] == 5
    assert "import" not in [r["mode_id"] for r in p["rows"]]


# ── budget interplay (B8) ───────────────────────────────────────────────────


@pytest.mark.live_solve
def test_default_weak_pipeline_at_budget_30_still_reaches_levers_and_dtc():
    report = _run(_feeder_hub(), default_weak_flexible_pack(), stages=None,
                  cfg=SolverConfig(voll=3000.0),
                  dtc_config=DtcConfig(critical_bus_ids=["hub"],
                                       islanding_contingencies=["import"]))
    c = report.completeness
    assert c["fmea_top"] == "ok"
    assert c["levers"] == "ok", report.sections["levers"].note
    assert c["dtc"] == "ok", report.sections["dtc"].note
    assert report.pipeline.solves_consumed <= 30


@pytest.mark.live_solve
def test_default_off_grid_pipeline_at_budget_30_still_reaches_levers():
    report = _run(_feeder_hub(), default_off_grid_pack(), stages=None,
                  cfg=SolverConfig(voll=3000.0))
    c = report.completeness
    assert c["fmea_top"] == "ok"
    assert c["levers"] == "ok", report.sections["levers"].note
    assert report.pipeline.solves_consumed <= 30


# ── Q4 guard: only the EH study may skip the closing restore ────────────────


def test_no_route_or_runner_skips_the_closing_restore():
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in list((root / "routers").rglob("*.py")) + \
            list((root / "services").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == "services/adequacy/eh_study.py":
            continue
        # Call sites only (``restore_base=False`` in docstrings is prose).
        if re.search(r"restore_base\s*=\s*False\s*[,)]", path.read_text()):
            offenders.append(rel)
    assert offenders == [], (
        "only the Energy Hub study (private copies) may pass "
        f"restore_base=False: {offenders}")
    eh = (root / "services/adequacy/eh_study.py").read_text()
    assert len(re.findall(r"restore_base\s*=\s*False\s*[,)]", eh)) == 2
