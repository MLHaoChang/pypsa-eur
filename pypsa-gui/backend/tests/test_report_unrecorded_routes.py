# ruff: noqa: F811 -- fixtures imported from other test modules are parameters here
"""Study harness 07f-b (GK B Q2, E1–E3): the routes that read a report's
meta, when a job saved version n but its receipt or settings were refused.
The meta then holds none of them, marked `settings_unrecorded`, never
version n-1's. The marked state is made with the store's own `pending` save,
as the job's save writes it (`test_report_job_saved_then_refused.py` drives
the job itself)."""

from __future__ import annotations

from services.llm_fake import FakeProvider
from services.reports import store
from services.reports.assemble import EXECUTIVE_SUMMARY_ID
from tests.test_report_job_http import (
    _draft,
    _install_provider,
    _poll,
    _script,
    _store_eh_report,
)
from tests.test_report_templates_routes import (  # noqa: F401 -- fixtures
    _bind,
    _plan_for,
    _report,
    _upload,
    fake_untagged,
    fixtures,
)


def _generated(client, api_project, session_state, monkeypatch, name):
    name = api_project(name)
    _store_eh_report(session_state(client))
    _install_provider(monkeypatch, FakeProvider(_script()))
    rid = client.post(
        f"/api/projects/{name}/reports/generate",
        json={"sections": [EXECUTIVE_SUMMARY_ID, "fmea_top"]},
    ).json()["report_id"]
    assert _poll(client, name)["status"] == "done"
    return name, rid


def _unrecorded(project_dir, rid, *pending, edit=None):
    """Version n+1 saved as a job saves it, its later writes refused."""
    doc = store.load_report(project_dir, rid)
    if edit is not None:
        doc = edit(doc)
    return store.save_version(project_dir, doc, pending=("generation", *pending))


# ── (a) the report list ─────────────────────────────────────────────────────


def test_the_list_route_marks_the_receipt_unrecorded_and_shows_none(
    client,
    api_project,
    session_state,
    project_storage_dir,
    monkeypatch,
):
    name, rid = _generated(
        client, api_project, session_state, monkeypatch, "unrec-list"
    )
    [before] = client.get(f"/api/projects/{name}/reports").json()
    assert before["generation"] is not None and before["settings_unrecorded"] is None

    assert _unrecorded(project_storage_dir(name), rid) == 2
    [meta] = client.get(f"/api/projects/{name}/reports").json()
    assert meta["report_id"] == rid
    assert meta["latest_version"] == 2
    assert meta["generation"] is None  # never version 1's
    assert meta["settings_unrecorded"] == {"version": 2, "fields": ["generation"]}


# ── (b) export ──────────────────────────────────────────────────────────────


def _untagged_with_plan(
    client, api_project, session_state, project_storage_dir, fixtures, name
):
    name = api_project(name)
    rid = _report(client, name, session_state)
    tpl = _upload(client, name, "corporate.docx", fixtures["corporate_untagged.docx"])
    bound = _bind(client, name, rid, tpl["file_id"])
    plan = _plan_for(bound["outline"])
    r = client.put(f"/api/projects/{name}/reports/{rid}/template/plan", json=plan)
    assert r.status_code == 200, r.text[:300]
    stored = store.load_meta(project_storage_dir(name), rid).mapping_plan
    assert stored is not None
    return name, rid, stored


def test_an_export_whose_mapping_was_not_recorded_is_refused(
    client,
    api_project,
    session_state,
    project_storage_dir,
    fixtures,
    fake_untagged,
):
    """E2 (i): never the code-only default mapping in its place, unsaid."""
    name, rid, _plan = _untagged_with_plan(
        client,
        api_project,
        session_state,
        project_storage_dir,
        fixtures,
        "unrec-export",
    )
    n = _unrecorded(project_storage_dir(name), rid, "mapping_plan")
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={})
    assert r.status_code == 409, r.text[:300]
    detail = r.json()["detail"]
    assert detail["error_kind"] == "report_settings_unrecorded"
    assert f"version {n}" in detail["message"]
    assert fake_untagged.rendered == []  # nothing rendered, nothing exported


def test_an_export_with_only_the_receipt_unrecorded_uses_the_reports_own_mapping(
    client,
    api_project,
    session_state,
    project_storage_dir,
    fixtures,
    fake_untagged,
):
    """E4 at the reader: the mapping is the report's, not the job's, so it
    carries and the export uses it."""
    name, rid, plan = _untagged_with_plan(
        client,
        api_project,
        session_state,
        project_storage_dir,
        fixtures,
        "unrec-export-ok",
    )
    _unrecorded(project_storage_dir(name), rid)
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={})
    assert r.status_code == 200, r.text[:300]
    assert fake_untagged.rendered[-1] == plan


def test_mapping_the_template_again_lifts_the_refusal(
    client,
    api_project,
    session_state,
    project_storage_dir,
    fixtures,
    fake_untagged,
):
    name, rid, plan = _untagged_with_plan(
        client, api_project, session_state, project_storage_dir, fixtures, "unrec-remap"
    )
    _unrecorded(project_storage_dir(name), rid, "mapping_plan")
    r = client.put(f"/api/projects/{name}/reports/{rid}/template/plan", json=plan)
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={})
    assert r.status_code == 200, r.text[:300]
    assert fake_untagged.rendered[-1] == plan


def test_a_regenerate_never_lifts_an_unrecorded_mapping(
    client,
    api_project,
    session_state,
    project_storage_dir,
    fixtures,
    fake_untagged,
    monkeypatch,
):
    """The mapping is still null in the regenerated version: export still says so."""
    name, rid, _plan = _untagged_with_plan(
        client,
        api_project,
        session_state,
        project_storage_dir,
        fixtures,
        "unrec-regen-map",
    )
    n = _unrecorded(project_storage_dir(name), rid, "mapping_plan")
    _install_provider(monkeypatch, FakeProvider([_draft("fmea_top", "Rewritten.")]))
    r = client.post(
        f"/api/projects/{name}/reports/{rid}/sections/fmea_top/regenerate", json={}
    )
    assert r.status_code == 200, r.text[:300]
    assert _poll(client, name)["version"] == n + 1
    r = client.post(f"/api/projects/{name}/reports/{rid}/export", json={})
    assert r.status_code == 409, r.text[:300]
    assert r.json()["detail"]["error_kind"] == "report_settings_unrecorded"
    assert fake_untagged.rendered == []


# ── (c) regenerate base ─────────────────────────────────────────────────────


def test_a_regenerate_bases_on_the_unrecorded_version_and_records_its_own_receipt(
    client,
    api_project,
    session_state,
    project_storage_dir,
    monkeypatch,
):
    name, rid = _generated(
        client, api_project, session_state, monkeypatch, "unrec-regen"
    )
    pdir = project_storage_dir(name)

    def edited_summary(doc):
        def edit(s):
            first = s.blocks[0].model_copy(update={"md": "Summary of version 2."})
            return s.model_copy(update={"blocks": [first, *s.blocks[1:]]})

        sections = [
            edit(s) if s.section_id == EXECUTIVE_SUMMARY_ID else s for s in doc.sections
        ]
        return doc.model_copy(update={"sections": sections})

    assert _unrecorded(pdir, rid, edit=edited_summary) == 2
    _install_provider(monkeypatch, FakeProvider([_draft("fmea_top", "Rewritten.")]))
    r = client.post(
        f"/api/projects/{name}/reports/{rid}/sections/fmea_top/regenerate",
        json={"instruction": "shorter"},
    )
    assert r.status_code == 200, r.text[:300]  # the mark never blocks it
    body = _poll(client, name)
    assert body["status"] == "done" and body["version"] == 3, body
    v3 = client.get(f"/api/projects/{name}/reports/{rid}").json()
    by_id = {s["section_id"]: s for s in v3["sections"]}
    assert (
        by_id[EXECUTIVE_SUMMARY_ID]["blocks"][0]["md"] == "Summary of version 2."
    )  # based on n
    meta = store.load_meta(pdir, rid)
    assert meta.settings_unrecorded is None
    assert meta.generation["sections"] == ["fmea_top"]
