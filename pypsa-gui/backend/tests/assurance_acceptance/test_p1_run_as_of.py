"""A1.3: a selected completed run stays checked as of its finish.

Issue 02 (P1). `get_run_evidence` with an exact `run_id` is a checked read of
that run's recorded metrics, bound to the run and a digest of its immutable
manifest taken before the handler ran. Editing the current inputs invalidates
current-source claims but not the run's; a newer failed or running run neither
replaces it nor can itself be checked. A rewritten manifest, a corrupt section,
another project or revoked access makes the run's evidence unavailable.
"""

# ruff: noqa: F811 -- imported shared fixtures.

import json

import pytest

from services import chat_tools, run_evidence
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.test_p1_revocation import revoke  # noqa: F401
from tests.assurance_acceptance.support import (
    approval,
    public_text,
    text_turn,
    tool_turn,
)

FINISHED = "2026-10-02T10:05:00+00:00"


def publish(root, project_id, run_id, status, started, **extra):
    kwargs = {"error": None, "metrics": None}
    if status == "completed":
        kwargs["metrics"] = {
            "objective": {"value": 1250.5, "unit": "EUR"},
            "served_energy": {"value": 42, "unit": "MWh"},
        }
        kwargs["sections"] = {"summary": {"objective": 1250.5}}
    elif status == "failed":
        kwargs["error"] = {"error_kind": "solver_infeasible", "message": "Infeasible."}
    kwargs.update(extra)
    run_evidence.publish_run(
        root,
        project_id,
        run_id=run_id,
        kind="simulation",
        status=status,
        started_at=started,
        finished_at=FINISHED if status == "completed" else started,
        mode="lopf",
        fingerprint="fp-old",
        **kwargs,
    )


@pytest.fixture
def run(baseline):
    _ctx, project_id, root = baseline
    publish(root, project_id, "run-old", "completed", "2026-10-02T10:00:00+00:00")
    return baseline


def newer_runs(root, project_id):
    publish(root, project_id, "run-failed", "failed", "2026-10-03T10:00:00+00:00")
    run_evidence.start_run(
        root,
        project_id,
        run_id="run-live",
        kind="simulation",
        started_at="2026-10-04T10:00:00+00:00",
        mode="lopf",
    )


def run_draft(value=1250.5, metric="objective", unit="EUR", run_id="run-old"):
    return json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "run-metric",
                    "kind": "observed",
                    "entity_id": run_id,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "source": {"tool_use_id": "read-run", "path": f"/{metric}"},
                }
            ],
        }
    )


def read_run(harness, draft=None, args=None):
    return harness.run(
        [
            tool_turn(
                "get_run_evidence",
                {"run_id": "run-old"} if args is None else args,
                call_id="read-run",
            ),
            text_turn(draft or run_draft()),
            text_turn(draft or run_draft()),
        ]
    )


def run_result(frames):
    return next(
        data
        for event, data in frames
        if data.get("tool_use_id") == "read-run"
        and event in ("tool_result", "tool_error")
    )


def checked_run(harness):
    approved = approval(read_run(harness))
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


def wrap_handler(monkeypatch, after=lambda result: None):
    real = chat_tools.DISPATCHERS["get_run_evidence"]
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        result = real(**kwargs)
        after(result)
        return result

    monkeypatch.setitem(chat_tools.DISPATCHERS, "get_run_evidence", handler)
    return calls


def test_a_completed_run_metric_is_checked_as_of_its_finish(run, harness):
    frames = read_run(harness)
    approved = approval(frames)
    assert approved["verdict"] == "passed"
    text = public_text(frames)
    assert f"Run run-old (completed {FINISHED}): objective = 1250.5 EUR" in text
    assert "as of that run, not the current inputs" in text
    record = harness.client.get(
        f"/api/chat/evidence/{approved['receipt']['evidence_ids'][0]}"
    ).json()
    assert record["project_id"] == run[1]
    assert record["scope"] == {
        "kind": "run",
        "entity_id": "run-old",
        "run_kind": "simulation",
        "mode": "lopf",
        "finished_at": FINISHED,
        "unsaved_inputs": False,
    }
    assert record["facts"] == [
        {
            "path": "/objective",
            "entity_id": "run-old",
            "metric": "objective",
            "value": 1250.5,
            "unit": "EUR",
        },
        {
            "path": "/served_energy",
            "entity_id": "run-old",
            "metric": "served_energy",
            "value": 42,
            "unit": "MWh",
        },
    ]
    # The model sees only the run's identity and its certified metrics.
    shown = run_result(frames)["result"]
    assert shown["metrics"] == {
        "objective": {"value": 1250.5, "unit": "EUR"},
        "served_energy": {"value": 42, "unit": "MWh"},
    }
    assert shown["run_id"] == "run-old" and shown["finished_at"] == FINISHED


def test_input_edit_invalidates_current_claims_but_not_the_run(run, harness):
    _, run_paths = checked_run(harness)
    current = approval(harness.load_fact())
    current_path = f"/api/chat/answers/{current['answer_id']}"
    assert harness.client.get(current_path).status_code == 200
    PyPSAService.get_active_context().network.loads.at["L1", "p_set"] = 9
    assert harness.client.get(current_path).status_code == 409
    assert [harness.client.get(p).status_code for p in run_paths] == [200, 200, 200]


def test_newer_failed_and_running_runs_do_not_replace_it(run, harness):
    evidence_id, paths = checked_run(harness)
    newer_runs(run[2], run[1])
    assert [harness.client.get(p).status_code for p in paths] == [200, 200, 200]
    record = harness.client.get(paths[2]).json()
    assert record["facts"][0]["entity_id"] == "run-old"
    assert record["scope"]["finished_at"] == FINISHED
    # A follow-up still names the selected run, not the newest one.
    again = approval(read_run(harness))
    assert again["verdict"] == "passed"


@pytest.mark.parametrize("run_id", ["run-failed", "run-live", "run-missing"])
def test_a_run_that_did_not_complete_is_refused_before_the_handler(
    run, harness, monkeypatch, run_id
):
    newer_runs(run[2], run[1])
    calls = wrap_handler(monkeypatch)
    frames = read_run(harness, run_draft(run_id=run_id), {"run_id": run_id})
    assert not calls
    refused = run_result(frames)
    assert refused["error_kind"] == "run_not_checkable"
    assert "completed" in refused["message"]
    assert approval(frames)["verdict"] == "limited"
    assert "1250.5" not in json.dumps(frames)


@pytest.mark.parametrize(
    "args",
    [
        {"run_id": "run-old", "section": "summary"},
        {"run_id": "run-old", "metrics": ["objective"]},
        {"run_id": 7},
        {},
    ],
    ids=["section", "extra", "number", "missing"],
)
def test_inexact_args_are_refused_before_the_handler(run, harness, monkeypatch, args):
    calls = wrap_handler(monkeypatch)
    frames = read_run(harness, args=args)
    assert not calls
    assert run_result(frames)["error_kind"] == "capability_unsupported"
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize(
    "draft",
    [
        run_draft(value=1250),
        run_draft(unit="kEUR"),
        run_draft(run_id="run-new"),
        run_draft(metric="emissions"),
    ],
    ids=["value", "unit", "run", "metric"],
)
def test_a_claim_off_the_recorded_metric_is_refused(run, harness, draft):
    frames = read_run(harness, draft)
    assert approval(frames)["verdict"] == "limited"
    assert "Run run-old" not in public_text(frames)


def rewrite_manifest(root, change):
    path = root / "run_evidence" / "run-old" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    change(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_a_rewritten_manifest_makes_the_run_unavailable(run, harness):
    _, paths = checked_run(harness)
    rewrite_manifest(run[2], lambda m: m["metrics"]["objective"].update(value=999))
    assert_unavailable(harness, paths)


def test_a_corrupt_section_makes_the_run_unavailable(run, harness):
    _, paths = checked_run(harness)
    section = run[2] / "run_evidence" / "run-old" / "sections" / "summary.json"
    section.write_text('{"objective": 1}', encoding="utf-8")
    assert_unavailable(harness, paths)


def test_another_project_cannot_read_the_run_evidence(run, harness):
    _, paths = checked_run(harness)
    for path in (
        "/api/projects/Another Baseline",
        "/api/projects/Another Baseline/activate",
    ):
        assert harness.client.post(path).status_code == 200
    assert_unavailable(harness, paths)


def test_a_manifest_changed_between_guard_and_capture_yields_no_evidence(
    run, harness, monkeypatch
):
    wrap_handler(
        monkeypatch,
        lambda result: rewrite_manifest(
            run[2], lambda m: m["metrics"]["served_energy"].update(value=43)
        ),
    )
    frames = read_run(harness)
    assert run_result(frames)["result"]["_assurance"]["evidence_ids"] == []
    assert approval(frames)["verdict"] == "limited"


def test_unsaved_inputs_are_named_in_the_answer(baseline, harness):
    _ctx, project_id, root = baseline
    publish(
        root,
        project_id,
        "run-old",
        "completed",
        "2026-10-02T10:00:00+00:00",
        unsaved_inputs=True,
    )
    frames = read_run(harness)
    assert approval(frames)["verdict"] == "passed"
    assert "solved with unsaved inputs" in public_text(frames)


@pytest.mark.parametrize(
    "field, value",
    [
        ("finished_at", "2026-10-09T00:00:00+00:00"),
        ("mode", "pf"),
        ("status", "failed"),
    ],
)
def test_any_manifest_change_makes_the_run_unavailable(run, harness, field, value):
    _, paths = checked_run(harness)
    rewrite_manifest(run[2], lambda m: m.update({field: value}))
    assert_unavailable(harness, paths)


def test_a_rewritten_evidence_fact_is_unavailable(run, harness):
    """A fact the run did not record never reads as checked, checksum or not."""
    from tests.assurance_acceptance.test_p1_integrity import rewrite

    evidence_id, paths = checked_run(harness)
    rewrite(evidence_id, lambda r: r["facts"][0].update(value=999))
    assert_unavailable(harness, paths)


@pytest.mark.parametrize(
    "field, value",
    [
        ("finished_at", "2026-10-09T00:00:00+00:00"),
        ("mode", "pf"),
        ("unsaved_inputs", True),
        ("entity_id", "run-new"),
    ],
)
def test_a_rewritten_evidence_scope_is_unavailable(run, harness, field, value):
    from tests.assurance_acceptance.test_p1_integrity import rewrite

    evidence_id, paths = checked_run(harness)
    rewrite(evidence_id, lambda r: r["scope"].update({field: value}))
    assert_unavailable(harness, paths)


MALFORMED = {
    "metrics_list": lambda m: m.update(metrics=[]),
    "sections_list": lambda m: m.update(sections=["summary"]),
    "no_finished_at": lambda m: m.update(finished_at=None),
}


@pytest.mark.parametrize("fault", sorted(MALFORMED))
def test_a_malformed_manifest_is_refused_and_invalidates(
    run, harness, monkeypatch, fault
):
    _, paths = checked_run(harness)
    rewrite_manifest(run[2], MALFORMED[fault])
    assert_unavailable(harness, paths)
    calls = wrap_handler(monkeypatch)
    frames = read_run(harness)
    assert not calls
    assert run_result(frames)["error_kind"] == "run_not_checkable"
    assert approval(frames)["verdict"] == "limited"


def test_a_markup_unit_is_escaped_in_the_answer(baseline, harness):
    _ctx, project_id, root = baseline
    publish(
        root,
        project_id,
        "run-old",
        "completed",
        "2026-10-02T10:00:00+00:00",
        metrics={"objective": {"value": 1250.5, "unit": "[EUR](http://x)"}},
    )
    frames = read_run(harness, run_draft(unit="[EUR](http://x)"))
    assert approval(frames)["verdict"] == "passed"
    assert "\\[EUR\\](http://x)" in public_text(frames)


@pytest.mark.parametrize("value", [True, "1250.5", None])
def test_a_non_number_claim_never_certifies(run, harness, value):
    frames = read_run(harness, run_draft(value=value))
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize("kind, status", [("membership", 404), ("disabled", 401)])
def test_revoked_access_reads_like_an_unknown_record(
    run, harness, revoke, kind, status
):
    _, paths = checked_run(harness)
    revoke[kind]()
    for path in paths:
        response = harness.client.get(path)
        assert response.status_code == status, response.text
        assert "1250.5" not in response.text
