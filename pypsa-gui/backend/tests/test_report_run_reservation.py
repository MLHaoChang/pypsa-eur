# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness issue 07, GK B C4: each LLM send of a run's report job is
reserved through `send_reservation.admit` under the run's lease before the
provider is called. A refusal makes no provider call and stops the job."""

# ruff: noqa: F811 -- imported pytest fixtures
import json

import pytest

from harness import send_reservation
from services.llm_fake import FakeProvider
from services.reports import network_chapters as NC
from services.reports.evidence import collect_evidence
from services.reports.report_job import prepare_report_job, run_report_job
from tests.test_harness_budget_reservation import lease, ledger, mode, run, set_limit  # noqa: F401
from tests.test_assistant_tasks_study_brief import baseline, helpers_state, session  # noqa: F401
from tests.test_harness_run_skeleton import site  # noqa: F401
from tests.test_harness_runner import detached  # noqa: F401
from tests.test_report_job import _PROFILE, _draft
from tests.test_report_network_chapters import _sources


class Counting(FakeProvider):
    def __init__(self, turns):
        super().__init__(turns)
        self.calls = 0

    def stream(self, request):
        self.calls += 1
        yield from super().stream(request)


def _job(tmp_path, provider, path, boot="boot-a"):
    ev = collect_evidence(
        study_report=None, eh_report=None, worksheet=None, network=_sources()
    )
    return prepare_report_job(
        project_name="demo",
        project_dir=tmp_path,
        evidence=ev,
        profile=_PROFILE,
        provider=provider,
        language="en",
        sections=[NC.HEADLINE, NC.CAPACITY],
        title="Run report",
        figure_pngs={},
        strict=True,
        run_binding=(path, boot),
    )


def _drafts():
    return [
        _draft(NC.HEADLINE, "The total cost is 120 EUR."),
        _draft(NC.CAPACITY, "Capacity is listed."),
    ]


def test_each_send_is_reserved_and_settled_under_the_runs_lease(tmp_path, run, mode):
    mode("output_tokens")
    provider = Counting(_drafts())
    job = _job(tmp_path, provider, run)
    run_report_job(job)
    assert provider.calls == 2 and "refused" not in job.record
    rows = ledger(run)
    assert len(rows["final_charges"]) == 2 and not send_reservation.has_open_holds(
        {"budgets": rows}
    )


@pytest.mark.parametrize(
    "case, reason",
    [("over_cap", "budget"), ("foreign_lease", "lease_lost"), ("no_cap", "no_cap")],
)
def test_a_refused_send_makes_no_provider_call_and_stops_the_job(
    tmp_path, run, mode, case, reason
):
    mode("total_tokens" if case == "no_cap" else "output_tokens")
    if case == "over_cap":
        set_limit(run, output_tokens=1)
    if case == "foreign_lease":
        lease(run, "boot-b")
    provider = Counting(_drafts())
    job = _job(tmp_path, provider, run)
    doc = run_report_job(job)
    assert provider.calls == 0
    assert job.record["refused"] == reason
    for s in doc.sections:
        if s.section_id in (NC.HEADLINE, NC.CAPACITY):
            assert not [b for b in s.blocks if b.type == "paragraph"]
            assert "send refused" in (s.note or "") or reason in (s.note or "")
    stored = json.loads(run.read_text())["run"]
    if reason == "lease_lost":
        assert (
            stored.get("reason") != "lease_lost"
        )  # a lapsed process writes nothing, not even a stop
    else:
        assert (stored["status"], stored["reason"]) == ("needs_user", reason)


def test_without_a_run_binding_the_provider_is_called_as_before(tmp_path, mode):
    mode("output_tokens")
    provider = Counting(_drafts())
    ev = collect_evidence(
        study_report=None, eh_report=None, worksheet=None, network=_sources()
    )
    job = prepare_report_job(
        project_name="demo",
        project_dir=tmp_path,
        evidence=ev,
        profile=_PROFILE,
        provider=provider,
        language="en",
        sections=[NC.HEADLINE, NC.CAPACITY],
        title="Panel report",
        figure_pngs={},
    )
    run_report_job(job)
    assert provider.calls == 2 and job.provider is provider


class LeaseLostAfterFirst(Counting):
    """The first send answers; then another instance takes the run's lease."""

    def __init__(self, turns, run):
        super().__init__(turns)
        self.run = run

    def stream(self, request):
        yield from super().stream(request)
        lease(self.run, "boot-b")


class Raising(Counting):
    def stream(self, request):
        self.calls += 1
        raise RuntimeError("provider down")
        yield  # pragma: no cover


def test_a_job_refused_mid_way_leaves_no_open_hold(tmp_path, run, mode):
    mode("output_tokens")
    provider = LeaseLostAfterFirst(_drafts(), run)
    job = _job(tmp_path, provider, run)
    run_report_job(job)
    assert provider.calls == 1 and job.record["refused"] == "lease_lost"
    rows = ledger(run)
    assert len(rows["final_charges"]) == 1 and not send_reservation.has_open_holds(
        {"budgets": rows}
    )


def test_a_provider_that_raises_leaves_no_open_hold(tmp_path, run, mode):
    mode("output_tokens")
    provider = Raising([])
    job = _job(tmp_path, provider, run)
    with pytest.raises(RuntimeError, match="provider down"):
        run_report_job(job)
    assert provider.calls >= 1 and job.record["status"] == "failed"
    rows = ledger(run)
    assert not send_reservation.has_open_holds({"budgets": rows})
    # the hold settled at its bound, flagged uncertain (the runner's turn-end rule)
    assert rows["final_charges"] and all(
        "uncertain" in (c.get("flags") or []) for c in rows["final_charges"]
    )
