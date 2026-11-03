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


# ── GK B (16) plan conditions ───────────────────────────────────────────────

_CORRUPT = [True, False, "4", "yes", float("nan"), float("inf"), 0, -1, 721, 720.0001, 10**400, None, [4], {"h": 4}]


@pytest.mark.parametrize("stored", _CORRUPT, ids=repr)
def test_both_readers_agree_on_every_stored_value(client, api_project, project_storage_dir, stored):
    """Condition 1: `read_settings` (the network routes' reader) and
    `ProjectInfo.settings` share one validator, so they agree on every
    corrupt value, for both settings."""
    from services import project_settings as ps

    name = api_project("aut-agree")
    d = project_storage_dir(name)
    meta = _read_meta(d)
    meta["settings"] = {KEY: stored, ps.DERIVE_LENGTHS: stored}
    (d / "metadata.json").write_text(json.dumps(meta))   # NaN/Infinity tokens on disk
    expected = dict(ps.DEFAULTS)
    if isinstance(stored, bool):            # a real flag value, corrupt only as hours
        expected[ps.DERIVE_LENGTHS] = stored
    assert _info(client, name)["settings"] == ps.read_settings(d) == expected


@pytest.mark.parametrize("stored,read", [(4, 4.0), (8.5, 8.5), (720, 720.0), (1e-9, 1e-9)])
def test_both_readers_agree_on_valid_values(client, api_project, project_storage_dir, stored, read):
    from services import project_settings as ps

    name = api_project("aut-agree-ok")
    d = project_storage_dir(name)
    meta = _read_meta(d)
    meta["settings"] = {KEY: stored, ps.DERIVE_LENGTHS: True}
    _write_meta(d, meta)
    expected = {**ps.DEFAULTS, KEY: read, ps.DERIVE_LENGTHS: True}
    assert _info(client, name)["settings"] == ps.read_settings(d) == expected
    assert isinstance(ps.read_settings(d)[KEY], float)


def test_the_dto_and_the_reader_name_the_same_settings():
    from models.schemas import ProjectSettings, UpdateProjectSettingsRequest
    from services import project_settings as ps

    assert set(ProjectSettings.model_fields) == set(ps.DEFAULTS) == set(UpdateProjectSettingsRequest.model_fields)
    assert ProjectSettings().model_dump() == ps.DEFAULTS


def test_an_int_is_stored_as_a_float_and_a_resave_is_byte_stable(client, api_project, project_storage_dir):
    """Condition 2."""
    name = api_project("aut-int")
    r = client.patch(URL.format(name), content='{"backup_autonomy_h": 4}',
                     headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    d = project_storage_dir(name)
    assert '"backup_autonomy_h": 4.0' in (d / "metadata.json").read_text()
    before = json.dumps(_read_meta(d)["settings"])
    assert client.post(f"/api/projects/{name}").status_code == 200
    assert json.dumps(_read_meta(d)["settings"]) == before


def test_bound_constants_are_shared_and_enforced_by_both_models():
    """Condition 6: 720 accepted, 720.0001 refused, through the comparison
    request and the settings PATCH body alike."""
    from pydantic import ValidationError

    from models.schemas import UpdateProjectSettingsRequest

    assert BACKUP_AUTONOMY_MAX_H == 720
    assert DesignIntentSpec(intent="A", backup_autonomy_h=720).backup_autonomy_h == 720
    assert UpdateProjectSettingsRequest(backup_autonomy_h=720).backup_autonomy_h == 720
    with pytest.raises(ValidationError):
        DesignIntentSpec(intent="A", backup_autonomy_h=720.0001)
    with pytest.raises(ValidationError):
        UpdateProjectSettingsRequest(backup_autonomy_h=720.0001)


def test_an_unbound_network_saved_over_a_project_writes_no_settings(
        client, api_project, install_network, project_storage_dir):
    """Condition 3: a network bound to no directory (new, never saved) has no
    settings table, so a forced save over a project writes none and never
    inherits the destination's leftovers."""
    from tests.conftest import build_network

    victim = api_project("aut-victim4")
    _set(client, victim, 99)
    install_network(build_network())        # unbound: no name, no directory
    r = client.post(f"/api/projects/{victim}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    assert "settings" not in _read_meta(project_storage_dir(victim))
    assert _autonomy(client, victim) == BACKUP_AUTONOMY_DEFAULT_H


def test_an_imported_bundle_keeps_it_through_its_own_save(client, api_project):
    """Condition 3: an imported project, once opened, saves into its own
    directory and keeps its table."""
    name = api_project("aut-imp-src")
    _set(client, name, 11)
    blob = client.get(f"/api/projects/{name}/bundle").content
    r = client.post("/api/projects/import_bundle?name=aut_imp_dst",
                    files={"file": ("a.pypsaproj.zip", blob, "application/zip")})
    assert r.status_code in (200, 201), r.text
    dst = r.json().get("imported") or r.json().get("name") or "aut_imp_dst"
    r = client.post(f"/api/projects/{_info(client, dst)['id']}/activate")
    assert r.status_code == 200, r.text
    assert client.post(f"/api/projects/{dst}").status_code == 200
    assert _autonomy(client, dst) == 11


def test_a_concurrent_save_and_patch_lose_neither_edit(client, api_project, project_storage_dir, monkeypatch):
    """Condition 4: the save holds the context's mutation lock across its
    metadata read-modify-write; the PATCH takes the same lock (after the
    project lock, as the save does), so a PATCH landing mid-save waits and
    its value survives the save's rewrite."""
    import sys
    import threading
    import time

    import routers.projects as projects

    name = api_project("aut-race")
    d = project_storage_dir(name)
    real_read = projects._read_meta
    save_reading, go = threading.Event(), threading.Event()
    paused = {"done": False}

    def slow_read(path):
        meta = real_read(path)
        # Only the save's own read, the one inside its `with ctx.mutation_lock`
        # (the endpoint runs on a worker thread, so the caller is the marker).
        if not paused["done"] and sys._getframe(1).f_code.co_name == "_save_context":
            paused["done"] = True
            save_reading.set()
            go.wait(10)
        return meta

    monkeypatch.setattr(projects, "_read_meta", slow_read)
    results = {}
    saver = threading.Thread(target=lambda: results.setdefault(
        "save", client.post(f"/api/projects/{name}", params={"objective": 123.0})))
    patcher = threading.Thread(target=lambda: results.setdefault(
        "patch", client.patch(URL.format(name), json={KEY: 9})))
    saver.daemon = patcher.daemon = True
    saver.start()
    try:
        assert save_reading.wait(10), "the save never reached its metadata read"
        patcher.start()
        time.sleep(0.5)
        assert patcher.is_alive(), "the PATCH did not wait for the save's lock"
    finally:
        go.set()                             # never leave the save parked
        saver.join(30)
        if patcher.is_alive() or patcher.ident:
            patcher.join(30)
    assert results["save"].status_code == 200, results["save"].text
    assert results["patch"].status_code == 200, results["patch"].text
    meta = real_read(d)
    assert meta["settings"] == {KEY: 9.0}          # the PATCH's edit
    assert meta["objective"] == 123.0              # the save's edit


def test_the_setting_is_inert_for_the_network_fingerprint(client, api_project):
    """Condition 5: autonomy feeds no LP, so a PATCH leaves the network
    input fingerprint (what results and digests are keyed on) unchanged."""
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _network_input_fingerprint

    name = api_project("aut-fp")
    ctx = PyPSAService.get_active_context()
    before = _network_input_fingerprint(ctx, exact=True)
    _set(client, name, 48)
    assert _network_input_fingerprint(ctx, exact=True) == before


def test_no_comparison_result_reads_the_stored_value():
    """Condition 5: each comparison run records the autonomy its own request
    carried (`intent_table.build_row`), and nothing on the comparison path
    reads the stored setting, so a later PATCH cannot relabel an old result.
    Wiring the comparison to the stored value is a follow-up that must
    snapshot it into the job record; this pin makes that a deliberate edit."""
    import inspect

    import routers.compare as compare_router
    from services.compare import intent_jobs, intent_table

    for module in (compare_router, intent_jobs, intent_table):
        src = inspect.getsource(module)
        assert "project_settings" not in src, module.__name__
        assert "read_settings" not in src, module.__name__
