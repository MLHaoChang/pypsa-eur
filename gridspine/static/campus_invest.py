"""Least-cost electrical assets from the library, re-checked by AC load flow
(plan C8).

Part one *recommends* a transformer size and a compensation rating. This
module *buys* them: it picks, for every need the study finds, the cheapest
library candidate, puts the choice on a copy of the campus file, re-solves
every selected hour and case with it, and escalates a need to its next
candidate while a check fails. The "with measures" column of the compliance
report is then a real AC result.

**Needs**, from part one's sizing on the campus as described:

* **Every transformer group** (parallel units between the same two buses).
  A group is always a need, because its units must be bought, unless every
  unit is ``existing`` and part one finds the group adequate: then keeping
  it is a candidate at zero cost.
* **The reactive gap**, capacitive and inductive: part one's compensation in
  Mvar at the worst hour. It is always a need; at a gap of zero its only
  candidate is "none" until a check fails on it.
* **Every cable** whose loading exceeds 100 % in any selected hour or case.
* **Every bus** whose fault level exceeds its switchgear rating, **and every
  bus without a rating** for which the library has switchgear at its
  voltage. The second is a choice: the switchgear has to be bought and its
  rating chosen, and a rated bus turns the switchgear check from
  ``not_rated`` into a judgement. A bus with an adequate rating, or with no
  library entry at its voltage, is not a need.

A cable or bus that first fails in a re-check becomes a need then.

**Candidates**, from the library at the matching voltages, ranked by
``annualised_cost`` (capex annuity plus fixed opex) times the units:

* **Transformer group:** n units of one library size, n = 1..3, between the
  group's bus voltages. With ``m`` the sizing margin, ``S_tot`` the group's
  worst intact flow and ``S_n1`` its worst flow with a unit out (the
  survivors' total; the intact flow for a single unit):

      intact:   S_tot / n * (1 + m) <= rating
      N-1:      S_n1 / (n - 1) * (1 + m) <= rating   (n >= 2, when N-1 is on)

  A group of two or more units keeps its redundancy under N-1, so it gets
  n >= 2 only. A single unit has no N-1 to size for (as in part one).
  ``existing`` units that part one finds adequate are a zero-cost
  candidate. Replacing a group with existing units buys the whole group.
* **Adding units next to an existing group** (plan C13): a group with at
  least one ``existing`` member also gets "keep the existing members and
  add k = 1..3 units of one library entry", costed for the new units only
  (planned members that are not existing are dropped, as by a
  replacement). An entry is offered only when it can run in parallel with
  every existing member (``parallel_check``, the tolerances in
  ``ParallelRules``, all assumed): the same bus voltages, the no-load ratio
  at the current tap positions within ``ratio_tol``, the impedance within
  ``uk_tol``, the same vector-group clock number (an unknown group is
  assumed compatible and flagged), and tap changers that can run together.
  The rating ratio is not limited. Its adequacy pre-filter estimates the
  sharing as ``S_i = S x (S_r,i / uk_i) / sum_j (S_r,j / uk_j)`` (equal
  ratios and X/R, no magnetising current), intact and with each unit out
  in turn, every unit within its own rating with the margin; the AC
  re-check then judges each unit on the solved flows. These candidates sit
  beside the replacements in one list by annual cost.
* **Reactive:** at most one library entry of each kind, each 1..3 units,
  at the compensation bus's voltage, in the combinations STATCOM alone;
  capacitor bank + reactor; capacitor bank alone (inductive gap 0); reactor
  alone (capacitive gap 0); STATCOM + capacitor bank. A combination must
  cover the gap with the margin in both directions (a STATCOM counts both
  ways). With three entries of each kind this is under 200 combinations.
* **Cable:** n = 1..3 parallel runs of one library section at its voltage
  that carry the worst current with the margin (``n * max_i_ka``), costed
  per km times its length times n.
* **Switchgear:** each library rating at the bus's voltage that passes
  ``campus_sc.judge`` at the worst fault level of any period, times the
  number of bays. **Bays** are the branches at the bus (each transformer
  unit, each cable run) plus the elements connected (each unit, each
  compensation entry, and the grid connection at the PCC).

**The loop** (``select_assets``):

1. Every need takes its cheapest candidate.
2. The choices go onto a copy of the campus file: a group's transformers
   are replaced by n units of the library type, every value copied with its
   tag; compensation entries are added at ``compensation_bus``; cables are
   re-conductored or paralleled; buses get the ``ik_rated_ka`` chosen.
3. The copy is built and every selected hour is re-solved, intact and N-1,
   with its reactive dispatch in place (``reactive_need(residual=False)``:
   inverters, then STATCOMs, then steps; nothing virtual). Fault levels are
   re-computed with the new transformers, every STATCOM energised in every
   period (conservative).
4. Every check is run. A failing check escalates the need it maps to:

   ===========================  ===========================================
   failing check                need escalated
   ===========================  ===========================================
   transformer loading > 100 %  that group; also when a unit's re-solved
   or the sizing rule           flow times the margin exceeds its own
                                rating (``size_transformers``, per unit;
                                the new units' own losses count)
   cable loading > 100 %        that cable
   PCC reactive band            the reactive need, to the next candidate
                                that adds Mvar in the short direction
   PCC or campus voltage        the reactive need, to the next candidate
   switchgear (judge)           that bus
   ===========================  ===========================================

   A voltage is mapped to the reactive need because compensation is the
   only voltage-acting asset here. That is an honest limit: compensation
   is dispatched to hold the PCC band, not a voltage, and a voltage problem
   may need a tap change, which is out of scope (no tap-changer
   optimisation). So when a reactive escalation made for a voltage does
   not reduce the worst voltage excursion, the need is reported unresolved
   rather than walked through the whole library.
5. The loop stops when every check passes, when a need runs out of
   candidates (it is reported unresolved, with the failing check, and the
   state with its last candidate is re-checked once more so the report is
   an AC result), when a re-solve does not converge, or at ``max_iter``.

**Timing.** Each asset is chosen once for the whole horizon, sized for the
worst period, and invested in the first period that needs it:

* a transformer group: the first period; but a replacement of, or an
  addition to, a group whose members all exist, the first period in which
  the existing units alone fail (part one's per-period adequacy and
  loading; the first period if they never do);
* a compensation item: the first period in which the final re-check
  dispatches it (the first period with a gap in its direction after the
  inverters); the first period if it is never dispatched;
* a cable: the first period it is overloaded;
* switchgear: the first period its fault level exceeds the old rating, or
  the first period for a bus that had none.

**Replacement at the end of life** (plan C14). Period ``p`` spans ``[p, p
+ w_p)``, ``w_p`` the project's investment-period weighting in years
(``period_spans``: a missing one is the gap between periods, assumed; a
study of one period spans one year), and the horizon ends at ``H = last p +
w_last``. Every purchase is bought again like for like (the same entry and
units, so no re-check) while the year is before ``H``: purchase ``k + 1`` in
``b_1 + floor(k L + 0.5)``, the investment case's rounding, half up, in the
first service year of the new unit. A purchase serves until its successor
starts (or ``H``). Existing assets cost nothing, but a kept transformer, and
an existing cable or compensation entry that is not a need, is listed as a
``keep`` row (``purchase`` 0) and bought again when its own life ends:
``commissioning_year + floor(L + 0.5)``, L and the price from its
``library_id``; with no commissioning year it is assumed at mid-life at the
first period; one whose life ended before the first period is bought in the
first period, flagged overdue; without a library entry its replacement is
unpriced (said on the row, not costed). Unresolved and ``none`` rows are
never bought again. The cost per period counts the capex of every purchase
made in the period's span (``capex_replacement_eur`` the renewals' part)
and each purchase's annuity times the share of the span it serves; where no
purchase starts or ends inside a period this is the rule before C14, every
asset counted in full from its period while it lives. The ranking is
unchanged: candidates are ranked by annualised cost, which a like-for-like
replacement leaves the same, and keeping an existing unit stays zero there
(``ranked_annual``; the run's notes say so).

Unresolved needs are priced in the table but not added to the cost per
period. The table's ``action`` says what each row does: ``buy`` (a new
asset), ``add`` (units added next to existing ones), ``keep`` (existing
units kept, at zero cost; an addition writes one such row beside its
``add`` row), ``replace`` (a group or cable bought where existing ones
stood) or ``renew`` (bought again at the end of a life).

Steady state only: no dynamics, no harmonics, no tap-changer optimisation.
Allowed to import pandapower (``static/``); never pypsa.
"""
import copy
import dataclasses
import math

import pandas as pd

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.network import MAX_NAME_LEN
from gridspine.schema.transformer import check_tap, check_vector_group, ratio as tap_ratio
from gridspine.static.campus_compliance import CHECKS, Q_TOL, campus_compliance
from gridspine.static.campus_flow import SizingCriteria, size_transformers, solve_cases, trafo_groups
from gridspine.static.campus_reactive import compensation_bus, reactive_need, size_compensation
from gridspine.static.campus_sc import campus_fault_levels, judge
from gridspine.templates.campus_assets import annualised_cost, library_entries, value
from gridspine.templates.grid_codes import load_grid_code

#: The compliance checks of an invested campus: part one's, plus the cables.
INVEST_CHECKS = (*CHECKS, "cable_loading")
MAX_UNITS = 3
MAX_ITER = 25
#: A voltage excursion that does not shrink by this much (pu) after a
#: reactive escalation counts as not improved.
V_IMPROVE = 1e-4
_LIB_KIND = {"capacitor_bank": "capacitor_banks", "shunt_reactor": "shunt_reactors", "statcom": "statcoms"}
_COMP_PREFIX = {"capacitor_bank": "CAP", "shunt_reactor": "REACTOR", "statcom": "STATCOM"}
#: The reactive combinations offered (module docstring).
_COMBOS = (("statcom",), ("capacitor_bank", "shunt_reactor"), ("capacitor_bank",), ("shunt_reactor",),
           ("statcom", "capacitor_bank"))
COLUMNS = ["need", "library_id", "kind", "units", "length_km", "invest_period", "capex_eur", "opex_eur_per_a",
           "annualised_eur_per_a", "existing", "status", "reason", "action",
           # plan C14: one row per purchase
           "purchase", "build_year", "service_to", "replaces", "lineage", "commissioned", "commissioned_source"]
#: The flag on an added unit whose vector group, or an existing unit's, is not given.
VG_ASSUMED = "vector group assumed compatible (not given)"
_PARALLEL_NOTE = ("A transformer vendor's application note on paralleling (Rex Power Magnetics, read 2026-10-12); "
                  "IEC 60076-8 not read")


@dataclasses.dataclass(frozen=True)
class ParallelRules:
    """When a library unit may be added in parallel with existing ones (plan
    C13, module docstring). The defaults are the owner's choices of
    2026-10-12, every tolerance assumed:

    * ``ratio_tol``: the no-load ratios at the current tap positions within
      this fraction of each existing unit's (the note: "within about 0.5 %");
    * ``uk_tol``: the impedance within this fraction of each existing unit's
      (owner: 10 %; the note says 7.5 %);
    * ``step_tol_pct``: tap steps within this many percentage points;
    * ``unknown_vector_group``: ``assume_compatible`` (offer it, flagged) or
      ``refuse``, when a vector group is not given on either side.

    The rating ratio of parallel units is not limited (owner)."""
    ratio_tol: float = 0.005
    uk_tol: float = 0.10
    step_tol_pct: float = 0.1
    unknown_vector_group: str = "assume_compatible"

    def __post_init__(self):
        for f in ("ratio_tol", "uk_tol", "step_tol_pct"):
            v = getattr(self, f)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ContractError(f"ParallelRules.{f} must be a non-negative number, got {v!r}")
        if self.unknown_vector_group not in ("assume_compatible", "refuse"):
            raise ContractError("ParallelRules.unknown_vector_group must be 'assume_compatible' or 'refuse', "
                                f"got {self.unknown_vector_group!r}")

    def scope(self) -> dict:
        """The rules as written to ``campus_invest_scope.json``."""
        tag = lambda v, note: {"value": v, "source": "assumed", "note": note}       # noqa: E731
        return {"ratio_tol": tag(self.ratio_tol, f"no-load ratio, at the current taps; {_PARALLEL_NOTE}: "
                                                  "within about 0.5 %"),
                "uk_tol": tag(self.uk_tol, f"impedance against each existing unit's; owner 2026-10-12: 10 %; "
                                           f"{_PARALLEL_NOTE}: typically within 7.5 %"),
                "step_tol_pct": tag(self.step_tol_pct, "tap step, percentage points; assumed"),
                "unknown_vector_group": self.unknown_vector_group,
                "rating_ratio": "not limited"}


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One way to meet a need: ``items`` is ``((kind, library_id, units), ...)``
    (empty for "keep" or "none"); ``annual`` ranks it. ``keep`` names the
    existing transformers an addition keeps (plan C13; empty otherwise),
    and ``note`` is what its row says about it."""
    label: str
    items: tuple
    annual: float
    existing: bool = False
    keep: tuple = ()
    note: str = ""


@dataclasses.dataclass
class Need:
    key: str                     # "transformer <group>", "reactive", "cable <name>", "switchgear <bus>"
    kind: str                    # transformer | reactive | cable | switchgear
    target: dict                 # what the candidates are applied to, and the need's figures
    candidates: list
    first_period: int
    pos: int = 0
    unresolved: str | None = None

    @property
    def choice(self):
        return self.candidates[self.pos] if self.candidates else None


@dataclasses.dataclass
class _Study:
    campus: object
    reactive: dict
    flows: dict
    bus: pd.DataFrame
    trafo: pd.DataFrame
    line: pd.DataFrame
    q: pd.DataFrame
    sizing: pd.DataFrame
    compensation: pd.DataFrame
    short_circuit: pd.DataFrame
    converged: bool


@dataclasses.dataclass
class SelectionState:
    """What C8 ends with, for the MILP's warm start (``campus_milp``): the
    needs at their final choice, the compensation names each reactive
    choice was given, the last re-check, and the study's context."""
    needs: list
    added: dict
    study: _Study
    hours: list
    rows_of: dict
    installed: dict
    periods: list
    lib_by_id: dict
    profile: dict
    as_is: pd.DataFrame
    skip_buses: frozenset
    feasible: bool
    #: ``(spans, source)`` of ``period_spans`` (plan C14).
    horizon: tuple = (None, None)


def _label(items):
    return " + ".join(f"{n} x {lid}" for _, lid, n in items)


def _tag(field):
    return {"value": field["value"], "source": field["source"]}


# --------------------------------------------------------------------------
# studying a campus: part one (as is) and the re-check (with measures)
# --------------------------------------------------------------------------

def _installed(hourly):
    return {int(p): set(b.loc[b["status"] == 1, "unit_id"]) for p, b in hourly.groupby("period")}


def _study(campus, hours, rows_of, installed, req, criteria, recheck) -> _Study:
    """Part one's calculation on ``campus``. ``recheck`` dispatches the real
    equipment only and solves every case with that dispatch in place."""
    reactive, flows = {}, {}
    for key in hours:
        rows = rows_of[key]
        r = reactive_need(campus, rows, req, residual=not recheck)
        reactive[key] = r
        flows[key] = solve_cases(campus, rows, setpoints=r.setpoints if recheck else None)
    return study_from(campus, reactive, flows, installed, req, criteria, recheck)


def study_from(campus, reactive, flows, installed, req, criteria, recheck=True) -> _Study:
    """The study tables from solved hours: ``reactive`` and ``flows`` are
    ``{(period, hour): ReactiveResult}`` and ``{(period, hour): solve_cases(...)}``.
    Shared with the MILP (``campus_milp``), which brings its own dispatch."""
    bus, trafo, line, q = [], [], [], []
    converged = True
    for key in flows:
        r, cases = reactive[key], flows[key]
        converged &= r.converged and all(f.converged for f in cases.values())
        period, hour = key
        for case, f in cases.items():
            k = {"period": period, "hour": hour, "case": case}
            bus += [{**k, **x} for x in f.bus.to_dict("records")]
            trafo += [{**k, **x} for x in f.trafo.to_dict("records")]
            line += [{**k, **x} for x in f.line.to_dict("records")]
        ok = r.converged and abs(r.q_final_mvar) <= req.q_limit_mvar + Q_TOL
        q.append({"period": period, "hour": hour, "converged": r.converged,
                  "q0_mvar": r.q_final_mvar if recheck else r.q0_mvar, "q_final_mvar": r.q_final_mvar,
                  "q_inverters_mvar": r.q_inverters_mvar, "q_comp_mvar": r.q_comp_mvar,
                  "compliant_without": ok if recheck else r.compliant_without})
    statcoms = {str(n) for n, c in campus.compensation.iterrows() if c["kind"] == "statcom"}
    sc = pd.concat([campus_fault_levels(campus, installed[p] | statcoms).assign(period=p) for p in sorted(installed)],
                   ignore_index=True)
    cols = {"trafo": ["period", "hour", "case", "trafo", "s_mva", "loading_pct"],
            "bus": ["period", "hour", "case", "bus", "vm_pu"],
            "line": ["period", "hour", "case", "cable", "i_ka", "loading_pct"]}
    return _Study(campus, reactive, flows, pd.DataFrame(bus, columns=cols["bus"]),
                  pd.DataFrame(trafo, columns=cols["trafo"]), pd.DataFrame(line, columns=cols["line"]),
                  pd.DataFrame(q), size_transformers(flows, campus, criteria), size_compensation(reactive, criteria),
                  sc, converged)


def _compliance(study, req, profile) -> pd.DataFrame:
    net = study.campus.net
    out = campus_compliance(
        bus=study.bus, trafo=study.trafo, reactive=study.q, sizing=study.sizing, compensation=study.compensation,
        short_circuit=study.short_circuit, requirement={"q_limit_mvar": req.q_limit_mvar, "clause": req.clause,
                                                         "source": req.source},
        profile=profile, pcc_bus=str(net.bus.at[int(net.ext_grid["bus"].iloc[0]), "name"]),
        bus_kv=dict(zip(net.bus["name"].astype(str), net.bus["vn_kv"].astype(float))))
    line = study.line
    if len(line):
        j = line["loading_pct"].astype(float).idxmax()
        peak = float(line.at[j, "loading_pct"])
        status = "fail" if peak > 100.0 + 1e-9 else "pass"
        where, detail = (int(line.at[j, "period"]), int(line.at[j, "hour"])), \
            f"worst {line.at[j, 'cable']} ({line.at[j, 'case']})"
    else:
        peak, status, where, detail = math.nan, "pass", (None, None), "no cable"
    cable = {"check": "cable_loading", "status_as_is": status, "status_with_measures": status, "value": peak,
             "limit": 100.0, "unit": "%", "worst_period": where[0], "worst_hour": where[1],
             "clause": "equipment rating (not a grid-code clause)", "source": "assumed", "detail": detail}
    return pd.concat([out, pd.DataFrame([cable])], ignore_index=True)


# --------------------------------------------------------------------------
# needs and their candidates
# --------------------------------------------------------------------------

def _group_buses(campus):
    """``{group name: (hv bus, lv bus)}``."""
    net = campus.net
    name_bus = {str(net.trafo.at[i, "name"]): (str(net.bus.at[int(net.trafo.at[i, "hv_bus"]), "name"]),
                                                str(net.bus.at[int(net.trafo.at[i, "lv_bus"]), "name"]))
                for i in net.trafo.index}
    return {g: name_bus[m[0]] for g, m in trafo_groups(campus).items()}


def _trafo_candidates(library, hv_kv, lv_kv, s_tot, s_n1, n_old, criteria):
    m, out = criteria.margin, []
    for e in library_entries(library, "transformers", hv_kv=hv_kv, lv_kv=lv_kv):
        rating = value(e["s_mva"])
        for n in range(1, MAX_UNITS + 1):
            if criteria.n_minus_1 and n_old >= 2 and n < 2:
                continue                                    # a redundant group stays redundant
            if s_tot / n * (1 + m) > rating + 1e-9:
                continue
            if criteria.n_minus_1 and n >= 2 and s_n1 / (n - 1) * (1 + m) > rating + 1e-9:
                continue
            items = (("transformer", e["id"], n),)
            out.append(Candidate(_label(items), items, n * annualised_cost(e, library)))
    return sorted(out, key=lambda c: (c.annual, c.items[0][2]))


def _read_tap(block, with_pos):
    """A tap block already validated (by the library or the campus build)."""
    return None if not block else check_tap("tap", block, lambda _w, f, spec: float(spec[f]["value"]), with_pos)


def parallel_check(entry, existing: dict, rules: ParallelRules = ParallelRules()):
    """Whether library ``entry`` may run in parallel with the ``existing``
    campus-file transformers ``{name: spec}`` (plan C13, module docstring):
    ``(why_not, flags, offset)``. ``why_not`` is None when it may; ``flags``
    are what its row must say (an unknown vector group); ``offset`` is the
    tap position the new unit takes, in steps from its neutral: the existing
    units' own, so that all run on the same position."""
    lid = entry["id"]
    new_tap = _read_tap(entry.get("tap"), with_pos=False)
    new_vg = entry["vector_group"]["value"] if "vector_group" in entry else None
    old_taps = {n: _read_tap(u.get("tap"), with_pos=True) for n, u in existing.items()}
    ref = next((tp for tp in old_taps.values() if tp is not None), None)
    offset = ref["pos"] - ref["neutral"] if new_tap is not None and ref is not None else 0
    flags = []
    for name, u in existing.items():
        old_tap, old_vg = old_taps[name], u.get("vector_group")
        if new_vg is not None and old_vg is not None:
            if check_vector_group(f"{lid}.vector_group", new_vg) != check_vector_group(f"{name}.vector_group", old_vg):
                return f"{lid}: vector group {new_vg} against {name}'s {old_vg} (another phase displacement)", [], 0
        elif rules.unknown_vector_group == "refuse":
            return f"{lid}: the vector group of {lid if new_vg is None else name} is not given", [], 0
        elif VG_ASSUMED not in flags:
            flags.append(VG_ASSUMED)
        if old_tap is not None and new_tap is None and old_tap["changer"] == "on_load":
            return f"{lid}: {name} has an on-load tap changer and {lid} none, so it cannot follow it", [], 0
        if old_tap is not None and new_tap is not None:
            if new_tap["side"] != old_tap["side"]:
                return f"{lid}: taps on the {new_tap['side'].upper()} side against {name}'s {old_tap['side'].upper()}", [], 0
            if round(abs(new_tap["step_percent"] - old_tap["step_percent"]), 9) > rules.step_tol_pct:
                return (f"{lid}: tap step {new_tap['step_percent']:g} % against {name}'s "
                        f"{old_tap['step_percent']:g} %"), [], 0
            if old_tap["changer"] == "on_load" and new_tap["changer"] != "on_load":
                return f"{lid}: {name} has an on-load tap changer and {lid} an off-circuit one", [], 0
            if (new_tap["min"] - new_tap["neutral"] > old_tap["min"] - old_tap["neutral"]
                    or new_tap["max"] - new_tap["neutral"] < old_tap["max"] - old_tap["neutral"]):
                return f"{lid}: its tap range does not cover {name}'s", [], 0
        r_old = tap_ratio(value(u["vn_hv_kv"]), value(u["vn_lv_kv"]), old_tap,
                          old_tap["pos"] - old_tap["neutral"] if old_tap else 0)
        r_new = tap_ratio(value(entry["hv_kv"]), value(entry["lv_kv"]), new_tap, offset)
        if abs(r_new - r_old) / r_old > rules.ratio_tol:
            return (f"{lid}: ratio {r_new:.4f} against {name}'s {r_old:.4f} "
                    f"({abs(r_new - r_old) / r_old:.2%}, more than {rules.ratio_tol:.2%})"), [], 0
        uk_new, uk_old = value(entry["vk_percent"]), value(u["vk_percent"])
        if abs(uk_new - uk_old) / uk_old > rules.uk_tol:
            return (f"{lid}: impedance {uk_new:g} % against {name}'s {uk_old:g} % "
                    f"(more than {rules.uk_tol:.1%} apart)"), [], 0
    return None, flags, offset


def _shares_hold(units, s_int, s_out, criteria):
    """The pre-filter's estimate (module docstring): ``units`` = ``[(S_r,
    uk)]`` sharing ``s_int`` intact and ``s_out`` with each unit out in
    turn (N-1 on), in proportion to ``S_r / uk``; every unit within its own
    rating with the margin."""
    w = [r / uk for r, uk in units]
    m = criteria.margin

    def holds(idx, total):
        tot_w = sum(w[i] for i in idx)
        return all(total * w[i] / tot_w * (1 + m) <= units[i][0] + 1e-9 for i in idx)
    every = list(range(len(units)))
    if not holds(every, s_int):
        return False
    return not (criteria.n_minus_1 and len(units) >= 2) or all(
        holds([i for i in every if i != j], s_out) for j in every)


def _add_candidates(library, hv_kv, lv_kv, existing, s_int, s_out, criteria, rules, adequacy=True):
    """Keep ``existing`` (``{name: spec}``) and add k = 1..3 units of one
    compatible entry (module docstring), costed for the new units only.
    ``adequacy`` False leaves out the sharing pre-filter (the MILP's open
    set)."""
    if not existing:
        return []
    keep = tuple(existing)
    old = [(value(u["sn_mva"]), value(u["vk_percent"])) for u in existing.values()]
    out = []
    for e in library_entries(library, "transformers", hv_kv=hv_kv, lv_kv=lv_kv):
        why, flags, _ = parallel_check(e, existing, rules)
        if why is not None:
            continue
        note = "; ".join([f"added in parallel with {', '.join(keep)} (existing)", *flags])
        for k in range(1, MAX_UNITS + 1):
            if adequacy and not _shares_hold(old + [(value(e["s_mva"]), value(e["vk_percent"]))] * k,
                                             s_int, s_out, criteria):
                continue
            items = (("transformer", e["id"], k),)
            out.append(Candidate(f"keep {', '.join(keep)} + {k} x {e['id']}", items,
                                 k * annualised_cost(e, library), keep=keep, note=note))
    return out


def _rank(cands):
    """One list by annual cost; at equal cost fewer new units first, and a
    replacement before an addition (a stable sort keeps their order)."""
    return sorted(cands, key=lambda c: (c.annual, c.items[0][2]))


def _coverage(items, lib_by_id):
    cap = ind = 0.0
    for kind, lid, n in items:
        q = n * value(lib_by_id[lid]["q_mvar"])
        cap += q if kind in ("statcom", "capacitor_bank") else 0.0
        ind += q if kind in ("statcom", "shunt_reactor") else 0.0
    return cap, ind


def _reactive_candidates(library, kv, cap_need, ind_need, lib_by_id):
    if cap_need <= 0 and ind_need <= 0:
        none = [Candidate("none", (), 0.0)]
    else:
        none = []
    options = {k: [(e["id"], n) for e in library_entries(library, _LIB_KIND[k], vn_kv=kv) for n in range(1, MAX_UNITS + 1)]
               for k in _LIB_KIND}
    out = []

    def walk(kinds, chosen):
        if not kinds:
            items = tuple(chosen)
            cap, ind = _coverage(items, lib_by_id)
            if cap >= cap_need - 1e-9 and ind >= ind_need - 1e-9:
                cost = sum(n * annualised_cost(lib_by_id[lid], library) for _, lid, n in items)
                out.append(Candidate(_label(items), items, cost))
            return
        for lid, n in options[kinds[0]]:
            walk(kinds[1:], [*chosen, (kinds[0], lid, n)])

    for combo in _COMBOS:
        walk(list(combo), [])
    return none + sorted(out, key=lambda c: (c.annual, sum(n for *_, n in c.items), c.label))


def _cable_candidates(library, kv, length_km, i_ka, criteria):
    out = []
    for e in library_entries(library, "cables", vn_kv=kv):
        for n in range(1, MAX_UNITS + 1):
            if n * value(e["max_i_ka"]) >= i_ka * (1 + criteria.margin) - 1e-12:
                items = (("cable", e["id"], n),)
                out.append(Candidate(_label(items), items, n * annualised_cost(e, library, length_km=length_km)))
    return sorted(out, key=lambda c: (c.annual, c.items[0][2]))


def _switchgear_candidates(library, kv, ik_ka, ip_ka, f_hz):
    out = []
    for e in library_entries(library, "switchgear", vn_kv=kv):
        if judge(ik_ka, ip_ka, value(e["ik_rated_ka"]), f_hz)[0]:
            items = (("switchgear", e["id"], 1),)       # units: the bays, counted when applied
            out.append(Candidate(f"{e['id']} per bay", items, annualised_cost(e, library)))
    return sorted(out, key=lambda c: c.annual)


def open_candidates(need, library, lib_by_id, criteria, f_hz):
    """Every candidate of ``need`` the library offers, before C8's adequacy
    filters: the same generators with a requirement of zero. Transformers
    keep the redundancy rule (a design rule, not a flow), C8's "keep
    (existing)" and C13's compatibility rules for additions; the reactive need gets "none" and every combination; a
    cable every section in 1..3 runs; a bus every rating at its voltage.
    The MILP (``campus_milp``) chooses among these with the AC constraints
    linearised, which is where adequacy is decided."""
    t = need.target
    if need.kind == "transformer":
        keep = [c for c in need.candidates if c.existing]
        return keep + _rank(_trafo_candidates(library, *t["kv"], 0.0, 0.0, t["n_old"], criteria)
                            + _add_candidates(library, *t["kv"], t["existing"], 0.0, 0.0, criteria, t["parallel"],
                                              adequacy=False))
    if need.kind == "reactive":
        return _reactive_candidates(library, t["kv"], 0.0, 0.0, lib_by_id)
    if need.kind == "cable":
        return _cable_candidates(library, t["kv"], t["length_km"], 0.0, criteria)
    if need.kind == "switchgear":
        return _switchgear_candidates(library, t["kv"], 0.0, 0.0, f_hz)
    return []


def _cable_need(name, study, library, criteria, spec):
    rows = study.line[study.line["cable"] == name]
    i_ka = float(rows["i_ka"].max())
    bad = rows[rows["loading_pct"].astype(float) > 100.0 + 1e-9]
    cs = spec["campus"]["cables"][name]
    kv = float(study.campus.net.bus.at[int(study.campus.net.line.loc[
        study.campus.net.line["name"] == name, "from_bus"].iloc[0]), "vn_kv"])
    length = float(cs["length_km"]["value"])
    return Need(f"cable {name}", "cable", {"cable": name, "i_ka": i_ka, "kv": kv, "length_km": length,
                                           "existing": bool(cs.get("existing"))},
                _cable_candidates(library, kv, length, i_ka, criteria), int(bad["period"].min()))


def _switchgear_need(bus, study, library, first_period):
    rows = study.short_circuit[study.short_circuit["bus"] == bus]
    ik, ip, kv = float(rows["ikss_max_ka"].max()), float(rows["ip_max_ka"].max()), float(rows["vn_kv"].iloc[0])
    f_hz = int(round(float(study.campus.net.f_hz)))
    return Need(f"switchgear {bus}", "switchgear", {"bus": bus, "kv": kv, "ik_ka": ik, "ip_ka": ip},
                _switchgear_candidates(library, kv, ik, ip, f_hz), first_period)


def _outage_total(study, members):
    """The group's largest total flow with one of its units out (the as-is
    flows), or its largest intact total for a single unit."""
    t = study.trafo
    rows = t[t["case"].isin([f"N-1:{m}" for m in members]) & t["trafo"].isin(members)]
    if not len(rows):
        rows = t[(t["case"] == "intact") & t["trafo"].isin(members)]
    return float(rows.groupby(["period", "hour", "case"])["s_mva"].sum().max()) if len(rows) else 0.0


def _fail_period(group, members, study, criteria, periods):
    """The first period in which the group as described fails (part one's
    per-unit adequacy, or a loading over 100 %), or the first period."""
    for p in periods:
        flows = {k: v for k, v in study.flows.items() if k[0] == p}
        if not flows:
            continue
        row = size_transformers(flows, study.campus, criteria).set_index("group").loc[group]
        t = study.trafo[(study.trafo["period"] == p) & study.trafo["trafo"].isin(members)]
        if not bool(row["adequate"]) or float(t["loading_pct"].astype(float).max()) > 100.0 + 1e-9:
            return p
    return periods[0]


def _needs(spec, study, library, criteria, periods, lib_by_id, skip_buses=frozenset(),
           parallel: ParallelRules = ParallelRules()):
    camp, c = study.campus, spec["campus"]
    first = periods[0]
    needs = []
    buses = _group_buses(camp)
    kv = dict(zip(camp.net.bus["name"].astype(str), camp.net.bus["vn_kv"].astype(float)))
    trafos = c.get("transformers") or {}
    for r in study.sizing.itertuples(index=False):
        members = trafo_groups(camp)[r.group]
        hv, lv = buses[r.group]
        s_tot = float(r.max_s_intact_mva)
        s_n1 = float(r.max_s_n1_mva) * (r.units - 1) if r.units >= 2 else s_tot
        existing = {m: copy.deepcopy(trafos[m]) for m in members if trafos[m].get("existing")}
        cands = _rank(_trafo_candidates(library, kv[hv], kv[lv], s_tot, s_n1, int(r.units), criteria)
                      + _add_candidates(library, kv[hv], kv[lv], existing, s_tot, _outage_total(study, members),
                                        criteria, parallel))
        peak = study.trafo.loc[study.trafo["trafo"].isin(members), "loading_pct"].astype(float).max()
        whole = len(existing) == len(members)
        if whole and bool(r.adequate) and not peak > 100.0 + 1e-9:
            cands = [Candidate("keep (existing)", (), 0.0, existing=True), *cands]
        needs.append(Need(f"transformer {r.group}", "transformer",
                          {"members": members, "hv_bus": hv, "lv_bus": lv, "s_tot": s_tot, "s_n1": s_n1,
                           "n_old": int(r.units), "kv": (kv[hv], kv[lv]), "existing": existing, "parallel": parallel},
                          cands, _fail_period(r.group, members, study, criteria, periods) if whole else first))
    comp = study.compensation.set_index("direction")
    cap_need = float(comp.at["capacitive", "required_mvar"]) * (1 + criteria.margin)
    ind_need = float(comp.at["inductive", "required_mvar"]) * (1 + criteria.margin)
    cbus = compensation_bus(camp)
    needs.append(Need("reactive", "reactive", {"bus": cbus, "kv": kv[cbus], "cap": cap_need, "ind": ind_need},
                      _reactive_candidates(library, kv[cbus], cap_need, ind_need, lib_by_id), first))
    over = study.line[study.line["loading_pct"].astype(float) > 100.0 + 1e-9]
    for name in dict.fromkeys(over["cable"]):
        needs.append(_cable_need(str(name), study, library, criteria, spec))
    sc = study.short_circuit
    for bus, rows in sc.groupby("bus", sort=False):
        if str(bus) in skip_buses:
            continue
        rated = rows["rated_ka"].notna().all()
        failing = rows[[str(a).lower() == "false" for a in rows["adequate"]]] if rated else rows.iloc[0:0]
        if rated and len(failing):
            needs.append(_switchgear_need(str(bus), study, library, int(failing["period"].min())))
        elif not rated and library_entries(library, "switchgear", vn_kv=float(rows["vn_kv"].iloc[0])):
            needs.append(_switchgear_need(str(bus), study, library, first))
    for n in needs:
        if not n.candidates:
            n.unresolved = _no_candidate(n)
    return needs


def _no_candidate(need):
    t = need.target
    if need.kind == "transformer":
        return (f"no library transformer at the group's voltages carries {t['s_tot']:.1f} MVA intact "
                f"({t['s_n1']:.1f} MVA with a unit out) in 1 to {MAX_UNITS} units with the margin")
    if need.kind == "reactive":
        return (f"no library combination at {t['kv']:g} kV covers {t['cap']:.1f} Mvar capacitive and "
                f"{t['ind']:.1f} Mvar inductive")
    if need.kind == "cable":
        return f"no library cable at {t['kv']:g} kV carries {t['i_ka']:.3f} kA in 1 to {MAX_UNITS} runs with the margin"
    return f"no library switchgear at {t['kv']:g} kV withstands Ik'' {t['ik_ka']:.2f} kA and peak {t['ip_ka']:.2f} kA"


# --------------------------------------------------------------------------
# putting the choices on a copy of the campus file
# --------------------------------------------------------------------------

def _names(base, k, taken, start=2):
    """``k`` new canonical names from ``base``: ``BASE_2``, ``BASE_3``, ...
    (from ``BASE_<start>``)."""
    out, i = [], start
    while len(out) < k:
        sfx = f"_{i}"
        cand = base[: MAX_NAME_LEN - len(sfx)].rstrip("_-") + sfx
        if cand not in taken:
            taken.add(cand)
            out.append(cand)
        i += 1
    return out


def _taken(c):
    return {"GRID", *map(str, c.get("transformers") or {}), *map(str, c.get("cables") or {}), *map(str, c["units"]),
            *(str(x["name"]) for x in c.get("compensation") or [])}


def _bays(c, bus):
    n = sum(1 for t in (c.get("transformers") or {}).values() if bus in (str(t["hv_bus"]), str(t["lv_bus"])))
    n += sum(int(cb.get("parallel", 1)) for cb in (c.get("cables") or {}).values()
             if bus in (str(cb["from_bus"]), str(cb["to_bus"])))
    n += sum(1 for u in c["units"].values() if str(u["bus"]) == bus)
    n += sum(1 for x in c.get("compensation") or [] if str(x["bus"]) == bus)
    return n + (1 if str(c["pcc"]["bus"]) == bus else 0)


def _from_library(e, hv_bus, lv_bus, offset=0):
    """A campus-file transformer of library type ``e``, every value with its
    tag, and its vector group and tap changer when the library gives them
    (the taps at ``offset`` steps from neutral)."""
    tr = {"hv_bus": hv_bus, "lv_bus": lv_bus, "library_id": e["id"],
          "sn_mva": _tag(e["s_mva"]), "vn_hv_kv": _tag(e["hv_kv"]), "vn_lv_kv": _tag(e["lv_kv"]),
          **{f: _tag(e[f]) for f in ("vk_percent", "vkr_percent", "pfe_kw", "i0_percent")}}
    if "vector_group" in e:
        tr["vector_group"] = e["vector_group"]["value"]
    if "tap" in e:
        tap = copy.deepcopy(e["tap"])
        tap["step_percent"] = _tag(tap["step_percent"])
        tap["pos"] = tap["neutral"] + offset
        tr["tap"] = tap
    return tr


def _apply(spec, needs, lib_by_id):
    """A copy of ``spec`` with every need's current choice in place, and
    ``{need key: [compensation names]}`` for the reactive items."""
    out = copy.deepcopy(spec)
    c = out["campus"]
    added = {}
    for need in needs:
        cand = need.choice
        if cand is None or not cand.items:
            continue
        t = need.target
        if need.kind == "transformer" and cand.keep:
            (_, lid, n), = cand.items
            e = lib_by_id[lid]
            old = c["transformers"]
            for m in t["members"]:
                if m not in cand.keep:
                    del old[m]                                   # a planned unit, dropped
            _why, _flags, offset = parallel_check(e, {m: old[m] for m in cand.keep}, t["parallel"])
            for name in _names(cand.keep[0], n, _taken(c)):
                old[name] = _from_library(e, t["hv_bus"], t["lv_bus"], offset)
        elif need.kind == "transformer":
            (_, lid, n), = cand.items
            e = lib_by_id[lid]
            old = c["transformers"]
            first = old[t["members"][0]]
            for m in t["members"]:
                del old[m]
            names = t["members"][:n]
            names += _names(t["members"][0], n - len(names), _taken(c) | set(t["members"]))
            for i, name in enumerate(names):
                tr = _from_library(e, t["hv_bus"], t["lv_bus"])
                if i == 0 and first.get("pypsa_name"):
                    tr["pypsa_name"] = first["pypsa_name"]
                old[name] = tr
        elif need.kind == "reactive":
            comps = c.setdefault("compensation", [])
            names = []
            for kind, lid, n in cand.items:
                e = lib_by_id[lid]
                for name in _names(_COMP_PREFIX[kind], n, _taken(c), start=1):
                    entry = {"name": name, "bus": t["bus"], "kind": kind, "q_mvar": _tag(e["q_mvar"]),
                             "library_id": lid}
                    if kind == "capacitor_bank":
                        entry["steps"] = int(value(e["steps"]))
                    comps.append(entry)
                    names.append(name)
            added[need.key] = names
        elif need.kind == "cable":
            (_, lid, n), = cand.items
            e = lib_by_id[lid]
            cb = c["cables"][t["cable"]]
            cb.update({f: _tag(e[f]) for f in ("r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka")})
            cb.update(parallel=n, library_id=lid)
            cb.pop("existing", None)
        else:
            (_, lid, _n), = cand.items
            where = c["pcc"] if str(c["pcc"]["bus"]) == t["bus"] else c["buses"][t["bus"]]
            where["ik_rated_ka"] = _tag(lib_by_id[lid]["ik_rated_ka"])
    return out, added


# --------------------------------------------------------------------------
# the re-check: which checks fail, and which need each maps to
# --------------------------------------------------------------------------

def _excursion(compliance):
    rows = compliance[compliance["check"].isin(["pcc_voltage", "campus_voltage"])
                      & (compliance["status_as_is"] == "fail")]
    return max((abs(float(r.value) - float(r.limit)) for r in rows.itertuples(index=False)), default=0.0)


def _failures(study, compliance, needs, req, criteria):
    """``[(need key, check, detail, direction)]`` for the re-checked state."""
    out = []
    if not study.converged:
        bad = [k for k, r in study.reactive.items() if not r.converged] + \
              [k for k, cs in study.flows.items() if not all(f.converged for f in cs.values())]
        return [(None, "load_flow", f"the re-solve did not converge at (period, hour) {sorted(set(bad))}", 0)]
    camp = study.campus
    buses = _group_buses(camp)
    sizing = study.sizing.set_index("group")
    for need in needs:
        if need.kind != "transformer":
            continue
        group = next(g for g, hb in buses.items() if hb == (need.target["hv_bus"], need.target["lv_bus"]))
        members = trafo_groups(camp)[group]
        peak = float(study.trafo.loc[study.trafo["trafo"].isin(members), "loading_pct"].astype(float).max())
        row = sizing.loc[group]
        if peak > 100.0 + 1e-9 or not bool(row["adequate"]):
            out.append((need.key, "transformer_loading",
                        f"{group}: loading up to {peak:.1f} %; with the {criteria.margin:.0%} margin "
                        f"{row['worst_unit']} carries {row['utilisation']:.1%} of its own rating", 0))
    for name, rows in study.line.groupby("cable", sort=False):
        peak = float(rows["loading_pct"].astype(float).max())
        if peak > 100.0 + 1e-9:
            out.append((f"cable {name}", "cable_loading", f"{name}: loading up to {peak:.1f} %", 0))
    q = study.q[study.q["converged"].astype(bool)]
    excess = q["q_final_mvar"].abs() - req.q_limit_mvar
    if (excess > Q_TOL).any():
        i = excess.idxmax()
        qf = float(q.at[i, "q_final_mvar"])
        out.append(("reactive", "pcc_reactive",
                    f"PCC Q {qf:.2f} Mvar against +-{req.q_limit_mvar:.2f} at ({int(q.at[i, 'period'])}, "
                    f"{int(q.at[i, 'hour'])})", 1 if qf > 0 else -1))
    for r in compliance.itertuples(index=False):
        if r.check in ("pcc_voltage", "campus_voltage") and r.status_as_is == "fail":
            out.append(("reactive", r.check, f"{r.value:.4f} pu against {r.limit:g} pu; {r.detail}", 0))
    sc = study.short_circuit
    for bus, rows in sc[sc["rated_ka"].notna()].groupby("bus", sort=False):
        bad = rows[[str(a).lower() == "false" for a in rows["adequate"]]]
        if len(bad):
            out.append((f"switchgear {bus}", "switchgear", f"{bus}: {bad['detail'].iloc[0]}", int(bad["period"].min())))
    return out


def _escalate(need, check, direction, lib_by_id):
    """Move ``need`` to its next candidate (see the module docstring); False
    when there is none."""
    start = need.pos + 1
    if need.kind == "reactive" and check == "pcc_reactive" and direction:
        cur = _coverage(need.choice.items, lib_by_id)[0 if direction > 0 else 1] if need.choice else 0.0
        for i in range(start, len(need.candidates)):
            if _coverage(need.candidates[i].items, lib_by_id)[0 if direction > 0 else 1] > cur + 1e-9:
                need.pos = i
                return True
        return False
    if start < len(need.candidates):
        need.pos = start
        return True
    return False


# --------------------------------------------------------------------------
# timing and cost
# --------------------------------------------------------------------------

#: How a period's span was found when the project does not give it (plan C14).
SPAN_GAP_NEXT = "assumed: the gap to the next period"
SPAN_GAP_PREV = "assumed: the gap from the previous period"
SPAN_SINGLE = "single period: one year"
#: The commissioning year of an existing asset that does not give one (owner, 2026-10-12).
MID_LIFE = "assumed: mid-life at the first period"
RANKING_NOTE = ("Replacements are reported, not ranked: candidates are ranked by annualised cost, which a "
                "like-for-like replacement leaves the same, and keeping an existing unit is ranked at zero although "
                "its replacement is costed later (plan C14).")


def _positive(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v > 0


def period_spans(periods, given=None) -> tuple:
    """``({period: years}, {period: source})``: period ``p`` spans ``[p, p +
    years)`` (plan C14). ``given`` is the project's investment-period
    weightings in years (keys may be strings, as read from JSON); a value
    that is missing or not positive is the gap to the next period, or for
    the last period the gap from the previous one, flagged assumed. A study
    of one period without a weighting spans one year (owner, 2026-10-12)."""
    periods = sorted(int(p) for p in periods)
    given = {int(k): v for k, v in (given or {}).items()}
    spans, source = {}, {}
    for i, p in enumerate(periods):
        if _positive(given.get(p)):
            spans[p], source[p] = given[p], "project"
        elif len(periods) == 1:
            spans[p], source[p] = 1, SPAN_SINGLE
        elif i + 1 < len(periods):
            spans[p], source[p] = periods[i + 1] - p, SPAN_GAP_NEXT
        else:
            spans[p], source[p] = p - periods[i - 1], SPAN_GAP_PREV
    return spans, source


def _next_year(anchor, life, k):
    """The year of purchase ``k + 1`` of a lineage first bought in
    ``anchor``: ``anchor + floor(k L + 0.5)`` (the investment case's rounding
    of a non-whole lifetime, half up, in the first service year)."""
    return int(anchor) + math.floor(k * life + 0.5)


def _lineage(anchor, life, horizon_end):
    """``[(year, service_to)]`` of a lineage first bought in ``anchor``: every
    purchase made before ``horizon_end``, each serving until its successor
    starts or the horizon ends."""
    out, k = [], 0
    while True:
        year = _next_year(anchor, life, k) if k else int(anchor)
        if k and year >= horizon_end:
            return out
        out.append((year, min(_next_year(anchor, life, k + 1), horizon_end)))
        k += 1


def purchase_years(start, life, horizon_end) -> list:
    """The years an asset first bought in ``start`` is bought, like for
    like, while the year is before ``horizon_end`` (plan C14)."""
    return [y for y, _ in _lineage(start, life, horizon_end)]


def assumed_commissioning(first, life) -> int:
    """An existing asset with no commissioning year, at mid-life at the first
    period: ``first - floor(L / 2 + 0.5)`` (owner, 2026-10-12)."""
    return int(first) - math.floor(life / 2 + 0.5)


def renewal_years(commissioned, life, first, horizon_end) -> tuple:
    """``(years, overdue)``: the years an existing asset commissioned in
    ``commissioned`` is bought again. Its life ends in ``commissioned +
    floor(L + 0.5)``; one that ended before the first period is overdue and
    bought in the first period (owner, 2026-10-12), then every L after."""
    if _next_year(commissioned, life, 1) < first:
        return purchase_years(first, life, horizon_end), True
    return purchase_years(commissioned, life, horizon_end)[1:], False


def ranked_annual(investment) -> float:
    """The annualised cost the selection ranks on: every chosen and kept row
    but the renewals (plan C14: replacements are reported, not ranked)."""
    inv = investment
    rows = inv[inv["status"].isin(["chosen", "kept"])]
    if "action" in rows:
        rows = rows[rows["action"] != "renew"]
    return float(rows["annualised_eur_per_a"].sum())


def _row_id(lineage, purchase):
    return f"{lineage}|{purchase}"


class _Horizon:
    """The study horizon (plan C14) and the notes the run writes about it."""

    def __init__(self, periods, spans, source):
        self.periods, self.spans, self.source = list(periods), spans, source
        self.end = self.periods[-1] + spans[self.periods[-1]]
        self.notes = []

    def period_of(self, year):
        """The period whose label is the last at or before ``year``."""
        return max(p for p in self.periods if p <= year) if year >= self.periods[0] else self.periods[0]

    def spanned(self, year):
        p = self.period_of(year)
        return p <= year < p + self.spans[p]

    def scope(self):
        return {"period_years": dict(self.spans), "source": dict(self.source), "horizon_end": self.end,
                "notes": [RANKING_NOTE, *self.notes]}


def _renewals(head, lineage, hz, years_and_ends, *, first_purchase, extra=None):
    """The renewal rows of a lineage: like for like, ``head``'s units and
    length, each at ``(year, service_to)``. ``first_purchase`` is the
    purchase number of the first renewal."""
    out = []
    for i, (year, end) in enumerate(years_and_ends):
        k = first_purchase + i
        r = {**head, **(extra or {}), "purchase": k, "build_year": year, "service_to": end,
             "invest_period": hz.period_of(year), "replaces": _row_id(lineage, k - 1), "lineage": lineage,
             "action": "renew", "status": "chosen", "existing": False}
        out.append(r)
        if not hz.spanned(year):
            hz.notes.append(f"{head['need']} ({head['library_id']}): bought again in {year}, in no period's span "
                            f"(after {hz.period_of(year)}'s {hz.spans[hz.period_of(year)]:g} years), so no period's "
                            "capex counts it")
    return out


def _priced_item(entry, library, kind, units, length):
    capex = units * (value(entry["capex_eur_per_km"]) * length if length else value(entry["capex_eur"]))
    return {"library_id": entry["id"], "kind": kind, "units": units, "length_km": length if length else math.nan,
            "capex_eur": capex, "opex_eur_per_a": capex * value(entry["opex_frac"]),
            "annualised_eur_per_a": units * annualised_cost(entry, library, length_km=length),
            "lifetime_a": value(entry["lifetime_a"])}


def _existing_rows(need, members, item_kind, hz, library, lib_by_id, status="kept", renew=True):
    """The keep rows of existing assets ``members`` (``[(name, spec)]``) of
    ``need`` and their renewals (plan C14): one keep row per library entry
    and commissioning year, then its renewals, bought like for like when its
    own life ends. ``item_kind`` is the library kind of an item (a
    compensation entry's own kind). A member without a priced library entry
    is kept with its replacement unpriced."""
    first = hz.periods[0]
    groups = {}
    for name, spec in members:
        lid = spec.get("library_id")
        e = lib_by_id.get(lid) if lid else None
        given = spec.get("commissioning_year")
        if given is not None:
            year, src = int(given["value"]), given["source"]
        elif e is not None:
            year, src = assumed_commissioning(first, value(e["lifetime_a"])), MID_LIFE
        else:
            year, src = None, ""
        groups.setdefault((lid, year, src), []).append((name, spec))
    out = []
    for (lid, year, src), group in groups.items():
        names = [n for n, _ in group]
        lineage = f"{need}|{','.join(names)}"
        spec0 = group[0][1]
        cable = item_kind == "cable"
        units = int(spec0.get("parallel", 1)) if cable else len(group)
        length = float(spec0["length_km"]["value"]) if cable else None
        e = lib_by_id.get(lid) if lid else None
        keep = {"need": need, "library_id": lid, "kind": "keep", "units": units,
                "length_km": length if cable else math.nan, "invest_period": first, "capex_eur": 0.0,
                "opex_eur_per_a": 0.0, "annualised_eur_per_a": 0.0, "existing": True, "status": status, "reason": "",
                "action": "keep", "lifetime_a": math.inf, "purchase": 0, "build_year": year, "commissioned": year,
                "commissioned_source": src, "lineage": lineage, "replaces": None, "service_to": math.nan}
        who = ", ".join(names)
        if e is None:
            why = ("no library_id" if not lid else f"library_id {lid} is not in the library the run used")
            keep["reason"] = f"replacement unpriced: {why}, so its lifetime and price are unknown"
            hz.notes.append(f"{who} ({need}): replacement unpriced: {why}")
            out.append(keep)
            continue
        life = value(e["lifetime_a"])
        if src == MID_LIFE:
            hz.notes.append(f"{who} ({need}): no commissioning year; {MID_LIFE} (commissioned {year})")
        years, overdue = renewal_years(year, life, first, hz.end)
        keep["service_to"] = min(first if overdue else _next_year(year, life, 1), hz.end)
        out.append(keep)
        if not renew:
            continue
        anchor = years[0] if overdue else year
        spans = _lineage(anchor, life, hz.end)
        spans = spans if overdue else spans[1:]
        head = {**keep, **_priced_item(e, library, item_kind, units, length), "reason": ""}
        if overdue:
            due = _next_year(year, life, 1)
            msg = f"overdue: its life ended in {due}, before the first period {first}; bought again in {first}"
            hz.notes.append(f"{who} ({need}): {msg}")
            rows = _renewals(head, lineage, hz, spans[:1], first_purchase=1, extra={"reason": msg})
            rows += _renewals(head, lineage, hz, spans[1:], first_purchase=2)
        else:
            rows = _renewals(head, lineage, hz, spans, first_purchase=1)
        out += rows
    return out


def _investment(needs, added, spec, study, library, lib_by_id, periods, spans=None, span_source=None):
    """The investment table, the cost per period and the horizon's scope
    (module docstring; plan C14 for the purchases)."""
    if spans is None:
        spans, span_source = period_spans(periods)
    hz = _Horizon(sorted(periods), spans, span_source or {p: "project" for p in spans})
    assumed = [p for p, src in hz.source.items() if src.startswith("assumed")]
    if assumed:
        hz.notes.append(f"period span assumed for {', '.join(map(str, assumed))} (the gap between periods): the "
                        "project's investment-period weightings do not give it")
    c = spec["campus"]
    dispatched = {}
    for (period, _hour), r in sorted(study.reactive.items()):
        for name, d in r.dispatch.items():
            if abs(d["q_mvar"]) > 1e-9:
                dispatched.setdefault(name, period)
    rows = []
    trafos = c.get("transformers") or {}
    for need in needs:
        status = "unresolved" if need.unresolved else "chosen"
        base = {"need": need.key, "existing": False, "status": status, "reason": need.unresolved or "",
                "length_km": math.nan, "action": ""}
        cand = need.choice
        if cand is None or not cand.items:
            if cand is not None and cand.existing:
                members = [(m, trafos[m]) for m in need.target["members"]]
                kept = _existing_rows(need.key, members, "transformer", hz, library, lib_by_id,
                                      status="unresolved" if need.unresolved else "kept",
                                      renew=not need.unresolved)
                if need.unresolved:
                    for r in kept:
                        r["reason"] = "; ".join(x for x in (need.unresolved, r["reason"]) if x)
                rows += kept
            else:
                rows.append({**base, "library_id": None, "kind": "none", "units": 0, "invest_period": periods[0],
                             "capex_eur": 0.0, "opex_eur_per_a": 0.0, "annualised_eur_per_a": 0.0,
                             "lifetime_a": math.inf, "status": status if need.unresolved else "not_needed"})
            continue
        if cand.keep:                                     # an addition: the kept units, then the new ones
            rows += _existing_rows(need.key, [(m, trafos[m]) for m in cand.keep], "transformer", hz, library,
                                   lib_by_id, renew=not need.unresolved)
            base["reason"] = "; ".join(x for x in (need.unresolved, cand.note) if x)
        t = need.target
        base["action"] = ("add" if cand.keep else "replace" if t.get("existing") and need.kind in ("transformer", "cable")
                          else "buy")
        names = iter(added.get(need.key, []))
        for kind, lid, n in cand.items:
            e = lib_by_id[lid]
            length = need.target.get("length_km") if kind == "cable" else None
            if kind == "switchgear":
                n = _bays(c, need.target["bus"])
            period = need.first_period
            if need.kind == "reactive":
                mine = [next(names) for _ in range(n)]
                used = [dispatched[m] for m in mine if m in dispatched]
                period = min(used) if used else periods[0]
            lineage = f"{need.key}|{lid}"
            life = value(e["lifetime_a"])
            (year, end), *later = _lineage(int(period), life, hz.end)
            head = {**base, **_priced_item(e, library, kind, n, length), "invest_period": int(period),
                    "purchase": 1, "build_year": year, "service_to": end, "lineage": lineage, "replaces": None,
                    "commissioned": None, "commissioned_source": ""}
            rows.append(head)
            if not need.unresolved:
                rows += _renewals(head, lineage, hz, later, first_purchase=2, extra={"reason": ""})
    needed = {n.key for n in needs}
    for name, cb in (c.get("cables") or {}).items():
        if cb.get("existing") and cb.get("library_id") and f"cable {name}" not in needed:
            rows += _existing_rows(f"cable {name}", [(str(name), cb)], "cable", hz, library, lib_by_id)
    new = {x for names in added.values() for x in names}
    for x in c.get("compensation") or []:
        if x.get("library_id") and str(x["name"]) not in new:
            rows += _existing_rows(f"compensation {x['name']}", [(str(x["name"]), x)], x["kind"], hz, library,
                                   lib_by_id)
    inv = pd.DataFrame(rows, columns=[*COLUMNS, "lifetime_a"])
    for col in ("purchase", "build_year", "commissioned"):
        inv[col] = pd.array(inv[col].astype(float), dtype="Int64")
    end = inv["service_to"].astype(float)
    inv["service_to"] = pd.array(end, dtype="Int64") if (end.dropna() % 1 == 0).all() else end
    return inv, _cost(inv, hz), hz.scope()


def _cost(inv, hz):
    """The cost per period (plan C14): the capex of every purchase made in
    the period's span, the part of it that buys again, and each purchase's
    annuity times the share of the span it serves."""
    counted = inv[inv["status"].isin(["chosen", "kept"]) & (inv["purchase"].fillna(0) >= 1)]
    cost = []
    for p in hz.periods:
        w = hz.spans[p]
        b = counted["build_year"].astype(float)
        made = counted[(b >= p) & (b < p + w)]
        served = sum(float(r.annualised_eur_per_a) * max(0.0, min(float(r.service_to), p + w)
                                                          - max(float(r.build_year), p)) / w
                     for r in counted.itertuples(index=False))
        cost.append({"period": int(p), "capex_eur": float(made["capex_eur"].sum()),
                     "capex_replacement_eur": float(made.loc[made["action"] == "renew", "capex_eur"].sum()),
                     "annualised_eur_per_a": served})
    return pd.DataFrame(cost, columns=["period", "capex_eur", "capex_replacement_eur", "annualised_eur_per_a"])


def with_measures(as_is, compliance) -> pd.DataFrame:
    """The as-is compliance with the re-checked one as its "with measures"
    columns."""
    after = compliance.set_index("check")
    final = as_is.copy()
    final["status_with_measures"] = final["check"].map(after["status_as_is"])
    final["value_with_measures"] = final["check"].map(after["value"])
    final["detail_with_measures"] = final["check"].map(after["detail"])
    return final


def _pcc_table(study):
    return pd.DataFrame([{"period": p, "hour": h, "case": case, "converged": f.converged, "p_mw": f.pcc_p_mw,
                          "q_mvar": f.pcc_q_mvar, "losses_mw": f.losses_mw}
                         for (p, h), cases in sorted(study.flows.items()) for case, f in cases.items()])


def _dispatch_table(study):
    rows = []
    for (period, hour), r in sorted(study.reactive.items()):
        rows.append({"period": period, "hour": hour, "element": "inverters", "kind": "inverters", "steps": None,
                     "q_mvar": r.q_inverters_mvar})
        rows += [{"period": period, "hour": hour, "element": name, "kind": d["kind"], "steps": d["steps"],
                  "q_mvar": d["q_mvar"]} for name, d in r.dispatch.items()]
    return pd.DataFrame(rows, columns=["period", "hour", "element", "kind", "steps", "q_mvar"])


# --------------------------------------------------------------------------
# the selector
# --------------------------------------------------------------------------

def select_assets(campus_spec, hourly, selection, library, req, profile, criteria: SizingCriteria = SizingCriteria(),
                  max_iter: int = MAX_ITER, pcc_switchgear: bool = True,
                  parallel: ParallelRules = ParallelRules(), period_years=None) -> dict:
    """Least-cost assets for ``campus_spec`` at the selected hours, AC-checked
    (module docstring). ``profile`` is a grid-code profile or its name.

    Returns ``investment``, ``cost``, ``compliance``, ``dispatch``, ``spec``
    (the invested campus file), ``history`` and ``unresolved``, and the
    re-solved ``pcc`` flows (per hour and case) and ``short_circuit``.

    ``pcc_switchgear`` says who buys the PCC's switchgear (owner decision,
    2026-10-05: a study setting, costed to the campus by default). False
    means the grid operator owns it: the PCC bus is never a need, and its
    switchgear row reports what it finds without buying anything. The
    choice is returned as ``scope``, with ``parallel``, the rules for adding
    units next to existing ones (``ParallelRules.scope``).

    ``period_years`` is ``{period: years}``, the project's investment-period
    weightings (plan C14, ``period_spans``); None takes every span as
    assumed. The horizon they give, and its notes, are ``scope["horizon"]``."""
    profile = load_grid_code(profile) if isinstance(profile, str) else profile
    lib_by_id = {e["id"]: e for kind in ("transformers", "cables", "capacitor_banks", "shunt_reactors", "statcoms",
                                         "switchgear") for e in library[kind]}
    hours = [(int(p), int(h)) for p, h in selection[["period", "hour"]].itertuples(index=False)]
    if not hours:
        raise ContractError("no selected hours to study; rank the campus first")
    rows_of = {(p, h): hourly[(hourly["period"] == p) & (hourly["hour"] == h)] for p, h in hours}
    installed = _installed(hourly)
    periods = sorted(installed)
    spans, span_source = period_spans(periods, period_years)
    base = _study(build_campus(campus_spec), hours, rows_of, installed, req, criteria, recheck=False)
    as_is = _compliance(base, req, profile)
    skip = frozenset() if pcc_switchgear else frozenset({str(campus_spec["campus"]["pcc"]["bus"])})
    needs = _needs(campus_spec, base, library, criteria, periods, lib_by_id, skip, parallel)
    history, last_voltage, stop = [], None, any(n.unresolved for n in needs)
    for it in range(1, max_iter + 1):
        spec, added = _apply(campus_spec, needs, lib_by_id)
        study = _study(build_campus(spec), hours, rows_of, installed, req, criteria, recheck=True)
        compliance = _compliance(study, req, profile)
        fails = [f for f in _failures(study, compliance, needs, req, criteria)
                 if f[1] != "switchgear" or f[0].split(" ", 1)[1] not in skip]
        if not fails or stop:
            break
        if it == max_iter:
            for key, check, detail, _ in fails:
                for need in needs:
                    if need.key == key and not need.unresolved:
                        need.unresolved = f"{check}: {detail}; stopped at the iteration cap ({max_iter})"
            break
        by_key = {n.key: n for n in needs}
        if fails[0][1] == "load_flow":
            needs.append(Need("load flow", "load_flow", {}, [], periods[0], unresolved=fails[0][2]))
            history.append({"iteration": it, "need": "load flow", "from": "", "to": "", "check": "load_flow",
                            "detail": fails[0][2]})
            break
        done = set()
        for key, check, detail, extra in fails:
            if key in done:
                continue
            need = by_key.get(key)
            if need is None:                                     # first seen in this re-check
                name = key.split(" ", 1)[1]
                need = (_cable_need(name, study, library, criteria, spec) if check == "cable_loading"
                        else _switchgear_need(name, study, library, extra))
                need.pos = -1
                needs.append(need)
                by_key[key] = need
            done.add(key)
            before = need.choice.label if need.choice is not None and need.pos >= 0 else "as described"
            if check in ("pcc_voltage", "campus_voltage"):
                now = _excursion(compliance)
                if last_voltage is not None and now >= last_voltage - V_IMPROVE:
                    need.unresolved = (f"{check}: {detail}; a reactive escalation did not reduce the excursion. "
                                       "Compensation is dispatched to the PCC band, not to a voltage: a tap change "
                                       "or voltage control may be needed (out of scope)")
                    history.append({"iteration": it, "need": key, "from": before, "to": before, "check": check,
                                    "detail": detail})
                    stop = True
                    continue
                last_voltage = now
            if not _escalate(need, check, extra if check == "pcc_reactive" else 0, lib_by_id):
                need.unresolved = f"{check}: {detail}; no further candidate in the library" if need.candidates \
                    else _no_candidate(need)
                if need.pos < 0:
                    need.pos = 0
                stop = True
                after = "none left"
            else:
                after = need.choice.label
            history.append({"iteration": it, "need": key, "from": before, "to": after, "check": check,
                            "detail": detail})
    investment, cost, horizon = _investment(needs, added, spec, study, library, lib_by_id, periods, spans,
                                            span_source)
    final = with_measures(as_is, compliance)
    unresolved = [{"need": n.key, "reason": n.unresolved} for n in needs if n.unresolved]
    return {"investment": investment.drop(columns=["lifetime_a"]), "cost": cost, "compliance": final,
            "dispatch": _dispatch_table(study), "spec": spec,
            "history": pd.DataFrame(history, columns=["iteration", "need", "from", "to", "check", "detail"]),
            "unresolved": unresolved, "pcc": _pcc_table(study), "short_circuit": study.short_circuit,
            "scope": {"pcc_switchgear": "campus" if pcc_switchgear else "grid_operator", "parallel": parallel.scope(),
                      "horizon": horizon},
            "state": SelectionState(needs, added, study, hours, rows_of, installed, periods, lib_by_id, profile, as_is,
                                    skip, not fails and not unresolved, (spans, span_source))}
