"""Load growth as a rate per year (homer-parity 04a, HOMER-GAPS-1).

A "load growth, % per year" per carrier derives the per-period load
multipliers, compounded from the growth start year:
``factor(P) = (1 + rate / 100) ** (P - base_year)``. The derived factors are
written into ``load_scalers_by_carrier``, the one map every reader already
uses (the LP's load scaling, the adequacy engines, finance, compare and the
FCA rebind through ``services.adequacy.demand``), so no reader changes.

The periods are the LP's own (``effective_periods``): the config's
``investment_periods`` override when set, else the network's. Every writer of
either re-derives here: the solver-config PUT and the network time-axis routes
(``refresh``); the solve's pre-flight check refuses a stored map that a fresh
derivation would change (``stale_problem``), for any writer that does not.

``load_scalers_derived`` records which (carrier, period) entries came from the
rate. Which entry is typed is decided here, from the STORED state, never from
a client flag (GK B C2): a submitted value equal to the stored derived value
stays derived, a different one becomes typed, and a key the submission drops
is derived again. A typed entry, and a period the legacy all-carrier
``load_scalers`` types, wins over the rate (R4). Every derived entry is
recomputed on each derivation, so none outlives its rate, its period or its
start year; a factor of exactly 1.0 is not written (an absent key already
means 1.0, so the LP input and every digest of the config stay as they were).

The rate is finite and above -100 %, the start year is a whole year, required
with a rate and not after the first period (R5), and every derived factor
must come out finite and above 0, as a multiplier must.
"""

from __future__ import annotations

import math

# Six decimals: the multiplier the user reads beside the rate, and a stored
# value that does not move with floating-point noise between two derivations.
_DECIMALS = 6


class LoadGrowthError(ValueError):
    """Growth inputs refused; ``detail`` is the 422 body (code, tool error
    kind, plain-words message)."""

    def __init__(self, detail: dict):
        super().__init__(detail["message"])
        self.detail = detail
        self.kind = detail["error_kind"]


def canonical_rates(rates) -> dict:
    """The rate map keyed by the canonical load carrier (``'AC'`` →
    ``'electrical'``), the key `load_scalers_by_carrier` is read by."""
    from services.solver.assumptions import _canonical_load_carrier_key

    return {_canonical_load_carrier_key(c): r for c, r in (rates or {}).items()}


def effective_periods(cfg, n) -> list[int]:
    """The periods the LP solves: the config's ``investment_periods`` override
    when multi-period and set (``assumptions`` step 4), else the network's."""
    if getattr(cfg, "multi_investment_periods", False) and getattr(
        cfg, "investment_periods", None
    ):
        return sorted(int(p) for p in cfg.investment_periods)
    periods = getattr(n, "investment_periods", None) if n is not None else None
    return (
        sorted(int(p) for p in periods) if periods is not None and len(periods) else []
    )


def load_growth_problems(rates: dict, base_year, periods: list) -> list[str]:
    """Each reason the growth inputs are refused, in plain words; empty when
    they are valid. No rate: always valid."""
    problems: list[str] = []
    for carrier, rate in (rates or {}).items():
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            problems.append(f"Load growth ({carrier}): must be a number in % per year")
        elif not math.isfinite(rate):
            problems.append(
                f"Load growth ({carrier}): must be a finite number in % per year"
            )
        elif rate <= -100.0:
            problems.append(
                f"Load growth ({carrier}): must be above -100 % per year "
                f"(a fall of 100 % or more leaves no load)"
            )
    if not rates:
        return problems
    if base_year is None:
        problems.append("Growth from year: required with a load growth rate")
    elif isinstance(base_year, bool) or not isinstance(base_year, int):
        problems.append("Growth from year: must be a whole year")
    elif periods and base_year > min(periods):
        problems.append(
            f"Growth from year: must not be after the first period ({min(periods)})"
        )
    return problems


def derived_marks(raw, scalers_by_carrier) -> dict[str, set[str]]:
    """The one reader of ``load_scalers_derived`` (GK B C3): carrier → the
    period keys whose stored value came from the rate. A malformed mark (not a
    dict of lists of period strings, or a mark on a key the stored map does not
    hold) reads as "not derived", so the stored value is kept as typed and
    nothing is overwritten. A well-formed mark on a period the period set no
    longer holds stays a mark, so the derivation drops that entry (GK B (a))."""
    out: dict[str, set[str]] = {}
    if not isinstance(raw, dict) or not isinstance(scalers_by_carrier, dict):
        return out
    for carrier, marks in raw.items():
        block = scalers_by_carrier.get(carrier)
        if not isinstance(marks, list) or not isinstance(block, dict):
            continue
        keep = {p for p in marks if isinstance(p, str) and p.isdigit() and p in block}
        if keep:
            out[carrier] = keep
    return out


def _factor(rate: float, years: int, carrier: str, period: int) -> float:
    try:
        factor = round((1.0 + rate / 100.0) ** years, _DECIMALS)
    except OverflowError:
        factor = math.inf
    if not math.isfinite(factor) or factor <= 0.0:
        raise LoadGrowthError(
            {
                "code": "load_growth_factor_invalid",
                "error_kind": "load_growth_factor_invalid",
                "message": f"Load growth ({carrier}): {rate:g} % per year gives a load multiplier of "
                f"{factor:g} in {period}; a multiplier must be a finite number above 0. "
                f"Use a smaller rate.",
            }
        )
    return factor


def derive_load_scalers(
    periods: list,
    base_year: int | None,
    rates: dict,
    submitted,
    stored,
    stored_derived,
    legacy: dict | None = None,
) -> tuple[dict, dict]:
    """``(load_scalers_by_carrier, load_scalers_derived)`` after applying the
    rates. ``submitted`` is the map as this change leaves it (the stored one
    when the change does not touch it); ``stored`` and ``stored_derived`` are
    the map and marks as stored before it. Inputs are not mutated and are
    assumed valid (``load_growth_problems``); a derived factor that is not
    finite and above 0 raises ``LoadGrowthError``."""
    submitted = submitted if isinstance(submitted, dict) else {}
    stored = stored if isinstance(stored, dict) else {}
    marks = derived_marks(stored_derived, stored)
    out: dict[str, dict] = {}
    out_derived: dict[str, list[str]] = {}
    for carrier in sorted(set(submitted) | set(rates or {})):
        block_in = (
            submitted.get(carrier) if isinstance(submitted.get(carrier), dict) else {}
        )
        stored_block = (
            stored.get(carrier) if isinstance(stored.get(carrier), dict) else {}
        )
        was = marks.get(carrier, set())
        typed = {
            p: v
            for p, v in block_in.items()
            if not (p in was and v == stored_block.get(p))
        }
        block = dict(typed)
        new_marks: list[str] = []
        rate = (rates or {}).get(carrier)
        if rate is not None and base_year is not None:
            for period in sorted(periods or ()):
                key = str(period)
                if key in typed or key in (legacy or {}):
                    continue
                factor = _factor(float(rate), period - int(base_year), carrier, period)
                if factor == 1.0:
                    continue
                block[key] = factor
                new_marks.append(key)
        if block or carrier in submitted:
            out[carrier] = block
        if new_marks:
            out_derived[carrier] = new_marks
    return out, out_derived


def apply(after: dict, stored_cfg, periods: list) -> dict:
    """The growth keys as a change leaves them: ``after`` holds the config's
    ``load_scalers``, ``load_scalers_by_carrier``, ``load_growth_pct_per_year``
    and ``load_growth_base_year`` after the change; ``stored_cfg`` is the
    config before it. Returns the keys to write, ``{}`` when no rate is stored
    or set (nothing is read or written, so the config stays as it is).
    Raises ``LoadGrowthError``."""
    rates = canonical_rates(after.get("load_growth_pct_per_year"))
    if not rates and not getattr(stored_cfg, "load_growth_pct_per_year", None):
        return {}
    base_year = after.get("load_growth_base_year")
    problems = load_growth_problems(rates, base_year, periods)
    if problems:
        raise LoadGrowthError(
            {
                "code": "load_growth_invalid",
                "error_kind": "load_growth_invalid",
                "message": "; ".join(problems),
            }
        )
    scalers, derived = derive_load_scalers(
        periods,
        base_year,
        rates,
        after.get("load_scalers_by_carrier") or {},
        getattr(stored_cfg, "load_scalers_by_carrier", None) or {},
        getattr(stored_cfg, "load_scalers_derived", None),
        after.get("load_scalers") or {},
    )
    return {
        "load_growth_pct_per_year": rates,
        "load_scalers_by_carrier": scalers,
        "load_scalers_derived": derived,
    }


def _stored_after(cfg) -> dict:
    return {
        k: getattr(cfg, k, None)
        for k in (
            "load_scalers",
            "load_scalers_by_carrier",
            "load_growth_pct_per_year",
            "load_growth_base_year",
        )
    }


def refresh(cfg, periods: list) -> dict:
    """The growth keys re-derived for a new period set, the stored inputs
    unchanged (a network time-axis change). ``{}`` without a rate. Raises
    ``LoadGrowthError`` (e.g. the start year after the new first period)."""
    return apply(_stored_after(cfg), cfg, periods)


def stale_problem(cfg, n) -> str | None:
    """A plain-words reason the stored multipliers are not what the rate gives
    for the periods the LP will solve, or ``None``: the pre-flight net for a
    writer that did not re-derive."""
    if not getattr(cfg, "load_growth_pct_per_year", None):
        return None
    periods = effective_periods(cfg, n)
    try:
        fresh = refresh(cfg, periods)
    except LoadGrowthError as exc:
        return str(exc)
    if fresh["load_scalers_by_carrier"] != (cfg.load_scalers_by_carrier or {}):
        return (
            "Load growth: the period load multipliers are out of date for the periods "
            f"{periods}; open Model Horizon's period economics and set the rate again"
        )
    return None


def refreshed_config(cfg, network_periods: list):
    """``cfg`` with its growth keys re-derived for the network's new periods
    (the config's own ``investment_periods`` override still wins), or ``cfg``
    itself when nothing changes or no rate is stored. Raises
    ``LoadGrowthError``."""
    if cfg is None or not getattr(cfg, "load_growth_pct_per_year", None):
        return cfg
    from dataclasses import replace
    from types import SimpleNamespace

    periods = effective_periods(
        cfg, SimpleNamespace(investment_periods=network_periods)
    )
    fresh = refresh(cfg, periods)
    if all(getattr(cfg, k, None) == v for k, v in fresh.items()):
        return cfg
    return replace(cfg, **fresh)


def follow_network(ctx) -> None:
    """Re-derive the live session's stored multipliers for its network's
    current periods (homer-parity 04a), for a writer that replaces the network
    but keeps the solver config: undo, a network import. A refusal leaves the
    config as it is; the solve's pre-flight check (``stale_problem``) names it."""
    cfg = ctx.solver_state.get("solver_config")
    periods = getattr(ctx.network, "investment_periods", None)
    try:
        grown = refreshed_config(
            cfg, [int(p) for p in periods] if periods is not None else []
        )
    except LoadGrowthError:
        return
    if grown is not cfg:
        ctx.solver_state["solver_config"] = grown
