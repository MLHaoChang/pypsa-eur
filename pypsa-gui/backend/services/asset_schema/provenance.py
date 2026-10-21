"""
Per-field provenance of asset parameters (uniform asset parameterisation, rule 7).

Stored in `n.meta["asset_params"][class][name][field]`, so it travels with the network through netCDF, undo and
project saves. This module is the only writer: the Expert card and the chat tool stamp a changed field
`customised` (through `network_crud._update_component`), a pack or template stamps what it wrote with `record`,
and the guided ledger (S2) reads `get` and projects it onto its rows. It never keeps a store of its own.

An absent entry means "not recorded", which is not the same as `default` (ADR-0001: unknown is not a value).
"""
from __future__ import annotations

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
           currency: str | None = None, fallback: str | None = None) -> None:
    """Write one field's provenance, replacing what was there. `currency` (a regional pack's
    ISO code) and `fallback` (`eu_fallback`: the value came from the EU row) are written only
    when given, so a record from the EU pack is what it always was."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    _root(n).setdefault(component_class, {}).setdefault(name, {})[field] = {
        "status": status,
        "source": source,
        "source_year": source_year,
        "currency_year": currency_year,
        "range": None if range is None else [float(range[0]), float(range[1])],
        "illustrative": bool(illustrative),
        **({"currency": currency} if currency else {}),
        **({"fallback": fallback} if fallback else {}),
    }


def mark_customised(n, component_class: str, name: str, fields: Iterable[str]) -> None:
    """A person changed these fields. The source they overrode stays, so the card can say what it replaced."""
    asset = _root(n).setdefault(component_class, {}).setdefault(name, {})
    for f in fields:
        entry = asset.get(f) or {"source": None, "source_year": None, "currency_year": None, "range": None}
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
