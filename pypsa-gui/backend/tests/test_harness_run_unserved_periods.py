# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Issue 07 B3b (GK B (33) P4): `kpi.unserved_mwh` on a multi-period solve.

Per investment period, shed MW × that snapshot's generator weighting, summed
within the period; never `investment_period_weightings`, never a sum over
periods. The gate compares the worst period and names it."""

from contextlib import nullcontext

import pandas as pd
import pytest

from services import run_gates
from services.run_gates import NoEvidence, Scored
from tests.test_harness_run_eh import DONE_JOB, ctx_for, env_for, read

PERIODS = (2030, 2040)


def periods_env(
    tmp_path,
    monkeypatch,
    *,
    shed=((0.0, 1.0), (1.0, 0.5)),
    weights=((2.0, 2.0), (3.0, 1.0)),
    years=(10.0, 10.0),
    frame_index=None,
    columns=("site",),
    load_carrier=None,
):
    """Two investment periods of two snapshots each; generator weightings
    differ between the periods, and investment-period years are not 1."""
    import pypsa

    from harness import calculations, durable

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.investment_periods = list(PERIODS)
    n.snapshot_weightings.loc[:, "generators"] = [w for p in weights for w in p]
    n.snapshot_weightings.loc[:, "objective"] = [w for p in weights for w in p]
    n.investment_period_weightings.loc[:, "years"] = list(years)
    n.add("Bus", "b")
    n.add(
        "Load",
        "site",
        bus="b",
        p_set=5.0,
        **({"carrier": load_carrier} if load_carrier else {}),
    )
    n.meta = {"assistant_solve_exact_input_fingerprint": "x1"}
    data = {c: [v for p in shed for v in p] for c in columns}
    frame = pd.DataFrame(
        data, index=n.snapshots if frame_index is None else frame_index
    )
    ctx = ctx_for(
        tmp_path, last_lost_load={"lost_load_t": frame, "voll_eur_per_mwh": 1000.0}
    )
    ctx.network = n
    monkeypatch.setattr(run_gates, "_on", lambda c, fn, *a: fn(*a))
    monkeypatch.setattr(durable, "network_guard", lambda: nullcontext())
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda c: True)
    monkeypatch.setattr(run_gates, "_solve_job", lambda env: DONE_JOB)
    return env_for(tmp_path, ctx=ctx)


def gate(env, op, value):
    rule = {
        "id": "r.unserved",
        "fact": "kpi.unserved_mwh",
        "op": op,
        "value": value,
        "severity": "block",
    }
    return run_gates.evaluate([rule], env)[0]


def test_each_period_weights_its_snapshots_by_its_own_generator_weighting(
    tmp_path, monkeypatch
):
    """2030: 1.0 MW × 2 h = 2.0 MWh; 2040: 1.0 × 3 + 0.5 × 1 = 3.5 MWh. Unit
    weightings would read 1.0 and 1.5; the years (10) never enter."""
    value = read("kpi.unserved_mwh", periods_env(tmp_path, monkeypatch))
    assert isinstance(value, Scored)
    assert value.value == pytest.approx(3.5)
    assert [(r["period"], r["unserved_mwh"], r["worst"]) for r in value.rows] == [
        (2030, pytest.approx(2.0), False),
        (2040, pytest.approx(3.5), True),
    ]


def test_the_gate_passes_when_every_period_is_within_the_cap_though_their_sum_is_not(
    tmp_path, monkeypatch
):
    """2.0 and 3.5 MWh each ≤ 4.0; their sum 5.5 is not (the sum-over-periods mutant fails)."""
    result = gate(periods_env(tmp_path, monkeypatch), "le", 4.0)
    assert result["verdict"] == "pass"
    assert result["facts"][0]["value"] == pytest.approx(3.5)
    assert [r["period"] for r in result["facts"][0]["rows"] if r["worst"]] == [2040]


def test_the_gate_fails_on_the_worst_period_though_unit_weightings_would_pass(
    tmp_path, monkeypatch
):
    """The reverse: unweighted, both periods (1.0, 1.5) are within 3.0; weighted,
    2040 reads 3.5 and fails, and the gate names it (the drop-weightings mutant passes)."""
    result = gate(periods_env(tmp_path, monkeypatch), "le", 3.0)
    assert result["verdict"] == "fail"
    assert [r["period"] for r in result["facts"][0]["rows"] if r["worst"]] == [2040]


def test_no_shed_in_any_period_passes_the_shipped_rule(tmp_path, monkeypatch):
    env = periods_env(tmp_path, monkeypatch, shed=((0.0, 0.0), (0.0, 0.0)))
    assert gate(env, "eq", 0.0)["verdict"] == "pass"


def test_a_misaligned_frame_is_no_evidence(tmp_path, monkeypatch):
    index = pd.MultiIndex.from_product(
        [[2030, 2041], pd.date_range("2030-01-01", periods=2, freq="h")]
    )
    value = read(
        "kpi.unserved_mwh", periods_env(tmp_path, monkeypatch, frame_index=index)
    )
    assert isinstance(value, NoEvidence) and value.value == "misaligned_source"


def test_a_column_that_is_no_load_is_no_evidence(tmp_path, monkeypatch):
    value = read("kpi.unserved_mwh", periods_env(tmp_path, monkeypatch, columns=("b",)))
    assert isinstance(value, NoEvidence) and value.value == "legacy_lost_load_capture"


def test_no_capture_is_no_evidence(tmp_path, monkeypatch):
    env = periods_env(tmp_path, monkeypatch)
    env.ctx.solver_state.pop("last_lost_load")
    value = read("kpi.unserved_mwh", env)
    assert isinstance(value, NoEvidence) and value.value == "no_lost_load_capture"


def test_another_solves_results_are_no_evidence(tmp_path, monkeypatch):
    env = periods_env(tmp_path, monkeypatch)
    env.ctx.network.meta = {"assistant_solve_exact_input_fingerprint": "x2"}
    value = read("kpi.unserved_mwh", env)
    assert isinstance(value, NoEvidence) and value.value == "other_solve"


def test_a_stale_multi_period_solve_reads_no_value(tmp_path, monkeypatch):
    from harness import calculations

    env = periods_env(tmp_path, monkeypatch)
    monkeypatch.setattr(calculations, "solve_matches_inputs", lambda c: False)
    assert read("kpi.unserved_mwh", env) is None


# ── Each F6 guard of the multi-period read refuses on its own (P4, P10) ──────


class _Snapshots:
    """The run's network with another snapshot index, every other read the network's."""

    def __init__(self, n, index):
        self._n, self.snapshots = n, index

    def __getattr__(self, name):
        return getattr(self._n, name)


def _by_period(env):
    return run_gates._unserved_by_period(env.ctx)


@pytest.mark.parametrize("levels", [1, 3])
def test_a_snapshot_index_that_is_not_period_by_time_is_unsupported(
    tmp_path, monkeypatch, levels
):
    env = periods_env(tmp_path, monkeypatch)
    n = env.ctx.network
    index = (
        n.snapshots.get_level_values(1)
        if levels == 1
        else pd.MultiIndex.from_arrays([*zip(*n.snapshots), ["a"] * len(n.snapshots)])
    )
    env.ctx.network = _Snapshots(n, index)
    value = _by_period(env)
    assert isinstance(value, NoEvidence) and value.value == "unsupported_snapshot_scope"


@pytest.mark.parametrize("cap, refused", [(3, True), (4, False)])
def test_more_snapshots_than_the_cap_are_unsupported(
    tmp_path, monkeypatch, cap, refused
):
    from harness import calculations

    env = periods_env(tmp_path, monkeypatch)
    monkeypatch.setattr(calculations, "MAX_SNAPSHOTS", cap)
    value = _by_period(env)
    assert (
        isinstance(value, NoEvidence) and value.value == "unsupported_snapshot_scope"
    ) is refused


def test_an_empty_capture_is_no_evidence(tmp_path, monkeypatch):
    value = read("kpi.unserved_mwh", periods_env(tmp_path, monkeypatch, columns=()))
    assert isinstance(value, NoEvidence) and value.value == "no_lost_load_capture"


def test_a_capture_with_a_repeated_column_is_misaligned(tmp_path, monkeypatch):
    env = periods_env(tmp_path, monkeypatch)
    held = env.ctx.solver_state["last_lost_load"]
    held["lost_load_t"] = pd.concat([held["lost_load_t"]] * 2, axis=1)
    value = read("kpi.unserved_mwh", env)
    assert isinstance(value, NoEvidence) and value.value == "misaligned_source"


@pytest.mark.parametrize("cap, refused", [(0, True), (1, False)])
def test_more_loads_than_the_cap_are_unsupported(tmp_path, monkeypatch, cap, refused):
    from harness import lost_load_calculations as lost_load

    env = periods_env(tmp_path, monkeypatch)
    monkeypatch.setattr(lost_load, "MAX_LOADS", cap)
    value = read("kpi.unserved_mwh", env)
    assert (
        isinstance(value, NoEvidence) and value.value == "unsupported_load_population"
    ) is refused


def test_a_capture_of_no_electrical_load_is_no_evidence(tmp_path, monkeypatch):
    env = periods_env(tmp_path, monkeypatch, load_carrier="heat")
    value = read("kpi.unserved_mwh", env)
    assert isinstance(value, NoEvidence) and value.value == "no_electrical_loads"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"shed": ((0.0, float("nan")), (1.0, 0.5))},
        {"weights": ((2.0, -1.0), (3.0, 1.0))},
    ],
)
def test_a_missing_shed_or_a_negative_weight_reads_no_value(
    tmp_path, monkeypatch, kwargs
):
    assert (
        read("kpi.unserved_mwh", periods_env(tmp_path, monkeypatch, **kwargs)) is None
    )


def test_a_single_snapshot_scope_is_read(tmp_path, monkeypatch):
    env = periods_env(tmp_path, monkeypatch)
    n = env.ctx.network
    one = n.snapshots[:1]
    held = env.ctx.solver_state["last_lost_load"]
    held["lost_load_t"] = held["lost_load_t"].loc[one]
    env.ctx.network = _Snapshots(n, one)
    value = _by_period(env)
    assert isinstance(value, Scored) and value.value == 0.0


def test_a_zero_generator_weighting_counts_no_energy_but_is_read(tmp_path, monkeypatch):
    env = periods_env(tmp_path, monkeypatch, weights=((0.0, 2.0), (3.0, 1.0)))
    value = read("kpi.unserved_mwh", env)
    assert isinstance(value, Scored)
    assert [r["unserved_mwh"] for r in value.rows] == [2.0, 3.5]
