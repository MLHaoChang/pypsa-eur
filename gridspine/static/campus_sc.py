"""Short-circuit levels on the campus, against its switchgear (plan C4c).

``campus_fault_levels(campus, installed)`` runs IEC 60909 at every bus. The
max case gives Ik'' and the peak ip; the min case gives Ik''. Every unit in
``installed`` (those that exist in the period being checked) is energised,
whatever its dispatch, because a converter or a running genset feeds the
fault whether or not it is producing. Every other unit is out.

A bus (or the PCC) with ``ik_rated_ka`` is judged by ``judge``:

* Ik'' max must not exceed the rated short-time current;
* ip must not exceed the rated peak withstand current, ``PEAK_FACTOR[f]``
  times the rated short-time current. The factor is 2.5 at 50 Hz and 2.6
  at 60 Hz, the standard ratio of IEC 62271-1. That ratio was not checked
  against the text, so it is ledgered as assumed.

A bus without a rating is reported, with ``adequate`` left as None, rather
than passed.

The calculation runs on a copy; ``campus.net`` is not changed. Allowed to
import pandapower (``static/``).
"""
import copy
import math

import pandapower.shortcircuit as sc
import pandas as pd

#: Rated peak withstand current over rated short-time current (IEC 62271-1).
PEAK_FACTOR = {50: 2.5, 60: 2.6}


def judge(ik_ka: float, ip_ka: float, rated_ka: float, f_hz: int):
    """``(adequate, detail)`` for one bus against its switchgear rating."""
    peak_rated = PEAK_FACTOR[int(f_hz)] * rated_ka
    problems = []
    if ik_ka > rated_ka:
        problems.append(f"Ik'' {ik_ka:.2f} kA above the rated {rated_ka:g} kA")
    if ip_ka > peak_rated:
        problems.append(f"peak {ip_ka:.2f} kA above the rated peak withstand {peak_rated:.2f} kA")
    if problems:
        return False, "; ".join(problems)
    return True, f"Ik'' {ik_ka:.2f} kA and peak {ip_ka:.2f} kA within {rated_ka:g} kA / {peak_rated:.2f} kA"


def campus_fault_levels(campus, installed) -> pd.DataFrame:
    """One row per bus: ``bus``, ``vn_kv``, ``ikss_max_ka``, ``ip_max_ka``,
    ``ikss_min_ka``, ``rated_ka``, ``rating_source``, ``adequate``,
    ``detail``."""
    net = copy.deepcopy(campus.net)
    installed = set(installed)
    net.sgen["in_service"] = net.sgen["name"].astype(str).isin(installed)
    sc.calc_sc(net, case="max", ip=True)
    ik_max = net.res_bus_sc["ikss_ka"].copy()
    ip_max = net.res_bus_sc["ip_ka"].copy()
    sc.calc_sc(net, case="min")
    ik_min = net.res_bus_sc["ikss_ka"].copy()
    p = campus.params
    ratings = p[p["param"] == "ik_rated_ka"].set_index("element")
    f_hz = int(round(float(net.f_hz)))
    rows = []
    for i in net.bus.index:
        name = str(net.bus.at[i, "name"])
        row = {"bus": name, "vn_kv": float(net.bus.at[i, "vn_kv"]),
               "ikss_max_ka": float(ik_max.at[i]), "ip_max_ka": float(ip_max.at[i]),
               "ikss_min_ka": float(ik_min.at[i]),
               "rated_ka": math.nan, "rating_source": None, "adequate": None, "detail": "no switchgear rating given"}
        if name in ratings.index:
            rated = float(ratings.at[name, "value"])
            adequate, detail = judge(row["ikss_max_ka"], row["ip_max_ka"], rated, f_hz)
            row.update(rated_ka=rated, rating_source=ratings.at[name, "source"], adequate=adequate, detail=detail)
        rows.append(row)
    return pd.DataFrame(rows)
