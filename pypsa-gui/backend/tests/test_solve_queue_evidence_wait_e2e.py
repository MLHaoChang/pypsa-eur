"""End to end: evidence is readable the moment `wait_for_job` sees terminal.

The #648 flake: `refresh_experiment` right after `wait_for_job` read
`experiment_evidence_unavailable`, because the queue flipped the status
before writing the evidence. A slow evidence write widens that window
deterministically; with the evidence written first it cannot open.
"""
# ruff: noqa: F811 -- imported pytest fixtures
import time

from services import chat_tools, run_evidence
from tests.test_experiment_case_advancement import arm, invoke, prepared, tool
from tests.test_workflow_tools import baseline  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_chat_tool_executor_starvation import saturated_pool  # noqa: F401


def test_evidence_is_available_when_wait_for_job_returns(baseline, monkeypatch):
    real = run_evidence.publish_queue_run

    def slow(job, *args, **kwargs):
        time.sleep(1.0)
        return real(job, *args, **kwargs)

    monkeypatch.setattr(run_evidence, "publish_queue_run", slow)
    eid = prepared()
    session, state = arm(eid)
    child = invoke(session, state["next_call"])["cases"][0]
    assert chat_tools.wait_for_job(child["job"]["id"], 25)["completed"]
    complete = tool("refresh_experiment", experiment_id=eid)
    assert complete["result_available"], complete["last_error"]
