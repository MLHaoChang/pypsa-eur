"""
A bundle import must not smuggle `extra_functionality_code` past the admin gate.

`routers/simulation._gate_user_code` guards `PUT /api/simulation/solver_config`,
and that route is NOT the only way the field gets set. `import_bundle` writes a
bundle's `solver_config.json` into the new project directory and loads it through
`_solver_config_from_dict`, which filters to the live `SolverConfig` field set —
and `extra_functionality_code` IS a live field. So a plain member refused with
403 on the PUT reached the identical capability by uploading a zip, verified
end-to-end: the code landed in `_state["solver_config"]`, was written to
`<org>/<uuid>/solver_config.json`, and `_compile_extra_functionality` executed it.

Two vectors, both closed here:
  * ACTIVATION — the member's own session gets the code as live solver config.
  * PLANTING — the code stays on disk in a project an ADMIN may later open, at
    which point `load_project` loads it and a solve runs it with full privileges.
    Stripping only the in-memory copy would leave this one open.

Found by an independent QA review, 2026-09-12. The lesson is about gate
placement: a field is only as gated as its least-guarded writer, and the PUT was
not the only writer.
"""
import io
import json
import uuid as _uuid
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from tests.conftest import attach_session


_EVIL = (
    "import pathlib\n"
    "pathlib.Path('/tmp/claude-0/should-not-exist').write_text('rce')\n"
    "def extra_functionality(n, sns):\n    pass\n"
)


def _client_with_role(auth_db, org_id, role):
    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = auth_db
    with session_local() as db:
        u = User(
            id=_uuid.uuid4(),
            email=f"u-{_uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(u)
        db.flush()
        db.add(OrgMembership(id=_uuid.uuid4(), user_id=u.id,
                             org_id=org_id, role=role))
        db.commit()
        uid = u.id
    c = TestClient(main.app)
    c.__enter__()
    return attach_session(c, session_local, uid), c


def _bundle_with_code(nc_bytes: bytes, name: str, code: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("network.nc", nc_bytes)
        zf.writestr("solver_config.json", json.dumps({
            "extra_functionality_code": code,
        }))
        zf.writestr("metadata.json", json.dumps({"name": name}))
    buf.seek(0)
    return buf.read()


@pytest.fixture
def member_client(_auth_db, seeded_identity):
    client, raw = _client_with_role(_auth_db, seeded_identity["org_id"], "member")
    yield client
    raw.__exit__(None, None, None)


def test_a_member_cannot_smuggle_user_code_through_a_bundle(
    member_client, api_project, project_storage_dir, monkeypatch,
):
    """The headline bypass: refused on the PUT, allowed through the zip."""
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    src = api_project("bypass-src")
    nc = (project_storage_dir(src) / "network.nc").read_bytes()

    r = member_client.post(
        "/api/projects/import_bundle", params={"name": "bypass-evil"},
        files={"file": ("e.zip", _bundle_with_code(nc, "bypass-evil", _EVIL),
                        "application/zip")},
    )
    assert r.status_code in (200, 201, 403), r.text[:300]

    live = member_client.get("/api/simulation/solver_config")
    assert live.status_code == 200, live.text
    got = (live.json().get("extra_functionality_code") or "").strip()
    assert not got, (
        f"a member's bundle set exec()-able code as live solver config: {got[:120]!r}"
    )


def test_the_smuggled_code_is_not_left_on_disk_for_an_admin_to_open(
    member_client, api_project, project_storage_dir, monkeypatch,
):
    """
    The planting vector. Stripping only the in-memory copy would leave the code
    in the project directory, and `load_project` loads it the moment an ADMIN
    opens that project — so the member escalates via someone else's session.
    """
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    src = api_project("plant-src")
    nc = (project_storage_dir(src) / "network.nc").read_bytes()

    r = member_client.post(
        "/api/projects/import_bundle", params={"name": "plant-evil"},
        files={"file": ("e.zip", _bundle_with_code(nc, "plant-evil", _EVIL),
                        "application/zip")},
    )
    if r.status_code == 403:
        return  # refused outright — nothing was written, nothing to plant.

    cfg_path = project_storage_dir("plant-evil") / "solver_config.json"
    if not cfg_path.exists():
        return
    on_disk = json.loads(cfg_path.read_text()).get("extra_functionality_code") or ""
    assert not on_disk.strip(), (
        "the member's code was left in the project directory, where load_project "
        f"will hand it to the next admin who opens the project: {on_disk[:120]!r}"
    )


def test_an_admin_bundle_keeps_its_code_when_the_operator_opted_in(
    client, api_project, project_storage_dir, monkeypatch,
):
    """
    The gate must not break the supported path: an org admin, with the operator
    flag on, may still carry `extra_functionality_code` in a bundle.
    """
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    src = api_project("ok-src")
    nc = (project_storage_dir(src) / "network.nc").read_bytes()
    benign = "def extra_functionality(n, sns):\n    pass\n"

    r = client.post(
        "/api/projects/import_bundle", params={"name": "ok-evil"},
        files={"file": ("e.zip", _bundle_with_code(nc, "ok-evil", benign),
                        "application/zip")},
    )
    assert r.status_code in (200, 201), r.text[:300]
    live = client.get("/api/simulation/solver_config").json()
    assert benign.strip() in (live.get("extra_functionality_code") or ""), (
        "an admin's own bundle lost its code — the gate over-reached"
    )
