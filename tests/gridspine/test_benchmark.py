"""GC-07 part 4: windowed errors between two channel frames (RMS vs EMT, model vs field)."""

import numpy as np
import pytest

from gridspine.readback.benchmark import benchmark, common_grid, window_names
from gridspine.readback.channels import ChannelFrame
from gridspine.schema.contracts import ContractError

T = np.round(np.arange(0, 1.0001, 0.001), 6)  # 1 kHz, 0..1 s
V = np.where((T >= 0.2) & (T < 0.35), 0.3, 1.0) + 0.01 * np.sin(40 * T)
EVENTS = (0.2, 0.35)


def _frame(values, name="V", unit="pu", time=T, source="ref"):
    return ChannelFrame(time, {name: values}, {name: unit}, source)


def _rows(rows, metric):
    return {r["window"]: r for r in rows if r["metric"] == metric}


def test_frame_against_itself_gives_zero_errors():
    rows = benchmark(_frame(V), _frame(V, source="test"), ["V"], EVENTS)
    assert len(rows) == 9
    assert all(r["value"] == 0.0 for r in rows)
    assert {r["window"] for r in rows} == {"pre_fault", "fault", "post_fault"}


def test_constant_offset_gives_mean_error_in_every_window():
    rows = benchmark(_frame(V), _frame(V + 0.02, source="test"), ["V"], EVENTS)
    for metric in ("me", "mae", "mxe"):
        for r in _rows(rows, metric).values():
            assert r["value"] == pytest.approx(0.02, abs=1e-12)
    # the sign is test minus reference
    rows = benchmark(_frame(V), _frame(V - 0.02, source="test"), ["V"], EVENTS)
    assert all(
        r["value"] == pytest.approx(-0.02, abs=1e-12)
        for r in _rows(rows, "me").values()
    )
    assert all(
        r["value"] == pytest.approx(0.02, abs=1e-12)
        for r in _rows(rows, "mae").values()
    )


def test_windows_split_at_the_event_times():
    rows = _rows(benchmark(_frame(V), _frame(V, source="test"), ["V"], EVENTS), "me")
    assert [(r["t_start"], r["t_end"], r["samples"]) for r in rows.values()] == [
        (0.0, 0.2, 200),
        (0.2, 0.35, 150),
        (0.35, 1.0, 651),
    ]
    # an error only inside the fault window shows only there
    bump = V + np.where((T >= 0.2) & (T < 0.35), 0.1, 0.0)
    rows = _rows(benchmark(_frame(V), _frame(bump, source="test"), ["V"], EVENTS), "me")
    assert rows["pre_fault"]["value"] == pytest.approx(0.0, abs=1e-12)
    assert rows["fault"]["value"] == pytest.approx(0.1)
    assert rows["post_fault"]["value"] == pytest.approx(0.0, abs=1e-12)
    # an error on the sample at the event time belongs to the window that starts there
    spike = V.copy()
    spike[T == 0.35] += 0.5
    rows = _rows(
        benchmark(_frame(V), _frame(spike, source="test"), ["V"], EVENTS), "mxe"
    )
    assert rows["fault"]["value"] == pytest.approx(0.0, abs=1e-12)
    assert rows["post_fault"]["value"] == pytest.approx(0.5) and rows["post_fault"][
        "at_s"
    ] == pytest.approx(0.35)


def test_window_names():
    assert window_names(2) == ("pre_fault", "fault", "post_fault")
    assert window_names(1) == ("w0", "w1")
    assert window_names(3) == ("w0", "w1", "w2", "w3")
    rows = benchmark(_frame(V), _frame(V, source="test"), ["V"], [0.5])
    assert [r["window"] for r in rows if r["metric"] == "me"] == ["w0", "w1"]


def test_max_error_is_signed_with_its_time():
    dip = V.copy()
    dip[T == 0.5] -= 0.3
    dip[T == 0.6] += 0.2
    r = _rows(benchmark(_frame(V), _frame(dip, source="test"), ["V"], EVENTS), "mxe")[
        "post_fault"
    ]
    assert r["value"] == pytest.approx(-0.3) and r["at_s"] == pytest.approx(0.5)


def test_resampling_onto_the_coarser_grid_and_shared_span():
    coarse_t = np.round(np.arange(0.1, 0.9001, 0.004), 6)
    coarse = _frame(np.interp(coarse_t, T, V) + 0.02, time=coarse_t, source="emt")
    grid = common_grid(_frame(V), coarse)
    assert grid[0] == pytest.approx(0.1) and grid[-1] == pytest.approx(0.9)
    assert np.diff(grid) == pytest.approx(0.004)
    rows = _rows(benchmark(_frame(V), coarse, ["V"], EVENTS), "me")
    assert rows["pre_fault"]["t_start"] == pytest.approx(0.1)
    assert rows["post_fault"]["t_end"] == pytest.approx(0.9)
    assert rows["pre_fault"]["samples"] == 25
    assert len(common_grid(_frame(V), _frame(V), dt=0.25)) == 5


def test_tolerances_pass_fail_and_margin():
    rows = benchmark(
        _frame(V),
        _frame(V + 0.02, source="test"),
        ["V"],
        EVENTS,
        tolerances={"V": {"me": 0.021, "mae": 0.05, "fault.mae": 0.01}},
    )
    me, mae, mxe = (_rows(rows, m) for m in ("me", "mae", "mxe"))
    assert all(
        r["status"] == "pass" and r["margin"] == pytest.approx(0.001)
        for r in me.values()
    )
    assert mae["pre_fault"]["status"] == "pass" and mae["pre_fault"][
        "margin"
    ] == pytest.approx(0.03)
    assert mae["fault"]["status"] == "fail" and mae["fault"]["margin"] == pytest.approx(
        -0.01
    )
    assert all(r["status"] == "unknown" and r["margin"] is None for r in mxe.values())
    assert mxe["fault"]["detail"] == "no tolerance given"
    rows = benchmark(
        _frame(V),
        _frame(V - 0.03, source="test"),
        ["V"],
        EVENTS,
        tolerances={"V": {"me": 0.02}},
    )
    assert all(r["status"] == "fail" for r in _rows(rows, "me").values())


def test_one_ulp_above_the_tolerance_fails():
    ones = np.ones_like(T)
    above = float(np.nextafter(0.5, 0.0))
    rows = benchmark(
        _frame(ones),
        _frame(ones * 1.5, source="test"),
        ["V"],
        EVENTS,
        tolerances={"V": {"me": above}},
    )
    assert all(r["status"] == "fail" for r in _rows(rows, "me").values())


def test_max_error_keeps_its_sign_and_first_time_on_ties():
    low = V.copy()
    low[(T > 0.4)] -= 0.01
    low[T == 0.5] -= 0.3
    low[T == 0.7] -= 0.3
    r = _rows(benchmark(_frame(V), _frame(low, source="test"), ["V"], EVENTS), "mxe")[
        "post_fault"
    ]
    assert r["value"] == pytest.approx(-0.31) and r["at_s"] == pytest.approx(0.5)


def test_error_exactly_at_the_tolerance_passes():
    ones = np.ones_like(T)
    rows = benchmark(
        _frame(ones),
        _frame(ones * 1.5, source="test"),
        ["V"],
        EVENTS,
        tolerances={"V": {"me": 0.5, "mxe": 0.4999}},
    )
    assert all(
        r["status"] == "pass" and r["margin"] == 0.0 for r in _rows(rows, "me").values()
    )
    assert all(r["status"] == "fail" for r in _rows(rows, "mxe").values())


def test_missing_channel_is_unknown_never_pass():
    rows = benchmark(
        _frame(V),
        _frame(V, name="U", source="test"),
        {"V": "V_emt"},
        EVENTS,
        tolerances={"V": {"me": 1.0, "mae": 1.0, "mxe": 1.0}},
    )
    assert len(rows) == 9
    assert all(r["status"] == "unknown" and r["value"] is None for r in rows)
    assert "test (test) has no channel 'V_emt'" in rows[0]["detail"]


def test_channel_mapping_and_units():
    ref = _frame(V, name="V_rms")
    test = ChannelFrame(T, {"V_emt": V + 0.01}, {"V_emt": "pu"}, "emt")
    rows = benchmark(ref, test, {"V_rms": "V_emt"}, EVENTS)
    assert (
        rows[0]["channel"] == "V_rms"
        and rows[0]["test_channel"] == "V_emt"
        and rows[0]["unit"] == "pu"
    )
    with pytest.raises(ContractError, match="in 'pu' but 'V' in 'kV'"):
        benchmark(_frame(V), _frame(V, unit="kV"), ["V"], EVENTS)
    # the same voltage in kV and in V: equal physically, refused rather than compared as numbers
    with pytest.raises(ContractError, match="in 'kV' but 'V' in 'V'"):
        benchmark(
            _frame(V * 132, unit="kV"), _frame(V * 132e3, unit="V"), ["V"], EVENTS
        )


def test_every_row_states_its_method():
    rows = benchmark(_frame(V), _frame(V, source="test"), ["V"], EVENTS)
    assert {r["method"] for r in rows} == {
        "whole-window metrics; no IEC pre-filter or sub-window split"
    }


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"events": (0.2, 1.0)}, "not inside the shared span"),
        ({"events": (0.0, 0.3)}, "not inside the shared span"),
        ({"events": (0.35, 0.2)}, "strictly increasing"),
        ({"events": (0.2, 0.2)}, "strictly increasing"),
        ({"events": (0.2005, 0.2008)}, "window 'fault' .* holds no sample"),
        ({"dt": 0}, "dt must be a number above zero"),
        ({"dt": True}, "dt must be a number above zero"),
        ({"dt": 2.0}, "fewer than two samples"),
        ({"channels": []}, "at least one channel"),
        ({"channels": [1]}, "must be text"),
        ({"tolerances": {"W": {"me": 1}}}, "not a benchmarked channel"),
        ({"tolerances": {"V": {"rmse": 1}}}, "has 'rmse'"),
        ({"tolerances": {"V": {"me": -1}}}, "number from 0"),
        ({"tolerances": {"V": {"me": True}}}, "number from 0"),
        ({"tolerances": {"V": {"me": float("nan")}}}, "number from 0"),
        ({"tolerances": {"V": {"me": float("inf")}}}, "number from 0"),
        ({"events": (0.0, 0.5)}, "not inside the shared span"),
        ({"events": (0.5, 1.0)}, "not inside the shared span"),
        ({"events": (0.5, float("nan"))}, "not inside the shared span"),
        ({"tolerances": {"V": 0.1}}, "must be a mapping"),
        ({"tolerances": [0.1]}, "tolerances must be a mapping"),
    ],
)
def test_refusals(kwargs, match):
    args = {"channels": ["V"], "events": EVENTS, **kwargs}
    with pytest.raises(ContractError, match=match):
        benchmark(_frame(V), _frame(V, source="test"), **args)


def test_no_shared_span_refused():
    later = _frame(V, time=T + 2.0, source="late")
    with pytest.raises(ContractError, match="share no time span"):
        benchmark(_frame(V), later, ["V"], EVENTS)


# --- one targeted test per guard (GK A2 B8) ---------------------------------------------------------------------


@pytest.mark.parametrize("dt", [float("nan"), float("inf"), -0.1, "0.1"])
def test_dt_refusals(dt):
    with pytest.raises(ContractError, match="dt must be a number above zero"):
        common_grid(_frame(V), _frame(V), dt=dt)


def test_two_sample_grid_and_touching_spans():
    assert len(common_grid(_frame(V), _frame(V), dt=1.0)) == 2
    touching = _frame(V, time=T + 1.0, source="late")
    with pytest.raises(ContractError, match="share no time span"):
        common_grid(_frame(V), touching)


@pytest.mark.parametrize("channels", [{"V": 5}, {5: "V"}])
def test_channel_names_must_be_text(channels):
    with pytest.raises(ContractError, match="must be text"):
        benchmark(_frame(V), _frame(V, source="test"), channels, EVENTS)


def test_missing_reference_channel_is_unknown():
    rows = benchmark(_frame(V, name="U"), _frame(V, source="test"), ["V"], EVENTS)
    assert all(r["status"] == "unknown" for r in rows)
    assert "ref (reference) has no channel 'V'" in rows[0]["detail"]
    assert rows[0]["unit"] == "pu"


@pytest.mark.parametrize("lim", ["0.1", None])
def test_tolerance_must_be_a_number(lim):
    with pytest.raises(ContractError, match="number from 0"):
        benchmark(
            _frame(V),
            _frame(V, source="test"),
            ["V"],
            EVENTS,
            tolerances={"V": {"me": lim}},
        )


def test_zero_tolerance_is_allowed():
    rows = benchmark(
        _frame(V), _frame(V, source="test"), ["V"], EVENTS, tolerances={"V": {"me": 0}}
    )
    assert all(r["status"] == "pass" for r in _rows(rows, "me").values())


def test_events_as_a_numpy_array():
    rows = benchmark(_frame(V), _frame(V, source="test"), ["V"], np.array([1]) * 0.5)
    assert [r["window"] for r in rows if r["metric"] == "me"] == ["w0", "w1"]
