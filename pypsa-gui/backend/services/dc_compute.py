"""DC 05: IT MW derived from the compute load, reported beside the typed IT MW.

Nothing here reaches the LP: the typed ``DcPhase.it_mw`` always wins, and a
derived figure more than ``MISMATCH_NOTE_ABOVE`` away from it is a note, never
an error."""
from __future__ import annotations

from models.dc_compute_spec import ComputeSpec

#: A derived figure further than this share from the typed one gets a note.
MISMATCH_NOTE_ABOVE = 0.10


def derived_it_mw(c: ComputeSpec, k: int) -> float:
    return c.accelerator_counts[k] * c.kw_per_accelerator * c.utilisation / 1000.0


def compute_report(c: ComputeSpec | None, typed_it_mw: list[float | None]) -> list[dict]:
    """Per phase: typed, derived, the figure used (typed) and a mismatch note."""
    if c is None:
        return []
    rows = []
    for k, typed in enumerate(typed_it_mw):
        derived = derived_it_mw(c, k) if k < len(c.accelerator_counts) else None
        warning = None
        if typed and derived is not None and abs(derived - typed) / typed > MISMATCH_NOTE_ABOVE:
            warning = (f"phase {k + 1}: compute gives {derived:g} MW IT, the typed IT is "
                       f"{typed:g} MW (typed IT is used)")
        rows.append({"typed_it_mw": typed, "compute_it_mw": derived,
                     "it_mw_used": typed or None, "warning": warning})
    return rows
