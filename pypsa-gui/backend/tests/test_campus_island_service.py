"""
I9a of the campus island plan: the island sidecar and the island study,
read, written and run through the campus electrical service and its routes.

- ``get_island(project)``: the raw sidecar (or None), and the last stored
  study (or None);
- ``save_island(project, config)``: validated by gridspine, then written; a
  bad one is a 422 naming the field and leaves the previous file;
- ``run_island_study(project, scenarios=None)``: ``island_study`` on the
  saved project, stored beside the campus run and returned.

The routes are a wrapper layer, as for the rest of the campus study: each
calls its one service function; the writes check the edit lock.
"""
import json

import pytest
from fastapi import HTTPException

from db.models import Project, User
from services import campus_electrical_service as ce
from services import project_registry
from tests import test_campus_electrical_service as _ces
from tests.test_island_rows import _hub
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401

user_and_db = _ces.user_and_db                       # the campus service's fixture


def _a(v):
    return {"value": v, "source": "assumed"}


_LIMITS = {"rocof_hz_per_s": _a(1e6), "f_min_hz": _a(47.5), "f_max_hz": _a(51.5),
           "qss_band_hz": _a(1e6)}
_GENSET = {"kind": "genset", "governor": "droop", "droop_pct": _a(4.0), "q_droop_pct": _a(4.0),
           "H_s": _a(1.5), "T_g_s": _a(0.5), "ramp_pu_per_s": _a(0.2), "start_s": _a(10.0),
           "sync_s": _a(5.0), "load_step_max_pct": _a(50.0), "cos_phi_r": _a(0.8),
           "xd_sat": _a(2.0), "excitation": "avr_pmg", "neutral_earthing": "solid"}
_BESS = {"kind": "bess", "control": "gfm_droop", "pf_rated": _a(0.95), "droop_pct": _a(4.0),
         "q_droop_pct": _a(4.0), "tau_f_s": _a(0.1), "i_max_pu": _a(1.2), "k_gfm_min": _a(1.0)}


def _config(scenarios=("ups_bridged", "seamless")):
    """The raw sidecar of the I2c hand case: a 10 MW import, a cold 20 MW
    genset, an extendable BESS at 100/MW."""
    return {"units": {"bess": dict(_BESS), "crit": {"kind": "load", "critical": True},
                      "g": dict(_GENSET)},
            "requirements": {"bridge_s": _a(1.0), "sustained_h": _a(1.0),
                             "rocof_window_ms": _a(500.0), "gfm_margin": _a(0.0),
                             "genset_redundancy_n": _a(0), "pickup_blocks": _a([1.0]),
                             "scenarios": list(scenarios),
                             "frequency_limits": {"transition": dict(_LIMITS),
                                                  "steady_island": dict(_LIMITS)}}}


@pytest.fixture
def project(user_and_db):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Island Hub")
    n = _hub(load=10.0, gensets=[("g", 20.0)])
    n.export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    return row


def _sidecar_path(project):
    return project_registry.project_dir(project) / "island_config.json"


# ── the sidecar ─────────────────────────────────────────────────────────────


def test_a_project_without_a_sidecar_has_no_island_config_and_no_study(project):
    assert ce.get_island(project) == {"config": None, "study": None}


def test_the_sidecar_round_trips_as_written(project):
    out = ce.save_island(project, _config())
    assert out["config"] == _config()
    assert json.loads(_sidecar_path(project).read_text()) == _config()
    assert ce.get_island(project)["config"] == _config()


def test_a_sidecar_that_does_not_validate_is_422_naming_the_field_and_keeps_the_old_one(project):
    ce.save_island(project, _config())
    bad = _config()
    del bad["requirements"]["pickup_blocks"]                  # ups_bridged needs them
    with pytest.raises(HTTPException) as exc:
        ce.save_island(project, bad)
    assert exc.value.status_code == 422 and "pickup_blocks" in exc.value.detail
    assert json.loads(_sidecar_path(project).read_text()) == _config()


def test_an_oversized_sidecar_is_413(project):
    big = _config()
    big["units"].update({f"load{i}": {"kind": "load", "critical": False}
                         for i in range(ce.MAX_ISLAND_BYTES // 30)})
    with pytest.raises(HTTPException) as exc:
        ce.save_island(project, big)
    assert exc.value.status_code == 413


study = _ces.study                                   # a project of another kind


@pytest.mark.parametrize("action", [
    lambda p: ce.get_island(p),
    lambda p: ce.save_island(p, _config()),
    lambda p: ce.run_island_study(p),
])
def test_every_island_action_refuses_a_project_of_another_kind(study, action):
    with pytest.raises(HTTPException) as exc:
        action(study)
    assert exc.value.status_code == 409 and "capacity-expansion" in exc.value.detail


# ── the study ───────────────────────────────────────────────────────────────


def test_the_study_runs_on_the_saved_project_and_is_stored(project):
    """The I2c hand case: ups_bridged costs nothing over the baseline;
    seamless needs 10 MW of BESS headroom at 100/MW."""
    ce.save_island(project, _config())
    out = ce.run_island_study(project)
    assert out["cost_of_island_capability"]["ups_bridged"] == pytest.approx(0.0, abs=1e-4)
    assert out["cost_of_seamless"] == pytest.approx(1000.0, abs=1e-4)
    assert out["variants"]["seamless"]["status"] == "ok"
    stored = ce.get_island(project)["study"]
    assert stored["cost_of_seamless"] == pytest.approx(1000.0, abs=1e-4)
    json.dumps(stored)                                         # plain JSON all the way down


def test_the_study_can_name_scenarios_the_sidecar_does_not_ship(project):
    ce.save_island(project, _config(scenarios=("ups_bridged",)))
    out = ce.run_island_study(project, scenarios=["ups_bridged", "seamless"])
    assert out["cost_of_seamless"] == pytest.approx(1000.0, abs=1e-4)


def test_a_study_without_a_sidecar_is_422(project):
    with pytest.raises(HTTPException) as exc:
        ce.run_island_study(project)
    assert exc.value.status_code == 422 and "island_config" in exc.value.detail


def test_saving_a_new_sidecar_drops_the_stale_study(project):
    ce.save_island(project, _config())
    ce.run_island_study(project)
    ce.save_island(project, _config(scenarios=("seamless",)))
    assert ce.get_island(project)["study"] is None


# ── the routes ──────────────────────────────────────────────────────────────


@pytest.fixture
def hub(client, _auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project_registry.create_root(db, user, "Router Hub")


@pytest.mark.parametrize("method, path, function, payload, expected_args", [
    ("get", "/island", "get_island", None, ()),
    ("put", "/island", "save_island", {"config": {"units": {}}}, ({"units": {}},)),
    ("post", "/island/study", "run_island_study", {}, (None,)),
    ("post", "/island/study", "run_island_study", {"scenarios": ["seamless"]}, (["seamless"],)),
])
def test_each_island_route_calls_its_service_function_with_the_row(client, hub, monkeypatch, method,
                                                                   path, function, payload,
                                                                   expected_args):
    calls = []
    monkeypatch.setattr(ce, function, lambda *args: calls.append(args) or {"ok": function})
    resp = getattr(client, method)(f"/api/campus-electrical/Router Hub{path}",
                                   **({"json": payload} if payload is not None else {}))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": function}
    project, *rest = calls[0]
    assert isinstance(project, Project) and project.name == "Router Hub"
    assert tuple(rest) == expected_args


def test_a_bad_scenario_is_422_before_the_service(client, hub, monkeypatch):
    monkeypatch.setattr(ce, "run_island_study", lambda *a: pytest.fail("not reached"))
    resp = client.post("/api/campus-electrical/Router Hub/island/study",
                       json={"scenarios": ["blackout"]})
    assert resp.status_code == 422


def test_the_island_writes_are_refused_under_another_users_lock_and_the_read_is_not(
        client, hub, same_org_other_user, monkeypatch):  # noqa: F811
    for function in ("get_island", "save_island", "run_island_study"):
        monkeypatch.setattr(ce, function, lambda *a: {"ok": True})
    assert client.post("/api/projects/Router Hub/lock").status_code == 200
    other = same_org_other_user
    for method, path, payload in [("put", "/island", {"config": {}}),
                                  ("post", "/island/study", {})]:
        resp = getattr(other, method)(f"/api/campus-electrical/Router Hub{path}", json=payload)
        assert _is_lock_refusal(resp), (method, path, resp.status_code, resp.text)
    assert other.get("/api/campus-electrical/Router Hub/island").status_code == 200


def test_another_orgs_project_cannot_reach_the_island_routes(other_org_client, hub):
    for method, path in (("get", "/island"), ("put", "/island"), ("post", "/island/study")):
        kw = {"json": {"config": {}}} if method != "get" else {}
        resp = getattr(other_org_client, method)(f"/api/campus-electrical/Router Hub{path}", **kw)
        assert resp.status_code == 404, (path, resp.status_code)
