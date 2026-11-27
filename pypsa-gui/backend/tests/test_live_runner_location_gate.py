"""
STUDY-GATE-1 (issue 48): the live-network runner starts and the solve queue
refuse an unlocated project with 422 typed `site_location_missing`, before any
worker, record, publish or queue row exists.

Per gated start: unlocated -> refused, draft anchor -> refused, confirmed anchor
-> not that code. "Nothing started" is a spy on `routers.results._publish_study`
(the one place a study record is published and its thread started) and, for the
queue, an empty job list. The queue is also re-checked when the worker dequeues.
"""

# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services import site_location
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.conftest import build_network

pytestmark = pytest.mark.unlocated

CODE = "site_location_missing"

# route -> body. `front` has a required field.
ROUTES = {
    "fmea_sweep": {},
    "front": {"levels": [1.0]},
    "frontier": {},
    "mc": {},
    "coupling_loop": {},
    "margin_loop": {},
}


def _save(client, install_network, name="Gate"):
    install_network(build_network(), name=name)
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    return name


def _put_anchor(client, name, confirmed: bool):
    rev = client.get(f"/api/projects/{name}/site-anchor").json()["revision"]
    r = client.put(
        f"/api/projects/{name}/site-anchor",
        json={
            "anchor": {
                "lat": 52.52,
                "lng": 13.4,
                "source": "manual",
                "confirmed": confirmed,
            },
            "expected_revision": rev,
        },
    )
    assert r.status_code == 200, r.text


def _assert_refused(r):
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert isinstance(d, dict), d
    assert d["code"] == d["error_kind"] == CODE and d["field"] == "x", d


def _not_the_gate(r):
    d = r.json().get("detail") if r.status_code == 422 else None
    assert not (isinstance(d, dict) and d.get("code") == CODE), r.text


@pytest.fixture
def published(monkeypatch):
    """Spy on the study publish. It never starts a thread: a start that gets
    past its gate is stopped here with a 418."""
    from routers import results

    calls: list = []

    def spy(key, record, thread):
        calls.append(key)
        raise HTTPException(418, "stub: past the gate")

    monkeypatch.setattr(results, "_publish_study", spy)
    return calls


@pytest.mark.parametrize("route", list(ROUTES))
def test_a_runner_start_refuses_an_unlocated_project_and_starts_nothing(
    client, install_network, tmp_projects_dir, published, route
):
    name = _save(client, install_network)
    url, body = f"/api/results/{route}", ROUTES[route]
    _assert_refused(client.post(url, json=body))
    _put_anchor(client, name, confirmed=False)  # a draft locates nothing
    _assert_refused(client.post(url, json=body))
    assert published == []
    _put_anchor(client, name, confirmed=True)
    _not_the_gate(client.post(url, json=body))


def test_the_investment_case_start_refuses_an_unlocated_project(
    client, install_network, tmp_projects_dir, bound_ctx
):
    """The case re-solves the site-today baseline on the active network. The
    route answers 409 `not_solved` before the start on an unsolved network, so
    the start is called directly."""
    from services.finance.investment_case_runner import start_investment_case

    name = _save(client, install_network)
    published: list = []

    def start():
        with bound_ctx(client):
            return start_investment_case(
                None,
                build_case=lambda: None,
                solver_state={},
                state_update=lambda **kw: None,
                publish_study=lambda *a: published.append(a),
            )

    for confirmed in (None, False):
        if confirmed is False:
            _put_anchor(client, name, confirmed=False)
        with pytest.raises(HTTPException) as exc:
            start()
        d = exc.value.detail
        assert exc.value.status_code == 422
        assert d["code"] == d["error_kind"] == CODE and d["field"] == "x"
    assert published == []
    _put_anchor(client, name, confirmed=True)
    with pytest.raises(HTTPException) as exc:
        start()
    assert not (
        isinstance(exc.value.detail, dict) and exc.value.detail.get("code") == CODE
    )
    assert published == []


# ── solve queue ─────────────────────────────────────────────────────────────


@pytest.fixture
def idle_queue():
    solve_queue.reset_for_tests()
    yield solve_queue
    solve_queue.reset_for_tests()


def test_enqueue_refuses_an_unlocated_project_and_queues_nothing(
    client, install_network, tmp_projects_dir, idle_queue
):
    name = _save(client, install_network)
    client.post("/api/simulation/queue/pause")
    for confirmed in (None, False):
        if confirmed is False:
            _put_anchor(client, name, confirmed=False)
        _assert_refused(client.post("/api/simulation/queue", json={"project_id": name}))
        assert solve_queue.list_jobs() == []
    _put_anchor(client, name, confirmed=True)
    r = client.post("/api/simulation/queue", json={"project_id": name})
    assert r.status_code == 200, r.text
    assert len(solve_queue.list_jobs()) == 1


def test_the_gate_reads_the_enqueued_projects_saved_network_when_not_resident(
    tmp_path, monkeypatch
):
    """A project that is not resident is judged on the `network.nc` the job
    would hydrate, in its own directory (never the foreground's)."""
    build_network().export_to_netcdf(tmp_path / "network.nc")
    got = site_location.queued_refusal("org:not-resident", tmp_path)
    assert got is not None and got["code"] == got["error_kind"] == CODE
    monkeypatch.setattr(site_location, "anchor_confirmed", lambda _d: True)
    assert site_location.queued_refusal("org:not-resident", tmp_path) is None


def test_requeue_refuses_an_unlocated_project(
    client,
    install_network,
    tmp_projects_dir,
    registry_key_for,
    project_storage_dir,
    idle_queue,
):
    import uuid

    from services.solve_queue import SolveJob

    name = _save(client, install_network)
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(
            id=jid,
            project_id=name,
            project_key=registry_key_for(name),
            storage_dir=str(project_storage_dir(name)),
            enqueued_at=time.time(),
        )
        job.status, job.finished_at = "failed", time.time()
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    _assert_refused(client.post(f"/api/simulation/queue/{jid}/requeue"))
    assert len(solve_queue.list_jobs()) == 1  # the old row only


def test_the_worker_rechecks_at_dequeue_and_never_solves(
    client, install_network, tmp_projects_dir, idle_queue, monkeypatch
):
    """A job queued while the gate was off (or persisted before it existed)
    fails typed when dequeued and is never solved."""
    from services import solver_service

    name = _save(client, install_network)
    solved: list = []
    monkeypatch.setattr(
        solver_service, "run_simulation", lambda *a, **k: solved.append(1)
    )
    client.post("/api/simulation/queue/pause")
    with monkeypatch.context() as off:
        off.setattr(site_location, "queued_refusal", lambda *a, **k: None)
        r = client.post("/api/simulation/queue", json={"project_id": name})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    client.post("/api/simulation/queue/resume")
    deadline = time.time() + 30
    job = None
    while time.time() < deadline:
        job = client.get("/api/simulation/queue").json()
        job = next(
            j for j in (job if isinstance(job, list) else job["jobs"]) if j["id"] == jid
        )
        if job["status"] in ("failed", "completed", "aborted"):
            break
        time.sleep(0.1)
    assert job["status"] == "failed", job
    assert job["condition"] == CODE and job["error"].startswith(CODE), job
    assert solved == []


def test_the_enqueue_gate_does_not_wait_for_a_running_solve(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir
):
    """A running solve holds the resident context's network lock for its whole
    run: the gate reads unlocked rather than waiting on it."""
    import threading

    from services.pypsa_service import PyPSAService

    name = _save(client, install_network)
    key = registry_key_for(name)
    resident = PyPSAService.get_context(key)
    assert resident is not None
    held, release = threading.Event(), threading.Event()

    def hold():
        with resident.mutation_lock:
            held.set()
            release.wait(30)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(5)
    out: list = []
    asker = threading.Thread(
        target=lambda: out.append(
            site_location.queued_refusal(key, project_storage_dir(name))
        ),
        daemon=True,
    )
    try:
        asker.start()
        asker.join(5)
        assert not asker.is_alive(), "the gate waited for the network lock"
        assert out and out[0]["code"] == CODE
    finally:
        release.set()
        holder.join(5)


def test_the_business_case_rebuild_refuses_an_unlocated_baseline_resolve(
    client, install_network, tmp_projects_dir, bound_ctx, monkeypatch
):
    """The case rebuild (investment case and business case both) re-solves the
    site-today baseline on a copy of the live network: an unlocated project is
    refused with the typed code before any LP; a located one reaches the solve."""
    from routers import results
    from services.finance.case import FinanceRefused
    from services.results import baseline as B
    from services.results import finance_case as fc

    name = _save(client, install_network)
    solved: list = []

    class Plan:
        needed = True

    monkeypatch.setattr(fc, "baseline_plan", lambda *a, **k: Plan())
    monkeypatch.setattr(
        B,
        "solve_baseline",
        lambda *a, **k: solved.append(1) or (_ for _ in ()).throw(RuntimeError("stub")),
    )
    monkeypatch.setattr(
        "models.finance.FinanceInputs.model_validate", lambda *a, **k: object()
    )

    def build():
        with bound_ctx(client):
            n = PyPSAService.get_network()
            return results._ic_build_case(
                n, SimpleNamespace(finance=None), owner=None, lost_load=None
            )()

    for confirmed in (None, False):
        if confirmed is False:
            _put_anchor(client, name, confirmed=False)
        with pytest.raises(FinanceRefused) as exc:
            build()
        assert exc.value.code == CODE
        assert solved == []
    _put_anchor(client, name, confirmed=True)
    with pytest.raises(RuntimeError, match="stub"):
        build()
    assert solved == [1]


def test_failure_card_names_the_location_and_the_fix():
    from services.failure_taxonomy import classify_failure

    card = classify_failure("failed", "site_location_missing")
    assert card["category"] == "site_location_missing"
    assert "no location" in card["title"]
    assert "latitude" in card["hint"].lower() and "longitude" in card["hint"].lower()
