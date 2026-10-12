"""Independent labels, incomplete observations and finite-sample limits."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from harness.review_calibration import VERSION, calibrate, judgment_template, rate, synthetic_packet, validate_packet


def sample():
    packet = synthetic_packet()
    reviews = [judgment_template(packet, reviewer) for reviewer in ("domain-reviewer-a", "domain-reviewer-b")]
    # These are test-only labels, not evidence of actual expert review.
    labels = ["supported", "unsupported", "unsupported", "supported", "supported",
              "unsupported", "unsupported", "supported", "supported", "unsupported"]
    for review in reviews:
        review["independent_domain_reviewer"] = True
        for row, label in zip(review["judgments"], labels):
            row["verdict"] = label
    observations = {"version": VERSION, "packet_sha256": validate_packet(packet),
                    "source_commit": "test-source", "model": "scripted-test-only", "policy_version": "1",
                    "observations": [{"case_id": c["id"], "verdict": "pass" if label == "supported" else "withhold"}
                                     for c, label in zip(packet["cases"], labels)]}
    return packet, reviews, observations


def test_synthetic_measurement_never_implies_population_or_expert_approval():
    packet, reviews, observations = sample()
    result = calibrate(packet, reviews, observations)
    assert result["measurement_complete"]
    assert result["scored_cases"] == 10
    assert result["false_acceptance"]["count"] == result["false_rejection"]["count"] == 0
    assert result["false_acceptance"]["denominator"] == result["false_rejection"]["denominator"] == 5
    assert result["false_acceptance"]["wilson_95"] is None
    assert result["population_error_estimate"] is None
    assert result["independent_domain_approval"] is False
    assert len(result["profiles"]) == 5
    assert all(p["false_acceptance"]["denominator"] == p["false_rejection"]["denominator"] == 1
               for p in result["profiles"].values())


@pytest.mark.parametrize("fault", ["disagreement", "unresolved", "missing_label", "one_reviewer", "no_attestation", "missing_observation", "error"])
def test_unresolved_and_unobserved_cases_cannot_be_reported_complete(fault):
    packet, reviews, observations = sample()
    if fault == "disagreement":
        reviews[1]["judgments"][0]["verdict"] = "unsupported"
    elif fault == "unresolved":
        reviews[1]["judgments"][0]["verdict"] = "unresolved"
    elif fault == "missing_label":
        reviews[1]["judgments"].pop()
    elif fault == "one_reviewer":
        reviews.pop()
    elif fault == "no_attestation":
        reviews[1]["independent_domain_reviewer"] = False
    elif fault == "missing_observation":
        observations["observations"].pop()
    else:
        observations["observations"][0]["verdict"] = "error"
    result = calibrate(packet, reviews, observations)
    assert not result["measurement_complete"]
    assert result["required_cases"] == 10
    assert result["population_error_estimate"] is None
    assert result["unresolved_cases"] or result["missing_observations"] or result["provider_errors"]


@pytest.mark.parametrize("fault", ["same_reviewer", "stale_labels", "changed_evidence", "stale_predictions", "duplicate_label", "unknown_label", "duplicate_prediction", "unknown_prediction", "invented_verdict", "extra_field", "bool_attestation", "duplicate_case"])
def test_forged_stale_duplicate_or_unknown_inputs_are_rejected(fault):
    packet, reviews, observations = sample()
    if fault == "same_reviewer":
        reviews[1]["reviewer_id"] = reviews[0]["reviewer_id"]
    elif fault == "stale_labels":
        reviews[1]["packet_sha256"] = "old"
    elif fault == "changed_evidence":
        packet["cases"][0]["evidence"] += " Changed value."
    elif fault == "stale_predictions":
        observations["packet_sha256"] = "old"
    elif fault == "duplicate_label":
        reviews[0]["judgments"].append(reviews[0]["judgments"][0])
    elif fault == "unknown_label":
        reviews[0]["judgments"][0]["case_id"] = "not-in-packet"
    elif fault == "duplicate_prediction":
        observations["observations"].append(observations["observations"][0])
    elif fault == "unknown_prediction":
        observations["observations"][0]["case_id"] = "not-in-packet"
    elif fault == "invented_verdict":
        observations["observations"][0]["verdict"] = "looks-reliable"
    elif fault == "extra_field":
        reviews[0]["expert_approval"] = True
    elif fault == "bool_attestation":
        reviews[0]["independent_domain_reviewer"] = "true"
    else:
        packet["cases"].append(copy.deepcopy(packet["cases"][0]))
    with pytest.raises(ValueError):
        calibrate(packet, reviews, observations)


def test_false_acceptance_and_false_rejection_use_separate_denominators():
    packet, reviews, observations = sample()
    observations["observations"][0]["verdict"] = "withhold"
    observations["observations"][1]["verdict"] = "pass"
    result = calibrate(packet, reviews, observations)
    assert result["false_acceptance"]["count"] == result["false_rejection"]["count"] == 1
    assert result["false_acceptance"]["rate"] == result["false_rejection"]["rate"] == 0.2
    # Measurement completion is not a release verdict and cannot erase errors.
    assert result["measurement_complete"]
    assert result["independent_domain_approval"] is False


def test_zero_observations_are_pending_not_zero_error():
    result = calibrate(synthetic_packet(), [])
    assert result["status"] == "pending_independent_judgments"
    assert result["false_acceptance"]["rate"] is None
    assert len(result["unresolved_cases"]) == len(result["missing_observations"]) == 10


def test_iid_intervals_are_conditional_on_complete_attested_sampling():
    packet, reviews, observations = sample()
    packet.update(sampling="representative_iid", population="defined target", sampling_plan="external-plan-1")
    digest = validate_packet(packet)
    for document in [*reviews, observations]:
        document["packet_sha256"] = digest
    result = calibrate(packet, reviews, observations)
    assert result["false_acceptance"]["wilson_95"]["upper"] == pytest.approx(0.43448246478317476)
    assert result["population_error_estimate"] is not None
    observations["observations"].pop()
    result = calibrate(packet, reviews, observations)
    assert result["population_error_estimate"] is None
    assert result["false_acceptance"]["wilson_95"] is None


def test_binomial_limits_and_absent_denominator():
    assert rate(0, 0, iid=True) == {"count": 0, "denominator": 0, "rate": None, "wilson_95": None}
    assert rate(0, 100, iid=True)["wilson_95"]["upper"] == pytest.approx(0.03699349820698568)
    assert rate(100, 100, iid=True)["wilson_95"]["lower"] == pytest.approx(0.9630065017930143)


@pytest.mark.parametrize("fault", ["malformed", "duplicate_json", "nonfinite_json", "missing_file", "nested_json", "too_many_reviewers"])
def test_cli_invalid_inputs_overwrite_a_previous_success(tmp_path, fault):
    report = tmp_path / "report.json"
    report.write_text('{"measurement_complete": true}')
    packet = tmp_path / "packet.json"
    if fault != "missing_file":
        packet.write_text("[" * 2000 + "]" * 2000 if fault == "nested_json" else
                          "{" if fault == "malformed" else '{"version":1,"version":2}' if fault == "duplicate_json" else '{"value":NaN}')
    script = Path(__file__).resolve().parents[1] / "tools/run_review_calibration.py"
    command = [sys.executable, str(script), "score", "--packet", str(packet), "--report", str(report)]
    if fault == "too_many_reviewers":
        command += [arg for i in range(11) for arg in ("--judgments", str(tmp_path / f"missing-{i}.json"))]
    run = subprocess.run(command,
                         capture_output=True, text=True)
    assert run.returncode == 1
    assert not json.loads(report.read_text())["measurement_complete"]


def test_cli_prepare_is_blind_and_cannot_overwrite_judgments(tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools/run_review_calibration.py"
    command = [sys.executable, str(script), "prepare", "--directory", str(tmp_path)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    packet = json.loads((tmp_path / "packet.json").read_text())
    assert all(set(c) == {"id", "profile", "evidence", "interpretation"} for c in packet["cases"])
    assert subprocess.run(command, capture_output=True).returncode != 0
    report = tmp_path / "scored.json"
    command = [sys.executable, str(script), "score", "--packet", str(tmp_path / "packet.json"),
               "--judgments", str(tmp_path / "reviewer-a.json"), "--judgments", str(tmp_path / "reviewer-b.json"),
               "--report", str(report)]
    assert subprocess.run(command, capture_output=True).returncode == 1
    assert json.loads(report.read_text())["status"] == "pending_independent_judgments"


def test_cli_cannot_destroy_its_input_by_reusing_report_path(tmp_path):
    packet = tmp_path / "packet.json"
    original = json.dumps(synthetic_packet())
    packet.write_text(original)
    script = Path(__file__).resolve().parents[1] / "tools/run_review_calibration.py"
    run = subprocess.run([sys.executable, str(script), "score", "--packet", str(packet), "--report", str(packet)],
                         capture_output=True)
    assert run.returncode != 0
    assert packet.read_text() == original
