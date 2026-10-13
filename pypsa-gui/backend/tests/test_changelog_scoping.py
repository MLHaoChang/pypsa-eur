"""
The changelog is per-organization, including for a caller with no membership.

`routers/changelog._caller_org_id` returns `None` when `get_user_membership`
finds nothing, and `change_log_service` reads `org_id=None` as "no filter" on
read and "clear EVERYTHING" on delete. So a membership-less caller saw every
tenant's edit history — component names, project names, the timing of their
work — and one `DELETE /api/changelog/` destroyed every tenant's audit trail.

Both docstrings asserted the opposite. `clear`'s said an unscoped clear "is
reachable only from test setup and the in-process reset helpers. The HTTP route
always passes the caller's org", and the route's said "The caller's own
organization's audit trail". Neither was true for this caller.

Membership-less users are not hypothetical: `tools/bootstrap_super_admin.py`
creates the shipped FIRST user of every deployment with `is_super_admin=True` and
no `OrgMembership` at all (verified — the file never mentions it).

The distinction this file pins: a super-admin reading across tenants is a
defensible product decision; DESTROYING every tenant's audit trail through a
route documented as org-scoped is not, and "has no membership" was never the
right predicate for either.

Found by an independent QA review, 2026-09-12. Server only — the desktop local
user always has a membership.
"""
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from services import change_log_service
from tests.conftest import attach_session


def _user(auth_db, *, org_id, super_admin=False):
    """A user with a membership in `org_id`, or none when `org_id` is None."""
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = auth_db
    with session_local() as db:
        u = User(
            id=_uuid.uuid4(),
            email=f"u-{_uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active",
            is_super_admin=super_admin,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(u)
        db.flush()
        if org_id is not None:
            db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id,
                                 org_id=org_id, role="admin"))
        db.commit()
        uid = u.id
    c = TestClient(main.app)
    c.__enter__()
    return attach_session(c, session_local, uid), c


@pytest.fixture
def two_orgs_with_history(_auth_db, seeded_identity, second_identity):
    """Audit entries belonging to two different organizations."""
    change_log_service.clear()
    change_log_service.log(
        action="update", component_type="Bus", name="org1-secret",
        description="org1-secret", org_id=str(seeded_identity["org_id"]),
    )
    change_log_service.log(
        action="update", component_type="Bus", name="org2-secret",
        description="org2-secret", org_id=str(second_identity["org_id"]),
    )
    yield
    change_log_service.clear()


@pytest.fixture
def orgless_client(_auth_db):
    client, raw = _user(_auth_db, org_id=None, super_admin=False)
    yield client
    raw.__exit__(None, None, None)


def test_a_membership_less_non_admin_sees_no_other_tenants_history(
    orgless_client, two_orgs_with_history,
):
    r = orgless_client.get("/api/changelog/")
    assert r.status_code == 200, r.text[:200]
    blob = r.text
    assert "org1-secret" not in blob and "org2-secret" not in blob, (
        f"a caller with no membership read other tenants' audit trails: {blob[:300]}"
    )


def test_a_membership_less_caller_cannot_destroy_every_tenants_history(
    orgless_client, two_orgs_with_history, seeded_identity,
):
    """
    The destructive half, and the reason this is more than an information leak.
    `clear(None)` empties the process-global deque for every organization.
    """
    orgless_client.delete("/api/changelog/")
    remaining = change_log_service.get_all(str(seeded_identity["org_id"]))
    details = [e.get("name") for e in remaining]
    assert "org1-secret" in details, (
        f"a membership-less DELETE destroyed another org's audit trail; org1 now "
        f"sees {details}"
    )


def test_a_normal_member_still_sees_and_clears_only_their_own_org(
    client, two_orgs_with_history, seeded_identity, second_identity,
):
    """The scoping that already worked must keep working."""
    r = client.get("/api/changelog/")
    assert r.status_code == 200, r.text[:200]
    assert "org1-secret" in r.text
    assert "org2-secret" not in r.text, "cross-tenant read by a normal member"

    client.delete("/api/changelog/")
    assert not [e for e in change_log_service.get_all(str(seeded_identity["org_id"]))
                if e.get("name") == "org1-secret"], "own-org clear did not work"
    assert [e for e in change_log_service.get_all(str(second_identity["org_id"]))
            if e.get("name") == "org2-secret"], (
        "clearing one org's history removed another org's"
    )


@pytest.fixture
def orgless_super_admin(_auth_db):
    """The shape `tools/bootstrap_super_admin.py` actually creates."""
    client, raw = _user(_auth_db, org_id=None, super_admin=True)
    yield client
    raw.__exit__(None, None, None)


def test_a_super_admin_without_a_membership_may_read_across_tenants(
    orgless_super_admin, two_orgs_with_history,
):
    """
    The deliberate half of the distinction. Cross-tenant VISIBILITY for a
    super-admin is a defensible product decision; the fix must not smuggle in a
    behaviour change by denying it, or the bootstrap user loses the audit view
    that is arguably the point of the role.
    """
    r = orgless_super_admin.get("/api/changelog/")
    assert r.status_code == 200, r.text[:200]
    assert "org1-secret" in r.text and "org2-secret" in r.text, (
        f"a super-admin lost the cross-tenant audit view: {r.text[:300]}"
    )


def test_even_a_super_admin_cannot_destroy_every_tenants_history_here(
    orgless_super_admin, two_orgs_with_history, seeded_identity, second_identity,
):
    """
    The other half, and the one that matters: read and destroy are NOT the same
    privilege. This route is documented as org-scoped, so it must never be the
    thing that wipes every tenant — whatever the caller's role. A deliberate
    global purge belongs behind an explicit admin operation that says so.
    """
    orgless_super_admin.delete("/api/changelog/")
    org1 = [e.get("name") for e in change_log_service.get_all(
        str(seeded_identity["org_id"]))]
    org2 = [e.get("name") for e in change_log_service.get_all(
        str(second_identity["org_id"]))]
    assert "org1-secret" in org1, f"org1's audit trail was destroyed: {org1}"
    assert "org2-secret" in org2, f"org2's audit trail was destroyed: {org2}"
