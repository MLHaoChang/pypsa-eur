"""Load growth as a rate per year (homer-parity 04a, HOMER-GAPS-1).

A typed "load growth, % per year" per carrier derives the per-period load
multipliers, compounded from the growth start year, and stores them in
`load_scalers_by_carrier`, the one map every reader (LP, adequacy, finance,
compare, the FCA rebind) already uses. GK B's plan GO: R4/R5 and C1-C7.
"""

import json
import math
from dataclasses import asdict, replace

import pytest

from services.reliability_goal import goal_free_config
from services.solver import load_growth as LG
from services.solver.load_growth import (
    derive_load_scalers,
    derived_marks,
    load_growth_problems,
)
from services.solver_service import SolverConfig
from tests.test_assurance_metrics import solved  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


def _derive(periods, base, rates, submitted=None, stored=None, marks=None, legacy=None):
    return derive_load_scalers(
        periods, base, rates, submitted or {}, stored or {}, marks, legacy
    )


def test_two_percent_from_2030_gives_1_104_in_2035():
    # Hand check: 1.02 ** 5 = 1.10408080...; 2030 is the start year, factor 1.0,
    # and an absent key already means 1.0, so none is written for it.
    assert _derive([2030, 2035], 2030, {"electrical": 2.0}) == (
        {"electrical": {"2035": 1.104081}},
        {"electrical": ["2035"]},
    )


def test_a_start_year_before_the_first_period_compounds_from_it():
    assert _derive([2030], 2028, {"hydrogen": 10.0})[0] == {"hydrogen": {"2030": 1.21}}


def test_a_typed_multiplier_wins_over_the_rate():
    typed = {"electrical": {"2035": 1.3}}
    scalers, marks = _derive(
        [2030, 2035, 2040], 2030, {"electrical": 2.0}, typed, typed
    )
    assert scalers["electrical"]["2035"] == 1.3
    assert scalers["electrical"]["2040"] == pytest.approx(1.02**10, abs=1e-6)
    assert marks == {"electrical": ["2040"]}


def test_a_period_the_legacy_all_carrier_multiplier_types_is_not_derived():
    scalers, marks = _derive(
        [2030, 2035, 2040], 2030, {"electrical": 2.0}, legacy={"2035": 1.2}
    )
    assert set(scalers["electrical"]) == {"2040"} and marks == {"electrical": ["2040"]}


def test_carriers_without_a_rate_are_left_as_typed_and_inputs_are_not_mutated():
    typed = {"heat": {"2035": 1.5}}
    scalers, marks = _derive([2030, 2035], 2030, {"electrical": 2.0}, typed, typed)
    assert scalers["heat"] == {"2035": 1.5} and "heat" not in marks
    assert typed == {"heat": {"2035": 1.5}}


# ── C2: derived or typed is the server's call, from the stored state ─────────

STORED = {"electrical": {"2035": 1.104081, "2040": 1.218994}}
MARKS = {"electrical": ["2035", "2040"]}


def test_a_submitted_value_equal_to_the_stored_derived_value_stays_derived():
    scalers, marks = _derive(
        [2030, 2035, 2040], 2030, {"electrical": 5.0}, STORED, STORED, MARKS
    )
    assert scalers["electrical"]["2035"] == round(1.05**5, 6)
    assert marks == {"electrical": ["2035", "2040"]}


def test_a_submitted_value_differing_from_the_stored_derived_value_becomes_typed():
    sub = {"electrical": {"2035": 1.5, "2040": 1.218994}}
    scalers, marks = _derive(
        [2030, 2035, 2040], 2030, {"electrical": 2.0}, sub, STORED, MARKS
    )
    assert scalers["electrical"]["2035"] == 1.5
    assert marks == {"electrical": ["2040"]}


def test_a_cleared_typed_cell_is_derived_again():
    stored = {"electrical": {"2035": 1.5, "2040": 1.218994}}
    sub = {"electrical": {"2040": 1.218994}}
    scalers, marks = _derive(
        [2030, 2035, 2040],
        2030,
        {"electrical": 2.0},
        sub,
        stored,
        {"electrical": ["2040"]},
    )
    assert scalers["electrical"]["2035"] == 1.104081
    assert marks == {"electrical": ["2035", "2040"]}


# ── GK B (a): no stale derived entry survives ────────────────────────────────


def test_a_derived_entry_whose_period_is_gone_is_removed():
    scalers, marks = _derive(
        [2030, 2035], 2030, {"electrical": 2.0}, STORED, STORED, MARKS
    )
    assert scalers == {"electrical": {"2035": 1.104081}} and marks == {
        "electrical": ["2035"]
    }


def test_a_derived_entry_that_becomes_1_is_removed_when_the_start_year_moves():
    scalers, marks = _derive(
        [2035, 2040], 2035, {"electrical": 2.0}, STORED, STORED, MARKS
    )
    assert scalers == {"electrical": {"2040": 1.104081}} and marks == {
        "electrical": ["2040"]
    }


def test_a_zero_rate_removes_every_derived_entry():
    assert _derive(
        [2030, 2035, 2040], 2030, {"electrical": 0.0}, STORED, STORED, MARKS
    ) == ({"electrical": {}}, {})


def test_a_removed_rate_removes_its_derived_entries_and_keeps_the_typed_ones():
    stored = {"electrical": {"2035": 1.3, "2040": 1.218994}}
    scalers, marks = _derive(
        [2030, 2035, 2040], 2030, {}, stored, stored, {"electrical": ["2040"]}
    )
    assert scalers == {"electrical": {"2035": 1.3}} and marks == {}


# ── C3: one reader of the marks; a malformed mark reads as "not derived" ─────


@pytest.mark.parametrize(
    "raw",
    [
        "2035",
        ["electrical"],
        {"electrical": "2035"},
        {"electrical": [2035]},
        {"electrical": ["2045"]},  # a mark on a key the map does not hold
        {"electrical": ["2030"]},  # a mark on a key the map does not hold
        {"hydrogen": ["2035"]},  # a carrier the map does not hold
        {"electrical": None},
    ],
)
def test_a_malformed_mark_reads_as_typed_and_nothing_is_overwritten(raw):
    stored = {"electrical": {"2035": 1.7}}
    scalers, _ = _derive([2030, 2035], 2030, {"electrical": 2.0}, stored, stored, raw)
    assert scalers["electrical"]["2035"] == 1.7


def test_the_mark_reader_keeps_only_well_formed_marks():
    assert derived_marks(
        {"electrical": ["2035", 7, "2099", "x"]},
        {"electrical": {"2035": 1.1, "x": 1.0}},
    ) == {"electrical": {"2035"}}
    assert derived_marks(None, {}) == {}


# ── GK B (b): every derived factor is finite and above 0 ─────────────────────


@pytest.mark.parametrize("rate", [1e6, -99.9999])
def test_a_rate_whose_factor_is_not_finite_and_above_0_is_refused(rate):
    with pytest.raises(LG.LoadGrowthError) as exc:
        _derive([2030, 2130], 2030, {"electrical": rate})
    assert exc.value.kind == "load_growth_factor_invalid"


# ── R5: one refusal each ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rate", [math.nan, math.inf, -math.inf, -100.0, -150.0, True, "2"]
)
def test_each_invalid_rate_is_refused(rate):
    assert load_growth_problems({"electrical": rate}, 2030, [2030, 2035])


@pytest.mark.parametrize("base", [None, 2031, True, "2030", 2030.0])
def test_each_invalid_start_year_is_refused(base):
    assert load_growth_problems({"electrical": 2.0}, base, [2030, 2035])


# ── C4: rate keys are load carriers, one rate per carrier ────────────────────


def test_two_aliases_of_one_carrier_are_refused():
    assert LG.rate_key_problems({"AC": 2.0, "electrical": 3.0}, None)


def test_a_rate_on_a_carrier_no_load_has_is_refused():
    assert LG.rate_key_problems({"foo": 2.0}, {"electrical"})


def test_a_rate_on_an_alias_of_a_load_carrier_passes():
    assert LG.rate_key_problems({"AC": 2.0}, {"electrical"}) == []


def test_without_a_network_the_carrier_is_not_checked():
    assert LG.rate_key_problems({"foo": 2.0}, None) == []
    assert LG.rate_key_problems({"foo": 2.0}, set()) == []


def test_a_valid_rate_and_no_rate_pass():
    assert load_growth_problems({"electrical": -5.0}, 2030, [2030, 2035]) == []
    assert load_growth_problems({}, None, [2030, 2035]) == []


def test_rate_keys_take_the_load_carrier_canonical_key():
    assert LG.canonical_rates({"AC": 2.0, "H2": 1.0}) == {
        "electrical": 2.0,
        "hydrogen": 1.0,
    }


def test_the_effective_periods_are_the_config_override_else_the_networks():
    from types import SimpleNamespace as NS

    net = NS(investment_periods=[2040, 2030])
    assert LG.effective_periods(
        NS(multi_investment_periods=False, investment_periods=[1]), net
    ) == [2030, 2040]
    assert LG.effective_periods(
        NS(multi_investment_periods=True, investment_periods=[2050]), net
    ) == [2050]
    assert (
        LG.effective_periods(
            NS(multi_investment_periods=True, investment_periods=[]), None
        )
        == []
    )


# ── R4 / C5: no rate is byte-identical in every fingerprint and digest ──────


def _ic_digest(cfg):
    from services.finance.investment_case_runner import assumptions_digest

    return assumptions_digest(
        finance=None, commercial=None, dispatch=None, packs={}, solver=cfg
    )[0]


GROWN = SolverConfig(
    multi_investment_periods=True,
    investment_periods=[2030, 2035],
    load_growth_pct_per_year={"electrical": 2.0},
    load_growth_base_year=2030,
    load_scalers_by_carrier={"electrical": {"2035": 1.104081}},
    load_scalers_derived={"electrical": ["2035"]},
)


def test_the_new_fields_never_enter_the_config_digest_input():
    keys = set(goal_free_config(SolverConfig()))
    assert not keys & {
        "load_growth_pct_per_year",
        "load_growth_base_year",
        "load_scalers_derived",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"load_scalers_derived": {}},
        {"load_growth_base_year": 2029},
        {"load_growth_pct_per_year": {"electrical": 2.0, "heat": 0.0}},
    ],
)
def test_a_growth_input_change_with_no_multiplier_change_leaves_both_digests(change):
    other = replace(GROWN, **change)
    assert json.dumps(
        goal_free_config(other), sort_keys=True, default=str
    ) == json.dumps(goal_free_config(GROWN), sort_keys=True, default=str)
    assert _ic_digest(other) == _ic_digest(GROWN)


def test_a_multiplier_the_rate_moves_moves_both_digests():
    other = replace(
        GROWN,
        load_growth_pct_per_year={"electrical": 3.0},
        load_scalers_by_carrier={"electrical": {"2035": round(1.03**5, 6)}},
    )
    assert goal_free_config(other) != goal_free_config(GROWN)
    assert _ic_digest(other) != _ic_digest(GROWN)


def test_a_bookkeeping_change_leaves_the_network_input_fingerprint(solved):  # noqa: F811
    from services import workflow_tools as W

    before = W._network_input_fingerprint(solved)
    cfg = solved.solver_state["solver_config"]
    solved.solver_state["solver_config"] = replace(
        cfg,
        load_growth_base_year=2001,
        load_scalers_derived={"electrical": ["2035"]},
        load_growth_pct_per_year={"electrical": 0.0},
    )
    assert W._network_input_fingerprint(solved) == before
    solved.solver_state["solver_config"] = replace(
        cfg, load_scalers_by_carrier={"electrical": {"2035": 1.1}}
    )
    assert W._network_input_fingerprint(solved) != before


# ── The solver-config route: the sink for the settings page and the chat tool ─


def _put(client, body):
    return client.put("/api/simulation/solver_config", json=body)


def _cfg(client):
    return client.get("/api/simulation/solver_config").json()


GROW = {
    "multi_investment_periods": True,
    "investment_periods": [2030, 2035],
    "load_growth_pct_per_year": {"electrical": 2.0},
    "load_growth_base_year": 2030,
}


def test_the_route_stores_the_rate_and_the_derived_multipliers(client):
    assert _put(client, GROW).status_code == 200
    cfg = _cfg(client)
    assert cfg["load_scalers_by_carrier"] == {"electrical": {"2035": 1.104081}}
    assert cfg["load_scalers_derived"] == {"electrical": ["2035"]}
    assert cfg["load_growth_pct_per_year"] == {"electrical": 2.0}


def test_the_route_keys_a_rate_by_the_canonical_carrier(client):
    assert (
        _put(client, {**GROW, "load_growth_pct_per_year": {"AC": 2.0}}).status_code
        == 200
    )
    assert _cfg(client)["load_growth_pct_per_year"] == {"electrical": 2.0}


def test_the_route_ignores_a_client_derived_flag(client):
    _put(client, GROW)
    r = _put(client, {"load_scalers_derived": {}})
    assert r.status_code == 200, r.text
    assert _cfg(client)["load_scalers_derived"] == {"electrical": ["2035"]}


def test_the_route_rederives_when_the_config_periods_change(client):
    _put(client, GROW)
    assert _put(client, {"investment_periods": [2030, 2040]}).status_code == 200
    assert set(_cfg(client)["load_scalers_by_carrier"]["electrical"]) == {"2040"}


def test_a_typed_cell_through_the_route_becomes_typed(client):
    _put(client, GROW)
    assert (
        _put(
            client, {"load_scalers_by_carrier": {"electrical": {"2035": 1.5}}}
        ).status_code
        == 200
    )
    cfg = _cfg(client)
    assert cfg["load_scalers_by_carrier"] == {"electrical": {"2035": 1.5}}
    assert cfg["load_scalers_derived"] == {}


@pytest.mark.parametrize(
    "body,kind",
    [
        (
            {
                "load_growth_pct_per_year": {"electrical": -100.0},
                "load_growth_base_year": 2030,
            },
            None,
        ),
        ({"load_growth_pct_per_year": {"electrical": 2.0}}, "load_growth_invalid"),
        (
            {
                "load_growth_pct_per_year": {"electrical": 2.0},
                "load_growth_base_year": 2031,
            },
            "load_growth_invalid",
        ),
        (
            {
                "load_growth_pct_per_year": {"electrical": 2.0},
                "load_growth_base_year": True,
            },
            None,
        ),
        (
            {
                "load_growth_pct_per_year": {"electrical": 2.0},
                "load_growth_base_year": "2030",
            },
            None,
        ),
        (
            {
                "load_growth_pct_per_year": {"electrical": 1e6},
                "load_growth_base_year": 1900,
            },
            "load_growth_factor_invalid",
        ),
    ],
)
def test_the_route_refuses_each_invalid_input(client, body, kind):
    _put(client, {"multi_investment_periods": True, "investment_periods": [2030, 2035]})
    r = _put(client, body)
    assert r.status_code == 422, r.text
    if kind:
        assert r.json()["detail"]["error_kind"] == kind


def test_the_route_refuses_a_non_finite_rate(client):
    # JSON has no NaN literal; Python's encoder writes one, as a lenient client might.
    r = client.put(
        "/api/simulation/solver_config",
        content=json.dumps(
            {
                "load_growth_pct_per_year": {"electrical": float("nan")},
                "load_growth_base_year": 2030,
            }
        ),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422, r.text


def test_without_a_rate_the_stored_scalers_are_untouched(client):
    typed = {"electrical": {"2030": 1.0, "2035": 1.2}}
    _put(
        client,
        {
            "multi_investment_periods": True,
            "investment_periods": [2030, 2035],
            "load_scalers_by_carrier": typed,
        },
    )
    cfg = _cfg(client)
    assert cfg["load_scalers_by_carrier"] == typed and cfg["load_scalers_derived"] == {}


# ── C1: the network's periods (the usual source) re-derive too ───────────────


@pytest.fixture
def grown_network(client, install_network):
    from tests.test_fca_load_scalers import _saved_two_period

    _saved_two_period(client, install_network, "lg_net")  # network periods 2030, 2035
    r = _put(
        client,
        {
            "multi_investment_periods": True,
            "load_growth_pct_per_year": {"electrical": 2.0},
            "load_growth_base_year": 2030,
        },
    )
    assert r.status_code == 200, r.text
    return client


def test_the_rate_derives_on_the_networks_periods(grown_network):
    assert _cfg(grown_network)["load_scalers_by_carrier"] == {
        "electrical": {"2035": 1.104081}
    }


def test_a_network_period_change_rederives(grown_network):
    r = grown_network.post(
        "/api/network/investment_periods", json={"periods": [2030, 2040]}
    )
    assert r.status_code == 200, r.text
    assert _cfg(grown_network)["load_scalers_by_carrier"] == {
        "electrical": {"2040": round(1.02**10, 6)}
    }


def test_a_network_period_before_the_start_year_is_refused_before_any_write(
    grown_network, session_ctx
):
    r = grown_network.post(
        "/api/network/investment_periods", json={"periods": [2025, 2035]}
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "load_growth_invalid"
    assert list(session_ctx(grown_network).network.investment_periods) == [2030, 2035]


def test_a_stale_stored_map_fails_the_preflight_check(grown_network, session_ctx):
    from services.validation_service import validate_for_run

    ctx = session_ctx(grown_network)
    cfg = ctx.solver_state["solver_config"]
    ctx.solver_state["solver_config"] = replace(
        cfg, load_scalers_by_carrier={"electrical": {"2035": 1.5}}
    )
    stale = replace(cfg, load_scalers_by_carrier={"electrical": {"2035": 1.5}})
    codes = [i.code for i in validate_for_run(ctx.network, stale)]
    assert "load_growth_stale" in codes
    assert "load_growth_stale" not in [
        i.code for i in validate_for_run(ctx.network, cfg)
    ]


def test_an_old_config_without_the_fields_loads_unchanged():
    old = asdict(SolverConfig())
    for k in (
        "load_growth_pct_per_year",
        "load_growth_base_year",
        "load_scalers_derived",
    ):
        old.pop(k)
    assert asdict(SolverConfig(**old)) == asdict(SolverConfig())


def test_an_undone_period_change_rederives_for_the_restored_periods(grown_network):
    # GK B addition (2): the undo stack restores the network, not the solver
    # config; the multipliers follow the restored periods.
    r = grown_network.post(
        "/api/network/investment_periods", json={"periods": [2030, 2040]}
    )
    assert r.status_code == 200, r.text
    assert set(_cfg(grown_network)["load_scalers_by_carrier"]["electrical"]) == {"2040"}
    r = grown_network.post("/api/network/undo")
    assert r.status_code == 200, r.text
    assert _cfg(grown_network)["load_scalers_by_carrier"] == {
        "electrical": {"2035": 1.104081}
    }


def test_a_period_change_no_writer_followed_blocks_the_solve_in_plain_words(
    grown_network, session_ctx
):
    # The config as derived for 2030/2040 against a network back on 2030/2035,
    # what an unhooked period writer (an undo without the hook) would leave.
    from services.validation_service import validate_for_run

    ctx = session_ctx(grown_network)
    stale = replace(
        ctx.solver_state["solver_config"],
        load_scalers_by_carrier={"electrical": {"2040": 1.218994}},
        load_scalers_derived={"electrical": ["2040"]},
    )
    issues = [
        i for i in validate_for_run(ctx.network, stale) if i.code == "load_growth_stale"
    ]
    assert issues and issues[0].severity == "error"
    assert "out of date for the periods [2030, 2035]" in issues[0].message


def test_the_route_refuses_a_rate_on_a_carrier_no_load_has(grown_network):
    r = _put(grown_network, {"load_growth_pct_per_year": {"foo": 2.0}})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "load_growth_invalid"
    assert _cfg(grown_network)["load_growth_pct_per_year"] == {"electrical": 2.0}


def test_the_route_refuses_two_aliases_of_one_carrier(client):
    r = _put(
        client,
        {
            "load_growth_pct_per_year": {"AC": 2.0, "electrical": 3.0},
            "load_growth_base_year": 2030,
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "load_growth_invalid"


# ── C7: export/import keeps the growth inputs; a network import re-derives ───


def test_a_bundle_round_trip_keeps_the_rate_start_year_and_marks(grown_network):
    before = _cfg(grown_network)
    r = grown_network.post("/api/projects/lg_net", params={"force": True})
    assert r.status_code == 200, r.text
    r = grown_network.get("/api/projects/lg_net/bundle")
    assert r.status_code == 200, r.text[:300]
    r = grown_network.post(
        "/api/projects/import_bundle",
        params={"name": "lg_copy"},
        files={"file": ("b.zip", r.content, "application/zip")},
    )
    assert r.status_code in (200, 201), r.text[:300]
    after = _cfg(grown_network)
    keys = (
        "load_growth_pct_per_year",
        "load_growth_base_year",
        "load_scalers_by_carrier",
        "load_scalers_derived",
    )
    assert {k: after[k] for k in keys} == {k: before[k] for k in keys}
    assert after["load_scalers_derived"] == {"electrical": ["2035"]}


def test_a_network_import_rederives_on_its_periods(grown_network, tmp_path):
    from tests.test_fca_load_scalers import build_edge_15min

    base = build_edge_15min()
    n = base.copy()
    n.set_snapshots(base.snapshots[:96])
    n.loads_t.p_set = base.loads_t.p_set.iloc[:96]
    n.generators_t.p_max_pu = base.generators_t.p_max_pu.iloc[:96]
    n.set_investment_periods([2030, 2040])
    path = tmp_path / "other.nc"
    n.export_to_netcdf(path)
    r = grown_network.post(
        "/api/io/import/netcdf",
        files={"file": ("other.nc", path.read_bytes(), "application/x-netcdf")},
    )
    assert r.status_code == 200, r.text[:300]
    assert _cfg(grown_network)["load_scalers_by_carrier"] == {
        "electrical": {"2040": round(1.02**10, 6)}
    }
