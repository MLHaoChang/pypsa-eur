"""branch-loading/1 and network-loading/1 through the checked stream (checked-results S3).

Expected values are worked by hand from the fixture and cross-checked against
`get_loadflow_summary(source="lopf")`, the read behind the Load flow tab.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pandas as pd
import pytest

from harness import assurance_scope, branch_calculations
from services import workflow_tools
from services.loadflow_summary import get_loadflow_summary
from tests.assurance_acceptance.support import approval, public_text
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

BRANCH, NETWORK = "branch-loading/1", "network-loading/1"


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", f"{BRANCH},{NETWORK}")


@pytest.fixture
def grid(baseline):
    """Cheap G1 at B1 feeds L2 at B2 over one line; dear G2 at B2 covers the rest."""
    ctx = baseline[0]
    n = ctx.network
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.loads_t.p_set = pd.DataFrame({"L1": [0, 0, 0]}, index=n.snapshots)
    n.add("Bus", "B2", v_nom=110)
    n.add("Load", "L2", bus="B2", p_set=[4, 9, 6])
    n.add("Generator", "G2", bus="B2", p_nom=20, marginal_cost=100)
    n.add("Line", "L12", bus0="B1", bus1="B2", s_nom=8, x=0.1, r=0.01)
    n.lines_t.s_max_pu = pd.DataFrame({"L12": [1, 1, 0.5]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2, 1, 3]
    return ctx


def solve(ctx):
    n = ctx.network
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    # Limits 8, 8, 4 MW: the line carries 4, 8, 4 MW; G2 covers 0, 1, 2 MW.
    assert list(n.lines_t.p0.L12) == pytest.approx([4, 8, 4])
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


# u = 4/8, 8/8, 4/4: peak 100 %, never above 1 + 1e-6, peak flow 8 MW, rating 8 MVA.
LINE = {"peak_branch_utilisation": 100, "hours_above_rating": 0,
        "peak_branch_flow": 8, "branch_rating": 8}


def overload(ctx):
    """An observed 10 MW in the first snapshot: u = 10/8 = 1.25 at weight 2."""
    lines = ctx.network.lines_t
    lines.p0.iloc[0, 0], lines.p1.iloc[0, 0] = 10, -10
    return {**LINE, "peak_branch_utilisation": 125, "hours_above_rating": 2, "peak_branch_flow": 10}


def solve_named(ctx, name):
    n = ctx.network
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    assert list(n.lines_t.p0[name]) == pytest.approx([4, 8, 4])
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


def run_scope(checked, ctx, checked_scope, values):
    frames = checked.run(NumericalProvider(values), message="Check loading.", checked_scope=checked_scope)
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return frames, response.json()


def scope(ctx, entity, profile=BRANCH, **options):
    return {"version": 1, "profile": profile, "project_id": ctx.project_uuid,
            "entity_id": entity, "retain": False, **options}


def run(checked, ctx, entity, values, profile=BRANCH):
    frames = checked.run(NumericalProvider(values), message="Check loading.",
                         checked_scope=scope(ctx, entity, profile))
    record = approval(frames)
    response = checked.client.get("/api/chat/answers/" + record["answer_id"])
    assert response.status_code == 200, response.text
    return frames, response.json()


def values(answer):
    return {b["metric"]: b["value"] for b in answer["blocks"]}


def test_line_loading_matches_the_hand_values_and_the_load_flow_tab(grid, checked):
    ctx = solve(grid)
    frames, answer = run(checked, ctx, "L12", LINE)
    assert answer["receipt"]["request_complete"]
    assert values(answer) == pytest.approx(LINE)
    row = next(r for r in get_loadflow_summary(source="lopf")["top"] if r["name"] == "L12")
    assert row["util_pct"] == pytest.approx(LINE["peak_branch_utilisation"])
    assert row["h_over_limit"] == pytest.approx(LINE["hours_above_rating"])
    text = public_text(frames)
    assert "Branch L12" in text and "Active power (P only)" in text
    assert "no N-1 and no voltage limits" in text
    assert "any missing flow or zero limit in the window is unavailable" in text
    assert "1 Lines and 0 Transformers; 0 Links excluded." in text


def test_an_observed_overload_counts_weighted_hours_like_the_tab(grid, checked):
    ctx = solve(grid)
    expected = overload(ctx)
    _, answer = run(checked, ctx, "L12", expected)
    assert values(answer) == pytest.approx(expected)
    row = next(r for r in get_loadflow_summary(source="lopf")["top"] if r["name"] == "L12")
    assert row["util_pct"] == pytest.approx(125)
    assert row["h_over_limit"] == pytest.approx(2)


def test_network_counts_branches_above_rating_as_an_integer(grid, checked):
    ctx = solve(grid)
    _, answer = run(checked, ctx, "network",
                    {"max_branch_utilisation": 100, "branches_above_rating": 0}, NETWORK)
    assert values(answer) == {"max_branch_utilisation": pytest.approx(100), "branches_above_rating": 0}
    overload(ctx)
    _, answer = run(checked, ctx, "network",
                    {"max_branch_utilisation": 125, "branches_above_rating": 1}, NETWORK)
    assert values(answer) == {"max_branch_utilisation": pytest.approx(125), "branches_above_rating": 1}


def test_q_left_by_an_earlier_power_flow_is_never_mixed_into_lopf_flow(grid, checked):
    ctx = solve(grid)
    ctx.network.lines_t.q0 = pd.DataFrame({"L12": [50, 50, 50]}, index=ctx.network.snapshots)
    _, answer = run(checked, ctx, "L12", LINE)
    assert values(answer) == pytest.approx(LINE)


def test_links_are_excluded_and_counted(grid, checked):
    grid.network.add("Link", "DC", bus0="B1", bus1="B2", p_nom=0)
    frames, _ = run(checked, solve(grid), "L12", LINE)
    assert "1 Lines and 0 Transformers; 1 Links excluded." in public_text(frames)


def test_a_missing_terminal_flow_makes_the_branch_unavailable(grid, checked):
    ctx = solve(grid)
    ctx.network.lines_t.p1.iloc[1, 0] = float("nan")
    _, answer = run(checked, ctx, "L12", {})
    assert not answer["receipt"]["request_complete"]
    assert {s["reason"] for s in answer["receipt"]["result_slots"]} == {"invalid_or_missing_branch_data"}


@pytest.mark.parametrize("case,reason", [
    ("ac_pf", "ac_pf_present"),
    ("ambiguous", "ambiguous_branch"),
    ("unknown", "unknown_branch"),
    ("too_many", "unsupported_branch_population"),
])
def test_unsupported_requests_are_refused_by_name(grid, checked, monkeypatch, case, reason):
    entity, profile = "L12", BRANCH
    if case == "ambiguous":
        grid.network.add("Bus", "B3", v_nom=20)
        grid.network.add("Transformer", "L12", bus0="B2", bus1="B3", s_nom=5, x=0.1)
    ctx = solve(grid)
    if case == "ac_pf":
        ctx.solver_state["ac_pf_results"] = {}
    elif case == "unknown":
        entity = "Ghost"
    elif case == "too_many":
        monkeypatch.setattr(branch_calculations, "MAX_BRANCHES", 0)
        entity, profile = "network", NETWORK
    frames = checked.run(NumericalProvider({}), message="Check loading.",
                         checked_scope=scope(ctx, entity, profile))
    receipt = approval(frames).get("receipt") or {}
    assert receipt.get("request_complete") is False
    assert {s["reason"] for s in receipt["result_slots"]} == {reason}
    ctx.solver_state.pop("ac_pf_results", None)


def test_a_named_class_must_match_the_branch(grid, checked):
    ctx = solve(grid)
    frames = checked.run(NumericalProvider({}), message="Check loading.",
                         checked_scope=scope(ctx, "L12", component_class="Transformer"))
    receipt = approval(frames).get("receipt") or {}
    assert {s["reason"] for s in receipt["result_slots"]} == {"unknown_branch"}


def test_a_missing_time_varying_limit_falls_back_to_the_static_one_like_the_tab(grid):
    n = grid.network
    n.lines.at["L12", "s_max_pu"] = 0.9
    n.lines_t.s_max_pu.iloc[0, 0] = float("nan")
    # loadflow_summary._smax_matrix fills a missing time-varying value from the static one.
    assert branch_calculations._s_max_pu(n.components["Line"], "L12", n.snapshots) == [0.9, 1, 0.5]


def test_a_branch_named_network_needs_its_class(grid, checked):
    n = grid.network
    n.lines.rename(index={"L12": "network"}, inplace=True)
    n.lines_t.s_max_pu.columns = ["network"]
    ctx = solve_named(grid, "network")
    frames = checked.run(NumericalProvider({"max_branch_utilisation": 100, "branches_above_rating": 0}),
                         message="Check loading.", checked_scope=scope(ctx, "network", NETWORK))
    assert "A Line named network exists; name its class to check that branch." in public_text(frames)
    _, answer = run_scope(checked, ctx, scope(ctx, "network", component_class="Line"), LINE)
    assert values(answer) == pytest.approx(LINE)


def test_scope_and_grammar():
    line = assurance_scope.from_message("Show the loading for line L12", "p", {})
    assert line["profile"] == BRANCH and line["component_class"] == "Line"
    assert assurance_scope.from_message("Show the loading for transformer T1", "p", {})[
        "component_class"] == "Transformer"
    with pytest.raises(ValueError, match="component_class_requires_branch_scope"):
        assurance_scope.validate({"version": 1, "profile": NETWORK, "project_id": "p",
                                  "entity_id": "network", "component_class": "Line"})
    network = assurance_scope.from_message("Show loading for network", "p", {})
    assert network["profile"] == NETWORK and network["entity_id"] == "network"
    with pytest.raises(ValueError, match="network_scope_required"):
        assurance_scope.validate({"version": 1, "profile": NETWORK, "project_id": "p", "entity_id": "L12"})
    with pytest.raises(ValueError, match="network_scope_required"):
        assurance_scope.validate({"version": 1, "profile": BRANCH, "project_id": "p", "entity_id": "network"})
