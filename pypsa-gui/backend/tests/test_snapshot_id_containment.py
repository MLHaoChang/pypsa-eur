"""
The snapshot id is a lookup key, never a path fragment.

WHAT THIS IS NOT. It is not a fix for a reachable traversal. `_safe_snapshot_dir`
already refused `..` outright and checked `is_relative_to` on the resolved path,
and no request could get a `/` into the id anyway — the router splits the URL on
`/` before the handler sees it, and `_SNAPSHOT_ID_RE` excludes the character.

WHAT CHANGED. `_existing_snapshot_dir` uses the caller's string ONLY in an
equality test against the names `iterdir()` returns, so the directory it hands
back is one that demonstrably already exists under `snapshots/`. The old helper
BUILT a path from the id and then argued the result was contained. Both are
safe; only one is safe by construction. The argument had two costs: an auditor
had to re-derive it, and CodeQL's `py/path-injection` — which does not model
`Path.is_relative_to` as a barrier — reported the flow running straight through
the guard (`snapshots.py:118 -> :130 -> :136`) into every downstream sink.

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


def test_what_it_returns_is_always_a_real_child_of_snapshots(tmp_path):
    """
    The property the old containment check argued for, now established by
    construction: the result came out of `iterdir()`, so it is a directory that
    already existed under `snapshots/`.
    """
    project_dir = tmp_path / "Proj"
    snaps = project_dir / "snapshots"
    (snaps / "2026-01-01T00-00-00-real").mkdir(parents=True)

    got = snap._existing_snapshot_dir(
        project_dir, "2026-01-01T00-00-00-real", "nope",
    )
    assert got.parent == snaps
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
