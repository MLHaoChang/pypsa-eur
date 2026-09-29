"""
`services/site_service.py` — the `sites.json` sidecar: document shape, its
validation, atomic I/O, the rename hook, and the per-site directory rules.

Plan: docs/superpowers/plans/2026-09-29-3d-site-view-phase1.md, Task 1.1.
Spec: docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md §4.1.

Why site ids have a charset. A site id becomes a directory name under
`<project>/sites/<id>/` the moment its context is cached, so an id of `..`
or `../x` is a path-traversal write. The same rule the snapshot and upload
ids already follow (`_SNAPSHOT_ID_RE`, `_FILE_ID_RE`): validated before any
path is joined, and the join itself re-checks containment.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib

import pytest

from services import site_service as ss

SPEC_EXAMPLE = {
    "version": 1,
    "sites": [
        {
            "id": "0f3a9c2b",
            "name": "Eemshaven campus",
            "buses": ["Campus 110kV", "Campus 33kV"],
            "boundary": [[6.8291, 53.4412], [6.8351, 53.4412], [6.8351, 53.4381], [6.8291, 53.4381]],
            "origin": {"lng": 6.8321, "lat": 53.4396},
            "placements": {
                "Generator:Gas gensets": {"x": -142.0, "y": 18.5, "heading": 90},
                "StorageUnit:BESS 1": {"x": 96.0, "y": -12.0, "heading": 0},
                "Bus:Campus 33kV": {"x": 0.0, "y": 0.0, "heading": 0},
            },
            "context_fetched_at": "2026-09-29T10:12:00Z",
        }
    ],
}


def _doc(**site_overrides):
    d = copy.deepcopy(SPEC_EXAMPLE)
    d["sites"][0].update(site_overrides)
    return d


# ── read ────────────────────────────────────────────────────────────────────

def test_empty_document_when_missing(tmp_path):
    assert ss.read_sites(tmp_path) == {"version": 1, "sites": []}


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", "42", ""])
def test_corrupt_file_degrades_to_empty(tmp_path, raw):
    (tmp_path / ss.SITES_FILE).write_text(raw)
    assert ss.read_sites(tmp_path) == {"version": 1, "sites": []}


def test_permission_error_is_raised_not_swallowed(tmp_path, monkeypatch):
    (tmp_path / ss.SITES_FILE).write_text(json.dumps(SPEC_EXAMPLE))
    real = pathlib.Path.read_text

    def deny(self, *a, **k):
        if self.name == ss.SITES_FILE:
            raise PermissionError("denied")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", deny)
    with pytest.raises(PermissionError):
        ss.read_sites(tmp_path)


# ── validate ────────────────────────────────────────────────────────────────

def test_validate_accepts_spec_example():
    ss.validate_sites(SPEC_EXAMPLE)  # no raise


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d.__setitem__("version", 2), "version"),
        (lambda d: d["sites"].append(copy.deepcopy(d["sites"][0])), "duplicate"),
        (lambda d: d["sites"][0].__setitem__("id", ""), "id"),
        (lambda d: d["sites"][0].__setitem__("id", ".."), "id"),
        (lambda d: d["sites"][0].__setitem__("id", "a/b"), "id"),
        (lambda d: d["sites"][0].__setitem__("id", "x" * 65), "id"),
        (lambda d: d["sites"][0].__setitem__("id", "sïte"), "id"),
        (lambda d: d["sites"][0].__setitem__("buses", ["ok", 3]), "buses"),
        (lambda d: d["sites"][0].__setitem__("boundary", [[6.8, 53.4], [6.9, 53.4]]), "boundary"),
        (lambda d: d["sites"][0].__setitem__("boundary", [[6.8, 53.4], [6.9, 53.4], [200.0, 53.4]]), "boundary"),
        (lambda d: d["sites"][0]["placements"]["Generator:Gas gensets"].__setitem__("x", float("nan")), "placements"),
        (lambda d: d["sites"][0]["placements"].__setitem__("no-colon", {"x": 0, "y": 0, "heading": 0}), "placements"),
        (lambda d: d["sites"][0]["placements"].__setitem__("Carrier:gas", {"x": 0, "y": 0, "heading": 0}), "placements"),
        (lambda d: d["sites"][0].__setitem__("origin", {"lng": 6.8}), "origin"),
    ],
)
def test_validate_rejects(mutate, needle):
    d = copy.deepcopy(SPEC_EXAMPLE)
    mutate(d)
    with pytest.raises(ss.SitesInvalid) as exc:
        ss.validate_sites(d)
    assert needle in str(exc.value)


def test_validate_preserves_unknown_keys(tmp_path):
    d = _doc(future_field={"a": 1})
    d["top_level_extra"] = "kept"
    ss.validate_sites(d)
    ss.write_sites(tmp_path, d)
    back = ss.read_sites(tmp_path)
    assert back["top_level_extra"] == "kept"
    assert back["sites"][0]["future_field"] == {"a": 1}


# ── write ───────────────────────────────────────────────────────────────────

def test_write_is_atomic_and_compact(tmp_path):
    ss.write_sites(tmp_path, SPEC_EXAMPLE)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [ss.SITES_FILE], f"temp file left behind: {names}"
    raw = (tmp_path / ss.SITES_FILE).read_text()
    assert "\n" not in raw.strip() and ": " not in raw
    assert json.loads(raw) == SPEC_EXAMPLE


def test_write_rejects_oversize(tmp_path):
    d = _doc(padding="x" * (ss.MAX_SITES_BYTES + 1))
    with pytest.raises(ss.SitesTooLarge):
        ss.write_sites(tmp_path, d)
    assert not (tmp_path / ss.SITES_FILE).exists()


# ── rename ──────────────────────────────────────────────────────────────────

def test_rename_component_moves_placement_key():
    d = copy.deepcopy(SPEC_EXAMPLE)
    changed = ss.rename_component(d, "Generator", "Gas gensets", "Gensets A")
    assert changed is True
    p = d["sites"][0]["placements"]
    assert "Generator:Gas gensets" not in p
    assert p["Generator:Gensets A"] == {"x": -142.0, "y": 18.5, "heading": 90}
    assert p["StorageUnit:BESS 1"] == {"x": 96.0, "y": -12.0, "heading": 0}


def test_rename_component_bus_is_a_valid_class():
    d = copy.deepcopy(SPEC_EXAMPLE)
    assert ss.rename_component(d, "Bus", "Campus 33kV", "MV bus") is True
    assert "Bus:MV bus" in d["sites"][0]["placements"]


def test_rename_component_missing_key_is_a_noop():
    d = copy.deepcopy(SPEC_EXAMPLE)
    before = copy.deepcopy(d)
    assert ss.rename_component(d, "Load", "nope", "still nope") is False
    assert d == before


# ── site directories ────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["..", "a/b", "", "x" * 65, "sïte", "../0f3a9c2b"])
def test_site_dir_rejects_unsafe_ids(tmp_path, bad):
    with pytest.raises(ss.SitesInvalid):
        ss.site_dir(tmp_path, bad)


def test_site_dir_is_contained(tmp_path):
    d = ss.site_dir(tmp_path, "0f3a9c2b")
    assert d == tmp_path / ss.SITES_DIR / "0f3a9c2b"
    assert d.resolve().is_relative_to(tmp_path.resolve())


def test_prune_site_dirs_removes_only_orphans(tmp_path):
    for name in ("a", "b", "..weird"):
        (tmp_path / ss.SITES_DIR / name).mkdir(parents=True)
        (tmp_path / ss.SITES_DIR / name / "context.json").write_text("{}")
    removed: list[pathlib.Path] = []

    def rm(p: pathlib.Path) -> None:
        removed.append(p)
        for child in p.iterdir():
            child.unlink()
        p.rmdir()

    doc = {"version": 1, "sites": [{**SPEC_EXAMPLE["sites"][0], "id": "a"}]}
    ss.prune_site_dirs(tmp_path, doc, rmtree=rm)
    assert removed == [tmp_path / ss.SITES_DIR / "b"]
    assert (tmp_path / ss.SITES_DIR / "a").exists()
    # A name that fails the id rule is not ours to delete — left alone.
    assert (tmp_path / ss.SITES_DIR / "..weird").exists()


def test_prune_site_dirs_without_sites_dir_is_a_noop(tmp_path):
    ss.prune_site_dirs(tmp_path, {"version": 1, "sites": []})
    assert not (tmp_path / ss.SITES_DIR).exists()
