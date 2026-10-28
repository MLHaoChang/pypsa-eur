"""
The Scenarios sheet (owner decision RW-SCEN-1): the base run beside every
sensitivity run of it, key KPIs per period and over the horizon, deltas
against the base, and native charts.

The runs are the base's sensitivity cases as `harness/sensitivity_comparison`
reads them: each case is opened only when the acting user may open it
(`project_registry.resolve_project`, the check opening the project makes; the
others are counted, never named), at most `MAX_CASES` of them, each read from
its own saved context (`workflow_tools._load_context`: its own network,
solver state and user series; the active context is not touched), and judged
comparable with the base by the same reasons the comparison gives
(`sensitivity_comparison._reasons`). A run that is not comparable shows its
reasons and no figures; a delta is a live formula only between two number
cells.

The route supplies the two router-owned reads per run: `result_df_for(state)`
(the result lookup over that run's solver state) and `ic_key(ctx, result_df)`
(the run's current investment-case staleness key, or `(None, why)`).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openpyxl.utils import get_column_letter

from services.results_workbook import charts
from services.results_workbook.cells import BOLD, formula, heading, put, width
from services.results_workbook.overview import CASE_ROWS, OverviewFigures, overview_figures

__all__ = ["TITLE", "Run", "Scenarios", "gather_scenarios", "write_scenarios"]

TITLE = "Scenarios"
HORIZON = "horizon"


@dataclass
class Run:
    """One run of the comparison; `runs[0]` is the base."""

    name: str
    project_id: str | None = None
    figs: OverviewFigures | None = None
    # `sensitivity_comparison._values(read)`: metric → (value, why, note).
    headline: dict | None = None
    ic_report: Any = None
    case_state: dict | None = None
    # `sensitivity_comparison` reason codes; empty when comparable with the base.
    reasons: list[str] = field(default_factory=list)


@dataclass
class Scenarios:
    runs: list[Run]                # the sensitivity runs, base excluded
    no_access: int = 0             # cases the acting user may not open
    over_cap: int = 0              # cases past `MAX_CASES`
    base_headline: dict | None = None
    # The open base differs from its saved run (the Compare-view rows read the saved one).
    base_unsaved: bool = False


def _cases(base_project_id: str) -> tuple[list[tuple[Any, dict]], int]:
    """([(case, spec)] the acting user may open, by name; count of the others)."""
    from fastapi import HTTPException

    from harness import sensitivity_comparison as sc
    from services import chat_tools, project_registry, workflow_tools as wt
    with chat_tools._acting() as (db, user):
        base = sc._resolve(db, user, base_project_id)
        cases, no_access = [], 0
        for child in project_registry.direct_children(db, base):
            spec = wt._read_json(project_registry.project_dir(child) / wt.SENSITIVITY_CASE_FILE)
            if not spec:
                continue
            try:
                child = project_registry.resolve_project(db, user, str(child.id))
            except HTTPException:
                no_access += 1
                continue
            cases.append((child, spec))
    return sorted(cases, key=lambda pair: pair[0].name.lower()), no_access


def gather_scenarios(base_project_id: str, base_basis: dict, *,
                     result_df_for: Callable[[dict], Callable],
                     ic_key: Callable[[Any, Callable], tuple[tuple[str, dict] | None, str | None]],
                     ) -> Scenarios | None:
    """The base's sensitivity runs, or None when it has none the user can see
    or hide. `base_basis` is the open base's `fingerprint`, `mode`, `basis`
    (`experiment_comparison.basis_digests`) and `ok` (its results are current)."""
    from harness import sensitivity_comparison as sc
    from services.finance.investment_case_runner import staleness
    from services.finance.report import load_ic_report
    cases, no_access = _cases(base_project_id)
    if not cases and not no_access:
        return None
    shown, over_cap = cases[:sc.MAX_CASES], max(0, len(cases) - sc.MAX_CASES)
    runs = []
    for project, spec in shown:
        read = sc._read(project)
        reasons = ([] if base_basis["ok"] else ["baseline_unavailable"]) + sc._reasons(
            base_basis, read, spec, base_basis["fingerprint"])
        run = Run(name=project.name, project_id=str(project.id), reasons=reasons)
        if not reasons:
            ctx = read["ctx"]
            state = ctx.solver_state
            result_df = result_df_for(state)
            run.figs = overview_figures(ctx.network, state.get("solver_config"),
                                        state.get("last_lost_load"), result_df)
            run.headline = sc._values(read)
            run.ic_report = load_ic_report(state)
            if run.ic_report is not None:
                current, why = ic_key(ctx, result_df)
                run.case_state = staleness(run.ic_report.model_dump(mode="json"), current, reason=why)
        runs.append(run)
    base_read = sc._read(_base_project(base_project_id))
    return Scenarios(runs=runs, no_access=no_access, over_cap=over_cap,
                     base_headline=sc._values(base_read),
                     base_unsaved=base_read["fingerprint"] != base_basis["fingerprint"])


def _base_project(base_project_id: str):
    from harness import sensitivity_comparison as sc
    from services import chat_tools
    with chat_tools._acting() as (db, user):
        return sc._resolve(db, user, base_project_id)


# ── the sheet ────────────────────────────────────────────────────────────

def _results_text(run: Run) -> str:
    from harness import sensitivity_comparison as sc
    if not run.reasons:
        return "current"
    return sc._not_comparable(run.name, run.reasons).split(": ", 1)[1]


def _case_text(run: Run) -> str:
    if run.reasons:
        return "not compared"
    if run.ic_report is None:
        return "none: run the investment case"
    st = run.case_state or {}
    if st.get("stale"):
        changed = ", ".join(st.get("changed") or []) or st.get("reason") or "?"
        return f"out of date (changed: {changed}): its figures are not shown"
    return "current"


def _case_value(run: Run, reader) -> float | None:
    if run.reasons or run.ic_report is None or (run.case_state or {}).get("stale"):
        return None
    try:
        v = reader(run.ic_report)
    except (AttributeError, KeyError, TypeError):
        return None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _rows(base: Run, runs: list[Run], base_headline: dict | None) -> list[tuple[str, list]]:
    """(label, [value per run, base first]) in sheet order: every Overview line
    over the horizon then per period, LCOE by carrier, the investment case,
    then the Compare view's headline metrics."""
    from harness import sensitivity_comparison as sc
    every = [base, *runs]
    periods = base.figs.periods if base.figs else []
    out: list[tuple[str, list]] = []

    def line_value(run: Run, key: str, period: str):
        if run.figs is None:
            return None
        line = next((x for x in run.figs.lines if x.key == key), None)
        if line is None:
            return None
        return line.total if period == HORIZON else line.by_period.get(period)

    for period in [HORIZON, *periods]:
        for line in base.figs.lines if base.figs else []:
            out.append((f"{line.label.strip()} ({line.unit}), {period}",
                        [line_value(r, line.key, period) for r in every]))
    carriers = sorted({c for r in every if r.figs for c in r.figs.lcoe_by_carrier})
    for period in [HORIZON, *periods]:
        for carrier in carriers:
            vals = []
            for r in every:
                total, by = (r.figs.lcoe_by_carrier.get(carrier, (None, {})) if r.figs else (None, {}))
                vals.append(total if period == HORIZON else by.get(period))
            out.append((f"LCOE, {carrier} (EUR/MWh), {period}", vals))
    for _key, label, unit, reader in CASE_ROWS:
        out.append((f"{label} ({unit}), {HORIZON}", [_case_value(r, reader) for r in every]))
    for metric, (label, unit, *_rest) in sc.METRICS.items():
        vals = [None if (r is base and base_headline is None) else
                ((base_headline if r is base else r.headline) or {}).get(metric, (None,))[0]
                for r in every]
        out.append((f"{label} ({unit}), {HORIZON}, Compare view", vals))
    return out


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def write_scenarios(ws, base: Run, scen: Scenarios) -> dict:
    """Write the sheet; returns where things went (rows by label, columns)."""
    ws.title = TITLE
    runs = scen.runs
    every = [base, *runs]
    put(ws, 1, 1, "Scenarios: the base run and its sensitivity runs")
    ws.cell(1, 1).font = BOLD
    put(ws, 2, 1, "Deltas are run − base, live formulas between two number cells; a run "
                  "that cannot be compared with the base shows why and no figures.")
    notes = []
    if scen.no_access:
        notes.append(f"{scen.no_access} scenario(s) not shown: no access")
    if scen.over_cap:
        notes.append(f"{scen.over_cap} scenario(s) not shown: the sheet compares up to "
                     f"{len(runs)} by name")
    if scen.base_unsaved:
        notes.append("The open base has unsaved changes: its Compare-view rows read its saved run.")
    for i, text in enumerate(notes):
        put(ws, 3 + i, 1, text)

    r = 4 + len(notes)
    heading(ws, r, ["Run", "Results", "Investment case"])
    run_rows = {}
    for i, run in enumerate(every):
        row = r + 1 + i
        put(ws, row, 1, f"{run.name} (base)" if run is base else run.name)
        put(ws, row, 2, "current" if run is base else _results_text(run))
        put(ws, row, 3, ("none: run the investment case" if base.ic_report is None else
                         "out of date: its figures are not shown"
                         if (base.case_state or {}).get("stale") else "current")
                if run is base else _case_text(run))
        run_rows[run.name] = row

    header = r + len(every) + 3
    first_val, last_val = 2, 1 + len(every)
    first_delta = last_val + 1
    heading(ws, header, ["KPI", *[run.name for run in every],
                         *[f"Δ {run.name} − base" for run in runs]])
    rows = {}
    row = header
    for label, vals in _rows(base, runs, scen.base_headline):
        row += 1
        rows[label] = row
        put(ws, row, 1, label)
        for j, v in enumerate(vals):
            put(ws, row, first_val + j, v)
        base_ref = f"{get_column_letter(first_val)}{row}"
        for j, run in enumerate(runs):
            col = first_delta + j
            if not run.reasons and _is_num(vals[0]) and _is_num(vals[1 + j]):
                formula(ws, row, col, f"={get_column_letter(first_val + 1 + j)}{row}-{base_ref}")
            else:
                put(ws, row, col, None)

    made = []
    anchor = f"{get_column_letter(first_delta + len(runs) + 1)}{header}"
    cap = rows.get(next((k for k in rows if k.startswith("Capex, annualised")
                         and k.endswith(f", {HORIZON}")), ""))
    if cap is not None and rows.get(next((k for k in rows if k.startswith("Opex (")
                                          and k.endswith(f", {HORIZON}")), "")) == cap + 1:
        made.append(charts.bar(ws, title="Capex (annualised) and opex over the horizon, by run",
                               stacked=True, from_rows=True, data=(1, cap, last_val, cap + 1),
                               cats=(first_val, header, last_val, header), anchor=anchor,
                               y_title="M EUR"))
    lcoe = rows.get(next((k for k in rows if k.startswith("System LCOE")
                          and k.endswith(f", {HORIZON}")), ""))
    if lcoe is not None:
        made.append(charts.bar(ws, title="System LCOE over the horizon, by run", from_rows=True,
                               data=(1, lcoe, last_val, lcoe),
                               cats=(first_val, header, last_val, header),
                               anchor=f"{get_column_letter(first_delta + len(runs) + 1)}{header + 20}",
                               y_title="EUR/MWh"))
    width(ws, {"A": 60, "B": 22, "C": 40})
    return {"header": header, "rows": rows, "run_rows": run_rows, "first_val": first_val,
            "first_delta": first_delta, "charts": len(made)}
