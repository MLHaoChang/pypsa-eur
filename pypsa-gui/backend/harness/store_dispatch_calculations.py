"""Checked dispatch and energy of one named Store (assistant-assurance 09, S5).

PyPSA 1.1.2's Store energy balance (`define_store_constraints`), read from the
raw arrays only:

    e[t] = eff[t] x e[t-1] - w[t] x p[t]        p[t] > 0 discharges to the bus
    eff[t] = (1 - sl[t]) ** w[t]                 standing loss is per hour

`w` is `snapshot_weightings.stores`. `sl[t]` is `stores_t.standing_loss[store][t]`
where that column exists (a NaN there is missing data, never the static value),
else the static `stores.standing_loss`. The state before the window is:

- e[a-1] for a window that starts after the first snapshot;
- e[last] for an `e_cyclic` Store whose window starts at the first snapshot;
- `e_initial` for a non-cyclic Store whose window starts at the first
  snapshot. PyPSA applies no standing loss to `e_initial`: there eff is 1.

Over the exact window:

    store_charged_energy    = sum_t w[t] x max(0, -p[t])
    store_discharged_energy = sum_t w[t] x max(0,  p[t])
    store_net_energy        = discharged - charged       (net delivered to the bus)
    store_standing_loss     = sum_t (1 - eff[t]) x e[t-1]
    peak_store_charging     = max_t max(0, -p[t])        (every snapshot, weight 0 too)
    peak_store_discharging  = max_t max(0,  p[t])
    final_store_energy      = e[b]                       (the window's last snapshot)
    peak_store_fill         = 100 x max_t e[t] / e_nom_opt

The fill divides by the optimised `e_nom_opt`, never `e_nom`, and is not
clamped: it stays below 100 % when e_max_pu < 1 and is negative when
e_min_pu < 0 lets e go below zero. Each window snapshot must satisfy the
balance within BALANCE_TOLERANCE x max(1, |e[t]|), and, where `e_nom_opt` is
usable, e_min_pu[t] x e_nom_opt <= e[t] <= e_max_pu[t] x e_nom_opt within the
same tolerance (the time-varying bounds where the Store has them).
"""

import math

from harness import profile_common
from harness.derived import NEAR_ZERO

PROFILE = "store-dispatch/1"
REFUSALS = frozenset({"unknown_store", "unsupported_snapshot_scope"})
# |e[t] - (eff x e[t-1] - w x p[t])| above this times max(1, |e[t]|) is not a
# solved Store's balance; the same slack applies to the energy bounds.
BALANCE_TOLERANCE = 1e-6
# Every reason a slot can carry; pinned by exact set equality in the tests.
REASONS = frozenset(
    {
        "unverified_solve",
        "invalid_or_missing_raw_data",
        "unsupported_standing_loss",
        "inconsistent_store_balance",
        "store_bound_violation",
        "negative_store_energy_with_losses",
        "no_store_capacity",
    }
)
METRICS = {
    "store_charged_energy": {
        "formula_id": "store-charged-energy/1",
        "unit": "MWh",
        "label": "Energy charged into the store",
    },
    "store_discharged_energy": {
        "formula_id": "store-discharged-energy/1",
        "unit": "MWh",
        "label": "Energy discharged from the store",
    },
    "store_net_energy": {
        "formula_id": "store-net-delivered-energy/1",
        "unit": "MWh",
        "label": "Net energy delivered to the bus",
    },
    "store_standing_loss": {
        "formula_id": "store-standing-loss/1",
        "unit": "MWh",
        "label": "Standing loss in the store",
    },
    "peak_store_charging": {
        "formula_id": "store-peak-charging/1",
        "unit": "MW",
        "label": "Peak charging power",
    },
    "peak_store_discharging": {
        "formula_id": "store-peak-discharging/1",
        "unit": "MW",
        "label": "Peak discharging power",
    },
    "final_store_energy": {
        "formula_id": "store-final-energy/1",
        "unit": "MWh",
        "label": "Stored energy at the window's end",
    },
    "peak_store_fill": {
        "formula_id": "store-peak-fill-of-optimised-capacity/1",
        "unit": "%",
        "label": "Peak fill of the optimised capacity",
    },
}
DISCLOSURE = (
    "Store power follows PyPSA's sign convention: p > 0 is discharge to the bus, p < 0 charging. Energies "
    "are weighted by the store snapshot weightings; the standing loss is (1 - (1 - standing loss)^w) of the "
    "energy held before each snapshot, using the time-varying standing loss where the store has one. The "
    "window starts from the energy before it: the last snapshot's for a cyclic store, e_initial (with no "
    "standing loss) for a non-cyclic one. Peak fill divides the largest energy by the optimised capacity "
    "e_nom_opt and is not clamped. A store whose energy does not follow its balance or leaves its bounds "
    "withholds the figures."
)


def _dynamic(n, attribute, entity, index):
    """The Store's series where it has one (NaN stays missing), else the static value."""
    from harness.calculations import _number

    frame = n.stores_t.get(attribute)
    if frame is not None and entity in frame.columns:
        return "series", [_number(v) for v in frame[entity].reindex(index)]
    return "static", [_number(n.stores.at[entity, attribute])] * len(index)


def _series(frame, name, index):
    from harness.calculations import _number

    if frame is None or name not in frame.columns:
        return None
    return [_number(v) for v in frame[name].reindex(index)]


def source(ctx, entity):
    """Called under the network lock: the Store's raw arrays; arithmetic in calculate."""
    from harness.calculations import _number

    n = ctx.network
    stores = n.stores
    if entity not in stores.index:
        raise ValueError("unknown_store")
    index = profile_common.single_period_index(n)
    row = stores.loc[entity]
    loss_source, standing_loss = _dynamic(n, "standing_loss", entity, index)
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "energy_weight": [
            _number(v) for v in n.snapshot_weightings["stores"].reindex(index)
        ],
        "bus": str(row["bus"]),
        "cyclic": bool(row["e_cyclic"]),
        "e_initial": _number(row["e_initial"]),
        "p": _series(n.stores_t.get("p"), entity, index),
        "e": _series(n.stores_t.get("e"), entity, index),
        "standing_loss_source": loss_source,
        "standing_loss": standing_loss,
        "e_min_pu": _dynamic(n, "e_min_pu", entity, index)[1],
        "e_max_pu": _dynamic(n, "e_max_pu", entity, index)[1],
        "e_nom_opt": _number(row["e_nom_opt"])
        if "e_nom_opt" in stores.columns
        else None,
        "valid_solve": profile_common.lp_outputs_live(ctx),
    }


def _capacity(raw):
    capacity = raw["e_nom_opt"]
    if capacity is None or not math.isfinite(capacity) or capacity <= NEAR_ZERO:
        return None
    return capacity


def _before(raw, a):
    """The energy held before the window's first snapshot, and whether loss applies to it."""
    if a > 0:
        return raw["e"][a - 1], True
    if raw["cyclic"]:
        return raw["e"][-1], True
    return raw["e_initial"], False


def _efficiency(t, w, loss, lossy):
    """The standing efficiency (1 - sl)^w, or 1 at the first snapshot when the start is e_initial."""
    return (1.0 - loss) ** w if lossy or t else 1.0


def _guard(raw, a, b, capacity):
    """The first reason no figure can stand, or None."""
    weights = raw["energy_weight"][a : b + 1]
    if not raw["valid_solve"] or not profile_common.finite(weights) or min(weights) < 0:
        return "unverified_solve"
    if raw["p"] is None or raw["e"] is None:
        return "invalid_or_missing_raw_data"
    p, e, sl = (raw[k][a : b + 1] for k in ("p", "e", "standing_loss"))
    bounds = (raw["e_min_pu"][a : b + 1], raw["e_max_pu"][a : b + 1])
    previous, lossy = _before(raw, a)
    if not all(map(profile_common.finite, (p, e, sl, [previous]))) or (
        capacity is not None and not all(map(profile_common.finite, bounds))
    ):
        return "invalid_or_missing_raw_data"
    if any(not 0.0 <= x < 1.0 for x in sl):
        return "unsupported_standing_loss"
    for t, (w, x, now, loss) in enumerate(zip(weights, p, e, sl)):
        eff = _efficiency(t, w, loss, lossy)
        if abs(now - (eff * previous - w * x)) > BALANCE_TOLERANCE * max(1.0, abs(now)):
            return "inconsistent_store_balance"
        previous = now
    if capacity is not None and any(
        now < low * capacity - BALANCE_TOLERANCE * max(1.0, abs(now))
        or now > high * capacity + BALANCE_TOLERANCE * max(1.0, abs(now))
        for now, low, high in zip(e, *bounds)
    ):
        return "store_bound_violation"
    return None


def calculate(raw, scope):
    """The energies, peaks and fill, or the slots unavailable with the first failing reason."""
    if (
        raw.get("version") != 1
        or raw.get("profile") != PROFILE
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("store_dispatch_source_identity")
    a, b = profile_common.window(raw["index"], scope)
    capacity = _capacity(raw)
    reason = _guard(raw, a, b, capacity)
    available, reasons = {}, {}
    if reason is not None:
        reasons = dict.fromkeys(METRICS, reason)
    else:
        weights, p, e, sl = (
            raw[k][a : b + 1] for k in ("energy_weight", "p", "e", "standing_loss")
        )
        charged = math.fsum(w * max(0.0, -x) for w, x in zip(weights, p))
        discharged = math.fsum(w * max(0.0, x) for w, x in zip(weights, p))
        available = {
            "store_charged_energy": charged,
            "store_discharged_energy": discharged,
            "store_net_energy": discharged - charged,
            "peak_store_charging": max(max(0.0, -x) for x in p),
            "peak_store_discharging": max(max(0.0, x) for x in p),
            "final_store_energy": e[-1],
        }
        start, lossy = _before(raw, a)
        held = [start, *e[:-1]]
        terms = [
            (1.0 - _efficiency(t, w, loss, lossy), before)
            for t, (w, loss, before) in enumerate(zip(weights, sl, held))
        ]
        if any(share > 0.0 and before < -NEAR_ZERO for share, before in terms):
            reasons["store_standing_loss"] = "negative_store_energy_with_losses"
        else:
            available["store_standing_loss"] = math.fsum(s * x for s, x in terms)
        if capacity is None:
            reasons["peak_store_fill"] = "no_store_capacity"
        else:
            available["peak_store_fill"] = 100.0 * max(e) / capacity
    return profile_common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def notes(raw, scope, label):
    """Always-shown scope lines: the sign, the start state, the loss read and the capacity."""
    start = profile_common.window(raw["index"], scope)[0]
    if start > 0:
        before = "the energy at the snapshot before the window"
    elif raw["cyclic"]:
        before = "the last snapshot's energy (cyclic store)"
    else:
        before = "e_initial, with no standing loss applied"
    loss = (
        "time-varying standing loss"
        if raw["standing_loss_source"] == "series"
        else "static standing loss"
    )
    capacity = _capacity(raw)
    shown = "not available" if capacity is None else f"{capacity:g} MWh"
    return [
        f"Store at {label(raw['bus'])}: p > 0 is discharge to the bus, p < 0 charging; {loss}.",
        f"The window starts from {before}.",
        f"Fill is against the optimised capacity e_nom_opt ({shown}).",
    ]
