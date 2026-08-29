"""Canonical-ID rules. Names are the keys that cross every stage boundary:
PyPSA bus name == pandapower bus name == .raw NAME == PowerFactory name.
The 12-char cap is the PSS/E v33 NAME field width — enforced here so an
illegal ID fails at ingest, not at export."""
import re

import pandas as pd

from .contracts import ContractError

MAX_NAME_LEN = 12

# Names are emitted unescaped into the .raw record stream and into CSVs, so the
# charset is an allowlist, not a denylist: a quote or comma splits a .raw
# record, a newline forges one, and a leading "=" makes the cell a spreadsheet
# formula. Widening this set is a decision, not a convenience.
NAME_CHARSET = re.compile(r"[A-Za-z0-9_-]+")


def _check_series(s: pd.Series, what: str) -> None:
    if s.isna().any():
        raise ContractError(f"{what} contains null names")
    if not all(isinstance(v, str) for v in s):
        raise ContractError(f"{what} contains non-string names")
    if s.duplicated().any():
        raise ContractError(f"{what} contains duplicate names: {sorted(s[s.duplicated()].unique())}")
    too_long = s[s.str.len() > MAX_NAME_LEN]
    if len(too_long):
        raise ContractError(
            f"{what} names exceed {MAX_NAME_LEN} chars (PSS/E v33 NAME limit): {sorted(too_long)}"
        )
    if (s.str.len() == 0).any():
        raise ContractError(f"{what} contains empty names")
    offenders = sorted(s[~s.str.fullmatch(NAME_CHARSET)])
    if offenders:
        raise ContractError(
            f"{what} names contain characters outside [A-Za-z0-9_-]: {offenders}"
        )


def validate_canonical(buses: pd.Series, unit_names: pd.Series) -> None:
    _check_series(buses.reset_index(drop=True), "buses")
    _check_series(unit_names.reset_index(drop=True), "units")


def unit_registry(gen_names, gen_buses, ext_names, ext_buses) -> pd.DataFrame:
    gens = pd.DataFrame({"unit_id": list(gen_names), "bus": list(gen_buses), "kind": "gen"})
    exts = pd.DataFrame({"unit_id": list(ext_names), "bus": list(ext_buses), "kind": "ext_grid"})
    reg = pd.concat([gens, exts], ignore_index=True).set_index("unit_id")
    if reg.index.duplicated().any():
        raise ContractError(f"duplicate unit ids: {sorted(reg.index[reg.index.duplicated()])}")
    return reg
