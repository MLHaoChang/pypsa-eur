"""
`started_by_me` on the queue listing (run progress slice 3, GK B3 N1/N2).

The finish/fail notification fires only for runs the signed-in user started.
The listing answers that as a per-caller capability, never as the owner's
identity, exactly like `can_dismiss`: `enqueued_by_user_id` stays out of every
response. Unlike `can_dismiss` it is true for the caller's queued and running
jobs as well, since the notification needs to know before the run ends.
"""

from __future__ import annotations

import uuid

import tests.test_solve_queue_dismiss as dismiss
from services.solve_queue import solve_queue
from tests.conftest import build_network


def _row(client, jid):
    return next(
        j for j in client.get("/api/simulation/queue").json()["jobs"]
        if j["id"] == str(jid)
    )


def test_my_job_is_started_by_me_in_every_status(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    dismiss._save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        me = dismiss._acting_user_id(client)
        for status in ("queued", "running", "completed", "failed", "interrupted", "aborted"):
            jid = dismiss._seed(status, "Mine", registry_key_for("Mine"), me)
            assert _row(client, jid)["started_by_me"] is True, status
    finally:
        solve_queue.reset_for_tests()


def test_a_colleagues_visible_job_is_not_started_by_me(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    """Fully visible (not redacted) and still not mine: ownership is consulted."""
    install_network(build_network(), name="Theirs")
    dismiss._save_project(client, "Theirs")
    solve_queue.reset_for_tests()
    try:
        for status in ("running", "completed"):
            jid = dismiss._seed(status, "Theirs", registry_key_for("Theirs"), uuid.uuid4())
            row = _row(client, jid)
            assert row["project_id"] == "Theirs", row
            assert row["started_by_me"] is False, (status, row)
    finally:
        solve_queue.reset_for_tests()


def test_a_legacy_row_with_no_owner_is_not_started_by_me(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    dismiss._save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        jid = dismiss._seed("completed", "Mine", registry_key_for("Mine"), None)
        assert _row(client, jid)["started_by_me"] is False
    finally:
        solve_queue.reset_for_tests()


def test_a_foreign_orgs_redacted_row_is_not_started_by_me(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    solve_queue.reset_for_tests()
    try:
        jid = dismiss._seed("completed", "Theirs", "some-other-org:deadbeef", uuid.uuid4())
        row = _row(client, jid)
        assert row["project_id"] is None, row
        assert row["started_by_me"] is False, row
    finally:
        solve_queue.reset_for_tests()


def test_a_persisted_only_job_still_answers_started_by_me(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    """After a restart the row comes from `solve_jobs`, not `_jobs`."""
    from services import solve_job_store

    install_network(build_network(), name="Mine")
    dismiss._save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        me = dismiss._acting_user_id(client)
        jid = dismiss._seed("interrupted", "Mine", registry_key_for("Mine"), me)
        with solve_queue._lock:
            job = solve_queue._jobs[jid]
        solve_job_store.record_enqueued(job, enqueued_by_user_id=me, solver_config_json=None)
        solve_job_store.record_status(job)
        solve_queue.reset_for_tests()

        assert _row(client, jid)["started_by_me"] is True
    finally:
        solve_queue.reset_for_tests()


def test_no_owner_or_user_id_reaches_the_listing(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    dismiss._save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        me = dismiss._acting_user_id(client)
        other = uuid.uuid4()
        dismiss._seed("running", "Mine", registry_key_for("Mine"), me)
        dismiss._seed("completed", "Theirs", "some-other-org:deadbeef", other)

        body = client.get("/api/simulation/queue").text
        rows = client.get("/api/simulation/queue").json()["jobs"]
        assert rows
        for row in rows:
            assert not any(k.startswith("_") for k in row), row
        assert str(me) not in body
        assert str(other) not in body
    finally:
        solve_queue.reset_for_tests()
