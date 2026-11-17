"""
homer-parity 03b: the optimal system type map routes, worker and throw-away
forks, with the 03a tests' fake `run_simulation` (the queue, the forks, the
campaign, the study slot and the reads are real). Gate letters are GK B's
plan conditions for 03a, which the map keeps (G1 stale, G6 statuses, G7 forks
deleted, G8 one study slot, G9 schema version).
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.study_s4_support import enable_studies
from tests.test_homer_supply_architecture_routes import (
    ArchSolver,
    _project,
    _var_rows,
)


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def session_local(_auth_db):
    return _auth_db[1]


@pytest.fixture
def arch_solver(monkeypatch):
    from services import solver_service

    solver = ArchSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _url(name, sid, tail=""):
    return f"/api/projects/{name}/studies/{sid}/system-type-map{tail}"


def _body(**kw):
    body = {
        "x": {"variable": "genset_fuel_price", "values": [0.5, 1.0, 2.0]},
        "y": {"variable": "load", "values": [1.0, 2.0, 3.0]},
        "architectures": ["grid", "pv"],
    }
    body.update(kw)
    return body


def _wait(client, name, sid, timeout=90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(_url(name, sid))
        if r.status_code == 200 and r.json().get("status") != "running":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("the map did not finish")


def test_never_run_is_404(client, studies_on, install_network):
    sid = _project(client, install_network, "tm-never")
    r = client.get(_url("tm-never", sid))
    assert r.status_code == 404
    assert r.json()["detail"]["error_kind"] == "system_type_map_never_run"
    assert client.post(_url("tm-never", sid, "/abort")).status_code == 404


def test_a_three_by_three_map_names_each_winner_and_its_gap(
    client, studies_on, install_network, arch_solver, session_local
):
    sid = _project(client, install_network, "tm-all")
    r = client.post(_url("tm-all", sid), json=_body())
    assert r.status_code == 202, r.text
    assert (
        r.json()["solves_estimated"] == 18
        and r.json()["axes"][0]["label"] == "Genset fuel price"
    )
    rec = _wait(client, "tm-all", sid)
    assert rec["status"] == "done", rec
    assert rec["schema_version"] == 1 and rec["stale"] is False
    assert [(c["i"], c["j"]) for c in rec["cells"]] == [
        (i, j) for i in range(3) for j in range(3)
    ]
    assert len(arch_solver.calls) == 18 and rec["solves_charged"] == 18
    for c in rec["cells"]:
        by = {row["architecture_id"]: row for row in c["rows"]}
        # the fake costs 100 per asset: PV alone (1 asset) beats the grid supply (2)
        assert c["winner_architecture_id"] == "pv" and c["winner_label"] == "PV"
        assert c["runner_up_gap_eur_per_year"] == pytest.approx(
            by["grid"]["total_cost_eur_per_year"] - by["pv"]["total_cost_eur_per_year"]
        )
        assert c["runner_up_gap_eur_per_year"] > 0
    assert [a["values"] for a in rec["axes"]] == [[0.5, 1.0, 2.0], [1.0, 2.0, 3.0]]
    assert _var_rows(session_local, "tm-all") == [] and rec["forks_left"] == []


def test_each_cell_reaches_its_fork_with_both_multipliers(
    client, studies_on, install_network, monkeypatch
):
    from services import solver_service

    seen = []

    class Recording(ArchSolver):
        def __call__(self, config, n, lock, stop_event, log_queue, state_update=None):
            if "genset" in n.generators.index:
                seen.append(
                    (
                        float(n.generators.at["genset", "marginal_cost"]),
                        float(n.loads.p_set.iloc[0]),
                    )
                )
            return super().__call__(
                config, n, lock, stop_event, log_queue, state_update
            )

    monkeypatch.setattr(solver_service, "run_simulation", Recording())
    sid = _project(client, install_network, "tm-mult")
    body = _body(
        architectures=["pv+genset"],
        x={"variable": "genset_fuel_price", "values": [0.5, 2.0]},
        y={"variable": "load", "values": [1.0, 3.0]},
    )
    assert client.post(_url("tm-mult", sid), json=body).status_code == 202
    assert _wait(client, "tm-mult", sid)["status"] == "done"
    assert sorted(seen) == [(125.0, 1.0), (125.0, 3.0), (500.0, 1.0), (500.0, 3.0)]


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"x": {"variable": "wind_resource"}}, "variable_not_in_network"),
        ({"x": {"variable": "pv_capital_cost"}}, "variable_not_in_network"),
        ({"x": {"variable": "nonsense"}}, "unknown_variable"),
        ({"y": {"variable": "genset_fuel_price"}}, "axes_not_different"),
        (
            {
                "x": {"variable": "load", "values": [1.0]},
                "y": {"variable": "genset_fuel_price"},
            },
            "axis_values_invalid",
        ),
        ({"architectures": ["wind"]}, "unknown_architecture"),
    ],
)
def test_a_request_the_map_cannot_compute_is_refused_before_anything_starts(
    client, studies_on, install_network, arch_solver, patch, code
):
    sid = _project(client, install_network, "tm-bad")
    r = client.post(_url("tm-bad", sid), json=_body(**patch))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == code
    assert arch_solver.calls == []


def test_a_map_over_the_campaign_limit_names_the_solve_count(
    client, studies_on, install_network, arch_solver
):
    sid = _project(client, install_network, "tm-big")
    five = [0.5, 0.75, 1.0, 1.25, 1.5]
    r = client.post(
        _url("tm-big", sid),
        json=_body(
            architectures=None,
            x={"variable": "genset_fuel_price", "values": five},
            y={"variable": "load", "values": five},
        ),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert (
        detail["error_kind"] == "campaign_budget_invalid"
        and "350 solves" in detail["message"]
    )
    assert arch_solver.calls == []


def test_a_small_budget_is_refused(client, studies_on, install_network, arch_solver):
    sid = _project(client, install_network, "tm-budget")
    r = client.post(_url("tm-budget", sid), json=_body(budget_solves=5))
    assert r.status_code == 409
    assert r.json()["detail"]["error_kind"] == "campaign_budget_exhausted"
    assert arch_solver.calls == []


def test_an_edited_base_reads_stale_and_an_unknown_schema_is_not_evaluated(
    client, studies_on, install_network, arch_solver, project_storage_dir
):
    import pypsa

    from services.pypsa_service import PyPSAService
    from services.study import store

    sid = _project(client, install_network, "tm-stale")
    client.post(
        _url("tm-stale", sid),
        json=_body(
            architectures=["pv"],
            x={"variable": "load", "values": [1, 2]},
            y={"variable": "genset_fuel_price", "values": [1, 2]},
        ),
    )
    rec = _wait(client, "tm-stale", sid)
    assert rec["stale"] is False
    path = project_storage_dir("tm-stale") / "network.nc"
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    n.generators.loc["genset", "marginal_cost"] = 999.0
    n.export_to_netcdf(path)
    assert client.get(_url("tm-stale", sid)).json()["stale"] is True
    store.save_aux(
        project_storage_dir("tm-stale"),
        sid,
        "system_type_map",
        {**rec, "schema_version": 99},
    )
    got = client.get(_url("tm-stale", sid)).json()
    assert (
        got["status"] == "not_evaluated" and got["reason"] == "unknown_schema_version"
    )


def test_abort_leaves_the_cells_not_reached_and_no_fork(
    client, studies_on, install_network, monkeypatch, session_local
):
    from services import solver_service

    gate = threading.Event()
    solver = ArchSolver(gate=gate)
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    sid = _project(client, install_network, "tm-abort")
    assert client.post(_url("tm-abort", sid), json=_body()).status_code == 202
    deadline = time.time() + 30
    while not solver.calls and time.time() < deadline:
        time.sleep(0.05)
    again = client.post(_url("tm-abort", sid), json=_body())
    assert (
        again.status_code == 409
        and again.json()["detail"]["error_kind"] == "study_running"
    )
    live = client.get(_url("tm-abort", sid)).json()
    assert live["status"] == "running" and live["kind"] == "system_type_map"
    assert client.post(_url("tm-abort", sid, "/abort")).json()["aborting"] is True
    gate.set()
    rec = _wait(client, "tm-abort", sid)
    assert rec["status"] == "aborted"
    assert any(r["status"] == "not_reached" for c in rec["cells"] for r in c["rows"])
    assert any(c["winner_architecture_id"] is None for c in rec["cells"])
    assert _var_rows(session_local, "tm-abort") == [] and rec["forks_left"] == []


def test_a_worker_exception_leaves_no_fork(
    client, studies_on, install_network, arch_solver, monkeypatch, session_local
):
    from services.study import system_type_map as M

    def boom(*a, **k):
        raise RuntimeError("mid-run")

    monkeypatch.setattr(M, "cell_of", boom)
    sid = _project(client, install_network, "tm-boom")
    assert client.post(_url("tm-boom", sid), json=_body()).status_code == 202
    rec = _wait(client, "tm-boom", sid)
    assert rec["status"] == "failed" and rec["error"] == "RuntimeError"
    assert _var_rows(session_local, "tm-boom") == []


def test_chat_tools_are_registered_with_their_tiers():
    from harness import catalogue, job_holds
    from services import chat_tools

    for name, tier in (
        ("compare_system_types", "execution_long_running"),
        ("get_system_type_map", "read"),
    ):
        assert name in chat_tools.DISPATCHERS and catalogue.TOOL_ROUTES[name]
        assert catalogue.safety_tier_for(name) == tier
    assert job_holds.JOB_KINDS["compare_system_types"] == job_holds.INPROC_SOLVER


def test_chat_tools_start_and_read_the_map(
    client, studies_on, install_network, arch_solver
):
    from fastapi import HTTPException

    from services import chat_tools

    sid = _project(client, install_network, "tm-chat")
    with pytest.raises(HTTPException) as exc:
        chat_tools.compare_system_types("tm-chat", sid, x_variable="load")
    assert exc.value.detail["error_kind"] == "axis_missing"
    out = chat_tools.compare_system_types(
        "tm-chat",
        sid,
        x_variable="genset_fuel_price",
        y_variable="load",
        x_values=[0.5, 1.0],
        y_values=[1.0, 2.0],
        architectures=["grid", "pv"],
    )
    assert out["action"] == "start" and out["solves_estimated"] == 8
    deadline = time.time() + 60
    while time.time() < deadline:
        got = chat_tools.get_system_type_map("tm-chat", sid)
        if "running" not in got:
            break
        time.sleep(0.1)
    assert got["status"] == "done" and got["stale"] is False and len(got["cells"]) == 4
    assert {c["winner_label"] for c in got["cells"]} == {"PV"}
    assert chat_tools.compare_system_types("tm-chat", sid, action="abort") == {
        "action": "abort",
        "status": "done",
        "aborting": False,
    }


def test_a_run_leaves_the_saved_project_as_it_was(
    client,
    studies_on,
    install_network,
    arch_solver,
    registry_key_for,
    project_storage_dir,
):
    """V6: the cells are private copies. The saved project's input fingerprint,
    network revision, undo stack and unsaved-edits flag are unchanged after a
    run, read from the session's own context."""
    from services import dirty_state
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _network_input_fingerprint

    sid = _project(client, install_network, "tm-pin")
    ctx = PyPSAService.get_context(registry_key_for("tm-pin"))
    assert ctx is not None
    before = (
        _network_input_fingerprint(ctx),
        ctx.network_revision,
        len(ctx.undo.stack),
        dirty_state.is_dirty(ctx),
        float(ctx.network.generators.at["genset", "marginal_cost"]),
        float(ctx.network.loads.p_set.iloc[0]),
    )
    assert client.post(_url("tm-pin", sid), json=_body()).status_code == 202
    assert _wait(client, "tm-pin", sid)["status"] == "done"
    ctx = PyPSAService.get_context(registry_key_for("tm-pin"))
    after = (
        _network_input_fingerprint(ctx),
        ctx.network_revision,
        len(ctx.undo.stack),
        dirty_state.is_dirty(ctx),
        float(ctx.network.generators.at["genset", "marginal_cost"]),
        float(ctx.network.loads.p_set.iloc[0]),
    )
    assert after == before


@pytest.mark.parametrize("raw", ["Infinity", "NaN", "11", "0", "-1"])
def test_an_axis_value_that_is_not_a_finite_multiplier_up_to_ten_is_refused(
    client, studies_on, install_network, arch_solver, raw
):
    """V1: pydantic accepts inf and nan unless told not to."""
    sid = _project(client, install_network, "tm-val")
    payload = (
        '{"x": {"variable": "load", "values": [1, ' + raw + "]}, "
        '"y": {"variable": "genset_fuel_price"}}'
    )
    r = client.post(
        _url("tm-val", sid),
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422, r.text
    assert arch_solver.calls == []


def test_the_solves_charged_are_the_solves_run(
    client, studies_on, install_network, arch_solver
):
    """V5: no probe or restore re-solve; charged == estimated == run."""
    sid = _project(client, install_network, "tm-spy")
    r = client.post(
        _url("tm-spy", sid),
        json=_body(
            x={"variable": "load", "values": [1, 2]},
            y={"variable": "genset_fuel_price", "values": [1, 2]},
        ),
    )
    rec = _wait(client, "tm-spy", sid)
    assert (
        r.json()["solves_estimated"]
        == rec["solves_charged"]
        == len(arch_solver.calls)
        == 8
    )


def test_an_unreadable_record_reads_not_evaluated(
    client, studies_on, install_network, project_storage_dir
):
    """V7 (G9): a record that is not a JSON object, or not JSON, never 500s."""
    from services.study import store

    sid = _project(client, install_network, "tm-junk")
    base_dir = project_storage_dir("tm-junk")
    store.save_aux(base_dir, sid, "system_type_map", [])
    for _ in range(2):
        r = client.get(_url("tm-junk", sid))
        assert r.status_code == 200, r.text
        assert (
            r.json()["status"] == "not_evaluated"
            and r.json()["reason"] == "unreadable_record"
        )
        a = client.post(_url("tm-junk", sid, "/abort"))
        assert a.status_code == 200 and a.json() == {
            "status": "not_evaluated",
            "aborting": False,
        }
        store._aux_path(base_dir, sid, "system_type_map").write_text("{not json")
