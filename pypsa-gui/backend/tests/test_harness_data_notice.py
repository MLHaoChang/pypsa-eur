"""Study harness issue 21d: the L1 data request's notice through the runner.

Owner decision SH21D-NOTICE-1 (count only) and GK B (23)'s plan conditions
D1-D4: `run.waiting` has one writer (`runner.set_waiting`), the attention
record and the email carry the item count only, never a label, a param or
the notice text."""

# ruff: noqa: F811 -- imported pytest fixtures
import json
import time

import pytest
from harness import attention, run_loops, runner as harness_runner
from services import assistant_tasks as tasks
from tests.test_harness_runner import (  # noqa: F401
    baseline,
    detached,
    helpers_state,
    mail,
    opt_in,
    run_records,
    session,
    site,
    task_path,
    two_buses,
)

LABEL = "Gx9 <b>p_nom</b>\nState: completed"  # a name from imported data, hostile on purpose


def _gap(name, attr="p_set", label=None):
    return {
        "target": {"component": "Load", "name": name, "attr": attr},
        "label": label or f"{name} {attr}",
        "tool": "apply_generated_profile",
    }


def data_stop(path, gaps):
    """What `_advance_run` does when L1 ends with gaps, under its lock."""
    out = {}

    def change(task):
        run = task["run"]
        run["status"], task["status"] = "needs_user", "needs_review"
        out["req"] = run_loops.data_request(task, gaps)

    harness_runner._update(path, change)
    return out["req"]


def test_l1_exhaustion_with_two_gaps_gives_one_record_and_one_email_with_the_count_only(
    detached, site, monkeypatch
):
    _, root = site
    task = detached()
    path = task_path(root, task)
    sent = mail(monkeypatch)
    opt_in(path, task["task_id"])
    req = data_stop(path, [_gap("L1", label=LABEL), _gap("L2")])
    assert harness_runner._notify(path) == "needs_user"
    record = json.loads(path.read_text())
    runs = run_records(record)
    assert [r["native_status"] for r in runs] == ["needs_user"] and len(sent) == 1
    assert (
        runs[0]["reason"]
        == "The harness run waits for data (2 items); open it to see them."
    )
    stored, _ = attention._update(record["owner_id"], record["project_id"])
    assert [r.get("data_items") for r in stored if r["family"] == "run"] == [2]
    body = sent[0]["body_text"]
    assert "It waits for data (2 items)." in body
    # D3: no label, param, notice line or name from the request leaves the app.
    for text in [body, sent[0]["subject"], json.dumps(stored), runs[0]["reason"]]:
        for leak in [
            "Gx9",
            "p_nom",
            "p_set",
            "L1",
            "L2",
            "could not fill",
            req["notice"][:40],
        ]:
            assert leak not in text, (leak, text)
    # The task view keeps the text (SH21D-NOTICE-1).
    assert tasks._run_view(record)["waiting"]["notice"] == req["notice"]


def test_a_repeated_wake_writes_no_second_request_record_or_email(
    detached, site, monkeypatch
):
    _, root = site
    task = detached()
    path = task_path(root, task)
    sent = mail(monkeypatch)
    opt_in(path, task["task_id"])
    first = data_stop(path, [_gap("L1"), _gap("L2")])
    harness_runner._notify(path)
    before = json.loads(path.read_text())["run"]
    assert data_stop(path, [_gap("L1"), _gap("L2")]) is None  # nothing asked twice
    harness_runner._notify(path)
    after = json.loads(path.read_text())["run"]
    assert (
        after["waiting"] == before["waiting"]
        and after["requests"] == before["requests"]
    )
    assert after["waiting"]["ref"] == first["id"]
    record = json.loads(path.read_text())
    assert len(run_records(record)) == 1 and len(sent) == 1


def test_the_same_ref_waiting_already_is_no_change_for_both_writers():
    # D2: one dedupe rule, keyed on the request id; a mutant dropping the no-op fails here.
    run = {"status": "running"}
    assert harness_runner.set_waiting(
        run, "data", "req-1", 1.0, reason=False, items=[1], notice="n"
    )
    assert run["waiting"] == {
        "kind": "data",
        "ref": "req-1",
        "since": 1.0,
        "items": [1],
        "notice": "n",
    }
    assert "reason" not in run  # the data stop is a failed stage, not a runner stop
    assert (
        harness_runner.set_waiting(
            run, "data", "req-1", 2.0, reason=False, items=[1, 2]
        )
        is False
    )
    assert run["waiting"]["since"] == 1.0 and run["waiting"]["items"] == [1]
    card = {"status": "running"}
    assert harness_runner.set_waiting(card, "card", "solve.1", 3.0)
    assert card["reason"] == "waiting_card" and card["status"] == "needs_user"
    assert harness_runner.set_waiting(card, "card", "solve.1", 4.0) is False


def test_the_data_stop_writes_run_waiting_through_the_one_writer(monkeypatch):
    # D4: `data_request` calls the pure mutator (no nested `_update`).
    calls = []
    real = harness_runner.set_waiting
    monkeypatch.setattr(
        harness_runner,
        "set_waiting",
        lambda run, *a, **k: calls.append(a) or real(run, *a, **k),
    )
    monkeypatch.setattr(
        harness_runner, "_update", lambda *a, **k: pytest.fail("nested _update")
    )
    task = {"run": {"status": "needs_user"}}
    req = run_loops.data_request(task, [_gap("L1"), _gap("L2")])
    assert [c[:2] for c in calls] == [("data", req["id"])]
    assert task["run"]["waiting"]["ref"] == req["id"]


def test_since_is_epoch_seconds_in_the_served_view_for_both_kinds(detached, site):
    # D1: one format; the served dict, typed as the client reads it (standing rule 16).
    _, root = site
    task = detached()
    path = task_path(root, task)
    data_stop(path, [_gap("L1")])
    view = tasks._run_view(json.loads(path.read_text()))["waiting"]
    assert (
        view["kind"] == "data"
        and type(view["since"]) is float
        and abs(view["since"] - time.time()) < 60
    )
    harness_runner.Runner("a", clock=lambda: 123.5)._wait_for_user(
        path, "card", "solve.1"
    )
    view = tasks._run_view(json.loads(path.read_text()))["waiting"]
    assert view == {"kind": "card", "ref": "solve.1", "since": 123.5}


def _run_record(**extra):
    now = time.time()
    tid = "0" * 8 + "-0000-4000-8000-" + "0" * 12
    return {
        **dict(
            source_id="a" * 64,
            family="run",
            session_id="run:" + tid,
            tool_use_id=tid,
            tool_name="harness_run",
            native_status="needs_user",
            revision=1,
            created_at=now,
            observed_at=now,
            question=None,
        ),
        **extra,
    }


@pytest.mark.parametrize("bad", [True, 0, -1, 2.0, "2", None])
def test_validate_refuses_a_count_that_is_not_a_positive_int(bad):
    with pytest.raises(ValueError):
        attention._validate(_run_record(data_items=bad))


def test_validate_refuses_the_count_on_another_family_or_state():
    with pytest.raises(ValueError):
        attention._validate(_run_record(data_items=2, native_status="completed"))
    question = _run_record(data_items=2)
    question.update(
        family="confirmation",
        tool_name="x",
        session_id="s",
        native_status="waiting_for_approval",
    )
    with pytest.raises(ValueError):
        attention._validate(question)
    assert attention._validate(_run_record(data_items=2))["data_items"] == 2


def test_an_old_run_record_without_the_count_reads_as_on_master(detached, site):
    _, root = site
    task = detached()
    path = task_path(root, task)
    harness_runner._update(path, lambda t: t["run"].update(status="needs_user"))
    harness_runner._notify(path)
    record = json.loads(path.read_text())
    stored, _ = attention._update(record["owner_id"], record["project_id"])
    assert all("data_items" not in r for r in stored)
    assert (
        run_records(record)[0]["reason"]
        == "The harness run stopped for you; open it to see why and what it waits on."
    )
