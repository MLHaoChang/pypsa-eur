"""
The edit card: one preview → confirm → receipt contract for every chat edit
(LC-04, LC-C5; owner decision Q3: an old→new card before EVERY chat edit, in
Expert and Guided alike, settings included).

A chat edit is a tool that changes project inputs or settings (`is_edit`).
For each one the dispatcher, in this order:

1. `preview(tool, args)` builds the card, or refuses before any card exists
   (an unknown, derived or read-only field, a non-finite value, a value the
   native column would round). The card's rows are exact old → new values in
   stored units, read from a NATIVE run on an isolated copy where an adapter
   can do that; derived columns the native handler recomputes appear as
   `role: 'derived'` rows. Nothing here converts or annualises a value.
2. The ordinary confirmation gate shows the card (`edit_card` on the
   `tool_pending_confirmation` frame) and waits for the user.
3. `require_current(card)` re-previews just before the handler runs; a card
   whose digest no longer matches the inputs refuses (`edit_preview_stale`).
4. `receipt(card, result)` reads every row back natively. `verified` needs
   each value to equal the card's `new`; anything else is `uncertain`, never
   success. The receipt also says whether results are now stale and whether
   `undo_last` can revert the edit.

Another kind of edit (a generated time series refined by deterministic ops,
TS-04) joins by registering an `Adapter` for its tool: `preview(args)` returns
the rows, `readback(args, rows)` the values actually stored. A tool that is an
edit without an adapter of its own still gets a card, of kind `arguments`
(`complete: False`): the requested values, with no old value claimed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException

VERSION = 1

# Write-tier tools that change project settings or inputs outside the live
# network's component tables. Network writes are found by `tool_edits_network`.
SETTINGS_EDITS = frozenset({
    "update_solver_config", "set_site_connection", "attach_tariff", "import_urdb_tariff",
    "define_participants", "put_stress_scenarios", "record_asset_health", "set_study_parameter",
    "apply_generated_profile", "update_project_layout",
    "gridspine_update_config", "gridspine_edit_template_param", "gridspine_set_dispatch_source",
    "campus_set_island_config", "campus_set_library", "campus_apply_gfm_settings", "campus_draft_campus",
    "set_report_mapping", "set_report_template", "import_edited_report",
})

# Write-tier tools that are not edits, and why. A new write tool must be put
# in one of the two lists (tests/test_edit_card.py pins it).
NOT_EDITS = {
    "activate_project": "switches the active project (LC-01B owns its approval)",
    "load_project": "switches the active project (LC-01B owns its approval)",
    "discuss_project_run": "names a run for the conversation; changes nothing",
    "preview_project_changes": "a preview record; apply_project_changes is the edit",
    "create_chart": "a chart file", "build_delivery": "a delivery file",
    "create_project_snapshot": "a backup of the current inputs",
    "export_asset_results": "an export", "export_chat_summary": "an export",
    "export_eh_report_docx": "an export", "export_experiment_comparison": "an export",
    "export_preview_png": "an export", "export_report_docx": "an export", "export_report_pdf": "an export",
    "export_run_evidence": "an export", "export_to_csv": "an export", "export_to_excel": "an export",
    "gridspine_export_handoff_bundle": "an export", "generate_study_report": "a report from a finished study",
    "start_task": "a task record", "resume_task": "a task record", "cancel_task": "a task record",
    "start_experiment": "an experiment record; its cases carry their own approvals",
    "prepare_experiment": "an experiment record", "advance_experiment": "an experiment record",
    "refresh_experiment": "an experiment record", "cancel_experiment": "an experiment record",
    "start_campaign": "a campaign record", "end_campaign": "a campaign record",
    "gridspine_create_study": "a new study record", "start_decision_study": "a new study record",
    "gridspine_compute_capacity": "a computation on the saved network",
    "gridspine_assess_connection": "a computation on the saved network",
    "campus_extract_grid_code": "reads a document into a study file",
    "campus_run_island_study": "a computation on copies of the saved network",
}


def is_edit(tool_name: str) -> bool:
    """True when a chat call of `tool_name` changes project inputs or settings."""
    from harness.catalogue import tool_edits_network

    return tool_name in SETTINGS_EDITS or tool_edits_network(tool_name)


@dataclass(frozen=True)
class Adapter:
    kind: str
    preview: Callable[[dict], list[dict]]
    readback: Callable[[dict, list[dict]], list[Any]]


ADAPTERS: dict[str, Adapter] = {}


def register(tool_name: str, adapter: Adapter) -> None:
    ADAPTERS[tool_name] = adapter


def _refuse(kind: str, message: str, status: int = 422):
    raise HTTPException(status, {"error_kind": kind, "message": message})


def _scalar(value):
    item = getattr(value, "item", None)
    value = item() if callable(item) else value
    if isinstance(value, float) and not math.isfinite(value):
        return None if math.isnan(value) else str(value)
    return value


def _same(a, b) -> bool:
    from services.network_crud import _same_value

    return _same_value(a, b)


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def _says(rows: list[dict]) -> str:
    def one(row):
        unit = f" {row['unit']}" if row.get("unit") else ""
        old = "?" if row.get("old") is None and row.get("old_known") is False else _number(row.get("old"))
        return f"{row['name']} {row['field']} {old} → {_number(row['new'])}{unit}"
    return "; ".join(one(row) for row in rows if row["role"] == "typed")


def _digest(card: dict, fingerprint: str | None) -> str:
    body = {k: v for k, v in card.items() if k != "digest"}
    body["inputs"] = fingerprint
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def _fingerprint() -> str | None:
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    try:
        return workflow_tools._network_input_fingerprint(PyPSAService.get_active_context())
    except Exception:  # noqa: BLE001 — a network the fingerprint cannot read binds nothing
        return None


def preview(tool_name: str, args: dict) -> dict:
    """The card for one edit call, or a refusal (HTTPException) before any card."""
    adapter = ADAPTERS.get(tool_name, _ARGUMENTS)
    rows = adapter.preview(args or {})
    card = {"version": VERSION, "kind": adapter.kind, "tool": tool_name, "rows": rows,
            "says": _says(rows), "complete": adapter is not _ARGUMENTS}
    card["digest"] = _digest(card, _fingerprint())
    return card


def require_current(tool_name: str, args: dict, card: dict) -> None:
    """Refuse when the inputs moved since the card was shown."""
    if preview(tool_name, args)["digest"] != card["digest"]:
        _refuse("edit_preview_stale", "The values changed since the card was shown; nothing was applied. "
                "Propose the edit again.", 409)


def _results_stale() -> bool:
    from services.pypsa_service import PyPSAService

    network = PyPSAService.get_network()
    solved = network.meta.get("assistant_solve_input_fingerprint") if hasattr(network, "meta") else None
    return bool(solved) and solved != _fingerprint()


def _undo(tool_name: str) -> dict:
    from services import chat_tools

    try:
        depth = int(chat_tools.undo_status().get("depth") or 0)
    except Exception:  # noqa: BLE001 — unknown undo state offers nothing
        depth = 0
    return {"tool": "undo_last", "available": tool_name in chat_tools.UNDO_CAPTURED_TOOLS and depth > 0}


def receipt(card: dict, args: dict) -> dict:
    """Native read-back of every row the card showed."""
    adapter = ADAPTERS.get(card["tool"], _ARGUMENTS)
    values = adapter.readback(args or {}, card["rows"])
    rows = [{**row, "readback": _scalar(value)} for row, value in zip(card["rows"], values)]
    verified = card["complete"] and all(_same(row["readback"], row["new"]) for row in rows)
    return {"version": VERSION, "digest": card["digest"], "outcome": "verified" if verified else "uncertain",
            "rows": rows, "says": card["says"], "results_stale": _results_stale(), "undo": _undo(card["tool"])}


# ── Components: update_component / bulk_update_components ──────────────────


def _fields(component_class: str) -> dict[str, dict]:
    from services import chat_tools
    from services.asset_schema.fields import fields_for
    from services.component_discovery import field_rows
    from services.pypsa_service import PyPSAService

    schemas = chat_tools._COMPONENT_CREATE_SCHEMAS
    if component_class not in schemas:
        _refuse("unknown_component_class", f"Unknown component_class {component_class!r}.")
    try:
        assets = fields_for(component_class)
    except KeyError:
        assets = ()
    rows = field_rows(chat_tools._get_schema(schemas[component_class]),
                      PyPSAService.get_network().components[component_class].defaults, assets)
    return {row["name"]: row for row in rows}


def _check_value(component_class: str, key: str, value, field: dict) -> None:
    if key == "name":
        if not isinstance(value, str) or not value.strip():
            _refuse("invalid_edit_value", "A new name must be a nonblank string.")
        return
    if field is None:
        _refuse("unknown_field", f"{component_class} has no field {key!r}. Read get_component_schema.")
    if field["derived"]:
        _refuse("derived_field", f"{component_class}.{key} is derived from other fields and cannot be set. "
                "Change the fields it follows from.")
    if not field["editable"]:
        _refuse("read_only_field", f"{component_class}.{key} is {field['editability_reason']} and cannot be set.")
    kind = field.get("native_type") or ""
    if kind.startswith("float") or kind.startswith("int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _refuse("invalid_edit_value", f"{component_class}.{key} takes a number.")
        if not math.isfinite(value):
            _refuse("invalid_edit_value", f"{component_class}.{key} takes a finite number.")
    elif kind.startswith("bool") and not isinstance(value, bool):
        _refuse("invalid_edit_value", f"{component_class}.{key} takes true or false.")


def _sandbox_rows(component_class: str, targets: list[tuple[str, dict]], run: Callable[[], Any]) -> list[dict]:
    """Run the native handler on an isolated copy and diff each target row."""
    from services import change_log_service, chat_tools, workflow_tools
    from services.pypsa_service import PyPSAService

    fields = _fields(component_class)
    for name, attrs in targets:
        for key, value in attrs.items():
            _check_value(component_class, key, value, fields.get(key))
    active = PyPSAService.get_active_context()
    attr = chat_tools._GENERIC_CRUD_ATTRS.get(component_class) or {
        "Bus": "buses", "Transformer": "transformers", "GlobalConstraint": "global_constraints"}[component_class]
    before = getattr(active.network, attr)
    missing = [name for name, _ in targets if name not in before.index]
    if missing:
        _refuse("component_not_found", f"{component_class} {missing[0]!r} not found.", 404)
    candidate = PyPSAService.build_context()
    candidate.network = workflow_tools._network_copy(active.network)
    candidate.user_ts = copy.deepcopy(active.user_ts)
    candidate.solver_state["solver_config"] = copy.deepcopy(active.solver_state["solver_config"])
    with workflow_tools._isolated(candidate), change_log_service.suppressed():
        try:
            run()
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
            _refuse("edit_refused", f"The edit would be refused: {detail}", exc.status_code)
    after = getattr(candidate.network, attr)
    rows = []
    for name, attrs in targets:
        new_name = attrs.get("name", name)
        if new_name != name:
            rows.append({"component_class": component_class, "name": name, "field": "name", "label": "Name",
                         "old": name, "new": new_name, "unit": "", "role": "typed"})
        # A GUI column (an investment part) may first appear with this edit.
        for column in [*before.columns, *(c for c in after.columns if c not in before.columns)]:
            if column not in after.columns:
                continue
            old = _scalar(before.at[name, column]) if column in before.columns else None
            new = _scalar(after.at[new_name, column])
            if column not in attrs and _same(old, new):
                continue
            field = fields.get(column) or {}
            if column in attrs and isinstance(attrs[column], (int, float)) and not isinstance(attrs[column], bool) \
                    and not _same(new, attrs[column]):
                _refuse("invalid_edit_value", f"{component_class}.{column} would store {new!r}, not "
                        f"{attrs[column]!r}. Choose a value the field holds exactly.")
            row = {"component_class": component_class, "name": name, "field": column,
                   "label": field.get("label", column), "old": old, "new": new,
                   "unit": field.get("unit_storage") or field.get("native_unit") or "",
                   "role": "typed" if column in attrs else "derived" if field.get("derived") else "follows"}
            scale = field.get("display_scale")
            if scale not in (None, 1, 1.0) and _numeric(new):
                row["display"] = {"old": old * scale if _numeric(old) else None, "new": new * scale,
                                  "unit": field.get("unit_display")}
            rows.append(row)
    return rows


def _component_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    network = PyPSAService.get_network()
    renamed = {row["name"]: row["new"] for row in rows if row["field"] == "name"}
    out = []
    for row in rows:
        klass = row["component_class"]
        attr = chat_tools._GENERIC_CRUD_ATTRS.get(klass) or {
            "Bus": "buses", "Transformer": "transformers", "GlobalConstraint": "global_constraints"}[klass]
        frame = getattr(network, attr)
        name = renamed.get(row["name"], row["name"])
        if row["field"] == "name":
            out.append(name if name in frame.index else None)
        elif name in frame.index and row["field"] in frame.columns:
            out.append(frame.at[name, row["field"]])
        else:
            out.append(None)
    return out


def _update_component_preview(args: dict) -> list[dict]:
    from services import chat_tools

    klass, name = args.get("component_class"), args.get("name")
    attrs = dict(args.get("attrs") or {})
    if args.get("new_name"):
        attrs["name"] = args["new_name"]
    if not attrs:
        _refuse("empty_edit", "Name at least one field to change.")
    return _sandbox_rows(klass, [(name, attrs)], lambda: chat_tools.update_component(
        klass, name, args.get("attrs"), args.get("new_name")))


def _bulk_preview(args: dict) -> list[dict]:
    from services import chat_tools

    klass, names, updates = args.get("component_class"), args.get("names"), args.get("updates")
    if not isinstance(names, list) or not names or not isinstance(updates, dict) or not updates:
        _refuse("empty_edit", "Name the components and at least one field to change.")
    if "name" in updates:
        _refuse("invalid_edit_value", "A bulk edit cannot rename.")
    return _sandbox_rows(klass, [(name, dict(updates)) for name in names],
                         lambda: chat_tools.bulk_update_components(klass, names, updates))


register("update_component", Adapter("parameter_edit", _update_component_preview, _component_readback))
register("bulk_update_components", Adapter("parameter_edit", _bulk_preview, _component_readback))


# ── Solver settings: update_solver_config ──────────────────────────────────


def _solver_config() -> dict:
    from dataclasses import asdict

    from services.pypsa_service import PyPSAService

    config = PyPSAService.get_active_context().solver_state["solver_config"]
    return asdict(config) if not isinstance(config, dict) else dict(config)


def _solver_preview(args: dict) -> list[dict]:
    partial = args.get("partial")
    if not isinstance(partial, dict) or not partial:
        _refuse("empty_edit", "Name at least one setting to change.")
    current = _solver_config()
    rows = []
    for key, value in partial.items():
        if key not in current:
            _refuse("unknown_field", f"The solver settings have no field {key!r}.")
        if isinstance(value, float) and not math.isfinite(value):
            _refuse("invalid_edit_value", f"Setting {key!r} takes a finite number.")
        rows.append({"component_class": "Settings", "name": "Solver settings", "field": key, "label": key,
                     "old": _scalar(current[key]), "new": value, "unit": "", "role": "typed"})
    return rows


def _solver_readback(args: dict, rows: list[dict]) -> list[Any]:
    current = _solver_config()
    return [current.get(row["field"]) for row in rows]


register("update_solver_config", Adapter("settings_edit", _solver_preview, _solver_readback))


# ── Any other edit: the requested arguments, no old value claimed ──────────


def _arguments_preview(args: dict) -> list[dict]:
    rows = []
    for key, value in sorted(args.items()):
        text = json.dumps(value, default=str, allow_nan=True)
        rows.append({"component_class": "", "name": "", "field": key, "label": key, "old": None,
                     "old_known": False, "new": value if len(text) <= 200 else text[:200] + "…",
                     "unit": "", "role": "typed"})
    return rows


_ARGUMENTS = Adapter("arguments", _arguments_preview, lambda args, rows: [None] * len(rows))
