"""
The P4 integration fixture: the edge site of `edge_15min` for a FULL YEAR at
hourly resolution (IC P4 plan, "The integration fixture"; review round 1 #9).

The finance engine replicates one operating year, so its template must be a
year (plan C3): the 7-day `edge_15min` represents 168 hours and is refused
`template_not_annual`. This network has 8,760 hourly snapshots (2030, not a
leap year), the same buses and components, a seasonal PV profile, the same
load shape, an export Link with a grid that absorbs export, and the owner's
assets costed the way PyPSA 1.x derives an annuity: `overnight_cost` with a
`discount_rate` and `lifetime` (review round 2 R7 — PyPSA needs both). Sizes
are fixed (not extendable): the finance case values a given design.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa

YEAR = 2030
DISCOUNT_RATE = 0.07      # the case's wacc_nominal matches it (the C10 gate reads consistent)
PV_OVERNIGHT_PER_MW = 700_000.0
BESS_OVERNIGHT_PER_MW = 1_000_000.0      # 4-hour storage, per MW of power
PV_LIFETIME = 30.0
BESS_LIFETIME = 15.0


def _profiles(idx: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    hour = np.asarray(idx.hour, dtype=float)
    doy = np.asarray(idx.dayofyear, dtype=float)
    # PV: a clipped sine 06:00–18:00 at midsummer, narrower in winter.
    season = 0.6 + 0.4 * np.cos(2 * np.pi * (doy - 172) / 365)
    half = 5.0 + 2.0 * np.cos(2 * np.pi * (doy - 172) / 365)
    pv = np.clip(np.cos((hour + 0.5 - 12.0) / half * (np.pi / 2)), 0.0, None) * season
    pv[np.abs(hour + 0.5 - 12.0) > half] = 0.0
    # Site load (MW): 20 base, +5 daytime, +25 evening peak — as edge_15min.
    load = 20.0 + 5.0 * ((hour >= 8) & (hour < 17)) + 25.0 * ((hour >= 17) & (hour < 21))
    return np.clip(pv, 0.0, 1.0), load.astype(float)


def build_edge_hourly_year() -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.date_range(f"{YEAR}-01-01", periods=8760, freq="h")
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 1.0
    for b in ("grid", "poc", "site"):
        n.add("Bus", b, carrier="AC")
    for c in ("AC", "grid", "solar", "battery"):
        n.add("Carrier", c)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=200.0,
          marginal_cost=60.0, p_min_pu=-1.0)          # the grid absorbs export
    n.add("Link", "import", bus0="grid", bus1="poc", p_nom=80.0, eh_role="grid_import",
          carrier="AC")
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC",
          marginal_cost=0.01)
    n.add("Link", "poc_site", bus0="poc", bus1="site", p_nom=200.0, p_min_pu=-1.0, carrier="AC")
    pv, load = _profiles(idx)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=40.0, p_max_pu=pv,
          marginal_cost=0.0, overnight_cost=PV_OVERNIGHT_PER_MW, discount_rate=DISCOUNT_RATE,
          lifetime=PV_LIFETIME)
    n.add("StorageUnit", "bess", bus="site", carrier="battery", p_nom=10.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.95, cyclic_state_of_charge=True,
          marginal_cost=0.5, overnight_cost=BESS_OVERNIGHT_PER_MW,
          discount_rate=DISCOUNT_RATE, lifetime=BESS_LIFETIME)
    n.add("Load", "site_load", bus="site", p_set=load)
    return n
