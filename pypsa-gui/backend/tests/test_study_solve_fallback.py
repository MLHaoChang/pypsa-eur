"""template-e2e 30 (TE2E-30-1): after a Guided study the workbook, the dispatch status and the run state
read the study's own solve when the live network has no dispatch of its own."""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from services import dirty_state, study_solve
from tests.test_results_workbook_views import _capture, _network

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


def _sink(n, literal: float) -> dict:
    """The side-results one solve publishes, every figure a distinct literal."""
    return {
        "last_lost_load": _capture(n, mw=literal, voll=literal * 10),
        "adequacy_report": {"target": {"system": {"achieved_ens_mwh": literal}}},
        "last_solve_settings": {
            "voll_eur_per_mwh": literal * 10,
            "ens_target_applied": True,
        },
    }


@pytest.fixture
def after_study(install_network, tmp_projects_dir, monkeypatch):
    """A live network with no dispatch (an edit cleared the user's own LOPF solve, whose stale
    side-results (111) remain in the live state) and a finished study holding its own solve (222)."""
    from routers import results as R
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    solved = _network()
    install_network(_unsolved(solved), name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    ctx = PyPSAService.get_active_context()
    for key, value in _sink(solved, 111.0).items():
        monkeypatch.setitem(R._state, key, value)
    cfg = SolverConfig()
    cfg.voll = 2220.0
    study_solve.keep(ctx.solver_state, solved, cfg, _sink(solved, 222.0))
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
    assert body["dispatch"] == "fresh"
    assert body["objective"] == after_study.solver_state[study_solve.KEY]["objective"]
    assert body["objective"] is not None


def test_an_edit_after_the_study_drops_it(client, after_study):
    after_study.solver_state["eh_study"]["network_revision"] = (
        dirty_state.revision() + 1
    )
    r = client.get(URL)
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "results_workbook_no_run"
    assert client.get("/api/simulation/status").json()["dispatch"] == "none"


def test_the_live_solve_wins(client, install_network, after_study):
    install_network(_network(), name="P")
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    study_solve.keep(ctx.solver_state, _network(), None, {})
    ctx.solver_state["eh_study"] = {
        "status": "done",
        "network_revision": dirty_state.revision(),
    }
    assert study_solve.fallback(ctx) is None


def test_the_chooser_lists_the_study_solve_tabs(client, after_study):
    rows = client.get("/api/results/workbook/views").json()["views"]
    assert any(r["available"] for r in rows), [r["reason"] for r in rows]


def test_the_workbook_is_built_from_the_study_solves_own_state(
    client, after_study, monkeypatch
):
    """F1: lost load, config, adequacy, settings and result snapshots are the study's (222), never the
    live state's (111) of the solve an edit cleared."""
    import services.results_workbook as RW

    seen = {}

    def spy(inp):
        seen["inp"] = inp
        return b"x"

    monkeypatch.setattr(RW, "build_results_workbook", spy)
    after_study.solver_state[study_solve.KEY]["state"]["lopf_results"] = {
        "generators_t.p": "lopf-222"
    }
    assert client.get(URL).status_code == 200
    inp = seen["inp"]
    assert inp.lost_load["voll_eur_per_mwh"] == 2220.0
    assert inp.cfg.voll == 2220.0
    assert (
        inp.solver_state["adequacy_report"]["target"]["system"]["achieved_ens_mwh"]
        == 222.0
    )
    assert inp.result_df(inp.network, "generators_t", "p") == "lopf-222"
    assert inp.solved_settings()["voll_eur_per_mwh"] == 2220.0
    assert inp.scenarios is None


def test_a_view_reads_the_run_solver_state_it_carries():
    from services.results_workbook import scenarios, views_adequacy

    assert views_adequacy._state(scenarios.Run(name="x", solver_state={"a": 1})) == {
        "a": 1
    }


def test_the_http_route_runs_the_study_as_the_requesting_user(
    client, install_network, monkeypatch
):
    """F2 (g): the run record is bound to the acting user; the route sets it."""
    from services import chat_tools
    import services.adequacy.eh_study_runner as runner

    install_network(_network(), name="P")
    seen = {}

    def stub(body, **kw):
        seen["actor"] = chat_tools.acting_user_id()
        return {"status": "running"}

    monkeypatch.setattr(runner, "start_eh_study", stub)
    chat_tools.set_acting_user(None)  # as in the app: no chat turn set one
    assert client.post("/api/results/eh_study", json={}).status_code == 200
    assert seen["actor"]
    assert chat_tools.acting_user_id() is None  # restored afterwards


def test_the_run_state_reads_the_study_solve(after_study):
    """F2 (i): a study-only solve is the latest run; an edit after it is not."""
    from services import flow_service

    assert flow_service._run_states(solving=False)["optimisation"] == "fresh"
    after_study.solver_state["eh_study"]["network_revision"] = (
        dirty_state.revision() + 1
    )
    assert flow_service._run_states(solving=False)["optimisation"] == "not_run"


@pytest.mark.parametrize("status", ["running", "failed", "aborted", None])
def test_only_a_done_study_is_a_solve_to_read(after_study, status):
    """F2 (a): the held solve exists while later stages run and after a failure."""
    after_study.solver_state["eh_study"]["status"] = status
    assert study_solve.fallback(after_study) is None
    after_study.solver_state["eh_study"]["status"] = "done"
    assert study_solve.fallback(after_study) is not None


def test_no_study_record_no_fallback(after_study):
    after_study.solver_state.pop("eh_study")
    assert study_solve.fallback(after_study) is None


def test_the_chooser_lists_exactly_the_tabs_the_workbook_fills(client, after_study):
    """F2 (j): the listing's availability is the workbook's own, filled and empty tabs alike."""
    rows = client.get("/api/results/workbook/views").json()["views"]
    listed = {r["label"] for r in rows if r["available"]}
    blob = client.get(URL, params={"all": "true"}).content
    about = load_workbook(io.BytesIO(blob))["About"]
    filled = {
        str(r[0])
        for r in about.iter_rows(values_only=True)
        if r[0] and isinstance(r[2], str) and r[2].startswith("sheets:")
    }
    assert filled and listed == filled
    assert any(not r["available"] for r in rows)


def test_keep_holds_an_isolated_copy_with_the_solves_state():
    net = _network()
    state = {"a": 1}
    store: dict = {}
    study_solve.keep(store, net, "cfg", state)
    state["a"] = 2
    net.generators_t.p.iloc[:, :] = -1.0
    held = store[study_solve.KEY]
    assert held["state"] == {"a": 1} and held["cfg"] == "cfg"
    assert held["network"] is not net
    assert (held["network"].generators_t.p.to_numpy() != -1.0).any()


def test_keep_without_a_store_is_a_no_op():
    assert study_solve.keep(None, _network(), "cfg", {"a": 1}) is None


def test_keep_without_state_holds_an_empty_one():
    store: dict = {}
    study_solve.keep(store, _network())
    assert (
        store[study_solve.KEY]["state"] == {} and store[study_solve.KEY]["cfg"] is None
    )


@pytest.mark.parametrize(
    "objective,expected",
    [
        (12.5, 12.5),
        (float("nan"), None),
        (float("inf"), None),
        (None, None),
        ("x", None),
    ],
)
def test_the_held_objective_is_finite_or_none(objective, expected):
    from types import SimpleNamespace

    assert study_solve._objective(SimpleNamespace(objective=objective)) == expected
    assert study_solve._objective(SimpleNamespace()) is None
