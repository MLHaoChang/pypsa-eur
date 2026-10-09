"""Pytest plugin: record real tool-handler calls exercised by passing tests.

Usage: pytest -p tests.tool_coverage_capture ...
This records offline handler coverage, not proof that external services ran.
"""
import functools
import inspect
import json
import os
from pathlib import Path

import pytest

_current = []
_coverage = {}


def pytest_sessionstart(session):
    from harness.catalogue import TOOLS
    from services import chat_tools
    declarations = {t["name"]: t for t in TOOLS}
    for name, original in list(chat_tools.DISPATCHERS.items()):
        signature = inspect.signature(original)
        properties = declarations[name]["input_schema"].get("properties", {})
        def decorate(name, original, signature, properties):
            @functools.wraps(original)
            def capture(*args, **kwargs):
                try:
                    result = original(*args, **kwargs)
                except Exception:
                    _current.append((name, "error", None))
                    raise
                bound = signature.bind(*args, **kwargs).arguments
                values = {}
                for key, value in bound.items():
                    if key in properties:
                        values[key] = value
                    elif signature.parameters[key].kind == inspect.Parameter.VAR_KEYWORD:
                        values.update({k: v for k, v in value.items() if k in properties})
                try:
                    encoded = json.dumps(values, ensure_ascii=False, allow_nan=False)
                    sample = json.loads(encoded) if len(encoded) <= 12000 else None
                except (TypeError, ValueError):
                    sample = None
                _current.append((name, "success", sample))
                return result
            return capture
        wrapped = decorate(name, original, signature, properties)
        chat_tools.DISPATCHERS[name] = wrapped
        if getattr(chat_tools, name, None) is original:
            setattr(chat_tools, name, wrapped)


def pytest_runtest_setup(item):
    _current.clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.passed:
        return
    for name, status, sample in _current:
        entry = _coverage.setdefault(name, {"success_tests": [], "error_tests": [], "samples": []})
        tests = entry[status + "_tests"]
        if item.nodeid not in tests:
            tests.append(item.nodeid)
        if sample is not None and sample not in entry["samples"] and len(entry["samples"]) < 3:
            entry["samples"].append(sample)


def pytest_sessionfinish(session, exitstatus):
    from harness.catalogue import TOOLS
    output = {"registered_tools": len(TOOLS), "tools": _coverage,
              "missing_success": [t["name"] for t in TOOLS
                                  if not _coverage.get(t["name"], {}).get("success_tests")]}
    Path(os.environ.get("TOOL_COVERAGE_REPORT", "/tmp/tool-handler-coverage.json")).write_text(
        json.dumps(output, ensure_ascii=False, indent=2))
