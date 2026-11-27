"""The site-location run gate (TEMPLATE-GAPS-1 Q1 a, template-e2e 01).

A missing site location blocks Run like any other required input. One predicate
decides it, and both the server's start paths (`POST /run`, `/run_ac_pf`, the
reliability study start) and the project flow's `site_location_missing` item
call it, so the Run button, the chat and a direct call agree.

The location is set when a bus is placed (the RP-01 rule, `_bus_coord`) or the
project's site anchor (`site_anchor.json`) is confirmed. A draft anchor does not
count, and an unreadable anchor file fails closed (not set).
"""

from __future__ import annotations

import pathlib

CODE = "site_location_missing"
MESSAGE = "The site has no location: set its latitude and longitude before running."


def placed_buses(n) -> int:
    from services.network_geometry import _bus_coord

    return sum(1 for b in n.buses.index if _bus_coord(n, b) is not None)


def anchor_confirmed(project_dir: pathlib.Path | None) -> bool:
    if project_dir is None:
        return False
    from services.site_scene import anchor
    from services.site_scene.contract import SceneError

    try:
        stored = anchor.read_anchor(project_dir).get("anchor")
    except SceneError:
        return False
    return bool(stored) and stored.get("confirmed") is True


def is_missing(n_buses: int, placed: int, anchored: bool) -> bool:
    """The one predicate. A network with no buses is the validator's own
    `buses_empty`, not a missing location."""
    return n_buses > 0 and placed == 0 and not anchored


def refusal(n, project_dir: pathlib.Path | None) -> dict | None:
    """The typed refusal a run start answers (422), or None. A scratch network
    (no project directory, so no anchor can exist) is not a project and is not
    gated."""
    if project_dir is None:
        return None
    if not is_missing(len(n.buses), placed_buses(n), anchor_confirmed(project_dir)):
        return None
    return {
        "code": CODE,
        "error_kind": "site_location_missing",
        "message": MESSAGE,
        "field": "x",
        "component": "Bus",
    }


def active_refusal() -> dict | None:
    """`refusal` on the foreground network and its project. The network and the
    project directory are read inside one non-blocking hold of the network lock
    when it is free, so a project load cannot pair one project's anchor with
    another's network; a lock held elsewhere falls back to an unlocked read.

    Covered starts: `POST /run` (and the chat run tools, which call the handler),
    `POST /run_ac_pf` and the reliability study start (`check_eh_study_start`).
    Study starts are gated in their runners on the study's base project
    (`services/study/runner.py` `refuse_unlocated`, STUDY-GATE-1). Also gated (STUDY-GATE-1, issue 48):
    the live-network runners and the solve queue, at enqueue (`queued_refusal`) and at dequeue."""
    from services.network_lengths import active_project_dir
    from services.pypsa_service import PyPSAService

    lock = PyPSAService.get_lock()
    if lock.acquire(blocking=False):
        try:
            return refusal(PyPSAService.get_network(), active_project_dir())
        finally:
            lock.release()
    return refusal(PyPSAService.get_network(), active_project_dir())


def queued_refusal(
    project_key: str | None, project_dir: pathlib.Path | None
) -> dict | None:
    """`refusal` on the network a queued job of this project will solve: the
    resident context's when the project is resident (the job solves that one in
    place), else the saved `network.nc` the job hydrates from. A confirmed
    anchor settles it without loading anything."""
    if project_dir is None or anchor_confirmed(project_dir):
        return None
    from services.pypsa_service import PyPSAService

    resident = PyPSAService.get_context(project_key) if project_key else None
    if resident is not None:
        # Non-blocking: a running solve holds this lock for its whole run, and a
        # queue enqueue must not wait it out. Not got: read unlocked (as
        # `active_refusal` does); the dequeue re-check is the backstop.
        lock = resident.mutation_lock
        if lock.acquire(blocking=False):
            try:
                return refusal(resident.network, project_dir)
            finally:
                lock.release()
        return refusal(resident.network, project_dir)
    nc_path = project_dir / "network.nc"
    if not nc_path.exists():
        return None
    import pypsa

    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, nc_path)
    return refusal(n, project_dir)
