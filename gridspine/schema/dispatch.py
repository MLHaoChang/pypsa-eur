"""The stage-1 -> stage-2 contract: per-unit, per-hour dispatch keyed by
canonical unit_id. PyPSA is one producer of this table; client-supplied
snapshots are another. Downstream stages never see a PyPSA object.

Integrality and range guards run against the values AS SUPPLIED, before dtype
coercion. `astype("int64")` truncates silently -- 1.5 becomes 1 -- so a guard
placed after coercion cannot see what the producer actually sent, and a relaxed
unit-commitment status of 0.5 would be recorded as fully online.

`q_mvar` is a load-flow result rather than a commitment decision: a unit with
status 0 may legitimately carry nonzero q_mvar (an offline synchronous machine
still exchanges reactive power with the grid), so the offline guard constrains
p_mw only. That asymmetry is intended.
"""
import numpy as np
import pandas as pd

from .contracts import ContractError

DISPATCH_COLUMNS = {
    "unit_id": "object",
    "hour": "int64",
    "p_mw": "float64",
    "q_mvar": "float64",
    "status": "int64",
}
_P_OFFLINE_TOL_MW = 1e-4


def validate_dispatch(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(DISPATCH_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"dispatch table missing columns: {sorted(missing)}")
    out = df.copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    if out["unit_id"].isna().any():
        raise ContractError(
            f"dispatch table has null unit_id in {int(out['unit_id'].isna().sum())} row(s)"
        )
    for col in ("p_mw", "q_mvar"):
        if out[col].isna().any():
            raise ContractError(f"dispatch table has NaN in {col}")
    status_raw = out["status"]
    bad_status = (status_raw != 0) & (status_raw != 1)  # NaN != x is True, so NaN is bad
    if bad_status.any():
        raise ContractError(
            f"status must be exactly 0 or 1, got {status_raw[bad_status].unique().tolist()}"
        )
    hour_raw = pd.to_numeric(out["hour"], errors="coerce")
    bad_hour = hour_raw.isna() | (hour_raw % 1 != 0)
    if bad_hour.any():
        raise ContractError(
            f"hour must be integral, got {out.loc[bad_hour, 'hour'].unique().tolist()}"
        )

    try:
        out = out.astype(DISPATCH_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"dispatch table dtype coercion failed: {exc}") from exc

    for col in ("p_mw", "q_mvar"):
        if np.isinf(out[col]).any():
            raise ContractError(f"dispatch table has non-finite inf in {col}")
    dup = out.duplicated(subset=["unit_id", "hour"])
    if dup.any():
        raise ContractError(f"duplicate (unit_id, hour) rows: {out.loc[dup, 'unit_id'].tolist()}")
    offline_producing = (out["status"] == 0) & (out["p_mw"].abs() > _P_OFFLINE_TOL_MW)
    if offline_producing.any():
        raise ContractError(
            f"units with status 0 but nonzero p_mw: {out.loc[offline_producing, 'unit_id'].tolist()}"
        )
    return out
