"""Applying recommended GFM settings (E2E demo plan 2026-10-14 §7.2, GFM-b):
the recommended droop, τ_f / H_v (and i_max when the search was allowed to
raise it) written into the island sidecar and the campus file, tagged
``assumed`` (heuristic screening), both validated before either is written.
The island study goes (its inputs changed); the campus results go stale by
their own hash of the campus file; the hub must be re-solved, and the
recommendation says so. Only a ``recommended`` result that is still the
run's can be applied.
"""
import json
import os
import time

import pytest
import yaml
from fastapi import HTTPException

from services import campus_electrical_service as ce
from services import campus_island_tuning as cit
from services import project_registry
from tests.test_campus_island_cuts_e2e import _network, _sidecar, user_and_db  # noqa: F401


@pytest.fixture
def drafted(user_and_db):  # noqa: F811
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Tune Hub")
    d = project_registry.ensure_project_dir(row)
    _network().export_to_netcdf(str(d / "network.nc"))
    (d / "island_config.json").write_text(json.dumps(_sidecar()))
    ce.draft(row)
    return row


def _campus(project):
    return yaml.safe_load((ce.campus_dir(project) / ce.CAMPUS_FILE).read_text())["campus"]


def _uid(project, name="bess"):
    return next(uid for uid, u in _campus(project)["units"].items() if u.get("pypsa_name") == name)


def _sidecar_of(project):
    return json.loads((project_registry.project_dir(project) / "island_config.json").read_text())


def _recommend(project, *, status="recommended", allow_i_max=False, recommended=None):
    run = ce.campus_dir(project) / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / ce.cs.FREQ_EVENTS_CSV).write_text("period,hour,event\n")
    current = {"droop_pct": 4.0, "tau_f_s": 0.1, "i_max_pu": 1.2, "H_equivalent_s": 0.4}
    rec = {"label": "heuristic screening", "status": status, "allow_i_max": allow_i_max,
           "settings": {_uid(project): {"model": "gfm_droop", "current": current,
                                        "recommended": recommended or {
                                            "droop_pct": 2.5, "tau_f_s": 0.6, "i_max_pu": 1.2,
                                            "H_equivalent_s": 1.2}}}}
    later = time.time() + 2
    (run / ce.cs.GFM_SETTINGS_JSON).write_text(json.dumps(rec))
    os.utime(run / ce.cs.GFM_SETTINGS_JSON, (later, later))
    return rec


def test_apply_writes_the_sidecar_and_the_campus_file_tagged_assumed(drafted):
    _recommend(drafted)
    study = ce._island_paths(drafted)[1]
    study.write_text("{}")                                  # an island study of the old inputs
    out = cit.apply_settings(drafted)
    side = _sidecar_of(drafted)["units"]["bess"]
    assert side["droop_pct"] == {"value": 2.5, "source": "assumed"}
    assert side["tau_f_s"] == {"value": 0.6, "source": "assumed"}
    assert side["i_max_pu"] == {"value": 1.2, "source": "assumed"}        # untouched (same value)
    block = _campus(drafted)["units"][_uid(drafted)]["island"]
    assert block["droop_pct"] == {"value": 2.5, "source": "assumed"}
    assert block["tau_f_s"] == {"value": 0.6, "source": "assumed"}
    assert {(c["field"], c["from"], c["to"]) for c in out["changes"]} == \
        {("droop_pct", 4.0, 2.5), ("tau_f_s", 0.1, 0.6)}
    assert all(c["unit"] == _uid(drafted) and c["pypsa_name"] == "bess" for c in out["changes"])
    assert out["hub_resolve_needed"] is True
    # the recommendation records it; the island study went with the old inputs
    got = cit.get_settings(drafted)
    assert got["applied"]["changes"] == out["changes"] and got["applied"]["hub_resolve_needed"]
    assert not study.is_file() and ce.get_island(drafted)["study"] is None


def test_i_max_is_written_only_when_the_search_could_raise_it(drafted):
    rec = {"droop_pct": 2.5, "tau_f_s": 0.6, "i_max_pu": 1.4, "H_equivalent_s": 1.2}
    _recommend(drafted, recommended=rec)
    cit.apply_settings(drafted)
    assert _sidecar_of(drafted)["units"]["bess"]["i_max_pu"]["value"] != 1.4
    _recommend(drafted, recommended=rec, allow_i_max=True)
    cit.apply_settings(drafted)
    assert _sidecar_of(drafted)["units"]["bess"]["i_max_pu"] == {"value": 1.4, "source": "assumed"}


@pytest.mark.parametrize("status, words", [
    ("not_achievable", "capacity"), ("unchanged", "nothing to apply"), ("incomplete", "incomplete")])
def test_only_a_recommended_result_is_applied(drafted, status, words):
    _recommend(drafted, status=status)
    before = _sidecar_of(drafted)
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 422 and words in exc.value.detail
    assert _sidecar_of(drafted) == before


def test_a_stale_or_missing_recommendation_is_refused(drafted):
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 409
    _recommend(drafted)
    run = ce.campus_dir(drafted) / "run"
    later = time.time() + 10
    os.utime(run / ce.cs.FREQ_EVENTS_CSV, (later, later))
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 409 and "again" in exc.value.detail


def test_an_invalid_value_writes_neither_file(drafted):
    _recommend(drafted, recommended={"droop_pct": -1.0, "tau_f_s": 0.6, "i_max_pu": 1.2,
                                     "H_equivalent_s": 1.2})
    side, campus = _sidecar_of(drafted), _campus(drafted)
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 422
    assert _sidecar_of(drafted) == side and _campus(drafted) == campus


def test_a_unit_the_campus_file_does_not_have_is_refused(drafted):
    rec = _recommend(drafted)
    rec["settings"]["ghost"] = rec["settings"].pop(_uid(drafted))
    path = ce.campus_dir(drafted) / "run" / ce.cs.GFM_SETTINGS_JSON
    path.write_text(json.dumps(rec))
    later = time.time() + 2
    os.utime(path, (later, later))
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 422 and "ghost" in exc.value.detail


# ── #310 review: both or neither, busy projects, the re-solve record ────────


def test_a_failed_campus_write_puts_the_sidecar_back_and_keeps_the_study(drafted, monkeypatch):
    """Both files or neither: the campus write fails after the sidecar's, the
    old sidecar comes back, the island study stays, nothing is recorded."""
    import services.atomic_io as aio

    _recommend(drafted)
    study = ce._island_paths(drafted)[1]
    study.write_text("{}")
    side_before, campus_before = _sidecar_of(drafted), _campus(drafted)
    real = aio.atomic_write_text

    def failing(path, text, *a, **k):
        if str(path).endswith(ce.CAMPUS_FILE):
            raise OSError("disk full")
        return real(path, text, *a, **k)
    monkeypatch.setattr(aio, "atomic_write_text", failing)
    with pytest.raises(OSError):
        cit.apply_settings(drafted)
    assert _sidecar_of(drafted) == side_before and _campus(drafted) == campus_before
    assert study.is_file()
    assert cit.get_settings(drafted)["applied"] is None


def test_an_oversized_file_is_refused_before_anything_is_written(drafted, monkeypatch):
    _recommend(drafted)
    side_before = _sidecar_of(drafted)
    monkeypatch.setattr(ce, "MAX_CAMPUS_BYTES", 10)
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 422 and "campus file" in exc.value.detail
    assert _sidecar_of(drafted) == side_before


@pytest.mark.parametrize("busy", ["cuts", "job"])
def test_no_apply_while_the_project_is_busy(drafted, monkeypatch, busy):
    from services import campus_island_cuts as cic
    from services import solve_queue as sq

    _recommend(drafted)
    if busy == "cuts":
        monkeypatch.setattr(cic, "_running", lambda record: True)
    else:
        key = project_registry.registry_key(drafted)
        monkeypatch.setattr(sq.solve_queue, "list_jobs", lambda: [
            {"project_key": key, "kind": "campus", "status": "running"}])
    before = _sidecar_of(drafted)
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 409
    assert _sidecar_of(drafted) == before


def test_a_unit_whose_control_changed_is_refused(drafted):
    """The droop and inertia were verified for one model; a unit since
    switched to another is refused, neither field written."""
    _recommend(drafted)
    path = project_registry.project_dir(drafted) / "island_config.json"
    cfg = json.loads(path.read_text())
    cfg["units"]["bess"]["control"] = "gfm_vsm"
    cfg["units"]["bess"].pop("tau_f_s")
    cfg["units"]["bess"]["H_v_s"] = {"value": 1.0, "source": "assumed"}
    path.write_text(json.dumps(cfg))
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 422 and "gfm_droop" in exc.value.detail
    assert _campus(drafted)["units"][_uid(drafted)]["island"]["droop_pct"]["value"] == 4.0


def test_a_recommended_field_either_file_lacks_is_refused(drafted):
    _recommend(drafted)
    spec_path = ce.campus_dir(drafted) / ce.CAMPUS_FILE
    spec = yaml.safe_load(spec_path.read_text())
    spec["campus"]["units"][_uid(drafted)]["island"].pop("tau_f_s")
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    before = _sidecar_of(drafted)
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 422 and "tau_f_s" in exc.value.detail
    assert _sidecar_of(drafted) == before


def test_applying_twice_is_refused(drafted):
    _recommend(drafted)
    cit.apply_settings(drafted)
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(drafted)
    assert exc.value.status_code == 409 and "already applied" in exc.value.detail


def test_the_re_solve_line_outlives_a_campus_run_and_goes_with_a_hub_solve(drafted):
    """The applied record sits beside the campus file: a campus run (which
    removes the run's recommendation) keeps it; a saved re-solve of the hub
    (the network changes) clears it."""
    _recommend(drafted)
    cit.apply_settings(drafted)
    for name in ce._island_hours_files():                      # what a campus run removes
        (ce.campus_dir(drafted) / "run" / name).unlink(missing_ok=True)
    got = cit.get_settings(drafted)
    assert got["recommendation"] is None and got["applied"]["hub_resolve_needed"] is True
    nc = project_registry.project_dir(drafted) / ce.NETWORK_FILE
    nc.write_bytes(nc.read_bytes() + b"\0")                   # the hub re-solved and saved
    assert cit.get_settings(drafted)["applied"] is None
    assert not (ce.campus_dir(drafted) / cit.APPLIED_FILE).exists()


# ── the route ───────────────────────────────────────────────────────────────


from db.models import Project  # noqa: E402
from tests.test_campus_island_service import hub as router_hub  # noqa: E402,F401
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: E402,F401

_APPLY = "/api/campus-electrical/Router Hub/island/gfm-settings/apply"


def test_the_apply_route_calls_the_service_with_the_row(client, router_hub, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(cit, "apply_settings", lambda *a, **kw: calls.append(a) or {"changes": []})
    resp = client.post(_APPLY)
    assert resp.status_code == 200 and resp.json() == {"changes": []}
    ((row,),) = calls
    assert isinstance(row, Project) and row.name == "Router Hub"


def test_apply_is_refused_under_another_users_lock(client, router_hub, same_org_other_user,  # noqa: F811
                                                   monkeypatch):
    monkeypatch.setattr(cit, "apply_settings", lambda *a, **kw: pytest.fail("not reached"))
    assert client.post("/api/projects/Router Hub/lock").status_code == 200
    assert _is_lock_refusal(same_org_other_user.post(_APPLY))


# ── end to end on the I7 hand hub (real gridspine) ──────────────────────────


@pytest.mark.slow
def test_the_real_recommender_on_the_i7_hand_hub_says_capacity_is_needed(user_and_db):  # noqa: F811
    """The I7 hand hub fails at both hours (a 10 MW GFM BESS against a 20 MW
    step, and the trip of that BESS, E3, which no setting covers): the real
    recommender says not achievable, names the capacity, and apply refuses
    with that reason, writing nothing. (A floor of 25 or 40 MW changes
    nothing: E3 needs N-1 cover. The applied path is held to hand cases above.)"""
    from tests.test_campus_island_cuts_e2e import SETTINGS

    db, user = user_and_db
    row = project_registry.create_root(db, user, "Tune E2E Hub")
    d = project_registry.ensure_project_dir(row)
    _network().export_to_netcdf(str(d / "network.nc"))
    (d / "island_config.json").write_text(json.dumps(_sidecar()))
    ce.draft(row)
    ce.run(row, SETTINGS)
    cit.recommend(ce.campus_dir(row) / "run", max_sims=cit.MAX_SIMS)
    got = cit.get_settings(row)
    assert got["state"] == "ready" and got["applied"] is None
    rec = got["recommendation"]
    assert rec["label"].startswith("heuristic screening") and rec["simulations"] <= cit.MAX_SIMS
    assert rec["status"] == "not_achievable" and rec["achievable"] is False and rec["capacity"]
    before = (d / "island_config.json").read_text()
    with pytest.raises(HTTPException) as exc:
        cit.apply_settings(row)
    assert exc.value.status_code == 422 and "capacity is needed" in exc.value.detail
    assert (d / "island_config.json").read_text() == before
    assert cit.get_settings(row)["applied"] is None
