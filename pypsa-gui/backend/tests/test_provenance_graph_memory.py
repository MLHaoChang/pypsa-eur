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


def _item(item, version):
    return {
        "id": f"{item}@v{version}",
        "summary": "7% used before",
        "kind": "parameter",
    }


def _graph(n, memory=_item):
    return G.money_graph(n, [("Generator", "g")], memory)


def _why(n, field="marginal_cost", memory=_item):
    return explain(n, "Generator", "g", field, memory)


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


def test_a_stamp_the_store_does_not_confirm_is_not_user_approved():
    # GK B P2: the stamp is text any imported file can carry. With no reader (or one that cannot confirm it) the
    # value reads `uncompared`, and the step does not say "user-approved".
    for read in (None, lambda i, v: None, lambda i, v: {}):
        out = _why(_net(), memory=read)
        assert out["outcome"]["state"] == "uncompared"
        assert "user-approved" not in json.dumps(out)
        assert out["steps"][-1]["memory"]["item"] is None


def test_why_names_the_memory_item_and_its_state():
    out = _why(_net())
    assert out["outcome"]["state"] == "from_memory"
    first, last = out["steps"][0], out["steps"][-1]
    assert first["state"] == "from_memory"
    assert last["node"] == f"memory:{ITEM}@v2" and last["kind"] == "memory"
    assert last["via"]["edge"] == "recalled_from"
    assert last["via"]["recorded"]["approved_by"] == "a@example.com"
    assert last["memory"]["state"] == "from memory v2, user-approved"
    assert last["memory"]["item"]["summary"] == "7% used before"
    assert last["memory"]["note"] is None
    json.dumps(out)


def test_retiring_or_editing_the_item_does_not_change_the_value_or_its_verdict():
    # Versions are immutable and the edge compares nothing: whatever the item says now (an edited summary), the
    # value's own stamp and state are as they were.
    n = _net()
    outs = [
        _why(n, memory=lambda i, v: {"summary": s})
        for s in ("7% used before", "now 9%")
    ]
    for o in outs:
        assert o["outcome"]["state"] == "from_memory"
        assert o["steps"][0]["stamp"] == outs[0]["steps"][0]["stamp"]


def test_the_graph_writes_nothing():
    n = _net()
    snap = json.dumps(n.meta, sort_keys=True, default=str)
    _why(n)
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
    _why(n)  # no 500


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
    P.mark_customised(n, "Generator", "g", ["marginal_cost"])
    entry = P.get(n, "Generator", "g")["marginal_cost"]
    assert "approved_by" not in entry and "approved_at" not in entry
    assert not [e for e in _graph(n).edges if e.kind == "recalled_from"]
    assert _why(n)["outcome"]["state"] != "from_memory"


WHY = "/api/network/asset_schema/Generator/provenance/g/why/marginal_cost"


def _netcdf(n, tmp_path) -> bytes:
    path = tmp_path / "forged.nc"
    n.export_to_netcdf(path)
    return path.read_bytes()


def _save_item(client) -> str:
    return client.post(
        "/api/memory/items",
        json={
            "kind": "preference",
            "body": {"key": "units.power", "value": "GW"},
            "concept_keys": ["topic:units"],
        },
    ).json()["id"]


def _net_for(item, version=1):
    return _net(source=f"memory:{item}@v{version}")


# ── P2: the import route cannot forge an approval ───────────────────────────


def test_p2_a_netcdf_import_with_a_forged_memory_stamp_is_not_user_approved(
    client, install_network, tmp_path
):
    import io

    install_network(_net())
    forged = _net(source="memory:mem_00000000000000000000000000000000@v2")
    r = client.post(
        "/api/io/import/netcdf",
        files={
            "file": (
                "f.nc",
                io.BytesIO(_netcdf(forged, tmp_path)),
                "application/x-netcdf",
            )
        },
    )
    assert r.status_code == 200, r.text
    out = client.get(WHY)
    assert out.status_code == 200, out.text
    assert out.json()["outcome"]["state"] != "from_memory"
    assert "user-approved" not in out.text and "a@example.com" not in out.text


# ── P3: a later edit, through each writer, ends the recall ──────────────────


def _installed(client, install_network, item):
    n = install_network(_net_for(item))
    assert client.get(WHY).json()["outcome"]["state"] == "from_memory"
    return n


def test_p3_a_single_edit_through_the_route_ends_the_recall(client, install_network):
    item = _save_item(client)
    _installed(client, install_network, item)
    r = client.put(
        "/api/network/generators/g",
        json={"name": "g", "bus": "b", "marginal_cost": 55.0},
    )
    assert r.status_code == 200, r.text
    out = client.get(WHY).json()
    assert out["outcome"]["state"] != "from_memory"
    assert out["steps"][0].get("memory") is None


def test_p3_a_bulk_edit_through_the_route_ends_the_recall(client, install_network):
    item = _save_item(client)
    _installed(client, install_network, item)
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "Generator",
            "names": ["g"],
            "updates": {"marginal_cost": 55.0},
        },
    )
    assert r.status_code == 200, r.text
    assert client.get(WHY).json()["outcome"]["state"] != "from_memory"


def test_p3_a_bulk_edit_that_changes_nothing_keeps_the_recall(client, install_network):
    item = _save_item(client)
    n = _installed(client, install_network, item)
    same = float(n.generators.at["g", "marginal_cost"])
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "Generator",
            "names": ["g"],
            "updates": {"marginal_cost": same},
        },
    )
    assert r.status_code == 200, r.text
    assert client.get(WHY).json()["outcome"]["state"] == "from_memory"


# ── P4: the viewer's own read; the same on every path ───────────────────────


def test_p4_the_owner_sees_the_item_and_a_second_user_does_not(
    client, install_network, _auth_db, seeded_identity, second_identity
):
    # The second user's own session reads another network, so the route handler is called for each user over the
    # same installed one: the same code the route runs, with that user's own database identity.
    from db.models import User
    from routers.network import get_asset_provenance_why as route

    item = _save_item(client)
    install_network(_net_for(item))
    with _auth_db[1]() as db:
        owner = db.get(User, seeded_identity["user_id"])
        other = db.get(User, second_identity["user_id"])
        mine = route("Generator", "g", "marginal_cost", db, owner)
        theirs = route("Generator", "g", "marginal_cost", db, other)
    assert mine["outcome"]["state"] == "from_memory"
    assert mine["steps"][-1]["memory"]["item"]["summary"]
    step = theirs["steps"][-1]["memory"]
    assert step["id"] == f"memory:{item}@v1"
    assert step["item"] is None and step["note"] == "not visible to you"
    assert theirs["outcome"]["state"] == "uncompared"
    assert "user-approved" not in json.dumps(theirs)


def test_p4_a_signed_out_viewer_sees_not_visible_and_the_store_is_never_read(
    client, install_network, monkeypatch
):
    import main
    from deps import optional_user
    from services import memory_store

    install_network(_net())
    monkeypatch.setattr(memory_store, "node", lambda *a: pytest.fail("store read"))
    main.app.dependency_overrides[optional_user] = lambda: None
    try:
        r = client.get(WHY)
    finally:
        main.app.dependency_overrides.pop(optional_user, None)
    assert r.json()["steps"][-1]["memory"]["note"] == "not visible to you"


def test_p4_the_chat_tool_reads_as_the_acting_user_and_gets_no_depends_object(
    client, install_network, monkeypatch
):
    from fastapi.params import Depends

    from services import chat_tools, memory_store

    item = _save_item(client)
    install_network(_net_for(item))
    real, seen = memory_store.node, []

    def spy(db, user, i, v):
        seen.append((db, user))
        return real(db, user, i, v)

    monkeypatch.setattr(memory_store, "node", spy)
    out = chat_tools.DISPATCHERS["why_this_number"](
        component="Generator", name="g", field="marginal_cost"
    )
    assert seen and not any(isinstance(x, Depends) for pair in seen for x in pair)
    assert out["outcome"]["state"] == "from_memory"
    assert out["steps"][-1]["memory"]["item"]["summary"]


def test_p4_the_ledger_walk_passes_the_viewers_reader():
    n = _net()
    seen = []

    def read(i, v):
        seen.append((i, v))
        return {"summary": "s"}

    from services.provenance_graph import explain as E

    # `overnight_cost` on Generator g, as the PV ledger row compiles to (`ledger_fields`)
    n2 = _net(field="overnight_cost")
    m = E.ledger_fields("pv_rooftop_eur_per_kw")[0]
    assert (m.component, m.field) == ("Generator", "overnight_cost")
    n2.add("Generator", m.name, bus="b", p_nom=1.0)
    P.record(
        n2,
        "Generator",
        m.name,
        "overnight_cost",
        status="customised",
        source=SRC,
        approved_by="a@example.com",
        approved_at="2026-11-12T10:00:00+00:00",
    )
    out = E.explain_ledger_row(n2, "pv_rooftop_eur_per_kw", read)
    assert seen == [(ITEM, 2)] and out["outcome"]["state"] == "from_memory"
    unseen = E.explain_ledger_row(
        n2,
        "pv_rooftop_eur_per_kw",
        lambda i, v: (_ for _ in ()).throw(MEM.ItemNotVisible(i)),
    )
    assert unseen["outcome"]["state"] == "uncompared"
    assert (
        E.explain_ledger_row(n2, "pv_rooftop_eur_per_kw")["outcome"]["state"]
        == "uncompared"
    )
    del n


@pytest.fixture
def studies_on(monkeypatch):
    from tests.study_s4_support import enable_studies

    yield from enable_studies(monkeypatch)


def test_p4_the_ledger_route_reads_memory_as_the_viewer(
    client,
    api_project,
    monkeypatch,
    project_storage_dir,
    studies_on,
    _auth_db,
    seeded_identity,
):
    """The PV price row on the option fork, its cost accepted from the owner's memory item: the owner reads
    `from_memory`; a second user and a signed-out request read `uncompared` and "not visible to you"."""
    import main
    import pypsa as _pypsa
    from db.models import User
    from deps import optional_user
    from services import solver_service
    from services.pypsa_service import PyPSAService
    from tests.study_s4_support import create_pack_study, wait_run
    from tests.test_study_expert_edits_routes import INTAKE
    from tests.test_study_runner import FakeSolver

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    name = "pg07-ledger"
    api_project(f"{name}-src")
    r = create_pack_study(
        client,
        f"{name}-src",
        name,
        intake={**INTAKE, "pv": {"enabled": True, "kind": "rooftop"}},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert (
        client.post(f"/api/projects/{name}/studies/{sid}/run", json={}).status_code
        == 202
    )
    assert wait_run(client, name, sid)["status"] == "done"

    item = _save_item(client)
    path = project_storage_dir(f"{name}-opt-bess_pv_2h") / "network.nc"
    n = _pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    P.record(
        n,
        "Generator",
        "pv",
        "overnight_cost",
        status="customised",
        source=f"memory:{item}@v1",
        approved_by="a@example.com",
        approved_at="2026-11-12T10:00:00+00:00",
    )
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.export_network_to_netcdf(n, path)

    url = f"/api/projects/{name}/studies/{sid}/ledger/pv_rooftop_eur_per_kw/why"
    mine = client.get(url, params={"option_id": "bess_pv_2h"})
    assert mine.status_code == 200, mine.text
    assert mine.json()["outcome"]["state"] == "from_memory"
    (field,) = mine.json()["fields"]
    assert field["steps"][-1]["memory"]["item"]["summary"]

    # A colleague in the same org (an admin, so the project is theirs to open) who is not the item's owner.
    import uuid
    from datetime import UTC, datetime

    from db.models import OrgMembership
    from services.auth_service import hash_password

    with _auth_db[1]() as db:
        other = User(
            id=uuid.uuid4(),
            email=f"colleague-{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("x-not-used-1A"),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=UTC),
        )
        db.add(other)
        db.flush()
        db.add(
            OrgMembership(
                id=uuid.uuid4(),
                user_id=other.id,
                org_id=seeded_identity["org_id"],
                role="admin",
            )
        )
        db.commit()
        db.refresh(other)
        db.expunge(other)
    for viewer in (other, None):
        main.app.dependency_overrides[optional_user] = lambda viewer=viewer: viewer
        try:
            r = client.get(url, params={"option_id": "bess_pv_2h"})
        finally:
            main.app.dependency_overrides.pop(optional_user, None)
        if viewer is None:  # signed out: the project gate itself refuses
            assert r.status_code in (401, 404), r.text
            continue
        assert r.status_code == 200, r.text
        assert r.json()["outcome"]["state"] == "uncompared"
        (field,) = r.json()["fields"]
        step = field["steps"][-1]["memory"]
        assert step["item"] is None and step["note"] == "not visible to you"
        assert "user-approved" not in r.text and "a@example.com" not in r.text


def test_p4_the_chat_tool_as_a_second_user_reads_not_visible(
    client, install_network, second_identity
):
    from services import chat_tools

    item = _save_item(client)
    install_network(_net_for(item))
    chat_tools.set_acting_user(second_identity["user_id"])
    out = chat_tools.DISPATCHERS["why_this_number"](
        component="Generator", name="g", field="marginal_cost"
    )
    assert out["outcome"]["state"] == "uncompared"
    assert out["steps"][-1]["memory"]["note"] == "not visible to you"
    assert "user-approved" not in json.dumps(out)


def test_the_chat_tool_with_no_acting_user_reads_uncompared_not_a_401(
    client, install_network
):
    from services import chat_tools

    item = _save_item(client)
    install_network(_net_for(item))
    chat_tools.set_acting_user(None)
    out = chat_tools.DISPATCHERS["why_this_number"](
        component="Generator", name="g", field="marginal_cost"
    )
    assert out["outcome"]["state"] == "uncompared"
    assert out["steps"][-1]["memory"]["note"] == "not visible to you"


def test_a3_an_unconfirmed_stamp_does_not_echo_its_approver():
    out = _why(_net(), memory=lambda i, v: None)
    text = json.dumps(out)
    assert "a@example.com" not in text and "2026-11-12T10:00" not in text
    assert out["steps"][0]["stamp"]["approval"].startswith("unconfirmed")
    assert out["steps"][-1]["via"]["recorded"]["approval"].startswith("unconfirmed")
    confirmed = json.dumps(_why(_net()))
    assert "a@example.com" in confirmed  # a confirmed approval still names who


def test_p4_a_store_404_reads_not_visible_and_any_other_failure_could_not_be_read():
    def gone(i, v):
        raise MEM.ItemNotVisible(i)

    def down(i, v):
        raise RuntimeError("db down")

    assert (
        _why(_net(), memory=gone)["steps"][-1]["memory"]["note"] == "not visible to you"
    )
    assert (
        _why(_net(), memory=down)["steps"][-1]["memory"]["note"]
        == "memory item could not be read"
    )


# ── P5 / P6 / P7 / P8 ───────────────────────────────────────────────────────


def test_p5_an_unresolvable_version_states_why_and_never_raises():
    for exc in (RuntimeError("db down"), LookupError("x")):

        def bad(item, version, exc=exc):
            raise exc

        step = _why(_net(), memory=bad)["steps"][-1]
        assert step["memory"]["note"] == "memory item could not be read"
        assert step["memory"]["id"] == f"memory:{ITEM}@v2"


def _hand_graph(*edges):
    b = G.Builder(None)
    for e in edges:
        for nid in (e.src, e.dst):
            b.node(G.Node("input", nid))
        b.edge(e)
    return b.build()


def test_p6_from_memory_replaces_only_fresh(monkeypatch):
    recall = MEM.MemoryRecall("i", 1, "u", "t", item_read={"summary": "s"})
    mem = G.Edge("recalled_from", "a", "m", recorded=recall)
    ok = G.Edge("priced_through", "a", "p", recorded="ok")
    bad = G.Edge("priced_through", "a", "q", recorded="bad")

    def compare(g, edge):
        return (
            None
            if edge.recorded == "ok"
            else G.Broken("e", edge.src, edge.dst, "f", 1, "f", 2, "changed")
        )

    monkeypatch.setitem(G.COMPARATORS, "priced_through", compare)
    assert G.staleness(_hand_graph(mem), "a").state == "from_memory"
    assert G.staleness(_hand_graph(mem, ok), "a").state == "from_memory"
    assert G.staleness(_hand_graph(mem, bad), "a").state == "stale"
    blank = G.Edge("priced_through", "a", "r", recorded=None)
    assert G.staleness(_hand_graph(mem, blank), "a").state == "uncompared"
    unconfirmed = G.Edge(
        "recalled_from", "a", "m", recorded=MEM.MemoryRecall("i", 1, "u", "t")
    )
    assert G.staleness(_hand_graph(unconfirmed), "a").state == "uncompared"
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
    out = _why(n)
    assert all("memory" not in step for step in out["steps"])
    assert out["outcome"]["state"] != "from_memory"
    assert set(out) == {"outcome", "target", "node", "steps"}


def test_p8_a_money_field_accepted_from_memory_is_read_too():
    out = _why(_net(field="overnight_cost"), "overnight_cost")
    assert out["outcome"]["state"] == "from_memory"
    assert out["steps"][-1]["kind"] == "memory"
