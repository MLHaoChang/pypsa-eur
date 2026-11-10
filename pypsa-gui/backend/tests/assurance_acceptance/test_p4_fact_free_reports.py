"""AA-05 S5b B4/B5 for reports (AA05-A46-SCOPE-1; GK B (35) B4, B5).

A report export never presents a value as checked: the human export and the
assistant's sealed `report_export` send an exact set of header names, no
header value carries a number at all, and their delivery items carry no
fact, metric or evidence key. The sealed export's manifest names its version
and disclosures (S4b), not a checked value.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import io
import json
import zipfile

from services import assistant_tools as A
from services import chat_tools as T
from tests.assurance_acceptance.test_p4_fact_free_surfaces import (
    BASE,
    CHECKED_KEYS,
    keys_in,
    numbers_in,
)
from tests.assurance_acceptance.test_p4_report_manifest import (  # noqa: F401
    PROJECT,
    _assistant_report,
    _blob,
    _human_report,
    _pinned_zip_clock,
    _version_file,
    helpers_state,
    project,
)

HEADERS = {
    "human report": BASE,
    "assistant report_export": BASE,
    # Only when a stored source is gone (S4b X3): its status, a word.
    "assistant report_export, source gone": BASE | {"x-report-source"},
}
# The delivery item of a sealed export: the same keys as a table's (S4b X5).
SEALED_ITEM = {
    "archive_path",
    "assurance",
    "dataset_sha256",
    "file_id",
    "filename",
    "mime",
    "provenance",
    "render_spec_sha256",
    "sha256",
    "size",
}
SEALED_ASSURANCE = {
    "artifact_sha256",
    "dataset",
    "dataset_sha256",
    "project_id",
    "render_spec",
    "render_spec_sha256",
    "schema_version",
    "source_kind",
    "validity",
}
SEALED_DATASET = {
    "figures",
    "mapping_plan_sha256",
    "report_id",
    "template",
    "version",
    "version_hash_basis",
    "version_sha256",
}
SEALED_RENDER_SPEC = {"format", "disclosures", "withheld"}
# A human export is a plain file: no assurance at all.
PLAIN_ITEM = {"archive_path", "file_id", "filename", "mime", "sha256", "size"}


def _human_export(client, monkeypatch):
    rid = _human_report(client, monkeypatch)
    response = client.post(
        f"/api/projects/{PROJECT}/reports/{rid}/export", json={"version": None}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _items(client, *file_ids):
    bundle = A.build_delivery(list(file_ids))
    response = client.get(bundle["download_url"])
    assert response.status_code == 200
    assert not numbers_in(response.headers)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {
            i["file_id"]: i for i in json.loads(archive.read("manifest.json"))["files"]
        }


def test_a_human_report_download_sends_no_checked_header_or_number(
    project, client, monkeypatch
):
    meta = _human_export(client, monkeypatch)
    response = _blob(client, meta["file_id"])
    assert response.status_code == 200
    assert set(response.headers) - {"vary"} == HEADERS["human report"]
    assert not numbers_in(response.headers)
    assert not set(meta) & CHECKED_KEYS


def test_an_assistant_report_export_sends_no_checked_header_or_number(
    project, client, monkeypatch
):
    meta = T.export_report_docx(_assistant_report(monkeypatch))
    response = _blob(client, meta["file_id"])
    assert response.status_code == 200
    assert set(response.headers) - {"vary"} == HEADERS["assistant report_export"]
    assert not numbers_in(response.headers)
    assert not set(meta) & CHECKED_KEYS


def test_a_report_export_whose_version_is_gone_names_only_its_source_status(
    project, client, monkeypatch
):
    rid = _assistant_report(monkeypatch)
    meta = T.export_report_docx(rid)
    _version_file(project, rid).unlink()
    response = _blob(client, meta["file_id"])
    assert response.status_code == 200
    assert (
        set(response.headers) - {"vary"}
        == HEADERS["assistant report_export, source gone"]
    )
    assert response.headers["x-report-source"] == "version_missing"
    assert not numbers_in(response.headers)


def test_delivered_reports_carry_no_checked_fact(project, client, monkeypatch):
    sealed = T.export_report_docx(_assistant_report(monkeypatch))
    human = _human_export(client, monkeypatch)
    items = _items(client, sealed["file_id"], human["file_id"])
    item = items[sealed["file_id"]]
    assert set(item) == SEALED_ITEM
    assert set(item["assurance"]) == SEALED_ASSURANCE
    assert item["assurance"]["source_kind"] == "report_export"
    assert set(item["assurance"]["dataset"]) == SEALED_DATASET
    assert set(item["assurance"]["render_spec"]) == SEALED_RENDER_SPEC
    assert not keys_in(item) & CHECKED_KEYS
    assert set(items[human["file_id"]]) == PLAIN_ITEM
