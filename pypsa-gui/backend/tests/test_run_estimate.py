"""
Run progress slice 1: how long a run will take, from its project's past runs.

Spec: `.scratch/run-progress/spec.md` §2. Conditions: GK B plan GO E1–E5
(`.scratch/run-progress/issues/01-run-estimate.md`).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone, UTC

import pytest

from services import run_estimate

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
LOPF = json.dumps({"mode": "lopf", "solver_name": "highs"})


def _row(seconds, *, status="completed", finished_offset_h=0, config=LOPF, rid=None):
    finished = T0 + timedelta(hours=finished_offset_h)
    return {
        "id": rid or uuid.uuid4(),
        "status": status,
        "solver_config": config,
        "started_at": finished - timedelta(seconds=seconds),
        "finished_at": finished,
    }


def _key(config=LOPF, kind="solve"):
    return run_estimate.mode_key(kind, config)


# ── E3: the exact rule ──────────────────────────────────────────────────────


def test_one_sample_is_unknown_and_says_so():
    out = run_estimate.estimate([_row(60)], "solve", _key())
    assert out["seconds"] is None
    assert out["samples"] == 1


def test_no_samples_is_unknown():
    assert run_estimate.estimate([], "solve", _key()) == {
        "seconds": None,
        "samples": 0,
        "run_ids": [],
    }


def test_two_samples_give_the_mean_of_the_two():
    out = run_estimate.estimate(
        [_row(60, finished_offset_h=0), _row(100, finished_offset_h=1)], "solve", _key()
    )
    assert out["seconds"] == 80
    assert out["samples"] == 2


def test_five_samples_give_the_middle_value():
    rows = [_row(s, finished_offset_h=i) for i, s in enumerate([300, 10, 50, 1000, 40])]
    out = run_estimate.estimate(rows, "solve", _key())
    assert out["seconds"] == 50
    assert out["samples"] == 5


def test_only_the_latest_five_by_finish_time_count():
    # Oldest (offset 0) is 10_000 s; the five newer ones are 10..50 s.
    rows = [_row(10_000, finished_offset_h=0)] + [
        _row(s, finished_offset_h=i + 1) for i, s in enumerate([10, 20, 30, 40, 50])
    ]
    rows.reverse()
    out = run_estimate.estimate(rows, "solve", _key())
    assert out["seconds"] == 30
    assert out["samples"] == 5
    assert str(rows[-1]["id"]) not in out["run_ids"]


def test_the_basis_names_the_runs_used_newest_first():
    a, b = _row(60, finished_offset_h=0), _row(90, finished_offset_h=2)
    out = run_estimate.estimate([a, b], "solve", _key())
    assert out["run_ids"] == [str(b["id"]), str(a["id"])]


# ── E1: eligibility ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status", ["failed", "aborted", "interrupted", "running", "queued"]
)
def test_only_completed_runs_count(status):
    rows = [
        _row(60),
        _row(60, finished_offset_h=1),
        _row(9999, status=status, finished_offset_h=2),
    ]
    out = run_estimate.estimate(rows, "solve", _key())
    assert out["seconds"] == 60
    assert out["samples"] == 2


def test_a_missing_start_is_excluded():
    bad = _row(60, finished_offset_h=2)
    bad["started_at"] = None
    out = run_estimate.estimate([_row(60), bad], "solve", _key())
    assert out["seconds"] is None and out["samples"] == 1


def test_a_naive_timestamp_is_excluded():
    bad = _row(60, finished_offset_h=2)
    bad["started_at"] = bad["started_at"].replace(tzinfo=None)
    out = run_estimate.estimate([_row(60), bad], "solve", _key())
    assert out["samples"] == 1


@pytest.mark.parametrize("seconds", [0, -30])
def test_a_run_that_finished_before_it_started_is_excluded(seconds):
    out = run_estimate.estimate(
        [_row(60), _row(seconds, finished_offset_h=1)], "solve", _key()
    )
    assert out["seconds"] is None and out["samples"] == 1


def test_a_non_utc_offset_is_converted_not_dropped():
    cet = timezone(timedelta(hours=1))
    other = _row(120, finished_offset_h=1)
    other["started_at"] = other["started_at"].astimezone(cet)
    out = run_estimate.estimate([_row(60), other], "solve", _key())
    assert out["seconds"] == 90


@pytest.mark.parametrize(
    "config",
    ["{not json", "[1, 2]", json.dumps({"mode": {"bad": 1}, "solver_name": None})],
)
def test_an_unreadable_snapshot_is_excluded_never_raises(config):
    rows = [
        _row(60),
        _row(60, finished_offset_h=1),
        _row(9999, finished_offset_h=2, config=config),
    ]
    out = run_estimate.estimate(rows, "solve", _key())
    assert out["seconds"] == 60 and out["samples"] == 2


def test_an_unreadable_job_snapshot_is_unknown_even_against_unreadable_rows():
    rows = [_row(60, config="{bad"), _row(60, finished_offset_h=1, config="{bad")]
    out = run_estimate.estimate(rows, "solve", _key("{bad"))
    assert out["seconds"] is None and out["samples"] == 0


# ── E2: the mode key ───────────────────────────────────────────────────────


def test_the_solve_mode_key_reads_through_the_queues_parser():
    # Legacy 'lpf' is coerced to 'lopf' by `_solver_config_from_dict`; unknown
    # keys are dropped. Both must compare equal to a plain lopf snapshot.
    legacy = json.dumps({"mode": "lpf", "solver_name": "highs", "gone_field": 1})
    assert _key(legacy) == _key(LOPF)
    assert run_estimate.MODE_FIELDS == (
        "mode",
        "solver_name",
        "multi_investment_periods",
    )


def test_a_mode_change_resets_the_samples():
    other = json.dumps(
        {"mode": "lopf", "solver_name": "highs", "multi_investment_periods": True}
    )
    rows = [_row(60), _row(60, finished_offset_h=1)]
    assert run_estimate.estimate(rows, "solve", _key(other))["samples"] == 0


def test_a_solver_change_resets_the_samples():
    other = json.dumps({"mode": "lopf", "solver_name": "gurobi"})
    rows = [_row(60), _row(60, finished_offset_h=1)]
    assert run_estimate.estimate(rows, "solve", _key(other))["samples"] == 0


def test_a_campus_run_compares_on_its_whole_settings():
    full = json.dumps({"k": 3, "invest": True})
    tuning = json.dumps({"action": "island_tuning", "allow_i_max": False})
    rows = [
        _row(600, config=full),
        _row(600, finished_offset_h=1, config=full),
        _row(5, finished_offset_h=2, config=tuning),
    ]
    assert run_estimate.estimate(rows, "campus", _key(full, "campus"))["seconds"] == 600
    reordered = json.dumps({"invest": True, "k": 3})
    assert _key(reordered, "campus") == _key(full, "campus")


def test_runs_without_a_snapshot_compare_with_each_other():
    rows = [_row(30, config=None), _row(50, finished_offset_h=1, config=None)]
    assert (
        run_estimate.estimate(rows, "gridspine", _key(None, "gridspine"))["seconds"]
        == 40
    )
