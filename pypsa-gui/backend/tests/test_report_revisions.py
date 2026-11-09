# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 07c (#970 HH-08A): report comments become revisions.

GK B (37) R1-R10. A comment is durable and stays open until it is addressed
or withdrawn. An edit of the closed set makes a new version against the
latest digest. Numbers, disclosures, evidence references, figures and the
re-run audit stay equal to the base's. An unsupported comment stays open
with its reason, and a stale base is refused.
"""

from __future__ import annotations

import base64
import io
import json
import threading
import zipfile

import pytest

from models.report import (
    Bullets,
    Callout,
    Figure,
    FigureRef,
    Paragraph,
    ReportDocument,
    RevisedFrom,
    Section,
    Table,
    TableRef,
)
from services.reports import number_audit, revisions as R, store
from services.reports.evidence import NumberFact
from tests.test_worksheet_foreign_lock import same_org_other_user  # noqa: F401 (fixture)

RID = "0123456789abcdef"
CAP = "/sections/grid/payload/cap_mw"
DUR = "/sections/grid/payload/duration_h"
FACTS = [
    NumberFact(path=CAP, value=1234, unit="MW"),
    NumberFact(path=DUR, value=4.5, unit="h"),
]
GRID_P = "The grid connection is 1234 MW, firm from 2030 at 3.2 EUR/MWh."
GRID_B = ["Storage of 4.5 h", "Export -5 MW at night"]


def _grid() -> Section:
    blocks = [
        Paragraph(md=GRID_P),
        Bullets(items=list(GRID_B)),
        Callout(kind="disclosure", text="Prices are in 2025 EUR."),
        TableRef(table_id="t1", caption="Assets"),
        FigureRef(figure_id="fig", caption="Dispatch"),
    ]
    return Section(
        section_id="grid",
        heading="Grid",
        source="llm",
        status="ok",
        blocks=blocks,
        audit=number_audit.audit([GRID_P, *GRID_B], FACTS),
    )


def _doc() -> ReportDocument:
    return ReportDocument(
        report_id=RID,
        version=1,
        title="Report",
        created_at="2026-11-08T08:00:00+00:00",
        evidence_hash="a" * 64,
        mode="evidence_only",
        sections=[
            _grid(),
            Section(
                section_id="summary",
                heading="Summary",
                source="code",
                status="ok",
                blocks=[
                    Paragraph(md="Charge window 240 min, a 0.2 share."),
                    Paragraph(md="Three units."),
                ],
            ),
        ],
        tables={
            "t1": Table(
                table_id="t1",
                columns=["Asset", "Power"],
                rows=[["BESS", "1200.0 MW"], ["PV", "350"]],
                caption="Assets",
                source_path="/results/assets",
            )
        },
        figures={
            "fig": Figure(
                figure_id="fig", png_file="fig.png", source_path="/results/dispatch"
            )
        },
    )


@pytest.fixture
def pdir(tmp_path):
    store.create_report(tmp_path, _doc())
    return tmp_path


def _digest(pdir) -> str:
    latest = store.load_meta(pdir, RID).latest_version
    return store.version_sha256(pdir, RID, latest)


def _anchor(section="grid", block=0, table=None) -> R.Anchor:
    return R.Anchor(section_id=section, block_index=block, table_id=table)


def _comment(pdir, anchor=None, text="show it shorter") -> R.Comment:
    return R.add_comment(pdir, RID, text, anchor or _anchor())


def _para(md):
    return R.ParagraphEdit(kind="paragraph_text", md=md)


def _refused(fn, *a) -> R.RevisionError:
    with pytest.raises(R.RevisionError) as exc:
        fn(*a)
    return exc.value


# ── AC1: durable, open until addressed or withdrawn ─────────────────────────


def test_a_comment_is_stored_durably_and_stays_open(pdir):
    c = _comment(pdir)
    raw = json.loads((store.report_dir(pdir, RID) / R.COMMENTS_FILE).read_text())
    assert (
        raw["schema_version"] == 1 and raw["comments"][0]["comment_id"] == c.comment_id
    )
    again = R.load_comments(pdir, RID)  # a fresh read from disk
    assert [x.status for x in again] == ["open"] and again[0].reason is None
    assert again[0].base_version == 1 and again[0].base_digest == _digest(pdir)


def test_withdrawn_is_terminal(pdir):
    c = _comment(pdir)
    assert R.withdraw(pdir, RID, c.comment_id).status == "withdrawn"
    assert _refused(R.withdraw, pdir, RID, c.comment_id).kind == "report_comment_closed"
    e = _refused(R.address, pdir, RID, c.comment_id, _digest(pdir), _para(GRID_P))
    assert (e.kind, e.status) == ("report_comment_closed", 409)
    assert store.load_meta(pdir, RID).latest_version == 1


def test_addressed_is_terminal(pdir):
    c = _comment(pdir)
    R.address(
        pdir, RID, c.comment_id, _digest(pdir), _para(GRID_P.replace("1234", "1,234"))
    )
    assert _refused(R.withdraw, pdir, RID, c.comment_id).kind == "report_comment_closed"
    e = _refused(R.address, pdir, RID, c.comment_id, _digest(pdir), _para(GRID_P))
    assert e.kind == "report_comment_closed"
    assert store.load_meta(pdir, RID).latest_version == 2


def test_an_unknown_comment_is_404(pdir):
    e = _refused(R.withdraw, pdir, RID, "f" * 16)
    assert (e.kind, e.status) == ("report_comment_not_found", 404)


# ── AC2: an addressed comment yields an equal-invariant copy ────────────────


def test_an_addressed_comment_yields_a_new_version_with_the_bases_invariants(pdir):
    base_file = store.report_dir(pdir, RID) / "v1.json"
    before = base_file.read_bytes()
    c = _comment(pdir)
    digest = _digest(pdir)
    text = "Grid: a 1,234 MW connection, firm from 2030 at 3.2 €/MWh."
    done, doc = R.address(pdir, RID, c.comment_id, digest, _para(text))
    assert doc.version == 2 and doc.sections[0].blocks[0].md == text
    assert doc.sections[0].source == "user_edit"
    assert R.check_invariants(store.load_report(pdir, RID, 1), doc, c.anchor) == []
    assert base_file.read_bytes() == before  # the base is never rewritten
    assert doc.revised_from == RevisedFrom(
        version=1, digest=digest, comment_id=c.comment_id
    )
    assert done.status == "addressed" and done.revision.version == 2
    assert done.revision.digest == store.version_sha256(pdir, RID, 2)
    assert R.load_comments(pdir, RID)[0].status == "addressed"


def test_the_audit_is_re_run_on_the_new_spelling_never_copied(pdir):
    """R4: the stored audit names the new spelling, so a copied base audit
    (which still says "1234 MW") dies here."""
    c = _comment(pdir)
    _, doc = R.address(
        pdir,
        RID,
        c.comment_id,
        _digest(pdir),
        _para(GRID_P.replace("1234 MW", "1,234 MW")),
    )
    verified = {v.text: v.path for v in doc.sections[0].audit.verified}
    assert verified.get("1,234 MW") == CAP and "1234 MW" not in verified


def test_a_bullet_edit_is_in_the_closed_set(pdir):
    c = _comment(pdir, _anchor(block=1))
    edit = R.BulletEdit(
        kind="bullet_text", items=["Storage: 4.50 h", "Export of -5 MW overnight"]
    )
    _, doc = R.address(pdir, RID, c.comment_id, _digest(pdir), edit)
    assert doc.sections[0].blocks[1].items == edit.items


def test_table_cells_and_text_are_in_the_closed_set(pdir):
    c = _comment(pdir, _anchor(block=None, table="t1"))
    edit = R.TableCellsEdit(
        kind="table_cells", rows=[["BESS", "1,200 MW"], ["PV", "350"]]
    )
    _, doc = R.address(pdir, RID, c.comment_id, _digest(pdir), edit)
    assert doc.tables["t1"].rows[0][1] == "1,200 MW"
    c2 = _comment(pdir, _anchor(block=None, table="t1"))
    edit2 = R.TableTextEdit(
        kind="table_text", caption="Assets on site", columns=["Asset", "Power, MW"]
    )
    _, doc2 = R.address(pdir, RID, c2.comment_id, _digest(pdir), edit2)
    assert doc2.tables["t1"].columns == ["Asset", "Power, MW"] and doc2.version == 3


@pytest.mark.parametrize(
    "old,new",
    [
        ("1234 MW", "1,234 MW"),  # thousands separator
        ("1234 MW", "1 234 MW"),  # a spaced thousands separator
        ("3.2 EUR/MWh", "3.20 EUR/MWh"),  # trailing zeros
        ("1234 MW", "1234MW"),  # unit spacing
        ("3.2 EUR/MWh", "3.2 €/MWh"),  # unit spelling, EUR to €
    ],
)
def test_an_equal_value_re_spelling_is_allowed(pdir, old, new):
    c = _comment(pdir)
    _, doc = R.address(
        pdir, RID, c.comment_id, _digest(pdir), _para(GRID_P.replace(old, new))
    )
    assert doc.version == 2


def test_a_per_year_suffix_re_spelling_is_allowed():
    assert R.numbers("LOLE 4 h/a") == R.numbers("LOLE 4 h/yr")


# ── AC3: outside the closed set stays open with its reason ──────────────────


@pytest.mark.parametrize(
    "section,old,new,reason",
    [
        ("grid", "1234 MW", "1,230 MW", "changes_number"),  # rounding
        ("grid", "1234 MW", "1.234 GW", "changes_number"),  # a unit conversion
        ("grid", "2030", "2035", "changes_number"),  # an exempt year
        ("grid", ", firm from 2030 at 3.2 EUR/MWh", "", "drops_number"),
        ("summary", "240 min", "4 h", "changes_number"),
        ("summary", "0.2 share", "20% share", "changes_number"),
    ],
)
def test_a_numeric_change_stays_open_with_its_reason(pdir, section, old, new, reason):
    base = store.load_report(pdir, RID)
    s = next(x for x in base.sections if x.section_id == section)
    c = _comment(pdir, _anchor(section=section))
    e = _refused(
        R.address,
        pdir,
        RID,
        c.comment_id,
        _digest(pdir),
        _para(s.blocks[0].md.replace(old, new)),
    )
    assert (e.kind, e.status) == ("report_revision_unsupported", 422)
    assert e.reasons[0] == reason
    stored = R.load_comments(pdir, RID)[0]
    assert (
        stored.status == "open"
        and stored.reason == reason
        and stored.reasons == e.reasons
    )
    assert store.load_meta(pdir, RID).latest_version == 1


def test_a_sign_change_is_refused(pdir):
    c = _comment(pdir, _anchor(block=1))
    edit = R.BulletEdit(
        kind="bullet_text", items=["Storage of 4.5 h", "Export 5 MW at night"]
    )
    e = _refused(R.address, pdir, RID, c.comment_id, _digest(pdir), edit)
    assert e.reasons[0] == "changes_number"


def test_a_unit_added_to_a_bare_cell_is_refused(pdir):
    c = _comment(pdir, _anchor(block=None, table="t1"))
    edit = R.TableCellsEdit(
        kind="table_cells", rows=[["BESS", "1200.0 MW"], ["PV", "350 MW"]]
    )
    assert (
        _refused(R.address, pdir, RID, c.comment_id, _digest(pdir), edit).reasons[0]
        == "changes_number"
    )


def test_a_number_moved_to_another_block_is_refused_with_equal_totals():
    base = _doc()
    moved = base.model_copy(deep=True)
    grid = moved.sections[0]
    grid.blocks[0] = Paragraph(md=GRID_P.replace("1234 MW", "a firm"))
    grid.blocks[1] = Bullets(
        items=["Storage of 4.5 h", "Export -5 MW at night, of 1234 MW"]
    )
    assert R.check_invariants(base, moved)[0] == "changes_number"


@pytest.mark.parametrize(
    "anchor,reason",
    [
        (_anchor(block=2), R.OUTSIDE_EDIT_SET),  # the disclosure callout
        (_anchor(block=4), R.OUTSIDE_EDIT_SET),  # the figure
        (_anchor(block=None), R.NO_EDIT_TARGET),  # the whole section
    ],
)
def test_an_unsupported_comment_stays_open_with_its_reason(pdir, anchor, reason):
    c = _comment(pdir, anchor, text="shorten the grid section")
    assert (c.status, c.reason) == ("open", reason)
    e = _refused(R.address, pdir, RID, c.comment_id, _digest(pdir), _para("Short."))
    assert e.reasons == [reason]
    assert R.load_comments(pdir, RID)[0].status == "open"
    assert store.load_meta(pdir, RID).latest_version == 1


def test_an_edit_of_the_wrong_kind_stays_open(pdir):
    c = _comment(pdir, _anchor(block=1))  # a bullet list
    e = _refused(
        R.address, pdir, RID, c.comment_id, _digest(pdir), _para("Storage of 4.5 h")
    )
    assert e.reasons == [R.EDIT_KIND_MISMATCH]
    t = _comment(pdir, _anchor(block=None, table="t1"))
    e2 = _refused(R.address, pdir, RID, t.comment_id, _digest(pdir), _para("x"))
    assert e2.reasons == [R.EDIT_KIND_MISMATCH]


# ── R6: each invariant part has its own killing case ────────────────────────


def _mutated(change):
    base = _doc()
    new = base.model_copy(deep=True)
    change(new)
    return base, new


def _add_block(d):
    d.sections[1].blocks.append(Paragraph(md="More."))


def _callout(d):
    d.sections[0].blocks[2] = Callout(kind="disclosure", text="Prices are nominal.")


def _source_path(d):
    d.tables["t1"] = d.tables["t1"].model_copy(update={"source_path": "/results/other"})


def _figure(d):
    d.figures["fig"] = d.figures["fig"].model_copy(update={"png_file": "other.png"})


def _evidence(d):
    d.evidence_hash = "b" * 64


def _shape(d):
    d.tables["t1"].rows.append(["Wind", "x"])


def _outside(d):
    d.sections[1].blocks[1] = Paragraph(md="Four units.")


def _figure_ref(d):
    d.sections[0].blocks[4] = FigureRef(figure_id="fig2", caption="Dispatch")


@pytest.mark.parametrize(
    "change,reason",
    [
        (_add_block, "changes_structure"),
        (_callout, "changes_disclosure"),
        (_source_path, "changes_evidence_ref"),
        (_figure_ref, "changes_evidence_ref"),
        (_figure, "changes_chart"),
        (_evidence, "changes_evidence_hash"),
        (_shape, "changes_table_shape"),
        (_outside, "edit_outside_anchor"),
    ],
)
def test_each_invariant_part_refuses_by_name(change, reason):
    base, new = _mutated(change)
    assert reason in R.check_invariants(base, new, _anchor(section="summary", block=0))


def test_a_copied_audit_refuses_changes_audit():
    base, new = _mutated(lambda d: None)
    new.sections[0].blocks[0] = Paragraph(md=GRID_P.replace("1234 MW", "1,234 MW"))
    assert R.check_invariants(base, new, _anchor()) == ["changes_audit"]


def test_an_audit_that_loses_a_verified_number_refuses():
    base, new = _mutated(lambda d: None)
    # 1,234.4 MW is an equal-looking number with a different value: the
    # number check and the re-run audit both catch it.
    new.sections[0].blocks[0] = Paragraph(md=GRID_P.replace("1234 MW", "1,234.4 MW"))
    new.sections[0] = new.sections[0].model_copy(
        update={"audit": R.reaudit(new.sections[0], R._facts(base.sections[0]))}
    )
    assert R.check_invariants(base, new, _anchor()) == [
        "changes_number",
        "changes_audit",
    ]


def test_an_edit_outside_its_own_block_is_refused():
    base, new = _mutated(lambda d: None)
    new.sections[0].blocks[1] = Bullets(items=["Storage, 4.5 h", *GRID_B[1:]])
    assert R.check_invariants(base, new, _anchor(block=0)) == ["edit_outside_anchor"]


def test_the_unchanged_document_has_no_reasons():
    assert R.check_invariants(_doc(), _doc(), _anchor()) == []


# ── AC4: a stale base is refused, nothing written ───────────────────────────


def test_a_stale_digest_is_refused_and_nothing_is_written(pdir):
    c = _comment(pdir)
    e = _refused(R.address, pdir, RID, c.comment_id, "0" * 64, _para(GRID_P))
    assert (e.kind, e.status) == ("revision_stale_base", 409)
    assert store.load_meta(pdir, RID).latest_version == 1
    assert R.load_comments(pdir, RID)[0] == c


def test_a_version_saved_after_the_comment_makes_the_old_digest_stale(pdir):
    c = _comment(pdir)
    old = _digest(pdir)
    store.save_version(pdir, store.load_report(pdir, RID))
    assert (
        _refused(R.address, pdir, RID, c.comment_id, old, _para(GRID_P)).kind
        == "revision_stale_base"
    )


def test_a_changed_anchor_block_is_stale_even_on_the_latest_digest(pdir):
    c = _comment(pdir)
    doc = store.load_report(pdir, RID)
    doc.sections[0].blocks[0] = Paragraph(md=GRID_P + " Revised.")
    store.save_version(pdir, doc)
    e = _refused(R.address, pdir, RID, c.comment_id, _digest(pdir), _para(GRID_P))
    assert e.kind == "revision_stale_base"
    assert store.load_meta(pdir, RID).latest_version == 2


def test_two_addresses_on_one_base_make_exactly_one_version(pdir):
    """R5: the digest check and the save run under `store.report_lock`."""
    a, b = _comment(pdir), _comment(pdir)
    digest = _digest(pdir)
    gate = threading.Barrier(2)
    out: dict = {}

    def run(c, text):
        gate.wait()
        try:
            out[c.comment_id] = R.address(pdir, RID, c.comment_id, digest, _para(text))[
                1
            ].version
        except R.RevisionError as exc:
            out[c.comment_id] = exc.kind

    threads = [
        threading.Thread(target=run, args=(a, GRID_P.replace("1234", "1,234"))),
        threading.Thread(target=run, args=(b, GRID_P.replace("1234", "1 234"))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(str, out.values())) == ["2", "revision_stale_base"]
    assert store.load_meta(pdir, RID).latest_version == 2
    assert sorted(c.status for c in R.load_comments(pdir, RID)) == ["addressed", "open"]


# ── R8: anchors and the file are read strictly ──────────────────────────────


@pytest.mark.parametrize(
    "anchor",
    [
        _anchor(section="nope"),
        _anchor(block=9),
        _anchor(block=0, table="t1"),
        _anchor(section="summary", block=None, table="t1"),  # not referenced there
        _anchor(block=None, table="t9"),
    ],
)
def test_an_anchor_that_names_nothing_is_refused_and_not_stored(pdir, anchor):
    e = _refused(R.add_comment, pdir, RID, "x", anchor)
    assert (e.kind, e.status) == ("report_comment_anchor_invalid", 422)
    assert R.load_comments(pdir, RID) == []


@pytest.mark.parametrize(
    "body",
    [
        "{not json",
        json.dumps({"schema_version": 2, "report_id": RID, "comments": []}),
        json.dumps({"schema_version": 1, "report_id": "f" * 16, "comments": []}),
        json.dumps(
            {"schema_version": 1, "report_id": RID, "comments": [{"comment_id": "x"}]}
        ),
        json.dumps({"schema_version": 1, "report_id": RID, "comments": [], "extra": 1}),
    ],
)
def test_a_malformed_comments_file_is_a_typed_refusal(pdir, body):
    (store.report_dir(pdir, RID) / R.COMMENTS_FILE).write_text(body)
    e = _refused(R.load_comments, pdir, RID)
    assert (e.kind, e.status) == ("report_comments_unreadable", 409)
    assert (
        _refused(R.add_comment, pdir, RID, "x", _anchor()).kind
        == "report_comments_unreadable"
    )


def test_duplicate_comment_ids_are_unreadable(pdir):
    c = _comment(pdir)
    path = store.report_dir(pdir, RID) / R.COMMENTS_FILE
    raw = json.loads(path.read_text())
    raw["comments"].append(raw["comments"][0])
    path.write_text(json.dumps(raw))
    assert (
        c and _refused(R.load_comments, pdir, RID).kind == "report_comments_unreadable"
    )


# ── R7: provenance, and an unrevised version stays byte-equal ───────────────


def test_an_unrevised_version_is_written_without_a_revised_from_key(pdir):
    raw = (store.report_dir(pdir, RID) / "v1.json").read_bytes()
    assert raw == _doc().model_dump_json(indent=2).encode()
    assert b"revised_from" not in raw


def test_a_later_save_of_a_revision_is_not_itself_a_revision(pdir):
    c = _comment(pdir)
    _, revised = R.address(
        pdir, RID, c.comment_id, _digest(pdir), _para(GRID_P.replace("1234", "1,234"))
    )
    assert b'"revised_from"' in (store.report_dir(pdir, RID) / "v2.json").read_bytes()
    store.save_version(pdir, revised)  # e.g. a regenerate starting from v2
    assert b"revised_from" not in (store.report_dir(pdir, RID) / "v3.json").read_bytes()
    assert store.load_report(pdir, RID, 3).revised_from is None


def test_the_store_lock_is_re_entrant_and_shared(pdir):
    with store.report_lock(pdir, RID):
        assert store.save_version(pdir, store.load_report(pdir, RID)) == 2
    held = threading.Event()
    release = threading.Event()

    def hold():
        with store.report_lock(pdir, RID):
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    saved: list = []
    s = threading.Thread(
        target=lambda: saved.append(
            store.save_version(pdir, store.load_report(pdir, RID))
        )
    )
    s.start()
    s.join(0.3)
    assert saved == []  # save_version waits for the holder
    release.set()
    s.join(5)
    t.join(5)
    assert saved == [3]


# ── GK B F1: every read-then-save writer names its base ──────────────────────


def _interleave(pdir):
    """Another writer's save, landing between a writer's read and its save."""
    store.save_version(
        pdir,
        store.load_report(pdir, RID).model_copy(update={"title": "saved in between"}),
    )


def test_creating_a_report_that_exists_is_refused_and_keeps_it(pdir):
    with pytest.raises(store.ReportExists):
        store.create_report(pdir, _doc().model_copy(update={"title": "a second one"}))
    assert store.load_report(pdir, RID).title == _doc().title


def test_save_version_refuses_a_base_that_is_not_the_latest(pdir):
    _interleave(pdir)  # latest is now v2
    with pytest.raises(store.ReportVersionConflict):
        store.save_version(pdir, store.load_report(pdir, RID, 1), expected_base=1)
    assert store.load_meta(pdir, RID).latest_version == 2
    assert store.save_version(pdir, store.load_report(pdir, RID), expected_base=2) == 3


def test_a_round_trip_over_a_superseded_base_writes_nothing(pdir, monkeypatch):
    from services.reports import roundtrip_service

    def read_then_interleave(base, data, read=None):
        _interleave(pdir)
        return object()

    monkeypatch.setattr(roundtrip_service, "_read_edited_docx", lambda: None)
    monkeypatch.setattr(roundtrip_service, "read_edited_copy", read_then_interleave)
    monkeypatch.setattr(
        roundtrip_service,
        "_merge_round_trip",
        lambda: lambda base, result: base.model_copy(update={"title": "merged copy"}),
    )
    with pytest.raises(store.ReportVersionConflict):
        roundtrip_service.run_round_trip(pdir, RID, b"docx", bind_as_template=False)
    meta = store.load_meta(pdir, RID)
    assert meta.latest_version == 2
    assert store.load_report(pdir, RID).title == "saved in between"


@pytest.mark.parametrize("bound, file_id", [(None, "tpl-new"), ("tpl-old", None)])
def test_binding_a_template_over_a_superseded_base_is_409(
    client, api_project, project_storage_dir, monkeypatch, bound, file_id
):
    from types import SimpleNamespace

    from routers import reports as reports_router

    name = api_project(f"rev-bind-{file_id}")
    pdir = project_storage_dir(name)
    store.create_report(pdir, _doc().model_copy(update={"template_file_id": bound}))
    check_lock = reports_router._check_lock

    def check_then_interleave(*a):
        check_lock(*a)
        _interleave(pdir)

    monkeypatch.setattr(reports_router, "_check_lock", check_then_interleave)
    monkeypatch.setattr(
        reports_router,
        "_resolve_template",
        lambda project, fid: (b"", SimpleNamespace(mode="styles", language="en")),
    )
    r = client.post(
        f"/api/projects/{name}/reports/{RID}/template", json={"file_id": file_id}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == "report_version_conflict"
    assert store.load_meta(pdir, RID).latest_version == 2
    assert store.load_report(pdir, RID).template_file_id == bound


# ── the routes ──────────────────────────────────────────────────────────────


def _base(name):
    return f"/api/projects/{name}/reports/{RID}/comments"


def test_the_routes_comment_address_and_withdraw(
    client, api_project, project_storage_dir
):
    name = api_project("rev-routes")
    store.create_report(project_storage_dir(name), _doc())
    listed = client.get(_base(name)).json()
    assert listed["comments"] == [] and listed["latest_version"] == 1
    r = client.post(
        _base(name),
        json={
            "text": "group the thousands",
            "anchor": {"section_id": "grid", "block_index": 0},
        },
    )
    assert r.status_code == 200, r.text[:300]
    cid = r.json()["comment_id"]
    bad = client.post(
        f"{_base(name)}/{cid}/address",
        json={
            "base_digest": "0" * 64,
            "edit": {"kind": "paragraph_text", "md": GRID_P},
        },
    )
    assert (
        bad.status_code == 409
        and bad.json()["detail"]["error_kind"] == "revision_stale_base"
    )
    numeric = client.post(
        f"{_base(name)}/{cid}/address",
        json={
            "base_digest": listed["digest"],
            "edit": {"kind": "paragraph_text", "md": GRID_P.replace("1234", "1,200")},
        },
    )
    assert numeric.status_code == 422
    assert numeric.json()["detail"]["reasons"][0] == "changes_number"
    ok = client.post(
        f"{_base(name)}/{cid}/address",
        json={
            "base_digest": listed["digest"],
            "edit": {"kind": "paragraph_text", "md": GRID_P.replace("1234", "1,234")},
        },
    )
    assert ok.status_code == 200, ok.text[:300]
    body = ok.json()
    assert body["version"] == 2 and body["comment"]["status"] == "addressed"
    assert body["document"]["revised_from"]["comment_id"] == cid
    assert client.get(_base(name)).json()["digest"] == body["digest"]
    w = client.post(f"{_base(name)}/{cid}/withdraw")
    assert (
        w.status_code == 409
        and w.json()["detail"]["error_kind"] == "report_comment_closed"
    )


def test_the_routes_refuse_a_malformed_edit_or_anchor(
    client, api_project, project_storage_dir
):
    name = api_project("rev-bad")
    store.create_report(project_storage_dir(name), _doc())
    a = client.post(
        _base(name), json={"text": "x", "anchor": {"section_id": "grid", "row": 1}}
    )
    assert a.status_code == 422 and a.json()["detail"]["error_kind"] == "invalid_anchor"
    cid = client.post(
        _base(name),
        json={"text": "x", "anchor": {"section_id": "grid", "block_index": 0}},
    ).json()["comment_id"]
    e = client.post(
        f"{_base(name)}/{cid}/address",
        json={"base_digest": "0" * 64, "edit": {"kind": "heading_text", "md": "x"}},
    )
    assert e.status_code == 422 and e.json()["detail"]["error_kind"] == "invalid_edit"
    empty = client.post(
        _base(name), json={"text": "  ", "anchor": {"section_id": "grid"}}
    )
    assert empty.status_code == 422


def test_a_malformed_file_is_409_on_the_route_never_500(
    client, api_project, project_storage_dir
):
    name = api_project("rev-file")
    pdir = project_storage_dir(name)
    store.create_report(pdir, _doc())
    (store.report_dir(pdir, RID) / R.COMMENTS_FILE).write_text("[]")
    r = client.get(_base(name))
    assert (
        r.status_code == 409
        and r.json()["detail"]["error_kind"] == "report_comments_unreadable"
    )


def test_writes_are_refused_under_a_foreign_lock_and_reads_are_not(
    client,
    api_project,
    project_storage_dir,
    same_org_other_user,  # noqa: F811
):
    name = api_project("rev-lock")
    store.create_report(project_storage_dir(name), _doc())
    cid = client.post(
        _base(name),
        json={"text": "x", "anchor": {"section_id": "grid", "block_index": 0}},
    ).json()["comment_id"]
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    for path, body in [
        (_base(name), {"text": "y", "anchor": {"section_id": "grid"}}),
        (f"{_base(name)}/{cid}/withdraw", None),
        (
            f"{_base(name)}/{cid}/address",
            {"base_digest": "0" * 64, "edit": {"kind": "paragraph_text", "md": "x"}},
        ),
    ]:
        r = same_org_other_user.post(path, json=body)
        assert (
            r.status_code == 409
            and r.json()["detail"]["error_kind"] == "project_locked"
        ), path
    assert same_org_other_user.get(_base(name)).status_code == 200
    assert [c["status"] for c in client.get(_base(name)).json()["comments"]] == ["open"]


def test_another_orgs_project_is_404(
    client, other_org_client, api_project, project_storage_dir
):
    name = api_project("rev-org")
    store.create_report(project_storage_dir(name), _doc())
    assert other_org_client.get(_base(name)).status_code == 404


def test_the_bundle_carries_the_comments(client, api_project, project_storage_dir):
    name = api_project("rev-bundle")
    store.create_report(project_storage_dir(name), _doc())
    cid = client.post(
        _base(name),
        json={"text": "x", "anchor": {"section_id": "grid", "block_index": 0}},
    ).json()["comment_id"]
    r = client.get(f"/api/projects/{name}/bundle")
    assert (
        f"reports/{RID}/{R.COMMENTS_FILE}"
        in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    )
    r = client.post(
        "/api/projects/import_bundle?name=rev_bundle_imported",
        files={"file": ("rev.pypsaproj.zip", r.content, "application/zip")},
    )
    assert r.status_code in (200, 201), r.text[:200]
    imported = r.json().get("imported") or r.json().get("name") or "rev_bundle_imported"
    got = client.get(f"/api/projects/{imported}/reports/{RID}/comments").json()
    assert [c["comment_id"] for c in got["comments"]] == [cid]


# ── R7: the sealed export's manifest names the revision ─────────────────────

from tests.assurance_acceptance import test_p4_report_manifest as _sealed  # noqa: E402

# A real 1x1 PNG: python-docx reads the image header when it embeds a figure.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

project = _sealed.project
helpers_state = _sealed.helpers_state


def test_the_sealed_export_names_the_revision_and_an_unrevised_one_does_not(project):
    from services import chat_tools as T

    store.create_report(project, _doc())
    store.write_figure(project, RID, "fig", _PNG)
    first = _sealed._sidecar(project, T.export_report_docx(RID)["file_id"])["manifest"]
    assert "revised_from" not in first["dataset"]
    c = _comment(project)
    digest = _digest(project)
    R.address(
        project, RID, c.comment_id, digest, _para(GRID_P.replace("1234", "1,234"))
    )
    sealed = _sealed._sidecar(project, T.export_report_docx(RID)["file_id"])["manifest"]
    assert sealed["dataset"]["version"] == 2
    assert sealed["dataset"]["revised_from"] == {
        "version": 1,
        "digest": digest,
        "comment_id": c.comment_id,
    }


# ── mutant triage: each test kills a mutants.py survivor ────────────────────


def test_an_eur_prefix_and_the_euro_sign_are_one_unit():
    assert R.numbers("EUR 3.2") == R.numbers("€3.2")


def test_no_text_states_no_numbers():
    assert R.numbers(None) == R.numbers("") == {}


def test_a_lower_case_unit_word_is_a_unit():
    assert R.numbers("240 min") != R.numbers("240 s")
    assert R.numbers("4 hours") == R.numbers("4 hours")


def test_a_number_in_a_table_or_figure_caption_counts():
    base, new = _mutated(lambda d: None)
    new.sections[0].blocks[3] = TableRef(table_id="t1", caption="Assets in 2035")
    assert R._number_reason(base, new) == "changes_number"


def test_a_block_added_with_a_number_is_a_change_not_a_drop():
    base, new = _mutated(
        lambda d: d.sections[1].blocks.append(Paragraph(md="Add 5 MW."))
    )
    assert R.check_invariants(base, new)[0] == "changes_number"


def test_an_edit_in_another_section_at_the_same_index_is_outside_the_anchor():
    base, new = _mutated(lambda d: None)
    new.sections[1].blocks[0] = Paragraph(
        md="Charge window 240 min, a 0.2 share, revised."
    )
    assert R.check_invariants(base, new, _anchor()) == ["edit_outside_anchor"]


def test_an_anchor_past_the_last_block_names_nothing(pdir):
    e = _refused(R.add_comment, pdir, RID, "x", _anchor(block=len(_grid().blocks)))
    assert e.kind == "report_comment_anchor_invalid"


def test_an_out_of_range_or_vanished_anchor_never_crashes_the_check():
    assert R.check_invariants(_doc(), _doc(), _anchor(block=9)) == []
    base, gone = _mutated(lambda d: d.sections.pop(0))
    assert "changes_structure" in R.check_invariants(base, gone, _anchor())
    base, extra = _mutated(
        lambda d: d.sections.append(
            Section(
                section_id="x",
                heading="X",
                source="code",
                status="ok",
                blocks=[Paragraph(md="New.")],
            )
        )
    )
    assert "changes_structure" in R.check_invariants(base, extra, _anchor(section="x"))


def test_a_comment_id_that_is_not_sixteen_hex_is_unreadable(pdir):
    c = _comment(pdir)
    path = store.report_dir(pdir, RID) / R.COMMENTS_FILE
    raw = json.loads(path.read_text())
    raw["comments"][0]["comment_id"] = "../../etc-passwd!"
    path.write_text(json.dumps(raw))
    assert (
        c and _refused(R.load_comments, pdir, RID).kind == "report_comments_unreadable"
    )


def test_an_anchor_block_removed_since_the_comment_is_stale(pdir):
    c = _comment(pdir, _anchor(block=1))
    doc = store.load_report(pdir, RID)
    doc.sections[0].blocks = doc.sections[0].blocks[:1]
    store.save_version(pdir, doc)
    e = _refused(
        R.address,
        pdir,
        RID,
        c.comment_id,
        _digest(pdir),
        R.BulletEdit(kind="bullet_text", items=["x"]),
    )
    assert e.kind == "revision_stale_base"


def test_an_unaudited_section_takes_an_equal_re_spelling(pdir):
    c = _comment(pdir, _anchor(section="summary"))
    _, doc = R.address(
        pdir,
        RID,
        c.comment_id,
        _digest(pdir),
        _para("Charge window 240 min, a 0.20 share."),
    )
    assert doc.version == 2 and doc.sections[1].audit == _doc().sections[1].audit


def _seed_with_grid_audit(pdir, audit):
    doc = _doc()
    doc.sections[0] = doc.sections[0].model_copy(update={"audit": audit})
    store.create_report(pdir, doc)


@pytest.mark.parametrize("facts", [FACTS, []], ids=["verified-only", "unverified-only"])
def test_a_one_sided_audit_is_still_re_run(tmp_path, facts):
    """A section whose audit has only verified numbers, or only unverified
    ones, is an audited section: its audit is re-run, never copied."""
    text = "Grid of 1234 MW and 4.5 h."
    audit = number_audit.audit([text], facts)
    assert bool(audit.verified) != bool(audit.unverified)
    doc = _doc()
    doc.sections[0].blocks[0] = Paragraph(md=text)
    doc.sections[0].blocks[1] = Bullets(items=["Storage"])
    doc.sections[0] = doc.sections[0].model_copy(update={"audit": audit})
    store.create_report(tmp_path, doc)
    c = R.add_comment(tmp_path, RID, "x", _anchor())
    _, out = R.address(
        tmp_path,
        RID,
        c.comment_id,
        _digest(tmp_path),
        _para("Grid of 1,234 MW and 4.5 h."),
    )
    a = out.sections[0].audit
    assert "1,234 MW" in [v.text for v in a.verified] + list(a.unverified)


def test_a_table_edit_does_not_re_judge_a_sections_stored_audit(tmp_path):
    """The stored audit of a section the edit does not touch is compared as
    stored (`edit_outside_anchor`), never re-run: a job audit the re-run
    cannot reproduce (here an unverified number from a withheld statement)
    must not block a table edit."""
    grid = _grid()
    audit = grid.audit.model_copy(
        update={"unverified": [*grid.audit.unverified, "99 MW"]}
    )
    _seed_with_grid_audit(tmp_path, audit)
    c = R.add_comment(tmp_path, RID, "x", _anchor(block=None, table="t1"))
    edit = R.TableCellsEdit(
        kind="table_cells", rows=[["BESS", "1,200 MW"], ["PV", "350"]]
    )
    _, doc = R.address(tmp_path, RID, c.comment_id, _digest(tmp_path), edit)
    assert doc.version == 2


# ── review follow-ups (mp-code-review) ──────────────────────────────────────


def test_a_spaced_minus_is_a_sign():
    assert R.numbers("5 MW") != R.numbers("- 5 MW")
    assert R.numbers("-5 MW") == R.numbers("- 5 MW")


# The stored version format before 07c (master 307dbd38): a version that is
# not a revision keeps exactly these keys, in this order (GK B R7).
V1_KEYS = [
    "schema_version",
    "report_id",
    "version",
    "title",
    "language",
    "created_at",
    "evidence_hash",
    "profile_id",
    "model",
    "mode",
    "template_file_id",
    "sections",
    "tables",
    "figures",
]


def test_an_unrevised_version_keeps_the_pre_07c_key_set(pdir):
    raw = json.loads((store.report_dir(pdir, RID) / "v1.json").read_text())
    assert list(raw) == V1_KEYS
    store.save_version(pdir, store.load_report(pdir, RID))
    assert (
        list(json.loads((store.report_dir(pdir, RID) / "v2.json").read_text()))
        == V1_KEYS
    )


def test_a_per_year_suffix_re_spelling_is_addressed(tmp_path):
    doc = _doc()
    doc.sections[1].blocks[0] = Paragraph(md="LOLE of 4 h/a.")
    store.create_report(tmp_path, doc)
    c = R.add_comment(tmp_path, RID, "x", _anchor(section="summary"))
    _, out = R.address(
        tmp_path, RID, c.comment_id, _digest(tmp_path), _para("LOLE of 4 h/yr.")
    )
    assert out.version == 2


def _stored(pdir):
    return store.report_dir(pdir, RID) / R.COMMENTS_FILE


@pytest.mark.parametrize(
    "change",
    [
        {"status": "addressed"},  # no revision
        {"revision": {"version": 2, "digest": "d"}},  # open with a revision
        {"reason": "outside_edit_set"},  # reason without reasons
        {"reason": "made_up", "reasons": ["made_up"]},
        {  # addressed, yet still carrying an open reason
            "status": "addressed",
            "revision": {"version": 2, "digest": "d"},
            "reason": "outside_edit_set",
            "reasons": ["outside_edit_set"],
        },
    ],
)
def test_a_comment_whose_fields_disagree_is_unreadable(pdir, change):
    _comment(pdir)
    raw = json.loads(_stored(pdir).read_text())
    raw["comments"][0].update(change)
    _stored(pdir).write_text(json.dumps(raw))
    assert _refused(R.load_comments, pdir, RID).kind == "report_comments_unreadable"


def test_the_report_lock_is_a_file_lock_beside_the_report(pdir):
    with store.report_lock(pdir, RID):
        assert (store.reports_dir(pdir) / f".{RID}.lock").exists()
    assert store.list_reports(pdir)[0].report_id == RID  # the lock file is no report


def test_a_lock_held_by_another_process_times_out_as_report_busy(pdir, monkeypatch):
    from contextlib import contextmanager

    from harness import durable

    @contextmanager
    def busy(*a, **k):
        raise TimeoutError("busy")
        yield

    monkeypatch.setattr(durable, "file_lock", busy)
    with pytest.raises(store.ReportBusy):
        store.save_version(pdir, store.load_report(pdir, RID))
    assert store.load_meta(pdir, RID).latest_version == 1
