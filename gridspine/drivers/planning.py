"""Variant-1 driver: the 39-bus vertical slice.
Each stage writes its artifact before the next starts — the file IS the
boundary. Engines only via the caged modules.

Increment 2 retired the hour-19 guard. Increment 1 never rescaled the
pandapower loads — they stayed at case39's native (peak) level — so only hour
19, the LOAD_SHAPE peak the dispatch was built for, produced a load-consistent
flow; other hours converged only by importing the difference silently through
the slack, and the driver refused them rather than emit a flow whose residual
was invisible. The dispatch stage now writes a LOADS artifact alongside the
dispatch table, and stage 2 applies both, so every hour is load-consistent and
the slack carries losses only.
"""
import argparse
import dataclasses
import json
from pathlib import Path

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39, registry_from_net
from gridspine.producers.pypsa_nodal import (
    run_uc,
    to_dispatch_table,
    to_loads_table,
    to_pypsa,
)
from gridspine.schema.errors import StageError
from gridspine.static.loadflow import LFResult, apply_snapshot, run_lf

# case39 is a 60 Hz system: the RAW header BASFRQ and the line-charging B
# conversion both key off it, so the export must not take the 50 Hz default.
CASE39_F_HZ = 60.0

LEDGER = [
    "q_mvar=0 in dispatch table: gens are PV nodes, Q is an LF result (assumed)",
    "LOAD_SHAPE is a synthetic 24 h profile, peak hour 19, valley hour 3 (assumed)",
    "ext_grid modelled as 3000 MW import at 80 EUR/MWh marginal cost (assumed)",
    "case39 exported at f_hz=60.0 (60 Hz system)",
    "loads q_mvar scaled at constant power factor from case39's native Q/P per "
    "bus, held fixed across all hours (assumed)",
]


@dataclasses.dataclass
class SliceResult:
    converged: bool
    artifacts: dict
    lf: LFResult


# The LOAD_SHAPE peak, and still the default `hour` so the increment-1 call is
# unchanged. It is no longer the only legal value: `apply_snapshot` sets the
# demand for whichever hour is asked for, and a wrong hour now fails loudly
# inside the loads contract instead of converging on a phantom import.
PEAK_HOUR = 19


def run_39bus_slice(outdir, hour: int = 19) -> SliceResult:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    art = {}
    stage = "ingest"
    try:
        net = load_case39()
        registry = registry_from_net(net)

        stage = "dispatch"
        n = to_pypsa(net)
        # The loads table is read off the network BEFORE the solve: p_set is an
        # input to the UC, not a result of it, so demand is knowable either
        # side of `run_uc` and taking it here keeps the artifact independent of
        # whether the solve succeeded.
        loads = to_loads_table(n, net)
        art["loads"] = outdir / "loads.csv"
        loads.to_csv(art["loads"], index=False)
        table = to_dispatch_table(run_uc(n))
        art["dispatch"] = outdir / "dispatch.csv"
        table.to_csv(art["dispatch"], index=False)

        stage = "loadflow"
        apply_snapshot(net, table, loads, hour=hour, registry=registry)
        lf = run_lf(net)
        art["lf_bus"] = outdir / "lf_bus.csv"
        art["lf_branch"] = outdir / "lf_branch.csv"
        lf.bus.to_csv(art["lf_bus"])
        lf.branch_loading.to_csv(art["lf_branch"])

        stage = "handoff"
        art["raw"] = outdir / "case39_dispatch.raw"
        write_raw(net, art["raw"], title=f"case39 UC dispatch hour {hour}",
                  f_hz=CASE39_F_HZ)

        art["manifest"] = outdir / "manifest.json"
        art["manifest"].write_text(json.dumps({
            "stages": ["ingest", "dispatch", "loadflow", "handoff"],
            "network": "pandapower case39, canonical names",
            "hour": hour,
            "load_consistency": "per-snapshot loads artifact (increment 2)",
            "ledger": LEDGER,
        }, indent=2))
        return SliceResult(converged=lf.converged, artifacts=art, lf=lf)
    except Exception as exc:
        StageError(stage=stage, element_ids=[], cause=repr(exc)).write(outdir)
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hour", type=int, default=19)
    args = ap.parse_args()
    res = run_39bus_slice(args.out, hour=args.hour)
    print(f"converged={res.converged}")
    for k, p in res.artifacts.items():
        print(f"{k}: {p}")
