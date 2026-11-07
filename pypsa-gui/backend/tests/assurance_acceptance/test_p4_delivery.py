"""A4.2 (AA-05 S2): an invalid chart stays out, labels say what the data
holds, and one delivery holds one run."""

# ruff: noqa: F811 -- imported shared fixtures.
import json

import pandas as pd
import pytest
from fastapi import HTTPException

from services import assurance_artifacts, chat_tools, upload_service, workflow_tools
from tests.assurance_acceptance.test_p4_artifacts import canonical_sha256
from tests.assurance_acceptance.test_p4_chart_data import chat_tools_root, strip
from tests.test_assurance_metrics import solved  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


def paths(chart):
    root = chat_tools_root()
    blob = upload_service.get_upload_path(
        "Tool Baseline", chart["file_id"], project_dir=root
    )
    return root, blob, root / "assistant_artifacts" / (chart["file_id"] + ".json")


def marker(chart):
    root = chat_tools_root()
    path = root / "assistant_artifacts" / "quarantine" / (chart["file_id"] + ".json")
    return json.loads(path.read_text()) if path.exists() else None


def reseal(chart, change):
    """Rewrite the sidecar, its hashes AND its marker, as a store-level forger would."""
    root, blob, sidecar = paths(chart)
    record = json.loads(sidecar.read_text())
    change(record, record["manifest"])
    manifest = record["manifest"]
    manifest["dataset_sha256"] = canonical_sha256(manifest["dataset"])
    manifest["render_spec_sha256"] = canonical_sha256(manifest["render_spec"])
    sidecar.write_text(json.dumps(record))
    meta_path = blob.parent / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["assurance_sha256"] = canonical_sha256(record)
    meta_path.write_text(json.dumps(meta))


def delivery(*charts):
    with pytest.raises(HTTPException) as caught:
        chat_tools.build_delivery([chart["file_id"] for chart in charts])
    return caught.value


# Quarantine (H8, J1, J2, J4)


def test_a_chart_whose_bytes_were_forged_stays_refused_after_they_are_restored(
    baseline, client
):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    _, blob, _ = paths(chart)
    original = blob.read_bytes()
    blob.write_bytes(b"forged png")
    assert client.get(chart["download_url"]).status_code == 409
    assert marker(chart)["reason_code"] == "bytes"
    blob.write_bytes(original)
    assert client.get(chart["download_url"]).status_code == 409
    assert delivery(chart).status_code == 409


def test_a_repaired_sidecar_is_still_refused(baseline, client):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    _, _, sidecar = paths(chart)
    original = sidecar.read_text()
    record = json.loads(original)
    record["manifest"]["dataset"]["values"] = [[999]]
    sidecar.write_text(json.dumps(record))
    assert client.get(chart["download_url"]).status_code == 409
    assert marker(chart)["reason_code"] == "sidecar"
    sidecar.write_text(original)
    assert client.get(chart["download_url"]).status_code == 409


def test_a_failed_quarantine_write_still_refuses(baseline, client, monkeypatch):
    from harness import durable

    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    _, blob, _ = paths(chart)
    blob.write_bytes(b"forged png")

    def busy(*args, **kwargs):
        raise TimeoutError("file lock busy")

    monkeypatch.setattr(durable, "file_lock", busy)
    assert client.get(chart["download_url"]).status_code == 409
    assert marker(chart) is None


def test_a_read_error_refuses_once_and_writes_no_marker(baseline, client, monkeypatch):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    _, _, sidecar = paths(chart)
    read = type(sidecar).read_text

    def flaky(self, *args, **kwargs):
        if self == sidecar:
            raise OSError("transient")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(type(sidecar), "read_text", flaky)
    assert client.get(chart["download_url"]).status_code == 409
    monkeypatch.setattr(type(sidecar), "read_text", read)
    assert marker(chart) is None
    assert client.get(chart["download_url"]).status_code == 200


def test_an_outdated_or_stale_solve_writes_no_marker(solved, client):
    chart = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    n = solved.network
    n.generators.at["G1", "marginal_cost"] = 99.0
    assert client.get(chart["download_url"]).headers["X-Chart-Scope"] == "solve_stale"
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(n, solved.solver_state["solver_config"])
    assert client.get(chart["download_url"]).status_code == 409
    assert marker(chart) is None


def test_an_unreadable_or_corrupt_marker_refuses(baseline, client, monkeypatch):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    root = chat_tools_root()
    lstat = assurance_artifacts.os.lstat

    def denied(path, *args, **kwargs):
        if "quarantine" in str(path):
            raise PermissionError("denied")
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(assurance_artifacts.os, "lstat", denied)
    assert client.get(chart["download_url"]).status_code == 409
    monkeypatch.setattr(assurance_artifacts.os, "lstat", lstat)
    assert client.get(chart["download_url"]).status_code == 200
    folder = root / "assistant_artifacts" / "quarantine"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (chart["file_id"] + ".json")).write_text("not json")
    assert client.get(chart["download_url"]).status_code == 409


@pytest.mark.parametrize(
    "file_id", ["../../evil", "0123456789abcdeF", "0123456789abcdef/../x", ""]
)
def test_a_malformed_file_id_is_quarantined_by_default_and_writes_nothing(
    baseline, file_id
):
    root = baseline[2]
    assert assurance_artifacts.quarantined(root, file_id) is True
    assurance_artifacts.quarantine(root, file_id, "bytes")
    assert not (root / "assistant_artifacts" / "quarantine").exists()
    assert not list(root.parent.rglob("evil*"))


def test_a_new_file_id_is_not_affected(baseline, client):
    forged = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    paths(forged)[1].write_bytes(b"forged png")
    assert client.get(forged["download_url"]).status_code == 409
    other = chat_tools.create_chart("Generator", "marginal_cost", ["G1"])
    assert client.get(other["download_url"]).status_code == 200


# Labels (A4.2, J3)

LABELS = {
    "title": lambda r, m: m["render_spec"].update(title="Load: p_set"),
    "y_label": lambda r, m: m["render_spec"].update(y_label="MWh"),
    "dataset_unit": lambda r, m: m["dataset"]["units"].update(p_nom="GW"),
    "record_unit": lambda r, m: r.update(unit="MWh"),
    "series": lambda r, m: m["render_spec"].update(series=["G9"]),
    "kind": lambda r, m: m["render_spec"].update(kind="line"),
    "x_label": lambda r, m: m["render_spec"].update(x_label="Snapshot index"),
    "x_values": lambda r, m: m["render_spec"].update(x_values=[0]),
    "rows": lambda r, m: r.update(rows=2),
    "units_for_other_columns": lambda r, m: m["dataset"]["units"].update(G9="MW"),
}


@pytest.mark.parametrize("fault", sorted(LABELS))
def test_a_label_that_misstates_the_data_is_refused_and_quarantined(
    baseline, client, fault
):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    reseal(chart, LABELS[fault])
    assert client.get(chart["download_url"]).status_code == 409
    assert marker(chart)["reason_code"] == "labels"


def test_a_line_chart_label_check_passes_for_a_series(solved, client):
    chart = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    spec = chart["manifest"]["render_spec"]
    assert spec["kind"] == "line" and spec["x_values"] == [0, 1]
    assert client.get(chart["download_url"]).status_code == 200


def test_a_legacy_manifest_gets_no_label_check(baseline, client):
    # J3: a chart drawn before S1 (no scope keys) verifies as on master, even
    # if an older create_chart titled it differently.
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])

    def older(record, manifest):
        strip(manifest)
        manifest["render_spec"]["title"] = "Generator p_nom"

    reseal(chart, older)
    response = client.get(chart["download_url"])
    assert response.status_code == 200 and response.headers["X-Chart-Scope"] == "legacy"
    assert marker(chart) is None


# Swapped manifests (H8)


def test_another_charts_valid_sidecar_on_this_file_is_refused(baseline, client):
    mine = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    theirs = chat_tools.create_chart("Generator", "marginal_cost", ["G1"])
    other = json.loads(paths(theirs)[2].read_text())
    reseal(mine, lambda record, manifest: (record.clear(), record.update(other)))
    assert client.get(mine["download_url"]).status_code == 409
    assert marker(mine)["reason_code"] == "bytes"


def test_a_sidecar_from_another_project_is_refused(baseline, client):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    reseal(
        chart,
        lambda record, manifest: manifest.update(
            project_id="00000000-0000-4000-8000-000000000000"
        ),
    )
    assert client.get(chart["download_url"]).status_code == 409
    assert marker(chart)["reason_code"] == "project"


# Mixed runs (H8, J5)


def test_result_charts_from_two_solves_are_not_delivered_together(solved, monkeypatch):
    first = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    n = solved.network
    n.loads_t.p_set = pd.DataFrame({"L1": [6.0, 8.0]}, index=n.snapshots)
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(n, solved.solver_state["solver_config"])
    second = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    # Unloaded, both charts verify on their hashes; only the run check can refuse.
    monkeypatch.setattr(assurance_artifacts, "_resident", lambda project_id: None)
    error = delivery(first, second)
    assert error.detail["error_kind"] == "delivery_mixed_runs"
    assert (
        first["file_id"] in error.detail["message"]
        and second["file_id"] in error.detail["message"]
    )


def test_result_charts_with_the_same_input_but_another_exact_stamp_are_mixed(
    solved, monkeypatch
):
    first = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    second = chat_tools.create_chart("Load", "p", ["L1"], source="result")
    reseal(
        second, lambda record, manifest: manifest["solve_stamp"].update(exact="0" * 64)
    )
    configured = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    monkeypatch.setattr(assurance_artifacts, "_resident", lambda project_id: None)
    assert (
        delivery(configured, first, second).detail["error_kind"]
        == "delivery_mixed_runs"
    )


def test_input_charts_before_and_after_an_edit_are_not_delivered_together(baseline):
    before = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    baseline[0].network.generators.at["G1", "p_nom"] = 30
    after = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    assert delivery(before, after).detail["error_kind"] == "delivery_mixed_runs"


def test_a_legacy_chart_and_a_stamped_chart_are_not_delivered_together(baseline):
    legacy = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    reseal(legacy, lambda record, manifest: strip(manifest))
    stamped = chat_tools.create_chart("Generator", "marginal_cost", ["G1"])
    assert delivery(legacy, stamped).detail["error_kind"] == "delivery_mixed_runs"


def test_legacy_charts_alone_still_deliver(baseline):
    charts = [
        chat_tools.create_chart("Generator", a, ["G1"])
        for a in ("p_nom", "marginal_cost")
    ]
    for chart in charts:
        reseal(chart, lambda record, manifest: strip(manifest))
    bundle = chat_tools.build_delivery([chart["file_id"] for chart in charts])
    assert bundle["files_total"] == 2


def test_stale_charts_of_one_solve_deliver_together_labelled(solved, client):
    import io
    import zipfile

    charts = [
        chat_tools.create_chart(c, "p", [name], source="result")
        for c, name in (("Generator", "G1"), ("Load", "L1"))
    ]
    solved.network.generators.at["G1", "marginal_cost"] = 99.0
    bundle = chat_tools.build_delivery([chart["file_id"] for chart in charts])
    with zipfile.ZipFile(
        io.BytesIO(client.get(bundle["download_url"]).content)
    ) as archive:
        items = json.loads(archive.read("manifest.json"))["files"]
    assert [item["scope_status"] for item in items] == ["solve_stale", "solve_stale"]
