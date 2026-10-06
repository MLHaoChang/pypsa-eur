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
