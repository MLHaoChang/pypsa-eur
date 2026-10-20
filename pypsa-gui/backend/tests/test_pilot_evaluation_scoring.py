"""The gate must detect plausible corrupt answers and cannot pass on abstention."""
import copy
import hashlib
import json

import httpx
import pytest

from harness.pilot_evaluation import CASES, GOLDEN_CASES, LiveBudget, public_label, report, score
from harness.protocol import ProviderError
from harness.providers.pilot_evaluation import bounded_provider, reserve_request
from tests.test_openai_compatibility import request, sse


def observation(case):
    text = "\n".join(["Independent checked fixture result."] + [
        f"{public_label(case, metric)}: {value} {unit}." for metric, (value, unit) in case.expected.items()])
    scope = {"profile": case.profile, "entity_id": case.entity}
    if "cost" in case.profile and not case.clarification:
        scope["cost_basis"] = {"currency": "EUR", "price_year": 2026,
                               "annual_fixed_cost_fraction": 0.5 if case.profile.startswith("storage-") else 1}
    receipt = {"request_scope": scope, "request_complete": case.valid,
               "policy_version": "1",
               "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "result_slots": [{"metric": metric, "disposition": "answered"} for metric in case.expected]
                    + [{"metric": metric, "disposition": "needs_clarification" if case.clarification else "unavailable"} for metric in case.unavailable]}
    answer = {"version": 1, "text": text, "answer_id": "owned-answer", "turn_id": "owned-turn", "receipt": receipt,
              "blocks": [{"entity_id": case.entity, "metric": metric, "value": value, "unit": unit} for metric, (value, unit) in case.expected.items()]}
    approved = {"version": 1, "answer_id": "owned-answer", "turn_id": "owned-turn", "receipt": receipt, "verdict": "passed" if case.valid else "limited"}
    frames = [("session_init", {"turn_id": "owned-turn", "session_id": "owned-session", "assurance": {"version": 1, "mode": "strict", "state": "checking"}}),
              ("answer_approved", approved), ("token", {"delta": text}), ("turn_done", {"answer_id": "owned-answer"})]
    return frames, answer


@pytest.mark.parametrize("corrupt", ["value", "unit", "bool", "entity", "turn", "missing", "duplicate", "all_abstain", "early_token", "late_token", "digest", "rendered_value"])
def test_corrupt_passed_receipt_cannot_satisfy_gate(corrupt):
    case = GOLDEN_CASES[0]
    frames, answer = observation(case)
    if corrupt in ("value", "unit", "bool"):
        answer["blocks"][0]["unit" if corrupt == "unit" else "value"] = "MW" if corrupt == "unit" else True if corrupt == "bool" else 999
    elif corrupt == "entity":
        answer["blocks"][0]["entity_id"] = "another-generator"
    elif corrupt == "turn":
        frames[0][1]["turn_id"] = "different-turn"
    elif corrupt == "missing":
        answer["blocks"].pop()
    elif corrupt == "duplicate":
        answer["blocks"].append(copy.deepcopy(answer["blocks"][0]))
    elif corrupt == "all_abstain":
        answer["blocks"] = []
        for slot in answer["receipt"]["result_slots"]:
            slot["disposition"] = "unavailable"
    elif corrupt == "early_token":
        frames.insert(1, ("token", {"delta": "Unapproved 999 MW"}))
    elif corrupt == "late_token":
        frames.append(("token", {"delta": "Unapproved 999 MW"}))
    elif corrupt == "rendered_value":
        text = answer["text"].replace("17 MWh", "999 MWh")
        answer["text"] = frames[2][1]["delta"] = text
        answer["receipt"]["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    else:
        answer["receipt"]["content_sha256"] = "incorrect"
    assert score(case, frames, answer)["failures"]


def passing_results():
    results = []
    for case in CASES:
        frames, answer = observation(case)
        value = score(case, frames, answer)
        assert not value["failures"]
        value.update(mode="deterministic", execution="passed")
        results.append(value)
    return results


@pytest.mark.parametrize("fault", ["missing", "duplicate", "skipped", "not_run", "live_missing", "execution", "unknown"])
def test_incomplete_execution_never_reuses_a_smaller_denominator(fault):
    results = passing_results()
    if fault == "missing":
        results.pop()
    elif fault == "duplicate":
        results.append(results[0])
    elif fault in ("skipped", "not_run"):
        results[0]["execution"] = fault
    elif fault == "unknown":
        results[0]["case_id"] = "unmapped"
    value = report(results, live=fault == "live_missing", execution_failed=fault == "execution")
    assert not value["passed"]
    assert value["required_cases"] == len(CASES) + (len(GOLDEN_CASES) if fault == "live_missing" else 0)


def test_adversarial_false_accepts_remain_in_error_rate_when_execution_fails():
    results = passing_results()
    bad = next(result for result in results if not result["valid_request"])
    bad.update(false_accept=True, execution="failed", failures=["invalid_slot_accepted"])
    value = report(results)
    assert not value["passed"]
    assert value["metrics"]["deterministic"]["false_acceptance"]["count"] == 1
    assert value["metrics"]["live"]["false_acceptance"]["rate"] is None


def test_default_report_does_not_claim_live_calibration():
    value = report(passing_results())
    assert value["passed"]
    assert value["live_status"] == "pending_opt_in"
    assert value["population_error_estimate"] is None
    assert not value["independent_domain_approval"]


def test_expanded_gate_requires_every_additional_case_and_versions_its_manifest():
    from harness.reliability_cases import CASES as STRESS_CASES

    results = passing_results()
    partial = report(results, expanded=True)
    assert not partial["passed"]
    assert len(partial["missing_cases"]) == len(STRESS_CASES) == 80
    assert partial["required_cases"] == 174
    assert partial["manifest_sha256"] != report(results)["manifest_sha256"]
    for case in STRESS_CASES:
        frames, answer = observation(case)
        value = score(case, frames, answer)
        assert not value["failures"]
        value.update(mode="deterministic", execution="passed")
        results.append(value)
    assert report(results, expanded=True)["passed"]
    results[-1]["incorrect_calculations"] = 1
    assert not report(results, expanded=True)["passed"]


def test_unavailable_receipt_cannot_hide_a_published_numerical_claim():
    case = next(case for case in CASES if case.fault == "value")
    frames, answer = observation(case)
    text = "Generator energy: 999 MWh."
    answer["text"] = frames[2][1]["delta"] = text
    answer["receipt"]["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    result = score(case, frames, answer)
    assert result["false_accept"]
    assert result["incorrect_calculations"] == result["published_calculations"] == 1
    assert "unavailable_result_published" in result["failures"]


@pytest.mark.parametrize("counter", ["false_accept", "false_reject", "incorrect_calculations", "unchecked_publication"])
def test_zero_error_release_rule_does_not_depend_on_reason_strings(counter):
    results = passing_results()
    results[0][counter] = 1
    results[0]["failures"] = []
    assert not report(results)["passed"]


@pytest.mark.parametrize("body", [{}, {"max_tokens": True}, {"max_tokens": 0}, {"max_tokens": 2049}, {"max_tokens": 1, "max_output_tokens": 1}])
def test_live_payload_without_one_bounded_output_limit_cannot_send(body):
    meter = LiveBudget()
    with pytest.raises(ProviderError):
        reserve_request(meter, httpx.Request("POST", "https://api.openai.com/v1/chat/completions", json=body))
    assert meter.requests == 0


def test_each_physical_retry_reserves_before_send():
    meter = LiveBudget()
    meter.REQUEST_LIMIT = 1
    calls = []
    def respond(req):
        calls.append(req)
        return httpx.Response(400, json={"error": {"param": "max_completion_tokens", "code": "unsupported_parameter"}})
    with bounded_provider("synthetic-key", meter, transport=httpx.MockTransport(respond)) as provider:
        with pytest.raises(ProviderError):
            list(provider.stream(request()))
    assert len(calls) == meter.requests == 1
    assert meter.output_reserved == request().max_tokens


def test_interrupted_or_missing_usage_does_not_refund_resource_reservation():
    meter = LiveBudget()
    with bounded_provider("synthetic-key", meter, transport=httpx.MockTransport(lambda req: httpx.Response(200,
        content=sse({"choices": [{"delta": {"content": "partial"}}]}, done=False)))) as provider:
        with pytest.raises(ProviderError):
            list(provider.stream(request()))
    assert meter.requests == 1
    assert meter.output_reserved == request().max_tokens
    assert "synthetic-key" not in json.dumps(meter.snapshot())


def test_request_size_and_deadline_refuse_before_reservation(monkeypatch):
    meter = LiveBudget()
    with pytest.raises(ProviderError):
        meter.reserve(65537, 1)
    monkeypatch.setattr("harness.pilot_evaluation.time.monotonic", lambda: meter.started + 241)
    with pytest.raises(ProviderError):
        meter.reserve(1, 1)
    assert meter.requests == 0


def test_concurrent_attempts_share_one_budget():
    from concurrent.futures import ThreadPoolExecutor
    meter = LiveBudget()
    meter.REQUEST_LIMIT = 1
    def attempt(_):
        try:
            meter.reserve(100, 128)
            return True
        except ProviderError:
            return False
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(attempt, range(10))) == 1
    assert meter.requests == 1
