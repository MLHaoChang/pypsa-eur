"""DC 07 S4: Results › Summary's Plan vs operation card names every metric the
gap service defines, and no other (OP_GAP_WORDS in OperationalGap.tsx)."""

from __future__ import annotations

import pathlib
import re

from services.solver.operational_gap import DEFINITIONS

_CARD = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend/src/pages/results/OperationalGap.tsx"
)


def _word_keys() -> set[str]:
    src = _CARD.read_text()
    body = re.search(r"export const OP_GAP_WORDS: Record<.*?> = \{(.*?)\n\}", src, re.S)
    assert body, "OP_GAP_WORDS not found"
    return set(re.findall(r"^  (\w+): \{", body.group(1), re.M))


def test_the_card_words_cover_exactly_the_service_metrics():
    assert _word_keys() == set(DEFINITIONS)
