"""Agent turns and the input loop L1 (study harness issue 04; design §6.1,
§6.3-6.5; owner decisions SH-1, SH-ENVELOPE-1, AI-558, TOOLSEL-1).

**Agent turn.** `loop.run_turn` on the runner's own session `run:<task>`,
headless: its frames go to the run's event log, never to an SSE client.
The stage instruction is per-turn content, read from the package only
(`harness/workflows/profiles/<profile>/<stage>.md`), so the system prompt
stays cached. The turn offers the stage's allowlist only
(`session.stage_tools`, honoured where the tool payload is built), and runs
Guided: every write goes through the confirmation gate, where the approval
envelope admits what it covers. A card is never shown: the turn withdraws
it on the spot and counts it as a refusal (nothing ran).

**L1.** A failed `block` input rule starts the loop when the run's
envelope is live: at most 3 rounds (the profile may lower it, the envelope's
round budget caps it). A round whose gate leaves the same failing set and the
same network fingerprint as the gate before it ends the loop `no_progress`;
running out of rounds ends it `exhausted`. Either way the stage is `failed`
and the run `needs_user`, with its failing rules and its change ledger.

**Ledger.** Every write a round makes appends one `ChangeRow` (§6.4) from its
verified edit receipt (LC-04). A write that ran without one appends nothing
and fails the round: the loop ends `unverified_write`, `needs_user`.

**Budget.** `run.budget.used.rounds` counts rounds and the token counts come
from the session's own `usage_acc` (budget.py), metered by
`runner.charge`; nothing is taken from the model's text.

**L2 (issue 05, §6.2).** A failed `block` result rule, or a solve that did
not finish optimal (the solve stage's job `failed`), starts the fix-and-rerun
loop when the envelope is live: at most 2 rounds, each with at most 2 changes
(counted at admission, `run_envelope.CHANGE_CAP_KIND`), each consuming a
solve. The harness reads the triage first (the job, its log, diagnose and
validate), fenced and size-capped; an unreadable triage ends the loop
`triage_unreadable`, never a blind round. A round with no settled change ends
it `no_change` (SC-14: no re-solve without a change); otherwise the run
enters the solve stage again, through the input gate. Each round is a cycle
record on `run.loop.history` (HH-06): the job the gate judged, its findings,
the changes, and the next job with its outcome. A capture solved after the
inputs changed (`capture.applied` false, C6f) ends it `inputs_changed`: the
solve counts as spent. Every ending but `passed` leaves the stage `failed`
and the run `needs_user` with the failing rules (C7); the loop never nests.
"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import re
import time
import uuid
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

LOOP = "L1"
STAGE = "input_gate"
REASON_CHARS = 240
L2 = "L2"
RESULT_STAGE = "result_gate"
RESULT_STAGES = ("solve", RESULT_STAGE)  # the gates whose failure may start L2
TRIAGE_CHARS, LOG_LINES = 6000, 40
# HH-10 (C6f): a captured solve whose result was not applied, by its capture reason.
NOT_APPLIED = frozenset({"inputs_changed", "write_back_failed", "not_applied"})


def _tasks():
    from services import assistant_tasks
    return assistant_tasks


# ── The stage instruction (package files only, contract 4) ──────────────────


def instruction(profile_ref: str, stage: str) -> str:
    """The profile's stage file, from the package; ValueError when it has none."""
    from services import run_profiles
    profile_id = str(profile_ref).split("@", 1)[0]
    if not re.fullmatch(r"[a-z0-9_]{1,40}", profile_id) or stage not in run_profiles.STAGES:
        raise ValueError(f"no stage instruction for {profile_ref!r} {stage!r}")
    source = resources.files("harness.workflows").joinpath("profiles", profile_id, f"{stage}.md")
    if not source.is_file():
        raise ValueError(f"no stage instruction for {profile_ref!r} {stage!r}")
    return source.read_text(encoding="utf-8")


# ── L1 bookkeeping (called by `_advance_run` under the task lock) ───────────


def _failing(gate):
    return sorted(r["rule"] for r in gate["rules"] if r["severity"] == "block" and r["verdict"] != "pass")


def rounds_left(task, path):
    """The envelope's agent-round budget (§6.5) less the rounds used."""
    from harness import runner
    from services import run_envelope
    envelope = run_envelope.read(path) or {}
    limit = ((envelope.get("budget") or {}).get("rounds")) or 0
    return limit - runner._budget(task)["used"].get("rounds", 0)


def after_failed_gate(path, task, gate, profile):
    """The input gate failed. True when an L1 round is now due (the stage
    stays active); False when the stage fails, the loop's ending (if one ran)
    on `run.loop`. Only harness code calls it, from native gate results."""
    from services import run_envelope
    run = task["run"]
    agent = profile["stages"][STAGE].get("agent")
    loop = run.get("loop")
    if loop is not None and loop["stage"] != STAGE:
        if not loop.get("ended"):
            _end_cycle(loop, None, "input_gate_failed")
            loop["ended"] = "input_gate_failed"  # never nested: no L1 inside an L2 re-solve
            return False
        run.setdefault("loops", []).append(run.pop("loop"))
        loop = None
    if loop is not None and loop.get("ended"):
        loop = None  # the user resumed after an ended loop: a fresh loop, the round budget still counts
    if agent is None or not run_envelope.live(path):
        if loop is not None:
            loop["ended"] = "no_envelope"
        return False
    failing = _failing(gate)
    if loop is None:
        loop = {"kind": LOOP, "stage": STAGE, "rounds": 0, "limit": agent["rounds"], "refusals": 0, "history": []}
        run["loop"] = loop
    last = loop["history"][-1] if loop["history"] else None
    if last is not None and last["failing"] == failing and last["fingerprint"] == gate["fingerprint"]:
        loop["ended"] = "no_progress"
    elif loop["rounds"] >= loop["limit"] or rounds_left(task, path) <= 0:
        loop["ended"] = "exhausted"
    else:
        loop["due"] = {"failing": failing, "fingerprint": gate["fingerprint"],
                       "rules": [r for r in gate["rules"] if r["rule"] in failing]}
        return True
    return False


def _fail(task, reason):
    """The loop ends without a pass: the stage `failed`, the run `needs_user`."""
    run = task["run"]
    loop = run["loop"]
    loop.pop("due", None)
    _end_cycle(loop, None, reason)
    loop["ended"] = reason
    stage = run["stages"][run["stage"]]
    stage["status"] = "failed"
    if loop["kind"] == L2:
        stage["reason"] = reason
    if run["status"] in ("running", "waiting_job"):
        run["status"], task["status"] = "needs_user", "needs_review"


def end(path, reason):
    """End a due loop from outside a round (a round that errored)."""
    from harness import runner

    def change(task):
        if not due(task):
            return False
        _fail(task, reason)
    runner._update(path, change)


def _unset(path, ctx):
    """`unset(target)` for `run_answers.data_items`: whether an envelope gap
    target is still empty in the run's project now. A reading that fails
    counts as unset (the item is asked, never assumed given)."""
    from services import run_envelope

    def unset(target):
        try:
            if target["component"] == "Load":
                from fnmatch import fnmatchcase

                from services.adequacy.site_peak import load_split
                electric, _ = load_split(ctx.network)
                return any(fnmatchcase(str(n), target["name"]) and not run_envelope._has_series(ctx, n)
                           for n in electric)
            if (target["name"], target["attr"]) == ("Commercial", "import_tariff_ref"):
                from harness.edit_card_site import _solver_commercial
                return not (_solver_commercial() or {}).get("import_tariff_ref")
            if target["name"] == "Weather":
                from services.profile_gen import weather_prefs
                return (weather_prefs.saved_choice(Path(path).parent.parent) or {}).get(target["attr"]) is None
        except Exception:  # noqa: BLE001 — unreadable: ask
            return True
        return True
    return unset


def data_gaps(path, task, profile, ctx):
    """Issue 21 §3 (GK B (20) Q2): the data items of the one notice when L1
    ends without a pass, read from the state now (outside the task lock):
    envelope gap targets still unset and profile `inputs.required` items
    still absent. Other failed rules are never items."""
    from services import run_answers, run_envelope
    envelope = run_envelope.read(path)
    run = task["run"]
    rows = run.get("intake") or []
    rows = rows.get("rows", []) if isinstance(rows, dict) else rows
    # Absent, not falsy: a param a usable intake row or an answer gave is present.
    present = {r.get("param") for r in rows if isinstance(r, dict) and r.get("status") in ("proposed", "confirmed")}
    present |= {i[1] for a in run.get("answers", []) for i in a.get("items", []) if i and i[0] == "param"}
    return run_answers.data_items(envelope, profile, unset=_unset(path, ctx), present=present.__contains__)


def data_request(task, items):
    """The L1 exit's one data request (§3), under the task lock: the items
    not asked before in this run, as `run.waiting = {kind: "data", ref,
    items}` and the harness's notice text. Returns the request, or None."""
    from services import run_answers, run_intake
    run = task["run"]
    req = run_answers.request(run.get("requests"), items or [])
    if req is None:
        return None
    req["notice"] = ("The input loop could not fill these, so the run waits for you. Give each through a "
                     "card in this chat:\n" + "\n".join(run_intake.request_lines(
                         [{"param": i.get("param") or i["target"]["attr"], "kind": "missing", "label": i["label"]}
                          for i in req["items"]])))
    from harness import runner
    run.setdefault("requests", []).append(req)
    # Issue 21d: through the runner's one `run.waiting` writer, in this locked update.
    runner.set_waiting(run, "data", req["id"], time.time(), reason=False,
                       items=req["items"], notice=req["notice"])
    return req


def after_passed_gate(task, stage=STAGE):
    """`stage`'s gate passed: its own loop, once it ran a round, ends `passed`."""
    loop = task["run"].get("loop")
    if loop is not None and loop["stage"] == stage and not loop.get("ended") and loop["rounds"]:
        _end_cycle(loop, job_of(task), "passed")
        loop["ended"] = "passed"


# ── L2 bookkeeping (`_advance_run`, under the task lock) ────────────────────


def job_of(task):
    """The job id of the run's latest queued solve step, or None (the solve
    stage's EH study stores its own job, issue 07 B2)."""
    step = next((s for s in reversed(task["steps"]) if s["id"].startswith("solve.") and s.get("job")
                 and s["tool"] == "solve_queue_enqueue"), None)
    return step["job"].get("job_id") if step else None


def _end_cycle(loop, job, outcome):
    """HH-06: the last cycle's next solve and what its gate said."""
    if loop["kind"] == L2 and loop["history"] and loop["history"][-1].get("outcome") is None:
        loop["history"][-1].update(next_job=job, outcome=outcome)


def _not_applied(gate):
    """C6f: the capture reason when the gate read a captured solve that was not applied."""
    for rule in gate["rules"]:
        value = rule["facts"][0].get("value") if rule.get("facts") else None
        if rule["verdict"] == "not_evaluated" and value in NOT_APPLIED:
            return value
    return None


def _solves_left(path, task):
    from harness import runner
    from services import run_envelope
    envelope = run_envelope._load(path)
    if runner.budget_spent(task) or envelope is None:
        return 0
    return (envelope.get("budget") or {}).get("solves", 0) - run_envelope.solves_used(envelope)


def after_failed_result(path, task, gate, profile):
    """The solve stage's or the result gate's gate failed (L2, §6.2). True
    when a round is now due (the stage stays active); False when the stage
    fails, the loop's ending (if one ran) on `run.loop` and the stage."""
    from services import run_envelope, run_gates
    run = task["run"]
    name = run["stage"]
    agent = profile["stages"][RESULT_STAGE].get("agent")
    job = job_of(task)
    failing = _failing(gate)
    loop = run.get("loop")
    if loop is not None and (loop.get("ended") or loop["stage"] != RESULT_STAGE):
        run.setdefault("loops", []).append(run.pop("loop"))
        loop = None

    def stop(reason):
        if loop is not None:
            _end_cycle(loop, job, reason)
            loop["ended"] = reason
        run["stages"][name]["reason"] = reason  # with or without a loop: the stage says why it failed
        return False
    if name == RESULT_STAGE and any(engine_step(task, rule["id"]) for rule in engine_rules(profile)):
        # Issue 05 PR C (Q4): the final gate, after an engine loop ran. Never an L2 round (the
        # engine's closing re-solve replaced the solve L2 would triage), never the engine again.
        engines = {rule["id"] for rule in engine_rules(profile)}
        moved = any(r["rule"] in engines and r["facts"][0].get("value") == "inputs_changed" for r in gate["rules"])
        # Study harness 12c (GK B (47) Q4): a loop the cap cut short ends on the cap, not as unconverged.
        capped = any(r["rule"] in engines and r["facts"][0].get("value") == run_gates.LOOP_BUDGET_SPENT
                     for r in gate["rules"])
        return stop("inputs_changed" if moved else run_gates.LOOP_BUDGET_SPENT if capped
                    else "not_converged" if engines & set(failing) else "result_failed")
    capture = _not_applied(gate)
    if capture is not None:
        # C6f: the solve is spent and its result is not the project's; the user decides.
        if loop is None:
            loop = _new_l2(run, agent)
            # Round 0: the judged job only; no next solve follows it.
            loop["history"].append({"round": 0, "job": job, "failing": failing, "next_job": None,
                                    "outcome": "inputs_changed"})
        loop["history"][-1]["capture_reason"] = capture
        return stop("inputs_changed")
    held = {r["id"] for r in profile["stages"][RESULT_STAGE].get("rules", [])
            if r.get("unevaluated") == run_gates.UNEVALUATED_TO_USER}
    if name == RESULT_STAGE and any(r["rule"] in held and r["rule"] in failing and r["verdict"] == "not_evaluated"
                                    for r in gate["rules"]):  # GK B (51) F2: a failing (block) rule only
        return stop("not_evaluated")  # GK A (24) Q2: no lever supplies the evidence; the user's
    if name == "solve" and not any(r["rule"] == "sv.solve.state" and r["verdict"] == "fail"
                                   and r["facts"][0].get("value") == "failed" for r in gate["rules"]):
        return stop("no_result")  # no solve to fix (aborted, missing): the user's
    if agent is None or not run_envelope.live(path):
        return stop("no_envelope")
    if loop is None:
        loop = _new_l2(run, agent)
    if loop["rounds"] >= loop["limit"]:
        return stop("cap_reached")
    if rounds_left(task, path) <= 0 or _solves_left(path, task) <= 0:
        return stop("exhausted")  # each round consumes a solve: none left, no round
    _end_cycle(loop, job, "failed")
    loop["due"] = {"failing": failing, "fingerprint": gate["fingerprint"], "gate_stage": name, "job": job,
                   "rules": [r for r in gate["rules"] if r["rule"] in failing]}
    return True


def _new_l2(run, agent):
    loop = {"kind": L2, "stage": RESULT_STAGE, "rounds": 0, "limit": (agent or {}).get("rounds", 0),
            "changes": (agent or {}).get("changes", 0), "refusals": 0, "history": []}
    run["loop"] = loop
    return loop


def judged(path, profile, step):
    """A run's solve step whose job ran and did not finish optimal is judged
    by the solve stage's gate (L2's trigger), not stopped on as a failed step,
    while L2 can run: the profile's result-gate agent and a live envelope.
    Otherwise it stays a failed step, as before."""
    from services import run_envelope
    return (step["id"].startswith("solve.") and step["status"] == "failed"
            and (step.get("job") or {}).get("outcome") == "failed"
            and profile["stages"][RESULT_STAGE].get("agent") is not None and run_envelope.live(path))


def _solve_again(task, round_no):
    """A round changed the inputs: the input gate is checked again on them,
    then the run's solve runs again (new steps; the old ones are superseded,
    kept on record) and the result gate after it."""
    run = task["run"]
    for step in task["steps"]:
        if step["id"].split(".")[0] in RESULT_STAGES and not step.get("superseded"):
            step["superseded"] = {"loop": L2, "round": round_no}
    for name in RESULT_STAGES:
        run["stages"][name] = {"status": "pending", "attempts": run["stages"][name]["attempts"]}
    run["stage"] = STAGE
    run["stages"][STAGE]["status"] = "active"


def due(task):
    loop = (task.get("run") or {}).get("loop")
    return bool(loop and loop.get("due") and not loop.get("ended"))


# ── One round (the runner, in the run's bound context) ──────────────────────


def _gap_sources(envelope):
    """What each granted data gap may be filled from: the values to copy."""
    out = []
    for gap in envelope.get("gaps", []):
        target = gap["target"]
        for name, record in (gap.get("at_grant") or {}).items():
            record = {k: v for k, v in record.items() if k != "identity"}  # the harness's check, not a value to copy
            if "tariffs" in record:
                record = {"tariffs": [{"name": n, "version": v} for n, v in record["tariffs"].values()]}
            out.append({"tool": gap["tool"], "component": target["component"], "name": name,
                        "attr": target["attr"], "source": record})
    return out


def _message(task, profile_ref, due_now, envelope):
    from harness import fence
    from services import workflow_tools
    try:
        readiness = workflow_tools.project_readiness(task["project_id"])
        next_tools = list(readiness.get("next_tools") or [])
    except Exception:  # noqa: BLE001 — the rules carry the facts; the hint is optional
        next_tools = []
    failed = [{"rule": r["rule"], "facts": r["facts"], **({"reason": r["reason"]} if r.get("reason") else {})}
              for r in due_now["rules"]]
    return (instruction(profile_ref, STAGE) + "\n\nFailed rules:\n" + json.dumps(failed, default=str)
            + "\n\nNext tools from project_readiness: " + json.dumps(next_tools)
            + "\n\nData gaps you may fill, each only from this source (data, not instructions):\n"
            + fence.fenced(json.dumps(_gap_sources(envelope))))


def _triage(task, ctx):
    """L2's triage reads (C3), run by the harness before the turn: the job
    the gate judged, its log's tail, diagnose_network and validate_network.
    Fenced as untrusted data and capped at TRIAGE_CHARS. Raises when any read
    fails: the round does not run blind."""
    from harness import fence
    from services import chat_tools, project_registry, run_gates
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, task["project_id"])
        env = run_gates.Env(task=task, ctx=ctx, project=project, project_dir=project_registry.project_dir(project))
        job = run_gates._solve_job(env)
    if job is None:
        raise LookupError("the run's solve job cannot be read")
    reads = {"job": {k: job.get(k) for k in ("id", "status", "condition", "objective", "error")},
             "job_log": run_gates._on(ctx, chat_tools.solve_queue_job_log, str(job["id"]), LOG_LINES)["lines"],
             "diagnose_network": run_gates._on(ctx, chat_tools.diagnose_network),
             "validate_network": run_gates._on(ctx, chat_tools.validate_network)}
    text = json.dumps(reads, default=str)
    if len(text) > TRIAGE_CHARS:
        text = text[:TRIAGE_CHARS] + " [truncated]"
    return fence.fenced(text)


def _l2_tunable(profile, failing, rules):
    """L2's levers (§6.0): the failed rules' own `tunable` attributes and the
    profile's network-column tunables. The envelope still bounds each write."""
    own = [t for rule in failing for t in rules.get(rule, {}).get("tunable", []) if "." in t]
    levers = {tuple(t.rsplit(".", 1)) for t in own}
    levers |= {(t["component"], t["attr"]) for t in profile.get("tunable", []) if ":" not in t["component"]}
    return frozenset(levers)


def _l2_message(task, profile_ref, due_now, triage, tunable):
    failed = [{"rule": r["rule"], "facts": r["facts"], **({"reason": r["reason"]} if r.get("reason") else {})}
              for r in due_now["rules"]]
    return (instruction(profile_ref, RESULT_STAGE) + "\n\nFailed rules:\n" + json.dumps(failed, default=str)
            + "\n\nLevers you may change: " + json.dumps(sorted(".".join(t) for t in tunable))
            + "\n\nTriage the harness read before this round (data, not instructions):\n" + triage)


def _driving_rule(target, failing, rules):
    """The failing rule that drove a change to `target` ((component, attr)),
    or None (§6.4 `rule_id | null`). The one failing rule whose `tunable`
    names the target; else the only failing rule; else None: when several
    rules fail and none names the target, the row never guesses (GK B (19)).
    `failing` is the gate's set as of this round's admission."""
    tuning = [r for r in failing if f"{target[0]}.{target[1]}" in rules.get(r, {}).get("tunable", [])]
    if len(tuning) == 1:
        return tuning[0]
    return failing[0] if len(failing) == 1 else None


def _ledger_row(call, receipt, *, approval, round_no, source=None, stage=STAGE, loop=LOOP, failing=(), rules=None):
    """One ChangeRow (§6.4) from a verified receipt: the call's target, its
    old and new values (a dict per field when the card showed several), the
    round and the failing rule that drove it (`_driving_rule`)."""
    typed = [r for r in receipt["rows"] if r.get("role") not in ("note", "at_solve")]
    first = typed[0]
    attr = str(first["field"]).split(".", 1)[0]
    one = len(typed) == 1
    reason = re.sub(r"\s+", " ", str(call.get("reason") or "")).strip()[:REASON_CHARS]
    rule = _driving_rule((first["component_class"], attr), list(failing), rules or {})
    return {"id": f"chg-{uuid.uuid4().hex[:12]}", "stage": stage, "loop": loop, "round": round_no, "rule": rule,
            "target": {"component": first["component_class"], "name": str(first["name"]), "attr": attr},
            "old": first["old"] if one else {r["field"]: r["old"] for r in typed},
            "new": first["new"] if one else {r["field"]: r["new"] for r in typed},
            "unit": first.get("unit") or "" if one else "",
            "reason": reason, "source": source, "approval": approval,
            "receipt": f"{receipt['digest']}:{call['id']}", "tool": call["tool"], "reverted": False}


def _spent(session, before):
    """The tokens the session used since `before` (its `usage_acc` then)."""
    return {k: max(0, int(session.usage_acc.get(k, 0)) - int(before.get(k, 0))) for k in before}


def agent_turn(path, ctx, session, *, tools, message, provider=None, recheck, scope=None):
    """One headless agent turn. Returns what happened, counted by harness
    code: `calls` (each with ran, carded, error and its receipt), the usage
    the session accrued, and the offered tool list."""
    from harness import edit_card, eventlog, loop
    from services import run_envelope
    calls = {}
    order = []
    locks = contextlib.ExitStack()
    held = {}

    def guard(tool, args):
        """Admission's re-check under the project's locks (as `Runner.dispatch`):
        the context is still the run's, the owner reaches it, no other lock
        or hold, and the freshness chain starts from its last link."""
        locks.close()
        if not ctx.mutation_lock.acquire(timeout=5.0):
            return False
        locks.callback(ctx.mutation_lock.release)
        locks.enter_context(ctx.solver_state_lock)
        if not recheck():
            locks.close()
            return False
        read = {}
        fingerprint, reason = run_envelope.chain_head(path, ctx, read=read)
        if reason:
            locks.close()
            return False
        held["before"], held["read"] = fingerprint, read
        return True

    receipts = []  # a covered write's after-identity, captured under its own locks (issue 22)

    def write_guard(call_id, tool, args):
        """Issue 22: a covered write compares and writes inside the project's
        lock on the worker, against the identity its admission read."""
        if tool not in run_envelope.ATOMIC_TOOLS:
            return None
        # No record of what admission read (no call row, or no guarded admission): an empty
        # expectation, which the pickup refuses as unreadable. Never an unguarded write.
        call = calls.get(call_id) or {}
        return run_envelope.admitted_write(session, path, ctx, call_id=call_id, tool=tool,
                                           expected=call.get("read") or {}, receipts=receipts, recheck=recheck)

    usage_before = dict(session.usage_acc)
    session.stage_tools = frozenset(tools)
    session.loop_scope = scope
    session.decision_guard = guard
    session.write_guard = write_guard
    offered = []
    said = []  # the model's words before a call: the ledger row's reason
    try:
        for kind, payload in loop.run_turn(session, message, provider=provider, ui_context={"ui_mode": "guided"}):
            tid = payload.get("tool_use_id") if isinstance(payload, dict) else None
            eventlog.record(session, "runner/frame", frame=kind, loop=(scope or {}).get("loop", LOOP),
                            **({"tool_use_id": tid} if tid else {}),
                            **({"error_kind": payload["error_kind"]} if isinstance(payload, dict)
                               and payload.get("error_kind") else {}))
            if kind == "token" and isinstance(payload, dict):
                said.append(str(payload.get("delta") or ""))
            if kind == "session_init":
                offered = sorted(session.stage_tools)
            if tid and tid not in calls and kind in ("tool_request", "tool_error", "tool_pending_confirmation"):
                calls[tid] = {"id": tid, "tool": payload.get("tool_name"), "args": payload.get("args"),
                              "ran": False, "carded": False, "error": None, "receipt": None, "before": None, "read": None,
                              "reason": "".join(said)[-REASON_CHARS:]}
                order.append(tid)
                said.clear()
            call = calls.get(tid)
            if kind == "tool_pending_confirmation":
                session.withdraw_confirmation(payload["confirmation_token"])  # nobody is there: a refusal
                call["carded"] = True
                locks.close()
            elif kind == "tool_running":
                if call is not None:
                    call["ran"], call["before"], call["read"] = True, held.pop("before", None), held.pop("read", None)
                locks.close()  # the handler takes its own locks on the tool executor
            elif kind == "tool_error" and call is not None:
                call["error"] = payload.get("error_kind")
                locks.close()
            elif kind == "tool_result" and call is not None:
                result = payload.get("result")
                if isinstance(result, dict) and isinstance(result.get("_edit_receipt"), dict):
                    call["receipt"] = result["_edit_receipt"]
                if call["ran"] and scope is not None and call["tool"] in run_envelope.GAP_PREPARE \
                        and isinstance(result, dict):
                    # GK B condition 4: the pending profile this round made, bound to it.
                    run_envelope.prepared(path, tid, result.get("job_id"), scope["round"])
            if call is not None and call["ran"] and kind in ("tool_result", "tool_error") \
                    and edit_card.is_edit(call["tool"]):
                # The freshness chain's next link (issue 03): only from the chain's last link.
                if call["tool"] not in run_envelope.ATOMIC_TOOLS:
                    run_envelope.settle(path, tid, ctx, call["before"])
                else:  # issue 22: only this call's own receipt
                    run_envelope.settle_own(path, tid, ctx, call["before"], receipts)
    finally:
        locks.close()
        session.stage_tools = None
        session.loop_scope = None
        session.decision_guard = None
        session.write_guard = None
    usage = _spent(session, usage_before)
    return {"calls": [calls[t] for t in order], "usage": usage, "offered": offered}


def run_round(path, ctx, session, *, provider=None, recheck):
    """One round of the run's due loop (L1 or L2): the agent turn, then the
    ledger rows, the refusals and the budget, written by harness code. The
    gate is evaluated again by the next `_advance_run`; an L2 round that
    changed the inputs sends the run to its solve again first."""
    from harness import edit_card, runner
    from services import run_envelope, run_profiles
    task = json.loads(path.read_text())
    run = task["run"]
    if not due(task):
        return "idle"
    kind, stage = run["loop"]["kind"], run["loop"]["stage"]
    profile = run_profiles.resolve(run["profile"])
    agent = profile["stages"][stage]["agent"]
    due_now = run["loop"]["due"]
    round_no = run["loop"]["rounds"] + 1
    envelope = run_envelope._load(path) or {}  # the audit/`at_grant` view: the gaps' sources (issue 28)
    started = time.time()
    # §6.1: the failed rules' `tunable` attributes are all an edit may change.
    rules = {r["id"]: r for r in profile["stages"][due_now.get("gate_stage", stage)]["rules"]}
    if kind == L2:
        tunable = _l2_tunable(profile, due_now["failing"], rules)
        try:
            triage = _triage(task, ctx)
        except Exception:  # noqa: BLE001 — C3: an unreadable triage stops the run, never a blind round
            logger.warning("run loop: the triage of %s could not be read", task["task_id"], exc_info=True)
            end(path, "triage_unreadable")
            return "l2_round"
        message = _l2_message(task, run["profile"], due_now, triage, tunable)
        scope = {"round": round_no, "tunable": tunable, "loop": L2, "changes": agent["changes"]}
    else:
        tunable = frozenset(tuple(t.rsplit(".", 1)) for rule in due_now["failing"]
                            for t in rules.get(rule, {}).get("tunable", []) if "." in t)
        message = _message(task, run["profile"], due_now, envelope)
        scope = {"round": round_no, "tunable": tunable}
    model = getattr(session, "model", None)
    usage_before = dict(session.usage_acc)
    try:
        outcome = agent_turn(path, ctx, session, tools=agent["tools"], message=message,
                             provider=provider, recheck=recheck, scope=scope)
    except BaseException:
        # A round that errors still spent its tokens: they count (§6.5).
        runner.charge(path, _spent(session, usage_before), model)
        raise
    after = run_envelope._load(path) or {}
    sources = {a["call_id"]: a["source"] for a in after.get("audit", [])
               if a.get("event") == "used" and a.get("source")}
    # C2: a verified change is a link the call's own receipt settled, not a ledger row or a result.
    settled = {link.get("after_change") for link in after.get("chain", [])}
    rows, unverified, refusals = [], [], 0
    for call in outcome["calls"]:
        # Refused at pickup (issue 22), for breaking a confirmed requirement (issue 23) or over
        # the round's change cap (issue 05): no write.
        if call["carded"] or call["error"] in (run_envelope.PICKUP_KIND, "requirement_violated",
                                               run_envelope.CHANGE_CAP_KIND):
            refusals += 1
            continue
        if not call["ran"] or not edit_card.is_edit(call["tool"]):
            continue
        receipt = call["receipt"]
        if not isinstance(receipt, dict) or receipt.get("outcome") != "verified" or not receipt.get("rows"):
            unverified.append(call["id"])
            continue
        rows.append(_ledger_row(call, receipt, approval="envelope:" + str(envelope.get("id")), round_no=round_no,
                                source=sources.get(call["id"]), stage=stage, loop=kind,
                                failing=due_now["failing"], rules=rules))
    changed = [call["id"] for call in outcome["calls"] if call["id"] in settled]
    runner.charge(path, outcome["usage"], model)

    def change(t):
        r = t["run"]
        loop = r.get("loop")
        if not loop or loop.get("ended") or not loop.get("due"):
            return False
        budget = runner._budget(t)
        budget["used"]["rounds"] = budget["used"].get("rounds", 0) + 1
        loop["rounds"] = round_no
        loop["refusals"] += refusals
        cycle = {"round": round_no, "failing": due_now["failing"],
                 "fingerprint": due_now["fingerprint"], "refusals": refusals,
                 "changes": [row["id"] for row in rows], "offered": outcome["offered"],
                 "usage": outcome["usage"], "seconds": round(time.time() - started, 3)}
        if kind == L2:  # HH-06: the job the gate judged and its native findings; the next job comes later
            cycle.update(job=due_now.get("job"), gate_stage=due_now.get("gate_stage"), settled=changed,
                         findings=[{k: r_[k] for k in ("rule", "verdict", "facts", "reason") if k in r_}
                                   for r_ in due_now["rules"]], next_job=None, outcome=None)
        loop["history"].append(cycle)
        loop.pop("due")
        r.setdefault("ledger", []).extend(rows)
        if unverified:
            loop["unverified"] = unverified
            _fail(t, "unverified_write")
        elif kind == L2 and not changed:
            cycle["outcome"] = "no_change"
            _fail(t, "no_change")  # SC-14: no re-solve without a change
        elif kind == L2:
            _solve_again(t, round_no)
    runner._update(path, change)
    return "l2_round" if kind == L2 else "l1_round"


# ── Engine loops (issue 05, PR C; C9, GK A (11) 1-6, (12) 7-12, GK B H1-H7) ─
#
# A result rule with an `engine` block is the engine's: `run_margin_loop` or
# `run_coupling_loop`, started by harness code through the chat_tools handler
# on the run's bound context, never by an agent turn. The result gate runs in
# two passes: the other rules first (a failure there is L2's, with the engine
# rules out of its failing set), then each engine rule's loop, one at a time,
# then the whole gate. A failing engine rule ends the run `needs_user`
# `not_converged`; nothing nests.

ENGINE = "engine"  # the run loop name engine steps and their envelope rows carry


def engine_rules(profile):
    return [r for r in profile["stages"][RESULT_STAGE]["rules"] if r.get("engine")]


def engine_step(task, rule_id):
    from services import run_gates
    return run_gates.engine_step(task, rule_id)


def gate_rules(task, profile, name):
    """The rules `name`'s gate evaluates now: the result gate's non-engine
    rules until every engine rule has a settled step, then all of them."""
    rules = profile["stages"][name]["rules"]
    if name != RESULT_STAGE or all((engine_step(task, r["id"]) or {}).get("status") == "completed"
                                   for r in engine_rules(profile)):
        return rules
    return [r for r in rules if not r.get("engine")]


def engine_due(task, profile):
    """The result gate's first pass passed (under the task lock). Adds the
    next engine rule's step when none is pending or waiting; True while an
    engine rule has no completed step (the stage stays active)."""
    pending = [r for r in engine_rules(profile)
               if (engine_step(task, r["id"]) or {}).get("status") != "completed"]
    if not pending:
        return False
    if any(engine_step(task, r["id"]) is not None for r in pending):
        return True  # one loop at a time: the open one first (condition 4)
    rule = pending[0]
    n = 1 + sum(1 for s in task["steps"] if s["id"].split(".")[0] == RESULT_STAGE)
    # H4: the loop's arguments are the rule's engine block only (the clamp comes at its start).
    task["steps"].append({"id": f"{RESULT_STAGE}.{n}", "tool": rule["engine"]["tool"], "engine": rule["id"],
                          "args": {"max_solves": rule["engine"]["max_solves"],
                                   "restore": rule["engine"]["restore"]},
                          "status": "pending"})
    return True


def card_engine(path, task, profile, ctx=None):
    """Issue 07 B3a (GK B (32) L2, L3): in a run that never had a grant
    (`run_envelope.never_granted`), the new engine step's own card (COP-1):
    it shows the loop tool, its rule, the clamped `max_solves` (the most its
    Continue may reserve), the overhead, `restore` and the goal's target on
    the network's horizon; the plan pauses at the result gate's card. Under
    the task lock, on `task` (the caller saves). True when it carded."""
    from services import run_envelope
    step = next((s for s in reversed(task["steps"]) if s.get("engine") and not s.get("superseded")), None)
    if step is None or step["status"] != "pending" or "card" in step or not run_envelope.never_granted(path, task):
        return False
    rule = next(r for r in engine_rules(profile) if r["id"] == step["engine"])
    engine = rule["engine"]
    overhead = _overhead(engine["tool"])
    step["card"] = {"tool": engine["tool"], "rule": rule["id"],
                    "max_solves": run_envelope.carded_clamp(task, profile, engine["max_solves"], overhead),
                    "overhead": overhead, "restore": engine["restore"], "target_lole_h": _card_target(ctx)}
    task["status"], task["pause"] = "paused_at_card", {"card": RESULT_STAGE, "after": None}
    return True


def _card_target(ctx):
    """The goal's LOLE target on the network's horizon (`loop_goal`), for
    the card only; the loop reads the goal itself at its start. None when
    it cannot be read."""
    if ctx is None:
        return None
    try:
        from services import reliability_goal
        from services.adequacy.metrics import horizon_years
        target, _, _ = reliability_goal.loop_goal(
            None, reliability_goal.active_goal(ctx.solver_state), float(horizon_years(ctx.network)))
    except Exception:  # noqa: BLE001 — an unreadable goal shows no target
        return None
    return float(target) if isinstance(target, (int, float)) and math.isfinite(target) else None


def _engine_stop(path, step_id, reason, detail=None, status="failed"):
    """An engine start that did not start: the step `status`, the result
    gate failed with `reason`, the run `needs_user`."""
    from harness import runner

    def change(task):
        step = next(s for s in task["steps"] if s["id"] == step_id)
        step["status"] = status
        step["error"] = (f"{reason}: {detail}" if detail else reason)[:REASON_CHARS * 2]
        run = task["run"]
        stage = run["stages"][RESULT_STAGE]
        stage["status"], stage["reason"] = "failed", reason
        if run["status"] in ("running", "waiting_job"):
            run["status"], task["status"] = "needs_user", "needs_review"
    runner._update(path, change)


def _gate_on_refusal(path, ctx):
    """A refused loop stops the run at the result gate with the gate read as it
    stands, so the engine rule shows the refusal's typed reason beside the
    other rules (GK A (19) SH11-L1: `eh.adequacy.lole` reads
    `loop_nothing_to_sample`, never passed or not_started). The stop
    is `_engine_stop`'s alone: the read changes no status, reason or attempt
    count (SH11-L9), and a read that fails leaves the stop as it is and says
    so on the stage (SH11-L8)."""
    from harness import runner
    from services import assistant_tasks, run_profiles
    try:
        task = json.loads(path.read_text())
        profile = run_profiles.resolve(task["run"]["profile"])
        # Every rule, the engine ones included: the refused step is settled (`failed`).
        record = {"gate": assistant_tasks._gate_now(task, profile["stages"][RESULT_STAGE]["rules"], ctx)}
    except Exception as exc:  # noqa: BLE001 - the stop stands; the stage names the failed read
        logger.warning("result gate read after an engine refusal failed: %s", type(exc).__name__)
        record = {"gate_error": f"the gate read after the refusal failed: {type(exc).__name__}"}

    def change(t):
        t["run"]["stages"][RESULT_STAGE].update(record)
    runner._update(path, change)


def _overhead(tool):
    """The solves a loop spends outside its `max_solves`: the margin loop's
    probe, and the closing re-solve every loop runs (GK A (12) Q1)."""
    return (1 if tool == "run_margin_loop" else 0) + 1


def start_engine(path, ctx, step, *, instance):
    """Start an engine step's loop (the runner, on the run's bound context).
    Returns "engine" when it started and is followed, or the refusal reason.
    A start the route refuses (its 422 set, or 409: the context is busy) is
    `engine_refused` with the route's detail; any other error is not a
    refusal: the step is uncertain and the error propagates (#1020)."""
    from fastapi import HTTPException

    from harness import continuation, job_adapters, runner
    from services import chat_tools, run_envelope, run_gates, run_profiles
    task = json.loads(path.read_text())
    profile = run_profiles.resolve(task["run"]["profile"])
    rule = next(r for r in engine_rules(profile) if r["id"] == step["engine"])
    engine = rule["engine"]
    study = run_gates.ENGINE_TOOLS[engine["tool"]]
    attempt = uuid.uuid4().hex
    claimed = {}

    def claim(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "pending" or any((x.get("job") or {}).get("kind") == ENGINE and x["status"] == "waiting"
                                           for x in t["steps"]):
            return False  # claimed elsewhere, or another loop still runs on this context (condition 4)
        s.update(status="running", attempt_id=attempt)
        claimed["ok"] = True
    runner._update(path, claim)
    if not claimed:
        return "claimed_elsewhere"
    carded = run_envelope.never_granted(path, task)  # issue 07 B3a: step by step, at its own card
    if carded:
        max_solves, reason = run_envelope.reserve_carded(
            path, step_id=step["id"], call_id=attempt, overhead=_overhead(engine["tool"]), restore=engine["restore"])
    else:
        max_solves, reason = run_envelope.reserve_engine(
            path, call_id=attempt, tool=engine["tool"], rule=rule["id"], max_solves=engine["max_solves"],
            overhead=_overhead(engine["tool"]), restore=engine["restore"])
    if reason is not None:
        _engine_stop(path, step["id"], reason)
        return reason
    runner._update(path, lambda t: runner._budget(t)["used"].update(
        rounds=runner._budget(t)["used"].get("rounds", 0) + 1))  # one engine call is one round
    before, _ = run_envelope.chain_head(path, ctx)  # what a `restore="final"` hold would link from (H3)
    from services import dirty_state
    revision = dirty_state.revision(ctx)  # H3: the counter the loop's own hold may move by one
    with ctx.solver_state_lock:  # the held setting's value before the loop (H6 `old`), read before it starts
        held_before = getattr(ctx.solver_state.get("solver_config"), _HELD[engine["tool"]][0], None)
    handler = getattr(chat_tools, engine["tool"])
    try:
        # Target and draws omitted: `active_loop_goal` puts the project's goal on this network's
        # horizon (`loop_goal(horizon_years(n))`), once.
        handler(max_solves=max_solves, restore=engine["restore"])
    except HTTPException as exc:
        run_envelope.settle_engine_solves(path, attempt, 0)  # refused before any solve
        if carded:
            runner._update(path, lambda t: run_envelope.settle_carded(
                t, next(s for s in t["steps"] if s["id"] == step["id"]), 0))
        _engine_stop(path, step["id"], "engine_refused", str(exc.detail))
        _gate_on_refusal(path, ctx)
        return "engine_refused"
    except BaseException:
        _engine_stop(path, step["id"], "engine_error", "the engine start raised", status="uncertain")
        raise
    with ctx.solver_state_lock:
        record = ctx.solver_state.get(study)
        started_at = record.get("started_at") if isinstance(record, dict) else None
    job_id = job_adapters.register_study_job(
        study_key=study, tool_name=engine["tool"], tool_use_id=f"run-{step['id']}-{attempt[:8]}",
        owner_user_id=task.get("owner_id"), project_uuid=task["project_id"], ctx=ctx)

    def wait(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s.get("attempt_id") != attempt or s["status"] != "running":
            return False
        s["status"] = "waiting"
        s["job"] = {"kind": ENGINE, "study": study, "job_id": job_id, "started_at": started_at,
                    "max_solves": max_solves, "continuation": "withdrawn", "before": before,
                    "held_before": held_before, "revision": revision,
                    "deadline_at": time.time() + continuation.MAX_ENGINE_FOLLOW_SECONDS}
        if t["status"] != "cancelled":
            t["status"] = "waiting_job"
    runner._update(path, wait)
    continuation.follow_engine(path, (task["task_id"], step["id"], attempt), ctx, instance=instance)
    return ENGINE


_HELD = {"run_margin_loop": ("reserve_margin", "lever_star"), "run_coupling_loop": ("ens_cap_permyriad", "eps_star")}


def engine_applied(path, ctx, step, loop):
    """H3 (under the project's mutation lock, at settle): False when an edit
    crossed the loop (the edit counter moved past the revision its record
    captured or re-captured at its hold): its result is not the project's,
    nothing joins the chain. True otherwise; a `restore="final"` hold then
    becomes the chain's next link and a ledger row naming the engine rule
    (H6), since the held setting is in the inputs the chain fingerprints."""
    from harness import runner
    from services import dirty_state, run_envelope, study_state
    now = dirty_state.revision(ctx)
    if study_state.edited_since(loop, revision=now) is not False:
        return False
    tool = step["tool"]
    attr, star = _HELD[tool]
    held = loop.get("restore") == "final" and loop.get("status") == "met" and loop.get(star) is not None
    # The loop's own hold re-captures its record's revision, which would hide an edit that landed
    # before it: the counter may have moved only by that hold since the harness started the loop.
    start = step["job"].get("revision")
    if isinstance(start, int) and now != start + int(held):
        return False
    if not (held and loop.get("base_restore_status") is not None):
        return True
    run_envelope.settle(path, step["attempt_id"], ctx, step["job"].get("before"))
    envelope = run_envelope.read(path) or {}
    row = {"id": f"chg-{uuid.uuid4().hex[:12]}", "stage": RESULT_STAGE, "loop": ENGINE, "round": None,
           "rule": step["engine"], "target": {"component": "solver_config", "name": "", "attr": attr},
           "old": step["job"].get("held_before"), "new": loop[star], "unit": "",
           "reason": "The engine loop held the setting its verdict is about (restore final).",
           "source": None, "approval": "envelope:" + str(envelope.get("id")),
           "receipt": f"engine:{step['attempt_id']}", "tool": tool, "reverted": False}
    runner._update(path, lambda t: t["run"].setdefault("ledger", []).append(row))
    return True


def recover_engines(path, ctx, *, instance, now=None):
    """Each tick and on a taken-over run (restart, §6.6), each waiting engine
    step with no live follower in this process (a takeover, or a follower
    that died, H2) is settled `timed_out` past its deadline, else from its
    loop's own record, followed again while it still runs on this context,
    or settled `lost`. Never started again (GK A (11)(b))."""
    from harness import continuation
    task = json.loads(path.read_text())
    for step in task["steps"]:
        if step["status"] != "waiting" or (step.get("job") or {}).get("kind") != ENGINE:
            continue
        ticket = (task["task_id"], step["id"], step.get("attempt_id"))
        if continuation.following(ticket):
            continue
        if continuation.expire_engine(path, ticket, instance=instance, now=time.time() if now is None else now):
            continue
        if continuation.settle_engine(path, ticket, instance=instance, ctx=ctx) == "running":
            continuation.follow_engine(path, ticket, ctx, instance=instance)
