"""
The sub-synchronous impedance scan at the point of connection (PoC)
(grid-compliance issue GC-08, TRGC 20).

R(f) and X(f) seen from the PoC for f = ``F_MIN_HZ``, ``F_MIN_HZ`` + df, ...,
f0 - ``F_MIN_HZ``, built per frequency from the pandapower tables the harmonic
scan reads (``harmonic_scan.branches``), with h = f / f0:

* cable and transformer: R + j h X in series (no skin term below f0), j h B / 2
  at each end of a cable;
* series capacitor (``series_caps``, ``(from bus, to bus, Xc ohm at f0)``):
  -j Xc / h;
* grid: c Un^2 / S_sc split by the case's R/X, R + j h X, a shunt at its bus
  (c as ``harmonic_scan.grid_c``);
* capacitor banks with every step in: j h B; shunt reactors: -j B / h.

A line of reactance X_L in series with a capacitor Xc = k X_L resonates at
f_er = f0 sqrt(k): X crosses zero from below.

**Resonance rows.** Every zero crossing of X from negative to non-negative is
a series resonance; its frequency f_er and R there are interpolated linearly
between scan points. A machine on the far side sees it at the complement
f0 - f_er, so the margin is the distance from f0 - f_er to the nearest given
torsional mode. Verdict ``flag`` when that margin is below the clearance (the
grid-code profile's ``subsync_torsional_clearance_hz``; without it
``DEFAULT_CLEARANCE_HZ``, marked ``assumed``), ``clear`` when modes are given
and the margin is at least the clearance, ``check`` when no mode is given. With
no series capacitor in the model a row ``not evaluated`` says grid-side series
compensation needs the TSP's data; it never reads ``clear``.

The network is passive (R, L, C; no converter impedance), so R at a crossing
is reported for information only. Sub-synchronous control interaction (SSCI)
needs the converter's own damping: one ``SSCI`` row per case reads ``not
evaluated``.

Screening only: the binding answer is the EMT study. Pure numpy and pandas.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static import harmonic_scan as hs

LABEL = "screening; binding answer is the EMT study"
F_MIN_HZ = 5.0
DF_HZ = 0.1
DEFAULT_CLEARANCE_HZ = 1.0
PROFILE_KEY = "subsync_torsional_clearance_hz"
SERIES = "series resonance"
SSCI = "SSCI"
NO_SERIES_CAP = "not evaluated: grid-side series compensation needs the TSP's data"
SSCI_NOTE = (
    "not evaluated: converter damping not modelled; binding answer is the EMT study"
)
NO_MODES = "no torsional modes given"
NO_CLEARANCE = "the grid code sets no torsional clearance; demo default"
SCAN_COLUMNS = ("case", "f_hz", "r_ohm", "x_ohm", "z_ohm", "label")
RESONANCE_COLUMNS = (
    "case",
    "kind",
    "f_er_hz",
    "f_comp_hz",
    "r_ohm",
    "machine",
    "mode_hz",
    "margin_hz",
    "clearance_hz",
    "verdict",
    "assumed",
    "note",
    "label",
)


@dataclasses.dataclass
class SubsyncStudy:
    scan: pd.DataFrame
    resonance: pd.DataFrame
    notes: list


def _finite(name: str, v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ContractError(
            f"sub-synchronous scan: {name} must be a number, got {v!r}"
        ) from None
    if not math.isfinite(x):
        raise ContractError(f"sub-synchronous scan: {name} must be finite, got {v!r}")
    return x


def frequencies(f0: float, df: float = DF_HZ) -> np.ndarray:
    """The scanned frequencies ``F_MIN_HZ``, ``F_MIN_HZ`` + df, ..., f0 - ``F_MIN_HZ``."""
    f0, df = _finite("f0", f0), _finite("df", df)
    if df <= 0 or f0 - F_MIN_HZ < F_MIN_HZ:
        raise ContractError(
            f"sub-synchronous scan: need df > 0 and f0 >= {2 * F_MIN_HZ}, got df={df}, f0={f0}"
        )
    return np.round(np.arange(F_MIN_HZ, f0 - F_MIN_HZ + df / 2, df), 9)


def z_poc(
    net,
    poc_bus: int,
    s_sc_mva: float,
    rx: float,
    fs: np.ndarray,
    series_caps=(),
    case: str = "normal",
    series=None,
) -> np.ndarray:
    """
    Complex Z seen from ``poc_bus`` (ohm at its voltage) at each frequency in ``fs`` for one grid
    case, every capacitor bank step in. ``series_caps`` are ``(from bus, to bus, Xc ohm at f0)``, Xc in
    ohm at the from bus's voltage.
    """
    f0 = float(net.f_hz)
    if series is None:
        series, _ = hs.branches(net, f0, 1.0)
    buses = list(hs._in_service(net.bus).index)
    at = {b: i for i, b in enumerate(buses)}
    if poc_bus not in at:
        raise ContractError(
            f"sub-synchronous scan: PoC bus {poc_bus} is not an in-service bus"
        )
    sbase, vn = float(net.sn_mva), net.bus["vn_kv"].astype(float)
    elems = [
        (a, b, r, x, 0.0, bsh) for a, b, r, x, bsh in series if a in at and b in at
    ]
    for a, b, xc in series_caps:
        if a not in at or b not in at:
            raise ContractError(
                f"sub-synchronous scan: series capacitor {a}-{b} is not between in-service buses"
            )
        xc = _finite("Xc", xc)
        if xc <= 0:
            raise ContractError(
                f"sub-synchronous scan: series capacitor {a}-{b} needs Xc > 0, got {xc}"
            )
        elems.append((a, b, 0.0, 0.0, xc / (vn[a] ** 2 / sbase), 0.0))
    part = hs._component(
        len(buses), [(at[a], at[b]) for a, b, *_ in elems], at[poc_bus]
    )
    pos = {buses[k]: i for i, k in enumerate(part)}
    m = len(part)
    h = np.asarray(fs, dtype=float) / f0
    y = np.zeros((len(h), m, m), dtype=complex)

    def shunt(bus, yh):
        if bus in pos:
            y[:, pos[bus], pos[bus]] += yh

    for a, b, r, x, xc, bsh in elems:
        if a not in pos:
            continue
        i, j = pos[a], pos[b]
        ys = 1.0 / (r + 1j * (h * x - xc / h))
        y[:, i, i] += ys + 0.5j * h * bsh
        y[:, j, j] += ys + 0.5j * h * bsh
        y[:, i, j] -= ys
        y[:, j, i] -= ys
    grid = 0
    for _, eg in hs._in_service(net.ext_grid).iterrows():
        bus = int(eg["bus"])
        if bus not in pos:
            continue
        zg = hs.grid_c(float(vn[bus]), case) * sbase / s_sc_mva
        xg = zg / math.sqrt(1.0 + rx**2)
        shunt(bus, 1.0 / (rx * xg + 1j * h * xg))
        grid += 1
    if not grid:
        raise ContractError("sub-synchronous scan: the PoC has no path to the grid")
    shunts = hs.switched_shunts(net)
    for i in shunts.index:
        steps = int(shunts.at[i, "max_step"])
        q, g = (
            float(shunts.at[i, "q_step_pu"]) * steps,
            float(shunts.at[i, "g_step_pu"]) * steps,
        )
        shunt(int(shunts.at[i, "bus"]), g - 1j * q * (h if q < 0 else 1.0 / h))
    e = np.zeros((len(h), m, 1), dtype=complex)
    e[:, pos[poc_bus], 0] = 1.0
    z = np.linalg.solve(y, e)[:, pos[poc_bus], 0]
    return z * float(vn[poc_bus]) ** 2 / sbase


def crossings(fs: np.ndarray, z: np.ndarray) -> list:
    """
    ``[(f_er, R there)]``: each zero crossing of X = Im z from negative to non-negative, interpolated
    linearly between scan points.
    """
    x, r = z.imag, z.real
    out = []
    for k in np.nonzero((x[:-1] < 0) & (x[1:] >= 0))[0]:
        t = -x[k] / (x[k + 1] - x[k])
        out.append(
            (
                float(fs[k] + t * (fs[k + 1] - fs[k])),
                float(r[k] + t * (r[k + 1] - r[k])),
            )
        )
    return out


def judge(margin_hz: float | None, clearance_hz: float) -> str:
    """``check`` with no mode given, ``flag`` when the margin is below the clearance, else ``clear``."""
    if margin_hz is None:
        return "check"
    return "flag" if margin_hz < clearance_hz else "clear"


def nearest_mode(
    f_hz: float, modes: dict
) -> tuple[str | None, float | None, float | None]:
    """``(machine, mode Hz, |f - mode|)`` of the given torsional mode nearest ``f_hz``; Nones without one."""
    pairs = [(m, f) for m, fl in modes.items() for f in fl]
    if not pairs:
        return None, None, None
    machine, fm = min(pairs, key=lambda p: (abs(f_hz - p[1]), p))
    return machine, fm, abs(f_hz - fm)


def _modes(modes: dict | None, f0: float) -> dict:
    out = {}
    for machine, fl in (modes or {}).items():
        vals = [_finite(f"torsional mode of {machine}", f) for f in fl]
        bad = [f for f in vals if not 0 < f < f0]
        if bad:
            raise ContractError(
                f"sub-synchronous scan: torsional modes of {machine} must lie in (0, {f0}) Hz, "
                f"got {bad}"
            )
        out[str(machine)] = vals
    return out


def clearance_from_profile(profile: dict | None) -> tuple[float | None, list]:
    """
    The profile's ``subsync_torsional_clearance_hz`` and where it comes from: ``(value or None,
    [{"key", "clause", "source"}])``.
    """
    lim = (profile or {}).get(PROFILE_KEY)
    if not isinstance(lim, dict):
        return None, []
    return _finite(PROFILE_KEY, lim["value"]), [
        {"key": PROFILE_KEY, "clause": lim.get("clause"), "source": lim.get("source")}
    ]


def subsync_scan(
    net,
    poc_bus: int,
    cases: dict,
    *,
    series_caps=(),
    torsional_modes: dict | None = None,
    clearance_hz: float | None = None,
    clearance_assumed: bool = False,
    notes=(),
    df: float = DF_HZ,
) -> SubsyncStudy:
    """
    The scan of ``net`` from ``poc_bus`` for each case in ``cases`` (``{case: (s_sc_mva, rx)}``).
    ``torsional_modes`` is ``{machine: [Hz, ...]}``. ``clearance_hz`` None takes ``DEFAULT_CLEARANCE_HZ``
    and marks every resonance row ``assumed``, as does ``clearance_assumed``. ``notes`` are carried onto
    the resonance rows.
    """
    f0 = float(net.f_hz)
    fs = frequencies(f0, df)
    modes = _modes(torsional_modes, f0)
    limit = (
        DEFAULT_CLEARANCE_HZ
        if clearance_hz is None
        else _finite("clearance", clearance_hz)
    )
    if limit <= 0:
        raise ContractError(f"sub-synchronous scan: clearance must be > 0, got {limit}")
    assumed = clearance_hz is None or clearance_assumed
    series, line_notes = hs.branches(net, f0, 1.0)
    notes = [*notes, *line_notes]
    base = {"clearance_hz": limit, "assumed": assumed, "label": LABEL}

    def note(*extra):
        return "; ".join(
            filter(None, [*extra, NO_CLEARANCE if clearance_hz is None else "", *notes])
        )

    frames, res = [], []
    for case, (s_sc, rx) in cases.items():
        s_sc = _finite(f"S_sc of case {case}", s_sc)
        rx = _finite(f"R/X of case {case}", rx)
        if s_sc <= 0 or rx < 0:
            raise ContractError(
                f"sub-synchronous scan: case {case} needs S_sc > 0 and R/X >= 0, got {s_sc}, {rx}"
            )
        z = z_poc(net, poc_bus, s_sc, rx, fs, series_caps, case, series)
        frames.append(
            pd.DataFrame(
                {
                    "case": case,
                    "f_hz": fs,
                    "r_ohm": z.real,
                    "x_ohm": z.imag,
                    "z_ohm": np.abs(z),
                    "label": LABEL,
                }
            )
        )
        for f_er, r in crossings(fs, z):
            comp = f0 - f_er
            machine, fm, margin = nearest_mode(comp, modes)
            res.append(
                {
                    "case": case,
                    "kind": SERIES,
                    "f_er_hz": f_er,
                    "f_comp_hz": comp,
                    "r_ohm": r,
                    "machine": machine,
                    "mode_hz": fm,
                    "margin_hz": margin,
                    "verdict": judge(margin, limit),
                    "note": note("" if modes else NO_MODES),
                    **base,
                }
            )
        if not series_caps:
            res.append(
                {
                    "case": case,
                    "kind": SERIES,
                    "verdict": "not evaluated",
                    "note": note(NO_SERIES_CAP),
                    **base,
                }
            )
        res.append(
            {
                "case": case,
                "kind": SSCI,
                "verdict": "not evaluated",
                "note": SSCI_NOTE,
                **base,
            }
        )
    scan = (
        pd.concat(frames, ignore_index=True).reindex(columns=list(SCAN_COLUMNS))
        if frames
        else pd.DataFrame(columns=list(SCAN_COLUMNS))
    )
    return SubsyncStudy(
        scan=scan,
        resonance=pd.DataFrame(res, columns=list(RESONANCE_COLUMNS)),
        notes=notes,
    )
