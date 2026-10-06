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


# ── D-1: the finished EH record travels with the project ──────────────────
# Persisted in results_state.pkl as `eh_study_record` (a declared result-state
# field, always None in memory) with the edit counter in metadata.json, so a
# re-opened or restarted project still knows its study and reads it honestly:
# the restored record is COMPARED against the saved counter, never trusted.

def _save(client, name):
    r = client.post(f"/api/projects/{name}")
    assert r.status_code == 200, r.text


def _drop_resident(registry_key_for, name):
    key = registry_key_for(name)
    with PyPSAService._registry_lock:
        PyPSAService._contexts.pop(key, None)


def _assert_declared_keys(state):
    """B-3: the live dict carries exactly the ProjectSolverState fields — the
    mirror is assigned None at hydrate, never popped."""
    from dataclasses import fields

    from services.project_context import ProjectSolverState
    assert set(state) == {f.name for f in fields(ProjectSolverState)}
    assert state["eh_study_record"] is None


def _pkl_data(project_storage_dir, name):
    from routers.projects import _safe_unpickle_results, _unwrap_results_state
    raw = (project_storage_dir(name) / "results_state.pkl").read_bytes()
    return _unwrap_results_state(_safe_unpickle_results(raw))


def test_a_finished_record_is_saved_and_restored_cold(
        client, install_network, monkeypatch, session_state, registry_key_for,
        project_storage_dir):
    import json

    fake_done(monkeypatch)
    a = hub_project(client, install_network, "persist-a")
    rec = run_study(client)
    _wait_thread_exit(session_state, client)
    _save(client, a)

    data = _pkl_data(project_storage_dir, a)
    mirror = data.get("eh_study_record")
    assert isinstance(mirror, dict) and mirror["status"] == "done"
    assert "thread" not in mirror and "stop_event" not in mirror
    assert mirror["started_at"] == rec["started_at"]
    meta = json.loads((project_storage_dir(a) / "metadata.json").read_text())
    assert meta["network_revision"] == mirror["network_revision"]
    # In memory the mirror is always None (a declared field, never a copy).
    assert session_state(client).get("eh_study_record") is None

    _drop_resident(registry_key_for, a)
    r = client.post(f"/api/projects/{a}/activate")
    assert r.status_code == 200, r.text
    r = client.get(STUDY_URL)
    assert r.status_code == 200, "a cold re-activation lost the saved study"
    body = r.json()
    assert body["status"] == "done"
    assert body["started_at"] == rec["started_at"]
    assert body["edited_since_study"] is False
    _assert_declared_keys(session_state(client))
    # The 409 mesh sees a restored record as finished: a new study is admitted.
    r = client.post(STUDY_URL, json={"archetype": "weak_flexible"})
    assert r.status_code == 200, r.text
    poll(client)


def test_a_restored_record_reads_edited_when_the_saved_network_moved_on(
        client, install_network, monkeypatch, session_state, registry_key_for):
    fake_done(monkeypatch)
    a = hub_project(client, install_network, "persist-edited")
    run_study(client)
    _wait_thread_exit(session_state, client)
    bus = client.get("/api/network/buses").json()[0]
    r = client.put(f"/api/network/buses/{bus['name']}",
                   json={**bus, "x": float(bus.get("x") or 0.0) + 0.001})
    assert r.status_code == 200, r.text
    rev = client.get("/api/network/undo/info").json()["network_revision"]
    _save(client, a)

    _drop_resident(registry_key_for, a)
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    body = client.get(STUDY_URL).json()
    assert body["status"] == "done"
    assert body["edited_since_study"] is True
    assert client.get("/api/network/undo/info").json()["network_revision"] == rev
    r = client.get(REVIEW_URL)
    assert r.status_code == 200, r.text
    assert r.json()["stale"] is False
    assert r.json()["edited_since_study"] is True


def test_a_restart_style_first_request_restores_the_record(
        client, install_network, monkeypatch, session_state, registry_key_for):
    """(E2)'s path: no activate at all — the first request with a stored
    pointer and no resident ctx hydrates through `active_project`."""
    fake_done(monkeypatch)
    a = hub_project(client, install_network, "persist-pointer")
    rec = run_study(client)
    _wait_thread_exit(session_state, client)
    _save(client, a)
    _drop_resident(registry_key_for, a)
    r = client.get(STUDY_URL)
    assert r.status_code == 200, r.text
    assert r.json()["started_at"] == rec["started_at"]
    assert r.json()["edited_since_study"] is False


def test_a_failed_and_an_aborted_record_are_restored_too(
        client, install_network, monkeypatch, session_state, registry_key_for):
    def boom(network, pack, cfg, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("services.adequacy.eh_study.run_eh_study", boom)
    a = hub_project(client, install_network, "persist-failed")
    r = client.post(STUDY_URL, json={"archetype": "weak_flexible"})
    assert r.status_code == 200, r.text
    assert poll(client)["status"] == "failed"
    _wait_thread_exit(session_state, client)
    _save(client, a)
    _drop_resident(registry_key_for, a)
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    body = client.get(STUDY_URL).json()
    assert body["status"] == "failed"
    assert client.get(REVIEW_URL).status_code == 204   # no report: stays 204

    gate = threading.Event()

    def wait_for_stop(network, pack, cfg, **kw):
        assert kw["stop_event"].wait(30)
        return {"archetype": "weak_flexible", "sections": {}, "notes": []}

    monkeypatch.setattr("services.adequacy.eh_study.run_eh_study", wait_for_stop)
    b = hub_project(client, install_network, "persist-aborted")
    assert client.post(STUDY_URL, json={"archetype": "weak_flexible"}).status_code == 200
    deadline = time.time() + 10
    while client.get(STUDY_URL).json().get("status") != "running":
        assert time.time() < deadline
        time.sleep(0.02)
    assert client.post(f"{STUDY_URL}/abort").status_code == 200
    assert poll(client)["status"] == "aborted"
    gate.set()
    _wait_thread_exit(session_state, client)
    _save(client, b)
    _drop_resident(registry_key_for, b)
    assert client.post(f"/api/projects/{b}/activate").status_code == 200
    assert client.get(STUDY_URL).json()["status"] == "aborted"


def test_legacy_pkl_without_the_record_restores_nothing(
        client, install_network, monkeypatch, session_state, registry_key_for,
        project_storage_dir):
    import pickle

    fake_done(monkeypatch)
    a = hub_project(client, install_network, "persist-legacy")
    run_study(client)
    _wait_thread_exit(session_state, client)
    _save(client, a)
    path = project_storage_dir(a) / "results_state.pkl"
    from routers.projects import _RESULTS_STATE_SCHEMA
    data = dict(_pkl_data(project_storage_dir, a))
    data.pop("eh_study_record", None)
    path.write_bytes(pickle.dumps({"__schema__": _RESULTS_STATE_SCHEMA, "data": data}))
    _drop_resident(registry_key_for, a)
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    assert client.get(STUDY_URL).status_code == 204


def test_a_finished_record_survives_save_and_reload_through_load(
        client, install_network, monkeypatch, session_state, api_project):
    """`GET /api/projects/{name}` (load) restores through
    `_restore_results_state`, the active-scoped twin of the hydrate path."""
    b = api_project("persist-other")
    fake_done(monkeypatch)
    a = hub_project(client, install_network, "persist-load")
    rec = run_study(client)
    _wait_thread_exit(session_state, client)
    _save(client, a)
    assert client.get(f"/api/projects/{b}").status_code == 200
    assert client.get(f"/api/projects/{a}").status_code == 200
    body = client.get(STUDY_URL).json()
    assert body["status"] == "done"
    assert body["started_at"] == rec["started_at"]
    assert body["edited_since_study"] is False
    _assert_declared_keys(session_state(client))


def test_hydrate_keeps_a_newer_in_memory_record(tmp_path):
    """The mirror is mapped only when the ctx has no `eh_study` of its own."""
    import pickle

    import pypsa

    from routers.projects import _RESULTS_STATE_SCHEMA, _hydrate_context_from_disk
    from services.project_context import ProjectContext

    n = pypsa.Network()
    n.add("Bus", "b")
    n.export_to_netcdf(tmp_path / "network.nc")
    disk = {"status": "done", "started_at": 1.0, "network_revision": 0}
    (tmp_path / "results_state.pkl").write_bytes(pickle.dumps(
        {"__schema__": _RESULTS_STATE_SCHEMA, "data": {"eh_study_record": disk}}))
    ctx = ProjectContext(network=pypsa.Network())
    planted = {"status": "done", "started_at": 2.0, "network_revision": 3}
    ctx.solver_state["eh_study"] = planted
    _hydrate_context_from_disk(ctx, tmp_path, "p")
    assert ctx.solver_state["eh_study"] is planted
    assert ctx.solver_state["eh_study_record"] is None

    ctx2 = ProjectContext(network=pypsa.Network())
    _hydrate_context_from_disk(ctx2, tmp_path, "p")
    assert ctx2.solver_state["eh_study"]["started_at"] == 1.0
    assert ctx2.solver_state["eh_study"]["thread"] is None
    assert ctx2.solver_state["eh_study_record"] is None


def test_the_mirror_round_trips_through_the_restricted_unpickler(
        client, install_network, monkeypatch, session_state, project_storage_dir):
    fake_done(monkeypatch)
    a = hub_project(client, install_network, "persist-unpickle")
    run_study(client)
    _wait_thread_exit(session_state, client)
    _save(client, a)
    mirror = _pkl_data(project_storage_dir, a)["eh_study_record"]
    assert isinstance(mirror["report"], dict)
    assert mirror["status"] == "done"


# ── The smoke's exact path, in local mode (no session) ────────────────────
# The desktop build has no session cookie, so every request uses the process
# foreground. A project created from a template (or imported from a bundle)
# was bound but never REGISTERED, so the next project switch dropped its whole
# context — the finished study with it — and re-activation hydrated a copy
# from disk. 10a holds only if the created project's ctx is resident, as
# `load_project` already makes it (`PyPSAService.register`).
from tests.test_local_mode_api import local_client  # noqa: E402,F401 — fixture


def test_local_mode_template_project_keeps_its_study_across_a_switch(
        local_client, monkeypatch):
    c = local_client
    fake_done(monkeypatch)
    assert c.post("/api/projects/from_template/eh_h2_hub",
                  params={"name": "LM H2"}).status_code == 200
    r = c.post(STUDY_URL, json={"archetype": "weak_flexible"})
    assert r.status_code == 200, r.text
    rec = poll(c)
    assert rec.get("status") == "done", rec
    assert c.post("/api/projects/from_template/eh_microgrid",
                  params={"name": "LM MG"}).status_code == 200
    assert c.get(STUDY_URL).status_code == 204
    assert c.post("/api/projects/LM%20MG/activate").status_code == 200
    assert c.post("/api/projects/LM%20H2/activate").status_code == 200
    r = c.get(STUDY_URL)
    assert r.status_code == 200, (
        "local mode: leaving a template-created project dropped its context "
        "and the finished study with it")
    assert r.json()["started_at"] == rec["started_at"]


def test_a_created_project_is_resident_under_its_own_key(
        client, registry_key_for, session_ctx, api_project):
    a = api_project("bundle-src")
    r = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "res-tpl"})
    assert r.status_code == 200, r.text
    key = registry_key_for("res-tpl")
    with PyPSAService._registry_lock:
        held = PyPSAService._contexts.get(key)
        bound = [k for k, c in PyPSAService._contexts.items() if c.loaded_project == "res-tpl"]
    assert held is not None, "the created project's ctx is not resident"
    assert held is session_ctx(client)
    assert bound == [key]

    bundle = client.get(f"/api/projects/{a}/bundle").content
    r = client.post("/api/projects/import_bundle",
                    files={"file": ("b.zip", bundle, "application/zip")},
                    params={"name": "res-imported"})
    assert r.status_code == 200, r.text
    key2 = registry_key_for("res-imported")
    with PyPSAService._registry_lock:
        held2 = PyPSAService._contexts.get(key2)
        bound2 = [k for k, c in PyPSAService._contexts.items() if c.loaded_project == "res-imported"]
    assert held2 is not None and held2 is session_ctx(client)
    assert bound2 == [key2]


# ── Gate S-1: a restore drops a record the snapshot does not carry ────────

def test_a_snapshot_without_results_does_not_bring_back_a_later_study(
        client, install_network, monkeypatch, session_state, project_storage_dir):
    """The restore copied only the files the snapshot HAS, so a snapshot taken
    with no results left the project's later `results_state.pkl` in place: its
    study record — of a different network — was mapped onto the restored one,
    and a counter that the restore moved back could read "not edited"."""
    def move(dx):
        b = client.get("/api/network/buses").json()[0]
        r = client.put(f"/api/network/buses/{b['name']}",
                       json={**b, "x": float(b.get("x") or 0) + dx})
        assert r.status_code == 200, r.text

    def rev():
        return client.get("/api/network/undo/info").json()["network_revision"]

    def xs():
        return [b["x"] for b in client.get("/api/network/buses").json()]

    fake_done(monkeypatch)
    a = hub_project(client, install_network, "coll")
    s0 = client.post(f"/api/projects/{a}/snapshots", json={"label": "s0"}).json()["id"]
    r0 = rev()
    move(1)
    move(1)
    assert client.post(f"/api/projects/{a}", params={"force": True}).status_code == 200
    assert not (project_storage_dir(a) / "results_state.pkl").exists()
    s1 = client.post(f"/api/projects/{a}/snapshots", json={"label": "s1"}).json()["id"]
    x_s1 = xs()
    assert client.post(f"/api/projects/{a}/snapshots/{s0}/restore").status_code == 200
    assert rev() == r0
    move(5)
    move(5)
    assert rev() == r0 + 2
    x_studied = xs()
    run_study(client)
    _wait_thread_exit(session_state, client)
    assert client.post(f"/api/projects/{a}", params={"force": True}).status_code == 200
    assert client.post(f"/api/projects/{a}/snapshots/{s1}/restore").status_code == 200
    assert xs() == x_s1 and x_s1 != x_studied
    r = client.get(STUDY_URL)
    assert r.status_code == 204, (
        f"S1 carries no study, but {r.json() if r.status_code == 200 else r.status_code} "
        "was restored from the project's later results_state.pkl")
    assert not (project_storage_dir(a) / "results_state.pkl").exists()
