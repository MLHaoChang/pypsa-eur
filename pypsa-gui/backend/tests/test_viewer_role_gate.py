"""The read-only project viewer role (project-viewer-role 01, VIEWER-ROLE-1).

A viewer of a project is refused every write with one kind, ``project_view_only``,
and the project is left as it was: over HTTP (`services/project_write_gate.py`),
in the solve queue (enqueue, requeue, abort, the worker) and in chat
(`harness/restrictions`). Every write route in `services/_project_write_rules.py`
is exercised, not a sample (GK B plan GO, conditions R1-R8).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from db.models import OrgMembership, Project, ProjectMembership, User
from harness import catalogue, restrictions
from services import project_acl, project_write_gate
from services.project_write_gate import KIND, RULES
from tests import test_workflow_tools as fixtures
from tests.conftest import attach_session
from tests.test_lc06_d22_undo_checkpoints import fingerprint

baseline = fixtures.baseline
helpers_state = fixtures.helpers_state

_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")
_DUMMY = "00000000-0000-4000-8000-000000000000"


# ── seeding ──────────────────────────────────────────────────────────────────


def _member(session_local, org_id, *, org_role="member") -> uuid.UUID:
    uid = uuid.uuid4()
    with session_local() as db:
        db.add(
            User(
                id=uid,
                email=f"viewer-{uid.hex}@example.com",
                password_hash="unused",
                status="active",
                is_super_admin=False,
                created_at=datetime.now(UTC),
            )
        )
        db.flush()
        db.add(OrgMembership(user_id=uid, org_id=org_id, role=org_role))
        db.commit()
    return uid


def _grant(session_local, project_id, user_id, role, *, by) -> None:
    with session_local() as db:
        db.add(
            ProjectMembership(
                project_id=uuid.UUID(str(project_id)),
                user_id=user_id,
                assigned_by=uuid.UUID(str(by)),
                assigned_at=datetime.now(UTC),
                role=role,
            )
        )
        db.commit()


def _set_role(session_local, project_id, user_id, role) -> None:
    with session_local() as db:
        row = (
            db.query(ProjectMembership)
            .filter_by(project_id=uuid.UUID(str(project_id)), user_id=user_id)
            .one()
        )
        row.role = role
        db.commit()


def _role(session_local, project_id, user_id):
    with session_local() as db:
        return project_acl.project_role(
            db, db.get(User, user_id), db.get(Project, uuid.UUID(str(project_id)))
        )


@pytest.fixture
def world(baseline, client, _auth_db, seeded_identity):
    """The seeded admin's saved project, with a viewer and an editor member.

    The admin's save took the edit lock; it is released so a member's write
    meets the viewer gate rather than the foreign-lock 409 that runs first.
    """
    ctx, project_id, path = baseline
    released = client.delete(f"/api/projects/{project_id}/lock")
    assert released.status_code in (200, 204), released.text
    _, session_local = _auth_db
    org = seeded_identity["org_id"]
    viewer = _member(session_local, org)
    editor = _member(session_local, org)
    _grant(session_local, project_id, viewer, "viewer", by=seeded_identity["user_id"])
    _grant(session_local, project_id, editor, "editor", by=seeded_identity["user_id"])
    return {
        "ctx": ctx,
        "project": project_id,
        "path": Path(path),
        "viewer": viewer,
        "editor": editor,
        "session_local": session_local,
    }


@pytest.fixture
def as_user(_auth_db):
    _, session_local = _auth_db
    opened = []

    def _open(user_id) -> TestClient:
        client = TestClient(main.app)
        client.__enter__()
        opened.append(client)
        return attach_session(client, session_local, user_id)

    yield _open
    for client in opened:
        client.__exit__(None, None, None)


def _disk(root: Path) -> list:
    return sorted(
        (str(p.relative_to(root)), hashlib.sha1(p.read_bytes()).hexdigest())
        for p in root.rglob("*")
        if p.is_file()
    )


def _state(world) -> dict:
    return {"disk": _disk(world["path"]), "network": fingerprint(world["ctx"].network)}


def _url(path: str, target: str | None, value: str) -> str:
    param = target.split(":", 1)[1] if target and target.startswith("path:") else None
    return _PARAM.sub(lambda m: value if m.group(1) == param else _DUMMY, path)


def _kind(response) -> str | None:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return None
    return detail.get("error_kind") if isinstance(detail, dict) else None


# ── R6: the role itself ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stored, expected",
    [
        ("editor", "edit"),
        ("viewer", "view"),
        (None, "view"),
        ("Editor", "view"),
        ("admin", "view"),
        ("", "view"),
        ("owner", "view"),
    ],
)
def test_a_malformed_stored_role_reads_view_only(world, stored, expected):
    _set_role(world["session_local"], world["project"], world["viewer"], stored)
    assert _role(world["session_local"], world["project"], world["viewer"]) == expected


def test_org_admins_and_lineage_creators_edit_whatever_the_row_says(
    world, seeded_identity
):
    session_local = world["session_local"]
    # The seeded identity is an org admin and the creator: a viewer row cannot demote it.
    _grant(
        session_local,
        world["project"],
        seeded_identity["user_id"],
        "viewer",
        by=seeded_identity["user_id"],
    )
    assert _role(session_local, world["project"], seeded_identity["user_id"]) == "edit"
    # A viewer who created a scenario child edits that child, not its root.
    with session_local() as db:
        root = db.get(Project, uuid.UUID(world["project"]))
        child = Project(
            id=uuid.uuid4(),
            org_id=root.org_id,
            name="Viewer Child",
            created_by=world["viewer"],
            storage_path="viewer-child",
            parent_project_id=root.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(child)
        db.commit()
        child_id = child.id
    assert _role(session_local, child_id, world["viewer"]) == "edit"
    assert _role(session_local, world["project"], world["viewer"]) == "view"


def test_no_membership_means_no_access_not_view(world):
    stranger = _member(world["session_local"], _org_of(world))
    assert _role(world["session_local"], world["project"], stranger) is None


def _org_of(world):
    with world["session_local"]() as db:
        return db.get(Project, uuid.UUID(world["project"])).org_id


# ── R7: the table is total and its allowances are pinned ─────────────────────


def _mutating_routes() -> set[tuple[str, str]]:
    from smoke.assistant_route_parity import app_routes

    return {
        key
        for key in app_routes(main.app)
        if key[0] in project_write_gate.WRITE_METHODS
    }


def test_viewer_route_table_is_total():
    routes = _mutating_routes()
    assert sorted(routes - RULES.keys()) == [], "write routes with no viewer-role row"
    assert sorted(RULES.keys() - routes) == [], "viewer-role rows whose route is gone"


ALLOWED = {
    ("POST", "/api/chat/stream"),
    ("POST", "/api/chat/ui-ack"),
    ("POST", "/api/chat/operations/{operation_id}/reconcile"),
    ("POST", "/api/chat/{session_id}/abort"),
    ("POST", "/api/chat/{session_id}/confirm"),
    ("POST", "/api/chat/{session_id}/continuations/{job_id}/dismiss"),
    ("POST", "/api/chat/{session_id}/goal"),
    ("POST", "/api/chat/{session_id}/progress/stop"),
    ("POST", "/api/chat/{session_id}/rewind"),
    ("POST", "/api/durable-conversations"),
    ("POST", "/api/durable-conversations/{thread_id}/checkpoint"),
    ("POST", "/api/durable-conversations/{thread_id}/profile"),
    ("POST", "/api/durable-conversations/{thread_id}/rebind"),
    ("POST", "/api/durable-conversations/{thread_id}/reconcile"),
    ("POST", "/api/durable-conversations/{thread_id}/resume"),
    ("POST", "/api/durable-conversations/{thread_id}/speech/barge-in"),
    ("POST", "/api/durable-conversations/{thread_id}/speech/end"),
    ("POST", "/api/durable-conversations/{thread_id}/speech/output/claim"),
    ("POST", "/api/durable-conversations/{thread_id}/speech/segment"),
    ("POST", "/api/durable-conversations/{thread_id}/speech/start"),
    ("POST", "/api/durable-conversations/{thread_id}/speech/usage"),
    ("POST", "/api/document_values/commercial/preview"),
    ("POST", "/api/document_values/preview"),
    ("POST", "/api/equipment_table/preview"),
    ("POST", "/api/library_reference/preview"),
    ("POST", "/api/profiles/estimate"),
    ("POST", "/api/results/billing/preview"),
    ("POST", "/api/simulation/preflight"),
    ("POST", "/api/projects/{name}/studies/preview"),
    ("POST", "/api/projects/{project_id}/activate"),
    ("DELETE", "/api/projects/{project_id}/lock"),
    ("POST", "/api/projects/{name}/sites/{site_id}/scene/proposals"),
    ("POST", "/api/projects/{name}/studies/{study_id}/grid-extension-break-even"),
    # Owner decision VIEWER-ROLE-1 (2026-11-17): Q3 comments, Q4 report export.
    ("POST", "/api/projects/{name}/reports/{report_id}/comments"),
    ("POST", "/api/projects/{name}/reports/{report_id}/export"),
}


def test_the_viewer_allowances_are_exactly_these():
    allowed = {key for key, rule in RULES.items() if rule.kind == "allow"}
    assert allowed == ALLOWED
    assert all(rule.detail.strip() for rule in RULES.values() if rule.kind != "write")


# ── every write route, end to end ────────────────────────────────────────────

_GATED = sorted(
    k for k, r in RULES.items() if r.kind == "write" and r.detail != "handler"
)


def test_a_viewer_is_refused_every_gated_write_and_nothing_changes(world, as_user):
    viewer = as_user(world["viewer"])
    opened = viewer.post(f"/api/projects/{world['project']}/activate")
    assert opened.status_code == 200, opened.text
    before = _state(world)
    wrong = []
    for method, path in _GATED:
        url = _url(path, RULES[(method, path)].detail, world["project"])
        response = viewer.request(method, url, json={})
        if response.status_code != 403 or _kind(response) != KIND:
            wrong.append((method, path, response.status_code, response.text[:160]))
    assert wrong == []
    assert _state(world) == before
    assert len(_GATED) > 200  # the matrix ran over the whole table, not a sample


# ── R1: the target is the declared project, in both directions ───────────────


@pytest.fixture
def own_project(world, as_user, install_network):
    """A project the viewer created (and so edits), saved and open in their session."""
    import pandas as pd
    import pypsa

    viewer = as_user(world["viewer"])
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.add("Bus", "B1")
    install_network(n)
    saved = viewer.post("/api/projects/Viewer Own")
    assert saved.status_code == 200, saved.text
    with world["session_local"]() as db:
        own = db.query(Project).filter_by(name="Viewer Own").one()
    return viewer, str(own.id)


def test_viewer_on_x_editor_on_active_y(world, own_project):
    viewer, own = own_project
    assert viewer.post(f"/api/projects/{own}/activate").status_code == 200
    # A path write to X (viewed) is refused although the open project Y is editable.
    refused = viewer.patch(f"/api/projects/{world['project']}/settings", json={})
    assert refused.status_code == 403 and _kind(refused) == KIND
    # A path write and an active write to Y pass the gate.
    passed = viewer.patch(f"/api/projects/{own}/settings", json={})
    assert _kind(passed) != KIND, passed.text
    undo = viewer.post("/api/network/undo", json={})
    assert _kind(undo) != KIND, undo.text


def test_editor_on_x_viewer_on_active_y(world, own_project):
    viewer, own = own_project
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    before = _state(world)
    undo = viewer.post("/api/network/undo", json={})
    assert undo.status_code == 403 and _kind(undo) == KIND
    passed = viewer.patch(f"/api/projects/{own}/settings", json={})
    assert _kind(passed) != KIND, passed.text
    assert _state(world) == before


def test_a_project_named_like_another_projects_id_is_checked_both_ways(
    world, own_project
):
    """`{name}` may resolve by id or by exact name; a viewer is refused if either is viewed.

    The app's own create and rename refuse such a name, so the row is seeded
    directly: the gate must not depend on that.
    """
    viewer, own = own_project
    with world["session_local"]() as db:
        row = db.get(Project, uuid.UUID(own))
        row.name = world["project"]  # the viewer's own project, named as X's id
        db.commit()
    refused = viewer.patch(f"/api/projects/{world['project']}/settings", json={})
    assert refused.status_code == 403 and _kind(refused) == KIND


def test_an_editor_member_passes_the_gate(world, as_user):
    editor = as_user(world["editor"])
    assert editor.post(f"/api/projects/{world['project']}/activate").status_code == 200
    passed = editor.patch(f"/api/projects/{world['project']}/settings", json={})
    assert _kind(passed) != KIND, passed.text
    undo = editor.post("/api/network/undo", json={})
    assert _kind(undo) != KIND, undo.text


# ── R2: a stranger never learns the project exists ───────────────────────────


def test_a_non_member_and_another_org_see_the_plain_404(
    world, as_user, other_org_client
):
    member = as_user(_member(world["session_local"], _org_of(world)))
    for client in (member, other_org_client):
        real = client.post(
            f"/api/projects/{world['project']}/rename", json={"new_name": "x"}
        )
        fake = client.post(
            f"/api/projects/{uuid.uuid4()}/rename", json={"new_name": "x"}
        )
        assert (
            (real.status_code, real.json())
            == (fake.status_code, fake.json())
            == (404, {"detail": "Project not found"})
        )


# ── handler-checked rows: the project is in the body or the job ──────────────


def test_the_handler_rows_are_exactly_the_queue_routes():
    handler = sorted(k for k, r in RULES.items() if r.detail == "handler")
    assert sorted(handler) == sorted(
        [
            ("POST", "/api/simulation/queue"),
            ("POST", "/api/simulation/queue/cancel_queued"),
            ("POST", "/api/simulation/queue/{job_id}/abort"),
            ("POST", "/api/simulation/queue/{job_id}/requeue"),
            (
                "POST",
                "/api/projects/{name}/reports/{report_id}/comments/{comment_id}/withdraw",
            ),
        ]
    )


def test_a_viewer_cannot_queue_a_solve_of_the_project(world, as_user):
    viewer = as_user(world["viewer"])
    from services.solve_queue import solve_queue

    jobs = len(solve_queue.list_jobs())
    response = viewer.post(
        "/api/simulation/queue", json={"project_id": world["project"]}
    )
    assert response.status_code == 403 and _kind(response) == KIND
    assert len(solve_queue.list_jobs()) == jobs


def _job(world, user_id, status="queued"):
    from services.solve_queue import SolveJob

    with world["session_local"]() as db:
        project = db.get(Project, uuid.UUID(world["project"]))
        key = f"{project.org_id}:{project.id}"
    return SolveJob(
        id=uuid.uuid4(),
        project_id=project.name,
        project_key=key,
        storage_dir=str(world["path"]),
        status=status,
        enqueued_by_user_id=str(user_id),
    )


def test_a_viewer_cannot_abort_cancel_or_requeue_an_editors_job(
    world, as_user, monkeypatch
):
    from routers import solve_queue as queue_router
    from services import solve_queue as queue_service

    job = _job(world, world["editor"])
    public = {"id": str(job.id), "status": "queued", "project_key": job.project_key}
    monkeypatch.setattr(queue_service.solve_queue, "get_job", lambda jid: public)
    monkeypatch.setattr(queue_service.solve_queue, "list_jobs", lambda: [public])
    aborted, cancelled = [], []
    monkeypatch.setattr(
        queue_service.solve_queue, "abort", lambda jid: aborted.append(jid)
    )
    monkeypatch.setattr(
        queue_service.solve_queue,
        "cancel_if_queued",
        lambda jid: cancelled.append(jid) or True,
    )
    viewer = as_user(world["viewer"])
    response = viewer.post(f"/api/simulation/queue/{job.id}/abort")
    assert response.status_code == 403 and _kind(response) == KIND
    swept = viewer.post("/api/simulation/queue/cancel_queued")
    assert swept.status_code == 200 and swept.json() == {"cancelled": 0}
    assert aborted == [] and cancelled == []
    # The editor is not stopped by the viewer branch.
    editor = as_user(world["editor"])
    assert editor.post("/api/simulation/queue/cancel_queued").json() == {"cancelled": 1}
    assert queue_router  # imported for its module-level wiring


def test_requeue_refuses_a_viewer_and_hides_the_project_from_a_non_member(
    world, as_user, monkeypatch
):
    from services import solve_queue as queue_service

    job = _job(world, world["editor"], status="failed")
    public = {
        "id": str(job.id),
        "status": "failed",
        "project_key": job.project_key,
        "project_id": job.project_id,
        "kind": "solve",
        "storage_dir": job.storage_dir,
        "solver_config": None,
    }
    monkeypatch.setattr(queue_service.solve_queue, "get_job", lambda jid: public)
    enqueued = []
    monkeypatch.setattr(
        queue_service.solve_queue,
        "enqueue_unique",
        lambda *a, **k: enqueued.append(k),
        raising=False,
    )
    viewer = as_user(world["viewer"])
    response = viewer.post(f"/api/simulation/queue/{job.id}/requeue")
    assert response.status_code == 403 and _kind(response) == KIND, response.text
    stranger = as_user(_member(world["session_local"], _org_of(world)))
    hidden = stranger.post(f"/api/simulation/queue/{job.id}/requeue")
    assert hidden.status_code == 404, hidden.text
    assert enqueued == []


# ── R3: the role is read again when deferred work runs ───────────────────────


def test_a_job_queued_by_an_editor_demoted_before_it_runs_is_not_run(
    world, monkeypatch
):
    from services import solve_queue as queue_service

    job = _job(world, world["editor"])
    assert queue_service._enqueuer_views_project(job) is False
    _set_role(world["session_local"], world["project"], world["editor"], "viewer")
    assert queue_service._enqueuer_views_project(job) is True
    finished, ran = [], []
    q = queue_service.solve_queue
    monkeypatch.setattr(
        q, "_finish", lambda j, status, error=None: finished.append((status, error))
    )
    monkeypatch.setattr(q, "_run_solve_job", lambda j: ran.append(j))
    before = _state(world)
    q._run_job(job)
    assert ran == [] and finished and finished[0][0] == "failed"
    assert finished[0][1].startswith(KIND)
    assert _state(world) == before


# ── chat: every non-read tier, whatever rule is saved ─────────────────────────

_WRITE_TIERS = [t for t in catalogue.SAFETY_TIERS if t != "read"]


@pytest.mark.parametrize("mode", ["standard", "confirm_changes", "read_only"])
@pytest.mark.parametrize("tier", _WRITE_TIERS)
def test_require_allowed_refuses_a_viewer_every_write_tier(tier, mode):
    snapshot = {"mode": mode, "revision": "r", "project_role": "view"}
    with pytest.raises(HTTPException) as refused:
        restrictions.require_allowed(snapshot, tier)
    assert (
        refused.value.status_code == 403 and refused.value.detail["error_kind"] == KIND
    )
    restrictions.require_allowed(snapshot, "read")
    restrictions.require_allowed(
        {**snapshot, "project_role": "edit", "mode": "standard"}, tier
    )


def test_a_role_lost_after_approval_refuses_at_execution(monkeypatch):
    edit = {"mode": "standard", "revision": "r", "project_role": "edit"}
    monkeypatch.setattr(
        restrictions, "capture", lambda s: {**edit, "project_role": "view"}
    )
    with pytest.raises(HTTPException) as refused:
        restrictions.require_current(edit, object())
    assert refused.value.detail["error_kind"] == KIND


def test_a_viewers_chat_write_tool_is_refused_and_writes_nothing(
    world, as_user, monkeypatch
):
    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    viewer = as_user(world["viewer"])
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    binding = PyPSAService.bind_request_context(world["ctx"])
    chat_tools.set_acting_user(world["viewer"])
    before = _state(world)
    try:
        frames = list(
            dispatch_tool_call(
                ChatSession(),
                {
                    "id": "viewer-write",
                    "name": "bulk_update_components",
                    "input": {
                        "component_class": "Generator",
                        "names": ["G1"],
                        "updates": {"marginal_cost": 99},
                    },
                },
                [],
            )
        )
        assert any(d.get("error_kind") == KIND for _, d in frames), frames
        assert not any(
            e in {"tool_pending_confirmation", "tool_running"} for e, _ in frames
        )
        reads = []
        read_frames = list(
            dispatch_tool_call(
                ChatSession(),
                {
                    "id": "viewer-read",
                    "name": "get_component",
                    "input": {"component_class": "Generator", "name": "G1"},
                },
                reads,
            )
        )
        assert not any(e == "tool_error" for e, _ in read_frames), read_frames
    finally:
        PyPSAService.reset_request_context(binding)
    assert _state(world) == before


# ── R4: where a tool's tier comes from, and no read tool reaches a write ─────


def test_undo_restore_and_snapshot_writes_are_not_read_tier():
    """The tier is the `Safety: <tier>` marker in the tool's catalogue description
    (`harness.catalogue.safety_tier_for`); `require_allowed` refuses every tier but read."""
    for tool in (
        "undo_last",
        "restore_project_snapshot",
        "delete_project_snapshot",
        "create_project_snapshot",
        "bulk_update_components",
    ):
        assert catalogue.safety_tier_for(tool) != "read", tool


def test_no_read_tier_tool_is_bound_to_a_route_a_viewer_is_refused():
    """A read-tier tool runs for a viewer, so the route it calls must not be a write row."""
    bound = []
    for tool, entries in catalogue.TOOL_ROUTES.items():
        if catalogue.safety_tier_for(tool) != "read":
            continue
        for entry in entries:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                rule = RULES.get((entry[0], entry[1]))
                if rule is not None and rule.kind == "write":
                    bound.append((tool, entry[0], entry[1]))
    assert bound == []


# ── owner decision VIEWER-ROLE-1 Q3: a viewer comments, and withdraws only their own ──


def test_a_viewer_comments_on_a_report_and_withdraws_only_their_own(world, as_user):
    from services.reports import revisions, store
    from tests import test_report_revisions as reports

    store.create_report(world["path"], reports._doc())
    rid = reports.RID
    editors = revisions.add_comment(
        world["path"],
        rid,
        "editor note",
        reports._anchor(),
        user="someone-else@example.com",
    )
    viewer = as_user(world["viewer"])
    added = viewer.post(
        f"/api/projects/{world['project']}/reports/{rid}/comments",
        json={
            "text": "please shorten",
            "anchor": {"section_id": "grid", "block_index": 0},
        },
    )
    assert added.status_code == 200, added.text
    mine = added.json()["comment_id"]
    base = f"/api/projects/{world['project']}/reports/{rid}/comments"
    refused = viewer.post(f"{base}/{editors.comment_id}/withdraw")
    assert refused.status_code == 403 and _kind(refused) == KIND
    assert viewer.post(f"{base}/{mine}/withdraw").status_code == 200
    status = {
        c.comment_id: c.status for c in revisions.load_comments(world["path"], rid)
    }
    assert status[editors.comment_id] == "open" and status[mine] == "withdrawn"
    addressed = viewer.post(f"{base}/{editors.comment_id}/address", json={})
    assert addressed.status_code == 403 and _kind(addressed) == KIND


# ── guard branches, one test each (mutation survivors, GK B R8) ──────────────


@pytest.fixture
def db(world):
    with world["session_local"]() as session:
        yield session


def _project(db, world) -> Project:
    return db.get(Project, uuid.UUID(world["project"]))


def test_require_writable_passes_without_a_user_or_project(db, world):
    viewer = db.get(User, world["viewer"])
    project_write_gate.require_writable(db, None, _project(db, world))
    project_write_gate.require_writable(db, viewer, None)
    with pytest.raises(HTTPException):
        project_write_gate.require_writable(db, viewer, _project(db, world))


def test_is_viewer(db, world):
    viewer, editor = db.get(User, world["viewer"]), db.get(User, world["editor"])
    assert project_write_gate.is_viewer(db, viewer, world["project"]) is True
    assert project_write_gate.is_viewer(db, editor, world["project"]) is False
    assert project_write_gate.is_viewer(db, None, world["project"]) is False
    assert project_write_gate.is_viewer(db, viewer, None) is False
    assert project_write_gate.is_viewer(db, viewer, "") is False
    assert project_write_gate.is_viewer(db, viewer, str(uuid.uuid4())) is False


def test_the_table_parser_refuses_a_bad_table():
    parse = project_write_gate._parse
    rules = parse("""
        # a comment, then a blank line

        POST /a  write active
        GET  /b  allow because
    """)
    assert rules == {
        ("POST", "/a"): project_write_gate.Rule("write", "active"),
        ("GET", "/b"): project_write_gate.Rule("allow", "because"),
    }
    for bad in (
        "POST /a write active\nPOST /a none twice",
        "POST /a maybe reason",
        "POST /a write somewhere",
        "POST /a write path:",
    ):
        with pytest.raises(ValueError):
            parse(bad)


def test_route_path_names_the_full_path_of_a_route_mounted_twice():
    from fastapi import APIRouter, FastAPI
    from starlette.requests import Request

    router = APIRouter()

    @router.post("/{name}/x")
    def handler(name: str):  # pragma: no cover - never called
        return name

    app = FastAPI()
    app.include_router(router, prefix="/one")
    app.include_router(router, prefix="/two")
    route = router.routes[0]

    def request(path, with_route=True):
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "app": app,
            "headers": [],
        }
        if with_route:
            scope["route"] = route
        return Request(scope)

    assert project_write_gate.route_path(request("/one/p/x")) == "/one/{name}/x"
    assert project_write_gate.route_path(request("/two/p/x")) == "/two/{name}/x"
    assert project_write_gate.route_path(request("/two/p/x", with_route=False)) is None


def test_full_paths_skips_a_route_without_a_path():
    class _Route:
        path = None

    class _App:
        routes = [_Route()]

    assert project_write_gate._full_paths(_App()) == {}


def test_rule_for_an_unknown_route_is_a_write_to_the_active_project():
    assert project_write_gate.rule_for("POST", None) == project_write_gate.Rule(
        "write", "active"
    )
    assert project_write_gate.rule_for("POST", "/nowhere") == project_write_gate.Rule(
        "write", "active"
    )


def test_path_projects_lists_each_project_once_and_nothing_unknown(db, world):
    viewer = db.get(User, world["viewer"])
    assert project_write_gate._path_projects(db, viewer, world["project"]) == [
        _project(db, world)
    ]
    assert project_write_gate._path_projects(db, viewer, "No Such Project") == []


def test_the_gate_lets_an_anonymous_write_through_to_the_handler(
    db, world, monkeypatch
):
    """No user: the handler's own 401 decides, the gate neither refuses nor crashes."""
    from starlette.requests import Request

    import deps

    monkeypatch.setattr(deps, "optional_user", lambda request, db: None)
    scope = {
        "type": "http",
        "method": "PATCH",
        "headers": [],
        "app": main.app,
        "path": f"/api/projects/{world['project']}/settings",
        "path_params": {"name": world["project"]},
    }
    request = Request(scope)
    monkeypatch.setattr(
        project_write_gate, "route_path", lambda r: "/api/projects/{name}/settings"
    )
    project_write_gate.require_project_write(request, db)


def test_a_viewer_reads_the_project(world, as_user):
    viewer = as_user(world["viewer"])
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    for url in (
        f"/api/projects/{world['project']}",
        "/api/network/generators",
        f"/api/projects/{world['project']}/snapshots",
    ):
        response = viewer.get(url)
        assert response.status_code == 200, (url, response.text[:160])


@pytest.mark.parametrize("org_role, expected", [("admin", "edit"), ("member", None)])
def test_an_org_admin_edits_without_a_membership_row(world, org_role, expected):
    someone = _member(world["session_local"], _org_of(world), org_role=org_role)
    assert _role(world["session_local"], world["project"], someone) == expected


def test_a_user_outside_every_org_has_no_role(world):
    uid = uuid.uuid4()
    with world["session_local"]() as session:
        session.add(
            User(
                id=uid,
                email=f"lone-{uid.hex}@example.com",
                password_hash="unused",
                status="active",
                is_super_admin=False,
                created_at=datetime.now(UTC),
            )
        )
        session.commit()
    assert _role(world["session_local"], world["project"], uid) is None


def test_no_project_snapshot_admits_every_tier():
    """An unsaved draft has no project rule and no role (capture returns None)."""
    for tier in catalogue.SAFETY_TIERS:
        restrictions.require_allowed(None, tier)


def test_a_rule_lost_without_a_snapshot_is_a_plain_revocation(monkeypatch):
    monkeypatch.setattr(
        restrictions, "capture", lambda s: {"mode": "standard", "project_role": "view"}
    )
    with pytest.raises(HTTPException) as refused:
        restrictions.require_current(None, object())
    assert refused.value.detail["error_kind"] == "confirmation_revoked"


def test_job_view_only_for_a_deleted_project_is_false(db, world):
    from routers import solve_queue as queue_router

    viewer = db.get(User, world["viewer"])
    org = _project(db, world).org_id
    gone = {"project_key": f"{org}:{uuid.uuid4()}"}
    assert queue_router._job_view_only(db, viewer, gone) is False
    real = {"project_key": f"{org}:{world['project']}"}
    assert queue_router._job_view_only(db, viewer, real) is True


def test_enqueuer_views_project_judges_only_a_complete_job(world):
    from services import solve_queue as queue_service

    job = _job(world, world["viewer"])
    assert queue_service._enqueuer_views_project(job) is True
    org_key = job.project_key
    for project_key, requester in [
        (None, world["viewer"]),
        ("", world["viewer"]),
        (org_key, None),
        ("no-colon", world["viewer"]),
        ("org:not-a-uuid", world["viewer"]),
        (org_key, "not-a-uuid"),
        (org_key, uuid.uuid4()),
        (f"{org_key.split(':')[0]}:{uuid.uuid4()}", world["viewer"]),
    ]:
        job.project_key, job.enqueued_by_user_id = project_key, requester
        assert queue_service._enqueuer_views_project(job) is False, (
            project_key,
            requester,
        )
