"""Explicit rollout/rollback controls and bounded observation without trust labels."""

import os
import time
import uuid

from harness import calculations
from services import assurance_records as records


def profile_enabled(profile):
    value = os.environ.get("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES")
    return profile in calculations.PROFILES and (
        value is None or profile in value.split(",")
    )


def effects_enabled():
    return (
        os.environ.get("PYPSA_GUI_ANSWER_ASSURANCE_EFFECTS", "disabled")
        == "checked-input-update/1"
    )


def capabilities():
    return {
        "version": 1,
        "mode": os.environ.get("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "disabled"),
        "profiles": {
            p: {"enabled": profile_enabled(p), "default_metrics": list(m),
                "metric_definitions": {metric: calculations.definition(metric, p) for metric in m}}
            for p, m in calculations.PROFILES.items()
        },
        "metric_definitions": calculations.DEFINITIONS,
        "effects": {"checked-input-update/1": effects_enabled()},
        "review_mode": os.environ.get("PYPSA_GUI_ANSWER_REVIEW_MODE", "disabled"),
        "exports": ["json", "zip"],
        "retention_seconds": records.RETENTION_SECONDS,
        "limits": {
            "snapshots": calculations.MAX_SNAPSHOTS,
            "claims": 20,
            "record_bytes": 128 * 1024,
        },
        "scope": "bounded_profiles",
        "independent_domain_approval": False,
        "unsupported": [
            "multi_investment_periods",
            "store_and_link_costs",
            "storage_charging_purchases_and_revenues",
            "storage_overnight_annuity_and_separate_fom",
            "nonlinear_costs",
            "NPV",
            "arbitrary_request_completeness",
            "engineering_certification",
        ],
    }


def observe(turn, blocks):
    started = time.monotonic()
    verdict = "would_withhold"
    try:
        _, facts, evidence_ids = turn.validate(blocks)
        verdict = "deterministic_match"
    except Exception:
        facts, evidence_ids = [], []
    oid = str(uuid.uuid4())
    records.save(
        {
            "version": 1,
            "record_kind": "shadow",
            "owner_id": turn.owner,
            "project_id": turn.project_id,
            "observation_id": oid,
            "turn_id": turn.turn_id,
            "candidate_verdict": verdict,
            "checked_blocks": len(facts),
            "evidence_ids": evidence_ids,
            "validation_seconds": time.monotonic() - started,
            "expires_at": time.time() + records.RETENTION_SECONDS,
        },
        oid,
    )
    # No model draft, thinking or prompt is stored. Shadow is observation only,
    # never an approved answer or an estimate of scientific false acceptance.
