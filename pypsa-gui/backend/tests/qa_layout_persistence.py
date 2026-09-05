"""
QA harness for blank-canvas layout (node positions + edge waypoints)
persistence — covers the end-to-end save/load contract the frontend's
`flushPendingLayoutToServer` + `fetchLayoutFor` rely on.

Test plan:
  [1] PUT layout to an existing project, then GET it — payload round-trips
      byte-for-byte.
  [2] PUT layout to a non-existent project returns 404 (matches the
      frontend's "save project first, flush layout second" ordering).
  [3] GET layout on a project with no layout.json returns {} (canvas
      degrades to the layout algorithm, not 500).
  [4] GET layout on a project with corrupted layout.json returns {} (same
      defensive degradation — the canvas never sees a crash).
  [5] Layout is included in the bundle download (.pypsaproj.zip).
  [6] Re-PUT overwrites the previous layout (full replacement, not merge).
  [7] Layout isolation: PUT to project A, PUT to project B, both reads
      return their own data — no cross-project bleed.
  [8] Save → wipe in-memory → load: the project's saved layout.json
      survives even though it isn't part of n.export_to_netcdf.
  [9] Oversized payload (> _MAX_LAYOUT_BYTES) returns 413 instead of
      silently truncating or 500-ing.
  [10] Browser-refresh simulation: when a drag PUT arrives via the page-
       unload fast path (fetch keepalive / sendBeacon equivalent), the
       server must still accept it. The frontend `persistLayoutOnUnload`
       helper relies on this — any backend tightening that requires extra
       headers / preflight would silently lose the last drag on refresh.

Files written under projects/_qa_layout_*/. All cleaned up at the end.
"""
from __future__ import annotations

import io
import json
import pathlib
import shutil
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pypsa
from fastapi.testclient import TestClient
from services.pypsa_service import PyPSAService

PASS = 0
FAIL = 0
PROJECT_A = "_qa_layout_a"
PROJECT_B = "_qa_layout_b"


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _crashed(label: str, exc: BaseException) -> None:
    """
    A scenario that raised never ran its assertions, so it must COUNT as a
    failure — printing the traceback and moving on leaves the driver exiting 0
    while testing nothing. That is exactly how the stale
    `routers.simulation.get_*` references below went unnoticed: the scenarios
    crashed on every run and the summary still read "Fail: 0".
    """
    _step(f"{label} ran without crashing", False, f"{type(exc).__name__}: {exc}")

def _seed_minimal_network() -> pypsa.Network:
    """Save_project refuses an empty network — give it the smallest valid one."""
    import pandas as pd
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=10.0)
    n.add("Generator", "G1", bus="B1", p_nom=20.0, marginal_cost=10.0)
    return n


def _make_client() -> TestClient:
    """
    FastAPI TestClient mounted on the live app — bypasses uvicorn so the
    QA harness runs hermetically even when the dev server is restarted.
    """
    from main import app
    return TestClient(app)


def _cleanup_projects() -> None:
    base = pathlib.Path(__file__).resolve().parent.parent / "projects"
    for name in (PROJECT_A, PROJECT_B):
        path = base / name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def _sample_layout() -> dict:
    """Realistic layout payload — same shape the frontend writes."""
    return {
        "version": 1,
        "savedAt": 1700000000000,
        "nodes": [
            {"id": "Bus 1", "canvasX": 100.0, "canvasY": 200.0},
            {"id": "Bus 2", "canvasX": 300.0, "canvasY": 400.0},
            {"id": "Bus 3", "canvasX": 500.0, "canvasY": 200.0},
            {"id": "Bus 4", "canvasX": 700.0, "canvasY": 350.0},
        ],
        "edges": [
            {
                "id": "Bus 1-Bus 2",
                "waypoints": [{"x": 200, "y": 300}],
                "history": [[]],
            },
            {
                "id": "Bus 2-Bus 3",
                "waypoints": [],
                "history": [[]],
            },
        ],
    }


# ── Tests ──────────────────────────────────────────────────────────────


def test_put_and_get_round_trip() -> None:
    print("\n[1] PUT layout → GET layout round-trip")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    # Save the project so the layout endpoint has a directory to write into.
    r = client.post(f"/api/projects/{PROJECT_A}")
    _step("project save returns 200", r.status_code == 200,
          f"status={r.status_code} body={r.text[:120]}")

    layout = _sample_layout()
    r = client.put(f"/api/projects/{PROJECT_A}/layout", json=layout)
    _step("PUT layout returns 200", r.status_code == 200,
          f"status={r.status_code} body={r.text[:120]}")

    r = client.get(f"/api/projects/{PROJECT_A}/layout")
    _step("GET layout returns 200", r.status_code == 200,
          f"status={r.status_code}")
    body = r.json()
    _step("layout round-trips byte-for-byte",
          body == layout,
          f"got keys={list(body.keys()) if isinstance(body, dict) else type(body).__name__}")


def test_put_to_missing_project_404() -> None:
    print("\n[2] PUT layout to non-existent project → 404")
    client = _make_client()
    _cleanup_projects()
    r = client.put("/api/projects/_qa_layout_missing/layout", json=_sample_layout())
    _step("PUT on missing project returns 404",
          r.status_code == 404,
          f"status={r.status_code} body={r.text[:200]}")


def test_get_on_no_layout_returns_empty() -> None:
    print("\n[3] GET layout on project with no layout.json → {}")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    r = client.post(f"/api/projects/{PROJECT_A}")
    _step("project save returns 200", r.status_code == 200)
    # Don't PUT a layout — GET should return empty dict, not 404.
    r = client.get(f"/api/projects/{PROJECT_A}/layout")
    _step("GET returns 200 (not 404)", r.status_code == 200)
    _step("GET returns empty object", r.json() == {},
          f"got {r.json()}")


def test_get_on_corrupt_layout_returns_empty() -> None:
    print("\n[4] GET layout on corrupt layout.json → {} (no 500)")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    r = client.post(f"/api/projects/{PROJECT_A}")
    _step("project save returns 200", r.status_code == 200)
    # Write garbage to layout.json directly.
    base = pathlib.Path(__file__).resolve().parent.parent / "projects"
    layout_path = base / PROJECT_A / "layout.json"
    layout_path.write_text("{ not valid json ::: }")
    r = client.get(f"/api/projects/{PROJECT_A}/layout")
    _step("GET on corrupt file returns 200 (defensive degradation)",
          r.status_code == 200,
          f"status={r.status_code}")
    _step("GET on corrupt file returns empty dict",
          r.json() == {},
          f"got {r.json()}")


def test_layout_in_bundle() -> None:
    print("\n[5] Saved layout is included in the .pypsaproj.zip bundle")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    r = client.post(f"/api/projects/{PROJECT_A}")
    _step("project save returns 200", r.status_code == 200)
    r = client.put(f"/api/projects/{PROJECT_A}/layout", json=_sample_layout())
    _step("PUT layout returns 200", r.status_code == 200)

    r = client.get(f"/api/projects/{PROJECT_A}/bundle")
    _step("bundle download returns 200", r.status_code == 200,
          f"status={r.status_code}")
    if r.status_code != 200:
        return
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    _step("bundle contains layout.json",
          "layout.json" in names,
          f"names = {names}")
    if "layout.json" in names:
        body = json.loads(zf.read("layout.json"))
        _step("bundled layout.json matches what was PUT",
              body == _sample_layout(),
              f"keys={list(body.keys()) if isinstance(body, dict) else '?'}")


def test_re_put_overwrites() -> None:
    print("\n[6] Re-PUT overwrites (full replacement, not merge)")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    client.post(f"/api/projects/{PROJECT_A}")

    layout_v1 = _sample_layout()
    client.put(f"/api/projects/{PROJECT_A}/layout", json=layout_v1)

    layout_v2 = {
        "version": 1,
        "savedAt": 1800000000000,
        "nodes": [{"id": "Solo Bus", "canvasX": 0.0, "canvasY": 0.0}],
        "edges": [],
    }
    r = client.put(f"/api/projects/{PROJECT_A}/layout", json=layout_v2)
    _step("second PUT returns 200", r.status_code == 200)
    r = client.get(f"/api/projects/{PROJECT_A}/layout")
    body = r.json()
    _step("GET returns the second PUT exactly (no merge artefacts)",
          body == layout_v2,
          f"got nodes count = {len(body.get('nodes', []))}, expected 1")


def test_layout_per_project_isolation() -> None:
    print("\n[7] Per-project layout isolation: A and B never share state")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    client.post(f"/api/projects/{PROJECT_A}")
    PyPSAService.set_network(_seed_minimal_network())
    client.post(f"/api/projects/{PROJECT_B}")

    layout_a = {**_sample_layout(), "savedAt": 111}
    layout_b = {**_sample_layout(), "savedAt": 222,
                "nodes": [{"id": "OnlyInB", "canvasX": 999.0, "canvasY": 999.0}],
                "edges": []}
    client.put(f"/api/projects/{PROJECT_A}/layout", json=layout_a)
    client.put(f"/api/projects/{PROJECT_B}/layout", json=layout_b)

    body_a = client.get(f"/api/projects/{PROJECT_A}/layout").json()
    body_b = client.get(f"/api/projects/{PROJECT_B}/layout").json()
    _step("project A layout matches what was PUT to A",
          body_a == layout_a,
          f"A.savedAt = {body_a.get('savedAt')}")
    _step("project B layout matches what was PUT to B",
          body_b == layout_b,
          f"B.savedAt = {body_b.get('savedAt')}")
    _step("project B's 'OnlyInB' node did NOT leak into A",
          all(n.get("id") != "OnlyInB" for n in body_a.get("nodes", [])),
          f"A.nodes = {body_a.get('nodes')}")


def test_save_load_cycle_preserves_layout() -> None:
    """
    Mirrors the front-end's auto-save flow: save project (creates dir +
    network.nc), PUT layout (lands in layout.json), wipe the network from
    memory, re-load the project via projects_router.load_project. The
    layout must survive that round-trip independent of the netcdf.
    """
    print("\n[8] Save → wipe in-memory → load: layout.json persists")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    r = client.post(f"/api/projects/{PROJECT_A}")
    _step("project save returns 200", r.status_code == 200)
    layout = _sample_layout()
    r = client.put(f"/api/projects/{PROJECT_A}/layout", json=layout)
    _step("PUT layout returns 200", r.status_code == 200)

    # Wipe the in-memory network — the front-end equivalent is reloading the
    # page or switching projects. Then load the saved project back.
    PyPSAService.reset_network()
    r = client.get(f"/api/projects/{PROJECT_A}")
    _step("project load returns 200", r.status_code == 200,
          f"status={r.status_code} body={r.text[:120]}")
    # Network is back in memory (load happens server-side). Now GET layout —
    # this is what the canvas does on mount via fetchLayoutFor().
    r = client.get(f"/api/projects/{PROJECT_A}/layout")
    body = r.json()
    _step("post-load layout matches pre-save layout",
          body == layout,
          f"savedAt={body.get('savedAt')} (expected {layout['savedAt']})")
    if isinstance(body, dict):
        _step("post-load node count survives", len(body.get("nodes", [])) == 4,
              f"got {len(body.get('nodes', []))}")
        _step("post-load edge count survives", len(body.get("edges", [])) == 2,
              f"got {len(body.get('edges', []))}")


def test_unload_keepalive_put_accepted() -> None:
    """
    Browser-refresh fast path: the frontend's `persistLayoutOnUnload`
    issues a `PUT` with `fetch(... keepalive: true)` from a `pagehide`
    listener. That request carries the same Content-Type as a normal save
    but the page is mid-unload — verify the backend accepts it as-is so
    the in-flight drag survives. Simulating it here is equivalent to a
    plain PUT (FastAPI sees the same body), but the test pins the contract
    in case anyone tightens the route later (e.g. CORS preflight, custom
    headers required).
    """
    print("\n[10] Unload-path PUT accepted (browser refresh survives)")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    client.post(f"/api/projects/{PROJECT_A}")
    # Equivalent of fetch(... method:'PUT', body:JSON.stringify(layout),
    # headers:{Content-Type:'application/json'}, keepalive:true) — the
    # keepalive flag is a network-stack hint, not a server-visible header.
    layout = _sample_layout()
    r = client.request(
        "PUT",
        f"/api/projects/{PROJECT_A}/layout",
        content=json.dumps(layout),
        headers={"Content-Type": "application/json"},
    )
    _step("unload-style PUT (raw body, JSON content-type) returns 200",
          r.status_code == 200,
          f"status={r.status_code} body={r.text[:120]}")
    r = client.get(f"/api/projects/{PROJECT_A}/layout")
    _step("layout round-trips through the unload path",
          r.json() == layout)


def test_oversized_payload_413() -> None:
    print("\n[9] Oversized layout payload returns 413")
    client = _make_client()
    _cleanup_projects()
    PyPSAService.set_network(_seed_minimal_network())
    client.post(f"/api/projects/{PROJECT_A}")
    # Build a payload larger than _MAX_LAYOUT_BYTES — read the cap dynamically
    # so the test stays correct if the limit changes.
    from routers.projects import _MAX_LAYOUT_BYTES
    huge_node_count = _MAX_LAYOUT_BYTES // 50 + 100
    fat_payload = {
        "version": 1,
        "savedAt": 0,
        "nodes": [
            {"id": f"node_{i:08d}", "canvasX": i * 1.0, "canvasY": i * 2.0}
            for i in range(huge_node_count)
        ],
        "edges": [],
    }
    r = client.put(f"/api/projects/{PROJECT_A}/layout", json=fat_payload)
    _step("oversized PUT returns 413 (Request Entity Too Large)",
          r.status_code == 413,
          f"status={r.status_code} body={r.text[:200]}")


# ── Driver ─────────────────────────────────────────────────────────────


def main() -> int:
    try:
        for fn in (
            test_put_and_get_round_trip,
            test_put_to_missing_project_404,
            test_get_on_no_layout_returns_empty,
            test_get_on_corrupt_layout_returns_empty,
            test_layout_in_bundle,
            test_re_put_overwrites,
            test_layout_per_project_isolation,
            test_save_load_cycle_preserves_layout,
            test_unload_keepalive_put_accepted,
            test_oversized_payload_413,
        ):
            try:
                fn()
            except Exception as e:
                _crashed("scenario", e)
                import traceback; traceback.print_exc()
    finally:
        _cleanup_projects()
    print()
    print("=" * 60)
    print(f"Total: {PASS + FAIL}  Pass: {PASS}  Fail: {FAIL}")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
