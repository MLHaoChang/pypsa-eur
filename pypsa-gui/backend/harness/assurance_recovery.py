"""Private owned checkpoints; only native approved history can resume scope."""

from copy import deepcopy
import os
import time
from typing import Literal
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from harness import assurance_scope
from services import assurance_records as records

KIND = "conversation_context"


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope: assurance_scope.RequestScope
    evidence_id: str = Field(min_length=36, max_length=36)
    run_key: str = Field(pattern=r"^[0-9a-f]{64}$")


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: StrictInt
    scope: assurance_scope.RequestScope
    entries: list[Reference] = Field(max_length=2)
    pending: Literal["cost_quantity_required", "explicit_cost_basis_required"] | None
    anchor: str = Field(min_length=36, max_length=36)
    expires_at: float = Field(allow_inf_nan=False)


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: StrictInt
    record_kind: Literal["conversation_context"]
    checkpoint_id: str
    owner_id: str
    project_id: str
    session_id: str
    state: Context | None
    expires_at: float = Field(allow_inf_nan=False)


def checkpoint_id(owner, project, session_id):
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL, f"assurance-context/1/{owner}/{project}/{session_id}"
        )
    )


def _write(session, owner, project, state):
    # Revocation only removes authority, including after membership is lost.
    records.authorize(owner, project if state else None)
    identifier = checkpoint_id(owner, project, session.session_id)
    saved = deepcopy(state)
    expires = time.time() + records.RETENTION_SECONDS
    if saved:
        saved.pop("binding", None)
        saved.pop("resumed", None)
        expires = min(expires, saved["expires_at"])
        saved["expires_at"] = expires
    record = Checkpoint.model_validate(
        {
            "version": 1,
            "record_kind": KIND,
            "checkpoint_id": identifier,
            "owner_id": owner,
            "project_id": project,
            "session_id": session.session_id,
            "state": saved,
            "expires_at": expires,
        }
    ).model_dump(exclude_none=False)
    # Preserve the scope's canonical optional-field representation.
    if saved:
        record["state"] = saved
    records.save(record, identifier)
    session.assurance_checkpoint_project = project


def suspend(turn):
    """Under network/session locks, before preparation, WAL or execution."""
    projects = dict.fromkeys(
        (turn.session.assurance_checkpoint_project, turn.project_id)
    )
    for project in projects:
        if project is None:
            continue
        identifier = checkpoint_id(turn.owner, project, turn.session.session_id)
        if records._path(turn.owner, identifier).exists():
            _write(turn.session, turn.owner, project, None)


def persist(turn):
    state = turn.session.assurance_context
    identifier = checkpoint_id(turn.owner, turn.project_id, turn.session.session_id)
    if state or records._path(turn.owner, identifier).exists():
        _write(turn.session, turn.owner, turn.project_id, state)


def invalidate(session):
    """Rewind calls this before changing messages, while holding its lock."""
    if session.assurance_checkpoint_project and session.owner_user_id:
        try:
            _write(
                session,
                session.owner_user_id,
                session.assurance_checkpoint_project,
                None,
            )
        except (OSError, ValueError):
            raise HTTPException(
                409, {"error_kind": "assurance_context_persistence_failed"}
            ) from None


def candidate(session, ctx, last_record):
    """Validate outside the session lock. This never reads transcript prose."""
    from harness.session import TURN_AUTHOR_KEY

    if os.environ.get("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled") not in {
        "pilot",
        "strict",
    }:
        return None
    owner, project = session.owner_user_id, ctx.project_uuid
    if (
        not owner
        or not project
        or last_record.get(TURN_AUTHOR_KEY) != owner
        or last_record.get("session_id") != session.session_id
        or not last_record.get("answer_id")
    ):
        return None
    try:
        identifier = checkpoint_id(owner, project, session.session_id)
        raw = records.load(owner, identifier, KIND)
        checkpoint = Checkpoint.model_validate(raw)
        state = checkpoint.state
        if (
            checkpoint.version != 1
            or checkpoint.checkpoint_id != identifier
            or checkpoint.session_id != session.session_id
            or checkpoint.project_id != project
            or state is None
            or state.version != 1
            or state.expires_at > checkpoint.expires_at
            or state.expires_at <= time.time()
            or state.anchor != last_record["answer_id"]
        ):
            return None
        scope = assurance_scope.validate(state.scope.model_dump(exclude_none=True))
        if scope["project_id"] != project or len(
            {e.run_key for e in state.entries}
        ) != len(state.entries):
            return None
        anchor = records.answer(owner, state.anchor)
        if (
            anchor["project_id"] != project
            or anchor["answer_id"] != state.anchor
            or state.expires_at > anchor["expires_at"]
        ):
            return None
        receipt = anchor["receipt"]
        expected = {
            k: v
            for k, v in receipt["request_scope"].items()
            if k in assurance_scope.RequestScope.model_fields
        }
        # The original dispatch receipt predates the explicit version field.
        expected.setdefault("version", 1)
        if state.pending:
            if (
                receipt["request_complete"] is not False
                or receipt["checked_block_ids"]
                or receipt["evidence_ids"]
                or not receipt["result_slots"]
                or any(
                    s["disposition"] != "needs_clarification"
                    or s["reason"] != state.pending
                    for s in receipt["result_slots"]
                )
            ):
                return None
        else:
            if (
                not receipt["request_complete"]
                or not anchor["blocks"]
                or not state.entries
            ):
                return None
            if len(receipt["evidence_ids"]) != 1:
                return None
            source = records.evidence(owner, receipt["evidence_ids"][0])
            if source["adapter_version"] != scope["profile"]:
                return None
            if expected.get("retain"):
                expected = {
                    **source["request_scope"],
                    "evidence_id": source["evidence_id"],
                }
            else:
                expected.update(
                    start=source["scope"]["start"], end=source["scope"]["end"]
                )
        if assurance_scope.validate(expected) != scope:
            return None
        entries = []
        for entry in state.entries:
            entry_scope = assurance_scope.validate(
                entry.scope.model_dump(exclude_none=True)
            )
            evidence = records.evidence(owner, entry.evidence_id)
            if (
                entry_scope["project_id"] != project
                or not entry_scope["retain"]
                or entry_scope.get("evidence_id")
                or entry_scope.get("comparison_evidence_id")
                or evidence["source_kind"] != "scoped_result"
                or evidence["request_scope"] != entry_scope
                or evidence["adapter_version"] != entry_scope["profile"]
                or evidence["source_sha256"] != entry.run_key
                or evidence["expires_at"] < state.expires_at
                and entry == state.entries[-1]
            ):
                return None
            entries.append(
                {
                    "scope": entry_scope,
                    "evidence_id": entry.evidence_id,
                    "run_key": entry.run_key,
                }
            )
        return {
            "version": 1,
            "binding": (owner, project, None),
            "scope": scope,
            "entries": entries,
            "pending": state.pending,
            "anchor": state.anchor,
            "expires_at": state.expires_at,
            "resumed": True,
        }
    except (ValueError, KeyError, TypeError, HTTPException, OSError):
        return None
