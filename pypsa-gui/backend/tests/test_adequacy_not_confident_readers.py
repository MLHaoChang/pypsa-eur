"""Adequacy loops 04 (D3, GK A (25)): every reader of a finished loop's
status handles `not_confident` explicitly, and none reads it as met, done or
a pass."""

from __future__ import annotations

from harness import activity_history, job_adapters
from services import assistant_activity, run_evidence, run_gates
from services.adequacy.coupling import LOOP_STATUSES


def test_not_confident_is_a_finished_loop_status():
    assert "not_confident" in LOOP_STATUSES
    assert "not_confident" in job_adapters._STUDY_TERMINAL
    assert "not_confident" in activity_history._TERMINAL["adequacy_study"]


def test_not_confident_never_reads_met_done_or_pass():
    assert "not_confident" not in job_adapters._STUDY_SUCCESS
    assert not run_gates.converged({"status": "not_confident"})
    assert assistant_activity._status("not_confident") == "partial"
    assert run_evidence.map_worker_status("not_confident") == "failed"
