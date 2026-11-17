"""
The Guided Energy Hub study's figures on the provenance graph (guided-results 02a, owner decision WHY-FULL-1).

`read(ctx)` takes one finished study record (`solver_state["eh_study"]`) through one normalising reader
(`normalise`), with the current inputs it is compared with, under the network's mutation lock (GK B W2). It never
waits for that lock: a held lock reads every comparison as `BUSY`, uncompared, never stale.

`study_graph(read)` declares one `derived` node per figure, each -derived_from-> `results:study` -computed_in->
`run:study` (`runs.add_study`, the study verdict the run graph already uses: `study_state.edited_since`):

- `expected_shortfall` (the report's `mc_lole_h`, h/yr) -derived_from-> `lole_ci` (the MC's interval, a step);
- `verdict` (the certification verdict) -derived_from-> `lole_ci`;
- `design_cost` (the report's `cost_at_target_eur`) -derived_from-> the finance and currency settings and the
  pack's prices. The study records neither and the revision counter does not cover `PUT /finance`, so both read
  uncompared: design_cost never reads fresh on the revision alone (GK B W1).

`run:study` -derived_from-> its inputs, each a `pins.Pin` of what the record holds against what a study started now
would read, compared exactly by `pins.compare_pin` (W3): the goal's value, basis and unit; the site type; the Monte
Carlo draws, horizon years, seed (only when recorded) and convergence target; the network revision; the run
choices no edit seam moves the revision for (pack overrides beyond the goal, stages, solve budget, demand-response
buses, DtC config and attribution), against the request Guided would send now; and each row of the record's
`assumptions` list, when it has one, against the network field the row names. A field the record did not capture,
or holds with the wrong type, records `Unrecorded` and reads uncompared, never fresh and never a default (W4).

Reads only (COPILOT-1): a locked read of the record, the solver config and the network; nothing is written.
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass, field
from typing import Any

from services.provenance_graph import pins, runs
from services.provenance_graph.explain import TargetUnknown
from services.provenance_graph.queries import Builder, staleness
from services.provenance_graph.types import Edge, Graph, Node, State, Unrecorded

FIGURES = ("expected_shortfall", "design_cost", "verdict")
#: The record shape this reader knows; an unversioned record is this shape (the runner writes no version yet).
RECORD_VERSION = 1
FINISHED = ("done", "failed", "aborted")
BUSY = "network busy (solve or edit running)"
#: The project goal's stored form: `target_lole_h` is hours per year (`AvailabilityTarget`, `reliability_goal`).
GOAL_BASIS = "h_per_year"
GOAL_UNIT = "h"
#: How "no LOLE goal" is pinned, on both sides: None is "not recorded" to `compare_pin`.
NO_GOAL = "no goal set"
#: How a run choice left at the study's default (recorded null) is pinned, on both sides.
DEFAULT_CHOICE = "none (the study's default)"
#: The run choices: none crosses an edit seam, so the revision never catches one; each is pinned against the
#: request Guided would send now (`flow_service._request_for` through `eh_request_with_goal`). Key → (record key,
#: plain words, accepted types).
CHOICES = {
    "run/pack_overrides": ("pack_overrides", "pack overrides beyond the goal", (dict,)),
    "run/stages": ("stages", "stages", (list,)),
    "run/budget_solves": ("budget_solves", "solve budget", (int,)),
    "run/dsr_buses": ("dsr_buses", "demand-response buses", (list,)),
    "run/dtc_config": ("dtc_config", "DtC config", (dict,)),
    "run/dtc_attribution": ("dtc_attribution", "DtC attribution", (str,)),
}
FINANCE_UNCOMPARED = (
    "the study did not record the finance and currency settings, and PUT /finance does not move the revision"
)
PACK_UNCOMPARED = "the study did not record the pack's prices"
RESULTS = runs.results_id("study")
RUN = runs.run_id("study")
LOLE_CI = "derived:eh_study/lole_ci"

_UNITS = {"expected_shortfall": "h/yr", "design_cost": "EUR", "verdict": None}


class StudyNotRun(TargetUnknown):
    """No finished study to explain: none recorded (404) or one still running (409)."""

    error_kind = "study_not_run"


def figure_id(figure: str) -> str:
    return f"derived:eh_study/{figure}"


def input_id(key: str) -> str:
    return f"input:eh_study/{key}"


# ── the one normalising reader ───────────────────────────────────────────────


def _number(d: dict, key: str, *, none: Any = None) -> Any:
    """A finite int or float at `d[key]`, or `Unrecorded` naming why not. `none`: what a recorded null pins as
    (None: a null is not recorded)."""
    if key not in d:
        return Unrecorded(f"{key}: not recorded")
    v = d[key]
    if v is None:
        return Unrecorded(f"{key}: null") if none is None else none
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return Unrecorded(f"{key}: not a finite number")
    return v


def _int(d: dict, key: str) -> Any:
    if key not in d:
        return Unrecorded(f"{key}: not recorded")
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, int):
        return Unrecorded(f"{key}: not an integer")
    return v


def _text(d: dict, key: str) -> Any:
    if key not in d:
        return Unrecorded(f"{key}: not recorded")
    v = d[key]
    return v if isinstance(v, str) else Unrecorded(f"{key}: not text")


def _choice(d: dict, key: str, words: str, types: tuple, *, nullable: bool = True) -> Any:
    """A run choice as recorded: the value, `DEFAULT_CHOICE` for a recorded null, or `Unrecorded`."""
    if key not in d:
        return Unrecorded(f"the study did not record its {words}")
    v = d[key]
    if v is None and nullable:
        return DEFAULT_CHOICE
    if isinstance(v, bool) or not isinstance(v, types):
        return Unrecorded(f"{key}: not {' or '.join(t.__name__ for t in types)}")
    return v


#: Pack overrides the goal pins already compare (`goal/value`; a target sets the certification metric).
_GOAL_OVERRIDES = ("target_lole_h", "certification_metric")


def beyond_goal(overrides: Any) -> Any:
    """Pack overrides without the goal's own keys; none left is the study's default."""
    if not isinstance(overrides, dict):
        return overrides
    rest = {k: v for k, v in overrides.items() if k not in _GOAL_OVERRIDES}
    return rest or DEFAULT_CHOICE


def _scalar(v: Any) -> bool:
    return isinstance(v, (str, bool)) or (isinstance(v, (int, float)) and math.isfinite(v))


@dataclass(frozen=True)
class Record:
    """A finished study record, normalised: each field a plain value or `Unrecorded`. `unreadable` names why the
    whole record cannot be compared (wrong version or shape); `raw` is the record for `edited_since`."""

    status: str | None = None
    unreadable: str | None = None
    raw: dict | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    figures: dict[str, Any] = field(default_factory=dict)
    lole_ci: Any = None
    period_basis: Any = None
    has_seed: bool = False
    assumptions: Any = ()


def normalise(raw: Any) -> Record:
    """The record as the graph reads it. Refused (`StudyNotRun`) when none is recorded or one is running."""
    if raw is None:
        raise StudyNotRun("no Energy Hub study has been run in this session")
    if not isinstance(raw, dict):
        return Record(unreadable="the study record is not an object")
    status = raw.get("status")
    if status == "running":
        raise StudyNotRun("the Energy Hub study is still running", status=409)
    if status not in FINISHED:
        return Record(unreadable=f"the study record's status {status!r} is not a finished study")
    version = raw.get("version", RECORD_VERSION)
    if version != RECORD_VERSION or isinstance(version, bool):
        return Record(status=status, unreadable=f"the study record's version {version!r} is not one this reads")
    report = raw.get("report")
    report = report if isinstance(report, dict) else {}
    sections = report.get("sections")
    cert = sections.get("certification") if isinstance(sections, dict) else None
    payload = cert.get("payload") if isinstance(cert, dict) else None
    payload = payload if isinstance(payload, dict) else {}
    fields = {
        "goal/value": _number(payload, "target_lole_h", none=NO_GOAL),
        "goal/basis": _text(payload, "target_basis"),
        "goal/unit": _text(payload, "target_unit"),
        "site_archetype": _text(raw, "archetype"),
        "mc/draws": _int(payload, "draws"),
        "mc/horizon_years": _number(payload, "horizon_years"),
        "network_revision": _int(raw, "network_revision"),
    }
    for node, (key, words, types) in CHOICES.items():
        fields[node] = _choice(raw, key, words, types, nullable=key != "budget_solves")
    fields["run/pack_overrides"] = beyond_goal(fields["run/pack_overrides"])
    fields["mc/cov_target"] = _choice(payload, "cov_target", "MC convergence target", (int, float), nullable=False)
    has_seed = payload.get("seed") is not None
    if has_seed:
        fields["mc/seed"] = _int(payload, "seed")
    ci = payload.get("lole_ci")
    lole_ci = (
        list(ci)
        if isinstance(ci, list) and len(ci) == 2 and all(not isinstance(_number({"v": x}, "v"), Unrecorded) for x in ci)
        else Unrecorded("lole_ci: not a pair of numbers")
    )
    verdict = _text(payload, "verdict") if payload.get("verdict") is not None else Unrecorded("verdict: not recorded")
    assumptions = raw.get("assumptions", ())
    return Record(
        status=status,
        raw=raw,
        fields=fields,
        figures={
            "expected_shortfall": _number(report, "mc_lole_h"),
            "design_cost": _number(report, "cost_at_target_eur"),
            "verdict": verdict,
        },
        lole_ci=lole_ci,
        period_basis=report.get("period_basis"),
        has_seed=has_seed,
        assumptions=tuple(assumptions)
        if isinstance(assumptions, (list, tuple))
        else Unrecorded("assumptions: not a list"),
    )


# ── the one locked read ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StudyRead:
    """One read: the record, whether the lock was busy, the current revision and the current inputs (each a value
    or the exception reading it raised)."""

    record: Record
    busy: bool
    revision: int
    current: dict[str, Any] = field(default_factory=dict)
    rows: tuple = ()


def _try(fn) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - an unreadable current input is stale, never fresh
        return exc


def _row_value(n, row: Any) -> Any:
    """What the network holds now for the field an assumption row names; raises when it cannot be read."""
    from services.asset_schema.schema import CLASS_ATTR

    table = getattr(n, CLASS_ATTR[row["component"]])
    value = table.at[row["entity"], row["field"]]
    return value.item() if hasattr(value, "item") else value


def _current(ctx, state: dict, record: Record) -> tuple[dict[str, Any], tuple]:
    """The inputs a study started now would read, as `eh_request_with_goal` and the runner resolve them: the
    project's goal, falling back to the site type's pack; the network's horizon and revision."""
    from services import reliability_goal as rg
    from services.adequacy.eh_study import DEFAULT_MC_SEED
    from services.adequacy.eh_study_runner import _PACK_FACTORY
    from services.adequacy.metrics import horizon_years

    goal = _try(lambda: rg.active_goal(state))
    pack = _try(lambda: _PACK_FACTORY[goal["site_archetype"]["value"]]())

    def goal_or_pack(key: str, from_pack):
        if goal[key]["source"] != rg.DEFAULT:
            return goal[key]["value"]
        return from_pack(pack)

    def target():
        value = goal_or_pack("target_lole_h", lambda p: p.availability.target_lole_h)
        return NO_GOAL if value is None else value

    def request():
        from services import network_lengths
        from services.adequacy.eh_study_runner import EhStudyRequest
        from services.flow_service import _request_for, read_template

        d = network_lengths.active_project_dir()
        template = read_template(d) if d is not None else None
        body = EhStudyRequest(**_request_for(template, state.get("solver_config")))
        return rg.eh_request_with_goal(body, goal)[0]

    req = _try(request)

    def choices() -> dict[str, Any]:
        from models.energy_hub import DEFAULT_EH_BUDGET_SOLVES
        from services.adequacy.eh_study import DEFAULT_MC_COV_TARGET, validate_stages
        from services.adequacy.eh_study_runner import normalised_overrides, resolve_dtc_attribution

        def tok(v):
            return DEFAULT_CHOICE if v is None else v

        raw_dtc, attribution = resolve_dtc_attribution(req.dtc_config, req.dtc_attribution)
        stages = validate_stages(req.stages)
        cov = (req.mc or {}).get("cov_target")
        return {
            "run/pack_overrides": beyond_goal(tok(normalised_overrides(req.pack_overrides))),
            "run/stages": tok(list(stages) if stages is not None else None),
            "run/budget_solves": int(req.budget_solves if req.budget_solves is not None else DEFAULT_EH_BUDGET_SOLVES),
            "run/dsr_buses": tok(list(dict.fromkeys(str(b) for b in req.dsr_buses)) if req.dsr_buses else None),
            "run/dtc_config": tok(dict(raw_dtc) if raw_dtc else None),
            "run/dtc_attribution": tok(attribution),
            "mc/cov_target": cov if cov is not None else getattr(pack, "mc_cov_target", DEFAULT_MC_COV_TARGET),
        }

    chosen = _try(choices)
    current = {
        **{k: (chosen if isinstance(chosen, Exception) else chosen[k]) for k in (*CHOICES, "mc/cov_target")},
        "goal/value": _try(target),
        "goal/basis": GOAL_BASIS,
        "goal/unit": GOAL_UNIT,
        "site_archetype": _try(lambda: goal["site_archetype"]["value"]),
        "mc/draws": _try(lambda: goal_or_pack("mc_draws", lambda p: getattr(p, "mc_draws", rg.DEFAULT_MC_DRAWS))),
        "mc/horizon_years": _try(lambda: float(horizon_years(ctx.network))),
        "mc/seed": _try(lambda: getattr(pack, "mc_seed", DEFAULT_MC_SEED)),
        "network_revision": runs._revision(ctx),
    }
    rows = record.assumptions if isinstance(record.assumptions, tuple) else ()
    return current, tuple(_try(lambda r=r: _row_value(ctx.network, r)) for r in rows)


def read(ctx=None) -> StudyRead:
    """The record and the current inputs, read once under the mutation lock when it is free (never waiting for
    it) and `solver_state` under its own lock (lock order: mutation, then solver_state). A held lock reads the
    record only: every comparison is `BUSY`."""
    from services.pypsa_service import PyPSAService

    if ctx is None:
        ctx = PyPSAService.get_active_context()
    lock = getattr(ctx, "mutation_lock", None)
    got = lock is not None and lock.acquire(blocking=False)
    try:
        with getattr(ctx, "solver_state_lock", None) or contextlib.nullcontext():
            state = dict(getattr(ctx, "solver_state", None) or {})
        record = normalise(state.get("eh_study"))
        revision = runs._revision(ctx)
        if lock is not None and not got:
            return StudyRead(record, True, revision)
        current, rows = _current(ctx, state, record)
        return StudyRead(record, False, revision, current, rows)
    finally:
        if got:
            lock.release()


# ── the graph ────────────────────────────────────────────────────────────────


@dataclass
class StudyGraph:
    graph: Graph
    #: Per node: what the wire shows for it (label, recorded value, unit, stamp), as `explain._metric_step` reads it.
    info: dict[str, dict] = field(default_factory=dict)


_LABELS = {
    "goal/value": "goal value",
    "goal/basis": "goal basis",
    "goal/unit": "goal unit",
    "site_archetype": "site type",
    "mc/draws": "MC draws",
    "mc/horizon_years": "horizon years",
    "mc/seed": "MC seed",
    "mc/cov_target": "MC convergence target",
    "network_revision": "network revision",
    **{k: words for k, (_key, words, _types) in CHOICES.items()},
}


def _reader(value: Any):
    def read():
        if isinstance(value, Exception):
            raise value
        return value

    return read


def _shown(v: Any) -> Any:
    return None if isinstance(v, Unrecorded) else pins.shown(v)


def study_graph(r: StudyRead) -> StudyGraph:
    rec = r.record
    blocked = BUSY if r.busy else rec.unreadable
    b = Builder(None)
    info: dict[str, dict] = {}

    def link(src: str, key: str, label: str, recorded: Any, current: Any, unit: str | None = None) -> None:
        dst = input_id(key)
        b.node(Node("input", dst))
        if blocked is not None:
            via: Any = Unrecorded(blocked)
        elif isinstance(recorded, Unrecorded):
            via = recorded
        else:
            via = pins.Pin(label, recorded, _reader(current))
        b.edge(Edge("derived_from", src, dst, via))
        info[dst] = {"label": label, "value": _shown(recorded), "unit": unit, "stamp": None}

    for figure in FIGURES:
        fid = figure_id(figure)
        b.node(Node("derived", fid))
        value = rec.figures.get(figure)  # none at all when the whole record is unreadable
        stamp = None
        if isinstance(value, Unrecorded):
            stamp = {"unreadable": value.reason}
        elif figure == "design_cost":
            stamp = {"period_basis": rec.period_basis if isinstance(rec.period_basis, str) else None}
        info[fid] = {"label": figure, "value": _shown(value), "unit": _UNITS[figure], "stamp": stamp}
        b.edge(Edge("derived_from", fid, RESULTS))
    b.node(Node("derived", LOLE_CI))
    info[LOLE_CI] = {"label": "lole_ci", "value": _shown(rec.lole_ci), "unit": "h per horizon", "stamp": None}
    for figure in ("expected_shortfall", "verdict"):
        b.edge(Edge("derived_from", figure_id(figure), LOLE_CI))
    b.edge(Edge("derived_from", LOLE_CI, RESULTS))

    edited = None
    if blocked is None:
        from services import study_state

        edited = study_state.edited_since(rec.raw, revision=r.revision)
    runs.add_study(
        b,
        rec.status == "done",
        edited,
        Unrecorded(blocked or "the study recorded no network revision"),
    )
    info[RESULTS] = {"label": "study results", "value": None, "unit": None, "stamp": None}
    info[RUN] = {"label": "study run", "value": rec.status, "unit": None, "stamp": None}

    for key, label in _LABELS.items():
        if key == "mc/seed" and not rec.has_seed:
            continue  # a seed is pinned only when the record holds one
        unit = {"goal/value": GOAL_UNIT, "mc/horizon_years": "years"}.get(key)
        link(RUN, key, label, rec.fields.get(key, Unrecorded(f"{label}: not recorded")), r.current.get(key), unit)

    if isinstance(rec.assumptions, Unrecorded):
        link(RUN, "assumptions", "assumptions", rec.assumptions, None)
    seen: set[str] = set()
    for i, row in enumerate(rec.assumptions if isinstance(rec.assumptions, tuple) else ()):
        ok = isinstance(row, dict)
        name = str(row.get("key")) if ok and row.get("key") is not None else f"#{i}"
        key = f"assumption/{name}" if name not in seen else f"assumption/{name}#{i}"
        seen.add(name)
        label = str(row.get("label") or name) if ok else name
        if not ok:
            recorded: Any = Unrecorded("assumption row: not an object")
        elif not all(isinstance(row.get(k), str) for k in ("component", "entity", "field")):
            recorded = Unrecorded("assumption row: names no network field")
        elif not _scalar(row.get("value")):
            recorded = Unrecorded("assumption row: no comparable value")
        else:
            recorded = row["value"]
        link(RUN, key, label, recorded, r.rows[i] if i < len(r.rows) else None)

    cost = figure_id("design_cost")
    link(cost, "finance", "finance and currency settings", Unrecorded(FINANCE_UNCOMPARED), None)
    link(cost, "pack_prices", "pack prices", Unrecorded(PACK_UNCOMPARED), None)
    return StudyGraph(b.build(), info)


def verdict(g: Graph, start: str) -> tuple[State, list[str]]:
    """A figure's state: its own walk to the run (`staleness`), and the run's inputs, which the walk does not
    follow past a `computed_in` edge. Stale when either is; else uncompared when either names anything; else fresh
    (each walk is never fresh having compared nothing: P1)."""
    walks = [staleness(g, start), staleness(g, RUN)]
    uncompared = list(dict.fromkeys(u for v in walks for u in v.uncompared))
    states = {v.state for v in walks}
    state: State = "stale" if "stale" in states else "uncompared" if "uncompared" in states else "fresh"
    return state, uncompared
