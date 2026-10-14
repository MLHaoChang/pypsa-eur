# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Exercise premerge.sh's check-run gate against a local remote and a fake gh."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/coord/premerge.sh"


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def clone(tmp_path):
    """A clone whose remote has master and PR #1 changing a docs file only."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "master")
    (origin / "README.md").write_text("base\n")
    git(origin, "add", ".")
    git(origin, "commit", "-q", "-m", "base")
    git(origin, "checkout", "-q", "-b", "pr")
    (origin / "doc.md").write_text("pr\n")
    git(origin, "add", ".")
    git(origin, "commit", "-q", "-m", "pr")
    git(origin, "update-ref", "refs/pull/1/head", "pr")
    git(origin, "checkout", "-q", "master")
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    return work


def premerge(clone, tmp_path, runs=None, gh_exit=0, with_gh=True):
    """Run premerge.sh with `runs` as the fake gh's (status, conclusion, name) rows."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if with_gh:
        gh = bin_dir / "gh"
        rows = "".join("\t".join(r) + "\n" for r in runs or [])
        gh.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            f"sys.stdout.write({rows!r})\n"
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
        ["bash", str(SCRIPT), "1"],
        cwd=clone,
        env={**os.environ, "PATH": path},
        capture_output=True,
        text=True,
    )


def test_all_success_or_skipped_passes(clone, tmp_path):
    result = premerge(
        clone,
        tmp_path,
        [("completed", "success", "Unit"), ("completed", "skipped", "Docs")],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 check run(s)" in result.stdout
    assert "RESULT: ok to merge" in result.stdout


@pytest.mark.parametrize(
    "row",
    [
        ("completed", "failure", "GUI backend"),
        ("in_progress", "-", "GUI backend shard 1/4"),
        ("completed", "cancelled", "Integration"),
    ],
)
def test_a_run_not_success_or_skipped_blocks_and_is_named(clone, tmp_path, row):
    result = premerge(clone, tmp_path, [("completed", "success", "Unit"), row])
    assert result.returncode == 1
    assert "2 check run(s)" in result.stdout
    assert row[2] in result.stdout
    assert "Unit" not in result.stdout.split("== checks")[1]
    assert "RESULT: blocked" in result.stdout


def test_no_runs_yet_blocks(clone, tmp_path):
    result = premerge(clone, tmp_path, [])
    assert result.returncode == 1
    assert "none yet" in result.stdout


def test_gh_failure_blocks(clone, tmp_path):
    result = premerge(clone, tmp_path, [], gh_exit=1)
    assert result.returncode == 1
    assert "checks: not read" in result.stdout


def test_missing_gh_blocks(clone, tmp_path):
    result = premerge(clone, tmp_path, with_gh=False)
    assert result.returncode == 1
    assert "checks: not read" in result.stdout
