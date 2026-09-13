"""
`extra_functionality_code` is `exec()`-ed in-process with full filesystem and
network privileges. Holding a project's lock must not be enough to set it.

The gate as shipped was a single process-wide environment variable,
`PYPSA_GUI_ALLOW_USER_CODE`, justified in `solver_service.user_code_enabled`'s
docstring on the grounds that "the GUI has no auth layer" and that deployments
are "single-user / localhost". Both were true when written and neither is now:
`main.py` refuses any unauthenticated `/api/*` request, and the product has orgs
and memberships. So with the flag on, ANY member could set the field and have it
executed — and being a lock holder means "nobody else is editing this", not "may
run code on the host".

These tests pin the authorization half. The env flag stays as the deployment's
kill switch and is asserted here too: both must hold.

See `docs/superpowers/assessments/2026-09-12-per-route-authorization-audit.md`
(finding 4) and gap 2 of the 2026-09-10 hardening assessment.
"""
import uuid as _uuid

import pytest

from tests.conftest import attach_session


_CODE = "def extra_functionality(n, snapshots):\n    pass\n"


@pytest.fixture
def member_client(_auth_db, seeded_identity):
    """
    A client whose user is a plain MEMBER of the seeded org, not an admin.

    The stock `client` fixture's user is seeded `role="admin"`, so it cannot
    show the refusal; without a member the guard would look like it worked
    while testing nothing.
    """
    from datetime import datetime, timezone

    from db.models import OrgMembership, User
    from fastapi.testclient import TestClient
    from services.auth_service import hash_password

    import main

    _engine, session_local = _auth_db
    with session_local() as db:
        user = User(
            id=_uuid.uuid4(),
            email=f"member-{_uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("irrelevant-for-session-attach"),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=user.id,
                             org_id=seeded_identity["org_id"], role="member"))
        db.commit()
        user_id = user.id

    with TestClient(main.app) as c:
        yield attach_session(c, session_local, user_id)


def _enable_flag(monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")


def test_a_member_cannot_set_user_code_even_with_the_flag_on(
    member_client, monkeypatch,
):
    """The headline: the env flag is the operator's switch, not a grant to all."""
    _enable_flag(monkeypatch)
    r = member_client.put(
        "/api/simulation/solver_config", json={"extra_functionality_code": _CODE},
    )
    assert r.status_code == 403, (
        f"a plain member set exec()-able code: {r.status_code} {r.text[:300]}"
    )


def test_the_code_is_not_stored_when_the_member_is_refused(
    member_client, monkeypatch,
):
    """
    A 403 that still wrote the field would be worse than no gate: the refusal
    would read as protection while the next run executes the code.
    """
    _enable_flag(monkeypatch)
    member_client.put(
        "/api/simulation/solver_config", json={"extra_functionality_code": _CODE},
    )
    got = member_client.get("/api/simulation/solver_config")
    assert got.status_code == 200, got.text
    assert not (got.json().get("extra_functionality_code") or "").strip(), (
        "the refused code was stored anyway"
    )


def test_an_admin_can_still_set_it_when_the_flag_is_on(client, monkeypatch):
    """The gate must not break the operator's supported path."""
    _enable_flag(monkeypatch)
    r = client.put(
        "/api/simulation/solver_config", json={"extra_functionality_code": _CODE},
    )
    assert r.status_code == 200, f"admin was refused: {r.status_code} {r.text[:300]}"
    assert _CODE.strip() in (r.json().get("extra_functionality_code") or "")


def test_an_admin_is_still_refused_when_the_flag_is_off(client, monkeypatch):
    """
    Both conditions are required. The env flag remains the deployment's kill
    switch — admin is not a way around an operator who never opted in.
    """
    monkeypatch.delenv("PYPSA_GUI_ALLOW_USER_CODE", raising=False)
    r = client.put(
        "/api/simulation/solver_config", json={"extra_functionality_code": _CODE},
    )
    assert r.status_code == 403, (
        f"admin bypassed the operator opt-in: {r.status_code} {r.text[:300]}"
    )


def test_a_member_can_still_change_ordinary_solver_settings(
    member_client, monkeypatch,
):
    """
    Scope check. The gate must cover the exec() field and nothing else — if it
    refused the whole config PUT it would break every member's solver settings.
    """
    _enable_flag(monkeypatch)
    r = member_client.put("/api/simulation/solver_config", json={"mode": "lopf"})
    assert r.status_code == 200, (
        f"the gate leaked onto ordinary settings: {r.status_code} {r.text[:300]}"
    )


def test_the_docstring_documents_the_authorization_requirement():
    """
    The stale justification is part of the defect: a reader who believes the
    risk was "no auth" will conclude it is solved and relax the gate.

    Asserted POSITIVELY — that the admin requirement and the flag's real role
    are documented — rather than by the absence of the old phrase. The first
    cut of this test asserted `"no auth layer" not in doc` and failed against a
    correct docstring, because the fix QUOTES the old claim in order to refute
    it, which is better documentation than deleting it. An absence assertion
    could not tell those two apart.
    """
    from services import solver_service

    doc = (solver_service.user_code_enabled.__doc__ or "").lower()
    assert "admin" in doc, (
        "the docstring does not mention that setting the field requires an admin"
    )
    # An `and`, not an `or`. The first cut used `or` and a mutation that deleted
    # the whole authorization sentence still passed on the surviving marker — so
    # the guard did not hold the property it claimed to.
    assert "not the whole gate" in doc, (
        "the docstring still presents the env flag as the whole gate"
    )
    assert "_gate_user_code" in doc, (
        "the docstring does not say WHERE authorization is actually enforced, so "
        "a reader cannot find the other half of the gate"
    )
    # If it repeats the old claim, it must be marked as no longer true.
    if "no auth layer" in doc:
        assert ("neither is now" in doc or "wrong" in doc), (
            "the docstring repeats 'no auth layer' without refuting it"
        )
