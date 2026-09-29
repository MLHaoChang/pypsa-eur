"""
Study-owned forks (MVP-1 S4 M1/M2; review v2 BC-4; gate S1 SB-4).

Unit-level pins beside the route tests in `test_study_runner.py`: the copy
walk skips `studies/` and `results_state.pkl`; ownership needs BOTH the fork
metadata AND the database parent; the router's metadata round-trip keys are
the service's.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from routers import projects as projects_router
from services.study import forks as F


def test_router_round_trips_exactly_the_owner_keys():
    assert projects_router._STUDY_FORK_META_KEYS == F.OWNER_KEYS


def test_the_copy_walk_skips_studies_and_results_state(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "studies").mkdir(parents=True)
    (src / "uploads" / "f1").mkdir(parents=True)
    dst.mkdir()
    (src / "studies" / ("a" * 32 + ".json")).write_text("{}")
    (src / "results_state.pkl").write_bytes(b"stale results")
    (src / "metadata.json").write_text("{}")
    (src / "uploads" / "f1" / "blob").write_bytes(b"x")
    F._copy_walk(src, dst)
    assert not (dst / "studies").exists()
    assert not (dst / "results_state.pkl").exists()
    assert (dst / "metadata.json").exists()
    assert (dst / "uploads" / "f1" / "blob").read_bytes() == b"x"


@pytest.fixture
def fork_dir(tmp_path, monkeypatch):
    from services import project_registry

    monkeypatch.setattr(project_registry, "project_dir", lambda row: tmp_path / row.name)

    def _make(name, parent, meta):
        (tmp_path / name).mkdir()
        (tmp_path / name / "metadata.json").write_text(json.dumps(meta))
        return SimpleNamespace(name=name, parent_project_id=parent)
    return _make


def test_ownership_needs_the_metadata_and_the_database_parent(fork_dir):
    meta = {"owner_study_id": "s1", "owner_base_project": "base-uuid"}
    owned = fork_dir("b-opt-none", "base-uuid", meta)
    assert F.is_study_owned(owned, study_id="s1", base_uuid="base-uuid")
    # A copy of the fork elsewhere (bundle import, a scenario of the fork):
    # the metadata came along, the parent did not.
    copied = fork_dir("copy", "someone-else", meta)
    assert not F.is_study_owned(copied, study_id="s1", base_uuid="base-uuid")
    # A user's own child of the base: right parent, no owner metadata.
    mine = fork_dir("mine", "base-uuid", {"parent_project": "b"})
    assert not F.is_study_owned(mine, study_id="s1", base_uuid="base-uuid")
    # Another study's fork of the same base.
    other = fork_dir("b-opt-x", "base-uuid", {**meta, "owner_study_id": "s2"})
    assert not F.is_study_owned(other, study_id="s1", base_uuid="base-uuid")


def test_fork_names_are_deterministic_and_slugged():
    assert F.fork_name("Site A", "bess_2h") == "Site A-opt-bess_2h"
    with pytest.raises(F.ForkError):
        F.fork_name("Site A", "../x")
