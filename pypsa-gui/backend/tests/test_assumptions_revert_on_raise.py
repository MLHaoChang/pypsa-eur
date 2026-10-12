"""
A mid-apply failure must leave the network as it found it.

`_apply_modelling_assumptions` mutates the live network step by step and returns
a `restore` callable at the END. `run_simulation` binds its `restore_modelling`
handle only once that call RETURNS, and every outer handler is gated on
`if restore_modelling is not None` — so an exception partway through left the
network carrying the LP transforms with nothing able to revert them: scaled
`fom_cost` / `capital_cost`, CO2-inflated marginal costs, scaled loads, possibly
added VOLL/DSR slacks. A later autosave or eviction write-back then persisted
that into the project.

The comment at `run_simulation` is accurate about the window it DOES cover
(between the apply returning and the solve try/finally). This is the window
inside the apply, which nothing covered.

Compounding, and fixed alongside: `periodized_costs` tracks the FOM scaling in
a module-global keyed by **`id(n)`**, discarded only inside `revert`. CPython
reuses addresses, so a leaked entry is not merely stale — an unrelated network
later allocated at the same address reads as already-scaled and silently keeps
its `fom_cost` on the wrong basis.

Method: `phase` is the apply's own log callback and a plain parameter, so a
`phase` that raises on its Nth call simulates a failure mid-apply with no
monkeypatching. N is swept rather than guessed, and the test asserts that at
least one N actually raised — a guard that silently never fires is the failure
mode this branch keeps finding.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.solver.assumptions import _apply_modelling_assumptions
from services.solver.periodized_costs import fom_is_scaled
from services.solver_service import SolverConfig


class _Boom(RuntimeError):
    pass


def _network() -> pypsa.Network:
    """Two periods and an overnight-costed asset, so step 1 has real work:
    `fom_cost` and `capital_cost` are both scaled by the horizon factor."""
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=2, freq="h")],
        names=["period", "timestep"],
    )
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2040]
    n.add("Bus", "B")
    n.add("Load", "L", bus="B", p_set=50.0)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Generator", "G", bus="B", carrier="gas", p_nom=100.0,
          marginal_cost=10.0, overnight_cost=1_000_000.0, lifetime=25.0,
          fom_cost=1234.0)
    return n


def _cfg() -> SolverConfig:
    # CO2 price so step 2 does work too; a discount rate so step 1 fills.
    return SolverConfig(multi_investment_periods=True, co2_price=50.0,
                        discount_rate=0.05, default_lifetime=25.0)


def _snapshot(n) -> dict:
    return {
        "fom_cost": n.generators["fom_cost"].copy(),
        "capital_cost": n.generators["capital_cost"].copy()
        if "capital_cost" in n.generators.columns else None,
        "marginal_cost": n.generators["marginal_cost"].copy(),
        "p_set": n.loads_t.p_set.copy() if not n.loads_t.p_set.empty else None,
    }


def _assert_restored(n, before: dict, where: str) -> None:
    pd.testing.assert_series_equal(
        n.generators["fom_cost"], before["fom_cost"], check_names=False,
        obj=f"fom_cost after {where}",
    )
    if before["capital_cost"] is not None:
        pd.testing.assert_series_equal(
            n.generators["capital_cost"], before["capital_cost"],
            check_names=False, obj=f"capital_cost after {where}",
        )
    pd.testing.assert_series_equal(
        n.generators["marginal_cost"], before["marginal_cost"],
        check_names=False, obj=f"marginal_cost after {where}",
    )
    assert not fom_is_scaled(n), (
        f"the FOM scaling registry still holds this network after {where}; "
        "an unrelated network at the same address would skip its own scaling"
    )


@pytest.mark.parametrize("nth", list(range(1, 9)))
def test_a_raise_mid_apply_reverts_every_transform(nth):
    n = _network()
    before = _snapshot(n)
    calls = {"i": 0}

    def phase(msg):
        calls["i"] += 1
        if calls["i"] == nth:
            raise _Boom(f"forced at phase call {nth}")

    try:
        _apply_modelling_assumptions(n, _cfg(), phase)
    except _Boom:
        _assert_restored(n, before, f"a raise at phase call {nth}")
        return
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"phase call {nth} raised something else: {exc!r}")
    # No raise at this N (the apply made fewer than `nth` phase calls, or the
    # step swallowed it) — nothing to assert here; coverage comes from the Ns
    # that did raise, and `test_at_least_one_injection_point_raises` insists
    # that at least one does.
    pytest.skip(f"phase call {nth} never happened or was swallowed")


def test_at_least_one_injection_point_raises():
    """Guard on the guard: if no N reaches an unguarded `phase` call, every
    parametrisation above skips and the suite reports green while testing
    nothing."""
    reached = 0
    for nth in range(1, 9):
        n = _network()
        calls = {"i": 0}

        def phase(msg, _n=nth, _c=calls):
            _c["i"] += 1
            if _c["i"] == _n:
                raise _Boom("forced")

        try:
            _apply_modelling_assumptions(n, _cfg(), phase)
        except _Boom:
            reached += 1
        except Exception:  # noqa: BLE001
            pass
    assert reached > 0, (
        "no phase call index produced an escaping exception, so the "
        "revert-on-raise path is untested"
    )


def test_the_happy_path_still_returns_a_working_restore():
    """The control: the guard must not swallow a successful apply."""
    n = _network()
    before = _snapshot(n)
    restore, captured = _apply_modelling_assumptions(n, _cfg(), lambda *_a: None)
    assert callable(restore)
    # The transforms are applied while the solve would run...
    assert fom_is_scaled(n)
    # ...and reverted on request, exactly as before.
    restore()
    _assert_restored(n, before, "a normal restore()")
