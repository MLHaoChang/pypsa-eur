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
  candidate. Partly existing groups are replaced whole; adding units next
  to the existing ones is not offered.
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
   transformer loading > 100 %  that group; also when the re-solved flow
   or the sizing rule           times the margin exceeds the units (the
                                new units' own losses count)
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

* a transformer group: the first period;
* a compensation item: the first period in which the final re-check
  dispatches it (the first period with a gap in its direction after the
  inverters); the first period if it is never dispatched;
* a cable: the first period it is overloaded;
* switchgear: the first period its fault level exceeds the old rating, or
  the first period for a bus that had none.

Its annualised cost counts in every period from that one while the period
is within its lifetime (``invest <= period < invest + lifetime``; period
labels are years). Replacement at the end of life is not modelled.
Existing assets cost nothing. Unresolved needs are priced in the table but
not added to the cost per period.

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
           "annualised_eur_per_a", "existing", "status", "reason"]


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One way to meet a need: ``items`` is ``((kind, library_id, units), ...)``
    (empty for "keep" or "none"); ``annual`` ranks it."""
    label: str
    items: tuple
    annual: float
    existing: bool = False


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
    reactive, flows, bus, trafo, line, q = {}, {}, [], [], [], []
    converged = True
    for key in hours:
        rows = rows_of[key]
        r = reactive_need(campus, rows, req, residual=not recheck)
        cases = solve_cases(campus, rows, setpoints=r.setpoints if recheck else None)
        reactive[key], flows[key] = r, cases
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


def _cable_need(name, study, library, criteria, spec):
    rows = study.line[study.line["cable"] == name]
    i_ka = float(rows["i_ka"].max())
    bad = rows[rows["loading_pct"].astype(float) > 100.0 + 1e-9]
    cs = spec["campus"]["cables"][name]
    kv = float(study.campus.net.bus.at[int(study.campus.net.line.loc[
        study.campus.net.line["name"] == name, "from_bus"].iloc[0]), "vn_kv"])
    length = float(cs["length_km"]["value"])
    return Need(f"cable {name}", "cable", {"cable": name, "i_ka": i_ka, "kv": kv, "length_km": length},
                _cable_candidates(library, kv, length, i_ka, criteria), int(bad["period"].min()))


def _switchgear_need(bus, study, library, first_period):
    rows = study.short_circuit[study.short_circuit["bus"] == bus]
    ik, ip, kv = float(rows["ikss_max_ka"].max()), float(rows["ip_max_ka"].max()), float(rows["vn_kv"].iloc[0])
    f_hz = int(round(float(study.campus.net.f_hz)))
    return Need(f"switchgear {bus}", "switchgear", {"bus": bus, "kv": kv, "ik_ka": ik, "ip_ka": ip},
                _switchgear_candidates(library, kv, ik, ip, f_hz), first_period)


def _needs(spec, study, library, criteria, periods, lib_by_id):
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
        cands = _trafo_candidates(library, kv[hv], kv[lv], s_tot, s_n1, int(r.units), criteria)
        peak = study.trafo.loc[study.trafo["trafo"].isin(members), "loading_pct"].astype(float).max()
        if all(trafos[m].get("existing") for m in members) and bool(r.adequate) and not peak > 100.0 + 1e-9:
            cands = [Candidate("keep (existing)", (), 0.0, existing=True), *cands]
        needs.append(Need(f"transformer {r.group}", "transformer",
                          {"members": members, "hv_bus": hv, "lv_bus": lv, "s_tot": s_tot, "s_n1": s_n1,
                           "n_old": int(r.units)}, cands, first))
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
        if need.kind == "transformer":
            (_, lid, n), = cand.items
            e = lib_by_id[lid]
            old = c["transformers"]
            first = old[t["members"][0]]
            for m in t["members"]:
                del old[m]
            names = t["members"][:n]
            names += _names(t["members"][0], n - len(names), _taken(c) | set(t["members"]))
            for i, name in enumerate(names):
                tr = {"hv_bus": t["hv_bus"], "lv_bus": t["lv_bus"], "library_id": lid,
                      "sn_mva": _tag(e["s_mva"]), "vn_hv_kv": _tag(e["hv_kv"]), "vn_lv_kv": _tag(e["lv_kv"]),
                      **{f: _tag(e[f]) for f in ("vk_percent", "vkr_percent", "pfe_kw", "i0_percent")}}
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
                        f"{group}: loading up to {peak:.1f} %, {row['required_unit_mva']:.1f} MVA per unit needed "
                        f"with the {criteria.margin:.0%} margin against {row['unit_rating_mva']:g} MVA", 0))
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

def _investment(needs, added, spec, study, library, lib_by_id, periods):
    c = spec["campus"]
    dispatched = {}
    for (period, _hour), r in sorted(study.reactive.items()):
        for name, d in r.dispatch.items():
            if abs(d["q_mvar"]) > 1e-9:
                dispatched.setdefault(name, period)
    rows = []
    for need in needs:
        status = "unresolved" if need.unresolved else "chosen"
        base = {"need": need.key, "existing": False, "status": status, "reason": need.unresolved or "",
                "length_km": math.nan}
        cand = need.choice
        if cand is None or not cand.items:
            kind = "keep" if cand is not None and cand.existing else "none"
            st = status if need.unresolved else ("kept" if kind == "keep" else "not_needed")
            rows.append({**base, "library_id": None, "kind": kind, "units": 0, "invest_period": periods[0],
                         "capex_eur": 0.0, "opex_eur_per_a": 0.0, "annualised_eur_per_a": 0.0,
                         "existing": kind == "keep", "status": st, "lifetime_a": math.inf})
            continue
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
            capex = n * (value(e["capex_eur_per_km"]) * length if length else value(e["capex_eur"]))
            rows.append({**base, "library_id": lid, "kind": kind, "units": n,
                         "length_km": length if length else math.nan, "invest_period": int(period),
                         "capex_eur": capex, "opex_eur_per_a": capex * value(e["opex_frac"]),
                         "annualised_eur_per_a": n * annualised_cost(e, library, length_km=length),
                         "lifetime_a": value(e["lifetime_a"])})
    inv = pd.DataFrame(rows, columns=[*COLUMNS, "lifetime_a"])
    counted = inv[inv["status"].isin(["chosen", "kept"])]
    cost = []
    for p in periods:
        live = counted[(counted["invest_period"] <= p) & (p < counted["invest_period"] + counted["lifetime_a"])]
        cost.append({"period": int(p), "capex_eur": float(counted.loc[counted["invest_period"] == p, "capex_eur"].sum()),
                     "annualised_eur_per_a": float(live["annualised_eur_per_a"].sum())})
    return inv, pd.DataFrame(cost)


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
                  max_iter: int = MAX_ITER) -> dict:
    """Least-cost assets for ``campus_spec`` at the selected hours, AC-checked
    (module docstring). ``profile`` is a grid-code profile or its name.

    Returns ``investment``, ``cost``, ``compliance``, ``dispatch``, ``spec``
    (the invested campus file), ``history`` and ``unresolved``."""
    profile = load_grid_code(profile) if isinstance(profile, str) else profile
    lib_by_id = {e["id"]: e for kind in ("transformers", "cables", "capacitor_banks", "shunt_reactors", "statcoms",
                                         "switchgear") for e in library[kind]}
    hours = [(int(p), int(h)) for p, h in selection[["period", "hour"]].itertuples(index=False)]
    if not hours:
        raise ContractError("no selected hours to study; rank the campus first")
    rows_of = {(p, h): hourly[(hourly["period"] == p) & (hourly["hour"] == h)] for p, h in hours}
    installed = _installed(hourly)
    periods = sorted(installed)
    base = _study(build_campus(campus_spec), hours, rows_of, installed, req, criteria, recheck=False)
    as_is = _compliance(base, req, profile)
    needs = _needs(campus_spec, base, library, criteria, periods, lib_by_id)
    history, last_voltage, stop = [], None, any(n.unresolved for n in needs)
    for it in range(1, max_iter + 1):
        spec, added = _apply(campus_spec, needs, lib_by_id)
        study = _study(build_campus(spec), hours, rows_of, installed, req, criteria, recheck=True)
        compliance = _compliance(study, req, profile)
        fails = _failures(study, compliance, needs, req, criteria)
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
    investment, cost = _investment(needs, added, spec, study, library, lib_by_id, periods)
    after = compliance.set_index("check")
    final = as_is.copy()
    final["status_with_measures"] = final["check"].map(after["status_as_is"])
    final["value_with_measures"] = final["check"].map(after["value"])
    final["detail_with_measures"] = final["check"].map(after["detail"])
    unresolved = [{"need": n.key, "reason": n.unresolved} for n in needs if n.unresolved]
    return {"investment": investment.drop(columns=["lifetime_a"]), "cost": cost, "compliance": final,
            "dispatch": _dispatch_table(study), "spec": spec,
            "history": pd.DataFrame(history, columns=["iteration", "need", "from", "to", "check", "detail"]),
            "unresolved": unresolved}
