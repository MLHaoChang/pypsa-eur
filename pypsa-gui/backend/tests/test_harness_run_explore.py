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
    baseline,
    dispatch,
    edit,
    helpers_state,
    session,
    wait_until,
)
from tests.test_harness_envelope import BLOCK, envelope_of, granted, on_run, profile  # noqa: F401
from tests.test_harness_envelope_atomic import ATOMIC, eh, writer  # noqa: F401
from tests.test_harness_immutable_solve import held_queue  # noqa: F401
from tests.test_harness_run_loops import Script, call, go, rated, say, stopped  # noqa: F401
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import (  # noqa: F401
    Clicks,
    detached,
    fake_report_provider,
    frames_of,
    settle,
    task_path,
)

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


def test_e8_propose_cases_is_offered_only_in_an_explore_turn_and_refuses_outside():
    """E8's reachability row: a stage-only tool, offered when the turn's
    `stage_tools` are the explore stage's, never in chat; called outside an
    explore turn it is a typed refusal."""
    import types

    from fastapi import HTTPException
    from harness import session as harness_session
    from harness.providers import wiring
    from harness.toolsets import STAGE_ONLY_TOOLS
    from services import chat_tools

    assert "propose_cases" in STAGE_ONLY_TOOLS
    wire = types.SimpleNamespace(tools=True, wire="anthropic")
    chat = harness_session.ChatSession(session_id="chat-e8")
    chat_tools.set_chat_session(chat)
    try:
        offered = {t["name"] for t in wiring._tools_payload_for_profile(wire)}
        assert "propose_cases" not in offered
        chat.stage_tools = frozenset(run_profiles.EXPLORE_TOOLS)
        offered = {t["name"] for t in wiring._tools_payload_for_profile(wire)}
        assert offered == set(run_profiles.EXPLORE_TOOLS)
        with pytest.raises(HTTPException) as refused:  # no explore turn on this session
            chat_tools.DISPATCHERS["propose_cases"](cases=three())
        assert refused.value.detail["error_kind"] == "explore_not_in_turn"
    finally:
        chat_tools.set_chat_session(None)


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
    # One modelled year, so the solve's capital-cost annuity is 1 and leaves
    # the inputs as they were. G1 is a genset candidate: a case that caps it
    # at 0 leaves the load unserved (the grid bus has no supply).
    network.snapshot_weightings.loc[:, :] = 8760.0 / len(network.snapshots)
    network.generators.loc["G1", ["p_nom_extendable", "p_nom", "capital_cost"]] = [
        True,
        0.0,
        1.0,
    ]
    network.storage_units.at["S1", "capital_cost"] = 1.0
    if "poc_link" not in network.links.index:
        # The site's grid connection: the site bus to a grid bus of its own.
        network.add("Bus", "grid", v_nom=network.buses.at["B1", "v_nom"])
        network.add(
            "Link", "poc_link", bus0="grid", bus1="B1", p_nom=100.0, carrier="AC"
        )
    prof = copy.deepcopy(run_profiles.PROFILES["eh_site@1"])
    prof["explore"] = {
        "levers": copy.deepcopy(LEVERS),
        "study": {"question_id": "bess_at_site"},
    }
    prof["envelope"] = dict(
        ATOMIC,
        # Issue 08: never the sweep; issue 07: the stage-7 report card, as eh_site@1.
        runs=[{"tool": "solve_queue_enqueue"}, {"tool": "generate_report"}],
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
    active, revision = str(ctx().project_uuid), ctx().network_revision
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
    # The base is unchanged by the cases, and the case gates read each child
    # from disk without activating it (E6).
    assert workflow_tools._network_input_fingerprint(ctx(), exact=True) == base_fp
    assert str(ctx().project_uuid) == active and ctx().network_revision == revision


def test_a_failing_case_does_not_fail_the_run(granted, explore_run, rated, monkeypatch):
    task, path, _ = granted()
    # The genset case's fork is infeasible: its 1 MW cap is under the 5 MW load.
    tiny = change(
        "Generator",
        "G1",
        "p_nom_max",
        1.0,
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
    """Issue 08 (GK B (8), confirmed by GK B (21)): no envelope covers a sweep.
    The base is saved first (a sweep branches the saved file), on its own card."""
    task, path, _ = granted()
    record = go(
        harness_runner.Runner("a", agent_provider=Script(propose(three()), say("."))),
        path,
        task,
    )
    run = record["run"]
    [save] = [s for s in record["steps"] if s["tool"] == "save_project"]
    assert run["status"] == "needs_user" and run["stage"] == "explore"
    assert run["waiting"] == {**run["waiting"], "kind": "card", "ref": save["id"]}
    # The save approved, the sweep still waits for the user's own card.
    record = go(
        harness_runner.Runner("a", decisions=Clicks(refs={save["id"]})), path, task
    )
    run = record["run"]
    [sweep] = [s for s in record["steps"] if s["tool"] == "run_sensitivity_sweep"]
    assert run["status"] == "needs_user" and run["waiting"]["kind"] == "card"
    assert run["waiting"]["ref"] == sweep["id"] and sweep["status"] == "pending"
    assert all(c["status"] == "admitted" for c in run["cases"])


def test_a_profile_envelope_listing_the_sweep_is_refused_at_load():
    p = copy.deepcopy(run_profiles.EH_SITE)
    p["envelope"] = dict(
        p["envelope"], runs=[*p["envelope"]["runs"], {"tool": "run_sensitivity_sweep"}]
    )
    with pytest.raises(run_profiles.ProfileError):
        run_profiles.load(p)


def test_e3_a_run_granted_before_explore_keeps_its_stages_and_never_sweeps(
    granted, explore_run, rated, monkeypatch
):
    """E3: a run started with explore skipped, resumed after eh_site@1 turned
    it on, keeps the stages it started with (frozen at start): explore stays
    skipped, no case is proposed and no sweep runs, carded or not."""
    before = jobs()
    old = copy.deepcopy(explore_run)
    old.pop("explore")  # the profile before issue 06 named no levers
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
    assert run["stages"]["explore"]["status"] == "skipped"
    assert run["status"] == "completed" and not run.get("cases")
    assert not [s for s in record["steps"] if s["id"].startswith("explore.")]
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


@pytest.fixture
def study(monkeypatch):
    """The decision study's tools, faked: what the explore steps called, and
    `record`, the study run `study_run` reads (status and solves charged)."""
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
        return {"stage": stage, "run": {"status": "running"}}

    monkeypatch.setattr(chat_tools, "start_decision_study", fake_start)
    monkeypatch.setattr(chat_tools, "set_study_parameter", fake_set)
    monkeypatch.setattr(chat_tools, "run_decision_study", fake_run)
    for name, fake in (
        ("start_decision_study", fake_start),
        ("set_study_parameter", fake_set),
        ("run_decision_study", fake_run),
    ):
        monkeypatch.setitem(chat_tools.DISPATCHERS, name, fake)
    # The ledger card's own adapter, on the fake study's rows: its old row,
    # the edited row, and what is stored after the write (the edit's receipt).
    import types

    from harness import edit_card_records

    def row(value, source, status):
        return types.SimpleNamespace(
            key="battery_capex",
            label="Battery capex",
            unit="EUR/kWh",
            value=value,
            source=source,
            status=status,
            provenance=status,
        )

    stored = row(300.0, "pack", "default")

    def fake_edit(args):
        return None, stored, row(args["value"], args["source"], "customised")

    def fake_set_stored(project, study_id, key, value, unit, source=None):
        stored.__dict__.update(vars(row(value, source, "customised")))
        return fake_set(project, study_id, key, value, unit, source)

    monkeypatch.setattr(edit_card_records, "_ledger_edit", fake_edit)
    monkeypatch.setattr(edit_card_records, "_stored_ledger_row", lambda args: stored)
    monkeypatch.setitem(chat_tools.DISPATCHERS, "set_study_parameter", fake_set_stored)
    state = types.SimpleNamespace(
        calls=calls, record={"status": "completed", "solves_charged": 3}
    )
    monkeypatch.setattr(
        run_explore, "study_run", lambda project, study_id: dict(state.record)
    )
    return state


def test_an_investment_case_runs_on_study_owned_forks_and_leaves_the_base_unchanged(
    granted, explore_run, rated, ledger, study
):
    calls = study.calls
    base_fp = workflow_tools._network_input_fingerprint(ctx(), exact=True)
    task, path, _ = granted()
    script = Script(propose([invest(capex())]), say("Investment case."))
    record = go(
        harness_runner.Runner("a", agent_provider=script, decisions=Clicks()),
        path,
        task,
    )
    [c] = record["run"]["cases"]
    assert (
        c["engine"] == "study" and c["study_id"] == "st-1" and c["status"] == "passed"
    )
    assert c["solves"] == {
        **c["solves"],
        "reserved": 3,
        "used": 3,
    }  # E5: reserved, then settled
    assert calls[0] == ("start", "bess_at_site") and calls[-1] == ("run", "options")
    assert all(
        s[4] for s in calls if s[0] == "set"
    )  # each ledger change carries its source
    assert workflow_tools._network_input_fingerprint(ctx(), exact=True) == base_fp


def test_e5_an_investment_case_that_spends_less_than_it_reserved_settles_what_it_spent(
    granted, explore_run, rated, ledger, study
):
    """GK B (23) E5: the case reserves its upper bound (3 option solves) and
    settles what the study charged (1): on the case, on its envelope audit
    row and in the run's solve count."""
    study.record = {"status": "completed", "solves_charged": 1}
    task, path, _ = granted()
    record = go(
        harness_runner.Runner(
            "a",
            agent_provider=Script(propose([invest(capex())]), say(".")),
            decisions=Clicks(),
        ),
        path,
        task,
    )
    [c] = record["run"]["cases"]
    assert c["status"] == "passed"
    assert (c["solves"]["reserved"], c["solves"]["used"]) == (3, 1)
    envelope = envelope_of(path)
    [row] = [a for a in envelope["audit"] if a["event"] == "explore"]
    assert (row["reserved"], row["solves"]) == (3, 1)
    others = sum(
        1
        for a in envelope["audit"]
        if a["event"] == "used" and a.get("tool") in run_envelope.RUN_TOOLS
    )
    assert run_envelope.solves_used(envelope) == others + 1
    assert record["run"]["budget"]["used"]["solves"] == run_envelope.solves_used(
        envelope
    )


def test_e10_a_late_study_case_settles_failed_on_a_runner_tick_not_only_at_boot(
    granted, explore_run, rated, ledger, study
):
    """GK B (23) E10: a study that never finishes settles failed past its
    deadline on the runner's own tick (`advance` in `_step`); no boot sweep runs."""
    from tests.test_harness_runner import later

    study.record = {"status": "running"}
    task, path, _ = granted()
    runner = harness_runner.Runner(
        "a",
        agent_provider=Script(propose([invest(capex())]), say(".")),
        decisions=Clicks(),
    )
    record = settle(
        runner,
        path,
        task["task_id"],
        lambda r: any(
            c.get("status") == "running" for c in r["run"].get("cases") or []
        ),
    )
    [c] = record["run"]["cases"]
    assert c["status"] == "running" and c["deadline_epoch"]
    late = harness_runner.Runner(
        "b",
        clock=later(
            run_explore.STUDY_DEADLINE_SECONDS + harness_runner.LEASE_SECONDS + 1
        ),
    )
    late.tick(path)
    [c] = json.loads(path.read_text())["run"]["cases"]
    assert c["status"] == "failed" and "deadline" in c["reason"]


def test_a_lost_case_job_after_a_restart_is_uncertain_and_needs_the_user(
    granted, explore_run, rated
):
    """The sweep's jobs wait in a held queue; one row is gone with the
    process. A fresh runner settles that sweep step `uncertain` from the queue
    (SC-09: nothing requeued) and the run needs the user."""
    from tests.test_harness_runner import later

    task, path, _ = granted()
    script = Script(propose(three()), say("."))
    record = go(  # every card but the sweep's: the run waits at the sweep card
        harness_runner.Runner(
            "a",
            agent_provider=script,
            decisions=lambda t, ref, h: None if ref == "explore.2" else "approve",
        ),
        path,
        task,
    )
    assert record["run"]["waiting"]["ref"] == "explore.2"
    queue = solve_queue.solve_queue
    queue.pause()  # the sweep's jobs are enqueued and never run
    try:
        a = harness_runner.Runner("a", decisions=Clicks())
        for _ in range(40):
            a.tick(path)
            flight = a._inflight.get(task["task_id"])
            if flight is not None:
                flight[1].join(120)
            record = json.loads(path.read_text())
            if all(c["status"] == "running" for c in record["run"]["cases"]) or stopped(
                record
            ):
                break
        [sweep] = [s for s in record["steps"] if s["tool"] == "run_sensitivity_sweep"]
        assert sweep["status"] == "waiting" and record["run"]["status"] == "waiting_job"
        assert {c["status"] for c in record["run"]["cases"]} == {"running"}
        sweep["job"]["jobs"][0]["job_id"] = (
            "00000000-0000-4000-8000-000000000000"  # gone with the process
        )
        path.write_text(json.dumps(record))
        rows = [j["id"] for j in solve_queue.solve_queue.list_jobs()]
        from services import assistant_tasks

        assistant_tasks._QUEUE_FOLLOWERS.clear()  # the restart: no follower of this process
        b = harness_runner.Runner("b", clock=later())
        assert b.tick(path) == "uncertain"
        run = json.loads(path.read_text())["run"]
        assert run["status"] == "needs_user" and run["reason"] == "uncertain"
        assert [j["id"] for j in solve_queue.solve_queue.list_jobs()] == rows
    finally:
        queue.resume()


# ── E4, E9: the proposal's write path, on a run file ────────────────────────


@pytest.fixture
def turn(tmp_path):
    """A run in its explore stage whose lease this process (`a`) holds, and
    an explore turn registered on a chat session; no envelope (no solves)."""
    import time

    from harness import session as harness_session
    from services import chat_tools

    folder = tmp_path / "project" / "assistant_tasks"
    folder.mkdir(parents=True)
    path = folder / "t-1.json"
    lease = {"lease_owner": "a", "lease_until": time.time() + 90}
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "task_id": "t-1",
                "steps": [],
                "run": {**empty_run(), "stage": "explore", "runner": lease},
            }
        )
    )
    chat = harness_session.ChatSession(session_id="run:t-1")
    chat_tools.set_chat_session(chat)
    state = run_explore.Turn(path, explore_profile(), ("a", time.time), base_value, [])
    run_explore._TURNS[chat.session_id] = state
    try:
        yield path
    finally:
        run_explore._TURNS.pop(chat.session_id, None)
        chat_tools.set_chat_session(None)


def tool_refusal(path, cases):
    """The tool as the model calls it: a typed refusal, and the run file as it was."""
    from fastapi import HTTPException
    from services import chat_tools

    before = path.read_bytes()
    with pytest.raises(HTTPException) as err:
        chat_tools.DISPATCHERS["propose_cases"](cases=cases)
    assert path.read_bytes() == before  # fail closed: nothing admitted, nothing written
    return err.value.detail["error_kind"]


def ppa():
    return case("ppa", genset(), engine="commercial")


def lapsed(path):
    record = json.loads(path.read_text())
    record["run"]["runner"] = {
        "lease_owner": "b",
        "lease_until": record["run"]["runner"]["lease_until"],
    }
    path.write_text(json.dumps(record))


TRIGGERS = {
    "invalid_explore_case": lambda: [],
    "explore_case_cap": lambda: [case(f"c{i}", genset()) for i in range(5)],
    "explore_change_cap": lambda: [case("three", genset(), *bess())],
    "explore_case_name": lambda: [case("same", genset()), case("SAME", genset())],
    "explore_not_a_lever": lambda: [
        case(
            "load",
            change(
                "Load", "L1", "p_set", 5.0, 0.0, 10.0, "MW", "profile:Load.L1.p_set"
            ),
        )
    ],
    "explore_value_out_of_range": lambda: [case("big", genset(value=130.0))],
    "explore_range_unsourced": lambda: [
        case("said", genset(source="model:it said so"))
    ],
    "explore_solve_budget": lambda: [
        case("genset", genset())
    ],  # no envelope: no solve left
}


def test_e9_every_refusal_kind_is_triggered_by_a_test():
    kinds = {k["error_kind"] for k in run_explore.ERROR_KINDS}
    assert kinds == set(TRIGGERS) | {"explore_not_in_turn"}
    manifest = json.loads(
        (
            __import__("pathlib").Path(__file__).parents[2]
            / "tool-error-kinds.d"
            / "claude-sh-06-explore-cases.json"
        ).read_text()
    )
    assert kinds == set(manifest["kinds"])


@pytest.mark.parametrize("kind", sorted(TRIGGERS))
def test_e9_each_refusal_is_typed_and_fails_closed(turn, kind):
    assert tool_refusal(turn, TRIGGERS[kind]()) == kind


def test_e9_a_base_class_error_is_never_reported_as_a_typed_refusal(turn, monkeypatch):
    """The mutant that raises the base class (ValueError) instead of
    ExploreError is no `explore_*` refusal: the tool's wrapper types only
    the stage's own errors."""
    from services import chat_tools

    def mutant(cases):
        raise ValueError("A run explores at most 4 cases (AI-558).")

    monkeypatch.setattr(run_explore, "propose_cases", mutant)
    with pytest.raises(ValueError):
        chat_tools.DISPATCHERS["propose_cases"](cases=[ppa()])
    assert issubclass(
        run_explore.ExploreError, ValueError
    )  # so the wrapper's except is the narrow one


def test_e4_admission_writes_only_while_this_process_holds_the_lease(turn):
    from services import chat_tools

    out = chat_tools.DISPATCHERS["propose_cases"](cases=[ppa()])
    assert out["admitted"] == [
        {
            "name": "ppa",
            "engine": "commercial",
            "status": "not_runnable",
            "reason": run_explore.NOT_RUNNABLE["commercial"],
        }
    ]
    assert [c["name"] for c in json.loads(turn.read_text())["run"]["cases"]] == ["ppa"]
    lapsed(turn)  # another runner took the run over meanwhile
    assert (
        tool_refusal(turn, [case("ppa2", genset(), engine="commercial")])
        == "explore_not_in_turn"
    )


def test_e4_a_turn_that_lost_its_stage_writes_nothing(turn):
    record = json.loads(turn.read_text())
    record["run"]["stage"] = "report"
    turn.write_text(json.dumps(record))
    assert tool_refusal(turn, [ppa()]) == "explore_not_in_turn"


# ── E1: a step's args are exactly what harness code builds ──────────────────


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(
            lambda a: a.update(baseline_project_id="someone-elses"), id="baseline"
        ),
        pytest.param(lambda a: a["cases"].pop(), id="cases"),
    ],
)
def test_e1_a_sweep_whose_args_differ_from_the_admitted_cases_needs_the_user(
    granted, explore_run, rated, tamper
):
    """GK B E1 (ruling #875): the sweep's baseline is the run's own project and
    its cases are exactly the admitted ones. A step whose args were changed on
    disk is refused before its card, and nothing is enqueued."""
    task, path, _ = granted()
    record = go(
        harness_runner.Runner(
            "a",
            agent_provider=Script(propose(three()), say(".")),
            decisions=lambda t, ref, h: None if ref == "explore.2" else "approve",
        ),
        path,
        task,
    )
    assert record["run"]["waiting"]["ref"] == "explore.2"
    [sweep] = [s for s in record["steps"] if s["tool"] == "run_sensitivity_sweep"]
    tamper(sweep["args"])
    path.write_text(json.dumps(record))
    jobs_before = [j["id"] for j in solve_queue.solve_queue.list_jobs()]
    record = go(
        harness_runner.Runner("a", decisions=Clicks(refs={"explore.2"})), path, task
    )
    run = record["run"]
    [sweep] = [s for s in record["steps"] if s["tool"] == "run_sensitivity_sweep"]
    assert run["status"] == "needs_user" and run["reason"] == "explore_args"
    assert sweep["status"] == "pending" and "job" not in sweep
    assert [j["id"] for j in solve_queue.solve_queue.list_jobs()] == jobs_before
    assert all(c["status"] == "admitted" for c in run["cases"])


def test_e1_a_save_under_another_name_needs_the_user(granted, explore_run, rated):
    """The save step saves the run's own project under its own name only."""
    task, path, _ = granted()
    record = go(
        harness_runner.Runner("a", agent_provider=Script(propose(three()), say("."))),
        path,
        task,
    )
    [save] = [s for s in record["steps"] if s["tool"] == "save_project"]
    assert record["run"]["waiting"]["ref"] == save["id"]
    save["args"]["name"] = "Someone else's project"
    path.write_text(json.dumps(record))
    record = go(
        harness_runner.Runner("a", decisions=Clicks(refs={save["id"]})), path, task
    )
    [save] = [s for s in record["steps"] if s["tool"] == "save_project"]
    assert (
        record["run"]["status"] == "needs_user"
        and record["run"]["reason"] == "explore_args"
    )
    assert save["status"] == "pending"


def test_e1_check_step_refuses_a_foreign_project_arg_and_passes_the_built_args():
    task = {
        "project_id": "p-1",
        "run": {**empty_run(), "stage": "explore", "cases": []},
        "steps": [],
    }
    step = {
        "id": "explore.1",
        "tool": "run_sensitivity_sweep",
        "explore": {"cases": ["x"]},
    }
    assert (
        run_explore.check_step(task, step, {"baseline_project_id": "p-2", "cases": []})
        == "explore_project"
    )
    assert (
        run_explore.check_step(task, step, {"target_project": "p-2"})
        == "explore_project"
    )
    # A step of another stage is not the explore stage's to check.
    other = {"id": "inputs.1", "tool": "run_sensitivity_sweep"}
    assert run_explore.check_step(task, other, {"baseline_project_id": "p-2"}) is None


# ── E10: a study case's deadline settles in the boot sweep ──────────────────


def _running_study(path, deadline, lease=None):
    record = json.loads(path.read_text())
    record["run"]["cases"] = [
        {
            "name": "bess",
            "engine": "study",
            "status": "running",
            "deadline_epoch": deadline,
        },
        {"name": "genset", "engine": "network", "status": "running"},
    ]
    if lease is not None:
        record["run"]["runner"] = lease
    path.write_text(json.dumps(record))


def test_e10_a_study_case_past_its_deadline_settles_failed_in_the_boot_sweep(turn):
    _running_study(
        turn, deadline=1000.0, lease={"lease_owner": "a", "lease_until": 500.0}
    )
    assert run_explore.expire(turn, boot="b", clock=lambda: 2000.0) == 1
    cases = {c["name"]: c for c in json.loads(turn.read_text())["run"]["cases"]}
    assert cases["bess"]["status"] == "failed" and "deadline" in cases["bess"]["reason"]
    assert (
        cases["genset"]["status"] == "running"
    )  # a network case's job settles from its queue


def test_e10_a_study_case_before_its_deadline_or_under_anothers_live_lease_stays(turn):
    _running_study(turn, deadline=3000.0)
    assert run_explore.expire(turn, boot="a", clock=lambda: 2000.0) == 0
    _running_study(
        turn, deadline=1000.0, lease={"lease_owner": "a", "lease_until": 9000.0}
    )
    before = turn.read_bytes()
    assert run_explore.expire(turn, boot="b", clock=lambda: 2000.0) == 0
    assert turn.read_bytes() == before


def test_a_profile_that_lowers_the_caps_is_held_to_them():
    prof = explore_profile()
    prof.setdefault("stages", {}).setdefault("explore", {})["agent"] = {
        "cases": 2,
        "changes": 1,
    }
    with pytest.raises(run_explore.ExploreError) as err:
        run_explore.check_proposal(
            empty_run(), prof, [case(f"c{i}", genset()) for i in range(3)]
        )
    assert err.value.kind == "explore_case_cap"
    with pytest.raises(run_explore.ExploreError) as err:
        run_explore.check_proposal(empty_run(), prof, [case("two", *bess())])
    assert err.value.kind == "explore_change_cap"


# ── GK B (23): a change that is not an object; names that fit their fork ────


@pytest.mark.parametrize("bad", [5, None, "genset", [1]])
@pytest.mark.parametrize("engine", ["network", "study", "commercial"])
def test_a_change_that_is_not_an_object_is_an_invalid_case_on_every_engine(
    ledger, engine, bad
):
    entry = (
        invest(capex(), bad)
        if engine == "study"
        else {**case("odd", genset(), bad), "engine": engine}
    )
    refused("invalid_explore_case", empty_run(), [entry])


def test_a_case_name_fits_its_child_project_name_whole():
    """GK B (23): the child project is "Explore <task8> <name>" within a
    project name's 64 characters, never cut: two names that differ only at
    their end are two projects, and a longer name is refused at proposal."""
    longest = "a" * run_explore.NAME_CHARS
    task = {"task_id": "0123456789abcdef"}
    one, two = (
        run_explore.case_project_name(task, {"name": longest[:-1] + end})
        for end in ("x", "y")
    )
    assert one != two and len(one) == len(two) == run_explore.PROJECT_NAME_CHARS
    refused("explore_case_name", empty_run(), [case(longest + "z", genset())])
    [admitted] = admit(empty_run(), [case(longest, genset())])
    assert admitted["name"] == longest


def test_a_sweep_refused_at_preflight_leaves_the_step_uncertain_and_the_run_needs_the_user(
    granted, explore_run, rated, monkeypatch
):
    """SH-06 deviation 2: one case failing network validation refuses the whole
    sweep before any project or job exists. The step is not known to have run,
    so it settles `uncertain`, the run needs the user, and nothing is judged
    from a partial sweep."""
    from services import chat_tools

    real = chat_tools.validate_network
    calls = []

    def second_case_fails(*a, **k):
        import inspect

        if not any(f.function == "run_sensitivity_sweep" for f in inspect.stack()):
            return real(*a, **k)  # the run's own gates validate as usual
        calls.append(1)
        if len(calls) == 2:
            return {"ok": False, "errors": ["case 2 breaks the network"]}
        return real(*a, **k)

    monkeypatch.setattr(chat_tools, "validate_network", second_case_fails)
    task, path, _ = granted()
    jobs_before = [j["id"] for j in solve_queue.solve_queue.list_jobs()]
    record = go(
        harness_runner.Runner(
            "a",
            agent_provider=Script(propose(three()), say(".")),
            decisions=lambda t, ref, h: "approve",
        ),
        path,
        task,
    )
    run = record["run"]
    [sweep] = [s for s in record["steps"] if s["tool"] == "run_sensitivity_sweep"]
    assert len(calls) >= 2  # the refusing case was reached
    assert sweep["status"] == "uncertain" and "job" not in sweep
    assert run["status"] == "needs_user" and run["reason"] == "uncertain"
    # only the baseline's own solve (the earlier enqueue step) is queued; no case job
    assert len(solve_queue.solve_queue.list_jobs()) - len(jobs_before) == 1
    assert not any(c["status"] in ("running", "done", "judged") for c in run["cases"])
    assert not any(s["tool"] == "judge" or "judged_by" in s for s in record["steps"])
