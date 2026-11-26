"""Checked renewable share of the electricity used on site (assistant-assurance 10, R4).

The figure is the one `/results/renewable_fraction` shows (HOMER's renewable
fraction, `services/solver/renewable_fraction.py`), in percent:

    renewable_share = 100 x (1 - E_nonren / E_served)

Membership is that module's own classification, read by name
(`renewable_fraction.classify`, whose renewable test is
`carrier_kpis._is_renewable_carrier`): the non-renewable Generators counted
at the generator and the active Loads, both on electrical buses. The energy
is computed here from the raw dispatch and the generator snapshot weightings,
never through `renewable_fraction()`; that is what the tests check.

Only the plain population is answered: every other class `classify` names (a
source Link out of a supply bus, a conversion Link or net cluster, an
injecting or unattributed source) changes the numerator or the denominator
in ways this basis does not model, so it withholds every figure with
`unsupported_supply_path`. Shed load, demand response or data-centre
curtailment (the slacks the solve removes) likewise withhold every figure
(`unserved_energy_not_modelled`); none is ever counted as zero.

A share is in percent. Comparing two shares is a percentage-point delta
(`percentage_point_delta/1`), never a percent change.
"""

import math

from harness import profile_common
from harness.derived import NEAR_ZERO

PROFILE = "renewable-share/1"
REFUSALS = frozenset({"network_scope_required", "unsupported_snapshot_scope"})
# The classes of `renewable_fraction.classify` this basis reads, and those that
# withhold every figure when non-empty. Together they are exactly the keys
# `classify` returns (pinned in the tests), so a new class cannot pass unread.
READ = frozenset({"nonren_generators", "loads"})
# Read by neither the KPI's arithmetic nor this one: renewable output and
# storage chargers whose energy comes back to electricity.
NOT_COUNTED = frozenset({"renewable_generators", "storage_links"})
WITHHOLDING = frozenset(
    {
        "source_links",
        "conversion_links",
        "conversion_inputs",
        "net_clusters",
        "open_clusters",
        "injecting_generators",
        "unattributed_generators",
        "unattributed_links",
        "unattributed_stores",
        "slack_generators",
    }
)
MAX_MEMBERS = 128
# Every reason a slot can carry; pinned by exact set equality in the tests.
REASONS = frozenset(
    {
        "unverified_solve",
        "invalid_or_missing_raw_data",
        "unsupported_supply_path",
        "unserved_energy_not_modelled",
        "unsupported_population",
        "no_electricity_served",
        "nonrenewable_exceeds_served",
    }
)
METRICS = {
    "renewable_share": {
        "formula_id": "renewable-share-of-served/1",
        "unit": "%",
        "label": "Renewable share of the electricity used on site",
    },
    "nonrenewable_energy": {
        "formula_id": "nonrenewable-generator-energy/1",
        "unit": "MWh",
        "label": "Non-renewable electricity supplied",
    },
    "electricity_used": {
        "formula_id": "served-load-energy/1",
        "unit": "MWh",
        "label": "Electricity used on site (served load)",
    },
}
SHARE_ONLY = frozenset({"renewable_share"})
DISCLOSURE = (
    "Renewable share is 100 x (1 - non-renewable electricity / electricity used on site), in percent, "
    "with the app's renewable-fraction classification: non-renewable Generators on electrical buses "
    "(positive output only) and active Loads on electrical buses, weighted by the generator snapshot "
    "weightings. Storage is neither supply nor use. A source Link, a conversion Link, an unattributed "
    "or injecting source, or shed or curtailed load withholds every figure. Compare two shares in "
    "percentage points, not percent."
)


def _slacks_removed(ctx):
    """True when the solve removed a slack whose energy is not served: VoLL, DSR, DC curtailment."""
    from services.commercial.settlement_inputs import dsr_activation
    from services.solver import dc_curtailment

    n = ctx.network
    capture = ctx.solver_state.get("last_lost_load") or {}
    return bool(
        capture.get("lost_load_t") is not None
        or capture.get("dsr_t") is not None
        or dc_curtailment.META_KEY in capture
        or (getattr(n, "meta", None) or {}).get(dc_curtailment.META_KEY)
        or dsr_activation(n)[0] is not None
    )


def _column(frame, name, index):
    from harness.calculations import _number

    if frame is None or name not in frame.columns:
        return None
    return [_number(v) for v in frame[name].reindex(index)]


def source(ctx, entity):
    """Called under the network lock: the classification and raw arrays; arithmetic in calculate."""
    from harness.calculations import _number
    from services.solver.renewable_fraction import classify

    n = ctx.network
    if entity != "network":
        raise ValueError("network_scope_required")
    index = profile_common.single_period_index(n)
    classes = classify(n)
    weights = n.snapshot_weightings["generators"].reindex(index)
    nonren = [str(g) for g in classes["nonren_generators"]]
    loads = [str(l) for l in classes["loads"]]
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "energy_weight": [_number(v) for v in weights],
        "withholding": sorted(k for k in WITHHOLDING if classes[k]),
        "unread": sorted(set(classes) - READ - NOT_COUNTED - WITHHOLDING),
        "slacks_removed": _slacks_removed(ctx),
        "generators": {g: _column(n.generators_t.p, g, index) for g in nonren}
        if len(nonren) <= MAX_MEMBERS
        else None,
        "loads": {l: _column(n.loads_t.p, l, index) for l in loads}
        if len(loads) <= MAX_MEMBERS
        else None,
        "renewable_generators": len(classes["renewable_generators"]),
        "valid_solve": profile_common.lp_outputs_live(ctx),
    }


def _guard(raw, weights):
    """The first reason the plain basis cannot stand for the population, or None."""
    if not raw["valid_solve"] or not profile_common.finite(weights) or min(weights) < 0:
        return "unverified_solve"
    if raw["unread"] or raw["generators"] is None or raw["loads"] is None:
        return "unsupported_population"
    if raw["withholding"]:
        return "unsupported_supply_path"
    if raw["slacks_removed"]:
        return "unserved_energy_not_modelled"
    series = list(raw["generators"].values()) + list(raw["loads"].values())
    if any(s is None or not profile_common.finite(s) for s in series):
        return "invalid_or_missing_raw_data"
    return None


def calculate(raw, scope):
    """The energies and the share, or every slot unavailable with the first failing reason."""
    if (
        raw.get("version") != 1
        or raw.get("profile") != PROFILE
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("renewable_share_source_identity")
    a, b = profile_common.window(raw["index"], scope)
    weights = raw["energy_weight"][a : b + 1]
    reason = _guard(raw, weights)
    available, reasons = {}, {}
    if reason is not None:
        reasons = dict.fromkeys(METRICS, reason)
    else:
        nonren = math.fsum(
            w * max(p[a + i], 0.0)
            for p in raw["generators"].values()
            for i, w in enumerate(weights)
        )
        served = math.fsum(
            w * p[a + i] for p in raw["loads"].values() for i, w in enumerate(weights)
        )
        available = {"nonrenewable_energy": nonren, "electricity_used": served}
        if served <= NEAR_ZERO:
            reasons["renewable_share"] = "no_electricity_served"
        elif nonren > served:
            # All non-renewable output counts as used on site; above the use it is undefined.
            reasons["renewable_share"] = "nonrenewable_exceeds_served"
        else:
            available["renewable_share"] = 100.0 * (1.0 - nonren / served)
    return profile_common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def notes(raw, scope, label):
    """Always-shown scope lines: who is counted, and how to compare two shares."""
    del scope, label
    count = lambda members: "too many" if members is None else len(members)  # noqa: E731
    return [
        f"{count(raw['generators'])} non-renewable Generators and {count(raw['loads'])} Loads counted; "
        f"{raw['renewable_generators']} renewable Generators supply the rest.",
        "The share is in percent; the difference between two shares is in percentage points.",
    ]
