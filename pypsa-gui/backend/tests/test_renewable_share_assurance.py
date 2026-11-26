"""renewable-share/1 (assistant-assurance 10, R4; A2.9, A2.10, A4.6).

A hand LP, solved the way the app solves, read through the checked profile and
`/results/renewable_fraction`. The expected figures are literals worked out
below, never read from the services under test.

Snapshots: three, generator weights 1, 0.5 and 2 (unequal, so a plain mean or
an unweighted sum gives a different number).

Dispatch, by hand (the LP's unique optimum; wind 0, biomass 10, gas 50,
h2 turbine 80 EUR/MWh):
- Load L: 10, 20, 8 MW.
- Wind (12 MW, p_max_pu 1, 0.25, 0.5): 10, 3, 6 MW (curtailed at t0).
- Biomass (3 MW): 0, 3, 2 MW.
- Gas: 0, 14, 0 MW. The h2 turbine never runs.

Non-renewable energy: 0.5 x 14 = 7 MWh. Electricity used: 10 + 0.5 x 20 +
2 x 8 = 36 MWh. Share: 100 x (1 - 7/36) = 80.55...%. With equal weights it
would be 100 x (1 - 14/38) = 63.15...%.
Window t1..t2: 7 MWh over 10 + 16 = 26 MWh, so 100 x (1 - 7/26) = 73.07...%.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pypsa
import pytest

from harness import assurance_scope, calculations, derived
from harness import renewable_share_calculations as share
from harness.assurance import Derived
from services import assurance_records as records
from services import workflow_tools
from services.results import carrier_kpis
from services.solver.renewable_fraction import classify
from tests.test_workflow_tools import baseline  # noqa: F401

WEIGHTS = [1.0, 0.5, 2.0]
STAMPS = pd.date_range("2026-01-01", periods=3, freq="h")
NONREN = 7.0
USED = 36.0
SHARE = 100 * (1 - 7 / 36)
WINDOW_SHARE = 100 * (1 - 7 / 26)
EQUAL_WEIGHT_SHARE = 100 * (1 - 14 / 38)


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", share.PROFILE)


def _network(load=(10.0, 20.0, 8.0)):
    n = pypsa.Network()
    n.set_snapshots(STAMPS)
    for column in ("objective", "stores", "generators"):
        n.snapshot_weightings[column] = WEIGHTS
    n.add("Carrier", ["AC", "onwind", "biomass", "gas", "hydrogen"])
    n.add("Bus", "AC", v_nom=110, carrier="AC")
    n.add("Load", "L", bus="AC", p_set=list(load))
    n.add(
        "Generator",
        "wind",
        bus="AC",
        carrier="onwind",
        p_nom=12,
        p_max_pu=[1.0, 0.25, 0.5],
        marginal_cost=0.0,
    )
    n.add("Generator", "bio", bus="AC", carrier="biomass", p_nom=3, marginal_cost=10.0)
    n.add("Generator", "gas", bus="AC", carrier="gas", p_nom=100, marginal_cost=50.0)
    n.add(
        "Generator",
        "h2 turbine",
        bus="AC",
        carrier="hydrogen",
        p_nom=5,
        marginal_cost=80.0,
    )
    return n


def _solve(ctx):
    n = ctx.network
    assert n.optimize(solver_name="highs")[1] == "optimal"
    ctx.solver_state.update(status="completed", condition="optimal")
    ctx.solver_state.pop("last_lost_load", None)
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


@pytest.fixture
def hand_share(baseline):
    """The hand network, unsolved: `solve_share` solves it."""
    ctx = baseline[0]
    ctx.network = _network()
    return ctx


def solve_share(ctx):
    return _solve(ctx)


@pytest.fixture
def share_case(baseline):
    ctx = baseline[0]

    def make(tweak=None, **kw):
        ctx.network = _network(**kw)
        if tweak is not None:
            tweak(ctx.network)
        return _solve(ctx)

    return make


def _scope(ctx, window=None, entity="network"):
    scope = {
        "version": 1,
        "profile": share.PROFILE,
        "project_id": ctx.project_uuid,
        "entity_id": entity,
        "retain": False,
    }
    if window:
        scope.update(start=window[0], end=window[1])
    return scope


def _checked(ctx, window=None):
    record = records.capture_scoped(records.actor_id(), _scope(ctx, window))
    values = {f["metric"]: (f["value"], f["unit"]) for f in record["facts"]}
    reasons = {
        s["metric"]: s["reason"]
        for s in record["slots"]
        if s["disposition"] == "unavailable"
    }
    return values, reasons


def _raw(ctx):
    return calculations.scoped_source(
        ctx, {"profile": share.PROFILE, "entity_id": "network"}
    )


def _reasons(raw, window=None):
    scope = {"profile": share.PROFILE, "entity_id": "network"}
    if window:
        scope.update(start=window[0], end=window[1])
    _, slots = share.calculate(raw, scope)
    return {s["metric"]: s.get("reason") for s in slots}


# ── classification: the existing one, by name, as an exact set ───────────


def test_the_renewable_keywords_are_the_named_set():
    assert set(carrier_kpis._RENEWABLE_KW) == {
        "wind",
        "solar",
        "ror",
        "hydro",
        "geothermal",
        "wave",
        "tidal",
        "pv",
        "biomass",
        "biogas",
        "run-of-river",
    }


def test_the_hand_network_is_classified_as_exact_sets(share_case):
    cls = classify(share_case().network)
    assert set(cls["nonren_generators"]) == {"gas", "h2 turbine"}
    assert set(cls["renewable_generators"]) == {"wind", "bio"}
    assert set(cls["loads"]) == {"L"}
    assert all(not cls[k] for k in share.WITHHOLDING)


def test_the_profile_names_every_class_the_classification_returns(share_case):
    """A class added to `classify` and not sorted into READ, NOT_COUNTED or WITHHOLDING fails here."""
    cls = classify(share_case().network)
    assert set(cls) == share.READ | share.NOT_COUNTED | share.WITHHOLDING
    assert not (share.READ & share.NOT_COUNTED)
    assert not ((share.READ | share.NOT_COUNTED) & share.WITHHOLDING)


def test_an_unnamed_class_withholds_every_figure(share_case, monkeypatch):
    ctx = share_case()
    from services.solver import renewable_fraction

    real = renewable_fraction.classify
    monkeypatch.setattr(
        renewable_fraction, "classify", lambda n: {**real(n), "new_class": ["x"]}
    )
    assert set(_checked(ctx)[1].values()) == {"unsupported_population"}


# ── oracle: hand arithmetic with unequal weights ─────────────────────────


def test_network_figures_equal_the_hand_arithmetic(share_case):
    values, reasons = _checked(share_case())
    assert reasons == {}
    assert values == {
        "renewable_share": (pytest.approx(SHARE, rel=1e-12), "%"),
        "nonrenewable_energy": (pytest.approx(NONREN, rel=1e-12), "MWh"),
        "electricity_used": (pytest.approx(USED, rel=1e-12), "MWh"),
    }
    assert SHARE != pytest.approx(EQUAL_WEIGHT_SHARE, abs=1.0)


def test_a_window_is_its_own_arithmetic(share_case):
    window = (STAMPS[1].isoformat(), STAMPS[2].isoformat())
    values, reasons = _checked(share_case(), window)
    assert reasons == {}
    assert values["renewable_share"][0] == pytest.approx(WINDOW_SHARE, rel=1e-12)
    assert values["electricity_used"][0] == pytest.approx(26.0, rel=1e-12)
    assert values["nonrenewable_energy"][0] == pytest.approx(NONREN, rel=1e-12)


def test_the_route_shows_the_same_figures(share_case, client):
    """A4.6: the route's fraction (0..1) is the checked share (percent) over 100."""
    values, _ = _checked(share_case())
    route = client.get("/api/results/renewable_fraction").json()
    assert route["reason"] is None
    assert route["fraction"] == pytest.approx(1 - 7 / 36, rel=1e-9)
    assert route["fraction"] * 100 == pytest.approx(
        values["renewable_share"][0], rel=1e-9
    )
    assert route["nonrenewable_mwh"] == pytest.approx(
        values["nonrenewable_energy"][0], rel=1e-9
    )
    assert route["served_mwh"] == pytest.approx(values["electricity_used"][0], rel=1e-9)


def test_an_injected_wrong_route_value_does_not_move_the_checked_answer(
    share_case, monkeypatch, client
):
    """A4.6/G4: a wrong service aggregate is caught by the parity check, not copied."""
    ctx = share_case()
    from services.solver import renewable_fraction as service

    real = service.renewable_fraction

    def wrong(n, **kw):
        out = real(n, **kw)
        return {**out, "fraction": out["fraction"] + 0.01}

    monkeypatch.setattr(service, "renewable_fraction", wrong)
    route = client.get("/api/results/renewable_fraction").json()
    values, _ = _checked(ctx)
    assert values["renewable_share"][0] == pytest.approx(SHARE, rel=1e-12)
    assert route["fraction"] * 100 != pytest.approx(
        values["renewable_share"][0], rel=1e-9
    )


# ── percent and percentage points, both ways ──────────────────────────────


def _derived(formula, operands, value, unit, **extra):
    return Derived.model_validate(
        {
            "id": "d1",
            "kind": "derived",
            "formula_id": formula,
            "operands": operands,
            "value": value,
            "unit": unit,
            **extra,
        }
    )


def _two_shares(share_case):
    whole = _checked(share_case())[0]["renewable_share"]
    window = _checked(share_case(), (STAMPS[1].isoformat(), STAMPS[2].isoformat()))[0][
        "renewable_share"
    ]
    key = derived.scope_key(
        {"project_id": "P", "scope": {"kind": "run_section", "entity_id": "R"}}
    )
    return {"a": (*whole, key), "b": (*window, key)}


def test_two_shares_differ_in_percentage_points(share_case):
    published = _two_shares(share_case)
    expected = SHARE - WINDOW_SHARE  # 7.47... pp
    [entry] = derived.check(
        [_derived("percentage_point_delta/1", ["a", "b"], expected, "pp")], published
    )
    assert (
        entry["value"] == pytest.approx(expected, rel=1e-12) and entry["unit"] == "pp"
    )


@pytest.mark.parametrize(
    ("formula", "extra"),
    [("percent_change/1", {"denominator": "b"}), ("difference/1", {})],
)
def test_two_shares_never_give_a_percent_change(share_case, formula, extra):
    """The other way: (a - b) / b x 100 = 10.2 % is not the share's change; it is refused."""
    published = _two_shares(share_case)
    value = (SHARE - WINDOW_SHARE) / WINDOW_SHARE * 100
    with pytest.raises(ValueError, match="derived_unit_mismatch"):
        derived.check([_derived(formula, ["a", "b"], value, "%", **extra)], published)


# ── validity: every reason has its own fixture ────────────────────────────


def _many_loads(n):
    """MAX_MEMBERS + 1 counted Loads: a real population over the cap."""
    for i in range(share.MAX_MEMBERS):
        n.add("Load", f"small {i}", bus="AC", p_set=0.01)


def _gas_export(n):
    """An export paying more than gas costs: gas runs to export, past the load."""
    n.add(
        "Generator",
        "export",
        bus="AC",
        carrier="grid",
        p_nom=50,
        sign=-1,
        marginal_cost=-60.0,
    )


def _inactive_load(n):
    n.add("Load", "off", bus="AC", p_set=5.0, active=False)


def _heat_load(n):
    n.add("Carrier", "heat")
    n.add("Bus", "H", carrier="heat")
    n.add("Load", "heat demand", bus="H", p_set=4.0)
    n.add(
        "Generator",
        "boiler",
        bus="H",
        carrier="gas boiler",
        p_nom=10,
        marginal_cost=30.0,
    )


def _curtailment_export(n):
    """A withdrawing Generator takes curtailed wind at t0 only (2 MW at -1 EUR/MWh)."""
    n.add(
        "Generator",
        "export",
        bus="AC",
        carrier="grid",
        p_nom=2,
        sign=-1,
        marginal_cost=-1.0,
    )


def _reach(share_case, monkeypatch, name):
    ctx = (
        share_case(load=(0.0, 0.0, 0.0))
        if name == "no_electricity_served"
        else share_case()
    )
    n, window = ctx.network, None
    if name == "unverified_solve":
        n.add("Load", "late", bus="AC", p_set=1)
    elif name == "invalid_or_missing_raw_data":
        n.generators_t.p.loc[STAMPS[1], "gas"] = float("nan")
    elif name == "unsupported_supply_path":
        n.add("Bus", "fuel", carrier="gas")
        n.add("Generator", "diesel", bus="fuel", carrier="diesel", p_nom=10)
        n.add("Link", "genset", bus0="fuel", bus1="AC", p_nom=10, efficiency=0.4)
        workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    elif name == "unserved_energy_not_modelled":
        ctx.solver_state["last_lost_load"] = {
            "lost_load_t": pd.DataFrame({"L": [0.0, 1.0, 0.0]}, index=STAMPS)
        }
    elif name == "unsupported_population":
        ctx = share_case(_many_loads)
    elif name == "nonrenewable_exceeds_served":
        ctx = share_case(_gas_export)
    return set(_checked(ctx, window)[1].values())


@pytest.mark.parametrize("name", sorted(share.REASONS))
def test_each_reason_has_its_own_fixture(share_case, monkeypatch, name):
    assert _reach(share_case, monkeypatch, name) == {name}


def test_the_share_alone_is_withheld_when_its_denominator_fails(share_case):
    values, reasons = _checked(share_case(load=(0.0, 0.0, 0.0)))
    assert reasons == {"renewable_share": "no_electricity_served"}
    assert values == {
        "nonrenewable_energy": (0.0, "MWh"),
        "electricity_used": (0.0, "MWh"),
    }


@pytest.mark.parametrize(
    ("served", "answered"), [(2 * derived.NEAR_ZERO, True), (derived.NEAR_ZERO, False)]
)
def test_the_denominator_boundary(share_case, served, answered):
    raw = _raw(share_case())
    raw["generators"] = {g: [0.0, 0.0, 0.0] for g in raw["generators"]}
    raw["loads"] = {"L": [served, 0.0, 0.0]}
    reasons = _reasons(raw)
    assert (reasons["renewable_share"] is None) is answered


def test_nonrenewable_equal_to_use_is_zero_percent(share_case):
    raw = _raw(share_case())
    raw["generators"] = {"gas": [10.0, 20.0, 8.0], "h2 turbine": [0.0, 0.0, 0.0]}
    facts, _ = share.calculate(raw, {"profile": share.PROFILE, "entity_id": "network"})
    assert {f["metric"]: f["value"] for f in facts}["renewable_share"] == 0.0


def test_negative_generator_output_is_not_counted(share_case):
    raw = _raw(share_case())
    raw["generators"]["gas"] = [-5.0, 14.0, 0.0]
    facts, _ = share.calculate(raw, {"profile": share.PROFILE, "entity_id": "network"})
    assert {f["metric"]: f["value"] for f in facts}[
        "nonrenewable_energy"
    ] == pytest.approx(NONREN)


@pytest.mark.parametrize("weight", [-0.5, None], ids=["negative", "non_finite"])
def test_a_negative_or_non_finite_weight_answers_nothing(share_case, weight):
    raw = _raw(share_case())
    raw["energy_weight"] = [1.0, weight, 2.0]  # `_number` captures NaN or inf as None
    assert set(_reasons(raw).values()) == {"unverified_solve"}


@pytest.mark.parametrize(
    "slack",
    ["dsr_t", "dc_curtailment"],
)
def test_each_removed_slack_withholds_every_figure(share_case, slack):
    ctx = share_case()
    ctx.solver_state["last_lost_load"] = {
        slack: pd.DataFrame({"x": [0.0, 0.0, 0.0]}, index=STAMPS)
    }
    assert set(_checked(ctx)[1].values()) == {"unserved_energy_not_modelled"}


def test_a_slack_generator_withholds_every_figure(baseline):
    """A VoLL slack left on the network (never dispatched here) is still not modelled."""
    from services.adequacy.slack import VOLL_SLACK_PREFIX

    ctx = baseline[0]
    ctx.network = _network()
    ctx.network.add(
        "Generator", VOLL_SLACK_PREFIX + "AC", bus="AC", p_nom=100, marginal_cost=1e4
    )
    _solve(ctx)
    assert set(_checked(ctx)[1].values()) == {"unsupported_supply_path"}


def test_refusals_are_named(share_case):
    ctx = share_case()
    with pytest.raises(ValueError, match="network_scope_required"):
        calculations.scoped_source(ctx, {"profile": share.PROFILE, "entity_id": "gas"})
    assert {"network_scope_required", "unsupported_snapshot_scope"} == share.REFUSALS


def test_a_record_of_another_entity_is_rejected(share_case):
    raw = _raw(share_case())
    with pytest.raises(ValueError, match="renewable_share_source_identity"):
        share.calculate(raw, {"profile": share.PROFILE, "entity_id": "gas"})


def test_grammar():
    scope = assurance_scope.from_message(
        "Show the renewable share for network.", "p1", {}
    )
    assert scope["profile"] == share.PROFILE and scope["entity_id"] == "network"
    windowed = assurance_scope.from_message(
        "show renewable share for network; from 2026-01-01T01:00 to 2026-01-01T02:00",
        "p1",
        {},
    )
    assert (windowed["start"], windowed["end"]) == (
        "2026-01-01T01:00",
        "2026-01-01T02:00",
    )
    assert (
        assurance_scope.from_message(
            "show the renewable share for generator gas", "p1", {}
        )
        is None
    )


# ── answered populations the KPI also answers: inactive, non-electrical, withdrawing ──


@pytest.mark.parametrize(
    "tweak", [_inactive_load, _heat_load, _curtailment_export], ids=lambda f: f.__name__
)
def test_an_answered_variant_equals_the_route(share_case, client, tweak):
    values, reasons = _checked(share_case(tweak))
    assert reasons == {}
    route = client.get("/api/results/renewable_fraction").json()
    assert route["reason"] is None
    assert values["renewable_share"][0] == pytest.approx(SHARE, rel=1e-12)
    assert route["fraction"] * 100 == pytest.approx(
        values["renewable_share"][0], rel=1e-9
    )
    assert route["served_mwh"] == pytest.approx(values["electricity_used"][0], rel=1e-9)


def test_a_withdrawing_generator_is_neither_supply_nor_use(share_case):
    ctx = share_case(_curtailment_export)
    assert ctx.network.generators_t.p.loc[STAMPS[0], "export"] == pytest.approx(2.0)
    cls = classify(ctx.network)
    assert "export" not in cls["nonren_generators"] + cls["injecting_generators"]


def test_a_nan_on_an_uncounted_column_does_not_withhold(share_case, client):
    ctx = share_case()
    ctx.network.generators_t.p.loc[STAMPS[1], "bio"] = float("nan")
    values, reasons = _checked(ctx)
    assert reasons == {}
    assert values["renewable_share"][0] == pytest.approx(SHARE, rel=1e-12)
    route = client.get("/api/results/renewable_fraction").json()
    assert route["fraction"] * 100 == pytest.approx(SHARE, rel=1e-9)


def test_the_exports_excess_is_withheld_on_both_surfaces(share_case, client):
    _reach_ctx = share_case(_gas_export)
    assert _checked(_reach_ctx)[1]["renewable_share"] == "nonrenewable_exceeds_served"
    route = client.get("/api/results/renewable_fraction").json()
    assert route["fraction"] is None and route["reason"]


def test_the_dsr_record_fallback_withholds_every_figure(share_case, monkeypatch):
    ctx = share_case()
    monkeypatch.setattr(
        "services.commercial.settlement_inputs.dsr_activation",
        lambda n: (pd.DataFrame({"AC": [0.0, 1.0, 0.0]}, index=STAMPS), []),
    )
    assert set(_checked(ctx)[1].values()) == {"unserved_energy_not_modelled"}


def test_a_committed_dc_curtailment_withholds_every_figure(share_case):
    from services.solver import dc_curtailment

    ctx = share_case()
    ctx.network.meta = {
        **(ctx.network.meta or {}),
        dc_curtailment.META_KEY: {"p_t": [0.0]},
    }
    assert set(_checked(ctx)[1].values()) == {"unserved_energy_not_modelled"}


def test_a_multi_period_network_is_refused(share_case):
    ctx = share_case()
    n = ctx.network
    n.set_snapshots(pd.MultiIndex.from_product([[2030], STAMPS]))
    n.set_investment_periods([2030])
    with pytest.raises(ValueError, match="unsupported_snapshot_scope"):
        calculations.scoped_source(
            ctx, {"profile": share.PROFILE, "entity_id": "network"}
        )


def test_another_entity_is_refused_before_the_snapshot_scope(share_case):
    ctx = share_case()
    ctx.network.set_snapshots(pd.MultiIndex.from_product([[2030], STAMPS]))
    with pytest.raises(ValueError, match="network_scope_required"):
        calculations.scoped_source(ctx, {"profile": share.PROFILE, "entity_id": "gas"})
