"""Bounded generator CO2 emissions for checked answers (checked-results S5).

Emissions are Σ_t w(t)·p(t)/η(t)·co2(carrier) with generator snapshot weights
and the time-varying efficiency (`get_switchable_as_dense`). A carrier with
no row in `n.carriers` emits nothing in the LP and counts as zero, disclosed.
The network total refuses when CO2 also flows through Links, StorageUnits or
Stores, which this profile does not count. The cap's shadow price is the
observed dual of a `co2_emissions` global constraint, reported as −mu.
"""

import math

from harness import profile_common as common

GENERATOR_PROFILE = "generator-emissions/1"
NETWORK_PROFILE = "network-emissions/1"
PROFILES = (GENERATOR_PROFILE, NETWORK_PROFILE)
MAX_GENERATORS = 64
REFUSALS = frozenset({
    "unknown_generator", "emissions_outside_scope", "unsupported_generator_population",
    "unsupported_snapshot_scope", "misaligned_source", "network_scope_required",
})
GENERATOR_METRICS = {
    "generator_emissions": {
        "formula_id": "generator-co2-emissions/1",
        "unit": "tCO2",
        "label": "CO2 emissions of this generator",
    },
}
NETWORK_METRICS = {
    "total_emissions": {
        "formula_id": "network-generator-co2-emissions/1",
        "unit": "tCO2",
        "label": "CO2 emissions of all generators",
    },
    "co2_cap_shadow_price": {
        "formula_id": "co2-cap-shadow-price/1",
        "unit": "EUR/tCO2",
        "label": "CO2 cap shadow price",
    },
}
DISCLOSURE = (
    "Generator CO2 only: dispatch over the time-varying efficiency times the carrier's co2_emissions, "
    "with generator snapshot weights. The shadow price is the observed dual of the CO2 cap, sign-flipped "
    "to a positive EUR/tCO2."
)


def _co2_elsewhere(n):
    """Links feeding a CO2-carrier bus, and storage whose carrier emits."""
    carriers = n.carriers
    emitting = set()
    if "co2_emissions" in carriers.columns:
        emitting = {c for c, v in carriers["co2_emissions"].items() if isinstance(v, (int, float)) and v != 0}
    co2_buses = {b for b, c in n.buses["carrier"].items() if isinstance(c, str) and "co2" in c.lower()}
    links = n.links
    feeding = [
        name for name in links.index
        if any(str(links.at[name, col]) in co2_buses for col in links.columns if col.startswith("bus"))
    ]
    storage = [
        f"{cls}:{name}" for cls, df in (("StorageUnit", n.storage_units), ("Store", n.stores))
        for name in df.index if df.at[name, "carrier"] in emitting
    ]
    return len(feeding) + len(storage)


def _generator(n, name, index, efficiency):
    from harness.calculations import _number

    carrier = n.generators.at[name, "carrier"]
    defined = carrier in n.carriers.index
    co2 = _number(n.carriers.at[carrier, "co2_emissions"]) if defined and "co2_emissions" in n.carriers.columns else 0.0
    p = n.generators_t.p
    return {
        "co2": co2,
        "defined": defined,
        "p": [_number(v) for v in p[name]] if name in p.columns else [None] * len(index),
        "efficiency": [_number(v) for v in efficiency[name]],
    }


def source(ctx, entity, network):
    from harness.calculations import _number
    from services import period_utils

    n = ctx.network
    index = common.single_period_index(n)
    if network != (entity == "network"):
        raise ValueError("network_scope_required")
    elsewhere = _co2_elsewhere(n)
    if network:
        if elsewhere:
            raise ValueError("emissions_outside_scope")
        # As PyPSA's primary-energy constraint: inactive generators do not count.
        active = n.generators["active"] if "active" in n.generators.columns else None
        names = [str(g) for g in n.generators.index if active is None or bool(active[g])]
        if len(names) > MAX_GENERATORS:
            raise ValueError("unsupported_generator_population")
    elif entity in n.generators.index:
        names = [entity]
    else:
        raise ValueError("unknown_generator")
    if not n.generators_t.p.index.equals(index):
        raise ValueError("misaligned_source")
    efficiency = n.get_switchable_as_dense("Generator", "efficiency")
    mu, cap = None, False
    gc = n.global_constraints
    if network and not gc.empty and "carrier_attribute" in gc.columns:
        caps = gc.index[(gc["carrier_attribute"].astype(str) == "co2_emissions")
                        & (gc["type"].astype(str) == "primary_energy")]
        if len(caps) == 1:
            cap, mu = True, _number(gc.at[caps[0], "mu"]) if "mu" in gc.columns else None
        elif len(caps) > 1:
            cap = "several"
    config = ctx.solver_state.get("solver_config")
    return {
        "version": 1,
        "profile": NETWORK_PROFILE if network else GENERATOR_PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "weight": [_number(v) for v in period_utils.snapshot_weights(n, "generators").reindex(index)],
        "generators": {name: _generator(n, name, index, efficiency) for name in names},
        "co2_elsewhere": elsewhere,
        "cap": cap,
        "mu": mu,
        "co2_price": _number(getattr(config, "co2_price", 0.0)),
        "valid_solve": common.lp_outputs_live(ctx),
    }


def _emissions(unit, weight, a, b):
    if unit["co2"] is None:
        return None
    if unit["co2"] == 0:
        return 0.0 if common.finite(unit["p"][a : b + 1]) else None
    total = []
    for i in range(a, b + 1):
        p, eta, w = unit["p"][i], unit["efficiency"][i], weight[i]
        if not common.finite([p, eta, w]) or eta <= 0 or w < 0:
            return None
        total.append(w * p / eta * unit["co2"])
    return math.fsum(total)


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != scope["profile"] or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("emissions_source_identity")
    a, b = common.window(raw["index"], scope)
    network = scope["profile"] == NETWORK_PROFILE
    base = "unverified_solve" if not raw["valid_solve"] else "invalid_or_missing_emissions_data"
    available, reasons = {}, {}
    values = [_emissions(u, raw["weight"], a, b) for u in raw["generators"].values()] if raw["valid_solve"] else [None]
    if all(v is not None for v in values):
        available["total_emissions" if network else "generator_emissions"] = math.fsum(values)
    if network:
        if raw["cap"] is True and raw["mu"] is not None and raw["valid_solve"]:
            # A slack cap has mu 0; report 0, not a signed -0.
            available["co2_cap_shadow_price"] = -raw["mu"] + 0.0
        else:
            reasons["co2_cap_shadow_price"] = (
                "several_co2_caps" if raw["cap"] == "several" else "no_co2_cap" if not raw["cap"] else base
            )
    definitions = NETWORK_METRICS if network else GENERATOR_METRICS
    reasons = {m: reasons.get(m, base) for m in definitions}
    return common.facts_and_slots(scope, definitions, available, reasons, b - a + 1)


def notes(raw, label):
    lines = []
    undefined = [g for g, u in raw["generators"].items() if not u["defined"]]
    if undefined:
        lines.append(f"{len(undefined)} generators have no carrier entry and count as zero emissions, as in the LP.")
    if raw["entity_id"] != "network" and raw["co2_elsewhere"]:
        lines.append(f"This generator only; {raw['co2_elsewhere']} Links or stores elsewhere carry CO2.")
    if raw["entity_id"] == "network" and raw["valid_solve"] and not raw["cap"] and (raw["co2_price"] or 0) > 0:
        price = repr(raw["co2_price"]).removesuffix(".0")
        lines.append(f"CO2 is priced at {price} EUR/tCO2 via co2_price (not a cap); no shadow price.")
    return lines
