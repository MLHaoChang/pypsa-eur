"""
The reliability result views' sheets (package 02, slice R3): Adequacy and FMEA.

Each builder reads a run's data from the same function its tab's route calls,
once per run (`ctx.runs`, the base first; a run that is not comparable with
the base gets no data), over that run's own solver state and network (the
base's are the active ones), and hands the payloads to `views.write_view`:

* Adequacy: `adequacy.report.adequacy_payload` (`GET /results/adequacy`), the
  AdequacyReport the last target-constrained solve stashed: the energy target
  and its achieved ENS per investment period (the horizon is their sum), by
  zone, and the reserve margin per period.
* FMEA: `adequacy.copt_endpoint.build_fmea_modes_payload`
  (`GET /results/fmea_modes`), every computed failure-mode row. It has no
  per-period split. Each row's criticality and severity are labelled by their
  own basis (`_basis`): the row's `criticality_basis` when it has one (ticket
  30 adds it), else by class: A and B are per study horizon until ticket 30
  lands, whatever the route's field names say; C is per year (frequency ×
  the damage of one event). No figure mixes two bases.

Neither reads a project's files: a scenario run's state and network are its
saved context, which `gather_scenarios` loaded after resolving its project.
"""
from __future__ import annotations

import contextlib

import pandas as pd

from services.results_workbook.views import HORIZON, Key, Table, ViewData
from services.results_workbook.views_grid import NO_PERIODS, _is_number, _records, _scalar
from services.results_workbook.views_network import _view

__all__: list[str] = []

#: Ticket 30 (condition 6): class A and B rows' `criticality_eur_per_year`
#: and `severity_eur` are over the study horizon, not per year, until it lands.
PER_HORIZON = "per study horizon (ticket 30)"
PER_YEAR = "per year"
PER_EVENT = "per event"
CRITICALITY = f"criticality, EUR {PER_HORIZON}"
SEVERITY = f"severity, EUR {PER_HORIZON}"
CRITICALITY_PER_YEAR = f"criticality, EUR {PER_YEAR}"
SEVERITY_PER_EVENT = f"severity, EUR {PER_EVENT}"
DELTA_EUE = "ΔEUE, MWh"
OCCURRENCE = "occurrence per year"


def _state(run) -> dict:
    """The run's solver state: its saved context's, or the active one (the base)."""
    if run.context is not None:
        return run.context.solver_state
    from services.pypsa_service import PyPSAService
    return PyPSAService.get_solver_state()


def _num(v) -> float | None:
    return float(v) if _is_number(v) else None


def _horizon_note(n) -> str:
    """The study horizon's length, so a reader can scale per-horizon figures."""
    from services.adequacy.metrics import horizon_years
    years = horizon_years(n)
    return (f"Study horizon: {years * 8760:,.6g} h of modelled time "
            f"({years:.6g} years; the snapshot weights' sum).")


# ── Adequacy ─────────────────────────────────────────────────────────────

def _adequacy(run) -> ViewData | None:
    from services.adequacy.report import adequacy_payload
    p = adequacy_payload(_state(run), run.solved_settings or (lambda: None))
    if p is None:  # the route's 204: nothing solved
        return None
    if not p.get("configured"):
        return ViewData(
            tables=[Table("Report", "", pd.DataFrame([{k: _scalar(v) for k, v in p.items()}]),
                          labels=len(p))],
            notes=[f"{run.name}: solved without a reliability target: not modelled "
                   "(the tab says so)."])
    target = p.get("target") or {}
    system = target.get("system") or {}
    metrics = p.get("metrics") or {}
    cost = p.get("cost") or {}
    energy = p.get("energy") or {}
    by_period = system.get("by_period") or []
    margin = p.get("reserve_margin") or {}
    keys: list[Key] = []
    if target.get("energy_target_set"):
        for label, field in (("ENS cap (the target)", "cap_mwh"),
                             ("Achieved ENS against the target", "achieved_ens_mwh")):
            # The system figure is the sum of its periods' (`build_adequacy_report`).
            by = {str(r.get("period")): _num(r.get(field)) for r in by_period}
            keys.append(Key(label, "MWh", {**by, HORIZON: _num(system.get(field))}))
    for label, unit, v in (
            ("Energy not served", "MWh", metrics.get("ens_mwh")),
            ("Shed hours", "h", metrics.get("shed_hours")),
            ("Involuntary lost load", "MWh", energy.get("involuntary_mwh")),
            ("Demand response", "MWh", energy.get("demand_response_mwh")),
            # The tab's own basis: a single period's yearly cost, or the
            # discounted cost over the plan; never read as the other.
            (("System cost excluding shed, discounted over the plan", "EUR")
             if cost.get("period_basis") == "npv_multi_period"
             else ("System cost excluding shed, per year", "EUR/a"))
            + (cost.get("total_system_cost_eur"),),
            ("VoLL", "EUR/MWh", ((p.get("inputs") or {}).get("voll") or {}).get(
                "default_eur_per_mwh"))):
        keys.append(Key(label, unit, {HORIZON: _num(v)}))
    # MW and ratios: per period only, never summed over the horizon.
    for label, unit, field in (("Firm capacity at peak", "MW", "firm_mw"),
                               ("Required capacity at peak", "MW", "required_mw"),
                               ("Reserve margin achieved", "fraction", "margin_achieved")):
        rows = margin.get("by_period") or []
        if rows:
            keys.append(Key(label, unit, {str(r.get("period")): _num(r.get(field)) for r in rows}))
    status = {"engine": p.get("engine"), "fidelity": p.get("fidelity"),
              "binding standard": target.get("binding"),
              "time basis": metrics.get("time_basis"),
              "cost basis": cost.get("period_basis")}
    tables = [t for t in (
        _records("ENS target by period", "MWh", by_period,
                 ["period", "binding", "cap_mwh", "achieved_ens_mwh"]),
        _records("ENS target by zone", "MWh (over the horizon)", target.get("zones") or [],
                 ["zone", "binding", "cap_mwh", "achieved_ens_mwh"]),
        _records("Reserve margin by period", "MW", margin.get("by_period") or [],
                 ["period", "met", "binding", "peak_mw", "required_mw", "firm_mw",
                  "margin_achieved", "max_achievable_mw", "n_peak_hours"]),
        Table("Report", "", pd.DataFrame([{k: _scalar(v) for k, v in status.items()}]),
              labels=len(status)),
    ) if t is not None]
    notes = ["Adequacy is the last target-constrained solve's AdequacyReport (engine "
             "lp_proxy: a deterministic proxy, not comparable to a statutory standard). "
             "ENS per period sums to the horizon; MW and the margin are per period only.",
             _horizon_note(run.network)]
    if cost.get("period_basis") != "npv_multi_period":
        notes.append("System cost excluding shed is the objective less the shed cost: "
                     "annualised capex plus the horizon's opex. It is per year only when "
                     "the horizon is a full year (8,760 h); see its length above.")
    return ViewData(keys=keys, tables=tables, notes=notes)


# ── FMEA ─────────────────────────────────────────────────────────────────

_FMEA_LABELS = ["mode_id", "name", "component_class", "failure_class", "occurrence_basis",
                "rate_source", "in_metric_scope", "engine", "fidelity", "zero_reason", "note",
                "criticality basis", "severity basis"]
_FMEA_VALUES = {"occurrence_per_year": OCCURRENCE, "delta_eue_mwh": DELTA_EUE}
#: A row's basis → (its criticality's column, its severity's column, the
#: criticality basis as said, the severity basis as said).
_BASES = {
    "per_horizon": (CRITICALITY, SEVERITY, PER_HORIZON, PER_HORIZON),
    "annualised": (CRITICALITY_PER_YEAR, SEVERITY_PER_EVENT,
                   f"{PER_YEAR} (annualised)", f"{PER_EVENT} (criticality ÷ occurrence)"),
    "per_event": (CRITICALITY_PER_YEAR, SEVERITY_PER_EVENT,
                  f"{PER_YEAR} (frequency × severity)", PER_EVENT),
}
#: The classes whose "highest criticality" is one key.
_FAMILIES = (("classes A/B", ("A", "B")), ("class C", ("C",)))


def _basis(m: dict) -> str:
    """The row's own basis: its `criticality_basis` (ticket 30), else by class.

    Without the field, A (`copt.attribute_criticality`) and B (`sweep`) are
    ΔEUE × VoLL over the horizon; C (`stress`) is frequency per year × the
    damage of one event, so already per year with a per-event severity.
    """
    b = m.get("criticality_basis")
    if b:
        return str(b)
    return "per_event" if m.get("failure_class") == "C" else "per_horizon"


def _fmea_row(m: dict) -> dict:
    b = _basis(m)
    # A basis this sheet does not know keeps its figures under a column that
    # names no unit beyond EUR; its basis column says what it is.
    crit_col, sev_col, crit_says, sev_says = _BASES.get(
        b, ("criticality, EUR (see its basis)", "severity, EUR (see its basis)", b, b))
    return {**{c: _scalar(m.get(c)) for c in _FMEA_LABELS[:-2]},
            "criticality basis": crit_says, "severity basis": sev_says,
            **{label: _num(m.get(field)) for field, label in _FMEA_VALUES.items()},
            crit_col: _num(m.get("criticality_eur_per_year")),
            sev_col: _num(m.get("severity_eur"))}


def _highest(modes: list[dict]) -> list[Key]:
    """One key per class family, in its rows' unit; None when they mix bases."""
    keys = []
    for name, classes in _FAMILIES:
        rows = [m for m in modes if m.get("failure_class") in classes]
        if not rows:
            continue
        bases = {_basis(m) for m in rows}
        crit = [c for m in rows if (c := _num(m.get("criticality_eur_per_year"))) is not None]
        if len(bases) > 1:
            keys.append(Key(f"Highest criticality, {name}", "EUR (rows on different bases)",
                            {HORIZON: None}))
            continue
        (b,) = bases
        unit = f"EUR {_BASES[b][2]}" if b in _BASES else f"EUR ({b})"
        keys.append(Key(f"Highest criticality, {name}", unit,
                        {HORIZON: max(crit) if crit else None}))
    return keys


def _fmea(run) -> ViewData | None:
    from services.adequacy.copt_endpoint import build_fmea_modes_payload
    if run.context is None:
        from services.pypsa_service import PyPSAService
        get_lock = PyPSAService.get_lock
    else:  # a scenario's network is its own private copy: no lock to take
        get_lock = contextlib.nullcontext
    state = _state(run)
    # ValueError is the route's 422: it reaches About as an error.
    p = build_fmea_modes_payload(run.network, run.cfg, sweep_record=state.get("fmea_sweep"),
                                 get_lock=get_lock)
    if p is None:  # the route's 204: no failure mode from any source
        return None
    modes = p.get("per_mode") or []
    rows = [_fmea_row(m) for m in modes]
    values = [*_FMEA_VALUES.values(), CRITICALITY, CRITICALITY_PER_YEAR, SEVERITY,
              SEVERITY_PER_EVENT]
    values += [c for r in rows for c in r if c not in _FMEA_LABELS and c not in values]
    frame = pd.DataFrame(rows, columns=[*_FMEA_LABELS, *values])
    classes = [m.get("failure_class") for m in modes]
    delta = [_num(m.get("delta_eue_mwh")) for m in modes]
    keys = [Key("Failure modes", "count", {HORIZON: float(len(modes))})]
    keys += [Key(f"Class {c} failure modes", "count", {HORIZON: float(classes.count(c))})
             for c in ("A", "B", "C") if c in classes]
    # No total, and no highest over every row: class A and the sweep's B/C
    # rows can describe the same outage, and their bases can differ.
    keys += [
        *_highest(modes),
        Key("Highest ΔEUE", "MWh",
            {HORIZON: max(delta) if delta and None not in delta else None}),
        Key("VoLL", "EUR/MWh", {HORIZON: _num(p.get("voll_eur_per_mwh"))}),
    ]
    notes = [NO_PERIODS,
             "Each row's criticality and severity sit in the column of their own basis "
             "(its basis columns say which), whatever the route's field names say: class "
             f"A and B rows are {PER_HORIZON} until ticket 30 lands; class C rows are "
             "frequency per year × the damage of one event. Rows are the route's order; "
             "the worksheet's expert class-D rows are not here (see the tab).",
             _horizon_note(run.network)]
    if p.get("sweep_status"):
        notes.append(f"Contingency sweep: {p['sweep_status']}.")
    if p.get("sweep_error"):
        notes.append(f"Sweep error: {p['sweep_error']}.")
    if p.get("sweep_base_restored") is False:
        notes.append("The sweep's closing base re-solve failed: the network is still on its "
                     "last contingency.")
    return ViewData(keys=keys, tables=[Table("Failure modes", "", frame,
                                             labels=len(_FMEA_LABELS))], notes=notes)


_view("adequacy", "Adequacy", _adequacy)
_view("fmea", "FMEA", _fmea)
