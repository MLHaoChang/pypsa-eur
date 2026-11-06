"""Interpretation markers (AA-03 S5): wording no checked evidence can support.

A model interpretation that claims a whole population, a cause or an optimum is
withheld before any reviewer call. Checked facts cannot establish these: a
population claim is stated only as an S4 predicate with its software line, a
causal claim has no evidence kind at all, and optimality needs checked solver
status (ticket 10).

The lists are English only (owner decision AA03-FREEZE-1, item 5). Wording in
other languages is not detected; that stays pending under ticket 09.

Matching runs on NFKC-normalized, casefolded text with word boundaries. A hyphen
or apostrophe is a boundary, and the words of a multi-word marker may be split
by any run of whitespace or hyphens.
"""

from __future__ import annotations

import re
import unicodedata

POPULATION = (
    "all",
    "every",
    "each",
    "none",
    "nothing",
    "nobody",
    "always",
    "never",
    "only",
    "entire",
    "whole",
    "largest",
    "smallest",
    "highest",
    "lowest",
    "biggest",
    "most",
    "least",
    "top",
    "bottom",
    "maximum",
    "minimum",
)

CAUSAL = (
    "because",
    "due to",
    "caused",
    "causes",
    "causing",
    "drives",
    "driven by",
    "leads to",
    "led to",
    "results in",
    "resulting in",
    "as a result",
    "therefore",
    "hence",
    "thus",
    "consequently",
    "owing to",
    "explains",
)

OPTIMALITY = (
    "optimal",
    "optimum",
    "optimally",
    "best",
    "cheapest",
    "least-cost",
    "proven",
    "guaranteed",
)

LISTS = {"population": POPULATION, "causal": CAUSAL, "optimality": OPTIMALITY}


def _pattern(marker: str) -> str:
    words = re.split(r"[\s-]+", marker)
    return r"\b" + r"[\s-]+".join(re.escape(word) for word in words) + r"\b"


_PATTERNS = {
    name: re.compile("|".join(_pattern(m) for m in markers))
    for name, markers in LISTS.items()
    if markers
}


def find(text: str) -> str | None:
    """The first list whose wording `text` uses, or None."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for name, pattern in _PATTERNS.items():
        if pattern.search(normalized):
            return name
    return None
