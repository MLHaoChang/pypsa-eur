"""
Starting an adequacy study must respect another user's edit lock.

`POST /api/results/{frontier,mc,fmea_sweep,margin_loop,coupling_loop}` each take
`PyPSAService.get_network()` — the SHARED resident network — and re-solve it in a
worker thread. The resident `ProjectContext` is shared per `(org, project)`, so a
non-holder who activates the same project points at the SAME in-memory network:
without a lock check, their study re-solves the holder's plan and the holder's
next autosave persists it.

`routers/results.py` had no lock check of any kind, and the foreign-lock
middleware did not reach it because that middleware is a PREFIX list
(`/api/network/`, `/api/io/`, `/api/simulation/`) and `/api/results/` was not on
it. `_refuse_if_mesh_busy` is NOT this control: it serialises studies against
each other under the PyPSA mutation lock — thread safety, not authorization.

Same class as `docs/superpowers/findings/2026-08-27-requeue-is-a-cross-user-overwrite.md`,
which was fixed by porting `enqueue_solve`'s holder check. See finding 1 of
`docs/superpowers/assessments/2026-09-12-per-route-authorization-audit.md`.

The five `/abort` siblings are deliberately NOT gated, for the reason the queue
allowlist already records: stopping work writes nothing, and gating an abort
would let a foreign lock trap a running study.

WHY THE CONTEXT RESOLVER IS MONKEYPATCHED. The middleware needs a BOUND context
(one with a `project_uuid`); `install_network` yields a scratch context, whose
uuid is None, which the gate correctly skips. Patching
`get_active_context` — the resolver, NOT the gate — is what lets a real request
reach the real middleware and produce a real 409. Nothing about the gate itself
is stubbed.
"""
import types
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from tests.conftest import attach_session


_STUDY_STARTS = ("frontier", "mc", "fmea_sweep", "margin_loop", "coupling_loop")


@pytest.fixture
def foreign_locked_project(_auth_db, seeded_identity, monkeypatch):
    """
    A project in the seeded org whose edit lock is held by SOMEONE ELSE, with
    the active context bound to it. Returns the project id.
    """
    from db.models import OrgMembership, Project, User
    from services import project_locks
    from services.auth_service import hash_password
    from services.pypsa_service import PyPSAService

    _engine, session_local = _auth_db
    now = datetime.now(tz=timezone.utc)
    project_id = _uuid.uuid4()

    with session_local() as db:
        holder = User(
            id=_uuid.uuid4(),
            email=f"holder-{_uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active",
            is_super_admin=False,
            created_at=now,
        )
        db.add(holder)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=holder.id,
                             org_id=seeded_identity["org_id"], role="admin"))
        db.add(Project(
            id=project_id,
            org_id=seeded_identity["org_id"],
            name=f"Locked-{project_id.hex[:6]}",
            created_by=holder.id,
            storage_path=f"irrelevant/{project_id.hex}",
            created_at=now,
            updated_at=now,
        ))
        db.commit()
        # The lock belongs to the holder, NOT to the client's seeded user.
        assert project_locks.acquire_lock(db, project_id, holder.id) is not None

    monkeypatch.setattr(
        PyPSAService, "get_active_context",
        classmethod(lambda cls: types.SimpleNamespace(project_uuid=str(project_id))),
    )
    return project_id


@pytest.mark.parametrize("study", _STUDY_STARTS)
def test_a_non_holder_cannot_start_a_study(study, _auth_db, seeded_identity,
                                           foreign_locked_project):
    """The defect: each of these re-solved the holder's network unchallenged."""
    _engine, session_local = _auth_db
    with TestClient(main.app) as c:
        client = attach_session(c, session_local, seeded_identity["user_id"])
        r = client.post(f"/api/results/{study}")
    assert r.status_code == 409, (
        f"/api/results/{study} started under a foreign lock: "
        f"{r.status_code} {r.text[:200]}"
    )
    detail = r.json().get("detail")
    assert isinstance(detail, dict), f"unexpected body shape: {r.text[:200]}"
    assert detail.get("error_kind") == "project_locked", detail
    # Same object shape the other two emitters send, so the frontend's
    # `_lockFromErrorDetail` can name the holder from this one too.
    assert "lock" in detail, detail


@pytest.mark.parametrize("study", _STUDY_STARTS)
def test_an_abort_is_not_gated(study, _auth_db, seeded_identity,
                               foreign_locked_project):
    """
    Aborts stay reachable: stopping work writes nothing, and gating them would
    let a foreign lock trap a running study with no way to stop it. Any status
    EXCEPT the lock 409 is acceptable here — what matters is that the gate did
    not refuse it.
    """
    _engine, session_local = _auth_db
    with TestClient(main.app) as c:
        client = attach_session(c, session_local, seeded_identity["user_id"])
        r = client.post(f"/api/results/{study}/abort")
    if r.status_code == 409:
        detail = r.json().get("detail")
        kind = detail.get("error_kind") if isinstance(detail, dict) else None
        assert kind != "project_locked", (
            f"/api/results/{study}/abort was refused by the foreign-lock gate; "
            f"aborts must stay reachable"
        )


def test_reads_under_the_gated_prefix_are_untouched(_auth_db, seeded_identity,
                                                    foreign_locked_project):
    """
    Mutation target (M3). Widening the gate's prefix to `/api/results/` brought
    48 GET routes under it, and the gate is supposed to apply to WRITES only —
    a foreign lock means "someone else is editing", never "you may not look".
    Turning the `is_write` condition off passed every other test in this file,
    so without this the over-gating direction was untested on the surface the
    change actually widened.
    """
    _engine, session_local = _auth_db
    with TestClient(main.app) as c:
        client = attach_session(c, session_local, seeded_identity["user_id"])
        # A GET that exists on this router. Its 200/404/422 does not matter —
        # what matters is that the LOCK gate did not refuse it.
        r = client.get("/api/results/frontier")
    if r.status_code == 409:
        detail = r.json().get("detail")
        kind = detail.get("error_kind") if isinstance(detail, dict) else None
        assert kind != "project_locked", (
            "a GET under /api/results/ was refused by the foreign-lock gate; "
            "the gate must apply to writes only"
        )
