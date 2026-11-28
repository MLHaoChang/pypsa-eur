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
