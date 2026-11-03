"""
"Does the in-memory network differ from what is on disk?"

Distinct from the undo stack, which answers a DIFFERENT question: "how many
undoable edits have happened since the last save". Those two coincided for a
long time, and three destructive-action guards were built on the assumption
that they always would. They do not: solver results are written straight into
the in-memory network and are never pushed to the undo stack, so a solved
project reported `depth == 0` and every guard let the work be destroyed
silently.

Deliberately NOT folded into `undo_service.clear()`. The concepts are not
synonyms — undoing to depth 0 does not make a solved network match disk — and a
future caller may legitimately want one without the other.

Set at the SINK where results are written (`solver_service.run_simulation`),
not at the HTTP layer. The first design marked dirty in the undo middleware and
was wrong twice over: the solve queue runs on a background thread with no
request at all, and even `POST /api/simulation/run` returns before its worker
finishes. Route-layer guards look complete and leave every direct caller open.

See docs/superpowers/specs/2026-08-27-unsaved-results-visibility-design.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from services.project_context import ProjectContext


def _active() -> ProjectContext:
    """
    The ACTIVE project's context. Lazy import mirrors `undo_service._active`,
    avoiding a project_context -> dirty_state -> pypsa_service -> project_context
    cycle.
    """
    from services.pypsa_service import PyPSAService  # noqa: PLC0415

    return PyPSAService.get_active_context()


def mark_dirty(ctx: ProjectContext | None = None) -> None:
    """
    Record that this context's in-memory network differs from disk.

    `ctx` is explicit for callers that are NOT running against the active
    project — a queue worker hydrates and solves its own context, and marking
    the active one there would both miss the real project and slander whichever
    project the user happens to be looking at.
    """
    (ctx or _active()).results_unsaved = True


def clear(ctx: ProjectContext | None = None) -> None:
    """
    Record that memory and disk now agree.

    Call this ONLY where that is actually true — after writing memory to disk,
    or after loading disk into memory. It is not a synonym for
    `undo_service.clear()`; check each site against the rule rather than
    pairing them by reflex.
    """
    (ctx or _active()).results_unsaved = False


def is_dirty(ctx: ProjectContext | None = None) -> bool:
    return bool((ctx or _active()).results_unsaved)


def bump_revision(ctx: ProjectContext | None = None) -> int:
    """
    Record that an edit seam was crossed on this context (P33b 10b).

    Monotonic: an edit then its undo is two bumps. The counter answers "has
    the network been edited since <a captured revision>?", which stays true
    after an undo — and that is all a sentence built on it may claim.
    """
    target = ctx or _active()
    target.network_revision = int(target.network_revision or 0) + 1
    return target.network_revision


def revision(ctx: ProjectContext | None = None) -> int:
    """The context's current edit counter (see `bump_revision`)."""
    return int((ctx or _active()).network_revision or 0)


def capture(record: dict, ctx: ProjectContext | None = None, *, lock=None) -> int:
    """
    Store the context's current edit counter on `record["network_revision"]`
    and return it: the one path a study record captures its revision by
    (LC-40b). `study_state.edited_since` compares the stored value with the
    counter at read time.

    With `lock` (a worker's capture: the mutation lock, taken whole here, so
    never call this inside a `with lock:` of the same lock; it is not
    re-entrant), the read happens under it, immediately before the study's
    first network read. Without, the counter is read alone (one int read) in
    the caller's thread before the study's thread starts. Either way an edit
    landing after the capture can only over-report "edited since", never
    miss one. Takes no other lock (no `solver_state_lock`).
    """
    target = ctx or _active()
    if lock is None:
        record["network_revision"] = revision(target)
    else:
        with lock:
            record["network_revision"] = revision(target)
    return record["network_revision"]


def hold(record: dict, ctx: ProjectContext | None = None) -> int:
    """
    A loop's own `restore="final"` hold (LC-40): bump the counter once, then
    re-capture it on `record`, and return the new counter.

    The hold is a solver-config write, so it moves the shared counter and
    every other study reads stale; the loop's own verdict describes the held
    setting, so its record re-captures the bumped value and reads fresh.

    Takes no lock: the caller holds the solver-state read-modify-write and
    calls this right after the config write, as the margin and coupling
    loops do.
    """
    record["network_revision"] = bump_revision(ctx)
    return record["network_revision"]
