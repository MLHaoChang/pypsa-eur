"""
provenance-graph 06, P4: the money and commercial verdicts are byte-equal before and after the slice's change to the
dst walk. `tests/data/provenance_graph_sweep.json` was written on master `432655af` (before slice 06) by running this
file with `WRITE_SWEEP=1`; the test compares the same dump with it. Verdict, broken edges, uncompared names and the
`why` walk of every money field, annualised cost and commercial cell, over the money oracle's scenarios.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from services import provenance_graph as G
from services.provenance_graph import money
from tests.test_provenance_graph import _scenarios
from tests.test_provenance_graph_commercial import (
    PEAK_KEY,
    PPA_KEY,
    PTIER_KEY,
    TENOR_KEY,
    TIER_KEY,
    _config,
)

GOLDEN = Path(__file__).parent / "data" / "provenance_graph_sweep.json"


def _verdict(v) -> dict:
    return {
        "state": v.state,
        "uncompared": list(v.uncompared),
        "broken": [
            b.wire() | {"edge": b.edge, "src": b.src, "dst": b.dst} for b in v.broken
        ],
    }


def _walk(g, node) -> list:
    return [
        [s.node.id, s.node.kind, s.via.kind if s.via else None, s.state]
        for s in G.why(g, node).steps
    ]


def _dump(tmp_path, monkeypatch) -> dict:
    out: dict = {}
    for label, n, cls, name in _scenarios(tmp_path, monkeypatch):
        graph = G.money_graph(n, [(cls, name)])
        nodes = [
            money.derived_id(cls, name, f)
            for f in (*money.SCALED_FIELDS, money.ANNUALISED)
        ]
        nodes += [
            money.input_id(cls, name, f)
            for f in (*money.SCALED_FIELDS, money.ANNUALISED)
        ]
        out[label] = {
            node: {
                "verdict": _verdict(G.staleness(graph, node)),
                "walk": _walk(graph, node),
            }
            for node in nodes
        }
    config = _config()
    cells = {}
    for key in (
        PEAK_KEY,
        PTIER_KEY,
        TIER_KEY,
        PPA_KEY,
        TENOR_KEY,
        "tariff:none/x",
        "ppa:none/y",
    ):
        cells[key] = {
            "verdict": _verdict(G.cell_verdict(config, key)),
            "wire": G.cell_wire(config, key),
        }
        cells[key + "|no config"] = {"verdict": _verdict(G.cell_verdict(None, key))}
    out["commercial"] = cells
    return json.loads(json.dumps(out, default=str, sort_keys=True, allow_nan=False))


def test_money_and_commercial_verdicts_are_byte_equal_to_master(tmp_path, monkeypatch):
    now = _dump(tmp_path, monkeypatch)
    if os.environ.get("WRITE_SWEEP"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n")
    assert now == json.loads(GOLDEN.read_text())
