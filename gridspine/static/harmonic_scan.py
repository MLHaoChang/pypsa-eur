"""The harmonic impedance scan at the point of connection (PoC)
(datacenter grid readiness issue 12, part 3).

The harmonic screen (``harmonics.py``) takes the grid as Z_sc = U^2 / S_k at
every order. This scan shows what that leaves out: the campus network's own
frequency response, seen from the PoC, with its cables' capacitance and its
capacitor banks, which can form a parallel resonance with the grid:

    Z_poc(h) = [Y(h)^-1]_poc,poc                  (ohm at the PoC voltage)
    f        = h * net.f_hz,  h = 1, 1 + dh, ..., h_max

Y(h) is built here from the pandapower tables, per order h:

* cable: lumped pi, R sqrt(h) + j h X in series (the sqrt(h) term is skin
  effect), j h B / 2 at each end, B = 2 pi f0 C;
* transformer: R sqrt(h) + j h X from vk / vkr at its rated ratio; no
  magnetising branch;
* grid: c Un^2 / S_sc (IEC 60909's equivalent source, c as
  ``island_sc_seq.c_factor``; 1.0 in the normal case) split by the case's
  R/X, R sqrt(h) + j h X, a shunt at its bus;
* capacitor bank: j h B per step in service; shunt reactor: -j B / h.

So one grid and one bank of Q_c at the PoC resonate at h_r = sqrt(S_sc / (c Q_c)).

**Cases.** One scan per short-circuit case (``max``, ``min``, ``normal``) and
per capacitor state: each step count 0..max_step of every switched shunt,
since a part-switched bank is often the state that lands on a characteristic
order. With more combinations than ``MAX_CAP_STATES``, all-in, all-out and
each shunt stepped alone with the others in are scanned, and a note says so.

**Resonance rows.** A local maximum of |Z| counts only when it stands at least
``PEAK_RATIO`` times above the higher of its two neighbouring minima (no load
damping is modelled, so every ripple is a local maximum). Its margin is the
distance in orders to the nearest order that counts: 6k +- 1, plus every order
a unit's spectrum injects. Verdict ``flag`` when the margin is below the
grid code's ``harmonic_resonance_clearance_h``, else ``clear``; without that
key the limit is ``DEFAULT_CLEARANCE_H`` and the row is ``assumed``.

**Envelope.** For each whole order h, the bounds of R, X and |Z| over every
case and capacitor state and every scan point within h +- 0.5.

Screening only: filter design stays in a harmonic study tool.

Pure numpy and pandas; no engine import.
"""
from __future__ import annotations

import dataclasses
import itertools
import math

import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static.harmonics import H_MAX
from gridspine.static.island_sc_seq import c_factor

DH = 0.1
#: Short-circuit cases in the order they are scanned.
CASES = ("max", "min", "normal")
#: Above this many capacitor states the scan falls back to all-in, all-out and one shunt stepped at a time.
MAX_CAP_STATES = 64
#: A peak counts only when |Z| stands this many times above the higher of its neighbouring minima.
PEAK_RATIO = 1.5
#: A cable longer than this fraction of a quarter wavelength at h_max * f0 is named in a note.
PI_FRACTION = 0.4
#: The resonance clearance, in orders, when the grid code sets none (demo value).
DEFAULT_CLEARANCE_H = 0.5
TOLERANCE = 1e-9

LABEL = "screening; filter design and the binding answer are a harmonic study with the grid's own impedance loci"

LEDGER = (
    "loads, converters and gensets are left out: no damping, so a peak's height is conservative, and no "
    "detuning by motor or load reactance, so its frequency is approximate",
    f"a resonance peak counts when |Z| stands at least {PEAK_RATIO} times above the higher of its "
    "neighbouring minima (PEAK_RATIO)",
    f"each cable is one lumped pi, valid while it is short against a quarter wavelength at h_max * f0; a "
    f"cable longer than {PI_FRACTION} of that is named in a note (PI_FRACTION)",
    "R rises with sqrt(h) (skin effect) on cables, transformers and the grid; X with h",
    "the grid is IEC 60909's equivalent source c Un^2 / S_sc (c = 1.1 in the max case, 1.0 in the min "
    "case above 1 kV, as island_sc_seq.c_factor), so h_r = sqrt(S_sc / (c Q_c)); the normal case takes c = "
    "1.0 (assumed)",
    "transformers at their rated ratio (no off-nominal correction where a bus's kV differs from the "
    "winding's), without the magnetising branch",
    "a peak at or above h_max is not reported (the scan has no point beyond it)",
    "a cable drafted from a PyPSA project converts b to C at a fixed European system frequency "
    "(gridspine/producers/campus.py), so on a 60 Hz project its C is 1.2 times too high until that is fixed",
)

SCAN_COLUMNS = ("case", "cap_state", "h", "f_hz", "r_ohm", "x_ohm", "z_ohm", "label")
ENVELOPE_COLUMNS = ("h", "f_hz", "r_min_ohm", "r_max_ohm", "x_min_ohm", "x_max_ohm", "z_max_ohm", "points",
                    "note", "label")
RESONANCE_COLUMNS = ("case", "cap_state", "h_r", "f_hz", "z_peak_ohm", "prominence", "nearest_order",
                     "order_set", "margin_h", "limit_h", "verdict", "assumed", "note", "label")

CHARACTERISTIC = "6k±1"
SPECTRUM = "unit spectrum"
SPECTRUM_DEMO = "demo unit spectrum"
NO_NORMAL = "no normal case given (pcc.sk_normal_mva), so it is not scanned"


@dataclasses.dataclass
class ScanStudy:
    scan: pd.DataFrame
    envelope: pd.DataFrame
    resonance: pd.DataFrame
    notes: list


def orders(h_max: float = H_MAX, dh: float = DH) -> np.ndarray:
    """The scanned orders 1, 1 + dh, ..., h_max."""
    if dh <= 0 or h_max < 1:
        raise ContractError(f"harmonic scan: need dh > 0 and h_max >= 1, got dh={dh}, h_max={h_max}")
    return np.round(np.arange(1.0, h_max + dh / 2, dh), 9)


def characteristic_orders(h_max: float = H_MAX) -> set:
    """6k +- 1 up to ``h_max``."""
    return {h for k in range(1, int(h_max) // 6 + 2) for h in (6 * k - 1, 6 * k + 1) if h <= h_max}


def _in_service(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["in_service"].astype(bool)] if "in_service" in df.columns else df


def _branches(net, f0: float, h_max: float = H_MAX):
    """``(series, notes)``: each in-service cable and transformer as ``(from bus, to bus, r pu, x pu,
    b pu)`` on ``net.sn_mva`` and the bus voltages, at the fundamental."""
    sbase, vn = float(net.sn_mva), net.bus["vn_kv"].astype(float)
    series, notes = [], []
    f_top = h_max * f0
    for _, ln in _in_service(net.line).iterrows():
        a, b = int(ln["from_bus"]), int(ln["to_bus"])
        length, par = float(ln["length_km"]), float(ln.get("parallel", 1) or 1)
        zbase = vn[a] ** 2 / sbase
        r = float(ln["r_ohm_per_km"]) * length / par / zbase
        x = float(ln["x_ohm_per_km"]) * length / par / zbase
        c = float(ln["c_nf_per_km"]) * 1e-9
        bsh = 2 * math.pi * f0 * c * length * par * zbase
        series.append((a, b, r, x, bsh))
        l_h = float(ln["x_ohm_per_km"]) / (2 * math.pi * f0)
        if c > 0 and l_h > 0:
            quarter = 1.0 / math.sqrt(l_h * c) / (4 * f_top)
            if length > PI_FRACTION * quarter:
                notes.append(f"cable {ln['name']} ({length:g} km) is longer than {PI_FRACTION} of a quarter "
                             f"wavelength at {f_top:g} Hz ({quarter:.3g} km): one lumped pi understates its "
                             f"resonances")
    for _, tr in _in_service(net.trafo).iterrows():
        a, b = int(tr["hv_bus"]), int(tr["lv_bus"])
        par = float(tr.get("parallel", 1) or 1)
        scale = sbase / float(tr["sn_mva"]) * (float(tr["vn_hv_kv"]) / vn[a]) ** 2 / par
        zk, rk = float(tr["vk_percent"]) / 100 * scale, float(tr["vkr_percent"]) / 100 * scale
        series.append((a, b, rk, math.sqrt(max(zk ** 2 - rk ** 2, 0.0)), 0.0))
    return series, notes


def switched_shunts(net) -> pd.DataFrame:
    """The in-service shunts (capacitor banks and reactors), index the shunt: ``name``, ``bus``,
    ``max_step``, ``q_step_pu`` (load convention: negative is capacitive) on ``net.sn_mva`` at the bus
    voltage, ``g_step_pu``."""
    sh = _in_service(net.shunt)
    if sh.empty:
        return pd.DataFrame(columns=["name", "bus", "max_step", "q_step_pu", "g_step_pu"])
    vn = net.bus["vn_kv"].astype(float)
    sbase = float(net.sn_mva)
    out = pd.DataFrame(index=sh.index)
    out["name"] = sh["name"].astype(str)
    out["bus"] = sh["bus"].astype(int)
    out["max_step"] = sh["max_step"].astype(int) if "max_step" in sh.columns else 1
    ratio = (vn[out["bus"]].to_numpy() / sh["vn_kv"].astype(float).to_numpy()) ** 2 \
        if "vn_kv" in sh.columns else 1.0
    out["q_step_pu"] = sh["q_mvar"].astype(float).to_numpy() * ratio / sbase
    out["g_step_pu"] = sh["p_mw"].astype(float).to_numpy() * ratio / sbase
    return out


def cap_states(shunts: pd.DataFrame) -> tuple[list, list]:
    """``(states, notes)``: each state ``{shunt: steps in service}``. Every combination of 0..max_step
    when there are at most ``MAX_CAP_STATES``; else all-in, all-out and each shunt stepped through its
    steps alone with the others in."""
    if shunts.empty:
        return [{}], []
    idx = list(shunts.index)
    ranges = [range(int(shunts.at[i, "max_step"]) + 1) for i in idx]
    count = math.prod(len(r) for r in ranges)
    if count <= MAX_CAP_STATES:
        return [dict(zip(idx, combo)) for combo in itertools.product(*ranges)], []
    full = {i: int(shunts.at[i, "max_step"]) for i in idx}
    states = [full, {i: 0 for i in idx}]
    for i in idx:
        for s in range(int(shunts.at[i, "max_step"])):
            if {**full, i: s} not in states:
                states.append({**full, i: s})
    note = (f"{count} capacitor states exceed MAX_CAP_STATES ({MAX_CAP_STATES}): scanned all in, all out and "
            f"each shunt stepped alone with the others in ({len(states)} states)")
    return states, [note]


def state_label(shunts: pd.DataFrame, state: dict) -> str:
    """``"C1=2/3;R1=0/1"``, or ``"none"`` without a switched shunt."""
    if not state:
        return "none"
    return ";".join(f"{shunts.at[i, 'name']}={s}/{int(shunts.at[i, 'max_step'])}" for i, s in state.items())


def _component(n: int, edges, start: int) -> list:
    adj = {i: set() for i in range(n)}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen, stack = {start}, [start]
    while stack:
        for j in adj[stack.pop()]:
            if j not in seen:
                seen.add(j)
                stack.append(j)
    return sorted(seen)


def grid_c(vn_kv: float, case: str) -> float:
    """IEC 60909's voltage factor c for the grid's equivalent source in ``case``; the normal case
    (not an IEC 60909 case) takes 1.0."""
    return c_factor(vn_kv, case) if case in ("max", "min") else 1.0


def z_poc(net, poc_bus: int, s_sc_mva: float, rx: float, state: dict, shunts: pd.DataFrame,
          hs: np.ndarray, series=None, case: str = "normal") -> np.ndarray:
    """Complex Z seen from ``poc_bus`` (ohm at its voltage) at each order in ``hs``, for one grid case
    and one capacitor state. ``series`` is ``_branches``'s, when already built. ``case`` sets the
    grid's voltage factor c (``grid_c``)."""
    if series is None:
        series, _ = _branches(net, float(net.f_hz))
    buses = list(_in_service(net.bus).index)
    at = {b: i for i, b in enumerate(buses)}
    if poc_bus not in at:
        raise ContractError(f"harmonic scan: PoC bus {poc_bus} is not an in-service bus")
    series = [s for s in series if s[0] in at and s[1] in at]
    part = _component(len(buses), [(at[a], at[b]) for a, b, *_ in series], at[poc_bus])
    pos = {buses[k]: i for i, k in enumerate(part)}
    m, sbase = len(part), float(net.sn_mva)
    hs = np.asarray(hs, dtype=float)
    sq = np.sqrt(hs)
    y = np.zeros((len(hs), m, m), dtype=complex)

    def shunt(bus, yh):
        if bus in pos:
            y[:, pos[bus], pos[bus]] += yh

    for a, b, r, x, bsh in series:
        if a not in pos:
            continue
        i, j = pos[a], pos[b]
        ys = 1.0 / (r * sq + 1j * hs * x)
        y[:, i, i] += ys + 0.5j * hs * bsh
        y[:, j, j] += ys + 0.5j * hs * bsh
        y[:, i, j] -= ys
        y[:, j, i] -= ys
    grid = 0
    for _, eg in _in_service(net.ext_grid).iterrows():
        bus = int(eg["bus"])
        if bus not in pos:
            continue
        zg = grid_c(float(net.bus.at[bus, "vn_kv"]), case) * sbase / s_sc_mva
        xg = zg / math.sqrt(1.0 + rx ** 2)
        shunt(bus, 1.0 / (rx * xg * sq + 1j * hs * xg))
        grid += 1
    if not grid:
        raise ContractError("harmonic scan: the PoC has no path to the grid")
    for i, steps in state.items():
        if not steps:
            continue
        q = float(shunts.at[i, "q_step_pu"]) * steps
        g = float(shunts.at[i, "g_step_pu"]) * steps
        # load convention: Y = (P - jQ) / V^2; a capacitor's susceptance rises with h, a reactor's falls
        shunt(int(shunts.at[i, "bus"]), g - 1j * q * (hs if q < 0 else 1.0 / hs))
    e = np.zeros((len(hs), m, 1), dtype=complex)
    e[:, pos[poc_bus], 0] = 1.0
    z = np.linalg.solve(y, e)[:, pos[poc_bus], 0]
    return z * float(net.bus.at[poc_bus, "vn_kv"]) ** 2 / sbase


def peaks(z: np.ndarray, ratio: float = PEAK_RATIO) -> list:
    """``[(index, prominence)]`` of each local maximum of ``z`` standing at least ``ratio`` times above
    the higher of its neighbouring minima (the lowest point on each side before a higher one)."""
    out = []
    for i in range(1, len(z) - 1):
        if not (z[i] > z[i - 1] and z[i] >= z[i + 1]):
            continue
        j, lo = i - 1, z[i]
        while j >= 0 and z[j] <= z[i]:
            lo = min(lo, z[j])
            j -= 1
        k, ro = i + 1, z[i]
        while k < len(z) and z[k] <= z[i]:
            ro = min(ro, z[k])
            k += 1
        base = max(lo, ro)
        if base > 0 and z[i] / base >= ratio - TOLERANCE:
            out.append((i, float(z[i] / base)))
    return out


def nearest_order(h_r: float, spectrum_orders=(), h_max: float = H_MAX, demo: bool = False) -> tuple[int, str]:
    """``(order, which set)``: the order that counts nearest to ``h_r``, from 6k +- 1 and the units'
    spectra; the set names where that order comes from (``demo``: the spectra include a topology's
    demo default)."""
    char, spec = characteristic_orders(h_max), {int(h) for h in spectrum_orders}
    best = min(char | spec, key=lambda h: (abs(h - h_r), h))
    sets = [name for name, s in ((CHARACTERISTIC, char), (SPECTRUM_DEMO if demo else SPECTRUM, spec))
            if best in s]
    return best, " and ".join(sets)


def judge(margin_h: float, limit_h: float) -> str:
    """``flag`` when the resonance is closer than ``limit_h`` to an order that counts, else ``clear``."""
    return "clear" if margin_h >= limit_h - TOLERANCE else "flag"


def envelope(scan: pd.DataFrame, f0: float, h_max: float = H_MAX) -> pd.DataFrame:
    """Per whole order h, the bounds of R, X and |Z| over every scan row within h +- 0.5."""
    rows = []
    for h in range(1, int(h_max) + 1):
        near = scan[(scan["h"] >= h - 0.5 - TOLERANCE) & (scan["h"] <= h + 0.5 + TOLERANCE)]
        if near.empty:
            continue
        rows.append({"h": h, "f_hz": h * f0, "r_min_ohm": near["r_ohm"].min(), "r_max_ohm": near["r_ohm"].max(),
                     "x_min_ohm": near["x_ohm"].min(), "x_max_ohm": near["x_ohm"].max(),
                     "z_max_ohm": near["z_ohm"].max(), "points": len(near)})
    return pd.DataFrame(rows, columns=[c for c in ENVELOPE_COLUMNS if c not in ("note", "label")])


def impedance_scan(net, poc_bus: int, cases: dict, *, spectrum_orders=(), spectrum_demo: bool = False,
                   limit_h: float | None = None, limit_assumed: bool = False, notes=(), h_max: float = H_MAX,
                   dh: float = DH) -> ScanStudy:
    """The scan of ``net`` from ``poc_bus`` for each case in ``cases`` (``{case: (s_sc_mva, rx)}``) and
    each capacitor state. ``limit_h`` is the grid code's clearance; None takes ``DEFAULT_CLEARANCE_H``
    and marks every resonance row ``assumed``, as does ``limit_assumed`` (a limit that is not the
    code's). A row whose nearest order is only in a demo spectrum (``spectrum_demo``) is ``assumed``
    too. ``notes`` are carried onto the envelope and resonance rows."""
    f0 = float(net.f_hz)
    hs = orders(h_max, dh)
    shunts = switched_shunts(net)
    states, state_notes = cap_states(shunts)
    series, line_notes = _branches(net, f0, h_max)
    notes = [*notes, *state_notes, *line_notes]
    note = "; ".join(notes)
    limit = DEFAULT_CLEARANCE_H if limit_h is None else float(limit_h)
    assumed = limit_h is None or limit_assumed

    frames, res = [], []
    for case, (s_sc, rx) in cases.items():
        if not (s_sc and s_sc > 0) or rx is None or rx < 0:
            raise ContractError(f"harmonic scan: case {case} needs S_sc > 0 and R/X >= 0, got {s_sc}, {rx}")
        for state in states:
            label = state_label(shunts, state)
            z = z_poc(net, poc_bus, float(s_sc), float(rx), state, shunts, hs, series, case)
            mag = np.abs(z)
            frames.append(pd.DataFrame({"case": case, "cap_state": label, "h": hs, "f_hz": hs * f0,
                                        "r_ohm": z.real, "x_ohm": z.imag, "z_ohm": mag, "label": LABEL}))
            for i, prom in peaks(mag):
                h_r = float(hs[i])
                order, which = nearest_order(h_r, spectrum_orders, h_max, spectrum_demo)
                margin = abs(h_r - order)
                res.append({"case": case, "cap_state": label, "h_r": h_r, "f_hz": h_r * f0,
                            "z_peak_ohm": float(mag[i]), "prominence": prom, "nearest_order": order,
                            "order_set": which, "margin_h": margin, "limit_h": limit,
                            "verdict": judge(margin, limit), "assumed": assumed or which == SPECTRUM_DEMO,
                            "note": "; ".join(filter(None, [
                                "the grid code sets no resonance clearance; demo default" if limit_h is None
                                else "",
                                note])),
                            "label": LABEL})
    scan = pd.concat(frames, ignore_index=True).reindex(columns=list(SCAN_COLUMNS)) if frames \
        else pd.DataFrame(columns=list(SCAN_COLUMNS))
    env = envelope(scan, f0, h_max)
    env["note"], env["label"] = note, LABEL
    return ScanStudy(scan=scan, envelope=env.reindex(columns=list(ENVELOPE_COLUMNS)),
                     resonance=pd.DataFrame(res, columns=list(RESONANCE_COLUMNS)), notes=notes)


def campus_cases(campus) -> tuple[int, dict, list]:
    """``(PoC bus index, {case: (s_sc_mva, rx)}, notes)`` of a built campus: max and min from its
    grid, normal from the optional ``pcc.sk_normal_mva`` / ``rx_normal`` (not scanned without it)."""
    net = campus.net
    eg = _in_service(net.ext_grid)
    if eg.empty:
        raise ContractError("harmonic scan: the campus has no grid connection")
    eg = eg.iloc[0]
    bus = int(eg["bus"])
    cases = {"max": (float(eg["s_sc_max_mva"]), float(eg["rx_max"])),
             "min": (float(eg["s_sc_min_mva"]), float(eg["rx_min"]))}
    p = campus.params
    pcc = str(net.bus.at[bus, "name"])

    def param(name):
        row = p[(p["element"] == pcc) & (p["param"] == name)]
        return float(row["value"].iloc[0]) if len(row) else None

    sk, rx = param("sk_normal_mva"), param("rx_normal")
    if sk is None:
        return bus, cases, [NO_NORMAL]
    cases["normal"] = (sk, rx)
    return bus, {c: cases[c] for c in CASES if c in cases}, []


def clearance_from_profile(profile: dict | None) -> tuple[float | None, list]:
    """The profile's ``harmonic_resonance_clearance_h`` and where it comes from:
    ``(value or None, [{"key", "clause", "source"}])``."""
    lim = (profile or {}).get("harmonic_resonance_clearance_h")
    if not isinstance(lim, dict):
        return None, []
    return float(lim["value"]), [{"key": "harmonic_resonance_clearance_h", "clause": lim.get("clause"),
                                  "source": lim.get("source")}]
