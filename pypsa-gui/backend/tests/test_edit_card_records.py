"""Old -> new edit cards for the record tools (Q3-CARDS-1, group "records"):
put_stress_scenarios, record_asset_health, set_study_parameter, update_project_layout."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import harness.edit_card_records  # noqa: F401  (registers the adapters)
from harness import edit_card
from services import chat_service, chat_tools
from tests.study_s4_support import dir_hash, enable_studies
from tests.test_edit_card import _dispatch
from tests.test_lv08_b1_study_project_tools import _start


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def session(client):
    chat_session = chat_service.ChatSession()
    chat_tools.set_chat_session(chat_session)
    yield chat_session
    chat_tools.set_chat_session(None)


def _rows(card):
    return {r["field"]: r for r in card["rows"] if r["role"] != "note"}


def _scenario(sid="dunkel", freq=2.0, load=1.1, **extra):
    return {"id": sid, "name": sid.title(), "kind": "parametric", "frequency_per_year": freq,
            "electrical_load_multiplier": load, "renewable_availability_multiplier": 0.2, **extra}


def _entry(name="G1", rate=0.02, mttr=48.0, **extra):
    return {"component": "generators", "name": name, "outage_rate_value": rate, "mttr_hours": mttr,
            "method": "inspection", "measured_at": "2025-01-31", **extra}


def _refusal(tool, args):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview(tool, args)
    return exc.value


# ── put_stress_scenarios ───────────────────────────────────────────────────

def test_stress_scenarios_card_old_to_new_and_verified_receipt(session, api_project):
    name = api_project("rec-stress")
    chat_tools.put_stress_scenarios(name, [_scenario(), _scenario("cold", 1.0)])
    args = {"name": name, "scenarios": [_scenario(freq=3.0), _scenario("cold", 1.0), _scenario("calm", 4.0)]}
    frames, cards, result = _dispatch(session, "put_stress_scenarios", args)
    card = cards[0]["edit_card"]
    assert card["kind"] == "settings_edit" and card["complete"]
    rows = _rows(card)
    freq = rows["scenario.dunkel.frequency_per_year"]
    assert (freq["old"], freq["new"], freq["label"]) == (2.0, 3.0, "Stress scenario · Dunkel · Events/yr")
    added = rows["scenario.calm"]
    assert added["old"] is None and added["new"] == "parametric · 4.0 events/yr"
    assert "scenario.cold" not in "".join(rows)  # unchanged scenario: nothing shown
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_stress_scenarios_removed_and_reordered_show(client, api_project):
    name = api_project("rec-stress-rm")
    chat_tools.put_stress_scenarios(name, [_scenario("a"), _scenario("b"), _scenario("c")])
    card = edit_card.preview("put_stress_scenarios", {"name": name, "scenarios": [_scenario("c"), _scenario("a")]})
    rows = _rows(card)
    assert rows["scenario.b"]["new"] is None and rows["scenario.b"]["words"]["new"] == "Removed"
    assert (rows["order"]["old"], rows["order"]["new"]) == ("a, c", "c, a")
    chat_tools.put_stress_scenarios(name, [_scenario("c"), _scenario("a")])
    args = {"name": name, "scenarios": [_scenario("c"), _scenario("a")]}
    assert edit_card.ADAPTERS["put_stress_scenarios"].readback(args, card["rows"]) == [r["new"] for r in card["rows"]]


def test_stress_scenarios_noop_and_invalid_refuse(client, api_project):
    name = api_project("rec-stress-same")
    chat_tools.put_stress_scenarios(name, [_scenario()])
    assert _refusal("put_stress_scenarios", {"name": name, "scenarios": [_scenario()]}).detail["error_kind"] == "empty_edit"
    bad = _refusal("put_stress_scenarios", {"name": name, "scenarios": [_scenario(freq=0)]})
    assert bad.detail["error_kind"] == "invalid_edit_value" and "frequency_per_year" in bad.detail["message"]


def test_stress_scenarios_series_are_summarised(client, api_project):
    name = api_project("rec-stress-series")
    series = {"G1": [0.1, 0.2]}
    prof = {"id": "p", "kind": "profiles", "frequency_per_year": 1.0, "renewable_availability": series}
    chat_tools.put_stress_scenarios(name, [prof])
    card = edit_card.preview("put_stress_scenarios", {"name": name, "scenarios": [
        {**prof, "renewable_availability": {"G1": [0.1, 0.9]}}]})
    (row,) = [r for r in card["rows"] if r["role"] == "typed"]
    assert row["old"].startswith("1 fields") and row["new"].startswith("1 fields") and row["old"] != row["new"]
    assert "0.9" not in json.dumps(card["rows"])


# ── record_asset_health ────────────────────────────────────────────────────

def test_asset_health_card_old_to_new_and_verified_receipt(session, api_project):
    name = api_project("rec-health")
    chat_tools.record_asset_health(name, [_entry(), _entry("G2")])
    args = {"name": name, "entries": [_entry(rate=0.05), _entry("G2"), _entry("G3", rate=0.01)]}
    frames, cards, result = _dispatch(session, "record_asset_health", args)
    rows = _rows(cards[0]["edit_card"])
    rate = rows["health:generators:G1:outage_rate_value"]
    assert (rate["old"], rate["new"], rate["label"]) == (0.02, 0.05, "Asset health · generators G1 · Outage rate")
    assert rows["health:generators:G3"]["old"] is None and "outage rate 0.01" in rows["health:generators:G3"]["new"]
    assert not any("G2" in f for f in rows)
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_asset_health_removal_noop_and_invalid(client, api_project):
    name = api_project("rec-health-rm")
    chat_tools.record_asset_health(name, [_entry(), _entry("G2")])
    card = edit_card.preview("record_asset_health", {"name": name, "entries": [_entry()]})
    assert _rows(card)["health:generators:G2"]["words"]["new"] == "Removed"
    assert _refusal("record_asset_health", {"name": name, "entries": [_entry(), _entry("G2")]}
                    ).detail["error_kind"] == "empty_edit"
    assert _refusal("record_asset_health", {"name": name, "entries": [_entry(rate=1.5)]}
                    ).detail["error_kind"] == "invalid_edit_value"


# ── set_study_parameter ────────────────────────────────────────────────────

def _study_args(project, study_id, **extra):
    return {"project": project, "study_id": study_id, "key": "discount_rate", "value": 0.06,
            "unit": "per unit", **extra}


def test_study_parameter_card_old_to_new_and_verified_receipt(session, api_project, studies_on):
    study_id, project = _start(api_project("rec-ledger-src"), "rec-ledger")
    args = _study_args(project, study_id, source="the owner's hurdle rate")
    frames, cards, result = _dispatch(session, "set_study_parameter", args)
    card = cards[0]["edit_card"]
    rows = _rows(card)
    value = rows["ledger.discount_rate.value"]
    assert value["new"] == 0.06 and value["old"] != 0.06 and value["unit"] == "per unit"
    assert rows["ledger.discount_rate.source"]["new"] == "the owner's hurdle rate"
    assert (rows["ledger.discount_rate.status"]["new"]) == "customised"
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_study_parameter_noop_invalid_and_disabled(client, api_project, studies_on):
    study_id, project = _start(api_project("rec-ledger-src2"), "rec-ledger2")
    chat_tools.set_study_parameter(project, study_id, "discount_rate", 0.06, "per unit", source="x")
    assert _refusal("set_study_parameter", _study_args(project, study_id, source="x")
                    ).detail["error_kind"] == "empty_edit"
    same_value = edit_card.preview("set_study_parameter", _study_args(project, study_id, source="y"))
    value = _rows(same_value)["ledger.discount_rate.value"]
    assert value["old"] == value["new"] == 0.06  # a value the user names shows even when unchanged
    assert _refusal("set_study_parameter", _study_args(project, study_id, unit="EUR")).detail["error_kind"] \
        == "invalid_edit_value"
    assert _refusal("set_study_parameter", _study_args(project, study_id, key="no_such_row")).status_code == 422


# ── update_project_layout ──────────────────────────────────────────────────

def _layout(x=1, nodes=2):
    return {"version": 2, "savedAt": 5, "nodes": [{"id": f"n{i}", "canvasX": x + i, "canvasY": 0} for i in range(nodes)],
            "edges": []}


def test_layout_card_shows_what_differs_and_verified_receipt(session, api_project):
    name = api_project("rec-layout")
    chat_tools.update_project_layout(name, _layout())
    new = {**_layout(x=9, nodes=3), "assetMode": "grouped"}
    frames, cards, result = _dispatch(session, "update_project_layout", {"name": name, "layout": new})
    card = cards[0]["edit_card"]
    rows = _rows(card)
    assert set(rows) == {"layout.nodes", "layout.assetMode"}  # version, savedAt, edges unchanged: not shown
    assert (rows["layout.nodes"]["old"], rows["layout.nodes"]["new"]) == ("2 items", "3 items")
    assert (rows["layout.assetMode"]["old"], rows["layout.assetMode"]["new"]) == (None, "grouped")
    assert any("1 added" in r.get("note", "") and "2 moved" in r["note"] for r in card["rows"])
    assert "n0" not in json.dumps(card["rows"])  # never dumped
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_layout_noop_oversize_and_non_object_refuse(client, api_project):
    name = api_project("rec-layout-same")
    chat_tools.update_project_layout(name, _layout())
    assert _refusal("update_project_layout", {"name": name, "layout": _layout()}).detail["error_kind"] == "empty_edit"
    big = {"blob": "x" * (4 * 1024 * 1024 + 1)}
    assert _refusal("update_project_layout", {"name": name, "layout": big}).status_code == 413
    assert _refusal("update_project_layout", {"name": name, "layout": [1]}).detail["error_kind"] == "invalid_edit_value"


# ── all four ───────────────────────────────────────────────────────────────

def test_previews_write_nothing(client, api_project, project_storage_dir, studies_on):
    name = api_project("rec-nowrite")
    study_id, study_project = _start(api_project("rec-nowrite-src"), "rec-nowrite-base")
    chat_tools.put_stress_scenarios(name, [_scenario()])
    chat_tools.record_asset_health(name, [_entry()])
    chat_tools.update_project_layout(name, _layout())
    before = {n: dir_hash(project_storage_dir(n)) for n in (name, study_project)}
    edit_card.preview("put_stress_scenarios", {"name": name, "scenarios": [_scenario(freq=9.0)]})
    edit_card.preview("record_asset_health", {"name": name, "entries": [_entry(rate=0.5)]})
    edit_card.preview("update_project_layout", {"name": name, "layout": _layout(x=7)})
    edit_card.preview("set_study_parameter", _study_args(study_project, study_id))
    assert {n: dir_hash(project_storage_dir(n)) for n in before} == before


def test_an_outsider_is_refused_before_any_old_value(client, api_project, second_identity, studies_on):
    name = api_project("rec-acl")
    study_id, study_project = _start(api_project("rec-acl-src"), "rec-acl-base")
    chat_tools.put_stress_scenarios(name, [_scenario()])
    chat_tools.set_acting_user(second_identity["user_id"])  # after the owner's setup
    for tool, args in (
        ("put_stress_scenarios", {"name": name, "scenarios": [_scenario(freq=9.0)]}),
        ("record_asset_health", {"name": name, "entries": [_entry()]}),
        ("update_project_layout", {"name": name, "layout": _layout()}),
        ("set_study_parameter", _study_args(study_project, study_id)),
    ):
        with pytest.raises(HTTPException) as exc:
            edit_card.preview(tool, args)
        assert exc.value.status_code in (403, 404), tool
        assert "old" not in json.dumps(exc.value.detail), tool


# ── gatekeeper fixes: disabled studies, foreign lock, receipts that can fail, text caps ──

@pytest.fixture
def colleague(_auth_db, seeded_identity):
    """A second user in the SAME org (so the ACL passes and only the lock can refuse)."""
    import uuid
    from datetime import UTC, datetime

    from db.models import OrgMembership, User
    from services.auth_service import hash_password

    _engine, session_local = _auth_db
    with session_local() as db:
        user = User(id=uuid.uuid4(), email=f"colleague-{uuid.uuid4().hex[:6]}@example.com",
                    password_hash=hash_password("irrelevant"), status="active", is_super_admin=False,
                    created_at=datetime.now(tz=UTC))
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=user.id, org_id=seeded_identity["org_id"], role="admin"))
        db.commit()
        return user.id


def test_set_study_parameter_card_refuses_a_disabled_feature_before_any_lookup(client, api_project, monkeypatch):
    from routers import studies

    def off():
        raise HTTPException(404, {"error_kind": "studies_disabled"})

    monkeypatch.setattr(studies, "require_decision_studies_enabled", off)
    project = api_project("rec-off")
    args = {"project": project, "study_id": "no-such-study", "key": "discount_rate",
            "value": 0.06, "unit": "per unit"}
    card_refusal = _refusal("set_study_parameter", args)
    with pytest.raises(HTTPException) as handler:
        chat_tools.set_study_parameter(project, "no-such-study", "discount_rate", 0.06, "per unit")
    assert (card_refusal.status_code, card_refusal.detail) == (handler.value.status_code, handler.value.detail)
    assert card_refusal.detail["error_kind"] == "studies_disabled"


def test_a_foreign_lock_refuses_each_card_where_it_refuses_the_tool(client, api_project, colleague, studies_on):
    name = api_project("rec-lock")
    study_id, study_project = _start(api_project("rec-lock-src"), "rec-lock-base")
    for project in (name, study_project):
        assert client.post(f"/api/projects/{project}/lock").status_code == 200
    chat_tools.set_acting_user(colleague)
    calls = (
        ("put_stress_scenarios", {"name": name, "scenarios": [_scenario()]},
         lambda: chat_tools.put_stress_scenarios(name, [_scenario()])),
        ("record_asset_health", {"name": name, "entries": [_entry()]},
         lambda: chat_tools.record_asset_health(name, [_entry()])),
        ("update_project_layout", {"name": name, "layout": _layout()},
         lambda: chat_tools.update_project_layout(name, _layout())),
        ("set_study_parameter", _study_args(study_project, study_id),
         lambda: chat_tools.set_study_parameter(study_project, study_id, "discount_rate", 0.06, "per unit")),
    )
    for tool, args, run in calls:
        refusal = _refusal(tool, args)
        with pytest.raises(HTTPException) as handler:
            run()
        assert refusal.status_code == handler.value.status_code == 409, tool
        assert refusal.detail["error_kind"] == handler.value.detail["error_kind"] == "project_locked", tool
        assert refusal.detail == handler.value.detail, tool  # the handler's refusal, no card, no old value


def _unwritten_receipt(tool, args):
    """The card for `args`, and its readback with the store left untouched."""
    card = edit_card.preview(tool, args)
    return card["rows"], edit_card.ADAPTERS[tool].readback(args, card["rows"])


def test_layout_edit_hidden_by_a_count_reads_back_uncertain_until_written(client, api_project):
    name = api_project("rec-layout-fp")
    chat_tools.update_project_layout(name, _layout(x=1))
    args = {"name": name, "layout": _layout(x=50)}  # same node count, moved coordinates
    rows, stored = _unwritten_receipt("update_project_layout", args)
    (nodes,) = [r for r in rows if r["field"] == "layout.nodes"]
    assert nodes["old"] == "2 items" and nodes["new"] == "2 items (changed)" and nodes["fingerprint"]
    assert stored != [r["new"] for r in rows]  # the store is unchanged: the receipt cannot verify
    chat_tools.update_project_layout(name, args["layout"])
    assert edit_card.ADAPTERS["update_project_layout"].readback(args, rows) == [r["new"] for r in rows]


def test_stress_series_edit_reads_back_uncertain_until_written(client, api_project):
    name = api_project("rec-stress-fp")
    prof = {"id": "p", "kind": "profiles", "frequency_per_year": 1.0, "renewable_availability": {"G1": [0.1, 0.2]}}
    chat_tools.put_stress_scenarios(name, [prof])
    new = {**prof, "renewable_availability": {"G1": [0.1, 0.9]}}
    args = {"name": name, "scenarios": [new]}
    rows, stored = _unwritten_receipt("put_stress_scenarios", args)
    assert stored != [r["new"] for r in rows]
    chat_tools.put_stress_scenarios(name, [new])
    assert edit_card.ADAPTERS["put_stress_scenarios"].readback(args, rows) == [r["new"] for r in rows]


def test_asset_health_edit_reads_back_uncertain_until_written(client, api_project):
    name = api_project("rec-health-fp")
    chat_tools.record_asset_health(name, [_entry()])
    args = {"name": name, "entries": [_entry(rate=0.05)]}
    rows, stored = _unwritten_receipt("record_asset_health", args)
    assert stored != [r["new"] for r in rows]
    chat_tools.record_asset_health(name, args["entries"])
    assert edit_card.ADAPTERS["record_asset_health"].readback(args, rows) == [r["new"] for r in rows]


def test_study_parameter_edit_reads_back_uncertain_until_written(client, api_project, studies_on):
    study_id, project = _start(api_project("rec-ledger-fp-src"), "rec-ledger-fp")
    args = _study_args(project, study_id, source="x")
    rows, stored = _unwritten_receipt("set_study_parameter", args)
    assert stored != [r["new"] for r in rows]
    chat_tools.set_study_parameter(project, study_id, "discount_rate", 0.06, "per unit", source="x")
    assert edit_card.ADAPTERS["set_study_parameter"].readback(args, rows) == [r["new"] for r in rows]


def test_long_text_is_capped_and_an_edit_past_the_cap_still_shows(client, api_project):
    name = api_project("rec-cap")
    head = "h" * 150
    chat_tools.record_asset_health(name, [_entry(note=head + "A")])
    args = {"name": name, "entries": [_entry(note=head + "B")]}
    rows, stored = _unwritten_receipt("record_asset_health", args)
    (note,) = [r for r in rows if r["field"].endswith(":note")]
    assert len(note["old"]) <= 121 and note["new"].endswith("… (changed)") and note["fingerprint"]
    assert stored != [r["new"] for r in rows]
    chat_tools.record_asset_health(name, args["entries"])
    assert edit_card.ADAPTERS["record_asset_health"].readback(args, rows) == [r["new"] for r in rows]
    long_name = {**_scenario("long"), "name": "n" * 300}
    card = edit_card.preview("put_stress_scenarios", {"name": name, "scenarios": [long_name]})
    assert all(len(r["label"]) < 200 for r in card["rows"] if r["role"] != "note")
