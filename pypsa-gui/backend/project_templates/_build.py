"""
Regenerate the bundled project-template networks.

Run from inside the pixi environment:

    pixi run python pypsa-gui/backend/project_templates/_build.py

Each template is written as a bare ``network.nc`` under
``project_templates/<id>/``. The GUI's ``POST /api/projects/from_template/<id>``
endpoint copies that file into a fresh project directory and loads it.

Templates are exported UNSOLVED on purpose — the user imports one, then runs
the LOPF themselves. Feasibility is verified at build time (a throwaway copy
is solved with HiGHS) so a shipped template can never be silently infeasible.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pypsa

HERE = pathlib.Path(__file__).parent

# 24-hour hourly horizon shared by both templates. A single representative day
# keeps the LOPF tiny (<1 s) while still exercising a time-varying load.
SNAPSHOTS = pd.date_range("2025-01-01", periods=24, freq="h")

# Normalised daily demand shape — двойной peak (morning + evening), trough at
# night. Multiplied onto each load's nominal MW below.
_HOURS = np.arange(24)
LOAD_SHAPE = (
    0.62
    + 0.18 * np.exp(-((_HOURS - 8) ** 2) / 6.0)    # morning peak ~08:00
    + 0.30 * np.exp(-((_HOURS - 19) ** 2) / 8.0)   # evening peak ~19:00
)


def _verify_feasible(n: pypsa.Network, label: str) -> None:
    """Solve a copy with HiGHS and assert the LOPF is feasible."""
    test = n.copy()
    status, condition = test.optimize(solver_name="highs")
    if status != "ok" or condition != "optimal":
        raise SystemExit(
            f"[{label}] template is NOT feasible: status={status} condition={condition}"
        )
    print(f"[{label}] feasible — objective {test.objective:,.0f}")


def _export(n: pypsa.Network, template_id: str) -> None:
    out_dir = HERE / template_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "network.nc"
    n.export_to_netcdf(str(out_path))
    print(f"[{template_id}] wrote {out_path}  "
          f"({len(n.buses)} buses, {len(n.lines)} lines, "
          f"{len(n.generators)} generators, {len(n.snapshots)} snapshots)")


# ── Template 1: 3-Bus Tutorial ───────────────────────────────────────────────
# Minimal AC network — a triangle of 3 buses, 1 generator, 2 loads. The
# smallest network that still has a meaningful power-flow / LOPF result.
def build_3bus() -> pypsa.Network:
    n = pypsa.Network()
    n.name = "3-Bus Tutorial"
    n.set_snapshots(SNAPSHOTS)

    n.add("Carrier", "AC")
    n.add("Carrier", "gas", co2_emissions=0.20)

    # Triangle layout. x/y are schematic here (no real geography) — the GUI's
    # blank canvas uses them as a sensible starting layout.
    n.add("Bus", "Bus 0", v_nom=220.0, x=0.0, y=0.0, carrier="AC")
    n.add("Bus", "Bus 1", v_nom=220.0, x=2.0, y=0.0, carrier="AC")
    n.add("Bus", "Bus 2", v_nom=220.0, x=1.0, y=1.7, carrier="AC")

    # Single dispatchable generator at Bus 0 — large enough to serve the whole
    # network so the LOPF is always feasible.
    n.add("Generator", "Gas 0", bus="Bus 0", carrier="gas",
          p_nom=300.0, marginal_cost=20.0)

    # Two time-varying loads. Bus 1 is the bigger sink.
    n.add("Load", "Load 1", bus="Bus 1", carrier="AC")
    n.add("Load", "Load 2", bus="Bus 2", carrier="AC")
    n.loads_t.p_set = pd.DataFrame(
        {
            "Load 1": 100.0 * LOAD_SHAPE,
            "Load 2": 80.0 * LOAD_SHAPE,
        },
        index=SNAPSHOTS,
    )

    # Triangle of identical lines. x > 0 is required for the LOPF's linearised
    # power flow; r and s_nom are sized so nothing binds.
    for ln, (b0, b1) in {
        "Line 0-1": ("Bus 0", "Bus 1"),
        "Line 1-2": ("Bus 1", "Bus 2"),
        "Line 0-2": ("Bus 0", "Bus 2"),
    }.items():
        n.add("Line", ln, bus0=b0, bus1=b1, x=0.1, r=0.01, s_nom=250.0)

    return n


# ── Template 2: IEEE 14-Bus ──────────────────────────────────────────────────
# Classic IEEE 14-bus test case. Bus/branch impedances are the standard
# MATPOWER `case14` values. NOTE: in the original case14, buses 3/6/8 are
# synchronous condensers (no real-power output). For a usable LOPF template
# they are modelled here as real (higher-cost) generators so the case is a
# meaningful economic-dispatch problem rather than a single-generator trivial
# solve. Buses 1 and 2 keep their real generating units.
def build_ieee14() -> pypsa.Network:
    n = pypsa.Network()
    n.name = "IEEE 14-Bus"
    n.set_snapshots(SNAPSHOTS)

    n.add("Carrier", "AC")
    n.add("Carrier", "coal", co2_emissions=0.34)
    n.add("Carrier", "gas", co2_emissions=0.20)
    n.add("Carrier", "oil", co2_emissions=0.27)

    # Schematic coordinates roughly following the canonical IEEE-14 one-line
    # diagram. Not geographic.
    bus_xy = {
        1: (0.0, 0.0),  2: (2.5, 0.0),  3: (5.0, -0.5),
        4: (3.5, 2.2),  5: (1.2, 2.0),  6: (1.0, 4.2),
        7: (4.2, 3.6),  8: (6.0, 3.8),  9: (4.0, 5.2),
        10: (2.8, 6.0), 11: (1.8, 5.0), 12: (-0.4, 5.2),
        13: (0.8, 6.4), 14: (3.0, 7.2),
    }
    for b, (x, y) in bus_xy.items():
        n.add("Bus", f"Bus {b}", v_nom=132.0, x=x, y=y, carrier="AC")

    # Five generators. Buses 1 & 2 are the real units; 3/6/8 stand in for the
    # condensers as mid/peaker plants. Capacities comfortably exceed the
    # ~259 MW system peak so the LOPF is always feasible; the cost spread
    # makes the dispatch non-trivial.
    gens = {
        "Coal 1": dict(bus="Bus 1",  carrier="coal", p_nom=332.0, marginal_cost=12.0),
        "Gas 2":  dict(bus="Bus 2",  carrier="gas",  p_nom=140.0, marginal_cost=28.0),
        "Gas 3":  dict(bus="Bus 3",  carrier="gas",  p_nom=100.0, marginal_cost=42.0),
        "Oil 6":  dict(bus="Bus 6",  carrier="oil",  p_nom=100.0, marginal_cost=48.0),
        "Oil 8":  dict(bus="Bus 8",  carrier="oil",  p_nom=100.0, marginal_cost=56.0),
    }
    for name, kw in gens.items():
        n.add("Generator", name, **kw)

    # Standard case14 nominal loads (MW). 11 load buses.
    base_loads = {
        2: 21.7, 3: 94.2, 4: 47.8, 5: 7.6, 6: 11.2, 9: 29.5,
        10: 9.0, 11: 3.5, 12: 6.1, 13: 13.5, 14: 14.9,
    }
    load_cols = {}
    for b, mw in base_loads.items():
        lname = f"Load {b}"
        n.add("Load", lname, bus=f"Bus {b}", carrier="AC")
        load_cols[lname] = mw * LOAD_SHAPE
    n.loads_t.p_set = pd.DataFrame(load_cols, index=SNAPSHOTS)

    # Standard case14 branch impedances (r, x in p.u. on 100 MVA base). The
    # three original tap-changing transformers (4-7, 4-9, 5-6) are modelled as
    # plain lines here — the template targets the LOPF, which ignores tap
    # ratios. s_nom is set uniformly high so no corridor binds.
    branches = [
        (1, 2,  0.01938, 0.05917), (1, 5,  0.05403, 0.22304),
        (2, 3,  0.04699, 0.19797), (2, 4,  0.05811, 0.17632),
        (2, 5,  0.05695, 0.17388), (3, 4,  0.06701, 0.17103),
        (4, 5,  0.01335, 0.04211), (4, 7,  0.0,     0.20912),
        (4, 9,  0.0,     0.55618), (5, 6,  0.0,     0.25202),
        (6, 11, 0.09498, 0.19890), (6, 12, 0.12291, 0.25581),
        (6, 13, 0.06615, 0.13027), (7, 8,  0.0,     0.17615),
        (7, 9,  0.0,     0.11001), (9, 10, 0.03181, 0.08450),
        (9, 14, 0.12711, 0.27038), (10, 11, 0.08205, 0.19207),
        (12, 13, 0.22092, 0.19988), (13, 14, 0.17093, 0.34802),
    ]
    # The three original transformer branches have r = 0 in case14. A line
    # with exactly zero resistance trips PyPSA's consistency check ("could
    # break the linear load flow"), so floor r at a tiny epsilon — negligible
    # electrically, but keeps the template warning-free on import.
    for b0, b1, r, x in branches:
        n.add("Line", f"Line {b0}-{b1}", bus0=f"Bus {b0}", bus1=f"Bus {b1}",
              r=max(r, 1e-4), x=x, s_nom=300.0)

    return n


# ── Template 3: Belgium Grid ─────────────────────────────────────────────────
# Unlike the two synthetic templates above, Belgium is NOT generated — it's
# PyPSA-Eur's own test-config output. The repo's test config is Belgium-only;
# `resources/test-elec/networks/base_s_5_elec_.nc` is the fully-built electricity
# network: the OpenStreetMap-derived Belgian HV grid clustered to 5 nodes, with
# generators (wind / solar / nuclear / CCGT / biomass / ...), batteries, H2
# electrolysis + fuel-cell links and real hourly demand. It solves out of the box.
#
# That .nc is a Snakemake workflow output (gitignored, not present on a fresh
# checkout). When it exists we re-export it here so the template self-identifies
# as "Belgium Grid"; when it doesn't, we leave the already-copied
# project_templates/belgium/network.nc untouched.
def build_belgium() -> pypsa.Network | None:
    repo_root = HERE.parents[2]   # project_templates → backend → pypsa-gui → repo
    src = repo_root / "resources" / "test-elec" / "networks" / "base_s_5_elec_.nc"
    if not src.exists():
        print(f"[belgium] SKIP — workflow output not found at {src}")
        print("          keeping the existing project_templates/belgium/network.nc")
        return None
    n = pypsa.Network(str(src))
    n.name = "Belgium Grid"
    return n


def main() -> None:
    builders = (
        ("3bus", build_3bus),
        ("ieee14", build_ieee14),
        ("belgium", build_belgium),
    )
    for template_id, builder in builders:
        n = builder()
        if n is None:
            continue
        _verify_feasible(n, template_id)
        _export(n, template_id)
    print("done.")


if __name__ == "__main__":
    main()
