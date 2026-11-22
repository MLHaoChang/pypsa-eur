"""Chat's Excel input workbook tools (excel-input-workbook issue 06, IMPORT-FLOW-1).

The downloads are offers the browser fetches (no bytes, nothing written); the
preview writes nothing; the import shows the preview on its confirmation card
first, creates a NEW project only, and never overwrites or touches the open one.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi import HTTPException

from db.models import Project
from services import chat_service, chat_tools, project_registry, upload_service
from services.pypsa_service import PyPSAService
from tests.test_excel_workbook_routes import MIME, _workbook
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.fixture
def session(baseline):  # noqa: F811
    value = chat_service.ChatSession()
    chat_tools.set_chat_session(value)
    return value


def dispatch(session, name, args, decision="approve"):
    frames, collector = [], []
    call = {"id": uuid.uuid4().hex, "name": name, "input": args}
    for event, data in chat_service._dispatch_real_tool_call(session, call, collector):
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            session.record_decision(data["confirmation_token"], decision)
    return frames, collector


def _attach(data: bytes, filename: str = "Grid West.xlsx"):
    return upload_service.add_upload("Tool Baseline", data, filename, MIME)


def _projects() -> list[str]:
    with chat_tools._acting() as (db, _user):
        return sorted(p.name for p in db.query(Project).all())


def test_the_template_is_offered_as_a_browser_download_and_nothing_is_written(baseline):  # noqa: F811
    before = _projects()
    out = chat_tools.DISPATCHERS["get_input_workbook"]()
    assert out["download_url"] == "/api/workbook/template"
    assert out["workbook_download"] == "template"
    assert out["filename"].endswith(".xlsx")
    assert _projects() == before


def test_a_project_s_workbook_is_offered_for_a_readable_project_only(baseline):  # noqa: F811
    out = chat_tools.DISPATCHERS["get_input_workbook"](project="Tool Baseline")
    assert out["download_url"] == "/api/projects/Tool%20Baseline/workbook"
    assert out["project"] == "Tool Baseline" and out["filename"] == "Tool Baseline.xlsx"
    with pytest.raises(HTTPException) as refused:
        chat_tools.DISPATCHERS["get_input_workbook"](project="No Such Project")
    assert refused.value.status_code == 404


def test_the_offered_link_downloads_the_workbook(baseline, client):  # noqa: F811
    out = chat_tools.DISPATCHERS["get_input_workbook"](project="Tool Baseline")
    r = client.get(out["download_url"])
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_a_preview_reads_the_upload_and_writes_nothing(baseline):  # noqa: F811
    data = _workbook()
    meta = _attach(data)
    before = _projects()
    out = chat_tools.DISPATCHERS["preview_input_workbook"](file_id=meta.file_id)
    assert out["sha256"] == hashlib.sha256(data).hexdigest()
    assert out["suggested_name"] == "Grid West"
    assert out["summary"]["buses"] >= 1
    assert out["next_tool"] == "import_input_workbook"
    assert _projects() == before


def test_an_import_is_carded_with_the_preview_and_creates_a_closed_new_project(
    session,
    baseline,  # noqa: F811
):  # noqa: F811
    data = _workbook()
    meta = _attach(data)
    sha = hashlib.sha256(data).hexdigest()
    frames, collector = dispatch(
        session,
        "import_input_workbook",
        {"file_id": meta.file_id, "expected_sha256": sha},
    )
    card = next(d for e, d in frames if e == "tool_pending_confirmation")
    wb = card["workbook_card"]
    assert (
        wb["kind"] == "workbook_import"
        and wb["sha256"] == sha
        and wb["name"] == "Grid West"
    )
    assert wb["summary"]["buses"] >= 1 and "counts" in wb["report"]
    result = next(d["result"] for e, d in frames if e == "tool_result")
    assert result["name"] == "Grid West" and result["workbook_imported"] is True
    assert "Grid West" in _projects()
    # The new project stays closed: the open project is still the baseline.
    assert PyPSAService.get_active_context().project_uuid == baseline[1]


def test_a_denied_import_creates_nothing(session, baseline):  # noqa: F811
    data = _workbook()
    meta = _attach(data)
    before = _projects()
    frames, _ = dispatch(
        session,
        "import_input_workbook",
        {"file_id": meta.file_id, "expected_sha256": hashlib.sha256(data).hexdigest()},
        decision="deny",
    )
    assert any(e == "tool_pending_confirmation" for e, _ in frames)
    assert not any(e == "tool_result" for e, _ in frames)
    assert _projects() == before


def test_a_hash_that_is_not_the_preview_s_refuses_before_any_card(session, baseline):  # noqa: F811
    meta = _attach(_workbook())
    before = _projects()
    frames, _ = dispatch(
        session,
        "import_input_workbook",
        {"file_id": meta.file_id, "expected_sha256": "0" * 64},
    )
    assert not any(e == "tool_pending_confirmation" for e, _ in frames)
    error = next(d for e, d in frames if e == "tool_error")
    assert error["error_kind"] == "import_file_changed"
    assert _projects() == before


def test_a_taken_name_refuses_and_overwrites_nothing(session, baseline):  # noqa: F811
    data = _workbook()
    meta = _attach(data)
    folder = baseline[2]
    files_before = sorted(p.name for p in folder.iterdir())
    with pytest.raises(HTTPException) as refused:
        chat_tools.DISPATCHERS["import_input_workbook"](
            file_id=meta.file_id,
            expected_sha256=hashlib.sha256(data).hexdigest(),
            name="Tool Baseline",
        )
    assert refused.value.status_code == 409
    assert sorted(p.name for p in folder.iterdir()) == files_before
    with chat_tools._acting() as (db, user):
        assert (
            project_registry.resolve_project(db, user, "Tool Baseline").name
            == "Tool Baseline"
        )


def test_a_file_changed_after_the_card_is_refused_and_creates_nothing(
    session,
    baseline,  # noqa: F811
):
    data = _workbook()
    meta = _attach(data)
    before = _projects()
    path = upload_service.get_upload_path("Tool Baseline", meta.file_id)
    frames, collector = [], []
    call = {
        "id": uuid.uuid4().hex,
        "name": "import_input_workbook",
        "input": {
            "file_id": meta.file_id,
            "expected_sha256": hashlib.sha256(data).hexdigest(),
        },
    }
    for event, payload in chat_service._dispatch_real_tool_call(
        session, call, collector
    ):
        frames.append((event, payload))
        if event == "tool_pending_confirmation":
            # The user's file is replaced while the card is open.
            path.write_bytes(_workbook(rate=0.05))
            session.record_decision(payload["confirmation_token"], "approve")
    assert not any(e == "tool_result" for e, _ in frames)
    error = next(d for e, d in frames if e == "tool_error")
    assert error["error_kind"] == "import_file_changed"
    assert _projects() == before


def test_a_named_import_shows_the_name_on_the_card_and_creates_that_project(
    session,
    baseline,  # noqa: F811
):
    data = _workbook()
    meta = _attach(data)
    frames, _ = dispatch(
        session,
        "import_input_workbook",
        {
            "file_id": meta.file_id,
            "expected_sha256": hashlib.sha256(data).hexdigest(),
            "name": "  Plant X  ",
        },
    )
    card = next(d for e, d in frames if e == "tool_pending_confirmation")
    assert card["workbook_card"]["name"] == "Plant X"
    assert "'Plant X'" in card["workbook_card"]["says"]
    assert (
        next(d["result"] for e, d in frames if e == "tool_result")["name"] == "Plant X"
    )
    assert "Plant X" in _projects() and "Grid West" not in _projects()


@pytest.mark.parametrize("unattended", ["auto_approve", "envelope"])
def test_nothing_but_the_user_s_card_approves_an_import(
    session,
    baseline,  # noqa: F811
    monkeypatch,
    unattended,
):
    from harness import confirm
    from services import run_envelope

    if unattended == "auto_approve":
        monkeypatch.setattr(confirm, "AUTO_APPROVE_TIERS", frozenset({"destructive"}))
    else:
        monkeypatch.setattr(run_envelope, "admit", lambda *a, **k: "admitted")
    data = _workbook()
    meta = _attach(data)
    before = _projects()
    frames, _ = dispatch(
        session,
        "import_input_workbook",
        {"file_id": meta.file_id, "expected_sha256": hashlib.sha256(data).hexdigest()},
        decision="deny",
    )
    assert "import_input_workbook" in confirm.CARD_ONLY_TOOLS
    assert any(e == "tool_pending_confirmation" for e, _ in frames)
    assert _projects() == before


@pytest.mark.parametrize(
    ("call", "args", "kind"),
    [
        ("preview_input_workbook", {"file_id": "  "}, "invalid_file_id"),
        ("preview_input_workbook", {"file_id": None}, "invalid_file_id"),
        (
            "import_input_workbook",
            {"file_id": "x", "expected_sha256": " "},
            "invalid_expected_sha256",
        ),
        (
            "import_input_workbook",
            {"file_id": "x", "expected_sha256": None},
            "invalid_expected_sha256",
        ),
        (
            "import_input_workbook",
            {"file_id": "", "expected_sha256": "a" * 64},
            "invalid_file_id",
        ),
        ("get_input_workbook", {"project": "p" * 257}, "invalid_project_name"),
    ],
)
def test_malformed_arguments_refuse_with_their_kind(baseline, call, args, kind):  # noqa: F811
    with pytest.raises(HTTPException) as refused:
        chat_tools.DISPATCHERS[call](**args)
    assert refused.value.detail["error_kind"] == kind


def test_a_256_character_project_name_is_looked_up_not_refused_for_length(baseline):  # noqa: F811
    with pytest.raises(HTTPException) as refused:
        chat_tools.DISPATCHERS["get_input_workbook"](project="p" * 256)
    assert refused.value.status_code == 404


def test_the_model_sees_capped_report_lists_with_their_totals():
    from services.workbook_chat import MODEL_LIST_CAP, _capped

    full = {
        "counts": {"Nodes": 1},
        "warnings": [f"w{i}" for i in range(MODEL_LIST_CAP + 2)],
        "unmapped": [{"source_item": "a", "reason": "b"}] * MODEL_LIST_CAP,
    }
    out = _capped(full, MODEL_LIST_CAP)
    assert out["warnings"] == full["warnings"][:MODEL_LIST_CAP]
    assert out["warnings_total"] == MODEL_LIST_CAP + 2
    assert len(out["unmapped"]) == MODEL_LIST_CAP and "unmapped_total" not in out
    assert (
        out["assumed"] == [] and out["suggestions"] == [] and "assumed_total" not in out
    )
    assert out["counts"] == {"Nodes": 1}


def test_an_unexpected_preview_failure_is_logged_and_gets_no_card(
    session,
    baseline,  # noqa: F811
    monkeypatch,
    caplog,
):
    from services import workbook_chat

    def boom(*_a, **_k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(workbook_chat, "preview", boom)
    with caplog.at_level("WARNING", logger="pypsa_gui.chat"):
        frames, _ = dispatch(
            session,
            "import_input_workbook",
            {"file_id": "x", "expected_sha256": "a" * 64},
        )
    assert not any(e == "tool_pending_confirmation" for e, _ in frames)
    assert (
        next(d for e, d in frames if e == "tool_error")["error_kind"]
        == "workbook_preview_unavailable"
    )
    assert any(r.exc_info and "disk gone" in str(r.exc_info[1]) for r in caplog.records)
