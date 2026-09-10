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


LOADS_COLUMNS = {
    "bus": "object",
    "hour": "int64",
    "p_mw": "float64",
    "q_mvar": "float64",
}


def validate_loads(df: pd.DataFrame) -> pd.DataFrame:
    """The loads artifact: per-bus, per-hour demand, keyed by canonical bus name.

    Same pre-coercion discipline as ``validate_dispatch`` and for the same
    reason: ``astype("int64")`` turns an hour of 1.5 into 1 in silence, so the
    integrality guard has to see the values as supplied.

    Relationship to ``ranking.metrics._checked_loads``, which re-checks a
    minimum on the consumer side so a client can recompute metrics from the CSV
    without importing the producer stack: everything this function accepts,
    that one accepts too. The one place this is deliberately STRICTER on a
    shared field is the sign of ``p_mw`` — a demand table with negative real
    power is a producer defect (an injection dressed as a load), and catching
    it here, where the artifact is written, is cheaper than explaining a
    negative `load_mw` metric later. Strictness in that direction is safe: it
    can only reject frames that would have reached ranking, never admit ones
    ranking would refuse.

    ``q_mvar`` is signed on purpose — a capacitive load exchanges negative
    reactive power, and case39 has two such buses.
    """
    missing = set(LOADS_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"loads table missing columns: {sorted(missing)}")
    out = df[list(LOADS_COLUMNS)].copy()

    # --- guards on the values as supplied; coercion below would hide these ---
    if out["bus"].isna().any():
        raise ContractError(
            f"loads table has null bus in {int(out['bus'].isna().sum())} row(s)"
        )
    for col in ("p_mw", "q_mvar"):
        if out[col].isna().any():
            raise ContractError(f"loads table has NaN in {col}")
    hour_raw = pd.to_numeric(out["hour"], errors="coerce")
    bad_hour = hour_raw.isna() | (hour_raw % 1 != 0)
    if bad_hour.any():
        raise ContractError(
            f"loads table hour must be integral, got "
            f"{out.loc[bad_hour, 'hour'].unique().tolist()}"
        )

    try:
        out = out.astype(LOADS_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"loads table dtype coercion failed: {exc}") from exc

    for col in ("p_mw", "q_mvar"):
        if np.isinf(out[col]).any():
            raise ContractError(f"loads table has non-finite inf in {col}")
    negative = out["p_mw"] < 0.0
    if negative.any():
        raise ContractError(
            f"loads table has negative p_mw at bus(es): "
            f"{out.loc[negative, 'bus'].tolist()}"
        )
    dup = out.duplicated(subset=["bus", "hour"])
    if dup.any():
        raise ContractError(
            f"loads table has duplicate (bus, hour) rows: {out.loc[dup, 'bus'].tolist()}"
        )
    return out
