"""A1.3-SECTIONS: a run's recorded section, checked as of its finish.

Issue 02 (P1). `get_run_evidence` with exactly a `run_id` and a `section` is a
checked read of the numbers that section records with a unit: every
{"value": number, "unit": text} object in it is one fact at its own pointer
under /data, bound to the run and a digest of its sealed manifest. Everything
else in the section stays unchecked data. A section that records no such
number, or too many, is refused before the handler, like a run that does not
check.
"""

# ruff: noqa: F811 -- imported shared fixtures.

import json

import pytest

from services import assurance_records as records
from services import chat_tools, run_evidence
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import (
    approval,
    public_text,
    text_turn,
    tool_turn,
)
from tests.assurance_acceptance.test_p1_revocation import revoke  # noqa: F401
from tests.assurance_acceptance.test_p1_run_as_of import FINISHED, publish

SUMMARY = {
    "metrics": {
        "objective": {"value": 1250.5, "unit": "EUR"},
        "served_energy": {"value": 42, "unit": "MWh"},
    },
    "by_carrier": [
        {"carrier": "gas", "objective": {"value": 800, "unit": "EUR"}},
        {"carrier": "wind", "objective": {"value": 450.5, "unit": "EUR"}},
    ],
    "plain": 3,
    "flag": {"value": True, "unit": "x"},
    "long_unit": {"value": 1, "unit": "u" * 17},
    "a~/b": {"value": 7, "unit": "MW"},
    "note": "unchecked text",
}


@pytest.fixture
def run(baseline):
    _ctx, project_id, root = baseline
    publish(
        root,
        project_id,
        "run-old",
        "completed",
        "2026-10-02T10:00:00+00:00",
        sections={"summary": SUMMARY, "notes": {"text": "no numbers"}},
    )
    return baseline


def draft(path, metric, value, unit="EUR", run_id="run-old"):
    return json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "section-value",
                    "kind": "observed",
                    "entity_id": run_id,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "source": {"tool_use_id": "read-section", "path": path},
                }
            ],
        }
    )


OBJECTIVE = draft("/data/metrics/objective/value", "objective", 1250.5)


def read_section(harness, text=OBJECTIVE, args=None):
    return harness.run(
        [
            tool_turn(
                "get_run_evidence",
                {"run_id": "run-old", "section": "summary"} if args is None else args,
                call_id="read-section",
            ),
            text_turn(text),
            text_turn(text),
        ]
    )


def section_result(frames):
    return next(
        data
        for event, data in frames
        if data.get("tool_use_id") == "read-section"
        and event in ("tool_result", "tool_error")
    )


def wrap_handler(monkeypatch, change=lambda result: result):
    real = chat_tools.DISPATCHERS["get_run_evidence"]
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        return change(real(**kwargs))

    monkeypatch.setitem(chat_tools.DISPATCHERS, "get_run_evidence", handler)
    return calls


def checked_section(harness, text=OBJECTIVE):
    approved = approval(read_section(harness, text))
    assert approved["verdict"] == "passed"
    (evidence_id,) = approved["receipt"]["evidence_ids"]
    paths = [
        f"/api/chat/answers/{approved['answer_id']}",
        f"/api/chat/answers/{approved['answer_id']}/export",
        f"/api/chat/evidence/{evidence_id}",
    ]
    assert [harness.client.get(p).status_code for p in paths] == [200, 200, 200]
    return evidence_id, paths


def assert_unavailable(harness, paths):
    for path in paths:
        response = harness.client.get(path)
        assert response.status_code == 409, response.text
        assert "1250.5" not in response.text


def refused(harness, monkeypatch, args, phrase):
    calls = wrap_handler(monkeypatch)
    frames = read_section(harness, args=args)
    assert not calls
    result = section_result(frames)
    assert result["error_kind"] == "run_not_checkable"
    assert phrase in result["message"]
    assert approval(frames)["verdict"] == "limited"


def test_a_section_value_is_checked_as_of_the_run(run, harness):
    frames = read_section(harness)
    approved = approval(frames)
    assert approved["verdict"] == "passed"
    assert (
        f"Run run-old (completed {FINISHED}), section summary: objective = 1250.5 EUR"
        in public_text(frames)
    )
    assert "as of that run, not the current inputs" in public_text(frames)
    record = harness.client.get(
        f"/api/chat/evidence/{approved['receipt']['evidence_ids'][0]}"
    ).json()
    manifest = json.loads(
        (run[2] / "run_evidence" / "run-old" / "manifest.json").read_text()
    )
    assert record["scope"] == {
        "kind": "run_section",
        "entity_id": "run-old",
        "run_kind": "simulation",
        "mode": "lopf",
        "finished_at": FINISHED,
        "unsaved_inputs": False,
        "section": "summary",
        "section_sha256": manifest["sections"]["summary"]["sha256"],
    }
    assert record["adapter_version"] == "run-section/1"
    # Only {value, unit} pairs with a finite, non-bool number and a 1..16
    # character unit; plain numbers, text and bools are not facts. Order is
    # the stored section's (its keys are written sorted).
    assert [
        (f["path"], f["metric"], f["value"], f["unit"]) for f in record["facts"]
    ] == [
        ("/data/a~0~1b/value", "a~/b", 7, "MW"),
        ("/data/by_carrier/0/objective/value", "objective", 800, "EUR"),
        ("/data/by_carrier/1/objective/value", "objective", 450.5, "EUR"),
        ("/data/metrics/objective/value", "objective", 1250.5, "EUR"),
        ("/data/metrics/served_energy/value", "served_energy", 42, "MWh"),
    ]
    assert all(f["entity_id"] == "run-old" for f in record["facts"])
    shown = section_result(frames)["result"]
    assert shown["section"] == "summary" and shown["run_id"] == "run-old"
    assert "data" not in shown and "note" not in json.dumps(shown)
    assert [v["path"] for v in shown["values"]] == [f["path"] for f in record["facts"]]


def test_a_repeated_key_is_matched_by_its_path(run, harness):
    second = draft("/data/by_carrier/1/objective/value", "objective", 450.5)
    assert approval(read_section(harness, second))["verdict"] == "passed"
    # The value of one occurrence never certifies at the other's path.
    crossed = draft("/data/by_carrier/0/objective/value", "objective", 450.5)
    assert approval(read_section(harness, crossed))["verdict"] == "limited"


def test_an_escaped_key_is_checked_at_its_escaped_path(run, harness):
    escaped = draft("/data/a~0~1b/value", "a~/b", 7, unit="MW")
    assert approval(read_section(harness, escaped))["verdict"] == "passed"


@pytest.mark.parametrize(
    "text",
    [
        draft("/data/plain", "plain", 3, unit="x"),
        draft("/data/flag/value", "flag", 1, unit="x"),
        draft("/data/long_unit/value", "long_unit", 1, unit="u" * 16),
        draft("/data/metrics/objective/value", "objective", 1250),
        draft("/data/metrics/objective/value", "objective", 1250.5, unit="kEUR"),
        draft("/data/metrics/objective/value", "objective", 1250.5, run_id="run-new"),
        draft("/objective", "objective", 1250.5),
    ],
    ids=["unitless", "bool", "long_unit", "value", "unit", "run", "metric_path"],
)
def test_a_claim_off_a_recorded_pair_is_refused(run, harness, text):
    frames = read_section(harness, text)
    assert approval(frames)["verdict"] == "limited"
    assert "section summary" not in public_text(frames)


def test_a_section_with_no_number_and_unit_is_refused(run, harness, monkeypatch):
    args = {"run_id": "run-old", "section": "notes"}
    refused(harness, monkeypatch, args, "records no number with a unit")


def test_an_unrecorded_section_is_refused(run, harness, monkeypatch):
    args = {"run_id": "run-old", "section": "dispatch"}
    refused(harness, monkeypatch, args, "does not record this section")


def test_a_section_over_the_fact_cap_is_refused(run, harness, monkeypatch):
    monkeypatch.setattr(records, "MAX_SECTION_FACTS", 4)
    args = {"run_id": "run-old", "section": "summary"}
    refused(harness, monkeypatch, args, "more than 4 numbers with units")


def test_a_section_whose_facts_outgrow_the_record_is_refused(run, harness, monkeypatch):
    # Within the section size and the fact count cap, but one long key sits
    # in every fact's path.
    wide = {
        "k" * 8000: {
            f"m{i}": {"value": i, "unit": "MW"}
            for i in range(records.MAX_SECTION_FACTS)
        }
    }
    publish(
        run[2],
        run[1],
        "run-wide",
        "completed",
        "2026-10-02T11:00:00+00:00",
        sections={"wide": wide},
    )
    args = {"run_id": "run-wide", "section": "wide"}
    refused(harness, monkeypatch, args, "too large to check")


def test_a_section_of_a_failed_run_is_refused(run, harness, monkeypatch):
    publish(run[2], run[1], "run-failed", "failed", "2026-10-03T10:00:00+00:00")
    args = {"run_id": "run-failed", "section": "summary"}
    refused(harness, monkeypatch, args, "this run is failed")


@pytest.mark.parametrize(
    "args",
    [
        {"run_id": "run-old", "section": "Summary"},
        {"run_id": "run-old", "section": 7},
        {"run_id": "run-old", "section": "summary", "extra": 1},
        {"section": "summary"},
    ],
    ids=["bad_name", "number", "extra", "no_run"],
)
def test_inexact_section_args_are_refused_before_the_handler(
    run, harness, monkeypatch, args
):
    calls = wrap_handler(monkeypatch)
    frames = read_section(harness, args=args)
    assert not calls
    assert section_result(frames)["error_kind"] == "capability_unsupported"
    assert approval(frames)["verdict"] == "limited"


def test_a_shown_value_off_the_record_is_not_certified(run, harness, monkeypatch):
    def change(result):
        result["data"]["metrics"]["objective"]["value"] = 999
        return result

    wrap_handler(monkeypatch, change)
    assert approval(read_section(harness))["verdict"] == "limited"
    # The other pairs it showed exactly still certify.
    served = draft("/data/metrics/served_energy/value", "served_energy", 42, unit="MWh")
    assert approval(read_section(harness, served))["verdict"] == "passed"


def test_a_section_changed_between_guard_and_capture_yields_no_evidence(
    run, harness, monkeypatch
):
    def change(result):
        section = run[2] / "run_evidence" / "run-old" / "sections" / "summary.json"
        section.write_text('{"metrics": {}}', encoding="utf-8")
        return result

    wrap_handler(monkeypatch, change)
    frames = read_section(harness)
    assert section_result(frames)["result"]["_assurance"]["evidence_ids"] == []


def test_input_edits_leave_a_checked_section_checked(run, harness):
    _, paths = checked_section(harness)
    PyPSAService.get_active_context().network.loads.at["L1", "p_set"] = 9
    assert [harness.client.get(p).status_code for p in paths] == [200, 200, 200]


def test_a_corrupt_section_makes_it_unavailable(run, harness):
    _, paths = checked_section(harness)
    section = run[2] / "run_evidence" / "run-old" / "sections" / "summary.json"
    section.write_text(json.dumps({**SUMMARY, "plain": 4}), encoding="utf-8")
    assert_unavailable(harness, paths)


def test_another_sections_corruption_makes_it_unavailable(run, harness):
    _, paths = checked_section(harness)
    notes = run[2] / "run_evidence" / "run-old" / "sections" / "notes.json"
    notes.write_text("{}", encoding="utf-8")
    assert_unavailable(harness, paths)


def test_a_rewritten_evidence_fact_is_unavailable(run, harness):
    from tests.assurance_acceptance.test_p1_integrity import rewrite

    evidence_id, paths = checked_section(harness)
    rewrite(evidence_id, lambda r: r["facts"][0].update(value=999))
    assert_unavailable(harness, paths)


@pytest.mark.parametrize(
    "field, value",
    [("section", "notes"), ("section_sha256", "0" * 64), ("entity_id", "run-new")],
)
def test_a_rewritten_evidence_scope_is_unavailable(run, harness, field, value):
    from tests.assurance_acceptance.test_p1_integrity import rewrite

    evidence_id, paths = checked_section(harness)
    rewrite(evidence_id, lambda r: r["scope"].update({field: value}))
    assert_unavailable(harness, paths)


def test_another_project_cannot_read_the_section_evidence(run, harness):
    _, paths = checked_section(harness)
    for path in (
        "/api/projects/Another Baseline",
        "/api/projects/Another Baseline/activate",
    ):
        assert harness.client.post(path).status_code == 200
    assert_unavailable(harness, paths)


@pytest.mark.parametrize("kind, status", [("membership", 404), ("disabled", 401)])
def test_revoked_access_reads_like_an_unknown_record(
    run, harness, revoke, kind, status
):
    _, paths = checked_section(harness)
    revoke[kind]()
    for path in paths:
        response = harness.client.get(path)
        assert response.status_code == status, response.text
        assert "1250.5" not in response.text


def test_a_markup_key_is_escaped_in_the_answer(baseline, harness):
    _ctx, project_id, root = baseline
    publish(
        root,
        project_id,
        "run-old",
        "completed",
        "2026-10-02T10:00:00+00:00",
        sections={"summary": {"[x](http://y)": {"value": 1, "unit": "MW"}}},
    )
    frames = read_section(
        harness, draft("/data/[x](http:~1~1y)/value", "[x](http://y)", 1, "MW")
    )
    assert approval(frames)["verdict"] == "passed"
    assert "\\[x\\](http://y)" in public_text(frames)


def test_run_evidence_section_status_names_the_reason(run):
    assert run_evidence.checked_run_section("run-old", "dispatch")[1:] == (
        None,
        None,
        "not_recorded",
    )


def test_a_section_whose_only_pair_is_a_bool_is_refused(baseline, harness, monkeypatch):
    _ctx, project_id, root = baseline
    publish(
        root,
        project_id,
        "run-old",
        "completed",
        "2026-10-02T10:00:00+00:00",
        sections={"summary": {"flag": {"value": True, "unit": "x"}}},
    )
    args = {"run_id": "run-old", "section": "summary"}
    refused(harness, monkeypatch, args, "records no number with a unit")


def test_another_sealed_manifest_for_the_run_makes_it_unavailable(run, harness):
    # As a run id reused after retention would get: same section, new metrics,
    # sealed by this server.
    _, paths = checked_section(harness)
    path = run[2] / "run_evidence" / "run-old" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.pop("mac")
    manifest["metrics"]["objective"]["value"] = 1
    path.write_text(json.dumps(run_evidence._sealed(manifest)), encoding="utf-8")
    assert run_evidence.checked_run_manifest("run-old")[2] == "completed"
    assert_unavailable(harness, paths)
