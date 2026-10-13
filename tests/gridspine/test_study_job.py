"""Increment 4, task 1: the driver as a JOB — a config in, progress out, an
abort that stops it.

The backend's solve queue owns one thread, a `stop_event` and a log queue
(`services/solve_queue.py`). For a gridspine run to live in that queue rather
than a second job system, `drivers/` has to offer exactly three things: a
serialisable config, a progress callback at every point where the run spends
real time, and a stop that takes effect inside those loops — a year is ~2 h of
unit commitment plus ~20 min of AC screening, so a stop that is only checked
at stage boundaries is not a stop.

THE ABORT TESTS ARE THE ONES THAT SEE THE POLL. A run that never checked
`stop_event` would still emit every progress event and still finish; only
stopping mid-window and finding no `dispatch.csv` proves the poll is inside
the loop. And an abort must leave the run RESUMABLE: the second abort test
stops after the dispatch is written and then finishes the study from it
(`--from-dispatch`, F3), reaching the same selection as an uninterrupted run.

Fixture size: 48 h, 24 h windows, 8 h overlap = three windows, k=1. Screening
is on for the progress fixture (it is the only run that asserts the screening
and handoff stages) and off for the abort runs, which never reach them.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from gridspine.drivers.progress import StudyAborted
from gridspine.drivers.study import StudyConfig, run_study
from gridspine.schema.contracts import ContractError

HOURS, WINDOW, OVERLAP, K = 48, 24, 8, 1
WINDOWS = 3          # t0 = 0, 16, 32 with step = window - overlap
STAGES = ("ingest", "dispatch", "ranking", "loadflow", "screening", "handoff")


class Recorder:
    """A progress callback that remembers everything, and can trip a stop."""

    def __init__(self, stop_event=None, stop_at=None):
        self.events = []
        self.stop_event = stop_event
        self.stop_at = stop_at          # (stage, done) -> set the event when seen

    def __call__(self, stage, done, total):
        self.events.append((stage, done, total))
        if self.stop_at is not None and (stage, done) == self.stop_at:
            self.stop_event.set()

    def stages(self):
        """Stage names with consecutive repeats collapsed."""
        out = []
        for stage, _done, _total in self.events:
            if not out or out[-1] != stage:
                out.append(stage)
        return out

    def order(self):
        """Stage names in order of FIRST appearance."""
        out = []
        for stage in self.stages():
            if stage not in out:
                out.append(stage)
        return out

    def for_stage(self, stage):
        return [(d, t) for s, d, t in self.events if s == stage]


class Event:
    """A stand-in for threading.Event — the driver must only need is_set()."""

    def __init__(self):
        self._set = False

    def set(self):
        self._set = True

    def is_set(self):
        return self._set


def _config(outdir, **kw):
    kw.setdefault("hours", HOURS)
    kw.setdefault("k", K)
    kw.setdefault("window", WINDOW)
    kw.setdefault("overlap", OVERLAP)
    return StudyConfig(outdir=outdir, **kw)


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("study_job")
    rec = Recorder()
    result = run_study(_config(out), progress=rec)
    return out, result, rec


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_config_round_trips_through_json(tmp_path):
    c = _config(tmp_path, screen=False, n2_prune_threshold_pct=90.0)
    again = StudyConfig.from_json(json.loads(json.dumps(c.to_json())))
    assert again == c
    assert c.to_json()["outdir"] == str(tmp_path)


@pytest.mark.parametrize("bad", [
    {"hours": 0}, {"k": 0}, {"window": 25}, {"window": -24},
    {"overlap": -1}, {"overlap": 24}, {"n2_prune_threshold_pct": -1.0},
])
def test_an_invalid_config_is_refused_before_anything_is_written(tmp_path, bad):
    out = tmp_path / "never"
    with pytest.raises(ContractError):
        _config(out, **bad)
    assert not out.exists()


# --------------------------------------------------------------------------
# progress
# --------------------------------------------------------------------------

def test_progress_visits_every_stage_in_order(full_run):
    """First appearance, not a block per stage: the per-hour loop finishes an
    hour before it starts the next, so loadflow and screening interleave —
    which is the property "resume from the last valid artifact" needs."""
    _out, _res, rec = full_run
    assert rec.order() == list(STAGES)
    assert rec.stages()[:3] == ["ingest", "dispatch", "ranking"]
    assert rec.stages()[-1] == "handoff"


def test_progress_is_monotone_and_completes_every_stage(full_run):
    _out, _res, rec = full_run
    for stage in STAGES:
        seen = rec.for_stage(stage)
        assert seen, stage
        totals = {t for _d, t in seen}
        assert len(totals) == 1, (stage, totals)
        dones = [d for d, _t in seen]
        assert dones == sorted(dones), (stage, dones)
        assert dones[-1] == seen[0][1], (stage, dones)   # ends at total
        assert dones[0] >= 1, stage


def test_the_dispatch_stage_reports_one_event_per_window(full_run):
    _out, _res, rec = full_run
    assert rec.for_stage("dispatch") == [(i, WINDOWS) for i in range(1, WINDOWS + 1)]


def test_the_per_hour_stages_count_the_selected_hours(full_run):
    _out, res, rec = full_run
    n = len(res.selected)
    for stage in ("loadflow", "screening", "handoff"):
        assert rec.for_stage(stage) == [(i, n) for i in range(1, n + 1)], stage


def test_the_ranking_stage_reports_the_ac_year_pass_over_every_hour(full_run):
    _out, _res, rec = full_run
    assert rec.for_stage("ranking")[-1] == (HOURS, HOURS)


def test_the_manifest_records_the_config_and_a_completed_status(full_run):
    out, res, _rec = full_run
    m = json.loads(res.artifacts["manifest"].read_text())
    assert m["status"] == "completed"
    assert m["config"] == StudyConfig(outdir=out, hours=HOURS, k=K, window=WINDOW, overlap=OVERLAP).to_json()


def test_a_study_runs_without_a_progress_callback(tmp_path):
    """The callback is optional; the queue passes one, a CLI run does not."""
    res = run_study(_config(tmp_path / "quiet", hours=24, window=24, overlap=0, screen=False))
    assert len(res.selected) >= 1


# --------------------------------------------------------------------------
# abort
# --------------------------------------------------------------------------

def test_a_stop_inside_the_window_loop_ends_the_run_before_the_dispatch_is_written(tmp_path):
    out = tmp_path / "aborted_early"
    stop = Event()
    rec = Recorder(stop_event=stop, stop_at=("dispatch", 1))
    with pytest.raises(StudyAborted):
        run_study(_config(out, screen=False), progress=rec, stop_event=stop)
    # stopped in the window loop: window 2 of 3 never ran, no dispatch artifact
    assert rec.for_stage("dispatch") == [(1, WINDOWS)]
    assert not (out / "dispatch.csv").exists()
    err = json.loads((out / "error_dispatch.json").read_text())
    assert err["stage"] == "dispatch"
    assert "abort" in err["cause"].lower()


def test_a_stop_after_the_dispatch_leaves_a_run_that_resumes_to_the_same_selection(tmp_path, full_run):
    _full_out, full_res, _rec = full_run
    out = tmp_path / "aborted_late"
    stop = Event()
    rec = Recorder(stop_event=stop, stop_at=("ranking", HOURS))
    with pytest.raises(StudyAborted):
        run_study(_config(out, screen=False), progress=rec, stop_event=stop)
    assert (out / "dispatch.csv").exists() and (out / "loads.csv").exists()
    err = json.loads((out / "error_ranking.json").read_text())
    assert "abort" in err["cause"].lower()

    resumed = run_study(StudyConfig(outdir=tmp_path / "resumed", k=K, from_dispatch=out))
    assert list(resumed.selected["hour"]) == list(full_res.selected["hour"])
    assert list(resumed.selected["reasons"]) == list(full_res.selected["reasons"])


def test_a_stop_that_is_never_set_does_not_disturb_the_run(full_run):
    _out, res, _rec = full_run
    assert len(res.selected) >= 1
    assert all(res.selected["converged"])


def test_the_resume_path_also_reports_progress_and_stops(tmp_path, full_run):
    full_out, _res, _rec = full_run
    stop = Event()
    rec = Recorder(stop_event=stop, stop_at=("ranking", HOURS))
    out = tmp_path / "resume_stop"
    with pytest.raises(StudyAborted):
        run_study(StudyConfig(outdir=out, k=K, screen=False, from_dispatch=full_out),
                  progress=rec, stop_event=stop)
    assert rec.stages()[:2] == ["ingest", "ranking"]     # no dispatch stage on a resume
    assert pd.read_csv(out / "dispatch.csv").equals(pd.read_csv(full_out / "dispatch.csv"))


def test_a_previous_runs_error_artifact_does_not_survive_into_this_run(tmp_path, monkeypatch):
    """`stage_status` reads `error_<stage>.json` as THE state of the run, and
    nothing ever removed one. The run directory is per project and reused, so a
    study that failed at its dispatch stage made the NEXT, successful run of the
    same project report `status: failed`, `dispatch: failed` and
    `handoff: pending` over a complete manifest and real bundles — a finished
    study hidden behind a week-old error.

    The artifacts describe a run, so clearing them is the first thing a run does.
    Stubbing the first stage keeps this a statement about `run_study`'s order and
    not another 15-minute study.
    """
    import gridspine.drivers.study as ds

    out = tmp_path / "reused"
    out.mkdir()
    (out / "error_dispatch.json").write_text(
        '{"stage": "dispatch", "element_ids": [], "cause": "ContractError(\'last week\')"}'
    )
    (out / "error_ranking.json").write_text('{"stage": "ranking", "cause": "x"}')
    seen = {}

    def fake_dispatch_year(outdir, **kw):
        seen["errors"] = sorted(p.name for p in Path(outdir).glob("error_*.json"))
        raise RuntimeError("far enough")

    monkeypatch.setattr(ds, "dispatch_year", fake_dispatch_year)
    with pytest.raises(RuntimeError, match="far enough"):
        run_study(_config(out))
    assert seen["errors"] == []
