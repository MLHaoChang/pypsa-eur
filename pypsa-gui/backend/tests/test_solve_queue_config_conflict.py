# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""AA-15: enqueue_unique refuses (opt-in) a differing solver config."""
from __future__ import annotations

import pytest

from services import solve_queue as sq


@pytest.fixture
def queue(monkeypatch):
    q = sq.SolveQueue()
    monkeypatch.setattr(q, "_ensure_dispatcher_locked", lambda: None)
    from services import solve_job_store
    monkeypatch.setattr(solve_job_store, "record_enqueued", lambda *a, **k: None)
    return q


def test_differing_config_refused(queue):
    job, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}')
    assert created
    with pytest.raises(sq.SolveConfigConflict) as ei:
        queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":2}',
                             refuse_config_conflict=True)
    assert ei.value.existing is job
    assert len(queue.list_jobs()) == 1


def test_identical_config_stays_idempotent(queue):
    job, _ = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}')
    again, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}',
                                          refuse_config_conflict=True)
    assert again is job and not created


def test_default_and_missing_config_keep_old_behaviour(queue):
    job, _ = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}')
    same, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":2}')
    assert same is job and not created
    same, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json=None,
                                         refuse_config_conflict=True)
    assert same is job and not created


def test_key_order_and_whitespace_are_not_a_conflict(queue):
    job, _ = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1,"b":2}')
    again, created = queue.enqueue_unique("p", project_key="o:1",
                                          solver_config_json='{ "b": 2, "a": 1 }',
                                          refuse_config_conflict=True)
    assert again is job and not created


def test_other_kind_is_not_refused(queue):
    job, _ = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":1}')
    same, created = queue.enqueue_unique("p", project_key="o:1", solver_config_json='{"a":2}',
                                         kind=sq.KIND_CAMPUS, refuse_config_conflict=True)
    assert same is job and not created


def test_two_threads_differing_configs_one_job(queue):
    import threading
    results, barrier = [], threading.Barrier(2)

    def go(cfg):
        barrier.wait()
        try:
            results.append(queue.enqueue_unique("p", project_key="o:1", solver_config_json=cfg,
                                                refuse_config_conflict=True)[1])
        except sq.SolveConfigConflict:
            results.append("conflict")

    ts = [threading.Thread(target=go, args=(c,)) for c in ('{"a":1}', '{"a":2}')]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(map(str, results)) == ["True", "conflict"]
    assert len(queue.list_jobs()) == 1


def _fresh_route_queue(monkeypatch):
    from routers import solve_queue as route
    from services import solve_job_store
    q = sq.SolveQueue()
    monkeypatch.setattr(q, "_ensure_dispatcher_locked", lambda: None)
    monkeypatch.setattr(solve_job_store, "record_enqueued", lambda *a, **k: None)
    monkeypatch.setattr(route, "solve_queue", q)
    return route, q


def test_route_409_names_job_only(client, install_network, tmp_projects_dir, session_ctx, monkeypatch):
    from tests.conftest import build_network
    route, q = _fresh_route_queue(monkeypatch)
    install_network(build_network(), name="PC")
    assert client.post("/api/projects/PC", params={"force": True, "rebind": True}).status_code == 200
    cfgs = iter(['{"solver":"highs"}', '{"solver":"gurobi"}', '{"solver":"highs"}'])
    monkeypatch.setattr(route, "_config_snapshot_for", lambda *a, **k: next(cfgs))
    first = client.post("/api/simulation/queue", json={"project_id": "PC"})
    assert first.status_code == 200, first.text
    r = client.post("/api/simulation/queue", json={"project_id": "PC"})
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["error_kind"] == "solve_config_conflict"
    assert body["job_id"] == first.json()["id"]
    assert "gurobi" not in r.text and "highs" not in r.text and "owner" not in body
    assert len(q.list_jobs()) == 1
    same = client.post("/api/simulation/queue", json={"project_id": "PC"})
    assert same.status_code == 200 and same.json()["already_queued"] is True


def test_route_fails_closed_for_conflict_subclass(client, install_network, tmp_projects_dir,
                                                  session_ctx, monkeypatch):
    """Kills a mutant that catches a broad base class: other errors must not become 409."""
    from tests.conftest import build_network
    route, q = _fresh_route_queue(monkeypatch)
    install_network(build_network(), name="PD")
    assert client.post("/api/projects/PD", params={"force": True, "rebind": True}).status_code == 200

    def boom(*a, **k):
        raise RuntimeError("not a conflict")
    monkeypatch.setattr(q, "enqueue_unique", boom)
    with pytest.raises(RuntimeError):
        client.post("/api/simulation/queue", json={"project_id": "PD"})


def test_chat_tool_surfaces_error_kind(client, install_network, tmp_projects_dir, session_ctx, monkeypatch):
    from fastapi import HTTPException
    from tests.conftest import build_network
    from services import chat_tools
    route, q = _fresh_route_queue(monkeypatch)
    install_network(build_network(), name="PE")
    assert client.post("/api/projects/PE", params={"force": True, "rebind": True}).status_code == 200
    cfgs = iter(['{"a":1}', '{"a":2}'])
    monkeypatch.setattr(route, "_config_snapshot_for", lambda *a, **k: next(cfgs))
    client.post("/api/simulation/queue", json={"project_id": "PE"})
    with pytest.raises(HTTPException) as ei:
        chat_tools.solve_queue_enqueue("PE")
    assert ei.value.status_code == 409 and ei.value.detail["error_kind"] == "solve_config_conflict"
