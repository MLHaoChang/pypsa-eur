"""The campus hourly tables (plan C2).

``campus_hourly.csv`` has one row per ``(unit_id, period, hour)``:

* ``p_mw`` is the power injected at the unit's bus. A load or a charging
  battery is negative.
* ``status`` is 1 if the asset exists in that investment period, else 0.
  A unit with status 0 carries no power.

``campus_pcc.csv`` has one row per ``(period, hour)``:

* ``weight`` is the hour's snapshot weighting, the hours of the year it
  stands for.
* ``import_mw`` is the capacity-expansion model's own net import at the
  PCC. It is negative when the campus exports. The AC load flow computes
  the real figure, losses included; this one ranks the hours (plan C3).

pandas only, like every other contract in ``schema/``.
"""
import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError

HOURLY_COLUMNS = ("unit_id", "period", "hour", "p_mw", "status")
PCC_COLUMNS = ("period", "hour", "weight", "import_mw")
HOURLY_CSV = "campus_hourly.csv"
PCC_CSV = "campus_pcc.csv"

#: MW tolerance for "carries no power".
P_TOL_MW = 1e-6


def _integral(out, col, where):
    v = pd.to_numeric(out[col], errors="coerce")
    if v.isna().any() or (v % 1 != 0).any():
        raise ContractError(f"{where}: {col} must be integral")
    out[col] = v.astype("int64")


def _finite(out, col, where):
    v = pd.to_numeric(out[col], errors="coerce")
    if not np.isfinite(v).all():
        raise ContractError(f"{where}: {col} must be finite")
    out[col] = v.astype("float64")


def validate_hourly(df: pd.DataFrame) -> pd.DataFrame:
    where = "campus hourly table"
    missing = [c for c in HOURLY_COLUMNS if c not in df.columns]
    if missing:
        raise ContractError(f"{where} missing columns: {missing}")
    out = df[list(HOURLY_COLUMNS)].copy()
    if out["unit_id"].isna().any():
        raise ContractError(f"{where}: null unit_id")
    out["unit_id"] = out["unit_id"].astype(str)
    for col in ("period", "hour", "status"):
        _integral(out, col, where)
    _finite(out, "p_mw", where)
    if (out["hour"] < 0).any():
        raise ContractError(f"{where}: hour must be non-negative")
    bad = ~out["status"].isin((0, 1))
    if bad.any():
        raise ContractError(f"{where}: status must be 0 or 1, got {sorted(out.loc[bad, 'status'].unique())}")
    off = (out["status"] == 0) & (out["p_mw"].abs() > P_TOL_MW)
    if off.any():
        raise ContractError(f"{where}: units with status 0 carry power: {sorted(out.loc[off, 'unit_id'].unique())}")
    dup = out.duplicated(subset=["unit_id", "period", "hour"])
    if dup.any():
        raise ContractError(f"{where}: duplicate (unit_id, period, hour) rows: {out.loc[dup, ['unit_id', 'period', 'hour']].values.tolist()[:5]}")
    return out


def validate_pcc(df: pd.DataFrame) -> pd.DataFrame:
    where = "campus PCC table"
    missing = [c for c in PCC_COLUMNS if c not in df.columns]
    if missing:
        raise ContractError(f"{where} missing columns: {missing}")
    out = df[list(PCC_COLUMNS)].copy()
    for col in ("period", "hour"):
        _integral(out, col, where)
    for col in ("weight", "import_mw"):
        _finite(out, col, where)
    if (out["weight"] <= 0).any():
        raise ContractError(f"{where}: weight must be positive")
    dup = out.duplicated(subset=["period", "hour"])
    if dup.any():
        raise ContractError(f"{where}: duplicate (period, hour) rows")
    return out
