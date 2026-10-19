"""
The process view (grid-to-process ticket 04, design §2.2 item 4): per product, what was made, delivered, bought in,
held and lost, and per process unit how long it ran and how often it started.

Every figure carries its unit and its weighting basis, as PyPSA weights it: Link, Load and Generator energy on
`snapshot_weightings.generators` (× investment-period years), a Store's level unweighted, EUR on `objective`. A
figure that cannot be resolved is null with a flag beside it (ADR-0001), and every number is finite: a non-finite
value is null and flagged, never NaN in the JSON (the #329 lesson).

The roles of a product bus's Generators come from `services.process_products`, never from their names alone.
"""
from __future__ import annotations

import math

from services.adequacy.eh_columns import _as_bool
from services.carrier_catalog import bus_tonne_factor
from services.compare.support import _build_snapshot_weights, _per_period_groupby
from services.period_utils import is_multi_period
from services.process_products import (
    BUY_IN,
    SHORTFALL,
    bus_unit,
    generator_role,
    lost_production_value,
    product_buses,
    product_value,
)

ENERGY_BASIS = "generators"
COST_BASIS = "objective"
_EPS = 1e-6


def _finite(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _tonne_factor(n, bus: str, unit: str) -> float | None:
    tf = bus_tonne_factor(n, bus)
    return 1.0 if tf is None and unit == "t" else tf


def _frame_col(frame, name: str, sns):
    if frame is None or name not in getattr(frame, "columns", []):
        return None
    return frame[name].reindex(sns).astype(float)


class _View:
    def __init__(self, n):
        self.n = n
        self.sns = n.snapshots
        self.multi = is_multi_period(n)
        self.w_energy = _build_snapshot_weights(n, ENERGY_BASIS)
        self.w_cost = _build_snapshot_weights(n, COST_BASIS)

    def wsum(self, series, weights) -> tuple[float | None, dict[str, float]]:
        """Weighted total and per-period split; (None, {}) when the series holds a non-finite value."""
        if series is None:
            return 0.0, {}
        if not series.map(math.isfinite).all():
            return None, {}
        weighted = series * weights
        return float(weighted.sum()), _per_period_groupby(weighted, self.sns, self.multi)

    def amount(self, series_list, unit: str, tf: float | None) -> dict:
        """A product quantity in the bus's unit, in t and in MWh, on the energy basis."""
        total, by_period = 0.0, {}
        for s in series_list:
            t, pp = self.wsum(s, self.w_energy)
            if t is None:
                total = None
                break
            total += t
            for k, v in pp.items():
                by_period[k] = by_period.get(k, 0.0) + v
        out: dict = {"value": total, "unit": unit, "weighting": ENERGY_BASIS, "by_period": by_period,
                     "flag": None if total is not None else "non_finite_values"}
        out["t"] = None if total is None or tf is None else total * tf
        out["t_flag"] = None if out["t"] is not None else out["flag"] or "tonne_factor_missing"
        if unit == "MWh":
            out["mwh"], out["mwh_flag"] = total, out["flag"]
        else:
            out["mwh"], out["mwh_flag"] = None, "material_product"
        return out


def _product(view: _View, bus: str) -> dict:
    n, sns = view.n, view.sns
    unit = bus_unit(n, bus)
    tf = _tonne_factor(n, bus, unit)

    made = []
    links_t = getattr(n, "links_t", None)
    for i in range(0, 5):
        col = f"bus{i}"
        if col not in n.links.columns:
            continue
        p_df = getattr(links_t, f"p{i}", None)
        for link in n.links.index[n.links[col] == bus]:
            s = _frame_col(p_df, link, sns)
            if s is not None:
                made.append((-s).clip(lower=0))

    loads = n.loads.index[n.loads["bus"] == bus]
    delivered = [_frame_col(getattr(n.loads_t, "p", None), ld, sns) for ld in loads]

    gens = n.generators.index[n.generators["bus"] == bus]
    gen_p = getattr(n.generators_t, "p", None)
    buy, lost, role_flags = [], [], set()
    for g in gens:
        role, flag = generator_role(n, g)
        if flag:
            role_flags.add(flag)
        if role == BUY_IN:
            buy.append(g)
        elif role == SHORTFALL:
            lost.append(g)

    lost_series = [_frame_col(gen_p, g, sns) for g in lost]
    lost_amount = view.amount([s.clip(lower=0) for s in lost_series if s is not None], unit, tf)
    eur, eur_flag, eur_pp = None, None, {}
    if not lost:
        lost_amount["flag"] = "no_shortfall_slack"
        eur_flag = "lost_production_value_missing"
    elif any(lost_production_value(n, g) is None for g in lost):
        eur_flag = "lost_production_value_missing"
    else:
        eur = 0.0
        for g, s in zip(lost, lost_series):
            if s is None:
                continue
            t, pp = view.wsum(s.clip(lower=0) * lost_production_value(n, g), view.w_cost)
            if t is None:
                eur, eur_flag, eur_pp = None, "non_finite_values", {}
                break
            eur += t
            for k, v in pp.items():
                eur_pp[k] = eur_pp.get(k, 0.0) + v
    lost_amount.update(eur=eur, eur_flag=eur_flag, eur_by_period=eur_pp, eur_weighting=COST_BASIS)

    inventory = []
    for st in n.stores.index[n.stores["bus"] == bus]:
        e = _frame_col(getattr(n.stores_t, "e", None), st, sns)
        values = [] if e is None else [_finite(v) for v in e.values]
        inventory.append({"name": str(st), "unit": unit, "weighting": "none", "e": values,
                          "flag": "non_finite_values" if any(v is None for v in values) else None})

    return {
        "bus": str(bus),
        "carrier": str(n.buses.at[bus, "carrier"] or ""),
        "unit": unit,
        "t_per_unit": tf,
        "product_value": product_value(n, bus),
        "production": view.amount(made, unit, tf),
        "delivery": view.amount([s for s in delivered if s is not None], unit, tf),
        "buy_in": view.amount([s.clip(lower=0) for s in (_frame_col(gen_p, g, sns) for g in buy) if s is not None],
                              unit, tf),
        "lost": lost_amount,
        "inventory": inventory,
        "role_flag": sorted(role_flags)[0] if role_flags else None,
    }


def _process_unit(view: _View, link: str) -> dict:
    n, sns = view.n, view.sns
    p0 = _frame_col(getattr(n.links_t, "p0", None), link, sns)
    status = _frame_col(getattr(n.links_t, "status", None), link, sns)
    start_up = _frame_col(getattr(n.links_t, "start_up", None), link, sns)
    committable = "committable" in n.links.columns and _as_bool(n.links.at[link, "committable"])
    flag = None
    if committable and status is not None:
        # Under linearised UC status and start_up are fractional; report them as they are, never rounded.
        fractional = ((status - status.round()).abs() > _EPS).any() or (
            start_up is not None and ((start_up - start_up.round()).abs() > _EPS).any())
        flag = "linearised_uc" if fractional else None
        running, _ = view.wsum(status, view.w_energy)
        running_basis = "status"
        starts = _finite(start_up.sum()) if start_up is not None else None
    else:
        on = (p0 > _EPS).astype(float) if p0 is not None else None
        running, _ = view.wsum(on, view.w_energy)
        running_basis = "p0_positive"
        starts = None if on is None else float(((on.diff() > 0.5).sum()))
    hours = _finite(n.snapshot_weightings["stores"].reindex(sns).sum()) if "stores" in n.snapshot_weightings else None
    return {
        "name": str(link),
        "product_bus": str(n.links.at[link, "bus1"]),
        "running_hours": running,
        "running_hours_basis": running_basis,
        "running_hours_weighting": ENERGY_BASIS,
        "starts": starts,
        "starts_basis": "modelled_snapshots",
        "modelled_hours": hours,
        "flag": flag if running is not None else "non_finite_values",
    }


def compute_process_view(n) -> dict:
    """The process view of a solved network: `products` (one per product bus) and `process_units` (one per Link with
    `eh_process` set). Both empty on a network with neither."""
    view = _View(n)
    units = []
    if "eh_process" in n.links.columns:
        units = [_process_unit(view, link) for link in n.links.index if _as_bool(n.links.at[link, "eh_process"])]
    return {"products": [_product(view, b) for b in product_buses(n)], "process_units": units}
