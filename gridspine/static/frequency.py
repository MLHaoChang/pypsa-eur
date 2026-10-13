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
* a **PQ** GFL unit, and a UPS on line, do not respond.

The load damping D defaults to 0: a data centre is mostly constant power, so
that is the conservative choice (ledgered).

**Labelled a screening** (``LABEL``): no voltage dynamics, no inter-unit
oscillation, no current-limit dynamics.

numpy only; no pandapower. ``units_from_campus`` reads I4's unit plan so
both share one P0 and one K = P_n/R (plan N1).
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
)


@dataclasses.dataclass(frozen=True)
class Unit:
    """One unit of the island, as the frequency model sees it. ``model`` is
    ``genset``, ``gfm_droop``, ``gfm_vsm``, ``gfl_fw`` or ``none``. Powers
    in MW; ``r`` is the droop in pu on P_n; ``h`` H on S_n (a genset's H, a
    VSM's H_v)."""
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
    label: str = LABEL


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
            elif u.model == "gfl_fw":
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


def simulate(units, dp_event, t_end=20.0, dt=1e-3, f0=F0, damping=0.0) -> Trace:
    """Integrate the island frequency after ``dp_event(t)`` (MW, positive a
    deficit: load gained or generation lost) with ``units`` (``Unit``).
    Fixed-step RK4 on Δf and the governors; the frequency-watt ramps and the
    limits are applied after each step."""
    units = list(units)
    x = {"df": 0.0}
    for u in units:
        if u.model in ("genset", "gfl_fw"):
            x[u.name] = 0.0
        if u.model == "genset" and u.isochronous:
            x[f"{u.name}~i"] = 0.0
    n = int(round(t_end / dt)) + 1
    t = np.linspace(0.0, (n - 1) * dt, n)
    df = np.zeros(n)
    dp = {u.name: np.zeros(n) for u in units}
    saturated, collapse = {}, None
    rocof0, _, _ = _balance(units, dict(x), 0.0, dp_event, f0, damping)
    if rocof0 is None:
        return Trace(t[:1], df[:1], {k: v[:1] for k, v in dp.items()}, {}, 0.0, math.nan)

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
            elif u.model in ("genset", "gfl_fw"):
                dp[u.name][i] = x[u.name] if (u.model == "gfl_fw" or tt >= u.join_s) else 0.0
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
    return Trace(t, df, dp, saturated, collapse, float(rocof0))


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


def judge(m, limits, f0=F0, equipment=()) -> Verdict:
    """``m`` against the ``transition`` limits during the event and the
    ``steady_island`` ones after it (requirements ``frequency_limits``
    values), and against each piece of ``equipment``: ``(label, f_low_hz,
    f_high_hz)``, e.g. a UPS input window or a chiller's ``f_trip_hz``.
    The binding limit is the broken one exceeded most **relative to what it
    allows** (f0 − f_min, f_max − f0, the RoCoF limit, the band), so limits
    of different kinds compare."""
    if m.collapse is not None:
        return Verdict(False, COLLAPSE, [COLLAPSE])
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
    return Verdict(not broken, broken[0][1] if broken else None, [label for _, label in broken])


# ── units and events ───────────────────────────────────────────────────────

def units_from_campus(campus, rows, online=None, cold=()):
    """The island's ``Unit`` list at one hour, from I4's unit plan (its P0
    and its references), so ΔP and K are shared with ``island_flow``.
    ``cold`` gensets join at ``start_s`` + ``sync_s`` from P = 0."""
    from gridspine.static.island_flow import IslandSettings, _build

    _, plan, _ = _build(campus, rows, online, IslandSettings())
    out = []
    for uid, r in plan.iterrows():
        if r["role"] in ("offline", "load") or r["block"] is None:
            continue
        v = r["block"]["values"]
        p0, p_n, s_n = float(r["p0_mw"]), float(r["p_n"]), float(r["s_n"])
        if r["kind"] == "genset":
            join = (v["start_s"] + v["sync_s"]) if uid in cold else 0.0
            out.append(Unit(uid, "genset", 0.0 if uid in cold else p0, p_n, s_n, 0.0, p_n,
                            r=v["droop_pct"] / 100.0 if "droop_pct" in v else math.nan, h=v["H_s"], t_g=v["T_g_s"],
                            isochronous=r["block"]["governor"] == "isochronous",
                            step_mw=v["load_step_max_pct"] / 100.0 * p_n, ramp_mw_per_s=v["ramp_pu_per_s"] * p_n,
                            join_s=join))
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
    site exports, an over-frequency). Cold gensets join on their own clock."""
    return simulate(units, step(import_mw), **kw)


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
