"""The deliverable tracker: status with its dependency guard, the data list,
and the data freeze (GC-09).

* ``state(project)``: every deliverable with its status, prerequisites, what
  blocks it, its data list, its freeze and whether that freeze is stale, the
  packages that feed it and whether their output is in, and the next step.
* ``set_status``: refuses ``gc_prerequisite_not_judged`` when a deliverable
  would reach ``submitted`` (or later) while a prerequisite is short of
  ``judged``, and ``gc_dependent_submitted`` when a prerequisite would drop
  below ``judged`` while a deliverable that needs it is submitted.
* ``freeze``: hashes the campus input into a model-set id and stamps it on the
  deliverable. Editing the input afterwards makes ``stale`` true.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from services import campus_electrical_service as ce

from . import catalogue as cat
from .sources import package_available
from .store import DataItem, DeliverableState, Freeze, GridComplianceError, editing, load

#: The campus files a freeze covers, in hash order. GC-01 replaces this
#: interim hash with its model-set freeze over the canonicalised inputs.
FROZEN_FILES = (ce.CAMPUS_FILE, ce.LIBRARY_FILE)

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_deliverable(code: str) -> cat.Deliverable:
    d = cat.BY_CODE.get(code)
    if d is None:
        raise GridComplianceError("gc_unknown_deliverable", 404,
                                  f"There is no TRGC deliverable {code!r}.")
    return d


def data_hash(project) -> str | None:
    """sha256 over the frozen campus files (name, NUL, bytes each), or None
    when the campus input has not been written yet."""
    folder = ce.campus_dir(project)
    if not (folder / ce.CAMPUS_FILE).is_file():
        return None
    h = hashlib.sha256()
    for name in FROZEN_FILES:
        path = folder / name
        if path.is_file():
            h.update(name.encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def _default_items(d: cat.Deliverable) -> list[DataItem]:
    return [DataItem(item_id=f"item-{i + 1}", label=label)
            for i, label in enumerate(d.data_items)]


def _blockers(code: str, statuses: dict[str, str]) -> list[str]:
    floor = cat.RANK[cat.PREREQUISITE_MIN]
    return [p for p in require_deliverable(code).depends_on
            if cat.RANK[statuses.get(p, "not_started")] < floor]


def _statuses(tracker) -> dict[str, str]:
    return {code: s.status for code, s in tracker.deliverables.items()}


def state(project) -> dict:
    tracker = load(project)
    statuses = _statuses(tracker)
    current = data_hash(project)
    available = {p: package_available(project, p)
                 for d in cat.DELIVERABLES for p in d.packages}
    rows = []
    for d in cat.DELIVERABLES:
        s = tracker.deliverables.get(d.code)
        status = s.status if s else "not_started"
        freeze = s.freeze if s else None
        blocked_by = _blockers(d.code, statuses)
        rows.append({
            "code": d.code,
            "title": d.title,
            "phase": d.phase,
            "tool": d.tool,
            "depends_on": list(d.depends_on),
            "blocked_by": blocked_by,
            "status": status,
            "status_at": s.status_at if s else None,
            "next_step": NEXT_STEP[status],
            "data": [i.model_dump() for i in
                     (s.data if s and s.data is not None else _default_items(d))],
            "freeze": freeze.model_dump() if freeze else None,
            "stale": bool(freeze and freeze.data_hash != current),
            "feeds": [{"package": p, "available": available[p]}
                      for p in d.packages],
            "comments": [c.model_dump() for c in s.comments] if s else [],
            "packs": [p.model_dump() for p in s.packs] if s else [],
        })
    return {
        "statuses": list(cat.STATUSES),
        "phases": {str(k): v for k, v in cat.PHASES.items()},
        "has_campus_input": current is not None,
        "deliverables": rows,
    }


def set_status(project, code: str, status: str) -> dict:
    require_deliverable(code)
    if status not in cat.RANK:
        raise GridComplianceError("gc_unknown_status", 422,
                                  f"{status!r} is not a deliverable status.")
    with editing(project) as tracker:
        statuses = _statuses(tracker)
        if cat.RANK[status] >= cat.RANK[cat.SUBMITTED]:
            blockers = _blockers(code, statuses)
            if blockers:
                raise GridComplianceError(
                    "gc_prerequisite_not_judged", 409,
                    f"TRGC {code} cannot be {status} while "
                    + ", ".join(f"TRGC {b}" for b in blockers)
                    + " is not judged yet.", blockers=blockers)
        if cat.RANK[status] < cat.RANK[cat.PREREQUISITE_MIN]:
            submitted = [d for d in cat.dependents(code)
                         if cat.RANK[statuses.get(d, "not_started")] >= cat.RANK[cat.SUBMITTED]]
            if submitted:
                raise GridComplianceError(
                    "gc_dependent_submitted", 409,
                    f"TRGC {code} cannot go back to {status}: "
                    + ", ".join(f"TRGC {d}" for d in submitted)
                    + " was submitted on its judged result.", blockers=submitted)
        entry = tracker.deliverables.setdefault(code, DeliverableState())
        entry.status = status
        entry.status_at = _now()
    return _row(project, code)


def set_data(project, code: str, items: list[dict]) -> dict:
    require_deliverable(code)
    parsed = [DataItem.model_validate(i) for i in items]
    ids = [i.item_id for i in parsed]
    if len(set(ids)) != len(ids):
        raise GridComplianceError("gc_duplicate_item", 422,
                                  "Two data-list items share an id.")
    with editing(project) as tracker:
        tracker.deliverables.setdefault(code, DeliverableState()).data = parsed
    return _row(project, code)


def freeze(project, code: str) -> dict:
    require_deliverable(code)
    digest = data_hash(project)
    if digest is None:
        raise GridComplianceError(
            "gc_no_campus_input", 409,
            "There is no campus input to freeze yet. Save the campus file first.")
    with editing(project) as tracker:
        tracker.deliverables.setdefault(code, DeliverableState()).freeze = Freeze(
            model_set_id=f"ms-{digest[:12]}", data_hash=digest,
            frozen_at=_now(), source="interim")
    return _row(project, code)


def _row(project, code: str) -> dict:
    return next(r for r in state(project)["deliverables"] if r["code"] == code)
