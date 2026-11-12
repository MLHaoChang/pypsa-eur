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
        {
            "approved": False
        },  # offered, never accepted (COPILOT-1): no approval stamp, no edge
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
    assert (
        last["memory"]["item"] is None
    )  # no reader given: the id and version come from the stamp
    assert last["memory"]["note"] == MEM.NOT_READ
    json.dumps(out)


def test_why_adds_the_item_the_reader_may_see():
    seen = []

    def resolve(item, version):
        seen.append((item, version))
        return {"summary": "7% used before", "evidence": [{"kind": "project"}]}

    out = explain(_net(), "Generator", "g", "marginal_cost", resolve)
    assert seen == [(ITEM, 2)]
    assert out["steps"][-1]["memory"]["item"]["summary"] == "7% used before"
    assert out["steps"][-1]["memory"]["note"] is None


def test_an_unreadable_item_still_names_its_id():
    def resolve(item, version):
        raise ValueError("not found")

    out = explain(_net(), "Generator", "g", "marginal_cost", resolve)
    assert out["steps"][-1]["memory"] == {
        "id": f"memory:{ITEM}@v2",
        "state": "from memory v2, user-approved",
        "item": None,
        "note": "memory item could not be read",
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
        memory_store,
        "node",
        lambda db, user, item, version: {"summary": f"{item}@{version}"},
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


# ── GK B plan conditions P1–P9 ──────────────────────────────────────────────


def _entry(**over):
    e = {
        "source": SRC,
        "approved_by": "a@example.com",
        "approved_at": "2026-11-12T10:00:00+00:00",
    }
    return {**e, **over}


@pytest.mark.parametrize(
    "over",
    [
        {"source": "memory:x"},  # P1: no version
        {"source": "memory:x@vfoo"},
        {"source": "memory:x@v0"},
        {"source": "memory:x@v-1"},
        {"source": "memory:x@v1 "},  # trailing junk
        {"source": "memory:x@v1\n"},
        {"source": "xmemory:x@v1"},
        {"source": 5},  # a non-string source
        {"source": ["memory:x@v1"]},
        {"source": None},
        {"approved_by": None},  # one test per missing part
        {"approved_by": ""},
        {"approved_by": 7},
        {"approved_at": None},
        {"approved_at": ""},
        {"approved_at": 7},
    ],
)
def test_p1_a_malformed_stamp_builds_no_edge_and_never_raises(over):
    assert MEM.recall_of(_entry(**over)) is None
    entry = _entry(**over)
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b")
    n.meta.setdefault("asset_params", {})["Generator"] = {"g": {"marginal_cost": entry}}
    g = _graph(n)
    assert not [e for e in g.edges if e.kind == "recalled_from"]
    explain(n, "Generator", "g", "marginal_cost")  # no 500


def test_p1_only_a_well_formed_stamp_builds_the_recall():
    assert MEM.recall_of(_entry()) == MEM.MemoryRecall(
        ITEM, 2, "a@example.com", "2026-11-12T10:00:00+00:00"
    )


def test_p2_no_other_writer_forges_a_memory_source():
    # Every path that records an approved entry builds its source from something that cannot start with
    # `memory:` (an upload id, a PDF citation, a reference id); only `memory_parameters.apply` writes
    # `memory:<id>@v<n>`. A project file holding a forged stamp is the one way in, and it can only name an
    # item, never make the viewer see one: `why` resolves it through the viewer's own read (P4).
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    writers = {
        str(f.relative_to(root))
        for d in ("services", "routers", "harness")
        for f in (root / d).rglob("*.py")
        if re.search(r"[\"']memory:[{<]|[\"']memory:\{", f.read_text())
    }
    assert writers <= {
        "services/memory_parameters.py",
        "services/provenance_graph/memory.py",
    }, writers


def test_p3_a_later_edit_ends_the_recall():
    n = _net()
    assert any(e.kind == "recalled_from" for e in _graph(n).edges)
    P.mark_customised(
        n, "Generator", "g", ["marginal_cost"]
    )  # update_component's writer
    entry = P.get(n, "Generator", "g")["marginal_cost"]
    assert "approved_by" not in entry and "approved_at" not in entry
    g = _graph(n)
    assert not [e for e in g.edges if e.kind == "recalled_from"]
    assert G.staleness(g, FIELD).state != "from_memory"
    assert explain(n, "Generator", "g", "marginal_cost")["outcome"]["state"] != (
        "from_memory"
    )


def test_p4_a_viewer_who_may_not_read_the_item_sees_the_id_and_not_visible_to_you():
    def hidden(item, version):
        raise MEM.ItemNotVisible(item)

    step = explain(_net(), "Generator", "g", "marginal_cost", hidden)["steps"][-1]
    assert step["memory"] == {
        "id": f"memory:{ITEM}@v2",
        "state": "from memory v2, user-approved",
        "item": None,
        "note": "not visible to you",
    }


def test_p4_a_second_user_cannot_read_the_item_the_first_saved(
    client, _auth_db, seeded_identity, second_identity, monkeypatch
):
    from db.models import User
    from services import memory_store as M

    item = client.post(
        "/api/memory/items",
        json={
            "kind": "preference",
            "body": {"key": "units.power", "value": "GW"},
            "concept_keys": ["topic:units"],
        },
    ).json()
    with _auth_db[1]() as db:
        owner = db.get(User, seeded_identity["user_id"])
        other = db.get(User, second_identity["user_id"])
        assert M.node(db, owner, item["id"], 1)["summary"]
        with pytest.raises(M.MemoryError) as exc:
            M.node(db, other, item["id"], 1)
        assert exc.value.status == 404  # the route maps this to `not visible to you`


def test_p4_the_route_maps_a_404_to_not_visible_and_a_signed_out_viewer_too(
    client, install_network, monkeypatch
):
    from services import memory_store

    install_network(_net())

    def miss(db, user, item, version):
        raise memory_store.MemoryError("memory item not found", "memory_not_found", 404)

    monkeypatch.setattr(memory_store, "node", miss)
    r = client.get("/api/network/asset_schema/Generator/provenance/g/why/marginal_cost")
    assert r.json()["steps"][-1]["memory"]["note"] == "not visible to you"
    assert r.json()["outcome"]["state"] == "from_memory"


def test_p5_an_unresolvable_version_states_why_and_stays_from_memory():
    for exc in (RuntimeError("db down"), LookupError("x")):

        def bad(item, version, exc=exc):
            raise exc

        out = explain(_net(), "Generator", "g", "marginal_cost", bad)
        assert out["outcome"]["state"] == "from_memory"
        assert out["steps"][-1]["memory"]["note"] == "memory item could not be read"


def _hand_graph(*edges):
    b = G.Builder(None)
    for e in edges:
        for nid in (e.src, e.dst):
            b.node(G.Node("input", nid))
        b.edge(e)
    return b.build()


def test_p6_from_memory_replaces_only_fresh(monkeypatch):
    mem = G.Edge("recalled_from", "a", "m", recorded=MEM.MemoryRecall("i", 1, "u", "t"))
    ok = G.Edge("priced_through", "a", "p", recorded="ok")
    bad = G.Edge("priced_through", "a", "q", recorded="bad")

    def compare(g, edge):
        return (
            None
            if edge.recorded == "ok"
            else G.Broken("e", edge.src, edge.dst, "f", 1, "f", 2, "changed")
        )

    monkeypatch.setitem(G.COMPARATORS, "priced_through", compare)
    # memory alone: from_memory
    assert G.staleness(_hand_graph(mem), "a").state == "from_memory"
    # memory + a compared edge that matches: still from_memory (the value's own stamp, never fresh)
    assert G.staleness(_hand_graph(mem, ok), "a").state == "from_memory"
    # stale wins
    assert G.staleness(_hand_graph(mem, bad), "a").state == "stale"
    # uncompared wins (an edge that recorded nothing)
    blank = G.Edge("priced_through", "a", "r", recorded=None)
    assert G.staleness(_hand_graph(mem, blank), "a").state == "uncompared"
    # fresh stays fresh without memory
    assert G.staleness(_hand_graph(ok), "a").state == "fresh"


def test_p6_the_ledger_rollup_keeps_the_same_precedence():
    from services.provenance_graph import explain as E

    src = open(E.__file__).read()
    assert (
        src.index('"stale"')
        < src.index('"uncompared" in states')
        < src.index('"from_memory" in states')
    )


def test_p7_a_network_with_no_memory_source_is_unchanged():
    n = _net(source="pack:eu-2023", approved=True)
    g = _graph(n)
    assert not [e for e in g.edges if e.kind == "recalled_from"]
    assert not [x for x in g.nodes.values() if x.kind == "memory"]
    out = explain(n, "Generator", "g", "marginal_cost")
    assert all("memory" not in step for step in out["steps"])
    assert out["outcome"]["state"] != "from_memory"
    assert set(out) == {"outcome", "target", "node", "steps"}


def test_p8_a_money_field_accepted_from_memory_is_read_too():
    n = _net(field="overnight_cost")
    out = explain(n, "Generator", "g", "overnight_cost")
    assert out["outcome"]["state"] == "from_memory"
    assert out["steps"][-1]["kind"] == "memory"


def test_p4_a_signed_out_viewer_sees_the_id_and_not_visible_to_you(
    client, install_network, monkeypatch
):
    import main
    from deps import optional_user
    from services import memory_store

    install_network(_net())
    monkeypatch.setattr(
        memory_store, "node", lambda *a: {"summary": "must not be read"}
    )
    main.app.dependency_overrides[optional_user] = lambda: None
    try:
        r = client.get(
            "/api/network/asset_schema/Generator/provenance/g/why/marginal_cost"
        )
    finally:
        main.app.dependency_overrides.pop(optional_user, None)
    memory = r.json()["steps"][-1]["memory"]
    assert memory["item"] is None and memory["note"] == "not visible to you"
