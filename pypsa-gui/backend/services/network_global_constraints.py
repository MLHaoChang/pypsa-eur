"""
Global-constraint CRUD specials (create / partial-PUT update / delete).

Lifted out of ``routers/network.py``. The GET stays on the router (one-line
factory). ``apply_update_global_constraint`` takes ``merge_partial_update=`` so
this module never imports ``routers.*`` — the thin handler injects the shared
MERGE helper. Never route through ``_update_component`` (that injects
``ensure_carrier``, which is wrong for a GlobalConstraint).
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from services import change_log_service
from services.pypsa_service import PyPSAService
from services.study_state import refuse_edit_during_live_study

_GC_OPTIONAL = ("carrier_attribute", "carrier", "investment_period")

# PyPSA (1.1) reads the carrier of these two types from `carrier_attribute`
# (`define_operational_limit` queries `carrier == glc.carrier_attribute`;
# `define_tech_capacity_expansion_limit` groups by it). `carrier` is not a
# GlobalConstraint attribute, but the GUI's forms and the chat write the
# carrier there, so a limit stored only in `carrier` never reached the LP.
# Both columns are kept equal for these types: `carrier_attribute` for the
# solve, `carrier` for the tables and the fuel-limit grid that read it.
_CARRIER_SCOPED = ("operational_limit", "tech_capacity_expansion_limit")


def _named(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def _scope_carrier(row: dict, sent: dict) -> None:
    """Write a carrier-scoped limit's carrier into both columns. What the request sent wins over the stored row,
    and `carrier` over `carrier_attribute` (the forms clear the attribute a type does not read)."""
    if row.get("type") not in _CARRIER_SCOPED:
        return
    candidates = (sent.get("carrier"), sent.get("carrier_attribute"), row.get("carrier"), row.get("carrier_attribute"))
    carrier = next((v for v in candidates if _named(v)), None)
    if carrier is not None:
        row["carrier"] = carrier
        row["carrier_attribute"] = carrier


def repair_carrier_scoped_limits(n) -> int:
    """A project saved before the fix: a carrier-scoped limit whose `carrier_attribute` names no carrier of the
    network (PyPSA's default "co2_emissions", or empty) takes the carrier from `carrier`. In memory only: the file
    changes on the next save. Returns how many rows it repaired."""
    gc = n.global_constraints
    if gc.empty or "carrier" not in gc.columns:
        return 0
    carriers = set(n.carriers.index)
    repaired = 0
    for name in gc.index:
        if gc.at[name, "type"] not in _CARRIER_SCOPED:
            continue
        carrier, attr = gc.at[name, "carrier"], gc.at[name, "carrier_attribute"]
        if _named(carrier) and attr != carrier and not (_named(attr) and attr in carriers):
            gc.at[name, "carrier_attribute"] = carrier
            repaired += 1
    return repaired


def apply_create_global_constraint(body) -> dict:
    refuse_edit_during_live_study()  # P27a A1: not routed via _create_component
    from services.component_names import require_valid_name

    require_valid_name(body.name, component_class="GlobalConstraint")  # item 8
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if body.name in n.global_constraints.index:
            raise HTTPException(409, f"GlobalConstraint '{body.name}' already exists")
        kwargs: dict[str, Any] = {
            "type": body.type,
            "sense": body.sense,
            "constant": float(body.constant),
        }
        for opt in _GC_OPTIONAL:
            v = getattr(body, opt, None)
            # Empty strings should not become column entries either — PyPSA
            # treats "" the same as None for these optional fields.
            if v is None or v == "":
                continue
            kwargs[opt] = v
        _scope_carrier(kwargs, kwargs)
        n.add("GlobalConstraint", body.name, **kwargs)
    change_log_service.log(
        "add", "GlobalConstraint", body.name,
        f"Added global constraint '{body.name}' ({body.type} {body.sense} {body.constant})",
    )
    return {"name": body.name}


def apply_update_global_constraint(name: str, body, *, merge_partial_update) -> dict:
    refuse_edit_during_live_study()  # P27a A1: not routed via _update_component
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if name not in n.global_constraints.index:
            raise HTTPException(404, f"GlobalConstraint '{name}' not found")
        # Real partial-PUT: same pattern as _update_component for the regular
        # component CRUD. Reads the existing row, merges `exclude_unset=True`
        # on top, so a body of `{"constant": 100}` ONLY changes constant
        # instead of resetting `type`/`sense`/`carrier_attribute`/period to
        # the Pydantic schema defaults (which was the B2 footgun before).
        # NOTE: deliberately NOT routed through _update_component — that injects
        # ensure_carrier (wrong for a GlobalConstraint). Shared MERGE only.
        sent = body.model_dump(exclude_unset=True)
        merged = merge_partial_update(n, "global_constraints", name, sent)
        _scope_carrier(merged, sent)
        new_name = merged.pop("name", name)
        if new_name != name:
            from services.component_names import require_valid_name

            # OPEN-ITEMS item 8; before the remove below.
            require_valid_name(new_name, component_class="GlobalConstraint")
        if new_name != name and new_name in n.global_constraints.index:
            # remove + add would silently replace the existing constraint.
            raise HTTPException(409, f"GlobalConstraint '{new_name}' already exists")
        n.remove("GlobalConstraint", name)
        n.add("GlobalConstraint", new_name, **merged)
    change_log_service.log(
        "update", "GlobalConstraint", new_name,
        f"Updated global constraint '{name}' → '{new_name}'",
    )
    return {"name": new_name}


def apply_delete_global_constraint(name: str) -> None:
    refuse_edit_during_live_study()  # P27a A1: not routed via _delete_component
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if name not in n.global_constraints.index:
            raise HTTPException(404, f"GlobalConstraint '{name}' not found")
        n.remove("GlobalConstraint", name)
    change_log_service.log(
        "delete", "GlobalConstraint", name,
        f"Deleted global constraint '{name}'",
    )
