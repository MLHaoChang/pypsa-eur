"""
WP1 — the per-project report store: `reports/<report_id>/{meta.json, v<N>.json,
figures/<figure_id>.png}` under the project directory.

The rules under test: a version file is never overwritten (a regenerate is a
new `v<N+1>.json`, the earlier one stays readable); the id is validated at the
service boundary with an anchored regex, as upload ids are, so a path can only
ever be joined from a 16-hex-character token; an unreadable `meta.json` is
skipped with one warning rather than hiding every other report.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re

import pytest

from models.report import Paragraph, ReportDocument, Section
from services.reports import store


def _doc(report_id: str = "0123456789abcdef", title: str = "Report") -> ReportDocument:
    return ReportDocument(
        report_id=report_id, version=1, title=title,
        created_at="2026-09-28T10:00:00+00:00", evidence_hash="a" * 64,
        profile_id=None, model=None, mode="evidence_only",
        sections=[Section(section_id="s", heading="S", source="code", status="ok",
                          blocks=[Paragraph(md="v1 text")])],
        tables={}, figures={},
    )


@pytest.fixture
def project_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "proj"
    d.mkdir()
    (d / "network.nc").write_bytes(b"fake nc")
    return d


# ── ids ─────────────────────────────────────────────────────────────────────

def test_new_report_id_is_sixteen_lowercase_hex_and_fresh():
    a, b = store.new_report_id(), store.new_report_id()
    assert re.fullmatch(store.REPORT_ID_RE, a)
    assert re.fullmatch(store.REPORT_ID_RE, b)
    assert a != b


@pytest.mark.parametrize("bad", [
    "..", "../x", "0123456789abcde/", "a/b", "0123456789abcde",       # 15 chars
    "0123456789abcdef0", "0123456789ABCDEF", "0123456789abcdef\n",
    "", "0123456789abcdeg", None, 42,
])
def test_a_malformed_id_is_refused_before_any_path_is_joined(project_dir, bad):
    with pytest.raises(store.InvalidReportId):
        store.load_report(project_dir, bad)
    with pytest.raises(store.InvalidReportId):
        store.delete_report(project_dir, bad)
    with pytest.raises(store.InvalidReportId):
        store.figure_path(project_dir, bad, "fig")
    with pytest.raises(store.InvalidReportId):
        store.create_report(project_dir, _doc().model_copy(update={"report_id": bad}))
    assert not (project_dir / "reports").exists()


def test_the_id_errors_are_value_errors_the_router_can_map():
    assert issubclass(store.InvalidReportId, ValueError)
    assert issubclass(store.ReportNotFound, ValueError)


def test_an_accepted_id_is_rebuilt_from_the_alphabet_and_equals_the_input():
    """CodeQL `py/path-injection` (PR #64): the string that names a directory
    is re-spelled from `_ID_ALPHABET`, and that re-spelling is the identity
    for every id the regexes accept — the fix must not rename anything."""
    for rid in ("0123456789abcdef", "ffffffffffffffff", "00000000deadbeef"):
        out = store.validate_report_id(rid)
        assert out == rid and out is not rid
    for fid in ("fmea_pareto", "Frontier-2", "a", "Z" * 64, "_-_"):
        out = store.validate_figure_id(fid)
        assert out == fid and out is not fid
    assert set("0123456789abcdef") <= set(store._ID_ALPHABET)
    assert set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-") \
        <= set(store._ID_ALPHABET)


# ── create / save / load ────────────────────────────────────────────────────

def test_create_writes_meta_and_v1(project_dir):
    meta = store.create_report(project_dir, _doc())
    rdir = project_dir / "reports" / "0123456789abcdef"
    assert (rdir / "meta.json").exists()
    assert (rdir / "v1.json").exists()
    assert not list(rdir.glob("*.tmp"))
    assert meta.report_id == "0123456789abcdef"
    assert meta.latest_version == 1
    assert meta.title == "Report"
    assert meta.mode == "evidence_only"
    assert meta.evidence_hash == "a" * 64
    assert meta.created_at == "2026-09-28T10:00:00+00:00"
    assert meta.updated_at
    on_disk = json.loads((rdir / "v1.json").read_text(encoding="utf-8"))
    assert on_disk["report_id"] == "0123456789abcdef"
    assert on_disk["version"] == 1


def test_create_refuses_to_overwrite_an_existing_report(project_dir):
    store.create_report(project_dir, _doc())
    with pytest.raises(store.ReportExists):
        store.create_report(project_dir, _doc(title="second"))
    assert store.load_report(project_dir, "0123456789abcdef").title == "Report"


def test_save_version_bumps_and_never_overwrites(project_dir):
    store.create_report(project_dir, _doc())
    v1_bytes = (project_dir / "reports" / "0123456789abcdef" / "v1.json").read_bytes()

    doc2 = _doc().model_copy(update={
        "sections": [Section(section_id="s", heading="S", source="llm", status="ok",
                             blocks=[Paragraph(md="v2 text")])],
    })
    # The caller does not pick the number: whatever `version` it carries, the
    # store assigns latest + 1 and echoes it.
    doc2.version = 1
    assert store.save_version(project_dir, doc2) == 2
    doc3 = doc2.model_copy(update={"version": 1})
    assert store.save_version(project_dir, doc3) == 3

    rdir = project_dir / "reports" / "0123456789abcdef"
    assert sorted(p.name for p in rdir.glob("v*.json")) == ["v1.json", "v2.json", "v3.json"]
    assert (rdir / "v1.json").read_bytes() == v1_bytes
    assert json.loads((rdir / "v2.json").read_text(encoding="utf-8"))["version"] == 2
    meta = store.load_meta(project_dir, "0123456789abcdef")
    assert meta.latest_version == 3


def test_save_version_refuses_a_stray_file_at_the_next_number(project_dir):
    """
    A `v2.json` that exists on disk but is not in the meta (a crashed or
    hand-copied write) is never replaced — the write raises instead.
    """
    store.create_report(project_dir, _doc())
    rdir = project_dir / "reports" / "0123456789abcdef"
    (rdir / "v2.json").write_text("{\"stray\": true}", encoding="utf-8")
    # The store looks at the files, not only the meta, so the next number is 3.
    assert store.save_version(project_dir, _doc()) == 3
    assert json.loads((rdir / "v2.json").read_text(encoding="utf-8")) == {"stray": True}


def test_load_latest_and_specific_versions(project_dir):
    store.create_report(project_dir, _doc())
    doc2 = _doc().model_copy(update={
        "sections": [Section(section_id="s", heading="S", source="llm", status="ok",
                             blocks=[Paragraph(md="v2 text")])],
    })
    store.save_version(project_dir, doc2)

    latest = store.load_report(project_dir, "0123456789abcdef")
    assert latest.version == 2
    assert latest.sections[0].blocks[0].md == "v2 text"
    first = store.load_report(project_dir, "0123456789abcdef", version=1)
    assert first.version == 1
    assert first.sections[0].blocks[0].md == "v1 text"
    with pytest.raises(store.ReportNotFound):
        store.load_report(project_dir, "0123456789abcdef", version=9)
    with pytest.raises(store.ReportNotFound):
        store.load_report(project_dir, "0123456789abcdef", version=0)


def test_save_version_on_an_unknown_report_is_not_found(project_dir):
    with pytest.raises(store.ReportNotFound):
        store.save_version(project_dir, _doc())
    assert not (project_dir / "reports" / "0123456789abcdef").exists()


def test_missing_report_is_not_found(project_dir):
    with pytest.raises(store.ReportNotFound):
        store.load_report(project_dir, "ffffffffffffffff")
    with pytest.raises(store.ReportNotFound):
        store.load_meta(project_dir, "ffffffffffffffff")
    with pytest.raises(store.ReportNotFound):
        store.delete_report(project_dir, "ffffffffffffffff")


# ── list / delete ───────────────────────────────────────────────────────────

def test_list_is_newest_first_and_empty_without_a_reports_dir(project_dir):
    assert store.list_reports(project_dir) == []
    older = _doc("aaaaaaaaaaaaaaaa", "older").model_copy(
        update={"created_at": "2026-09-28T09:00:00+00:00"})
    newer = _doc("bbbbbbbbbbbbbbbb", "newer").model_copy(
        update={"created_at": "2026-09-28T11:00:00+00:00"})
    store.create_report(project_dir, older)
    store.create_report(project_dir, newer)
    assert [m.title for m in store.list_reports(project_dir)] == ["newer", "older"]


def test_list_skips_an_unreadable_meta_with_one_warning(project_dir, caplog):
    store.create_report(project_dir, _doc("aaaaaaaaaaaaaaaa", "good"))
    broken = project_dir / "reports" / "bbbbbbbbbbbbbbbb"
    broken.mkdir(parents=True)
    (broken / "meta.json").write_text("{not json", encoding="utf-8")
    # A stray file and a mis-named directory are simply not reports.
    (project_dir / "reports" / "README.txt").write_text("x", encoding="utf-8")
    (project_dir / "reports" / "not-an-id").mkdir()
    with caplog.at_level(logging.WARNING, logger="services.reports.store"):
        listed = store.list_reports(project_dir)
    assert [m.title for m in listed] == ["good"]
    warnings = [r for r in caplog.records if "bbbbbbbbbbbbbbbb" in r.getMessage()]
    assert len(warnings) == 1


def test_delete_removes_the_directory_and_nothing_else(project_dir):
    store.create_report(project_dir, _doc("aaaaaaaaaaaaaaaa"))
    store.create_report(project_dir, _doc("bbbbbbbbbbbbbbbb"))
    store.write_figure(project_dir, "aaaaaaaaaaaaaaaa", "fig", b"\x89PNG\r\n\x1a\n")
    store.delete_report(project_dir, "aaaaaaaaaaaaaaaa")
    assert not (project_dir / "reports" / "aaaaaaaaaaaaaaaa").exists()
    assert (project_dir / "reports" / "bbbbbbbbbbbbbbbb" / "v1.json").exists()
    assert (project_dir / "network.nc").exists()


# ── figures ─────────────────────────────────────────────────────────────────

def test_write_and_resolve_a_figure(project_dir):
    store.create_report(project_dir, _doc())
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    path = store.write_figure(project_dir, "0123456789abcdef", "fmea_pareto", png)
    assert path == project_dir / "reports" / "0123456789abcdef" / "figures" / "fmea_pareto.png"
    assert path.read_bytes() == png
    assert store.figure_path(project_dir, "0123456789abcdef", "fmea_pareto") == path
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("bad", ["..", "a/b", "x.png", "", "a b", "é", "x" * 65])
def test_a_malformed_figure_id_never_becomes_a_path(project_dir, bad):
    store.create_report(project_dir, _doc())
    with pytest.raises(store.InvalidFigureId):
        store.write_figure(project_dir, "0123456789abcdef", bad, b"png")
    with pytest.raises(store.InvalidFigureId):
        store.figure_path(project_dir, "0123456789abcdef", bad)


def test_an_unknown_figure_is_not_found(project_dir):
    store.create_report(project_dir, _doc())
    with pytest.raises(store.FigureNotFound):
        store.figure_path(project_dir, "0123456789abcdef", "nope")
    with pytest.raises(store.ReportNotFound):
        store.figure_path(project_dir, "ffffffffffffffff", "nope")


# ── WP6: the job's `generation` object survives a later save_version ─────────

def test_generation_meta_survives_save_version_and_is_listed(project_dir):
    """
    WP3 writes `generation{repairs, prose_failures, …}` into `meta.json`
    after the save; a later `save_version` used to rewrite the meta from the
    model and drop it. `ReportMeta.generation` carries it now.
    """
    store.create_report(project_dir, _doc())
    path = project_dir / "reports" / "0123456789abcdef" / "meta.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["generation"] is None
    generation = {"repairs": 1, "prose_failures": [{"section_id": "s", "reason": "no_json"}],
                  "sections": ["s"], "language": "en", "aborted": False}
    raw["generation"] = generation
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert store.load_meta(project_dir, "0123456789abcdef").generation == generation
    assert store.save_version(project_dir, _doc()) == 2
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["latest_version"] == 2
    assert after["generation"] == generation
    listed = store.list_reports(project_dir)
    assert listed[0].generation == generation
    assert listed[0].model_dump()["generation"] == generation


# ── WP11: the template binding and the mapping plan survive save_version ─────

def test_template_fields_and_mapping_plan_survive_save_version(project_dir):
    """
    `ReportMeta.template_mode`, `template_language` and `mapping_plan` are
    set by the template routes through `update_meta` and, like `generation`,
    must survive a later `save_version` from any caller (a regenerate, a
    bind of the same file) rather than be dropped by the meta rewrite.
    """
    store.create_report(project_dir, _doc())
    meta = store.load_meta(project_dir, "0123456789abcdef")
    assert meta.template_mode is None and meta.template_language is None
    assert meta.mapping_plan is None
    plan = {"entries": [{"heading_index": 5, "action": "keep", "new_text": None,
                         "section_ids": ["s"]}],
            "inserted": [], "placeholders": {}, "unmapped_sections": [], "notes": []}
    updated = store.update_meta(project_dir, "0123456789abcdef", template_mode="untagged",
                                template_language="de", mapping_plan=plan)
    assert updated.template_mode == "untagged" and updated.template_language == "de"
    assert updated.mapping_plan == plan
    assert store.load_meta(project_dir, "0123456789abcdef").mapping_plan == plan

    assert store.save_version(project_dir, _doc()) == 2
    path = project_dir / "reports" / "0123456789abcdef" / "meta.json"
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["latest_version"] == 2
    assert after["template_mode"] == "untagged" and after["template_language"] == "de"
    assert after["mapping_plan"] == plan
    listed = store.list_reports(project_dir)
    assert listed[0].mapping_plan == plan and listed[0].template_mode == "untagged"

    # Clearing goes through the same helper and is honoured explicitly (None
    # is a value here, not "leave as is").
    cleared = store.update_meta(project_dir, "0123456789abcdef", template_mode=None,
                                template_language=None, mapping_plan=None)
    assert cleared.template_mode is None and cleared.mapping_plan is None
    assert cleared.latest_version == 2
    with pytest.raises(store.ReportNotFound):
        store.update_meta(project_dir, "ffffffffffffffff", mapping_plan=plan)
    with pytest.raises(TypeError):
        store.update_meta(project_dir, "0123456789abcdef", not_a_field=1)


def test_roundtrip_file_id_survives_save_version(project_dir):
    """
    WP12 added `ReportMeta.roundtrip_file_id`; WP13 sets it through
    `update_meta` when an edited copy is merged. Both were built in parallel
    and neither added the field to `_CARRIED_META_FIELDS`, so a later
    `save_version` (the regenerate that follows a round trip) dropped it.
    """
    store.create_report(project_dir, _doc())
    updated = store.update_meta(project_dir, "0123456789abcdef",
                                roundtrip_file_id="fedcba9876543210")
    assert updated.roundtrip_file_id == "fedcba9876543210"
    assert store.save_version(project_dir, _doc()) == 2
    assert store.load_meta(project_dir, "0123456789abcdef").roundtrip_file_id == "fedcba9876543210"
    assert store.list_reports(project_dir)[0].roundtrip_file_id == "fedcba9876543210"
