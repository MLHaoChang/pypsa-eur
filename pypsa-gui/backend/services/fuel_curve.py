"""
A generator's fuel curve (homer-parity 02a, HOMER-GAPS-1).

HOMER models a genset's fuel use as a no-load intercept plus a slope:

    fuel [MWh fuel/h] = no_load × p_nom × on + slope × p

``fuel_curve_no_load`` is MWh of fuel per hour per MW of rated capacity while the unit is on (HOMER's intercept
coefficient), ``fuel_curve_slope`` is MWh of fuel per MWh generated (the unit of the DC backup heat rate,
``models/flex_archetypes.py``), ``fuel_price`` is EUR per MWh of fuel. The curve reaches the solve through PyPSA's
own columns, so the objective and ``n.statistics`` opex carry it with no extra term:

* ``marginal_cost = slope × fuel_price`` (EUR/MWh);
* ``stand_by_cost = no_load × p_nom × fuel_price`` (EUR/h while committed). PyPSA weights it by the snapshot
  weighting in both the objective and the statistics; it needs ``committable`` and a fixed ``p_nom`` (a cost per
  hour cannot follow ``p_nom_opt``).

``derived_costs`` is the one derivation. The solve staging (``solver_service``) applies it to every generator and
is authoritative; the asset writers (``network_crud``, ``network_bulk``) apply it too so the form shows the values,
and every writer refuses what ``guard`` refuses. While a curve is set the two cost columns are the curve's: a
direct edit of either is refused, and setting a curve over a non-zero value says what it replaces
(``replace_notice``).

The DC backup (``services/adequacy/dc_backup.py``) prices its genset fuel off the LP (``backup_cost_delta``) and
never writes a generator's ``marginal_cost``, so the two never fight over one column. Stated precedence (GK B
ruling 3): the backup prices fuel from its own heat rate, says so in its cost notes when a genset carries a curve
("Fuel curve not used here"), and its NPV is never summed into the solve's opex.

A generator with none of the three set keeps its own costs: no curve, no change.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

SLOPE = "fuel_curve_slope"
NO_LOAD = "fuel_curve_no_load"
PRICE = "fuel_price"
COLUMNS = (SLOPE, NO_LOAD, PRICE)
#: The PyPSA columns the curve writes.
DERIVED = ("marginal_cost", "stand_by_cost")
#: Each column as the Generator form labels it (`services/asset_schema/fields.py`): a refusal opens with it.
LABELS = {
    SLOPE: "Fuel curve slope",
    NO_LOAD: "No-load fuel",
    PRICE: "Fuel price",
    "marginal_cost": "Marginal cost",
    "stand_by_cost": "Stand-by cost",
}


def _num(v: Any) -> float | None:
    """A finite float, or None for missing, blank, NaN or non-numeric."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _is_unset(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _flag(v: Any) -> bool:
    """A bool column's cell; unset (None, NaN) is False."""
    return False if _is_unset(v) else bool(v)


def curve_of(row: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """``(slope, no_load, price)`` with unset terms as 0, or None when the row sets no curve term."""
    vals = [_num(row.get(k)) for k in COLUMNS]
    if all(v is None for v in vals):
        return None
    slope, no_load, price = (v or 0.0 for v in vals)
    return slope, no_load, price


def check(row: Mapping[str, Any]) -> str | None:
    """Why the row's curve cannot be priced, or None when it can (or sets none)."""
    for k in COLUMNS:
        v = row.get(k)
        if not _is_unset(v) and _num(v) is None:
            return f"{LABELS[k]}: enter a finite number (got {v})"
    curve = curve_of(row)
    if curve is None:
        return None
    slope, no_load, _price = curve
    for k, v in zip(COLUMNS, curve):
        if v < 0:
            return f"{LABELS[k]}: enter zero or more (got {v:g})"
    if (slope > 0 or no_load > 0) and _num(row.get(PRICE)) is None:
        return "Fuel price: enter the price per MWh of fuel, which the fuel curve needs"
    if no_load > 0:
        if not _flag(row.get("committable")):
            return (
                "No-load fuel: turn on Unit commitment, or set it to 0. The fuel is burnt only while the "
                "unit is on"
            )
        if _flag(row.get("p_nom_extendable")):
            return (
                "No-load fuel: untick Extendable and set a fixed capacity, or set it to 0. Its cost per "
                "hour follows the rated capacity"
            )
    return None


def _same(a: Any, b: Any) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is None or fb is None:
        return _is_unset(a) and _is_unset(b)
    return math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-9)


def guard(
    after: Mapping[str, Any],
    *,
    before: Mapping[str, Any] | None = None,
    submitted: Mapping[str, Any] | None = None,
    cost_series: Iterable[str] = (),
) -> str | None:
    """Why a write that leaves the generator as ``after`` is refused, or None.

    ``before`` is the stored row (None on a create), ``submitted`` the fields the writer sent, ``cost_series`` the
    derived columns the generator also has a time series for.
    """
    if msg := check(after):
        return msg
    if curve_of(after) is None:
        return None
    if series := [c for c in DERIVED if c in set(cost_series)]:
        return (
            f"{LABELS[series[0]]}: remove its profile, or clear the fuel curve. The curve sets this cost, "
            "so the profile would be ignored"
        )
    if before is not None and curve_of(before) is not None:
        for col in DERIVED:
            v = (submitted or {}).get(col)
            # A blank is not an edit: the form may send a read-only row empty.
            if not _is_unset(v) and not _same(v, before.get(col)):
                return (
                    f"{LABELS[col]}: set by the fuel curve. Change the fuel price, slope or no-load fuel, "
                    "or clear the curve to enter it directly"
                )
    return None


def cost_series(n, name: str) -> list[str]:
    """The curve-derived cost columns generator ``name`` also has a time series for."""
    return [
        c
        for c in DERIVED
        if (df := getattr(n.generators_t, c, None)) is not None and name in df.columns
    ]


_UNITS = {"marginal_cost": "€/MWh", "stand_by_cost": "€/h"}


def replace_notice(
    after: Mapping[str, Any], *, before: Mapping[str, Any] | None = None
) -> str | None:
    """What a newly set curve replaces: the non-zero costs the row carries before the derivation, or None.

    ``after`` is the row as written (the stored values overlaid with what was sent), so a cost sent together with
    a new curve is the one named.
    """
    if curve_of(after) is None or (before is not None and curve_of(before) is not None):
        return None
    new = derived_costs(after)
    replaced = [
        f"the {LABELS[col].lower()} of {_num(after.get(col)):g} {_UNITS[col]} with {new[col]:g} "
        f"{_UNITS[col]}"
        for col in DERIVED
        if (_num(after.get(col)) or 0.0) != 0.0 and not _same(after.get(col), new[col])
    ]
    return f"The fuel curve replaces {' and '.join(replaced)}." if replaced else None


def cleared(
    after: Mapping[str, Any], *, before: Mapping[str, Any] | None
) -> dict[str, float]:
    """The costs a write that clears a curve resets: the stand-by cost, which the form cannot edit and which
    would otherwise stay a charge per on-hour with no curve behind it. Marginal cost stays (it is editable)."""
    if before is None or curve_of(before) is None or curve_of(after) is not None:
        return {}
    return {"stand_by_cost": 0.0}


def clear_notice(
    after: Mapping[str, Any], *, before: Mapping[str, Any] | None
) -> str | None:
    """What a write that clears a curve did to its costs (`cleared`), or None when it clears none.

    ``after`` is the row as written; the marginal cost it keeps is the one it carries (derived, or sent)."""
    if not cleared(after, before=before):
        return None
    mc = _num(after.get("marginal_cost"))
    return (
        "Fuel curve cleared: stand-by cost reset to 0 €/h; marginal cost keeps "
        f"{mc or 0.0:g} €/MWh (was derived from the curve)."
    )


def derived_costs(row: Mapping[str, Any]) -> dict[str, float]:
    """The PyPSA cost columns the curve sets, or {} when the row sets no curve."""
    curve = curve_of(row)
    if curve is None:
        return {}
    slope, no_load, price = curve
    p_nom = _num(row.get("p_nom")) or 0.0
    return {"marginal_cost": slope * price, "stand_by_cost": no_load * p_nom * price}


def apply(n, names=None, *, stamp: bool = True) -> list[str]:
    """Write ``derived_costs`` onto ``n.generators`` for each curve generator in ``names`` (all when None).

    Idempotent: the derived columns follow the inputs and ``p_nom`` only. A value that changes is stamped
    customised like any edit (``stamp``). Returns the generators whose costs changed.
    """
    gens = n.generators
    if gens.empty or not any(c in gens.columns for c in COLUMNS):
        return []
    idx = gens.index if names is None else [g for g in names if g in gens.index]
    written = []
    for name in idx:
        costs = derived_costs(gens.loc[name])
        if not costs:
            continue
        if "stand_by_cost" not in gens.columns:
            gens["stand_by_cost"] = 0.0
        changed = [
            col for col, val in costs.items() if not _same(gens.at[name, col], val)
        ]
        for col in changed:
            gens.at[name, col] = costs[col]
        if changed:
            written.append(name)
            if stamp:
                from services.asset_schema import provenance

                provenance.mark_customised(n, "Generator", name, changed)
    return written


class FuelCurveError(ValueError):
    """A solve refused before its LP is built: a curve it cannot price (`lp_refusals`)."""


def lp_refusals(n) -> list[str]:
    """``"<generator>: <why>"`` for each curve the LP cannot price: half a curve or a curve with a cost profile
    (`guard`). Read as the solve starts, before any transient transform: a CO2 price writes the cost columns of
    an emitter (a scalar adder, or a per-period series), which this would misread."""
    gens = n.generators
    if gens.empty or not any(c in gens.columns for c in COLUMNS):
        return []
    return [
        f"{name}: {msg}"
        for name in gens.index
        if (msg := guard(gens.loc[name], cost_series=cost_series(n, name)))
    ]


def wrap_lp(network, user_fn, log_queue=None):
    """Chain the fuel curve guard in front of ``user_fn`` (`solver_service.build_lp_chain`); ``user_fn``
    unchanged when nothing is refused. The refusals are read here, as the chain is built (before the solve's
    transient transforms), and raised as `FuelCurveError` when the LP is built. Writes nothing: staging the
    derived costs is `apply`'s, in `run_simulation` and the sizing loop's `_prepare`."""
    refusals = lp_refusals(network)
    if not refusals:
        return user_fn
    msg = "Fuel curve: " + "; ".join(refusals)
    if log_queue is not None:
        try:
            log_queue.put(f"[FUEL CURVE] ERROR: {msg}")
        except Exception:  # noqa: BLE001 - a log is never a reason to fail differently
            pass

    def fuel_curve_fn(n, snapshots):
        raise FuelCurveError("; ".join(refusals))

    return fuel_curve_fn


def fuel_use(
    n, period_years: Mapping[Any, float] | None = None
) -> list[dict[str, Any]]:
    """Each curve generator's fuel burn and cost over the solved horizon, from solved values only.

    Weighted like the LP objective: ``snapshot_weightings.objective`` × the period's ``years`` (``period_years``,
    from ``services.period_utils.period_years_map``; empty or None for a single period). A generator with no
    solved dispatch, or a no-load term and no solved status, reads None: never 0, never ``p_set``.
    """
    gens = n.generators
    if gens.empty or not any(c in gens.columns for c in COLUMNS):
        return []
    p = getattr(n.generators_t, "p", None)
    status = getattr(n.generators_t, "status", None)
    index = n.snapshots
    w = n.snapshot_weightings.objective.reindex(index).fillna(0.0)
    if isinstance(index, pd.MultiIndex) and period_years:
        from services.period_utils import years_for_period

        w = w * pd.Series(
            [years_for_period(period_years, k[0]) for k in index], index=index
        )
    rows = []
    for name in gens.index:
        curve = curve_of(gens.loc[name])
        if curve is None:
            continue
        slope, no_load, price = curve
        row = {
            "generator": name,
            "carrier": str(gens.at[name, "carrier"]),
            "fuel_mwh": None,
            "no_load_fuel_mwh": None,
            "fuel_cost_eur": None,
            "no_load_cost_eur": None,
        }
        rows.append(row)
        if p is None or name not in p.columns:
            continue
        no_load_on = no_load > 0 and _flag(gens.at[name, "committable"])
        if no_load_on and (status is None or name not in status.columns):
            continue
        p_nom = _num(gens.at[name, "p_nom"]) or 0.0
        slope_mwh = float((slope * p[name].reindex(index) * w).sum())
        no_load_mwh = (
            float((no_load * p_nom * status[name].reindex(index) * w).sum())
            if no_load_on
            else 0.0
        )
        fuel = slope_mwh + no_load_mwh
        row.update(
            fuel_mwh=fuel,
            no_load_fuel_mwh=no_load_mwh,
            fuel_cost_eur=fuel * price,
            no_load_cost_eur=no_load_mwh * price,
        )
    return rows
