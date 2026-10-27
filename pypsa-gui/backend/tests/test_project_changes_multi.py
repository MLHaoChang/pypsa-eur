"""LC-16 slice A: one card for several assets of mixed classes (OJ-21), one bad
member (OJ-23) and one-step undo (OJ-26), on a real project with no paid calls."""
import json
import uuid

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness import edit_card
from services import assistant_tools, change_log_service, chat_service, chat_tools, project_registry, undo_service
from services import workflow_tools as wt
from services.pypsa_service import PyPSAService
from tests.test_workflow_tools import helpers_state  # noqa: F401


@pytest.fixture
def mixed(client, session_ctx, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    n.add("Bus", "B1", v_nom=110)
    n.add("Bus", "B2", v_nom=110)
    n.add("Bus", "B3", v_nom=20)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=300)
    n.add("Line", "L2", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=300)
    n.add("Transformer", "T1", bus0="B2", bus1="B3", x=0.1, s_nom=40)
    n.add("Generator", "G1", bus="B1", p_nom=80, marginal_cost=10, carrier="solar")
    n.add("StorageUnit", "Battery", bus="B2", p_nom=10, max_hours=2, inv_power_overnight=100000.0, inv_power_lifetime=20.0)
    n.add("Load", "D1", bus="B3", p_set=5)
    install_network(n)
    response = client.post("/api/projects/Multi Asset")
    assert response.status_code == 200, response.text
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, "Multi Asset")
            path = project_registry.project_dir(project)
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        undo_service.clear()
        global BEFORE
        BEFORE = values()
        assert BEFORE[:2] == (300, 300) and BEFORE[3] == 40 and BEFORE[2] != 150000
        yield ctx, session, path
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


BEFORE = None  # set by the fixture: the battery's overnight cost starts at the app's default


OJ21 = [
    {"component_class": "Line", "names": ["L1"], "attribute": "s_nom", "value": 500},
    {"component_class": "Line", "names": ["L2"], "attribute": "s_nom", "value": 450},
    {"component_class": "StorageUnit", "names": ["Battery"], "attribute": "inv_power_overnight", "value": 150000},
    {"component_class": "Transformer", "names": ["T1"], "attribute": "s_nom", "value": 63},
]


def values():
    n = PyPSAService.get_network()
    return (n.lines.at["L1", "s_nom"], n.lines.at["L2", "s_nom"], n.storage_units.at["Battery", "inv_power_overnight"],
            n.transformers.at["T1", "s_nom"])


def dispatch(session, name, args, decide=lambda data: "approve", guided=False):
    frames, collector = [], []
    for event, data in chat_service._dispatch_real_tool_call(
            session, {"id": uuid.uuid4().hex, "name": name, "input": args}, collector, guided=guided):
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], decide(data))
    return frames


def frame(frames, kind):
    return next(d for e, d in frames if e == kind)


def refusal(changes):
    with pytest.raises(HTTPException) as failure:
        chat_tools.preview_project_changes(changes)
    return failure.value.detail


def test_mixed_classes_one_card_one_approval_native_readback(mixed):
    ctx, session, _ = mixed
    preview = chat_tools.preview_project_changes(OJ21)
    assert preview["changes_total"] == 4
    assert [(d["name"], d["before"], d["after"]) for d in preview["diff"]] == [
        ("L1", 300, 500), ("L2", 300, 450), ("Battery", BEFORE[2] / 1000, 150), ("T1", 40, 63)]
    # The card's display unit, not the stored one: 150 EUR/kW is 150000 EUR/MW.
    assert [d["unit"] for d in preview["diff"]] == ["MVA", "MVA", "EUR/kW", "MVA"]
    assert values() == BEFORE
    frames = dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]})
    pending = [d for e, d in frames if e == "tool_pending_confirmation"]
    assert len(pending) == 1
    card = pending[0]["edit_card"]
    assert card["kind"] == "parameter_edit" and card["complete"] is True
    typed = [(r["component_class"], r["name"], r["field"], r["old"], r["new"]) for r in card["rows"] if r["role"] == "typed"]
    assert typed == [("Line", "L1", "s_nom", 300, 500), ("Line", "L2", "s_nom", 300, 450),
                     ("StorageUnit", "Battery", "inv_power_overnight", BEFORE[2], 150000),
                     ("Transformer", "T1", "s_nom", 40, 63)]
    # The app annualises the overnight cost at solve time; the card states the stored basis.
    assert next(r["unit"] for r in card["rows"] if r["field"] == "inv_power_overnight") == "EUR/MW"
    assert all(r["unit"] for r in card["rows"] if r["role"] == "typed")
    assert card["binding"]["preview_id"] == preview["preview_id"]
    result = frame(frames, "tool_result")["result"]
    receipt = result["_edit_receipt"]
    assert receipt["outcome"] == "verified"
    assert receipt["undo"]["available"] is True
    assert values() == (500, 450, 150000, 63)


def test_guided_asks_one_approval_and_still_cards_other_writes(mixed):
    # OJ-21: "one card, one approval … in Expert or Guided". The preview stores
    # a record and changes nothing; the apply card is the approval.
    from harness import confirm

    ctx, session, _ = mixed
    assert confirm.GUIDED_UNCARDED == frozenset({"preview_project_changes"})
    frames = dispatch(session, "preview_project_changes", {"changes": OJ21}, guided=True)
    assert not [d for e, d in frames if e == "tool_pending_confirmation"]
    preview = frame(frames, "tool_result")["result"]
    assert values() == BEFORE
    frames = dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]}, guided=True)
    pending = [d for e, d in frames if e == "tool_pending_confirmation"]
    assert len(pending) == 1 and pending[0]["edit_card"]["complete"] is True
    assert values() == (500, 450, 150000, 63)
    # Every other write-tier tool keeps Guided's generic card.
    frames = dispatch(session, "create_chart", {"component": "Line", "attribute": "s_nom", "names": ["L1"]},
                      decide=lambda d: "deny", guided=True)
    assert len([d for e, d in frames if e == "tool_pending_confirmation"]) == 1


def test_denial_leaves_every_value_unchanged(mixed):
    ctx, session, _ = mixed
    preview = chat_tools.preview_project_changes(OJ21)
    dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]}, decide=lambda d: "deny")
    assert values() == BEFORE


def test_a_part_column_the_asset_never_had_is_created_like_a_single_edit(mixed):
    # The OJ grid's battery has no investment parts: the column is absent, as
    # after a NetCDF import. A single update_component creates it; so does the batch.
    ctx, session, _ = mixed
    PyPSAService.get_network().storage_units.drop(columns=["inv_power_overnight"], inplace=True)
    preview = chat_tools.preview_project_changes(OJ21[2:3])
    assert "inv_power_overnight" not in PyPSAService.get_network().storage_units.columns
    assert [(d["name"], d["before"], d["after"]) for d in preview["diff"]] == [("Battery", "not set", 150)]
    frames = dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]})
    row = next(r for r in frame(frames, "tool_pending_confirmation")["edit_card"]["rows"] if r["role"] == "typed")
    assert (row["old"], row["new"]) == (None, 150000)
    receipt = frame(frames, "tool_result")["result"]["_edit_receipt"]
    assert receipt["outcome"] == "verified"
    assert "not set → 150 EUR/kW (stored as 150000 EUR/MW)" in receipt["says"], receipt["says"]
    assert PyPSAService.get_network().storage_units.at["Battery", "inv_power_overnight"] == 150000
    undo = [(r["name"], r["field"], r["old"], r["new"]) for r in edit_card._undo_preview({}) if r["role"] == "typed"]
    assert ("Battery", "inv_power_overnight", 150000, None) in undo, undo
    # The undo row keeps the unit and display the edit card had (never "No unit").
    back = next(r for r in edit_card._undo_preview({}) if r["field"] == "inv_power_overnight")
    assert (back["unit"], back["display"]) == ("EUR/MW", {"old": 150, "new": None, "unit": "EUR/kW"}), back
    assert back["label"] != "inv_power_overnight"
    chat_tools.undo_last()
    units = PyPSAService.get_network().storage_units
    assert "inv_power_overnight" not in units.columns or pd.isna(units.at["Battery", "inv_power_overnight"])


def test_one_missing_member_refuses_everything_and_names_it(mixed):
    ctx, _, root = mixed
    detail = refusal([{"component_class": "Line", "names": ["L1", "L2"], "attribute": "s_nom", "value": 500},
                      {"component_class": "Line", "names": ["L3"], "attribute": "s_nom", "value": 400}])
    assert detail["error_kind"] == "change_component_missing"
    assert "'L3'" in detail["message"] and "'L1'" in detail["message"] and "'L2'" in detail["message"]
    assert not list((root / "assistant_previews").glob("*.json")) if (root / "assistant_previews").exists() else True
    assert values() == BEFORE


def test_missing_names_are_bounded_in_the_message(mixed):
    detail = refusal([{"component_class": "Line", "names": ["x" * 500] + [f"M{i}" for i in range(15)],
                       "attribute": "s_nom", "value": 1}])
    assert detail["error_kind"] == "change_component_missing"
    assert len(detail["message"]) < 600


@pytest.mark.parametrize("change,kind", [
    ({"component_class": "Line", "names": ["L1"], "attribute": "s_nom_opt", "value": 5}, "read_only_field"),
    ({"component_class": "StorageUnit", "names": ["Battery"], "attribute": "capital_cost", "value": 5}, "derived_field"),
    ({"component_class": "Line", "names": ["L1"], "attribute": "bus0", "value": 5}, "invalid_change_attribute"),
    ({"component_class": "Line", "names": ["L1"], "attribute": "no_such_field", "value": 5}, "invalid_change_attribute"),
    ({"component_class": "Line", "names": ["L1"], "attribute": "s_nom_extendable", "value": 1}, "invalid_edit_value"),
    ({"component_class": "Line", "names": ["L1"], "attribute": "s_nom", "value": -5}, "invalid_edit_value"),
    ({"component_class": "Line", "names": ["L1"], "attribute": "s_nom", "value": float("inf")}, "invalid_changes"),
    ({"component_class": "Carrier", "names": ["AC"], "attribute": "co2_emissions", "value": 1}, "invalid_changes"),
    ({"component_class": "GlobalConstraint", "names": ["c"], "attribute": "constant", "value": 1}, "invalid_changes"),
    ({"component_class": "ShuntImpedance", "names": ["s"], "attribute": "g", "value": 1}, "invalid_changes"),
])
def test_refused_fields_and_classes(mixed, change, kind):
    assert refusal([change])["error_kind"] == kind
    assert values() == BEFORE


def test_row_cap_counts_entity_field_rows(mixed):
    many = [{"component_class": "Line", "names": ["L1", "L2"], "attribute": f, "value": 1.0}
            for f in ("s_nom", "s_max_pu", "capital_cost", "length", "terrain_factor", "num_parallel",
                      "s_nom_min", "s_nom_max", "marginal_cost", "lifetime", "build_year")]
    assert refusal(many)["error_kind"] == "invalid_preview"


def test_preview_leaves_live_state_untouched_even_when_refused(mixed):
    ctx = mixed[0]
    before = (undo_service.depth(), len(change_log_service.get_all()), json.dumps(ctx.user_ts, default=str, sort_keys=True),
              wt._network_input_fingerprint(ctx))
    chat_tools.preview_project_changes(OJ21)
    refusal([*OJ21, {"component_class": "Line", "names": ["L1"], "attribute": "s_nom_opt", "value": 5}])
    assert (undo_service.depth(), len(change_log_service.get_all()), json.dumps(ctx.user_ts, default=str, sort_keys=True),
            wt._network_input_fingerprint(ctx)) == before
    assert values() == BEFORE


def test_one_undo_restores_the_whole_batch(mixed):
    ctx, session, _ = mixed
    PyPSAService.get_network().meta["assistant_solve_input_fingerprint"] = wt._network_input_fingerprint(ctx)
    preview = chat_tools.preview_project_changes(OJ21)
    result = frame(dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]}), "tool_result")["result"]
    assert result["_edit_receipt"]["results_stale"] is True
    assert undo_service.depth() == 1
    chat_tools.undo_last()
    assert values() == BEFORE
    assert undo_service.depth() == 0
    with pytest.raises(HTTPException) as failure:
        chat_tools.undo_last()
    assert failure.value.status_code == 409
    assert values() == BEFORE


def test_card_for_one_preview_cannot_approve_another(mixed):
    first = chat_tools.preview_project_changes(OJ21[:2])
    second = chat_tools.preview_project_changes(OJ21[2:])
    card = edit_card.preview("apply_project_changes", {"preview_id": first["preview_id"]})
    with pytest.raises(HTTPException) as failure:
        edit_card.require_current("apply_project_changes", {"preview_id": second["preview_id"]}, card)
    assert failure.value.detail["error_kind"] == "edit_preview_stale"


def test_record_changed_after_the_card_refuses_the_apply(mixed):
    ctx, session, root = mixed
    preview = chat_tools.preview_project_changes(OJ21)
    path = root / "assistant_previews" / (preview["preview_id"] + ".json")

    def tamper(data):
        record = json.loads(path.read_text())
        record["changes"][0]["value"] = 900
        record["diff"][0]["after"] = 900
        record["review_sha256"] = assistant_tools._preview_digest(record)
        path.write_text(json.dumps(record))
        return "approve"

    frames = dispatch(session, "apply_project_changes", {"preview_id": preview["preview_id"]}, decide=tamper)
    assert any(e == "tool_error" for e, _ in frames) or "error" in json.dumps(frame(frames, "tool_result"), default=str)
    assert "invalid_preview" in json.dumps(frames, default=str)
    assert values() == BEFORE


def test_sweep_and_experiment_allowlist_unchanged(mixed):
    assert wt._ATTRIBUTES["Line"] == {"s_nom", "s_nom_min", "s_nom_max", "s_nom_extendable", "s_max_pu",
                                      "capital_cost", "r", "x"}
    with pytest.raises(HTTPException):
        assistant_tools._changes([{"component_class": "Line", "names": ["L1"], "attribute": "length", "value": 2.0}])


def test_a_priced_part_without_a_lifetime_is_refused_as_a_single_edit_is(mixed):
    # GK B: the bulk handler skips the part check update_component runs, so one
    # card could store a priced energy part that no lifetime prices.
    with pytest.raises(HTTPException) as single:
        chat_tools.update_component("StorageUnit", "Battery", {"inv_energy_overnight": 50000.0})
    assert single.value.status_code == 422
    detail = refusal([{"component_class": "StorageUnit", "names": ["Battery"],
                       "attribute": "inv_energy_overnight", "value": 50000.0}])
    assert detail["error_kind"] == "invalid_edit_value"
    assert str(single.value.detail) in detail["message"]
    assert detail["message"].startswith("Storage 'Battery'") and "StorageUnit" not in detail["message"]
    assert values() == BEFORE


def test_a_part_priced_with_its_lifetime_in_the_same_preview_passes(mixed):
    # GK B's two cases on an imported battery without power parts: the price
    # alone refuses as update_component does; with its lifetime it previews.
    PyPSAService.get_network().storage_units.drop(columns=["inv_power_overnight", "inv_power_lifetime"], inplace=True)
    price = {"component_class": "StorageUnit", "names": ["Battery"], "attribute": "inv_power_overnight", "value": 150000}
    with pytest.raises(HTTPException) as single:
        chat_tools.update_component("StorageUnit", "Battery", {"inv_power_overnight": 150000})
    assert single.value.status_code == 422
    assert refusal([price])["error_kind"] == "invalid_edit_value"
    assert "inv_power_overnight" not in PyPSAService.get_network().storage_units.columns
    preview = chat_tools.preview_project_changes([price, {**price, "attribute": "inv_power_lifetime", "value": 20}])
    assert preview["valid"] and preview["changes_total"] == 2
