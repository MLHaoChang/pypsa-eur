# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 13 (design §9.3 "eh_ammonia_staged"; GK A (24) GO, C1-C7):
eh_ammonia_staged@1. Stage 2 waits for the user's process units; the result
gate blocks on a flagged binary operational check and on a delivery shortfall
above the tolerance; the grey-NH3 investment case warns only; the coupling
report (`grid_risk`) is a chapter, listed as a gap when not established."""

from __future__ import annotations

import copy
import fnmatch
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import run_gates, run_profiles

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

REF = "eh_ammonia_staged@1"
TEMPLATE = "eh_ammonia_staged"
YEARS = (2027, 2030, 2035)
# C5: each lever at twice its inherited template cap (eh_ammonia.py).
CAPS = {
    ("Generator", "wind_new", "p_nom_max"): 150.0,
    ("Generator", "solar_new", "p_nom_max"): 80.0,
    ("StorageUnit", "bess_new", "p_nom_max"): 80.0,
    ("Link", "electrolyser_new", "p_nom_max"): 40.0,
}
LEVERS = {
    **{
        (c, f"{name}_{y}", a): 2 * cap
        for (c, name, a), cap in CAPS.items()
        for y in YEARS
    },
    ("Store", "h2_storage", "e_nom_max"): 20000.0,
    ("Store", "nh3_tank", "e_nom_max"): 12000.0,
}
# The envelope names each candidate class by its wildcard, whatever the planning years.
EDITS = {
    **{(c, f"{name}_*", a): 2 * cap for (c, name, a), cap in CAPS.items()},
    ("Store", "h2_storage", "e_nom_max"): 20000.0,
    ("Store", "nh3_tank", "e_nom_max"): 12000.0,
}
NEVER = [
    ("Link", "haber_bosch"),
    ("Load", "process_load"),
    ("Load", "nh3_delivery"),
    ("Generator", "__buy_nh3"),
    ("Generator", "__lost_nh3"),
    ("Link", "grid_import"),
    ("Link", "electrolyser"),
    ("Generator", "wind_farm"),
    ("Generator", "solar_park"),
    ("StorageUnit", "site_battery"),
]


def _rules(stage="result_gate"):
    return {r["id"]: r for r in run_profiles.resolve(REF)["stages"][stage]["rules"]}


def _network():
    return T.BUILDERS[TEMPLATE]()


# ── the profile ──────────────────────────────────────────────────────────────


def test_the_profile_serves_the_ammonia_template_without_an_eh_study_or_a_lole_loop():
    profile = run_profiles.resolve(REF)
    assert profile["templates"] == [TEMPLATE]
    assert "eh_study" not in profile and not run_profiles.needs_goal(profile)
    assert not any(r.get("engine") for r in profile["stages"]["result_gate"]["rules"])
    assert [a["tool"] for a in profile["stages"]["solve"]["actions"]] == [
        "solve_queue_enqueue"
    ]
    assert profile["stages"]["result_gate"]["agent"]["changes"] == 2
    assert profile["stages"]["result_gate"]["agent"]["rounds"] == 2
    assert "grid_risk" in profile["chapters"]
    assert profile["envelope"]["budget"]["solves"] == 3


def test_stage_2_waits_on_the_users_process_units():
    rule = _rules("inputs")["nh3.process.units"]
    assert (rule["fact"], rule["op"], rule["severity"]) == (
        "process.units",
        "nonempty",
        "block",
    )
    assert "tunable" not in rule


def test_the_check_and_the_shortfall_block_and_the_investment_case_only_warns():
    rules = _rules()
    check, short, ic = (
        rules["nh3.binary_check"],
        rules["nh3.delivery.shortfall"],
        rules["nh3.ic.counterfactual"],
    )
    assert (check["fact"], check["op"], check["value"], check["severity"]) == (
        "process.binary_check",
        "eq",
        "ok",
        "block",
    )
    # C7: the threshold compare is `le` at exactly the tolerance.
    assert (short["fact"], short["op"], short["value"], short["severity"]) == (
        "process.delivery.shortfall",
        "le",
        0.01,
        "block",
    )
    assert "1%" in short["basis"] and "tolerance" in short["basis"]
    assert (ic["fact"], ic["severity"]) == ("ic.counterfactual", "warn")
    assert {"res.solve.status", "res.fresh", "kpi.unserved_mwh"} <= set(rules)


def test_the_envelope_is_the_candidate_classes_and_the_tanks_at_twice_their_cap():
    """C5: each bound is twice the cap the template itself gives."""
    n = _network()
    edits = {
        (e["target"]["component"], e["target"]["name"], e["target"]["attr"]): e[
            "bounds"
        ]
        for e in run_profiles.resolve(REF)["envelope"]["edits"]
    }
    assert edits == {k: {"min": 0.0, "max": v} for k, v in EDITS.items()}
    for (
        component,
        name,
        attr,
    ), bound in LEVERS.items():  # each instance the wildcards reach
        assert bound == 2 * float(n.c[component].static.at[name, attr]), name


def test_the_process_units_the_demand_the_role_generators_and_built_assets_are_never_levers():
    edits = run_profiles.resolve(REF)["envelope"]["edits"]
    for component, name in NEVER:
        assert not any(
            e["target"]["component"] == component
            and fnmatch.fnmatch(name, e["target"]["name"])
            for e in edits
        ), name
    tunable = {
        f"{t['component']}.{t['attr']}" for t in run_profiles.resolve(REF)["tunable"]
    }
    assert tunable == {
        "Generator.p_nom_max",
        "StorageUnit.p_nom_max",
        "Link.p_nom_max",
        "Store.e_nom_max",
    }
    for rule in _rules().values():
        assert set(rule.get("tunable", [])) <= tunable


# ── the readers (C1-C4) ──────────────────────────────────────────────────────


@pytest.fixture
def direct(monkeypatch):
    """The readers on a context of the test's own, the run's solve taken as
    its own (`_own_solve` and the freshness check are pinned elsewhere)."""
    import harness.calculations as calculations

    monkeypatch.setattr(
        run_gates, "_own_solve", lambda env, read, typed=False: read(env.ctx)
    )
    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: True)

    def gate(rule_id, check=None, **ctx):
        if check is not None:
            ctx["solver_state"] = {"last_process_uc_check": check}
        rule = {**_rules("inputs"), **_rules()}[rule_id]
        env = run_gates.Env(
            task={}, ctx=SimpleNamespace(**ctx), project=None, project_dir=None
        )
        return run_gates.evaluate([rule], env)[0]

    return gate


def _check(status="ok", flagged=False, periods=()):
    return {
        "status": status,
        "flagged": flagged,
        "message": f"check {status}",
        "periods": list(periods),
        "worst": None,
    }


def _row(period, flagged):
    return {
        "period": period,
        "window": f"{period} week 1",
        "delta": 12.0 if flagged else 0.1,
        "pct": 0.2 if flagged else 0.001,
        "extra_shortfall": {},
        "flagged": flagged,
        "status": "ok",
    }


def test_an_ok_check_passes(direct):
    result = direct("nh3.binary_check", network=_network(), check=_check())
    assert result["verdict"] == "pass"


def test_a_flagged_check_fails_with_its_flagged_periods(direct):
    rows = [_row(2027, False), _row(2030, True), _row(2035, True)]
    result = direct(
        "nh3.binary_check",
        network=_network(),
        solver_state={},
        check=_check(flagged=True, periods=rows),
    )
    assert result["verdict"] == "fail" and result["facts"][0]["value"] == "flagged"
    assert [r["period"] for r in result["facts"][0]["detail"]] == [2030, 2035]
    assert result["facts"][0]["detail"][0]["delta"] == 12.0


@pytest.mark.parametrize("flagged", [1, "true", 0, "false", None])
def test_a_flag_that_is_not_a_bool_never_passes(direct, flagged):
    """C1: only `flagged is False` exactly passes."""
    result = direct(
        "nh3.binary_check",
        network=_network(),
        solver_state={},
        check=_check(flagged=flagged),
    )
    assert result["verdict"] == "not_evaluated"
    assert result["facts"][0]["value"] == "flagged_unreadable"


def test_an_unverified_check_is_not_evaluated_with_its_reason(direct):
    """Q2 = A: the run goes to needs_user; a lever cannot fix a timeout."""
    payload = {
        **_check("unverified"),
        "message": "binary check unverified: 2030: bin solve status warning",
    }
    result = direct("nh3.binary_check", network=_network(), check=payload)
    assert result["verdict"] == "not_evaluated"
    assert result["facts"][0]["value"] == "check_unverified"
    assert "bin solve status warning" in result["reason"]


@pytest.mark.parametrize(
    "mode, verdict",
    [("binary", "pass"), ("linearised", "not_evaluated"), ("auto", "not_evaluated")],
)
def test_no_check_passes_only_after_a_binary_plan(direct, mode, verdict):
    """C2: a binary plan is exact (no check runs); any other missing check
    is no evidence."""
    result = direct(
        "nh3.binary_check",
        network=_network(),
        solver_state={"solver_config": SimpleNamespace(process_uc=mode)},
    )
    assert result["verdict"] == verdict
    if verdict == "pass":
        assert result["facts"][0]["basis"] == run_gates.BINARY_EXACT_BASIS


def test_auto_resolving_to_binary_passes_without_a_check(direct):
    """C2 (a): `auto` with another committable unit plans with binaries."""
    n = _network()
    n.generators.at["wind_farm", "committable"] = True
    result = direct(
        "nh3.binary_check",
        network=n,
        solver_state={"solver_config": SimpleNamespace(process_uc="auto")},
    )
    assert result["verdict"] == "pass"


def test_the_shipped_process_unit_is_filled_and_an_unfilled_one_waits(direct):
    n = _network()
    assert direct("nh3.process.units", network=n)["verdict"] == "pass"
    n.links.at["haber_bosch", "efficiency"] = float("nan")
    result = direct("nh3.process.units", network=n)
    assert result["verdict"] == "fail" and result["facts"][0]["value"] == []


@pytest.mark.parametrize(
    "column, value",
    [
        ("efficiency", 0.0),
        ("efficiency", float("nan")),
        ("p_nom", 0.0),
        ("committable", False),
        ("eh_process", False),
    ],
)
def test_each_missing_form_input_leaves_no_process_unit(direct, column, value):
    """The form writes the capacity and the efficiencies (the display-only
    throughput unit and specific energy are derived, never read)."""
    n = _network()
    n.links.at["haber_bosch", column] = value
    assert direct("nh3.process.units", network=n)["facts"][0]["value"] == []


def test_a_capacity_the_solve_sizes_counts_and_a_product_without_tonnes_does_not(
    direct,
):
    n = _network()
    n.links.at["haber_bosch", "p_nom"] = 0.0
    n.links.at["haber_bosch", "p_nom_extendable"] = True
    assert direct("nh3.process.units", network=n)["facts"][0]["value"] == [
        "haber_bosch"
    ]
    n.buses.at["nh3", "mwh_per_t"] = float("nan")
    assert direct("nh3.process.units", network=n)["facts"][0]["value"] == []


def test_the_display_only_form_fields_are_never_read(direct):
    n = _network()
    n.links.at["haber_bosch", "throughput_unit"] = ""
    n.links.at["haber_bosch", "specific_energy_kwh_per_t"] = float("nan")
    assert direct("nh3.process.units", network=n)["facts"][0]["value"] == [
        "haber_bosch"
    ]


def _solved(n, lost=None, buy=None):
    """The template's network with a delivery, buy-in and shortfall
    dispatch of the test's own: `lost`/`buy` map a snapshot position to MW."""
    sns = n.snapshots
    n.loads_t.p = n.loads_t.p_set.reindex(index=sns, columns=n.loads.index).fillna(0.0)
    p = n.generators_t.p.reindex(index=sns, columns=n.generators.index).fillna(0.0)
    for name, values in (("__lost_nh3", lost or {}), ("__buy_nh3", buy or {})):
        for i, v in values.items():
            p.iloc[i, p.columns.get_loc(name)] = v
    n.generators_t.p = p
    return n


def _period_slice(n, year):
    sns = n.snapshots
    return [i for i, s in enumerate(sns) if s[0] == year]


def _expected_share(n, year, lost):
    """C3 by hand: Σ_w·p / mwh_per_t ÷ (Σ_w / 24), shortfall over target."""
    w = n.snapshot_weightings["generators"]
    idx = _period_slice(n, year)
    days = sum(float(w.iloc[i]) for i in idx) / 24.0
    tf = 1.0 / float(n.buses.at["nh3", "mwh_per_t"])
    target = (
        sum(float(w.iloc[i]) * float(n.loads_t.p["nh3_delivery"].iloc[i]) for i in idx)
        * tf
        / days
    )
    short = sum(float(w.iloc[i]) * lost.get(i, 0.0) for i in idx) * tf / days
    return short / target


def _uneven(n):
    """C3: uneven generator weightings inside each period (uniform ones cancel)."""
    w = n.snapshot_weightings["generators"].copy()
    for year in YEARS:
        for k, i in enumerate(_period_slice(n, year)):
            w.iloc[i] = 1.0 + 9.0 * (k % 2)
    n.snapshot_weightings["generators"] = w
    return n


def test_the_shortfall_reads_the_worst_period_weighted_by_snapshot(direct):
    """C3: one period fails, the others are clear; the shortfall sits in the
    heavily weighted snapshots, so dropping the weights changes the value."""
    n = _uneven(_network())
    idx = _period_slice(n, 2030)
    delivery = float(n.loads_t.p_set["nh3_delivery"].iloc[idx[0]])
    lost = {i: 0.2 * delivery for i in idx if (idx.index(i) % 2) == 1}
    _solved(n, lost=lost)
    result = direct("nh3.delivery.shortfall", network=n)
    expected = _expected_share(n, 2030, lost)
    assert result["verdict"] == "fail"
    assert result["facts"][0]["value"] == pytest.approx(expected, rel=1e-9)
    unweighted = sum(lost.values()) / (delivery * len(idx))
    assert abs(expected - unweighted) > 0.01
    rows = {r["period"]: r for r in result["facts"][0]["rows"]}
    assert (
        rows[2030]["worst"]
        and not rows[2027]["worst"]
        and rows[2027]["shortfall_tpd"] == 0.0
    )
    assert rows[2030]["target_tpd"] == pytest.approx(
        100.0, rel=1e-6
    )  # DELIVERY_TPD[2030]


def test_a_buy_in_alone_meets_delivery_and_is_shown(direct):
    """C4: grey NH3 bought in is shown, never counted as short."""
    n = _network()
    idx = _period_slice(n, 2035)
    _solved(n, buy={i: 5.0 for i in idx})
    result = direct("nh3.delivery.shortfall", network=n)
    assert result["verdict"] == "pass" and result["facts"][0]["value"] == 0.0
    rows = {r["period"]: r for r in result["facts"][0]["rows"]}
    assert rows[2035]["buy_in_tpd"] > 0


def test_only_the_shortfall_counts_when_buy_in_and_shortfall_share_a_period(direct):
    n = _uneven(_network())
    idx = _period_slice(n, 2027)
    lost = {i: 0.5 for i in idx}
    _solved(n, lost=lost, buy={i: 50.0 for i in idx})
    result = direct("nh3.delivery.shortfall", network=n)
    assert result["facts"][0]["value"] == pytest.approx(
        _expected_share(n, 2027, lost), rel=1e-9
    )


def test_a_renamed_shortfall_generator_is_read_by_its_role(direct):
    """C3: the role column, never the name."""
    n = _network()
    row = n.generators.loc["__lost_nh3"]
    n.remove("Generator", "__lost_nh3")
    n.add(
        "Generator",
        "contract_penalty",
        bus="nh3",
        carrier=row["carrier"],
        p_nom=row["p_nom"],
        marginal_cost=row["marginal_cost"],
    )
    n.generators.at["contract_penalty", "process_role"] = "shortfall"
    idx = _period_slice(n, 2027)
    delivery = float(n.loads_t.p_set["nh3_delivery"].iloc[idx[0]])
    n.loads_t.p = n.loads_t.p_set.reindex(
        index=n.snapshots, columns=n.loads.index
    ).fillna(0.0)
    p = n.generators_t.p.reindex(index=n.snapshots, columns=n.generators.index).fillna(
        0.0
    )
    for i in idx:
        p.iloc[i, p.columns.get_loc("contract_penalty")] = 0.5 * delivery
    n.generators_t.p = p
    result = direct("nh3.delivery.shortfall", network=n)
    assert result["verdict"] == "fail" and result["facts"][0]["value"] == pytest.approx(
        0.5, rel=1e-9
    )


def test_a_shortfall_named_but_without_its_role_is_no_evidence(direct):
    n = _solved(_network())
    n.generators["process_role"] = ""
    result = direct("nh3.delivery.shortfall", network=n)
    assert (
        result["verdict"] == "not_evaluated"
        and result["facts"][0]["value"] == "no_shortfall_role"
    )


def test_a_shortfall_at_exactly_the_tolerance_passes(direct, monkeypatch):
    monkeypatch.setitem(
        run_gates.FACTS,
        "process.delivery.shortfall",
        run_gates.Fact("t", "v", "p.u.", lambda env: run_profiles.NH3_SHORTFALL_MAX),
    )
    assert direct("nh3.delivery.shortfall", network=None)["verdict"] == "pass"


def test_an_unreadable_weight_or_dispatch_reads_no_shortfall(direct):
    n = _solved(_network())
    n.snapshot_weightings.iloc[
        3, n.snapshot_weightings.columns.get_loc("generators")
    ] = -1.0
    result = direct("nh3.delivery.shortfall", network=n)
    assert (result["verdict"], result["facts"][0]["value"]) == (
        "not_evaluated",
        "unreadable_weights",
    )
    n = _solved(_network())
    n.generators_t.p.iloc[2, n.generators_t.p.columns.get_loc("__lost_nh3")] = float(
        "nan"
    )
    result = direct("nh3.delivery.shortfall", network=n)
    assert (result["verdict"], result["facts"][0]["value"]) == (
        "not_evaluated",
        "unreadable_dispatch",
    )


def test_results_that_predate_the_inputs_read_no_shortfall(direct, monkeypatch):
    """The freshness guard: a stale solve is no evidence with its own reason."""
    import harness.calculations as calculations

    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: False)
    result = direct("nh3.delivery.shortfall", network=_solved(_network()))
    assert (result["verdict"], result["facts"][0]["value"]) == (
        "not_evaluated",
        "stale_solve",
    )


@pytest.mark.parametrize("rule_id", ["nh3.binary_check", "nh3.delivery.shortfall"])
def test_without_the_runs_own_solve_neither_reader_has_evidence(rule_id):
    """Through the real `_own_solve`: a run with no solve step reads nothing,
    whatever check or dispatch the context holds."""
    ctx = SimpleNamespace(
        network=_solved(_network()), solver_state={"last_process_uc_check": _check()}
    )
    env = run_gates.Env(task={"steps": []}, ctx=ctx, project=None, project_dir=None)
    result = run_gates.evaluate([_rules()[rule_id]], env)[0]
    assert (result["verdict"], result["facts"][0]["value"]) == (
        "not_evaluated",
        "not_solved",
    )


def test_the_check_rule_goes_to_the_user_when_unevaluated_and_the_key_takes_one_value():
    assert _rules()["nh3.binary_check"]["unevaluated"] == run_gates.UNEVALUATED_TO_USER
    assert "unevaluated" not in _rules()["nh3.delivery.shortfall"]
    rule = {k: v for k, v in _rules()["nh3.binary_check"].items() if k != "reads"}
    with pytest.raises(run_gates.RuleError):
        run_gates.check_rule({**rule, "unevaluated": "agent"})


@pytest.mark.parametrize("severity", ["warn"])
def test_the_key_is_refused_on_a_rule_that_never_fails_the_stage(severity):
    """GK B (51) F2: only a failing (block) rule ends the stage on the key."""
    rule = {k: v for k, v in _rules()["nh3.binary_check"].items() if k != "reads"}
    with pytest.raises(run_gates.RuleError, match="block rule only"):
        run_gates.check_rule({**rule, "severity": severity})


def test_the_key_is_refused_outside_the_result_gate():
    """GK B (51) F2: no other stage reads the key, so none may carry it."""
    profile = copy.deepcopy(run_profiles.PROFILES[REF])
    rule = {k: v for k, v in _rules()["nh3.binary_check"].items() if k != "reads"}
    profile["stages"].setdefault("input_gate", {}).setdefault("rules", []).append(
        {**rule, "id": "nh3.binary_check.early"})
    with pytest.raises(run_profiles.ProfileError, match="only in the result gate"):
        run_profiles.load(profile)


def _gate_row(rule, severity, verdict):
    return {"rule": rule, "severity": severity, "verdict": verdict,
            "facts": [{"value": "check_unverified" if verdict == "not_evaluated" else 0.2}]}


def _at_result_gate():
    return {"steps": [], "run": {"stage": "result_gate",
                                 "stages": {"result_gate": {"status": "active", "attempts": 1}}}}


@pytest.mark.parametrize("agent", [True, False])
def test_a_keyed_rule_not_evaluated_stops_before_any_loop_or_envelope(tmp_path, agent):
    """GK B (51) F1, F3: with another block rule failing too, the stage ends on
    `not_evaluated` before the envelope check (none is live here) and before a
    loop exists, with or without an agent; the gate keeps the other fail."""
    from harness import run_loops
    profile = copy.deepcopy(run_profiles.resolve(REF))
    if not agent:
        profile["stages"]["result_gate"].pop("agent", None)
    task = _at_result_gate()
    gate = {"verdict": "fail", "rules": [_gate_row("nh3.binary_check", "block", "not_evaluated"),
                                         _gate_row("nh3.delivery.shortfall", "block", "fail")]}
    before = copy.deepcopy(gate)
    assert run_loops.after_failed_result(tmp_path / "task.json", task, gate, profile) is False
    assert task["run"]["stages"]["result_gate"]["reason"] == "not_evaluated"
    assert "loop" not in task["run"] and "loops" not in task["run"]
    assert gate == before


def test_a_keyed_rule_that_does_not_fail_the_gate_never_stops_it(tmp_path):
    """GK B (51) F2: a keyed rule outside `failing` (a warn, which the profile
    loader refuses, built here by hand) does not end the stage on not_evaluated."""
    from harness import run_loops
    profile = copy.deepcopy(run_profiles.resolve(REF))
    warn = {**profile["stages"]["result_gate"]["rules"][0], "id": "x.warn", "severity": "warn",
            "unevaluated": run_gates.UNEVALUATED_TO_USER}
    profile["stages"]["result_gate"]["rules"].append(warn)
    task = _at_result_gate()
    gate = {"verdict": "fail", "rules": [_gate_row("x.warn", "warn", "not_evaluated"),
                                         _gate_row("nh3.delivery.shortfall", "block", "fail")]}
    assert run_loops.after_failed_result(tmp_path / "task.json", task, gate, profile) is False
    assert task["run"]["stages"]["result_gate"]["reason"] == "no_envelope"


def test_the_profile_lists_what_blocks_an_unattended_run():
    """Design §9.3 "Missing": the binary check has no chat read; the form is the user's."""
    missing = run_profiles.resolve(REF)["missing_tools"]
    assert any("binary operational check" in m for m in missing)
    assert any("process-unit form" in m for m in missing)


@pytest.mark.parametrize(
    "solver_state",
    [
        {
            "last_process_uc_check": {
                "status": "ok",
                "flagged": False,
                "message": "",
                "periods": [],
                "worst": None,
            }
        },
        {"solver_config": SimpleNamespace(process_uc="binary")},
    ],
    ids=["ok_check", "binary_plan_without_check"],
)
def test_a_stale_solve_reads_no_binary_check(direct, monkeypatch, solver_state):
    """GK A (24) B2: inputs edited since the solve (e.g. `process_uc`) leave
    neither the check nor the binary-plan pass standing."""
    import harness.calculations as calculations

    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: False)
    result = direct("nh3.binary_check", network=_network(), solver_state=solver_state)
    assert (result["verdict"], result["facts"][0]["value"]) == (
        "not_evaluated",
        "stale_solve",
    )
