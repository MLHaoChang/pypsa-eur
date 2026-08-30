"""
A network swap must be REFUSED while a study is running (Phase 11).

A study's worker closes over the ``pypsa.Network`` object captured before it
started (``routers/results.py``: ``n = PyPSAService.get_network()``, then
``_solve_once(..., n, ...)`` on every iterate). Replacing the network
therefore does not STOP a running study — it DETACHES it. The study keeps
solving the object it captured and keeps publishing into ``solver_state``,
which ``reset_network`` carries forward to the new project. So:

* the new project's Adequacy tab fills in LIVE with the old project's study,
  which is worse than a stale record because it looks fresh;
* the study's own answer is silently wrong to its user;
* a ``restore="final"`` loop writes the OLD project's certified
  ``ens_cap_permyriad`` / ``reserve_margin`` into the NEW project's solver
  config, and re-solves the new network at it.

Phase 10 fixed the finished-record half and pinned the deliberate decision NOT
to clear a live record. This is the half it deferred.

The guard lives at the ONE choke point (`PyPSAService.reset_network`) and
refuses by default, so a route added later is protected by construction rather
than by someone remembering to call a helper.
"""
from __future__ import annotations

import threading

import pandas as pd
import pypsa
import pytest

from services.project_context import (
    ABORTABLE_STUDIES,
    STUDY_KEYS,
    STUDY_LABELS,
)


def _network(load_mw: float = 100.0, periods: int = 4) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=periods, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=load_mw)
    n.add("Generator", "firm", bus="b", carrier="gas", p_nom=load_mw * 0.8,
          marginal_cost=10.0, outage_rate_value=0.1,
          outage_rate_basis="EFORd", mttr_hours=4.0)
    return n


class _LiveStudy:
    """A REAL live daemon thread under a study key, in a GIVEN state dict.

    Every guard tests ``thread.is_alive()``, so a sentinel dict would prove
    nothing — the same discipline the Phase-7 mesh tests use.

    ★ The state dict is passed in rather than fetched from
    `PyPSAService.get_solver_state()`, and that is load-bearing. A request
    resolves a SESSION-scoped context, whose `solver_state` is a different
    object from the one `_active` holds outside a request. Seeding the wrong
    one makes the route see no study at all, and the test then passes or fails
    for a reason that has nothing to do with the guard. `session_state(client)`
    hands back the dict the route itself will read — the same fixture the
    Phase-7 mesh tests use for the same reason.
    """

    def __init__(self, state: dict, key: str = "mc"):
        self.state = state
        self.key = key
        self._release = threading.Event()
        self._thread = threading.Thread(
            target=self._release.wait, daemon=True, name=f"fake-{key}")

    def __enter__(self):
        self._thread.start()
        self.state[self.key] = {
            "status": "running", "result": None, "error": None,
            "started_at": 1.0, "finished_at": None,
            "thread": self._thread,
        }
        return self

    def __exit__(self, *exc):
        self._release.set()
        self._thread.join(timeout=5)
        self.state[self.key] = None
        return False


# Every user-facing route that REPLACES the foreground network. Each one calls
# `PyPSAService.reset_network()`; each one, during a study, detaches it.
SWAP_ROUTES = [
    ("POST", "/api/network/reset", None),
    ("POST", "/api/network/undo", None),
]


def _bus_names(client) -> list[str]:
    """The live network's buses, read THROUGH THE API.

    ★ Not `PyPSAService.get_network() is before`: object identity is not a
    valid probe here. `install_network` writes the PROCESS foreground while a
    request resolves a SESSION-scoped context, so the two can legitimately be
    different objects with no swap having happened — an identity assertion
    fails for a reason that has nothing to do with the guard. What the test
    actually cares about is whether the user's network survived, and that is
    exactly what the API answers.
    """
    r = client.get("/api/network/buses")
    assert r.status_code == 200, r.text
    return sorted(b["name"] for b in r.json())


def _seed_undo_history(client) -> None:
    """Give `/api/network/undo` something to undo.

    Without history it answers 409 "Nothing to undo" from its OWN precondition
    and never reaches the guard — which would make the refusal test pass for
    entirely the wrong reason, and the no-study test fail for one.
    """
    r = client.post("/api/network/buses",
                    json={"name": "undo_probe", "v_nom": 380.0,
                          "carrier": "AC"})
    assert r.status_code in (200, 201), r.text


@pytest.mark.parametrize("method,url,body", SWAP_ROUTES,
                         ids=[r[1] for r in SWAP_ROUTES])
def test_a_network_replacing_route_is_refused_while_a_study_runs(
        client, install_network, session_state, method, url, body):
    """★ A1: 409, and the network is NOT swapped.

    Bite (verified): `allow_during_study=True` by default in `reset_network`.
    """
    install_network(_network(load_mw=137.0))
    _seed_undo_history(client)
    before = _bus_names(client)
    assert before, "the fixture network has no buses to lose"

    with _LiveStudy(session_state(client), "mc"):
        r = client.request(method, url, json=body)
        assert r.status_code == 409, (
            f"{method} {url} replaced the network under a running study: "
            f"{r.status_code} {r.text[:200]}")
        detail = r.json()["detail"]
        assert STUDY_LABELS["mc"] in detail, (
            f"the refusal does not name the study: {detail}")
        # …and it really did not swap: the user's network is still there.
        assert _bus_names(client) == before, (
            "the route returned 409 but had already changed the network")


@pytest.mark.parametrize("method,url,body", SWAP_ROUTES,
                         ids=[r[1] for r in SWAP_ROUTES])
def test_the_same_route_still_works_with_no_study_running(
        client, install_network, method, url, body):
    """★ A2: the guard must not become a permanent block.

    A refusal that never lifts is not a guard, it is an outage. Bite: refuse
    unconditionally (drop the `if detail` test).
    """
    install_network(_network())
    _seed_undo_history(client)
    r = client.request(method, url, json=body)
    assert r.status_code != 409, (
        f"{method} {url} is refused with NO study running: {r.text[:200]}")


def test_a_FINISHED_study_blocks_nothing(client, install_network):
    """★ A6: only a LIVE study refuses; a finished record is Phase 10's job
    (it gets cleared) and must not wedge the surface."""
    from services.pypsa_service import PyPSAService

    install_network(_network())
    with PyPSAService.get_solver_state_lock():
        PyPSAService.get_solver_state()["mc"] = {
            "status": "done", "result": {"marker": "FINISHED"}, "error": None,
            "started_at": 1.0, "finished_at": 2.0, "thread": None,
        }
    r = client.post("/api/network/reset")
    assert r.status_code != 409, r.text
    assert PyPSAService.get_solver_state().get("mc") is None, (
        "Phase 10's clear did not run on the swap")


def test_allow_during_study_still_swaps_and_still_keeps_the_live_record(
        client, install_network):
    """★ A5: the explicit opt-out works, and Phase 10's pin still holds on it.

    An internal caller that genuinely must proceed can, and on that path a
    LIVE record is still not cleared — clearing it would make
    `study_running()` False while the worker is alive, breaking the 409 mesh.

    Bite: make `allow_during_study` clear the record too.
    """
    from services.pypsa_service import PyPSAService
    from services.study_state import study_running

    install_network(_network())
    # Called DIRECTLY, outside a request, so `_active`'s own state is the one
    # `reset_network` reads — no session context is involved on this path.
    with _LiveStudy(PyPSAService.get_solver_state(), "mc"):
        PyPSAService.reset_network(allow_during_study=True)
        assert PyPSAService.get_solver_state().get("mc") is not None, (
            "the opt-out path cleared a live record")
        assert study_running("mc") is True


@pytest.mark.parametrize("key", STUDY_KEYS)
def test_the_refusal_names_the_study_and_only_offers_a_real_remedy(
        client, install_network, session_state, key):
    """★ A3: never invent a control the user does not have.

    Only `coupling_loop` and `margin_loop` expose an `/abort` route. The
    Phase-7 sentence told EVERY blocked user to "abort it", which for the MC,
    the frontier and the FMEA sweep names a button that does not exist — and
    this phase would have propagated that to seven more routes.

    Bite (verified): use the same remedy clause for every study.
    """
    install_network(_network())
    with _LiveStudy(session_state(client), key):
        r = client.post("/api/network/reset")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert STUDY_LABELS[key] in detail, detail
        if key in ABORTABLE_STUDIES:
            assert "abort it" in detail, detail
        else:
            assert "cannot be aborted" in detail, (
                f"{key} has no abort route, but the refusal offers one: "
                + detail)


def test_abortable_studies_matches_the_routes_that_actually_exist():
    """★ A4: the copy cannot drift from the routes.

    `ABORTABLE_STUDIES` is a claim about the HTTP surface. Derive the truth
    from the source and compare, so giving the MC an abort route later fails
    HERE rather than shipping a refusal that under-sells what the user can do.

    Bite: add "mc" to ABORTABLE_STUDIES.
    """
    import pathlib
    import re

    src = pathlib.Path("routers/results.py").read_text()
    have_abort = set(re.findall(r'@results_router\.post\("/(\w+)/abort"\)', src))
    assert have_abort == set(ABORTABLE_STUDIES), (
        f"studies with an /abort route: {sorted(have_abort)}; "
        f"ABORTABLE_STUDIES says: {sorted(ABORTABLE_STUDIES)}")


def test_a_CRASHED_worker_does_not_wedge_every_route_forever(
        client, install_network, session_state):
    """★ A6b: a record that says "running" over a DEAD thread must not block.

    Recorded honestly, because the first version of this file claimed this
    bite and did not carry it: `test_a_FINISHED_study_blocks_nothing` named
    "test `status` alone, without `thread.is_alive()`" as its broken variant,
    but its fixture record said `status: "done"` — so a status-only check
    returned False there too and the variant passed. The test was weaker than
    its own docstring.

    The hazard is specific: a worker that dies without writing its terminal
    status leaves `status == "running"` behind forever. Testing liveness by
    the STATUS STRING would then refuse every network-replacing route for the
    rest of the session — a permanent, unrecoverable-without-restart outage
    caused by the guard meant to protect the user. `record_is_running` asks
    the thread, which is why it cannot happen.

    Bite (verified): `return bool(record.get("status") == "running")`.
    """
    install_network(_network())
    state = session_state(client)

    dead = threading.Thread(target=lambda: None, name="crashed-study")
    dead.start()
    dead.join(timeout=5)
    assert not dead.is_alive(), "the fixture thread refused to die"

    # Exactly the shape a crashed worker leaves behind.
    state["mc"] = {
        "status": "running", "result": None, "error": None,
        "started_at": 1.0, "finished_at": None, "thread": dead,
    }
    try:
        r = client.post("/api/network/reset")
        assert r.status_code != 409, (
            "a dead worker's stale 'running' record wedged the route: "
            + r.text[:200])
    finally:
        state["mc"] = None
