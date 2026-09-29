"""
QA: phase 4 of the study-report plan, end to end — a real Energy Hub study
over HTTP, then the user-template journey on the scripted fake provider:

  1. the study (the same leg ``qa_reports_phase2`` runs);
  2. both template fixtures built with ``tests/fixtures/report_templates/_build.py``
     and uploaded as ``kind=report_template``; the list filters by kind;
  3. an evidence-only report bound to the TAGGED fixture and exported: the
     Word file re-opens with the report's title where ``{{ meta.title }}``
     was, one looped row per ``fmea_top`` row, no ``{{`` left anywhere;
  4. the CORPORATE (untagged) fixture bound instead: the mapping job on the
     fake provider (scripted with a plan that renames heading 4 and drops 5)
     → status done → ``GET …/template`` shows the plan → ``PUT`` an edited
     plan → export: cover text and the "Confidential" footer intact, the
     renamed heading present, ``updateFields`` in ``settings.xml``, the
     ``sec:fmea_top`` bookmark present;
  5. ``generate_report`` with the GERMAN corporate fixture and no ``language``
     → the document's ``language == "de"``;
  6. unbind → export → the default writer again.

Leg 4 (and the untagged half of leg 5's checks) needs WP10's
``services.reports.template_untagged``; when that module is not present in
the checkout the driver prints ``[SKIP] untagged legs — template_untagged
not present`` for those steps and reports the tagged-leg totals. The
provider is ``services.llm_fake.FakeProvider`` installed at the seam the job
router looks up (``routers.report_jobs._provider_for_profile``); no key, no
network.

Plan: docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
(phase 4 gate: WP8–WP11). Runs through ``tests/run_qa_drivers.py``. Exit 0 =
every step that ran passed.
"""
from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import shutil
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# THE load-bearing import, and it must come first: `qa_support` pins the
# sandbox before anything imports `main` or `settings`.
from tests import qa_support          # noqa: E402

from tests.eh_stage_fixtures import VOLL, certifiable_weak_network  # noqa: E402

PROJECT = "qa_reports_phase4"
BUDGET_SOLVES = 8
STAGES = ["apply_pack", "ens_solve", "fmea_top", "assemble"]
JOB_TIMEOUT = 120.0
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_BUILD = pathlib.Path(__file__).resolve().parent / "fixtures" / "report_templates" / "_build.py"
RENAMED_HEADING = "4 Residual failure modes (renamed by the plan)"
EDITED_HEADING = "4 Residual failure modes (edited by the user)"
SKIP_NOTE = "[SKIP] untagged legs — template_untagged not present"

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


def _untagged_available() -> bool:
    from services.reports import templates

    return templates.untagged_available()


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


def _open_docx(blob: bytes):
    from docx import Document

    return Document(io.BytesIO(blob))


def _docx_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            parts.extend(c.text for c in row.cells)
    for section in doc.sections:
        parts.extend(p.text for p in section.header.paragraphs)
        parts.extend(p.text for p in section.footer.paragraphs)
    return "\n".join(parts)


def _bookmarks(doc) -> list[str]:
    from docx.oxml.ns import qn

    return [el.get(qn("w:name")) for el in doc.element.body.iter(qn("w:bookmarkStart"))]


def _settings_xml(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        try:
            return zf.read("word/settings.xml").decode("utf-8", "replace")
        except KeyError:
            return ""


def _blob(file_id: str) -> bytes | None:
    r = qa_support.client().get(f"/api/projects/{PROJECT}/uploads/{file_id}/blob")
    return r.content if r.status_code == 200 else None


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
    top = ((rep.get("sections") or {}).get("fmea_top") or {}).get("payload", {}).get("top") or []
    _step("fmea_top is established with at least one ranked mode",
          comp.get("fmea_top") == "ok" and len(top) >= 1,
          f"status={comp.get('fmea_top')} rows={len(top)}")
    return rep


def section_2_upload_templates(fixture_dir: pathlib.Path) -> dict[str, str] | None:
    print("\n[2] build both fixtures with _build.py and upload them as report_template")
    spec = importlib.util.spec_from_file_location("report_template_fixtures", _BUILD)
    builder = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(builder)
    paths = builder.build_all(fixture_dir)
    paths.update(builder.build_all(fixture_dir, language="de"))
    _step("the builder wrote the tagged, corporate and German corporate fixtures",
          all(paths[n].is_file() for n in (builder.TAGGED, builder.CORPORATE, builder.CORPORATE_DE)),
          str(sorted(paths)))

    c = qa_support.client()
    ids: dict[str, str] = {}
    for key, name in (("tagged", builder.TAGGED), ("corporate", builder.CORPORATE),
                      ("corporate_de", builder.CORPORATE_DE)):
        r = c.post(f"/api/projects/{PROJECT}/uploads", params={"kind": "report_template"},
                   files={"file": (name, paths[name].read_bytes(), DOCX_MIME)})
        ok = r.status_code == 200 and r.json().get("kind") == "report_template"
        _step(f"{name} uploads as kind report_template", ok, r.text[:200])
        if not ok:
            return None
        ids[key] = r.json()["file_id"]
    r = c.get(f"/api/projects/{PROJECT}/uploads", params={"kind": "report_template"})
    listed = {u["file_id"] for u in (r.json() if r.status_code == 200 else [])}
    _step("GET /uploads?kind=report_template lists exactly the three",
          r.status_code == 200 and listed == set(ids.values()), f"HTTP {r.status_code}")
    r = c.get(f"/api/projects/{PROJECT}/uploads", params={"kind": "nope"})
    _step("an unknown kind is 400 unsupported_upload_kind",
          r.status_code == 400 and (r.json().get("detail") or {}).get("error_kind")
          == "unsupported_upload_kind", r.text[:160])
    tools = _as_session(__import__("services.chat_tools", fromlist=["x"]).list_report_templates)
    _step("list_report_templates (chat tool) agrees",
          {t.get("file_id") for t in tools} == set(ids.values()), str(tools)[:200])
    return ids


def section_3_tagged(rep: dict, ids: dict[str, str]) -> str | None:
    print("\n[3] evidence-only report → bind the tagged fixture → export → re-open")
    c = qa_support.client()
    r = c.post(f"/api/projects/{PROJECT}/reports",
               json={"mode": "evidence_only", "title": "QA phase-4 tagged report"})
    _step("the evidence-only report is created", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return None
    rid = r.json()["report_id"]
    r = c.get(f"/api/projects/{PROJECT}/reports/{rid}/template")
    _step("GET template is all null before any binding",
          r.status_code == 200 and r.json() == {"template_file_id": None, "mode": None,
                                                "language": None, "outline": None, "plan": None},
          r.text[:160])
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/template", json={"file_id": ids["tagged"]})
    body = r.json() if r.status_code == 200 else {}
    _step("binding the tagged fixture answers mode tagged with its outline",
          r.status_code == 200 and body.get("mode") == "tagged"
          and body.get("template_file_id") == ids["tagged"]
          and [t["kind"] for t in (body.get("outline") or {}).get("tags", [])][:3]
          == ["var", "var", "for"], r.text[:200])
    doc = c.get(f"/api/projects/{PROJECT}/reports/{rid}").json()
    _step("the binding is version 2 of the document with template_file_id set",
          doc.get("version") == 2 and doc.get("template_file_id") == ids["tagged"],
          f"version={doc.get('version')}")
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/export", json={"filename": "qa-phase4-tagged"})
    _step("the export answers an agent_export chip",
          r.status_code == 200 and r.json().get("kind") == "agent_export", r.text[:200])
    if r.status_code != 200:
        return rid
    blob = _blob(r.json()["file_id"])
    if blob is None:
        _step("the blob route serves the export", False)
        return rid
    word = _open_docx(blob)
    text = _docx_text(word)
    _step("the export re-opens with python-docx", bool(text))
    _step("the report title stands where {{ meta.title }} was",
          "QA phase-4 tagged report" in text and "meta.title" not in text)
    _step("no tag is left anywhere (body, tables, header, footer)",
          "{{" not in text and "{%" not in text)
    top = ((rep.get("sections") or {}).get("fmea_top") or {}).get("payload", {}).get("top") or []
    table = word.tables[0] if word.tables else None
    rows = [[cell.text for cell in row.cells] for row in table.rows] if table is not None else []
    fmea_rows = (doc.get("tables") or {}).get("fmea_top", {}).get("rows") or []
    _step("the looped table carries one row per fmea_top row (header + N), the template row gone",
          table is not None and rows[0] == ["Loop", "Rank", "Component", "End"]
          and len(rows) == 1 + len(fmea_rows) and len(fmea_rows) == len(top)
          and all(rows[i + 1][1] == fmea_rows[i][0] and rows[i + 1][2] == fmea_rows[i][1]
                  for i in range(len(fmea_rows))),
          f"rows={len(rows)} fmea={len(fmea_rows)} top={len(top)}")
    _step("the footer carries the evidence hash", doc.get("evidence_hash", "?") in text)
    _step("it is the template's layout, not the default writer's",
          "Generated by PyPSA Studio" not in text)
    return rid


def _plan_from_outline(outline: dict, rename_text: str) -> dict:
    def idx(prefix: str) -> int:
        return next(h["index"] for h in outline["headings"] if h["text"].startswith(prefix))

    return {
        "entries": [
            {"heading_index": idx("1 "), "action": "keep", "new_text": None,
             "section_ids": ["executive_summary"]},
            {"heading_index": idx("2 "), "action": "keep", "new_text": None, "section_ids": []},
            {"heading_index": idx("3 "), "action": "keep", "new_text": None,
             "section_ids": ["certification"]},
            {"heading_index": idx("4 "), "action": "rename", "new_text": rename_text,
             "section_ids": ["fmea_top"]},
            {"heading_index": idx("5 "), "action": "drop", "new_text": None, "section_ids": []},
        ],
        "inserted": [],
        "placeholders": {"[Client name]": "QA client"},
        "unmapped_sections": [],
        "notes": [],
    }


def section_4_untagged(rid: str, ids: dict[str, str], seam: _Seam) -> None:
    print("\n[4] bind the corporate fixture → mapping job → PUT an edited plan → export")
    if not _untagged_available():
        for label in ("bind + propose_mapping job", "GET template shows the plan",
                      "PUT an edited plan", "export into the corporate template"):
            _skip(label)
        return
    from services.llm_fake import FakeProvider

    c = qa_support.client()
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/template", json={"file_id": ids["corporate"]})
    body = r.json() if r.status_code == 200 else {}
    _step("binding the corporate fixture answers mode untagged, language en",
          r.status_code == 200 and body.get("mode") == "untagged" and body.get("language") == "en"
          and (body.get("outline") or {}).get("has_toc") is True, r.text[:200])
    if r.status_code != 200:
        return
    outline = body["outline"]
    plan = _plan_from_outline(outline, RENAMED_HEADING)
    provider = FakeProvider([_turn(plan)])
    seam.install(provider)
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/template/plan", json={})
    _step("POST template/plan starts the mapping job",
          r.status_code == 200 and r.json() == {"status": "running", "report_id": rid}, r.text[:200])
    if r.status_code != 200:
        return
    status = _poll_job()
    _step("the mapping job reaches done with mode 'mapping'",
          status.get("status") == "done" and status.get("mode") == "mapping",
          f"status={status.get('status')} error={status.get('error')} "
          f"failures={status.get('prose_failures')}")
    _step("the fake saw exactly one request", len(provider.requests) == 1,
          f"requests={len(provider.requests)}")
    got = c.get(f"/api/projects/{PROJECT}/reports/{rid}/template").json()
    stored = got.get("plan") or {}
    renamed = [e for e in stored.get("entries") or [] if e.get("action") == "rename"]
    dropped = [e for e in stored.get("entries") or [] if e.get("action") == "drop"]
    _step("GET template shows the stored plan: heading 4 renamed, heading 5 dropped",
          got.get("mode") == "untagged" and len(renamed) == 1
          and renamed[0].get("new_text") == RENAMED_HEADING and len(dropped) == 1,
          f"entries={[(e.get('heading_index'), e.get('action')) for e in stored.get('entries') or []]} "
          f"notes={stored.get('notes')}")
    if not renamed:
        return
    edited = json.loads(json.dumps(stored))
    for entry in edited["entries"]:
        if entry["action"] == "rename":
            entry["new_text"] = EDITED_HEADING
    r = c.put(f"/api/projects/{PROJECT}/reports/{rid}/template/plan", json=edited)
    _step("PUT the edited plan stores it",
          r.status_code == 200 and any(e.get("new_text") == EDITED_HEADING
                                       for e in r.json().get("entries") or []), r.text[:200])
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/export",
               json={"filename": "qa-phase4-corporate"})
    _step("the export into the corporate template answers a chip", r.status_code == 200, r.text[:300])
    if r.status_code != 200:
        return
    blob = _blob(r.json()["file_id"])
    if blob is None:
        _step("the blob route serves the corporate export", False)
        return
    word = _open_docx(blob)
    text = _docx_text(word)
    _step("the cover text is intact", "Energy Hub Reference Design" in text)
    _step("the 'Confidential' footer is intact", "Confidential" in text)
    _step("the renamed (then edited) heading is present", EDITED_HEADING in text)
    _step("the dropped 'Lorem ipsum' heading is gone", "Lorem ipsum" not in text)
    _step("updateFields is set in settings.xml", "updateFields" in _settings_xml(blob))
    _step("the sec:fmea_top bookmark is present", "sec:fmea_top" in _bookmarks(word),
          str(_bookmarks(word))[:160])


def section_5_generate_de(rep: dict, ids: dict[str, str], seam: _Seam) -> None:
    print("\n[5] generate_report with the German corporate template and no language → de")
    from services import chat_tools
    from services.llm_fake import FakeProvider
    from services.reports.assemble import EXECUTIVE_SUMMARY_ID

    targets = [EXECUTIVE_SUMMARY_ID, "fmea_top"]
    provider = FakeProvider([_draft(EXECUTIVE_SUMMARY_ID, "Die Zusammenfassung."),
                             _draft("fmea_top", "Generator g dominiert.")])
    seam.install(provider)
    started = _as_session(chat_tools.generate_report, title="QA Phase-4 Bericht",
                          sections=targets, template_file_id=ids["corporate_de"])
    rid = started.get("report_id") or ""
    _step("generate_report(template_file_id=<de>) starts",
          started.get("status") == "running" and len(rid) == 16, str(started)[:200])
    if len(rid) != 16:
        return
    status = _poll_job()
    _step("the job reaches done", status.get("status") == "done",
          f"status={status.get('status')} error={status.get('error')}")
    c = qa_support.client()
    doc = c.get(f"/api/projects/{PROJECT}/reports/{rid}").json()
    _step("the document's language is 'de' (the template's), bound to the template",
          doc.get("language") == "de" and doc.get("template_file_id") == ids["corporate_de"],
          f"language={doc.get('language')} template={doc.get('template_file_id')}")
    _step("the German language reached the section requests",
          bool(provider.requests) and "de" in json.dumps(provider.requests[0].messages, default=str))
    got = c.get(f"/api/projects/{PROJECT}/reports/{rid}/template").json()
    _step("GET template on the generated report: untagged, de, no plan yet",
          got.get("mode") == "untagged" and got.get("language") == "de" and got.get("plan") is None,
          str({k: got.get(k) for k in ("mode", "language", "plan")}))
    listed = _as_session(chat_tools.list_reports)
    meta = next((m for m in listed if m.get("report_id") == rid), {})
    _step("list_reports carries template_mode/template_language",
          meta.get("template_mode") == "untagged" and meta.get("template_language") == "de",
          str({k: meta.get(k) for k in ("template_mode", "template_language")}))
    tool_view = _as_session(chat_tools.get_report_template, rid)
    _step("get_report_template (chat tool) agrees", tool_view.get("template_file_id") == ids["corporate_de"])


def section_6_unbind(rid: str) -> None:
    print("\n[6] unbind → export → the default writer again")
    from services import chat_tools

    out = _as_session(chat_tools.set_report_template, rid, None)
    _step("set_report_template(rid, null) unbinds",
          out.get("template_file_id") is None and out.get("mode") is None, str(out)[:200])
    c = qa_support.client()
    got = c.get(f"/api/projects/{PROJECT}/reports/{rid}/template").json()
    _step("GET template is all null again, the plan cleared",
          got.get("template_file_id") is None and got.get("plan") is None, str(got)[:160])
    r = c.post(f"/api/projects/{PROJECT}/reports/{rid}/export", json={"filename": "qa-phase4-default"})
    _step("the export answers a chip", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    blob = _blob(r.json()["file_id"])
    text = _docx_text(_open_docx(blob)) if blob else ""
    _step("it is the default writer's document (its footer line, no template text)",
          "Generated by PyPSA Studio" in text and "Evidence hash in the footer." not in text
          and "Confidential" not in text and EDITED_HEADING not in text)


def main() -> int:
    print("=" * 60)
    print("QA: reports phase 4 — EH study → user templates (tagged + untagged) on the fake provider")
    print("=" * 60)
    if not _untagged_available():
        print(f"  {SKIP_NOTE}: services.reports.template_untagged is not importable in this "
              "checkout; the untagged legs are reported as SKIP, not FAIL")
    crashed = False
    seam: _Seam | None = None
    fixture_dir = pathlib.Path(tempfile.mkdtemp(prefix="qa_reports_phase4_"))
    try:
        qa_support.reset_backend()
        qa_support.delete_project(PROJECT)
        from services import chat_tools

        chat_tools.set_acting_user(str(qa_support.user().id))
        seam = _Seam()
        rep = section_1_real_study()
        if rep is not None:
            ids = section_2_upload_templates(fixture_dir)
            if ids is not None:
                rid = section_3_tagged(rep, ids)
                if rid is not None:
                    section_4_untagged(rid, ids, seam)
                    section_5_generate_de(rep, ids, seam)
                    section_6_unbind(rid)
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
            shutil.rmtree(fixture_dir, ignore_errors=True)
        except Exception as exc:                                 # noqa: BLE001
            print(f"  ! cleanup raised {type(exc).__name__}: {exc}")

    total = PASS + FAIL + (1 if crashed else 0)
    print("\n" + "=" * 60)
    print(f"Total: {total}")
    print(f"Pass:  {PASS}")
    print(f"Fail:  {FAIL + (1 if crashed else 0)}")
    print(f"Skip:  {SKIP}" + ("  (untagged legs — template_untagged not present)" if SKIP else ""))
    print("=" * 60)
    return 1 if (FAIL or crashed) else 0


if __name__ == "__main__":
    sys.exit(main())
