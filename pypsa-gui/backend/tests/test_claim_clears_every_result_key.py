"""
A new solve claim must wipe EVERY per-solve result, in both claims.

`routers/simulation.py::run` and `services/solve_queue.py::_run_solve_job`
each listed the result keys to clear by hand, and they drifted: the queue
claim was missing all five `eh_*` keys. Those keys are in
`RESULT_STATE_KEYS`, so they are persisted to `results_state.pkl` and
re-hydrated — a queued re-solve therefore saved the PREVIOUS plan's
energy-hub tables beside the new dispatch, and `/results/eh_*` served them.

The comment above `RESULT_STATE_KEYS` already declared the tuple the single
source of truth "rather than maintaining a parallel copy that can drift". Two
parallel copies had grown anyway, because nothing made deriving them easier
than typing them. Both claims now splat `cleared_result_state()`.
"""
from __future__ import annotations

import inspect

from services.project_context import RESULT_STATE_KEYS, cleared_result_state


def test_the_wipe_covers_every_persisted_result_key():
    assert set(cleared_result_state()) == set(RESULT_STATE_KEYS)
    assert all(v is None for v in cleared_result_state().values())


def test_the_energy_hub_keys_are_in_the_wipe():
    # Named explicitly: these are the five that were missing, and a future
    # edit that drops them from the tuple should fail here, not silently.
    wipe = cleared_result_state()
    for key in ("eh_reference_design_report", "eh_redundancy_comparison",
                "eh_lever_comparison", "eh_dtc_stress", "eh_dtc_planning"):
        assert key in wipe, f"{key} is no longer cleared on a new claim"


def test_neither_claim_hand_lists_the_result_keys_any_more():
    """
    The guard that keeps them from drifting again. Both claim sites must
    DERIVE the wipe; a hand-typed `eh_dtc_stress=None` in either one is how
    the two came apart in the first place.
    """
    import routers.simulation as sim
    import services.solve_queue as sq

    for mod in (sim, sq):
        src = inspect.getsource(mod)
        assert "cleared_result_state()" in src, (
            f"{mod.__name__} no longer derives its claim wipe"
        )
        # A hand-typed reset of a result key next to the derived splat means
        # someone started a third parallel copy.
        for key in ("eh_dtc_stress", "eh_lever_comparison"):
            assert f"{key}=None" not in src, (
                f"{mod.__name__} hand-lists {key} again"
            )
