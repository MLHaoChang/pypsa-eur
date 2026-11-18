"""Build the small shot networks as .nc files (imported through the app's Import file)."""
import sys
import pandas as pd
import pypsa

out = sys.argv[1]

def base():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "solar")
    n.add("Bus", "B1", carrier="AC")
    n.add("Load", "L1", bus="B1", p_set=[10, 12, 14, 12, 10, 8])
    n.add("Generator", "Solar", bus="B1", carrier="solar", p_nom=8, marginal_cost=5)
    n.add("Generator", "Gas", bus="B1", carrier="gas", p_nom=10, marginal_cost=20)
    return n

n = base(); n.name = "bridge-small"; n.export_to_netcdf(f"{out}/bridge-small.nc")
n = base(); n.name = "bridge-slack"
# A slack generator (name prefix __voll_) under an ordinary carrier.
n.add("Generator", "__voll_B1", bus="B1", carrier="AC", p_nom=1000, marginal_cost=100)
n.loads_t.p_set["L1"] = [10, 12, 22, 12, 10, 8]
n.export_to_netcdf(f"{out}/bridge-slack.nc")
for f in ("bridge-small", "bridge-slack"):
    m = pypsa.Network(f"{out}/{f}.nc"); print(f, m.optimize(solver_name="highs"), m.objective)
