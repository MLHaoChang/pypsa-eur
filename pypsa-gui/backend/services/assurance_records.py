"""Owned, bounded assurance records; current-input evidence is not a run store."""

from __future__ import annotations

import hashlib
import json
import math
from numbers import Real
import os
import time
import uuid

from fastapi import HTTPException

import app_paths
from harness.durable import network_guard, transaction
from services import project_registry
from services.pypsa_service import PyPSAService

MAX_RECORDS = 512
RETENTION_SECONDS = 24 * 60 * 60
# Deliberately narrow: these are static configured inputs, not solved outputs.
INPUT_METRICS = {
    "Load": {"p_set": "MW", "q_set": "MVar"},
    "Generator": {"p_nom": "MW"},
    "Line": {"s_nom": "MVA"},
    "Transformer": {"s_nom": "MVA"},
    "StorageUnit": {"p_nom": "MW"},
    "Store": {"e_nom": "MWh"},
}


def digest(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def unavailable(kind):
    raise HTTPException(
        409, {"error_kind": kind, "message": "Checked evidence is unavailable."}
    )


def actor_id():
    from services import chat_tools

    with chat_tools._acting() as (_, user):
        return str(user.id)


def _user(db, owner):
    from db.models import User

    user = db.get(User, uuid.UUID(owner))
    if user is None or user.status != "active":
        raise HTTPException(401, "Authentication required")
    return user


def authorize(owner, project_id=None):
    from db.session import SessionLocal

    with SessionLocal() as db:
        user = _user(db, owner)
        if project_id is not None:
            project_registry.resolve_project(db, user, project_id)


def _path(owner, record_id):
    try:
        if str(uuid.UUID(record_id)) != record_id:
            raise ValueError()
        if str(uuid.UUID(owner)) != owner:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, "Record not found") from None
    return (
        app_paths.app_data_dir() / "assistant_assurance" / owner / (record_id + ".json")
    )


def save(record, record_id):
    """Commit record and checksum together before anything public is emitted."""
    path = _path(record["owner_id"], record_id)
    envelope = {"record": record, "sha256": digest(record)}
    encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > 128 * 1024:
        unavailable("assurance_record_too_large")
    with transaction(path.parent):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists() and record["record_kind"] not in {
            "operation",
            "ui_action",
            "conversation_context",
        }:
            if path.read_bytes() != encoded:
                unavailable("evidence_unavailable")
            return
        # Expired records cannot be replayed; reclaim them before applying the
        # quota so a long-running pilot does not permanently exhaust storage.
        now = time.time()
        for candidate in path.parent.glob("*.json"):
            if candidate.stat().st_mtime + RETENTION_SECONDS <= now:
                try:
                    pending = json.loads(candidate.read_text(encoding="utf-8"))[
                        "record"
                    ]
                    if pending.get("record_kind") == "operation" and pending.get(
                        "outcome"
                    ) in {"reserved", "running", "uncertain"}:
                        continue  # Never erase the reservation for a possible late effect.
                except (ValueError, KeyError, TypeError, OSError):
                    # A corrupt reservation cannot safely be treated as unused.
                    continue
                candidate.unlink(missing_ok=True)
        if not path.exists() and len(list(path.parent.glob("*.json"))) >= MAX_RECORDS:
            unavailable("assurance_storage_full")
        temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("xb") as stream:
                os.chmod(temp, 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            # The rename must survive restart as well as the file contents.
            if os.name != "nt":
                fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            temp.unlink(missing_ok=True)


def load(owner, record_id, kind):
    path = _path(owner, record_id)
    authorize(owner)
    try:
        if path.stat().st_size > 128 * 1024:
            raise ValueError()
        envelope = json.loads(path.read_text(encoding="utf-8"))
        record = envelope["record"]
        if digest(record) != envelope["sha256"]:
            raise ValueError()
        if record["owner_id"] != owner or record["record_kind"] != kind:
            raise HTTPException(404, "Record not found")
        if record["expires_at"] <= time.time():
            raise ValueError()
    except FileNotFoundError:
        raise HTTPException(404, "Record not found") from None
    except (OSError, ValueError, KeyError, TypeError):
        unavailable(
            "answer_evidence_unavailable"
            if kind == "answer"
            else "evidence_unavailable"
        )
    authorize(owner, record.get("project_id"))
    return record


def _raw_facts(ctx, component_class, name):
    """Read the original values under the network lock, before UI sanitization."""
    component = ctx.network.components[component_class]
    if name not in component.static.index:
        unavailable("evidence_unavailable")
    facts = []
    for metric, unit in INPUT_METRICS[component_class].items():
        raw = component.static.at[name, metric]
        # Missing data does not become zero or a fact.
        if isinstance(raw, bool) or not isinstance(raw, Real):
            continue
        try:
            value = float(raw)
        except (ValueError, TypeError, OverflowError):
            continue
        if math.isfinite(value):
            facts.append(
                {
                    "path": "/" + metric,
                    "entity_id": name,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                }
            )
    return facts


def capture_input(owner, args, result):
    ctx = PyPSAService.get_active_context()
    component_class, name = args.get("component_class"), args.get("name")
    if (
        component_class not in INPUT_METRICS
        or not isinstance(result, dict)
        or not ctx.project_uuid
    ):
        return None
    authorize(owner, ctx.project_uuid)
    with network_guard():
        if ctx.solver_state.get("status") == "running":
            return None
        facts = _raw_facts(ctx, component_class, name)
        # A raced or incorrect handler response cannot certify itself.
        facts = [
            f
            for f in facts
            if type(result.get(f["metric"])) in (int, float)
            and result[f["metric"]] == f["value"]
        ]
        if not facts:
            return None
        record = {
            "version": 1,
            "record_kind": "evidence",
            "evidence_id": str(uuid.uuid4()),
            "owner_id": owner,
            "project_id": ctx.project_uuid,
            "org_id": ctx.org_id,
            "source_kind": "configured_input",
            "adapter_version": "static-input/1",
            "scope": {
                "component_class": component_class,
                "entity_id": name,
                "kind": "static",
            },
            "facts": facts,
            "source_sha256": digest(_raw_facts(ctx, component_class, name)),
            "validity": {
                "outcome": "completed",
                "as_of": time.time(),
                "coverage": "static_input",
                "warnings": [
                    "Static configured input; time-series overrides are not checked."
                ],
            },
            "expires_at": time.time() + RETENTION_SECONDS,
        }
        save(record, record["evidence_id"])
    return record


def evidence(owner, evidence_id):
    record = load(owner, evidence_id, "evidence")
    if record["source_kind"] == "scoped_result":
        from harness import calculations

        raw = record["raw"]
        scope = record["request_scope"]
        if not scope.get("retain"):
            ctx = PyPSAService.get_active_context()
            if ctx.project_uuid != record["project_id"]:
                unavailable("evidence_unavailable")
            try:
                with network_guard():
                    raw = calculations.scoped_source(ctx, scope, owner)
            except (ValueError, KeyError, TypeError):
                unavailable("evidence_unavailable")
        try:
            facts, slots = calculations.scoped_calculate(raw, scope)
            if (
                digest(raw) != record["source_sha256"]
                or facts != record["facts"]
                or slots != record["slots"]
            ):
                unavailable("evidence_unavailable")
        except (ValueError, KeyError, TypeError):
            unavailable("evidence_unavailable")
        return record
    # This tranche admits current inputs only. It does not substitute a cold,
    # saved network for changed foreground inputs or claim retained-run validity.
    ctx = PyPSAService.get_active_context()
    if ctx.project_uuid != record["project_id"]:
        unavailable("evidence_unavailable")
    try:
        with network_guard():
            scope = record["scope"]
            if record["source_kind"] == "calculated_result":
                from services import assurance_metrics

                raw = assurance_metrics.source(ctx, scope["entity_id"])
                if digest(raw) != record["source_sha256"]:
                    unavailable("evidence_unavailable")
                facts, slots = assurance_metrics.calculate(raw, scope["entity_id"])
                if facts != record["facts"] or slots != record["slots"]:
                    unavailable("evidence_unavailable")
                return record
            if ctx.solver_state.get("status") == "running":
                unavailable("evidence_unavailable")
            if (
                digest(_raw_facts(ctx, scope["component_class"], scope["entity_id"]))
                != record["source_sha256"]
            ):
                unavailable("evidence_unavailable")
    except (KeyError, ValueError, TypeError):
        unavailable("evidence_unavailable")
    return record


def capture_dispatch(owner, entity):
    from services import assurance_metrics

    ctx = PyPSAService.get_active_context()
    authorize(owner, ctx.project_uuid)
    with network_guard():
        raw = assurance_metrics.source(ctx, entity)
        facts, slots = assurance_metrics.calculate(raw, entity)
        record = {
            "version": 1,
            "record_kind": "evidence",
            "evidence_id": str(uuid.uuid4()),
            "owner_id": owner,
            "project_id": ctx.project_uuid,
            "org_id": ctx.org_id,
            "source_kind": "calculated_result",
            "adapter_version": assurance_metrics.PROFILE,
            "scope": {
                "component_class": "Generator",
                "entity_id": entity,
                "kind": "full_horizon",
                "start": raw["index"][0],
                "end": raw["index"][-1],
            },
            "facts": facts,
            "slots": slots,
            "raw": raw,
            "source_sha256": digest(raw),
            "validity": {
                "outcome": "completed"
                if all(s["disposition"] == "answered" for s in slots)
                else "partial",
                "coverage": "full_horizon",
                "as_of": time.time(),
                "warnings": [assurance_metrics.DISCLOSURE],
            },
            "expires_at": time.time() + RETENTION_SECONDS,
        }
        save(record, record["evidence_id"])
        return record


def capture_scoped(owner, scope):
    from harness import calculations

    ctx = PyPSAService.get_active_context()
    if ctx.project_uuid != scope["project_id"]:
        raise ValueError("wrong_project")
    authorize(owner, ctx.project_uuid)
    if scope.get("evidence_id"):
        record = evidence(owner, scope["evidence_id"])
        if record["source_kind"] != "scoped_result" or not record["request_scope"].get(
            "retain"
        ):
            raise ValueError("not_retained_run")
        expected = {k: v for k, v in scope.items() if k != "evidence_id"}
        if expected != record["request_scope"]:
            raise ValueError("historical_scope_mismatch")
        return record
    with network_guard():
        raw = calculations.scoped_source(ctx, scope, owner)
        facts, slots = calculations.scoped_calculate(raw, scope)
        first = (raw["storage_unit"] if scope["profile"] in calculations.STORAGE_PROFILES
                 else raw if scope["profile"] in (calculations.carrier.PROFILE, *calculations.branch.PROFILES,
                                                  calculations.lost_load.PROFILE, calculations.price.PROFILE,
                                                  *calculations.emissions.PROFILES,
                                                  calculations.curtailment.PROFILE,
                                                  calculations.capacity.PROFILE, calculations.cycles.PROFILE,
                                                  calculations.balance.PROFILE, calculations.site_import.PROFILE)
                 else next(iter(raw["generators"].values())))
        record = {
            "version": 1,
            "record_kind": "evidence",
            "evidence_id": str(uuid.uuid4()),
            "owner_id": owner,
            "project_id": ctx.project_uuid,
            "org_id": ctx.org_id,
            "source_kind": "scoped_result",
            "adapter_version": scope["profile"],
            "request_scope": scope,
            "scope": {
                "entity_id": scope["entity_id"],
                "kind": "retained_run" if scope.get("retain") else "current_run",
                "start": scope.get("start", first["index"][0]),
                "end": scope.get("end", first["index"][-1]),
            },
            "facts": facts,
            "slots": slots,
            "raw": raw,
            "source_sha256": digest(raw),
            "validity": {
                "coverage": "requested_population",
                "as_of": time.time(),
                "warnings": [calculations.disclosure(scope)],
            },
            "expires_at": time.time() + RETENTION_SECONDS,
        }
        save(record, record["evidence_id"])
        return record


def public(record):
    return {
        k: v
        for k, v in record.items()
        if k
        not in {
            "owner_id",
            "org_id",
            "record_kind",
            "expires_at",
            "source_sha256",
            "reservation_nonce",
            "worker_pid",
            "worker_host",
        }
    }


def answer(owner, answer_id):
    record = load(owner, answer_id, "answer")
    if (
        hashlib.sha256(record["text"].encode()).hexdigest()
        != record["receipt"]["content_sha256"]
    ):
        unavailable("answer_evidence_unavailable")
    for evidence_id in record["receipt"]["evidence_ids"]:
        try:
            evidence(owner, evidence_id)
        except HTTPException:
            unavailable("answer_evidence_unavailable")
    for oid in record["receipt"].get("operation_ids", []):
        from harness.effects import operation

        try:
            if operation(owner, oid)["outcome"] != "completed":
                unavailable("answer_evidence_unavailable")
        except HTTPException:
            unavailable("answer_evidence_unavailable")
    return record


def history_projection(turns, owner, *, strict=False):
    """Every approved replay is revalidated; imported flags cannot create trust."""
    projected = []
    for turn in turns:
        if turn.get("answer_id"):
            try:
                checked = answer(owner, turn["answer_id"])
                projected.append(
                    {
                        **turn,
                        "requires_checked": True,
                        "assistant": [{"type": "text", "text": checked["text"]}],
                        "receipt": checked["receipt"],
                    }
                )
            except HTTPException:
                projected.append(
                    {
                        **{
                            k: v
                            for k, v in turn.items()
                            if k not in {"assistant", "receipt", "answer_id"}
                        },
                        # An expired/unavailable answer still cannot silently
                        # downgrade its retry. This is policy, never evidence.
                        "requires_checked": True,
                        "assistant": [
                            {
                                "type": "text",
                                "text": "Previous checked answer is unavailable.",
                            }
                        ],
                    }
                )
        elif strict:
            projected.append(
                {
                    **turn,
                    "assistant": [
                        {"type": "text", "text": "Legacy answer: not validated."}
                    ],
                }
            )
        else:
            projected.append(turn)
    return projected
