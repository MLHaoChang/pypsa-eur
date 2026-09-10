"""Read-back of PowerFactory results against a HANDOFF BUNDLE (spec stage 6).

The bundle is the contract: it carries the load flow that was handed over
(`lf_bus.csv`, `lf_branch_flow.csv`, written by `handoff/bundle.py` when the
bundle was made), so a PowerFactory export is compared against exactly that —
nothing is re-solved here, and the comparison cannot drift from what the
engineer imported. The per-element checks and tolerances are `pf_compare`'s,
the phase-1 oracle (<1 % |Vm|, 0.5° angle; 1 % P floored at 1 MW, 5 Mvar Q).

Every outcome is a file in the bundle directory, like every other stage:
the uploads byte-for-byte (`pf_bus.csv`, `pf_branches.csv`), the per-element
tables (`readback_bus.csv`, `readback_branches.csv`) and the summary
(`readback.json`) the UI and the copilot read. Nothing is written until the
comparison has succeeded, so a bad export leaves an earlier read-back intact.

Engine cage: pandas on CSVs only — no pandapower behind this module.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gridspine.readback.pf_compare import compare_branch_flows, compare_lf
from gridspine.schema.contracts import ContractError

READBACK_JSON = "readback.json"
PF_BUS_CSV = "pf_bus.csv"
PF_BRANCHES_CSV = "pf_branches.csv"
READBACK_BUS_CSV = "readback_bus.csv"
READBACK_BRANCHES_CSV = "readback_branches.csv"

#: The gate, named once so the summary and the figures quote the same numbers.
BUS_TOLERANCE = {"vm_rel_err": 0.01, "va_abs_err_deg": 0.5}
BRANCH_TOLERANCE = {"p_rel_err": 0.01, "q_abs_err_mvar": 5.0}

FIGURES = ("vm", "va", "branch_p", "branch_q")


@dataclasses.dataclass
class BundleLF:
    """The bundle's load flow in the shape `pf_compare` reads (`LFResult`'s
    attributes, without importing `static/`)."""

    converged: bool
    hour: int
    bus: pd.DataFrame
    branch_flow: pd.DataFrame


def load_bundle_lf(bundle_dir) -> BundleLF:
    bundle_dir = Path(bundle_dir)
    for name in ("manifest.json", "lf_bus.csv", "lf_branch_flow.csv"):
        if not (bundle_dir / name).is_file():
            raise ContractError(f"{bundle_dir} is not a complete handoff bundle: {name} missing")
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    bus = pd.read_csv(bundle_dir / "lf_bus.csv").set_index("bus")
    branch_flow = pd.read_csv(bundle_dir / "lf_branch_flow.csv", dtype={"ckt": str})
    return BundleLF(
        converged=bool(manifest.get("converged", False)),
        hour=int(manifest["hour"]),
        bus=bus,
        branch_flow=branch_flow,
    )


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _source(path) -> dict | None:
    if path is None:
        return None
    path = Path(path)
    return {"filename": path.name, "sha256": _sha256(path), "bytes": path.stat().st_size}


def _bus_summary(cmp: pd.DataFrame) -> dict:
    worst = (cmp["vm_rel_err"] / BUS_TOLERANCE["vm_rel_err"]).combine(
        cmp["va_abs_err_deg"] / BUS_TOLERANCE["va_abs_err_deg"], max
    )
    return {
        "n": int(len(cmp)),
        "n_ok": int(cmp["ok"].sum()),
        "max_vm_rel_err": float(cmp["vm_rel_err"].max()),
        "max_va_abs_err_deg": float(cmp["va_abs_err_deg"].max()),
        "worst": str(worst.idxmax()),
        "pass": bool(cmp["ok"].all()),
    }


def _branch_summary(cmp: pd.DataFrame) -> dict:
    worst = (cmp["p_rel_err"] / BRANCH_TOLERANCE["p_rel_err"]).combine(
        cmp["q_abs_err_mvar"] / BRANCH_TOLERANCE["q_abs_err_mvar"], max
    )
    return {
        "n": int(len(cmp)),
        "n_ok": int(cmp["ok"].sum()),
        "max_p_rel_err": float(cmp["p_rel_err"].max()),
        "max_q_abs_err_mvar": float(cmp["q_abs_err_mvar"].max()),
        "worst": [str(k) for k in worst.idxmax()],
        "pass": bool(cmp["ok"].all()),
    }


def read_back(bundle_dir, bus_csv, branch_csv=None) -> dict:
    """Compare a PowerFactory export with the bundle's load flow and record it.

    `bus_csv` is required (the phase-1 gate); `branch_csv` is optional — when
    absent the branches are NOT judged and the summary says so (`None`), rather
    than passing by default. The overall `pass` is the AND of what was judged.
    Raises `ContractError` — and writes nothing — when the bundle's load flow
    did not converge, a column is missing, or the element sets disagree.
    """
    bundle_dir = Path(bundle_dir)
    lf = load_bundle_lf(bundle_dir)
    bus_cmp = compare_lf(lf, bus_csv, vm_tol=BUS_TOLERANCE["vm_rel_err"], va_tol_deg=BUS_TOLERANCE["va_abs_err_deg"])
    branch_cmp = (
        compare_branch_flows(lf, branch_csv, p_tol=BRANCH_TOLERANCE["p_rel_err"],
                             q_tol_mvar=BRANCH_TOLERANCE["q_abs_err_mvar"])
        if branch_csv is not None else None
    )

    # Comparison done: now, and only now, write. Uploads first, byte-for-byte,
    # so the summary's hashes describe files that are actually there.
    (bundle_dir / PF_BUS_CSV).write_bytes(Path(bus_csv).read_bytes())
    bus_cmp.to_csv(bundle_dir / READBACK_BUS_CSV, index_label="bus")
    if branch_cmp is not None:
        (bundle_dir / PF_BRANCHES_CSV).write_bytes(Path(branch_csv).read_bytes())
        branch_cmp.to_csv(bundle_dir / READBACK_BRANCHES_CSV)
    else:
        for name in (PF_BRANCHES_CSV, READBACK_BRANCHES_CSV):
            (bundle_dir / name).unlink(missing_ok=True)      # an older branch read-back no longer applies

    bus = _bus_summary(bus_cmp)
    branches = _branch_summary(branch_cmp) if branch_cmp is not None else None
    summary = {
        "hour": lf.hour,
        "pass": bool(bus["pass"] and (branches is None or branches["pass"])),
        "bus": bus,
        "branches": branches,
        "tolerances": {"bus": BUS_TOLERANCE, "branches": BRANCH_TOLERANCE},
        "sources": {"bus_csv": _source(bus_csv), "branch_csv": _source(branch_csv)},
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (bundle_dir / READBACK_JSON).write_text(json.dumps(summary, indent=2))
    return summary


def readback_summary(bundle_dir) -> dict | None:
    path = Path(bundle_dir) / READBACK_JSON
    return json.loads(path.read_text()) if path.is_file() else None


def _rows(cmp: pd.DataFrame, pp_col: str, pf_col: str, err_col: str, label) -> list:
    return [
        {"element": label(idx), "pandapower": float(r[pp_col]), "powerfactory": float(r[pf_col]),
         "err": float(r[err_col]), "ok": bool(r["ok"])}
        for idx, r in cmp.iterrows()
    ]


def readback_figure(bundle_dir, name: str) -> dict:
    """One comparison as DATA — both sides per element, the error and the
    verdict — for the UI to draw and the copilot to read. `name` is one of
    `FIGURES`; a bundle with no read-back answers `available: False`."""
    if name not in FIGURES:
        raise ContractError(f"unknown figure {name!r}; one of {', '.join(FIGURES)}")
    bundle_dir = Path(bundle_dir)
    hour = int(json.loads((bundle_dir / "manifest.json").read_text())["hour"])
    summary = readback_summary(bundle_dir)
    if summary is None:
        return {"available": False, "name": name, "hour": hour,
                "reason": f"no PowerFactory results uploaded for hour {hour} yet"}
    if name in ("vm", "va"):
        cmp = pd.read_csv(bundle_dir / READBACK_BUS_CSV).set_index("bus")
        col, tol_key = ("vm_pu", "vm_rel_err") if name == "vm" else ("va_degree", "va_abs_err_deg")
        rows = _rows(cmp, f"{col}_pp", f"{col}_pf", tol_key, str)
        tolerance = {tol_key: BUS_TOLERANCE[tol_key]}
    else:
        if not (bundle_dir / READBACK_BRANCHES_CSV).is_file():
            return {"available": False, "name": name, "hour": hour,
                    "reason": f"no PowerFactory branch export uploaded for hour {hour}"}
        cmp = pd.read_csv(bundle_dir / READBACK_BRANCHES_CSV, dtype={"ckt": str}).set_index(["from_bus", "to_bus", "ckt"])
        col, tol_key = ("p_from_mw", "p_rel_err") if name == "branch_p" else ("q_from_mvar", "q_abs_err_mvar")
        rows = _rows(cmp, f"{col}_pp", f"{col}_pf", tol_key, lambda k: f"{k[0]}→{k[1]} ({k[2]})")
        tolerance = {tol_key: BRANCH_TOLERANCE[tol_key]}
    return {"available": True, "name": name, "hour": hour, "tolerance": tolerance, "rows": rows}
