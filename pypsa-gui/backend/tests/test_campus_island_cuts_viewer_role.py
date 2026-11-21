# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Project-viewer-role 02, slice 2: the campus island apply job reads its
requester's role again before each write and each solve.

A refusal in trial N stops the loop with ``stopped == "project_view_only"``.
Every saved result keeps the files it was made from: before trial N's hub
solve, the cuts file and the campus YAML go back to their bytes after trial
N-1; once the solve has read them they stay, and trial N stays in the
history, unfinished. A role that cannot be read refuses; a job with no
requester (local mode) is not judged.

``POST …/gfm-settings/apply`` is synchronous and gated at its route, so it
has no job to re-check.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from services import campus_electrical_service as ce
from services import campus_island_cuts as cic
from services import project_registry, project_write_gate
from services.project_write_gate import KIND
from tests import test_campus_island_cuts as cut_fixtures
from tests import test_campus_island_service as service_fixtures
from tests.test_campus_island_cuts import _propose, _ready, _row, _wait_done
from tests.test_viewer_role_runners import Member

saved = cut_fixtures.saved
project = service_fixtures.project
user_and_db = service_fixtures.user_and_db

CALLS_PER_TRIAL = 4  # before the cuts write, the solve, the rerun, the trial's write


def _yaml(n: int) -> str:
    return f"campus:\n  f_hz: 50.0\n  name: trial {n}\n"


class Script:
    """Each rerun proposes one more failing hour and rewrites the campus
    YAML, so every trial changes both files."""

    def __init__(self, project, on_solve=None):
        self.project, self.n, self.on_solve = project, 0, on_solve
        # file name -> the bytes the latest saved result was made from
        self.made_from: dict = {}

    def solve(self, project):
        self.n += 1
        self.made_from.update(_inputs(project))  # the saved hub read both files
        if self.on_solve is not None:
            self.on_solve(self.n)
        return 1000.0 * self.n

    def rerun(self, project):
        (ce.campus_dir(project) / ce.CAMPUS_FILE).write_text(_yaml(self.n))
        _propose(project, [_row(hour=self.n)])
        self.made_from[ce.CAMPUS_FILE] = _inputs(project)[ce.CAMPUS_FILE]


def _files(project):
    paths = [
        project_registry.project_dir(project) / cic.CUTS_FILE,
        ce.campus_dir(project) / ce.CAMPUS_FILE,
    ]
    return {p.name: (p.read_bytes() if p.is_file() else None) for p in paths}


STEPS = ("cuts", "solve", "rerun", "record")  # the step each check guards


def _inputs(project):
    """What a saved result reads of the two files: the applied cuts (the
    trial history is not a solve input) and the campus YAML."""
    files = _files(project)
    raw = files[cic.CUTS_FILE]
    return {
        cic.CUTS_FILE: None if raw is None else json.loads(raw)["cuts"],
        ce.CAMPUS_FILE: files[ce.CAMPUS_FILE],
    }


@pytest.mark.parametrize("at", range(1, 2 * CALLS_PER_TRIAL + 1))
def test_after_a_refusal_every_saved_result_was_made_from_the_files_on_disk(saved, at):
    """`at` is the role check that refuses: 1–4 in trial 1, 5–8 in trial 2.

    Kills: no restore (at 2, 6), an unconditional restore (at 3, 4, 7, 8),
    a restore of missing files only (at 6)."""
    _propose(saved, [_row(hour=0)])
    script, after = Script(saved), {}
    calls = {"n": 0}

    def may_write():
        calls["n"] += 1
        if calls["n"] % CALLS_PER_TRIAL == 1:  # a trial begins
            after[calls["n"] // CALLS_PER_TRIAL] = _files(saved)
        if calls["n"] == at:
            raise project_write_gate.RequesterViewOnly()

    out = cic.apply_loop(
        saved,
        solve=script.solve,
        rerun=script.rerun,
        stop_event=threading.Event(),
        may_write=may_write,
    )
    n = (at - 1) // CALLS_PER_TRIAL  # the refused trial, 0-based
    step = STEPS[(at - 1) % CALLS_PER_TRIAL]
    assert calls["n"] == at  # nothing ran past the refusal
    assert out["stopped"] == KIND and out["refused"] == {"trial": n + 1, "before": step}
    assert [t["trial"] for t in out["trials"]] == list(range(1, n + 1))
    disk = _files(saved)
    # The invariant: each saved result's inputs are the files on disk.
    for name, made_from in script.made_from.items():
        assert _inputs(saved)[name] == made_from, (name, step)
    history = cic.get_cuts(saved)["trials"]
    if step in ("cuts", "solve"):  # no saved result read trial N's files
        assert disk == after[n]
        assert len(history) == n
    else:  # the hub was solved with trial N's cuts: they and trial N stay
        assert disk[cic.CUTS_FILE] != after[n][cic.CUTS_FILE]
        assert [t["trial"] for t in history] == list(range(1, n + 2))
        assert history[-1]["verdict"] == cic.UNFINISHED
        assert history[-1]["objective"] is None
        assert [c["hour"] for c in history[-1]["added"]] == [n]


def test_without_a_refusal_the_loop_asks_before_every_write_and_solve(saved):
    _propose(saved, [_row(hour=0)])
    script, calls = Script(saved), []
    out = cic.apply_loop(
        saved,
        solve=script.solve,
        rerun=script.rerun,
        stop_event=threading.Event(),
        may_write=lambda: calls.append(1),
    )
    assert out["stopped"] == "N reached" and len(out["trials"]) == cic.MAX_TRIALS
    assert len(calls) == CALLS_PER_TRIAL * cic.MAX_TRIALS


def test_a_refused_queue_solve_is_a_view_only_refusal(saved, monkeypatch):
    from services import solve_queue as sq
    from services.study import runner

    _ready(saved)
    job = SimpleNamespace(
        status="failed", objective=None, error=project_write_gate.REFUSAL
    )
    monkeypatch.setattr(
        sq.solve_queue, "enqueue_unique", lambda name, **kw: (job, True)
    )
    monkeypatch.setattr(runner, "_wait", lambda j, stop: None)
    with pytest.raises(project_write_gate.RequesterViewOnly):
        cic._queue_solve(saved, 7, threading.Event())
    job.error = "infeasible"  # any other failure stays a plain failure
    with pytest.raises(RuntimeError, match="infeasible") as other:
        cic._queue_solve(saved, 7, threading.Event())
    assert not isinstance(other.value, project_write_gate.RequesterViewOnly)


def test_a_file_removed_during_the_refused_trial_is_put_back(saved, tmp_path):
    kept, gone = tmp_path / "kept.yaml", tmp_path / "gone.yaml"
    kept.write_text("a: 1\n")
    gone.write_text("b: 2\n")
    snap = cic._snapshot([kept, gone, tmp_path / "new.yaml"])
    kept.write_text("a: 2\n")
    gone.unlink()
    (tmp_path / "new.yaml").write_text("c: 3\n")
    cic._restore(snap)
    assert kept.read_text() == "a: 1\n" and gone.read_text() == "b: 2\n"
    assert not (tmp_path / "new.yaml").exists()


# ── the job, with a real member's role ──────────────────────────────────────


def _job(saved, monkeypatch, on_solve, user_id):
    _propose(saved, [_row(hour=0)])
    _ready(saved)
    script = Script(saved, on_solve)
    monkeypatch.setattr(cic, "_queue_solve", lambda p, u, stop: script.solve(p))
    monkeypatch.setattr(cic, "_rerun", lambda p, settings: script.rerun(p))
    cic.start_apply_job(saved, user_id=user_id)
    return _wait_done(saved)


@pytest.fixture
def member(saved, _auth_db):
    return Member(_auth_db[1], project_id=saved.id)


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_an_apply_job_whose_requester_loses_edit_in_trial_2_keeps_trial_1(
    saved, member, monkeypatch, change
):
    solved_from = {}

    def on_solve(n):
        if n == 1:
            return
        solved_from.update(_inputs(saved))  # what trial 2's hub solve read
        getattr(member, change)()  # the check after the solve refuses

    rec = _job(saved, monkeypatch, on_solve, member.id)
    assert rec["state"] == "failed" and rec["stopped"] == KIND, rec
    assert rec["error_code"] == KIND and rec["error"] == project_write_gate.REFUSAL
    # One trial finished; trial 2 was refused after its solve, before its rerun.
    assert rec["trials"] == 1
    assert (rec["refused_trial"], rec["refused_before"]) == (2, "rerun")
    cuts = cic.get_cuts(saved)
    assert [t["verdict"] for t in cuts["trials"]][1] == cic.UNFINISHED
    assert [c["hour"] for c in cuts["applied"]] == [0, 1]  # trial 2's, solved, stay
    assert _inputs(saved) == solved_from  # the saved hub's inputs are on disk
    assert (ce.campus_dir(saved) / ce.CAMPUS_FILE).read_text() == _yaml(1)


def test_an_apply_job_of_an_editor_who_stays_one_runs_to_its_end(
    saved, member, monkeypatch
):
    rec = _job(saved, monkeypatch, None, member.id)
    assert rec["state"] == "done" and rec["trials"] == cic.MAX_TRIALS, rec
    assert "error_code" not in rec


def test_an_apply_job_whose_role_cannot_be_read_refuses(saved, member, monkeypatch):
    from db import session as db_session

    def broken():
        raise RuntimeError("database gone")

    def on_solve(n):
        if n == 2:
            monkeypatch.setattr(db_session, "SessionLocal", broken)

    rec = _job(saved, monkeypatch, on_solve, member.id)
    assert rec["state"] == "failed" and rec["error_code"] == KIND, rec
    assert rec["trials"] == 1 and rec["refused_trial"] == 2


def test_an_apply_job_with_no_requester_is_not_judged(saved, monkeypatch):
    monkeypatch.setattr(
        project_write_gate,
        "requester_may_not_write",
        lambda u, p: (
            pytest.fail("a local-mode job is not judged") if u is not None else False
        ),
    )
    rec = _job(saved, monkeypatch, None, None)
    assert rec["state"] == "done" and rec["trials"] == cic.MAX_TRIALS, rec


def test_the_job_reads_the_role_against_the_project_row(saved, monkeypatch):
    asked = []
    monkeypatch.setattr(
        project_write_gate,
        "requester_may_not_write",
        lambda u, p: asked.append((u, p)) or False,
    )
    _job(saved, monkeypatch, None, "u-1")
    assert asked and set(asked) == {("u-1", saved.id)}
