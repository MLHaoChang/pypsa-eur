"""WP1.5a-0 spike: new linopy variables inside extra_functionality."""
import logging, queue, sys, threading, io
sys.path.insert(0, "/home/user/pypsa-eur/pypsa-gui/backend")
import numpy as np, pandas as pd
from tests.fixtures.investment_case import edge_15min as F

RATE = 12_000.0  # €/MW-month demand charge

def build():
    F.START = "2030-01-28 00:00"   # spans Jan/Feb
    n = F.build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0
    return n

def months_of(n):
    return pd.Index(pd.DatetimeIndex(n.snapshots).strftime("%Y-%m"), name="month")

def add_peak(n, sns):
    m = n.model
    months = pd.Index(sorted(set(months_of(n))), name="month")
    peak = m.add_variables(lower=0, name="ic_peak_import", coords=[months])
    p = m["Link-p"]
    dims = p.dims
    name_dim = [d for d in dims if d != "snapshot"][0]
    p_imp = p.sel({name_dim: "import"})
    month_of_sns = xr_month = months_of(n)
    import xarray as xr
    sel = xr.DataArray(month_of_sns.to_numpy(), coords={"snapshot": n.snapshots}, dims="snapshot")
    peak_t = peak.sel(month=sel)
    m.add_constraints(p_imp - peak_t <= 0, name="ic_peak_import_def")
    m.objective += (RATE * peak).sum()
    return dims

log_stream = io.StringIO()
h = logging.StreamHandler(log_stream); h.setLevel(logging.DEBUG)
logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.INFO)

# A: direct optimize
n = build()
info = {}
def ef(n, sns): info["dims"] = add_peak(n, sns)
status = n.optimize(solver_name="highs", extra_functionality=ef)
peak = n.model.variables["ic_peak_import"].solution.to_pandas()
p0max = n.links_t.p0["import"].groupby(months_of(n).to_numpy()).max()
print("A status", status, "dims", info["dims"])
print("A peak solution", peak.to_dict(), "p0 max", p0max.to_dict())
energy_cost = float((n.snapshot_weightings.objective * n.generators_t.p["grid_supply"] * 50.0).sum()
                    + (n.snapshot_weightings.objective * n.storage_units_t.p_dispatch["bess"] * 0.5).sum())
print("A objective", n.objective, "manual energy+demand", energy_cost + RATE * peak.sum())
errs = [l for l in log_stream.getvalue().splitlines() if "ic_peak" in l]
print("A log lines mentioning ic_peak:", errs[:5])

# baseline without the term
b = build(); b.optimize(solver_name="highs")
print("A baseline objective (no demand charge)", b.objective, "baseline import peak", b.links_t.p0["import"].max())

# B: through run_simulation with objective scale, patching the commercial wrapper
import services.solver_service as SS
from services.pypsa_service import PyPSAService
def run(scale):
    n = build()
    def wrap(network, user_fn, cfg, log_queue=None):
        def fn(n_, sns):
            if user_fn is not None: user_fn(n_, sns)
            add_peak(n_, sns)
        return fn
    orig = SS._wrap_with_commercial_bindings
    SS._wrap_with_commercial_bindings = wrap
    try:
        PyPSAService.set_network(n)
        st = SS.run_simulation(SS.SolverConfig(user_objective_scale=scale), n, PyPSAService.get_lock(),
                               threading.Event(), queue.SimpleQueue(), state_update=lambda **kw: None)
    finally:
        SS._wrap_with_commercial_bindings = orig
    sol = n.model.variables["ic_peak_import"].solution.to_pandas() if n.model is not None else None
    return st, n.objective, sol
for sc in (1.0, 1e-3):
    st, obj, sol = run(sc)
    print(f"B scale={sc}", st, "objective", obj, "peak", None if sol is None else sol.to_dict())
