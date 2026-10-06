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
    limit, not a code requirement, so that fallback is tagged ``assumed``.
``transformer_loading``
    The highest loading of any transformer, intact or N-1, against 100 % of
    its rating (equipment).
``switchgear``
    Ik'' and peak against each rated bus (C4c). Buses without a rating make
    the check ``not_rated``.

Every row carries:
- ``status_as_is``, for the campus as described;
- ``status_with_measures``, with the recommended transformer ratings and
  compensation. A voltage check becomes ``not_rechecked`` whenever any
  measure is recommended, because those measures move voltages and the
  study did not re-solve with them in place.
- ``value``, ``limit``, ``unit``, the worst ``(period, hour)``, ``clause``,
  ``source`` and ``detail``.

pandas only, plus the profile's band lookup.
"""
import math

import pandas as pd

from gridspine.templates.grid_codes import band_for

CHECKS = ("pcc_reactive", "pcc_voltage", "campus_voltage", "transformer_loading", "switchgear")
#: Mvar tolerance for "on the band edge" after correction (the solver's tol).
Q_TOL = 0.011


def _true(flag) -> bool:
    """A judgement read back from a table: True, numpy True or "True"."""
    return str(flag).strip().lower() == "true"


def _row(check, as_is, with_measures, value, limit, unit, where, clause, source, detail):
    period, hour = where if where else (None, None)
    return {"check": check, "status_as_is": as_is, "status_with_measures": with_measures,
            "value": value, "limit": limit, "unit": unit, "worst_period": period, "worst_hour": hour,
            "clause": clause, "source": source, "detail": detail}


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


def campus_compliance(bus, trafo, reactive, sizing, compensation, short_circuit, requirement, profile,
                      pcc_bus, bus_kv) -> pd.DataFrame:
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
        source = band["source"] if own or not design else "assumed"
        what = ("code band at the connection point" if not design else
                "the profile's campus design band" if own else
                "design limit inside the campus, the code band applied as one")
        detail = (f"worst at {b} ({case}); {what}"
                  f"; band {band['v_min']:g}-{band['v_max']:g} pu")
        rows.append(_row(check, status, after, value, limit, "pu", where, band["clause"], source, detail))

    # transformers
    j = trafo["loading_pct"].idxmax()
    peak = float(trafo.at[j, "loading_pct"])
    as_is = "fail" if peak > 100.0 + 1e-9 else "pass"
    recs = [f"{r.group}: {r.units} x {r.recommended_unit_mva:g} MVA recommended (now {r.unit_rating_mva:g} MVA)"
            for r in sizing.itertuples(index=False) if not bool(r.adequate)]
    after = "pass" if all(pd.notna(r.recommended_unit_mva) for r in sizing.itertuples(index=False)) else "fail"
    rows.append(_row("transformer_loading", as_is, after, peak, 100.0, "%",
                     (int(trafo.at[j, "period"]), int(trafo.at[j, "hour"])),
                     "equipment rating (not a grid-code clause)", "assumed",
                     "; ".join(recs) if recs else f"worst {trafo.at[j, 'trafo']} ({trafo.at[j, 'case']})"))

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
    return pd.DataFrame(rows, columns=["check", "status_as_is", "status_with_measures", "value", "limit", "unit",
                                       "worst_period", "worst_hour", "clause", "source", "detail"])
