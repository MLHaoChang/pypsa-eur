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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException

VERSION = 1

# Write-tier tools that change project settings or inputs outside the live
# network's component tables. Network writes are found by `tool_edits_network`.
SETTINGS_EDITS = frozenset({
    "update_solver_config", "set_site_connection", "attach_tariff", "import_urdb_tariff",
    "define_participants", "put_stress_scenarios", "record_asset_health", "set_study_parameter",
    "apply_generated_profile", "apply_generated_profiles", "apply_default_series",
    "update_project_layout",
    "gridspine_update_config", "gridspine_edit_template_param", "gridspine_set_dispatch_source",
    "campus_set_island_config", "campus_set_library", "campus_apply_gfm_settings", "campus_draft_campus",
    "set_report_mapping", "set_report_template", "import_edited_report",
    "apply_commercial_document_values",
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
    # The flow section a settings edit lands in, when its rows name no component (a card
    # with a class-keyed row gets its section from the component).
    section_id: str | None = None
    # What else the card binds (in its digest) beside its rows: the stored
    # review an `apply_project_changes` card shows.
    binding: Callable[[dict], dict] | None = None
    # The set lines a card shows above their rows (LC-16 slice B), in its digest.
    sets: Callable[[dict], list[dict]] | None = None


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


def _says(rows: list[dict], sets: list[dict] = ()) -> str:
    def phrase(group):
        # One phrase per set, never a name per member: a 20-asset set stays one line.
        count = (f"{group['size']} asset{'' if group['size'] == 1 else 's'}"
                 + (f", {group['inactive']} inactive" if group.get("inactive") else ""))
        if group["factor_words"]:
            change = f"{group['attribute']} {group['factor_words']}"
        else:
            row = next(r for r in rows if r["role"] == "typed" and r.get("component_class") == group["component_class"]
                       and r["name"] in group["names"] and r["field"] == group["attribute"])
            shown = row.get("display") or row
            change = f"{group['attribute']} → {_number(shown['new'])}" + (f" {shown['unit']}" if shown.get("unit") else "")
        return f"{group['words']} · {count} · {change}"

    def one(row):
        if row["field"] == "exists":
            return f"{'adds' if row['new'] else 'removes'} {row['component_class']} {row['name']}"
        # The card's display unit (150 EUR/kW, stored as 150000 EUR/MW), as EditCard.tsx shows it.
        shown = row.get("display")
        old, new, unit = (shown["old"], shown["new"], shown["unit"]) if shown else (row.get("old"), row["new"], row.get("unit"))
        unit = f" {unit}" if unit else ""
        # The card's own words for an absent value (EditCard.tsx): never "None".
        old = ("?" if row.get("old_known") is False else "not set") if old is None else _number(old)
        new = "not set" if new is None else _number(new)
        stored = (f" (stored as {_number(row['new'])} {row['unit']})"
                  if shown and _numeric(row["new"]) and shown["unit"] != row.get("unit") else "")
        what = row.get("says_name") or f"{row['name']} {row['field']}"  # words, not the raw path
        return f"{what} {old} → {new}{unit}{stored}"
    grouped = {(g["component_class"], name, g["attribute"]) for g in sets for name in g["names"]}
    return "; ".join([phrase(g) for g in sets] + [one(row) for row in rows if row["role"] == "typed"
                                                   and (row.get("component_class"), row["name"], row["field"]) not in grouped])


# Kept on the card for the receipt, outside the digest: a section's state can
# move with edits elsewhere and does not change what the user approves.
_UNBOUND = frozenset({"digest", "section_state"})


def _digest(card: dict) -> str:
    """Binds what the card SHOWS: a different old, new or derived value is a
    different card. Edits elsewhere in the project leave it valid. A row's source
    cell is bound by its upload, sha256 and cell, never by the raw text it holds."""
    body = {k: v for k, v in card.items() if k not in _UNBOUND}
    body["rows"] = [{**row, "source": {k: v for k, v in row["source"].items() if k != "raw"}}
                    if isinstance(row.get("source"), dict) else row for row in body.get("rows", [])]
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def _fingerprint() -> str | None:
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    try:
        return workflow_tools._network_input_fingerprint(PyPSAService.get_active_context())
    except Exception:  # noqa: BLE001 — a network the fingerprint cannot read binds nothing
        return None


def preview(tool_name: str, args: dict, *, with_state: bool = True) -> dict:
    """The card for one edit call, or a refusal (HTTPException) before any card.
    `with_state=False` skips the section-state read (outside the digest)."""
    adapter = ADAPTERS.get(tool_name, _ARGUMENTS)
    rows = adapter.preview(args or {})
    sections = _attach_targets(rows)
    card = {"version": VERSION, "kind": adapter.kind, "tool": tool_name, "rows": rows,
            "says": _says(rows), "complete": adapter is not _ARGUMENTS}
    sets = adapter.sets(args or {}) if adapter.sets is not None else []
    if sets:
        card["sets"] = sets
        card["says"] = _says(rows, sets)
    if adapter.binding is not None:
        card["binding"] = adapter.binding(args or {})
    if not sections and adapter.section_id:
        from services import flow_sections as F

        sections = [{"id": adapter.section_id, "label": F.SECTION_LABELS[adapter.section_id]}]
    if sections:
        card["section"] = sections[0]
    card["digest"] = _digest(card)
    if sections and with_state:
        card["section_state"] = _section_state(sections[0]["id"])
    return card


def _project_dir():
    import pathlib

    from services.pypsa_service import PyPSAService

    storage = getattr(PyPSAService.get_active_context(), "storage_dir", None)
    return pathlib.Path(storage) if storage else None


def _attach_targets(rows: list[dict]) -> list[dict]:
    """Each component row's FlowTarget `{section, panel, component, entity,
    field}` by the flow's own class rule (`flow_service.section_for_component`);
    returns the sections met, in row order. A project without a flow (a scratch
    network, or the flow unavailable) leaves the rows without a target."""
    from services import flow_service
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    project_dir, seen, sections = _project_dir(), {}, []
    for row in rows:
        klass, name = row.get("component_class"), row.get("name")
        if not klass or not name or klass == "Settings" or row["role"] == "note":
            continue
        if klass == "Commercial":  # the import tariff and contracts: section 7, no component to open
            from services import flow_sections as F

            if "commercial" not in [s["id"] for s in sections]:
                sections.append({"id": "commercial", "label": F.SECTION_LABELS["commercial"]})
            continue
        if (klass, name) not in seen:
            try:
                seen[(klass, name)] = flow_service.section_for_component(
                    ctx.network, ctx.solver_state["solver_config"], project_dir, klass, name)
            except Exception:  # noqa: BLE001 — a missing section names nothing; it never blocks an edit
                seen[(klass, name)] = None
        section = seen[(klass, name)]
        if section is None:
            continue
        row["target"] = {"section": section["id"], "panel": section.get("panel"), "component": klass,
                         "entity": name, "field": None if row["field"] == "exists" else row["field"]}
        if section["id"] not in [s["id"] for s in sections]:
            sections.append({"id": section["id"], "label": section["label"]})
    return sections


def _section_state(section_id: str) -> dict | None:
    """The section's flow state (`flow_service.project_flow`), or None while the
    flow is deferred or unavailable."""
    from services import flow_service
    from services.pypsa_service import PyPSAService

    project_dir = _project_dir()
    if project_dir is None:
        return None
    try:
        payload = flow_service.project_flow(project_dir, cache_key=PyPSAService.get_active_context().registry_key)
    except Exception:  # noqa: BLE001 — no state is claimed when the flow cannot be read
        return None
    if payload.get("deferred"):
        return None
    row = next((s for s in payload.get("sections") or [] if s.get("section") == section_id), None)
    return None if row is None or row.get("state") is None else {"id": section_id, "label": row["label"],
                                                                  "state": row["state"]}


def require_current(tool_name: str, args: dict, card: dict) -> None:
    """Refuse when the inputs moved since the card was shown."""
    if preview(tool_name, args, with_state=False)["digest"] != card["digest"]:
        _refuse("edit_preview_stale", "The values changed since the card was shown; nothing was applied. "
                "Propose the edit again.", 409)


# The digest of the card the user approved, bound by the turn loop around the
# handler, never taken from model arguments. A handler that rechecks its card
# under its own lock (`apply_equipment_mapping`) reads it here.
_APPROVED: ContextVar[str | None] = ContextVar("edit_card_approved", default=None)


@contextmanager
def approved(digest: str):
    token = _APPROVED.set(digest)
    try:
        yield
    finally:
        _APPROVED.reset(token)


def approved_digest() -> str | None:
    return _APPROVED.get()


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
    # Every row the live write stores is compared, derived ones included; a
    # solve-time row is what PyPSA will write later, not now.
    verified = card["complete"] and all(_same(row["readback"], row["new"]) for row in rows
                                        if row["role"] not in ("at_solve", "note"))
    out = {"version": VERSION, "digest": card["digest"], "outcome": "verified" if verified else "uncertain",
           "rows": rows, "says": card["says"], "results_stale": _results_stale(), "undo": _undo(card["tool"])}
    if card.get("section"):
        after = _section_state(card["section"]["id"])
        if after is not None:
            before = card.get("section_state")
            out["section"] = {**after, "changed": before is None or before.get("state") != after["state"]}
    return out


def reread(tool_name: str, args: dict, earlier: dict) -> dict | None:
    """A fresh native read-back of an earlier receipt's rows (LC-23 SC-12):
    the receipt, now verified, when every stored row reads back as its new
    value; None when a row differs or the tool has no native adapter."""
    adapter = ADAPTERS.get(tool_name)
    rows = [{k: v for k, v in row.items() if k != "readback"} for row in earlier.get("rows") or []]
    if adapter is None or not rows:
        return None
    values = adapter.readback(args or {}, rows)
    rows = [{**row, "readback": _scalar(value)} for row, value in zip(rows, values)]
    if len(rows) != len(earlier["rows"]) or not all(
            _same(row["readback"], row["new"]) for row in rows if row["role"] not in ("at_solve", "note")):
        return None
    return {**earlier, "outcome": "verified", "rows": rows}


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
        # An app column outside the class schema (eh_* tags and the like): the
        # native handler decides, and the diff refuses a key it did not store.
        return
    if field["derived"]:
        _refuse("derived_field", f"{component_class}.{key} is derived from other fields and cannot be set. "
                "Change the fields it follows from.")
    if not field["editable"]:
        _refuse("read_only_field", f"{component_class}.{key} is {field['editability_reason']} and cannot be set.")
    if key == "overnight_cost" and "asset" not in field["sources"]:
        # The app derives this class's capital_cost from its own cost fields;
        # PyPSA's overnight_cost would be a second basis beside them.
        _refuse("cost_set_by_parts", f"A {component_class}'s investment cost comes from its own investment "
                "fields (for a StorageUnit its power and energy part costs, inv_power_overnight and "
                "inv_energy_overnight); edit those. PyPSA's overnight_cost would replace that cost at solve time.")
    kind = field.get("native_type") or ""
    if kind.startswith("float") or kind.startswith("int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _refuse("invalid_edit_value", f"{component_class}.{key} takes a number.")
        if not math.isfinite(value):
            _refuse("invalid_edit_value", f"{component_class}.{key} takes a finite number.")
    elif kind.startswith("bool") and not isinstance(value, bool):
        _refuse("invalid_edit_value", f"{component_class}.{key} takes true or false.")


# A rated capacity and its bounds are magnitudes; PyPSA stores a negative one
# without complaint and the solve then reads it as a reversed limit.
RATINGS = frozenset(f"{base}{suffix}" for base in ("s_nom", "p_nom", "e_nom") for suffix in ("", "_min", "_max"))


def _check_rating(name: str, key: str, value, field: dict | None) -> None:
    if key in RATINGS and isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
        unit = (field or {}).get("unit_storage") or ""
        _refuse("invalid_edit_value", f"{name} {key} cannot be negative ({value:g} {unit}".rstrip()
                + "); a rating is a magnitude, at least 0.")


# PyPSA rewrites these from the standard type at solve time
# (`apply_line_types` / `apply_transformer_types`), so a typed component's
# value would read back and then be discarded.
TYPE_OWNED = {
    "Line": ("r", "x", "b"),
    "Transformer": ("r", "x", "g", "b", "phase_shift", "s_nom", "tap_side", "tap_ratio"),
}
# The capacity the optimiser chooses while `<nom>_extendable` is true.
CAPACITY = {"Line": "s_nom", "Transformer": "s_nom", "Link": "p_nom", "Generator": "p_nom",
            "StorageUnit": "p_nom", "Store": "e_nom"}


def _merged(frame, name: str, attrs: dict, column: str):
    if column in attrs:
        return attrs[column]
    return frame.at[name, column] if column in frame.columns else None


def _check_row_rules(component_class: str, frame, name: str, attrs: dict) -> None:
    owned = TYPE_OWNED.get(component_class, ())
    kind = _merged(frame, name, attrs, "type")
    if owned and isinstance(kind, str) and kind:
        hit = [key for key in attrs if key in owned]
        if hit:
            _refuse("type_owned_field", f"{component_class} {name} takes {', '.join(hit)} from its type {kind!r}, "
                    "which PyPSA applies at solve time. Change the type or clear it.")
    nom = CAPACITY.get(component_class)
    if nom and nom in attrs and bool(_merged(frame, name, attrs, f"{nom}_extendable")):
        low, high = (_scalar(_merged(frame, name, attrs, f"{nom}_{bound}")) for bound in ("min", "max"))
        _refuse("extendable_capacity", f"{component_class} {name} is extendable, so the optimiser chooses {nom} "
                f"(bounds {nom}_min={low}, {nom}_max={high}). Change {nom}_min / {nom}_max, or make it "
                "non-extendable in the same edit.")


def _solve_time_rows(component_class: str, network, attr: str, names: list[str]) -> list[dict]:
    """What PyPSA's type derive will write at solve time, run on a further copy."""
    owned = TYPE_OWNED.get(component_class)
    frame = getattr(network, attr)
    if not owned or "type" not in frame.columns or not (frame.loc[names, "type"].astype(str) != "").any():
        return []
    from pypsa.network import power_flow

    typed = network.copy()
    (power_flow.apply_line_types if component_class == "Line" else power_flow.apply_transformer_types)(typed)
    solved = getattr(typed, attr)
    rows = []
    for name in names:
        if not frame.at[name, "type"]:
            continue
        for column in owned:
            now, then = _scalar(frame.at[name, column]), _scalar(solved.at[name, column])
            if not _same(now, then):
                rows.append({"component_class": component_class, "name": name, "field": column, "label": column,
                             "old": now, "new": then, "unit": "", "role": "at_solve",
                             "note": f"set from type {frame.at[name, 'type']!r} at solve time"})
    return rows


def _component_attr(component_class: str) -> str:
    """The network attribute holding a class's static frame (`Line` → `lines`)."""
    from services import chat_tools

    return chat_tools._GENERIC_CRUD_ATTRS.get(component_class) or {
        "Bus": "buses", "Transformer": "transformers", "GlobalConstraint": "global_constraints"}[component_class]


def _sandbox_rows(component_class: str, targets: list[tuple[str, dict]], run: Callable[[], Any]) -> list[dict]:
    """Run the native handler on an isolated copy and diff each target row."""
    from services import change_log_service, workflow_tools
    from services.pypsa_service import PyPSAService

    fields = _fields(component_class)
    for name, attrs in targets:
        for key, value in attrs.items():
            _check_value(component_class, key, value, fields.get(key))
            _check_rating(name, key, value, fields.get(key))
    active = PyPSAService.get_active_context()
    attr = _component_attr(component_class)
    before = getattr(active.network, attr)
    missing = [name for name, _ in targets if name not in before.index]
    if missing:
        _refuse("component_not_found", f"{component_class} {missing[0]!r} not found.", 404)
    for name, attrs in targets:
        _check_row_rules(component_class, before, name, attrs)
    candidate = PyPSAService.build_context()
    candidate.network = workflow_tools._network_copy(active.network)
    candidate.user_ts = copy.deepcopy(active.user_ts)
    candidate.solver_state["solver_config"] = copy.deepcopy(active.solver_state["solver_config"])
    with workflow_tools._isolated(candidate), change_log_service.suppressed():
        try:
            run()
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("error_kind"):
                raise  # a native refusal keeps its own kind (study_in_flight, …)
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
            _refuse("edit_refused", f"The edit would be refused: {detail}", exc.status_code)
    after = getattr(candidate.network, attr)
    rows = []
    for name, attrs in targets:
        new_name = attrs.get("name", name)
        dropped = [key for key in attrs if key != "name" and (
            key not in after.columns or not _same(_scalar(after.at[new_name, key]), attrs[key]))
            and key not in fields]
        if dropped:
            _refuse("unknown_field", f"{component_class} has no field {dropped[0]!r}. Read get_component_schema.")
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
            if column not in attrs and str(field.get("native_status") or "").startswith("Output"):
                continue  # a solver output, not an input the edit changes
            if column in attrs and isinstance(attrs[column], (int, float)) and not isinstance(attrs[column], bool) \
                    and not _same(new, attrs[column]):
                _refuse("invalid_edit_value", f"{component_class}.{column} would store {new!r}, not "
                        f"{attrs[column]!r}. Choose a value the field holds exactly.")
            row = {"component_class": component_class, "name": name, "field": column,
                   "label": field.get("label", column), "old": old, "new": new,
                   "unit": _carrier_unit(field.get("unit_storage") or field.get("native_unit") or "",
                                         candidate.network, after, new_name),
                   "role": "typed" if column in attrs else "derived" if field.get("derived") else "follows"}
            rows.append(_with_display(row, field))
    targets_now = {(component_class, n) for name, attrs in targets for n in (name, attrs.get("name", name))}
    rows += _existence_rows(active.network, candidate.network, targets_now)
    rows += _solve_time_rows(component_class, candidate.network, attr, [attrs.get("name", name) for name, attrs in targets])
    rows += _cost_notes(component_class, candidate.network, attr, targets, rows)
    return rows


def _with_display(row: dict, field: dict) -> dict:
    """A row in the field's display unit too (EUR/kW for a stored EUR/MW), so
    every card, receipt and undo shows one value in one unit."""
    scale = field.get("display_scale")
    old, new = row["old"], row["new"]
    if scale not in (None, 1, 1.0) and (_numeric(new) or _numeric(old)):
        row["display"] = {"old": old * scale if _numeric(old) else None,
                          "new": new * scale if _numeric(new) else None, "unit": field.get("unit_display")}
    return row


def _carrier_unit(unit: str, network, frame, name: str) -> str:
    """A rate or amount in its bus's own unit (t/h, MW_th, …), never an assumed MW."""
    if unit not in ("MW", "MWh"):
        return unit
    column = "bus" if "bus" in frame.columns else "bus0" if "bus0" in frame.columns else None
    bus = frame.at[name, column] if column else None
    own = network.buses.at[bus, "unit"] if bus in network.buses.index and "unit" in network.buses.columns else None
    if not isinstance(own, str) or not own.strip() or own.strip() in ("MW", "None"):
        return unit
    own = own.strip()
    if unit == "MW":
        return own
    return own[:-2] if own.endswith("/h") else f"{own}·h"


def _rate(frame, name: str) -> tuple[float, str]:
    """The discount rate a row uses and where it comes from (component, project setting, default)."""
    own = frame.at[name, "discount_rate"] if "discount_rate" in frame.columns else None
    if isinstance(own, (int, float)) and math.isfinite(own):
        return float(own), "component"
    rate = getattr(_solver_config_object(), "discount_rate", None)
    return (0.07, "default") if rate is None else (float(rate), "project setting")


def _solver_config_object():
    from services.pypsa_service import PyPSAService

    return PyPSAService.get_active_context().solver_state["solver_config"]


def _money_basis(network) -> str:
    from models.finance import FinanceInputs
    from services.finance.report import basis_statement

    try:
        fin = FinanceInputs.model_validate(getattr(_solver_config_object(), "finance", None) or {})
        periods = list(network.investment_periods)
        base = int(periods[0]) if periods else int(network.snapshots[0].year)
        return basis_statement(fin, base)
    except Exception:  # noqa: BLE001 — a snapshot index without years states no base year
        return "money basis not stated"


def _cost_notes(component_class: str, network, attr: str, targets, rows: list[dict]) -> list[dict]:
    """The inputs a derived or solve-time annual cost follows from; no number of ours."""
    frame = getattr(network, attr)
    notes = []
    for name, attrs in targets:
        name = attrs.get("name", name)
        rate, source = _rate(frame, name)
        derived = any(r["name"] == name and r["field"] == "capital_cost" and r["role"] == "derived" for r in rows)
        if derived:
            notes.append(_note(component_class, name, "capital_cost",
                               f"Derived by the app from each investment part's overnight cost and lifetime at "
                               f"discount rate {rate:g} ({source}); {_money_basis(network)}."))
        if "overnight_cost" in attrs:
            try:
                network.components[component_class].periodized_cost
            except ValueError as exc:
                _refuse("overnight_cost_refused_here", f"PyPSA refuses overnight_cost on this network: {exc}")
            life = _scalar(frame.at[name, "lifetime"]) if "lifetime" in frame.columns else None
            text = (f"Annualised by PyPSA at solve time: overnight_cost × annuity(discount rate {rate:g} ({source}), "
                    f"lifetime {life}) × nyears, plus fom_cost; {_money_basis(network)}.")
            old_capital = _scalar(frame.at[name, "capital_cost"]) if "capital_cost" in frame.columns else None
            if isinstance(old_capital, (int, float)) and old_capital:
                text += f" It replaces the existing capital_cost {old_capital:g} at solve time."
            notes.append(_note(component_class, name, "overnight_cost", text))
    return notes


def _note(component_class: str, name: str, field: str, text: str) -> dict:
    return {"component_class": component_class, "name": name, "field": field, "label": field, "old": None,
            "new": None, "unit": "", "role": "note", "note": text}


def _component_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    network = PyPSAService.get_network()
    renamed = {row["name"]: row["new"] for row in rows if row["field"] == "name"}
    out = []
    for row in rows:
        if row["role"] == "note":
            out.append(None)
            continue
        klass = row["component_class"]
        frame = network.components[klass].static
        name = renamed.get(row["name"], row["name"])
        if row["field"] == "exists":
            out.append(name in frame.index)
        elif row["field"] == "name":
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


# ── Several assets in one reviewed preview: apply_project_changes (LC-16) ──
#
# The rows are the stored preview's, rechecked against the live inputs by
# `preview_confirmation`; the card binds that preview's review_sha256, so the
# card the user approves is provably the preview the handler applies.

def _project_changes_preview(args: dict) -> list[dict]:
    from services.assistant_tools import preview_card

    return preview_card(args.get("preview_id"))[0]


def _project_changes_binding(args: dict) -> dict:
    from services.assistant_tools import preview_card

    return preview_card(args.get("preview_id"))[1]


def _project_changes_sets(args: dict) -> list[dict]:
    from services.assistant_tools import preview_card

    return preview_card(args.get("preview_id"))[2]


register("apply_project_changes", Adapter("parameter_edit", _project_changes_preview, _component_readback,
                                           binding=_project_changes_binding, sets=_project_changes_sets))


# ── Library references: apply_library_reference ───────────────────────────


_RATE_WORDS = {"component": "this asset's own", "project setting": "the project's", "default": "the default"}


def _library_reference_preview(args: dict) -> list[dict]:
    """The native update's rows for the fields a shipped reference writes, each with
    the reference (source cell, money year) and the pack value beside it, plus one
    note per confirmation given, per projection-year note and the licence. A plan
    still missing a confirmation refuses before any card (`reference_confirmation_needed`)."""
    from services import chat_tools
    from services.library import reference_apply as A
    from services.pypsa_service import PyPSAService

    p = A.plan_active(args)
    A.require_confirmed(p)
    klass, name = p["component_class"], p["name"]
    rows = _sandbox_rows(klass, [(name, dict(p["attrs"]))],
                         lambda: chat_tools.update_component(klass, name, dict(p["attrs"])))
    beside = {row["field"]: row for row in p["rows"]}
    for row in rows:
        if row["role"] == "typed" and row["name"] == name and row["field"] in beside:
            row.update({k: beside[row["field"]][k] for k in ("reference", "pack", "differs")})
    for need in p["needs"]:
        said = need.get("disclosure") or need["text"].replace(f" {A.LEFT_UNCONFIRMED}", "")
        rows.append({**_note(klass, name, need["confirmation"], f"Confirmed: {said}"), "confirmed": True})
    # One plain annualisation line in this card's money words, in place of the generic
    # formula note (which names the pack's price-data year, not the reference's).
    rows = [r for r in rows if not (r["role"] == "note" and r["field"] == "overnight_cost")]
    frame = PyPSAService.get_network().components[klass].static
    rate, rate_source = _rate(frame, name)
    read_as = f", read as {p['base_year']} money" if p.get("base_year") not in (None, p["currency_year"]) else ""
    rows.append(_note(klass, name, "overnight_cost",
                      f"The optimisation annualises this cost over the {p['attrs']['lifetime']:g}-year lifetime "
                      f"at {_RATE_WORDS.get(rate_source, 'a')} {rate * 100:g} % discount rate. Price data: "
                      f"{p['currency_year']} {p['currency']}{read_as}."))
    rows += [_note(klass, name, "projection_year", text) for text in p["notes"]]
    rows.append(_note(klass, name, "licence", f"{p['attribution']} Licence {p['licence']}."))
    return rows


register("apply_library_reference", Adapter("parameter_edit", _library_reference_preview, _component_readback))


# ── Equipment tables: apply_equipment_mapping (data-to-model slice 1) ──────


def _equipment_table_preview(args: dict) -> list[dict]:
    """The native update's rows for every changed cell of the mapped table, each typed row
    with its source cell (`source`: upload, full sha256, sheet, row, column, raw value and
    unit), plus one note per confirmation given, per left-out cell and per basis note. A
    plan missing a confirmation refuses before any card (`table_confirmation_needed`); a
    table that changes nothing refuses (`nothing_to_change`)."""
    from services import equipment_table as T

    return _mapped_rows(T.plan_active(args), "preview_equipment_mapping")


def _mapped_rows(p: dict, counter: str) -> list[dict]:
    """The card rows of an `equipment_table.plan`-shaped plan (a table, or PDF citations):
    the native update's rows run on a copy, each typed row with its `source` (and `money`
    for a cost), then the notes. `counter` is the read tool that counts left-out gaps."""
    from services import chat_tools
    from services import equipment_table as T
    from services.pypsa_service import PyPSAService

    T.require_confirmed(p)
    todo = T.changes(PyPSAService.get_network(), p)
    if not todo:
        _refuse("nothing_to_change", "Every mapped value already equals the network's; nothing to apply.")
    T.require_card_size(todo)
    klass = p["component_class"]

    def run():
        for name, attrs in todo.items():
            chat_tools.update_component(klass, name, dict(attrs))

    rows = _sandbox_rows(klass, list(todo.items()), run)
    lineage = {(c["name"], c["field"]): c for c in p["cells"]}
    notes = []
    for row in rows:
        cell = lineage.get((row["name"], row["field"])) if row["role"] == "typed" else None
        if cell is not None:
            row["source"] = cell["source"]
            if cell["money_year"] is not None:
                row["money"] = {"currency": cell["currency"], "money_year": cell["money_year"]}
            notes += [_note(klass, row["name"], row["field"], text) for text in cell.get("notes", ())]
    rows += notes
    for need in p["needs"]:
        rows.append({**_note(klass, "", need["confirmation"], need["text"]), "confirmed": True})
    for gap in p["unmapped"][:T.MAX_LISTED_GAPS]:
        rows.append({**_note(klass, gap.get("name") or "", gap.get("field") or gap.get("column") or "",
                              gap["message"]), "gap": gap["kind"], **({"source": gap["source"]} if "source" in gap else {})})
    if len(p["unmapped"]) > T.MAX_LISTED_GAPS:
        rows.append(_note(klass, "", "", f"{len(p['unmapped']) - T.MAX_LISTED_GAPS} more cells are left out; "
                          f"{counter} counts them by kind."))
    rows += [_note(klass, "", "money_year", text) for text in p["notes"]]
    return rows


register("apply_equipment_mapping", Adapter("parameter_edit", _equipment_table_preview, _component_readback))


# ── PDF datasheets: apply_document_values (data-to-model slice 2) ──────────


def _document_values_preview(args: dict) -> list[dict]:
    """As an equipment table's card, each typed row's `source` naming the PDF's page and
    the raw quote (`services/document_values.py`); the digest binds both."""
    from services import document_values as D

    return _mapped_rows(D.plan_active(args), "preview_document_values")


register("apply_document_values", Adapter("parameter_edit", _document_values_preview, _component_readback))


def _commercial_document_values_preview(args: dict) -> list[dict]:
    """The import tariff's and PPA contracts' values cited from a PDF: one typed row per
    changed value with its page and quote and the stored config's `basis` (compare-and-
    set), then the notes (`services/document_values_commercial.py`)."""
    from services import document_values_commercial as DC

    return DC.card_rows(DC.plan_active(args))


def _commercial_document_values_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services import document_values_commercial as DC

    return DC.readback(args, rows)


register("apply_commercial_document_values", Adapter("settings_edit", _commercial_document_values_preview,
                                                     _commercial_document_values_readback))


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


# ── Undo: the snapshot it restores, against the project now ────────────────

MAX_UNDO_ROWS = 20
# PyPSA's own bookkeeping tables, rebuilt from the components; never an input.
SKIP_CLASSES = frozenset({"SubNetwork"})


def _plain(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _existence_rows(before, after, skip: set[tuple[str, str]]) -> list[dict]:
    """Components a write adds or removes besides its target (e.g. a carrier it creates)."""
    rows = []
    for component in after.components:
        klass = component.name
        if klass in SKIP_CLASSES:
            continue
        old_names = set(before.components[klass].static.index)
        new_names = set(component.static.index)
        for name in sorted(old_names ^ new_names):
            if (klass, name) in skip:
                continue
            rows.append({"component_class": klass, "name": str(name), "field": "exists", "label": "exists",
                         "old": name in old_names, "new": name in new_names, "unit": "", "role": "follows"})
    return rows


def _blank(value) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _undo_preview(args: dict) -> list[dict]:
    import pathlib
    import tempfile

    import pypsa

    from services import chat_tools, undo_service
    from services.pypsa_service import PyPSAService

    netcdf = undo_service.peek()
    if netcdf is None:
        _refuse("nothing_to_undo", "There is no change to undo.", 409)
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as handle:
        path = pathlib.Path(handle.name)
        handle.write(netcdf)
    try:
        restored = pypsa.Network()
        restored.import_from_netcdf(path)
    finally:
        path.unlink(missing_ok=True)
    now = PyPSAService.get_network()
    rows, more, described = [], 0, {}
    for component in now.components:
        klass, current = component.name, component.static
        if klass in SKIP_CLASSES:
            continue
        before = restored.components[klass].static
        defaults = component.defaults
        # A column the undone edit created (an investment part) is absent from
        # the restored frame: the undo takes it back to "not set".
        inputs = [c for c in current.columns
                  if "Input" in str(defaults.at[c, "status"] if c in defaults.index else "Input")]
        for name in current.index.union(before.index):
            if name not in before.index or name not in current.index:
                change = [("exists", name in current.index, name in before.index)]
            else:
                change = [(c, _scalar(current.at[name, c]),
                           _scalar(before.at[name, c]) if c in before.columns else None) for c in inputs]
            for field, old, new in change:
                if not (_plain(old) and _plain(new)) or (_blank(old) and _blank(new)) or _same(old, new):
                    continue
                if len(rows) >= MAX_UNDO_ROWS:
                    more += 1
                    continue
                unit = str(defaults.at[field, "unit"]) if field in defaults.index else ""
                # The app's own fields (an investment part) carry their unit and
                # label in the class schema, as the edit card that set them did.
                if klass not in described:
                    described[klass] = _fields(klass) if klass in chat_tools._COMPONENT_CREATE_SCHEMAS else {}
                known = described[klass].get(field) or {}
                unit = known.get("unit_storage") or known.get("native_unit") or unit
                rows.append(_with_display({"component_class": klass, "name": str(name), "field": field,
                                           "label": known.get("label", field), "old": old, "new": new,
                                           "unit": "" if unit in ("n/a", "nan") else unit, "role": "typed"}, known))
    if more:
        rows.append(_note("", "", "more", f"and {more} more values this undo restores"))
    if not rows:
        rows.append(_note("", "", "nothing", "The undo step restores no input value."))
    return rows


def _undo_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.pypsa_service import PyPSAService

    network = PyPSAService.get_network()
    out = []
    for row in rows:
        if row["role"] == "note":
            out.append(None)
            continue
        frame = network.components[row["component_class"]].static
        if row["field"] == "exists":
            out.append(row["name"] in frame.index)
        else:
            out.append(frame.at[row["name"], row["field"]]
                       if row["name"] in frame.index and row["field"] in frame.columns else None)
    return out


register("undo_last", Adapter("parameter_edit", _undo_preview, _undo_readback))


# ── Generated series: apply_generated_profile (TS-04) ──────────────────────
# The series the target holds now against the one the apply would write
# (`services/profile_gen/card.py`): peak, energy and load factor for a power
# target, max/mean/min for any other, notes for the hours that change, the
# apply's warnings and any refinement ops.


def _profile_preview(args: dict) -> list[dict]:
    from services.profile_gen import card

    return card.preview(args)


def _profile_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.profile_gen import card

    return card.readback(args, rows)


register("apply_generated_profile", Adapter("series_edit", _profile_preview, _profile_readback))


def _defaults_preview(args: dict) -> list[dict]:
    from services.profile_gen import card

    return card.preview_batch(args)


def _defaults_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.profile_gen import card

    return card.readback_batch(args, rows)


register("apply_default_series", Adapter("series_edit", _defaults_preview, _defaults_readback))


def _members_preview(args: dict) -> list[dict]:
    from services.profile_gen import card

    return card.preview_members(args)


def _members_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.profile_gen import card

    return card.readback_members(args, rows)


register("apply_generated_profiles", Adapter("series_edit", _members_preview, _members_readback))


# ── Any other edit: the requested arguments, no old value claimed ──────────


def _arguments_preview(args: dict) -> list[dict]:
    if not args:
        return [_note("", "", "arguments", "This change takes no values.")]
    rows = []
    for key, value in sorted(args.items()):
        text = json.dumps(value, default=str, allow_nan=True)
        rows.append({"component_class": "", "name": "", "field": key, "label": key, "old": None,
                     "old_known": False, "new": value if len(text) <= 200 else text[:200] + "…",
                     "unit": "", "role": "typed"})
    return rows


_ARGUMENTS = Adapter("arguments", _arguments_preview, lambda args, rows: [None] * len(rows))


# ── Campus island: campus_apply_gfm_settings ───────────────────────────────
# The grid-forming recommendation's apply (W10 R3): the same card for the copilot and the
# Control › Grid-forming tab. The project is the call's `project_id` in chat; the panel's route
# names the project it already authorised with `for_project`, never an argument a model supplies.

_PROJECT: ContextVar[Any] = ContextVar("edit_card_project", default=None)


@contextmanager
def for_project(project):
    """Name the (already authorised) project row the campus adapters read for this call."""
    token = _PROJECT.set(project)
    try:
        yield
    finally:
        _PROJECT.reset(token)


def _campus(args: dict, run: Callable[[Any], Any]):
    named = _PROJECT.get()
    if named is not None:
        return run(named)
    from services import chat_tools

    with chat_tools._acting() as (db, user):
        return run(chat_tools._gridspine_project(db, user, (args or {}).get("project_id")))


def _gfm_preview(args: dict) -> list[dict]:
    from services import campus_island_tuning as cit

    return _campus(args, cit.card_rows)


def _gfm_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services import campus_island_tuning as cit

    return _campus(args, lambda project: cit.card_readback(project, rows))


# Section 5: the Controls block of each converter-coupled asset (card-workspaces.md §3.3).
register("campus_apply_gfm_settings", Adapter("settings_edit", _gfm_preview, _gfm_readback, section_id="assets"))
