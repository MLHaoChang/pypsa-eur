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

import json
import time
import uuid

import pandas as pd
import pypsa
import pytest

from db.models import User
from services import project_registry
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


def test_apply_reports_the_time_basis_it_used(client, wind_project,
                                             completed_job):
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/apply",
        json={"job_id": completed_job["id"], "component": "g_wind",
              "time_basis": "Europe/Berlin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["time_basis"] == "Europe/Berlin"


def test_apply_with_an_unknown_time_basis_is_a_typed_refusal(
    client, wind_project, completed_job,
):
    resp = client.post(
        f"/api/projects/{wind_project}/profiles/apply",
        json={"job_id": completed_job["id"], "component": "g_wind",
              "time_basis": "Mars/Olympus"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error_kind"] == "invalid_time_basis"


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
