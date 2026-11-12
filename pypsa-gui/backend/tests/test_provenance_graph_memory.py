"""
provenance-graph 07 (MEMORY-1, COPILOT-1): a value the user accepted from a memory item links to that item.

The edge is built from the stamp `memory_parameters.apply` writes, so it exists only after acceptance; retiring or
editing the item leaves the value's stamp (and so its verdict) as it was; the verdict reads `from_memory`, never
`fresh`.
"""

from __future__ import annotations

import json

import pypsa
import pytest

from services import provenance_graph as G
from services.asset_schema import provenance as P
from services.provenance_graph import memory as MEM
from services.provenance_graph import money
from services.provenance_graph.explain import explain

ITEM = "mem_3f2a9c"
SRC = f"memory:{ITEM}@v2"
FIELD = money.input_id("Generator", "g", "marginal_cost")


def _net(source=SRC, *, approved=True, field="marginal_cost"):
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b", p_nom=1.0)
    P.record(
        n,
        "Generator",
        "g",
        field,
        status="customised",
        source=source,
        source_year=2024,
        currency_year=2024,
        approved_by="a@example.com" if approved else None,
        approved_at="2026-11-12T10:00:00+00:00" if approved else None,
        replaces="pack:eu-2023",
    )
    return n


def _graph(n):
    return G.money_graph(n, [("Generator", "g")])


def test_an_accepted_value_links_to_its_memory_item_with_who_and_when():
    g = _graph(_net())
    mid = f"memory:{ITEM}@v2"
    assert g.nodes[mid].kind == "memory"
    (edge,) = [e for e in g.edges if e.kind == "recalled_from"]
    assert (edge.src, edge.dst) == (FIELD, mid)
    assert edge.recorded == MEM.MemoryRecall(
        ITEM, 2, "a@example.com", "2026-11-12T10:00:00+00:00", "pack:eu-2023"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"approved": False},  # offered, never accepted (COPILOT-1): no approval stamp, no edge
        {"source": "memory:mem_x"},  # no version: not a memory stamp
        {"source": "memory:mem_x@v0"},
        {"source": "pack:eu-2023"},
    ],
)
def test_no_edge_without_an_accepted_memory_stamp(kwargs):
    g = _graph(_net(**kwargs))
    assert not [e for e in g.edges if e.kind == "recalled_from"]
    assert not [n for n in g.nodes.values() if n.kind == "memory"]


def test_the_verdict_is_from_memory_never_fresh():
    v = G.staleness(_graph(_net()), FIELD)
    assert v.state == "from_memory" and v.broken == ()


def test_why_names_the_memory_item_and_its_state():
    out = explain(_net(), "Generator", "g", "marginal_cost")
    assert out["outcome"]["state"] == "from_memory"
    first, last = out["steps"][0], out["steps"][-1]
    assert first["state"] == "from_memory"
    assert last["node"] == f"memory:{ITEM}@v2" and last["kind"] == "memory"
    assert last["via"]["edge"] == "recalled_from"
    assert last["via"]["recorded"]["approved_by"] == "a@example.com"
    assert last["memory"]["state"] == "from memory v2, user-approved"
    assert last["memory"]["item"] is None  # no reader given: the id and version come from the stamp
    json.dumps(out)


def test_why_adds_the_item_the_reader_may_see():
    seen = []

    def resolve(item, version):
        seen.append((item, version))
        return {"summary": "7% used before", "evidence": [{"kind": "project"}]}

    out = explain(_net(), "Generator", "g", "marginal_cost", resolve)
    assert seen == [(ITEM, 2)]
    assert out["steps"][-1]["memory"]["item"]["summary"] == "7% used before"


def test_an_unreadable_item_still_names_its_id():
    def resolve(item, version):
        raise ValueError("not found")

    out = explain(_net(), "Generator", "g", "marginal_cost", resolve)
    assert out["steps"][-1]["memory"] == {
        "id": f"memory:{ITEM}@v2",
        "state": "from memory v2, user-approved",
        "item": None,
    }


def test_retiring_or_editing_the_item_does_not_change_the_value_or_its_verdict():
    # Versions are immutable and the edge compares nothing, so the verdict reads only the value's own stamp: an
    # item that cannot be resolved any more (retired, scope lost) leaves state and stamp as they were.
    n = _net()

    def gone(item, version):
        raise LookupError("retired")

    def edited(item, version):
        return {"summary": "now says 9%"}

    outs = [
        explain(n, "Generator", "g", "marginal_cost", r) for r in (None, gone, edited)
    ]
    for o in outs:
        assert o["outcome"]["state"] == "from_memory"
        assert o["steps"][0]["stamp"] == outs[0]["steps"][0]["stamp"]
        assert o["steps"][0]["state"] == "from_memory"


def test_the_route_resolves_the_item_for_the_signed_in_user(
    client, install_network, monkeypatch
):
    from services import memory_store

    install_network(_net())
    monkeypatch.setattr(
        memory_store, "node", lambda db, user, item, version: {"summary": f"{item}@{version}"}
    )
    r = client.get("/api/network/asset_schema/Generator/provenance/g/why/marginal_cost")
    assert r.status_code == 200, r.text
    step = r.json()["steps"][-1]
    assert step["memory"]["state"] == "from memory v2, user-approved"
    assert step["memory"]["item"] == {"summary": f"{ITEM}@2"}


def test_the_graph_writes_nothing():
    n = _net()
    snap = json.dumps(n.meta, sort_keys=True, default=str)
    explain(n, "Generator", "g", "marginal_cost")
    assert json.dumps(n.meta, sort_keys=True, default=str) == snap
