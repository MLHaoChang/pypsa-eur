"""LC-16 slice B: a set of assets chosen by a selector and changed by a relative
operation (OJ-22). The server resolves the set and computes every new value;
after that each member is an ordinary absolute row, so slice A's card, apply
and undo carry it unchanged."""
import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness import edit_card
from services import chat_service, chat_tools, project_registry, undo_service
from services.pypsa_service import PyPSAService
from tests.test_project_changes_multi import dispatch, frame
from tests.test_workflow_tools import helpers_state  # noqa: F401


@pytest.fixture
def grid(client, session_ctx, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    for bus in ("B1", "B2", "B3"):
        n.add("Bus", bus, v_nom=110)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=300)
    n.add("Line", "L2", bus0="B2", bus1="B3", x=0.1, r=0.01, s_nom=200)
    n.add("Line", "L3", bus0="B1", bus1="B3", x=0.1, r=0.01, s_nom=100)
    n.add("Generator", "Solar", bus="B3", p_nom=80, carrier="solar")
    n.add("Generator", "Solar roof", bus="B2", p_nom=10, carrier="solar", p_max_pu=0.9)
    n.add("Generator", "Solar old", bus="B1", p_nom=5, carrier="solar", active=False)
    n.add("Generator", "Gas", bus="B1", p_nom=100, carrier="gas", efficiency=0.9, build_year=2020)
    n.add("Link", "K1", bus0="B1", bus1="B3", bus2="B2", p_nom=50, efficiency=0.9, efficiency2=0.1)
    n.add("StorageUnit", "Battery", bus="B2", p_nom=10, max_hours=2, inv_power_overnight=100000.0,
          inv_power_lifetime=20.0, efficiency_store=0.95)
    n.add("Load", "D1", bus="B3", p_set=5)
    install_network(n)
    response = client.post("/api/projects/Selector Grid")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            path = project_registry.project_dir(project_registry.resolve_project(db, user, "Selector Grid"))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        undo_service.clear()
        yield ctx, session, path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


ALL_SOLAR_P_NOM = {"component_class": "Generator", "select": {"carrier": "solar"}, "attribute": "p_nom",
                   "operation": {"scale": 1.2}}


def p_nom():
    g = PyPSAService.get_network().generators
    return {name: g.at[name, "p_nom"] for name in ("Solar", "Solar roof", "Solar old", "Gas")}


BEFORE = {"Solar": 80, "Solar roof": 10, "Solar old": 5, "Gas": 100}


def refused(changes, root):
    """The refusal's detail, after checking it left nothing behind (B8)."""
    previews = root / "assistant_previews"
    before = sorted(previews.glob("*.json")) if previews.exists() else []
    network = PyPSAService.get_network().generators.copy()
    with pytest.raises(HTTPException) as failure:
        chat_tools.preview_project_changes(changes)
    assert (sorted(previews.glob("*.json")) if previews.exists() else []) == before
    pd.testing.assert_frame_equal(PyPSAService.get_network().generators, network)
    return failure.value.detail


def test_all_solar_plus_20_percent_is_computed_by_the_server(grid):
    # OJ-22: "Increase all solar capacity by 20 %": 80 → 96 MW, computed here.
    ctx, session, _ = grid
    preview = chat_tools.preview_project_changes([ALL_SOLAR_P_NOM])
    assert [(d["name"], d["before"], d["after"]) for d in preview["diff"]] == [
        ("Solar", 80, 96), ("Solar roof", 10, 12), ("Solar old", 5, 6)]
    [selected] = preview["sets"]
    assert selected["size"] == 3 and selected["inactive"] == 1
    assert selected["factor_words"] == "× 1.2 (+20 %)"
    assert "as of this preview" in selected["words"]
    assert p_nom() == BEFORE
    frames = dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]})
    [pending] = [d for e, d in frames if e == "tool_pending_confirmation"]
    card = pending["edit_card"]
    assert card["sets"] == preview["sets"]
    assert card["says"] == ("all Generator with carrier solar, as of this preview · 3 assets, 1 inactive · "
                            "p_nom × 1.2 (+20 %)")
    receipt = frame(frames, "tool_result")["result"]["_edit_receipt"]
    assert receipt["outcome"] == "verified" and receipt["says"] == card["says"]
    assert p_nom() == {**BEFORE, "Solar": 96, "Solar roof": 12, "Solar old": 6}
    chat_tools.undo_last()
    assert p_nom() == BEFORE


def test_halving_and_a_names_form_operation(grid):
    preview = chat_tools.preview_project_changes([
        {"component_class": "Line", "names": ["L1", "L2"], "attribute": "s_nom", "operation": {"scale": 0.5}}])
    assert [(d["name"], d["after"]) for d in preview["diff"]] == [("L1", 150), ("L2", 100)]
    assert preview["sets"][0]["factor_words"] == "× 0.5 (−50 %)"


def test_bus_selector_counts_every_port(grid):
    # B6: a branch is "at bus X" through any of its bus columns, bus2 included.
    lines = chat_tools.preview_project_changes([
        {"component_class": "Line", "select": {"bus": "B3"}, "attribute": "s_nom", "operation": {"scale": 2}}])
    assert [d["name"] for d in lines["diff"]] == ["L2", "L3"]
    assert "at bus B3 (any port)" in lines["sets"][0]["words"]
    links = chat_tools.preview_project_changes([
        {"component_class": "Link", "select": {"bus": "B2"}, "attribute": "p_nom", "operation": {"scale": 2}}])
    assert [d["name"] for d in links["diff"]] == ["K1"]


def test_selectors_and_together_and_all_selects_the_class(grid):
    preview = chat_tools.preview_project_changes([
        {"component_class": "Generator", "select": {"carrier": "solar", "bus": "B3"}, "attribute": "p_nom",
         "operation": {"scale": 1.5}}])
    assert [(d["name"], d["after"]) for d in preview["diff"]] == [("Solar", 120)]
    every = chat_tools.preview_project_changes([
        {"component_class": "Line", "select": {"all": True}, "attribute": "s_nom", "value": 400}])
    assert every["sets"][0]["size"] == 3 and every["sets"][0]["factor_words"] is None


def test_empty_set_is_a_refusal_naming_what_exists(grid):
    detail = refused([{**ALL_SOLAR_P_NOM, "select": {"carrier": "wind"}}], grid[2])
    assert detail["error_kind"] == "change_selection_empty"
    assert "carrier wind" in detail["message"] and "solar" in detail["message"] and "gas" in detail["message"]


def test_a_selector_key_the_class_lacks_lists_the_valid_keys(grid):
    # A Bus has no bus column: refused with the keys it has, not resolved to an empty set.
    detail = refused([{"component_class": "Bus", "select": {"bus": "B1"}, "attribute": "v_nom",
                       "operation": {"scale": 2}}], grid[2])
    assert detail["error_kind"] == "invalid_change_selector"
    assert "carrier, all" in detail["message"]


@pytest.mark.parametrize("change", [
    {"component_class": "Line", "names": ["L1"], "select": {"all": True}, "attribute": "s_nom", "value": 1},
    {"component_class": "Line", "names": ["L1"], "attribute": "s_nom", "value": 1, "operation": {"scale": 2}},
    {"component_class": "Line", "attribute": "s_nom", "value": 1},
    {"component_class": "Line", "names": ["L1"], "attribute": "s_nom"},
    {"component_class": "Line", "names": ["L1"], "attribute": "s_nom", "operation": {"add": 2}},
    {"component_class": "Line", "select": {"all": False}, "attribute": "s_nom", "value": 1},
    {"component_class": "Line", "select": {}, "attribute": "s_nom", "value": 1},
])
def test_malformed_items_refuse(grid, change):
    assert refused([change], grid[2])["error_kind"] == "invalid_changes"


@pytest.mark.parametrize("factor", [0, -1, 1000.5, float("inf"), float("nan")])
def test_factor_bounds(grid, factor):
    # B1: 0 < f <= 1000; "set to 0" stays an absolute value.
    detail = refused([{**ALL_SOLAR_P_NOM, "operation": {"scale": factor}}], grid[2])
    assert detail["error_kind"] in ("invalid_change_value", "invalid_changes")


def test_an_operation_on_a_flag_refuses(grid):
    detail = refused([{"component_class": "Line", "names": ["L1"], "attribute": "s_nom_extendable",
                       "operation": {"scale": 2}}], grid[2])
    assert detail["error_kind"] == "invalid_edit_value"


def test_an_integer_field_is_never_rounded(grid):
    # B2: 2020 × 1.001 = 2022.02 is not an integer year.
    detail = refused([{"component_class": "Generator", "names": ["Gas"], "attribute": "build_year",
                       "operation": {"scale": 1.001}}], grid[2])
    assert detail["error_kind"] == "invalid_change_value"
    assert "Gas" in detail["message"] and "2022.02" in detail["message"]


def test_an_unset_old_value_cannot_be_scaled(grid):
    PyPSAService.get_network().generators.loc["Solar roof", "marginal_cost"] = float("nan")
    detail = refused([{"component_class": "Generator", "select": {"carrier": "solar"}, "attribute": "marginal_cost",
                       "operation": {"scale": 2}}], grid[2])
    assert detail["error_kind"] == "invalid_change_value"
    assert "Solar roof" in detail["message"]


def test_a_scaled_per_unit_value_above_one_refuses(grid):
    # B4: 0.9 × 1.2 = 1.08 is above the per-unit limit; the member is named.
    detail = refused([{"component_class": "Generator", "names": ["Solar roof"], "attribute": "p_max_pu",
                       "operation": {"scale": 1.2}}], grid[2])
    assert detail["error_kind"] == "invalid_change_value" and "Solar roof" in detail["message"]
    detail = refused([{"component_class": "StorageUnit", "names": ["Battery"], "attribute": "efficiency_store",
                       "operation": {"scale": 1.1}}], grid[2])
    assert detail["error_kind"] == "invalid_change_value" and "Battery" in detail["message"]


def test_a_link_efficiency_may_exceed_one(grid):
    # A heat pump's COP: Link efficiencies are conversion factors, not shares.
    preview = chat_tools.preview_project_changes([
        {"component_class": "Link", "names": ["K1"], "attribute": "efficiency", "operation": {"scale": 3}}])
    assert preview["diff"][0]["after"] == pytest.approx(270)  # the card shows %


def test_a_composite_capital_cost_cannot_be_scaled(grid):
    detail = refused([{"component_class": "StorageUnit", "names": ["Battery"], "attribute": "capital_cost",
                       "operation": {"scale": 1.2}}], grid[2])
    assert detail["error_kind"] == "derived_field"


def test_a_time_series_shadows_the_static_value(grid):
    # B3: a member with its own p_max_pu series ignores the static value at solve time.
    n = PyPSAService.get_network()
    n.generators_t.p_max_pu["Solar"] = pd.Series([0.0, 0.7], index=n.snapshots)
    for change in ({"component_class": "Generator", "select": {"carrier": "solar"}, "attribute": "p_max_pu",
                    "operation": {"scale": 0.5}},
                   {"component_class": "Generator", "names": ["Solar"], "attribute": "p_max_pu", "value": 0.5}):
        detail = refused([change], grid[2])
        assert detail["error_kind"] == "change_series_shadowed"
        assert "'Solar'" in detail["message"] and "Solar roof" not in detail["message"]


def test_resolved_rows_count_against_the_cap(grid):
    detail = refused([{"component_class": "Generator", "select": {"all": True}, "attribute": f,
                       "operation": {"scale": 1.1}} for f in ("p_nom", "marginal_cost", "p_nom_max",
                                                              "p_nom_min", "capital_cost", "lifetime")], grid[2])
    assert detail["error_kind"] in ("invalid_preview", "derived_field")
    detail = refused([{"component_class": "Generator", "select": {"all": True}, "attribute": f, "value": 1.0}
                      for f in ("p_nom", "marginal_cost", "p_nom_max", "p_nom_min", "lifetime", "ramp_limit_up")],
                     grid[2])
    assert detail["error_kind"] == "invalid_preview"
    assert "4 Generator" in detail["message"] or "24" in detail["message"]


def test_the_set_is_frozen_at_preview_time(grid):
    # B5: a solar generator added after the preview makes the apply refuse.
    ctx, session, _ = grid
    preview = chat_tools.preview_project_changes([ALL_SOLAR_P_NOM])
    PyPSAService.get_network().add("Generator", "Solar new", bus="B1", p_nom=1, carrier="solar")
    frames = dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]})
    assert "preview_stale" in str(frames)
    assert PyPSAService.get_network().generators.at["Solar", "p_nom"] == 80


def test_guided_treats_the_selector_form_as_the_names_form(grid):
    # B9b: the Guided single-card exception does not key on the payload's shape.
    ctx, session, _ = grid
    frames = dispatch(session, "preview_project_changes", {"changes": [ALL_SOLAR_P_NOM]}, guided=True)
    assert not [d for e, d in frames if e == "tool_pending_confirmation"]
    preview = frame(frames, "tool_result")["result"]
    assert p_nom() == BEFORE
    frames = dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]}, guided=True)
    [pending] = [d for e, d in frames if e == "tool_pending_confirmation"]
    assert pending["edit_card"]["complete"] is True and pending["edit_card"]["sets"][0]["size"] == 3
    assert p_nom()["Solar"] == 96


def test_the_undo_card_keeps_the_set_line(grid):
    ctx, session, _ = grid
    preview = chat_tools.preview_project_changes([ALL_SOLAR_P_NOM])
    dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]})
    undo = edit_card.preview("undo_last", {})
    assert {(r["name"], r["new"]) for r in undo["rows"] if r["role"] == "typed"} >= {("Solar", 80), ("Solar roof", 10)}
