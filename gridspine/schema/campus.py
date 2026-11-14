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
* ``tie_open`` (optional) is True in an hour when the solve left nothing at
  the PCC able to carry power then (``producers.campus.pcc_tie_open``):
  there is no islanding event to judge in that hour. A table without the
  column, or a cell that is not a clear true or false, reads as closed, so
  the hour is judged as it always was (fail closed).

pandas only, like every other contract in ``schema/``.
"""
import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError

HOURLY_COLUMNS = ("unit_id", "period", "hour", "p_mw", "status")
PCC_COLUMNS = ("period", "hour", "weight", "import_mw")
#: Optional ``campus_pcc.csv`` column: the solved tie state per hour.
PCC_TIE_OPEN = "tie_open"
HOURLY_CSV = "campus_hourly.csv"
PCC_CSV = "campus_pcc.csv"

#: Preferred transformer ratings in MVA (R10 series).
STANDARD_MVA = (0.1, 0.16, 0.25, 0.4, 0.63, 1.0, 1.6, 2.5, 4.0, 6.3, 10.0, 16.0, 20.0, 25.0,
                31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0, 250.0, 315.0,
                400.0, 500.0, 630.0, 800.0, 1000.0)

#: MW tolerance for "carries no power".
P_TOL_MW = 1e-6

#: The PCC exchange (MW) a normally open tie may show in any hour: the
#: solver's noise, not power through the tie. Also the most a PCC Link may
#: be able to carry in an hour (solved rating × per-unit limit) and still
#: count as open then (``tie_open``).
TIE_OPEN_TOL_MW = 0.01

_TRUE = frozenset({"true", "1", "1.0"})


def _tie_flag(v) -> bool:
    """One ``tie_open`` cell: True only for a clear true; anything else
    (false, empty, NaN, junk) is closed (fail closed)."""
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    return str(v).strip().lower() in _TRUE


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
    out[PCC_TIE_OPEN] = (df[PCC_TIE_OPEN].map(_tie_flag).astype(bool) if PCC_TIE_OPEN in df.columns
                         else pd.Series(False, index=out.index, dtype=bool))
    if (out["weight"] <= 0).any():
        raise ContractError(f"{where}: weight must be positive")
    dup = out.duplicated(subset=["period", "hour"])
    if dup.any():
        raise ContractError(f"{where}: duplicate (period, hour) rows")
    return out
