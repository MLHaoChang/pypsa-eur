"""
Auth-mode (multi-user tenancy) integration tests for the projects router.

These exercise the DB-registry + tree-aware ACL + org-scoped storage wiring
added in Task 7. They run with ``PYPSA_GUI_AUTH_ENABLED=true`` and a temp
``PROJECTS_ROOT`` so nothing touches the operator's real projects. The legacy
(auth-disabled) project tests continue to pass unchanged because the router
only takes the DB path when auth is enabled AND a user is resolved.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime, timezone

import pandas as pd
import pypsa
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import main
from db import session as db_session_module
from db.models import Organization, OrgMembership, Project, ProjectMembership, User
from services.auth_service import hash_password
from services import project_registry
from services.storage_paths import storage_path_for
from settings import get_settings

pytestmark = pytest.mark.auth_smoke


# ── Environment / session fixtures ───────────────────────────────────────────

@pytest.fixture
def tenancy_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:5173")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def session_local(db_engine, monkeypatch, tenancy_env):
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(db_session_module, "SessionLocal", testing_session_local)
    return testing_session_local


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── DB seeding helpers ───────────────────────────────────────────────────────

def _create_org(session_local, *, name: str | None = None) -> Organization:
    with session_local() as db:
        org = Organization(name=name or f"Org {uuid.uuid4()}", created_at=_now())
        db.add(org)
        db.commit()
        db.refresh(org)
        return org


def _create_user(session_local, *, email: str | None = None, is_super_admin: bool = False) -> User:
    with session_local() as db:
        user = User(
            email=email or f"{uuid.uuid4()}@example.com",
            password_hash=hash_password("secret-pass"),
            status="active",
            is_super_admin=is_super_admin,
            created_at=_now(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _add_membership(session_local, *, user_id, org_id, role: str) -> None:
    with session_local() as db:
        db.add(OrgMembership(user_id=user_id, org_id=org_id, role=role))
        db.commit()


def _seed_network(directory) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=10.0)
    n.export_to_netcdf(str(directory / "network.nc"))


def _create_project(
    session_local,
    *,
    org,
    creator,
    name,
    parent=None,
    seed_disk=True,
) -> Project:
    project_id = uuid.uuid4()
    # Phase 1b: `storage_path_for` returns a path RELATIVE to `projects_root`,
    # so the row stores the relative form and anything that writes must rejoin
    # it with the root first — otherwise `_seed_network` lands in
    # `pypsa-gui/backend/<org>/…`, inside the checkout (pixi runs the suite
    # with that cwd) and outside `.gitignore`'s reach.
    #
    # `taken=set()` is sound here and only here: `UniqueConstraint(org_id,
    # name)` already makes every project in one org distinctly named, and no
    # two names in this module sanitise alike.
    relative = storage_path_for(
        org.id, project_id, name, taken=set(), org_segment=True
    )
    storage_path = get_settings().projects_root / relative
    if seed_disk:
        _seed_network(storage_path)
    with session_local() as db:
        project = Project(
            id=project_id,
            org_id=org.id,
            name=name,
            created_by=creator.id,
            storage_path=str(relative),
            parent_project_id=parent.id if parent is not None else None,
            scenario_description=None,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return project


def _assign(session_local, *, project, user, assigned_by) -> None:
    with session_local() as db:
        db.add(
            ProjectMembership(
                project_id=project.id,
                user_id=user.id,
                assigned_by=assigned_by.id,
                assigned_at=_now(),
            )
        )
        db.commit()


@contextlib.contextmanager
def _client_for(email: str):
    with TestClient(main.app) as client:
        resp = client.post("/api/auth/login", json={"email": email, "password": "secret-pass"})
        assert resp.status_code == 200, resp.text
        # Step 0a: state-changing routes require the double-submit CSRF
        # token. The login response carries it in the body precisely so a
        # non-browser client can echo it back without parsing cookies.
        client.headers["X-CSRF-Token"] = resp.json()["csrf_token"]
        yield client


# ── Tests ────────────────────────────────────────────────────────────────────

def test_user_b_cannot_list_user_a_project(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    with _client_for("a@example.com") as client_a:
        names_a = {p["name"] for p in client_a.get("/api/projects/").json()}
        ids_a = {p["id"] for p in client_a.get("/api/projects/").json()}
    with _client_for("b@example.com") as client_b:
        names_b = {p["name"] for p in client_b.get("/api/projects/").json()}

    assert project_a.name in names_a
    assert str(project_a.id) in ids_a
    assert project_a.name not in names_b


def test_user_b_cannot_load_or_activate_user_a_project(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    with _client_for("b@example.com") as client_b:
        assert client_b.get(f"/api/projects/{project_a.id}").status_code == 404
        assert client_b.post(f"/api/projects/{project_a.id}/activate").status_code == 404
        assert client_b.get(f"/api/projects/{project_a.id}/bundle").status_code == 404

    with _client_for("a@example.com") as client_a:
        assert client_a.get(f"/api/projects/{project_a.id}").status_code == 200
        assert client_a.post(f"/api/projects/{project_a.id}/activate").status_code == 200


def test_create_scenario_inherits_tree_access(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")
    _assign(session_local, project=root, user=member, assigned_by=admin)

    with _client_for("member@example.com") as client_member:
        resp = client_member.post(
            f"/api/projects/{root.id}/scenarios",
            json={"name": "S1", "description": "x", "scenario_type": "scenario"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["parent_project"] == root.name
        assert body["id"] is not None and body["id"] != str(root.id)
        # The category is its own field now. It used to be a `[type]` prefix on
        # the description, which this test asserted verbatim.
        assert body["scenario_description"] == "x"
        assert body["scenario_type"] == "scenario"

        # And the retired encoding is REFUSED rather than stored, so the two
        # channels can never disagree about one project's category.
        legacy = client_member.post(
            f"/api/projects/{root.id}/scenarios",
            json={"name": "S_legacy", "description": "[scenario] x"},
        )
        assert legacy.status_code == 400, legacy.text
        assert "scenario_type" in legacy.json()["detail"]

        # Member has tree-inherited access to the freshly-created child.
        assert client_member.get(f"/api/projects/{body['id']}").status_code == 200
        assert client_member.post(f"/api/projects/{body['id']}/activate").status_code == 200
        assert client_member.get(f"/api/projects/{body['id']}/bundle").status_code in (200, 404)

    # The child row carries the DB parent pointer.
    with session_local() as db:
        child = db.get(Project, uuid.UUID(body["id"]))
        assert child is not None
        assert child.parent_project_id == root.id
        assert child.org_id == org.id


# ── PATCH /{name}/scenario ───────────────────────────────────────────────────
# Category and description were write-once until this route: a mistyped
# description, or a scenario that turned out to be the baseline, could only be
# corrected by branching a replacement and deleting the original.

def _seed_admin_root(session_local, *, name="Root"):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    return org, admin, _create_project(session_local, org=org, creator=admin, name=name)


def test_scenario_metadata_is_editable_after_creation(session_local):
    _org, _admin, root = _seed_admin_root(session_local)
    with _client_for("admin@example.com") as client:
        resp = client.patch(
            f"/api/projects/{root.id}/scenario",
            json={"scenario_type": "baseline", "description": "the reference run"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["scenario_type"] == "baseline"
        assert resp.json()["scenario_description"] == "the reference run"
        # And it is durable, not just echoed back.
        listed = client.get("/api/projects/").json()
        row = next(r for r in listed if r["id"] == str(root.id))
        assert row["scenario_type"] == "baseline"


def test_a_root_project_may_carry_a_category(session_local):
    # A root is very often the baseline. Restricting the field to branches
    # would be arbitrary — the category describes a project's role in a study,
    # and roots have one.
    _org, _admin, root = _seed_admin_root(session_local)
    with _client_for("admin@example.com") as client:
        resp = client.patch(
            f"/api/projects/{root.id}/scenario", json={"scenario_type": "baseline"}
        )
    assert resp.status_code == 200
    assert resp.json()["parent_project"] is None
    assert resp.json()["scenario_type"] == "baseline"


def test_patch_is_partial_so_changing_one_field_keeps_the_other(session_local):
    # The partial-PUT trap this codebase has hit repeatedly: a body carrying
    # only the category must not reset the description to a schema default.
    _org, _admin, root = _seed_admin_root(session_local)
    with _client_for("admin@example.com") as client:
        client.patch(
            f"/api/projects/{root.id}/scenario",
            json={"scenario_type": "scenario", "description": "keep me"},
        )
        resp = client.patch(
            f"/api/projects/{root.id}/scenario", json={"scenario_type": "stress"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scenario_type"] == "stress"
    assert resp.json()["scenario_description"] == "keep me"


def test_explicit_null_clears_a_field(session_local):
    # `null` is a real value here — it is how the user empties the box. Only
    # an ABSENT key means "leave alone".
    _org, _admin, root = _seed_admin_root(session_local)
    with _client_for("admin@example.com") as client:
        client.patch(
            f"/api/projects/{root.id}/scenario",
            json={"scenario_type": "stress", "description": "temporary"},
        )
        resp = client.patch(
            f"/api/projects/{root.id}/scenario",
            json={"description": None, "scenario_type": None},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scenario_description"] is None
    assert resp.json()["scenario_type"] is None


def test_patch_rejects_an_unknown_category(session_local):
    _org, _admin, root = _seed_admin_root(session_local)
    with _client_for("admin@example.com") as client:
        resp = client.patch(
            f"/api/projects/{root.id}/scenario", json={"scenario_type": "sensitivity"}
        )
    assert resp.status_code == 400
    assert "baseline" in resp.json()["detail"]


def test_patch_rejects_a_description_still_carrying_the_retired_prefix(session_local):
    _org, _admin, root = _seed_admin_root(session_local)
    with _client_for("admin@example.com") as client:
        resp = client.patch(
            f"/api/projects/{root.id}/scenario", json={"description": "[stress] winter"}
        )
    assert resp.status_code == 400
    assert "scenario_type='stress'" in resp.json()["detail"]


def test_patch_mirrors_into_metadata_json_for_bundle_export(session_local):
    import json

    _org, _admin, root = _seed_admin_root(session_local)
    with _client_for("admin@example.com") as client:
        resp = client.post(
            f"/api/projects/{root.id}/scenarios",
            json={"name": "Branch", "description": "d", "scenario_type": "scenario"},
        )
        child_id = resp.json()["id"]
        client.patch(
            f"/api/projects/{child_id}/scenario",
            json={"scenario_type": "stress", "description": "edited"},
        )

    with session_local() as db:
        child = db.get(Project, uuid.UUID(child_id))
    meta = json.loads((project_registry.project_dir(child) / "metadata.json").read_text())
    assert meta["scenario_type"] == "stress"
    assert meta["scenario_description"] == "edited"


def test_patch_refuses_a_project_the_caller_may_not_edit(session_local):
    # Same permission as rename/delete: relabelling a project in a shared
    # workspace is the same class of act.
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    outsider = _create_user(session_local, email="outsider@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    other_org = _create_org(session_local, name="Other")
    _add_membership(session_local, user_id=outsider.id, org_id=other_org.id, role="admin")
    root = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("outsider@example.com") as client:
        resp = client.patch(
            f"/api/projects/{root.id}/scenario", json={"scenario_type": "stress"}
        )
    # 404, not 403: the check runs before the caller has proved read access, so
    # answering 403 would confirm the project exists.
    assert resp.status_code == 404


def test_an_empty_patch_body_is_a_no_op(session_local):
    _org, _admin, root = _seed_admin_root(session_local)
    with _client_for("admin@example.com") as client:
        client.patch(
            f"/api/projects/{root.id}/scenario",
            json={"scenario_type": "stress", "description": "keep"},
        )
        resp = client.patch(f"/api/projects/{root.id}/scenario", json={})
    assert resp.status_code == 200
    assert resp.json()["scenario_type"] == "stress"
    assert resp.json()["scenario_description"] == "keep"


# ── The seams: a category must survive leaving and re-entering the app ───────
# QA found the feature exported the field correctly and then threw it away on
# the way back in. `_project_info_db` serves the DB ROW and overrides the
# storage dir, so a row created without the category makes the correct value
# in the restored metadata.json unreachable rather than merely redundant.

def test_a_bundle_round_trip_keeps_the_category(session_local):
    import io
    import zipfile

    _org, _admin, root = _seed_admin_root(session_local, name="Exported")
    with _client_for("admin@example.com") as client:
        # Branch a child rather than patching the fixture root: the scenario
        # path writes a real metadata.json, which is what a bundle carries.
        # (A project that has never been saved has no sidecar, so PATCH has
        # nowhere to mirror to — see the warning it logs for that case.)
        child_id = client.post(
            f"/api/projects/{root.id}/scenarios",
            json={"name": "Branch", "description": "cold winter", "scenario_type": "stress"},
        ).json()["id"]

        bundle = client.get(f"/api/projects/{child_id}/bundle")
        assert bundle.status_code == 200, bundle.text

        # The bundle must carry it…
        meta = json.loads(
            zipfile.ZipFile(io.BytesIO(bundle.content)).read("metadata.json")
        )
        assert meta["scenario_type"] == "stress"
        assert meta["scenario_description"] == "cold winter"

        # …and the import must put it on the ROW, not only back on disk.
        # `_project_info_db` serves the row and overrides the storage dir, so
        # a row created without the category makes the restored value
        # unreachable rather than merely redundant.
        restored = client.post(
            "/api/projects/import_bundle",
            files={"file": ("Branch.pypsaproj.zip", bundle.content, "application/zip")},
            params={"name": "Reimported"},
        )
        assert restored.status_code in (200, 201), restored.text

        row = next(
            r for r in client.get("/api/projects/").json() if r["name"] == "Reimported"
        )
        assert row["scenario_type"] == "stress"
        assert row["scenario_description"] == "cold winter"


def test_an_old_bundle_is_split_on_import_not_stored_tagged(session_local, tmp_path):
    # A bundle exported before migration 0004 carries the category as a
    # `[type]` prefix inline. Importing it verbatim recreates exactly the
    # state 0004 exists to delete: an uncategorised row whose description
    # renders the marker as prose.
    import io
    import zipfile

    _org, _admin, _root = _seed_admin_root(session_local, name="Host")
    seed = tmp_path / "oldbundle"
    _seed_network(seed)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("network.nc", (seed / "network.nc").read_bytes())
        zf.writestr("metadata.json", json.dumps({
            "name": "OldBundle",
            "scenario_description": "[stress] cold winter",
        }))

    with _client_for("admin@example.com") as client:
        resp = client.post(
            "/api/projects/import_bundle",
            files={"file": ("OldBundle.pypsaproj.zip", buf.getvalue(), "application/zip")},
        )
        assert resp.status_code in (200, 201), resp.text
        rows = {r["name"]: r for r in client.get("/api/projects/").json()}

    assert "OldBundle" in rows
    assert rows["OldBundle"]["scenario_type"] == "stress"
    assert rows["OldBundle"]["scenario_description"] == "cold winter"


def test_scenario_child_metadata_parent_name_in_sync(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    root = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("admin@example.com") as client:
        resp = client.post(
            f"/api/projects/{root.id}/scenarios",
            json={"name": "Branch", "description": "d"},
        )
        assert resp.status_code == 201, resp.text
        child_id = resp.json()["id"]

    # metadata.json on the child's storage dir points at the base NAME.
    import json
    with session_local() as db:
        child = db.get(Project, uuid.UUID(child_id))
    meta = json.loads((project_registry.project_dir(child) / "metadata.json").read_text())
    assert meta["parent_project"] == "Root"


def test_member_without_assignment_cannot_see_root(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("member@example.com") as client_member:
        names = {p["name"] for p in client_member.get("/api/projects/").json()}
        assert root.name not in names
        assert client_member.get(f"/api/projects/{root.id}").status_code == 404


def test_delete_permission_enforced(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")
    _assign(session_local, project=root, user=member, assigned_by=admin)

    # Member can access but cannot delete a root they didn't create.
    with _client_for("member@example.com") as client_member:
        assert client_member.delete(f"/api/projects/{root.id}").status_code == 403

    with _client_for("admin@example.com") as client_admin:
        assert client_admin.delete(f"/api/projects/{root.id}").status_code == 200

    with session_local() as db:
        assert db.get(Project, root.id) is None


def test_rename_updates_registry(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    root = _create_project(session_local, org=org, creator=admin, name="OldName")

    with _client_for("admin@example.com") as client:
        resp = client.post(f"/api/projects/{root.id}/rename", json={"new_name": "NewName"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "NewName"
        assert resp.json()["id"] == str(root.id)

    with session_local() as db:
        assert db.get(Project, root.id).name == "NewName"


def test_members_get_and_put(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("admin@example.com") as client:
        assert client.get(f"/api/projects/{root.id}/members").json() == []
        resp = client.put(
            f"/api/projects/{root.id}/members",
            json={"user_ids": [str(member.id)]},
        )
        assert resp.status_code == 200, resp.text
        emails = {m["email"] for m in resp.json()}
        assert member.email in emails

    # Now the assigned member can see the root in their list.
    with _client_for("member@example.com") as client_member:
        names = {p["name"] for p in client_member.get("/api/projects/").json()}
        assert root.name in names


def test_member_cannot_manage_members(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    member = _create_user(session_local, email="member@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=member.id, org_id=org.id, role="member")
    root = _create_project(session_local, org=org, creator=admin, name="Root")
    _assign(session_local, project=root, user=member, assigned_by=admin)

    with _client_for("member@example.com") as client_member:
        resp = client_member.put(
            f"/api/projects/{root.id}/members",
            json={"user_ids": [str(member.id)]},
        )
        assert resp.status_code == 403


# ── Read-only sub-resource ACL gating (Task 7 review — must-fix #1) ───────────
# layout / statistics / results_bundle used to resolve a project via the flat
# `_safe_project_dir(name)` alone, bypassing the org-scoped registry + ACL. In
# auth mode they must now 404 for another org/user's project and succeed for an
# authorized one — the same resolution as every other project-scoped route.


def test_layout_get_isolation(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    # Another org's user cannot read the layout — 404 (existence not leaked).
    with _client_for("b@example.com") as client_b:
        assert client_b.get(f"/api/projects/{project_a.id}/layout").status_code == 404

    # The owner reads the layout fine — empty ({}) since none was saved.
    with _client_for("a@example.com") as client_a:
        resp = client_a.get(f"/api/projects/{project_a.id}/layout")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {}


def test_layout_put_then_get_roundtrip_for_owner(session_local):
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=admin, name="Root")

    with _client_for("admin@example.com") as client:
        put = client.put(
            f"/api/projects/{project.id}/layout", json={"nodes": {"B1": [1, 2]}}
        )
        assert put.status_code == 200, put.text
        got = client.get(f"/api/projects/{project.id}/layout")
        assert got.status_code == 200
        assert got.json() == {"nodes": {"B1": [1, 2]}}

    # The layout landed in the org-scoped storage dir, not a flat path.
    layout_file = project_registry.project_dir(project) / "layout.json"
    assert layout_file.exists()


def test_layout_put_isolation(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    with _client_for("b@example.com") as client_b:
        resp = client_b.put(
            f"/api/projects/{project_a.id}/layout", json={"nodes": {}}
        )
        assert resp.status_code == 404


def test_statistics_isolation(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    # Another org's user cannot read statistics — 404.
    with _client_for("b@example.com") as client_b:
        assert client_b.get(f"/api/projects/{project_a.id}/statistics").status_code == 404

    # Owner gets real statistics from the seeded network (1 bus, 3 snapshots).
    with _client_for("a@example.com") as client_a:
        resp = client_a.get(f"/api/projects/{project_a.id}/statistics")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["buses"] == 1
        assert body["snapshots"] == 3
        assert body["name"] == "Alpha"


def test_results_bundle_isolation(session_local):
    org_a = _create_org(session_local, name="Org A")
    org_b = _create_org(session_local, name="Org B")
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org_a.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org_b.id, role="admin")
    project_a = _create_project(session_local, org=org_a, creator=user_a, name="Alpha")

    # Another org's user cannot read the results bundle — 404, not a leak.
    with _client_for("b@example.com") as client_b:
        assert client_b.get(f"/api/projects/{project_a.id}/results_bundle").status_code == 404

    # Owner is authorized; the seeded network was never solved so the bundle is
    # empty → 204 (authorization succeeded, there's just no dispatch to return).
    with _client_for("a@example.com") as client_a:
        assert client_a.get(f"/api/projects/{project_a.id}/results_bundle").status_code == 204


# ── UUID-shaped names (Task 7 review — must-fix #3) ───────────────────────────


def test_uuid_shaped_name_resolves_by_name(session_local):
    """A project *named* like a UUID (that is NOT any project's id) must still
    resolve by name — the resolver falls back to a name lookup when the UUID
    parse succeeds but matches no id in the org."""
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")

    uuid_name = str(uuid.uuid4())  # a valid UUID string used as the NAME
    project = _create_project(session_local, org=org, creator=admin, name=uuid_name)
    assert str(project.id) != uuid_name  # distinct from its own id

    with _client_for("admin@example.com") as client:
        # Addressed by the UUID-shaped NAME (no row has this as an id).
        assert client.get(f"/api/projects/{uuid_name}").status_code == 200
        assert client.get(f"/api/projects/{uuid_name}/statistics").status_code == 200
        # The real id still resolves too.
        assert client.get(f"/api/projects/{project.id}").status_code == 200


def test_find_project_uuid_fallback_unit(session_local):
    """Unit-level: find_project returns the row when addressed by a UUID-shaped
    name with no matching id, and None for a genuinely absent UUID."""
    from services import project_registry

    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")

    uuid_name = str(uuid.uuid4())
    project = _create_project(session_local, org=org, creator=admin, name=uuid_name)

    with session_local() as db:
        db_user = db.get(User, admin.id)
        # UUID-shaped name, no id match → falls back to name lookup.
        found = project_registry.find_project(db, db_user, uuid_name)
        assert found is not None
        assert found.id == project.id
        # A UUID that matches neither an id nor a name → None.
        assert project_registry.find_project(db, db_user, str(uuid.uuid4())) is None
        # The real id resolves directly.
        assert project_registry.find_project(db, db_user, str(project.id)).id == project.id


# ── Save-As copies uploads from the auth storage path (review — must-fix #2) ──


def test_save_as_copies_uploads_from_auth_storage(session_local):
    """Save-As (save the active project under a new name) must copy the
    `uploads/` dir from the SOURCE project's org-scoped storage_path into the
    new project's storage_path — not from a flat `_safe_project_dir(loaded)`."""
    org = _create_org(session_local, name="Org")
    admin = _create_user(session_local, email="admin@example.com")
    _add_membership(session_local, user_id=admin.id, org_id=org.id, role="admin")
    source = _create_project(session_local, org=org, creator=admin, name="Source")

    # Seed an upload under the SOURCE project's org-scoped storage dir.
    uploads = project_registry.project_dir(source) / "uploads" / "file-1"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "ref.csv").write_text("a,b\n1,2\n")

    with _client_for("admin@example.com") as client:
        # Load the source so the active singleton is bound to "Source".
        assert client.get(f"/api/projects/{source.id}").status_code == 200
        # Save-As to a brand-new name → create the root row + copy the bundle.
        resp = client.post("/api/projects/Copy", params={"rebind": "true"})
        assert resp.status_code == 200, resp.text

    # The new project's org-scoped storage dir received the uploads copy.
    with session_local() as db:
        copy_row = db.scalar(
            select(Project).where(Project.org_id == org.id, Project.name == "Copy")
        )
    assert copy_row is not None
    copied = project_registry.project_dir(copy_row) / "uploads" / "file-1" / "ref.csv"
    assert copied.exists(), "uploads/ was not copied from the source storage_path"
    assert copied.read_text() == "a,b\n1,2\n"


# ── Task 5: lock enforcement at project write edges ───────────────────────────
#
# This file has no `client_a`/`client_b`/`shared_project` fixtures, so the
# brief's sketches are adapted to the inline org/user/project construction
# style already used above and in `test_project_locks.py`'s own Task 4
# section — two org-admin users share one project (org-admin access means
# neither the rename/delete permission check nor the save ACL check shadows
# the lock check we're testing).


def _seed_two_user_project(session_local, *, name="Shared"):
    org = _create_org(session_local)
    user_a = _create_user(session_local, email="a@example.com")
    user_b = _create_user(session_local, email="b@example.com")
    _add_membership(session_local, user_id=user_a.id, org_id=org.id, role="admin")
    _add_membership(session_local, user_id=user_b.id, org_id=org.id, role="admin")
    project = _create_project(session_local, org=org, creator=user_a, name=name)
    return org, user_a, user_b, project


def test_save_409s_when_other_user_holds_lock(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        r = client_b.post(f"/api/projects/{project.name}")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error_kind"] == "project_locked"


def test_rename_and_delete_409_under_foreign_lock(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200

        rename = client_b.post(
            f"/api/projects/{project.name}/rename", json={"new_name": "Taken"}
        )
        assert rename.status_code == 409, rename.text
        assert rename.json()["detail"]["error_kind"] == "project_locked"

        delete = client_b.delete(f"/api/projects/{project.name}")
        assert delete.status_code == 409, delete.text
        assert delete.json()["detail"]["error_kind"] == "project_locked"


def test_holder_still_saves_and_free_project_saves(session_local):
    _org, user_a, _user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a:
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        r = client_a.post(f"/api/projects/{project.name}")
        assert r.status_code in (200, 409), r.text
        # 409 is only acceptable if some OTHER save gate (e.g. empty-network
        # guard) fired — the lock itself must never be the reason the holder's
        # own save is refused.
        if r.status_code == 409:
            assert r.json()["detail"].get("error_kind") != "project_locked"


def test_enqueue_409s_when_other_user_holds_lock(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        r = client_b.post("/api/simulation/queue", json={"project_id": str(project.id)})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error_kind"] == "project_locked"


# `routers/uploads.py` write edges (`POST`/`DELETE .../uploads`) write into
# `project.directory` the same as save/rename/delete, but were missed by the
# sweep that added `_enforce_project_lock` to this router family — neither
# handler called it, and the write middleware's `_FOREIGN_LOCK_GATE_PREFIXES`
# doesn't cover `/api/projects/` either (that prefix is exempt there by
# design, since it IS covered by the route-edge calls — except these two
# weren't). This is a consistency gap, not an authz hole: `ProjectAccessDep`
# already ACL-gates the project inside the caller's org before either handler
# runs; what was missing is refusing a non-holder's write while someone else
# holds the edit lock.

def test_upload_post_409s_when_other_user_holds_lock(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        r = client_b.post(
            f"/api/projects/{project.id}/uploads",
            files={"file": ("intruder.csv", b"a,b\n1,2\n", "text/csv")},
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error_kind"] == "project_locked"


def test_upload_delete_409s_when_other_user_holds_lock(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        # Seed a file as the (soon-to-be) holder, BEFORE locking, so there is
        # something real for the non-holder to try to delete.
        upload = client_a.post(
            f"/api/projects/{project.id}/uploads",
            files={"file": ("ref.csv", b"a,b\n1,2\n", "text/csv")},
        )
        assert upload.status_code == 200, upload.text
        file_id = upload.json()["file_id"]

        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200

        r = client_b.delete(f"/api/projects/{project.id}/uploads/{file_id}")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error_kind"] == "project_locked"

        # And it genuinely wasn't deleted — the intruder's refused DELETE
        # didn't quietly go through underneath the 409.
        listed = client_a.get(f"/api/projects/{project.id}/uploads").json()
        assert any(u["file_id"] == file_id for u in listed)


def test_upload_holder_can_still_post_and_delete_under_own_lock(session_local):
    # Regression guard: the lock check must gate the NON-holder, not everyone.
    _org, user_a, _user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a:
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200

        upload = client_a.post(
            f"/api/projects/{project.id}/uploads",
            files={"file": ("mine.csv", b"a,b\n1,2\n", "text/csv")},
        )
        assert upload.status_code == 200, upload.text
        file_id = upload.json()["file_id"]

        delete = client_a.delete(f"/api/projects/{project.id}/uploads/{file_id}")
        assert delete.status_code == 200, delete.text
        assert delete.json()["deleted"] is True


def test_upload_does_not_acquire_the_edit_lock(session_local):
    """
    The uploads edges are CHECK-ONLY (`_check_project_lock`), not
    acquire-on-write (`_enforce_project_lock`).

    This is the sibling assertion to the three 409 tests above: they pin what
    the gate must REFUSE, and nothing pinned what it must not DO. The first
    version of this gate used `_enforce_project_lock`, passed its own 79
    targeted tests, and still shipped a defect — an attachment upload claimed
    a 120 s edit lock that outlived the request, so two tests in other files
    failed in the full run while passing in isolation. The damage was
    cross-file, which is precisely what a targeted suite cannot see.

    Uploading a file is not an act of editing the project; it is incidental to
    one, like the canvas layout flush that `put_layout` deliberately keeps
    check-only. A passive caller must not be able to take an idle project's
    lock just by touching it.
    """
    from services import project_locks

    _org, _user_a, user_b, project = _seed_two_user_project(session_local)

    with session_local() as db:
        assert project_locks.get_lock(db, project.id) is None, "precondition: unlocked"

    with _client_for(user_b.email) as client_b:
        upload = client_b.post(
            f"/api/projects/{project.id}/uploads",
            files={"file": ("incidental.csv", b"a,b\n1,2\n", "text/csv")},
        )
        assert upload.status_code == 200, upload.text
        file_id = upload.json()["file_id"]

        with session_local() as db:
            assert project_locks.get_lock(db, project.id) is None, (
                "POST /uploads acquired the edit lock; it must only CHECK. "
                "A passive write left a live claim behind."
            )

        assert client_b.delete(
            f"/api/projects/{project.id}/uploads/{file_id}"
        ).status_code == 200

        with session_local() as db:
            assert project_locks.get_lock(db, project.id) is None, (
                "DELETE /uploads/{file_id} acquired the edit lock; check-only."
            )


# ── Task 6: foreign-lock gate in the write middleware ──────────────────────
#
# `_enforce_project_lock` (Task 4/5) covers the /api/projects/* write edges
# (save/rename/delete/scenario/layout/members/snapshots/enqueue). It does NOT
# cover /api/network/* or /api/io/* — those routes never resolve a `project`
# row, they mutate the resident `PyPSAService` singleton directly. Because the
# resident ProjectContext is shared per (org, project) (both users' sessions
# `activate` the SAME registry slot and get the SAME in-memory network), a
# non-holder's component write lands in the holder's memory and the holder's
# next autosave persists it. This is a middleware CHECK ONLY (get_lock +
# compare holder) — never an acquire; `_enforce_project_lock` still owns
# acquisition at the project write edges.
#
# No `client_a`/`client_b`/`shared_project` fixtures exist in this file (see
# the Task 5 section note above) — reuse `_seed_two_user_project` /
# `_client_for` and the real bus-create shape from
# `tests/test_line_lengths.py` (`POST /api/network/buses` with
# `{"name", "v_nom"}`).

def test_network_write_409s_when_active_project_lock_held_by_other(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        # Both users activate the same project; A holds the lock. Activation
        # is what binds the middleware's active-context project_uuid that the
        # gate reads — without it there's nothing for the gate to check.
        assert client_a.post(f"/api/projects/{project.id}/activate").status_code == 200
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        assert client_b.post(f"/api/projects/{project.id}/activate").status_code == 200

        r = client_b.post(
            "/api/network/buses", json={"name": "Intruder", "v_nom": 380.0}
        )
        assert r.status_code == 409, r.text
        assert r.json().get("code") == "project_locked"

        # The holder's own writes against the same shared context still pass.
        r = client_a.post(
            "/api/network/buses", json={"name": "Legit", "v_nom": 380.0}
        )
        assert r.status_code in (200, 201), r.text


def test_network_write_503s_when_lock_check_db_call_errors(session_local, monkeypatch):
    """
    F4: the gate's DB block (`with SessionLocal() as gate_db: get_lock(...)`)
    must fail CLOSED with 503.

    A DB error means the lock is UNKNOWN, not absent — the other pass-through
    branches (free/expired lock, unbound context, no auth_user) each KNOW there
    is nothing to guard, and this one does not. Letting the write through on a
    DB error hands a non-holder write into the holder's shared resident context
    precisely when the check that would have caught it broke. The auth block a
    few lines above in the same middleware already answers an unreachable DB
    with 503; this matches its posture.

    Not hypothetical: `get_lock` -> `_prune_expired` does a `db.delete` +
    `db.commit` on the expired-lock path, so two concurrent writes racing a
    lock's expiry can hit a SQLAlchemy `StaleDataError` from THIS exact call.
    Simulated here by monkeypatching `get_lock` to raise directly.

    The monkeypatch is applied only around the write itself, AFTER activate
    + lock: `project_locks.get_lock` is also called by
    `_serialize_project_lock` inside the activate/lock endpoints themselves
    (routers/projects.py), which have no fail-closed wrapper of their own and
    are not what this test is exercising — raising for the whole test would
    just 500 those setup calls instead.
    """
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/activate").status_code == 200
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        assert client_b.post(f"/api/projects/{project.id}/activate").status_code == 200

        def _raise(*_args, **_kwargs):
            raise RuntimeError("simulated DB error (e.g. StaleDataError on prune)")

        monkeypatch.setattr("services.project_locks.get_lock", _raise)

        r = client_b.post(
            "/api/network/buses", json={"name": "StillWorks", "v_nom": 380.0}
        )
        assert r.status_code == 503, r.text


# ── I1: one wire shape for every project_locked refusal ────────────────────
#
# Three emitters send this refusal — `_enforce_project_lock` (route edges),
# the write middleware, and the solve-queue enqueue check. The frontend reads
# `detail.lock` (`utils/projectActions._lockFromErrorDetail`) to fill the
# read-only banner, so a middleware 409 whose `detail` was a bare prose string
# left the banner with no holder to name. All three now carry
# `{error_kind, message, lock}`; the middleware keeps its top-level `code` for
# the existing quiet-toast keying.


def test_middleware_lock_409_carries_the_shared_detail_shape(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/activate").status_code == 200
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        assert client_b.post(f"/api/projects/{project.id}/activate").status_code == 200

        r = client_b.post(
            "/api/network/buses", json={"name": "Intruder", "v_nom": 380.0}
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["code"] == "project_locked"
        detail = body["detail"]
        assert detail["error_kind"] == "project_locked"
        assert isinstance(detail["message"], str) and detail["message"]
        assert detail["lock"] == {"holder_email": user_a.email, "yours": False}


def test_enqueue_lock_409_carries_the_serialized_lock(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        r = client_b.post("/api/simulation/queue", json={"project_id": str(project.id)})
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error_kind"] == "project_locked"
        assert detail["lock"] == {"holder_email": user_a.email, "yours": False}


# ── F2: the middleware gate also covers /api/simulation/* ──────────────────
#
# `/api/simulation/*` writes reach the resident network the same way
# `/api/network/*` does — solver_config edits, run, abort and force_reset all
# land in the shared `(org, project)` context and are persisted by the
# holder's next autosave. `POST /api/simulation/queue` is the one exception:
# it names its project in the BODY and runs its own lock check against THAT
# project, so testing the ACTIVE project here would refuse the wrong thing.


def test_simulation_write_409s_when_active_project_lock_held_by_other(session_local):
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/activate").status_code == 200
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        assert client_b.post(f"/api/projects/{project.id}/activate").status_code == 200

        r = client_b.put("/api/simulation/solver_config", json={"solver": "highs"})
        assert r.status_code == 409, r.text
        assert r.json().get("code") == "project_locked"

        # The holder is unaffected.
        assert client_a.put(
            "/api/simulation/solver_config", json={"solver": "highs"}
        ).status_code == 200


def test_enqueue_of_an_unlocked_project_passes_under_a_foreign_active_lock(
    session_local,
):
    """
    The `/api/simulation/queue` exemption is load-bearing, not cosmetic: the
    middleware would test the ACTIVE project's lock while the request is about
    a DIFFERENT project named in the body. Enqueueing an unlocked project must
    not be refused because the caller happens to be looking at a locked one.
    """
    org, user_a, user_b, project = _seed_two_user_project(session_local)
    other = _create_project(session_local, org=org, creator=user_b, name="Unlocked")

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/activate").status_code == 200
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        assert client_b.post(f"/api/projects/{project.id}/activate").status_code == 200

        r = client_b.post("/api/simulation/queue", json={"project_id": str(other.id)})
        assert r.status_code != 409, r.text


# ── N1: abort/clear_finished are job-scoped, not active-project-scoped ─────
#
# `POST /api/simulation/queue/{job_id}/abort` and
# `POST /api/simulation/queue/clear_finished` never resolve the session's
# ACTIVE project — abort is authorized against the JOB, clear_finished is
# cross-org/super-admin-gated. An exact-path exemption for bare
# `/api/simulation/queue` left these two 409ing merely because the caller's
# active project happened to be foreign-locked: refusing on the wrong
# project's lock, the exact failure the exemption exists to prevent.


def test_abort_is_not_refused_by_active_project_foreign_lock(session_local):
    """
    Pre-fix, the middleware's foreign-lock gate 409s BEFORE the router ever
    sees the job id (exact-path exemption only matched bare
    `/api/simulation/queue`). Post-fix, an unknown job id reaches the router
    and gets its own 404 `No solve job with id ...` — a perfectly good
    negative assertion since the point is that `project_locked` must not be
    the reason.
    """
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/activate").status_code == 200
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        assert client_b.post(f"/api/projects/{project.id}/activate").status_code == 200

        unknown_job_id = str(uuid.uuid4())
        r = client_b.post(f"/api/simulation/queue/{unknown_job_id}/abort")
        assert r.status_code != 409, r.text
        assert r.status_code == 404, r.text


def test_clear_finished_is_not_refused_by_active_project_foreign_lock(session_local):
    """
    Same reasoning as the abort test above: clear_finished is cross-org and
    super-admin-gated, never scoped to the caller's active project. `user_b`
    is not a super-admin here, so the real refusal is 403 — the assertion
    that matters is that it is NOT 409 `project_locked`.
    """
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/activate").status_code == 200
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        assert client_b.post(f"/api/projects/{project.id}/activate").status_code == 200

        r = client_b.post("/api/simulation/queue/clear_finished")
        assert r.status_code != 409, r.text


def test_preflight_is_not_refused_by_active_project_foreign_lock(session_local):
    """
    `POST /api/simulation/preflight` is a read-only diagnostic: it calls
    `validate_for_run(n, config)` and returns the issue list, taking no
    PyPSA lock and mutating nothing. But it is a POST under the gated
    `/api/simulation/` prefix, and the gate's `is_write` test keys on the
    HTTP verb, not on behaviour — so pre-fix, a non-holder's Validate button
    409s `project_locked`, and since that code is toast-suppressed on the
    frontend, the button silently does nothing.
    """
    _org, user_a, user_b, project = _seed_two_user_project(session_local)

    with _client_for(user_a.email) as client_a, _client_for(user_b.email) as client_b:
        assert client_a.post(f"/api/projects/{project.id}/activate").status_code == 200
        assert client_a.post(f"/api/projects/{project.id}/lock").status_code == 200
        assert client_b.post(f"/api/projects/{project.id}/activate").status_code == 200

        r = client_b.post("/api/simulation/preflight")
        if r.status_code == 409:
            assert r.json().get("detail", {}).get("error_kind") != "project_locked", r.text
        else:
            assert r.status_code != 409


def test_foreign_lock_gate_exempt_predicate_is_an_explicit_allowlist(monkeypatch):
    """
    N1 follow-up: the exemption must not silently widen to every path that
    happens to share the `/api/simulation/queue` prefix. A future sibling
    route acting on the active project (a hypothetical `purge_all`, or a
    per-job route beyond abort) must default to GATED, not exempt.

    Asserted directly against the predicate `main._foreign_lock_gate_exempt`
    since no such sibling route exists yet to exercise over HTTP.
    """
    exempt = main._foreign_lock_gate_exempt

    # The three real, deliberately-exempt routes.
    assert exempt("/api/simulation/queue") is True
    assert exempt("/api/simulation/queue/clear_finished") is True
    assert exempt("/api/simulation/queue/6c1f7e2a-6e6b-4c7e-9c1a-3f2b1a9d4e5f/abort") is True
    assert exempt("/api/simulation/preflight") is True

    # The queue negatives below pin the job-scoped family, but nothing yet
    # pins the NEW read-only-POST category against being widened into "POSTs
    # someone found inconvenient". `/api/simulation/run` and
    # `/api/simulation/run_ac_pf` are the canonical MUTATING POSTs under the
    # same prefix — both take the PyPSA lock and drive n.add/n.remove through
    # the LP/PF build — so they must stay gated. If either assertion below
    # ever fails, the read-only category has been abused.
    assert exempt("/api/simulation/run") is False
    assert exempt("/api/simulation/run_ac_pf") is False

    # Hypothetical siblings under the SAME prefix must stay gated by default.
    assert exempt("/api/simulation/queue/purge_all") is False
    assert exempt("/api/simulation/queue/6c1f7e2a-6e6b-4c7e-9c1a-3f2b1a9d4e5f/priority") is False

    # Non-canonical / crafted job-id segments on the abort route must NOT
    # ride the exemption — fail-gated is the safe direction (a stray 409,
    # never an accidental bypass).
    assert exempt("/api/simulation/queue/not-a-uuid/abort") is False
    assert exempt("/api/simulation/queue/../abort") is False

# ── N2: requeue writes to a project without checking who holds its lock ────
#
# A LIVE cross-user overwrite path, not a latent one. The foreign-lock
# middleware resolves the SESSION'S active project
# (`PyPSAService.get_active_context().project_uuid`) and refuses only when
# THAT project is foreign-locked. `requeue_job` resolves its target from the
# job's `project_key`, which is unrelated — so:
#
#   1. A holds the lock on project P.
#   2. B's active project is Q, unlocked. No special setup.
#   3. B requeues a job whose `project_key` is P.
#   4. The gate tests Q, finds it free, and passes the request through.
#   5. Requeue enqueues a solve that SAVES P, overwriting A's work.
#
# The gate fires only when B's own active project happens to be locked — a
# condition with nothing to do with the target — so it was never the
# protection it looked like. Requeue is authorized by `_may_see`, which its
# own docstring notes is deliberately WEAKER than the `_may_abort` used for
# stopping work, so the trigger set is wider than for aborting the same job.
#
# `enqueue_solve` has carried the matching check since Step 0a for exactly
# this reason; requeue is the second route that creates solve-and-save work
# and it shipped without it.


def test_requeue_refuses_when_the_jobs_own_project_is_foreign_locked(session_local):
    """
    The lock is held by `user_b` while `user_a` — the project's creator, so
    ACL access is not in question — does the requeuing, and `user_a`
    activates NOTHING.

    Activating nothing is the point rather than a shortcut: with no active
    project there is no session lock for the middleware to test, so it cannot
    be the source of a refusal. A 409 here can only have come from the route
    itself, which is the guarantee that makes this test still meaningful once
    the exemption lands and the middleware stops looking at this path at all.

    Ordering mirrors `enqueue_solve`: the lock check precedes the `network.nc`
    existence test, so a held lock is reported as `project_locked` rather than
    masked by a missing-network 404.
    """
    from services import project_locks
    from services.solve_queue import SolveJob, solve_queue

    org, user_a, user_b, project = _seed_two_user_project(session_local)

    solve_queue.reset_for_tests()
    try:
        jid = uuid.uuid4()
        with solve_queue._lock:
            job = SolveJob(
                id=jid,
                project_id=project.name,
                enqueued_at=0.0,
                project_key=f"{org.id}:{project.id}",
            )
            job.status = "completed"
            job.enqueued_by_user_id = user_a.id
            solve_queue._jobs[jid] = job
            solve_queue._order.append(jid)

        with session_local() as db:
            assert project_locks.acquire_lock(db, project.id, user_b.id) is not None

        with _client_for(user_a.email) as client_a:
            r = client_a.post(f"/api/simulation/queue/{jid}/requeue")
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["error_kind"] == "project_locked", r.text
            # I1 wire shape: the frontend reads `detail.lock` to name the
            # holder in its read-only banner. A refusal without it leaves the
            # banner saying "another user".
            assert r.json()["detail"].get("lock") is not None, r.text
    finally:
        solve_queue.reset_for_tests()
