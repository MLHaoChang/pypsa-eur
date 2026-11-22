"""Model files into a new project (project-io 04, Slice A; GK B3 plan GO, conditions C1-C7).

The format list reads the registry; a preview writes nothing; an import creates a new
project, all or nothing, and never overwrites one (IMPORT-FLOW-1). The reader runs in a
child process, so the fakes live in `tests/model_import_fakes`, which the child imports.
"""

from __future__ import annotations

import json
import multiprocessing
import threading
import uuid
from pathlib import Path

import pypsa
import pytest

from db.models import Project
from gridspine.ingest.import_result import DECLARED, ImporterSpec
from services import model_import_service as svc
from services import project_contents as PC
from settings import get_settings

REPO = Path(__file__).resolve().parents[3]
CASE14 = REPO / "tests/gridspine/fixtures/psse/case14.raw"
PLEXOS = REPO / "tests/gridspine/fixtures/plexos/world2015_irl.xml"
FAKE = ImporterSpec(
    format_id="fake",
    display_name="Fake model",
    extensions=(".fake",),
    help="A test-only format.",
    package="tests.model_import_fakes",
)


@pytest.fixture
def fake(monkeypatch):
    """The registry plus the fake importer, as the service and the child see it."""
    monkeypatch.setattr(svc, "declared", lambda: (*DECLARED, FAKE))


@pytest.fixture
def store(_auth_db):
    """The project rows, the folders and the staging folders: what a refused call must not leave."""
    _, session_local = _auth_db
    root = Path(get_settings().projects_root)
    staging = svc.staging_root()

    def state():
        with session_local() as db:
            rows = sorted(str(p.id) for p in db.query(Project).all())
        # Non-empty folders: the first project in a workspace also makes its (empty) parent.
        dirs = sorted(
            str(p)
            for p in root.rglob("*")
            if p.is_dir()
            and p != staging
            and staging not in p.parents
            and any(p.iterdir())
        )
        return rows, dirs, sorted(p.name for p in staging.iterdir())

    return state


def _files(*named):
    return [("files", (name, data, "application/octet-stream")) for name, data in named]


def _preview(client, *named, options=None):
    data = {"options": json.dumps(options)} if options is not None else {}
    return client.post("/api/model-import/preview", files=_files(*named), data=data)


def _import(client, *named, sha, name=None, options=None):
    data = {"expected_sha256": sha}
    if name is not None:
        data["name"] = name
    if options is not None:
        data["options"] = json.dumps(options)
    return client.post("/api/model-import/import", files=_files(*named), data=data)


def _kind(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("error_kind") if isinstance(detail, dict) else None


def _dir(session_local, project_id: str) -> Path:
    from services import project_registry

    with session_local() as db:
        return project_registry.project_dir(db.get(Project, uuid.UUID(project_id)))


def _dgs_bytes(tmp_path) -> bytes:
    import pandapower.networks as pn

    from gridspine.handoff.dgs_writer import write_dgs

    net = pn.example_simple()
    net.ext_grid[["s_sc_max_mva", "s_sc_min_mva", "rx_max", "rx_min"]] = [
        1000.0,
        800.0,
        0.1,
        0.1,
    ]
    path = tmp_path / "simple.dgs"
    write_dgs(net, path)
    return path.read_bytes()


# ── formats ──────────────────────────────────────────────────────────────────


def test_formats_come_from_the_registry_and_a_new_importer_appears(client, monkeypatch):
    ids = [f["id"] for f in client.get("/api/model-import/formats").json()["formats"]]
    assert {"psse", "dgs", "plexos"} <= set(ids) and "fake" not in ids
    assert "excel" not in ids  # the Excel workbook has its own import (#1798)
    monkeypatch.setattr(svc, "declared", lambda: (*DECLARED, FAKE))
    formats = {
        f["id"]: f for f in client.get("/api/model-import/formats").json()["formats"]
    }
    assert formats["fake"] == {
        "id": "fake",
        "label": "Fake model",
        "extensions": [".fake"],
        "help": "A test-only format.",
    }


def test_formats_need_a_signed_in_caller(anon_client):
    assert anon_client.get("/api/model-import/formats").status_code == 401


# ── preview: plain names, nothing kept ───────────────────────────────────────


@pytest.mark.parametrize("which", ["psse", "dgs", "plexos"])
def test_a_preview_of_each_real_format_lists_everything_and_keeps_nothing(
    client, store, tmp_path, which
):
    named = {
        "psse": ("case14.raw", CASE14.read_bytes()),
        "dgs": ("simple.dgs", _dgs_bytes(tmp_path)),
        "plexos": ("world2015_irl.xml", PLEXOS.read_bytes()),
    }[which]
    before = store()
    r = _preview(client, named)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == which and body["files"] == [named[0]]
    assert len(body["sha256"]) == 64 and body["bus_count"] > 0
    assert body["counts"] and all(isinstance(v, int) for v in body["counts"].values())
    # Full lists, never counts (issue 04); a row reads in words, not field codes.
    assert isinstance(body["unmapped"], list) and isinstance(body["assumed"], list)
    for row in body["assumed"]:
        assert set(row) >= {"asset", "parameter", "value", "why"} and row["why"]
    if which == "psse":
        assert body["counts"]["Buses"] == 14 and body["stored_files"] == [
            "psse_case.json"
        ]
    if which == "plexos":
        assert len(body["assumed"]) > 0 and len(body["unmapped"]) > 0
        assert any(a["parameter"] == "Model" for a in body["assumed"])
    assert store() == before


def test_an_unknown_extension_is_a_plain_422(client, store):
    before = store()
    r = _preview(client, ("notes.txt", b"hello"))
    assert r.status_code == 422 and _kind(r) == "import_unreadable"
    assert "no importer reads .txt" in r.json()["detail"]["message"]
    assert store() == before


def test_the_excel_workbook_points_to_its_own_import(client, store):
    """`.xlsx` with no DGS content falls back to the Excel input workbook, which has its own route."""
    from openpyxl import Workbook

    import io

    buf = io.BytesIO()
    Workbook().save(buf)
    r = _preview(client, ("book.xlsx", buf.getvalue()))
    assert r.status_code == 422 and _kind(r) == "import_format_elsewhere", r.text


def test_files_of_two_formats_are_refused(client, fake, store):
    r = _preview(client, ("a.fake", b"ok"), ("case14.raw", CASE14.read_bytes()))
    assert r.status_code == 422 and _kind(r) == "import_files_invalid"


def test_a_reader_refusal_is_its_own_words(client, fake, store):
    before = store()
    r = _preview(client, ("m.fake", b"refuse"))
    assert (
        r.status_code == 422
        and r.json()["detail"]["message"] == "m.fake is not a fake model"
    )
    assert store() == before


def test_a_reader_crash_is_a_plain_422_not_a_500(client, fake, store):
    before = store()
    r = _preview(client, ("m.fake", b"boom"))
    assert r.status_code == 422 and _kind(r) == "import_unreadable"
    assert "(RuntimeError)" in r.json()["detail"]["message"]
    assert store() == before


def test_a_reader_past_the_time_budget_is_stopped_and_its_child_killed(
    client, fake, store, monkeypatch
):
    monkeypatch.setattr(svc, "READ_SECONDS", 15.0)
    before = store()
    r = _preview(client, ("m.fake", b"sleep"))
    assert r.status_code == 422 and _kind(r) == "import_timeout", r.text
    assert r.json()["detail"]["budget_seconds"] == 15.0
    assert not [
        c for c in multiprocessing.active_children() if c.name == "model-import"
    ]
    assert store() == before


def test_a_child_that_dies_at_the_memory_limit_is_a_plain_422(
    client, fake, store, monkeypatch
):
    monkeypatch.setattr(svc, "READ_MEMORY", 3 * 1024 * 1024 * 1024)
    before = store()
    r = _preview(client, ("m.fake", b"eat"))
    assert r.status_code == 422 and _kind(r) == "import_unreadable", r.text
    assert store() == before


# ── C4: uploads ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "..",
        "a/b.fake",
        "a\\b.fake",
        " a.fake",
        "a\x01.fake",
        "x:y.fake",
        "CON.fake",
        "nul",
        "a" * 252 + ".fake",
    ],
)
def test_a_file_name_that_is_not_plain_is_refused(bad):
    with pytest.raises(Exception) as err:
        svc.check_names([bad])
    assert (
        err.value.status_code == 422
        and err.value.detail["error_kind"] == "import_files_invalid"
    )


def test_an_empty_name_and_duplicate_names_are_refused():
    for names in ([""], ["a.fake", "A.fake"], ["a.fake", "a.fake."], []):
        with pytest.raises(Exception) as err:
            svc.check_names(names)
        assert err.value.status_code == 422, names


def test_more_files_than_the_cap_are_refused_before_reading(client, fake, store):
    named = [(f"m{i}.fake", b"ok") for i in range(svc.MAX_FILES + 1)]
    r = _preview(client, *named)
    assert r.status_code == 422 and _kind(r) == "import_files_invalid"
    assert store()[2] == []


def test_files_over_the_byte_cap_together_are_413(client, fake, store, monkeypatch):
    from services import upload_guard

    monkeypatch.setattr(upload_guard, "_DEFAULT_MAX_UPLOAD_BYTES", 10)
    before = store()
    r = _preview(client, ("a.fake", b"ok ok"), ("b.fake", b"ok ok ok"))
    assert r.status_code == 413
    assert store() == before


@pytest.mark.parametrize(
    "raw", ["[1]", "{bad", '{"Model": "x"}', '{"model": {"a": 1}}']
)
def test_options_that_are_not_short_scalar_values_are_refused(client, fake, raw):
    r = client.post(
        "/api/model-import/preview",
        files=_files(("m.fake", b"ok")),
        data={"options": raw},
    )
    assert r.status_code == 422 and _kind(r) == "import_options_invalid"


# ── import: a new project, all or nothing ────────────────────────────────────


def test_an_import_creates_a_project_that_opens_with_its_report(
    client, _auth_db, store
):
    _, session_local = _auth_db
    named = ("case14.raw", CASE14.read_bytes())
    sha = _preview(client, named).json()["sha256"]
    r = _import(client, named, sha=sha, name="IEEE 14")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "IEEE 14" and body["bus_count"] == 14
    dest = _dir(session_local, body["project_id"])
    n = pypsa.Network()
    n.import_from_netcdf(str(dest / "network.nc"))
    assert len(n.buses) == 14
    report = json.loads((dest / "import_report.json").read_text())
    assert (
        report["format"] == "psse"
        and report["sha256"] == sha
        and report["files"] == ["case14.raw"]
    )
    assert report["report"]["counts"]["Buses"] == 14
    from gridspine.ingest.import_result import ImportReport

    assert ImportReport.from_dict(report["report"]).counts == report["report"]["counts"]
    assert (dest / "psse_case.json").is_file()
    meta = json.loads((dest / "metadata.json").read_text())
    assert meta["bus_count"] == 14 and meta["network_revision"] == 0
    opened = client.post(f"/api/projects/{body['project_id']}/activate")
    assert opened.status_code == 200, opened.text
    assert len(client.get("/api/network/buses").json()) == 14
    assert store()[2] == []


def test_an_importer_s_metadata_is_merged_and_keeps_the_create_s_keys(
    client, fake, _auth_db, store
):
    """The DGS reader stores its names as a metadata.json sidecar (project-io (6) hand-off)."""
    _, session_local = _auth_db
    named = ("m.fake", b"importmeta")
    sha = _preview(client, named).json()["sha256"]
    r = _import(client, named, sha=sha, name="Merged meta")
    assert r.status_code == 201, r.text
    meta = json.loads(
        (_dir(session_local, r.json()["project_id"]) / "metadata.json").read_text()
    )
    assert meta["import"] == {"format": "fake", "names": {"North": "N1"}}
    assert set(svc._create_meta(0, 0)) <= set(meta) and meta["bus_count"] == 2


def test_a_real_dgs_import_keeps_its_names_beside_the_create_s_keys(
    client, _auth_db, tmp_path
):
    _, session_local = _auth_db
    named = ("simple.dgs", _dgs_bytes(tmp_path))
    sha = _preview(client, named).json()["sha256"]
    r = _import(client, named, sha=sha, name="DGS simple")
    assert r.status_code == 201, r.text
    meta = json.loads(
        (_dir(session_local, r.json()["project_id"]) / "metadata.json").read_text()
    )
    assert meta["import"]["format"] == "dgs" and "dgs_names" in meta["import"]
    assert set(svc._create_meta(0, 0)) <= set(meta) and meta["bus_count"] == 7


@pytest.mark.parametrize("word", ["meta", "ownmeta", "stray"])
def test_a_sidecar_the_project_does_not_keep_refuses_the_import(
    client, fake, store, word
):
    """C5: a reader may not set the project's own metadata keys or store other files."""
    named = ("m.fake", word.encode())
    before = store()
    r = _preview(client, named)
    assert r.status_code == 422 and _kind(r) == "import_sidecar_refused", r.text
    sha = svc.digest({}, [named])
    r = _import(client, named, sha=sha, name=f"Sidecar {word}")
    assert r.status_code == 422 and _kind(r) == "import_sidecar_refused"
    assert store() == before


def test_a_stale_digest_is_409_and_creates_nothing(client, fake, store):
    sha = _preview(client, ("m.fake", b"ok")).json()["sha256"]
    before = store()
    r = _import(client, ("m.fake", b"ok changed"), sha=sha, name="Stale")
    assert r.status_code == 409 and _kind(r) == "import_file_changed"
    assert store() == before


def test_options_changed_since_the_preview_are_409(client, fake, store):
    """C3: the digest covers the options, so a different Model choice is never imported silently."""
    named = ("m.fake", b"ok")
    sha = _preview(client, named, options={"model": "Base"}).json()["sha256"]
    before = store()
    r = _import(client, named, sha=sha, name="Opts", options={"model": "High"})
    assert r.status_code == 409 and _kind(r) == "import_file_changed"
    assert store() == before
    r = _import(client, named, sha=sha, name="Opts", options={"model": "Base"})
    assert r.status_code == 201, r.text


def test_a_failure_after_the_row_exists_leaves_no_row_and_no_folder(
    client, fake, store, monkeypatch
):
    named = ("m.fake", b"ok")
    sha = _preview(client, named).json()["sha256"]
    before = store()

    def broken(dest, st):
        (dest / "network.nc").write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_write_files", broken)
    r = _import(client, named, sha=sha, name="Half made")
    assert r.status_code == 500 and _kind(r) == "import_write_failed"
    assert store() == before


def test_a_taken_name_is_409_and_overwrites_nothing(client, fake, _auth_db, store):
    _, session_local = _auth_db
    named = ("m.fake", b"ok")
    sha = _preview(client, named).json()["sha256"]
    first = _import(client, named, sha=sha, name="Taken")
    assert first.status_code == 201
    dest = _dir(session_local, first.json()["project_id"])
    stamp = (dest / "network.nc").read_bytes()
    before = store()
    again = _import(client, named, sha=sha, name="Taken")
    assert again.status_code == 409
    assert store() == before and (dest / "network.nc").read_bytes() == stamp


def test_two_imports_racing_for_one_name_one_wins_the_other_is_a_clean_409(
    client, fake, store, monkeypatch
):
    """GK (b): both pass every check, then meet at `create_root`; the database decides."""
    from services import project_registry

    named = ("m.fake", b"ok")
    sha = _preview(client, named).json()["sha256"]
    before_rows, before_dirs, _ = store()
    real = project_registry.create_root
    barrier = threading.Barrier(2, timeout=60)

    def meet(*args, **kwargs):
        barrier.wait()
        return real(*args, **kwargs)

    monkeypatch.setattr(project_registry, "create_root", meet)
    results = []

    def go():
        try:
            results.append(_import(client, named, sha=sha, name="Raced"))
        except BaseException as exc:  # surfaced by the assert below
            results.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert sorted(getattr(r, "status_code", 0) for r in results) == [201, 409], [
        getattr(r, "text", r) for r in results
    ]
    rows, dirs, staged = store()
    assert len(rows) == len(before_rows) + 1
    made = [
        d for d in dirs if d not in before_dirs and (Path(d) / "network.nc").is_file()
    ]
    assert len(made) == 1 and staged == []


# ── C2: who may import ───────────────────────────────────────────────────────


def test_a_signed_out_caller_is_401_and_creates_nothing(anon_client, fake, store):
    before = store()
    assert _preview(anon_client, ("m.fake", b"ok")).status_code == 401
    assert _import(anon_client, ("m.fake", b"ok"), sha="0" * 64).status_code == 401
    assert store() == before


def test_a_caller_outside_every_workspace_is_403_and_creates_nothing(
    _auth_db, fake, store
):
    from tests.test_changelog_scoping import _user

    orgless, raw = _user(_auth_db, org_id=None)
    try:
        named = ("m.fake", b"ok")
        before = store()
        r = _import(orgless, named, sha=svc.digest({}, [named]), name="No org")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "User is not a member of any organization"
        assert store() == before
    finally:
        raw.__exit__(None, None, None)


# ── the contents table ───────────────────────────────────────────────────────


def test_every_importer_file_is_in_the_contents_table_with_its_writer():
    """`pending_writer` (#1648) is cleared: the import path now writes these files."""
    for name in (*PC.IMPORTER_SIDECARS, svc.REPORT_FILE):
        entry = PC.entry(name)
        assert entry is not None and entry.carried_by == frozenset(
            {PC.BUNDLE, PC.SAVE_AS, PC.FORK, PC.SNAPSHOT}
        ), name
        assert not entry.pending_writer, name
    assert not [e.path for e in PC.TABLE if e.pending_writer]


# ── review and mutant follow-ups ─────────────────────────────────────────────


def test_a_non_finite_option_is_refused_before_reading(client, fake):
    r = client.post(
        "/api/model-import/preview",
        files=_files(("m.fake", b"ok")),
        data={"options": '{"x": NaN}'},
    )
    assert r.status_code == 422 and _kind(r) == "import_options_invalid"


def test_a_format_sniff_that_breaks_is_a_plain_422(client, fake, store, monkeypatch):
    from gridspine.ingest import import_result as IR

    def broken(*_a, **_k):
        raise RuntimeError("sniff broke")

    monkeypatch.setattr(IR, "for_file", broken)
    before = store()
    r = _preview(client, ("m.fake", b"ok"))
    assert r.status_code == 422 and _kind(r) == "import_unreadable"
    assert store() == before


def _without_gridspine(monkeypatch):
    """A build whose gridspine does not import (OPTIONAL_AT_RUNTIME in the packaging test)."""
    import sys

    monkeypatch.setitem(sys.modules, "gridspine.ingest", None)
    monkeypatch.setitem(sys.modules, "gridspine.ingest.import_result", None)


def test_a_build_without_gridspine_refuses_every_route_plainly_and_writes_nothing(
    client, fake, store, monkeypatch
):
    named = ("m.fake", b"ok")
    sha = _preview(client, named).json()["sha256"]
    _without_gridspine(monkeypatch)
    before = store()
    for r in (
        client.get("/api/model-import/formats"),
        _preview(client, named),
        _import(client, named, sha=sha, name="No gridspine"),
    ):
        assert r.status_code == 503 and _kind(r) == "import_unavailable", r.text
    assert store() == before


def test_a_child_without_gridspine_answers_plainly_and_writes_nothing(
    monkeypatch, tmp_path
):
    from services import model_import_worker as W

    _without_gridspine(monkeypatch)
    out = W.run("fake", [], {}, str(tmp_path), ())
    assert set(out) == {"unavailable"} and list(tmp_path.iterdir()) == []


def test_a_child_that_answers_unavailable_is_a_plain_503(
    client, fake, store, monkeypatch
):
    monkeypatch.setattr(
        svc, "_read_in_child", lambda st: {"unavailable": "No module named 'gridspine'"}
    )
    before = store()
    r = _preview(client, ("m.fake", b"ok"))
    assert r.status_code == 503 and _kind(r) == "import_unavailable", r.text
    assert store() == before


def test_reads_past_the_concurrency_cap_are_503_and_read_nothing(
    client, fake, store, monkeypatch
):
    calls = []
    monkeypatch.setattr(svc, "_read_in_child", lambda st: calls.append(st) or {})
    for _ in range(svc.MAX_CONCURRENT_READS):
        assert svc._READS.acquire(blocking=False)
    try:
        r = _preview(client, ("m.fake", b"ok"))
    finally:
        for _ in range(svc.MAX_CONCURRENT_READS):
            svc._READS.release()
    assert r.status_code == 503 and _kind(r) == "import_busy" and calls == []
    assert store()[2] == []


def test_a_caller_outside_every_workspace_is_refused_before_anything_is_read(
    _auth_db, fake, monkeypatch
):
    from tests.test_changelog_scoping import _user

    staged = []
    real = svc.stage

    async def counting(*a, **k):
        staged.append(1)
        return await real(*a, **k)

    monkeypatch.setattr(svc, "stage", counting)
    orgless, raw = _user(_auth_db, org_id=None)
    try:
        named = ("m.fake", b"ok")
        assert (
            _import(
                orgless, named, sha=svc.digest({}, [named]), name="No org"
            ).status_code
            == 403
        )
    finally:
        raw.__exit__(None, None, None)
    assert staged == []


def test_a_database_failure_after_the_row_exists_is_rolled_back_and_removed(
    client, fake, _auth_db, store, monkeypatch
):
    """M2: a failed flush leaves the session needing a rollback before the row can go."""
    from services import project_registry

    named = ("m.fake", b"ok")
    sha = _preview(client, named).json()["sha256"]
    before = store()
    seen = {}
    real = project_registry.create_root

    def keep(db, *a, **k):
        project = real(db, *a, **k)
        seen["db"], seen["project"] = db, project
        return project

    def broken(dest, st):
        db, project = seen["db"], seen["project"]
        db.add(
            Project(
                id=project.id,
                org_id=project.org_id,
                name=project.name,
                created_by=project.created_by,
                storage_path="dup",
            )
        )
        db.flush()  # IntegrityError: the same id again

    monkeypatch.setattr(project_registry, "create_root", keep)
    monkeypatch.setattr(svc, "_write_files", broken)
    r = _import(client, named, sha=sha, name="Bad flush")
    assert r.status_code == 500 and _kind(r) == "import_write_failed", r.text
    assert store() == before


def test_a_staging_write_that_fails_leaves_no_staging_folder(fake, monkeypatch):
    """M11: the staging folder is removed when writing an upload into it fails."""
    import pathlib

    def full(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(pathlib.Path, "write_bytes", full)
    with pytest.raises(OSError):
        svc._stage_files(["m.fake"], {}, [("m.fake", b"ok")])
    assert [p.name for p in svc.staging_root().iterdir()] == []


def test_staging_folders_a_killed_process_left_are_cleared(fake):
    import os
    import time

    old = svc.staging_root() / "import-left-behind"
    old.mkdir()
    fresh = svc.staging_root() / "import-in-use"
    fresh.mkdir()
    os.utime(old, (time.time() - 2 * svc.STALE_STAGING_SECONDS,) * 2)
    svc.clear_stale_staging()
    assert not old.exists() and fresh.exists()
    fresh.rmdir()
