"""Issued, expiring mounted-view acknowledgements bound to a turn and revision."""

import time
import uuid

from fastapi import HTTPException

from harness.durable import network_guard, transaction
from services import assurance_records as records, workflow_tools
from services.pypsa_service import PyPSAService

PANELS = frozenset(
    {
        "results",
        "overview",
        "issues",
        "simparams",
        "timeseries",
        "capacityBounds",
        "scenarios",
        "snapshots",
        "horizon",
        "solveQueue",
        "hubDesign",
        "workspace",
        "settings",
        "gridspine",
        "reports",
        "campusElectrical",
    }
)


def cursor_id(owner, session_id):
    return str(uuid.uuid5(uuid.UUID(owner), "navigation/" + session_id))


def begin_turn(turn, generation):
    turn.binding_generation = generation
    oid = cursor_id(turn.owner, turn.session.session_id)
    if not records._path(turn.owner, oid).exists():
        return
    with transaction(records._path(turn.owner, oid).parent):
        if not records._path(turn.owner, oid).exists():
            return
        records.save(
            {
                "version": 1,
                "record_kind": "ui_action",
                "owner_id": turn.owner,
                "project_id": turn.project_id,
                "turn_id": turn.turn_id,
                "binding_generation": generation,
                "action_id": None,
                "expires_at": time.time() + records.RETENTION_SECONDS,
            },
            oid,
        )


def issue(turn, payload):
    target = {k: v for k, v in payload.items() if k in {"panel_id", "results_tab"}}
    if (
        payload.get("kind") != "navigate"
        or target.get("panel_id") not in PANELS
        or set(payload) - {"kind", "panel_id", "results_tab", "tool_use_id"}
        or ("results_tab" in target and target["panel_id"] != "results")
    ):
        return None
    ctx = PyPSAService.get_active_context()
    oid = str(uuid.uuid4())
    cursor = cursor_id(turn.owner, turn.session.session_id)
    with network_guard(), transaction(records._path(turn.owner, oid).parent):
        records.authorize(turn.owner, turn.project_id)
        if ctx.project_uuid != turn.project_id:
            return None
        record = {
            "version": 1,
            "record_kind": "ui_action",
            "owner_id": turn.owner,
            "project_id": turn.project_id,
            "project_name": ctx.loaded_project,
            "action_id": oid,
            "turn_id": turn.turn_id,
            "session_id": turn.session.session_id,
            "binding_generation": turn.binding_generation,
            "target": target,
            "input_sha256": workflow_tools._network_input_fingerprint(ctx),
            "outcome": "submitted",
            "expires_at": time.time() + 30,
        }
        records.save(record, oid)
        if not records._path(turn.owner, cursor).exists():
            records.save(
                {
                    "version": 1,
                    "record_kind": "ui_action",
                    "owner_id": turn.owner,
                    "project_id": turn.project_id,
                    "turn_id": turn.turn_id,
                    "binding_generation": turn.binding_generation,
                    "action_id": None,
                    "expires_at": time.time() + records.RETENTION_SECONDS,
                },
                cursor,
            )
        current = records.load(turn.owner, cursor, "ui_action")
        if current["turn_id"] != turn.turn_id:
            return None
        records.save({**current, "action_id": oid}, cursor)
        return {
            k: record[k]
            for k in (
                "version",
                "action_id",
                "turn_id",
                "binding_generation",
                "target",
                "project_id",
                "project_name",
                "input_sha256",
            )
        }


def acknowledge(owner, body):
    required = {"version", "action_id", "turn_id", "binding_generation", "target"}
    if (
        not isinstance(body, dict)
        or set(body) != required
        or type(body.get("version")) is not int
        or body["version"] != 1
        or type(body.get("binding_generation")) is not int
        or not isinstance(body.get("target"), dict)
        or not all(
            isinstance(body.get(k), str) and 1 <= len(body[k]) <= 128
            for k in ("action_id", "turn_id")
        )
    ):
        raise HTTPException(422, {"error_kind": "invalid_assurance_request"})
    try:
        with (
            network_guard(),
            transaction(records._path(owner, body["action_id"]).parent),
        ):
            record = records.load(owner, body["action_id"], "ui_action")
            current = records.load(
                owner, cursor_id(owner, record["session_id"]), "ui_action"
            )
            ctx = PyPSAService.get_active_context()
            if (
                any(record[k] != body[k] for k in required)
                or current["action_id"] != body["action_id"]
                or current["turn_id"] != body["turn_id"]
                or current["binding_generation"] != body["binding_generation"]
                or ctx.project_uuid != record["project_id"]
                or workflow_tools._network_input_fingerprint(ctx)
                != record["input_sha256"]
            ):
                raise ValueError("mismatch")
            if record["outcome"] == "submitted":
                record.update(
                    outcome="completed",
                    acknowledged_at=time.time(),
                    verification="browser_mounted_view",
                )
                records.save(record, body["action_id"])
            return {
                "version": 1,
                "action_id": body["action_id"],
                "outcome": record["outcome"],
                "verification": record["verification"],
            }
    except (HTTPException, ValueError, KeyError, TypeError):
        raise HTTPException(409, {"error_kind": "ui_ack_mismatch"}) from None
