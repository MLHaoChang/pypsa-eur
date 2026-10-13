"""The connection-point assessment table (increment 10): one row per check.

``status`` is ``pass`` / ``fail`` against the grid-code profile's limit, or
``reported`` for a figure the code does not gate (SCR). ``value`` is in
``unit``; ``limit`` is the threshold the value was held to (empty when the
check is a rule, not a number). ``clause`` and ``source`` say which rule was
applied and how sure it is: ``code`` for a number the regulation states,
``assumed`` for an engineering choice.

pandas only, like every other contract in ``schema/``.
"""
import pandas as pd

from gridspine.schema.contracts import ContractError

CHECKS = (
    "connection", "energisation", "load_trip", "facility_trip",
    "q_lead", "q_lag", "scr_onsite", "scr_load",
)
STATUSES = ("pass", "fail", "reported")
SOURCES = ("code", "assumed")
CONNECTION_COLUMNS = ("check", "status", "value", "unit", "limit", "detail", "clause", "source")


def validate_connection(df: pd.DataFrame, key=("check",)) -> pd.DataFrame:
    missing = [c for c in CONNECTION_COLUMNS if c not in df.columns]
    if missing:
        raise ContractError(f"connection table missing columns: {missing}")
    out = df.copy()
    for col, allowed in (("check", CHECKS), ("status", STATUSES), ("source", SOURCES)):
        bad = ~out[col].isin(allowed)
        if bad.any():
            raise ContractError(f"{col} must be one of {list(allowed)}, got {out.loc[bad, col].unique().tolist()}")
    for col in ("value", "limit"):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    if out["clause"].isna().any() or (out["clause"].astype(str).str.strip() == "").any():
        raise ContractError("every connection check must name the clause it applied")
    dup = out.duplicated(subset=list(key))
    if dup.any():
        raise ContractError(f"duplicate connection rows: {out.loc[dup, list(key)].values.tolist()}")
    return out.reset_index(drop=True)
