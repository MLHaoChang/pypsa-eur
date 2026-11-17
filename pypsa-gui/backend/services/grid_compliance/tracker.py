"""The deliverable tracker: status with its guards, the data list, and the
data freeze (GC-09).

* ``state(project)``: every deliverable with its status, prerequisites, what
  blocks it, its data list, its freeze and the freeze's state, the packages
  that feed it and whether their output is in, and the next step.
* ``set_status``: the status is **user-declared** (``status_basis``): the
  tracker records what the engineer says, it never infers "results in" or
  "judged" from a package's output, and a pending package never reads as
  judged. Under the tracker's lock, a move refuses
  ``gc_prerequisite_not_judged`` when the deliverable would reach
  ``submitted`` (or later) while a prerequisite is short of ``judged``;
  ``gc_freeze_not_fresh`` when it would reach ``submitted`` without a fresh
  freeze; and ``gc_dependent_submitted`` when a prerequisite would drop below
  ``judged`` while a deliverable that needs it is submitted.
* ``freeze``: hashes the project's inputs (campus input, asset library and the
  network's LP inputs) into a model-set id and stamps it on the deliverable.

A freeze reads one of ``FREEZE_STATES``: ``fresh`` when the inputs still hash
to it; ``stale`` after an edit; ``refreeze_needed`` when it was made by
another freeze method than the current one (interim vs GC-01's model set);
``unreadable`` when the current inputs cannot be hashed. Only ``fresh``
counts as frozen.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, UTC

from services import campus_electrical_service as ce

from . import catalogue as cat
from .sources import package_available
from .store import (
    DataItem,
    DeliverableState,
    Freeze,
    GridComplianceError,
    editing,
    load,
)

#: The campus files a freeze covers, in hash order. GC-01 replaces this
#: interim hash with its model-set freeze over the canonicalised inputs.
FROZEN_FILES = (ce.CAMPUS_FILE, ce.LIBRARY_FILE)
#: The freeze method in force. A freeze made by another reads ``refreeze_needed``.
FREEZE_SOURCE = "interim"
FREEZE_STATES = ("none", "fresh", "stale", "refreeze_needed", "unreadable")
#: How each freeze state reads in user-facing text ("out of date", never "stale").
FREEZE_WORDS = {
    "none": "not frozen",
    "fresh": "up to date",
    "stale": "out of date",
    "refreeze_needed": "made by an older freeze method",
    "unreadable": "not checkable: the inputs cannot be read",
}

NEXT_STEP = {
    "not_started": "Collect the data list",
    "data": "Freeze the data and export the study pack",
    "exported": "Run the study and load the results",
    "results_in": "Judge the results against the grid code",
    "judged": "Submit to the TSO",
    "submitted": "Wait for TSO comments",
    "tso_comments": "Answer the TSO comments",
    "accepted": "Done",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def require_deliverable(code: str) -> cat.Deliverable:
    d = cat.BY_CODE.get(code)
    if d is None:
        raise GridComplianceError(
            "gc_unknown_deliverable", 404, f"There is no TRGC deliverable {code}."
        )
    return d


def network_fingerprint(project) -> str | None:
    """The network's LP-input fingerprint (solver outputs excluded), from the
    resident context when the project is open (it holds unsaved edits), else
    from the network on disk. None when it cannot be read."""
    from services import project_registry, workflow_tools
    from services.provenance_graph.runs import input_fingerprint
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_context(project_registry.registry_key(project))
    if ctx is None:
        try:
            ctx = workflow_tools._load_context(project)
        except Exception:  # noqa: BLE001 — an unreadable network is no evidence
            return None
    return input_fingerprint(ctx)


def data_hash(project) -> str | None:
    """sha256 over the frozen campus files (name, NUL, bytes each) and the
    network fingerprint. None when the campus input is not written yet or the
    network cannot be read: an absent digest never matches a freeze."""
    folder = ce.campus_dir(project)
    if not (folder / ce.CAMPUS_FILE).is_file():
        return None
    network = network_fingerprint(project)
    if network is None:
        return None
    h = hashlib.sha256()
    for name in FROZEN_FILES:
        path = folder / name
        if path.is_file():
            h.update(name.encode() + b"\0" + path.read_bytes())
    h.update(b"network\0" + network.encode())
    return h.hexdigest()


def freeze_state(freeze: Freeze | None, current: str | None) -> str:
    if freeze is None:
        return "none"
    if freeze.source != FREEZE_SOURCE:
        return "refreeze_needed"
    if current is None:
        return "unreadable"
    return "fresh" if freeze.data_hash == current else "stale"


def default_items(d: cat.Deliverable) -> list[DataItem]:
    return [
        DataItem(item_id=f"item-{i + 1}", label=label)
        for i, label in enumerate(d.data_items)
    ]


def _rank(statuses: dict[str, str], code: str) -> int:
    return cat.RANK[statuses.get(code, "not_started")]


def _blockers(code: str, statuses: dict[str, str]) -> list[str]:
    floor = cat.RANK[cat.PREREQUISITE_MIN]
    return [
        p for p in require_deliverable(code).depends_on if _rank(statuses, p) < floor
    ]


def _statuses(tracker) -> dict[str, str]:
    return {code: s.status for code, s in tracker.deliverables.items()}


def _view(project, tracker, codes) -> list[dict]:
    statuses = _statuses(tracker)
    current = data_hash(project)
    wanted = [cat.BY_CODE[c] for c in codes]
    available = {p: package_available(project, p) for d in wanted for p in d.packages}
    rows = []
    for d in wanted:
        s = tracker.deliverables.get(d.code) or DeliverableState()
        fstate = freeze_state(s.freeze, current)
        rows.append(
            {
                "code": d.code,
                "title": d.title,
                "phase": d.phase,
                "tool": d.tool,
                "depends_on": list(d.depends_on),
                "blocked_by": _blockers(d.code, statuses),
                "status": s.status,
                "status_basis": "declared",
                "status_at": s.status_at,
                "next_step": NEXT_STEP[s.status],
                "data": [
                    i.model_dump()
                    for i in (s.data if s.data is not None else default_items(d))
                ],
                "freeze": s.freeze.model_dump() if s.freeze else None,
                "freeze_state": fstate,
                "stale": fstate == "stale",
                "feeds": [
                    {
                        "package": p,
                        "label": cat.PACKAGE_LABELS[p],
                        "available": available[p],
                    }
                    for p in d.packages
                ],
                "comments": [c.model_dump() for c in s.comments],
                "packs": [p.model_dump() for p in s.packs],
            }
        )
    return rows


def state(project) -> dict:
    return {
        "statuses": list(cat.STATUSES),
        "phases": {str(k): v for k, v in cat.PHASES.items()},
        "has_campus_input": (ce.campus_dir(project) / ce.CAMPUS_FILE).is_file(),
        "deliverables": _view(project, load(project), list(cat.BY_CODE)),
    }


def check_move(code: str, status: str, statuses: dict[str, str], fstate: str) -> None:
    """Refuse a move the guards forbid. ``statuses`` and ``fstate`` are read
    under the tracker's lock, in the same critical section as the write."""
    rank = cat.RANK[status]
    if rank >= cat.RANK[cat.SUBMITTED]:
        blockers = _blockers(code, statuses)
        if blockers:
            raise GridComplianceError(
                "gc_prerequisite_not_judged",
                409,
                f"TRGC {code} cannot be {cat.STATUS_WORDS[status]} while "
                + ", ".join(f"TRGC {b}" for b in blockers)
                + " is not judged yet.",
                blockers=blockers,
            )
        if fstate != "fresh":
            raise GridComplianceError(
                "gc_freeze_not_fresh",
                409,
                f"TRGC {code} cannot be {cat.STATUS_WORDS[status]} on data that is not frozen "
                f"(freeze: {FREEZE_WORDS[fstate]}). Freeze the data first.",
            )
    if rank < cat.RANK[cat.PREREQUISITE_MIN]:
        submitted = [
            d
            for d in cat.dependents(code)
            if _rank(statuses, d) >= cat.RANK[cat.SUBMITTED]
        ]
        if submitted:
            raise GridComplianceError(
                "gc_dependent_submitted",
                409,
                f"TRGC {code} cannot go back to {cat.STATUS_WORDS[status]}: "
                + ", ".join(f"TRGC {d}" for d in submitted)
                + " was submitted on its judged result.",
                blockers=submitted,
            )


def set_status(project, code: str, status: str) -> dict:
    require_deliverable(code)
    if status not in cat.RANK:
        raise GridComplianceError(
            "gc_unknown_status", 422, f"“{status}” is not a deliverable status."
        )
    # Hashing the network is slow, so it is done before the lock; the freeze
    # it is compared with, and every status, are read under the lock.
    current = (
        data_hash(project) if cat.RANK[status] >= cat.RANK[cat.SUBMITTED] else None
    )
    with editing(project) as tracker:
        entry = tracker.deliverables.setdefault(code, DeliverableState())
        check_move(
            code, status, _statuses(tracker), freeze_state(entry.freeze, current)
        )
        entry.status = status
        entry.status_at = now()
    return _view(project, tracker, [code])[0]


def set_data(project, code: str, items: list[dict]) -> dict:
    require_deliverable(code)
    parsed = [DataItem.model_validate(i) for i in items]
    ids = [i.item_id for i in parsed]
    if len(set(ids)) != len(ids):
        raise GridComplianceError(
            "gc_duplicate_item", 422, "Two data-list items share an id."
        )
    with editing(project) as tracker:
        tracker.deliverables.setdefault(code, DeliverableState()).data = parsed
    return _view(project, tracker, [code])[0]


def freeze(project, code: str) -> dict:
    require_deliverable(code)
    if not (ce.campus_dir(project) / ce.CAMPUS_FILE).is_file():
        raise GridComplianceError(
            "gc_no_campus_input",
            409,
            "There is no campus input to freeze yet. Save the campus file first.",
        )
    digest = data_hash(project)
    if digest is None:
        raise GridComplianceError(
            "gc_inputs_unreadable",
            409,
            "The project's network cannot be read, so its inputs cannot be frozen.",
        )
    with editing(project) as tracker:
        tracker.deliverables.setdefault(code, DeliverableState()).freeze = Freeze(
            model_set_id=f"ms-{digest[:12]}",
            data_hash=digest,
            frozen_at=now(),
            source=FREEZE_SOURCE,
        )
    return _view(project, tracker, [code])[0]
