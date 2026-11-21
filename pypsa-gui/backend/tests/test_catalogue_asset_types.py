"""
`asset_types.py` holds the rows of `frontend/src/utils/assetTypes.ts` `TYPES` as
data (spec expert-catalogue §3.3). Until slice 04 makes the frontend read the
catalogue, this pins the two tables together: same ids in the same match
order, same icon, same label where the TypeScript label is a literal, and the
same carrier patterns.
"""

from __future__ import annotations

import pathlib
import re

from services.asset_schema.asset_types import ASSET_TYPES

TS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "utils"
    / "assetTypes.ts"
)
ROW = re.compile(
    r"id: '(?P<id>\w+)', label: (?:'(?P<label>[^']*)'|\w+), color: '#[0-9a-f]{6}', icon: '(?P<icon>\w+)'"
)


def _ts_rows() -> list[re.Match]:
    text = TS.read_text("utf-8")
    body = text[text.index("const TYPES: AssetTypeSpec[] = [") :]
    return list(ROW.finditer(body[: body.index("\n]\n")]))


def test_same_ids_in_the_same_match_order():
    assert [m["id"] for m in _ts_rows()] == [t.id for t in ASSET_TYPES]


def test_same_icons_and_literal_labels():
    by_id = {t.id: t for t in ASSET_TYPES}
    for m in _ts_rows():
        t = by_id[m["id"]]
        assert t.icon == m["icon"], t.id
        if m["label"] is not None:
            assert t.plain_name == m["label"], t.id


def test_every_literal_carrier_pattern_in_the_ts_table_is_ported():
    """Each inline `/…/i` regex of the TypeScript table appears verbatim as a pattern here."""
    text = TS.read_text("utf-8")
    body = text[text.index("const TYPES: AssetTypeSpec[] = [") :]
    body = body[: body.index("\n]\n")]
    ts_patterns = set(re.findall(r"carrier: /((?:[^/\\]|\\.)+)/i", body))
    ours = {
        p
        for t in ASSET_TYPES
        for m in t.match
        for p in (m.carrier, m.generic_unless)
        if p
    }
    assert ts_patterns <= ours, sorted(ts_patterns - ours)
