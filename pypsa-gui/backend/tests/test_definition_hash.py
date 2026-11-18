"""AA-10 S7: a profile's metric definition hash in checked evidence (GK B H1-H5).

The hash covers each metric's label, unit and formula_id, in the profile's
order, and the renderer version; it is recorded when a value is checked and
only compared afterwards, never recomputed into a stored record.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import io
import json
import zipfile
from collections import Counter

import pytest

from harness import calculations
from services import (
    assurance_artifacts,
    assurance_records,
    checked_charts,
    upload_service,
)
from tests.assurance_acceptance.support import approval
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_checked_charts import GENERATOR, ask, private_store, sidecar  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

PROFILE = "generator-dispatch/1"
# Pinned: a change here means every recorded generator-dispatch answer now reads
# "definition changed". Update it only together with a reviewed definition change.
GOLDEN = "sha256:e1b3b8a724e06191105f23625db18731d96a964407beea021206fb86df3d43f1"


def test_the_golden_hash_is_pinned():
    assert calculations.definition_hash(PROFILE) == GOLDEN


def test_every_registered_profile_hashes():
    for profile in calculations.PROFILES:
        assert calculations.definition_hash(profile).startswith("sha256:")


@pytest.mark.parametrize("key", ["label", "unit", "formula_id"])
def test_a_changed_definition_field_changes_the_hash(monkeypatch, key):
    metric = calculations.PROFILES[PROFILE][0]
    changed = {**calculations.DEFINITIONS[metric], key: "changed"}
    monkeypatch.setitem(calculations.DEFINITIONS, metric, changed)
    assert calculations.definition_hash(PROFILE) != GOLDEN


def test_the_metric_order_changes_the_hash(monkeypatch):
    reordered = tuple(reversed(calculations.PROFILES[PROFILE]))
    monkeypatch.setitem(calculations.PROFILES, PROFILE, reordered)
    assert calculations.definition_hash(PROFILE) != GOLDEN


def test_the_renderer_version_changes_the_hash(monkeypatch):
    monkeypatch.setattr(
        calculations, "RENDERER_VERSION", calculations.RENDERER_VERSION + 1
    )
    assert calculations.definition_hash(PROFILE) != GOLDEN


def test_another_profiles_definition_change_leaves_the_hash(monkeypatch):
    other = calculations.storage.PROFILE
    assert not set(calculations.PROFILES[other]) & set(calculations.PROFILES[PROFILE])
    before = calculations.definition_hash(other)
    metric = calculations.PROFILES[other][0]
    monkeypatch.setitem(
        calculations.DEFINITIONS,
        metric,
        {**calculations.DEFINITIONS[metric], "unit": "changed"},
    )
    assert calculations.definition_hash(other) != before
    assert calculations.definition_hash(PROFILE) == GOLDEN


def test_a_float_in_the_input_is_refused(monkeypatch):
    metric = calculations.PROFILES[PROFILE][0]
    monkeypatch.setitem(
        calculations.DEFINITIONS,
        metric,
        {**calculations.DEFINITIONS[metric], "unit": 1.0},
    )
    with pytest.raises(TypeError, match="definition_hash_float"):
        calculations.definition_hash(PROFILE)


def test_a_float_renderer_version_is_refused(monkeypatch):
    monkeypatch.setattr(calculations, "RENDERER_VERSION", 1.0)
    with pytest.raises(TypeError, match="definition_hash_float"):
        calculations.definition_hash(PROFILE)


def test_a_nested_float_is_refused():
    with pytest.raises(TypeError):
        calculations._no_floats({"a": [{"b": (1, 2.5)}]})
    calculations._no_floats({"a": [{"b": (1, "2.5", True, None)}]})


def test_an_unsupported_metric_or_unknown_profile_is_refused(monkeypatch):
    metric = calculations.PROFILES[PROFILE][0]
    monkeypatch.delitem(calculations.DEFINITIONS, metric)
    assert calculations.definition(metric)["formula_id"] == "unsupported"
    with pytest.raises(ValueError, match="unsupported_metric_definition"):
        calculations.definition_hash(PROFILE)
    with pytest.raises(KeyError):
        calculations.definition_hash("no-such-profile/1")


def test_every_formula_id_is_unique():
    """H2: the hash covers ids, not code, so one id must name one formula."""
    ids = [d["formula_id"] for d in calculations.DEFINITIONS.values()]
    ids.append(calculations.storage_costs.TOTAL_DEFINITION["formula_id"])
    assert [i for i, n in Counter(ids).items() if n > 1] == []
    assert "unsupported" not in ids


# --- Recorded in evidence, answers and charts; compared on read (H3, H4). ---


def _chart(checked, solved):
    receipt = ask(checked, solved, PROFILE, "G1", GENERATOR, ["p"])
    assert receipt["chart"]["status"] == "drawn", receipt["chart"]
    return receipt


def test_new_evidence_records_the_hash_and_hides_it(solved, checked):
    receipt = _chart(checked, solved)
    stored = assurance_records.load(
        assurance_records.actor_id(), receipt["evidence_ids"][0], "evidence"
    )
    assert stored["definition_hash"] == GOLDEN
    assert "definition_hash" not in assurance_records.public(stored)


def test_a_sealed_chart_verifies_as_drawn_and_reports_its_recorded_hash(
    solved, checked, monkeypatch
):
    from services.assistant_tools import _project

    receipt = _chart(checked, solved)
    manifest = sidecar(receipt["chart"])
    assert manifest["definition_hash"] == GOLDEN
    for metric in calculations.PROFILES[PROFILE]:
        monkeypatch.setitem(
            calculations.DEFINITIONS,
            metric,
            {**calculations.DEFINITIONS[metric], "formula_id": metric + "/changed"},
        )
    current = calculations.definition_hash(PROFILE)
    assert current != GOLDEN
    assert checked_charts.verify(manifest) == "current"
    project, root, _ = _project()
    file_id = receipt["chart"]["file_id"]
    meta = upload_service.get_upload_meta(project.name, file_id, project_dir=root)
    data = upload_service.get_upload_path(
        project.name, file_id, project_dir=root
    ).read_bytes()
    view = assurance_artifacts.verify_chart(root, meta, data, str(project.id))
    assert view["evidence_status"] == "current"
    assert view["definition_hash"] == GOLDEN != current


def test_a_chart_drawn_before_the_hash_reads_not_recorded(solved, checked):
    receipt = _chart(checked, solved)
    legacy = {
        k: v for k, v in sidecar(receipt["chart"]).items() if k != "definition_hash"
    }
    assert checked_charts.verify(legacy) == "current"
    assert assurance_artifacts.NOT_RECORDED == "not recorded"


def _answer(checked, solved):
    frames = checked.run(
        NumericalProvider(GENERATOR),
        message="Check results.",
        checked_scope={
            "version": 1,
            "profile": PROFILE,
            "project_id": solved.project_uuid,
            "entity_id": "G1",
            "retain": False,
        },
    )
    return approval(frames)["answer_id"]


def _get(checked, answer_id):
    response = checked.client.get("/api/chat/answers/" + answer_id)
    assert response.status_code == 200, response.text
    return response.json()


def _export(checked, answer_id, fmt="json"):
    response = checked.client.get(f"/api/chat/answers/{answer_id}/export?format={fmt}")
    assert response.status_code == 200, response.text
    return response


def test_the_answer_route_says_whether_the_definition_changed(
    solved, checked, monkeypatch
):
    answer_id = _answer(checked, solved)
    shown = _get(checked, answer_id)
    assert shown["definition_hash"] == GOLDEN and shown["definition_changed"] is False
    assert (
        json.loads(_export(checked, answer_id).content)["definition_changed"] is False
    )
    stored = assurance_records.load(assurance_records.actor_id(), answer_id, "answer")
    # A label change keeps every stored fact current, so the answer still reads.
    metric = calculations.PROFILES[PROFILE][0]
    monkeypatch.setitem(
        calculations.DEFINITIONS,
        metric,
        {**calculations.DEFINITIONS[metric], "label": "Renamed"},
    )
    changed = _get(checked, answer_id)
    assert changed["definition_changed"] is True
    assert changed["definition_hash"] == GOLDEN  # recorded, never recomputed
    exported = _export(checked, answer_id)
    assert json.loads(exported.content)["definition_changed"] is True
    zipped = zipfile.ZipFile(io.BytesIO(_export(checked, answer_id, "zip").content))
    assert json.loads(zipped.read("answer.json"))["definition_changed"] is True
    # The read wrote nothing back.
    assert (
        assurance_records.load(assurance_records.actor_id(), answer_id, "answer")
        == stored
    )


def test_an_answer_without_a_hash_has_no_flag(solved, checked):
    answer_id = _answer(checked, solved)
    # Resealed as a record written before S7 (records are immutable through save).
    path = assurance_records._path(assurance_records.actor_id(), answer_id)
    envelope = json.loads(path.read_text())
    del envelope["record"]["definition_hash"]
    envelope.update(assurance_records.seal(envelope["record"], answer_id))
    path.write_text(json.dumps(envelope))
    shown = _get(checked, answer_id)
    assert "definition_changed" not in shown and "definition_hash" not in shown
    assert "definition_changed" not in json.loads(_export(checked, answer_id).content)


def test_definition_changed_compares_and_never_raises():
    scope = {"request_scope": {"profile": PROFILE}}
    assert assurance_records.definition_changed(scope) is None
    assert (
        assurance_records.definition_changed({**scope, "definition_hash": GOLDEN})
        is False
    )
    assert (
        assurance_records.definition_changed({**scope, "definition_hash": "sha256:0"})
        is True
    )
    gone = {"request_scope": {"profile": "gone/1"}, "definition_hash": GOLDEN}
    assert assurance_records.definition_changed(gone) is True
    answer = {
        "receipt": {"request_scope": {"profile": PROFILE}},
        "definition_hash": GOLDEN,
    }
    assert assurance_records.definition_changed(answer) is False
