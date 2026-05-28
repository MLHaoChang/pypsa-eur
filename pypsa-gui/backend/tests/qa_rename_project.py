"""
QA for the project-rename endpoint.

Covers:

  [1] Happy path — POST /projects/{name}/rename moves the directory,
      refreshes metadata.json's name field, and is reachable under the
      new path while the old one returns 404.
  [2] In-memory n.name syncs when the renamed project is the active
      singleton — so subsequent autosaves target the new directory and
      n.export_to_netcdf() embeds the new name.
  [3] Child scenarios with `parent_project == old_name` are reparented
      to the new name. Grandchildren are left untouched (they point to
      the IMMEDIATE parent, which didn't change).
  [4] Error paths: 404 for missing source, 409 for target-already-exists,
      400 for same-name and invalid-name (path traversal, bad chars).
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa
from fastapi.testclient import TestClient
from main import app
from routers.projects import PROJECTS_DIR
from services.pypsa_service import PyPSAService

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
    from routers.projects import save_project
    PyPSAService.set_network(n)
    save_project(name, force=True, clear_undo=False)


def _force_cleanup(*names: str) -> None:
    for n in names:
        d = PROJECTS_DIR / n
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


# ── Test 1 — happy path: directory moved, metadata refreshed ─────────────────

def test_happy_path() -> None:
    print("\n[1] rename moves dir + refreshes metadata.json")
    old_name = "_qa_rename_src"
    new_name = "_qa_rename_dst"
    _force_cleanup(old_name, new_name)
    _save_under(old_name, _build_minimal_network(old_name))

    client = TestClient(app)
    r = client.post(f"/api/projects/{old_name}/rename", json={"new_name": new_name})
    _step("HTTP 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        _force_cleanup(old_name, new_name)
        return
    info = r.json()
    _step("response.name == new_name", info.get("name") == new_name, f"got {info.get('name')}")

    # On-disk: old gone, new exists.
    _step("old project dir removed",      not (PROJECTS_DIR / old_name).exists())
    _step("new project dir exists",       (PROJECTS_DIR / new_name).exists())
    _step("new dir has network.nc",       (PROJECTS_DIR / new_name / "network.nc").exists())

    # metadata.json was refreshed with the new name.
    meta_path = PROJECTS_DIR / new_name / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        _step("metadata.json.name == new_name",
              meta.get("name") == new_name, f"got {meta.get('name')!r}")
    else:
        _step("metadata.json exists at new path", False)

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
    # The active singleton IS this network, and its .name field carries the
    # old name. After rename, n.name should be new_name.
    live = PyPSAService.get_network()
    live.name = old_name  # ensure name matches the dir for the rename hook to fire
    _step("pre-rename n.name == old_name", live.name == old_name)

    client = TestClient(app)
    r = client.post(f"/api/projects/{old_name}/rename", json={"new_name": new_name})
    _step("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        live2 = PyPSAService.get_network()
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

    # Build a three-level tree: parent ← child ← grandchild. Set the
    # parent_project field directly in metadata.json since /scenarios
    # would also try to round-trip the live network state — we just need
    # the on-disk pointers for this test.
    _save_under(parent, _build_minimal_network(parent))
    _save_under(child, _build_minimal_network(child))
    _save_under(grandchild, _build_minimal_network(grandchild))

    def _set_parent(name: str, parent_name: str) -> None:
        meta_path = PROJECTS_DIR / name / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        meta["parent_project"] = parent_name
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    _set_parent(child, parent)
    _set_parent(grandchild, child)

    # Sanity-check the on-disk pointers BEFORE the rename so we know the
    # test setup itself is correct.
    def _parent_of_now(name: str) -> str | None:
        meta_path = PROJECTS_DIR / name / "metadata.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")).get("parent_project")
        except Exception:
            return None
    _step(f"PRE: child's parent_project is set to '{parent}'",
          _parent_of_now(child) == parent,
          f"got {_parent_of_now(child)!r}")

    client = TestClient(app)
    r = client.post(f"/api/projects/{parent}/rename", json={"new_name": new_parent})
    _step("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code != 200:
        _force_cleanup(parent, new_parent, child, grandchild)
        return

    def _parent_of(name: str) -> str | None:
        meta_path = PROJECTS_DIR / name / "metadata.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")).get("parent_project")
        except Exception:
            return None

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

    client = TestClient(app)

    # 404 — source missing.
    r = client.post("/api/projects/_does_not_exist_xyz/rename", json={"new_name": "anything"})
    _step("404 for missing source", r.status_code == 404, f"got {r.status_code}")

    # 409 — target already exists.
    r = client.post(f"/api/projects/{name}/rename", json={"new_name": other})
    _step("409 for target-already-exists", r.status_code == 409, f"got {r.status_code}")

    # 400 — same name.
    r = client.post(f"/api/projects/{name}/rename", json={"new_name": name})
    _step("400 for same name", r.status_code == 400, f"got {r.status_code}")

    # 400 — invalid characters (path traversal).
    r = client.post(f"/api/projects/{name}/rename", json={"new_name": "../escape"})
    _step("400 for invalid characters", r.status_code in (400, 422),
          f"got {r.status_code}")

    # 400 — empty / whitespace-only name (Pydantic min_length=1 → 422 actually).
    r = client.post(f"/api/projects/{name}/rename", json={"new_name": ""})
    _step("400/422 for empty name", r.status_code in (400, 422), f"got {r.status_code}")

    _force_cleanup(name, other)


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
