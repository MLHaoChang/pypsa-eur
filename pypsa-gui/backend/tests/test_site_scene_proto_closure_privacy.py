"""
SC-00 N, D03 closure / D05 private media / V55 / V60.

The leakage probes run the REAL bundle export (`routers.projects.
_project_bundle_bytes`) and the real Save-As/scenario/snapshot directory copy
(`_copy_bundle_dirs`) over a project whose private originals sit in the
proposed private store. Marked bytes, their hash, an EXIF marker, the original
filename and the private path must not appear in any output.
"""
from __future__ import annotations

import copy
import hashlib
import io
import uuid
import zipfile

import pytest

from routers import projects as projects_router
from services import site_service
from tests.site_scene_proto import media_policy as media
from tests.site_scene_proto import scene_store as store
from tests.site_scene_proto.contract import SceneError

ORG, PROJECT, OTHER_PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
MARKER = b"SC00N-PRIVATE-ORIGINAL-7f3a"
EXIF = b"Exif\x00\x00GPSLatitude-SC00N"
FILENAME = "IMG_secret_rooftop.jpg"
SITE = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]], "placements": {}}


@pytest.fixture
def world(tmp_path):
    app_data, projects_root = tmp_path / "app", tmp_path / "projects"
    project = projects_root / str(PROJECT)
    project.mkdir(parents=True)
    (project / "network.nc").write_bytes(b"not-a-real-network")
    site_service.write_sites(project, {"version": 1, "sites": [copy.deepcopy(SITE)]})
    media_id = uuid.uuid4()
    original = media.media_path(app_data, [projects_root], ORG, PROJECT, media_id)
    original.mkdir(parents=True)
    (original / "original").write_bytes(b"\xff\xd8" + EXIF + MARKER + FILENAME.encode())
    store.adopt(project, "campus", store.legacy_site_digest(site_service.read_sites(project), "campus"))
    record = media.source_record(media_id, "photo")
    store.apply(project, "campus", {"id": "p1", "base_revision": 1, "input_hash": "h",
                                    "changes": {"buildings": [{"id": "layout:1", "source_record": record}]}}, 1)
    return {"app_data": app_data, "root": projects_root, "project": project, "original": original,
            "media_id": media_id, "record": record}


def _leaks(blob: bytes, world) -> list[str]:
    needles = {"bytes": MARKER, "exif": EXIF, "filename": FILENAME.encode(),
               "hash": hashlib.sha256(MARKER).hexdigest().encode(),
               "private_path": str(world["original"]).encode(), "store_dir": media.PRIVATE_MEDIA_DIR.encode()}
    return [k for k, v in needles.items() if v in blob]


def test_the_private_store_is_outside_every_projects_root(world):
    assert not world["original"].is_relative_to(world["root"])
    with pytest.raises(SceneError) as e:
        media.media_path(world["root"], [world["root"]], ORG, PROJECT, uuid.uuid4())
    assert e.value.kind == "integrity_error"


@pytest.mark.parametrize("org,proj", [(None, PROJECT), (ORG, None)])
def test_a_flat_project_without_owner_identity_cannot_hold_media(tmp_path, org, proj):
    with pytest.raises(SceneError) as e:
        media.media_path(tmp_path, [], org, proj, uuid.uuid4())
    assert e.value.kind == "forbidden" and e.value.detail == {"reason": "no_owner_identity"}


@pytest.mark.parametrize("bad", ["../../etc", "Campus", "", 7])
def test_identities_are_uuids_never_names_or_paths(tmp_path, bad):
    with pytest.raises(SceneError):
        media.media_path(tmp_path, [], ORG, bad, uuid.uuid4())


def test_scene_state_holds_only_an_opaque_source_record(world):
    assert set(world["record"]) == {"source_id", "kind", "state", "reason"}
    gen = (world["project"] / store.closure(world["project"], "campus")[1]).read_bytes()
    assert _leaks(gen, world) == [] and str(world["media_id"]).encode() in gen


def test_the_real_bundle_export_carries_no_original(world):
    blob = projects_router._project_bundle_bytes("p", src=world["project"])
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert "sites/campus/scene/current.json" in names
    unpacked = b"".join(zipfile.ZipFile(io.BytesIO(blob)).read(n) for n in names)
    assert _leaks(blob, world) == [] and _leaks(unpacked, world) == []


def test_the_real_save_as_copy_carries_no_original(world, tmp_path):
    dst = tmp_path / "copy"
    dst.mkdir()
    projects_router._copy_bundle_dirs(world["project"], dst)
    copied = b"".join(p.read_bytes() for p in dst.rglob("*") if p.is_file())
    assert _leaks(copied, world) == []


@pytest.mark.parametrize("op,state", [("snapshot", "available"), ("restore", "available"),
                                      ("scenario", "unavailable"), ("save_as", "unavailable"),
                                      ("bundle_export", "unavailable"), ("bundle_import", "unavailable")])
def test_each_copy_operation_marks_the_original_as_contracts_3_says(world, op, state):
    out = media.for_copy([world["record"]], op)
    assert out[0]["state"] == state and out[0]["source_id"] == world["record"]["source_id"]
    if state == "unavailable":
        assert out[0]["reason"] == "original_not_copied"


def test_every_read_reauthorizes_the_handle(world):
    ok = dict(owner_project_id=PROJECT, caller_project_id=PROJECT, caller_can_read=True, age_days=1, deleted=False)
    assert media.resolve(world["record"], **ok) == world["record"]
    for change, kind in [({"caller_project_id": OTHER_PROJECT}, "not_found"),
                         ({"caller_can_read": False}, "not_found"),
                         ({"deleted": True}, "source_unavailable"),
                         ({"age_days": media.RETENTION_DAYS + 0.01}, "source_unavailable")]:
        with pytest.raises(SceneError) as e:
            media.resolve(world["record"], **{**ok, **change})
        assert e.value.kind == kind
    assert media.resolve(world["record"], **{**ok, "age_days": media.RETENTION_DAYS}) == world["record"]


@pytest.mark.parametrize("field,limit,args", [
    ("media_bytes", media.MAX_MEDIA_BYTES, lambda v: (v, None, 0, 0)),
    ("image_pixels", media.MAX_IMAGE_PIXELS, lambda v: (1, v, 0, 0)),
    ("project_media_files", media.MAX_PROJECT_MEDIA_FILES, lambda v: (1, None, v - 1, 0)),
    ("project_media_bytes", media.MAX_PROJECT_MEDIA_BYTES, lambda v: (1, None, 0, v - 1)),
])
def test_media_quotas_both_sides(field, limit, args):
    media.check_upload(*args(limit))
    with pytest.raises(SceneError) as e:
        media.check_upload(*args(limit + 1))
    assert e.value.detail["limit"] == field


# ── V60: the copy closure ───────────────────────────────────────────────────

def test_the_closure_holds_only_the_pointed_generation(world, tmp_path):
    sdir = store.scene_dir(world["project"], "campus")
    for junk in ("generations/g000009-orphan/generation.json", "generations/failed.tmp", "cache/tiles.bin",
                 "worker-scratch/out.glb"):
        (sdir / junk).parent.mkdir(parents=True, exist_ok=True)
        (sdir / junk).write_bytes(b"JUNK-" + junk.encode())
    dst = tmp_path / "dst"
    copied = store.copy_closure(world["project"], dst, "campus")
    files = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert files == sorted(p.as_posix() for p in copied) and len(files) == 2
    assert b"JUNK-" not in b"".join((dst / f).read_bytes() for f in files)
    # The superseded generation 1 (adoption) is excluded too: only revision 2 travels.
    assert store.read_scene(dst, "campus")["revision"] == 2


def test_a_missing_pointed_generation_fails_the_copy_and_leaves_the_destination(world, tmp_path):
    rel = store.closure(world["project"], "campus")[1]
    (world["project"] / rel).unlink()
    dst = tmp_path / "dst"
    with pytest.raises(SceneError) as e:
        store.copy_closure(world["project"], dst, "campus")
    assert e.value.kind == "integrity_error" and not dst.exists()


def test_a_concurrent_apply_cannot_mix_generations_into_a_copy(world, tmp_path, monkeypatch):
    # Interleave an apply between the closure read and the byte copy: the lock serialises them.
    real = store._closure_locked
    raced: list[object] = []

    def closure_then_race(project_dir, site_id):
        paths = real(project_dir, site_id)
        t = __import__("threading").Thread(target=lambda: raced.append(store.apply(
            project_dir, site_id, {"id": "p2", "base_revision": 2, "input_hash": "h2", "changes": {"buildings": []}}, 2)))
        t.start()
        t.join(timeout=0.2)
        raced.append(t)
        return paths

    monkeypatch.setattr(store, "_closure_locked", closure_then_race)
    dst = tmp_path / "dst"
    store.copy_closure(world["project"], dst, "campus")
    raced[-1].join()
    assert store.read_scene(dst, "campus")["revision"] == 2  # the copy holds one complete generation
    assert store.read_scene(world["project"], "campus")["revision"] == 3  # the apply ran after it
