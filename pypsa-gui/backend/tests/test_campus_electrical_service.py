"""Campus electrical study, the service (plan C6).

A capacity-expansion (hub) project, once solved and saved, can be taken to
its electrical design. The service lets the user:
* draft a campus description from the project's own network;
* edit and save that description;
* run the study (prepare, rank and size the campus);
* read the results back.

The service refuses:
* a project of another kind (409);
* a project not yet saved, or saved but not solved (422);
* a description that does not build (422, naming the field);
* a draft that would overwrite the user's edits without being asked (409).
"""
import uuid

import numpy as np
import pandas as pd
import pypsa
import pytest
import yaml
from fastapi import HTTPException

from db.models import Project, User
from services import campus_electrical_service as ce
from services import gridspine_service as gs
from services import project_registry

H = 12


@pytest.fixture
def user_and_db(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(User, seeded_identity["user_id"])


def hub_network(solved=True):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=H, freq="h"))
    n.add("Bus", "grid", v_nom=110.0)
    n.add("Bus", "mv", v_nom=20.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0)
    n.add("Link", "grid_import", bus0="grid", bus1="mv", p_nom=60.0, efficiency=0.99)
    n.add("Load", "dc_load", bus="mv", p_set=40.0)
    n.add("Generator", "pv", bus="mv", carrier="solar", p_nom=20.0)
    n.add("StorageUnit", "bess", bus="mv", carrier="battery", p_nom=10.0, max_hours=2.0)
    n.buses["eh_poc"] = [True, False]
    n.buses["eh_sk_mva"] = [2000.0, float("nan")]
    if solved:
        k = np.arange(H)
        pv = np.clip(np.sin(np.pi * k / H), 0, None) * 20.0
        bess = np.where(k % 2 == 0, -5.0, 5.0)
        load = np.full(H, 40.0)
        n.generators_t.p = pd.DataFrame({"pv": pv, "grid_supply": load - pv - bess}, index=n.snapshots)
        n.storage_units_t.p = pd.DataFrame({"bess": bess}, index=n.snapshots)
        n.loads_t.p = pd.DataFrame({"dc_load": load}, index=n.snapshots)
        n.links_t.p0 = pd.DataFrame({"grid_import": (load - pv - bess) / 0.99}, index=n.snapshots)
        n.links_t.p1 = -0.99 * n.links_t.p0
    return n


@pytest.fixture
def hub(user_and_db):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Campus Hub")
    hub_network().export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    return row


@pytest.fixture
def study(user_and_db):
    db, user = user_and_db
    created = gs.create_study(db, user, "Planning Study", config={"hours": 24, "k": 1, "window": 24, "overlap": 0})
    return db.get(Project, uuid.UUID(created["id"]))


@pytest.mark.parametrize("action", [
    lambda p: ce.get_state(p),
    lambda p: ce.draft(p),
    lambda p: ce.save_campus(p, "campus: {}"),
    lambda p: ce.run(p, {}),
])
def test_every_action_refuses_a_project_of_another_kind(study, action):
    with pytest.raises(HTTPException) as exc:
        action(study)
    assert exc.value.status_code == 409 and "capacity-expansion" in exc.value.detail


def test_a_project_never_saved_cannot_be_drafted(user_and_db):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Never Saved")
    with pytest.raises(HTTPException) as exc:
        ce.draft(row)
    assert exc.value.status_code == 422 and "save" in exc.value.detail


def test_a_project_saved_but_not_solved_cannot_be_drafted(user_and_db):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Unsolved")
    hub_network(solved=False).export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    with pytest.raises(HTTPException) as exc:
        ce.draft(row)
    assert exc.value.status_code == 422 and "solve" in exc.value.detail


def test_a_draft_comes_from_the_project_and_is_kept_as_the_campus_file(hub):
    out = ce.draft(hub)
    spec = yaml.safe_load(out["campus_yaml"])
    assert spec["campus"]["pcc"]["pypsa_name"] == "grid"
    state = ce.get_state(hub)
    assert state["campus_yaml"] == out["campus_yaml"] and state["results"] is None
    assert "eu_rfg_dcc_ce" in state["profiles"]


def test_drafting_again_does_not_overwrite_edits_unless_asked(hub):
    ce.draft(hub)
    with pytest.raises(HTTPException) as exc:
        ce.draft(hub)
    assert exc.value.status_code == 409 and "overwrite" in exc.value.detail
    assert ce.draft(hub, overwrite=True)["campus_yaml"]


def test_a_saved_campus_is_validated_and_a_bad_one_is_refused_with_the_field(hub):
    spec = yaml.safe_load(ce.draft(hub)["campus_yaml"])
    spec["campus"]["pcc"]["sk_min_mva"]["value"] = 1e9
    with pytest.raises(HTTPException) as exc:
        ce.save_campus(hub, yaml.safe_dump(spec))
    assert exc.value.status_code == 422 and "sk_min_mva" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        ce.save_campus(hub, "campus: [unclosed")
    assert exc.value.status_code == 422 and "YAML" in exc.value.detail
    spec["campus"]["pcc"]["sk_min_mva"]["value"] = 1500.0
    ce.save_campus(hub, yaml.safe_dump(spec))
    assert yaml.safe_load(ce.get_state(hub)["campus_yaml"]) == spec


def test_an_oversized_campus_file_is_refused(hub):
    with pytest.raises(HTTPException) as exc:
        ce.save_campus(hub, "x" * (ce.MAX_CAMPUS_BYTES + 1))
    assert exc.value.status_code == 413


def test_running_without_a_campus_asks_for_one(hub):
    with pytest.raises(HTTPException) as exc:
        ce.run(hub, {})
    assert exc.value.status_code == 422 and "campus" in exc.value.detail


def test_a_run_sizes_the_campus_and_its_results_read_back(hub):
    ce.draft(hub)
    out = ce.run(hub, {"k": 1, "pf": 0.95, "margin": 0.1, "n_minus_1": True})
    res = out["results"]
    assert [r["check"] for r in res["compliance"]] == [
        "pcc_reactive", "pcc_voltage", "campus_voltage", "transformer_loading", "switchgear"]
    assert res["transformers"][0]["margin"] == 0.1
    assert res["requirement"]["pf"] == 0.95 and res["requirement"]["source"] == "assumed"
    assert res["selection"] and {"period", "hour", "reasons"} <= set(res["selection"][0])
    assert res["short_circuit"] and res["compensation"]
    state = ce.get_state(hub)
    assert state["results"] == res and state["settings"]["pf"] == 0.95 and not state["stale"]


def test_results_turn_stale_when_the_campus_changes(hub):
    spec = yaml.safe_load(ce.draft(hub)["campus_yaml"])
    ce.run(hub, {"k": 1})
    spec["campus"]["pcc"]["sk_max_mva"]["value"] = 2500.0
    ce.save_campus(hub, yaml.safe_dump(spec))
    assert ce.get_state(hub)["stale"]


@pytest.mark.parametrize("settings, match", [
    ({"k": 0}, "k"),
    ({"pf": 1.5}, "pf"),
    ({"margin": -0.1}, "margin"),
    ({"profile": "nowhere"}, "nowhere"),
])
def test_bad_settings_are_refused(hub, settings, match):
    ce.draft(hub)
    with pytest.raises(HTTPException) as exc:
        ce.run(hub, settings)
    assert exc.value.status_code == 422 and match in exc.value.detail


def test_the_defaults_match_the_engines(hub):
    from gridspine.drivers import campus_study
    assert ce.DEFAULTS["k"] == campus_study.DEFAULT_K
    assert ce.DEFAULTS["profile"] == campus_study.DEFAULT_PROFILE


def test_a_build_without_the_engine_answers_503(hub, monkeypatch):
    monkeypatch.setattr(ce, "CAMPUS_AVAILABLE", False)
    monkeypatch.setattr(ce, "CAMPUS_IMPORT_ERROR", "no gridspine")
    with pytest.raises(HTTPException) as exc:
        ce.get_state(hub)
    assert exc.value.status_code == 503 and "no gridspine" in exc.value.detail
