"""
AS-AUTONOMY-1 (owner decision AS10-AUTONOMY-2): the intent-A backup autonomy,
stored with the project.

The value lives in the existing per-project `settings` table of
`metadata.json` (plan M2), beside `derive_lengths_from_geometry`, so it
travels everywhere metadata.json does. These tests pin the validation (finite,
> 0, bounded; 422 otherwise) and the round-trips: a save, a bundle
export/import, a snapshot restore, a scenario fork, Save-a-Copy and Save-As.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from models.design_intent import BACKUP_AUTONOMY_DEFAULT_H, BACKUP_AUTONOMY_MAX_H, DesignIntentSpec
from routers.projects import _read_meta, _write_meta
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401

URL = "/api/projects/{}/settings"
KEY = "backup_autonomy_h"


def _info(client, name) -> dict:
    rows = client.get("/api/projects/").json()
    return next(p for p in rows if p["name"] == name)


def _autonomy(client, name) -> float:
    return _info(client, name)["settings"][KEY]


def _set(client, name, hours) -> None:
    r = client.patch(URL.format(name), json={KEY: hours})
    assert r.status_code == 200, r.text
    assert r.json()["settings"][KEY] == hours


# ── the bound is one bound ──────────────────────────────────────────────────

def test_the_stored_bound_is_the_comparison_bound():
    """The comparison request (DesignIntentSpec) and the stored setting share
    one default and one upper bound, so a stored value is always one the
    comparison accepts."""
    field = DesignIntentSpec.model_fields[KEY]
    assert field.default == BACKUP_AUTONOMY_DEFAULT_H == 4.0
    DesignIntentSpec(intent="A", backup_autonomy_h=BACKUP_AUTONOMY_MAX_H)
    with pytest.raises(ValueError):
        DesignIntentSpec(intent="A", backup_autonomy_h=BACKUP_AUTONOMY_MAX_H + 1e-6)


# ── read, write, reset ──────────────────────────────────────────────────────

def test_default_is_the_intent_default(client, api_project):
    name = api_project("aut-default")
    assert _autonomy(client, name) == BACKUP_AUTONOMY_DEFAULT_H


def test_patch_sets_and_persists(client, api_project, project_storage_dir):
    name = api_project("aut-set")
    _set(client, name, 8.5)
    assert _autonomy(client, name) == 8.5
    assert _read_meta(project_storage_dir(name))["settings"] == {KEY: 8.5}


def test_patch_leaves_the_sibling_setting_alone(client, api_project, project_storage_dir):
    name = api_project("aut-sibling")
    assert client.patch(URL.format(name), json={"derive_lengths_from_geometry": True}).status_code == 200
    _set(client, name, 2)
    assert _read_meta(project_storage_dir(name))["settings"] == {
        "derive_lengths_from_geometry": True, KEY: 2}
    assert _info(client, name)["settings"]["derive_lengths_from_geometry"] is True


def test_null_resets_to_the_default(client, api_project, project_storage_dir):
    name = api_project("aut-reset")
    _set(client, name, 12)
    r = client.patch(URL.format(name), json={KEY: None})
    assert r.status_code == 200, r.text
    assert r.json()["settings"][KEY] == BACKUP_AUTONOMY_DEFAULT_H
    assert KEY not in _read_meta(project_storage_dir(name))["settings"]


def test_the_upper_bound_itself_is_accepted(client, api_project):
    name = api_project("aut-max")
    _set(client, name, BACKUP_AUTONOMY_MAX_H)


@pytest.mark.parametrize("body", [
    '{"backup_autonomy_h": 0}',
    '{"backup_autonomy_h": -1}',
    '{"backup_autonomy_h": 720.0001}',
    '{"backup_autonomy_h": 1e309}',          # parses to inf
    '{"backup_autonomy_h": NaN}',
    '{"backup_autonomy_h": Infinity}',
    '{"backup_autonomy_h": -Infinity}',
    '{"backup_autonomy_h": true}',
    '{"backup_autonomy_h": "4"}',
    '{"backup_autonomy_h": [4]}',
])
def test_an_invalid_value_is_422_and_writes_nothing(client, api_project, project_storage_dir, body):
    name = api_project("aut-bad")
    _set(client, name, 6)
    r = client.patch(URL.format(name), content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    assert _read_meta(project_storage_dir(name))["settings"] == {KEY: 6}


def test_an_unknown_setting_is_422(client, api_project):
    name = api_project("aut-extra")
    r = client.patch(URL.format(name), json={KEY: 4, "autonomy": 4})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("stored", [0, -3, 1e6, "8", True, None, [8], {"h": 8}])
def test_a_bad_stored_value_reads_as_the_default(client, api_project, project_storage_dir, stored):
    """A hand-edited metadata.json never 500s and never reaches the UI as a
    value the comparison would refuse."""
    name = api_project("aut-corrupt")
    d = project_storage_dir(name)
    meta = _read_meta(d)
    meta["settings"] = {KEY: stored, "derive_lengths_from_geometry": True}
    _write_meta(d, meta)
    settings = _info(client, name)["settings"]
    assert settings[KEY] == BACKUP_AUTONOMY_DEFAULT_H
    # One bad field does not take a good sibling down with it.
    assert settings["derive_lengths_from_geometry"] is True


def test_a_non_finite_stored_value_reads_as_the_default(client, api_project, project_storage_dir):
    name = api_project("aut-nan")
    d = project_storage_dir(name)
    meta = _read_meta(d)
    meta["settings"] = {KEY: float("nan")}
    (d / "metadata.json").write_text(json.dumps(meta))   # NaN token on disk
    assert _autonomy(client, name) == BACKUP_AUTONOMY_DEFAULT_H


def test_patch_is_refused_under_a_foreign_lock(client, api_project, same_org_other_user):  # noqa: F811
    name = api_project("aut-lock")
    assert client.post(f"/api/projects/{name}/lock").status_code == 200
    r = same_org_other_user.patch(URL.format(name), json={KEY: 9})
    assert _is_lock_refusal(r), f"{r.status_code} {r.text[:200]}"
    assert _autonomy(client, name) == BACKUP_AUTONOMY_DEFAULT_H


# ── round-trips ─────────────────────────────────────────────────────────────

def test_a_save_keeps_it(client, api_project):
    name = api_project("aut-save")
    _set(client, name, 7)
    assert client.post(f"/api/projects/{name}").status_code == 200
    assert _autonomy(client, name) == 7


def test_bundle_export_and_import_carry_it(client, api_project):
    name = api_project("aut-bundle")
    _set(client, name, 10)
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    meta = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("metadata.json"))
    assert meta["settings"][KEY] == 10
    r = client.post("/api/projects/import_bundle?name=aut_bundle_imported",
                    files={"file": ("a.pypsaproj.zip", r.content, "application/zip")})
    assert r.status_code in (200, 201), r.text
    imported = r.json().get("imported") or r.json().get("name") or "aut_bundle_imported"
    assert _autonomy(client, imported) == 10


def test_snapshot_restore_brings_it_back(client, api_project):
    name = api_project("aut-snap")
    _set(client, name, 3)
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code in (200, 201), r.text
    snap_id = r.json()["id"]
    _set(client, name, 30)
    r = client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore")
    assert r.status_code == 200, r.text
    assert _autonomy(client, name) == 3


def test_a_scenario_fork_inherits_it(client, api_project):
    name = api_project("aut-parent")
    _set(client, name, 5)
    r = client.post(f"/api/projects/{name}/scenarios", json={"name": "aut-child", "description": "x"})
    assert r.status_code in (200, 201), r.text
    child = r.json().get("name") or "aut-child"
    assert _autonomy(client, child) == 5
    _set(client, child, 50)
    assert _autonomy(client, name) == 5


@pytest.mark.parametrize("rebind", [False, True], ids=["save-a-copy", "save-as"])
def test_a_copy_to_a_new_name_carries_it(client, api_project, rebind):
    """Before AS-AUTONOMY-1 the save carried `settings` from the DESTINATION's
    old metadata, so a copy to a new name started from the defaults."""
    name = api_project("aut-src")
    _set(client, name, 16)
    dst = f"aut-copy-{int(rebind)}"
    r = client.post(f"/api/projects/{dst}", params={"force": True, "rebind": rebind})
    assert r.status_code == 200, r.text
    assert _autonomy(client, dst) == 16
    assert _autonomy(client, name) == 16


def test_a_forced_save_as_over_another_project_takes_the_sources(client, api_project):
    """The overwritten project's choice does not survive under the source's
    network, as its layout does not (`_carry_sidecars_on_move`)."""
    victim = api_project("aut-victim")
    _set(client, victim, 99)
    src = api_project("aut-src2")           # now the bound project
    _set(client, src, 2)
    r = client.post(f"/api/projects/{victim}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    assert _autonomy(client, victim) == 2


def test_a_forced_save_as_from_a_default_source_drops_the_victims(client, api_project, project_storage_dir):
    victim = api_project("aut-victim2")
    _set(client, victim, 99)
    api_project("aut-src3")                 # never set: no settings table entry
    r = client.post(f"/api/projects/{victim}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    assert _autonomy(client, victim) == BACKUP_AUTONOMY_DEFAULT_H
