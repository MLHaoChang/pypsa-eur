"""
The site question pack (guided investment study MVP-1, phase S4).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S4)
Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md §8.1

:func:`build_site_network` turns a study's intake and its assumptions ledger
into the network ONE option solves. Every cost is read from a ledger row,
never a literal; every rule below is a plan or gate condition:

* **Buses** ``site`` and ``grid``. The site bus is NOT tagged ``eh_poc`` (N11):
  ``archetypes.select_import_links`` must return exactly ``['grid_import']``,
  by role, and the pack refuses to return a network where it does not.
* **Grid supply** (N13, N2-v2): a zero-cost Generator at ``grid`` with
  ``p_min_pu = -1`` (it supplies import and absorbs export), tagged
  ``eh_role = grid_supply`` so preflight's ``gen_zero_costs`` warning skips
  it. The tariff is priced on the links, not on this unit.
* **Connection** ``grid_import`` (``eh_role = grid_import``, ``p_nom`` = the
  connection limit, active) and ``grid_export`` (``eh_role = grid_export``).
  An inactive import link is refused (``PackError``, code
  ``import_link_inactive``).
* **Prices** are permanent network data written by
  ``tariff.write_tariff_prices`` from ``tariff.tariff_from_ledger`` — the
  ledger's demand-charge price and energy-price level applied to the chosen
  tariff's structure, the same object the LP config and the bill read.
* **Load** from the intake: an upload (``series_mw`` or an ``upload_id``
  resolved by the caller) or a synthetic sector profile from
  ``study_library/load_profiles/`` scaled to the stated annual MWh (marked
  ``synthetic_load_profile``). Written to ``loads_t.p_set`` directly; the
  process-global ``_user_ts`` is never touched (OPEN-ITEMS 1).
* **PV** (only in an option whose ``free_assets`` has it): an extendable
  Generator on the ordinary ``overnight_cost`` + ``lifetime`` +
  ``discount_rate`` path, with a synthetic profile marked
  ``synthetic_pv_profile``. Rooftop or utility rows by ``intake.pv.kind``.
* **Battery** (review v1 B3/B4, spec §8.1 amended): a ``StorageUnit`` with
  ``p_nom_extendable``, the option's enumerated ``max_hours``,
  ``cyclic_state_of_charge`` (N3-v2), ``efficiency_store =
  efficiency_dispatch = sqrt(rte)`` from the ledger's ONE round-trip row, and
  ``capital_cost`` per MW written directly as the SUM OF TWO ANNUITIES
  (labelled ``derived_from_two_annuities``; the one place the guided flow
  writes the annuity field, because ``overnight_cost`` cannot carry two
  lifetimes)::

      annuity(r, inverter_life) x inverter_eur_per_kw x 1000
        + max_hours x annuity(r, storage_life) x storage_eur_per_kwh x 1000

  plus ``fom_cost`` = inverter FOM share x inverter investment (the library
  has no storage-block FOM row, so FOM applies to the inverter only).
  ``overnight_cost`` stays unset; the upfront figures the pro forma books are
  :func:`battery_upfront_eur_per_mw`, read from the ledger.
* **Bounds** (BC-2): every extendable asset gets a finite ``p_nom_max`` =
  connection limit x the ledger's ``sizing_limit_connection_multiple``.
* **Snapshots**: 8760 hourly steps of one non-leap year, weightings 1.
* **Option ``none``** (BC-1) omits the battery and PV; it never fixes them
  at 0.

Refused before anything is built (typed ``PackError``): a ledger with any
``needs_attention`` row (gate S2), a ledger priced on another tariff than
the intake chose (a tariff change not re-seeded), an incomplete intake, a
measured capacity charge and the other tariff forms MVP-1 does not price
(gate S3), a leap year.
"""
from __future__ import annotations

import calendar
import csv
import functools
import hashlib
import io
import json
import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import pandas as pd
import pypsa

from models.study import AssumptionsLedger, DecisionQuestion, OptionSpec, Tariff
from services.solver.periodized_costs import _annuity
from services.study import library as study_library
from services.study import questions as Q
from services.study import tariff as study_tariff

__all__ = [
    "HOURS", "PackError", "PACK_META_KEY", "battery_capital_cost_eur_per_mw",
    "battery_fom_eur_per_mw", "battery_upfront_eur_per_mw", "build_site_network",
    "effective_tariff", "intake_tariff", "ledger_hash", "ledger_values",
    "load_profile_ids", "needs_attention_rows", "option_solver_config",
    "refuse_unrunnable_ledger", "round_trip_efficiency",
]

HOURS = 8760
PACK_META_KEY = "decision_study_pack"
IMPORT_LINK = "grid_import"
EXPORT_LINK = "grid_export"
GRID_SUPPLY = "grid_supply"
LOAD_NAME = "site_load"
BATTERY_NAME = "battery"
PV_NAME = "pv"
_PROFILE_DIR = study_library.LIBRARY_DIR / "load_profiles"


class PackError(ValueError):
    """The pack will not build. `code` is stable and machine-read."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ── ledger reads ──────────────────────────────────────────────────────────

def needs_attention_rows(ledger: AssumptionsLedger) -> list[str]:
    """Every row a re-seed flagged (status or honesty-note protocol)."""
    keys = {r.key for r in ledger.rows if r.status == "needs_attention"}
    for note in ledger.honesty_notes:
        if note.startswith("needs_attention:"):
            keys.add(note.split(":", 2)[1])
    return sorted(keys)


def refuse_unrunnable_ledger(ledger: AssumptionsLedger | None) -> None:
    """Gate S2 carry: nothing runs while any row needs attention."""
    if ledger is None:
        raise PackError("ledger_missing", "the study has no assumptions ledger")
    flagged = needs_attention_rows(ledger)
    if flagged:
        raise PackError(
            "ledger_needs_attention",
            f"ledger row(s) {', '.join(flagged)} need attention after a "
            "re-seed; reset or re-enter them before the study can run (a "
            "value kept from another tariff must never reach the LP)")


def ledger_values(ledger: AssumptionsLedger) -> dict[str, float | None]:
    return {r.key: r.value for r in ledger.rows}


def _need(values: Mapping[str, float | None], key: str) -> float:
    v = values.get(key)
    if v is None or not math.isfinite(float(v)):
        raise PackError("ledger_row_missing",
                        f"the ledger has no value for {key!r}")
    return float(v)


def ledger_hash(ledger: AssumptionsLedger) -> str:
    """A stable hash of the ledger's values, units and statuses."""
    payload = sorted((r.key, r.value, r.unit, r.status, r.provenance)
                     for r in ledger.rows)
    return hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest()[:16]


def round_trip_efficiency(ledger: AssumptionsLedger) -> float:
    """The ONE round-trip row (gate S2 [S10]); the pack splits it sqrt/sqrt."""
    return _need(ledger_values(ledger), "battery_round_trip_efficiency")


def battery_capital_cost_eur_per_mw(ledger: AssumptionsLedger,
                                    max_hours: float) -> float:
    """
    The two-annuity capital cost per MW (EUR/MW/yr), from the ledger:
    ``annuity(r, inverter_life) x inverter x 1000 + max_hours x
    annuity(r, storage_life) x storage x 1000``.
    """
    v = ledger_values(ledger)
    r = _need(v, "discount_rate")
    inverter = _annuity(r, _need(v, "battery_inverter_lifetime_years")) \
        * _need(v, "battery_inverter_eur_per_kw") * 1000.0
    storage = float(max_hours) * _annuity(r, _need(v, "battery_storage_lifetime_years")) \
        * _need(v, "battery_storage_eur_per_kwh") * 1000.0
    return inverter + storage


def battery_fom_eur_per_mw(ledger: AssumptionsLedger) -> float:
    """Inverter FOM share x inverter investment (EUR/MW/yr); no storage FOM row."""
    v = ledger_values(ledger)
    return (_need(v, "battery_inverter_fom_pct_per_year") / 100.0
            * _need(v, "battery_inverter_eur_per_kw") * 1000.0)


def battery_upfront_eur_per_mw(ledger: AssumptionsLedger,
                               max_hours: float) -> dict[str, float]:
    """
    The upfront investment per MW the pro forma books in year 0 (S5), from
    the ledger — never back-calculated from ``capital_cost``, which mixes two
    lifetimes and so has no single overnight cost behind it.
    """
    v = ledger_values(ledger)
    inverter = _need(v, "battery_inverter_eur_per_kw") * 1000.0
    storage = float(max_hours) * _need(v, "battery_storage_eur_per_kwh") * 1000.0
    return {"inverter": inverter, "storage": storage, "total": inverter + storage}


# ── intake reads ──────────────────────────────────────────────────────────

def _site(intake: Mapping[str, Any]) -> Mapping[str, Any]:
    site = intake.get("site") if isinstance(intake, Mapping) else None
    return site if isinstance(site, Mapping) else {}


def _connection_mw(intake: Mapping[str, Any]) -> float | None:
    v = _site(intake).get("connection_mw")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def missing_inputs(intake: Mapping[str, Any] | None) -> list[str]:
    """The template's mandatory inputs the intake has not answered."""
    intake = intake or {}
    out = []
    zone = _site(intake).get("zone")
    if not isinstance(zone, str) or not zone.strip():
        out.append("site")
    if _connection_mw(intake) is None:
        out.append("connection_limit")
    load = intake.get("load")
    if not isinstance(load, Mapping) or not load.get("source"):
        out.append("load")
    # `tariff` falls back to the library default with an honesty note.
    return out


def intake_tariff(intake: Mapping[str, Any] | None,
                  library: study_library.Library) -> tuple[Tariff, list[str]]:
    """The intake's tariff, validated as the engine will price it."""
    try:
        tariff, _prov = study_library._intake_tariff(intake or {}, library)
    except study_library.LibraryError as exc:
        raise PackError("tariff_invalid", str(exc)) from None
    try:
        study_tariff.validate_tariff_intake(tariff)
    except study_tariff.TariffError as exc:
        raise PackError(exc.code, str(exc)) from None
    chosen = (intake or {}).get("tariff") if isinstance(intake, Mapping) else None
    notes = [] if chosen else ["tariff_not_chosen_library_default"]
    return tariff, notes


def _snapshots(intake: Mapping[str, Any]) -> pd.DatetimeIndex:
    year = _site(intake).get("year", 2025)
    try:
        year = int(year)
    except (TypeError, ValueError):
        raise PackError("intake_invalid", f"site.year {year!r} is not a year") from None
    idx = pd.date_range(f"{year}-01-01", periods=HOURS, freq="h")
    # BC-S4-4: 8760 hours from 1 January of a leap year stop on 30 December,
    # leaving a short December billed in full; refused, not truncated.
    if calendar.isleap(year):
        raise PackError(
            "leap_year_unsupported",
            f"{year} is a leap year; MVP-1 models 8760 hourly steps of one "
            "non-leap year")
    return idx


def effective_tariff(intake: Mapping[str, Any] | None, ledger: AssumptionsLedger,
                     library: study_library.Library,
                     snapshots: pd.DatetimeIndex | None = None) -> Tariff:
    """The tariff the pack, the LP and the bill price with (ledger applied)."""
    tariff, _notes = intake_tariff(intake, library)
    rows = [r for r in ledger.rows if r.key == "tariff"]
    if not rows or rows[0].technical_name != tariff.tariff_id:
        held = rows[0].technical_name if rows else None
        raise PackError(
            "ledger_tariff_stale",
            f"the ledger is priced on tariff {held!r} but the intake chose "
            f"{tariff.tariff_id!r}; re-seed the ledger before running")
    idx = snapshots if snapshots is not None else _snapshots(intake or {})
    try:
        return study_tariff.tariff_from_ledger(tariff, ledger, idx)
    except study_tariff.TariffError as exc:
        raise PackError(exc.code, str(exc)) from None


# ── load and PV profiles ──────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _profile_index() -> dict[str, dict[str, str]]:
    with (_PROFILE_DIR / "index.csv").open(encoding="utf-8", newline="") as fh:
        return {r["profile_id"]: r for r in csv.DictReader(fh)}


def load_profile_ids() -> list[str]:
    return sorted(_profile_index())


@functools.lru_cache(maxsize=8)
def _profile_factors(profile_id: str) -> dict[tuple[int, str, int], float]:
    if profile_id not in _profile_index():
        raise PackError("load_profile_unknown",
                        f"no sector load profile {profile_id!r} "
                        f"(known: {', '.join(load_profile_ids())})")
    out: dict[tuple[int, str, int], float] = {}
    with (_PROFILE_DIR / f"{profile_id}.csv").open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out[(int(r["month"]), r["daytype"], int(r["hour"]))] = float(r["factor"])
    if len(out) != 12 * 2 * 24:
        raise PackError("load_profile_invalid", f"{profile_id}.csv is incomplete")
    return out


def _synthetic_load(idx: pd.DatetimeIndex, profile_id: str, annual_mwh: float) -> np.ndarray:
    f = _profile_factors(profile_id)
    shape = np.array([f[(t.month, "weekend" if t.weekday() >= 5 else "weekday", t.hour)]
                      for t in idx], dtype=float)
    return shape / shape.sum() * float(annual_mwh)


def _parse_series_bytes(blob: bytes) -> list[float]:
    """The last numeric column of a CSV, header rows skipped."""
    text = blob.decode("utf-8-sig", errors="replace")
    out: list[float] = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        try:
            out.append(float(row[-1]))
        except ValueError:
            if out:
                raise PackError("load_upload_invalid",
                                f"non-numeric value {row[-1]!r} after data started") from None
    return out


def _load_series(intake: Mapping[str, Any], idx: pd.DatetimeIndex,
                 resolve_upload: Callable[[str], bytes] | None) -> tuple[np.ndarray, list[str]]:
    load = intake.get("load")
    source = str(load.get("source"))
    if source in ("upload", "uploaded", "measured"):
        series = load.get("series_mw")
        if series is None and load.get("upload_id"):
            if resolve_upload is None:
                raise PackError("load_upload_unresolved",
                                "the load names an upload but no upload store was given")
            series = _parse_series_bytes(resolve_upload(str(load["upload_id"])))
        if not isinstance(series, (list, tuple)) or len(series) != HOURS:
            n = len(series) if isinstance(series, (list, tuple)) else 0
            raise PackError("load_upload_invalid",
                            f"an uploaded load needs {HOURS} hourly MW values; got {n}")
        arr = np.asarray(series, dtype=float)
        if not np.isfinite(arr).all() or (arr < 0).any():
            raise PackError("load_upload_invalid",
                            "the uploaded load has a negative or non-finite value")
        return arr, []
    profile = str(load.get("profile") or "commercial_office")
    try:
        annual = float(load.get("annual_mwh"))
    except (TypeError, ValueError):
        raise PackError("intake_incomplete",
                        "a sector-profile load needs annual_mwh") from None
    if not math.isfinite(annual) or annual <= 0:
        raise PackError("intake_invalid", "annual_mwh must be > 0")
    return _synthetic_load(idx, profile, annual), [f"synthetic_load_profile:{profile}"]


def _synthetic_pv(idx: pd.DatetimeIndex, latitude: float) -> np.ndarray:
    """
    A clear-sky-shaped PV profile with a monthly clearness derate: solar
    elevation from declination and hour angle, a stated shape, NOT weather.
    Marked ``synthetic_pv_profile`` wherever it is used.
    """
    phi = math.radians(latitude)
    doy = idx.dayofyear.to_numpy()
    hour = idx.hour.to_numpy() + 0.5
    decl = np.radians(23.45) * np.sin(2 * np.pi * (284 + doy) / 365.0)
    omega = np.radians(15.0 * (hour - 12.0))
    sin_alt = np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(omega)
    clearness = np.array([0.45, 0.50, 0.58, 0.64, 0.68, 0.70,
                          0.72, 0.70, 0.64, 0.56, 0.47, 0.42])[idx.month.to_numpy() - 1]
    return np.clip(np.maximum(sin_alt, 0.0) ** 1.15 * clearness, 0.0, 1.0)


# ── the network ───────────────────────────────────────────────────────────

def _option(question: DecisionQuestion, option_id: str) -> OptionSpec:
    try:
        return Q.option(question, option_id)
    except KeyError as exc:
        raise PackError("option_unknown", str(exc)) from None


def build_site_network(intake: Mapping[str, Any] | None,
                       ledger: AssumptionsLedger,
                       option_id: str = "none", *,
                       library: study_library.Library | None = None,
                       question: DecisionQuestion = Q.BESS_AT_SITE,
                       resolve_upload: Callable[[str], bytes] | None = None,
                       ) -> pypsa.Network:
    """
    The network of one option of the site question (see the module
    docstring for every rule). ``option_id="none"`` is the grid-only
    baseline. The pack's provenance (option, ledger hash, cost bases,
    upfront figures, honesty notes) is written to ``n.meta[PACK_META_KEY]``,
    which survives the netCDF round trip.
    """
    intake = intake or {}
    library = library or study_library.load_library()
    refuse_unrunnable_ledger(ledger)
    missing = missing_inputs(intake)
    if missing:
        raise PackError("intake_incomplete",
                        f"mandatory input(s) not answered: {', '.join(missing)}")
    opt = _option(question, option_id)
    if Q.PV in opt.free_assets and not Q.pv_enabled(intake):
        raise PackError("option_not_offered",
                        f"option {option_id!r} needs PV enabled at intake")

    idx = _snapshots(intake)
    tariff = effective_tariff(intake, ledger, library, idx)
    _t, tariff_notes = intake_tariff(intake, library)
    values = ledger_values(ledger)
    site = _site(intake)
    conn = _connection_mw(intake)
    multiple = _need(values, "sizing_limit_connection_multiple")
    p_nom_max = conn * multiple
    notes: list[str] = list(tariff_notes)

    n = pypsa.Network()
    n.name = f"site pack: {option_id}"
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 1.0
    for carrier in ("AC", "grid", "grid_import", "grid_export", "battery", "solar", "load"):
        n.add("Carrier", carrier)
    zone = str(site.get("zone")).strip()
    n.add("Bus", "site", carrier="AC", v_nom=1.0, country=zone)
    n.add("Bus", "grid", carrier="AC", v_nom=1.0, country=zone)
    n.add("Generator", "grid", bus="grid", carrier="grid", p_nom=conn,
          p_min_pu=-1.0, p_max_pu=1.0, marginal_cost=0.0, capital_cost=0.0,
          eh_role=GRID_SUPPLY)
    n.add("Link", IMPORT_LINK, bus0="grid", bus1="site", carrier="grid_import",
          p_nom=conn, efficiency=1.0, active=True, eh_role="grid_import")
    n.add("Link", EXPORT_LINK, bus0="site", bus1="grid", carrier="grid_export",
          p_nom=conn, efficiency=1.0, active=True, eh_role="grid_export")
    try:
        study_tariff.write_tariff_prices(n, tariff, IMPORT_LINK, EXPORT_LINK)
    except study_tariff.TariffError as exc:
        raise PackError(exc.code, str(exc)) from None

    load, load_notes = _load_series(intake, idx, resolve_upload)
    notes += load_notes
    n.add("Load", LOAD_NAME, bus="site", carrier="load")
    n.loads_t.p_set[LOAD_NAME] = load

    r = _need(values, "discount_rate")
    cost_basis: dict[str, str] = {}
    upfront: dict[str, Any] = {}
    if Q.PV in opt.free_assets:
        kind = str((intake.get("pv") or {}).get("kind") or "rooftop")
        if kind not in ("rooftop", "utility"):
            raise PackError("intake_invalid", f"pv.kind {kind!r} is rooftop or utility")
        pv_cost = _need(values, f"pv_{kind}_eur_per_kw") * 1000.0
        latitude = float(site.get("latitude", 51.0))
        n.add("Generator", PV_NAME, bus="site", carrier="solar",
              p_nom_extendable=True, p_nom_min=0.0, p_nom_max=p_nom_max,
              overnight_cost=pv_cost,
              lifetime=_need(values, f"pv_{kind}_lifetime_years"),
              discount_rate=r,
              fom_cost=_need(values, f"pv_{kind}_fom_pct_per_year") / 100.0 * pv_cost,
              marginal_cost=0.0)
        n.generators_t.p_max_pu[PV_NAME] = _synthetic_pv(idx, latitude)
        notes.append("synthetic_pv_profile")
        cost_basis[PV_NAME] = "overnight_cost_lifetime_discount_rate"
        upfront[PV_NAME] = {"total": pv_cost}

    hours = Q.max_hours(opt)
    if hours is not None:
        rte = round_trip_efficiency(ledger)
        eta = math.sqrt(rte)
        n.add("StorageUnit", BATTERY_NAME, bus="site", carrier="battery",
              p_nom_extendable=True, p_nom_min=0.0, p_nom_max=p_nom_max,
              max_hours=hours, cyclic_state_of_charge=True,
              efficiency_store=eta, efficiency_dispatch=eta,
              capital_cost=battery_capital_cost_eur_per_mw(ledger, hours),
              fom_cost=battery_fom_eur_per_mw(ledger),
              # Recorded, not used for the annuity: the storage block's
              # lifetime is the pro forma horizon. `overnight_cost` stays
              # unset — it cannot carry two lifetimes.
              lifetime=_need(values, "battery_storage_lifetime_years"),
              discount_rate=r, marginal_cost=0.0)
        cost_basis[BATTERY_NAME] = "derived_from_two_annuities"
        upfront[BATTERY_NAME] = battery_upfront_eur_per_mw(ledger, hours)
        notes.append("battery_fom_on_inverter_investment_only")

    from models.energy_hub import ImportOverlaySpec
    from services.adequacy.archetypes import select_import_links

    selected = select_import_links(n, ImportOverlaySpec())
    if selected != [IMPORT_LINK]:
        raise PackError("import_link_selection",
                        f"select_import_links returned {selected}, not ['{IMPORT_LINK}']")
    if not bool(n.links.at[IMPORT_LINK, "active"]):
        raise PackError("import_link_inactive", f"{IMPORT_LINK} is inactive")

    n.meta[PACK_META_KEY] = {
        "question_id": question.question_id,
        "option_id": option_id,
        "tariff_id": tariff.tariff_id,
        "ledger_hash": ledger_hash(ledger),
        "ledger_version": ledger.ledger_version,
        "connection_mw": conn,
        "p_nom_max_mw": p_nom_max,
        "max_hours": hours,
        "cost_basis": cost_basis,
        "upfront_eur_per_mw": upfront,
        "honesty_notes": notes,
    }
    return n


def option_solver_config(ledger: AssumptionsLedger, tariff: Tariff):
    """
    The explicit `SolverConfig` of an option fork (plan S4 M1): lopf, one
    flat year, the full strategy (no rolling, no myopic), no SCLOPF, no AC
    power flow, no user code, the demand charge from the ledger-applied
    tariff (one billing-period source), and the discount rate and the
    fallback lifetime from the ledger. Nothing is inherited from the base
    project's config.
    """
    from services.solver_service import SolverConfig

    v = ledger_values(ledger)
    return SolverConfig(
        solver_name="highs", mode="lopf", multi_investment_periods=False,
        solve_strategy="full", sclopf=False, run_ac_pf_after_lopf=False,
        extra_functionality_code="",
        discount_rate=_need(v, "discount_rate"),
        default_lifetime=_need(v, "battery_storage_lifetime_years"),
        demand_charge=study_tariff.demand_charge_config(tariff, [IMPORT_LINK]),
    )
