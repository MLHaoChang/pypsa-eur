"""The PCC compliance report of a campus study (plan C5).

``campus_compliance(...)`` turns the study's own tables into one row per
check (``CHECKS``):

``pcc_reactive``
    The PCC reactive exchange against the study's band (C4b). The clause
    and tag are the requirement's.
``pcc_voltage``
    The PCC voltage, in every selected hour and case, against the profile's
    band for its nominal voltage. The clause and tag are the band's.
``campus_voltage``
    Every other bus. If the profile carries a ``campus_voltage`` band, that
    band and its clause and tag are used. Otherwise the buses are held to the
    same banded limits as the PCC. Inside the campus these are a **design**
    limit, not a code requirement, so that fallback is tagged ``assumed``,
    unless the band is still ``extracted`` (read from a document and not yet
    confirmed), which it then stays.
``transformer_loading``
    The highest loading of any transformer, intact or N-1, against 100 % of
    its rating (equipment). A campus without a transformer has no such row;
    the campus voltage row (or, without other buses, the PCC voltage row)
    then says so (``NO_TRANSFORMER``), so a reader can tell "not
    applicable" from "forgotten".
``switchgear``
    Ik'' and peak against each rated bus (C4c). Buses without a rating make
    the check ``not_rated``.
``pcc_q_band``
    The PCC Q of every converged hour against the profile's P-dependent band
    (``q_capability_pq``), read at that hour's intact PCC P. A P outside the
    band's P range fails, naming the hour and P.
``pcc_power_factor``
    The PCC power factor of every converged hour against ``pf_min_lagging``
    (Q > 0) or ``pf_min_leading`` (Q < 0). Hours with ``|P|`` below
    ``PF_MIN_P_SHARE`` of ``p_ref`` are left out: the power factor of a site
    near zero exchange says nothing about its reactive behaviour.

Both read ``requirement["p_ref_mw"]`` (the connection capacity the profile's
per-unit values scale by) and the intact rows of ``pcc``. Without the
profile's limit, without ``p_ref_mw`` or without ``pcc``, the row is
``unknown`` (detail "no limit set" for a missing limit): never pass, never
fail.

Signs are the study's at the connection point: ``p_mw`` is the ``ext_grid``
P, positive when the site imports (``res_ext_grid``); Q is positive when the
grid supplies reactive power into the site, i.e. a lagging power factor.

Every row carries:
- ``status_as_is``, for the campus as described;
- ``status_with_measures``, with the recommended transformer ratings and
  compensation. A voltage check becomes ``not_rechecked`` whenever any
  measure is recommended, because those measures move voltages and the
  study did not re-solve with them in place.
- ``value``, ``limit``, ``unit``, the worst ``(period, hour)``, the case of
  that reading (``worst_case``: ``intact`` or ``N-1:<unit>``; None for the
  reactive exchange, one figure per hour, and for the fault level, one per
  period), ``clause``, ``source`` and ``detail``; a voltage row also the
  band its limit is an edge of (``limit_min``, ``limit_max``; None for the
  other checks). A reactive pass that relies on ``Q_TOL`` (the exchange
  above the band by no more than it) says so in its detail. ``source`` is the limit's own tag, so a limit
  read from an uploaded grid code and not yet confirmed says ``extracted``.

pandas only, plus the profile's band lookup.
"""
import math

import pandas as pd

from gridspine.templates.grid_codes import band_for, q_band_at

CHECKS = ("pcc_reactive", "pcc_voltage", "campus_voltage", "transformer_loading", "switchgear", "pcc_q_band",
          "pcc_power_factor")
#: Added to the clause of the PCC checks of a normally open tie
#: (``requirement["tie_open"]``): they are made at the tie's rating, as
#: grid-connected operation.
OPEN_TIE_CLAUSE = " [applies with the tie closed (grid-connected operation), not to islanded operation]"
#: The transformer row's note for a normally open tie's PCC transformers.
OPEN_TIE_TRAFO = "normally open tie: rated at p_connection_mw, not sized from islanded hours"
#: The note a campus without a transformer carries in its voltage row.
NO_TRANSFORMER = "no transformer between the PCC and the site buses ({kv} kV): transformer loading not assessed"
#: Mvar tolerance for "on the band edge" after correction (the solver's tol).
Q_TOL = 0.011
#: Hours whose PCC ``|P|`` is below this share of ``p_ref`` have no power
#: factor check: near zero exchange the ratio is undefined or meaningless.
PF_MIN_P_SHARE = 0.1
#: The detail of a row whose limit the profile does not set.
NO_LIMIT = "no limit set"


def _true(flag) -> bool:
    """A judgement read back from a table: True, numpy True or "True"."""
    return str(flag).strip().lower() == "true"


#: Columns added after the others were in use: they come last, here and in the
#: invested table (``campus_invest.with_measures``).
ADDED = ("worst_case", "limit_min", "limit_max")
COLUMNS = ["check", "status_as_is", "status_with_measures", "value", "limit", "unit", "worst_period", "worst_hour",
           "clause", "source", "detail", *ADDED]


def _row(check, as_is, with_measures, value, limit, unit, where, clause, source, detail, case=None, band=None):
    period, hour = where if where else (None, None)
    lo, hi = (band["v_min"], band["v_max"]) if band else (None, None)
    return {"check": check, "status_as_is": as_is, "status_with_measures": with_measures,
            "value": value, "limit": limit, "unit": unit, "worst_period": period, "worst_hour": hour,
            "worst_case": case, "clause": clause, "source": source, "detail": detail, "limit_min": lo, "limit_max": hi}


def _voltage(rows: pd.DataFrame, profile, bus_kv, fixed_band=None):
    """Worst voltage against its band: ``(status, value, limit, where, band)``.
    The worst is the largest excursion outside a band, or else the reading
    closest to its band edge. ``fixed_band`` replaces the per-voltage lookup."""
    worst = None
    for r in rows.itertuples(index=False):
        band = fixed_band or band_for(profile, bus_kv[r.bus])
        over, under = r.vm_pu - band["v_max"], band["v_min"] - r.vm_pu
        if over >= under:
            key, limit = over, band["v_max"]
        else:
            key, limit = under, band["v_min"]
        if worst is None or key > worst[0]:
            worst = (key, float(r.vm_pu), limit, (int(r.period), int(r.hour)), band, r.bus, r.case)
    if worst is None:
        return None
    key, value, limit, where, band, bus, case = worst
    return ("fail" if key > 1e-9 else "pass"), value, limit, where, band, bus, case


def _transformer_row(trafo, sizing, held=frozenset()):
    """The ``transformer_loading`` row. ``held`` are a normally open tie's
    transformers (``campus_flow.held_transformers``): unsized, so no measure
    clears their overload, and the tie-closed clause is the row's only when
    the worst unit is one of them."""
    j = trafo["loading_pct"].idxmax()
    peak = float(trafo.at[j, "loading_pct"])
    as_is = "fail" if peak > 100.0 + 1e-9 else "pass"
    recs = [f"{r.group}: {r.units} x {r.recommended_unit_mva:g} MVA recommended (now {r.unit_rating_mva:g} MVA)"
            for r in sizing.itertuples(index=False) if not bool(r.adequate)]
    tie = trafo[trafo["trafo"].astype(str).isin(held)]
    tie_over = bool(len(tie)) and float(tie["loading_pct"].astype(float).max()) > 100.0 + 1e-9
    after = "pass" if all(pd.notna(r.recommended_unit_mva) for r in sizing.itertuples(index=False)) \
        and not tie_over else "fail"
    detail = "; ".join(recs) if recs else f"worst {trafo.at[j, 'trafo']} ({trafo.at[j, 'case']})"
    clause = "equipment rating (not a grid-code clause)"
    if len(tie):
        detail = f"{detail}; {OPEN_TIE_TRAFO}" + ("; the tie is overloaded, and no measure resizes it"
                                                  if tie_over else "")
        if str(trafo.at[j, "trafo"]) in held:
            clause += OPEN_TIE_CLAUSE
    return _row("transformer_loading", as_is, after, peak, 100.0, "%",
                (int(trafo.at[j, "period"]), int(trafo.at[j, "hour"])), clause, "assumed", detail,
                str(trafo.at[j, "case"]))


def _unknown(check, unit, clause, source, detail):
    return _row(check, "unknown", "unknown", math.nan, math.nan, unit, None, clause, source, detail)


def _pcc_hours(reactive, pcc, p_ref):
    """``(period, hour, p_mw, q0_mvar, q_final_mvar)`` of every converged
    hour, P from ``pcc``'s intact case; None when P cannot be read."""
    if pcc is None or p_ref is None or not len(pcc):
        return None
    intact = pcc[pcc["case"].astype(str) == "intact"]
    p = {(int(r.period), int(r.hour)): float(r.p_mw) for r in intact.itertuples(index=False)}
    out = []
    for r in reactive[reactive["converged"].astype(bool)].itertuples(index=False):
        key = (int(r.period), int(r.hour))
        if key in p and math.isfinite(p[key]):
            out.append((*key, p[key], float(r.q0_mvar), float(r.q_final_mvar)))
    return out or None


def _why_unknown(lim, hours, p_ref):
    if lim is None:
        return NO_LIMIT
    if p_ref is None:
        return "connection capacity (p_ref) unknown: the per-unit limit cannot be scaled"
    if hours is None:
        return "no PCC active power for the studied hours"
    return None


def _q_band_status(lim, hours, p_ref, col):
    """``(status, worst, out_of_range)`` of the Q band for Q column ``col``
    (3: as-is, 4: with measures). ``worst`` is ``(key, period, hour, p, q,
    lo, hi, limit)`` in MW/Mvar; ``out_of_range`` the first hour whose P lies
    outside the band's P range."""
    worst, outside = None, None
    for h in hours:
        period, hour, p, q = h[0], h[1], h[2], h[col]
        band = q_band_at(lim, p / p_ref)
        if band is None:
            outside = outside or (period, hour, p)
            continue
        lo, hi = band[0] * p_ref, band[1] * p_ref
        over, under = q - hi, lo - q
        key, limit = (over, hi) if over >= under else (under, lo)
        if worst is None or key > worst[0]:
            worst = (key, period, hour, p, q, lo, hi, limit)
    fail = outside is not None or (worst is not None and worst[0] > Q_TOL)
    return ("fail" if fail else "pass"), worst, outside


def _q_band_row(profile, hours, p_ref):
    lim = profile.get("q_capability_pq")
    why = _why_unknown(lim, hours, p_ref)
    if why:
        return _unknown("pcc_q_band", "Mvar", lim["clause"] if lim else "", lim["source"] if lim else "assumed", why)
    as_is, worst, outside = _q_band_status(lim, hours, p_ref, 3)
    after = _q_band_status(lim, hours, p_ref, 4)[0]
    if outside is not None:
        period, hour, p = outside
        row = _row("pcc_q_band", as_is, after, p, math.nan, "MW", (period, hour), lim["clause"], lim["source"],
                   f"P {p:.2f} MW ({p / p_ref:.3f} pu) at ({period}, {hour}) is outside the band's P range "
                   f"{lim['points'][0][0]:g} to {lim['points'][-1][0]:g} pu")
        return row
    key, period, hour, p, q, lo, hi, limit = worst
    detail = f"worst at P {p:.2f} MW ({p / p_ref:.3f} pu); band {lo:.2f} to {hi:.2f} Mvar there"
    if as_is == "pass" and key > 0:
        detail += f"; passes within the solver tolerance of {Q_TOL:g} Mvar"
    row = _row("pcc_q_band", as_is, after, q, limit, "Mvar", (period, hour), lim["clause"], lim["source"], detail)
    row["limit_min"], row["limit_max"] = lo, hi
    return row


def _pf_status(profile, hours, col):
    """``(status, worst)`` of the power factor for Q column ``col``; ``worst``
    is ``(margin, period, hour, p, q, pf, limit, side)``, None when no hour
    has a limit on its side."""
    worst = None
    for h in hours:
        period, hour, p, q = h[0], h[1], h[2], h[col]
        side = "pf_min_lagging" if q > 0 else "pf_min_leading" if q < 0 else None
        pf = abs(p) / math.hypot(p, q)
        if side is None or side not in profile:
            continue
        limit = float(profile[side]["value"])
        if worst is None or pf - limit < worst[0]:
            worst = (pf - limit, period, hour, p, q, pf, limit, side)
    if worst is None:
        return "pass", None
    return ("fail" if worst[0] < -1e-9 else "pass"), worst


def _pf_row(profile, hours, p_ref):
    sides = [k for k in ("pf_min_lagging", "pf_min_leading") if k in profile]
    lim = profile[sides[0]] if sides else None
    why = _why_unknown(lim, hours, p_ref)
    if why:
        return _unknown("pcc_power_factor", "", lim["clause"] if lim else "", lim["source"] if lim else "assumed",
                        why)
    floor = PF_MIN_P_SHARE * p_ref
    kept = [h for h in hours if abs(h[2]) >= floor]
    note = (f"{len(hours) - len(kept)} hour(s) with |P| below {PF_MIN_P_SHARE:.0%} of p_ref ({floor:.2f} MW) "
            "left out") if len(kept) < len(hours) else ""
    if not kept:
        return _unknown("pcc_power_factor", "", lim["clause"], lim["source"], note)
    as_is, worst = _pf_status(profile, kept, 3)
    after = _pf_status(profile, kept, 4)[0]
    unset = [k.removeprefix("pf_min_") for k in ("pf_min_lagging", "pf_min_leading") if k not in profile]
    extra = [x for x in (note, f"no {unset[0]} limit set" if unset else "") if x]
    if worst is None:
        return _row("pcc_power_factor", as_is, after, math.nan, math.nan, "", None, lim["clause"], lim["source"],
                    "; ".join(["no hour on a side with a limit", *extra]))
    margin, period, hour, p, q, pf, limit, side = worst
    own = profile[side]
    detail = "; ".join([f"{side.removeprefix('pf_min_')} at P {p:.2f} MW, Q {q:.2f} Mvar", *extra])
    return _row("pcc_power_factor", as_is, after, pf, limit, "", (period, hour), own["clause"], own["source"], detail)


def campus_compliance(bus, trafo, reactive, sizing, compensation, short_circuit, requirement, profile,
                      pcc_bus, bus_kv, held=frozenset(), pcc=None) -> pd.DataFrame:
    rows = []
    comp = compensation.set_index("direction")
    measures = bool((compensation["recommended_mvar"] > 0).any() or (~sizing["adequate"].astype(bool)).any())

    # PCC reactive band
    lim = float(requirement["q_limit_mvar"])
    ok = reactive[reactive["converged"].astype(bool)]
    i = ok["q0_mvar"].abs().idxmax()
    as_is = "pass" if ok["compliant_without"].astype(bool).all() else "fail"
    after = "pass" if (ok["q_final_mvar"].abs() <= lim + Q_TOL).all() else "fail"
    cap, ind = float(comp.at["capacitive", "recommended_mvar"]), float(comp.at["inductive", "recommended_mvar"])
    detail = (f"inverters supply up to {ok['q_inverters_mvar'].abs().max():.1f} Mvar; compensation recommended: "
              f"{cap:g} Mvar capacitive, {ind:g} Mvar inductive")
    # a pass within Q_TOL above the band is said, so a value over the limit never reads as a silent pass
    passed = [*(ok["q_final_mvar"].abs() if after == "pass" else []), *(ok["q0_mvar"].abs() if as_is == "pass" else [])]
    q_edge = max(passed, default=0.0)
    if q_edge > lim:
        detail += (f"; passes within the solver tolerance of {Q_TOL:g} Mvar: {q_edge:.3f} Mvar against the "
                   f"{lim:.3f} Mvar band")
    rows.append(_row("pcc_reactive", as_is, after, float(ok.at[i, "q0_mvar"]), lim, "Mvar",
                     (int(ok.at[i, "period"]), int(ok.at[i, "hour"])), requirement["clause"],
                     requirement["source"], detail))

    # voltages
    for check, sel, design in (("pcc_voltage", bus["bus"] == pcc_bus, False),
                               ("campus_voltage", bus["bus"] != pcc_bus, True)):
        own = profile.get("campus_voltage") if design else None
        v = _voltage(bus[sel], profile, bus_kv, own)
        if v is None:
            rows.append(_row(check, "pass", "pass", math.nan, math.nan, "pu", None, "", "assumed",
                             "no bus of this kind"))
            continue
        status, value, limit, where, band, b, case = v
        after = "not_rechecked" if measures else status
        # The code band applied inside the campus is a design choice, so
        # assumed; but a band nobody has confirmed stays extracted, so an
        # unconfirmed number is never laundered into an engineering choice.
        source = band["source"] if own or not design or band["source"] == "extracted" else "assumed"
        what = ("code band at the connection point" if not design else
                "the profile's campus design band" if own else
                "design limit inside the campus, the code band applied as one")
        detail = (f"worst at {b} ({case}); {what}"
                  f"; band {band['v_min']:g}-{band['v_max']:g} pu")
        rows.append(_row(check, status, after, value, limit, "pu", where, band["clause"], source, detail, str(case),
                         band))

    # transformers: none on a campus that runs at the grid's voltage
    tie_open = bool(requirement.get("tie_open"))
    if tie_open:
        for r in rows:
            if r["check"] == "pcc_voltage" and not r["clause"].endswith(OPEN_TIE_CLAUSE):
                r["clause"] += OPEN_TIE_CLAUSE
    if len(trafo):
        rows.append(_transformer_row(trafo, sizing, held))
    else:
        kvs = [bus_kv[pcc_bus]] + sorted({v for b, v in bus_kv.items() if b != pcc_bus}, reverse=True)
        note = NO_TRANSFORMER.format(kv="/".join(f"{v:g}" for v in kvs) if len(kvs) > 1 else f"{kvs[0]:g}")
        at = next((r for r in rows if r["check"] == "campus_voltage"), None) or \
            next(r for r in rows if r["check"] == "pcc_voltage")
        at["detail"] = f"{at['detail']}; {note}"

    # switchgear
    rated = short_circuit[short_circuit["rated_ka"].notna()]
    unrated = short_circuit[short_circuit["rated_ka"].isna()]
    if len(rated) and not all(_true(x) for x in rated["adequate"]):
        status = "fail"
    elif len(unrated):
        status = "not_rated"
    else:
        status = "pass"
    k = short_circuit["ikss_max_ka"].idxmax()
    failing = [f"{r.bus}: {r.detail}" for r in rated.itertuples(index=False) if not _true(r.adequate)]
    detail = "; ".join(failing) or (
        f"no rating for {sorted(set(unrated['bus']))}" if len(unrated) else "every rated bus within its rating")
    rows.append(_row("switchgear", status, status, float(short_circuit.at[k, "ikss_max_ka"]),
                     float(short_circuit.at[k, "rated_ka"]), "kA", (int(short_circuit.at[k, "period"]), None),
                     "equipment rating (IEC 62271-1 peak ratio, assumed)", "assumed", detail))

    # P-dependent Q band and power factor at the PCC
    p_ref = requirement.get("p_ref_mw")
    hours = _pcc_hours(reactive, pcc, p_ref)
    rows.append(_q_band_row(profile, hours, p_ref))
    rows.append(_pf_row(profile, hours, p_ref))
    if tie_open:
        for r in rows[-2:]:
            if r["clause"]:
                r["clause"] += OPEN_TIE_CLAUSE
    return pd.DataFrame(rows, columns=COLUMNS)
