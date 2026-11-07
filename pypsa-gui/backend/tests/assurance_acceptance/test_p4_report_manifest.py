# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""AA-05 S4b: the assistant's report exports carry a sealed manifest.

GK B (32) X1-X6. The assistant's `export_report_docx` / `export_report_pdf`
publish through `_publish_sealed_export`: the manifest names the stored
version by its file's hash, each figure and the template by their bytes, the
disclosures and the withheld placeholders. The download route and
`build_delivery` re-check it. Only a seal or byte mismatch quarantines (J1);
a source that is gone is noted and served (X3); a source that changed is
refused, `report_version_changed`, with no marker.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import HTTPException

from services import assistant_tools as A, chat_tools as T, upload_service
from services.assurance_artifacts import canonical_sha256, quarantined
from services.reports import formatting as F, report_job, store
from tests.assurance_acceptance.test_p4_reports import (
    PLACEHOLDER,
    _assistant_report,
    _human_report,
)
from tests.test_chat_report_tools import PROJECT
from tests.assurance_acceptance import test_p4_reports as _p4

project = _p4.project
helpers_state = _p4.helpers_state


def _sidecar(project_dir, file_id: str) -> dict:
    return json.loads((project_dir / "assistant_artifacts" / f"{file_id}.json").read_text())


def _blob(client, file_id: str):
    return client.get(f"/api/projects/{PROJECT}/uploads/{file_id}/blob")


def _quarantine_dir(project_dir):
    return project_dir / "assistant_artifacts" / "quarantine"


def _markers(project_dir) -> set[str]:
    folder = _quarantine_dir(project_dir)
    return {p.name for p in folder.iterdir()} if folder.is_dir() else set()


def _upload_path(project_dir, file_id: str):
    return upload_service.get_upload_path(PROJECT, file_id, project_dir=project_dir)


def _pdf(monkeypatch):
    from services.reports import pdf

    monkeypatch.setattr(pdf, "pdf_available", lambda: True)
    monkeypatch.setattr(pdf, "convert_docx_to_pdf", lambda data, **_: b"%PDF-1.4 " + hashlib.sha256(data).digest())


# ── B1/X1: the assistant's export is sealed; the human one is not ──────────


def test_an_assistant_docx_export_is_sealed_with_its_version_and_disclosures(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    meta = T.export_report_docx(rid)
    record = _sidecar(project, meta["file_id"])
    manifest = record["manifest"]
    doc = store.load_report(project, rid)
    raw = (store.report_dir(project, rid) / f"v{doc.version}.json").read_bytes()
    assert manifest["source_kind"] == "report_export"
    assert manifest["dataset"]["report_id"] == rid and manifest["dataset"]["version"] == doc.version
    assert manifest["dataset"]["version_sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["dataset"]["version_hash_basis"] == "stored_file"
    assert manifest["render_spec"]["format"] == "docx"
    assert manifest["render_spec"]["disclosures"] == report_job.disclosure_texts(doc)
    assert manifest["render_spec"]["disclosures"]  # the fixture carries at least one
    assert manifest["render_spec"]["withheld"] == [{"section_id": "fmea_top", "placeholders": 1}]
    assert manifest["dataset_sha256"] == canonical_sha256(manifest["dataset"])
    assert manifest["render_spec_sha256"] == canonical_sha256(manifest["render_spec"])
    data = _upload_path(project, meta["file_id"]).read_bytes()
    assert manifest["artifact_sha256"] == hashlib.sha256(data).hexdigest()
    stored = upload_service.get_upload_meta(PROJECT, meta["file_id"], project_dir=project)
    assert stored.assurance_sha256 == canonical_sha256(record)
    response = _blob(client, meta["file_id"])
    assert response.status_code == 200 and response.content == data
    assert "X-Report-Source" not in response.headers


def test_an_assistant_pdf_export_is_sealed_after_its_conversion(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    _pdf(monkeypatch)
    meta = T.export_report_pdf(rid)
    manifest = _sidecar(project, meta["file_id"])["manifest"]
    data = _upload_path(project, meta["file_id"]).read_bytes()
    assert data.startswith(b"%PDF-1.4 ")
    assert manifest["render_spec"]["format"] == "pdf"
    assert manifest["artifact_sha256"] == hashlib.sha256(data).hexdigest()
    assert _blob(client, meta["file_id"]).content == data


def test_the_human_export_has_no_sidecar_and_the_same_bytes(project, client, monkeypatch):
    rid = _human_report(client, monkeypatch)
    response = client.post(f"/api/projects/{PROJECT}/reports/{rid}/export", json={})
    assert response.status_code == 200, response.text
    file_id = response.json()["file_id"]
    meta = upload_service.get_upload_meta(PROJECT, file_id, project_dir=project)
    assert meta.assurance_sha256 is None
    assert not (project / "assistant_artifacts" / f"{file_id}.json").exists()
    again = client.post(f"/api/projects/{PROJECT}/reports/{rid}/export", json={"strict": True})
    assert again.status_code == 200
    assert _upload_path(project, again.json()["file_id"]).read_bytes() == _upload_path(project, file_id).read_bytes()


def test_the_disclosure_check_runs_before_the_seal(project, monkeypatch):
    """X1 order: a lost disclosure refuses before any sidecar or upload."""
    from services.reports import docx_writer

    rid = _assistant_report(monkeypatch, fmea="Generator g dominates again.")
    required = report_job.disclosure_texts(store.load_report(project, rid))
    real = docx_writer._disclosure
    monkeypatch.setattr(docx_writer, "_disclosure",
                        lambda doc, text: None if text in required else real(doc, text))
    sealed = []
    monkeypatch.setattr(T, "_publish_sealed_export", lambda *a, **k: sealed.append(a) or ({}, {}))
    with pytest.raises(HTTPException) as exc:
        T.export_report_docx(rid)
    assert exc.value.detail["error_kind"] == "report_disclosure_missing"
    assert sealed == []


def test_an_identical_re_export_keeps_the_first_seal(project, monkeypatch):
    rid = _assistant_report(monkeypatch)
    first = T.export_report_docx(rid)
    seal = upload_service.get_upload_meta(PROJECT, first["file_id"], project_dir=project).assurance_sha256
    second = T.export_report_docx(rid)
    if second["file_id"] == first["file_id"]:  # a byte-identical render
        assert upload_service.get_upload_meta(
            PROJECT, first["file_id"], project_dir=project).assurance_sha256 == seal
    else:  # docx timestamps differ: each export has its own seal
        assert _sidecar(project, second["file_id"])["manifest"]["dataset"] == \
            _sidecar(project, first["file_id"])["manifest"]["dataset"]


def test_a_quarantined_export_is_refused_and_writes_nothing(project, monkeypatch):
    rid = _assistant_report(monkeypatch)
    _pdf(monkeypatch)
    first = T.export_report_pdf(rid)
    folder = _quarantine_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{first['file_id']}.json").write_text(json.dumps({"code": "bytes"}))
    sidecar = project / "assistant_artifacts" / f"{first['file_id']}.json"
    sidecar.unlink()
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file())
    with pytest.raises(HTTPException) as exc:
        T.export_report_pdf(rid)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "artifact_quarantined"
    after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file())
    assert after == before


# ── B2/X4: only seal or byte mismatches quarantine ─────────────────────────


def test_forged_then_restored_bytes_stay_quarantined(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    path = _upload_path(project, file_id)
    good = path.read_bytes()
    path.write_bytes(good + b"x")
    assert _blob(client, file_id).status_code == 409
    assert quarantined(project, file_id)
    path.write_bytes(good)
    assert _blob(client, file_id).status_code == 409


def test_an_edited_sidecar_quarantines(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    path = project / "assistant_artifacts" / f"{file_id}.json"
    record = json.loads(path.read_text())
    record["manifest"]["render_spec"]["withheld"] = []
    path.write_text(json.dumps(record))
    assert _blob(client, file_id).status_code == 409
    assert quarantined(project, file_id)


def _version_file(project_dir, rid: str):
    return store.report_dir(project_dir, rid) / f"v{store.load_report(project_dir, rid).version}.json"


def test_a_stored_version_changed_on_disk_refuses_without_a_marker(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    version = _version_file(project, rid)
    version.write_bytes(version.read_bytes() + b"\n")
    response = _blob(client, file_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "report_version_changed"
    assert _markers(project) == set()


def test_a_load_normalised_version_does_not_read_as_changed(project, client, monkeypatch):
    """X2: the hash is of the file as written, so a loader that normalises
    the document differently does not change it."""
    from models.report import ReportDocument

    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    real = ReportDocument.model_validate_json.__func__

    def normalising(cls, text, *a, **k):
        doc = real(cls, text, *a, **k)
        return doc.model_copy(update={"title": doc.title + " (migrated)"})

    monkeypatch.setattr(ReportDocument, "model_validate_json", classmethod(normalising))
    assert _blob(client, file_id).status_code == 200


def test_a_deleted_version_is_served_with_a_note_and_no_marker(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    _version_file(project, rid).unlink()
    response = _blob(client, file_id)
    assert response.status_code == 200
    assert response.headers["X-Report-Source"] == "version_missing"
    assert _markers(project) == set()
    item = A.build_delivery([file_id])
    archive = upload_service.get_upload_bytes(PROJECT, item["file_id"], project_dir=project)
    import io
    import zipfile

    manifest = json.loads(zipfile.ZipFile(io.BytesIO(archive)).read("manifest.json"))
    (entry,) = manifest["files"]
    assert entry["source_status"] == "version_missing"
    assert "no longer stored" in entry["source_note"]


def test_a_deleted_report_is_served_with_a_note(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    store.delete_report(project, rid)
    response = _blob(client, file_id)
    assert response.status_code == 200
    assert response.headers["X-Report-Source"] == "version_missing"
    assert _markers(project) == set()


def test_a_swapped_template_refuses_and_a_deleted_one_is_noted(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    path = project / "assistant_artifacts" / f"{file_id}.json"
    # A template binding recorded at seal time (the fixture report has none):
    # reseal the record so only the template check differs.
    record = json.loads(path.read_text())
    blob = b"template bytes"
    tmeta = upload_service.add_upload(PROJECT, blob, "t.docx", "application/octet-stream",
                                      kind="report_template", project_dir=project)
    record["manifest"]["dataset"]["template"] = {"file_id": tmeta.file_id, "sha256": "0" * 64}
    _reseal(project, file_id, record)
    response = _blob(client, file_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "report_version_changed"
    assert _markers(project) == set()
    record["manifest"]["dataset"]["template"] = {"file_id": "f" * 16, "sha256": "0" * 64}
    _reseal(project, file_id, record)
    response = _blob(client, file_id)
    assert response.status_code == 200
    assert response.headers["X-Report-Source"] == "template_missing"


def test_a_changed_figure_refuses_and_a_deleted_one_is_noted(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    record = _sidecar(project, file_id)
    store.write_figure(project, rid, "fig1", b"\x89PNG one")
    record["manifest"]["dataset"]["figures"] = {"fig1": "0" * 64}
    _reseal(project, file_id, record)
    response = _blob(client, file_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "report_version_changed"
    record["manifest"]["dataset"]["figures"] = {"fig2": "0" * 64}
    _reseal(project, file_id, record)
    response = _blob(client, file_id)
    assert response.status_code == 200
    assert response.headers["X-Report-Source"] == "figure_missing"
    assert _markers(project) == set()


def _reseal(project_dir, file_id: str, record: dict) -> None:
    """Rewrite a sidecar and the upload's seal together, as a consistent
    (but altered) export would carry them."""
    manifest = record["manifest"]
    manifest["dataset_sha256"] = canonical_sha256(manifest["dataset"])
    manifest["render_spec_sha256"] = canonical_sha256(manifest["render_spec"])
    (project_dir / "assistant_artifacts" / f"{file_id}.json").write_text(json.dumps(record))
    meta_path = _upload_path(project_dir, file_id).parent / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["assurance_sha256"] = canonical_sha256(record)
    meta_path.write_text(json.dumps(meta))


def test_disclosure_drift_on_matching_bytes_refuses_without_a_marker(project, client, monkeypatch):
    """X4/R6: a read-back that loses a disclosure on bytes whose hash matches
    is parser drift, not tampering."""
    from services.reports import docx_reader

    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    monkeypatch.setattr(docx_reader, "body_text", lambda data: "")
    response = _blob(client, file_id)
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "artifact_evidence_unavailable"
    assert _markers(project) == set()

    def broken(data):
        raise ValueError("unreadable")

    monkeypatch.setattr(docx_reader, "body_text", broken)
    assert _blob(client, file_id).status_code == 409
    assert _markers(project) == set()


def test_an_io_error_on_the_version_refuses_without_a_marker(project, client, monkeypatch):
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]

    def unreadable(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(store, "version_sha256", unreadable)
    assert _blob(client, file_id).status_code == 409
    assert _markers(project) == set()


# ── X5: delivery items carry the hashes; reports do not join _one_run ──────


def test_a_delivery_item_carries_the_report_hashes(project, monkeypatch):
    import io
    import zipfile

    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    delivered = A.build_delivery([file_id])
    archive = upload_service.get_upload_bytes(PROJECT, delivered["file_id"], project_dir=project)
    (entry,) = json.loads(zipfile.ZipFile(io.BytesIO(archive)).read("manifest.json"))["files"]
    manifest = _sidecar(project, file_id)["manifest"]
    assert entry["assurance"] == manifest
    assert entry["dataset_sha256"] == manifest["dataset_sha256"]
    assert entry["render_spec_sha256"] == manifest["render_spec_sha256"]
    assert "source_status" not in entry and "scope_status" not in entry


# ── W1: only the exact placeholder text marks a withheld block ─────────────


def test_only_the_exact_placeholder_text_is_a_placeholder():
    for count in (0, 1, 2, 10, 123):
        assert F.is_withheld_block({"type": "callout", "kind": "gap", "text": F.withheld_placeholder(count)})
    assert F.withheld_placeholder(1) == PLACEHOLDER
    for text in ("Withheld: data not public.", PLACEHOLDER + " More.", "x" + PLACEHOLDER,
                 PLACEHOLDER.replace("1 number", "1 numbers"), PLACEHOLDER.replace("1 number", "2 number"),
                 PLACEHOLDER.replace("1 number", "01 numbers")):
        assert not F.is_withheld_block({"type": "callout", "kind": "gap", "text": text}), text


def test_a_regenerate_keeps_a_gap_that_only_starts_like_a_placeholder():
    from models.report import Section

    gap = {"type": "callout", "kind": "gap", "text": "Withheld: data not public."}
    section = Section.model_validate({"section_id": "s", "heading": "S", "source": "code", "status": "ok", "blocks": [
        gap, {"type": "callout", "kind": "gap", "text": PLACEHOLDER}]})
    kept = report_job._code_blocks(section)
    assert [b.text for b in kept] == [gap["text"]]


def test_a_template_lookup_that_fails_otherwise_refuses_and_is_not_read_as_missing(project, client, monkeypatch):
    """Only a 404 means the template is gone; any other failure is no
    verdict and refuses, rather than serving the file as `template_missing`."""
    rid = _assistant_report(monkeypatch)
    file_id = T.export_report_docx(rid)["file_id"]
    record = _sidecar(project, file_id)
    record["manifest"]["dataset"]["template"] = {"file_id": "e" * 16, "sha256": "0" * 64}
    _reseal(project, file_id, record)

    def failing(*a, **k):
        raise HTTPException(503, {"error_kind": "upload_store_unavailable"})

    monkeypatch.setattr(upload_service, "get_upload_bytes", failing)
    response = _blob(client, file_id)
    assert response.status_code == 503
    assert "X-Report-Source" not in response.headers
    assert _markers(project) == set()
