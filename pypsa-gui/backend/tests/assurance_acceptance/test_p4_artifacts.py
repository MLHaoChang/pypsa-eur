"""A4: use the real chart/delivery handlers and isolated artifact bytes."""

import hashlib
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from services import chat_tools


def canonical_sha256(value):
    """Published protocol encoding, independent of production serialization."""

    def normalized(item):
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, dict):
            return {key: normalized(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalized(child) for child in item]
        return item

    encoded = json.dumps(
        normalized(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_chart_binds_exact_input_data_units_render_and_image_bytes(baseline, client):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    manifest = chart.get("manifest")
    assert isinstance(manifest, dict), "Chart has no exact dataset/render/byte manifest"
    assert manifest["project_id"] == baseline[1]
    assert manifest["source_kind"] == "configured_input"
    expected = {
        "index": ["G1"],
        "columns": ["p_nom"],
        "values": [[20]],
        "units": {"p_nom": "MW"},
    }
    assert manifest["dataset"] == expected
    assert manifest["dataset_sha256"] == canonical_sha256(expected)
    assert manifest["render_spec"]
    assert manifest["render_spec_sha256"] == canonical_sha256(manifest["render_spec"])
    payload = client.get(chart["download_url"])
    assert payload.status_code == 200 and payload.content.startswith(b"\x89PNG\r\n")
    assert manifest["artifact_sha256"] == hashlib.sha256(payload.content).hexdigest()


def test_legacy_storage_delivery_rejects_tampered_chart_provenance(baseline):
    # Fault injection against CURRENT storage only, not a frozen protocol path.
    # Future manifest/storage adapters must supply their own corruption fixture.
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    sidecar = baseline[2] / "assistant_artifacts" / (chart["file_id"] + ".json")
    record = json.loads(sidecar.read_text())
    record["unit"] = "MWh"
    sidecar.write_text(json.dumps(record))
    with pytest.raises(HTTPException) as failure:
        chat_tools.build_delivery([chart["file_id"]])
    assert failure.value.status_code == 409


def test_delivery_preserves_the_chart_assurance_chain(baseline, client):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    bundle = chat_tools.build_delivery([chart["file_id"]])
    response = client.get(bundle["download_url"])
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        item = json.loads(archive.read("manifest.json"))["files"][0]
        chain = item.get("assurance")
        assert chain and chain["source_kind"] == "configured_input", (
            "Delivery lost chart data/render integrity"
        )
        assert chain == chart["manifest"]
        assert chain["dataset_sha256"] == canonical_sha256(chain["dataset"])
        assert chain["render_spec_sha256"] == canonical_sha256(chain["render_spec"])
        assert (
            chain["artifact_sha256"]
            == hashlib.sha256(archive.read(item["archive_path"])).hexdigest()
        )


def test_existing_unsolved_result_chart_refusal_remains_enforced(baseline):
    with pytest.raises(HTTPException) as failure:
        chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    assert failure.value.status_code == 409


@pytest.mark.parametrize("fault", ["dataset", "render", "delete", "bytes", "unit"])
def test_download_and_delivery_reject_chart_corruption(baseline, client, fault):
    from services import upload_service

    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    sidecar = baseline[2] / "assistant_artifacts" / (chart["file_id"] + ".json")
    if fault == "delete":
        sidecar.unlink()
    elif fault == "bytes":
        upload_service.get_upload_path(
            "Tool Baseline", chart["file_id"], project_dir=baseline[2]
        ).write_bytes(b"forged png")
    else:
        record = json.loads(sidecar.read_text())
        if fault == "dataset":
            record["manifest"]["dataset"]["values"][0][0] = 999
        elif fault == "render":
            record["manifest"]["render_spec"]["title"] = "Everything is safe"
        else:
            record["unit"] = "MWh"
        sidecar.write_text(json.dumps(record))
    assert client.get(chart["download_url"]).status_code == 409
    with pytest.raises(HTTPException) as caught:
        chat_tools.build_delivery([chart["file_id"]])
    assert caught.value.status_code == 409


def test_source_changes_during_render_are_not_published(baseline, monkeypatch):
    from matplotlib.figure import Figure

    savefig = Figure.savefig

    def change_source(figure, *args, **kwargs):
        savefig(figure, *args, **kwargs)
        baseline[0].network.generators.at["G1", "p_nom"] = 21

    monkeypatch.setattr(Figure, "savefig", change_source)
    with pytest.raises(HTTPException) as caught:
        chat_tools.create_chart("Generator", "p_nom", ["G1"])
    assert caught.value.status_code == 409


def test_delivery_copies_the_same_validated_provenance(baseline, client, monkeypatch):
    from services import assurance_artifacts

    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    verify = assurance_artifacts.verify_chart

    def change_after_validation(*args, **kwargs):
        checked = verify(*args, **kwargs)
        sidecar = baseline[2] / "assistant_artifacts" / (chart["file_id"] + ".json")
        record = json.loads(sidecar.read_text())
        record["unit"] = "MWh"
        sidecar.write_text(json.dumps(record))
        return checked

    monkeypatch.setattr(assurance_artifacts, "verify_chart", change_after_validation)
    bundle = chat_tools.build_delivery([chart["file_id"]])
    # Disable injection while downloading the ordinary bundle.
    monkeypatch.setattr(assurance_artifacts, "verify_chart", verify)
    response = client.get(bundle["download_url"])
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        item = json.loads(archive.read("manifest.json"))["files"][0]
        assert item["provenance"] == chart["provenance"]
        assert item["assurance"] == chart["manifest"]
