# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Overlap, conflict, semantic-test and check-run gating of ``scripts/coord/premerge.sh``."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "premerge.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash and git",
)


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo, files, msg):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """A remote with master and a PR branch forked from it, plus a clone."""
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-q", "-b", "master")
    git(remote, "config", "user.email", "t@example.com")
    git(remote, "config", "user.name", "t")
    commit(
        remote,
        {"a.py": "a\n", "b.py": "b\n", "docs/superpowers/OPEN-ITEMS.md": "x\n"},
        "init",
    )
    git(remote, "branch", "pr")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(remote), str(clone))
    return remote, clone


def publish_pr(remote, files, msg="pr"):
    git(remote, "checkout", "-q", "pr")
    sha = commit(remote, files, msg)
    git(remote, "update-ref", "refs/pull/7/head", sha)
    git(remote, "checkout", "-q", "master")
    return sha


GREEN = [("completed", "success", "Unit"), ("completed", "skipped", "Docs")]


def run(clone, runs=GREEN, gh_exit=0, with_gh=True, comments=None, **env):
    """Run premerge.sh; a fake gh reports `runs`, or `comments` (bodies) for the PR comments call."""
    bin_dir = clone.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    if with_gh:
        gh = bin_dir / "gh"
        rows = "".join("\t".join(r) + "\n" for r in runs)
        notes = (
            None
            if comments is None
            else "".join(
                json.dumps({"login": "x", "body": body}) + "\n" for body in comments
            )
        )
        gh.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            f"notes = {notes!r}\n"
            "comments = notes is not None and any(a.endswith('/comments') for a in sys.argv)\n"
            f"sys.stdout.write(notes if comments else {rows!r})\n"
            f"sys.exit({gh_exit})\n"
        )
        gh.chmod(0o755)
        path = f"{bin_dir}:{os.environ['PATH']}"
    else:
        # Only the tools the script needs, so a real gh cannot leak in.
        for tool in ("git", "bash", "awk", "grep", "sed", "comm", "sort", "mktemp"):
            (bin_dir / tool).symlink_to(shutil.which(tool))
        path = str(bin_dir)
    return subprocess.run(
        ["bash", str(SCRIPT), "7"],
        cwd=clone,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path, **env},
    )


def test_clean_no_overlap(repos):
    remote, clone = repos
    commit(remote, {"b.py": "b2\n"}, "master moves")
    head = publish_pr(remote, {"a.py": "a2\n"})
    res = run(clone)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"head:       {head}" in res.stdout
    assert "behind:     1 merge(s)" in res.stdout
    assert "RESULT: ok to merge" in res.stdout


def test_docs_only_overlap_passes(repos):
    remote, clone = repos
    commit(remote, {"docs/superpowers/OPEN-ITEMS.md": "x\ny\n"}, "master docs")
    publish_pr(remote, {"docs/superpowers/OPEN-ITEMS.md": "w\nx\n"})
    res = run(clone)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "docs-only  docs/superpowers/OPEN-ITEMS.md" in res.stdout


def test_code_overlap_blocks_even_when_clean(repos):
    remote, clone = repos
    commit(remote, {"a.py": "a\nmaster\n"}, "master code")
    publish_pr(remote, {"a.py": "pr\na\n"})
    res = run(clone)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "clean" in res.stdout
    assert "code       a.py" in res.stdout


BACKEND_TEST = "pypsa-gui/backend/tests/test_calc.py"


def overlapping_module(repos, tests=True):
    remote, clone = repos
    base = {"pypsa-gui/backend/services/calc.py": "x = 1\ny = 2\nz = 3\n"}
    if tests:
        base[BACKEND_TEST] = "def test_calc():\n    pass\n"
    git(remote, "checkout", "-q", "master")
    commit(remote, base, "add calc")
    git(remote, "branch", "-f", "pr", "master")
    git(clone, "pull", "-q")
    commit(
        remote,
        {"pypsa-gui/backend/services/calc.py": "x = 0\ny = 2\nz = 3\n"},
        "master edit",
    )
    publish_pr(remote, {"pypsa-gui/backend/services/calc.py": "x = 1\ny = 2\nz = 4\n"})
    return clone


def test_tested_code_overlap_is_waived(repos, tmp_path):
    clone = overlapping_module(repos)
    res = run(clone, PREMERGE_PYTHON=fake_python(tmp_path, 0))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "waived      code overlap (1 file(s))" in res.stdout
    assert "tests/test_calc.py" in res.stdout.split("waived      code overlap")[1]


def test_failing_overlap_test_blocks(repos, tmp_path):
    clone = overlapping_module(repos)
    res = run(clone, PREMERGE_PYTHON=fake_python(tmp_path, 1))
    assert res.returncode == 1, res.stdout + res.stderr
    assert "waived" not in res.stdout
    assert "RESULT: blocked (semantic tests failed; overlap: 1 code file)" in res.stdout


def test_overlap_without_a_test_file_blocks_and_says_why(repos, tmp_path):
    clone = overlapping_module(repos, tests=False)
    res = run(clone, PREMERGE_PYTHON=fake_python(tmp_path, 0))
    assert res.returncode == 1, res.stdout + res.stderr
    assert "no test     pypsa-gui/backend/services/calc.py" in res.stdout
    assert "overlap: 1 code file" in res.stdout


def test_frontend_overlap_runs_vitest(repos, tmp_path):
    remote, clone = repos
    src = "pypsa-gui/frontend/src/components/Card"
    commit(
        remote,
        {
            f"{src}.tsx": "a\nb\nc\n",
            f"{src}.test.tsx": "t\n",
            "pypsa-gui/backend/keep": "",
        },
        "add card",
    )
    git(remote, "branch", "-f", "pr", "master")
    git(clone, "pull", "-q")
    commit(remote, {f"{src}.tsx": "A\nb\nc\n"}, "master edit")
    publish_pr(remote, {f"{src}.tsx": "a\nb\nC\n"})
    vitest = tmp_path / "fakevitest"
    vitest.write_text('#!/bin/sh\necho vitest "$@"\nexit 0\n')
    vitest.chmod(0o755)
    res = run(
        clone, PREMERGE_PYTHON=fake_python(tmp_path, 0), PREMERGE_VITEST=str(vitest)
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "vitest src/components/Card.test.tsx" in res.stdout
    assert "waived      code overlap" in res.stdout


def test_identical_overlap_passes(repos):
    """A cherry-pick of a change master already has leaves byte-identical files."""
    remote, clone = repos
    commit(remote, {"a.py": "a\nfix\n", "b.py": "b2\n"}, "master fix")
    publish_pr(remote, {"a.py": "a\nfix\n"}, "cherry-pick of the fix")
    res = run(clone)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "identical  a.py" in res.stdout


def test_conflict_blocks(repos):
    remote, clone = repos
    commit(remote, {"b.py": "master\n"}, "master code")
    publish_pr(remote, {"b.py": "pr\n"})
    res = run(clone)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "CONFLICT:\n  b.py" in res.stdout
    assert "RESULT: blocked" in res.stdout


def fake_python(tmp_path, code):
    py = tmp_path / "fakepy"
    py.write_text(f'#!/bin/sh\necho ran "$@"\nexit {code}\n')
    py.chmod(0o755)
    return str(py)


@pytest.mark.parametrize(("code", "verdict"), [(0, "pass"), (1, "FAIL")])
def test_route_change_runs_semantic_tests(repos, tmp_path, code, verdict):
    remote, clone = repos
    publish_pr(remote, {"pypsa-gui/backend/routers/x.py": "route\n"})
    res = run(clone, PREMERGE_PYTHON=fake_python(tmp_path, code))
    assert f"semantic tests: {verdict}" in res.stdout, res.stdout + res.stderr
    assert "test_chat_tools_argshape.py" in res.stdout
    assert res.returncode == code
    assert git(clone, "worktree", "list").count("\n") == 0


def test_unrelated_change_skips_semantic_tests(repos, tmp_path):
    remote, clone = repos
    publish_pr(remote, {"a.py": "a2\n"})
    res = run(clone, PREMERGE_PYTHON=fake_python(tmp_path, 1))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "not needed" in res.stdout


@pytest.mark.parametrize(
    "path",
    [
        "pypsa-gui/backend/services/project_acl.py",
        "pypsa-gui/backend/harness/mcp_server.py",
        "pypsa-gui/backend/routers/mcp.py",
    ],
)
def test_acl_and_mcp_changes_run_all_six_semantic_tests(repos, tmp_path, path):
    remote, clone = repos
    publish_pr(remote, {path: "x\n"})
    res = run(clone, PREMERGE_PYTHON=fake_python(tmp_path, 0))
    assert res.returncode == 0, res.stdout + res.stderr
    for test in (
        "test_project_acl.py",
        "test_project_route_acl_policy.py",
        "test_harness_mcp_server.py",
    ):
        assert test in res.stdout
    assert "assistant inventory" not in res.stdout


def fake_vitest(tmp_path, code):
    vitest = tmp_path / "fakevitest"
    vitest.write_text(f'#!/bin/sh\necho vitest "$@"\nexit {code}\n')
    vitest.chmod(0o755)
    return str(vitest)


def test_catalogue_change_checks_the_inventory_and_the_tool_list_vitests(
    repos, tmp_path
):
    remote, clone = repos
    publish_pr(
        remote,
        {
            "pypsa-gui/backend/harness/catalogue.py": "x\n",
            "pypsa-gui/frontend/keep": "",
        },
    )
    res = run(
        clone,
        PREMERGE_PYTHON=fake_python(tmp_path, 0),
        PREMERGE_VITEST=fake_vitest(tmp_path, 0),
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "smoke/assistant_inventory.py --check" in res.stdout
    assert "inventory: pass" in res.stdout
    assert (
        "vitest src/components/ChatPanel.test.tsx src/components/ChatPanel.sendRequest.test.tsx"
        in res.stdout
    )
    assert "frontend tool-list tests: pass" in res.stdout


def test_a_failing_tool_list_vitest_blocks(repos, tmp_path):
    remote, clone = repos
    publish_pr(
        remote,
        {
            "pypsa-gui/backend/services/chat_tools.py": "x\n",
            "pypsa-gui/frontend/keep": "",
        },
    )
    res = run(
        clone,
        PREMERGE_PYTHON=fake_python(tmp_path, 0),
        PREMERGE_VITEST=fake_vitest(tmp_path, 1),
    )
    assert res.returncode == 1
    assert "frontend tool-list tests failed" in res.stdout


def test_a_kind_fragment_runs_the_manifest_tests(repos, tmp_path):
    remote, clone = repos
    publish_pr(
        remote,
        {
            "pypsa-gui/tool-error-kinds.d/claude-x.json": "{}\n",
            "pypsa-gui/frontend/keep": "",
            "pypsa-gui/backend/keep": "",
        },
    )
    res = run(
        clone,
        PREMERGE_PYTHON=fake_python(tmp_path, 0),
        PREMERGE_VITEST=fake_vitest(tmp_path, 0),
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert (
        "test_tool_error_kind_manifest.py" in res.stdout
        and "test_json_fragments.py" in res.stdout
    )
    assert "ChatPanel.manifest.test.tsx" in res.stdout


def test_green_checks_are_counted(repos):
    remote, clone = repos
    publish_pr(remote, {"a.py": "a2\n"})
    res = run(clone)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "2 check run(s)" in res.stdout
    assert "all success or skipped" in res.stdout


@pytest.mark.parametrize(
    "row",
    [
        ("completed", "failure", "GUI backend"),
        ("in_progress", "-", "GUI backend shard 1/4"),
        ("completed", "cancelled", "Integration"),
    ],
)
def test_a_run_not_success_or_skipped_blocks_and_is_named(repos, row):
    remote, clone = repos
    publish_pr(remote, {"a.py": "a2\n"})
    res = run(clone, runs=[("completed", "success", "Unit"), row])
    assert res.returncode == 1
    checks = res.stdout.split("== checks")[1]
    assert "2 check run(s)" in checks
    assert row[2] in checks
    assert "Unit" not in checks
    assert "RESULT: blocked" in res.stdout


def test_no_runs_yet_blocks(repos):
    remote, clone = repos
    publish_pr(remote, {"a.py": "a2\n"})
    res = run(clone, runs=[])
    assert res.returncode == 1
    assert "none yet" in res.stdout


@pytest.mark.parametrize("kw", [{"gh_exit": 1}, {"with_gh": False}])
def test_unreadable_checks_block(repos, kw):
    remote, clone = repos
    publish_pr(remote, {"a.py": "a2\n"})
    res = run(clone, **kw)
    assert res.returncode == 1
    assert "checks: not read" in res.stdout


def test_no_runs_on_an_old_head_hints_at_a_master_merge(repos, monkeypatch):
    remote, clone = repos
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00Z")
    publish_pr(remote, {"a.py": "a2\n"})
    res = run(clone, runs=[])
    assert res.returncode == 1
    assert "no CI ran on this head" in res.stdout
    assert "merge master in, as a real merge commit" in res.stdout


def test_no_runs_on_a_fresh_head_has_no_hint(repos):
    remote, clone = repos
    publish_pr(remote, {"a.py": "a2\n"})
    res = run(clone, runs=[])
    assert "none yet" in res.stdout
    assert "no CI ran" not in res.stdout


def test_a_stale_ok_with_many_changed_files_is_reported_not_a_crash(repos):
    # `head -15` closed the pipe on a long file list and premerge died with
    # SIGPIPE (exit 141) in the verdicts section (#790, 455 files).
    remote, clone = repos
    old = publish_pr(remote, {"a.py": "a2\n"})
    publish_pr(remote, {f"many/{'x' * 60}_{i:04}.txt": "" for i in range(2000)}, "many")
    res = run(clone, comments=[f"GK OK @{old}: checked"])
    assert res.returncode != 141, res.stdout + res.stderr
    assert f"GK OK @{old}: stale: changed since: " in res.stdout, (
        res.stdout + res.stderr
    )
