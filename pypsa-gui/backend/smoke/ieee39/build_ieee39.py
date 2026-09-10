"""
The IEEE 39-bus (New England) test system as a PyPSA network for the GUI's
solution-FMEA journey — topology, impedances, ratings, loads and generator
sizes from the standard case (MATPOWER `case39`, 100 MVA, 345 kV); everything
the standard data is SILENT on is generic and stated here:

* carriers per unit (the case has none): 30 hydro; 31, 33 nuclear; 32, 34, 38,
  39 coal; 35, 36, 37 gas CCGT — a plausible New England-shaped mix;
* marginal costs (€/MWh): hydro 5, nuclear 10, coal 30, gas 55;
* outage data: the per-carrier defaults library for most units, with an
  explicit EFORd/MTTR typed on four so both `source` kinds appear
  (nuclear 0.03 / 72 h; coal 0.06 / 48 h; gas 0.05 / 24 h; hydro 0.02 / 24 h);
* a 168 h (one-week) hourly horizon with a double-peak daily load shape
  scaled so each bus's PEAK equals the case's PD — total peak 6 254 MW against
  7 387 MW of installed capacity;
* one 500 MW wind farm at bus 16 on an hourly profile (generic diurnal +
  weather shape, mean CF ≈ 0.35), one 200 MW / 4 h battery at bus 20, and one
  EXTENDABLE gas peaker at bus 16 (p_nom 0, p_nom_max 2 000 MW, capital cost
  60 000 €/MW·a) so the planning loops have a lever;
* line reactances converted from p.u. to ohms at 345 kV (x × 345² / 100),
  `s_nom` = rateA; the case's transformers are modelled as lines with their
  reactance (a DC LOPF reads x only).
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pypsa

BASE_MVA = 100.0
V_KV = 345.0
Z_BASE = V_KV ** 2 / BASE_MVA          # Ω per p.u.

# fbus, tbus, r, x, b, rateA   — MATPOWER case39 branch table
BRANCHES = [
    (1, 2, 0.0035, 0.0411, 0.6987, 600), (1, 39, 0.0010, 0.0250, 0.75, 1000),
    (2, 3, 0.0013, 0.0151, 0.2572, 500), (2, 25, 0.0070, 0.0086, 0.146, 500),
    (2, 30, 0.0, 0.0181, 0.0, 900), (3, 4, 0.0013, 0.0213, 0.2214, 500),
    (3, 18, 0.0011, 0.0133, 0.2138, 500), (4, 5, 0.0008, 0.0128, 0.1342, 600),
    (4, 14, 0.0008, 0.0129, 0.1382, 500), (5, 6, 0.0002, 0.0026, 0.0434, 1200),
    (5, 8, 0.0008, 0.0112, 0.1476, 900), (6, 7, 0.0006, 0.0092, 0.113, 900),
    (6, 11, 0.0007, 0.0082, 0.1389, 480), (6, 31, 0.0, 0.0250, 0.0, 1800),
    (7, 8, 0.0004, 0.0046, 0.078, 900), (8, 9, 0.0023, 0.0363, 0.3804, 900),
    (9, 39, 0.0010, 0.0250, 1.2, 900), (10, 11, 0.0004, 0.0043, 0.0729, 600),
    (10, 13, 0.0004, 0.0043, 0.0729, 600), (10, 32, 0.0, 0.0200, 0.0, 900),
    (12, 11, 0.0016, 0.0435, 0.0, 500), (12, 13, 0.0016, 0.0435, 0.0, 500),
    (13, 14, 0.0009, 0.0101, 0.1723, 600), (14, 15, 0.0018, 0.0217, 0.366, 600),
    (15, 16, 0.0009, 0.0094, 0.171, 600), (16, 17, 0.0007, 0.0089, 0.1342, 600),
    (16, 19, 0.0016, 0.0195, 0.304, 600), (16, 21, 0.0008, 0.0135, 0.2548, 600),
    (16, 24, 0.0003, 0.0059, 0.068, 600), (17, 18, 0.0007, 0.0082, 0.1319, 600),
    (17, 27, 0.0013, 0.0173, 0.3216, 600), (19, 20, 0.0007, 0.0138, 0.0, 900),
    (19, 33, 0.0007, 0.0142, 0.0, 900), (20, 34, 0.0009, 0.0180, 0.0, 900),
    (21, 22, 0.0008, 0.0140, 0.2565, 900), (22, 23, 0.0006, 0.0096, 0.1846, 600),
    (22, 35, 0.0, 0.0143, 0.0, 900), (23, 24, 0.0022, 0.0350, 0.361, 600),
    (23, 36, 0.0005, 0.0272, 0.0, 900), (25, 26, 0.0032, 0.0323, 0.531, 600),
    (25, 37, 0.0006, 0.0232, 0.0, 900), (26, 27, 0.0014, 0.0147, 0.2396, 600),
    (26, 28, 0.0043, 0.0474, 0.7802, 600), (26, 29, 0.0057, 0.0625, 1.029, 600),
    (28, 29, 0.0014, 0.0151, 0.249, 600), (29, 38, 0.0008, 0.0156, 0.0, 1200),
]

# bus: PD (MW) — case39
LOADS = {3: 322.0, 4: 500.0, 7: 233.8, 8: 522.0, 9: 6.5, 12: 8.53, 15: 320.0,
         16: 329.0, 18: 158.0, 20: 680.0, 21: 274.0, 23: 247.5, 24: 308.6,
         25: 224.0, 26: 139.0, 27: 281.0, 28: 206.0, 29: 283.5, 31: 9.2,
         39: 1104.0}

# bus: (Pmax MW, carrier, marginal cost, explicit outage data or None)
GENS = {
    30: (1040.0, "hydro", 5.0, dict(outage_rate_value=0.02, outage_rate_basis="EFORd", mttr_hours=24.0)),
    31: (646.0, "nuclear", 10.0, dict(outage_rate_value=0.03, outage_rate_basis="EFORd", mttr_hours=72.0)),
    32: (725.0, "coal", 30.0, None),
    33: (652.0, "nuclear", 10.0, None),
    34: (508.0, "coal", 30.0, dict(outage_rate_value=0.06, outage_rate_basis="EFORd", mttr_hours=48.0)),
    35: (687.0, "gas", 55.0, None),
    36: (580.0, "gas", 55.0, dict(outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=24.0)),
    37: (564.0, "gas", 55.0, None),
    38: (865.0, "coal", 30.0, None),
    39: (1100.0, "coal", 30.0, None),
}

HOURS = 168


def load_shape(h: np.ndarray) -> np.ndarray:
    """Double-peak daily shape (morning + evening), a weekend dip, peak 1.0."""
    hod = h % 24
    day = h // 24
    daily = (0.62 + 0.18 * np.exp(-((hod - 8) ** 2) / 6.0)
             + 0.30 * np.exp(-((hod - 19) ** 2) / 8.0))
    weekend = np.where(day >= 5, 0.85, 1.0)
    s = daily * weekend
    return s / s.max()


def wind_shape(h: np.ndarray, seed: int = 39) -> np.ndarray:
    rng = np.random.default_rng(seed)
    hod = h % 24
    diurnal = 0.35 + 0.15 * np.cos((hod - 3) / 24 * 2 * np.pi)
    weather = np.convolve(rng.normal(0, 0.25, len(h) + 24), np.ones(12) / 12, mode="same")[:len(h)]
    cf = np.clip(diurnal + weather, 0.02, 0.95)
    return cf


def build(load_scale: float = 1.0) -> pypsa.Network:
    """``load_scale`` > 1 is the STRESSED variant: every load scaled so the
    week peaks above the installed fleet's firm capacity — the planning
    loops then have to build the peaker instead of verifying a plan that
    already meets the target on its first probe."""
    n = pypsa.Network()
    n.name = "IEEE 39-bus (New England) — FMEA e2e"
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    for c, co2 in (("hydro", 0.0), ("nuclear", 0.0), ("coal", 0.34), ("gas", 0.2),
                   ("wind", 0.0), ("battery", 0.0), ("AC", 0.0)):
        n.add("Carrier", c, co2_emissions=co2)
    for b in range(1, 40):
        n.add("Bus", f"B{b}", v_nom=V_KV, carrier="AC",
              x=float(np.cos(b / 39 * 2 * np.pi)), y=float(np.sin(b / 39 * 2 * np.pi)))
    for i, (f, t, r, x, b, rate) in enumerate(BRANCHES):
        n.add("Line", f"L{f}-{t}", bus0=f"B{f}", bus1=f"B{t}",
              r=r * Z_BASE, x=x * Z_BASE, b=b / Z_BASE, s_nom=float(rate),
              carrier="AC", length=50.0)
    h = np.arange(HOURS)
    shape = load_shape(h)
    for b, pd_mw in LOADS.items():
        n.add("Load", f"D{b}", bus=f"B{b}", carrier="AC", p_set=pd_mw)
        n.loads_t.p_set[f"D{b}"] = pd_mw * shape * load_scale
    for b, (pmax, carrier, mc, occ) in GENS.items():
        kw = dict(bus=f"B{b}", carrier=carrier, p_nom=pmax, marginal_cost=mc,
                  p_min_pu=0.0)
        if occ:
            kw.update(occ)
        n.add("Generator", f"G{b}", **kw)
    n.add("Generator", "W16", bus="B16", carrier="wind", p_nom=500.0,
          marginal_cost=0.0)
    n.generators_t.p_max_pu["W16"] = wind_shape(h)
    n.add("Generator", "PK16", bus="B16", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=2000.0, capital_cost=60000.0,
          marginal_cost=90.0)
    n.add("StorageUnit", "S20", bus="B20", carrier="battery", p_nom=200.0,
          max_hours=4.0, efficiency_store=0.95, efficiency_dispatch=0.95,
          marginal_cost=1.0, cyclic_state_of_charge=True)
    return n


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "ieee39.nc"
    n = build(float(sys.argv[2]) if len(sys.argv) > 2 else 1.0)
    n.export_to_netcdf(out)
    print(f"wrote {out}: {len(n.buses)} buses, {len(n.lines)} lines, "
          f"{len(n.generators)} generators, {len(n.loads)} loads, "
          f"{len(n.storage_units)} storage, {len(n.snapshots)} snapshots; "
          f"peak load {n.loads_t.p_set.sum(axis=1).max():.0f} MW, "
          f"thermal+hydro capacity {n.generators.loc[~n.generators.carrier.isin(['wind']), 'p_nom'].sum():.0f} MW")
