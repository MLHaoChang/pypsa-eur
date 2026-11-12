"""Study harness issue 33: the inputs stage's site-context fetch (eh_site@3).

The intake may name the site by a point (`run_intake.site`: lat/lon in
degrees, never a creation param). At inputs-stage entry the point is frozen
from the run's granted intake rows (`freeze`, GK B (49) S1), never from a
model's args or turn. The runner's native action `site_context_fetch`
(`run_actions.REGISTRY`) then fetches the map context of a square of
±`HALF_SIDE_M` around it with the service the `fetch_site_context` tool calls
(`services.site_context.fetch_context`), and keeps it in the run's own record:
the step's result and a file beside the task (`<task>.site_context`). It
writes nothing to the project and reserves no solve (Q2).

The step always settles, inside `FETCH_TIMEOUT_S` (below the run lease,
`runner.LEASE_SECONDS`; S3):
- `fetched`: the document, with what the response held before any cap
  (`found`), whether the service truncated it (S6) and how many terrain
  tiles it lacks;
- `unavailable`: the upstream refused, failed, answered something it cannot
  read, or took too long, with its message; a programming error is not
  caught here and stops the step for the user instead
  (`run_actions._dispatch`);
- `not_named`: the intake names no site (or names it badly); nothing is
  fetched.

The input gate's `eh.site.context` (warn) reads it (`outcome`): it passes on
`fetched` with nothing truncated and every terrain tile in, and is not
evaluated with its reason otherwise. The report lists that gap
(`gap_line`); never a silent default.

The fetch runs on a daemon thread the step waits on for at most
`FETCH_TIMEOUT_S`. A fetch past it is abandoned: its answer is never read or
kept, and the thread ends with its client's timeouts (an upstream that
trickles bytes can hold it up to the service's 32 MB cap), never holding
the run or the process's exit.

Egress (S4): no setting gates the `fetch_site_context` tool's outbound call
today; the endpoints are the operator's (`PYPSAGUI_OVERPASS_URL`,
`PYPSAGUI_TERRAIN_URL`). The transport seam (`_TRANSPORT`) is a module
attribute only tests set (S5): no route, intake field or profile value
reaches it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

VERSION = 1  # the frozen record's and the step result's shape
HALF_SIDE_M = 250.0  # the fetched square's half side; the service pads it (`site_context.padded_bbox`)
FETCH_TIMEOUT_S = (
    40.0  # the step's wall bound; `runner.LEASE_SECONDS` (90 s) is above it (S3)
)
NOT_NAMED = "no site named in the intake"
STATES = ("fetched", "unavailable", "not_named")
SUFFIX = ".site_context"  # never `.json`: the task folder's globs read every `*.json` as a task
_M_PER_DEG = 111_320.0
_TRANSPORT = None  # S5: test-only (an `httpx.MockTransport`); nothing else sets it


def _run_actions():
    from services import run_actions

    return run_actions


def _rows(task) -> list:
    intake = (task.get("run") or {}).get("intake")
    if isinstance(intake, dict):
        intake = intake.get("rows")
    return intake if isinstance(intake, list) else []


def freeze(task) -> dict:
    """The frozen record: the site the run's granted intake rows name (or
    none, with the reason a badly named one gives), and its digest."""
    from services import run_intake

    point, gap = run_intake.site(_rows(task))
    frozen = {
        "kind": _run_actions().SITE_KIND,
        "version": VERSION,
        "site": point,
        "invalid": gap["message"] if gap else None,
        "solves": 0,
    }
    return {**frozen, "digest": _run_actions().digest(core(frozen))}


def core(frozen) -> dict:
    return {k: frozen.get(k) for k in ("kind", "version", "site", "invalid", "solves")}


def card(frozen) -> dict:
    site = frozen.get("site")
    return {
        "site": f"{site['lat']:.5f}, {site['lon']:.5f}" if site else "none named",
        "solves": frozen.get("solves"),
    }


def valid(frozen) -> bool:
    from services import run_intake

    return (
        frozen.get("version") == VERSION
        and frozen.get("solves") == 0
        and (frozen.get("site") is None or run_intake.site_point_ok(frozen["site"]))
    )


def boundary(site) -> list[list[float]]:
    """The square of ±`HALF_SIDE_M` around the point, as [lng, lat] corners."""
    dlat = HALF_SIDE_M / _M_PER_DEG
    dlon = HALF_SIDE_M / (_M_PER_DEG * max(math.cos(math.radians(site["lat"])), 1e-6))
    lat0, lat1 = site["lat"] - dlat, site["lat"] + dlat
    lon0, lon1 = max(site["lon"] - dlon, -180.0), min(site["lon"] + dlon, 180.0)
    return [[lon0, lat1], [lon1, lat1], [lon1, lat0], [lon0, lat0]]


def context_path(path) -> Path:
    path = Path(path)
    return path.with_name(path.name.removesuffix(".json") + SUFFIX)


def _unavailable(reason) -> dict:
    return {
        "kind": _run_actions().SITE_KIND,
        "version": VERSION,
        "state": "unavailable",
        "reason": str(reason)[:240] or "the site context could not be fetched",
    }


def _bounded(call):
    """`call()` on a daemon thread, waited on for `FETCH_TIMEOUT_S`:
    `(True, value)`, `(False, exception)`, or None past the bound."""
    out = {}

    def run():
        try:
            out["value"] = (True, call())
        except BaseException as exc:  # noqa: BLE001 — handed to the waiting step, which decides
            out["value"] = (False, exc)

    worker = threading.Thread(target=run, name="site-context", daemon=True)
    worker.start()
    worker.join(FETCH_TIMEOUT_S)
    return out.get("value")


def fetch(path, site, attempt) -> dict:
    """The step's result for a named site: `fetched` with the document
    written beside the task under this attempt's name (`_publish` moves it
    in while the attempt still holds the run), or `unavailable` with the
    reason, within `FETCH_TIMEOUT_S`."""
    import httpx

    from services import site_context
    from services.atomic_io import atomic_write_text

    shape = boundary(site)
    done = _bounded(
        lambda: site_context.fetch_context(
            shape,
            transport=_TRANSPORT,
            budget_s=min(site_context.TOTAL_BUDGET_S, FETCH_TIMEOUT_S),
        )
    )
    if done is None:
        return _unavailable(
            f"the map services did not answer within {FETCH_TIMEOUT_S:.0f} s"
        )
    ok, doc = done
    if not ok:
        if isinstance(
            doc, (site_context.SiteContextUnavailable, httpx.HTTPError, OSError)
        ):
            return _unavailable(doc)
        raise doc  # a programming error stops the step for the user (S3)
    text = json.dumps(doc, sort_keys=True)
    try:
        atomic_write_text(_pending_path(path, attempt), text)
    except OSError as exc:
        return _unavailable(
            f"the fetched context could not be kept ({type(exc).__name__})"
        )
    terrain = doc.get("terrain") if isinstance(doc.get("terrain"), dict) else None
    return {
        "kind": _run_actions().SITE_KIND,
        "version": VERSION,
        "state": "fetched",
        "site": dict(site),
        "boundary": shape,
        "truncated": bool(doc.get("truncated")),
        "found": dict(doc["found"]),
        "terrain_missing": None
        if terrain is None
        else int(terrain.get("missing_tiles") or 0),
        "source": doc.get("source"),
        "fetched_at": doc.get("fetched_at"),
        "file": context_path(path).name,
        "digest": hashlib.sha256(text.encode()).hexdigest(),
    }


def _pending_path(path, attempt) -> Path:
    target = context_path(path)
    return target.with_name(f"{target.name}.{attempt}.tmp")


def dispatch(path, step, frozen, attempt, instance) -> str:
    """Run the fetch (or settle `not_named`) and keep the result while this
    attempt still holds the run (C3)."""
    run_actions = _run_actions()
    site = frozen.get("site")
    if site is None:
        result = {
            "kind": run_actions.SITE_KIND,
            "version": VERSION,
            "state": "not_named",
            "reason": frozen.get("invalid") or NOT_NAMED,
        }
    else:
        result = fetch(path, site, attempt)
    return _publish(Path(path), step["id"], attempt, instance, result)


def _publish(path, step_id, attempt, instance, result) -> str:
    """Keep the result, and move this attempt's document in, only while the
    attempt still holds the run; a run taken over keeps neither."""
    from harness import runner

    run_actions = _run_actions()
    pending = _pending_path(path, attempt)
    wrote = {}

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step_id)
        if not run_actions._held(t, s, attempt, instance):
            return False
        if result["state"] == "fetched":
            pending.replace(context_path(path))
        s.update(status="completed", result=result)
        wrote["ok"] = True

    try:
        runner._update(path, done)
    finally:
        pending.unlink(missing_ok=True)
    if not wrote:
        logger.info(
            "run %s: the site context's result was not kept (run taken over)", path.stem
        )
        return "taken_over"
    return run_actions.SITE_FETCH


def shape_ok(result) -> bool:
    if (
        not isinstance(result, dict)
        or result.get("version") != VERSION
        or result.get("state") not in STATES
    ):
        return False
    if result["state"] == "fetched":
        return (
            isinstance(result.get("truncated"), bool)
            and isinstance(result.get("found"), dict)
            and (
                result.get("terrain_missing") is None
                or isinstance(result["terrain_missing"], int)
            )
        )
    return isinstance(result.get("reason"), str)


def outcome(task) -> tuple[str, str | None, dict | None]:
    """`(state, reason, result)`: `fetched` (reason None) only for a whole
    fetch; else the state that is no evidence, with its reason. A run with
    no fetch step (not reached, or a record that predates it) reads
    `not_run` (S6)."""
    step = _run_actions().latest(task, _run_actions().SITE_FETCH)
    if step is None or step.get("status") != "completed":
        return "not_run", "the run has not fetched the site context", None
    result = step.get("result")
    if not shape_ok(result):
        return "unreadable", "the run's site-context record cannot be read", None
    if result["state"] != "fetched":
        return result["state"], result["reason"], result
    if result["truncated"]:
        return (
            "truncated",
            (
                "the map service's answer was cut to the nearest features "
                f"({result['found'].get('buildings', '?')} buildings found)"
            ),
            result,
        )
    if result.get("terrain_missing") is None:
        return "no_terrain", "the terrain around the site could not be fetched", result
    if result["terrain_missing"]:
        return (
            "partial_terrain",
            f"{result['terrain_missing']} terrain tile(s) around the site could not be fetched",
            result,
        )
    return "fetched", None, result


def gap_line(task) -> str | None:
    """The report's gap row for a run that has a fetch step and no whole
    context; None when the context was fetched, or the run has no step."""
    if _run_actions().latest(task or {}, _run_actions().SITE_FETCH) is None:
        return None
    state, reason, _ = outcome(task)
    return None if state == "fetched" else f"Site context not established: {reason}"
