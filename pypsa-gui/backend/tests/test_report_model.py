"""
WP1 — `ReportDocument` is the one document shape every later package reads
and writes (the writer, the generator, the viewer, the round trip).

Two properties are load-bearing: a document survives JSON unchanged (the
store persists it as `v<N>.json` and hands it back), and a reader tolerates
keys it does not know (`extra="ignore"`, as `UploadMeta` does) so a v2 writer
does not break a v1 reader.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from models.report import (
    Bullets,
    Callout,
    Field,
    FigureRef,
    Paragraph,
    ReportDocument,
    ReportMeta,
    Section,
    TableRef,
)


def _doc() -> ReportDocument:
    return ReportDocument(
        report_id="0123456789abcdef",
        version=1,
        title="Energy Hub — reference design",
        created_at="2026-09-28T10:00:00+00:00",
        evidence_hash="a" * 64,
        profile_id="local-qwen",
        model="qwen3:8b",
        mode="generated",
        sections=[
            Section(
                section_id="executive_summary",
                heading="Executive summary",
                source="llm",
                status="ok",
                blocks=[
                    Paragraph(md="The hub achieves **3.2 h/yr** LOLE."),
                    Bullets(items=["one", "two"]),
                    TableRef(table_id="headline", caption="Target vs achieved"),
                    FigureRef(figure_id="frontier", caption="Cost vs ENS"),
                    Callout(kind="disclosure", text="Excludes shed cost."),
                    Field(key="Evidence hash", value="a" * 8),
                ],
                note=None,
                audit={"unverified": ["4.0 h/yr"],
                       "verified": [{"text": "3.2 h/yr", "path": "/mc/lole_h"}]},
            ),
            Section(
                section_id="frontier",
                heading="Frontier",
                source="code",
                status="not_established",
                blocks=[],
                note="frontier stage not run",
            ),
        ],
        tables={
            "headline": {
                "table_id": "headline",
                "columns": ["metric", "target", "achieved"],
                "rows": [["ENS ‱", "1", "0.8"]],
                "caption": "Headline",
                "source_path": "/eh/headline",
            },
        },
        figures={
            "frontier": {
                "figure_id": "frontier",
                "png_file": "frontier.png",
                "caption": "Cost vs ENS",
                "source_path": "/eh/sections/frontier",
            },
        },
    )


def test_json_round_trip_is_identity():
    doc = _doc()
    text = doc.model_dump_json()
    back = ReportDocument.model_validate_json(text)
    assert back == doc
    assert back.model_dump() == doc.model_dump()
    # And through a plain dict, which is what the store's json.loads yields.
    assert ReportDocument.model_validate(json.loads(text)) == doc


def test_defaults_are_the_pinned_ones():
    doc = _doc()
    assert doc.schema_version == 1
    assert doc.language == "en"
    assert doc.template_file_id is None
    empty = Section(section_id="s", heading="S", source="code", status="skipped")
    assert empty.blocks == []
    assert empty.note is None
    assert empty.audit.unverified == []
    assert empty.audit.verified == []
    # Increment 3 (WP12): the round trip's additive fields default to "nothing".
    assert empty.pending_instruction is None
    assert empty.comments == []
    meta = ReportMeta(
        report_id="0123456789abcdef", title="t",
        created_at="2026-09-28T10:00:00+00:00",
        updated_at="2026-09-28T10:05:00+00:00",
        latest_version=1, mode="evidence_only", evidence_hash="b" * 64,
    )
    assert meta.roundtrip_file_id is None


def test_unknown_keys_are_ignored_at_every_level():
    raw = json.loads(_doc().model_dump_json())
    raw["future_top_level"] = {"x": 1}
    raw["sections"][0]["future_section_key"] = "y"
    raw["sections"][0]["blocks"][0]["future_block_key"] = "z"
    raw["sections"][0]["audit"]["future_audit_key"] = 1
    raw["tables"]["headline"]["future_table_key"] = 1
    raw["figures"]["frontier"]["future_figure_key"] = 1
    back = ReportDocument.model_validate(raw)
    assert back == _doc()
    assert "future_top_level" not in back.model_dump()


def test_blocks_are_discriminated_on_type():
    sec = Section.model_validate({
        "section_id": "s", "heading": "S", "source": "code", "status": "ok",
        "blocks": [
            {"type": "paragraph", "md": "p"},
            {"type": "bullets", "items": ["a"]},
            {"type": "table_ref", "table_id": "t"},
            {"type": "figure_ref", "figure_id": "f"},
            {"type": "callout", "kind": "gap", "text": "g"},
            {"type": "field", "key": "k", "value": "v"},
        ],
    })
    assert [type(b) for b in sec.blocks] == [
        Paragraph, Bullets, TableRef, FigureRef, Callout, Field,
    ]
    assert sec.blocks[2].caption is None
    with pytest.raises(ValidationError):
        Section.model_validate({
            "section_id": "s", "heading": "S", "source": "code", "status": "ok",
            "blocks": [{"type": "hologram", "md": "p"}],
        })


@pytest.mark.parametrize("field, bad", [
    ("status", "done"),
    ("source", "robot"),
])
def test_section_enums_are_closed(field, bad):
    payload = {"section_id": "s", "heading": "S", "source": "code", "status": "ok"}
    payload[field] = bad
    with pytest.raises(ValidationError):
        Section.model_validate(payload)


def test_callout_kind_and_mode_are_closed():
    with pytest.raises(ValidationError):
        Callout(kind="warning", text="x")
    raw = _doc().model_dump()
    raw["mode"] = "handwritten"
    with pytest.raises(ValidationError):
        ReportDocument.model_validate(raw)
    raw = _doc().model_dump()
    raw["schema_version"] = 2
    with pytest.raises(ValidationError):
        ReportDocument.model_validate(raw)


def test_report_meta_round_trips_and_ignores_unknown_keys():
    meta = ReportMeta(
        report_id="0123456789abcdef", title="t",
        created_at="2026-09-28T10:00:00+00:00",
        updated_at="2026-09-28T10:05:00+00:00",
        latest_version=2, mode="evidence_only", evidence_hash="b" * 64,
        profile_id=None, model=None,
    )
    raw = json.loads(meta.model_dump_json())
    raw["future"] = 1
    assert ReportMeta.model_validate(raw) == meta


def test_round_trip_fields_survive_json():
    sec = Section(section_id="s", heading="S", source="user_edit", status="ok",
                  pending_instruction="tighten the wording", comments=["a", "b"])
    back = Section.model_validate_json(sec.model_dump_json())
    assert back.pending_instruction == "tighten the wording"
    assert back.comments == ["a", "b"]
    meta = ReportMeta(
        report_id="0123456789abcdef", title="t",
        created_at="2026-09-28T10:00:00+00:00",
        updated_at="2026-09-28T10:05:00+00:00",
        latest_version=3, mode="generated", evidence_hash="b" * 64,
        roundtrip_file_id="0123456789abcdef",
    )
    assert ReportMeta.model_validate_json(meta.model_dump_json()).roundtrip_file_id == \
        "0123456789abcdef"
