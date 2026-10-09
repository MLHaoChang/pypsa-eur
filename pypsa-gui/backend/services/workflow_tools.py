"""Bounded workflow helpers over the existing project, solve and study services.

No model calls, alternate queue, or confirmation bypass lives here. The sweep
creates saved child projects in isolated contexts; it never activates them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
from collections import Counter, OrderedDict
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, ValidationError

from services.pypsa_service import PyPSAService


def _refuse(kind, message, status=422):
    raise HTTPException(status, {"error_kind": kind, "message": message})


@contextmanager
def _isolated(ctx):
    """A private view even inside an SSE turn's shared binding cell."""
    cell = PyPSAService._turn_cell.set(None)
    binding = PyPSAService.bind_request_context(ctx)
    try:
        yield
    finally:
        PyPSAService.reset_request_context(binding)
        PyPSAService._turn_cell.reset(cell)


def _load_context(project):
    from routers.projects import _hydrate_context_from_disk
    from services import project_registry
    ctx = PyPSAService.build_context()
    ctx.org_id, ctx.project_uuid = str(project.org_id), str(project.id)
    ctx.storage_dir = str(project_registry.project_dir(project))
    _hydrate_context_from_disk(ctx, Path(ctx.storage_dir), project.name)
    return ctx


def use_toolset(domain: str) -> dict:
    from harness.toolsets import DOMAINS
    from services import chat_tools
    if domain not in DOMAINS:
        _refuse("invalid_toolset", "Choose one of: " + ", ".join(DOMAINS))
    session = chat_tools.chat_session()
    if session is None:
        _refuse("no_chat_session", "Toolsets require an active chat session.", 409)
    with session._lock:
        session.toolset = domain
    return {"toolset": domain, "effective": "next_model_request",
            "note": "Project eligibility and the exact offered-tool allowlist still apply."}


def wait_for_job(job_id: str, timeout_seconds: float = 20) -> dict:
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools
    from services.solve_queue import _TERMINAL
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 25:
        _refuse("invalid_wait_timeout", "timeout_seconds must be between 0 and 25.")
    session = chat_tools.chat_session()
    stop = session.abort_event if session is not None else threading.Event()
    if session is not None:
        with session._lock:
            session.job_wait_progress = None
    started = time.monotonic()
    deadline = started + timeout_seconds
    while True:
        # Fresh authority on every poll: account/ACL revocation during a wait
        # must not turn a terminal result into a disclosure.
        with chat_tools._acting() as (db, user):
            job = _visible_job_or_404(db, user, job_id)
        terminal = job["status"] in _TERMINAL
        cancelled = stop.is_set()
        if session is not None:
            with session._lock:
                session.job_wait_progress = {"job_id": job_id, "status": job["status"], "position": job.get("position")}
        if terminal or cancelled or time.monotonic() >= deadline:
            return {"job": job, "terminal": terminal, "completed": job["status"] == "completed",
                    "timed_out": not terminal and not cancelled,
                    "wait_cancelled": cancelled, "waited_seconds": round(time.monotonic() - started, 3),
                    "note": "Cancelling this wait does not abort the job; use solve_queue_abort to stop it."}
        stop.wait(min(0.25, max(0, deadline - time.monotonic())))


def _read_json(path):
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError):
        _refuse("unreadable_study_artifact", "A study artifact cannot be read; rerun or repair the study.", 409)


def _file_stamp(path):
    try:
        stat = path.stat()
        return [stat.st_mtime_ns, stat.st_size]
    except FileNotFoundError:
        return None


def _active_job(project):
    from services import project_registry
    from services.solve_queue import solve_queue
    key = project_registry.registry_key(project)
    return next((job for job in solve_queue.list_jobs() if job.get("project_key") == key and job["status"] in ("queued", "running")), None)


def _study_fingerprint(root, config):
    # Include all top-level result tables and error files. Large source files
    # use identity+mtime+size, so reading a summary never rereads a year of NC.
    paths = [root / "config.json", root / "templates_overlay.json"]
    run = root / "run"
    paths.extend(sorted(p for p in run.glob("*") if p.is_file()))
    for key in ("from_network", "from_external", "from_external_loads"):
        if config.get(key):
            paths.append(Path(config[key]))
    if config.get("from_dispatch"):
        source = Path(config["from_dispatch"])
        paths.extend(source / name for name in ("manifest.json", "dispatch.csv", "loads.csv"))
    stamps = [(str(path), _file_stamp(path)) for path in paths]
    return hashlib.sha256(json.dumps(stamps).encode()).hexdigest()


def _study_state(project):
    from services import gridspine_service as gs, project_registry
    gs.require_planning(project)
    root = project_registry.project_dir(project) / "gridspine"
    if not (root / "config.json").is_file():
        _refuse("study_config_missing", "The study configuration is missing.", 409)
    config = gs.config_for_dir(root).to_json()
    manifest = _read_json(root / "run" / "manifest.json")
    state = gs.get_stage_status(project)
    active = _active_job(project)
    previous = manifest.get("config")
    ignored = {"outdir", "hours"}  # Imported sources determine their own hours.
    config_matches = isinstance(previous, dict) and all(previous.get(k) == v for k, v in config.items() if k not in ignored)
    if not config.get("from_network") and not config.get("from_external") and not config.get("from_dispatch"):
        config_matches = config_matches and isinstance(previous, dict) and previous.get("hours") == config.get("hours")
    modified_overlay = _file_stamp(root / "templates_overlay.json")
    manifest_stamp = _file_stamp(root / "run" / "manifest.json")
    source_paths = [Path(config[k]) for k in ("from_network", "from_external", "from_external_loads") if config.get(k)]
    if config.get("from_dispatch"):
        source_paths += [Path(config["from_dispatch"]) / name for name in ("manifest.json", "dispatch.csv", "loads.csv")]
    stamps = [modified_overlay] + [_file_stamp(p) for p in source_paths]
    source_changed = any(stamp is None for stamp in stamps[1:]) or (manifest_stamp is not None and any(stamp and stamp[0] > manifest_stamp[0] for stamp in stamps))
    fresh = state.get("status") == "completed" and active is None and config_matches and not source_changed
    return root, config, {"status": state.get("status"), "active_job_id": None if active is None else str(active.get("id")),
                          "fresh": fresh, "config_matches_run": config_matches, "source_or_overlay_changed": source_changed,
                          "run_id": hashlib.sha256(json.dumps([str(project.id), manifest_stamp]).encode()).hexdigest()[:20],
                          "fingerprint": _study_fingerprint(root, config),
                          "note": "Freshness uses the saved config and artifact/source file timestamps; this is steady-state evidence, not dynamic compliance."}


def project_readiness(project_id: str | None = None) -> dict:
    from services import chat_tools, dirty_state, gridspine_service as gs, project_registry
    active = PyPSAService.get_active_context()
    project_id = project_id or active.project_uuid
    with chat_tools._acting() as (db, user):
        if project_id is None:
            return {"project_id": None, "kind": "unbound", "ready": False,
                    "blockers": ["Create or save a project first."], "next_tools": ["create_project_from_template", "save_project"]}
        project = project_registry.resolve_project(db, user, project_id)
        identity = {"project_id": str(project.id), "name": project.name, "kind": gs.kind_of(project),
                    "active": str(active.project_uuid) == str(project.id)}
        if gs.kind_of(project) == gs.PLANNING_DYNAMICS:
            try:
                _, _, state = _study_state(project)
            except HTTPException as exc:
                return {**identity, "ready": False, "blockers": [exc.detail], "next_tools": ["gridspine_get_config"]}
            blockers = ["A study job is still active."] if state["active_job_id"] else []
            return {**identity, "ready": not blockers, "blockers": blockers, "evidence": state,
                    "next_tools": ["wait_for_job"] if blockers else (["get_study_evidence"] if state["fresh"] else ["gridspine_get_config", "gridspine_run_pipeline"])}
        saved = (project_registry.project_dir(project) / "network.nc").is_file()
        if not saved and not identity["active"]:
            return {**identity, "saved": False, "ready": False, "blockers": ["The project has no saved network."], "next_tools": ["save_project"]}
        ctx = active if identity["active"] else _load_context(project)
        with _isolated(ctx):
            validation = chat_tools.validate_network()
            cfg = chat_tools.get_solver_config()
            solvers = chat_tools.check_solver_availability()
            status = chat_tools.get_simulation_status()
            freshness = chat_tools.dispatch_status()
        blockers = []
        if validation.get("deferred"):
            blockers.append("Validation is deferred while a solver or study is active.")
        if validation.get("errors"):
            blockers.append("Network validation has errors.")
        if not solvers.get(cfg.get("solver_name"), False):
            blockers.append("The selected solver is unavailable.")
        if ctx.user_ts_unreadable:
            blockers.append("The saved user time-series store cannot be read.")
        dirty = dirty_state.is_dirty(ctx)
        return {**identity, "saved": saved, "dirty": dirty, "ready": not blockers,
                "blockers": blockers, "validation": {**validation, "issues": validation.get("issues", [])[:12]},
                "validation_issue_count": len(validation.get("issues", [])), "solvers": solvers,
                "solver_name": cfg.get("solver_name"), "simulation": status, "dispatch": freshness,
                "user_timeseries_count": len(ctx.user_ts), "user_timeseries_unreadable": ctx.user_ts_unreadable,
                "next_tools": ["validate_network", "update_solver_config"] if blockers else (["save_project"] if dirty else (["get_study_evidence"] if freshness.get("state") == "fresh" else ["run_simulation"]))}


_EVIDENCE_CACHE = OrderedDict()
_EVIDENCE_LOCK = threading.Lock()


def _fit_evidence_page(out):
    """Keep counts and page cursors visible through the harness result cap."""
    from harness.results import _RESULT_CONTENT_CAP
    def oversized():
        return len(json.dumps(out, default=str)) > _RESULT_CONTENT_CAP - 128
    original_returned = out["returned"]
    while len(out["items"]) > 1 and oversized():
        out["items"].pop()
    if out["items"] and oversized():
        original = out["items"][0]
        width = 512
        while oversized() and width >= 32:
            clipped = [key for key, value in original.items() if isinstance(value, str) and len(value) > width]
            out["items"][0] = {key: value[:width] + "…" if key in clipped else value for key, value in original.items()}
            out["items"][0]["_truncated_fields"] = clipped
            width //= 2
    out["returned"] = len(out["items"])
    out["has_more"] = out["offset"] + out["returned"] < out["total_count"]
    if out["returned"] < original_returned:
        out["limit_clamped_to"] = out["returned"]
    return out


def get_study_evidence(project_id: str | None = None, section: str = "connection", check: str | None = None,
                       status: str | None = None, hour: int | None = None, assessment_id: str | None = None,
                       bus: str | None = None, offset: int = 0, limit: int = 10, compare_to: str | None = None) -> dict:
    from services import chat_tools, gridspine_service as gs, project_registry
    if section not in ("connection", "capacity", "ranked_snapshots", "simulation"):
        _refuse("invalid_evidence_section", "Unknown evidence section.")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        _refuse("invalid_evidence_page", "offset must be non-negative and limit between 1 and 50.")
    if hour is not None and (type(hour) is not int or hour < 0):
        _refuse("invalid_evidence_hour", "hour must be a non-negative integer.")
    if section != "connection" and (check is not None or assessment_id is not None):
        _refuse("invalid_evidence_filter", "check and assessment_id apply only to connection evidence.")
    if section == "ranked_snapshots" and bus is not None:
        _refuse("invalid_evidence_filter", "Ranked snapshots do not have a bus filter.")
    project_id = project_id or PyPSAService.get_active_context().project_uuid
    if not project_id:
        _refuse("project_required", "Activate a project or provide project_id.", 409)
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, project_id)
        if section == "simulation":
            if any(v is not None for v in (check, status, hour, assessment_id, bus)):
                _refuse("invalid_evidence_filter", "Simulation summaries do not accept study-row filters.")
            ctx = PyPSAService.get_context(project_registry.registry_key(project))
            if ctx is not None and ctx.results_unsaved:
                _refuse("unsaved_results", "Save this project's changes before interpreting saved evidence.", 409)
            summary = chat_tools._model_to_dict(chat_tools.get_project_results_summary(str(project.id)))
            saved = _load_context(project)
            from services.dispatch_status import dispatch_status
            fresh = dispatch_status(saved.network) == "fresh" and _active_job(project) is None
            objective = getattr(saved.network, "_objective", None)
            out = {"project_id": str(project.id), "section": section, "basis": "last_saved_results", "available": fresh,
                   "summary": chat_tools._scenario_headlines(summary), "objective": objective if fresh else None,
                   "fingerprint": _network_input_fingerprint(saved), "cache_hit": False}
            if compare_to:
                # Detailed carrier/period tables can consume the entire result
                # budget. Keep the saved headline deltas; callers can request
                # focused tables through the existing compare_scenarios tool.
                out["comparison"] = chat_tools.compare_scenarios(compare_to, str(project.id), focus="overview")
                out["comparison"].pop("focus_section", None)
            return out
        if compare_to is not None:
            _refuse("invalid_evidence_filter", "compare_to is available for simulation evidence.")
        _, _, evidence = _study_state(project)
        if not evidence["fresh"]:
            return {"project_id": str(project.id), "section": section, "evidence": evidence, "available": False,
                    "items": [], "note": "Wait for the active job or rerun the study before interpreting these results."}
        key = (str(project.id), evidence["fingerprint"], section, check, status, hour, assessment_id, bus, offset, limit)
        with _EVIDENCE_LOCK:
            cached = _EVIDENCE_CACHE.get(key)
            if cached is not None:
                _EVIDENCE_CACHE.move_to_end(key)
                return {**copy.deepcopy(cached), "cache_hit": True}
        if section == "connection":
            rows = gs.get_connection(project, assessment_id=assessment_id, hour=hour)["rows"]
        elif section == "capacity":
            rows = gs.get_capacity(project, bus=bus, hour=hour)["rows"]
            rows = [{**row, "status": ("preexisting_violation" if row.get("binding_preexisting") else "ac_computed" if row.get("method") == "ac" else "dc_only")} for row in rows]
        else:
            rows = gs.list_ranked_snapshots(project)
            run_state = gs.get_stage_status(project)
            converged = set(run_state.get("converged_hours", []))
            rows = [{**row, "status": "converged" if row["hour"] in converged else "not_converged"} for row in rows]
            if hour is not None:
                rows = [r for r in rows if r.get("hour") == hour]
        if bus is not None:
            rows = [r for r in rows if r.get("bus") == bus]
        if check is not None:
            rows = [r for r in rows if r.get("check") == check]
        counts = dict(sorted(Counter(str(r.get("status", "unknown")) for r in rows).items()))
        failed = sum(1 for r in rows if r.get("status") == "fail")
        if status is not None:
            rows = [r for r in rows if r.get("status") == status]
        page = chat_tools._paginate(rows, offset, limit)
        # Detect writes during the read. Never cache a mixed run under an old stamp.
        _, _, after = _study_state(project)
        if after["fingerprint"] != evidence["fingerprint"] or not after["fresh"]:
            _refuse("study_changed_during_read", "The study changed during this read; retry after completion.", 409)
        out = {"project_id": str(project.id), "section": section, "available": True, "evidence": evidence,
               "counts_before_status_filter": counts, "failed_count_before_status_filter": failed,
               **page, "cache_hit": False,
               "note": "Counts cover every matching row before the status filter, not just this page. Use check='connection' for overall connection pass/fail counts."}
        out = _fit_evidence_page(out)
        with _EVIDENCE_LOCK:
            _EVIDENCE_CACHE[key] = copy.deepcopy(out)
            while len(_EVIDENCE_CACHE) > 32:
                _EVIDENCE_CACHE.popitem(last=False)
        return out


_ATTRIBUTES = {
    "Generator": {"marginal_cost", "capital_cost", "p_nom", "p_nom_min", "p_nom_max", "p_nom_extendable", "p_min_pu", "p_max_pu", "efficiency"},
    "Load": {"p_set", "q_set"},
    "Line": {"s_nom", "s_nom_min", "s_nom_max", "s_nom_extendable", "s_max_pu", "capital_cost", "r", "x"},
    "Link": {"p_nom", "p_nom_min", "p_nom_max", "p_nom_extendable", "capital_cost", "marginal_cost", "efficiency", "p_min_pu", "p_max_pu"},
    "StorageUnit": {"p_nom", "p_nom_min", "p_nom_max", "p_nom_extendable", "capital_cost", "marginal_cost", "max_hours", "efficiency_store", "efficiency_dispatch"},
    "Store": {"e_nom", "e_nom_min", "e_nom_max", "e_nom_extendable", "capital_cost", "marginal_cost", "e_min_pu", "e_max_pu"},
}


class SensitivityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component_class: Literal["Generator", "Load", "Line", "Link", "StorageUnit", "Store"]
    names: list[str] = Field(min_length=1, max_length=100)
    attribute: str
    value: StrictBool | StrictFloat | StrictInt


class SensitivityCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_name: str = Field(min_length=1, max_length=64)
    changes: list[SensitivityChange] = Field(min_length=1, max_length=20)


def _network_input_fingerprint(ctx):
    """Hash model inputs, excluding solver outputs so a solve can be reused."""
    # PyPSA assigns a slack control while solving. Canonicalise controls on a
    # private copy, retaining user PV/slack choices, rather than mistaking the
    # deterministic PQ→Slack assignment for a parameter edit.
    n = ctx.network.copy()
    n.determine_network_topology()
    for subnet in n.sub_networks.obj:
        subnet.find_bus_controls()
    parts = [n.snapshots.to_series().to_json(date_format="iso"), n.snapshot_weightings.to_json(),
             n.investment_period_weightings.to_json(),
             json.dumps(asdict(ctx.solver_state["solver_config"]), sort_keys=True, default=str)]
    for component in n.components:
        defaults = component.defaults
        columns = [name for name in defaults.index if "Input" in str(defaults.at[name, "status"]) and name in component.static.columns]
        parts.append(component.static[columns].to_json())
        for name in sorted(component.dynamic):
            if name in defaults.index and "Input" in str(defaults.at[name, "status"]):
                parts.append(component.dynamic[name].to_json(date_format="iso"))
    parts.extend(str(key) + series.to_json(date_format="iso") for key, series in sorted(ctx.user_ts.items()))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


_SWEEP_LOCK = threading.Lock()


def run_sensitivity_sweep(baseline_project_id: str, cases: list[dict]) -> dict:
    """Preflight all cases, then persist/enqueue isolated children. No activation."""
    from models.schemas import CreateScenarioRequest
    from routers import projects
    from services import chat_tools, dirty_state, gridspine_service as gs, project_registry
    from services.safe_names import fold
    if not isinstance(cases, list) or not 1 <= len(cases) <= 4:
        _refuse("invalid_sensitivity_cases", "Provide between 1 and 4 cases per sweep.")
    try:
        parsed = [SensitivityCase.model_validate(case) for case in cases]
        requests = [CreateScenarioRequest(name=case.new_name, description="Harness sensitivity scenario") for case in parsed]
    except ValidationError:
        _refuse("invalid_sensitivity_cases", "Cases require valid unique names and typed component changes.")
    if len({fold(request.name) for request in requests}) != len(parsed):
        _refuse("invalid_sensitivity_cases", "Scenario names must be unique.")
    if not _SWEEP_LOCK.acquire(blocking=False):
        _refuse("sensitivity_busy", "Another sweep is being prepared; retry later.", 409)
    outcomes = []
    try:
        with chat_tools._acting() as (db, user):
            base = project_registry.resolve_project(db, user, baseline_project_id)
            if gs.kind_of(base) != gs.CAPACITY_EXPANSION:
                _refuse("unsupported_sensitivity_project", "This sweep supports network component parameters; use GridSpine config tools for study refinement.", 409)
            resident = PyPSAService.get_context(project_registry.registry_key(base))
            if resident is None and str(PyPSAService.get_active_context().project_uuid) == str(base.id):
                resident = PyPSAService.get_active_context()
            if resident is not None and (dirty_state.is_dirty(resident) or resident.solver_state.get("status") == "running"):
                _refuse("baseline_not_saved", "Save the baseline and wait for its solve before branching.", 409)
            if _active_job(base) is not None:
                _refuse("baseline_busy", "Wait for the baseline's queued job before branching.", 409)
            base_dir = project_registry.project_dir(base)
            base_files = [base_dir / name for name in ("network.nc", "solver_config.json", "user_ts.json")]
            base_stamps = [_file_stamp(path) for path in base_files]
            base_ctx = _load_context(base)
            base_fingerprint = _network_input_fingerprint(base_ctx)
            prepared = []
            # All changes are validated on disposable copies before creating any project.
            for case, request in zip(parsed, requests):
                ctx = PyPSAService.build_context()
                ctx.network = base_ctx.network.copy()
                ctx.org_id = base_ctx.org_id
                ctx.user_ts = copy.deepcopy(base_ctx.user_ts)
                ctx.solver_state["solver_config"] = copy.deepcopy(base_ctx.solver_state["solver_config"])
                with _isolated(ctx):
                    for change in case.changes:
                        if change.attribute not in _ATTRIBUTES[change.component_class]:
                            _refuse("invalid_sensitivity_attribute", "The attribute is outside the supported engineering input allowlist.")
                        boolean = change.attribute.endswith("_extendable")
                        if boolean != isinstance(change.value, bool) or (not boolean and not math.isfinite(change.value)):
                            _refuse("invalid_sensitivity_value", "Use finite numbers for numeric inputs and booleans for extendable flags.")
                        chat_tools.bulk_update_components(change.component_class, change.names, {change.attribute: change.value})
                    validation = chat_tools.validate_network()
                    if not validation.get("ok") or validation.get("deferred"):
                        _refuse("sensitivity_preflight_failed", "A sensitivity case fails network validation; no cases were created.", 409)
                expected = _network_input_fingerprint(ctx)
                spec = {"baseline_id": str(base.id), "baseline_fingerprint": base_fingerprint, "case": case.model_dump(), "input_fingerprint": expected}
                existing = project_registry.find_project(db, user, case.new_name)
                if existing is not None:
                    # find_project checks org/name uniqueness, not tree ACLs.
                    # Authorize BEFORE reading any existing case's provenance.
                    existing = project_registry.resolve_project(db, user, str(existing.id))
                    old = _read_json(project_registry.project_dir(existing) / "sensitivity_case.json")
                    if existing.parent_project_id != base.id or any(old.get(k) != v for k, v in spec.items()) or _network_input_fingerprint(_load_context(existing)) != expected:
                        _refuse("sensitivity_name_conflict", "A case name already exists with different inputs; choose a new name.", 409)
                    existing_ctx = PyPSAService.get_context(project_registry.registry_key(existing))
                    if existing_ctx is not None and dirty_state.is_dirty(existing_ctx):
                        _refuse("sensitivity_case_dirty", "An existing sensitivity case has unsaved edits.", 409)
                prepared.append((ctx, request, spec, existing))
            if [_file_stamp(path) for path in base_files] != base_stamps:
                _refuse("baseline_changed", "The saved baseline changed during preparation; retry from its current state.", 409)
            for ctx, request, spec, existing in prepared:
                if chat_tools.chat_session() is not None and chat_tools.chat_session().abort_event.is_set():
                    return {"baseline_project_id": str(base.id), "status": "cancelled", "cases": outcomes}
                result = {"name": request.name, "created": existing is None, "reused": False}
                try:
                    if [_file_stamp(path) for path in base_files] != base_stamps:
                        _refuse("baseline_changed", "The saved baseline changed during preparation; inspect saved cases before retrying.", 409)
                    if existing is None:
                        info = projects.create_scenario(str(base.id), request, db=db, user=user)
                        result["project_id"] = str(info.id)
                        existing = project_registry.resolve_project(db, user, str(info.id))
                        ctx.loaded_project = existing.name
                        ctx.org_id, ctx.project_uuid = str(existing.org_id), str(existing.id)
                        ctx.storage_dir = str(project_registry.project_dir(existing))
                        # Invalidate copied dispatch/results; only the new queued solve
                        # establishes results for the altered inputs.
                        with _isolated(ctx):
                            from services.dispatch_status import clear_dispatch
                            clear_dispatch(ctx.network)
                            ctx.network._objective = None
                            ctx.network._objective_constant = None
                        ctx.solver_state.update(status="idle", objective=None, condition=None)
                        projects._enforce_project_lock(db, existing, user)
                        projects._save_context(ctx, existing.name, storage_dir=Path(ctx.storage_dir), db=db, user=user, project_row=existing)
                        from services.atomic_io import atomic_write_bytes
                        atomic_write_bytes(Path(ctx.storage_dir) / "sensitivity_case.json", json.dumps(spec, sort_keys=True).encode())
                    result["project_id"] = str(existing.id)
                    saved_ctx = _load_context(existing)
                    from services.dispatch_status import dispatch_status
                    solved = dispatch_status(saved_ctx.network) == "fresh" and getattr(saved_ctx.network, "objective", None) is not None and _active_job(existing) is None
                    if solved:
                        # Four reusable cases must still fit the harness result
                        # cap. Read comparisons separately with evidence tools.
                        result.update(reused=True, status="completed", objective=saved_ctx.network.objective)
                    else:
                        result.update(status="queued", job=chat_tools.solve_queue_enqueue(str(existing.id)))
                    outcomes.append(result)
                except Exception as exc:
                    result.update(status="failed", error_kind=(exc.detail.get("error_kind", "sensitivity_failed") if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else "sensitivity_failed"))
                    outcomes.append(result)
                    return {"baseline_project_id": str(base.id), "status": "partial", "cases": outcomes,
                            "note": "Completed preparations remain saved. Inspect failed cases and retry; nothing was deleted."}
            return {"baseline_project_id": str(base.id), "baseline_fingerprint": base_fingerprint, "status": "submitted", "cases": outcomes,
                    "next_tools": ["wait_for_job", "get_study_evidence"],
                    "note": "Baseline and active project are unchanged. After jobs finish, read simulation evidence with compare_to=baseline_project_id. Static parameter changes preserve existing time-series overrides."}
    finally:
        _SWEEP_LOCK.release()
