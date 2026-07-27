"""
E2E QA for Step 0b — the session-bound active project.

S0.10 is the plan's case. The rest are the two situations the plan flagged as
"must be handled explicitly", plus the properties that make S0.10 falsifiable:
a test that only checks "user A sees A's data" passes on a single-user process
where B never asked for anything.
"""
from __future__ import annotations

import pypsa
import pytest

from services.pypsa_service import PyPSAService
from tests.conftest import build_network


def _named_network(bus: str) -> pypsa.Network:
    n = build_network()
    n.add("Bus", bus, v_nom=380.0)
    return n


def _save(client, name: str, install_network, bus: str) -> None:
    install_network(_named_network(bus), name=name)
    resp = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text


# ── S0.10 ───────────────────────────────────────────────────────────────────

def test_s0_10_two_sessions_read_their_own_active_project(
    client, other_org_client, install_network
):
    """
    S0.10 — user A and user B on ONE process with different active projects:
    `GET /api/network/buses` returns each user's own network.

    This is the route class the plan calls the hinge: it names no project at
    all, so before Step 0b it resolved through a PROCESS-GLOBAL active context.
    Whoever activated last decided what BOTH users read next.

    The assertion is deliberately symmetric and INTERLEAVED. A one-directional
    check ("A still sees A after B activates") passes on a process where B's
    request simply never ran; alternating the reads is what proves each request
    resolves independently rather than inheriting whatever the last one left.
    """
    _save(client, "AliceProject", install_network, "ALICE_BUS")
    _save(other_org_client, "BobProject", install_network, "BOB_BUS")

    assert client.post("/api/projects/AliceProject/activate").status_code == 200
    assert other_org_client.post("/api/projects/BobProject/activate").status_code == 200

    for _ in range(3):
        alice = {r["name"] for r in client.get("/api/network/buses").json()}
        bob = {r["name"] for r in other_org_client.get("/api/network/buses").json()}
        assert "ALICE_BUS" in alice and "BOB_BUS" not in alice, alice
        assert "BOB_BUS" in bob and "ALICE_BUS" not in bob, bob

    # `/api/network/meta` reports the binding, not just the payload — a shared
    # context would show one project name to both callers.
    assert client.get("/api/network/meta").json()["loaded_project"] == "AliceProject"
    assert (
        other_org_client.get("/api/network/meta").json()["loaded_project"]
        == "BobProject"
    )


def test_s0_10b_a_write_by_one_session_is_invisible_to_the_other(
    client, other_org_client, install_network
):
    """
    The read half of S0.10 can pass on two contexts that happen to hold
    different data. This proves they are genuinely separate objects: a MUTATION
    through one session must not appear in the other.
    """
    _save(client, "WriterProject", install_network, "W_BUS")
    _save(other_org_client, "ReaderProject", install_network, "R_BUS")
    assert client.post("/api/projects/WriterProject/activate").status_code == 200
    assert other_org_client.post("/api/projects/ReaderProject/activate").status_code == 200

    created = client.post(
        "/api/network/buses", json={"name": "ONLY_FOR_WRITER", "v_nom": 220.0}
    )
    assert created.status_code in (200, 201), created.text

    writer = {r["name"] for r in client.get("/api/network/buses").json()}
    reader = {r["name"] for r in other_org_client.get("/api/network/buses").json()}
    assert "ONLY_FOR_WRITER" in writer
    assert "ONLY_FOR_WRITER" not in reader, (
        "a mutation leaked across sessions — the two are still one context"
    )


# ── The unbound "New Project" case ──────────────────────────────────────────

def test_unbound_sessions_get_their_own_scratch_context(client, other_org_client):
    """
    `pypsa_service` documented `_active` as uniquely handling the UNBOUND (New
    Project) case "the registry can't key (no project_id yet)". That case is the
    DEFAULT state on first load, not an edge case, so if it stayed process-global
    every unbound user on the pod would share one draft network.

    Each session now gets a `scratch:<session-id>` slot.
    """
    assert client.post("/api/network/reset").status_code == 200
    assert other_org_client.post("/api/network/reset").status_code == 200

    a = client.post("/api/network/buses", json={"name": "DRAFT_A", "v_nom": 110.0})
    assert a.status_code in (200, 201), a.text

    a_buses = {r["name"] for r in client.get("/api/network/buses").json()}
    b_buses = {r["name"] for r in other_org_client.get("/api/network/buses").json()}
    assert "DRAFT_A" in a_buses
    assert "DRAFT_A" not in b_buses, (
        "two unbound sessions share one draft network — the New Project state "
        "is process-global again"
    )

    scratch = [k for k in PyPSAService.list_ids() if k.startswith("scratch:")]
    assert len(scratch) >= 2, f"expected a scratch slot per session, got {scratch}"


def test_reset_clears_the_session_pointer(client, install_network, _auth_db):
    """
    `POST /api/network/reset` must un-bind the SESSION, not just swap the
    in-memory network.

    Leaving the pointer set would make the very next request re-resolve the old
    project and hydrate it back on top of the cleared network — the reset would
    appear to silently undo itself one request later.
    """
    _save(client, "ToBeReset", install_network, "RESET_BUS")
    assert client.post("/api/projects/ToBeReset/activate").status_code == 200
    assert client.get("/api/network/meta").json()["loaded_project"] == "ToBeReset"

    assert client.post("/api/network/reset").status_code == 200
    assert client.get("/api/network/meta").json()["loaded_project"] is None

    buses = {r["name"] for r in client.get("/api/network/buses").json()}
    assert "RESET_BUS" not in buses, "the reset was undone by a re-resolve"


def test_activate_persists_the_pointer_to_the_database(
    client, install_network, _auth_db, project_row
):
    """
    The pointer is DURABLE, not just in-process. That is the property Step 3
    depends on: a request landing on a different replica has to be able to find
    out which project this session was looking at.
    """
    from sqlalchemy import select

    from db.models import Session as SessionRow

    _save(client, "Durable", install_network, "DURABLE_BUS")
    assert client.post("/api/projects/Durable/activate").status_code == 200

    _engine, session_local = _auth_db
    with session_local() as db:
        pointers = db.scalars(
            select(SessionRow.active_project_id).where(
                SessionRow.active_project_id.is_not(None)
            )
        ).all()
    assert project_row("Durable").id in pointers, (
        "activate did not write sessions.active_project_id — the pointer is "
        "still only in process memory"
    )


# ── Revocation-in-between ───────────────────────────────────────────────────

def test_pointer_is_rechecked_against_the_acl_on_every_request(
    client, other_org_client, install_network, _auth_db, project_row
):
    """
    A pointer is not a capability.

    It is written once at activate time but read on every later request, and
    access can be revoked in between. Forging one directly (as a stolen or
    tampered-with row would) must not grant access: the resolver re-checks and
    CLEARS it, dropping the user to the unbound state rather than serving
    another org's network.
    """
    _save(client, "OrgAOnly", install_network, "SECRET_BUS")
    row = project_row("OrgAOnly")

    _engine, session_local = _auth_db
    from sqlalchemy import select, update

    from db.models import Session as SessionRow

    # Point ORG B's session at ORG A's project behind the API's back.
    token_hash_of_b = None
    with session_local() as db:
        # The most recently created session belongs to the other-org client.
        rows = db.scalars(select(SessionRow).order_by(SessionRow.expires_at.desc())).all()
        for candidate in rows:
            from db.models import User

            user = db.get(User, candidate.user_id)
            if user is not None and user.email == "other@example.com":
                token_hash_of_b = candidate.token_hash
                break
        assert token_hash_of_b is not None
        db.execute(
            update(SessionRow)
            .where(SessionRow.token_hash == token_hash_of_b)
            .values(active_project_id=row.id)
        )
        db.commit()

    buses = other_org_client.get("/api/network/buses")
    assert buses.status_code == 200
    assert "SECRET_BUS" not in {r["name"] for r in buses.json()}, (
        "a forged active-project pointer served another org's network"
    )

    with session_local() as db:
        cleared = db.scalar(
            select(SessionRow.active_project_id).where(
                SessionRow.token_hash == token_hash_of_b
            )
        )
    assert cleared is None, "the unauthorized pointer was left in place"


# ── Plural eviction protection ──────────────────────────────────────────────

def test_every_session_active_project_is_protected_from_eviction(
    client, other_org_client, install_network
):
    """
    `_evict_if_over_cap` protected a SINGLE `get_active_id()`. With one active
    project per session that is not enough, and eviction is still WRITE-BACK —
    so an unprotected victim is not merely dropped, it is FLUSHED to disk over
    whatever is there. Both sessions' actives must survive a cap squeeze.
    """
    _save(client, "KeepA", install_network, "KEEP_A_BUS")
    _save(other_org_client, "KeepB", install_network, "KEEP_B_BUS")
    assert client.post("/api/projects/KeepA/activate").status_code == 200
    assert other_org_client.post("/api/projects/KeepB/activate").status_code == 200

    protected = PyPSAService._session_active_keys()
    assert len(protected) >= 2, (
        f"expected one protected key per live session, got {protected}"
    )

    resident_before = set(PyPSAService.list_ids())
    original_cap = PyPSAService.RESIDENT_CAP
    PyPSAService.RESIDENT_CAP = 1
    try:
        # Force the cap check with a throwaway registration.
        from services.project_context import ProjectContext

        filler = ProjectContext(network=pypsa.Network(), loaded_project="Filler")
        PyPSAService.register("org-x:filler", filler)

        survivors = set(PyPSAService.list_ids())
        # Only RESIDENT protected keys are assertable: `_session_active_keys`
        # also names scratch slots for live sessions from earlier tests that
        # were never registered, and "was never resident" is not "was evicted".
        for key in protected & resident_before:
            assert key in survivors, f"{key} was evicted despite being active"
    finally:
        PyPSAService.RESIDENT_CAP = original_cap


@pytest.mark.parametrize("marker", ["S0.10"])
def test_qa_case_ids_are_covered(marker):
    import pathlib

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert f"{marker} " in source or f"{marker} —" in source
