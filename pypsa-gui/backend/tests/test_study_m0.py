"""
S4 M0: a question-first study creates its OWN base project (plan S4
"Mutation boundary", review v2 BC-5; gate S1 carries).

* Creating a study changes no pre-existing project's directory — hashed file
  by file, every project in the database, before and after.
* The new base project holds the question pack (the grid-only baseline
  network) and the sidecar; its record carries `pack_project`.
* The request's slot is untouched, and the process `_user_ts` is not read
  or written.
* A record attached to an existing user project (a custom question) runs no
  pack and cannot be run; neither can a record copied into another project.
* The intake refuses a measured capacity charge (gate S3 N-v2-2), and a
  tariff change re-seeds the ledger (gate S2 [S9]).
"""
from __future__ import annotations

import copy
import json

import pypsa
import pytest

from services.study import packs as P
from tests.study_s4_support import (
    INTAKE,
    all_project_dirs,
    create_pack_study,
    dir_hash,
    enable_studies,
)


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


def test_creating_a_study_changes_no_existing_project_and_builds_its_own_base(
        client, api_project, studies_on, _auth_db, session_ctx, project_storage_dir):
    from routers import network as network_router

    _engine, session_local = _auth_db
    api_project("m0-src")
    api_project("m0-other")
    before = {k: dir_hash(d) for k, d in all_project_dirs(session_local).items()}
    slot_before = session_ctx(client).loaded_project
    # `_user_ts` is a VIEW of the active context's store since #71 (no
    # `__eq__`: a deep copy of the view never equals it); compare contents.
    user_ts_before = copy.deepcopy(dict(network_router._user_ts.items()))

    r = create_pack_study(client, "m0-src", "m0-base")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["base_project_name"] == "m0-base"

    after = all_project_dirs(session_local)
    for key, digest in before.items():
        assert dir_hash(after[key]) == digest, f"project {key} changed on disk"
    assert session_ctx(client).loaded_project == slot_before
    after_ts = dict(network_router._user_ts.items())
    assert sorted(after_ts) == sorted(user_ts_before)
    assert all(after_ts[k] is v or getattr(after_ts[k], "equals", lambda o: after_ts[k] == o)(v)
               for k, v in user_ts_before.items())

    base_dir = project_storage_dir("m0-base")
    assert str(base_dir) not in {str(d) for d in before}
    assert body["pack_project"] == body["base_project"]
    assert body["base_project"] not in before
    sidecar = json.loads((base_dir / "studies" / f"{body['study_id']}.json").read_text())
    assert sidecar["pack_project"] == body["base_project"]
    assert sidecar["budget"]["solves_max"] == 5          # estimate_solves, 5 options
    n = pypsa.Network(str(base_dir / "network.nc"))
    assert n.meta[P.PACK_META_KEY]["option_id"] == "none"
    assert list(n.links.index) == ["grid_import", "grid_export"]
    assert n.storage_units.empty and len(n.snapshots) == 8760
    assert not (base_dir / "user_ts.json").exists()


def test_a_record_attached_to_an_existing_project_never_runs_a_pack(
        client, api_project, studies_on, _auth_db, project_storage_dir):
    api_project("m0-attached")
    r = client.post("/api/projects/m0-attached/studies/", json={
        "question_id": "bess_site", "name": "custom", "intake": INTAKE})
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert r.json()["pack_project"] is None
    digest = dir_hash(project_storage_dir("m0-attached"))
    r = client.post(f"/api/projects/m0-attached/studies/{sid}/run", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == "study_not_runnable"
    assert dir_hash(project_storage_dir("m0-attached")) == digest


def test_a_copied_pack_record_is_stale_and_not_runnable(
        client, api_project, studies_on, project_storage_dir):
    api_project("m0-copy-src")
    r = create_pack_study(client, "m0-copy-src", "m0-copy-base")
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    src = project_storage_dir("m0-copy-base") / "studies" / f"{sid}.json"
    data = json.loads(src.read_text())
    data["findings_ref"] = f"studies/{sid}.findings.json"
    api_project("m0-copy-dst")
    dst = project_storage_dir("m0-copy-dst") / "studies"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / f"{sid}.json").write_text(json.dumps(data))

    got = client.get(f"/api/projects/m0-copy-dst/studies/{sid}").json()
    assert got["stale"] is True
    assert "copied_record_findings_computed_on_the_origin_forks" in got["stale_reasons"]
    r = client.post(f"/api/projects/m0-copy-dst/studies/{sid}/run", json={})
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "study_not_runnable"
    # Adoption persists the stale mark.
    r = client.patch(f"/api/projects/m0-copy-dst/studies/{sid}", json={"name": "adopted"})
    assert r.status_code == 200, r.text
    stored = json.loads((dst / f"{sid}.json").read_text())
    assert stored["stale"] is True


def test_the_intake_refuses_a_measured_capacity_charge(client, api_project, studies_on):
    from services.study import library as lib

    api_project("m0-measured")
    custom = lib.load_library().tariffs["de_industrial_illustrative"].model_dump()
    custom.update(tariff_id="site", source="site bill",
                  capacity_charge={"price_per_mw_per_year": 50000.0, "basis": "measured"})
    r = create_pack_study(client, "m0-measured", "m0-measured-base",
                          intake={**INTAKE, "tariff": {"custom": custom}})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "capacity_charge_measured_unsupported"
    # Nothing was created.
    assert client.get("/api/projects/m0-measured-base/studies/").status_code == 404

    r = create_pack_study(client, "m0-measured", "m0-measured-base")
    sid = r.json()["study_id"]
    r = client.patch(f"/api/projects/m0-measured-base/studies/{sid}", json={
        "step": "tariff", "intake": {"tariff": {"custom": custom}}})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "capacity_charge_measured_unsupported"


def _custom_tariff(price):
    from services.study import library as lib

    t = lib.load_library().tariffs["de_industrial_illustrative"].model_dump()
    t.update(tariff_id="site_bill", name="Site bill", source="site bill 2025",
             honesty_notes=[])
    t["demand_charge"]["price_per_mw_per_period"] = price
    return t


def test_a_tariff_change_reseeds_the_supplied_tariffs_rows(client, api_project, studies_on):
    """
    Gate S2 carry: the `changed_at` filter — a supplied tariff's own rows are
    re-derived from the new intake, never kept as the user's.
    """
    api_project("m0-reseed")
    r = create_pack_study(client, "m0-reseed", "m0-reseed-base",
                          intake={**INTAKE, "tariff": {"custom": _custom_tariff(7000.0)}})
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    rows = {x["key"]: x for x in r.json()["ledger"]["rows"]}
    assert rows["demand_charge_price"]["value"] == 7000.0
    assert rows["demand_charge_price"]["provenance"] == "user"

    r = client.patch(f"/api/projects/m0-reseed-base/studies/{sid}", json={
        "step": "tariff", "intake": {"tariff": {"tariff_id": "de_industrial_illustrative"}}})
    assert r.status_code == 200, r.text
    rows = {x["key"]: x for x in r.json()["ledger"]["rows"]}
    assert rows["demand_charge_price"]["value"] == 9000.0
    assert rows["demand_charge_price"]["provenance"] == "library"
    assert rows["tariff"]["technical_name"] == "de_industrial_illustrative"


def test_a_needs_attention_row_refuses_the_run(client, api_project, studies_on):
    api_project("m0-attn")
    r = create_pack_study(client, "m0-attn", "m0-attn-base")
    sid = r.json()["study_id"]
    r = client.put(f"/api/projects/m0-attn-base/studies/{sid}/ledger", json={
        "rows": [{"key": "demand_charge_price", "value": 8000.0, "unit": "EUR/MW/month"}]})
    assert r.status_code == 200, r.text
    # The time-of-use tariff has no demand charge: the user's 8000 no longer
    # applies, and the automatic re-seed flags it.
    r = client.patch(f"/api/projects/m0-attn-base/studies/{sid}", json={
        "step": "tariff", "intake": {"tariff": {"tariff_id": "tou_reference_illustrative"}}})
    assert r.status_code == 200, r.text
    rows = {x["key"]: x for x in r.json()["ledger"]["rows"]}
    assert rows["demand_charge_price"]["status"] == "needs_attention"
    r = client.post(f"/api/projects/m0-attn-base/studies/{sid}/run", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == "ledger_needs_attention"
    assert "demand_charge_price" in r.json()["detail"]["message"]


def test_incomplete_intake_is_refused_at_creation(client, api_project, studies_on):
    api_project("m0-incomplete")
    r = create_pack_study(client, "m0-incomplete", "m0-incomplete-base",
                          intake={"tariff": {"tariff_id": "de_industrial_illustrative"}})
    assert r.status_code == 422
    assert r.json()["detail"]["error_kind"] == "intake_incomplete"


def test_an_energy_price_level_edit_on_a_flat_tariff_is_refused(
        client, api_project, studies_on):
    """
    Gate S4 [S5]: on a single-band tariff the level changes nothing, so an
    edit is refused rather than stored and ignored.
    """
    api_project("m0-flat")
    sid = create_pack_study(client, "m0-flat", "m0-flat-base").json()["study_id"]
    r = client.put(f"/api/projects/m0-flat-base/studies/{sid}/ledger", json={
        "rows": [{"key": "energy_price_level", "value": 1.5, "unit": "multiplier"}]})
    assert r.status_code == 422, r.text
    assert "single" in r.text and "energy_price_level" in r.text
    # On a time-of-use tariff the same edit is accepted.
    r = client.patch(f"/api/projects/m0-flat-base/studies/{sid}", json={
        "step": "tariff", "intake": {"tariff": {"tariff_id": "tou_reference_illustrative"}}})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/projects/m0-flat-base/studies/{sid}/ledger", json={
        "reseed": True,
        "rows": [{"key": "energy_price_level", "value": 1.5, "unit": "multiplier"}]})
    assert r.status_code == 200, r.text


# ── Gate U2-WP6 W2: a failed creation leaves no minted export series ───────

def _minted_spy(monkeypatch) -> list[str]:
    """Record the name of every export series the creation mints."""
    from services.study import compile as study_compile

    real = study_compile.mint_export_series
    names: list[str] = []

    def spy(db, org_id, *, base_uuid, study_id, **kw):
        ref = real(db, org_id, base_uuid=base_uuid, study_id=study_id, **kw)
        names.append(study_compile.export_series_name(base_uuid, study_id))
        assert ref is not None and ref.id == names[-1]   # the seed tariff exports
        return ref

    monkeypatch.setattr(study_compile, "mint_export_series", spy)
    return names


def _series_rows(session_local, name: str) -> int:
    from sqlalchemy import select

    from db.models import LibraryItem

    with session_local() as db:
        return len(db.scalars(select(LibraryItem).where(
            LibraryItem.kind == "series", LibraryItem.name == name)).all())


def test_a_creation_that_fails_after_the_mint_leaves_no_series(
        client, api_project, studies_on, _auth_db, monkeypatch):
    from routers import studies as studies_router

    _engine, session_local = _auth_db
    api_project("m0-orphan-src")
    names = _minted_spy(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(studies_router.store, "save_study", boom)
    with pytest.raises(RuntimeError, match="disk full"):   # the original error, unmasked
        create_pack_study(client, "m0-orphan-src", "m0-orphan-base")
    assert len(names) == 1 and names[0].startswith("decision-study:")
    assert _series_rows(session_local, names[0]) == 0
    assert client.get("/api/projects/m0-orphan-base/studies/").status_code == 404


def test_a_bind_refusal_after_the_mint_leaves_no_series(
        client, api_project, studies_on, _auth_db, monkeypatch):
    _engine, session_local = _auth_db
    api_project("m0-orphan-bind")
    names = _minted_spy(monkeypatch)

    def refuse(*_a, **_k):
        raise P.PackError("library_series_unresolved", "the export series did not resolve")

    monkeypatch.setattr(P, "bind_option", refuse)
    r = create_pack_study(client, "m0-orphan-bind", "m0-orphan-bind-base")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "library_series_unresolved"
    assert len(names) == 1
    assert _series_rows(session_local, names[0]) == 0


# ── Gate U2-WP6 W1: the series is kept while the base project uses it ─────

def _base_of(session_local, body: dict):
    """(org id, export series name) of a created pack study."""
    import uuid

    from db.models import Project
    from services.study import compile as study_compile

    with session_local() as db:
        row = db.get(Project, uuid.UUID(body["base_project"]))
        return row.org_id, study_compile.export_series_name(row.id, body["study_id"])


def _put_series(session_local, org_id, name: str, price: float, source="decision_study"):
    import pandas as pd

    from services.library.export_series import put_flat_export_series

    with session_local() as db:
        return put_flat_export_series(db, org_id, name, price, source=source,
                                      snapshots=pd.date_range("2025-01-01", periods=24,
                                                              freq="h"))


def _payload_files(session_local, name: str) -> list:
    """The payload file of every version of `name` (any org)."""
    import pathlib

    from sqlalchemy import select

    from db.models import LibraryItem
    from services.storage_paths import library_file
    from settings import get_settings

    root = pathlib.Path(get_settings().projects_root)
    with session_local() as db:
        return [library_file(root, r.org_id, r.path) for r in db.scalars(select(LibraryItem).where(
            LibraryItem.kind == "series", LibraryItem.name == name)).all()]


def _unpin_base(base_dir) -> None:
    """The base no longer names the series (an expert rebound its export price)."""
    def strip(x):
        if isinstance(x, dict):
            return {k: (None if k == "export_price_ref" else strip(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x

    cfg_path = base_dir / "solver_config.json"
    cfg = json.loads(cfg_path.read_text())
    assert "decision-study:" in json.dumps(cfg)          # it did pin it
    cfg_path.write_text(json.dumps(strip(cfg)))
    (base_dir / "library_refs.json").unlink(missing_ok=True)
    assert "decision-study:" not in cfg_path.read_text()


def _created_with_two_versions(client, session_local, src: str, base: str):
    r = create_pack_study(client, src, base)
    assert r.status_code == 201, r.text
    body = r.json()
    org_id, name = _base_of(session_local, body)
    assert _put_series(session_local, org_id, name, 41.0).version == 2
    files = _payload_files(session_local, name)
    assert len(files) == 2 and all(f.is_file() for f in files)
    return body, org_id, name, files


def test_a_study_delete_keeps_every_version_while_its_untouched_base_uses_it(
        client, api_project, studies_on, _auth_db, project_row):
    _engine, session_local = _auth_db
    api_project("w1-keep-src")
    body, _org, name, files = _created_with_two_versions(
        client, session_local, "w1-keep-src", "w1-keep-base")
    r = client.delete(f"/api/projects/w1-keep-base/studies/{body['study_id']}")
    assert r.status_code == 204, r.text
    assert r.headers["X-Study-Export-Series"] == "export_series_kept_in_use"
    assert _series_rows(session_local, name) == 2
    assert all(f.is_file() for f in files)
    assert project_row("w1-keep-base") is not None


def test_a_study_delete_removes_every_version_once_the_base_no_longer_pins_it(
        client, api_project, studies_on, _auth_db, project_storage_dir):
    _engine, session_local = _auth_db
    api_project("w1-del-src")
    body, _org, name, files = _created_with_two_versions(
        client, session_local, "w1-del-src", "w1-del-base")
    _unpin_base(project_storage_dir("w1-del-base"))
    r = client.delete(f"/api/projects/w1-del-base/studies/{body['study_id']}")
    assert r.status_code == 204, r.text
    assert r.headers["X-Study-Export-Series"] == "deleted:2"
    assert _series_rows(session_local, name) == 0
    assert not any(f.exists() for f in files)


def test_a_study_delete_keeps_the_series_an_unrelated_project_pins(
        client, api_project, studies_on, _auth_db, project_storage_dir):
    from services.library import bundle_pins, series_store

    _engine, session_local = _auth_db
    api_project("w1-pin-src")
    body, org_id, name, files = _created_with_two_versions(
        client, session_local, "w1-pin-src", "w1-pin-base")
    _unpin_base(project_storage_dir("w1-pin-base"))
    api_project("w1-pin-expert")
    with session_local() as db:
        ref = series_store.ref_for(db, org_id, name, 1)
    bundle_pins.write_pins(project_storage_dir("w1-pin-expert"), [ref])
    r = client.delete(f"/api/projects/w1-pin-base/studies/{body['study_id']}")
    assert r.status_code == 204, r.text
    assert r.headers["X-Study-Export-Series"] == "export_series_kept_in_use"
    assert _series_rows(session_local, name) == 2
    assert all(f.is_file() for f in files)


def test_a_series_failure_never_fails_the_study_delete(
        client, api_project, studies_on, _auth_db, project_storage_dir, monkeypatch):
    from services.study import compile as study_compile

    _engine, session_local = _auth_db
    api_project("w1-fail-src")
    body, _org, name, _files = _created_with_two_versions(
        client, session_local, "w1-fail-src", "w1-fail-base")

    def boom(*_a, **_k):
        raise RuntimeError("library unavailable")

    monkeypatch.setattr(study_compile, "delete_export_series", boom)
    sid = body["study_id"]
    r = client.delete(f"/api/projects/w1-fail-base/studies/{sid}")
    assert r.status_code == 204, r.text
    assert r.headers["X-Study-Export-Series"] == "export_series_delete_failed"
    assert not (project_storage_dir("w1-fail-base") / "studies" / f"{sid}.json").exists()
    assert _series_rows(session_local, name) == 2


def test_the_startup_sweep_deletes_the_series_of_a_gone_base_and_keeps_the_rest(
        client, api_project, studies_on, _auth_db, project_row, project_storage_dir):
    """
    Owner (W1): deleting the BASE is what makes the series go, through the
    startup sweep. A series whose base row still exists is never touched, even
    when nothing pins it; nor is any series that is not a study's.
    """
    import uuid

    from services.study import compile as study_compile
    from services.study import forks as study_forks

    _engine, session_local = _auth_db
    api_project("w1-sweep-src")
    _live, org_id, live_name, live_files = _created_with_two_versions(
        client, session_local, "w1-sweep-src", "w1-sweep-live")
    _gone, _org, gone_name, gone_files = _created_with_two_versions(
        client, session_local, "w1-sweep-src", "w1-sweep-gone")
    # Only the base row's existence keeps the live series from here on.
    _unpin_base(project_storage_dir("w1-sweep-live"))
    # A series minted for a base that never became a project (a pre-W2
    # orphan), and series that are not a study's: a strict parse skips them.
    never = study_compile.export_series_name(uuid.uuid4(), uuid.uuid4().hex)
    _put_series(session_local, org_id, never, 40.0)
    others = ["my prices", f"decision-study:not-a-uuid:{uuid.uuid4().hex}:export",
              study_compile.export_series_name(uuid.uuid4(), uuid.uuid4().hex) + ":old"]
    for other in others:
        _put_series(session_local, org_id, other, 40.0)
    r = client.delete("/api/projects/w1-sweep-gone")
    assert r.status_code == 200, r.text
    assert project_row("w1-sweep-gone") is None
    assert _series_rows(session_local, gone_name) == 2   # project delete is not GS's

    with session_local() as db:
        assert study_forks.sweep_leftover_forks(db) == []    # no fork: the series sweep ran

    assert _series_rows(session_local, gone_name) == 0
    assert not any(f.exists() for f in gone_files)
    assert _series_rows(session_local, never) == 0
    assert _series_rows(session_local, live_name) == 2
    assert all(f.is_file() for f in live_files)
    for other in others:
        assert _series_rows(session_local, other) == 1, other
    with session_local() as db:
        assert study_compile.sweep_orphan_export_series(db) == []   # idempotent
    assert _series_rows(session_local, live_name) == 2
