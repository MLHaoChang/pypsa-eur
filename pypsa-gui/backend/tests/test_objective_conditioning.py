"""
Numerical objective-conditioning analyzer (services/solver_service).

`_objective_conditioning` summarises the magnitude spread of the LP's objective
cost coefficients and recommends a power-of-10 scale — used by the always-on
`[NUMERICS]` diagnostic and the opt-in `auto_objective_scale`. These tests pin
the recommend-only-when-poor behaviour (so auto-scale is a no-op for healthy
models) and the integration that an auto-scaled solve still reports € correctly.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from tests.conftest import build_network
from routers import simulation as sim_router
from services.solver_service import SolverConfig, _objective_conditioning


def _net(snapshots: int = 4) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=snapshots, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    return n


def test_healthy_network_recommends_no_scale():
    # marginal_cost ~50, capital_cost ~1e5 → geomean ~2e3, inside the healthy
    # band → recommended_scale is exactly 1.0 (auto-scale would be a no-op).
    n = _net()
    n.add("Generator", "gas", bus="B1", carrier="gas",
          p_nom=200.0, marginal_cost=50.0)
    n.add("Generator", "wind", bus="B1", carrier="wind",
          p_nom_extendable=True, p_nom_max=500.0, capital_cost=1.0e5)
    cond = _objective_conditioning(n)
    assert cond is not None
    assert cond["recommended_scale"] == 1.0, cond
    assert cond["count"] >= 2


def test_ill_conditioned_high_magnitude_recommends_downscale():
    # All coefficients ~1e9 → geomean far above the healthy band → recommend a
    # sub-1 power of 10 (clamped at 1e-6) to pull magnitudes back down.
    n = _net()
    for i in range(3):
        n.add("Generator", f"g{i}", bus="B1", carrier="gas",
              p_nom_extendable=True, p_nom_max=500.0, capital_cost=1.0e9)
    cond = _objective_conditioning(n)
    assert cond is not None
    assert cond["recommended_scale"] < 1.0, cond
    assert cond["geomean"] > 1e4


def test_insufficient_coefficients_returns_none():
    # No costed components → fewer than two nonzero coefficients → None.
    n = _net()
    n.add("Generator", "free", bus="B1", marginal_cost=0.0, p_nom=100.0)
    assert _objective_conditioning(n) is None


def test_card_shape():
    n = _net()
    n.add("Generator", "gas", bus="B1", p_nom=200.0, marginal_cost=50.0)
    n.add("Generator", "wind", bus="B1", p_nom_extendable=True,
          p_nom_max=500.0, capital_cost=1.0e5)
    cond = _objective_conditioning(n)
    assert set(cond.keys()) == {
        "count", "min", "max", "geomean", "spread_log10", "recommended_scale"
    }


def test_auto_scale_run_keeps_objective_in_euros(client, install_network, session_state):
    # A feasible solve with auto_objective_scale=True must still report the
    # objective in € (the post-solve rescale divides the scale back out). We
    # don't assert a specific value (scale is data-dependent) — only that the
    # run completes cleanly and the objective is finite, proving the auto path
    # threads through the existing wrap + rescale machinery without corruption.
    install_network(build_network())
    # Configure the CLIENT'S context (Step 0b): `sim_router._state` read from
    # the test thread is the process foreground, which the request never sees.
    client.get("/api/network/meta")  # force the session's context to resolve
    session_state(client)["solver_config"] = SolverConfig(auto_objective_scale=True)

    resp = client.post("/api/simulation/run")
    assert resp.status_code == 200, resp.text
    state = session_state(client)
    t = state.get("thread")
    assert t is not None
    t.join(timeout=90)
    assert not t.is_alive()
    s = dict(state)
    assert s["status"] == "completed", f"{s['status']} / {s['condition']}"
    assert isinstance(s["objective"], (int, float))
    assert s["objective"] == s["objective"]  # not NaN
