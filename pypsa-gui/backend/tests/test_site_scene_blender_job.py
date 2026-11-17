"""Issue 06 slice (a): the `blender_scene` queue kind, its artifact and its routes.

The worker is the fake Blender from `test_site_scene_blender_worker`. GK B's
conditions: B1 (kind safety), B4 (late output never stored), B6 (artifact
route) and the single-dispatcher cancel. Slice (a) has no path that applies
returned geometry: the scene's files are unchanged by a run.
"""

from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from db.models import SolveJobRow
from services import solve_queue as sq
from services.site_scene import blender_job
from services.site_scene import blender_worker as bw
from services.site_scene.contract import SceneError
from tests.test_site_scene_blender_worker import make  # noqa: F401 - fixture
from tests.test_site_scene_export import _scene_with_buildings
from tests.test_site_scene_routes import _empty_network, _kind

pytestmark = pytest.mark.skipif(
    __import__("sys").platform != "linux", reason="the worker is Linux-only"
)

JOBS = "/api/projects/geo/sites/campus/scene/blender-jobs"
ART = "/api/projects/geo/sites/campus/scene/artifacts/"


@pytest.fixture(autouse=True)
def _clean_queue():
    sq.solve_queue.reset_for_tests()
    bw._ready_cache.clear()
    yield
    sq.solve_queue.reset_for_tests()
    bw._ready_cache.clear()


@pytest.fixture
def geo(client, install_network, project_storage_dir):
    """The routes test's saved project with an empty network."""
    install_network(_empty_network(), name="geo")
    assert (
        client.post(
            "/api/projects/geo", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    return project_storage_dir("geo")


def _wait(pred, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _status(job_id):
    return sq.solve_queue.get_job(uuid.UUID(str(job_id)))["status"]


@pytest.fixture
def worker(make, monkeypatch):  # noqa: F811 - the imported fixture
    """Point the process env at a stub worker in `mode`."""

    def use(mode="ok"):
        env, rec = make(mode)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return rec

    return use


def _scene_files(project):
    d = project / "sites" / "campus" / "scene"
    return {
        p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()
    }


# ── B1: kind safety ──────────────────────────────────────────────────────────


def test_blender_scene_is_a_registered_kind():
    assert (
        sq.KIND_BLENDER_SCENE == "blender_scene" and sq.KIND_BLENDER_SCENE in sq.KINDS
    )


def test_a_blender_row_never_reaches_the_solve_runner(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(
        sq.SolveQueue, "_run_solve_job", lambda self, job: ran.append(job.id)
    )
    monkeypatch.delenv(bw.ENV_IMAGE, raising=False)
    job = sq.solve_queue.enqueue(
        "p",
        project_key="p",
        storage_dir=str(tmp_path),
        solver_config_json=blender_job.spec("campus", "p"),
        kind=sq.KIND_BLENDER_SCENE,
    )
    assert _wait(lambda: _status(job.id) in sq._TERMINAL)
    got = sq.solve_queue.get_job(job.id)
    assert ran == [] and got["status"] == "failed" and got["kind"] == "blender_scene"
    assert got["error"].startswith(bw.UNAVAILABLE)


def test_restore_keeps_the_kind_and_an_unknown_kind_fails_closed(tmp_path):
    row = {
        "id": str(uuid.uuid4()),
        "project_id": "p",
        "project_key": "p",
        "status": "queued",
        "storage_dir": str(tmp_path),
        "solver_config": blender_job.spec("campus", "p"),
        "kind": sq.KIND_BLENDER_SCENE,
        "enqueued_at": time.time(),
    }
    job, created = sq.solve_queue.restore(row)
    assert created and job.kind == sq.KIND_BLENDER_SCENE
    with pytest.raises(ValueError, match=sq.UNKNOWN_KIND_CONDITION):
        sq.solve_queue.restore({**row, "id": str(uuid.uuid4()), "kind": "blender"})


def test_a_running_blender_job_is_interrupted_on_boot_and_not_redispatched(
    _auth_db, seeded_identity, tmp_path, monkeypatch
):
    from services import solve_job_store

    called = []
    monkeypatch.setattr(
        sq.SolveQueue, "_run_blender_scene_job", lambda self, job: called.append(job.id)
    )
    _engine, session_local = _auth_db
    job_id = uuid.uuid4()
    with session_local() as db:
        db.add(
            SolveJobRow(
                id=job_id,
                project_id="p",
                project_key="p",
                kind=sq.KIND_BLENDER_SCENE,
                status="running",
                storage_dir=str(tmp_path),
                solver_config=blender_job.spec("campus", "p"),
                enqueued_by_user_id=seeded_identity["user_id"],
                enqueued_at=datetime.now(tz=UTC),
            )
        )
        db.commit()
    interrupted, _resumed = solve_job_store.reconcile_on_boot()
    assert interrupted >= 1
    with session_local() as db:
        row = db.get(SolveJobRow, job_id)
        assert (row.status, row.kind) == ("interrupted", sq.KIND_BLENDER_SCENE)
    time.sleep(0.3)
    assert called == []


# ── the routes ───────────────────────────────────────────────────────────────


def test_with_no_container_worker_the_route_is_503_and_names_why(
    client, geo, monkeypatch
):
    _scene_with_buildings(client, geo)
    monkeypatch.delenv(bw.ENV_IMAGE, raising=False)
    r = client.post(JOBS)
    assert r.status_code == 503 and _kind(r) == "worker_unavailable"
    assert bw.ENV_IMAGE in r.json()["detail"]["message"]
    assert sq.solve_queue.list_jobs() == []


def test_a_run_stores_an_artifact_that_downloads_and_leaves_the_scene_unchanged(
    client, geo, worker
):
    _scene_with_buildings(client, geo)
    worker("ok")
    before = _scene_files(geo)
    r = client.post(JOBS)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == "blender_scene"
    assert _wait(lambda: _status(job["id"]) in sq._TERMINAL)
    assert _status(job["id"]) == "completed", sq.solve_queue.get_job(
        uuid.UUID(job["id"])
    )
    assert _scene_files(geo) == before, "slice (a) never writes the scene"

    d = client.get(ART + job["id"])
    assert d.status_code == 200, d.text
    assert d.headers["content-type"] == "application/zip"
    assert d.headers["content-disposition"].startswith("attachment;")
    z = zipfile.ZipFile(io.BytesIO(d.content))
    assert sorted(z.namelist()) == ["meta.json", "scene.blend", "scene.glb"]
    meta = json.loads(z.read("meta.json"))
    pointer = json.loads((geo / "sites/campus/scene/current.json").read_text())
    assert (meta["revision"], meta["generation"], meta["generation_sha256"]) == (
        pointer["revision"],
        pointer["generation"],
        pointer["sha256"],
    )
    assert meta["artifact_id"] == job["id"] and meta["site_id"] == "campus"
    assert z.read("scene.blend") == b"BLENDER-v502"
    import hashlib

    assert hashlib.sha256(z.read("scene.glb")).hexdigest() == meta["export_glb_sha256"]
    assert not list((geo / blender_job.ARTIFACTS).glob(".tmp-*"))


def test_a_second_job_while_one_is_active_is_409(client, geo, worker):
    _scene_with_buildings(client, geo)
    worker("hang")
    first = client.post(JOBS).json()
    r = client.post(JOBS)
    assert r.status_code == 409 and _kind(r) == "job_active"
    sq.solve_queue.abort(uuid.UUID(first["id"]))
    assert _wait(lambda: _status(first["id"]) in sq._TERMINAL, timeout=20)


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-uuid",
        "x" * 36,
        str(uuid.uuid4()).upper(),
        str(uuid.uuid4()).replace("-", ""),
    ],
)
def test_an_artifact_id_that_is_not_a_canonical_uuid_is_404(client, geo, bad):
    _scene_with_buildings(client, geo)
    r = client.get(ART + bad)
    assert r.status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        "..",
        "../meta.json",
        "../../secrets",
        "/etc/passwd",
        "",
        None,
        "{" + str(uuid.uuid4()) + "}",
    ],
)
def test_artifact_dir_never_builds_a_path_from_a_non_uuid(tmp_path, bad):
    with pytest.raises(Exception) as e:
        blender_job.artifact_dir(tmp_path, bad)
    assert getattr(e.value, "kind", None) == "not_found"


def test_an_artifact_of_another_site_or_project_is_404(client, geo, worker, tmp_path):
    _scene_with_buildings(client, geo)
    aid = str(uuid.uuid4())
    result = bw.Result(b"glb", b"blend", "")
    blender_job._store(
        geo, aid, result, {"artifact_id": aid, "site_id": "other-site", "revision": 1}
    )
    assert client.get(ART + aid).status_code == 404, "another site's artifact"
    other = tmp_path / "elsewhere"
    blender_job._store(
        other, aid, result, {"artifact_id": aid, "site_id": "campus", "revision": 1}
    )
    with pytest.raises(Exception) as e:
        blender_job.read_artifact(geo, "campus", str(uuid.uuid4()))
    assert getattr(e.value, "kind", None) == "not_found"


def test_a_symlinked_artifact_file_is_404(client, geo, tmp_path):
    _scene_with_buildings(client, geo)
    aid = str(uuid.uuid4())
    blender_job._store(
        geo, aid, bw.Result(b"g", b"b", ""), {"artifact_id": aid, "site_id": "campus"}
    )
    secret = tmp_path / "secret"
    secret.write_text("x")
    blend = geo / blender_job.ARTIFACTS / aid / "scene.blend"
    blend.unlink()
    blend.symlink_to(secret)
    assert client.get(ART + aid).status_code == 404


# ── B4: late output ──────────────────────────────────────────────────────────


def test_output_that_arrives_after_a_cancel_is_never_stored(
    client, geo, monkeypatch, worker
):
    _scene_with_buildings(client, geo)
    worker("ok")
    stop = __import__("threading").Event()
    real = bw.run

    def finishes_then_cancelled(*a, **k):
        out = real(*a, **k)
        stop.set()
        return out

    monkeypatch.setattr(bw, "run", finishes_then_cancelled)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        storage_dir=str(geo),
        solver_config_json=blender_job.spec("campus", "geo"),
    )
    with pytest.raises(bw.WorkerError) as e:
        blender_job.run_queued(job, stop)
    assert e.value.kind == bw.CANCELLED
    assert not (geo / blender_job.ARTIFACTS / str(job.id)).exists()


# ── one dispatcher: a cancel frees it for the solve behind ───────────────────


def test_cancel_frees_the_dispatcher_within_the_grace_so_a_queued_solve_starts(
    client, geo, worker, monkeypatch, tmp_path
):
    _scene_with_buildings(client, geo)
    rec = worker("hang")
    started = []
    monkeypatch.setattr(
        sq.SolveQueue,
        "_run_solve_job",
        lambda self, job: (
            started.append(time.monotonic()),
            self._finish(job, "completed"),
        ),
    )
    scene = client.post(JOBS).json()
    assert _wait(
        lambda: __import__("pathlib").Path(str(rec) + ".kid").exists(), timeout=20
    )
    solve = sq.solve_queue.enqueue(
        "other", project_key="other", storage_dir=str(tmp_path)
    )
    order = [j["id"] for j in sq.solve_queue.list_jobs()]
    assert order.index(scene["id"]) < order.index(str(solve.id)), (
        "the scene job is ahead of the solve"
    )
    assert started == []
    t0 = time.monotonic()
    sq.solve_queue.abort(uuid.UUID(scene["id"]))
    assert _wait(lambda: bool(started), timeout=15)
    assert started[0] - t0 < bw.Caps().grace_s + 3
    assert _status(scene["id"]) == "aborted"


def test_a_cancel_that_lands_while_storing_rolls_the_artifact_back(
    client, geo, monkeypatch, worker
):
    _scene_with_buildings(client, geo)
    worker("ok")
    stop = __import__("threading").Event()
    real = blender_job._store

    def store_then_cancel(*a, **k):
        real(*a, **k)
        stop.set()

    monkeypatch.setattr(blender_job, "_store", store_then_cancel)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        storage_dir=str(geo),
        solver_config_json=blender_job.spec("campus", "geo"),
    )
    with pytest.raises(bw.WorkerError) as e:
        blender_job.run_queued(job, stop)
    assert e.value.kind == bw.CANCELLED
    assert not (geo / blender_job.ARTIFACTS / str(job.id)).exists()


def test_an_unexpected_error_is_served_without_host_paths(monkeypatch, tmp_path):
    def boom(job, stop):
        raise OSError(13, "Permission denied", str(tmp_path / "secret-host-path"))

    monkeypatch.setattr(blender_job, "run_queued", boom)
    job = sq.solve_queue.enqueue(
        "p",
        project_key="p",
        storage_dir=str(tmp_path),
        solver_config_json=blender_job.spec("campus", "p"),
        kind=sq.KIND_BLENDER_SCENE,
    )
    assert _wait(lambda: _status(job.id) in sq._TERMINAL)
    got = sq.solve_queue.get_job(job.id)
    assert got["status"] == "failed" and got["error"].startswith(bw.FAILED)
    assert "secret-host-path" not in got["error"] and str(tmp_path) not in got["error"]


def test_a_store_that_fails_partway_leaves_neither_a_temp_nor_an_artifact(tmp_path):
    aid = str(uuid.uuid4())
    result = bw.Result(glb=b"g", blend=b"b", log_tail="")
    with pytest.raises(TypeError):
        blender_job._store(tmp_path, aid, result, {"unserialisable": object()})
    parent = blender_job.artifact_dir(tmp_path, aid).parent
    assert list(parent.iterdir()) == []


def test_only_a_member_of_the_project_reaches_the_jobs_and_artifacts(
    client, anon_client, other_org_client, geo, worker
):
    _scene_with_buildings(client, geo)
    worker("ok")
    job = client.post(JOBS).json()
    assert _wait(lambda: _status(job["id"]) in sq._TERMINAL)
    assert client.get(ART + job["id"]).status_code == 200
    assert anon_client.get(ART + job["id"]).status_code == 401
    assert anon_client.post(JOBS).status_code in (401, 403)
    r = other_org_client.get(ART + job["id"])
    assert r.status_code == 404 and b"BLENDER" not in r.content, r.status_code
    assert other_org_client.post(JOBS).status_code == 404


def test_an_unknown_kind_is_a_typed_refusal_and_known_kinds_pass():
    for kind in sq.KINDS:
        sq.check_kind(kind)
    with pytest.raises(ValueError) as e:
        sq.check_kind("blender_scene_v2")
    assert str(e.value).startswith(sq.UNKNOWN_KIND_CONDITION)


def test_no_mount_source_comes_from_the_project_or_the_request(
    client, geo, worker, monkeypatch
):
    import tempfile

    _scene_with_buildings(client, geo)
    worker("ok")
    seen = []
    real = bw._container_argv

    def spy(ready, name, work, tail, caps):
        argv = real(ready, name, work, tail, caps)
        seen.append([argv[i + 1] for i, a in enumerate(argv) if a == "--mount"])
        return argv

    monkeypatch.setattr(bw, "_container_argv", spy)
    job = client.post(JOBS).json()
    assert _wait(lambda: _status(job["id"]) in sq._TERMINAL)
    assert _status(job["id"]) == "completed"
    assert seen, "the worker ran through the spied argv"
    tmp = Path(tempfile.gettempdir()).resolve()
    tree = Path(bw.os.environ[bw.ENV_DIR])
    for mounts in seen:
        work, blender = (
            dict(kv.split("=", 1) for kv in m.split(",") if "=" in kv) for m in mounts
        )
        assert Path(work["source"]).parent.resolve() == tmp
        assert Path(work["source"]).name.startswith("blender-run-")
        assert Path(blender["source"]) == tree
        for m in mounts:
            assert str(geo) not in m and "campus" not in m and job["id"] not in m


def test_a_timed_out_job_fails_typed_with_no_artifact_and_frees_the_dispatcher(
    client, geo, worker, monkeypatch, tmp_path
):
    _scene_with_buildings(client, geo)
    worker("hang")
    monkeypatch.setitem(
        blender_job.run_queued.__kwdefaults__,
        "caps",
        bw.Caps(wall_s=2.0, grace_s=1.0, poll_s=0.05, version_timeout_s=5.0),
    )
    started = []
    monkeypatch.setattr(
        sq.SolveQueue,
        "_run_solve_job",
        lambda self, job: (
            started.append(time.monotonic()),
            self._finish(job, "completed"),
        ),
    )
    scene = client.post(JOBS).json()
    solve = sq.solve_queue.enqueue(
        "other", project_key="other", storage_dir=str(tmp_path)
    )
    assert _wait(lambda: _status(scene["id"]) in sq._TERMINAL, timeout=30)
    got = sq.solve_queue.get_job(uuid.UUID(scene["id"]))
    assert got["status"] == "failed" and got["error"].startswith(bw.TIMEOUT), got
    assert not (geo / blender_job.ARTIFACTS / scene["id"]).exists()
    assert not list((geo / blender_job.ARTIFACTS).glob(".tmp-*"))
    assert _wait(lambda: bool(started), timeout=15), "the queued solve starts"
    assert _status(str(solve.id)) == "completed"


def test_a_symlinked_artifact_dir_is_404(client, geo, tmp_path):
    _scene_with_buildings(client, geo)
    aid = str(uuid.uuid4())
    elsewhere = tmp_path / "elsewhere"
    blender_job._store(
        elsewhere,
        aid,
        bw.Result(b"g", b"b", ""),
        {"artifact_id": aid, "site_id": "campus"},
    )
    store = geo / blender_job.ARTIFACTS
    store.mkdir(parents=True, exist_ok=True)
    (store / aid).symlink_to(elsewhere / blender_job.ARTIFACTS / aid)
    assert client.get(ART + aid).status_code == 404


# ── slice (b): GK B's pins on the artifact read ─────────────────────────────


def test_a_symlinked_artifact_store_is_404(client, geo, tmp_path):
    _scene_with_buildings(client, geo)
    aid = str(uuid.uuid4())
    elsewhere = tmp_path / "elsewhere"
    blender_job._store(
        elsewhere,
        aid,
        bw.Result(b"g", b"b", ""),
        {"artifact_id": aid, "site_id": "campus"},
    )
    (geo / blender_job.ARTIFACTS).symlink_to(elsewhere / blender_job.ARTIFACTS)
    assert client.get(ART + aid).status_code == 404


def test_meta_json_is_always_read_under_its_own_small_cap(geo, monkeypatch):
    aid = str(uuid.uuid4())
    blender_job._store(
        geo, aid, bw.Result(b"g", b"b", ""), {"artifact_id": aid, "site_id": "campus"}
    )
    caps = []
    real = bw._read_output

    def spy(work, name, cap):
        caps.append((name, cap))
        return real(work, name, cap)

    monkeypatch.setattr(bw, "_read_output", spy)
    blender_job.read_artifact(geo, "campus", aid)
    meta_caps = [c for n, c in caps if n == "meta.json"]
    assert meta_caps and set(meta_caps) == {blender_job.META_CAP}
    assert blender_job.META_CAP < bw.Caps().output_bytes


def test_an_oversized_meta_json_is_404(geo):
    aid = str(uuid.uuid4())
    pad = "x" * blender_job.META_CAP
    blender_job._store(
        geo,
        aid,
        bw.Result(b"g", b"b", ""),
        {"artifact_id": aid, "site_id": "campus", "pad": pad},
    )
    with pytest.raises(SceneError) as e:
        blender_job.read_artifact(geo, "campus", aid)
    assert e.value.kind == "not_found"
