# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Pin of the hub run files (DC review 09, GK A ask 7).

The ratings work adds ``campus_lf_line.csv`` to a sized run and must leave
every other file of prepare -> rank -> size -> invest as master wrote it
before the change. The literal hashes below were computed on master
(a812e21e) with :func:`run_hashes`, in a fresh worktree, with and without a
cable. They hash the content, not the bytes: floats are written to six
significant digits, so a solver's last-digit noise between platforms does
not move them, and a changed figure does. ``campus_manifest.json`` is
pinned without ``network_path`` and ``network_sha256``, which name the
temporary network file of each run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

#: Files a run gains with the ratings work; everything else is pinned.
NEW_FILES = {"campus_lf_line.csv"}
_VOLATILE = {"campus_manifest.json": ("network_path", "network_sha256")}


def _canon(x):
    if isinstance(x, float):
        return float(f"{x:.6g}")
    if isinstance(x, dict):
        return {str(k): _canon(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_canon(v) for v in x]
    return x


def content_hash(path: Path) -> str:
    if path.suffix == ".csv":
        df = pd.read_csv(path)
        text = df.to_csv(index=False, float_format="%.6g")
    elif path.suffix in (".json", ".yaml"):
        data = (json.loads if path.suffix == ".json" else yaml.safe_load)(
            path.read_text()
        )
        for key in _VOLATILE.get(path.name, ()):
            data.pop(key, None)
        text = json.dumps(_canon(data), sort_keys=True)
    else:
        raise AssertionError(f"no content hash for {path.name}")
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _v(x):
    return {"value": x, "source": "assumed"}


def run_hashes(tmp: Path, cabled: bool) -> dict[str, str]:
    from gridspine.drivers.campus_study import (
        draft_from_project,
        invest_campus,
        prepare_campus,
        rank_campus,
        size_campus,
    )
    from tests.gridspine.test_campus_hourly import solved_hub

    nc = tmp / "n.nc"
    solved_hub(periods=[2030, 2040]).export_to_netcdf(str(nc))
    spec = draft_from_project(nc).spec
    if cabled:
        c = spec["campus"]
        c["buses"]["MV2"] = {"vn_kv": 20.0}
        c["cables"] = {
            "CB1": {
                "from_bus": "MV",
                "to_bus": "MV2",
                "length_km": _v(1.5),
                "r_ohm_per_km": _v(0.125),
                "x_ohm_per_km": _v(0.11),
                "c_nf_per_km": _v(300.0),
                "max_i_ka": _v(0.4),
                "parallel": 3,
            }
        }
        c["units"]["DC_LOAD"]["bus"] = "MV2"
    run = tmp / "run"
    prepare_campus(run, spec, nc)
    rank_campus(run, k=1)
    size_campus(run)
    invest_campus(run)
    return {f.name: content_hash(f) for f in sorted(run.iterdir())}


MASTER = {
    False: {
        "campus.yaml": "84e15adf89828cd6",
        "campus_compliance.csv": "2006136913f6a342",
        "campus_compliance_invested.csv": "742272a89a67bfa2",
        "campus_cost.csv": "5f7df15c0995d3bc",
        "campus_hourly.csv": "2e8f3414f964249e",
        "campus_invest_dispatch.csv": "5ea1fb1f86adf380",
        "campus_invest_history.csv": "c6078fd66c944338",
        "campus_invest_scope.json": "0d25e60122b9e513",
        "campus_invest_taps.csv": "52b585d00d11e42b",
        "campus_invested.yaml": "6f5a2243bebf5f3b",
        "campus_investment.csv": "9b693e1f4e16191a",
        "campus_lf_bus.csv": "7c0eb2516fdb088c",
        "campus_lf_pcc.csv": "345ce9e0e0fbd3ed",
        "campus_lf_trafo.csv": "a585efe1f5e212c9",
        "campus_manifest.json": "c1a9d2322ea9626d",
        "campus_metrics.csv": "df51313862dbfe8e",
        "campus_pcc.csv": "26c86d86f697296d",
        "campus_reactive.csv": "6afc0fe26106f784",
        "campus_reported_requirements.json": "700196ca79a9dfae",
        "campus_requirement.json": "54c5cf021141b40d",
        "campus_selected.csv": "7c6bd957dc229506",
        "campus_short_circuit.csv": "aeeadb05e159dd55",
        "campus_sizing_compensation.csv": "3090c7f548810417",
        "campus_sizing_trafo.csv": "bf0e05bf9c738699",
    },
    True: {
        "campus.yaml": "d688948fdd1b5f6c",
        "campus_compliance.csv": "8e3af150aea9bac0",
        "campus_compliance_invested.csv": "81ca66e3a70f31f0",
        "campus_cost.csv": "346e7bc713cbbcf0",
        "campus_hourly.csv": "2e8f3414f964249e",
        "campus_invest_dispatch.csv": "5ea1fb1f86adf380",
        "campus_invest_history.csv": "c6078fd66c944338",
        "campus_invest_scope.json": "0d25e60122b9e513",
        "campus_invest_taps.csv": "52b585d00d11e42b",
        "campus_invested.yaml": "0cb841bf1d3dbc79",
        "campus_investment.csv": "4ac4c5344a1f1a95",
        "campus_lf_bus.csv": "ce1207c3a95d8e65",
        "campus_lf_pcc.csv": "da15b90d95dc3eef",
        "campus_lf_trafo.csv": "1a73b808b72eb918",
        "campus_manifest.json": "df64bfbccb9a9347",
        "campus_metrics.csv": "df51313862dbfe8e",
        "campus_pcc.csv": "26c86d86f697296d",
        "campus_reactive.csv": "31329d893aacfc34",
        "campus_reported_requirements.json": "700196ca79a9dfae",
        "campus_requirement.json": "54c5cf021141b40d",
        "campus_selected.csv": "7c6bd957dc229506",
        "campus_short_circuit.csv": "0c2700246a188390",
        "campus_sizing_compensation.csv": "3090c7f548810417",
        "campus_sizing_trafo.csv": "2fca442a6fa7d81a",
    },
}


@pytest.mark.parametrize("cabled", [False, True], ids=["hub", "hub_with_cable"])
def test_the_run_files_are_as_master_wrote_them(tmp_path, cabled):
    got = run_hashes(tmp_path, cabled)
    assert NEW_FILES <= set(got)
    assert {k: v for k, v in got.items() if k not in NEW_FILES} == MASTER[cabled]
