"""Detailed-grid -> PyPSA nodal converter + UC dispatch producer.
Nodal = identity region map: every PyPSA element name equals the canonical
detailed-grid name. The only module allowed to import pypsa."""
import numpy as np
import pandas as pd
import pypsa

# Normalised daily load shape (24 h), peak = 1.0 at hour 19. Ledgered
# assumption: synthetic shape for the vertical slice; real studies supply
# measured series.
LOAD_SHAPE = [
    0.62, 0.58, 0.56, 0.45, 0.56, 0.60, 0.68, 0.78, 0.86, 0.90, 0.92, 0.93,
    0.92, 0.90, 0.89, 0.90, 0.93, 0.97, 0.99, 1.00, 0.94, 0.86, 0.76, 0.67,
]

EXT_GRID_P_NOM_MW = 3000.0
EXT_GRID_MARGINAL_COST = 80.0  # EUR/MWh — import priced above all thermal units


def to_pypsa(net, snapshots: int = 24) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(range(snapshots))
    bus_name = net.bus["name"]

    for _, b in net.bus.iterrows():
        n.add("Bus", b["name"], v_nom=b["vn_kv"])

    for i, ln in net.line.iterrows():
        vn = net.bus.at[ln["from_bus"], "vn_kv"]
        n.add(
            "Line", f"L_{i:02d}",
            bus0=bus_name.at[ln["from_bus"]], bus1=bus_name.at[ln["to_bus"]],
            r=ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"],
            x=ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"],
            s_nom=np.sqrt(3) * vn * ln["max_i_ka"] * ln["parallel"],
        )

    for i, tr in net.trafo.iterrows():
        n.add(
            "Transformer", f"T_{i:02d}",
            bus0=bus_name.at[tr["hv_bus"]], bus1=bus_name.at[tr["lv_bus"]],
            s_nom=tr["sn_mva"], x=tr["vk_percent"] / 100.0,
            r=tr["vkr_percent"] / 100.0, tap_ratio=1.0, model="t",
        )

    per_bus = net.load.groupby("bus")["p_mw"].sum()
    shape = pd.Series(LOAD_SHAPE[:snapshots], index=n.snapshots)
    for b, p in per_bus.items():
        n.add("Load", f"LD_{bus_name.at[b]}", bus=bus_name.at[b], p_set=shape * float(p))

    for i, (_, g) in enumerate(net.gen.iterrows()):
        p_nom = g.get("max_p_mw", np.nan)
        if not np.isfinite(p_nom) or p_nom <= 0:
            p_nom = 1.2 * g["p_mw"]
        n.add(
            "Generator", g["name"], bus=bus_name.at[g["bus"]],
            p_nom=float(p_nom), committable=True, p_min_pu=0.3,
            min_up_time=2, min_down_time=2, start_up_cost=1000.0,
            marginal_cost=10.0 + 4.0 * i,
        )

    for _, e in net.ext_grid.iterrows():
        n.add("Generator", e["name"], bus=bus_name.at[e["bus"]],
              p_nom=EXT_GRID_P_NOM_MW, committable=False,
              marginal_cost=EXT_GRID_MARGINAL_COST)
    return n
