"""
Offline blind-review reconciliation and bounded calibration statistics.

Files are evaluation data, never instructions or runtime approval authority.
Reviewer independence and population sampling are attestations, not identities
this offline tool can authenticate.
"""
from __future__ import annotations

import hashlib
import json
import math

VERSION = "qualitative-review-calibration/1"
MAX_CASES = 2000


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_packet(packet):
    if not isinstance(packet, dict) or set(packet) != {"version", "sampling", "population", "sampling_plan", "cases"}:
        raise ValueError("packet_schema")
    if packet["version"] != VERSION or packet["sampling"] not in {"synthetic", "representative_iid"}:
        raise ValueError("packet_version_or_sampling")
    for key in ("population", "sampling_plan"):
        if not isinstance(packet[key], str) or len(packet[key]) > 1000:
            raise ValueError("sampling_metadata")
        if packet["sampling"] == "representative_iid" and not packet[key].strip():
            raise ValueError("sampling_metadata")
    cases = packet["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("cases_limit")
    ids = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"id", "profile", "evidence", "interpretation"}:
            raise ValueError("case_schema")
        for key, limit in (("id", 160), ("profile", 160), ("evidence", 8000), ("interpretation", 2000)):
            if not isinstance(case[key], str) or not case[key].strip() or len(case[key]) > limit:
                raise ValueError("case_limit")
        ids.append(case["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_case")
    return _hash(packet)


def judgment_template(packet, reviewer_id):
    return {"version": VERSION, "packet_sha256": validate_packet(packet),
            "reviewer_id": reviewer_id, "independent_domain_reviewer": False,
            "judgments": [{"case_id": c["id"], "verdict": "unresolved"} for c in packet["cases"]]}


def rate(count, denominator, *, iid=False):
    """Wilson 95% score interval only for an explicitly attested IID sample."""
    value = {"count": count, "denominator": denominator,
             "rate": count / denominator if denominator else None,
             "wilson_95": None}
    if denominator and iid:
        z = 1.959963984540054
        p = count / denominator
        scale = 1 + z * z / denominator
        center = (p + z * z / (2 * denominator)) / scale
        radius = z * math.sqrt(p * (1 - p) / denominator + z * z / (4 * denominator ** 2)) / scale
        value["wilson_95"] = {"lower": max(0, center - radius), "upper": min(1, center + radius)}
    return value


def _rows(document, name, allowed, required_ids, digest):
    if document.get("version") != VERSION or document.get("packet_sha256") != digest:
        raise ValueError("stale_document")
    rows = document.get(name)
    if not isinstance(rows, list) or len(rows) > MAX_CASES:
        raise ValueError("rows_limit")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"case_id", "verdict"}:
            raise ValueError("row_schema")
        case_id = row["case_id"]
        if not isinstance(case_id, str) or case_id not in required_ids or case_id in result:
            raise ValueError("unknown_or_duplicate_case")
        if not isinstance(row["verdict"], str) or row["verdict"] not in allowed:
            raise ValueError("verdict_schema")
        result[case_id] = row["verdict"]
    return result


def calibrate(packet, judgments, observations=None):
    """Incomplete/disputed labels and provider errors never become successes."""
    digest = validate_packet(packet)
    ids = [c["id"] for c in packet["cases"]]
    if not isinstance(judgments, list) or len(judgments) > 10:
        raise ValueError("reviewer_limit")
    reviewers = set()
    ballots = []
    for document in judgments:
        if not isinstance(document, dict) or set(document) != {"version", "packet_sha256", "reviewer_id", "independent_domain_reviewer", "judgments"}:
            raise ValueError("judgment_schema")
        reviewer = document["reviewer_id"]
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 160 or reviewer in reviewers:
            raise ValueError("reviewer_identity")
        if type(document["independent_domain_reviewer"]) is not bool:
            raise ValueError("independence_schema")
        reviewers.add(reviewer)
        rows = _rows(document, "judgments", {"supported", "unsupported", "unresolved"}, ids, digest)
        if document["independent_domain_reviewer"]:
            ballots.append(rows)
    labels = {}
    unresolved = []
    for case_id in ids:
        votes = [b.get(case_id, "unresolved") for b in ballots]
        if len(votes) >= 2 and len(set(votes)) == 1 and votes[0] != "unresolved":
            labels[case_id] = votes[0]
        else:
            unresolved.append(case_id)
    predictions = {}
    if observations is not None:
        if not isinstance(observations, dict) or set(observations) != {"version", "packet_sha256", "source_commit", "model", "policy_version", "observations"}:
            raise ValueError("observation_schema")
        for key in ("source_commit", "model", "policy_version"):
            if not isinstance(observations[key], str) or not observations[key].strip() or len(observations[key]) > 160:
                raise ValueError("observation_identity")
        predictions = _rows(observations, "observations", {"pass", "withhold", "error"}, ids, digest)
    missing = [case_id for case_id in ids if case_id not in predictions]
    errors = [case_id for case_id in ids if predictions.get(case_id) == "error"]
    scored = [case_id for case_id in ids if case_id in labels and predictions.get(case_id) in {"pass", "withhold"}]
    supported = [case_id for case_id in scored if labels[case_id] == "supported"]
    unsupported = [case_id for case_id in scored if labels[case_id] == "unsupported"]
    ready = not (unresolved or missing or errors)
    # Intervals cannot use a selected resolved subset of an IID sample. Missing,
    # disputed or failed observations suppress population estimates entirely.
    iid = packet["sampling"] == "representative_iid" and ready
    acceptance = rate(sum(predictions[i] == "pass" for i in unsupported), len(unsupported), iid=iid)
    rejection = rate(sum(predictions[i] == "withhold" for i in supported), len(supported), iid=iid)
    profiles = {}
    for profile in sorted({case["profile"] for case in packet["cases"]}):
        profile_ids = [c["id"] for c in packet["cases"] if c["profile"] == profile]
        positive = [i for i in supported if i in profile_ids]
        negative = [i for i in unsupported if i in profile_ids]
        profiles[profile] = {
            "required_cases": len(profile_ids), "resolved_cases": sum(i in labels for i in profile_ids),
            "scored_cases": sum(i in scored for i in profile_ids),
            "false_acceptance": rate(sum(predictions[i] == "pass" for i in negative), len(negative), iid=iid),
            "false_rejection": rate(sum(predictions[i] == "withhold" for i in positive), len(positive), iid=iid),
        }
    return {"version": VERSION, "packet_sha256": digest, "required_cases": len(ids),
            "independent_reviewers_attested": len(ballots), "resolved_cases": len(labels),
            "unresolved_cases": unresolved, "missing_observations": missing, "provider_errors": errors,
            "scored_cases": len(scored), "false_acceptance": acceptance, "false_rejection": rejection,
            "profiles": profiles,
            "withholding": rate(sum(predictions[i] == "withhold" for i in scored), len(scored)),
            "measurement_complete": ready,
            "status": "measured" if ready else "pending_independent_judgments" if unresolved else "incomplete_observations",
            "sampling": packet["sampling"],
            "population_error_estimate": {"false_acceptance": acceptance, "false_rejection": rejection} if iid else None,
            "observation_identity": {k: observations[k] for k in ("source_commit", "model", "policy_version")} if observations else None,
            "independent_domain_approval": False,
            "claim_scope": "offline_measurement_only; identity_and_iid_sampling_are_external_attestations"}


def synthetic_packet():
    """Blind examples derived from accounting boundaries, with no answer key."""
    examples = (
        ("generator-dispatch/1", "G1 generated 17 MWh; marginal dispatch expenditure is 170 EUR. Fixed capacity costs are outside this profile.",
         "170 EUR is the total system cost.", "The checked marginal dispatch expenditure is 170 EUR; this does not include fixed capacity costs."),
        ("generator-cost/1", "For price year 2026 and declared full-year allocation: variable expenditure 170 EUR; allocated fixed cost 60 EUR; total 230 EUR.",
         "230 EUR is the discounted lifetime investment cost.", "The total is 230 EUR on the declared allocation basis; lifetime discounted investment cost is not established."),
        ("network-linear-cost/1", "A two-generator linear subset costs 300 EUR on the declared basis. Other component types and nonlinear costs are unavailable.",
         "300 EUR includes every component and establishes the whole network's economic optimum.", "The checked total is 300 EUR for the supported linear subset; whole-network costs are not established."),
        ("storage-unit-dispatch/1", "Battery charged 10 MWh from the grid, discharged 4 MWh, and ended at 0 MWh. Cyclic storage and adequacy are not evaluated.",
         "The battery's 0 MWh final state proves the system meets its reliability target.", "Grid charging exceeded discharge by 6 MWh; this dispatch accounting does not establish adequacy."),
        ("storage-unit-cost/1", "Battery direct costs: discharge 20 EUR; holding 16 EUR; allocated fixed 15 EUR; total 51 EUR. Charging purchase cost and revenue are excluded.",
         "51 EUR includes all purchased charging electricity and determines arbitrage profit.", "51 EUR is the direct StorageUnit total on the declared basis; purchased charging electricity and arbitrage profit are not established."),
    )
    cases = []
    for index, (profile, evidence, first, second) in enumerate(examples):
        # Alternate presentation order; neither IDs nor packet fields encode
        # the authored suggested label. Reviewers see no model verdicts.
        texts = (first, second) if index % 2 else (second, first)
        for offset, text in enumerate(texts):
            cases.append({"id": f"interpretation-{index * 2 + offset + 1:02}", "profile": profile,
                          "evidence": evidence, "interpretation": text})
    return {"version": VERSION, "sampling": "synthetic", "population": "", "sampling_plan": "", "cases": cases}
