# ruff: noqa: F811 -- imported pytest fixtures
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness issue 11 (design §9.3 "eh_h2_hub"): eh_h2_hub@1 and
eh_h2_hub_staged@1. The offtake is served in every hour (block), the
electrolyser's utilisation is reported against the profile band (warn), LOLE
is the coupling loop's (as eh_datacenter, Q5), and the investment rule reads
`ic.counterfactual` (issue 19), evaluated once established and never blocking.
The runs install the shipped template networks in place of the baseline."""

from __future__ import annotations

import copy
import functools
import json
import sys
from pathlib import Path

import pandas as pd
import pypsa
import pytest
from fastapi.testclient import TestClient  # noqa: F401 - the client fixture's type

from harness import runner as harness_runner
from services import (
    chat_tools,
    flow_service,
    project_registry,
    run_envelope,
    run_gates,
    run_profiles,
)
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import helpers_state, session  # noqa: F401
from tests.test_harness_envelope import envelope_of, granted, profile  # noqa: F401
from tests.test_harness_run_loops import Script, call, go, rated, refused, say  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN
from tests.test_harness_runner import detached, fake_report_provider  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

REFS = {"eh_h2_hub": "eh_h2_hub@1", "eh_h2_hub_staged": "eh_h2_hub_staged@1"}


def _rules(ref, stage="result_gate"):
    return {r["id"]: r for r in run_profiles.resolve(ref)["stages"][stage]["rules"]}


# ── the profiles ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("template, ref", REFS.items())
def test_each_profile_serves_its_template_with_the_eh_study_and_the_coupling_loop(
    template, ref
):
    profile = run_profiles.resolve(ref)
    assert profile["templates"] == [template]
    assert _rules(ref, "setup")["set.project.template"]["value"] == template
    solve = [a["tool"] for a in profile["stages"]["solve"]["actions"]]
    assert solve == ["solve_queue_enqueue", "run_eh_study"]
    lole = _rules(ref)["eh.adequacy.lole"]
    assert lole["engine"] == {
        "tool": "run_coupling_loop",
        "max_solves": 3,
        "restore": "base",
    }
    assert run_profiles.needs_goal(profile) and profile["params"]["required"] == []
    assert [r["id"] for r in profile["stages"]["result_gate"]["rules"]] == [
        "res.solve.status",
        "res.fresh",
        "kpi.unserved_mwh",
        "h2.offtake.met",
        "h2.electrolyser.utilisation",
        "h2.ic.counterfactual",
        "eh.adequacy.lole",
    ]


@pytest.mark.parametrize("ref", REFS.values())
def test_the_offtake_blocks_and_utilisation_and_the_investment_case_only_warn(ref):
    rules = _rules(ref)
    offtake = rules["h2.offtake.met"]
    assert (offtake["fact"], offtake["op"], offtake["value"], offtake["severity"]) == (
        "h2.offtake.shed_hours",
        "eq",
        0.0,
        "block",
    )
    use = rules["h2.electrolyser.utilisation"]
    assert (use["op"], use["value"], use["severity"]) == (
        "within",
        run_profiles.H2_UTILISATION_BAND,
        "warn",
    )
    ic = rules["h2.ic.counterfactual"]
    assert (ic["fact"], ic["op"], ic["value"], ic["severity"]) == (
        "ic.counterfactual",
        "eq",
        "established",
        "warn",
    )


@pytest.mark.parametrize("ref", REFS.values())
def test_the_offtake_and_the_connection_are_never_levers(ref):
    """§6.0: the offtake is demand; grid_import and the fuel cell are not
    in the envelope, though Link.p_nom is the class lever."""
    profile = run_profiles.resolve(ref)
    attrs = {t["attr"] for t in profile["tunable"]}
    assert not attrs & run_profiles.NEVER_TUNABLE
    names = [e["target"]["name"] for e in profile["envelope"]["edits"]]
    for name in (
        "h2_offtake",
        "grid_import",
        "fuel_cell",
        "grid_supply",
        "wind_farm",
        "solar_park",
    ):
        assert not any(
            pd.Series([name]).str.match(n.replace("*", ".*") + "$").iloc[0]
            for n in names
        ), name
    with pytest.raises(run_profiles.ProfileError, match="never tunable"):
        run_profiles.load(
            {
                **profile,
                "tunable": [
                    *profile["tunable"],
                    {"component": "Load", "attr": "p_set"},
                ],
            }
        )
    bounded = {
        (e["target"]["name"], e["target"]["attr"]): e["bounds"]
        for e in profile["envelope"]["edits"]
    }
    assert all(
        b["min"] == 0.0 and b["max"] > 0 and "rel" not in b for b in bounded.values()
    )


def test_the_single_year_hub_sizes_its_electrolyser_and_the_staged_hub_its_new_ones():
    single = {
        (e["target"]["component"], e["target"]["name"], e["target"]["attr"])
        for e in run_profiles.resolve("eh_h2_hub@1")["envelope"]["edits"]
    }
    staged = {
        (e["target"]["component"], e["target"]["name"], e["target"]["attr"])
        for e in run_profiles.resolve("eh_h2_hub_staged@1")["envelope"]["edits"]
    }
    assert ("Link", "electrolyser", "p_nom") in single and (
        "Store",
        "h2_storage",
        "e_nom",
    ) in single
    assert ("Link", "electrolyser_new_*", "p_nom_max") in staged and (
        "Link",
        "electrolyser",
        "p_nom",
    ) not in staged
    # Every envelope edit is one of the profile's tunables (checked at load).
    for ref in REFS.values():
        profile = run_profiles.resolve(ref)
        tunable = {(t["component"], t["attr"]) for t in profile["tunable"]}
        assert {
            (e["target"]["component"], e["target"]["attr"])
            for e in profile["envelope"]["edits"]
        } == tunable
        assert set(_rules(ref)["h2.offtake.met"]["tunable"]) == {
            f"{c}.{a}" for c, a in tunable
        }


# ── runs on the shipped templates ───────────────────────────────────────────


@pytest.fixture
def template(request):
    return getattr(request, "param", "eh_h2_hub")


@pytest.fixture
def baseline(client, session_ctx, install_network, template, tmp_path):
    """The shipped template network as the run's project (in place of
    `tests.test_workflow_tools.baseline`, which the run fixtures build on),
    read back from its netCDF as a saved project is."""
    from services.solver_service import SolverConfig

    T.BUILDERS[template]().export_to_netcdf(tmp_path / "template.nc")
    install_network(pypsa.Network(tmp_path / "template.nc"))
    name = f"H2 Hub {template}"
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    # The template's own solver settings (its VoLL slacks) and a stored reliability goal (h/yr).
    ctx.solver_state["solver_config"] = SolverConfig(
        **{
            **T.SOLVER_CONFIG,
            **T.SOLVER_CONFIG_EXTRA.get(template, {}),
            "target_lole_h": 2.0,
        }
    )
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, name)
            path = project_registry.project_dir(project)
        yield ctx, str(project.id), path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


@pytest.fixture
def site(baseline, template):
    _, project_id, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(json.dumps({"id": template}))
    return project_id, root


def _ctx():
    return PyPSAService.get_active_context()


@pytest.fixture
def goal_granted(session, site, detached):
    """`tests.test_harness_envelope.granted`, the card's reliability goal echoed (P5a)."""
    from tests.test_harness_run_skeleton import start
    from tests.test_harness_runner import task_path

    def go_(ref):
        task = start(run={**RUN, "profile": ref})
        run_envelope.decide(
            session, task["task_id"], "unattended", goal=task["envelope"]["goal"]
        )
        chat_tools.set_chat_session(None)
        chat_tools.set_acting_user(None)
        return task, task_path(site[1], task)

    return go_


def _without_eh(ref):
    """The profile without its EH study and LOLE loop (`tests/test_harness_run_eh_site_v2.py`
    drives those for real on eh_site@2): the L2 mechanics only."""
    profile = copy.deepcopy(run_profiles.PROFILES[ref])
    solve = profile["stages"]["solve"]
    solve["actions"] = [a for a in solve["actions"] if a["tool"] != "run_eh_study"]
    solve["rules"] = [r for r in solve["rules"] if r["fact"] != "eh.study.status"]
    gate = profile["stages"]["result_gate"]
    gate["rules"] = [r for r in gate["rules"] if not r.get("engine")]
    return {k: v for k, v in profile.items() if k != "eh_study"}


def _electrolyser(value, n=0):
    return call(
        "update_component",
        {"component_class": "Link", "name": "electrolyser", "attrs": {"p_nom": value}},
        n,
    )


def test_an_undersized_electrolyser_fails_the_offtake_and_l2_sizes_it_within_bounds(
    granted, rated, monkeypatch
):
    """Acceptance: 20 MW cannot serve 25 MW of H2 at 68 % (36.8 MW in), so
    the offtake sheds; one L2 round raises the electrolyser within the
    envelope's bounds, the re-solve serves every hour."""
    monkeypatch.setitem(
        run_profiles.PROFILES, "eh_h2_hub@1", _without_eh("eh_h2_hub@1")
    )
    _ctx().network.links.at["electrolyser", "p_nom"] = 20.0
    task, path, _ = granted(run={**RUN, "profile": "eh_h2_hub@1"})
    script = Script(
        _electrolyser(60.0),
        say("The offtake shed: 20 MW of electrolyser feeds 13.6 MW of H2. Raised it."),
    )
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert "loop" in run, (
        run["status"],
        run["stage"],
        [(s["id"], s["status"], s.get("error")) for s in record["steps"]],
    )
    first = next(c for c in run["loop"]["history"])
    assert {f["rule"] for f in first["findings"]} >= {"h2.offtake.met"}
    # H1: the finding is a shed read from the run's own (real) solve, never a missing reading.
    offtake = next(f for f in first["findings"] if f["rule"] == "h2.offtake.met")
    assert offtake["verdict"] == "fail" and offtake["facts"][0]["value"] > 0
    assert run["status"] == "completed", (
        run["stage"],
        run["stages"][run["stage"]].get("reason"),
    )
    assert (
        run["loop"]["kind"] == "L2"
        and run["loop"]["rounds"] <= 2
        and run["loop"]["ended"] == "passed"
    )
    [row] = run["ledger"]
    assert (
        row["target"] == {"component": "Link", "name": "electrolyser", "attr": "p_nom"}
        and row["new"] == 60.0
    )
    verdicts = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert verdicts["h2.offtake.met"]["verdict"] == "pass"
    assert verdicts["h2.offtake.met"]["facts"][0]["value"] == 0.0
    assert verdicts["kpi.unserved_mwh"]["verdict"] == "pass"
    assert verdicts["h2.ic.counterfactual"]["verdict"] == "not_evaluated"
    assert float(_ctx().network.links.at["electrolyser", "p_nom"]) == 60.0


def test_an_electrolyser_beyond_the_envelopes_bound_is_refused_and_nothing_changes(
    granted, rated, monkeypatch
):
    monkeypatch.setitem(
        run_profiles.PROFILES, "eh_h2_hub@1", _without_eh("eh_h2_hub@1")
    )
    _ctx().network.links.at["electrolyser", "p_nom"] = 20.0
    task, path, _ = granted(run={**RUN, "profile": "eh_h2_hub@1"})
    script = Script(
        _electrolyser(200.0),
        say("Tried 200 MW."),
        say("Nothing more within the bounds."),
    )
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    assert record["run"]["status"] != "completed"
    assert [t for t, _ in refused(path)] == ["update_component"]
    assert record["run"]["ledger"] == []
    assert float(_ctx().network.links.at["electrolyser", "p_nom"]) == 20.0


@pytest.mark.parametrize("template", list(REFS), indirect=True)
def test_a_run_on_each_template_stops_at_the_result_gate_on_the_loops_typed_refusal(
    goal_granted, rated, fake_report, template
):
    """Acceptance 1 is blocked by .scratch/adequacy-loops/issues/01 (GK A (19)
    SH11-L1, L2, L3, L5): the shipped profile, EH study and coupling loop
    real. The offtake passes on the run's own solve; the coupling loop
    refuses (the templates' outages sit on Links), the LOLE rule reads that
    typed reason, never passed or not_started, and the run stops at the
    result gate. The refused loop charges no solve."""
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks

    task, path = goal_granted(REFS[template])
    clicks = Clicks(refs={"solve.2"})
    record = go(run_runner(clicks), path, task)
    run = record["run"]
    verdicts = {
        r["rule"]: r
        for r in (run["stages"]["result_gate"].get("gate") or {}).get("rules", [])
    }
    assert run["stage"] == "result_gate" and run["status"] == "needs_user", (
        run["stage"],
        run["status"],
    )
    assert (
        run["stages"]["result_gate"]["status"] == "failed"
        and run["stages"]["handoff"]["status"] != "passed"
    )
    assert clicks.used == ["solve.2"]
    lole = verdicts["eh.adequacy.lole"]
    assert lole["verdict"] == "not_evaluated" and lole["severity"] == "block"
    assert lole["facts"][0]["value"] == run_gates.LOOP_CANNOT_SAMPLE
    assert lole["facts"][0]["value"] not in (
        "passed",
        "not_started",
    ) and not run_gates.passed([lole])
    assert verdicts["h2.offtake.met"]["verdict"] == "pass"
    assert verdicts["h2.ic.counterfactual"]["verdict"] == "not_evaluated"
    assert verdicts["h2.ic.counterfactual"]["reason"].startswith("not_run")
    use = verdicts["h2.electrolyser.utilisation"]
    assert use["verdict"] in ("pass", "fail") and 0.0 < use["facts"][0]["value"] <= 1.0
    engine = next(s for s in record["steps"] if s.get("engine"))
    assert engine["tool"] == "run_coupling_loop" and engine["status"] == "failed"
    assert "nothing to sample" in engine["error"]
    # L2: the refused loop's reservation is released; only the solve and the EH study are charged.
    envelope = envelope_of(path)
    [row] = [
        a
        for a in envelope["audit"]
        if a.get("tool") == "run_coupling_loop" and "reserved" in a
    ]
    assert row["solves"] == 0
    assert run_envelope.solves_used(envelope) == 2


@pytest.mark.parametrize("ref", REFS.values())
def test_a_done_eh_study_meeting_the_goal_never_passes_the_refused_loops_lole_rule(
    tmp_path, ref
):
    """GK A (19) SH11-L3, F9: the EH study finished and its own MC LOLE meets
    the goal, but the coupling loop refused (nothing to sample): the LOLE rule
    reads the typed refusal, is not evaluated, and the gate does not pass."""
    from tests.test_harness_run_eh import ctx_for, eh_done, env_for, record

    certified = {
        "stage": "mc_certify",
        "status": "run",
        "note": None,
        "solves_charged": 0,
        "metric": "mc_lole",
        "target_lole_h": 2.0,
        "lole_h_per_year": 0.1,
        "met": True,
    }
    eh = record(stages=record()["report"]["pipeline"]["stages"] + [certified])
    refused_loop = {
        "id": "result_gate.1",
        "tool": "run_coupling_loop",
        "engine": "eh.adequacy.lole",
        "status": "failed",
        "error": f"engine_refused: {run_gates.NOTHING_TO_SAMPLE}",
    }
    env = env_for(
        tmp_path, ctx=ctx_for(tmp_path, eh_study=eh), steps=[eh_done(), refused_loop]
    )
    env.task["run"]["profile"] = ref
    rule = _rules(ref)["eh.adequacy.lole"]
    [result] = run_gates.evaluate([rule], env)
    assert result["verdict"] == "not_evaluated" and result["severity"] == "block"
    assert result["facts"][0]["value"] == run_gates.LOOP_CANNOT_SAMPLE
    assert not run_gates.passed([result])


# ── the readers, on solved networks (no run) ─────────────────────────────────


def _solved(template, **links):
    """A copy of the template solved once per (template, links): the readers' tests edit it."""
    return _solved_once(template, tuple(sorted(links.items()))).copy()


@functools.cache
def _solved_once(template, links):
    links = dict(links)
    import logging

    logging.disable(logging.WARNING)
    try:
        n = T.BUILDERS[template]()
        for name, p_nom in links.items():
            n.links.at[name, "p_nom"] = p_nom
        n.optimize(
            solver_name="highs",
            solver_options={"output_flag": False},
            multi_investment_periods=bool(len(n.investment_periods)),
        )
    finally:
        logging.disable(logging.NOTSET)
    n.model.solver_model = None  # a copy of a solved network carries no solver model
    return n


def _rule(rid):
    return {**_rules("eh_h2_hub@1")[rid]}


def _read_on(monkeypatch, n, reader, *args, capture=None, current=True):
    """`reader` on a context holding `n`, past `_own_solve`'s identity checks."""
    from harness import calculations

    ctx = type(
        "Ctx", (), {"network": n, "solver_state": {"last_lost_load": capture or {}}}
    )()
    monkeypatch.setattr(run_gates, "_own_solve", lambda env, read, typed=False: read(ctx))
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda c: current)
    return reader(None, *args)


def test_the_offtake_is_the_one_load_on_an_h2_bus():
    n = T.BUILDERS["eh_h2_hub"]()
    assert run_gates._offtake_load(n) == "h2_offtake"
    n.add("Load", "second", bus="h2", p_set=1.0)
    assert isinstance(run_gates._offtake_load(n), run_gates.NoEvidence)
    n.remove("Load", ["second", "h2_offtake"])
    missing = run_gates._offtake_load(n)
    assert (
        isinstance(missing, run_gates.NoEvidence) and missing.value == "offtake_loads:0"
    )


def test_the_electrolyser_fleet_is_named_by_its_role_else_its_carrier():
    n = T.BUILDERS["eh_h2_hub_staged"]()
    assert run_gates._electrolysers(n) == [
        "electrolyser",
        *(f"electrolyser_new_{y}" for y in (2027, 2030, 2035)),
    ]
    n.links["eh_role"] = ""
    assert "electrolyser" in run_gates._electrolysers(n)  # carrier "electrolysis"
    n.links["carrier"] = "AC"
    assert run_gates._electrolysers(n) == []


def test_utilisation_is_the_input_energy_over_capacity_on_the_shipped_hub(monkeypatch):
    n = _solved("eh_h2_hub")
    u = _read_on(
        monkeypatch,
        n,
        run_gates._electrolyser_utilisation,
        _rule("h2.electrolyser.utilisation"),
    )
    w = n.snapshot_weightings["generators"]
    expected = float(
        (n.links_t.p0["electrolyser"].clip(lower=0) * w).sum() / (60.0 * w.sum())
    )
    assert u == pytest.approx(expected) and 0.0 < u <= 1.0


def test_staged_utilisation_has_a_row_per_period_and_reads_the_period_furthest_outside(
    monkeypatch,
):
    n = _solved("eh_h2_hub_staged")
    lo, hi = run_profiles.H2_UTILISATION_BAND
    scored = _read_on(
        monkeypatch,
        n,
        run_gates._electrolyser_utilisation,
        _rule("h2.electrolyser.utilisation"),
    )
    assert [r["period"] for r in scored.rows] == [2027, 2030, 2035]
    worst = next(r for r in scored.rows if r["worst"])
    gap = [max(lo - r["utilisation"], r["utilisation"] - hi, 0.0) for r in scored.rows]
    assert scored.value == worst["utilisation"] and gap[
        scored.rows.index(worst)
    ] == max(gap)
    assert all(
        r["verdict"] == ("pass" if lo <= r["utilisation"] <= hi else "fail")
        and r["assumed"] is False
        for r in scored.rows
    )


def test_no_electrolyser_or_no_capacity_is_no_evidence_never_zero(monkeypatch):
    n = _solved("eh_h2_hub")
    n.links.at["electrolyser", "p_nom_opt"] = (
        0.0  # a built Link reads its p_nom (as the app's solve leaves it)
    )
    assert (
        _read_on(
            monkeypatch,
            n,
            run_gates._electrolyser_utilisation,
            _rule("h2.electrolyser.utilisation"),
        )
        > 0
    )
    n.links.at["electrolyser", "p_nom"] = 0.0
    n.links_t.p0["electrolyser"] = 0.0
    assert (
        _read_on(
            monkeypatch,
            n,
            run_gates._electrolyser_utilisation,
            _rule("h2.electrolyser.utilisation"),
        )
        is None
    )
    n.links["eh_role"], n.links["carrier"] = "", "AC"
    read = _read_on(
        monkeypatch,
        n,
        run_gates._electrolyser_utilisation,
        _rule("h2.electrolyser.utilisation"),
    )
    assert isinstance(read, run_gates.NoEvidence) and read.value == "no_electrolyser"


@pytest.mark.parametrize("flow", [11.0, -1.0])
def test_dispatch_outside_the_capacity_it_was_solved_against_is_no_evidence(
    monkeypatch, flow
):
    """Blocker 2: the staged run read 1.23 when the live p_nom_opt of an
    extendable electrolyser was its pre-solve 0 next to the solve's 11.1 MW
    dispatch. A capacity the dispatch exceeds is not the solve's: never a
    utilisation above 1 (nor one from a reversed flow)."""
    n = _solved("eh_h2_hub_staged")
    rule = _rule("h2.electrolyser.utilisation")
    assert not isinstance(
        _read_on(monkeypatch, n, run_gates._electrolyser_utilisation, rule),
        run_gates.NoEvidence,
    )
    cap = float(n.links.at["electrolyser", "p_nom"])
    n.links_t.p0.loc[n.snapshots[-1], "electrolyser"] = cap + flow if flow > 0 else flow
    read = _read_on(monkeypatch, n, run_gates._electrolyser_utilisation, rule)
    assert (
        isinstance(read, run_gates.NoEvidence)
        and read.value == "dispatch_exceeds_capacity"
    )


def test_utilisation_is_the_capacity_factor_asset_economics_gives_a_link():
    """H3: one formula, `asset_economics.link_capacity_factor`."""
    from services.results.asset_economics import link_capacity_factor

    assert link_capacity_factor(300.0, 10.0, 60.0) == 0.5
    assert (
        link_capacity_factor(300.0, 0.0, 60.0) is None
        and link_capacity_factor(300.0, 10.0, 0.0) is None
    )


def _staged_capture(n, shed_2030=0.0):
    frame = pd.DataFrame(0.0, index=n.snapshots, columns=list(n.loads.index))
    frame.loc[n.snapshots.get_level_values(0) == 2030, "h2_offtake"] = shed_2030
    frame.iloc[0, frame.columns.get_loc("h2_offtake")] = 0.0
    return {"lost_load_t": frame}


def test_staged_offtake_counts_shed_hours_per_period_and_reads_the_worst(monkeypatch):
    n = T.BUILDERS["eh_h2_hub_staged"]()
    w = n.snapshot_weightings["generators"]
    scored = _read_on(
        monkeypatch, n, run_gates._offtake_shed_hours, capture=_staged_capture(n, 2.0)
    )
    rows = {r["period"]: r for r in scored.rows}
    expected = float(w[n.snapshots.get_level_values(0) == 2030].sum())
    assert rows[2030]["shed_hours"] == pytest.approx(expected) and rows[2030]["worst"]
    assert (
        rows[2027]["shed_hours"] == rows[2035]["shed_hours"] == 0.0
        and scored.value == rows[2030]["shed_hours"]
    )
    # Shed at or below the threshold is LP dust, never an hour.
    dust = _read_on(
        monkeypatch, n, run_gates._offtake_shed_hours, capture=_staged_capture(n, 1e-4)
    )
    assert dust.value == 0.0


@pytest.mark.parametrize(
    "capture, current, expected",
    [
        (None, True, "no_lost_load_capture"),  # no capture: never 0
        ("other_load", True, "no_lost_load_capture"),  # a capture without the offtake
        ("ok", False, "stale_solve"),  # a stale solve has no value
    ],
)
def test_staged_offtake_without_its_own_capture_is_no_evidence(
    monkeypatch, capture, current, expected
):
    n = T.BUILDERS["eh_h2_hub_staged"]()
    frame = (
        _staged_capture(n)
        if capture == "ok"
        else (
            {
                "lost_load_t": _staged_capture(n)["lost_load_t"].drop(
                    columns=["h2_offtake"]
                )
            }
            if capture
            else None
        )
    )
    read = _read_on(
        monkeypatch, n, run_gates._offtake_shed_hours, capture=frame, current=current
    )
    assert isinstance(read, run_gates.NoEvidence) and read.value == expected


# ── the investment rule (issue 19's fact) ───────────────────────────────────


@pytest.mark.parametrize(
    "state, reasons, verdict",
    [
        ("established", [], "pass"),
        (
            "not_established",
            ["counterfactual_not_established:no_reference_price"],
            "not_evaluated",
        ),
        ("not_run", ["no_case"], "not_evaluated"),
    ],
)
def test_the_investment_rule_is_evaluated_once_established_and_never_blocks(
    monkeypatch, state, reasons, verdict
):
    from services import ic_counterfactual as ic

    monkeypatch.setattr(
        ic,
        "read",
        lambda ctx, on: {"state": state, "basis": "site_today", "reasons": reasons},
    )
    env = run_gates.Env(task={}, ctx=object(), project=None, project_dir=None)
    [result] = run_gates.evaluate([_rule("h2.ic.counterfactual")], env)
    assert result["verdict"] == verdict and result["severity"] == "warn"
    if verdict == "not_evaluated":
        assert result["reason"] == f"{state}: {', '.join(reasons)}"
    assert run_gates.passed([result])


# ── GK B (38) amendment A1–A3: the queued solve's objective compare ──────────


@pytest.fixture
def queued_job(site, template):
    """The project's own queued solve, run to completion (as the run's solve.1)."""
    from harness import continuation
    from services.study.runner import _on_ctx
    from tests.test_assistant_tasks_study_brief import wait_until

    cell = PyPSAService._turn_cell.set(None)  # as the runner's thread: no chat turn
    try:
        queued = _on_ctx(_ctx(), chat_tools.solve_queue_enqueue, site[0])
    finally:
        PyPSAService._turn_cell.reset(cell)
    job = {"job_id": queued["id"], "project_id": site[0]}
    assert wait_until(lambda: continuation._queue_job_status(job) == "completed", 300)
    return job


@pytest.mark.parametrize("template", list(REFS), indirect=True)
def test_a_queued_h2_hub_solve_is_verified_on_the_runs_total_with_its_constant(
    queued_job, template
):
    """A2 (a), A3: the manifest's objective is the queue's total
    (`_compute_run_objective`, the LP objective plus its constant), never the
    bare LP objective; it is not None for the staged (multi-period) hub."""
    from harness import continuation
    from routers.simulation import _compute_run_objective
    from services import workflow_tools as wt

    evidence = continuation._queue_run(queued_job)
    assert evidence is not None, "a granted H2 hub solve read unverified"
    with chat_tools._acting() as (db, user):
        saved = wt._load_context(
            project_registry.resolve_project(db, user, queued_job["project_id"])
        )
    total = _compute_run_objective(saved.network, saved.solver_state["solver_config"])
    assert total is not None and evidence["objective"] == total
    if template == "eh_h2_hub":  # its constant carries the built assets' CAPEX
        assert float(saved.network.objective) != total


@pytest.mark.parametrize(
    "change", ["objective_off_by_one", "no_config", "no_total", "inputs_changed"]
)
def test_a_queued_solve_whose_total_cannot_be_matched_stays_unverified(
    queued_job, monkeypatch, change
):
    """A1, A2 (b): a manifest objective off by 1.0, a saved project with no
    solver config (the total would be priced with the foreground's) or no
    total at all is no evidence; so is a saved network whose inputs changed
    since its solve."""
    from harness import continuation
    from routers import simulation
    from services import run_evidence, workflow_tools as wt

    if change == "objective_off_by_one":
        load = run_evidence._load_run

        def off(root, project_id, run_id):
            manifest = copy.deepcopy(load(root, project_id, run_id))
            manifest["metrics"]["objective"]["value"] += 1.0
            return manifest

        monkeypatch.setattr(run_evidence, "_load_run", off)
    elif change == "no_config":
        load_ctx = wt._load_context

        def bare(child):
            saved = load_ctx(child)
            saved.solver_state.pop("solver_config", None)
            return saved

        monkeypatch.setattr(wt, "_load_context", bare)
    elif change == "inputs_changed":
        fresh = wt.solve_freshness

        def changed(ctx):
            return {**fresh(ctx), "inputs_changed_since_solve": True}

        monkeypatch.setattr(wt, "solve_freshness", changed)
    else:
        monkeypatch.setattr(
            simulation, "_compute_run_objective", lambda n, cfg=None: None
        )
    assert continuation._queue_run(queued_job) is None


# ── H4, H5: the envelope's levers ────────────────────────────────────────────


@pytest.mark.parametrize("ref", REFS.values())
def test_each_envelope_attr_is_the_active_one(ref):
    """H4: a fixed asset is sized by p_nom (e_nom), an extendable one by its
    cap p_nom_max: every listed instance of the template is the kind its attr moves."""
    import fnmatch

    n = T.BUILDERS[ref.split("@")[0]]()
    for e in run_profiles.resolve(ref)["envelope"]["edits"]:
        component, glob, attr = (
            e["target"]["component"],
            e["target"]["name"],
            e["target"]["attr"],
        )
        static = n.c[component].static
        names = [x for x in static.index if fnmatch.fnmatchcase(x, glob)]
        assert names, glob
        extendable = "e_nom_extendable" if component == "Store" else "p_nom_extendable"
        assert all(
            bool(static.at[x, extendable]) == attr.endswith("_max") for x in names
        ), (glob, attr)
        assert all(
            e["bounds"]["min"] <= float(static.at[x, attr]) <= e["bounds"]["max"]
            for x in names
        ), glob


def _objective(template, component, name, attr, value):
    import logging

    n = T.BUILDERS[template]()
    if value is not None:
        n.c[component].static.at[name, attr] = value
    logging.disable(logging.WARNING)
    try:
        n.optimize(
            solver_name="highs",
            solver_options={"output_flag": False},
            multi_investment_periods=bool(len(n.investment_periods)),
        )
    finally:
        logging.disable(logging.NOTSET)
    return float(n.objective)


@pytest.mark.parametrize(
    "template, target, value",
    [
        ("eh_h2_hub", ("Link", "electrolyser", "p_nom"), 40.0),
        ("eh_h2_hub_staged", ("Link", "electrolyser_new_2035", "p_nom_max"), 5.0),
    ],
)
def test_a_bounded_edit_changes_the_solve(template, target, value):
    """H4: a lever within its bounds moves the optimum on each template."""
    entry = next(
        e
        for e in run_profiles.resolve(REFS[template])["envelope"]["edits"]
        if e["target"]["attr"] == target[2]
        and e["target"]["name"].rstrip("*") in target[1]
    )
    assert entry["bounds"]["min"] <= value <= entry["bounds"]["max"]
    assert _objective(template, *target, value) != pytest.approx(
        _objective(template, *target, None), rel=1e-6
    )


def _admit(granted, ref, args):
    from tests.test_harness_envelope import ask, on_run, refusals

    task, path, _ = granted(run={**RUN, "profile": ref}, detach=False)
    with on_run(task, path) as run:
        assert ask(run, "update_component", args) is None
    return refusals(path)[-1]


@pytest.mark.parametrize(
    "template, name, attrs",
    [
        (
            "eh_h2_hub",
            "fuel_cell",
            {"p_nom_max": 50.0},
        ),  # extendable: its cap is the active attr
        ("eh_h2_hub", "grid_import", {"p_nom": 100.0}),
        ("eh_h2_hub_staged", "fuel_cell", {"p_nom_max": 50.0}),
        ("eh_h2_hub_staged", "grid_import", {"p_nom": 100.0}),
        (
            "eh_h2_hub_staged",
            "electrolyser",
            {"p_nom": 25.0},
        ),  # the staged hub's built one
    ],
    indirect=["template"],
)
def test_the_connection_the_backup_and_the_built_staged_electrolyser_are_outside_the_entry(
    granted, monkeypatch, template, name, attrs
):
    """H5, at `_match_edit`: the grant names none of them."""
    ref = REFS[template]
    monkeypatch.setitem(run_profiles.PROFILES, ref, _without_eh(ref))
    [(attr, value)] = attrs.items()
    before = float(_ctx().network.links.at[name, attr])
    assert before != value
    assert (
        _admit(granted, ref, {"component_class": "Link", "name": name, "attrs": attrs})
        == "outside_entry"
    )
    assert float(_ctx().network.links.at[name, attr]) == before


@pytest.mark.parametrize("template", list(REFS), indirect=True)
def test_the_offtake_is_never_tunable_and_never_admitted(
    granted, monkeypatch, template
):
    """H5: the offtake's p_set is outside any grant, and an envelope that
    lists it is refused as never tunable."""
    ref = REFS[template]
    monkeypatch.setitem(run_profiles.PROFILES, ref, _without_eh(ref))
    before = _ctx().network.loads.at["h2_offtake", "p_set"]
    assert (
        _admit(
            granted,
            ref,
            {"component_class": "Load", "name": "h2_offtake", "attrs": {"p_set": 1.0}},
        )
        == "outside_entry"
    )
    assert _ctx().network.loads.at["h2_offtake", "p_set"] == before
    with pytest.raises(run_envelope.EnvelopeError, match="never tunable"):
        run_envelope._check_edit(
            {
                "tool": "update_component",
                "bounds": {"min": 0.0, "max": 30.0},
                "target": {"component": "Load", "name": "h2_offtake", "attr": "p_set"},
            }
        )


@pytest.mark.parametrize("template", ["eh_h2_hub_staged"], indirect=True)
def test_a_staged_electrolyser_cap_above_its_bound_is_refused(
    granted, monkeypatch, template
):
    """H4: above the bound is out_of_bounds (the single-year hub's: above)."""
    ref = REFS[template]
    monkeypatch.setitem(run_profiles.PROFILES, ref, _without_eh(ref))
    assert (
        _admit(
            granted,
            ref,
            {
                "component_class": "Link",
                "name": "electrolyser_new_2035",
                "attrs": {"p_nom_max": 81.0},
            },
        )
        == "out_of_bounds"
    )
    assert float(_ctx().network.links.at["electrolyser_new_2035", "p_nom_max"]) == 40.0


# ── H9: the readers' guards on unreadable numbers (mutant survivors) ─────────


@pytest.mark.parametrize("where", ["shed", "weight"])
def test_an_unreadable_shed_or_weight_in_one_period_reads_no_shed_hours(
    tmp_path, monkeypatch, where
):
    """A NaN shed or a NaN or negative weight in 2040 makes the whole reading
    unavailable, never a number from the other period."""
    env = _h2_env(
        tmp_path,
        monkeypatch,
        shed=((1.0, 0.0), (float("nan"), 1.0))
        if where == "shed"
        else ((1.0, 0.0), (0.0, 1.0)),
    )
    if where == "weight":
        env.ctx.network.snapshot_weightings.iloc[
            2, env.ctx.network.snapshot_weightings.columns.get_loc("generators")
        ] = float("nan")
    assert run_gates.FACTS["h2.offtake.shed_hours"].read(env) is None


def test_a_negative_weight_reads_no_shed_hours_and_no_utilisation(
    tmp_path, monkeypatch
):
    env = _h2_env(tmp_path, monkeypatch)
    sw = env.ctx.network.snapshot_weightings
    sw.iloc[3, sw.columns.get_loc("generators")] = -1.0
    assert run_gates.FACTS["h2.offtake.shed_hours"].read(env) is None
    result = _gate(env, "h2.electrolyser.utilisation")
    assert result["verdict"] == "not_evaluated" and result["facts"][0]["value"] is None


@pytest.mark.parametrize("where", ["flow", "weight"])
def test_an_unreadable_flow_or_weight_reads_no_utilisation(
    tmp_path, monkeypatch, where
):
    env = _h2_env(
        tmp_path,
        monkeypatch,
        dispatch=((6.0, 6.0), (float("nan"), 10.0))
        if where == "flow"
        else ((6.0, 6.0), (1.0, 10.0)),
    )
    if where == "weight":
        sw = env.ctx.network.snapshot_weightings
        sw.iloc[2, sw.columns.get_loc("generators")] = float("nan")
    result = _gate(env, "h2.electrolyser.utilisation")
    assert result["verdict"] == "not_evaluated" and result["facts"][0]["value"] is None


def test_a_zero_weight_snapshot_is_read_and_counts_nothing(tmp_path, monkeypatch):
    """A weight of 0 is a valid weighting (only negative or unreadable is
    not): 2040's (3, 1) becomes (3, 0), so its 10 MW hour counts nothing."""
    env = _h2_env(tmp_path, monkeypatch)
    sw = env.ctx.network.snapshot_weightings
    sw.iloc[3, sw.columns.get_loc("generators")] = 0.0
    rows = {
        r["period"]: r
        for r in _gate(env, "h2.electrolyser.utilisation")["facts"][0]["rows"]
    }
    assert rows[2040]["utilisation"] == pytest.approx(3.0 / 30.0)
    shed = run_gates.FACTS["h2.offtake.shed_hours"].read(env)
    assert [(r["period"], r["shed_hours"]) for r in shed.rows] == [
        (2030, 2.0),
        (2040, 0.0),
    ]


def test_an_infinite_weight_reads_no_utilisation(tmp_path, monkeypatch):
    env = _h2_env(tmp_path, monkeypatch)
    sw = env.ctx.network.snapshot_weightings
    sw.iloc[2, sw.columns.get_loc("generators")] = float("inf")
    result = _gate(env, "h2.electrolyser.utilisation")
    assert result["verdict"] == "not_evaluated" and result["facts"][0]["value"] is None


@pytest.mark.parametrize("flow, band_edge", [(4.0, 0.40), (9.5, 0.95)])
def test_utilisation_on_the_bands_edge_passes(tmp_path, monkeypatch, flow, band_edge):
    """The band [0.40, 0.95] is closed at both ends."""
    env = _h2_env(tmp_path, monkeypatch, dispatch=((flow, flow), (flow, flow)))
    rows = _gate(env, "h2.electrolyser.utilisation")["facts"][0]["rows"]
    assert all(
        r["utilisation"] == pytest.approx(band_edge) and r["verdict"] == "pass"
        for r in rows
    )


def test_a_failed_loop_job_keeps_its_own_outcome(tmp_path):
    """A loop step whose job settled without a record reads the job's own
    outcome, not the step's status."""
    from tests.test_harness_run_eh import ctx_for, env_for

    step = {
        "id": "result_gate.1",
        "tool": "run_coupling_loop",
        "engine": "eh.adequacy.lole",
        "status": "failed",
        "job": {"outcome": "cancelled"},
        "error": "the user cancelled the loop",
    }
    env = env_for(tmp_path, ctx=ctx_for(tmp_path), steps=[step])
    [result] = run_gates.evaluate([_rules(REFS["eh_h2_hub"])["eh.adequacy.lole"]], env)
    assert (
        result["facts"][0]["value"] == "cancelled"
        and result["verdict"] == "not_evaluated"
    )


def test_dispatch_within_a_relative_tolerance_of_a_large_capacity_is_read(
    tmp_path, monkeypatch
):
    """The capacity check's tolerance scales with the capacity (1e-6 of it):
    a 10 MW electrolyser at 10.000005 MW is the solver's rounding, not
    another capacity."""
    env = _h2_env(tmp_path, monkeypatch, dispatch=((6.0, 6.0), (1.0, 10.000005)))
    rows = {
        r["period"]: r
        for r in _gate(env, "h2.electrolyser.utilisation")["facts"][0]["rows"]
    }
    assert rows[2040]["utilisation"] == pytest.approx(0.325, rel=1e-6)


def test_a_capture_on_other_snapshots_is_no_evidence_of_shed(tmp_path, monkeypatch):
    """The capture's frame must be on the network's own snapshots."""
    env = _h2_env(tmp_path, monkeypatch)
    capture = env.ctx.solver_state["last_lost_load"]
    capture["lost_load_t"] = capture["lost_load_t"].iloc[::-1]
    value = run_gates.FACTS["h2.offtake.shed_hours"].read(env)
    assert (
        isinstance(value, run_gates.NoEvidence)
        and value.value == "no_lost_load_capture"
    )


def test_an_electrolyser_named_by_its_role_is_read_whatever_its_carrier(
    tmp_path, monkeypatch
):
    """The fleet is the Links whose `eh_role` says electrolyser first; the
    carrier is the fallback only."""
    env = _h2_env(tmp_path, monkeypatch)
    links = env.ctx.network.links
    links.loc["electrolyser", "carrier"] = "H2 conversion"
    links["eh_role"] = "electrolyser"
    result = _gate(env, "h2.electrolyser.utilisation")
    assert result["facts"][0]["value"] == pytest.approx(0.325)


# ── Q3-A1, Q3-A2 (GK B (39)): the staged envelope, lever by lever ────────────

# (component, name glob, attr) -> (upper bound, an instance, the class's built asset or None)
STAGED_LEVERS = {
    ("Generator", "wind_new*", "p_nom_max"): (300.0, "wind_new_2027", "wind_farm"),
    ("Generator", "solar_new*", "p_nom_max"): (150.0, "solar_new_2027", "solar_park"),
    ("StorageUnit", "bess_new*", "p_nom_max"): (200.0, "bess_new_2027", "site_battery"),
    ("Link", "electrolyser_new_*", "p_nom_max"): (
        80.0,
        "electrolyser_new_2035",
        "electrolyser",
    ),
    # The H2 store is one built asset and is itself the lever: no other Store to refuse.
    ("Store", "h2_storage", "e_nom"): (6000.0, "h2_storage", None),
}


def _static(component, name, attr):
    return float(_ctx().network.c[component].static.at[name, attr])


def test_the_staged_envelope_is_the_five_levers_with_their_bounds():
    """Q3-A1: each lever and its bound, and nothing else."""
    edits = {
        (e["target"]["component"], e["target"]["name"], e["target"]["attr"]): e[
            "bounds"
        ]
        for e in run_profiles.resolve(REFS["eh_h2_hub_staged"])["envelope"]["edits"]
    }
    assert edits == {k: {"min": 0.0, "max": v[0]} for k, v in STAGED_LEVERS.items()}


@pytest.mark.parametrize("template", ["eh_h2_hub_staged"], indirect=True)
@pytest.mark.parametrize("lever", list(STAGED_LEVERS), ids=lambda k: k[1])
def test_each_staged_lever_above_its_bound_is_refused(
    granted, monkeypatch, template, lever
):
    """Q3-A1: above each lever's bound is out_of_bounds and nothing changes."""
    ref = REFS[template]
    monkeypatch.setitem(run_profiles.PROFILES, ref, _without_eh(ref))
    component, _, attr = lever
    bound, name, _ = STAGED_LEVERS[lever]
    before = _static(component, name, attr)
    args = {"component_class": component, "name": name, "attrs": {attr: bound + 1.0}}
    assert _admit(granted, ref, args) == "out_of_bounds"
    assert _static(component, name, attr) == before


@pytest.mark.parametrize("template", ["eh_h2_hub_staged"], indirect=True)
@pytest.mark.parametrize(
    "lever",
    [k for k, v in STAGED_LEVERS.items() if v[2] is not None],
    ids=lambda k: k[1],
)
def test_each_classes_built_asset_is_outside_the_staged_entry(
    granted, monkeypatch, template, lever
):
    """Q3-A1, at `_match_edit`: the built (non-_new_) asset of each lever's
    class is outside the entry, even at a value within the lever's bound."""
    ref = REFS[template]
    monkeypatch.setitem(run_profiles.PROFILES, ref, _without_eh(ref))
    component, _, attr = lever
    built = STAGED_LEVERS[lever][2]
    before = _static(component, built, attr)
    args = {"component_class": component, "name": built, "attrs": {attr: 10.0}}
    assert before != 10.0 and _admit(granted, ref, args) == "outside_entry"
    assert _static(component, built, attr) == before


def test_both_hubs_offer_the_same_levers():
    """Q3-A2: the staged and single-year envelopes size the same component
    classes by the same capacity field, so they cannot drift apart. The
    active attr differs only by `_max` (H4: the single-year hub's built
    electrolyser by p_nom, the staged hub's new ones by their cap)."""

    def shape(ref):
        edits = run_profiles.resolve(ref)["envelope"]["edits"]
        return sorted(
            (e["target"]["component"], e["target"]["attr"].removesuffix("_max"))
            for e in edits
        )

    assert shape(REFS["eh_h2_hub_staged"]) == shape(REFS["eh_h2_hub"])
    assert len(shape(REFS["eh_h2_hub"])) == len(STAGED_LEVERS)


# ── H1, H2: the readers' F6 guards and their weightings, on two periods ──────


def _h2_env(
    tmp_path,
    monkeypatch,
    *,
    loads=("h2_offtake",),
    shed=((1.0, 0.0), (0.0, 1.0)),
    dispatch=((6.0, 6.0), (1.0, 10.0)),
    job=None,
    exact="x1",
):
    """Two investment periods of two snapshots: generator weightings (2, 2)
    and (3, 1); objective weightings (1, 1) and (4, 4) and period years 5
    and 20, unequal so that neither may enter; an H2 bus with `loads`, a 10 MW electrolyser feeding it."""
    from contextlib import nullcontext

    from harness import calculations, durable
    from tests.test_harness_run_eh import DONE_JOB, ctx_for, env_for

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.investment_periods = [2030, 2040]
    n.snapshot_weightings.loc[:, "generators"] = [2.0, 2.0, 3.0, 1.0]
    n.snapshot_weightings.loc[:, "objective"] = [1.0, 1.0, 4.0, 4.0]
    n.investment_period_weightings.loc[:, "years"] = [5.0, 20.0]
    n.add("Bus", "el")
    n.add("Bus", "h2", carrier="H2")
    n.add("Load", "site", bus="el", p_set=5.0)
    for name in loads:
        n.add("Load", name, bus="h2", p_set=1.0)
    n.add(
        "Link", "electrolyser", bus0="el", bus1="h2", p_nom=10.0, carrier="electrolysis"
    )
    n.links_t.p0 = pd.DataFrame(
        {"electrolyser": [v for p in dispatch for v in p]}, index=n.snapshots
    )
    n.meta = {"assistant_solve_exact_input_fingerprint": exact}
    frame = pd.DataFrame(
        {name: [v for p in shed for v in p] for name in ("site", *loads)},
        index=n.snapshots,
    )
    ctx = ctx_for(
        tmp_path, last_lost_load={"lost_load_t": frame, "voll_eur_per_mwh": 1000.0}
    )
    ctx.network = n
    monkeypatch.setattr(run_gates, "_on", lambda c, fn, *a: fn(*a))
    monkeypatch.setattr(durable, "network_guard", lambda: nullcontext())
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda c: True)
    monkeypatch.setattr(
        run_gates, "_solve_job", lambda env: DONE_JOB if job is None else job
    )
    return env_for(tmp_path, ctx=ctx)


def _gate(env, rid, ref="eh_h2_hub_staged@1"):
    [result] = run_gates.evaluate([_rules(ref)[rid]], env)
    return result


def test_shed_hours_weigh_each_snapshot_by_its_generator_weighting_and_read_the_worst_period(
    tmp_path, monkeypatch
):
    """H2: 2030 sheds one 2 h snapshot, 2040 one 1 h snapshot. Unit weights
    read 1 and 1; objective weights 1 and 4; years 10 and 20 (2040 worst
    on each); a sum over periods 3."""
    scored = run_gates.FACTS["h2.offtake.shed_hours"].read(
        _h2_env(tmp_path, monkeypatch)
    )
    assert scored.value == 2.0
    assert [(r["period"], r["shed_hours"], r["worst"]) for r in scored.rows] == [
        (2030, 2.0, True),
        (2040, 1.0, False),
    ]
    result = _gate(_h2_env(tmp_path, monkeypatch), "h2.offtake.met")
    assert result["verdict"] == "fail" and not run_gates.passed([result])


def test_utilisation_weighs_each_snapshot_by_its_generator_weighting_and_reads_the_period_outside_the_band(
    tmp_path, monkeypatch
):
    """H2: 2030 (6, 6) on (2, 2) reads 0.6; 2040 (1, 10) on (3, 1) reads
    13/40 = 0.325, below the band, though unit or objective weights would
    read 0.55."""
    result = _gate(_h2_env(tmp_path, monkeypatch), "h2.electrolyser.utilisation")
    rows = {r["period"]: r for r in result["facts"][0]["rows"]}
    assert (
        rows[2030]["utilisation"] == pytest.approx(0.6)
        and rows[2030]["verdict"] == "pass"
    )
    assert (
        rows[2040]["utilisation"] == pytest.approx(0.325)
        and rows[2040]["verdict"] == "fail"
    )
    assert rows[2040]["worst"] and result["facts"][0]["value"] == pytest.approx(0.325)
    assert (
        result["verdict"] == "fail"
        and result["severity"] == "warn"
        and run_gates.passed([result])
    )


@pytest.mark.parametrize(
    "loads, expected",
    [
        ((), "offtake_loads:0"),  # the offtake is missing
        (("h2_offtake", "h2_export"), "offtake_loads:2"),  # several
    ],
)
def test_a_missing_or_second_offtake_is_no_evidence(
    tmp_path, monkeypatch, loads, expected
):
    result = _gate(_h2_env(tmp_path, monkeypatch, loads=loads), "h2.offtake.met")
    assert (
        result["verdict"] == "not_evaluated" and result["facts"][0]["value"] == expected
    )
    assert not run_gates.passed([result])


def test_a_renamed_offtake_is_found_by_its_h2_bus(tmp_path, monkeypatch):
    scored = run_gates.FACTS["h2.offtake.shed_hours"].read(
        _h2_env(tmp_path, monkeypatch, loads=("ammonia_plant",))
    )
    assert scored.value == 2.0


@pytest.mark.parametrize(
    "change, reason",
    [
        (
            "infeasible",
            "solve_failed",
        ),  # the run's solve did not complete (an infeasible LP fails the job)
        ("not_applied", "inputs_changed"),
        ("no_identity", "no_capture"),
        ("other_solve", "other_solve"),
        ("no_capture", "no_lost_load_capture"),
        ("stale", "stale_solve"),
        ("no_solve", "not_solved"),
    ],
)
@pytest.mark.parametrize("rid", ["h2.offtake.met", "h2.electrolyser.utilisation"])
def test_without_the_runs_own_solve_neither_reader_has_evidence(
    tmp_path, monkeypatch, change, reason, rid
):
    """H1: never a value from a failed, unapplied, unidentified, other or
    stale solve, nor from a missing capture: the offtake rule never passes."""
    from harness import calculations

    job = {
        "infeasible": {"status": "failed", "capture": {"applied": True, "exact": "x1"}},
        "not_applied": {
            "status": "completed",
            "capture": {"applied": False, "reason": "inputs_changed"},
        },
        "no_identity": {"status": "completed", "capture": {"applied": True}},
    }.get(change)
    env = _h2_env(
        tmp_path, monkeypatch, job=job, exact="x2" if change == "other_solve" else "x1"
    )
    if change == "no_capture":
        env.ctx.solver_state.pop("last_lost_load")
    if change == "stale":
        monkeypatch.setattr(calculations, "solve_matches_inputs", lambda c: False)
    if change == "no_solve":
        monkeypatch.setattr(run_gates, "_solve_job", lambda env: None)
    result = _gate(env, rid)
    if rid == "h2.electrolyser.utilisation" and change == "no_capture":
        return  # utilisation reads the dispatch, never the shed capture
    assert result["verdict"] == "not_evaluated", result
    assert result["facts"][0]["value"] == reason  # H1: typed, never a bare none
    if rid == "h2.offtake.met":
        assert not run_gates.passed([result])


@pytest.mark.parametrize("template", list(REFS))
def test_the_lole_target_is_the_goal_on_each_hubs_horizon(template):
    """H6: the goal's h/yr target goes on the template's horizon through
    `loop_goal` with `metrics.horizon_years` (the run's card and the loop
    read the same); the staged hub's horizon is longer than a year."""
    import types

    from harness import run_loops
    from services.adequacy.metrics import horizon_years
    from services.solver_service import SolverConfig

    n = T.BUILDERS[template]()
    years = float(horizon_years(n))
    assert (years > 1.5) == (template == "eh_h2_hub_staged")
    ctx = types.SimpleNamespace(
        network=n,
        solver_state={
            "solver_config": SolverConfig(
                **{
                    **T.SOLVER_CONFIG,
                    **T.SOLVER_CONFIG_EXTRA.get(template, {}),
                    "target_lole_h": 2.0,
                }
            )
        },
    )
    assert run_loops._card_target(ctx) == pytest.approx(2.0 * years)


# ── H7: the solve budget ─────────────────────────────────────────────────────


@pytest.mark.parametrize("ref", REFS.values())
def test_the_budget_covers_the_solve_two_l2_rounds_the_loop_and_its_restore(ref):
    """H7: every reservation at its cap. The solve stage runs the queued
    solve and the EH study, again in each L2 round; then the coupling loop
    and its closing re-solve."""
    from harness import run_loops

    profile = run_profiles.resolve(ref)
    eh = profile["eh_study"]["budget_solves"]
    lole = _rules(ref)["eh.adequacy.lole"]["engine"]
    solve_stage = 1 + eh
    worst = (
        solve_stage
        + run_profiles.L2_ROUNDS * solve_stage
        + lole["max_solves"]
        + run_loops._overhead(lole["tool"])
    )
    assert (
        eh,
        run_profiles.L2_ROUNDS,
        lole["max_solves"],
        run_loops._overhead(lole["tool"]),
    ) == (2, 2, 3, 1)
    assert profile["envelope"]["budget"]["solves"] == worst == 13


def _spend(path, solves):
    envelope = run_envelope._load(path)
    envelope["audit"] += [
        {"event": "used", "tool": "solve_queue_enqueue", "call_id": f"spent-{i}"}
        for i in range(solves)
    ]
    run_envelope._save(path, envelope)


@pytest.mark.parametrize("template", ["eh_h2_hub"], indirect=True)
def test_the_loop_reserves_within_the_budget_and_stops_typed_when_it_is_spent(
    granted, monkeypatch, template
):
    """H7: with 4 solves left the coupling loop reserves 3 and its closing
    re-solve; with 1 left nothing, `budget_spent`."""
    ref = REFS[template]
    lole = _rules(ref)["eh.adequacy.lole"]["engine"]
    monkeypatch.setitem(run_profiles.PROFILES, ref, _without_eh(ref))
    task, path, _ = granted(run={**RUN, "profile": ref}, detach=False)
    budget = run_envelope._load(path)["budget"]["solves"]

    def reserve(call_id):
        return run_envelope.reserve_engine(
            path,
            call_id=call_id,
            tool=lole["tool"],
            rule="eh.adequacy.lole",
            max_solves=lole["max_solves"],
            overhead=1,
            restore=lole["restore"],
        )

    _spend(path, budget - 4)
    assert reserve("loop-1") == (3, None)
    assert run_envelope.solves_used(run_envelope._load(path)) == budget
    assert reserve("loop-2") == (None, "budget_spent")
