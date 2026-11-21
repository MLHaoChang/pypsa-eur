# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The live progress record (pio/08): what a running stage reports between its
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

from gridspine.drivers.progress import PROGRESS_RECORD, Progress, StudyAborted
from gridspine.drivers.status import stage_status
from gridspine.drivers.study import StudyConfig, run_study

HOURS, WINDOW, OVERLAP, K = 48, 24, 8, 1
WINDOWS = 3          # t0 = 0, 16, 32 with step = window - overlap


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
    assert events == []          # the callback hears completed work only
    p.tick("dispatch", 1, 3)
    assert _read(rec)["stage_started_at"] == body["stage_started_at"]


def test_begin_honours_a_pending_stop(tmp_path):
    stop = threading.Event()
    stop.set()
    with pytest.raises(StudyAborted):
        Progress(stop_event=stop, record=tmp_path / PROGRESS_RECORD).begin("dispatch", 3)


def test_a_new_run_removes_the_last_runs_record(tmp_path):
    rec = tmp_path / PROGRESS_RECORD
    rec.write_text(json.dumps({"stage": "dispatch", "done": 2, "total": 3}))
    Progress(record=rec)
    assert not rec.exists()


def test_a_record_that_cannot_be_written_never_fails_the_run(tmp_path):
    rec = tmp_path / PROGRESS_RECORD
    rec.mkdir()                  # a directory where the file should be
    events = []
    p = Progress(lambda *e: events.append(e), record=rec)
    p.tick("dispatch", 1, 3)
    p.begin("ranking", 5)
    assert events == [("dispatch", 1, 3)]


def test_without_a_record_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Progress().tick("dispatch", 1, 3)
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# a real run keeps the record current
# --------------------------------------------------------------------------

def test_a_run_records_the_dispatch_total_before_the_first_window_is_solved(tmp_path):
    seen = {}

    def callback(stage, done, total):
        # The callback runs before the tick's own write, so at the first
        # window's event the record still holds what came before it.
        if (stage, done) == ("dispatch", 1):
            seen.update(_read(tmp_path / PROGRESS_RECORD))

    run_study(StudyConfig(outdir=tmp_path, hours=HOURS, k=K, window=WINDOW, overlap=OVERLAP),
              progress=callback)
    assert (seen["stage"], seen["done"], seen["total"]) == ("dispatch", 0, WINDOWS)
    last = _read(tmp_path / PROGRESS_RECORD)
    assert last["stage"] == "handoff" and last["done"] == last["total"]


# --------------------------------------------------------------------------
# stage_status reads it for the running stage only
# --------------------------------------------------------------------------

def _dispatch_running(tmp_path, record):
    """A run directory past ingest, inside the unit commitment."""
    (tmp_path / "loads.csv").write_text("snapshot\n")
    (tmp_path / PROGRESS_RECORD).write_text(json.dumps(record))
    return stage_status(tmp_path)


def test_the_running_dispatch_reports_its_windows_and_elapsed_time(tmp_path):
    import time
    st = _dispatch_running(tmp_path, {
        "stage": "dispatch", "done": 1, "total": 3,
        "stage_started_at": time.time() - 90, "updated_at": time.time(),
    })
    d = st["stages"]["dispatch"]
    assert (d["state"], d["done"], d["total"]) == ("running", 1, 3)
    assert 89 <= d["elapsed_s"] < 120
    # The record adds counts, never a state, and only to the running stage.
    assert "elapsed_s" not in st["stages"]["ingest"]
    assert st["stages"]["ranking"] == {"state": "pending", "done": 0, "total": 1}
    assert st["status"] == "running"


def test_a_record_for_another_stage_is_ignored(tmp_path):
    st = _dispatch_running(tmp_path, {"stage": "ingest", "done": 1, "total": 1,
                                      "stage_started_at": 0.0})
    assert st["stages"]["dispatch"] == {"state": "running", "done": 0, "total": 1}


@pytest.mark.parametrize("body", [
    "not json",
    json.dumps(["dispatch", 1, 3]),
    json.dumps({"stage": "dispatch", "done": "one", "total": 3}),
    json.dumps({"stage": "dispatch"}),
])
def test_an_unreadable_record_is_ignored(tmp_path, body):
    (tmp_path / "loads.csv").write_text("snapshot\n")
    (tmp_path / PROGRESS_RECORD).write_text(body)
    assert stage_status(tmp_path)["stages"]["dispatch"] == {"state": "running", "done": 0, "total": 1}


def test_a_record_without_a_start_gives_counts_but_no_clock(tmp_path):
    st = _dispatch_running(tmp_path, {"stage": "dispatch", "done": 2, "total": 3})
    d = st["stages"]["dispatch"]
    assert (d["done"], d["total"]) == (2, 3) and "elapsed_s" not in d


def test_a_stage_done_on_disk_ignores_the_record(tmp_path):
    (tmp_path / "dispatch.csv").write_text("snapshot\n")
    st = _dispatch_running(tmp_path, {"stage": "dispatch", "done": 1, "total": 3,
                                      "stage_started_at": 0.0})
    assert st["stages"]["dispatch"] == {"state": "done", "done": 1, "total": 1}


def test_a_per_hour_stage_keeps_its_disk_count_and_gains_the_clock(tmp_path):
    import time
    for name in ("loads.csv", "dispatch.csv", "metrics.csv"):
        (tmp_path / name).write_text("x\n")
    (tmp_path / "selected.csv").write_text("hour\n7\n19\n")
    (tmp_path / "lf_7_bus.csv").write_text("x\n")
    (tmp_path / PROGRESS_RECORD).write_text(json.dumps({
        "stage": "loadflow", "done": 2, "total": 9, "stage_started_at": time.time() - 5,
    }))
    lf = stage_status(tmp_path)["stages"]["loadflow"]
    assert (lf["state"], lf["done"], lf["total"]) == ("running", 1, 2)
    assert lf["elapsed_s"] >= 4
