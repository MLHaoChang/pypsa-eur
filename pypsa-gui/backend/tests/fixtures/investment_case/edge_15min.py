"""
15-minute edge fixture for the Edge Investment Case (plan P1 WP1.0).

Three buses — `grid`, `poc`, `site` — an import Link `grid→poc` tagged
`eh_role="grid_import"` (the point of connection), a PoC→site transfer, daytime
PV, a BESS, and a site load whose EVENING peak sits outside the PV window so a
demand charge always binds. Seven days × 96 quarter-hours = 672 snapshots,
weighted 0.25 h each (weights are in HOURS: that is what makes Σ p × w an
energy in MWh at any resolution).

`solved_edge_15min()` solves once per process (module cache, like
`tests/golden/fixture.py::_SOLVED`); callers get a COPY so a test cannot leak
mutations into the next.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa

STEPS_PER_DAY = 96
DAYS = 7
HOURS_PER_STEP = 0.25
START = "2030-01-07 00:00"   # a Monday; winter so the evening peak is dark

_SOLVED: pypsa.Network | None = None


def _profiles(idx: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    hour = np.asarray(idx.hour + idx.minute / 60.0, dtype=float)
    # PV: a clipped sine between 08:00 and 16:00.
    pv = np.clip(np.sin((hour - 8.0) / 8.0 * np.pi), 0.0, None)
    pv[(hour < 8.0) | (hour > 16.0)] = 0.0
    # Site load (MW): 20 base, +25 evening peak 17:00–21:00, +5 daytime.
    load = 20.0 + 5.0 * ((hour >= 8) & (hour < 17)) + 25.0 * ((hour >= 17) & (hour < 21))
    return pv, load.astype(float)


def build_edge_15min() -> pypsa.Network:
    n = pypsa.Network()
    idx = pd.date_range(START, periods=STEPS_PER_DAY * DAYS, freq="15min")
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = HOURS_PER_STEP
    for b in ("grid", "poc", "site"):
        n.add("Bus", b, carrier="AC")
    n.add("Carrier", "AC")
    n.add("Carrier", "grid")
    n.add("Carrier", "solar")
    n.add("Carrier", "battery")
    n.add("Generator", "grid_supply", bus="grid", carrier="grid",
          p_nom=200.0, marginal_cost=60.0)
    n.add("Link", "import", bus0="grid", bus1="poc", p_nom=80.0,
          eh_role="grid_import", carrier="AC")
    n.add("Link", "poc_site", bus0="poc", bus1="site", p_nom=200.0,
          p_min_pu=-1.0, carrier="AC")
    pv, load = _profiles(idx)
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=30.0,
          p_max_pu=pv, marginal_cost=0.0)
    n.add("StorageUnit", "bess", bus="site", carrier="battery", p_nom=10.0,
          max_hours=4.0, efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True, marginal_cost=0.5)
    n.add("Load", "site_load", bus="site", p_set=load)
    return n


def solved_edge_15min() -> pypsa.Network:
    global _SOLVED
    if _SOLVED is None:
        n = build_edge_15min()
        n.optimize(solver_name="highs")
        n.model.solver_model = None  # allow copy()
        _SOLVED = n
    return _SOLVED.copy()
