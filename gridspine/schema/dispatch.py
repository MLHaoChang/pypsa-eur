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


#: Largest accepted `hour`. An hour is a POSITION in the study, and 1e6 of them
#: is 114 years of hourly snapshots — the bound exists to refuse a value that is
#: plainly not an index, not to police how long a study may be. Two real client
#: mistakes land above it: a timestamp in epoch seconds (1.7e9) and the epoch
#: NANOSECONDS a datetime64 column turns into (1.7e18), the latter of which
#: `astype("int64")` used to accept and the former of which it still would.
_HOUR_MAX = 1_000_000


def _checked_hours(table: str, hour: pd.Series) -> None:
    """Refuse anything that is not a plausible snapshot index, BEFORE coercion.

    Three guards, each for a thing `astype("int64")` does quietly:

      * a datetime64/timedelta64 column. `pd.to_numeric` succeeds on one and
        returns epoch nanoseconds, every value integral, so the integrality
        guard below cannot see it. A client's timestamp column — `snapshot` and
        `timestep` are both aliases `producers.external` accepts, and both are
        what a market model calls it — became hours like
        1704067200000000000, and the study completed with `bundle_h…/`
        directories named after them.
      * a non-integral value: 1.5 truncates to 1.
      * a value outside the index range: 1e19 passes `% 1 == 0` and WRAPS to
        INT64_MIN, and a negative hour indexes nothing.
    """
    if pd.api.types.is_datetime64_any_dtype(hour) or pd.api.types.is_timedelta64_dtype(hour):
        raise ContractError(
            f"{table} hour must be an integer snapshot index, not a timestamp "
            f"(got dtype {hour.dtype}); convert the column to the study's hour "
            "positions before handing the table over"
        )
    numeric = pd.to_numeric(hour, errors="coerce")
    bad = numeric.isna() | (numeric % 1 != 0)
    if bad.any():
        raise ContractError(
            f"{table} hour must be integral, got {hour[bad].unique().tolist()}"
        )
    out_of_range = (numeric < 0) | (numeric > _HOUR_MAX)
    if out_of_range.any():
        raise ContractError(
            f"{table} hour must be a snapshot index in 0..{_HOUR_MAX}, got "
            f"{hour[out_of_range].unique().tolist()} — a timestamp (seconds or "
            "nanoseconds since the epoch) looks like this, and int64 coercion "
            "would wrap the larger ones rather than refuse them"
        )


def validate_dispatch(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(DISPATCH_COLUMNS) - set(df.columns)
    if missing:
        raise ContractError(f"dispatch table missing columns: {sorted(missing)}")
    # The contract's columns, in the contract's order — the way `validate_loads`
    # does it. Without the selection the stage-1 artifact's column order was
    # whatever the client's file had.
    out = df[list(DISPATCH_COLUMNS)].copy()

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
    _checked_hours("dispatch table", out["hour"])

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
    _checked_hours("loads table", out["hour"])

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
