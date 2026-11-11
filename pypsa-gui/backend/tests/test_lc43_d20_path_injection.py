r"""LC-43, LC-D20.path_injection: hostile names never become paths or header breaks.

The seam is the copilot's delivery tools (``export_csv_bundle``, ``build_delivery``) and
the authenticated blob download each delivery names, plus the upload route a person
uses. Three name sinks are read independently:

- the stored name (``add_upload`` -> ``safe_upload_filename``): a model-chosen delivery
  name ``..\\..\\a/b"c\\r\\nd<e>.zip`` is stored as one plain file name, with no path
  separator, ``..``, quote or control character, and the bytes land inside the
  project's own ``uploads`` directory;
- the ``Content-Disposition`` header of the download: one line, its quoted
  ``filename`` is the stored name, and a ``filename*`` (when present) decodes to it;
- the zip member a bundle names (``build_delivery``): ``<file_id>_<name>`` with every
  character outside ``[A-Za-z0-9_.-]`` replaced by ``_`` and at most 100 characters
  after the prefix.

Hand oracle for the member: a stored name ``a b;c'd(1).csv`` becomes the member
``<file_id>_a_b_c_d_1_.csv``; a 150-character stored name keeps its first 100
characters.

The upload route refuses ``..``, ``/`` and ``\\`` in a name with 400 invalid_filename
and stores nothing.
"""

# ruff: noqa: F811 -- shared public harness fixtures
import io
import re
import urllib.parse
import zipfile
from pathlib import Path

import pytest

from services import chat_tools, upload_service
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

HOSTILE = '..\\..\\a/b"c\r\nd<e>.zip'


def _uploads(project_dir: Path) -> list[str]:
    return sorted(
        m.file_id
        for m in upload_service.list_uploads("Tool Baseline", project_dir=project_dir)
    )


def _header_name(header: str) -> tuple[str, str | None]:
    """The quoted ``filename`` and the decoded ``filename*`` of one header value."""
    assert "\r" not in header and "\n" not in header
    quoted = re.findall(r'filename="([^"]*)"', header)
    assert len(quoted) == 1, header
    star = re.findall(r"filename\*=UTF-8''(\S+)", header)
    return quoted[0], urllib.parse.unquote(star[0]) if star else None


def _assert_plain_name(name: str) -> None:
    assert "/" not in name and "\\" not in name and ".." not in name and '"' not in name
    assert not re.search(r"[\x00-\x1f]", name), repr(name)


def test_a_hostile_delivery_name_is_stored_as_one_plain_name_inside_the_project(
    baseline, client
):
    _ctx, _project_id, project_dir = baseline
    source = chat_tools.export_csv_bundle()
    before = _uploads(project_dir)

    delivery = chat_tools.build_delivery([source["file_id"]], HOSTILE)

    assert _uploads(project_dir) == sorted(before + [delivery["file_id"]])
    _assert_plain_name(delivery["filename"])
    assert delivery["filename"].endswith(".zip")
    path = upload_service.get_upload_path(
        "Tool Baseline", delivery["file_id"], project_dir=project_dir
    )
    uploads_dir = (Path(project_dir) / "uploads").resolve()
    assert path.resolve().is_relative_to(uploads_dir)

    response = client.get(delivery["download_url"])
    assert response.status_code == 200, response.text
    quoted, star = _header_name(response.headers["content-disposition"])
    assert quoted == delivery["filename"]
    assert star in (None, delivery["filename"])
    _assert_plain_name(quoted)


def test_each_zip_member_is_the_file_id_and_a_plain_bounded_name(
    baseline, client, monkeypatch
):
    _ctx, _project_id, project_dir = baseline
    long_name = "x" * 146 + ".csv"
    named = []
    for name in ("a b;c'd(1).csv", long_name):
        monkeypatch.setattr(
            chat_tools, "_export_preflight", lambda data, _n=name: "Tool Baseline"
        )
        named.append(
            chat_tools._save_agent_export(
                b"bus,v_nom\nB1,110\n" + name.encode(), name, "text/csv"
            )
        )
    monkeypatch.undo()
    stored = {item["file_id"]: item["filename"] for item in named}
    assert stored[named[0]["file_id"]] == "a b;c'd(1).csv"
    assert stored[named[1]["file_id"]] == long_name

    delivery = chat_tools.build_delivery([item["file_id"] for item in named])
    body = client.get(delivery["download_url"]).content
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = sorted(n for n in archive.namelist() if n != "manifest.json")
    first, second = named[0]["file_id"], named[1]["file_id"]
    assert sorted([f"{first}_a_b_c_d_1_.csv", f"{second}_{'x' * 100}"]) == members
    for member in members:
        assert re.fullmatch(r"[0-9a-f]{16}_[A-Za-z0-9_.-]{1,100}", member), member


@pytest.mark.parametrize("name", ["../etc/passwd", "a/b.csv", "a\\b.csv", "..csv"])
def test_the_upload_route_refuses_a_path_in_the_name_and_stores_nothing(
    baseline, client, name
):
    _ctx, project_id, project_dir = baseline
    before = _uploads(project_dir)
    response = client.post(
        f"/api/projects/{project_id}/uploads",
        files={"file": (name, b"bus,v_nom\nB1,110\n", "text/csv")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error_kind"] == "invalid_filename"
    assert _uploads(project_dir) == before
