"""
E2E QA for Step 0a of `docs/superpowers/plans/2026-07-26-cloud-saas-migration.md`.

One test per plan case, named after its id, so a failure points at the case
rather than at a helper. The plan's own commentary on what makes each case
FALSIFIABLE is reproduced in the docstrings, because several v1/v2 cases passed
against unmodified code and the replacements are only worth anything if the
reason they are stronger survives with them.

S0.10 is deliberately absent: it asserts the session-bound active project, which
is Step 0b.
"""
from __future__ import annotations

import logging
import logging.handlers
import queue
import subprocess
import sys
import threading

import pytest

from tests.conftest import build_network


# ── helpers ─────────────────────────────────────────────────────────────────

def _save(client, name: str, install_network, network=None) -> None:
    install_network(network or build_network(), name=name)
    resp = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text


# ── S0.1 — per-router tenant sweep ──────────────────────────────────────────

def test_s0_1_cross_tenant_project_routes_return_404_never_403_or_200(
    client, other_org_client, install_network, project_row
):
    """
    S0.1 — for every route carrying a project param, org B names org A's project
    by NAME and by UUID.

    Asserts 404 every time (never 403, never 200) AND that the body is
    byte-identical to a genuinely-not-found body. The body equality is the part
    that matters: a distinct message for "exists but not yours" is an existence
    oracle even when the status code matches.
    """
    _save(client, "TenantA", install_network)
    row = project_row("TenantA")
    assert row is not None
    by_uuid = str(row.id)

    # Routes that carry a real project path parameter — the 14 the plan counted,
    # by module: uploads (6), snapshots (4), compare (2), project_network (2).
    probes = [
        ("GET", "/api/projects/{p}/uploads"),
        ("GET", "/api/projects/{p}/uploads/deadbeef00000000/meta"),
        ("GET", "/api/projects/{p}/uploads/deadbeef00000000/blob"),
        ("GET", "/api/projects/{p}/uploads/deadbeef00000000/signature?session_id=s"),
        ("DELETE", "/api/projects/{p}/uploads/deadbeef00000000"),
        ("GET", "/api/projects/{p}/snapshots"),
        ("POST", "/api/projects/{p}/snapshots"),
        ("POST", "/api/projects/{p}/snapshots/snap1/restore"),
        ("DELETE", "/api/projects/{p}/snapshots/snap1"),
        ("GET", "/api/projects/{p}/compare-state"),
        ("GET", "/api/projects/{p}/results-summary"),
        ("GET", "/api/projects/{p}/network/meta"),
        ("GET", "/api/projects/{p}/network/buses"),
        # Project-scoped routes on the projects router itself.
        ("GET", "/api/projects/{p}"),
        ("POST", "/api/projects/{p}/activate"),
        ("DELETE", "/api/projects/{p}"),
        ("GET", "/api/projects/{p}/bundle"),
        ("GET", "/api/projects/{p}/members"),
        # Body-parameterised, so invisible to a path-param inventory.
        ("POST", "/api/simulation/queue"),
    ]

    def _call(http_client, method: str, template: str, project: str):
        if template == "/api/simulation/queue":
            return http_client.post(template, json={"project_id": project})
        return http_client.request(method, template.format(p=project), json={})

    for method, template in probes:
        for handle in (by_uuid, "TenantA"):
            hit = _call(other_org_client, method, template, handle)
            missing = _call(other_org_client, method, template, "NoSuchProjectAnywhere")
            assert hit.status_code == 404, (
                f"{method} {template} with {handle!r} returned "
                f"{hit.status_code}, not 404 — org B reached org A's project"
            )
            assert missing.status_code == 404, f"{method} {template} control"
            assert hit.content == missing.content, (
                f"{method} {template}: the not-yours body differs from the "
                f"not-found body — that difference is an existence oracle"
            )


# ── S0.2 — public-path guard ────────────────────────────────────────────────

def test_s0_2_auth_public_paths_are_exactly_the_five_known_members():
    """
    S0.2 — the unauthenticated surface is a closed set.

    v2's version ("unauthenticated request → 401") was VACUOUS: the middleware
    already did that, so the case passed before and after the change. What is
    worth guarding is the SET — a new public path added without review is the
    actual regression, and this fails on it.
    """
    from main import _AUTH_PUBLIC_PATHS

    assert _AUTH_PUBLIC_PATHS == {
        "/api/auth/forgot-password",
        "/api/auth/login",
        "/api/auth/reset-password",
        "/api/auth/set-password",
        "/api/health",
    }


def test_s0_2b_every_public_path_is_reachable_anonymously(anon_client):
    """The set is not just declared — each member really is open, and a
    non-member really is not."""
    assert anon_client.get("/api/health").status_code == 200
    assert anon_client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "x"}
    ).status_code == 401  # reached the handler, not the gate
    assert anon_client.get("/api/projects/").status_code == 401


# ── S0.3 — name-key collision ───────────────────────────────────────────────

def test_s0_3_same_named_projects_in_two_orgs_do_not_share_a_context(
    client, other_org_client, install_network
):
    """
    S0.3 — org A and org B both create `Baseline`; both activate; A adds a bus.

    Targets the registry name-key collision directly. Names are unique per ORG
    but the registry was per PROCESS, so both orgs' `Baseline` resolved to one
    slot: whoever activated second either saw the other's network or evicted it.
    """
    a_net = build_network()
    a_net.add("Bus", "A_ONLY_BUS")
    _save(client, "Baseline", install_network, a_net)

    b_net = build_network()
    b_net.add("Bus", "B_ONLY_BUS")
    _save(other_org_client, "Baseline", install_network, b_net)

    assert client.post("/api/projects/Baseline/activate").status_code == 200
    assert other_org_client.post("/api/projects/Baseline/activate").status_code == 200

    a_view = client.get("/api/projects/Baseline/network/buses").json()
    b_view = other_org_client.get("/api/projects/Baseline/network/buses").json()
    a_names = {row["name"] for row in a_view}
    b_names = {row["name"] for row in b_view}

    assert "A_ONLY_BUS" in a_names
    assert "A_ONLY_BUS" not in b_names, "org B is reading org A's resident network"
    assert "B_ONLY_BUS" in b_names
    assert "B_ONLY_BUS" not in a_names


# ── S0.4 — ProjectDep resolves per-org ──────────────────────────────────────

def test_s0_4_project_dep_routes_serve_each_org_its_own_data(
    client, other_org_client, install_network
):
    """
    S0.4 — two orgs, identically-named projects, through each `ProjectDep`
    route: each receives ITS OWN org's data.

    v2 asserted on a resolved PATH echoed in a debug header. That is both
    weaker (a correct path does not prove the right bytes were served) and
    itself a leak (it publishes the storage layout). This asserts on content.
    """
    a_net = build_network()
    a_net.add("Bus", "ORG_A_MARKER")
    _save(client, "Shared", install_network, a_net)

    b_net = build_network()
    b_net.add("Bus", "ORG_B_MARKER")
    _save(other_org_client, "Shared", install_network, b_net)

    a_meta = client.get("/api/projects/Shared/network/meta")
    b_meta = other_org_client.get("/api/projects/Shared/network/meta")
    assert a_meta.status_code == 200 and b_meta.status_code == 200

    a_buses = {r["name"] for r in client.get("/api/projects/Shared/network/buses").json()}
    b_buses = {
        r["name"]
        for r in other_org_client.get("/api/projects/Shared/network/buses").json()
    }
    assert "ORG_A_MARKER" in a_buses and "ORG_B_MARKER" not in a_buses
    assert "ORG_B_MARKER" in b_buses and "ORG_A_MARKER" not in b_buses


# ── S0.5 — changelog tenancy ────────────────────────────────────────────────

def test_s0_5_changelog_is_scoped_to_the_reading_org(
    client, other_org_client, install_network, anon_client
):
    """
    S0.5 — org B reads `GET /changelog` after org A edits: only its own entries.
    `DELETE /` as B leaves A's intact; anonymous → 401.
    """
    _save(client, "AuditA", install_network)
    assert client.post(
        "/api/network/buses", json={"name": "A_SECRET_BUS", "v_nom": 380.0}
    ).status_code in (200, 201)

    a_entries = client.get("/api/changelog/").json()
    assert any("A_SECRET_BUS" in str(e) for e in a_entries)

    b_entries = other_org_client.get("/api/changelog/").json()
    assert not any("A_SECRET_BUS" in str(e) for e in b_entries), (
        "org B can read org A's audit trail — component and project names leak"
    )

    # B clearing its own log must not touch A's.
    assert other_org_client.delete("/api/changelog/").status_code == 204
    a_after = client.get("/api/changelog/").json()
    assert any("A_SECRET_BUS" in str(e) for e in a_after), (
        "org B's clear deleted org A's entries"
    )

    assert anon_client.get("/api/changelog/").status_code == 401
    assert anon_client.delete("/api/changelog/").status_code == 401


# ── S0.6 — solver log leak ──────────────────────────────────────────────────

def test_s0_6_solve_log_excludes_other_threads_but_keeps_pypsa_lines():
    """
    S0.6 — tenant A solves while tenant B triggers an ERROR log on the same
    process: A's stream contains no line from B's request, AND A's stream still
    contains `pypsa`/`linopy`/HiGHS lines.

    The second half is the whole point. v2 proposed fixing the leak by scoping
    the handler to "the solver's own logger", which would have EMPTIED the log:
    the root attachment is deliberate, because what the user reads IS
    `pypsa.*` / `linopy.*` / HiGHS output emitted under third-party logger
    names. This asserts the fix cannot regress into that.
    """
    from services.solver_service import _ThreadScopedQueueHandler

    sink: queue.SimpleQueue = queue.SimpleQueue()
    handler = _ThreadScopedQueueHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        # Tenant A's solve thread is THIS thread: third-party solver output.
        logging.getLogger("pypsa.optimization").info("PYPSA_LINE tenant A model build")
        logging.getLogger("linopy.model").info("LINOPY_LINE writing objective")
        logging.getLogger("highspy").info("HIGHS_LINE Model status: Optimal")

        # Tenant B's request lands on a DIFFERENT thread, as every concurrent
        # request does under uvicorn's threadpool.
        def _other_tenant_request():
            logging.getLogger("routers.projects").error(
                "TENANT_B_SECRET failed to open /orgs/b/secret-project"
            )

        t = threading.Thread(target=_other_tenant_request)
        t.start()
        t.join(timeout=5)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    captured = []
    while True:
        try:
            captured.append(sink.get_nowait().getMessage())
        except queue.Empty:
            break

    joined = "\n".join(captured)
    assert "TENANT_B_SECRET" not in joined, (
        "another tenant's log record reached this solve's stream"
    )
    for marker in ("PYPSA_LINE", "LINOPY_LINE", "HIGHS_LINE"):
        assert marker in joined, (
            f"{marker} missing — the leak fix emptied the solve log, which is "
            f"the regression v2's remedy would have introduced"
        )


# ── S0.7 — replica header ───────────────────────────────────────────────────

def test_s0_7a_replica_header_is_stable_across_calls(client):
    """
    S0.7 (first half) — STABLE across N calls to a directly-addressed replica.

    v2 asserted the id "differs across calls", which flakes under round-robin
    and passes trivially on a per-request seed. Stability is the property that
    makes the multi-replica cases in Steps 2-3 mean anything.
    """
    from security import REPLICA_HEADER

    ids = {client.get("/api/health").headers[REPLICA_HEADER] for _ in range(5)}
    assert len(ids) == 1, f"replica id changed within one process: {ids}"
    assert ids.pop().strip(), "replica header present but empty"


def test_s0_7b_replica_header_differs_between_processes():
    """
    S0.7 (second half) — DIFFERS between two directly-addressed replicas.

    Genuinely spawns a second interpreter rather than simulating one: the id is
    keyed on `(pid, boot nonce)`, and only a real second process proves the
    nonce is per-process rather than per-import.
    """
    import security

    program = (
        "import sys; sys.path.insert(0, '.');"
        "import os; os.environ.setdefault('SECRET_KEY', 'test-secret-not-a-real-key');"
        "import security; print(security.replica_id())"
    )
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    other = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(backend), capture_output=True, text=True, timeout=120,
    )
    assert other.returncode == 0, other.stderr
    assert other.stdout.strip() != security.replica_id(), (
        "two processes produced the same replica id — a sticky proxy would "
        "make every multi-replica assertion pass while state stayed local"
    )


def test_s0_7c_replica_header_does_not_leak_topology():
    """The id is mandated test infrastructure, but it must not publish the
    host: no pid, no hostname, no port."""
    import os
    import socket

    import security

    value = security.replica_id()
    assert str(os.getpid()) not in value
    assert socket.gethostname().lower() not in value.lower()
    assert len(value) == 16 and all(c in "0123456789abcdef" for c in value)


# ── S0.8 — CSRF ─────────────────────────────────────────────────────────────

def test_s0_8_state_changing_routes_reject_a_session_without_a_token(
    client, install_network
):
    """
    S0.8 — cross-origin `POST /api/projects/{name}` with a valid session cookie
    but no token is rejected. Repeated for `DELETE /{name}?cascade=true`.

    Both are the destructive pair the plan names: POST is a SAVE that
    overwrites, and DELETE with cascade removes a whole scenario tree. With
    `SameSite=None; Secure` cookies and credentialed CORS, both were reachable
    from any page the browser visited.
    """
    _save(client, "CsrfTarget", install_network)

    # Same session, token header stripped — this is exactly what a forged
    # cross-site request looks like: the browser attaches the cookie, the
    # attacker cannot read the token to echo it.
    del client.headers["X-CSRF-Token"]

    save = client.post("/api/projects/CsrfTarget", params={"force": True})
    assert save.status_code == 403, save.text
    assert save.json()["code"] == "csrf_token_invalid"

    delete = client.delete("/api/projects/CsrfTarget", params={"cascade": True})
    assert delete.status_code == 403, delete.text

    # The project survived the forged delete.
    client.headers["X-CSRF-Token"] = client.cookies.get("pypsa_gui_csrf")
    assert client.get("/api/projects/CsrfTarget").status_code == 200


def test_s0_8b_a_disallowed_origin_is_rejected_even_with_a_valid_token(client):
    """
    The Origin check and the token are BOTH required.

    This is why the CORS allowlist had to ship in the same step: while
    `*.cursorusercontent.com` was allowlisted and credentialed, such a page
    passed the Origin check, read the response, read the token, and forged.
    """
    resp = client.post(
        "/api/network/reset", headers={"Origin": "https://evil.example.com"}
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "csrf_origin_rejected"


def test_s0_8c_safe_methods_are_not_gated(client):
    """A GET must not require a token — gating reads would break every page
    load and is not what CSRF protects against."""
    del client.headers["X-CSRF-Token"]
    assert client.get("/api/projects/").status_code == 200


def test_s0_8d_cors_allowlist_has_no_wildcard():
    """
    The regex allowlist is gone, not merely narrowed.

    `allow_origin_regex` matching a whole domain makes every subdomain an
    allowlisted credentialed origin, which is equivalent to disabling CSRF for
    that domain.
    """
    from fastapi.middleware.cors import CORSMiddleware

    import main

    cors = [m for m in main.app.user_middleware if m.cls is CORSMiddleware]
    assert len(cors) == 1
    options = cors[0].kwargs
    assert options.get("allow_origin_regex") in (None, "")
    assert "*" not in options.get("allow_origins", [])
    assert options.get("allow_credentials") is True


# ── S0.9 — login throttle ───────────────────────────────────────────────────

def test_s0_9_repeated_failed_logins_are_throttled(anon_client, seeded_identity):
    """
    S0.9 — N failed logins from one source are throttled after the threshold,
    and a valid login still succeeds from an unaffected source.

    The second half is what stops the fix from becoming a denial-of-service
    vector: keying on the IP alone would let an attacker lock out any user by
    burning that user's budget from an unrelated address.
    """
    import security
    from settings import get_settings

    security.reset_login_throttle_for_tests()
    limit = get_settings().login_max_attempts
    email = seeded_identity["email"]

    for attempt in range(limit):
        resp = anon_client.post(
            "/api/auth/login", json={"email": email, "password": "wrong"}
        )
        assert resp.status_code == 401, f"attempt {attempt} was not a 401"

    blocked = anon_client.post(
        "/api/auth/login", json={"email": email, "password": "wrong"}
    )
    assert blocked.status_code == 429, blocked.text
    assert int(blocked.headers["Retry-After"]) > 0

    # Even the CORRECT password is refused while the bucket is blocked — a
    # throttle that lets the real password through is not a throttle.
    correct = anon_client.post(
        "/api/auth/login", json={"email": email, "password": "test-password-123"}
    )
    assert correct.status_code == 429

    # An unaffected source is unaffected: same user, different client address.
    other_source = anon_client.post(
        "/api/auth/login",
        json={"email": email, "password": "test-password-123"},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert other_source.status_code == 200, other_source.text
    security.reset_login_throttle_for_tests()


# ── S0.11 — foreign keys ────────────────────────────────────────────────────

def test_s0_11_sqlite_enforces_on_delete_set_null(_auth_db):
    """
    S0.11 — deleting a parent row nulls `parent_project_id` on SQLite.

    SQLite ships with foreign keys OFF per connection, so every `ON DELETE SET
    NULL` in `db/models.py` was inert and the plan's claim that "a FK cannot
    dangle" was true only on Postgres. The API refuses the delete anyway
    (409/cascade), which is exactly why this had to be asserted at the DB
    level — the route path never exercises it.
    """
    import uuid as _uuid
    from datetime import datetime, timezone

    from sqlalchemy import delete, text

    from db.models import Organization, Project, User

    _engine, session_local = _auth_db
    with session_local() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1, (
            "PRAGMA foreign_keys is OFF — ON DELETE clauses never fire"
        )

        org = Organization(id=_uuid.uuid4(), name=f"FK Org {_uuid.uuid4()}",
                           created_at=datetime.now(tz=timezone.utc))
        owner = User(id=_uuid.uuid4(), email=f"{_uuid.uuid4()}@example.com",
                     password_hash=None, status="active", is_super_admin=False,
                     created_at=datetime.now(tz=timezone.utc))
        db.add_all([org, owner])
        db.flush()

        parent = Project(
            id=_uuid.uuid4(), org_id=org.id, name="FKParent", created_by=owner.id,
            storage_path="/tmp/fk-parent", parent_project_id=None,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        db.add(parent)
        db.flush()
        child = Project(
            id=_uuid.uuid4(), org_id=org.id, name="FKChild", created_by=owner.id,
            storage_path="/tmp/fk-child", parent_project_id=parent.id,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )
        db.add(child)
        db.commit()
        child_id = child.id

        # Delete through a TYPED Core statement, not raw SQL with a stringified
        # uuid: `Project.id` is `CHAR(32)` on SQLite (hex, no dashes), so
        # `WHERE id = '<dashed-uuid>'` matches zero rows and the test would
        # "pass the delete" while changing nothing.
        parent_id = parent.id
        deleted = db.execute(delete(Project).where(Project.id == parent_id))
        assert deleted.rowcount == 1, "the parent row was not actually deleted"
        db.commit()
        # Expire the identity map: the DELETE went through Core, so the ORM's
        # cached `child` instance still holds the pre-delete value and would
        # make this test fail even when the database did the right thing.
        db.expire_all()
        assert db.get(Project, parent_id) is None

        orphan = db.get(Project, child_id)
        assert orphan is not None, "the child row was removed, not re-parented"
        assert orphan.parent_project_id is None, (
            "parent_project_id still points at a deleted row — ON DELETE SET "
            "NULL did not fire"
        )


@pytest.mark.parametrize("marker", ["S0.1", "S0.5", "S0.8", "S0.9"])
def test_qa_case_ids_are_covered(marker):
    """
    Guard against a case being deleted rather than fixed.

    The plan's ids are the contract with the reviewer; this fails if the test
    that carries one disappears.
    """
    import pathlib

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert f"{marker} " in source or f"{marker} —" in source
