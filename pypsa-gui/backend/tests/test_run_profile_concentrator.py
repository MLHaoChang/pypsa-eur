# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 16 (design §9.3 "eh_concentrator_staged"; GK A (28) GO,
Q1-Q6): eh_concentrator_staged@1 on the shipped template. The result gate
blocks on the tonne buses' mass balance and on an investment case not
established against the site as today; the motor start, the restart and N-1
on the site transformer warn with their numbers; the KPIs read as warn rules.
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
EDITS = {("Generator", "pv_new_*", "p_nom_max"): 2 * C.PV_MAX_MW,
         ("StorageUnit", "bess_new_*", "p_nom_max"): 2 * C.BESS_MAX_MW,
         ("Store", "coarse_ore_stockpile_new_*", "e_nom_max"): 2 * C.STOCKPILE_MAX_T}
NEVER = [("Link", "crusher"), ("Link", "mill_1"), ("Link", "mill_2"), ("Link", "mill_3"), ("Link", "flotation"),
         ("Generator", "mine"), ("Load", "shipments"), ("Load", "site_services"), ("Link", "grid_import"),
         ("Generator", "__lost_concentrate"), ("Store", "concentrate_shed"), ("Store", "coarse_ore_stockpile")]


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

    monkeypatch.setattr(run_gates, "_own_solve", lambda env, read, typed=False: read(env.ctx))
    monkeypatch.setattr(run_gates, "_on", lambda ctx, fn, *args: fn(*args))
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda ctx: True)

    def gate(rule_id, **ctx):
        rule = {**_rules("inputs"), **_rules()}[rule_id]
        env = run_gates.Env(task={}, ctx=SimpleNamespace(**ctx), project=ctx.pop("project", None),
                            project_dir=None)
        return run_gates.evaluate([rule], env)[0]

    return gate


# ── the profile ──────────────────────────────────────────────────────────────


def test_the_profile_serves_the_concentrator_template_with_the_case_after_the_solve():
    p = run_profiles.resolve(REF)
    assert run_profiles.PROFILES[REF] is run_profiles.EH_CONCENTRATOR_STAGED
    assert p["templates"] == [TEMPLATE] and "eh_study" not in p
    assert [a["tool"] for a in p["stages"]["solve"]["actions"]][-1] == "run_investment_case"
    assert not any(r.get("engine") for r in p["stages"]["result_gate"]["rules"])
    assert _rules("setup")["set.project.template"]["value"] == TEMPLATE
    assert p["chapters"] == ["sizing", "investment_case", "grid_risk"]
    assert any(m.startswith("NPV by stage") for m in p["missing_tools"])
    assert any(m.startswith("N-1 on the site transformer") for m in p["missing_tools"])
    assert run_profiles.CONC_BASELINE_UNCOUNTED in p["missing_tools"]
    assert "not counted by the envelope (run_ic.SOLVES = 0" in run_profiles.CONC_BASELINE_UNCOUNTED


def test_the_mass_balance_and_the_case_block_and_the_screens_and_kpis_warn():
    rules = _rules()
    block = {k for k, r in rules.items() if r["severity"] == "block"}
    assert {"conc.mass_balance", "conc.ic.counterfactual", "conc.ic.basis", "kpi.unserved_mwh"} <= block
    for rid in ("conc.motor_start", "conc.restart", "conc.n_1.site_transformer",
                "conc.kpi.lost_tonnes", "conc.kpi.shed_level", "conc.kpi.npv"):
        assert rules[rid]["severity"] == "warn", rid
    assert (rules["conc.mass_balance"]["op"], rules["conc.mass_balance"]["value"]) == ("le", 1e-6)
    assert "LP enforces nodal balance" in rules["conc.mass_balance"]["basis"]
    assert (rules["conc.ic.basis"]["op"], rules["conc.ic.basis"]["value"]) == ("eq", "site_today")
    assert (rules["conc.motor_start"]["op"], rules["conc.motor_start"]["value"]) == ("eq", "pass")
    assert (rules["conc.restart"]["op"], rules["conc.restart"]["value"]) == ("eq", 0)
    assert rules["conc.n_1.site_transformer"]["op"] == "empty"
    assert "never as served" in rules["kpi.unserved_mwh"]["basis"]


def test_the_envelope_is_the_candidate_classes_at_twice_their_cap(n0):
    p = run_profiles.resolve(REF)
    edits = {(e["target"]["component"], e["target"]["name"], e["target"]["attr"]): e["bounds"]["max"]
             for e in p["envelope"]["edits"]}
    assert edits == EDITS
    for (comp, pattern, _), _cap in EDITS.items():
        names = [x for x in n0.static(comp).index if fnmatch.fnmatch(x, pattern)]
        assert sorted(names) == sorted(f"{pattern[:-1]}{y}" for y in YEARS), names
    assert p["envelope"]["budget"]["rounds"] == 2


@pytest.mark.parametrize("component,name", NEVER)
def test_the_process_the_demand_the_import_and_the_slack_are_never_levers(n0, component, name):
    assert name in n0.static(component).index
    for e in run_profiles.resolve(REF)["envelope"]["edits"]:
        t = e["target"]
        assert not (t["component"] == component and fnmatch.fnmatch(name, t["name"])), (component, name)


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
    n.add("Link", "crush", bus0="el", bus1="ore", bus2="rom", efficiency=1.0, efficiency2=-1.0, p_nom=100)
    n.add("Link", "grind", bus0="ore", bus1="out", p_nom=100)
    n.add("Link", "feed", bus0="el", bus1="out", bus2="ore", efficiency=1.0, efficiency2=-1.0, p_nom=100)
    n.add("StorageUnit", "pile_su", bus="ore", p_nom=10)
    n.add("Store", "pile", bus="ore", e_nom=10)
    n.add("Load", "spill", bus="ore")
    n.add("Load", "ship", bus="out")
    sns = n.snapshots
    mine = np.array([10.0, 12.0, 8.0, 9.0, 11.0, 10.0])
    crush = mine                       # p2 = −mine (draws rom), p1 = −mine (gives ore)
    feed = np.full(6, 2.0)             # p2 draws 2 from ore, p1 gives 2 to out
    su = np.array([1.0, -1.0, 0.5, 0.0, 0.0, -0.5])
    store = np.array([-0.5, 0.5, 0.0, 1.0, -1.0, 0.0])
    spill = np.full(6, 0.25)
    grind = crush - feed + su + store - spill   # ore balance: in = grind at port 0
    lost = np.array([0.0, 1.0, 0.0, 0.0, 0.5, 0.0])
    n.generators_t.p = pd.DataFrame({"mine": mine, "grid": 20.0, "__lost": lost}, index=sns)
    n.links_t.p0 = pd.DataFrame({"crush": crush, "grind": grind, "feed": feed}, index=sns)
    n.links_t.p1 = pd.DataFrame({"crush": -crush, "grind": -grind, "feed": -feed}, index=sns)
    n.links_t.p2 = pd.DataFrame({"crush": crush, "feed": feed}, index=sns)
    n.storage_units_t.p = pd.DataFrame({"pile_su": su}, index=sns)
    n.stores_t.p = pd.DataFrame({"pile": store}, index=sns)
    n.loads_t.p = pd.DataFrame({"spill": spill, "ship": grind + feed + lost}, index=sns)
    return n


def test_a_balanced_chain_passes_with_every_tonne_bus_and_period_named(direct):
    n = _chain()
    out = RC.mass_balance_of(n)
    assert out.value < 1e-12
    assert {(r["bus"], r["period"]) for r in out.rows} == {(b, p) for b in ("rom", "ore", "out")
                                                           for p in (2027, 2030)}
    r = direct("conc.mass_balance", network=n)
    assert r["verdict"] == "pass" and r["severity"] == "block"


# Each kind on the tonne bus, by the frame and column a residual is added to: a reader that drops one fails.
KINDS = [("generators_t", "p", "mine"), ("storage_units_t", "p", "pile_su"), ("stores_t", "p", "pile"),
         ("loads_t", "p", "spill"), ("links_t", "p0", "grind"), ("links_t", "p1", "crush"),
         ("links_t", "p2", "feed")]


@pytest.mark.parametrize("frame,attr,name", KINDS, ids=[k[2] + "." + k[1] for k in KINDS])
def test_a_residual_on_any_component_kind_fails_weighted_by_its_snapshot(direct, frame, attr, name):
    n = _chain()
    df = getattr(getattr(n, frame), attr).copy()
    df.iloc[1, df.columns.get_loc(name)] += 0.01   # snapshot (2027, 1), weight 10
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
        df.iloc[0, df.columns.get_loc("spill")] += share * fed / 1.0   # weight 1
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


@pytest.mark.parametrize("frame,attr,name", KINDS, ids=[k[2] + "." + k[1] for k in KINDS])
def test_a_missing_flow_column_is_no_evidence(direct, frame, attr, name):
    n = _chain()
    setattr(getattr(n, frame), attr, getattr(getattr(n, frame), attr).drop(columns=[name]))
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
        assert r["verdict"] == "not_evaluated" and r["facts"][0]["value"] == "stale_solve", rid


@pytest.mark.parametrize("rule_id", ["conc.mass_balance", "conc.kpi.lost_tonnes", "conc.kpi.shed_level"])
def test_without_the_runs_own_solve_the_solve_readers_have_no_evidence(rule_id):
    rule = _rules()[rule_id]
    env = run_gates.Env(task={}, ctx=SimpleNamespace(network=None), project=None, project_dir=None)
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
        env = run_gates.Env(task={}, ctx=SimpleNamespace(network=n), project="p", project_dir=None)
        return run_gates.evaluate([rule], env)[0]
    return gate


def test_the_shipped_dol_mills_fail_the_motor_start_with_their_dip_and_only_warn(n0, screens):
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
    rows = [{"id": "motor_start:m", "verdict": "pass", "project_value": 1.5, "limit": 3.0,
             "motor_start": {"start_method": "vfd"}, "note": ""}]
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
    n.links["ride_through_curve"] = "100:100"   # every unit rides through every dip class
    r = screens("conc.restart", n)
    assert r["verdict"] == "pass", r
    assert r["facts"][0]["detail"] == ["no process unit trips on the dip classes"]


# ── N-1 on the site transformer (Q4) ─────────────────────────────────────────


def test_the_shipped_template_has_no_site_transformer_so_n_1_is_not_evaluated(n0, screens):
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
    assert r["facts"][0]["detail"] == ["T1 out of service stops no process unit",
                                       "T2 out of service stops no process unit"]


# ── the investment case's basis (Q5) ─────────────────────────────────────────


@pytest.mark.parametrize("fact,verdict", [
    ({"state": "established", "basis": "site_today", "reasons": []}, "pass"),
    ({"state": "established", "basis": "analytic", "reasons": []}, "fail"),
    ({"state": "not_established", "basis": "site_today",
      "reasons": ["counterfactual_not_established:product_load_needs_baseline"]}, "not_evaluated"),
    ({"state": "not_run", "basis": None, "reasons": ["no_case"]}, "not_evaluated"),
])
def test_the_case_passes_only_established_against_the_site_as_today(direct, monkeypatch, fact, verdict):
    from services import ic_counterfactual as ic
    monkeypatch.setattr(ic, "read", lambda ctx, on: fact)
    r = direct("conc.ic.basis")
    assert r["verdict"] == verdict and r["severity"] == "block"
    if verdict == "not_evaluated":
        assert r["reason"] == ic.reason_words(fact)


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


def test_shipments_met_only_by_the_slack_show_the_lost_tonnes_and_their_value(n0, direct):
    n = _shipped_by_slack(n0, 2.0)
    r = direct("conc.kpi.lost_tonnes", network=n, solver_state={})
    assert (r["verdict"], r["severity"]) == ("pass", "warn")
    rows = {row["period"]: row for row in r["facts"][0]["detail"]}
    hours = float(n.snapshot_weightings["generators"].loc[2027].sum())
    assert rows["2027"]["lost_t_per_year"] == pytest.approx(2.0 * hours)
    assert rows["2027"]["lost_eur_per_year"] == pytest.approx(2.0 * hours * C.LOST_EUR_PER_T)
    assert rows["2030"]["lost_t_per_year"] == 0.0
    assert r["facts"][0]["value"] == pytest.approx(2.0 * hours)


def test_the_shed_level_reads_least_peak_and_last_per_period(n0, direct):
    n = n0.copy()
    e = pd.DataFrame(0.0, index=n.snapshots, columns=n.stores.index)
    e.loc[2030, RC.SHED] = np.linspace(100.0, 25_000.0, len(e.loc[2030]))
    n.stores_t.e = e
    r = direct("conc.kpi.shed_level", network=n)
    assert (r["verdict"], r["severity"]) == ("pass", "warn")
    rows = {row["period"]: row for row in r["facts"][0]["detail"]}
    assert (rows[2030]["least_t"], rows[2030]["peak_t"], rows[2030]["last_t"]) == (100.0, 25_000.0, 25_000.0)
    assert r["facts"][0]["value"] == 25_000.0


def test_no_shed_is_no_evidence(n0, direct):
    n = n0.copy()
    n.stores_t.e = pd.DataFrame(index=n.snapshots)
    assert direct("conc.kpi.shed_level", network=n)["verdict"] == "not_evaluated"
