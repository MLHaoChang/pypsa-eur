"""The approval envelope: approve once, run unattended inside it (study
harness issue 03, design §7; owner decisions SH-ENVELOPE-1, COP-1,
SH-RESEARCH-1).

`<task_id>.envelope.json` sits beside a version-3 run's task file:

* **Proposal.** `_start_run` writes the profile's `envelope` block as a
  `proposed` envelope; `describe` is the plan-review card's detail.
* **Grant.** Only the user's verdict on that card, sent as the `/stream`
  request's `envelope_decision` (never a tool call), reaches `decide`.
  "Approve and run unattended" grants it, narrowed as the user chose and never
  wider than the profile, recorded as `approvals.decided(by="user")` with the
  envelope id. "Approve, step by step" grants nothing (COP-1).
* **Use.** `confirm._confirm_destructive_tool` asks `admit` before a card, on
  the runner's session `run:<task_id>` only. A matching call is approved as
  `approvals.decided(by="envelope:<id>")` plus an audit `used` row (the
  `policy` path, fail closed); anything else gets a `refused` row with its
  reason and takes the card path. Nothing is cached from the grant: every
  dispatch is checked again.
* **End.** `expires_at`, an exhausted budget, `cancel_task` or a revoke from
  the plan card. The runner pauses and notifies; nothing re-grants itself.

Freshness chain (GK B, B2): the project fingerprint at grant, then the one
after each runner change (`settle`). Any other drift, or a fingerprint that
cannot be read, matches nothing and pauses the run.
"""
from __future__ import annotations

import copy
import fnmatch
import json
import logging
import math
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)

VERSION = 1
STATES = ("proposed", "granted", "expired", "revoked", "exhausted")
SESSION_PREFIX = "run:"  # harness/runner.py's session for a run

# §6.5: the code caps what a profile or the user may set.
CAPS = {"solves": 20, "rounds": 20, "wall_time_h": 72.0}
RESEARCH_DEFAULT_EUR, RESEARCH_CAP_EUR = 2.0, 10.0  # SH-RESEARCH-CAP-1

# The run tools an envelope may list (`runs`); each admitted one counts as a solve.
RUN_TOOLS = frozenset({"run_simulation", "run_ac_pf_stage", "solve_queue_enqueue",
                       "run_sensitivity_sweep", "run_eh_study"})
# Always its own card, even when listed: others rely on the publication (§7).
NEVER_ADMITTED = frozenset({"campus_publish_grid_code"})
# A researched value is never written without the user's card (SH-RESEARCH-1).
# The research step (issue 09) marks what it brings in with these keys or a
# `research:` source; a write that carries one never matches.
RESEARCH_KEYS = frozenset({"research", "research_id", "research_ref", "research_value"})
RESEARCH_SOURCE = "research:"

# Refusals that end the envelope's use for this run: the runner pauses and
# notifies (§7 "Scope and audit", the freshness chain).
PAUSING = frozenset({"expired", "revoked", "exhausted", "drift", "fingerprint_unreadable", "brief_stale"})

_GLOB = re.compile(r"[*?\[\]]")
_CLASS = re.compile(r"^[A-Z][A-Za-z]{1,40}$")


class EnvelopeError(ValueError):
    """An envelope (or a proposal block) a run may not carry."""


def _refuse(kind, message, status=409):
    raise HTTPException(status, {"error_kind": kind, "message": message})


def _invalid(message):
    _refuse("invalid_envelope", message, 422)


def _now():
    return time.time()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


def _epoch(text):
    return datetime.fromisoformat(text).timestamp()


def _fingerprint(ctx):
    """The run project's input fingerprint (solver outputs excluded), or
    None when it cannot be read: that counts as drift (fail closed)."""
    from services import workflow_tools
    try:
        return workflow_tools._network_input_fingerprint(ctx) or None
    except Exception:  # noqa: BLE001
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ── Validation ───────────────────────────────────────────────────────────────


def _check_edit(entry):
    from harness import confirm, edit_card
    from services import run_profiles
    if not isinstance(entry, dict) or not {"tool", "target"} <= set(entry) <= {"tool", "target", "bounds", "enum"}:
        raise EnvelopeError(f"An edit names tool, target and bounds or enum: {entry!r}")
    tool, target = entry["tool"], entry["target"]
    if not isinstance(tool, str) or tool in NEVER_ADMITTED or not edit_card.is_edit(tool) \
            or confirm._safety_tier_for(tool) != "write":
        raise EnvelopeError(f"{tool!r} is not a write tool an envelope can list.")
    if not isinstance(target, dict) or set(target) != {"component", "name", "attr"} \
            or not all(isinstance(v, str) and v for v in target.values()):
        raise EnvelopeError("A target names component, name and attr.")
    component, name, attr = target["component"], target["name"], target["attr"]
    if not _CLASS.match(component) or len(name) > 64:
        raise EnvelopeError(f"{component!r} is not one component class; a glob matches names in one class only.")
    if attr in run_profiles.NEVER_TUNABLE or "grid_code" in attr or attr == "name" \
            or component.lower() in run_profiles._NEVER_COMPONENTS:
        raise EnvelopeError(f"{component}.{attr} is never tunable: the agent changes design levers, never the question.")
    if ("bounds" in entry) == ("enum" in entry):
        raise EnvelopeError(f"{component}.{attr}: an edit carries numeric bounds or an enum of allowed values.")
    if "bounds" in entry:
        bounds = entry["bounds"]
        if not isinstance(bounds, dict) or not bounds or not set(bounds) <= {"min", "max", "rel"} \
                or not all(_number(v) for v in bounds.values()):
            raise EnvelopeError(f"{component}.{attr}: bounds are numeric min, max and/or rel.")
        if "rel" in bounds and not 0 < bounds["rel"] <= 1:
            raise EnvelopeError(f"{component}.{attr}: rel is a fraction of the value at grant, in (0, 1].")
        if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
            raise EnvelopeError(f"{component}.{attr}: min is above max.")
    else:
        enum = entry["enum"]
        if not isinstance(enum, list) or not 0 < len(enum) <= 20 \
                or not all(isinstance(v, str) or _number(v) for v in enum):
            raise EnvelopeError(f"{component}.{attr}: an enum lists 1-20 allowed values.")
    return {"tool": tool, "target": dict(target), **{k: copy.deepcopy(entry[k]) for k in ("bounds", "enum") if k in entry}}


def _check_run(entry):
    if not isinstance(entry, dict) or "tool" not in entry or not set(entry) <= {"tool", "kind"} \
            or entry["tool"] not in RUN_TOOLS or not isinstance(entry.get("kind", ""), str):
        raise EnvelopeError(f"A run names one of {sorted(RUN_TOOLS)} and an optional kind: {entry!r}")
    return dict(entry)


def _check_budget(budget):
    if not isinstance(budget, dict) or set(budget) != set(CAPS):
        raise EnvelopeError(f"A budget sets {sorted(CAPS)}.")
    for key, cap in CAPS.items():
        value = budget[key]
        if not _number(value) or not 0 < value <= cap or (key != "wall_time_h" and value != int(value)):
            raise EnvelopeError(f"budget.{key} is above its hard cap {cap} or not a positive count.")
    return {"solves": int(budget["solves"]), "rounds": int(budget["rounds"]), "wall_time_h": float(budget["wall_time_h"])}


def _check_research(research):
    if not isinstance(research, dict) or set(research) != {"allowed", "cost_cap_eur"} \
            or not isinstance(research["allowed"], bool) or not _number(research["cost_cap_eur"]) \
            or not 0 <= research["cost_cap_eur"] <= RESEARCH_CAP_EUR:
        raise EnvelopeError(f"research is allowed (bool) and cost_cap_eur, at most EUR {RESEARCH_CAP_EUR:g}.")
    return {"allowed": research["allowed"], "cost_cap_eur": float(research["cost_cap_eur"])}


def check_block(block):
    """A profile's `envelope` block, validated (`run_profiles.load`)."""
    if not isinstance(block, dict) or not {"edits", "runs", "budget"} <= set(block) <= {"edits", "runs", "budget", "research"}:
        raise EnvelopeError("An envelope block has edits, runs, budget and optional research.")
    if not isinstance(block["edits"], list) or not isinstance(block["runs"], list):
        raise EnvelopeError("edits and runs are lists.")
    return {"edits": [_check_edit(e) for e in block["edits"]], "runs": [_check_run(r) for r in block["runs"]],
            "budget": _check_budget(block["budget"]),
            "research": _check_research(block.get("research", {"allowed": False, "cost_cap_eur": RESEARCH_DEFAULT_EUR}))}


def _key(entry):
    return entry["tool"], entry["target"]["component"], entry["target"]["attr"]


def _within(edit, wide):
    """`edit` names no instance, value or bound `wide` does not allow."""
    if _key(edit) != _key(wide):
        return False
    name, glob = edit["target"]["name"], wide["target"]["name"]
    if name != glob and (_GLOB.search(name) or not fnmatch.fnmatchcase(name, glob)):
        return False
    if "enum" in wide:
        return "enum" in edit and all(v in wide["enum"] for v in edit["enum"])
    if "bounds" not in edit:
        return False
    mine, theirs = edit["bounds"], wide["bounds"]
    return all(k in mine for k in theirs) and mine.get("min", -math.inf) >= theirs.get("min", -math.inf) \
        and mine.get("max", math.inf) <= theirs.get("max", math.inf) and mine.get("rel", 0) <= theirs.get("rel", math.inf)


def narrow(block, candidate):
    """The user's (or a model's) envelope, checked to be no wider than the
    profile's block: fewer edits or runs, tighter bounds, a lower budget.
    Research spend may be raised up to its hard cap (SH-RESEARCH-CAP-1)."""
    try:
        mine = check_block(candidate)
    except EnvelopeError as exc:
        _invalid(str(exc)[:300])
    for edit in mine["edits"]:
        if not any(_within(edit, wide) for wide in block["edits"]):
            _invalid(f"{edit['tool']} on {edit['target']} is wider than the profile's envelope.")
    for run in mine["runs"]:
        if run not in block["runs"]:
            _invalid(f"{run} is not in the profile's envelope.")
    for key, value in mine["budget"].items():
        if value > block["budget"][key]:
            _invalid(f"budget.{key} {value:g} is above the profile's {block['budget'][key]:g}.")
    if mine["research"]["allowed"] and not block["research"]["allowed"]:
        _invalid("This profile allows no research step.")
    return mine


# ── The file ─────────────────────────────────────────────────────────────────


def path_for(task_path):
    task_path = Path(task_path)
    return task_path.with_name(task_path.name.removesuffix(".json") + ".envelope.json")


_FIELDS = frozenset({"id", "task_id", "project_id", "owner_id", "version", "granted_at", "expires_at",
                     "fingerprint", "chain", "profile", "edits", "runs", "budget", "research", "notify",
                     "state", "audit"})


def _load(task_path):
    """The envelope, or None. A file that is not a whole version-1 envelope
    is none at all: nothing is admitted from it (fail closed)."""
    try:
        envelope = json.loads(path_for(task_path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(envelope, dict) or not _FIELDS <= set(envelope) or envelope["version"] != VERSION \
            or envelope["state"] not in STATES or (envelope["state"] != "proposed" and not envelope["expires_at"]):
        return None
    return envelope


def _save(task_path, envelope):
    from services import assistant_tasks
    assistant_tasks._save(path_for(task_path), envelope)


def _audit(envelope, event, **fields):
    envelope["audit"].append({"at": _iso(_now()), "event": event, **{k: v for k, v in fields.items() if v is not None}})


def propose(task_path, task, profile):
    """The profile's envelope block as a `proposed` envelope (intake)."""
    block = profile.get("envelope")
    if block is None:
        return None
    envelope = {"id": str(uuid.uuid4()), "task_id": task["task_id"], "project_id": task["project_id"],
                "owner_id": task["owner_id"], "version": VERSION, "granted_at": None, "expires_at": None,
                "fingerprint": None, "chain": [], "profile": task["run"]["profile"],
                "edits": [dict(e, at_grant={}) for e in copy.deepcopy(block["edits"])],
                "runs": copy.deepcopy(block["runs"]), "budget": dict(block["budget"]),
                "research": dict(block["research"]), "notify": {"email": False},
                "state": "proposed", "audit": []}
    _save(task_path, envelope)
    return envelope


def _rows(envelope):
    lines = []
    for e in envelope["edits"]:
        t = e["target"]
        allowed = ", ".join(map(str, e["enum"])) if "enum" in e else ", ".join(
            f"{k} {v:g}" if k != "rel" else f"±{v:.0%} of the value at grant" for k, v in e["bounds"].items())
        lines.append(f"- Edit {t['component']} `{t['name']}` {t['attr']} ({allowed}) with {e['tool']}")
    lines += [f"- Run {r['tool']}" + (f" ({r['kind']})" if r.get("kind") else "") for r in envelope["runs"]]
    b = envelope["budget"]
    lines.append(f"- Up to {b['solves']} solves and {b['rounds']} rounds, for at most {b['wall_time_h']:g} h")
    r = envelope["research"]
    lines.append(f"- Research: up to EUR {r['cost_cap_eur']:g}" if r["allowed"] else "- No research")
    lines.append("- Never: deletes, grid-code publish, external tools, researched values, anything else (a card each)")
    return "\n".join(lines)


def describe(envelope):
    """What the plan-review card shows (issue 08 renders it)."""
    if envelope is None:
        return None
    audit = envelope["audit"]
    return {"id": envelope["id"], "state": envelope["state"], "expires_at": envelope["expires_at"],
            "edits": [{k: v for k, v in e.items() if k != "at_grant"} for e in envelope["edits"]],
            "runs": envelope["runs"], "budget": envelope["budget"], "research": envelope["research"],
            "notify": envelope["notify"],
            "used": sum(1 for a in audit if a["event"] == "used"),
            "refused": sum(1 for a in audit if a["event"] == "refused"),
            "detail": _rows(envelope)}


def read(task_path):
    return describe(_load(task_path))


# ── Grant, revoke, end ───────────────────────────────────────────────────────


def _instances(ctx, edit):
    """`at_grant`: the value of every instance the glob matches now. A value
    JSON cannot hold (inf, NaN) is kept as None: no relative bound around it."""
    from harness.edit_card import _component_attr
    target = edit["target"]
    try:
        frame = getattr(ctx.network, _component_attr(target["component"]))
    except Exception:  # noqa: BLE001 — an unknown class matches no instance
        return {}
    out = {}
    for name in frame.index:
        if not fnmatch.fnmatchcase(str(name), target["name"]):
            continue
        value = frame.at[name, target["attr"]] if target["attr"] in frame.columns else None
        value = value.item() if hasattr(value, "item") else value
        out[str(name)] = value if isinstance(value, str) or _number(value) else None
    return out


def decide(session, task_id, verdict, envelope=None, notify_email=False):
    """The user's verdict on the plan-review card; returns the message the
    harness writes into the conversation. Reached only from the `/stream`
    request's `envelope_decision`, never from a tool call."""
    from harness import approvals
    from services import assistant_tasks, run_profiles
    from services.pypsa_service import PyPSAService
    if verdict not in ("unattended", "stepwise", "revoke"):
        _invalid("Choose unattended, stepwise or revoke.")
    with assistant_tasks._LOCK:
        path, task = assistant_tasks._read(task_id)
        current = _load(path)
        if not task.get("run") or current is None:
            _refuse("envelope_not_grantable", "This plan is not a harness run with an envelope.")
        if verdict == "stepwise":
            return f"Approve, step by step (plan: {task['title']}): every write and run keeps its own card."
        if verdict == "revoke":
            if current["state"] != "granted":
                _refuse("envelope_not_grantable", "This run has no granted envelope to stop.")
            current["state"] = "revoked"
            _audit(current, "revoked", reason="user")
            _save(path, current)
            if task["run"]["status"] in ("running", "waiting_job", "needs_user"):
                task["run"]["status"], task["run"]["reason"] = "paused", "envelope_revoked"
                task["status"] = "needs_review" if task["status"] not in ("cancelled", "completed") else task["status"]
                assistant_tasks._save(path, task)
            message = f"Stop the unattended run (plan: {task['title']}): the envelope is revoked."
        else:
            if task["status"] in ("cancelled", "completed") or (current["state"] == "granted" and not _suspended(current)):
                _refuse("envelope_not_grantable", "This run is over or already runs inside a granted envelope.")
            block = check_block(run_profiles.resolve(task["run"]["profile"])["envelope"])
            chosen = narrow(block, envelope if envelope is not None else block)
            ctx = PyPSAService.get_active_context()
            fingerprint = assistant_tasks._fingerprint()
            if fingerprint is None or str(ctx.project_uuid) != task["project_id"]:
                _refuse("brief_inputs_unreadable", "The project's inputs cannot be read, so nothing can be granted.")
            if assistant_tasks._stale(task, fingerprint):
                _refuse("task_brief_stale", "The inputs changed since the brief; review a new brief first.")
            envelope_id, now = str(uuid.uuid4()), _now()
            if not approvals.decided(session, call_id="envelope-" + envelope_id, tool="grant_envelope",
                                     tier="envelope", outcome="approved", by="user",
                                     link={"envelope_id": envelope_id}):
                _refuse("approval_unavailable", "The grant could not be put on record, so nothing is granted.")
            granted = dict(current, id=envelope_id, granted_at=_iso(now),
                           expires_at=_iso(now + chosen["budget"]["wall_time_h"] * 3600),
                           fingerprint=fingerprint, chain=[],
                           edits=[dict(e, at_grant=_instances(ctx, e)) for e in chosen["edits"]],
                           runs=chosen["runs"], budget=chosen["budget"], research=chosen["research"],
                           notify={"email": bool(notify_email)}, state="granted")
            _audit(granted, "granted", reason="user")
            _save(path, granted)
            message = (f"Approve and run unattended (plan: {task['title']}), inside this envelope until "
                       f"{granted['expires_at']}:\n{_rows(granted)}")
    assistant_tasks._wake(path)
    return message


def end(task_path, state, reason):
    """Expire, exhaust or revoke a granted envelope (cancel_task, the runner)."""
    from services import assistant_tasks
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        if envelope is None or envelope["state"] != "granted":
            return False
        envelope["state"] = state
        _audit(envelope, state, reason=reason)
        _save(task_path, envelope)
        return True


def _suspended(envelope):
    """A pausing refusal (drift, an unreadable fingerprint, a stale brief)
    since the grant: the run goes on step by step until the user grants
    again."""
    since = max((i for i, a in enumerate(envelope["audit"]) if a["event"] == "granted"), default=-1)
    return any(a["event"] == "refused" and a.get("reason") in PAUSING for a in envelope["audit"][since + 1:])


def live(task_path):
    """Granted, not expired, not suspended: the runner may go on without a click."""
    envelope = _load(task_path)
    return envelope is not None and envelope["state"] == "granted" and not _suspended(envelope) \
        and _now() < _epoch(envelope["expires_at"])


def lapsed(task_path):
    """Granted, but past `expires_at`: the runner ends it and pauses."""
    envelope = _load(task_path)
    return envelope is not None and envelope["state"] == "granted" and _now() >= _epoch(envelope["expires_at"])


def covers(task_path, tool):
    """A live envelope lists `tool` (the runner then dispatches without
    waiting; admission still decides, at dispatch)."""
    envelope = _load(task_path) if live(task_path) else None
    return envelope is not None and any(e["tool"] == tool for e in envelope["edits"] + envelope["runs"])


def last_refusal(task_path):
    envelope = _load(task_path)
    rows = [a for a in (envelope or {}).get("audit", []) if a["event"] == "refused"]
    return rows[-1]["reason"] if rows else None


# ── Admission ────────────────────────────────────────────────────────────────


def _research(value):
    if isinstance(value, dict):
        return any(k in RESEARCH_KEYS or _research(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_research(v) for v in value)
    return isinstance(value, str) and value.startswith(RESEARCH_SOURCE)


def _deletes(tool, edit_card):
    rows = (edit_card or {}).get("rows") or []
    return re.search(r"(^|_)(delete|remove)(_|$)", tool) is not None \
        or any(r.get("field") == "exists" and r.get("old") is True and r.get("new") is False for r in rows)


def _match_edit(envelope, tool, edit_card):
    """None when every typed row of the card is a listed instance within its
    bounds, else the reason."""
    if not edit_card or not edit_card.get("complete"):
        return "unresolved_target"
    rows = edit_card.get("rows") or []
    if any(r.get("field") == "exists" for r in rows):
        return "outside_entry"  # it creates (or removes) a component
    typed = [r for r in rows if r.get("role") == "typed"]
    if not typed:
        return "unresolved_target"
    for row in typed:
        entries = [e for e in envelope["edits"] if e["tool"] == tool
                   and e["target"]["component"] == row.get("component_class") and e["target"]["attr"] == row.get("field")
                   and fnmatch.fnmatchcase(str(row.get("name")), e["target"]["name"])]
        if not entries:
            return "outside_entry"
        entries = [e for e in entries if str(row["name"]) in e["at_grant"]]
        if not entries:
            return "new_instance"
        if not any(_in_bounds(e, row["new"], e["at_grant"][str(row["name"])]) for e in entries):
            return "out_of_bounds"
    return None


def _in_bounds(entry, new, at_grant):
    if "enum" in entry:
        return any(new == v and type(new) is type(v) for v in entry["enum"])
    if not _number(new):
        return False
    bounds = entry["bounds"]
    if new < bounds.get("min", -math.inf) or new > bounds.get("max", math.inf):
        return False
    if "rel" in bounds:
        # Relative to the value AT GRANT, never to the current value (B2).
        return _number(at_grant) and abs(new - at_grant) <= bounds["rel"] * abs(at_grant) * (1 + 1e-12)
    return True


def _refusal(session, envelope, task, tool, args, tier, external, edit_card, ctx):
    from harness.catalogue import TOOLS
    if external:
        return "external"
    if tool not in {t["name"] for t in TOOLS}:
        return "unknown_tool"
    if tool in NEVER_ADMITTED:
        return "grid_code_publish"
    if _deletes(tool, edit_card):
        return "delete"
    if tier == "destructive":
        return "destructive"
    if _research(args):
        return "research_value"
    is_run = any(r["tool"] == tool for r in envelope["runs"])
    reason = _standing(session, envelope, task, ctx, solve=is_run)
    if reason is not None:
        return reason
    if _names_another_project(args, ctx):
        return "wrong_project"
    if is_run:
        kinds = [r.get("kind") for r in envelope["runs"] if r["tool"] == tool]
        return None if None in kinds or (isinstance(args, dict) and args.get("kind") in kinds) else "outside_entry"
    if not any(e["tool"] == tool for e in envelope["edits"]):
        return "unlisted_tool"
    return _match_edit(envelope, tool, edit_card)


def _names_another_project(args, ctx):
    """Whether a call's arguments name any project but the run's (GK B (8)):
    every `project*` argument, a list of them included, must be the run's
    uuid, or its name where the tool would not read the name as a uuid
    (`project_registry.find_project`). Fail closed on anything else."""
    from services.project_registry import _try_uuid
    own = str(ctx.project_uuid)
    name = getattr(ctx, "loaded_project", None)
    for key, value in (args.items() if isinstance(args, dict) else ()):
        if "project" not in key or key == "projection_year":
            continue
        for item in value if isinstance(value, list) else [value]:
            if item == own or (item == name and _try_uuid(item) is None):
                continue
            return True
    return False


def _standing(session, envelope, task, ctx, solve=False):
    """What a granted envelope must still be at a dispatch or a card
    boundary: granted, this run's, unexpired, budget left, the freshness
    chain unbroken and the brief current. None, or the reason."""
    from harness.runner import budget_spent
    from services import assistant_tasks
    if envelope["state"] != "granted":
        return "not_granted" if envelope["state"] == "proposed" else envelope["state"]
    if not (envelope["task_id"] == task["task_id"] and envelope["project_id"] == task["project_id"]
            and envelope["owner_id"] == task["owner_id"] == str(session.owner_user_id)
            and ctx is not None and str(ctx.project_uuid) == envelope["project_id"]):
        return "wrong_project"
    if _now() >= _epoch(envelope["expires_at"]):
        return "expired"
    solves = sum(1 for a in envelope["audit"] if a["event"] == "used" and a.get("tool") in RUN_TOOLS)
    if budget_spent(task) or (solve and solves >= envelope["budget"]["solves"]):
        return "exhausted"
    current = _fingerprint(ctx)
    if not current:
        return "fingerprint_unreadable"
    if current != (envelope["chain"][-1]["fingerprint"] if envelope["chain"] else envelope["fingerprint"]):
        return "drift"
    if assistant_tasks._stale(task, current):
        return "brief_stale"
    return None


def admit(session, *, call_id, tool, args, tier, external, edit_card):
    """Before a card on the runner's session: "admitted" (approved and on
    record), "unavailable" (it matched but could not be recorded: deny), or
    None (the card path; a `refused` row says why when an envelope exists)."""
    from harness import approvals
    from services import assistant_tasks
    task_id = getattr(session, "task_id", None)
    if not task_id or str(getattr(session, "session_id", "") or "") != SESSION_PREFIX + str(task_id):
        return None  # an ordinary chat or copilot session never consults one (COP-1)
    try:
        with assistant_tasks._LOCK:
            path, task = assistant_tasks._read(task_id)
            envelope = _load(path)
    except (HTTPException, OSError, ValueError):
        return None
    if envelope is None or not task.get("run"):
        return None
    reason = _refusal(session, envelope, task, tool, args, tier, external, edit_card, assistant_tasks.RUN_CONTEXT.get())
    guard = getattr(session, "decision_guard", None)
    if reason is None and (guard is None or not guard(tool, args)):
        reason = "recheck"  # the project, owner, ACL or locks moved: re-checked under the locks
    link = {"envelope_id": envelope["id"]}
    with assistant_tasks._LOCK:
        envelope = _load(path)
        if reason is None and (envelope is None or envelope["state"] != "granted" or envelope["id"] != link["envelope_id"]):
            reason = "revoked"
        if reason is None:
            recorded = approvals.decided(session, call_id=call_id, tool=tool, tier=tier, outcome="approved",
                                         by="envelope:" + envelope["id"], link={**approvals.links(session, args), **link})
            _audit(envelope, "used" if recorded else "refused", call_id=call_id, tool=tool,
                   reason=None if recorded else "approval_unavailable")
            try:
                _save(path, envelope)
            except OSError:
                logger.warning("run envelope: audit for %s could not be written", tool, exc_info=True)
                return "unavailable"
            return "admitted" if recorded else "unavailable"
        if envelope is None:
            return None
        if reason in ("expired", "exhausted") and envelope["state"] == "granted":
            envelope["state"] = reason
            _audit(envelope, reason, reason=reason)
        _audit(envelope, "refused", call_id=call_id, tool=tool, reason=reason)
        try:
            _save(path, envelope)
        except OSError:
            logger.warning("run envelope: refusal of %s could not be written", tool, exc_info=True)
    return None


def refused_before_gate(task_path, call_id, tool, reason):
    """A refusal the plan's own admission made before the gate (a stale
    brief): on the envelope's audit, so it suspends as its own would."""
    from services import assistant_tasks
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        if envelope is None or envelope["state"] != "granted":
            return
        _audit(envelope, "refused", call_id=call_id, tool=tool, reason=reason)
        _save(task_path, envelope)


def cross(session, task_path, card, ctx):
    """COP-1's card boundary inside a live envelope: the runner goes on, on
    record as the envelope's decision, only while the envelope still stands
    (the same freshness, project and expiry checks as a dispatch; the tick
    has re-checked ACL and locks). Returns "crossed", the refusal's reason
    (on the audit too), or None when no live envelope applies."""
    from harness import approvals
    from services import assistant_tasks
    if not live(task_path):
        return None
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        task = json.loads(Path(task_path).read_text())
        call_id = f"stage-{card}-{uuid.uuid4().hex[:8]}"
        reason = _standing(session, envelope, task, ctx)
        if reason is None and not approvals.decided(
                session, call_id=call_id, tool="continue_at_card", tier="stage", outcome="approved",
                by="envelope:" + envelope["id"], link={"envelope_id": envelope["id"]}):
            reason = "approval_unavailable"
        _audit(envelope, "refused" if reason else "used", call_id=call_id, tool="continue_at_card", reason=reason)
        _save(task_path, envelope)
    return reason or "crossed"


def chain_head(task_path, ctx):
    """What a runner step starts from while an envelope is live, read under
    the step's locks: (fingerprint, None) on the chain's last link, else
    (fingerprint, "drift" or "fingerprint_unreadable"). (None, None) with
    no live envelope."""
    from services import assistant_tasks
    if not live(task_path):
        return None, None
    current = _fingerprint(ctx)
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
    if envelope is None or envelope["state"] != "granted":
        return None, None
    last = envelope["chain"][-1]["fingerprint"] if envelope["chain"] else envelope["fingerprint"]
    return current, None if current and current == last else "drift" if current else "fingerprint_unreadable"


def settle(task_path, call_id, ctx, before):
    """The freshness chain's next link after a runner change, only when the
    step started from the chain's last link (`before`, read under the step's
    locks): a change made before the step, even one the envelope never saw
    (an uncarded step), stays drift (GK B (8)). The brief's fingerprint
    follows the chain while it was the chain's last link (SC-11)."""
    from services import assistant_tasks
    current = _fingerprint(ctx)  # None: no link, so the next admission reads drift
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        if envelope is None or envelope["state"] != "granted" or not current or not before:
            return
        last = envelope["chain"][-1]["fingerprint"] if envelope["chain"] else envelope["fingerprint"]
        if before != last or current == last:
            return
        envelope["chain"].append({"after_change": call_id, "fingerprint": current})
        _save(task_path, envelope)
        task = json.loads(Path(task_path).read_text())
        if task.get("fingerprint") == last:
            task["fingerprint"] = current
            assistant_tasks._save(Path(task_path), task)
