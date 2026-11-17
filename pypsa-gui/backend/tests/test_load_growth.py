"""Load growth as a rate per year (homer-parity 04a, HOMER-GAPS-1).

A typed "load growth, % per year" per carrier derives the per-period load
multipliers, compounded from the growth base year, and stores them in
`load_scalers_by_carrier`, the one map every reader (LP, adequacy, finance,
compare, the FCA rebind) already uses. A typed multiplier wins over the rate
(R4); the rate is finite and above -100 %, and the base year is required and
not after the first period (R5). With no rate nothing changes, byte for byte.
"""
import json
import math
from dataclasses import asdict

import pytest

from services.reliability_goal import goal_free_config
from services.solver.load_growth import derive_load_scalers, load_growth_problems
from services.solver_service import SolverConfig


def test_two_percent_from_2030_gives_1_104_in_2035():
    # Hand check: 1.02 ** 5 = 1.10408080...; 2030 is the base year, factor 1.0,
    # and an absent key already means 1.0, so none is written for it.
    scalers, derived = derive_load_scalers([2030, 2035], 2030, {"electrical": 2.0}, {}, {})
    assert scalers == {"electrical": {"2035": 1.104081}}
    assert derived == {"electrical": ["2035"]}


def test_a_typed_multiplier_wins_over_the_rate():
    scalers, derived = derive_load_scalers(
        [2030, 2035, 2040], 2030, {"electrical": 2.0}, {"electrical": {"2035": 1.3}}, {})
    assert scalers["electrical"]["2035"] == 1.3
    assert scalers["electrical"]["2040"] == pytest.approx(1.02 ** 10, abs=1e-6)
    assert derived == {"electrical": ["2040"]}


def test_a_period_the_legacy_all_carrier_multiplier_types_is_not_derived():
    scalers, derived = derive_load_scalers([2030, 2035, 2040], 2030, {"electrical": 2.0}, {}, {},
                                           {"2035": 1.2})
    assert scalers == {"electrical": {"2040": pytest.approx(1.02 ** 10, abs=1e-6)}}
    assert derived == {"electrical": ["2040"]}


def test_a_changed_rate_rederives_only_the_derived_entries():
    scalers, derived = derive_load_scalers(
        [2030, 2035, 2040], 2030, {"electrical": 2.0}, {"electrical": {"2035": 1.3}}, {})
    scalers, derived = derive_load_scalers([2030, 2035, 2040], 2030, {"electrical": 5.0},
                                           scalers, derived)
    assert scalers["electrical"]["2035"] == 1.3
    assert scalers["electrical"]["2040"] == pytest.approx(1.05 ** 10, abs=1e-6)


def test_a_removed_rate_drops_its_derived_entries_and_keeps_the_typed_ones():
    scalers, derived = derive_load_scalers(
        [2030, 2035, 2040], 2030, {"electrical": 2.0, "heat": 1.0},
        {"electrical": {"2035": 1.3}}, {})
    scalers, derived = derive_load_scalers([2030, 2035, 2040], 2030, {"heat": 1.0},
                                           scalers, derived)
    assert scalers["electrical"] == {"2035": 1.3}
    assert "electrical" not in derived
    assert set(scalers["heat"]) == {"2035", "2040"}


def test_a_base_year_before_the_first_period_compounds_from_it():
    scalers, _ = derive_load_scalers([2030], 2028, {"hydrogen": 10.0}, {}, {})
    assert scalers == {"hydrogen": {"2030": 1.21}}


def test_a_zero_rate_writes_nothing():
    assert derive_load_scalers([2030, 2035], 2030, {"electrical": 0.0}, {}, {}) == ({}, {})


def test_carriers_without_a_rate_are_left_as_typed():
    typed = {"heat": {"2035": 1.5}}
    scalers, derived = derive_load_scalers([2030, 2035], 2030, {"electrical": 2.0}, typed, {})
    assert scalers["heat"] == {"2035": 1.5}
    assert "heat" not in derived
    assert typed == {"heat": {"2035": 1.5}}  # the input is not mutated


# ── R5: one refusal each ─────────────────────────────────────────────────────

@pytest.mark.parametrize("rate", [math.nan, math.inf, -math.inf])
def test_a_non_finite_rate_is_refused(rate):
    assert load_growth_problems({"electrical": rate}, 2030, [2030, 2035])


@pytest.mark.parametrize("rate", [-100.0, -150.0])
def test_a_rate_at_or_below_minus_100_percent_is_refused(rate):
    assert load_growth_problems({"electrical": rate}, 2030, [2030, 2035])


def test_a_rate_without_a_base_year_is_refused():
    assert load_growth_problems({"electrical": 2.0}, None, [2030, 2035])


def test_a_base_year_after_the_first_period_is_refused():
    assert load_growth_problems({"electrical": 2.0}, 2031, [2030, 2035])


def test_a_valid_rate_and_no_rate_pass():
    assert load_growth_problems({"electrical": -5.0}, 2030, [2030, 2035]) == []
    assert load_growth_problems({}, None, [2030, 2035]) == []


# ── R4: no rate is byte-identical in every fingerprint and digest ───────────

def test_the_new_fields_never_enter_the_config_digest_input():
    cfg = SolverConfig()
    keys = set(goal_free_config(cfg))
    assert not keys & {"load_growth_pct_per_year", "load_growth_base_year",
                       "load_scalers_derived"}
    grown = SolverConfig(load_growth_pct_per_year={"electrical": 0.0}, load_growth_base_year=2030)
    assert json.dumps(goal_free_config(grown), sort_keys=True, default=str) \
        == json.dumps(goal_free_config(cfg), sort_keys=True, default=str)
    assert set(asdict(cfg)) - keys >= {"load_growth_pct_per_year"}


# ── The route: the one sink for the settings page and the chat tool ─────────

def _put(client, body):
    return client.put("/api/simulation/solver_config", json=body)


def test_the_route_stores_the_rate_and_the_derived_multipliers(client):
    r = _put(client, {"multi_investment_periods": True, "investment_periods": [2030, 2035],
                      "load_growth_pct_per_year": {"electrical": 2.0},
                      "load_growth_base_year": 2030})
    assert r.status_code == 200, r.text
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["load_scalers_by_carrier"] == {"electrical": {"2035": 1.104081}}
    assert cfg["load_scalers_derived"] == {"electrical": ["2035"]}
    assert cfg["load_growth_pct_per_year"] == {"electrical": 2.0}


def test_the_route_rederives_when_the_periods_change(client):
    _put(client, {"multi_investment_periods": True, "investment_periods": [2030, 2035],
                  "load_growth_pct_per_year": {"electrical": 2.0}, "load_growth_base_year": 2030})
    assert _put(client, {"investment_periods": [2030, 2040]}).status_code == 200
    cfg = client.get("/api/simulation/solver_config").json()
    assert set(cfg["load_scalers_by_carrier"]["electrical"]) == {"2040"}


def test_a_typed_cell_through_the_route_becomes_typed(client):
    _put(client, {"multi_investment_periods": True, "investment_periods": [2030, 2035],
                  "load_growth_pct_per_year": {"electrical": 2.0}, "load_growth_base_year": 2030})
    r = _put(client, {"load_scalers_by_carrier": {"electrical": {"2035": 1.5}},
                      "load_scalers_derived": {}})
    assert r.status_code == 200, r.text
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["load_scalers_by_carrier"] == {"electrical": {"2035": 1.5}}
    assert cfg["load_scalers_derived"] == {}


@pytest.mark.parametrize("body", [
    {"load_growth_pct_per_year": {"electrical": -100.0}, "load_growth_base_year": 2030},
    {"load_growth_pct_per_year": {"electrical": 2.0}},
    {"load_growth_pct_per_year": {"electrical": 2.0}, "load_growth_base_year": 2031},
])
def test_the_route_refuses_each_invalid_input(client, body):
    _put(client, {"multi_investment_periods": True, "investment_periods": [2030, 2035]})
    r = _put(client, body)
    assert r.status_code == 422, r.text


def test_the_route_refuses_a_non_finite_rate(client):
    # JSON has no NaN literal; Python's encoder writes one, as a lenient client might.
    r = client.put("/api/simulation/solver_config",
                   content=json.dumps({"load_growth_pct_per_year": {"electrical": float("nan")},
                                       "load_growth_base_year": 2030}),
                   headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text


def test_without_a_rate_the_stored_scalers_are_untouched(client):
    typed = {"electrical": {"2030": 1.0, "2035": 1.2}}
    _put(client, {"multi_investment_periods": True, "investment_periods": [2030, 2035],
                  "load_scalers_by_carrier": typed})
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["load_scalers_by_carrier"] == typed
    assert cfg["load_scalers_derived"] == {}
