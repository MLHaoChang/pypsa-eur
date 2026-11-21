# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
The live progress record (pio/08): what a running stage reports between its
artifacts.

The unit commitment writes `dispatch.csv` only after its last window, so a
status read from artifacts alone said "dispatch 0/1" for the whole solve, which
is minutes on a two-week study. `Progress` now also writes each emitted tick to
`progress.json` in the run directory, and `stage_status` reads it for the
running stage only. Artifacts still decide every state: the record adds counts
and an elapsed time, never a state.
"""

import json
import threading

import pytest

from gridspine.drivers import progress as progress_mod
from gridspine.drivers.progress import (
    PROGRESS_RECORD,
    PROGRESS_RUN,
    Progress,
    StudyAborted,
)
from gridspine.drivers.status import stage_status
from gridspine.drivers.study import StudyConfig, run_study

HOURS, WINDOW, OVERLAP, K = 48, 24, 8, 1
WINDOWS = 3  # t0 = 0, 16, 32 with step = window - overlap


def _read(path):
    return json.loads(path.read_text())


# --------------------------------------------------------------------------
# Progress writes the record
# --------------------------------------------------------------------------


def test_a_tick_is_written_to_the_record_with_its_stage_start(tmp_path):
    rec = tmp_path / PROGRESS_RECORD
    p = Progress(record=rec)
    p.tick("dispatch", 1, 3)
    first = _read(rec)
    p.tick("dispatch", 2, 3)
    second = _read(rec)
    assert (second["stage"], second["done"], second["total"]) == ("dispatch", 2, 3)
    # One stage, one start: later ticks keep it.
    assert second["stage_started_at"] == first["stage_started_at"]
    assert second["updated_at"] >= first["updated_at"]


def test_a_new_stage_restarts_the_clock(tmp_path, monkeypatch):
    from gridspine.drivers import progress as progress_mod

    clock = iter([100.0, 100.0, 160.0, 160.0])
    monkeypatch.setattr(progress_mod.time, "time", lambda: next(clock))
    rec = tmp_path / PROGRESS_RECORD
    p = Progress(record=rec)
    p.tick("ingest", 1, 1)
    assert _read(rec)["stage_started_at"] == 100.0
    p.tick("dispatch", 1, 3)
    assert (_read(rec)["stage"], _read(rec)["stage_started_at"]) == ("dispatch", 160.0)


def test_begin_writes_none_done_and_is_not_an_event(tmp_path):
    events = []
    rec = tmp_path / PROGRESS_RECORD
    p = Progress(lambda *e: events.append(e), record=rec)
    p.begin("dispatch", 3)
    body = _read(rec)
    assert (body["stage"], body["done"], body["total"]) == ("dispatch", 0, 3)
    assert body["stage_started_at"] is not None
    assert events == []  # the callback hears completed work only
    p.tick("dispatch", 1, 3)
    assert _read(rec)["stage_started_at"] == body["stage_started_at"]


def test_begin_honours_a_pending_stop(tmp_path):
    stop = threading.Event()
    stop.set()
    with pytest.raises(StudyAborted):
        Progress(stop_event=stop, record=tmp_path / PROGRESS_RECORD).begin(
            "dispatch", 3
        )


def test_a_new_run_removes_the_last_runs_record(tmp_path):
    rec = tmp_path / PROGRESS_RECORD
    rec.write_text(json.dumps({"stage": "dispatch", "done": 2, "total": 3}))
    Progress(record=rec)
    assert not rec.exists()


def test_a_record_that_cannot_be_written_never_fails_the_run(tmp_path):
    rec = tmp_path / PROGRESS_RECORD
    rec.mkdir()  # a directory where the file should be
    events = []
    p = Progress(lambda *e: events.append(e), record=rec)
    p.tick("dispatch", 1, 3)
    p.begin("ranking", 5)
    assert events == [("dispatch", 1, 3)]


def test_the_record_names_its_run(tmp_path):
    rec = tmp_path / PROGRESS_RECORD
    p = Progress(record=rec)
    p.tick("dispatch", 1, 3)
    run = (tmp_path / PROGRESS_RUN).read_text()
    assert len(run) == 32 and _read(rec)["run"] == run
    assert Progress(record=rec)._run_id != run  # a new run, a new nonce
    assert (tmp_path / PROGRESS_RUN).read_text() != run


def test_a_throttled_stage_still_ends_at_its_total(tmp_path):
    rec = tmp_path / PROGRESS_RECORD
    p = Progress(record=rec)
    for done in range(1, 8761):
        p.tick("ranking", done, 8760)
        if done == 8759:
            assert _read(rec)["done"] < 8759  # throttled meanwhile
    assert (_read(rec)["done"], _read(rec)["total"]) == (8760, 8760)


def test_a_failed_write_is_logged_once(tmp_path, monkeypatch, caplog):
    def refuse(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(progress_mod.os, "replace", refuse)
    p = Progress(record=tmp_path / PROGRESS_RECORD)
    for done in range(1, 4):
        p.tick("dispatch", done, 3)
    assert [r for r in caplog.records if "not written" in r.getMessage()].__len__() == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_the_callback_hears_the_same_events_with_or_without_a_record(tmp_path):
    def events(**kw):
        heard = []
        p = Progress(lambda *e: heard.append(e), **kw)
        for done in range(1, 1001):
            p.tick("ranking", done, 1000)
        return heard

    assert events() == events(record=tmp_path / PROGRESS_RECORD)


def test_without_a_record_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Progress().tick("dispatch", 1, 3)
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# a real run keeps the record current
# --------------------------------------------------------------------------


def _config(outdir):
    return StudyConfig(outdir=outdir, hours=HOURS, k=K, window=WINDOW, overlap=OVERLAP)


def test_a_run_records_the_dispatch_total_before_the_first_window_is_solved(tmp_path):
    seen, windows = {}, []

    def callback(stage, done, total):
        if stage == "dispatch":
            windows.append(done)
        # The callback runs before the tick's own write, so at the first
        # window's event the record still holds what `begin` wrote.
        if (stage, done) == ("dispatch", 1):
            seen.update(_read(tmp_path / PROGRESS_RECORD))

    run_study(_config(tmp_path), progress=callback)
    assert (seen["stage"], seen["done"]) == ("dispatch", 0)
    # GK P1: the total announced before window 1 is the number of windows solved.
    assert seen["total"] == len(windows) == WINDOWS


def test_a_finished_run_leaves_no_record(tmp_path):
    run_study(_config(tmp_path))
    assert not (tmp_path / PROGRESS_RECORD).exists()
    assert not (tmp_path / PROGRESS_RUN).exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_a_failed_run_leaves_no_record(tmp_path):
    stop = threading.Event()
    stop.set()
    with pytest.raises(StudyAborted):
        run_study(_config(tmp_path), stop_event=stop)
    assert not (tmp_path / PROGRESS_RECORD).exists()
    assert not (tmp_path / PROGRESS_RUN).exists()


def _outputs(outdir):
    # A file may name its own run directory; that is not a difference.
    return {
        p.relative_to(outdir).as_posix(): p.read_bytes().replace(
            str(outdir).encode(), b"<out>"
        )
        for p in sorted(outdir.rglob("*"))
        if p.is_file()
    }


def test_the_record_changes_no_output_and_a_failing_write_fails_nothing(
    tmp_path, monkeypatch
):
    """
    GK P2/P6: with the record, with it failing on every write, and without
    it, the run directory ends byte-identical (manifest timestamps aside).
    """
    with_record = tmp_path / "with"
    run_study(_config(with_record))

    def refuse(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(progress_mod.os, "replace", refuse)
    failing = tmp_path / "failing"
    run_study(_config(failing))
    monkeypatch.undo()

    class NoRecord(Progress):
        def __init__(self, callback=None, stop_event=None, record=None):
            super().__init__(callback, stop_event)

    import gridspine.drivers.study as study_mod

    monkeypatch.setattr(study_mod, "Progress", NoRecord)
    without = tmp_path / "without"
    run_study(_config(without))

    a, b, c = _outputs(with_record), _outputs(failing), _outputs(without)
    assert sorted(a) == sorted(b) == sorted(c)
    for name in a:
        if name.rsplit("/", 1)[-1] in ("manifest.json", "ledger.json", "ledger.md"):
            continue  # differ between ANY two runs (their own time and paths), record or not
        assert a[name] == b[name] == c[name], name


# --------------------------------------------------------------------------
# stage_status reads it for the running stage only
# --------------------------------------------------------------------------

RUN = "a" * 32


def _write_record(outdir, record, run=RUN):
    (outdir / PROGRESS_RUN).write_text(run)
    (outdir / PROGRESS_RECORD).write_text(json.dumps({"run": RUN, **record}))


def _dispatch_running(tmp_path, record, run=RUN):
    """A run directory past ingest, inside the unit commitment."""
    (tmp_path / "loads.csv").write_text("snapshot\n")
    _write_record(tmp_path, record, run)
    return stage_status(tmp_path)


def test_a_record_left_by_another_run_is_ignored_however_new(tmp_path):
    import time

    st = _dispatch_running(
        tmp_path,
        {
            "stage": "dispatch",
            "done": 2,
            "total": 3,
            "stage_started_at": time.time() + 3600,
            "updated_at": time.time() + 3600,
        },
        run="b" * 32,
    )
    assert st["stages"]["dispatch"] == {"state": "running", "done": 0, "total": 1}


def test_a_record_without_its_run_file_is_ignored(tmp_path):
    (tmp_path / "loads.csv").write_text("snapshot\n")
    (tmp_path / PROGRESS_RECORD).write_text(
        json.dumps({"run": RUN, "stage": "dispatch", "done": 1, "total": 3})
    )
    assert stage_status(tmp_path)["stages"]["dispatch"]["total"] == 1


@pytest.mark.parametrize("done, shown", [(7, 3), (-2, 0)])
def test_the_count_is_clamped_to_the_total(tmp_path, done, shown):
    st = _dispatch_running(tmp_path, {"stage": "dispatch", "done": done, "total": 3})
    d = st["stages"]["dispatch"]
    # Clamped, and still running: a record never completes a stage.
    assert (d["state"], d["done"], d["total"]) == ("running", shown, 3)


def test_the_running_dispatch_reports_its_windows_and_elapsed_time(tmp_path):
    import time

    st = _dispatch_running(
        tmp_path,
        {
            "stage": "dispatch",
            "done": 1,
            "total": 3,
            "stage_started_at": time.time() - 90,
            "updated_at": time.time(),
        },
    )
    d = st["stages"]["dispatch"]
    assert (d["state"], d["done"], d["total"]) == ("running", 1, 3)
    assert 89 <= d["elapsed_s"] < 120
    # The record adds counts, never a state, and only to the running stage.
    assert "elapsed_s" not in st["stages"]["ingest"]
    assert st["stages"]["ranking"] == {"state": "pending", "done": 0, "total": 1}
    assert st["status"] == "running"


def test_a_record_for_another_stage_is_ignored(tmp_path):
    st = _dispatch_running(
        tmp_path, {"stage": "ingest", "done": 1, "total": 1, "stage_started_at": 0.0}
    )
    assert st["stages"]["dispatch"] == {"state": "running", "done": 0, "total": 1}


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps(["dispatch", 1, 3]),
        json.dumps({"run": RUN, "stage": "dispatch", "done": 1, "total": "three"}),
        json.dumps({"run": RUN, "stage": "dispatch", "done": 1, "total": 3})[:-5],
        json.dumps({"stage": "dispatch", "done": "one", "total": 3}),
        json.dumps({"stage": "dispatch"}),
    ],
)
def test_an_unreadable_record_is_ignored(tmp_path, body):
    (tmp_path / "loads.csv").write_text("snapshot\n")
    (tmp_path / PROGRESS_RUN).write_text(RUN)
    (tmp_path / PROGRESS_RECORD).write_text(body)
    assert stage_status(tmp_path)["stages"]["dispatch"] == {
        "state": "running",
        "done": 0,
        "total": 1,
    }


def test_a_record_without_a_start_gives_counts_but_no_clock(tmp_path):
    st = _dispatch_running(tmp_path, {"stage": "dispatch", "done": 2, "total": 3})
    d = st["stages"]["dispatch"]
    assert (d["done"], d["total"]) == (2, 3) and "elapsed_s" not in d


def test_a_stage_done_on_disk_ignores_the_record(tmp_path):
    (tmp_path / "dispatch.csv").write_text("snapshot\n")
    st = _dispatch_running(
        tmp_path, {"stage": "dispatch", "done": 1, "total": 3, "stage_started_at": 0.0}
    )
    assert st["stages"]["dispatch"] == {"state": "done", "done": 1, "total": 1}


def test_a_per_hour_stage_keeps_its_disk_count_and_gains_the_clock(tmp_path):
    import time

    for name in ("loads.csv", "dispatch.csv", "metrics.csv"):
        (tmp_path / name).write_text("x\n")
    (tmp_path / "selected.csv").write_text("hour\n7\n19\n")
    (tmp_path / "lf_7_bus.csv").write_text("x\n")
    _write_record(
        tmp_path,
        {
            "stage": "loadflow",
            "done": 2,
            "total": 9,
            "stage_started_at": time.time() - 5,
        },
    )
    lf = stage_status(tmp_path)["stages"]["loadflow"]
    assert (lf["state"], lf["done"], lf["total"]) == ("running", 1, 2)
    assert lf["elapsed_s"] >= 4
