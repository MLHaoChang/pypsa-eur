"""
`_slugify_label` was rewritten to draw its characters from `_SLUG_ALPHABET`
instead of substituting over the label with `_LABEL_RE`. The point was
provenance, not behaviour: the slug reaches a directory name, and a regex
substitution is not a barrier CodeQL models, so the label stayed tainted all
the way to ~30 `py/path-injection` sinks.

A rewrite that changes the OUTPUT, though, renames snapshots. Directory names
are the ids clients hold, so this compares the new implementation against the
old one over a corpus rather than against expectations written by the same
person who wrote the new code.

That is not hypothetical here: the first draft emitted one dash per rejected
character and missed the `+` in `[^A-Za-z0-9_\\-]+`, which collapses a RUN to a
single dash. Every label with two adjacent spaces would have slugified
differently. This file is what catches that.
"""
from __future__ import annotations

import re

import pytest

from routers.snapshots import _SLUG_ALPHABET, _slugify_label

# The implementation this replaced, verbatim.
_OLD_LABEL_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def _old_slugify(label: str) -> str:
    slug = _OLD_LABEL_RE.sub("-", (label or "").strip())[:32].strip("-_.")
    return slug or "snapshot"


CORPUS = [
    "", " ", "   ", None,
    "base", "Base Case", "base  case", "base   case",       # the run cases
    "a  b", "a - b", "a--b", "a_b", "a.b", "a/b", "a\\b",
    "-leading", "trailing-", "--both--", "._-mixed-_.",
    "Ünïcödé läbel", "日本語のラベル", "emoji 🎉 label",
    "x" * 40, "y" * 32, ("z " * 30),
    "tabs\tand\nnewlines", "semi;colon", "quote'd", 'double"q',
    "%2f", "../..", "..", ".", "null\x00byte",
    "MiXeD CaSe 123", "0123456789", "_", "-", "___",
]


@pytest.mark.parametrize("label", CORPUS)
def test_the_rewrite_slugifies_exactly_as_the_regex_did(label):
    assert _slugify_label(label) == _old_slugify(label), (
        f"slug changed for {label!r} — this renames existing snapshots"
    )


def test_every_output_character_comes_from_the_alphabet():
    """The property the rewrite exists to make evident."""
    for label in CORPUS:
        for ch in _slugify_label(label):
            assert ch in _SLUG_ALPHABET, (
                f"{ch!r} escaped into a slug for {label!r}"
            )


def test_the_alphabet_and_the_old_pattern_still_agree():
    """
    Guard against drift: `_SLUG_ALPHABET` is the character class the old regex
    used, as data. If someone widens one they must widen the other, and this
    is what says so.
    """
    for ch in _SLUG_ALPHABET:
        assert not _OLD_LABEL_RE.match(ch), f"{ch!r} is in the alphabet but the regex rejects it"
    for code in range(32, 127):
        ch = chr(code)
        if not _OLD_LABEL_RE.match(ch):
            assert ch in _SLUG_ALPHABET, f"{ch!r} passes the regex but is not in the alphabet"


def test_a_slug_is_never_empty_and_never_over_the_cap():
    for label in CORPUS:
        slug = _slugify_label(label)
        assert slug, f"empty slug for {label!r}"
        assert len(slug) <= 32, f"slug over cap for {label!r}: {len(slug)}"
