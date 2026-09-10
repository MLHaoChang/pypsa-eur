"""The DC-sensitivity artifact: how the LODF crosses the stage boundary.

``ranking/`` imports no engine, yet task 8 ranks all 8760 hours on N-1
severity. The two meet here: ``static/lodf`` computes PTDF and LODF from the
pandapower DC model and hands them over as plain arrays with the names that
key every other artifact; ``ranking/severity`` rebuilds each hour's bus
injections from the dispatch and loads tables and multiplies. Like every
other arrow in the pipeline it is a FILE (``.npz``), so a client can recompute
the severity from the CSVs and this one file without the simulation stack.

The validator is the contract: shapes agree, the reference column of the
PTDF is exactly zero, islanding branches have NaN columns (never inf) and live
ones are finite with -1 on the diagonal, ratings are positive, names are
canonical and unique. numpy only.
"""
import dataclasses
from pathlib import Path

import numpy as np

from .contracts import ContractError
from .network import NAME_CHARSET


@dataclasses.dataclass
class DCSensitivities:
    ptdf: np.ndarray          # n_branch x n_bus, bus order = bus_names, ref column zero
    lodf: np.ndarray          # n_branch x n_branch, NaN columns for islanding branches
    islanding: np.ndarray     # bool per branch
    rating_mva: np.ndarray    # from-side MVA rating per branch
    bus_names: list
    branch_ids: list          # "from-to-ckt", the N-1 contingency ids
    ref_bus: int


def validate_dc_sensitivities(s: DCSensitivities) -> DCSensitivities:
    ptdf = np.asarray(s.ptdf, dtype=float)
    lodf = np.asarray(s.lodf, dtype=float)
    islanding = np.asarray(s.islanding, dtype=bool)
    rating = np.asarray(s.rating_mva, dtype=float)
    bus_names = [str(b) for b in s.bus_names]
    branch_ids = [str(b) for b in s.branch_ids]
    if ptdf.ndim != 2:
        raise ContractError(f"ptdf must be 2-D, got shape {ptdf.shape}")
    n_br, n_bus = ptdf.shape
    if lodf.shape != (n_br, n_br):
        raise ContractError(f"lodf shape {lodf.shape} does not match {n_br} branches")
    if islanding.shape != (n_br,) or rating.shape != (n_br,):
        raise ContractError("islanding and rating_mva must have one entry per branch")
    if len(bus_names) != n_bus or len(set(bus_names)) != n_bus:
        raise ContractError(f"bus_names must be {n_bus} unique names")
    if len(branch_ids) != n_br or len(set(branch_ids)) != n_br:
        raise ContractError(f"branch_ids must be {n_br} unique ids")
    bad = [b for b in bus_names if not NAME_CHARSET.fullmatch(b)]
    if bad:
        raise ContractError(f"bus_names outside the canonical charset: {bad}")
    ref = int(s.ref_bus)
    if not 0 <= ref < n_bus:
        raise ContractError(f"ref_bus {ref} outside 0..{n_bus - 1}")
    if not np.isfinite(ptdf).all():
        raise ContractError("ptdf has non-finite entries")
    if np.abs(ptdf[:, ref]).max() != 0.0:
        raise ContractError("ptdf reference column must be exactly zero")
    live = ~islanding
    if not np.isfinite(lodf[:, live]).all():
        raise ContractError("lodf has non-finite entries in non-islanding columns")
    if islanding.any() and not np.isnan(lodf[:, islanding]).all():
        raise ContractError("lodf islanding columns must be NaN")
    if live.any() and not np.allclose(lodf[live, live], -1.0, atol=1e-6):
        raise ContractError("lodf diagonal must be -1 on non-islanding branches")
    if not (np.isfinite(rating).all() and (rating > 0).all()):
        raise ContractError("rating_mva must be finite and > 0")
    return DCSensitivities(
        ptdf=ptdf, lodf=lodf, islanding=islanding, rating_mva=rating,
        bus_names=bus_names, branch_ids=branch_ids, ref_bus=ref,
    )


def save_dc_sensitivities(s: DCSensitivities, path) -> Path:
    s = validate_dc_sensitivities(s)
    path = Path(path)
    if path.suffix != ".npz":
        path = path.with_suffix(path.suffix + ".npz")
    np.savez(
        path, ptdf=s.ptdf, lodf=s.lodf, islanding=s.islanding, rating_mva=s.rating_mva,
        bus_names=np.array(s.bus_names, dtype=str), branch_ids=np.array(s.branch_ids, dtype=str),
        ref_bus=np.array(s.ref_bus),
    )
    return path


def load_dc_sensitivities(path) -> DCSensitivities:
    with np.load(Path(path), allow_pickle=False) as z:
        return validate_dc_sensitivities(DCSensitivities(
            ptdf=z["ptdf"], lodf=z["lodf"], islanding=z["islanding"], rating_mva=z["rating_mva"],
            bus_names=[str(b) for b in z["bus_names"]], branch_ids=[str(b) for b in z["branch_ids"]],
            ref_bus=int(z["ref_bus"]),
        ))
