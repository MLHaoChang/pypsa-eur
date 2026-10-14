# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Every item in `docs/superpowers/OPEN-ITEMS.md` has its own number.

Findings, plans, commits and tests cite items by number, and the file says
numbering is never reused: a closed item keeps its number in "Closed since the
2026-09-12 pass". On 2026-10-13 two branches had each added an item 12 and an
item 13, so "item 12" named two different defects. Open items are `### N.`
headings and closed items are `* **N**` bullets; both lists share one
numbering.

Here rather than in the GUI backend suite because the `Unit` job runs on
every pull request, while `GUI backend` is path-filtered to
`pypsa-gui/backend/**` and skips a docs-only change to this file.
"""

import re
from collections import Counter
from pathlib import Path

OPEN_ITEMS = Path(__file__).parent.parent / "docs" / "superpowers" / "OPEN-ITEMS.md"

_OPEN = re.compile(r"^### (\d+)\.", re.MULTILINE)
_CLOSED = re.compile(r"^\* \*\*(\d+)\b", re.MULTILINE)


def _numbers(text: str) -> list[int]:
    return [int(n) for n in _OPEN.findall(text) + _CLOSED.findall(text)]


def test_every_open_items_entry_has_a_unique_number():
    numbers = _numbers(OPEN_ITEMS.read_text(encoding="utf-8"))
    assert numbers, "no items found: the heading or bullet format changed"
    duplicates = sorted(n for n, k in Counter(numbers).items() if k > 1)
    assert not duplicates, (
        f"item numbers used more than once in {OPEN_ITEMS.name}: {duplicates}. "
        "Give the newer item the next free number and add a renumbering note."
    )


def test_the_check_sees_both_lists():
    text = "### 7. open\n\n* **7** — closed\n"
    assert _numbers(text) == [7, 7]
