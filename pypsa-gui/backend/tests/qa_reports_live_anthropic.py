"""
QA: the LIVE LLM probe of the study-report plan — one real generation on the
active profile through the CHAT TOOL path, and the number audit inspected on
the real prose.

  1. a real ``strong_grid`` study over HTTP (the leg every report driver runs);
  2. ``generate_report`` (chat tool) on the active profile — no fake at the
     seam, the provider is whatever ``llm_config.resolve_profile(None)``
     builds; ``get_report_status`` polled until the job leaves ``running``;
     the record names the writer (``profile_id``, ``model``);
  3. ``get_report`` (chat tool): every generated section is ``llm`` or says
     "prose not established" with the reason; the number audit is re-derived
     on the stored prose with the audit's own tokeniser and EVERY token is
     either in ``audit.verified`` or in ``audit.unverified`` — none silently
     accepted, none silently dropped;
  4. ``export_report_docx`` (chat tool): the "Numbers to check" appendix
     lists every unverified number of every section under that section's
     heading, and is absent when nothing was flagged;
  5. ``regenerate_report_section`` with an instruction → the next version,
     every other section byte-identical.

The probe needs a key for the active anthropic-wire profile
(``ANTHROPIC_API_KEY`` in the environment, the same variable the backend's
``llm_anthropic.build_client`` reads). Without one the driver prints
``UNPROBED`` and exits 0 — that is the honest outcome under
``run_qa_drivers.py`` in CI, where no key exists; it is NOT a pass. Pass
``--require-key`` to make a missing key exit 2 instead (a workstation run
that expects to probe). ``PYPSA_GUI_TEST_LIVE_PROFILE`` names another
configured profile to write with instead of the active one.

Plan: docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md
(phase-2 row: "live probe"). Findings §7.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# THE load-bearing import, and it must come first: `qa_support` pins the
# sandbox before anything imports `main` or `settings`.
from tests import qa_support          # noqa: E402
from services.reports.evidence import fmea_top_modes  # noqa: E402

from tests.eh_stage_fixtures import VOLL, certifiable_weak_network  # noqa: E402

PROJECT = "qa_reports_live_anthropic"
BUDGET_SOLVES = 8
STAGES = ["apply_pack", "ens_solve", "fmea_top", "assemble"]
JOB_TIMEOUT = 900.0
KEY_VAR = "ANTHROPIC_API_KEY"
APPENDIX_HEADING = "Numbers to check"
INSTRUCTION = "Rewrite in two sentences, naming the top failure mode."

PASS = 0
FAIL = 0


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _as_session(fn, *args, **kwargs):
    """A chat tool under the signed-in session's ``ProjectContext`` (see phase 2)."""
    from services.pypsa_service import PyPSAService

    token = PyPSAService.bind_request_context(qa_support.session_context())
    try:
        return fn(*args, **kwargs)
    finally:
        PyPSAService.reset_request_context(token)


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


def _poll_tool(*, timeout: float = JOB_TIMEOUT) -> dict:
    """``get_report_status`` (the chat tool, not the route) until the job leaves running."""
    from services import chat_tools

    t0 = time.time()
    body: dict = {}
    while time.time() - t0 < timeout:
        body = _as_session(chat_tools.get_report_status)
        if body.get("status") != "running":
            return body
        time.sleep(1.5)
    return {"status": "timeout", **body}


def _prose(section: dict) -> list[str]:
    """What the job audited: paragraph markdown and bullet items, in order."""
    out: list[str] = []
    for block in section.get("blocks") or []:
        if block.get("type") == "paragraph":
            out.append(block.get("md") or "")
        elif block.get("type") == "bullets":
            out.extend(block.get("items") or [])
    return out


def _blob(file_id: str) -> bytes | None:
    r = qa_support.client().get(f"/api/projects/{PROJECT}/uploads/{file_id}/blob")
    return r.content if r.status_code == 200 else None


def _appendix(blob: bytes) -> dict[str, list[str]] | None:
    """``{heading: [numbers]}`` from the "Numbers to check" appendix, or None when absent."""
    from docx import Document

    word = Document(io.BytesIO(blob))
    paragraphs = list(word.paragraphs)
    start = next((i for i, p in enumerate(paragraphs) if p.text == APPENDIX_HEADING), None)
    if start is None:
        return None
    out: dict[str, list[str]] = {}
    for p in paragraphs[start + 1:]:
        # The writer's rows are `<bold heading>: ` + the numbers; the
        # introductory sentence is a Disclosure paragraph, not a row.
        if not p.runs or not p.runs[0].bold or p.style.name == "Disclosure":
            continue
        heading = p.runs[0].text.rstrip().rstrip(":")
        numbers = "".join(r.text for r in p.runs[1:])
        if heading and numbers:
            out[heading] = [n.strip() for n in numbers.split(", ") if n.strip()]
    return out


class _Seam:
    """The job router's profile resolution, pointed at a named profile when asked."""

    def __init__(self) -> None:
        import routers.report_jobs as jobs

        self.jobs = jobs
        self.orig_resolve = jobs._resolve_profile

    def use(self, profile_id: str | None) -> None:
        if not profile_id:
            return
        from services import llm_config

        self.jobs._resolve_profile = lambda: llm_config.resolve_profile(profile_id)

    def restore(self) -> None:
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
    top = fmea_top_modes(((rep.get("sections") or {}).get("fmea_top") or {}).get("payload"))
    _step("fmea_top is established with at least one ranked mode",
          comp.get("fmea_top") == "ok" and len(top) >= 1,
          f"status={comp.get('fmea_top')} rows={len(top)}")
    return rep


def section_2_generate(sections: list[str] | None = None) -> tuple[str, dict] | None:
    print("\n[2] generate_report (chat tool) on the live profile → get_report_status until done")
    from services import chat_tools

    started = _as_session(chat_tools.generate_report, title="Live probe report", language="en",
                          sections=sections)
    rid = started.get("report_id") or ""
    _step("generate_report starts", started.get("status") == "running" and len(rid) == 16,
          str(started)[:300])
    if len(rid) != 16:
        return None
    record = _poll_tool()
    _step("get_report_status leaves running", record.get("status") in ("done", "aborted", "failed"),
          f"status={record.get('status')}")
    _step("the job is done", record.get("status") == "done",
          f"status={record.get('status')} error={str(record.get('error'))[:300]} "
          f"progress={record.get('progress')}")
    _step("the record names the writer (profile + model)",
          bool(record.get("profile_id")) and bool(record.get("model")),
          f"profile={record.get('profile_id')} model={record.get('model')}")
    print(f"  → written by profile {record.get('profile_id')!r}, model {record.get('model')!r}; "
          f"repairs={record.get('repairs')} "
          f"prose_failures={[f.get('section_id') for f in record.get('prose_failures') or []]}")
    if record.get("status") != "done":
        return None
    return rid, record


def section_3_audit(rid: str, record: dict) -> dict:
    print("\n[3] get_report (chat tool): the number audit re-derived on the real prose")
    from services import chat_tools
    from services.reports.number_audit import extract_numbers

    c = qa_support.client()
    raw = c.get(f"/api/projects/{PROJECT}/reports/{rid}").json()
    by_id = {s["section_id"]: s for s in raw.get("sections") or []}
    listed = _as_session(chat_tools.list_reports)
    meta = next((m for m in listed if m.get("report_id") == rid), {})
    targets = (meta.get("generation") or {}).get("sections") or []
    bad = [sid for sid in targets
           if not (by_id.get(sid, {}).get("source") == "llm"
                   or str(by_id.get(sid, {}).get("note", "")).startswith("prose not established"))]
    _step("every target section is llm or carries a 'prose not established' note",
          not bad, f"targets={len(targets)} bad={bad}")

    tool_view = _as_session(chat_tools.get_report, rid)
    _step("get_report (chat tool) serves the same version",
          tool_view.get("report_id") == rid and tool_view.get("version") == raw.get("version"),
          f"outline={tool_view.get('outline')} chars={len(json.dumps(tool_view))}")
    one = _as_session(chat_tools.get_report, rid, None, targets[0]) if targets else {}
    _step("get_report(section_id=…) reads one section in full",
          bool(one.get("sections")) and one["sections"][0].get("section_id") == (targets[0] if targets else None))

    llm_sections = [s for s in raw.get("sections") or [] if s.get("source") == "llm"]
    _step("at least one section carries model prose", len(llm_sections) >= 1,
          f"llm sections={[s['section_id'] for s in llm_sections]}")
    flagged: dict[str, list[str]] = {}
    silently_accepted: list[str] = []
    silently_dropped: list[str] = []
    tokens_total = 0
    for s in llm_sections:
        audit = s.get("audit") or {}
        verified = {v.get("text") if isinstance(v, dict) else v for v in audit.get("verified") or []}
        unverified = list(audit.get("unverified") or [])
        if unverified:
            flagged[s["heading"]] = unverified
        seen: set[str] = set()
        for text in _prose(s):
            for token in extract_numbers(text):
                seen.add(token.text)
                tokens_total += 1
                if token.text not in verified and token.text not in unverified:
                    silently_accepted.append(f"{s['section_id']}:{token.text}")
        for text in list(verified) + unverified:
            if text not in seen:
                silently_dropped.append(f"{s['section_id']}:{text}")
    _step("every number in the prose is either verified (with an evidence path) or flagged",
          not silently_accepted, f"tokens={tokens_total} silently accepted={silently_accepted[:10]}")
    _step("the audit lists only numbers that are in the prose", not silently_dropped,
          f"not in the prose={silently_dropped[:10]}")
    n_flagged = sum(len(v) for v in flagged.values())
    print(f"  → {tokens_total} numeric tokens in {len(llm_sections)} model-written sections; "
          f"{n_flagged} flagged as not in the evidence: "
          + (json.dumps(flagged, ensure_ascii=False) if flagged else "none"))
    return flagged


def section_4_export(rid: str, flagged: dict[str, list[str]]) -> None:
    print("\n[4] export_report_docx (chat tool): the 'Numbers to check' appendix")
    from services import chat_tools

    out = _as_session(chat_tools.export_report_docx, rid)
    _step("export_report_docx answers an agent_export chip", out.get("kind") == "agent_export", str(out)[:200])
    blob = _blob(out.get("file_id", ""))
    if blob is None:
        _step("the blob route serves the export", False)
        return
    appendix = _appendix(blob)
    if not flagged:
        _step("no number was flagged → no appendix", appendix is None,
              f"appendix={appendix}")
        return
    _step("the appendix is present because numbers were flagged", appendix is not None)
    if appendix is None:
        return
    missing = [f"{h}: {n}" for h, nums in flagged.items() for n in nums if n not in (appendix.get(h) or [])]
    extra = [f"{h}: {n}" for h, nums in appendix.items() for n in nums if n not in (flagged.get(h) or [])]
    _step("every unverified number of every section is listed under its heading", not missing,
          f"missing={missing[:10]}")
    _step("the appendix lists nothing the audit did not flag", not extra, f"extra={extra[:10]}")


def section_5_regenerate(rid: str) -> None:
    print("\n[5] regenerate_report_section with an instruction → the next version")
    from services import chat_tools

    c = qa_support.client()
    before = c.get(f"/api/projects/{PROJECT}/reports/{rid}").json()
    target = next((s["section_id"] for s in before.get("sections") or [] if s.get("source") == "llm"), None)
    if target is None:
        _step("a model-written section to regenerate", False)
        return
    started = _as_session(chat_tools.regenerate_report_section, rid, target, INSTRUCTION)
    _step(f"regenerate_report_section({target!r}) starts", started.get("status") == "running", str(started)[:200])
    record = _poll_tool()
    _step("the regenerate job is done at version + 1",
          record.get("status") == "done" and record.get("version") == before.get("version", 0) + 1,
          f"status={record.get('status')} version={record.get('version')} error={str(record.get('error'))[:200]}")
    if record.get("status") != "done":
        return
    after = c.get(f"/api/projects/{PROJECT}/reports/{rid}").json()
    a = {s["section_id"]: s for s in before.get("sections") or []}
    b = {s["section_id"]: s for s in after.get("sections") or []}
    same = [sid for sid in a if sid != target and json.dumps(a[sid], sort_keys=True) == json.dumps(b.get(sid), sort_keys=True)]
    _step("every other section is byte-identical", len(same) == len(a) - 1, f"identical={len(same)}/{len(a) - 1}")
    _step("the regenerated section is still model prose", b.get(target, {}).get("source") == "llm",
          f"source={b.get(target, {}).get('source')} unverified={b.get(target, {}).get('audit', {}).get('unverified')}")


def main() -> int:
    require_key = "--require-key" in sys.argv[1:]
    print("=" * 60)
    print("QA: reports LIVE probe — one real generation on the active profile, audit inspected")
    print("=" * 60)
    if not os.environ.get(KEY_VAR):
        print(f"\n  UNPROBED: {KEY_VAR} is not set in this environment, so the active "
              "anthropic-wire profile has no key and generate_report would answer "
              "missing_api_key. Nothing was generated; this is not a pass.")
        print("  Set the variable (the backend reads it, so does this driver) and re-run.")
        return 2 if require_key else 0
    crashed = False
    seam: _Seam | None = None
    try:
        qa_support.reset_backend()
        qa_support.delete_project(PROJECT)
        from services import chat_tools

        chat_tools.set_acting_user(str(qa_support.user().id))
        seam = _Seam()
        seam.use(os.environ.get("PYPSA_GUI_TEST_LIVE_PROFILE"))
        rep = section_1_real_study()
        if rep is not None:
            created = section_2_generate()
            if created is not None:
                rid, record = created
                flagged = section_3_audit(rid, record)
                section_4_export(rid, flagged)
                section_5_regenerate(rid)
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
    print("=" * 60)
    return 1 if (FAIL or crashed) else 0


if __name__ == "__main__":
    sys.exit(main())
