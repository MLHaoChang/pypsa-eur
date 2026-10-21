"""S3b: a checked loading answer joined to the Load flow displayed-data ACK."""

# ruff: noqa: F811 -- imported shared fixtures.
import pytest

from harness import assurance, navigation, navigation_join
from harness.session import ChatSession
from services import assurance_records
from tests.test_branch_loading_assurance import grid, solve  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "branch-loading/1")


def evidence(ctx, **window):
    scope = {"version": 1, "profile": "branch-loading/1", "project_id": ctx.project_uuid, "entity_id": "L12",
             "component_class": "Line", "retain": False, **window}
    return scope, assurance_records.capture_scoped(assurance_records.actor_id(), scope)


def opened(client, ctx, acknowledge=True):
    owner = assurance_records.actor_id()
    turn = assurance.Turn(ChatSession(owner_user_id=owner), ctx)
    navigation.begin_turn(turn, 3)
    action = navigation.issue(turn, {"kind": "navigate", "panel_id": "results", "results_tab": "loadflow"})
    assert action["version"] == 2
    if acknowledge:
        body = {k: action[k] for k in ("version", "action_id", "turn_id", "binding_generation", "target",
                                       "source_binding")}
        assert client.post("/api/chat/ui-ack", json=body).status_code == 200
    return assurance_records.load(owner, action["action_id"], "ui_action")


def test_the_tab_showing_the_checked_solve_and_window_joins(client, grid):
    ctx = solve(grid)
    scope, record = evidence(ctx)
    joined = navigation_join.join(ctx, opened(client, ctx), scope, record)
    assert joined == {"shows": "checked_evidence", "window": {"start": record["scope"]["start"],
                                                               "end": record["scope"]["end"]}}


def test_an_unconfirmed_display_is_opened_only(client, grid):
    ctx = solve(grid)
    scope, record = evidence(ctx)
    assert navigation_join.join(ctx, opened(client, ctx, acknowledge=False), scope, record) == {
        "shows": "not_confirmed"}


def test_a_different_window_is_named(client, grid):
    ctx = solve(grid)
    first, second = (v.isoformat() for v in ctx.network.snapshots[:2])
    scope, record = evidence(ctx, start=first, end=second)
    assert navigation_join.join(ctx, opened(client, ctx), scope, record) == {
        "shows": "different_view", "reason": "window_differs"}


def test_inputs_changed_after_the_display_are_named(client, grid):
    ctx = solve(grid)
    scope, record = evidence(ctx)
    action = opened(client, ctx)
    ctx.network.loads.at["L1", "p_set"] = 1
    assert navigation_join.join(ctx, action, scope, record)["reason"] == "inputs_changed"


def test_an_ac_power_flow_refuses_the_join(client, grid):
    ctx = solve(grid)
    scope, record = evidence(ctx)
    action = opened(client, ctx)
    ctx.solver_state["ac_pf_results"] = {"converged": True}
    assert navigation_join.join(ctx, action, scope, record)["reason"] == "source_not_lopf"


def test_another_profile_or_tab_is_not_joined(client, grid):
    ctx = solve(grid)
    scope, record = evidence(ctx)
    action = opened(client, ctx)
    assert navigation_join.join(ctx, {**action, "target": {"panel_id": "results", "results_tab": "prices"}},
                                scope, record) is None
    assert navigation_join.join(ctx, action, {**scope, "profile": "bus-price/1"}, record) is None
