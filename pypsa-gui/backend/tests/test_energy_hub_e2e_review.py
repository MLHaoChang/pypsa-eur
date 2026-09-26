"""
Fixes from the cross-phase end-to-end review of P10–P18 (plan 2026-09-26,
"E2E review"). Each test reproduces the reviewer's verified finding.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from services.solver_service import SolverConfig


# ── M1: a profiles scenario naming components not on the network ──────────


def test_profiles_with_unmatched_names_are_incomplete_not_unstressed():
    from services.adequacy import stress as ST
    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "demand", bus="b", p_set=50.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=100.0, marginal_cost=10)
    PyPSAService.set_network(n)
    sc = {"id": "dunkel", "kind": "profiles", "frequency_per_year": 0.05,
          "profile_pack": "synth_dunkelflaute"}        # keyed on l / wind1
    rows, _ = ST.run_class_c_sweep(
        n, PyPSAService.get_lock(), SolverConfig(voll=3000.0), [sc],
        stop_event=threading.Event(), log_queue=queue.SimpleQueue())
    row = next(r for r in rows if r["id"] == "scenario:dunkel")
    assert row["status"] == "profiles_incomplete"
    note = row["meta"]["note"]
    assert "not on the network" in note and "l" in note and "wind1" in note


# ── M2: the default import_cap ladder's 0 MW rung ──────────────────────────


def test_a_zero_import_cap_is_a_no_import_rung_not_a_preflight_error():
    from services.adequacy import levers as L
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    n = _feeder_hub()
    undo, mut = L.apply_lever_scenario(n, "import_cap", value=0.0)
    assert float(n.links.at["import", "p_nom"]) > 0          # preflight-safe
    assert float(n.links.at["import", "p_max_pu"]) == 0.0
    assert mut["value"] == 0.0
    undo()
    assert float(n.links.at["import", "p_max_pu"]) == 1.0


@pytest.mark.live_solve
def test_the_default_import_cap_ladder_solves_every_rung():
    from services.adequacy import levers as L
    from services.pypsa_service import PyPSAService
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    n = _feeder_hub()
    PyPSAService.set_network(n)
    table = L.compare_lever_scenarios(
        n, SolverConfig(voll=3000.0, ens_cap_permyriad=10.0),
        lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        kind="import_cap")
    assert [o["status"] for o in table["options"]] == ["ok"] * 3, table["options"]
    assert not any(o["ineffective"] for o in table["options"])


def test_a_validation_refusal_is_not_charged_as_a_solve(monkeypatch):
    from services.adequacy import levers as L
    from services.pypsa_service import PyPSAService
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    monkeypatch.setattr("services.solver_service.run_simulation",
                        lambda *a, **k: ("error", "validation_failed"))
    n = _feeder_hub()
    PyPSAService.set_network(n)
    table = L.compare_lever_scenarios(
        n, SolverConfig(voll=3000.0, ens_cap_permyriad=10.0),
        lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        kind="storage_duration")
    assert table["solves_attempted"] == 0
    assert all(o["condition"] == "validation_failed" for o in table["options"])


# ── m1: a stage exception is skipped (refusal) or failed, never aborted ─────


def _study(n, stages, **kw):
    from models.energy_hub import default_weak_flexible_pack
    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService
    PyPSAService.set_network(n)
    return S.run_eh_study(
        n, default_weak_flexible_pack(), SolverConfig(voll=3000.0),
        lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(), stages=stages, **kw)


@pytest.mark.live_solve
def test_a_config_refusal_is_skipped_and_an_error_is_failed(monkeypatch):
    from services.adequacy import levers as L
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    n = _feeder_hub()
    n.buses["eh_critical"] = False                  # DtC cannot resolve
    monkeypatch.setattr(L, "compare_lever_scenarios",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rep = _study(n, ["apply_pack", "ens_solve", "levers", "dtc_stress"])
    stages = {r.stage: r for r in rep.pipeline.stages}
    assert stages["dtc_stress"].status == "skipped"
    assert "critical" in stages["dtc_stress"].note
    assert stages["levers"].status == "failed"
    assert "boom" in stages["levers"].note
    assert rep.pipeline.aborted is False
    assert all(r.status != "aborted" for r in rep.pipeline.stages)


# ── m3: readiness previews the overrides that will run ──────────────────────


def test_readiness_previews_pack_overrides(client, install_network):
    import json

    from tests.test_energy_hub_frontier_fmea import _feeder_hub
    install_network(_feeder_hub())
    base = client.get("/api/results/eh_readiness",
                      params={"archetype": "strong_grid"}).json()
    ov = {"target_lole_h": 3.0, "certification_metric": "mc_lole"}
    with_ov = client.get("/api/results/eh_readiness", params={
        "archetype": "strong_grid", "pack_overrides": json.dumps(ov)}).json()
    pred = lambda r: {s["stage"]: s["prediction"] for s in r["stages"]}  # noqa: E731
    assert pred(base)["mc_certify"] == "not_requested"
    assert pred(with_ov)["mc_certify"] == "run"
    assert with_ov["pack_hash"] != base["pack_hash"]
    bad = client.get("/api/results/eh_readiness", params={
        "archetype": "strong_grid", "pack_overrides": '{"nope": 1}'})
    assert bad.status_code == 422 and "pack_overrides" in bad.text


# ── m5: chat reads the EH sibling tables ────────────────────────────────────


def test_chat_reads_every_eh_sibling_table(install_network):
    from services import chat_tools as T
    from services.chat_tools_schema import ADEQUACY_KIND_ENUM
    for kind in ("eh_redundancy", "eh_levers", "eh_dtc", "eh_dtc_planning"):
        assert kind in ADEQUACY_KIND_ENUM
        out = T.get_adequacy_results(kind)          # nothing ran: no_data
        assert isinstance(out, dict) and out.get("status") == "no_data", out
