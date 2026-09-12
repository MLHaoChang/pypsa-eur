"""
The snapshot id is a lookup key, never a path fragment.

WHAT THIS IS NOT. It is not a fix for a reachable traversal. `_safe_snapshot_dir`
already refused `..` outright and checked `is_relative_to` on the resolved path,
and no request could get a `/` into the id anyway — the router splits the URL on
`/` before the handler sees it, and `_SNAPSHOT_ID_RE` excludes the character.

WHAT CHANGED. `_existing_snapshot_dir` uses the caller's string ONLY in an
equality test against the names `iterdir()` returns, so the NAME it hands back
is one the directory really contains rather than one the caller supplied. The
old helper BUILT a path from the id and then argued the result was contained.
The argument had two costs: an auditor had to re-derive it, and CodeQL's
`py/path-injection` — which does not model `Path.is_relative_to` as a barrier —
reported the flow running straight through the guard
(`snapshots.py:118 -> :130 -> :136`) into every downstream sink.

WHAT THE FIRST VERSION OF THIS FILE GOT WRONG, and why the symlink cases at the
bottom exist. It claimed the `iterdir()` match made the containment check
redundant — "both are safe; only one is safe by construction". That was false.
`iterdir()` yields a symlink as an ordinary child and `is_dir()` follows it, so
`snapshots/<valid-id>` could be a link to any directory on the box; the helper
this replaced resolved the path and refused exactly that with 400, and the
rewrite shipped without it. The resolve is back. Every test above passed both
before and after that regression, which is the point of the ones below.

WHY THESE CALL THE HELPER DIRECTLY. A hostile id cannot be delivered through
`client.<verb>(url)`: httpx resolves `..` against the URL before sending, so
`.../snapshots/../../Victim/snapshots/X/restore` leaves as
`/api/projects/Victim/snapshots/X/restore` — a perfectly ordinary request for a
project this user owns, which answers 200 and proves nothing. An early draft of
this file asserted on exactly that and "caught" a vulnerability that was
httpx's URL joiner. The routes are covered by the two round-trip cases; the
containment property is tested where it actually lives.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi import HTTPException

from routers import snapshots as snap
from tests.conftest import build_network


def _save(client, name: str, install_network) -> None:
    install_network(build_network(), name=name)
    resp = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text


def _make_snapshot(client, name: str) -> str:
    resp = client.post(f"/api/projects/{name}/snapshots", json={"label": "base"})
    assert resp.status_code in (200, 201), resp.text
    sid = resp.json()["id"]
    assert sid, resp.text
    return sid


# ── the routes still work ───────────────────────────────────────────────────

def test_a_real_snapshot_id_still_restores(client, install_network, tmp_projects_dir):
    """The lookup must still FIND things; a guard that refused everything would
    make every case below vacuously green."""
    _save(client, "SnapHost", install_network)
    sid = _make_snapshot(client, "SnapHost")
    assert client.post(
        f"/api/projects/SnapHost/snapshots/{sid}/restore"
    ).status_code == 200


def test_a_real_snapshot_id_still_deletes(client, install_network, tmp_projects_dir):
    _save(client, "SnapHost", install_network)
    sid = _make_snapshot(client, "SnapHost")
    assert client.delete(f"/api/projects/SnapHost/snapshots/{sid}").status_code == 204


# ── the id never becomes a path ─────────────────────────────────────────────

@pytest.mark.parametrize("hostile", [
    "..",                       # passes the regex — `.` is allowed — and would
    "../..",                    # resolve to the project dir if it were joined
    "....",
    "/etc/passwd",
    "a/b",
    "..\\..\\windows",
    "a" * 129,                  # over the regex's length cap
    "has space",
    "",
])
def test_a_hostile_id_never_yields_a_path(tmp_path, hostile):
    """
    Every one of these must raise rather than return. The assertion is on the
    RETURN, not on the status code: a 400 and a 404 are both fine answers, but
    handing back any path at all is not.
    """
    project_dir = tmp_path / "Proj"
    (project_dir / "snapshots" / "2026-01-01T00-00-00-real").mkdir(parents=True)

    with pytest.raises(HTTPException) as exc:
        snap._existing_snapshot_dir(project_dir, hostile, "nope")
    assert exc.value.status_code in (400, 404)


@pytest.mark.parametrize("malformed", [
    "../..", "/etc/passwd", "a/b", "..\\..\\windows",
    "a" * 129, "has space", "",
])
def test_the_regex_guard_specifically_still_answers_400(tmp_path, malformed):
    """
    Pins the REGEX, which the case above does not: that one accepts 400 or 404,
    so a mutation deleting `_SNAPSHOT_ID_RE` entirely survived it — every id
    then fell through to the `iterdir()` miss and 404'd, which the assertion
    allowed. A malformed id is a client error and must say so.
    """
    project_dir = tmp_path / "Proj"
    (project_dir / "snapshots").mkdir(parents=True)

    with pytest.raises(HTTPException) as exc:
        snap._existing_snapshot_dir(project_dir, malformed, "nope")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("dotted", [".", "..", "...."])
def test_dot_ids_pass_the_regex_and_are_an_ordinary_miss(tmp_path, dotted):
    """
    `_SNAPSHOT_ID_RE` is `[A-Za-z0-9_\-.T]{1,128}`, so `.` and `..` match it.
    The helper this replaced had a SECOND guard — `startswith(".")` → 400 —
    and the rewrite dropped it. That is safe here and only here: the id is
    never joined onto a path, so `..` is just a name that no entry has. Pinned
    so the next person can see the difference was noticed rather than missed,
    and so re-introducing a path join fails a test instead of shipping.
    """
    project_dir = tmp_path / "Proj"
    (project_dir / "snapshots" / "2026-01-01T00-00-00-real").mkdir(parents=True)

    with pytest.raises(HTTPException) as exc:
        snap._existing_snapshot_dir(project_dir, dotted, "nope")
    assert exc.value.status_code == 404


def test_a_plain_file_named_like_a_snapshot_is_not_a_snapshot(tmp_path):
    """
    Pins the `is_dir()` guard, which was also unpinned. Without it the helper
    hands back a FILE, and `delete`'s `_force_rmtree` raises NotADirectoryError
    — a 500 where the honest answer is 404.
    """
    project_dir = tmp_path / "Proj"
    snaps = project_dir / "snapshots"
    snaps.mkdir(parents=True)
    (snaps / "2026-01-01T00-00-00-file").write_text("not a directory")

    with pytest.raises(HTTPException) as exc:
        snap._existing_snapshot_dir(project_dir, "2026-01-01T00-00-00-file", "nope")
    assert exc.value.status_code == 404


def test_what_it_returns_is_always_a_real_child_of_snapshots(tmp_path):
    """The result came out of `iterdir()`, so it is a directory that already
    existed under `snapshots/` — and, per the cases below, one that is still
    under `snapshots/` after resolution."""
    project_dir = tmp_path / "Proj"
    snaps = project_dir / "snapshots"
    (snaps / "2026-01-01T00-00-00-real").mkdir(parents=True)

    got = snap._existing_snapshot_dir(
        project_dir, "2026-01-01T00-00-00-real", "nope",
    )
    assert got.parent == snaps.resolve()
    assert got.is_dir()
    assert got.name == "2026-01-01T00-00-00-real"


def test_a_well_formed_id_that_names_nothing_is_404_not_a_path(tmp_path):
    """The id is a KEY. A name that matches no entry is a miss, and a miss is
    not a directory the caller gets to hear about."""
    project_dir = tmp_path / "Proj"
    (project_dir / "snapshots").mkdir(parents=True)

    with pytest.raises(HTTPException) as exc:
        snap._existing_snapshot_dir(project_dir, "2026-01-01T00-00-00-absent", "nope")
    assert exc.value.status_code == 404


def test_a_missing_snapshots_directory_is_a_miss_not_a_crash(tmp_path):
    """A project that has never been snapshotted has no `snapshots/` at all;
    `iterdir()` raises FileNotFoundError there and the caller must still get
    the ordinary 404."""
    project_dir = tmp_path / "Proj"
    project_dir.mkdir(parents=True)

    with pytest.raises(HTTPException) as exc:
        snap._existing_snapshot_dir(project_dir, "2026-01-01T00-00-00-x", "nope")
    assert exc.value.status_code == 404


# ── a symlink is a child too ────────────────────────────────────────────────
#
# `iterdir()` establishes that the NAME is real. It says nothing about where the
# entry leads. These are the cases the rewrite silently stopped refusing.

def test_a_symlink_out_of_the_tree_is_refused(tmp_path):
    """
    `snapshots/<valid-id>` -> somewhere else entirely. The name matches, the
    regex passes, `is_dir()` follows the link and says yes. Only resolving the
    path catches it — which is why the resolve is not redundant with the
    `iterdir()` match.
    """
    project_dir = tmp_path / "Proj"
    (project_dir / "snapshots").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    (project_dir / "snapshots" / "2026-01-01T00-00-00-evil").symlink_to(
        outside, target_is_directory=True,
    )

    with pytest.raises(HTTPException) as exc:
        snap._existing_snapshot_dir(
            project_dir, "2026-01-01T00-00-00-evil", "nope",
        )
    # 404, not 400: the id is well formed. It just is not a snapshot of this
    # project, and that is all the caller is entitled to hear.
    assert exc.value.status_code == 404


def test_the_symlink_would_otherwise_have_leaked_another_project(tmp_path):
    """
    States the consequence rather than the mechanism, so the case survives a
    refactor of the helper. `restore` copies `_BUNDLE_FILES` out of whatever
    directory it is handed — `network.nc`, and `chat.jsonl` with it. Point the
    link at a second project and the helper is the only thing standing between
    a caller and that project's chat history.
    """
    projects = tmp_path / "projects"
    mine = projects / "Mine"
    (mine / "snapshots").mkdir(parents=True)
    victim = projects / "Victim"
    victim.mkdir(parents=True)
    (victim / "network.nc").write_bytes(b"victim network")
    (victim / "chat.jsonl").write_text('{"user": "victim secrets"}\n')

    (mine / "snapshots" / "2026-01-01T00-00-00-leak").symlink_to(
        victim, target_is_directory=True,
    )

    with pytest.raises(HTTPException):
        snap._existing_snapshot_dir(mine, "2026-01-01T00-00-00-leak", "nope")


def test_a_symlink_within_the_tree_is_still_allowed(tmp_path):
    """The check is containment, not "no symlinks". A link from one snapshot
    name to another resolves inside `snapshots/` and stays legal — refusing it
    would be a behaviour change the old helper never made."""
    project_dir = tmp_path / "Proj"
    snaps = project_dir / "snapshots"
    real = snaps / "2026-01-01T00-00-00-real"
    real.mkdir(parents=True)
    (snaps / "2026-01-01T00-00-00-alias").symlink_to(real, target_is_directory=True)

    got = snap._existing_snapshot_dir(
        project_dir, "2026-01-01T00-00-00-alias", "nope",
    )
    assert got == real.resolve()


def test_a_dangling_symlink_is_a_miss_not_a_crash(tmp_path):
    """`is_dir()` is False for a broken link, so this never reaches the
    resolve — but assert it, because a resolve that ran first would raise
    instead of 404ing."""
    project_dir = tmp_path / "Proj"
    (project_dir / "snapshots").mkdir(parents=True)
    (project_dir / "snapshots" / "2026-01-01T00-00-00-dead").symlink_to(
        tmp_path / "never-existed", target_is_directory=True,
    )

    with pytest.raises(HTTPException) as exc:
        snap._existing_snapshot_dir(project_dir, "2026-01-01T00-00-00-dead", "nope")
    assert exc.value.status_code == 404


def test_a_project_reached_through_a_symlinked_root_still_works(tmp_path):
    """
    The failure mode of a containment check written carelessly: resolve one
    side and not the other, and every project under a symlinked projects root
    — `/tmp` on macOS, a OneDrive-backed `~/Documents` — 404s for every
    snapshot it has.
    """
    real_project = tmp_path / "real" / "Proj"
    (real_project / "snapshots" / "2026-01-01T00-00-00-real").mkdir(parents=True)
    linked = tmp_path / "linked-Proj"
    linked.symlink_to(real_project, target_is_directory=True)

    got = snap._existing_snapshot_dir(linked, "2026-01-01T00-00-00-real", "nope")
    assert got.name == "2026-01-01T00-00-00-real"


# ── the listing helper is the other consumer, and it had the same hole ──────
#
# Fixing `_existing_snapshot_dir` alone was not enough. `_list_snapshot_dirs`
# feeds `list_snapshots` AND `_prune_oldest`, which runs on every create once
# the cap is hit — and `_force_rmtree` on a link does not refuse.

def test_the_listing_drops_an_entry_that_escapes_the_tree(tmp_path):
    project_dir = tmp_path / "Proj"
    snaps = project_dir / "snapshots"
    (snaps / "2026-01-02T00-00-00-real").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (snaps / "1990-01-01T00-00-00-escape").symlink_to(
        outside, target_is_directory=True,
    )

    listed = [d.name for d in snap._list_snapshot_dirs(project_dir)]
    assert listed == ["2026-01-02T00-00-00-real"]


def test_the_prune_candidate_list_cannot_contain_an_escaping_entry(tmp_path):
    """
    Stated as the property `_prune_oldest` depends on rather than by driving
    the prune: the oldest-first tail of this list is what gets deleted, and the
    escaping entry sorts OLDEST (its id starts with 1990), so it would be the
    first thing chosen.
    """
    project_dir = tmp_path / "Proj"
    snaps = project_dir / "snapshots"
    for name in ("2026-01-01T00-00-00-a", "2026-01-02T00-00-00-b"):
        (snaps / name).mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.mkdir()
    (snaps / "1990-01-01T00-00-00-oldest").symlink_to(
        victim, target_is_directory=True,
    )

    dirs = snap._list_snapshot_dirs(project_dir)
    assert all(d.resolve().is_relative_to(snaps.resolve()) for d in dirs)
    assert victim not in [d.resolve() for d in dirs]
