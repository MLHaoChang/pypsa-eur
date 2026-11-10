"""
provenance-graph 02 (`.scratch/provenance-graph/issues/02-money-path-slice.md`): the graph core and the money path.
Plan GO conditions (issue 01, `## Comments`): P1 no vacuous fresh, P2 legacy writers, P3 byte-equal wire against
master's `provenance.staleness` (frozen in `tests/provenance_graph_oracle.py`), P5 rebuilt per query, P8 a test per
edge kind that kills a mutant of its comparison.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pypsa
import pytest

from services import provenance_graph as G
from services.asset_schema import provenance as P
from services.provenance_graph import money
from services.provenance_graph.types import RESERVED_EDGE_KINDS, RESERVED_NODE_KINDS
from tests import provenance_graph_oracle as oracle
from tests.test_typed_cost_units_stale import (
    _applied_before_this,
    _clear_caches,  # noqa: F401 - the autouse fixture
    _fc,
    _pv,
    _strip_scaled_by,
)

BACKEND = Path(__file__).resolve().parents[1]
DC = {
    "column": "dc_ac_ratio",
    "value": 1.2,
    "technology": None,
    "capacity_basis": "output_electric_dc",
}


def _gen(ratio=1.2, scaled=DC, **entry):
    """A PV generator whose overnight cost recorded `scaled` as its scale input, priced at ratio 1.2."""
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "g", bus="b", carrier="solar", p_nom=10.0)
    n.generators["dc_ac_ratio"] = float("nan")
    if ratio is not None:
        n.generators.at["g", "dc_ac_ratio"] = ratio
    P.record(n, "Generator", "g", "overnight_cost", status="customised", source="s")
    P.record(n, "Generator", "g", "lifetime", status="default", source="pack")
    if scaled is not None:
        P.annotate(
            n,
            "Generator",
            "g",
            "overnight_cost",
            scaled_by=copy.deepcopy(scaled),
            **entry,
        )
    return n


def _verdict(n, field="overnight_cost"):
    return G.staleness(
        G.money_graph(n, [("Generator", "g")]),
        money.derived_id("Generator", "g", field),
    )


# ── types ───────────────────────────────────────────────────────────────────


def test_an_unknown_node_or_edge_kind_is_refused():
    with pytest.raises(ValueError, match="unknown node kind"):
        G.Node("formula", "x")
    with pytest.raises(ValueError, match="unknown edge kind"):
        G.Edge("guessed_from", "a", "b")


def test_a_node_added_under_two_kinds_is_refused():
    b = G.Builder(None)
    b.node(G.Node("input", "x"))
    with pytest.raises(ValueError, match="added as"):
        b.node(G.Node("derived", "x"))


def test_the_reserved_kinds_build_an_empty_set_today(tmp_path, monkeypatch):
    g = G.money_graph(
        _pv(tmp_path, monkeypatch), [("Generator", "g"), ("Generator", "absent")]
    )
    assert g.nodes and not [
        x for x in g.nodes.values() if x.kind in RESERVED_NODE_KINDS
    ]
    assert not [e for e in g.edges if e.kind in RESERVED_EDGE_KINDS]


# ── priced_through: fresh, changed, unreadable (P8: each kills a mutant of the comparison) ──


def test_priced_through_fresh_when_the_scale_input_is_what_was_recorded():
    v = _verdict(_gen(ratio=1.2 * (1 + 1e-12)))
    assert v.state == "fresh" and v.broken == () and v.uncompared == ()


@pytest.mark.parametrize("ratio", [1.3, 1.2 * (1 + 1e-8), 1.1])
def test_priced_through_stale_when_the_scale_input_changed(ratio):
    v = _verdict(_gen(ratio=ratio))
    assert v.state == "stale"
    (b,) = v.broken
    assert (b.edge, b.field, b.recorded, b.current_field, b.reason) == (
        "priced_through",
        "dc_ac_ratio",
        1.2,
        "dc_ac_ratio",
        "scale input changed",
    )
    assert b.current == pytest.approx(ratio)
    assert b.dst == money.input_id("Generator", "g", "dc_ac_ratio")


def test_priced_through_stale_when_another_column_prices_it_now():
    v = _verdict(_gen(scaled={**DC, "column": "efficiency"}))
    (b,) = v.broken
    assert (b.field, b.current_field, b.reason) == (
        "efficiency",
        "dc_ac_ratio",
        "scale input changed",
    )


def test_priced_through_stale_when_the_scale_input_cannot_be_read_now():
    v = _verdict(_gen(ratio=None))
    (b,) = v.broken
    assert v.state == "stale" and b.current is None and b.current_field is None
    assert b.reason.startswith("cannot be priced now:") and b.recorded == 1.2


@pytest.mark.parametrize(
    "bad", ["1.2", {"column": "dc_ac_ratio"}, {**DC, "value": float("inf")}]
)
def test_priced_through_stale_when_the_record_is_malformed(bad):
    v = _verdict(_gen(scaled=bad))
    assert v.state == "stale" and v.broken[0].reason == "scale input record malformed"
    assert v.broken[0].dst == money.input_id("Generator", "g", "scale_input")


def test_priced_through_stale_when_a_scaling_pack_price_recorded_nothing(
    tmp_path, monkeypatch
):
    """P2: a pack price from before TCU-05 (annotated with its basis, no `scaled_by`)."""
    n = _pv(tmp_path, monkeypatch)
    _strip_scaled_by(n, "Generator", "g")
    g = G.money_graph(n, [("Generator", "g")])
    for f in money.SCALED_FIELDS:
        v = G.staleness(g, money.derived_id("Generator", "g", f))
        assert v.state == "stale" and v.broken[0].reason == "scale input not recorded"


@pytest.mark.parametrize(
    "dc, reason", [(True, "scale input not recorded"), (False, None)]
)
def test_a_library_reference_applied_before_tcu05(monkeypatch, dc, reason):
    """P2: a customised value with `approved_by` and no scale input: DC reads not recorded, AC compares nothing."""
    n = _applied_before_this(monkeypatch, dc=dc)
    g = G.money_graph(n, [("Generator", "pv")])
    v = G.staleness(g, money.derived_id("Generator", "pv", "overnight_cost"))
    if reason:
        assert v.state == "stale" and v.broken[0].reason == reason
    else:
        assert v.state == "uncompared" and v.broken == ()


# ── derived_from, stamped_with, shown_in ─────────────────────────────────────


def test_derived_from_carries_a_broken_price_to_the_annualised_cost():
    n = _gen(ratio=1.3)
    P.record(n, "Generator", "g", "fom_cost", status="customised", source="other")
    g = G.money_graph(n, [("Generator", "g")])
    v = G.staleness(g, money.derived_id("Generator", "g", "capital_cost"))
    assert v.state == "stale" and [b.src for b in v.broken] == [
        money.derived_id("Generator", "g", "overnight_cost")
    ]
    # The plain fom_cost compared nothing: named, never fresh (P1).
    assert v.uncompared == (money.input_id("Generator", "g", "fom_cost"),)


def test_derived_from_reads_fresh_only_when_every_input_compared():
    g = G.money_graph(_gen(), [("Generator", "g")])
    assert (
        G.staleness(g, money.derived_id("Generator", "g", "capital_cost")).state
        == "fresh"
    )


def test_derived_from_unreadable_input_is_stale_on_the_annualised_cost():
    g = G.money_graph(_gen(ratio=None), [("Generator", "g")])
    assert (
        G.staleness(g, money.derived_id("Generator", "g", "capital_cost")).state
        == "stale"
    )


def test_stamped_with_and_shown_in_never_break_and_compare_nothing():
    n = _gen(scaled=None)
    g = G.money_graph(n, [("Generator", "g")])
    assert {e.kind for e in g.edges} == {"stamped_with", "shown_in", "derived_from"}
    for nid in (
        money.input_id("Generator", "g", "overnight_cost"),
        money.derived_id("Generator", "g", "capital_cost"),
    ):
        v = G.staleness(g, nid)
        assert (
            v.state == "uncompared" and v.broken == () and v.uncompared
        )  # P1: no vacuous fresh
    assert G.staleness(
        g, money.input_id("Generator", "g", "overnight_cost")
    ).uncompared == (money.input_id("Generator", "g", "overnight_cost"),)
    assert G.stale_entries(n, "Generator", "g") == P.get(
        n, "Generator", "g"
    )  # G2: no `stale` key on the wire
    stamp = next(
        e
        for e in g.edges
        if e.kind == "stamped_with" and e.src.endswith("/overnight_cost")
    )
    assert (
        stamp.recorded == P.get(n, "Generator", "g")["overnight_cost"]
        and stamp.dst == "source:s"
    )


def test_an_unknown_node_reads_uncompared_never_fresh():
    g = G.money_graph(_gen(), [("Generator", "g")])
    assert G.staleness(g, "derived:Generator/nope/overnight_cost").state == "uncompared"


def test_each_money_surface_asks_through_its_shown_in_edge():
    g = G.money_graph(_gen(ratio=1.3), [("Generator", "g")])
    for surface in money.ASSET_SURFACES:
        (v,) = G.surface_verdicts(
            g, money.surface_id(surface, "Generator", "g", "overnight_cost")
        )
        assert v.state == "stale"
    (v,) = G.surface_verdicts(
        g, money.surface_id("economics_costs", "Generator", "g", "capital_cost")
    )
    assert v.state == "stale"


def test_why_walks_from_the_annualised_cost_to_the_scale_input():
    g = G.money_graph(_gen(ratio=1.3), [("Generator", "g")])
    w = G.why(g, money.derived_id("Generator", "g", "capital_cost"))
    assert [(s.node.id, s.via and s.via.kind, s.state) for s in w.steps] == [
        (money.derived_id("Generator", "g", "capital_cost"), None, "stale"),
        (money.derived_id("Generator", "g", "overnight_cost"), "derived_from", "stale"),
        (money.input_id("Generator", "g", "dc_ac_ratio"), "priced_through", "stale"),
    ]
    assert [s.node.kind for s in w.steps] == ["derived", "derived", "input"]
    assert G.why(g, "input:nowhere").steps[0].node == G.Node("input", "input:nowhere")
    assert w.steps[0].stamp is None  # the annualised cost carries no stamp of its own
    assert w.steps[1].stamp["source"] == "s" and w.steps[1].stamp["scaled_by"] == DC


def test_the_graph_writes_nothing_and_is_rebuilt_per_query():
    """P5: a change between two queries is seen by the second; the store is never written."""
    n = _gen()
    before = copy.deepcopy(P._root(n))
    assert "stale" not in G.stale_entries(n, "Generator", "g")["overnight_cost"]
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    assert G.stale_entries(n, "Generator", "g")["overnight_cost"]["stale"][
        "current"
    ] == pytest.approx(1.3)
    assert P._root(n) == before


# ── P3: byte-equal with master's `provenance.staleness` ─────────────────────


def _scenarios(tmp_path, monkeypatch):
    k = iter(range(100))

    def pv():
        return _pv(tmp_path / str(next(k)), monkeypatch)

    yield "pv fresh", pv(), "Generator", "g"
    n = pv()
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    yield "pv edited", n, "Generator", "g"
    n = pv()
    n.generators.at["g", "dc_ac_ratio"] = float("nan")
    yield "pv unreadable", n, "Generator", "g"
    n = pv()
    P.mark_customised(n, "Generator", "g", ["overnight_cost"])
    yield "pv typed over", n, "Generator", "g"
    n = pv()
    _strip_scaled_by(n, "Generator", "g")
    yield "pack price with no input", n, "Generator", "g"
    n = pv()
    P._root(n)["Generator"]["g"]["overnight_cost"]["scaled_by"] = {
        "column": "dc_ac_ratio"
    }
    yield "malformed", n, "Generator", "g"
    for ratio in (1.2, 1.3):
        n = pv()
        P._root(n)["Generator"]["g"]["fom_cost"].pop("scaled_by")
        n.generators.at["g", "dc_ac_ratio"] = ratio
        yield f"fom inherits the overnight annotation at {ratio}", n, "Generator", "g"
    n = _fc()
    yield "link fresh", n, "Link", "fc"
    n = _fc()
    n.links.at["fc", "efficiency"] = 0.4
    yield "link edited", n, "Link", "fc"
    yield "dc reference", _applied_before_this(monkeypatch, dc=True), "Generator", "pv"
    yield "ac reference", _applied_before_this(monkeypatch, dc=False), "Generator", "pv"
    yield (
        "other release",
        _applied_before_this(
            monkeypatch,
            dc=False,
            source="eu-dea-2026-08#" + "0" * 16 + "/solar-utility@2030",
        ),
        "Generator",
        "pv",
    )
    n = _gen(scaled=None)
    yield "in memory, nothing scaled", n, "Generator", "g"
    yield "in memory, nothing recorded", pypsa.Network(), "Generator", "none"


def _wire(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str, allow_nan=True)


def test_the_graph_wire_is_byte_equal_to_the_old_staleness(tmp_path, monkeypatch):
    seen = []
    for label, n, cls, name in _scenarios(tmp_path, monkeypatch):
        old = oracle.staleness(n, cls, name, P.get(n, cls, name))
        assert _wire(G.stale_entries(n, cls, name)) == _wire(old), label
        seen.append(any("stale" in e for e in old.values()))
    assert any(seen) and not all(seen)


def test_both_routes_are_byte_equal_to_the_old_staleness_on_an_edited_network(
    tmp_path, monkeypatch, client, install_network
):
    from services.library.defaults_pack.pricing import price_from_pack
    from tests.test_library_defaults_pack_typed_units import TYPED

    n = install_network(_pv(tmp_path, monkeypatch))
    price_from_pack(n, "links", "fc", "fuel-cell", version=TYPED)
    # Edit through the API, as a person would, then off the API (in memory).
    r = client.put(
        "/api/network/generators/g",
        json={"name": "g", "bus": "ac", "carrier": "solar", "dc_ac_ratio": 1.35},
    )
    assert r.status_code == 200, r.text
    assert n.generators.at["g", "dc_ac_ratio"] == pytest.approx(1.35)
    n.links.at["fc", "efficiency"] = 0.45
    rows = client.get("/api/network/asset_costs").json()["assets"]
    assert rows
    for row in rows:
        old = oracle.staleness(
            n, row["component"], row["name"], P.get(n, row["component"], row["name"])
        )
        assert _wire(row["provenance"]) == _wire(json.loads(json.dumps(old))), row[
            "name"
        ]
    for cls, name in (("Generator", "g"), ("Link", "fc")):
        got = client.get(f"/api/network/asset_schema/{cls}/provenance/{name}").json()[
            "fields"
        ]
        assert _wire(got) == _wire(
            json.loads(json.dumps(oracle.staleness(n, cls, name, P.get(n, cls, name))))
        )
        assert "stale" in got["overnight_cost"]


# ── guards ───────────────────────────────────────────────────────────────────


def _sources(exclude=("tests", "provenance_graph", ".venv", "node_modules")):
    for p in BACKEND.rglob("*.py"):
        if not set(p.relative_to(BACKEND).parts) & set(exclude):
            yield p


def test_no_reader_keeps_its_own_scale_comparison():
    """A migrated reader asks the graph: nothing calls `provenance.staleness` (gone) or reads the current scale
    input itself; `pricing.current_scale_input` is the graph's one reader of "now"."""
    assert not hasattr(P, "staleness") and not hasattr(P, "_verdict")
    offenders = []
    for p in _sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "staleness"
                and isinstance(node.value, ast.Name)
                and "provenance" in node.value.id
                and node.value.id != "provenance_graph"
            ):
                offenders.append(f"{p}:{node.lineno}")
            name = (
                node.attr
                if isinstance(node, ast.Attribute)
                else node.id
                if isinstance(node, ast.Name)
                else None
            )
            if name == "current_scale_input" and p.name != "pricing.py":
                offenders.append(f"{p}:{node.lineno}")
    assert offenders == []


def test_no_production_module_imports_the_oracle():
    assert not [
        p
        for p in _sources()
        if "provenance_graph_oracle" in p.read_text(encoding="utf-8")
    ]


@pytest.mark.parametrize(
    "module",
    [
        "services/adequacy/portfolio.py",
        "services/time_aggregation_service.py",
        "services/assistant_tools.py",
    ],
)
def test_no_cache_key_reads_a_graph_node(module):
    """GK B ruling (c): cache keys (adequacy, time aggregation, previews) stay out of the graph."""
    assert "provenance_graph" not in (BACKEND / module).read_text(encoding="utf-8")


# ── the Guided ledger (TCU-07): the row reads the verdict of the asset field it compiles to ──


@pytest.fixture(scope="module")
def pv_fork():
    from services.study import library as study_library
    from services.study import packs
    from services.study import questions as Q
    from tests.test_study_asset_view import INTAKE

    library = study_library.load_defaults()
    ledger = study_library.seed_ledger(Q.BESS_AT_SITE, INTAKE, library)
    return packs.build_site_network(
        INTAKE, ledger, "bess_pv_2h", library=library
    ), ledger


def _price_pv_through_its_ratio(n, ratio=1.2):
    """What a DC-priced Library reference applied on the fork leaves: the ratio, and the price's scale input."""
    n.generators["dc_ac_ratio"] = float("nan")
    n.generators.at["pv", "dc_ac_ratio"] = ratio
    P.annotate(n, "Generator", "pv", "overnight_cost", scaled_by={**DC, "value": ratio})


def test_an_untouched_fork_has_no_stale_ledger_row(pv_fork):
    from services.study import asset_view

    n, ledger = pv_fork
    assert asset_view.ledger_stale(n, ledger) == {}
    m = n.copy()
    _price_pv_through_its_ratio(m)
    assert asset_view.ledger_stale(m, ledger) == {}


def test_a_ratio_change_reads_the_same_stale_on_the_ledger_row_as_on_the_card(pv_fork):
    from services.study import asset_view

    n, ledger = pv_fork
    m = n.copy()
    _price_pv_through_its_ratio(m)
    m.generators.at["pv", "dc_ac_ratio"] = 1.3
    card = G.stale_entries(m, "Generator", "pv")["overnight_cost"]["stale"]
    assert card["reason"] == "scale input changed"
    got = asset_view.ledger_stale(m, ledger)
    assert got == {
        "pv_rooftop_eur_per_kw": {
            "component": "Generator",
            "name": "pv",
            "field": "overnight_cost",
            "stale": card,
        }
    }
    # The PV kind the pack did not read is not this fork's row.
    assert "pv_utility_eur_per_kw" in asset_view.asset_snapshot(m).stale


def test_an_unrecorded_scale_input_reads_stale_on_the_ledger_row_too(pv_fork):
    from services.study import asset_view

    n, ledger = pv_fork
    m = n.copy()
    _price_pv_through_its_ratio(m)
    P._root(m)["Generator"]["pv"]["overnight_cost"]["scaled_by"] = {
        "column": "dc_ac_ratio"
    }
    assert (
        asset_view.ledger_stale(m, ledger)["pv_rooftop_eur_per_kw"]["stale"]["reason"]
        == "scale input record malformed"
    )
    del P._root(m)["Generator"]["pv"]["overnight_cost"]["scaled_by"]
    P.annotate(
        m,
        "Generator",
        "pv",
        "overnight_cost",
        status="default",
        capacity_basis="output_electric_dc",
    )
    assert (
        G.stale_entries(m, "Generator", "pv")["overnight_cost"]["stale"]["reason"]
        == "scale input not recorded"
    )
    assert (
        asset_view.ledger_stale(m, ledger)["pv_rooftop_eur_per_kw"]["stale"]["reason"]
        == "scale input not recorded"
    )


def _hand_built(*edges):
    b = G.Builder(None)
    for e in edges:
        for nid in (e.src, e.dst):
            b.node(G.Node("input" if nid.startswith("input:") else "derived", nid))
        b.edge(e)
    return b.build()


def test_an_edge_with_a_comparator_but_nothing_recorded_compares_nothing():
    g = _hand_built(G.Edge("priced_through", "derived:x", "input:y"))
    v = G.staleness(g, "derived:x")
    assert (v.state, v.uncompared) == ("uncompared", ("input:y",))


def test_an_edge_kind_with_no_comparator_compares_nothing_whatever_it_recorded():
    g = _hand_built(
        G.Edge("computed_in", "derived:x", "input:run", recorded="fingerprint")
    )
    v = G.staleness(g, "derived:x")
    assert (v.state, v.uncompared) == ("uncompared", ("input:run",))


def test_a_cycle_is_walked_once():
    g = _hand_built(
        G.Edge("derived_from", "derived:a", "derived:b"),
        G.Edge("derived_from", "derived:b", "derived:a"),
    )
    v = G.staleness(g, "derived:a")
    assert (v.state, v.uncompared) == (
        "uncompared",
        ("derived:a",),
    )  # P1: compared nothing, never fresh
    assert [s.node.id for s in G.why(g, "derived:a").steps] == [
        "derived:a",
        "derived:b",
    ]
