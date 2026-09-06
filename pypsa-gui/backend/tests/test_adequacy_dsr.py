"""
The demand-response slack tier (Phase 1 Task 4).

Design: spec §4.4; plan Phase 1 Task 4. A voluntary, opt-in, volume-capped
second slack tier priced at contracted compensation — a RESOURCE. It never
counts as unserved energy: excluded from the lost-load capture, from the
ENS cap, and from voll_shed_* in the cost decomposition.

Fixture economics: dsr_price (50) < backup (200) < voll — so the LP uses
DSR up to its volume cap BEFORE the expensive unit, and involuntary
shedding only where even the backup cannot help.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from services import validation_service as VS
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

WEIGHT = 3.0
N_SNAPSHOTS = 4
LOAD_MW = 100.0
CHEAP_MW = 60.0
BACKUP_MC = 200.0
DSR_PRICE = 50.0
DSR_SHARE = 0.2                      # 20 % of the bus's 100 MW peak = 20 MW
DSR_MWH = LOAD_MW * DSR_SHARE * N_SNAPSHOTS * WEIGHT   # 240


def _network(backup_mw: float = 40.0) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N_SNAPSHOTS, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    n.add("Generator", "cheap", bus="b", carrier="gas", p_nom=CHEAP_MW,
          marginal_cost=10.0)
    if backup_mw > 0:
        n.add("Generator", "backup", bus="b", carrier="gas", p_nom=backup_mw,
              marginal_cost=BACKUP_MC)
    return n


def _solve(n: pypsa.Network, voll: float = 3000.0, **cfg_kw) -> tuple[dict, list[str]]:
    PyPSAService.set_network(n)
    sink: dict = {}
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    cfg = SolverConfig(voll=voll, **cfg_kw)
    status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(), log_q,
        state_update=lambda **kw: sink.update(kw),
    )
    assert status in ("ok", "optimal"), (status, condition)
    lines: list[str] = []
    while True:
        try:
            lines.append(str(log_q.get_nowait()))
        except queue.Empty:
            break
    return sink, lines


DSR_CFG = dict(dsr_price_eur_per_mwh=DSR_PRICE, dsr_share_of_load=DSR_SHARE,
               dsr_buses=["b"])


def test_lp_prefers_dsr_up_to_its_volume_cap():
    """cheap 60 + DSR 20 + backup 20 serves the 100 MW load: DSR (50 €/MWh)
    dispatches to its full 20 MW cap before the 200 €/MWh backup fills the
    rest, and nothing is involuntarily shed."""
    sink, _ = _solve(_network(), **DSR_CFG)
    cap = sink.get("last_lost_load") or {}
    assert float(cap.get("dsr_total_mwh", 0.0)) == pytest.approx(DSR_MWH, rel=1e-3)
    assert float(cap.get("lost_load_total_mwh", 0.0)) == pytest.approx(0.0, abs=1e-6)


def test_capture_separates_the_tiers():
    """Backup too small (10 MW): served = 60+20+10, 10 MW shed involuntarily.
    The capture must put 10 MW in lost_load and 20 MW in dsr — no mixing,
    and no __dsr column inside lost_load_t."""
    sink, _ = _solve(_network(backup_mw=10.0), **DSR_CFG)
    cap = sink["last_lost_load"]
    assert float(cap["dsr_total_mwh"]) == pytest.approx(DSR_MWH, rel=1e-3)
    assert float(cap["lost_load_total_mwh"]) == pytest.approx(
        10.0 * N_SNAPSHOTS * WEIGHT, rel=1e-3)
    assert "b" in cap["lost_load_t"].columns
    assert not any(str(c).startswith("__dsr") for c in cap["lost_load_t"].columns)
    assert "dsr_t" in cap and float(cap["dsr_t"].max().max()) == pytest.approx(
        LOAD_MW * DSR_SHARE, rel=1e-3)


def test_ens_cap_counts_involuntary_only():
    """voll=150 < backup 200 makes involuntary shedding attractive; a tight
    ENS cap (2 % of the 1200 MWh demand = 24 MWh) must constrain ONLY the
    involuntary tier — DSR stays at its full 240 MWh."""
    sink, _ = _solve(_network(), voll=150.0, ens_cap_permyriad=200.0, **DSR_CFG)
    cap = sink["last_lost_load"]
    assert float(cap["dsr_total_mwh"]) == pytest.approx(DSR_MWH, rel=1e-3), (
        "the ENS cap throttled demand response — it must sum the "
        "involuntary tier only"
    )
    cap_mwh = 200.0 / 1e4 * (LOAD_MW * N_SNAPSHOTS * WEIGHT)
    assert float(cap["lost_load_total_mwh"]) == pytest.approx(cap_mwh, rel=1e-3)


def test_report_energy_block_splits_the_tiers():
    from models.adequacy import AdequacyReport
    sink, _ = _solve(_network(backup_mw=10.0), voll=3000.0,
                     ens_cap_permyriad=9000.0, **DSR_CFG)
    r = AdequacyReport.model_validate(sink["adequacy_report"])
    assert r.energy.demand_response_mwh == pytest.approx(DSR_MWH, rel=1e-3)
    assert r.energy.involuntary_mwh == pytest.approx(
        10.0 * N_SNAPSHOTS * WEIGHT, rel=1e-3)


def test_decomposition_keeps_dsr_out_of_voll_shed():
    sink, lines = _solve(_network(backup_mw=10.0), **DSR_CFG)
    decomp = [l for l in lines if "VOLL_shed=" in l]
    assert decomp, "no [COST-DECOMP] line found"
    # Involuntary: 10 MW × 4 × 3 × 3000 €/MWh = 0.36 M€. With DSR lumped in
    # it would read 0.36 + 240 × 50 / 1e6 = 0.372.
    assert "VOLL_shed=0.36M€" in decomp[-1], decomp[-1]
    assert any("DSR=" in l for l in decomp), (
        "decomposition must report the DSR tier in its own bucket")


def test_off_without_optin_buses_and_loud():
    """Price set but no opt-in buses: the tier must stay OFF (never silently
    global — spec §4.4's double-count hazard) and preflight must warn."""
    sink, _ = _solve(_network(backup_mw=10.0),
                     dsr_price_eur_per_mwh=DSR_PRICE,
                     dsr_share_of_load=DSR_SHARE)
    cap = sink["last_lost_load"]
    assert float(cap.get("dsr_total_mwh", 0.0)) == 0.0
    issues = VS.validate_for_run(_network(), SolverConfig(
        voll=3000.0, dsr_price_eur_per_mwh=DSR_PRICE,
        dsr_share_of_load=DSR_SHARE))
    assert any(i.code == "dsr_enabled_without_buses" for i in issues), \
        [i.code for i in issues]


def test_double_count_warning_next_to_modelled_flexibility():
    n = _network()
    n.add("StorageUnit", "batt", bus="b", p_nom=10.0, max_hours=4.0)
    issues = VS.validate_for_run(n, SolverConfig(voll=3000.0, **DSR_CFG))
    assert any(i.code == "dsr_double_count_risk" for i in issues), \
        [i.code for i in issues]
