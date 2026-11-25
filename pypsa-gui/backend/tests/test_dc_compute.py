"""DC 05: the optional compute block derives IT MW per phase (GK A W3, W6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.dc_compute_spec import ComputeSpec
from services.dc_compute import MISMATCH_NOTE_ABOVE, compute_report, derived_it_mw


def spec(**kw):
    return ComputeSpec(
        **{
            "kw_per_accelerator": 1.5,
            "utilisation": 0.8,
            "accelerator_counts": [10000, 20000],
            **kw,
        }
    )


def test_derived_it_mw_is_count_times_kw_times_utilisation():
    assert derived_it_mw(spec(), 0) == pytest.approx(12.0)  # 10000 * 1.5 * 0.8 / 1000
    assert derived_it_mw(spec(), 1) == pytest.approx(24.0)


def test_report_shows_derived_next_to_typed_and_typed_wins():
    rows = compute_report(spec(), [12.0, 30.0])
    assert rows[0]["typed_it_mw"] == 12.0 and rows[0]["compute_it_mw"] == pytest.approx(
        12.0
    )
    assert rows[0]["it_mw_used"] == 12.0 and rows[0]["warning"] is None
    assert rows[1]["it_mw_used"] == 30.0  # typed, not the derived 24
    assert (
        rows[1]["warning"] and "30" in rows[1]["warning"] and "24" in rows[1]["warning"]
    )


def test_without_typed_there_is_nothing_to_win_over_but_derived_is_reported_only():
    rows = compute_report(spec(), [None, None])
    assert rows[0]["it_mw_used"] is None and rows[0]["compute_it_mw"] == pytest.approx(
        12.0
    )


def test_no_compute_block_reports_nothing():
    assert compute_report(None, [12.0]) == []


def test_mismatch_boundary_is_literal_ten_percent():
    assert MISMATCH_NOTE_ABOVE == 0.10
    s = ComputeSpec(
        kw_per_accelerator=1.0, utilisation=1.0, accelerator_counts=[11000, 11001]
    )
    rows = compute_report(s, [10.0, 10.0])  # derived 11.0 and 11.001 MW vs typed 10 MW
    assert rows[0]["warning"] is None  # exactly 10 % over typed
    assert rows[1]["warning"] is not None  # just over


@pytest.mark.parametrize(
    "bad",
    [
        dict(kw_per_accelerator=0),
        dict(kw_per_accelerator=-1),
        dict(kw_per_accelerator=float("inf")),
        dict(kw_per_accelerator=float("nan")),
        dict(utilisation=0),
        dict(utilisation=1.01),
        dict(utilisation=float("inf")),
        dict(accelerator_counts=[0]),
        dict(accelerator_counts=[-5]),
        dict(accelerator_counts=[float("inf")]),
        dict(accelerator_counts=[float("nan")]),
        dict(accelerator_counts=[]),
    ],
)
def test_compute_bounds_refuse(bad):
    with pytest.raises(ValidationError):
        spec(**bad)


def test_whole_accelerators_only_and_no_typed_figure_means_none_used():
    with pytest.raises(ValidationError):
        spec(accelerator_counts=[12.5])
    assert compute_report(spec(), [0, 0])[0]["it_mw_used"] is None


def test_a_phase_beyond_the_given_counts_has_no_derived_figure():
    rows = compute_report(spec(), [12.0, 24.0, 40.0])
    assert rows[2]["compute_it_mw"] is None and rows[2]["warning"] is None
    assert rows[2]["it_mw_used"] == 40.0
