# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
The Excel workbook's golden round trip (GK B T5, ticket 04): each fixture network
written by `write_workbook` and read back by `read_excel`, kept as exact text, so a
change to the reader can be checked byte for byte against the reader that wrote the
golden.

Regenerate on the commit you compare against (the PR body names it):

    python -m tests.gridspine.excel_golden

It writes `excel_golden.json` beside this file, with each network's input
fingerprint when the app backend is importable.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import warnings
from typing import Any

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
GOLDEN = HERE / "excel_golden.json"

#: Values added to the hard set for ticket 04 (T2): signed zeros and a subnormal.
EXTRA = [0.0, -0.0, 5e-324]


def _wb():
    """The round-trip tests' module, whose fixtures and catalogue the golden uses."""
    try:
        from tests.gridspine import test_excel_workbook as T
    except ImportError:  # from the app backend, whose `tests` is its own package
        sys.path.insert(0, str(HERE))
        import test_excel_workbook as T
    return T


def cases() -> dict[str, tuple[Any, dict]]:
    """Each fixture: (network, sidecars written with it)."""
    T = _wb()
    out: dict[str, tuple[Any, dict]] = {}
    for multi in (False, True):
        n = T._network(multi)
        solver = (
            T.SOLVER
            if multi
            else {
                **T.SOLVER,
                "multi_investment_periods": False,
                "investment_periods": [],
            }
        )
        out[f"network-multi={multi}"] = (
            n,
            {"user_ts.json": T._user_ts(n), "solver_config.json": solver},
        )
    for v in [*T.HARD, *EXTRA]:
        solver = {
            "discount_rate": v,
            "multi_investment_periods": False,
            "investment_periods": [],
        }
        out[f"hard={float(v).hex()}"] = (
            T._hard_network(v),
            {"solver_config.json": solver},
        )
    return out


def read_back(network, sidecars, directory: pathlib.Path):
    """The fixture written and read again, as the round-trip tests do it."""
    T = _wb()
    return T._read(T._write(network, directory, sidecars=sidecars))


def _cell(v: Any) -> str:
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        return "f:" + v.hex()
    return f"{type(v).__name__}:{v!r}"


def _frame(df: pd.DataFrame | pd.Series) -> dict:
    if isinstance(df, pd.Series):
        df = df.to_frame()
    return {
        "index": [_cell(x) if not isinstance(x, tuple) else repr(x) for x in df.index],
        "index_names": [repr(x) for x in df.index.names],
        "columns": [repr(c) for c in df.columns],
        "dtypes": [str(t) for t in df.dtypes],
        "values": [[_cell(v) for v in row] for row in df.itertuples(index=False)],
    }


def snapshot(res) -> dict:
    """Every static and time-series frame, the snapshots and the sidecars, exactly."""
    T = _wb()
    n = res.network
    frames: dict[str, dict] = {}
    for _, _, ln in T.L.SHEETS:
        frames[ln] = _frame(getattr(n, ln))
        for attr, df in getattr(n, f"{ln}_t").items():
            if not df.empty:
                frames[f"{ln}_t.{attr}"] = _frame(df)
    frames["snapshot_weightings"] = _frame(n.snapshot_weightings)
    frames["investment_period_weightings"] = _frame(n.investment_period_weightings)
    return {
        "snapshots": [repr(s) for s in n.snapshots],
        "frames": frames,
        "sidecars": json.loads(json.dumps(res.sidecars, sort_keys=True, default=repr)),
        "sidecar_floats": _sidecar_floats(res.sidecars),
    }


def _sidecar_floats(obj: Any, path: str = "") -> dict[str, str]:
    """Each float in the sidecars by path, as float.hex: JSON alone loses -0.0."""
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_sidecar_floats(v, f"{path}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_sidecar_floats(v, f"{path}/{i}"))
    elif isinstance(obj, float):
        out[path] = obj.hex()
    return out


def fingerprint(network) -> str | None:
    """`_network_input_fingerprint` when the app backend is importable, else None."""
    backend = HERE.parents[1] / "pypsa-gui" / "backend"
    sys.path.insert(0, str(backend))
    try:
        from services.project_context import ProjectContext
        from services.workflow_tools import _network_input_fingerprint
    except ImportError:
        return None
    return _network_input_fingerprint(ProjectContext(network))


def main() -> None:
    warnings.filterwarnings("ignore")
    golden: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as d:
        for i, (name, (n, sidecars)) in enumerate(cases().items()):
            sub = pathlib.Path(d) / str(i)
            sub.mkdir()
            res = read_back(n, sidecars, sub)
            golden[name] = {**snapshot(res), "fingerprint": fingerprint(res.network)}
    GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN.name}: {len(golden)} cases")


if __name__ == "__main__":
    main()
