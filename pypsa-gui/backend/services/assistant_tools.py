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


def find_capabilities(goal: str, limit: int = 3) -> dict:
    from harness.catalogue import TOOLS, safety_tier_for
    from harness.toolsets import filter_tools
    from harness.providers.wiring import _tools_payload
    from services import chat_tools
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
        if boolean != isinstance(change.value, bool) or (not boolean and not math.isfinite(change.value)):
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
        record = {"preview_id": preview_id, "project_id": str(project.id), "owner_id": owner,
                  "fingerprint": fingerprint, "changes": [c.model_dump() for c in parsed],
                  "diff": diffs[:20], "changes_total": len(diffs), "applied": False}
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
        ctx = PyPSAService.get_active_context()
        _idle(ctx, project)
        if record.get("applied"):
            return {"preview_id": preview_id, "project_id": str(project.id), "applied": True, "reused": True}
        if workflow._network_input_fingerprint(ctx) != record["fingerprint"]:
            workflow._refuse("preview_stale", "Project inputs changed; create and review a new preview.", 409)
        candidate, diffs, _ = _candidate(ctx, _changes(record["changes"]))
        # Persist uncertainty BEFORE effects, so an interrupted apply cannot replay.
        if record.get("applying"):
            workflow._refuse("preview_apply_uncertain", "Previous application was interrupted; inspect inputs and create a new preview.", 409)
        record["applying"] = True
        tasks._save(path, record)
        PyPSAService.set_network(candidate.network)
        dirty_state.mark_dirty()
        from services import change_log_service
        change_log_service.log("update", "Network", preview_id, f"Applied reviewed preview: {len(diffs)} engineering values")
        record["applied"] = True
        tasks._save(path, record)
        return {"preview_id": preview_id, "project_id": str(project.id), "applied": True, "reused": False,
                "changed_values": len(diffs), "saved": False, "next_tools": ["validate_network", "run_simulation", "save_project"]}


def create_chart(component: str, attribute: str, names: list[str], source: str = "input") -> dict:
    from services import chat_tools
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
    with _CHART_LOCK:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        figure = Figure(figsize=(8, 4), dpi=120)
        FigureCanvasAgg(figure)
        axis = figure.subplots()
        if len(table) == len(names) and list(table.columns) == [attribute]:
            axis.bar(list(map(str, table.index)), table[attribute].values)
        else:
            for name in table.columns:
                axis.plot(range(len(table)), table[name].values, label=str(name))
            axis.legend()
            axis.set_xlabel("Snapshot index")
        axis.set_title(f"{component}: {attribute}")
        axis.set_ylabel(unit)
        figure.tight_layout()
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png")
    if str(PyPSAService.get_active_context().project_uuid) != str(project.id):
        workflow._refuse("project_changed", "Active project changed while rendering the chart; retry in the intended project.", 409)
    artifact = chat_tools._save_agent_export(buffer.getvalue(), f"{component}_{attribute}.png", "image/png")
    provenance = {"component": component, "attribute": attribute, "names": names, "source": source,
                  "rows": len(table), "unit": unit, "input_fingerprint": fingerprint,
                  "dispatch": freshness, "first_index": str(table.index[0]), "last_index": str(table.index[-1])}
    # Sidecar lives with the content-addressed artifact, outside the model prompt.
    tasks._save(chart_root / "assistant_artifacts" / (artifact["file_id"] + ".json"), provenance)
    return {**artifact, "provenance": provenance}


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
            if sidecar.is_file():
                item["provenance"] = workflow._read_json(sidecar)
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
    record = workflow._read_json(root / "assistant_previews" / (preview_id + ".json"))
    if record.get("owner_id") != owner or record.get("project_id") != str(project.id):
        workflow._refuse("preview_not_found", "Preview not found.", 404)
    out = {"preview_id": preview_id, "project_id": str(project.id),
           "changes_total": record["changes_total"], "reviewed_changes": record["diff"],
           "note": "Apply these typed changes to unchanged inputs. Existing time-series overrides remain. Changes are left unsaved."}
    while len(json.dumps(out, default=str)) > 3800 and out["reviewed_changes"]:
        out["reviewed_changes"].pop()
        out["diff_truncated"] = True
    return out
