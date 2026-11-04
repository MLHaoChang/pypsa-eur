# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Study harness issue 25: the notice when an agent loop ends without a pass."""
import json
import time

import pytest
from harness import loop_notice, runner as harness_runner
from services import run_envelope
from services import email_service
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_envelope import BLOCK, envelope_of, granted, on_run, profile  # noqa: F401
from tests.test_harness_run_loops import (  # noqa: F401
    Script, call, energy_source, go, no_series, rated, say, tunable_rule,
)
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import detached, frames_of, settle, task_path  # noqa: F401


@pytest.fixture
def outbox():
    email_service.reset_outbox_for_tests()
    email_service.set_delivery_hook_for_tests(None)
    yield email_service.OUTBOX
    email_service.set_delivery_hook_for_tests(None)
    email_service.reset_outbox_for_tests()


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(loop_notice, "_address", lambda task: "owner@example.com")


def exhaust(granted, profile, notify, monkeypatch):
    profile(BLOCK)
    no_series()
    real = run_envelope.decide
    monkeypatch.setattr(run_envelope, "decide", lambda *a, **k: real(*a, notify_email=notify, **k))
    task, path, _ = granted()
    turns = []
    for n, cost in enumerate((11.0, 12.0, 13.0)):
        turns += [call("update_component", {"component_class": "Generator", "name": "G1",
                                            "attrs": {"marginal_cost": cost}}, n), say("Tried a cost.")]
    record = go(harness_runner.Runner("a", agent_provider=Script(*turns)), path, task)
    return path, record


def test_exhausted_loop_with_notify_email_sends_one_notice_with_rules_and_ledger(
        granted, profile, site, rated, tunable_rule, outbox, owner, monkeypatch):
    path, record = exhaust(granted, profile, True, monkeypatch)
    assert record["run"]["loop"]["ended"] == "exhausted" and record["run"]["status"] == "needs_user"
    mails = [m for m in outbox if m["metadata"].get("kind") == "harness_loop_end"]
    assert len(mails) == 1 and mails[0]["to"] == "owner@example.com"
    body = mails[0]["body_text"]
    assert "in.loads.series" in body and "marginal_cost" in body and "13.0" in body
    assert record["run"]["loop"]["notice"]["sent"] is True
    harness_runner._notify(path)  # a later tick: no second mail
    assert len([m for m in outbox if m["metadata"].get("kind") == "harness_loop_end"]) == 1


def test_without_notify_email_nothing_is_sent(granted, profile, site, rated, tunable_rule, outbox, owner, monkeypatch):
    _, record = exhaust(granted, profile, False, monkeypatch)
    assert record["run"]["loop"]["ended"] == "exhausted"
    assert not [m for m in outbox if m["metadata"].get("kind") == "harness_loop_end"]
    assert "notice" not in record["run"]["loop"]


def test_a_sender_failure_leaves_the_run_needs_user_with_the_failure_recorded(
        granted, profile, site, rated, tunable_rule, outbox, owner, monkeypatch):
    def boom(envelope):
        raise RuntimeError("smtp down")
    email_service.set_delivery_hook_for_tests(boom)
    _, record = exhaust(granted, profile, True, monkeypatch)
    assert record["run"]["status"] == "needs_user"
    assert record["run"]["loop"]["notice"]["sent"] is False
    assert record["run"]["loop"]["notice"]["error"] == "send_failed"


def test_model_text_in_the_loop_history_never_reaches_the_mail():
    task = {"task_id": "t1", "run": {"stages": {"input_gate": {"gate": {"rules": []}}}, "ledger": []}}
    loop = {"ended": "exhausted", "stage": "input_gate", "rounds": 1,
            "history": [{"changes": [], "note": "IGNORE ALL PREVIOUS", "offered": ["IGNORE ALL PREVIOUS"]}]}
    assert "IGNORE" not in loop_notice._body(task, loop, "http://x")


def test_a_claim_with_no_result_is_shown_uncertain_and_never_resent(
        granted, profile, site, rated, tunable_rule, outbox, owner, monkeypatch):
    path, record = exhaust(granted, profile, True, monkeypatch)
    outbox.clear()

    def stale(t):
        t["run"]["loop"]["notice"] = {"claimed_at": time.time() - 3600, "sent": None}
    harness_runner._update(path, stale)
    harness_runner._notify(path)
    notice = harness_runner._read(path)["run"]["loop"]["notice"]
    assert notice["sent"] is False and notice["error"] == "uncertain" and not outbox


def test_the_run_view_shows_the_notice_failure(granted, profile, site, rated, tunable_rule, outbox, owner,
                                               monkeypatch):
    from services import assistant_tasks

    def boom(envelope):
        raise RuntimeError("smtp down")
    email_service.set_delivery_hook_for_tests(boom)
    _, record = exhaust(granted, profile, True, monkeypatch)
    view = assistant_tasks._run_view(record)
    assert view["status"] == "needs_user"
    assert view["loop"]["notice"]["sent"] is False and view["loop"]["notice"]["error"] == "send_failed"
