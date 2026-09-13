"""
Where a per-asset outage rate CAME FROM — the provenance ledger.

`occurrence.resolve_outage_params` already resolves a rate three ways:
``asset`` (someone typed it on the component), ``carrier_default`` (the NERC
GADS-style class library, which carries its own citation), or ``missing``.
Two of those three explain themselves. The first does not, and it is the one
that matters: a condition-based rate is the whole claim behind
condition-based reliability, and

    lambda = 0.031 because a drone survey found conductor damage on span 14

and

    lambda = 0.031 because someone typed it

are entirely different statements that the model records identically. This
module stores the difference, per project, as ``asset_health.json``.

**It never sets a rate.** The values live where they always did — on the
component columns, written through the ordinary edit paths — and this file
records what was applied, by what method, measured when. The two can
therefore disagree, and `provenance_report` is what makes that visible:
a ledger nobody can contradict is decoration, not provenance.

That split is also the shipped rule for sidecars: the route serves the file
and nothing else, because "the server never mixes foreground network state
with on-disk project state" (see `routers/adequacy_worksheet`). The fusion
happens one layer up, in a caller that knows WHICH network it is looking at.

Scope: one CURRENT record per asset. Inspection history — the same asset
measured every spring for a decade — is a different feature with a different
shape, and pretending a one-row-per-asset file can hold it would produce a
ledger that silently keeps the last write.
"""
from __future__ import annotations

import json
import math
import pathlib
import re

from services.adequacy.occurrence import VALID_BASES, resolve_outage_params
from services.atomic_io import atomic_write_text

SIDECAR_NAME = "asset_health.json"
SCHEMA = 1
MAX_ENTRIES = 5000
MAX_TEXT_LEN = 2000

# The component frames that carry occurrence columns — the same five
# `validation_service._check_outage_params` walks.
VALID_COMPONENTS = ("generators", "storage_units", "stores", "links", "lines")

# How the number was obtained. `expert_judgement` is on the list ON PURPOSE:
# the alternative to naming it is not rigour, it is people picking whichever
# label looks most defensible. An honest "an engineer's estimate" is worth
# more than a laundered "inspection".
VALID_METHODS = (
    "inspection",          # a survey of the physical asset (drone, walkdown, LiDAR)
    "sensor",              # condition monitoring — DGA, partial discharge, thermal
    "lab_test",            # a sample tested off the asset
    "vendor_datasheet",    # the manufacturer's published figure
    "operating_history",   # this asset's own outage record
    "fleet_statistic",     # a class average narrowed to this asset
    "expert_judgement",    # a person's estimate, named as one
)
VALID_CONFIDENCE = ("low", "medium", "high")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Relative tolerance for "the ledger still describes the network". Floats make
# a round trip through JSON and a DataFrame; an exact compare would report
# drift on every entry that never changed.
_DRIFT_RTOL = 1e-9


class AssetHealthValidationError(ValueError):
    pass


def _empty() -> dict:
    return {"__schema__": SCHEMA, "version": 0, "entries": []}


def load_asset_health(project_dir: pathlib.Path) -> dict:
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
        "entries": list(raw.get("entries") or []),
    }


def _text(entry: dict, key: str, *, required: bool) -> str | None:
    value = entry.get(key)
    if value is None or value == "":
        if required:
            raise AssetHealthValidationError(f"entry is missing '{key}'")
        return None
    if not isinstance(value, str):
        raise AssetHealthValidationError(f"'{key}' must be a string")
    if len(value) > MAX_TEXT_LEN:
        raise AssetHealthValidationError(
            f"'{key}' exceeds {MAX_TEXT_LEN} characters")
    return value


def _number(entry: dict, key: str) -> float | None:
    value = entry.get(key)
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise AssetHealthValidationError(f"'{key}' must be a number") from exc
    if not math.isfinite(out):
        raise AssetHealthValidationError(f"'{key}' must be finite")
    return out


def _validate_entry(entry: dict) -> dict:
    """
    One normalised entry, or raise.

    Unknown keys are refused, not dropped: a misspelled `mttr_hrs` that
    vanished silently would read as a recorded repair time that was never
    recorded.
    """
    if not isinstance(entry, dict):
        raise AssetHealthValidationError("each entry must be an object")
    known = {"component", "name", "outage_rate_value", "outage_rate_basis",
             "mttr_hours", "method", "source_ref", "measured_at",
             "confidence", "note"}
    extra = set(entry) - known
    if extra:
        raise AssetHealthValidationError(f"unknown keys {sorted(extra)}")

    component = _text(entry, "component", required=True)
    if component not in VALID_COMPONENTS:
        raise AssetHealthValidationError(
            f"component '{component}' carries no outage columns; expected one "
            f"of {', '.join(VALID_COMPONENTS)}")
    name = _text(entry, "name", required=True)

    rate = _number(entry, "outage_rate_value")
    if rate is not None and not (0.0 <= rate < 1.0):
        raise AssetHealthValidationError(
            f"'{name}': outage_rate_value must be in [0, 1) (got {rate})")
    mttr = _number(entry, "mttr_hours")
    if mttr is not None and mttr <= 0:
        raise AssetHealthValidationError(
            f"'{name}': mttr_hours must be positive (got {mttr})")
    if rate is None and mttr is None:
        raise AssetHealthValidationError(
            f"'{name}': an entry must record a rate or an MTTR — provenance "
            "for no number is not provenance")

    basis = _text(entry, "outage_rate_basis", required=False)
    if basis is not None and basis not in VALID_BASES:
        raise AssetHealthValidationError(
            f"'{name}': outage_rate_basis must be one of "
            f"{', '.join(VALID_BASES)} (got '{basis}')")

    method = _text(entry, "method", required=True)
    if method not in VALID_METHODS:
        raise AssetHealthValidationError(
            f"'{name}': method must be one of {', '.join(VALID_METHODS)} "
            f"(got '{method}')")
    # Required, and this is the point of the feature: a condition measurement
    # with no date is not a measurement. A 2019 inspection presented as
    # today's condition is the failure mode this whole ledger exists to stop.
    measured_at = _text(entry, "measured_at", required=True)
    if not _ISO_DATE.match(measured_at):
        raise AssetHealthValidationError(
            f"'{name}': measured_at must be an ISO date (YYYY-MM-DD), got "
            f"'{measured_at}'")

    confidence = _text(entry, "confidence", required=False)
    if confidence is not None and confidence not in VALID_CONFIDENCE:
        raise AssetHealthValidationError(
            f"'{name}': confidence must be one of "
            f"{', '.join(VALID_CONFIDENCE)} (got '{confidence}')")

    return {
        "component": component,
        "name": name,
        "outage_rate_value": rate,
        "outage_rate_basis": basis,
        "mttr_hours": mttr,
        "method": method,
        "source_ref": _text(entry, "source_ref", required=False),
        "measured_at": measured_at,
        "confidence": confidence,
        "note": _text(entry, "note", required=False),
    }


def validate_entries(entries: list) -> list[dict]:
    if not isinstance(entries, list):
        raise AssetHealthValidationError("entries must be a list")
    if len(entries) > MAX_ENTRIES:
        raise AssetHealthValidationError(
            f"too many entries ({len(entries)} > {MAX_ENTRIES})")
    out, seen = [], set()
    for entry in entries:
        normalised = _validate_entry(entry)
        key = (normalised["component"], normalised["name"])
        if key in seen:
            raise AssetHealthValidationError(
                f"duplicate entry for {key[0]}/{key[1]} — this ledger holds "
                "one CURRENT record per asset, so a second row would silently "
                "win and the first would look recorded when it is not")
        seen.add(key)
        out.append(normalised)
    return out


def save_asset_health(project_dir: pathlib.Path, entries: list) -> dict:
    """
    Validate, then replace the ledger whole.

    A rejected save leaves the previous file byte-identical — validation runs
    before the write.
    """
    validated = validate_entries(entries)
    current = load_asset_health(project_dir)
    state = {
        "__schema__": SCHEMA,
        "version": current["version"] + 1,
        "entries": validated,
    }
    atomic_write_text(project_dir / SIDECAR_NAME,
                      json.dumps(state, indent=2, sort_keys=True))
    return state


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return math.isclose(a, b, rel_tol=_DRIFT_RTOL, abs_tol=_DRIFT_RTOL)


def provenance_report(n, entries: list[dict]) -> dict:
    """
    Reconcile the ledger against a network.

    Four findings, each a different remedy:

      * ``unsourced``  — the network carries an ASSET-level rate (it overrides
        the carrier library) with no ledger entry. This is the finding the
        module exists for: an unexplained override.
      * ``drifted``    — the ledger and the network disagree on the number, so
        one of them is stale and the provenance no longer describes what the
        engines will read.
      * ``orphaned``   — the ledger names an asset the network does not have
        (renamed, deleted, or the wrong project).
      * ``sourced``    — matched, current, explained.

    Pure: reads both sides, writes neither.
    """
    by_key = {(e["component"], e["name"]): e for e in entries}
    sourced, drifted, orphaned, unsourced = [], [], [], []
    matched: set[tuple[str, str]] = set()

    for component in VALID_COMPONENTS:
        df = getattr(n, component, None)
        if df is None or df.empty:
            continue
        try:
            resolved = resolve_outage_params(n, component)
        except Exception:  # noqa: BLE001 — a broken frame is not our finding
            continue
        for name in df.index:
            key = (component, str(name))
            entry = by_key.get(key)
            is_asset_level = str(resolved.at[name, "source"]) == "asset"
            if entry is None:
                if is_asset_level:
                    unsourced.append({
                        "component": component, "name": str(name),
                        "outage_rate_value": float(resolved.at[name, "rate"]),
                        "reason": (
                            "this rate overrides the carrier library and has "
                            "no recorded source"),
                    })
                continue
            matched.add(key)
            network_rate = (float(resolved.at[name, "rate"])
                            if is_asset_level else None)
            if not _close(entry["outage_rate_value"], network_rate):
                drifted.append({
                    "component": component, "name": str(name),
                    "recorded": entry["outage_rate_value"],
                    "on_network": network_rate,
                    "reason": (
                        "the recorded measurement no longer matches the value "
                        "the engines will read"),
                })
            else:
                sourced.append({
                    "component": component, "name": str(name),
                    "outage_rate_value": entry["outage_rate_value"],
                    "method": entry["method"],
                    "measured_at": entry["measured_at"],
                    "confidence": entry["confidence"],
                })

    for key, entry in by_key.items():
        if key in matched:
            continue
        orphaned.append({
            "component": key[0], "name": key[1], "method": entry["method"],
            "reason": "no such asset on this network",
        })

    return {
        "sourced": sourced,
        "unsourced": unsourced,
        "drifted": drifted,
        "orphaned": orphaned,
        "counts": {
            "sourced": len(sourced), "unsourced": len(unsourced),
            "drifted": len(drifted), "orphaned": len(orphaned),
        },
    }
