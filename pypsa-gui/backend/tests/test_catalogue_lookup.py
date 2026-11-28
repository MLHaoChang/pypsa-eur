# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""expert-catalogue 05: the server's catalogue lookups agree with the frontend's.

`find_parameter` ranks as the ⌘K palette does and resolves former ids as
`findEntry` does; the asset-type matcher follows `matchType`. Each is driven by
a case table the frontend's vitests read too (`tests/data/`), so a change to
either copy fails one of the two suites.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from services.asset_schema import lookup

DATA = pathlib.Path(__file__).parent / "data"
SEARCH = json.loads((DATA / "catalogue_search_cases.json").read_text("utf-8"))
MATCH = json.loads((DATA / "asset_type_match_cases.json").read_text("utf-8"))

POOL = tuple(
    SimpleNamespace(**{**e, "aliases": tuple(e["aliases"])}) for e in SEARCH["entries"]
)


@pytest.mark.parametrize("case", SEARCH["search"], ids=lambda c: c["why"])
def test_search_ranks_as_the_palette_does(case):
    hits = lookup.search(
        case["query"],
        asset_type_id=case.get("asset_type"),
        function=case.get("function"),
        pool=POOL,
    )
    assert [e.id for e in hits] == case["ids"]


def test_the_table_covers_every_ranking_tier():
    whys = " ".join(c["why"] for c in SEARCH["search"])
    for tier in (
        "exact plain name",
        "alias",
        "model name",
        "word prefix",
        "short help",
        "guide text",
        "cap of 8",
        "tie",
    ):
        assert tier in whys, tier


@pytest.mark.parametrize("case", SEARCH["resolve"], ids=lambda c: c["id"])
def test_a_former_id_resolves_to_the_current_entry(case, monkeypatch):
    from services.asset_schema import catalogue as C

    monkeypatch.setattr(
        C,
        "entries",
        lambda: tuple(
            SimpleNamespace(
                **e, spec=SimpleNamespace(former_ids=tuple(e["former_ids"]))
            )
            for e in SEARCH["entries"]
        ),
    )
    lookup.clear_caches()
    try:
        entry, renamed = lookup.resolve(case["id"])
        assert (entry.id if entry else None) == case["current"]
        assert renamed == (entry is not None and entry.id != case["id"])
    finally:
        monkeypatch.undo()
        lookup.clear_caches()


@pytest.mark.parametrize("case", MATCH["cases"], ids=lambda c: c["why"])
def test_the_asset_type_matcher_follows_the_frontend(case):
    buses = case.get("buses", {})
    t = lookup.asset_type_of(case["cls"], case["row"], buses.get)
    assert (t.id if t else None) == case["backend"]
    if case["backend"] != case["frontend"]:
        # Only the site-membership port rule, which the server does not apply, may split them.
        assert "port" in case["why"]


def test_guide_text_is_searched_without_its_markdown():
    text = lookup._plain_text(
        "See [the tap ratio](guide/tap.md).\n```py\nhiddencode = 1\n```\n## **Bold** | cell > quote"
    )
    assert "the tap ratio" in text and "guide/tap" not in text
    assert "hiddencode" not in text
    assert not set(text) & set("#*_`>|")


def test_asset_types_of_lists_a_class_s_placeable_types_only(monkeypatch):
    import dataclasses

    gen = next(t for t in lookup.ASSET_TYPES if "Generator" in t.components)
    bus = next(t for t in lookup.ASSET_TYPES if "Generator" not in t.components)
    hidden = dataclasses.replace(gen, id="hiddenGen", internal=True)
    hidden_bus = dataclasses.replace(bus, id="hiddenBus", internal=True)
    monkeypatch.setattr(lookup, "ASSET_TYPES", (gen, hidden, bus, hidden_bus))
    assert lookup.asset_types_of("Generator") == [gen.id]
