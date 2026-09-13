"""
`explain_investment` — the sizing question, answered from evidence.

The tool exists because "why did the model build X" is almost always answered
by WHICH CONSTRAINT BOUND IT, and nothing in the per-asset metric registry
carries p_nom_max / p_nom_extendable. These tests pin the classification on a
network built so that each outcome is reachable and unambiguous, plus the
honesty properties: no verdict, no silent zero on an unsolved network, and
reading notes that match the case.
"""
from __future__ import annotations

import json

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from services import chat_service
from services import chat_tools as T
from services import chat_tools_schema as S


def _sizing_network(*, solve: bool = True, co2_cap: float | None = None) -> pypsa.Network:
    """
    One bus, four sizing outcomes.

    Costs are chosen so the ordering is forced, not incidental: wind is the
    cheapest energy but capped at 40 MW (ceiling), gas is dearer per MW but
    uncapped (interior), nuclear is priced out entirely (not built), and
    diesel is not extendable at all.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    n.add("Bus", "B1")
    n.add("Carrier", "gas", co2_emissions=0.4)
    n.add("Carrier", "wind")
    n.add("Carrier", "nuclear")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "wind1", bus="B1", carrier="wind", p_nom_extendable=True,
          capital_cost=10_000.0, marginal_cost=0.0, p_nom_max=40.0)
    n.add("Generator", "gas1", bus="B1", carrier="gas", p_nom_extendable=True,
          capital_cost=30_000.0, marginal_cost=60.0)
    n.add("Generator", "nuclear1", bus="B1", carrier="nuclear",
          p_nom_extendable=True, capital_cost=900_000.0, marginal_cost=5.0)
    n.add("Generator", "diesel", bus="B1", carrier="gas", p_nom=30.0,
          marginal_cost=300.0)
    if co2_cap is not None:
        n.add("GlobalConstraint", "co2", type="primary_energy",
              carrier_attribute="co2_emissions", sense="<=", constant=co2_cap)
    if solve:
        n.optimize(solver_name="highs")
    return n


def _schema() -> dict:
    return next(t for t in S.TOOLS if t["name"] == "explain_investment")


# ── Registration ───────────────────────────────────────────────────────────


def test_tool_is_registered_dispatchable_and_read_tier():
    assert _schema()
    assert callable(T.DISPATCHERS["explain_investment"])
    assert "explain_investment" in S.TOOL_ROUTES
    assert chat_service._safety_tier_for("explain_investment") == "read"


def test_investment_enum_matches_the_sizeable_classes():
    """The enum is a hand copy of _NOM_COL; drift makes the tool 400 wrongly."""
    from services.asset_results.compute import nom_col_for

    for cls in S.INVESTMENT_CLASS_ENUM:
        assert nom_col_for(cls) is not None, f"{cls} is not sizeable"
    for not_sizeable in ("Bus", "Load"):
        assert not_sizeable not in S.INVESTMENT_CLASS_ENUM
        assert nom_col_for(not_sizeable) is None


# ── The classification ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name,expected", [
    ("wind1", "at_upper_bound"),
    ("gas1", "interior"),
    ("nuclear1", "not_built"),
    ("diesel", "not_extendable"),
])
def test_binding_constraint_classification(name, expected, install_network):
    install_network(_sizing_network())
    out = T.explain_investment("Generator", name)
    assert out["sizing"]["binding_constraint"] == expected
    assert out["sizing"]["explanation"] == T._BINDING_EXPLANATIONS[expected]


def test_ceiling_is_detected_at_solver_tolerance(install_network):
    """
    An LP lands ON a bound within tolerance. An exact `==` reports 'interior'
    for a plainly saturated asset — the one wrong answer this tool exists to
    avoid — so the comparison is relative.
    """
    install_network(_sizing_network())
    sizing = T.explain_investment("Generator", "wind1")["sizing"]
    assert sizing["optimised"] == pytest.approx(sizing["upper_bound"])
    assert sizing["headroom"] == pytest.approx(0.0, abs=1e-6)


def test_a_zero_floor_is_not_reported_as_a_floor(install_network):
    """
    p_nom_min = 0 is not a constraint. Calling an unbuilt asset
    'at_lower_bound' reads as if something forced the zero, when it lost on
    cost — a different remedy entirely.
    """
    install_network(_sizing_network())
    out = T.explain_investment("Generator", "nuclear1")
    assert out["sizing"]["lower_bound"] == 0.0
    assert out["sizing"]["binding_constraint"] == "not_built"
    assert "not worth building" in out["sizing"]["explanation"]


def test_a_real_floor_is_reported_as_one(install_network):
    n = _sizing_network(solve=False)
    n.generators.loc["nuclear1", "p_nom_min"] = 15.0
    n.optimize(solver_name="highs")
    install_network(n)
    sizing = T.explain_investment("Generator", "nuclear1")["sizing"]
    assert sizing["binding_constraint"] == "at_lower_bound"
    assert sizing["optimised"] == pytest.approx(15.0)


def test_unbounded_ceiling_is_null_not_infinity(install_network):
    """`p_nom_max` defaults to inf, and inf is not JSON."""
    install_network(_sizing_network())
    out = T.explain_investment("Generator", "gas1")
    assert out["sizing"]["upper_bound"] is None
    assert out["sizing"]["headroom"] is None
    json.dumps(out, default=str)  # must not raise


def test_added_capacity_is_optimised_minus_existing(install_network):
    install_network(_sizing_network())
    s = T.explain_investment("Generator", "wind1")["sizing"]
    assert s["added"] == pytest.approx(s["optimised"] - s["existing"])


# ── Honesty on an unsolved network ─────────────────────────────────────────


def test_unsolved_network_refuses_to_explain_a_decision(install_network):
    """
    PyPSA initialises `p_nom_opt` to 0.0, so an unsolved network looks exactly
    like one that built nothing. Without the dispatch gate this tool would
    report 'not_built' for every extendable asset on a network nobody has run.
    """
    install_network(_sizing_network(solve=False))
    out = T.explain_investment("Generator", "gas1")
    assert out["sizing"]["binding_constraint"] == "not_solved"
    assert out["sizing"]["optimised"] is None
    assert out["dispatch_state"]["state"] != "fresh"
    assert out["system_signals"]["bus_prices"] == {}
    assert out["system_signals"]["co2_caps"] == []
    assert out["system_signals"]["congestion"]["lines"] == []
    assert "no fresh dispatch" in out["system_signals"]["congestion"]["note"]


# ── System signals ─────────────────────────────────────────────────────────


def test_bus_price_signals_are_reported_for_the_asset_bus(install_network):
    install_network(_sizing_network())
    prices = T.explain_investment("Generator", "gas1")["system_signals"]["bus_prices"]
    assert "B1" in prices
    assert "bus_price_mean" in prices["B1"]


def test_a_binding_co2_cap_and_its_shadow_price_travel_with_the_asset(install_network):
    install_network(_sizing_network(co2_cap=50.0))
    out = T.explain_investment("Generator", "gas1")
    caps = out["system_signals"]["co2_caps"]
    assert caps, "an active CO2 cap must be reported"
    assert "shadow_price_eur_per_tCO2" in caps[0]
    assert any("CO2 cap binds" in note for note in out["reading_notes"]), \
        "a binding cap must be called out — its shadow price is part of the economics"


def test_a_buses_with_no_lines_says_so_rather_than_blaming_the_duals(install_network):
    """
    `_sizing_network` is a single bus. compute_line_duals answers "No LP duals
    captured — re-run the solve", which would send the user after a re-solve
    that changes nothing. The tool has to interpret, not relay.
    """
    install_network(_sizing_network())
    out = T.explain_investment("Generator", "gas1")
    congestion = out["system_signals"]["congestion"]
    assert congestion["lines"] == []
    assert "no line connects to this asset" in congestion["note"]
    assert "re-run" not in congestion["note"].lower()
    assert any("congestion.note" in note for note in out["reading_notes"])


def test_missing_duals_are_reported_as_unknown_not_as_uncongested(install_network):
    """
    Lines exist but the solve captured no duals: empty means UNKNOWN, which is
    a different claim from "nothing binds".
    """
    n = _sizing_network(solve=False)
    n.add("Bus", "B2")
    n.add("Line", "l1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=500.0)
    n.optimize(solver_name="highs")           # no assign_all_duals
    install_network(n)
    congestion = T.explain_investment(
        "Generator", "gas1")["system_signals"]["congestion"]
    assert congestion["lines"] == []
    assert "duals" in congestion["note"].lower()


def test_a_solved_uncongested_network_says_nothing_binds(install_network):
    """The last empty case: lines there, duals present, nothing binding."""
    n = _sizing_network(solve=False)
    n.add("Bus", "B2")
    n.add("Line", "l1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=500.0)
    n.optimize(solver_name="highs", assign_all_duals=True)
    install_network(n)
    congestion = T.explain_investment(
        "Generator", "gas1")["system_signals"]["congestion"]
    assert congestion["lines"] == []
    assert "no line at this asset" in congestion["note"]


def test_a_binding_line_out_of_the_asset_bus_is_reported(install_network):
    """
    The 'why HERE' half of the question. Cheap generation behind a line too
    small to export it is the classic answer, and an explanation that omits
    the congestion attributes the outcome to cost alone.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "cheap")
    n.add("Bus", "demand")
    # 20 MW of wire between 100 MW of demand and the cheap side: it binds.
    n.add("Line", "corridor", bus0="cheap", bus1="demand", x=0.1, r=0.01,
          s_nom=20.0)
    n.add("Load", "L1", bus="demand", p_set=100.0)
    n.add("Generator", "cheap_gen", bus="cheap", p_nom_extendable=True,
          capital_cost=1_000.0, marginal_cost=1.0)
    n.add("Generator", "local_gen", bus="demand", p_nom_extendable=True,
          capital_cost=5_000.0, marginal_cost=90.0)
    # `assign_all_duals` is what writes lines_t.mu_upper — the real solver
    # path sets it, and without it there are no congestion duals to read.
    n.optimize(solver_name="highs", assign_all_duals=True)
    install_network(n)

    congestion = T.explain_investment(
        "Generator", "cheap_gen")["system_signals"]["congestion"]
    assert congestion["lines"], "the binding corridor out of this bus must be reported"
    assert congestion["lines"][0]["name"] == "corridor"
    assert congestion["lines"][0]["binding_hours"] > 0
    assert congestion["note"] is None


def test_congestion_is_filtered_to_the_asset_own_buses(install_network):
    """A line the asset does not touch is not evidence about the asset."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    for bus in ("a", "b", "c"):
        n.add("Bus", bus)
    n.add("Line", "ab", bus0="a", bus1="b", x=0.1, r=0.01, s_nom=20.0)
    n.add("Line", "bc", bus0="b", bus1="c", x=0.1, r=0.01, s_nom=1000.0)
    n.add("Load", "L1", bus="b", p_set=100.0)
    n.add("Generator", "on_a", bus="a", p_nom_extendable=True,
          capital_cost=1_000.0, marginal_cost=1.0)
    n.add("Generator", "on_c", bus="c", p_nom_extendable=True,
          capital_cost=5_000.0, marginal_cost=90.0)
    n.optimize(solver_name="highs", assign_all_duals=True)
    install_network(n)

    named = {
        r["name"]
        for r in T.explain_investment("Generator", "on_c")[
            "system_signals"]["congestion"]["lines"]
    }
    assert "ab" not in named, "a line at another bus is not this asset's evidence"


# ── Framing ────────────────────────────────────────────────────────────────


def test_interior_asset_carries_the_zero_profit_note(install_network):
    """Otherwise a near-zero net profit reads as a defect and gets a made-up cause."""
    install_network(_sizing_network())
    notes = T.explain_investment("Generator", "gas1")["reading_notes"]
    assert any("Zero-profit equilibrium" in note for note in notes)


def test_bounded_asset_is_told_not_to_narrate_economics(install_network):
    install_network(_sizing_network())
    notes = T.explain_investment("Generator", "wind1")["reading_notes"]
    assert any("bound, not an optimum" in note for note in notes)


def test_every_case_says_the_payload_is_evidence_not_a_verdict(install_network):
    install_network(_sizing_network())
    for name in ("wind1", "gas1", "nuclear1", "diesel"):
        notes = T.explain_investment("Generator", name)["reading_notes"]
        assert any("EVIDENCE, not a verdict" in note for note in notes)


def test_kpis_come_from_the_registry_not_a_second_computation(install_network):
    """
    The headline must be the SAME numbers the Asset Detail tab shows — a
    parallel computation here is how the chat answer and the panel start
    disagreeing.
    """
    install_network(_sizing_network())
    fused = T.explain_investment("Generator", "gas1")["asset_kpis"]
    direct = T.get_asset_results("Generator", "gas1", category="summary")["headline"]
    assert fused == direct


# ── Refusals ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cls", ["Bus", "Load"])
def test_non_investment_classes_are_refused_with_400(cls, install_network):
    install_network(_sizing_network())
    with pytest.raises(HTTPException) as exc:
        T.explain_investment(cls, "B1")
    assert exc.value.status_code == 400
    assert "Generator" in str(exc.value.detail)


def test_unknown_asset_is_404(install_network):
    install_network(_sizing_network())
    with pytest.raises(HTTPException) as exc:
        T.explain_investment("Generator", "no-such-generator")
    assert exc.value.status_code == 404


def test_payload_is_json_serialisable_for_every_class(install_network):
    install_network(_sizing_network())
    for name in ("wind1", "gas1", "nuclear1", "diesel"):
        body = json.dumps(T.explain_investment("Generator", name), default=str)
        assert "Response object" not in body


def test_system_prompt_routes_sizing_questions_here():
    """A tool the agent never reaches for is a tool that does not exist."""
    prompt = chat_service._build_system_prompt(chat_service.ChatSession())
    assert "explain_investment FIRST" in prompt
    assert "NOT" in prompt and "sized by its economics" in prompt
