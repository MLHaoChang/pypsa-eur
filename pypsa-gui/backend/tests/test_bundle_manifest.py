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
from tests.test_project_contents import _bundle_from, _left_behind, _post


@pytest.fixture
def projects_root():
    from settings import get_settings

    return pathlib.Path(get_settings().projects_root)


def _zip(
    members: dict[str, bytes], manifest: dict | bytes | None = None
) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if manifest is not None:
            raw = (
                manifest
                if isinstance(manifest, bytes)
                else json.dumps(manifest).encode()
            )
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
        (
            {"network.nc": b"net"},
            _manifest(),
            "'uploads/a.csv' is listed in its manifest but missing",
        ),
        (
            {**_GOOD, "layout.json": b"{}"},
            _manifest(),
            "'layout.json' is in the bundle but not in its manifest",
        ),
        (
            {**_GOOD, "network.nc": b"nett"},
            _manifest(),
            "'network.nc' has a different size",
        ),
        (
            {**_GOOD, "network.nc": b"NET"},
            _manifest(),
            "'network.nc' does not match its checksum",
        ),
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
        (
            _manifest(members={"network.nc": {"size": "3", "sha256": "0" * 64}}),
            "entry for 'network.nc'",
        ),
        (
            _manifest(members={"network.nc": {"size": 3, "sha256": 1}}),
            "entry for 'network.nc'",
        ),
        (
            _manifest(members={"network.nc": {"size": 3, "sha256": "0" * 63}}),
            "entry for 'network.nc'",
        ),
        (
            _manifest(members={"network.nc": {"size": 3, "sha256": "0" * 64 + "\n"}}),
            "entry for 'network.nc'",
        ),
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
    data = bytearray(
        BM.add_to(_zip({"network.nc": b"n" * 4000}).fp.getvalue(), project_kind=None)
    )
    # Flip one byte inside network.nc's stored data: the zip's own CRC fails.
    off = (
        zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo("network.nc").header_offset
        + 30
        + len("network.nc")
    )
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
    assert set(json.loads(zf.read(PC.MANIFEST))["members"]) == set(zf.namelist()) - {
        PC.MANIFEST
    }

    r = _post(client, r.content, "bm_dst")
    assert r.status_code == 200, r.text
    assert "warnings" not in r.json()
    assert not (project_storage_dir("bm_dst") / PC.MANIFEST).exists()


def test_an_old_bundle_without_a_manifest_imports_with_the_warning(
    client, api_project, project_storage_dir
):
    d = project_storage_dir(api_project("bm-old"))
    r = _post(client, _bundle_from(d), "bm_old_dst")
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == [BM.NO_MANIFEST_WARNING]


@pytest.mark.parametrize("how", ["changed", "added", "newer"])
def test_a_damaged_or_newer_bundle_is_refused_and_leaves_nothing(
    client,
    api_project,
    project_storage_dir,
    project_row,
    projects_root,
    how,
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
    assert r.json()["detail"]["error_kind"] == (
        "bundle_too_new" if how == "newer" else "bundle_corrupt"
    )
    assert _left_behind(project_row, projects_root, dst) == []


def test_a_dry_run_writes_nothing_and_answers_what_the_import_does(
    client,
    api_project,
    project_storage_dir,
    project_row,
    projects_root,
    row_count,
):
    d = project_storage_dir(api_project("bm-dry"))
    data = _bundle_from(
        d,
        {
            "layout.json": b"{}",
            "stray.txt": b"x",
            "solver_config.json": json.dumps(
                {"extra_functionality_code": "print(1)"}
            ).encode(),
        },
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
    assert real.status_code == 200, real.text
    got = real.json()
    assert got["stripped"] == dry["stripped"]
    assert got["ignored"] == dry["ignored"]
    assert got["warnings"] == dry["warnings"]
    assert (
        sorted(
            p.name
            for p in project_storage_dir("bm_dry_dst").iterdir()
            if p.name in dry["members"]
        )
        == dry["members"]
    )

    again = _dry(client, data, "bm_dry_dst")
    assert again.status_code == 200, again.text
    assert again.json()["would_conflict"] is True


def test_a_dry_run_refuses_what_the_import_refuses_and_leaves_nothing(
    client,
    api_project,
    project_storage_dir,
    project_row,
    projects_root,
    row_count,
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
    client,
    api_project,
    project_storage_dir,
    projects_root,
    monkeypatch,
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


def test_a_dry_run_needs_a_signed_in_user(
    anon_client, client, api_project, project_storage_dir
):
    d = project_storage_dir(api_project("bm-dry-anon"))
    data = _bundle_from(d)
    anon = _dry(anon_client, data, "bm_dry_anon_dst")
    real = _post(anon_client, data, "bm_dry_anon_dst")
    assert anon.status_code == real.status_code
    assert anon.status_code in (401, 403)


# ── GK B3 M1–M5 ─────────────────────────────────────────────────────────────


def test_an_oversized_manifest_is_refused_before_it_is_parsed():
    big = json.dumps(_manifest(pad="x" * BM.MAX_MANIFEST_BYTES)).encode()
    exc = _refusal(_zip(_GOOD, big))
    assert exc.kind == "bundle_corrupt"
    assert "too large" in exc.message


def test_a_manifest_at_the_cap_is_read():
    base = json.dumps(_manifest(pad="")).encode()
    exact = json.dumps(
        _manifest(pad="x" * (BM.MAX_MANIFEST_BYTES - len(base)))
    ).encode()
    assert len(exact) == BM.MAX_MANIFEST_BYTES
    assert BM.check(_zip(_GOOD, exact)) == []


def test_a_negative_size_is_refused():
    m = _manifest()
    m["members"]["network.nc"]["size"] = -1
    assert "entry for 'network.nc'" in _refusal(_zip(_GOOD, m)).message


def test_a_zero_size_member_is_fine():
    members = {**_GOOD, "layout.json": b""}
    assert BM.check(_zip(members, _manifest(members))) == []


def test_a_member_the_screen_would_ignore_still_counts_as_extra():
    exc = _refusal(_zip({**_GOOD, "stray.txt": b"x"}, _manifest()))
    assert "'stray.txt' is in the bundle but not in its manifest" in exc.message


def test_export_builds_a_fresh_manifest_and_never_copies_one_from_disk(
    client, api_project, project_storage_dir
):
    name = api_project("bm-stray")
    (project_storage_dir(name) / PC.MANIFEST).write_text('{"stale": true}')
    zf = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{name}/bundle").content))
    assert zf.namelist().count(PC.MANIFEST) == 1
    assert json.loads(zf.read(PC.MANIFEST))["schema_version"] == BM.SCHEMA_VERSION
    assert BM.check(zf) == []


def test_a_snapshot_and_its_restore_never_carry_a_manifest(
    client, api_project, project_storage_dir
):
    name = api_project("bm-snap")
    d = project_storage_dir(name)
    (d / PC.MANIFEST).write_text('{"stale": true}')
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code == 200, r.text
    snap = d / "snapshots" / r.json()["id"]
    assert not any(p.name == PC.MANIFEST for p in snap.rglob("*"))
    (d / PC.MANIFEST).unlink()
    r = client.post(f"/api/projects/{name}/snapshots/{snap.name}/restore")
    assert r.status_code == 200, r.text
    assert not (d / PC.MANIFEST).exists()
    assert all(PC.MANIFEST not in PC.names(op) for op in PC.OPS)


def _active_state(_auth_db):
    from db.models import Session as SessionRow
    from sqlalchemy import select
    from services.pypsa_service import PyPSAService

    _engine, session_local = _auth_db
    with session_local() as db:
        pointers = sorted(
            str(x) for x in db.scalars(select(SessionRow.active_project_id))
        )
    ctx = PyPSAService._active
    return pointers, id(ctx), id(ctx.network) if ctx is not None else None


def test_a_dry_run_swaps_nothing_and_never_reaches_the_commit_path(
    client, api_project, project_storage_dir, _auth_db, monkeypatch
):
    from routers import projects
    from services import active_project

    d = project_storage_dir(api_project("bm-dry-swap"))
    data = _bundle_from(d)
    before = _active_state(_auth_db)

    def never(*a, **k):
        raise AssertionError("the dry run reached the commit path")

    monkeypatch.setattr(projects, "_open_imported_bundle", never)
    monkeypatch.setattr(projects, "_move_staged", never)
    monkeypatch.setattr(active_project, "set_active_project", never)
    r = _dry(client, data, "bm_dry_swap_dst")
    assert r.status_code == 200, r.text
    assert _active_state(_auth_db) == before


def test_would_conflict_matches_the_real_409_both_ways(
    client, api_project, project_storage_dir
):
    d = project_storage_dir(api_project("bm-clash"))
    data = _bundle_from(d)
    # Taken: the dry run says so and the real import answers 409.
    assert _dry(client, data, "bm-clash").json()["would_conflict"] is True
    assert _post(client, data, "bm-clash").status_code == 409
    # Free, though a near-miss in case: the dry run says free and the import succeeds.
    assert _dry(client, data, "BM-CLASH-2").json()["would_conflict"] is False
    assert _post(client, data, "BM-CLASH-2").status_code == 200


def test_a_running_study_is_a_dry_run_warning_but_refuses_the_import(
    client, api_project, project_storage_dir, monkeypatch
):
    from fastapi import HTTPException
    from services.pypsa_service import PyPSAService

    d = project_storage_dir(api_project("bm-study"))
    data = _bundle_from(d)

    def running(cls, action="", *, ctx=None):
        raise HTTPException(409, f"Cannot {action} while a study is running.")

    monkeypatch.setattr(PyPSAService, "refuse_if_study_running", classmethod(running))
    r = _dry(client, data, "bm_study_dst")
    assert r.status_code == 200, r.text
    assert (
        "Cannot import a project bundle while a study is running."
        in r.json()["warnings"]
    )
    assert _post(client, data, "bm_study_dst").status_code == 409


# ── mutant follow-ups (PR2b run 1 and run 2 survivors) ──────────────────────


def test_the_manifest_cap_is_one_mib_and_a_large_real_manifest_reads():
    assert BM.MAX_MANIFEST_BYTES == 1024 * 1024
    members = {f"uploads/file-{i:04d}.csv": b"x" for i in range(400)}
    zf = zipfile.ZipFile(
        io.BytesIO(BM.add_to(_zip(members).fp.getvalue(), project_kind=None))
    )
    assert zf.getinfo(PC.MANIFEST).file_size > 32 * 1024
    assert BM.check(zf) == []


@pytest.mark.parametrize(
    "cfg",
    [
        b"{}",
        b'{"extra_functionality_code": null}',
        b'{"extra_functionality_code": "  "}',
    ],
    ids=["no-key", "null", "blank"],
)
def test_a_dry_run_strips_nothing_without_user_code(
    client, api_project, project_storage_dir, cfg
):
    d = project_storage_dir(api_project("bm-dry-nocode"))
    r = _dry(client, _bundle_from(d, {"solver_config.json": cfg}), "bm_dry_nocode_dst")
    assert r.status_code == 200, r.text
    assert r.json()["stripped"] == []


@pytest.mark.parametrize(
    "settings, at_default",
    [(None, True), (b'{"discount_rate": 0.05}', False)],
    ids=["no-settings", "own-rate"],
)
def test_a_dry_run_reports_rederived_rows_and_the_rate_they_used(
    client, tmp_path, settings, at_default
):
    from tests.test_project_contents import _priced_bundle

    def stale(n):
        n.storage_units.at["bess", "capital_cost"] = 1.0

    data = _priced_bundle(tmp_path, stale)
    if settings is not None:
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as dst:
            for info in src.infolist():
                dst.writestr(info, src.read(info.filename))
            dst.writestr("solver_config.json", settings)
        data = buf.getvalue()
    r = _dry(client, data, "bm_dry_priced_dst")
    assert r.status_code == 200, r.text
    dry = r.json()
    assert dry["rederived"], dry
    assert dry["rederived_at_default_rate"] is at_default
    real = _post(client, data, "bm_dry_priced_dst")
    assert real.status_code == 200, real.text
    real_fields = real.json()
    assert real_fields["rederived"] == dry["rederived"]
    assert real_fields.get("rederived_at_default_rate", False) is at_default


def test_a_running_study_refuses_the_real_import_before_the_upload_is_read(
    client, api_project, project_storage_dir, project_row, projects_root, monkeypatch
):
    from fastapi import HTTPException

    from routers import projects
    from services.pypsa_service import PyPSAService

    d = project_storage_dir(api_project("bm-study-first"))

    def running(cls, action="", *, ctx=None):
        raise HTTPException(409, f"Cannot {action} while a study is running.")

    def never(*a, **k):
        raise AssertionError("the upload was read past the study guard")

    monkeypatch.setattr(PyPSAService, "refuse_if_study_running", classmethod(running))
    monkeypatch.setattr(projects, "_bundle_network_owning_its_costs", never)
    r = _post(client, _bundle_from(d), "bm_study_first_dst")
    assert r.status_code == 409, r.text
    assert _left_behind(project_row, projects_root, "bm_study_first_dst") == []


def test_the_dry_run_leaves_the_callers_stripped_list_alone(tmp_path):
    from routers import projects

    (tmp_path / "solver_config.json").write_text('{"extra_functionality_code": "x=1"}')
    stripped = ["gridspine.from_network"]

    class _NoUser:
        pass

    import unittest.mock as um

    with um.patch("services.project_registry.find_project", return_value=None):
        out = projects._import_dry_run(
            None,
            _NoUser(),
            tmp_path,
            "n",
            None,
            {"network.nc"},
            ignored=[],
            stripped=stripped,
            rederived=[],
            rate_source="default",
            warnings=[],
        )
    assert out["stripped"] == ["gridspine.from_network", "extra_functionality_code"]
    assert stripped == ["gridspine.from_network"]


@pytest.mark.parametrize(
    "cfg", [b"{not json", b"[1, 2]"], ids=["not-json", "not-object"]
)
def test_an_unreadable_solver_config_refuses_the_dry_run_and_the_import_alike(
    client, api_project, project_storage_dir, project_row, projects_root, row_count, cfg
):
    d = project_storage_dir(api_project("bm-badcfg"))
    data = _bundle_from(d, {"solver_config.json": cfg})
    rows, tree = row_count(), _tree(projects_root)

    for r in (
        _dry(client, data, "bm_badcfg_dst"),
        _post(client, data, "bm_badcfg_dst"),
    ):
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["error_kind"] == "import_unreadable"
        assert "solver_config.json" in detail["message"]
    assert (row_count(), _tree(projects_root)) == (rows, tree)
    assert _left_behind(project_row, projects_root, "bm_badcfg_dst") == []


def test_a_dry_run_names_the_goal_values_the_import_drops(
    client, api_project, project_storage_dir
):
    d = project_storage_dir(api_project("bm-goal"))
    cfg = json.dumps({"target_lole_h": -1}).encode()
    data = _bundle_from(d, {"solver_config.json": cfg})

    dry = _dry(client, data, "bm_goal_dst")
    assert dry.status_code == 200, dry.text
    assert dry.json()["dropped_goal"] == [{"field": "target_lole_h", "value": -1}]
    real = _post(client, data, "bm_goal_dst")
    assert real.status_code == 200, real.text
    assert real.json()["dropped_goal"] == dry.json()["dropped_goal"]
