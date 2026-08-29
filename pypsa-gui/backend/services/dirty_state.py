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
