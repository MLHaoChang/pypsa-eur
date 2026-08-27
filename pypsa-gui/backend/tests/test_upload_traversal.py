"""
`file_id` is attacker-influenced input, and it reaches `shutil.rmtree`.

`file_id` is minted as `sha256(bytes)[:16]` — 16 lowercase hex characters —
but nothing on the READ path ever checked that. Every consumer built its
path as `uploads_dir / file_id`, so `file_id='..'` addressed the PROJECT
directory, and `shutil.rmtree` on it deleted the project's contents (the
network, the scenarios, everything) before raising FileNotFoundError on the
final component. The `except OSError` then reported `deleted=False,
reason="in_use"` at HTTP 200 — "couldn't delete one file", rendered
identically to the truth, which was "destroyed your project".

Two reachability notes that decide where the fix belongs:

  * Via HTTP the route converter is `[^/]+`, so a slash cannot survive and
    the worst reachable target is `uploads/..`, the caller's own project.
  * `services/chat_tools.py::delete_upload` calls this service DIRECTLY with
    a model-supplied string. No router, no converter, no regex. Slashes
    survive, so the reachable set is any directory the backend can write —
    in a desktop app, anything the user can write. That path is driven by
    model output, so prompt injection through any file or tool result the
    assistant reads is a sufficient trigger.

So the guard belongs at the SERVICE boundary, where both callers converge. A
route-level fix would look complete and leave the worse path open.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi import HTTPException

from services import upload_service


@pytest.fixture
def project(tmp_path: pathlib.Path):
    """A project with one real upload, sibling data, and an outside victim."""
    projects_root = tmp_path / "projects"
    proj_dir = projects_root / "P1"
    proj_dir.mkdir(parents=True)

    # Sibling data inside the project — what `uploads/..` destroys.
    (proj_dir / "network.nc").write_bytes(b"IMPORTANT MODEL DATA")
    (proj_dir / "scenarios").mkdir()
    (proj_dir / "scenarios" / "s1.json").write_text("{}")

    # A directory OUTSIDE the project — only the chat path can address it.
    victim = projects_root / "victim"
    victim.mkdir()
    (victim / "keepme.txt").write_text("another project's data")

    meta = upload_service.add_upload(
        "P1", b"hello world", "hello.txt", "text/plain", project_dir=proj_dir,
    )
    return proj_dir, victim, meta.file_id


def test_delete_upload_refuses_dotdot_and_leaves_the_project_intact(project):
    proj_dir, _victim, _file_id = project

    resp = upload_service.delete_upload("P1", "..", project_dir=proj_dir)

    # The project is still there. This is the assertion that matters.
    assert (proj_dir / "network.nc").read_bytes() == b"IMPORTANT MODEL DATA"
    assert (proj_dir / "scenarios" / "s1.json").exists()
    assert (proj_dir / "uploads").exists()

    # And the refusal is not success-shaped.
    assert resp.deleted is False


def test_delete_upload_refuses_a_traversal_escaping_the_project(project):
    """The chat-tool shape: slashes, reaching outside the projects tree."""
    proj_dir, victim, _file_id = project

    resp = upload_service.delete_upload(
        "P1", "../../victim", project_dir=proj_dir,
    )

    assert victim.exists(), "traversal escaped the project and destroyed a sibling"
    assert (victim / "keepme.txt").read_text() == "another project's data"
    assert resp.deleted is False


@pytest.mark.parametrize(
    "bad_id",
    ["..", "../../victim", "/etc", "a/b", "", ".", "ABCDEF0123456789",
     "0123456789abcde", "0123456789abcdef0", "0123456789abcdeg"],
)
def test_read_paths_refuse_a_malformed_file_id(project, bad_id):
    """
    A traversal READ is its own disclosure bug — `get_upload_path` hands the
    resolved path to a streaming download, and `get_upload_bytes` to the chat
    tool. Uppercase / wrong-length / non-hex ids are refused too: the guard is
    an allowlist of what a real file_id looks like, not a blocklist of the
    tricks we happened to think of.
    """
    proj_dir, _victim, _file_id = project

    for fn in (upload_service.get_upload_meta, upload_service.get_upload_path):
        with pytest.raises(HTTPException) as ei:
            fn("P1", bad_id, project_dir=proj_dir)
        assert ei.value.status_code == 404


def test_read_path_cannot_reach_another_projects_upload(tmp_path):
    """
    The read paths' 404 today is INCIDENTAL, not a guard: it fires only
    because no `meta.json` happens to exist at the traversed path. Where one
    does — another project's uploads dir — the traversal succeeds and hands
    back that project's bytes. `get_upload_bytes` feeds the chat tool, so this
    is cross-project disclosure driven by a model-supplied string.
    """
    projects_root = tmp_path / "projects"
    mine = projects_root / "Mine"
    theirs = projects_root / "Theirs"
    mine.mkdir(parents=True)
    theirs.mkdir(parents=True)

    upload_service.add_upload(
        "Mine", b"my own file", "mine.txt", "text/plain", project_dir=mine,
    )
    secret = upload_service.add_upload(
        "Theirs", b"SECRET NEIGHBOUR DATA", "secret.txt", "text/plain",
        project_dir=theirs,
    )

    traversed = f"../../Theirs/uploads/{secret.file_id}"
    with pytest.raises(HTTPException) as ei:
        upload_service.get_upload_bytes("Mine", traversed, project_dir=mine)
    assert ei.value.status_code == 404


def test_a_real_file_id_still_works(project):
    """The sibling assertion: the guard must refuse traversal, not everything."""
    proj_dir, _victim, file_id = project

    assert upload_service.get_upload_meta(
        "P1", file_id, project_dir=proj_dir,
    ).file_id == file_id
    assert upload_service.get_upload_bytes(
        "P1", file_id, project_dir=proj_dir,
    ) == b"hello world"

    resp = upload_service.delete_upload("P1", file_id, project_dir=proj_dir)
    assert resp.deleted is True
    assert not (proj_dir / "uploads" / file_id).exists()
