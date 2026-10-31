# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""
Spectrum input (AS-09): a measured or vendor power trace in place of the envelope's single (A, T) pair.

The AI-training envelope (`services.dc_load_profile.dynamic_envelope`) gives each phase one oscillation, an
amplitude A at a period T. A trace (time in s, active power in W, kW, MW or GW) carries the whole spectrum:

* **PSD.** The one-sided power spectral density of the mean-removed trace, a Hann-windowed periodogram over
  the whole record (`scipy.signal.welch` with one segment), in MW²/Hz. It integrates to the trace's variance,
  so a sine of amplitude A carries A²/2.
* **Band energy.** The PSD integrated over the band of concern, MW². A utility example limits content in a
  critical band of ~0.1-20 Hz to 20 % of in-band energy (Choukse et al., arXiv 2508.14318, 2025); both the
  band and the limit are "assumed" defaults the caller can change.
* **f_c.** The lowest PSD bin at which the cumulative in-band share reaches 1 − limit: at most the limit's
  share of in-band energy lies above it. A smoothing tier that cancels everything up to f_c leaves the grid
  within the limit.
* **Tier split.** A technology is fast enough for the band when its response latency is below about
  1/(2π f) of the band it must cancel (research note section 4; 0.32 s at T = 2 s). A split storage (AS-02)
  joins the AS-03 smoothing tier when its latency is at most 1/(2π f_c); `apply_tier_split` sets the
  `smoothing_tier` flag that AS-03's floors read. A store's latency comes from its converter control through
  `smoothing.response_latency`, the control from its technology (`TECHNOLOGY_CONTROL`, assumed).
* **Equivalent oscillation** (assumed: single-sine equivalent of a multi-tone band). A = √(2 E_band) at
  T = the band's energy-weighted mean period ∑E_i/f_i / ∑E_i replaces a phase's (A, T) for
  `floors_from_envelope`. Weighting the period by energy, not taking 1/f_peak, keeps a broadband trace's slow
  content in the energy floor (the swing A·T/π grows with T); for one tone both are 1/f.

Time is numeric seconds. The trace is read in memory and never stored (SAGC-1). Every limit is generic engineering, "assumed".
"""
from __future__ import annotations

import copy
import io
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from services.adequacy.smoothing import response_latency

if TYPE_CHECKING:
    import pypsa

#: Band of concern and the share of its energy the grid may see; assumed, after Choukse et al. (2025).
BAND_HZ = (0.1, 20.0)
IN_BAND_LIMIT = 0.20
SOURCE = ("assumed; band ~0.1-20 Hz and 20 % in-band limit after a utility example cited by Choukse et al., "
          "arXiv 2508.14318 (2025); fast means latency below 1/(2π f)")
#: Power units the trace may carry, in MW.
UNITS = {"w": 1e-6, "kw": 1e-3, "mw": 1.0, "gw": 1e3}
#: Header suffixes that name a unit, but not one of active power.
NOT_POWER = {"wh", "kwh", "mwh", "gwh", "hz", "a", "ka", "v", "kv", "var", "kvar", "mvar", "va", "kva", "mva",
             "pu", "%", "s", "ms"}
#: The trace must hold this many periods of the band's low edge, so that edge is resolved past the window.
MIN_PERIODS = 3
#: The clipped band must hold this many PSD bins.
MIN_BAND_BINS = 3
MIN_SAMPLES = 8
#: The PSD takes the whole trace as one segment, so its length is capped: 2^21 samples, about 11.6 h at 50 Hz.
MAX_SAMPLES = 2 ** 21
#: Upload cap for a trace, bytes: 2^21 rows of about 30 characters.
MAX_TRACE_BYTES = 64 * 1024 * 1024
#: Largest |P| read as plausible for one site, MW; above it the unit is likely wrong (W labelled MW).
MAX_ABS_MW = 1e5
#: Sampling may jitter by this share of the median interval; beyond it the PSD would need resampling.
JITTER = 0.01
EQUIVALENT = "assumed: single-sine equivalent of a multi-tone band (A = √(2·E_band), T energy-weighted)"
#: Points of the PSD the route returns for a chart.
PSD_POINTS = 512
#: The converter control each AS-02 storage technology is taken to run, a `smoothing.LATENCY_S` key; assumed:
#: a supercap and a high-C BESS behind a grid-forming converter, an energy BESS behind a grid-following one.
#: A technology not listed has no known control and `smoothing.response_latency` takes it as grid following.
TECHNOLOGY_CONTROL = {"supercap": "gfm_droop", "high_c_bess": "gfm_droop", "energy_bess": "gfl_pq"}


class TraceError(ValueError):
    """The upload is not a trace the spectrum can read, or the analysis was asked for something it cannot do."""


@dataclass(frozen=True)
class Trace:
    t_s: np.ndarray
    p_mw: np.ndarray


def _unit_token(column: str) -> str:
    tokens = [t for t in re.split(r"[_\s()\[\]/]+", column.strip().lower()) if t]
    return tokens[-1] if len(tokens) > 1 else ""


def _power_scale(column: str, unit: str | None) -> float:
    token = _unit_token(column)
    if token in NOT_POWER:
        raise TraceError(f"column '{column}' names unit '{token}', not an active-power unit (W, kW, MW or GW)")
    header = token if token in UNITS else None
    given = None
    if unit is not None and unit.strip():
        given = unit.strip().lower()
        if given not in UNITS:
            raise TraceError(f"unit '{unit}' is not an active-power unit; use W, kW, MW or GW")
    if header and given and header != given:
        raise TraceError(f"the header says '{token}' and the unit field says '{unit}': they disagree")
    if not (header or given):
        raise TraceError(f"no power unit: name it in the header (for example '{column}_mw') or the unit field")
    return UNITS[header or given]


def _seconds(raw: pd.Series, column: str) -> np.ndarray:
    token = _unit_token(column)
    if token not in ("", "s"):
        raise TraceError(f"time column '{column}' names unit '{token}'; time must be numeric seconds "
                         "(header 'time_s')")
    num = pd.to_numeric(raw, errors="coerce")
    if num.isna().all():
        raise TraceError(f"time column '{column}' is not numeric seconds; timestamps are not read, convert "
                         "them to seconds from the start")
    return num.to_numpy(float)


def read_trace(blob: bytes, unit: str | None = None) -> Trace:
    """A trace from a UTF-8 CSV or TSV export: the first column is time in numeric seconds, the second active
    power with its unit in the header suffix (`power_mw`) or `unit`. Refused, with the reason, when empty,
    binary, too short or long, not numeric, not strictly increasing, unevenly sampled, or in a wrong unit."""
    if b"\x00" in blob:
        raise TraceError("the upload is binary, not a CSV text file")
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TraceError(f"the upload is not UTF-8 text (byte {exc.start}); save it as a UTF-8 CSV") from exc
    if not text.strip():
        raise TraceError("the upload is empty")
    first = text.lstrip().splitlines()[0]
    sep = "\t" if "\t" in first else ";" if ";" in first and "," not in first else ","
    try:
        frame = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, skip_blank_lines=True)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise TraceError(f"the upload is not a readable CSV: {exc}") from exc
    if frame.shape[1] < 2:
        raise TraceError("the trace needs two columns: time, then active power")
    if frame.empty:
        raise TraceError("the trace is empty: a header but no rows")
    if len(frame) > MAX_SAMPLES:
        raise TraceError(f"the trace has {len(frame)} rows; at most {MAX_SAMPLES} are read, shorten or "
                         "downsample it")
    if len(frame) < MIN_SAMPLES:
        raise TraceError(f"the trace has {len(frame)} rows; it needs at least {MIN_SAMPLES}")
    t_col, p_col = str(frame.columns[0]), str(frame.columns[1])
    scale = _power_scale(p_col, unit)
    t = _seconds(frame.iloc[:, 0], t_col)
    p = pd.to_numeric(frame.iloc[:, 1], errors="coerce").to_numpy(float) * scale
    for values, col in ((t, t_col), (p, p_col)):
        bad = np.flatnonzero(~np.isfinite(values))
        if bad.size:
            raise TraceError(f"row {bad[0] + 1}: '{col}' is not a number ({bad.size} such row(s))")
    dt = np.diff(t)
    back = np.flatnonzero(dt <= 0)
    if back.size:
        raise TraceError(f"time must be strictly increasing; it is not at row {back[0] + 2} "
                         f"({t[back[0]]:g} s then {t[back[0] + 1]:g} s)")
    step = float(np.median(dt))
    worst = int(np.argmax(np.abs(dt - step)))
    if abs(dt[worst] - step) > JITTER * step:
        raise TraceError(f"the trace is not evenly sampled: {dt[worst]:g} s between rows {worst + 1} and "
                         f"{worst + 2} against a {step:g} s median; resample it to a fixed interval")
    peak = float(np.max(np.abs(p)))
    if peak > MAX_ABS_MW:
        raise TraceError(f"|P| reaches {peak:.3g} MW, implausible for one site; check the unit "
                         "(a trace in W labelled MW?)")
    return Trace(t, p)


def latency_bound_s(f_hz: float) -> float:
    """The slowest response that still cancels content at f: 1/(2π f), s. Also the reach of a latency, Hz."""
    return 1.0 / (2.0 * math.pi * f_hz)


def spectrum(t_s, p_mw) -> tuple[np.ndarray, np.ndarray]:
    """(f in Hz, one-sided PSD in MW²/Hz) of the mean-removed trace: one Hann-windowed segment."""
    from scipy.signal import welch

    p = np.asarray(p_mw, float)
    fs = 1.0 / float(np.median(np.diff(np.asarray(t_s, float))))
    return welch(p, fs=fs, window="hann", nperseg=p.size, detrend="constant", scaling="density")


def _df(f: np.ndarray) -> float:
    return float(f[1] - f[0])


def band_energy(f, psd, lo: float, hi: float) -> float:
    """∫ PSD df over lo <= f <= hi, MW²."""
    f, psd = np.asarray(f), np.asarray(psd)
    return float(psd[(f >= lo) & (f <= hi)].sum() * _df(f))


def corner_frequency(f, psd, lo: float, hi: float, limit: float) -> float | None:
    """f_c: the lowest band frequency above which at most `limit` of the band energy lies; None for no
    band energy."""
    f, psd = np.asarray(f), np.asarray(psd)
    m = (f >= lo) & (f <= hi)
    fb, eb = f[m], psd[m]
    total = eb.sum()
    if total <= 0:
        return None
    above = total - np.cumsum(eb)  # strictly above each bin
    return float(fb[np.flatnonzero(above <= limit * total)[0]])


def in_band_test(f, psd, latency_s: float, band=BAND_HZ, limit: float = IN_BAND_LIMIT) -> dict:
    """What a tier of latency τ leaves: the band energy above its reach 1/(2πτ), as a share of the band."""
    lo, hi = band
    reach = latency_bound_s(latency_s)
    total = band_energy(f, psd, lo, hi)
    left = band_energy(f, psd, max(lo, np.nextafter(reach, math.inf)), hi) if reach < hi else 0.0
    share = left / total if total > 0 else 0.0
    return {"latency_s": latency_s, "reach_hz": reach, "residual_share": share, "limit": limit,
            "passes": share <= limit}


def _check(band, limit) -> tuple[float, float]:
    lo, hi = (float(x) for x in band)
    if not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo < hi):
        raise TraceError(f"the band must be 0 < low < high Hz, got {lo:g}-{hi:g} Hz")
    if not (math.isfinite(limit) and 0 < limit < 1):
        raise TraceError(f"the in-band limit is a share between 0 and 1, got {limit:g}")
    return lo, hi


def _decimate(f: np.ndarray, psd: np.ndarray) -> tuple[list, list]:
    if f.size <= PSD_POINTS:
        return f.tolist(), psd.tolist()
    chunks = np.array_split(np.arange(f.size), PSD_POINTS)
    return [float(f[c].mean()) for c in chunks], [float(psd[c].mean()) for c in chunks]


def analyse(trace: Trace, band=BAND_HZ, limit: float = IN_BAND_LIMIT) -> dict:
    """The trace's PSD, band energy, f_c, equivalent oscillation and per-technology in-band test."""
    lo, hi = _check(band, limit)
    t, p = trace.t_s, trace.p_mw
    duration = float(t[-1] - t[0]) + float(np.median(np.diff(t)))
    fs = 1.0 / float(np.median(np.diff(t)))
    nyquist = fs / 2.0
    notes = []
    if nyquist <= lo:
        raise TraceError(f"sampled at {fs:g} Hz, its Nyquist frequency {nyquist:g} Hz is at or below the "
                         f"band's {lo:g} Hz; sample faster")
    if duration < MIN_PERIODS / lo:
        raise TraceError(f"the trace lasts {duration:g} s, too short to resolve {lo:g} Hz (needs "
                         f"{MIN_PERIODS / lo:g} s, {MIN_PERIODS} periods)")
    if nyquist < hi:
        notes.append(f"band clipped at the Nyquist frequency {nyquist:g} Hz (sampled at {fs:g} Hz)")
        hi = nyquist
    f, psd = spectrum(t, p)
    total = float(psd.sum() * _df(f))
    if total <= 1e-12 * max(1.0, float(np.mean(p)) ** 2):
        raise TraceError("the trace is flat: no fluctuation to analyse")
    m = (f >= lo) & (f <= hi)
    e_band = band_energy(f, psd, lo, hi)
    f_peak = float(f[m][np.argmax(psd[m])])
    period = float((psd[m] / f[m]).sum() / psd[m].sum()) if psd[m].sum() > 0 else 1.0 / f_peak
    f_c = corner_frequency(f, psd, lo, hi, limit)
    if m.sum() < MIN_BAND_BINS:
        raise TraceError(f"the band {lo:g}-{hi:g} Hz holds {int(m.sum())} PSD bin(s) of {_df(f):g} Hz; it needs "
                         f"{MIN_BAND_BINS}: use a longer trace or a wider band")
    tests = [{"technology": tech, **in_band_test(f, psd, _latency(tech)["latency_s"], (lo, hi), limit)}
             for tech in TECHNOLOGY_CONTROL]
    f_out, psd_out = _decimate(f, psd)
    return {
        "samples": int(p.size), "sample_hz": fs, "duration_s": duration, "band_hz": [lo, hi],
        "f_peak_hz": f_peak, "band_energy_mw2": e_band, "total_energy_mw2": total,
        "in_band_share": e_band / total, "limit": limit, "f_c_hz": f_c,
        "fast_latency_s": None if not f_c else latency_bound_s(f_c),
        "equivalent_oscillation": {"amplitude_mw": math.sqrt(2.0 * e_band), "period_s": period,
                                   "frequency_hz": 1.0 / period, "source": EQUIVALENT},
        "tests": tests, "notes": notes, "source": SOURCE,
        "psd": {"f_hz": f_out, "mw2_per_hz": psd_out},
    }


def replace_oscillation(envelope: dict, analysis: dict, cod: str | None = None) -> dict:
    """A copy of `envelope` whose phase at `cod` (every phase when None) takes the trace's equivalent (A, T)."""
    out = copy.deepcopy(envelope)
    phases = [ph for ph in out["phases"] if cod is None or str(ph.get("cod")) == str(cod)]
    if not phases:
        raise TraceError(f"the envelope has no phase with COD {cod}")
    eq = analysis["equivalent_oscillation"]
    for ph in phases:
        ph["oscillation"] = {**(ph.get("oscillation") or {}), "amplitude_mw": eq["amplitude_mw"],
                             "period_s": eq["period_s"], "frequency_hz": eq["frequency_hz"], "source": "trace",
                             "basis": EQUIVALENT}
    return out


def _latency(technology: str) -> dict:
    """`smoothing.response_latency` of a converter running the technology's assumed control."""
    return response_latency(technology, {"control": TECHNOLOGY_CONTROL.get(technology)})


def store_latency(n: pypsa.Network, name: str, override: float | None = None) -> tuple[float, str]:
    """(latency s, basis) of Store `name`: a given `override`, else its own `control` column when set, else
    its `storage_technology`'s assumed control (`TECHNOLOGY_CONTROL`); every control reads
    `smoothing.response_latency`, an unknown one as grid following. One source for every reader of a
    smoothing store's latency, so the tier split and the control-settings sheet cannot disagree."""
    if override is not None:
        return float(override), "given"
    control = n.stores.at[name, "control"] if "control" in n.stores.columns else None
    if isinstance(control, str) and control.strip():
        r = response_latency(name, {"control": control.strip()})
        return r["latency_s"], f"{r['basis']}, from the store's control {control.strip()}"
    tech = n.stores.at[name, "storage_technology"] if "storage_technology" in n.stores.columns else None
    r = _latency(tech)
    return r["latency_s"], f"{r['basis']}, assumed for {tech}"


def _overrides(latency: dict[str, float] | None, stores) -> dict[str, float]:
    out = {}
    for name, value in (latency or {}).items():
        if name not in stores:
            raise TraceError(f"latency override names '{name}', which is not a split storage on the network")
        v = float(value)
        if not (math.isfinite(v) and v > 0):
            raise TraceError(f"latency override for '{name}' must be a finite number > 0 s, got {value}")
        out[name] = v
    return out


def tier_split(n: pypsa.Network, f_c_hz: float, latency: dict[str, float] | None = None) -> dict:
    """Per AS-02 split storage: technology, latency and its basis, the bound 1/(2π f_c), whether it reaches f_c,
    and its `smoothing_tier` flag before and after the split (`before` None when never set). Changes nothing."""
    from services.solver.envelope_floors import STORE_COLUMNS
    from services.solver.split_storage import split_storage_tags

    if not (math.isfinite(f_c_hz) and f_c_hz > 0):
        raise TraceError(f"f_c must be > 0 Hz, got {f_c_hz}")
    tags = split_storage_tags(n)
    if tags is None:
        if latency:
            _overrides(latency, ())
        return {}
    override = _overrides(latency, tags.index)
    bound = latency_bound_s(f_c_hz)
    flag = STORE_COLUMNS[0]
    out = {}
    for name, tech in tags["storage_technology"].items():
        lat, basis = store_latency(n, name, override.get(name))
        old = n.stores.at[name, flag] if flag in n.stores.columns else math.nan
        eligible = lat <= bound
        reason = (f"responds in {lat:g} s, within 1/(2π f_c) = {bound:.3g} s" if eligible else
                  f"responds in {lat:g} s, slower than 1/(2π f_c) = {bound:.3g} s")
        out[name] = {"technology": tech, "latency_s": lat, "latency_basis": basis, "latency_bound_s": bound,
                     "eligible": eligible, "before": None if pd.isna(old) else bool(old), "after": eligible,
                     "reason": reason}
    return out


def apply_tier_split(n: pypsa.Network, f_c_hz: float, latency: dict[str, float] | None = None) -> dict:
    """Set AS-03's `smoothing_tier` flag on each split storage from `tier_split`: on when it reaches f_c, off
    when it does not, overwriting a flag set earlier (the table reports `before`). Other Stores are left alone."""
    from services.solver.envelope_floors import tag_store

    split = tier_split(n, f_c_hz, latency)
    if not split:
        raise TraceError("the network has no split storage (a Store with charger and discharger links) to put "
                         "in the smoothing tier")
    for name, row in split.items():
        tag_store(n, name, smoothing_tier=row["eligible"])
    return split
