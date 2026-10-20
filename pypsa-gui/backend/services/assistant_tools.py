"""Small references, immutable previews and artifacts over existing app services."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import re
import threading
import uuid
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from fastapi import HTTPException
from pydantic import ValidationError
from services.pypsa_service import PyPSAService
from services import assistant_tasks as tasks
from services import workflow_tools as workflow

_CHART_LOCK = threading.Lock()
_REVIEWED_PREVIEW: ContextVar[str | None] = ContextVar('reviewed_preview', default=None)


@contextmanager
def confirmed_preview(review_sha256: str):
    """Bind a server-captured review to this invocation, never model arguments."""
    token = _REVIEWED_PREVIEW.set(review_sha256)
    try:
        yield
    finally:
        _REVIEWED_PREVIEW.reset(token)


def _preview_digest(record):
    body = {k: v for k, v in record.items() if k != 'review_sha256'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _preview_review(record):
    """Require the complete bounded review; legacy/clipped records fail closed."""
    try:
        rows = record['diff']
        valid = (record.get('version') == 1 and isinstance(rows, list)
                 and 1 <= len(rows) <= 20 and record['changes_total'] == len(rows)
                 and record['review_sha256'] == _preview_digest(record))
        if not valid:
            raise ValueError('incomplete or changed preview')
        out = {'preview_id': record['preview_id'], 'project_id': record['project_id'],
               'changes_total': len(rows), 'reviewed_changes': copy.deepcopy(rows),
               'review_sha256': record['review_sha256'],
               'note': 'Apply these typed changes to unchanged inputs. Existing time-series overrides remain. Changes are left unsaved.'}
        if len(json.dumps(out, allow_nan=False)) > 3800:
            raise ValueError('oversized review')
        return out
    except (KeyError, TypeError, ValueError, OverflowError):
        workflow._refuse('invalid_preview', 'Preview is changed, incomplete or too large. Create a new preview with at most 20 values; split large changes into smaller reviews.', 409)


def _project():
    from services import chat_tools, project_registry
    ctx = PyPSAService.get_active_context()
    if not ctx.project_uuid:
        workflow._refuse("no_active_project", "Create, save and activate a project first.", 409)
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, ctx.project_uuid)
        return project, project_registry.project_dir(project), str(user.id)


def _file(file_id):
    from services import upload_service
    project, root, owner = _project()
    meta = upload_service.get_upload_meta(project.name, file_id, project_dir=root)
    path = upload_service.get_upload_path(project.name, file_id, project_dir=root)
    if path.stat().st_size > upload_service.MAX_FILE_BYTES:
        workflow._refuse("file_too_large", "File exceeds the 25 MB inspection/import limit.", 413)
    return project, root, owner, meta, path


def get_file_delivery(file_id: str) -> dict:
    project, _, _, meta, _ = _file(file_id)
    return {"file_id": meta.file_id, "filename": meta.filename, "mime": meta.mime,
            "size": meta.size, "kind": meta.kind, "sha256": meta.sha256,
            "project_id": str(project.id), "download_url":
            f"/api/projects/{quote(str(project.id), safe='')}/uploads/{meta.file_id}/blob"}


@lru_cache(maxsize=2)
def _search_index(catalogue_json):
    catalogue = json.loads(catalogue_json)
    return [(tool, set(re.findall(r"[a-z0-9]+", tool["name"].lower())),
             set(re.findall(r"[a-z0-9]+", tool["description"].lower()))) for tool in catalogue]


# OJ-45: the owner's core features as families. `needs` says what the project
# must have; `changes` families are refused by a read-only personal rule.
_FAMILIES = (
    {"family": "projects", "example": "Open oj-grid and tell me what's in it.", "needs": None,
     "tools": ("list_projects", "resolve_project_target", "activate_project", "get_project_statistics")},
    {"family": "results", "example": "Show me the load flow results of the latest calculation.", "needs": "run",
     "tools": ("get_results", "get_loadflow_summary", "list_project_runs", "get_run_evidence", "read_result")},
    {"family": "checks", "example": "Do the inputs make sense? Check the network.", "needs": "project",
     "tools": ("validate_network", "diagnose_network", "project_readiness"),
     "note": "These check the inputs; rules for whether results are plausible do not exist yet."},
    {"family": "edits", "example": "Set the battery's max hours to 6 and both lines to 400 MVA.", "needs": "project",
     "changes": True, "tools": ("update_component", "bulk_update_components", "batch_create_components")},
    {"family": "sensitivities", "example": "Run the gas price at +20% and compare it with the baseline.",
     "needs": "project", "changes": True,
     "tools": ("run_sensitivity_sweep", "create_scenario", "compare_project_runs", "compare_scenarios")},
    {"family": "studies", "example": "Run the simulation and show me what changed.", "needs": "project",
     "changes": True, "tools": ("run_simulation", "run_ac_pf_stage", "run_mc_study", "wait_for_job")},
    {"family": "reports", "example": "Create a report of this study.", "needs": "project", "changes": True,
     "tools": ("generate_report", "list_reports", "export_report_docx")},
)


def _capability_overview() -> dict:
    from harness import restrictions
    from harness.catalogue import TOOLS
    from services import chat_tools, project_acl, project_registry, run_evidence

    session = chat_tools.chat_session()
    hints = getattr(session, "turn_ui_context", None) or {}
    mode = "guided" if isinstance(hints, dict) and hints.get("ui_mode") == "guided" else "expert"
    known = {t["name"] for t in TOOLS}
    ctx = PyPSAService.get_active_context()
    project = rule = role = None
    has_run = False
    if ctx.project_uuid:
        with chat_tools._acting() as (db, user):
            row = project_registry.resolve_project(db, user, ctx.project_uuid)
            rule = restrictions.read(db, user.id, row.id)["mode"]
            role = "manager" if project_acl.can_manage_membership(db, user, row) else "member"
            project = {"id": str(row.id), "name": row.name}
        root, project_id, _owner = run_evidence._authority()
        has_run = any(r["status"] == "completed" for r in run_evidence._all_records(root, project_id))
    families = []
    for spec in _FAMILIES:
        reason = None
        if spec["needs"] and project is None:
            reason = "Open or save a project first."
        elif spec["needs"] == "run" and not has_run:
            reason = "No completed run yet; run a simulation first."
        elif spec.get("changes") and rule == "read_only":
            reason = "Your personal rule for this project is read-only."
        note = spec.get("note")
        if spec.get("changes") and not reason and (mode == "guided" or rule == "confirm_changes"):
            note = "Each change shows a confirmation card before it is applied."
        families.append({"family": spec["family"], "available": reason is None, "reason": reason,
                         "example": spec["example"], "note": note,
                         "tools": [name for name in spec["tools"] if name in known]})
    return {"overview": True, "project": project, "role": role, "mode": mode, "personal_rule": rule,
            "families": families,
            "note": "Discovery grants no permissions; every tool still checks access when used."}


def find_capabilities(goal: str | None = None, limit: int = 3, overview: bool = False) -> dict:
    from harness.catalogue import TOOLS, safety_tier_for
    from harness.toolsets import filter_tools
    from harness.providers.wiring import _tools_payload
    from services import chat_tools
    if overview is True:
        return _capability_overview()
    if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 1000 or type(limit) is not int or not 1 <= limit <= 8:
        workflow._refuse("invalid_capability_query", "Use a short goal and a limit of 1–8.")
    words = set(re.findall(r"[a-z0-9]+", goal.lower())) - {"the", "a", "to", "and", "my", "of", "for", "with"}
    aliases = {"chart": {"plot", "chart"}, "simulate": {"simulation", "solve"},
               "download": {"export", "delivery"}, "upload": {"import", "upload"},
               "improve": {"refinement", "sensitivity"}, "plan": {"task", "workflow"}}
    for word in list(words):
        words.update(aliases.get(word, ()))
    session = chat_tools.chat_session()
    domain = getattr(session, "toolset", "all")
    eligible = {t["name"] for t in _tools_payload()}
    visible = {t["name"] for t in filter_tools(TOOLS, domain)}
    index = _search_index(json.dumps(TOOLS, sort_keys=True))
    ranked = sorted(index, key=lambda row: (int(row[0]["name"] in goal),
                    5 * len(words & row[1]) + len(words & row[2])), reverse=True)
    items = []
    for tool, names, description in ranked:
        if not words & (names | description) and tool["name"] not in goal:
            continue
        schema = tool["input_schema"]
        items.append({"name": tool["name"], "description": tool["description"][:350],
                      "safety_tier": safety_tier_for(tool["name"]), "required": schema["required"],
                      "inputs": list(schema["properties"]),
                      "eligible_for_project": tool["name"] in eligible,
                      "in_current_toolset": tool["name"] in visible})
        if len(items) == limit:
            break
    out = {"goal": goal, "toolset": domain, "items": items,
           "note": "Discovery grants no permissions. Use the named tool normally; use_toolset('all') restores a hidden domain. Missing prerequisites are described by each tool and project_readiness."}
    while len(json.dumps(out)) > 3800 and len(items) > 1:
        items.pop()
    return out


def inspect_import(file_id: str) -> dict:
    _, _, _, meta, path = _file(file_id)
    suffix = Path(meta.filename).suffix.lower()
    formats = {".nc": "netcdf", ".zip": "csv_bundle", ".xlsx": "excel", ".xls": "excel", ".m": "matpower"}
    base = {**get_file_delivery(file_id), "native_import_format": formats.get(suffix),
            "units": "Not inferred; confirm units and column mappings before importing.",
            "note": "Inspection does not mutate the network. Native imports replace the current network and receive confirmation."}
    if suffix not in (".csv", ".xlsx", ".xls"):
        return {**base, "tabular_inspection": "unsupported", "next_tool": "import_uploaded_network" if suffix in formats else None}
    if suffix == ".csv":
        sheets = {"csv": pd.read_csv(path, nrows=200)}
    else:
        # Bound decompressed office data before pandas parses the workbook.
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > 50 * 1024 * 1024:
                    workflow._refuse("import_expansion_too_large", "Workbook expands beyond the 50 MB inspection limit.", 413)
        with pd.ExcelFile(path) as book:
            sheet_names = book.sheet_names
            sheets = {name: pd.read_excel(book, sheet_name=name, nrows=200) for name in sheet_names[:5]}
            base["total_sheets"] = len(sheet_names)
    tables = []
    for name, table in sheets.items():
        columns = []
        for column in table.columns[:20]:
            values = table[column]
            entry = {"name": str(column)[:100], "dtype": str(values.dtype), "missing_in_sample": int(values.isna().sum())}
            if any(token in str(column).lower() for token in ("time", "date", "snapshot")):
                dates = pd.to_datetime(values, errors="coerce", utc=True)
                entry["time_axis"] = {"invalid": int(dates.isna().sum()), "duplicates": int(dates.duplicated().sum()),
                                      "monotonic": bool(dates.is_monotonic_increasing)}
            columns.append(entry)
        tables.append({"sheet": str(name)[:100], "sample_rows": len(table), "row_limit": 200,
                       "sample_may_be_truncated": len(table) == 200, "total_columns": len(table.columns), "columns": columns})
    base["tables"] = tables
    while len(json.dumps(base)) > 3800:
        if len(tables) > 1:
            tables.pop()
        elif tables[0]["columns"]:
            tables[0]["columns"].pop()
        else:
            break
        base["metadata_truncated"] = True
    return base


def import_uploaded_network(file_id: str, format: str) -> dict:
    import base64
    from services import chat_tools
    handlers = {"netcdf": chat_tools.import_network_nc, "csv_bundle": chat_tools.import_csv_bundle,
                "excel": chat_tools.import_excel, "matpower": chat_tools.import_matpower}
    if format not in handlers:
        workflow._refuse("invalid_import_format", "Choose netcdf, csv_bundle, excel or matpower.")
    _, _, _, meta, path = _file(file_id)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != meta.sha256:
        workflow._refuse("import_file_changed", "Uploaded bytes no longer match the content hash; upload the file again.", 409)
    if format in ("csv_bundle", "excel") and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                workflow._refuse("import_expansion_too_large", "Import expands beyond the 50 MB limit.", 413)
    # Base64 exists only within the server adapter, never in model context.
    return handlers[format](bytes_b64=base64.b64encode(data).decode(), filename=meta.filename)


def _changes(changes):
    if not isinstance(changes, list) or not 1 <= len(changes) <= 20:
        workflow._refuse("invalid_changes", "Provide 1–20 typed engineering changes.")
    try:
        parsed = [workflow.SensitivityChange.model_validate(change) for change in changes]
    except ValidationError:
        workflow._refuse("invalid_changes", "Changes require component_class, names, attribute and typed value.")
    seen = set()
    for change in parsed:
        if change.attribute not in workflow._ATTRIBUTES[change.component_class]:
            workflow._refuse("invalid_change_attribute", "This attribute is outside the engineering input allowlist.")
        boolean = change.attribute.endswith("_extendable")
        try:
            finite = math.isfinite(change.value)
        except OverflowError:
            finite = False
        if boolean != isinstance(change.value, bool) or (not boolean and not finite):
            workflow._refuse("invalid_change_value", "Use finite numbers or booleans for extendable flags.")
        for name in change.names:
            key = (change.component_class, name, change.attribute)
            if key in seen:
                workflow._refuse("duplicate_change", "Each component/attribute may be changed only once per preview.")
            seen.add(key)
    return parsed


def _candidate(ctx, changes):
    from services import chat_tools
    candidate = PyPSAService.build_context()
    candidate.network = workflow._network_copy(ctx.network)
    candidate.user_ts = copy.deepcopy(ctx.user_ts)
    candidate.solver_state["solver_config"] = copy.deepcopy(ctx.solver_state["solver_config"])
    diffs = []
    for change in changes:
        component = candidate.network.components[change.component_class]
        if not set(change.names) <= set(component.static.index):
            workflow._refuse("change_component_missing", "A named component does not exist.")
        for name in change.names:
            before = component.static.at[name, change.attribute]
            before = before.item() if hasattr(before, "item") else before
            before = before if isinstance(before, bool) or math.isfinite(before) else str(before)
            unit = str(component.defaults.at[change.attribute, "unit"])
            diffs.append({"component_class": change.component_class, "name": name, "attribute": change.attribute,
                          "before": before.item() if hasattr(before, "item") else before,
                          "after": change.value, "unit": unit})
        component.static.loc[change.names, change.attribute] = change.value
        for name in change.names:
            stored = component.static.at[name, change.attribute]
            stored = stored.item() if hasattr(stored, 'item') else stored
            if stored != change.value:
                workflow._refuse('invalid_change_value', 'The native input field would round this value; choose an exactly representable value and create a new preview.', 409)
    with workflow._isolated(candidate):
        validation = chat_tools.validate_network()
    if not validation.get("ok") or validation.get("deferred"):
        workflow._refuse("change_preflight_failed", "The candidate fails network validation; the project was not modified.", 409)
    return candidate, diffs, validation


def _idle(ctx, project):
    from services.study_state import refuse_edit_during_live_study
    refuse_edit_during_live_study()
    if ctx.solver_state.get("status") == "running" or workflow._active_job(project):
        workflow._refuse("project_busy", "Wait for the current solve before previewing or applying changes.", 409)


def preview_project_changes(changes: list[dict]) -> dict:
    project, root, owner = _project()
    parsed = _changes(changes)
    with PyPSAService.get_lock():
        ctx = PyPSAService.get_active_context()
        _idle(ctx, project)
        fingerprint = workflow._network_input_fingerprint(ctx)
        _, diffs, validation = _candidate(ctx, parsed)
        preview_id = str(uuid.uuid4())
        record = {"version": 1, "preview_id": preview_id, "project_id": str(project.id), "owner_id": owner,
                  "fingerprint": fingerprint, "changes": [c.model_dump() for c in parsed],
                  "diff": diffs, "changes_total": len(diffs), "applied": False}
        record['review_sha256'] = _preview_digest(record)
        _preview_review(record)
        tasks._save(root / "assistant_previews" / (preview_id + ".json"), record)
    out = {"preview_id": preview_id, "project_id": str(project.id), "fingerprint": fingerprint,
           "valid": True, "changes_total": len(diffs), "diff": diffs[:20],
           "warning_count": validation.get("warnings", 0),
           "validation_issues": validation.get("issues", [])[:3],
           "note": "No network changes applied. Static parameters can be overridden by existing time series. apply_project_changes requires confirmation and unchanged inputs."}
    while len(json.dumps(out, default=str)) > 3800 and out["diff"]:
        out["diff"].pop()
    out["diff_truncated"] = len(out["diff"]) < len(diffs)
    return out


def apply_project_changes(preview_id: str) -> dict:
    from services import dirty_state
    project, root, owner = _project()
    try:
        preview_id = str(uuid.UUID(preview_id))
    except (ValueError, TypeError, AttributeError):
        workflow._refuse("preview_not_found", "Preview not found.", 404)
    path = root / "assistant_previews" / (preview_id + ".json")
    with PyPSAService.get_lock():
        record = workflow._read_json(path)
        if record.get("owner_id") != owner or record.get("project_id") != str(project.id):
            workflow._refuse("preview_not_found", "Preview not found.", 404)
        _preview_review(record)
        expected = _REVIEWED_PREVIEW.get()
        if expected is not None and expected != record['review_sha256']:
            workflow._refuse('invalid_preview', 'The reviewed preview changed; create and review a new preview.', 409)
        ctx = PyPSAService.get_active_context()
        _idle(ctx, project)
        if record.get("applied"):
            return {"preview_id": preview_id, "project_id": str(project.id), "applied": True, "reused": True}
        if workflow._network_input_fingerprint(ctx) != record["fingerprint"]:
            workflow._refuse("preview_stale", "Project inputs changed; create and review a new preview.", 409)
        candidate, diffs, _ = _candidate(ctx, _changes(record["changes"]))
        if json.dumps(diffs, sort_keys=True) != json.dumps(record['diff'], sort_keys=True):
            workflow._refuse('invalid_preview', 'The stored changes do not match the exact review; create a new preview.', 409)
        # Persist uncertainty BEFORE effects, so an interrupted apply cannot replay.
        if record.get("applying"):
            workflow._refuse("preview_apply_uncertain", "Previous application was interrupted; inspect inputs and create a new preview.", 409)
        record["applying"] = True
        record['review_sha256'] = _preview_digest(record)
        tasks._save(path, record)
        PyPSAService.set_network(candidate.network)
        # The copy still carries the previous solve. Topology is unchanged, so
        # dispatch_status would keep calling those tables fresh. Drop them and
        # the completed status, the same way a network edit does.
        active = PyPSAService.get_active_context()
        from services.dispatch_status import clear_dispatch
        clear_dispatch(active.network)
        active.network._objective = None
        active.network._objective_constant = None
        with PyPSAService.get_solver_state_lock():
            active.solver_state.update(
                status="idle", condition=None, objective=None, solve_time=None,
            )
        dirty_state.mark_dirty()
        from services import change_log_service
        change_log_service.log("update", "Network", preview_id, f"Applied reviewed preview: {len(diffs)} engineering values")
        record["applied"] = True
        record['review_sha256'] = _preview_digest(record)
        tasks._save(path, record)
        return {"preview_id": preview_id, "project_id": str(project.id), "applied": True, "reused": False,
                "changed_values": len(diffs), "saved": False, "next_tools": ["validate_network", "run_simulation", "save_project"]}


def create_chart(component: str, attribute: str, names: list[str], source: str = "input") -> dict:
    from services import chat_tools
    from services.assurance_artifacts import canonical_sha256
    project, chart_root, _ = _project()
    if component not in ("Generator", "Load", "Line", "Link", "StorageUnit", "Store", "Bus", "Transformer") or source not in ("input", "result") or not isinstance(names, list) or not 1 <= len(names) <= 8 or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        workflow._refuse("invalid_chart", "Choose a component, input/result source, and 1–8 unique asset names.")
    with PyPSAService.get_lock():
        ctx = PyPSAService.get_active_context()
        comp = ctx.network.components[component]
        defaults = comp.defaults
        if attribute not in defaults.index or source.title() not in str(defaults.at[attribute, "status"]):
            # Metadata calls solved attributes 'Output', not 'Result'.
            if source != "result" or attribute not in defaults.index or "Output" not in str(defaults.at[attribute, "status"]):
                workflow._refuse("invalid_chart_attribute", "Attribute does not match the chosen input/result source.")
        freshness = chat_tools.dispatch_status()
        fingerprint = workflow._network_input_fingerprint(ctx)
        matching_inputs = ctx.network.meta.get("assistant_solve_input_fingerprint") == fingerprint
        if source == "result" and (ctx.solver_state.get("status") == "running" or freshness["state"] != "fresh" or not matching_inputs):
            workflow._refuse("chart_results_unavailable", "Results do not have a matching input fingerprint. Solve the current inputs before charting results.", 409)
        dynamic = comp.dynamic.get(attribute)
        if dynamic is not None and not dynamic.empty and set(names) <= set(dynamic.columns):
            table = dynamic[names].copy()
        elif source == "input" and attribute in comp.static.columns and set(names) <= set(comp.static.index):
            table = comp.static.loc[names, [attribute]].copy()
        else:
            workflow._refuse("chart_data_missing", "No series exists for these assets/attribute.", 409)
        if len(table) > 5000:
            workflow._refuse("chart_too_many_rows", "Choose a shorter time series (maximum 5000 rows).")
        table = table.apply(pd.to_numeric, errors="coerce")
        import numpy as np
        if table.empty or not np.isfinite(table.to_numpy()).all():
            workflow._refuse("chart_invalid_data", "Chart data must contain finite numeric values.")
        unit = str(defaults.at[attribute, "unit"])
        dataset = {"index": [list(map(str, i)) if isinstance(i, tuple) else str(i) for i in table.index],
                   "columns": list(map(str, table.columns)), "values": table.to_numpy().tolist(),
                   "units": {str(column): unit for column in table.columns}}
        bar = len(table) == len(names) and list(table.columns) == [attribute]
        render_spec = {"kind": "bar" if bar else "line", "title": f"{component}: {attribute}",
                       "x_label": "Asset" if bar else "Snapshot index", "y_label": unit,
                       "series": dataset["columns"], "x_values": dataset["index"] if bar else list(range(len(table))),
                       "size_inches": [8, 4], "dpi": 120}
    with _CHART_LOCK:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        figure = Figure(figsize=(8, 4), dpi=120)
        FigureCanvasAgg(figure)
        axis = figure.subplots()
        if bar:
            axis.bar(list(map(str, table.index)), table[attribute].values)
        else:
            for name in table.columns:
                axis.plot(range(len(table)), table[name].values, label=str(name))
            axis.legend()
        axis.set_xlabel(render_spec["x_label"])
        axis.set_title(render_spec["title"])
        axis.set_ylabel(render_spec["y_label"])
        figure.tight_layout()
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png")
    if str(PyPSAService.get_active_context().project_uuid) != str(project.id):
        workflow._refuse("project_changed", "Active project changed while rendering the chart; retry in the intended project.", 409)
    data = buffer.getvalue()
    byte_hash = hashlib.sha256(data).hexdigest()
    manifest = {"schema_version": 1, "project_id": str(project.id),
                "source_kind": "configured_input" if source == "input" else "captured_result",
                "dataset": dataset, "render_spec": render_spec,
                "dataset_sha256": canonical_sha256(dataset), "render_spec_sha256": canonical_sha256(render_spec),
                "artifact_sha256": byte_hash,
                "validity": {"coverage": "captured_rows", "input_fingerprint": fingerprint,
                             "limitations": ["Data/render/byte integrity; this does not certify engineering validity."]}}
    provenance = {"component": component, "attribute": attribute, "names": names, "source": source,
                  "rows": len(table), "unit": unit, "input_fingerprint": fingerprint,
                  "dispatch": freshness, "first_index": str(table.index[0]), "last_index": str(table.index[-1])}
    provenance["manifest"] = manifest
    # Sidecar lives with the content-addressed artifact, outside the model prompt.
    # Commit before blob_ready becomes visible; a missing sidecar fails closed.
    with PyPSAService.get_lock():
        current = PyPSAService.get_active_context()
        if current is not ctx or workflow._network_input_fingerprint(current) != fingerprint:
            workflow._refuse("chart_source_changed", "Chart inputs changed while rendering; retry.", 409)
        current_comp = current.network.components[component]
        current_dynamic = current_comp.dynamic.get(attribute)
        if dynamic is not None and not dynamic.empty and set(names) <= set(dynamic.columns):
            unchanged = current_dynamic is not None and current_dynamic[names].equals(table)
        else:
            unchanged = current_comp.static.loc[names, [attribute]].equals(table)
        if not unchanged or str(current_comp.defaults.at[attribute, "unit"]) != unit:
            workflow._refuse("chart_source_changed", "Chart data changed while rendering; retry.", 409)
        _project()  # Fresh owner/project authorization before publishing bytes.
        tasks._save(chart_root / "assistant_artifacts" / (byte_hash[:16] + ".json"), provenance)
        artifact = chat_tools._save_agent_export(data, f"{component}_{attribute}.png", "image/png",
                                                 assurance_sha256=canonical_sha256(provenance))
    return {**artifact, "provenance": provenance, "manifest": manifest}


def build_delivery(file_ids: list[str], filename: str = "delivery.zip") -> dict:
    from services import chat_tools, upload_service
    if not isinstance(file_ids, list) or not 1 <= len(file_ids) <= 20 or any(not isinstance(f, str) for f in file_ids) or len(set(file_ids)) != len(file_ids):
        workflow._refuse("invalid_delivery", "Select 1–20 unique file IDs.")
    if not isinstance(filename, str) or not filename.lower().endswith(".zip") or len(filename) > 100:
        workflow._refuse("invalid_delivery", "Provide a short .zip filename.")
    project, root, _ = _project()
    files, total = [], 0
    for file_id in file_ids:
        _, _, _, meta, path = _file(file_id)
        total += path.stat().st_size
        if total > upload_service.MAX_FILE_BYTES - 100000:
            workflow._refuse("delivery_too_large", "Delivery contents exceed the 25 MB limit.", 413)
        files.append((meta, path))
    manifest = {"version": 1, "project_id": str(project.id), "files": []}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for meta, path in files:
            # Content ID prefix avoids basename collisions; no model-controlled paths.
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(meta.filename).name)[:100] or "file"
            entry = f"{meta.file_id}_{safe}"
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != meta.sha256:
                workflow._refuse("delivery_file_changed", "An artifact changed; regenerate it before delivery.", 409)
            archive.writestr(entry, data)
            item = {"file_id": meta.file_id, "archive_path": entry, "filename": meta.filename,
                    "sha256": meta.sha256, "size": len(data), "mime": meta.mime}
            sidecar = root / "assistant_artifacts" / (meta.file_id + ".json")
            from services.assurance_artifacts import verify_chart
            checked = verify_chart(root, meta, data, str(project.id), provenance=True)
            if checked is not None:
                item["assurance"] = checked["manifest"]
                item["provenance"] = checked
            elif sidecar.is_file():
                item["provenance"] = workflow._read_json(sidecar)
                item["provenance_status"] = "unvalidated"
            manifest["files"].append(item)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    if buffer.tell() > upload_service.MAX_FILE_BYTES:
        workflow._refuse("delivery_too_large", "Compressed delivery exceeds 25 MB.", 413)
    if str(PyPSAService.get_active_context().project_uuid) != str(project.id):
        workflow._refuse("project_changed", "Active project changed while building delivery; retry in the intended project.", 409)
    return {**chat_tools._save_agent_export(buffer.getvalue(), filename, "application/zip"),
            "files_total": len(files), "manifest": "manifest.json"}


def preview_confirmation(preview_id):
    """Human-readable immutable diff; not additional handler arguments."""
    project, root, owner = _project()
    try:
        preview_id = str(uuid.UUID(preview_id))
    except (ValueError, TypeError, AttributeError):
        workflow._refuse("preview_not_found", "Preview not found.", 404)
    with PyPSAService.get_lock():
        record = workflow._read_json(root / "assistant_previews" / (preview_id + ".json"))
        if record.get("owner_id") != owner or record.get("project_id") != str(project.id):
            workflow._refuse("preview_not_found", "Preview not found.", 404)
        out = _preview_review(record)
        if record.get('applied') or record.get('applying'):
            workflow._refuse('invalid_preview', 'Preview was already applied or interrupted; inspect inputs and create a new preview.', 409)
        ctx = PyPSAService.get_active_context()
        if workflow._network_input_fingerprint(ctx) != record['fingerprint']:
            workflow._refuse('preview_stale', 'Project inputs changed; create and review a new preview.', 409)
        _, diffs, _ = _candidate(ctx, _changes(record['changes']))
        if json.dumps(diffs, sort_keys=True) != json.dumps(record['diff'], sort_keys=True):
            workflow._refuse('invalid_preview', 'The stored changes do not match the exact review; create a new preview.', 409)
        return out
