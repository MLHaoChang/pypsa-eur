"""First-pass island frequency check: aggregated inertia and droop (plan I6).

Plan: ``docs/superpowers/plans/2026-10-07-campus-island-operation.md``.

**The model**, one bus, every unit at the island's single frequency:

    M(t) dΔf/dt = Σ_i ΔP_i(t) − ΔP_event(t) − D·P_load·Δf/f0

with Δf in Hz, powers in MW and M in MW·s/Hz:

* an online **genset** adds 2·H·S_n/f0 to M once joined, and its governor
  ΔP_m: T_g dΔP_m/dt = −K·Δf − ΔP_m with K = P_n/(R·f0); an
  ``isochronous`` governor adds an integral, −K·(Δf + ∫Δf dt / T_i), so Δf
  settles at 0. ΔP_m is held to the load-acceptance step plus the ramp since
  it joined, and P0 + ΔP_m to [0, P_n]. A cold genset (``seamless``) joins
  at ``start_s`` + ``sync_s`` from P = 0;
* a **droop GFM**, in its native form, filters its measured power
  (τ_f dP_f/dt = P − P_f) and imposes f = f0 − R·f0·(P_f − P0)/P_n. On the
  shared bus that is P = P0 − K·(Δf + τ_f·dΔf/dt), so it adds K·τ_f to M
  and −K·Δf to ΣΔP. ``native_droop_reference`` codes the filter and the
  imposed frequency directly, for the cross-check;
* a **VSM** adds 2·H_v·S_n/f0 to M and −K·Δf (its steady-state gain 1/R
  about ω0 is the droop; no separate damping);
* a GFM beyond its P range or its current limit (i_max·S_n, in MW: a
  frequency screening sees only P) becomes a constant-P source at the limit,
  and its M and K go. With no machine joined and every GFM at a limit the
  result is ``COLLAPSE``, not a number;
* a **frequency-watt** GFL unit: −K_fw·sign(Δf)·max(0, |Δf| − db) after its
  delay, ramp-limited, capped at ``fw_p_limit_mw`` and at its headroom (PV
  only curtails: it has no upward reserve);
* a **PQ** GFL unit does not respond;
* a **UPS** (plan I6b) carries its IT load ``it_mw`` on its battery when it
  is off line: at an islanding if it ``transfers``, and whenever |Δf| leaves
  its input window ``f_window_hz`` (a drop-out, logged). Back inside the
  window it walks its load back in at ``it_mw / walk_in_s``. The IT load on
  the battery is relief to the island balance; its energy is counted against
  the UPS's stored energy (``e_mwh``, e(t−1) from I3a).

The load damping D defaults to 0: a data centre is mostly constant power, so
that is the conservative choice (ledgered).

**Labelled a screening** (``LABEL``): no voltage dynamics, no inter-unit
oscillation, no current-limit dynamics.

The engine is numpy only; no pandapower. ``units_from_campus`` reads I4's
unit plan so both share one P0 and one K = P_n/R (plan N1).

**I6b** adds E4 (``e4_ups_bridged``: the dead-bus pickup and the Q1 bridge
check), E6 (``e6_planned``: the ramp time), the Q8 rerun
(``i_max_sensitivity``), the EMT/RMS flags (``emt_flags``), and the sweeps:
``e1_every_hour`` on I3a's lossless steps and ``campus_frequency_study`` at
the island critical hours (I4's ΔP_AC, pandapower through I4 and I5).
"""
import dataclasses
import math

import numpy as np

LABEL = "frequency screening (single-bus aggregate; no voltage dynamics)"
COLLAPSE = "frequency collapse: no grid-forming headroom"
F0 = 50.0
#: An isochronous governor's integral time and, without its own droop_pct,
#: its proportional droop (assumed, ledgered).
ISO_TI_S = 2.0
ISO_DROOP = 0.04
LEDGER = (
    "frequency: load damping D = 0 by default (a data centre is mostly constant power; conservative)",
    f"frequency: an isochronous governor is a PI on Δf with T_i = {ISO_TI_S:g} s and, without droop_pct, "
    f"a {ISO_DROOP:.0%} proportional droop (assumed)",
    "frequency: a GFM's current limit is i_max · S_n taken as MW; the screening sees active power only",
    "frequency: PV frequency-watt only curtails; it carries no upward reserve",
    "frequency: a UPS walks its IT load back in linearly over walk_in_s, from the islanding or from the "
    "moment the frequency is back inside its window; it drops off again whenever |Δf| leaves the window",
)


@dataclasses.dataclass(frozen=True)
class Unit:
    """One unit of the island, as the frequency model sees it. ``model`` is
    ``genset``, ``gfm_droop``, ``gfm_vsm``, ``gfl_fw``, ``ups`` or
    ``none``. Powers in MW; ``r`` is the droop in pu on P_n; ``h`` H on S_n
    (a genset's H, a VSM's H_v). A genset's ``start_s`` + ``sync_s`` is its
    join time when cold (``ready_s``). A UPS's ``ups`` holds ``it_mw``,
    ``walk_in_s``, ``f_window_hz``, ``transfers`` and ``e_mwh``."""
    name: str
    model: str
    p0: float
    p_n: float
    s_n: float = math.nan
    p_min: float = 0.0
    p_max: float = math.nan
    r: float = math.nan
    h: float = 0.0
    tau_f: float = 0.0
    t_g: float = 0.5
    isochronous: bool = False
    step_mw: float = math.inf
    ramp_mw_per_s: float = math.inf
    join_s: float = 0.0
    i_max_mw: float = math.inf
    fw: dict = dataclasses.field(default_factory=dict)
    start_s: float = 0.0
    sync_s: float = 0.0
    ups: dict = dataclasses.field(default_factory=dict)

    @property
    def ready_s(self):
        """A cold genset's start plus synchronisation, s: when it can join."""
        return self.start_s + self.sync_s

    @property
    def k(self):
        """The droop gain P_n / (R·f0), MW/Hz (at F0)."""
        return self.p_n / (self.r * F0) if self.r == self.r and self.r else 0.0

    @property
    def hi(self):
        return min(self.p_max if self.p_max == self.p_max else self.p_n, self.i_max_mw)

    @property
    def lo(self):
        return max(self.p_min, -self.i_max_mw)


@dataclasses.dataclass
class Trace:
    t: np.ndarray
    df: np.ndarray               # Hz
    dp: dict                     # unit -> ΔP from P0 over t, MW
    saturated: dict              # unit -> first time at a limit, s (absent: never)
    collapse: float | None       # the time of collapse, s, or None
    rocof0: float                # dΔf/dt at 0⁺, Hz/s
    ups: dict = dataclasses.field(default_factory=dict)    # UPS -> UpsLog
    label: str = LABEL


@dataclasses.dataclass
class UpsLog:
    """What a UPS did in the event: when it dropped off line (window
    violations), the energy its battery gave, and when its IT load was back
    on the island (``None``: still on its battery at the end)."""
    drops: list
    energy_mwh: float
    t_online_s: float | None
    e_mwh: float

    @property
    def autonomy_ok(self):
        """The battery held out, and the load is back on the island."""
        return self.t_online_s is not None and self.energy_mwh <= self.e_mwh + 1e-12


def _gfm_m(u, f0):
    if u.model == "gfm_droop":
        return u.p_n * u.tau_f / (u.r * f0)
    return 2.0 * u.h * u.s_n / f0                                      # VSM


def _balance(units, x, t, dp_event, f0, damping):
    """``(dΔf/dt, ΔP of each GFM, saturated GFMs)`` at state ``x``; the
    saturated set is found by iteration (a GFM beyond a limit holds it)."""
    df = x["df"]
    gfm = [u for u in units if u.model in ("gfm_droop", "gfm_vsm")]
    sat = set()
    for _ in range(len(gfm) + 1):
        m, p = 0.0, -dp_event(t) - damping * df / f0
        for u in units:
            if u.model == "genset" and t >= u.join_s:
                m += 2.0 * u.h * u.s_n / f0
                p += x[u.name]
            elif u.model in ("gfl_fw", "ups"):
                p += x[u.name]
        for u in gfm:
            if u.name in sat:
                p += x[f"{u.name}~lim"]
            else:
                m += _gfm_m(u, f0)
                p -= u.p_n / (u.r * f0) * df
        if m <= 0.0:
            return None, {}, sat
        rocof = p / m
        new, dps = set(), {}
        for u in gfm:
            dpu = -u.p_n / (u.r * f0) * df - _gfm_m(u, f0) * rocof
            if u.name in sat:
                dps[u.name] = x[f"{u.name}~lim"]
                new.add(u.name)
            elif u.p0 + dpu > u.hi + 1e-9 or u.p0 + dpu < u.lo - 1e-9:
                x[f"{u.name}~lim"] = (u.hi if u.p0 + dpu > u.hi else u.lo) - u.p0
                new.add(u.name)
            else:
                dps[u.name] = dpu
        if new == sat:
            return rocof, dps, sat
        sat = new
    raise RuntimeError("the GFM saturation set did not settle")


def _targets(units, x, t, f0):
    """The time derivatives of the governor states, and the fw targets."""
    dx = {}
    for u in units:
        if u.model == "genset":
            if t < u.join_s:
                dx[u.name] = 0.0
                continue
            k = u.p_n / ((u.r if u.r == u.r and u.r else ISO_DROOP) * f0)
            ref = -k * (x["df"] + (x[f"{u.name}~i"] / ISO_TI_S if u.isochronous else 0.0))
            dx[u.name] = (ref - x[u.name]) / u.t_g
            if u.isochronous:
                dx[f"{u.name}~i"] = x["df"]
    return dx


def _clamp(units, x, t):
    """Hold the genset and fw states to their limits after a step."""
    for u in units:
        if u.model == "genset" and t >= u.join_s:
            room = u.step_mw + u.ramp_mw_per_s * (t - u.join_s)
            lo, hi = max(-room, 0.0 - u.p0), min(room, u.p_n - u.p0)
            x[u.name] = min(max(x[u.name], lo), hi)


def _fw_step(units, x, t, dt):
    for u in units:
        if u.model != "gfl_fw" or t < u.fw["delay_s"]:
            continue
        f = u.fw
        excess = max(0.0, abs(x["df"]) - f["deadband_hz"])
        target = -math.copysign(f["k_mw_per_hz"] * excess, x["df"])
        target = min(max(target, -f["p_limit_mw"], u.lo - u.p0), f["p_limit_mw"], u.hi - u.p0)
        step = f["ramp_mw_per_s"] * dt
        x[u.name] += min(max(target - x[u.name], -step), step)


def _ups_step(units, x, dt, log, t):
    """A UPS off line, or past its window, carries its IT load; back inside
    the window it walks the load back in. ``log`` gathers the drops and the
    battery energy."""
    for u in units:
        if u.model != "ups":
            continue
        a, rec, before = u.ups, log[u.name], x[u.name]
        if abs(x["df"]) > a["f_window_hz"]:
            if before < a["it_mw"] - 1e-12:
                rec["drops"].append(float(t))
            x[u.name] = a["it_mw"]
            rec["energy_mws"] += before * dt              # on its battery from the end of the step
        elif before > 0.0:
            rate = a["it_mw"] / a["walk_in_s"] if a["walk_in_s"] > 0 else math.inf
            x[u.name] = max(0.0, before - rate * dt)
            rec["energy_mws"] += 0.5 * (before + x[u.name]) * dt if rate * dt <= before else 0.5 * before ** 2 / rate
            if x[u.name] == 0.0:
                rec["t_online"] = float(t)
        if x[u.name] > 0.0:
            rec["t_online"] = None


def simulate(units, dp_event, t_end=20.0, dt=1e-3, f0=F0, damping=0.0, islanding=False) -> Trace:
    """Integrate the island frequency after ``dp_event(t)`` (MW, positive a
    deficit: load gained or generation lost) with ``units`` (``Unit``).
    Fixed-step RK4 on Δf and the governors; the frequency-watt ramps, the
    UPS states and the limits are applied after each step. ``islanding``:
    the event opens the PCC, so a UPS that ``transfers`` starts on its
    battery. A ``dp_event`` with an ``advance(t, Δf, RoCoF)`` method is told
    the state after each step and may change itself (E4's pickup)."""
    units = list(units)
    x = {"df": 0.0}
    ups_log = {}
    for u in units:
        if u.model in ("genset", "gfl_fw"):
            x[u.name] = 0.0
        if u.model == "genset" and u.isochronous:
            x[f"{u.name}~i"] = 0.0
        if u.model == "ups":
            x[u.name] = u.ups["it_mw"] if islanding and u.ups["transfers"] else 0.0
            ups_log[u.name] = {"drops": [], "energy_mws": 0.0, "t_online": None if x[u.name] > 0 else 0.0}
    advance = getattr(dp_event, "advance", None)
    n = int(round(t_end / dt)) + 1
    t = np.linspace(0.0, (n - 1) * dt, n)
    df = np.zeros(n)
    dp = {u.name: np.zeros(n) for u in units}
    saturated, collapse = {}, None
    rocof0, _, _ = _balance(units, dict(x), 0.0, dp_event, f0, damping)
    if rocof0 is None:
        return Trace(t[:1], df[:1], {k: v[:1] for k, v in dp.items()}, {}, 0.0, math.nan,
                     _ups_logs(units, ups_log))

    def deriv(state, tt):
        s = dict(state)
        rocof, _, _ = _balance(units, s, tt, dp_event, f0, damping)
        if rocof is None:
            return None
        d = {"df": rocof}
        d.update(_targets(units, s, tt, f0))
        return d

    def add(state, d, h):
        out = dict(state)
        for k, v in d.items():
            out[k] = out[k] + h * v
        return out

    for i in range(n):
        tt = t[i]
        rocof, dps, sat = _balance(units, dict(x), tt, dp_event, f0, damping)
        if rocof is not None and advance and advance(float(tt), x["df"], rocof):
            rocof, dps, sat = _balance(units, dict(x), tt, dp_event, f0, damping)
        if rocof is None:
            collapse = float(tt)
            t, df = t[:i], df[:i]
            dp = {k: v[:i] for k, v in dp.items()}
            break
        df[i] = x["df"]
        for u in units:
            if u.model in ("gfm_droop", "gfm_vsm"):
                dp[u.name][i] = dps[u.name]
                if u.name in sat:
                    saturated.setdefault(u.name, float(tt))
            elif u.model in ("genset", "gfl_fw", "ups"):
                dp[u.name][i] = x[u.name] if (u.model != "genset" or tt >= u.join_s) else 0.0
        if i == n - 1:
            break
        k1 = deriv(x, tt)
        k2 = deriv(add(x, k1, dt / 2), tt + dt / 2) if k1 else None
        k3 = deriv(add(x, k2, dt / 2), tt + dt / 2) if k2 else None
        k4 = deriv(add(x, k3, dt), tt + dt) if k3 else None
        if k4 is None:
            collapse = float(tt + dt)
            t, df = t[:i + 1], df[:i + 1]
            dp = {k: v[:i + 1] for k, v in dp.items()}
            break
        for k in k1:
            x[k] = x[k] + dt / 6.0 * (k1[k] + 2 * k2[k] + 2 * k3[k] + k4[k])
        _clamp(units, x, tt + dt)
        _fw_step(units, x, tt + dt, dt)
        _ups_step(units, x, dt, ups_log, tt + dt)
    return Trace(t, df, dp, saturated, collapse, float(rocof0), _ups_logs(units, ups_log))


def _ups_logs(units, log):
    return {u.name: UpsLog(log[u.name]["drops"], log[u.name]["energy_mws"] / 3600.0, log[u.name]["t_online"],
                           u.ups["e_mwh"]) for u in units if u.model == "ups"}


def native_droop_reference(genset, gfm, dp, t_end=5.0, dt=1e-3, f0=F0):
    """An independently coded genset plus one native droop GFM after a step
    ``dp`` (MW), for the cross-check with ``simulate``'s aggregate form
    (plan I6, D'Arco–Suul). States: the GFM's filtered power P_f, the
    genset's governor ΔP_m. The GFM imposes Δf = −R·f0·ΔP_f/P_n; its
    electrical output and the genset's swing are solved together each step.
    No limits. Returns ``(t, Δf)``."""
    kg = genset.p_n / (genset.r * f0)
    mg = 2.0 * genset.h * genset.s_n / f0
    n = int(round(t_end / dt)) + 1
    t = np.linspace(0.0, (n - 1) * dt, n)
    out = np.zeros(n)

    def rhs(pf, pm):
        df = -gfm.r * f0 * pf / gfm.p_n
        # unknowns: P_gfm (ΔP of the GFM) and dΔf/dt
        #   dΔf/dt = −(R f0 / P_n) (P_gfm − P_f) / τ_f        (the imposed frequency, differentiated)
        #   mg dΔf/dt = ΔP_m + P_gfm − dp                       (the genset swing, the bus balance)
        a = np.array([[gfm.r * f0 / (gfm.p_n * gfm.tau_f), 1.0], [-1.0, mg]])
        b = np.array([gfm.r * f0 * pf / (gfm.p_n * gfm.tau_f), pm - dp])
        p_gfm, rocof = np.linalg.solve(a, b)
        return (p_gfm - pf) / gfm.tau_f, (-kg * df - pm) / genset.t_g, df

    pf = pm = 0.0
    for i in range(n):
        d1f, d1m, out[i] = rhs(pf, pm)
        if i == n - 1:
            break
        d2f, d2m, _ = rhs(pf + dt / 2 * d1f, pm + dt / 2 * d1m)
        d3f, d3m, _ = rhs(pf + dt / 2 * d2f, pm + dt / 2 * d2m)
        d4f, d4m, _ = rhs(pf + dt * d3f, pm + dt * d3m)
        pf += dt / 6 * (d1f + 2 * d2f + 2 * d3f + d4f)
        pm += dt / 6 * (d1m + 2 * d2m + 2 * d3m + d4m)
    return t, out


# ── metrics and limits ──────────────────────────────────────────────────────

@dataclasses.dataclass
class Metrics:
    rocof0_hz_per_s: float
    rocof_window_hz_per_s: float
    window_ms: float
    nadir_hz: float              # the extreme Δf (signed: below 0 for a deficit)
    t_nadir_s: float
    df_qss_hz: float
    t_return_s: float            # the last time |Δf − Δf_qss| leaves the band; 0 if it never does
    saturated: dict
    collapse: float | None


def metrics(trace, window_ms=500.0, band_hz=0.1) -> Metrics:
    """The event's frequency figures from a ``Trace``."""
    if trace.collapse is not None:
        return Metrics(trace.rocof0, math.nan, window_ms, math.nan, math.nan, math.nan, math.nan,
                       dict(trace.saturated), trace.collapse)
    w = window_ms / 1000.0
    iw = min(int(round(w / (trace.t[1] - trace.t[0]))), len(trace.t) - 1)
    rocof_w = (trace.df[iw] - trace.df[0]) / trace.t[iw]
    i_ext = int(np.argmax(np.abs(trace.df)))
    qss = float(trace.df[-1])
    outside = np.flatnonzero(np.abs(trace.df - qss) > band_hz)
    t_ret = float(trace.t[outside[-1]]) if outside.size else 0.0
    return Metrics(trace.rocof0, float(rocof_w), window_ms, float(trace.df[i_ext]), float(trace.t[i_ext]), qss,
                   t_ret, dict(trace.saturated), None)


@dataclasses.dataclass
class Verdict:
    ok: bool
    binding: str | None          # the most sensitive limit broken, named
    failures: list               # every limit broken, as text
    worst: float = 0.0           # the binding limit's relative exceedance (inf on collapse)


def judge(m, limits, f0=F0, equipment=()) -> Verdict:
    """``m`` against the ``transition`` limits during the event and the
    ``steady_island`` ones after it (requirements ``frequency_limits``
    values), and against each piece of ``equipment``: ``(label, f_low_hz,
    f_high_hz)``, e.g. a UPS input window or a chiller's ``f_trip_hz``.
    The binding limit is the broken one exceeded most **relative to what it
    allows** (f0 − f_min, f_max − f0, the RoCoF limit, the band), so limits
    of different kinds compare."""
    if m.collapse is not None:
        return Verdict(False, COLLAPSE, [COLLAPSE], math.inf)
    tr, st = limits["transition"], limits["steady_island"]
    f_ext, f_qss = f0 + m.nadir_hz, f0 + m.df_qss_hz

    def low(label, limit, f):
        return label, (limit - f) / (f0 - limit)

    def high(label, limit, f):
        return label, (f - limit) / (limit - f0)

    checks = [
        (f"transition RoCoF {abs(m.rocof_window_hz_per_s):.2f} Hz/s over {m.window_ms:g} ms against "
         f"{tr['rocof_hz_per_s']:g} Hz/s", (abs(m.rocof_window_hz_per_s) - tr["rocof_hz_per_s"]) / tr["rocof_hz_per_s"]),
        low(f"transition nadir {f_ext:.2f} Hz against {tr['f_min_hz']:g} Hz", tr["f_min_hz"], f_ext),
        high(f"transition peak {f_ext:.2f} Hz against {tr['f_max_hz']:g} Hz", tr["f_max_hz"], f_ext),
    ]
    band = f"steady island {f_qss:.2f} Hz against {st['f_min_hz']:g}-{st['f_max_hz']:g} Hz"
    checks.append(max(low(band, st["f_min_hz"], f_qss), high(band, st["f_max_hz"], f_qss), key=lambda c: c[1]))
    checks.append((f"steady island deviation {abs(m.df_qss_hz):.2f} Hz against {st['qss_band_hz']:g} Hz",
                   (abs(m.df_qss_hz) - st["qss_band_hz"]) / st["qss_band_hz"]))
    for label, lo, hi in equipment:
        checks.append(low(f"{label}, nadir {f_ext:.2f} Hz against {lo:g} Hz", lo, f_ext))
        if hi == hi:
            checks.append(high(f"{label}, peak {f_ext:.2f} Hz against {hi:g} Hz", hi, f_ext))
    broken = sorted(((over, label) for label, over in checks if over > 1e-12), reverse=True)
    return Verdict(not broken, broken[0][1] if broken else None, [label for _, label in broken],
                   broken[0][0] if broken else 0.0)


# ── units and events ───────────────────────────────────────────────────────

def units_from_campus(campus, rows, online=None, cold=(), soc=None):
    """The island's ``Unit`` list at one hour, from I4's unit plan (its P0
    and its references), so ΔP and K are shared with ``island_flow``.
    ``cold`` gensets join at ``start_s`` + ``sync_s`` from P = 0. A UPS
    carries the hour's draw of its ``it_load``; its stored energy is
    ``soc[uid]`` (e(t−1), I3a) when given, else the campus ``e_mwh``."""
    from gridspine.static.island_flow import IslandSettings, _build

    _, plan, _ = _build(campus, rows, online, IslandSettings())
    soc = soc or {}
    out = []
    for uid, r in plan.iterrows():
        if r["role"] in ("offline", "load") or r["block"] is None:
            continue
        v = r["block"]["values"]
        p0, p_n, s_n = float(r["p0_mw"]), float(r["p_n"]), float(r["s_n"])
        if r["kind"] == "ups":
            it = sum(-float(plan.at[lid, "p0_mw"]) for lid in r["block"]["it_load"])
            e = soc.get(uid, campus.units.at[uid, "e_mwh"])
            out.append(Unit(uid, "ups", 0.0, p_n, s_n,
                            ups={"it_mw": it, "walk_in_s": v["walk_in_s"], "f_window_hz": v["f_window_hz"],
                                 "transfers": r["block"]["transfers_on_islanding"], "e_mwh": float(e)}))
            continue
        if r["kind"] == "genset":
            join = (v["start_s"] + v["sync_s"]) if uid in cold else 0.0
            out.append(Unit(uid, "genset", 0.0 if uid in cold else p0, p_n, s_n, 0.0, p_n,
                            r=v["droop_pct"] / 100.0 if "droop_pct" in v else math.nan, h=v["H_s"], t_g=v["T_g_s"],
                            isochronous=r["block"]["governor"] == "isochronous",
                            step_mw=v["load_step_max_pct"] / 100.0 * p_n, ramp_mw_per_s=v["ramp_pu_per_s"] * p_n,
                            join_s=join, start_s=v["start_s"], sync_s=v["sync_s"]))
            continue
        control = r["block"].get("control")
        lo = -p_n if r["kind"] == "bess" else 0.0
        hi = p_n if r["kind"] == "bess" else p0
        if control in ("gfm_droop", "gfm_vsm"):
            out.append(Unit(uid, control, p0, p_n, s_n, lo, hi,
                            r=v["droop_pct"] / 100.0, h=v.get("H_v_s", 0.0), tau_f=v.get("tau_f_s", 0.0),
                            i_max_mw=v["i_max_pu"] * s_n))
        elif control == "gfl_fw":
            out.append(Unit(uid, "gfl_fw", p0, p_n, s_n, lo, hi,
                            fw={"k_mw_per_hz": v["fw_k_mw_per_hz"], "deadband_hz": v["fw_deadband_hz"],
                                "delay_s": v["fw_delay_s"], "ramp_mw_per_s": v["fw_ramp_mw_per_s"],
                                "p_limit_mw": v["fw_p_limit_mw"]}))
    return out


def step(dp_mw, at_s=0.0):
    """A step of ``dp_mw`` (positive: a deficit) at ``at_s``."""
    return lambda t: dp_mw if t >= at_s else 0.0


def e1_islanding(units, import_mw, **kw):
    """E1: the PCC opens with ``import_mw`` crossing it (negative when the
    site exports, an over-frequency). Cold gensets join on their own clock;
    a UPS that transfers starts on its battery and walks back in."""
    return simulate(units, step(import_mw), islanding=True, **kw)


def e2_load_step(units, step_mw, **kw):
    """E2: the largest load step (a chiller restart, a UPS walk-in block)."""
    return simulate(units, step(step_mw), **kw)


def e3_unit_trip(units, name, **kw):
    """E3: ``name`` trips: its P0 is lost and its response goes with it."""
    lost = next(u for u in units if u.name == name)
    rest = [u for u in units if u.name != name]
    return simulate(rest, step(lost.p0), **kw)


def e5_pv_loss(units, lost_mw, **kw):
    """E5: PV output lost in the island (a cloud)."""
    return simulate(units, step(lost_mw), **kw)


# ── I6b: the ups_bridged start, planned islanding, Q8 and the EMT flags ─────

class Pickup:
    """E4's load schedule, a ``dp_event`` that advances itself: the first
    block at 0, then block k+1 once Δf is within ``band_hz`` of its settled
    value after k blocks (−ΣP/ΣK, or 0 with an isochronous genset) and
    |RoCoF| ≤ ``settle_hz_per_s``: the governors have recovered. A real
    controller may wait a fixed interval instead (ledgered)."""

    def __init__(self, blocks, k_mw_per_hz, isochronous, band_hz=0.1, settle_hz_per_s=0.05):
        self.blocks, self.k, self.iso = list(blocks), k_mw_per_hz, isochronous
        self.band, self.settle = band_hz, settle_hz_per_s
        self.times = [0.0]

    def __call__(self, t):
        return sum(b for b, tk in zip(self.blocks, self.times) if t >= tk)

    def settled(self, n):
        """Δf settled after the first ``n`` blocks."""
        if self.iso:
            return 0.0
        return -sum(self.blocks[:n]) / self.k if self.k else math.nan

    def advance(self, t, df, rocof):
        n = len(self.times)
        if n == len(self.blocks) or t <= self.times[-1]:
            return False
        if abs(df - self.settled(n)) <= self.band and abs(rocof) <= self.settle:
            self.times.append(t)
            return True
        return False


@dataclasses.dataclass
class Bridge:
    """The Q1 bridge check for one UPS under ``ups_bridged``: its stored
    energy at the IT load lasts ``autonomy_s``; it must last ``needed_s`` =
    max(``bridge_s``, the gensets' ready time + the last block's pickup)."""
    ups: str
    it_mw: float
    e_mwh: float
    autonomy_s: float
    needed_s: float
    ok: bool


@dataclasses.dataclass
class BridgedStart:
    """E4. ``trace`` runs from ``ready_s`` (the dead bus until then); the
    blocks are picked up at ``block_times_s`` on that clock; ``blocks``
    holds each block's ``Metrics``. ``complete``: every block was taken
    within the horizon and the frequency settled after the last."""
    trace: Trace
    ready_s: float
    block_times_s: list
    blocks: list
    complete: bool
    bridge: list
    dropped: list                # the gensets left out (N+k)


def _segment(trace, t0, t1):
    """The trace from ``t0`` to ``t1``, re-timed from 0."""
    sel = (trace.t >= t0 - 1e-12) & (trace.t < t1 - 1e-12)
    t, df = trace.t[sel] - t0, trace.df[sel]
    rocof0 = (df[1] - df[0]) / (t[1] - t[0]) if len(t) > 1 else math.nan
    return Trace(t, df, {k: v[sel] for k, v in trace.dp.items()},
                 {k: v - t0 for k, v in trace.saturated.items() if t0 - 1e-12 <= v < t1 - 1e-12}, None, rocof0)


def e4_ups_bridged(gensets, blocks, ups=(), bridge_s=0.0, redundancy_n=0, band_hz=0.1, settle_hz_per_s=0.05,
                   window_ms=500.0, t_end=600.0, dt=5e-3, f0=F0) -> BridgedStart:
    """E4, the ``ups_bridged`` start (plan I6b): the bus is dead, the UPSs
    carry the IT load, the gensets start and, once every one is ready
    (start + sync, the join I2b uses), pick up ``blocks`` one at a time
    (``Pickup``). The ``redundancy_n`` largest gensets fail to start (N+k,
    as I2b's pickup row). ``ups`` is ``(name, it_mw, e_mwh)`` per UPS, e_mwh
    being e(t−1); the bridge check needs each to last max(``bridge_s``,
    ready + the last pickup)."""
    from gridspine.schema.contracts import ContractError

    gens = sorted((g for g in gensets if g.model == "genset"), key=lambda g: (-g.p_n, g.name))
    dropped, gens = [g.name for g in gens[:redundancy_n]], gens[redundancy_n:]
    if not gens or not blocks:
        raise ContractError("E4 needs a genset left at N+k and the pickup blocks")
    ready = max(g.ready_s for g in gens)
    units = [dataclasses.replace(g, p0=0.0, join_s=0.0) for g in gens]
    iso = any(g.isochronous for g in units)
    sched = Pickup(blocks, sum(g.k for g in units if not g.isochronous), iso, band_hz, settle_hz_per_s)
    tr = simulate(units, sched, t_end=t_end, dt=dt, f0=f0)
    complete = (len(sched.times) == len(sched.blocks) and tr.collapse is None
                and abs(tr.df[-1] - sched.settled(len(sched.blocks))) <= band_hz)
    edges = [*sched.times, math.inf]
    per = [metrics(_segment(tr, a, b), window_ms, band_hz) for a, b in zip(edges, edges[1:])]
    last = ready + sched.times[-1] if complete else math.inf
    bridge = []
    for name, it, e in ups:
        autonomy = e * 3600.0 / it if it > 0 else math.inf
        needed = max(bridge_s, last)
        bridge.append(Bridge(name, it, e, autonomy, needed, autonomy >= needed))
    return BridgedStart(tr, ready, list(sched.times), per, complete, bridge, dropped)


@dataclasses.dataclass
class PlannedIslanding:
    """E6. ``ramp_s``: the time for the island's units to take over the
    import before the PCC opens (inf, with the ``shortfall_mw``, when they
    cannot); ``p0`` the dispatch at the opening; ``trace`` the opening with
    the ``residual_mw`` left at the PCC (None when not feasible)."""
    feasible: bool
    ramp_s: float
    shortfall_mw: float
    p0: dict
    trace: Trace | None


def _ramp_room(u, tau, up):
    """What ``u`` can take over by ``tau`` s of a planned ramp. Gensets ramp
    at ``ramp_pu_per_s`` from their join; a GFM moves at once (a converter
    ramps in milliseconds; ledgered); a frequency-watt unit at its ramp;
    other units hold their dispatch."""
    room = max(0.0, (u.hi - u.p0) if up else (u.p0 - u.lo))
    if u.model == "genset":
        return min(room, u.ramp_mw_per_s * max(0.0, tau - u.join_s))
    if u.model in ("gfm_droop", "gfm_vsm"):
        return room
    if u.model == "gfl_fw":
        return min(room, u.fw["ramp_mw_per_s"] * tau)
    return 0.0


def e6_planned(units, import_mw, residual_mw=0.0, **kw) -> PlannedIslanding:
    """E6, ``planned`` islanding (plan I6b): the gensets and the storage
    ramp to cover ``import_mw`` (negative: the export is taken back), then
    the PCC opens with ``residual_mw`` left (≈ 0). Cold gensets start on
    their clock and ramp from their join. The take-over is shared in
    proportion to what each unit can give at the ramp time."""
    units = list(units)
    need, up = abs(import_mw), import_mw > 0

    def room(tau):
        return sum(_ramp_room(u, tau, up) for u in units)

    full = max([0.0] + [u.join_s + ((u.hi - u.lo) / u.ramp_mw_per_s if u.ramp_mw_per_s else 0.0)
                        for u in units if u.model == "genset"]
               + [((u.hi - u.lo) / u.fw["ramp_mw_per_s"]) for u in units if u.model == "gfl_fw"])
    if room(full) < need - 1e-9:
        return PlannedIslanding(False, math.inf, need - room(full), {u.name: u.p0 for u in units}, None)
    lo, hi = 0.0, full
    if room(0.0) >= need:
        hi = 0.0
    for _ in range(200):
        if hi - lo <= 1e-9:
            break
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if room(mid) >= need else (mid, hi)
    tau = hi
    total = room(tau)
    sign = 1.0 if up else -1.0
    shifted = []
    for u in units:
        share = _ramp_room(u, tau, up) * need / total if total > 0 else 0.0
        shifted.append(dataclasses.replace(u, p0=u.p0 + sign * share, join_s=0.0) if u.model != "ups"
                       else u)
    trace = simulate(shifted, step(residual_mw), **kw)
    return PlannedIslanding(True, tau, 0.0, {u.name: u.p0 for u in shifted}, trace)


def i_max_sensitivity(units, run, values):
    """Q8: ``run(units)`` again with every GFM's current limit at each of
    ``values`` (pu of S_n, the requirements' ``i_max_sensitivity_pu``).
    Returns ``[(i_max_pu, result)]``."""
    out = []
    for v in values:
        varied = [dataclasses.replace(u, i_max_mw=v * u.s_n) if u.model in ("gfm_droop", "gfm_vsm") else u
                  for u in units]
        out.append((v, run(varied)))
    return out


#: The EMT/RMS recommendation thresholds (plan I6; assumed, ledgered).
EMT_NADIR_MARGIN = 0.2       # of the allowed deviation f0 − f_min (or f_max − f0) left at the extreme
EMT_SCR = 3.0                # short-circuit ratio below which the grid or the island is weak
EMT_GFL_SHARE = 0.3          # of the online source MVA, grid-following
EMT_GFM_SHARE = 0.5          # of the online source MVA, grid-forming
LEDGER += (
    f"frequency: EMT/RMS is recommended below a {EMT_NADIR_MARGIN:.0%} nadir margin, on GFM saturation, "
    f"at SCR < {EMT_SCR:g} with a GFL share above {EMT_GFL_SHARE:.0%}, and in an island with SCR < {EMT_SCR:g} "
    f"and a GFM share above {EMT_GFM_SHARE:.0%} (thresholds assumed)",
    "frequency: E4 picks up the next block once the governors have recovered (within the band of the settled "
    "frequency, |RoCoF| ≤ 0.05 Hz/s); the gensets are ready at the slowest one's start + sync",
    "frequency: E6 takes a converter's ramp as instantaneous; gensets ramp at ramp_pu_per_s from their join",
    "frequency: E2, E3 and E5 start from I4's island dispatch at the nominal frequency (secondary control "
    "has restored it after the islanding)",
)


def emt_flags(m, limits, f0=F0, scr_pcc=math.nan, gfl_share=math.nan, island_scr=math.nan,
              gfm_share=math.nan) -> list:
    """Why a screened event should go to an EMT or RMS study (plan I6): a
    nadir margin below ``EMT_NADIR_MARGIN`` of the transition limit's
    allowed deviation, GFM saturation, SCR < 3 at the PCC with a large GFL
    share, a high GFM share in a weak island. NaN inputs are not judged."""
    flags = []
    if m.collapse is not None:
        flags.append(f"{COLLAPSE} at {m.collapse:.3f} s")
    else:
        tr = limits["transition"]
        f = f0 + m.nadir_hz
        margin = ((f - tr["f_min_hz"]) / (f0 - tr["f_min_hz"]) if m.nadir_hz < 0
                  else (tr["f_max_hz"] - f) / (tr["f_max_hz"] - f0))
        if margin < EMT_NADIR_MARGIN - 1e-9:
            flags.append(f"nadir margin {margin:.0%} of the allowed deviation, below {EMT_NADIR_MARGIN:.0%}")
    if m.saturated:
        flags.append("grid-forming units saturate in the event: " + ", ".join(sorted(m.saturated)))
    if scr_pcc < EMT_SCR and gfl_share > EMT_GFL_SHARE:
        flags.append(f"SCR {scr_pcc:.2f} at the PCC with {gfl_share:.0%} grid-following")
    if island_scr < EMT_SCR and gfm_share > EMT_GFM_SHARE:
        flags.append(f"island SCR {island_scr:.2f} with {gfm_share:.0%} grid-forming")
    return flags


# ── the campus: its equipment, the EMT inputs and the hour sweeps ───────────

def equipment(campus, f0=F0):
    """The island's frequency-sensitive equipment for ``judge``: each UPS's
    input window, f0 ± ``f_window_hz``, and each load's ``f_trip_hz`` (an
    under-frequency trip below f0, an over-frequency trip above it)."""
    out = []
    for uid, b in sorted(campus.island["units"].items()):
        v = b["values"]
        if b["kind"] == "ups":
            out.append((f"UPS window {uid}", f0 - v["f_window_hz"], f0 + v["f_window_hz"]))
        elif b["kind"] == "load" and "f_trip_hz" in v:
            f = v["f_trip_hz"]
            out.append((f"{uid} trip", f, math.nan) if f < f0 else (f"{uid} trip", math.nan, f))
    return out


def emt_inputs(campus, rows):
    """The inputs of ``emt_flags`` at one hour: the PCC's SCR on the
    grid-following MVA, the grid-following and grid-forming shares of the
    online source MVA, and the island's SCR (I5's minimum fault level at the
    weakest converter bus over the converter MVA). The online sources are
    those built in the hour's period (``status`` 1), running or not: a
    genset with island data starts on islanding."""
    from gridspine.schema.island import GFM_CONTROLS
    from gridspine.static.island_sc import island_fault_levels

    status = rows.set_index("unit_id")["status"]
    s = {"gfl": 0.0, "gfm": 0.0, "gen": 0.0}
    sources, conv_buses = set(), set()
    for uid, u in campus.units.iterrows():
        if u["kind"] not in ("bess", "pv", "wind", "genset"):
            continue
        block = campus.island["units"].get(uid)
        if int(status.get(uid, 0)) != 1:              # not built in the period
            continue
        sources.add(uid)
        if u["kind"] == "genset":
            s["gen"] += float(u["s_mva"])
            continue
        conv_buses.add(u["bus"])
        s["gfm" if block and block.get("control") in GFM_CONTROLS else "gfl"] += float(u["s_mva"])
    total = sum(s.values())
    conv = s["gfl"] + s["gfm"]
    sk_min = float(campus.net.ext_grid["s_sc_min_mva"].iloc[0])
    island_scr = math.nan
    if conv and sources:
        levels = island_fault_levels(campus, online=sources).set_index("bus")
        vn = dict(zip(campus.net.bus["name"].astype(str), campus.net.bus["vn_kv"]))
        island_scr = min(math.sqrt(3.0) * vn[b] * float(levels.at[b, "ikss_min_ka"]) for b in conv_buses) / conv
    return {"scr_pcc": sk_min / s["gfl"] if s["gfl"] else math.inf,
            "gfl_share": s["gfl"] / total if total else 0.0,
            "island_scr": island_scr,
            "gfm_share": s["gfm"] / total if total else 0.0}


def _limits(campus):
    req = campus.island["requirements"]
    if req is None:
        from gridspine.schema.contracts import ContractError
        raise ContractError("the frequency check needs the campus island requirements (campus.island)")
    return {k: v["values"] for k, v in req["frequency_limits"].items()}, req


def _hour_rows(hourly, period, hour):
    sel = hourly[(hourly["period"] == period) & (hourly["hour"] == hour)]
    return sel[["unit_id", "p_mw", "status"]].reset_index(drop=True)


def built(rows):
    """The units that exist in the hour's period (``status`` 1 in the campus
    hourly table): the island holds these and no others."""
    return {str(u) for u, st in zip(rows["unit_id"], rows["status"]) if int(st) == 1}


def cold_gensets(campus, rows, unit_rows=None):
    """The gensets with island data that are not running at the hour: from
    I3a's ``online`` where it has the unit, else not in service or at 0."""
    online = {}
    if unit_rows is not None:
        online = {r["unit_id"]: r["online"] for _, r in unit_rows.iterrows() if r["online"] == r["online"]}
    hour = rows.set_index("unit_id")
    out = set()
    for uid, b in campus.island["units"].items():
        if b["kind"] != "genset":
            continue
        running = (online[uid] >= 0.5 if uid in online
                   else int(hour.at[uid, "status"]) == 1 and float(hour.at[uid, "p_mw"]) > 0.0)
        if not running:
            out.add(uid)
    return out


def _event_row(key, event, dp_mw, trace, limits, window_ms, eq, f0, emt=None, **extra):
    m = metrics(trace, window_ms)
    v = judge(m, limits, f0, eq)
    flags = emt_flags(m, limits, f0, **(emt or {}))
    return {**key, "event": event, **extra, "dp_mw": dp_mw, "rocof0_hz_per_s": m.rocof0_hz_per_s,
            "rocof_window_hz_per_s": m.rocof_window_hz_per_s, "nadir_hz": m.nadir_hz, "t_nadir_s": m.t_nadir_s,
            "df_qss_hz": m.df_qss_hz, "t_return_s": m.t_return_s, "saturated": ", ".join(sorted(m.saturated)),
            "collapse_s": m.collapse, "ok": v.ok, "binding": v.binding, "worst": v.worst,
            "emt": "; ".join(flags)}


#: The fixed columns of the sweep and the study tables (plan I6c): a table
#: carries every column whatever events ran, empty where one does not apply.
_METRIC_COLUMNS = ("dp_mw", "rocof0_hz_per_s", "rocof_window_hz_per_s", "nadir_hz", "t_nadir_s", "df_qss_hz",
                   "t_return_s", "saturated", "collapse_s", "ok", "binding", "worst", "emt")
E1_HOURLY_COLUMNS = ("period", "hour", "scenario", "event", "direction", "dp_source", "ups_drops", *_METRIC_COLUMNS)
#: ``unit``: E3's tripped unit; ``i_max_pu``: a Q8 rerun; ``block``,
#: ``t_block_s``: an E4 block.
EVENT_COLUMNS = ("period", "hour", "scenario", "event", "dp_source", "unit", "i_max_pu", "block", "t_block_s",
                 *_METRIC_COLUMNS)
LOSSES_COLUMNS = ("period", "hour", "i4_viable", "i4_reason", "dp_ac_mw", "import_lossless_mw", "losses_mw")
#: ``kind`` ``ups``: E4's Q1 check (``it_mw``, ``autonomy_s``, ``needed_s``,
#: the pickup); ``gfm``: the energy a GFM gives until the gensets join
#: (``energy_mwh``, ``join_s``, ``until_s``). ``ok`` is empty where there is
#: no e(t−1) to judge.
BRIDGE_COLUMNS = ("period", "hour", "scenario", "unit", "kind", "e_mwh", "ok", "it_mw", "autonomy_s", "needed_s",
                  "pickup_complete", "ready_s", "dropped", "energy_mwh", "join_s", "until_s")
PLANNED_COLUMNS = ("period", "hour", "scenario", "import_mw", "feasible", "ramp_s", "shortfall_mw")
UPS_COLUMNS = ("period", "hour", "scenario", "event", "ups", "drops", "first_drop_s", "energy_mwh", "e_mwh",
               "t_online_s", "autonomy_ok")


def e1_every_hour(campus, hourly, island_hourly, island_unit_hourly=None, scenario="seamless",
                  dt=5e-3, t_end=10.0, f0=F0):
    """E1 at every hour (plan I6b): the PCC opens on I3a's lossless 0⁺ steps,
    ``dp_up_mw`` (an import lost, under-frequency) and ``dp_down_mw`` (an
    export lost, over-frequency), each run where it is above 0, on the
    units at the hour; the gensets not running start and join on their
    clock. A UPS that transfers is left out: I3a's steps already net its
    transfer (the walk-in runs at the critical hours,
    ``campus_frequency_study``); one that stays on line is kept, so a
    drop-out from its window is seen (``ups_drops``).
    Fast enough for every hour at the default 5 ms step; identical hours are
    run once. Returns one row per (period, hour, direction)."""
    import pandas as pd

    from gridspine.schema.island import validate_island_hourly, validate_island_unit_hourly

    limits, req = _limits(campus)
    window, eq = req["values"]["rocof_window_ms"], equipment(campus, f0)
    ih = validate_island_hourly(island_hourly)
    ih = ih[ih["scenario"] == scenario]
    iu = validate_island_unit_hourly(island_unit_hourly) if island_unit_hourly is not None else None
    out, cache = [], {}
    for _, r in ih.sort_values(["period", "hour"]).iterrows():
        period, hour = int(r["period"]), int(r["hour"])
        rows = _hour_rows(hourly, period, hour)
        urows = iu[(iu["period"] == period) & (iu["hour"] == hour)] if iu is not None else None
        units = [u for u in units_from_campus(campus, rows, online=built(rows),
                                              cold=cold_gensets(campus, rows, urows))
                 if not (u.model == "ups" and u.ups["transfers"])]
        key = {"period": period, "hour": hour, "scenario": scenario}
        for direction, dp in (("import", r["dp_up_mw"]), ("export", -r["dp_down_mw"])):
            if not abs(dp) > 1e-9 or (direction == "import") != (dp > 0):
                continue
            ck = (repr(units), round(dp, 9))
            if ck not in cache:
                cache[ck] = simulate(units, step(dp), t_end=t_end, dt=dt, f0=f0)
            drops = sorted(n for n, rec in cache[ck].ups.items() if rec.drops)
            out.append(_event_row(key, "E1", dp, cache[ck], limits, window, eq, f0, direction=direction,
                                  dp_source="I3a lossless", ups_drops=", ".join(drops)))
    return pd.DataFrame(out, columns=list(E1_HOURLY_COLUMNS))


@dataclasses.dataclass
class FrequencyStudy:
    """``campus_frequency_study``'s tables. ``events``: one row per event
    run (E1 on I4's ΔP_AC, E2, E3, E5, each E4 block, the E6 opening, and
    the Q8 reruns with their ``i_max_pu``); ``losses``: ΔP_AC against the
    lossless import; ``bridge``: E4's Q1 check per UPS and, under
    ``seamless``, each GFM's energy until the gensets join; ``planned``:
    E6's ramp; ``ups``: what each UPS did in each event."""
    events: object
    losses: object
    bridge: object
    planned: object
    ups: object
    label: str = LABEL


def _gfm_bridge(key, units, trace, soc):
    """Under ``seamless``, the energy each GFM gives from the islanding until
    the cold gensets have joined (plan I6, E1), against its e(t−1) where
    I3a gives it. A trace shorter than the join is counted to its end and
    says so."""
    joins = [u.join_s for u in units if u.model == "genset" and u.join_s > 0]
    if not joins:
        return []
    t_join = max(joins)
    sel = trace.t <= t_join + 1e-12
    out = []
    for u in units:
        if u.model not in ("gfm_droop", "gfm_vsm"):
            continue
        p = u.p0 + trace.dp[u.name][sel]
        e = float(np.sum(0.5 * (p[1:] + p[:-1]) * np.diff(trace.t[sel]))) / 3600.0
        e0 = soc.get(u.name, math.nan)
        out.append({**key, "unit": u.name, "kind": "gfm", "until_s": min(t_join, float(trace.t[-1])),
                    "join_s": t_join, "energy_mwh": e, "e_mwh": e0, "ok": bool(e <= e0) if e0 == e0 else None})
    return out


def campus_frequency_study(campus, hourly, hours, island_unit_hourly=None, load_step_mw=None,
                           dt=1e-3, t_end=20.0, f0=F0) -> FrequencyStudy:
    """E1–E6 at the island critical ``hours``, ``[(period, hour)]`` (plan
    I6b; I3b chooses them), in each of the campus's island ``scenarios``:

    * ``seamless``: E1 on I4's ΔP_AC with the UPS transfers and walk-in, its
      losses against the lossless import reported; then, from I4's island
      dispatch, E2 (``load_step_mw``, else the largest pickup block, else the
      hour's largest load), E3 (the unit with the largest |P|) and E5 (the
      island's PV output lost); Q8 reruns E1 and E2 at each
      ``i_max_sensitivity_pu``;
    * ``ups_bridged``: E4 with the ``pickup_blocks`` at N+k, and the bridge
      check on each UPS's e(t−1) (I3a's ``soc_start_mwh``, else ``e_mwh``);
    * ``planned``: E6 on the same ΔP_AC, its ramp time.

    Each event is judged against the limits and the equipment, and flagged
    for EMT/RMS where ``emt_flags`` says so."""
    import pandas as pd

    from gridspine.schema.island import validate_island_unit_hourly
    from gridspine.static.island_flow import island_flow

    limits, req = _limits(campus)
    rv = req["values"]
    window, eq = rv["rocof_window_ms"], equipment(campus, f0)
    iu = validate_island_unit_hourly(island_unit_hourly) if island_unit_hourly is not None else None
    ev, losses, bridge, planned, ups = [], [], [], [], []
    kw = {"t_end": t_end, "dt": dt, "f0": f0}

    def log_ups(key, event, trace):
        for name, rec in trace.ups.items():
            ups.append({**key, "event": event, "ups": name, "drops": len(rec.drops),
                        "first_drop_s": rec.drops[0] if rec.drops else math.nan, "energy_mwh": rec.energy_mwh,
                        "e_mwh": rec.e_mwh, "t_online_s": rec.t_online_s, "autonomy_ok": rec.autonomy_ok})

    for period, hour in hours:
        key = {"period": int(period), "hour": int(hour)}
        rows = _hour_rows(hourly, period, hour)
        urows = iu[(iu["period"] == period) & (iu["hour"] == hour)] if iu is not None else None
        soc, it = {}, {}
        if urows is not None:
            for _, r in urows.iterrows():
                if r["soc_start_mwh"] == r["soc_start_mwh"]:
                    soc[r["unit_id"]] = r["soc_start_mwh"]
                if r["p_ups_it_mw"] == r["p_ups_it_mw"]:
                    it[r["unit_id"]] = r["p_ups_it_mw"]
        units = units_from_campus(campus, rows, online=built(rows), cold=cold_gensets(campus, rows, urows),
                                  soc=soc)
        units = [dataclasses.replace(u, ups={**u.ups, "it_mw": it[u.name]}) if u.name in it else u for u in units]
        lossless = -float((rows["p_mw"] * (rows["status"] == 1)).sum())
        i4 = island_flow(campus, rows, online=built(rows))
        dp_ac = i4.delta_p_ac_mw if i4.viable else math.nan
        losses.append({**key, "i4_viable": i4.viable, "i4_reason": i4.reason, "dp_ac_mw": dp_ac,
                       "import_lossless_mw": lossless, "losses_mw": dp_ac - lossless})
        emt = emt_inputs(campus, rows)
        dp_e1 = dp_ac if i4.viable else lossless

        for scenario in req["scenarios"]:
            sk = {**key, "scenario": scenario}
            if scenario == "seamless":
                def e1(us):
                    return e1_islanding(us, dp_e1, **kw)
                tr = e1(units)
                ev.append(_event_row(sk, "E1", dp_e1, tr, limits, window, eq, f0, emt,
                                     dp_source="I4 ΔP_AC" if i4.viable else "lossless import"))
                log_ups(sk, "E1", tr)
                bridge += _gfm_bridge(sk, units, tr, soc)
                if not i4.viable:
                    continue
                p_isl = i4.units.set_index("unit_id")["p_mw"]
                isl = [dataclasses.replace(u, p0=float(p_isl[u.name]), join_s=0.0) if u.model != "ups" else u
                       for u in units]
                step_mw = load_step_mw
                if step_mw is None:
                    blocks = rv.get("pickup_blocks")
                    step_mw = max(blocks) if blocks else float((-rows["p_mw"].clip(upper=0.0)).max())

                def e2(us):
                    return e2_load_step(us, step_mw, **kw)
                tr = e2(isl)
                ev.append(_event_row(sk, "E2", step_mw, tr, limits, window, eq, f0, emt))
                log_ups(sk, "E2", tr)
                sources = [u for u in isl if u.model not in ("ups", "none")]
                if sources:
                    lost = max(sources, key=lambda u: (abs(u.p0), u.p_n))
                    tr = e3_unit_trip(isl, lost.name, **kw)
                    ev.append(_event_row(sk, "E3", lost.p0, tr, limits, window, eq, f0, emt, unit=lost.name))
                    log_ups(sk, "E3", tr)
                pv = float(sum(p_isl[uid] for uid in p_isl.index if campus.units.at[uid, "kind"] == "pv"))
                if pv > 1e-9:
                    tr = e5_pv_loss(isl, pv, **kw)
                    ev.append(_event_row(sk, "E5", pv, tr, limits, window, eq, f0, emt))
                    log_ups(sk, "E5", tr)
                for name, run, base, dp in (("E1", e1, units, dp_e1), ("E2", e2, isl, step_mw)):
                    for v, tr in i_max_sensitivity(base, run, rv["i_max_sensitivity_pu"]):
                        ev.append(_event_row(sk, name, dp, tr, limits, window, eq, f0, emt, i_max_pu=v))
            elif scenario == "ups_bridged":
                gens = [u for u in units if u.model == "genset"]
                feeds = [(u.name, u.ups["it_mw"], u.ups["e_mwh"]) for u in units if u.model == "ups"]
                res = e4_ups_bridged(gens, rv["pickup_blocks"], feeds, rv["bridge_s"], rv["genset_redundancy_n"],
                                     window_ms=window, f0=f0)
                for i, m in enumerate(res.blocks):
                    v = judge(m, limits, f0, eq)
                    ev.append({**sk, "event": "E4", "block": i + 1, "dp_mw": rv["pickup_blocks"][i],
                               "t_block_s": res.ready_s + res.block_times_s[i],
                               "rocof_window_hz_per_s": m.rocof_window_hz_per_s, "nadir_hz": m.nadir_hz,
                               "t_nadir_s": m.t_nadir_s, "df_qss_hz": m.df_qss_hz, "t_return_s": m.t_return_s,
                               "ok": v.ok, "binding": v.binding, "worst": v.worst,
                               "emt": "; ".join(emt_flags(m, limits, f0))})
                for b in res.bridge:
                    bridge.append({**sk, "unit": b.ups, "kind": "ups", "it_mw": b.it_mw, "e_mwh": b.e_mwh,
                                   "autonomy_s": b.autonomy_s,
                                   "needed_s": b.needed_s, "ok": b.ok, "pickup_complete": res.complete,
                                   "ready_s": res.ready_s, "dropped": ", ".join(res.dropped)})
            elif scenario == "planned":
                res = e6_planned([u for u in units if u.model != "ups"], dp_e1, **kw)
                planned.append({**sk, "import_mw": dp_e1, "feasible": res.feasible, "ramp_s": res.ramp_s,
                                "shortfall_mw": res.shortfall_mw})
                if res.trace is not None:
                    ev.append(_event_row(sk, "E6", 0.0, res.trace, limits, window, eq, f0, emt))
    return FrequencyStudy(*(pd.DataFrame(t, columns=list(c)) for t, c in (
        (ev, EVENT_COLUMNS), (losses, LOSSES_COLUMNS), (bridge, BRIDGE_COLUMNS), (planned, PLANNED_COLUMNS),
        (ups, UPS_COLUMNS))))
