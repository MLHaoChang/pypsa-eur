"""PowerFactory result read-back and <1% comparison — the phase-1 oracle.
Manual flow: import the .raw in PowerFactory, run Newton-Raphson LF,
export bus results per the fixture README, drop the CSV, run the test."""

import pandas as pd

from gridspine.schema.contracts import ContractError


def compare_lf(lf, pf_csv, vm_tol=0.01, va_tol_deg=0.5) -> pd.DataFrame:
    # A diverged LF carries an empty bus frame, which would otherwise surface
    # below as a bus-set mismatch and blame the fixture.
    if not lf.converged:
        raise ContractError("LF result not converged — nothing to compare")
    pf = pd.read_csv(pf_csv)
    missing = [c for c in ("bus_name", "vm_pu", "va_degree") if c not in pf.columns]
    if missing:
        # A hand-exported CSV with the wrong header otherwise fails as a bare
        # KeyError naming only the first column pandas happened to reach.
        raise ContractError(f"PowerFactory CSV missing required columns: {missing}")
    pf = pf.set_index("bus_name")
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


# --- branch flows -----------------------------------------------------------
# The bus comparison above is keyed on a single name. A branch needs the whole
# (from_bus, to_bus, ckt) triple: parallel circuits share a bus pair and are
# distinguishable ONLY by the circuit id the .raw stamped on them, so joining
# on the pair alone would silently average two different circuits together.

BRANCH_KEY = ["from_bus", "to_bus", "ckt"]
#: Exactly the column set the fixture runbook tells the operator to export —
#: and deliberately the same six names, in the same order, as
#: ``gridspine.static.loadflow.BRANCH_FLOW_COLUMNS``, so the PowerFactory CSV
#: drops straight onto the pandapower frame with no translation step. The two
#: constants are NOT imported from one another (the engine cage keeps this
#: module pandas-only, with no pandapower import behind it); they are kept in
#: step by test_pf_compare.py and the runbook. Change one, change all three.
BRANCH_CSV_COLUMNS = BRANCH_KEY + ["p_from_mw", "q_from_mvar", "loading_percent"]


def _branch_indexed(df, side):
    missing = [c for c in BRANCH_CSV_COLUMNS if c not in df.columns]
    if missing:
        raise ContractError(
            f"{side} branch CSV missing required columns: {missing}"
        )
    out = df.copy()
    # `ckt` is a RAW text field, but a column of all-numeric ids reads back
    # from pandas as int64. Comparing that against the LF result's str keys
    # reports every branch as missing on BOTH sides — a key-set mismatch that
    # looks like a topology disagreement and is really a dtype.
    out["ckt"] = out["ckt"].astype(str).str.strip()
    for c in ("from_bus", "to_bus"):
        out[c] = out[c].astype(str).str.strip()
    out = out.set_index(BRANCH_KEY)
    if out.index.duplicated().any():
        # Equal key SETS do not imply equal key LISTS; a duplicated row would
        # fan the alignment out instead of failing.
        raise ContractError(
            f"duplicate branch keys in {side}: "
            f"{sorted(set(out.index[out.index.duplicated()]))}"
        )
    return out


def compare_branch_flows(lf, pf_csv, p_tol=0.01, q_tol_mvar=5.0) -> pd.DataFrame:
    """Per-branch pandapower-vs-PowerFactory FROM-end flow comparison.

    `p_tol` is RELATIVE against the PowerFactory reading, whose magnitude is
    floored at 1 MW: a branch idling near zero otherwise divides a rounding
    difference by ~0 and reports thousands of percent. `q_tol_mvar` is
    absolute — reactive flow legitimately crosses zero, so a relative test
    there has no stable reference at all. `loading_percent` is carried
    through for the operator to eyeball; it is NOT part of `ok`, because it
    is a derived ratio against a rating the two tools may not agree on.
    """
    # Same guard as compare_lf: a diverged LF carries an empty branch frame,
    # which would otherwise surface below as a key-set mismatch and blame the
    # fixture for what is really a failed load flow.
    if not lf.converged:
        raise ContractError("LF result not converged — nothing to compare")
    pf = _branch_indexed(pd.read_csv(pf_csv), "PowerFactory")
    pp_ = _branch_indexed(lf.branch_flow, "pandapower")
    if set(pf.index) != set(pp_.index):
        raise ContractError(
            f"branch set mismatch: only-pandapower={sorted(set(pp_.index) - set(pf.index))} "
            f"only-powerfactory={sorted(set(pf.index) - set(pp_.index))}"
        )
    out = pd.DataFrame(index=pp_.index.copy())
    out["p_from_mw_pp"] = pp_["p_from_mw"]
    out["p_from_mw_pf"] = pf["p_from_mw"]
    out["p_abs_err_mw"] = (out["p_from_mw_pp"] - out["p_from_mw_pf"]).abs()
    out["p_rel_err"] = out["p_abs_err_mw"] / out["p_from_mw_pf"].abs().clip(lower=1.0)
    out["q_from_mvar_pp"] = pp_["q_from_mvar"]
    out["q_from_mvar_pf"] = pf["q_from_mvar"]
    out["q_abs_err_mvar"] = (out["q_from_mvar_pp"] - out["q_from_mvar_pf"]).abs()
    out["loading_percent_pp"] = pp_["loading_percent"]
    out["loading_percent_pf"] = pf["loading_percent"]
    out["ok"] = (out["p_rel_err"] < p_tol) & (out["q_abs_err_mvar"] < q_tol_mvar)
    return out
