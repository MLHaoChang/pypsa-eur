"""Provenance-graph issue 08: `commercial.provenance` cells as `input` nodes with compared `stamped_with` edges.

GK B (45) plan GO conditions C1–C9; each test names the one it covers."""

from __future__ import annotations

import copy
import json

import pytest

from services import document_values_commercial as DC
from services.provenance_graph import commercial as C
from services.provenance_graph import queries as Q
from services.provenance_graph.types import CellStamp, Edge, Node
from tests.test_document_values_commercial import (  # noqa: F401  (`site` is a fixture)
    EUR_PRICE,
    PEAK,
    _dispatch,
    _pdf,
    _preview,
    _stored,
    site,
)

PEAK_KEY = "tariff:energy/periods[peak].rate"
PTIER_KEY = "tariff:block/periods[day].tier_rates[1]"
TIER_KEY = "tariff:stepped/tiers[1].rate"
PPA_KEY = "ppa:ppa1/price"
TENOR_KEY = "ppa:ppa1/tenor_years"


def _entry(value, unit, currency="EUR", year=None, source="pdf:abc p1 'quote'") -> dict:
    return {
        "source": source,
        "value": value,
        "unit": unit,
        "currency": currency,
        "currency_year": year,
        "approved_by": "ann",
        "approved_at": "2026-11-10T10:00:00+00:00",
    }


def _config() -> dict:
    tariff = {
        "id": "t",
        "name": "Grid tariff",
        "jurisdiction": "DE",
        "valid_from": "2029-01-01",
        "items": [
            {
                "id": "energy",
                "kind": "energy",
                "unit": "per_kwh",
                "periods": [
                    {"name": "peak", "rate": 0.2},
                    {"name": "offpeak", "rate": 0.1},
                ],
            },
            {
                "id": "block",
                "kind": "energy",
                "unit": "per_kwh",
                "periods": [{"name": "day", "tier_rates": [0.3, 0.4]}],
            },
            {
                "id": "stepped",
                "kind": "demand",
                "unit": "per_kw_month",
                "tiers": [
                    {"threshold": 0, "rate": 9.0},
                    {"threshold": 100, "rate": 12.0},
                ],
            },
        ],
    }
    ppa = {
        "type": "ppa",
        "id": "ppa1",
        "price": 55.0,
        "tenor_years": 10,
        "base_year": 2030,
    }
    return {
        "import_tariff": tariff,
        "contracts": [ppa],
        "provenance": {
            PEAK_KEY: _entry(0.2, "/kWh"),
            PTIER_KEY: _entry(0.4, "/kWh"),
            TIER_KEY: _entry(12.0, "/kW/month"),
            PPA_KEY: _entry(55.0, "/MWh", year=2030),
            TENOR_KEY: _entry(10, "years", currency=None),
        },
    }


def _item(config: dict, item_id: str) -> dict:
    return next(x for x in config["import_tariff"]["items"] if x["id"] == item_id)


def _state(config, key) -> str:
    return Q.cell_verdict(config, key).state


def _broke(config, key):
    verdict = Q.cell_verdict(config, key)
    assert verdict.state == "stale" and len(verdict.broken) == 1
    return verdict.broken[0]


# ── Fresh / changed / unreadable (P8) ──────────────────────────────────────


@pytest.mark.parametrize("key", [PEAK_KEY, PTIER_KEY, TIER_KEY, PPA_KEY, TENOR_KEY])
def test_a_cell_that_holds_its_approved_value_is_fresh(key):
    verdict = Q.cell_verdict(_config(), key)
    assert (verdict.state, verdict.broken, verdict.uncompared) == ("fresh", (), ())


def test_an_edited_cell_is_stale_and_says_recorded_and_current():
    config = _config()
    _item(config, "energy")["periods"][0]["rate"] = 0.09
    broke = _broke(config, PEAK_KEY)
    assert broke.wire() == {
        "field": "value",
        "recorded": 0.2,
        "current_field": PEAK_KEY,
        "current": 0.09,
        "reason": "cell edited since approval",
    }
    assert (broke.edge, broke.src, broke.dst) == (
        "stamped_with",
        C.cell_id(PEAK_KEY),
        "source:pdf:abc p1 'quote'",
    )
    assert _state(config, PPA_KEY) == "fresh"  # one edit breaks one cell


def _no_item(c):
    c["import_tariff"]["items"] = [
        x for x in c["import_tariff"]["items"] if x["id"] != "energy"
    ]


def _no_period(c):
    _item(c, "energy")["periods"] = [{"name": "offpeak", "rate": 0.2}]


def _no_tier(c):
    _item(c, "stepped")["tiers"] = [{"threshold": 0, "rate": 12.0}]


def _no_period_tier(c):
    _item(c, "block")["periods"][0]["tier_rates"] = [0.4]


def _no_contract(c):
    c["contracts"] = []


def _no_tariff(c):
    del c["import_tariff"]


def _null_tariff(c):
    c["import_tariff"] = None


def _no_tiers_key(c):
    del _item(c, "stepped")["tiers"]


#: (what goes, the key that reads it, the `CELL_GONE` type the read raises). Each type has its own case (C3).
GONE = [
    (_no_item, PEAK_KEY, StopIteration),
    (_no_period, PEAK_KEY, StopIteration),
    (_no_tier, TIER_KEY, IndexError),
    (_no_period_tier, PTIER_KEY, IndexError),
    (_no_contract, PPA_KEY, StopIteration),
    (_no_tariff, PEAK_KEY, KeyError),
    (_no_tiers_key, TIER_KEY, KeyError),
    (_null_tariff, PEAK_KEY, TypeError),
]


@pytest.mark.parametrize(
    ("gone", "key", "raises"), GONE, ids=[g[0].__name__ for g in GONE]
)
def test_a_cell_whose_item_period_tier_or_contract_is_gone_is_stale_never_fresh(
    gone, key, raises
):
    config = _config()
    gone(config)
    with pytest.raises(raises):
        DC.cell_value(config, key)
    broke = _broke(config, key)
    assert (broke.reason, broke.field, broke.recorded, broke.current) == (
        C.GONE,
        "value",
        config["provenance"][key]["value"],
        None,
    )


def test_a_path_that_is_no_tariff_value_reads_gone_through_attribute_error():
    """The fifth `CELL_GONE` type: a stored key whose path matches no tariff pattern."""
    config = _config()
    config["provenance"]["tariff:energy/bogus"] = _entry(1.0, "/kWh")
    with pytest.raises(AttributeError):
        DC.cell_value(config, "tariff:energy/bogus")
    assert _broke(config, "tariff:energy/bogus").reason == C.GONE


def test_an_error_outside_cell_gone_is_never_read_as_fresh_it_propagates(monkeypatch):
    """C3: the comparator catches exactly `CELL_GONE`."""

    def boom(config, key):
        raise RuntimeError("bug")

    monkeypatch.setattr(DC, "cell_now", boom)
    with pytest.raises(RuntimeError):
        Q.cell_verdict(_config(), PEAK_KEY)


def test_an_error_outside_cell_gone_while_reading_the_value_propagates(monkeypatch):
    """C3, the first catch site: `cell_value` raising anything but `CELL_GONE` is never read as gone."""

    def boom(config, key):
        raise RuntimeError("bug")

    monkeypatch.setattr(DC, "cell_value", boom)
    with pytest.raises(RuntimeError):
        Q.cell_verdict(_config(), PEAK_KEY)


# ── What the stamp compares (C2) ───────────────────────────────────────────


def test_a_tariff_currency_that_appeared_or_vanished_is_stale():
    """None is a value: a currency the tariff no longer implies, or one the stamp did not record, differs."""
    config = _config()
    config["import_tariff"]["jurisdiction"] = "generic"
    broke = _broke(config, PEAK_KEY)
    assert (broke.field, broke.recorded, broke.current) == ("currency", "EUR", None)
    config = _config()
    config["provenance"][PEAK_KEY]["currency"] = None
    config["import_tariff"]["currency"] = "USD"
    broke = _broke(config, PEAK_KEY)
    assert (broke.field, broke.recorded, broke.current) == ("currency", None, "USD")


def test_a_price_stamped_with_no_money_year_is_stale_once_the_contract_states_one():
    config = _config()
    config["provenance"][PPA_KEY]["currency_year"] = None
    broke = _broke(config, PPA_KEY)
    assert (broke.field, broke.recorded, broke.current) == ("currency_year", None, 2030)


def test_an_equal_value_whose_unit_moved_is_stale():
    config = _config()
    _item(config, "energy")["unit"] = "per_kw_month"
    broke = _broke(config, PEAK_KEY)
    assert (broke.field, broke.recorded, broke.current, broke.reason) == (
        "unit",
        "/kWh",
        "/kW/month",
        "unit differs from the approved one",
    )


@pytest.mark.parametrize("change", [{"currency": "USD"}, {"jurisdiction": "US-CA"}])
def test_an_equal_value_whose_tariff_currency_moved_is_stale(change):
    config = _config()
    config["import_tariff"].update(change)
    broke = _broke(config, PEAK_KEY)
    assert (broke.field, broke.recorded, broke.current) == ("currency", "EUR", "USD")


def test_an_equal_price_whose_contract_money_year_moved_is_stale():
    config = _config()
    config["contracts"][0]["base_year"] = 2031
    broke = _broke(config, PPA_KEY)
    assert (broke.field, broke.recorded, broke.current) == ("currency_year", 2030, 2031)
    assert _state(config, TENOR_KEY) == "fresh"  # not a price: no money year to compare
    del config["contracts"][0][
        "base_year"
    ]  # the year the stamp names is no longer stated: not fresh
    broke = _broke(config, PPA_KEY)
    assert (broke.field, broke.recorded, broke.current) == ("currency_year", 2030, None)
    assert Q.cell_wire(config, PPA_KEY)["compared"] == [
        "value",
        "unit",
        "currency_year",
    ]


@pytest.mark.parametrize("unit", ["per_parsec", None, ["per_kwh"]])
def test_a_cell_whose_item_unit_cannot_be_read_is_stale_and_says_so(unit):
    config = _config()
    _item(config, "energy")["unit"] = unit
    assert DC.cell_value(config, PEAK_KEY) == 0.2
    assert _broke(config, PEAK_KEY).reason == C.UNREADABLE


def test_the_wire_names_the_fields_compared_and_the_ones_not():
    config = _config()
    wire = Q.cell_wire(config, PEAK_KEY)
    assert wire == {
        "entry": config["provenance"][PEAK_KEY],
        "state": "fresh",
        "compared": ["value", "unit", "currency"],
        "uncompared": ["source", "currency_year", "approved_by", "approved_at"],
    }
    assert Q.cell_wire(config, PPA_KEY)["compared"] == [
        "value",
        "unit",
        "currency_year",
    ]
    assert (
        "currency" in Q.cell_wire(config, PPA_KEY)["uncompared"]
    )  # a contract states no currency
    config["import_tariff"]["jurisdiction"] = (
        "generic"  # implies none: the stamp's EUR is no longer what the tariff is in
    )
    wire = Q.cell_wire(config, PEAK_KEY)
    assert (wire["state"], wire["compared"], wire["stale"]["current"]) == (
        "stale",
        ["value", "unit", "currency"],
        None,
    )
    _item(config, "energy")["periods"][0]["rate"] = 0.5
    assert Q.cell_wire(config, PEAK_KEY)["stale"]["current"] == 0.5
    _no_item(config)
    wire = Q.cell_wire(config, PEAK_KEY)
    assert (wire["state"], wire["compared"], wire["stale"]["reason"]) == (
        "stale",
        ["value"],
        C.GONE,
    )
    assert Q.cell_wire(config, "ppa:ppa1/floor") is None


# ── `_same` (C4) ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("recorded", "stored"),
    [
        (55, 55.0),
        (0.3, 0.1 + 0.2),
        ("55", 55.0),
        (None, None),
        (None, float("nan")),
        (float("nan"), None),
    ],
)
def test_values_equal_for_provenance_purposes_are_fresh(recorded, stored):
    """Cells hold scalars only (a rate, a price, a count), so no nested case. `0.1 + 0.2`, the numeric string
    and NaN differ under `==`."""
    config = _config()
    config["contracts"][0]["price"] = stored
    config["provenance"][PPA_KEY]["value"] = recorded
    assert _state(config, PPA_KEY) == "fresh"


@pytest.mark.parametrize(
    ("recorded", "stored"), [(55, None), (None, 55.0), ("x", 55.0), (55.0, 55.1)]
)
def test_values_that_differ_are_stale(recorded, stored):
    config = _config()
    config["contracts"][0]["price"] = stored
    config["provenance"][PPA_KEY]["value"] = recorded
    assert _broke(config, PPA_KEY).reason == "cell edited since approval"


# ── The walk rule (C1) ─────────────────────────────────────────────────────


def _asset_stamp_graph():
    b = Q.Builder(None)
    b.node(Node("input", "input:Generator/pv/capital_cost"))
    b.edge(
        Edge(
            "stamped_with",
            "input:Generator/pv/capital_cost",
            "source:pack",
            recorded={"source": "pack"},
        )
    )
    return b.build()


def test_staleness_leaves_an_asset_stamp_unwalked():
    verdict = Q.staleness(_asset_stamp_graph(), "input:Generator/pv/capital_cost")
    assert (verdict.state, verdict.uncompared) == (
        "uncompared",
        ("input:Generator/pv/capital_cost",),
    )


def test_why_leaves_an_asset_stamp_unwalked():
    walk = Q.why(_asset_stamp_graph(), "input:Generator/pv/capital_cost")
    assert [(s.node.id, s.via, s.stamp) for s in walk.steps] == [
        ("input:Generator/pv/capital_cost", None, {"source": "pack"})
    ]


def test_staleness_walks_a_cell_stamp():
    g = Q.commercial_graph(_config())
    assert [e.kind for e in Q.walked(g, C.cell_id(PEAK_KEY))] == ["stamped_with"]
    assert Q.staleness(g, C.cell_id(PEAK_KEY)).state == "fresh"


def test_why_walks_a_cell_stamp_and_shows_its_entry():
    config = _config()
    _item(config, "energy")["periods"][0]["rate"] = 0.09
    walk = Q.why(Q.commercial_graph(config), C.cell_id(PEAK_KEY))
    cell, source = walk.steps
    assert (cell.node.id, cell.state, cell.stamp) == (
        C.cell_id(PEAK_KEY),
        "stale",
        config["provenance"][PEAK_KEY],
    )
    assert (source.node.id, source.via.kind, source.state) == (
        "source:pdf:abc p1 'quote'",
        "stamped_with",
        "stale",
    )


def test_a_cell_stamp_with_no_registered_comparator_is_uncompared_never_fresh(
    monkeypatch,
):
    monkeypatch.delitem(Q.COMPARATORS, "stamped_with")
    verdict = Q.cell_verdict(_config(), PEAK_KEY)
    assert (verdict.state, verdict.uncompared) == (
        "uncompared",
        ("source:pdf:abc p1 'quote'",),
    )


def test_a_key_with_no_stored_entry_is_uncompared_never_fresh():
    verdict = Q.cell_verdict(_config(), "ppa:ppa1/floor")
    assert (verdict.state, verdict.uncompared) == (
        "uncompared",
        (C.cell_id("ppa:ppa1/floor"),),
    )
    assert Q.cell_verdict(None, PEAK_KEY).state == "uncompared"
    assert (
        Q.cell_verdict({"provenance": ["not", "a", "dict"]}, PEAK_KEY).state
        == "uncompared"
    )


# ── Ids and sources (C6, C7) ───────────────────────────────────────────────


def test_cell_ids_are_distinct_for_keys_that_differ_only_by_a_separator():
    keys = [
        "tariff:a/b:c/d",
        "tariff:a/b/c:d",
        "tariff:a:b/c/d",
        "tariff:a/b:c:d",
        "tariff:a/b/c/d",
    ]
    assert len({C.cell_id(k) for k in keys}) == len(keys)
    config = {**_config(), "provenance": {k: _entry(1.0, "/kWh") for k in keys}}
    g = Q.commercial_graph(config)
    assert sorted(g.nodes) == sorted(C.cell_id(k) for k in keys)
    assert {Q.staleness(g, C.cell_id(k)).state for k in keys} == {
        "stale"
    }  # none parses to a live cell


@pytest.mark.parametrize(
    "key",
    [
        "nonsense",
        "",
        "tariff:",
        "tariff:energy",
        "tariff:/x",
        "grid:energy/x",
        ":energy/periods[peak].rate",
    ],
)
def test_a_key_that_does_not_parse_gets_a_node_and_reads_malformed(key):
    config = {**_config(), "provenance": {key: _entry(0.2, "/kWh")}}
    assert C.cell_id(key) in Q.commercial_graph(config).nodes
    assert _broke(config, key).reason == C.MALFORMED


@pytest.mark.parametrize(
    "entry", [None, "pdf", 0.2, [], {}, {"source": "pdf:abc", "unit": "/kWh"}]
)
def test_a_malformed_entry_is_stale_never_a_crash(entry):
    config = _config()
    config["provenance"][PEAK_KEY] = entry
    assert _broke(config, PEAK_KEY).reason == C.MALFORMED


@pytest.mark.parametrize("source", [None, "", 7, "pdf:never-seen"])
def test_an_entry_with_no_or_an_unknown_source_keeps_its_own_edge_and_compares(source):
    config = _config()
    config["provenance"][PEAK_KEY]["source"] = source
    config["provenance"][PPA_KEY]["source"] = source
    g = Q.commercial_graph(config)
    stamps = {e.src: e.dst for e in g.edges if e.kind == "stamped_with"}
    assert len(stamps) == 5
    if source != "pdf:never-seen":
        assert stamps[C.cell_id(PEAK_KEY)] != stamps[C.cell_id(PPA_KEY)]
        assert stamps[C.cell_id(PEAK_KEY)] == f"source-unrecorded:{PEAK_KEY}"
    assert _state(config, PEAK_KEY) == "fresh"
    _item(config, "energy")["periods"][0]["rate"] = 0.09
    assert _state(config, PEAK_KEY) == "stale" and _state(config, PPA_KEY) == "fresh"


def test_a_cell_stamp_records_its_key_and_entry():
    config = _config()
    (edge,) = [
        e for e in Q.commercial_graph(config).edges if e.src == C.cell_id(PPA_KEY)
    ]
    assert edge.recorded == CellStamp(PPA_KEY, config["provenance"][PPA_KEY])


# ── `document_source` on the graph: master's check, row for row (P3, C5) ───


def _master_slot_of(commercial: dict, key: str):
    target, _, rest = key.partition(":")
    entity, _, path = rest.partition("/")
    return DC.read(
        commercial, target, entity, DC._where({"target_kind": target, "field": path})
    )


def _master_document_source(commercial: dict | None, key: str) -> dict | None:
    """`document_source` as master had it at 92324c52, verbatim."""
    entry = ((commercial or {}).get("provenance") or {}).get(key)
    if not isinstance(entry, dict):
        return None
    try:
        now = _master_slot_of(commercial, key)
    except (KeyError, StopIteration, IndexError, TypeError, AttributeError):
        return None
    return entry if DC._same(now, entry.get("value")) else None


def _edit_peak(c):
    _item(c, "energy")["periods"][0]["rate"] = 0.09


def _drop_entry(c):
    del c["provenance"][PEAK_KEY]


def _string_entry(c):
    c["provenance"][PEAK_KEY] = "pdf"


SAME_AS_MASTER = [
    lambda c: None,
    _edit_peak,
    _drop_entry,
    _string_entry,
    *[g[0] for g in GONE],
]


@pytest.mark.parametrize("change", SAME_AS_MASTER)
def test_document_source_reads_as_master_did(change):
    config = _config()
    change(config)
    keys = [*_config()["provenance"], "ppa:ppa1/floor", "tariff:energy/bogus"]
    assert [DC.document_source(config, k) for k in keys] == [
        _master_document_source(config, k) for k in keys
    ]
    assert DC.document_source(None, PEAK_KEY) is None


def _unit_moved(c):
    _item(c, "energy")["unit"] = "per_kw_month"


def _currency_moved(c):
    c["import_tariff"]["currency"] = "USD"


def _year_moved(c):
    c["contracts"][0]["base_year"] = 2031


def _valueless_entry_on_a_blank_cell(c):
    c["contracts"][0]["price"] = None
    del c["provenance"][PPA_KEY]["value"]


@pytest.mark.parametrize(
    ("change", "key"),
    [
        (_unit_moved, PEAK_KEY),
        (_currency_moved, PEAK_KEY),
        (_year_moved, PPA_KEY),
        (_valueless_entry_on_a_blank_cell, PPA_KEY),
    ],
)
def test_document_source_no_longer_speaks_where_master_was_wrong(change, key):
    """C5: master compared the value only, so it kept speaking for a cell whose unit, currency or money year
    had moved, and for an entry that records no value at all. Those are defects, not pinned."""
    config = _config()
    change(config)
    assert _master_document_source(config, key) is not None
    assert DC.document_source(config, key) is None


# ── On the stored config: no write, one read (C8, P5) ──────────────────────


def _digest() -> str:
    return json.dumps(DC.stored_commercial(), sort_keys=True, default=str)


def test_a_verdict_writes_nothing_and_reads_the_store_once(site, monkeypatch):  # noqa: F811
    _, args = _preview(
        _pdf(), [PEAK, EUR_PRICE], money_year=2030, confirm=["money_year"]
    )
    _dispatch(site, "apply_commercial_document_values", args)
    before = _digest()
    stored = _stored()
    passed = copy.deepcopy(stored)
    reads = []
    real = DC.stored_commercial
    monkeypatch.setattr(DC, "stored_commercial", lambda: reads.append(1) or real())
    keys = sorted(stored["provenance"])
    assert keys == [PPA_KEY, PEAK_KEY]
    assert [Q.cell_verdict(stored, k).state for k in keys] == ["fresh", "fresh"]
    assert [Q.cell_wire(stored, k)["state"] for k in keys] == ["fresh", "fresh"]
    assert [DC.document_source(stored, k) for k in keys] == [
        stored["provenance"][k] for k in keys
    ]
    assert [_master_document_source(stored, k) for k in keys] == [
        stored["provenance"][k] for k in keys
    ]
    assert reads == [] and stored == passed  # the caller's one read is the only one
    monkeypatch.undo()
    assert _digest() == before


def test_live_still_prunes_a_gone_cell_and_keeps_the_rest():
    """C9: `_live`, which apply prunes with, is unchanged by the graph."""
    config = _config()
    _no_item(config)
    live = DC._live(config, config["provenance"])
    assert PEAK_KEY not in live
    assert set(live) == set(config["provenance"]) - {PEAK_KEY}
