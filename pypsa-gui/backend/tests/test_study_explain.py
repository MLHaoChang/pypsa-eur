"""
S6: `explain_investment` lifted into `services/study/explain.py` (plan S6
"explain"), so the guided flow can explain an option's battery and PV from
an explicit network, without the copilot.

The lift is pinned BYTE-IDENTICAL: `tests/golden/explain_investment_recorded.json`
was recorded from `chat_tools.explain_investment` BEFORE the lift (set
``RECORD_EXPLAIN=1`` to re-record; do not, unless the payload is meant to
change) and every case must serialise to exactly those bytes after it.
"""
from __future__ import annotations

import json
import os
import pathlib

import pandas as pd
import pypsa
import pytest

from services import chat_tools as T
from tests.test_chat_explain_investment import _sizing_network

RECORDED = pathlib.Path(__file__).resolve().parent / "golden" / "explain_investment_recorded.json"


def _corridor() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "cheap")
    n.add("Bus", "demand")
    n.add("Line", "corridor", bus0="cheap", bus1="demand", x=0.1, r=0.01, s_nom=20.0)
    n.add("Load", "L1", bus="demand", p_set=100.0)
    n.add("Generator", "cheap_gen", bus="cheap", p_nom_extendable=True,
          capital_cost=1_000.0, marginal_cost=1.0)
    n.add("Generator", "local_gen", bus="demand", p_nom_extendable=True,
          capital_cost=5_000.0, marginal_cost=90.0)
    n.optimize(solver_name="highs", assign_all_duals=True)
    return n


def _storage() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=[50.0, 50.0, 150.0, 150.0, 50.0, 50.0])
    n.add("Generator", "base", bus="B1", p_nom=120.0, marginal_cost=20.0)
    n.add("Generator", "peaker", bus="B1", p_nom=100.0, marginal_cost=200.0)
    n.add("StorageUnit", "bat", bus="B1", p_nom_extendable=True, max_hours=2.0,
          capital_cost=5.0, efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True, p_nom_max=40.0)
    n.optimize(solver_name="highs")
    return n


CASES = {
    "sizing": (lambda: _sizing_network(), [("Generator", g) for g in
                                             ("wind1", "gas1", "nuclear1", "diesel")]),
    "co2": (lambda: _sizing_network(co2_cap=50.0), [("Generator", "gas1")]),
    "unsolved": (lambda: _sizing_network(solve=False), [("Generator", "gas1")]),
    "corridor": (_corridor, [("Generator", "cheap_gen")]),
    "storage": (_storage, [("StorageUnit", "bat")]),
}


def _serialise(out: dict) -> str:
    return json.dumps(out, sort_keys=True, default=str, indent=1)


def _outputs(install_network) -> dict[str, str]:
    got: dict[str, str] = {}
    for case, (build, assets) in CASES.items():
        install_network(build())
        for cls, name in assets:
            got[f"{case}:{cls}:{name}"] = _serialise(T.explain_investment(cls, name))
    return got


def test_explain_investment_is_byte_identical_to_the_recording(install_network):
    got = _outputs(install_network)
    if os.environ.get("RECORD_EXPLAIN") == "1":
        RECORDED.write_text(json.dumps(got, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        pytest.skip("recorded")
    want = json.loads(RECORDED.read_text(encoding="utf-8"))
    assert sorted(got) == sorted(want)
    for key in want:
        assert got[key] == want[key], key
