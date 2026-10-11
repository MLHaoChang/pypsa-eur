"""Bounded server-owned conversational scope; language never authorizes effects."""

from copy import deepcopy
import re
import time
import uuid

from fastapi import HTTPException

from harness import assurance_scope, calculations
from harness.durable import network_guard
from services import assurance_records as records
from services.pypsa_service import PyPSAService

REPEAT = r"(?:show|give me)\s+(?:those|these|the same)\s+results\s+again"
COMPARE = (
    r"compare\s+(?:that|those results|these results)\s+with\s+the\s+previous\s+run"
)
BASIS = r"(?:use\s+)?price year\s+(\d{4});\s*annual fixed cost fraction\s+(0(?:\.\d+)?|1(?:\.0+)?)"
CHOICES = ("Marginal dispatch expenditure", "Total declared-basis cost")
QUESTIONS = {
    "cost_quantity_required": "Which quantity do you mean: Marginal dispatch expenditure or Total declared-basis cost?",
    "explicit_cost_basis_required": "Please provide the EUR price year and annual fixed cost fraction. Reply: Price year YYYY; annual fixed cost fraction F (between 0 and 1).",
    "conversation_scope_required": "Please specify the generator or network and the results to check; there is no available checked scope in this chat.",
    "conversation_scope_changed": "The project, browser binding or selected component changed. Please specify the generator or network and results again.",
    "previous_compatible_run_required": "No previous distinct checked run has the same entity, time window and cost basis. Check a compatible baseline first or provide an explicit retained comparison scope.",
    "baseline_unavailable": "The selected previous run is unavailable or could not be verified. Please check a compatible baseline again.",
    "unsupported_followup": "That follow-up contains constraints outside the supported scope grammar. Please specify an exact result scope; no constraint has been ignored.",
    "current_run_required": "This selection refers to a retained historical run. Specify the candidate and baseline explicitly to compare historical runs.",
}


def _match(pattern, message):
    return re.fullmatch(pattern + r"[.!?]?", message.strip(), re.I)


def _binding(turn):
    return (turn.owner, turn.project_id, getattr(turn, "binding_generation", 0))


def _state(turn, ui):
    with turn.session._lock:
        state = deepcopy(turn.session.assurance_context)
        assistants = [m for m in turn.session.messages if m.get("role") == "assistant"]
    if (
        not state
        or state["expires_at"] <= time.time()
        or not assistants
        or assistants[-1].get("_approved_answer_id") != state["anchor"]
    ):
        return None, "conversation_scope_required"
    selected = (ui or {}).get("selected_component")
    scope = state["scope"]
    conflict = (
        scope["profile"] != "network-linear-cost/1"
        and isinstance(selected, dict)
        and (
            selected.get("class") != (
                "StorageUnit" if scope["profile"] == "storage-unit-dispatch/1" else "Generator"
            )
            or selected.get("name") != scope["entity_id"]
        )
    )
    resumed = state.get("resumed") and tuple(state["binding"]) == (
        turn.owner,
        turn.project_id,
        None,
    )
    if (tuple(state["binding"]) != _binding(turn) and not resumed) or conflict:
        with turn.session._lock:
            turn.session.assurance_context = None
        return None, "conversation_scope_changed"
    # Fresh membership/account checks on every lookup; UI selections confer no ACL.
    records.authorize(turn.owner, turn.project_id)
    if resumed:
        state["binding"] = _binding(turn)
        state.pop("resumed", None)
        with turn.session._lock:
            turn.session.assurance_context = deepcopy(state)
    return state, None


def _needs(turn, scope, reason):
    turn.clarification = {"reason": reason, "question": QUESTIONS[reason]}
    turn.pending_scope = (
        deepcopy(scope)
        if reason in {"cost_quantity_required", "explicit_cost_basis_required"}
        else None
    )
    return scope or {
        "version": 1,
        "profile": calculations.PROFILE,
        "project_id": turn.project_id or "unbound",
        "entity_id": "unselected",
        "retain": False,
    }


def _basis(match):
    return assurance_scope.CostBasis.model_validate(
        {
            "currency": "EUR",
            "price_year": int(match[1]),
            "annual_fixed_cost_fraction": float(match[2]),
        }
    ).model_dump()


def _compatible(scope):
    return {
        k: scope.get(k)
        for k in ("profile", "project_id", "entity_id", "start", "end", "cost_basis")
    }


def resolve(turn, message, ui, explicit):
    """Resolve only whole-message forms. Unknown suffixes never lose constraints."""
    if explicit is not None:
        turn.conversation_state, _ = _state(turn, None)
        return explicit
    state, state_reason = _state(turn, ui)
    turn.conversation_state = state
    pending = state.get("pending") if state else None
    if pending and message.strip().rstrip(".!?").lower() in {
        c.lower() for c in CHOICES
    }:
        scope = deepcopy(state["scope"])
        if pending != "cost_quantity_required":
            return _needs(turn, scope, "unsupported_followup")
        marginal = message.strip().lower().startswith("marginal")
        scope["profile"] = (
            (
                "network-linear-cost/1"
                if scope["entity_id"] == "network"
                else calculations.PROFILE
            )
            if marginal
            else (
                "network-linear-cost/1"
                if scope["entity_id"] == "network"
                else "generator-cost/1"
            )
        )
        scope["metrics"] = ["marginal_expenditure" if marginal else "total_cost"]
        if marginal and scope["profile"] == calculations.PROFILE:
            scope.pop("cost_basis", None)
        elif "cost_basis" not in scope:
            return _needs(turn, scope, "explicit_cost_basis_required")
        return assurance_scope.validate(scope)
    basis = _match(BASIS, message)
    if basis:
        if not state:
            return _needs(turn, None, state_reason)
        if (
            pending not in {None, "explicit_cost_basis_required"}
            or state["scope"]["profile"] not in calculations.COST_PROFILES
        ):
            return _needs(turn, state["scope"], "unsupported_followup")
        scope = deepcopy(state["scope"])
        # Changing a historical basis must never silently turn it into a current run.
        if scope.get("evidence_id"):
            return _needs(turn, scope, "current_run_required")
        try:
            scope["cost_basis"] = _basis(basis)
            if scope.get("comparison_evidence_id"):
                return _needs(turn, scope, "previous_compatible_run_required")
        except ValueError:
            return _needs(turn, scope, "explicit_cost_basis_required")
        return assurance_scope.validate(scope)
    ambiguous = _match(
        r"(?:please\s+)?(?:show me|give me|show)\s+(?:the\s+)?cost\s+for\s+(generator\s+(.{1,128}?)|network)(?:;\s*from\s+(\S+)\s+to\s+(\S+))?",
        message,
    )
    if ambiguous:
        scope = {
            "version": 1,
            "project_id": turn.project_id,
            "retain": False,
            "profile": "generator-cost/1" if ambiguous[2] else "network-linear-cost/1",
            "entity_id": ambiguous[2] or "network",
            "metrics": ["total_cost"],
        }
        if ambiguous[3]:
            scope.update(start=ambiguous[3], end=ambiguous[4])
        return _needs(turn, assurance_scope.validate(scope), "cost_quantity_required")
    try:
        scope = assurance_scope.from_message(message, turn.project_id, ui)
    except ValueError:
        return _needs(turn, None, "unsupported_followup")
    if scope:
        if scope["profile"] in calculations.COST_PROFILES and not scope.get("cost_basis"):
            return _needs(turn, scope, "explicit_cost_basis_required")
        return scope
    repeat, compare = _match(REPEAT, message), _match(COMPARE, message)
    window = _match(
        r"(?:show|give me)\s+(?:those|these|the same)\s+results\s+from\s+(\S+)\s+to\s+(\S+)",
        message,
    )
    if repeat or compare or window:
        if not state:
            return _needs(turn, None, state_reason)
        if pending:
            return _needs(turn, state["scope"], pending)
        scope = deepcopy(state["scope"])
        if repeat:
            return scope
        if scope.get("evidence_id"):
            return _needs(turn, scope, "current_run_required")
        if window:
            if scope.get("comparison_evidence_id"):
                return _needs(turn, scope, "previous_compatible_run_required")
            scope.update(start=window[1], end=window[2])
            return assurance_scope.validate(scope)
        metrics = list(calculations.requested_metrics(scope))
        scope["metrics"] = list(
            dict.fromkeys([*metrics, "cost_delta", "cost_change_percent"])
        )
        if scope["profile"] == calculations.PROFILE or "total_cost" not in metrics:
            return _needs(turn, scope, "previous_compatible_run_required")
        # Compare the current candidate, not a recursively embedded comparison.
        scope.pop("comparison_evidence_id", None)
        current_scope = {
            **scope,
            "metrics": [
                m for m in scope["metrics"] if m not in calculations.COMPARISON_METRICS
            ],
        }
        entries = state["entries"]
        try:
            with network_guard():
                current = calculations.scoped_source(
                    PyPSAService.get_active_context(), current_scope, turn.owner
                )
            key = records.digest(current)
            index = -2 if entries and entries[-1]["run_key"] == key else -1
            if len(entries) < abs(index) or _compatible(
                entries[index]["scope"]
            ) != _compatible(scope):
                return _needs(turn, scope, "previous_compatible_run_required")
            baseline = records.evidence(turn.owner, entries[index]["evidence_id"])
            if baseline["source_sha256"] != entries[index]["run_key"]:
                raise ValueError("changed_baseline")
        except (ValueError, KeyError, TypeError, HTTPException):
            return _needs(turn, scope, "baseline_unavailable")
        scope["comparison_evidence_id"] = baseline["evidence_id"]
        scope["metrics"] = list(
            dict.fromkeys(
                [
                    *calculations.requested_metrics(scope),
                    "cost_delta",
                    "cost_change_percent",
                ]
            )
        )
        return assurance_scope.validate(scope)
    if re.match(r"(?:use\s+)?price year\b", message.strip(), re.I):
        return _needs(turn, state["scope"] if state else None, "unsupported_followup")
    if re.match(
        r"(?:compare\s+(?:that|those|these)|(?:show|give me)\s+(?:those|these|the same)\s+results)",
        message.strip(),
        re.I,
    ):
        return _needs(turn, state["scope"] if state else None, "unsupported_followup")
    return None


def remember(turn):
    """Called only at final publication. At most two distinct immutable run refs."""
    record = turn.approved
    if turn.clarification:
        if turn.pending_scope is None:
            turn.session.assurance_context = None
            return
        scope = deepcopy(turn.pending_scope)
        entries = (turn.conversation_state or {}).get("entries", [])
        pending = turn.clarification["reason"]
        expires = min(
            (turn.conversation_state or {}).get("expires_at", record["expires_at"]),
            record["expires_at"],
        )
    elif (
        record["receipt"].get("request_complete")
        and turn.request_evidence
        and record["blocks"]
    ):
        evidence = records.evidence(turn.owner, turn.request_evidence["evidence_id"])
        scope = {
            k: v
            for k, v in (turn.request_scope or {}).items()
            if k in assurance_scope.RequestScope.model_fields
        }
        scope.update(
            version=1,
            retain=True,
            start=evidence["scope"]["start"],
            end=evidence["scope"]["end"],
        )
        scope.pop("evidence_id", None)
        scope.pop("comparison_evidence_id", None)
        if scope.get("metrics"):
            scope["metrics"] = [
                m for m in scope["metrics"] if m not in calculations.COMPARISON_METRICS
            ]
        scope = assurance_scope.validate(scope)
        raw = deepcopy(evidence["raw"])
        if evidence["source_kind"] == "calculated_result":
            raw = {
                "generators": {scope["entity_id"]: raw},
                "passive_costs": [],
                "unsupported_cost_components": [],
            }
        raw.pop("comparison", None)
        facts, slots = calculations.scoped_calculate(raw, scope)
        if not slots or not all(s["disposition"] == "answered" for s in slots):
            raise ValueError("unavailable_conversation_snapshot")
        frozen = {
            **evidence,
            "evidence_id": str(uuid.uuid4()),
            "source_kind": "scoped_result",
            "request_scope": scope,
            "scope": {**evidence["scope"], "kind": "retained_run"},
            "facts": facts,
            "slots": slots,
            "raw": raw,
            "source_sha256": records.digest(raw),
        }
        records.save(frozen, frozen["evidence_id"])
        entry = {
            "scope": deepcopy(scope),
            "evidence_id": frozen["evidence_id"],
            "run_key": frozen["source_sha256"],
        }
        old = turn.conversation_state
        entries = (
            deepcopy(old["entries"])
            if old and tuple(old["binding"]) == _binding(turn)
            else []
        )
        if entries and _compatible(entries[-1]["scope"]) != _compatible(scope):
            entries = []
        if entries and entries[-1]["run_key"] == entry["run_key"]:
            entries[-1] = entry
        else:
            entries.append(entry)
        entries = entries[-2:]
        if turn.request_scope.get("retain"):
            scope = {
                **evidence["request_scope"],
                "evidence_id": evidence["evidence_id"],
            }
        else:
            scope = {
                k: deepcopy(v)
                for k, v in turn.request_scope.items()
                if k in assurance_scope.RequestScope.model_fields
            }
            scope.update(
                version=1,
                retain=False,
                start=evidence["scope"]["start"],
                end=evidence["scope"]["end"],
            )
            scope = assurance_scope.validate(scope)
        pending = None
        expires = frozen["expires_at"]
    else:
        turn.session.assurance_context = None
        return
    turn.session.assurance_context = {
        "version": 1,
        "binding": _binding(turn),
        "scope": scope,
        "entries": entries,
        "pending": pending,
        "anchor": record["answer_id"],
        "expires_at": expires,
    }
