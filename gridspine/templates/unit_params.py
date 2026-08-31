"""Dynamic-parameter templates as data. Every value is tagged with its
provenance — this file format IS the assumptions ledger's unit section."""
from pathlib import Path

import pandas as pd
import yaml

from gridspine.schema.contracts import ContractError

_DEFAULT = Path(__file__).parent / "data" / "case39_units.yaml"
_SOURCES = frozenset({"measured", "datasheet", "assumed"})
_REQUIRED = ("h_s", "mbase_mva", "source", "include_in_inertia")


def load_unit_params(path=None) -> pd.DataFrame:
    raw = yaml.safe_load(Path(path or _DEFAULT).read_text())
    units = raw.get("units")
    if not isinstance(units, dict) or not units:
        raise ContractError("unit params YAML has no 'units' mapping")
    df = pd.DataFrame.from_dict(units, orient="index")
    df.index.name = "unit_id"
    missing = [c for c in _REQUIRED if c not in df.columns or df[c].isna().any()]
    if missing:
        raise ContractError(f"unit params missing/null columns: {missing}")
    bad_src = df.loc[~df["source"].isin(_SOURCES), "source"]
    if len(bad_src):
        raise ContractError(f"unknown source tags {sorted(set(bad_src))}; allowed {sorted(_SOURCES)}")
    counted = df[df["include_in_inertia"].astype(bool)]
    if ((counted["h_s"] <= 0) | (counted["mbase_mva"] <= 0)).any():
        raise ContractError("h_s and mbase_mva must be positive for inertia-counted units")
    if df.index.duplicated().any():
        raise ContractError(f"duplicate unit ids: {sorted(df.index[df.index.duplicated()])}")
    return df
