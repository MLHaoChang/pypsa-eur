"""Execution prerequisites must never execute or silently replace a plan."""

# ruff: noqa: F811 -- Imported pytest fixtures are used as test arguments.

import json

import pytest
from fastapi import HTTPException

from services import experiments as ex, run_evidence, workflow_tools
from tests.test_workflow_tools import baseline  # noqa: F401
from tests.test_experiments_persist import roots  # noqa: F401


def cases(path="Generator.G1.p_nom", value=30, value_type="number", unit="MW"):
    change = {"path": path, "value": value, "value_type": value_type}
    if unit is not None:
        change["unit"] = unit
    return [{"id": "case-a", "changes": [change]}]


def start(**kwargs):
    return ex.start_experiment(title="Bounded case", cases=cases(), request_id="same", **kwargs)


@pytest.mark.parametrize("limits", [
    {"max_cases": True}, {"max_cases": 0}, {"max_cases": 9},
    {"max_tool_attempts": 0}, {"max_elapsed_seconds": 3601},
    {"max_cost_usd": -1}, {"max_cost_usd": float("nan")},
    {"max_cost_usd": float("inf")}, {"failure_policy": "guess"},
    {"arbitrary": {"execute": True}},
])
def test_bad_limits_refuse_before_any_checkpoint(roots, limits, tmp_path):
    with pytest.raises(HTTPException) as error:
        start(limits=limits)
    assert error.value.detail["error_kind"] == "invalid_experiment_limits"
    assert not list(tmp_path.rglob("index.json"))
    assert not list(tmp_path.rglob("*.json"))


def test_changed_request_payload_cannot_reuse_original(roots):
    first = start()
    with pytest.raises(HTTPException) as error:
        ex.start_experiment(title="Different", cases=cases(), request_id="same")
    assert error.value.detail["error_kind"] == "experiment_request_conflict"
    assert ex.get_experiment(first["experiment_id"])["title"] == "Bounded case"
    assert ex.list_experiments()["total_count"] == 1


@pytest.mark.parametrize("content", ["{", '{"by_request":[],"order":[]}',
    '{"by_request":{},"order":["../../outside"]}',
    '{"by_request":{},"by_request":{},"order":[]}'])
def test_corrupt_index_does_not_create_a_replacement(roots, tmp_path, content):
    first = start()
    index = next(tmp_path.rglob("index.json"))
    index.write_text(content)
    with pytest.raises(HTTPException) as error:
        start()
    assert error.value.detail["error_kind"] == "experiment_checkpoint_unreadable"
    assert len(list(index.parent.glob("*.json"))) == 2
    assert ex.get_experiment(first["experiment_id"])["executed"] is False


def test_preflight_checks_real_candidate_without_changing_inputs(baseline):
    ctx, project_id, root = baseline
    before = workflow_tools._network_input_fingerprint(ctx)
    saved = (root / "network.nc").read_bytes()
    plan = start()
    prepared = ex.prepare_experiment(plan["experiment_id"])
    assert prepared["preflight"]["status"] == "validated"
    assert prepared["preflight"]["input_fingerprint"] == before
    assert prepared["preflight"]["project_id"] == project_id
    assert prepared["executed"] is False and prepared["execution_available"] is False
    assert workflow_tools._network_input_fingerprint(ctx) == before
    assert (root / "network.nc").read_bytes() == saved
    assert not prepared["job_ids"]
    assert prepared["cases"][0]["status"] == "planned"


@pytest.mark.parametrize("path,value,value_type,unit", [
    ("Generator.Missing.p_nom", 1, "number", "MW"),
    ("Generator.G1.p", 1, "number", "MW"),
    ("Generator.G1.p_nom", -1, "number", "MW"),
    ("Generator.G1.p_nom", 1, "number", "kW"),
    ("Generator.G1.p_nom_extendable", 1, "number", None),
    ("Generator.G1.carrier", "invented", "enum", None),
    ("Generator.G1.p_nom", 2**53 + 1, "number", "MW"),
])
def test_invalid_actual_target_cannot_be_prepared(baseline, path, value, value_type, unit):
    ctx, _, _ = baseline
    before = workflow_tools._network_input_fingerprint(ctx)
    plan = ex.start_experiment(title="Bad target", cases=cases(path, value, value_type, unit), request_id="bad")
    with pytest.raises(HTTPException):
        ex.prepare_experiment(plan["experiment_id"])
    assert ex.get_experiment(plan["experiment_id"])["preflight"]["status"] == "not_prepared"
    assert workflow_tools._network_input_fingerprint(ctx) == before


@pytest.mark.parametrize("state", ["missing", "running", "failed", "other_project", "stale", "corrupt"])
def test_unavailable_baseline_cannot_be_prepared(baseline, state):
    ctx, project, root = baseline
    fp = workflow_tools._network_input_fingerprint(ctx)
    if state == "running":
        run_evidence.start_run(root, project, run_id="base", kind="simulation", fingerprint=fp)
    elif state != "missing":
        run_evidence.publish_run(root, "other" if state == "other_project" else project,
            run_id="base", kind="simulation", status="failed" if state == "failed" else "completed",
            fingerprint="different" if state == "stale" else fp,
            sections={"summary": {"objective": 100}})
        if state == "corrupt":
            (root / "run_evidence/base/sections/summary.json").write_text("{}")
    plan = start(baseline_run_id="base")
    with pytest.raises(HTTPException):
        ex.prepare_experiment(plan["experiment_id"])
    assert ex.get_experiment(plan["experiment_id"])["preflight"]["status"] == "not_prepared"


def test_recorded_matching_baseline_and_source_drift(baseline):
    ctx, project, root = baseline
    fp = workflow_tools._network_input_fingerprint(ctx)
    run_evidence.publish_run(root, project, run_id="base", kind="simulation", status="completed",
        fingerprint=fp, sections={"summary": {"objective": 100}})
    plan = start(baseline_run_id="base")
    prepared = ex.prepare_experiment(plan["experiment_id"])
    assert prepared["preflight"]["baseline"]["run_id"] == "base"
    ctx.network.generators.at["G1", "p_nom"] = 19
    assert ex.get_experiment(plan["experiment_id"])["preflight"]["status"] == "stale"
    with pytest.raises(HTTPException):
        ex.prepare_experiment(plan["experiment_id"])


def test_sub_json_precision_change_invalidates_preflight(baseline):
    ctx, _, _ = baseline
    ctx.network.generators.at['G1', 'marginal_cost'] = 10.1234567890123
    old_marker = workflow_tools._network_input_fingerprint(ctx)
    plan = start()
    prepared = ex.prepare_experiment(plan['experiment_id'])
    old_exact = prepared['preflight']['exact_input_fingerprint']
    ctx.network.generators.at['G1', 'marginal_cost'] = 10.1234567890124
    assert workflow_tools._network_input_fingerprint(ctx) == old_marker
    assert workflow_tools._network_input_fingerprint(ctx, exact=True) != old_exact
    assert ex.get_experiment(plan['experiment_id'])['preflight']['status'] == 'stale'


def test_exact_codec_preserves_integer_precision_in_mixed_numeric_tables():
    import pandas as pd
    frame = pd.DataFrame({'integer': [2**53], 'float': [1.0]})
    first = workflow_tools._exact_frame_json(frame)
    frame.at[0, 'integer'] = 2**53 + 1
    assert workflow_tools._exact_frame_json(frame) != first


def test_exact_codec_preserves_missing_values_separately_from_zero():
    import pandas as pd
    frame = pd.DataFrame({'value': [0.0]})
    zero = workflow_tools._exact_frame_json(frame)
    frame.at[0, 'value'] = float('nan')
    assert workflow_tools._exact_frame_json(frame) != zero



def test_legacy_plan_is_readable_and_migrates_only_after_preflight(baseline):
    _, _, root = baseline
    plan = start()
    path = root / "experiments" / (plan["experiment_id"] + ".json")
    record = json.loads(path.read_text())
    record["version"] = 1
    for key in ("plan_sha256", "preflight", "operations"):
        record.pop(key, None)
    record["limits"] = {}
    path.write_text(json.dumps(record))
    assert ex.get_experiment(plan["experiment_id"])["executed"] is False
    assert json.loads(path.read_text())["version"] == 1
    out = ex.prepare_experiment(plan["experiment_id"])
    assert out["preflight"]["status"] == "validated"
    assert json.loads(path.read_text())["version"] == 2
    assert out["executed"] is False


def test_concurrent_same_request_has_one_owned_record(roots):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: start(), range(8)))
    assert len({row["experiment_id"] for row in results}) == 1
    assert ex.list_experiments()["total_count"] == 1


def test_equal_integral_float_retry_is_not_a_changed_plan(roots):
    first = start()
    second = ex.start_experiment(title='Bounded case', cases=cases(value=30.0), request_id='same')
    assert first['experiment_id'] == second['experiment_id']


def test_candidate_readback_cannot_silently_change_requested_value(baseline, monkeypatch):
    from services import assistant_tools
    original = assistant_tools._candidate
    def mismatched(ctx, changes):
        candidate, diffs, validation = original(ctx, changes)
        candidate.network.generators.at['G1', 'p_nom'] = 29
        return candidate, diffs, validation
    monkeypatch.setattr(assistant_tools, '_candidate', mismatched)
    plan = start()
    with pytest.raises(HTTPException) as error:
        ex.prepare_experiment(plan['experiment_id'])
    assert error.value.detail['error_kind'] == 'experiment_target_invalid'
    assert ex.get_experiment(plan['experiment_id'])['preflight']['status'] == 'not_prepared'


def test_legacy_ambiguous_budget_remains_readable_but_cannot_be_prepared(baseline):
    _, _, root = baseline
    plan = start()
    path = root / "experiments" / (plan["experiment_id"] + ".json")
    record = json.loads(path.read_text())
    record.update(version=1, limits={"max_cost": 10})
    for key in ("plan_sha256", "preflight", "operations"):
        record.pop(key)
    path.write_text(json.dumps(record))
    assert ex.get_experiment(plan["experiment_id"])["limits"] == {"max_cost": 10}
    with pytest.raises(HTTPException) as error:
        ex.prepare_experiment(plan["experiment_id"])
    assert error.value.detail["error_kind"] == "invalid_experiment_limits"
    assert json.loads(path.read_text())["version"] == 1


def test_pruned_evidence_invalidates_preflight(baseline):
    ctx, project, root = baseline
    run_evidence.publish_run(root, project, run_id="base", kind="simulation", status="completed",
        fingerprint=workflow_tools._network_input_fingerprint(ctx), sections={"summary": {"objective": 100}})
    plan = start(baseline_run_id="base")
    ex.prepare_experiment(plan["experiment_id"])
    (root / "run_evidence/base/sections/summary.json").unlink()
    assert ex.get_experiment(plan["experiment_id"])["preflight"]["status"] == "stale"


def test_unknown_domain_preflight_preserves_plan(baseline):
    plan = start(domain="gridspine")
    with pytest.raises(HTTPException) as error:
        ex.prepare_experiment(plan["experiment_id"])
    assert error.value.detail["error_kind"] == "experiment_adapter_unavailable"
    assert ex.get_experiment(plan["experiment_id"])["preflight"]["status"] == "not_prepared"


def test_deleted_index_and_record_cannot_restart_request(roots, tmp_path):
    plan = start()
    root = next(tmp_path.rglob("index.json")).parent
    (root / (plan["experiment_id"] + ".json")).unlink()
    with pytest.raises(HTTPException):
        start()
    assert not (root / (plan["experiment_id"] + ".json")).exists()


def test_case_cap_is_checked_against_complete_plan(roots):
    two = cases()
    two.append({**two[0], "id": "case-b"})
    with pytest.raises(HTTPException) as error:
        ex.start_experiment(title="Two", cases=two, request_id="two", limits={"max_cases": 1})
    assert error.value.detail["error_kind"] == "invalid_experiment_limits"


def test_default_store_cap_retains_old_plan_and_allows_exact_retry(roots):
    for index in range(100):
        ex.start_experiment(title="Bounded case", cases=cases(), request_id=f"request-{index}")
    with pytest.raises(HTTPException) as error:
        ex.start_experiment(title="Over cap", cases=cases(), request_id="over")
    assert error.value.detail["error_kind"] == "experiment_storage_full"
    repeat = ex.start_experiment(title="Bounded case", cases=cases(), request_id="request-0")
    assert repeat["executed"] is False
    assert ex.list_experiments(limit=1)["total_count"] == 100
    assert ex.list_experiments(limit=1)["has_more"] is True


def test_authenticated_stream_uses_new_tool_without_execution(baseline, client, monkeypatch):
    from services import chat_service
    from tests.assurance_acceptance.support import Harness, tool_turn, text_turn, parse_sse
    ctx, _, _ = baseline
    plan = start()
    before = workflow_tools._network_input_fingerprint(ctx)
    harness = Harness(client, monkeypatch)
    try:
        harness.install([
            tool_turn("prepare_experiment", {"experiment_id": plan["experiment_id"]}),
            text_turn("The prerequisites were checked; no cases ran."),
        ])
        response = client.post('/api/chat/stream', json={
            'message': 'prepare_experiment for this network plan', 'session_id': 'experiment-preflight-stream'})
        harness.session_ids.append('experiment-preflight-stream')
        assert response.status_code == 200, response.text
        frames = parse_sse(response.text)
        results = [payload["result"] for event, payload in frames
                   if event == "tool_result" and payload.get("tool_name") == "prepare_experiment"]
        assert results and results[0]["preflight"]["status"] == "validated"
        assert results[0]["executed"] is False
        assert workflow_tools._network_input_fingerprint(ctx) == before
        assert not ctx.results_unsaved
    finally:
        for session_id in harness.session_ids:
            chat_service.drop_session(session_id)


def test_guided_denial_does_not_prepare_plan(baseline, monkeypatch):
    from harness.providers.fake import FakeProvider
    from tests.test_openai_comprehensive_live import setup_profile, turn
    plan = start()
    session = setup_profile(monkeypatch, {"prepare_experiment"})
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "prepare", "name": "prepare_experiment",
                     "input": {"experiment_id": plan["experiment_id"]}}]},
        {"blocks": [{"type": "text", "text": "Declined."}]},
    ])
    frames = turn(session, "Check the experiment", fake, decision="deny", ui_context={"ui_mode": "guided"})
    assert any(event == "tool_pending_confirmation" for event, _ in frames)
    assert ex.get_experiment(plan["experiment_id"])["preflight"]["status"] == "not_prepared"
