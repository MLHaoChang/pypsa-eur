"""Bundle manifest and import dry run (project-io issue 02, GK B I7–I9)."""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import zipfile

import pytest

from services import bundle_manifest as BM
from services import project_contents as PC
from tests.test_project_contents import _bundle_from, _left_behind, _post, projects_root  # noqa: F401


def _zip(members: dict[str, bytes], manifest: dict | bytes | None = None) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if manifest is not None:
            raw = manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode()
            zf.writestr(PC.MANIFEST, raw)
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def _rec(data: bytes) -> dict:
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


_GOOD = {"network.nc": b"net", "uploads/a.csv": b"1,2"}


def _manifest(src: dict[str, bytes] = _GOOD, **over) -> dict:
    return {
        "schema_version": BM.SCHEMA_VERSION,
        "app_version": "x",
        "project_kind": None,
        "members": {n: _rec(d) for n, d in src.items()},
        **over,
    }


def _refusal(zf) -> PC.BundleRefused:
    with pytest.raises(PC.BundleRefused) as exc:
        BM.check(zf)
    return exc.value


# ── the manifest itself ─────────────────────────────────────────────────────


def test_a_built_manifest_lists_every_member_but_itself_and_checks_clean():
    data = BM.add_to(_zip(_GOOD).fp.getvalue(), project_kind="planning")
    zf = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(zf.read(PC.MANIFEST))
    assert manifest["schema_version"] == BM.SCHEMA_VERSION == 1
    assert manifest["app_version"] == BM.APP_VERSION
    assert manifest["project_kind"] == "planning"
    assert manifest["members"] == {n: _rec(d) for n, d in _GOOD.items()}
    assert BM.check(zf) == []


def test_no_manifest_imports_with_a_warning():
    assert BM.check(_zip(_GOOD)) == [BM.NO_MANIFEST_WARNING]


def test_a_directory_entry_is_not_a_member():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("uploads/", b"")
        for n, d in _GOOD.items():
            zf.writestr(n, d)
        zf.writestr(PC.MANIFEST, json.dumps(_manifest()))
    assert BM.check(zipfile.ZipFile(buf)) == []


@pytest.mark.parametrize(
    "members, manifest, says",
    [
        ({"network.nc": b"net"}, _manifest(), "'uploads/a.csv' is listed in its manifest but missing"),
        ({**_GOOD, "layout.json": b"{}"}, _manifest(), "'layout.json' is in the bundle but not in its manifest"),
        ({**_GOOD, "network.nc": b"nett"}, _manifest(), "'network.nc' has a different size"),
        ({**_GOOD, "network.nc": b"NET"}, _manifest(), "'network.nc' does not match its checksum"),
    ],
    ids=["missing", "extra", "size", "hash"],
)
def test_a_member_that_does_not_match_is_refused(members, manifest, says):
    exc = _refusal(_zip(members, manifest))
    assert exc.kind == "bundle_corrupt"
    assert says in exc.message
    assert "Nothing was imported" in exc.message


@pytest.mark.parametrize(
    "manifest, says",
    [
        (b"{not json", "manifest cannot be read"),
        (b"\xff\xfe", "manifest cannot be read"),
        (b"[1]", "manifest cannot be read"),
        (_manifest(schema_version=None), "no valid schema version"),
        (_manifest(schema_version="1"), "no valid schema version"),
        (_manifest(schema_version=True), "no valid schema version"),
        (_manifest(schema_version=0), "no valid schema version"),
        (_manifest(members=[]), "no list of files"),
        (_manifest(members={"network.nc": {"size": 3}}), "entry for 'network.nc'"),
        (_manifest(members={"network.nc": {"size": "3", "sha256": "0" * 64}}), "entry for 'network.nc'"),
        (_manifest(members={"network.nc": {"size": 3, "sha256": 1}}), "entry for 'network.nc'"),
        (_manifest(members={"network.nc": {"size": 3, "sha256": "0" * 63}}), "entry for 'network.nc'"),
        (_manifest(members={"network.nc": {"size": 3, "sha256": "0" * 64 + "\n"}}), "entry for 'network.nc'"),
        (_manifest(members={"network.nc": ["x"]}), "entry for 'network.nc'"),
    ],
)
def test_an_unreadable_manifest_is_refused(manifest, says):
    exc = _refusal(_zip(_GOOD, manifest))
    assert exc.kind == "bundle_corrupt"
    assert says in exc.message


def test_a_newer_schema_is_refused_with_update_the_app():
    exc = _refusal(_zip(_GOOD, _manifest(schema_version=BM.SCHEMA_VERSION + 1)))
    assert exc.kind == "bundle_too_new"
    assert "Update the app" in exc.message
    assert f"this app reads up to {BM.SCHEMA_VERSION}" in exc.message


def test_the_current_schema_is_accepted():
    assert BM.check(_zip(_GOOD, _manifest(schema_version=BM.SCHEMA_VERSION))) == []


def test_a_member_whose_data_is_damaged_is_refused_not_a_500():
    data = bytearray(BM.add_to(_zip({"network.nc": b"n" * 4000}).fp.getvalue(), project_kind=None))
    # Flip one byte inside network.nc's stored data: the zip's own CRC fails.
    off = zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo("network.nc").header_offset + 30 + len("network.nc")
    data[off + 5] ^= 0xFF
    exc = _refusal(zipfile.ZipFile(io.BytesIO(bytes(data))))
    assert exc.kind == "bundle_corrupt"
    assert "'network.nc' cannot be read" in exc.message


def test_the_member_screen_neither_allows_nor_ignores_the_manifest():
    allowed, ignored = PC.screen_members(_zip(_GOOD, _manifest()))
    assert PC.MANIFEST not in allowed and PC.MANIFEST not in ignored
    assert "network.nc" in allowed


# ── the route ───────────────────────────────────────────────────────────────


def _tree(root: pathlib.Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


@pytest.fixture
def row_count(_auth_db):
    from db.models import Project
    from sqlalchemy import func, select

    _engine, session_local = _auth_db

    def _count() -> int:
        with session_local() as db:
            return db.scalar(select(func.count()).select_from(Project))

    return _count


def _dry(client, data: bytes, name: str):
    return client.post(
        f"/api/projects/import_bundle?name={name}&dry_run=true",
        files={"file": ("p.pypsaproj.zip", data, "application/zip")},
    )


def test_an_export_carries_a_manifest_and_imports_without_a_warning(
    client, api_project, project_storage_dir
):
    name = api_project("bm-src")
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert PC.MANIFEST in zf.namelist()
    assert set(json.loads(zf.read(PC.MANIFEST))["members"]) == set(zf.namelist()) - {PC.MANIFEST}

    r = _post(client, r.content, "bm_dst")
    assert r.status_code in (200, 201), r.text
    assert "warnings" not in r.json()
    assert not (project_storage_dir("bm_dst") / PC.MANIFEST).exists()


def test_an_old_bundle_without_a_manifest_imports_with_the_warning(
    client, api_project, project_storage_dir
):
    d = project_storage_dir(api_project("bm-old"))
    r = _post(client, _bundle_from(d), "bm_old_dst")
    assert r.status_code in (200, 201), r.text
    assert r.json()["warnings"] == [BM.NO_MANIFEST_WARNING]


@pytest.mark.parametrize("how", ["changed", "added", "newer"])
def test_a_damaged_or_newer_bundle_is_refused_and_leaves_nothing(
    client, api_project, project_storage_dir, project_row, projects_root, how  # noqa: F811
):
    name = api_project(f"bm-bad-{how}")
    good = client.get(f"/api/projects/{name}/bundle").content
    src = zipfile.ZipFile(io.BytesIO(good))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "metadata.json" and how == "changed":
                data = data + b" "
            if info.filename == PC.MANIFEST and how == "newer":
                data = json.dumps({**json.loads(data), "schema_version": 99}).encode()
            zf.writestr(info.filename, data)
        if how == "added":
            zf.writestr("layout.json", b"{}")
    dst = f"bm_bad_{how}_dst"
    r = _post(client, buf.getvalue(), dst)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_kind"] == ("bundle_too_new" if how == "newer" else "bundle_corrupt")
    assert _left_behind(project_row, projects_root, dst) == []


def test_a_dry_run_writes_nothing_and_answers_what_the_import_does(
    client, api_project, project_storage_dir, project_row, projects_root, row_count  # noqa: F811
):
    d = project_storage_dir(api_project("bm-dry"))
    data = _bundle_from(
        d,
        {"layout.json": b"{}", "stray.txt": b"x",
         "solver_config.json": json.dumps({"extra_functionality_code": "print(1)"}).encode()},
    )
    rows, tree = row_count(), _tree(projects_root)

    r = _dry(client, data, "bm_dry_dst")
    assert r.status_code == 200, r.text
    dry = r.json()
    assert (row_count(), _tree(projects_root)) == (rows, tree)
    assert _left_behind(project_row, projects_root, "bm_dry_dst") == []
    assert dry == {
        "dry_run": True,
        "name": "bm_dry_dst",
        "project_kind": None,
        "members": ["layout.json", "metadata.json", "network.nc", "solver_config.json"],
        "would_conflict": False,
        "stripped": ["extra_functionality_code"],
        "ignored": ["stray.txt"],
        "rederived": [],
        "rederived_at_default_rate": False,
        "warnings": [BM.NO_MANIFEST_WARNING],
    }

    real = _post(client, data, "bm_dry_dst")
    assert real.status_code in (200, 201), real.text
    got = real.json()
    assert got["stripped"] == dry["stripped"]
    assert got["ignored"] == dry["ignored"]
    assert got["warnings"] == dry["warnings"]
    assert sorted(p.name for p in project_storage_dir("bm_dry_dst").iterdir()
                  if p.name in dry["members"]) == dry["members"]

    again = _dry(client, data, "bm_dry_dst")
    assert again.status_code == 200, again.text
    assert again.json()["would_conflict"] is True


def test_a_dry_run_refuses_what_the_import_refuses_and_leaves_nothing(
    client, api_project, project_storage_dir, project_row, projects_root, row_count  # noqa: F811
):
    d = project_storage_dir(api_project("bm-dry-bad"))
    rows, tree = row_count(), _tree(projects_root)
    r = _dry(client, _bundle_from(d, {"uploads/../../x": b"x"}), "bm_dry_bad_dst")
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_kind"] == "bundle_unsafe_path"
    r = _dry(client, _bundle_from(d, meta={"project_kind": "nope"}), "bm_dry_bad_dst")
    assert r.status_code == 422, r.text
    assert (row_count(), _tree(projects_root)) == (rows, tree)


def test_a_dry_run_cleans_its_staging_when_a_late_check_fails(
    client, api_project, project_storage_dir, projects_root, monkeypatch  # noqa: F811
):
    from routers import projects

    d = project_storage_dir(api_project("bm-dry-late"))

    def boom(*a, **k):
        raise RuntimeError("late")

    monkeypatch.setattr(projects, "_import_dry_run", boom)
    with pytest.raises(RuntimeError):
        _dry(client, _bundle_from(d), "bm_dry_late_dst")
    staging = projects_root / ".bundle-staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_a_dry_run_needs_a_signed_in_user(anon_client, client, api_project, project_storage_dir):
    d = project_storage_dir(api_project("bm-dry-anon"))
    data = _bundle_from(d)
    anon = _dry(anon_client, data, "bm_dry_anon_dst")
    real = _post(anon_client, data, "bm_dry_anon_dst")
    assert anon.status_code == real.status_code
    assert anon.status_code in (401, 403)
