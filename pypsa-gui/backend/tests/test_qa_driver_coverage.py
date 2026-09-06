"""
The `qa_*.py` drivers are actually run by something, and by the right something.

`pytest.ini` sets `python_files = test_*.py`, so pytest does not collect the
drivers — deliberately; they are PASS/FAIL scripts, not pytest functions. The
cost of that decision, until `tests/run_qa_drivers.py` existed, was that NOTHING
ran them. Five of nineteen had been broken since the auth/tenancy migration and
several of the rest were exiting zero while testing almost nothing
(`docs/superpowers/findings/2026-09-05-qa-driver-rot.md`).

This module is the guard on the arrangement that replaced that. It is a normal
collected test, so it runs in the `gui-backend-tests` CI job, and it fails if:

  * the runner's skip list goes stale, or
  * the pixi task that invokes the runner moves or disappears, or
  * the CI step that invokes the pixi task disappears.

Any one of those silently shrinks what CI covers back towards zero, which is
exactly how the original rot survived for months.

These are static checks — reading the runner's exclusion map, `pixi.toml` and
the workflow file. They do not RUN the drivers; that takes ~2 minutes and is
the CI step's job.
"""
from __future__ import annotations

import pathlib
import tomllib

import pytest

_TESTS = pathlib.Path(__file__).resolve().parent
_REPO = _TESTS.parent.parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "test.yaml"
_PIXI = _REPO / "pixi.toml"

_TASK = "gui-qa-drivers"


def _excluded() -> dict[str, str]:
    """The runner's skip map, read without importing the whole module."""
    from tests.run_qa_drivers import _EXCLUDED

    return _EXCLUDED


def test_every_driver_is_either_run_or_explicitly_excluded():
    """
    The runner discovers `qa_*.py` by glob, so a NEW driver is picked up for
    free. What needs guarding is the other direction: an entry in `_EXCLUDED`
    naming a file that no longer exists means someone renamed or deleted a
    driver and the skip list did not follow — and the next file to take that
    name would be skipped without anyone deciding to skip it.
    """
    stale = sorted(name for name in _excluded() if not (_TESTS / name).is_file())
    assert not stale, (
        f"run_qa_drivers._EXCLUDED names files that do not exist: {stale}. "
        f"A stale exclusion silently shrinks the set CI covers."
    )


def test_every_exclusion_carries_a_reason():
    """
    A skip with no stated reason is indistinguishable from an accident. This is
    the same standard `pixi.toml` already applies to the desktop tests — "a
    skipped test reads as a green suite, which is how a hole this size stays
    open".
    """
    thin = sorted(name for name, why in _excluded().items() if len(why.strip()) < 20)
    assert not thin, f"these exclusions need a real reason, not a placeholder: {thin}"


def test_the_runner_is_not_collected_by_pytest():
    """
    `run_qa_drivers.py` spawns eighteen subprocesses that each solve networks.
    If it were ever renamed to `test_*.py` it would be collected AND still run
    by the CI step, doubling a two-minute cost and interleaving its output with
    the suite's.
    """
    import configparser

    parser = configparser.ConfigParser()
    parser.read(_TESTS.parent / "pytest.ini")
    # Read the SETTING, not the file text. pytest.ini names `python_files =
    # test_*.py` in a comment as well as in the setting, so a substring search
    # passes on the comment alone — i.e. it would still pass after someone
    # changed the setting, which is the one thing it is here to catch.
    patterns = parser.get("pytest", "python_files", fallback="").split()
    assert patterns == ["test_*.py"], (
        f"pytest.ini collects {patterns} — it no longer restricts collection to "
        f"test_*.py alone, so the qa_*.py drivers would now be collected AS WELL "
        f"AS run by the CI step. That is a deliberate reversal to make, not a "
        f"drive-by; see docs/superpowers/findings/2026-09-05-qa-driver-rot.md."
    )
    assert not _TESTS.joinpath("test_run_qa_drivers.py").exists()


def test_the_pixi_task_exists_in_the_test_feature():
    """
    Same placement reasoning as `gui-tests`: in the root `[tasks]` table the
    task resolves to the `default` environment, which lacks the dependencies
    the drivers need.
    """
    manifest = tomllib.loads(_PIXI.read_text())
    root_tasks = manifest.get("tasks", {})
    test_tasks = manifest.get("feature", {}).get("test", {}).get("tasks", {})

    assert _TASK not in root_tasks, (
        f"`{_TASK}` is in the root [tasks] table, so it resolves to the "
        f"`default` environment rather than `test`."
    )
    assert _TASK in test_tasks, f"`{_TASK}` is not defined under [feature.test.tasks]"

    task = test_tasks[_TASK]
    assert task.get("cwd") == "pypsa-gui/backend", (
        f"`{_TASK}` must run from pypsa-gui/backend; got cwd={task.get('cwd')!r}"
    )
    assert "run_qa_drivers.py" in task.get("cmd", ""), (
        f"`{_TASK}` no longer invokes the runner; got {task.get('cmd')!r}"
    )


def test_ci_runs_the_qa_drivers():
    """
    The pixi task existing is not the same as CI calling it. This asserts the
    `gui-backend-tests` job does — the whole point of the arrangement is that
    nobody has to remember.

    Matched as text rather than parsed as YAML on purpose: PyYAML is not a
    declared dependency of the `test` environment, and a test that skips when
    its parser is absent is the failure mode this file exists to prevent.
    """
    if not _WORKFLOW.is_file():
        pytest.fail(f"{_WORKFLOW} is missing — the backend CI job cannot be checked")

    text = _WORKFLOW.read_text()
    job = text.split("gui-backend-tests:", 1)
    assert len(job) == 2, "the `gui-backend-tests` job is gone from test.yaml"
    # Up to the next top-level job key (two-space indent at column 0).
    body = job[1].split("\n  integration-tests:", 1)[0]

    assert f"pixi run {_TASK}" in body, (
        f"the `gui-backend-tests` job no longer runs `pixi run {_TASK}`, so the "
        f"qa_*.py drivers are back to being run by nobody."
    )
    assert "pixi run gui-tests" in body, (
        "the `gui-backend-tests` job no longer runs the pytest suite either"
    )
