"""
Durable, bounded receipts around ordinary network-case dispatches.

Controls offer calls; only the normal confirmed dispatch invokes their handlers.
An interrupted submission is never automatically retried.
"""
from __future__ import annotations

import copy
import math
import re
import time
import uuid
from contextlib import nullcontext
from contextvars import ContextVar

from harness.durable import network_guard
from services import experiments as ex

_ADMISSION = ContextVar("experiment_sweep_admission", default=None)
_STATES = {"ready", "submitting", "submitted", "running", "completed", "failed",
           "uncertain", "unavailable", "budget_stopped"}
_clock = time.time


def _path(root, experiment_id):
    directory = root / "experiment_execution"
    if directory.is_symlink():
        ex._refuse("experiment_checkpoint_unreadable", "The execution directory is unsafe.")
    return directory / (experiment_id + ".json")


def _name(record):
    name = "exp-" + record["experiment_id"].replace("-", "") + "-" + record["cases"][0]["id"][:24]
    if "_parent_record" in record:
        index = next(i for i, c in enumerate(record["_parent_record"]["cases"]) if c["id"] == record["_case_id"])
        name += "-" + str(index + 1)
    return name


def _submission(record):
    changes = []
    for change in record["cases"][0]["changes"]:
        component, _, rest = change["path"].partition(".")
        name, _, attribute = rest.rpartition(".")
        changes.append({"component_class": component, "names": [name],
                        "attribute": attribute, "value": change["value"]})
    return {"baseline_project_id": record["project_id"],
            "cases": [{"new_name": _name(record), "changes": changes}]}


def _new(record):
    return {"version": 1, "experiment_id": record["experiment_id"],
        "project_id": record["project_id"], "owner_id": record["owner_id"],
        "plan_sha256": record["plan_sha256"], "preflight_sha256": ex._digest(record["preflight"]),
        "case_name": _name(record), "status": "ready", "cancelled": False,
        "started_at": _clock(), "attempts": [], "case_project_id": None,
        "job_id": None, "job_status": None, "run_id": None, "evidence_sha256": None,
        "objective": None, "objective_unit": None, "last_error": None}


def _read(root, record):
    if "_parent_record" in record:
        from harness.experiment_two_case import read_case
        return read_case(root, record)
    path = _path(root, record["experiment_id"])
    if not path.exists() and not path.is_symlink():
        if record.get("operations"):
            ex._refuse("experiment_checkpoint_unreadable", "The reserved execution ledger is missing; no replacement was created.")
        return None
    if not record.get("operations"):
        ex._refuse("experiment_checkpoint_unreadable", "The execution ledger has no matching plan reservation.")
    return _validate(record, ex._read_json(path))


def _validate(record, data):
    try:
        if (set(data) != set(_new(record)) or type(data["version"]) is not int or data["version"] != 1
                or any(data[k] != record[k] for k in ("experiment_id", "project_id", "owner_id", "plan_sha256"))
                or data["preflight_sha256"] != ex._digest(record["preflight"])
                or data["case_name"] != _name(record) or data["status"] not in _STATES
                or type(data["cancelled"]) is not bool
                or isinstance(data["started_at"], bool) or not isinstance(data["started_at"], (int, float))
                or not math.isfinite(data["started_at"])
                or not isinstance(data["attempts"], list)
                or len(data["attempts"]) > record["limits"]["max_tool_attempts"]
                or data["job_status"] not in (None, "queued", "running", "completed", "failed", "aborted", "interrupted")):
            raise ValueError()
        for key in ("case_project_id", "job_id"):
            if data[key] is not None and not ex._valid_id(data[key]):
                raise ValueError()
        for key in ("evidence_sha256",):
            if data[key] is not None and (not isinstance(data[key], str) or not re.fullmatch(r"[0-9a-f]{64}", data[key])):
                raise ValueError()
        if data["run_id"] is not None and data["run_id"] != "q-" + data["job_id"].replace("-", ""):
            raise ValueError()
        if data["objective"] is not None and (isinstance(data["objective"], bool)
                or not isinstance(data["objective"], (int, float)) or not math.isfinite(data["objective"])):
            raise ValueError()
        if data["objective_unit"] is not None and (not isinstance(data["objective_unit"], str)
                or not 1 <= len(data["objective_unit"]) <= 24):
            raise ValueError()
        if data["last_error"] is not None and (not isinstance(data["last_error"], str) or len(data["last_error"]) > 100):
            raise ValueError()
        ids = set()
        for attempt in data["attempts"]:
            if (set(attempt) != {"id", "tool", "status", "started_at"}
                    or not re.fullmatch(r"[0-9a-f]{32}", attempt["id"]) or attempt["id"] in ids
                    or attempt["tool"] not in ("run_sensitivity_sweep", "wait_for_job")
                    or attempt["status"] not in ("reserved", "returned", "uncertain")
                    or isinstance(attempt["started_at"], bool)
                    or not isinstance(attempt["started_at"], (int, float)) or not math.isfinite(attempt["started_at"])):
                raise ValueError()
            ids.add(attempt["id"])
        submissions = [a for a in data["attempts"] if a["tool"] == "run_sensitivity_sweep"]
        if len(submissions) > 1 or (data["status"] != "ready" and not submissions and data["status"] != "budget_stopped"):
            raise ValueError()
        if data["job_id"] and (not data["case_project_id"] or not submissions):
            raise ValueError()
        if data["status"] == "completed" and (data["job_status"] != "completed"
                or not data["run_id"] or not data["evidence_sha256"] or data["objective"] is None
                or data["objective_unit"] is None):
            raise ValueError()
        if data["status"] == "ready" and data["attempts"]:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        ex._refuse("experiment_checkpoint_unreadable", "Execution state is invalid; no operation was replayed.")
    return data


def _save(root, record, ledger):
    if "_parent_record" in record:
        from harness.experiment_two_case import save_case
        return save_case(root, record, ledger)
    ex._save(_path(root, record["experiment_id"]), ledger)


def _load(experiment_id, case_id=None):
    root, project, owner = ex._authority()
    ex._load_index(root)
    record = ex._read_record(root, experiment_id, project, owner)
    if case_id is not None:
        from harness.experiment_two_case import select_case
        record = select_case(record, case_id)
    return root, record


def _budget(record, ledger):
    elapsed = _clock() - ledger["started_at"]
    limits = record["limits"]
    return (0 <= elapsed < limits["max_elapsed_seconds"]
            and len(ledger["attempts"]) < limits["max_tool_attempts"])


def _observe(record, ledger):
    """Read authoritative state; a submitted receipt never proves completion."""
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools, project_registry, run_evidence, workflow_tools as wt
    data = copy.deepcopy(ledger)
    if not data["job_id"]:
        if data["status"] == "submitting":
            data["status"] = "uncertain"
        return data
    try:
        with chat_tools._acting() as (db, user):
            child = project_registry.resolve_project(db, user, data["case_project_id"])
            if str(child.parent_project_id) != record["project_id"] or child.name != data["case_name"]:
                raise ValueError()
            job = _visible_job_or_404(db, user, data["job_id"])
            if job.get("project_key") != project_registry.registry_key(child) or job.get("kind") != "solve":
                raise ValueError()
            data["job_status"] = job["status"]
            if job["status"] in ("queued", "running"):
                data.update(status="submitted" if job["status"] == "queued" else "running", last_error=None)
                return data
            if job["status"] != "completed":
                data.update(status="failed", last_error="experiment_job_not_completed")
                return data
            saved = wt._load_context(child)
            expected = record["preflight"]["case_checks"][0]
            exact = wt._network_input_fingerprint(saved, exact=True)
            from services.dispatch_status import dispatch_status
            if (exact != expected["exact_input_fingerprint"]
                    # The same check a queued plan step settles on (continuation._queue_run).
                    or wt.solve_freshness(saved)["inputs_changed_since_solve"] is not False
                    or dispatch_status(saved.network) != "fresh" or wt._active_job(child)):
                data.update(status="unavailable", last_error="experiment_case_input_drift")
                return data
            child_root = project_registry.project_dir(child)
            run_id = run_evidence.queue_run_id(data["job_id"])
            manifest = run_evidence._load_run(child_root, str(child.id), run_id)
            if (not manifest or manifest["status"] != "completed" or manifest.get("job_id") != data["job_id"]
                    or manifest.get("kind") != "solve_queue" or manifest.get("mode") != "solve_queue"
                    or manifest.get("actor_id") != record["owner_id"]
                    or not run_evidence._evidence_available(child_root, manifest)):
                data.update(status="unavailable", last_error="experiment_evidence_unavailable")
                return data
            objective = manifest.get("metrics", {}).get("objective", {}).get("value")
            unit = manifest.get("metrics", {}).get("objective", {}).get("unit")
            if (isinstance(objective, bool) or not isinstance(objective, (int, float))
                    or not math.isfinite(objective) or saved.network.objective != objective
                    or job.get("objective") != objective or not isinstance(unit, str) or not 1 <= len(unit) <= 24):
                data.update(status="unavailable", last_error="experiment_result_mismatch")
                return data
            digest = ex._digest(manifest)
            if data["evidence_sha256"] and data["evidence_sha256"] != digest:
                data.update(status="unavailable", last_error="experiment_evidence_changed")
                return data
            data.update(status="completed", run_id=run_id, evidence_sha256=digest,
                        objective=objective, objective_unit=unit, last_error=None)
    except (ValueError, KeyError, TypeError, RuntimeError, ex.HTTPException):
        data.update(status="unavailable", last_error="experiment_source_unavailable")
    return data


def _view(record, data, *, root=None, ledger=None):
    job_status = data["job_status"]
    executed = (True if job_status in ("running", "completed", "failed") else
                None if job_status in ("interrupted", "aborted")
                or data["status"] in ("submitting", "uncertain", "unavailable") else False)
    out = {k: data[k] for k in ("experiment_id", "project_id", "case_project_id", "job_id", "job_status",
        "run_id", "evidence_sha256", "status", "cancelled", "last_error")}
    out.update(executed=executed, execution_available=True,
        result_available=data["status"] == "completed", attempts_used=len(data["attempts"]),
        case_effects_reserved=any(a['tool'] == 'run_sensitivity_sweep' for a in data['attempts']),
        note="Execute next_call through ordinary harness admission. Cancel stops advancement, not its job. No checked-answer certification is implied.")
    if data["status"] == "completed":
        out["objective"] = data["objective"]
        out["objective_unit"] = data["objective_unit"]
        out["objective_basis"] = "recorded_solver_objective"
        out["evidence_tools"] = {"activate_project": {"project_id": data["case_project_id"]},
            "get_run_evidence": {"run_id": data["run_id"]}, "export_run_evidence": {"run_id": data["run_id"]}}
    if not data["cancelled"] and _budget(record, data):
        if data["status"] == "ready":
            out["next_call"] = {"tool": "run_sensitivity_sweep", "args": _submission(record)}
        elif data["status"] in ("submitted", "running"):
            remaining = record["limits"]["max_elapsed_seconds"] - (_clock() - data["started_at"])
            out["next_call"] = {"tool": "wait_for_job", "args": {"job_id": data["job_id"], "timeout_seconds": min(20, max(0, remaining))}}
    if root is not None and ledger is not None:
        from harness.experiment_reconciliation import decorate
        return decorate(root, record, ledger, out)
    return out


def describe(record):
    root, project, owner = ex._authority()
    if project != record["project_id"] or owner != record["owner_id"]:
        ex._refuse("experiment_not_found", "Experiment not found.", 404)
    if len(record["cases"]) == 2:
        from harness.experiment_two_case import describe as describe_pair
        return describe_pair(root, record)
    ledger = _read(root, record)
    return None if ledger is None else _view(record, _observe(record, ledger), root=root, ledger=ledger)


def advance_experiment(experiment_id: str) -> dict:
    from services import chat_tools, dirty_state, project_registry, workflow_tools as wt
    session = chat_tools.chat_session()
    if session is None:
        ex._refuse("no_chat_session", "Case advancement requires a chat session.")
    with network_guard():
        root, record = _load(experiment_id)
        with ex._LOCK, ex._transaction(root):
            if record["domain"] == "network" and len(record["cases"]) == 2:
                from harness.experiment_two_case import advance
                return advance(root, record, session)
            if not runnable(record["domain"], len(record["cases"])) or len(record["cases"]) != 1:
                ex._refuse("experiment_adapter_unavailable", RUNNABLE_RULE)
            if record.get("version") != 2 or ex._public_preflight(record).get("status") != "validated":
                ex._refuse("experiment_preflight_required", "Prepare the unchanged current network before advancing.")
            ledger = _read(root, record)
            if ledger is None:
                ctx = ex.PyPSAService.get_active_context()
                with chat_tools._acting() as (db, user):
                    baseline = project_registry.resolve_project(db, user, record["project_id"])
                    saved = wt._load_context(baseline)
                if dirty_state.is_dirty(ctx) or wt._network_input_fingerprint(saved, exact=True) != record["preflight"]["exact_input_fingerprint"]:
                    ex._refuse("experiment_baseline_not_saved", "Save the exact baseline inputs before arming a case.")
                ledger = _new(record)
                record['operations'] = [{'kind': 'single-network-case-ledger', 'version': 1,
                    'preflight_sha256': ex._digest(record['preflight'])}]
                # Plan reference first: a missing/orphan ledger cannot reset admission.
                ex._save(root / 'experiments' / (experiment_id + '.json'), record)
                _save(root, record, ledger)
            session.experiment_id = experiment_id
            session.experiment_project_id = record["project_id"]
            return _view(record, _observe(record, ledger), root=root, ledger=ledger)


RUNNABLE_RULE = "This adapter advances one or two network cases only."


def runnable(domain, count) -> bool:
    """The execution admission rule: 1 or 2 network cases per experiment.
    `advance_experiment` and a study brief's `start_task` both ask it."""
    return domain == "network" and count in (1, 2)


def refresh_experiment(experiment_id: str) -> dict:
    with network_guard():
        root, record = _load(experiment_id)
        with ex._LOCK, ex._transaction(root):
            if len(record["cases"]) == 2:
                from harness.experiment_two_case import refresh
                return refresh(root, record)
            ledger = _read(root, record)
            if ledger is None:
                ex._refuse("experiment_not_armed", "Use advance_experiment before observing case execution.")
            ledger = _observe(record, ledger)
            _save(root, record, ledger)
            return _view(record, ledger, root=root, ledger=ledger)


def cancel_experiment(experiment_id: str) -> dict:
    with network_guard():
        root, record = _load(experiment_id)
        with ex._LOCK, ex._transaction(root):
            if len(record["cases"]) == 2:
                from harness.experiment_two_case import cancel
                return cancel(root, record)
            ledger = _read(root, record)
            if ledger is None:
                ex._refuse("experiment_not_armed", "This experiment has no advancement to cancel.")
            ledger["cancelled"] = True
            _save(root, record, ledger)
            return _view(record, _observe(record, ledger), root=root, ledger=ledger)


def assert_sweep_admission(baseline_project_id, cases):
    """Reserved names cannot bypass their ledger via a fresh session/direct call."""
    if any(isinstance(c, dict) and isinstance(c.get("new_name"), str)
           and re.match(r"^exp-[0-9a-f]{32}-", c["new_name"]) for c in cases):
        if _ADMISSION.get() != ex._digest({"baseline_project_id": baseline_project_id, "cases": cases}):
            ex._refuse("experiment_dispatch_required", "Arm this experiment and use its exact ordinary call.")


def invoke(session, tool, args, handler):
    """The ordinary loop calls this only after offering, auth and confirmation."""
    eid = getattr(session, "experiment_id", None)
    if not eid or tool not in ("run_sensitivity_sweep", "wait_for_job"):
        return _ordinary(session, args, handler)
    root, record = _load(eid)
    if len(record["cases"]) == 2:
        from harness.experiment_two_case import invoke as invoke_pair
        return invoke_pair(session, root, record, tool, args, handler)
    if tool == "wait_for_job":
        with network_guard():
            root, record = _load(eid)
            with ex._LOCK, ex._transaction(root):
                ledger = _read(root, record)
                tracked = ledger is not None and args.get("job_id") == ledger["job_id"]
        if not tracked:
            return _ordinary(session, args, handler)
    # A shared native fence precedes the baseline lock and survives session loss.
    from harness.experiment_reconciliation import submission_guard
    with submission_guard(eid) if tool == "run_sensitivity_sweep" else nullcontext():
        with network_guard() if tool == "run_sensitivity_sweep" else nullcontext():
            return _invoke(session, eid, tool, args, handler)


def _ordinary(session, args, handler):
    try:
        return handler(**args)
    finally:
        bound = getattr(session, "experiment_project_id", None)
        if bound and str(ex.PyPSAService.get_active_context().project_uuid) != bound:
            # Ordinary explicit activation detaches the chat, never its receipt.
            session.experiment_id = None
            session.experiment_project_id = None


def _invoke(session, eid, tool, args, handler, *, case_id=None):
    with network_guard():
        root, record = _load(eid, case_id)
        if record["project_id"] != session.experiment_project_id:
            ex._refuse("experiment_context_changed", "The experiment's project binding changed.")
        with ex._LOCK, ex._transaction(root):
            ledger = _read(root, record)
            if ledger is None:
                ex._refuse("experiment_checkpoint_unreadable", "The armed execution checkpoint is missing.")
            if "_parent_record" in record:
                from harness.experiment_two_case import check_admission
                check_admission(root, record, tool, args)
            if tool == "wait_for_job" and args.get("job_id") != ledger["job_id"]:
                ex._refuse("experiment_context_changed", "The tracked job changed before its wait.")
            if tool == "run_sensitivity_sweep" and ex._digest(args) != ex._digest(_submission(record)):
                ex._refuse("experiment_call_mismatch", "Use the exact returned case arguments.")
            if not _budget(record, ledger):
                ex._refuse("experiment_budget_exhausted", "The advancement attempt/time cap was reached. Read receipts or cancel; existing jobs are not aborted.")
            if ledger["cancelled"] or (tool == "run_sensitivity_sweep" and ledger["status"] != "ready"):
                ex._refuse("experiment_effect_already_reserved", "This case cannot be submitted again; inspect its existing receipt.")
            if tool == "run_sensitivity_sweep" and ex._public_preflight(record).get("status") != "validated":
                ex._refuse("experiment_context_changed", "The exact baseline changed after preflight.")
            if tool == "wait_for_job" and (ledger["status"] not in ("submitted", "running")
                    or isinstance(args.get("timeout_seconds", 20), bool)
                    or not isinstance(args.get("timeout_seconds", 20), (int, float))
                    or not math.isfinite(args.get("timeout_seconds", 20))
                    or not 0 <= args.get("timeout_seconds", 20) <= min(25, record["limits"]["max_elapsed_seconds"] - (_clock() - ledger["started_at"]))):
                ex._refuse("experiment_wait_unavailable", "Wait only for the submitted case within its remaining time cap.")
            if tool == "run_sensitivity_sweep":
                from services import chat_tools, dirty_state, project_registry, workflow_tools as wt
                with chat_tools._acting() as (db, user):
                    base = project_registry.resolve_project(db, user, record["project_id"])
                    saved = wt._load_context(base)
                    if project_registry.find_project(db, user, _name(record)) is not None:
                        ex._refuse("experiment_case_name_conflict", "The reserved scenario name already exists; inspect it before replanning.")
                if (dirty_state.is_dirty(ex.PyPSAService.get_active_context())
                        or wt._network_input_fingerprint(saved, exact=True) != record["preflight"]["exact_input_fingerprint"]):
                    ex._refuse("experiment_baseline_not_saved", "The saved baseline changed before dispatch.")
            attempt = {"id": uuid.uuid4().hex, "tool": tool, "status": "reserved", "started_at": _clock()}
            ledger["attempts"].append(attempt)
            if tool == "run_sensitivity_sweep":
                ledger["status"] = "submitting"
            _save(root, record, ledger)
            if tool == "run_sensitivity_sweep":
                from harness.experiment_reconciliation import prepare
                prepare(root, record, attempt)
        # No network lock is held across a job wait; its worker must be able to finish.
    token = _ADMISSION.set(ex._digest(args)) if tool == "run_sensitivity_sweep" else None
    try:
        result = handler(**args)
    except BaseException:
        with ex._LOCK, ex._transaction(root):
            ledger = _read(root, record)
            next(a for a in ledger["attempts"] if a["id"] == attempt["id"])["status"] = "uncertain"
            if tool == "run_sensitivity_sweep":
                ledger.update(status="uncertain", last_error="experiment_submission_unconfirmed")
            _save(root, record, ledger)
        raise
    finally:
        if token is not None:
            _ADMISSION.reset(token)
    with ex._LOCK, ex._transaction(root):
        ledger = _read(root, record)
        next(a for a in ledger["attempts"] if a["id"] == attempt["id"])["status"] = "returned"
        if tool == "run_sensitivity_sweep":
            child = (result.get("cases") or [{}])[0] if isinstance(result, dict) else {}
            job_id = child.get("job", {}).get("id")
            ledger.update(status="uncertain", last_error="experiment_submission_unconfirmed")
            if ex._valid_id(child.get("project_id")):
                from services import chat_tools, project_registry
                with chat_tools._acting() as (db, user):
                    row = project_registry.resolve_project(db, user, child["project_id"])
                    if row.name == _name(record) and str(row.parent_project_id) == record["project_id"]:
                        ledger["case_project_id"] = str(row.id)
                        if ex._valid_id(job_id):
                            ledger.update(job_id=job_id, status="submitted", last_error=None)
        _save(root, record, ledger)
    return result
