"""Stage-boundary contracts for contingency screening and fault levels.

Three artifacts cross these boundaries, and each gets the same pre-coercion
discipline as ``validate_dispatch``: guards read the values AS SUPPLIED,
because ``astype`` launders a fractional hour into an integer and the string
"False" into truthiness before any later check could see it.

Contingency set
    One row per outage to study: ``contingency_id``, ``kind`` in {branch,
    unit}, ``element_ids`` (a list of canonical element ids) and ``order`` in
    {1, 2}. The set is engine-free data — whether every element id names a
    real element is the CALLER's check against the registry, so this module
    stays importable without pandapower.

Contingency results
    One row per (contingency, hour). A non-convergent contingency is a ROW
    with ``converged=False`` carrying ``NON_CONVERGED_SEVERITY`` — never a
    missing row, and never a row whose severity happens to read benign. An
    absent row and a survived contingency are indistinguishable downstream,
    which is why the validator enforces the sentinel in both directions:
    diverged rows must carry it, converged rows must not.

Fault levels
    One row per (bus, case) from IEC 60909, ``case`` in {max, min}. A fault
    level of zero at an energised bus is a coverage failure (an element with
    no short-circuit data was silently skipped), not a result, so the floor is
    strictly positive.

Contingency ids obey the canonical charset by FULLMATCH (a ``$``-anchored
regex passes ``"BUS_01\\n"``), but NOT the 12-character cap: that cap is the
PSS/E v33 NAME field width and applies to the bus and unit names that reach
the .raw. A pair id like ``BUS_01-BUS_02-1--BUS_02-BUS_03-1`` is longer by
construction and never becomes a NAME.
"""
import numpy as np
import pandas as pd

from .contracts import ContractError
from .network import NAME_CHARSET

#: Severity recorded for a contingency whose load flow did not converge.
#: A large FINITE float rather than ``inf``: it sorts above every real severity
#: and survives a CSV round-trip, which ``inf`` does not reliably do.
NON_CONVERGED_SEVERITY = 1.0e6

CONTINGENCY_KINDS = frozenset({"branch", "unit"})
FAULT_CASES = frozenset({"max", "min"})

CONTINGENCY_SET_COLUMNS = {
    "contingency_id": "object",
    "kind": "object",
    "element_ids": "object",
    "order": "int64",
}

CONTINGENCY_RESULT_COLUMNS = {
    "contingency_id": "object",
    "hour": "int64",
    "converged": "bool",
    "max_branch_loading_pct": "float64",
    "min_vm_pu": "float64",
    "max_vm_pu": "float64",
    "n_violations": "int64",
    "severity": "float64",
}
_PHYSICS_COLUMNS = ("max_branch_loading_pct", "min_vm_pu", "max_vm_pu")

FAULT_LEVEL_COLUMNS = {
    "bus": "object",
    "ikss_ka": "float64",
    "sk_mva": "float64",
    "case": "object",
}


def _check_ids(values, what: str) -> None:
    """Canonical charset by fullmatch, no nulls, no empties. No length cap — see module docstring."""
    for v in values:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            raise ContractError(f"{what} contains a null id")
        if not isinstance(v, str):
            raise ContractError(f"{what} contains a non-string id: {v!r}")
        if v == "":
            raise ContractError(f"{what} contains an empty id")
        if not NAME_CHARSET.fullmatch(v):
            raise ContractError(
                f"{what} id contains characters outside [A-Za-z0-9_-]: {v!r}"
            )


def _integral(series: pd.Series, what: str) -> pd.Series:
    """The pre-coercion integrality guard, shared by hour and count columns."""
    raw = pd.to_numeric(series, errors="coerce")
    bad = raw.isna() | (raw % 1 != 0)
    if bad.any():
        raise ContractError(
            f"{what} must be integral, got {series[bad].unique().tolist()}"
        )
    return raw


def validate_contingency_set(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(CONTINGENCY_SET_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"contingency set missing columns: {sorted(missing)}")
    out = df.copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    _check_ids(out["contingency_id"], "contingency_id")
    order_raw = out["order"]
    bad_order = (order_raw != 1) & (order_raw != 2)  # NaN != x is True, so NaN is bad
    if bad_order.any():
        raise ContractError(
            f"order must be exactly 1 or 2, got {order_raw[bad_order].unique().tolist()}"
        )
    bad_kind = ~out["kind"].isin(CONTINGENCY_KINDS)
    if bad_kind.any():
        raise ContractError(
            f"kind must be one of {sorted(CONTINGENCY_KINDS)}, got "
            f"{out.loc[bad_kind, 'kind'].unique().tolist()}"
        )
    for cid, elems, order in zip(out["contingency_id"], out["element_ids"], order_raw):
        # A bare string is iterable and would read as one element per character.
        if not isinstance(elems, (list, tuple)):
            raise ContractError(
                f"contingency {cid}: element_ids must be a list, got {type(elems).__name__}"
            )
        if len(elems) == 0:
            raise ContractError(f"contingency {cid}: element_ids is empty")
        if len(elems) != int(order):
            raise ContractError(
                f"contingency {cid}: order {int(order)} but {len(elems)} element_ids"
            )
        _check_ids(elems, f"contingency {cid} element_ids")
        if len(set(elems)) != len(elems):
            raise ContractError(f"contingency {cid}: element_ids repeat an element")

    try:
        out = out.astype(CONTINGENCY_SET_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"contingency set dtype coercion failed: {exc}") from exc

    dup = out["contingency_id"].duplicated()
    if dup.any():
        raise ContractError(
            f"duplicate contingency_id: {sorted(out.loc[dup, 'contingency_id'].unique())}"
        )
    return out


def validate_contingency_results(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(CONTINGENCY_RESULT_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"contingency results missing columns: {sorted(missing)}")
    out = df.copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    _check_ids(out["contingency_id"], "contingency_id")
    bad_conv = [
        v for v in out["converged"] if not isinstance(v, (bool, np.bool_))
    ]
    if bad_conv:
        raise ContractError(
            f"converged must be exactly True or False, got {bad_conv[:5]!r}"
        )
    _integral(out["hour"], "hour")
    n_viol = _integral(out["n_violations"], "n_violations")
    if (n_viol < 0).any():
        raise ContractError(
            f"n_violations must be >= 0, got {n_viol[n_viol < 0].unique().tolist()}"
        )

    try:
        out = out.astype(CONTINGENCY_RESULT_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"contingency results dtype coercion failed: {exc}") from exc

    sev = out["severity"]
    if sev.isna().any() or np.isinf(sev).any():
        raise ContractError("severity must be finite (no NaN, no inf)")
    if (sev < 0).any():
        raise ContractError(f"severity must be >= 0, got {sev[sev < 0].unique().tolist()}")

    converged = out["converged"]
    # Physics columns may be NaN ONLY on a diverged row — there is no flow to
    # report — and must be finite everywhere else.
    phys = out[list(_PHYSICS_COLUMNS)]
    nan_on_converged = phys.isna().any(axis=1) & converged
    if nan_on_converged.any():
        raise ContractError(
            "NaN physics on a converged row: "
            f"{out.loc[nan_on_converged, 'contingency_id'].tolist()}"
        )
    inf_any = np.isinf(phys.fillna(0.0)).any(axis=1)
    if inf_any.any():
        raise ContractError(
            f"non-finite inf in physics columns: {out.loc[inf_any, 'contingency_id'].tolist()}"
        )
    conv_rows = out[converged]
    if (conv_rows["max_branch_loading_pct"] < 0).any():
        raise ContractError("max_branch_loading_pct must be >= 0")
    if (conv_rows["min_vm_pu"] <= 0).any() or (conv_rows["max_vm_pu"] <= 0).any():
        raise ContractError("min_vm_pu and max_vm_pu must be > 0")
    if (conv_rows["min_vm_pu"] > conv_rows["max_vm_pu"]).any():
        raise ContractError("min_vm_pu exceeds max_vm_pu")

    # The cross-field rule, both directions.
    diverged_benign = (~converged) & (sev != NON_CONVERGED_SEVERITY)
    if diverged_benign.any():
        raise ContractError(
            "diverged rows must carry NON_CONVERGED_SEVERITY: "
            f"{out.loc[diverged_benign, 'contingency_id'].tolist()}"
        )
    converged_sentinel = converged & (sev == NON_CONVERGED_SEVERITY)
    if converged_sentinel.any():
        raise ContractError(
            "converged rows may not carry NON_CONVERGED_SEVERITY: "
            f"{out.loc[converged_sentinel, 'contingency_id'].tolist()}"
        )

    dup = out.duplicated(subset=["contingency_id", "hour"])
    if dup.any():
        raise ContractError(
            f"duplicate (contingency_id, hour) rows: {out.loc[dup, 'contingency_id'].tolist()}"
        )
    return out


def validate_fault_levels(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(FAULT_LEVEL_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"fault levels missing columns: {sorted(missing)}")
    out = df.copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    _check_ids(out["bus"], "bus")
    bad_case = ~out["case"].isin(FAULT_CASES)
    if bad_case.any():
        raise ContractError(
            f"case must be one of {sorted(FAULT_CASES)}, got "
            f"{out.loc[bad_case, 'case'].unique().tolist()}"
        )

    try:
        out = out.astype(FAULT_LEVEL_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"fault levels dtype coercion failed: {exc}") from exc

    for col in ("ikss_ka", "sk_mva"):
        vals = out[col]
        bad = vals.isna() | np.isinf(vals) | (vals <= 0)
        if bad.any():
            raise ContractError(
                f"{col} must be finite and > 0, got {vals[bad].unique().tolist()} "
                f"at bus(es) {out.loc[bad, 'bus'].tolist()}"
            )
    dup = out.duplicated(subset=["bus", "case"])
    if dup.any():
        raise ContractError(
            f"duplicate (bus, case) rows: {out.loc[dup, 'bus'].tolist()}"
        )
    return out
