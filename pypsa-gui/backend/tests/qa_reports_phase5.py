"""
QA: phase 5 of the study-report plan, end to end — a real Energy Hub study
over HTTP, then the round-trip journey on the scripted fake provider:

  1. the study (the same leg ``qa_reports_phase2`` runs);
  2. an evidence-only report, then ``generate_report`` on the fake provider
     (summary + ``fmea_top``) → export → the blob downloaded;
  3. the Word file edited with python-docx: one paragraph of ``fmea_top``
     changed, a comment "shorten this" on a run of ``certification``, and a
     ``w:ins`` / ``w:del`` pair in another section (net-neutral: the same
     word deleted and inserted, so accepting them changes no text);
  4. uploaded as ``kind=report_roundtrip`` → ``POST …/roundtrip``: the new
     version's ``fmea_top`` is ``user_edit`` with the edited text,
     ``certification.pending_instruction == "shorten this"``,
     ``accepted_tracked_changes == 2``, the file bound as the template;
  5. ``GET …/diff/{v}/{v+1}`` marks ``fmea_top`` changed and the rest
     unchanged;
  6. regenerate ``certification`` with NO instruction → the fake saw the
     pending instruction and the new version clears it;
  7. ``GET capabilities`` → ``pdf`` true here (soffice on PATH) → export pdf
     → 500 ``pdf_conversion_failed`` in this container (its ``soffice``
     cannot load a ``.docx``); a workstation with a working LibreOffice gets
     a PDF chip — the driver prints which happened;
  8. the chat tools ``diff_report_versions`` and ``list_report_roundtrips``.

Legs 4–6 need WP12's ``services.reports.roundtrip``; when that module is not
present in the checkout the driver prints ``[SKIP] round-trip legs —
roundtrip module not present`` for those steps and runs the rest. The
provider is ``services.llm_fake.FakeProvider`` installed at the seam the job
router looks up (``routers.report_jobs._provider_for_profile``); no key, no
network.

Plan: docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
(phase 5 gate: WP12–WP14). Runs through ``tests/run_qa_drivers.py``. Exit 0 =
every step that ran passed.
"""
from __future__ import annotations

import copy
import io
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# THE load-bearing import, and it must come first: `qa_support` pins the
# sandbox before anything imports `main` or `settings`.
from tests import qa_support          # noqa: E402
from services.reports.evidence import fmea_top_modes  # noqa: E402

from tests.eh_stage_fixtures import VOLL, certifiable_weak_network  # noqa: E402

PROJECT = "qa_reports_phase5"
BUDGET_SOLVES = 8
STAGES = ["apply_pack", "ens_solve", "fmea_top", "assemble"]
JOB_TIMEOUT = 120.0
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SKIP_NOTE = "[SKIP] round-trip legs — roundtrip module not present"
EDITED_TEXT = "The user rewrote this paragraph in Word."
COMMENT = "shorten this"
TRACKED_SECTION = "target"

PASS = 0
FAIL = 0
SKIP = 0


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _skip(label: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  {SKIP_NOTE}: {label}")


def _roundtrip_available() -> bool:
    from services.reports import roundtrip_service

    return roundtrip_service.roundtrip_available()


def _poll_http(path: str, *, timeout: float = 600.0) -> dict:
    c = qa_support.client()
    t0 = time.time()
    body: dict = {}
    while time.time() - t0 < timeout:
        r = c.get(path)
        if r.status_code == 204:
            return {"status": "no-content"}
        if r.status_code != 200:
            return {"status": f"http-{r.status_code}", "error": r.text[:300]}
        body = r.json()
        if body.get("status") != "running":
            return body
        time.sleep(0.25)
    return {"status": "timeout", **body}


def _as_session(fn, *args, **kwargs):
    """A chat tool under the signed-in session's ``ProjectContext`` (see phase 2)."""
    from services.pypsa_service import PyPSAService

    token = PyPSAService.bind_request_context(qa_support.session_context())
    try:
        return fn(*args, **kwargs)
    finally:
        PyPSAService.reset_request_context(token)


def _poll_job(*, timeout: float = JOB_TIMEOUT) -> dict:
    return _poll_http(f"/api/projects/{PROJECT}/reports/generate/status", timeout=timeout)


def _turn(payload: dict | str) -> dict:
    from services.llm_provider import LLMEvent

    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"events": [LLMEvent(type="text_delta", text=text)],
            "blocks": [{"type": "text", "text": text}]}


def _draft(section_id: str, *paragraphs: str) -> dict:
    return _turn({"section_id": section_id, "paragraphs": list(paragraphs), "bullets": []})


def _blob(file_id: str) -> bytes | None:
    r = qa_support.client().get(f"/api/projects/{PROJECT}/uploads/{file_id}/blob")
    return r.content if r.status_code == 200 else None


def _by_id(doc: dict) -> dict[str, dict]:
    return {s["section_id"]: s for s in doc.get("sections") or []}


class _Seam:
    """Install a provider at the job router's seam for the whole driver."""

    def __init__(self) -> None:
        import routers.report_jobs as jobs

        self.jobs = jobs
        self.orig_provider = jobs._provider_for_profile
        self.orig_resolve = jobs._resolve_profile

    def install(self, provider) -> None:
        self.jobs._provider_for_profile = lambda profile: (provider, None)
        try:
            self.orig_resolve()
        except Exception as exc:                                  # noqa: BLE001
            from types import SimpleNamespace

            print(f"  ! active profile unresolvable ({type(exc).__name__}); using a stand-in")
            self.jobs._resolve_profile = lambda: SimpleNamespace(
                id="qa-fake-profile", model="qa-fake-model", max_output_tokens=None)

    def restore(self) -> None:
        self.jobs._provider_for_profile = self.orig_provider
        self.jobs._resolve_profile = self.orig_resolve


# ── editing the Word file ─────────────────────────────────────────────────

def _section_paragraphs(word, section_id: str) -> list:
    """The body paragraphs between the ``sec:<id>`` bookmark and the next one."""
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    out = []
    inside = False
    for el in word.element.body.iterchildren():
        if el.tag != qn("w:p"):
            continue
        names = [b.get(qn("w:name")) for b in el.iter(qn("w:bookmarkStart"))]
        if any(n and n.startswith("sec:") for n in names):
            if inside:
                break
            inside = any(n == f"sec:{section_id}" for n in names)
            continue
        if inside:
            out.append(Paragraph(el, word))
    return out


def _first_text_paragraph(paragraphs: list):
    return next((p for p in paragraphs if p.text.strip() and p.runs), None)


def _tracked_swap(paragraph, tracked_id: int = 9001) -> bool:
    """
    Delete the paragraph's first run and insert a run with the SAME text after
    it, as Word's track-changes would record a word retyped: one ``w:del``,
    one ``w:ins``, and no text changes once both are accepted.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    if not paragraph.runs:
        return False
    run = paragraph.runs[0]
    text = run.text
    r = run._r
    stamp = "2026-09-29T00:00:00Z"
    deleted = OxmlElement("w:del")
    deleted.set(qn("w:id"), str(tracked_id))
    deleted.set(qn("w:author"), "QA")
    deleted.set(qn("w:date"), stamp)
    inserted = OxmlElement("w:ins")
    inserted.set(qn("w:id"), str(tracked_id + 1))
    inserted.set(qn("w:author"), "QA")
    inserted.set(qn("w:date"), stamp)
    r.addprevious(deleted)
    deleted.append(r)
    for t in r.findall(qn("w:t")):
        t.tag = qn("w:delText")
    new_run = copy.deepcopy(r)
    for t in new_run.findall(qn("w:delText")):
        t.tag = qn("w:t")
    inserted.append(new_run)
    deleted.addnext(inserted)
    return bool(text)


def _edit_export(blob: bytes) -> tuple[bytes, dict]:
    from docx import Document

    word = Document(io.BytesIO(blob))
    notes: dict = {}
    target = _first_text_paragraph(_section_paragraphs(word, "fmea_top"))
    notes["fmea_original"] = target.text if target is not None else None
    if target is not None:
        for run in target.runs[1:]:
            run._r.getparent().remove(run._r)
        target.runs[0].text = EDITED_TEXT
    cert = _first_text_paragraph(_section_paragraphs(word, "certification"))
    notes["certification_original"] = cert.text if cert is not None else None
    if cert is not None:
        word.add_comment(cert.runs[0], text=COMMENT, author="QA", initials="QA")
    other = _first_text_paragraph(_section_paragraphs(word, TRACKED_SECTION))
    notes["tracked_original"] = other.text if other is not None else None
    notes["tracked"] = _tracked_swap(other) if other is not None else False
    out = io.BytesIO()
    word.save(out)
    return out.getvalue(), notes


# ── the journey ───────────────────────────────────────────────────────────

def section_1_real_study() -> dict | None:
    print("\n[1] a real strong_grid study on the certifiable weak network")
    c = qa_support.client()
    qa_support.install_network(certifiable_weak_network())
    qa_support.save_project(PROJECT)

    r = c.put("/api/simulation/solver_config", json={"solver_name": "highs", "voll": VOLL})
    _step("the solver config takes the VoLL", r.status_code == 200, r.text[:200])
    r = c.post("/api/results/eh_study", json={
        "archetype": "strong_grid", "budget_solves": BUDGET_SOLVES, "stages": STAGES})
    _step("the study starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return None
    study = _poll_http("/api/results/eh_study")
    _step("the study finishes", study.get("status") == "done",
          f"status={study.get('status')} error={str(study.get('error'))[:200]}")
    if study.get("status") != "done":
        return None
    r = c.get("/api/results/eh_reference_design")
    _step("the report is persisted for GET", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return None
    rep = r.json()
    comp = rep.get("completeness") or {}
    top = fmea_top_modes(((rep.get("sections") or {}).get("fmea_top") or {}).get("payload"))
    _step("fmea_top is established with at least one ranked mode",
          comp.get("fmea_top") == "ok" and len(top) >= 1,
          f"status={comp.get('fmea_top')} rows={len(top)}")
    return rep


def section_2_generate_and_export(seam: _Seam) -> tuple[str, dict, bytes] | None:
    print("\n[2] evidence-only report → generate (fake provider) → export → download")
    from services import chat_tools
    from services.llm_fake import FakeProvider
    from services.reports.assemble import EXECUTIVE_SUMMARY_ID

    c = qa_support.client()
    r = c.post(f"/api/projects/{PROJECT}/reports",
               json={"mode": "evidence_only", "title": "QA phase-5 evidence-only"})
    _step("the evidence-only report is created", r.status_code == 200, r.text[:200])

    provider = FakeProvider([
        _draft(EXECUTIVE_SUMMARY_ID, "The plan is summarised here."),
        _draft("fmea_top", "Generator g dominates the residual risk.",
               "A second paragraph on the residual modes."),
    ])
    seam.install(provider)
    started = _as_session(chat_tools.generate_report, title="QA phase-5 report",
                          sections=[EXECUTIVE_SUMMARY_ID, "fmea_top"])
    rid = started.get("report_id") or ""
    _step("generate_report starts", started.get("status") == "running" and len(rid) == 16,
          str(started)[:200])
    if len(rid) != 16:
        return None
    status = _poll_job()
    _step("the job reaches done", status.get("status") == "done",
          f"status={status.get('status')} error={status.get('error')}")
    doc = c.get(f"/api/projects/{PROJECT}/reports/{rid}").json()
    by_id = _by_id(doc)
    _step("fmea_top carries the fake's prose (source llm)",
          by_id.get("fmea_top", {}).get("source") == "llm"
          and any(b.get("type") == "paragraph" for b in by_id.get("fmea_top", {}).get("blocks", [])),
          str(by_id.get("fmea_top", {}).get("source")))
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/export", json={"filename": "qa-phase5"})
    _step("the export answers an agent_export chip",
          r.status_code == 200 and r.json().get("kind") == "agent_export", r.text[:200])
    if r.status_code != 200:
        return None
    blob = _blob(r.json()["file_id"])
    _step("the blob route serves the export", blob is not None and blob[:2] == b"PK")
    if blob is None:
        return None
    return rid, doc, blob


def section_3_edit_and_upload(blob: bytes) -> str | None:
    print("\n[3] edit the Word file with python-docx → upload as report_roundtrip")
    edited, notes = _edit_export(blob)
    _step("a paragraph of fmea_top was rewritten", notes.get("fmea_original") is not None,
          f"was: {str(notes.get('fmea_original'))[:60]!r}")
    _step("a comment was anchored on a run of certification",
          notes.get("certification_original") is not None,
          f"on: {str(notes.get('certification_original'))[:60]!r}")
    _step(f"a w:del + w:ins pair was written in {TRACKED_SECTION}", bool(notes.get("tracked")),
          f"on: {str(notes.get('tracked_original'))[:60]!r}")
    c = qa_support.client()
    r = c.post(f"/api/projects/{PROJECT}/uploads", params={"kind": "report_roundtrip"},
               files={"file": ("qa-phase5-edited.docx", edited, DOCX_MIME)})
    ok = r.status_code == 200 and r.json().get("kind") == "report_roundtrip"
    _step("the edited copy uploads as kind report_roundtrip", ok, r.text[:200])
    if not ok:
        return None
    fid = r.json()["file_id"]
    r = c.get(f"/api/projects/{PROJECT}/uploads", params={"kind": "report_roundtrip"})
    _step("GET /uploads?kind=report_roundtrip lists it",
          r.status_code == 200 and [u["file_id"] for u in r.json()] == [fid], r.text[:160])
    return fid


def section_4_roundtrip(rid: str, base: dict, fid: str) -> int | None:
    print("\n[4] POST …/roundtrip → the merged version")
    if not _roundtrip_available():
        for label in ("POST roundtrip", "fmea_top is user_edit with the edited text",
                      "certification carries the pending instruction",
                      "accepted_tracked_changes == 2", "the file is bound as the template"):
            _skip(label)
        return None
    c = qa_support.client()
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/roundtrip", json={"file_id": fid})
    _step("POST roundtrip answers 200", r.status_code == 200, r.text[:400])
    if r.status_code != 200:
        return None
    body = r.json()
    result = body.get("result") or {}
    _step("the version stepped by one",
          body.get("version") == base.get("version", 0) + 1,
          f"version={body.get('version')} base={base.get('version')}")
    _step("the file is bound as the template", body.get("template_file_id") == fid,
          str(body.get("template_file_id")))
    _step("accepted_tracked_changes == 2", result.get("accepted_tracked_changes") == 2,
          f"accepted={result.get('accepted_tracked_changes')} unmatched={result.get('unmatched')}")
    doc = c.get(f"/api/projects/{PROJECT}/reports/{rid}").json()
    by_id = _by_id(doc)
    fmea = by_id.get("fmea_top", {})
    texts = [b.get("md", "") for b in fmea.get("blocks", []) if b.get("type") == "paragraph"]
    _step("fmea_top is user_edit with the edited text",
          fmea.get("source") == "user_edit" and any(EDITED_TEXT in t for t in texts),
          f"source={fmea.get('source')} paragraphs={[t[:40] for t in texts]}")
    cert = by_id.get("certification", {})
    _step("certification.pending_instruction == 'shorten this'",
          cert.get("pending_instruction") == COMMENT,
          f"pending={cert.get('pending_instruction')!r} comments={cert.get('comments')}")
    _step("the pending instruction is on the merged version only",
          _by_id(c.get(f"/api/projects/{PROJECT}/reports/{rid}/versions/{base['version']}").json())
          .get("certification", {}).get("pending_instruction") is None)
    return body.get("version")


def section_5_diff(rid: str, a: int, b: int) -> None:
    print(f"\n[5] GET …/versions/{a}/diff/{b}")
    if not _roundtrip_available():
        _skip("the diff marks fmea_top changed and the rest unchanged")
        return
    c = qa_support.client()
    r = c.get(f"/api/projects/{PROJECT}/reports/{rid}/versions/{a}/diff/{b}")
    _step("the diff answers 200", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    rows = {row["section_id"]: row for row in r.json().get("sections") or []}
    changed = sorted(sid for sid, row in rows.items() if row["change"] != "unchanged")
    _step("fmea_top is changed and the rest unchanged (the tracked pair was net-neutral)",
          changed == ["fmea_top"], f"changed={changed}")
    _step("fmea_top: source_a llm → source_b user_edit",
          rows.get("fmea_top", {}).get("source_a") == "llm"
          and rows.get("fmea_top", {}).get("source_b") == "user_edit",
          str({k: rows.get("fmea_top", {}).get(k) for k in ("source_a", "source_b")}))
    _step("certification's row carries the pending instruction",
          rows.get("certification", {}).get("pending_instruction") == COMMENT,
          str(rows.get("certification", {}).get("pending_instruction")))
    r = c.get(f"/api/projects/{PROJECT}/reports/{rid}/versions/{a}/diff/{b + 50}")
    _step("an unknown version is 404 report_version_not_found",
          r.status_code == 404 and (r.json().get("detail") or {}).get("error_kind")
          == "report_version_not_found", r.text[:160])


def section_6_regenerate_with_pending(rid: str, seam: _Seam) -> None:
    print("\n[6] regenerate certification with no instruction → the pending one is used and cleared")
    if not _roundtrip_available():
        _skip("regenerate uses and clears pending_instruction")
        return
    from services.llm_fake import FakeProvider

    c = qa_support.client()
    before = c.get(f"/api/projects/{PROJECT}/reports/{rid}").json()
    provider = FakeProvider([_draft("certification", "Certification, shortened.")])
    seam.install(provider)
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/sections/certification/regenerate", json={})
    _step("POST regenerate (no instruction) starts", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    status = _poll_job()
    _step("the job reaches done at version+1",
          status.get("status") == "done" and status.get("version") == before.get("version", 0) + 1,
          f"status={status.get('status')} version={status.get('version')} error={status.get('error')}")
    sent = json.dumps([r.messages for r in provider.requests], default=str)
    _step("the fake saw the pending instruction in the request",
          len(provider.requests) == 1 and COMMENT in sent, f"requests={len(provider.requests)}")
    after = _by_id(c.get(f"/api/projects/{PROJECT}/reports/{rid}").json())
    cert = after.get("certification", {})
    _step("the new version's pending_instruction is null",
          cert.get("pending_instruction") is None and cert.get("source") == "llm",
          f"pending={cert.get('pending_instruction')!r} source={cert.get('source')}")


def section_7_pdf(rid: str) -> None:
    print("\n[7] capabilities → export pdf")
    c = qa_support.client()
    r = c.get(f"/api/projects/{PROJECT}/reports/capabilities")
    caps = r.json() if r.status_code == 200 else {}
    _step("GET capabilities answers {pdf: bool}", r.status_code == 200 and isinstance(caps.get("pdf"), bool),
          r.text[:120])
    from services.reports import pdf as pdf_mod

    binary = pdf_mod.soffice_binary()
    _step("pdf reflects soffice/libreoffice on PATH", caps.get("pdf") == (binary is not None),
          f"binary={binary}")
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/export",
               json={"format": "pdf", "filename": "qa-phase5"})
    if not caps.get("pdf"):
        _step("export pdf is 501 pdf_not_available without soffice",
              r.status_code == 501 and (r.json().get("detail") or {}).get("error_kind")
              == "pdf_not_available", r.text[:200])
        return
    if r.status_code == 200:
        meta = r.json()
        blob = _blob(meta.get("file_id", ""))
        print("  → this LibreOffice converted the document: a PDF chip came back")
        _step("export pdf answers a PDF agent_export chip (working LibreOffice)",
              meta.get("kind") == "agent_export" and meta.get("mime") == "application/pdf"
              and meta.get("filename", "").endswith(".pdf")
              and blob is not None and blob.startswith(b"%PDF-"), str(meta)[:200])
        return
    detail = (r.json().get("detail") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
    print("  → this container's soffice could not load the .docx: the conversion failed")
    _step("export pdf is 500 pdf_conversion_failed with the stderr head (this container)",
          r.status_code == 500 and detail.get("error_kind") == "pdf_conversion_failed"
          and bool(detail.get("message")), f"HTTP {r.status_code} {r.text[:200]}")


def section_8_chat_tools(rid: str, a: int | None, b: int | None, fid: str) -> None:
    print("\n[8] chat tools diff_report_versions and list_report_roundtrips")
    from services import chat_tools

    listed = _as_session(chat_tools.list_report_roundtrips)
    _step("list_report_roundtrips lists the edited copy",
          [u.get("file_id") for u in listed] == [fid]
          and listed[0].get("kind") == "report_roundtrip", str(listed)[:200])
    if a is None or b is None:
        base = qa_support.client().get(f"/api/projects/{PROJECT}/reports/{rid}").json()
        a = b = base.get("version", 1)
    out = _as_session(chat_tools.diff_report_versions, rid, a, b)
    rows = {row["section_id"]: row["change"] for row in out.get("sections") or []}
    expected_changed = ["fmea_top"] if a != b else []
    _step("diff_report_versions agrees with the route",
          out.get("a") == a and out.get("b") == b and out.get("changed") == expected_changed
          and set(rows.values()) <= {"changed", "unchanged"},
          f"changed={out.get('changed')} added={out.get('added')} removed={out.get('removed')}")


def main() -> int:
    print("=" * 60)
    print("QA: reports phase 5 — EH study → export → edit in Word → round trip → diff → regenerate → PDF")
    print("=" * 60)
    if not _roundtrip_available():
        print(f"  {SKIP_NOTE}: services.reports.roundtrip is not importable in this "
              "checkout; legs 4–6 are reported as SKIP, not FAIL")
    crashed = False
    seam: _Seam | None = None
    try:
        qa_support.reset_backend()
        qa_support.delete_project(PROJECT)
        from services import chat_tools

        chat_tools.set_acting_user(str(qa_support.user().id))
        seam = _Seam()
        rep = section_1_real_study()
        if rep is not None:
            made = section_2_generate_and_export(seam)
            if made is not None:
                rid, base, blob = made
                fid = section_3_edit_and_upload(blob)
                if fid is not None:
                    merged = section_4_roundtrip(rid, base, fid)
                    section_5_diff(rid, base["version"], merged if merged else base["version"] + 1)
                    section_6_regenerate_with_pending(rid, seam)
                    section_7_pdf(rid)
                    section_8_chat_tools(rid, base["version"] if merged else None, merged, fid)
    except Exception as exc:                                     # noqa: BLE001
        crashed = True
        import traceback
        print(f"\n  [FAIL] the journey raised {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        try:
            if seam is not None:
                seam.restore()
            from services import chat_tools

            chat_tools.set_acting_user(None)
            qa_support.delete_project(PROJECT)
            qa_support.reset_backend()
        except Exception as exc:                                 # noqa: BLE001
            print(f"  ! cleanup raised {type(exc).__name__}: {exc}")

    total = PASS + FAIL + (1 if crashed else 0)
    print("\n" + "=" * 60)
    print(f"Total: {total}")
    print(f"Pass:  {PASS}")
    print(f"Fail:  {FAIL + (1 if crashed else 0)}")
    print(f"Skip:  {SKIP}" + ("  (round-trip legs — roundtrip module not present)" if SKIP else ""))
    print("=" * 60)
    return 1 if (FAIL or crashed) else 0


if __name__ == "__main__":
    sys.exit(main())
