"""LC-43, LC-D20.delivery_gates: each delivery, import and chart gate refuses and writes nothing.

The seam is the copilot's delivery and import tools (``build_delivery``,
``get_file_delivery``, the ``export_*`` tools, ``import_uploaded_network``,
``create_chart``, ``export_run_evidence``) and the authenticated blob download.

Hand oracle, the baseline project (B1 110 kV, G1 20 MW at 10 EUR/MWh, L1 5 MW, two
snapshots). Each refusal names its kind and status, and afterwards ``lc43_gates.state``
(the open network's fingerprint, each report's version count, every upload directory
and the blob count) is exactly as before:

- an id that lives in another project's uploads is 404 upload_not_found here, and
  malformed ids (``..``, 15 hex digits, upper-case hex) are 404 upload_not_found too;
- another organisation's client fetching a delivery's download URL gets 404 with the
  plain detail ``Project not found``;
- an upload whose blob was never finished (``blob_ready`` false) is 404 upload_not_found
  to the delivery tool and to the download;
- an export over the per-file cap is 413 file_too_large, for each of the network, CSV,
  Excel and MATPOWER exports; a delivery whose contents
  exceed the cap less 100 kB is 413 delivery_too_large (the cap is lowered so a
  two-snapshot network crosses it);
- an import with an unnamed format is 422 invalid_import_format; an upload whose bytes
  no longer match their hash is 409 import_file_changed;
- a malformed netCDF file, CSV bundle, workbook or MATPOWER case, and a well-formed zip
  or workbook that holds no network, is 422 import_unreadable, with the open project and
  its unsaved G1 edit (33 MW) kept (LC-43c: each file is parsed into a candidate network
  before it replaces the open one); a route's own refusal (a MATPOWER baseMVA of -1)
  keeps its 422 and detail and the network too;
- a chart of an unknown component, no names, a repeated name or nine names is 422
  invalid_chart;
- ``export_run_evidence`` with no run named and none discussed is 409
  discussed_run_required.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import base64
import io
import json
import zipfile

import openpyxl

import pytest

from services import chat_tools, upload_service
from services.pypsa_service import PyPSAService
from tests.lc43_gates import refused, state
from tests.test_assistant_capabilities import dispatch, session  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


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
    before = state(project_dir)
    for file_id in (foreign.file_id, "..", "0" * 15, foreign.file_id.upper()):
        refused(
            lambda f=file_id: chat_tools.build_delivery([f]), 404, "upload_not_found"
        )
        refused(
            lambda f=file_id: chat_tools.get_file_delivery(f), 404, "upload_not_found"
        )
    assert state(project_dir) == before


def test_another_organisation_cannot_download_a_delivery(
    baseline, client, other_org_client
):
    _ctx, _project_id, project_dir = baseline
    delivery = chat_tools.export_network_nc()
    assert client.get(delivery["download_url"]).status_code == 200
    before = state(project_dir)
    response = other_org_client.get(delivery["download_url"])
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Project not found"}, response.text
    assert state(project_dir) == before
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
    before = state(project_dir)
    refused(
        lambda: chat_tools.get_file_delivery(delivery["file_id"]),
        404,
        "upload_not_found",
    )
    refused(
        lambda: chat_tools.build_delivery([delivery["file_id"]]),
        404,
        "upload_not_found",
    )
    response = client.get(delivery["download_url"])
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error_kind"] == "upload_not_found"
    assert state(project_dir) == before


@pytest.mark.parametrize(
    "export",
    ["export_network_nc", "export_csv_bundle", "export_excel", "export_matpower"],
)
def test_an_export_over_the_cap_is_refused_and_saves_nothing(
    baseline, monkeypatch, export
):
    _ctx, _project_id, project_dir = baseline
    before = state(project_dir)
    monkeypatch.setattr(upload_service, "MAX_FILE_BYTES", 64)
    refused(getattr(chat_tools, export), 413, "file_too_large")
    assert state(project_dir) == before


def test_a_delivery_over_the_cap_is_refused_and_saves_nothing(baseline, monkeypatch):
    _ctx, _project_id, project_dir = baseline
    source = chat_tools.export_csv_bundle()
    before = state(project_dir)
    monkeypatch.setattr(upload_service, "MAX_FILE_BYTES", 100_000 + source["size"] - 1)
    refused(
        lambda: chat_tools.build_delivery([source["file_id"]]),
        413,
        "delivery_too_large",
    )
    assert state(project_dir) == before
    monkeypatch.setattr(upload_service, "MAX_FILE_BYTES", 100_000 + source["size"])
    assert chat_tools.build_delivery([source["file_id"]])["files_total"] == 1


def test_an_import_needs_a_named_format_and_unchanged_bytes(baseline):
    _ctx, _project_id, project_dir = baseline
    source = chat_tools.export_network_nc()
    before = state(project_dir)
    for fmt in ("pdf", "", "NETCDF"):
        refused(
            lambda f=fmt: chat_tools.import_uploaded_network(source["file_id"], f),
            422,
            "invalid_import_format",
        )
    path = upload_service.get_upload_path(
        "Tool Baseline", source["file_id"], project_dir=project_dir
    )
    path.write_bytes(path.read_bytes() + b"\0")
    refused(
        lambda: chat_tools.import_uploaded_network(source["file_id"], "netcdf"),
        409,
        "import_file_changed",
    )
    assert state(project_dir) == before


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
    before = state(project_dir)
    refused(
        lambda: chat_tools.create_chart(component, "p_nom", names), 422, "invalid_chart"
    )
    assert state(project_dir) == before
    assert (
        chat_tools.create_chart("Generator", "p_nom", ["G1"])["file_id"]
        not in before["uploads"]
    )


def test_run_evidence_is_never_exported_for_a_run_nobody_named(baseline):
    _ctx, _project_id, project_dir = baseline
    before = state(project_dir)
    refused(chat_tools.export_run_evidence, 409, "discussed_run_required")
    assert state(project_dir) == before


def _empty_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "no tables")
    return buffer.getvalue()


def _empty_workbook() -> bytes:
    buffer = io.BytesIO()
    workbook = openpyxl.Workbook()
    workbook.active.title = "notes"
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "name, data, fmt",
    [
        ("bad.nc", b"not a netcdf", "netcdf"),
        ("empty.zip", _empty_zip(), "csv_bundle"),
        ("empty.xlsx", _empty_workbook(), "excel"),
        ("bad.zip", b"PK\x03\x04junk", "csv_bundle"),
        ("bad.xlsx", b"junk", "excel"),
        ("bad.m", b"function x", "matpower"),
        # Parses its bus, then fails on the branch row: refused, not half-imported.
        (
            "half.m",
            b"mpc.bus = [\n1 3 0 0 0 0 1 1 0 110 1 1.1 0.9;\n];\n"
            b"mpc.branch = [\n1 9 x;\n];\n",
            "matpower",
        ),
    ],
)
def test_a_malformed_import_is_refused_and_keeps_the_open_network(
    baseline, session, name, data, fmt
):
    _ctx, project_id, project_dir = baseline
    PyPSAService.get_network().generators.at["G1", "p_nom"] = 33.0  # an unsaved edit
    upload = upload_service.add_upload(
        "Tool Baseline", data, name, "application/octet-stream", project_dir=project_dir
    )
    before = state(project_dir)
    frames = dispatch(
        session, "import_uploaded_network", {"file_id": upload.file_id, "format": fmt}
    )
    assert [d["error_kind"] for e, d in frames if e == "tool_error"] == [
        "import_unreadable"
    ]
    assert not [d for e, d in frames if e == "tool_result"]
    assert str(PyPSAService.get_active_context().project_uuid) == project_id
    assert state(project_dir) == before
    assert PyPSAService.get_network().generators.at["G1", "p_nom"] == 33.0


def test_a_routes_own_import_refusal_keeps_its_detail_and_the_open_network(baseline):
    """The candidate parse passes a route's own HTTPException through unchanged
    (it is not re-labelled import_unreadable) and swaps nothing in."""
    _ctx, project_id, project_dir = baseline
    PyPSAService.get_network().generators.at["G1", "p_nom"] = 33.0  # an unsaved edit
    before = state(project_dir)
    case = b"mpc.baseMVA = -1;\nmpc.bus = [\n1 3 0 0 0 0 1 1 0 110 1 1.1 0.9;\n];\n"
    refused(
        lambda: chat_tools.import_matpower(base64.b64encode(case).decode()),
        422,
        "MATPOWER baseMVA must be positive and finite",
    )
    assert str(PyPSAService.get_active_context().project_uuid) == project_id
    assert state(project_dir) == before
    assert PyPSAService.get_network().generators.at["G1", "p_nom"] == 33.0
