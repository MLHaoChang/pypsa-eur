"""DC 05: hourly utilisation shapes per workload kind, literal values (GK A W3, W7)."""
from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from models.dc_compute_spec import WorkloadMixSpec
from services.dc_workload_shapes import shape_utilisation

HOD = np.arange(24)
TRAIN = np.full(24, 0.9)
MIX = WorkloadMixSpec(trough_share=0.4, peak_share=0.8, peak_hour=20, training_share=0.25, flat_share=0.6)


def u(kind, mix=MIX, train=TRAIN):
    return shape_utilisation(kind, HOD, training=train, mix=mix)


def test_ai_training_returns_the_training_series_untouched():
    assert u("ai_training") is TRAIN or np.array_equal(u("ai_training"), TRAIN)


def test_inference_peaks_at_peak_hour_and_troughs_twelve_hours_later():
    r = u("inference")
    assert r[20] == pytest.approx(0.8)
    assert r[8] == pytest.approx(0.4)
    assert r[14] == pytest.approx(0.4 + 0.4 * 0.5 * (1 + np.cos(2 * np.pi * (14 - 20) / 24)))
    assert r[14] == pytest.approx(0.6)  # quarter period away: the midpoint
    assert r[19] == pytest.approx(0.7931852)  # one hour off the peak is not the peak
    assert int(np.argmax(r)) == 20 and int(np.argmin(r)) == 8


def test_mixed_is_the_training_share_blend():
    r = u("mixed")
    assert r[20] == pytest.approx(0.25 * 0.9 + 0.75 * 0.8)  # 0.825
    assert r[8] == pytest.approx(0.25 * 0.9 + 0.75 * 0.4)  # 0.525


def test_flat_is_the_flat_share():
    assert np.all(u("flat") == 0.6)


@pytest.mark.parametrize("bad", [
    dict(trough_share=-0.1), dict(peak_share=1.1), dict(trough_share=0.9, peak_share=0.5),
    dict(peak_hour=24), dict(peak_hour=-1), dict(peak_hour=3.5), dict(training_share=1.1),
    dict(training_share=-0.1), dict(flat_share=1.1), dict(flat_share=-0.1),
    dict(trough_share=float("nan")), dict(peak_share=float("inf")), dict(flat_share=float("inf")),
    dict(training_share=float("inf")),
])
def test_mix_bounds_refuse(bad):
    with pytest.raises(ValidationError):
        WorkloadMixSpec(**bad)


def test_defaults_are_valid():
    WorkloadMixSpec()
