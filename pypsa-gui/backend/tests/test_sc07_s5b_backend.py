# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""SC-07 S5b backend: GK W1/V4, the my_previews early stop, and the S5a roof-words survivor."""

from __future__ import annotations

import time
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from services.site_scene import chat, proposals

PROJECT = SimpleNamespace(registry_key="proj-key", directory="/nonexistent")
OWNER, SITE = "owner-a", "campus"


def _entry(i, status="ready", owner=OWNER, site=SITE):
    return {
        "owner": owner,
        "site_id": site,
        "status": status,
        "input_hash": f"h{i}",
        "action": "bind_targets",
        "base_revision": i,
        "created": time.monotonic(),
        "bytes": 1,
    }


def _reference(entries, marked, receipts):
    """The S5a code with no early stop: every row's chain read, then the sort and the cap."""
    mine = {
        pid: e
        for (key, pid), e in entries.items()
        if key == PROJECT.registry_key and e["owner"] == OWNER and e["site_id"] == SITE
    }
    order = list(dict.fromkeys([*reversed(list(mine)), *reversed(marked)]))
    out = []
    for pid in order:
        entry, receipt = mine.get(pid), receipts.get(pid)
        if (
            entry is not None
            and receipt is not None
            and receipt["input_hash"] != entry["input_hash"]
        ):
            receipt = None
        if receipt is not None:
            status = "applied"
        elif pid in marked:
            status = "outcome_unknown"
        elif entry is not None and entry["status"] in ("ready", "applying"):
            status = "open"
        else:
            continue
        row = {"id": pid, "status": status}
        if entry is not None:
            row.update(action=entry["action"], base_revision=entry["base_revision"])
        out.append(row)
    out.sort(key=lambda r: r["status"] == "applied")
    return out[: chat.MAX_LIVE_PER_OWNER]


def _scenario(monkeypatch, entries, marked, receipts):
    store = OrderedDict(((PROJECT.registry_key, pid), e) for pid, e in entries)
    monkeypatch.setattr(proposals, "_entries", store)
    monkeypatch.setattr(
        chat,
        "_UNKNOWN",
        {(PROJECT.registry_key, OWNER, SITE, pid): None for pid in marked},
    )
    reads = []

    def committed(_directory, _site, pid):
        reads.append(pid)
        return receipts.get(pid)

    monkeypatch.setattr(proposals, "_committed", committed)
    expected = _reference(store, marked, receipts)
    return chat.my_previews(PROJECT, OWNER, SITE), expected, reads


def test_eight_open_rows_stop_the_chain_reads_with_the_same_output(monkeypatch):
    # Newest last in the map: 4 applied (newest), then 8 open, then 3 more applied (oldest).
    applied_old = [(f"a{i}", _entry(i)) for i in range(3)]
    open_rows = [(f"o{i}", _entry(10 + i)) for i in range(8)]
    applied_new = [(f"n{i}", _entry(30 + i)) for i in range(4)]
    receipts = {
        pid: {"input_hash": e["input_hash"]} for pid, e in applied_old + applied_new
    }
    got, expected, reads = _scenario(
        monkeypatch, applied_old + open_rows + applied_new, [], receipts
    )
    assert got == expected
    assert [r["status"] for r in got] == ["open"] * 8
    # Newest first: the 4 applied, then the 8 open; the 3 oldest applied are never read.
    assert reads == [p for p, _ in reversed(applied_new)] + [
        p for p, _ in reversed(open_rows)
    ]


def test_markers_count_as_rows_to_act_on(monkeypatch):
    open_rows = [(f"o{i}", _entry(i)) for i in range(6)]
    marked = ["u0", "u1", "u2"]  # markers with no live entry come after the entries
    got, expected, reads = _scenario(monkeypatch, open_rows, marked, {})
    assert got == expected
    assert [r["status"] for r in got] == ["open"] * 6 + ["outcome_unknown"] * 2
    assert "u0" not in reads  # markers are read newest first (u2, u1, u0)


@pytest.mark.parametrize("n_open", [0, 3, 6])
def test_fewer_than_the_cap_reads_every_row(monkeypatch, n_open):
    """At most 8 rows to act on (n_open, a0 whose receipt is another's, and u0): every row is read."""
    open_rows = [(f"o{i}", _entry(i)) for i in range(n_open)]
    applied = [(f"a{i}", _entry(20 + i)) for i in range(5)]
    other = [
        ("x0", _entry(99, owner="owner-b")),
        ("c0", _entry(98, status="cancelled")),
    ]
    receipts = {pid: {"input_hash": e["input_hash"]} for pid, e in applied}
    receipts["a0"] = {
        "input_hash": "another"
    }  # a receipt for another preview is not this one's
    got, expected, reads = _scenario(
        monkeypatch, open_rows + applied + other, ["u0"], receipts
    )
    assert got == expected
    assert sorted(reads) == sorted([p for p, _ in open_rows + applied] + ["c0", "u0"])


def test_exactly_the_cap_of_open_rows_is_listed(monkeypatch):
    """Kills a break one row early: the eighth open row is listed."""
    open_rows = [(f"o{i}", _entry(i)) for i in range(chat.MAX_LIVE_PER_OWNER)]
    got, expected, _ = _scenario(monkeypatch, open_rows, [], {})
    assert got == expected and len(got) == chat.MAX_LIVE_PER_OWNER


# ── S5a survivor: chat.py `_on_words` roof placement words ──────────────────


def test_roof_placement_reads_the_building_label():
    words = {"b1": "“Main hall”"}
    assert (
        chat._on_words(words, "roof:b1")
        == f"on the roof of {chat.feature_word(words, 'b1')}"
    )
    assert "b1" not in chat._on_words(words, "roof:b1")


def test_roof_of_a_building_gone_from_the_site_never_shows_its_id():
    said = chat._on_words({}, "roof:way-12345")
    assert said.startswith("on the roof of ")
    assert "way-12345" not in said


@pytest.mark.parametrize("on", ["ground", None, 7, "roofb1", ""])
def test_anything_else_is_on_the_ground(on):
    assert chat._on_words({"b1": "x"}, on) == "on the ground"
