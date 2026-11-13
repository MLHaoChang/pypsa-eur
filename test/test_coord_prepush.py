# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""File selection and FAIL paths of ``scripts/coord/prepush.sh`` (no real checks run)."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "prepush.sh"


def git(cwd, *a):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "master")
    (tmp_path / "README").write_text("x")
    for name in (
        "pypsa-gui/backend/tests/test_costs.py",
        "pypsa-gui/frontend/src/components/Kpi.test.tsx",
    ):
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "feat")
    return tmp_path


def commit(repo, files):
    for name, text in files.items():
        f = repo / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "change")


def prepush(repo, *args, env=None):
    return subprocess.run(
        ["bash", str(SCRIPT), *args, "master"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def test_plan_selects_files_by_what_changed(repo):
    commit(
        repo,
        {
            "pypsa-gui/backend/services/costs.py": "x = 1\n",
            "pypsa-gui/frontend/src/components/Kpi.tsx": "export {}\n",
            "docs/note.md": "hi\n",
        },
    )
    out = prepush(repo, "--plan").stdout
    assert "plan ruff: pypsa-gui/backend/services/costs.py" in out
    assert "docs/note.md" not in out.split("plan ruff:")[1].splitlines()[0]
    assert "plan tsc:" in out
    assert "plan mapped backend tests: tests/test_costs.py" in out
    assert "plan mapped frontend tests: src/components/Kpi.test.tsx" in out
    for fixed in (
        "test_route_chat_disposition",
        "assistant_route_parity",
        "assistant_control_matrix",
        "site3dUiContract",
        "repr guard",
    ):
        assert fixed in out


def test_plan_skips_steps_with_no_matching_change(repo):
    commit(repo, {"docs/note.md": "hi\n"})
    out = prepush(repo, "--plan").stdout
    assert "plan ruff" not in out and "plan tsc" not in out and "plan mapped" not in out
    assert "plan contract pytest" in out  # the contract set always runs


def test_a_changed_test_file_runs_itself(repo):
    commit(repo, {"pypsa-gui/backend/tests/test_costs.py": "def test_a():\n    pass\n"})
    assert (
        "plan mapped backend tests: tests/test_costs.py"
        in prepush(repo, "--plan").stdout
    )


def test_missing_backend_python_fails_and_never_passes(repo):
    commit(repo, {"docs/note.md": "hi\n"})
    r = prepush(repo, env={"PREPUSH_PYTHON": "/nonexistent/python"})
    assert r.returncode == 1
    assert "FAIL contract tests (missing: backend python" in r.stdout
    assert "PASS contract" not in r.stdout


def test_missing_node_modules_fails_tsc_and_vitest(repo):
    commit(repo, {"pypsa-gui/frontend/src/components/Kpi.tsx": "export {}\n"})
    r = prepush(repo, env={"PREPUSH_PYTHON": "/nonexistent/python"})
    assert "FAIL tsc (missing:" in r.stdout
    assert "FAIL contract: vitest (missing:" in r.stdout
    assert "FAIL mapped frontend tests (missing:" in r.stdout


def test_missing_ruff_fails(repo):
    commit(repo, {"a.py": "x = 1\n"})
    r = prepush(
        repo,
        env={
            "PREPUSH_RUFF": "/nonexistent/ruff",
            "PREPUSH_PYTHON": "/nonexistent/python",
        },
    )
    assert "FAIL ruff (missing:" in r.stdout


def test_allow_skip_prints_skip_and_passes(repo):
    commit(repo, {"docs/note.md": "hi\n"})
    r = prepush(repo, "--allow-skip", env={"PREPUSH_PYTHON": "/nonexistent/python"})
    assert "SKIP contract tests (missing:" in r.stdout
    assert "PASS contract" not in r.stdout
    assert r.returncode == 0


def test_repr_guard_failure_fails_the_run(repo):
    commit(repo, {"pypsa-gui/backend/services/svc.py": 'msg = f"bad {1!r}"\n'})
    r = prepush(repo, env={"PREPUSH_PYTHON": "/nonexistent/python"})
    assert "FAIL repr guard" in r.stdout
    assert r.returncode == 1


def _fake_tool(path, code):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nexit {code}\n")
    path.chmod(0o755)


def test_a_failing_step_in_every_subshell_fails_the_run(repo):
    """Steps run in ``( cd ... && run ... )``; their failures must reach the exit code."""
    for tool in ("tsc", "vitest"):
        _fake_tool(repo / "pypsa-gui/frontend/node_modules/.bin" / tool, 1)
    _fake_tool(repo / "fakepython", 1)
    commit(
        repo,
        {
            "pypsa-gui/frontend/src/components/Kpi.tsx": "export {}\n",
            "pypsa-gui/backend/services/costs.py": "x = 1\n",
        },
    )
    r = prepush(repo, env={"PREPUSH_PYTHON": str(repo / "fakepython")})
    for step in (
        "tsc",
        "mapped backend tests",
        "mapped frontend tests",
        "contract: pytest",
        "contract: route parity",
        "contract: control matrix",
        "contract: vitest",
    ):
        assert f"FAIL {step}" in r.stdout, (step, r.stdout)
    assert "PREPUSH: FAIL" in r.stdout and "PREPUSH: PASS" not in r.stdout
    assert r.returncode == 1


def test_every_step_passing_exits_zero(repo):
    for tool in ("tsc", "vitest"):
        _fake_tool(repo / "pypsa-gui/frontend/node_modules/.bin" / tool, 0)
    _fake_tool(repo / "fakepython", 0)
    commit(repo, {"pypsa-gui/frontend/src/components/Kpi.tsx": "export {}\n"})
    r = prepush(repo, env={"PREPUSH_PYTHON": str(repo / "fakepython")})
    assert "PREPUSH: PASS" in r.stdout and r.returncode == 0, r.stdout


def _slow_python(repo, output=""):
    """A backend python that hangs on the mapped tests only and passes the rest."""
    path = repo / "fakepython"
    path.write_text(
        f'#!/bin/sh\ncase "$*" in *test_costs*) echo "{output}"; sleep 30;; esac\nexit 0\n'
    )
    path.chmod(0o755)
    return {"PREPUSH_PYTHON": str(path), "PREPUSH_MAPPED_CAP": "1"}


def test_mapped_tests_hitting_the_cap_are_cut_off_without_failing_the_push(repo):
    for tool in ("tsc", "vitest"):
        _fake_tool(repo / "pypsa-gui/frontend/node_modules/.bin" / tool, 0)
    commit(repo, {"pypsa-gui/backend/services/costs.py": "x = 1\n"})
    r = prepush(repo, env=_slow_python(repo, "..s.  [ 40%]"))
    assert "CUT  mapped backend tests (cut off at 1s" in r.stdout, r.stdout
    assert "CI covers them" in r.stdout
    assert "FAIL" not in r.stdout
    assert "PREPUSH: PASS (0 skipped, 1 mapped step(s) cut off at 1s" in r.stdout
    assert r.returncode == 0


def test_a_test_failure_before_the_cap_still_fails(repo):
    for tool in ("tsc", "vitest"):
        _fake_tool(repo / "pypsa-gui/frontend/node_modules/.bin" / tool, 0)
    commit(repo, {"pypsa-gui/backend/services/costs.py": "x = 1\n"})
    r = prepush(repo, env=_slow_python(repo, "..F.  [ 40%]"))
    assert "FAIL mapped backend tests" in r.stdout, r.stdout
    assert "CUT" not in r.stdout
    assert r.returncode == 1


def test_contract_checks_run_before_the_mapped_tests(repo):
    commit(repo, {"pypsa-gui/backend/services/costs.py": "x = 1\n"})
    out = prepush(repo, "--plan").stdout
    assert out.index("plan contract pytest") < out.index("plan mapped backend tests")
    assert out.index("plan contract vitest") < out.index("plan mapped backend tests")


def test_a_cap_that_is_not_a_number_is_refused(repo):
    commit(repo, {"docs/note.md": "hi\n"})
    r = prepush(repo, "--plan", env={"PREPUSH_MAPPED_CAP": "10m"})
    assert r.returncode == 2 and "PREPUSH_MAPPED_CAP" in r.stderr


def test_registry_contract_runs_when_a_router_changes_and_not_otherwise(repo):
    commit(repo, {"pypsa-gui/backend/services/costs.py": "x = 1\n"})
    assert "plan contract registry" not in prepush(repo, "--plan").stdout
    commit(repo, {"pypsa-gui/backend/routers/results.py": "x = 1\n"})
    out = prepush(repo, "--plan").stdout
    assert "plan contract registry:" in out
    assert "test_the_endpoint_list_covers_every_series_endpoint" in out
    assert "plan contract registry" in out and out.index("registry") < out.index(
        "plan mapped"
    )
    commit(repo, {"pypsa-gui/backend/harness/catalogue.py": "x = 1\n"})
    assert "plan contract registry:" in prepush(repo, "--plan").stdout


def test_registry_contract_is_not_capped_and_its_failure_fails(repo):
    commit(repo, {"pypsa-gui/backend/routers/results.py": "x = 1\n"})
    py = repo / "fakepython"
    py.write_text(
        '#!/bin/sh\ncase "$*" in *test_assistant_inventory*) sleep 3; echo F; exit 1;; esac\nexit 0\n'
    )
    py.chmod(0o755)
    r = prepush(repo, env={"PREPUSH_PYTHON": str(py), "PREPUSH_MAPPED_CAP": "1"})
    assert "FAIL contract: registry" in r.stdout and "CUT contract" not in r.stdout, (
        r.stdout
    )


def test_a_cut_line_names_the_test_files_it_skipped(repo):
    for tool in ("tsc", "vitest"):
        _fake_tool(repo / "pypsa-gui/frontend/node_modules/.bin" / tool, 0)
    commit(repo, {"pypsa-gui/backend/services/costs.py": "x = 1\n"})
    r = prepush(repo, env=_slow_python(repo, "..s.  [ 40%]"))
    assert "not finished: tests/test_costs.py" in r.stdout, r.stdout
