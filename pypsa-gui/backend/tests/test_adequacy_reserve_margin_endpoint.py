"""
GET /results/reserve_margin (Phase 8 §4): the firm-capacity result surface.

Driven over the REAL HTTP stack with an authenticated TestClient — never by
calling the handler function directly. That distinction has already earned its
keep on this router (a direct call cannot see a missing `HTTPException`
import, and it bypasses auth, CSRF and the per-session project context the
solver worker's state writes have to agree with).

The endpoint serves the PERSISTED stash and never recomputes: the wrapper
measured its peaks against the load-scaling transforms, which the post-solve
restore has since reverted, so a recomputation reads different loads and
drifts from the standard the LP actually enforced. Every test here is written
so that a recomputing implementation fails.
"""
from __future__ import annotations

import copy

import pytest

from routers import simulation as sim_router
from services.solver_service import SolverConfig
from tests.test_adequacy_reserve_margin import (
    FORCED_PEAKER_MW,
    GAS_DERATE,
    LOAD_MW,
    MARGIN,
    REQUIRED_MW,
    _network,
)

URL = "/api/results/reserve_margin"

# A payload the installed network could not possibly produce: if the endpoint
# recomputes anything, these numbers are not what comes back.
SENTINEL = {
    "margin": 0.42,
    "horizon_wide": False,
    "by_period": [{
        "period": "2031",
        "peak_mw": 12345.0,
        "peak_snapshots": ["2031-07-04 17:00:00"],
        "n_peak_hours": 1,
        "required_mw": 17529.9,
        "firm_mw": 17530.0,
        "max_achievable_mw": 99999.0,
        "max_achievable_unbounded": False,
        "margin_achieved": 0.4201,
        "met": True,
        "binding": True,
    }],
    "assets": [{
        "name": "ghost", "period": "2031", "kind": "generator",
        "capacity_mw": 18452.6, "derate": 0.95, "basis": "EFORd",
        "source": "asset", "extendable": False, "energy_limited": False,
        "firm_mw": 17530.0,
    }],
    "derating_bases": {"EFORd": 1},
}


def _run_and_join(client, session_state, timeout: float = 120.0) -> dict:
    resp = client.post("/api/simulation/run")
    assert resp.status_code == 200, resp.text
    state = session_state(client)
    t = state.get("thread")
    assert t is not None, "worker thread was not registered on the session state"
    t.join(timeout=timeout)
    assert not t.is_alive(), f"solve did not finish within {timeout}s"
    return dict(state)


# ── ★ 204 before any solve ────────────────────────────────────────────────

def test_204_before_any_solve(client, install_network):
    """★ Bite: serve 200 with an empty body. 204 is the "never solved" signal
    the panel branches on; a 200 with a falsy body renders as a solve that
    enforced a margin and built nothing."""
    install_network(_network())
    r = client.get(URL)
    assert r.status_code == 204, r.text
    assert r.content == b""


def test_204_when_the_last_solve_set_no_margin(client, install_network,
                                               session_state):
    """★ A margin left in state from a previous solve is a stale standard the
    next plan never met — the same republish-a-stale-report failure QA round 2
    found on the ENS cap."""
    install_network(_network())
    session_state(client)["last_reserve_margin"] = copy.deepcopy(SENTINEL)
    session_state(client)["solver_config"] = SolverConfig()   # no margin
    _run_and_join(client, session_state)
    assert client.get(URL).status_code == 204


# ── ★ the persisted stash, never a recomputation ──────────────────────────

def test_serves_the_persisted_payload_verbatim(client, install_network,
                                               session_state):
    """★ [S6] The peaks are SOLVE-TIME truth. A post-solve recomputation reads
    the restored loads (the scalers are undone by then) and reports a standard
    the LP never enforced."""
    install_network(_network())
    session_state(client)["last_reserve_margin"] = copy.deepcopy(SENTINEL)
    r = client.get(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["margin"] == pytest.approx(0.42)
    assert body["by_period"][0]["peak_mw"] == pytest.approx(12345.0)
    assert body["by_period"][0]["period"] == "2031"
    assert body["assets"][0]["name"] == "ghost"


# ── ★ amendment 6: `inf` is not JSON ──────────────────────────────────────

def _unbounded_payload() -> dict:
    """A REAL payload carrying `inf`, built by the shipped wrapper.

    Recorded finding, and the reason this is not driven through a live solve:
    `_check_extendable_bounds` has refused an infinite `p_nom_max` on any
    extendable since long before this phase ("extendable: need finite
    p_nom_min < p_nom_max"), so today no run that reaches the solver can put
    an `inf` in the stash. The value is still reachable here — a payload
    restored from a `results_state.pkl` written by another build, a caller of
    `reserve_margin_payload` that is not `run_simulation`, or the day that
    bound is relaxed — and amendment 6 makes nulling it this wave's
    obligation, so it is pinned against the real shape rather than a
    hand-written dict that could drift from it.
    """
    from services.adequacy.report import reserve_margin_payload
    from tests.test_adequacy_reserve_margin import _apply, _stash

    n = _network()
    n.generators.at["peaker", "p_nom_max"] = float("inf")
    n, _lines = _apply(n, reserve_margin=MARGIN)
    stash = _stash(n)
    assert stash["periods"]["ALL"]["max_achievable_mw"] == float("inf"), (
        "the fixture no longer produces the unbounded case it exists to pin")
    return reserve_margin_payload(n, stash)


def test_an_unbounded_extendable_is_nulled_not_serialised_as_infinity(
        client, install_network, session_state):
    """
    ★ Amendment 6 — an unbounded `p_nom_max` makes `max_achievable_mw` `inf`.
    Starlette dumps responses with `allow_nan=False`, so an untouched `inf`
    raises INSIDE the response and the panel gets a 500 instead of a report.
    `null` + an explicit flag is the honest rendering: "unbounded" is not a
    number, and clamping it to a large one would invent a ceiling nobody
    entered — one that §3's `max_achievable < required` test could then fire
    on by accident.
    """
    install_network(_network())
    session_state(client)["last_reserve_margin"] = _unbounded_payload()

    r = client.get(URL)
    assert r.status_code == 200, r.text[:400]
    assert "Infinity" not in r.text
    row = r.json()["by_period"][0]
    assert row["max_achievable_mw"] is None
    assert row["max_achievable_unbounded"] is True


# ── ★ end to end: solve → state → endpoint ────────────────────────────────

def test_a_real_solve_serves_the_standard_it_enforced(
        client, install_network, session_state):
    """The whole chain the panel depends on, through the worker that ships:
    the wrapper stashes, `run_simulation` emits into solver state like
    `last_lost_load`, and the endpoint serves it."""
    install_network(_network())
    session_state(client)["solver_config"] = SolverConfig(reserve_margin=MARGIN)
    state = _run_and_join(client, session_state)
    assert state["status"] == "completed", (
        state["status"], state.get("condition"))

    body = client.get(URL).json()
    assert body["margin"] == pytest.approx(MARGIN)
    assert body["horizon_wide"] is True
    row = body["by_period"][0]
    assert row["period"] == "ALL"
    assert row["peak_mw"] == pytest.approx(LOAD_MW)
    assert row["required_mw"] == pytest.approx(REQUIRED_MW)
    assert row["firm_mw"] == pytest.approx(REQUIRED_MW, rel=1e-6)
    assert row["binding"] is True and row["met"] is True
    assert row["n_peak_hours"] == len(row["peak_snapshots"])
    built = {a["name"]: a for a in body["assets"]}
    assert built["peaker"]["capacity_mw"] == pytest.approx(
        FORCED_PEAKER_MW, rel=1e-6)
    assert built["peaker"]["firm_mw"] == pytest.approx(
        GAS_DERATE * FORCED_PEAKER_MW, rel=1e-6)


def test_the_network_attribute_never_outlives_the_solve(
        client, install_network, session_state):
    """`_reserve_margin_targets` is deleted like `_ens_cap_targets`: the
    endpoint reads STATE, so a lingering attribute could only ever be the next
    run's report reading this run's targets."""
    from services.pypsa_service import PyPSAService

    install_network(_network())
    session_state(client)["solver_config"] = SolverConfig(reserve_margin=MARGIN)
    _run_and_join(client, session_state)
    n = PyPSAService.get_network()
    assert getattr(n, "_reserve_margin_targets", None) is None
    assert client.get(URL).status_code == 200
