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
