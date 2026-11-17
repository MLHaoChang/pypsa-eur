"""
The system type map's confirmation data (homer-parity 03b-1, GK B V9).

`compare_system_types` queues cells x architectures solves, so its
confirmation carries what approving it runs:

* ``shown``: the arguments the card shows, with the defaults filled in (the
  study, both variables, every axis value, the architectures named, the
  budget), the one thing the digest freezes besides the saved base;
* ``base_fingerprint``: the saved base project's input fingerprint, so a base
  edit after approval is refused at execution (`require_current`);
* ``architectures`` and ``solves``: derived from the saved base and the axes,
  NOT in the digest. At execution they must equal what the card computed at
  approval (`require_current` refuses on a mismatch).

Until the map UI (03b-2) renders this card, the card the user sees is the
plain-arguments card, so the digest binds only that. The architecture labels
and the solve count enter the digest in 03b-2, together with their rendering.

It refuses before any card (nothing is approvable): an unsaved base
(``baseline_not_saved``), a bad axis, a variable the project cannot vary
(``variable_not_in_network``), an unknown architecture, and a map over the
campaign limit (``campaign_budget_invalid``).
"""

from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException

VERSION = 1


def _refuse(kind: str, message: str, status: int = 409):
    raise HTTPException(
        status_code=status, detail={"error_kind": kind, "message": message}
    )


def digest_of(shown: dict, base_fingerprint: str) -> str:
    """The digest of the shown arguments plus the saved base's revision, and
    nothing else."""
    body = {"shown": shown, "base_fingerprint": base_fingerprint}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str, allow_nan=False).encode()
    ).hexdigest()


def preview(args: dict) -> dict:
    """The card for one `compare_system_types` start, or a refusal before any card."""
    from routers import studies as R
    from services import chat_tools, project_registry
    from services.adequacy import campaign
    from services.pypsa_service import PyPSAService
    from services.study import runner as study_runner
    from services.study import supply_architecture_runner as SAR
    from services.study import supply_architectures as SA
    from services.study import system_type_map as M

    try:
        xa, ya = M.check_axes(
            {"variable": args.get("x_variable"), "values": args.get("x_values")},
            {"variable": args.get("y_variable"), "values": args.get("y_values")},
        )
    except M.MapRefused as exc:
        _refuse(exc.code, exc.detail, 422)
    ap = chat_tools._authorized_project(str(args.get("project") or ""))
    with chat_tools._acting() as (db, user):
        base = R._base_row(ap, db, user)
    if base is None:
        _refuse("project_not_found", "The study's base project was not found.", 404)
    resident = PyPSAService.get_context(project_registry.registry_key(base))
    try:
        SAR._refuse_unsaved(resident)
        network, _cfg, fingerprint = SAR._saved_base(base)
        archs = SA.select(network, args.get("architectures"))
    except study_runner.RunRefused as exc:
        _refuse(exc.code, exc.message, exc.status)
    except SA.ArchitectureUnsolved as exc:
        _refuse(
            exc.code, exc.detail, 422 if exc.code == "unknown_architecture" else 409
        )
    groups = SA.classify(network)
    for var, values in (xa, ya):
        if not M.apply_variable(network.copy(), var, values[0], groups).scaled:
            _refuse(
                "variable_not_in_network",
                f"{var.label} changes nothing in this project: it has no such "
                "asset (or none the optimiser sizes) or its value is zero",
                422,
            )
    cells = len(xa[1]) * len(ya[1])
    too_many = M.solves_refusal(cells, len(archs), campaign.MAX_BUDGET_SOLVES)
    if too_many:
        _refuse("campaign_budget_invalid", too_many, 422)
    shown = {
        "project": str(args.get("project") or ""),
        "study_id": str(args.get("study_id") or ""),
        "x": {"variable": xa[0].variable_id, "values": xa[1]},
        "y": {"variable": ya[0].variable_id, "values": ya[1]},
        "architectures": args.get("architectures"),
        "budget_solves": args.get("budget_solves"),
    }
    return {
        "version": VERSION,
        "kind": "system_type_map",
        "tool": "compare_system_types",
        "shown": shown,
        "base_fingerprint": fingerprint,
        "architectures": [a.architecture_id for a in archs],
        "solves": cells * len(archs),
        "digest": digest_of(shown, fingerprint),
    }


def require_current(args: dict, card: dict) -> None:
    """Just before the map runs: the base is the one approved and the derived
    architectures and solve count are what the card computed."""
    now = preview(args)
    if now["digest"] != card["digest"]:
        _refuse(
            "system_map_preview_stale",
            "The base project or the request changed since the card; review a new card.",
        )
    if (now["architectures"], now["solves"]) != (card["architectures"], card["solves"]):
        _refuse(
            "system_map_preview_stale",
            "The architectures or the solve count differ from the card; review a new card.",
        )
