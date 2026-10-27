"""
D12 / D04 measurement: native generate (preview), Apply (durable commit) and
read-back for synthetic campuses of 1, 100, 1000 and 2000 (= MAX_BUILDINGS)
buildings. Run from pypsa-gui/backend:

    python ../../.scratch/site-scene-generation/sc-00-n/evidence/measure_native.py > \
        ../../.scratch/site-scene-generation/sc-00-n/evidence/d12-native-budgets.json

Synthetic inputs only (D08): 10 x 20 x 8 m rectangles on a 25 m grid, and the
same count as 12-vertex source rings for the vertex-heavy case.
"""
from __future__ import annotations

import json
import math
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from services import site_service  # noqa: E402
from tests.site_scene_proto import native_scene, scene_store  # noqa: E402

SITE = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.57, 48.13], [11.59, 48.13], [11.59, 48.15]], "placements": {}}


def greenfield(n: int) -> dict:
    return {"greenfield": [{"id": str(i), "center": [(i % 50) * 25.0, (i // 50) * 25.0], "width": 10, "depth": 20,
                            "height": 8, "heading": (i * 7) % 360, "unit": "m"} for i in range(n)]}


def rings(n: int, k: int = 12) -> dict:
    out = []
    for i in range(n):
        lng0, lat0 = 11.575 + (i % 50) * 0.0003, 48.135 + (i // 50) * 0.0002
        ring = [[lng0 + 0.0001 * math.cos(2 * math.pi * j / k), lat0 + 0.00007 * math.sin(2 * math.pi * j / k)]
                for j in range(k)]
        out.append({"namespace": "osm", "source_id": f"way/{i}", "ring": ring, "levels": 3})
    return {"sources": out}


def timed(fn, repeat=5):
    samples = []
    for _ in range(repeat):
        t = time.perf_counter()
        out = fn()
        samples.append((time.perf_counter() - t) * 1000)
    return out, round(statistics.median(samples), 2), round(max(samples), 2)


def run(label: str, inputs: dict) -> dict:
    preview, gen_ms, gen_max = timed(lambda: native_scene.generate(SITE, inputs))
    tracemalloc.start()
    native_scene.generate(SITE, inputs)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        site_service.write_sites(project, {"version": 1, "sites": [dict(SITE)]})
        scene_store.adopt(project, "campus", scene_store.legacy_site_digest(site_service.read_sites(project), "campus"))
        changes = {"buildings": preview["buildings"]}
        prop = {"id": "p", "base_revision": 1, "input_hash": scene_store.sha256(changes), "changes": changes}
        t = time.perf_counter()
        scene_store.apply(project, "campus", prop, 1)
        apply_ms = round((time.perf_counter() - t) * 1000, 2)
        _, read_ms, _ = timed(lambda: scene_store.read_scene(project, "campus"))
        gen_bytes = (project / scene_store.closure(project, "campus")[1]).stat().st_size
    return {"case": label, **preview["stats"], "generate_ms_median": gen_ms, "generate_ms_max": gen_max,
            "generate_peak_kib": round(peak / 1024), "apply_ms": apply_ms, "read_ms_median": read_ms,
            "generation_file_bytes": gen_bytes}


def main() -> None:
    cases = [run(f"rect x{n}", greenfield(n)) for n in (1, 100, 1000, 2000)]
    cases.append(run("12-gon x2000", rings(2000)))
    # Adversarial cases at the segment-pair budget (the O(n^2) crossing check).
    for k in (64, 256):
        n = native_scene.MAX_SEGMENT_PAIRS // native_scene.segment_pairs(k)
        cases.append(run(f"{k}-gon x{n} (pair budget)", rings(n, k)))
    print(json.dumps({"python": platform.python_version(), "machine": platform.machine(),
                      "platform": platform.platform(), "limits": {
                          "MAX_BUILDINGS": native_scene.MAX_BUILDINGS,
                          "MAX_FOOTPRINT_VERTICES": native_scene.MAX_FOOTPRINT_VERTICES,
                          "MAX_TRIANGLES": native_scene.MAX_TRIANGLES,
                          "MAX_PREVIEW_BYTES": native_scene.MAX_PREVIEW_BYTES,
                          "MAX_SEGMENT_PAIRS": native_scene.MAX_SEGMENT_PAIRS},
                      "cases": cases}, indent=2))


if __name__ == "__main__":
    main()
