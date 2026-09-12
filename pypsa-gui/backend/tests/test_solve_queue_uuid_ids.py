"""
R23/R37 — a job id is a UUID on the wire and in the model's description.

Per-process `itertools.count(1)` ids collide across replicas: two replicas both
issue id 1, which is harmless while the ids die with the process and is not
harmless the moment a job table outlives it. The blast radius was enumerated in
advance and is exactly three surfaces: the abort route's path parameter,
`SolveJob.id` in `frontend/src/api/solveQueue.ts`, and the int coercion in the
`solve_queue_abort` chat tool.
"""
from __future__ import annotations

import uuid

from services.solve_queue import solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_an_enqueued_job_id_parses_as_a_uuid(client, install_network, tmp_projects_dir):
    install_network(build_network(), name="Ident")
    _save_project(client, "Ident")
    job = client.post("/api/simulation/queue", json={"project_id": "Ident"}).json()
    assert isinstance(job["id"], str)
    uuid.UUID(job["id"])  # raises ValueError if it is not one


def test_abort_takes_the_uuid_and_a_garbage_id_is_a_plain_404(
    client, install_network, tmp_projects_dir,
):
    install_network(build_network(), name="Stoppable")
    _save_project(client, "Stoppable")
    job = client.post("/api/simulation/queue", json={"project_id": "Stoppable"}).json()

    r = client.post(f"/api/simulation/queue/{job['id']}/abort")
    assert r.status_code == 200, r.text

    # A malformed id must be indistinguishable from an unknown one — a 422
    # would say "that is not even a valid id", which is a different oracle but
    # an oracle nonetheless.
    bad = client.post("/api/simulation/queue/not-a-uuid/abort")
    assert bad.status_code == 404, bad.text
    unknown = client.post(f"/api/simulation/queue/{uuid.uuid4()}/abort")
    assert unknown.status_code == 404, unknown.text


def test_the_chat_tool_accepts_the_uuid_string_unchanged(
    client, install_network, tmp_projects_dir,
):
    from services import chat_tools

    install_network(build_network(), name="ChatIdent")
    _save_project(client, "ChatIdent")
    job = client.post("/api/simulation/queue", json={"project_id": "ChatIdent"}).json()
    res = chat_tools.solve_queue_abort(job["id"])
    assert res["id"] == job["id"]


def test_the_abort_tool_description_states_the_id_is_a_uuid():
    from services import chat_tools_schema

    for tool in chat_tools_schema.TOOLS:
        if tool["name"] == "solve_queue_abort":
            assert "UUID" in tool["description"], tool["description"]
            return
    raise AssertionError("no solve_queue_abort tool in the schema")


def test_the_in_memory_job_table_is_keyed_by_uuid():
    solve_queue.reset_for_tests()
    job = solve_queue.enqueue("KeyCheck")
    try:
        assert isinstance(job.id, uuid.UUID)
        assert job.id in solve_queue._jobs
    finally:
        solve_queue.reset_for_tests()
