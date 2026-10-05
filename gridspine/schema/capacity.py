"""The connection-capacity table (increment 9): one row per (bus, hour, kind).

``capacity_mw`` is the AC answer, the most MW that creates no new violation and
worsens no existing one, intact and under N-1 (``static/capacity.py``). It is
empty where only the DC screen ran (``method == "dc"``). ``dc_estimate_mw`` is
the closed-form DC answer, kept beside the AC one so the gap stays visible.
``binding_kind`` names what stopped it, and ``binding_preexisting`` says whether
that constraint was ALREADY violated before anything connected. Such a limit
is "blocked by an existing overload": its MW figure is the worsening tolerance
over the bus's distribution factor, not room on the network, and must not be
read as headroom. ``none_up_to_cap`` means nothing bound
up to the search cap, so ``capacity_mw`` is that cap and must be read as "at
least", never as infinity.

pandas only, like every other contract in ``schema/``.
"""
import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError

KINDS = ("load", "generation")
#: The table's file name, in the run directory and in every bundle.
CAPACITY_CSV = "capacity.csv"
METHODS = ("ac", "dc")
BINDING_KINDS = (
    "thermal_intact", "thermal_n1",
    "v_low_intact", "v_high_intact", "v_low_n1", "v_high_n1",
    "n1_divergence", "ac_divergence", "none_up_to_cap",
)
CAPACITY_COLUMNS = (
    "bus", "hour", "kind", "capacity_mw", "dc_estimate_mw",
    "binding_kind", "binding_element", "binding_contingency", "binding_preexisting", "method",
)


def _one_of(series, allowed, col):
    bad = ~series.isin(allowed)
    if bad.any():
        raise ContractError(
            f"{col} must be one of {sorted(allowed)}, got {series[bad].unique().tolist()}"
        )


def validate_capacity(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in CAPACITY_COLUMNS if c not in df.columns]
    if missing:
        raise ContractError(f"capacity table missing columns: {missing}")
    out = df[list(CAPACITY_COLUMNS)].copy()

    _one_of(out["kind"], KINDS, "kind")
    _one_of(out["method"], METHODS, "method")
    _one_of(out["binding_kind"], BINDING_KINDS, "binding_kind")

    try:
        out["hour"] = out["hour"].astype("int64")
        for col in ("capacity_mw", "dc_estimate_mw"):
            out[col] = pd.to_numeric(out[col], errors="raise").astype("float64")
    except (ValueError, TypeError) as exc:
        raise ContractError(f"capacity table dtype coercion failed: {exc}") from exc
    if (out["hour"] < 0).any():
        raise ContractError(f"hour must be >= 0, got {out.loc[out['hour'] < 0, 'hour'].tolist()}")

    for col in ("capacity_mw", "dc_estimate_mw"):
        vals = out[col]
        bad = np.isinf(vals) | (vals < 0)
        if bad.any():
            raise ContractError(f"{col} must be finite and >= 0, got {vals[bad].tolist()}")
    ac = out["method"] == "ac"
    if out.loc[ac, "capacity_mw"].isna().any():
        raise ContractError("capacity_mw is required on every row the AC search produced (method 'ac')")

    pre = out["binding_preexisting"].map(
        lambda v: v if isinstance(v, (bool, np.bool_)) else {"True": True, "False": False}.get(v, v)
    )
    bad = ~pre.map(lambda v: isinstance(v, (bool, np.bool_)))
    if bad.any():
        raise ContractError(
            f"binding_preexisting must be a bool, got {out.loc[bad, 'binding_preexisting'].unique().tolist()}"
        )
    out["binding_preexisting"] = pre.astype(bool)

    for col in ("binding_element", "binding_contingency"):
        out[col] = out[col].astype(object).where(out[col].notna(), None)
    dup = out.duplicated(subset=["bus", "hour", "kind"])
    if dup.any():
        raise ContractError(f"duplicate (bus, hour, kind) rows: {out.loc[dup, ['bus', 'hour', 'kind']].values.tolist()}")
    return out.reset_index(drop=True)
