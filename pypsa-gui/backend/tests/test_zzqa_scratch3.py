"""QA SCRATCH — delete after review. Probes tenancy/lock coverage."""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from tests.conftest import attach_session, _SEED


@pytest.fixture
def same_org_other_user(_auth_db, seeded_identity):
    """A SECOND user in the SAME org as `client`, org role 'admin'."""
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        u = User(
            id=_uuid.uuid4(),
            email=f"colleague-{_uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password(_SEED["password"]),
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


def test_worksheet_put_ignores_foreign_lock(client, api_project, same_org_other_user):
    name = api_project("lockdemo")
    # A takes the edit lock.
    r = client.post(f"/api/projects/{name}/lock")
    assert r.status_code == 200, r.text

    b = same_org_other_user
    # Control: layout PUT is lock-checked.
    layout = b.put(f"/api/projects/{name}/layout", json={"nodes": {}})
    # Control: uploads POST is lock-checked.
    up = b.post(f"/api/projects/{name}/uploads",
                files={"file": ("a.csv", b"x,y\n1,2\n", "text/csv")})
    # Subject: worksheet PUT.
    ws = b.put(f"/api/projects/{name}/worksheet",
               json={"manual_rows": [{"id": "hacked"}], "overlays": {}})
    ss = b.put(f"/api/projects/{name}/stress_scenarios", json={"scenarios": []})
    print("LAYOUT", layout.status_code, layout.text[:200])
    print("UPLOAD", up.status_code, up.text[:200])
    print("WORKSHEET", ws.status_code, ws.text[:300])
    print("STRESS", ss.status_code, ss.text[:300])
    # Confirm the write actually landed (A reads it back)
    back = client.get(f"/api/projects/{name}/worksheet")
    print("READBACK", back.status_code, back.text[:300])
