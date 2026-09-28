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

_GC_OPTIONAL = ("carrier_attribute", "carrier", "investment_period")


def apply_create_global_constraint(body) -> dict:
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
        n.add("GlobalConstraint", body.name, **kwargs)
    change_log_service.log(
        "add", "GlobalConstraint", body.name,
        f"Added global constraint '{body.name}' ({body.type} {body.sense} {body.constant})",
    )
    return {"name": body.name}


def apply_update_global_constraint(name: str, body, *, merge_partial_update) -> dict:
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
        merged = merge_partial_update(
            n, "global_constraints", name, body.model_dump(exclude_unset=True)
        )
        new_name = merged.pop("name", name)
        n.remove("GlobalConstraint", name)
        n.add("GlobalConstraint", new_name, **merged)
    change_log_service.log(
        "update", "GlobalConstraint", new_name,
        f"Updated global constraint '{name}' → '{new_name}'",
    )
    return {"name": new_name}


def apply_delete_global_constraint(name: str) -> None:
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        if name not in n.global_constraints.index:
            raise HTTPException(404, f"GlobalConstraint '{name}' not found")
        n.remove("GlobalConstraint", name)
    change_log_service.log(
        "delete", "GlobalConstraint", name,
        f"Deleted global constraint '{name}'",
    )
