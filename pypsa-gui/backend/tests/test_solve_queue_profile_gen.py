"""A profile generation run is a job in the EXISTING solve queue (Task 6).

The spec is explicit that there is no second job system: a generated profile
can mean a minutes-long CDS download, so it rides the same FIFO, the same
status/abort machinery and the same boot reconciliation as a solve — the
`profile_gen` kind just picks a different runner. The runner never touches a
network: it writes a PENDING artifact (`<storage_dir>/profiles_pending/
<job_id>.nc` + `<job_id>.json`); applying it to a network is Task 5's
`assign_profile`, reached only through the apply endpoint.

THE ABORT TEST IS THE ONE THAT MATTERS. A cutout download is the long pole;
a queue that can start one but not stop it forces the user to kill the
process. The stop_event the queue already owns reaches the downloader
(`ensure_cutout(stop_event=…)` → `CutoutDownloadAborted`) and is polled
between stages — the abort here proves the whole chain.
"""
import json
import time
import uuid

import pytest

from db.models import SolveJobRow
from services import solve_queue as sq
from services.profile_gen import job as job_mod


def _wait(predicate, timeout=60.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture(autouse=True)
def _clean_queue():
    sq.solve_queue.reset_for_tests()
    yield
    sq.solve_queue.reset_for_tests()


def _spec(**over):
    spec = {
        "points": [{"id": "site-a", "lon": 8.0, "lat": 50.0},
                   {"id": "site-b", "lon": 9.5, "lat": 51.2}],
        "technology": "wind",
        "preset": "default",
        "source": "synthetic",
        "year": 2023,
    }
    spec.update(over)
    return spec


def _enqueue(storage_dir, spec=None):
    return sq.solve_queue.enqueue(
        "profile-project",
        project_key="profile-project",
        storage_dir=str(storage_dir),
        solver_config_json=json.dumps(spec if spec is not None else _spec()),
        kind=sq.KIND_PROFILE_GEN,
    )


def _finished(job_id, timeout=60.0):
    return _wait(
        lambda: sq.solve_queue.get_job(job_id)["status"] in sq._TERMINAL,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# the kind
# ---------------------------------------------------------------------------


def test_profile_gen_is_a_registered_kind():
    assert sq.KIND_PROFILE_GEN == "profile_gen"
    assert sq.KIND_PROFILE_GEN in sq.KINDS


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_a_profile_job_runs_to_completion_and_writes_a_pending_artifact(tmp_path):
    job = _enqueue(tmp_path)

    assert _finished(job.id)
    done = sq.solve_queue.get_job(job.id)
    assert done["status"] == "completed", done["error"]
    assert done["kind"] == "profile_gen"

    pending = tmp_path / "profiles_pending"
    nc = pending / f"{job.id}.nc"
    meta = pending / f"{job.id}.json"
    assert nc.exists() and meta.exists()

    import xarray as xr

    with xr.open_dataset(nc) as ds:
        assert ds.sizes["time"] == 8760  # one non-leap weather year
        assert set(ds.data_vars) == {"site-a", "site-b"}

    record = json.loads(meta.read_text())
    assert record["technology"] == "wind"
    assert record["source"] == "synthetic"
    assert record["weather_year"] == 2023
    assert record["job_id"] == str(job.id)
    assert record["points"] == ["site-a", "site-b"]


def test_the_network_is_never_touched(tmp_path):
    job = _enqueue(tmp_path)
    assert _finished(job.id)
    assert not (tmp_path / "network.nc").exists()


# ---------------------------------------------------------------------------
# abort
# ---------------------------------------------------------------------------


def test_aborting_a_profile_job_stops_it_and_writes_no_artifact(tmp_path,
                                                                monkeypatch):
    import threading

    from services.profile_gen.cutout_cache import CutoutDownloadAborted

    entered = threading.Event()

    def slow_generate(*args, **kwargs):
        entered.set()
        # Honour the stop event the way a real download would: the queue's
        # abort() sets job.stop_event; the runner passes it down and polls it
        # between stages. Here we poll the job's own event until abort lands.
        deadline = time.time() + 30
        while time.time() < deadline:
            for j in sq.solve_queue._jobs.values():
                if j.stop_event is not None and j.stop_event.is_set():
                    raise CutoutDownloadAborted("stop requested")
            time.sleep(0.05)
        raise AssertionError("abort never reached the running job")

    monkeypatch.setattr(job_mod, "generate_profile", slow_generate)

    job = _enqueue(tmp_path)
    assert _wait(lambda: entered.is_set(), timeout=30)
    sq.solve_queue.abort(job.id)

    assert _finished(job.id, timeout=30)
    done = sq.solve_queue.get_job(job.id)
    assert done["status"] == "aborted"
    pending = tmp_path / "profiles_pending"
    leftovers = list(pending.glob(f"{job.id}.*")) if pending.exists() else []
    assert leftovers == []


def test_an_aborted_download_leaves_no_partial_artifact(tmp_path, monkeypatch):
    from services.profile_gen.cutout_cache import CutoutDownloadAborted

    def aborted_download(*args, **kwargs):
        raise CutoutDownloadAborted("stop requested")

    monkeypatch.setattr(job_mod, "ensure_cutout", aborted_download)
    job = _enqueue(tmp_path, _spec(source="atlite"))

    assert _finished(job.id)
    done = sq.solve_queue.get_job(job.id)
    assert done["status"] == "aborted"
    pending = tmp_path / "profiles_pending"
    leftovers = list(pending.glob("*")) if pending.exists() else []
    assert leftovers == []


# ---------------------------------------------------------------------------
# typed failures
# ---------------------------------------------------------------------------


def test_a_malformed_spec_fails_the_job_without_an_artifact(tmp_path):
    job = _enqueue(tmp_path, _spec(technology="nuclear"))

    assert _finished(job.id)
    done = sq.solve_queue.get_job(job.id)
    assert done["status"] == "failed"
    assert "UnsupportedTechnologyError" in (done["error"] or "")
    pending = tmp_path / "profiles_pending"
    leftovers = list(pending.glob("*")) if pending.exists() else []
    assert leftovers == []


def test_a_job_without_a_storage_dir_fails_loudly():
    job = sq.solve_queue.enqueue(
        "profile-project",
        solver_config_json=json.dumps(_spec()),
        kind=sq.KIND_PROFILE_GEN,
    )
    assert _finished(job.id)
    done = sq.solve_queue.get_job(job.id)
    assert done["status"] == "failed"
    assert "storage" in (done["error"] or "").lower()


# ---------------------------------------------------------------------------
# the artifact path guard
# ---------------------------------------------------------------------------


def test_a_job_id_that_escapes_the_pending_dir_is_refused(tmp_path):
    with pytest.raises(job_mod.ProfileJobError):
        job_mod._artifact_paths(tmp_path, "../escape")


def test_artifact_paths_stay_inside_profiles_pending(tmp_path):
    nc, meta = job_mod._artifact_paths(tmp_path, str(uuid.uuid4()))
    assert nc.parent == (tmp_path / "profiles_pending").resolve()
    assert meta.suffix == ".json"


# ---------------------------------------------------------------------------
# eviction
# ---------------------------------------------------------------------------


def test_eviction_keeps_only_the_last_n_pending_profiles(tmp_path):
    pending = tmp_path / "profiles_pending"
    pending.mkdir()
    keep = 3
    for i in range(5):
        jid = f"job-{i}"
        (pending / f"{jid}.nc").write_bytes(b"x")
        (pending / f"{jid}.json").write_text("{}")
        # mtimes decide age; make them strictly increasing
        t = 1_700_000_000 + i
        import os
        os.utime(pending / f"{jid}.nc", (t, t))
        os.utime(pending / f"{jid}.json", (t, t))

    job_mod._evict_pending(tmp_path, keep=keep)

    survivors = sorted(p.stem for p in pending.glob("*.nc"))
    assert survivors == ["job-2", "job-3", "job-4"]
    assert sorted(p.stem for p in pending.glob("*.json")) == survivors


# ---------------------------------------------------------------------------
# boot reconciliation is kind-agnostic
# ---------------------------------------------------------------------------


def test_a_running_profile_job_is_interrupted_on_boot(_auth_db, seeded_identity,
                                                      tmp_path):
    from services import solve_job_store

    _engine, session_local = _auth_db
    job_id = uuid.uuid4()
    from datetime import datetime, timezone
    with session_local() as db:
        db.add(SolveJobRow(
            id=job_id,
            project_id="profile-project",
            project_key="profile-project",
            kind=sq.KIND_PROFILE_GEN,
            status="running",
            storage_dir=str(tmp_path),
            solver_config=json.dumps(_spec()),
            enqueued_by_user_id=seeded_identity["user_id"],
            enqueued_at=datetime.now(tz=timezone.utc),
        ))
        db.commit()

    interrupted, _resumed = solve_job_store.reconcile_on_boot()

    assert interrupted >= 1
    with session_local() as db:
        row = db.get(SolveJobRow, job_id)
        assert row.status == "interrupted"
        assert row.kind == sq.KIND_PROFILE_GEN
