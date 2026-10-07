"""
The assumptions library (guided investment study MVP-1, phase S2).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S2)
Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md §4.3

Three vendored files under ``backend/study_library/`` (the runtime never
downloads):

* ``technology_costs.csv``: rows transcribed from technology-data v0.14.0
  (``outputs/costs_2030.csv``; the extraction is kept beside the plan as
  ``docs/superpowers/notes/2026-09-28-mvp1-s2-seed-rows-technology-data-v0.14.0.csv``),
  with a ±30 % range marked ``assumed``; one derived row (round-trip
  efficiency = inverter efficiency squared, ``source=derived``) and
  degradation placeholders (``note=not_used_in_mvp1``, no value).
* ``tariffs.csv``: two illustrative seed tariffs, one row per component,
  assembled into :class:`models.study.Tariff`.
* ``finance_defaults.yaml``: the real discount rate, the basis, the
  perspective, the currency year and the horizon/replacement rules.

:func:`seed_ledger` turns the library plus the study's intake into an
:class:`AssumptionsLedger` whose every row is ``provenance=library,
status=default``, with ``sensitivity_flag`` set on the question's key drivers.
"""
from __future__ import annotations

import csv
import functools
import math
import pathlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import yaml

from models.study import (
    AssumptionsLedger,
    DecisionQuestion,
    LedgerDomain,
    LedgerRange,
    LedgerRow,
    Tariff,
)

__all__ = [
    "BESS_KEY_DRIVERS", "DEFAULTS_VERSION_PREFIX", "DERIVED", "ENGINE_PATHS",
    "ENGINE_ROWS", "INTAKE_DERIVED_ROWS", "LIBRARY_DIR", "LIBRARY_VERSION", "Library",
    "LibraryError", "RULE_DESCRIPTOR", "TechnologyRow", "UnknownLibraryVersion",
    "horizon_and_replacements", "key_drivers_of", "load_defaults", "load_library",
    "seed_ledger",
]

LIBRARY_VERSION = "technology-data v0.14.0"
LIBRARY_DIR = pathlib.Path(__file__).resolve().parents[2] / "study_library"

# The BESS question's key drivers. S4's template
# (`services/study/questions.py::BESS_AT_SITE.key_drivers`) is THE list and
# the routes seed from it; this alias is kept for the S2 tests and held equal
# to the template by `tests/test_study_questions.py` (gate S2 carry).
BESS_KEY_DRIVERS: tuple[str, ...] = (
    "battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw",
    "demand_charge_price", "energy_price_level", "discount_rate",
)

TECH_COLUMNS = (
    "technology", "parameter", "value", "unit", "basis", "currency_year",
    "source", "source_year", "source_url", "range_low", "range_high",
    "range_source", "domain", "projection_year", "note",
)
TARIFF_COLUMNS = (
    "tariff_id", "name", "source", "source_year", "currency", "currency_year",
    "billing_period", "component", "label", "price", "unit", "basis",
    "months", "weekdays", "hours", "note",
)

# (technology, parameter) -> (ledger key, plain label, technical name).
# A library row with no entry here is refused at load: a key is a contract
# the pack (S4), the pro forma (S5) and the tornado (S6) read by name.
_TECH_KEYS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("battery inverter", "investment"): (
        "battery_inverter_eur_per_kw", "Battery inverter cost",
        "StorageUnit capital_cost, power part (derived_from_two_annuities)"),
    ("battery inverter", "FOM"): (
        "battery_inverter_fom_pct_per_year", "Battery inverter fixed O&M",
        "fom_cost (share of investment per year)"),
    ("battery inverter", "efficiency"): (
        "battery_inverter_efficiency", "Battery inverter efficiency (one way)",
        # Gate S2 [S10]: the pack reads the round-trip row (sqrt(rte) on each
        # side); this row only feeds it while that row is a library default.
        "input to battery_round_trip_efficiency (the pack reads that row)"),
    ("battery inverter", "lifetime"): (
        "battery_inverter_lifetime_years", "Battery inverter lifetime",
        "lifetime (inverter annuity; replacement interval)"),
    ("battery storage", "investment"): (
        "battery_storage_eur_per_kwh", "Battery storage (energy) cost",
        "StorageUnit capital_cost, energy part x max_hours "
        "(derived_from_two_annuities)"),
    ("battery storage", "lifetime"): (
        "battery_storage_lifetime_years", "Battery storage lifetime",
        "lifetime (storage annuity; pro forma horizon)"),
    ("battery", "round-trip efficiency"): (
        "battery_round_trip_efficiency", "Battery round-trip efficiency",
        "efficiency_store = efficiency_dispatch = sqrt(value)"),
    ("battery storage", "degradation calendar"): (
        "battery_storage_degradation_calendar_pct_per_year",
        "Battery capacity fade per year (not modelled in MVP-1)", "-"),
    ("battery storage", "degradation cycling"): (
        "battery_storage_degradation_cycling_pct_per_cycle",
        "Battery capacity fade per full cycle (not modelled in MVP-1)", "-"),
    ("solar-utility", "investment"): (
        "pv_utility_eur_per_kw", "Solar PV (ground-mounted) cost", "overnight_cost"),
    ("solar-utility", "FOM"): (
        "pv_utility_fom_pct_per_year", "Solar PV (ground-mounted) fixed O&M",
        "fom_cost (share of investment per year)"),
    ("solar-utility", "lifetime"): (
        "pv_utility_lifetime_years", "Solar PV (ground-mounted) lifetime", "lifetime"),
    ("solar-rooftop", "investment"): (
        "pv_rooftop_eur_per_kw", "Solar PV (rooftop) cost", "overnight_cost"),
    ("solar-rooftop", "FOM"): (
        "pv_rooftop_fom_pct_per_year", "Solar PV (rooftop) fixed O&M",
        "fom_cost (share of investment per year)"),
    ("solar-rooftop", "lifetime"): (
        "pv_rooftop_lifetime_years", "Solar PV (rooftop) lifetime", "lifetime"),
}

# Derived rows: key -> (input keys, formula). A derived row that is still a
# library default follows its inputs when they are edited
# (`ledger.refresh_derived`); once the user sets it, it is theirs.
DERIVED: Mapping[str, tuple[tuple[str, ...], Callable[..., float]]] = MappingProxyType({
    "battery_round_trip_efficiency": (
        ("battery_inverter_efficiency",), lambda eff: eff * eff),
})

NOT_USED = "not_used_in_mvp1"

# One meaning for `energy_price_level` (gate S2 [S8], pinned in S4): a
# dimensionless multiplier on the tariff's ENERGY BANDS around their
# time-weighted mean over the modelled snapshots,
#     band_price' = mean + level x (band_price - mean),
# so 1.0 is the tariff as written, 0 would flatten every band to the mean and
# 2 doubles each band's distance from it. It changes the time-of-use spread,
# not the average price, and is therefore a no-op on a single-band (flat)
# tariff. Per-MWh network charges, the demand charge and the export credit are
# NOT scaled. Applied in one place, `services/study/tariff.py::
# tariff_from_ledger`, which the pack, the LP config and the bill all read.
ENERGY_PRICE_LEVEL_LABEL = (
    "Energy price level (multiplier on the tariff's energy bands around "
    "their time-weighted mean)")
ENERGY_PRICE_LEVEL_TECHNICAL = (
    "links_t.marginal_cost on grid_import: band = mean + value x (band - mean); "
    "network charges, demand charge and export credit unscaled")
ENERGY_PRICE_LEVEL_HELP = (
    "Dimensionless, default 1.0 (the tariff as written). Scales each energy "
    "band's distance from the bands' time-weighted mean, so it widens (above "
    "1) or narrows (below 1) the time-of-use spread without moving the average "
    "energy price; on a single-band tariff it changes nothing. Network "
    "charges, the demand charge and the export price are not scaled.")


class LibraryError(ValueError):
    """A vendored library file (or an intake reference into it) is invalid."""


class UnknownLibraryVersion(LookupError):
    """Only the vendored version can be loaded; nothing is downloaded."""


@dataclass(frozen=True)
class TechnologyRow:
    key: str
    label: str
    technical_name: str
    technology: str
    parameter: str
    value: float | None
    unit: str
    basis: str
    currency_year: int | None
    source: str
    source_year: int | None
    source_url: str | None
    range_low: float | None
    range_high: float | None
    range_source: str | None
    note: str
    # Gate S2: the physical domain (BC-S2-3) and the year technology-data
    # projected the cost for (BC-S2-5; `costs_2030.csv` rows are 2030).
    domain: LedgerDomain | None = None
    projection_year: int | None = None
    # U2 WP3: the defaults pack's flag (None from the legacy CSV).
    illustrative: bool | None = None


@dataclass(frozen=True)
class Library:
    version: str
    technology: tuple[TechnologyRow, ...]
    tariffs: Mapping[str, Tariff]
    finance: Mapping[str, Any]
    default_tariff_id: str
    directory: pathlib.Path = field(default=LIBRARY_DIR, compare=False)
    # U2 WP3: the generic defaults pack this view was read from (None for the
    # legacy `study_library/` files), and each pack tariff's `illustrative`.
    pack: Any = field(default=None, compare=False)
    tariff_illustrative: Mapping[str, bool] = field(default_factory=dict, compare=False)


# ── parsing ───────────────────────────────────────────────────────────────

def _opt_float(text: str, where: str) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise LibraryError(f"{where}: {text!r} is not a number") from None
    if not math.isfinite(value):
        raise LibraryError(f"{where}: {text!r} is not finite")
    return value


def _opt_int(text: str, where: str) -> int | None:
    value = _opt_float(text, where)
    return None if value is None else int(value)


def _read_csv(path: pathlib.Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != columns:
            raise LibraryError(
                f"{path.name}: header {reader.fieldnames} != {list(columns)}")
        return list(reader)


def _parse_technology(path: pathlib.Path) -> tuple[TechnologyRow, ...]:
    out: list[TechnologyRow] = []
    for line, raw in enumerate(_read_csv(path, TECH_COLUMNS), start=2):
        where = f"{path.name} line {line}"
        ident = (raw["technology"], raw["parameter"])
        if ident not in _TECH_KEYS:
            raise LibraryError(f"{where}: no ledger key for {ident}")
        key, label, technical = _TECH_KEYS[ident]
        value = _opt_float(raw["value"], where)
        note = raw["note"].strip()
        if value is None and note != NOT_USED:
            raise LibraryError(f"{where}: {key} has no value and is not "
                               f"marked {NOT_USED}")
        if not raw["unit"].strip() or not raw["source"].strip():
            raise LibraryError(f"{where}: {key} needs a unit and a source")
        low = _opt_float(raw["range_low"], where)
        high = _opt_float(raw["range_high"], where)
        if (low is None) != (high is None):
            raise LibraryError(f"{where}: {key} has half a range")
        try:
            domain = LedgerDomain.parse(raw["domain"])
        except ValueError as exc:
            raise LibraryError(f"{where}: {key} domain: {exc}") from None
        for label_, v in (("value", value), ("range_low", low), ("range_high", high)):
            if v is not None and not domain.contains(v):
                raise LibraryError(f"{where}: {key} {label_} {v} is outside {domain}")
        out.append(TechnologyRow(
            key=key, label=label, technical_name=technical,
            technology=ident[0], parameter=ident[1], value=value,
            unit=raw["unit"].strip(), basis=raw["basis"].strip() or "real",
            currency_year=_opt_int(raw["currency_year"], where),
            source=raw["source"].strip(),
            source_year=_opt_int(raw["source_year"], where),
            source_url=raw["source_url"].strip() or None,
            range_low=low, range_high=high,
            range_source=raw["range_source"].strip() or None, note=note,
            domain=domain,
            projection_year=_opt_int(raw["projection_year"], where),
        ))
    by_key = {r.key: r for r in out}
    if len(by_key) != len(out):
        raise LibraryError(f"{path.name}: duplicate ledger keys")
    # A derived row in the file must agree with its formula, so the vendored
    # number and the refresh rule cannot drift apart.
    for key, (inputs, fn) in DERIVED.items():
        row = by_key.get(key)
        if row is None:
            continue
        if row.source != "derived":
            raise LibraryError(f"{path.name}: {key} must be source=derived")
        expected = fn(*(by_key[i].value for i in inputs))
        if not math.isclose(row.value, expected, rel_tol=0, abs_tol=5e-5):
            raise LibraryError(
                f"{path.name}: {key}={row.value} but its formula gives {expected}")
    return tuple(out)


def _int_list(text: str, where: str) -> list[int]:
    """``"0-4;7"`` -> ``[0, 1, 2, 3, 4, 7]``; empty means every."""
    out: list[int] = []
    for part in (text or "").replace(" ", "").split(";"):
        if not part:
            continue
        try:
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                out.extend(range(a, b + 1))
            else:
                out.append(int(part))
        except ValueError:
            raise LibraryError(f"{where}: {text!r} is not a list of integers") from None
    return out


_NOTE_CODE_RE = re.compile(r"[a-z]+(_[a-z]+)*")


def _parse_tariffs(path: pathlib.Path) -> dict[str, Tariff]:
    grouped: dict[str, dict[str, Any]] = {}
    for line, raw in enumerate(_read_csv(path, TARIFF_COLUMNS), start=2):
        where = f"{path.name} line {line}"
        tid = raw["tariff_id"].strip()
        t = grouped.setdefault(tid, {
            "tariff_id": tid, "name": raw["name"].strip(),
            "source": raw["source"].strip(),
            "source_year": _opt_int(raw["source_year"], where),
            "currency": raw["currency"].strip(),
            "currency_year": _opt_int(raw["currency_year"], where),
            "billing_period": raw["billing_period"].strip(),
            "energy_bands": [], "network_charges": [], "honesty_notes": [],
            "honesty_help": {},
        })
        kind, label = raw["component"].strip(), raw["label"].strip()
        price = _opt_float(raw["price"], where)
        if kind != "honesty_note" and price is None:
            raise LibraryError(f"{where}: {tid} {kind} has no price")
        if kind == "energy_band":
            t["energy_bands"].append({"label": label, "price_per_mwh": price, "applies": {
                "months": _int_list(raw["months"], where),
                "weekdays": _int_list(raw["weekdays"], where),
                "hours": _int_list(raw["hours"], where)}})
        elif kind == "demand_charge":
            t["demand_charge"] = {"price_per_mw_per_period": price,
                                  "basis": raw["basis"].strip()}
        elif kind == "capacity_charge":
            t["capacity_charge"] = {"price_per_mw_per_year": price,
                                    "basis": raw["basis"].strip()}
        elif kind == "fixed_charge":
            t["fixed_charge_per_period"] = price
        elif kind == "network_charge":
            t["network_charges"].append({"label": label, "price": price,
                                         "basis": raw["basis"].strip()})
        elif kind == "export":
            t["export"] = {"price_per_mwh": price}
        elif kind == "connection_limit":
            t["connection_limit_mw"] = price
        elif kind == "honesty_note":
            # Gate S5 BC-S5-3: the code (label column) travels into the case
            # and the report; the sentence (note column) is its help text.
            if not _NOTE_CODE_RE.fullmatch(label):
                raise LibraryError(
                    f"{where}: {tid} honesty_note needs a snake_case code without "
                    f"digits in the label column, got {label!r}")
            t["honesty_notes"].append(label)
            t["honesty_help"][label] = raw["note"].strip()
        else:
            raise LibraryError(f"{where}: unknown tariff component {kind!r}")
    try:
        return {tid: Tariff.model_validate(data) for tid, data in grouped.items()}
    except ValueError as exc:
        raise LibraryError(f"{path.name}: {exc}") from exc


@functools.lru_cache(maxsize=4)
def _load(version: str, directory: pathlib.Path) -> Library:
    finance = yaml.safe_load((directory / "finance_defaults.yaml").read_text(encoding="utf-8"))
    if finance.get("library_version") != version:
        raise LibraryError(
            f"finance_defaults.yaml is for {finance.get('library_version')!r}, "
            f"not {version!r}")
    tariffs = _parse_tariffs(directory / "tariffs.csv")
    if not tariffs:
        raise LibraryError("tariffs.csv holds no tariff")
    return Library(
        version=version,
        technology=_parse_technology(directory / "technology_costs.csv"),
        tariffs=MappingProxyType(tariffs),
        finance=MappingProxyType(finance),
        default_tariff_id=next(iter(tariffs)),
        directory=directory,
    )


def load_library(version: str = LIBRARY_VERSION) -> Library:
    """
    The vendored library. Only :data:`LIBRARY_VERSION` exists; any other
    version is refused rather than fetched.
    """
    if version != LIBRARY_VERSION:
        raise UnknownLibraryVersion(
            f"assumptions library {version!r} is not vendored; this build "
            f"carries {LIBRARY_VERSION!r} only")
    return _load(version, LIBRARY_DIR)


# ── the generic defaults pack (U2 WP3) ────────────────────────────────────
#
# Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §1,
# §6. IC's generic defaults pack (U1 follow-up a, `services/library/
# defaults_pack/`) is the source of every default; `load_defaults` reads it
# into the same `Library` view the ledger, the pack builder and the routes
# take, so seeding is one code path. The pack states values in its own units
# (EUR/MW, share/year) and keeps each catalogue figure as `original_value` /
# `original_unit`: the guided ledger keeps the catalogue's (EUR/kW, %/year),
# the units its users type, converting the range and the domain back through
# the row's own `conversion_factor`.

DEFAULTS_VERSION_PREFIX = "generic-defaults"

# Pack value key -> the GS identity in `_TECH_KEYS`, in the ledger's row order
# (the order of the legacy `technology_costs.csv`, so a seeded ledger lists
# its rows as WP0's did).
_PACK_TECH_ROWS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("battery.power.overnight", ("battery inverter", "investment")),
    ("battery.power.fom_share", ("battery inverter", "FOM")),
    ("battery.power.efficiency", ("battery inverter", "efficiency")),
    ("battery.power.lifetime", ("battery inverter", "lifetime")),
    ("battery.energy.overnight", ("battery storage", "investment")),
    ("battery.energy.lifetime", ("battery storage", "lifetime")),
    ("solar-utility.investment.overnight", ("solar-utility", "investment")),
    ("solar-utility.investment.fom_share", ("solar-utility", "FOM")),
    ("solar-utility.investment.lifetime", ("solar-utility", "lifetime")),
    ("solar-rooftop.investment.overnight", ("solar-rooftop", "investment")),
    ("solar-rooftop.investment.fom_share", ("solar-rooftop", "FOM")),
    ("solar-rooftop.investment.lifetime", ("solar-rooftop", "lifetime")),
    ("battery.round_trip_efficiency", ("battery", "round-trip efficiency")),
    ("battery.energy.degradation_calendar", ("battery storage", "degradation calendar")),
    ("battery.energy.degradation_cycling", ("battery storage", "degradation cycling")),
)
# Pack rows the guided ledger does not seed, each with the reason. Any other
# unmapped pack technology row is refused at load (a key is a contract).
_PACK_ROWS_NOT_SEEDED = {
    # The guided pack books the battery's FOM on the inverter (power) part only.
    "battery.energy.fom_share": "fom_booked_on_power_part",
}
_PACK_FINANCE_KEYS = {"finance.discount_rate": "discount_rate",
                      "finance.sizing_limit_connection_multiple":
                          "sizing_limit_connection_multiple"}

# The engine field each guided ledger row compiles to (plan §1.1 and §1.2;
# `compile.py` is the only reader). Rows that only feed another row or are
# not compiled carry None. Not hashed (`packs.ledger_hash`).
ENGINE_PATHS: Mapping[str, str | None] = MappingProxyType({
    "battery_inverter_eur_per_kw": "network.StorageUnit.battery.inv_power_overnight",
    "battery_inverter_fom_pct_per_year": "network.StorageUnit.battery.fom_cost",
    "battery_inverter_efficiency": None,
    "battery_inverter_lifetime_years": "network.StorageUnit.battery.inv_power_lifetime",
    "battery_storage_eur_per_kwh": "network.StorageUnit.battery.inv_energy_overnight",
    "battery_storage_lifetime_years": "network.StorageUnit.battery.inv_energy_lifetime",
    "pv_utility_eur_per_kw": "network.Generator.pv.overnight_cost",
    "pv_utility_fom_pct_per_year": "network.Generator.pv.fom_cost",
    "pv_utility_lifetime_years": "network.Generator.pv.lifetime",
    "pv_rooftop_eur_per_kw": "network.Generator.pv.overnight_cost",
    "pv_rooftop_fom_pct_per_year": "network.Generator.pv.fom_cost",
    "pv_rooftop_lifetime_years": "network.Generator.pv.lifetime",
    "battery_round_trip_efficiency": "network.StorageUnit.battery.efficiency_store",
    "battery_storage_degradation_calendar_pct_per_year": None,
    "battery_storage_degradation_cycling_pct_per_cycle": None,
    "tariff": "commercial.import_tariff",
    "demand_charge_price": "commercial.import_tariff.items[demand].periods[0].rate",
    "energy_price_level": "commercial.import_tariff.items[energy].periods[*].rate",
    "sizing_limit_connection_multiple": "network.p_nom_max",
    "discount_rate": "solver_config.discount_rate",
    "financial_close_year": "finance.financial_close",
    "cod_year": "finance.cod_by_asset",
    "contingency_share": "finance.contingency_share",
    **{f"escalation_{c}": f"finance.escalation.{c}"
       for c in ("tariff", "export", "opex", "capex", "fuel", "ppa")},
    "inflation": "finance.inflation",
    "cost_of_equity_rule": "finance.cost_of_equity",
    "analysis_years_rule": "finance.analysis_years",
    "value_flows_template": "commercial.value_flows",
    "salvage_rule": "finance.terminal_value",
    "pv_degradation_pct_per_year": "finance.degradation_by_asset.pv",
    "tax_pack": "finance.tax_pack_id",
    "incentives_rule": "finance.incentives",
    "export_series": "commercial.export_price_ref",
    "export_cap_mw": "commercial.connection.export_cap_mw",
})

# Rows 21-34's source until IC's pack carries the rules (Q7; gate U2-S1 C4),
# and the year the U2 sub-plan stated them.
GUIDED_RULE_SOURCE = "guided study (pending pack rule, Q7)"
GUIDED_RULES_YEAR = 2026

# A descriptor row: no number, a rule the pack states (`apply_user_row`
# refuses it, as it refuses the tariff descriptor).
RULE_DESCRIPTOR = "rule_descriptor"
# Rows 21-22 and 34 carry a number set by a pack rule from the intake (the
# modelled year, the tariff's export cap): shown, never typed over.
INTAKE_DERIVED_ROWS = frozenset({"financial_close_year", "cod_year", "export_cap_mw"})
_ESCALATION_CLASSES = ("tariff", "export", "opex", "capex", "fuel", "ppa")
# Rows 21-34 in ledger order (row 34 only when the tariff states a cap).
ENGINE_ROWS: tuple[str, ...] = (
    "financial_close_year", "cod_year", "contingency_share",
    *(f"escalation_{c}" for c in _ESCALATION_CLASSES), "inflation", "cost_of_equity_rule",
    "analysis_years_rule", "value_flows_template", "salvage_rule",
    "pv_degradation_pct_per_year", "tax_pack", "incentives_rule", "export_series",
    "export_cap_mw")


def _scaled(x: float | None, factor: float) -> float | None:
    """
    A pack-unit figure back in the catalogue's unit (rounded off the float
    noise of the division, far below any stated digit).
    """
    return None if x is None else round(float(x) / factor, 9)


def _domain_in(text: str | None, factor: float) -> LedgerDomain | None:
    if not text:
        return None
    d = LedgerDomain.parse(text)
    return d.model_copy(update={"low": _scaled(d.low, factor),
                                "high": _scaled(d.high, factor)})


def _pack_technology(pack, finance_year: int) -> tuple[TechnologyRow, ...]:
    by_key = {v.key: v for v in pack.cost_values}
    mapped = {k for k, _ in _PACK_TECH_ROWS} | set(_PACK_ROWS_NOT_SEEDED)
    # Strict on the technologies the guided study reads (a new battery or PV
    # row must be mapped or named not seeded); rows of other technologies
    # belong to another face (IC's campus equipment, pack 2026-10-07).
    ours = {by_key[k].technology for k in mapped if k in by_key}
    stray = sorted(k for k, v in by_key.items() if v.technology in ours and k not in mapped)
    if stray:
        raise LibraryError(f"defaults pack {pack.version}: no ledger key for {stray}")
    out: list[TechnologyRow] = []
    for pack_key, ident in _PACK_TECH_ROWS:
        v = by_key.get(pack_key)
        if v is None:
            raise LibraryError(f"defaults pack {pack.version} has no {pack_key!r} row")
        key, label, technical = _TECH_KEYS[ident]
        f = float(v.conversion_factor) or 1.0
        value = v.original_value
        out.append(TechnologyRow(
            key=key, label=label, technical_name=technical, technology=ident[0],
            parameter=ident[1], value=value, unit=v.original_unit,
            basis=v.price_basis or "real",
            # The legacy files stated the study's currency year on every valued
            # technology row; the pack states it on money rows only.
            currency_year=(v.currency_year if v.currency_year is not None
                           else (finance_year if value is not None else None)),
            source="derived" if v.derived is not None else v.source,
            source_year=v.source_year if value is not None or v.source_url else None,
            source_url=v.source_url,
            range_low=_scaled(v.range.low, f) if v.range else None,
            range_high=_scaled(v.range.high, f) if v.range else None,
            range_source=v.range.source if v.range else None,
            note=v.note or "", domain=_domain_in(v.domain, f),
            projection_year=v.projection_year, illustrative=v.illustrative))
    return tuple(out)


def _gs_tariff_from_pack(pt) -> Tariff:
    """
    The guided form (`models.study.Tariff`) of a pack tariff, from the
    pack's seed rows (each item keeps the seed's own price and unit) and the IC
    periods they name. The form is what the guided tariff step shows (owner
    decision 8); `compile.py` turns it back into the engine's `Tariff`.
    """
    meta, items = pt.meta, {i.id: i for i in pt.tariff.items}
    data: dict[str, Any] = {
        "tariff_id": pt.tariff.id, "name": pt.tariff.name,
        # GS's convention for a seed: the string `illustrative` (the maturity
        # badge and the report read it); the pack's prose source is kept in
        # the descriptor row's help.
        "source": "illustrative" if meta.illustrative else meta.source,
        "source_year": meta.source_year, "currency": meta.currency,
        "currency_year": meta.currency_year, "billing_period": meta.billing_period,
        "energy_bands": [], "network_charges": [],
        "honesty_notes": [h.code for h in meta.honesty],
        "honesty_help": {h.code: h.help for h in meta.honesty},
        "export": {"price_per_mwh": meta.export.price_per_mwh,
                   "series_ref": meta.export.series, "cap_mw": meta.export.cap_mw},
    }
    for item_id, rows in meta.items.items():
        item = items[item_id]
        for row in rows:
            if row.component == "energy_band":
                periods = [p for p in item.periods if p.name in row.periods]
                hours = sorted({h for p in periods if p.start_hour is not None
                                for h in range(p.start_hour, p.end_hour)})
                data["energy_bands"].append({"label": row.label,
                                             "price_per_mwh": row.original_price,
                                             "applies": {"months": list(periods[0].months),
                                                         "weekdays": list(periods[0].weekdays),
                                                         "hours": hours}})
            elif row.component == "network_charge":
                data["network_charges"].append({"label": row.label,
                                                "price": row.original_price,
                                                "basis": row.basis})
            elif row.component == "demand_charge":
                data["demand_charge"] = {"price_per_mw_per_period": row.original_price,
                                         "basis": row.basis or "billing_period_peak"}
            elif row.component == "capacity_charge":
                data["capacity_charge"] = {"price_per_mw_per_year": row.original_price,
                                           "basis": row.basis or "contracted"}
            elif row.component == "fixed_charge":
                data["fixed_charge_per_period"] = row.original_price
            else:
                raise LibraryError(f"pack tariff {pt.tariff.id}: unknown seed component "
                                   f"{row.component!r}")
    try:
        return Tariff.model_validate(data)
    except ValueError as exc:
        raise LibraryError(f"pack tariff {pt.tariff.id}: {exc}") from exc


def _pack_key_to_gs(pack_key: str) -> str:
    for k, ident in _PACK_TECH_ROWS:
        if k == pack_key:
            return _TECH_KEYS[ident][0]
    raise LibraryError(f"pack rule names {pack_key!r}, which the guided ledger does not seed")


def _finance_spec(v) -> dict[str, Any]:
    return {"key": _PACK_FINANCE_KEYS[v.key], "value": v.value, "unit": v.unit,
            "basis": v.price_basis or "real", "source": v.source,
            "source_year": v.source_year, "source_url": v.source_url,
            "range": ({"low": v.range.low, "high": v.range.high, "source": v.range.source}
                      if v.range else {}),
            "domain": v.domain, "illustrative": v.illustrative}


@functools.lru_cache(maxsize=4)
def _load_defaults(version: str | None) -> Library:
    from services.library.defaults_pack.loader import load_defaults_pack

    pack = load_defaults_pack(version)
    fin = pack.finance
    label = f"{DEFAULTS_VERSION_PREFIX} {pack.version} {pack.hash[:12]}"
    [rule] = fin.replacement_rules
    finance = {
        "library_version": label, "pack_stamp": pack.stamp, "seed_library": fin.seed_library,
        "currency": fin.currency, "currency_year": fin.currency_year,
        "currency_year_source": fin.currency_year_source,
        "basis": fin.basis.model_dump(), "basis_source": fin.basis_source,
        "perspective": fin.perspective, "perspective_source": fin.perspective_source,
        "discount_rate": _finance_spec(fin.discount_rate),
        "horizon_rule": {"horizon_years": _pack_key_to_gs(fin.horizon_rule.horizon_years),
                         "source": fin.horizon_rule.source},
        "replacement_rules": [{"key": rule.key, "component": "battery inverter",
                               "every_years": _pack_key_to_gs(rule.every_years),
                               "within": rule.within, "cost": _pack_key_to_gs(rule.cost),
                               "source": rule.source}],
        "degradation": fin.degradation,
        "sizing_limit": _finance_spec(fin.sizing_limit),
    }
    tariffs = {tid: _gs_tariff_from_pack(pt) for tid, pt in pack.tariffs.items()}
    return Library(
        version=label, technology=_pack_technology(pack, fin.currency_year),
        tariffs=MappingProxyType(tariffs), finance=MappingProxyType(finance),
        default_tariff_id=pack.default_tariff_id, pack=pack,
        tariff_illustrative=MappingProxyType(
            {tid: pt.meta.illustrative for tid, pt in pack.tariffs.items()}))


def load_defaults(version: str | None = None) -> Library:
    """
    The guided defaults read from IC's generic defaults pack (U2 WP3), as the
    `Library` view `seed_ledger` / `reseed_ledger` / `reset_rows` and
    `packs.build_site_network` take. `version` None is the newest shipped
    pack; an unknown one is refused by the pack loader (nothing is fetched).
    Its `version` (and a seeded ledger's `ledger_version`) reads
    ``"generic-defaults <pack version> <pack hash[:12]>"``.
    """
    return _load_defaults(version)


def _engine_rows(intake: Mapping[str, Any], tariff: Tariff, tariff_provenance: str,
                 library: Library) -> list[dict[str, Any]]:
    """
    Plan §1.2 rows 21-34: what a minimal single-owner finance case needs and
    GS had no row for, each a guided-study rule (`provenance=library`,
    `status=default`, source :data:`GUIDED_RULE_SOURCE` until the pack
    carries the rules, Q7; the rule id in `help`) with its `engine_path`. Non-numeric rules
    are descriptors (value null, `rule_descriptor`) so the report's
    assumptions appendix still lists them.
    """
    fin = library.finance
    site = intake.get("site") if isinstance(intake, Mapping) else None
    try:
        year = int((site or {}).get("year", 2025))
    except (TypeError, ValueError):
        year = 2025
    basis = fin.get("basis", {})

    def row(key, label, value, unit, rule, *, domain=None, help_=None, currency_year=None,
            source=None):
        # Gate U2-S1 C4: the pack carries none of these rules (Q7), so they are
        # the guided study's own and are never attributed to the pack; the rule
        # id stays in the help. A row from the tariff cites the tariff.
        if source is None:
            source, year_of_source = GUIDED_RULE_SOURCE, GUIDED_RULES_YEAR
            help_ = " ".join(x for x in (help_, f"Guided-study rule {rule}.") if x)
        else:
            year_of_source = tariff.source_year
        r: dict[str, Any] = dict(
            key=key, label=label, technical_name=ENGINE_PATHS[key], value=value, unit=unit,
            source=source, source_year=year_of_source,
            currency_year=currency_year, domain=domain, help=help_)
        if value is None:
            r["unavailable"] = {"value": RULE_DESCRIPTOR}
        return r

    out = [
        row("financial_close_year", "Financial close (one construction year)", float(year - 1),
            "year", "guided.financial_close.one_year_before_model_year",
            help_="One construction year: the year before the modelled year, so the "
                  "engine's year 0 is the study's capex year."),
        row("cod_year", "Commercial operation of every owner asset", float(year), "year",
            "guided.cod.model_year"),
        row("contingency_share", "Capex contingency", 0.0, "share of capex",
            "guided.finance.no_contingency", domain=LedgerDomain.parse("[0, 1]")),
        *(row(f"escalation_{c}", f"Escalation of {c} prices (real basis)", 0.0,
              "per unit per year", "guided.basis.real" if basis.get("terms") == "real"
              else "guided.basis.nominal", domain=LedgerDomain.parse("(-1, 1)"))
          for c in _ESCALATION_CLASSES),
        row("inflation", "Inflation (not stated on the real basis)", None, "per unit per year",
            "guided.basis.real"),
        row("cost_of_equity_rule", "Cost of equity = the discount rate (all equity)", None,
            "rule", "guided.finance.all_equity"),
        row("analysis_years_rule", "Analysis years = the storage lifetime", None, "rule",
            "guided.horizon.storage_lifetime"),
        row("value_flows_template", "Value flows: one owner (the site)", None, "rule",
            "guided.value_flows.single_owner"),
        row("salvage_rule", "Salvage: present value of the remaining annuities", None, "rule",
            "guided.salvage.annuity_pv_remaining_life"),
        row("pv_degradation_pct_per_year", "Solar PV degradation (not modelled)", 0.0,
            "%/year", "guided.degradation.none", domain=LedgerDomain.parse("[0, 100]"),
            help_="Degradation is not modelled (mvp_basis_no_degradation)."),
        row("tax_pack", "Tax pack: off (pre-tax basis)", None, "rule",
            "guided.basis.pre_tax" if basis.get("tax") == "pre" else "guided.basis.post_tax"),
        row("incentives_rule", "Incentives: none (excluding subsidies)", None, "rule",
            "guided.basis.excl_subsidy" if basis.get("subsidy") == "excl"
            else "guided.basis.incl_subsidy"),
        row("export_series", "Export price series (minted per study)", None, "series",
            "", source=f"Tariff {tariff.name!r} ({tariff.source})"),
    ]
    if tariff.export.cap_mw is not None:
        out.append(row("export_cap_mw", "Export cap of the connection",
                       float(tariff.export.cap_mw), "MW", "",
                       source=f"Tariff {tariff.name!r} ({tariff.source})",
                       domain=LedgerDomain.parse("[0, inf)")))
    return out


# ── seeding ───────────────────────────────────────────────────────────────

def key_drivers_of(question: DecisionQuestion | Iterable[str]) -> tuple[str, ...]:
    """A question's key drivers, or the list itself (the S2 stand-in)."""
    if isinstance(question, DecisionQuestion):
        return tuple(question.key_drivers)
    if isinstance(question, str):
        raise TypeError("pass a DecisionQuestion or a list of ledger keys")
    return tuple(question)


def _range(low: float | None, high: float | None,
           source: str | None) -> LedgerRange | None:
    if low is None:
        return None
    return LedgerRange(low=low, high=high,
                       source=source if source in ("assumed", "source") else None)


def _assumed_30(value: float) -> LedgerRange:
    lo, hi = sorted((round(value * 0.7, 4), round(value * 1.3, 4)))
    return LedgerRange(low=lo, high=hi, source="assumed")


def _tech_row(r: TechnologyRow) -> dict[str, Any]:
    # BC-S2-5: a 2030 projection says so wherever the label is shown.
    label = f"{r.label} ({r.projection_year} projection)" if r.projection_year else r.label
    row: dict[str, Any] = dict(
        key=r.key, label=label, technical_name=r.technical_name, domain=r.domain,
        value=r.value, unit=r.unit, basis=r.basis,
        currency_year=r.currency_year, source=r.source,
        source_year=r.source_year, source_url=r.source_url,
        range=_range(r.range_low, r.range_high, r.range_source),
    )
    if r.value is None:
        row["unavailable"] = {"value": r.note or "not_available"}
    return row


_TARIFF_PROVENANCES = frozenset({"user", "imported"})


def _intake_tariff(intake: Mapping[str, Any], library: Library) -> tuple[Tariff, str]:
    """
    The study's tariff and where it came from: ``library`` for a seed tariff
    chosen by id (or the library default when none is chosen), ``user`` (or
    ``imported``) for one the intake supplies as ``{"custom": {...}}``.
    """
    chosen = (intake.get("tariff") or {}) if isinstance(intake, Mapping) else {}
    if not isinstance(chosen, Mapping):
        raise LibraryError("intake tariff must be an object")
    tid, custom = chosen.get("tariff_id"), chosen.get("custom")
    if tid is not None and custom is not None:
        raise LibraryError("intake tariff names a library tariff_id AND a custom "
                           "tariff; give one")
    if custom is not None:
        provenance = chosen.get("provenance", "user")
        if provenance not in _TARIFF_PROVENANCES:
            raise LibraryError(f"a supplied tariff's provenance is user or imported, "
                               f"not {provenance!r}")
        try:
            return Tariff.model_validate(custom), provenance
        except ValueError as exc:
            raise LibraryError(f"intake custom tariff: {exc}") from None
    if tid is None:
        return library.tariffs[library.default_tariff_id], "library"
    if tid not in library.tariffs:
        raise LibraryError(
            f"intake tariff {tid!r} is not in the library "
            f"({sorted(library.tariffs)})")
    return library.tariffs[tid], "library"


def _tariff_rows(tariff: Tariff, provenance: str) -> list[dict[str, Any]]:
    """
    The tariff's ledger rows: a descriptor row ``tariff`` that records which
    tariff the study prices with and where it came from (the maturity badge
    reads it; gate S2 BC-S2-1), and the two key-driver rows it sets. On a
    tariff the user supplied, those prices are the user's own figures.
    """
    tariff_source = f"Tariff {tariff.name!r} ({tariff.source})"
    supplied = provenance != "library"
    who = {"provenance": provenance, "status": "customised" if supplied else "default"}
    descriptor = dict(
        key="tariff", label=f"Tariff: {tariff.name}", technical_name=tariff.tariff_id,
        # Not a number: the tariff is chosen or supplied at the tariff step,
        # never typed into the ledger (`ledger.apply_user_row` refuses it).
        value=None, unavailable={"value": "tariff_descriptor"}, unit="tariff",
        currency_year=tariff.currency_year, source=tariff.source,
        source_year=tariff.source_year, **who,
    )
    dc = tariff.demand_charge
    demand: dict[str, Any] = dict(
        key="demand_charge_price", label="Demand charge on peak import",
        technical_name="SolverConfig.demand_charge.price_per_mw_per_period",
        unit=f"{tariff.currency}/MW/{tariff.billing_period}",
        currency_year=tariff.currency_year, source=tariff_source,
        source_year=tariff.source_year, domain=LedgerDomain(low=0.0),
    )
    if dc is None:
        demand.update(value=None, range=None, provenance=provenance, status="default",
                      unavailable={"value": "not_applicable"})
    else:
        demand.update(value=dc.price_per_mw_per_period,
                      range=_assumed_30(dc.price_per_mw_per_period), **who)
    level = dict(
        key="energy_price_level", label=ENERGY_PRICE_LEVEL_LABEL,
        technical_name=ENERGY_PRICE_LEVEL_TECHNICAL, help=ENERGY_PRICE_LEVEL_HELP,
        value=1.0, unit="multiplier",
        # The multiplier scales the tariff's prices, so it carries their year.
        currency_year=tariff.currency_year, source=tariff_source,
        source_year=tariff.source_year, range=_assumed_30(1.0),
        domain=LedgerDomain(low=0.0, low_open=True), **who,
    )
    if len({float(b.price_per_mwh) for b in tariff.energy_bands}) <= 1:
        # Gate S4 [S5]: on a single-band tariff the level rescales nothing, so
        # the row is not applicable — an edit is refused (BC-S2-2 machinery),
        # the maturity badge does not ask for it, and a re-seed onto such a
        # tariff flags a kept user value. The pack reads a null level as 1.0.
        level.update(value=None, range=None, provenance=provenance,
                     status="default", unavailable={"value": "not_applicable"})
    return [descriptor, demand, level]


def _sizing_row(library: Library) -> dict[str, Any] | None:
    """
    The sizing limit (plan S4, review v2 BC-2): every extendable asset's
    ``p_nom_max`` is the connection limit times this multiple. From the
    finance file, because ``_TECH_KEYS`` admits technology rows only.
    """
    spec = library.finance.get("sizing_limit")
    if not spec:
        return None
    rng = spec.get("range") or {}
    return dict(
        key=spec["key"],
        label="Sizing limit for every extendable asset (multiple of the connection limit)",
        technical_name="p_nom_max = connection_mw x value (battery StorageUnit, PV Generator)",
        help=("Upper bound on the battery's MW and the PV's MW, as a multiple of "
              "the site's connection limit. A size at this bound is not an "
              "optimum: the verdict names it (size_at_upper_bound)."),
        value=float(spec["value"]), unit=spec["unit"], basis="real",
        currency_year=None, source=spec["source"].strip(),
        source_year=int(spec["source_year"]),
        range=_range(rng.get("low"), rng.get("high"), rng.get("source")),
        domain=LedgerDomain.parse(spec["domain"]),
    )


def _finance_rows(library: Library) -> list[dict[str, Any]]:
    dr = library.finance["discount_rate"]
    rng = dr.get("range") or {}
    sizing = _sizing_row(library)
    return ([sizing] if sizing else []) + [dict(
        key="discount_rate", label="Discount rate (real, pre-tax)",
        technical_name="discount_rate", value=float(dr["value"]),
        unit=dr["unit"], basis=dr["basis"],
        # A real rate discounts money of the study's one currency year.
        currency_year=int(library.finance["currency_year"]),
        source=dr["source"], source_year=int(dr["source_year"]),
        source_url=dr.get("source_url"),
        range=_range(rng.get("low"), rng.get("high"), rng.get("source")),
        domain=LedgerDomain.parse(dr["domain"]),
    )]


def seed_ledger(question: DecisionQuestion | Iterable[str],
                intake: Mapping[str, Any] | None,
                library: Library) -> AssumptionsLedger:
    """
    A fresh ledger from the library and the intake: every library row
    ``provenance=library, status=default``, ``sensitivity_flag`` on the
    question's key drivers. ``question`` is a :class:`DecisionQuestion` or,
    until S4 ships the template, the key-driver list itself.

    The tariff rows follow ``intake["tariff"]``: ``{"tariff_id": ...}`` picks a
    library seed (the library's first tariff when none is chosen), and
    ``{"custom": {...}}`` is a tariff the user supplied, whose rows carry
    ``provenance=user`` (or ``imported``) rather than ``library``. A key
    driver that no row carries is recorded as an honesty note, never
    silently dropped.
    """
    drivers = set(key_drivers_of(question))
    tariff, tariff_provenance = _intake_tariff(intake or {}, library)
    raw = ([_tech_row(r) for r in library.technology]
           + _tariff_rows(tariff, tariff_provenance) + _finance_rows(library))
    if library.pack is not None:
        # U2 WP3: rows 21-34 from the pack's rules (plan §1.2).
        raw += _engine_rows(intake or {}, tariff, tariff_provenance, library)
    rows = [LedgerRow(**{"provenance": "library", "status": "default", **r},
                      sensitivity_flag=r["key"] in drivers) for r in raw]
    if library.pack is not None:
        rows = [r.model_copy(update={"engine_path": ENGINE_PATHS.get(r.key),
                                     "illustrative": _illustrative(r.key, tariff,
                                                                   tariff_provenance, library)})
                for r in rows]
    notes: list[str] = []
    # BC-S2-5: the vintage of the technology costs, so a reader in 2026 is
    # not told these are current prices.
    for proj, cy in sorted({(r.projection_year, r.currency_year) for r in library.technology
                            if r.projection_year is not None}):
        notes.append(f"technology_costs_are_{proj}_projections_in_{cy}_eur")
    missing = sorted(drivers - {r.key for r in rows})
    if missing:
        notes.append("key_drivers_without_a_row:" + ",".join(missing))
    study_year = int(library.finance["currency_year"])
    if tariff.currency_year != study_year:
        notes.append(f"tariff_currency_year_{tariff.currency_year}_differs_"
                     f"from_study_{study_year}")
    return AssumptionsLedger(ledger_version=library.version, rows=rows,
                             honesty_notes=tuple(notes))


def _illustrative(key: str, tariff: Tariff, tariff_provenance: str,
                  library: Library) -> bool | None:
    """
    The pack's `illustrative` flag a seeded row carries (None: not a pack
    figure — a rule row, or a tariff the user supplied).
    """
    if key in ("tariff", "demand_charge_price", "energy_price_level"):
        if tariff_provenance != "library":
            return None
        return library.tariff_illustrative.get(tariff.tariff_id)
    for r in library.technology:
        if r.key == key:
            return r.illustrative
    for spec_key in ("discount_rate", "sizing_limit"):
        spec = library.finance.get(spec_key) or {}
        if spec.get("key", spec_key) == key:
            return spec.get("illustrative")
    return None


def horizon_and_replacements(ledger: AssumptionsLedger,
                             library: Library) -> tuple[int, tuple[int, ...]]:
    """
    The finance rules, read against the ledger (so a user lifetime moves
    them): the horizon is the storage lifetime, and the inverter is replaced
    every inverter lifetime strictly inside it (10 and 25 give (10, 20)).
    """
    values = {r.key: r.value for r in ledger.rows}
    horizon_key = library.finance["horizon_rule"]["horizon_years"]
    horizon = int(round(values[horizon_key]))
    [rule] = library.finance["replacement_rules"]
    every = int(round(values[rule["every_years"]]))
    if every <= 0:
        raise LibraryError(f"{rule['every_years']} must be positive")
    return horizon, tuple(range(every, horizon, every))
