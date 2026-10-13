"""
The round-trip helper of ``smoke-reports.mjs`` (phase 5 browser leg): take an
exported report ``.docx``, edit it the way a user would in Word, and write the
edited copy next to it.

    python smoke_reports_edit_docx.py IN.docx OUT.docx

Edits, in this order, on the sections the writer bookmarked (``sec:<id>``):

1. one NEW paragraph right after the heading of the first section whose id
   is in ``PREFERRED_EDIT`` (else the first section) — a plain paragraph is
   what the round-trip reader turns into a ``Paragraph`` block, so the
   section comes back ``changed`` with ``source: user_edit`` whatever kind of
   report (evidence-only or generated) the export was;
2. one Word comment (``python-docx`` 1.2 ``add_comment``) on the first run of
   the first text paragraph inside a DIFFERENT section — the reader turns it
   into that section's ``pending_instruction``.

Prints one JSON line: ``{"edited": id, "edited_heading": text,
"commented": id, "commented_heading": text}``. Exit 1 when the file has no
``sec:`` bookmark (not a report export) or no section can take the comment.

Only python-docx is needed (the ``SMOKE_PYTHON`` interpreter has it, it
imports pypsa too). No backend import: this file runs outside the sandbox
the QA drivers pin, so it must not touch ``main`` or ``settings``.
"""
from __future__ import annotations

import json
import sys

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

EDITED_TEXT = "The user rewrote this section in Word (smoke round trip)."
COMMENT = "shorten this (smoke)"
PREFERRED_EDIT = ("fmea_top", "executive_summary")
PREFERRED_COMMENT = ("certification", "target", "sizing")
BOOKMARK_PREFIX = "sec:"
APPENDIX_ID = "numbers_to_check"


def _sections(doc) -> list[tuple[str, Paragraph, list[Paragraph]]]:
    """``(section_id, heading paragraph, body paragraphs)`` per bookmarked section."""
    out: list[tuple[str, Paragraph, list[Paragraph]]] = []
    current: tuple[str, Paragraph, list[Paragraph]] | None = None
    for el in doc.element.body.iterchildren():
        if el.tag != qn("w:p"):
            continue
        names = [b.get(qn("w:name")) for b in el.iter(qn("w:bookmarkStart"))]
        sec = next((n[len(BOOKMARK_PREFIX):] for n in names
                    if n and n.startswith(BOOKMARK_PREFIX)), None)
        if sec is not None:
            if current is not None:
                out.append(current)
            current = None if sec == APPENDIX_ID else (sec, Paragraph(el, doc), [])
            if sec == APPENDIX_ID:
                break
            continue
        if current is not None:
            current[2].append(Paragraph(el, doc))
    if current is not None:
        out.append(current)
    return out


def _pick(sections, preferred, *, exclude: str | None, need_run: bool):
    def ok(sec) -> bool:
        sid, _heading, body = sec
        if sid == exclude:
            return False
        if need_run:
            return any(p.text.strip() and p.runs for p in body)
        return True

    for sid in preferred:
        for sec in sections:
            if sec[0] == sid and ok(sec):
                return sec
    return next((sec for sec in sections if ok(sec)), None)


def main(src: str, dst: str) -> int:
    doc = Document(src)
    sections = _sections(doc)
    if not sections:
        print("no sec:<id> bookmark in the file — not a report export", file=sys.stderr)
        return 1
    edit = _pick(sections, PREFERRED_EDIT, exclude=None, need_run=False)
    comment = _pick(sections, PREFERRED_COMMENT, exclude=edit[0], need_run=True)
    if comment is None:
        print("no other section has a paragraph with runs to comment on", file=sys.stderr)
        return 1

    # 1. a new paragraph right after the heading — python-docx appends at the
    #    end, so the element is moved into place.
    new_p = doc.add_paragraph(EDITED_TEXT)
    edit[1]._p.addnext(new_p._p)

    # 2. a comment on the first run of the section's first text paragraph
    target = next(p for p in comment[2] if p.text.strip() and p.runs)
    doc.add_comment(target.runs[0], text=COMMENT, author="Smoke", initials="SM")

    doc.save(dst)
    print(json.dumps({
        "edited": edit[0], "edited_heading": edit[1].text,
        "commented": comment[0], "commented_heading": comment[1].text,
        "edited_text": EDITED_TEXT, "comment": COMMENT,
    }))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: smoke_reports_edit_docx.py IN.docx OUT.docx")
    sys.exit(main(sys.argv[1], sys.argv[2]))
