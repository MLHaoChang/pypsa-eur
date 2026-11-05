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

import contextlib
import copy
import fnmatch
import hashlib
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
# Issue 05 PR C: the engine loops a result rule may drive. A grant lists one
# only to cover `restore: "final"` (a solver-config write, LC-40); its solves
# are its own audit row's (`reserve_engine`), never one admitted solve.
ENGINE_TOOLS = frozenset({"run_margin_loop", "run_coupling_loop"})
# Counted as solves when admitted, but no envelope lists them (issue 08, GK B (8)):
# a sweep or an EH study is always the user's own card.
NEVER_LISTED = frozenset({"run_sensitivity_sweep", "run_eh_study"})
# Always its own card, even when listed: others rely on the publication (§7).
NEVER_ADMITTED = frozenset({"campus_publish_grid_code"})
# A researched value is never written without the user's card (SH-RESEARCH-1).
# The research step (issue 09) marks what it brings in with these keys or a
# `research:` source; a write that carries one never matches.
RESEARCH_KEYS = frozenset({"research", "research_id", "research_ref", "research_value"})
RESEARCH_SOURCE = "research:"

# Refusals that end the envelope's use for this run: the runner pauses and
# notifies (§7 "Scope and audit", the freshness chain).
PAUSING = frozenset({"expired", "revoked", "exhausted", "drift", "fingerprint_unreadable", "brief_stale",
                     "handover_unbound"})

# Data-gap tools (§7, GK B; issue 04): admitted only from a `gaps` entry that
# names the target, and only to fill a gap: the instance had no series (or the
# setting no value) at the grant and still has none. A value that is there is
# never replaced, so §6.0 holds: demand is filled where it is missing, never
# changed. Each tool fills these (component, attr) targets only.
GAP_TARGETS = {
    "apply_generated_profile": frozenset({("Load", "p_set")}),
    "attach_tariff": frozenset({("Settings", "import_tariff_ref")}),
    "set_weather_choice": frozenset({("Settings", "source"), ("Settings", "year")}),
}
# The step that prepares a fill: it writes only the pending profile store, for
# the loads its args name, and is admitted only for gaps a listed entry names.
GAP_PREPARE = {"generate_load_profile": "apply_generated_profile"}
# GK B's conditions on the gaps grant (issue 04, AI-567b): a fill's value never
# comes from the model. Each gap names a source, fixed in the profile and
# resolved to concrete values at the grant; L1 may only copy them.
#   load_generator: the generator run with the profile's archetype and year,
#     its energy from a source in LOAD_ENERGY_SOURCES;
#   weather_choices: one of the profile's listed (source, year) pairs;
#   library_tariff: a Library tariff the project's organisation already holds,
#     listed at the grant by its hash (L1 never creates or uploads one).
# A new kind (an SH-21 intake row) is one more entry here and in the checks.
GAP_SOURCES = {"apply_generated_profile": "load_generator", "set_weather_choice": "weather_choices",
               "attach_tariff": "library_tariff"}
LOAD_ARCHETYPES = ("industrial", "commercial", "residential", "ev_charging", "heat", "data_centre")
# The energies a load_generator source may name, each resolving
# (network, load) to the generator's params, or None when it has none for that
# load. Empty until one is ruled (GK B, issue 04): a static p_set is constant
# demand, not a gap, and reshaping it raises the peak (§6.0). An SH-21 intake
# row or an owner decision adds one; until then a Load fill gets a card.
LOAD_ENERGY_SOURCES: dict = {}
_PREPARE_ARGS = {"project_id", "archetype", "loads", "params", "year", "replay_refinements"}

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
    if not isinstance(entry, dict) or "tool" not in entry or not set(entry) <= {"tool", "kind", "restore"} \
            or entry["tool"] not in RUN_TOOLS | ENGINE_TOOLS or not isinstance(entry.get("kind", ""), str) \
            or ("restore" in entry and (entry["tool"] not in ENGINE_TOOLS or entry["restore"] != "final")):
        raise EnvelopeError(f"A run names one of {sorted(RUN_TOOLS | ENGINE_TOOLS)} and an optional kind; an "
                            f"engine loop may carry restore \"final\": {entry!r}")
    if entry["tool"] in NEVER_LISTED:
        raise EnvelopeError(f"{entry['tool']} is never in an envelope; it is always its own card.")
    return dict(entry)


def _check_source(tool, source):
    kind = GAP_SOURCES[tool]
    if not isinstance(source, dict) or source.get("kind") != kind:
        raise EnvelopeError(f"A {tool} gap names its source, of kind {kind}.")
    if kind == "load_generator":
        if set(source) != {"kind", "archetype", "year", "energy"} or source["archetype"] not in LOAD_ARCHETYPES \
                or isinstance(source["year"], bool) or not isinstance(source["year"], int) \
                or source["energy"] not in LOAD_ENERGY_SOURCES:
            raise EnvelopeError(f"A load_generator source names archetype, year and an energy source "
                                f"({sorted(LOAD_ENERGY_SOURCES) or 'none is ruled yet'}).")
    elif kind == "weather_choices":
        choices = source.get("choices")
        if set(source) != {"kind", "choices"} or not isinstance(choices, list) or not choices or not all(
                isinstance(c, dict) and set(c) == {"source", "year"} and isinstance(c["source"], str)
                and isinstance(c["year"], int) and not isinstance(c["year"], bool) for c in choices):
            raise EnvelopeError("A weather_choices source lists its (source, year) choices.")
    elif set(source) != {"kind"}:
        raise EnvelopeError("A library_tariff source carries its kind only; the grant lists the tariffs.")
    return copy.deepcopy(source)


def _check_gap(entry):
    if not isinstance(entry, dict) or set(entry) != {"tool", "target", "source"} or entry["tool"] not in GAP_TARGETS:
        raise EnvelopeError(f"A gap names one of {sorted(GAP_TARGETS)}, its target and its source: {entry!r}")
    target = entry["target"]
    if not isinstance(target, dict) or set(target) != {"component", "name", "attr"} \
            or not all(isinstance(v, str) and v for v in target.values()) or len(target["name"]) > 64:
        raise EnvelopeError("A gap's target names component, name and attr.")
    if (target["component"], target["attr"]) not in GAP_TARGETS[entry["tool"]]:
        raise EnvelopeError(f"{entry['tool']} fills only {sorted(GAP_TARGETS[entry['tool']])}.")
    if target["component"] == "Settings" and _GLOB.search(target["name"]):
        raise EnvelopeError("A settings gap names its setting, never a glob.")
    return {"tool": entry["tool"], "target": dict(target), "source": _check_source(entry["tool"], entry["source"])}


def _source_within(mine, wide):
    """A grant's source narrows the profile's: the same generator inputs, a
    subset of the weather choices, the same tariff rule."""
    if mine["kind"] == "weather_choices":
        return all(c in wide["choices"] for c in mine["choices"])
    return mine == wide


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
    if not isinstance(block, dict) or not {"edits", "runs", "budget"} <= set(block) \
            <= {"edits", "runs", "budget", "research", "gaps"}:
        raise EnvelopeError("An envelope block has edits, runs, budget and optional research and gaps.")
    if not isinstance(block["edits"], list) or not isinstance(block["runs"], list) \
            or not isinstance(block.get("gaps", []), list):
        raise EnvelopeError("edits, runs and gaps are lists.")
    return {"edits": [_check_edit(e) for e in block["edits"]], "runs": [_check_run(r) for r in block["runs"]],
            "gaps": [_check_gap(g) for g in block.get("gaps", [])],
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
    for gap in mine["gaps"]:
        if not any(gap["tool"] == wide["tool"] and gap["target"]["component"] == wide["target"]["component"]
                   and gap["target"]["attr"] == wide["target"]["attr"]
                   and (gap["target"]["name"] == wide["target"]["name"] or (
                       not _GLOB.search(gap["target"]["name"])
                       and fnmatch.fnmatchcase(gap["target"]["name"], wide["target"]["name"])))
                   and _source_within(gap["source"], wide["source"])
                   for wide in block["gaps"]):
            _invalid(f"{gap['tool']} on {gap['target']} is wider than the profile's envelope.")
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


def _pickup_refused(envelope):
    """The calls admitted but refused at pickup (issue 22): nothing ran."""
    return {a.get("call_id") for a in envelope["audit"] if a["event"] == "refused" and a.get("call_id")}


def solves_used(envelope):
    """The run's solves (issue 05, C4): each admitted run tool that was not
    refused at pickup, and each reserving row (an engine loop's, PR C; any
    event that carries `reserved`): its settled `solves`, else its whole
    reservation (a loop still running, or lost). A failed or cancelled job
    still counts. The one count both the envelope's budget check and
    `run.budget.used.solves` read."""
    gone = _pickup_refused(envelope)
    return sum(1 for a in envelope["audit"]
               if a["event"] == "used" and a.get("tool") in RUN_TOOLS and a.get("call_id") not in gone) \
        + sum(int(a["solves"] if "solves" in a else a["reserved"]) for a in envelope["audit"]
              if "reserved" in a and a.get("call_id") not in gone)


def restore_covered(envelope, tool, restore):
    """`restore: "final"` writes the solver config (LC-40): only a grant
    whose runs list the engine tool with it covers that (GK A (12) Q2)."""
    return restore == "base" or any(r.get("tool") == tool and r.get("restore") == "final"
                                    for r in (envelope or {}).get("runs", []))


def reserve_engine(task_path, *, call_id, tool, rule, max_solves, overhead, restore):
    """An engine loop's start (issue 05 PR C, C4, conditions 2, 3, 9): under
    the task lock, its `max_solves` is the rule's, clamped to the solves left
    after its `overhead` (probe, closing re-solve) and to `MAX_LOOP_SOLVES`,
    and that worst case is reserved on one audit row. (max_solves, None),
    or (None, reason) with nothing reserved."""
    from harness.runner import budget_spent
    from services import assistant_tasks
    from services.adequacy.coupling import MAX_LOOP_SOLVES
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        task = json.loads(Path(task_path).read_text())
        if envelope is None or not live(task_path):
            return None, "no_envelope"
        if not restore_covered(envelope, tool, restore):
            return None, "restore_uncovered"
        left = envelope["budget"]["solves"] - solves_used(envelope)
        clamped = min(int(max_solves), left - int(overhead), MAX_LOOP_SOLVES)
        if budget_spent(task) or clamped < 1:
            return None, "budget_spent"
        _audit(envelope, "engine", call_id=call_id, tool=tool, rule=rule, restore=restore,
               reserved=clamped + int(overhead))
        _save(task_path, envelope)
        _count_solves(task_path, envelope)
        return clamped, None


def settle_engine_solves(task_path, call_id, solves):
    """The loop ended: its reservation becomes what it spent, once (C7).
    False when the row is gone or already settled."""
    from services import assistant_tasks
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        row = next((a for a in (envelope or {}).get("audit", [])
                    if a["event"] == "engine" and a.get("call_id") == call_id), None)
        if row is None or "solves" in row:
            return False
        row["solves"] = int(solves)
        _save(task_path, envelope)
        _count_solves(task_path, envelope)
        return True


# Issue 05 (C2): an L2 round's third admitted change is refused with this kind.
CHANGE_CAP_KIND = "round_change_cap"


def _changes_in(envelope, loop, round_no):
    """The edits admitted in one loop round and not refused at pickup."""
    gone = _pickup_refused(envelope)
    return sum(1 for a in envelope["audit"] if a["event"] == "used" and a.get("loop") == loop
               and a.get("round") == round_no and a.get("tool") not in RUN_TOOLS and a.get("call_id") not in gone)


def _count_solves(task_path, envelope):
    """`run.budget.used.solves` from `solves_used` (under `assistant_tasks._LOCK`)."""
    from harness.runner import _budget
    from services import assistant_tasks
    task = json.loads(Path(task_path).read_text())
    if not task.get("run"):
        return
    used = _budget(task)["used"]
    if used.get("solves") != solves_used(envelope):
        used["solves"] = solves_used(envelope)
        assistant_tasks._save(Path(task_path), task)


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
                "gaps": [dict(g, at_grant={}) for g in copy.deepcopy(block.get("gaps", []))],
                "research": dict(block["research"]), "notify": {"email": False},
                # Issue 21 §2: the run's own setup (create the project from the
                # template with the confirmed intake inputs), set by `set_setup`.
                "setup": None,
                "state": "proposed", "audit": []}
    _save(task_path, envelope)
    return envelope


SETUP_TOOL = "create_project_from_template"


def set_setup(task_path, template, name, params_digest):
    """Issue 21 §2: the proposed envelope's `setup` entry, the card's first
    row ("Create project <name> from the template with the inputs above"),
    bound to the intake rows' digest. Only a proposed envelope takes it."""
    from services import assistant_tasks
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        _invalid("The new project needs a name of at most 120 characters.")
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        if envelope is None or envelope["state"] != "proposed":
            _refuse("envelope_not_grantable", "Only a proposed plan takes a setup entry.")
        envelope["setup"] = {"tool": SETUP_TOOL, "template": template, "name": name.strip(),
                             "params_digest": params_digest}
        _save(task_path, envelope)
        return envelope


def _rows(envelope):
    lines = []
    setup = envelope.get("setup")
    if setup:
        lines.append(f"- Create project `{setup['name']}` from the {setup['template']} template "
                     "with the inputs above")
    for e in envelope["edits"]:
        t = e["target"]
        allowed = ", ".join(map(str, e["enum"])) if "enum" in e else ", ".join(
            f"{k} {v:g}" if k != "rel" else f"±{v:.0%} of the value at grant" for k, v in e["bounds"].items())
        lines.append(f"- Edit {t['component']} `{t['name']}` {t['attr']} ({allowed}) with {e['tool']}")
    lines += [_gap_row(g) for g in envelope.get("gaps", [])]
    lines += [f"- Run {r['tool']}" + (f" ({r['kind']})" if r.get("kind") else "") for r in envelope["runs"]]
    b = envelope["budget"]
    lines.append(f"- Up to {b['solves']} solves and {b['rounds']} rounds, for at most {b['wall_time_h']:g} h")
    r = envelope["research"]
    lines.append(f"- Research: up to EUR {r['cost_cap_eur']:g}" if r["allowed"] else "- No research")
    lines.append("- Never: deletes, grid-code publish, external tools, researched values, anything else (a card each)")
    return "\n".join(lines)


def _gap_row(gap):
    """One plan-review row for a gap entry, in the user's words (UX)."""
    t, source = gap["target"], gap["source"]
    fill = f"- Fill a missing {t['component']} `{t['name']}` {t['attr']} with {gap['tool']}"
    never = " (only where none is set; never replaces one)"
    if source["kind"] == "load_generator":
        return f"{fill} from a {source['archetype']} {source['year']} profile from {source['energy']}{never}"
    if source["kind"] == "weather_choices":
        return f"{fill} from one of " + ", ".join(f"{c['source']} {c['year']}" for c in source["choices"]) + never
    if not gap.get("at_grant"):  # the plan, before you approve it
        return f"{fill} from a Library tariff, only when exactly one is held when you approve{never}"
    held = [f"{name} v{version}" for record in gap["at_grant"].values()
            for name, version in (record.get("tariffs") or {}).values()]
    if len(held) == 1:
        return f"{fill} from the Library tariff {held[0]}{never}"
    if not held:
        return "- No tariff is filled: none is held in the Library when you approve."
    both = "both" if len(held) == 2 else "all"
    return f"- No tariff is filled: {', '.join(held[:-1])} and {held[-1]} are {both} held, so you choose one yourself."


def describe(envelope):
    """What the plan-review card shows (issue 08 renders it)."""
    if envelope is None:
        return None
    audit = envelope["audit"]
    return {"id": envelope["id"], "state": envelope["state"], "expires_at": envelope["expires_at"],
            "edits": [{k: v for k, v in e.items() if k != "at_grant"} for e in envelope["edits"]],
            "gaps": [{k: v for k, v in g.items() if k != "at_grant"} for g in envelope.get("gaps", [])],
            "runs": envelope["runs"], "budget": envelope["budget"], "research": envelope["research"],
            "notify": envelope["notify"], "setup": envelope.get("setup"),
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


def _has_series(ctx, name):
    return str(name) in {str(c) for c in ctx.network.loads_t.p_set.columns}


def source_digest(record):
    """The digest an admitted fill is bound to: its grant-time source record."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()


class HeldTariffWithoutId(ValueError):
    """A held Library tariff whose ref carries no id: it cannot be named, so
    the grant records no tariff (GK B (21), issue 27)."""

    def __init__(self, ref):
        self.ref = {k: ref.get(k) for k in ("kind", "hash", "version")}
        super().__init__(f"a held Library tariff has no id (hash {self.ref['hash']}, version {self.ref['version']})")


def shipped_ref_dumps(kind):
    """Every shipped common Library item of `kind`, each ref dumped as
    `list_items` lists it ({kind, id, version, hash}). Held-tariff detection
    compares whole refs against this list (see `_library_tariffs`)."""
    from services.library import common
    return [common.ref(kind, name).model_dump(mode="json") for name in common.names(kind)]


def _library_tariffs():
    """{hash: [name, version]} of the Library tariffs the project's
    organisation holds now (read as `attach_tariff` reads them). Only the
    organisation's own tariffs are held (GK B, issue 27): a shipped common
    tariff, which every organisation sees, is left out when its ref equals
    the shipped one exactly (kind, id, version and hash), so an own tariff
    that shares a shipped id still counts. A held tariff is named by its
    ref's `id`; one without an id raises, so the grant records no tariff and
    the fill is refused (fail closed)."""
    from routers.library import ItemKind, list_items
    from services import chat_tools
    shipped = shipped_ref_dumps("tariff")
    items = chat_tools._library_call(list_items, ItemKind.tariff)
    items = items.get("items", items) if isinstance(items, dict) else items
    out = {}
    for item in items or []:
        item = item if isinstance(item, dict) else item.model_dump(mode="json")
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else item
        if not isinstance(ref.get("hash"), str) or not ref["hash"] or ref in shipped:
            continue
        if not isinstance(ref.get("id"), str) or not ref["id"]:
            raise HeldTariffWithoutId(ref)
        out[ref["hash"]] = [ref["id"], ref.get("version")]
    return out


def _tariff_hash(args):
    from routers.library import ItemKind, get_item
    from services import chat_tools
    ref = chat_tools._library_call(get_item, ItemKind.tariff, args.get("name"), version=args.get("version")).ref
    return ref.hash


def _gap_instances(ctx, gap):
    """`at_grant` of a gap entry: each instance it names that is a gap now,
    with the concrete source its fill must copy (GK B, AI-567b). A component
    made or renamed later is never in it. A Load gap is an electric site load
    only: a named offtake or process load refuses the grant, a glob skips it.
    Anything unreadable names no gap (fail closed)."""
    target, source = gap["target"], gap["source"]
    if target["component"] == "Settings":
        try:
            if source["kind"] == "weather_choices":
                return {target["name"]: {"choices": copy.deepcopy(source["choices"])}}
            return {target["name"]: {"tariffs": _library_tariffs()}}
        except HeldTariffWithoutId as err:  # a held tariff that cannot be named grants none
            logger.warning("run envelope: no tariff is granted: %s", err)
            return {}
        except Exception:  # noqa: BLE001 — an unreadable Library grants no tariff
            logger.warning("run envelope: the Library tariffs could not be listed at the grant", exc_info=True)
            return {}
    from services.adequacy.site_peak import load_split
    try:
        network = ctx.network
        electric, other = load_split(network)
    except Exception:  # noqa: BLE001 — an unreadable network names no gap
        return {}
    if not _GLOB.search(target["name"]) and target["name"] in {o[0] for o in other}:
        _invalid(f"Load {target['name']} is an offtake or process demand: it is never filled as a data gap.")
    out = {}
    for name in electric:
        if not fnmatch.fnmatchcase(str(name), target["name"]) or _has_series(ctx, name):
            continue
        try:
            params = LOAD_ENERGY_SOURCES[source["energy"]](network, name)
        except Exception:  # noqa: BLE001 — a source that cannot be read names no gap
            params = None
        if isinstance(params, dict) and params:
            out[str(name)] = {"archetype": source["archetype"], "year": source["year"], "params": params,
                              "identity": _identity(network, name)}
    return out


def _identity(network, name):
    """What makes a gap load the one granted: a load renamed onto a granted
    name, or made again under it, reads another bus or static value."""
    return {"bus": str(network.loads.at[name, "bus"]), "p_set": float(network.loads.at[name, "p_set"])}


def _same_load(ctx, name, record):
    try:
        return _identity(ctx.network, name) == record["identity"]
    except (KeyError, TypeError, ValueError):
        return False


def decide(session, task_id, verdict, envelope=None, notify_email=False, intake_digest=None):
    """The user's verdict on the plan-review card; returns the message the
    harness writes into the conversation. Reached only from the `/stream`
    request's `envelope_decision`, never from a tool call. A plan with a
    `setup` entry hands over at the grant (`_grant_setup`, issue 21 §2):
    `intake_digest` is the digest of the intake rows the card showed."""
    from harness import approvals
    from services import assistant_tasks, run_profiles
    from services.pypsa_service import PyPSAService
    if verdict not in ("unattended", "stepwise", "revoke"):
        _invalid("Choose unattended, stepwise or revoke.")
    setup = None
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
            if current.get("setup"):
                setup = (path, task, current, chosen)
        if setup is None and verdict == "unattended":
            ctx = PyPSAService.get_active_context()
            fingerprint = assistant_tasks._fingerprint()
            if fingerprint is None or str(ctx.project_uuid) != task["project_id"]:
                _refuse("brief_inputs_unreadable", "The project's inputs cannot be read, so nothing can be granted.")
            if assistant_tasks._stale(task, fingerprint):
                _refuse("task_brief_stale", "The inputs changed since the brief; review a new brief first.")
            gaps = [dict(g, at_grant=_gap_instances(ctx, g)) for g in chosen["gaps"]]  # may refuse (§6.0)
            envelope_id, now = str(uuid.uuid4()), _now()
            if not approvals.decided(session, call_id="envelope-" + envelope_id, tool="grant_envelope",
                                     tier="envelope", outcome="approved", by="user",
                                     link={"envelope_id": envelope_id}):
                _refuse("approval_unavailable", "The grant could not be put on record, so nothing is granted.")
            granted = dict(current, id=envelope_id, granted_at=_iso(now),
                           expires_at=_iso(now + chosen["budget"]["wall_time_h"] * 3600),
                           fingerprint=fingerprint, chain=[],
                           edits=[dict(e, at_grant=_instances(ctx, e)) for e in chosen["edits"]],
                           gaps=gaps, prepared=[],
                           runs=chosen["runs"], budget=chosen["budget"], research=chosen["research"],
                           notify={"email": bool(notify_email)}, state="granted")
            _audit(granted, "granted", reason="user")
            _save(path, granted)
            message = (f"Approve and run unattended (plan: {task['title']}), inside this envelope until "
                       f"{granted['expires_at']}:\n{_rows(granted)}")
    if setup is not None:
        return _grant_setup(session, *setup, intake_digest=intake_digest, notify_email=notify_email)
    assistant_tasks._wake(path)
    return message


def _cited_uploads(rows):
    return sorted({r["source"]["upload_id"] for r in rows
                   if r["status"] == "confirmed" and r["source"].get("upload_id")})


def _grant_setup(session, path, task, current, chosen, *, intake_digest, notify_email):
    """Issue 21 §2, create at grant: the intake digest is checked against the
    rows and their sources (R6), the user's decision goes on record, then
    `run_handover.hand_over` creates the project and starts the run there,
    and the host task ends `handed_over`. No lock is held here: the
    hand-over's file lock comes first, `assistant_tasks._LOCK` last (R3)."""
    from harness import approvals
    from services import assistant_tasks, run_handover, run_intake, run_profiles
    from services.pypsa_service import PyPSAService
    setup = current["setup"]
    intake = task["run"].get("intake") or {}
    rows = intake.get("rows") or []
    profile = run_profiles.resolve(task["run"]["profile"])
    ctx = PyPSAService.get_active_context()
    if str(ctx.project_uuid) != task["project_id"]:
        _refuse("brief_inputs_unreadable", "The project's inputs cannot be read, so nothing can be granted.")
    if not isinstance(intake_digest, str) or setup.get("params_digest") != intake_digest:
        _refuse("intake_stale", "The inputs changed since the card was shown; review them again.")
    items = run_intake.missing(profile, rows, intake.get("gaps") or [])
    if items:
        _refuse("intake_missing", run_intake.request_text(items))
    try:
        host = run_intake.host_for(ctx, task["owner_id"], task["project_id"])
        run_intake.check_grant(rows, intake_digest, host)
    except run_intake.IntakeError as exc:
        if exc.kind == "not_found":
            _refuse("not_found", str(exc), 404)
        _refuse("intake_stale", str(exc))
    confirmed = run_intake.confirm(rows)
    prior = run_handover.read_intent(path)
    if prior is not None and prior.get("digest") == intake_digest:
        decision_id = prior["decision_id"]  # a retried grant goes on from its intent (A1)
    else:
        decision_id = "envelope-" + str(uuid.uuid4())  # put on record by the precheck, once it passes
    handover = {"decision_id": decision_id, "digest": intake_digest}

    def precheck():
        # GK B (20) Q1: under the hand-over lock, the host's inputs and the
        # requirements the brief recorded, never a live re-read as the baseline.
        with assistant_tasks._LOCK:
            _, fresh = assistant_tasks._read(task["task_id"])
        if fresh["status"] in ("cancelled", "completed"):
            raise run_handover.HandoverError("envelope_not_grantable", "This run is over.")
        fingerprint = assistant_tasks._fingerprint()
        if fingerprint is None:
            raise run_handover.HandoverError("brief_inputs_unreadable",
                                             "The project's inputs cannot be read, so nothing can be granted.")
        if assistant_tasks._stale(fresh, fingerprint):
            raise run_handover.HandoverError("task_brief_stale",
                                             "The inputs or requirements changed since the brief; review a new "
                                             "brief first.")
        # Only a grant that passed the checks goes on record, once (GK B (20)): a
        # retry that resumes an intent keeps the decision that intent names.
        if run_handover.read_intent(path) is None and not approvals.decided(
                session, call_id=decision_id, tool="grant_envelope", tier="envelope", outcome="approved",
                by="user", link={"envelope_id": current["id"], "setup": setup["template"]}):
            raise run_handover.HandoverError("approval_unavailable",
                                             "The grant could not be put on record, so nothing is granted.")

    def grant(new_path, new_task, new_ctx):
        grant_handover(new_path, new_task, chosen, handover=handover, ctx=new_ctx, notify_email=notify_email)

    start = assistant_tasks.handover_starter(task, profile, task["owner_id"], grant=grant, handover=handover)
    try:
        intent = run_handover.hand_over(
            path, owner_id=task["owner_id"], origin={"project_id": task["project_id"], "task_id": task["task_id"]},
            name=setup["name"], params=run_intake.params(confirmed), digest=intake_digest, decision_id=decision_id,
            host_name=host.name, host_dir=host.dir, uploads=_cited_uploads(confirmed), start=start,
            template=setup["template"], precheck=precheck)
    except run_handover.HandoverError as exc:
        _HANDOVER_REFUSALS.get(exc.kind, _HANDOVER_REFUSALS["handover_incomplete"])(str(exc))
    assistant_tasks.end_handed_over(path, intent)
    with assistant_tasks._LOCK:
        envelope = _load(path)
        if envelope is not None:
            _audit(envelope, "handed_over", project_id=intent["project_id"], task_id=intent["task_id"],
                   decision_id=decision_id)
            _save(path, envelope)
    return (f"Approve and run unattended (plan: {task['title']}): project `{intent['project_name']}` was created "
            f"from the {setup['template']} template with the inputs shown, and the run goes on there.")


# A hand-over's refusal, as its own literal kind (the error-kind manifest).
_HANDOVER_REFUSALS = {
    "handover_busy": lambda m: _refuse("handover_busy", m),
    "handover_conflict": lambda m: _refuse("handover_conflict", m),
    "handover_incomplete": lambda m: _refuse("handover_incomplete", m),
    "upload_copy_mismatch": lambda m: _refuse("upload_copy_mismatch", m),
    "access": lambda m: _refuse("not_authorized", m, 403),
    "envelope_not_grantable": lambda m: _refuse("envelope_not_grantable", m),
    "brief_inputs_unreadable": lambda m: _refuse("brief_inputs_unreadable", m),
    "task_brief_stale": lambda m: _refuse("task_brief_stale", m),
    "approval_unavailable": lambda m: _refuse("approval_unavailable", m),
}


def grant_handover(task_path, task, chosen, *, handover, ctx, notify_email=False):
    """The created project's envelope, granted as the host card's decision
    (A2): it carries that decision id and the grant digest, the narrowing the
    user chose, and the fingerprint after creation as its chain's first link.
    Called once, by the hand-over's `start`, before the run is woken."""
    envelope = _load(task_path)
    if envelope is None or envelope["state"] != "proposed":
        raise ValueError("The created project's run has no proposed envelope.")
    fingerprint, now = task["fingerprint"], _now()
    granted = dict(envelope, id=str(uuid.uuid4()), granted_at=_iso(now),
                   expires_at=_iso(now + chosen["budget"]["wall_time_h"] * 3600),
                   fingerprint=fingerprint, chain=[{"after_change": "handover", "fingerprint": fingerprint}],
                   edits=[dict(e, at_grant=_instances(ctx, e)) for e in chosen["edits"]],
                   gaps=[dict(g, at_grant=_gap_instances(ctx, g)) for g in chosen["gaps"]], prepared=[],
                   runs=chosen["runs"], budget=chosen["budget"], research=chosen["research"],
                   notify={"email": bool(notify_email)}, handover=dict(handover), state="granted")
    _audit(granted, "granted", reason="user", decision_id=handover["decision_id"])
    _save(task_path, granted)
    return granted


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
    return envelope is not None and tool not in ENGINE_TOOLS and (
        any(e["tool"] == tool for e in envelope["edits"] + envelope["runs"]) or any(
        tool in (g["tool"], *[p for p, t in GAP_PREPARE.items() if t == g["tool"]]) for g in envelope.get("gaps", [])))


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


def _still_gap(ctx, row):
    if row.get("component_class") == "Settings":
        return row.get("old") in (None, "")
    return row.get("component_class") == "Load" and not _has_series(ctx, row.get("name"))


def _same_source(args, record):
    params = args.get("params")
    return args.get("archetype") == record["archetype"] and args.get("year") == record["year"] \
        and isinstance(params, dict) and set(params) == set(record["params"]) and all(
            _number(params[k]) and math.isclose(params[k], v, rel_tol=1e-9) for k, v in record["params"].items())


def _match_prepare(envelope, tool, args, ctx, note):
    entries = [g for g in envelope.get("gaps", []) if g["tool"] == GAP_PREPARE[tool]]
    if not entries:
        return "unlisted_tool"
    names = args.get("loads") if isinstance(args, dict) else None
    if not isinstance(names, list) or not names or not set(args) <= _PREPARE_ARGS \
            or args.get("replay_refinements") not in (None, False):
        return "outside_entry"
    records = []
    for name in names:
        record = next((g["at_grant"][str(name)] for g in entries if str(name) in g["at_grant"]), None)
        if record is None:
            return "new_instance"
        if _has_series(ctx, name):
            return "not_a_gap"
        if not _same_load(ctx, name, record):
            return "new_instance"
        if not _same_source(args, record):
            return "not_granted_source"
        records.append(record)
    note["source"] = source_digest(records[0]) if len(records) == 1 else source_digest(records)
    note["prepare"] = {"loads": [str(n) for n in names], "source": note["source"]}
    return None


def _match_fill(envelope, tool, args, edit_card, ctx, note):
    entries = [g for g in envelope.get("gaps", []) if g["tool"] == tool]
    if not entries:
        return "unlisted_tool"
    if not edit_card or not edit_card.get("complete"):
        return "unresolved_target"
    rows = edit_card.get("rows") or []
    typed = [r for r in rows if r.get("role") == "typed"]
    if not typed:
        return "unresolved_target"
    if any(r.get("field") == "exists" for r in rows):
        return "outside_entry"
    records = []
    for row in typed:
        attr = str(row.get("field")).split(".")[0]
        matched = [g for g in entries if g["target"]["component"] == row.get("component_class")
                   and g["target"]["attr"] in (attr, row.get("field"))
                   and fnmatch.fnmatchcase(str(row.get("name")), g["target"]["name"])]
        if not matched:
            return "outside_entry"
        granted = [g["at_grant"][str(row["name"])] for g in matched if str(row["name"]) in g["at_grant"]]
        if not granted:
            return "new_instance"
        if not _still_gap(ctx, row):
            return "not_a_gap"
        if any("identity" in g and not _same_load(ctx, row["name"], g) for g in granted):
            return "new_instance"  # each row against its own grant record
        records += granted
    if tool == "set_weather_choice":
        pick = {"source": args.get("source"), "year": args.get("year")}
        if not all(pick in r["choices"] for r in records):
            return "not_granted_source"
        note["source"] = source_digest(pick)
    elif tool == "attach_tariff":
        if args.get("replace_inline"):
            return "outside_entry"  # a fill never replaces a tariff
        # Exactly one granted tariff (GK B): with several, the choice is the user's, on a card.
        digest = _tariff_hash(args)
        if not all(len(r["tariffs"]) == 1 and digest in r["tariffs"] for r in records):
            return "not_granted_source"
        note["source"] = digest
    else:
        # The prepare binding (GK B condition 4): only a pending profile this
        # run's own round prepared from the granted source, for this load.
        if (args.get("component_type") or "Load") != "Load" or (args.get("attribute") or "p_set") != "p_set" \
                or args.get("point_id") not in (None, args.get("component")) \
                or args.get("time_basis") not in (None, "UTC"):
            return "outside_entry"  # another load's column, or a shifted series: not the granted source
        job = next((j for j in envelope.get("prepared", []) if j["job_id"] == args.get("job_id")), None)
        loop_round = (note.get("scope") or {}).get("round")
        if job is None or job["task_id"] != envelope.get("task_id") or job["round"] != loop_round \
                or str(args.get("component")) not in job["loads"] or any(str(r["name"]) not in job["loads"] for r in typed):
            return "unbound_prepare"
        note["source"] = job["source"]
    return None


def _match_gap(envelope, tool, args, edit_card, ctx, note):
    """None when the call fills only gaps a listed entry names, from the
    source fixed at the grant, else the reason. A check that cannot run
    counts as no gap: the card (GK B condition 7)."""
    try:
        if tool in GAP_PREPARE:
            return _match_prepare(envelope, tool, args, ctx, note)
        return _match_fill(envelope, tool, args if isinstance(args, dict) else {}, edit_card, ctx, note)
    except Exception:  # noqa: BLE001 — fail closed
        logger.warning("run envelope: the gap check for %s could not run", tool, exc_info=True)
        return "not_a_gap"


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


def _refusal(session, envelope, task, tool, args, tier, external, edit_card, ctx, note):
    from harness.catalogue import TOOLS
    if external:
        return "external"
    if tool not in {t["name"] for t in TOOLS}:
        return "unknown_tool"
    if tool in NEVER_ADMITTED:
        return "grid_code_publish"
    if tool in ENGINE_TOOLS:
        # A `runs` entry covers the harness's own engine step only (C9, #875): a model turn
        # never starts a loop, its solves and target its own.
        return "engine_only"
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
    if tool in GAP_PREPARE or (tool in GAP_TARGETS and not any(e["tool"] == tool for e in envelope["edits"])):
        return _match_gap(envelope, tool, args, edit_card, ctx, note)
    if not any(e["tool"] == tool for e in envelope["edits"]):
        return "unlisted_tool"
    reason = _match_edit(envelope, tool, edit_card)
    scope = note.get("scope")
    if reason is None and scope is not None:
        # An L1 turn (§6.1) changes only the failed rules' `tunable` attributes.
        tunable = scope.get("tunable") or frozenset()
        if any((r.get("component_class"), r.get("field")) not in tunable
               for r in edit_card["rows"] if r.get("role") == "typed"):
            return "not_tunable"
    return reason


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


def _handover_unbound(envelope, task):
    """A2: a run a hand-over started is admitted only under an envelope that
    carries the host card's decision id and grant digest, both equal to what
    its task's `run.origin` recorded, and the digest the digest of the intake
    rows the run was created with (what the user's card showed); an envelope
    that carries one on any other run is no grant either."""
    from services import run_intake
    origin = (task.get("run") or {}).get("origin")
    bound = envelope.get("handover")
    if origin is None and bound is None:
        return False
    if not isinstance(origin, dict) or not isinstance(bound, dict):
        return True
    pair = (bound.get("decision_id"), bound.get("digest"))
    if not all(pair) or pair != (origin.get("decision_id"), origin.get("digest")):
        return True
    rows = task["run"].get("intake")
    try:
        return not isinstance(rows, list) or run_intake.digest(rows) != pair[1]
    except (KeyError, TypeError, AttributeError):
        return True  # rows that cannot be read bind nothing


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
    if _handover_unbound(envelope, task):
        return "handover_unbound"
    if _now() >= _epoch(envelope["expires_at"]):
        return "expired"
    if budget_spent(task) or (solve and solves_used(envelope) >= envelope["budget"]["solves"]):
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
    record), "unavailable" (it matched but could not be recorded: deny),
    `CHANGE_CAP_KIND` (an L2 round's changes are spent: a typed refusal), or
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
    ctx = assistant_tasks.RUN_CONTEXT.get()
    note = {"scope": getattr(session, "loop_scope", None)}
    reason = _refusal(session, envelope, task, tool, args, tier, external, edit_card, ctx, note)
    guard = getattr(session, "decision_guard", None)
    if reason is None and (guard is None or not guard(tool, args)):
        reason = "recheck"  # the project, owner, ACL or locks moved: re-checked under the locks
    if reason is None and (tool in GAP_PREPARE or "source" in note):
        # GK B condition 1: still a gap, read again under the step's locks.
        reason = _match_gap(envelope, tool, args, edit_card, ctx, note)
    link = {"envelope_id": envelope["id"]}
    scope = note["scope"] or {}
    loop_round = {"loop": scope.get("loop"), "round": scope.get("round")} if scope.get("changes") else {}
    with assistant_tasks._LOCK:
        envelope = _load(path)
        if reason is None and (envelope is None or envelope["state"] != "granted" or envelope["id"] != link["envelope_id"]):
            reason = "revoked"
        if reason is None and tool in RUN_TOOLS and solves_used(envelope) >= envelope["budget"]["solves"]:
            # C4: checked again where it is recorded, under the held locks: two solves
            # admitted at budget - 1 never both pass.
            reason = "exhausted"
        if reason is None and loop_round and tool not in RUN_TOOLS \
                and _changes_in(envelope, loop_round["loop"], loop_round["round"]) >= scope["changes"]:
            reason = CHANGE_CAP_KIND  # C2: counted at admission, under the envelope's lock
        if reason is None:
            recorded = approvals.decided(session, call_id=call_id, tool=tool, tier=tier, outcome="approved",
                                         by="envelope:" + envelope["id"], link={**approvals.links(session, args), **link})
            _audit(envelope, "used" if recorded else "refused", call_id=call_id, tool=tool,
                   reason=None if recorded else "approval_unavailable",
                   **({"source": note["source"]} if "source" in note else {}),
                   **({"prepare": note["prepare"]} if "prepare" in note else {}), **loop_round)
            try:
                _save(path, envelope)
                if tool in RUN_TOOLS:
                    _count_solves(path, envelope)
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
    return CHANGE_CAP_KIND if reason == CHANGE_CAP_KIND else None


def prepared(task_path, call_id, job_id, round_no):
    """Bind the pending profile an admitted `generate_load_profile` call made
    to this run and round (GK B condition 4): `apply_generated_profile` is
    admitted only for a job on this list, for the loads it was prepared for."""
    from services import assistant_tasks
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        if envelope is None or envelope["state"] != "granted" or not isinstance(job_id, str) or not job_id:
            return False
        used = next((a for a in reversed(envelope["audit"]) if a.get("event") == "used"
                     and a.get("call_id") == call_id and a.get("prepare")), None)
        if used is None:
            return False  # not admitted from the envelope: nothing to bind
        envelope.setdefault("prepared", []).append({"job_id": job_id, "task_id": envelope["task_id"],
                                                    "round": round_no, "call_id": call_id, **used["prepare"]})
        _save(task_path, envelope)
    return True


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
        if tool in RUN_TOOLS:
            _count_solves(task_path, envelope)  # a solve refused at pickup is no job


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


def _exact(ctx):
    """The exact input fingerprint (`exact=True`: a sub-precision change
    moves it), or None when it cannot be read."""
    from services import workflow_tools
    try:
        return workflow_tools._network_input_fingerprint(ctx, exact=True) or None
    except Exception:  # noqa: BLE001
        return None


def identity(ctx, exact=True):
    """The project's input identity, read under the caller's locks: the
    chain's fingerprint, the exact fingerprint (unless `exact=False`) and the
    edit counter (`dirty_state.capture`, LC-40b). A fingerprint that cannot
    be read is None."""
    from services import dirty_state
    found = {"fingerprint": _fingerprint(ctx), "exact": _exact(ctx) if exact else None}
    dirty_state.capture(found, ctx)  # no lock=: the caller holds the mutation lock (one int read)
    return found


def _last(envelope):
    """The chain's last link: its fingerprint and, on a link that carries
    one, its edit counter (the grant's own fingerprint carries none)."""
    if envelope["chain"]:
        link = envelope["chain"][-1]
        return link["fingerprint"], link.get("network_revision")
    return envelope["fingerprint"], None


def chain_head(task_path, ctx, read=None):
    """What a runner step starts from while an envelope is live, read under
    the step's locks: (fingerprint, None) on the chain's last link, else
    (fingerprint, "drift" or "fingerprint_unreadable"). (None, None) with
    no live envelope. A link that carries an edit counter must match it too:
    an edit then its undo is the same input on a later revision (issue 22).
    With `read` (a dict), the step's whole input identity and the envelope's
    id are stored on it: what the step's write compares at pickup."""
    from services import assistant_tasks
    if not live(task_path):
        return None, None
    found = identity(ctx, exact=read is not None)
    current = found["fingerprint"]
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
    if envelope is None or envelope["state"] != "granted":
        return None, None
    if read is not None:
        read.update(found, envelope_id=envelope["id"])
    last, revision = _last(envelope)
    if not current:
        return current, "fingerprint_unreadable"
    moved = current != last or (revision is not None and found["network_revision"] != revision)
    return current, "drift" if moved else None


def settle(task_path, call_id, ctx, before, after=None):
    """The freshness chain's next link after a runner change, only when the
    step started from the chain's last link (`before`, read under the step's
    locks): a change made before the step, even one the envelope never saw
    (an uncarded step), stays drift (GK B (8)). The brief's fingerprint
    follows the chain while it was the chain's last link (SC-11).

    `after` is a covered step's receipt (`admitted_write`): the identity
    captured under the write's own locks, never a later read, so an edit
    landing after them is drift on the next step (issue 22). Without it
    (a step outside `ATOMIC_TOOLS`: `run_simulation`, `run_ac_pf_stage`, or a
    card-approved step) the identity is read now: the README's remaining
    limit."""
    from services import assistant_tasks
    after = after if after is not None else identity(ctx, exact=False)
    current = after.get("fingerprint")  # None: no link, so the next admission reads drift
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        if envelope is None or envelope["state"] != "granted" or not current or not before:
            return
        last, revision = _last(envelope)
        if before != last or (current == last and (revision is None or after.get("network_revision") == revision)):
            return
        envelope["chain"].append({"after_change": call_id, "fingerprint": current,
                                  "exact": after.get("exact"), "network_revision": after.get("network_revision")})
        _save(task_path, envelope)
        task = json.loads(Path(task_path).read_text())
        if task.get("fingerprint") == last:
            task["fingerprint"] = current
            assistant_tasks._save(Path(task_path), task)


# ── Issue 22: compare and write in one project lock ─────────────────────────

# The envelope steps whose write runs inside `admitted_write`, each settling
# from its own receipt (FILL-10-6 gives each data-gap tool its own row); the
# step-wise runner and L1 branch on this set. Not covered (the README's
# remaining limit): the envelope-admitted runs `run_simulation` and
# `run_ac_pf_stage` (`RUN_TOOLS`), and any step the user approved on a card,
# which settle from a read after the step.
ATOMIC_TOOLS = frozenset({
    # eh_site@1's edit shapes (its size caps, any attrs: the compare is
    # shape-independent, so a mixed call is guarded too, never refused for it).
    "update_component",
    # The run's queued solve: its input is captured under the held locks.
    "solve_queue_enqueue",
    # Data-gap fills (issue 04). None edits the network, so the pickup expects
    # +0 on the edit counter. A tariff ref is in the solver config the
    # fingerprint hashes; a weather choice is a profile-generator default the
    # solve never reads, so a clean fill of it adds no link.
    "apply_generated_profile",
    "attach_tariff",
    "set_weather_choice",
    # Prepares a load fill: writes the pending profile store only (not an edit,
    # so no link), but its pickup compare refuses drift before that write.
    "generate_load_profile",
})
PICKUP_KIND = "envelope_pickup_refused"  # the literal `admitted_write` refuses with


def settle_own(task_path, call_id, ctx, before, receipts):
    """A covered step's settle: from its own call's receipt only. None (a
    refusal, or a worker past its deadline) adds no link, so the next step
    reads drift (fail closed); another call's receipt never settles it."""
    receipt = next((r for r in list(receipts) if r["call_id"] == call_id), None)
    if receipt is not None:
        settle(task_path, call_id, ctx, before, after=receipt)


def _pickup(session, task_path, ctx, tool, expected, recheck):
    """Under the project's locks, before the handler: None, or why the write
    may not run. Reads the envelope and the task without
    `assistant_tasks._LOCK` (both are written by atomic replace), so the
    order stays mutation_lock -> solver_state_lock and nothing else."""
    from services.chat_tools_schema import tool_edits_network
    if not expected:
        return "fingerprint_unreadable"  # nothing on record to compare: never an unchecked write
    if not recheck():
        return "recheck"
    envelope = _load(task_path)
    if envelope is None or envelope["state"] != "granted" or envelope["id"] != expected.get("envelope_id"):
        return "revoked" if envelope is None or envelope["state"] == "granted" else envelope["state"]
    try:
        task = json.loads(Path(task_path).read_text())
    except (OSError, ValueError):
        return "recheck"
    reason = _standing(session, envelope, task, ctx)
    if reason:
        return reason
    now = identity(ctx)
    if not now["fingerprint"] or not now["exact"] or now["network_revision"] is None \
            or not expected.get("exact") or expected.get("network_revision") is None:
        return "fingerprint_unreadable"
    # Coupled with `harness.loop`'s dispatch seam: it bumps the edit counter
    # once for a network-editing tool after `tool_running` and before this
    # pickup, so the run's own edit is expected here; anything else is drift.
    own = 1 if tool_edits_network(tool) else 0
    if now["exact"] != expected["exact"] or now["network_revision"] != expected["network_revision"] + own:
        return "drift"
    return None


@contextlib.contextmanager
def admitted_write(session, task_path, ctx, *, call_id, tool, expected, recheck, receipts):
    """An envelope-admitted step's write, on the worker thread that runs its
    handler (issue 22): take the project's `mutation_lock` then its
    `solver_state_lock` (today's order; per project, never process-global),
    re-check the run and the envelope, compare the input identity the runner
    read under its own locks (`expected`) with the current one, run the
    handler and capture the after-identity, all before releasing. A refusal
    raises before any write, and its audit row is written after the locks
    are released. The receipt names its call: a caller settles only from
    its own step's receipt."""
    from harness.runner import LOCK_TIMEOUT_SECONDS
    reason = "locked"
    if ctx.mutation_lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        try:
            if ctx.solver_state_lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
                try:
                    try:
                        reason = _pickup(session, task_path, ctx, tool, expected, recheck)
                    except Exception:  # noqa: BLE001 - an unreadable check refuses (fail closed)
                        logger.warning("run envelope: pickup check for %s failed", tool, exc_info=True)
                        reason = "fingerprint_unreadable"
                    if reason is None:
                        from services import solve_capture
                        try:
                            # HH-10 (issue 05): the queued solve captures what was compared.
                            with (solve_capture.requested(ctx) if tool == "solve_queue_enqueue"
                                  else contextlib.nullcontext()):
                                yield
                        finally:
                            receipts.append({"call_id": call_id, **identity(ctx)})
                        return
                finally:
                    ctx.solver_state_lock.release()
        finally:
            ctx.mutation_lock.release()
    refused_before_gate(task_path, call_id, tool, reason)
    _refuse("envelope_pickup_refused", f"The run's envelope did not admit this write at pickup ({reason}); "
                         "the step waits for the user.")


# ── Issue 21 §4: a carded write that answers the run's data request ─────────


def settle_answer(task_path, *, actor_id, project_id, decision, diff, before, after):
    """B1-B4: the write is an answer row of the run's open data request, its
    link appended to the freshness chain, only when `run_answers.answer`
    admits it under the task lock (the chain head, read here, must equal
    `before`). Returns the row (with `complete`), or None: the write stays
    drift and the next step pauses the run as today."""
    from services import assistant_tasks, run_answers
    current = (after or {}).get("fingerprint")
    with assistant_tasks._LOCK:
        envelope = _load(task_path)
        if envelope is None or envelope["state"] != "granted" or not current or current == before:
            return None  # a write that changed no input answers nothing
        task = json.loads(Path(task_path).read_text())
        run = task.get("run") or {}
        ref = (run.get("waiting") or {}).get("ref")
        req = next((r for r in run.get("requests", []) if r["id"] == ref), None)
        head, _ = _last(envelope)
        row, reason = run_answers.answer(task, req, actor_id=actor_id, project_id=project_id, decision=decision,
                                         diff=diff, before=before, head=head)
        if row is None:
            _audit(envelope, "answer_refused", call_id=decision.get("id") if decision else None, reason=reason)
            _save(task_path, envelope)
            return None
        envelope["chain"].append({"after_change": "answer:" + decision["id"], "fingerprint": current,
                                  "exact": after.get("exact"), "network_revision": after.get("network_revision"),
                                  "answers": req["id"]})
        _audit(envelope, "answer", call_id=decision["id"], request=req["id"])
        _save(task_path, envelope)
        complete = run_answers.record(req, row)
        run.setdefault("answers", []).append(row)
        if task.get("fingerprint") == head:
            task["fingerprint"] = current
        if complete:
            run["waiting"] = None  # every item answered: the gate is checked again (resume_task)
        assistant_tasks._save(Path(task_path), task)
    return dict(row, complete=complete)


def _waiting_on_data(owner_id):
    """The run of the open project that waits on a data request from this
    owner: (task path, task id), or None."""
    from services import assistant_tasks
    root, project, owner = assistant_tasks._authority()
    if str(owner) != str(owner_id):
        return None
    for path in sorted((Path(root) / "assistant_tasks").glob("*.json")):
        if path.name.endswith(".envelope.json") or path.name.endswith(".handover.json"):
            continue
        try:
            task = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        run = task.get("run") if isinstance(task, dict) else None
        if isinstance(run, dict) and (run.get("waiting") or {}).get("kind") == "data" \
                and task.get("owner_id") == str(owner_id) and task.get("project_id") == str(project):
            return path, task["task_id"]
    return None


def _inputs(ctx):
    """A copy of what a write can change, for `_changed`: each class's static
    and time-series frames, and the network-wide inputs (snapshots, their
    weightings, the solver config). Read under the caller's locks."""
    n = ctx.network
    frames = {}
    for component in n.components:
        frames[(str(component.name), None)] = component.static.copy()
        for attr in component.dynamic:
            frames[(str(component.name), attr)] = component.dynamic[attr].copy()
    network = json.dumps([n.snapshots.to_series().to_json(date_format="iso"), n.snapshot_weightings.to_json(),
                          n.investment_period_weightings.to_json(),
                          (getattr(ctx, "solver_state", None) or {}).get("solver_config")],
                         sort_keys=True, default=str)
    return network, frames


def _changed(before, after):
    """B2: every `(class, name, attr)` whose value differs between two
    `_inputs` reads (a row or column added or removed counts as changed),
    plus `("network", "", "inputs")` when a network-wide input moved."""
    out = set()
    if before[0] != after[0]:
        out.add(("network", "", "inputs"))

    def differ(old, new):  # aligned on both axes; NaN equals NaN; a missing cell differs
        old, new = old.align(new)
        return ~((old == new) | (old.isna() & new.isna()))

    for key in before[1].keys() | after[1].keys():
        cls, series = key
        old, new = before[1].get(key), after[1].get(key)
        if old is None or new is None:  # a frame that appeared or went: compare with its empty self
            old, new = (new.iloc[0:0], new) if old is None else (old, old.iloc[0:0])
        if series is None:  # static: a row per component, a column per attribute
            # A component added or removed is a change no item names.
            out.update((cls, str(name), "") for name in old.index.symmetric_difference(new.index))
            cells = differ(old, new)
            rows, cols = cells.to_numpy(dtype=bool).nonzero()
            out.update((cls, str(cells.index[r]), str(cells.columns[c])) for r, c in zip(rows, cols))
        elif not old.index.equals(new.index) and len(old) and len(new):
            out.add(("network", "", "inputs"))  # the snapshots moved
        else:  # time series: a column per component
            moved = differ(old, new).any(axis=0)
            out.update((cls, str(name), series) for name in moved.index[moved.to_numpy(dtype=bool)])
    return sorted(out)


@contextlib.contextmanager
def answering(session, call_id, edit_card):
    """Around a card write the user approved in ordinary chat (COP-1): when
    the open project's run waits on a data request, the write's identity
    before and after goes to `settle_answer`. Both are read, and the write
    runs, under the project's `mutation_lock` then `solver_state_lock`, as in
    `admitted_write` (B3), and the diff is what the write changed in the
    network, never the card's rows (B2). Locks that cannot be had leave the
    write an ordinary one: drift. A complete request resumes the run
    (`resume_task` checks the gate again). Runner sessions never answer."""
    from harness.runner import LOCK_TIMEOUT_SECONDS
    from services import assistant_tasks
    from services.pypsa_service import PyPSAService
    found = None
    try:
        if edit_card and not str(getattr(session, "session_id", "") or "").startswith(SESSION_PREFIX):
            found = _waiting_on_data(getattr(session, "owner_user_id", None))
    except Exception:  # noqa: BLE001 — no run to answer: an ordinary write
        found = None
    if found is None:
        yield
        return
    ctx = PyPSAService.get_active_context()
    if not ctx.mutation_lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        yield
        return
    try:
        if not ctx.solver_state_lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            yield
            return
        try:
            try:
                before, inputs = identity(ctx, exact=False), _inputs(ctx)
            except Exception:  # noqa: BLE001 — unreadable: the write stands as drift
                logger.warning("run envelope: the inputs before a card write could not be read", exc_info=True)
                before = None
            yield
            if before is None:
                return
            try:
                after, diff = identity(ctx, exact=False), _changed(inputs, _inputs(ctx))
            except Exception:  # noqa: BLE001
                logger.warning("run envelope: the inputs after a card write could not be read", exc_info=True)
                return
        finally:
            ctx.solver_state_lock.release()
    finally:
        ctx.mutation_lock.release()
    path, task_id = found
    try:
        row = settle_answer(path, actor_id=str(session.owner_user_id), project_id=str(ctx.project_uuid),
                            decision={"id": call_id, "by": "user"}, diff=diff,
                            before=before.get("fingerprint"), after=after)
        if row is not None and row["complete"]:
            assistant_tasks.resume_task(task_id)
    except Exception:  # noqa: BLE001 — the write stands; the run reads it as drift
        logger.warning("run envelope: the answer to the data request could not be settled", exc_info=True)

