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
import json
import sys
from pathlib import Path

import pandas as pd
import pypsa
import pytest
from fastapi.testclient import TestClient  # noqa: F401 - the client fixture's type

from harness import runner as harness_runner
from services import chat_tools, flow_service, project_registry, run_envelope, run_gates, run_profiles
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
def test_each_profile_serves_its_template_with_the_eh_study_and_the_coupling_loop(template, ref):
    profile = run_profiles.resolve(ref)
    assert profile["templates"] == [template]
    assert _rules(ref, "setup")["set.project.template"]["value"] == template
    solve = [a["tool"] for a in profile["stages"]["solve"]["actions"]]
    assert solve == ["solve_queue_enqueue", "run_eh_study"]
    lole = _rules(ref)["eh.adequacy.lole"]
    assert lole["engine"] == {"tool": "run_coupling_loop", "max_solves": 3, "restore": "base"}
    assert run_profiles.needs_goal(profile) and profile["params"]["required"] == []
    assert [r["id"] for r in profile["stages"]["result_gate"]["rules"]] == [
        "res.solve.status", "res.fresh", "kpi.unserved_mwh", "h2.offtake.met", "h2.electrolyser.utilisation",
        "h2.ic.counterfactual", "eh.adequacy.lole"]


@pytest.mark.parametrize("ref", REFS.values())
def test_the_offtake_blocks_and_utilisation_and_the_investment_case_only_warn(ref):
    rules = _rules(ref)
    offtake = rules["h2.offtake.met"]
    assert (offtake["fact"], offtake["op"], offtake["value"], offtake["severity"]) == (
        "h2.offtake.shed_hours", "eq", 0.0, "block")
    use = rules["h2.electrolyser.utilisation"]
    assert (use["op"], use["value"], use["severity"]) == ("within", run_profiles.H2_UTILISATION_BAND, "warn")
    ic = rules["h2.ic.counterfactual"]
    assert (ic["fact"], ic["op"], ic["value"], ic["severity"]) == ("ic.counterfactual", "eq", "established", "warn")


@pytest.mark.parametrize("ref", REFS.values())
def test_the_offtake_and_the_connection_are_never_levers(ref):
    """§6.0: the offtake is demand; grid_import and the fuel cell are not
    in the envelope, though Link.p_nom is the class lever."""
    profile = run_profiles.resolve(ref)
    attrs = {t["attr"] for t in profile["tunable"]}
    assert not attrs & run_profiles.NEVER_TUNABLE
    names = [e["target"]["name"] for e in profile["envelope"]["edits"]]
    for name in ("h2_offtake", "grid_import", "fuel_cell", "grid_supply", "wind_farm", "solar_park"):
        assert not any(pd.Series([name]).str.match(n.replace("*", ".*") + "$").iloc[0] for n in names), name
    with pytest.raises(run_profiles.ProfileError, match="never tunable"):
        run_profiles.load({**profile, "tunable": [*profile["tunable"], {"component": "Load", "attr": "p_set"}]})
    bounded = {(e["target"]["name"], e["target"]["attr"]): e["bounds"] for e in profile["envelope"]["edits"]}
    assert all(b["min"] == 0.0 and b["max"] > 0 and "rel" not in b for b in bounded.values())


def test_the_single_year_hub_sizes_its_electrolyser_and_the_staged_hub_its_new_ones():
    single = {(e["target"]["component"], e["target"]["name"], e["target"]["attr"])
              for e in run_profiles.resolve("eh_h2_hub@1")["envelope"]["edits"]}
    staged = {(e["target"]["component"], e["target"]["name"], e["target"]["attr"])
              for e in run_profiles.resolve("eh_h2_hub_staged@1")["envelope"]["edits"]}
    assert ("Link", "electrolyser", "p_nom") in single and ("Store", "h2_storage", "e_nom") in single
    assert ("Link", "electrolyser_new_*", "p_nom_max") in staged and ("Link", "electrolyser", "p_nom") not in staged
    # Every envelope edit is one of the profile's tunables (checked at load).
    for ref in REFS.values():
        profile = run_profiles.resolve(ref)
        tunable = {(t["component"], t["attr"]) for t in profile["tunable"]}
        assert {(e["target"]["component"], e["target"]["attr"]) for e in profile["envelope"]["edits"]} == tunable
        assert set(_rules(ref)["h2.offtake.met"]["tunable"]) == {f"{c}.{a}" for c, a in tunable}


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
    ctx.solver_state["solver_config"] = SolverConfig(**{**T.SOLVER_CONFIG, **T.SOLVER_CONFIG_EXTRA.get(template, {}),
                                                       "target_lole_h": 2.0})
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
        run_envelope.decide(session, task["task_id"], "unattended", goal=task["envelope"]["goal"])
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
    return call("update_component", {"component_class": "Link", "name": "electrolyser", "attrs": {"p_nom": value}}, n)


def test_an_undersized_electrolyser_fails_the_offtake_and_l2_sizes_it_within_bounds(
        granted, rated, monkeypatch):
    """Acceptance: 20 MW cannot serve 25 MW of H2 at 68 % (36.8 MW in), so
    the offtake sheds; one L2 round raises the electrolyser within the
    envelope's bounds, the re-solve serves every hour."""
    monkeypatch.setitem(run_profiles.PROFILES, "eh_h2_hub@1", _without_eh("eh_h2_hub@1"))
    _ctx().network.links.at["electrolyser", "p_nom"] = 20.0
    task, path, _ = granted(run={**RUN, "profile": "eh_h2_hub@1"})
    script = Script(_electrolyser(60.0), say("The offtake shed: 20 MW of electrolyser feeds 13.6 MW of H2. Raised it."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert "loop" in run, (run["status"], run["stage"], [(s["id"], s["status"], s.get("error")) for s in record["steps"]])
    first = next(c for c in run["loop"]["history"])
    assert {f["rule"] for f in first["findings"]} >= {"h2.offtake.met"}
    assert run["status"] == "completed", (run["stage"], run["stages"][run["stage"]].get("reason"))
    assert run["loop"]["kind"] == "L2" and run["loop"]["rounds"] <= 2 and run["loop"]["ended"] == "passed"
    [row] = run["ledger"]
    assert row["target"] == {"component": "Link", "name": "electrolyser", "attr": "p_nom"} and row["new"] == 60.0
    verdicts = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert verdicts["h2.offtake.met"]["verdict"] == "pass"
    assert verdicts["h2.offtake.met"]["facts"][0]["value"] == 0.0
    assert verdicts["kpi.unserved_mwh"]["verdict"] == "pass"
    assert verdicts["h2.ic.counterfactual"]["verdict"] == "not_evaluated"
    assert float(_ctx().network.links.at["electrolyser", "p_nom"]) == 60.0


def test_an_electrolyser_beyond_the_envelopes_bound_is_refused_and_nothing_changes(granted, rated, monkeypatch):
    monkeypatch.setitem(run_profiles.PROFILES, "eh_h2_hub@1", _without_eh("eh_h2_hub@1"))
    _ctx().network.links.at["electrolyser", "p_nom"] = 20.0
    task, path, _ = granted(run={**RUN, "profile": "eh_h2_hub@1"})
    script = Script(_electrolyser(200.0), say("Tried 200 MW."), say("Nothing more within the bounds."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    assert record["run"]["status"] != "completed"
    assert [t for t, _ in refused(path)] == ["update_component"]
    assert record["run"]["ledger"] == []
    assert float(_ctx().network.links.at["electrolyser", "p_nom"]) == 20.0


@pytest.mark.parametrize("template", list(REFS), indirect=True)
def test_a_run_on_each_template_reaches_the_hand_off_with_the_offtake_and_lole_passing(
        goal_granted, rated, fake_report, template):
    """Acceptance: the shipped profile, EH study and coupling loop real; the
    EH study is the user's card. The offtake and LOLE rules pass; the
    investment rule is not evaluated (no case was run) and the run passes."""
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks

    task, path = goal_granted(REFS[template])
    clicks = Clicks(refs={"solve.2"})
    record = go(run_runner(clicks), path, task)
    run = record["run"]
    assert run["status"] == "completed", json.dumps([run["stage"], run.get("reason"), run.get("waiting"), [
        (r["rule"], r["verdict"], r.get("reason"), r["facts"][0]["value"])
        for r in (run["stages"]["result_gate"].get("gate") or {}).get("rules", [])],
        [(s["id"], s["tool"], s["status"], s.get("error")) for s in record["steps"][5:]]], default=str)
    assert clicks.used == ["solve.2"]
    verdicts = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert verdicts["h2.offtake.met"]["verdict"] == verdicts["eh.adequacy.lole"]["verdict"] == "pass"
    assert verdicts["h2.ic.counterfactual"]["verdict"] == "not_evaluated"
    assert verdicts["h2.ic.counterfactual"]["reason"].startswith("not_run")
    use = verdicts["h2.electrolyser.utilisation"]
    assert use["verdict"] in ("pass", "fail") and 0.0 < use["facts"][0]["value"] <= 1.0
    engine = next(s for s in record["steps"] if s.get("engine"))
    assert engine["tool"] == "run_coupling_loop" and engine["status"] == "completed"
    assert run["stages"]["handoff"]["status"] == "passed"


# ── the readers, on solved networks (no run) ─────────────────────────────────


def _solved(template, **links):
    import logging
    logging.disable(logging.WARNING)
    try:
        n = T.BUILDERS[template]()
        for name, p_nom in links.items():
            n.links.at[name, "p_nom"] = p_nom
        n.optimize(solver_name="highs", solver_options={"output_flag": False},
                   multi_investment_periods=bool(len(n.investment_periods)))
    finally:
        logging.disable(logging.NOTSET)
    return n


def _rule(rid):
    return {**_rules("eh_h2_hub@1")[rid]}


def _read_on(monkeypatch, n, reader, *args, capture=None, current=True):
    """`reader` on a context holding `n`, past `_own_solve`'s identity checks."""
    from harness import calculations
    ctx = type("Ctx", (), {"network": n, "solver_state": {"last_lost_load": capture or {}}})()
    monkeypatch.setattr(run_gates, "_own_solve", lambda env, read: read(ctx))
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda c: current)
    return reader(None, *args)


def test_the_offtake_is_the_one_load_on_an_h2_bus():
    n = T.BUILDERS["eh_h2_hub"]()
    assert run_gates._offtake_load(n) == "h2_offtake"
    n.add("Load", "second", bus="h2", p_set=1.0)
    assert isinstance(run_gates._offtake_load(n), run_gates.NoEvidence)
    n.remove("Load", ["second", "h2_offtake"])
    missing = run_gates._offtake_load(n)
    assert isinstance(missing, run_gates.NoEvidence) and missing.value == "offtake_loads:0"


def test_the_electrolyser_fleet_is_named_by_its_role_else_its_carrier():
    n = T.BUILDERS["eh_h2_hub_staged"]()
    assert run_gates._electrolysers(n) == ["electrolyser", *(f"electrolyser_new_{y}" for y in (2027, 2030, 2035))]
    n.links["eh_role"] = ""
    assert "electrolyser" in run_gates._electrolysers(n)  # carrier "electrolysis"
    n.links["carrier"] = "AC"
    assert run_gates._electrolysers(n) == []


def test_utilisation_is_the_input_energy_over_capacity_on_the_shipped_hub(monkeypatch):
    n = _solved("eh_h2_hub")
    u = _read_on(monkeypatch, n, run_gates._electrolyser_utilisation, _rule("h2.electrolyser.utilisation"))
    w = n.snapshot_weightings["generators"]
    expected = float((n.links_t.p0["electrolyser"].clip(lower=0) * w).sum() / (60.0 * w.sum()))
    assert u == pytest.approx(expected) and 0.0 < u <= 1.0


def test_staged_utilisation_has_a_row_per_period_and_reads_the_period_furthest_outside(monkeypatch):
    n = _solved("eh_h2_hub_staged")
    lo, hi = run_profiles.H2_UTILISATION_BAND
    scored = _read_on(monkeypatch, n, run_gates._electrolyser_utilisation, _rule("h2.electrolyser.utilisation"))
    assert [r["period"] for r in scored.rows] == [2027, 2030, 2035]
    worst = next(r for r in scored.rows if r["worst"])
    gap = [max(lo - r["utilisation"], r["utilisation"] - hi, 0.0) for r in scored.rows]
    assert scored.value == worst["utilisation"] and gap[scored.rows.index(worst)] == max(gap)
    assert all(r["verdict"] == ("pass" if lo <= r["utilisation"] <= hi else "fail") and r["assumed"] is False
               for r in scored.rows)


def test_no_electrolyser_or_no_capacity_is_no_evidence_never_zero(monkeypatch):
    n = _solved("eh_h2_hub")
    n.links.at["electrolyser", "p_nom_opt"] = 0.0  # a built Link reads its p_nom (as the app's solve leaves it)
    assert _read_on(monkeypatch, n, run_gates._electrolyser_utilisation, _rule("h2.electrolyser.utilisation")) > 0
    n.links.at["electrolyser", "p_nom"] = 0.0
    assert _read_on(monkeypatch, n, run_gates._electrolyser_utilisation, _rule("h2.electrolyser.utilisation")) is None
    n.links["eh_role"], n.links["carrier"] = "", "AC"
    read = _read_on(monkeypatch, n, run_gates._electrolyser_utilisation, _rule("h2.electrolyser.utilisation"))
    assert isinstance(read, run_gates.NoEvidence) and read.value == "no_electrolyser"


def _staged_capture(n, shed_2030=0.0):
    frame = pd.DataFrame(0.0, index=n.snapshots, columns=list(n.loads.index))
    frame.loc[n.snapshots.get_level_values(0) == 2030, "h2_offtake"] = shed_2030
    frame.iloc[0, frame.columns.get_loc("h2_offtake")] = 0.0
    return {"lost_load_t": frame}


def test_staged_offtake_counts_shed_hours_per_period_and_reads_the_worst(monkeypatch):
    n = T.BUILDERS["eh_h2_hub_staged"]()
    w = n.snapshot_weightings["generators"]
    scored = _read_on(monkeypatch, n, run_gates._offtake_shed_hours, capture=_staged_capture(n, 2.0))
    rows = {r["period"]: r for r in scored.rows}
    expected = float(w[n.snapshots.get_level_values(0) == 2030].sum())
    assert rows[2030]["shed_hours"] == pytest.approx(expected) and rows[2030]["worst"]
    assert rows[2027]["shed_hours"] == rows[2035]["shed_hours"] == 0.0 and scored.value == rows[2030]["shed_hours"]
    # Shed at or below the threshold is LP dust, never an hour.
    dust = _read_on(monkeypatch, n, run_gates._offtake_shed_hours, capture=_staged_capture(n, 1e-4))
    assert dust.value == 0.0


@pytest.mark.parametrize("capture, current, expected", [
    (None, True, "no_lost_load_capture"),  # no capture: never 0
    ("other_load", True, "no_lost_load_capture"),  # a capture without the offtake
    ("ok", False, None),  # a stale solve has no value
])
def test_staged_offtake_without_its_own_capture_is_no_evidence(monkeypatch, capture, current, expected):
    n = T.BUILDERS["eh_h2_hub_staged"]()
    frame = _staged_capture(n) if capture == "ok" else (
        {"lost_load_t": _staged_capture(n)["lost_load_t"].drop(columns=["h2_offtake"])} if capture else None)
    read = _read_on(monkeypatch, n, run_gates._offtake_shed_hours, capture=frame, current=current)
    if expected is None:
        assert read is None
    else:
        assert isinstance(read, run_gates.NoEvidence) and read.value == expected


# ── the investment rule (issue 19's fact) ───────────────────────────────────


@pytest.mark.parametrize("state, reasons, verdict", [
    ("established", [], "pass"),
    ("not_established", ["counterfactual_not_established:no_reference_price"], "not_evaluated"),
    ("not_run", ["no_case"], "not_evaluated"),
])
def test_the_investment_rule_is_evaluated_once_established_and_never_blocks(monkeypatch, state, reasons, verdict):
    from services import ic_counterfactual as ic
    monkeypatch.setattr(ic, "read", lambda ctx, on: {"state": state, "basis": "site_today", "reasons": reasons})
    env = run_gates.Env(task={}, ctx=object(), project=None, project_dir=None)
    [result] = run_gates.evaluate([_rule("h2.ic.counterfactual")], env)
    assert result["verdict"] == verdict and result["severity"] == "warn"
    if verdict == "not_evaluated":
        assert result["reason"] == f"{state}: {', '.join(reasons)}"
    assert run_gates.passed([result])
