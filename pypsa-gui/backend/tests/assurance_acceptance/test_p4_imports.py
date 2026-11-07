"""AA-05 S3 (A4.5): imports are input assertions until validated, inspection
guesses nothing, and table exports carry a dataset hash their bytes prove."""

# ruff: noqa: F811 -- imported shared fixtures.
import datetime
import hashlib
import io
import json
import uuid
import zipfile

import pytest
from fastapi import HTTPException

from services import assurance_artifacts, chat_tools, upload_service
from tests.assurance_acceptance.test_p4_artifacts import canonical_sha256
from tests.assurance_acceptance.test_p4_chart_data import chat_tools_root
from tests.assurance_acceptance.test_p4_delivery import delivery, marker, paths, tree
from tests.test_workflow_tools import baseline  # noqa: F401


def sidecar(export):
    return json.loads(paths(export)[2].read_text())


def refused(call, *args):
    with pytest.raises(HTTPException) as caught:
        call(*args)
    return caught.value


def delivered_items(client, *exports):
    bundle = chat_tools.build_delivery([export["file_id"] for export in exports])
    with zipfile.ZipFile(io.BytesIO(client.get(bundle["download_url"]).content)) as archive:
        return json.loads(archive.read("manifest.json"))["files"]


# P1: an import is an input assertion until validated (H10, R1-R4)


@pytest.fixture(autouse=True)
def private_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))


def imported(network_change=None):
    """Export the loaded network (optionally changed first), import it back.
    An import unbinds the project, so an unsaved network is saved first."""
    from services.pypsa_service import PyPSAService

    if not PyPSAService.get_loaded_project():
        chat_tools.save_project_as("Saved " + uuid.uuid4().hex[:8])
    if network_change is not None:
        network_change(PyPSAService.get_network())
    exported = chat_tools.export_network_nc()
    return chat_tools.import_uploaded_network(exported["file_id"], "netcdf")


def network():
    from services.pypsa_service import PyPSAService

    return PyPSAService.get_network()


def status():
    return chat_tools.validate_network()["import_status"]


def records():
    import app_paths

    folder = app_paths.app_data_dir() / "assistant_import_validations"
    return sorted(p.name for p in folder.glob("*.json")) if folder.exists() else []


def test_a_network_that_was_not_imported_reads_not_imported(baseline):
    assert status() == "not_imported" and records() == []


def test_an_import_reads_input_assertion_until_a_validate_passes(baseline):
    result = imported()
    assert result["import_status"] == "input_assertion"
    stamp = network().meta["assistant_import"]
    assert stamp["format"] == "netcdf" and len(stamp["import_id"]) == 32
    assert status() == "validated"
    assert records() == [stamp["import_id"] + ".json"]
    import app_paths

    folder = app_paths.app_data_dir() / "assistant_import_validations"
    record = json.loads((folder / records()[0]).read_text())
    assert set(record) == {"import_id", "sha256", "exact", "at"}
    assert record["sha256"] == stamp["sha256"]
    network().generators.at["G1", "marginal_cost"] = 99.0
    assert assurance_artifacts.import_status(ctx()) == "input_assertion"
    assert status() == "validated"  # an edit followed by a passing validate re-stamps (R1d)


def ctx():
    from services.pypsa_service import PyPSAService

    return PyPSAService.get_active_context()


def test_a_failing_validate_leaves_it_an_input_assertion(baseline, monkeypatch):
    import routers.simulation

    imported()
    monkeypatch.setattr(
        routers.simulation,
        "preflight",
        lambda: {"ok": False, "errors": 1, "warnings": 0, "issues": []},
    )
    assert status() == "input_assertion" and records() == []


def test_validate_changes_nothing_on_the_network(baseline):
    # R2: a read-tier tool. Dirty flag, exact inputs and meta are unchanged.
    from services import dirty_state, workflow_tools

    imported()
    before = (
        dirty_state.is_dirty(ctx()),
        workflow_tools._network_input_fingerprint(ctx(), exact=True),
        json.dumps(network().meta, sort_keys=True),
    )
    assert status() == "validated"
    assert before == (
        dirty_state.is_dirty(ctx()),
        workflow_tools._network_input_fingerprint(ctx(), exact=True),
        json.dumps(network().meta, sort_keys=True),
    )


def test_a_re_import_starts_a_new_assertion(baseline):
    imported()
    assert status() == "validated"
    first = network().meta["assistant_import"]["import_id"]
    assert imported()["import_status"] == "input_assertion"
    assert network().meta["assistant_import"]["import_id"] != first
    assert assurance_artifacts.import_status(ctx()) == "input_assertion"


def test_a_file_pre_stamped_as_validated_imports_as_an_input_assertion(baseline):
    # R1, R1a: a record exists for the stamp the file carries, and the file
    # also claims a validated fingerprint; neither carries over.
    imported()
    assert status() == "validated"
    carried = dict(network().meta["assistant_import"])
    exact = assurance_artifacts.exact_or_none(ctx())

    def forge(n):
        n.meta["assistant_validated_fingerprint"] = exact

    result = imported(forge)
    assert result["import_status"] == "input_assertion"
    assert network().meta["assistant_import"]["import_id"] != carried["import_id"]
    assert not [k for k in network().meta if k.startswith("assistant_validated")]
    assert assurance_artifacts.import_status(ctx()) == "input_assertion"


@pytest.mark.parametrize("import_id", ["../../evil", "A" * 32, "", None, 7])
def test_a_malformed_import_id_reads_input_assertion_and_writes_nothing(
    baseline, import_id
):
    imported()
    network().meta["assistant_import"]["import_id"] = import_id
    assert status() == "input_assertion" and records() == []


@pytest.mark.parametrize("fault", ["corrupt", "other_sha256", "lock_timeout"])
def test_a_bad_record_or_a_busy_lock_reads_input_assertion(baseline, monkeypatch, fault):
    import app_paths

    imported()
    stamp = network().meta["assistant_import"]
    if fault == "lock_timeout":
        from harness import durable

        def busy(*args, **kwargs):
            raise TimeoutError("busy")

        monkeypatch.setattr(durable, "file_lock", busy)
        assert status() == "input_assertion"
        return
    assert status() == "validated"
    path = app_paths.app_data_dir() / "assistant_import_validations" / (stamp["import_id"] + ".json")
    if fault == "corrupt":
        path.write_text("{")
    else:
        record = json.loads(path.read_text())
        record["sha256"] = "0" * 64
        path.write_text(json.dumps(record))
    assert assurance_artifacts.import_status(ctx()) == "input_assertion"


def test_validated_survives_a_save_into_a_project_and_a_reload(baseline):
    imported()
    assert status() == "validated"
    chat_tools.save_project_as("Imported Copy")
    chat_tools.load_project("Imported Copy")
    assert network().meta["assistant_import"]["import_id"]
    assert assurance_artifacts.import_status(ctx()) == "validated"


def test_an_artifact_keeps_the_status_it_was_drawn_with(baseline):
    # R4: drawn while an input assertion, it stays one after a later validate.
    imported()
    chat_tools.save_project_as("Imported Copy")
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    table = chat_tools.export_to_csv([["1"]], ["h"], "t.csv")
    assert chart["manifest"]["validity"]["import_status"] == "input_assertion"
    assert sidecar(table)["manifest"]["validity"]["import_status"] == "input_assertion"
    assert status() == "validated"
    assert sidecar(chart)["manifest"]["validity"]["import_status"] == "input_assertion"
    later = chat_tools.create_chart("Generator", "marginal_cost", ["G1"])
    assert later["manifest"]["validity"]["import_status"] == "validated"


def test_without_an_import_no_manifest_names_an_import_status(baseline):
    # R4: byte-identical to master (the H4 pin checks the chart's validity exactly).
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    table = chat_tools.export_to_csv([["1"]], ["h"], "t.csv")
    assert "import_status" not in chart["manifest"]["validity"]
    assert "import_status" not in sidecar(table)["manifest"]["validity"]


# P2: inspection guesses nothing (R9)


def test_a_semicolon_csv_is_not_sniffed_and_nothing_is_inferred(baseline):
    meta = upload_service.add_upload(
        "Tool Baseline", b"bus;p_mw\nB1;5\n", "loads.csv", "text/csv"
    )
    report = chat_tools.inspect_import(meta.file_id)
    assert report["delimiter"] == "comma (not detected)"
    assert [c["name"] for c in report["tables"][0]["columns"]] == ["bus;p_mw"]
    assert report["units"].startswith("Not inferred")
    text = json.dumps(report).lower()
    assert "mapping" not in [key.lower() for key in report]
    assert '"unit":' not in text and "inferred_unit" not in text


@pytest.mark.parametrize("fmt", ["", "csv", "NETCDF"])
def test_an_import_without_a_named_supported_format_is_refused(baseline, fmt):
    exported = chat_tools.export_network_nc()
    error = refused(chat_tools.import_uploaded_network, exported["file_id"], fmt)
    assert error.detail["error_kind"] == "invalid_import_format"


# P3: table exports (R5-R8)


def test_a_csv_export_seals_the_table_as_written(baseline, client):
    export = chat_tools.export_to_csv([["G1", 0.1 + 0.2], ["=cmd", None]], ["name", "p"], "t.csv")
    manifest = sidecar(export)["manifest"]
    rows = manifest["dataset"]["sheets"][0]["rows"]
    assert rows == [["name", "p"], ["G1", "0.30000000000000004"], ["'=cmd", ""]]
    assert manifest["dataset_sha256"] == canonical_sha256(manifest["dataset"])
    assert manifest["artifact_sha256"] == export["sha256"]
    assert manifest["validity"] == {
        "coverage": "rows_as_written",
        "limitations": ["Cells are text as written."],
    }
    assert client.get(export["download_url"]).status_code == 200
    (item,) = delivered_items(client, export)
    assert item["dataset_sha256"] == manifest["dataset_sha256"]
    assert item["render_spec_sha256"] == manifest["render_spec_sha256"]


XLSX_CASES = {
    "timestamp_index": (
        [["at", "MW"], [datetime.datetime(2026, 1, 1, 1), 5]],
        [["at", "MW"], ["2026-01-01T01:00:00", 5]],
    ),
    "nan_and_empty_text": (
        [["a", "b"], [float("nan"), ""], [float("inf"), "x"]],
        [["a", "b"], [None, None], [None, "x"]],
    ),
    "formula_escaped": ([["=SUM(A1)", "@x"]], [["'=SUM(A1)", "'@x"]]),
    "empty_sheet": ([], []),
    "bool_date_ragged": (
        [["a", "b"], [True, datetime.date(2026, 1, 2)], ["c"]],
        [["a", "b"], [True, "2026-01-02T00:00:00"], ["c", None]],
    ),
    "seventeen_digits": (
        [[0.1 + 0.2, 12345678901234567, 1 / 3]],
        [[0.3, 12345678901234570, 0.3333333333333333]],
    ),
}


@pytest.mark.parametrize("case", sorted(XLSX_CASES))
def test_an_excel_export_seals_what_the_file_holds(baseline, client, case):
    given, held = XLSX_CASES[case]
    export = chat_tools.export_to_excel({"Data": given}, "t.xlsx")
    manifest = sidecar(export)["manifest"]
    assert manifest["dataset"] == {"sheets": [{"name": "Data", "rows": held}]}
    data = client.get(export["download_url"])
    assert data.status_code == 200
    assert canonical_sha256(
        assurance_artifacts.parse_table("xlsx", data.content)
    ) == manifest["dataset_sha256"]


def test_an_excel_sheet_name_is_the_one_the_file_carries(baseline):
    export = chat_tools.export_to_excel({"S" * 40: [["a"]], "": [["b"]]}, "t.xlsx")
    manifest = sidecar(export)["manifest"]
    names = [sheet["name"] for sheet in manifest["dataset"]["sheets"]]
    assert names == ["S" * 31, "Sheet"] == manifest["render_spec"]["sheets"]


def test_an_export_that_would_not_read_back_is_refused_and_saves_nothing(
    baseline, monkeypatch
):
    root = chat_tools_root()
    before = tree(root)
    monkeypatch.setattr(
        assurance_artifacts, "parse_table", lambda fmt, data: {"sheets": []}
    )
    error = refused(chat_tools.export_to_csv, [["a"]], ["h"], "t.csv")
    assert error.status_code == 409
    assert error.detail["error_kind"] == "table_round_trip_failed"
    assert tree(root) == before


def test_forged_table_bytes_are_refused_and_quarantined(baseline, client):
    export = chat_tools.export_to_csv([["1"]], ["h"], "t.csv")
    paths(export)[1].write_bytes(b"h\r\n2\r\n")
    assert client.get(export["download_url"]).status_code == 409
    assert marker(export)["reason_code"] == "bytes"
    assert delivery(export).status_code == 409


def test_a_table_sidecar_edited_without_its_seal_is_refused_and_quarantined(
    baseline, client
):
    export = chat_tools.export_to_csv([["1"]], ["h"], "t.csv")
    path = paths(export)[2]
    record = json.loads(path.read_text())
    record["manifest"]["dataset"]["sheets"][0]["rows"][1] = ["2"]
    path.write_text(json.dumps(record))
    assert client.get(export["download_url"]).status_code == 409
    assert marker(export)["reason_code"] == "sidecar"


@pytest.mark.parametrize("drift", ["mismatch", "raises"])
def test_a_re_parse_that_disagrees_with_intact_bytes_refuses_without_a_marker(
    baseline, client, monkeypatch, drift
):
    export = chat_tools.export_to_csv([["1"]], ["h"], "t.csv")

    def parser(fmt, data):
        if drift == "raises":
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "drift")
        return {"sheets": []}

    monkeypatch.setattr(assurance_artifacts, "parse_table", parser)
    assert client.get(export["download_url"]).status_code == 409
    assert delivery(export).status_code == 409
    assert marker(export) is None
    monkeypatch.undo()
    assert client.get(export["download_url"]).status_code == 200


def test_an_identical_re_export_of_a_quarantined_table_is_refused_and_writes_nothing(
    baseline, client
):
    export = chat_tools.export_to_csv([["1"]], ["h"], "t.csv")
    _, blob, _ = paths(export)
    original = blob.read_bytes()
    blob.write_bytes(b"forged")
    assert client.get(export["download_url"]).status_code == 409
    blob.write_bytes(original)
    root = chat_tools_root()
    before = tree(root)
    error = refused(chat_tools.export_to_csv, [["1"]], ["h"], "t.csv")
    assert error.detail["error_kind"] == "artifact_quarantined"
    assert tree(root) == before
    other = chat_tools.export_to_csv([["2"]], ["h"], "t.csv")
    assert client.get(other["download_url"]).status_code == 200


BASE_ITEM = {"file_id", "archive_path", "filename", "sha256", "size", "mime"}


def test_other_exports_deliver_with_byte_hashes_only(baseline, client):
    # R7: the five exports without a manifest build exactly as on master.
    network = chat_tools.export_network_nc()
    items = delivered_items(client, network)
    assert set(items[0]) == BASE_ITEM
    assert items[0]["sha256"] == hashlib.sha256(
        client.get(network["download_url"]).content
    ).hexdigest()


def test_a_table_and_a_scoped_chart_deliver_together(baseline, client):
    # R7: a table manifest has no scope keys, so it never joins _one_run.
    table = chat_tools.export_to_csv([["1"]], ["h"], "t.csv")
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    items = delivered_items(client, table, chart)
    assert "scope_status" not in items[0] and items[1]["scope_status"] == "input_as_drawn"
