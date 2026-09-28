"""
The adequacy sidecars must respect another user's edit lock.

`PUT /api/projects/{name}/worksheet` and `PUT .../stress_scenarios` carried
`ProjectAccessDep` — the ACL check, answering "may this caller see this project"
— and no lock check at all. That is not the same question as "may this caller
write it right now while somebody else holds the edit lock".

They are mounted under `/api/projects`, which is deliberately absent from
`main._FOREIGN_LOCK_GATE_PREFIXES` because that family enforces the lock in its
handlers instead. These two handlers never got it. `routers/uploads.py:176-181`
carries a comment saying its own case "was missed by the sweep that added lock
enforcement elsewhere in this router family" — so this is the third instance of
one miss, which is why the controls below are asserted alongside the subjects
rather than assumed.

Why it matters: `save_worksheet` / `save_scenarios` REPLACE the sidecar wholesale
and bump `version`, and the holder's client uses `version` for last-write-wins,
so it treats the intruder's content as the newer authoritative copy and the
holder's FMEA rows and overlays are gone. Both files are in
`routers/projects._BUNDLE_FILES`, so the write also propagates into every later
bundle export and snapshot.

Found by an independent QA review, 2026-09-12. Server only: `_check_project_lock`
early-returns in local mode, so the desktop build is unaffected.
"""
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from tests.conftest import attach_session


@pytest.fixture
def same_org_other_user(_auth_db, seeded_identity):
    """A second user in the SAME org — the realistic intruder for a lock test."""
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        u = User(
            id=_uuid.uuid4(),
            email=f"colleague-{_uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(u)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id,
                             org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        uid = u.id
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, uid)


def _is_lock_refusal(r) -> bool:
    if r.status_code != 409:
        return False
    detail = r.json().get("detail")
    return isinstance(detail, dict) and detail.get("error_kind") == "project_locked"


def test_the_controls_really_are_lock_checked(client, api_project,
                                              same_org_other_user):
    """
    Guard on the guard. If layout/uploads ever stop being lock-checked, the
    subject assertions below would still pass while proving nothing — so the
    controls are asserted, not assumed.
    """
    name = api_project("wslock-controls")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200

    b = same_org_other_user
    layout = b.put(f"/api/projects/{name}/layout", json={"nodes": {}})
    upload = b.post(f"/api/projects/{name}/uploads",
                    files={"file": ("a.csv", b"x,y\n1,2\n", "text/csv")})
    assert _is_lock_refusal(layout), f"layout control: {layout.status_code} {layout.text[:160]}"
    assert _is_lock_refusal(upload), f"uploads control: {upload.status_code} {upload.text[:160]}"


def test_worksheet_put_is_refused_under_a_foreign_lock(client, api_project,
                                                       same_org_other_user):
    name = api_project("wslock-ws")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200

    r = same_org_other_user.put(
        f"/api/projects/{name}/worksheet",
        # A VALID body on purpose: an invalid one 422s on validation, and the
        # test would then pass without proving a write was ever preventable.
        json={"manual_rows": [], "overlays": {"COPT-1": {"notes": "intruder"}}},
    )
    assert _is_lock_refusal(r), (
        f"a non-holder replaced the worksheet: {r.status_code} {r.text[:200]}"
    )


def test_stress_scenarios_put_is_refused_under_a_foreign_lock(
    client, api_project, same_org_other_user,
):
    name = api_project("wslock-ss")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200

    r = same_org_other_user.put(
        f"/api/projects/{name}/stress_scenarios", json={"scenarios": []},
    )
    assert _is_lock_refusal(r), (
        f"a non-holder replaced the stress scenarios: {r.status_code} {r.text[:200]}"
    )


def test_the_holder_can_still_write_both(client, api_project):
    """The check is check-only: the holder's own lock must not refuse them."""
    name = api_project("wslock-holder")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200

    ws = client.put(f"/api/projects/{name}/worksheet",
                    json={"manual_rows": [], "overlays": {}})
    ss = client.put(f"/api/projects/{name}/stress_scenarios", json={"scenarios": []})
    assert ws.status_code == 200, f"holder refused on worksheet: {ws.text[:200]}"
    assert ss.status_code == 200, f"holder refused on stress_scenarios: {ss.text[:200]}"


def test_an_unlocked_project_is_writable_and_stays_unlocked(client, api_project,
                                                            same_org_other_user):
    """
    Check-only, not acquire: writing a sidecar on a FREE project must not claim
    the lock, or a passive write would leave a 120s claim behind that outlives
    the request (the reasoning `_check_project_lock`'s own docstring gives).
    """
    name = api_project("wslock-free")
    # `api_project` saves the project, and save is an ACQUIRE edge, so the
    # project is NOT free until the creator releases it. The first cut of this
    # test omitted the release and failed on its own wrong premise rather than
    # on a defect — worth the explicit step here.
    assert client.delete(f"/api/projects/{name}/lock").status_code in (200, 204)

    r = same_org_other_user.put(f"/api/projects/{name}/worksheet",
                                json={"manual_rows": [], "overlays": {}})
    assert r.status_code == 200, f"free project refused: {r.text[:200]}"
    # The original holder-less state must survive: `client` can still take it.
    assert client.post(f"/api/projects/{name}/lock").status_code == 200, (
        "the sidecar write acquired the lock instead of only checking it"
    )
