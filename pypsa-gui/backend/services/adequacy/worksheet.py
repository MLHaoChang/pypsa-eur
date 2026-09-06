"""
The FMEA worksheet sidecar — per-project MANUAL state only.

Design: spec §§4.2, 8.3; plan 2026-08-28-fmea-phase3-worksheet.md. Two kinds
of user-authored data, persisted as ``adequacy_worksheet.json`` in the
project directory (schema-versioned JSON via atomic_io — no pickle, human-
diffable):

* ``manual_rows`` — class-D expert failure modes, each a full contract
  ``FailureModeResult`` with engine="expert" / fidelity="expert_judgement"
  (the Phase 3 provenance literals). Anything else is rejected: an expert
  row masquerading as an engine row would forge provenance.
* ``overlays`` — ``{mode_id: {mitigability, notes?}}`` annotations that the
  CLIENT re-attaches to computed rows after they regenerate. Computed rows
  are deliberately never stored here, which is the whole survival story:
  a re-solve regenerates the computed side and cannot touch this file.

Reject-don't-truncate caps keep the sidecar bounded; a failed save leaves
the previous file byte-identical (atomic write, validation before write).
"""
from __future__ import annotations

import json
import pathlib

from pydantic import ValidationError

from models.adequacy import FailureModeResult
from services.atomic_io import atomic_write_text

SIDECAR_NAME = "adequacy_worksheet.json"
SCHEMA = 1
MAX_MANUAL_ROWS = 200
MAX_TEXT_LEN = 2000
_OVERLAY_KEYS = {"mitigability", "notes"}


class WorksheetValidationError(ValueError):
    pass


def _empty() -> dict:
    return {"__schema__": SCHEMA, "version": 0, "manual_rows": [], "overlays": {}}


def load_worksheet(project_dir: pathlib.Path) -> dict:
    path = project_dir / SIDECAR_NAME
    if not path.exists():
        return _empty()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(raw, dict) or raw.get("__schema__") != SCHEMA:
        return _empty()
    return {
        "__schema__": SCHEMA,
        "version": int(raw.get("version", 0) or 0),
        "manual_rows": list(raw.get("manual_rows") or []),
        "overlays": dict(raw.get("overlays") or {}),
    }


def _validate(manual_rows: list[dict], overlays: dict) -> None:
    if len(manual_rows) > MAX_MANUAL_ROWS:
        raise WorksheetValidationError(
            f"too many manual rows ({len(manual_rows)} > {MAX_MANUAL_ROWS})")
    for row in manual_rows:
        try:
            fm = FailureModeResult.model_validate(row)
        except ValidationError as exc:
            raise WorksheetValidationError(f"invalid manual row: {exc}") from exc
        if fm.engine != "expert" or fm.fidelity != "expert_judgement":
            raise WorksheetValidationError(
                f"manual row '{fm.name}' must carry engine='expert' / "
                "fidelity='expert_judgement' — expert rows never impersonate "
                "an engine's provenance"
            )
        if fm.failure_class != "D":
            raise WorksheetValidationError(
                f"manual row '{fm.name}' must be failure class D — computed "
                "classes regenerate from their engines and are not stored here"
            )
        if fm.mitigability is not None and len(fm.mitigability) > MAX_TEXT_LEN:
            raise WorksheetValidationError(
                f"manual row '{fm.name}': mitigability exceeds {MAX_TEXT_LEN} chars")
    for mode_id, overlay in overlays.items():
        if not isinstance(mode_id, str) or not mode_id:
            raise WorksheetValidationError("overlay keys must be mode_id strings")
        if not isinstance(overlay, dict):
            raise WorksheetValidationError(f"overlay '{mode_id}' must be an object")
        extra = set(overlay) - _OVERLAY_KEYS
        if extra:
            raise WorksheetValidationError(
                f"overlay '{mode_id}' has unknown keys {sorted(extra)}")
        for k, v in overlay.items():
            if not isinstance(v, str):
                raise WorksheetValidationError(
                    f"overlay '{mode_id}'.{k} must be a string")
            if len(v) > MAX_TEXT_LEN:
                raise WorksheetValidationError(
                    f"overlay '{mode_id}'.{k} exceeds {MAX_TEXT_LEN} chars")


def save_worksheet(project_dir: pathlib.Path, *, manual_rows: list[dict],
                   overlays: dict) -> dict:
    """Validate, then replace the whole manual state (payloads are small).
    Returns the saved state incl. the bumped version. Validation runs
    BEFORE the write, so a rejected save leaves the file untouched."""
    _validate(manual_rows, overlays)
    current = load_worksheet(project_dir)
    state = {
        "__schema__": SCHEMA,
        "version": current["version"] + 1,
        "manual_rows": manual_rows,
        "overlays": overlays,
    }
    atomic_write_text(project_dir / SIDECAR_NAME,
                      json.dumps(state, indent=2, sort_keys=True))
    return state
