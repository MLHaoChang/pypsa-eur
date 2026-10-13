"""
C1 — the self-authored contract settlement fixture (Edge Investment Case P2
plan, oracle table: "PV + BESS behind the PoC with the BESS charging from PV
AND the site exporting in some intervals; each contract type of WP2.2a/b;
15-min, 7 days; settlement lines; the fixture's arithmetic, to the cent").

A hand-set dispatch, not a solve, so every expected line is plain arithmetic
the test writes out independently of `services/commercial/contracts.py`:

  * two generators — `pv` (a clipped midday sine, 0 at night) and `wind`
    (flat 2 MW) — so pro-rata export attribution is non-trivial;
  * the BESS charges from PV 10:00–14:00 (not export);
  * the site exports 11:00–13:00 on days 2, 4 and 6 (0-based 1, 3, 5; 0.8 × PV there);
  * a reference price: 60 €/MWh by day, 90 in the evening peak, −15 at
    12:00–13:00 on days 3 and 5 (0-based 2, 4; negative prices), 40 at night;
  * WP2.2b: two loads on bus `site` (`site_load` 20 MW, `aux` 5 MW) and one on
    `other` (`far` 10 MW); DSR activation on `site` of 4 MW 18:00–19:30 on
    days 1 and 3 (0-based 0, 2) and 18:00–18:30 on day 5 (0-based 4) — three
    events; the BESS discharges 2 MW 18:00–20:00 every day; a Link `chp`
    delivers 1 MW at bus1 all week.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

START = "2030-03-04 00:00"   # a Monday in March (no DST in UTC)
STEPS = 96 * 7
W = 0.25                     # represented hours per row (a plain week)


def build():
    idx = pd.date_range(START, periods=STEPS, freq="15min")
    hour = np.asarray(idx.hour + idx.minute / 60.0)
    day = np.asarray((idx - idx[0]).days)
    pv = np.clip(np.sin((hour - 7.0) / 12.0 * np.pi), 0.0, None) * 8.0
    pv[(hour < 7) | (hour >= 19)] = 0.0
    wind = np.full(STEPS, 2.0)
    export = np.where(np.isin(day, [1, 3, 5]) & (hour >= 11) & (hour < 13), 0.8 * pv, 0.0)
    ref = np.where((hour >= 7) & (hour < 17), 60.0, 40.0)
    ref = np.where((hour >= 17) & (hour < 21), 90.0, ref)
    ref = np.where(np.isin(day, [2, 4]) & (hour >= 12) & (hour < 13), -15.0, ref)
    gens = pd.DataFrame({"pv": pv, "wind": wind}, index=idx)
    loads = pd.DataFrame({"site_load": 20.0, "aux": 5.0, "far": 10.0}, index=idx)
    dsr_on = (np.isin(day, [0, 2]) & (hour >= 18) & (hour < 19.5)) | \
        ((day == 4) & (hour >= 18) & (hour < 18.5))
    dsr = pd.DataFrame({"site": np.where(dsr_on, 4.0, 0.0)}, index=idx)
    bess = pd.DataFrame({"bess": np.where((hour >= 18) & (hour < 20), 2.0, 0.0)}, index=idx)
    # The BESS charges from PV 10:00–14:00 (up to 3 MW): on-site use of the
    # PV that an as_consumed_btm PPA counts as consumed.
    bess_charge = pd.DataFrame({"bess": np.where((hour >= 10) & (hour < 14),
                                                 np.minimum(pv, 3.0), 0.0)}, index=idx)
    chp = pd.DataFrame({"chp": 1.0}, index=idx)
    return {
        "loads": loads, "load_bus": {"site_load": "site", "aux": "site", "far": "other"},
        "dsr": dsr, "storage_discharge": bess, "storage_charge": bess_charge,
        "link_output": chp, "site_generators": ["pv", "wind"],
        "index": idx, "weights": np.full(STEPS, W), "generators": gens,
        "export_mw": pd.Series(export, index=idx), "ref": pd.Series(ref, index=idx),
        "hour": hour, "day": day,
    }
