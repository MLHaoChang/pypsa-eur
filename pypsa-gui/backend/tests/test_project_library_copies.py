"""project-io 03: a project keeps a copy of each Library payload it pins.

GK B3's plan conditions L1-L7 are named on the tests that pin them.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile

import pytest

from services import project_contents as PCT
from services.library import bundle_pins as P
from services.library import project_copies as PC
from services.library import series_store as S
from tests.bundle_util import refresh_manifest
from tests.test_library_bundle_pins import (
    _drop_copies,
    _library_rows,
    _open,
    _put,
    _save_with_refs,
)
from tests.test_project_contents import _left_behind, projects_root  # noqa: F401 (fixture)


def _pin(ref) -> dict:
    return {
        "kind": "series",
        "id": ref["id"],
        "version": ref["version"],
        "hash": ref["hash"],
    }


def _copy(project_storage_dir, name, ref):
    return project_storage_dir(name) / PC.member_name(_pin(ref))


def _bundle(client, name) -> bytes:
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200, r.text
    return r.content


def _edited(data: bytes, replace: dict[str, bytes | None]) -> bytes:
    """`data` with members replaced (None drops one), manifest rebuilt."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in src.namelist():
            if m not in replace:
                zf.writestr(m, src.read(m))
        for m, body in replace.items():
            if body is not None:
                zf.writestr(m, body)
    return refresh_manifest(buf.getvalue())


def _import(client, data, name):
    return client.post(
        f"/api/projects/import_bundle?name={name}",
        files={"file": ("b.zip", data, "application/zip")},
    )


# ── the copy a save writes ─────────────────────────────────────────────────


def test_a_save_keeps_a_copy_named_by_the_pins_hash(
    client, api_project, session_ctx, project_storage_dir
):
    name = api_project("lc-save")
    ref = _put(client)
    _save_with_refs(client, session_ctx, name, [ref])
    path = _copy(project_storage_dir, name, ref)
    assert path.name == f"{ref['hash']}.csv.gz"
    assert hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == ref["hash"]
    assert PCT.entry("library").carried_by == frozenset(
        {PCT.BUNDLE, PCT.SAVE_AS, PCT.FORK, PCT.SNAPSHOT}
    )  # L6


def test_the_copy_serves_the_pin_when_the_library_row_is_gone(
    client, api_project, session_ctx, project_storage_dir, _auth_db, seeded_identity
):
    name = api_project("lc-gone")
    ref = _put(client)
    _save_with_refs(client, session_ctx, name, [ref])
    session_local, LibraryItem = _library_rows(_auth_db)
    with session_local() as db:
        db.query(LibraryItem).delete()
        db.commit()
    # Served by the copy; the notice says the Library row (its currency) is gone.
    assert [i["reason"] for i in _open(client, name)["library_issues"]] == ["copy_only"]
    from models.commercial import PriceSeriesRef

    pref = PriceSeriesRef(**{k: ref[k] for k in ("id", "version", "hash", "source")})
    with session_local() as db:
        got = S.resolve(
            db, seeded_identity["org_id"], pref, project_dir=project_storage_dir(name)
        )
        assert list(got.to_numpy()) == [40.0 + i for i in range(8)]
        assert str(got.index.tz) == "UTC"
        with pytest.raises(S.LibraryRefNotFound):
            S.resolve(db, seeded_identity["org_id"], pref)


# ── L2: a copy is used only when it hashes to its pin ──────────────────────


def test_a_modified_copy_is_reported_and_never_read(
    client, api_project, session_ctx, project_storage_dir, _auth_db, seeded_identity
):
    name = api_project("lc-mod")
    ref = _put(client)
    _save_with_refs(client, session_ctx, name, [ref])
    path = _copy(project_storage_dir, name, ref)
    path.write_bytes(
        gzip.compress(b"# tz=UTC\ntimestamp_utc,value\n2030-01-01T00:00:00Z,999\n")
    )
    session_local, LibraryItem = _library_rows(_auth_db)
    with session_local() as db:
        issues = P.check_pins(
            db, seeded_identity["org_id"], project_storage_dir(name), config={}
        )
    assert [(i["reason"], i["id"]) for i in issues] == [("copy_modified", ref["id"])]
    assert PC.member_name(_pin(ref)) in issues[0]["message"]
    from models.commercial import PriceSeriesRef

    pref = PriceSeriesRef(**{k: ref[k] for k in ("id", "version", "hash", "source")})
    with session_local() as db:
        # The org's Library serves it, not the modified copy.
        got = S.resolve(
            db, seeded_identity["org_id"], pref, project_dir=project_storage_dir(name)
        )
        assert 999.0 not in list(got.to_numpy())
        db.query(LibraryItem).delete()
        db.commit()
        with pytest.raises(S.LibraryRefStale, match="copy .* was modified"):
            S.resolve(
                db,
                seeded_identity["org_id"],
                pref,
                project_dir=project_storage_dir(name),
            )


def test_a_copy_too_large_once_unpacked_is_not_read(tmp_path, monkeypatch):
    data = b"# tz=naive\ntimestamp_utc,value\n" + b"".join(
        f"2030-01-01T{h:02d}:00:00,1\n".encode() for h in range(24)
    )
    pin = {
        "kind": "series",
        "id": "x",
        "version": 1,
        "hash": hashlib.sha256(data).hexdigest(),
    }
    assert PC.write(tmp_path, pin, data)
    assert PC.read(tmp_path, pin) == (data, False)
    monkeypatch.setattr(PC, "MAX_PAYLOAD", len(data) - 1)
    assert PC.read(tmp_path, pin) == (None, True)


# ── L1 / L5: the import checks the copies before the row exists ────────────


@pytest.fixture
def pinned_bundle(client, api_project, session_ctx):
    name = api_project("lc-bundle")
    ref = _put(client)
    _save_with_refs(client, session_ctx, name, [ref])
    return _bundle(client, name), ref


def test_a_bundle_carries_the_copy(pinned_bundle):
    data, ref = pinned_bundle
    assert PC.member_name(_pin(ref)) in zipfile.ZipFile(io.BytesIO(data)).namelist()


def _refused(client, data, name, project_row, projects_root, says):  # noqa: F811
    r = _import(client, data, name)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_kind"] == "bundle_corrupt"
    assert says in r.json()["detail"]["message"]
    assert _left_behind(project_row, projects_root, name) == []


def test_a_bundle_whose_copy_does_not_match_its_pin_is_refused(
    client,
    pinned_bundle,
    project_row,
    projects_root,  # noqa: F811
):  # noqa: F811
    data, ref = pinned_bundle
    member = PC.member_name(_pin(ref))
    bad = _edited(data, {member: gzip.compress(b"# tz=UTC\ntimestamp_utc,value\n")})
    _refused(
        client,
        bad,
        "lc_bad_copy",
        project_row,
        projects_root,
        f"{member} does not match its pin",
    )
    assert _import(client, bad, "lc_bad_copy_dry").status_code == 400


def test_a_copy_no_pin_names_is_refused(
    client,
    pinned_bundle,
    project_row,
    projects_root,  # noqa: F811
):  # noqa: F811
    data, _ref = pinned_bundle
    stray = f"library/series/{'a' * 64}.csv.gz"
    _refused(
        client,
        _edited(data, {stray: gzip.compress(b"x")}),
        "lc_stray",
        project_row,
        projects_root,
        f"{stray} is not the copy of any pin",
    )


@pytest.mark.parametrize(
    "ident", ["x" * 129, "a\x00b", "a\nb"], ids=["too-long", "nul", "newline"]
)
def test_a_pin_that_is_not_a_library_name_is_refused(
    client,
    pinned_bundle,
    project_row,
    projects_root,  # noqa: F811
    request,
    ident,
):  # noqa: F811
    # An empty id is an unreadable sidecar, reported and not refused (as before).
    data, ref = pinned_bundle
    sidecar = {"schema": 1, "refs": [{"id": ident, "version": 1, "hash": ref["hash"]}]}
    _refused(
        client,
        _edited(data, {P.SIDECAR_NAME: json.dumps(sidecar)}),
        f"lc_badname_{request.node.callspec.id}",
        project_row,
        projects_root,
        "is not a Library name",
    )


def test_a_traversal_shaped_id_never_forms_a_path(tmp_path):
    """L1: a copy is named by its pin's sha256; the id and version never
    reach the path, and a pin whose hash is not a sha256 has no copy."""
    pin = {
        "kind": "series",
        "id": "../../../etc/passwd",
        "version": 1,
        "hash": "a" * 64,
    }
    assert (
        PC.path_for(tmp_path, pin)
        == tmp_path / "library" / "series" / f"{'a' * 64}.csv.gz"
    )
    for bad in ("../" + "a" * 61, "A" * 64, "a" * 63, None):
        assert PC.path_for(tmp_path, {**pin, "hash": bad}) is None
    assert PC.path_for(tmp_path, {**pin, "kind": "../x"}) is None


def test_a_pin_with_no_copy_imports_and_the_library_serves_it(client, pinned_bundle):
    data, ref = pinned_bundle
    r = _import(client, _edited(data, {PC.member_name(_pin(ref)): None}), "lc_nocopy")
    assert r.status_code == 200, r.text
    assert r.json()["library_issues"] == []


def test_a_bundle_without_its_sidecar_keeps_the_copies_its_config_names(
    client, pinned_bundle
):
    # The config's refs pin their copies too, so the copy is not a stray.
    data, ref = pinned_bundle
    r = _import(client, _edited(data, {P.SIDECAR_NAME: None}), "lc_nosidecar")
    assert r.status_code == 200, r.text


# ── L7: the newer-version notice is read-only ──────────────────────────────


def test_a_newer_library_version_changes_nothing_in_the_project(
    client, api_project, session_ctx, project_storage_dir
):
    name = api_project("lc-newer")
    _save_with_refs(client, session_ctx, name, [_put(client)])
    d = project_storage_dir(name)

    def state():
        return sorted(
            (p.relative_to(d).as_posix(), p.read_bytes())
            for p in [d / P.SIDECAR_NAME, *(d / "library").rglob("*")]
            if p.is_file()
        )

    before = state()
    _put(client, offset=5.0)
    issues = _open(client, name)["library_issues"]
    assert [i["reason"] for i in issues] == ["newer_in_library"]
    assert state() == before


# ── L3: the migration on load ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "lock, writes",
    [({"yours": True}, True), ({"yours": False}, False), (None, False)],
    ids=["lock-holder", "someone-else", "no-lock"],
)
def test_the_migration_writes_only_for_the_lock_holder(
    client,
    api_project,
    session_ctx,
    project_storage_dir,
    project_row,
    monkeypatch,
    lock,
    writes,
):
    from routers import projects

    name = api_project("lc-migrate")
    ref = _put(client)
    _save_with_refs(client, session_ctx, name, [ref])
    _drop_copies(project_storage_dir, name)
    monkeypatch.setattr(projects.local_mode, "is_local_mode", lambda: False)
    monkeypatch.setattr(projects, "_serialize_project_lock", lambda *a, **k: lock)
    from db.session import SessionLocal

    with SessionLocal() as db:
        row = project_row(name)
        got = projects._migrate_library_copies(db, row, project_storage_dir(name), None)
    assert got == ([PC.member_name(_pin(ref))] if writes else [])
    assert _copy(project_storage_dir, name, ref).is_file() is writes


def test_the_migration_fills_once_and_then_writes_nothing(
    client, api_project, session_ctx, project_storage_dir
):
    name = api_project("lc-once")
    ref = _put(client)
    _save_with_refs(client, session_ctx, name, [ref])
    _drop_copies(project_storage_dir, name)
    assert _open(client, name)["library_issues"] == []
    path = _copy(project_storage_dir, name, ref)
    assert path.is_file()
    stamp = path.stat().st_mtime_ns
    _open(client, name)
    assert path.stat().st_mtime_ns == stamp


# ── mutant survivors (project_copies unit level) ───────────────────────────


def _series_pin(data: bytes) -> dict:
    return {
        "kind": "series",
        "id": "x",
        "version": 1,
        "hash": hashlib.sha256(data).hexdigest(),
    }


_TARIFF = json.dumps(
    {
        "id": "nl-tou",
        "name": "NL TOU",
        "jurisdiction": "NL",
        "valid_from": "2030-01-01",
        "items": [
            {
                "id": "energy",
                "kind": "energy",
                "unit": "per_kwh",
                "periods": [{"name": "day", "rate": 0.20}],
            }
        ],
    }
).encode()
_CSV = b"# tz=naive\ntimestamp_utc,value\n2030-01-01T00:00:00,1\n"


def test_write_refuses_a_mismatch_and_skips_a_good_copy(tmp_path):
    pin = _series_pin(_CSV)
    assert PC.write(tmp_path, pin, _CSV + b"x") is False
    assert PC.path_for(tmp_path, pin).exists() is False
    assert PC.write(tmp_path, {**pin, "hash": "nope"}, _CSV) is False
    assert PC.write(tmp_path, pin, _CSV) is True
    assert PC.write(tmp_path, pin, _CSV) is False


def test_a_truncated_or_oversized_copy_reads_as_modified(tmp_path, monkeypatch):
    pin = _series_pin(_CSV)
    path = PC.path_for(tmp_path, pin)
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(_CSV, mtime=0)[:-12])
    assert PC.read(tmp_path, pin) == (None, True)
    path.write_bytes(gzip.compress(_CSV, mtime=0))
    monkeypatch.setattr(PC, "MAX_PAYLOAD", path.stat().st_size - 1)
    assert PC.read(tmp_path, pin) == (None, True)


def test_an_item_copy_is_read_as_is_not_unzipped(tmp_path):
    raw = gzip.compress(_TARIFF, mtime=0)
    pin = {
        "kind": "tariff",
        "id": "t",
        "version": 1,
        "hash": hashlib.sha256(raw).hexdigest(),
    }
    assert PC.write(tmp_path, pin, raw)
    # Never unzipped: gzip bytes are not a tariff, so the copy is not used.
    assert PC.read(tmp_path, pin) == (None, True)
    plain = {**pin, "hash": hashlib.sha256(_TARIFF).hexdigest()}
    assert PC.write(tmp_path, plain, _TARIFF)
    assert PC.read(tmp_path, plain) == (_TARIFF, False)


@pytest.mark.parametrize(
    "ident, ok",
    [
        ("x" * 128, True),
        ("", False),
        ("a b", True),
        (7, False),
        ("a\x7fb", False),
        ("a\x1fb", False),
    ],
    ids=["at-cap", "empty", "space", "not-a-string", "del", "unit-sep"],
)
def test_the_id_rule_boundaries(tmp_path, ident, ok):
    pin = {**_series_pin(_CSV), "id": ident}
    assert (PC.staged_problems(tmp_path, [], [pin]) == []) is ok


def test_a_directory_entry_and_a_duplicate_pin_are_not_problems_twice(tmp_path):
    pin = _series_pin(_CSV)
    path = PC.path_for(tmp_path, pin)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"junk")
    members = ["library/", "library/series/", PC.member_name(pin)]
    assert PC.staged_problems(tmp_path, members, [pin, dict(pin)]) == [
        f"{PC.member_name(pin)} does not match its pin in library_refs.json, "
        "or is not a valid payload."
    ]


def test_fill_skips_a_good_copy_and_a_shipped_common_item(tmp_path, monkeypatch):
    from services.library import common
    from services.library import series_store

    pin = _series_pin(_CSV)
    assert PC.write(tmp_path, pin, _CSV)
    calls = []
    monkeypatch.setattr(series_store, "payload_bytes", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(common, "is_common", lambda kind, name: True)
    item = {"kind": "tariff", "id": "c", "version": 1, "hash": "b" * 64}
    assert PC.fill(None, None, tmp_path, [pin, item]) == []
    assert calls == []


def test_a_pin_of_an_unknown_kind_has_no_copy_to_read_write_or_fill(
    tmp_path, monkeypatch
):
    from services.library import items

    pin = {**_series_pin(_CSV), "kind": "bogus"}
    assert PC.read(tmp_path, pin) == (None, False)
    assert PC.write(tmp_path, pin, _CSV) is False
    calls = []
    monkeypatch.setattr(items, "payload_bytes", lambda *a, **k: calls.append(a) or _CSV)
    assert PC.fill(None, None, tmp_path, [pin]) == []
    assert calls == []


def test_fill_copies_an_item_and_a_series_named_like_a_common_item(
    tmp_path, monkeypatch
):
    from services.library import common, items, series_store

    body = _TARIFF
    item = {
        "kind": "tariff",
        "id": "mine",
        "version": 1,
        "hash": hashlib.sha256(body).hexdigest(),
    }
    series = {**_series_pin(_CSV), "id": "c"}
    monkeypatch.setattr(common, "is_common", lambda kind, name: name == "c")
    monkeypatch.setattr(items, "payload_bytes", lambda *a, **k: body)
    monkeypatch.setattr(series_store, "payload_bytes", lambda *a, **k: _CSV)
    written = PC.fill(None, None, tmp_path, [item, series])
    assert sorted(written) == sorted([PC.member_name(item), PC.member_name(series)])
    assert PC.read(tmp_path, item) == (body, False)


def test_an_item_pin_resolves_from_its_copy_and_a_modified_one_is_named(
    tmp_path, monkeypatch
):
    from models.commercial import LibraryItemRef
    from services.library import items as I

    raw = _TARIFF
    pin = {
        "kind": "tariff",
        "id": "t",
        "version": 1,
        "hash": hashlib.sha256(raw).hexdigest(),
    }
    assert PC.write(tmp_path, pin, raw)
    ref = LibraryItemRef(**pin)

    def gone(*a, **k):
        raise S.LibraryRefNotFound("no such row")

    monkeypatch.setattr(I, "payload_bytes", gone)
    assert I.resolve(None, None, ref, project_dir=tmp_path) == json.loads(_TARIFF)
    PC.path_for(tmp_path, pin).write_bytes(_TARIFF + b" ")
    with pytest.raises(S.LibraryRefStale, match="copy of tariff 't' v1 was modified"):
        I.resolve(None, None, ref, project_dir=tmp_path)
    with pytest.raises(S.LibraryRefNotFound):
        I.resolve(None, None, ref)


# ── GK B3 NOT OK @a366899a ─────────────────────────────────────────────────


def test_a_re_pinned_project_exports_a_bundle_its_import_accepts(
    client, api_project, session_ctx, project_storage_dir
):
    name = api_project("lc-repin")
    v1 = _put(client)
    _save_with_refs(client, session_ctx, name, [v1])
    v2 = _put(client, offset=1.0)
    assert v2["version"] == v1["version"] + 1
    _save_with_refs(client, session_ctx, name, [v2])
    assert not _copy(project_storage_dir, name, v1).exists()
    assert _copy(project_storage_dir, name, v2).is_file()
    r = _import(client, _bundle(client, name), "lc_repin_again")
    assert r.status_code == 200, r.text


def test_prune_keeps_the_pins_copies_and_a_temp_file_in_flight(tmp_path):
    import os
    import time

    pin = _series_pin(_CSV)
    assert PC.write(tmp_path, pin, _CSV)
    folder = PC.path_for(tmp_path, pin).parent
    stray = folder / f"{'b' * 64}.csv.gz"
    stray.write_bytes(b"old")
    fresh, stale = folder / "x.tmp", folder / "y.tmp"
    fresh.write_bytes(b"")
    stale.write_bytes(b"")
    old = time.time() - PC._TMP_GRACE_S - 5
    os.utime(stale, (old, old))
    assert PC.prune(tmp_path, [pin]) == sorted(
        f"library/series/{p.name}" for p in (stray, stale)
    )
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [PC.path_for(tmp_path, pin).name, "x.tmp"]
    )
    assert PC.prune(tmp_path / "none", [pin]) == []


def test_an_unreadable_sidecar_prunes_nothing(tmp_path):
    pin = _series_pin(_CSV)
    assert PC.write(tmp_path, pin, _CSV)
    (tmp_path / P.SIDECAR_NAME).write_text("{not json")
    assert P.write_copies(None, None, tmp_path) == []
    assert PC.path_for(tmp_path, pin).is_file()


def _pinned(kind: str, data: bytes) -> dict:
    return {
        "kind": kind,
        "id": "x",
        "version": 1,
        "hash": hashlib.sha256(data).hexdigest(),
    }


@pytest.mark.parametrize(
    "kind, data",
    [
        ("series", b"# tz=naive\ntimestamp_utc,value\n2030-01-01T00:00:00,nan\n"),
        ("series", b"# tz=naive\ntimestamp_utc,value\n2030-01-01T00:00:00,inf\n"),
        ("series", b"# tz=Not/AZone\ntimestamp_utc,value\n2030-01-01T00:00:00,1\n"),
        ("series", b"# tz=naive\ntimestamp_utc,value\nnot a time,1\n"),
        ("tariff", b"{not json"),
        ("tariff", b'{"id": "c"}'),
    ],
    ids=["nan", "inf", "bad-tz", "bad-index", "not-json", "not-a-tariff"],
)
def test_a_copy_whose_contents_are_not_a_valid_payload_is_never_used(
    tmp_path, kind, data
):
    # The hash alone vouches for nothing: the bundle brings both pin and copy.
    pin = _pinned(kind, data)
    assert PC.write(tmp_path, pin, data)
    assert PC.read(tmp_path, pin) == (None, True)
    members = [PC.member_name(pin)]
    [problem] = PC.staged_problems(tmp_path, members, [pin])
    assert "is not a valid payload" in problem


def test_a_bundle_whose_copy_is_not_finite_is_refused(
    client,
    pinned_bundle,
    project_row,
    projects_root,  # noqa: F811
):
    data, ref = pinned_bundle
    bad = b"# tz=UTC\ntimestamp_utc,value\n2030-01-01T00:00:00Z,nan\n"
    sha = hashlib.sha256(bad).hexdigest()
    pin = {**_pin(ref), "hash": sha}
    sidecar = {
        "schema": 1,
        "refs": [{"id": ref["id"], "version": ref["version"], "hash": sha}],
    }
    edited = _edited(
        data,
        {
            PC.member_name(_pin(ref)): None,
            PC.member_name(pin): gzip.compress(bad),
            P.SIDECAR_NAME: json.dumps(sidecar).encode(),
        },
    )
    r = _import(client, edited, "lc_nan")
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_kind"] == "bundle_corrupt"
    assert _left_behind(project_row, projects_root, "lc_nan") == []


def test_a_series_served_by_its_copy_alone_is_a_notice_not_a_fault(
    tmp_path, monkeypatch
):
    pin = _series_pin(_CSV)
    monkeypatch.setattr(S, "ref_for", lambda *a, **k: None)
    [issue] = P._copy_only(None, None, pin)
    assert (issue["code"], issue["reason"]) == (P.COPY_ONLY_CODE, "copy_only")
    assert P.COPY_ONLY_CODE in P.NOTICE_CODES
    monkeypatch.setattr(S, "ref_for", lambda *a, **k: object())
    assert P._copy_only(None, None, pin) == []
    assert P._copy_only(None, None, {**pin, "kind": "tariff"}) == []
