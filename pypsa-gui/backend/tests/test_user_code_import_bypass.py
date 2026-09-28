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

Three vectors, all closed here:
  * ACTIVATION — the member's own session gets the code as live solver config.
  * PLANTING — the code stays on disk in a project an ADMIN may later open, at
    which point `load_project` loads it and a solve runs it with full privileges.
    Stripping only the in-memory copy would leave this one open.
  * DELEGATION — the importer is an ADMIN, and the code is not theirs. The first
    fix gated on `user_code_authorized(db, user)`, which let this one straight
    through: an admin who imports a colleague's .pypsaproj.zip was running that
    colleague's module body. Demonstrated, not inferred — with the old guard
    restored, the payload in the last test below writes its sentinel file inside
    the admin's session. So the strip is now unconditional.

Found by an independent QA review, 2026-09-12; the third vector by a follow-up
review of that fix. The lesson is about gate placement, twice over: a field is
only as gated as its least-guarded writer (the PUT was not the only writer), and
a privilege check is the wrong gate for a question about provenance.
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


def test_even_an_admin_bundle_is_stripped_and_the_response_says_so(
    client, api_project, project_storage_dir, monkeypatch,
):
    """
    The strip is UNCONDITIONAL, admins included, and the import reports it.

    The first version of the gate asked `user_code_authorized(db, user)` -- "is
    the importer privileged". That is the wrong question: the field carries code
    the importer did not write, and a zip cannot attest authorship. An org admin
    with the operator flag on is exactly the identity whose solve runs with the
    most privilege, so it is the worst one to hand a stranger's module body to.

    The workflow cost is real and is asserted here rather than hidden: an admin
    moving their own project by bundle gets the field back only by re-setting it
    through the gated PUT. `stripped` in the response is what makes that
    discoverable instead of mysterious.
    """
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    src = api_project("ok-src")
    nc = (project_storage_dir(src) / "network.nc").read_bytes()
    benign = "def extra_functionality(n, sns):\n    pass\n"

    r = client.post(
        "/api/projects/import_bundle", params={"name": "ok-strip"},
        files={"file": ("e.zip", _bundle_with_code(nc, "ok-strip", benign),
                        "application/zip")},
    )
    assert r.status_code in (200, 201), r.text[:300]
    assert "extra_functionality_code" in (r.json().get("stripped") or []), (
        "the field was dropped without telling the importer -- a silent "
        f"difference between what was uploaded and what was imported: {r.text[:300]}"
    )

    live = client.get("/api/simulation/solver_config").json()
    assert not (live.get("extra_functionality_code") or "").strip(), (
        "an admin's bundle still set exec()-able code as live solver config"
    )
    cfg_path = project_storage_dir("ok-strip") / "solver_config.json"
    if cfg_path.exists():
        on_disk = json.loads(cfg_path.read_text()).get("extra_functionality_code") or ""
        assert not on_disk.strip(), "left on disk for the next load_project"


def test_an_admin_importing_a_colleagues_bundle_gets_no_execution(
    client, api_project, project_storage_dir, monkeypatch, tmp_path,
):
    """
    The scenario the privileged-importer gate missed entirely.

    A member cannot set the field, so they send the admin a .pypsaproj.zip
    instead -- "here is my project, have a look". Under the old check the admin
    is authorized, so the code was kept, and `_compile_extra_functionality`
    exec()s the MODULE BODY: the payload here needs no call to
    `extra_functionality` at all, it fires the moment the config is compiled.

    Asserted by running the compile step itself, not just by reading the config:
    "not in the config" and "did not run" are different claims, and the second
    one is the one that matters. `_compile_extra_functionality` is exactly what
    `services.solver_service` reaches for at the top of a solve.
    """
    monkeypatch.setenv("PYPSA_GUI_ALLOW_USER_CODE", "1")
    sentinel = tmp_path / "colleague-payload-ran"
    payload = (
        "import pathlib\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('rce')\n"
        "def extra_functionality(n, sns):\n    pass\n"
    )
    src = api_project("colleague-src")
    nc = (project_storage_dir(src) / "network.nc").read_bytes()

    r = client.post(
        "/api/projects/import_bundle", params={"name": "colleague-proj"},
        files={"file": ("c.zip", _bundle_with_code(nc, "colleague-proj", payload),
                        "application/zip")},
    )
    assert r.status_code in (200, 201), r.text[:300]

    from services.solver_service import _compile_extra_functionality

    live = client.get("/api/simulation/solver_config").json()
    # Execution first, and deliberately before the config assertion: if the
    # config one ran first it would always trip first and this one could rot
    # unfalsifiable.
    _compile_extra_functionality(live.get("extra_functionality_code") or "")
    assert not sentinel.exists(), (
        "a colleague's module body executed inside the admin's session"
    )
    assert not (live.get("extra_functionality_code") or "").strip(), (
        "a colleague's code became the admin's live solver config"
    )
