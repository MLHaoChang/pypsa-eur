# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 13: eh_ammonia_staged@1 on the shipped template, as the
run's own project: the envelope's levers at admission and runs through L2
(GK A (24) C5, C6; acceptance 1-3). The readers are pinned on contexts of
their own in `test_run_profile_ammonia.py`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pypsa
import pytest
from fastapi.testclient import TestClient  # noqa: F401 - the client fixture's type

from harness import runner as harness_runner
from services import chat_tools, flow_service, project_registry, run_profiles
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import helpers_state, session  # noqa: F401
from tests.test_harness_envelope import envelope_of, granted, profile  # noqa: F401
from tests.test_harness_run_loops import Script, call, go, rated, refused, say  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN
from tests.test_harness_runner import detached, fake_report_provider  # noqa: F401
from tests.test_run_profile_ammonia import LEVERS, NEVER, REF, TEMPLATE, YEARS

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

# The fixtures this file's tests take by name (imported above, used here).
FIXTURES = (granted, rated)


# ── runs on the shipped template ─────────────────────────────────────────────


@pytest.fixture
def baseline(client, session_ctx, install_network, tmp_path):
    """The shipped template network as the run's project, read back from
    its netCDF as a saved project is, with the template's solver settings."""
    from services.solver_service import SolverConfig

    T.BUILDERS[TEMPLATE]().export_to_netcdf(tmp_path / "template.nc")
    install_network(pypsa.Network(tmp_path / "template.nc"))
    name = "Ammonia hub"
    response = client.post(f"/api/projects/{name}")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    ctx.solver_state["solver_config"] = SolverConfig(**T.solver_config(TEMPLATE))
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
def site(baseline):
    _, project_id, root = baseline
    (root / flow_service.TEMPLATE_FILE).write_text(json.dumps({"id": TEMPLATE}))
    return project_id, root


def _ctx():
    return PyPSAService.get_active_context()


def _admit(granted, args):
    from tests.test_harness_envelope import ask, on_run, refusals

    task, path, _ = granted(run={**RUN, "profile": REF}, detach=False)
    with on_run(task, path) as run:
        assert ask(run, "update_component", args) is None
    return refusals(path)[-1]


@pytest.mark.parametrize("lever", list(LEVERS), ids=lambda k: k[1])
def test_each_lever_above_its_bound_is_refused(granted, lever):
    """C5: one over-bound change per lever is out_of_bounds; nothing changes."""
    component, name, attr = lever
    before = float(_ctx().network.c[component].static.at[name, attr])
    args = {
        "component_class": component,
        "name": name,
        "attrs": {attr: LEVERS[lever] + 1.0},
    }
    assert _admit(granted, args) == "out_of_bounds"
    assert float(_ctx().network.c[component].static.at[name, attr]) == before


@pytest.mark.parametrize(
    "component, name", [c for c in NEVER if c[0] != "Load"], ids=lambda v: str(v)
)
def test_a_never_lever_is_outside_the_envelope(granted, component, name):
    """C5, at `_match_edit`: the lever attr of the class, within a lever's
    bound, is still outside the entry for this component."""
    attr = "e_nom_max" if component == "Store" else "p_nom_max"
    before = float(_ctx().network.c[component].static.at[name, attr])
    args = {"component_class": component, "name": name, "attrs": {attr: 10.0}}
    assert _admit(granted, args) == "outside_entry"
    assert float(_ctx().network.c[component].static.at[name, attr]) == before


def _flag_below(monkeypatch, store, threshold, seen):
    """The binary check as the solve job runs it, flagged while `store`'s
    cap is below `threshold`: the check each solve leaves is its own."""
    from services import solver_service

    real = solver_service._process_uc

    def check(n, cfg, *, solver_options=None, solve=None):
        cap = float(n.stores.at[store, "e_nom_max"])
        seen.append(cap)
        flagged = cap < threshold
        rows = [{**real._empty_row(p), "flagged": flagged and p == 2035} for p in YEARS]
        return {**real._payload(n, "ok", flagged, f"cap {cap}", rows, [], 0.0)}

    monkeypatch.setattr(real, "binary_check", check)


def test_a_flagged_check_fails_and_l2_clears_it_on_the_new_solves_check(
    granted, rated, monkeypatch, site
):
    """Acceptance 1 and C6: round 1's check is flagged (period 2035); one L2
    change within bounds; the result gate reads the re-solve's own check."""
    from services.reports import chapters

    seen = []
    _flag_below(monkeypatch, "nh3_tank", 7000.0, seen)
    # Acceptance 3: the coupling report not established (the shipped
    # template establishes it; here its payload is unreadable).
    monkeypatch.setattr(chapters, "grid_risk_payload", lambda source: None)
    task, path, _ = granted(run={**RUN, "profile": REF})
    script = Script(
        call(
            "update_component",
            {
                "component_class": "Store",
                "name": "nh3_tank",
                "attrs": {"e_nom_max": 9000.0},
            },
            0,
        ),
        say("The binary check flagged 2035; the NH3 tank cap was raised."),
    )
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert "loop" in run, (
        run["status"],
        run["stage"],
        [(s["id"], s["status"], s.get("error")) for s in record["steps"]],
    )
    first = run["loop"]["history"][0]
    check = next(f for f in first["findings"] if f["rule"] == "nh3.binary_check")
    assert check["verdict"] == "fail", (check, seen)
    assert [r["period"] for r in check["facts"][0]["detail"]] == [2035]
    assert seen == [6000.0, 9000.0]
    assert run["status"] == "completed", (
        run["stage"],
        run["stages"][run["stage"]].get("reason"),
    )
    assert run["loop"]["kind"] == "L2" and run["loop"]["ended"] == "passed"
    [row] = run["ledger"]
    assert row["target"] == {
        "component": "Store",
        "name": "nh3_tank",
        "attr": "e_nom_max",
    }
    verdicts = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert verdicts["nh3.binary_check"]["verdict"] == "pass"
    assert verdicts["nh3.delivery.shortfall"]["verdict"] == "pass"
    assert verdicts["nh3.ic.counterfactual"]["verdict"] == "not_evaluated"
    # Acceptance 3: the coupling report is a chapter; not established, it is a listed gap.
    report = {r["rule"]: r for r in run["stages"]["report"]["gate"]["rules"]}
    assert report["rp.report.unsupported_listed"]["verdict"] == "pass"
    from harness import run_report
    from services.reports.evidence import SECTION_TITLES

    doc = run_report.saved(record, site[1])
    sections = {sec.section_id: sec for sec in doc.sections}
    gaps = [str(row[0]) for row in doc.tables["harness_gaps"].rows if row]
    assert sections["grid_risk"].status != "ok"
    assert any(
        g.startswith(run_report.CHAPTER_GAP + SECTION_TITLES["grid_risk"]) for g in gaps
    ), gaps
    assert run["stage"] == "handoff"


def test_a_delivery_shortfall_fails_and_l2_changes_only_levers(
    granted, rated, monkeypatch
):
    """Acceptance 2: almost no new electrolyser and almost no grey NH3, so
    the hub falls short of the 2030 delivery; L2 raises that candidate's cap
    within its bound and the re-solve delivers 2030; what remains short
    still fails."""
    n = _ctx().network
    for y in YEARS:
        n.links.at[f"electrolyser_new_{y}", "p_nom_max"] = (
            1.0  # a candidate built in 2027 runs on in 2030
        )
    n.generators.at["__buy_nh3", "p_nom"] = 0.01
    task, path, _ = granted(run={**RUN, "profile": REF})
    script = Script(
        call(
            "update_component",
            {
                "component_class": "Link",
                "name": "electrolyser_new_2030",
                "attrs": {"p_nom_max": 80.0},
            },
            0,
        ),
        say(
            "The 2030 NH3 delivery fell short for want of H2; raised its new electrolyser's cap."
        ),
        say("Nothing more within the levers."),
    )
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert "loop" in run, (
        run["status"],
        run["stage"],
        [(s["id"], s["status"], s.get("error")) for s in record["steps"]],
    )
    first = next(
        f
        for f in run["loop"]["history"][0]["findings"]
        if f["rule"] == "nh3.delivery.shortfall"
    )
    assert (
        first["verdict"] == "fail"
        and first["facts"][0]["value"] > run_profiles.NH3_SHORTFALL_MAX
    )
    [row] = run["ledger"]
    assert row["target"] == {
        "component": "Link",
        "name": "electrolyser_new_2030",
        "attr": "p_nom_max",
    }
    before = {r["period"]: r for r in first["facts"][0]["rows"]}
    assert before[2030]["shortfall_share"] > run_profiles.NH3_SHORTFALL_MAX
    # The re-solve's own reading: 2030 delivers; 2035 stays short (no grey
    # NH3 to cover the Haber-Bosch loop's part-load), so the rule still
    # fails and the run goes to the user, never passes on round 1's reading.
    verdicts = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    rows = {
        r["period"]: r for r in verdicts["nh3.delivery.shortfall"]["facts"][0]["rows"]
    }
    assert rows[2030]["shortfall_share"] <= run_profiles.NH3_SHORTFALL_MAX
    assert (
        verdicts["nh3.delivery.shortfall"]["verdict"] == "fail" and rows[2035]["worst"]
    )
    assert run["status"] == "needs_user"
    tunable = {
        (t["component"], t["attr"]) for t in run_profiles.resolve(REF)["tunable"]
    }
    assert all(
        (r["target"]["component"], r["target"]["attr"]) in tunable
        for r in run["ledger"]
    )


def test_an_unverified_check_goes_to_the_user_and_l2_changes_nothing(
    granted, rated, monkeypatch
):
    """GK A (24) Q2 = A: an unverified check is no evidence; the result stage
    ends for the user with no L2 round, no change and no further solve."""
    from services import solver_service

    real = solver_service._process_uc
    seen = []

    def check(n, cfg, *, solver_options=None, solve=None):
        seen.append(1)
        return real.unverified_payload(n, "2035: bin solve status warning", 0.0)

    monkeypatch.setattr(real, "binary_check", check)
    task, path, _ = granted(run={**RUN, "profile": REF})
    script = (
        Script()
    )  # never consulted: a turn would fail the run with "Script: exhausted"
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert run["status"] == "needs_user" and run["stage"] == "result_gate"
    assert run["stages"]["result_gate"]["reason"] == "not_evaluated"
    verdicts = {r["rule"]: r for r in run["stages"]["result_gate"]["gate"]["rules"]}
    assert verdicts["nh3.binary_check"]["verdict"] == "not_evaluated"
    assert verdicts["nh3.binary_check"]["facts"][0]["value"] == "check_unverified"
    assert run.get("ledger", []) == [] and script.requests == [] and seen == [1]
    assert "loop" not in run or run["loop"]["rounds"] == 0
