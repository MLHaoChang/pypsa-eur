"""
Per-field provenance of asset parameters (uniform asset parameterisation, rule 7).

Stored in `n.meta["asset_params"][class][name][field]`, so it travels with the network through netCDF, undo and
project saves. This module is the only writer: the Expert card and the chat tool stamp a changed field
`customised` (through `network_crud._update_component`), a pack or template stamps what it wrote with `record`,
and the guided ledger (S2) reads `get` and projects it onto its rows. It never keeps a store of its own.

An absent entry means "not recorded", which is not the same as `default` (ADR-0001: unknown is not a value).
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

STATUSES = ("default", "customised", "measured")

_KEY = "asset_params"


def _root(n) -> dict:
    meta = n.meta if isinstance(getattr(n, "meta", None), dict) else {}
    if meta is not getattr(n, "meta", None):
        n.meta = meta
    return meta.setdefault(_KEY, {})


def _prune(n, component_class: str, name: str) -> None:
    root = _root(n)
    cls = root.get(component_class)
    if cls is not None and not cls.get(name):
        cls.pop(name, None)
    if cls is not None and not cls:
        root.pop(component_class, None)


def get(n, component_class: str, name: str) -> dict[str, dict[str, Any]]:
    """Every recorded field of one asset, `{field: entry}`; `{}` when nothing is recorded."""
    meta = getattr(n, "meta", None)
    if not isinstance(meta, dict):
        return {}
    return {k: dict(v) for k, v in meta.get(_KEY, {}).get(component_class, {}).get(name, {}).items()}


def record(n, component_class: str, name: str, field: str, *, status: str, source: str | None = None,
           source_year: int | None = None, currency_year: int | None = None,
           range: tuple[float, float] | None = None, illustrative: bool = False,
           currency: str | None = None, fallback: str | None = None,
           approved_by: str | None = None, approved_at: str | None = None,
           replaces: str | None = None) -> None:
    """Write one field's provenance, replacing what was there. `currency` (the value's ISO
    currency: a regional pack's, or an applied Library reference's) and `fallback`
    (`eu_fallback`: the value came from the EU row) are written only when given, so a
    record from the EU pack is what it always was. `approved_by`, `approved_at` and
    `replaces` (a value the user applied from a Library reference, and the source it
    replaced) are likewise kept only when given."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    entry = {
        "status": status,
        "source": source,
        "source_year": source_year,
        "currency_year": currency_year,
        "range": None if range is None else [float(range[0]), float(range[1])],
        "illustrative": bool(illustrative),
        **({"currency": currency} if currency else {}),
        **({"fallback": fallback} if fallback else {}),
    }
    for key, value in (("approved_by", approved_by), ("approved_at", approved_at), ("replaces", replaces)):
        if value is not None:
            entry[key] = value
    _root(n).setdefault(component_class, {}).setdefault(name, {})[field] = entry


def annotate(n, component_class: str, name: str, field: str, **extra: Any) -> None:
    """Add keys to a field's recorded entry (the typed cost units of a pack row: `capacity_basis`,
    `includes_grid_connection`, `connection_fee_review`). Nothing is recorded for a field that
    `record` has not written; `record` itself replaces the entry, extras included."""
    entry = _root(n).get(component_class, {}).get(name, {}).get(field)
    if entry is not None:
        entry.update(extra)


_DROPPED_ON_EDIT = ("approved_by", "approved_at", "scaled_by", "dc_ac_ratio")

#: The money fields a scaled price writes (pricing: overnight × scale, FOM = overnight × share).
_SCALED_FIELDS = ("overnight_cost", "fom_cost")
_NOT_RECORDED = {"field": None, "recorded": None, "current_field": None, "current": None,
                 "reason": "scale input not recorded"}


def _recorded_input(entry: dict) -> dict | None:
    """The scale input an entry was priced with: `scaled_by`, or TC-02's `dc_ac_ratio` key."""
    if isinstance(entry.get("scaled_by"), dict):
        return entry["scaled_by"]
    if entry.get("dc_ac_ratio") is not None:
        # TC-02 wrote the key on a DC-priced (`output_electric_dc`) overnight cost only.
        return {"column": "dc_ac_ratio", "value": entry["dc_ac_ratio"], "technology": None,
                "capacity_basis": "output_electric_dc"}
    return None


def _verdict(n, component_class: str, name: str, scaled: dict) -> dict | None:
    from services.library.defaults_pack.pricing import current_scale_input

    try:
        column, value = current_scale_input(n, component_class, name, scaled.get("technology"),
                                            scaled["capacity_basis"])
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        return {"field": scaled["column"], "recorded": scaled["value"], "current_field": None,
                "current": None, "reason": f"cannot be priced now: {exc}"}
    if column == scaled["column"] and math.isclose(float(value), float(scaled["value"]), rel_tol=1e-9):
        return None
    return {"field": scaled["column"], "recorded": scaled["value"], "current_field": column,
            "current": value, "reason": "scale input changed"}


def _priced_by_scaling_rule(component_class: str, entry: dict) -> bool:
    """A pack price (status default, annotated with its row's capacity basis) whose rule scales."""
    from services.library.defaults_pack.pricing import _CLASS, SCALED_RULES, scale_rule

    component = next((c for c, cls in _CLASS.items() if cls == component_class), None)
    return entry.get("status") == "default" and \
        scale_rule(component, entry.get("capacity_basis")) in SCALED_RULES   # unknown: "refuse"


def staleness(n, component_class: str, name: str, entries: dict[str, dict]) -> dict[str, dict]:
    """`entries` (from `get`) with `stale` added to each money field whose price was scaled by a
    column of the asset (TCU-05: a PV Generator's `dc_ac_ratio`, a Link's output efficiency) that
    now differs, in column or value (math.isclose rel 1e-9), from what pricing would read; or that
    pricing would now refuse (`current` None). A scaled pack price that recorded no input reads
    `stale` with reason "scale input not recorded", never fresh. Pure: the store is not changed;
    a field with nothing to say is returned as it was."""
    out = dict(entries)
    over = entries.get("overnight_cost") or {}
    for field in _SCALED_FIELDS:
        entry = entries.get(field)
        if entry is None:
            continue
        scaled = _recorded_input(entry)
        basis = entry
        # fom_cost carries no annotation of its own before TCU-05: it shares the overnight
        # cost's when both are the same pack price.
        if scaled is None and field == "fom_cost" and entry.get("status") == over.get("status") \
                and entry.get("source") == over.get("source"):
            scaled, basis = _recorded_input(over), over
        if scaled is not None:
            verdict = _verdict(n, component_class, name, scaled)
        elif _priced_by_scaling_rule(component_class, basis):
            verdict = dict(_NOT_RECORDED)
        else:
            verdict = None
        if verdict is not None:
            out[field] = {**entry, "stale": verdict}
    return out


def mark_customised(n, component_class: str, name: str, fields: Iterable[str]) -> None:
    """A person changed these fields. The source they overrode stays, so the card can say what it replaced."""
    asset = _root(n).setdefault(component_class, {}).setdefault(name, {})
    for f in fields:
        entry = asset.get(f) or {"source": None, "source_year": None, "currency_year": None, "range": None}
        # A later edit is not the approval of an applied reference: that approval goes, and so
        # does the scale input it was priced with (a typed value has none, TCU-05).
        entry = {k: v for k, v in entry.items() if k not in _DROPPED_ON_EDIT}
        asset[f] = {**entry, "status": "customised", "illustrative": False}
    _prune(n, component_class, name)


def rename(n, component_class: str, old: str, new: str) -> None:
    cls = _root(n).get(component_class)
    if cls and old in cls:
        cls[new] = cls.pop(old)
    _prune(n, component_class, new)


def drop(n, component_class: str, name: str) -> None:
    cls = _root(n).get(component_class)
    if cls is not None:
        cls.pop(name, None)
    _prune(n, component_class, name)
