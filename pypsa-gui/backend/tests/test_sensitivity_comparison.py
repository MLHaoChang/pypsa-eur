"""LC-18: one read for a base and its sensitivity cases (OJ-29, OJ-30).

The base (tests.test_workflow_tools.baseline) serves 5 MW for 2 h from G1 at
10 EUR/MWh: opex = 10 MWh x 10 EUR = 100 EUR = 0.0001 M€, and no capex. Each
case re-prices G1 only, so a case at c EUR/MWh has opex = 10 c EUR: the hand
oracle below.
"""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException

from harness import sensitivity_comparison as sc
from services import chat_tools
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401

MWH = 5 * 2


def opex(price):
    return price * MWH / 1e6


def tool(name, **args):
    return chat_tools.DISPATCHERS[name](**args)


def refused(kind, status, name, **args):
    with pytest.raises(HTTPException) as exc:
        tool(name, **args)
    assert exc.value.status_code == status and exc.value.detail["error_kind"] == kind, exc.value.detail
    return exc.value.detail


def solve(project_id):
    job = chat_tools.solve_queue_enqueue(project_id)
    job_id = job.get("id") or job.get("job_id")
    terminal = chat_tools.wait_for_job(job_id, 25)
    assert terminal["terminal"] and terminal["completed"], chat_tools.solve_queue_job_log(job_id, 40)["lines"][-4:]


def sweep(project_id, *prices, attribute="marginal_cost", prefix="Gas"):
    cases = [{"new_name": f"{prefix} {p}", "changes": [{"component_class": "Generator", "names": ["G1"],
                                                         "attribute": attribute, "value": p}]}
             for p in prices]
    out = chat_tools.run_sensitivity_sweep(project_id, cases)
    for case in out["cases"]:
        terminal = chat_tools.wait_for_job(case["job"]["id"], 25)
        assert terminal["terminal"] and terminal["completed"], terminal
    return {f"{prefix} {p}": case["project_id"] for p, case in zip(prices, out["cases"])}


def edit_saved(project_id, change):
    """Change a saved project on disk, as an edit and save in the app would."""
    from routers import projects
    from services import project_registry, workflow_tools as wt
    from services.pypsa_service import PyPSAService
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, project_id)
        # The open project is solved from memory: edit it there, then save.
        active = PyPSAService.get_active_context()
        ctx = active if str(active.project_uuid) == str(project.id) else wt._load_context(project)
        change(ctx)
        projects._save_context(ctx, project.name, storage_dir=Path(ctx.storage_dir), db=db, user=user,
                               project_row=project)


def set_price(price):
    def change(ctx):
        ctx.network.generators.loc["G1", "marginal_cost"] = price
    return change


@pytest.fixture
def swept(baseline):
    _ctx, project_id, _root = baseline
    solve(project_id)
    return project_id, sweep(project_id, 5, 20, 30)


def by_name(report):
    return {c["name"]: c for c in report["cases"]}


def test_oj29_one_read_gives_every_case_its_hand_computed_deltas(swept):
    base_id, ids = swept
    report = tool("compare_sensitivities", baseline_project_id=base_id)
    assert report["status"] == "completed"
    assert report["base"]["name"] == "Tool Baseline"
    assert report["base"]["values"]["opex_meur"]["value"] == pytest.approx(opex(10))
    cases = by_name(report)
    assert set(cases) == set(ids)
    for price in (5, 20, 30):
        cell = cases[f"Gas {price}"]["values"]["opex_meur"]
        assert cell["value"] == pytest.approx(opex(price))
        assert cell["delta"] == pytest.approx(opex(price) - opex(10))
        assert cell["delta_pct"] == pytest.approx((price - 10) / 10 * 100)
        assert cases[f"Gas {price}"]["project_id"] == ids[f"Gas {price}"]
        assert any("10 → " + str(price) in row for row in cases[f"Gas {price}"]["declared_changes"])
    # UX: signed with + and U+2212, money in the unit that reads the row (here €);
    # the table cell carries no unit, its header does. Labels are the Compare view's.
    assert cases["Gas 20"]["values"]["opex_meur"]["cell"] == "200 · +100 · +100 %"
    assert cases["Gas 5"]["values"]["opex_meur"]["cell"] == "50 · −50 · −50 %"
    assert report["base"]["values"]["opex_meur"]["cell"] == "100"
    assert {m["metric"]: m["header"] for m in report["metrics"]}["opex_meur"] == "Operational expenditure (€)"
    assert {m["metric"]: m["label"] for m in report["metrics"]}["opex_meur"] == "Operational expenditure"
    # A metric this network does not have is unavailable in words, never a zero.
    loading = cases["Gas 20"]["values"]["peak_line_loading_pct"]
    assert loading == {"value": None, "unavailable": "Gas 20: no line loading saved for this run"}
    # GK (a): total cost is the LP's own basis, with the horizon named.
    total = cases["Gas 20"]["values"]["total_cost_meur"]
    assert total["value"] == pytest.approx(opex(20)) and total["basis_note"] == "CAPEX and OPEX both cover the modelled 2 h, not a year"
    assert "rank" not in report and "show" not in report and "why" not in report


def test_oj30_rank_by_gives_a_qualified_preference_and_its_why(swept):
    base_id, ids = swept
    report = tool("compare_sensitivities", baseline_project_id=base_id, rank_by="total_cost_meur")
    rank = report["rank"]
    assert [r["name"] for r in rank["ranking"]] == ["Gas 5", "Tool Baseline", "Gas 20", "Gas 30"]
    assert rank["tie_tolerance"] == pytest.approx(0.001 * opex(10))
    preferred = rank["preference"]
    assert preferred["name"] == "Gas 5" and preferred["runner_up"] == "Tool Baseline"
    assert preferred["margin"] == pytest.approx(opex(10) - opex(5))
    assert preferred["qualifier"].startswith("Lowest Total annuitised CAPEX + Operational expenditure among the 4")
    facts = report["why"]["facts"]
    assert report["why"]["case"] == "Gas 5" and any("10 → 5" in f for f in facts)
    assert facts[-1] == ("Total annuitised CAPEX + Operational expenditure: −50 € (−50 %) "
                         "against Tool Baseline")
    assert report["show"]["args"] == {"panel_id": "compare", "compare_a": "Tool Baseline",
                                      "compare_b": "Gas 5", "compare_tab": "economics"}
    assert report["show"]["offer"] == "Open Compare for Tool Baseline vs Gas 5 on Economics?"
    high = tool("compare_sensitivities", baseline_project_id=base_id, rank_by="opex_meur", rank_order="highest_first")
    assert high["rank"]["preference"]["name"] == "Gas 30" and high["show"]["args"]["compare_tab"] == "dispatch"
    picked = tool("compare_sensitivities", baseline_project_id=base_id, metrics=["peak_line_loading_pct"],
                  compare_case=ids["Gas 20"])
    assert picked["show"]["offer"] == "Open Compare for Tool Baseline vs Gas 20 on Line loading?"
    assert [m["metric"] for m in picked["metrics"]] == ["peak_line_loading_pct"]


def test_a_base_preference_moves_nothing(baseline):
    _ctx, base_id, _root = baseline
    solve(base_id)
    sweep(base_id, 20, 30)
    report = tool("compare_sensitivities", baseline_project_id=base_id, rank_by="opex_meur")
    assert report["rank"]["preference"]["is_base"] and "why" not in report and "show" not in report


@pytest.mark.parametrize("price, tie", [(10.005, True), (10.02, False)])
def test_a_lead_inside_the_tie_tolerance_is_no_defensible_preference(baseline, price, tie):
    _ctx, base_id, _root = baseline
    solve(base_id)
    sweep(base_id, price)
    rank = tool("compare_sensitivities", baseline_project_id=base_id, rank_by="opex_meur")["rank"]
    margin = opex(price) - opex(10)
    assert (margin < rank["tie_tolerance"]) is tie
    if tie:
        assert rank["preference"] is None
        assert "within the tie tolerance of 0.1 € (0.1 % of the base)" in rank["no_defensible_preference"]
    else:
        assert rank["preference"]["name"] == "Tool Baseline"


def test_ld19_zero_baseline_has_deltas_but_no_percentage(baseline):
    """The base has no capital cost; a case of 1000 EUR/MW/yr on G1's 20 MW has
    0.02 M€ a year, pro-rated by the engine to the modelled 2 h of 8760."""
    _ctx, base_id, _root = baseline
    solve(base_id)
    sweep(base_id, 1000, attribute="capital_cost", prefix="Capex")
    report = tool("compare_sensitivities", baseline_project_id=base_id, rank_by="capex_meur_total")
    cell = report["cases"][0]["values"]["capex_meur_total"]
    assert report["base"]["values"]["capex_meur_total"]["value"] == 0
    capex = 20 * 1000 / 1e6 * 2 / 8760
    assert cell["value"] == pytest.approx(capex) and cell["delta"] == pytest.approx(capex)
    assert cell["delta_pct"] is None and cell["delta_pct_note"] == "zero base: no percentage"
    assert cell["cell"] == "4.566 · +4.566"
    assert sc._delta_display(sc._scale("capex_meur_total", [0, capex]), cell) == "+4.566 €"
    assert report["rank"]["tie_tolerance"] == sc.METRICS["capex_meur_total"][3]
    assert report["rank"]["tie_tolerance_display"].endswith("(absolute: the base is zero)")
    assert report["rank"]["preference"]["name"] == "Tool Baseline"


def test_ld19_partial_failure_lists_the_unsolved_case_with_no_deltas(swept):
    from services.dispatch_status import clear_dispatch
    base_id, ids = swept
    edit_saved(ids["Gas 20"], lambda ctx: clear_dispatch(ctx.network))
    report = tool("compare_sensitivities", baseline_project_id=base_id, rank_by="opex_meur")
    assert report["status"] == "partial"
    case = by_name(report)["Gas 20"]
    assert case["reasons"] == ["case_unsolved"] and "values" not in case
    assert case["not_comparable"] == ["Gas 20: not solved yet"]
    assert "Gas 20" not in [r["name"] for r in report["rank"]["ranking"]]
    assert by_name(report)["Gas 30"]["values"]["opex_meur"]["delta"] == pytest.approx(opex(30) - opex(10))


def test_a_case_edited_after_its_solve_is_out_of_date_and_names_finance(swept):
    base_id, ids = swept
    edit_saved(ids["Gas 30"], set_price(31))
    case = by_name(tool("compare_sensitivities", baseline_project_id=base_id))["Gas 30"]
    assert case["reasons"] == ["case_stale", "case_inputs_changed"] and "values" not in case
    # UX: one line per case, both reasons in one plain clause.
    assert case["not_comparable"] == ["Gas 30: its inputs or finance settings changed since its last solve "
                                      "and since it was branched from the base, so its results are out of date"]


def test_a_base_changed_since_branching_compares_nothing(swept):
    base_id, _ids = swept
    edit_saved(base_id, lambda ctx: ctx.network.loads.__setitem__("p_set", 6.0))
    solve(base_id)
    report = tool("compare_sensitivities", baseline_project_id=base_id)
    assert report["status"] == "unavailable"
    assert all(c["reasons"] == ["baseline_changed"] for c in report["cases"])


def test_ld19_another_horizon_is_incomparable(swept):
    base_id, ids = swept
    def weights(ctx):
        ctx.network.snapshot_weightings.loc[:, "objective"] = 2.0
    edit_saved(ids["Gas 20"], weights)     # a queued solve re-derives the weights, so none here
    case = by_name(tool("compare_sensitivities", baseline_project_id=base_id))["Gas 20"]
    assert "horizon_differs" in case["reasons"] and "values" not in case
    [line] = case["not_comparable"]
    assert line.startswith("Gas 20: ") and "a different time horizon or snapshot weighting from the base" in line


@pytest.mark.parametrize("field, value, reason", [("solver_options", {"threads": 1}, "solver_settings_differ"),
                                                  ("mode", "ac_pf", "mode_differs")])
def test_ld19_other_solver_settings_or_mode_are_incomparable(swept, field, value, reason):
    base_id, ids = swept
    def config(ctx):
        ctx.solver_state["solver_config"] = replace(ctx.solver_state["solver_config"], **{field: value})
    edit_saved(ids["Gas 20"], config)
    case = by_name(tool("compare_sensitivities", baseline_project_id=base_id))["Gas 20"]
    assert reason in case["reasons"] and "values" not in case
    assert ("solver_settings_differ" in case["reasons"]) is (reason == "solver_settings_differ")


def test_an_unsolved_base_compares_nothing(baseline):
    _ctx, base_id, _root = baseline
    sweep(base_id, 20)
    report = tool("compare_sensitivities", baseline_project_id=base_id, rank_by="opex_meur")
    assert report["status"] == "unavailable"
    assert report["base"]["unavailable"] == ["Tool Baseline: the base is not solved yet"]
    assert report["base"]["values"]["opex_meur"]["value"] is None
    case = report["cases"][0]
    assert case["reasons"][0] == "baseline_unavailable" and "values" not in case
    assert report["rank"]["preference"] is None and "show" not in report


def test_a_project_that_is_not_a_case_of_the_base_is_refused(swept, client):
    base_id, ids = swept
    assert client.post("/api/projects/Tool Other").status_code == 200
    detail = refused("not_a_sensitivity_case", 422, "compare_sensitivities", baseline_project_id=base_id,
                     case_project_ids=[ids["Gas 20"], "Tool Other"])
    assert "Tool Other" in detail["message"]
    # A case of another base is not a case of this one either.
    refused("not_a_sensitivity_case", 422, "compare_sensitivities", baseline_project_id=ids["Gas 5"],
            case_project_ids=[ids["Gas 20"]])


def test_a_project_the_user_cannot_open_reads_as_not_found(swept, second_identity):
    base_id, ids = swept
    unknown = refused("sensitivity_project_not_found", 404, "compare_sensitivities", baseline_project_id=base_id,
                      case_project_ids=["00000000-0000-0000-0000-000000000000"])
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(second_identity["user_id"])
    try:
        foreign = refused("sensitivity_project_not_found", 404, "compare_sensitivities", baseline_project_id=base_id,
                          case_project_ids=[ids["Gas 20"]])
    finally:
        chat_tools.set_acting_user(previous)
    assert foreign == unknown


def test_more_than_eight_cases_are_refused_before_any_read(monkeypatch):
    monkeypatch.setattr(sc, "_projects", lambda *a: pytest.fail("read a project"))
    refused("invalid_sensitivity_comparison", 422, "compare_sensitivities", baseline_project_id="Base",
            case_project_ids=[f"case-{i}" for i in range(9)])


def test_a_base_with_more_cases_than_the_cap_asks_for_names(swept, monkeypatch):
    base_id, _ids = swept
    monkeypatch.setattr(sc, "MAX_CASES", 2)
    detail = refused("invalid_sensitivity_comparison", 422, "compare_sensitivities", baseline_project_id=base_id)
    assert "has 3 cases" in detail["message"]


def test_export_writes_the_inspected_report_and_refuses_a_changed_one(swept):
    base_id, ids = swept
    report = tool("compare_sensitivities", baseline_project_id=base_id, rank_by="opex_meur")
    out = tool("export_sensitivity_comparison", baseline_project_id=base_id, rank_by="opex_meur",
               comparison_sha256=report["comparison_sha256"])
    assert out["comparison_sha256"] == report["comparison_sha256"] and out["status"] == "completed"
    assert out["filename"].startswith("sensitivity_comparison_")
    edit_saved(ids["Gas 30"], set_price(31))
    refused("sensitivity_comparison_changed", 409, "export_sensitivity_comparison", baseline_project_id=base_id,
            rank_by="opex_meur", comparison_sha256=report["comparison_sha256"])


@pytest.mark.parametrize("name", ["compare_sensitivities", "export_sensitivity_comparison"])
def test_lct19_a_checked_turn_withholds_numbers_these_tools_did_not_check(swept, checked, name):
    from tests.assurance_acceptance.support import UNSAFE, public_text, text_turn, tool_turn
    base_id, _ids = swept
    args = {"baseline_project_id": base_id}
    if name.startswith("export"):
        args["comparison_sha256"] = tool("compare_sensitivities", **args)["comparison_sha256"]
    frames = checked.run([tool_turn(name, args), text_turn(UNSAFE), text_turn(UNSAFE)])
    assert any(event == "tool_error" and p.get("tool_name") == name for event, p in frames)
    assert UNSAFE not in public_text(frames)


def test_compare_scenarios_focuses_the_loading_tab(swept):
    base_id, ids = swept
    out = chat_tools.compare_scenarios(base_id, ids["Gas 20"], focus="loading", open_compare_rail=True)
    assert out["focus"] == "loading" and out["compare_tab"] == "loading"
    assert set(out["focus_section"]) == {"a", "b"} and "lines" in out["focus_section"]["a"]


def test_labels_are_the_compare_views_own_words():
    from harness import catalogue
    tsx = (Path(__file__).parents[2] / "frontend/src/pages/CompareView.tsx").read_text()
    tabs = dict(re.findall(r"\{ id: '(\w+)',\s+label: '([^']+)'", tsx))
    assert tabs == sc.TAB_LABELS
    for name, (label, unit, tab, _tol) in sc.METRICS.items():
        assert tab in tabs
        parts = label.split(" + ") if name == "total_cost_meur" else [label]
        assert all(part in tsx for part in parts), label
    assert catalogue.SENSITIVITY_METRICS == list(sc.METRICS)
    assert "loading" in catalogue.COMPARE_FOCUS_ENUM


def test_the_report_fits_its_packet_at_the_maximum(monkeypatch):
    """GK 8: 8 cases x every metric, each with 12+ declared rows of long names, under 24 KB."""
    import pypsa
    from types import SimpleNamespace as NS
    names = [f"Generator-with-a-deliberately-long-engineering-name-{i:02d}" for i in range(14)]
    n = pypsa.Network()
    n.add("Bus", "B1")
    for name in names:
        n.add("Generator", name, bus="B1", marginal_cost=10)
    spec = {"baseline_id": "b", "input_fingerprint": "f", "baseline_fingerprint": "f",
            "case": {"changes": [{"component_class": "Generator", "names": names,
                                  "attribute": "marginal_cost", "value": 12.3456789}]}}
    base = NS(name="A base project with a long name", id="b")
    cases = [(NS(name=f"Sensitivity case number {i} with a long name", id=f"c{i}"), spec) for i in range(8)]
    monkeypatch.setattr(sc, "_projects", lambda *a: (base, cases))
    lines = [{"peak_loading": {"total": 0.5}}]
    summary = {"has_solve": True, "is_multi_period": False,
               "capacity": {"capacity_mw_by_carrier": {"gas": {"total": 1234.5}},
                            "capex_meur_by_carrier": {"gas": {"total": 123.4}}},
               "dispatch": {"dispatch_gwh_by_carrier": {"gas": {"total": 9876.5}}, "opex_meur": {"total": 321.9},
                            "total_load_gwh": {"total": 9876.5}},
               **{k: {"available": True, f: {"total": 12.3}} for k, f in
                  (("emissions", "total_kt"), ("curtailment", "total_gwh"), ("lost_load", "total_mwh"))},
               "loading": {"available": True, "lines": lines}}
    read = {"summary": summary, "ctx": NS(network=n), "fingerprint": "f", "fresh": True, "mode": ("lopf", False),
            "basis": {"snapshots_sha256": "s", "weights_sha256": "w", "solver_config_sha256": "c"}, "hours": 8760.0}
    monkeypatch.setattr(sc, "_read", lambda project: read)
    report = sc.compare_sensitivities("b", rank_by="opex_meur")
    assert len(report["cases"]) == 8 and all(len(c["declared_changes"]) == 12 for c in report["cases"])
    assert all(c["declared_changes_truncated"] for c in report["cases"])
    assert len(json.dumps(report, ensure_ascii=False).encode()) < 24 * 1024


@pytest.mark.parametrize("query, name", [
    ("Compare the sensitivities against the base", "compare_sensitivities"),
    ("Which sensitivity case is cheapest and why?", "compare_sensitivities"),
    ("Compare scenario A with scenario B side by side", "compare_scenarios"),
    ("Compare the two experiment receipts", "compare_experiments"),
])
def test_the_bounded_catalogue_offers_each_comparison_for_its_own_query(query, name):
    """GK 4: past the provider's 128-tool cap, the new tool neither hides nor crowds out."""
    from harness.catalogue import TOOLS
    from harness.providers import wiring
    selected = [t["name"] for t in wiring._bounded_tools(TOOLS, query)]
    assert len(TOOLS) > wiring._OPENAI_TOOL_LIMIT >= len(selected)
    assert name in selected


def test_cases_with_an_uploaded_profile_read_as_current(baseline):
    """A queued solve records its inputs with the project's uploaded series
    (user_ts), as every later read hydrates them; else every such case would
    read as out of date."""
    import pandas as pd
    _ctx, base_id, _root = baseline
    def profile(ctx):
        series = pd.Series([1.0, 0.8], index=ctx.network.snapshots, name="G1")   # as the upload stores it
        ctx.network.generators_t.p_max_pu["G1"] = series
        ctx.user_ts[("generators", "p_max_pu", "G1")] = series
    edit_saved(base_id, profile)
    solve(base_id)
    sweep(base_id, 20)
    report = tool("compare_sensitivities", baseline_project_id=base_id)
    assert report["status"] == "completed", report["cases"][0].get("not_comparable")


@pytest.mark.parametrize("values, unit", [([0.02264, 0.1132], "k€"), ([1.5, 0.2], "M€"), ([0.0001], "€"),
                                          ([None, 0.0], "€")])
def test_money_rows_read_in_the_unit_that_makes_their_largest_value_at_least_one(values, unit):
    assert sc._scale("total_cost_meur", values)[0] == unit
    assert sc._scale("emissions_kt", [0.0001]) == ("kt", 1.0)


def test_a_k_euro_row_formats_cells_and_deltas_like_the_cost_card():
    cell = sc._cell(sc._scale("total_cost_meur", [0.1458, 0.1232]), 0.1458, 0.1232)
    assert sc._delta_display(sc._scale("total_cost_meur", [0.1458]), cell) == "−22.6 k€ (−15.5 %)"
    assert cell["cell"] == "123.2 · −22.6 · −15.5 %"


def test_other_reasons_join_in_one_line():
    assert sc._not_comparable("Lines 160", ["case_unsolved", "mode_differs"]) == (
        "Lines 160: not solved yet; solved in a different mode from the base")


def test_multi_period_total_cost_is_unavailable_never_mixed(monkeypatch):
    """GK B: annuitised CAPEX and undiscounted OPEX of a multi-period run do not add."""
    head = {"has_solve": True, "is_multi_period": True, "opex_meur": 2.0, "capex_meur_total": 3.0,
            "capacity_mw_total": 10.0, "dispatch_gwh_total": 1.0}
    monkeypatch.setattr(chat_tools, "_scenario_headlines", lambda summary: head)
    values = sc._values({"summary": {"has_solve": True}, "hours": sc.HOURS_PER_YEAR})
    assert values["total_cost_meur"] == (None, "multi-period: annuitised CAPEX and undiscounted OPEX do not add", None)
    assert values["opex_meur"][0] == 2.0 and values["capex_meur_total"][0] == 3.0


def test_an_experiment_case_project_is_no_sensitivity_case_and_not_counted(swept, monkeypatch):
    from services import project_registry
    base_id, ids = swept
    experiment_name = "exp-" + "0" * 32 + "-Gas"
    with chat_tools._acting() as (db, user):
        child = project_registry.resolve_project(db, user, ids["Gas 30"])
        child.name = experiment_name
        db.commit()
    monkeypatch.setattr(sc, "MAX_CASES", 2)  # two real cases fit; a counted third would be refused
    report = tool("compare_sensitivities", baseline_project_id=base_id)
    assert sorted(by_name(report)) == ["Gas 20", "Gas 5"]
