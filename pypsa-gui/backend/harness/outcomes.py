"""Verified outcomes of blocking foreground jobs (assistant assurance issue 04, S1).

`classify` is a pure function. Its two inputs are the call's own submission
record, captured when the loop minted the job handle, and an independent read
of live state taken after the call: the solver record bound to the handle and
the run's evidence marker. It never reads the handler's returned status
fields (GK B J1). Every unreadable, missing or other-identity input gives
"uncertain", never "completed" (#1079, J2/J3).

Scope: run_simulation, run_ac_pf_stage, wait_for_job on an `fg:` handle and
abort_simulation. wait_for_job on study (`ad:`), report (`rpt:`) or queue
jobs reads "uncertain" with a pending reason; nonblocking jobs wait for
live-voice issue 11.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

VERSION = 1
JOB_TOOLS = frozenset(
    {"run_simulation", "run_ac_pf_stage", "wait_for_job", "abort_simulation"}
)
OUTCOMES = (
    "submitted",
    "running",
    "completed",
    "partial",
    "failed",
    "cancel_requested",
    "cancelled",
    "uncertain",
)

_LOCK = threading.Lock()
_SUBMISSIONS: OrderedDict[str, dict] = OrderedDict()
_MAX = 512


def note_submission(job_id: str, ctx: Any, token: dict | None) -> None:
    """Record the call's own identity when the loop mints a foreground handle.

    `token` is run_evidence.start_foreground_run's return; None (an unsaved
    scratch network) leaves no run marker, so the job can never read completed.
    """
    from harness import job_adapters as ja

    with ja._LOCK:
        handle = ja._FOREGROUND.get(job_id)
        binding = ja._ACTIVITY_BINDINGS.get(job_id)
    record = {
        "job_id": job_id,
        "tool_use_id": handle.tool_use_id if handle else None,
        "project": str(handle.project_uuid) if handle and handle.project_uuid else None,
        "ctx": binding[0] if binding else None,
        "worker": binding[2] if binding else None,
        "run_id": token.get("run_id") if token else None,
        "started_at": token.get("started_at") if token else None,
        "evidence": (token["root"], token["project_id"]) if token else None,
    }
    with _LOCK:
        _SUBMISSIONS[job_id] = record
        while len(_SUBMISSIONS) > _MAX:
            _SUBMISSIONS.popitem(last=False)


def submission(job_id: Any) -> dict | None:
    with _LOCK:
        return (
            dict(_SUBMISSIONS[job_id])
            if isinstance(job_id, str) and job_id in _SUBMISSIONS
            else None
        )


def _by_tool_use(tool_use_id: str) -> dict | None:
    with _LOCK:
        found = [r for r in _SUBMISSIONS.values() if r["tool_use_id"] == tool_use_id]
    return dict(found[-1]) if found else None


def _bound_to_live_record() -> dict | None:
    """The submission whose worker is the live solver record's own (abort's target)."""
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        worker = ctx.solver_state.get("thread")
    with _LOCK:
        found = [
            r
            for r in _SUBMISSIONS.values()
            if worker is not None and r["worker"] is worker and r["ctx"] == id(ctx)
        ]
    return dict(found[-1]) if found else None


def read_live(expected: dict) -> dict:
    """Independent read after the call. Raises nothing: a failure is a read error."""
    try:
        from services import run_evidence
        from services.pypsa_service import PyPSAService

        ctx = PyPSAService.get_active_context()
        with ctx.solver_state_lock:
            state = ctx.solver_state
            live = {
                k: state.get(k)
                for k in (
                    "status",
                    "kind",
                    "ac_pf_converged_count",
                    "ac_pf_total_snapshots",
                )
            }
            live["worker"] = state.get("thread")
        live["ctx"] = id(ctx)
        live["project"] = str(ctx.project_uuid) if ctx.project_uuid else None
        run = None
        if expected.get("evidence") and expected.get("run_id"):
            root, project_id = expected["evidence"]
            run = run_evidence._load_run(root, project_id, expected["run_id"])
        live["run"] = (
            {k: run.get(k) for k in ("run_id", "job_id", "started_at")}
            if isinstance(run, dict)
            else None
        )
        return live
    except Exception as exc:  # noqa: BLE001 -- an unreadable outcome is uncertain (#1079)
        return {"error": type(exc).__name__}


def _counts(read: dict) -> tuple[int, int] | None:
    ok, total = read.get("ac_pf_converged_count"), read.get("ac_pf_total_snapshots")
    if (
        type(ok) is not int
        or type(total) is not int
        or total < 1
        or not 0 <= ok <= total
    ):
        return None
    return ok, total


def classify(
    tool: str, expected: dict | None, read: dict | None, *, abort: bool = False
) -> dict:
    """Pure: the outcome of one job call from its own submission and an independent read."""

    def out(outcome, reason=None):
        return {"outcome": outcome, "reason": reason}

    if expected is None:
        return out("uncertain", "no_submission")
    if not isinstance(read, dict) or "error" in read:
        return out("uncertain", "read_failed")
    if expected.get("ctx") is None or read.get("ctx") != expected["ctx"]:
        return out("uncertain", "other_context")
    if expected.get("project") is None or read.get("project") != expected["project"]:
        return out("uncertain", "other_project")
    if expected.get("worker") is None or read.get("worker") is not expected["worker"]:
        return out("uncertain", "other_job")
    run = read.get("run")
    if expected.get("run_id") is None or not isinstance(run, dict):
        return out("uncertain", "no_run_record")
    if (
        run.get("run_id") != expected["run_id"]
        or run.get("job_id") != expected["job_id"]
    ):
        return out("uncertain", "other_run")
    if run.get("started_at") != expected.get("started_at"):
        return out("uncertain", "other_start")
    status = read.get("status")
    if status == "running":
        return out("cancel_requested" if abort else "running")
    if status == "queued":
        return out("cancel_requested" if abort else "submitted")
    if status == "aborted":
        return out("cancelled")
    if status == "failed":
        return out("failed")
    if status == "completed":
        if read.get("kind") != "ac_pf":
            return out("completed")
        counts = _counts(read)
        if counts is None:
            return out("uncertain", "convergence_unknown")
        return (
            out("completed")
            if counts[0] == counts[1]
            else out("partial", "snapshots_not_converged")
        )
    return out("uncertain", "status_unknown")


def project(tool: str, args: dict, tool_use_id: str) -> dict:
    """The `_outcome` block for one job tool result (ordinary turns)."""
    if tool == "wait_for_job":
        job_id = args.get("job_id")
        if not (isinstance(job_id, str) and job_id.startswith("fg:")):
            return {
                "version": VERSION,
                "outcome": "uncertain",
                "job_id": job_id if isinstance(job_id, str) else None,
                "reason": "adapter_pending",
            }
        expected = submission(job_id)
    elif tool == "abort_simulation":
        expected = _bound_to_live_record()
    else:
        expected = _by_tool_use(tool_use_id)
    verdict = classify(
        tool,
        expected,
        read_live(expected) if expected else None,
        abort=tool == "abort_simulation",
    )
    return {
        "version": VERSION,
        **verdict,
        "job_id": expected["job_id"] if expected else None,
    }


def _reset_for_tests() -> None:
    with _LOCK:
        _SUBMISSIONS.clear()


# ── S2: requested versus actual for component writes ─────────────────────────
#
# `note_write` runs in the dispatch seam after the edit counter's bump and
# before the handler: it records, per requested item, whether the row existed
# and (bulk update) the requested value coerced by the bulk path's own column
# rules. `read_write` reads the live network back under the PyPSA lock,
# between two reads of the edit counter. `classify_write` is pure. The
# handler's returned JSON is never read (GK B W1–W7).

WRITE_TOOLS = frozenset(
    {
        "bulk_update_components",
        "batch_create_components",
        "delete_component",
        "batch_delete_components",
    }
)
_MAX_PATHS = 5


class _Uncomparable:
    """A requested value the column rules refuse, or an attr with no column (W2)."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNCOMPARABLE"


UNCOMPARABLE = _Uncomparable()


def _static(n, component_class):
    from services.chat_tools import _GENERIC_CRUD_ATTRS

    attr = (
        "global_constraints"
        if component_class == "GlobalConstraint"
        else _GENERIC_CRUD_ATTRS.get(component_class)
    )
    if not attr:
        raise KeyError(component_class)
    return getattr(n, attr)


def _coerce(df, component_class, attr, value):
    """`value` as the bulk path would write it into `df[attr]`, or UNCOMPARABLE."""
    from services.network_bulk import _coerce_bulk_value

    if attr not in df.columns:
        return UNCOMPARABLE
    try:
        return _coerce_bulk_value(df, attr, value, component_class)
    except Exception:  # noqa: BLE001 - a refused value is not comparable
        return UNCOMPARABLE


def _requested(tool: str, args: dict) -> tuple[str, list[dict]]:
    """(component_class, items) from the call's own arguments; ValueError when malformed."""
    cls = args.get("component_class")
    if not isinstance(cls, str):
        raise ValueError("component_class")
    if tool == "bulk_update_components":
        names, updates = args.get("names"), args.get("updates")
        if not isinstance(names, list) or not isinstance(updates, dict) or not updates:
            raise ValueError("bulk_args")
        return cls, [
            {"kind": "cell", "name": str(name), "attr": str(attr), "requested": value}
            for name in names
            for attr, value in updates.items()
        ]
    if tool == "batch_create_components":
        entries = args.get("components")
        if not isinstance(entries, list) or not all(
            isinstance(e, dict) and isinstance(e.get("name"), str) for e in entries
        ):
            raise ValueError("create_args")
        return cls, [
            {
                "kind": "create",
                "name": e["name"],
                "attrs": {k: v for k, v in e.items() if k != "name"},
            }
            for e in entries
        ]
    names = [args.get("name")] if tool == "delete_component" else args.get("names")
    if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
        raise ValueError("delete_args")
    return cls, [{"kind": "delete", "name": name} for name in names]


def note_write(tool: str, args: dict) -> dict | None:
    """The call's own request and the rows' prior presence, before the handler runs."""
    if tool not in WRITE_TOOLS:
        return None
    from services import dirty_state
    from services.pypsa_service import PyPSAService

    try:
        ctx = PyPSAService.get_active_context()
        cls, items = _requested(tool, args)
        with PyPSAService.get_lock():
            df = _static(ctx.network, cls)
            for item in items:
                item["before_present"] = item["name"] in df.index
                if item["kind"] == "cell":
                    item["expected"] = _coerce(df, cls, item["attr"], item["requested"])
            revision = dirty_state.revision(ctx)
    except Exception:  # noqa: BLE001 - no note reads uncertain later
        return {"tool": tool, "error": True}
    return {
        "tool": tool,
        "ctx": id(ctx),
        "cls": cls,
        "items": items,
        "revision": revision,
    }


def read_write(note: dict | None) -> dict:
    """An independent read of the live rows the call named; never the handler's JSON."""
    if not note or note.get("error"):
        return {"error": "no_note"}
    from services import dirty_state
    from services.pypsa_service import PyPSAService

    try:
        ctx = PyPSAService.get_active_context()
        if id(ctx) != note["ctx"]:
            return {"error": "other_context"}
        with PyPSAService.get_lock():
            start = dirty_state.revision(ctx)
            df = _static(ctx.network, note["cls"])
            rows = {}
            for item in note["items"]:
                name = item["name"]
                present = name in df.index
                row = {"present": present}
                if present and item["kind"] == "cell":
                    row["value"] = (
                        df.at[name, item["attr"]]
                        if item["attr"] in df.columns
                        else UNCOMPARABLE
                    )
                elif present and item["kind"] == "create":
                    row["attrs"] = {
                        attr: (
                            _coerce(df, note["cls"], attr, value),
                            df.at[name, attr] if attr in df.columns else UNCOMPARABLE,
                        )
                        for attr, value in item["attrs"].items()
                    }
                rows[name if item["kind"] != "cell" else f"{name}/{item['attr']}"] = row
            end = dirty_state.revision(ctx)
    except Exception:  # noqa: BLE001 - an unreadable network reads uncertain
        return {"error": "readback_failed"}
    return {"revision_start": start, "revision_end": end, "rows": rows}


def _same(expected, actual) -> bool | None:
    """True/False, or None when the two cannot be compared."""
    import math

    if expected is UNCOMPARABLE or actual is UNCOMPARABLE:
        return None
    import numbers

    import numpy as np

    if isinstance(expected, (bool, np.bool_)) or isinstance(actual, (bool, np.bool_)):
        try:
            return (
                isinstance(expected, (bool, np.bool_))
                and isinstance(actual, (bool, np.bool_))
                and bool(expected) == bool(actual)
            )
        except Exception:  # noqa: BLE001
            return None
    if isinstance(expected, numbers.Real):
        try:
            a = float(actual)
        except (TypeError, ValueError):
            return False
        e = float(expected)
        if math.isnan(e) or math.isnan(a):
            return math.isnan(e) and math.isnan(a)
        return e == a
    if isinstance(expected, str):
        return isinstance(actual, str) and expected == actual
    if expected is None:
        return actual is None
    return None


def _path(cls: str, item: dict) -> str:
    return f"/{cls}/{item['name']}" + (
        f"/{item['attr']}" if item["kind"] == "cell" else ""
    )


def classify_write(note: dict | None, read: dict | None) -> dict:
    """Pure: completed / partial / failed / uncertain for one write call."""

    def out(outcome, reason=None, **extra):
        return {"outcome": outcome, "reason": reason, **extra}

    if not note or note.get("error"):
        return out("uncertain", "no_submission")
    if not isinstance(read, dict) or "error" in read:
        return out("uncertain", "readback_failed")
    if (
        read.get("revision_start") != note["revision"]
        or read.get("revision_end") != note["revision"]
    ):
        return out("uncertain", "revision_moved")
    rows = read.get("rows") or {}
    done, not_done, unknown = [], [], []
    for item in note["items"]:
        key = (
            item["name"] if item["kind"] != "cell" else f"{item['name']}/{item['attr']}"
        )
        row = rows.get(key)
        if row is None:
            unknown.append(item)
            continue
        if item["kind"] == "delete":
            (
                done if item["before_present"] and not row["present"] else not_done
            ).append(item)
            continue
        if item["kind"] == "create":
            if item["before_present"] or not row["present"]:
                not_done.append(item)
                continue
            verdicts = [_same(e, a) for e, a in (row.get("attrs") or {}).values()]
            if any(v is None for v in verdicts):
                unknown.append(item)
            elif all(verdicts):
                done.append(item)
            else:
                not_done.append(item)
            continue
        # A cell: the row must have existed, and the value must read as requested.
        # A requested value equal to the old value counts as changed (GK B (24)).
        if not item["before_present"] or not row["present"]:
            not_done.append(item)
            continue
        verdict = _same(
            item.get("expected", UNCOMPARABLE), row.get("value", UNCOMPARABLE)
        )
        (unknown if verdict is None else done if verdict else not_done).append(item)
    counts = {
        "requested": len(note["items"]),
        "done": len(done),
        "not_done": len(not_done),
        "unknown": len(unknown),
    }
    paths = [_path(note["cls"], i) for i in not_done[:_MAX_PATHS]]
    if unknown:
        return out("uncertain", "not_comparable", counts=counts, paths_not_done=paths)
    if not not_done:
        return out("completed", counts=counts, paths_not_done=[])
    if not done:
        return out("failed", counts=counts, paths_not_done=paths)
    return out("partial", counts=counts, paths_not_done=paths)


def project_write(note: dict | None) -> dict:
    """The `_outcome` block for one write tool result (ordinary turns)."""
    return {"version": VERSION, **classify_write(note, read_write(note))}
