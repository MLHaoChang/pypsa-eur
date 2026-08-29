"""
R31 — any terminal job can be run again in one action.

All four terminal statuses are eligible on identical terms, `interrupted`
included: R25 bars only AUTOMATIC re-enqueue at boot (so a job that crashed the
process cannot crash-loop it), never a user's explicit decision to try again.

Subject to R15: requeueing a project that already has an active job returns that
job with `already_queued: true` rather than creating a duplicate.
"""
from __future__ import annotations

import time
import uuid

from fastapi.testclient import TestClient

import main
from services.solve_queue import SolveJob, solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _seed_terminal(status: str, name: str, key: str, storage_dir: str) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(
            id=jid, project_id=name, project_key=key,
            storage_dir=storage_dir, enqueued_at=time.time(),
        )
        job.status = status
        job.finished_at = time.time()
        job.solver_config_json = '{"solver_name": "highs", "co2_price": 7.0}'
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_every_terminal_status_is_requeueable_interrupted_included(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Retry")
    _save_project(client, "Retry")
    key = registry_key_for("Retry")
    where = str(project_storage_dir("Retry"))

    for status in ("completed", "failed", "aborted", "interrupted"):
        solve_queue.reset_for_tests()
        old = _seed_terminal(status, "Retry", key, where)
        r = client.post(f"/api/simulation/queue/{old}/requeue")
        assert r.status_code == 200, (status, r.text)
        body = r.json()
        assert body["already_queued"] is False, (status, body)
        assert body["id"] != str(old), status
        assert body["project_id"] == "Retry"
    solve_queue.reset_for_tests()


def test_the_new_job_inherits_the_original_config_snapshot(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    import json as _json
    from sqlalchemy import select

    from db.models import SolveJobRow
    from db.session import SessionLocal

    install_network(build_network(), name="Retry2")
    _save_project(client, "Retry2")
    solve_queue.reset_for_tests()
    old = _seed_terminal(
        "failed", "Retry2", registry_key_for("Retry2"), str(project_storage_dir("Retry2")),
    )
    try:
        body = client.post(f"/api/simulation/queue/{old}/requeue").json()
        with SessionLocal() as db:
            row = db.scalar(select(SolveJobRow).where(SolveJobRow.id == uuid.UUID(body["id"])))
        assert row is not None
        assert _json.loads(row.solver_config)["co2_price"] == 7.0, (
            "requeue re-resolved the config instead of reproducing the run"
        )
    finally:
        solve_queue.reset_for_tests()


def test_a_queued_or_running_job_is_not_requeueable(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Busy")
    _save_project(client, "Busy")
    key = registry_key_for("Busy")
    where = str(project_storage_dir("Busy"))
    solve_queue.reset_for_tests()
    try:
        for status in ("queued", "running"):
            jid = _seed_terminal(status, "Busy", key, where)
            with solve_queue._lock:
                solve_queue._jobs[jid].status = status
                solve_queue._jobs[jid].finished_at = None
            r = client.post(f"/api/simulation/queue/{jid}/requeue")
            assert r.status_code == 409, (status, r.text)
    finally:
        solve_queue.reset_for_tests()


def test_requeue_is_subject_to_the_duplicate_rule(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Once")
    _save_project(client, "Once")
    key = registry_key_for("Once")
    where = str(project_storage_dir("Once"))
    solve_queue.reset_for_tests()
    try:
        client.post("/api/simulation/queue/pause")
        old = _seed_terminal("completed", "Once", key, where)
        first = client.post(f"/api/simulation/queue/{old}/requeue").json()
        second = client.post(f"/api/simulation/queue/{old}/requeue").json()
        assert second["already_queued"] is True
        assert second["id"] == first["id"]
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_a_job_the_caller_may_not_see_404s(
    client, other_org_client, install_network, tmp_projects_dir,
    registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Hidden")
    _save_project(client, "Hidden")
    solve_queue.reset_for_tests()
    try:
        old = _seed_terminal(
            "completed", "Hidden", registry_key_for("Hidden"), str(project_storage_dir("Hidden")),
        )
        assert other_org_client.post(f"/api/simulation/queue/{old}/requeue").status_code == 404
    finally:
        solve_queue.reset_for_tests()


def test_requeue_of_a_persisted_only_interrupted_job_survives_a_restart(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    """
    Ruling 2: an `interrupted` job's in-memory `SolveJob` does not survive a
    restart, only its row does (`_persisted_public_or_none`). Requeue must
    still work for it, sourcing `storage_dir` / `solver_config_json` from the
    persisted row rather than from memory (which is empty after the reset).
    """
    from services import solve_job_store

    install_network(build_network(), name="Crashed")
    _save_project(client, "Crashed")
    key = registry_key_for("Crashed")
    where = str(project_storage_dir("Crashed"))
    solve_queue.reset_for_tests()
    try:
        old = _seed_terminal("running", "Crashed", key, where)
        with solve_queue._lock:
            solve_queue._jobs[old].status = "running"
            solve_queue._jobs[old].finished_at = None
        # Persist the row (record_enqueued + record_status), then simulate a
        # process restart: drop the in-memory SolveJob and let boot
        # reconciliation flip the surviving `running` row to `interrupted`.
        with solve_queue._lock:
            job = solve_queue._jobs[old]
        solve_job_store.record_enqueued(
            job, enqueued_by_user_id=None, solver_config_json=job.solver_config_json,
        )
        solve_job_store.record_status(job)
        solve_queue.reset_for_tests()
        solve_job_store.reconcile_on_boot()

        r = client.post(f"/api/simulation/queue/{old}/requeue")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["already_queued"] is False
        assert body["id"] != str(old)
        assert body["project_id"] == "Crashed"

        import json as _json
        from sqlalchemy import select

        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            row = db.scalar(select(SolveJobRow).where(SolveJobRow.id == uuid.UUID(body["id"])))
        assert row is not None
        assert row.storage_dir == where
        assert _json.loads(row.solver_config)["co2_price"] == 7.0
    finally:
        solve_queue.reset_for_tests()


def test_requeue_after_rename_uses_the_current_directory_and_name(
    _auth_db, monkeypatch, tmp_path, install_network,
):
    """
    Fix round 1 (review finding). In LOCAL mode, `project_registry.rename_project`
    MOVES the project directory on disk. A requeue must resolve the project's
    CURRENT directory and name fresh from the DB row — via the source job's
    `project_key` — rather than reusing the source job's captured
    `storage_dir` / display name: enqueue "Alpha" -> finish -> rename Alpha to
    "Beta" (directory moves) -> requeue the old job must carry Beta's CURRENT
    directory, not Alpha's now-gone one, and the old directory must not be
    recreated.
    """
    import uuid as _uuid

    from sqlalchemy import select

    import local_mode
    from db.models import Project
    from services import project_registry

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as local_client:
            local_client.cookies.clear()
            install_network(build_network(), name="Alpha")
            r = local_client.post(
                "/api/projects/Alpha", params={"force": True, "rebind": True}
            )
            assert r.status_code == 200, r.text

            with session_local() as db:
                project = db.scalar(select(Project).where(Project.name == "Alpha"))
                assert project is not None
                key = f"{project.org_id}:{project.id}"
                old_dir = project_registry.project_dir(project)
            assert old_dir.exists()

            solve_queue.reset_for_tests()
            jid = _uuid.uuid4()
            with solve_queue._lock:
                job = SolveJob(
                    id=jid, project_id="Alpha", project_key=key,
                    storage_dir=str(old_dir), enqueued_at=time.time(),
                )
                job.status = "completed"
                job.finished_at = time.time()
                job.solver_config_json = '{"solver_name": "highs", "co2_price": 7.0}'
                solve_queue._jobs[jid] = job
                solve_queue._order.append(jid)

            r = local_client.post(
                "/api/projects/Alpha/rename", json={"new_name": "Beta"}
            )
            assert r.status_code == 200, r.text

            with session_local() as db:
                renamed = db.get(Project, project.id)
                new_dir = project_registry.project_dir(renamed)
            assert new_dir != old_dir
            assert new_dir.exists()
            assert not old_dir.exists(), (
                "requeue must not have recreated the stale pre-rename directory"
            )

            resp = local_client.post(f"/api/simulation/queue/{jid}/requeue")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["project_id"] == "Beta", "requeue served the stale display name"
            assert not old_dir.exists(), (
                "requeue recreated the pre-rename directory via a stale storage_dir"
            )

            import json as _json

            from db.models import SolveJobRow
            from db.session import SessionLocal as _SessionLocal

            with _SessionLocal() as db:
                row = db.scalar(
                    select(SolveJobRow).where(SolveJobRow.id == _uuid.UUID(body["id"]))
                )
            assert row is not None
            assert row.storage_dir == str(new_dir), (
                "requeue carried the source job's stale storage_dir instead of "
                "resolving the project's current directory"
            )
            assert _json.loads(row.solver_config)["co2_price"] == 7.0
    finally:
        solve_queue.reset_for_tests()
        with session_local() as db:
            local_mode.remove_local_identity(db)


# A MUTATION CHECK ONLY PROVES SOMETHING IF THE MUTANT ACTUALLY RUNS THE
# MUTATED LINE, and nothing tells you whether it did.
#
# A surviving mutant has two causes that look identical and need opposite
# fixes: the assertion is vacuous, or the mutated code was never reached. The
# test below hit the second. Its assertion was fine; a null `storage_dir` made
# the route 404 at its "no saved network on disk" check, several statements
# before the branch under mutation, so widening `_may_see` changed nothing that
# could reach it. Both attempts reported "mutant survived" and only one of them
# meant "the test is weak".
#
# What distinguishes them is a DIFFERENT OBSERVABLE. "Still fails" is
# compatible with never having run; "the status went 404 -> 200" is not. Seeding
# a real `storage_dir` gave that: the mutant reaches the branch, requeues
# successfully, and the failure reads `200 == 404` — which simultaneously fixes
# the test and demonstrates the branch is genuinely exploitable.
#
# Recorded here rather than only in the commit message because the next person
# doing mutation checking will assume reaching the code is the easy part.
def test_an_unkeyed_job_is_not_requeueable_under_auth(
    client, install_network, tmp_projects_dir, project_storage_dir,
):
    """
    Pins the invariant that makes requeue's holder check COMPLETE.

    `requeue_job` resolves its project from `old["project_key"]`. When that
    yields nothing it falls back to whatever the job itself captured — a
    branch that necessarily has no `project.id`, and therefore CANNOT carry
    the `project_locks.get_lock` check the keyed branch does. If that branch
    were reachable under auth it would be a hole straight through the fix:
    requeue a legacy job, solve and save its project, with no lock consulted
    at any layer.

    It is not reachable, and the reason lives in a different function:
    `_may_see` answers `local_mode.is_local_mode()` for a job with no
    `project_key`, so under auth `_visible_job_or_404` 404s before the branch
    is ever entered — and in local mode there is one tenant and one user, so
    no foreign lock can exist to check.

    That is a two-function argument with nothing tying the halves together, so
    a later widening of `_may_see` (say, attributing unkeyed jobs by
    `enqueued_by_user_id` — a plausible cleanup now that the column exists)
    would silently open the unchecked branch. Nothing in `requeue_job` would
    look wrong, and no existing test would fail. This one would.

    Asserted through the ROUTE rather than on `_may_see` directly: what must
    hold is "the unchecked branch is unreachable", and only the route can
    answer that.
    """
    install_network(build_network(), name="Legacy")
    _save_project(client, "Legacy")
    solve_queue.reset_for_tests()
    try:
        jid = uuid.uuid4()
        with solve_queue._lock:
            # No project_key — the pre-Step-0a shape — but a REAL
            # `storage_dir` holding a real `network.nc`.
            #
            # That detail is the whole test. A null storage_dir makes the
            # route 404 at its "no saved network on disk" check, which is a
            # DIFFERENT 404 arriving BEFORE the branch under test and
            # independent of `_may_see` entirely. Verified by mutation: with
            # `_may_see` widened to admit unkeyed jobs, the null-dir version
            # of this test still passed — it was pinning nothing. With a real
            # directory the mutant reaches the fallback branch, requeues
            # successfully, and the assertion below fails as it must.
            job = SolveJob(
                id=jid,
                project_id="Legacy",
                storage_dir=str(project_storage_dir("Legacy")),
                enqueued_at=0.0,
            )
            job.status = "completed"
            solve_queue._jobs[jid] = job
            solve_queue._order.append(jid)

        r = client.post(f"/api/simulation/queue/{jid}/requeue")
        assert r.status_code == 404, (
            "an unkeyed job reached requeue's fallback branch under auth — that "
            "branch cannot check the project lock, so requeue would solve and "
            "save a project with no holder check at any layer. See _may_see."
        )
    finally:
        solve_queue.reset_for_tests()
