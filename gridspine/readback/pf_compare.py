"""PowerFactory result read-back and <1% comparison — the phase-1 oracle.
Manual flow: import the .raw in PowerFactory, run Newton-Raphson LF,
export bus results per the fixture README, drop the CSV, run the test."""

import pandas as pd

from gridspine.schema.contracts import ContractError


def compare_lf(lf, pf_csv, vm_tol=0.01, va_tol_deg=0.5) -> pd.DataFrame:
    pf = pd.read_csv(pf_csv).set_index("bus_name")
    if set(pf.index) != set(lf.bus.index):
        raise ContractError(
            f"bus set mismatch: only-pandapower={sorted(set(lf.bus.index) - set(pf.index))} "
            f"only-powerfactory={sorted(set(pf.index) - set(lf.bus.index))}"
        )
    out = pd.DataFrame(index=lf.bus.index.copy())
    out["vm_pu_pp"] = lf.bus["vm_pu"]
    out["vm_pu_pf"] = pf["vm_pu"]
    out["vm_rel_err"] = (out["vm_pu_pp"] - out["vm_pu_pf"]).abs() / out["vm_pu_pf"].abs()
    out["va_degree_pp"] = lf.bus["va_degree"]
    out["va_degree_pf"] = pf["va_degree"]
    out["va_abs_err_deg"] = (out["va_degree_pp"] - out["va_degree_pf"]).abs()
    out["ok"] = (out["vm_rel_err"] < vm_tol) & (out["va_abs_err_deg"] < va_tol_deg)
    return out
