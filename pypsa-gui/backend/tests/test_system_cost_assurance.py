"""system-cost-breakdown/1 (assistant-assurance 10, R3; A2.9, A4.6).

A hand LP with every component class priced, solved the way the app solves
(`with_periodized_cost_defaults` around `n.optimize`), so the LP objective and
`/results/cost_breakdown` both carry the solve's own cost coefficients. The
expected figures are literals worked out below, never read from the services
under test.

Snapshots: two, objective weights 1 and 2, so nyears = 3 / 8760. A typed annual
capital_cost or fom_cost is scaled by nyears, so 2920 EUR/MW/yr is 1 EUR/MW per
horizon; the annual inputs are chosen as multiples of 2920.

Dispatch, by hand (the LP's unique optimum):
- Bus C load 10, 20 MW; StorageUnit SU (2 MWh) discharges 2 MWh / 2 h = 1 MW at
  t1, where it trims the extendable Line and Transformer, so 0 and 1 MW.
- Line L and Transformer T carry 10, 19 MW, so both are sized 19 MW.
- Bus D load 0, 3 MW; Store S (1 MWh) gives 0.5 MW at t1; Link K gives 0, 2.5 MW.
- Wind is built to its 4 MW cap with p_max_pu 1, 0.5: 4, 2 MW.
- Gas fills the rest at bus A: 10 - 4 = 6 MW, 19 + 2.5 - 2 = 19.5 MW.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import math

import pandas as pd
import pypsa
import pytest

from harness import assurance_scope, calculations
from harness import system_cost_calculations as system_cost
from services import assurance_records as records
from services import workflow_tools
from services.solver.periodized_costs import _annuity, with_periodized_cost_defaults
from tests.test_workflow_tools import baseline  # noqa: F401

WEIGHTS = [1.0, 2.0]
STAMPS = pd.date_range("2026-01-01", periods=2, freq="h")
# CRF(5 %, 20 y) = 0.05 x 1.05^20 / (1.05^20 - 1), 1.05^20 = 2.653297705...
CRF = 0.05 * 1.05**20 / (1.05**20 - 1)
# Wind: overnight 8760 EUR/MW, so 8760 x CRF x 3/8760 = 3 CRF per MW; 4 MW built.
WIND_CAPEX = 4 * 3 * CRF  # 0.96291104...
# Per-horizon capex, coefficient x optimal capacity:
#   gas 5 x 50 = 250 (not extendable), wind 3 CRF x 4, Line 0.5 x 19 = 9.5,
#   Transformer 1 x 19 = 19, Link (2 + fom 1) x 2.5 = 7.5,
#   Store 0.3 x 1 = 0.3 (not extendable), StorageUnit 3 x 2 = 6 (not extendable).
# Opex, Σ w x marginal x dispatch:
#   gas 20 x (1 x 6 + 2 x 19.5) = 900, Link 1 x 2 x 2.5 = 5,
#   Store 0.1 x 2 x 0.5 = 0.1, StorageUnit 0.5 x 2 x 1 = 1.
CARRIERS = {
    "gas": (250.0, 900.0),
    "wind": (WIND_CAPEX, 0.0),
    "AC": (9.5 + 19.0, 0.0),
    "heat": (7.5, 5.0),
    "h2": (0.3, 0.1),
    "battery": (6.0, 1.0),
}
CAPEX = 250 + WIND_CAPEX + 9.5 + 19 + 7.5 + 0.3 + 6  # 293.26291104...
OPEX = 900 + 5 + 0.1 + 1  # 906.1
# The objective holds the extendable capex and all opex:
# 3 CRF x 4 + 9.5 + 19 + 7.5 + 906.1 = 943.06291104...
OBJECTIVE = WIND_CAPEX + 9.5 + 19 + 7.5 + OPEX
CLASSES = ("Generator", "Link", "StorageUnit", "Store", "Line", "Transformer")


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", system_cost.PROFILE)


def _network(**gas):
    n = pypsa.Network()
    n.set_snapshots(STAMPS)
    for column in ("objective", "stores", "generators"):
        n.snapshot_weightings[column] = WEIGHTS
    n.add("Carrier", ["gas", "wind", "AC", "heat", "h2", "battery"])
    n.add("Bus", ["A", "B"], v_nom=110, carrier="AC")
    n.add("Bus", "C", v_nom=20, carrier="AC")
    n.add("Bus", "D", carrier="heat")
    n.add("Generator", "gas", bus="A", carrier="gas", p_nom=50, marginal_cost=20,
          capital_cost=14600, **gas)
    n.add("Generator", "wind", bus="A", carrier="wind", p_nom_extendable=True, p_nom_max=4,
          p_max_pu=[1.0, 0.5], overnight_cost=8760, discount_rate=0.05, lifetime=20)
    n.add("Line", "L", bus0="A", bus1="B", x=0.1, s_nom_extendable=True, capital_cost=1460,
          carrier="AC")
    n.add("Transformer", "T", bus0="B", bus1="C", x=0.1, s_nom_extendable=True,
          capital_cost=2920, carrier="AC")
    n.add("Load", "dC", bus="C", p_set=[10.0, 20.0])
    n.add("Link", "K", bus0="A", bus1="D", p_nom_extendable=True, capital_cost=5840,
          fom_cost=2920, marginal_cost=1, carrier="heat")
    n.add("Load", "dD", bus="D", p_set=[0.0, 3.0])
    n.add("Store", "S", bus="D", e_nom=1, e_initial=1, capital_cost=876, marginal_cost=0.1,
          carrier="h2")
    n.add("StorageUnit", "SU", bus="C", p_nom=2, max_hours=1, state_of_charge_initial=2,
          capital_cost=8760, marginal_cost=0.5, carrier="battery")
    return n


def solve_costed(ctx):
    """Solve as the app does: the periodized-cost fill around the LP, then reverted."""
    n = ctx.network
    cfg = ctx.solver_state["solver_config"]
    with with_periodized_cost_defaults(n, cfg):
        assert n.optimize(solver_name="highs", include_objective_constant=False)[1] == "optimal"
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, cfg)
    return ctx


@pytest.fixture
def hand_cost(baseline):
    """The hand network, unsolved: `solve_costed` solves it."""
    ctx = baseline[0]
    ctx.network = _network()
    return ctx


@pytest.fixture
def cost_case(baseline):
    """`cost_case(**gas)`: a fresh hand network, solved; keyword args go to the gas Generator."""
    ctx = baseline[0]

    def make(**gas):
        ctx.network = _network(**gas)
        return solve_costed(ctx)

    return make


def _scope(ctx, entity, window=None):
    scope = {"version": 1, "profile": system_cost.PROFILE, "project_id": ctx.project_uuid,
             "entity_id": entity, "retain": False}
    if window:
        scope.update(start=window[0], end=window[1])
    return scope


def _checked(ctx, entity, window=None):
    record = records.capture_scoped(records.actor_id(), _scope(ctx, entity, window))
    values = {f["metric"]: f["value"] for f in record["facts"]}
    reasons = {s["metric"]: s["reason"] for s in record["slots"] if s["disposition"] == "unavailable"}
    return values, reasons


def _raw(ctx, entity="network"):
    return calculations.scoped_source(ctx, {"profile": system_cost.PROFILE, "entity_id": entity})


def _reasons(raw, entity="network"):
    _, slots = system_cost.calculate(raw, {"profile": system_cost.PROFILE, "entity_id": entity})
    return {s["metric"]: s.get("reason") for s in slots}


def test_network_figures_equal_the_hand_arithmetic(cost_case):
    values, reasons = _checked(cost_case(), "network")
    assert reasons == {}
    assert values["system_capex"] == pytest.approx(CAPEX, rel=1e-12)
    assert values["system_opex"] == pytest.approx(OPEX, rel=1e-9)
    assert values["system_cost"] == pytest.approx(CAPEX + OPEX, rel=1e-9)
    assert abs(values["objective_residual"]) <= system_cost.RESIDUAL_TOLERANCE * OBJECTIVE


def test_the_carriers_sum_to_the_network_and_each_is_its_own_arithmetic(cost_case):
    ctx = cost_case()
    total = []
    for carrier, (capex, opex) in CARRIERS.items():
        values, reasons = _checked(ctx, carrier)
        assert reasons == {"objective_residual": "network_scope_required"}
        assert values["system_capex"] == pytest.approx(capex, rel=1e-12, abs=1e-12)
        assert values["system_opex"] == pytest.approx(opex, rel=1e-9, abs=1e-9)
        total.append(values["system_cost"])
    assert math.fsum(total) == pytest.approx(_checked(ctx, "network")[0]["system_cost"], rel=1e-12)


def test_the_objective_is_the_hand_lp_basis(cost_case):
    """Extendable capex plus all opex is the LP objective; non-extendable capex is not in it."""
    assert cost_case().network.objective == pytest.approx(OBJECTIVE, rel=1e-9)


def test_the_annuity_is_the_hand_number_on_both_routes(cost_case, client):
    assert CRF == pytest.approx(0.0802425872, rel=1e-9)
    assert system_cost.annuity(0.05, 20) == pytest.approx(CRF, rel=1e-15)
    assert _annuity(0.05, 20) == pytest.approx(CRF, rel=1e-15)
    assert system_cost.annuity(0.0, 20) == 1 / 20
    ctx = cost_case()
    assert _checked(ctx, "wind")[0]["system_capex"] == pytest.approx(WIND_CAPEX, rel=1e-12)
    route = client.get("/api/results/cost_breakdown").json()
    (wind,) = [r for r in route["by_carrier"] if r["carrier"] == "wind"]
    assert wind["capex"] == round(WIND_CAPEX, 5)


def _by_carrier(route):
    out = {}
    for row in route["by_carrier"]:
        capex, opex = out.get(row["carrier"], (0.0, 0.0))
        out[row["carrier"]] = (capex + row["capex"], opex + row["opex"])
    return {k: v for k, v in out.items() if v != (0.0, 0.0)}


def _disagreements(route, ctx):
    """(carrier, metric) pairs where the route and the checked answer differ at the route's 5 dp."""
    out = set()
    routed = _by_carrier(route)
    for carrier in CARRIERS:
        values = _checked(ctx, carrier)[0]
        capex, opex = routed.get(carrier.casefold(), (None, None))
        for metric, theirs in (("system_capex", capex), ("system_opex", opex),
                               ("system_cost", None if capex is None else capex + opex)):
            if theirs is None or round(theirs, 5) != round(values[metric], 5):
                out.add((carrier, metric))
    return out


def test_the_route_shows_the_same_figures(cost_case, client):
    """A4.6: Σ by_carrier is the route's total, and every carrier is the checked figure."""
    ctx = cost_case()
    route = client.get("/api/results/cost_breakdown").json()
    assert math.fsum(r["total"] for r in route["by_carrier"]) == pytest.approx(route["total"], rel=1e-9)
    assert route["total"] == round(CAPEX + OPEX, 5)
    assert _disagreements(route, ctx) == set()


def test_an_injected_plus_one_breaks_exactly_the_pairs_it_feeds(cost_case, client, monkeypatch):
    """+1 EUR on one carrier's route opex: exactly its opex and total disagree; the
    checked answer does not read the service, so it does not move."""
    from routers import results

    ctx = cost_case()
    real = results.compute_cost_breakdown

    def wrong(*args, **kwargs):
        out = real(*args, **kwargs)
        for row in out["by_carrier"]:
            if row["carrier"] == "heat":
                row["opex"] += 1.0
                row["total"] += 1.0
        return out

    monkeypatch.setattr(results, "compute_cost_breakdown", wrong)
    route = client.get("/api/results/cost_breakdown").json()
    assert _disagreements(route, ctx) == {("heat", "system_opex"), ("heat", "system_cost")}
    assert _checked(ctx, "heat")[0]["system_opex"] == pytest.approx(5.0, rel=1e-9)


@pytest.mark.parametrize("carrier", sorted(CARRIERS))
def test_dropping_a_carrier_breaks_the_identity(cost_case, carrier):
    raw = _raw(cost_case())
    assert set(_reasons(raw).values()) == {None}
    raw["assets"] = [a for a in raw["assets"] if a["carrier"] != carrier]
    # Every carrier has a term in the objective (the smallest, h2's opex, is 0.1 EUR).
    assert set(_reasons(raw).values()) == {"objective_mismatch"}


@pytest.mark.parametrize("component", CLASSES)
def test_dropping_a_class_from_the_basis_breaks_the_identity(cost_case, component):
    raw = _raw(cost_case())
    raw["assets"] = [a for a in raw["assets"] if a["class"] != component]
    assert set(_reasons(raw).values()) == {"objective_mismatch"}


def test_the_residual_is_the_objective_minus_the_hand_basis(cost_case):
    """An objective 0.0005 EUR above the hand basis (inside 1e-6 x 943) leaves 0.0005 EUR."""
    ctx = cost_case()
    ctx.network._objective = OBJECTIVE + 0.0005
    values, reasons = _checked(ctx, "network")
    assert reasons == {}
    assert values["objective_residual"] == pytest.approx(0.0005, abs=1e-9)


@pytest.mark.parametrize(("factor", "answered"), [(1.0, True), (2.0, False)])
def test_the_residual_tolerance_boundary(cost_case, factor, answered):
    """At RESIDUAL_TOLERANCE x max(1, |objective|) it answers; at twice that it refuses."""
    raw = _raw(cost_case())
    values = {f["metric"]: f["value"] for f in system_cost.calculate(
        raw, {"profile": system_cost.PROFILE, "entity_id": "network"})[0]}
    basis = raw["objective"] - values["objective_residual"]
    tol = system_cost.RESIDUAL_TOLERANCE
    # Solve objective = basis (1 + f tol) so the residual is exactly f tol |objective|,
    # with a hair of slack below the boundary when it must pass.
    raw["objective"] = basis / (1 - factor * tol * (1 - 1e-9 if answered else 1))
    reasons = set(_reasons(raw).values())
    assert reasons == ({None} if answered else {"objective_mismatch"})


def test_the_tolerance_scales_with_one_below_a_unit_objective(cost_case):
    """max(1, |objective|): a tiny objective is judged against an absolute 1e-6 EUR."""
    raw = _raw(cost_case())
    for asset in raw["assets"]:
        asset["capital_cost"] = asset["fom_cost"] = 0.0
        asset["overnight_cost"] = None
        if asset["marginal"] is not None:
            asset["marginal"] = [0.0] * len(asset["marginal"])
    raw["objective"] = 0.9 * system_cost.RESIDUAL_TOLERANCE
    assert set(_reasons(raw).values()) == {None}
    raw["objective"] = 2 * system_cost.RESIDUAL_TOLERANCE
    assert set(_reasons(raw).values()) == {"objective_mismatch"}


def _reach(cost_case, monkeypatch, name):
    """One fixture per reason; returns the reasons a checked capture gives."""
    entity, window = "network", None
    if name == "mip_solve":
        ctx = cost_case(committable=True)
    elif name == "unmodelled_cost_terms":
        ctx = cost_case(start_up_cost=1.0)
    elif name == "annuity_inputs_missing":
        ctx = cost_case()
        ctx.network.generators.loc["wind", "discount_rate"] = math.nan
        workflow_tools._record_solve_inputs(ctx.network, ctx.solver_state["solver_config"])
    else:
        ctx = cost_case()
    n = ctx.network
    if name == "unverified_solve":
        n.add("Load", "late", bus="B", p_set=1)
    elif name == "objective_not_recorded":
        n._objective = math.nan
    elif name == "objective_scaled":
        n._pypsa_gui_objective_scale = 2.0
    elif name == "objective_constant":
        n._objective_constant = 5.0
    elif name == "extra_objective_terms":
        monkeypatch.setattr("services.solver.reserve_products.objective_revenue", lambda n: 3.0)
    elif name == "invalid_or_missing_raw_data":
        n.generators_t.p.iloc[0, 0] = math.nan
    elif name == "objective_mismatch":
        n._objective += 1.0
    elif name == "whole_horizon_required":
        window = (STAMPS[0].isoformat(), STAMPS[0].isoformat())
    elif name == "network_scope_required":
        entity = "gas"
    return set(_checked(ctx, entity, window)[1].values())


@pytest.mark.parametrize("name", sorted(system_cost.REASONS))
def test_each_reason_has_its_own_fixture(cost_case, monkeypatch, name):
    reasons = _reach(cost_case, monkeypatch, name)
    assert reasons == {name}


def test_every_slot_reason_is_declared_and_reached(cost_case, monkeypatch):
    """Exact set equality: a reason given but not declared, or declared but never reached, fails."""
    seen = set()
    for name in sorted(system_cost.REASONS):
        seen |= _reach(cost_case, monkeypatch, name)
        monkeypatch.undo()
        monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", system_cost.PROFILE)
    assert seen == system_cost.REASONS


def test_an_extendable_asset_with_existing_capacity_is_an_objective_constant(cost_case):
    raw = _raw(cost_case())
    (line,) = [a for a in raw["assets"] if a["name"] == "L"]
    line["nom"] = 1.0
    assert set(_reasons(raw).values()) == {"objective_constant"}


@pytest.mark.parametrize(
    ("entity", "change", "kind"),
    [
        ("nowhere", None, "unknown_carrier"),
        ("Gas", None, "unknown_carrier"),
        ("gas", "twin", "ambiguous_carrier"),
        ("network", "multi", "unsupported_snapshot_scope"),
        ("network", "cap", "unsupported_carrier_population"),
    ],
)
def test_refusals_are_named(cost_case, monkeypatch, entity, change, kind):
    ctx = cost_case()
    if change == "twin":
        ctx.network.add("Generator", "gas2", bus="A", carrier="Gas", p_nom=1)
    elif change == "multi":
        ctx.network.investment_periods = [2030]
    elif change == "cap":
        monkeypatch.setattr(system_cost, "MAX_ASSETS", 3)
    with pytest.raises(ValueError) as error:
        _raw(ctx, entity)
    assert error.value.args[0] == kind
    assert kind in system_cost.REFUSALS


def test_a_record_of_another_entity_is_rejected(cost_case):
    raw = _raw(cost_case(), "gas")
    with pytest.raises(ValueError, match="system_cost_source_identity"):
        system_cost.calculate(raw, {"profile": system_cost.PROFILE, "entity_id": "wind"})


def test_grammar():
    scope = assurance_scope.from_message("Show the system cost for network.", "p1", {})
    assert scope["profile"] == system_cost.PROFILE and scope["entity_id"] == "network"
    carrier = assurance_scope.from_message("show system cost for carrier gas", "p1", {})
    assert carrier["profile"] == system_cost.PROFILE and carrier["entity_id"] == "gas"
