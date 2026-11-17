"""Load growth as a rate per year (homer-parity 04a, HOMER-GAPS-1).

A "load growth, % per year" per carrier derives the per-period load
multipliers, compounded from the growth base year:
``factor(P) = (1 + rate / 100) ** (P - base_year)``. The derived factors are
written into ``load_scalers_by_carrier``, the one map every reader already
uses (the LP's load scaling, the adequacy engines, finance, compare and the
FCA rebind through ``services.adequacy.demand``), so no reader changes.

``load_scalers_derived`` records which (carrier, period) entries came from the
rate. Every other entry was typed and wins over the rate (GK B ruling R4); a
changed rate re-derives only the derived entries, and a removed rate drops
them. A factor of exactly 1.0 is not written: an absent key already means
1.0, so the LP input and every digest of the config stay as they were.

The rate is finite and above -100 % (a fall of 100 % or more leaves no load
at all), and the base year is required and not after the first investment
period (R5).
"""
from __future__ import annotations

import math

# Six decimals: the multiplier the user reads beside the rate, and a stored
# value that does not move with floating-point noise between two derivations.
_DECIMALS = 6


def load_growth_problems(rates: dict, base_year: int | None, periods: list) -> list[str]:
    """Each reason the growth inputs are refused, in plain words; empty when
    they are valid. No rate: always valid."""
    problems: list[str] = []
    for carrier, rate in (rates or {}).items():
        try:
            value = float(rate)
        except (TypeError, ValueError):
            problems.append(f"load growth for {carrier}: a number in % per year, got {rate!r}")
            continue
        if not math.isfinite(value):
            problems.append(f"load growth for {carrier}: a finite number in % per year, "
                            f"got {rate!r}")
        elif value <= -100.0:
            problems.append(f"load growth for {carrier}: above -100 % per year (a fall of "
                            f"100 % or more leaves no load), got {value:g} %")
    if rates and base_year is None:
        problems.append("load growth: the year growth starts from is required with a rate")
    elif rates and periods and int(base_year) > min(int(p) for p in periods):
        problems.append(f"load growth: the year growth starts from ({base_year}) must not be "
                        f"after the first investment period ({min(int(p) for p in periods)})")
    return problems


def derive_load_scalers(periods: list, base_year: int | None, rates: dict,
                        scalers_by_carrier: dict, derived: dict,
                        legacy: dict | None = None) -> tuple[dict, dict]:
    """``(load_scalers_by_carrier, load_scalers_derived)`` after applying the
    rates. Typed entries (present and not marked derived) are kept, and so is
    a period the legacy all-carrier ``load_scalers`` types (no carrier's rate
    overrides it: a typed multiplier wins over the rate); every
    derived entry is recomputed from the current rate, or dropped when its
    carrier has no rate any more. The inputs are not mutated; the inputs are
    assumed valid (``load_growth_problems``)."""
    out: dict[str, dict[str, float]] = {}
    out_derived: dict[str, list[str]] = {}
    carriers = set(scalers_by_carrier or {}) | set(rates or {})
    for carrier in sorted(carriers):
        was_derived = set((derived or {}).get(carrier, ()))
        typed = {p: v for p, v in (scalers_by_carrier or {}).get(carrier, {}).items()
                 if p not in was_derived}
        block = dict(typed)
        marks: list[str] = []
        rate = (rates or {}).get(carrier)
        if rate is not None and base_year is not None:
            growth = 1.0 + float(rate) / 100.0
            for period in sorted(int(p) for p in periods or ()):
                key = str(period)
                if key in typed or key in (legacy or {}):
                    continue
                factor = round(growth ** (period - int(base_year)), _DECIMALS)
                if factor == 1.0:
                    continue
                block[key] = factor
                marks.append(key)
        if block or carrier in (scalers_by_carrier or {}):
            out[carrier] = block
        if marks:
            out_derived[carrier] = marks
    return out, out_derived
