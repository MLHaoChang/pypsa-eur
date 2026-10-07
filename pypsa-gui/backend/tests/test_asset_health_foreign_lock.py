"""
The asset-health ledger must respect another user's edit lock.

`PUT /api/projects/{name}/asset_health` carried `ProjectAccessDep` — the ACL
check, answering "may this caller see this project" — and no lock check at all,
and took no `db` or `user` parameter, so it had nothing to check a lock WITH.
`/api/projects` is deliberately absent from `main._FOREIGN_LOCK_GATE_PREFIXES`
because that family enforces the lock in its handlers, so there was no gate
behind it either.

This is the FOURTH instance of one miss, and the second in this very file:
`routers/uploads.py` had two, `worksheet` and `stress_scenarios` were the third
(`tests/test_worksheet_foreign_lock.py`, whose docstring already called itself
"the third instance"). This PUT landed in `a130636` on 2026-09-10, two days
before the commit that fixed its two siblings, so it was never in the audit's
route list and never in that fix's scope. Nothing failed. That is OPEN-ITEMS
item 6's complaint, and this file is the fourth data point for it.

Why it matters: `save_asset_health`'s own docstring says it "replace[s] the
ledger whole", and it bumps `version`, so a non-holder does not merge into the
holder's provenance ledger — it replaces it and hands the holder's client a
higher version number, which that client reads as the newer authoritative copy.
`asset_health.json` is in `routers/projects._BUNDLE_FILES`, so the write also
propagates into every later bundle export and snapshot, and
`services/adequacy/study_report.py:_evidence_gaps` reads the file: a wiped
ledger makes the holder's own study report call every asset-level outage rate
`unsourced`.

Reproduced 2026-09-30 against `a9a1f5b`:
`findings/2026-09-30-asset-health-put-ignores-a-foreign-edit-lock.md`.
Server only: `_check_project_lock` early-returns in local mode, so the desktop
build is unaffected.
"""
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from tests.conftest import attach_session

# One valid ledger row. `_validate_entry` refuses unknown keys and requires a
# rate or an MTTR plus `measured_at`, so this is the minimum that actually
# records provenance — a row that 422s would make every assertion below pass
# while proving nothing was ever writable.
_ENTRY = {
    "component": "lines",
    "name": "L1",
    "outage_rate_value": 0.01,
    "method": "vendor_datasheet",
    "source_ref": "ACME datasheet rev C",
    "measured_at": "2026-09-01",
    "confidence": "high",
}


@pytest.fixture
def same_org_other_user(_auth_db, seeded_identity):
    """A second user in the SAME org — the realistic intruder for a lock test.

    Deliberately not a cross-ORG user: the ACL is working, and a cross-org
    caller would be refused by `ProjectAccessDep` before the lock is ever
    reached. That would make this file green for the wrong reason.
    """
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


def test_the_control_really_is_lock_checked(client, api_project,
                                            same_org_other_user):
    """
    Guard on the guard, the same shape `test_worksheet_foreign_lock.py` uses.

    If `worksheet` ever stops being lock-checked, the subject assertions below
    would still pass while proving nothing about `asset_health` — so the sibling
    is asserted, not assumed.
    """
    name = api_project("ahlock-control")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200

    ws = same_org_other_user.put(f"/api/projects/{name}/worksheet",
                                 json={"manual_rows": [], "overlays": {}})
    assert _is_lock_refusal(ws), (
        f"sibling control: {ws.status_code} {ws.text[:160]}"
    )


def test_asset_health_put_is_refused_under_a_foreign_lock(client, api_project,
                                                          same_org_other_user):
    """
    The property, not the status code: the holder's ledger SURVIVES.

    A test that only asserted 409 would go green on any refusal — a 409 from
    somewhere else, or a validation error that happened to share the code. What
    must be true is that the holder's provenance is still there afterwards.
    """
    name = api_project("ahlock-subject")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200

    seeded = client.put(f"/api/projects/{name}/asset_health",
                        json={"entries": [_ENTRY]})
    assert seeded.status_code == 200, f"holder could not seed: {seeded.text[:200]}"
    before = client.get(f"/api/projects/{name}/asset_health").json()
    assert before["entries"], "fixture seeded nothing, so nothing could be lost"

    r = same_org_other_user.put(f"/api/projects/{name}/asset_health",
                                json={"entries": []})

    after = client.get(f"/api/projects/{name}/asset_health").json()
    assert after["entries"] == before["entries"], (
        f"a non-holder wiped the holder's provenance ledger: intruder got "
        f"{r.status_code}; entries {before['entries']} -> {after['entries']}"
    )
    assert after["version"] == before["version"], (
        f"a refused write still bumped `version` to {after['version']}, which "
        f"the holder's client reads as a newer authoritative copy"
    )
    assert _is_lock_refusal(r), (
        f"the ledger survived but the refusal was not a lock refusal: "
        f"{r.status_code} {r.text[:200]}"
    )


def test_the_holder_can_still_write(client, api_project):
    """Check-only: the holder's own lock must not refuse them."""
    name = api_project("ahlock-holder")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200

    r = client.put(f"/api/projects/{name}/asset_health",
                   json={"entries": [_ENTRY]})
    assert r.status_code == 200, f"holder refused on asset_health: {r.text[:200]}"
    assert r.json()["entries"], r.text[:200]


def test_an_unlocked_project_is_writable_and_stays_unlocked(client, api_project,
                                                            same_org_other_user):
    """
    Check-only, not acquire: writing a sidecar on a FREE project must not claim
    the lock, or a passive write would leave a 120s claim behind that outlives
    the request (the reasoning `_check_project_lock`'s own docstring gives).
    """
    name = api_project("ahlock-free")
    # `api_project` saves the project, and save is an ACQUIRE edge, so the
    # project is NOT free until the creator releases it. Omitting this release
    # is what made the first cut of the sibling file's equivalent test fail on
    # its own wrong premise rather than on a defect.
    assert client.delete(f"/api/projects/{name}/lock").status_code in (200, 204)

    r = same_org_other_user.put(f"/api/projects/{name}/asset_health",
                                json={"entries": [_ENTRY]})
    assert r.status_code == 200, f"free project refused: {r.text[:200]}"
    assert client.post(f"/api/projects/{name}/lock").status_code == 200, (
        "the sidecar write acquired the lock instead of only checking it"
    )
