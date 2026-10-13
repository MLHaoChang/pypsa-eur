# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Overlap, conflict and semantic-test gating of ``scripts/coord/premerge.sh``."""

import os
import shutil
import subprocess
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


def run(clone, **env):
    return subprocess.run(
        ["bash", str(SCRIPT), "7"],
        cwd=clone,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
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
