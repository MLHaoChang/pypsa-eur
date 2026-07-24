"""
End-to-end solve through POST /api/simulation/run.

This is the FIRST test that drives a real HiGHS solve through the API worker +
`run_simulation` (the existing suite solves via `n.optimize` directly and never
exercised the run path). It is the safety net for multi-project Phase A2, which
rewires `run_simulation` to write its side-results (`last_lost_load`,
AC-PF `ac_pf_*`) through an INJECTED `state_update` callable instead of importing
the module-global `routers.simulation._state`. The VOLL test below specifically
exercises that injected write — without the rewire intact, `last_lost_load`
would not land in `_state` and the assertion fails.
"""
from __future__ import annotations

from conftest import build_network
from routers import simulation as sim_router
from services.solver_service import SolverConfig


def _run_and_join(client, timeout: float = 90.0) -> dict:
    """POST /run, wait for the worker thread to finish, return a state snapshot."""
    resp = client.post("/api/simulation/run")
    assert resp.status_code == 200, resp.text
    t = sim_router._state.get("thread")
    assert t is not None, "worker thread was not registered on _state"
    t.join(timeout=timeout)
    assert not t.is_alive(), f"solve did not finish within {timeout}s"
    return sim_router._state_snapshot()


def test_run_lopf_through_api_populates_state(client, install_network):
    # Feasible single-bus LP: load 100 MW, dispatchable cap ~230 MW.
    install_network(build_network())  # solver_config defaults: highs / lopf / voll=0
    s = _run_and_join(client)
    assert s["status"] == "completed", f"status={s['status']} condition={s['condition']}"
    assert isinstance(s["objective"], (int, float)), f"objective={s['objective']!r}"
    assert s["objective"] == s["objective"]  # not NaN


def test_run_voll_through_api_populates_last_lost_load(client, install_network):
    # Force load shedding: demand (1000 MW) far exceeds dispatchable capacity
    # (gas 200 + solar ~30), and voll>0 adds per-bus slack generators. The LP
    # sheds via the slack, `run_simulation` captures it, and (Phase A2) writes it
    # to the injected state via state_update(last_lost_load=…).
    n = build_network()
    n.loads.loc["L1", "p_set"] = 1000.0
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig(voll=1000.0)

    s = _run_and_join(client)
    assert s["status"] == "completed", f"status={s['status']} condition={s['condition']}"
    lost = s["last_lost_load"]
    assert lost is not None, "VOLL run should have captured last_lost_load into _state"
    assert lost.get("lost_load_total_mwh", 0) > 0, f"expected shed energy, got {lost!r}"


def test_run_infeasible_surfaces_failure_card(client, install_network):
    # Demand (1000 MW) far exceeds firm capacity (gas 200 + solar ~30) with no
    # extendable assets and voll=0 (no lost-load slack) → infeasible LP. The
    # terminal taxonomy must populate `last_failure`, and /status must echo it.
    n = build_network()
    n.loads.loc["L1", "p_set"] = 1000.0
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig(voll=0.0)

    s = _run_and_join(client)
    assert s["status"] != "completed", f"expected failure, got {s['status']}/{s['condition']}"
    fail = s["last_failure"]
    assert fail is not None, "infeasible solve should populate last_failure"
    # Exact category depends on whether HiGHS presolve reports infeasible vs
    # infeasible_or_unbounded; either (or a generic solver_error) is acceptable.
    assert fail["category"] in ("infeasible", "infeasible_or_unbounded", "solver_error"), fail
    assert fail["title"] and fail["hint"]

    # /status echoes the same card so a late poll / reload can render it.
    st = client.get("/api/simulation/status").json()
    assert st["failure"] is not None
    assert st["failure"]["category"] == fail["category"]


def test_run_infeasible_emits_diagnosis(client, install_network):
    # An infeasible solve must emit the [INFEASIBLE] "why" hints into the log
    # (here: peak demand 1000 MW >> firm capacity ~230 MW, voll=0).
    n = build_network()
    n.loads.loc["L1", "p_set"] = 1000.0
    install_network(n)
    sim_router._state["solver_config"] = SolverConfig(voll=0.0)
    s = _run_and_join(client)
    assert s["status"] != "completed", f"{s['status']}/{s['condition']}"
    lines = client.get("/api/simulation/log_history").json()["lines"]
    assert any("[INFEASIBLE]" in ln for ln in lines), "no infeasibility diagnosis emitted"
    assert any("Peak demand" in ln for ln in lines), "expected the capacity-shortfall hint"


def test_run_success_clears_failure_card(client, install_network):
    # A feasible run must leave last_failure None (clears any stale card).
    install_network(build_network())
    s = _run_and_join(client)
    assert s["status"] == "completed", f"status={s['status']} condition={s['condition']}"
    assert s["last_failure"] is None, f"success must clear last_failure, got {s['last_failure']!r}"


def test_status_dispatch_freshness_reflects_edits(client, install_network):
    # Before any solve: no dispatch.
    install_network(build_network())
    assert client.get("/api/simulation/status").json()["dispatch"] == "none"

    # After a successful solve: dispatch is fresh.
    s = _run_and_join(client)
    assert s["status"] == "completed", f"{s['status']}/{s['condition']}"
    assert client.get("/api/simulation/status").json()["dispatch"] == "fresh"

    # A topology edit clears the solver dispatch (undo middleware) → none. This
    # is the transition the StatusBar watches to flag "results cleared, re-run".
    r = client.post("/api/network/buses", json={"name": "B_extra"})
    assert r.status_code == 201, r.text
    assert client.get("/api/simulation/status").json()["dispatch"] == "none"


def test_run_ac_pf_after_lopf_populates_ac_pf_state(client, install_network):
    # Enable the LOPF→AC-PF auto-chain so run_simulation's SECOND injected write,
    # `_emit_state(**ac_pf_out)`, is exercised (the sibling of the lost-load
    # write above; without the A2 rewire intact the AC-PF results wouldn't reach
    # `_state`). Single-bus PF converges trivially.
    install_network(build_network())
    sim_router._state["solver_config"] = SolverConfig(run_ac_pf_after_lopf=True)
    s = _run_and_join(client)
    assert s["status"] == "completed", f"status={s['status']} condition={s['condition']}"
    assert (s["ac_pf_results"] is not None or s["ac_pf_convergence_list"] is not None), (
        "AC-PF auto-chain should have routed ac_pf_* into _state via the injected sink"
    )
