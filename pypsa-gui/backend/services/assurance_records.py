"""Owned, bounded assurance records; current-input evidence is not a run store."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from numbers import Real
import os
import time
import uuid

from fastapi import HTTPException

import app_paths
from harness.durable import network_guard, transaction
from services import project_registry
from services.pypsa_service import PyPSAService

logger = logging.getLogger(__name__)

MAX_RECORDS = 512
RETENTION_SECONDS = 24 * 60 * 60
# Deliberately narrow: these are static configured inputs, not solved outputs.
INPUT_METRICS = {
    "Load": {"p_set": "MW", "q_set": "MVar"},
    "Generator": {"p_nom": "MW"},
    "Line": {"s_nom": "MVA"},
    "Transformer": {"s_nom": "MVA"},
    "StorageUnit": {"p_nom": "MW"},
    "Store": {"e_nom": "MWh"},
}

# get_timeseries (component accessor, attribute) -> (component class, unit).
SERIES_METRICS = {
    ("loads", "p_set"): ("Load", "MW"),
    ("generators", "p_max_pu"): ("Generator", "p.u."),
}
# Larger series are refused before the handler. A step's fact carries the
# name twice, so the facts' encoded size is bounded too, under save()'s
# 128 KiB record cap with room for the rest of the record.
MAX_SERIES_POINTS = 744
MAX_SERIES_FACT_BYTES = 120 * 1024

# The checked-answer policy and the current-input adapters this server applies.
# A record written under another one is not re-read as checked (A1.4).
POLICY_VERSION = "1"
INPUT_ADAPTERS = {
    "static": "static-input/1",
    "static_page": "static-input-page/1",
    "series": "series-input/1",
    "run": "run-metrics/1",
}


def digest(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def unavailable(kind):
    raise HTTPException(
        409, {"error_kind": kind, "message": "Checked evidence is unavailable."}
    )


def actor_id():
    from services import chat_tools

    with chat_tools._acting() as (_, user):
        return str(user.id)


def _user(db, owner):
    from db.models import User

    user = db.get(User, uuid.UUID(owner))
    if user is None or user.status != "active":
        raise HTTPException(401, "Authentication required")
    return user


def authorize(owner, project_id=None):
    from db.session import SessionLocal

    with SessionLocal() as db:
        user = _user(db, owner)
        if project_id is not None:
            project_registry.resolve_project(db, user, project_id)


def _path(owner, record_id):
    try:
        if str(uuid.UUID(record_id)) != record_id:
            raise ValueError()
        if str(uuid.UUID(owner)) != owner:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, "Record not found") from None
    from pathlib import Path

    # The containment is the barrier CodeQL reads (py/path-injection), spelled
    # as in site_service: realpath, then a prefix check against the root.
    root = os.path.realpath(app_paths.app_data_dir() / "assistant_assurance")
    full = os.path.realpath(os.path.join(root, owner, record_id + ".json"))
    if not full.startswith(root + os.sep):
        raise HTTPException(404, "Record not found")
    return Path(full)


def save(record, record_id):
    """Commit record and checksum together before anything public is emitted."""
    path = _path(record["owner_id"], record_id)
    envelope = {"record": record, "sha256": digest(record)}
    encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > 128 * 1024:
        unavailable("assurance_record_too_large")
    with transaction(path.parent):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists() and record["record_kind"] not in {
            "operation",
            "ui_action",
            "conversation_context",
        }:
            if path.read_bytes() != encoded:
                unavailable("evidence_unavailable")
            return
        # Expired records cannot be replayed; reclaim them before applying the
        # quota so a long-running pilot does not permanently exhaust storage.
        now = time.time()
        for candidate in path.parent.glob("*.json"):
            if candidate.stat().st_mtime + RETENTION_SECONDS <= now:
                try:
                    pending = json.loads(candidate.read_text(encoding="utf-8"))[
                        "record"
                    ]
                    if pending.get("record_kind") == "operation" and pending.get(
                        "outcome"
                    ) in {"reserved", "running", "uncertain"}:
                        continue  # Never erase the reservation for a possible late effect.
                except (ValueError, KeyError, TypeError, OSError):
                    # A corrupt reservation cannot safely be treated as unused.
                    continue
                candidate.unlink(missing_ok=True)
        if not path.exists() and len(list(path.parent.glob("*.json"))) >= MAX_RECORDS:
            unavailable("assurance_storage_full")
        temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("xb") as stream:
                os.chmod(temp, 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            # The rename must survive restart as well as the file contents.
            if os.name != "nt":
                fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            temp.unlink(missing_ok=True)


def discard(owner, record_id):
    """Remove a record just saved by a commit whose access was revoked meanwhile.

    A missing file is fine. A file that cannot be removed is logged by id and
    left: load() re-checks project access, so it still reads as unknown.
    """
    path = _path(owner, record_id)
    try:
        with transaction(path.parent):
            path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Revoked record %s could not be removed", record_id)


def load(owner, record_id, kind):
    path = _path(owner, record_id)
    authorize(owner)
    try:
        if path.stat().st_size > 128 * 1024:
            raise ValueError()
        envelope = json.loads(path.read_text(encoding="utf-8"))
        record = envelope["record"]
        if digest(record) != envelope["sha256"]:
            raise ValueError()
        if record["owner_id"] != owner or record["record_kind"] != kind:
            raise HTTPException(404, "Record not found")
        if record["expires_at"] <= time.time():
            raise ValueError()
    except FileNotFoundError:
        raise HTTPException(404, "Record not found") from None
    except (OSError, ValueError, KeyError, TypeError):
        unavailable(
            "answer_evidence_unavailable"
            if kind == "answer"
            else "evidence_unavailable"
        )
    try:
        authorize(owner, record.get("project_id"))
    except HTTPException as refused:
        # Revoked project access reads exactly like a record that never existed.
        if refused.status_code == 404:
            raise HTTPException(404, "Record not found") from None
        raise
    return record


def _finite(raw):
    """The float of a finite real input, else None; bools and strings are not numbers."""
    if isinstance(raw, bool) or not isinstance(raw, Real):
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _raw_facts(ctx, component_class, name):
    """Read the original values under the network lock, before UI sanitization."""
    component = ctx.network.components[component_class]
    if name not in component.static.index:
        unavailable("evidence_unavailable")
    facts = []
    for metric, unit in INPUT_METRICS[component_class].items():
        # Missing data does not become zero or a fact.
        value = _finite(component.static.at[name, metric])
        if value is not None:
            facts.append(
                {
                    "path": "/" + metric,
                    "entity_id": name,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                }
            )
    return facts


def capture_input(owner, args, result):
    ctx = PyPSAService.get_active_context()
    component_class, name = args.get("component_class"), args.get("name")
    if (
        component_class not in INPUT_METRICS
        or not isinstance(result, dict)
        or not ctx.project_uuid
    ):
        return None
    authorize(owner, ctx.project_uuid)
    with network_guard():
        if ctx.solver_state.get("status") == "running":
            return None
        facts = _raw_facts(ctx, component_class, name)
        # A raced or incorrect handler response cannot certify itself.
        facts = [
            f
            for f in facts
            if type(result.get(f["metric"])) in (int, float)
            and result[f["metric"]] == f["value"]
        ]
        if not facts:
            return None
        record = {
            "version": 1,
            "record_kind": "evidence",
            "evidence_id": str(uuid.uuid4()),
            "owner_id": owner,
            "project_id": ctx.project_uuid,
            "org_id": ctx.org_id,
            "source_kind": "configured_input",
            "adapter_version": INPUT_ADAPTERS["static"],
            "scope": {
                "component_class": component_class,
                "entity_id": name,
                "kind": "static",
            },
            "facts": facts,
            "source_sha256": digest(_raw_facts(ctx, component_class, name)),
            "validity": {
                "outcome": "completed",
                "as_of": time.time(),
                "coverage": "static_input",
                "warnings": [
                    "Static configured input; time-series overrides are not checked."
                ],
            },
            "expires_at": time.time() + RETENTION_SECONDS,
        }
        save(record, record["evidence_id"])
    return record


# The handler packs a page under its own MAX_PAGE_CHARS, so a page is a few
# dozen rows; save() caps every record at 128 KiB and refuses a larger one.
def pointer(*tokens):
    """RFC 6901 JSON pointer; '~' and '/' in names cannot alias another path."""
    return "".join(
        "/" + str(t).replace("~", "~0").replace("/", "~1") for t in tokens
    )


def page_source_digest(ctx, component_class):
    """What a page asserts: the project, the class's rows and its INPUT_METRICS.

    Call under network_guard. Milliseconds, unlike the whole-network input
    fingerprint, so a checked page does not hold the network lock for seconds.
    """
    static = ctx.network.components[component_class].static
    metrics = [m for m in INPUT_METRICS[component_class] if m in static.columns]
    return digest(
        {
            "project_id": ctx.project_uuid,
            "component_class": component_class,
            # repr keeps NaN, inf and float/int distinctions JSON would lose.
            "rows": [
                [str(name), *(repr(static.at[name, m]) for m in metrics)]
                for name in static.index
            ],
        }
    )


def active_page_source_digest(component_class):
    """Taken before list_components runs; capture keeps evidence only if unchanged."""
    ctx = PyPSAService.get_active_context()
    with network_guard():
        return page_source_digest(ctx, component_class)


def _certified(shown, raw):
    """Exact numeric equality of a shown value with a finite raw input.

    JSON has one number type, so an int shown for a float input of the same
    value certifies; the stored fact is always the raw float. Bools, None,
    strings and non-finite values never certify.
    """
    value = _finite(raw)
    return (
        type(shown) in (int, float)
        and value is not None
        and math.isfinite(shown)
        and shown == value
    )


def capture_input_page(owner, args, result, digest_before):
    """Evidence for exactly the rows one list_components page showed.

    Rows are identified by name, never by position. The class source digest
    taken before the handler must equal the one read here, so the handler's
    rows and the raw values come from one input state.
    """
    ctx = PyPSAService.get_active_context()
    component_class = args.get("component_class")
    if (
        component_class not in INPUT_METRICS
        or digest_before is None
        or not isinstance(result, dict)
        or not ctx.project_uuid
    ):
        return None
    items, total = result.get("items"), result.get("total_count")
    if not isinstance(items, list) or not items or type(total) is not int:
        return None
    names = [row.get("name") if isinstance(row, dict) else None for row in items]
    if any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
        return None
    authorize(owner, ctx.project_uuid)
    with network_guard():
        if ctx.solver_state.get("status") == "running":
            return None
        if page_source_digest(ctx, component_class) != digest_before:
            return None
        static = ctx.network.components[component_class].static
        if any(n not in static.index for n in names):
            return None
        facts = []
        for row, name in zip(items, names):
            for metric, unit in INPUT_METRICS[component_class].items():
                if metric not in static.columns:
                    continue
                raw = static.at[name, metric]
                if _certified(row.get(metric), raw):
                    facts.append(
                        {
                            "path": pointer(name, metric),
                            "entity_id": name,
                            "metric": metric,
                            "value": float(raw),
                            "unit": unit,
                        }
                    )
        if not facts:
            return None
        offset = args.get("offset", 0)
        if result.get("has_more") is not (offset + len(items) < total):
            return None
        record = {
            "version": 1,
            "record_kind": "evidence",
            "evidence_id": str(uuid.uuid4()),
            "owner_id": owner,
            "project_id": ctx.project_uuid,
            "org_id": ctx.org_id,
            "source_kind": "configured_input",
            "adapter_version": INPUT_ADAPTERS["static_page"],
            "scope": {
                "component_class": component_class,
                "kind": "static_page",
                "offset": offset,
                "returned": len(items),
                "total_count": total,
                "has_more": result["has_more"],
            },
            "facts": facts,
            "source_sha256": digest_before,
            "validity": {
                "outcome": "completed",
                "as_of": time.time(),
                "coverage": "page",
                "warnings": [
                    "One page of configured inputs, not the whole class.",
                    "Static configured input; time-series overrides are not checked.",
                ],
            },
            "expires_at": time.time() + RETENTION_SECONDS,
        }
        save(record, record["evidence_id"])
    return record


def _series_source(ctx, args):
    """(digest, series) of the one series get_timeseries reads for `args`.

    Call under network_guard. The digest covers the project, the exact args,
    the source (user upload or network), and the raw bytes of the index levels
    and values, so an edit anywhere in the series moves it. repr is never used:
    pandas and numpy summarize long arrays.
    """
    import pandas as pd

    from routers.network_time_axis import resolve_series

    source, series = resolve_series(
        ctx.network, args["component"], args["attribute"], args["name"]
    )
    hashed = hashlib.sha256(
        json.dumps(
            {"project_id": ctx.project_uuid, "args": args, "source": source},
            sort_keys=True,
        ).encode()
    )
    if series is not None:
        hashed.update(f"values:{series.dtype}:{len(series)}".encode())
        for level in range(series.index.nlevels):
            values = series.index.get_level_values(level)
            if isinstance(values, pd.DatetimeIndex):
                # asi8 is in the index's own unit; pin it to ns.
                hashed.update(f"datetime[ns]:{values.tz}".encode())
                hashed.update(values.as_unit("ns").asi8.tobytes())
            else:
                hashed.update(json.dumps([str(v) for v in values]).encode())
        hashed.update(series.to_numpy(dtype="float64").tobytes())
    return hashed.hexdigest(), series


def _series_steps(args, series):
    """Every step of `series` as the fact it would certify, by row.

    Values are taken after the JSON round-trip a shown result goes through.
    """
    from routers.network_time_axis import index_labels

    labels, periods = index_labels(series.index)
    unit = SERIES_METRICS[(args["component"], args["attribute"])][1]
    steps = []
    for i, raw in enumerate(series.to_numpy(dtype="float64")):
        tokens = (labels[i],) if periods is None else (periods[i], labels[i])
        steps.append(
            {
                "path": pointer(args["name"], *tokens),
                "entity_id": args["name"],
                "metric": args["attribute"],
                "value": json.loads(json.dumps(float(raw))),
                "unit": unit,
            }
        )
    return labels, periods, steps


def active_series_source(args):
    """Taken before get_timeseries runs: the source digest, step count and the
    encoded size of the facts if every step certified."""
    ctx = PyPSAService.get_active_context()
    with network_guard():
        source_digest, series = _series_source(ctx, args)
        if series is None:
            return source_digest, 0, 0
        if len(series) > MAX_SERIES_POINTS:
            return source_digest, len(series), None
        size = len(json.dumps(_series_steps(args, series)[2], ensure_ascii=False).encode())
    return source_digest, len(series), size


def unpointer(path):
    """The tokens of an RFC 6901 pointer built by pointer()."""
    return [t.replace("~1", "/").replace("~0", "~") for t in path.split("/")[1:]]


def capture_series(owner, args, result, digest_before):
    """Evidence for exactly the points one get_timeseries result showed.

    Row i of the result maps to the raw series' row i, by its timestep label
    (and period). A point certifies only when the shown value equals the raw
    value after a JSON round-trip; missing and non-finite values never do.
    """
    ctx = PyPSAService.get_active_context()
    key = (args.get("component"), args.get("attribute"))
    name = args.get("name")
    if (
        key not in SERIES_METRICS
        or digest_before is None
        or not isinstance(result, dict)
        or not ctx.project_uuid
    ):
        return None
    component_class = SERIES_METRICS[key][0]
    index, data = result.get("index"), result.get("data")
    if (
        result.get("columns") != [name]
        or not isinstance(index, list)
        or not isinstance(data, list)
        or not index
        or len(index) != len(data)
    ):
        return None
    authorize(owner, ctx.project_uuid)
    with network_guard():
        if ctx.solver_state.get("status") == "running":
            return None
        source_digest, series = _series_source(ctx, args)
        if (
            source_digest != digest_before
            or series is None
            or not len(series) == len(index) <= MAX_SERIES_POINTS
        ):
            return None
        labels, periods, steps = _series_steps(args, series)
        if labels != index or periods != result.get("periods"):
            return None
        # A repeated timestep would make a path ambiguous.
        if len({step["path"] for step in steps}) != len(steps) or any(
            not isinstance(row, list) or len(row) != 1 for row in data
        ):
            return None
        facts = [
            step for step, row in zip(steps, data) if _certified(row[0], step["value"])
        ]
        if not facts:
            return None
        record = {
            "version": 1,
            "record_kind": "evidence",
            "evidence_id": str(uuid.uuid4()),
            "owner_id": owner,
            "project_id": ctx.project_uuid,
            "org_id": ctx.org_id,
            "source_kind": "configured_input",
            "adapter_version": INPUT_ADAPTERS["series"],
            "scope": {
                "component_class": component_class,
                "component": key[0],
                "entity_id": name,
                "attribute": key[1],
                "kind": "series",
                "points": len(index),
                "certified": len(facts),
            },
            "facts": facts,
            "source_sha256": digest_before,
            "validity": {
                "outcome": "completed",
                "as_of": time.time(),
                "coverage": "series_points",
                "warnings": [
                    "Points of one configured time series, not a series aggregate.",
                    "Missing or non-finite steps are not certified.",
                ],
            },
            "expires_at": time.time() + RETENTION_SECONDS,
        }
        save(record, record["evidence_id"])
    return record


def series_args(scope):
    """The exact get_timeseries args a series evidence scope was captured for."""
    return {
        "component": scope["component"],
        "name": scope["entity_id"],
        "attribute": scope["attribute"],
    }


def _expected_adapter_version(record):
    """The adapter version this server would write for the record's kind."""
    if record["source_kind"] == "scoped_result":
        from harness import calculations

        profile = record["request_scope"]["profile"]
        # A profile this server no longer registers is not re-read as checked.
        if profile not in calculations.PROFILES:
            unavailable("evidence_unavailable")
        return profile
    if record["source_kind"] == "calculated_result":
        from services import assurance_metrics

        return assurance_metrics.PROFILE
    return INPUT_ADAPTERS[record["scope"]["kind"]]


def _require_current_facts(facts, expected):
    """Every stored fact is one the current source gives, so a rewritten
    record whose checksum was recomputed still cannot certify a value."""
    if (
        not isinstance(facts, list)
        or not facts
        or any(f not in expected for f in facts)
    ):
        unavailable("evidence_unavailable")


def _page_facts(ctx, component_class, facts):
    """The facts a page would certify now, for the rows and metrics stored."""
    static = ctx.network.components[component_class].static
    expected = []
    for fact in facts:
        name, metric = fact["entity_id"], fact["metric"]
        unit = INPUT_METRICS[component_class].get(metric)
        if unit is None or name not in static.index or metric not in static.columns:
            continue
        value = _finite(static.at[name, metric])
        if value is not None:
            expected.append(
                {
                    "path": pointer(name, metric),
                    "entity_id": name,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                }
            )
    return expected


def evidence(owner, evidence_id):
    record = load(owner, evidence_id, "evidence")
    try:
        adapter = _expected_adapter_version(record)
        current = record["version"] == 1 and record["adapter_version"] == adapter
    except (KeyError, TypeError, AttributeError):
        current = False
    if not current:
        unavailable("evidence_unavailable")
    if record["source_kind"] == "scoped_result":
        from harness import calculations

        raw = record["raw"]
        scope = record["request_scope"]
        if not scope.get("retain"):
            ctx = PyPSAService.get_active_context()
            if ctx.project_uuid != record["project_id"]:
                unavailable("evidence_unavailable")
            try:
                raw = _scoped_read(ctx, scope, owner)
            except (ValueError, KeyError, TypeError):
                unavailable("evidence_unavailable")
        try:
            facts, slots = calculations.scoped_calculate(raw, scope)
            if (
                digest(raw) != record["source_sha256"]
                or facts != record["facts"]
                or slots != record["slots"]
            ):
                unavailable("evidence_unavailable")
        except (ValueError, KeyError, TypeError):
            unavailable("evidence_unavailable")
        return record
    if record["scope"].get("kind") == "run":
        # As of the run's finish: current inputs and newer runs do not touch it.
        try:
            project_id, manifest, _status = run_source(record["scope"]["entity_id"])
            if (
                project_id != record["project_id"]
                or manifest is None
                or digest(manifest) != record["source_sha256"]
                or _run_scope(manifest) != record["scope"]
            ):
                unavailable("evidence_unavailable")
            _require_current_facts(record["facts"], _run_facts(manifest))
        except (HTTPException, KeyError, ValueError, TypeError, AttributeError):
            unavailable("evidence_unavailable")
        return record
    # Otherwise current inputs only. This does not substitute a cold, saved
    # network for changed foreground inputs.
    ctx = PyPSAService.get_active_context()
    if ctx.project_uuid != record["project_id"]:
        unavailable("evidence_unavailable")
    try:
        with network_guard():
            scope = record["scope"]
            if record["source_kind"] == "calculated_result":
                from services import assurance_metrics

                raw = assurance_metrics.source(ctx, scope["entity_id"])
                if digest(raw) != record["source_sha256"]:
                    unavailable("evidence_unavailable")
                facts, slots = assurance_metrics.calculate(raw, scope["entity_id"])
                if facts != record["facts"] or slots != record["slots"]:
                    unavailable("evidence_unavailable")
                return record
            if ctx.solver_state.get("status") == "running":
                unavailable("evidence_unavailable")
            if scope.get("kind") == "series":
                # Any edit to the series, its source or index invalidates it.
                args = series_args(scope)
                source_digest, series = _series_source(ctx, args)
                if source_digest != record["source_sha256"] or series is None:
                    unavailable("evidence_unavailable")
                _require_current_facts(record["facts"], _series_steps(args, series)[2])
                return record
            if scope.get("kind") == "static_page":
                # Any edit to the class's rows or inputs, or another project,
                # invalidates the page.
                if (
                    page_source_digest(ctx, scope["component_class"])
                    != record["source_sha256"]
                ):
                    unavailable("evidence_unavailable")
                _require_current_facts(
                    record["facts"],
                    _page_facts(ctx, scope["component_class"], record["facts"]),
                )
                return record
            raw = _raw_facts(ctx, scope["component_class"], scope["entity_id"])
            if digest(raw) != record["source_sha256"]:
                unavailable("evidence_unavailable")
            _require_current_facts(record["facts"], raw)
    except (KeyError, ValueError, TypeError):
        unavailable("evidence_unavailable")
    return record


def run_source(run_id):
    """(project id, manifest, status) of one run; the manifest only when the
    run completed and its recorded sections verify."""
    from services import run_evidence

    return run_evidence.checked_run_manifest(run_id)


def _run_facts(manifest):
    """One fact per finite recorded metric, bound to the run by its id."""
    from services import run_evidence

    return [
        {
            "path": pointer(metric),
            "entity_id": manifest["run_id"],
            "metric": metric,
            "value": entry["value"],
            "unit": entry["unit"],
        }
        for metric, entry in sorted((manifest.get("metrics") or {}).items())
        if run_evidence._METRIC.fullmatch(metric)
        and isinstance(entry, dict)
        and _finite(entry.get("value")) is not None
        and isinstance(entry.get("unit"), str)
    ]


def _run_scope(manifest):
    return {
        "kind": "run",
        "entity_id": manifest["run_id"],
        "run_kind": manifest.get("kind"),
        "mode": manifest.get("mode"),
        "finished_at": manifest["finished_at"],
        "unsaved_inputs": bool(manifest.get("unsaved_inputs")),
    }


def capture_run(owner, args, result, digest_before):
    """Evidence for the metrics one get_run_evidence result showed, as of the
    run's finish. A metric certifies only when the shown value equals the
    recorded one; the manifest must be the one digested before the handler."""
    ctx = PyPSAService.get_active_context()
    if digest_before is None or not isinstance(result, dict) or not ctx.project_uuid:
        return None
    authorize(owner, ctx.project_uuid)
    project_id, manifest, _status = run_source(args.get("run_id"))
    if (
        manifest is None
        or project_id != ctx.project_uuid
        or digest(manifest) != digest_before
        or result.get("run_id") != manifest["run_id"]
        or result.get("status") != "completed"
        or not isinstance(result.get("metrics"), dict)
    ):
        return None
    shown = result["metrics"]
    facts = [
        fact
        for fact in _run_facts(manifest)
        if isinstance(shown.get(fact["metric"]), dict)
        and shown[fact["metric"]].get("unit") == fact["unit"]
        and _certified(shown[fact["metric"]].get("value"), fact["value"])
    ]
    if not facts:
        return None
    record = {
        "version": 1,
        "record_kind": "evidence",
        "evidence_id": str(uuid.uuid4()),
        "owner_id": owner,
        "project_id": project_id,
        "org_id": ctx.org_id,
        "source_kind": "run_manifest",
        "adapter_version": INPUT_ADAPTERS["run"],
        "scope": _run_scope(manifest),
        "facts": facts,
        "source_sha256": digest_before,
        "validity": {
            "outcome": "completed",
            "as_of": time.time(),
            "coverage": "run_metrics",
            "warnings": [
                "Metrics as recorded when the run finished; current inputs may differ.",
            ],
        },
        "expires_at": time.time() + RETENTION_SECONDS,
    }
    save(record, record["evidence_id"])
    return record


def capture_dispatch(owner, entity):
    from services import assurance_metrics

    ctx = PyPSAService.get_active_context()
    authorize(owner, ctx.project_uuid)
    with network_guard():
        raw = assurance_metrics.source(ctx, entity)
        facts, slots = assurance_metrics.calculate(raw, entity)
        record = {
            "version": 1,
            "record_kind": "evidence",
            "evidence_id": str(uuid.uuid4()),
            "owner_id": owner,
            "project_id": ctx.project_uuid,
            "org_id": ctx.org_id,
            "source_kind": "calculated_result",
            "adapter_version": assurance_metrics.PROFILE,
            "scope": {
                "component_class": "Generator",
                "entity_id": entity,
                "kind": "full_horizon",
                "start": raw["index"][0],
                "end": raw["index"][-1],
            },
            "facts": facts,
            "slots": slots,
            "raw": raw,
            "source_sha256": digest(raw),
            "validity": {
                "outcome": "completed"
                if all(s["disposition"] == "answered" for s in slots)
                else "partial",
                "coverage": "full_horizon",
                "as_of": time.time(),
                "warnings": [assurance_metrics.DISCLOSURE],
            },
            "expires_at": time.time() + RETENTION_SECONDS,
        }
        save(record, record["evidence_id"])
        return record


def capture_scoped(owner, scope):
    from harness import calculations

    ctx = PyPSAService.get_active_context()
    if ctx.project_uuid != scope["project_id"]:
        raise ValueError("wrong_project")
    authorize(owner, ctx.project_uuid)
    if scope.get("evidence_id"):
        record = evidence(owner, scope["evidence_id"])
        if record["source_kind"] != "scoped_result" or not record["request_scope"].get(
            "retain"
        ):
            raise ValueError("not_retained_run")
        expected = {k: v for k, v in scope.items() if k != "evidence_id"}
        if expected != record["request_scope"]:
            raise ValueError("historical_scope_mismatch")
        return record
    raw = _scoped_read(ctx, scope, owner)
    with network_guard():
        facts, slots = calculations.scoped_calculate(raw, scope)
        first = (raw["storage_unit"] if scope["profile"] in calculations.STORAGE_PROFILES
                 else raw if scope["profile"] in (calculations.carrier.PROFILE, *calculations.branch.PROFILES,
                                                  calculations.lost_load.PROFILE, calculations.price.PROFILE,
                                                  *calculations.emissions.PROFILES,
                                                  calculations.curtailment.PROFILE,
                                                  calculations.capacity.PROFILE, calculations.cycles.PROFILE,
                                                  calculations.balance.PROFILE, calculations.site_import.PROFILE,
                                                  *calculations.investment.PROFILES, calculations.copt_screen.PROFILE,
                                                  calculations.readiness.PROFILE)
                 else next(iter(raw["generators"].values())))
        record = {
            "version": 1,
            "record_kind": "evidence",
            "evidence_id": str(uuid.uuid4()),
            "owner_id": owner,
            "project_id": ctx.project_uuid,
            "org_id": ctx.org_id,
            "source_kind": "scoped_result",
            "adapter_version": scope["profile"],
            "request_scope": scope,
            "scope": {
                "entity_id": scope["entity_id"],
                "kind": "retained_run" if scope.get("retain") else "current_run",
                "start": scope.get("start", first["index"][0]),
                "end": scope.get("end", first["index"][-1]),
            },
            "facts": facts,
            "slots": slots,
            "raw": raw,
            "source_sha256": digest(raw),
            "validity": {
                "coverage": "requested_population",
                "as_of": time.time(),
                "warnings": [calculations.disclosure(scope)],
            },
            "expires_at": time.time() + RETENTION_SECONDS,
        }
        save(record, record["evidence_id"])
        return record


def _scoped_read(ctx, scope, owner):
    """The profile's raw source under the network lock. The investment case's own
    staleness is read outside the lock, as its route reads it, before and after
    the locked read: both must be current and name the same report."""
    from harness import calculations

    invested = scope["profile"] in calculations.investment.PROFILES
    before = calculations.investment.current(ctx) if invested else None
    with network_guard():
        raw = calculations.scoped_source(ctx, scope, owner, investment_state=before)
    if invested:
        after = calculations.investment.current(ctx)
        if not calculations.investment.is_current(after) or after["assumptions_hash"] != raw["assumptions_hash"]:
            raise ValueError("investment_case_not_current")
    return raw


def public(record):
    return {
        k: v
        for k, v in record.items()
        if k
        not in {
            "owner_id",
            "org_id",
            "record_kind",
            "expires_at",
            "source_sha256",
            "reservation_nonce",
            "worker_pid",
            "worker_host",
        }
    }


def answer(owner, answer_id):
    record = load(owner, answer_id, "answer")
    try:
        current = (
            record["version"] == 1
            and record["receipt"]["policy_version"] == POLICY_VERSION
            and hashlib.sha256(record["text"].encode()).hexdigest()
            == record["receipt"]["content_sha256"]
            and isinstance(record["receipt"]["evidence_ids"], list)
        )
    except (KeyError, TypeError, AttributeError):
        current = False
    if not current:
        unavailable("answer_evidence_unavailable")
    for evidence_id in record["receipt"]["evidence_ids"]:
        try:
            evidence(owner, evidence_id)
        except HTTPException:
            unavailable("answer_evidence_unavailable")
    for oid in record["receipt"].get("operation_ids", []):
        from harness.effects import operation

        try:
            if operation(owner, oid)["outcome"] != "completed":
                unavailable("answer_evidence_unavailable")
        except HTTPException:
            unavailable("answer_evidence_unavailable")
    return record


def _unvalidated(turn):
    # A legacy turn is explicitly unvalidated; persisted trust fields without an
    # approved answer record are imported flags, never a checked label (A0.3).
    return {
        **{k: v for k, v in turn.items() if k not in {"receipt", "requires_checked"}},
        "assurance_state": "unvalidated",
    }


def history_projection(turns, owner, *, strict=False):
    """Every approved replay is revalidated; imported flags cannot create trust."""
    projected = []
    for turn in turns:
        if turn.get("answer_id"):
            try:
                checked = answer(owner, turn["answer_id"])
                projected.append(
                    {
                        **turn,
                        "requires_checked": True,
                        "assistant": [{"type": "text", "text": checked["text"]}],
                        "receipt": checked["receipt"],
                        "assurance_state": "checked",
                    }
                )
            except HTTPException:
                projected.append(
                    {
                        **{
                            k: v
                            for k, v in turn.items()
                            if k not in {"assistant", "receipt", "answer_id"}
                        },
                        # An expired/unavailable answer still cannot silently
                        # downgrade its retry. This is policy, never evidence.
                        "requires_checked": True,
                        "assurance_state": "unavailable",
                        "assistant": [
                            {
                                "type": "text",
                                "text": "Previous checked answer is unavailable.",
                            }
                        ],
                    }
                )
        elif strict:
            projected.append(
                {
                    **_unvalidated(turn),
                    "assistant": [
                        {"type": "text", "text": "Legacy answer: not validated."}
                    ],
                }
            )
        else:
            projected.append(_unvalidated(turn))
    return projected
