"""Provider-neutral first publication gate: checked static inputs or honest fallback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    ValidationError,
)

from services import assurance_records as records


def strict_configured():
    return os.environ.get("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled") == "strict"


def admission(options):
    mode = os.environ.get("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled")
    if mode not in {"disabled", "shadow", "pilot", "strict"}:
        raise HTTPException(503, {"error_kind": "assurance_unavailable"})
    if options is not None:
        if (
            not isinstance(options, dict)
            or set(options) != {"version", "mode"}
            or type(options.get("version")) is not int
            or options.get("mode") != "strict"
        ):
            raise HTTPException(422, {"error_kind": "invalid_assurance_request"})
        if options["version"] != 1:
            raise HTTPException(422, {"error_kind": "unsupported_assurance_version"})
        if mode not in {"pilot", "strict"}:
            raise HTTPException(503, {"error_kind": "assurance_unavailable"})
    return mode == "strict" or options is not None


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool_use_id: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_id: str | None = Field(default=None, min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=128)


class Observed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["observed"]
    source: Source
    entity_id: str = Field(min_length=1, max_length=128)
    metric: str = Field(min_length=1, max_length=64)
    value: Annotated[StrictInt | StrictFloat, Field(allow_inf_nan=False)]
    unit: str = Field(min_length=1, max_length=16)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictInt
    blocks: list[Observed] = Field(min_length=1, max_length=20)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _label(value):
    # Prevent a component name from becoming executable Markdown/HTML/a link.
    return (
        re.sub(r"([\\`*_{}\[\]<>#!|])", r"\\\1", value)
        .replace("\n", " ")
        .replace("\r", " ")
    )


class Turn:
    def __init__(self, session, ctx):
        self.turn_id = str(uuid.uuid4())
        self.owner = session.owner_user_id or records.actor_id()
        self.project_id = ctx.project_uuid
        self.sources = {}
        self.approved = None
        self.session = session

    def history_seed(self, messages):
        seed = []
        for message in messages:
            content = message.get("content")
            if message.get("role") == "assistant":
                blocks = (
                    [
                        b
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    ]
                    if isinstance(content, list)
                    else []
                )
                answer_id = message.get("_approved_answer_id")
                if answer_id:
                    try:
                        answer = records.answer(self.owner, answer_id)
                        blocks.append({"type": "text", "text": answer["text"]})
                    except HTTPException:
                        blocks.append(
                            {
                                "type": "text",
                                "text": "Previous checked answer is unavailable.",
                            }
                        )
                if blocks:
                    seed.append({"role": "assistant", "content": blocks})
            elif (
                isinstance(content, list)
                and content
                and all(b.get("type") == "tool_result" for b in content)
            ):
                seed.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                **b,
                                "content": "Previous diagnostic result; retrieve current evidence before answering.",
                            }
                            for b in content
                        ],
                    }
                )
            else:
                from harness.history import is_turn_summary

                if not is_turn_summary(message):
                    seed.append(message)
        return seed

    def tool_guard(self, tool_name, args):
        # Unsupported effects do not execute during this narrow strict pilot.
        if tool_name == "get_component":
            return (
                isinstance(args, dict)
                and set(args) == {"component_class", "name"}
                and args.get("component_class") in records.INPUT_METRICS
                and isinstance(args.get("name"), str)
                and 1 <= len(args["name"]) <= 128
            )
        if tool_name == "ui_open_panel":
            # Existing typed handler owns target validation. No acknowledgement
            # or completion claim is issued by this tranche.
            return True
        return False

    def capture(self, tool_name, args, result, tool_use_id):
        if tool_name != "get_component":
            return
        record = records.capture_input(self.owner, args, result)
        if record is not None:
            self.sources[tool_use_id] = record

    def result_projection(self, tool_name, result, tool_use_id):
        record = self.sources.get(tool_use_id)
        outcome = (
            "completed"
            if record is not None
            else "submitted"
            if tool_name == "ui_open_panel"
            else "uncertain"
        )
        metadata = {
            "version": 1,
            "outcome": outcome,
            "evidence_ids": [record["evidence_id"]] if record else [],
        }
        if record is None:
            return {"_assurance": metadata}
        # No arbitrary annotations, prose, links or artifact metadata pre-approval.
        return {
            **{fact["metric"]: fact["value"] for fact in record["facts"]},
            "_assurance": metadata,
        }

    def validate(self, blocks):
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if len(text.encode()) > 64 * 1024:
            raise ValueError("oversized draft")
        draft = Draft.model_validate(json.loads(text, object_pairs_hook=_object))
        if draft.schema_version != 1 or len({b.id for b in draft.blocks}) != len(
            draft.blocks
        ):
            raise ValueError("invalid version or repeated claim")
        public_blocks, evidence_ids, lines = [], [], []
        for block in draft.blocks:
            source = block.source
            if (source.tool_use_id is None) == (source.evidence_id is None):
                raise ValueError("exactly one source required")
            if source.tool_use_id is not None:
                record = self.sources.get(source.tool_use_id)
                if record is None:
                    raise ValueError("unknown source")
            else:
                # Only captured inputs from this turn are admitted in the pilot.
                record = next(
                    (
                        r
                        for r in self.sources.values()
                        if r["evidence_id"] == source.evidence_id
                    ),
                    None,
                )
                if record is None:
                    raise ValueError("unknown source")
            record = records.evidence(self.owner, record["evidence_id"])
            fact = next((f for f in record["facts"] if f["path"] == source.path), None)
            if fact is None or any(
                getattr(block, key) != fact[key]
                for key in ("entity_id", "metric", "value", "unit")
            ):
                raise ValueError("claim does not match exact evidence")
            public_blocks.append(
                {
                    "id": block.id,
                    "kind": "observed",
                    **fact,
                    "source": {
                        "evidence_id": record["evidence_id"],
                        "path": fact["path"],
                    },
                }
            )
            if record["evidence_id"] not in evidence_ids:
                evidence_ids.append(record["evidence_id"])
            value_text = repr(fact["value"]).removesuffix(".0")
            lines.append(
                f"Configured {record['scope']['component_class']} input {_label(fact['entity_id'])}: "
                f"{fact['metric']} = {value_text} {fact['unit']}."
            )
        return (
            "\n".join(lines)
            + "\nStatic inputs; time-series overrides are not checked.",
            public_blocks,
            evidence_ids,
        )

    def commit(self, blocks, ctx):
        from harness.history import _redact_for_persist
        from services.pypsa_service import PyPSAService

        if (
            self.session.abort_event.is_set()
            or PyPSAService.get_active_context() is not ctx
        ):
            raise HTTPException(409, {"error_kind": "assurance_context_changed"})
        limitations = []
        try:
            text, public_blocks, evidence_ids = self.validate(blocks)
            if _redact_for_persist(text) != text:
                raise ValueError("sensitive content")
        except (ValueError, TypeError, KeyError, ValidationError, HTTPException):
            text = "I couldn't verify that answer. No checked result is available for this request."
            public_blocks, evidence_ids = [], []
            limitations = [
                "This pilot checks static component inputs only; the draft or its evidence could not be validated."
            ]
        records.authorize(self.owner, self.project_id)
        if self.session.abort_event.is_set() or ctx.project_uuid != self.project_id:
            raise HTTPException(409, {"error_kind": "assurance_context_changed"})
        receipt = {
            "policy_version": "1",
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "evidence_ids": evidence_ids,
            "checked_block_ids": [b["id"] for b in public_blocks],
            "limitations": limitations,
            "reviewer_status": "not_required",
        }
        answer_id = str(uuid.uuid4())
        record = {
            "version": 1,
            "record_kind": "answer",
            "owner_id": self.owner,
            "project_id": self.project_id,
            "answer_id": answer_id,
            "turn_id": self.turn_id,
            "text": text,
            "blocks": public_blocks,
            "verdict": "limited" if limitations else "passed",
            "receipt": receipt,
            "expires_at": time.time() + records.RETENTION_SECONDS,
        }
        # One worker/locked network is the supported pilot. Check evidence once
        # more under the same lock as the durable commit; do not certify a race.
        with PyPSAService.get_lock():
            for evidence_id in evidence_ids:
                records.evidence(self.owner, evidence_id)
            records.authorize(self.owner, self.project_id)
            if self.session.abort_event.is_set():
                raise HTTPException(409, {"error_kind": "assurance_context_changed"})
            records.save(record, answer_id)
        self.approved = record
        return [{"type": "text", "text": text}]

    def approval(self):
        return {
            k: self.approved[k]
            for k in ("version", "answer_id", "turn_id", "verdict", "receipt")
        }

    def project_frame(self, event, payload):
        if event == "session_init":
            return {
                **payload,
                "turn_id": self.turn_id,
                "assurance": {"version": 1, "mode": "strict", "state": "checking"},
            }
        if event == "tool_result":
            return {
                **payload,
                "result": self.result_projection(
                    payload["tool_name"], payload.get("result"), payload["tool_use_id"]
                ),
            }
        if event in {"choice_request", "workflow_state"}:
            return None
        if event == "tool_progress":
            return {
                "tool_use_id": payload.get("tool_use_id"),
                "line": "Application task is running.",
                "kind": "INFO",
            }
        if event == "tool_error":
            return {
                **payload,
                "message": "The tool did not complete. No completed effect is verified.",
            }
        if event == "ui_event":
            return {
                k: v
                for k, v in payload.items()
                if k in {"kind", "panel_id", "results_tab"}
            }
        return payload


PROMPT = """
Strict checked-answer mode: any prose/thinking is private. After tools, return
only JSON {"schema_version":1,"blocks":[{"id":"fact-1","kind":"observed",
"source":{"tool_use_id":"the-actual-call-id","path":"/p_set"},
"entity_id":"the-actual-entity","metric":"p_set","value":5,"unit":"MW"}]}.
Use only exact captured static input facts and their units. Do not add narrative,
claims of completed effects, solved results or calculations. Unsupported requests
must not invent facts. Existing authorization and confirmations still apply.
"""
