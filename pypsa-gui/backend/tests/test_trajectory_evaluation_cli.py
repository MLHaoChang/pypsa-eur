# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Issue 07b: the offline trajectory CLI, with its child pytest process faked."""

import json
import subprocess

from tools import run_trajectory_evaluation as cli


class Child:
    """A stand-in child process: records how it was started, optionally hangs."""

    started = []

    def __init__(self, command, cwd, env, stdout, stderr, start_new_session):
        Child.started.append({"command": command, "env": env})
        self.pid, self.hang = 0, Child.hang

    def wait(self, timeout=None):
        if self.hang and timeout is not None:
            self.hang = False
            raise subprocess.TimeoutExpired("pytest", timeout)
        return 0

    def kill(self):
        pass


def _run(tmp_path, monkeypatch, *args, hang=False):
    Child.started, Child.hang = [], hang
    monkeypatch.setattr(cli, "source_identity", lambda: {"commit": "test"})
    monkeypatch.setattr(cli.subprocess, "Popen", Child)
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k pilot")
    out = tmp_path / "trajectory.json"
    code = cli.main(["--report", str(out), *args])
    return code, json.loads(out.read_text()), Child.started[0]


def test_the_cli_runs_every_case_offline_and_a_report_the_child_never_wrote_fails(
    tmp_path, monkeypatch
):
    code, value, child = _run(tmp_path, monkeypatch)
    assert code == 1 and value["passed"] is False  # the pre-written report stays failed
    assert value["missing_cases"] and value["required_cases"] == 8
    command = child["command"]
    assert (
        command[-1] == cli.TARGET and "smoke.accept_harness_pilot" in command
    )  # the socket guard
    assert (
        "ANTHROPIC_API_KEY" not in child["env"] and "PYTEST_ADDOPTS" not in child["env"]
    )


def test_the_hang_guard_is_off_by_default_and_a_killed_run_fails(tmp_path, monkeypatch):
    code, value, _ = _run(tmp_path, monkeypatch, hang=True)
    assert (
        value["hung_and_killed"] is False
    )  # no timeout given: wait(None) never times out
    code, value, _ = _run(tmp_path, monkeypatch, "--timeout", "5", hang=True)
    assert code == 1 and value["hung_and_killed"] is True and value["passed"] is False
