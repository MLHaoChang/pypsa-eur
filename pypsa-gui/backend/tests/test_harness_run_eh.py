# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness issue 07 PR B2: eh_site's Energy Hub study and its rules.

GK B (24) C1 (run_eh_study is always the user's card, on args frozen from
the profile and the project's goal), C2 (its solves count against the
envelope) and C3 (each new fact fails closed), with GK B (31)'s F1-F12:
the full goal freeze (F1), the status map (F2), the record identity (F3),
the settle count (F4), the refused start (F5), the run's own solve (F6),
energization (F7), the review in the result gate (F8), the LOLE loop (F9)
and C8 (eh_site@1 in place)."""

import json
import threading
import types
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException

from harness import run_eh, runner as harness_runner
from services import flow_service, run_envelope, run_gates, run_profiles
from services.run_gates import Env, NoEvidence

PROFILE = run_profiles.resolve("eh_site@1")
TEMPLATE = {
    "id": "eh_site",
    "recommended_archetype": "weak_flexible",
    "pack_overrides": {"import_p_nom_mw": 40.0},
    "stages": None,
    "dtc_attribution": "per_load",
    "energization": [
        {"year": 2027, "available_import_mw": 20.0, "connection_mw": 40.0},
        {"year": 2030, "available_import_mw": 40.0, "connection_mw": 40.0},
    ],
}
PROJECT = "11111111-1111-1111-1111-111111111111"


def ctx_for(tmp_path, cfg=None, template=TEMPLATE, **state):
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    if template is not None:
        (root / flow_service.TEMPLATE_FILE).write_text(json.dumps(template))
    return types.SimpleNamespace(
        storage_dir=str(root),
        project_uuid=PROJECT,
        solver_state_lock=threading.RLock(),
        solver_state={"solver_config": dict(cfg or {}), **state},
    )


def task_for(tmp_path, steps=(), status="running"):
    path = tmp_path / "task.json"
    task = {
        "task_id": "t1",
        "project_id": PROJECT,
        "status": "running",
        "steps": list(steps),
        "run": {
            "profile": "eh_site@1",
            "status": status,
            "stage": "solve",
            "stages": {},
        },
    }
    path.write_text(json.dumps(task))
    return path, task


def envelope(path, solves=6, audit=(), state="granted", **extra):
    record = {
        "id": "e1",
        "task_id": "t1",
        "project_id": PROJECT,
        "owner_id": "u",
        "version": 1,
        "granted_at": "2026-11-06T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "fingerprint": "f",
        "chain": [],
        "profile": "eh_site@1",
        "edits": [],
        "runs": [],
        "budget": {"solves": solves, "rounds": 6, "wall_time_h": 8.0},
        "research": {},
        "notify": {},
        "state": state,
        "audit": list(audit),
        **extra,
    }
    run_envelope.path_for(path).write_text(json.dumps(record))
    return record


def frozen_step(ctx, solves=6, **step):
    frozen = run_eh.freeze(ctx, PROFILE, solves)
    return {
        "id": "solve.2",
        "tool": "run_eh_study",
        "args": dict(frozen["args"]),
        "frozen": frozen,
        "status": "pending",
        "attempt_id": "a1",
        **step,
    }


# ── The profile and the envelope (C1) ────────────────────────────────────────


def test_the_solve_stage_runs_the_eh_study_after_the_solve_on_unfrozen_args():
    actions = PROFILE["stages"]["solve"]["actions"]
    assert [a["tool"] for a in actions] == ["solve_queue_enqueue", "run_eh_study"]
    assert (
        actions[1]["args"] == run_eh.UNFROZEN
        and "run_eh_study" in run_profiles.BOUND_CONTEXT_TOOLS
    )
    steps = run_profiles.materialise(PROFILE, "solve", PROJECT)
    assert steps[1] == {
        "id": "solve.2",
        "tool": "run_eh_study",
        "args": run_eh.UNFROZEN,
        "status": "pending",
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda s: {
            **s,
            "solve": {
                **s["solve"],
                "actions": [
                    {
                        "tool": "run_eh_study",
                        "args": {"pack_overrides": {"target_lole_h": 99}},
                    }
                ],
            },
        },
        lambda s: {
            **s,
            "result_gate": {
                **s["result_gate"],
                "actions": [run_profiles.EH_STUDY_ACTION],
            },
        },
        lambda s: {
            **s,
            "solve": {
                **s["solve"],
                "actions": [{"tool": "solve_queue_enqueue", "args": run_eh.UNFROZEN}],
            },
        },
    ],
)
def test_a_profile_never_writes_the_eh_studys_args_itself(change):
    with pytest.raises(run_profiles.ProfileError, match="run_eh_study"):
        run_profiles.load(
            {
                **run_profiles.EH_SITE,
                "stages": change(dict(run_profiles.EH_SITE["stages"])),
            }
        )


def test_a_profile_without_its_eh_study_block_cannot_run_the_study():
    with pytest.raises(run_profiles.ProfileError, match="eh_study block"):
        run_profiles.load(
            {k: v for k, v in run_profiles.EH_SITE.items() if k != "eh_study"}
        )


def test_a_grant_that_lists_run_eh_study_is_refused():
    """C1 / F11: an EH study is always the user's own card, in envelope mode too."""
    with pytest.raises(run_envelope.EnvelopeError, match="always its own card"):
        run_envelope._check_run({"tool": "run_eh_study"})
    assert "run_eh_study" in run_envelope.NEVER_LISTED


def test_a_live_envelope_never_covers_the_eh_step(tmp_path, monkeypatch):
    path, _ = task_for(tmp_path)
    envelope(path, runs=[{"tool": "solve_queue_enqueue"}])
    assert run_envelope.covers(path, "solve_queue_enqueue") and not run_envelope.covers(
        path, "run_eh_study"
    )


def test_a_model_turn_is_never_admitted_the_eh_study(tmp_path, monkeypatch):
    """review-gk (#1123): the harness's own EH step is never a model turn's,
    even under a live grant with the run's standing in order."""
    path, task = task_for(tmp_path)
    grant = envelope(path, runs=[{"tool": "solve_queue_enqueue"}])
    monkeypatch.setattr(run_envelope, "_standing", lambda *a, **k: None)
    monkeypatch.setattr(run_envelope, "_names_another_project", lambda *a: False)
    assert (
        run_envelope._refusal(
            None,
            grant,
            task,
            "run_eh_study",
            {"archetype": "weak_flexible"},
            "execution",
            False,
            None,
            None,
            {},
        )
        == "unlisted_tool"
    )


# ── The freeze (C1, F1) ──────────────────────────────────────────────────────


def test_the_freeze_resolves_every_goal_field_the_study_would_read_live(tmp_path):
    ctx = ctx_for(
        tmp_path,
        cfg={"target_lole_h": 2.0, "mc_draws": 300, "site_archetype": "weak_flexible"},
    )
    frozen = run_eh.freeze(ctx, PROFILE, 6)
    assert frozen["args"] == {
        "archetype": "weak_flexible",
        "stages": ["apply_pack", "ens_solve", "mc_certify", "assemble"],
        "budget_solves": 2,
        "mc": {"draws": 300},
        "dtc_attribution": "per_load",
        "pack_overrides": {
            "import_p_nom_mw": 40.0,
            "target_lole_h": 2.0,
            "certification_metric": "mc_lole",
        },
    }
    assert frozen["digest"] == run_eh.digest(frozen["args"])
    assert frozen["goal"]["target_lole_h"] == {"value": 2.0, "source": "stored"}


def test_the_freeze_pins_default_draws_that_the_study_would_read_live(tmp_path):
    """F1: a goal left at its defaults still freezes `mc.draws` (the goal
    merge leaves a default unset, and the study would read it at its start)."""
    from services import reliability_goal

    ctx = ctx_for(tmp_path, cfg={})
    frozen = run_eh.freeze(ctx, PROFILE, 6)
    goal = run_eh.goal_of(ctx)
    assert goal["mc_draws"]["source"] == reliability_goal.DEFAULT
    assert frozen["args"]["mc"] == {"draws": goal["mc_draws"]["value"]}


def test_the_freeze_caps_the_budget_at_the_solves_left_and_refuses_none(tmp_path):
    ctx = ctx_for(tmp_path)
    assert run_eh.freeze(ctx, PROFILE, 1)["args"]["budget_solves"] == 1
    with pytest.raises(run_eh.FreezeError):
        run_eh.freeze(ctx, PROFILE, 0)


def test_the_grant_freezes_the_args_and_the_stage_entry_uses_them(tmp_path):
    ctx = ctx_for(tmp_path, cfg={"mc_draws": 250})
    path, task = task_for(tmp_path)
    granted = run_envelope._eh_frozen(ctx, task, {"budget": {"solves": 6}})
    envelope(path, eh_study=granted)
    ctx.solver_state["solver_config"]["mc_draws"] = (
        999  # after the grant: never read at stage entry
    )
    steps = run_profiles.materialise(PROFILE, "solve", PROJECT)
    run_eh.fill(steps, run_eh.stage_frozen(path, task, PROFILE, ctx))
    assert steps[1]["args"]["mc"] == {"draws": 250} and steps[1]["frozen"] == granted
    assert steps[0]["args"] == {"project_id": PROJECT}


def test_without_a_grant_the_stage_entry_freezes_within_the_profile_budget(tmp_path):
    ctx = ctx_for(tmp_path)
    path, task = task_for(tmp_path)
    steps = run_profiles.materialise(PROFILE, "solve", PROJECT)
    run_eh.fill(steps, run_eh.stage_frozen(path, task, PROFILE, ctx))
    assert steps[1]["args"]["budget_solves"] == 2 and steps[1]["frozen"][
        "digest"
    ] == run_eh.digest(steps[1]["args"])


def test_a_freeze_that_fails_leaves_the_step_unfrozen(tmp_path, monkeypatch):
    path, task = task_for(tmp_path)
    monkeypatch.setattr(run_eh, "goal_of", lambda ctx: 1 / 0)
    monkeypatch.setattr(flow_service, "_request_for", lambda *a: 1 / 0)
    steps = run_profiles.materialise(PROFILE, "solve", PROJECT)
    run_eh.fill(steps, run_eh.stage_frozen(path, task, PROFILE, ctx_for(tmp_path)))
    assert steps[1]["args"] == run_eh.UNFROZEN and "frozen" not in steps[1]


# ── Every execution (C1, C2, F1, F11) ────────────────────────────────────────


def test_the_frozen_args_are_admitted_and_their_solves_reserved(tmp_path):
    ctx = ctx_for(tmp_path)
    path, task = task_for(tmp_path)
    envelope(
        path, audit=[{"event": "used", "tool": "solve_queue_enqueue", "call_id": "c0"}]
    )
    step = frozen_step(ctx)
    assert run_eh.admit(path, task, step, step["args"], ctx) is None
    rows = [a for a in run_envelope._load(path)["audit"] if a["event"] == "eh_study"]
    assert rows == [
        {
            "at": rows[0]["at"],
            "event": "eh_study",
            "call_id": "a1",
            "tool": "run_eh_study",
            "step": "solve.2",
            "reserved": 2,
        }
    ]
    assert (
        harness_runner._budget(task)["used"]["solves"] == 3
        and task["run"]["status"] == "running"
    )


def test_model_supplied_pack_overrides_never_reach_the_call(tmp_path):
    """C1 / F11 (the digest compare): any other args stop the run, eh_args."""
    ctx = ctx_for(tmp_path)
    path, task = task_for(tmp_path)
    envelope(path)
    step = frozen_step(ctx)
    tampered = {**step["args"], "pack_overrides": {"target_lole_h": 99.0}}
    assert run_eh.admit(path, task, step, tampered, ctx)[0] == "eh_args"
    assert task["run"]["status"] == "needs_user" and task["run"]["reason"] == "eh_args"
    assert not [
        a for a in run_envelope._load(path)["audit"] if a["event"] == "eh_study"
    ]


def test_a_step_whose_stored_args_moved_from_its_freeze_is_refused(tmp_path):
    ctx = ctx_for(tmp_path)
    path, task = task_for(tmp_path)
    step = frozen_step(ctx)
    step["frozen"]["args"]["budget_solves"] = (
        9  # the record no longer matches its own digest
    )
    assert run_eh.admit(path, task, step, step["args"], ctx)[0] == "eh_args"
    unfrozen = {**step, "args": run_eh.UNFROZEN}
    unfrozen.pop("frozen")
    assert (
        run_eh.admit(path, task_for(tmp_path)[1], unfrozen, run_eh.UNFROZEN, ctx)[0]
        == "eh_args"
    )


def test_a_goal_edited_after_the_freeze_stops_the_step(tmp_path):
    """F1 / F11 (the goal freeze): the study never runs on a goal its card did not show."""
    ctx = ctx_for(tmp_path)
    path, task = task_for(tmp_path)
    step = frozen_step(ctx)
    ctx.solver_state["solver_config"]["target_lole_h"] = 0.5
    assert run_eh.admit(path, task, step, step["args"], ctx)[0] == "eh_args"
    assert task["run"]["reason"] == "eh_args"


def test_another_projects_context_is_not_the_frozen_goal(tmp_path):
    ctx = ctx_for(tmp_path)
    path, task = task_for(tmp_path)
    step = frozen_step(ctx)
    other = types.SimpleNamespace(
        **{**vars(ctx), "project_uuid": "22222222-2222-2222-2222-222222222222"}
    )
    assert run_eh.admit(path, task, step, step["args"], other)[0] == "eh_args"


@pytest.mark.parametrize("used, left", [(5, 1), (6, 0)])
def test_a_study_that_needs_more_solves_than_are_left_is_refused(tmp_path, used, left):
    """C2 / F11 (the zero-left check): nothing is reserved, the run stops, eh_budget."""
    ctx = ctx_for(tmp_path)
    path, task = task_for(tmp_path)
    envelope(
        path,
        audit=[
            {"event": "used", "tool": "solve_queue_enqueue", "call_id": f"c{i}"}
            for i in range(used)
        ],
    )
    step = frozen_step(ctx)
    kind, message = run_eh.admit(path, task, step, step["args"], ctx)
    assert (
        kind == "eh_budget"
        and f"{left} left" in message
        and task["run"]["reason"] == "eh_budget"
    )
    assert not [
        a for a in run_envelope._load(path)["audit"] if a["event"] == "eh_study"
    ]


# ── The settle (F2, F3, F4) ──────────────────────────────────────────────────


def record(status="done", started=1.0, stages=None, report=True):
    stages = (
        stages
        if stages is not None
        else [
            {"stage": "apply_pack", "status": "run", "note": None, "solves_charged": 0},
            {"stage": "ens_solve", "status": "run", "note": None, "solves_charged": 1},
        ]
    )
    return {
        "status": status,
        "started_at": started,
        "error": None,
        "report": {"pipeline": {"stages": stages}} if report else None,
    }


EXHAUSTED = {
    "stage": "mc_certify",
    "status": "skipped",
    "solves_charged": 0,
    "note": "not run: budget_solves exhausted (2/2) before this stage",
}


@pytest.mark.parametrize(
    "rec, begun, expected",
    [
        (record(), 1.0, ("done", "completed")),
        (
            record(stages=record()["report"]["pipeline"]["stages"] + [EXHAUSTED]),
            1.0,
            ("done", "failed"),
        ),
        (record("failed"), 1.0, ("failed", "failed")),
        (record("aborted"), 1.0, ("aborted", "failed")),
        (record("running"), 1.0, ("unknown", "uncertain")),
        (
            record("completed"),
            1.0,
            ("unknown", "uncertain"),
        ),  # F2: the runner never writes it
        (
            record(started=2.0),
            1.0,
            ("unknown", "uncertain"),
        ),  # F3: another study's record
        (None, 1.0, ("unknown", "uncertain")),
        (record(), None, ("unknown", "uncertain")),
        (record(report=False), 1.0, ("done", "uncertain")),
    ],
)
def test_only_its_own_done_record_with_every_stage_run_completes_the_step(
    rec, begun, expected
):
    """F2 / F3 / F11 (the status map, the identity check)."""
    native, status, error = run_eh.outcome(rec, begun)
    assert (native, status) == expected
    if expected == ("done", "failed"):
        assert "mc_certify: not run: budget_solves exhausted" in error


@pytest.mark.parametrize(
    "stages, count",
    [
        (record()["report"]["pipeline"]["stages"], 1),
        (
            [
                {"stage": "ens_solve", "solves_charged": 3},
                {"stage": "levers", "solves_charged": 4},
            ],
            7,
        ),
        ([{"stage": "ens_solve"}], None),
        ([{"stage": "ens_solve", "solves_charged": "1"}], None),
        ([], None),
    ],
)
def test_the_count_is_the_sum_of_solves_charged_or_unknown(stages, count):
    assert run_eh.charged({"report": {"pipeline": {"stages": stages}}}) == count


def ticket_task(tmp_path, solves_used=1):
    path, task = task_for(
        tmp_path,
        steps=[
            {
                "id": "solve.2",
                "tool": "run_eh_study",
                "status": "waiting",
                "attempt_id": "a1",
                "job": {"job_id": "ad:1", "continuation": "armed"},
            }
        ],
    )
    envelope(
        path,
        audit=[
            {"event": "used", "tool": "solve_queue_enqueue", "call_id": "c0"},
            {
                "event": "eh_study",
                "call_id": "a1",
                "tool": "run_eh_study",
                "reserved": 2,
            },
        ],
    )
    return path, ("t1", "solve.2", "a1")


def settled(monkeypatch):
    calls = []
    from services import assistant_tasks

    monkeypatch.setattr(
        assistant_tasks, "settle_job", lambda *a: calls.append(a) or {"ok": True}
    )
    return calls


def eh_row(path):
    return next(
        a for a in run_envelope._load(path)["audit"] if a["event"] == "eh_study"
    )


def test_a_study_that_charged_more_than_reserved_is_charged_in_full(
    tmp_path, monkeypatch
):
    """F4: never capped down to the reservation."""
    path, ticket = ticket_task(tmp_path)
    calls = settled(monkeypatch)
    stages = [
        {"stage": "ens_solve", "status": "run", "note": None, "solves_charged": 3}
    ]
    run_eh.settle(path, ticket, "ad:1", record(stages=stages), 1.0)
    assert (
        eh_row(path)["solves"] == 3
        and run_envelope.solves_used(run_envelope._load(path)) == 4
    )
    assert calls[0][3:5] == ("done", "completed")


def test_an_unreadable_count_keeps_the_whole_reservation(tmp_path, monkeypatch):
    path, ticket = ticket_task(tmp_path)
    settled(monkeypatch)
    run_eh.settle(
        path,
        ticket,
        "ad:1",
        record(stages=[{"stage": "ens_solve", "status": "run"}]),
        1.0,
    )
    assert (
        eh_row(path)["solves"] == 2
        and run_envelope.solves_used(run_envelope._load(path)) == 3
    )  # C4: closed


def test_a_replaced_record_settles_uncertain_and_keeps_the_reservation(
    tmp_path, monkeypatch
):
    path, ticket = ticket_task(tmp_path)
    calls = settled(monkeypatch)
    run_eh.settle(path, ticket, "ad:1", record(started=5.0), 1.0)
    assert (
        calls[0][3:5] == ("unknown", "uncertain") and eh_row(path)["solves"] == 2
    )  # C4: closed in full


def open_reservations(path):
    return [
        a
        for a in run_envelope._load(path)["audit"]
        if a["event"] == "eh_study" and "solves" not in a
    ]


@pytest.mark.parametrize(
    "rec, solves",
    [
        (record(), 1),  # done: what it charged
        (record(status="failed"), 1),  # failed: what it charged
        (
            record(stages=[{"stage": "ens_solve", "status": "run"}]),
            2,
        ),  # unreadable count: in full
        (record(started=5.0), 2),  # replaced: in full
        (None, 2),  # gone: in full
    ],
)
def test_no_reservation_stays_open_after_a_settle(tmp_path, monkeypatch, rec, solves):
    """C4 addendum: done, failed, unknown."""
    path, ticket = ticket_task(tmp_path)
    settled(monkeypatch)
    run_eh.settle(path, ticket, "ad:1", rec, 1.0)
    assert not open_reservations(path) and eh_row(path)["solves"] == solves


def test_a_settle_that_raises_closes_the_reservation_in_full(tmp_path, monkeypatch):
    """C4 addendum: a raise in the follower."""
    path, ticket = ticket_task(tmp_path)
    monkeypatch.setattr(run_eh, "settle", lambda *a: 1 / 0)
    run_eh._follow(path, ticket, "ad:1", ctx_for(tmp_path), 1.0)
    assert not open_reservations(path) and eh_row(path)["solves"] == 2


@pytest.mark.parametrize("bindings", [True, False])
def test_the_follower_reads_its_own_record_never_a_later_users_study(
    tmp_path, monkeypatch, bindings
):
    """F3: a user starts an EH study after the run's own one ended; the run's
    step is settled from its own record (by started_at), with or without the
    job adapters' activity bindings."""
    from harness import job_adapters

    path, ticket = ticket_task(tmp_path)
    calls = settled(monkeypatch)
    ctx = ctx_for(
        tmp_path, eh_study=record(started=9.0)
    )  # the user's, already replacing the run's
    if not bindings:
        monkeypatch.setattr(
            job_adapters, "_ACTIVITY_BINDINGS", type(job_adapters._ACTIVITY_BINDINGS)()
        )
    run_eh._follow(path, ticket, "ad:1", ctx, 1.0)
    assert calls[0][3:5] == ("unknown", "uncertain")
    ctx.solver_state["eh_study"] = record(started=1.0)
    run_eh._follow(path, ticket, "ad:1", ctx, 1.0)
    assert calls[1][3:5] == ("done", "completed")


def test_the_step_keeps_the_started_at_its_own_receipt_named(tmp_path, monkeypatch):
    """F3: the identity is the one the step's own start published (its
    receipt), never the record read after the call returned: a user's study
    that replaced it in between is never the step's."""
    from services import assistant_tasks

    path, ticket = ticket_task(tmp_path)
    monkeypatch.setattr(
        assistant_tasks, "_read", lambda task_id: (path, json.loads(path.read_text()))
    )
    monkeypatch.setattr(assistant_tasks, "await_job", lambda *a: path)
    followed = threading.Event()
    seen = []
    monkeypatch.setattr(run_eh, "_follow", lambda *a: seen.append(a) or followed.set())
    ctx = ctx_for(
        tmp_path, eh_study=record(started=9.0)
    )  # a user's study, already replacing the run's
    assert run_eh.wait(ticket, "ad:1", "s1", ctx, 1.0)
    assert followed.wait(5) and seen[0][-1] == 1.0
    assert json.loads(path.read_text())["steps"][0]["job"]["started_at"] == 1.0


def test_a_refused_start_releases_the_reservation(tmp_path):
    """F5: a 409 or 422 spends nothing."""
    path, ticket = ticket_task(tmp_path)
    task = json.loads(path.read_text())
    assert run_eh.release(path, "a1", 0, task) and eh_row(path)["solves"] == 0
    assert harness_runner._budget(task)["used"]["solves"] == 1
    assert not run_eh.release(path, "a1", 0, task)  # once


@pytest.mark.parametrize(
    "result, error",
    [
        (
            None,
            HTTPException(
                409, {"error_kind": "study_busy", "message": "a study is running"}
            ),
        ),
        (None, HTTPException(422, {"error_kind": "bad_request", "message": "invalid"})),
        ({"status": "refused", "error_kind": "campaign_cap"}, None),
    ],
)
def test_a_refused_start_through_dispatch_fails_the_step_and_releases(
    tmp_path, monkeypatch, result, error
):
    """F5, through `finish_step`: a start that never ran settles failed, its
    reservation released, and nothing is charged or retried."""
    from services import assistant_tasks

    path, ticket = ticket_task(tmp_path)
    task = json.loads(path.read_text())
    del task["steps"][0]["job"]  # the start's own receipt, before any job is followed
    task.update(owner_id="u1", title="Own site study")
    path.write_text(json.dumps(task))
    monkeypatch.setattr(assistant_tasks, "_path", lambda task_id: (path, PROJECT, "u1"))
    monkeypatch.setattr(
        assistant_tasks, "_view", lambda task, *a, **k: None
    )  # the reply, not under test
    assistant_tasks.finish_step(ticket, result=result, error=error)
    after = json.loads(path.read_text())
    assert after["steps"][0]["status"] == "failed"
    assert eh_row(path)["solves"] == 0 and not open_reservations(path)  # C4
    assert harness_runner._budget(after)["used"]["solves"] == 1


def test_a_start_left_unfollowed_closes_its_reservation_in_full(tmp_path, monkeypatch):
    """C4 addendum: a started study whose record is not followed settles
    uncertain, charged its whole reservation."""
    from services import assistant_tasks

    path, ticket = ticket_task(tmp_path)
    task = json.loads(path.read_text())
    del task["steps"][0]["job"]
    task.update(owner_id="u1", title="Own site study")
    path.write_text(json.dumps(task))
    monkeypatch.setattr(assistant_tasks, "_path", lambda task_id: (path, PROJECT, "u1"))
    monkeypatch.setattr(assistant_tasks, "_view", lambda task, *a, **k: None)
    assistant_tasks.finish_step(ticket, result={"status": "running", "started_at": 1.0})
    assert json.loads(path.read_text())["steps"][0]["status"] == "uncertain"
    assert not open_reservations(path) and eh_row(path)["solves"] == 2


# ── The facts (C3, F2, F3, F6, F7, F8) ───────────────────────────────────────


def env_for(tmp_path, ctx=None, steps=(), template=TEMPLATE):
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    if template is not None:
        (root / flow_service.TEMPLATE_FILE).write_text(json.dumps(template))
    task = {
        "task_id": "t1",
        "project_id": PROJECT,
        "steps": list(steps),
        "run": {"profile": "eh_site@1"},
    }
    return Env(task=task, ctx=ctx, project=None, project_dir=root)


def eh_done(started=1.0):
    return {
        "id": "solve.2",
        "tool": "run_eh_study",
        "status": "completed",
        "result": {"started_at": started},
    }


def read(name, env):
    fact = run_gates.FACTS[name]
    return fact.read(env)


@pytest.mark.parametrize(
    "energization, value",
    [
        ([{"available_import_mw": 20.0}, {"available_import_mw": 40.0}], 20.0),
        ([{"available_import_mw": 0}], 0.0),
        ([{"available_import_mw": 20.0}, {"available_import_mw": float("nan")}], None),
        ([{"available_import_mw": "20"}], None),
        ([{"available_import_mw": True}], None),
        ([{"year": 2030}], None),
        ([], None),
        (None, None),
    ],
)
def test_energization_reads_none_for_a_missing_or_non_finite_stage(
    tmp_path, energization, value
):
    """F7."""
    template = {**TEMPLATE, "energization": energization}
    assert read("eh.energization.min_mw", env_for(tmp_path, template=template)) == value


def test_no_template_reads_no_energization(tmp_path):
    assert read("eh.energization.min_mw", env_for(tmp_path, template=None)) is None


def fact_gate(monkeypatch, rules, values):
    """The real gate over `rules`, each fact read as `values` gives it."""
    for name, value in values.items():
        real = run_gates.FACTS[name]
        monkeypatch.setitem(
            run_gates.FACTS,
            name,
            run_gates.Fact(
                real.reads, real.key, real.unit, (lambda v: lambda env: v)(value)
            ),
        )
    env = Env(
        task={"task_id": "t1", "steps": [], "run": {}},
        ctx=None,
        project=None,
        project_dir=None,
    )
    results = run_gates.evaluate(
        rules, env
    )  # gate()'s own verdict, without its context pinning
    return {"rules": results, "passed": run_gates.passed(results)}


@pytest.mark.parametrize(
    "fact, missing",
    [
        ("eh.readiness.ready", None),
        ("eh.energization.min_mw", None),
        ("eh.readiness.ready", NoEvidence("x", "unreadable")),
    ],
)
def test_not_evaluated_at_the_input_gate_blocks_and_never_passes(
    monkeypatch, fact, missing
):
    """F7, C3."""
    values = {"eh.readiness.ready": True, "eh.energization.min_mw": 0.0, fact: missing}
    gate = fact_gate(monkeypatch, run_profiles.EH_INPUT_RULES, values)
    assert not gate["passed"]
    assert {r["rule"]: r["verdict"] for r in gate["rules"]}[fact] == "not_evaluated"
    assert fact_gate(
        monkeypatch,
        run_profiles.EH_INPUT_RULES,
        {"eh.readiness.ready": True, "eh.energization.min_mw": 0.0},
    )["passed"]


@pytest.mark.parametrize(
    "fact", ["eh.study.status", "eh.review.blockers", "kpi.unserved_mwh"]
)
def test_an_unreadable_result_fact_fails_its_rule(monkeypatch, fact):
    """C3: a reader that raises is no evidence, never a pass."""
    real = run_gates.FACTS[fact]
    monkeypatch.setitem(
        run_gates.FACTS,
        fact,
        run_gates.Fact(real.reads, real.key, real.unit, lambda env: 1 / 0),
    )
    rules = [
        r
        for r in run_profiles.EH_SOLVE_RULES + run_profiles.EH_RESULT_RULES
        if r["fact"] == fact
    ]
    gate = run_gates.gate(
        rules,
        Env(
            task={"task_id": "t1", "steps": [], "run": {}},
            ctx=None,
            project=None,
            project_dir=None,
        ),
    )
    assert not gate["passed"] and gate["rules"][0]["verdict"] == "not_evaluated"


def readiness(monkeypatch, tmp_path, result):
    from services import chat_tools

    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    monkeypatch.setattr(chat_tools, "get_eh_readiness", lambda *a: result)
    return read("eh.readiness.ready", env_for(tmp_path, ctx=ctx_for(tmp_path)))


def rows(*predictions):
    return [{"stage": f"s{i}", "prediction": p} for i, p in enumerate(predictions)]


@pytest.mark.parametrize(
    "result, value",
    [
        (
            {
                "import": {"applied": True},
                "stages": rows("run", "run", "not_requested"),
            },
            True,
        ),
        ({"import": {"applied": True}, "stages": rows("run", "skipped_budget")}, False),
        (
            {"import": {"applied": True}, "stages": rows("run", "may_skip_budget")},
            False,
        ),
        (
            {"import": {"applied": True}, "stages": rows("run", "not_established")},
            False,
        ),
        ({"import": {"applied": False}, "stages": rows("fails", "not_reached")}, False),
        ({"import": {"applied": True}, "stages": rows("not_requested")}, False),
        (
            {
                "import": {"applied": True},
                "stages": rows("run"),
                "truncated": {"stages": 12},
            },
            None,
        ),
        ({"import": {"applied": True}}, None),
        (None, None),
    ],
)
def test_readiness_is_ready_only_when_every_requested_stage_runs(
    monkeypatch, tmp_path, result, value
):
    assert readiness(monkeypatch, tmp_path, result) == value


def test_readiness_reads_the_args_the_study_would_run_with(monkeypatch, tmp_path):
    from services import chat_tools

    seen = []
    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    monkeypatch.setattr(
        chat_tools, "get_eh_readiness", lambda *a: seen.append(a) or None
    )
    read("eh.readiness.ready", env_for(tmp_path, ctx=ctx_for(tmp_path)))
    assert seen == [
        (
            "weak_flexible",
            2,
            ["apply_pack", "ens_solve", "mc_certify", "assemble"],
            "per_load",
            {"import_p_nom_mw": 40.0},
        )
    ]


def test_readiness_reads_the_args_the_grant_froze(monkeypatch, tmp_path):
    """C1: in envelope mode the input gate predicts the study the grant
    froze (its granted budget), not one frozen again from the profile."""
    from services import chat_tools

    seen = []
    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    monkeypatch.setattr(
        chat_tools, "get_eh_readiness", lambda *a: seen.append(a) or None
    )
    env = env_for(tmp_path, ctx=ctx_for(tmp_path))
    (env.project_dir / "assistant_tasks").mkdir()
    granted = {
        "archetype": "strong_grid",
        "budget_solves": 1,
        "stages": ["apply_pack", "ens_solve"],
    }
    envelope(
        env.project_dir / "assistant_tasks" / "t1.json", eh_study={"args": granted}
    )
    read("eh.readiness.ready", env)
    assert seen == [("strong_grid", 1, ["apply_pack", "ens_solve"], None, None)]


@pytest.mark.parametrize(
    "steps, state, expected",
    [
        ([], {"eh_study": record()}, "not_own"),  # no EH step of the run's own
        ([eh_done()], {}, "not_own"),
        (
            [eh_done()],
            {"eh_study": record(started=2.0)},
            "not_own",
        ),  # a later user study replaced it
        ([{**eh_done(), "status": "failed"}], {"eh_study": record()}, "not_own"),
        (
            [eh_done()],
            {
                "eh_study": record(
                    stages=record()["report"]["pipeline"]["stages"] + [EXHAUSTED]
                )
            },
            "stage_skipped",
        ),
        ([eh_done()], {"eh_study": record()}, "done"),
        ([eh_done()], {"eh_study": record("failed")}, "failed"),
    ],
)
def test_the_study_status_reads_the_runs_own_record_only(
    tmp_path, steps, state, expected
):
    value = read(
        "eh.study.status",
        env_for(tmp_path, ctx=ctx_for(tmp_path, **state), steps=steps),
    )
    assert (value.value if isinstance(value, NoEvidence) else value) == expected


def test_the_review_reads_the_runs_own_record_never_the_latest(tmp_path, monkeypatch):
    """F3, F8: review_report on the record the step settled from."""
    from services.adequacy import eh_review

    seen = []

    def review(report, rec):
        seen.append(rec["started_at"])
        return {
            "findings": [
                {"id": "certification_fail", "severity": "high"},
                {"id": "budget_tight", "severity": "low"},
            ]
        }

    monkeypatch.setattr(eh_review, "review_report", review)
    ctx = ctx_for(tmp_path, eh_study=record())
    assert read(
        "eh.review.blockers", env_for(tmp_path, ctx=ctx, steps=[eh_done()])
    ) == ["certification_fail"]
    ctx.solver_state["eh_study"] = record(started=7.0)
    assert (
        read("eh.review.blockers", env_for(tmp_path, ctx=ctx, steps=[eh_done()])).value
        == "not_own"
    )
    assert seen == [1.0]


@pytest.mark.parametrize(
    "out", [{"findings": "x"}, {"findings": [], "truncated": True}, None]
)
def test_a_review_that_cannot_be_read_whole_is_none(tmp_path, monkeypatch, out):
    """F8: an errored or truncated review fails the rule."""
    from services.adequacy import eh_review

    monkeypatch.setattr(eh_review, "review_report", lambda *a: out)
    env = env_for(tmp_path, ctx=ctx_for(tmp_path, eh_study=record()), steps=[eh_done()])
    assert read("eh.review.blockers", env) is None


def test_a_study_record_without_a_report_reads_no_review(tmp_path, monkeypatch):
    """F8, fail closed: the own record carries no report to review."""
    from services.adequacy import eh_review

    monkeypatch.setattr(eh_review, "review_report", lambda *a: {"findings": []})
    env = env_for(
        tmp_path,
        ctx=ctx_for(tmp_path, eh_study=record(report=False)),
        steps=[eh_done()],
    )
    assert read("eh.review.blockers", env) is None


def lost_load_env(
    tmp_path,
    monkeypatch,
    *,
    job,
    weights=(2.0, 2.0, 3.0),
    shed=(0.0, 1.5, 0.5),
    exact="x1",
):
    """A three-snapshot network with non-unit generator weightings and the
    solve's lost-load capture; the run's own solve job is `job`."""
    import pypsa

    from harness import calculations, durable

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.snapshot_weightings.loc[:, "generators"] = list(weights)
    n.snapshot_weightings.loc[:, "objective"] = list(weights)
    n.add("Bus", "b")
    n.add("Load", "site", bus="b", p_set=5.0)
    n.meta = {"assistant_solve_exact_input_fingerprint": exact}
    frame = pd.DataFrame({"site": list(shed)}, index=n.snapshots)
    ctx = ctx_for(
        tmp_path, last_lost_load={"lost_load_t": frame, "voll_eur_per_mwh": 1000.0}
    )
    ctx.network = n
    monkeypatch.setattr(run_gates, "_on", lambda c, fn, *a: fn(*a))
    monkeypatch.setattr(durable, "network_guard", lambda: nullcontext())
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda c: True)
    monkeypatch.setattr(run_gates, "_solve_job", lambda env: job)
    return env_for(tmp_path, ctx=ctx)


DONE_JOB = {"status": "completed", "capture": {"applied": True, "exact": "x1"}}


def test_unserved_energy_weights_each_snapshot_by_its_generator_weighting(
    tmp_path, monkeypatch
):
    """F6: 1.5 MW × 2 h + 0.5 MW × 3 h = 4.5 MWh (a unit weighting would read 2.0)."""
    env = lost_load_env(tmp_path, monkeypatch, job=DONE_JOB)
    assert read("kpi.unserved_mwh", env) == pytest.approx(4.5)


def test_no_shed_reads_zero(tmp_path, monkeypatch):
    assert (
        read(
            "kpi.unserved_mwh",
            lost_load_env(tmp_path, monkeypatch, job=DONE_JOB, shed=(0, 0, 0)),
        )
        == 0.0
    )


@pytest.mark.parametrize(
    "job, exact, reason",
    [
        (None, "x1", None),
        ({"status": "running"}, "x1", None),
        (
            {
                "status": "completed",
                "capture": {"applied": False, "reason": "inputs_changed"},
            },
            "x1",
            "inputs_changed",
        ),
        ({"status": "completed", "capture": {"applied": True}}, "x1", "no_capture"),
        (DONE_JOB, "x2", "other_solve"),  # the live results are another solve's
    ],
)
def test_unserved_energy_of_anything_but_the_runs_own_solve_is_no_evidence(
    tmp_path, monkeypatch, job, exact, reason
):
    value = read(
        "kpi.unserved_mwh", lost_load_env(tmp_path, monkeypatch, job=job, exact=exact)
    )
    assert (value.value if isinstance(value, NoEvidence) else value) == reason


def test_a_stale_solve_reads_no_unserved_energy(tmp_path, monkeypatch):
    from harness import calculations

    env = lost_load_env(tmp_path, monkeypatch, job=DONE_JOB)
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda c: False)
    assert read("kpi.unserved_mwh", env) is None


def test_a_solve_without_its_lost_load_capture_reads_no_evidence(tmp_path, monkeypatch):
    """F6, fail closed: the source cannot be built (here: no capture)."""
    env = lost_load_env(tmp_path, monkeypatch, job=DONE_JOB)
    env.ctx.solver_state.pop("last_lost_load")
    assert read("kpi.unserved_mwh", env).value == "no_lost_load_capture"


def test_unserved_energy_never_reads_without_the_runs_context(tmp_path):
    assert read("kpi.unserved_mwh", env_for(tmp_path, ctx=None)) is None


# ── The LOLE rule (F9) ───────────────────────────────────────────────────────


def test_the_lole_rule_drives_the_margin_loop_on_the_goal_over_the_horizon(monkeypatch):
    """F9: the engine call leaves target and draws to `active_loop_goal`,
    which puts the goal's h/yr on `horizon_years(n)` (here 3 years)."""
    from services import reliability_goal

    rule = next(r for r in run_profiles.EH_RESULT_RULES if r.get("engine"))
    assert rule["fact"] == run_gates.LOOP_FACT and rule["engine"]["restore"] == "base"
    goal = {
        "target_lole_h": {"value": 2.0, "source": "stored"},
        "mc_draws": {"value": 400, "source": "stored"},
    }
    body = types.SimpleNamespace(target_lole_h=None, draws=None)
    assert reliability_goal.loop_goal(body, goal, 3.0) == (
        6.0,
        400,
        {"target_lole_h": "stored", "mc_draws": "stored"},
    )


def test_the_gates_lole_verdict_is_the_loop_record_only(tmp_path):
    """F9: a done EH study whose own MC LOLE meets the goal is no LOLE
    verdict; only the loop's record is."""
    steps = [eh_done()]
    env = env_for(tmp_path, ctx=ctx_for(tmp_path, eh_study=record()), steps=steps)
    rule = next(r for r in run_profiles.EH_RESULT_RULES if r.get("engine"))
    value = run_gates.FACTS[run_gates.LOOP_FACT].read(env, rule)
    assert isinstance(value, NoEvidence) and value.value == "not_started"


# ── C8: eh_site@1 in place ───────────────────────────────────────────────────


def test_a_run_stored_past_solve_entry_resolves_and_never_passes_the_new_rules(
    tmp_path,
):
    """C8: no EH step (the run entered solve before B2): the new result rules
    read no record and are not evaluated, so the gate fails for the user."""
    env = env_for(
        tmp_path,
        ctx=ctx_for(tmp_path, eh_study=record()),
        steps=[{"id": "solve.1", "tool": "solve_queue_enqueue", "status": "completed"}],
    )
    rules = [
        r for r in run_profiles.EH_RESULT_RULES if not r.get("engine")
    ] + run_profiles.EH_SOLVE_RULES
    results = run_gates.evaluate(rules, env)
    by = {r["rule"]: r["verdict"] for r in results}
    assert by["eh.review.blockers"] == by["eh.study.status"] == "not_evaluated"
    assert not run_gates.passed(results)


# ── F10: no project write ────────────────────────────────────────────────────


def test_the_eh_study_runs_on_a_private_copy(monkeypatch):
    """F10: `run_eh_study` copies the network, under its lock, before any
    stage reads or changes it, so the step writes nothing to the project and
    adds no ledger row."""
    import pypsa

    from services.adequacy import eh_study

    class Seen(Exception):
        pass

    def first_read(pack, *, network, dsr_buses):
        raise Seen(network)

    monkeypatch.setattr(eh_study.arch, "solver_config_patch_with_preflight", first_read)
    shared = pypsa.Network()
    shared.add("Bus", "b")
    with pytest.raises(Seen) as seen:
        eh_study.run_eh_study(
            shared,
            None,
            None,
            lock=threading.Lock(),
            stop_event=threading.Event(),
            stages=["apply_pack", "ens_solve"],
        )
    assert seen.value.args[0] is not shared
    assert list(seen.value.args[0].buses.index) == ["b"]
    assert not run_profiles.NATIVE_STEPS.get("solve")


def test_the_step_adds_no_ledger_row_and_no_chain_link(tmp_path, monkeypatch):
    """F10, C7: admitted, started and settled, the EH step leaves the run's
    ledger and the envelope's chain as they were (only its reservation row)."""
    ctx = ctx_for(tmp_path)
    path, task = task_for(tmp_path)
    task["run"]["ledger"] = [{"id": "chg-1", "tool": "update_component"}]
    envelope(
        path,
        audit=[{"event": "used", "tool": "solve_queue_enqueue", "call_id": "c0"}],
        chain=[{"id": "chg-1"}],
    )
    step = frozen_step(ctx)
    task["steps"] = [{**step, "status": "waiting", "job": {"job_id": "ad:1"}}]
    assert run_eh.admit(path, task, step, step["args"], ctx) is None
    path.write_text(json.dumps(task))
    settled(monkeypatch)
    run_eh.settle(path, ("t1", "solve.2", "a1"), "ad:1", record(), 1.0)
    assert json.loads(path.read_text())["run"]["ledger"] == [
        {"id": "chg-1", "tool": "update_component"}
    ]
    grant = run_envelope._load(path)
    assert grant["chain"] == [{"id": "chg-1"}]
    assert [a["event"] for a in grant["audit"]] == ["used", "eh_study"] and grant[
        "audit"
    ][1]["solves"] == 1


def test_module_path():
    assert Path(run_eh.__file__).name == "run_eh.py"
