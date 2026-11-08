"""
A queued solve stamps its provenance from the project it was queued for.

The dispatcher thread has no request, so `PyPSAService.get_active_context()`
there resolves the process foreground, which may be another project. SH-07b's
trajectory evaluation (#1282) caught two reads that used it: the sizing
record's revision (`solver_service._revision_or_none`) and the org of the
change-log "save" entry the queue's write-back leaves (`_active_org_id`). Both
must come from the solving project, and fail closed when it has none.
"""

from __future__ import annotations

import time
import uuid

import pypsa
import pytest

from services import change_log_service
from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.conftest import build_network

DECOY_ORG = "decoy-org"
DECOY_REVISION = 9999


def _wait_for_terminal(job_id, timeout: float = 90.0) -> dict:
    job_id = uuid.UUID(str(job_id))
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = solve_queue.get_job(job_id) or {}
        if job.get("status") in ("completed", "failed", "aborted"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job did not finish within {timeout}s")


def _make_decoy_foreground(monkeypatch) -> ProjectContext:
    """Another org's project as the process foreground: what the dispatcher
    thread resolves `get_active_context()` to."""
    n = pypsa.Network()
    n.name = "Decoy"
    decoy = ProjectContext(network=n, org_id=DECOY_ORG)
    decoy.network_revision = DECOY_REVISION
    monkeypatch.setattr(PyPSAService, "_active", decoy)
    return decoy


@pytest.fixture
def decoy_foreground(monkeypatch):
    return _make_decoy_foreground(monkeypatch)


def test_a_queued_solve_stamps_its_own_projects_revision_and_org(
    client,
    install_network,
    tmp_projects_dir,
    registry_key_for,
    monkeypatch,
):
    install_network(build_network(solve=False), name="SQA")
    assert (
        client.post(
            "/api/projects/SQA", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    key = registry_key_for("SQA")
    org_a = key.partition(":")[0]
    assert org_a != DECOY_ORG
    revision_a = int(PyPSAService.get_context(key).network_revision or 0)
    decoy_foreground = _make_decoy_foreground(monkeypatch)
    change_log_service.clear()

    r = client.post("/api/simulation/queue", json={"project_id": "SQA"})
    assert r.status_code == 200, r.text
    done = _wait_for_terminal(r.json()["id"])
    assert done["status"] == "completed", done

    ctx_a = PyPSAService.get_context(key)
    assert ctx_a is not None and ctx_a is not decoy_foreground
    record = ctx_a.network.meta.get("sizing_record")
    assert isinstance(record, dict), "the queued sizing solve left no sizing record"
    assert record["revision"] == revision_a != DECOY_REVISION, (
        f"the sizing record carries revision {record['revision']!r}, not project A's"
    )

    # The foreground gets nothing (GK B, Q4).
    assert int(decoy_foreground.network_revision) == DECOY_REVISION
    assert (
        change_log_service.get_all(org_id=DECOY_ORG)
        == [e for e in change_log_service.get_all() if e["org_id"] is None]
        == []
    ), "the decoy's org gained a change-log entry from A's solve"

    saves = [
        e
        for e in change_log_service.get_all()
        if e["action"] == "save" and e["name"] == "SQA"
    ]
    assert saves, "the queued solve's write-back left no save entry"
    assert {e["org_id"] for e in saves} == {org_a}, (
        f"the save entry was logged under {[e['org_id'] for e in saves]!r}, not A's org"
    )


def test_the_revision_of_a_network_no_context_owns_is_none(decoy_foreground):
    from services.solver_service import _revision_or_none

    assert _revision_or_none(decoy_foreground.network) == DECOY_REVISION
    assert _revision_or_none(pypsa.Network()) is None
    solving = ProjectContext(network=pypsa.Network())
    solving.network_revision = 7
    with PyPSAService.solving_context(solving):
        assert _revision_or_none(solving.network) == 7


def test_the_owning_org_binds_the_log_and_an_unknown_one_logs_nothing(decoy_foreground):
    from services.solve_queue import _owning_org

    change_log_service.clear()
    with _owning_org("org-a:1234"):
        change_log_service.log("save", "Project", "P1", "bound")
    with _owning_org("P2"):  # a hand-made job's name key: no org
        change_log_service.log("save", "Project", "P2", "unbound")
    change_log_service.log("save", "Project", "P3", "foreground")
    orgs = {e["name"]: e["org_id"] for e in change_log_service.get_all()}
    assert orgs == {"P1": "org-a", "P3": DECOY_ORG}


@pytest.mark.live_solve
def test_a_foreground_solve_records_the_active_revision_as_master_did(reset_backend):
    """The /run path's record is unchanged: master stamped
    `dirty_state.revision()` (the active ctx), and the active ctx owns the
    network a foreground solve runs on."""
    from services import dirty_state
    from tests.test_fixed_sizes import _install, _plan_net, _solve

    n = _install(_plan_net())
    PyPSAService.get_active_context().network_revision = 5
    assert _solve()[0] in ("ok", "optimal")
    assert n.meta["sizing_record"]["revision"] == dirty_state.revision() == 5


def test_a_record_without_a_revision_reads_as_no_provenance():
    """Every reader of the sizing record's revision (GK B, Q1):
    `fixed_sizes.run_label` (services/solver/fixed_sizes.py:185) passes it on as
    `source_revision`; `grid_risk_production._solve_stamp`
    (services/results/grid_risk_production.py:690) omits a None field."""
    from services.results.grid_risk_production import _solve_stamp
    from services.solver import fixed_sizes
    from services.solver_service import SolverConfig

    n = pypsa.Network()
    n.meta["sizing_record"] = {
        "revision": None,
        "solved_at": "2026-11-08T00:00:00+00:00",
        "sha256": "x",
        "sizes": {},
        "topology": "t",
    }
    label = fixed_sizes.run_label(n, SolverConfig(fixed_sizes="capacity_run"))
    assert label["source_revision"] is None and label["source"] == "capacity_run"
    assert _solve_stamp(n) == {"solved_at": "2026-11-08T00:00:00+00:00"}


def test_an_unresolvable_key_writes_no_save_entry(
    client,
    install_network,
    tmp_projects_dir,
    registry_key_for,
    monkeypatch,
):
    """A job whose key names no org (a hand-made job) has no owner to log under:
    its write-back saves the project and logs nothing (GK B, Q3)."""
    install_network(build_network(solve=False), name="SQN")
    assert (
        client.post(
            "/api/projects/SQN", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    real_enqueue = solve_queue.enqueue_unique

    def org_less(project_id, *, project_key=None, **kw):
        return real_enqueue(project_id, project_key=project_id, **kw)

    monkeypatch.setattr(solve_queue, "enqueue_unique", org_less)
    _make_decoy_foreground(monkeypatch)
    change_log_service.clear()
    r = client.post("/api/simulation/queue", json={"project_id": "SQN"})
    assert r.status_code == 200, r.text
    assert _wait_for_terminal(r.json()["id"])["status"] == "completed"
    assert [e for e in change_log_service.get_all() if e["action"] == "save"] == []


def test_local_mode_still_logs_the_queued_save_under_its_one_org(
    tmp_path,
    monkeypatch,
    _auth_db,
    install_network,
):
    """Local mode keys every job `LOCAL_ORG_ID:uuid`, so the save entry is the
    one master wrote (the foreground there is the same project)."""
    import local_mode
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    solve_queue.reset_for_tests()
    try:
        with TestClient(main.app) as local_client:
            local_client.cookies.clear()
            install_network(build_network(solve=False), name="SQL")
            assert (
                local_client.post(
                    "/api/projects/SQL", params={"force": True, "rebind": True}
                ).status_code
                == 200
            )
            change_log_service.clear()
            r = local_client.post("/api/simulation/queue", json={"project_id": "SQL"})
            assert r.status_code == 200, r.text
            assert _wait_for_terminal(r.json()["id"])["status"] == "completed"
            saves = [
                {k: v for k, v in e.items() if k not in ("id", "timestamp")}
                for e in change_log_service.get_all()
                if e["action"] == "save"
            ]
            assert saves == [
                {
                    "action": "save",
                    "component_type": "Project",
                    "name": "SQL",
                    "description": saves[0]["description"] if saves else None,
                    "org_id": str(local_mode.LOCAL_ORG_ID),
                }
            ], saves
            assert saves[0]["description"].startswith("Saved project 'SQL' (")
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)
        solve_queue.reset_for_tests()


@pytest.mark.parametrize(
    "key, org",
    [
        ("org-a:1234", "org-a"),
        (None, None),
        ("", None),
        ("Baseline", None),
        (":1234", None),
        ("org-a:", None),
    ],
)
def test_only_an_org_and_uuid_key_names_an_owner(decoy_foreground, key, org):
    from services.solve_queue import _owning_org

    change_log_service.clear()
    with _owning_org(key):
        change_log_service.log("save", "Project", "P", "x")
    assert [e["org_id"] for e in change_log_service.get_all()] == ([org] if org else [])


def test_a_solving_context_that_holds_the_network_reads_no_foreground(
    decoy_foreground, monkeypatch
):
    """On the dispatcher the active context is the foreground: the revision
    must not even read it when the solving context owns the network (#1282's
    decoy check records every active-context read)."""
    from services.solver_service import _revision_or_none

    solving = ProjectContext(network=pypsa.Network())
    solving.network_revision = 3

    def no_foreground():
        raise AssertionError("read the foreground")

    with PyPSAService.solving_context(solving):
        monkeypatch.setattr(PyPSAService, "get_active_context", no_foreground)
        assert _revision_or_none(solving.network) == 3


def test_a_bound_solving_context_still_lets_the_active_network_read_its_own_revision(
    decoy_foreground,
):
    """A private solving context (an adequacy copy) is bound while the active
    project's own network is solved: the record takes the active revision."""
    from services.solver_service import _revision_or_none

    with PyPSAService.solving_context(ProjectContext(network=pypsa.Network())):
        assert _revision_or_none(decoy_foreground.network) == DECOY_REVISION
