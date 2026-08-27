"""
R34 — `current: job_id | None` cannot represent a pool.

The scalar was recomputed per request as the FIRST visible running job, and the
route's docstring, the frontend type comment and the `solve_queue_list` tool
description all committed to the singular in prose. Above a concurrency of 1 it
silently reported one arbitrary running job and hid the rest, with no schema
signal that it was truncating — and the chat consumer is TOLD, in prose it
cannot verify, that a truncated field is complete.
"""
from __future__ import annotations

import uuid

from services.solve_queue import SolveJob, solve_queue


def _seed(status: str, name: str, key: str | None) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=name, project_key=key, enqueued_at=0.0)
        job.status = status
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_running_is_a_list_and_current_is_gone(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    from tests.conftest import build_network

    install_network(build_network(), name="Listed")
    r = client.post("/api/projects/Listed", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    key = registry_key_for("Listed")
    solve_queue.reset_for_tests()
    try:
        a = _seed("running", "Listed", key)
        b = _seed("running", "Listed", key)

        payload = client.get("/api/simulation/queue").json()
        assert "current" not in payload, payload
        assert sorted(payload["running"]) == sorted([str(a), str(b)]), payload
    finally:
        solve_queue.reset_for_tests()


def test_an_empty_queue_reports_an_empty_list(client):
    solve_queue.reset_for_tests()
    payload = client.get("/api/simulation/queue").json()
    assert payload["running"] == []


def test_another_orgs_running_job_is_not_listed(
    client, other_org_client, install_network, tmp_projects_dir,
):
    solve_queue.reset_for_tests()
    try:
        theirs = _seed("running", "Theirs", f"{uuid.uuid4()}:{uuid.uuid4()}")

        payload = client.get("/api/simulation/queue").json()
        assert str(theirs) not in payload["running"], (
            "a running job id leaked across orgs — the id alone was enough to abort it"
        )
        # The row itself is still there, redacted, so queue depth stays readable.
        assert any(j["id"] == str(theirs) for j in payload["jobs"])
    finally:
        solve_queue.reset_for_tests()


def test_the_list_tool_description_drops_the_singular():
    from services import chat_tools_schema

    for tool in chat_tools_schema.TOOLS:
        if tool["name"] == "solve_queue_list":
            assert "current" not in tool["description"], tool["description"]
            assert "running" in tool["description"], tool["description"]
            return
    raise AssertionError("no solve_queue_list tool in the schema")
