"""The owner-journey fixture `oj-grid` and its hand-calculated oracle.

Three 110 kV buses. L1 (B1-B2) and L2 (B2-B3) in series, T1 (B1-B3) in
parallel with them; Gas at B1, Solar and the 90 MW load at B3, Battery and
H2 tank at B3. 24 hourly snapshots.

Hand calculation (DC flow, kept here, never taken from an adapter):

- Series reactances: x_pu(L1) = x_pu(L2) = 12.1 ohm / (110 kV)^2 = 0.001;
  x_pu(T1) = 0.2 pu / 100 MVA = 0.002. The L1+L2 path (0.002) and T1 (0.002)
  are equal, so every MW sent from B1 to B3 splits half and half.
- Solar costs 0 and never exceeds the load (80 MW <= 90 MW), so all of it is
  used; Gas (50 EUR/MWh) covers the rest: gas_t = 90 - 80 * pu_t.
- Storage cannot lower the cost: Gas costs the same every hour and Battery
  (efficiency 0.9 each way) and H2 tank (1 % standing loss per hour) only lose
  energy, so both stay idle and the optimum is unique.
- Peak gas = 90 MW in the hours with no sun (pu = 0). Each path then carries
  45 MW: T1 45 / 100 MVA = 45 %, L1 and L2 45 / 300 MVA = 15 % each.
"""
import numpy as np
import pandas as pd
import pypsa

NAME = 'oj-grid'
HOURS = 24
LOAD_MW = 90.0
SOLAR_MW = 80.0
# Zero at night, a smooth peak of 1.0 at 12:00.
SOLAR_PU = [round(max(0.0, float(np.sin(np.pi * (h - 6) / 12))), 6) if 6 <= h <= 18 else 0.0
            for h in range(HOURS)]

ORACLE = {
    'ranking': ['T1', 'L1', 'L2'],  # T1 first; L1/L2 tie, broken by name
    'peak_flow_mw': {'T1': 45.0, 'L1': 45.0, 'L2': 45.0},
    'util_pct': {'T1': 45.0, 'L1': 15.0, 'L2': 15.0},
    'rating_mva': {'T1': 100.0, 'L1': 300.0, 'L2': 300.0},
    'over_limit_hours': 0.0,
}


def build_oj_grid() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2026-06-01', periods=HOURS, freq='h'))
    for bus in ('B1', 'B2', 'B3'):
        n.add('Bus', bus, v_nom=110)
    n.add('Line', 'L1', bus0='B1', bus1='B2', x=12.1, r=0.1, s_nom=300)
    n.add('Line', 'L2', bus0='B2', bus1='B3', x=12.1, r=0.1, s_nom=300)
    n.add('Transformer', 'T1', bus0='B1', bus1='B3', x=0.2, r=0.001, s_nom=100)
    n.add('Generator', 'Gas', bus='B1', p_nom=100, marginal_cost=50, carrier='gas')
    n.add('Generator', 'Solar', bus='B3', p_nom=SOLAR_MW, marginal_cost=0, carrier='solar',
          p_max_pu=pd.Series(SOLAR_PU, index=n.snapshots))
    n.add('Load', 'Demand', bus='B3', p_set=LOAD_MW)
    # The power part's lifetime, so pricing it (OJ-21) passes the priced-part check.
    n.add('StorageUnit', 'Battery', bus='B3', p_nom=20, max_hours=4,
          efficiency_store=0.9, efficiency_dispatch=0.9, inv_power_lifetime=15.0)
    n.add('Store', 'H2 tank', bus='B3', e_nom=500, standing_loss=0.01)
    return n
