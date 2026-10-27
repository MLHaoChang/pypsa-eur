"""
The sweep card (LC-17): what a chat sensitivity sweep shows before it runs.

`run_sensitivity_sweep` is the one path that creates and queues sensitivity
cases: the experiment tools (`advance_experiment`) hand it their exact call
too. So its confirmation carries this card, whichever way the call came:

* per case: its name (derived from its changes when the call gave none, and
  marked so), whether it is an existing case, and one row per asset and field,
  `entity · field · base → case · unit`, with the base read from the SAVED
  baseline (the one the sweep branches), never the open editor;
* `solves`: how many solves approving it queues (an existing, solved case with
  matching inputs queues none).

It refuses before any card: an unsaved baseline (`baseline_not_saved`; the
assistant offers `save_project`, its own card, and never saves silently), an
asset the baseline lacks, and a name another project already holds with other
inputs (`sensitivity_name_conflict`: the assistant asks for a new name).
`require_current` re-builds the card just before the sweep runs; a card whose
digest moved refuses (`sweep_preview_stale`).
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from fastapi import HTTPException

VERSION = 1
MAX_ROWS = 100  # every row is shown; a larger sweep is split by the user

# Field words in a derived name: "battery-capex-70pct", never the raw column.
_WORDS = {"capital_cost": "capex", "marginal_cost": "mc", "efficiency": "eff",
          "efficiency_store": "eff-store", "efficiency_dispatch": "eff-dispatch", "max_hours": "hours"}
# A rating is the asset's size: its name carries only the value ("lines-500").
_SIZES = frozenset({"p_nom", "s_nom", "e_nom"})
_PLURAL = {"Generator": "generators", "Load": "loads", "Line": "lines", "Link": "links",
           "StorageUnit": "storage-units", "Store": "stores"}


def _refuse(kind: str, message: str, status: int = 409):
    raise HTTPException(status, {"error_kind": kind, "message": message})


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-")


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}".replace(".", "p").replace("-", "minus")


def _amount(change: dict, bases: list) -> str:
    value = change["value"]
    if isinstance(value, bool):
        return "on" if value else "off"
    # A change that scales one common base reads as a share of it ("70pct").
    if bases and all(_finite(b) and b == bases[0] for b in bases) and bases[0] not in (0,) and value != bases[0]:
        pct = round(value / bases[0] * 100, 6)
        if pct > 0 and pct.is_integer():
            return f"{int(pct)}pct"
    return _number(value)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def case_name(changes: list[dict], network) -> str:
    """A case's name from its changes, e.g. "battery-capex-70pct-lines-500"."""
    parts = []
    for change in changes:
        frame = network.components[change["component_class"]].static
        names = change["names"]
        bases = [frame.at[n, change["attribute"]] for n in names
                 if n in frame.index and change["attribute"] in frame.columns]
        entity = _slug(names[0]) if len(names) == 1 else _PLURAL.get(change["component_class"], _slug(change["component_class"]) + "s")
        field = None if change["attribute"] in _SIZES else _WORDS.get(change["attribute"], change["attribute"])
        parts += [entity, *([field] if field else []), _amount(change, bases)]
    name = _slug("-".join(_slug(p) for p in parts)) or "sensitivity"
    while len(name) > 64:  # the project-name cap, cut at a word boundary
        name = name.rsplit("-", 1)[0] if "-" in name[:64] else name[:64]
    return name


def checked(cases) -> list:
    """The cases, refused (`invalid_sensitivity_cases`) unless each has the
    sweep's shape: 1–4 cases, an optional string name, 1–20 typed changes."""
    from pydantic import ValidationError

    from services.workflow_tools import SensitivityChange

    def bad():
        _refuse("invalid_sensitivity_cases", "Each case needs 1–20 changes, each with component_class, "
                "names (1–100), attribute and a number or boolean value.", 422)
    if not isinstance(cases, list) or not 1 <= len(cases) <= 4:
        _refuse("invalid_sensitivity_cases", "Provide between 1 and 4 cases per sweep.", 422)
    for case in cases:
        if (not isinstance(case, dict) or set(case) - {"new_name", "changes"}
                or not isinstance(case.get("new_name") or "", str)
                or not isinstance(case.get("changes"), list) or not 1 <= len(case["changes"]) <= 20):
            bad()
        try:
            for change in case["changes"]:
                SensitivityChange.model_validate(change)
        except ValidationError:
            bad()
    return cases


def named(cases: list, network) -> list:
    """The cases, checked, with each missing `new_name` derived from its changes."""
    checked(cases)
    return [{**case, "new_name": case_name(case.get("changes") or [], network)}
            if isinstance(case, dict) and not case.get("new_name") else case for case in cases]


def _value(value):
    if isinstance(value, bool) or value is None:
        return value
    if hasattr(value, "item"):
        value = value.item()
    return bool(value) if isinstance(value, bool) else float(value) if _finite(value) else None


def _rows(network, change: dict) -> list[dict]:
    from harness.edit_card import _carrier_unit, _fields

    cls, attr = change["component_class"], change["attribute"]
    frame = network.components[cls].static
    field = _fields(cls).get(attr) or {}
    rows = []
    for name in change["names"]:
        if name not in frame.index:
            _refuse("unknown_component", f"{cls} “{name}” is not in the saved baseline.", 422)
        base = _value(frame.at[name, attr]) if attr in frame.columns else None
        row = {"component_class": cls, "name": name, "field": attr, "label": field.get("label") or attr,
               "base": base, "case": change["value"],
               "unit": _carrier_unit(field.get("unit_storage") or field.get("native_unit") or "", network, frame, name)}
        scale = field.get("display_scale")
        if scale not in (None, 1, 1.0) and _finite(change["value"]):
            row["display"] = {"base": base * scale if _finite(base) else None,
                              "case": change["value"] * scale, "unit": field.get("unit_display")}
        rows.append(row)
    return rows


def _digest(card: dict) -> str:
    body = {k: v for k, v in card.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def preview(args: dict) -> dict:
    """The card for one `run_sensitivity_sweep` call, or a refusal before any card."""
    from services import chat_tools, dirty_state, gridspine_service as gs, project_registry
    from services import workflow_tools as wt
    from services.dispatch_status import dispatch_status
    from services.pypsa_service import PyPSAService

    cases = checked(args.get("cases"))
    with chat_tools._acting() as (db, user):
        base = project_registry.resolve_project(db, user, str(args.get("baseline_project_id") or ""))
        if gs.kind_of(base) != gs.CAPACITY_EXPANSION:
            _refuse("unsupported_sensitivity_project", "This sweep supports network component parameters; use GridSpine config tools for study refinement.")
        resident = PyPSAService.get_context(project_registry.registry_key(base))
        if resident is None and str(PyPSAService.get_active_context().project_uuid) == str(base.id):
            resident = PyPSAService.get_active_context()
        if resident is not None and dirty_state.is_dirty(resident):
            _refuse("baseline_not_saved", f"The baseline “{base.name}” has unsaved changes and a sweep branches the saved "
                    "file. Save it first (save_project shows its own card); nothing was saved or created.")
        try:
            network = wt._load_context(base).network
        except Exception:  # noqa: BLE001 — an unreadable saved baseline shows no card
            _refuse("sweep_preview_unavailable", f"The saved baseline “{base.name}” could not be read; "
                    "open and save it, then try again.")
        out, solves, total = [], 0, 0
        for case in named(cases, network):
            rows = [row for change in case["changes"] for row in _rows(network, change)]
            total += len(rows)
            if total > MAX_ROWS:
                _refuse("sweep_card_too_large", f"One card shows at most {MAX_ROWS} asset rows; split the sweep.", 422)
            entry = {"name": case["new_name"], "derived": not any(c is case for c in cases), "existing": False, "rows": rows}
            existing = project_registry.find_project(db, user, case["new_name"])
            queues = True
            if existing is not None:
                existing = project_registry.resolve_project(db, user, str(existing.id))
                old = wt._read_json(project_registry.project_dir(existing) / wt.SENSITIVITY_CASE_FILE)
                if existing.parent_project_id != base.id or old.get("case") != {"new_name": case["new_name"], "changes": case["changes"]}:
                    _refuse("sensitivity_name_conflict", f"A project named “{case['new_name']}” already exists with other "
                            f"inputs. The case needs another name (for example “{case['new_name']}-2”).")
                entry["existing"] = True
                saved = wt._load_context(existing).network
                queues = not (dispatch_status(saved) == "fresh" and getattr(saved, "objective", None) is not None
                              and wt._active_job(existing) is None)
            solves += queues
            out.append(entry)
    card = {"version": VERSION, "kind": "sweep", "tool": "run_sensitivity_sweep",
            "baseline": {"project_id": str(base.id), "name": base.name}, "cases": out, "solves": solves,
            "says": f"{len(out)} sensitivity case{'' if len(out) == 1 else 's'} from {base.name}: "
                    + "; ".join(c["name"] for c in out) + f"; queues {solves} solve{'' if solves == 1 else 's'}"}
    card["digest"] = _digest(card)
    return card


def require_current(args: dict, card: dict) -> None:
    """Just before the sweep runs: the card the user approved still holds."""
    if preview(args)["digest"] != card["digest"]:
        _refuse("sweep_preview_stale", "The baseline or a case changed since the card; review a new card.")
