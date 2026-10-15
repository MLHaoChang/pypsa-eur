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
    ``genset``, ``gfm_droop``, ``gfm_vsm``, ``gfl_fw``, ``ups``,
    ``inertia`` (H·S alone: no response and no limit; I7's inversion) or
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
    unit_mw: float = math.nan     # a genset of several units: one unit's MW (N+k removes units; NaN: one unit)

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
    trip: tuple | None = None    # (t, what): Δf crossed the trip floor or ceiling at t (``TripFloor``)


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
            elif u.model == "inertia":                    # I7: inertia alone, no response, no limit
                m += 2.0 * u.h * u.s_n / f0
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
            dx[u.name] = _hold(u, x[u.name], (ref - x[u.name]) / u.t_g, t)
            if u.isochronous:
                dx[f"{u.name}~i"] = _hold_integral(u, x[u.name], x["df"], t)
    return dx


def _hold_integral(u, p, df, t):
    """Anti-windup for the isochronous integral: at a bound it does not
    integrate an error that pushes the genset further out (Δf < 0 at its
    upper bound, > 0 at its lower), so it does not wind up while the genset
    is held and overshoot once released."""
    lo, hi, _, _ = _genset_bounds(u, t)
    if (p >= hi - 1e-12 and df < 0.0) or (p <= lo + 1e-12 and df > 0.0):
        return 0.0
    return df


def _genset_bounds(u, t):
    """``(lo, hi, rate_lo, rate_hi)``: a joined genset's ΔP range at ``t``,
    its step plus ramp since joining within 0..P_n, and how fast each bound
    moves (the ramp while it binds, else 0)."""
    elapsed = t - u.join_s
    room = u.step_mw + (u.ramp_mw_per_s * elapsed if elapsed > 0.0 else 0.0)
    lo, hi = max(-room, 0.0 - u.p0), min(room, u.p_n - u.p0)
    ramp = u.ramp_mw_per_s
    return lo, hi, -ramp if -room > 0.0 - u.p0 else 0.0, ramp if room < u.p_n - u.p0 else 0.0


def _hold(u, p, dp_dt, t):
    """Anti-windup: a genset at a bound moves no faster outward than the
    bound does, so the RK4 stages inside a step never count power beyond
    it (``_clamp`` alone holds it only after the step)."""
    lo, hi, rate_lo, rate_hi = _genset_bounds(u, t)
    if p >= hi - 1e-12:
        dp_dt = min(dp_dt, rate_hi)
    if p <= lo + 1e-12:
        dp_dt = max(dp_dt, rate_lo)
    return dp_dt


def _clamp(units, x, t):
    """Hold the genset and fw states to their limits after a step."""
    for u in units:
        if u.model == "genset" and t >= u.join_s:
            lo, hi, _, _ = _genset_bounds(u, t)
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


def simulate(units, dp_event, t_end=20.0, dt=1e-3, f0=F0, damping=0.0, islanding=False, floor=None) -> Trace:
    """Integrate the island frequency after ``dp_event(t)`` (MW, positive a
    deficit: load gained or generation lost) with ``units`` (``Unit``).
    Fixed-step RK4 on Δf and the governors; the frequency-watt ramps, the
    UPS states and the limits are applied after each step. ``islanding``:
    the event opens the PCC, so a UPS that ``transfers`` starts on its
    battery. A ``dp_event`` with an ``advance(t, Δf, RoCoF)`` method is told
    the state after each step and may change itself (E4's pickup).
    ``floor`` (a ``TripFloor``): once Δf crosses it the run stops there, a
    trip (``Trace.trip``), not a number; None runs to ``t_end``."""
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
    saturated, collapse, trip = {}, None, None
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
        crossed = _tripped(floor, x["df"], f0)
        if crossed is not None:
            trip = (float(tt), crossed)
            t, df = t[:i + 1], df[:i + 1]
            dp = {k: v[:i + 1] for k, v in dp.items()}
            break
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
    return Trace(t, df, dp, saturated, collapse, float(rocof0), _ups_logs(units, ups_log), trip=trip)


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
    trip: tuple | None = None    # (t, what), from the ``Trace``
    df_min_hz: float = math.nan  # the lowest and the highest Δf over the event; NaN: read
    df_max_hz: float = math.nan  # ``nadir_hz`` for both (a ``Metrics`` built by hand)


@dataclasses.dataclass(frozen=True)
class TripFloor:
    """Where the island's protection ends the run: Δf below ``lo_hz`` or
    above ``hi_hz`` (absolute Hz; NaN: none on that side), each named by
    what sets it (``trip_floor``)."""
    lo_hz: float = math.nan
    lo_what: str = ""
    hi_hz: float = math.nan
    hi_what: str = ""


def trip_floor(limits, equipment_=(), f0=F0) -> TripFloor:
    """The lowest frequency any limit allows, and the highest: the
    transition and steady-island f_min / f_max and each piece of
    equipment's low and high (``equipment``; an undefined side, NaN, is
    skipped). Beyond it the linear model means nothing (owner,
    2026-10-14), so the event ends as a trip. ``judge`` fails a run that
    reaches either side, since it checks the lowest and the highest Δf, so
    the floor changes the label and the figures, never the verdict. Where
    a UPS window sets it, the run stops before that UPS's transfer is
    modelled; the verdict is the same, the window failed."""
    tr, st = limits["transition"], limits["steady_island"]
    lows = [(tr["f_min_hz"], "transition f_min"), (st["f_min_hz"], "steady-island f_min")]
    highs = [(tr["f_max_hz"], "transition f_max"), (st["f_max_hz"], "steady-island f_max")]
    for label, lo, hi in equipment_:
        if lo == lo:
            lows.append((lo, label))
        if hi == hi:
            highs.append((hi, label))
    lo, lo_what = min(lows, key=lambda c: c[0])
    hi, hi_what = max(highs, key=lambda c: c[0])
    return TripFloor(float(lo), lo_what, float(hi), hi_what)


def _tripped(floor, df, f0):
    """What ``df`` crossed of ``floor``, or None."""
    if floor is None:
        return None
    f = f0 + df
    if f < floor.lo_hz:
        return f"Δf below the {floor.lo_hz:g} Hz floor ({floor.lo_what})"
    if f > floor.hi_hz:
        return f"Δf above the {floor.hi_hz:g} Hz ceiling ({floor.hi_what})"
    return None


def metrics(trace, window_ms=500.0, band_hz=0.1) -> Metrics:
    """The event's frequency figures from a ``Trace``."""
    if trace.collapse is not None:
        return Metrics(trace.rocof0, math.nan, window_ms, math.nan, math.nan, math.nan, math.nan,
                       dict(trace.saturated), trace.collapse)
    if trace.trip is not None:
        return Metrics(trace.rocof0, math.nan, window_ms, math.nan, math.nan, math.nan, math.nan,
                       dict(trace.saturated), None, trace.trip)
    w = window_ms / 1000.0
    iw = min(int(round(w / (trace.t[1] - trace.t[0]))), len(trace.t) - 1)
    rocof_w = (trace.df[iw] - trace.df[0]) / trace.t[iw]
    i_ext = int(np.argmax(np.abs(trace.df)))
    qss = float(trace.df[-1])
    outside = np.flatnonzero(np.abs(trace.df - qss) > band_hz)
    t_ret = float(trace.t[outside[-1]]) if outside.size else 0.0
    return Metrics(trace.rocof0, float(rocof_w), window_ms, float(trace.df[i_ext]), float(trace.t[i_ext]), qss,
                   t_ret, dict(trace.saturated), None, None, float(trace.df.min()), float(trace.df.max()))


@dataclasses.dataclass
class Verdict:
    ok: bool
    binding: str | None          # the most sensitive limit broken, named
    failures: list               # every limit broken, as text
    worst: float = 0.0           # the binding limit's relative exceedance (inf on collapse)


def trip_label(trip):
    """``"trip: Δf below the 47.5 Hz floor (transition f_min) at t = 3.21 s"``."""
    t, what = trip
    return f"trip: {what} at t = {t:.2f} s"


def judge(m, limits, f0=F0, equipment=()) -> Verdict:
    """``m`` against the ``transition`` limits during the event and the
    ``steady_island`` ones after it (requirements ``frequency_limits``
    values), and against each piece of ``equipment``: ``(label, f_low_hz,
    f_high_hz)``, e.g. a UPS input window or a chiller's ``f_trip_hz``.
    The binding limit is the broken one exceeded most **relative to what it
    allows** (f0 − f_min, f_max − f0, the RoCoF limit, the band), so limits
    of different kinds compare. The low limits are read on the lowest Δf
    and the high ones on the highest, so a dip that recovers into an
    overshoot is judged on both."""
    if m.collapse is not None:
        return Verdict(False, COLLAPSE, [COLLAPSE], math.inf)
    if m.trip is not None:
        label = trip_label(m.trip)
        return Verdict(False, label, [label], math.inf)
    tr, st = limits["transition"], limits["steady_island"]
    f_qss = f0 + m.df_qss_hz
    f_lo = f0 + (m.nadir_hz if math.isnan(m.df_min_hz) else m.df_min_hz)
    f_hi = f0 + (m.nadir_hz if math.isnan(m.df_max_hz) else m.df_max_hz)

    def low(label, limit, f):
        return label, (limit - f) / (f0 - limit)

    def high(label, limit, f):
        return label, (f - limit) / (limit - f0)

    checks = [
        (f"transition RoCoF {abs(m.rocof_window_hz_per_s):.2f} Hz/s over {m.window_ms:g} ms against "
         f"{tr['rocof_hz_per_s']:g} Hz/s", (abs(m.rocof_window_hz_per_s) - tr["rocof_hz_per_s"]) / tr["rocof_hz_per_s"]),
        low(f"transition nadir {f_lo:.2f} Hz against {tr['f_min_hz']:g} Hz", tr["f_min_hz"], f_lo),
        high(f"transition peak {f_hi:.2f} Hz against {tr['f_max_hz']:g} Hz", tr["f_max_hz"], f_hi),
    ]
    band = f"steady island {f_qss:.2f} Hz against {st['f_min_hz']:g}-{st['f_max_hz']:g} Hz"
    checks.append(max(low(band, st["f_min_hz"], f_qss), high(band, st["f_max_hz"], f_qss), key=lambda c: c[1]))
    checks.append((f"steady island deviation {abs(m.df_qss_hz):.2f} Hz against {st['qss_band_hz']:g} Hz",
                   (abs(m.df_qss_hz) - st["qss_band_hz"]) / st["qss_band_hz"]))
    for label, lo, hi in equipment:
        checks.append(low(f"{label}, nadir {f_lo:.2f} Hz against {lo:g} Hz", lo, f_lo))
        if hi == hi:
            checks.append(high(f"{label}, peak {f_hi:.2f} Hz against {hi:g} Hz", hi, f_hi))
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
                            join_s=join, start_s=v["start_s"], sync_s=v["sync_s"],
                            unit_mw=v.get("unit_mw", math.nan)))
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
    holds each block's ``Metrics``, judged from nominal (f0 restored between
    blocks). ``complete``: every block settled within its horizon.
    ``dropped`` names what N+k removed: a genset, or ``"NAME: 1 x U MW"``."""
    trace: Trace
    ready_s: float
    block_times_s: list
    blocks: list
    complete: bool
    bridge: list
    dropped: list                # the gensets left out (N+k)


def _remove_units(gens, k):
    """N+k as I2b (owner, 2026-10-14): ``k`` times, the largest unit fails
    to start: a whole genset, or one ``unit_mw`` of a genset that has
    several (its rating, MVA, load step and ramp scale down; H stays on the
    remaining MVA). Returns ``(gensets left, what was removed)``."""
    gens, removed = list(gens), []
    for _ in range(k):
        if not gens:
            break

        def unit(g):
            return g.unit_mw if g.unit_mw == g.unit_mw and 0 < g.unit_mw < g.p_n else g.p_n
        g = max(gens, key=lambda g: (unit(g), g.p_n, g.name))
        u = unit(g)
        if u >= g.p_n - 1e-9:
            gens.remove(g)
            removed.append(g.name)
            continue
        f = (g.p_n - u) / g.p_n
        gens[gens.index(g)] = dataclasses.replace(g, p_n=g.p_n - u, s_n=g.s_n * f, p_max=g.hi * f,
                                                  step_mw=g.step_mw * f, ramp_mw_per_s=g.ramp_mw_per_s * f)
        removed.append(f"{g.name}: 1 x {u:g} MW")
    return gens, removed


def _settle_time(trace, settled, band_hz, settle_hz_per_s):
    """The first time after 0 that Δf is within ``band_hz`` of ``settled``
    and |RoCoF| ≤ ``settle_hz_per_s``: the governors have recovered (inf if
    never within the trace)."""
    if trace.collapse is not None or trace.trip is not None or len(trace.t) < 2:
        return math.inf
    rocof = np.gradient(trace.df, trace.t)
    ok = (np.abs(trace.df - settled) <= band_hz) & (np.abs(rocof) <= settle_hz_per_s) & (trace.t > 0)
    hit = np.flatnonzero(ok)
    return float(trace.t[hit[0]]) if hit.size else math.inf


#: E4 runs each block only until its tail is steady: |RoCoF| at most
#: ``E4_STEADY_HZ_PER_S`` over the last ``E4_STEADY_HOLD_S``. The first run is
#: ``E4_FIRST_S`` long, doubled while the tail still moves, up to ``t_end``.
#: The settled value read from the last sample then differs from the full
#: horizon's by at most the tolerance times the slowest time constant.
E4_STEADY_HZ_PER_S = 1e-6
E4_STEADY_HOLD_S = 5.0
E4_FIRST_S = 20.0


def _steady_tail(trace, hold_s=E4_STEADY_HOLD_S, tol=E4_STEADY_HZ_PER_S):
    """True when the last ``hold_s`` of ``trace`` hold |RoCoF| ≤ ``tol``,
    or the island has collapsed (nothing more to see)."""
    if trace.collapse is not None or trace.trip is not None:
        return True
    if trace.t[-1] < hold_s:
        return False
    sel = trace.t >= trace.t[-1] - hold_s - 1e-12
    return float(np.max(np.abs(np.gradient(trace.df[sel], trace.t[sel])))) <= tol


def _block_run(units, block, t_end, dt, f0, first_s=E4_FIRST_S, floor=None):
    """One E4 block, run until its tail is steady (``_steady_tail``): from
    ``first_s``, doubled each time the tail still moves, at most ``t_end``.
    A slow island (an isochronous genset, a ramp-limited one) is extended
    rather than read early."""
    t = min(first_s, t_end)
    while True:
        tr = simulate(units, step(block), t_end=t, dt=dt, f0=f0, floor=floor)
        if t >= t_end or _steady_tail(tr):
            return tr
        t = min(2.0 * t, t_end)


def e4_ups_bridged(gensets, blocks, ups=(), bridge_s=0.0, redundancy_n=0, band_hz=0.1, settle_hz_per_s=0.05,
                   window_ms=500.0, t_end=600.0, dt=5e-3, f0=F0, steady_stop=True, cache=None,
                   floor=None) -> BridgedStart:
    """E4, the ``ups_bridged`` start (plan I6b): the bus is dead, the UPSs
    carry the IT load, the gensets start and, once every one is ready
    (start + sync, the join I2b uses), pick up ``blocks`` one at a time.
    The ``redundancy_n`` largest units fail to start (N+k as I2b's pickup
    row, ``_remove_units``). ``ups`` is ``(name, it_mw, e_mwh)`` per UPS,
    e_mwh being e(t−1); the bridge check needs each to last
    max(``bridge_s``, ready + the last pickup).

    **Between blocks** (owner, 2026-10-14; ledgered): once a block has
    settled (Δf within ``band_hz`` of −block/ΣK, or of 0 with an isochronous
    genset, and |RoCoF| ≤ ``settle_hz_per_s``), secondary or load-share
    control restores f0, so the next block starts from nominal at the
    loading the earlier blocks left (each genset at its droop share), with
    its load-acceptance step and ramp counted afresh. Each block is
    simulated on its own, until its tail is steady (``_block_run``; at most
    ``t_end``; ``steady_stop`` False runs every block to ``t_end``); ``trace``
    joins them, each up to its settling, the last whole.

    ``cache`` (a dict): the pickup depends only on the gensets left at N+k
    (their dispatch and join reset), the blocks and the run parameters, so
    hours with the same fleet share one; the bridge check runs per call."""
    from gridspine.schema.contracts import ContractError

    gens, dropped = _remove_units([g for g in gensets if g.model == "genset"], redundancy_n)
    if not gens or not blocks:
        raise ContractError("E4 needs a genset left at N+k and the pickup blocks")
    ready = max(g.ready_s for g in gens)
    units = [dataclasses.replace(g, p0=0.0, join_s=0.0) for g in gens]
    key = (repr(units), tuple(float(b) for b in blocks), band_hz, settle_hz_per_s, window_ms, t_end, dt, f0,
           steady_stop, floor)
    if cache is not None and key in cache:
        trace, times, per, complete = cache[key]
    else:
        trace, times, per, complete = _e4_pickup(units, blocks, band_hz, settle_hz_per_s, window_ms, t_end, dt, f0,
                                                 steady_stop, floor)
        if cache is not None:
            cache[key] = (trace, times, per, complete)
    last_pickup = ready + times[-1] if complete else math.inf
    bridge = []
    for name, it, e in ups:
        autonomy = e * 3600.0 / it if it > 0 else math.inf
        needed = max(bridge_s, last_pickup)
        bridge.append(Bridge(name, it, e, autonomy, needed, autonomy >= needed))
    return BridgedStart(trace, ready, times, per, complete, bridge, dropped)


def _e4_pickup(units, blocks, band_hz, settle_hz_per_s, window_ms, t_end, dt, f0, steady_stop, floor=None):
    """E4's blocks on the reset gensets: ``(trace, block times, metrics,
    complete)``."""
    iso = any(g.isochronous for g in units)
    k_droop = sum(g.k for g in units if not g.isochronous)
    times, per, parts, complete, clock = [], [], [], True, 0.0
    for i, block in enumerate(blocks):
        tr = (_block_run(units, block, t_end, dt, f0, floor=floor) if steady_stop
              else simulate(units, step(block), t_end=t_end, dt=dt, f0=f0, floor=floor))
        times.append(clock)
        per.append(metrics(tr, window_ms, band_hz))
        settled = 0.0 if iso else (-block / k_droop if k_droop else math.nan)
        t_set = _settle_time(tr, settled, band_hz, settle_hz_per_s)
        last = i == len(blocks) - 1
        cut = tr.t <= (tr.t[-1] if last or t_set == math.inf else t_set) + 1e-12
        if i:                                     # its 0 is the previous block's settling instant
            cut &= tr.t > 0.0
        parts.append((tr.t[cut] + clock, tr.df[cut]))
        if t_set == math.inf:
            complete = False
            break
        clock += t_set
        j = int(np.flatnonzero(tr.t >= t_set - 1e-12)[0])
        units = [dataclasses.replace(u, p0=u.p0 + float(tr.dp[u.name][j])) for u in units]
    t_all = np.concatenate([a for a, _ in parts])
    df_all = np.concatenate([b for _, b in parts])
    return Trace(t_all, df_all, {}, {}, None, per[0].rocof0_hz_per_s), times, per, complete


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
    "frequency: E4 assumes secondary or load-share control restores f0 between pickup blocks, so each block "
    "starts from nominal at the accumulated loading (owner, 2026-10-14); N+k removes the k largest units, a "
    "genset's unit_mw where it has several",
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
    from gridspine.schema.island import CONVERTER_KINDS, GFM_CONTROLS
    from gridspine.static.island_sc import island_fault_levels

    status = rows.set_index("unit_id")["status"]
    s = {"gfl": 0.0, "gfm": 0.0, "gen": 0.0}
    sources, conv_buses = set(), set()
    for uid, u in campus.units.iterrows():
        if u["kind"] not in (*CONVERTER_KINDS, "genset"):
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
            "collapse_s": m.collapse, "trip_s": m.trip[0] if m.trip else None, "ok": v.ok, "binding": v.binding,
            "worst": v.worst, "emt": "; ".join(flags)}


#: The fixed columns of the sweep and the study tables (plan I6c): a table
#: carries every column whatever events ran, empty where one does not apply.
_METRIC_COLUMNS = ("dp_mw", "rocof0_hz_per_s", "rocof_window_hz_per_s", "nadir_hz", "t_nadir_s", "df_qss_hz",
                   "t_return_s", "saturated", "collapse_s", "trip_s", "ok", "binding", "worst", "emt")
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


def _sweep_units(campus, rows, urows):
    """``e1_every_hour``'s units at one hour: the built ones, the cold
    gensets on their clock, a UPS that transfers left out; the rest on the
    plan's values (no e(t−1), no I3a IT load)."""
    return [u for u in units_from_campus(campus, rows, online=built(rows), cold=cold_gensets(campus, rows, urows))
            if not (u.model == "ups" and u.ups["transfers"])]


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
    floor = trip_floor(limits, eq, f0)
    ih = validate_island_hourly(island_hourly)
    ih = ih[ih["scenario"] == scenario]
    iu = validate_island_unit_hourly(island_unit_hourly) if island_unit_hourly is not None else None
    out, cache = [], {}
    for _, r in ih.sort_values(["period", "hour"]).iterrows():
        period, hour = int(r["period"]), int(r["hour"])
        rows = _hour_rows(hourly, period, hour)
        urows = iu[(iu["period"] == period) & (iu["hour"] == hour)] if iu is not None else None
        units = _sweep_units(campus, rows, urows)
        key = {"period": period, "hour": hour, "scenario": scenario}
        for direction, dp in (("import", r["dp_up_mw"]), ("export", -r["dp_down_mw"])):
            if not abs(dp) > 1e-9 or (direction == "import") != (dp > 0):
                continue
            ck = (repr(units), round(dp, 9), floor)
            if ck not in cache:
                cache[ck] = simulate(units, step(dp), t_end=t_end, dt=dt, f0=f0, floor=floor)
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


def _hour_units(campus, hourly, iu, period, hour):
    """``(rows, unit_rows, units, soc)`` of one hour as the study runs it:
    the built units, the cold gensets, I3a's e(t−1) and UPS IT load."""
    rows = _hour_rows(hourly, period, hour)
    urows = iu[(iu["period"] == period) & (iu["hour"] == hour)] if iu is not None else None
    soc, it = {}, {}
    if urows is not None:
        for _, r in urows.iterrows():
            if r["soc_start_mwh"] == r["soc_start_mwh"]:
                soc[r["unit_id"]] = r["soc_start_mwh"]
            if r["p_ups_it_mw"] == r["p_ups_it_mw"]:
                it[r["unit_id"]] = r["p_ups_it_mw"]
    units = units_from_campus(campus, rows, online=built(rows), cold=cold_gensets(campus, rows, urows), soc=soc)
    units = [dataclasses.replace(u, ups={**u.ups, "it_mw": it[u.name]}) if u.name in it else u for u in units]
    return rows, urows, units, soc


def _island_units(units, i4):
    """The units at I4's island dispatch, every genset joined: the start of
    E2, E3 and E5."""
    p_isl = i4.units.set_index("unit_id")["p_mw"]
    return [dataclasses.replace(u, p0=float(p_isl[u.name]), join_s=0.0) if u.model != "ups" else u for u in units]


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
    floor = trip_floor(limits, eq, f0)               # beyond it every event is a trip, not a number
    iu = validate_island_unit_hourly(island_unit_hourly) if island_unit_hourly is not None else None
    ev, losses, bridge, planned, ups = [], [], [], [], []
    kw = {"t_end": t_end, "dt": dt, "f0": f0, "floor": floor}
    e4_cache = {}                                  # hours with the same fleet share one E4 pickup

    def log_ups(key, event, trace):
        for name, rec in trace.ups.items():
            ups.append({**key, "event": event, "ups": name, "drops": len(rec.drops),
                        "first_drop_s": rec.drops[0] if rec.drops else math.nan, "energy_mwh": rec.energy_mwh,
                        "e_mwh": rec.e_mwh, "t_online_s": rec.t_online_s, "autonomy_ok": rec.autonomy_ok})

    for period, hour in hours:
        key = {"period": int(period), "hour": int(hour)}
        rows, urows, units, soc = _hour_units(campus, hourly, iu, period, hour)
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
                isl = _island_units(units, i4)
                p_isl = i4.units.set_index("unit_id")["p_mw"]
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
                                     window_ms=window, f0=f0, cache=e4_cache, floor=floor)
                for i, m in enumerate(res.blocks):
                    v = judge(m, limits, f0, eq)
                    ev.append({**sk, "event": "E4", "block": i + 1, "dp_mw": rv["pickup_blocks"][i],
                               "t_block_s": res.ready_s + res.block_times_s[i],
                               "rocof_window_hz_per_s": m.rocof_window_hz_per_s, "nadir_hz": m.nadir_hz,
                               "t_nadir_s": m.t_nadir_s, "df_qss_hz": m.df_qss_hz, "t_return_s": m.t_return_s,
                               "trip_s": m.trip[0] if m.trip else None,
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


# ── I7: frequency cuts back into sizing ─────────────────────────────────────

#: Per (period, hour, scenario, kind), at most two rows over that hour's
#: failing events: the tightest feasible cut, and one infeasible row where an
#: event no cut fixes (so an infeasible row never hides a feasible one; plan
#: I7, contract agreed with pypsa-gui's I7). ``required = current + delta``;
#: ``current`` is the hub LP's own value from I3a; ``feasible`` False
#: (``required`` inf) when no cut fixes the event, or the cut is beyond
#: ``CUT_CAP`` times ``current``.
CUT_COLUMNS = ("period", "hour", "scenario", "event", "kind", "required", "current", "delta", "unit", "binding",
               "derivation", "heuristic", "feasible", "note")
CUT_KINDS = {"inertia": "MW·s", "headroom_up": "MW", "headroom_down": "MW", "droop_gain": "MW/Hz"}
#: The scenarios whose cuts map onto the hub's 0⁺ rows (pypsa-gui's I7 has
#: them for ``seamless`` only; ``planned`` gives none, E6 being its event).
CUT_SCENARIOS = ("seamless",)
#: No addition above this multiple of the current value is proposed (the
#: inertia bisection's ceiling; a droop or headroom cut beyond it is
#: infeasible).
CUT_CAP = 100.0
#: The engine's M or ΣK against I3a's beyond this is noted.
CUT_MATCH_TOL = 0.02
CUT_LABEL = "heuristic: the units' response parameters are held at the current solution"
_CUT_T_END = 10.0


def _transient(label):
    return label.startswith("transition") or ", nadir" in label or ", peak" in label


def _steady(label):
    return label.startswith("steady island")


def _gain(u):
    """A unit's droop gain as I2b counts it, MW/Hz: a joined genset's (an
    isochronous one adds none) and a GFM's."""
    if u.model == "genset" and u.join_s <= 0.0:
        return 0.0 if u.isochronous else u.k
    return u.k if u.model in ("gfm_vsm", "gfm_droop") else 0.0


def _response(units, f0=F0):
    """``(M in MW·s, ΣK in MW/Hz)`` of ``units`` at 0⁺: joined gensets and
    unsaturated GFMs, as I2b counts them (an isochronous genset adds no
    gain)."""
    m = 0.0
    for u in units:
        if (u.model == "genset" and u.join_s <= 0.0) or u.model == "gfm_vsm":
            m += u.h * u.s_n
        elif u.model == "gfm_droop":
            m += u.tau_f * u.p_n / (2.0 * u.r)
    return m, sum(_gain(u) for u in units)


def _bisect(passes, cap, rel=1e-3, iters=60):
    """The least x in [0, cap] for which ``passes(x)`` (monotone), or inf."""
    if not passes(cap):
        return math.inf
    lo, hi = 0.0, cap
    for _ in range(iters):
        if hi - lo <= rel * hi:
            break
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if passes(mid) else (mid, hi)
    return hi


def _settled_allowed(m, limits, f0, equipment_):
    """How far the settled frequency may sit from f0 on its side: the
    tightest of the steady qss band and the steady, transition and
    equipment limits (a settled value outside any of them cannot be fixed
    by inertia)."""
    st, tr = limits["steady_island"], limits["transition"]
    if m.df_qss_hz < 0:
        sides = [f0 - st["f_min_hz"], f0 - tr["f_min_hz"], *[f0 - lo for _, lo, _ in equipment_ if lo == lo]]
    else:
        sides = [st["f_max_hz"] - f0, tr["f_max_hz"] - f0, *[hi - f0 for _, _, hi in equipment_ if hi == hi]]
    return min([st["qss_band_hz"], *sides])


def _settle_run(run, units, t_end, tries=5):
    """``run(units, t)`` from the caller's horizon, doubled until the trace
    has settled (Δf moved less than 0.5 % + 1 mHz over its second half) or
    collapsed; ``(trace, t)``."""
    t = t_end
    for i in range(tries):
        if i:
            t *= 2.0
        trace = run(units, t)
        if trace.collapse is not None:
            break
        half = trace.df[int(np.searchsorted(trace.t, 0.5 * trace.t[-1]))]
        if abs(trace.df[-1] - half) <= 0.005 * abs(trace.df[-1]) + 1e-3:
            break
    return trace, t


def cuts_for_event(units, run, limits, f0=F0, equipment_=(), window_ms=500.0, t_end=_CUT_T_END, failing=None,
                   where=""):
    """The cuts one failing event asks for: ``[(kind, delta, binding,
    derivation, feasible, note)]``. ``run(units, t_end)`` re-runs the event;
    ``t_end`` is the horizon it was judged at, from which every run here is
    lengthened until it has settled (``_settle_run``), so a slow island is
    judged on its settled frequency.

    * the settled frequency beyond what any limit allows (``_settled_allowed``)
      → ``droop_gain`` (``_droop_cut``): ΔK = ΣK (|Δf_qss| / Δf_allowed − 1)
      on a linear droop island, less what a unit at its limit cannot give;
    * a transient limit (RoCoF; a nadir or peak, transition or equipment,
      that the settled value meets) → ``inertia``: the least pure-inertia
      increment ΔM (MW·s) for which those limits pass, by bisection on the
      engine with the response held, each run lengthened until it settles;
    * a GFM at a limit → ``headroom_up``/``_down``: what the saturated GFMs
      were asked for beyond their range, from the event re-run without it;
      the droop and inertia cuts then come from that re-run, not from the
      runaway trace (``_saturated_cuts``).

    ``failing``: the binding limit the study gave the event. Where the
    re-run asks for nothing, one infeasible row says the failure was not
    reproduced (``where``: the run's step and horizon), not nothing."""
    tr, t_set = _settle_run(run, units, t_end)
    m = metrics(tr, window_ms)
    v = judge(m, limits, f0, equipment_)
    if m.collapse is not None:
        return [("inertia", math.inf, COLLAPSE, "collapse", False,
                 "no machine joined and every GFM at a limit: the island needs a former, not a cut")]
    if m.saturated:
        out = _saturated_cuts(units, run, m, t_end, limits, f0, equipment_, window_ms)
    else:
        out = _response_cuts(units, run, tr, m, v, t_end, limits, f0, equipment_, window_ms)
    if not out and failing is not None:
        at = ", ".join(x for x in (where, f"run to {t_set:g} s") if x)
        out.append(("droop_gain" if _steady(failing) else "inertia", math.inf, failing, "re-run", False,
                    f"re-run passes; not reproduced at {at}"))
    return out


def _saturated_cuts(units, run, m, t_end, limits, f0, equipment_, window_ms):
    """A GFM at a limit: the trace runs away, so only the headroom is read
    from it, what the saturated GFMs were asked for beyond their range on
    the event re-run with that range lifted. The droop and inertia cuts come
    from that re-run, the headroom cut applied."""
    sat = [u for u in units if u.name in m.saturated]
    free = [dataclasses.replace(u, p_min=-math.inf, p_max=math.inf, i_max_mw=math.inf)
            if u.name in m.saturated else u for u in units]
    tr_free, _ = _settle_run(run, free, t_end)
    up = sum(max(0.0, float(np.max(u.p0 + tr_free.dp[u.name])) - u.hi) for u in sat)
    down = sum(max(0.0, u.lo - float(np.min(u.p0 + tr_free.dp[u.name]))) for u in sat)
    names = ", ".join(sorted(m.saturated))
    out = [(kind, d, f"GFM at a limit: {names}",
            "the saturated GFMs' demand beyond their range, event re-run without it", True, "")
           for kind, d in (("headroom_up", up), ("headroom_down", down)) if d > 1e-9]
    lifted = f"; on the event with {names}'s range lifted (the headroom cut applied)"
    out += [(kind, d, b, der + lifted, ok, note)
            for kind, d, b, der, ok, note in cuts_for_event(free, run, limits, f0, equipment_, window_ms, t_end=t_end)]
    return out


def _droop_cut(units, trace, m, allowed):
    """``(ΔK, clipped)``: the droop gain to add so the island settles within
    ``allowed`` (Hz), from each droop unit's settled ΔP_i in ``trace``. At
    the tighter settled value a unit gives min(ΔP_i, K_i Δf_allowed), so
    ΔK = Σ max(0, |ΔP_i| − K_i Δf_allowed) / Δf_allowed: ΣK (|Δf_qss| /
    Δf_allowed − 1) on a linear droop island, and exact where a unit sits at
    its limit (``clipped``: those units' names)."""
    s = -1.0 if m.df_qss_hz < 0 else 1.0           # a deficit: the units raise their output
    need, clipped = 0.0, []
    for u in units:
        k = _gain(u)
        if k <= 0.0:
            continue
        p = -s * float(trace.dp[u.name][-1])
        need += max(0.0, p - k * allowed)
        if p < k * abs(m.df_qss_hz) * (1.0 - 1e-3) - 1e-6:
            clipped.append(u.name)
    return need / allowed if allowed > 0 else math.inf, clipped


def _response_cuts(units, run, trace, m, v, t_end, limits, f0, equipment_, window_ms):
    """The droop and inertia cuts of an event no GFM saturates in."""
    out = []
    m_now, k_now = _response(units, f0)
    allowed = _settled_allowed(m, limits, f0, equipment_)
    settled_ok = abs(m.df_qss_hz) <= allowed + 1e-9
    if not settled_ok:
        steady = [f for f in v.failures if _steady(f)] or [f for f in v.failures if not f.startswith("transition RoCoF")]
        binding = steady[0] if steady else f"settled {m.df_qss_hz:.3f} Hz against {allowed:g} Hz allowed"
        if k_now > 0:
            dk, clipped = _droop_cut(units, trace, m, allowed)
            out.append(("droop_gain", dk, binding,
                        "Σ max(0, |ΔP_i| − K_i Δf_allowed) / Δf_allowed over the droop units' settled ΔP_i, "
                        "Δf_allowed the tightest of the qss band and the steady, transition and equipment limits",
                        dk < math.inf,
                        f"at a limit: {', '.join(clipped)} (their gain in the total does not help)" if clipped else ""))
        else:
            out.append(("droop_gain", math.inf, binding, "no droop gain in the island", False,
                        "nothing with droop to scale: the island needs a droop unit"))

    def fixable(f):
        return f.startswith("transition RoCoF") or (_transient(f) and settled_ok)

    target = [f for f in v.failures if fixable(f)]
    if target:
        qss = m.df_qss_hz

        def passes(dm):
            extra = Unit("~inertia", "inertia", 0.0, 0.0, 1.0, h=dm)
            t = t_end
            for _ in range(5):
                trace = run([*units, extra], t)
                if trace.collapse is not None:
                    return False
                if abs(trace.df[-1] - qss) <= 0.02 * abs(qss) + 1e-3:
                    break
                t *= 2.0
            vv = judge(metrics(trace, window_ms), limits, f0, equipment_)
            return not any(fixable(f) for f in vv.failures)
        dm = _bisect(passes, CUT_CAP * max(m_now, 1.0))
        out.append(("inertia", dm, target[0], "engine bisection on a pure-inertia increment, response held",
                    dm < math.inf, "" if dm < math.inf else f"no increment up to {CUT_CAP:g} x M passes"))
    return out


def _engine_value(kind, units, f0=F0):
    """The engine's own value of ``kind`` on ``units``: M, ΣK, or the
    formers' headroom."""
    if kind in ("inertia", "droop_gain"):
        return _response(units, f0)[0 if kind == "inertia" else 1]
    formers = [u for u in units if u.model in ("genset", "gfm_droop", "gfm_vsm") and u.join_s <= 0.0]
    return sum((u.hi - u.p0) if kind == "headroom_up" else (u.p0 - u.lo) for u in formers)


def _i3a_current(kind, scenario, units, ih_row, urows, f0):
    """``(current, note)`` for a cut: the hub LP's own value from I3a, else
    the engine's, said so."""
    if kind in ("inertia", "droop_gain"):
        engine = _engine_value(kind, units, f0)
        col = "inertia_mws" if kind == "inertia" else "k_mw_per_hz"
        if ih_row is None:
            return engine, f"no I3a row for {scenario}: current is the engine's {engine:.4g} {CUT_KINDS[kind]}"
        lp = float(ih_row[col])
        off = abs(engine - lp) > CUT_MATCH_TOL * max(abs(lp), 1e-9)
        return lp, (f"engine {engine:.4g} against I3a {col} {lp:.4g} {CUT_KINDS[kind]}" if off else "")
    col = "headroom_up_mw" if kind == "headroom_up" else "headroom_down_mw"
    formers = [u for u in units if u.model in ("genset", "gfm_droop", "gfm_vsm") and u.join_s <= 0.0]
    if urows is None or not len(urows):
        room = _engine_value(kind, units, f0)
        return room, f"no I3a unit rows: current is the engine's {room:.4g} MW"
    names = {u.name for u in formers}
    sel = urows[urows["unit_id"].isin(names)]
    return float(sel[col].fillna(0.0).sum()), ""


def _event_run(campus, hourly, iu, r, ctx, study, sweep, f0=F0):
    """``(units, run, (dt, t_end))`` of one failing event row, rebuilt as its
    table ran it: a ``sweep`` row (``e1_every_hour``) on ``_sweep_units``, a
    study row (``campus_frequency_study``) on ``_hour_units``, each at its
    own ``(dt, t_end)``; None where the island has no dispatch."""
    from gridspine.static.island_flow import island_flow

    period, hour, event = int(r["period"]), int(r["hour"]), str(r["event"])
    dp = float(r["dp_mw"])
    if event == "E1" and r.get("dp_source") == "I3a lossless":
        dt, t_end = sweep
        rows = _hour_rows(hourly, period, hour)
        urows = iu[(iu["period"] == period) & (iu["hour"] == hour)] if iu is not None else None
        units = _sweep_units(campus, rows, urows)

        def run(us, t, dp=dp):
            return simulate(us, step(dp), t_end=t, dt=dt, f0=f0)
        return units, run, sweep
    dt, t_end = study
    if (period, hour) not in ctx:
        rows, urows, units, _ = _hour_units(campus, hourly, iu, period, hour)
        ctx[(period, hour)] = {"rows": rows, "urows": urows, "units": units, "isl": None}
    c = ctx[(period, hour)]
    if event == "E1":
        def run(us, t, dp=dp):
            return e1_islanding(us, dp, t_end=t, dt=dt, f0=f0)
        return c["units"], run, study
    if c["isl"] is None:
        i4 = island_flow(campus, c["rows"], online=built(c["rows"]))
        c["isl"] = _island_units(c["units"], i4) if i4.viable else []
    units = c["isl"]
    if not units:
        return None
    if event == "E3":
        lost = next(u for u in units if u.name == r["unit"])
        units = [u for u in units if u.name != r["unit"]]
        dp = lost.p0

    def run(us, t, dp=dp):
        return simulate(us, step(dp), t_end=t, dt=dt, f0=f0)
    return units, run, study


def propose_cuts(campus, hourly, events, sweep=None, island_hourly=None, island_unit_hourly=None, f0=F0,
                 study=(1e-3, 20.0), sweep_run=(5e-3, 10.0)):
    """I7's cuts (``CUT_COLUMNS``) for the failing events of a frequency
    study: E1, E2, E3 and E5 in ``events`` (``FrequencyStudy.events``, Q8
    reruns aside) and E1 in ``sweep`` (``e1_every_hour``). Each event is
    rebuilt as its table ran it, at that table's ``(dt, t_end)`` (``study``,
    ``sweep_run``: the two functions' defaults, which the driver uses), and
    inverted by ``cuts_for_event``; ``current`` is I3a's (``island_hourly``,
    ``island_unit_hourly``). No cut for E4 or E6. Per (period, hour,
    scenario, kind): the tightest feasible row, and an infeasible one where
    an event has no cut."""
    import pandas as pd

    from gridspine.schema.island import validate_island_hourly, validate_island_unit_hourly

    limits, req = _limits(campus)
    window, eq = req["values"]["rocof_window_ms"], equipment(campus, f0)
    ih = validate_island_hourly(island_hourly) if island_hourly is not None else None
    iu = validate_island_unit_hourly(island_unit_hourly) if island_unit_hourly is not None else None
    failing = []
    if events is not None and len(events):
        e = events[events["event"].isin(["E1", "E2", "E3", "E5"])]
        if "i_max_pu" in e:
            e = e[e["i_max_pu"].isna()]
        failing += [r for _, r in e[~e["ok"].astype(bool)].iterrows()]
    if sweep is not None and len(sweep):
        failing += [r for _, r in sweep[~sweep["ok"].astype(bool)].iterrows()]
    ctx, rows_out = {}, []
    for r in failing:
        period, hour, scenario, event = int(r["period"]), int(r["hour"]), str(r["scenario"]), str(r["event"])
        built_ = _event_run(campus, hourly, iu, r, ctx, study, sweep_run, f0)
        if built_ is None:
            continue
        units, run, (dt, t_end) = built_
        urows = iu[(iu["period"] == period) & (iu["hour"] == hour)] if iu is not None else None
        ih_row = None
        if ih is not None:
            hit = ih[(ih["period"] == period) & (ih["hour"] == hour) & (ih["scenario"] == scenario)]
            ih_row = hit.iloc[0] if len(hit) else None
        fw = any(u.model == "gfl_fw" for u in units)
        binding_ = r.get("binding")
        failed = str(binding_) if binding_ == binding_ and binding_ is not None else "failing in the study"
        for kind, delta, binding, derivation, feasible, note in cuts_for_event(
                units, run, limits, f0, eq, window, t_end=t_end, failing=failed,
                where=f"dt {dt:g} s, t_end {t_end:g} s"):
            current, cnote = _i3a_current(kind, scenario, units, ih_row, urows, f0)
            heuristic = kind != "droop_gain" or fw or note.startswith("at a limit")
            if scenario not in CUT_SCENARIOS:
                feasible, note = False, f"{scenario}: no 0+ row in the hub for this scenario"
            elif event == "E3" and feasible:
                feasible, note = False, (f"a unit trip needs N-1 cover from the other units, not a cut on the "
                                         f"total: the units left need {delta:+.4g} {CUT_KINDS[kind]}")
            elif feasible and delta > CUT_CAP * (ref := max(abs(current), abs(_engine_value(kind, units, f0)), 1.0)):
                feasible, note = False, (f"{delta:.4g} {CUT_KINDS[kind]} is beyond {CUT_CAP:g} x the current "
                                         f"{ref:.4g}: not offered as a cut")
            notes = "; ".join(n for n in (note, cnote) if n)
            rows_out.append({"period": period, "hour": hour, "scenario": scenario, "event": event, "kind": kind,
                             "required": current + delta if feasible else math.inf, "current": current,
                             "delta": delta if feasible else math.inf, "unit": CUT_KINDS[kind], "binding": binding,
                             "derivation": derivation,
                             "heuristic": heuristic, "feasible": bool(feasible), "note": notes})
    df = pd.DataFrame(rows_out, columns=list(CUT_COLUMNS))
    if not len(df):
        return df
    df = df.sort_values("required", ascending=False, kind="stable")
    df = df.drop_duplicates(["period", "hour", "scenario", "kind", "feasible"], keep="first")
    return df.sort_values(["period", "hour", "scenario", "kind", "feasible"], ascending=[True] * 4 + [False],
                          kind="stable").reset_index(drop=True)
