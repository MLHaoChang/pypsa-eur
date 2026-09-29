"""
A project pins the Library versions it references (Edge Investment Case P1 WP1.1c).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.1c

Every `PriceSeriesRef` a project's solver config carries (the commercial block,
WP1.3 onward) is recorded as `(id, version, hash)` in a `library_refs.json`
sidecar at save. Opening the project, importing its bundle or activating it
cold re-checks each pin against the org's Library. A pin that no longer
resolves to exactly the pinned bytes is reported as a `library_ref_stale`
issue. It is never swapped for whatever the Library holds now.
"""
from __future__ import annotations

import io
import json
import zipfile

import pandas as pd
import pytest

from routers.projects import _BUNDLE_FILES
from services.library import bundle_pins as P


def _put(client, name="px", offset=0.0):
    idx = pd.date_range("2030-01-01", periods=8, freq="15min", tz="UTC")
    r = client.post("/api/library/series", json={
        "name": name, "timestamps": [t.isoformat() for t in idx],
        "values": [40.0 + i + offset for i in range(8)], "meta": {"source": "pytest"}})
    assert r.status_code == 200, r.text
    return r.json()


def _save_with_refs(client, session_ctx, name, refs):
    ctx = session_ctx(client)
    ctx.solver_state["solver_config"].commercial = {
        "import_tariff_id": "t1", "export_price_ref": refs[0] if refs else None,
        "extra": [{"deep": r} for r in refs[1:]],
    }
    r = client.post(f"/api/projects/{name}", params={"force": True, "expect": name})
    assert r.status_code == 200, r.text


def _open(client, name):
    r = client.get(f"/api/projects/{name}")
    assert r.status_code == 200, r.text
    return r.json()


# ── collector ──────────────────────────────────────────────────────────────


def test_collect_refs_walks_nested_dicts_and_lists_and_dedupes():
    a = {"id": "a", "version": 1, "hash": "a" * 64, "source": "s"}
    b = {"id": "b", "version": 2, "hash": "b" * 64, "source": "s", "provider": None}
    cfg = {"x": a, "y": [{"z": b}, a], "noise": {"id": "not-a-ref"},
           "bad": {"id": "c", "version": 0, "hash": "c" * 64, "source": "s"}}
    refs = P.collect_refs(cfg)
    assert [(r.id, r.version) for r in refs] == [("a", 1), ("b", 2)]


def test_the_sidecar_travels_with_every_bundle_path():
    assert P.SIDECAR_NAME == "library_refs.json"
    assert P.SIDECAR_NAME in _BUNDLE_FILES


# ── save ───────────────────────────────────────────────────────────────────


def test_save_writes_one_pin_per_referenced_version(client, api_project, session_ctx,
                                                    project_storage_dir):
    name = api_project("pinned")
    a, b = _put(client, "px"), _put(client, "fuel")
    _save_with_refs(client, session_ctx, name, [a, b])
    side = json.loads((project_storage_dir(name) / P.SIDECAR_NAME).read_text())
    assert side["schema"] == 1
    assert side["refs"] == sorted(
        [{"id": x["id"], "version": x["version"], "hash": x["hash"]} for x in (a, b)],
        key=lambda d: (d["id"], d["version"]))


def test_a_project_with_no_refs_writes_no_sidecar_and_drops_a_stale_one(
        client, api_project, session_ctx, project_storage_dir):
    name = api_project("unpinned")
    assert not (project_storage_dir(name) / P.SIDECAR_NAME).exists()
    _save_with_refs(client, session_ctx, name, [_put(client)])
    assert (project_storage_dir(name) / P.SIDECAR_NAME).exists()
    _save_with_refs(client, session_ctx, name, [])
    assert not (project_storage_dir(name) / P.SIDECAR_NAME).exists()


# ── reload ─────────────────────────────────────────────────────────────────


def test_an_intact_library_reloads_with_no_issue(client, api_project, session_ctx):
    name = api_project("intact")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    assert _open(client, name)["library_issues"] == []


def test_a_new_version_in_the_library_does_not_move_the_pin(client, api_project, session_ctx):
    name = api_project("newer")
    v1 = _put(client)
    _save_with_refs(client, session_ctx, name, [v1])
    v2 = _put(client, offset=5.0)
    assert v2["version"] == 2
    body = _open(client, name)
    assert body["library_issues"] == []  # v1 still resolves exactly
    cfg = session_ctx(client).solver_state["solver_config"]
    assert cfg.commercial["export_price_ref"]["version"] == 1  # not substituted


def _library_rows(_auth_db):
    from db.models import LibraryItem

    _engine, session_local = _auth_db
    return session_local, LibraryItem


def test_a_deleted_library_item_is_a_missing_issue(client, api_project, session_ctx, _auth_db):
    name = api_project("gone")
    ref = _put(client)
    _save_with_refs(client, session_ctx, name, [ref])
    session_local, LibraryItem = _library_rows(_auth_db)
    with session_local() as db:
        db.query(LibraryItem).delete()
        db.commit()
    issues = _open(client, name)["library_issues"]
    assert [(i["code"], i["reason"], i["id"], i["version"]) for i in issues] == [
        ("library_ref_stale", "missing", "px", 1)]
    # The config still names the pinned version: no silent substitution.
    cfg = session_ctx(client).solver_state["solver_config"]
    assert cfg.commercial["export_price_ref"]["hash"] == ref["hash"]


def test_a_changed_hash_is_a_changed_issue(client, api_project, session_ctx, _auth_db):
    name = api_project("changed")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    session_local, LibraryItem = _library_rows(_auth_db)
    with session_local() as db:
        row = db.query(LibraryItem).one()
        row.hash = "f" * 64
        db.commit()
    issues = _open(client, name)["library_issues"]
    assert [i["reason"] for i in issues] == ["changed"]


def test_a_lost_payload_file_is_an_unreadable_issue(client, api_project, session_ctx,
                                                    seeded_identity):
    import pathlib

    from services.library.series_store import _default_root
    from services.storage_paths import library_dir

    name = api_project("nofile")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    for f in pathlib.Path(library_dir(_default_root(), seeded_identity["org_id"])).rglob("*.gz"):
        f.unlink()
    issues = _open(client, name)["library_issues"]
    assert [i["reason"] for i in issues] == ["payload_unreadable"]


def _bundle_with(client, name, sidecar):
    """The project's bundle with `library_refs.json` replaced (None = dropped).
    Disk edits under a RESIDENT project are overwritten by its write-back on
    the next load, so a tampered sidecar arrives the way it would in practice:
    in a hand-edited or older bundle."""
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    src = zipfile.ZipFile(io.BytesIO(r.content))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in src.namelist():
            if m != P.SIDECAR_NAME:
                zf.writestr(m, src.read(m))
        if sidecar is not None:
            zf.writestr(P.SIDECAR_NAME, sidecar)
    return buf.getvalue()


def _import(client, data, as_name):
    r = client.post(f"/api/projects/import_bundle?name={as_name}",
                    files={"file": ("b.zip", data, "application/zip")})
    assert r.status_code in (200, 201), r.text
    return r.json()["library_issues"]


def test_a_ref_the_sidecar_does_not_pin_is_reported(client, api_project, session_ctx):
    name = api_project("unpinned-ref")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    issues = _import(client, _bundle_with(client, name, None), "unpinned_in")
    assert [i["reason"] for i in issues] == ["unpinned"]


def test_a_corrupt_sidecar_is_an_issue_not_a_failed_import(client, api_project, session_ctx):
    name = api_project("corrupt")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    issues = _import(client, _bundle_with(client, name, "{not json"), "corrupt_in")
    # The config's ref is still checked (and resolves), so only the sidecar is reported.
    assert [i["reason"] for i in issues] == ["sidecar_unreadable"]


def test_issues_are_logged_to_the_changelog(client, api_project, session_ctx, _auth_db):
    name = api_project("logged")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    session_local, LibraryItem = _library_rows(_auth_db)
    with session_local() as db:
        db.query(LibraryItem).delete()
        db.commit()
    _open(client, name)
    log = client.get("/api/changelog/").json()
    entries = log if isinstance(log, list) else log.get("entries", [])
    assert any("library_ref_stale" in json.dumps(e) or "Library" in json.dumps(e)
               for e in entries)


# ── bundle / other org ─────────────────────────────────────────────────────


def test_a_bundle_carries_the_pins_and_import_checks_them(client, other_org_client,
                                                          api_project, session_ctx):
    name = api_project("shared")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    assert P.SIDECAR_NAME in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    # Same org: the pin resolves.
    same = client.post("/api/projects/import_bundle?name=shared_again",
                       files={"file": ("b.zip", r.content, "application/zip")})
    assert same.status_code in (200, 201), same.text
    assert same.json()["library_issues"] == []
    # Another org has no such item: reported, never resolved against their Library.
    other = other_org_client.post("/api/projects/import_bundle?name=shared_there",
                                  files={"file": ("b.zip", r.content, "application/zip")})
    assert other.status_code in (200, 201), other.text
    assert [i["reason"] for i in other.json()["library_issues"]] == ["missing"]


@pytest.mark.parametrize("pins", [None, {"schema": 1, "refs": []}])
def test_check_pins_with_nothing_to_check_is_empty(tmp_path, _auth_db, seeded_identity, pins):
    if pins is not None:
        (tmp_path / P.SIDECAR_NAME).write_text(json.dumps(pins))
    _engine, session_local = _auth_db
    with session_local() as db:
        assert P.check_pins(db, seeded_identity["org_id"], tmp_path, config={}) == []


# ── Review conditions (WP1.1c review round 1) ───────────────────────────────


def test_a_corrupt_payload_is_an_issue_not_a_500(client, api_project, session_ctx,
                                                  seeded_identity):
    """#1: junk bytes (not a missing file) must not escape as BadGzipFile."""
    import pathlib

    from services.library.series_store import _default_root
    from services.storage_paths import library_dir

    name = api_project("junk")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    for f in pathlib.Path(library_dir(_default_root(), seeded_identity["org_id"])).rglob("*.gz"):
        f.write_bytes(b"not gzip at all")
    issues = _open(client, name)["library_issues"]
    assert [i["reason"] for i in issues] == ["payload_unreadable"]


def test_opening_a_project_without_a_solver_config_does_not_inherit_the_previous_one(
        client, api_project, session_ctx, project_storage_dir):
    """#2: B has no solver_config.json; A's commercial block must not follow."""
    a = api_project("with_refs")
    _save_with_refs(client, session_ctx, a, [_put(client)])
    b = api_project("bare")
    (project_storage_dir(b) / "solver_config.json").unlink()
    (project_storage_dir(b) / P.SIDECAR_NAME).unlink(missing_ok=True)
    _open(client, a)
    body = _open(client, b)
    assert body["library_issues"] == []
    assert session_ctx(client).solver_state["solver_config"].commercial is None


def test_activating_a_resident_project_rechecks_its_refs(client, api_project, session_ctx,
                                                        _auth_db):
    """#3: a context can be resident without ever having been checked."""
    name = api_project("resident")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    session_local, LibraryItem = _library_rows(_auth_db)
    with session_local() as db:
        db.query(LibraryItem).delete()
        db.commit()
    r = client.post(f"/api/projects/{name}/activate")
    assert r.status_code == 200, r.text
    assert [i["reason"] for i in r.json()["library_issues"]] == ["missing"]


def test_the_sidecar_is_written_with_the_house_atomic_writer(client, api_project, session_ctx,
                                                             project_storage_dir):
    """#5: no mkstemp leftovers outside the `.tmp` sweep; normal file mode."""
    import stat

    name = api_project("atomic")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    side = project_storage_dir(name) / P.SIDECAR_NAME
    assert stat.S_IMODE(side.stat().st_mode) == stat.S_IMODE(
        (project_storage_dir(name) / "solver_config.json").stat().st_mode)
    assert not [p for p in project_storage_dir(name).iterdir()
                if p.name.startswith(f".{P.SIDECAR_NAME}")]


@pytest.mark.parametrize("bad", [
    {"id": "px", "version": 2.7, "hash": "a" * 64},
    {"id": "px", "version": True, "hash": "a" * 64},
    {"id": None, "version": 1, "hash": "a" * 64},
    {"id": "px", "version": 0, "hash": "a" * 64},
])
def test_a_malformed_pin_is_an_unreadable_sidecar(tmp_path, bad):
    """#7: never 'repair' a pin into a different one."""
    (tmp_path / P.SIDECAR_NAME).write_text(json.dumps({"schema": 1, "refs": [bad]}))
    pins, issues = P.read_pins(tmp_path)
    assert pins == [] and [i["reason"] for i in issues] == ["sidecar_unreadable"]


def test_restoring_a_snapshot_without_pins_drops_the_current_sidecar(
        client, api_project, session_ctx, project_storage_dir):
    """#6: the restored config has no refs; the live sidecar must not survive."""
    name = api_project("snap")
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "before refs"})
    assert r.status_code in (200, 201), r.text
    snap_id = r.json().get("id") or r.json().get("snapshot_id")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    assert (project_storage_dir(name) / P.SIDECAR_NAME).exists()
    r = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
    assert r.status_code == 200, r.text
    assert not (project_storage_dir(name) / P.SIDECAR_NAME).exists()
