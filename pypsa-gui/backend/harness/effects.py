"""Mandatory effect contracts, durable deduplication and independent read-back."""

import copy
import json
import os
import socket
import time
import uuid

from fastapi import HTTPException

from harness.durable import network_guard, transaction
from services import assurance_records as records, workflow_tools
from services.pypsa_service import PyPSAService


def refuse():
    raise HTTPException(
        409,
        {
            "error_kind": "capability_unsupported",
            "message": "The required effect contract could not be verified.",
        },
    )


def validate(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "version",
            "operation_id",
            "project_id",
            "tool_name",
            "arguments",
            "before_sha256",
        }
        or type(value.get("version")) is not int
        or value["version"] != 1
        or value.get("tool_name") != "update_component"
    ):
        raise ValueError("unsupported_effect_contract")
    if str(uuid.UUID(value["operation_id"])) != value["operation_id"]:
        raise ValueError("invalid_operation_id")
    if (
        not isinstance(value["project_id"], str)
        or not 1 <= len(value["project_id"]) <= 128
    ):
        raise ValueError("invalid_project")
    digest = value["before_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("invalid_revision")
    args = value["arguments"]
    if not isinstance(args, dict) or set(args) != {"component_class", "name", "attrs"}:
        raise ValueError("unsupported_effect_arguments")
    if (
        args["component_class"] not in records.INPUT_METRICS
        or not isinstance(args["name"], str)
        or not 1 <= len(args["name"]) <= 128
    ):
        raise ValueError("unsupported_component")
    attrs = args["attrs"]
    if (
        not isinstance(attrs, dict)
        or not attrs
        or not set(attrs) <= set(records.INPUT_METRICS[args["component_class"]])
    ):
        raise ValueError("unsupported_changed_paths")
    from harness.calculations import _number

    if any(
        type(v) not in (int, float) or _number(v) is None or (k != "q_set" and v < 0)
        for k, v in attrs.items()
    ):
        raise ValueError("invalid_domain_value")
    return json.loads(json.dumps(value, allow_nan=False))


def operation(owner, operation_id):
    return records.load(owner, operation_id, "operation")


def reserve(turn, args):
    """After confirmation, reserve before undo/dirty bookkeeping or queuing."""
    contract = turn.operation_contract
    from harness.rollout import effects_enabled

    if not effects_enabled() or records.digest(args) != records.digest(
        contract["arguments"]
    ):
        refuse()
    oid, owner = contract["operation_id"], turn.owner
    path = records._path(owner, oid)
    if path.exists():
        prior = operation(owner, oid)
        if (
            prior["contract_sha256"] != records.digest(contract)
            or prior["outcome"] != "completed"
        ):
            refuse()
        return False
    ctx = PyPSAService.get_active_context()
    with network_guard(), transaction(path.parent):
        records.authorize(owner, contract["project_id"])
        if path.exists():
            refuse()
        if (
            ctx.project_uuid != contract["project_id"]
            or turn.session.abort_event.is_set()
            or ctx.solver_state.get("status") == "running"
            or workflow_tools._network_input_fingerprint(ctx)
            != contract["before_sha256"]
        ):
            refuse()
        component = ctx.network.components[args["component_class"]]
        if args["name"] not in component.static.index:
            refuse()
        expected = copy.copy(ctx)
        expected.network = workflow_tools._network_copy(ctx.network)
        for key, value in args["attrs"].items():
            expected.network.components[args["component_class"]].static.at[
                args["name"], key
            ] = value
        nonce = str(uuid.uuid4())
        records.save(
            {
                "version": 1,
                "record_kind": "operation",
                "owner_id": owner,
                "project_id": ctx.project_uuid,
                "operation_id": oid,
                "contract": contract,
                "contract_sha256": records.digest(contract),
                "before_sha256": contract["before_sha256"],
                "expected_after_sha256": workflow_tools._network_input_fingerprint(
                    expected
                ),
                "reservation_nonce": nonce,
                "outcome": "reserved",
                "reserved_at": time.time(),
                "worker_pid": os.getpid(),
                "worker_host": socket.gethostname(),
                "expires_at": time.time() + records.RETENTION_SECONDS,
            },
            oid,
        )
        turn.reservation_nonce = nonce
        return True


def register_worker(turn, future):
    """A finished/cancelled worker cannot leave a live reservation by accident."""
    owner, oid = turn.owner, turn.operation_contract["operation_id"]

    def finished(_):
        try:
            with transaction(records._path(owner, oid).parent):
                record = operation(owner, oid)
                if record["outcome"] in {"reserved", "running"}:
                    record.update(outcome="uncertain", finished_at=time.time())
                    records.save(record, oid)
        except Exception:
            # Retain the original reservation; inability to checkpoint never
            # becomes permission to repeat the effect.
            pass

    future.add_done_callback(finished)


def _worker_dead(record):
    if record.get("worker_host") != socket.gethostname():
        return False
    try:
        os.kill(record["worker_pid"], 0)
        return False
    except ProcessLookupError:
        return True
    except (PermissionError, KeyError, OSError):
        return False


def reconcile(owner, operation_id, contract_sha256):
    """Read-back only. Neither a client verdict nor a live worker can settle it."""
    ctx = PyPSAService.get_active_context()
    with network_guard(), transaction(records._path(owner, operation_id).parent):
        record = operation(owner, operation_id)
        live_unknown = record["outcome"] in {"reserved", "running"} or not record.get(
            "finished_at"
        )
        if (
            record["contract_sha256"] != contract_sha256
            or ctx.project_uuid != record["project_id"]
            or (live_unknown and not _worker_dead(record))
            or ctx.solver_state.get("status") == "running"
        ):
            refuse()
        if record["outcome"] in {"completed", "failed"}:
            return record
        actual = workflow_tools._network_input_fingerprint(ctx)
        if actual == record["expected_after_sha256"]:
            record.update(
                outcome="completed",
                actual_after_sha256=actual,
                reconciled_at=time.time(),
                finished_at=record.get("finished_at", time.time()),
                verification="independent_readback",
            )
        elif actual == record["before_sha256"]:
            record.update(
                outcome="failed",
                actual_after_sha256=actual,
                reconciled_at=time.time(),
                finished_at=record.get("finished_at", time.time()),
                verification="no_current_effect",
            )
        else:
            refuse()
        records.save(record, operation_id)
        return record


def execute(turn, tool_name, args, handler):
    """
    Runs inside the ordinary handler worker AFTER existing confirmation.

    Keep validation, reservation, handler and read-back under the project lock.
    No handler JSON can create a completion receipt. An interrupted reservation
    blocks retries; a late worker can finish its own reservation only.
    """
    contract = turn.operation_contract
    from harness.rollout import effects_enabled

    if not effects_enabled():
        refuse()
    if (
        contract is None
        or tool_name != contract["tool_name"]
        or records.digest(args) != records.digest(contract["arguments"])
    ):
        refuse()
    owner, oid = turn.owner, contract["operation_id"]
    path = records._path(owner, oid)
    ctx = PyPSAService.get_active_context()
    with PyPSAService.get_lock(), transaction(path.parent):
        records.authorize(owner, contract["project_id"])
        if (
            ctx.project_uuid != contract["project_id"]
            or turn.session.abort_event.is_set()
        ):
            refuse()
        existing = operation(owner, oid)
        if existing["contract_sha256"] != records.digest(contract):
            refuse()
        if existing["outcome"] == "completed":
            turn.operations[oid] = existing
            return {
                "_assurance": {
                    "outcome": "completed",
                    "operation_id": oid,
                    "replayed": True,
                }
            }
        if (
            existing["outcome"] != "reserved"
            or existing["reservation_nonce"] != turn.reservation_nonce
        ):
            refuse()
        if (
            turn.effect_pending
            or ctx.solver_state.get("status") == "running"
            or workflow_tools._network_input_fingerprint(ctx)
            != contract["before_sha256"]
        ):
            records.save(
                {
                    **existing,
                    "outcome": "failed",
                    "finished_at": time.time(),
                    "verification": "rejected_before_execution",
                },
                oid,
            )
            refuse()
        after = existing["expected_after_sha256"]
        record = {**existing, "outcome": "running", "started_at": time.time()}
        # A durable reservation precedes any effect, including a handler failure.
        records.save(record, oid)
        try:
            result = handler(**args)
            actual = workflow_tools._network_input_fingerprint(ctx)
            completed = actual == after and not (
                isinstance(result, dict)
                and (result.get("error") or result.get("ok") is False)
            )
            record.update(
                outcome="completed" if completed else "uncertain",
                actual_after_sha256=actual,
                finished_at=time.time(),
                changed_paths=[
                    f"/{args['component_class']}/{args['name']}/{key}"
                    for key in args["attrs"]
                ],
            )
        except Exception:
            record.update(outcome="uncertain", finished_at=time.time())
            records.save(record, oid)
            turn.operations[oid] = record
            raise
        records.save(record, oid)
        turn.operations[oid] = record
        if not completed:
            refuse()
        return {
            "_assurance": {
                "outcome": "completed",
                "operation_id": oid,
                "before_sha256": record["before_sha256"],
                "after_sha256": actual,
            }
        }
