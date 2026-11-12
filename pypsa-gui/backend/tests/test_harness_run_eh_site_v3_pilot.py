# ruff: noqa: F811  (pytest fixtures imported from sibling test modules)
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Study harness issue 33 (L7): the eh_site@3 pilots, offline. The map
services are an `httpx.MockTransport` (`tests/test_harness_run_eh_site_v3.py`)."""

from __future__ import annotations

import uuid

import pytest

from harness import run_site
from services import run_actions, run_envelope, run_handover, site_context
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline,
    dispatch,
    edit,
    helpers_state,
    session,
    wait_until,
)
from tests.test_harness_envelope import granted, on_run, profile  # noqa: F401
from tests.test_harness_run_eh_site_v2 import _tick_to, goal_host  # noqa: F401
from tests.test_harness_run_eh_site_v3 import SITE, _start, offline, overpass, site_host  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import site  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401
from tests.test_run_intake_grant import _new_task


# ── The offline smoke (L7): an eh_site@3 pilot that names its site ───────────


def _pilot(site_host, session, site, citations, monkeypatch):
    from tests.test_harness_run_report_handoff import runner as run_runner
    from tests.test_harness_runner import Clicks, mail

    mail(monkeypatch)
    name = f"Pilot {uuid.uuid4().hex[:6]}"
    view = _start(citations, name)
    run_envelope.decide(
        session,
        view["task_id"],
        "unattended",
        intake_digest=view["run"]["intake"]["digest"],
    )
    intent = run_handover.read_intent(task_path(site[1], view))
    path, task = _new_task(intent)
    record = _tick_to(
        run_runner(Clicks(refs={"solve.2"})),
        path,
        task["task_id"],
        lambda r: r["run"]["status"] in ("completed", "needs_user", "failed"),
    )
    assert record["run"]["status"] == "completed", record["run"]
    fetch = next(s for s in record["steps"] if s["tool"] == run_actions.SITE_FETCH)
    assert fetch["status"] == "completed"
    rules = {
        r["rule"]: r for r in record["run"]["stages"]["input_gate"]["gate"]["rules"]
    }
    return path, record, fetch, rules["eh.site.context"]


def _saved_gaps(record, path):
    from harness import run_report

    doc = run_report.saved(record, path.parent.parent)
    return [row[0] for row in doc.tables["harness_gaps"].rows]


def test_pilot_v3_fetches_the_named_site_offline_and_the_rule_passes(
    site_host, session, site, fake_report, offline, monkeypatch
):
    """Issue 33 acceptance (L7): the eh_site@2 pilot on @3, with the site
    named in the intake. The inputs stage fetches it from the fixture (no
    network), the input gate's `eh.site.context` passes on it, and the run
    goes on to the hand-off with no site gap in its report."""
    path, record, fetch, rule = _pilot(
        site_host,
        session,
        site,
        [*site_host["citations"], site_host["goal"], *site_host["site"]],
        monkeypatch,
    )
    assert fetch["result"]["state"] == "fetched" and fetch["frozen"]["site"] == SITE
    assert [c for c in offline if "interpreter" in c] == [
        site_context.DEFAULT_OVERPASS_URL
    ]
    assert rule["verdict"] == "pass"
    assert run_site.context_path(path).is_file()
    assert not [g for g in _saved_gaps(record, path) if g.startswith("Site context")]


@pytest.mark.parametrize("named", [True, False])
def test_pilot_v3_lists_a_site_it_could_not_fetch_or_was_not_given_in_the_saved_report(
    named, site_host, session, site, fake_report, monkeypatch
):
    """GK B (49) item 4: an unavailable fetch and a site not named each read
    not_evaluated (warn: the run still completes) and the saved report's
    harness_gaps table, which `report.unsupported_listed` reads, names it."""
    calls = []
    monkeypatch.setattr(run_site, "_TRANSPORT", overpass(calls, status=502))
    citations = [
        *site_host["citations"],
        site_host["goal"],
        *(site_host["site"] if named else []),
    ]
    path, record, fetch, rule = _pilot(site_host, session, site, citations, monkeypatch)
    assert fetch["result"]["state"] == ("unavailable" if named else "not_named")
    assert bool(calls) is named
    assert rule["verdict"] == "not_evaluated"
    assert (
        ("HTTP 502" in rule["reason"])
        if named
        else rule["reason"] == run_site.NOT_NAMED
    )
    assert f"Site context not established: {rule['reason']}" in _saved_gaps(
        record, path
    )
