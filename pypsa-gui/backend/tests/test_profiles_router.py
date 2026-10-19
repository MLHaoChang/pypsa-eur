"""`/api/profiles` + `/api/projects/{name}/profiles` — the profile generator's
HTTP surface (Task 7).

The routes are a thin layer over the pieces Tasks 1–6 pinned:

  * `GET /api/profiles/presets`        — the preset catalogue (Task 4)
  * `POST /api/profiles/estimate`      — a SYNTHETIC, downsampled preview,
    labelled an estimate; never enqueues, never touches a project
  * `POST /api/projects/{name}/profiles/generate` — enqueue a `profile_gen`
    solve-queue job (Task 6) against the project's authorized storage_dir
  * `GET  /api/projects/{name}/profiles/pending`  — the pending artifacts
  * `POST /api/projects/{name}/profiles/apply`    — write one pending profile
    onto a generator through `assign_profile` (Task 5), with an undo snapshot
    pushed FIRST (the middleware only covers /api/network/ and /api/io/) and
    the project lock enforced like every other project write edge

Authorization behaves like every other project-scoped router: another org's
project is a 404, never a 403.
"""
from __future__ import annotations

import time
import uuid

import pandas as pd
import pypsa
import pytest

from services import solve_queue as sq

SNAPS_2025 = pd.date_range("2025-01-01", periods=8760, freq="h")


def _wait(predicate, timeout=60.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _wind_network() -> pypsa.Network:
    n = pypsa.Network()
    idx = SNAPS_2025.copy()
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Carrier", "wind")
    n.add("Generator", "g_wind", bus="B1", carrier="wind", p_nom=100.0,
          marginal_cost=0.0)
    return n


def _save_as(test_client, name: str):
    resp = test_client.post(
        f"/api/projects/{name}", params={"force": True, "rebind": True}
    )
    assert resp.status_code == 200, resp.text
    return resp


@pytest.fixture
def wind_project(client, install_network):
    """A saved, activated project holding one wind generator at (8.0, 50.0)."""
    install_network(_wind_network())
    _save_as(client, "Profile Farm")
    return "Profile Farm"


def _spec(**over):
    spec = {
        "points": [{"id": "g_wind", "lon": 8.0, "lat": 50.0}],
        "technology": "wind",
        "preset": "default",
        "source": "synthetic",
        "year": 2023,
    }
    spec.update(over)
    return spec


def _generate(client, name, spec=None):
    return client.post(
        f"/api/projects/{name}/profiles/generate",
        json=spec if spec is not None else _spec(),
    )


def _job_view(client, job_id):
    jobs = client.get("/api/simulation/queue").json()
    rows = jobs["jobs"] if isinstance(jobs, dict) else jobs
    for row in rows:
        if row.get("id") == job_id:
            return row
    return None


def _await_job(client, job_id):
    assert _wait(
        lambda: (row := _job_view(client, job_id)) is not None
        and row["status"] in sq._TERMINAL
    )
    return _job_view(client, job_id)


# ---------------------------------------------------------------------------
# presets + estimate (unauthenticated-resource shape, no project)
# ---------------------------------------------------------------------------


def test_presets_lists_the_catalogue(client):
    resp = client.get("/api/profiles/presets")
    assert resp.status_code == 200, resp.text
    presets = resp.json()["presets"]
    assert presets, "expected at least one preset"
    wind = [p for p in presets if p["technology"] == "wind"]
    assert wind and all("params" in p and "name" in p for p in wind)


def test_presets_filter_by_technology(client):
    resp = client.get("/api/profiles/presets", params={"technology": "solar"})
    assert resp.status_code == 200
    assert all(p["technology"] == "solar" for p in resp.json()["presets"])


def test_estimate_returns_a_downsampled_labelled_preview(client):
    resp = client.post("/api/profiles/estimate", json=_spec())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estimate"] is True
    assert body["source"] == "synthetic"
    # Downsampled: far fewer points than the 8760 the real artifact carries.
    series = body["series"]["g_wind"]
    assert 200 <= len(series) <= 400  # daily means of a non-leap year
    assert 0.0 <= min(series) and max(series) <= 1.0


def test_estimate_refuses_an_unsupported_technology(client):
    resp = client.post("/api/profiles/estimate", json=_spec(technology="nuclear"))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "unsupported_technology"
    assert "wind" in detail["message"] and "solar" in detail["message"]


# ---------------------------------------------------------------------------
# generate → job
# ---------------------------------------------------------------------------


def test_generate_enqueues_a_profile_job_and_the_artifact_lands(
    client, wind_project, project_storage_dir, seeded_identity,
):
    resp = _generate(client, wind_project)
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["kind"] == "profile_gen"

    done = _await_job(client, job["id"])
    assert done["status"] == "completed", done["error"]

    pending = project_storage_dir(
        wind_project, seeded_identity["org_id"]
    ) / "profiles_pending"
    assert (pending / f"{job['id']}.nc").exists()
    assert (pending / f"{job['id']}.json").exists()


def test_a_second_generate_while_one_is_queued_is_a_409(client, wind_project,
                                                        monkeypatch):
    import threading

    from services.profile_gen import job as job_mod

    entered = threading.Event()
    release = threading.Event()
    orig = job_mod.generate_profile

    def slow(*args, **kwargs):
        entered.set()
        release.wait(30)
        return orig(*args, **kwargs)

    monkeypatch.setattr(job_mod, "generate_profile", slow)
    try:
        first = _generate(client, wind_project)
        assert first.status_code == 202, first.text
        assert _wait(lambda: entered.is_set(), timeout=30)

        second = _generate(client, wind_project)
        assert second.status_code == 409, second.text
    finally:
        release.set()


def test_generate_with_the_atlite_source_but_no_cds_key_is_a_422(
    client, wind_project, monkeypatch,
):
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    monkeypatch.setattr(
        "services.profile_gen.cutout_cache._read_cdsapirc", lambda: (None, None)
    )
    resp = _generate(client, wind_project, _spec(source="atlite"))
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "cds.climate.copernicus.eu" in str(detail)


def test_generate_validates_the_spec_in_the_request(client, wind_project):
    resp = _generate(client, wind_project, _spec(technology="nuclear"))
    assert resp.status_code == 422, resp.text


def test_generate_on_another_orgs_project_is_a_404(other_org_client):
    resp = other_org_client.post(
        "/api/projects/Profile Farm/profiles/generate", json=_spec()
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# pending
# ---------------------------------------------------------------------------


@pytest.fixture
def completed_job(client, wind_project, seeded_identity):
    resp = _generate(client, wind_project)
    assert resp.status_code == 202, resp.text
    job = resp.json()
    done = _await_job(client, job["id"])
    assert done["status"] == "completed", done["error"]
    return job


def test_pending_lists_the_generated_profile(client, wind_project,
                                             completed_job):
    resp = client.get(f"/api/projects/{wind_project}/profiles/pending")
    assert resp.status_code == 200, resp.text
    pending = resp.json()["pending"]
    mine = [p for p in pending if p["job_id"] == completed_job["id"]]
    assert len(mine) == 1
    assert mine[0]["technology"] == "wind"
    assert mine[0]["weather_year"] == 2023
    assert mine[0]["points"] == ["g_wind"]


def test_pending_on_another_orgs_project_is_a_404(other_org_client):
    resp = other_org_client.get("/api/projects/Profile Farm/profiles/pending")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_apply_writes_the_profile_onto_the_generator(client, wind_project,
                                                     completed_job,
                                                     session_ctx):
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/apply",
        json={"job_id": completed_job["id"], "component": "g_wind"},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["component"] == "g_wind"
    assert summary["attribute"] == "p_max_pu"
    assert summary["hours"] == 8760
    assert 0.0 < summary["mean"] < 1.0

    ctx = session_ctx(client)
    # The store write lands on the context's own dict (the tenancy rule).
    assert ("generators", "p_max_pu", "g_wind") in ctx.user_ts
    # The network itself carries the aligned bound, not just the store.
    assert ctx.network.generators_t.p_max_pu["g_wind"].notna().all()


def test_apply_pushes_an_undo_snapshot_first(client, wind_project,
                                             completed_job):
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/apply",
        json={"job_id": completed_job["id"], "component": "g_wind"},
    )
    assert resp.status_code == 200, resp.text

    undo = client.post("/api/network/undo")
    assert undo.status_code == 200, undo.text
    # The generator's time-varying bound is gone again.
    payload = client.get("/api/network/timeseries/generators/p_max_pu").json()
    assert "g_wind" not in payload["columns"]


def test_apply_to_a_missing_component_is_a_typed_refusal(client, wind_project,
                                                         completed_job):
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/apply",
        json={"job_id": completed_job["id"], "component": "deleted_gen"},
    )
    assert resp.status_code == 422, resp.text
    assert "deleted_gen" in str(resp.json()["detail"])


def test_apply_to_a_gas_generator_is_refused(client, install_network,
                                             completed_job):
    n = _wind_network()
    n.add("Generator", "g_gas", bus="B1", carrier="gas", p_nom=50.0)
    install_network(n)

    resp = client.post(
        "/api/projects/Profile Farm/profiles/apply",
        json={"job_id": completed_job["id"], "component": "g_gas"},
    )
    assert resp.status_code == 422, resp.text


def test_apply_with_an_unknown_job_id_is_a_404(client, wind_project):
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/apply",
        json={"job_id": str(uuid.uuid4()), "component": "g_wind"},
    )
    assert resp.status_code == 404, resp.text


def test_apply_on_another_orgs_project_is_a_404(other_org_client):
    resp = other_org_client.post(
        "/api/projects/Profile Farm/profiles/apply",
        json={"job_id": str(uuid.uuid4()), "component": "g_wind"},
    )
    assert resp.status_code == 404


def test_apply_warns_when_the_bus_moved_since_generation(client,
                                                         install_network,
                                                         completed_job):
    """The artifact was generated for (8.0, 50.0); the generator's bus now
    sits somewhere else, so the answer carries a warning — but still applies
    (the user may have deliberately re-sited the asset)."""
    n = _wind_network()
    n.buses.loc["B1", "x"] = 12.5  # moved ~4.5° east after generation
    install_network(n)

    resp = client.post(
        "/api/projects/Profile Farm/profiles/apply",
        json={"job_id": completed_job["id"], "component": "g_wind"},
    )
    assert resp.status_code == 200, resp.text
    warning = resp.json().get("warning")
    assert warning and "moved" in warning.lower()


def test_apply_warns_when_the_bus_lost_its_coordinates(client,
                                                       install_network,
                                                       completed_job):
    """A bus reset to the 0/0 default after generation is no 'move' to
    (0, 0): the warning says the coordinates are gone."""
    n = _wind_network()
    n.buses.loc["B1", ["x", "y"]] = [0.0, 0.0]
    install_network(n)

    resp = client.post(
        "/api/projects/Profile Farm/profiles/apply",
        json={"job_id": completed_job["id"], "component": "g_wind"},
    )
    assert resp.status_code == 200, resp.text
    assert "no coordinates now" in (resp.json().get("warning") or "")


# ---------------------------------------------------------------------------
# C2: resolution through the HTTP surface
# ---------------------------------------------------------------------------


def test_estimate_honours_daily_resolution(client):
    resp = client.post("/api/profiles/estimate", json=_spec(resolution="daily"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolution"] == "daily"
    assert len(body["series"]["g_wind"]) == 365  # 2023, non-leap


def test_estimate_honours_weekly_resolution(client):
    resp = client.post("/api/profiles/estimate", json=_spec(resolution="weekly"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolution"] == "weekly"
    assert len(body["series"]["g_wind"]) == 53


def test_estimate_default_resolution_is_hourly_downsampled(client):
    resp = client.post("/api/profiles/estimate", json=_spec())
    assert resp.status_code == 200
    assert resp.json()["resolution"] == "hourly"


def test_estimate_refuses_an_unsupported_resolution(client):
    resp = client.post(
        "/api/profiles/estimate", json=_spec(resolution="fortnightly"),
    )
    assert resp.status_code == 422, resp.text


def test_generate_with_daily_resolution_round_trips_to_apply(
    client, wind_project, session_ctx,
):
    """Generate at daily resolution, wait for the queued job, apply onto the
    hourly network: the summary reports 8760 upsampled hours and the
    daily provenance."""
    resp = _generate(client, wind_project, _spec(resolution="daily"))
    assert resp.status_code == 202, resp.text
    job = resp.json()
    done = _await_job(client, job["id"])
    assert done["status"] == "completed", done["error"]

    pending = client.get(
        f"/api/projects/{wind_project}/profiles/pending"
    ).json()["pending"]
    mine = [p for p in pending if p["job_id"] == job["id"]]
    assert mine and mine[0]["resolution"] == "daily"

    applied = client.post(
        f"/api/projects/{wind_project}/profiles/apply",
        json={"job_id": job["id"], "component": "g_wind"},
    )
    assert applied.status_code == 200, applied.text
    summary = applied.json()
    assert summary["resolution"] == "daily"
    assert summary["hours"] == 8760  # the 2025 network year, upsampled

    ctx = session_ctx(client)
    col = ctx.network.generators_t.p_max_pu["g_wind"]
    day = col.loc["2025-03-05T00:00":"2025-03-05T23:00"]
    assert (day == day.iloc[0]).all(), "a daily profile holds 24 equal hours"


def test_generate_refuses_an_unsupported_resolution(client, wind_project):
    resp = _generate(client, wind_project, _spec(resolution="annual"))
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# E1: from_generators on the HTTP routes (the GUI default path shares the
# chat tools' resolver — one code path in routers/profiles.py)
# ---------------------------------------------------------------------------


def test_estimate_from_generators_resolves_bus_coordinates(client,
                                                           wind_project):
    """The global estimate resolves generator names against the foreground
    network; the deterministic synthetic source must then match the
    explicit-points call exactly."""
    explicit = client.post("/api/profiles/estimate", json=_spec()).json()
    resp = client.post(
        "/api/profiles/estimate",
        json={"from_generators": ["g_wind"], "technology": "wind",
              "year": 2023},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["series"]["g_wind"] == explicit["series"]["g_wind"]


def test_estimate_without_points_or_generators_is_a_422(client):
    resp = client.post("/api/profiles/estimate",
                       json={"technology": "wind"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_kind"] == "no_points"


def test_estimate_with_an_unknown_generator_is_a_422(client, wind_project):
    resp = client.post(
        "/api/profiles/estimate",
        json={"from_generators": ["no_such_gen"], "technology": "wind"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "unknown_generator"
    assert "no_such_gen" in detail["message"]


def test_generate_from_generators_records_the_coordinates(
    client, wind_project, project_storage_dir, seeded_identity,
):
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/generate",
        json={"from_generators": ["g_wind"], "technology": "wind",
              "year": 2023, "source": "synthetic"},
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()
    done = _await_job(client, job["id"])
    assert done["status"] == "completed", done["error"]

    pending = client.get(
        f"/api/projects/{wind_project}/profiles/pending"
    ).json()["pending"]
    mine = [p for p in pending if p["job_id"] == job["id"]]
    assert len(mine) == 1
    assert mine[0]["points"] == ["g_wind"]
    assert mine[0]["point_coords"]["g_wind"] == [8.0, 50.0]


def test_generate_without_points_or_generators_is_a_422(client, wind_project):
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/generate",
        json={"technology": "wind"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_kind"] == "no_points"


# ---------------------------------------------------------------------------
# RP-01: coordinates come from the generator, else its bus; a missing pair
# (NaN, or PyPSA's default 0/0) is a typed refusal, never a site at 0°E 0°N
# ---------------------------------------------------------------------------


def _mixed_network() -> pypsa.Network:
    """g_wind at (8.0, 50.0); g_nocoord on a bus left at PyPSA's default
    x=y=0; g_nan on a bus with no latitude; g_far on a bus out of range."""
    n = _wind_network()
    n.add("Bus", "B0")  # x = y = 0, PyPSA's default
    n.add("Bus", "BN", x=9.0, y=51.0)
    # n.add() fills NaN with the 0 default; an import or a direct edit can
    # still leave a NaN behind, so set it after the add
    n.buses.loc["BN", "y"] = float("nan")
    n.add("Bus", "BF", x=9.0, y=91.0)
    n.add("Generator", "g_nocoord", bus="B0", carrier="wind", p_nom=10.0)
    n.add("Generator", "g_nan", bus="BN", carrier="wind", p_nom=10.0)
    n.add("Generator", "g_far", bus="BF", carrier="wind", p_nom=10.0)
    return n


@pytest.fixture
def mixed_project(client, install_network):
    install_network(_mixed_network())
    _save_as(client, "Coord Farm")
    return "Coord Farm"


def _estimate(client, names):
    return client.post(
        "/api/profiles/estimate",
        json={"from_generators": names, "technology": "wind", "year": 2023},
    )


def test_a_bus_at_the_default_origin_is_a_missing_coordinates_refusal(
    client, mixed_project,
):
    resp = _estimate(client, ["g_nocoord"])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "missing_coordinates"
    assert detail["generators"] == ["g_nocoord"]
    assert "B0" in detail["message"]
    # both remedies are named
    assert "set coordinates on the bus" in detail["message"]
    assert "upload a profile" in detail["message"]


def test_a_bus_with_a_nan_latitude_is_a_missing_coordinates_refusal(
    client, mixed_project,
):
    resp = _estimate(client, ["g_nan"])
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "missing_coordinates"
    assert resp.json()["detail"]["generators"] == ["g_nan"]


def test_the_refusal_lists_only_the_generators_missing_coordinates(
    client, mixed_project,
):
    resp = _estimate(client, ["g_wind", "g_nocoord", "g_nan"])
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["generators"] == ["g_nocoord", "g_nan"]


def test_a_bus_out_of_range_is_a_missing_coordinates_refusal(
    client, mixed_project,
):
    resp = _estimate(client, ["g_far"])
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["generators"] == ["g_far"]


def test_an_explicit_point_overrides_a_generator_without_coordinates(
    client, mixed_project,
):
    resp = client.post(
        "/api/profiles/estimate",
        json={"points": [{"id": "g_nocoord", "lon": 9.2, "lat": 52.3}],
              "from_generators": ["g_nocoord"],
              "technology": "wind", "year": 2023},
    )
    assert resp.status_code == 200, resp.text
    assert list(resp.json()["series"]) == ["g_nocoord"]


def test_an_explicit_point_does_not_hide_an_unknown_generator(
    client, mixed_project,
):
    resp = client.post(
        "/api/profiles/estimate",
        json={"points": [{"id": "g_typo", "lon": 9.2, "lat": 52.3}],
              "from_generators": ["g_typo"],
              "technology": "wind", "year": 2023},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "unknown_generator"
    assert "g_typo" in resp.json()["detail"]["message"]


def test_generate_for_a_generator_without_coordinates_queues_nothing(
    client, mixed_project, project_storage_dir, seeded_identity,
):
    resp = client.post(
        f"/api/projects/{mixed_project}/profiles/generate",
        json={"from_generators": ["g_wind", "g_nocoord"],
              "technology": "wind", "year": 2023, "source": "synthetic"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "missing_coordinates"
    pending = client.get(
        f"/api/projects/{mixed_project}/profiles/pending"
    ).json()
    assert pending["pending"] == []
    assert pending.get("jobs", []) == []


# ---------------------------------------------------------------------------
# RP-06: shaping writes a NEW pending artifact (never the network); apply is
# still the only writer and keeps the calibration provenance
# ---------------------------------------------------------------------------


def _shape(client, name, job_id, ops, **extra):
    return client.post(f"/api/projects/{name}/profiles/shape",
                       json={"job_id": job_id, "ops": ops, **extra})


def test_shape_writes_a_new_pending_artifact_and_not_the_network(
    client, wind_project, completed_job,
):
    resp = _shape(client, wind_project, completed_job["id"],
                  [{"op": "target_flh", "hours": 2000}])
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["job_id"] != completed_job["id"]
    assert out["parent_job"] == completed_job["id"]
    assert abs(out["stats"]["g_wind"]["result_stats"]["flh"] - 2000) < 10

    pending = client.get(f"/api/projects/{wind_project}/profiles/pending"
                         ).json()["pending"]
    shaped = [p for p in pending if p["job_id"] == out["job_id"]]
    assert len(shaped) == 1
    assert shaped[0]["calibrated"] is True
    assert shaped[0]["parent_job"] == completed_job["id"]
    # the network is untouched until apply
    payload = client.get("/api/network/timeseries/generators/p_max_pu").json()
    assert "g_wind" not in payload["columns"]


def test_applying_a_shaped_artifact_keeps_its_calibration_provenance(
    client, wind_project, completed_job, session_ctx,
):
    shaped = _shape(client, wind_project, completed_job["id"],
                    [{"op": "tail_damp", "side": "low", "beyond_q": 0.2,
                      "factor": 0.5}]).json()
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/apply",
        json={"job_id": shaped["job_id"], "component": "g_wind"},
    )
    assert resp.status_code == 200, resp.text

    meta = session_ctx(client).user_ts_meta[
        ("generators", "p_max_pu", "g_wind")]
    assert meta["calibrated"] is True
    assert meta["parent_job"] == completed_job["id"]
    assert meta["shaping"]["g_wind"]["ops"][0]["op"] == "tail_damp"


def test_shape_with_an_unreachable_target_is_a_typed_refusal(
    client, wind_project, completed_job,
):
    resp = _shape(client, wind_project, completed_job["id"],
                  [{"op": "target_flh", "hours": 8700}])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "unreachable_target"
    assert detail["reachable_max"] < 8760


def test_shape_with_an_unknown_op_is_a_typed_refusal(client, wind_project,
                                                      completed_job):
    resp = _shape(client, wind_project, completed_job["id"],
                  [{"op": "make_it_windier"}])
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "invalid_shape_op"


def test_shape_with_an_unknown_job_is_a_404(client, wind_project):
    resp = _shape(client, wind_project, "no-such-job",
                  [{"op": "target_flh", "hours": 2000}])
    assert resp.status_code == 404


def test_shape_on_another_orgs_project_is_a_404(other_org_client):
    resp = _shape(other_org_client, "Profile Farm", "any",
                  [{"op": "target_flh", "hours": 2000}])
    assert resp.status_code == 404
