"""project-io 03, weather part (owner Q4; GK B3 W1–W7).

A project keeps the series it generated, not the cutout, and records in its
metadata which cutout each applied target's series came from: the cutout's own
extent, year and sha256 (`profile_cutouts`).
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.profile_gen import cutout_digest as CD
from services.profile_gen import job as J
from services.profile_gen import weather_prefs as W

SHA = "a" * 64


def _cutout(path: Path, x=(5.0, 12.0), y=(48.0, 55.0), year=2023) -> Path:
    import xarray as xr

    xs = np.linspace(*x, 8)
    ys = np.linspace(*y, 8)
    times = pd.date_range(f"{year}-01-01", periods=4, freq="h")
    ds = xr.Dataset(
        {"influx": (("time", "y", "x"), np.zeros((len(times), len(ys), len(xs))))},
        coords={"time": times, "y": ys, "x": xs},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path)
    return path


def _entry(**kw) -> dict:
    return {
        "component_type": "generators",
        "component": "g_a",
        "attribute": "p_max_pu",
        "module": "era5",
        "bbox": [5.0, 48.0, 12.0, 55.0],
        "year": 2023,
        "sha256": SHA,
        "applied_at": "2026-11-18T00:00:00+00:00",
        **kw,
    }


# ── the cutout's record (W1, W2) ────────────────────────────────────────────


def test_the_record_is_the_cutouts_own_extent_year_and_bytes(tmp_path):
    path = _cutout(tmp_path / "era5_0123456789ab_2023.nc")
    got = CD.cutout_record(path)
    assert got == {
        "module": "era5",
        "bbox": [5.0, 48.0, 12.0, 55.0],
        "year": 2023,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_a_cutout_replaced_while_recorded_is_refused(tmp_path, monkeypatch):
    path = _cutout(tmp_path / "era5_x_2023.nc")
    real = CD.os.stat

    def moved(p, *a, **k):
        st = real(p, *a, **k)
        return SimpleNamespace(
            st_dev=st.st_dev,
            st_ino=st.st_ino + 1,
            st_size=st.st_size,
            st_mtime_ns=st.st_mtime_ns,
        )

    monkeypatch.setattr(CD.os, "stat", moved)
    with pytest.raises(CD.CutoutChanged):
        CD.cutout_record(path)


def test_a_file_that_is_not_a_cutout_is_a_value_error(tmp_path):
    import xarray as xr

    path = tmp_path / "era5_x_2023.nc"
    xr.Dataset({"v": (("t",), [1.0])}, coords={"t": [0]}).to_netcdf(path)
    with pytest.raises(ValueError, match="not a cutout"):
        CD.cutout_record(path)


@pytest.mark.parametrize(
    "dims",
    [
        {"time": 1, "y": 0, "x": 2},
        {"time": 1, "y": 2, "x": 0},
        {"time": 0, "y": 2, "x": 2},
    ],
    ids=["no-y", "no-x", "no-time"],
)
def test_an_empty_grid_is_a_value_error(tmp_path, dims):
    import xarray as xr

    path = tmp_path / "era5_x_2023.nc"
    xr.Dataset(
        {"v": (("time", "y", "x"), np.zeros((dims["time"], dims["y"], dims["x"])))},
        coords={
            "time": pd.date_range("2023-01-01", periods=dims["time"]),
            "y": np.arange(dims["y"], dtype=float),
            "x": np.arange(dims["x"], dtype=float),
        },
    ).to_netcdf(path)
    with pytest.raises(ValueError, match="empty"):
        CD.cutout_record(path)


# ── the job puts it in the pending record (W1, W3) ─────────────────────────


def _atlite_job(tmp_path, points):
    spec = {"points": points, "technology": "wind", "source": "atlite", "year": 2023}
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id="p",
        storage_dir=str(tmp_path / "proj"),
        solver_config_json=json.dumps(spec),
    )


def _fake_generate(points, technology, **kw):
    ids = [p["id"] for p in points]
    idx = pd.date_range("2023-01-01", periods=4, freq="h")
    return SimpleNamespace(
        series=pd.DataFrame({i: [0.5] * 4 for i in ids}, index=idx),
        meta={"source": kw.get("source"), "technology": technology},
    )


@pytest.fixture
def atlite_job(tmp_path, monkeypatch):
    cutout = _cutout(tmp_path / "cache" / "era5_0123456789ab_2023.nc")
    asked = []

    def ensure(bbox, year, **kw):
        asked.append(bbox)
        return cutout

    monkeypatch.setattr(J, "ensure_cutout", ensure)
    monkeypatch.setattr(J, "generate_profile", _fake_generate)
    monkeypatch.setattr(J, "_resolve_params", lambda spec: {})
    points = [
        {"id": "g_a", "lon": 8.0, "lat": 50.0},
        {"id": "g_b", "lon": 9.0, "lat": 51.0},
    ]
    return _atlite_job(tmp_path, points), cutout, asked


def test_an_atlite_job_records_the_cutout_it_was_generated_from(atlite_job):
    job, cutout, asked = atlite_job
    _nc, meta = J.run_queued(job, None, None)
    record = json.loads(Path(meta).read_text())
    # The cutout is larger than the points' bbox: its own extent is recorded.
    assert asked == [(8.0, 50.0, 9.0, 51.0)]
    assert record["cutout"] == CD.cutout_record(cutout)
    assert record["cutout"]["bbox"] == [5.0, 48.0, 12.0, 55.0]


def test_a_cutout_that_cannot_be_recorded_leaves_it_out_and_the_job_succeeds(
    atlite_job, monkeypatch
):
    job, _cutout_path, _asked = atlite_job

    def boom(path):
        raise OSError("unreadable")

    monkeypatch.setattr(CD, "cutout_record", boom)
    _nc, meta = J.run_queued(job, None, None)
    assert "cutout" not in json.loads(Path(meta).read_text())


def test_a_synthetic_job_records_no_cutout(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "generate_profile", _fake_generate)
    monkeypatch.setattr(J, "_resolve_params", lambda spec: {})
    monkeypatch.setattr(J, "ensure_cutout", lambda *a, **k: pytest.fail("no cutout"))
    job = _atlite_job(tmp_path, [{"id": "g_a", "lon": 8.0, "lat": 50.0}])
    job.solver_config_json = json.dumps(
        {**json.loads(job.solver_config_json), "source": "synthetic"}
    )
    _nc, meta = J.run_queued(job, None, None)
    assert "cutout" not in json.loads(Path(meta).read_text())


# ── the project's record (W3, W4, W5, W6) ──────────────────────────────────


def _write_meta(d: Path, meta: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(meta))


@pytest.mark.parametrize(
    "bad",
    [
        {"sha256": "A" * 64},
        {"sha256": "a" * 63},
        {"sha256": "a" * 64 + "\n"},
        {"sha256": None},
        {"bbox": [5.0, 48.0, 12.0]},
        {"bbox": [12.0, 48.0, 5.0, 55.0]},
        {"bbox": [5.0, 55.0, 12.0, 48.0]},
        {"bbox": [5.0, 48.0, 5.0, 55.0]},
        {"bbox": [5.0, 48.0, 12.0, 48.0]},
        {"bbox": [5.0, 48.0, float("nan"), 55.0]},
        {"bbox": [5.0, 48.0, float("inf"), 55.0]},
        {"bbox": [True, 48.0, 12.0, 55.0]},
        {"bbox": ["5", 48.0, 12.0, 55.0]},
        {"bbox": [None, 48.0, 12.0, 55.0]},
        {"bbox": "5,48,12,55"},
        {"year": "2023"},
        {"year": True},
        {"year": 2023.0},
        {"module": 7},
        {"component": None},
        {"component_type": 1},
        {"attribute": ["p_max_pu"]},
    ],
)
def test_a_malformed_entry_is_dropped_on_read(tmp_path, bad):
    good = _entry(component="g_b")
    _write_meta(tmp_path, {W.CUTOUTS_KEY: [_entry(**bad), good, "junk"]})
    assert W.recorded_cutouts(tmp_path) == [good]


@pytest.mark.parametrize("value", [None, {}, "x", 7])
def test_a_record_that_is_not_a_list_reads_as_none(tmp_path, value):
    _write_meta(tmp_path, {W.CUTOUTS_KEY: value})
    assert W.recorded_cutouts(tmp_path) == []


def test_reading_writes_nothing(tmp_path):
    _write_meta(tmp_path, {W.CUTOUTS_KEY: [_entry(sha256="bad"), _entry()]})
    before = (tmp_path / "metadata.json").read_bytes()
    W.recorded_cutouts(tmp_path)
    assert (tmp_path / "metadata.json").read_bytes() == before


CUT = {"module": "era5", "bbox": [5.0, 48.0, 12.0, 55.0], "year": 2023, "sha256": SHA}
A = ("generators", "g_a", "p_max_pu")
B = ("generators", "g_b", "p_max_pu")


def test_reapplying_a_target_replaces_its_entry_and_other_targets_stay(tmp_path):
    _write_meta(tmp_path, {"profile_weather": {"source": "atlite", "year": 2023}})
    W.record_cutouts(tmp_path, [A, B], CUT, "t1")
    W.record_cutouts(tmp_path, [A], {**CUT, "sha256": "b" * 64, "year": 2022}, "t2")
    got = {
        (e["component"], e["sha256"], e["year"], e["applied_at"])
        for e in W.recorded_cutouts(tmp_path)
    }
    assert got == {("g_a", "b" * 64, 2022, "t2"), ("g_b", SHA, 2023, "t1")}
    meta = json.loads((tmp_path / "metadata.json").read_text())
    assert meta["profile_weather"] == {"source": "atlite", "year": 2023}


def test_a_series_with_no_cutout_removes_the_targets_entry(tmp_path):
    W.record_cutouts(tmp_path, [A, B], CUT, "t1")
    W.record_cutouts(tmp_path, [A], None, "t2")
    assert [e["component"] for e in W.recorded_cutouts(tmp_path)] == ["g_b"]
    W.record_cutouts(tmp_path, [B], None, "t3")
    assert W.CUTOUTS_KEY not in json.loads((tmp_path / "metadata.json").read_text())


def test_a_malformed_cutout_is_not_recorded(tmp_path):
    W.record_cutouts(tmp_path, [A], CUT, "t1")
    W.record_cutouts(tmp_path, [A], {**CUT, "sha256": "nope"}, "t2")
    assert W.recorded_cutouts(tmp_path) == []


def test_one_write_for_many_targets_and_none_when_nothing_changes(
    tmp_path, monkeypatch
):
    writes = []
    real = W.atomic_write_text
    monkeypatch.setattr(
        W, "atomic_write_text", lambda p, t: (writes.append(p), real(p, t))
    )
    W.record_cutouts(tmp_path, [A, B], CUT, "t1")
    assert len(writes) == 1
    W.record_cutouts(tmp_path, [A, B], CUT, "t1")
    W.record_cutouts(tmp_path, [("loads", "l", "p_set")], None, "t2")
    assert len(writes) == 1
    (tmp_path / "metadata.json").unlink()
    W.record_cutouts(tmp_path, [A], None, "t3")
    assert len(writes) == 1 and not (tmp_path / "metadata.json").exists()


# ── the apply routes (W3, W5, W6) ──────────────────────────────────────────

SNAPS = pd.date_range("2025-01-01", periods=8760, freq="h", name="snapshot")
POINTS = [
    {"id": "g_a", "lon": 8.0, "lat": 50.0},
    {"id": "g_b", "lon": 9.0, "lat": 51.0},
]


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(SNAPS)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Carrier", ["wind"])
    for g in ("g_a", "g_b"):
        n.add("Generator", g, bus="B1", carrier="wind", p_nom=10.0)
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    resp = client.post(
        "/api/projects/Cutout Farm", params={"force": True, "rebind": True}
    )
    assert resp.status_code == 200, resp.text
    return "Cutout Farm"


def _job(client, project, pdir: Path, cutout=CUT) -> str:
    """A completed synthetic job whose pending record names `cutout`, as an
    atlite job's would."""
    from services import solve_queue as sq

    resp = client.post(
        f"/api/projects/{project}/profiles/generate",
        json={"points": POINTS, "technology": "wind", "year": 2023},
    )
    assert resp.status_code in (200, 202), resp.text
    job_id = resp.json()["id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        job = sq.solve_queue.get_job(uuid.UUID(job_id))
        if job and job["status"] in sq._TERMINAL:
            break
        time.sleep(0.1)
    assert job["status"] == "completed", job.get("error")
    if cutout is not None:
        _nc, meta = J._artifact_paths(pdir, job_id)
        meta.write_text(json.dumps({**json.loads(meta.read_text()), "cutout": cutout}))
    return job_id


def _apply(client, project, job_id, component="g_a"):
    return client.post(
        f"/api/projects/{project}/profiles/apply",
        json={"job_id": job_id, "component": component},
    )


def _batch(client, project, job_id):
    return client.post(
        f"/api/projects/{project}/profiles/apply-batch",
        json={
            "job_id": job_id,
            "members": [{"component": "g_a"}, {"component": "g_b"}],
        },
    )


def _targets(pdir):
    return {e["component"]: e["sha256"] for e in W.recorded_cutouts(pdir)}


def test_apply_records_the_cutout_and_the_weather_readout_lists_it(
    client, project, project_storage_dir
):
    pdir = project_storage_dir(project)
    r = _apply(client, project, _job(client, project, pdir))
    assert r.status_code == 200, r.text
    assert _targets(pdir) == {"g_a": SHA}
    got = client.get(f"/api/projects/{project}/profiles/weather")
    assert got.status_code == 200, got.text
    [entry] = got.json()["cutouts"]
    assert {k: entry[k] for k in ("component", "bbox", "year", "sha256")} == {
        "component": "g_a",
        "bbox": CUT["bbox"],
        "year": 2023,
        "sha256": SHA,
    }


def test_a_later_series_with_no_cutout_removes_the_targets_record(
    client, project, project_storage_dir
):
    pdir = project_storage_dir(project)
    assert _apply(client, project, _job(client, project, pdir)).status_code == 200
    assert (
        _apply(client, project, _job(client, project, pdir, cutout=None)).status_code
        == 200
    )
    assert _targets(pdir) == {}


def test_a_batch_records_every_member_in_one_write(
    client, project, project_storage_dir, monkeypatch
):
    pdir = project_storage_dir(project)
    job_id = _job(client, project, pdir)
    writes = []
    real = W.atomic_write_text
    monkeypatch.setattr(
        W, "atomic_write_text", lambda p, t: (writes.append(p), real(p, t))
    )
    r = _batch(client, project, job_id)
    assert r.status_code == 200, r.text
    assert _targets(pdir) == {"g_a": SHA, "g_b": SHA}
    assert len(writes) == 1


def test_a_batch_that_fails_midway_records_only_what_it_wrote(
    client, project, project_storage_dir, monkeypatch
):
    from services.profile_gen import apply as apply_mod
    from routers import profiles

    pdir = project_storage_dir(project)
    job_id = _job(client, project, pdir)
    real = apply_mod.assign_profile

    def flaky(ctx, component, *a, **k):
        if component == "g_b":
            raise RuntimeError("disk full")
        return real(ctx, component, *a, **k)

    monkeypatch.setattr(profiles, "assign_profile", flaky)
    r = _batch(client, project, job_id)
    assert r.status_code == 409, r.text
    assert _targets(pdir) == {"g_a": SHA}


def test_a_refused_apply_writes_no_record(
    client, project, project_storage_dir, monkeypatch
):
    from fastapi import HTTPException

    from routers import projects

    pdir = project_storage_dir(project)
    job_id = _job(client, project, pdir)
    before = (pdir / "metadata.json").read_bytes()

    def locked(*a, **k):
        raise HTTPException(status_code=423, detail="locked by someone else")

    monkeypatch.setattr(projects, "_check_project_lock", locked)
    assert _apply(client, project, job_id).status_code == 423
    assert _batch(client, project, job_id).status_code == 423
    assert (pdir / "metadata.json").read_bytes() == before


def test_a_record_that_cannot_be_written_never_fails_the_apply(
    client, project, project_storage_dir, monkeypatch
):
    pdir = project_storage_dir(project)
    job_id = _job(client, project, pdir)

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(W, "atomic_write_text", boom)
    assert _apply(client, project, job_id).status_code == 200


# ── it travels with the project (W4) ───────────────────────────────────────


def test_a_bundle_carries_the_record_and_an_import_keeps_only_valid_entries(
    client, project, project_storage_dir
):
    from tests.bundle_util import refresh_manifest

    pdir = project_storage_dir(project)
    assert _apply(client, project, _job(client, project, pdir)).status_code == 200
    data = client.get(f"/api/projects/{project}/bundle").content
    src = zipfile.ZipFile(io.BytesIO(data))
    meta = json.loads(src.read("metadata.json"))
    assert [e["component"] for e in meta[W.CUTOUTS_KEY]] == ["g_a"]
    meta[W.CUTOUTS_KEY].append(_entry(component="g_b", sha256="../../etc"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in src.namelist():
            zf.writestr(m, json.dumps(meta) if m == "metadata.json" else src.read(m))
    r = client.post(
        "/api/projects/import_bundle?name=cutout_there",
        files={"file": ("b.zip", refresh_manifest(buf.getvalue()), "application/zip")},
    )
    assert r.status_code == 200, r.text
    assert _targets(project_storage_dir("cutout_there")) == {"g_a": SHA}
