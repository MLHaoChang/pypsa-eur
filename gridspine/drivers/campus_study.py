"""The campus study as the backend sees it (plan C2).

A campus study is separate from the case39 year study. It runs no unit
commitment, no N-2 and no PSS/E handoff. It takes two inputs:

* a **solved capacity-expansion project** (``network.nc``), the assets and
  their hourly dispatch per investment period;
* a **campus description** (``gridspine.ingest.campus``), the electrical
  plant between those assets and the grid.

From these it prepares a run directory that the ranking (C3) and the sizing
(C4) read:

``campus.yaml``
    The campus exactly as used.
``campus_hourly.csv``, ``campus_pcc.csv``
    The hourly tables (``gridspine.schema.campus``).
``campus_manifest.json``
    Both inputs by sha256, the periods and the unit count. A later stage can
    then recognise a run whose project or campus has changed since. Also the
    study horizon (plan C14): ``period_weightings``, the project's
    investment-period weightings in years (null where it gives none),
    ``period_years``, the span of each period they give
    (``campus_invest.period_spans``: a missing one is the gap between
    periods; a single period spans one year), and ``period_years_source``.

``rank_campus`` then ranks the prepared hours (``gridspine.ranking.campus``).
It writes ``campus_metrics.csv`` and ``campus_selected.csv``, one row per
selected (period, hour), with its reasons joined by ``;``.

``island_frequency_campus`` runs the island frequency check (plan I6,
``gridspine.static.frequency``) on a ranked run with island data: E1 at every
hour of the ``seamless`` scenario on the island tables' steps
(``campus_freq_e1_hourly.csv``), and E1–E6 at the island critical hours, the
selected hours an island criterion picked (``campus_freq_events.csv``,
``campus_freq_losses.csv``, ``campus_freq_bridge.csv``,
``campus_freq_planned.csv``, ``campus_freq_ups.csv``), with a summary
(``campus_freq_summary.json``) that names the failing hours and the hours
the every-hour E1 fails but the ranking did not select. ``psse_campus``
writes the PSS/E handoff (plan I8, ``gridspine.handoff.campus_bundle``), one
bundle per island critical hour, under ``psse/``. ``cuts_campus`` then
derives I7's frequency cuts for the hub (``campus_freq_cuts.csv``,
``gridspine.static.frequency.propose_cuts``): the inertia, headroom and droop
gain each failing hour needs, for the user to apply (plan Q6).

``island_static_campus`` runs the island steady state and short circuit at
the same hours (plan I4, I5, I5b, ``gridspine.static.island_hours``): the
island flow after the gensets join (``campus_island_flow*.csv``), and the
fault levels, relays, unbalanced faults, earth reference and earth-fault
relays as the island forms and once it is steady (``campus_island_sc.csv``,
``campus_island_relays*.csv``, ``campus_island_sc_unbalanced.csv``,
``campus_island_earth*.csv``), with a summary
(``campus_island_summary.json``) of what fails.

``size_campus`` solves every selected hour, intact and transformer N-1
(``gridspine.static.campus_flow``). It writes the per-hour results
(``campus_lf_trafo.csv``, ``campus_lf_bus.csv`` and ``campus_lf_pcc.csv``,
with a ``case`` column) and the transformer sizing
(``campus_sizing_trafo.csv``). It also corrects every selected hour into
the PCC reactive band (``gridspine.static.campus_reactive``), writing
``campus_reactive.csv``, the compensation sizing
(``campus_sizing_compensation.csv``), and the requirement it applied
(``campus_requirement.json``).
- The band comes from the grid-code profile, or from the study's
  connection-agreement power factor.
- Its P_ref is the campus file's ``p_connection_mw``, or else the year's
  peak import. The requirement records which one was used.
Finally it runs IEC 60909 at every bus for each period, with the units
installed in that period energised, against any switchgear ratings
(``campus_short_circuit.csv``, ``gridspine.static.campus_sc``), and
assembles the PCC compliance report (``campus_compliance.csv``,
``gridspine.static.campus_compliance``) against the chosen profile.

``invest_campus`` then buys the electrical assets (plan C8,
``gridspine.static.campus_invest``): least cost from the asset library (the
shipped one, or a project's copy), every choice re-checked by AC load flow at
every selected hour and case. It recomputes part one's sizing from the
prepared and ranked run, with the same profile and power factor as
``size_campus``, so it needs ``campus_selected.csv`` but not the sizing
files. It writes:

``campus_investment.csv``
    One row per need and purchase: library id, kind, units, length, the
    period it is invested in, capex, opex and annualised cost, existing,
    status (``chosen``, ``kept``, ``not_needed``, ``unresolved``), the reason
    and the ``action`` (``buy``, ``add``, ``keep``, ``replace``; plan C13;
    ``renew``, plan C14). Units added next to an existing transformer group
    give two rows: the kept units (``keep``) and the new ones (``add``),
    whose reason names the units they join and any assumption (an unknown
    vector group). Plan C14: an asset whose life ends inside the horizon is
    bought again like for like (``renew`` rows), an existing one from its
    ``commissioning_year`` (or mid-life at the first period, assumed);
    ``purchase`` (0 for a kept existing asset), ``build_year``,
    ``service_to``, ``replaces`` and ``lineage`` tie the purchases of one
    asset together, and ``commissioned`` / ``commissioned_source`` say when
    an existing one went into service and how that is known.
``campus_cost.csv``
    The capex invested and the annualised electrical cost, per period; the
    capex is every purchase made in the period's span, and
    ``capex_replacement_eur`` the part of it that buys again (plan C14).
``campus_invested.yaml``
    The campus file with the chosen assets, valid for ``load_campus``.
``campus_compliance_invested.csv``
    The compliance as is and with the assets, the latter a re-solved AC
    result, plus the cable loading.
``campus_invest_history.csv``
    Every escalation: iteration, need, from, to, the failing check.
``campus_invest_dispatch.csv``
    The reactive dispatch per selected hour: inverters, STATCOMs, steps.
``campus_invest_scope.json``
    Who buys the PCC switchgear (``pcc_switchgear``), the rules a unit
    added next to existing transformers had to meet (``parallel``,
    ``ParallelRules.scope``: tolerances tagged assumed), who moves the
    PCC transformer's taps (``pcc_tap_control``, plan C15), and the study
    horizon (``horizon``, plan C14: the span of each period, where it came
    from, the horizon's end and the notes: assumed spans and commissioning
    years, overdue and unpriced replacements, and that replacements are
    reported, not ranked).
``campus_invest_taps.csv``
    The tap of every transformer with tap data, per selected hour and case
    of the re-check (plan C15, ``gridspine.static.campus_taps``): ``period,
    hour, case, transformer, group, tap_pos, ratio_pct, moved_by``. The
    study moves the on-load taps where a campus voltage is out of its band,
    per hour and per case (an N-1 case after the tap changers have acted,
    the Q dispatch held); ``moved_by`` is the change from the file's
    position, ``ratio_pct`` the ratio against the rated one. Header only
    when no transformer has tap data.

``invest_campus(..., method="milp")`` chooses the assets jointly instead
(plan C11, ``gridspine.static.campus_milp``), warm-started from the
least-cost pick, and writes the same files, plus:

``campus_milp_history.csv``
    One row per iteration of the loop: cost, AC feasibility, worst
    violation and linearisation error, Delta, beta, rho, accepted, slack.
``campus_milp_comparison.csv``
    Per need, C8's choice and cost against the MILP's.

``ASSET_LIBRARY_PATH`` (the shipped library) and ``load_asset_library`` are
re-exported, so that a backend keeping a project's own copy needs nothing
but this module.

``draft_from_project`` is the "generate" step. ``check_campus``,
``grid_code_profiles`` and ``SizingCriteria`` complete the seam the backend
uses, since pypsa-gui reaches gridspine through ``drivers`` and ``schema``
only. It drafts a campus from the
saved project, for the user to edit before ``prepare_campus``.

A study may be held to a project's own grid-code profile (plan C10): one
YAML file per profile in a directory, passed as ``extra_dirs`` to
``grid_code_profiles`` and as ``profile_dirs`` to ``size_campus``. The
grid-code functions the backend needs for its drafts (``validate_profile``,
``confirm_limit``, ``unconfirmed``, ``uncovered_kv_ranges``, ``load_grid_code``,
``PROFILE_ID``) are
re-exported here for the same reason.

Everything is validated before anything is written: the campus is built,
the tables are made and checked, and only then do the files land. Each file
is replaced atomically, so a refused campus leaves the run as it was.
"""
import dataclasses
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

import pandas as pd
import yaml

from gridspine.ingest.campus import Campus, build_campus
from gridspine.producers.campus import CampusDraft, campus_hourly, draft_campus, is_solved, period_weightings
from gridspine.producers.pypsa_nodal import load_solved_network
from gridspine.ranking.campus import campus_metrics, island_metrics, select_campus_hours
from gridspine.schema.campus import HOURLY_CSV, PCC_CSV, validate_hourly, validate_pcc
from gridspine.schema.contracts import ContractError
from gridspine.static.campus_flow import SizingCriteria, held_transformers, size_transformers, solve_cases
from gridspine.static.campus_reactive import ibr_q_capability, ibr_q_curve, reactive_need, requirement_from, size_compensation
from gridspine.static.campus_compliance import OPEN_TIE_CLAUSE, campus_compliance
from gridspine.static.campus_invest import ParallelRules, period_spans, select_assets
from gridspine.static.campus_milp import select_assets_milp
from gridspine.static.campus_sc import campus_fault_levels
from gridspine.static.campus_taps import PCC_TAP_CONTROL  # noqa: F401  (re-exported: the backend's seam)
from gridspine.templates.campus_assets import DEFAULT_PATH as ASSET_LIBRARY_PATH  # noqa: F401  (the backend's seam)
from gridspine.templates.campus_assets import load_asset_library
from gridspine.templates.grid_codes import (  # noqa: F401  (re-exported: the backend's seam)
    PROFILE_ID,
    SOURCES as GRID_CODE_SOURCES,
    confirm_limit,
    list_grid_codes,
    load_grid_code,
    reported_requirements,
    uncovered_kv_ranges,
    unconfirmed,
    validate_profile,
)

CAMPUS_YAML = "campus.yaml"
CAMPUS_MANIFEST = "campus_manifest.json"
METRICS_CSV = "campus_metrics.csv"
SELECTED_CSV = "campus_selected.csv"
DEFAULT_K = 3
LF_TRAFO_CSV = "campus_lf_trafo.csv"
LF_BUS_CSV = "campus_lf_bus.csv"
LF_PCC_CSV = "campus_lf_pcc.csv"
SIZING_TRAFO_CSV = "campus_sizing_trafo.csv"
REACTIVE_CSV = "campus_reactive.csv"
SIZING_COMP_CSV = "campus_sizing_compensation.csv"
REQUIREMENT_JSON = "campus_requirement.json"
SHORT_CIRCUIT_CSV = "campus_short_circuit.csv"
COMPLIANCE_CSV = "campus_compliance.csv"
#: IEEE 2800's IBR reactive capability, when the profile states it.
IBR_CAPABILITY_CSV = "campus_ibr_capability.csv"
IBR_Q_CURVE_CSV = "campus_ibr_q_curve.csv"
#: What the profile requires that a steady-state study cannot show.
REPORTED_REQUIREMENTS_JSON = "campus_reported_requirements.json"
INVESTMENT_CSV = "campus_investment.csv"
COST_CSV = "campus_cost.csv"
INVESTED_YAML = "campus_invested.yaml"
COMPLIANCE_INVESTED_CSV = "campus_compliance_invested.csv"
INVEST_HISTORY_CSV = "campus_invest_history.csv"
INVEST_DISPATCH_CSV = "campus_invest_dispatch.csv"
INVEST_SCOPE_JSON = "campus_invest_scope.json"
INVEST_TAPS_CSV = "campus_invest_taps.csv"
FREQ_E1_HOURLY_CSV = "campus_freq_e1_hourly.csv"
FREQ_EVENTS_CSV = "campus_freq_events.csv"
FREQ_LOSSES_CSV = "campus_freq_losses.csv"
FREQ_BRIDGE_CSV = "campus_freq_bridge.csv"
FREQ_PLANNED_CSV = "campus_freq_planned.csv"
FREQ_UPS_CSV = "campus_freq_ups.csv"
FREQ_SUMMARY_JSON = "campus_freq_summary.json"
FREQ_CUTS_CSV = "campus_freq_cuts.csv"
GFM_SETTINGS_JSON = "campus_gfm_settings.json"
#: How many processes the island checks ran on, by stage (``_record_workers``).
#: Kept out of ``CAMPUS_MANIFEST``, which is hashed as an input elsewhere:
#: the results do not depend on it.
WORKERS_JSON = "campus_workers.json"
PSSE_DIR = "psse"
#: ``island_static_campus``'s files, by ``IslandStatic`` table.
ISLAND_STATIC_CSV = {
    "flow": "campus_island_flow.csv",
    "flow_units": "campus_island_flow_units.csv",
    "flow_bus": "campus_island_flow_bus.csv",
    "flow_branch": "campus_island_flow_branch.csv",
    "sc": "campus_island_sc.csv",
    "relays": "campus_island_relays.csv",
    "relays_imax": "campus_island_relays_imax.csv",
    "unbalanced": "campus_island_sc_unbalanced.csv",
    "earth": "campus_island_earth.csv",
    "earth_relays": "campus_island_earth_relays.csv",
}
ISLAND_STATIC_SUMMARY_JSON = "campus_island_summary.json"
#: The keys of ``campus_freq_summary.json``, in order (plan I6c).
FREQ_SUMMARY_KEYS = ("label", "ledger", "scenarios", "island_hours", "every_hour_e1", "failing_hours",
                     "bridge_failing_hours", "planned_infeasible_hours", "e1_failing_hours",
                     "e1_failing_not_selected")
MILP_HISTORY_CSV = "campus_milp_history.csv"
MILP_COMPARISON_CSV = "campus_milp_comparison.csv"
METHODS = ("least_cost", "milp")
DEFAULT_PROFILE = "eu_rfg_dcc_ce"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def draft_from_project(network_path, island=None, f_hz=None) -> CampusDraft:
    """A campus draft from the saved, solved project at ``network_path``.
    An unsolved project is refused here. The draft itself needs no dispatch,
    but the study does, and the user should hear it now rather than at run
    time. ``island``, the project's validated island sidecar, is passed to
    ``draft_campus`` (campus island plan I1, I9b): the campus then carries
    its island data. Without it the draft is unchanged. ``f_hz`` is
    ``draft_campus``'s own parameter, passed through."""
    n = load_solved_network(network_path)
    if not is_solved(n):
        raise ContractError("the project is not solved; solve it and save, then draft the campus")
    return draft_campus(n, island=island, f_hz=f_hz)


def check_campus(spec: dict) -> None:
    """Refuse a campus description that does not build (ContractError)."""
    build_campus(spec)


def grid_code_profiles(extra_dirs=()) -> dict:
    """``{profile: title}`` a campus study can be held to: the shipped
    profiles, then the project's own in ``extra_dirs``."""
    return list_grid_codes(extra_dirs=extra_dirs)


def prepare_campus(run_dir, campus: dict, network_path) -> dict:
    """Validate ``campus`` against the project at ``network_path``, then write
    the run (see the module docstring). Returns a summary: ``periods``,
    ``units``, ``hours_per_period``."""
    network_path = Path(network_path)
    build_campus(campus)                                   # refuse before writing
    n = load_solved_network(network_path)
    hourly, pcc = campus_hourly(n, campus)
    periods = sorted(int(p) for p in pcc["period"].unique())
    weightings = period_weightings(n)
    spans, span_source = period_spans(periods, weightings)
    campus_text = yaml.safe_dump(campus, sort_keys=False)
    manifest = {
        "campus_sha256": hashlib.sha256(campus_text.encode()).hexdigest(),
        "network_path": str(network_path),
        "network_sha256": _sha256_file(network_path),
        "periods": periods,
        "units": int(hourly["unit_id"].nunique()),
        "hours_per_period": int(pcc.groupby("period").size().max()),
        "period_weightings": {str(p): weightings.get(p) for p in periods},
        "period_years": {str(p): w for p, w in spans.items()},
        "period_years_source": {str(p): src for p, src in span_source.items()},
    }
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(run_dir / CAMPUS_YAML, campus_text)
    _write_text_atomic(run_dir / HOURLY_CSV, hourly.to_csv(index=False))
    _write_text_atomic(run_dir / PCC_CSV, pcc.to_csv(index=False))
    _write_text_atomic(run_dir / CAMPUS_MANIFEST, json.dumps(manifest, indent=2))
    # The island tables (plan I3a) belong to the solve the run was prepared
    # from; whoever writes them does so after this, so an earlier run's go.
    from gridspine.schema.island import ISLAND_HOURLY_CSV, ISLAND_UNIT_HOURLY_CSV
    for name in (ISLAND_UNIT_HOURLY_CSV, ISLAND_HOURLY_CSV):
        (run_dir / name).unlink(missing_ok=True)
    return {"periods": periods, "units": manifest["units"], "hours_per_period": manifest["hours_per_period"]}


def _require(run_dir: Path, name: str) -> Path:
    path = run_dir / name
    if not path.is_file():
        raise ContractError(f"{path} is missing; prepare the campus study first")
    return path


def campus_tables(run_dir):
    """``(hourly, pcc)`` of a prepared run, validated."""
    run_dir = Path(run_dir)
    hourly = validate_hourly(pd.read_csv(_require(run_dir, HOURLY_CSV), dtype={"unit_id": str}))
    pcc = validate_pcc(pd.read_csv(_require(run_dir, PCC_CSV)))
    return hourly, pcc


def load_run_campus(run_dir) -> Campus:
    """The campus of a prepared run, built from its stored ``campus.yaml``."""
    return build_campus(yaml.safe_load(_require(Path(run_dir), CAMPUS_YAML).read_text()))


def _island_tables(run_dir: Path):
    """``(island_unit_hourly, island_hourly)`` of the run when the project's
    solve wrote them (plan I3a; written by pypsa-gui beside
    ``campus_hourly.csv``), else None. One without the other is refused."""
    from gridspine.schema.island import (
        ISLAND_HOURLY_CSV,
        ISLAND_UNIT_HOURLY_CSV,
        validate_island_hourly,
        validate_island_unit_hourly,
    )

    paths = (run_dir / ISLAND_UNIT_HOURLY_CSV, run_dir / ISLAND_HOURLY_CSV)
    have = [p.is_file() for p in paths]
    if not any(have):
        return None
    if not all(have):
        missing = [p.name for p, h in zip(paths, have) if not h]
        raise ContractError(f"the run has one island table without the other ({missing} missing)")
    return (validate_island_unit_hourly(pd.read_csv(paths[0], dtype={"unit_id": str})),
            validate_island_hourly(pd.read_csv(paths[1])))


def rank_campus(run_dir, k: int = DEFAULT_K) -> pd.DataFrame:
    """Rank the prepared run's hours and select the critical ones (k per
    criterion, per period), with the island criteria when the run has the
    island tables (plan I3b). Writes ``campus_metrics.csv`` and
    ``campus_selected.csv``; returns the selection."""
    run_dir = Path(run_dir)
    hourly, pcc = campus_tables(run_dir)
    spec = yaml.safe_load(_require(run_dir, CAMPUS_YAML).read_text())
    metrics = campus_metrics(hourly, pcc, spec)
    island = _island_tables(run_dir)
    if island is not None:
        # Plan I3b: the island criteria join the base ones in one top-k union.
        metrics = metrics.join(island_metrics(hourly, *island, spec), how="left").fillna(0.0)
    selection = select_campus_hours(metrics, k=k)
    _write_text_atomic(run_dir / METRICS_CSV, metrics.reset_index().to_csv(index=False))
    _write_text_atomic(run_dir / SELECTED_CSV,
                       selection.assign(reasons=selection["reasons"].map(";".join)).to_csv(index=False))
    return selection


def selected_hours(run_dir) -> pd.DataFrame:
    """The stored selection: ``period``, ``hour``, ``reasons`` (a list)."""
    df = pd.read_csv(_require(Path(run_dir), SELECTED_CSV))
    return df.assign(reasons=df["reasons"].str.split(";"))


def island_hours(run_dir) -> list:
    """``[(period, hour)]`` of the stored selection that an island criterion
    picked (plan I3b's ``ISLAND_CRITERIA``): the island critical hours."""
    from gridspine.ranking.campus import ISLAND_CRITERIA

    island = {reason for reason, _ in ISLAND_CRITERIA}
    sel = selected_hours(run_dir)
    return [(int(r["period"]), int(r["hour"])) for _, r in sel.iterrows() if island & set(r["reasons"])]




def _record_workers(run_dir, stage, n, fresh=False):
    """Note in ``WORKERS_JSON`` how many processes ``stage`` ran on
    (``parallel.used``); ``fresh`` starts the file over (the frequency
    check, which the cuts follow)."""
    path = run_dir / WORKERS_JSON
    record = {} if fresh or not path.is_file() else json.loads(path.read_text())
    record[stage] = n
    _write_text_atomic(path, json.dumps(record, indent=2))


def island_frequency_campus(run_dir, every_hour: bool = True, workers=1, should_stop=None) -> dict:
    """The island frequency check of a ranked run (plan I6, see the module
    docstring). Needs the campus island requirements and the island tables
    (plan I3a). ``every_hour=False`` skips the every-hour E1. The island
    hours run over ``workers`` processes (default 1, this one; the count
    used is noted in ``WORKERS_JSON``); ``should_stop()`` is asked between
    them (``parallel.Stopped``, nothing written). Returns the summary it
    writes."""
    from gridspine.static import frequency as F
    from gridspine.static import parallel

    run_dir = Path(run_dir)
    campus = load_run_campus(run_dir)
    if campus.island["requirements"] is None:
        raise ContractError("the campus has no island requirements (campus.island); the frequency check needs them")
    tables = _island_tables(run_dir)
    if tables is None:
        raise ContractError("the run has no island tables (island_unit_hourly.csv, island_hourly.csv); "
                            "run the project's campus solve with its island sidecar first")
    unit, hourly_island = tables
    hourly, _ = campus_tables(run_dir)
    hours = island_hours(run_dir)
    scenarios = campus.island["requirements"]["scenarios"]
    f0 = float(campus.net.f_hz)
    sweep = (F.e1_every_hour(campus, hourly, hourly_island, unit, f0=f0)
             if every_hour and "seamless" in scenarios else pd.DataFrame(columns=list(F.E1_HOURLY_COLUMNS)))
    study = F.campus_frequency_study(campus, hourly, hours, island_unit_hourly=unit, f0=f0, workers=workers,
                                     should_stop=should_stop)
    _record_workers(run_dir, "island_frequency", parallel.used(workers, len(hours)), fresh=True)
    for name, df in ((FREQ_E1_HOURLY_CSV, sweep), (FREQ_EVENTS_CSV, study.events), (FREQ_LOSSES_CSV, study.losses),
                     (FREQ_BRIDGE_CSV, study.bridge), (FREQ_PLANNED_CSV, study.planned), (FREQ_UPS_CSV, study.ups)):
        _write_text_atomic(run_dir / name, df.to_csv(index=False))

    def failing(df):
        if df.empty:
            return []
        bad = df[~df["ok"].astype(bool)]
        return sorted({(int(p), int(h)) for p, h in zip(bad["period"], bad["hour"])})

    sweep_fail = failing(sweep)
    bridge_fail = (sorted({(int(r["period"]), int(r["hour"])) for _, r in study.bridge.iterrows()
                           if r["ok"] is not None and not r["ok"]})        # None: no e(t−1) to judge
                   if not study.bridge.empty else [])
    infeasible = (sorted({(int(r["period"]), int(r["hour"])) for _, r in study.planned.iterrows() if not r["feasible"]})
                  if not study.planned.empty else [])
    summary = {
        "label": F.LABEL,
        "ledger": list(F.LEDGER),
        "scenarios": scenarios,
        "island_hours": [list(h) for h in hours],
        "every_hour_e1": bool(len(sweep)),
        "failing_hours": [list(h) for h in failing(study.events)],
        "bridge_failing_hours": [list(h) for h in bridge_fail],
        "planned_infeasible_hours": [list(h) for h in infeasible],
        "e1_failing_hours": [list(h) for h in sweep_fail],
        # E1 at every hour doubles as a check on the ranking's choice (plan I6)
        "e1_failing_not_selected": [list(h) for h in sweep_fail if h not in set(hours)],
    }
    _write_text_atomic(run_dir / FREQ_SUMMARY_JSON, json.dumps(summary, indent=2))
    return summary


def island_static_campus(run_dir) -> dict:
    """The island steady state and short circuit of a ranked run at its
    island critical hours (plan I4, I5, I5b; see the module docstring).
    Needs the campus island data; reads I3a's per-unit table when the run
    has the island tables, for which gensets run at each hour. Returns the
    summary it writes."""
    from gridspine.static import island_hours as IH

    run_dir = Path(run_dir)
    campus = load_run_campus(run_dir)
    if not campus.island["units"]:
        raise ContractError("the campus has no island data (no unit has an island block); "
                            "the island steady state and short circuit need it")
    tables = _island_tables(run_dir)
    hourly, _ = campus_tables(run_dir)
    hours = island_hours(run_dir)
    study = IH.island_static_study(campus, hourly, hours, tables[0] if tables is not None else None)
    for name, fname in ISLAND_STATIC_CSV.items():
        _write_text_atomic(run_dir / fname, getattr(study, name).to_csv(index=False))
    summary = IH.summary(study, hours)
    summary["models"] = {ISLAND_STATIC_CSV[name]: text for name, text in summary["models"].items()}
    _write_text_atomic(run_dir / ISLAND_STATIC_SUMMARY_JSON, json.dumps(summary, indent=2))
    return summary


def cuts_campus(run_dir, workers=1, should_stop=None) -> pd.DataFrame:
    """I7's frequency cuts for a run whose frequency check has run
    (``island_frequency_campus``): the failing E1, E2, E3 and E5 events at
    the island hours and E1 at every hour, each re-run and inverted
    (``frequency.propose_cuts``), ``current`` from the run's I3a tables.
    The events run over ``workers`` processes and ``should_stop`` is asked
    between them, as in ``island_frequency_campus``. Writes
    ``campus_freq_cuts.csv`` (``frequency.CUT_COLUMNS``); returns it."""
    from gridspine.static import frequency as F
    from gridspine.static import parallel

    run_dir = Path(run_dir)
    events_path, sweep_path = run_dir / FREQ_EVENTS_CSV, run_dir / FREQ_E1_HOURLY_CSV
    if not events_path.is_file() or not sweep_path.is_file():
        raise ContractError("the run has no frequency check (campus_freq_events.csv); run island_frequency_campus first")
    campus = load_run_campus(run_dir)
    tables = _island_tables(run_dir)
    hourly, _ = campus_tables(run_dir)
    events, sweep = pd.read_csv(events_path), pd.read_csv(sweep_path)
    cuts = F.propose_cuts(campus, hourly, events, sweep,
                          island_hourly=tables[1] if tables is not None else None,
                          island_unit_hourly=tables[0] if tables is not None else None,
                          f0=float(campus.net.f_hz), workers=workers, should_stop=should_stop)
    _record_workers(run_dir, "cuts", parallel.used(workers, len(F.cut_events(events, sweep))))
    _write_text_atomic(run_dir / FREQ_CUTS_CSV, cuts.to_csv(index=False))
    return cuts


def gfm_settings_campus(run_dir, *, allow_i_max: bool = False, max_sims: int = 400, margin=None,
                        should_stop=None) -> dict:
    """Recommended grid-forming settings for a run whose frequency check has
    run (``island_frequency_campus``): one droop and one equivalent inertia
    for every GFM converter that make the island-hour events pass with margin
    (``gfm_tuning.recommend_gfm_settings``), or, when none within the bounds
    does, the capacity I7 would buy. The cases are the E1, E2, E3 and E5 rows
    at the island hours, passing and failing alike (a setting must not break
    a passing event), each rebuilt as the study ran it; E4 has no GFM in it and
    E6 is not rebuilt, so neither is a case; the every-hour E1 sweep is left
    to the cuts. ``margin`` defaults to the requirements'
    ``gfm_tuning_margin`` (0.1 when absent; never ``gfm_margin``, which is
    I2b's capacity margin). The search ranges are ledgered as assumed
    (``ledger``). ``should_stop`` is the search's cooperative stop
    (``gfm_tuning.SearchStopped``, nothing written). Writes
    ``campus_gfm_settings.json`` and returns it."""
    from gridspine.static import frequency as F
    from gridspine.static import gfm_tuning as T

    run_dir = Path(run_dir)
    events_path = run_dir / FREQ_EVENTS_CSV
    if not events_path.is_file():
        raise ContractError("the run has no frequency check (campus_freq_events.csv); run island_frequency_campus first")
    campus = load_run_campus(run_dir)
    tables = _island_tables(run_dir)
    hourly, _ = campus_tables(run_dir)
    iu = tables[0] if tables is not None else None
    limits, req = F._limits(campus)
    values = req["values"]
    f0 = float(campus.net.f_hz)
    window, eq = values["rocof_window_ms"], F.equipment(campus, f0)
    if margin is None:
        margin = float(values.get("gfm_tuning_margin", 0.1))
    events = pd.read_csv(events_path)
    events = events[events["event"].isin(["E1", "E2", "E3", "E5"])]
    if "i_max_pu" in events:
        events = events[events["i_max_pu"].isna()]              # Q8's reruns are sensitivities, not cases
    ctx, cases, seen = {}, [], set()
    for _, r in events.iterrows():
        key = (int(r["period"]), int(r["hour"]), str(r["scenario"]), str(r["event"]),
               str(r.get("unit")), round(float(r["dp_mw"]), 9))
        if key in seen:
            continue
        seen.add(key)
        built_ = F._event_run(campus, hourly, iu, r, ctx, (1e-3, 20.0), (5e-3, 10.0), f0=f0)
        if built_ is None:
            continue

        def run(us, dt, t_end, r=r):
            return F._event_run(campus, hourly, iu, r, ctx, (dt, t_end), (dt, t_end), f0=f0)[1](us, t_end)
        cases.append(T.Case(f"{key[0]} h{key[1]} {key[2]} {key[3]}", built_[0], run))
    rec = T.recommend_gfm_settings(cases, limits, f0=f0, equipment_=eq, window_ms=window, margin=margin,
                                   allow_i_max=allow_i_max, max_sims=max_sims, should_stop=should_stop)
    out = {"label": rec.label, "ledger": T.ledger(rec.bounds), "status": rec.status, "achievable": rec.achievable, "changed": rec.changed,
           "settings": rec.settings,
           "cases": rec.cases, "capacity": rec.capacity, "comparison": rec.comparison,
           "simulations": rec.simulations, "max_simulations": max_sims, "margin": margin,
           "bounds": dataclasses.asdict(rec.bounds), "allow_i_max": allow_i_max, "notes": rec.notes,
           "excluded": ["E4 (no grid-forming converter in a UPS-bridged genset start)",
                        "E6 (planned islanding is not rebuilt)", "the every-hour E1 sweep (see the cuts)"]}
    out = _json_ready(out)
    _write_text_atomic(run_dir / GFM_SETTINGS_JSON, json.dumps(out, indent=2, allow_nan=False))
    return out


def _json_ready(x):
    """``x`` with each non-finite float as None and each numpy scalar as its
    Python value: strict JSON (a collapse's ``inf`` RoCoF, nadir or capacity
    delta would otherwise be written as ``Infinity``, which a JSON response
    refuses). ``json.dumps``' ``default`` never sees a float, so this walks
    the value first."""
    if isinstance(x, dict):
        return {k: _json_ready(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_ready(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (bool, int, float, str)):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def psse_campus(run_dir, hours=None) -> list:
    """The PSS/E handoff of a ranked run (plan I8): one bundle per island
    critical hour (``island_hours``), or per ``hours`` given, under
    ``psse/``, each named ``p<period>_h<hour>``. Returns the bundles."""
    from gridspine.handoff.campus_bundle import export_campus_bundle
    from gridspine.static.frequency import LEDGER

    run_dir = Path(run_dir)
    campus = load_run_campus(run_dir)
    hourly, _ = campus_tables(run_dir)
    out = run_dir / PSSE_DIR
    bundles = []
    for period, hour in (island_hours(run_dir) if hours is None else hours):
        rows = hourly[(hourly["period"] == period) & (hourly["hour"] == hour)][["unit_id", "p_mw", "status"]]
        if rows.empty:
            raise ContractError(f"the run has no hour {hour} in period {period}")
        bundles.append(export_campus_bundle(out, campus, rows.reset_index(drop=True), f"p{period}_h{hour}",
                                            ledger=LEDGER))
    return bundles


#: P_ref of a normally open tie (``pcc.tie_normally_open``): its rating
#: stands for the connection the grid-connected checks are made at.
#: The PCC exchange (MW) a normally open tie may show in any hour: the
#: solver's noise, not power through the tie.
TIE_OPEN_TOL_MW = 0.01
OPEN_TIE_P_REF = "normally open tie: its rating, the campus file p_connection_mw (grid-connected checks)"


def _requirement(code, campus, pcc, pf):
    """``(requirement, p_ref, p_ref_from)`` at the PCC. For a normally open
    tie the clause says it applies with the tie closed."""
    p_ref, p_ref_from = _p_ref(campus, pcc)
    req = requirement_from(code, p_ref, pf=pf)
    if campus.tie_normally_open:
        req = dataclasses.replace(req, clause=req.clause + OPEN_TIE_CLAUSE)
    return req, p_ref, p_ref_from


def _p_ref(campus, pcc: pd.DataFrame):
    p = campus.params
    hit = p[(p["param"] == "p_connection_mw")]
    peak = float(pcc["import_mw"].abs().max())
    if campus.tie_normally_open and peak > TIE_OPEN_TOL_MW:
        i = pcc["import_mw"].abs().idxmax()
        raise ContractError(
            f"pcc.tie_normally_open is set, but the PCC imports or exports {peak:.3g} MW (period "
            f"{int(pcc.at[i, 'period'])}, hour {int(pcc.at[i, 'hour'])}): the tie conducts, so its transformers "
            "must be sized; remove the flag"
        )
    if len(hit):
        return float(hit["value"].iloc[0]), OPEN_TIE_P_REF if campus.tie_normally_open else "campus file p_connection_mw"
    if not peak > 0:
        raise ContractError(
            "the PCC has no import or export in any hour of the prepared periods, so the peak import gives no "
            "connection capacity; set the campus file's pcc p_connection_mw (and, for a normally open tie, "
            "tie_normally_open: true)"
        )
    return peak, "peak |import| over every hour of the prepared periods"


def size_campus(run_dir, criteria: SizingCriteria = SizingCriteria(), profile: str = DEFAULT_PROFILE,
                pf: float | None = None, profile_dirs=()) -> dict:
    """Solve the selected hours, size the transformers, and correct each hour
    into the PCC reactive band. Writes the per-hour tables, the two sizing
    tables and the requirement. Returns ``{"transformers", "compensation",
    "requirement"}``. ``profile`` may name a project profile in
    ``profile_dirs``; its unconfirmed limits reach the report as
    ``extracted``."""
    run_dir = Path(run_dir)
    code = load_grid_code(profile, extra_dirs=profile_dirs)
    hourly, pcc = campus_tables(run_dir)
    selection = selected_hours(run_dir)
    campus = load_run_campus(run_dir)
    req, p_ref, p_ref_from = _requirement(code, campus, pcc, pf)
    flows, reactive, trafo_rows, bus_rows, pcc_rows, q_rows = {}, {}, [], [], [], []
    for period, hour in selection[["period", "hour"]].itertuples(index=False):
        period, hour = int(period), int(hour)
        rows = hourly[(hourly["period"] == period) & (hourly["hour"] == hour)]
        cases = solve_cases(campus, rows)
        flows[(period, hour)] = cases
        for case, f in cases.items():
            key = {"period": period, "hour": hour, "case": case}
            pcc_rows.append({**key, "converged": f.converged, "p_mw": f.pcc_p_mw, "q_mvar": f.pcc_q_mvar,
                             "losses_mw": f.losses_mw})
            trafo_rows += [{**key, **r} for r in f.trafo.to_dict("records")]
            bus_rows += [{**key, **r} for r in f.bus.to_dict("records")]
        r = reactive_need(campus, rows, req)
        reactive[(period, hour)] = r
        q_rows.append({"period": period, "hour": hour, "converged": r.converged, "q0_mvar": r.q0_mvar,
                       "q_final_mvar": r.q_final_mvar, "q_inverters_mvar": r.q_inverters_mvar,
                       "q_comp_mvar": r.q_comp_mvar, "compliant_without": r.compliant_without})
    sizing = size_transformers(flows, campus, criteria)
    comp = size_compensation(reactive, criteria)
    sc_frames = []
    for period, block in hourly.groupby("period"):
        installed = set(block.loc[block["status"] == 1, "unit_id"])
        sc_frames.append(campus_fault_levels(campus, installed).assign(period=int(period)))
    short_circuit = pd.concat(sc_frames, ignore_index=True)
    requirement = {"q_limit_mvar": req.q_limit_mvar, "clause": req.clause, "source": req.source,
                   "profile": profile, "pf": pf, "p_ref_mw": p_ref, "p_ref_from": p_ref_from}
    if campus.tie_normally_open:
        requirement["tie_open"] = True
    _write_text_atomic(run_dir / LF_TRAFO_CSV, pd.DataFrame(trafo_rows).to_csv(index=False))
    _write_text_atomic(run_dir / LF_BUS_CSV, pd.DataFrame(bus_rows).to_csv(index=False))
    _write_text_atomic(run_dir / LF_PCC_CSV, pd.DataFrame(pcc_rows).to_csv(index=False))
    _write_text_atomic(run_dir / SIZING_TRAFO_CSV, sizing.to_csv(index=False))
    _write_text_atomic(run_dir / REACTIVE_CSV, pd.DataFrame(q_rows).to_csv(index=False))
    _write_text_atomic(run_dir / SIZING_COMP_CSV, comp.to_csv(index=False))
    _write_text_atomic(run_dir / REQUIREMENT_JSON, json.dumps(requirement, indent=2))
    _write_text_atomic(run_dir / SHORT_CIRCUIT_CSV, short_circuit.to_csv(index=False))
    net = campus.net
    compliance = campus_compliance(
        bus=pd.DataFrame(bus_rows), trafo=pd.DataFrame(trafo_rows), reactive=pd.DataFrame(q_rows),
        sizing=sizing, compensation=comp, short_circuit=short_circuit, requirement=requirement,
        profile=code, pcc_bus=str(net.bus.at[int(net.ext_grid["bus"].iloc[0]), "name"]),
        bus_kv=dict(zip(net.bus["name"].astype(str), net.bus["vn_kv"].astype(float))),
        held=held_transformers(campus), pcc=pd.DataFrame(pcc_rows),
    )
    _write_text_atomic(run_dir / COMPLIANCE_CSV, compliance.to_csv(index=False))
    # Beside the compliance table, never in it (the measure loop reads that):
    # the IBR capability, and the requirements no steady-state check can show.
    ibr = ibr_q_capability(campus, code)
    if ibr is not None:
        _write_text_atomic(run_dir / IBR_CAPABILITY_CSV, ibr.to_csv(index=False))
    else:
        (run_dir / IBR_CAPABILITY_CSV).unlink(missing_ok=True)       # an earlier profile's table
    curve = ibr_q_curve(campus, code)
    if curve is not None:
        _write_text_atomic(run_dir / IBR_Q_CURVE_CSV, curve.to_csv(index=False))
    else:
        (run_dir / IBR_Q_CURVE_CSV).unlink(missing_ok=True)          # an earlier profile's table
    reported = reported_requirements(code)
    _write_text_atomic(run_dir / REPORTED_REQUIREMENTS_JSON, json.dumps(reported, indent=2))
    return {"transformers": sizing, "compensation": comp, "requirement": requirement,
            "short_circuit": short_circuit, "compliance": compliance, "ibr_capability": ibr,
            "ibr_q_curve": curve, "reported_requirements": reported}


def invest_campus(run_dir, library=None, criteria: SizingCriteria = SizingCriteria(), profile: str = DEFAULT_PROFILE,
                  pf: float | None = None, profile_dirs=(), pcc_switchgear: bool = True,
                  method: str = "least_cost", progress=None, should_stop=None,
                  parallel: ParallelRules = ParallelRules(), pcc_tap_control: str = "campus") -> dict:
    """Least-cost electrical assets for a ranked run, AC-checked (module
    docstring). ``library`` is a path, or None for the shipped library;
    ``profile`` may be a project profile in ``profile_dirs``, as for
    ``size_campus``. ``pcc_switchgear`` False leaves the PCC's switchgear to
    the grid operator (``select_assets``); the choice is written with the
    results.
    Writes the investment files and returns ``{"investment", "cost",
    "compliance", "history", "dispatch", "spec", "unresolved", "scope"}``.

    ``method="milp"`` chooses jointly (plan C11, ``campus_milp``), starting
    from the least-cost pick, and also writes ``campus_milp_history.csv``
    and ``campus_milp_comparison.csv``; its result adds ``milp_history``,
    ``comparison``, ``summary`` and ``fallback``. ``progress`` and
    ``should_stop`` go to the MILP's loop (``select_assets_milp``): one call
    per iteration, and a cancel that ends the loop with
    ``summary["stop"] == "cancelled"``, its best point still written; the
    least-cost pick takes neither.

    ``parallel`` are the rules for adding units next to existing
    transformers (plan C13, ``campus_invest.ParallelRules``); the defaults
    are the owner's, all assumed, and are written to
    ``campus_invest_scope.json`` with the PCC switchgear setting.

    ``pcc_tap_control`` says who moves the taps of the transformers at the
    PCC (plan C15): ``campus`` (default; the study may move them) or
    ``grid_operator`` (they stay at the file's taps). It is written to the
    scope file too, and the taps of the re-check to
    ``campus_invest_taps.csv``."""
    if method not in METHODS:
        raise ContractError(f"unknown investment method {method!r}; allowed {list(METHODS)}")
    if method != "milp" and (progress is not None or should_stop is not None):
        raise ContractError("progress and should_stop belong to method='milp'; the least-cost pick takes neither")
    run_dir = Path(run_dir)
    hourly, pcc = campus_tables(run_dir)
    selection = selected_hours(run_dir)
    spec = yaml.safe_load(_require(run_dir, CAMPUS_YAML).read_text())
    lib = load_asset_library(library)
    grid_code = load_grid_code(profile, extra_dirs=profile_dirs)
    req, _, _ = _requirement(grid_code, build_campus(spec), pcc, pf)
    choose = select_assets_milp if method == "milp" else select_assets
    hooks = {"progress": progress, "should_stop": should_stop} if method == "milp" else {}
    manifest = json.loads(_require(run_dir, CAMPUS_MANIFEST).read_text())
    out = choose(spec, hourly, selection, lib, req, grid_code, criteria,
                 pcc_switchgear=pcc_switchgear, parallel=parallel, pcc_tap_control=pcc_tap_control,
                 period_years=manifest.get("period_weightings"), **hooks)
    build_campus(out["spec"])                                # the invested file must build before it is written
    _write_text_atomic(run_dir / INVESTMENT_CSV, out["investment"].to_csv(index=False))
    _write_text_atomic(run_dir / COST_CSV, out["cost"].to_csv(index=False))
    _write_text_atomic(run_dir / INVESTED_YAML, yaml.safe_dump(out["spec"], sort_keys=False))
    _write_text_atomic(run_dir / COMPLIANCE_INVESTED_CSV, out["compliance"].to_csv(index=False))
    _write_text_atomic(run_dir / INVEST_HISTORY_CSV, out["history"].to_csv(index=False))
    _write_text_atomic(run_dir / INVEST_DISPATCH_CSV, out["dispatch"].to_csv(index=False))
    _write_text_atomic(run_dir / INVEST_SCOPE_JSON, json.dumps(out["scope"], indent=2))
    _write_text_atomic(run_dir / INVEST_TAPS_CSV, out["taps"].to_csv(index=False))
    if method == "milp":
        _write_text_atomic(run_dir / MILP_HISTORY_CSV, out["milp_history"].to_csv(index=False))
        _write_text_atomic(run_dir / MILP_COMPARISON_CSV, out["comparison"].to_csv(index=False))
    return out


# --- the harmonic screen (grid-to-process issue 17) --------------------------

HARMONICS_CSV = "campus_harmonics.csv"

SK_FROM_NETWORK = "the project's PoC fault level (eh_sk_mva)"
SK_TYPICAL = "typical for the PoC voltage (the draft's fallback; the project gives no fault level)"
SK_CAMPUS = "the campus description (uploaded or edited)"


def _pcc_sk_min(pcc: dict) -> float:
    v = pcc["sk_min_mva"]
    return float(v["value"] if isinstance(v, dict) else v)


def _sk_source(campus: dict, n) -> tuple[str, bool]:
    """Where the campus PCC's minimum fault level came from, and whether it is
    the producer's typical fallback."""
    from gridspine.producers.campus import _typical_sk

    pcc = campus["campus"]["pcc"]
    sk = _pcc_sk_min(pcc)
    bus = pcc.get("pypsa_name")
    given = math.nan
    if bus in n.buses.index and "eh_sk_mva" in n.buses.columns:
        try:
            given = float(n.buses.at[bus, "eh_sk_mva"])
        except (TypeError, ValueError):
            given = math.nan
    if math.isfinite(given) and given > 0:
        if math.isclose(sk, given, rel_tol=1e-9):
            return SK_FROM_NETWORK, False
    elif math.isclose(sk, _typical_sk(float(pcc["vn_kv"]))[0], rel_tol=1e-9):
        return SK_TYPICAL, True
    return SK_CAMPUS, False


def _unchanged_project(run_dir: Path):
    """The project network a run was prepared from, refused when it changed
    since (its sha256 is in the manifest)."""
    manifest = json.loads(_require(run_dir, CAMPUS_MANIFEST).read_text())
    network_path = Path(manifest["network_path"])
    if not network_path.is_file() or _sha256_file(network_path) != manifest["network_sha256"]:
        raise ContractError("the project changed since this campus run was prepared; prepare the run again")
    return load_solved_network(network_path)


def harmonic_units(n, poc: str) -> pd.DataFrame:
    """The harmonic sources of a project network, all lumped at ``poc``: every
    Link that is a process unit (``eh_process``) or names a
    ``harmonic_source``. A unit with an input missing is kept (the screen says
    "no input")."""
    links = n.links
    cols = ("rated_mva", "harmonic_source", "harmonic_spectrum", "spectrum_post_filter")

    def col(name, default):
        return links[name] if name in links.columns else pd.Series(default, index=links.index, dtype=object)

    process = col("eh_process", False).map(lambda v: str(v).strip().lower() in ("true", "1", "1.0"))
    source = col("harmonic_source", "").map(lambda v: "" if pd.isna(v) else str(v).strip())
    chosen = links.index[process | (source != "")]
    out = pd.DataFrame({"unit": list(chosen), "poc": poc}, columns=["unit", "poc"])
    for c in cols:
        values = col(c, None).reindex(chosen)
        out[c if c != "spectrum_post_filter" else "post_filter"] = list(values)
    return out


def _screen_inputs(run_dir, profile, profile_dirs):
    """A prepared run's campus description, its project network (refused when
    it changed since the run was prepared) and the grid-code profile."""
    n = _unchanged_project(run_dir)
    campus = yaml.safe_load(_require(run_dir, CAMPUS_YAML).read_text())
    return campus, n, load_grid_code(profile, extra_dirs=profile_dirs)


def _screen_frame(pocs, units, unit_assumed, sk_source, typical, profile, limit_assumed, poc_columns,
                  unit_columns) -> pd.DataFrame:
    """One ``level`` ``poc`` row per PoC and one ``unit`` row per unit, with
    the S_k source, the profile and the ``assumed`` flag."""
    frames = []
    for level, df, extra in (("poc", pocs, bool(unit_assumed.any())), ("unit", units, unit_assumed)):
        df = df.copy()
        df.insert(0, "level", level)
        df["sk_mva_source"] = sk_source
        df["profile"] = profile
        df["assumed"] = limit_assumed | typical | extra
        if typical:
            df["note"] = df["note"].map(lambda s: f"S_k is {SK_TYPICAL}, so no verdict here is a pass to "
                                                  f"rely on; {s}")
        frames.append(df)
    columns = ["level", *dict.fromkeys((*unit_columns[:2], *poc_columns, *unit_columns)),
               "sk_mva_source", "profile", "assumed"]
    return pd.concat(frames, ignore_index=True).reindex(columns=columns)


def harmonics_campus(run_dir, profile: str = DEFAULT_PROFILE, profile_dirs=(), law: str = "iec") -> pd.DataFrame:
    """The harmonic screen of a prepared run (``static/harmonics.py``),
    written to ``campus_harmonics.csv``: one ``level`` ``poc`` row and one
    ``unit`` row per harmonic source.

    S_k is the campus PCC's minimum case; ``sk_mva_source`` says whether it is
    the project's own fault level, the draft's typical fallback (the row is
    then ``assumed`` and says so) or the campus description's. The unit inputs
    are read from the project network the run was prepared from, which must
    be unchanged since (its sha256 is in the manifest)."""
    from gridspine.static import harmonics as hm

    run_dir = Path(run_dir)
    campus, n, code = _screen_inputs(run_dir, profile, profile_dirs)
    limits, cited = hm.limits_from_profile(code)

    pcc = campus["campus"]["pcc"]
    poc = str(pcc["bus"])
    sk = _pcc_sk_min(pcc)
    sk_source, typical = _sk_source(campus, n)
    study = hm.harmonic_table(harmonic_units(n, poc), {poc: sk}, limits, law=law)

    limit_assumed = not cited or any(c["source"] != "code" for c in cited)
    unit_demo = study.units["spectrum_source"].astype(str).str.startswith("demo")
    out = _screen_frame(study.pocs, study.units, unit_demo, sk_source, typical, profile, limit_assumed,
                        hm.POC_COLUMNS, hm.UNIT_COLUMNS)
    _write_text_atomic(run_dir / HARMONICS_CSV, out.to_csv(index=False))
    return out


# --- the flicker screen (grid-to-process issue 17) ---------------------------

FLICKER_CSV = "campus_flicker.csv"


def flicker_units(n, poc: str) -> pd.DataFrame:
    """The fluctuating loads of a project network, all lumped at ``poc``:
    every Link that is a process unit (``eh_process``) or names a flicker
    step. A unit with an input missing is kept (the screen says "no input")."""
    links = n.links

    def col(name, default):
        return links[name] if name in links.columns else pd.Series(default, index=links.index, dtype=object)

    process = col("eh_process", False).map(lambda v: str(v).strip().lower() in ("true", "1", "1.0"))
    chosen = links.index[process | col("flicker_step_mva", None).notna()]
    return pd.DataFrame({"unit": list(chosen), "poc": poc,
                         "step_mva": list(col("flicker_step_mva", None).reindex(chosen)),
                         "changes_per_min": list(col("flicker_changes_per_min", None).reindex(chosen))},
                        columns=["unit", "poc", "step_mva", "changes_per_min"])


def flicker_campus(run_dir, profile: str = DEFAULT_PROFILE, profile_dirs=()) -> pd.DataFrame:
    """The flicker screen of a prepared run (``static/flicker.py``), written
    to ``campus_flicker.csv``: one ``level`` ``poc`` row and one ``unit`` row
    per process unit, with S_k and its source as in ``harmonics_campus``."""
    from gridspine.static import flicker as fl

    run_dir = Path(run_dir)
    campus, n, code = _screen_inputs(run_dir, profile, profile_dirs)
    limits, cited = fl.limits_from_profile(code)

    pcc = campus["campus"]["pcc"]
    poc = str(pcc["bus"])
    sk_source, typical = _sk_source(campus, n)
    study = fl.flicker_table(flicker_units(n, poc), {poc: _pcc_sk_min(pcc)}, limits)

    limit_assumed = not cited or any(c["source"] != "code" for c in cited)
    out = _screen_frame(study.pocs, study.units, pd.Series(False, index=study.units.index), sk_source, typical,
                        profile, limit_assumed, fl.POC_COLUMNS, fl.UNIT_COLUMNS)
    _write_text_atomic(run_dir / FLICKER_CSV, out.to_csv(index=False))
    return out


# --- the harmonic impedance scan (datacenter grid readiness issue 12) -------

HARMONIC_SCAN_CSV = "campus_harmonic_scan.csv"
HARMONIC_ENVELOPE_CSV = "campus_harmonic_envelope.csv"
HARMONIC_RESONANCE_CSV = "campus_harmonic_resonance.csv"


def harmonic_scan_campus(run_dir, profile: str = DEFAULT_PROFILE, profile_dirs=()):
    """The harmonic impedance scan of a prepared run from its PCC
    (``static/harmonic_scan.py``), written to ``campus_harmonic_scan.csv``
    (R(h), X(h) per short-circuit case and capacitor state),
    ``campus_harmonic_envelope.csv`` and ``campus_harmonic_resonance.csv``.

    The orders that count are 6k +- 1 and every order a harmonic source's
    spectrum injects (vendor or demo default, as the harmonic screen takes
    them), read from the project network, which must be unchanged since the
    run was prepared. Returns the ``ScanStudy``."""
    from gridspine.static import harmonic_scan as hs
    from gridspine.static import harmonics as hm

    run_dir = Path(run_dir)
    n = _unchanged_project(run_dir)
    campus = load_run_campus(run_dir)
    bus, cases, notes = hs.campus_cases(campus)
    spectrum_orders, demo = set(), False
    for _, u in harmonic_units(n, str(campus.net.bus.at[bus, "name"])).iterrows():
        spectrum, source = hm.unit_spectrum(u["harmonic_source"], u["harmonic_spectrum"])
        spectrum_orders.update(h for h, pct in (spectrum or {}).items() if pct > 0)
        demo |= bool(spectrum) and source.startswith("demo")
    limit, cited = hs.clearance_from_profile(load_grid_code(profile, extra_dirs=profile_dirs))
    not_code = bool(cited) and cited[0]["source"] != "code"
    if not_code:
        notes.append(f"the resonance clearance is {cited[0]['source']}, not the grid code's")
    study = hs.impedance_scan(campus.net, bus, cases, spectrum_orders=sorted(spectrum_orders), spectrum_demo=demo,
                              limit_h=limit, limit_assumed=not_code, notes=notes)
    for name, df in ((HARMONIC_SCAN_CSV, study.scan), (HARMONIC_ENVELOPE_CSV, study.envelope),
                     (HARMONIC_RESONANCE_CSV, study.resonance)):
        _write_text_atomic(run_dir / name, df.to_csv(index=False))
    return study
