"""
Business case 15b: the strip's "what scales" sentence is built from
`businessCaseVolumeGroups.json`. This pins its ids to the engine's volume classes
(exact sets), so a new stream or linked kind fails here until the sentence names it.
"""

from __future__ import annotations

import json
from pathlib import Path

import services.finance.business_case as BCM

GROUPS = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/pages/results/investment/businessCaseVolumeGroups.json"
)


def _ids(section: str, key: str) -> list[str]:
    groups = json.loads(GROUPS.read_text())[section]
    return [i for g in groups for i in g.get(key, [])]


def _streams(cls: str) -> set[str]:
    return {s for s, c in BCM.STREAM_VOLUME_CLASS.items() if c == cls}


def test_the_sentence_names_exactly_the_engine_s_volume_classes():
    for section, kinds, cls in (
        ("scales", BCM.VOLUME_KINDS, "scale"),
        ("kept", BCM.FIXED_KINDS, "fixed"),
        ("refuses", BCM.UNDETERMINED_KINDS, "undetermined"),
    ):
        k, s = _ids(section, "kinds"), _ids(section, "streams")
        assert len(k) == len(set(k)) and len(s) == len(set(s)), section
        assert set(k) == set(kinds), section
        assert set(s) == _streams(cls), section
