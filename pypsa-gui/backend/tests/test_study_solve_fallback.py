"""template-e2e 30 (TE2E-30-1): after a Guided study the workbook, the dispatch status and the run state
read the study's own solve when the live network has no dispatch of its own."""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from services import dirty_state, study_solve
from tests.test_results_workbook_views import _network

URL = "/api/results/workbook.xlsx"


def _unsolved(n):
    """The live network a study leaves behind: components, no dispatch, no solve."""
    from services.adequacy.redundancy import _detach_solver_model

    _detach_solver_model(n)
    live = n.copy()
    for comp in live.components:
        for df in comp.dynamic.values():
            df.drop(columns=df.columns, inplace=True, errors="ignore")
    return live


@pytest.fixture
def after_study(install_network, tmp_projects_dir, monkeypatch):
    from services.pypsa_service import PyPSAService

    solved = _network()
    install_network(_unsolved(solved), name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    ctx = PyPSAService.get_active_context()
    ctx.solver_state[study_solve.KEY] = {"network": solved, "objective": 123.0}
    ctx.solver_state["eh_study"] = {
        "status": "done",
        "network_revision": dirty_state.revision(),
    }
    return ctx


def test_workbook_reads_the_study_solve(client, after_study):
    r = client.get(URL, params={"views": "dispatch"})
    assert r.status_code == 200, r.text
    wb = load_workbook(io.BytesIO(r.content))
    assert "Dispatch" in wb.sheetnames
    assert wb["Dispatch"].max_row > 3


def test_status_reads_the_study_solve(client, after_study):
    body = client.get("/api/simulation/status").json()
    assert body["dispatch"] == "fresh" and body["objective"] == 123.0


def test_an_edit_after_the_study_drops_it(client, after_study):
    after_study.solver_state["eh_study"]["network_revision"] = (
        dirty_state.revision() + 1
    )
    assert client.get(URL).status_code == 204
    assert client.get("/api/simulation/status").json()["dispatch"] == "none"


def test_the_live_solve_wins(client, install_network, after_study):
    install_network(_network(), name="P")
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    ctx.solver_state[study_solve.KEY] = {"network": _network(), "objective": 123.0}
    ctx.solver_state["eh_study"] = {
        "status": "done",
        "network_revision": dirty_state.revision(),
    }
    assert study_solve.fallback(ctx) is None


def test_the_chooser_lists_the_study_solve_tabs(client, after_study):
    rows = client.get("/api/results/workbook/views").json()["views"]
    assert any(r["available"] for r in rows), [r["reason"] for r in rows]
