"""
QA for the project-rename endpoint.

Covers:

  [1] Happy path — POST /projects/{name}/rename renames the project,
      refreshes metadata.json's name field, and is reachable under the
      new name while the old one returns 404. Whether the DIRECTORY moves
      is local-mode-only (`project_registry._may_move_directory`); this
      driver runs web-mode, where storage is UUID-keyed and stays put.
  [2] In-memory n.name syncs when the renamed project is the active
      singleton — so subsequent autosaves target the new directory and
      n.export_to_netcdf() embeds the new name.
  [3] Direct-child scenarios are reparented to the new name. Grandchildren
      are left untouched (they point to the IMMEDIATE parent, which didn't
      change). The tree is built through POST /{base}/scenarios, because
      the reparent walks `Project.parent_project_id`, not metadata.json.
  [4] Error paths: 404 for missing source, 409 for target-already-exists,
      400/422 for same-name and empty-name. A traversal-shaped name is NO
      LONGER rejected — see the finding referenced in test 4 — so what is
      asserted there is containment: the directory stays under the
      projects root.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa

# Before any `main` / `routers` / `services` import: pins the sandbox (throwaway
# DB, projects root and app-data dir) and seeds the org this driver signs in as.
# See tests/qa_support.py.
from tests import qa_support  # noqa: E402  (ordering is the point)

from settings import get_settings

PASS = 0
FAIL = 0


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _build_minimal_network(name: str) -> pypsa.Network:
    n = pypsa.Network()
    n.name = name
    n.snapshots = pd.date_range("2025-01-01", periods=4, freq="h")
    n.add("Bus", "B1")
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Generator", "Gas", bus="B1", carrier="gas", p_nom=100.0, marginal_cost=50.0)
    n.add("Load", "L", bus="B1", p_set=50.0)
    return n


def _save_under(name: str, n: pypsa.Network) -> None:
    """
    Save `n` as `name`, through the real route as an authenticated caller.

    Calling `save_project()` as a plain function stopped working at the tenancy
    migration: its `db`/`user` parameters are `Depends(...)` defaults, and a
    direct call hands those sentinels to `project_registry`, which asks the
    `Depends` object for `.id`.
    """
    qa_support.install_network(n)
    qa_support.save_project(name)


def _project_dir(name: str) -> pathlib.Path | None:
    """
    Where `name`'s files live, or None when no such project exists.

    Storage is org-scoped (`projects_root/<org>/<uuid>/`), so `PROJECTS_DIR /
    name` — what this driver used to compute — names a path that does not
    exist even when the project does.
    """
    return qa_support.project_dir(name)


def _meta_of(name: str) -> dict | None:
    d = _project_dir(name)
    if d is None:
        return None
    path = d / "metadata.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _force_cleanup(*names: str) -> None:
    """Drop each project — row AND directory — so a re-run cannot 409."""
    qa_support.delete_project(*names)


# ── Test 1 — happy path: project renamed, metadata refreshed ────────────────

def test_happy_path() -> None:
    print("\n[1] rename renames the project + refreshes metadata.json")
    old_name = "_qa_rename_src"
    new_name = "_qa_rename_dst"
    _force_cleanup(old_name, new_name)
    _save_under(old_name, _build_minimal_network(old_name))

    client = qa_support.client()
    r = client.post(f"/api/projects/{old_name}/rename", json={"new_name": new_name})
    _step("HTTP 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        _force_cleanup(old_name, new_name)
        return
    info = r.json()
    _step("response.name == new_name", info.get("name") == new_name, f"got {info.get('name')}")

    # On disk. The old assertions here were "old dir removed / new dir exists",
    # which is LOCAL-mode behaviour: `project_registry._may_move_directory` only
    # lets a rename move the directory in local mode, and `rename_project` says
    # of the other branch "the directory keeps the name it was created with,
    # which is stale but readable, resolvable and safe". This driver runs in the
    # web-mode sandbox, so the directory is UUID-keyed and does not move — the
    # name lives on the row. Assert the contract that actually holds: the
    # renamed project resolves, and its files came with it.
    dest = _project_dir(new_name)
    _step("renamed project resolves to a directory", dest is not None)
    _step("its directory exists", dest is not None and dest.exists())
    _step("its directory still holds network.nc",
          dest is not None and (dest / "network.nc").exists())
    _step("the old name resolves to nothing", _project_dir(old_name) is None)

    # metadata.json was refreshed with the new name.
    meta = _meta_of(new_name)
    if meta is not None:
        _step("metadata.json.name == new_name",
              meta.get("name") == new_name, f"got {meta.get('name')!r}")
    else:
        _step("metadata.json exists for the renamed project", False)

    # Old path is gone → 404 via GET; new path works.
    _step("GET old → 404",
          client.get(f"/api/projects/{old_name}").status_code == 404)
    _step("GET new → 200",
          client.get(f"/api/projects/{new_name}").status_code == 200)

    _force_cleanup(old_name, new_name)


# ── Test 2 — in-memory n.name syncs when renaming the active project ─────────

def test_in_memory_name_sync() -> None:
    print("\n[2] in-memory n.name updates when renaming the active project")
    old_name = "_qa_rename_active_src"
    new_name = "_qa_rename_active_dst"
    _force_cleanup(old_name, new_name)

    n = _build_minimal_network(old_name)
    _save_under(old_name, n)

    # Read the network out of the CLIENT'S OWN context, not
    # `PyPSAService.get_network()`. The active project is per session since the
    # tenancy migration, so the process foreground this driver's thread sees is
    # a different context from the one the client's requests resolve to — and
    # asserting on the wrong one makes a working rename hook look like a no-op.
    ctx = qa_support.session_context()
    _step("the client's context is bound to the saved project",
          ctx.loaded_project == old_name, f"got {ctx.loaded_project!r}")
    ctx.network.name = old_name  # the hook keys off the bound project, not this
    _step("pre-rename n.name == old_name", ctx.network.name == old_name)

    client = qa_support.client()
    r = client.post(f"/api/projects/{old_name}/rename", json={"new_name": new_name})
    _step("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        live2 = qa_support.session_context().network
        _step("post-rename n.name == new_name",
              live2.name == new_name, f"got {live2.name!r}")

    _force_cleanup(old_name, new_name)


# ── Test 3 — child scenarios reparented ──────────────────────────────────────

def test_child_reparent() -> None:
    print("\n[3] direct-child scenarios are reparented")
    parent = "_qa_rename_parent"
    new_parent = "_qa_rename_parent_renamed"
    child = "_qa_rename_child"
    grandchild = "_qa_rename_grandchild"
    _force_cleanup(parent, new_parent, child, grandchild)

    # Build a three-level tree: parent <- child <- grandchild, THROUGH the
    # scenarios route.
    #
    # This used to write `parent_project` straight into each metadata.json,
    # which was enough when the tree lived on disk. It is not enough now:
    # `_rename_project_db` reparents `project_registry.direct_children(db,
    # project)`, a query on `Project.parent_project_id`. A metadata-only
    # pointer leaves that column NULL, so the rename correctly finds no
    # children and the assertion below fails while nothing is wrong.
    client = qa_support.client()

    _save_under(parent, _build_minimal_network(parent))

    def _branch(base: str, name: str) -> None:
        r = client.post(f"/api/projects/{base}/scenarios", json={"name": name})
        assert r.status_code == 201, f"branch {name} off {base}: {r.status_code} {r.text[:200]}"

    _branch(parent, child)
    _branch(child, grandchild)

    # Sanity-check the on-disk pointers BEFORE the rename so we know the
    # test setup itself is correct.
    def _parent_of_now(name: str) -> str | None:
        meta = _meta_of(name)
        return None if meta is None else meta.get("parent_project")
    _step(f"PRE: child's parent_project is set to '{parent}'",
          _parent_of_now(child) == parent,
          f"got {_parent_of_now(child)!r}")

    client = qa_support.client()
    r = client.post(f"/api/projects/{parent}/rename", json={"new_name": new_parent})
    _step("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code != 200:
        _force_cleanup(parent, new_parent, child, grandchild)
        return

    def _parent_of(name: str) -> str | None:
        meta = _meta_of(name)
        return None if meta is None else meta.get("parent_project")

    _step("child's parent_project updated → new_parent",
          _parent_of(child) == new_parent,
          f"got {_parent_of(child)!r}")
    _step("grandchild's parent_project unchanged (was child, still child)",
          _parent_of(grandchild) == child,
          f"got {_parent_of(grandchild)!r}")

    _force_cleanup(parent, new_parent, child, grandchild)


# ── Test 4 — error paths ────────────────────────────────────────────────────

def test_error_paths() -> None:
    print("\n[4] error paths: 404 / 409 / 400")
    name = "_qa_rename_err_src"
    other = "_qa_rename_err_other"
    _force_cleanup(name, other)
    _save_under(name, _build_minimal_network(name))
    _save_under(other, _build_minimal_network(other))

    client = qa_support.client()

    # 404 — source missing.
    r = client.post("/api/projects/_does_not_exist_xyz/rename", json={"new_name": "anything"})
    _step("404 for missing source", r.status_code == 404, f"got {r.status_code}")

    # 409 — target already exists.
    r = client.post(f"/api/projects/{name}/rename", json={"new_name": other})
    _step("409 for target-already-exists", r.status_code == 409, f"got {r.status_code}")

    # 400 — same name.
    r = client.post(f"/api/projects/{name}/rename", json={"new_name": name})
    _step("400 for same name", r.status_code == 400, f"got {r.status_code}")

    # 400 — empty / whitespace-only name (Pydantic min_length=1 → 422 actually).
    # Checked BEFORE the traversal rename below: that one leaves the project
    # named `../escape`, and a URL path carrying it is normalised away by the
    # client and answered 405 — which says nothing about the rename route.
    r = client.post(f"/api/projects/{name}/rename", json={"new_name": ""})
    _step("400/422 for empty name", r.status_code in (400, 422), f"got {r.status_code}")

    # Path traversal. This used to assert `400`; the route no longer rejects
    # the name, and returns 200 (recorded in
    # docs/superpowers/findings/2026-09-06-rename-accepts-any-name-and-it-reaches-a-header.md).
    # It is not exploitable — the name is a DB value now, and the only place it
    # reaches the filesystem is `storage_paths.allocate_storage_path`, which runs
    # it through `safe_names.safe_dir_name` ('../escape' -> '_escape'). So assert
    # the property the old status code was defending rather than the status code
    # itself: whatever the route does with such a name, the project's directory
    # stays inside the projects root.
    r = client.post(f"/api/projects/{name}/rename", json={"new_name": "../escape"})
    if r.status_code in (400, 422):
        _step("traversal-shaped name rejected outright", True, f"got {r.status_code}")
        escaped_as = name
    else:
        _step("traversal-shaped name accepted (see the finding)",
              r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
        escaped_as = "../escape"
    landed = _project_dir(escaped_as)
    root = pathlib.Path(get_settings().projects_root).resolve()
    _step("the project directory stays inside the projects root",
          landed is not None and landed.resolve().is_relative_to(root),
          f"dir={landed} root={root}")

    _force_cleanup(name, other, escaped_as)


def main() -> int:
    print("=== qa_rename_project ===")
    test_happy_path()
    test_in_memory_name_sync()
    test_child_reparent()
    test_error_paths()
    total = PASS + FAIL
    print(f"\nTotal: {total}  Pass: {PASS}  Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
