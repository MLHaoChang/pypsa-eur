import pypsa, pandas as pd, sys
n = pypsa.Network()
n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
n.add("Carrier", "AC"); n.add("Carrier", "gas", co2_emissions=0.0)
for b in ["Bus_A", "Bus_B", "Bus_C"]:
    n.add("Bus", b, v_nom=20.0, carrier="AC")
n.add("Line", "Line_AB", bus0="Bus_A", bus1="Bus_B", x=0.5, r=0.05, s_nom=5000, length=1)
n.add("Line", "Line_BC", bus0="Bus_B", bus1="Bus_C", x=20.0, r=1.0, s_nom=5000, length=10)
n.add("Generator", "Gen_A", bus="Bus_A", p_nom=10000, marginal_cost=10, carrier="gas", control="Slack")
n.add("Load", "Load_B", bus="Bus_B", p_set=pd.Series([2.0, 2.0, 2.0], n.snapshots))
n.add("Load", "Load_C", bus="Bus_C", p_set=pd.Series([1.0, 3.0, 200.0], n.snapshots))
if len(sys.argv) > 2:
    n.optimize(solver_name="highs")
    r = n.pf()
    print(r["converged"])
n.export_to_netcdf(sys.argv[1])
