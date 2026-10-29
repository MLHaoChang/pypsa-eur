"""
The grid and control result views' sheets (package 02, slice R2): Load Flow,
Grid code & connection, Electrical design, Island & ride-through and
Grid-forming.

Each builder reads a run's data from the same function its tab's route calls,
once per run (`ctx.runs`, the base first; a run that is not comparable with
the base gets no data), and hands the payloads to `views.write_view`:

* Load Flow: `loadflow_summary.get_loadflow_view` over the run's own context
  (the base's is the active one), for the whole run and for each investment
  period's window; the per-branch flows are the `/lines` and `/transformers`
  series (`views_network._ts` over the run's own `result_df`).
* Grid code & connection: a hub project's campus study
  (`campus_electrical_service.get_state`), or a planning → dynamics study's
  connection capacity and assessments (`gridspine_service.get_capacity`,
  `get_connection`). Electrical design: the hub's campus study only.
* Island & ride-through: `campus_electrical_service.get_island` and
  `campus_island_report.island_report`. Grid-forming:
  `campus_island_tuning.get_settings`.

The last four read the project's files, not the network: each run's project
row comes from `inputs.project_for(run)`, which resolves the acting user's
access (`project_registry.resolve_project`) before anything of the project is
read; a run the user may not open has no data.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from fastapi import HTTPException

from services.results_workbook.registry import register
from services.results_workbook.views import HORIZON, Key, Table, ViewData, write_view
from services.results_workbook.views_network import (
    _by, _labels, _periods, _sum_or_none, _ts, _ts_frame, _view,
)

__all__: list[str] = []

NO_PERIODS = ("The study behind this view has no per-period split: its figures are per "
              "case or critical hour (each row names its period where it has one), so the "
              "key results are shown for the horizon only.")


#: Number columns that label a row rather than measure it.
LABEL_COLUMNS = frozenset({"Period", "period", "hour", "snapshot", "year", "case", "row",
                           "worst_period", "worst_hour", "invest_period", "build_year",
                           "service_to", "worst_period_with_measures",
                           "worst_hour_with_measures"})


# ── helpers ──────────────────────────────────────────────────────────────

def _scalar(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return "; ".join(map(str, v))
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True, default=str)
    return v


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))


def _records(name: str, unit: str, rows: list[dict] | None,
             columns: list[str] | None = None) -> Table | None:
    """A route's list of row dicts as a long table: the columns holding any
    text (or yes/no) label the rows, the number columns are its values."""
    if not rows:
        return None
    frame = pd.DataFrame([{k: _scalar(v) for k, v in r.items()} for r in rows])
    if columns is not None:
        frame = frame.reindex(columns=columns)
    values = [c for c in frame.columns if c not in LABEL_COLUMNS
              and frame[c].notna().any() and all(_is_number(v) for v in frame[c].dropna())]
    labels = [c for c in frame.columns if c not in values]
    if not labels:  # a row needs a label: its position in the route's list
        frame.insert(0, "row", range(1, len(frame) + 1))
        labels = ["row"]
    return Table(name, unit, frame[labels + values], labels=len(labels))


def _project_view(view_id: str, title: str, read: Callable[[Any, Any], ViewData | None]):
    """Registers a builder over the project's files: `read(run, project)` once
    per comparable run whose project the acting user may open (access first:
    `inputs.project_for` resolves it before any file is read)."""

    @register(view_id)
    def build(wb, ctx) -> bool:
        project_for = ctx.inputs.project_for
        datas: list[ViewData | None] = []
        for run in ctx.runs:
            project = (project_for(run) if project_for is not None and run.network is not None
                       else None)
            datas.append(None if project is None else read(run, project))
        return write_view(wb, ctx, view_id, title, datas)
    return build


def _is_hub(project) -> bool:
    from services.gridspine_service import CAPACITY_EXPANSION, kind_of
    return kind_of(project) == CAPACITY_EXPANSION


def _count(rows: list[dict] | None, field: str, value: str) -> int | None:
    return None if rows is None else sum(1 for r in rows if r.get(field) == value)


# ── Load Flow ────────────────────────────────────────────────────────────

_BRANCH_FIELDS = ("util_pct", "rating_pct", "mean_util_pct", "peak_flow", "mean_flow",
                  "h_at_limit", "h_over_limit")


def _period_windows(n) -> list[tuple[Any, int, int]]:
    """(period, first, last snapshot position) of each investment period."""
    out = []
    level = n.snapshots.get_level_values(0)
    for p in _periods(n):
        idx = np.flatnonzero(level == p)
        if idx[-1] - idx[0] + 1 != len(idx):
            raise ValueError(f"period {p}'s snapshots are not contiguous")
        out.append((p, int(idx[0]), int(idx[-1])))
    return out


def _branch_rows(label, payload: dict) -> list[dict]:
    rows = []
    for r in payload.get("rows") or []:
        cap = r.get("capacity") or {}
        rows.append({"Period": label, "class": r.get("class"), "name": r.get("name"),
                     "basis": r.get("basis"), "peak_at": r.get("peak_at"),
                     **{f: r.get(f) for f in _BRANCH_FIELDS},
                     "rating": (r.get("rating") or {}).get("value"),
                     "rating_basis": (r.get("rating") or {}).get("basis"),
                     "configured_s_nom": cap.get("configured"),
                     "optimized_s_nom": cap.get("optimized")})
    return rows


def _loadflow(run) -> ViewData | None:
    from services.loadflow_summary import LIMITS_CLAIM, get_loadflow_view
    n = run.network
    whole = get_loadflow_view("lopf", context=run.context)
    if whole.get("status") != "ok":
        # The tab's own "nothing to show": no solve, or no line or transformer.
        if whole.get("unavailable_kind") in ("not_solved", "no_ac_branches"):
            return None
        return ViewData(notes=[f"{run.name}: load flow not available "
                               f"({whole.get('unavailable_kind')}): {whole.get('message')}"])
    per = {p: get_loadflow_view("lopf", a, b, context=run.context)
           for p, a, b in _period_windows(n)}
    payloads = {**{str(p): v for p, v in per.items()}, HORIZON: whole}
    d = ViewData(notes=[
        "Line and transformer loading as the Load Flow tab reads it (LOPF active-power "
        "results): the horizon is the tab's default window (every snapshot); each period is "
        "the tab's window over that period's snapshots. Hours are weighted by the generator "
        "snapshot weights × the period's years.", LIMITS_CLAIM])

    def key(label, unit, read):
        by = {}
        for lab, p in payloads.items():
            by[lab] = read(p) if p.get("status") == "ok" else None
        d.keys.append(Key(label, unit, _by(n, {k: v for k, v in by.items() if k != HORIZON},
                                            by[HORIZON])))
    key("Branches ranked", "count", lambda p: p["counts"]["ranked"])
    key("Branches over their limit", "count", lambda p: p["counts"]["over_limit"])
    key("Branches at their limit", "count", lambda p: p["counts"]["at_limit"])
    key("Highest peak loading", "%", lambda p: (p.get("rows") or [{}])[0].get("util_pct"))
    key("Weighted hours in the window", "h", lambda p: p["window"]["weighted_hours"])

    rows = [r for lab, p in payloads.items() if p.get("status") == "ok"
            for r in _branch_rows(lab, p)]
    peak = pd.DataFrame({lab: {f"{r['class']} {r['name']}": r["util_pct"]
                               for r in _branch_rows(lab, p)}
                         for lab, p in payloads.items() if p.get("status") == "ok"})
    if not peak.empty:
        peak.insert(0, "Branch", peak.index)
        d.tables.append(Table("Peak loading by branch and period", "%",
                              peak.reset_index(drop=True)))
    d.tables.append(_records("Branch loading", "%, MW, h", rows))
    skipped = _records("Branches not ranked", "", whole.get("skipped"))
    if skipped is not None:
        d.tables.append(skipped)
    for accessor, name in (("lines_t", "Line flow (bus0)"),
                           ("transformers_t", "Transformer flow (bus0)")):
        ts = _ts(run, accessor, "p0")
        if ts is not None:
            d.tables.append(Table(name, "MW", _ts_frame(ts), _labels(ts)))
    return d


# ── Grid code & connection ───────────────────────────────────────────────

def _campus_results(project) -> tuple[dict, dict] | None:
    from services import campus_electrical_service as ce
    state = ce.get_state(project)
    return None if state.get("results") is None else (state, state["results"])


def _study_grid_code(project) -> ViewData | None:
    from services import gridspine_service as gs
    try:
        cap = gs.get_capacity(project)
    except HTTPException as exc:
        if exc.status_code != 404:  # 404: no screened run yet, as the tab says
            raise
        cap = None
    conn = gs.get_connection(project).get("rows") or []
    if not (cap and cap.get("rows")) and not conn:
        return None
    d = ViewData(notes=["Connection capacity and connection-point assessment of the "
                        "planning → dynamics study (latest run), as the Grid code tab reads "
                        "them; DC rows carry the screen's estimate, AC rows the computed "
                        "capacity.", NO_PERIODS])
    rows = (cap or {}).get("rows") or []
    for kind in ("load", "generation"):
        of = [r for r in rows if r.get("kind") == kind]
        dc = [r["dc_estimate_mw"] for r in of if _is_number(r.get("dc_estimate_mw"))]
        ac = [r["capacity_mw"] for r in of if _is_number(r.get("capacity_mw"))]
        d.keys.append(Key(f"Lowest DC capacity estimate — {kind}", "MW",
                          {HORIZON: float(min(dc)) if dc else None}))
        d.keys.append(Key(f"Lowest AC connection capacity — {kind}", "MW",
                          {HORIZON: float(min(ac)) if ac else None}))
    if rows:
        frame = pd.DataFrame(rows)
        wide = frame.pivot_table(index="bus", columns="kind", values="dc_estimate_mw",
                                 aggfunc="min")
        if not wide.empty:
            wide.columns = [f"{c} (lowest over the hours)" for c in wide.columns]
            wide.insert(0, "Bus", wide.index.astype(str))
            d.tables.append(Table("DC capacity estimate by bus", "MW", wide.reset_index(drop=True)))
        d.tables.append(_records("Connection capacity", "MW", rows))
    if conn:
        for status in sorted({str(r.get("status")) for r in conn}):
            d.keys.append(Key(f"Connection checks — {status}", "count",
                              {HORIZON: _count(conn, "status", status)}))
        d.tables.append(_records("Connection-point assessments", "", conn))
    return d


def _grid_code(run, project) -> ViewData | None:
    if not _is_hub(project):
        return _study_grid_code(project)
    got = _campus_results(project)
    if got is None:
        return None
    state, res = got
    invested = res.get("investment") is not None
    d = ViewData(notes=["Campus PCC compliance against the chosen grid code, as the Grid code "
                        "tab reads it (the campus study): a steady-state screen at the selected "
                        "hours, not a compliance certificate.", NO_PERIODS])
    if state.get("stale"):
        d.notes.append(f"{run.name}: the campus changed since this study ran.")
    comp = res.get("compliance") or []
    for status in ("fail", "pass"):
        d.keys.append(Key(f"Checks {status} as is", "count",
                          {HORIZON: _count(comp, "status_as_is", status)}))
        d.keys.append(Key(f"Checks {status} with measures", "count",
                          {HORIZON: _count(comp, "status_with_measures", status)}))
    if invested:
        inv = res.get("compliance_invested") or []
        d.keys.append(Key("Checks fail with the assets (AC re-solved)", "count",
                          {HORIZON: _count(inv, "status_with_measures", "fail")}))
    req = res.get("requirement") or {}
    d.keys.append(Key("PCC reactive band (±)", "Mvar", {HORIZON: req.get("q_limit_mvar")}))
    d.keys.append(Key("P_ref", "MW", {HORIZON: req.get("p_ref_mw")}))
    d.tables += [t for t in (
        _records("PCC compliance", "", comp),
        _records("PCC compliance with the assets (AC re-solved)", "",
                 res.get("compliance_invested")) if invested else None,
        _records("PCC reactive power", "Mvar", res.get("reactive")),
        _records("Unit reactive capability", "", res.get("ibr_q_curve")),
        _records("Needs an RMS study (not checked)", "",
                 [{"requirement": r} if not isinstance(r, dict) else r
                  for r in res.get("reported_requirements") or []]),
    ) if t is not None]
    if res.get("harmonic_scan"):
        d.notes.append("The harmonic impedance scan is shown in the tab, not in this sheet.")
    return d


# ── Electrical design ────────────────────────────────────────────────────

def _electrical_design(run, project) -> ViewData | None:
    if not _is_hub(project):
        return None  # the tab: the electrical design is a hub project's campus study
    got = _campus_results(project)
    if got is None:
        return None
    state, res = got
    n = run.network
    d = ViewData(notes=["The campus study's electrical design as the Electrical design tab "
                        "reads it: proposals at its critical hours, nothing adopted into the "
                        "model. Costs per investment period from the study's cost table; "
                        "the horizon is their sum only when every period's cost is known."])
    if state.get("stale"):
        d.notes.append(f"{run.name}: the campus changed since this study ran.")
    cost = res.get("cost") or []
    if cost:
        for field, label, unit in (("capex_eur", "Electrical capex", "EUR"),
                                   ("capex_replacement_eur", "Electrical capex, renewals", "EUR"),
                                   ("annualised_eur_per_a", "Electrical cost, annualised",
                                    "EUR/a")):
            per = {r.get("period"): r.get(field) for r in cost}
            by = {str(p): v for p, v in per.items()}
            by[HORIZON] = _sum_or_none(per.values())
            d.keys.append(Key(label, unit, by if _periods(n) else {HORIZON: by[HORIZON]}))
    elif res.get("investment") is None:
        d.notes.append(f"{run.name}: the last run did not invest; no costs.")
    d.keys.append(Key("Unresolved needs", "count",
                      {HORIZON: None if res.get("unresolved") is None
                       else len(res.get("unresolved") or [])}))
    d.keys.append(Key("Critical hours selected", "count",
                      {HORIZON: len(res.get("selection") or [])}))
    d.tables += [t for t in (
        _records("Electrical cost by period", "EUR", cost),
        _records("Investment", "EUR", res.get("investment")),
        _records("Transformers", "MVA", res.get("transformers")),
        _records("Reactive compensation", "Mvar", res.get("compensation")),
        _records("Short circuit", "kA", res.get("short_circuit")),
        _records("Critical hours", "", res.get("selection")),
        _records("Unresolved needs", "", res.get("unresolved")),
        _records("Escalation history", "", res.get("history")),
    ) if t is not None]
    return d


# ── Island & ride-through ────────────────────────────────────────────────

def _island(run, project) -> ViewData | None:
    from services import campus_electrical_service as ce
    from services.campus_island_report import island_report
    if not _is_hub(project):
        return None
    island = ce.get_island(project)
    report = island_report(project)
    tables = [t for t in report.get("tables") or [] if t.get("group") != "inputs"]
    if not tables:  # no island study and no island-hours results: nothing has run
        return None
    d = ViewData(notes=[report.get("caveat") or "", NO_PERIODS])
    if island.get("study_at"):
        d.notes.append(f"{run.name}: island study run {island['study_at']}.")
    if report.get("missing"):
        d.notes.append(f"{run.name}: not in the report: {', '.join(report['missing'])}.")
    seen: set[str] = set()
    for t in report.get("tables") or []:
        name = t.get("title") or t.get("id")
        while name in seen:
            name += " (2)"
        seen.add(name)
        rows = [dict(zip(t.get("columns") or [], r)) for r in t.get("rows") or []]
        table = _records(name, "", rows, list(t.get("columns") or []))
        if table is not None:
            d.tables.append(table)
        if t.get("id") == "costs":
            for r in rows:
                d.keys.append(Key(f"Cost of island capability — {r.get('scenario')}", "EUR",
                                  {HORIZON: r.get("cost of island capability")}))
    d.keys.append(Key("Report tables with assumed rows", "count",
                      {HORIZON: sum(1 for t in report.get("tables") or []
                                    if t.get("assumed_rows"))}))
    return d


# ── Grid-forming ─────────────────────────────────────────────────────────

def _grid_forming(run, project) -> ViewData | None:
    from services import campus_island_tuning as cit
    if not _is_hub(project):
        return None
    got = cit.get_settings(project)
    rec = got.get("recommendation")
    if got.get("state") == "not_run" or rec is None:
        return None
    d = ViewData(notes=[f"{rec.get('label') or 'Heuristic screening'}, never a certificate: "
                        "when no setting passes, capacity is needed.", NO_PERIODS])
    if got.get("state") == "stale":
        d.notes.append(f"{run.name}: {got.get('reason')}")
    cases = rec.get("cases") or []
    d.keys.append(Key("Cases passing before", "count", {HORIZON: _count(cases, "ok_before", True)}))
    d.keys.append(Key("Cases passing after", "count", {HORIZON: _count(cases, "ok_after", True)}))
    d.keys.append(Key("Cases", "count", {HORIZON: len(cases)}))
    d.keys.append(Key("Simulations", "count", {HORIZON: rec.get("simulations")}))
    capacity = rec.get("capacity") or []
    for kind in sorted({str(r.get("kind")) for r in capacity}):
        of = [r for r in capacity if str(r.get("kind")) == kind and _is_number(r.get("delta"))]
        unit = next((r.get("unit") for r in of), "") or ""
        d.keys.append(Key(f"Capacity needed — {kind}", unit,
                          {HORIZON: float(max(r["delta"] for r in of)) if of else None}))
    settings = [{"unit": unit, "model": s.get("model"), "parameter": param,
                 "current": (s.get("current") or {}).get(param),
                 "recommended": (s.get("recommended") or {}).get(param)}
                for unit, s in (rec.get("settings") or {}).items()
                for param in _union_keys(s.get("current"), s.get("recommended"))]
    d.tables += [t for t in (
        _records("Recommended settings", "", settings,
                 ["unit", "model", "parameter", "current", "recommended"]),
        _records("Cases before and after", "", cases),
        _records("Capacity needed", "", capacity),
    ) if t is not None]
    return d


def _union_keys(*dicts) -> list[str]:
    out: dict[str, None] = {}
    for x in dicts:
        out.update(dict.fromkeys(x or {}))
    return list(out)


_view("loadflow", "Load Flow", _loadflow)
_project_view("grid_code", "Grid code & connection", _grid_code)
_project_view("electrical_design", "Electrical design", _electrical_design)
_project_view("island", "Island & ride-through", _island)
_project_view("grid_forming", "Grid-forming", _grid_forming)
