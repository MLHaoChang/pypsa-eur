"""
Library series delete and pin query (IC engine ask "delete_series / pinned_by",
owner commission 2026-10-13; retires the decision study's C3e WORKAROUND,
`study.compile._ic_internals_delete_series`).

  * `pinned_by(name, *, project_dirs)` names every project directory that pins
    the series: a `library_refs.json` series pin (`via="sidecar"`), a ref in
    its `solver_config.json` (`via="config"`), or a pin source it cannot read
    and so might pin it (`via="unreadable"`);
  * `delete_series(db, org_id, name, *, project_dirs)` deletes EVERY version
    of the org's series, never while a project pins it or a pin source is
    unreadable; a payload file goes only when no remaining row of the org
    points at it (two names cut to the same 48-character slug with the same
    content share one content-addressed file);
  * `project_dirs` is required: a delete never skips the pin check.

Written red first: neither function existed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
import uuid

import numpy as np
import pandas as pd
import pytest


@pytest.fixture()
def db(_auth_db):
    _engine, session_local = _auth_db
    with session_local() as s:
        yield s
        s.rollback()


@pytest.fixture()
def org(seeded_identity):
    return seeded_identity["org_id"]


def _prices(level: float = 40.0) -> pd.Series:
    idx = pd.date_range("2030-01-01", periods=48, freq="h", tz="UTC")
    return pd.Series(np.full(len(idx), level), index=idx)


def _project(tmp_path, name: str, *, pins=None, config=None):
    d = tmp_path / "projects" / name
    d.mkdir(parents=True)
    if pins is not None:
        (d / "library_refs.json").write_text(pins if isinstance(pins, str)
                                             else json.dumps(pins), encoding="utf-8")
    if config is not None:
        (d / "solver_config.json").write_text(config if isinstance(config, str)
                                              else json.dumps(config), encoding="utf-8")
    return d


def _pin_doc(ref, kind=None):
    pin = {"id": ref.id, "version": ref.version, "hash": ref.hash}
    if kind is None:
        return {"schema": 1, "refs": [pin]}
    return {"schema": 2, "refs": [{"kind": kind, **pin}]}


def _put(db, org, name, level, root):
    from services.library import series_store as S

    return S.put_series(db, org, name, _prices(level), {"source": "decision_study"}, root=root)


# ── pinned_by ─────────────────────────────────────────────────────────────

def test_pinned_by_names_each_way_a_project_can_pin_a_series(db, org, tmp_path):
    from services.library import series_store as S

    root = tmp_path / "lib"
    ref = _put(db, org, "export_a", 40.0, root)
    other = _put(db, org, "export_b", 41.0, root)
    free = _project(tmp_path, "free")
    by_pin = _project(tmp_path, "by_pin", pins=_pin_doc(ref))
    by_cfg = _project(tmp_path, "by_cfg",
                      config={"commercial": {"export_price_ref": ref.model_dump()}})
    pins_other = _project(tmp_path, "pins_other", pins=_pin_doc(other),
                          config={"commercial": {"export_price_ref": other.model_dump()}})
    got = S.pinned_by("export_a", project_dirs=[free, by_pin, by_cfg, pins_other])
    assert got == [S.SeriesPin(project_dir=by_pin, via="sidecar"),
                   S.SeriesPin(project_dir=by_cfg, via="config")]
    assert S.pinned_by("export_a", project_dirs=[free, pins_other]) == []
    assert S.pinned_by("export_a", project_dirs=[]) == []


def test_a_library_item_pin_of_the_same_name_is_not_a_series_pin(db, org, tmp_path):
    from services.library import series_store as S

    ref = _put(db, org, "same_name", 40.0, tmp_path / "lib")
    d = _project(tmp_path, "tariff_pin", pins=_pin_doc(ref, kind="tariff"))
    assert S.pinned_by("same_name", project_dirs=[d]) == []
    d2 = _project(tmp_path, "series_pin_v2", pins=_pin_doc(ref, kind="series"))
    assert S.pinned_by("same_name", project_dirs=[d2]) == [S.SeriesPin(d2, "sidecar")]


@pytest.mark.parametrize("pins,config", [("{not json", None), (None, "{not json"),
                                         ({"schema": 9, "refs": []}, None)])
def test_an_unreadable_pin_source_might_pin_the_series(db, org, tmp_path, pins, config):
    from services.library import series_store as S

    _put(db, org, "export_a", 40.0, tmp_path / "lib")
    d = _project(tmp_path, "broken", pins=pins, config=config)
    assert S.pinned_by("export_a", project_dirs=[d]) == [S.SeriesPin(d, "unreadable")]


def test_a_missing_project_directory_pins_nothing(tmp_path):
    from services.library import series_store as S

    assert S.pinned_by("export_a", project_dirs=[tmp_path / "gone"]) == []


# ── delete_series ─────────────────────────────────────────────────────────

def test_delete_removes_every_version_and_its_payload_files(db, org, tmp_path):
    from services.library import series_store as S
    from services.storage_paths import library_file

    root = tmp_path / "lib"
    v1 = _put(db, org, "export_a", 40.0, root)
    v2 = _put(db, org, "export_a", 41.0, root)
    assert (v1.version, v2.version) == (1, 2)
    files = [library_file(root, org, r.path) for r in _rows(db, org, "export_a")]
    assert all(f.exists() for f in files) and len(set(files)) == 2

    out = S.delete_series(db, org, "export_a", project_dirs=[_project(tmp_path, "free")],
                          root=root)
    assert out == S.SeriesDeleted(deleted_versions=2, kept=None, pinned_by=())
    assert S.latest_ref(db, org, "export_a") is None
    with pytest.raises(S.LibraryRefNotFound):
        S.resolve(db, org, v1, root=root)
    assert not any(f.exists() for f in files)
    # Idempotent: nothing left to delete is not an error.
    assert S.delete_series(db, org, "export_a", project_dirs=[], root=root) == \
        S.SeriesDeleted(deleted_versions=0, kept=None, pinned_by=())


@pytest.mark.parametrize("how,kept", [("sidecar", "series_pinned"), ("config", "series_pinned"),
                                      ("unreadable", "series_pins_unreadable")])
def test_a_pinned_series_is_kept_whole(db, org, tmp_path, how, kept):
    from services.library import series_store as S

    root = tmp_path / "lib"
    ref = _put(db, org, "export_a", 40.0, root)
    d = {"sidecar": lambda: _project(tmp_path, "p", pins=_pin_doc(ref)),
         "config": lambda: _project(tmp_path, "p", config={"x": [ref.model_dump()]}),
         "unreadable": lambda: _project(tmp_path, "p", pins="{not json")}[how]()
    out = S.delete_series(db, org, "export_a", project_dirs=[d], root=root)
    assert out == S.SeriesDeleted(deleted_versions=0, kept=kept,
                                  pinned_by=(S.SeriesPin(d, how),))
    assert S.resolve(db, org, ref, root=root).iloc[0] == 40.0


def test_a_payload_file_another_row_shares_is_kept(db, org, tmp_path):
    """Two names cut to the same 48-character slug, same content: one file."""
    from services.library import series_store as S
    from services.storage_paths import library_file

    root = tmp_path / "lib"
    stem = "x" * 48
    a = _put(db, org, stem + "_study_a", 40.0, root)
    b = _put(db, org, stem + "_study_b", 40.0, root)
    (row_a,), (row_b,) = _rows(db, org, a.id), _rows(db, org, b.id)
    assert row_a.path == row_b.path
    out = S.delete_series(db, org, a.id, project_dirs=[], root=root)
    assert out.deleted_versions == 1
    assert library_file(root, org, row_b.path).exists()
    assert S.resolve(db, org, b, root=root).iloc[0] == 40.0


def test_delete_touches_only_this_org(db, org, tmp_path):
    from db.models import Organization
    from services.library import series_store as S

    root = tmp_path / "lib"
    other = Organization(id=uuid.uuid4(), name=f"other-{uuid.uuid4().hex[:6]}",
                         created_at=datetime.now(timezone.utc))
    db.add(other)
    db.commit()
    mine = _put(db, org, "export_a", 40.0, root)
    theirs = _put(db, other.id, "export_a", 40.0, root)
    assert S.delete_series(db, org, "export_a", project_dirs=[], root=root).deleted_versions == 1
    assert S.latest_ref(db, org, mine.id) is None
    assert S.resolve(db, other.id, theirs, root=root).iloc[0] == 40.0


def test_delete_never_skips_the_pin_check(db, org, tmp_path):
    from services.library import series_store as S

    _put(db, org, "export_a", 40.0, tmp_path / "lib")
    with pytest.raises(TypeError):
        S.delete_series(db, org, "export_a", root=tmp_path / "lib")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="project_dirs"):
        S.delete_series(db, org, "export_a", project_dirs=None, root=tmp_path / "lib")
    assert S.latest_ref(db, org, "export_a") is not None


def _rows(db, org, name):
    from sqlalchemy import select

    from db.models import LibraryItem

    return db.scalars(select(LibraryItem).where(
        LibraryItem.org_id == org, LibraryItem.kind == "series",
        LibraryItem.name == name).order_by(LibraryItem.version)).all()
