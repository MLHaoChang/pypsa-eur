"""
The calculation-metric producer of "why this number" (provenance-graph issue 09, PG-03b).

`metric_graph(owner, evidence_id, metric)` reads one checked evidence record (`assurance_records.load`) and
declares the metric as a `derived` node. Its `derived_from` edges go to what it was computed from. Each edge
carries a `pins.Pin`:
- the record's `raw` source, one pin per leaf (a generator's `inputs.p` series is one);
- the record's window (`harness.fact_identity.window`);
- the metric's value as its formula gives it now.
A pin's recorded side is what the record holds. Its current side is the same part of
`harness.calculations.scoped_source` read now, so the existing `compare_pin` compares them exactly (GK B (54) Y3).

What it cannot compare reads `uncompared`, never fresh:
- A retained run is not compared with the open network.
- A busy network lock is waited for (bounded) and not read (Y1).
- The run the metric was computed in (`computed_in`) is not compared until slice 04.

A record for another project never reads the open network (Y2): its pins read stale, as unreadable.

It reads only (COPILOT-1): a checked load and a locked read of the network, nothing saved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from services.provenance_graph import pins
from services.provenance_graph.explain import TargetUnknown
from services.provenance_graph.queries import Builder
from services.provenance_graph.types import Edge, Graph, Node, Unrecorded

#: Raw keys about the run, not inputs to the metric: compared by `computed_in` (slice 04), never pinned here. A
#: deny-list, so a raw key a profile adds later becomes one more pin and fails closed (GK B (54) Y4).
RUN_KEYS = frozenset(
    {
        "input_fingerprint",
        "solved_fingerprint",
        "status",
        "condition",
        "valid_solve",
        "profile",
    }
)
SCOPED = "scoped_result"
RETAINED = "retained run; not compared with the open network"
BUSY = "network busy (solve or edit running)"
OTHER_PROJECT = "record is for another project"
RUN_UNCOMPARED = "computed_in uncompared until slice 04"
#: How a scalar the record holds as null is pinned: a dict, which no other pinned value is (series are digests).
MISSING = {"missing": True}


@dataclass
class MetricGraph:
    graph: Graph
    start: str
    run: str
    #: Per node: what the wire shows for it (label, recorded value, unit, stamp).
    info: dict[str, dict] = field(default_factory=dict)


def metric_id(evidence_id: str, metric: str) -> str:
    return f"derived:metric:{evidence_id}/{metric}"


def input_id(evidence_id: str, key: str) -> str:
    return f"input:metric:{evidence_id}/{key}"


def window_id(evidence_id: str) -> str:
    return f"input:window:{evidence_id}"


def formula_id(evidence_id: str, metric: str) -> str:
    return f"input:formula:{evidence_id}/{metric}"


def _canonical(value: Any) -> bytes:
    from services.assurance_records import _canonical as canonical

    return canonical(value)


def pinned(value: Any) -> Any:
    """A value as a pin compares it: a series or mapping as the sha256 of its canonical form (the form the
    record's `source_sha256` is taken over), a scalar as its canonical JSON value, null as `MISSING`. Raises
    ValueError for what the canonical form refuses (a NaN): that value cannot be read."""
    encoded = _canonical(value)
    if isinstance(value, (list, tuple, dict)):
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
    loaded = json.loads(encoded)
    return MISSING if loaded is None else loaded


def raw_paths(raw: Any, prefix: tuple = ()) -> list[tuple]:
    """The pinned leaves of a raw source, as key paths: each value that is not a non-empty mapping, at any depth
    (a generator's `inputs.p` series is one leaf), but none under a run key (`RUN_KEYS`)."""
    if not isinstance(raw, dict) or not raw:
        return [prefix]
    out: list[tuple] = []
    for key, value in raw.items():
        if key not in RUN_KEYS:
            out.extend(raw_paths(value, (*prefix, key)))
    return out


def label(path: tuple) -> str:
    return "/".join(str(p) for p in path)


def _index_paths(raw: dict) -> list[tuple]:
    """Where a raw source may hold the snapshot index its window is taken over (`capture_scoped`'s `first`):
    the source itself, its storage unit, or its first generator."""
    paths: list[tuple] = [("index",), ("storage_unit", "index")]
    generators = raw.get("generators")
    if isinstance(generators, dict) and generators:
        paths.append(("generators", next(iter(generators)), "index"))
    return paths


def _at(raw: dict, path: tuple) -> Any:
    for part in path:
        raw = raw[part]
    return raw


def window_path(record: dict) -> tuple | None:
    """The path whose index gives the record's own window, or None when none does (the window then cannot be
    read now: stale, never fresh)."""
    from harness import fact_identity

    raw, scope, held = record.get("raw") or {}, record["request_scope"], record.get("window")
    for path in _index_paths(raw):
        try:
            if fact_identity.window(_at(raw, path), scope) == held:
                return path
        except (KeyError, TypeError, IndexError, StopIteration):
            continue
    return None


def _load(owner: str | None, evidence_id: str, metric: str) -> tuple[dict, dict]:
    """The checked record and the metric's fact, or a typed refusal (X4, GK B (54) Y5). Another owner's record,
    an expired one and an unknown one read alike: 404, no existence oracle."""
    from harness import calculations
    from services import assurance_records

    if owner is None:
        raise TargetUnknown("an evidence record is read as a signed-in user", status=401)
    if metric not in calculations.DEFINITIONS:
        raise TargetUnknown(f"no calculation metric '{metric}'", status=422)
    try:
        record = assurance_records.load(owner, evidence_id, "evidence")
    except HTTPException as refused:
        if refused.status_code == 401:
            raise TargetUnknown("an evidence record is read as a signed-in user", status=401) from None
        if refused.status_code in (404, 409):
            raise TargetUnknown(f"no evidence record '{evidence_id}'") from None
        raise
    if record.get("source_kind") != SCOPED:
        raise TargetUnknown(f"evidence record '{evidence_id}' holds no calculation metric", status=422)
    fact = next((f for f in record.get("facts") or () if f.get("metric") == metric), None)
    if fact is None:
        raise TargetUnknown(f"evidence record '{evidence_id}' answers no '{metric}'")
    return record, fact


class _Current:
    """The current raw source, read once: `raw`, or why it cannot be compared (`uncompared`) or read (`error`)."""

    def __init__(self, record: dict, owner: str) -> None:
        self.raw: dict | None = None
        self.uncompared: str | None = None
        self.error: str | None = None
        scope = record["request_scope"]
        if scope.get("retain"):
            self.uncompared = RETAINED
            return
        from services import assurance_records
        from services.pypsa_service import PyPSAService

        ctx = PyPSAService.get_active_context()
        if ctx.project_uuid != record["project_id"]:
            self.error = OTHER_PROJECT  # Y2: the open network is another project's; it is not read
            return
        try:
            self.raw = assurance_records.scoped_read(ctx, scope, owner)
        except HTTPException as refused:
            detail = refused.detail if isinstance(refused.detail, dict) else {}
            if refused.status_code == 409 and detail.get("error_kind") == "assurance_context_changed":
                self.uncompared = BUSY  # Y1: the lock was not had in time; not a comparison
            else:
                self.error = str(detail.get("error_kind") or refused.detail)
        except (ValueError, KeyError, TypeError) as exc:
            self.error = str(exc) or type(exc).__name__

    def read(self, get):
        """A pin's `read`: `get(raw)` of the current source, raising when it cannot be read (stale, P3)."""

        def _read():
            if self.raw is None:
                raise LookupError(self.error)
            return get(self.raw)

        return _read


def metric_graph(owner: str | None, evidence_id: str, metric: str) -> MetricGraph:
    from harness import calculations, fact_identity

    record, fact = _load(owner, evidence_id, metric)
    scope = record["request_scope"]
    raw = record.get("raw") or {}
    validity = record.get("validity") or {}
    start = metric_id(evidence_id, metric)
    b = Builder(None)
    b.node(Node("derived", start))
    info = {
        start: {
            "label": metric,
            "value": fact.get("value"),
            "unit": fact.get("unit"),
            "stamp": {
                "source": f"evidence:{evidence_id}",
                "formula_id": fact.get("formula_id"),
                "profile": scope.get("profile"),
                "entity_id": fact.get("entity_id"),
                "as_of": validity.get("as_of"),
            },
        }
    }
    current = _Current(record, owner)

    def link(dst: str, label: str, recorded: Any, get) -> None:
        b.node(Node("input", dst))
        if current.uncompared is not None:
            b.edge(Edge("derived_from", start, dst, Unrecorded(current.uncompared)))
        else:
            b.edge(Edge("derived_from", start, dst, pins.Pin(label, recorded, current.read(get))))

    def recorded_pin(value: Any) -> Any:
        try:
            return pinned(value)
        except (TypeError, ValueError):
            return None  # the record holds no comparable value: `compare_pin` reads it "not recorded"

    wid = window_id(evidence_id)
    window_at = window_path(record)

    def window_now(now: dict) -> Any:
        if window_at is None:
            raise LookupError("the record's window matches no index it holds")
        return json.loads(_canonical(fact_identity.window(_at(now, window_at), scope)))

    link(wid, "window", record.get("window"), window_now)
    info[wid] = {"label": "window", "value": record.get("window"), "unit": None, "stamp": None}

    def recalculated(now: dict) -> Any:
        facts, _slots = calculations.scoped_calculate(now, scope)
        for f in facts:
            if f.get("metric") == metric:
                return f.get("value")
        raise LookupError(f"'{metric}' is not answered now")

    fid = formula_id(evidence_id, metric)
    link(fid, f"value ({fact.get('formula_id')})", fact.get("value"), recalculated)
    info[fid] = {
        "label": f"value ({fact.get('formula_id')})",
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "stamp": None,
    }
    held_paths = raw_paths(raw)
    paths = list(held_paths)
    if current.raw is not None:
        paths += [p for p in raw_paths(current.raw) if p not in paths]  # a leaf only the current source has
    for path in paths:
        try:
            recorded = recorded_pin(_at(raw, path)) if path in held_paths else None
        except (KeyError, TypeError):
            recorded = None
        nid = input_id(evidence_id, label(path))
        link(nid, label(path), recorded, lambda now, path=path: pinned(_at(now, path)))
        info[nid] = {"label": label(path), "value": pins.shown(recorded), "unit": None, "stamp": None}
    return MetricGraph(b.build(), start, f"run:{evidence_id}", info)
