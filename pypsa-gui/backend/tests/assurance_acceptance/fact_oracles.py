"""AA-05 S5a raw-data oracles (AA05-FREEZE-1 (iii); GK B (34) K3-K5).

One function per profile module: it reads the raw frames of the network (or
the stored report rows / scorecard rows of the two non-network profiles) and
returns one fact's value. Nothing here calls the harness or services; a test
holds this module's imports to numpy, pandas and the standard library.

A window is `start` and `end` snapshots, both inclusive, as the checked scope
names them: in index positions [a, b + 1). `column` names the
snapshot_weightings column, so a test can show a wrong column differs.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def rows(index, start=None, end=None):
    """Index positions of the inclusive window [start, end]."""
    a = 0 if start is None else index.get_loc(pd.Timestamp(start))
    b = len(index) - 1 if end is None else index.get_loc(pd.Timestamp(end))
    return slice(a, b + 1)


def _dynamic(n, table, attribute, name):
    """The time series of `attribute`, or the static value on every snapshot."""
    frame = getattr(n, table + "_t")[attribute]
    if name in frame.columns:
        return frame[name].to_numpy(dtype=float)
    return np.full(len(n.snapshots), float(getattr(n, table).at[name, attribute]))


def _weights(n, column):
    return n.snapshot_weightings[column].to_numpy(dtype=float)


def _total(values):
    return math.fsum(values.tolist())


# calculations.py
def dispatch_energy(n, generator, start=None, end=None, column="generators"):
    """Sum of p(t) w(t) over the window."""
    p = n.generators_t.p[generator].to_numpy(dtype=float)
    return _total((p * _weights(n, column))[rows(n.snapshots, start, end)])


# storage_calculations.py
def discharged_energy(n, unit, start=None, end=None, column="stores"):
    """Sum of p_dispatch(t) w(t): the grid-side discharge, never p_store."""
    p = n.storage_units_t.p_dispatch[unit].to_numpy(dtype=float)
    return _total((_weights(n, column) * p)[rows(n.snapshots, start, end)])


# storage_costs.py
def storage_discharge_expenditure(n, unit, start=None, end=None, column="objective"):
    """Sum of p_dispatch(t) marginal_cost(t) w(t)."""
    p = n.storage_units_t.p_dispatch[unit].to_numpy(dtype=float)
    price = _dynamic(n, "storage_units", "marginal_cost", unit)
    return _total((p * price * _weights(n, column))[rows(n.snapshots, start, end)])


# carrier_calculations.py
def carrier_generator_energy(n, carrier, start=None, end=None, column="generators"):
    """Sum over the carrier's generators, and the window, of p(t) w(t)."""
    names = [g for g in n.generators.index if n.generators.at[g, "carrier"] == carrier]
    p = n.generators_t.p[names].to_numpy(dtype=float)
    w = _weights(n, column)[:, None]
    return _total((w * p)[rows(n.snapshots, start, end)].ravel())


# branch_calculations.py
def hours_above_rating(
    n, line, start=None, end=None, column="generators", tolerance=1e-6
):
    """Weight of the snapshots where max(|p0|, |p1|) exceeds s_nom x s_max_pu(t)."""
    flow = np.maximum(
        np.abs(n.lines_t.p0[line].to_numpy(dtype=float)),
        np.abs(n.lines_t.p1[line].to_numpy(dtype=float)),
    )
    row = n.lines.loc[line]
    rating = float(row["s_nom_opt"] if row["s_nom_extendable"] else row["s_nom"])
    over = flow / (rating * _dynamic(n, "lines", "s_max_pu", line)) > 1 + tolerance
    window = rows(n.snapshots, start, end)
    return _total(_weights(n, column)[window][over[window]])


# lost_load_calculations.py
def lost_load_energy(shed, weightings, load, start=None, end=None, column="generators"):
    """Sum of max(shed(t), 0) w(t) over the solve's captured VoLL slack of `load`."""
    values = np.maximum(shed[load].to_numpy(dtype=float), 0.0)
    w = weightings[column].to_numpy(dtype=float)
    return _total((w * values)[rows(weightings.index, start, end)])


# price_calculations.py
def mean_price(n, bus, start=None, end=None, column="generators"):
    """Weight-weighted mean of the bus price: sum(w p) / sum(w)."""
    window = rows(n.snapshots, start, end)
    p = n.buses_t.marginal_price[bus].to_numpy(dtype=float)[window]
    w = _weights(n, column)[window]
    return _total(w * p) / _total(w)


# emissions_calculations.py
def generator_emissions(n, generator, start=None, end=None, column="generators"):
    """Sum of w(t) p(t) / efficiency(t) x the carrier's co2_emissions."""
    p = n.generators_t.p[generator].to_numpy(dtype=float)
    eta = _dynamic(n, "generators", "efficiency", generator)
    co2 = float(n.carriers.at[n.generators.at[generator, "carrier"], "co2_emissions"])
    return _total((_weights(n, column) * p / eta * co2)[rows(n.snapshots, start, end)])


# curtailment_calculations.py
def curtailed_energy(n, generator, start=None, end=None, column="generators"):
    """Sum of max(p_max_pu(t) x capacity - p(t), 0) w(t)."""
    row = n.generators.loc[generator]
    capacity = float(row["p_nom_opt"] if row["p_nom_extendable"] else row["p_nom"])
    available = n.generators_t.p_max_pu[generator].to_numpy(dtype=float) * capacity
    residual = available - n.generators_t.p[generator].to_numpy(dtype=float)
    return _total(
        (_weights(n, column) * np.maximum(residual, 0.0))[rows(n.snapshots, start, end)]
    )


# capacity_calculations.py
def optimal_capacity(n, generator):
    """p_nom_opt when extendable, else p_nom: no snapshot weighting."""
    row = n.generators.loc[generator]
    return float(row["p_nom_opt"] if row["p_nom_extendable"] else row["p_nom"])


# storage_cycles_calculations.py
def equivalent_cycles(n, store, start=None, end=None, column="stores"):
    """Sum of max(p(t), 0) w(t) over e_nom: a Store's p > 0 leaves it."""
    p = n.stores_t.p[store].to_numpy(dtype=float)
    row = n.stores.loc[store]
    capacity = float(row["e_nom_opt"] if row["e_nom_extendable"] else row["e_nom"])
    out = _weights(n, column) * np.maximum(p, 0.0)
    return _total(out[rows(n.snapshots, start, end)]) / capacity


# energy_balance_calculations.py
def served_energy(n, shed=None, start=None, end=None, column="generators"):
    """Sum of max(load(t) - shed(t), 0) w(t) over every Load."""
    served = n.loads_t.p.to_numpy(dtype=float).sum(axis=1)
    if shed is not None:
        served = served - np.maximum(
            np.nan_to_num(shed.to_numpy(dtype=float)), 0.0
        ).sum(axis=1)
    return _total(
        (np.maximum(served, 0.0) * _weights(n, column))[rows(n.snapshots, start, end)]
    )


# site_import_calculations.py
def grid_import_energy(n, start=None, end=None, column="generators"):
    """Sum of the "grid"-carrier generators' positive output times w(t)."""
    grid = [
        g
        for g in n.generators.index
        if str(n.generators.at[g, "carrier"]).strip().lower() == "grid"
    ]
    supply = np.maximum(n.generators_t.p[grid].to_numpy(dtype=float), 0.0).sum(axis=1)
    return _total((supply * _weights(n, column))[rows(n.snapshots, start, end)])


# investment_calculations.py
def equity_npv(payload):
    """NPV of the stored equity cash rows at the stored cost of equity; year 0 undiscounted."""
    cash = np.asarray(payload["cash"]["equity_post_tax"], dtype=float)
    years = np.arange(len(cash), dtype=float)
    return _total(cash / (1.0 + float(payload["cost_of_equity"])) ** years)


# copt_calculations.py
def screening_lole(n, column="generators"):
    """Sum of w(t) P[available < residual(t)] over two-state units on a 1 MW grid.

    Two-state units are generators with an outage rate; the rest are must-take,
    netted from demand at p_max_pu(t) x p_nom. Available is strictly below the
    residual: grid points k < ceil(r - 1e-12)."""
    gens = n.generators
    rate = pd.to_numeric(gens.get("outage_rate_value"), errors="coerce")
    two_state = [g for g in gens.index if np.isfinite(rate[g]) and rate[g] > 0]
    probability = np.ones(1)
    for g in two_state:
        capacity, q = int(round(float(gens.at[g, "p_nom"]))), float(rate[g])
        unit = np.zeros(capacity + 1)
        unit[0], unit[capacity] = q, 1.0 - q
        probability = np.convolve(probability, unit)
    demand = n.loads_t.p_set.to_numpy(dtype=float).sum(axis=1)
    take = sum(
        (
            _dynamic(n, "generators", "p_max_pu", g) * float(gens.at[g, "p_nom"])
            for g in gens.index
            if g not in two_state
        ),
        np.zeros(len(n.snapshots)),
    )
    lolp = []
    for r in demand - take:
        k = min(math.ceil(r - 1e-12), len(probability)) if r > 0 else 0
        lolp.append(math.fsum(probability[:k].tolist()))
    return _total(_weights(n, column) * np.asarray(lolp))


# grid_readiness_calculations.py
# The scalar rows and the rule each declares, restated from the grid code.
READINESS_RULES = {
    "scr": (">=", "warn", 0.0),
    "ramp_rate": ("<=", "fail", 1e-9),
    "rapid_voltage_change": ("<=", "warn", 1e-9),
    "rapid_voltage_change_infrequent": ("<=", "warn", 1e-9),
    "reactive_range": (">=", "warn", 1e-9),
    "voltage_unbalance": ("<=", "warn", 1e-9),
    "motor_start:": ("<=", "warn", 1e-9),
}


def _rule(row_id):
    if row_id in READINESS_RULES:
        return READINESS_RULES[row_id]
    prefix = row_id.partition(":")[0] + ":"
    return READINESS_RULES.get(prefix) if ":" in row_id else None


def readiness_verdict(row):
    """The verdict the row's value earns against its limit, or None when the row
    is not a scalar row with its declared rule and finite numbers."""
    rule, declared = _rule(row["id"]), row.get("rule")
    value, limit = row.get("project_value"), row.get("limit")
    numbers = all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in (value, limit)
    )
    if (
        rule is None
        or not numbers
        or row.get("verdict") == "unknown"
        or not isinstance(declared, dict)
    ):
        return None
    comparator, otherwise, tolerance = rule
    if (
        declared.get("comparator"),
        declared.get("otherwise"),
        declared.get("tolerance"),
    ) != rule:
        return None
    met = (
        value <= limit + tolerance
        if comparator == "<="
        else value < limit
        if comparator == "<"
        else value >= limit - tolerance
    )
    return "pass" if met else otherwise


def checked_pass(rows_):
    """Count of rows whose stored verdict is pass and is reproduced by their rule."""
    return sum(
        1
        for row in rows_
        if row.get("verdict") == "pass" and readiness_verdict(row) == "pass"
    )
