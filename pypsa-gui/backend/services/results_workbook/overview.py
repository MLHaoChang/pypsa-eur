"""
The Overview sheet: the run's investment-grade KPIs (owner decision RW-KPI-1).

One column per investment period and a Horizon column, from the optimisation:

* capex: overnight (`cost_breakdown.capex_lifetime`, the present value of the
  upfront investment; horizon only) and annualised (Σ over carriers of
  `economics_by_carrier.capex_meur`, which carries fixed O&M as the LP's
  periodized cost does);
* opex (Σ `opex_meur`) and its parts: fixed O&M (`cost_breakdown.fom`, inside
  the annualised capex), variable (`gen_cost_meur`), storage charging,
  curtailment and lost load;
* fuel: PyPSA's marginal cost does not separate fuel from variable O&M, so the
  row is `not_established` rather than a guessed split;
* total system cost (`cost_breakdown.total`, which reconciles with the LP
  objective), energy served (load less lost load, energy weighting) and the
  system LCOE as a live formula over those two cells;
* LCOE per carrier and period (`economics_by_carrier.lcoe_eur_per_mwh`).

Then the investment-case block, read from the stored report
(`finance.report.load_ic_report`): NPV, IRR (project and equity, pre and post
tax), payback, DSCR, financial LCOE and debt size. Without a report every row
is `not_established` with "run the investment case"; the export never runs it.
With one, the project post-tax cash flow is written below with live discount
formulas on a rate cell (the WACC) and the NPV cell is `=CF0+NPV(rate,CF1:CFn)`
(`finance.metrics.npv`'s convention: year 0 undiscounted).

Charts (native, from this sheet's cells): capex vs opex stacked by period, LCOE
by carrier by period, and the yearly cash flow with its cumulative discounted
sum when the case exists.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from openpyxl.utils import get_column_letter

from services.results_workbook import charts
from services.results_workbook.cells import NOT_ESTABLISHED, formula, heading, put, width

__all__ = ["CASE_ROWS", "OverviewFigures", "Line", "RUN_THE_CASE", "TITLE",
           "overview_figures", "write_overview"]

TITLE = "Overview"
RUN_THE_CASE = "run the investment case"
_HEADER_ROW = 4


@dataclass
class Line:
    key: str
    label: str
    unit: str
    total: float | None
    by_period: dict[str, float | None]
    source: str
    note: str | None = None


@dataclass
class OverviewFigures:
    periods: list[str]
    lines: list[Line]
    # carrier → (total, {period: value}) of the LCOE, EUR/MWh.
    lcoe_by_carrier: dict[str, tuple[float | None, dict[str, float | None]]]
    notes: list[str] = field(default_factory=list)

    def line(self, key: str) -> Line:
        return next(x for x in self.lines if x.key == key)


# ── figures ──────────────────────────────────────────────────────────────

def _finite(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _econ_sum(by_carrier: dict, key: str, periods: list[str]) -> tuple[float | None, dict]:
    total = 0.0
    pp = {p: 0.0 for p in periods}
    for v in by_carrier.values():
        cell = v.get(key) or {}
        t = _finite(cell.get("total"))
        if t is None:
            return None, {p: None for p in periods}
        total += t
        for p in periods:
            x = _finite((cell.get("by_period") or {}).get(p, 0.0))
            pp[p] = None if x is None or pp[p] is None else pp[p] + x
    return total, pp


def _energy_served(n, lost_load: dict | None, result_df) -> tuple[float | None, dict[str, float]]:
    """MWh of load served: Σ load × energy weight, less the lost-load capture."""
    import pandas as pd

    from services.compare.support import _per_period_groupby
    from services.period_utils import snapshot_weights

    sns = n.snapshots
    is_multi = isinstance(sns, pd.MultiIndex)
    w = snapshot_weights(n, "generators")
    p = result_df(n, "loads_t", "p")
    if p is None or getattr(p, "empty", True):
        return None, {}
    served = p.reindex(sns).fillna(0.0).astype(float).sum(axis=1)
    ll = (lost_load or {}).get("lost_load_t")
    if ll is not None and not getattr(ll, "empty", True):
        served = served - ll.reindex(sns).fillna(0.0).astype(float).sum(axis=1)
    weighted = served.mul(w, axis=0)
    return float(weighted.sum()), _per_period_groupby(weighted, sns, is_multi)


def overview_figures(n, cfg, lost_load: dict | None, result_df) -> OverviewFigures:
    """The Overview's numbers, each from the service the Results tabs read."""
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.economics_by_carrier import compute_economics_by_carrier

    econ = compute_economics_by_carrier(n, cfg, lost_load, result_df=result_df)
    cb = compute_cost_breakdown(n, cfg) or {}
    notes: list[str] = []
    by_carrier = econ.get("by_carrier") or {}
    if "error" in econ or not econ.get("available"):
        notes.append("The per-carrier economics could not be computed for this run; "
                     "its rows are not_established.")
        by_carrier = {}
    periods = sorted({p for v in by_carrier.values()
                      for p in ((v.get("capex_meur") or {}).get("by_period") or {})}, key=int)
    if not periods:
        periods = [str(r["period"]) for r in cb.get("by_period") or []]
    econ_ok = bool(by_carrier)

    def econ_line(key, label, field_, note=None):
        if not econ_ok:
            return Line(key, label, "M EUR", None, {p: None for p in periods},
                        "economics_by_carrier", note)
        t, pp = _econ_sum(by_carrier, field_, periods)
        return Line(key, label, "M EUR", t, pp, f"economics_by_carrier.{field_} (Σ carriers)", note)

    cb_pp = {str(r["period"]): r for r in cb.get("by_period") or []}

    def cb_line(key, label, field_, note=None):
        if not cb:
            return Line(key, label, "M EUR", None, {p: None for p in periods},
                        "cost_breakdown", note)
        t = _finite(cb.get(field_))
        pp = {p: (None if p not in cb_pp or _finite(cb_pp[p].get(field_)) is None
                  else cb_pp[p][field_] / 1e6) for p in periods}
        return Line(key, label, "M EUR", None if t is None else t / 1e6, pp,
                    f"cost_breakdown.{field_}", note)

    overnight = _finite(cb.get("capex_lifetime")) if cb else None
    energy_mwh, energy_pp = _energy_served(n, lost_load, result_df)
    lines = [
        Line("capex_overnight", "Capex, overnight (present value of the upfront investment)",
             "M EUR", None if overnight is None else overnight / 1e6, {p: None for p in periods},
             "cost_breakdown.capex_lifetime",
             "horizon only: the upfront cost is not split by period"
             if overnight is not None else
             "an upfront cost could not be resolved for at least one component class"),
        econ_line("capex_annualised", "Capex, annualised (incl. fixed O&M)", "capex_meur"),
        econ_line("opex", "Opex", "opex_meur",
                  "variable + storage charging + curtailment + lost load"),
        econ_line("opex_variable", "  of which variable opex (marginal cost)", "gen_cost_meur"),
        econ_line("opex_storage_charging", "  of which storage charging", "storage_charge_cost_meur"),
        econ_line("opex_curtailment", "  of which curtailment", "curtailment_cost_meur"),
        econ_line("opex_lost_load", "  of which lost load", "lost_load_cost_meur"),
        cb_line("fixed_om", "Fixed O&M (already inside annualised capex, not in opex)", "fom"),
        Line("fuel", "Fuel", "M EUR", None, {p: None for p in periods}, "not separable",
             "PyPSA's marginal cost does not separate fuel from variable O&M"),
        cb_line("system_cost", "Total system cost", "total",
                "annualised capex + opex as the LP objective counts them"),
        Line("energy_served", "Energy served", "GWh",
             None if energy_mwh is None else energy_mwh / 1e3,
             {p: (energy_pp[p] / 1e3 if p in energy_pp else None) for p in periods},
             "load less lost load, energy weighting"),
        # The value is a formula over the two cells above (write_overview).
        Line("system_lcoe", "System LCOE", "EUR/MWh", None, {p: None for p in periods},
             "total system cost / energy served (live formula)"),
    ]
    lcoe: dict[str, tuple[float | None, dict[str, float | None]]] = {}
    for carrier in sorted(by_carrier):
        cell = by_carrier[carrier].get("lcoe_eur_per_mwh") or {}
        disp = _finite((by_carrier[carrier].get("dispatch_gwh") or {}).get("total"))
        if not disp:
            continue  # a carrier that served no energy has no LCOE row
        lcoe[carrier] = (_finite(cell.get("total")),
                         {p: _finite((cell.get("by_period") or {}).get(p)) for p in periods})
    return OverviewFigures(periods, lines, lcoe, notes)


# ── the investment-case block ────────────────────────────────────────────

def _payload(report, section: str) -> dict:
    st = report.sections.get(section) if report is not None else None
    return (st.payload or {}) if st is not None else {}


#: (key, label, unit, reader(report) -> value)
CASE_ROWS: tuple[tuple[str, str, str, Any], ...] = (
    ("npv_project_post_tax", "NPV, project post-tax at the WACC", "EUR",
     lambda r: r.npv_at_wacc),
    ("npv_project_pre_tax", "NPV, project pre-tax at the WACC", "EUR",
     lambda r: _payload(r, "project").get("project_pre_tax_npv")),
    ("irr_project_pre_tax", "IRR, project pre-tax", "per unit", lambda r: r.project_irr_pre_tax),
    ("irr_project_post_tax", "IRR, project post-tax", "per unit", lambda r: r.project_irr_post_tax),
    ("irr_equity_pre_tax", "IRR, equity pre-tax", "per unit",
     lambda r: _payload(r, "project").get("equity_pre_tax_irr")),
    ("irr_equity_post_tax", "IRR, equity post-tax", "per unit",
     lambda r: _payload(r, "project").get("equity_post_tax_irr")),
    ("payback_years", "Payback (equity post-tax cash)", "years",
     lambda r: _payload(r, "project").get("payback_years")),
    ("min_dscr", "DSCR, minimum", "ratio", lambda r: r.min_dscr),
    ("avg_dscr", "DSCR, average", "ratio", lambda r: r.avg_dscr),
    ("lcoe_financial", "LCOE, financial (finance-consistent)", "EUR/MWh",
     lambda r: r.lcoe_finance_consistent_eur_per_mwh),
    ("debt_size", "Debt size", "EUR", lambda r: _payload(r, "debt").get("amount")),
)


def _cash_flow(report) -> tuple[list[int], list[float], float] | None:
    """(years, project post-tax cash, WACC) when all are established."""
    p = _payload(report, "project")
    years = p.get("years") or []
    cash = (p.get("cash") or {}).get("project_post_tax")
    rate = _finite(p.get("wacc_nominal"))
    if not years or not cash or rate is None or len(cash) != len(years):
        return None
    vals = [_finite(v) for v in cash]
    if any(v is None for v in vals):
        return None
    return [int(y) for y in years], vals, rate


# ── writing ──────────────────────────────────────────────────────────────

def write_overview(ws, figs: OverviewFigures, report) -> dict[str, Any]:
    """Write the sheet; returns the cell map the tests and About read."""
    ws.title = TITLE
    P = figs.periods
    ncol = 1 + len(P) + 1
    horizon_col = ncol
    unit_col, src_col = ncol + 1, ncol + 2
    heading(ws, 1, ["Overview: investment-grade KPIs over the simulation horizon"])
    put(ws, 2, 1, "Costs in M EUR as the optimisation counts them (period columns: the "
                  "period's years; Horizon: their sum). not_established: the figure could "
                  "not be computed from this run, and no number stands in for it.")
    heading(ws, _HEADER_ROW, ["Metric"] + P + ["Horizon", "Unit", "Source / note"])
    rows: dict[str, int] = {}
    r = _HEADER_ROW + 1
    for ln in figs.lines:
        rows[ln.key] = r
        put(ws, r, 1, ln.label)
        for j, p in enumerate(P, start=2):
            put(ws, r, j, ln.by_period.get(p))
        put(ws, r, horizon_col, ln.total)
        put(ws, r, unit_col, ln.unit)
        put(ws, r, src_col, ln.source if not ln.note else f"{ln.source}: {ln.note}")
        r += 1
    # System LCOE: M EUR / GWh × 1000 = EUR/MWh, live over the two cells above.
    lr, cr, er = rows["system_lcoe"], rows["system_cost"], rows["energy_served"]
    for j in range(2, horizon_col + 1):
        L = get_column_letter(j)
        c, e = ws.cell(cr, j).value, ws.cell(er, j).value
        if isinstance(c, (int, float)) and isinstance(e, (int, float)) and e > 0:
            formula(ws, lr, j, f"={L}{cr}*1000/{L}{er}")
        else:
            put(ws, lr, j, None)
    for note in figs.notes:
        put(ws, r, 1, note)
        r += 1

    # LCOE by carrier.
    r += 1
    put(ws, r, 1, "LCOE by carrier (EUR/MWh; source economics_by_carrier.lcoe_eur_per_mwh)")
    r += 1
    lcoe_header = r
    heading(ws, r, ["Carrier"] + P + ["Horizon"])
    for carrier, (total, pp) in figs.lcoe_by_carrier.items():
        r += 1
        put(ws, r, 1, carrier)
        for j, p in enumerate(P, start=2):
            put(ws, r, j, pp.get(p))
        put(ws, r, horizon_col, total)
    lcoe_last = r

    # Investment case.
    r += 2
    put(ws, r, 1, "Investment case")
    r += 1
    heading(ws, r, ["KPI", "Value", "Unit", "Source / note"])
    case_rows: dict[str, int] = {}
    src = (f"investment case report {report.case_id}" if report is not None else None)
    for key, label, unit, read in CASE_ROWS:
        r += 1
        case_rows[key] = r
        put(ws, r, 1, label)
        put(ws, r, 3, unit)
        if report is None:
            put(ws, r, 2, None)
            put(ws, r, 4, RUN_THE_CASE)
            continue
        value = _finite(read(report))
        put(ws, r, 2, value)
        put(ws, r, 4, src if value is not None
            else f"{src}: not established by the case (see Case About)")

    cash: dict[str, Any] = {}
    flow = _cash_flow(report) if report is not None else None
    if report is not None and flow is None:
        r += 2
        put(ws, r, 1, "Cash flow: the case's project post-tax cash or WACC is not "
                      "established, so no live NPV formula is written.")
    if flow is not None:
        years, vals, rate = flow
        r += 2
        put(ws, r, 1, "Discount rate (WACC, nominal)")
        put(ws, r, 2, rate)
        put(ws, r, 3, "edit this cell: the discount factors and the NPV follow it")
        rate_cell = f"$B${r}"
        r += 1
        hdr = r
        heading(ws, r, ["Year", "t", "Project post-tax cash (EUR)", "Discount factor",
                        "Discounted cash flow", "Cumulative discounted"])
        first = r + 1
        for i, (y, v) in enumerate(zip(years, vals)):
            r = first + i
            ws.cell(r, 1).value = y
            ws.cell(r, 2).value = i
            ws.cell(r, 3).value = v
            formula(ws, r, 4, f"=1/(1+{rate_cell})^B{r}")
            formula(ws, r, 5, f"=C{r}*D{r}")
            formula(ws, r, 6, f"=E{r}" if i == 0 else f"=F{r - 1}+E{r}")
        last = r
        npv = f"=C{first}" + (f"+NPV({rate_cell},C{first + 1}:C{last})" if last > first else "")
        r += 2
        put(ws, r, 1, "NPV (live formula)")
        formula(ws, r, 2, npv)
        npv_row = r
        r += 1
        put(ws, r, 1, "Check: sum of discounted cash flows")
        formula(ws, r, 2, f"=SUM(E{first}:E{last})")
        r += 1
        put(ws, r, 1, "NPV in the stored report")
        put(ws, r, 2, _finite(report.npv_at_wacc))
        put(ws, r, 3, "static: does not move when the rate or a cash flow is edited")
        # The KPI row reads the live NPV.
        formula(ws, case_rows["npv_project_post_tax"], 2, f"=B{npv_row}")
        put(ws, case_rows["npv_project_post_tax"], 4,
            f"{src}: live formula over the cash flow below")
        cash = {"header": hdr, "first": first, "last": last, "npv_row": npv_row,
                "rate_cell": rate_cell}

    # Charts, to the right of the tables.
    anchor_col = get_column_letter(src_col + 2)
    data_last = 1 + len(P) if P else horizon_col
    data_first_col = 2 if P else horizon_col
    cap, op = rows["capex_annualised"], rows["opex"]
    made = [charts.bar(ws, title="Capex (annualised) vs opex by period", stacked=True,
                       from_rows=True, data=(1, cap, data_last, op),
                       cats=(data_first_col, _HEADER_ROW, data_last, _HEADER_ROW),
                       anchor=f"{anchor_col}{_HEADER_ROW}", y_title="M EUR")]
    if lcoe_last > lcoe_header:
        made.append(charts.bar(ws, title="LCOE by carrier by period",
                               data=(data_first_col, lcoe_header, data_last, lcoe_last),
                               cats=(1, lcoe_header + 1, 1, lcoe_last),
                               anchor=f"{anchor_col}{_HEADER_ROW + 20}", y_title="EUR/MWh"))
    if cash:
        made.append(charts.bar_line(ws, title="Project post-tax cash flow and cumulative NPV",
                                    bars=(3, cash["header"], 3, cash["last"]),
                                    line_data=(6, cash["header"], 6, cash["last"]),
                                    cats=(1, cash["first"], 1, cash["last"]),
                                    anchor=f"{anchor_col}{_HEADER_ROW + 40}",
                                    y_title="EUR", line_title="cumulative discounted, EUR"))
    width(ws, {"A": 52, get_column_letter(src_col): 60})
    return {"rows": rows, "horizon_col": horizon_col, "periods": P,
            "lcoe": (lcoe_header, lcoe_last), "case_rows": case_rows, "cash": cash,
            "charts": len(made), "not_established": NOT_ESTABLISHED}
