"""Hard ranges of catalogue entries, and the one validator every write path runs.

Spec §3.2 and expert-catalogue issue 01, conditions C8–C12 and GK B2's
R1–R9 for 01a-3:

- A hard bound sits only where PyPSA or the LP is undefined for the value.
  Each row in ``HARD`` names its source. Anything else is a soft bound or a
  flag, never a refusal, and that includes min ≤ max pairs (R2).
- NaN is refused on every numeric entry with a numeric engine default; where
  the engine has none, NaN is the stored "not set" and stays writable
  (``nan_is_unset``, K1d; the list is pinned in the tests). A Link's
  ``efficiency{k}`` takes NaN only while its ``bus{k}`` is blank. ±inf is accepted only where the engine's own
  default is that infinity (``INF_OK``, C11).
- A bound is in the stored model unit, so a field on a tonne bus is checked
  in t or t/h (C10). The hard rows are sign and zero bounds, which hold in
  any unit.
- Only what a write changes is checked (C8). A value equal to the stored one,
  after the path's own coercion, passes even when it is out of range, so an
  asset stored out of range can still be edited (R4).
- Restores, undo, project open and template apply never call this (R5).
  Imports load and flag (01a-3b).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

ERROR_KIND = "value_out_of_range"


@dataclass(frozen=True)
class Range:
    min: float | None = None
    max: float | None = None
    min_open: bool = False
    max_open: bool = False
    soft_min: float | None = None
    soft_max: float | None = None
    source: str = ""


_PYPSA = "pypsa 1.1.2"

#: (component class, column) → hard range. Each row cites where the engine
#: is undefined outside it (R1).
HARD: dict[tuple[str, str], Range] = {
    ("Bus", "v_nom"): Range(
        min=0.0,
        min_open=True,
        source=f"{_PYPSA} network/power_flow.py:748: x_pu = x / v_nom**2; "
        "a nominal voltage is a magnitude",
    ),
    ("StorageUnit", "efficiency_dispatch"): Range(
        min=0.0,
        min_open=True,
        source=f"{_PYPSA} optimization/constraints.py:1541: the state-of-charge "
        "balance divides the dispatch by efficiency_dispatch",
    ),
    ("StorageUnit", "standing_loss"): Range(
        max=1.0,
        source=f"{_PYPSA} optimization/constraints.py:1530: (1 - standing_loss) "
        "** elapsed_hours is undefined below 0 for fractional hours",
    ),
    ("Store", "standing_loss"): Range(
        max=1.0,
        source=f"{_PYPSA} optimization/constraints.py:1726: (1 - standing_loss) "
        "** elapsed_hours is undefined below 0 for fractional hours",
    ),
}

#: (component class, column) → the infinity its engine default is (C11).
#: Pinned in the tests against ``catalogue._engine_defaults``.
INF_OK: dict[tuple[str, str], float] = {
    ("Generator", "e_sum_max"): math.inf,
    ("Generator", "e_sum_min"): -math.inf,
    ("Generator", "lifetime"): math.inf,
    ("Generator", "p_nom_max"): math.inf,
    ("Line", "lifetime"): math.inf,
    ("Line", "s_nom_max"): math.inf,
    ("Link", "lifetime"): math.inf,
    ("Link", "p_nom_max"): math.inf,
    ("StorageUnit", "lifetime"): math.inf,
    ("StorageUnit", "p_nom_max"): math.inf,
    ("Store", "e_nom_max"): math.inf,
    ("Store", "lifetime"): math.inf,
    ("Transformer", "lifetime"): math.inf,
    ("Transformer", "s_nom_max"): math.inf,
}

NUMERIC_KINDS = frozenset({"number", "int"})


@lru_cache(maxsize=1)
def nan_is_unset() -> frozenset[tuple[str, str]]:
    """The numeric entries whose engine default is NaN or none at all, where a
    NaN is the stored "not set" (K1d: a blank discount rate). NaN is accepted
    there and refused everywhere else. The extra-port efficiencies are in the
    set, but ``problem`` accepts their NaN only on an unconnected port."""
    from services.asset_schema import catalogue

    defaults = catalogue._engine_defaults()
    return frozenset(
        k
        for k in _numeric_entries()
        if k not in defaults or _is_extra_port_efficiency(k)
    )


def _is_extra_port_efficiency(key: tuple[str, str]) -> bool:
    """A Link's ``efficiency2``…``k``: NaN marks an unused port, since PyPSA
    reads ``efficiency{k}`` only where ``bus{k}`` is set (R1: these get no
    bound)."""
    cls, col = key
    return (
        cls == "Link"
        and col.startswith("efficiency")
        and col[len("efficiency") :].isdigit()
    )


def _is_set(bus: Any) -> bool:
    """A port's bus is connected when it names a bus (PyPSA reads
    ``efficiency{k}`` only then)."""
    return isinstance(bus, str) and bus.strip() != ""


@lru_cache(maxsize=1)
def _numeric_entries() -> dict[tuple[str, str], str]:
    """(class, column) → plain name, for every numeric component entry."""
    from services.asset_schema import catalogue

    return {
        (e.scope, e.spec.key): e.plain_name
        for e in catalogue.entries()
        if e.spec.kind in NUMERIC_KINDS
    }


def wire(scope: str, key: str) -> dict[str, Any] | None:
    """The entry's ``range`` on the catalogue wire, or None when it has none."""
    r = HARD.get((scope, key))
    if r is None:
        return None
    return {
        "min": r.min,
        "max": r.max,
        "min_open": r.min_open,
        "max_open": r.max_open,
        "soft_min": r.soft_min,
        "soft_max": r.soft_max,
    }


def show(value: float) -> str:
    """The value as given: never rounded, so -0.0001 never reads as -0 (R8)."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "infinity" if value > 0 else "minus infinity"
    return repr(value)


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def problem(
    component_class: str, key: str, value: Any, row: Mapping[str, Any] | None = None
) -> str | None:
    """The refusal text for one value, or None when it is in range or not a
    numeric catalogue entry.

    ``row`` is the component as it stands after the write. A NaN on a Link's
    ``efficiency{k}`` is an unused port only when ``row`` is known and its
    ``bus{k}`` is blank; with no row (a bulk write) it is refused."""
    plain = _numeric_entries().get((component_class, key))
    if plain is None:
        return None
    v = _as_number(value)
    if v is None:
        return None
    if math.isnan(v):
        if _is_extra_port_efficiency((component_class, key)):
            port = "bus" + key[len("efficiency") :]
            if row is not None and not _is_set(row.get(port)):
                return None
            return f"{plain} must be a number while {port} is connected; got NaN."
        if (component_class, key) in nan_is_unset():
            return None
        return f"{plain} must be a number; got NaN."
    if math.isinf(v):
        if INF_OK.get((component_class, key)) == v:
            return None
        return f"{plain} must be a finite number here; got {show(v)}."
    r = HARD.get((component_class, key))
    if r is None:
        return None
    if r.min is not None and (v <= r.min if r.min_open else v < r.min):
        word = "more than" if r.min_open else "at least"
        return f"{plain} must be {word} {r.min:g}; got {show(v)}."
    if r.max is not None and (v >= r.max if r.max_open else v > r.max):
        word = "less than" if r.max_open else "at most"
        return f"{plain} must be {word} {r.max:g}; got {show(v)}."
    return None


def same_value(new: Any, old: Any) -> bool:
    """True when ``new`` is the stored ``old`` after coercion: float noise and
    a number sent as a string count as the same value (R4)."""
    a, b = _as_number(new), _as_number(old)
    if a is None or b is None:
        return False
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def refuse(component_class: str, key: str, value: Any, message: str) -> HTTPException:
    v = _as_number(value)
    return HTTPException(
        422,
        detail={
            "error_kind": "value_out_of_range",  # == ERROR_KIND; the kind manifest reads the literal
            "component": component_class,
            "field": key,
            "value": show(v) if v is not None else str(value),
            "message": message,
        },
    )


def check(
    component_class: str,
    changes: Mapping[str, Any],
    stored: Mapping[str, Any] | None = None,
) -> None:
    """Refuse (422 ``value_out_of_range``) the first changed value out of range.

    ``stored`` is the row before the write. A value equal to its stored value
    is no change and is never refused (C8, R4). A create passes no ``stored``."""
    row = {**(stored or {}), **changes}
    for key in sorted(changes):
        value = changes[key]
        if stored is not None and key in stored and same_value(value, stored[key]):
            continue
        message = problem(component_class, key, value, row)
        if message is not None:
            raise refuse(component_class, key, value, message)


def check_column(
    component_class: str, key: str, value: Any, stored_values: Iterable[Any]
) -> None:
    """The bulk form: one value written to many rows. It is refused unless
    every target already stores it. No stored value at all (no target, or
    the column is absent) is a change, so it is refused."""
    message = problem(component_class, key, value)
    if message is None:
        return
    stored = list(stored_values)
    if stored and all(same_value(value, old) for old in stored):
        return
    raise refuse(component_class, key, value, message)


#: The classes whose rows the sweep reads, by network attribute.
_LISTS = {
    "Bus": "buses",
    "Generator": "generators",
    "Load": "loads",
    "Line": "lines",
    "Link": "links",
    "Store": "stores",
    "StorageUnit": "storage_units",
    "Transformer": "transformers",
    "ShuntImpedance": "shunt_impedances",
}


def problems(n) -> list[dict[str, Any]]:
    """Every stored value of ``n`` that a new write would refuse: what an
    import flags (01a-3b) and what the shipped-network sweep pins empty (R3).
    Reads only; it never changes ``n``."""
    out: list[dict[str, Any]] = []
    numeric = _numeric_entries()
    for cls, attr in _LISTS.items():
        frame = getattr(n, attr)
        cols = [k for (c, k) in numeric if c == cls and k in frame.columns]
        for key in sorted(cols):
            port = _is_extra_port_efficiency((cls, key))
            for name, value in frame[key].items():
                message = problem(cls, key, value, frame.loc[name] if port else None)
                if message is not None:
                    v = _as_number(value)
                    out.append(
                        {
                            "component": cls,
                            "id": str(name),
                            "field": key,
                            "value": show(v) if v is not None else str(value),
                            "message": message,
                        }
                    )
    return out


#: The min ≤ max pairs the import flags check, by class (gatekeeper R2: they
#: are flags only and never refused). A pair is checked only when both
#: columns are in the frame.
_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "Generator": (
        ("p_nom_min", "p_nom_max"),
        ("p_min_pu", "p_max_pu"),
        ("sk_min_mva", "sk_max_mva"),
        ("rx_min", "rx_max"),
    ),
    "Link": (("p_nom_min", "p_nom_max"), ("p_min_pu", "p_max_pu")),
    "StorageUnit": (("p_nom_min", "p_nom_max"),),
    "Line": (("s_nom_min", "s_nom_max"),),
    "Transformer": (("s_nom_min", "s_nom_max"),),
    "Store": (("e_nom_min", "e_nom_max"), ("e_min_pu", "e_max_pu")),
}


def flags(n) -> list[dict[str, Any]]:
    """What an import tells the user about ``n`` (C9: the values still load
    unchanged): every :func:`problems` row as ``kind: "out_of_range"``, then
    every row whose min is above its max as ``kind: "min_above_max"``.
    A pair flags only finite against finite: NaN, unset or infinity on
    either side never flags. Static columns only; the ``*_t`` series of
    ``p_min_pu``/``p_max_pu``/``e_min_pu``/``e_max_pu`` are not read.
    Reads only; it never changes ``n``. Never persisted (R10)."""
    out: list[dict[str, Any]] = [{**row, "kind": "out_of_range"} for row in problems(n)]
    numeric = _numeric_entries()
    for cls, pairs in _PAIRS.items():
        frame = getattr(n, _LISTS[cls])
        for lo, hi in pairs:
            if lo not in frame.columns or hi not in frame.columns:
                continue
            lo_name = numeric.get((cls, lo), lo)
            hi_name = numeric.get((cls, hi), hi)
            for name, a, b in zip(frame.index, frame[lo], frame[hi]):
                x, y = _as_number(a), _as_number(b)
                if x is None or y is None:
                    continue
                if not (math.isfinite(x) and math.isfinite(y)):
                    continue
                if x > y:
                    out.append(
                        {
                            "component": cls,
                            "id": str(name),
                            "field": lo,
                            "value": show(x),
                            "message": (
                                f"{lo_name} ({show(x)}) is above {hi_name} ({show(y)})."
                            ),
                            "kind": "min_above_max",
                        }
                    )
    return out


#: The most flag rows one import response carries; the total says how many.
FLAG_CAP = 200


def import_flags(n) -> dict[str, Any]:
    """The two keys every import response spreads: ``range_flags``, the first
    :data:`FLAG_CAP` rows of :func:`flags`, and ``range_flags_total``, how many
    there are in all. ``range_flags_total`` is None when the sweep itself
    failed (logged): the import then succeeds or fails exactly as it would
    without flags. Plain JSON (strings and numbers); never persisted (R10)."""
    try:
        rows = flags(n)
    except Exception:
        logger.exception("range flags sweep failed; the import goes on unflagged")
        return {"range_flags": [], "range_flags_total": None}
    return {"range_flags": rows[:FLAG_CAP], "range_flags_total": len(rows)}
