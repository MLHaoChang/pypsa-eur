"""
P33b 10a — a finished Energy Hub study record survives re-activation.

`reset_network` used to null the finished study keys on the OUTGOING, resident
context (`prev.solver_state[key] = None`) before copying the dict for the fresh
one. `prev` is project A's registered context, so creating project B from a
template, loading B or pressing "New" erased A's own record, and re-activating
A (a pointer swap) found nothing: the Guided hub rail opened at Site and the
greeting said no study had run while the stored report was still there.

The fix clears on the COPY. These tests pin the three swaps, the guarantee the
clear existed for (the fresh context has no study), the running-record rule,
and undo (which re-imports in place and must put the finished record back on
the context later requests read — P33b step 0).
"""
from __future__ import annotations

import threading
import time

from services.pypsa_service import PyPSAService

STUDY_URL = "/api/results/eh_study"
REVIEW_URL = "/api/results/eh_review"


def _report():
    from models.energy_hub import ReferenceDesignReport, empty_section_map

    return ReferenceDesignReport(
        archetype="weak_flexible", pack_hash="p", assumptions_hash="a",
        sections=empty_section_map(default="skipped"), ens_cap_permyriad=10.0,
    )


def fake_done(monkeypatch, *, gate: threading.Event | None = None,
              reached: threading.Event | None = None):
    """Replace the EH pipeline with one that stores a report and finishes
    `done` — the real worker, record and publish path around it are kept."""
    from services.adequacy import eh_report as R

    def run(network, pack, cfg, **kw):
        if reached is not None:
            reached.set()
        if gate is not None:
            assert gate.wait(30)
        rep = _report()
        store = kw.get("store")
        if store is not None:
            R.store_eh_report(store, rep)
        return rep

    monkeypatch.setattr("services.adequacy.eh_study.run_eh_study", run)


def poll(client, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(STUDY_URL)
        if r.status_code == 204:
            return {}
        body = r.json()
        if body.get("status") != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("eh_study never finished")


def hub_project(client, install_network, name: str) -> str:
    """A real, saved project holding the feeder hub, active on the client."""
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    install_network(_feeder_hub())
    r = client.put("/api/simulation/solver_config",
                   json={"solver_name": "highs", "voll": 3000.0})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    return name


def run_study(client) -> dict:
    r = client.post(STUDY_URL, json={"archetype": "weak_flexible"})
    assert r.status_code == 200, r.text
    rec = poll(client)
    assert rec.get("status") == "done", rec
    return rec


def _wait_thread_exit(session_state, client, timeout=5.0):
    rec = session_state(client).get("eh_study") or {}
    t = rec.get("thread")
    if t is not None:
        t.join(timeout)


def _assert_a_has_its_study(client, a, started_at):
    r = client.post(f"/api/projects/{a}/activate")
    assert r.status_code == 200, r.text
    r = client.get(STUDY_URL)
    assert r.status_code == 200, (
        "re-activating the project lost its finished hub study record")
    body = r.json()
    assert body["status"] == "done"
    assert body["started_at"] == started_at
    r = client.get(REVIEW_URL)
    assert r.status_code == 200, r.text
    assert r.json()["stale"] is False


def test_a_finished_study_survives_a_template_create_and_reactivation(
        client, install_network, monkeypatch, session_state, session_ctx):
    fake_done(monkeypatch)
    a = hub_project(client, install_network, "h2-a")
    rec = run_study(client)
    _wait_thread_exit(session_state, client)

    r = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "tpl-b"})
    assert r.status_code == 200, r.text
    assert session_ctx(client).loaded_project == "tpl-b"
    assert client.get(STUDY_URL).status_code == 204
    assert session_state(client).get("eh_study") is None

    _assert_a_has_its_study(client, a, rec["started_at"])


def test_a_finished_study_survives_a_load_of_another_project(
        client, install_network, monkeypatch, session_state, session_ctx, api_project):
    b = api_project("plain-b")
    fake_done(monkeypatch)
    a = hub_project(client, install_network, "h2-load-a")
    rec = run_study(client)
    _wait_thread_exit(session_state, client)

    # Load (GET /api/projects/{name}) of another project.
    r = client.get(f"/api/projects/{b}")
    assert r.status_code == 200, r.text
    assert session_ctx(client).loaded_project == b
    assert client.get(STUDY_URL).status_code == 204
    _assert_a_has_its_study(client, a, rec["started_at"])

    # "New" (POST /api/network/reset).
    assert client.post("/api/network/reset").status_code == 200
    assert session_ctx(client).loaded_project is None
    assert client.get(STUDY_URL).status_code == 204
    _assert_a_has_its_study(client, a, rec["started_at"])


def test_the_fresh_context_still_has_no_study(
        client, install_network, monkeypatch, session_state, api_project):
    """The guarantee the clear existed for, re-pinned against the reorder:
    after each swap the ACTIVE (fresh) context carries no finished record."""
    b = api_project("fresh-b")
    fake_done(monkeypatch)
    a = hub_project(client, install_network, "fresh-a")
    run_study(client)
    _wait_thread_exit(session_state, client)

    for swap in (
        lambda: client.post("/api/projects/from_template/eh_h2_hub",
                            params={"name": "fresh-tpl"}),
        lambda: client.get(f"/api/projects/{b}"),
        lambda: client.post("/api/network/reset"),
    ):
        assert client.post(f"/api/projects/{a}/activate").status_code == 200
        assert session_state(client).get("eh_study") is not None
        r = swap()
        assert r.status_code == 200, r.text
        assert session_state(client).get("eh_study") is None, (
            "a finished study leaked into the fresh context")


def test_a_running_record_is_still_shared_not_copied(client, install_network):
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True, name="fake-study")
    t.start()
    try:
        from tests.test_energy_hub_frontier_fmea import _feeder_hub
        install_network(_feeder_hub())
        rec = {"status": "running", "report": None, "error": None,
               "started_at": 1.0, "finished_at": None, "thread": t}
        with PyPSAService.get_solver_state_lock():
            PyPSAService.get_solver_state()["eh_study"] = rec
        PyPSAService.reset_network(allow_during_study=True)
        assert PyPSAService.get_solver_state().get("eh_study") is rec
    finally:
        release.set()
        t.join(timeout=5)
        PyPSAService.get_solver_state().pop("eh_study", None)


def test_undo_keeps_a_finished_record(
        client, install_network, monkeypatch, session_state):
    fake_done(monkeypatch)
    hub_project(client, install_network, "undo-keep")
    rec = run_study(client)
    _wait_thread_exit(session_state, client)

    bus = client.get("/api/network/buses").json()[0]
    x0 = float(bus.get("x") or 0.0)
    r = client.put(f"/api/network/buses/{bus['name']}",
                   json={**bus, "x": x0 + 0.001})
    assert r.status_code == 200, r.text
    r = client.post("/api/network/undo")
    assert r.status_code == 200, r.text

    # Every read on a fresh request: the context later requests read.
    xs = {b["name"]: float(b.get("x") or 0.0)
          for b in client.get("/api/network/buses").json()}
    assert xs[bus["name"]] == x0
    r = client.get(STUDY_URL)
    assert r.status_code == 200, "the undo dropped the finished study record"
    body = r.json()
    assert body["status"] == "done"
    assert body["started_at"] == rec["started_at"]
    st = session_state(client).get("eh_study")
    assert st.get("thread") is None or not st["thread"].is_alive()
