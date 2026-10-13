"""
Per-project settings in `metadata.json` — today one: "Derive lengths from
geometry" (plan M2, off by default). `PATCH /api/projects/{name}/settings`
writes it, `ProjectInfo.settings` carries it, a save keeps it, and the
network-side reader (`services/project_settings.py`) answers from the file
alone so the length routes never import the projects router.
"""
from __future__ import annotations

import json

import pytest

from routers.projects import _read_meta
from services import project_settings as ps
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401

URL = "/api/projects/{}/settings"


def _info(client, name) -> dict:
    rows = client.get("/api/projects/").json()
    return next(p for p in rows if p["name"] == name)


# ── the pure reader ─────────────────────────────────────────────────────────

def test_reader_defaults_off_when_the_file_is_missing_or_corrupt(tmp_path):
    assert ps.derive_lengths_enabled(tmp_path) is False
    (tmp_path / "metadata.json").write_text("{not json")
    assert ps.derive_lengths_enabled(tmp_path) is False
    (tmp_path / "metadata.json").write_text(json.dumps({"settings": "nope"}))
    assert ps.derive_lengths_enabled(tmp_path) is False
    assert ps.derive_lengths_enabled(None) is False


def test_reader_sees_the_flag_and_ignores_truthy_strings(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"settings": {ps.DERIVE_LENGTHS: True}}))
    assert ps.derive_lengths_enabled(tmp_path) is True
    (tmp_path / "metadata.json").write_text(json.dumps({"settings": {ps.DERIVE_LENGTHS: "yes"}}))
    assert ps.derive_lengths_enabled(tmp_path) is False


# ── the route ───────────────────────────────────────────────────────────────

def test_default_is_off_in_project_info(client, api_project):
    name = api_project("settings-default")
    assert _info(client, name)["settings"] == {"derive_lengths_from_geometry": False}


def test_patch_turns_it_on_and_persists_in_metadata(client, api_project, project_storage_dir):
    name = api_project("settings-on")
    r = client.patch(URL.format(name), json={"derive_lengths_from_geometry": True})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["derive_lengths_from_geometry"] is True
    assert _info(client, name)["settings"]["derive_lengths_from_geometry"] is True
    meta = _read_meta(project_storage_dir(name))
    assert meta["settings"] == {"derive_lengths_from_geometry": True}
    # The rest of the file is untouched by a settings edit.
    assert meta["bus_count"] == 1 and "created_at" in meta
    assert ps.derive_lengths_enabled(project_storage_dir(name)) is True


def test_patch_off_again(client, api_project, project_storage_dir):
    name = api_project("settings-off")
    client.patch(URL.format(name), json={"derive_lengths_from_geometry": True})
    r = client.patch(URL.format(name), json={"derive_lengths_from_geometry": False})
    assert r.status_code == 200 and r.json()["settings"]["derive_lengths_from_geometry"] is False
    assert ps.derive_lengths_enabled(project_storage_dir(name)) is False


def test_a_save_keeps_the_setting(client, api_project, project_storage_dir):
    """
    `save_project` rewrites metadata.json from a fixed key list; the setting
    must ride along or the first autosave after the toggle silently resets it.
    """
    name = api_project("settings-save")
    client.patch(URL.format(name), json={"derive_lengths_from_geometry": True})
    r = client.post(f"/api/projects/{name}", params={"force": True})
    assert r.status_code == 200, r.text
    assert _read_meta(project_storage_dir(name))["settings"] == {"derive_lengths_from_geometry": True}
    assert _info(client, name)["settings"]["derive_lengths_from_geometry"] is True


def test_an_empty_patch_changes_nothing(client, api_project, project_storage_dir):
    name = api_project("settings-noop")
    before = _read_meta(project_storage_dir(name))
    r = client.patch(URL.format(name), json={})
    assert r.status_code == 200, r.text
    assert _read_meta(project_storage_dir(name)) == before


def test_a_non_boolean_is_422(client, api_project):
    name = api_project("settings-type")
    assert client.patch(URL.format(name), json={"derive_lengths_from_geometry": "yes"}).status_code == 422


def test_unknown_project_404_and_other_org_404(client, other_org_client, api_project):
    name = api_project("settings-mine")
    assert client.patch(URL.format("nope"), json={"derive_lengths_from_geometry": True}).status_code == 404
    assert other_org_client.patch(URL.format(name), json={"derive_lengths_from_geometry": True}).status_code == 404


def test_patch_is_refused_under_a_foreign_lock(client, api_project, same_org_other_user):  # noqa: F811
    name = api_project("settings-lock")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    r = same_org_other_user.patch(URL.format(name), json={"derive_lengths_from_geometry": True})
    assert _is_lock_refusal(r), f"{r.status_code} {r.text[:200]}"
    assert _info(client, name)["settings"]["derive_lengths_from_geometry"] is False


@pytest.mark.parametrize("payload", [{"settings": {}}, {}])
def test_project_info_tolerates_old_metadata(client, api_project, project_storage_dir, payload):
    """A pre-M2 metadata.json (no `settings`) is the default, not an error."""
    name = api_project("settings-old")
    pdir = project_storage_dir(name)
    meta = _read_meta(pdir)
    meta.pop("settings", None)
    meta.update(payload)
    (pdir / "metadata.json").write_text(json.dumps(meta))
    assert _info(client, name)["settings"] == {"derive_lengths_from_geometry": False}
