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
    from gridspine.schema.island import (
        GFM_CONTROLS,
        ISLAND_HOURLY_CSV,
        ISLAND_UNIT_HOURLY_COLUMNS,
        ISLAND_UNIT_HOURLY_CSV,
        PRESETS,
        SERVICES,
        has_grid_former,
        offered_services,
        select_preset,
        validate_island_config,
        validate_island_hourly,
        validate_island_unit_hourly,
    )

    GRIDSPINE_AVAILABLE = True
    GRIDSPINE_IMPORT_ERROR = None
except ImportError as exc:                      # pragma: no cover - build-shape branch
    GRIDSPINE_AVAILABLE = False
    GRIDSPINE_IMPORT_ERROR = str(exc)

    class ContractError(ValueError):
        """Stand-in so callers' ``except ContractError`` still works."""

    validate_island_config = None
    GFM_CONTROLS = frozenset()
    # The control presets (ticket 04): none without gridspine.
    PRESETS, SERVICES = {}, ()

    def _needs_gridspine(*args, **kwargs):
        raise ContractError(f"the control presets need gridspine ({GRIDSPINE_IMPORT_ERROR})")

    has_grid_former = offered_services = select_preset = _needs_gridspine
    # The I3a hourly tables (plan I3a): without gridspine there are none to write.
    ISLAND_UNIT_HOURLY_CSV = "island_unit_hourly.csv"
    ISLAND_HOURLY_CSV = "island_hourly.csv"
    ISLAND_UNIT_HOURLY_COLUMNS = ()

    def validate_island_unit_hourly(df):
        raise ContractError(f"the island tables need gridspine ({GRIDSPINE_IMPORT_ERROR})")

    validate_island_hourly = validate_island_unit_hourly

SIDECAR_NAME = "island_config.json"

#: The typed warning ``island_warnings`` raises when the effective config has
#: nothing to form the island's voltage (no GFM bess, no genset).
NO_GRID_FORMER = "preset_leaves_no_grid_former"


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


def island_warnings(cfg: dict) -> list[dict]:
    """Typed warnings ``{code, message}`` on a validated sidecar: today, an
    island with no grid former (a bess on a GFM control, or a genset), which
    a project preset such as ``grid_following`` can leave."""
    if has_grid_former(cfg):
        return []
    why = f" under preset {cfg['preset']!r}" if cfg.get("preset") else ""
    return [{"code": NO_GRID_FORMER,
             "message": f"no unit forms the island's voltage{why}: no bess on a grid-forming control and "
                        "no genset, so the island cannot hold itself up"}]
