# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness issue 06: the explore stage (design §3 stage 6, §6.7;
owner decisions AI-558, AI-567).

The agent proposes at most 4 cases with at most 2 changes each, every
change with a sourced range; harness code admits them into `run.cases`,
all or nothing. Network cases run through `run_sensitivity_sweep`,
investment cases through the decision-study runner on study-owned forks;
each case is judged by its own result gate on its own project. A failed
case does not fail the run; the base case still does. A case that needs an
absent domain adapter is `not_runnable` with its reason.

Written red first (SH-06 plan); `harness/run_explore.py` lands with the
build once issue 05 is done."""

# ruff: noqa: F811 -- imported pytest fixtures
import copy
import json

import pytest
from harness import run_explore, runner as harness_runner
from services import run_envelope, run_gates, run_profiles, solve_queue, workflow_tools
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    session,
)
from tests.test_harness_envelope import BLOCK, envelope_of, granted, on_run, profile  # noqa: F401
from tests.test_harness_envelope_atomic import ATOMIC, eh, writer  # noqa: F401
from tests.test_harness_immutable_solve import held_queue  # noqa: F401
from tests.test_harness_run_loops import Script, call, go, rated, say  # noqa: F401
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import Clicks, detached, frames_of, settle, task_path  # noqa: F401

# ── A profile with explore levers on the test network's shapes ──────────────

LEVERS = [
    {
        "component": "Link",
        "name": "poc_link",
        "attr": "p_nom",
        "unit": "MW",
        "explore_only": True,
        "bounds": {"min": 0.0, "max": 200.0},
        "basis": "The site's grid connection, as built.",
    },
    {
        "component": "Generator",
        "name": "G1",
        "attr": "p_nom_max",
        "unit": "MW",
        "bounds": {"min": 0.0, "max": 120.0},
        "basis": "Genset sizes the template offers.",
    },
    {
        "component": "StorageUnit",
        "name": "S1",
        "attr": "p_nom_max",
        "unit": "MW",
        "bounds": {"min": 0.0, "max": 80.0},
        "basis": "BESS power the template offers.",
    },
    {
        "component": "StorageUnit",
        "name": "S1",
        "attr": "max_hours",
        "unit": "h",
        "bounds": {"min": 1.0, "max": 8.0},
        "basis": "BESS durations the template offers.",
    },
]


def explore_profile(**over):
    base = copy.deepcopy(run_profiles.EH_SITE)
    base["explore"] = {
        "levers": copy.deepcopy(LEVERS),
        "study": {"question_id": "bess_at_site"},
        **over,
    }
    return run_profiles.load(base)


def change(component, name, attr, value, low, high, unit, source):
    return {
        "component": component,
        "name": name,
        "attr": attr,
        "value": value,
        "range": {"low": low, "high": high, "unit": unit},
        "source": source,
    }


def genset(value=90.0, source="profile:Generator.G1.p_nom_max"):
    return change("Generator", "G1", "p_nom_max", value, 60.0, 120.0, "MW", source)


def bess(value=40.0, hours=4.0):
    return [
        change(
            "StorageUnit",
            "S1",
            "p_nom_max",
            value,
            20.0,
            80.0,
            "MW",
            "profile:StorageUnit.S1.p_nom_max",
        ),
        change(
            "StorageUnit",
            "S1",
            "max_hours",
            hours,
            2.0,
            8.0,
            "h",
            "profile:StorageUnit.S1.max_hours",
        ),
    ]


def connection(value=150.0):
    return change(
        "Link",
        "poc_link",
        "p_nom",
        value,
        100.0,
        200.0,
        "MW",
        "profile:Link.poc_link.p_nom",
    )


def case(name, *changes, engine="network"):
    return {"name": name, "engine": engine, "changes": list(changes)}


def three():
    return [
        case("connection", connection()),
        case("bess", *bess()),
        case("genset", genset()),
    ]


def empty_run(**over):
    return {"cases": [], "ledger": [], "research": {"rows": []}, **over}


def base_value(target):
    return {"p_nom": 100.0, "p_nom_max": 60.0, "max_hours": 2.0}[target["attr"]]


def admit(run, cases, profile=None, solves_left=10):
    return run_explore.admit(
        run,
        profile or explore_profile(),
        cases,
        solves_left=solves_left,
        base_value=base_value,
    )


def refused(kind, run, cases, **kw):
    before = json.dumps(run, sort_keys=True)
    with pytest.raises(run_explore.ExploreError) as err:
        admit(run, cases, **kw)
    assert err.value.kind == kind, err.value
    assert json.dumps(run, sort_keys=True) == before  # all or nothing: nothing admitted
    return err.value


# ── AI-558 caps: refused at proposal ────────────────────────────────────────


def test_three_sourced_cases_are_admitted_with_one_ledger_row_per_change():
    run = empty_run()
    admit(run, three())
    assert [c["name"] for c in run["cases"]] == ["connection", "bess", "genset"]
    assert all(
        c["status"] == "admitted" and c["engine"] == "network" for c in run["cases"]
    )
    assert [len(c["changes"]) for c in run["cases"]] == [1, 2, 1]
    ids = [i for c in run["cases"] for i in c["changes"]]
    rows = {r["id"]: r for r in run["ledger"]}
    assert set(ids) == set(rows) and len(ids) == 4
    row = rows[run["cases"][2]["changes"][0]]
    assert (row["stage"], row["loop"], row["case"]) == ("explore", "explore", "genset")
    assert row["target"] == {
        "component": "Generator",
        "name": "G1",
        "attr": "p_nom_max",
    }
    assert (row["old"], row["new"], row["unit"], row["source"]) == (
        60.0,
        90.0,
        "MW",
        "profile:Generator.G1.p_nom_max",
    )
    assert row["range"] == {"low": 60.0, "high": 120.0, "unit": "MW"}


def test_a_fifth_case_is_refused_at_proposal():
    five = three() + [case("genset_hi", genset(110.0)), case("genset_lo", genset(70.0))]
    refused("explore_case_cap", empty_run(), five)


def test_a_fifth_case_across_two_proposals_is_refused_and_the_first_three_stay():
    run = empty_run()
    admit(run, three())
    refused(
        "explore_case_cap",
        run,
        [case("genset_hi", genset(110.0)), case("genset_lo", genset(70.0))],
    )
    assert len(run["cases"]) == 3


def test_a_third_change_in_a_case_is_refused_at_proposal():
    refused("explore_change_cap", empty_run(), [case("all", connection(), *bess())])


def test_a_case_name_already_admitted_is_refused():
    run = empty_run()
    admit(run, [case("genset", genset())])
    refused("explore_case_name", run, [case("genset", genset(100.0))])


# ── Sourced ranges (AI-558; SH-RESEARCH-1) ──────────────────────────────────


def test_a_change_without_a_source_is_refused():
    bare = genset()
    bare.pop("source")
    refused("explore_range_unsourced", empty_run(), [case("genset", bare)])


@pytest.mark.parametrize(
    "source",
    ["", "model:I think so", "profile:Generator.G2.p_nom_max", "research:nope"],
)
def test_a_source_that_does_not_resolve_is_refused(source):
    refused(
        "explore_range_unsourced", empty_run(), [case("genset", genset(source=source))]
    )


def test_a_value_outside_its_range_is_refused():
    refused(
        "explore_value_out_of_range", empty_run(), [case("genset", genset(value=130.0))]
    )


def test_a_range_wider_than_the_levers_bounds_is_refused():
    wide = change(
        "Generator",
        "G1",
        "p_nom_max",
        90.0,
        0.0,
        500.0,
        "MW",
        "profile:Generator.G1.p_nom_max",
    )
    refused("explore_value_out_of_range", empty_run(), [case("genset", wide)])


def test_a_range_in_another_unit_is_refused_not_converted():
    kw = change(
        "Generator",
        "G1",
        "p_nom_max",
        90000.0,
        60000.0,
        120000.0,
        "kW",
        "profile:Generator.G1.p_nom_max",
    )
    refused("explore_value_out_of_range", empty_run(), [case("genset", kw)])


def test_a_target_that_is_not_a_lever_is_refused():
    load = change("Load", "L1", "p_set", 10.0, 5.0, 20.0, "MW", "profile:Load.L1.p_set")
    refused("explore_not_a_lever", empty_run(), [case("demand", load)])


@pytest.mark.parametrize("attr", ["target_lole_h", "p_set", "fault_level_mva"])
def test_a_never_tunable_lever_is_refused_at_profile_load(attr):
    with pytest.raises(run_profiles.ProfileError):
        explore_profile(
            levers=[
                {
                    "component": "Generator",
                    "name": "G1",
                    "attr": attr,
                    "unit": "MW",
                    "bounds": {"min": 0.0, "max": 1.0},
                    "basis": "x",
                }
            ]
        )


GAP_TARGET = {"component": "Generator", "name": "G1", "attr": "p_nom_max"}


def research_row(
    row_id,
    low,
    high,
    *,
    status="confirmed",
    verification="verified",
    unit="MW",
    target=GAP_TARGET,
):
    """A SH-29 range row (issue 06 E11): `value` null, `range{low, high}`."""
    return {
        "id": row_id,
        "for": {**target, "gap": "explore:G1.p_nom_max"},
        "value": None,
        "range": {"low": low, "high": high},
        "unit": unit,
        "basis": "nameplate",
        "status": status,
        "verification": verification,
        "url": "https://example.org/a",
        "quote": f"{low} to {high} MW",
    }


def research_profile():
    """A range row is matched to its lever by target (SH-29: the gap's target
    is the lever's component, name and attr)."""
    return explore_profile()


def test_a_range_from_a_confirmed_verified_research_row_is_admitted():
    run = empty_run(research={"rows": [research_row("r-1", 60.0, 120.0)]})
    admit(
        run, [case("genset", genset(source="research:r-1"))], profile=research_profile()
    )
    [row] = run["ledger"]
    assert row["source"] == "research:r-1"


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "proposed"},
        {"status": "rejected"},
        {"verification": "unverified"},
        {"unit": "kW"},
        {"target": {"component": "StorageUnit", "name": "S1", "attr": "p_nom_max"}},
    ],
)
def test_a_research_range_from_an_unsettled_unverified_or_foreign_row_is_refused(bad):
    run = empty_run(research={"rows": [research_row("r-1", 60.0, 120.0, **bad)]})
    refused(
        "explore_range_unsourced",
        run,
        [case("genset", genset(source="research:r-1"))],
        profile=research_profile(),
    )


def test_a_change_whose_range_differs_from_its_research_row_is_refused():
    run = empty_run(research={"rows": [research_row("r-1", 70.0, 120.0)]})
    refused(
        "explore_range_unsourced",
        run,
        [case("genset", genset(source="research:r-1"))],
        profile=research_profile(),
    )


def test_e2_a_row_rejected_between_proposal_and_admission_refuses():
    """E2: the row is re-read under the lock at admission, never cached from
    the proposal."""
    run = empty_run(research={"rows": [research_row("r-1", 60.0, 120.0)]})
    proposal = [case("genset", genset(source="research:r-1"))]
    run_explore.check_proposal(
        run, research_profile(), proposal
    )  # sourced when proposed
    run["research"]["rows"][0]["status"] = "rejected"  # the user rejected it meanwhile
    with pytest.raises(run_explore.ExploreError) as err:
        run_explore.admit(
            run, research_profile(), proposal, solves_left=10, base_value=base_value
        )
    assert err.value.kind == "explore_range_unsourced" and run["cases"] == []


# ── E7: a lever is never a tunable, both ways ───────────────────────────────


@pytest.mark.parametrize("tunable", [{"component": "Link", "attr": "p_nom"}])
def test_e7_a_profile_listing_an_explore_only_lever_as_tunable_is_refused_at_load(
    tunable,
):
    """E7: the connection (`poc_link p_nom`) changes forks only; it is never
    an L1/L2 tunable. (Levers that eh_site@1 already tunes in L2, its size
    caps, stay both: GK B ruling pending.)"""
    p = copy.deepcopy(run_profiles.EH_SITE)
    p["explore"] = {"levers": copy.deepcopy(LEVERS)}
    p["tunable"] = [*p["tunable"], tunable]
    with pytest.raises(run_profiles.ProfileError):
        run_profiles.load(p)


# ── E8: propose_cases only in explore turns ─────────────────────────────────


def test_e8_propose_cases_outside_an_explore_turn_refuses():
    from harness.catalogue import TOOLS
    from services import chat_tools

    assert "propose_cases" in {t["name"] for t in TOOLS}
    with pytest.raises(Exception) as err:
        chat_tools.DISPATCHERS["propose_cases"](cases=three())
    assert "explore" in str(err.value)


# ── Engines and the solve budget ────────────────────────────────────────────


def test_cases_needing_more_solves_than_are_left_are_refused():
    refused("explore_solve_budget", empty_run(), three(), solves_left=2)


def test_a_case_whose_domain_has_no_adapter_is_admitted_not_runnable_and_needs_no_solve():
    run = empty_run()
    admit(
        run,
        [case("ppa", genset(), engine="commercial"), case("genset", genset())],
        solves_left=1,
    )
    ppa, gen = run["cases"]
    assert ppa["status"] == "not_runnable" and "commercial" in ppa["reason"]
    assert gen["status"] == "admitted"


class _Band:
    def __init__(self, low, high, source):
        self.low, self.high, self.source = low, high, source


class _Row:
    def __init__(self, key, value, unit, band):
        self.key, self.value, self.unit, self.range = key, value, unit, band


INTAKE = {"site": {"connection_mw": 10.0}}


@pytest.fixture
def ledger(monkeypatch):
    """The study's seeded ledger: one sourced band, one assumed band."""
    rows = {
        "battery_capex": _Row(
            "battery_capex", 300.0, "EUR/kWh", _Band(200.0, 400.0, "source")
        ),
        "pv_capex": _Row("pv_capex", 600.0, "EUR/kWp", _Band(420.0, 780.0, "assumed")),
    }
    monkeypatch.setattr(run_explore, "seeded_ledger", lambda question_id, intake: rows)
    monkeypatch.setattr(run_explore, "study_solves", lambda question_id, intake: 3)
    from services.study import packs

    monkeypatch.setattr(packs, "missing_inputs", lambda intake: [])
    return rows


def capex(value=250.0, low=200.0, high=400.0, key="battery_capex", unit="EUR/kWh"):
    return {
        "key": key,
        "value": value,
        "range": {"low": low, "high": high, "unit": unit},
        "source": f"ledger:{key}",
    }


def invest(*changes, intake=INTAKE):
    return {
        "name": "bess_invest",
        "engine": "study",
        "intake": intake,
        "changes": list(changes),
    }


def test_an_investment_case_names_the_profiles_question_and_reserves_its_options(
    ledger,
):
    run = empty_run()
    admit(run, [invest(capex())])
    [c] = run["cases"]
    assert (
        c["engine"] == "study"
        and c["question_id"] == "bess_at_site"
        and c["solves"]["reserved"] == 3
    )
    [row] = run["ledger"]
    assert row["target"] == {"ledger_key": "battery_capex"} and (
        row["old"],
        row["new"],
    ) == (300.0, 250.0)


@pytest.mark.parametrize(
    "bad,kind",
    [
        (
            capex(key="pv_capex", value=600.0, low=420.0, high=780.0, unit="EUR/kWp"),
            "explore_range_unsourced",
        ),
        (capex(low=100.0), "explore_range_unsourced"),
        (capex(value=450.0), "explore_value_out_of_range"),
        (capex(key="nope"), "explore_not_a_lever"),
    ],
)
def test_an_investment_change_needs_the_ledgers_sourced_band(ledger, bad, kind):
    refused(kind, empty_run(), [invest(bad)])


def test_an_investment_case_with_more_option_solves_than_left_is_refused(ledger):
    refused("explore_solve_budget", empty_run(), [invest(capex())], solves_left=2)


def test_an_investment_case_is_not_runnable_without_a_study_question():
    run = empty_run()
    admit(run, [invest(capex())], profile=explore_profile(study=None))
    assert run["cases"][0]["status"] == "not_runnable"


def test_the_explore_stage_offers_one_proposal_turn_with_the_ai558_caps():
    stage = explore_profile()["stages"]["explore"]
    assert (
        stage["agent"]["cases"] == 4
        and stage["agent"]["changes"] == 2
        and stage["agent"]["rounds"] == 1
    )
    assert "ex.cases.settled" in {r["id"] for r in stage["rules"]}
    with pytest.raises(run_profiles.ProfileError):
        p = copy.deepcopy(run_profiles.EH_SITE)
        p["stages"]["explore"] = {
            "actions": [],
            "rules": stage["rules"],
            "agent": {**stage["agent"], "cases": 5},
        }
        run_profiles.load(p)


# ── The explore gate: settled, not passed ───────────────────────────────────


@pytest.mark.parametrize(
    "statuses,unsettled",
    [
        (["passed", "failed", "not_runnable"], 0),
        (["passed", "running"], 1),
        (["admitted"], 1),
        ([], 0),
    ],
)
def test_the_explore_gate_counts_settled_cases(statuses, unsettled):
    task = {
        "run": {
            "cases": [{"name": str(i), "status": s} for i, s in enumerate(statuses)]
        },
        "steps": [],
    }
    [result] = run_gates.evaluate(
        [
            run_gates.check_rule(
                {
                    "id": "ex.cases.settled",
                    "fact": "explore.unsettled",
                    "op": "eq",
                    "value": 0,
                    "severity": "block",
                    "basis": "",
                }
            )
        ],
        run_gates.Env(task=task, ctx=None, project=None, project_dir=None),
    )
    assert result["facts"][0]["value"] == unsettled
    assert result["verdict"] == ("pass" if unsettled == 0 else "fail")


# ── Acceptance: eh_site, three cases through the existing engines ───────────


def ctx():
    return PyPSAService.get_active_context()


def jobs():
    return {j["id"] for j in solve_queue.solve_queue.list_jobs()}


@pytest.fixture
def explore_run(eh, monkeypatch):
    """A granted eh_site@1 run whose explore stage is on, with the test
    network's levers, a connection link and a sweep-covering envelope."""
    import pypsa  # noqa: F401

    network = ctx().network
    if "poc_link" not in network.links.index:
        network.add(
            "Link",
            "poc_link",
            bus0=network.generators.at["G1", "bus"],
            bus1=network.generators.at["G1", "bus"],
            p_nom=100.0,
        )
    prof = copy.deepcopy(run_profiles.PROFILES["eh_site@1"])
    prof["explore"] = {
        "levers": copy.deepcopy(LEVERS),
        "study": {"question_id": "bess_at_site"},
    }
    prof["envelope"] = dict(
        ATOMIC,
        runs=[{"tool": "solve_queue_enqueue"}],  # issue 08: never the sweep
        budget={"solves": 8, "rounds": 6, "wall_time_h": 8.0},
    )
    monkeypatch.setitem(run_profiles.PROFILES, "eh_site@1", prof)
    return prof


def propose(cases, n=0):
    return call("propose_cases", {"cases": cases}, n)


def test_eh_site_connection_bess_genset_three_cases_each_with_its_gate_and_settled(
    granted, explore_run, rated
):
    before = jobs()
    base_fp = workflow_tools._network_input_fingerprint(ctx(), exact=True)
    task, path, _ = granted()
    script = Script(propose(three()), say("Three options: connection, BESS, genset."))
    record = go(
        harness_runner.Runner("a", agent_provider=script, decisions=Clicks()),
        path,
        task,
    )
    run = record["run"]
    assert run["stages"]["explore"]["status"] == "passed", run["stages"]["explore"]
    cases = {c["name"]: c for c in run["cases"]}
    assert set(cases) == {"connection", "bess", "genset"}
    for c in cases.values():
        assert c["status"] in ("passed", "failed") and c["gate"]["rules"], c
        assert c["project_id"] != task["project_id"]  # its own fork
        assert {r["rule"] for r in c["gate"]["rules"]} >= {
            "res.solve.status",
            "res.fresh",
        }
    # One sweep step; each case job is a solve of the run (C4), and no other.
    sweeps = [
        s
        for s in record["steps"]
        if s["id"].startswith("explore.") and s["tool"] == "run_sensitivity_sweep"
    ]
    assert len(sweeps) == 1
    new = jobs() - before
    assert (
        run["budget"]["used"]["solves"]
        == len(new)
        == run_envelope.solves_used(envelope_of(path))
    )
    assert {c["job"]["job_id"] for c in cases.values()} <= new
    # The base is unchanged by the cases.
    assert workflow_tools._network_input_fingerprint(ctx(), exact=True) == base_fp


def test_a_failing_case_does_not_fail_the_run(granted, explore_run, rated, monkeypatch):
    task, path, _ = granted()
    # The genset case's fork is infeasible: its cap is under the load.
    tiny = change(
        "Generator",
        "G1",
        "p_nom_max",
        0.0,
        0.0,
        120.0,
        "MW",
        "profile:Generator.G1.p_nom_max",
    )
    script = Script(
        propose([case("genset_none", tiny), case("bess", *bess())]), say("Two cases.")
    )
    record = go(
        harness_runner.Runner("a", agent_provider=script, decisions=Clicks()),
        path,
        task,
    )
    run = record["run"]
    cases = {c["name"]: c for c in run["cases"]}
    assert cases["genset_none"]["status"] == "failed" and cases["genset_none"]["reason"]
    assert any(r["verdict"] == "fail" for r in cases["genset_none"]["gate"]["rules"])
    assert run["stages"]["explore"]["status"] == "passed"
    assert run["stage"] != "explore" and run["status"] in ("running", "completed")


def test_a_failing_base_case_still_fails_the_run_before_explore(
    granted, explore_run, rated
):
    PyPSAService.get_network().loads.at["L1", "p_set"] = (
        1e6  # infeasible base, no L2 change offered
    )
    task, path, _ = granted()
    record = go(
        harness_runner.Runner("a", agent_provider=Script(say("No change."))), path, task
    )
    run = record["run"]
    assert run["status"] == "needs_user" and run["stage"] in ("solve", "result_gate")
    assert run["stages"]["explore"]["status"] == "pending" and not run.get("cases")


def test_a_not_runnable_case_settles_with_its_reason_and_no_job(
    granted, explore_run, rated
):
    before = jobs()
    task, path, _ = granted()
    script = Script(
        propose([case("ppa", genset(), engine="commercial")]), say("A PPA case.")
    )
    record = go(
        harness_runner.Runner("a", agent_provider=script, decisions=Clicks()),
        path,
        task,
    )
    [c] = record["run"]["cases"]
    assert c["status"] == "not_runnable" and "commercial" in c["reason"]
    assert record["run"]["stages"]["explore"]["status"] == "passed"
    assert (
        len(jobs() - before) == record["run"]["budget"]["used"]["solves"]
    )  # the base solve only
    assert not any(s["tool"] == "run_sensitivity_sweep" for s in record["steps"])


def test_the_sweep_is_always_the_users_card_even_under_a_granted_envelope(
    granted, explore_run, rated
):
    """Issue 08 (GK B (8), confirmed by GK B (21)): no envelope covers a sweep."""
    task, path, _ = granted()
    record = go(
        harness_runner.Runner("a", agent_provider=Script(propose(three()), say("."))),
        path,
        task,
    )
    run = record["run"]
    assert run["status"] == "needs_user" and run["stage"] == "explore"
    assert run["waiting"]["kind"] == "card"
    [sweep] = [s for s in record["steps"] if s["tool"] == "run_sensitivity_sweep"]
    assert run["waiting"]["ref"] == sweep["id"] and sweep["status"] == "pending"
    assert all(c["status"] == "admitted" for c in run["cases"])


def test_a_profile_envelope_listing_the_sweep_is_refused_at_load():
    p = copy.deepcopy(run_profiles.EH_SITE)
    p["envelope"] = dict(
        p["envelope"], runs=[*p["envelope"]["runs"], {"tool": "run_sensitivity_sweep"}]
    )
    with pytest.raises(run_profiles.ProfileError):
        run_profiles.load(p)


def test_e3_a_run_granted_before_explore_resumes_to_the_sweep_card_never_unattended(
    granted, explore_run, rated, monkeypatch
):
    """E3: a run started with explore skipped, resumed after eh_site@1 turned
    it on, reaches the sweep card and runs no sweep without the user."""
    before = jobs()
    old = copy.deepcopy(explore_run)
    old["stages"] = {
        **old.get("stages", {}),
        "explore": {"skip": "Exploring cases is not part of a run yet."},
    }
    monkeypatch.setitem(run_profiles.PROFILES, "eh_site@1", old)
    task, path, _ = granted()
    monkeypatch.setitem(run_profiles.PROFILES, "eh_site@1", explore_run)
    record = go(
        harness_runner.Runner("b", agent_provider=Script(propose(three()), say("."))),
        path,
        task,
    )
    run = record["run"]
    assert run["stages"]["explore"]["status"] in ("skipped", "active")
    if run["stages"]["explore"]["status"] == "active":
        assert run["status"] == "needs_user" and run["waiting"]["kind"] == "card"
    assert not any(
        s["tool"] == "run_sensitivity_sweep" and s["status"] != "pending"
        for s in record["steps"]
    )
    assert len(jobs() - before) == 1  # the base solve only


def test_a_sweep_is_counted_once_per_case_job_not_twice(granted, explore_run, rated):
    """SH-05 PR C: the sweep's RUN_TOOLS `used` row and the explore audit row
    never count the same solve twice."""
    before = jobs()
    task, path, _ = granted()
    go(
        harness_runner.Runner(
            "a", agent_provider=Script(propose(three()), say(".")), decisions=Clicks()
        ),
        path,
        task,
    )
    envelope = envelope_of(path)
    assert run_envelope.solves_used(envelope) == len(jobs() - before) == 1 + 3


def test_an_investment_case_runs_on_study_owned_forks_and_leaves_the_base_unchanged(
    granted, explore_run, rated, monkeypatch
):
    calls = []
    from services import chat_tools

    def fake_start(project, question_id, name, intake, base_project_name=None):
        calls.append(("start", question_id))
        return {"study_id": "st-1", "project": "fork-base"}

    def fake_set(project, study_id, key, value, unit, source=None):
        calls.append(("set", key, value, unit, source))
        return {"key": key, "value": value}

    def fake_run(project, study_id, stage="options", fidelity="quick_screen"):
        calls.append(("run", stage))
        return {
            "stage": stage,
            "run": {"state": "completed", "options": [{"status": "completed"}]},
        }

    monkeypatch.setattr(chat_tools, "start_decision_study", fake_start)
    monkeypatch.setattr(chat_tools, "set_study_parameter", fake_set)
    monkeypatch.setattr(chat_tools, "run_decision_study", fake_run)
    monkeypatch.setattr(
        run_explore,
        "study_run",
        lambda project, study_id: {
            "state": "completed",
            "options": [{"status": "completed", "solves": 1}],
        },
    )
    base_fp = workflow_tools._network_input_fingerprint(ctx(), exact=True)
    task, path, _ = granted()
    script = Script(
        propose([case("bess_invest", *bess(), engine="study")]), say("Investment case.")
    )
    record = go(
        harness_runner.Runner("a", agent_provider=script, decisions=Clicks()),
        path,
        task,
    )
    [c] = record["run"]["cases"]
    assert (
        c["engine"] == "study" and c["study_id"] == "st-1" and c["status"] == "passed"
    )
    assert calls[0] == ("start", "bess_at_site") and calls[-1] == ("run", "options")
    assert all(
        s[4] for s in calls if s[0] == "set"
    )  # each ledger change carries its source
    assert workflow_tools._network_input_fingerprint(ctx(), exact=True) == base_fp


def test_a_lost_case_job_after_a_restart_is_uncertain_and_needs_the_user(
    granted, explore_run, rated, held_queue
):
    """The sweep's jobs wait in a held queue; one row is gone with the
    process. A fresh runner settles that sweep step `uncertain` from the queue
    (SC-09: nothing requeued) and the run needs the user."""
    from tests.test_harness_runner import later

    task, path, _ = granted()
    record = go(
        harness_runner.Runner(
            "a", agent_provider=Script(propose(three()), say(".")), decisions=Clicks()
        ),
        path,
        task,
    )
    [sweep] = [s for s in record["steps"] if s["tool"] == "run_sensitivity_sweep"]
    assert sweep["status"] == "waiting" and record["run"]["status"] == "waiting_job"
    assert {c["status"] for c in record["run"]["cases"]} == {"running"}
    sweep["job"]["jobs"][0]["job_id"] = (
        "00000000-0000-4000-8000-000000000000"  # gone with the process
    )
    path.write_text(json.dumps(record))
    rows = [j["id"] for j in solve_queue.solve_queue.list_jobs()]
    b = harness_runner.Runner("b", clock=later())
    assert b.tick(path) == "uncertain"
    run = json.loads(path.read_text())["run"]
    assert run["status"] == "needs_user" and run["reason"] == "uncertain"
    assert [j["id"] for j in solve_queue.solve_queue.list_jobs()] == rows
