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
"""
from __future__ import annotations

import contextlib
import json
import re
import time
import uuid
from importlib import resources
from pathlib import Path

LOOP = "L1"
STAGE = "input_gate"
REASON_CHARS = 240


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
    loop["ended"] = reason
    run["stages"][STAGE]["status"] = "failed"
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
    run.setdefault("requests", []).append(req)
    run["waiting"] = {"kind": "data", "ref": req["id"], "items": req["items"], "notice": req["notice"],
                      "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return req


def after_passed_gate(task):
    loop = task["run"].get("loop")
    if loop is not None and not loop.get("ended") and loop["rounds"]:
        loop["ended"] = "passed"


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
            + "\n\nData gaps you may fill, each only from this source:\n" + json.dumps(_gap_sources(envelope)))


def _ledger_row(call, receipt, *, approval, round_no, source=None):
    """One ChangeRow (§6.4) from a verified receipt: the call's target, its
    old and new values (a dict per field when the card showed several)."""
    typed = [r for r in receipt["rows"] if r.get("role") not in ("note", "at_solve")]
    first = typed[0]
    attr = str(first["field"]).split(".", 1)[0]
    one = len(typed) == 1
    reason = re.sub(r"\s+", " ", str(call.get("reason") or "")).strip()[:REASON_CHARS]
    return {"id": f"chg-{uuid.uuid4().hex[:12]}", "stage": STAGE, "loop": LOOP, "round": round_no, "rule": None,
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
            eventlog.record(session, "runner/frame", frame=kind, loop=LOOP,
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
    """One L1 round on the run whose round is due: the agent turn, then the
    ledger rows, the refusals and the budget, written by harness code. The
    gate is evaluated again by the next `_advance_run`."""
    from harness import edit_card, runner
    from services import run_envelope, run_profiles
    task = json.loads(path.read_text())
    run = task["run"]
    if not due(task):
        return "idle"
    profile = run_profiles.resolve(run["profile"])
    agent = profile["stages"][STAGE]["agent"]
    due_now = run["loop"]["due"]
    round_no = run["loop"]["rounds"] + 1
    envelope = run_envelope.read(path) or {}
    started = time.time()
    # §6.1: the failed rules' `tunable` attributes are all an edit may change.
    rules = {r["id"]: r for r in profile["stages"][STAGE]["rules"]}
    tunable = frozenset(tuple(t.rsplit(".", 1)) for rule in due_now["failing"]
                        for t in rules.get(rule, {}).get("tunable", []) if "." in t)
    model = getattr(session, "model", None)
    usage_before = dict(session.usage_acc)
    try:
        outcome = agent_turn(path, ctx, session, tools=agent["tools"],
                             message=_message(task, run["profile"], due_now, envelope),
                             provider=provider, recheck=recheck, scope={"round": round_no, "tunable": tunable})
    except BaseException:
        # A round that errors still spent its tokens: they count (§6.5).
        runner.charge(path, _spent(session, usage_before), model)
        raise
    sources = {a["call_id"]: a["source"] for a in (run_envelope.read(path) or {}).get("audit", [])
               if a.get("event") == "used" and a.get("source")}
    rows, unverified, refusals = [], [], 0
    for call in outcome["calls"]:
        # Refused at pickup (issue 22) or for breaking a confirmed requirement (issue 23): no write.
        if call["carded"] or call["error"] in (run_envelope.PICKUP_KIND, "requirement_violated"):
            refusals += 1
            continue
        if not call["ran"] or not edit_card.is_edit(call["tool"]):
            continue
        receipt = call["receipt"]
        if not isinstance(receipt, dict) or receipt.get("outcome") != "verified" or not receipt.get("rows"):
            unverified.append(call["id"])
            continue
        rows.append(_ledger_row(call, receipt, approval="envelope:" + str(envelope.get("id")), round_no=round_no,
                                source=sources.get(call["id"])))
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
        loop["history"].append({"round": round_no, "failing": due_now["failing"],
                                "fingerprint": due_now["fingerprint"], "refusals": refusals,
                                "changes": [row["id"] for row in rows], "offered": outcome["offered"],
                                "usage": outcome["usage"], "seconds": round(time.time() - started, 3)})
        loop.pop("due")
        r.setdefault("ledger", []).extend(rows)
        if unverified:
            loop["unverified"] = unverified
            _fail(t, "unverified_write")
    runner._update(path, change)
    return "l1_round"
