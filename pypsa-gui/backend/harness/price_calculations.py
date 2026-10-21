"""Bounded bus marginal prices for checked answers (checked-results S5).

Observed `buses_t.marginal_price` (already per MWh: PyPSA normalises the dual
by the objective weighting), summarised with generator snapshot weights as
the time weight. No claim about which unit sets the price.
"""

import math

from harness import profile_common as common

PROFILE = "bus-price/1"
VOLL_TOLERANCE = 1e-6
REFUSALS = frozenset({"unknown_bus", "unsupported_snapshot_scope", "misaligned_source"})
METRICS = {
    "mean_price": {
        "formula_id": "bus-price-time-weighted-mean/1",
        "unit": "EUR/MWh",
        "label": "Time-weighted mean price (generator snapshot weights)",
    },
    "min_price": {"formula_id": "bus-price-min/1", "unit": "EUR/MWh", "label": "Lowest price"},
    "max_price": {"formula_id": "bus-price-max/1", "unit": "EUR/MWh", "label": "Highest price"},
    "hours_at_voll": {
        "formula_id": "bus-price-weighted-hours-at-voll/1",
        "unit": "h",
        "label": "Weighted hours priced at the solve's VoLL",
    },
}
DISCLOSURE = (
    "Observed LP marginal prices of this bus; the mean is time-weighted with generator snapshot "
    "weights. At VoLL means a price within 1e-6 of the VoLL the solve used. Which unit sets the "
    "price is not checked."
)


def source(ctx, entity):
    from harness.calculations import _number
    from services import period_utils

    n = ctx.network
    index = common.single_period_index(n)
    if entity not in n.buses.index:
        raise ValueError("unknown_bus")
    prices = n.buses_t.marginal_price
    if prices is None or not prices.index.equals(index):
        raise ValueError("misaligned_source")
    settings = ctx.solver_state.get("last_solve_settings") or {}
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "weight": [_number(v) for v in period_utils.snapshot_weights(n, "generators").reindex(index)],
        "price": [_number(v) for v in prices[entity]] if entity in prices.columns else [None] * len(index),
        "voll": _number(settings.get("voll_eur_per_mwh")),
        "valid_solve": common.lp_outputs_live(ctx),
    }


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("price_source_identity")
    a, b = common.window(raw["index"], scope)
    price, weight = raw["price"][a : b + 1], raw["weight"][a : b + 1]
    available, reasons = {}, {}
    base = "unverified_solve" if not raw["valid_solve"] else "invalid_or_missing_price_data"
    if raw["valid_solve"] and common.finite(price + weight) and all(w >= 0 for w in weight) and sum(weight) > 0:
        available = {
            "mean_price": math.fsum(w * p for w, p in zip(weight, price)) / math.fsum(weight),
            "min_price": min(price),
            "max_price": max(price),
        }
        voll = raw["voll"]
        if voll is not None and voll > 0:
            available["hours_at_voll"] = math.fsum(
                w for w, p in zip(weight, price) if p >= voll * (1 - VOLL_TOLERANCE)
            )
        else:
            reasons["hours_at_voll"] = "voll_missing_or_zero"
    reasons = {m: reasons.get(m, base) for m in METRICS}
    return common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)
