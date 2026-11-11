# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Role lookup by ``scripts/coord/whois.py`` from ``docs/agents/coordinator.md`` on origin/master."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "coord" / "whois.py"

COORDINATOR_MD = """# Coordinator

Current coordinator session: `session_01COORD`

Engine gatekeeper A (gridspine; owner decision GK-SPLIT-1): `session_01GKA`

Engine gatekeeper B (harness; GK-SPLIT-1): `session_01GKB`

UX reviewer (UI coherence gate): `session_01UX`

Send every coordinator message to this id.
"""


def git(cwd, *a):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A repo with no remote: the fetch fails and the last origin/master answers."""
    git(tmp_path, "init", "-q", "-b", "master")
    return tmp_path


def publish(repo, text):
    f = repo / "docs" / "agents" / "coordinator.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "ids")
    git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")


def whois(repo, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=repo, capture_output=True, text=True
    )


def test_prints_every_role_as_tab_separated_lines(repo):
    publish(repo, COORDINATOR_MD)
    r = whois(repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        "coordinator\tsession_01COORD",
        "gka\tsession_01GKA",
        "gkb\tsession_01GKB",
        "ux\tsession_01UX",
    ]


@pytest.mark.parametrize(
    ("role", "sid"),
    [
        ("coordinator", "session_01COORD"),
        ("gkb", "session_01GKB"),
        ("ux", "session_01UX"),
    ],
)
def test_one_role_prints_just_its_id(repo, role, sid):
    publish(repo, COORDINATOR_MD)
    assert whois(repo, role).stdout == sid + "\n"


def test_reads_origin_master_not_the_working_tree(repo):
    publish(repo, COORDINATOR_MD)
    (repo / "docs/agents/coordinator.md").write_text(
        COORDINATOR_MD.replace("session_01COORD", "session_01LOCAL")
    )
    assert whois(repo, "coordinator").stdout == "session_01COORD\n"


def test_a_missing_role_exits_non_zero_with_a_message(repo):
    publish(repo, COORDINATOR_MD.replace("`session_01GKB`", "`<session_…>`"))
    r = whois(repo, "gkb")
    assert r.returncode == 1 and r.stdout == ""
    assert "no session id for gkb" in r.stderr
    assert whois(repo).returncode == 1  # the full listing needs every role


def test_unknown_role_and_unreadable_file_fail(repo):
    assert "usage" in whois(repo, "owner").stderr
    r = whois(repo)  # nothing published: no origin/master
    assert r.returncode == 1 and "cannot read origin/master" in r.stderr
