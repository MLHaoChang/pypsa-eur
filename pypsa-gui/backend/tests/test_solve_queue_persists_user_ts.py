"""
A queue solve persists the solved project's OWN user time series.

`solve_queue` was the last caller deciding `persist_user_ts` with
`ctx is PyPSAService._active` — a predicate that stopped meaning "is this the
foreground" at Step 0b and answers False for everything. It could only be
replaced once two things landed: `_save_context` serialising `ctx.user_ts`, and
`_hydrate_context_from_disk` restoring `user_ts.json` (without which a
dispatcher-hydrated context has an EMPTY store, and persisting it would replace
a good sidecar with a netcdf-derived backup).

SCOPE, stated honestly: unlike the shutdown flush and the resident-cap eviction,
no data-loss scenario was identified for THIS call site — with the old predicate
the sidecar was simply left untouched, which is lossless for a project that
already had one. What the change buys is that the queue stops relying on a
predicate that is False by accident rather than by meaning, and that a
queue-solved project ends up with a sidecar describing it, exactly as a
foreground save would.
"""
from __future__ import annotations

import json
import time
import uuid

import pandas as pd
import pypsa

from services.solve_queue import solve_queue
from tests.conftest import build_network


def _wait_for_terminal(job_id, timeout: float = 90.0) -> dict:
    job_id = uuid.UUID(str(job_id))
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = solve_queue.get_job(job_id) or {}
        if job.get("status") in ("completed", "failed", "aborted"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job did not finish within {timeout}s")


def _profiled_network() -> pypsa.Network:
    """`build_network` with a TIME-VARYING load, so there is an input profile."""
    n = build_network(solve=False)
    idx = n.snapshots
    for name in n.loads.index:
        n.loads_t.p_set[name] = pd.Series([50.0] * len(idx), index=idx)
    return n


def test_a_queue_solve_leaves_the_project_a_sidecar_describing_itself(
    client, install_network, tmp_projects_dir, project_storage_dir, session_ctx
):
    install_network(_profiled_network(), name="Q1")
    assert client.post(
        "/api/projects/Q1", params={"force": True, "rebind": True}
    ).status_code == 200

    sidecar = project_storage_dir("Q1") / "user_ts.json"
    sidecar.unlink(missing_ok=True)

    r = client.post("/api/simulation/queue", json={"project_id": "Q1"})
    assert r.status_code == 200, r.text
    done = _wait_for_terminal(r.json()["id"])
    assert done["status"] == "completed", done

    assert sidecar.exists(), (
        "the queue solve saved the project without writing a user_ts.json, so "
        "its input profiles live only in the netcdf"
    )
    saved = json.loads(sidecar.read_text())
    assert saved.get("loads", {}).get("p_set"), (
        f"the sidecar does not describe this project's own load profile: {saved!r}"
    )
