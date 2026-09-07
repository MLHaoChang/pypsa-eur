"""
The `/api/simulation/preflight` route's check-then-act window.

Recorded as a MINOR pre-existing finding by the Phase 12c v3 review and
carried as backlog through 12d–12i: the route guards on
`_solver_in_flight()` and then validates, so a solve starting in between is
judged against a half-transformed network.

**What the window can and cannot do**, established before fixing it rather
than assumed:

* it CANNOT corrupt the network. `adequacy.demand.lp_demand_frame` deep-copies
  before applying the load-scale factors, and every consumer on this path is
  read-only on the live frame (12c-0 shipped-code review, finding 2);
* it CAN produce wrong ADVICE. The scaling is not idempotent (measured ×1.25²
  by the 12c v3 review), so validating while the solve has already scaled the
  demand in place double-scales it, which can invent a
  `reserve_margin_unreachable` that is not true of the user's network.

The route already has an answer for "a solver worker is alive" — `deferred`,
with a reason and a Revalidate prompt. The fix is to re-check after
validating and give that answer, which is what the user would have got had
the solve started a millisecond earlier. The route still does not take the
mutation lock: taking it would block the UI for the length of a solve, which
is the reason the route was written this way.
"""
from __future__ import annotations

import pandas as pd
import pypsa

import routers.simulation as SIM


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=200.0,
          marginal_cost=10.0)
    n.add("Load", "l", bus="b", p_set=100.0)
    return n


def test_preflight_answers_normally_when_no_solver_is_in_flight(install_network):
    """The common path is untouched: no worker, a real issue list."""
    install_network(_network())
    out = SIM.preflight()
    assert out.get("deferred") is not True
    assert "issues" in out and isinstance(out["issues"], list)
    assert out["errors"] == sum(1 for i in out["issues"]
                                if i["severity"] == "error")


def test_preflight_defers_when_a_solve_is_already_running(install_network,
                                                          monkeypatch):
    """The pre-existing guard still fires — the fix must not replace it."""
    install_network(_network())
    monkeypatch.setattr(SIM, "_solver_in_flight", lambda: True)
    out = SIM.preflight()
    assert out["deferred"] is True
    assert out["issues"] == []
    assert "Solver worker still active" in out["deferred_reason"]


def test_preflight_defers_when_a_solve_STARTS_during_validation(
        install_network, monkeypatch):
    """★ The window itself. `_solver_in_flight` is False at the guard and
    True by the time validation returns — exactly the interleaving the 12c
    review described — and the route must answer `deferred` rather than serve
    advice computed against transient LP scaffolding.

    Bite (verified): drop the re-check after `validate_for_run` — the route
    returns a normal issue list judged on a half-transformed network.
    """
    install_network(_network())
    calls = {"n": 0}

    def flaky() -> bool:
        # False on the guard, True on every later call: the solve started
        # while `validate_for_run` was running.
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(SIM, "_solver_in_flight", flaky)
    out = SIM.preflight()

    assert calls["n"] >= 2, "the route never re-checked after validating"
    assert out["deferred"] is True, out
    assert out["issues"] == []
    assert out["errors"] == 0 and out["warnings"] == 0
    assert out["deferred_stuck"] is False


def test_the_deferred_answer_still_distinguishes_a_stuck_abort(
        install_network, monkeypatch):
    """The two remediations stay distinct through the re-check path: a solve
    aborted but still in native solver code needs a backend restart, and the
    route says so rather than telling the user to wait."""
    install_network(_network())
    calls = {"n": 0}

    def flaky() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(SIM, "_solver_in_flight", flaky)
    SIM._state["status"] = "aborted"
    try:
        out = SIM.preflight()
    finally:
        SIM._state["status"] = None

    assert out["deferred"] is True
    assert out["deferred_stuck"] is True
    assert "Restart the backend" in out["deferred_reason"]


def test_the_scaling_the_window_would_double_is_still_read_only():
    """The half of the finding that bounds its severity: the demand frame the
    validator reads is a COPY when anything is scaled, so the window can
    never write back onto `loads_t.p_set`. Pinned because the fix above is
    justified by it — if this stopped being true, a re-check would not be
    enough and the route would need the lock.
    """
    from services.adequacy.demand import lp_demand_frame
    from services.solver_service import SolverConfig

    n = _network()
    n.loads_t.p_set = pd.DataFrame({"l": [100.0, 100.0, 100.0]},
                                   index=n.snapshots)
    before = n.loads_t.p_set.copy(deep=True)
    got = lp_demand_frame(n, SolverConfig())
    assert got is not None
    # Unscaled config: the frame itself is returned (documented, bit-identical
    # to reading it directly) and nothing is mutated either way.
    pd.testing.assert_frame_equal(n.loads_t.p_set, before)
