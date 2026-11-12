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
  (`found`) and whether the service truncated it (S6);
- `unavailable`: the upstream refused, failed or took too long, with its
  message; a programming error is not caught here and stops the step for
  the user instead (`run_actions._dispatch`);
- `not_named`: the intake names no site (or names it badly); nothing is
  fetched.

The input gate's `eh.site.context` (warn) reads it (`outcome`): it passes on
`fetched` with nothing truncated, and is not evaluated with its reason
otherwise. The report lists that gap (`gap_line`); never a silent default.

Egress (S4): no setting gates the `fetch_site_context` tool's outbound call
today; the endpoints are the operator's (`PYPSAGUI_OVERPASS_URL`,
`PYPSAGUI_TERRAIN_URL`). The transport seam (`_TRANSPORT`) is a module
attribute only tests set (S5): no route, intake field or profile value
reaches it.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

VERSION = 1  # the frozen record's and the step result's shape
HALF_SIDE_M = 250.0  # the fetched square's half side; the service pads it (`site_context.padded_bbox`)
FETCH_TIMEOUT_S = 40.0  # the step's wall bound; `runner.LEASE_SECONDS` (90 s) is above it (S3)
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


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def freeze(task) -> dict:
    """The frozen record: the site the run's granted intake rows name (or
    none, with the reason a badly named one gives), and its digest."""
    from services import run_intake

    point, gap = run_intake.site(_rows(task))
    frozen = {"kind": _run_actions().SITE_KIND, "version": VERSION, "site": point,
              "invalid": gap["message"] if gap else None, "solves": 0}
    return {**frozen, "digest": _digest(core(frozen))}


def core(frozen) -> dict:
    return {k: frozen.get(k) for k in ("kind", "version", "site", "invalid", "solves")}


def card(frozen) -> dict:
    site = frozen.get("site")
    return {"site": f"{site['lat']:.5f}, {site['lon']:.5f}" if site else "none named",
            "solves": frozen.get("solves")}


def _point_ok(site) -> bool:
    from services import run_profiles

    if not isinstance(site, dict) or set(site) != {"lat", "lon"}:
        return False
    limits = {"lat": run_profiles.SITE_LAT_MAX, "lon": run_profiles.SITE_LON_MAX}
    return all(isinstance(site[k], float) and math.isfinite(site[k]) and abs(site[k]) <= limits[k]
               for k in limits)


def valid(frozen) -> bool:
    return (frozen.get("version") == VERSION and frozen.get("solves") == 0
            and (frozen.get("site") is None or _point_ok(frozen["site"])))


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
    return {"kind": _run_actions().SITE_KIND, "version": VERSION, "state": "unavailable",
            "reason": str(reason)[:240] or "the site context could not be fetched"}


def fetch(path, site) -> dict:
    """The step's result for a named site: `fetched` with the document kept
    beside the task, or `unavailable` with the reason, within
    `FETCH_TIMEOUT_S`."""
    import httpx

    from services import site_context
    from services.atomic_io import atomic_write_text

    shape = boundary(site)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="site-context")
    try:
        future = pool.submit(site_context.fetch_context, shape, transport=_TRANSPORT,
                             budget_s=min(site_context.TOTAL_BUDGET_S, FETCH_TIMEOUT_S))
        try:
            doc = future.result(timeout=FETCH_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            # The fetch's own thread ends on its client's timeouts; its answer is never read.
            return _unavailable(f"the map services did not answer within {FETCH_TIMEOUT_S:.0f} s")
        except (site_context.SiteContextUnavailable, httpx.HTTPError, OSError) as exc:
            return _unavailable(exc)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    text = json.dumps(doc, sort_keys=True)
    try:
        atomic_write_text(context_path(path), text)
    except OSError as exc:
        return _unavailable(f"the fetched context could not be kept ({type(exc).__name__})")
    found = doc.get("found") if isinstance(doc.get("found"), dict) else {
        "buildings": len(doc.get("buildings") or []), "lines": len(doc.get("lines") or [])}
    return {"kind": _run_actions().SITE_KIND, "version": VERSION, "state": "fetched",
            "site": dict(site), "boundary": shape, "truncated": bool(doc.get("truncated")),
            "found": dict(found), "source": doc.get("source"), "fetched_at": doc.get("fetched_at"),
            "file": context_path(path).name, "digest": hashlib.sha256(text.encode()).hexdigest()}


def dispatch(path, step, frozen, attempt, instance) -> str:
    """Run the fetch (or settle `not_named`) and keep the result while this
    attempt still holds the run (C3)."""
    run_actions = _run_actions()
    site = frozen.get("site")
    if site is None:
        result = {"kind": run_actions.SITE_KIND, "version": VERSION, "state": "not_named",
                  "reason": frozen.get("invalid") or NOT_NAMED}
    else:
        result = fetch(path, site)
    return _publish(Path(path), step["id"], attempt, instance, result)


def _publish(path, step_id, attempt, instance, result) -> str:
    from harness import runner

    run_actions = _run_actions()
    wrote = {}

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step_id)
        if not run_actions._held(t, s, attempt, instance):
            return False
        s.update(status="completed", result=result)
        wrote["ok"] = True

    runner._update(path, done)
    if not wrote:
        logger.info("run %s: the site context's result was not kept (run taken over)", path.stem)
        return "taken_over"
    return run_actions.SITE_FETCH


def shape_ok(result) -> bool:
    if not isinstance(result, dict) or result.get("version") != VERSION or result.get("state") not in STATES:
        return False
    if result["state"] == "fetched":
        return isinstance(result.get("truncated"), bool) and isinstance(result.get("found"), dict)
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
        return "truncated", (f"the map service's answer was cut to the nearest features "
                             f"({result['found'].get('buildings', '?')} buildings found)"), result
    return "fetched", None, result


def gap_line(task) -> str | None:
    """The report's gap row for a run that has a fetch step and no whole
    context; None when the context was fetched, or the run has no step."""
    if _run_actions().latest(task or {}, _run_actions().SITE_FETCH) is None:
        return None
    state, reason, _ = outcome(task)
    return None if state == "fetched" else f"Site context not established: {reason}"
