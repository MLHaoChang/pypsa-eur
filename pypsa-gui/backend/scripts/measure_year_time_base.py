"""Measure a concentrator-sized plan on the hourly-year time base (grid-to-process 09, design §4.2).

Not part of the test suite: the year solve may take minutes. Run from pypsa-gui/backend:

    python scripts/measure_year_time_base.py                    # both bases, each in its own process
    python scripts/measure_year_time_base.py year --ipm --timeout 1800

`--ipm` sets the app's `solver_options` {"solver": "ipm"} (HiGHS interior point; the app's default is HiGHS'
choice, dual simplex here). Each base runs in a child process; the parent reports the child's peak RSS and wall
time even when the time limit kills it.

A demo network of about 30 components (5 buses; grid, PV, wind and the lost-concentrate slack; a crusher and two
mills as committable process Links under linearised UC; a stockpile, a 21-day concentrate shed and a battery;
three candidates per period) is staged over 2027/2030/2035 by `eh_site.stage_network` and solved through the app's
own `run_simulation` (its HiGHS options, presolve and the process-UC binary check). Reported per base: build time,
solve time, peak RSS of the process, LP rows / columns / non-zeros, whether any variable is integer, the objective,
the machine and the HiGHS version. The synthetic profiles are demo inputs, not data.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import platform
import queue
import resource
import subprocess
import sys
import threading
import time

import numpy as np
import pandas as pd
import pypsa

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BACKEND), str(BACKEND / "project_templates")]

YEARS = [2027, 2030, 2035]
WEIGHTS = {2027: 3, 2030: 5, 2035: 10}
CYCLE_H, SHIP_H = 504, 24               # a vessel every 21 days, loaded out over a day
CONC_TPH = 60.0                          # mean concentrate rate the shipments take (t/h)


def _profiles(index: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    hod = index.hour.to_numpy()
    doy = index.dayofyear.to_numpy()
    season = 0.75 + 0.25 * np.cos(2 * np.pi * (doy - 172) / 365)           # summer-high PV
    pv = np.clip(np.sin(np.pi * (hod - 6) / 12), 0, None) * season
    rng = np.random.default_rng(9)
    wind = np.clip(0.35 + 0.2 * np.cos(2 * np.pi * doy / 365)
                   + np.convolve(rng.normal(0, 0.25, len(index)), np.ones(12) / 12, "same"), 0, 1)
    price = 60 + 25 * np.sin(np.pi * (hod - 8) / 12) ** 2 + 10 * np.cos(2 * np.pi * doy / 365)
    return {"pv": pv, "wind": wind, "price": price}


def build(base: str) -> pypsa.Network:
    import eh_site as S

    index = (S.SNAPSHOTS if base == "week" else pd.date_range("2030-01-01", periods=8760, freq="h"))
    prof = _profiles(pd.DatetimeIndex(index))
    if base == "year":
        ship = np.zeros(len(index))
        for start in range(CYCLE_H - SHIP_H, len(index) - SHIP_H + 1, CYCLE_H):
            ship[start:start + SHIP_H] = CONC_TPH * CYCLE_H / SHIP_H
    else:
        ship = np.full(len(index), CONC_TPH)        # the week base can only hold continuous delivery

    n = pypsa.Network()
    n.set_snapshots(index)
    for bus, carrier in (("grid", "AC"), ("mv", "AC"), ("ore", "ore"), ("conc", "concentrate"),
                         ("batt", "battery")):
        n.add("Bus", bus, carrier=carrier)
    n.add("Generator", "grid_supply", bus="grid", p_nom=80.0, marginal_cost=pd.Series(prof["price"], index))
    n.add("Generator", "pv", bus="mv", p_nom=20.0, p_max_pu=pd.Series(prof["pv"], index))
    n.add("Generator", "wind", bus="mv", p_nom=15.0, p_max_pu=pd.Series(prof["wind"], index))
    n.add("Generator", "__lost_conc", bus="conc", p_nom=1e4, marginal_cost=900.0)
    n.add("Link", "pcc", bus0="grid", bus1="mv", p_nom=80.0, efficiency=0.99)
    n.add("Load", "auxiliaries", bus="mv", p_set=pd.Series(4.0, index))
    n.add("Load", "shipments", bus="conc", p_set=pd.Series(ship, index))
    # Process units: electricity in on bus0, product out on bus1, ore drawn on bus2 (mills).
    n.add("Link", "crusher", bus0="mv", bus1="ore", p_nom=12.0, efficiency=200.0 / 12.0,
          committable=True, p_min_pu=0.4, min_up_time=4, min_down_time=4, start_up_cost=500.0)
    for mill in ("mill_1", "mill_2"):
        n.add("Link", mill, bus0="mv", bus1="conc", bus2="ore", p_nom=18.0, efficiency=40.0 / 18.0,
              efficiency2=-100.0 / 18.0, committable=True, p_min_pu=0.5, min_up_time=8, min_down_time=6,
              start_up_cost=2000.0)
    n.links["eh_process"] = n.links.index.isin(["crusher", "mill_1", "mill_2"])
    n.add("Store", "stockpile", bus="ore", e_nom=8000.0)
    n.add("Store", "shed", bus="conc", e_nom_extendable=True, capital_cost=40.0, e_nom_max=60_000.0)
    n.add("Store", "battery", bus="batt", e_nom=20.0)
    n.add("Link", "battery_charge", bus0="mv", bus1="batt", p_nom=10.0, efficiency=0.95)
    n.add("Link", "battery_discharge", bus0="batt", bus1="mv", p_nom=10.0, efficiency=0.95)
    n.stores["delivery_interval_h"] = 0.0
    if base == "year":
        n.stores.loc["shed", "delivery_interval_h"] = float(CYCLE_H)

    def candidates(net, y):
        snaps = net.snapshots[net.snapshots.get_level_values(0) == y]
        tiled = lambda k: np.tile(prof[k], len(YEARS))  # noqa: E731
        net.add("Generator", f"pv_new_{y}", bus="mv", p_nom_extendable=True, p_nom_max=100.0, capital_cost=60_000.0,
                build_year=y, lifetime=25, p_max_pu=pd.Series(tiled("pv"), net.snapshots))
        net.add("Generator", f"wind_new_{y}", bus="mv", p_nom_extendable=True, p_nom_max=100.0, capital_cost=110_000.0,
                build_year=y, lifetime=25, p_max_pu=pd.Series(tiled("wind"), net.snapshots))
        net.add("Link", f"bess_new_{y}", bus0="mv", bus1="batt", p_nom_extendable=True, p_nom_max=100.0, capital_cost=30_000.0,
                build_year=y, lifetime=15, efficiency=0.95)
        assert len(snaps)

    return S.stage_network(n, YEARS, load_scale={y: {"auxiliaries": 1.0, "shipments": 1.0} for y in YEARS},
                           energization={"pcc": [80.0, 80.0, 80.0]}, weights=WEIGHTS,
                           existing=("grid_supply", "pv", "wind", "pcc", "crusher", "mill_1", "mill_2"),
                           candidates=candidates, time_base=base)


def measure(base: str, ipm: bool = False) -> dict:
    import highspy
    from services.solver_service import SolverConfig, run_simulation
    from services.validation_service import has_errors, validate_for_run

    t0 = time.perf_counter()
    n = build(base)
    build_s = time.perf_counter() - t0
    cfg = SolverConfig(solver_name="highs", multi_investment_periods=True,
                       solver_options={"solver": "ipm"} if ipm else {})
    issues = validate_for_run(n, cfg)
    if has_errors(issues):
        raise SystemExit(f"{base}: refused: {[(i.code, i.name) for i in issues if i.severity == 'error']}")
    log: queue.SimpleQueue = queue.SimpleQueue()
    t1 = time.perf_counter()
    status, cond = run_simulation(cfg, n, threading.Lock(), threading.Event(), log)
    solve_s = time.perf_counter() - t1
    m = n.model
    a = m.matrices.A
    phases = []
    while not log.empty():
        msg = str(log.get())
        if "Unit commitment" in msg or "binary" in msg.lower() or "Presolve" in msg:
            phases.append(msg.strip()[:160])
    return {
        "base": base, "status": status, "condition": cond, "snapshots": len(n.snapshots),
        "components": int(sum(len(df) for df in (n.buses, n.generators, n.links, n.loads, n.stores))),
        "build_s": round(build_s, 2), "solve_s": round(solve_s, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "lp_rows": int(a.shape[0]), "lp_cols": int(a.shape[1]), "lp_nnz": int(a.nnz),
        "integer_vars": int(m.variables.integers.nvars + m.variables.binaries.nvars),
        "objective": float(n.objective), "shed_e_nom_opt": float(n.stores.at["shed", "e_nom_opt"]),
        "highs": highspy.Highs().version(), "pypsa": pypsa.__version__,
        "machine": f"{platform.processor() or platform.machine()}, {os.cpu_count()} CPU, "
                   f"{os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2**30:.1f} GiB",
        "phases": phases,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("bases", nargs="*", default=["week", "year"])
    ap.add_argument("--ipm", action="store_true")
    ap.add_argument("--timeout", type=float, default=2700.0)
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    logging.disable(logging.WARNING)
    if args.child:
        print(json.dumps({**measure(args.bases[0], args.ipm), "solver_options": "ipm" if args.ipm else "app default"}))
    else:
        for b in args.bases:
            cmd = [sys.executable, __file__, b, "--child", *(["--ipm"] if args.ipm else [])]
            t0 = time.perf_counter()
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, cwd=BACKEND, timeout=args.timeout)
                line = out.stdout.strip().splitlines()[-1] if out.returncode == 0 else out.stderr[-2000:]
            except subprocess.TimeoutExpired:
                line = json.dumps({"base": b, "status": "time limit", "limit_s": args.timeout})
            print(line, json.dumps({"base": b, "wall_s": round(time.perf_counter() - t0, 1),
                                    "child_peak_rss_mb": round(resource.getrusage(resource.RUSAGE_CHILDREN)
                                                               .ru_maxrss / 1024, 1)}), flush=True)
