# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Project-viewer-role 02, slice 2: the campus island apply job reads its
requester's role again before each write and each solve.

A refusal in trial N stops the loop with ``stopped == "project_view_only"``:
trials 1..N-1 stay, and the cuts file and the campus YAML hold their bytes
after trial N-1. A role that cannot be read refuses; a job with no requester
(local mode) is not judged.

``POST …/gfm-settings/apply`` is synchronous and gated at its route, so it
has no job to re-check.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from services import campus_electrical_service as ce
from services import campus_island_cuts as cic
from services import project_registry, project_write_gate
from services.project_write_gate import KIND
from tests.test_campus_island_cuts import _propose, _ready, _row, _wait_done, saved  # noqa: F401
from tests.test_campus_island_service import project, user_and_db  # noqa: F401
from tests.test_viewer_role_runners import Member

CALLS_PER_TRIAL = 4  # before the cuts write, the solve, the rerun, the trial's write


def _yaml(n: int) -> str:
    return f"campus:\n  f_hz: 50.0\n  name: trial {n}\n"


class Script:
    """Each rerun proposes one more failing hour and rewrites the campus
    YAML, so every trial changes both files."""

    def __init__(self, project, on_solve=None):
        self.project, self.n, self.on_solve = project, 0, on_solve

    def solve(self, project):
        self.n += 1
        if self.on_solve is not None:
            self.on_solve(self.n)
        return 1000.0 * self.n

    def rerun(self, project):
        (ce.campus_dir(project) / ce.CAMPUS_FILE).write_text(_yaml(self.n))
        _propose(project, [_row(hour=self.n)])


def _files(project):
    paths = [project_registry.project_dir(project) / cic.CUTS_FILE,
             ce.campus_dir(project) / ce.CAMPUS_FILE]
    return {p.name: (p.read_bytes() if p.is_file() else None) for p in paths}


@pytest.mark.parametrize("at", range(1, 2 * CALLS_PER_TRIAL + 1))
def test_a_refusal_in_trial_n_leaves_the_files_as_trial_n_minus_1_left_them(saved, at):
    """`at` is the role check that refuses: 1–4 in trial 1, 5–8 in trial 2."""
    _propose(saved, [_row(hour=0)])
    script, after = Script(saved), {}
    calls = {"n": 0}

    def may_write():
        calls["n"] += 1
        if calls["n"] % CALLS_PER_TRIAL == 1:                  # a trial begins
            after[calls["n"] // CALLS_PER_TRIAL] = _files(saved)
        if calls["n"] == at:
            raise project_write_gate.RequesterViewOnly()

    out = cic.apply_loop(saved, solve=script.solve, rerun=script.rerun,
                         stop_event=threading.Event(), may_write=may_write)
    n = (at - 1) // CALLS_PER_TRIAL                            # the refused trial, 0-based
    assert out["stopped"] == KIND
    assert [t["trial"] for t in out["trials"]] == list(range(1, n + 1))
    assert _files(saved) == after[n]
    assert len(cic.get_cuts(saved)["trials"]) == n
    assert calls["n"] == at                                    # nothing ran past the refusal


def test_without_a_refusal_the_loop_asks_before_every_write_and_solve(saved):
    _propose(saved, [_row(hour=0)])
    script, calls = Script(saved), []
    out = cic.apply_loop(saved, solve=script.solve, rerun=script.rerun,
                         stop_event=threading.Event(), may_write=lambda: calls.append(1))
    assert out["stopped"] == "N reached" and len(out["trials"]) == cic.MAX_TRIALS
    assert len(calls) == CALLS_PER_TRIAL * cic.MAX_TRIALS


def test_a_refused_queue_solve_is_a_view_only_refusal(saved, monkeypatch):
    from services import solve_queue as sq
    from services.study import runner

    _ready(saved)
    job = SimpleNamespace(status="failed", objective=None, error=project_write_gate.REFUSAL)
    monkeypatch.setattr(sq.solve_queue, "enqueue_unique", lambda name, **kw: (job, True))
    monkeypatch.setattr(runner, "_wait", lambda j, stop: None)
    with pytest.raises(project_write_gate.RequesterViewOnly):
        cic._queue_solve(saved, 7, threading.Event())


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
        saved, member, monkeypatch, change):
    after_1 = {}

    def on_solve(n):
        if n == 1:
            return
        after_1.update(_files(saved))  # trial 2's cuts are written; the check after the solve refuses
        getattr(member, change)()

    rec = _job(saved, monkeypatch, on_solve, member.id)
    assert rec["state"] == "failed" and rec["stopped"] == KIND, rec
    assert rec["error_code"] == KIND and rec["error"] == project_write_gate.REFUSAL
    assert rec["trials"] == 1 and len(cic.get_cuts(saved)["trials"]) == 1
    cuts = project_registry.project_dir(saved) / cic.CUTS_FILE
    assert [c["hour"] for c in cic.get_cuts(saved)["applied"]] == [0]   # trial 1's only
    assert cuts.read_bytes() != after_1[cic.CUTS_FILE]                    # trial 2's put back
    assert (ce.campus_dir(saved) / ce.CAMPUS_FILE).read_text() == _yaml(1)


def test_an_apply_job_of_an_editor_who_stays_one_runs_to_its_end(saved, member, monkeypatch):
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
    assert rec["trials"] == 1


def test_an_apply_job_with_no_requester_is_not_judged(saved, monkeypatch):
    monkeypatch.setattr(project_write_gate, "requester_may_not_write",
                        lambda u, p: pytest.fail("a local-mode job is not judged")
                        if u is not None else False)
    rec = _job(saved, monkeypatch, None, None)
    assert rec["state"] == "done" and rec["trials"] == cic.MAX_TRIALS, rec


def test_the_job_reads_the_role_against_the_project_row(saved, monkeypatch):
    asked = []
    monkeypatch.setattr(project_write_gate, "requester_may_not_write",
                        lambda u, p: asked.append((u, p)) or False)
    _job(saved, monkeypatch, None, "u-1")
    assert asked and set(asked) == {("u-1", saved.id)}
