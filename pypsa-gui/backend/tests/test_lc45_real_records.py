# ruff: noqa: F811 -- imported pytest fixtures
"""LC-45: the study follower reads the records the runners really save.

Options records come from a real run (the fake solver of `test_study_runner`);
tornado records are the dict `tornado_runner._save` hands the store, built from
a real `TornadoOutcome`. Nothing here is a hand-written record."""

from types import SimpleNamespace

import pytest

from harness import continuation
from services import solver_service
from services.pypsa_service import PyPSAService
from services.study import findings as F, tornado_runner as T
from tests.study_s4_support import OPTIONS, wait_run
from tests.test_study_runner import FakeSolver, _setup, studies_on  # noqa: F401


def _state(rec, kind="study_options"):
    return continuation._study_state_of(
        rec, {"kind": kind, "attempt": rec.get("started_at")}
    )


def _real_run(client, api_project, monkeypatch, name, on_call=None):
    solver = FakeSolver(on_call=on_call)
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _setup(client, api_project, name)
    assert (
        client.post(f"/api/projects/{name}/studies/{sid}/run", json={}).status_code
        == 202
    )
    return wait_run(client, name, sid)


def test_a_real_finished_run_is_completed_and_has_the_keys_the_follower_reads(
    client, api_project, studies_on, monkeypatch
):
    rec = _real_run(client, api_project, monkeypatch, "lc45-done")
    assert rec["status"] == "done"
    for key in ("status", "started_at", "options", "solved", "pending", "error"):
        assert key in rec, key
    assert _state(rec) == ("completed", None)


def test_a_real_run_with_a_failed_option_row_is_partial_with_the_count(
    client, api_project, studies_on, monkeypatch
):
    def fail_the_second(k):
        if k == 2:
            raise RuntimeError("solver crashed")

    rec = _real_run(client, api_project, monkeypatch, "lc45-row", fail_the_second)
    unsolved = len(OPTIONS) - len(rec["solved"])
    assert unsolved >= 1
    got, reason = _state(rec)
    assert (
        got == "partial"
        and reason == f"{unsolved} of {len(OPTIONS)} options did not solve"
    )


def test_a_real_user_abort_is_aborted(client, api_project, studies_on, monkeypatch):
    holder = {}

    def stop_after_first(k):
        if k == 1:
            PyPSAService.get_context(holder["key"]).solver_state["decision_study"][
                "stop_event"
            ].set()

    solver = FakeSolver(on_call=stop_after_first)
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _setup(client, api_project, "lc45-abort")
    holder["key"] = __import__(
        "services.project_registry", fromlist=["x"]
    ).registry_key(_row("lc45-abort"))
    assert (
        client.post(f"/api/projects/lc45-abort/studies/{sid}/run", json={}).status_code
        == 202
    )
    rec = wait_run(client, "lc45-abort", sid)
    assert rec["status"] == "aborted"
    assert _state(rec) == ("aborted", "aborted")


def _row(name):
    from db.models import Project
    from db.session import SessionLocal

    with SessionLocal() as db:
        return db.query(Project).filter(Project.name == name).one()


# ── tornado: the dict `_save` writes, with a real outcome ────────────────────


def _saved(monkeypatch, status, rob, error=None, **record):
    saved = {}
    monkeypatch.setattr(
        T.store, "save_aux", lambda base, sid, name, data: saved.update(data)
    )
    outcome = F.TornadoOutcome([], rob) if rob is not None else None
    base = {"started_at": 1.0, "variants": []}
    T._save(
        None,
        "s1",
        SimpleNamespace(findings={}),
        {**base, **record},
        outcome,
        status,
        error,
        [],
    )
    return saved


@pytest.mark.parametrize(
    "rob, native, reason",
    [
        (F.Robustness(status="ok"), "completed", None),
        # a tornado with nothing to test is the study's answer, and the verdict reads it (N3)
        (
            F.Robustness(status="skipped", note="no_battery_candidate"),
            "completed",
            None,
        ),
        (
            F.Robustness(
                status="skipped", note="best_battery_npv_not_positive_at_centre"
            ),
            "completed",
            None,
        ),
        (
            F.Robustness(status="not_established", note="tornado_row_failed"),
            "partial",
            "tornado_row_failed",
        ),
    ],
)
def test_a_done_tornado_reads_by_its_robustness(monkeypatch, rob, native, reason):
    got, why = _state(_saved(monkeypatch, "done", rob), "study_tornado")
    assert got == native and (reason is None or reason in why)


def test_a_tornado_with_one_ok_row_and_one_failed_row_is_partial(monkeypatch):
    rows = [
        F.TornadoRow(
            key="a",
            label="A",
            unit="x",
            centre_value=1.0,
            low_value=0.5,
            high_value=1.5,
            npv_low=1.0,
            npv_high=2.0,
            swing=1.0,
            evaluation="redispatch",
            notes=[],
            unavailable={},
        ),
    ]
    rob = F.Robustness(
        status="not_established", tornado=rows, pending=["b"], note="tornado_row_failed"
    )
    got, why = _state(_saved(monkeypatch, "done", rob), "study_tornado")
    assert got == "partial" and "tornado_row_failed" in why


def test_a_tornado_deadline_reads_partial_from_its_note_not_the_error_text(monkeypatch):
    # the error text carries no "deadline": only `robustness.note` and the flag say so
    saved = _saved(
        monkeypatch,
        "aborted",
        F.Robustness(status="ok"),
        error="the wait ran out",
        deadline_exceeded="the wait ran out",
    )
    assert saved["robustness"]["note"] == T.DEADLINE_NOTE
    saved.pop("deadline_exceeded", None)
    got, why = _state(saved, "study_tornado")
    assert got == "partial" and why == "the wait ran out"


def test_a_tornado_budget_stop_is_partial(monkeypatch):
    saved = _saved(
        monkeypatch, "aborted", F.Robustness(status="ok"), budget_exhausted=True
    )
    assert _state(saved, "study_tornado") == ("partial", "the solve budget ran out")


def test_a_tornado_user_abort_is_aborted_even_with_an_outcome(monkeypatch):
    saved = _saved(monkeypatch, "aborted", F.Robustness(status="ok"))
    assert _state(saved, "study_tornado") == ("aborted", "aborted")


def test_a_failed_run_whose_error_mentions_a_deadline_stays_failed():
    rec = {
        "status": "failed",
        "started_at": 1.0,
        "error": "the tariff deadline moved",
        "options": ["a"],
        "solved": [],
        "pending": [],
    }
    assert _state(rec) == ("failed", "the tariff deadline moved")


def test_the_options_deadline_is_partial_by_its_code():
    rec = {
        "status": "failed",
        "started_at": 1.0,
        "options": ["a"],
        "solved": [],
        "pending": [],
        "error": "solve_deadline_exceeded: wait ran out (option a)",
    }
    got, why = _state(rec)
    assert got == "partial" and "solve_deadline_exceeded" in why


# ── a malformed record reads unknown and never raises (N1) ───────────────────


@pytest.mark.parametrize(
    "bad",
    [
        {"robustness": "ok"},
        {"robustness": ["ok"]},
        {"solved": [["a"]]},  # unhashable
        {"solved": "abc"},
        {"options": "abc"},
        {"pending": 3},
        {"status": ["done"]},
    ],
)
def test_a_malformed_record_reads_unknown_and_never_raises(monkeypatch, bad):
    rec = {
        "status": "done",
        "started_at": 1.0,
        "options": ["a"],
        "solved": ["a"],
        "pending": [],
        **bad,
    }
    monkeypatch.setattr(continuation, "_study_record", lambda job: rec)
    job = {"kind": "study_options", "attempt": 1.0}
    assert continuation._study_state(job) == ("unknown", None)
    assert continuation._queue_job_status(job) == "unknown"
