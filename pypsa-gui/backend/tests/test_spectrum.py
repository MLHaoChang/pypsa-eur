# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""AS-09: an uploaded power trace gives a PSD, band energy, f_c and the fast-tier split.

Every trace here is synthetic, built in the test; no measured trace enters the repo (SAGC-1).
"""
from __future__ import annotations

import math

import numpy as np
import pypsa
import pytest

from services.adequacy import spectrum as S
from services.solver import envelope_floors as EF
from services.solver.split_storage import add_split_storage

FS = 50.0        # Hz
DURATION = 600.0  # s: 300 periods of 0.5 Hz, so the tone sits on a bin


def _sine(freq=0.5, amp=10.0, mean=100.0, fs=FS, duration=DURATION, phase=0.3):
    t = np.arange(int(duration * fs)) / fs
    return t, mean + amp * np.sin(2 * math.pi * freq * t + phase)


def _csv(t, p, header="time_s,power_mw") -> bytes:
    return (header + "\n" + "\n".join(f"{a:.6f},{b:.9g}" for a, b in zip(t, p))).encode()


def _analyse(t, p, **kw) -> dict:
    return S.analyse(S.Trace(np.asarray(t, float), np.asarray(p, float)), **kw)


# --- the acceptance test: a 0.5 Hz sine of 10 MW amplitude ------------------------------------------------------

def test_a_half_hertz_sine_peaks_at_half_a_hertz_with_the_analytic_band_energy():
    out = _analyse(*_sine())
    assert out["f_peak_hz"] == pytest.approx(0.5, abs=1e-9)
    # A sine of amplitude A carries A^2 / 2 of variance: 50 MW^2 for 10 MW, all of it inside 0.1-20 Hz.
    assert out["band_energy_mw2"] == pytest.approx(10.0 ** 2 / 2, rel=1e-3)
    assert out["total_energy_mw2"] == pytest.approx(50.0, rel=1e-3)
    assert out["in_band_share"] == pytest.approx(1.0, abs=1e-3)


def test_the_psd_integrates_to_the_variance_and_the_mean_is_not_energy():
    t, p = _sine(mean=0.0)
    f, psd = S.spectrum(t, p)
    _, psd_offset = S.spectrum(t, p + 500.0)
    assert np.trapezoid(psd, f) == pytest.approx(np.var(p), rel=1e-3)
    assert np.allclose(psd, psd_offset)


def test_a_tone_outside_the_band_carries_no_band_energy():
    t, p = _sine(freq=0.02, duration=1000.0)
    out = _analyse(t, p)
    assert out["band_energy_mw2"] < 1e-3 * out["total_energy_mw2"]
    assert out["in_band_share"] < 1e-3


def test_two_tones_split_their_energy_by_amplitude():
    t, p = _sine(freq=0.5, amp=10.0)
    p = p + 5.0 * np.sin(2 * math.pi * 4.0 * t)
    f, psd = S.spectrum(t, p)
    assert S.band_energy(f, psd, 0.1, 1.0) == pytest.approx(50.0, rel=1e-3)
    assert S.band_energy(f, psd, 2.0, 20.0) == pytest.approx(12.5, rel=1e-3)


# --- f_c and the in-band limit ------------------------------------------------------------------------------------

def test_f_c_of_a_single_tone_is_the_tone_and_its_latency_bound_is_one_over_two_pi_f():
    out = _analyse(*_sine())
    df = 1.0 / DURATION
    assert out["f_c_hz"] == pytest.approx(0.5, abs=2 * df)
    # The research note's band limit: 0.32 s at T = 2 s.
    assert out["fast_latency_s"] == pytest.approx(1 / (2 * math.pi * out["f_c_hz"]))
    assert out["fast_latency_s"] == pytest.approx(0.318, abs=0.002)
    assert out["limit"] == S.IN_BAND_LIMIT == 0.20 and "assumed" in out["source"]


def test_f_c_leaves_at_most_the_limit_of_in_band_energy_above_it():
    t, p = _sine(freq=0.5, amp=10.0)
    p = p + 5.0 * np.sin(2 * math.pi * 4.0 * t)  # 50 + 12.5 MW^2: the 4 Hz tone is 20 % of the band
    out = _analyse(t, p, limit=0.25)
    assert out["f_c_hz"] == pytest.approx(0.5, abs=0.01)    # cancelling up to 0.5 Hz leaves 20 % <= 25 %
    out = _analyse(t, p, limit=0.10)
    assert out["f_c_hz"] == pytest.approx(4.0, abs=0.01)    # 20 % > 10 %: the tier must reach 4 Hz


def test_the_in_band_test_passes_a_fast_tier_and_fails_a_slow_one():
    t, p = _sine(freq=0.5, amp=10.0)
    p = p + 5.0 * np.sin(2 * math.pi * 4.0 * t)
    trace = S.Trace(t, p)
    f, psd = S.spectrum(t, p)
    # 0.02 s reaches 1/(2 pi 0.02) = 8 Hz: both tones cancelled, nothing left.
    fast = S.in_band_test(f, psd, latency_s=0.02)
    assert fast["residual_share"] == pytest.approx(0.0, abs=1e-3) and fast["passes"]
    # 0.2 s reaches 0.8 Hz: the 4 Hz tone (20 %) is left, over a 10 % limit.
    slow = S.in_band_test(f, psd, latency_s=0.2, limit=0.10)
    assert slow["residual_share"] == pytest.approx(0.2, abs=2e-3) and not slow["passes"]
    assert slow["reach_hz"] == pytest.approx(1 / (2 * math.pi * 0.2))
    assert S.analyse(trace)["tests"]  # one row per shipped technology


def test_the_band_is_clipped_at_nyquist_and_says_so():
    t, p = _sine(fs=10.0)  # Nyquist 5 Hz < 20 Hz
    out = _analyse(t, p)
    assert out["band_hz"] == pytest.approx([0.1, 5.0])
    assert any("Nyquist" in note for note in out["notes"])


# --- the trace replaces the single (A, T) pair --------------------------------------------------------------------

def _envelope():
    phase = lambda cod, a: {"cod": cod, "swing_mw": 30.0,  # noqa: E731
                            "oscillation": {"amplitude_mw": a, "period_s": 2.0, "frequency_hz": 0.5},
                            "checkpoint": {"depth_mw": 0.0, "duration_s": 0.0}}
    return {"phases": [phase("2028-01-01", 3.0), phase("2030-01-01", 4.0)]}


def test_the_trace_replaces_a_phase_oscillation_and_the_floors_read_it():
    out = _analyse(*_sine(freq=0.25, amp=10.0, duration=800.0))
    env = _envelope()
    new = S.replace_oscillation(env, out, cod="2030-01-01")
    osc = new["phases"][1]["oscillation"]
    assert osc["amplitude_mw"] == pytest.approx(10.0, rel=1e-3)   # sqrt(2 x 50 MW^2)
    assert osc["period_s"] == pytest.approx(4.0, rel=1e-6)
    assert osc["source"] == "trace"
    assert new["phases"][0]["oscillation"]["amplitude_mw"] == 3.0  # the other phase keeps its (A, T)
    assert env["phases"][1]["oscillation"]["amplitude_mw"] == 4.0  # the input is not changed
    assert EF.floors_from_envelope(new, 10.0)["power_floor_mw"] == pytest.approx(40.0, rel=1e-3)


def test_without_a_cod_every_phase_takes_the_trace():
    out = _analyse(*_sine())
    new = S.replace_oscillation(_envelope(), out)
    assert all(ph["oscillation"]["source"] == "trace" for ph in new["phases"])


def test_an_unknown_cod_is_refused():
    with pytest.raises(S.TraceError, match="no phase"):
        S.replace_oscillation(_envelope(), _analyse(*_sine()), cod="2040-01-01")


# --- f_c feeds the AS-03 tier split --------------------------------------------------------------------------------

def _site() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(range(4))
    n.add("Bus", "ac")
    add_split_storage(n, "cap", "ac", "supercap")
    add_split_storage(n, "bess", "ac", "energy_bess")
    n.add("Store", "plain", bus="ac", e_nom=1.0)  # not an AS-02 split storage: left alone
    return n


def test_a_two_hertz_f_c_puts_the_fast_store_in_the_tier_and_the_slow_one_out():
    n = _site()
    split = S.apply_tier_split(n, 2.0)  # reach 0.08 s: the GFM supercap, not the GFL energy BESS
    assert split["cap"]["eligible"] and not split["bess"]["eligible"]
    assert split["cap"]["latency_s"] == S.TECHNOLOGY_LATENCY_S["supercap"]
    assert n.stores.at["cap", "smoothing_tier"] == 1.0 and n.stores.at["bess", "smoothing_tier"] == 0.0
    assert "plain" not in split and math.isnan(n.stores.at["plain", "smoothing_tier"])
    assert EF.refusals(n, {"power_floor_mw": 1.0}) == []


def test_a_slow_band_lets_any_bess_in_the_tier():
    n = _site()
    split = S.apply_tier_split(n, 0.5)  # reach 0.32 s, as in the prototype at T = 2 s
    assert split["cap"]["eligible"] and split["bess"]["eligible"]
    assert n.stores.at["bess", "smoothing_tier"] == 1.0


def test_a_latency_override_moves_a_store_across_the_split():
    n = _site()
    split = S.apply_tier_split(n, 2.0, latency={"bess": 0.02})
    assert split["bess"]["eligible"] and split["bess"]["latency_s"] == 0.02


def test_a_network_without_split_storage_has_nothing_to_split():
    n = pypsa.Network()
    n.add("Bus", "ac")
    with pytest.raises(S.TraceError, match="no AS-02 split storage"):
        S.apply_tier_split(n, 0.5)


# --- bad uploads ---------------------------------------------------------------------------------------------------

def test_a_clean_csv_reads_and_converts_kw_to_mw():
    t, p = _sine(duration=60.0)
    tr = S.read_trace(_csv(t, p * 1000.0, header="time_s,power_kw"))
    assert np.allclose(tr.p_mw, p) and np.allclose(tr.t_s, t)
    tr = S.read_trace(_csv(t, p * 1e6, header="t,p"), unit="W")
    assert np.allclose(tr.p_mw, p)


def test_iso_timestamps_read_as_seconds():
    body = "time,power_mw\n" + "\n".join(
        f"2026-01-01T00:{i // 600:02d}:{i // 10 % 60:02d}.{i % 10}00,{100 + math.sin(i):.6f}" for i in range(400))
    tr = S.read_trace(body.encode())
    assert tr.t_s[0] == 0.0 and tr.t_s[1] == pytest.approx(0.1)


@pytest.mark.parametrize("blob, match", [
    (b"", "empty"),
    (b"time_s,power_mw\n", "empty"),
    (b"time_s,power_mw\n0,1\n0.1,2\n", "at least"),
])
def test_empty_or_tiny_uploads_are_refused(blob, match):
    with pytest.raises(S.TraceError, match=match):
        S.read_trace(blob)


def test_a_non_monotonic_time_column_is_refused_and_names_the_row():
    t, p = _sine(duration=60.0)
    t[100], t[101] = t[101], t[100]
    with pytest.raises(S.TraceError, match=r"strictly increasing.*row 10[12]"):
        S.read_trace(_csv(t, p))


def test_a_repeated_timestamp_is_refused():
    t, p = _sine(duration=60.0)
    t[50] = t[49]
    with pytest.raises(S.TraceError, match="strictly increasing"):
        S.read_trace(_csv(t, p))


@pytest.mark.parametrize("header, unit, match", [
    ("time_s,power_mwh", None, "unit"),        # energy, not power
    ("time_s,power_hz", None, "unit"),
    ("time_s,power", None, "unit"),            # no unit anywhere
    ("time_s,power", "MVAr", "unit"),
    ("time_s,power_kw", "MW", "disagree"),     # header and form field disagree
])
def test_wrong_or_missing_units_are_refused(header, unit, match):
    t, p = _sine(duration=60.0)
    with pytest.raises(S.TraceError, match=match):
        S.read_trace(_csv(t, p, header=header), unit=unit)


def test_a_trace_in_watts_labelled_mw_is_refused_as_implausible():
    t, p = _sine(duration=60.0)
    with pytest.raises(S.TraceError, match="implausible"):
        S.read_trace(_csv(t, p * 1e6))


def test_non_numeric_or_missing_values_are_refused():
    t, p = _sine(duration=60.0)
    blob = _csv(t, p).replace(b"\n1.000000,", b"\n1.000000,abc\n1.0000001,", 1)
    with pytest.raises(S.TraceError, match="not a number|strictly increasing"):
        S.read_trace(blob)
    p[10] = np.nan
    with pytest.raises(S.TraceError, match="not a number"):
        S.read_trace(_csv(t, p))


def test_uneven_sampling_is_refused():
    t, p = _sine(duration=60.0)
    t[200:] += 0.01  # one 30 ms gap in a 20 ms series
    with pytest.raises(S.TraceError, match="evenly sampled"):
        S.read_trace(_csv(t, p))


def test_a_trace_too_short_for_the_band_is_refused():
    t, p = _sine(duration=5.0)  # < 1 / 0.1 Hz
    with pytest.raises(S.TraceError, match="too short"):
        _analyse(t, p)


def test_a_flat_trace_has_no_spectrum_to_read():
    t = np.arange(1000) / FS
    with pytest.raises(S.TraceError, match="flat"):
        _analyse(t, np.full_like(t, 100.0))


def test_a_trace_too_coarse_for_the_band_is_refused():
    t, p = _sine(freq=0.01, fs=0.1, duration=3000.0)  # Nyquist 0.05 Hz < 0.1 Hz
    with pytest.raises(S.TraceError, match="Nyquist"):
        _analyse(t, p)


# --- the route ------------------------------------------------------------------------------------------------------

URL = "/api/network/spectrum/trace"


def test_the_route_analyses_an_upload_without_touching_the_network(client, install_network):
    n = install_network(_site())
    t, p = _sine(freq=2.0)
    r = client.post(URL, files={"file": ("trace.csv", _csv(t, p), "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["f_peak_hz"] == pytest.approx(2.0)
    assert body["tier_split"]["cap"]["eligible"] and not body["tier_split"]["bess"]["eligible"]
    assert body["applied"] is False and "smoothing_tier" not in n.stores.columns
    assert "psd" in body and len(body["psd"]["f_hz"]) == len(body["psd"]["mw2_per_hz"]) <= S.PSD_POINTS


def test_the_route_applies_the_tier_split_when_asked(client, install_network):
    n = install_network(_site())
    t, p = _sine(freq=2.0)
    r = client.post(URL, files={"file": ("trace.csv", _csv(t, p), "text/csv")}, data={"apply": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True
    assert n.stores.at["cap", "smoothing_tier"] == 1.0 and n.stores.at["bess", "smoothing_tier"] == 0.0


@pytest.mark.parametrize("blob, data, match", [
    (b"", {}, "empty"),
    (b"time_s,power_mwh\n" + b"\n".join(f"{i / 10},{i}".encode() for i in range(200)), {}, "unit"),
    (b"time_s,power_mw\n" + b"\n".join(f"{(i % 50) / 10},{i}".encode() for i in range(200)), {}, "increasing"),
    (None, {"limit": "1.5"}, "limit"),
    (None, {"band_lo_hz": "5", "band_hi_hz": "1"}, "band"),
])
def test_the_route_refuses_bad_uploads_with_a_clear_error(client, install_network, blob, data, match):
    install_network(_site())
    if blob is None:
        blob = _csv(*_sine())
    r = client.post(URL, files={"file": ("trace.csv", blob, "text/csv")}, data=data)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_kind"] == "bad_trace"
    assert match in detail["message"]
