"""
UX overhaul S2b: results routes tell "not solved", "solved without this
standard" and "a computed zero" apart (owner decision D3-backend a).

Before, /results/lost_load, /results/adequacy and /results/reserve_margin
answered 204 for several different cases, so the GUI could not say "Not
modelled" without guessing — LostLoadTab told a user who HAD set a VoLL to
"set a VoLL". Now 204 means only "nothing solved"; a solve that ran without
the standard answers 200 `configured: false` (lost load: `voll_applied:
false`, totals null).

Every value comes from the SOLVE-TIME settings (`last_solve_settings`),
never the current Solver Settings draft — each test that could tell the two
apart edits the draft after the solve.

Driven over the real HTTP stack, like test_adequacy_reserve_margin_endpoint.
"""
from __future__ import annotations

import math

import pandas as pd
import pypsa

from services.solver_service import SolverConfig
from tests.test_adequacy_reserve_margin import _network


def _run_and_join(client, session_state, timeout: float = 120.0) -> dict:
    resp = client.post("/api/simulation/run")
    assert resp.status_code == 200, resp.text
    state = session_state(client)
    t = state.get("thread")
    assert t is not None
    t.join(timeout=timeout)
    assert not t.is_alive(), f"solve did not finish within {timeout}s"
    assert state.get("condition") == "optimal", (state.get("status"), state.get("condition"))
    return state


def _solve(client, install_network, session_state, **cfg_kw) -> dict:
    install_network(_network(peaker=False))
    session_state(client)["solver_config"] = SolverConfig(**cfg_kw)
    return _run_and_join(client, session_state)


# ── the solve-time stash ──────────────────────────────────────────────────

def test_a_successful_solve_records_its_settings(client, install_network, session_state):
    state = _solve(client, install_network, session_state, voll=3000.0)
    s = state.get("last_solve_settings")
    assert s is not None
    assert s["voll_eur_per_mwh"] == 3000.0
    assert s["ens_target_applied"] is False
    assert s["reserve_margin_applied"] is False


# ── /results/lost_load ────────────────────────────────────────────────────

def test_lost_load_204_before_any_solve(client, install_network):
    install_network(_network(peaker=False))
    assert client.get("/api/results/lost_load").status_code == 204


def test_lost_load_says_voll_was_off_with_null_totals(client, install_network, session_state):
    _solve(client, install_network, session_state, voll=0.0)
    # The draft changes after the solve; the answer must not.
    session_state(client)["solver_config"] = SolverConfig(voll=5000.0)
    r = client.get("/api/results/lost_load")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voll_applied"] is False
    assert body["voll_eur_per_mwh"] is None
    # Not modelled is never a computed zero.
    assert body["total_mwh"] is None
    assert body["total_cost_eur"] is None


def test_lost_load_with_voll_and_nothing_shed_is_a_computed_zero(client, install_network, session_state):
    _solve(client, install_network, session_state, voll=3000.0)
    session_state(client)["solver_config"] = SolverConfig(voll=0.0)
    r = client.get("/api/results/lost_load")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voll_applied"] is True
    assert body["voll_eur_per_mwh"] == 3000.0
    assert body["total_mwh"] == 0
    assert body["total_cost_eur"] == 0


# ── /results/adequacy ─────────────────────────────────────────────────────

def test_adequacy_solved_without_a_target_is_configured_false(client, install_network, session_state):
    _solve(client, install_network, session_state, voll=3000.0)
    r = client.get("/api/results/adequacy")
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False


# ── /results/reserve_margin ───────────────────────────────────────────────

def test_reserve_margin_solved_without_a_margin_is_configured_false(client, install_network, session_state):
    _solve(client, install_network, session_state)
    session_state(client)["solver_config"] = SolverConfig(reserve_margin=0.15)
    r = client.get("/api/results/reserve_margin")
    assert r.status_code == 200, r.text
    assert r.json() == {"configured": False}


# ── legacy projects: the stash is absent, the existing stashes decide ─────

def test_a_legacy_solve_without_the_stash_infers_the_settings(client, install_network, session_state):
    _solve(client, install_network, session_state, voll=0.0)
    session_state(client)["last_solve_settings"] = None   # a pre-S2b results_state.pkl
    r = client.get("/api/results/adequacy")
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False
    assert client.get("/api/results/reserve_margin").json() == {"configured": False}


# ── emissions: `binding` is tri-state ─────────────────────────────────────

def test_cap_binding_is_null_when_the_cap_was_not_in_the_last_lp():
    from services.results.emissions import _cap_binding
    assert _cap_binding(mu=float("nan"), slack=0.0) is None
    assert _cap_binding(mu=None, slack=0.0) is None
    assert _cap_binding(mu=-12.5, slack=0.0) is True
    assert _cap_binding(mu=0.0, slack=1000.0, cap_value=5000.0) is False
