"""
What is in the legacy tree, and what is not a project (phase 1b, Task 7 — F2).

Every fixture below mirrors something that is actually sitting in
`pypsa-gui/backend/projects/` on the machine this was written on. The tree is
113 MB across 13 directories plus one `.zip`, and the classification rules are
what stop the import from either dropping a real project or adopting something
that never was one.

The rule that matters most: **classify by CONTENT, never by suffix.**
`new_project_test.pypsaproj` looks like a bundle file and is in fact a
directory holding a 7 MB `network.nc` and a 15.7 MB `user_ts.json` — and it
roots a three-level scenario chain, so dropping it fabricates a second dangling
parent as well as losing 22 MB.
"""
from __future__ import annotations

import json
import uuid

import pytest

from services.legacy_import import LegacyProject, install_id, inventory


def _write_project(root, name: str, *, parent: str | None = None, network=True,
                   display_name: str | None = None):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    meta = {"name": display_name or name, "parent_project": parent,
            "scenario_description": "[scenario] variant" if parent else None}
    (directory / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if network:
        (directory / "network.nc").write_text(f"network:{name}", encoding="utf-8")
    return directory


@pytest.fixture
def legacy_tree(tmp_path):
    """A miniature of the real tree, one entry per classification rule."""
    root = tmp_path / "projects"
    root.mkdir()

    _write_project(root, "Belgium Grid")
    # A directory whose NAME says bundle and whose CONTENT says project.
    _write_project(root, "new_project_test.pypsaproj")
    # A real chain: child -> parent, both present.
    _write_project(root, "heat with time-series", parent="new_project_test.pypsaproj")
    _write_project(root, "H2 Demand 250MW", parent="heat with time-series")
    # A parent that was never saved. Normal, and reported rather than fixed.
    _write_project(root, "4_nodes_N-1", parent="test_project_4_nodes2")
    # Two names that sanitise onto one directory.
    _write_project(root, "Study_1")
    _write_project(root, "Study-1", display_name="Study:1")
    # Long enough to exceed `Project.name`'s String(64), which SQLite does not
    # enforce, so nothing would complain until something else tripped.
    _write_project(root, "L" * 200)
    # Not projects.
    (root / "archive.pypsaproj.zip").write_bytes(b"PK\x03\x04not-really")
    _write_project(root, "Empty Folder", network=False)
    # The org-scoped tree: UUID-named, `network.nc` one level DOWN. This is
    # the shape the real tree has, and it holds a solved network — see
    # `test_projects_inside_the_org_tree_are_importable`.
    org_tree = root / str(uuid.uuid4())
    nested = _write_project(org_tree, str(uuid.uuid4()))
    # The org layout carries no display name; that lived in the database.
    (nested / "metadata.json").write_text(
        json.dumps({"parent_project": None, "bus_count": 3}), encoding="utf-8"
    )

    return root


def _by_dir(root) -> dict[str, LegacyProject]:
    return {p.dir_name: p for p in inventory(root)}


def test_a_pypsaproj_directory_is_importable(legacy_tree):
    """The single most expensive misclassification available: 22 MB and the
    root of a three-level chain, behind a name that reads as an archive."""
    found = _by_dir(legacy_tree)["new_project_test.pypsaproj"]
    assert found.importable
    assert found.has_network


def test_a_zip_file_is_not_a_project(legacy_tree):
    found = _by_dir(legacy_tree)
    assert "archive.pypsaproj.zip" not in {p.dir_name for p in inventory(legacy_tree) if p.importable}
    assert found["archive.pypsaproj.zip"].skip_reason == "not a directory"


def test_a_directory_without_a_network_is_skipped(legacy_tree):
    assert _by_dir(legacy_tree)["Empty Folder"].skip_reason == "no network.nc"


def test_the_org_tree_is_flagged_not_imported(legacy_tree):
    """
    The CONTAINER is not a project — its `network.nc` is at depth 2, so a
    recursive content check would import the whole org root as one. The
    projects INSIDE it are imported; see the org-tree tests below.
    """
    org_entries = [p for p in inventory(legacy_tree) if p.skip_reason == "org-scoped tree"]
    assert len(org_entries) == 1
    assert not org_entries[0].importable


def test_the_importable_set_is_exactly_the_projects(legacy_tree):
    """The whole classification in one assertion — and the nested project the
    original phase silently dropped."""
    found = sorted(p.dir_name for p in inventory(legacy_tree) if p.importable)
    nested = [d for d in found if "/" in d]
    assert len(nested) == 1, found
    assert [d for d in found if "/" not in d] == sorted([
        "4_nodes_N-1",
        "Belgium Grid",
        "H2 Demand 250MW",
        "L" * 200,
        "Study-1",
        "Study_1",
        "heat with time-series",
        "new_project_test.pypsaproj",
    ])


def test_parents_are_reported_by_name(legacy_tree):
    found = _by_dir(legacy_tree)
    assert found["H2 Demand 250MW"].parent_name == "heat with time-series"
    assert found["heat with time-series"].parent_name == "new_project_test.pypsaproj"
    assert found["Belgium Grid"].parent_name is None


def test_a_dangling_parent_is_normal_and_still_importable(legacy_tree):
    """`4_nodes_N-1` points at `test_project_4_nodes2`, which was never saved.
    Refusing the child would lose a real project over a broken pointer."""
    found = _by_dir(legacy_tree)["4_nodes_N-1"]
    assert found.importable
    assert found.parent_name == "test_project_4_nodes2"


def test_the_row_name_is_truncated_defensively(legacy_tree):
    """
    `Project.name` is `String(64)` and SQLite does not enforce it, so a 200-char
    name persists silently until something else trips over it.
    """
    found = _by_dir(legacy_tree)["L" * 200]
    assert len(found.name) <= 64


def test_the_display_name_comes_from_metadata_when_present(legacy_tree):
    """The directory is `Study-1`; the project calls itself `Study:1`. The row
    keeps the real name and `safe_names` deals with the directory."""
    assert _by_dir(legacy_tree)["Study-1"].name == "Study:1"


def test_a_missing_root_inventories_nothing(tmp_path):
    assert inventory(tmp_path / "not-here") == []


def test_unreadable_metadata_does_not_lose_the_project(tmp_path):
    """A corrupt `metadata.json` costs the parent pointer, not the project."""
    root = tmp_path / "projects"
    directory = root / "Broken Meta"
    directory.mkdir(parents=True)
    (directory / "metadata.json").write_text("{not json", encoding="utf-8")
    (directory / "network.nc").write_text("network", encoding="utf-8")

    found = _by_dir(root)["Broken Meta"]
    assert found.importable
    assert found.name == "Broken Meta"
    assert found.parent_name is None


# ── install id ───────────────────────────────────────────────────────────────

def test_install_id_is_stable_across_calls(tmp_path, monkeypatch):
    """
    A per-process id would make every receipt carry a different one: the next
    run reads its own destinations as non-matching, reports them as foreign
    collisions, and skips them PERMANENTLY — the exact outcome the receipt
    exists to prevent.
    """
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    first = install_id()
    assert install_id() == first
    assert uuid.UUID(first)


def test_install_id_is_not_the_shared_local_constant(tmp_path, monkeypatch):
    """`LOCAL_ORG_ID`/`LOCAL_USER_ID` are fixed constants shared by EVERY
    install, which would make the term a no-op."""
    import local_mode

    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    assert install_id() not in {str(local_mode.LOCAL_ORG_ID), str(local_mode.LOCAL_USER_ID)}


def test_install_id_creates_its_parent_directory(tmp_path, monkeypatch):
    """`O_EXCL` under a missing parent raises `FileNotFoundError`. The
    rehearsal never hits that because bootstrap runs first; a bare CLI
    invocation on a fresh machine would."""
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "never" / "created"))
    assert uuid.UUID(install_id())


def test_two_installs_get_different_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "a"))
    first = install_id()
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "b"))
    assert install_id() != first


# ── The org tree holds real projects (review finding F1) ────────────────────

def test_projects_inside_the_org_tree_are_importable(legacy_tree):
    """
    Phase 1b classified `<org_uuid>/` as "org-scoped tree", dropped it, and did
    not even report it — while the tree it was written against held a SOLVED
    3-bus, 8760-snapshot network with its chat, layout and time series.
    `--rebase-db` does not cover it either: that walks database ROWS, and a
    fresh desktop install has no row for it.
    """
    entries = inventory(legacy_tree)
    nested = [p for p in entries if "/" in p.dir_name]

    assert len(nested) == 1, [p.dir_name for p in nested]
    assert nested[0].importable
    assert nested[0].has_network


def test_the_org_container_itself_is_reported_but_never_imported(legacy_tree):
    """It is not a project — its `network.nc` is one level down — but the user
    must be able to see that the importer noticed it."""
    containers = [p for p in inventory(legacy_tree) if p.skip_reason == "org-scoped tree"]
    assert len(containers) == 1
    assert not containers[0].importable


def test_a_uuid_named_project_gets_a_findable_name(legacy_tree):
    """
    The org layout stores no display name — it lived in the database the tree
    was detached from. A UUID directory would satisfy the importer and defeat
    E1, so the fallback has to be something a user recognises and can rename.
    """
    nested = next(p for p in inventory(legacy_tree) if "/" in p.dir_name)
    assert not nested.name.startswith("Imported project ") or len(nested.name) < 40
    uuid_part = nested.dir_name.split("/")[1]
    assert nested.name != uuid_part


def test_only_one_level_is_descended(tmp_path):
    """`rglob` would mistake a project's own `snapshots/` and `uploads/`
    subtrees for sibling projects."""
    root = tmp_path / "projects"
    org = root / str(uuid.uuid4())
    project = org / str(uuid.uuid4())
    deep = project / "snapshots" / "2026-01-01-a"
    deep.mkdir(parents=True)
    (deep / "network.nc").write_text("snapshot", encoding="utf-8")
    (project / "network.nc").write_text("network", encoding="utf-8")

    nested = [p for p in inventory(root) if p.importable]
    assert len(nested) == 1
    assert nested[0].path == project
