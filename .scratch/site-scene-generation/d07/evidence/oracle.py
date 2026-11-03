# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""D07 known-hour oracle (F03/F04 domain fixture). Run: python oracle.py"""
import sys, logging, pandas as pd, pypsa
logging.disable(logging.WARNING)
def build(freq="h", w=1.0):
    n = pypsa.Network(); n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq=freq))
    n.snapshot_weightings.loc[:, :] = w
    for c in ["AC", "heat", "H2", "NH3", "N2"]: n.add("Carrier", c)
    n.add("Bus", "elec", carrier="AC"); n.add("Bus", "heat", carrier="heat"); n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh"); n.add("Bus", "n2", carrier="N2", unit="t")
    n.buses["mwh_per_t"] = float("nan"); n.buses.loc["nh3", "mwh_per_t"] = 5.2
    n.add("Generator", "grid", bus="elec", p_nom=100, marginal_cost=10)
    n.add("Generator", "boiler", bus="heat", p_nom=100, marginal_cost=50)
    n.add("Generator", "n2_supply", bus="n2", p_nom=100, marginal_cost=1)
    n.add("Load", "office", bus="elec", p_set=[2, 3, 4, 3])
    n.add("Load", "heat_demand", bus="heat", p_set=[5, 5, 5, 5])
    n.add("Load", "nh3_offtake", bus="nh3", p_set=[8, 8, 0, 4])
    n.add("Link", "electrolyser", bus0="elec", bus1="h2", efficiency=0.5, bus2="heat", efficiency2=0.1, p_nom=100)
    n.add("Link", "haber", bus0="h2", bus1="nh3", efficiency=0.8, bus2="elec", efficiency2=-0.05,
          bus3="n2", efficiency3=-0.2, p_nom=100)
    n.links["eh_process"] = True
    return n
for label, freq, w in [("hourly", "h", 1.0), ("quarter", "15min", 0.25), ("representative", "h", 10.0)]:
    n = build(freq, w); st, cond = n.optimize(solver_name="highs", solver_options={"output_flag": False})
    t = n.snapshots[3]
    print(label, st, cond, "t3=", t)
    L = n.links_t
    for k in ["p0", "p1", "p2", "p3"]: print(" ", k, L[k].loc[t].round(9).to_dict())
    print("  gen", n.generators_t.p.loc[t].round(9).to_dict(), "loads", n.loads_t.p.loc[t].round(9).to_dict())
    print("  t2 haber p1", L.p1.loc[n.snapshots[2], "haber"])
    wg = n.snapshot_weightings.generators
    print("  electrolyser p0 weighted MWh", float((L.p0["electrolyser"] * wg).sum()))
