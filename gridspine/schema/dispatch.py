"""The stage-1 -> stage-2 contract: per-unit, per-hour dispatch keyed by
canonical unit_id. PyPSA is one producer of this table; client-supplied
snapshots are another. Downstream stages never see a PyPSA object."""
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
    for col in ("p_mw", "q_mvar"):
        if out[col].isna().any():
            raise ContractError(f"dispatch table has NaN in {col}")
    try:
        out = out.astype(DISPATCH_COLUMNS)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"dispatch table dtype coercion failed: {exc}") from exc
    if not out["status"].isin([0, 1]).all():
        bad = sorted(out.loc[~out["status"].isin([0, 1]), "status"].unique())
        raise ContractError(f"status must be 0/1, got {bad}")
    dup = out.duplicated(subset=["unit_id", "hour"])
    if dup.any():
        raise ContractError(f"duplicate (unit_id, hour) rows: {out.loc[dup, 'unit_id'].tolist()}")
    offline_producing = (out["status"] == 0) & (out["p_mw"].abs() > _P_OFFLINE_TOL_MW)
    if offline_producing.any():
        raise ContractError(
            f"units with status 0 but nonzero p_mw: {out.loc[offline_producing, 'unit_id'].tolist()}"
        )
    return out
