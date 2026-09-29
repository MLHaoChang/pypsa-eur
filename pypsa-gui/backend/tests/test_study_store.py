"""
Decision-study persistence (MVP-1 S1): one sidecar `studies/<study_id>.json`
inside the base project's storage directory, written atomically.

The id is a uuid4 hex and nothing else: it is the only client input that
reaches a path, so the store refuses any other shape as "not found" before it
touches the disk.
"""
from __future__ import annotations

import json
from datetime import datetime, UTC

import pytest

from models.study import DecisionStudy
from services.study import store

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _study(name="Site A", study_id=None):
    return DecisionStudy(
        study_id=study_id or store.new_study_id(), name=name,
        question_id="bess_site", base_project="p-uuid",
        created_at=NOW, updated_at=NOW,
    )


def test_new_study_id_is_uuid4_hex():
    import uuid

    sid = store.new_study_id()
    assert len(sid) == 32 and sid == sid.lower()
    assert uuid.UUID(hex=sid).version == 4


def test_save_writes_the_sidecar_in_the_project_studies_dir(tmp_path):
    s = _study()
    store.save_study(tmp_path, s)
    path = tmp_path / store.SIDECAR_DIR / f"{s.study_id}.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["study_id"] == s.study_id
    assert store.SIDECAR_DIR == "studies"


def test_save_goes_through_atomic_io(tmp_path, monkeypatch):
    import services.atomic_io as aio

    calls = []
    real = aio.atomic_write_text

    def spy(path, text, encoding="utf-8"):
        calls.append(path)
        return real(path, text, encoding=encoding)

    monkeypatch.setattr(store, "atomic_write_text", spy)
    s = _study()
    store.save_study(tmp_path, s)
    assert calls == [tmp_path / "studies" / f"{s.study_id}.json"]


def test_load_roundtrips_and_list_returns_every_study(tmp_path):
    a, b = _study("A"), _study("B")
    store.save_study(tmp_path, a)
    store.save_study(tmp_path, b)
    assert store.load_study(tmp_path, a.study_id) == a
    assert {s.study_id for s in store.list_studies(tmp_path)} == {
        a.study_id, b.study_id}


def test_list_is_empty_for_a_project_without_studies(tmp_path):
    assert store.list_studies(tmp_path) == []


@pytest.mark.parametrize("bad", [
    "../network", "0123", "X" * 32, "0123456789abcdef0123456789abcde/",
    "0123456789ABCDEF0123456789ABCDEF",
])
def test_a_malformed_id_is_not_found_without_touching_disk(tmp_path, bad):
    with pytest.raises(store.StudyNotFound):
        store.load_study(tmp_path, bad)
    with pytest.raises(store.StudyNotFound):
        store.delete_study(tmp_path, bad)


def test_unknown_id_is_not_found(tmp_path):
    with pytest.raises(store.StudyNotFound):
        store.load_study(tmp_path, store.new_study_id())


def test_delete_removes_only_that_study(tmp_path):
    a, b = _study("A"), _study("B")
    store.save_study(tmp_path, a)
    store.save_study(tmp_path, b)
    store.delete_study(tmp_path, a.study_id)
    with pytest.raises(store.StudyNotFound):
        store.load_study(tmp_path, a.study_id)
    assert store.load_study(tmp_path, b.study_id) == b


def test_a_file_whose_id_disagrees_with_its_name_is_refused(tmp_path):
    """A study copied under another id must not answer for that id."""
    a = _study("A")
    store.save_study(tmp_path, a)
    other = store.new_study_id()
    (tmp_path / "studies" / f"{a.study_id}.json").rename(
        tmp_path / "studies" / f"{other}.json")
    with pytest.raises(store.StudyUnreadable):
        store.load_study(tmp_path, other)


def test_an_oversized_study_is_refused_and_the_old_file_survives(tmp_path):
    s = _study()
    store.save_study(tmp_path, s)
    big = s.model_copy(update={"intake": {"blob": "x" * (store.MAX_STUDY_BYTES + 1)}})
    with pytest.raises(store.StudyTooLarge):
        store.save_study(tmp_path, big)
    assert store.load_study(tmp_path, s.study_id) == s
