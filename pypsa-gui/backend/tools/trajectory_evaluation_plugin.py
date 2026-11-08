"""Private pytest report adapter for the standalone trajectory suite
(`tools/run_trajectory_evaluation.py`): it writes the suite report from the
scored cases of `tests/test_trajectory_evaluation.py::test_trajectory_case`.
A case that never ran or whose driver raised is a failed case, never a skip."""

import json
import os
from pathlib import Path

import pytest

from harness import trajectory_evaluation as te


class TrajectoryReport:
    def __init__(self):
        self.items = []

    def save(self, failed=False):
        results = []
        for item in self.items:
            case = getattr(getattr(item, "callspec", None), "params", {}).get("case")
            if case is None:
                continue
            result = getattr(item, "trajectory_result", None)
            outcome = getattr(item, "trajectory_outcome", "not_run")
            if result is None:
                result = te.failed(
                    case, outcome if outcome != "passed" else "observation_missing"
                )
            elif outcome != "passed" and "execution_failed" not in result["failures"]:
                result = dict(
                    result, failures=[*result["failures"], "execution_" + outcome]
                )
            results.append(dict(result, execution=outcome))
        value = te.report(results)
        if failed:
            value["passed"] = False
        value["source"] = json.loads(
            os.environ.get("PYPSA_GUI_TRAJECTORY_SOURCE") or "{}"
        )
        path = Path(os.environ["PYPSA_GUI_TRAJECTORY_REPORT"])
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, indent=2, allow_nan=False, default=str) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return value

    def pytest_collection_finish(self, session):
        self.items = list(session.items)
        self.save(failed=True)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        result = (yield).get_result()
        previous = getattr(item, "trajectory_outcome", "not_run")
        if result.failed or result.skipped:
            item.trajectory_outcome = result.outcome
        elif result.when == "call" and previous == "not_run":
            item.trajectory_outcome = "passed"

    def pytest_sessionfinish(self, session, exitstatus):
        if not self.save(failed=bool(exitstatus))["passed"]:
            session.exitstatus = 1


def pytest_configure(config):
    config.pluginmanager.register(TrajectoryReport(), "trajectory-evaluation-report")
