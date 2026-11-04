"""A1.6/A1.7: a partly converged AC power flow is not a checked run.

Issue 02 (P1). An AC power-flow run is sealed "completed" even when only some
snapshots converged. The checked run reads (`get_run_evidence`, a run section
and `compare_project_runs`) refuse such a run before the handler and name the
outcome, as they do for a failed run; an AC power-flow run that does not record
how many snapshots converged is refused as "not known to have converged". A fully
converged run still checks. Unchecked reads still show the run.
"""

# ruff: noqa: F811 -- imported shared fixtures.

import json

import pytest

from services import run_evidence
from tests.assurance_acceptance import test_p1_run_as_of as as_of
from tests.assurance_acceptance import test_p1_run_compare as compare
from tests.assurance_acceptance import test_p1_run_sections as sections
from tests.assurance_acceptance.support import approval


def counts(converged, total):
    return {
        "ac_pf_converged_count": {"value": converged, "unit": "snapshots"},
        "ac_pf_total_snapshots": {"value": total, "unit": "snapshots"},
    }


def publish(root, project_id, run_id, extra, started, *, kind="ac_pf"):
    run_evidence.publish_run(
        root,
        project_id,
        run_id=run_id,
        kind=kind,
        status="completed",
        started_at=started,
        finished_at=as_of.FINISHED,
        mode="ac_pf",
        fingerprint="fp-1",
        metrics={"objective": {"value": 1250.5, "unit": "EUR"}, **extra},
        sections={"summary": sections.SUMMARY},
        error=None,
    )


def publish_old(baseline, extra, **kw):
    _ctx, project_id, root = baseline
    publish(root, project_id, "run-old", extra, "2026-10-02T10:00:00+00:00", **kw)


def refused_read(harness, monkeypatch, phrase):
    calls = as_of.wrap_handler(monkeypatch)
    frames = as_of.read_run(harness)
    assert not calls
    result = as_of.run_result(frames)
    assert result["error_kind"] == "run_not_checkable"
    assert phrase in result["message"]
    assert approval(frames)["verdict"] == "limited"
    assert not approval(frames)["receipt"]["evidence_ids"]


def test_a_fully_converged_ac_pf_run_is_checked(baseline, harness):
    publish_old(baseline, counts(8, 8))
    frames = as_of.read_run(harness)
    assert approval(frames)["verdict"] == "passed"
    frames = sections.read_section(harness)
    assert approval(frames)["verdict"] == "passed"


@pytest.mark.parametrize("kind", ["ac_pf", "simulation"])
def test_a_partly_converged_run_is_refused_before_the_handler(
    baseline, harness, monkeypatch, kind
):
    publish_old(baseline, counts(5, 8), kind=kind)
    refused_read(
        harness, monkeypatch, "this run is partly converged (5 of 8 snapshots converged)"
    )


def test_a_section_of_a_partly_converged_run_is_refused(baseline, harness, monkeypatch):
    publish_old(baseline, counts(0, 8))
    args = {"run_id": "run-old", "section": "summary"}
    sections.refused(
        harness, monkeypatch, args,
        "this run is partly converged (0 of 8 snapshots converged)",
    )


@pytest.mark.parametrize("role", ["run", "baseline"])
def test_a_comparison_with_a_partly_converged_side_is_refused(
    baseline, harness, monkeypatch, role
):
    _ctx, project_id, root = baseline
    partial = "run-new" if role == "run" else "run-base"
    for run_id, started in (
        ("run-base", "2026-10-01T10:00:00+00:00"),
        ("run-new", "2026-10-02T10:00:00+00:00"),
    ):
        publish(root, project_id, run_id,
                counts(7, 8) if run_id == partial else counts(8, 8), started)
    phrase = ("the run is" if role == "run" else "the baseline run is")
    compare.refused(harness, monkeypatch, compare.ARGS, f"{phrase} partly converged (7 of 8 snapshots converged)")


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"ac_pf_total_snapshots": {"value": 8, "unit": "snapshots"}},
        {"ac_pf_converged_count": {"value": 8, "unit": "snapshots"}},
        counts(8.0, 8),
        counts(-1, 8),
        counts(0, 0),
        counts(9, 8),
        counts(8, 8) | {"ac_pf_total_snapshots": {"value": 8, "unit": "MW"}},
    ],
    ids=["none", "no_converged", "no_total", "float", "negative",
         "zero_total", "more_converged_than_total", "wrong_unit"],
)
def test_an_ac_pf_run_without_valid_counts_is_not_known_to_have_converged(
    baseline, harness, monkeypatch, extra
):
    publish_old(baseline, extra)
    refused_read(harness, monkeypatch, "this run is not known to have converged")


def test_the_checked_manifest_names_the_outcome(baseline):
    publish_old(baseline, counts(5, 8))
    assert run_evidence.checked_run_manifest("run-old")[1:] == (
        None, "partly_converged (5 of 8 snapshots converged)"
    )


@pytest.mark.parametrize("converged", [5, 8])
def test_tampered_counts_refuse_as_the_mac_failure(
    baseline, harness, monkeypatch, converged
):
    publish_old(baseline, counts(8 if converged == 5 else 5, 8))
    path = compare.manifest_path(baseline[2], "run-old")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["metrics"]["ac_pf_converged_count"]["value"] = converged
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert run_evidence.checked_run_manifest("run-old")[1:] == (None, "unverified")
    refused_read(harness, monkeypatch, "this run is unverified.")


@pytest.mark.parametrize(
    "pair", [(True, True), (True, 8), (8, True), (False, True)],
    ids=["both", "converged", "total", "false_of_true"],
)
def test_bool_counts_are_not_known_to_have_converged(pair):
    # publish_run refuses a bool metric, so a sealed run cannot carry one;
    # the gate still must not read True as the integer 1.
    record = {"kind": "ac_pf", "metrics": counts(*pair)}
    assert run_evidence._convergence(record) == "not_known_to_have_converged"
