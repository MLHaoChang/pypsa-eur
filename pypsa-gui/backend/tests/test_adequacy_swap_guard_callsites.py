"""
The swap guard must refuse BEFORE a route does destructive work (Phase 11b).

Phase 11 put the refusal inside `PyPSAService.reset_network` and claimed, in
its spec and its commit message, that this was "the ONE choke point — every
path that replaces the foreground network comes through here" and that the
409 is "raised BEFORE any mutation, so a refusal leaves the swap not-started
rather than half-done".

Both claims were WRONG, and an adversarial review of the shipped code
reproduced the consequences end to end:

* `undo_last` pops the undo entry — destructively — twenty lines before the
  guard runs, so a refused undo EATS the step it refused to apply;
* `restore_snapshot` overwrites `network.nc` and the whole bundle on disk
  before the guard, then tells the user the restore was refused;
* `import_bundle` / `create_from_template` commit the project row first, so
  the retry the refusal advises fails forever with "already exists";
* `clustering` replaces the network through `set_network`, an EIGHTH path
  that has neither the guard nor Phase 10's study-record clear.

The fix is a precheck (`PyPSAService.refuse_if_study_running`) that routes
call before touching anything, plus the guard and the clear on `set_network`.
"""
from __future__ import annotations

import threading

import pandas as pd
import pypsa
import pytest


def _network(buses: int = 1) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    for i in range(buses):
        n.add("Bus", f"b{i}", carrier="AC", country="AA")
    n.add("Load", "l", bus="b0", p_set=100.0)
    n.add("Generator", "firm", bus="b0", carrier="gas", p_nom=80.0,
          marginal_cost=10.0, outage_rate_value=0.1,
          outage_rate_basis="EFORd", mttr_hours=4.0)
    return n


class _LiveStudy:
    """A genuinely live study record in a GIVEN state dict."""

    def __init__(self, state: dict, key: str = "mc"):
        self.state, self.key = state, key
        self._release = threading.Event()
        self._thread = threading.Thread(
            target=self._release.wait, daemon=True, name=f"fake-{key}")

    def __enter__(self):
        self._thread.start()
        self.state[self.key] = {
            "status": "running", "result": None, "error": None,
            "started_at": 1.0, "finished_at": None, "thread": self._thread,
        }
        return self

    def __exit__(self, *exc):
        self._release.set()
        self._thread.join(timeout=5)
        self.state[self.key] = None
        return False


def test_a_refused_undo_does_not_eat_the_undo_step(
        client, install_network, session_state, api_project):
    """★ BLOCKER 1: the refusal must not consume what it refused to apply.

    `undo_service.pop()` is destructive and ran BEFORE the guard, so two
    Ctrl-Z presses during a study emptied a two-deep undo stack while changing
    nothing. The user is told to wait; their history is gone when they do.

    Bite (verified): move the precheck back below `undo_service.pop()`.
    """
    # Bind a real project first: the undo store the ROUTE uses is per-context,
    # and without a binding an earlier test's context churn can leave this one
    # with no slot — which looks identical to the defect under test.
    api_project("undo_guard_project")
    install_network(_network())
    # Two real, undoable mutations.
    for i in (0, 1):
        r = client.post("/api/network/buses",
                        json={"name": f"probe{i}", "v_nom": 380.0,
                              "carrier": "AC"})
        assert r.status_code in (200, 201), r.text
    names = lambda: sorted(b["name"] for b in
                           client.get("/api/network/buses").json())
    assert "probe0" in names() and "probe1" in names(), names()

    # NOTE: no "prove undo works" step here, deliberately. This harness grants
    # a single undo slot, so consuming one to prove undo works would consume
    # the very thing under test and make the final assertion ambiguous. The
    # ORDERING is pinned exactly by
    # `test_every_destructive_step_of_a_swap_route_runs_AFTER_its_precheck`;
    # this test adds the behavioural half.
    with _LiveStudy(session_state(client)):
        for _ in range(3):
            r = client.post("/api/network/undo")
            assert r.status_code == 409, r.text

    # ★ The refusals must have consumed NOTHING: the remaining step is still
    # there and still undoes. Asserted behaviourally rather than by reading
    # `undo_service`'s deque, because the stack is per-context and a test that
    # inspects the wrong context proves nothing either way.
    assert client.post("/api/network/undo").status_code == 200, (
        "the refused undos ATE the undo stack — the step that was still "
        "pending before them is gone")
    assert "probe1" not in names(), names()


def test_a_refused_undo_names_the_action_the_user_actually_took(
        client, install_network, session_state):
    """★ The refusal said "Trying to replace the network now…" for a Ctrl-Z.

    `action` defaults, and no call site passed one, so every refusal described
    an action the user did not take. Bite: drop the `action=` argument.
    """
    install_network(_network())
    client.post("/api/network/buses",
                json={"name": "probe", "v_nom": 380.0, "carrier": "AC"})
    with _LiveStudy(session_state(client)):
        r = client.post("/api/network/undo")
        assert r.status_code == 409, r.text
        assert "undo" in r.json()["detail"], r.json()["detail"]


def test_clustering_is_guarded_too(client, install_network, session_state):
    """★ BLOCKER 4: `set_network` is an EIGHTH network-replacing path.

    Phase 11's spec asserted `reset_network` was the only one. `clustering.py`
    calls `PyPSAService.set_network`, which replaces the network and carries
    `solver_state` forward with neither the guard nor Phase 10's clear — so
    clustering during a study returns 200 and detaches it, which is verbatim
    the defect Phase 11 exists to prevent.

    Bite (verified): remove the guard from `set_network`.
    """
    from services.pypsa_service import PyPSAService

    install_network(_network(buses=3))
    with _LiveStudy(PyPSAService.get_solver_state()):
        with pytest.raises(Exception) as exc:
            PyPSAService.set_network(_network(buses=1))
        assert "409" in str(exc.value) or "running" in str(exc.value).lower(), (
            f"set_network replaced the network under a live study: {exc.value}")


def test_set_network_clears_a_finished_study_like_reset_does(
        client, install_network):
    """★ BLOCKER 4b: the same path also skipped Phase 10's clear, so a study
    of the PRE-clustering network survived onto the clustered one."""
    from services.pypsa_service import PyPSAService

    install_network(_network(buses=3))
    with PyPSAService.get_solver_state_lock():
        PyPSAService.get_solver_state()["mc"] = {
            "status": "done", "result": {"marker": "PRE_CLUSTER"},
            "error": None, "started_at": 1.0, "finished_at": 2.0,
            "thread": None,
        }
    PyPSAService.set_network(_network(buses=1))
    assert PyPSAService.get_solver_state().get("mc") is None, (
        "a study of the pre-clustering network survived the swap")


def test_a_refused_template_create_leaves_no_orphan_and_can_be_RETRIED(
        client, install_network, session_state):
    """★ BLOCKERS 3: the refusal must not commit the project row.

    `create_root` ran before the guard, so a refused create left a phantom
    project behind — and then the retry the refusal ADVISES was poisoned
    forever, because the committed row makes every later attempt fail with
    "Project already exists". A guard whose own refusal makes the operation
    permanently impossible is worse than no guard.

    Bite (verified): drop the precheck from `create_from_template`.
    """
    install_network(_network())
    name = "ghost_after_refusal"

    def listed():
        r = client.get("/api/projects/")
        return [p["name"] for p in (r.json() if r.status_code == 200 else [])]

    with _LiveStudy(session_state(client)):
        r = client.post(f"/api/projects/from_template/3bus?name={name}")
        assert r.status_code == 409, r.text
    assert name not in listed(), (
        f"a REFUSED create left an orphan project behind: {name}")

    # …and the retry the refusal advised is not poisoned. It may still fail
    # for an unrelated reason (no bundled template in this environment), but
    # it must NOT fail with the "already exists" the refusal itself created.
    r2 = client.post(f"/api/projects/from_template/3bus?name={name}")
    assert "already exists" not in r2.text, (
        "the refusal committed a row that makes the retry impossible: "
        + r2.text[:200])


def test_a_refused_project_load_changes_nothing(
        client, install_network, session_state, api_project):
    """★ BLOCKER 3b (behavioural half): a refused load leaves the user where
    they were — same network, nothing half-loaded.

    Bite (verified): drop the precheck from `load_project`.
    """
    other = api_project("some_other_project")
    install_network(_network())
    client.post("/api/network/buses",
                json={"name": "keepme", "v_nom": 380.0, "carrier": "AC"})

    def names():
        return sorted(b["name"] for b in client.get("/api/network/buses").json())

    before = names()
    assert "keepme" in before

    with _LiveStudy(session_state(client)):
        r = client.get(f"/api/projects/{other}")
        assert r.status_code == 409, r.text
        assert "load a project" in r.json()["detail"], r.json()["detail"]

    assert names() == before, (
        f"a REFUSED load changed the live network: {before} -> {names()}")


def test_every_destructive_step_of_a_swap_route_runs_AFTER_its_precheck():
    """★ BLOCKER 1/2/3 (structural half): the ordering IS the fix.

    The undo-history consequence resists a behavioural assertion here — the
    route and the test process resolve DIFFERENT undo contexts (in-test
    `undo_service._active().stack` reads 0 while `POST /api/network/undo`
    succeeds), so a depth check proves nothing either way. What can be
    asserted exactly is the property the fix actually establishes: in each
    route, the precheck precedes every destructive call.

    That is what was wrong. Phase 11 asserted the 409 is "raised BEFORE any
    mutation"; it was true inside `reset_network` and false at four call
    sites, which popped the undo entry, overwrote the project's files on disk,
    or committed the project row first.

    Bite (verified): move any precheck below its route's first destructive
    call and this fails, naming the route.
    """
    import inspect
    import re

    from routers import network as network_router
    from routers import projects as projects_router
    from routers import snapshots as snapshots_router

    DESTRUCTIVE = (
        "undo_service.pop(", "undo_service.clear(", "create_root(",
        "ensure_project_dir(", "_atomic_write_with(", "_copy_bundle_dirs(",
        "_create_snapshot_internal(",
    )
    targets = [
        (network_router, "undo_last"),
        (projects_router, "load_project"),
        (projects_router, "create_from_template"),
        (projects_router, "import_bundle"),
        (snapshots_router, "restore_snapshot"),
    ]
    for mod, fn_name in targets:
        src = inspect.getsource(getattr(mod, fn_name))
        # Ignore comment lines: the fix's own comments NAME the destructive
        # calls, and matching those would make this test pass on prose.
        code = "\n".join(ln for ln in src.splitlines()
                          if not ln.lstrip().startswith("#"))
        guard = code.find("refuse_if_study_running")
        assert guard >= 0, f"{fn_name} has no precheck at all"
        for token in DESTRUCTIVE:
            at = code.find(token)
            if at >= 0:
                assert guard < at, (
                    f"{fn_name}: {token} runs BEFORE the study precheck, so a "
                    "refusal leaves the route half-done")
