"""LV 13: the old → new cards for `campus_set_grid_limit`, `campus_reset_grid_limit` and
`gridspine_upload_external_dispatch`. Each card shows the change before the write; each
receipt reads the project back, so a write that stores nothing is never `verified`."""
from __future__ import annotations

import io
import json

import pytest
from fastapi import HTTPException, UploadFile

from db.models import User
from harness import edit_card
from services import chat_tools, gridspine_service as gs, project_registry
from services.adequacy import grid_limits

P = {"project_id": "Chat Hub"}


@pytest.fixture
def hub(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project = project_registry.create_root(db, user, "Chat Hub")
        project_registry.project_dir(project).mkdir(parents=True, exist_ok=True)
        yield project


def _folder(hub):
    return project_registry.project_dir(hub)


def _template(hub, **ledger):
    rows = [{"item": item, **row} for item, row in ledger.items()]
    (_folder(hub) / "eh_template.json").write_text(json.dumps({"ledger": rows}))


def _typed(card):
    return {r["field"]: r for r in card["rows"] if r["role"] == "typed"}


def _says_ok(card):
    assert not card["says"].endswith((".", "!", "?")), card["says"]  # EditCard.tsx appends ". Read back…"


# ── campus_set_grid_limit ───────────────────────────────────────────────────


def test_set_limit_card_shows_the_template_value_and_the_new_one(hub):
    _template(hub, oscillation={"value": 2.0, "source": "assumed"})
    args = {**P, "item": "oscillation", "source": "datasheet", "value": 0.5, "note": "OEM sheet"}
    card = edit_card.preview("campus_set_grid_limit", args)
    rows = _typed(card)
    assert (rows["value"]["old"], rows["value"]["new"], rows["value"]["unit"]) == (2.0, 0.5, "%")
    assert (rows["source"]["old"], rows["source"]["new"]) == ("assumed", "datasheet")
    assert rows["source"]["words"] == {"old": "Assumed (to check)", "new": "Datasheet"}
    assert (rows["note"]["old"], rows["note"]["new"]) == ("", "OEM sheet")
    assert card["says"] == "Sets the oscillation band to 0.5 %, Datasheet source"
    _says_ok(card)
    assert grid_limits.load_limits(_folder(hub)) == {}, "a card writes nothing"


def test_set_limit_receipt_is_verified_after_the_write(hub):
    args = {**P, "item": "connection_ramp", "source": "measured", "value": 5, "note": ""}
    card = edit_card.preview("campus_set_grid_limit", args)
    chat_tools.campus_set_grid_limit(**args)
    out = edit_card.receipt(card, args)
    assert out["outcome"] == "verified"
    assert {r["field"]: r["readback"] for r in out["rows"]} == {"value": 5.0, "source": "measured"}


def test_set_limit_receipt_is_not_verified_when_the_write_does_nothing(hub, monkeypatch):
    monkeypatch.setattr(grid_limits, "set_limit", lambda *a, **k: {})
    args = {**P, "item": "oscillation", "source": "measured", "value": 1}
    card = edit_card.preview("campus_set_grid_limit", args)
    chat_tools.campus_set_grid_limit(**args)
    assert edit_card.receipt(card, args)["outcome"] == "uncertain"


def test_set_limit_with_the_stored_values_is_an_empty_edit(hub):
    args = {**P, "item": "oscillation", "source": "measured", "value": 1}
    chat_tools.campus_set_grid_limit(**args)
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("campus_set_grid_limit", args)
    assert exc.value.detail["error_kind"] == "empty_edit"


def test_set_limit_fault_level_is_source_only_and_a_bad_value_is_the_writes_refusal(hub):
    card = edit_card.preview("campus_set_grid_limit", {**P, "item": "fault_level", "source": "measured"})
    assert set(_typed(card)) == {"source"}
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("campus_set_grid_limit", {**P, "item": "oscillation", "source": "measured", "value": -1})
    assert exc.value.detail["error_kind"] == "invalid_limit_value"


# ── campus_reset_grid_limit ─────────────────────────────────────────────────


def test_reset_limit_card_goes_back_to_the_template_and_the_receipt_reads_it(hub, monkeypatch):
    _template(hub, connection_ramp={"value": 10.0, "source": "code"})
    chat_tools.campus_set_grid_limit(**P, item="connection_ramp", source="assumed", value=3, note="guess")
    args = {**P, "item": "connection_ramp"}
    card = edit_card.preview("campus_reset_grid_limit", args)
    rows = _typed(card)
    assert (rows["entry"]["old"], rows["entry"]["new"]) == ("set by you", "not set")
    assert (rows["value"]["old"], rows["value"]["new"]) == (3.0, 10.0)
    assert (rows["source"]["old"], rows["source"]["new"]) == ("assumed", "code")
    assert (rows["note"]["old"], rows["note"]["new"]) == ("guess", "")
    _says_ok(card)
    chat_tools.campus_reset_grid_limit(**args)
    assert edit_card.receipt(card, args)["outcome"] == "verified"


def test_reset_limit_receipt_is_not_verified_when_the_write_does_nothing(hub, monkeypatch):
    chat_tools.campus_set_grid_limit(**P, item="oscillation", source="measured", value=1)
    args = {**P, "item": "oscillation"}
    card = edit_card.preview("campus_reset_grid_limit", args)
    monkeypatch.setattr(grid_limits, "clear_limit", lambda *a: True)
    chat_tools.campus_reset_grid_limit(**args)
    assert edit_card.receipt(card, args)["outcome"] == "uncertain"


def test_reset_limit_with_no_entry_of_its_own_is_an_empty_edit(hub):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("campus_reset_grid_limit", {**P, "item": "oscillation"})
    assert exc.value.detail["error_kind"] == "empty_edit"


# ── gridspine_upload_external_dispatch ──────────────────────────────────────


@pytest.fixture
def tables(hub, monkeypatch):
    files = {"d1": ("dispatch.csv", b"t,g\n0,1\n"), "d2": ("dispatch.csv", b"t,g\n0,2\n"),
             "l1": ("loads.csv", b"t,l\n0,1\n")}
    from types import SimpleNamespace

    from services import upload_service

    monkeypatch.setattr(upload_service, "get_upload_meta", lambda project, file_id: SimpleNamespace(mime="text/csv"))
    monkeypatch.setattr(chat_tools, "_require_active_project", lambda: "Chat Hub")
    monkeypatch.setattr(chat_tools, "_active_upload",
                        lambda file_id: UploadFile(filename=files[file_id][0], file=io.BytesIO(files[file_id][1])))
    monkeypatch.setattr(gs, "require_planning", lambda project: None)
    monkeypatch.setattr(gs, "_check_external", lambda *a: {"hours": 1})
    return files


def test_dispatch_card_shows_the_tables_and_the_receipt_reads_the_stored_files(tables, hub):
    args = {**P, "dispatch_file_id": "d1", "loads_file_id": "l1"}
    card = edit_card.preview("gridspine_upload_external_dispatch", args)
    rows = _typed(card)
    assert (rows["dispatch_table"]["old"], rows["dispatch_table"]["new"]) == ("none", "dispatch.csv")
    assert (rows["loads_table"]["old"], rows["loads_table"]["new"]) == ("none", "loads.csv")
    _says_ok(card)
    assert not (_folder(hub) / "gridspine" / "uploads").exists(), "a card stores nothing"
    chat_tools.gridspine_upload_external_dispatch(**args)
    out = edit_card.receipt(card, args)
    assert out["outcome"] == "verified"
    assert [r["readback"] for r in out["rows"]] == ["dispatch.csv", "loads.csv"]


def test_dispatch_receipt_is_not_verified_when_the_write_does_nothing(tables, hub, monkeypatch):
    monkeypatch.setattr(gs, "upload_external_dispatch", lambda *a, **k: {})
    args = {**P, "dispatch_file_id": "d1"}
    card = edit_card.preview("gridspine_upload_external_dispatch", args)
    chat_tools.gridspine_upload_external_dispatch(**args)
    assert edit_card.receipt(card, args)["outcome"] == "uncertain"


def test_dispatch_card_for_a_same_named_file_with_other_bytes_is_changed_and_reads_the_content(tables, hub):
    first = {**P, "dispatch_file_id": "d1"}
    chat_tools.gridspine_upload_external_dispatch(**first)
    args = {**P, "dispatch_file_id": "d2"}
    card = edit_card.preview("gridspine_upload_external_dispatch", args)
    assert _typed(card)["dispatch_table"]["new"] == "dispatch.csv (changed)"
    # the write is skipped: the old bytes are still stored under the same name
    assert edit_card.receipt(card, args)["outcome"] == "uncertain"
    chat_tools.gridspine_upload_external_dispatch(**args)
    assert edit_card.receipt(card, args)["outcome"] == "verified"
    with pytest.raises(HTTPException) as exc:  # the same bytes again change nothing
        edit_card.preview("gridspine_upload_external_dispatch", args)
    assert exc.value.detail["error_kind"] == "empty_edit"


def test_dispatch_card_refuses_a_table_the_write_would_refuse(tables, hub, monkeypatch):
    def refuse(*a):
        raise gs.ContractError("the dispatch has no hours column")

    monkeypatch.setattr(gs, "_check_external", refuse)
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("gridspine_upload_external_dispatch", {**P, "dispatch_file_id": "d1"})
    assert exc.value.status_code == 422 and "no hours column" in exc.value.detail
