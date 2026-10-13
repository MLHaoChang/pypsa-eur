"""
Energy Hub custom network columns (P14): coercion, typed creation and dtype
normalisation for the ``eh_*`` tags in ``models.energy_hub.EH_CUSTOM_COLUMNS``.

They are NOT PyPSA attributes, so three boundaries need help:

* the D21 CRUD whitelist would drop a first write (column not yet present);
* ``/_bulk`` refuses columns the frame lacks;
* a bool column that picked up ``None``/``NaN`` becomes ``object``, the one
  shape netCDF refuses — so every network-replacing path normalises.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from models.energy_hub import EH_CUSTOM_COLUMNS, EH_INTERNAL_ROLES, EH_LINK_ROLES

_ATTR_OF = {"Bus": "buses", "Link": "links"}
_TRUE = ("true", "1", "yes", "y", "t")
_FALSE = ("false", "0", "no", "n", "f", "")


def eh_columns_for(component_class: str) -> dict[str, str]:
    return EH_CUSTOM_COLUMNS.get(component_class, {})


def _as_bool(value) -> bool:
    if value is None or (isinstance(value, (float, np.floating))
                         and math.isnan(value)):
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in _TRUE + _FALSE:
        return value.strip().lower() in _TRUE
    raise ValueError(f"expected true/false, got {value!r}")


def coerce_eh_value(component_class: str, col: str, value):
    """The stored value for one API-written cell; ValueError names the rule.

    Study-internal roles (``EH_INTERNAL_ROLES``) are refused here: a user
    tag of ``eh_n1_conversion`` would silently change what a redundancy
    scenario counts. The normaliser still keeps them on load.
    """
    kind = eh_columns_for(component_class)[col]
    if kind == "bool":
        try:
            return _as_bool(value)
        except ValueError as exc:
            raise ValueError(f"{col}: {exc}") from exc
    if kind == "float_nonneg":
        if value is None or value == "":
            return float("nan")
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{col}: expected a number, got {value!r}") from None
        if not math.isfinite(v) or v < 0:
            raise ValueError(f"{col}: must be a finite number >= 0, got {v!r}")
        return v
    if kind == "role":
        v = "" if value is None else str(value).strip()
        if v in EH_INTERNAL_ROLES:
            raise ValueError(
                f"{col}: {v!r} is set by the redundancy study, not by hand")
        if v not in EH_LINK_ROLES:
            raise ValueError(
                f"{col}: {v!r} is not an Energy Hub role; expected one of "
                f"{[r for r in EH_LINK_ROLES if r and r not in EH_INTERNAL_ROLES]}"
                " (or empty)")
        return v
    raise ValueError(f"{col}: unknown kind {kind!r}")  # pragma: no cover


def _default(kind: str):
    return {"bool": False, "float_nonneg": float("nan"), "role": ""}[kind]


def ensure_eh_column(n, component_class: str, col: str) -> None:
    """Create a whitelisted column with its typed default if absent."""
    attr = _ATTR_OF.get(component_class)
    df = getattr(n, attr, None) if attr else None
    if df is None or col in df.columns:
        return
    kind = eh_columns_for(component_class)[col]
    df[col] = pd.Series(_default(kind), index=df.index,
                        dtype=bool if kind == "bool" else
                        float if kind == "float_nonneg" else object)


def normalise_eh_columns(n) -> None:
    """Typed dtypes for every PRESENT whitelisted column (never creates one).

    Idempotent; unparseable cells fall back to the column default rather than
    raising — a save must not fail on a stale value.
    """
    for component_class, cols in EH_CUSTOM_COLUMNS.items():
        df = getattr(n, _ATTR_OF[component_class], None)
        if df is None:
            continue
        for col, kind in cols.items():
            if col not in df.columns:
                continue
            try:
                if kind == "bool":
                    def _b(v):
                        try:
                            return _as_bool(v)
                        except ValueError:
                            return False
                    df[col] = df[col].map(_b).astype(bool)
                elif kind == "float_nonneg":
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
                else:
                    df[col] = df[col].map(
                        lambda v: "" if v is None or (isinstance(v, float)
                                                      and math.isnan(v))
                        else str(v)).astype(object)
            except Exception:                                 # noqa: BLE001
                pass
