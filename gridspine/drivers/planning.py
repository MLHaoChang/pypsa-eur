"""Variant-1 driver, increment-1 scope: the 39-bus vertical slice.
Each stage writes its artifact before the next starts — the file IS the
boundary. Engines only via the caged modules.

Increment-1 limitation: the pandapower loads are never rescaled — they stay at
case39's native (peak) level — so only hour 19, the LOAD_SHAPE peak the
dispatch is built for, produces a load-consistent flow; other hours converge
only by importing the difference silently through the slack, so the driver
refuses them.
"""
import argparse
import dataclasses
import json
from pathlib import Path

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import load_case39, registry_from_net
from gridspine.producers.pypsa_nodal import run_uc, to_dispatch_table, to_pypsa
from gridspine.schema.contracts import ContractError
from gridspine.schema.errors import StageError
from gridspine.static.loadflow import LFResult, apply_dispatch, run_lf

# case39 is a 60 Hz system: the RAW header BASFRQ and the line-charging B
# conversion both key off it, so the export must not take the 50 Hz default.
CASE39_F_HZ = 60.0

LEDGER = [
    "q_mvar=0 in dispatch table: gens are PV nodes, Q is an LF result (assumed)",
    "LOAD_SHAPE is a synthetic 24 h profile, peak hour 19, valley hour 3 (assumed)",
    "ext_grid modelled as 3000 MW import at 80 EUR/MWh marginal cost (assumed)",
    "case39 exported at f_hz=60.0 (60 Hz system)",
]


@dataclasses.dataclass
class SliceResult:
    converged: bool
    artifacts: dict
    lf: LFResult


PEAK_HOUR = 19

# net.load is never rescaled in increment 1, so the pandapower demand is
# case39's native (shape 1.00) level. Only the LOAD_SHAPE peak hour matches it.
HOUR_GUARD_MSG = (
    "increment 1's pandapower loads are fixed at the hour-19 (peak) level; "
    "other hours produce load-inconsistent flows (they still converge — the "
    "slack silently imports the residual); per-snapshot load scaling lands in "
    "increment 2"
)


def run_39bus_slice(outdir, hour: int = 19) -> SliceResult:
    if hour != PEAK_HOUR:
        raise ContractError(f"hour={hour}: {HOUR_GUARD_MSG}")
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    art = {}
    stage = "ingest"
    try:
        net = load_case39()
        registry = registry_from_net(net)

        stage = "dispatch"
        n = to_pypsa(net)
        table = to_dispatch_table(run_uc(n))
        art["dispatch"] = outdir / "dispatch.csv"
        table.to_csv(art["dispatch"], index=False)

        stage = "loadflow"
        apply_dispatch(net, table, hour=hour, registry=registry)
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
            "load_consistency": "hour 19 only (increment 1)",
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
