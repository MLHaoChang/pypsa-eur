"""
The results workbook's two doors (`.scratch/results-workbook/issues/03-export-picker-and-chat-tool.md`):
`GET /api/results/workbook.xlsx` (the Export chooser) and the chat tool
`export_results_workbook` build the same sheets for the same view list, and the
picker's view list marks the cards' views as pre-ticked.
"""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from services import chat_tools as T
from tests.test_results_workbook_views import NETWORK_VIEWS, _network

URL = "/api/results/workbook.xlsx"


def _sheets(blob: bytes) -> dict[str, list[tuple]]:
    wb = load_workbook(io.BytesIO(blob))
    # Every cell but About's own clock ("Generated at").
    return {ws.title: [tuple(c.value for c in row) for row in ws.iter_rows()
                       if not (ws.title == "About" and row[0].value == "Generated at")] for ws in wb}


def _charts(blob: bytes) -> dict[str, int]:
    return {ws.title: len(ws._charts) for ws in load_workbook(io.BytesIO(blob))}


@pytest.fixture
def solved_project(install_network, tmp_projects_dir, monkeypatch):
    import services.workflow_tools as wt

    install_network(_network(), name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wt, "solve_freshness",
                        lambda ctx=None: {"inputs_changed_since_solve": False, "results_current": True})
    return tmp_projects_dir / "P"


def _tool_blob(**kw) -> bytes:
    meta = T.export_results_workbook(**kw)
    assert meta["kind"] == "agent_export" and meta["filename"].endswith(".xlsx")
    from services import upload_service

    return upload_service.get_upload_bytes("P", meta["file_id"])


@pytest.mark.parametrize("views,all_views", [([], False), (NETWORK_VIEWS[:3], False), ([], True)])
def test_chat_tool_and_route_write_the_same_sheets(client, solved_project, views, all_views):
    params = {"views": ",".join(views)}
    if all_views:
        params["all"] = "true"
    # The tool first: a request leaves the test thread's active network behind.
    tool = _tool_blob(views=views, all=all_views)
    r = client.get(URL, params=params)
    assert r.status_code == 200, r.text
    assert _sheets(tool) == _sheets(r.content)
    assert _charts(tool) == _charts(r.content)


def test_chat_tool_refuses_an_unknown_view_with_the_route_error_kind(solved_project):
    with pytest.raises(HTTPException) as exc:
        T.export_results_workbook(views=["nope"])
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == "unknown_result_view"


def test_chat_tool_refuses_a_stale_run_unless_stored(solved_project, monkeypatch):
    import services.workflow_tools as wt

    monkeypatch.setattr(wt, "solve_freshness",
                        lambda ctx=None: {"inputs_changed_since_solve": True, "results_current": False})
    with pytest.raises(HTTPException) as exc:
        T.export_results_workbook()
    assert exc.value.detail["error_kind"] == "results_workbook_stale"
    assert T.export_results_workbook(stored=True)["bytes"] > 0


def test_picker_lists_every_view_and_pre_ticks_the_cards(client, solved_project):
    from services.results_workbook.registry import resolve_views, workbook_views

    rows = client.get("/api/results/workbook/views").json()["views"]
    assert [r["view_id"] for r in rows] == [v.view_id for v in workbook_views()]
    assert {"view_id", "label", "card", "card_label", "selected", "available", "reason"} <= set(rows[0])
    ticked = [r["view_id"] for r in rows if r["selected"]]
    assert ticked and set(ticked) < {r["view_id"] for r in rows}
    sheets = _sheets(client.get(URL).content)
    from services.result_views import view
    # Each pre-ticked view that has results is a sheet of the default workbook.
    assert any(view(v).label in sheets for v in ticked)
    assert resolve_views(None, ["dispatch"]) == ["dispatch"]


def test_picker_marks_a_view_without_data_unavailable_with_a_reason(client, solved_project):
    rows = {r["view_id"]: r for r in client.get("/api/results/workbook/views").json()["views"]}
    assert rows["dispatch"]["available"] is True and rows["dispatch"]["reason"] is None
    # The test network was solved without an AC load flow.
    assert rows["loadflow"]["available"] is False
    assert rows["loadflow"]["reason"]


def test_every_workbook_view_has_a_results_tab_label():
    """The picker words each view as the Results panel does (UX: no raw ids)."""
    import re
    from pathlib import Path

    from services.results_workbook.registry import workbook_views

    src = (Path(__file__).resolve().parents[2] / "frontend/src/pages/Results.tsx").read_text()
    tabs = dict(re.findall(r"\{ id: '(\w+)',\s*label: '([^']+)'", src))
    missing = [v.view_id for v in workbook_views() if v.view_id not in tabs]
    assert not missing, f"workbook views with no Results tab label: {missing}"
