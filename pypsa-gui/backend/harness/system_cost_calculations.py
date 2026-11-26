"""Checked system cost by carrier, tied to the LP objective (assistant-assurance 10, R3).

The figures follow `/results/cost_breakdown` (`services/results/cost_breakdown.py`):
an asset's capex is its per-horizon cost coefficient times its optimal capacity,
its opex the objective-weighted marginal cost of its dispatch, and a carrier's
cost the sum over every Generator, Link, StorageUnit, Store, Line and
Transformer that carries it. They are computed here from the component tables
and snapshot weightings alone, never through that service or through
`services/results/objective_decomposition.py`; those are what the tests check.

The per-horizon coefficient is the solve's own: a directly typed `capital_cost`
and `fom_cost` are annual and scaled by nyears (Σ objective weights / 8760, as
`services/solver/periodized_costs.fill_periodized_cost_defaults` does for the
solve); an `overnight_cost` is annuitised, overnight × CRF(rate, lifetime) ×
nyears, as `pypsa.costs.periodized_cost` does.

Every figure is published only when the hand basis explains the objective: the
extendable capex plus all opex equals `n.objective` within RESIDUAL_TOLERANCE.
Whatever this basis does not model (a scaled objective, an objective constant,
reserve/PCS/commercial terms, a MIP, unmodelled cost attributes) withholds every
figure with a named reason; it never widens the tolerance.
"""

import math

from harness import profile_common

PROFILE = "system-cost-breakdown/1"
# Relative to max(1, |objective|). The hand basis is evaluated on the solver's own
# primal values (capacities and dispatch), so with the objective scale held at 1
# it differs from the reported objective only by floating-point summation order,
# about 1e-13 of the objective over the bounded population (MAX_ASSETS x
# MAX_SNAPSHOTS terms). 1e-9 leaves that several orders of headroom and refuses
# any missing term above a billionth of the objective.
RESIDUAL_TOLERANCE = 1e-9
HOURS_PER_YEAR = 8760.0
MAX_ASSETS = 128
# The component class, its capacity attribute and the dispatch its marginal cost multiplies.
CLASSES = (
    ("Generator", "p_nom", "p"),
    ("Link", "p_nom", "p0"),
    ("StorageUnit", "p_nom", "p_dispatch"),
    ("Store", "e_nom", "p"),
    ("Line", "s_nom", None),
    ("Transformer", "s_nom", None),
)
# Objective terms this basis does not model; any nonzero value withholds every figure.
UNMODELLED = (
    "marginal_cost_quadratic",
    "marginal_cost_storage",
    "spill_cost",
    "stand_by_cost",
    "start_up_cost",
    "shut_down_cost",
    # The app's curtailment penalty (services/solver/objective.py), a Generator column.
    "curtailment_cost",
)
REFUSALS = frozenset(
    {
        "unsupported_snapshot_scope",
        "unsupported_carrier_population",
        "ambiguous_carrier",
        "unknown_carrier",
    }
)
# Every reason a slot can carry; pinned by exact set equality in the tests.
REASONS = frozenset(
    {
        "unverified_solve",
        "objective_not_recorded",
        "objective_scaled",
        "objective_constant",
        "extra_objective_terms",
        "mip_solve",
        "unmodelled_cost_terms",
        "annuity_inputs_missing",
        "invalid_or_missing_raw_data",
        "objective_mismatch",
        "whole_horizon_required",
        "network_scope_required",
    }
)
METRICS = {
    "system_capex": {
        "formula_id": "system-capex-by-carrier/1",
        "unit": "EUR",
        "label": "Capital cost (per modelled horizon)",
    },
    "system_opex": {
        "formula_id": "system-opex-by-carrier/1",
        "unit": "EUR",
        "label": "Operating cost",
    },
    "system_cost": {
        "formula_id": "system-cost-by-carrier/1",
        "unit": "EUR",
        "label": "System cost (capital plus operating)",
    },
    "objective_residual": {
        "formula_id": "objective-minus-hand-lp-basis/1",
        "unit": "EUR",
        "label": "LP objective minus the hand cost basis",
    },
}
NETWORK_ONLY = frozenset({"objective_residual"})
DISCLOSURE = (
    "System cost is capex (cost coefficient per modelled horizon times optimal capacity) plus "
    "objective-weighted opex, summed per carrier over Generators, Links, StorageUnits, Stores, Lines and "
    "Transformers. It is published only when the extendable capex plus all opex reproduces the LP "
    "objective; non-extendable capex is reported but is not in the objective. Whole horizon only. "
    "An extendable asset with existing capacity, an overnight cost without its own discount rate and "
    "finite lifetime, a composite asset, a curtailment penalty or a scaled objective withholds every figure."
)


def annuity(rate, lifetime):
    """CRF: r(1+r)^L / ((1+r)^L - 1), and 1/L at r = 0 (written here, not imported)."""
    if rate == 0:
        return 1.0 / lifetime
    growth = (1.0 + rate) ** lifetime
    return rate * growth / (growth - 1.0)


def _nonzero(component, attr, names):
    """True when the static or time-varying attribute is nonzero (or non-finite) on any member."""
    values = []
    if attr in component.static:
        values += list(component.static.loc[names, attr])
    frame = component.dynamic.get(attr)
    if frame is not None and len(frame.columns):
        cols = [c for c in frame.columns if c in names]
        values += [v for c in cols for v in frame[c]]
    return any(not (isinstance(v, (int, float)) and v == 0) for v in values)


def _carriers(n):
    seen = set()
    for component, *_ in CLASSES:
        static = n.components[component].static
        if "carrier" in static:
            seen.update(str(c) for c in static["carrier"])
    return seen


def source(ctx, entity):
    """Called under the network lock; every asset is captured, the entity filter comes later."""
    from harness.calculations import _number
    from services.asset_schema.derive import has_parts

    n = ctx.network
    index = profile_common.single_period_index(n)
    if getattr(n, "has_scenarios", False):
        # Stochastic objectives weight each scenario by its probability; not modelled here.
        raise ValueError("unsupported_snapshot_scope")
    if entity != "network":
        carriers = _carriers(n)
        variants = sorted(
            c for c in carriers if c != entity and c.casefold() == entity.casefold()
        )
        if variants and entity in carriers:
            raise ValueError("ambiguous_carrier")
        if entity not in carriers:
            raise ValueError("unknown_carrier", *variants[:1])
    weights = n.snapshot_weightings["objective"]
    assets, unmodelled, mip = [], False, False
    for component_class, nom, dispatch in CLASSES:
        component = n.components[component_class]
        static = component.static
        names = list(static.index)
        if not names:
            continue
        unmodelled = unmodelled or any(
            _nonzero(component, a, names) for a in UNMODELLED
        )
        if "active" in static and not static["active"].all():
            unmodelled = True
        if any(has_parts(component_class, static.loc[name]) for name in names):
            # Composite rows are re-derived from their parts at solve time; this basis reads the columns.
            unmodelled = True
        if "committable" in static and static["committable"].any():
            mip = True
        extendable = static[f"{nom}_extendable"]
        mod = static.get(f"{nom}_mod")
        if mod is not None and ((mod > 0) & extendable).any():
            mip = True
        if dispatch is not None:
            marginal = n.get_switchable_as_dense(component_class, "marginal_cost")
            flow = component.dynamic.get(dispatch)
        for name in names:
            row = static.loc[name]
            overnight = row.get("overnight_cost")
            asset = {
                "class": component_class,
                "name": str(name),
                "carrier": str(row.get("carrier", "")),
                "extendable": bool(extendable[name]),
                "nom": _number(row[nom]),
                "nom_opt": _number(row[f"{nom}_opt"]),
                "capital_cost": _number(row.get("capital_cost", 0.0)),
                "fom_cost": _number(row.get("fom_cost", 0.0)),
                "overnight_cost": None
                if overnight is None or overnight != overnight
                else _number(overnight),
                "discount_rate": _number(row.get("discount_rate")),
                "lifetime": _number(row.get("lifetime")),
                "marginal": None,
                "dispatch": None,
            }
            if dispatch is not None:
                asset["marginal"] = [_number(v) for v in marginal[name].reindex(index)]
                asset["dispatch"] = (
                    [_number(v) for v in flow[name].reindex(index)]
                    if flow is not None and name in flow
                    else None
                )
            assets.append(asset)
    if len(assets) > MAX_ASSETS:
        raise ValueError("unsupported_carrier_population")
    objective = getattr(n, "objective", None)
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "objective_weight": [_number(v) for v in weights.reindex(index)],
        "objective": None if objective is None else _number(objective),
        "objective_constant": _number(getattr(n, "objective_constant", 0.0) or 0.0),
        "objective_scale": _objective_scale(ctx),
        "extra_objective_terms": _extra_terms(ctx),
        "mip": mip,
        "unmodelled_cost_terms": unmodelled,
        "assets": assets,
        "valid_solve": profile_common.lp_outputs_live(ctx),
    }


def _objective_scale(ctx):
    """The scale the solve applied: the live sentinel before it is reverted, else the recorded config.

    The solver deletes `_pypsa_gui_objective_scale` after rescaling, so the config the solve
    ran with is what persists; an automatic scale counts as unknown (None).
    """
    from harness.calculations import _number

    live = getattr(ctx.network, "_pypsa_gui_objective_scale", None)
    if live is not None:
        return _number(live)
    cfg = (ctx.solver_state or {}).get("solver_config")
    if getattr(cfg, "auto_objective_scale", False):
        return None
    return _number(getattr(cfg, "user_objective_scale", 1.0))


def _extra_terms(ctx):
    """Objective terms in no component: PCS/STATCOM sizes, reserve revenue, committed commercial terms."""
    from services.commercial.cost_rows import commercial_cost_terms
    from services.solver.pcs_statcom import cost_terms
    from services.solver.reserve_products import objective_revenue

    n = ctx.network
    cfg = (ctx.solver_state or {}).get("solver_config")
    commercial = commercial_cost_terms(n, getattr(cfg, "commercial", None))
    return bool(
        any(v for v in cost_terms(n).values())
        or objective_revenue(n)
        or commercial.get("items")
    )


def _coefficient(asset, nyears):
    """The per-horizon cost per unit of capacity, or None when an input is missing."""
    fom = asset["fom_cost"] or 0.0
    if asset["overnight_cost"] is not None:
        rate, life = asset["discount_rate"], asset["lifetime"]
        if rate is None or life is None or not life > 0 or rate < 0:
            return None
        base = asset["overnight_cost"] * annuity(rate, life) * nyears
    else:
        if asset["capital_cost"] is None:
            return None
        base = asset["capital_cost"] * nyears
    return base + fom * nyears


def _guard(raw, weights):
    """The first reason the hand basis cannot stand for the objective, or None."""
    if not raw["valid_solve"] or not profile_common.finite(weights) or min(weights) < 0:
        return "unverified_solve"
    if (
        raw["objective"] is None
    ):  # `_number` captures a NaN or infinite objective as None
        return "objective_not_recorded"
    if raw["objective_scale"] != 1.0:
        return "objective_scaled"
    if raw["objective_constant"] != 0.0:
        return "objective_constant"
    if raw["extra_objective_terms"]:
        return "extra_objective_terms"
    if raw["mip"]:
        return "mip_solve"
    if raw["unmodelled_cost_terms"]:
        return "unmodelled_cost_terms"
    return None


def calculate(raw, scope):
    """Every figure, or every slot unavailable with the first reason the basis fails."""
    if (
        raw.get("version") != 1
        or raw.get("profile") != PROFILE
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("system_cost_source_identity")
    a, b = profile_common.window(raw["index"], scope)
    weights = raw["objective_weight"]
    available = {}
    reason = None if (a, b) == (0, len(weights) - 1) else "whole_horizon_required"
    reason = reason or _guard(raw, weights)
    if reason is None:
        nyears = math.fsum(weights) / HOURS_PER_YEAR
        rows = []
        for asset in raw["assets"]:
            coefficient = _coefficient(asset, nyears)
            if coefficient is None:
                reason = "annuity_inputs_missing"
                break
            values = [asset["nom"], asset["nom_opt"], coefficient]
            if asset["marginal"] is not None:
                if asset["dispatch"] is None:
                    reason = "invalid_or_missing_raw_data"
                    break
                values += asset["marginal"] + asset["dispatch"]
            if not profile_common.finite(values):
                reason = "invalid_or_missing_raw_data"
                break
            opex = 0.0
            if asset["marginal"] is not None:
                opex = math.fsum(
                    w * m * d
                    for w, m, d in zip(weights, asset["marginal"], asset["dispatch"])
                )
            rows.append((asset, coefficient, coefficient * asset["nom_opt"], opex))
    if reason is None and any(
        asset["extendable"] and coefficient * asset["nom"] != 0
        for asset, coefficient, *_ in rows
    ):
        # PyPSA books an extendable asset's existing capacity as the objective constant.
        reason = "objective_constant"
    if reason is None:
        lp_basis = math.fsum(
            [capex for asset, _, capex, _ in rows if asset["extendable"]]
            + [opex for *_, opex in rows]
        )
        residual = raw["objective"] - lp_basis
        if not abs(residual) <= RESIDUAL_TOLERANCE * max(1.0, abs(raw["objective"])):
            reason = "objective_mismatch"
    reasons = {}
    if reason is not None:
        reasons = dict.fromkeys(METRICS, reason)
    else:
        network = raw["entity_id"] == "network"
        mine = [r for r in rows if network or r[0]["carrier"] == raw["entity_id"]]
        available["system_capex"] = math.fsum(capex for _, _, capex, _ in mine)
        available["system_opex"] = math.fsum(opex for *_, opex in mine)
        available["system_cost"] = math.fsum(capex + opex for _, _, capex, opex in mine)
        if network:
            available["objective_residual"] = residual
        else:
            reasons["objective_residual"] = "network_scope_required"
    return profile_common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def notes(raw, scope, label):
    """Always-shown scope lines: the population and the objective identity it stands on."""
    del scope, label
    count = len(raw["assets"])
    carriers = sorted({a["carrier"] for a in raw["assets"]})
    return [
        f"{count} assets over {len(carriers)} carriers; figures cover the whole solved horizon.",
        "Checked against the LP objective: extendable capex plus opex must reproduce it "
        f"within {RESIDUAL_TOLERANCE:g} of its size.",
    ]
