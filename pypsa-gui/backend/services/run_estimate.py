"""
How long a project's next run will take, from its own past runs.

Run progress spec (`.scratch/run-progress/spec.md` §2, slice 1). The estimate
is the median wall time (`finished_at - started_at`) of the project's last
`SAMPLE_SIZE` completed runs of the same kind and the same solver mode. With
fewer than `MIN_SAMPLES` such runs it is unknown, and it says so: `seconds`
is None, never 0. It always carries its basis (`samples`, the run ids), so a
screen says "based on the last n runs of this project" and never a promise.

Only this project's runs count (GK B, slice 1 plan (a)): another project's
model can be any size. Queue waiting time is not part of it.

What makes up "mode" (`mode_key`), per kind:

- `solve`: the solver config's `mode`, `solver_name` and
  `multi_investment_periods`, read through the same parser the queue uses for
  a job's snapshot (`routers.projects._solver_config_from_dict`).
- `campus` and `profile_gen`: the job's whole settings snapshot (canonical
  JSON). Those jobs store their own settings, not a solver config: a campus
  run's island checks and investment switches, or an island-tuning action,
  change its length far more than anything else, so only identical settings
  compare.
- `gridspine`: no snapshot, so every run of the project compares.

Network size and snapshot count are not part of the key. The `solve_jobs` row
does not record them, and a project's size rarely changes between runs; when
it does, the median of five moves within a few runs, and the status line says
"longer than usual" for a run past its estimate.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import datetime, UTC
from typing import Any

logger = logging.getLogger(__name__)

#: How many of the latest eligible runs the median is taken over.
SAMPLE_SIZE = 5
#: Fewer eligible runs than this, and the estimate is unknown.
MIN_SAMPLES = 2

#: The solver-config fields that make two runs comparable.
MODE_FIELDS = ("mode", "solver_name", "multi_investment_periods")

#: Marker for a row whose solver config cannot be read: never equal to any key.
_UNREADABLE = object()


def mode_key(kind: str, solver_config: str | None) -> Any:
    """
    The comparable part of a `kind` job's settings snapshot.

    None (no snapshot) is its own key, so such runs compare with each other.
    A snapshot that does not parse returns `_UNREADABLE`, which matches
    nothing, so the row is left out rather than failing the request.
    """
    if solver_config is None:
        return None
    try:
        data = json.loads(solver_config)
        if not isinstance(data, dict):
            return _UNREADABLE
        if kind != "solve":
            return json.dumps(data, sort_keys=True)
        from routers.projects import _solver_config_from_dict

        config = _solver_config_from_dict(data)
        return tuple(getattr(config, f) for f in MODE_FIELDS)
    except Exception:  # noqa: BLE001 — an unreadable snapshot is excluded, never a 500
        logger.warning("run_estimate: unreadable %s settings snapshot excluded", kind)
        return _UNREADABLE


def _utc(value: Any) -> datetime | None:
    """A timezone-aware datetime in UTC, or None for anything else (naive included)."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        return None
    return value.astimezone(UTC)


def duration_s(row: dict) -> float | None:
    """A completed row's wall time in seconds, or None when it cannot count."""
    if row.get("status") != "completed":
        return None
    started, finished = _utc(row.get("started_at")), _utc(row.get("finished_at"))
    if started is None or finished is None:
        return None
    seconds = (finished - started).total_seconds()
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds


def estimate(rows: list[dict], kind: str, key: Any) -> dict:
    """
    The estimate from `rows` (one project's runs of one kind, any order).

    Keeps the rows that are completed, have a positive UTC duration and match
    `key`; takes the latest `SAMPLE_SIZE` by `finished_at`; returns the median
    of their durations. For an even count that is the mean of the two middle
    values (`statistics.median`).
    """
    eligible = []
    for row in rows if key is not _UNREADABLE else ():
        seconds = duration_s(row)
        row_key = mode_key(kind, row.get("solver_config"))
        if seconds is None or row_key is _UNREADABLE or row_key != key:
            continue
        eligible.append((_utc(row["finished_at"]), seconds, str(row.get("id"))))
    eligible.sort(key=lambda e: e[0], reverse=True)
    used = eligible[:SAMPLE_SIZE]
    if len(used) < MIN_SAMPLES:
        return {"seconds": None, "samples": len(used), "run_ids": [r[2] for r in used]}
    return {
        "seconds": statistics.median(r[1] for r in used),
        "samples": len(used),
        "run_ids": [r[2] for r in used],
    }


def for_job(
    job_id: Any, project_key: str, kind: str, solver_config: str | None
) -> dict:
    """The estimate for job `job_id`: its project's other completed runs of its
    kind under the same mode. The job itself never counts towards its own."""
    from services import solve_job_store

    rows = [
        r
        for r in solve_job_store.load_completed_runs(project_key, kind)
        if str(r.get("id")) != str(job_id)
    ]
    return {"kind": kind, **estimate(rows, kind, mode_key(kind, solver_config))}
