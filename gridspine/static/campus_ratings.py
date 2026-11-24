"""
Switchgear and cable ratings chosen from the study, not only declared (DC review 09).

Both choices read results the campus study already has and pick the smallest
adequate entry of a ladder. Neither changes the campus: a declared rating
stays what it is, and a declared rating below the chosen one is flagged.

**Switchgear, per bus.** From the IEC 60909 max case of every period
(``campus_sc.campus_fault_levels``) the duty is

* Ik'', the initial symmetrical current;
* Ib = mu x Ik'', the symmetrical breaking current (IEC 60909-0 §4.5). mu is
  1 unless given: the conservative value, exact far from a generator. As
  mu <= 1, Ib never exceeds Ik'';
* Ith = Ik'' x sqrt(m + n), the thermal equivalent current over the fault
  duration Tk (IEC 60909-0 §4.8). m, the heat of the DC component, is
  computed from kappa = ip / (sqrt(2) Ik''), f and Tk (``dc_factor_m``); n,
  the AC decay, is 1 unless given (far from a generator, conservative near
  one). Against a rated short-time current Ithr for the rated duration Tkr
  (IEC 62271-1) the check is Ith <= Ithr when Tk <= Tkr, else
  Ith sqrt(Tk) <= Ithr sqrt(Tkr): the duty compared is Ith referred to Tkr,
  Ith x sqrt(max(Tk, Tkr) / Tkr);
* ip, the peak (max case only).

The chosen rating is the smallest library switchgear at the bus voltage
whose rated short-time current covers max(Ik'', Ib, Ith referred to Tkr)
x (1 + margin) and whose rated peak (``campus_sc.judge``: ``PEAK_FACTOR[f]``
x rated) covers ip x (1 + margin). With no library entry at that voltage,
the IEC 62271-1 R10 ladder ``SWITCHGEAR_LADDER_KA`` is used, unpriced. The
governing quantity is the one with the highest utilisation of the chosen
rating, with the period it came from. A period whose Ik'' or ip is missing
or not finite is not evaluated; a bus with no evaluated period is
``not_evaluated``, never rated.

**Cables, per run.** The design current is the largest ``i_ka`` over every
hour of the intact case and of every transformer outage (``N-1:<trafo>``);
the study has no cable-outage case, so that is the N-1 it can give. A
library cable at the run's voltage, in ``n`` = 1..3 parallel runs, passes
when

* ampacity: n x ``max_i_ka`` x derating >= I x (1 + margin);
* voltage drop: 100 sqrt(3) I L (R cos phi + X sin phi) / (n U) <= limit %;
* short-circuit withstand: S >= Ith sqrt(Tk) / k (IEC 60949 adiabatic), with
  Ith the thermal equivalent current over the clearing time Tk at the end
  with the larger value, not divided between parallel runs. Without a fault
  level at both ends the withstand is not evaluated (``ith_ka`` NaN), never
  passed.

The passing (cable, n) with the lowest capex is chosen (fewer runs, then the
smaller section, on a tie); the governing criterion is the one with the highest
utilisation. Derating, the voltage-drop limit, the power factor, the
clearing time and the conductor are ``CableAssumptions``, every one
``assumed``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd

from gridspine.static.campus_sc import PEAK_FACTOR, judge
from gridspine.templates.campus_assets import library_entries, value

#: Rated short-time currents, kA: the R10 series of IEC 62271-1 §5.6.
SWITCHGEAR_LADDER_KA = (16.0, 20.0, 25.0, 31.5, 40.0, 50.0, 63.0, 80.0)
#: Parallel runs a cable choice may use (as ``campus_invest.MAX_UNITS``).
MAX_RUNS = 3
#: Adiabatic short-circuit factor k, A s^0.5 / mm2, XLPE insulated, 90 -> 250 degC (IEC 60949).
K_FACTOR = {"Al": 94.0, "Cu": 143.0}
WITHSTAND = "short-circuit withstand"


def _number(
    name: str, x, *, low: float = 0.0, low_open: bool = True, high: float | None = None
) -> None:
    """Raise ``ValueError`` naming ``name`` unless ``x`` is a finite number in range."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError(f"{name} must be a finite number, got {x!r}")
    if (x <= low) if low_open else (x < low):
        raise ValueError(
            f"{name} must be {'above' if low_open else 'at least'} {low:g}, got {x!r}"
        )
    if high is not None and x > high:
        raise ValueError(f"{name} must be at most {high:g}, got {x!r}")


def check_margin(margin) -> None:
    """The sizing margin: finite and not negative (0 is no margin)."""
    _number("margin", margin, low=0.0, low_open=False)


@dataclass(frozen=True)
class SwitchgearAssumptions:
    """IEC 60909-0 / 62271-1 values the study does not compute; each is ``assumed``."""

    mu: float = 1.0  # Ib / Ik'' (§4.5): 1 far from a generator, conservative near one
    n: float = 1.0  # AC-decay heat factor of Ith (§4.8): 1 far from a generator, conservative near one
    tk_s: float = 1.0  # fault duration Tk the thermal current is taken over
    tkr_s: float = (
        1.0  # rated duration of short circuit Tkr (IEC 62271-1 preferred value)
    )

    def __post_init__(self):
        _number("mu", self.mu, high=1.0)
        _number("n", self.n, high=1.0)
        _number("tk_s", self.tk_s)
        _number("tkr_s", self.tkr_s)


@dataclass(frozen=True)
class CableAssumptions:
    """Installation and protection values the campus file does not give; each is ``assumed``."""

    derating: float = (
        0.8  # grouping x soil x temperature, on the library's reference ampacity
    )
    vd_limit_pct: float = 3.0  # steady-state voltage drop along one run
    pf: float = 0.95  # load power factor for the voltage drop
    t_clear_s: float = 1.0  # fault clearing time Tk for the withstand
    conductor: str = "Al"  # the library's cables are aluminium; Cu gives the copper k

    def __post_init__(self):
        _number("derating", self.derating, high=1.0)
        _number("vd_limit_pct", self.vd_limit_pct)
        _number("pf", self.pf, high=1.0)
        _number("t_clear_s", self.t_clear_s)
        if self.conductor not in K_FACTOR:
            raise ValueError(
                f"conductor must be one of {sorted(K_FACTOR)}, got {self.conductor!r}"
            )

    @property
    def k(self) -> float:
        """A s^0.5 / mm2, XLPE 90 -> 250 degC (IEC 60949): ``K_FACTOR[conductor]``."""
        return K_FACTOR[self.conductor]


def dc_factor_m(kappa: float, f_hz: float, tk_s: float) -> float:
    """
    IEC 60909-0 §4.8 heat factor of the DC component:
    m = (exp(4 f Tk ln(kappa - 1)) - 1) / (2 f Tk ln(kappa - 1)).
    No DC component (kappa <= 1) gives 0; kappa >= 2 gives the limit 2.
    """
    if kappa <= 1.0:
        return 0.0
    if kappa >= 2.0:
        return 2.0
    x = math.log(kappa - 1.0)
    return (math.exp(4 * f_hz * tk_s * x) - 1) / (2 * f_hz * tk_s * x)


_UNITS = {
    "mu": "",
    "n": "",
    "tk_s": "s",
    "tkr_s": "s",
    "derating": "",
    "vd_limit_pct": "%",
    "pf": "",
    "t_clear_s": "s",
    "conductor": "",
    "k": "A s^0.5/mm2",
}


def assumptions_table(sg: SwitchgearAssumptions, cb: CableAssumptions) -> list[dict]:
    """The assumptions as rows ``{applies_to, name, value, unit, source}``, for the schedule's footer."""
    rows = [
        {"applies_to": "switchgear", "name": k, "value": v}
        for k, v in asdict(sg).items()
    ]
    rows += [
        {"applies_to": "cables", "name": k, "value": v} for k, v in asdict(cb).items()
    ]
    rows.append({"applies_to": "cables", "name": "k", "value": cb.k})
    return [{**r, "unit": _UNITS[r["name"]], "source": "assumed"} for r in rows]


def thermal_current(
    ikss_ka: float, ip_ka: float, f_hz: float, tk_s: float, n: float = 1.0
) -> float:
    """Ith = Ik'' sqrt(m + n) over ``tk_s`` (IEC 60909-0 §4.8), kA."""
    kappa = ip_ka / (math.sqrt(2) * ikss_ka) if ikss_ka > 0 else 0.0
    return ikss_ka * math.sqrt(dc_factor_m(kappa, f_hz, tk_s) + n)


def switchgear_duty(
    ikss_ka: float,
    ip_ka: float,
    f_hz: float,
    a: SwitchgearAssumptions = SwitchgearAssumptions(),
) -> dict:
    """
    ``{"Ik''", "Ib", "Ith", "Ith@Tkr", "ip"}`` in kA for one bus and
    period: Ith over Tk, and Ith referred to the rated duration Tkr.
    """
    ith = thermal_current(ikss_ka, ip_ka, f_hz, a.tk_s, a.n)
    return {
        "Ik''": ikss_ka,
        "Ib": a.mu * ikss_ka,
        "Ith": ith,
        "Ith@Tkr": ith * math.sqrt(max(a.tk_s, a.tkr_s) / a.tkr_s),
        "ip": ip_ka,
    }


#: The quantities the rated short-time current covers, in the order a tie names them.
_SHORT_TIME = ("Ik''", "Ib", "Ith@Tkr")
_COLUMN = {
    "Ik''": "ikss_ka",
    "Ib": "ib_ka",
    "Ith": "ith_ka",
    "Ith@Tkr": "ith_tkr_ka",
    "ip": "ip_ka",
}


def _label(q: str, a: SwitchgearAssumptions) -> str:
    if q != "Ith@Tkr":
        return q
    return (
        "Ith"
        if a.tk_s <= a.tkr_s
        else f"Ith over {a.tk_s:g} s referred to {a.tkr_s:g} s"
    )


def _above(need: float, limit: float) -> str:
    """``need`` printed with enough decimals that it never reads equal to ``limit``."""
    for d in range(2, 10):
        s = f"{need:.{d}f}"
        if float(s) != limit:
            return s
    return repr(need)


def _switchgear_options(library, vn_kv):
    """``[(id, rated_ka, capex_eur|None)]`` smallest first: the library's at ``vn_kv``, else the ladder."""
    lib = library_entries(library, "switchgear", vn_kv=vn_kv) if library else []
    if lib:
        return sorted(
            (
                (e["id"], float(value(e["ik_rated_ka"])), float(value(e["capex_eur"])))
                for e in lib
            ),
            key=lambda o: o[1],
        )
    return [(f"IEC 62271-1 {r:g} kA", r, None) for r in SWITCHGEAR_LADDER_KA]


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def choose_switchgear(
    short_circuit: pd.DataFrame,
    library: dict | None,
    f_hz: int,
    margin: float,
    a: SwitchgearAssumptions = SwitchgearAssumptions(),
) -> pd.DataFrame:
    """
    One row per bus from ``campus_fault_levels`` rows (with ``period``):
    ``bus``, ``vn_kv``, the worst duty (``ikss_ka``, ``ib_ka``, ``ith_ka``,
    ``ith_tkr_ka``, ``ip_ka``), ``tk_s``, ``tkr_s``, ``chosen_id``,
    ``chosen_ka``, ``chosen_ip_ka``, ``capex_eur``, ``governing``,
    ``utilisation``, ``declared_ka``, ``flag`` (``declared_below_chosen``,
    ``beyond_ladder``, ``not_evaluated`` or None). The result does not depend
    on the order of the rows.
    """
    check_margin(margin)
    sc = short_circuit.copy()
    if "period" not in sc:
        sc["period"] = 0
    rows = []
    for bus, block in sc.groupby("bus", sort=False):
        worst: dict = {}
        for r in block.sort_values("period", kind="stable").itertuples(index=False):
            if not (_finite(r.ikss_max_ka) and _finite(r.ip_max_ka)):
                continue  # not evaluated: never a duty of nan or 0
            for q, x in switchgear_duty(
                float(r.ikss_max_ka), float(r.ip_max_ka), f_hz, a
            ).items():
                if q not in worst or x > worst[q][0]:
                    worst[q] = (x, int(r.period))
        vn_kv = float(block["vn_kv"].iloc[0])
        declared = (
            block["rated_ka"].dropna()
            if "rated_ka" in block
            else pd.Series(dtype=float)
        )
        declared_ka = float(declared.iloc[0]) if len(declared) else math.nan
        row = {
            "bus": str(bus),
            "vn_kv": vn_kv,
            **{
                col: worst[q][0] if q in worst else math.nan
                for q, col in _COLUMN.items()
            },
            "tk_s": a.tk_s,
            "tkr_s": a.tkr_s,
            "declared_ka": declared_ka,
        }
        empty = {
            "chosen_id": None,
            "chosen_ka": math.nan,
            "chosen_ip_ka": math.nan,
            "capex_eur": math.nan,
            "utilisation": math.nan,
        }
        if not worst:
            row.update(
                empty,
                governing="no fault level was computed at this bus",
                flag="not_evaluated",
            )
            rows.append(row)
            continue
        q_st = max(_SHORT_TIME, key=lambda k: (worst[k][0], -_SHORT_TIME.index(k)))
        need_st, need_ip = worst[q_st][0] * (1 + margin), worst["ip"][0] * (1 + margin)
        options = _switchgear_options(library, vn_kv)
        chosen = next(
            (o for o in options if judge(need_st, need_ip, o[1], f_hz)[0]), None
        )
        if chosen is None:
            largest = options[-1][1]
            peak = PEAK_FACTOR[int(f_hz)] * largest
            short = need_st / largest >= need_ip / peak
            q, need, limit = (
                (q_st, need_st, largest) if short else ("ip", need_ip, peak)
            )
            what = "rated short-time current" if short else "rated peak"
            row.update(
                empty,
                flag="beyond_ladder",
                governing=(
                    f"{_label(q, a)} {_above(need, limit)} kA with the {margin:.0%} margin, above the "
                    f"largest {what} {limit:g} kA at {vn_kv:g} kV"
                ),
            )
        else:
            cid, rated, capex = chosen
            peak = PEAK_FACTOR[int(f_hz)] * rated
            util = {q: worst[q][0] / rated for q in _SHORT_TIME}
            util["ip"] = worst["ip"][0] / peak
            q = max(
                util, key=lambda k: (util[k], -list(util).index(k))
            )  # ties: first in Ik'', Ib, Ith, ip
            row.update(
                chosen_id=cid,
                chosen_ka=rated,
                chosen_ip_ka=peak,
                capex_eur=math.nan if capex is None else capex,
                governing=f"{_label(q, a)} {worst[q][0]:.2f} kA, period {worst[q][1]}",
                utilisation=util[q],
                flag="declared_below_chosen" if declared_ka < rated else None,
            )
        rows.append(row)
    return pd.DataFrame(rows)


def cable_checks(
    entry: dict,
    n: int,
    i_ka: float,
    length_km: float,
    vn_kv: float,
    ith_ka: float,
    margin: float,
    a: CableAssumptions = CableAssumptions(),
) -> dict:
    """
    Utilisation per criterion (<= 1 passes) of ``n`` runs of library cable
    ``entry``. ``ith_ka`` is the thermal equivalent current over
    ``a.t_clear_s``; NaN leaves the withstand out (not evaluated).
    """
    r, x = float(value(entry["r_ohm_per_km"])), float(value(entry["x_ohm_per_km"]))
    sin = math.sqrt(max(0.0, 1 - a.pf**2))
    vd_pct = 100 * math.sqrt(3) * i_ka * length_km * (r * a.pf + x * sin) / (n * vn_kv)
    out = {
        "ampacity": i_ka
        * (1 + margin)
        / (n * float(value(entry["max_i_ka"])) * a.derating),
        "voltage drop": vd_pct / a.vd_limit_pct,
        "_vd_pct": vd_pct,
        "_s_min_mm2": math.nan,
    }
    if _finite(ith_ka):
        s_min = ith_ka * 1e3 * math.sqrt(a.t_clear_s) / a.k
        out[WITHSTAND] = s_min / float(value(entry["cross_section_mm2"]))
        out["_s_min_mm2"] = s_min
    return out


def choose_cable(
    library: dict,
    *,
    vn_kv: float,
    length_km: float,
    i_ka: float,
    ith_ka: float,
    margin: float,
    a: CableAssumptions = CableAssumptions(),
) -> dict:
    """
    The cheapest passing ``(entry, n)``: ``chosen_id``, ``conductor``,
    ``k``, ``runs``, ``section_mm2``, ``chosen_i_ka`` (n x max_i_ka, before
    derating), ``capex_eur``, ``governing``, ``utilisation``, ``vd_pct``,
    ``s_min_mm2``, ``withstand_evaluated``; ``chosen_id`` None when nothing
    in the library passes.
    """
    check_margin(margin)
    evaluated = _finite(ith_ka)
    best = None
    for e in library_entries(library, "cables", vn_kv=vn_kv) if library else []:
        for n in range(1, MAX_RUNS + 1):
            c = cable_checks(e, n, i_ka, length_km, vn_kv, ith_ka, margin, a)
            crit = {k: v for k, v in c.items() if not k.startswith("_")}
            if max(crit.values()) > 1 + 1e-12:
                continue
            key = (
                n * float(value(e["capex_eur_per_km"])) * length_km,
                n,
                float(value(e["cross_section_mm2"])),
            )
            if best is None or key < best[0]:
                gov = max(crit, key=lambda k: (crit[k], -list(crit).index(k)))
                best = (
                    key,
                    {
                        "chosen_id": e["id"],
                        "runs": n,
                        "section_mm2": key[2],
                        "chosen_i_ka": n * float(value(e["max_i_ka"])),
                        "capex_eur": key[0],
                        "governing": gov,
                        "utilisation": crit[gov],
                        "vd_pct": c["_vd_pct"],
                        "s_min_mm2": c["_s_min_mm2"],
                    },
                )
    base = {"conductor": a.conductor, "k": a.k, "withstand_evaluated": evaluated}
    if best is None:
        return {
            **base,
            "chosen_id": None,
            "runs": 0,
            "section_mm2": math.nan,
            "chosen_i_ka": math.nan,
            "capex_eur": math.nan,
            "governing": f"no library cable at {vn_kv:g} kV passes in 1..{MAX_RUNS} runs",
            "utilisation": math.nan,
            "vd_pct": math.nan,
            "s_min_mm2": math.nan,
        }
    return {**base, **best[1]}
