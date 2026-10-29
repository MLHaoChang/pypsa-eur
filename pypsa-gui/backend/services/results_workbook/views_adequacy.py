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
  per-period split. Until ticket 30 lands its criticality and severity are
  not per year, whatever the route's field names say: every label here calls
  them "per study horizon (ticket 30)".

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

#: Ticket 30 (condition 6): the route's `criticality_eur_per_year` and
#: `severity_eur` are over the study horizon, not per year, until it lands.
PER_HORIZON = "per study horizon (ticket 30)"
CRITICALITY = f"criticality, EUR {PER_HORIZON}"
SEVERITY = f"severity, EUR {PER_HORIZON}"
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
             "ENS per period sums to the horizon; MW and the margin are per period only."]
    return ViewData(keys=keys, tables=tables, notes=notes)


# ── FMEA ─────────────────────────────────────────────────────────────────

_FMEA_LABELS = ["mode_id", "name", "component_class", "failure_class", "occurrence_basis",
                "rate_source", "in_metric_scope", "engine", "fidelity", "zero_reason", "note"]
_FMEA_VALUES = {"occurrence_per_year": OCCURRENCE, "criticality_eur_per_year": CRITICALITY,
                "severity_eur": SEVERITY, "delta_eue_mwh": DELTA_EUE}


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
    rows = [{**{c: _scalar(m.get(c)) for c in _FMEA_LABELS},
             **{label: _num(m.get(field)) for field, label in _FMEA_VALUES.items()}}
            for m in modes]
    frame = pd.DataFrame(rows, columns=[*_FMEA_LABELS, *_FMEA_VALUES.values()])
    classes = [m.get("failure_class") for m in modes]
    crit = [_num(m.get("criticality_eur_per_year")) for m in modes]
    delta = [_num(m.get("delta_eue_mwh")) for m in modes]
    keys = [Key("Failure modes", "count", {HORIZON: float(len(modes))})]
    keys += [Key(f"Class {c} failure modes", "count", {HORIZON: float(classes.count(c))})
             for c in ("A", "B", "C") if c in classes]
    keys += [
        # The route sorts by criticality: the top row is the highest. No total:
        # class A and the sweep's B/C rows can describe the same outage.
        Key(f"Highest criticality ({PER_HORIZON})", "EUR", {HORIZON: crit[0] if crit else None}),
        Key("Highest ΔEUE", "MWh",
            {HORIZON: max(delta) if delta and None not in delta else None}),
        Key("VoLL", "EUR/MWh", {HORIZON: _num(p.get("voll_eur_per_mwh"))}),
    ]
    notes = [NO_PERIODS,
             f"Criticality and severity are {PER_HORIZON}, not per year, whatever the "
             "route's field names say. Rows are the route's order (highest criticality "
             "first); the worksheet's expert class-D rows are not here (see the tab)."]
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
