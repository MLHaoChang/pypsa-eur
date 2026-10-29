"""A1.3-COMPARE: deltas between two runs, checked as of both finishes.

Issue 02 (P1). `compare_project_runs` with two different run ids (and
optionally recorded metric names) is a checked read of the deltas it computes
from the two recorded manifests: one fact per delta in its metric's unit and
one per percentage ("%", only when the baseline is not zero), bound to the run
and to digests of both sealed manifests. Only comparable runs (same mode, same
input fingerprint) are checked; `allow_input_changes` stays unchecked.
"""

# ruff: noqa: F811 -- imported shared fixtures.

import json

import pytest

from services import chat_tools, run_evidence
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import (
    approval,
    public_text,
    text_turn,
    tool_turn,
)
from tests.assurance_acceptance.test_p1_revocation import revoke  # noqa: F401

BASE_METRICS = {
    "objective": {"value": 100, "unit": "EUR"},
    "spill": {"value": 0, "unit": "MWh"},
    "served": {"value": 40, "unit": "MWh"},
    "emissions": {"value": 3, "unit": "t"},
}
RUN_METRICS = {
    "objective": {"value": 110, "unit": "EUR"},
    "spill": {"value": 5, "unit": "MWh"},
    "served": {"value": 42, "unit": "MWh"},
    "emissions": {"value": 3000, "unit": "kg"},
}


def publish(
    root,
    project_id,
    run_id,
    metrics,
    started,
    *,
    status="completed",
    mode="lopf",
    fingerprint="fp-1",
):
    run_evidence.publish_run(
        root,
        project_id,
        run_id=run_id,
        kind="simulation",
        status=status,
        started_at=started,
        finished_at=started.replace("10:00", "10:05"),
        mode=mode,
        fingerprint=fingerprint,
        metrics=metrics if status == "completed" else None,
        sections={"summary": {"note": run_id}} if status == "completed" else None,
        error=None if status == "completed" else {"error_kind": "x", "message": "x"},
    )


@pytest.fixture
def runs(baseline):
    _ctx, project_id, root = baseline
    publish(root, project_id, "run-base", BASE_METRICS, "2026-10-01T10:00:00+00:00")
    publish(root, project_id, "run-new", RUN_METRICS, "2026-10-02T10:00:00+00:00")
    return baseline


def draft(path, metric, value, unit, run_id="run-new"):
    return json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "delta",
                    "kind": "observed",
                    "entity_id": run_id,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "source": {"tool_use_id": "compare", "path": path},
                }
            ],
        }
    )


DELTA = draft("/deltas/objective/delta/value", "objective", 10, "EUR")
PCT = draft("/deltas/objective/delta_pct", "objective", 10.0, "%")
ARGS = {"run_id": "run-new", "baseline_run_id": "run-base"}


def compare(harness, text=DELTA, args=None):
    return harness.run(
        [
            tool_turn(
                "compare_project_runs",
                ARGS if args is None else args,
                call_id="compare",
            ),
            text_turn(text),
            text_turn(text),
        ]
    )


def compare_result(frames):
    return next(
        data
        for event, data in frames
        if data.get("tool_use_id") == "compare"
        and event in ("tool_result", "tool_error")
    )


def wrap_handler(monkeypatch, change=lambda result: result):
    real = chat_tools.DISPATCHERS["compare_project_runs"]
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        return change(real(**kwargs))

    monkeypatch.setitem(chat_tools.DISPATCHERS, "compare_project_runs", handler)
    return calls


def checked_compare(harness, text=DELTA):
    approved = approval(compare(harness, text))
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


def refused(harness, monkeypatch, args, phrase):
    calls = wrap_handler(monkeypatch)
    frames = compare(harness, args=args)
    assert not calls
    result = compare_result(frames)
    assert result["error_kind"] == "run_not_checkable"
    assert phrase in result["message"]
    assert approval(frames)["verdict"] == "limited"


def manifest_path(root, run_id):
    return root / "run_evidence" / run_id / "manifest.json"


def test_a_delta_is_checked_by_hand_values(runs, harness):
    frames = compare(harness)
    approved = approval(frames)
    assert approved["verdict"] == "passed"
    assert (
        "Run run-new against baseline run-base (both completed, same inputs): "
        "objective changed by 10 EUR (run minus baseline, as recorded)."
    ) in public_text(frames)
    record = harness.client.get(
        f"/api/chat/evidence/{approved['receipt']['evidence_ids'][0]}"
    ).json()
    assert record["adapter_version"] == "run-compare/1"
    assert record["scope"] == {
        "kind": "run_comparison",
        "entity_id": "run-new",
        "baseline_run_id": "run-base",
        "mode": "lopf",
        "finished_at": "2026-10-02T10:05:00+00:00",
        "baseline_finished_at": "2026-10-01T10:05:00+00:00",
        "metrics": None,
        "unsaved_inputs": False,
    }
    # By hand: 100 -> 110 EUR is +10 EUR and +10 %; 40 -> 42 MWh is +2 MWh
    # and +5 %; spill's baseline is 0, so it has a delta and no percentage;
    # emissions are recorded in t and kg, so they have no fact at all.
    assert [
        (f["path"], f["metric"], f["value"], f["unit"]) for f in record["facts"]
    ] == [
        ("/deltas/objective/delta/value", "objective", 10, "EUR"),
        ("/deltas/objective/delta_pct", "objective", 10.0, "%"),
        ("/deltas/served/delta/value", "served", 2, "MWh"),
        ("/deltas/served/delta_pct", "served", 5.0, "%"),
        ("/deltas/spill/delta/value", "spill", 5, "MWh"),
    ]
    assert all(f["entity_id"] == "run-new" for f in record["facts"])
    shown = compare_result(frames)["result"]
    assert shown["deltas"]["objective"] == {
        "delta": {"value": 10, "unit": "EUR"},
        "delta_pct": 10.0,
    }
    assert "emissions" not in json.dumps(shown)


def test_a_percentage_is_checked_in_percent(runs, harness):
    frames = compare(harness, PCT)
    assert approval(frames)["verdict"] == "passed"
    assert "objective changed by 10 % of the baseline" in public_text(frames)


@pytest.mark.parametrize(
    "text",
    [
        draft("/deltas/objective/delta/value", "objective", 110, "EUR"),
        draft("/deltas/objective/delta/value", "objective", 10, "%"),
        draft("/deltas/objective/delta_pct", "objective", 10.0, "EUR"),
        draft("/deltas/objective/delta_pct", "objective", 0.1, "%"),
        draft("/deltas/spill/delta_pct", "spill", 0, "%"),
        draft("/deltas/emissions/delta/value", "emissions", 0, "t"),
        draft(
            "/deltas/objective/delta/value", "objective", 10, "EUR", run_id="run-base"
        ),
        draft("/objective", "objective", 110, "EUR"),
    ],
    ids=[
        "run_value",
        "unit",
        "pct_unit",
        "fraction",
        "zero_baseline",
        "units_differ",
        "baseline_entity",
        "metric_path",
    ],
)
def test_a_claim_off_a_computed_delta_is_refused(runs, harness, text):
    frames = compare(harness, text)
    assert approval(frames)["verdict"] == "limited"
    assert "against baseline" not in public_text(frames)


def test_named_metrics_limit_the_facts(runs, harness):
    args = {**ARGS, "metrics": ["served"]}
    approved = approval(
        compare(harness, draft("/deltas/served/delta/value", "served", 2, "MWh"), args)
    )
    assert approved["verdict"] == "passed"
    record = harness.client.get(
        f"/api/chat/evidence/{approved['receipt']['evidence_ids'][0]}"
    ).json()
    assert record["scope"]["metrics"] == ["served"]
    assert {f["metric"] for f in record["facts"]} == {"served"}


def test_allow_input_changes_false_is_still_checked(runs, harness):
    approved = approval(compare(harness, args={**ARGS, "allow_input_changes": False}))
    assert approved["verdict"] == "passed"


@pytest.mark.parametrize(
    "args",
    [
        {**ARGS, "allow_input_changes": True},
        {**ARGS, "extra": 1},
        {"run_id": "run-new", "baseline_run_id": "run-new"},
        {"run_id": "run-new"},
        {**ARGS, "metrics": "objective"},
        {**ARGS, "metrics": []},
        {"run_id": 7, "baseline_run_id": "run-base"},
    ],
    ids=[
        "allow_input_changes",
        "extra",
        "same_run",
        "no_baseline",
        "metrics_text",
        "metrics_empty",
        "number",
    ],
)
def test_inexact_compare_args_are_refused_before_the_handler(
    runs, harness, monkeypatch, args
):
    calls = wrap_handler(monkeypatch)
    frames = compare(harness, args=args)
    assert not calls
    assert compare_result(frames)["error_kind"] == "capability_unsupported"
    assert approval(frames)["verdict"] == "limited"


def test_runs_with_different_inputs_are_refused(runs, harness, monkeypatch):
    publish(
        runs[2],
        runs[1],
        "run-other",
        RUN_METRICS,
        "2026-10-03T10:00:00+00:00",
        fingerprint="fp-2",
    )
    args = {"run_id": "run-other", "baseline_run_id": "run-base"}
    refused(
        harness, monkeypatch, args, "the runs are not comparable (fingerprint mismatch)"
    )


def test_runs_in_different_modes_are_refused(runs, harness, monkeypatch):
    publish(
        runs[2], runs[1], "run-pf", RUN_METRICS, "2026-10-03T10:00:00+00:00", mode="pf"
    )
    args = {"run_id": "run-pf", "baseline_run_id": "run-base"}
    refused(harness, monkeypatch, args, "the runs are not comparable (mode mismatch)")


def test_a_failed_baseline_is_refused_naming_its_role(runs, harness, monkeypatch):
    publish(
        runs[2],
        runs[1],
        "run-failed",
        None,
        "2026-10-03T10:00:00+00:00",
        status="failed",
    )
    args = {"run_id": "run-new", "baseline_run_id": "run-failed"}
    refused(harness, monkeypatch, args, "the baseline run is failed")


def test_an_unsealed_run_is_refused_naming_its_role(runs, harness, monkeypatch):
    path = manifest_path(runs[2], "run-new")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    del manifest["mac"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    refused(harness, monkeypatch, ARGS, "the run is unverified")


def test_no_delta_in_a_shared_unit_is_refused(runs, harness, monkeypatch):
    args = {**ARGS, "metrics": ["emissions"]}
    refused(
        harness, monkeypatch, args, "no recorded metric has a delta in the same unit"
    )


def test_a_shown_delta_off_the_record_is_not_certified(runs, harness, monkeypatch):
    def change(result):
        result["deltas"]["objective"]["delta"]["value"] = 11
        return result

    wrap_handler(monkeypatch, change)
    assert approval(compare(harness))["verdict"] == "limited"
    # The percentage it showed exactly still certifies.
    assert approval(compare(harness, PCT))["verdict"] == "passed"


def test_a_run_changed_between_guard_and_capture_yields_no_evidence(
    runs, harness, monkeypatch
):
    def change(result):
        path = manifest_path(runs[2], "run-base")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["metrics"]["objective"]["value"] = 99
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return result

    wrap_handler(monkeypatch, change)
    frames = compare(harness)
    assert compare_result(frames)["result"]["_assurance"]["evidence_ids"] == []


def test_input_edits_leave_a_checked_delta_checked(runs, harness):
    _, paths = checked_compare(harness)
    PyPSAService.get_active_context().network.loads.at["L1", "p_set"] = 9
    assert [harness.client.get(p).status_code for p in paths] == [200, 200, 200]


@pytest.mark.parametrize("run_id", ["run-new", "run-base"])
def test_a_changed_manifest_on_either_side_makes_it_unavailable(runs, harness, run_id):
    _, paths = checked_compare(harness)
    path = manifest_path(runs[2], run_id)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["metrics"]["objective"]["value"] += 1
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert_unavailable(harness, paths)


def test_a_corrupt_baseline_section_makes_it_unavailable(runs, harness):
    _, paths = checked_compare(harness)
    section = runs[2] / "run_evidence" / "run-base" / "sections" / "summary.json"
    section.write_text("{}", encoding="utf-8")
    assert_unavailable(harness, paths)


def test_a_rewritten_evidence_fact_is_unavailable(runs, harness):
    from tests.assurance_acceptance.test_p1_integrity import rewrite

    evidence_id, paths = checked_compare(harness)
    rewrite(evidence_id, lambda r: r["facts"][0].update(value=11))
    assert_unavailable(harness, paths)


@pytest.mark.parametrize(
    "field, value",
    [("baseline_run_id", "run-new"), ("metrics", ["objective"]), ("mode", "pf")],
)
def test_a_rewritten_evidence_scope_is_unavailable(runs, harness, field, value):
    from tests.assurance_acceptance.test_p1_integrity import rewrite

    evidence_id, paths = checked_compare(harness)
    rewrite(evidence_id, lambda r: r["scope"].update({field: value}))
    assert_unavailable(harness, paths)


def test_another_project_cannot_read_the_delta_evidence(runs, harness):
    _, paths = checked_compare(harness)
    for path in (
        "/api/projects/Another Baseline",
        "/api/projects/Another Baseline/activate",
    ):
        assert harness.client.post(path).status_code == 200
    assert_unavailable(harness, paths)


@pytest.mark.parametrize("kind, status", [("membership", 404), ("disabled", 401)])
def test_revoked_access_reads_like_an_unknown_record(
    runs, harness, revoke, kind, status
):
    _, paths = checked_compare(harness)
    revoke[kind]()
    for path in paths:
        assert harness.client.get(path).status_code == status


@pytest.mark.parametrize("run_id", ["run-new", "run-base"])
def test_another_sealed_manifest_on_either_side_makes_it_unavailable(
    runs, harness, run_id
):
    # As a run id reused after retention would get: same metrics compared,
    # another warning, sealed by this server.
    _, paths = checked_compare(harness)
    path = manifest_path(runs[2], run_id)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.pop("mac")
    manifest["warnings"] = ["reused"]
    path.write_text(json.dumps(run_evidence._sealed(manifest)), encoding="utf-8")
    assert run_evidence.checked_run_manifest(run_id)[2] == "completed"
    assert_unavailable(harness, paths)
