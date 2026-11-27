"""Checked flow through one named two-port Link (assistant-assurance 09, S4).

PyPSA's Link sign convention, read from the raw dispatch only:

    p0[t] > 0  power withdrawn at bus0;   p1[t] < 0  power delivered at bus1
    p1[t] = -eta[t] x p0[t]

`eta[t]` is `links_t.efficiency[link][t]` when that column exists (a NaN or
inf there is missing data, never a fall-back to the static value), else the
static `links.efficiency`. With `w[t]` the generator snapshot weightings over
the exact window:

    link_input_energy    = sum_t w[t] x p0[t]               (net; reverse flow subtracts)
    link_output_energy   = -sum_t w[t] x p1[t]
    link_conversion_loss = link_input_energy - link_output_energy
                         = sum_t w[t] x (1 - eta[t]) x p0[t]
    peak_link_flow       = max_t |p0[t]|                     (every window snapshot, weight 0 too)
    peak_link_loading    = 100 x peak_link_flow / p_nom_opt  (percent)

The loading denominator is the optimised `p_nom_opt`, never `p_nom` or
`p_max_pu x p_nom`; with `p_max_pu > 1` it can exceed 100 % and is not
clamped. A Link with a non-empty bus2 or later port (efficiency2+) is not
modelled, and a p1 that does not equal -eta x p0 within FLOW_TOLERANCE is not
trusted; both withhold every figure. Reverse flow through a lossy Link would
book a gain, so it withholds the loss alone, never as a negative number.
"""

import math
import re

from harness import profile_common
from harness.derived import NEAR_ZERO

PROFILE = "link-flow/1"
REFUSALS = frozenset({"unknown_link", "unsupported_snapshot_scope"})
# |p1 + eta x p0| above this times max(1, |p0|) is not a solved Link's flow.
FLOW_TOLERANCE = 1e-6
# Every reason a slot can carry; pinned by exact set equality in the tests.
REASONS = frozenset(
    {
        "unverified_solve",
        "unsupported_link_ports",
        "invalid_or_missing_raw_data",
        "inconsistent_link_flow",
        "reverse_flow_with_losses",
        "no_link_capacity",
    }
)
METRICS = {
    "link_input_energy": {
        "formula_id": "link-bus0-net-withdrawal/1",
        "unit": "MWh",
        "label": "Energy into the link (bus0, net)",
    },
    "link_output_energy": {
        "formula_id": "link-bus1-net-delivery/1",
        "unit": "MWh",
        "label": "Energy out of the link (bus1, net)",
    },
    "link_conversion_loss": {
        "formula_id": "link-conversion-loss/1",
        "unit": "MWh",
        "label": "Conversion loss in the link",
    },
    "peak_link_flow": {
        "formula_id": "link-peak-bus0-flow/1",
        "unit": "MW",
        "label": "Peak flow at bus0",
    },
    "peak_link_loading": {
        "formula_id": "link-peak-loading-of-optimised-capacity/1",
        "unit": "%",
        "label": "Peak loading of the optimised capacity",
    },
}
DISCLOSURE = (
    "Link flow follows PyPSA's sign convention: p0 > 0 is power taken from bus0, p1 < 0 power delivered "
    "at bus1, and p1 = -efficiency x p0 (the time-varying efficiency where the link has one). Energies "
    "are net over the window, weighted by the generator snapshot weightings; the loss is energy in "
    "minus energy out. Peak flow is the largest |p0|; peak loading divides it by the optimised capacity "
    "p_nom_opt and can exceed 100 % where p_max_pu > 1. A link with a third port, a flow that does not "
    "match its efficiency, or reverse flow through a lossy link (loss only) withholds the figures."
)


def _series(frame, name, index):
    from harness.calculations import _number

    if frame is None or name not in frame.columns:
        return None
    return [_number(v) for v in frame[name].reindex(index)]


def _filled(value):
    """A port that names a bus: not None, NaN or blank."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return bool(str(value).strip())


def source(ctx, entity):
    """Called under the network lock: the Link's raw arrays; arithmetic in calculate."""
    from harness.calculations import _number

    n = ctx.network
    links = n.links
    if entity not in links.index:
        raise ValueError("unknown_link")
    index = profile_common.single_period_index(n)
    row = links.loc[entity]
    efficiency = _series(n.links_t.get("efficiency"), entity, index)
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "energy_weight": [
            _number(v) for v in n.snapshot_weightings["generators"].reindex(index)
        ],
        "bus0": str(row["bus0"]),
        "bus1": str(row["bus1"]),
        "extra_ports": sorted(
            c
            for c in links.columns
            if (m := re.fullmatch(r"bus(\d+)", c))
            and int(m[1]) >= 2
            and _filled(row[c])
        ),
        "p0": _series(n.links_t.get("p0"), entity, index),
        "p1": _series(n.links_t.get("p1"), entity, index),
        "efficiency_source": "series" if efficiency is not None else "static",
        "efficiency": efficiency
        if efficiency is not None
        else [_number(row["efficiency"])] * len(index),
        "p_nom_opt": _number(row["p_nom_opt"])
        if "p_nom_opt" in links.columns
        else None,
        "valid_solve": profile_common.lp_outputs_live(ctx),
    }


def _guard(raw, weights, p0, p1, eta):
    """The first reason no figure can stand, or None."""
    if not raw["valid_solve"] or not profile_common.finite(weights) or min(weights) < 0:
        return "unverified_solve"
    if raw["extra_ports"]:
        return "unsupported_link_ports"
    if p0 is None or p1 is None or not all(map(profile_common.finite, (p0, p1, eta))):
        return "invalid_or_missing_raw_data"
    if any(
        abs(b + e * a) > FLOW_TOLERANCE * max(1.0, abs(a))
        for a, b, e in zip(p0, p1, eta)
    ):
        return "inconsistent_link_flow"
    return None


def calculate(raw, scope):
    """The flows and the loading, or the slots unavailable with the first failing reason."""
    if (
        raw.get("version") != 1
        or raw.get("profile") != PROFILE
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("link_flow_source_identity")
    a, b = profile_common.window(raw["index"], scope)
    cut = lambda values: None if values is None else values[a : b + 1]  # noqa: E731
    weights, p0, p1, eta = (
        cut(raw[k]) for k in ("energy_weight", "p0", "p1", "efficiency")
    )
    reason = _guard(raw, weights, p0, p1, eta)
    available, reasons = {}, {}
    if reason is not None:
        reasons = dict.fromkeys(METRICS, reason)
    else:
        energy_in = math.fsum(w * x for w, x in zip(weights, p0))
        energy_out = -math.fsum(w * x for w, x in zip(weights, p1))
        peak = max(abs(x) for x in p0)
        available = {
            "link_input_energy": energy_in,
            "link_output_energy": energy_out,
            "peak_link_flow": peak,
        }
        if any(x < -NEAR_ZERO and e != 1.0 for x, e in zip(p0, eta)):
            reasons["link_conversion_loss"] = "reverse_flow_with_losses"
        else:
            available["link_conversion_loss"] = energy_in - energy_out
        capacity = raw["p_nom_opt"]
        if capacity is None or not math.isfinite(capacity) or capacity <= NEAR_ZERO:
            reasons["peak_link_loading"] = "no_link_capacity"
        else:
            available["peak_link_loading"] = 100.0 * peak / capacity
    return profile_common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def notes(raw, scope, label):
    """Always-shown scope lines: the direction, the efficiency read and the capacity."""
    del scope
    eta = (
        "time-varying efficiency"
        if raw["efficiency_source"] == "series"
        else "static efficiency"
    )
    capacity = raw["p_nom_opt"]
    shown = (
        "not available"
        if capacity is None or not math.isfinite(capacity)
        else f"{capacity:g} MW"
    )
    return [
        f"Flow from {label(raw['bus0'])} (bus0) to {label(raw['bus1'])} (bus1), {eta}; "
        "p0 > 0 is taken from bus0, p1 < 0 delivered at bus1.",
        f"Loading is against the optimised capacity p_nom_opt ({shown}).",
    ]
