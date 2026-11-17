# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 16 (design §9.3 "eh_concentrator_staged"; GK A (28) GO,
Q1-Q6): eh_concentrator_staged@1 on the shipped template. The result gate
blocks on the tonne buses' mass balance; the investment case (a case the user
ran on this network, GK A (28) Q8, study-harness issue 52), the motor start,
the restart and N-1 on the site transformer warn with their numbers; the KPIs
read as warn rules.
The readers are pinned here on contexts of the test's own; the run to the
hand-off is `test_run_profile_concentrator_runs.py`."""

from __future__ import annotations

import fnmatch
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import run_gates, run_gates_concentrator as RC, run_profiles

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_concentrator as C  # noqa: E402
import eh_templates as T  # noqa: E402

REF = "eh_concentrator_staged@1"
TEMPLATE = "eh_concentrator_staged"
YEARS = C.YEARS
# Each lever at twice its template cap (eh_concentrator.py).
EDITS = {
    ("Generator", "pv_new_*", "p_nom_max"): 2 * C.PV_MAX_MW,
    ("StorageUnit", "bess_new_*", "p_nom_max"): 2 * C.BESS_MAX_MW,
    ("Store", "coarse_ore_stockpile_new_*", "e_nom_max"): 2 * C.STOCKPILE_MAX_T,
}
NEVER = [
    ("Link", "crusher"),
    ("Link", "mill_1"),
    ("Link", "mill_2"),
    ("Link", "mill_3"),
    ("Link", "flotation"),
    ("Generator", "mine"),
    ("Load", "shipments"),
    ("Load", "site_services"),
    ("Link", "grid_import"),
    ("Generator", "__lost_concentrate"),
    ("Store", "concentrate_shed"),
    ("Store", "coarse_ore_stockpile"),
]


def _rules(stage="result_gate"):
    return {r["id"]: r for r in run_profiles.resolve(REF)["stages"][stage]["rules"]}


@pytest.fixture(scope="module")
def n0():
    return T.BUILDERS[TEMPLATE]()


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

    def gate(rule_id, **ctx):
        rule = {**_rules("inputs"), **_rules()}[rule_id]
        env = run_gates.Env(
            task={},
            ctx=SimpleNamespace(**ctx),
            project=ctx.pop("project", None),
            project_dir=None,
        )
        return run_gates.evaluate([rule], env)[0]

    return gate


# ── the profile ──────────────────────────────────────────────────────────────


def test_the_profile_serves_the_concentrator_template_with_the_case_outside_the_run():
    p = run_profiles.resolve(REF)
    assert run_profiles.PROFILES[REF] is run_profiles.EH_CONCENTRATOR_STAGED
    assert p["templates"] == [TEMPLATE] and "eh_study" not in p
    solve_tools = [a["tool"] for a in p["stages"]["solve"]["actions"]]
    assert "run_investment_case" not in solve_tools
    assert [r["tool"] for r in p["envelope"]["runs"]] == [
        "solve_queue_enqueue",
        "generate_report",
    ]
    assert not any(r.get("engine") for r in p["stages"]["result_gate"]["rules"])
    assert _rules("setup")["set.project.template"]["value"] == TEMPLATE
    assert p["chapters"] == ["sizing", "investment_case", "grid_risk"]
    assert any(m.startswith("NPV by stage") for m in p["missing_tools"])
    assert any(m.startswith("N-1 on the site transformer") for m in p["missing_tools"])
    assert run_profiles.CONC_CASE_OUTSIDE in p["missing_tools"]
    assert "study-harness issue 52" in run_profiles.CONC_CASE_OUTSIDE
    assert not hasattr(run_profiles, "CONC_BASELINE_UNCOUNTED")


def test_the_mass_balance_blocks_and_the_case_the_screens_and_kpis_warn():
    rules = _rules()
    block = {k for k, r in rules.items() if r["severity"] == "block"}
    assert {"conc.mass_balance", "kpi.unserved_mwh"} <= block
    assert not {"conc.ic.counterfactual", "conc.ic.basis", "conc.kpi.npv"} & block
    for rid in (
        "conc.motor_start",
        "conc.restart",
        "conc.n_1.site_transformer",
        "conc.ic.counterfactual",
        "conc.ic.basis",
        "conc.kpi.lost_tonnes",
        "conc.kpi.shed_level",
        "conc.kpi.npv",
    ):
        assert rules[rid]["severity"] == "warn", rid
    for rid in ("conc.ic.counterfactual", "conc.ic.basis", "conc.kpi.npv"):
        assert "study-harness issue 52" in rules[rid]["basis"], rid
    assert (rules["conc.mass_balance"]["op"], rules["conc.mass_balance"]["value"]) == (
        "le",
        1e-6,
    )
    assert "LP enforces nodal balance" in rules["conc.mass_balance"]["basis"]
    assert (rules["conc.ic.basis"]["op"], rules["conc.ic.basis"]["value"]) == (
        "eq",
        "site_today",
    )
    assert (rules["conc.motor_start"]["op"], rules["conc.motor_start"]["value"]) == (
        "eq",
        "pass",
    )
    assert (rules["conc.restart"]["op"], rules["conc.restart"]["value"]) == ("eq", 0)
    assert rules["conc.n_1.site_transformer"]["op"] == "empty"
    assert "never as served" in rules["kpi.unserved_mwh"]["basis"]


def test_the_envelope_is_the_candidate_classes_at_twice_their_cap(n0):
    p = run_profiles.resolve(REF)
    edits = {
        (e["target"]["component"], e["target"]["name"], e["target"]["attr"]): e[
            "bounds"
        ]["max"]
        for e in p["envelope"]["edits"]
    }
    assert edits == EDITS
    for (comp, pattern, _), _cap in EDITS.items():
        names = [x for x in n0.static(comp).index if fnmatch.fnmatch(x, pattern)]
        assert sorted(names) == sorted(f"{pattern[:-1]}{y}" for y in YEARS), names
    assert p["envelope"]["budget"]["rounds"] == 2


@pytest.mark.parametrize("component,name", NEVER)
def test_the_process_the_demand_the_import_and_the_slack_are_never_levers(
    n0, component, name
):
    assert name in n0.static(component).index
    for e in run_profiles.resolve(REF)["envelope"]["edits"]:
        t = e["target"]
        assert not (t["component"] == component and fnmatch.fnmatch(name, t["name"])), (
            component,
            name,
        )


# ── mass balance (Q1) ────────────────────────────────────────────────────────


def _chain():
    """Two periods of three snapshots, uneven weights. A tonne bus `ore` holds one of every component kind:
    a supply Generator (`mine`, fed in), a StorageUnit, a Store, a Load, and Links at port 0 (`grind`), port 1
    (`crush`, from `rom`) and port 2 (`feed`). Dispatch balances every tonne bus exactly."""
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2027, 2030], [0, 1, 2]]))
    n.investment_periods = [2027, 2030]
    n.snapshot_weightings.loc[:, "generators"] = [1.0, 10.0, 3.0, 2.0, 7.0, 5.0]
    n.add("Bus", "el", carrier="AC")
    for b in ("rom", "ore", "out"):
        n.add("Bus", b, carrier="ore", unit="t")
    n.add("Generator", "mine", bus="rom", p_nom=100)
    n.add("Generator", "grid", bus="el", p_nom=100)
    n.add("Generator", "__lost", bus="out", p_nom=100, process_role="shortfall")
    n.add(
        "Link",
        "crush",
        bus0="el",
        bus1="ore",
        bus2="rom",
        efficiency=1.0,
        efficiency2=-1.0,
        p_nom=100,
    )
    n.add("Link", "grind", bus0="ore", bus1="out", p_nom=100)
    n.add(
        "Link",
        "feed",
        bus0="el",
        bus1="out",
        bus2="ore",
        efficiency=1.0,
        efficiency2=-1.0,
        p_nom=100,
    )
    n.add("StorageUnit", "pile_su", bus="ore", p_nom=10)
    n.add("Store", "pile", bus="ore", e_nom=10)
    n.add("Load", "spill", bus="ore")
    n.add("Load", "ship", bus="out")
    sns = n.snapshots
    mine = np.array([10.0, 12.0, 8.0, 9.0, 11.0, 10.0])
    crush = mine  # p2 = −mine (draws rom), p1 = −mine (gives ore)
    feed = np.full(6, 2.0)  # p2 draws 2 from ore, p1 gives 2 to out
    su = np.array([1.0, -1.0, 0.5, 0.0, 0.0, -0.5])
    store = np.array([-0.5, 0.5, 0.0, 1.0, -1.0, 0.0])
    spill = np.full(6, 0.25)
    grind = crush - feed + su + store - spill  # ore balance: in = grind at port 0
    lost = np.array([0.0, 1.0, 0.0, 0.0, 0.5, 0.0])
    n.generators_t.p = pd.DataFrame(
        {"mine": mine, "grid": 20.0, "__lost": lost}, index=sns
    )
    n.links_t.p0 = pd.DataFrame(
        {"crush": crush, "grind": grind, "feed": feed}, index=sns
    )
    n.links_t.p1 = pd.DataFrame(
        {"crush": -crush, "grind": -grind, "feed": -feed}, index=sns
    )
    n.links_t.p2 = pd.DataFrame({"crush": crush, "feed": feed}, index=sns)
    n.storage_units_t.p = pd.DataFrame({"pile_su": su}, index=sns)
    n.stores_t.p = pd.DataFrame({"pile": store}, index=sns)
    n.loads_t.p = pd.DataFrame({"spill": spill, "ship": grind + feed + lost}, index=sns)
    return n


def test_a_balanced_chain_passes_with_every_tonne_bus_and_period_named(direct):
    n = _chain()
    out = RC.mass_balance_of(n)
    assert out.value < 1e-12
    assert {(r["bus"], r["period"]) for r in out.rows} == {
        (b, p) for b in ("rom", "ore", "out") for p in (2027, 2030)
    }
    r = direct("conc.mass_balance", network=n)
    assert r["verdict"] == "pass" and r["severity"] == "block"


# Each kind on the tonne bus, by the frame and column a residual is added to: a reader that drops one fails.
KINDS = [
    ("generators_t", "p", "mine"),
    ("storage_units_t", "p", "pile_su"),
    ("stores_t", "p", "pile"),
    ("loads_t", "p", "spill"),
    ("links_t", "p0", "grind"),
    ("links_t", "p1", "crush"),
    ("links_t", "p2", "feed"),
]


@pytest.mark.parametrize(
    "frame,attr,name", KINDS, ids=[k[2] + "." + k[1] for k in KINDS]
)
def test_a_residual_on_any_component_kind_fails_weighted_by_its_snapshot(
    direct, frame, attr, name
):
    n = _chain()
    df = getattr(getattr(n, frame), attr).copy()
    df.iloc[1, df.columns.get_loc(name)] += 0.01  # snapshot (2027, 1), weight 10
    setattr(getattr(n, frame), attr, df)
    out = RC.mass_balance_of(n)
    worst = next(r for r in out.rows if r["worst"])
    assert worst["period"] == 2027
    fed = 1.0 * 10 + 10.0 * 12 + 3.0 * 8 + (10.0 * 0.01 if name == "mine" else 0.0)
    assert worst["residual_t"] == pytest.approx(10.0 * 0.01)
    assert worst["share"] == pytest.approx(0.1 / fed)
    r = direct("conc.mass_balance", network=n)
    assert r["verdict"] == "fail"
    assert r["facts"][0]["value"] == pytest.approx(0.1 / fed)


def test_a_residual_at_the_tolerance_passes_and_above_fails(direct):
    n = _chain()
    fed = 1.0 * 10 + 10.0 * 12 + 3.0 * 8
    for share, verdict in ((0.9e-6, "pass"), (1.1e-6, "fail")):
        m = _chain()
        df = m.loads_t.p.copy()
        df.iloc[0, df.columns.get_loc("spill")] += share * fed / 1.0  # weight 1
        m.loads_t.p = df
        assert direct("conc.mass_balance", network=m)["verdict"] == verdict, share
    assert RC.mass_balance_of(n).value < 1e-12


def test_a_period_with_nothing_fed_in_is_no_evidence(direct):
    n = _chain()
    df = n.generators_t.p.copy()
    df.loc[2030, "mine"] = 0.0
    n.generators_t.p = df
    r = direct("conc.mass_balance", network=n)
    assert r["verdict"] == "not_evaluated" and "period 2030" in r["reason"]


def test_the_shortfall_slack_is_not_fed_in(direct):
    n = _chain()
    df = n.generators_t.p.copy()
    df.loc[2030, "mine"] = 0.0
    df.loc[2030, "__lost"] = 5.0
    n.generators_t.p = df
    assert direct("conc.mass_balance", network=n)["verdict"] == "not_evaluated"


def test_an_lp_slack_without_a_role_is_not_fed_in(direct):
    """#1461 S1: a slack Generator (by its name or carrier) on a tonne bus is
    not a supply, with or without a shortfall role."""
    from services.adequacy.slack import SLACK_NAME_PREFIXES

    n = _chain()
    name = SLACK_NAME_PREFIXES[0] + "out"
    n.add("Generator", name, bus="out", p_nom=100)
    df = n.generators_t.p.copy()
    df[name] = 0.0
    df.loc[2030, "mine"] = 0.0
    df.loc[2030, name] = 5.0
    df.loc[2030, "__lost"] = 0.0
    n.generators_t.p = df
    assert direct("conc.mass_balance", network=n)["verdict"] == "not_evaluated"


@pytest.mark.parametrize(
    "frame,attr,name", KINDS, ids=[k[2] + "." + k[1] for k in KINDS]
)
def test_a_missing_flow_column_is_no_evidence(direct, frame, attr, name):
    n = _chain()
    setattr(
        getattr(n, frame), attr, getattr(getattr(n, frame), attr).drop(columns=[name])
    )
    r = direct("conc.mass_balance", network=n)
    assert r["verdict"] == "not_evaluated", r


def test_a_non_finite_flow_or_weight_is_no_evidence(direct):
    n = _chain()
    df = n.stores_t.p.copy()
    df.iloc[2, 0] = float("nan")
    n.stores_t.p = df
    assert direct("conc.mass_balance", network=n)["verdict"] == "not_evaluated"
    m = _chain()
    m.snapshot_weightings.loc[m.snapshots[0], "generators"] = -1.0
    assert direct("conc.mass_balance", network=m)["verdict"] == "not_evaluated"


def test_no_tonne_bus_is_no_evidence(direct):
    n = _chain()
    n.buses["unit"] = ""
    n.buses["carrier"] = "AC"
    assert direct("conc.mass_balance", network=n)["verdict"] == "not_evaluated"


def test_results_that_predate_the_inputs_have_no_mass_balance(direct, monkeypatch):
    import harness.calculations as calculations

    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: False)
    for rid in ("conc.mass_balance", "conc.kpi.lost_tonnes", "conc.kpi.shed_level"):
        r = direct(rid, network=_chain())
        assert (
            r["verdict"] == "not_evaluated" and r["facts"][0]["value"] == "stale_solve"
        ), rid


@pytest.mark.parametrize(
    "rule_id", ["conc.mass_balance", "conc.kpi.lost_tonnes", "conc.kpi.shed_level"]
)
def test_without_the_runs_own_solve_the_solve_readers_have_no_evidence(rule_id):
    rule = _rules()[rule_id]
    env = run_gates.Env(
        task={}, ctx=SimpleNamespace(network=None), project=None, project_dir=None
    )
    r = run_gates.evaluate([rule], env)[0]
    assert r["verdict"] == "not_evaluated"


# ── motor start and restart (Q2, Q3) ─────────────────────────────────────────


def _inputs(n):
    from services.adequacy import grid_readiness as gr

    profile, error = gr._load_profile("eu_rfg_dcc_ce", ())
    return gr._Inputs(tempfile.mkdtemp(), n, profile, error, None, None)


@pytest.fixture
def screens(monkeypatch):
    """The screen readers on `n` with the default grid code, as `_scorecard_inputs` gives them."""

    def gate(rule_id, n):
        monkeypatch.setattr(RC, "_scorecard_inputs", lambda env, net: _inputs(net))
        monkeypatch.setattr(RC, "_on_network", lambda env, read: read(n))
        rule = _rules()[rule_id]
        env = run_gates.Env(
            task={}, ctx=SimpleNamespace(network=n), project="p", project_dir=None
        )
        return run_gates.evaluate([rule], env)[0]

    return gate


def test_the_shipped_dol_mills_fail_the_motor_start_with_their_dip_and_only_warn(
    n0, screens
):
    r = screens("conc.motor_start", n0.copy())
    assert (r["verdict"], r["severity"]) == ("fail", "warn")
    detail = r["facts"][0]["detail"]
    for mill in C.MILLS:
        assert f"{mill}: ΔU 29.63 % > 3 % (dol): FAIL" in detail, detail


def _all_vfd(n0):
    """Every process unit with a motor on a VFD start (the shipped crusher and flotation carry no motor)."""
    n = n0.copy()
    units = [u for u in n.links.index if bool(n.links.at[u, "eh_process"])]
    n.links.loc[units, "start_method"] = "vfd"
    n.links.loc[[u for u in units if u not in C.MILLS], "motor_kw"] = 500.0
    return n


def test_vfd_starts_pass_the_motor_start(n0, screens):
    r = screens("conc.motor_start", _all_vfd(n0))
    assert r["verdict"] == "pass", r
    assert all(d.endswith(": PASS") for d in r["facts"][0]["detail"])


def test_vfd_mills_beside_units_with_no_motor_are_no_evidence_never_a_pass(n0, screens):
    n = n0.copy()
    n.links.loc[list(C.MILLS), "start_method"] = "vfd"
    r = screens("conc.motor_start", n)
    assert r["verdict"] == "not_evaluated" and "crusher" in r["reason"]


def test_a_motor_start_with_an_unknown_and_no_fail_is_no_evidence(n0, screens):
    n = n0.copy()
    n.links.loc[list(C.MILLS), "start_method"] = "vfd"
    n.links.loc["mill_1", "motor_kw"] = float("nan")
    r = screens("conc.motor_start", n)
    assert r["verdict"] == "not_evaluated" and "mill_1" in r["reason"]


def test_the_motor_start_reads_the_scorecards_own_rows(n0, monkeypatch):
    """Q2: the dip is never recomputed: the reader shows what `_motor_start_rows` gives."""
    from services.adequacy import grid_readiness as gr

    rows = [
        {
            "id": "motor_start:m",
            "verdict": "pass",
            "project_value": 1.5,
            "limit": 3.0,
            "motor_start": {"start_method": "vfd"},
            "note": "",
        }
    ]
    monkeypatch.setattr(gr, "_motor_start_rows", lambda inp: rows)
    out = RC.motor_start_of(_inputs(n0))
    assert out.value == "pass" and out.detail == ["m: ΔU 1.50 % ≤ 3 % (vfd): PASS"]
    rows[0].update(verdict=gr.RULE_MOTOR_START["otherwise"], project_value=4.0)
    assert RC.motor_start_of(_inputs(n0)).value == "fail"


def test_the_shipped_mills_fail_to_restart_after_a_trip_and_only_warn(n0, screens):
    r = screens("conc.restart", n0.copy())
    assert (r["verdict"], r["severity"]) == ("fail", "warn")
    assert r["facts"][0]["value"] == len(C.MILLS)
    assert any(d.startswith("lost while restarting") for d in r["facts"][0]["detail"])


def test_vfd_mills_restart(n0, screens):
    n = n0.copy()
    n.links.loc[list(C.MILLS), "start_method"] = "vfd"
    r = screens("conc.restart", n)
    # The crusher and flotation carry no restart time: unknown with no fail is no evidence, never a pass.
    assert r["verdict"] == "not_evaluated" and "restart time not set" in r["reason"]


def test_no_unit_tripping_restarts_nothing_and_passes(n0, screens):
    n = n0.copy()
    n.links["ride_through_curve"] = (
        "100:100"  # every unit rides through every dip class
    )
    r = screens("conc.restart", n)
    assert r["verdict"] == "pass", r
    assert r["facts"][0]["detail"] == ["no process unit trips on the dip classes"]


# ── N-1 on the site transformer (Q4) ─────────────────────────────────────────


def test_the_shipped_template_has_no_site_transformer_so_n_1_is_not_evaluated(
    n0, screens
):
    r = screens("conc.n_1.site_transformer", n0.copy())
    assert (r["verdict"], r["severity"]) == ("not_evaluated", "warn")
    assert r["reason"] == RC.NO_SITE_TRANSFORMER


def _with_transformers(n0, k):
    """The PoC import lands on a new `site_hv` bus; `k` transformers feed the site from it."""
    n = n0.copy()
    v = float(n.buses.at["site", "v_nom"])
    n.add("Bus", "site_hv", carrier="AC", v_nom=v)
    n.links.loc["grid_import", "bus1"] = "site_hv"
    for i in range(1, k + 1):
        n.add("Link", f"T{i}", bus0="site_hv", bus1="site", p_nom=40.0, p_min_pu=-1.0)
    return n


def test_one_site_transformer_fails_n_1_naming_the_units_it_stops(n0, screens):
    r = screens("conc.n_1.site_transformer", _with_transformers(n0, 1))
    assert (r["verdict"], r["severity"]) == ("fail", "warn")
    assert r["facts"][0]["value"] == ["T1"]
    [line] = r["facts"][0]["detail"]
    assert line.startswith("T1 out of service stops ") and "mill_1" in line


def test_two_parallel_site_transformers_pass_n_1(n0, screens):
    r = screens("conc.n_1.site_transformer", _with_transformers(n0, 2))
    assert r["verdict"] == "pass", r
    assert r["facts"][0]["detail"] == [
        "T1 out of service stops no process unit",
        "T2 out of service stops no process unit",
    ]


# ── the investment case's basis (Q5) ─────────────────────────────────────────


@pytest.mark.parametrize(
    "fact,verdict",
    [
        ({"state": "established", "basis": "site_today", "reasons": []}, "pass"),
        ({"state": "established", "basis": "analytic", "reasons": []}, "fail"),
        (
            {
                "state": "not_established",
                "basis": "site_today",
                "reasons": [
                    "counterfactual_not_established:product_load_needs_baseline"
                ],
            },
            "not_evaluated",
        ),
        ({"state": "not_run", "basis": None, "reasons": ["no_case"]}, "not_evaluated"),
    ],
)
def test_the_case_passes_only_established_against_the_site_as_today(
    direct, monkeypatch, fact, verdict
):
    from services import ic_counterfactual as ic

    monkeypatch.setattr(ic, "read", lambda ctx, on: fact)
    r = direct("conc.ic.basis")
    assert r["verdict"] == verdict and r["severity"] == "warn"
    if verdict == "not_evaluated":
        assert r["reason"] == ic.reason_words(fact)


def _stored_case(stale=False):
    from services import ic_counterfactual as ic

    return {
        "status": "done",
        "flags": [ic._SITE_TODAY],
        "report": {"present": True, "stale": stale, "assumptions_hash": "h"},
    }


@pytest.mark.parametrize(
    "case,current,reasons",
    [
        (_stored_case(), True, None),
        (_stored_case(stale=True), True, ["case_stale"]),
        (_stored_case(stale=None), True, ["case_stale"]),
        (_stored_case(), False, ["case_stale", "results_not_current"]),
        (None, True, ["no_case"]),
    ],
    ids=["fresh", "stale", "stale_unknown", "results_not_current", "absent"],
)
def test_the_case_rules_read_only_a_case_current_on_this_network(
    direct, monkeypatch, case, current, reasons
):
    """GK A (28) Q8 (1), (2): the real `classify` on the stored case. Fresh,
    the case rules pass and the NPV is its number; stale, out of date for the
    solve, or absent, each case rule is not evaluated with its reason (never
    a pass) and the NPV has no number."""
    import routers.results as results_router
    from services import chat_tools
    from services import ic_counterfactual as ic

    monkeypatch.setattr(ic, "_case", lambda ctx, on: (case, current))
    report = {"assumptions_hash": "h"}
    monkeypatch.setattr(
        results_router, "get_investment_case_report", lambda detail: report
    )
    monkeypatch.setattr(
        chat_tools,
        "_ic_headlines",
        lambda r: {
            "project_post_tax_npv_at_wacc": 1.5e6,
            "project_post_tax_irr": 0.11,
        },
    )
    monkeypatch.setattr(
        chat_tools, "_section", lambda r, key: {"payload": {"currency": "EUR"}}
    )
    rules = {
        rid: direct(rid)
        for rid in ("conc.ic.counterfactual", "conc.ic.basis", "conc.kpi.npv")
    }
    for rid, r in rules.items():
        assert r["severity"] == "warn", rid
        if reasons is None:
            assert r["verdict"] == "pass", (rid, r.get("reason"))
        else:
            assert r["verdict"] == "not_evaluated", rid
            fact = {"state": "not_run", "basis": None, "reasons": reasons}
            assert r["reason"] == ic.reason_words(fact), (rid, r["reason"])
    npv = rules["conc.kpi.npv"]["facts"][0]["value"]
    if reasons is None:
        assert npv == {"npv_at_wacc": 1.5e6, "irr": 0.11, "currency": "EUR"}
    else:
        assert npv == "not_run", npv  # the typed state, never a figure


# ── KPIs (Q6) ────────────────────────────────────────────────────────────────


def _shipped_by_slack(n0, lost_tph):
    """The template with a dispatch of the test's own: the shipments met only by the lost-production slack
    in 2027 (`lost_tph` t/h every hour), nothing lost after."""
    n = n0.copy()
    sns = n.snapshots
    p = pd.DataFrame(0.0, index=sns, columns=n.generators.index)
    p.loc[2027, "__lost_concentrate"] = lost_tph
    n.generators_t.p = p
    n.loads_t.p = pd.DataFrame(0.0, index=sns, columns=n.loads.index)
    n.loads_t.p.loc[2027, "shipments"] = lost_tph
    return n


def test_shipments_met_only_by_the_slack_show_the_lost_tonnes_and_their_value(
    n0, direct
):
    n = _shipped_by_slack(n0, 2.0)
    r = direct("conc.kpi.lost_tonnes", network=n, solver_state={})
    assert (r["verdict"], r["severity"]) == ("pass", "warn")
    rows = {row["period"]: row for row in r["facts"][0]["detail"]}
    hours = float(n.snapshot_weightings["generators"].loc[2027].sum())
    assert rows["2027"]["lost_t_per_year"] == pytest.approx(2.0 * hours)
    assert rows["2027"]["lost_eur_per_year"] == pytest.approx(
        2.0 * hours * C.LOST_EUR_PER_T
    )
    assert rows["2030"]["lost_t_per_year"] == 0.0
    assert r["facts"][0]["value"] == pytest.approx(2.0 * hours)
    # GK A (28) ask 1: such a run never reads as fully delivered. The product
    # shortfall accessor reads the whole 2027 shipment as lost, so delivered
    # tonnes are 0 < shipments; the mass balance has nothing fed in (never a
    # pass); and no rule of the profile reads a delivered or served product.
    from services.process_products import shortfalls

    w = n.snapshot_weightings["generators"]
    [short] = [s for s in shortfalls(n) if s.bus == "concentrate"]
    shipped = float((n.loads_t.p["shipments"] * w).loc[2027].sum())
    lost = float((short.p * w).loc[2027].sum())
    assert shipped == pytest.approx(2.0 * hours) and lost == pytest.approx(shipped)
    assert shipped - lost < shipped
    assert direct("conc.mass_balance", network=n)["verdict"] == "not_evaluated"
    # (kpi.unserved_mwh is electrical load only: its basis says concentrate
    # short of the shipments is never served.)
    facts = {rule["fact"] for rule in _rules().values()} - {"kpi.unserved_mwh"}
    assert not {f for f in facts if "deliver" in f or "served" in f}, facts
    assert "never as served" in _rules()["kpi.unserved_mwh"]["basis"]


def test_the_shed_level_reads_least_peak_and_last_per_period(n0, direct):
    n = n0.copy()
    e = pd.DataFrame(0.0, index=n.snapshots, columns=n.stores.index)
    e.loc[2030, RC.SHED] = np.linspace(100.0, 25_000.0, len(e.loc[2030]))
    n.stores_t.e = e
    r = direct("conc.kpi.shed_level", network=n)
    assert (r["verdict"], r["severity"]) == ("pass", "warn")
    rows = {row["period"]: row for row in r["facts"][0]["detail"]}
    assert (rows[2030]["least_t"], rows[2030]["peak_t"], rows[2030]["last_t"]) == (
        100.0,
        25_000.0,
        25_000.0,
    )
    assert r["facts"][0]["value"] == 25_000.0


def test_no_shed_is_no_evidence(n0, direct):
    n = n0.copy()
    n.stores_t.e = pd.DataFrame(index=n.snapshots)
    assert direct("conc.kpi.shed_level", network=n)["verdict"] == "not_evaluated"


# ── the no-evidence states, by name (mutants: each guard's own state) ────────


def _state(r):
    return r["verdict"], r["facts"][0]["value"]


@pytest.mark.parametrize(
    "frame,attr,name", KINDS, ids=[k[2] + "." + k[1] for k in KINDS]
)
def test_a_missing_flow_column_is_unreadable_dispatch(direct, frame, attr, name):
    n = _chain()
    setattr(
        getattr(n, frame), attr, getattr(getattr(n, frame), attr).drop(columns=[name])
    )
    assert _state(direct("conc.mass_balance", network=n)) == (
        "not_evaluated",
        "unreadable_dispatch",
    )


def test_a_link_port_with_no_flow_frame_is_unreadable_dispatch(direct):
    n = _chain()
    n.links["bus3"] = ""
    n.links.loc["grind", "bus3"] = "ore"
    assert getattr(n.links_t, "p3", None) is None or "grind" not in n.links_t.p3.columns
    assert _state(direct("conc.mass_balance", network=n)) == (
        "not_evaluated",
        "unreadable_dispatch",
    )


def test_only_bus_n_columns_are_link_ports():
    n = _chain()
    n.links["bus_note"] = "ore"
    assert RC._link_ports(n) == [(0, "bus0"), (1, "bus1"), (2, "bus2")]


def test_no_tonne_bus_is_named(direct):
    n = _chain()
    n.buses["unit"] = ""
    n.buses["carrier"] = "AC"
    assert _state(direct("conc.mass_balance", network=n)) == (
        "not_evaluated",
        "no_tonne_bus",
    )


def test_an_infinite_weight_is_unreadable_and_a_zero_weight_is_read(direct):
    n = _chain()
    n.snapshot_weightings.loc[n.snapshots[0], "generators"] = float("inf")
    assert _state(direct("conc.mass_balance", network=n)) == (
        "not_evaluated",
        "unreadable_weights",
    )
    m = _chain()
    m.snapshot_weightings.loc[m.snapshots[0], "generators"] = 0.0
    assert direct("conc.mass_balance", network=m)["verdict"] == "pass"


def test_a_missing_supply_column_is_unreadable_dispatch(direct):
    n = _chain()
    n.generators_t.p = n.generators_t.p.drop(columns=["mine"])
    assert _state(direct("conc.mass_balance", network=n)) == (
        "not_evaluated",
        "unreadable_dispatch",
    )


def test_the_screens_read_the_projects_saved_grid_code_else_the_default(
    n0, tmp_path, monkeypatch
):
    import json

    from services import campus_electrical_service as ce
    from services import project_registry

    from services.adequacy import grid_readiness as gr

    monkeypatch.setattr(project_registry, "project_dir", lambda project: tmp_path)
    load, asked = gr._load_profile, []
    monkeypatch.setattr(
        gr, "_load_profile", lambda pid, dirs: asked.append(pid) or load(pid, dirs)
    )
    env = SimpleNamespace(project="p")
    default = RC._scorecard_inputs(env, n0)
    assert (
        default.profile == load(ce.DEFAULTS["profile"], ())[0]
        and default.profile_error is None
    )
    (tmp_path / ce.SUBDIR).mkdir()
    (tmp_path / ce.SUBDIR / ce.SETTINGS_FILE).write_text(
        json.dumps({"profile": "us_ieee2800_nerc"})
    )
    saved = RC._scorecard_inputs(env, n0)
    assert saved.profile == load("us_ieee2800_nerc", ())[0] != default.profile
    (tmp_path / ce.SUBDIR / ce.SETTINGS_FILE).write_text(json.dumps({"profile": None}))
    RC._scorecard_inputs(env, n0)
    assert asked == [ce.DEFAULTS["profile"], "us_ieee2800_nerc", ce.DEFAULTS["profile"]]


@pytest.mark.parametrize(
    "text",
    ["{not json", "[]", "null", '{"profile": 3}', '{"profile": "nope"}'],
)
def test_unreadable_settings_or_an_unknown_grid_code_are_no_evidence(
    n0, tmp_path, monkeypatch, text
):
    """#1384 N2b: never the default grid code in their place."""
    from services import campus_electrical_service as ce
    from services import project_registry

    monkeypatch.setattr(project_registry, "project_dir", lambda project: tmp_path)
    (tmp_path / ce.SUBDIR).mkdir()
    (tmp_path / ce.SUBDIR / ce.SETTINGS_FILE).write_text(text)
    out = RC._scorecard_inputs(SimpleNamespace(project="p"), n0)
    assert isinstance(out, run_gates.NoEvidence) and out.value == "grid_code_unreadable"


@pytest.mark.parametrize("rule_id", ["conc.motor_start", "conc.restart"])
def test_the_screens_on_unreadable_settings_are_not_evaluated(
    n0, tmp_path, monkeypatch, direct, rule_id
):
    from services import campus_electrical_service as ce
    from services import project_registry

    monkeypatch.setattr(project_registry, "project_dir", lambda project: tmp_path)
    (tmp_path / ce.SUBDIR).mkdir()
    (tmp_path / ce.SUBDIR / ce.SETTINGS_FILE).write_text("[]")
    r = direct(rule_id, network=n0, project="p")
    assert _state(r) == ("not_evaluated", "grid_code_unreadable")


@pytest.mark.parametrize(
    "ctx,project", [(None, "p"), (SimpleNamespace(network="n"), None), (None, None)]
)
def test_the_screens_need_the_runs_context_and_project(monkeypatch, ctx, project):
    seen = []
    out = RC._on_network(SimpleNamespace(ctx=ctx, project=project), seen.append)
    assert (
        isinstance(out, run_gates.NoEvidence) and out.value == "not_run" and seen == []
    )


def test_the_screens_read_the_live_network_under_its_guard(monkeypatch):
    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    seen = []
    RC._on_network(
        SimpleNamespace(ctx=SimpleNamespace(network="n"), project="p"), seen.append
    )
    assert seen == ["n"]


def _rows(monkeypatch, rows):
    from services.adequacy import grid_readiness as gr

    monkeypatch.setattr(gr, "_motor_start_rows", lambda inp: rows)


def test_a_row_without_its_motor_detail_still_reads(n0, monkeypatch):
    _rows(
        monkeypatch,
        [
            {
                "id": "motor_start:m",
                "verdict": "pass",
                "project_value": 1.0,
                "limit": 3.0,
            }
        ],
    )
    assert RC.motor_start_of(_inputs(n0)).detail == ["m: ΔU 1.00 % ≤ 3 % (None): PASS"]


@pytest.mark.parametrize("verdict,value", [("warn", None), ("unknown", 5.0)])
def test_only_a_computed_dip_over_the_limit_fails(n0, monkeypatch, verdict, value):
    _rows(
        monkeypatch,
        [
            {
                "id": "motor_start:m",
                "verdict": verdict,
                "project_value": value,
                "limit": 3.0,
                "note": "n",
            }
        ],
    )
    out = RC.motor_start_of(_inputs(n0))
    assert isinstance(out, run_gates.NoEvidence) and out.value == "motor_start_unknown"


def test_no_process_unit_has_nothing_to_restart_or_lose(n0, direct):
    n = n0.copy()
    n.remove("Link", [u for u in n.links.index if bool(n.links.at[u, "eh_process"])])
    out = RC.restart_of(_inputs(n))
    assert isinstance(out, run_gates.NoEvidence) and out.value == "no_process_unit"
    assert _state(direct("conc.kpi.lost_tonnes", network=n, solver_state={})) == (
        "not_evaluated",
        "no_process_unit",
    )


def test_every_unit_restarting_on_a_vfd_passes_with_none_failing(n0, screens):
    n = _all_vfd(n0)
    units = [u for u in n.links.index if bool(n.links.at[u, "eh_process"])]
    n.links.loc[[u for u in units if u not in C.MILLS], "restart_time_h"] = 0.5
    r = screens("conc.restart", n)
    assert (r["verdict"], r["facts"][0]["value"]) == ("pass", 0), r


def test_no_lost_production_value_names_what_is_missing(n0, direct):
    n = _shipped_by_slack(n0, 2.0)
    n.remove("Generator", "__lost_concentrate")
    n.buses.loc["concentrate", "product_value"] = float("nan")
    r = direct("conc.kpi.lost_tonnes", network=n, solver_state={})
    assert _state(r) == ("not_evaluated", "lost_production_missing")
    assert "lost-production value" in r["reason"]


def test_a_period_with_unreadable_tonnes_is_no_evidence(n0, monkeypatch):
    from services.results import grid_risk_production as grp

    line = {
        "key": "connection",
        "status": "ok",
        "missing": [],
        "by_period": {"2027": {"t_per_year": None, "eur_per_year": None}},
    }
    monkeypatch.setattr(grp, "grid_risk_production", lambda n, lost: {"lines": [line]})
    out = RC.lost_production_of(n0, None)
    assert (
        isinstance(out, run_gates.NoEvidence) and out.value == "lost_production_missing"
    )


def test_a_shed_store_removed_or_unreadable_is_no_evidence(n0, direct):
    n = n0.copy()
    e = pd.DataFrame(0.0, index=n.snapshots, columns=n.stores.index)
    n.stores_t.e = e
    m = n.copy()
    m.remove("Store", RC.SHED)
    m.stores_t.e = e  # its level kept, the store gone: still no shed
    assert _state(direct("conc.kpi.shed_level", network=m)) == (
        "not_evaluated",
        "no_shed",
    )
    e2 = e.copy()
    e2.iloc[3, e2.columns.get_loc(RC.SHED)] = float("nan")
    n.stores_t.e = e2
    assert _state(direct("conc.kpi.shed_level", network=n)) == (
        "not_evaluated",
        "unreadable_dispatch",
    )


# ── survivors of the mutant run (each reader's own words and inputs) ─────────


def test_a_missing_line_with_nothing_named_says_it_is_not_readable(n0, monkeypatch):
    from services.results import grid_risk_production as grp

    line = {"key": "connection", "status": "missing", "missing": [], "by_period": {}}
    monkeypatch.setattr(grp, "grid_risk_production", lambda n, lost: {"lines": [line]})
    out = RC.lost_production_of(n0, None)
    assert out.reason == "the lost production is not readable"


def test_a_shed_store_with_no_solved_level_is_no_shed(n0, direct):
    n = n0.copy()
    n.stores_t.e = pd.DataFrame(
        0.0, index=n.snapshots, columns=[s for s in n.stores.index if s != RC.SHED]
    )
    assert _state(direct("conc.kpi.shed_level", network=n)) == (
        "not_evaluated",
        "no_shed",
    )


@pytest.mark.parametrize("state", [{"last_lost_load": "capture"}, None])
def test_the_lost_tonnes_read_the_solves_own_capture(direct, monkeypatch, state):
    seen = []
    monkeypatch.setattr(
        RC,
        "lost_production_of",
        lambda n, lost: seen.append(lost) or run_gates.NoEvidence("x", "x"),
    )
    direct("conc.kpi.lost_tonnes", network="n", solver_state=state)
    assert seen == [None if state is None else "capture"]


def test_unreadable_settings_say_so_not_that_the_code_does_not_load(
    n0, tmp_path, monkeypatch
):
    from services import campus_electrical_service as ce
    from services import project_registry

    monkeypatch.setattr(project_registry, "project_dir", lambda project: tmp_path)
    (tmp_path / ce.SUBDIR).mkdir()
    (tmp_path / ce.SUBDIR / ce.SETTINGS_FILE).write_text('{"profile": 3}')
    out = RC._scorecard_inputs(SimpleNamespace(project="p"), n0)
    assert "cannot be read" in out.reason


@pytest.mark.parametrize("note, words", [("n", "m: n"), (None, "m: not computed")])
def test_an_unknown_motor_start_names_its_note_or_not_computed(
    n0, monkeypatch, note, words
):
    row = {
        "id": "motor_start:m",
        "verdict": "unknown",
        "project_value": None,
        "limit": 3.0,
    }
    if note:
        row["note"] = note
    _rows(monkeypatch, [row])
    out = RC.motor_start_of(_inputs(n0))
    assert isinstance(out, run_gates.NoEvidence) and out.reason == words
