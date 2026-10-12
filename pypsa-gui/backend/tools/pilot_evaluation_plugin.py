"""Private pytest report adapter used only by the standalone pilot gate."""
import json
import os
from pathlib import Path

import pytest

from harness.pilot_evaluation import report


class EvaluationReport:
    def __init__(self):
        self.session = None
        self.meter = None

    def save(self, failed=False):
        results = []
        for item in self.session.items:
            case = getattr(getattr(item, "callspec", None), "params", {}).get("case")
            if case is None:
                continue
            mode = "live" if item.originalname == "test_live_pilot_case" else "deterministic"
            result = dict(getattr(item, "pilot_result", {"case_id": case.id, "profile": case.profile,
                "fault": case.fault, "mode": mode, "scored": False, "failures": ["observation_missing"]}))
            result["execution"] = getattr(item, "pilot_execution", "not_run")
            results.append(result)
        value = report(results, live=os.environ.get("PYPSA_GUI_PILOT_EVALUATION_LIVE") == "1", execution_failed=failed)
        value["source"] = json.loads(os.environ["PYPSA_GUI_PILOT_EVALUATION_SOURCE"])
        value["policy_version"] = "1"
        value["model"] = os.environ.get("PYPSA_GUI_PILOT_EVALUATION_MODEL") if value["live_requested"] else None
        value["live_budget"] = self.meter.snapshot() if self.meter else None
        path = Path(os.environ["PYPSA_GUI_PILOT_EVALUATION_REPORT"])
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
        return value

    def pytest_collection_finish(self, session):
        self.session = session
        self.save()

    def pytest_runtest_call(self, item):
        if "live_meter" in item.funcargs:
            self.meter = item.funcargs["live_meter"]
            self.meter.on_change = self.save

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        result = (yield).get_result()
        previous = getattr(item, "pilot_execution", "not_run")
        if result.failed or result.skipped:
            item.pilot_execution = result.outcome
        elif result.when == "call" and previous == "not_run":
            item.pilot_execution = "passed"
        self.save()

    def pytest_sessionfinish(self, session, exitstatus):
        self.session = session
        if not self.save(failed=bool(exitstatus))["passed"]:
            session.exitstatus = 1


def pytest_configure(config):
    config.pluginmanager.register(EvaluationReport(), "pilot-evaluation-report")
