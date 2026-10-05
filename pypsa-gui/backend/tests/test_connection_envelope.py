"""
Connection agreements — dynamic envelope and FCA (Edge Investment Case P1 WP1.4b).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.4b
Spec §5 table: "Dynamic operating envelope / FCA | time-varying p_max_pu from
envelope series; curtailment hours as a stress-class entry".

The envelope is a Library series (MW the site may import per interval). Like the
export price, the config route resolves it and writes `links_t["ic_envelope_mw"]`
on the current snapshot axis. `apply_connection_agreement` turns it into a
time-varying `p_max_pu` for one solve and undoes it afterwards. An FCA
agreement's curtailment hours become a `profiles` stress entry on the PoC Link:
the deterministic worst-case hours, zeroed, disclosed as `fca_synthetic_hours`.
"""
from __future__ import annotations

import queue
import threading
from datetime import date

import numpy as np
import pandas as pd
import pytest

from models.commercial import ConnectionAgreement
from services.adequacy import stress as ST
from services.commercial import connection as C
from services.commercial import lp_bindings as L
from tests.fixtures.investment_case.edge_15min import build_edge_15min

_ENV = {"id": "env", "version": 1, "hash": "e" * 64, "source": "t"}


def _env_series(n, mw):
    return pd.Series(mw, index=n.snapshots)


def _dynamic(cap=60.0, **kw):
    return ConnectionAgreement(kind="non_firm_dynamic", import_cap_mw=cap, envelope=_ENV,
                               available_from=date(2030, 1, 1), **kw)


def _fca(hours=10.0, envelope=None, cap=40.0):
    return ConnectionAgreement(kind="fca", import_cap_mw=cap, envelope=envelope,
                               curtailment_hours_per_year=hours,
                               available_from=date(2030, 1, 1))


def test_the_envelope_becomes_time_varying_availability():
    n = build_edge_15min()  # import p_nom 80
    mw = np.where(np.arange(len(n.snapshots)) % 96 < 48, 20.0, 70.0)
    assert C.write_envelope(n, "import", _env_series(n, mw)) == 0
    applied = C.apply_connection_agreement(n, _dynamic(cap=60.0), poc_link="import")
    pmp = n.links_t.p_max_pu["import"].to_numpy()
    # min(envelope, cap) / p_nom: 20/80 in the first half-day, 60/80 after.
    assert np.allclose(pmp, np.minimum(mw, 60.0) / 80.0)
    applied.undo()
    assert "import" not in n.links_t.p_max_pu.columns


def test_the_envelope_respects_an_existing_availability_profile():
    n = build_edge_15min()
    n.links_t.p_max_pu["import"] = 0.1
    C.write_envelope(n, "import", _env_series(n, 70.0))
    applied = C.apply_connection_agreement(n, _dynamic(cap=80.0), poc_link="import")
    assert np.allclose(n.links_t.p_max_pu["import"], 0.1)  # the tighter one wins
    applied.undo()
    assert np.allclose(n.links_t.p_max_pu["import"], 0.1)


def test_a_missing_or_stale_envelope_is_refused_not_ignored():
    n = build_edge_15min()
    with pytest.raises(C.CommercialBindingError, match="envelope"):
        C.apply_connection_agreement(n, _dynamic(), poc_link="import")
    C.write_envelope(n, "import", _env_series(n, 50.0))
    n.set_snapshots(n.snapshots[::4])
    with pytest.raises(C.CommercialBindingError, match="re-apply"):
        C.apply_connection_agreement(n, _dynamic(), poc_link="import")


def test_a_partial_envelope_is_not_written():
    n = build_edge_15min()
    short = pd.Series(50.0, index=n.snapshots[:10])
    assert C.write_envelope(n, "import", short) == len(n.snapshots) - 10
    assert "import" not in n.links_t.get(C.ENVELOPE_ATTR, pd.DataFrame()).columns


def test_fca_without_an_envelope_caps_at_the_contracted_capacity():
    n = build_edge_15min()
    C.apply_connection_agreement(n, _fca(cap=40.0), poc_link="import")
    assert n.links.at["import", "p_max_pu"] == pytest.approx(40.0 / 80.0)


def test_fca_curtailment_hours_become_a_profiles_stress_entry():
    n = build_edge_15min()  # 7 days at 0.25 h
    entry = C.fca_stress_entry(n, _fca(hours=876.0, cap=40.0), poc_link="import")
    # 876 h/yr × (168/8760) yr = 16.8 h → 67 quarter-hours (rounded).
    series = np.asarray(entry["links_p_max_pu"]["import"])
    assert entry["kind"] == "profiles" and entry["disclosure"] == "fca_synthetic_hours"
    assert (series == 0.0).sum() == 67
    assert np.allclose(series[series > 0], 40.0 / 80.0)
    # Deterministic worst case: the zeroed snapshots are the highest-load ones.
    load = n.loads_t.p_set["site_load"].to_numpy()
    assert load[series == 0.0].min() >= np.sort(load)[::-1][66] - 1e-9
    # It is a valid registry entry.
    ST._validate([entry])
    assert ST._profiles_match_horizon(entry, len(n.snapshots))


def test_fca_stress_entry_is_deterministic():
    n = build_edge_15min()
    a = C.fca_stress_entry(n, _fca(hours=100.0), poc_link="import")
    b = C.fca_stress_entry(n, _fca(hours=100.0), poc_link="import")
    assert a == b


# ── through run_simulation and the route ───────────────────────────────────


@pytest.mark.live_solve
def test_dynamic_envelope_bounds_the_solved_import():
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    n = build_edge_15min()
    n.add("Generator", "backup", bus="site", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    mw = np.where(np.arange(len(n.snapshots)) % 96 >= 68, 15.0, 70.0)  # evening squeeze
    C.write_envelope(n, "import", _env_series(n, mw))
    commercial = {"poc_link": "import", "connection": _dynamic(cap=60.0).model_dump(mode="json")}
    PyPSAService.set_network(n)
    status, _ = run_simulation(SolverConfig(commercial=commercial), n, PyPSAService.get_lock(),
                               threading.Event(), queue.SimpleQueue(),
                               state_update=lambda **kw: None)
    assert status in ("ok", "optimal")
    assert (n.links_t.p0["import"].to_numpy() <= np.minimum(mw, 60.0) + 1e-6).all()
    assert "import" not in n.links_t.p_max_pu.columns  # undone


def test_route_resolves_the_envelope_and_registers_the_fca_entry(
        client, install_network, session_ctx, project_storage_dir):
    edge = build_edge_15min()
    install_network(edge, name="fca")
    r = client.post("/api/projects/fca", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    idx = edge.snapshots.tz_localize("UTC")
    ref = client.post("/api/library/series", json={
        "name": "env", "timestamps": [t.isoformat() for t in idx],
        "values": [50.0] * len(idx), "meta": {"source": "t"}}).json()
    agreement = ConnectionAgreement(kind="fca", import_cap_mw=40.0, envelope=ref,
                                    curtailment_hours_per_year=100.0,
                                    available_from=date(2030, 1, 1))
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "timezone": "UTC",
        "connection": agreement.model_dump(mode="json")}})
    assert r.status_code == 200, r.text
    live = session_ctx(client).network
    assert np.allclose(live.links_t[C.ENVELOPE_ATTR]["import"], 50.0)
    registry = ST.load_scenarios(project_storage_dir("fca"))
    entry = next(e for e in registry if e["id"] == "fca_import")
    assert entry["disclosure"] == "fca_synthetic_hours"
    # Re-applying replaces the entry rather than duplicating it.
    assert client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "timezone": "UTC",
        "connection": agreement.model_dump(mode="json")}}).status_code == 200
    assert [e["id"] for e in ST.load_scenarios(project_storage_dir("fca"))].count("fca_import") == 1


def test_route_refuses_a_dynamic_envelope_without_a_zone_for_a_zoned_series(
        client, install_network):
    edge = build_edge_15min()
    install_network(edge)
    idx = edge.snapshots.tz_localize("UTC")
    ref = client.post("/api/library/series", json={
        "name": "env2", "timestamps": [t.isoformat() for t in idx],
        "values": [50.0] * len(idx), "meta": {"source": "t"}}).json()
    agreement = ConnectionAgreement(kind="non_firm_dynamic", import_cap_mw=40.0, envelope=ref,
                                    available_from=date(2030, 1, 1))
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "connection": agreement.model_dump(mode="json")}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "timezone_required"


# ── WP1.4 review round 2: FCA registration ─────────────────────────────────


def _fca_body(hours=100.0):
    agreement = ConnectionAgreement(kind="fca", import_cap_mw=40.0,
                                    curtailment_hours_per_year=hours,
                                    available_from=date(2030, 1, 1))
    return {"commercial": {"poc_link": "import", "connection": agreement.model_dump(mode="json")}}


def test_fca_on_an_unsaved_project_is_refused(client, install_network):
    install_network(build_edge_15min())
    r = client.put("/api/simulation/solver_config", json=_fca_body())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "fca_needs_saved_project"


def _saved_edge(client, install_network, name):
    install_network(build_edge_15min(), name=name)
    assert client.post(f"/api/projects/{name}",
                       params={"force": True, "rebind": True}).status_code == 200


def test_clearing_or_changing_the_config_removes_the_fca_entry(
        client, install_network, project_storage_dir):
    _saved_edge(client, install_network, "fca2")
    assert client.put("/api/simulation/solver_config", json=_fca_body()).status_code == 200
    assert [e["id"] for e in ST.load_scenarios(project_storage_dir("fca2"))] == ["fca_import"]
    assert client.put("/api/simulation/solver_config",
                      json={"commercial": None}).status_code == 200
    assert ST.load_scenarios(project_storage_dir("fca2")) == []


def test_a_full_registry_is_a_422_and_writes_nothing(client, install_network, project_storage_dir):
    _saved_edge(client, install_network, "fca3")
    full = [{"id": f"s{i}", "kind": "parametric", "frequency_per_year": 1.0}
            for i in range(ST.MAX_SCENARIOS)]
    ST.save_scenarios(project_storage_dir("fca3"), full)
    r = client.put("/api/simulation/solver_config", json=_fca_body())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "stress_registry_invalid"
    assert len(ST.load_scenarios(project_storage_dir("fca3"))) == ST.MAX_SCENARIOS


def test_an_unreadable_registry_is_not_overwritten(client, install_network, project_storage_dir):
    _saved_edge(client, install_network, "fca4")
    path = project_storage_dir("fca4") / ST.SIDECAR_NAME
    path.write_text("{corrupt")
    r = client.put("/api/simulation/solver_config", json=_fca_body())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stress_registry_unreadable"
    assert path.read_text() == "{corrupt"


# ── WP1.4 review round 3 ───────────────────────────────────────────────────


def test_route_writes_an_envelope_on_a_multi_period_network(client, install_network, session_ctx):
    """#1: the route aligned once and the writer aligned again (MultiIndex crash)."""
    base = build_edge_15min()
    n = base.copy()
    n.set_snapshots(base.snapshots[:96])
    n.snapshot_weightings.loc[:, :] = 0.25
    n.loads_t.p_set = base.loads_t.p_set.iloc[:96]
    n.generators_t.p_max_pu = base.generators_t.p_max_pu.iloc[:96]
    n.set_investment_periods([2030, 2035])
    install_network(n)
    idx = base.snapshots[:96].tz_localize("UTC")
    ref = client.post("/api/library/series", json={
        "name": "env_mp", "timestamps": [t.isoformat() for t in idx],
        "values": [50.0] * len(idx), "meta": {"source": "t"}}).json()
    agreement = ConnectionAgreement(kind="non_firm_dynamic", import_cap_mw=40.0, envelope=ref,
                                    available_from=date(2030, 1, 1))
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "timezone": "UTC",
        "connection": agreement.model_dump(mode="json")}})
    assert r.status_code == 200, r.text
    live = session_ctx(client).network
    assert len(live.links_t[C.ENVELOPE_ATTR]["import"]) == 192
    assert np.allclose(live.links_t[C.ENVELOPE_ATTR]["import"], 50.0)


def test_a_lever_import_cap_is_respected_by_the_agreement():
    """#3: a study's planning limit on the PoC Link wins over a larger contract."""
    from services.adequacy.levers import apply_lever_scenario

    n = build_edge_15min()
    undo, _ = apply_lever_scenario(n, "import_cap", value=20.0)
    applied = C.apply_connection_agreement(
        n, ConnectionAgreement(kind="firm", import_cap_mw=60.0, available_from=date(2030, 1, 1)),
        poc_link="import")
    assert n.links.at["import", "p_nom"] == pytest.approx(20.0)
    applied.undo()
    undo()
    assert not getattr(n, C.LINK_LIMITS_ATTR, None)


def test_the_operational_pin_never_exceeds_the_contract_and_flags_a_stale_design():
    """#4: a design solved without this agreement must not pin above the cap."""
    n = build_edge_15min()
    n.links.loc["import", "p_nom_opt"] = 80.0
    setattr(n, C.OPERATIONAL_ATTR, True)
    fee = {"id": "f", "kind": "capacity", "unit": "per_kw_year",
           "periods": [{"name": "all", "rate": 50.0}]}
    applied = C.apply_connection_agreement(
        n, ConnectionAgreement(kind="firm", import_cap_mw=60.0, capacity_fee=fee,
                               available_from=date(2030, 1, 1)), poc_link="import")
    assert n.links.at["import", "p_nom_min"] == pytest.approx(60.0)
    assert applied.facts["connection"]["operational_design_mismatch"] is True
    applied.undo()


def test_putting_a_null_commercial_block_when_none_is_stored_does_not_rebind(
        client, install_network, monkeypatch):
    """#6: a full-payload PUT from the settings form must not touch the registry."""
    import routers.simulation as sim

    install_network(build_edge_15min())
    called = []
    monkeypatch.setattr(sim, "_bind_commercial", lambda *a, **k: called.append(1))
    assert client.put("/api/simulation/solver_config",
                      json={"commercial": None, "voll": 0.0}).status_code == 200
    assert called == []


@pytest.mark.live_solve
def test_a_missing_stress_link_is_an_incomplete_row_not_an_aborted_sweep():
    """#2: fail closed per scenario; the rest of class C still runs."""
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = build_edge_15min()
    n.add("Generator", "backup", bus="site", p_nom=100.0, marginal_cost=500.0, carrier="grid")
    PyPSAService.set_network(n)
    good = {"id": "cold", "kind": "parametric", "frequency_per_year": 0.1,
            "electrical_load_multiplier": 1.1}
    gone = {"id": "fca_gone", "kind": "profiles", "frequency_per_year": 1.0,
            "links_p_max_pu": {"ghost": [0.0] * len(n.snapshots)}}
    rows, _restore = ST.run_class_c_sweep(n, PyPSAService.get_lock(), SolverConfig(voll=1000.0),
                                          [good, gone], log_queue=queue.SimpleQueue())
    by_id = {r["id"]: r for r in rows}
    assert by_id["scenario:fca_gone"]["status"] == "profiles_incomplete"
    assert by_id["scenario:fca_gone"]["meta"]["note"] == "link_missing"
    assert by_id["scenario:cold"].get("status") != "profiles_incomplete"


# ── WP1.4 review round 4 (PASS WITH CONDITIONS) ────────────────────────────


@pytest.mark.parametrize("kind", ["non_firm_static", "fca"])
def test_a_lever_below_a_non_firm_contract_caps_instead_of_failing(kind):
    """Condition 1: the study limit clamps the contract before the p_nom check."""
    from services.adequacy.levers import apply_lever_scenario

    n = build_edge_15min()
    undo, _ = apply_lever_scenario(n, "import_cap", value=20.0)
    extra = {"curtailment_hours_per_year": 10.0} if kind == "fca" else {}
    applied = C.apply_connection_agreement(
        n, ConnectionAgreement(kind=kind, import_cap_mw=30.0, available_from=date(2030, 1, 1),
                               **extra), poc_link="import")
    assert float(n.links.at["import", "p_max_pu"]) == pytest.approx(1.0)  # 20/20
    applied.undo()
    undo()


def test_the_sweep_base_result_carries_the_design_mismatch():
    """Condition 2: a stale pinned design reaches the sweep result."""
    import inspect

    from services.adequacy import sweep

    src = inspect.getsource(sweep.run_contingency_sweep)
    assert "commercial_flags" in src
