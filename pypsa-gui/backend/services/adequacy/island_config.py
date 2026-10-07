"""
The island sidecar ``island_config.json`` (plan 2026-10-07 campus island, I1).

It holds, per PyPSA component, the converter control mode and the unit
dynamics an island study needs (droop, inertia, governor, UPS behaviour,
critical loads), plus the study requirements (ride-through, scenarios,
frequency limits).

**The schema is gridspine's** (``gridspine/schema/island.py``). gridspine's
campus draft and its island producers read the same file, and pypsa-gui
imports gridspine, never the reverse, so this module only loads the file and
hands it to gridspine's validator. It defines no model of its own.

**The import is guarded**, as in ``services/gridspine_service.py``: a build
without gridspine (``test_packaging_requirements.OPTIONAL_AT_RUNTIME``) then
says what is missing instead of failing three frames down.
"""
from __future__ import annotations

import json
import pathlib

try:
    from gridspine.schema.contracts import ContractError
    from gridspine.schema.island import validate_island_config

    GRIDSPINE_AVAILABLE = True
    GRIDSPINE_IMPORT_ERROR = None
except ImportError as exc:                      # pragma: no cover - build-shape branch
    GRIDSPINE_AVAILABLE = False
    GRIDSPINE_IMPORT_ERROR = str(exc)

    class ContractError(ValueError):
        """Stand-in so callers' ``except ContractError`` still works."""

    validate_island_config = None

SIDECAR_NAME = "island_config.json"


def validate(raw: dict) -> dict:
    """The sidecar's content, validated by gridspine."""
    if not GRIDSPINE_AVAILABLE:
        raise ContractError(
            f"the island data needs gridspine, which this build does not carry ({GRIDSPINE_IMPORT_ERROR})")
    return validate_island_config(raw)


def load_island_config(path: str | pathlib.Path) -> dict | None:
    """The validated sidecar at ``path``, or None when the project has none.
    A file that is not JSON, or not a valid island config, is a
    ``ContractError`` naming the problem."""
    path = pathlib.Path(path)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path.name} is not valid JSON: {exc}") from exc
    return validate(raw)
