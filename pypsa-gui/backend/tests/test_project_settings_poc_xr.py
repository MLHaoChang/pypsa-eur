"""
AS-17 (owner decision FILL-10-2): the project's point-of-connection X/R, stored with the project.

The value lives in the per-project `settings` table of `metadata.json` beside
`backup_autonomy_h`. Unset means none (the scorecard assumes `flicker_inputs.DEFAULT_XR`), so the
default is null, not 4. A finite number in (0, 100]; 422 otherwise, and a stored value that is not
one reads as unset.
"""
from __future__ import annotations

import json

import pytest

from routers.projects import _read_meta, _write_meta
from services import project_settings as PS

URL = "/api/projects/{}/settings"
KEY = "poc_xr"


def _info(client, name) -> dict:
    rows = client.get("/api/projects/").json()
    return next(p for p in rows if p["name"] == name)


def _xr(client, name):
    return _info(client, name)["settings"][KEY]


def _set(client, name, xr) -> None:
    r = client.patch(URL.format(name), json={KEY: xr})
    assert r.status_code == 200, r.text
    assert r.json()["settings"][KEY] == xr


@pytest.mark.parametrize("value, read", [
    (1.5, 1.5), (4, 4.0), (100, 100.0), (1e-3, 1e-3),
    (0, None), (-2, None), (100.0001, None), (float("nan"), None), (float("inf"), None),
    (True, None), ("1.5", None), ([1.5], None), (None, None),
])
def test_the_one_validator(value, read):
    assert PS.settings_from_table({KEY: value})[KEY] == read
    assert PS.DEFAULTS[KEY] is None


def test_the_reader_reads_it_from_metadata_json(tmp_path):
    assert PS.poc_xr(tmp_path) is None                                  # no file
    (tmp_path / "metadata.json").write_text(json.dumps({"settings": {KEY: 2.5}}))
    assert PS.poc_xr(tmp_path) == 2.5


def test_default_is_unset(client, api_project):
    assert _xr(client, api_project("xr-default")) is None


def test_patch_sets_and_persists_beside_the_siblings(client, api_project, project_storage_dir):
    name = api_project("xr-set")
    assert client.patch(URL.format(name), json={"backup_autonomy_h": 6}).status_code == 200
    _set(client, name, 1.5)
    assert _read_meta(project_storage_dir(name))["settings"] == {"backup_autonomy_h": 6, KEY: 1.5}
    assert _info(client, name)["settings"]["backup_autonomy_h"] == 6


def test_null_clears_it(client, api_project, project_storage_dir):
    name = api_project("xr-clear")
    _set(client, name, 3)
    r = client.patch(URL.format(name), json={KEY: None})
    assert r.status_code == 200, r.text
    assert r.json()["settings"][KEY] is None
    assert KEY not in _read_meta(project_storage_dir(name))["settings"]


def test_the_upper_bound_itself_is_accepted(client, api_project):
    _set(client, api_project("xr-max"), 100)


@pytest.mark.parametrize("body", [
    '{"poc_xr": 0}',
    '{"poc_xr": -1}',
    '{"poc_xr": 100.0001}',
    '{"poc_xr": 1e309}',
    '{"poc_xr": NaN}',
    '{"poc_xr": Infinity}',
    '{"poc_xr": -Infinity}',
    '{"poc_xr": true}',
    '{"poc_xr": "1.5"}',
    '{"poc_xr": [1.5]}',
])
def test_an_invalid_value_is_422_and_writes_nothing(client, api_project, project_storage_dir, body):
    name = api_project("xr-bad")
    _set(client, name, 2)
    r = client.patch(URL.format(name), content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    assert _read_meta(project_storage_dir(name))["settings"] == {KEY: 2}


@pytest.mark.parametrize("stored", [0, -3, 1e6, "2", True, [2], {"x": 2}])
def test_a_bad_stored_value_reads_as_unset(client, api_project, project_storage_dir, stored):
    name = api_project("xr-corrupt")
    d = project_storage_dir(name)
    meta = _read_meta(d)
    meta["settings"] = {KEY: stored, "derive_lengths_from_geometry": True}
    _write_meta(d, meta)
    settings = _info(client, name)["settings"]
    assert settings[KEY] is None and settings["derive_lengths_from_geometry"] is True


def test_a_non_finite_stored_value_reads_as_unset(client, api_project, project_storage_dir):
    name = api_project("xr-nan")
    d = project_storage_dir(name)
    meta = _read_meta(d)
    meta["settings"] = {KEY: float("nan")}
    (d / "metadata.json").write_text(json.dumps(meta))
    assert _xr(client, name) is None


def test_a_save_keeps_it(client, api_project):
    name = api_project("xr-save")
    _set(client, name, 7.5)
    assert client.post(f"/api/projects/{name}").status_code == 200
    assert _xr(client, name) == 7.5
