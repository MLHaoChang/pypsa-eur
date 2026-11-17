"""AC PF seeding for phase-shifting transformers (owner decision DGS-SEED-1).

A PowerFactory Dyn11 transformer imports as ``phase_shift=30`` (30° x clock
number, as pandapower maps it). The fixture below, two Dyn11 transformers in a
110/20/20 kV chain, diverges from a flat start and converges once ``lpf()``
seeds ``pf(use_seed=True)``. Networks without an active shifting transformer
keep the caller's flat-start ``pf`` call exactly.
"""

from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.solver import pf_seed
from services.solver.pf_seed import phase_shift_transformers, run_pf


def _chain(shift: float = 30.0, active: bool = True) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "HV", v_nom=110.0)
    n.add("Generator", "G", bus="HV", control="Slack")
    prev = "HV"
    for i in (1, 2):
        n.add("Bus", f"MV{i}", v_nom=20.0)
        n.add(
            "Transformer",
            f"T{i}",
            bus0=prev,
            bus1=f"MV{i}",
            s_nom=40.0,
            x=0.1,
            r=0.01,
            phase_shift=shift,
            active=active,
        )
        n.add("Load", f"L{i}", bus=f"MV{i}", p_set=10.0, q_set=2.5)
        prev = f"MV{i}"
    return n


def _converged(result) -> bool:
    return bool(result["converged"].all().all())


def test_dyn11_chain_fails_flat_start():
    """Pins the fixture: without the seed, Newton-Raphson diverges."""
    n = _chain()
    assert not _converged(n.pf(use_seed=False))


def test_dyn11_chain_converges_seeded_and_says_so():
    n = _chain()
    log: list[str] = []
    result = run_pf(
        n, log.append, snapshots=n.snapshots, use_seed=False, distribute_slack=False
    )
    assert _converged(result)
    assert len(log) == 1 and "lpf() then pf(use_seed=True)" in log[0]
    assert "T1, T2" in log[0]
    # The seeded solve lands on the shifted angle: two -30° hops below the slack.
    assert n.buses_t.v_ang["MV2"].iloc[0] == pytest.approx(
        -2 * 30 * 3.14159265 / 180, abs=0.12
    )


def test_solver_service_call_shape_is_seeded_too():
    """``run_simulation``'s pf mode calls ``run_pf(network, phase)`` with no kwargs."""
    n = _chain()
    assert _converged(run_pf(n, lambda _m: None))


class _Spy:
    """Records lpf/pf calls and forwards them to the real network."""

    def __init__(self, n):
        self.calls: list[tuple[str, dict]] = []
        real_lpf, real_pf = n.lpf, n.pf

        def lpf(**kw):
            self.calls.append(("lpf", kw))
            return real_lpf(**kw)

        def pf(**kw):
            self.calls.append(("pf", kw))
            return real_pf(**kw)

        n.lpf, n.pf = lpf, pf


@pytest.mark.parametrize("shift", [0.0, 360.0, -720.0])
def test_no_shift_keeps_the_exact_flat_start_call(shift):
    n = _chain(shift=shift)
    spy = _Spy(n)
    log: list[str] = []
    kw = dict(snapshots=n.snapshots, x_tol=1e-6, use_seed=False, distribute_slack=False)
    assert _converged(run_pf(n, log.append, **kw))
    assert spy.calls == [("pf", kw)]
    assert log == []


def test_no_shift_no_kwargs_is_bare_pf():
    n = _chain(shift=0.0)
    spy = _Spy(n)
    run_pf(n, None)
    assert spy.calls == [("pf", {})]


def test_inactive_shifting_transformer_keeps_flat_start():
    n = _chain()
    n.transformers.loc["T2", "active"] = False
    n.transformers.loc["T1", "phase_shift"] = 0.0
    n.add("Generator", "G2", bus="MV2", control="Slack")  # MV2 is its own island
    assert phase_shift_transformers(n) == []
    spy = _Spy(n)
    assert _converged(run_pf(n, None, use_seed=False))
    assert spy.calls == [("pf", {"use_seed": False})]


def test_seeded_call_shape():
    n = _chain(shift=330.0)
    spy = _Spy(n)
    run_pf(n, None, snapshots=n.snapshots[:1], use_seed=False, distribute_slack=False)
    assert [c[0] for c in spy.calls] == ["lpf", "pf"]
    assert list(spy.calls[0][1]["snapshots"]) == list(n.snapshots[:1])
    assert spy.calls[1][1]["use_seed"] is True
    assert spy.calls[1][1]["distribute_slack"] is False


def test_only_active_shifting_transformers_are_named():
    n = _chain(shift=-30.0)
    n.transformers.loc["T1", "active"] = False
    assert phase_shift_transformers(n) == ["T2"]


def test_multi_period_activity_follows_the_snapshot_periods():
    n = _chain()
    n.set_snapshots(
        pd.MultiIndex.from_product(
            [[2030, 2040], pd.date_range("2030-01-01", periods=1, freq="h")]
        )
    )
    n.investment_periods = [2030, 2040]
    n.transformers.loc["T1", "phase_shift"] = 0.0
    n.transformers.loc["T2", ["build_year", "lifetime"]] = [2040, 20]
    assert phase_shift_transformers(n, n.snapshots[:1]) == []
    assert phase_shift_transformers(n, n.snapshots[1:]) == ["T2"]


def test_call_sites_use_run_pf():
    """Both app pf calls go through the seeding helper."""
    import inspect

    from services import ac_pf_service, solver_service

    acpf = inspect.getsource(ac_pf_service)
    assert "pf_result = run_pf(" in acpf and "network.pf(" not in acpf
    assert "_run_pf(network, phase)" in inspect.getsource(solver_service.run_simulation)
    assert pf_seed.run_pf is run_pf


@pytest.mark.parametrize("count, tail", [(5, "T4)."), (6, "T4...).")])
def test_log_names_at_most_five_transformers(count, tail):
    n = pypsa.Network()
    n.add("Bus", "HV", v_nom=110.0)
    for i in range(count):
        n.add("Bus", f"B{i}", v_nom=20.0)
        n.add(
            "Transformer",
            f"T{i}",
            bus0="HV",
            bus1=f"B{i}",
            x=0.1,
            s_nom=1.0,
            phase_shift=150.0,
        )
    n.lpf = n.pf = lambda **kw: None  # only the log line is under test
    log: list[str] = []
    run_pf(n, log.append)
    assert f"{count} transformer(s)" in log[0] and log[0].endswith(tail)
