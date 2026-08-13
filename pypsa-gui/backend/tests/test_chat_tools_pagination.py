"""
Improvement #16 — a read the agent can finish.

`list_components` returned a bare list. Past 200 rows `_truncate_result`
replaced it with `{"_truncated": True, "total": N, "sample": [...200]}` —
which tells the agent how much it is missing and gives it no way whatsoever
to ask for the rest. On a 400-bus network the agent simply could not
enumerate the buses, and nothing in the tool surface said why.

Worse than the missing rows: a list of exactly 200 and a list of 200-of-5000
were indistinguishable at the call site until the truncation marker
appeared, so "did I see everything?" had no reliable answer.

The fix is an explicit envelope — `{items, total_count, offset, returned,
has_more}` — returned ALWAYS, not only when paginating. One shape is easier
for a model to reason about than a polymorphic one, and a response that
always carries `total_count` answers "did I see everything?" on every call
rather than only on the calls that overflowed.
"""
from __future__ import annotations

import pypsa
import pytest

from services import chat_tools


@pytest.fixture
def wide_network(install_network):
    """250 buses — comfortably past the 200-row truncation cliff."""
    n = pypsa.Network()
    for i in range(250):
        n.add("Bus", f"B{i:03d}")
    install_network(n, name="WideNet")
    return n


def test_a_small_read_reports_that_it_is_complete(install_network):
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    install_network(n, name="SmallNet")

    out = chat_tools.list_components("Bus")

    assert out["total_count"] == 2
    assert out["returned"] == 2
    assert out["offset"] == 0
    assert out["has_more"] is False
    assert [r["name"] for r in out["items"]] == ["B1", "B2"]


def test_a_wide_read_says_how_much_is_left(wide_network):
    """
    The whole point: the agent is told the total and that more exists,
    rather than being handed a silently shortened list.
    """
    out = chat_tools.list_components("Bus")

    assert out["total_count"] == 250
    assert out["has_more"] is True
    assert 0 < out["returned"] < 250


def test_a_page_stays_inside_the_result_budget(wide_network):
    """
    Row count is the wrong unit. A Bus row is ~10x a Carrier row, so a page
    capped only by rows serialises to 45 KB for Buses and gets replaced
    wholesale by `_truncate_result`'s preview string — the opaque blob this
    item exists to remove, arrived at by a different route.
    """
    import json
    out = chat_tools.list_components("Bus")

    assert len(json.dumps(out["items"], default=str)) <= chat_tools.MAX_PAGE_CHARS + 512
    # The page was cut by bytes, well before the row cap.
    assert out["returned"] < chat_tools.DEFAULT_PAGE_SIZE
    assert out["limit_clamped_to"] == out["returned"]


def test_walking_offset_by_returned_reaches_every_row(wide_network):
    """
    The contract the tool description sells to the agent: re-call with
    offset += returned until has_more is false. It has to terminate, and
    cover the network exactly once.
    """
    names: list[str] = []
    offset = 0
    for _ in range(100):  # generous bound; a stuck page would blow it
        page = chat_tools.list_components("Bus", offset=offset)
        names.extend(r["name"] for r in page["items"])
        offset += page["returned"]
        if not page["has_more"]:
            break
        assert page["returned"] > 0, "a zero-row page would never terminate"
    else:
        pytest.fail("pagination did not terminate")

    assert len(names) == 250
    assert len(set(names)) == 250
    assert names == sorted(names)


def test_a_single_oversized_row_still_advances(install_network):
    """
    The non-termination trap. If one row alone exceeds the byte budget, a
    strict budget check would return an empty page forever and the agent
    could never get past it. The first row always ships.
    """
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Carrier", "c" + "x" * (chat_tools.MAX_PAGE_CHARS * 2))
    n.add("Carrier", "small")
    install_network(n, name="FatRowNet")

    out = chat_tools.list_components("Carrier", limit=5)

    assert out["returned"] >= 1
    assert out["has_more"] is True


def test_a_page_past_the_end_is_empty_but_still_honest(wide_network):
    out = chat_tools.list_components("Bus", offset=9_999)

    assert out["items"] == []
    assert out["returned"] == 0
    assert out["has_more"] is False
    # total_count must survive an out-of-range page — it is how the agent
    # recovers from having walked off the end.
    assert out["total_count"] == 250


def test_an_explicit_limit_is_honoured(wide_network):
    out = chat_tools.list_components("Bus", offset=10, limit=5)

    assert out["returned"] == 5
    assert out["has_more"] is True
    assert [r["name"] for r in out["items"]] == [f"B{i:03d}" for i in range(10, 15)]


def test_a_limit_wider_than_the_network_is_not_an_error(install_network):
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="TinyNet")

    out = chat_tools.list_components("Bus", limit=500)

    assert out["returned"] == 1
    assert out["has_more"] is False


def test_an_oversized_limit_is_clamped_rather_than_refused(wide_network):
    """
    A model that asks for everything at once should get a page, not a 400.
    Refusing would just cost a turn to re-ask with a number it has to guess.

    `limit_clamped_to` reports what actually shipped rather than which bound
    bit — the agent's next call needs the number it can act on, and whether
    the row cap or the byte budget produced it is not information it can use.
    """
    out = chat_tools.list_components("Bus", limit=10_000)

    assert out["returned"] <= chat_tools.MAX_PAGE_SIZE
    assert out["limit_clamped_to"] == out["returned"]
    assert out["has_more"] is True


def test_a_negative_offset_is_refused(wide_network):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        chat_tools.list_components("Bus", offset=-1)
    assert exc.value.status_code == 400


def test_a_zero_or_negative_limit_is_refused(wide_network):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        chat_tools.list_components("Bus", limit=0)
    assert exc.value.status_code == 400


def test_an_unknown_component_class_still_fails_before_paginating(install_network):
    n = pypsa.Network()
    install_network(n, name="EnumNet")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        chat_tools.list_components("Reactor")
    assert exc.value.status_code == 400


def test_the_envelope_escapes_the_blind_list_truncation(wide_network):
    """
    The envelope has to be a dict, because `_truncate_result` replaces any
    list longer than 200 with `{_truncated, total, sample}` — the exact
    opaque shape this item exists to remove. A page of 200 sits right on
    that boundary.
    """
    from services import chat_service
    out = chat_service._truncate_result(chat_tools.list_components("Bus"))

    assert "_truncated" not in out
    assert out["total_count"] == 250
    assert out["has_more"] is True


def test_list_all_timeseries_paginates_the_same_way(install_network):
    import pandas as pd
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.add("Bus", "B1")
    for i in range(4):
        n.add("Load", f"L{i}", bus="B1", p_set=[1.0, 2.0, 3.0])
    install_network(n, name="TsNet")

    out = chat_tools.list_all_timeseries()

    assert "items" in out
    assert out["total_count"] == len(out["items"])
    assert out["offset"] == 0
    assert out["has_more"] is False
