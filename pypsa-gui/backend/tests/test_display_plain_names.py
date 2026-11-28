"""Display replacement on the live-voice TTS path (expert-catalogue spec §7.3).

`chat_plain_names.display_plain_names` on the case table it shares with the
frontend (`frontend/src/catalogue/displayPlainNames.test.ts`), and a spoken
checked answer carrying the plain names the chat shows.
"""

# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from services import chat_plain_names as P
from services import live_speech as ls
from services.asset_schema import fields, lookup
from tests.test_live_speech_output_lc07 import (  # noqa: F401 — fixtures
    _checked_frames,
    _reset,
    _spoken_turn,
    transport,
)

TABLE = json.loads(
    (
        pathlib.Path(__file__).parent / "data" / "display_plain_names_cases.json"
    ).read_text("utf-8")
)


@pytest.mark.parametrize("case", TABLE["cases"], ids=[c["why"] for c in TABLE["cases"]])
def test_case_table(case):
    assert P.display_plain_names(case["text"], case.get("user", "")) == case["want"]


def test_the_words_are_the_catalogue_lookup():
    assert P.display_plain_names("`e_nom`") == lookup.plain_name("Store", "e_nom")
    assert P.display_plain_names("`p_set`") == lookup.plain_name("Load", "p_set")


_PANEL_CLASSES = {
    "Generator": fields.GENERATOR,
    "StorageUnit": fields.STORAGE_UNIT,
    "Store": fields.STORE,
    "Link": fields.LINK,
    "Line": fields.LINE,
    "Transformer": fields.TRANSFORMER,
    "Load": fields.LOAD,
    "Bus": fields.BUS,
    "Carrier": fields.CARRIER,
    "GlobalConstraint": fields.GLOBAL_CONSTRAINT,
    "ShuntImpedance": fields.SHUNT_IMPEDANCE,
}


def test_chat_keeps_raw_exactly_the_names_the_panel_labels_differently():
    """The set is derived: every snake_case name whose Properties-panel label
    (`FieldSpec.label`) is not the catalogue word on some class. It fails when a new
    name drifts, or once the panel shows the catalogue's word (expert-catalogue
    slice 03): then drop the name from `PANEL_NAMES_DIFFER` here and in
    `displayPlainNames.ts`, and from `panel_names_differ` in the case table."""
    derived = {
        spec.key
        for cls, specs in _PANEL_CLASSES.items()
        for spec in specs
        if "_" in spec.key
        and lookup.plain_name(cls, spec.key) not in (None, spec.label)
    }
    assert P.PANEL_NAMES_DIFFER == derived
    assert set(TABLE["panel_names_differ"]) == derived


SPOKEN = "Set `e_nom` to 200 MWh; `p_nom` stays."


def test_a_spoken_answer_carries_the_plain_names(transport, monkeypatch):
    tid, sid, token, _, _ = _spoken_turn(
        monkeypatch, frames=_checked_frames(SPOKEN), approved=SPOKEN
    )
    out = ls.claim_output(tid, token=token, session_id=sid)["output"]
    said = "Set Energy capacity to 200 MWh; `p_nom` stays."
    assert out["text"] == said
    assert out["sha256"] == hashlib.sha256(said.encode("utf-8")).hexdigest()
    assert out["chars"] == len(said)


def test_a_name_the_user_said_is_spoken_as_said(transport, monkeypatch):
    tid, sid, token, _, _ = _spoken_turn(
        monkeypatch,
        frames=_checked_frames(SPOKEN),
        approved=SPOKEN,
        text="what is e_nom",
    )
    assert ls.claim_output(tid, token=token, session_id=sid)["output"]["text"] == SPOKEN
