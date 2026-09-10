"""
The save guards in `routers/projects.py::_save_context`, and the invariant that
they run inside the mutation lock.

Three guards stand between a save request and destroyed data:

  * **identity** — `expect` asserts which project the caller believes is active;
    a mismatch means the network was swapped out from under it (another tab, an
    external client) and saving would write the wrong network.
  * **cross-project claim** — the active network is bound to A, the caller is
    saving to B, and B already has a network on disk. Silently overwriting B was
    the v6 F1 footgun: no UI signal that B had just been destroyed. The caller
    must opt in with `rebind` (Save-As) or `force`.
  * **empty network** — refuse to overwrite a project that had buses with a
    blank network. This is what stops autosave from wiping a project after a
    server restart, when the browser still has `currentProject` in localStorage
    but the backend network is empty.

Why this file replaces a source-line test
-----------------------------------------
The invariant these guards depend on is a TOCTOU one, and it was previously
asserted by `test_save_guard_source_placement_invariant` in
`tests/test_chat_state_carry.py`, which read `routers/projects.py` as TEXT and
required

    line("with ctx.mutation_lock:")
      < line("loaded = ctx.loaded_project")
      < line("already exists on disk and the in-memory")
      < line("if not force and nc_path.exists() and n.buses.empty:")

That is a proxy for the real requirement, and a brittle one in a specific way:
`line_with` returns the FIRST match anywhere in the file. Move the guards into a
helper defined above `_save_context` and the four anchors start naming lines in
different functions — the assertion keeps passing while proving nothing. It also
cannot survive re-wording a message, which its own comment admits ("Future
refactors that re-word the message must also update this anchor").

So this file asserts the property itself, by observation rather than by layout:
the lock is entered, `loaded_project` is read while it is held, and the 409 is
raised while it is STILL held — proven by the lock's `__exit__` receiving the
exception. That holds however the code is arranged, and it fails if a future
refactor lifts a guard outside the lock, which is the regression the original
test existed to catch.
"""
from __future__ import annotations

import pathlib

import pypsa
import pytest
from fastapi import HTTPException

from routers import projects as projects_router
from services.project_context import ProjectContext


class _RecordingLock:
    """A mutation lock that records how it was used, and by what."""

    def __init__(self, events: list[str]):
        self.events = events
        self.held = False
        self.exception_seen: type[BaseException] | None = None

    def __enter__(self):
        self.held = True
        self.events.append("lock_enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.held = False
        self.exception_seen = exc_type
        self.events.append(f"lock_exit:{exc_type.__name__ if exc_type else 'clean'}")
        return False


class _WatchedContext(ProjectContext):
    """A ProjectContext that records each read of `loaded_project`."""

    def __init__(self, *args, events: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_events", events)

    @property
    def loaded_project(self):  # type: ignore[override]
        self._events.append(
            "loaded_read_held" if self.mutation_lock.held else "loaded_read_UNLOCKED"
        )
        return self.__dict__.get("_loaded_project_value")

    @loaded_project.setter
    def loaded_project(self, value):
        self.__dict__["_loaded_project_value"] = value


def _network_with_buses(n_buses=2):
    n = pypsa.Network()
    for i in range(n_buses):
        n.add("Bus", f"B{i}")
    return n


@pytest.fixture
def saved_project(tmp_projects_dir, monkeypatch):
    """A project that already exists on disk, so the guards have something to protect."""
    monkeypatch.setattr(projects_router, "PROJECTS_DIR", tmp_projects_dir)
    ctx = ProjectContext(network=_network_with_buses(), loaded_project=None)
    projects_router._save_context(ctx, "existing", force=True)
    assert (tmp_projects_dir / "existing" / "network.nc").exists()
    return tmp_projects_dir


def _drive_guarded_save(tmp_projects_dir, *, loaded, name, **kw):
    """Run a save that the cross-project guard should refuse; return the events."""
    events: list[str] = []
    ctx = _WatchedContext(network=_network_with_buses(), events=events)
    ctx.loaded_project = loaded
    lock = _RecordingLock(events)
    object.__setattr__(ctx, "mutation_lock", lock)
    with pytest.raises(HTTPException) as excinfo:
        projects_router._save_context(ctx, name, **kw)
    return events, lock, excinfo.value


def test_the_cross_project_guard_refuses_and_says_how_to_opt_in(saved_project):
    _events, _lock, exc = _drive_guarded_save(
        saved_project, loaded="other", name="existing",
    )
    assert exc.status_code == 409
    detail = str(exc.detail)
    assert "rebind=true" in detail and "force=true" in detail, (
        "the 409 must name both opt-ins, or the caller cannot proceed"
    )


def test_the_guard_raises_while_the_mutation_lock_is_still_held(saved_project):
    """
    The invariant, asserted by observation rather than by source layout.

    `__exit__` receiving the HTTPException is direct proof that the raise
    happened INSIDE the `with` block. Lift the guard above the lock and the
    exception never reaches `__exit__`.
    """
    events, lock, _exc = _drive_guarded_save(
        saved_project, loaded="other", name="existing",
    )
    assert lock.exception_seen is HTTPException, (
        f"the 409 was raised outside the mutation lock (lock saw "
        f"{lock.exception_seen}); that is the TOCTOU race the guard exists to "
        f"avoid — another tab can swap the binding between the read and the write"
    )
    assert events[0] == "lock_enter", f"something ran before the lock: {events}"


def test_loaded_project_is_read_while_the_lock_is_held(saved_project):
    """
    Reading the binding outside the lock observes a torn value, which is the
    other half of the same race.
    """
    events, _lock, _exc = _drive_guarded_save(
        saved_project, loaded="other", name="existing",
    )
    reads = [e for e in events if e.startswith("loaded_read")]
    assert reads, "loaded_project was never read; the fixture is not exercising the guard"
    unlocked = [e for e in reads if e.endswith("UNLOCKED")]
    assert not unlocked, (
        f"loaded_project was read outside the mutation lock ({len(unlocked)} of "
        f"{len(reads)} reads); another tab's load can swap the binding mid-read"
    )


def test_rebind_opts_in_and_the_save_proceeds(saved_project, monkeypatch):
    """The documented escape hatch still works — the guard is not a wall."""
    ctx = ProjectContext(network=_network_with_buses(3), loaded_project="other")
    projects_router._save_context(ctx, "existing", rebind=True)
    assert (saved_project / "existing" / "network.nc").exists()


def test_force_opts_in_too(saved_project):
    ctx = ProjectContext(network=_network_with_buses(3), loaded_project="other")
    projects_router._save_context(ctx, "existing", force=True)


def test_an_empty_network_cannot_overwrite_a_populated_project(saved_project):
    """
    The autosave-after-restart guard. Without it, a browser that still has
    `currentProject` in localStorage wipes the project on its next autosave.
    """
    ctx = ProjectContext(network=pypsa.Network(), loaded_project="existing")
    with pytest.raises(HTTPException) as excinfo:
        projects_router._save_context(ctx, "existing")
    assert excinfo.value.status_code == 409
    assert "empty network" in str(excinfo.value.detail)


def test_the_identity_guard_refuses_a_mismatched_expect(saved_project):
    ctx = ProjectContext(network=_network_with_buses(), loaded_project="A")
    with pytest.raises(HTTPException) as excinfo:
        projects_router._save_context(ctx, "A", expect="B")
    assert excinfo.value.status_code == 409
    assert "not 'B'" in str(excinfo.value.detail)
