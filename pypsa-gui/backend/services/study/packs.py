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
* **Prices** are NOT network data (U2 WP6, plan §2 C2): the Links carry no
  tariff price. The tariff (the ledger's demand-charge price and energy-price
  level applied to the chosen tariff's structure) is compiled into the fork's
  ``SolverConfig.commercial`` (:func:`option_commercial`,
  :func:`option_solver_config`) and the Investment Case engine prices the PoC
  at solve time (``lp_bindings.materialise_poc_prices``) and carries the
  demand charge in the LP (``add_demand_terms``). The tariff is still
  validated here, so an unpriceable one refuses the build.
* **Load** from the intake: an upload (``series_mw`` or an ``upload_id``
  resolved by the caller) or a synthetic sector profile from
  ``study_library/load_profiles/`` scaled to the stated annual MWh (marked
  ``synthetic_load_profile``). Written to ``loads_t.p_set`` directly; the
  process-global ``_user_ts`` is never touched (OPEN-ITEMS 1).
* **PV** (only in an option whose ``free_assets`` has it): an extendable
  Generator on the ordinary ``overnight_cost`` + ``lifetime`` +
  ``discount_rate`` path, with a synthetic profile marked
  ``synthetic_pv_profile``. Rooftop or utility rows by ``intake.pv.kind``.
* **Battery** (review v1 B3/B4, spec §8.1 amended; U2 WP7 C1): a
  ``StorageUnit`` with ``p_nom_extendable``, the option's enumerated
  ``max_hours``, ``cyclic_state_of_charge`` (N3-v2), ``efficiency_store =
  efficiency_dispatch = sqrt(rte)`` from the ledger's ONE round-trip row,
  written through the asset schema's ``derive.apply_parts`` as TWO UPFRONT
  PARTS (:func:`battery_parts`, labelled ``two_upfront_parts``): power =
  the inverter (EUR/kW x 1000 per MW, the inverter lifetime, the inverter FOM
  share) and energy = the storage block (EUR/kWh x 1000 per MWh, scaled by
  ``max_hours``, the storage lifetime, no FOM row). ``apply_parts`` derives
  the columns the LP reads — ``capital_cost`` per MW, the SUM OF TWO
  ANNUITIES::

      annuity(r, inverter_life) x inverter_eur_per_kw x 1000
        + max_hours x annuity(r, storage_life) x storage_eur_per_kwh x 1000

  (= :func:`battery_capital_cost_eur_per_mw`), ``fom_cost`` = inverter FOM
  share x inverter investment (= :func:`battery_fom_eur_per_mw`) and
  ``lifetime`` = the longest part — and leaves ``overnight_cost`` EMPTY,
  because PyPSA annuitises a typed ``overnight_cost`` over ONE lifetime and
  then ignores ``capital_cost``. The finance engine reads the parts through
  ``asset_schema.access.upfront_parts`` (S0b); their sum is
  :func:`battery_upfront_eur_per_mw`.
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
import dataclasses
import functools
import hashlib
import io
import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import pandas as pd
import pypsa

from models.study import AssumptionsLedger, DecisionQuestion, OptionSpec, Tariff
from services.asset_schema import derive
from services.solver.periodized_costs import _annuity
from services.study import library as study_library
from services.study import questions as Q
from services.study import tariff as study_tariff

__all__ = [
    "HOURS", "LOAD_UNITS", "LoadUpload", "PackError", "PACK_META_KEY", "battery_capital_cost_eur_per_mw",
    "battery_fom_eur_per_mw", "battery_parts", "battery_upfront_eur_per_mw", "bind_option",
    "build_site_network",
    "effective_tariff", "option_commercial", "intake_tariff", "ledger_hash", "ledger_values",
    "load_profile_ids", "load_profiles", "needs_attention_rows", "read_intake_load", "snapshots_for", "option_solver_config", "parse_load_upload",
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


def battery_parts(ledger: AssumptionsLedger) -> dict[str, float]:
    """
    U2 WP7 C1: the battery's two upfront parts as the asset schema's
    StorageUnit part columns (`derive.apply_parts`), from the ledger rows
    1, 2, 4, 5 and 6: power = the inverter (EUR/MW, its lifetime, its FOM
    share), energy = the storage block (EUR/MWh, scaled by `max_hours` on
    the asset, its lifetime; FOM 0, the library has no storage FOM row).
    """
    v = ledger_values(ledger)
    return {
        "inv_power_overnight": _need(v, "battery_inverter_eur_per_kw") * 1000.0,
        "inv_power_lifetime": _need(v, "battery_inverter_lifetime_years"),
        "inv_power_fom_share": _need(v, "battery_inverter_fom_pct_per_year") / 100.0,
        "inv_energy_overnight": _need(v, "battery_storage_eur_per_kwh") * 1000.0,
        "inv_energy_lifetime": _need(v, "battery_storage_lifetime_years"),
        "inv_energy_fom_share": 0.0,
    }


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


def snapshots_for(intake: Mapping[str, Any] | None) -> pd.DatetimeIndex:
    """The study year's hourly snapshots (refuses a leap year); S8's preview."""
    return _snapshots(intake or {})


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


def load_profiles() -> list[dict[str, Any]]:
    """The sector profiles as the guided flow lists them (S8), in id order."""
    idx = _profile_index()
    return [{"profile_id": pid, "label": idx[pid]["label"], "source": idx[pid]["source"],
             "synthetic": str(idx[pid].get("synthetic", "")).strip().lower() == "true",
             "note": idx[pid].get("note") or ""}
            for pid in sorted(idx)]


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


# ── an uploaded load (S8; gate S4 [S6] carry) ─────────────────────────────
#
# A meter export is read with checks, never as "the last numeric column" on
# trust: one value column, optionally after one timestamp column; timestamps,
# when present, must be exactly the study year's 8760 hours in order; the
# unit comes from the value column's header (kW/MW, or kWh/MWh per hour) or
# from the intake's `load.unit`, and the two must agree; the series then goes
# through the same shape checks as every uploaded profile
# (`services/timeseries_qa.py::check_series`), whose findings ride along as
# codes (`load_upload_qa_<code>`) and as their sentences. None of the QA
# findings blocks: by the QA module's own policy the user is told and decides.

LOAD_UNITS = ("kW", "MW")
MAX_LOAD_BYTES = 25 * 1024 * 1024   # the upload route's per-file cap
# Any power or energy unit token in the value column's header (gate S8
# BC-S8-6): W/Wh/VA/var with an optional k/M/G/T prefix. Only kW(h) and MW(h)
# are read; any other named unit is refused, never overridden by the answer.
_HEADER_UNIT_RE = re.compile(r"(?<![a-z])([kmgt]?)(w|va|var)(h?)(?![a-z])", re.IGNORECASE)


@dataclasses.dataclass
class LoadUpload:
    """A parsed load upload: MW values, what was read and what was found."""

    values: np.ndarray
    unit: str
    has_timestamps: bool
    notes: list[str]
    warnings: list[dict[str, str]]


def _number(text: str) -> float | None:
    try:
        return float(str(text).strip())
    except ValueError:
        return None


def _header_unit(header: list[str] | None) -> str | None:
    if not header:
        return None
    m = _HEADER_UNIT_RE.search(str(header[-1]))
    if m is None:
        return None
    prefix, base = m.group(1).lower(), m.group(2).lower()
    if base == "w" and prefix in ("k", "m"):
        return "kW" if prefix == "k" else "MW"
    raise PackError("load_upload_unit_unsupported", (
        f"the file's header names the unit {m.group(0)!r}; give the load in kW or MW "
        "(kWh or MWh per hour)"))


def _check_timestamps(cells: list[str], year: int) -> None:
    expected = pd.date_range(f"{year}-01-01", periods=HOURS, freq="h")
    rule = (f"timestamps must be the {HOURS} hours of {year} in order, one per "
            f"hour from 1 January {year} 00:00, without gaps or repeats")
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(cells, format="mixed"))
    except (ValueError, TypeError) as exc:
        raise PackError("load_upload_timestamps_invalid",
                        f"a timestamp could not be read ({exc}); {rule}") from None
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    if len(idx) != HOURS:
        raise PackError("load_upload_timestamps_invalid",
                        f"the file has {len(idx)} rows; {rule}")
    bad = np.flatnonzero(idx.values != expected.values)
    if bad.size:
        i = int(bad[0])
        raise PackError("load_upload_timestamps_invalid", (
            f"row {i + 1} reads {idx[i]:%Y-%m-%d %H:%M} where {expected[i]:%Y-%m-%d %H:%M} "
            f"was expected; {rule}"))


def parse_load_upload(blob: bytes, *, unit: str | None, year: int) -> LoadUpload:
    """
    Read an uploaded load CSV into ``HOURS`` MW values (see the block
    comment above for the rules). Refusals are typed ``PackError`` codes:
    ``load_upload_invalid``, ``load_upload_timestamps_invalid``,
    ``load_upload_unit_unknown``, ``load_upload_unit_mismatch``.
    """
    from services.timeseries_qa import check_series

    if unit is not None and unit not in LOAD_UNITS:
        raise PackError("load_upload_invalid", f"load.unit is kW or MW, not {unit!r}")
    text = blob.decode("utf-8-sig", errors="replace")
    # Gate S8 re-verification BC-S8-v2-1: Excel for Mac's "CSV (Macintosh)"
    # ends lines with a bare CR, which `csv.reader` rejects mid-field; one
    # line ending for all three spellings, and a reader error is a refusal.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text))
                if r and any(c.strip() for c in r)]
    except csv.Error as exc:
        raise PackError("load_upload_invalid",
                        f"the file is not a readable CSV ({exc})") from None
    if not rows:
        raise PackError("load_upload_invalid", "the uploaded file is empty")
    header = None
    if _number(rows[0][-1]) is None:
        header, rows = rows[0], rows[1:]
    widths = {len(r) for r in rows}
    if not rows or not widths <= {1, 2} or len(widths) != 1:
        raise PackError("load_upload_invalid", (
            "the file must have one load column, optionally after one timestamp "
            f"column; found rows of {sorted(widths) or [0]} column(s)"))
    has_timestamps = widths == {2}
    values: list[float] = []
    for i, r in enumerate(rows):
        v = _number(r[-1])
        if v is None:
            raise PackError("load_upload_invalid",
                            f"row {i + 1}: {r[-1]!r} is not a number")
        values.append(v)
    if has_timestamps:
        _check_timestamps([r[0] for r in rows], year)
    elif len(values) != HOURS:
        raise PackError("load_upload_invalid",
                        f"an uploaded load needs {HOURS} hourly values; got {len(values)}")
    named = _header_unit(header)
    if named is not None and unit is not None and named != unit:
        raise PackError("load_upload_unit_mismatch", (
            f"the file's header says {named} but the answer says {unit}; "
            "correct one of them"))
    used = named or unit
    if used is None:
        raise PackError("load_upload_unit_unknown", (
            "the file's header names no unit; say whether the values are kW or MW"))
    arr = np.asarray(values, dtype=float)
    notes: list[str] = []
    if used == "kW":
        arr = arr / 1000.0
        notes.append("load_upload_converted_from_kw")
    if not has_timestamps:
        notes.append("load_upload_without_timestamps")
    if not np.isfinite(arr).all() or (arr < 0).any():
        raise PackError("load_upload_invalid",
                        "the uploaded load has a negative or non-finite value")
    issues = check_series("Load", LOAD_NAME, "p_set", arr)
    notes += [f"load_upload_qa_{i.code}" for i in issues]
    return LoadUpload(values=arr, unit=used, has_timestamps=has_timestamps, notes=notes,
                      warnings=[{"code": i.code, "message": i.message} for i in issues])


def _load_series(intake: Mapping[str, Any], idx: pd.DatetimeIndex,
                 resolve_upload: Callable[[str], bytes] | None) -> tuple[np.ndarray, list[str]]:
    read = read_intake_load(intake, idx, resolve_upload)
    return read.values, list(read.notes)


def read_intake_load(intake: Mapping[str, Any], idx: pd.DatetimeIndex,
                     resolve_upload: Callable[[str], bytes] | None) -> LoadUpload:
    """
    The intake's load as the pack reads it (S8: the preview route shows the
    user exactly this, QA findings included). Needs an answered load step.
    """
    load = intake.get("load")
    if not isinstance(load, Mapping) or not load.get("source"):
        raise PackError("intake_incomplete", "the load step has not been answered")
    source = str(load.get("source"))
    if source in ("upload", "uploaded", "measured"):
        series = load.get("series_mw")
        text = load.get("csv_text")
        if series is None and isinstance(text, str):
            # Gate S8 BC-S8-5: a draft's file travels in the intake until the
            # study is created, so no user project holds it before then.
            blob = text.encode("utf-8")
            if len(blob) > MAX_LOAD_BYTES:
                raise PackError("load_upload_invalid", (
                    f"the load file is larger than {MAX_LOAD_BYTES // (1024 * 1024)} MB"))
            return parse_load_upload(blob, unit=load.get("unit"), year=int(idx[0].year))
        if series is None and load.get("upload_id"):
            if resolve_upload is None:
                raise PackError("load_upload_unresolved",
                                "the load names an upload but no upload store was given")
            return parse_load_upload(resolve_upload(str(load["upload_id"])),
                                     unit=load.get("unit"), year=int(idx[0].year))
        if not isinstance(series, (list, tuple)) or len(series) != HOURS:
            n = len(series) if isinstance(series, (list, tuple)) else 0
            raise PackError("load_upload_invalid",
                            f"an uploaded load needs {HOURS} hourly MW values; got {n}")
        arr = np.asarray(series, dtype=float)
        if not np.isfinite(arr).all() or (arr < 0).any():
            raise PackError("load_upload_invalid",
                            "the uploaded load has a negative or non-finite value")
        return LoadUpload(values=arr, unit="MW", has_timestamps=False, notes=[], warnings=[])
    profile = str(load.get("profile") or "commercial_office")
    try:
        annual = float(load.get("annual_mwh"))
    except (TypeError, ValueError):
        raise PackError("intake_incomplete",
                        "a sector-profile load needs annual_mwh") from None
    if not math.isfinite(annual) or annual <= 0:
        raise PackError("intake_invalid", "annual_mwh must be > 0")
    return LoadUpload(values=_synthetic_load(idx, profile, annual), unit="MW",
                      has_timestamps=True, notes=[f"synthetic_load_profile:{profile}"],
                      warnings=[])


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
    # The tariff the engine will price must compile (U2 WP6): its refusals
    # (an unpriced export, an unsupported basis) still refuse the build.
    option_commercial(intake, ledger, library, idx)
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
              discount_rate=r, marginal_cost=0.0)
        # C1: the two parts; `apply_parts` writes the two-annuity
        # `capital_cost`, the FOM and the lifetime (the storage block's: the
        # case horizon) and leaves `overnight_cost` empty.
        derive.apply_parts(n, "StorageUnit", BATTERY_NAME, battery_parts(ledger),
                           discount_rate=r)
        cost_basis[BATTERY_NAME] = "two_upfront_parts"
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


def option_commercial(intake: Mapping[str, Any] | None, ledger: AssumptionsLedger,
                      library, snapshots: pd.DatetimeIndex | None = None, *,
                      export_series=None):
    """
    The study's compiled commercial config (U2 WP6; `compile.
    commercial_from_ledger`): the intake's tariff with the ledger applied, on
    the study year's axis, its export price as the study's minted series
    (`export_series`, a `PriceSeriesRef` or its dict; None leaves it
    unminted, which refuses a bind). A compile refusal is a `PackError` with
    the compiler's code.
    """
    from services.study import compile as study_compile

    idx = snapshots if snapshots is not None else _snapshots(intake or {})
    try:
        return study_compile.commercial_from_ledger(intake, ledger, library, idx,
                                                    export_series=export_series)
    except study_compile.CompileError as exc:
        raise PackError(exc.code, exc.message) from None


def bind_option(n: pypsa.Network, compiled, *, resolve_ref):
    """
    C6: the compiled config bound on an option's in-memory network
    (`compile.bind_on_network`) before its fork is written; `PackError` on a
    refusal.
    """
    from services.study import compile as study_compile

    try:
        return study_compile.bind_on_network(n, compiled, resolve_ref=resolve_ref)
    except study_compile.CompileError as exc:
        raise PackError(exc.code, exc.message) from None


def option_solver_config(ledger: AssumptionsLedger, compiled):
    """
    The explicit `SolverConfig` of an option fork (plan S4 M1; U2 WP6 C6):
    `compile.solver_config` — lopf, one flat year, the full strategy, no
    SCLOPF, no AC power flow, no user code, the discount rate and the
    fallback lifetime from the ledger, and the compiled commercial config
    (`option_commercial`) that prices the LP. Nothing is inherited from the
    base project's config.
    """
    from services.study import compile as study_compile

    try:
        return study_compile.solver_config(ledger, compiled)
    except study_compile.CompileError as exc:
        raise PackError(exc.code, exc.message) from None
