"""REPLAY: an older sealed copy of a mutable record is not the current one.

Issue 02 (P1). save() overwrites the mutable kinds (operation, ui_action,
conversation_context) in place, and the record MAC (#850) binds a record to
its id but not to a version. So anyone who could write the record store could
put back an older envelope sealed for the same id within its 24 hours, and it
read as valid. Each save now takes the next value of a high-water mark kept in
the DB, outside the store, and seals it into the record; load() accepts only a
record whose mark equals the DB's.

Threat model: an attacker who can write the record store (even restore the
whole store directory) but cannot read SECRET_KEY or the key file, and cannot
write the DB. Rolling back the store and the DB together (a backup restore) is
out of scope.
"""

import json
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

import app_paths
from db.models import AssuranceRecordSeq, User
from db import session as db_session
from services import assurance_records as records

MUTABLE = sorted(records.MUTABLE_KINDS)


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))


@pytest.fixture
def owner():
    user_id = uuid.uuid4()
    with db_session.SessionLocal() as db:
        db.add(User(id=user_id, email=f"{user_id}@replay.test", status="active",
                    created_at=datetime.now(UTC)))
        db.commit()
    yield str(user_id)
    with db_session.SessionLocal() as db:
        db.query(AssuranceRecordSeq).filter_by(owner_id=user_id).delete()
        db.query(User).filter_by(id=user_id).delete()
        db.commit()


def record(owner, kind, version, **extra):
    return {"version": 1, "record_kind": kind, "owner_id": owner,
            "expires_at": time.time() + 3600, "note": f"v{version}", **extra}


def path_of(owner, record_id):
    return app_paths.app_data_dir() / "assistant_assurance" / owner / f"{record_id}.json"


def unavailable(owner, record_id, kind):
    with pytest.raises(HTTPException) as refused:
        records.load(owner, record_id, kind)
    assert refused.value.status_code == 409
    return refused.value


def mark_row(owner, record_id):
    with db_session.SessionLocal() as db:
        return db.get(AssuranceRecordSeq, (uuid.UUID(owner), uuid.UUID(record_id)))


def set_mark(owner, record_id, **values):
    with db_session.SessionLocal() as db:
        db.query(AssuranceRecordSeq).filter_by(
            owner_id=uuid.UUID(owner), record_id=uuid.UUID(record_id)
        ).update(values)
        db.commit()


def two_versions(owner, kind):
    """Save v1 and v2 of one id; return the id, v1's bytes and v2."""
    rid = str(uuid.uuid4())
    records.save(record(owner, kind, 1), rid)
    v1 = path_of(owner, rid).read_bytes()
    v2 = record(owner, kind, 2)
    records.save(v2, rid)
    # Positive control: the current version reads, without its mark.
    assert records.load(owner, rid, kind) == v2
    return rid, v1, v2


@pytest.mark.parametrize("kind", MUTABLE)
def test_older_sealed_copy_put_back_is_unavailable(owner, kind):
    rid, v1, _ = two_versions(owner, kind)
    path_of(owner, rid).write_bytes(v1)
    unavailable(owner, rid, kind)


@pytest.mark.parametrize("kind", MUTABLE)
def test_whole_store_restored_from_an_older_copy_is_unavailable(owner, kind, tmp_path):
    rid = str(uuid.uuid4())
    records.save(record(owner, kind, 1), rid)
    store = path_of(owner, rid).parent
    shutil.copytree(store, tmp_path / "backup")
    records.save(record(owner, kind, 2), rid)
    shutil.rmtree(store)
    shutil.copytree(tmp_path / "backup", store)
    unavailable(owner, rid, kind)


@pytest.mark.parametrize("kind", MUTABLE)
def test_a_mark_rolled_back_below_the_file_is_unavailable(owner, kind):
    rid, _, _ = two_versions(owner, kind)
    set_mark(owner, rid, seq=1)
    unavailable(owner, rid, kind)


@pytest.mark.parametrize("kind", MUTABLE)
def test_a_copy_from_another_epoch_is_unavailable(owner, kind):
    rid, _, _ = two_versions(owner, kind)
    set_mark(owner, rid, epoch=uuid.uuid4())
    unavailable(owner, rid, kind)


@pytest.mark.parametrize("kind", MUTABLE)
def test_a_record_with_no_mark_is_unavailable(owner, kind):
    """A mutable record sealed before marks existed reads as unavailable."""
    rid = str(uuid.uuid4())
    path = path_of(owner, rid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records.seal(record(owner, kind, 1), rid)))
    unavailable(owner, rid, kind)
    # ...and so does a marked record whose DB mark is gone.
    rid, _, _ = two_versions(owner, kind)
    with db_session.SessionLocal() as db:
        db.delete(db.get(AssuranceRecordSeq, (uuid.UUID(owner), uuid.UUID(rid))))
        db.commit()
    unavailable(owner, rid, kind)


@pytest.mark.parametrize("kind", MUTABLE)
def test_a_crash_after_the_mark_commit_never_accepts_the_old_file(owner, kind, monkeypatch):
    rid = str(uuid.uuid4())
    records.save(record(owner, kind, 1), rid)
    real = records.os.replace

    def crash(src, dst):
        if str(dst).endswith(rid + ".json"):
            raise OSError("killed between the mark commit and the file write")
        return real(src, dst)

    monkeypatch.setattr(records.os, "replace", crash)
    with pytest.raises(OSError):
        records.save(record(owner, kind, 2), rid)
    monkeypatch.setattr(records.os, "replace", real)
    assert mark_row(owner, rid).seq == 2
    unavailable(owner, rid, kind)
    # The next save writes the current version again.
    v3 = record(owner, kind, 3)
    records.save(v3, rid)
    assert records.load(owner, rid, kind) == v3


@pytest.mark.parametrize("kind", MUTABLE)
def test_two_concurrent_saves_take_distinct_marks(owner, kind):
    rid = str(uuid.uuid4())
    records.save(record(owner, kind, 0), rid)
    errors = []
    barrier = threading.Barrier(2)

    def write(version):
        try:
            barrier.wait()
            records.save(record(owner, kind, version), rid)
        except Exception as exc:  # noqa: BLE001 -- surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(v,)) for v in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert mark_row(owner, rid).seq == 3
    assert records.load(owner, rid, kind)["note"] in {"v1", "v2"}


@pytest.mark.parametrize("kind", MUTABLE)
def test_a_pruned_mark_starts_a_fresh_chain(owner, kind):
    rid = str(uuid.uuid4())
    records.save(record(owner, kind, 1), rid)
    v1 = path_of(owner, rid).read_bytes()
    with db_session.SessionLocal() as db:
        db.delete(db.get(AssuranceRecordSeq, (uuid.UUID(owner), uuid.UUID(rid))))
        db.commit()
    v2 = record(owner, kind, 2)
    records.save(v2, rid)
    assert mark_row(owner, rid).seq == 1  # same seq as v1, another epoch
    assert records.load(owner, rid, kind) == v2
    path_of(owner, rid).write_bytes(v1)
    unavailable(owner, rid, kind)


def age(path):
    old = time.time() - records.RETENTION_SECONDS - 60
    import os

    os.utime(path, (old, old))


def test_the_sweep_drops_marks_with_their_records(owner):
    expired = str(uuid.uuid4())
    records.save(record(owner, "ui_action", 1, expires_at=time.time() + 1), expired)
    reserved = str(uuid.uuid4())
    records.save(record(owner, "operation", 1, outcome="reserved",
                        expires_at=time.time() + 1), reserved)
    age(path_of(owner, expired))
    age(path_of(owner, reserved))
    time.sleep(1.1)
    records.save(record(owner, "ui_action", 1), str(uuid.uuid4()))
    assert not path_of(owner, expired).exists()
    assert mark_row(owner, expired) is None
    # An unresolved reservation keeps its file, so it keeps its mark.
    assert path_of(owner, reserved).exists()
    assert mark_row(owner, reserved) is not None


def test_immutable_kinds_take_no_mark_and_stay_write_once(owner):
    rid = str(uuid.uuid4())
    evidence = record(owner, "evidence", 1)
    records.save(evidence, rid)
    before = path_of(owner, rid).read_bytes()
    records.save(evidence, rid)  # an identical re-save is a no-op
    assert path_of(owner, rid).read_bytes() == before
    assert records.MARK_KEY not in json.loads(before)["record"]
    assert mark_row(owner, rid) is None
    with pytest.raises(HTTPException) as refused:
        records.save(record(owner, "evidence", 2), rid)
    assert refused.value.status_code == 409


@pytest.mark.parametrize("kind", MUTABLE)
def test_a_read_during_a_save_in_flight_gets_the_new_version(owner, kind, monkeypatch):
    """Readers take no lock; one that meets the file a step behind its mark
    waits for the save to land rather than reading as unavailable."""
    rid = str(uuid.uuid4())
    records.save(record(owner, kind, 1), rid)
    real = records.os.replace
    committed = threading.Event()

    def slow(src, dst):
        if str(dst).endswith(rid + ".json"):
            committed.set()
            time.sleep(0.2)
        return real(src, dst)

    monkeypatch.setattr(records.os, "replace", slow)
    v2 = record(owner, kind, 2)
    writer = threading.Thread(target=records.save, args=(v2, rid))
    writer.start()
    assert committed.wait(5)
    assert mark_row(owner, rid).seq == 2  # the mark is ahead of the file here
    assert records.load(owner, rid, kind) == v2
    writer.join()
