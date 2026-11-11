"""LC-43, LC-D20.delivery_gates: each delivery, import and chart gate refuses and writes nothing.

The seam is the copilot's delivery and import tools (``build_delivery``,
``get_file_delivery``, the ``export_*`` tools, ``import_uploaded_network``,
``create_chart``, ``export_run_evidence``) and the authenticated blob download.

Hand oracle, the baseline project (B1 110 kV, G1 20 MW at 10 EUR/MWh, L1 5 MW, two
snapshots). Each refusal names its kind and status, and afterwards the project's
uploads and G1's 20 MW are exactly as before:

- an id that lives in another project's uploads is 404 upload_not_found here, and
  malformed ids (``..``, 15 hex digits, upper-case hex) are 404 upload_not_found too;
- another organisation's client fetching a delivery's download URL gets 404;
- an upload whose blob was never finished (``blob_ready`` false) is 404 upload_not_found
  to the delivery tool and to the download;
- an export over the per-file cap is 413 file_too_large; a delivery whose contents
  exceed the cap less 100 kB is 413 delivery_too_large (the cap is lowered so a
  two-snapshot network crosses it);
- an import with an unnamed format is 422 invalid_import_format; an upload whose bytes
  no longer match their hash is 409 import_file_changed;
- a chart of an unknown component, no names, a repeated name or nine names is 422
  invalid_chart;
- ``export_run_evidence`` with no run named and none discussed is 409
  discussed_run_required.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import json

import pytest
from fastapi import HTTPException

from services import chat_tools, upload_service
from services.pypsa_service import PyPSAService
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


def _state(project_dir) -> tuple[list[str], float]:
    uploads = sorted(
        m.file_id
        for m in upload_service.list_uploads("Tool Baseline", project_dir=project_dir)
    )
    g1 = float(PyPSAService.get_active_context().network.generators.at["G1", "p_nom"])
    return uploads, g1


def _refused(call, status: int, kind: str) -> None:
    with pytest.raises(HTTPException) as failure:
        call()
    assert failure.value.status_code == status, failure.value.detail
    assert failure.value.detail["error_kind"] == kind, failure.value.detail


def test_an_id_from_another_project_or_a_malformed_id_reaches_no_file(
    baseline, tmp_path
):
    _ctx, _project_id, project_dir = baseline
    foreign = upload_service.add_upload(
        "Elsewhere",
        b"bus\nB9\n",
        "elsewhere.csv",
        "text/csv",
        kind="agent_export",
        project_dir=tmp_path / "elsewhere",
    )
    before = _state(project_dir)
    for file_id in (foreign.file_id, "..", "0" * 15, foreign.file_id.upper()):
        _refused(
            lambda f=file_id: chat_tools.build_delivery([f]), 404, "upload_not_found"
        )
        _refused(
            lambda f=file_id: chat_tools.get_file_delivery(f), 404, "upload_not_found"
        )
    assert _state(project_dir) == before


def test_another_organisation_cannot_download_a_delivery(
    baseline, client, other_org_client
):
    delivery = chat_tools.export_network_nc()
    assert client.get(delivery["download_url"]).status_code == 200
    response = other_org_client.get(delivery["download_url"])
    assert response.status_code == 404, response.text
    assert delivery["sha256"].encode() not in response.content


def test_an_unfinished_upload_is_never_delivered(baseline, client):
    _ctx, _project_id, project_dir = baseline
    delivery = chat_tools.export_network_nc()
    meta_path = (
        upload_service.get_upload_path(
            "Tool Baseline", delivery["file_id"], project_dir=project_dir
        ).parent
        / "meta.json"
    )
    meta = json.loads(meta_path.read_text())
    meta_path.write_text(json.dumps({**meta, "blob_ready": False}))
    _refused(
        lambda: chat_tools.get_file_delivery(delivery["file_id"]),
        404,
        "upload_not_found",
    )
    _refused(
        lambda: chat_tools.build_delivery([delivery["file_id"]]),
        404,
        "upload_not_found",
    )
    response = client.get(delivery["download_url"])
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error_kind"] == "upload_not_found"


def test_an_export_over_the_cap_is_refused_and_saves_nothing(baseline, monkeypatch):
    _ctx, _project_id, project_dir = baseline
    before = _state(project_dir)
    monkeypatch.setattr(upload_service, "MAX_FILE_BYTES", 64)
    _refused(chat_tools.export_network_nc, 413, "file_too_large")
    assert _state(project_dir) == before


def test_a_delivery_over_the_cap_is_refused_and_saves_nothing(baseline, monkeypatch):
    _ctx, _project_id, project_dir = baseline
    source = chat_tools.export_csv_bundle()
    before = _state(project_dir)
    monkeypatch.setattr(upload_service, "MAX_FILE_BYTES", 100_000 + source["size"] - 1)
    _refused(
        lambda: chat_tools.build_delivery([source["file_id"]]),
        413,
        "delivery_too_large",
    )
    assert _state(project_dir) == before
    monkeypatch.setattr(upload_service, "MAX_FILE_BYTES", 100_000 + source["size"])
    assert chat_tools.build_delivery([source["file_id"]])["files_total"] == 1


def test_an_import_needs_a_named_format_and_unchanged_bytes(baseline):
    _ctx, _project_id, project_dir = baseline
    source = chat_tools.export_network_nc()
    before = _state(project_dir)
    for fmt in ("pdf", "", "NETCDF"):
        _refused(
            lambda f=fmt: chat_tools.import_uploaded_network(source["file_id"], f),
            422,
            "invalid_import_format",
        )
    path = upload_service.get_upload_path(
        "Tool Baseline", source["file_id"], project_dir=project_dir
    )
    path.write_bytes(path.read_bytes() + b"\0")
    _refused(
        lambda: chat_tools.import_uploaded_network(source["file_id"], "netcdf"),
        409,
        "import_file_changed",
    )
    assert _state(project_dir) == before


@pytest.mark.parametrize(
    "component, names",
    [
        ("Turbine", ["G1"]),
        ("Generator", []),
        ("Generator", ["G1", "G1"]),
        ("Generator", [f"G{i}" for i in range(9)]),
        ("Generator", [""]),
    ],
)
def test_a_chart_outside_its_shape_is_refused_and_draws_nothing(
    baseline, component, names
):
    _ctx, _project_id, project_dir = baseline
    before = _state(project_dir)
    _refused(
        lambda: chat_tools.create_chart(component, "p_nom", names), 422, "invalid_chart"
    )
    assert _state(project_dir) == before
    assert (
        chat_tools.create_chart("Generator", "p_nom", ["G1"])["file_id"]
        not in before[0]
    )


def test_run_evidence_is_never_exported_for_a_run_nobody_named(baseline):
    _ctx, _project_id, project_dir = baseline
    before = _state(project_dir)
    _refused(chat_tools.export_run_evidence, 409, "discussed_run_required")
    assert _state(project_dir) == before
