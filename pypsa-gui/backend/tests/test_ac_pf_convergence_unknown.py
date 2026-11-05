"""AA-12: AC power flow convergence that PyPSA did not report is unknown, never converged.

`n.pf()` returns `Dict(n_iter, error, converged)`; `converged` is a bool frame of
snapshots x sub-networks. A snapshot counts as converged only when every
sub-network cell is a real boolean True. A missing or unreadable result, a
frame with no columns, a NaN or non-bool cell, and a snapshot the frame does not
cover are unknown (`ok: None`). A known False wins over unknown for its
snapshot. While any snapshot is unknown the run records no converged count, so
the sealed run carries its total alone and is refused as not known to have
converged.
"""
import queue

import numpy as np
import pandas as pd
import pytest

from services.ac_pf_service import _convergence_rows, run_ac_pf_stage
from services.solver_service import SolverConfig
from tests.test_ac_pf_link_dispatch import LOAD, _hub

HOURS = pd.date_range("2030-01-01", periods=3, freq="h")


def _frame(rows, index=HOURS, columns=("sn0",)):
    return pd.DataFrame(rows, index=index, columns=list(columns))


# ── the pure reader ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("pf_result", [None, {}, {"converged": None}, {"converged": "yes"}],
                         ids=["none", "no_key", "none_value", "not_a_frame"])
def test_a_result_without_a_frame_is_unknown(pf_result):
    assert _convergence_rows(pf_result, HOURS) == [None, None, None]


class _Raising(dict):
    def get(self, key, default=None):
        raise RuntimeError("converged access raised")


class _RaisingFrame(pd.DataFrame):
    def __iter__(self):
        raise RuntimeError("iteration raised")

    def itertuples(self, *a, **k):
        raise RuntimeError("itertuples raised")

    def reindex(self, *a, **k):
        raise RuntimeError("reindex raised")


@pytest.mark.parametrize("pf_result", [
    _Raising(),
    {"converged": _RaisingFrame({"sn0": [True, True, True]}, index=HOURS)},
], ids=["converged_access_raises", "frame_read_raises"])
def test_a_result_whose_read_raises_is_unknown(pf_result):
    assert _convergence_rows(pf_result, HOURS) == [None, None, None]


def test_a_frame_with_no_sub_network_columns_is_unknown():
    assert _convergence_rows({"converged": pd.DataFrame(index=HOURS)}, HOURS) == [None] * 3


def test_a_nan_cell_is_unknown_not_converged():
    conv = _frame({"sn0": [True, np.nan, True]})
    assert _convergence_rows({"converged": conv}, HOURS) == [True, None, True]


@pytest.mark.parametrize("value", [1, "True", None], ids=["int", "str", "none"])
def test_a_non_bool_cell_is_unknown(value):
    conv = _frame({"sn0": pd.Series([True, value, True], index=HOURS, dtype=object)})
    assert _convergence_rows({"converged": conv}, HOURS) == [True, None, True]


def test_a_snapshot_missing_from_the_frame_is_unknown_and_extra_rows_are_ignored():
    index = pd.DatetimeIndex([HOURS[0], HOURS[2], HOURS[2] + pd.Timedelta("1h")])
    conv = _frame({"sn0": [True, True, False]}, index=index)
    assert _convergence_rows({"converged": conv}, HOURS) == [True, None, True]


def test_a_known_false_wins_over_unknown_for_its_snapshot():
    conv = _frame({"a": [True, False, True], "b": [True, np.nan, np.nan]}, columns=("a", "b"))
    assert _convergence_rows({"converged": conv}, HOURS) == [True, False, None]


def test_numpy_bools_and_a_series_are_read():
    conv = pd.Series(np.array([True, False, True]), index=HOURS)
    assert _convergence_rows({"converged": conv}, HOURS) == [True, False, True]
    assert all(type(ok) is bool for ok in _convergence_rows({"converged": conv}, HOURS))


def test_multi_period_keys_on_the_snapshot_multiindex():
    sns = pd.MultiIndex.from_product([[2030, 2040], HOURS])
    conv = pd.DataFrame({"sn0": [True] * 5}, index=sns[:5])
    assert _convergence_rows({"converged": conv}, sns) == [True] * 5 + [None]
    # A frame on the flat timestep index does not cover the multi-period snapshots.
    flat = _frame({"sn0": [True, True, True]})
    assert _convergence_rows({"converged": flat}, sns) == [None] * 6


# ── the Stage 2 record ──────────────────────────────────────────────────────


def _stage(monkeypatch, pf_result, multi_period=False):
    n = _hub(multi_period)
    monkeypatch.setattr(n, "pf", lambda *a, **k: pf_result)
    return n, run_ac_pf_stage(n, SolverConfig(), queue.Queue())


def _oks(out):
    return [row["ok"] for row in out["ac_pf_convergence_list"]]


@pytest.mark.parametrize("pf_result", [None, {}, _Raising()],
                         ids=["none", "no_key", "converged_access_raises"])
def test_stage_2_without_convergence_evidence_records_unknown(monkeypatch, pf_result):
    n, out = _stage(monkeypatch, pf_result)
    assert _oks(out) == [None] * len(LOAD)
    assert set(out["ac_pf_convergence"].values()) == {None}
    assert out["ac_pf_unknown_count"] == len(LOAD)
    assert out["ac_pf_converged_count"] is None
    assert out["ac_pf_total_snapshots"] == len(n.snapshots)


def test_stage_2_with_one_unknown_snapshot_records_no_converged_count(monkeypatch):
    sns = None

    def pf(*a, **k):
        return {"converged": pd.DataFrame({"sn0": [True, False]}, index=sns[:2])}

    n = _hub()
    sns = n.snapshots
    monkeypatch.setattr(n, "pf", pf)
    out = run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    assert _oks(out) == [True, False, None]
    assert out["ac_pf_unknown_count"] == 1
    assert out["ac_pf_converged_count"] is None
    assert out["ac_pf_total_snapshots"] == 3


def test_stage_2_multi_period_counts_every_snapshot(monkeypatch):
    n = _hub(multi_period=True)
    conv = pd.DataFrame({"sn0": [True] * (len(n.snapshots) - 1)}, index=n.snapshots[:-1])
    monkeypatch.setattr(n, "pf", lambda *a, **k: {"converged": conv})
    out = run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    assert _oks(out) == [True] * (len(n.snapshots) - 1) + [None]
    assert [row["period"] for row in out["ac_pf_convergence_list"]] == [2030] * 3 + [2040] * 3
    assert out["ac_pf_unknown_count"] == 1 and out["ac_pf_converged_count"] is None
    assert out["ac_pf_total_snapshots"] == len(n.snapshots) == 6


def test_stage_2_fully_known_keeps_its_counts(monkeypatch):
    n = _hub()
    conv = pd.DataFrame({"sn0": [True, False, True]}, index=n.snapshots)
    monkeypatch.setattr(n, "pf", lambda *a, **k: {"converged": conv})
    out = run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    assert _oks(out) == [True, False, True]
    assert out["ac_pf_unknown_count"] == 0
    assert out["ac_pf_converged_count"] == 2
    assert out["ac_pf_total_snapshots"] == 3


def test_a_real_pf_is_fully_known():
    out = run_ac_pf_stage(_hub(), SolverConfig(), queue.Queue())
    assert _oks(out) == [True] * len(LOAD)
    assert out["ac_pf_unknown_count"] == 0
    assert out["ac_pf_converged_count"] == out["ac_pf_total_snapshots"] == len(LOAD)


def test_a_raising_pf_still_fails_the_stage_and_records_no_counts(monkeypatch):
    n = _hub()

    def pf(*a, **k):
        raise ValueError("bad network")

    monkeypatch.setattr(n, "pf", pf)
    with pytest.raises(ValueError, match="bad network"):
        run_ac_pf_stage(n, SolverConfig(), queue.Queue())
