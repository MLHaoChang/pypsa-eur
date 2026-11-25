"""control-strategy 02 (UX 29 condition 1): every setting gridspine's island
schema lets a converter mode or a UPS carry has words in the frontend's one map
(`CONTROL_FIELD_WORDS` in pages/islandWords.ts), so a new field without words
fails here rather than showing its key in the Controls block."""

from __future__ import annotations

import pathlib
import re

from gridspine.schema import island as schema

WORDS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "pages"
    / "islandWords.ts"
)


def _keys(block: str) -> set[str]:
    body = re.search(
        r"CONTROL_FIELD_WORDS[^=]*=\s*\{(.*?)\n\}", WORDS.read_text(), re.S
    )
    assert body, f"no {block} in {WORDS}"
    return set(re.findall(r"^\s{2}(\w+):", body.group(1), re.M))


def _block_fields() -> set[str]:
    return {
        "pf_rated",
        *schema._PF_CHOICE,
        *schema._UPS_REQUIRED,
        *(f for fs in schema._CONTROL_FIELDS.values() for f in fs),
    }


def test_every_schema_control_setting_has_words():
    fields = _block_fields()
    words = _keys("CONTROL_FIELD_WORDS")
    assert fields <= words, sorted(fields - words)


def test_the_ups_fields_the_block_edits_are_the_schema_s():
    ups = re.search(r"UPS_CONTROL_FIELDS = \[(.*?)\]", WORDS.read_text()).group(1)
    assert re.findall(r"'(\w+)'", ups) == list(schema._UPS_REQUIRED)


def test_every_limit_the_block_can_meet_has_words():
    """A 422 names its limit in gridspine's wording (`_SCALAR`); the block maps
    each to words (`LIMIT_WORDS`, UX 30), so a new wording fails here rather
    than falling back to "was not accepted"."""
    body = re.search(r"LIMIT_WORDS[^=]*=\s*\{(.*?)\n\}", WORDS.read_text(), re.S)
    assert body, f"no LIMIT_WORDS in {WORDS}"
    keys = {
        q or bare
        for q, bare in re.findall(r"^\s{2}(?:'([^']+)'|([\w-]+)):", body.group(1), re.M)
    }
    wordings = {schema._SCALAR[f][1] for f in _block_fields() if f in schema._SCALAR}
    assert wordings, "no block field has a scalar check"
    assert wordings <= keys, sorted(wordings - keys)
