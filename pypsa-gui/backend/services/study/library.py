"""
The assumptions library (guided investment study MVP-1, phase S2; U2 WP3/WP10).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S2),
docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §6.
Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md §4.3

Every default comes from the Investment Case's generic defaults pack
(:func:`load_defaults`, U2 WP3; the pinned :data:`PACK_VERSION`), read into
the :class:`Library` view: the technology rows (the catalogue's units, the
ledger's keys and labels), the two illustrative seed tariffs as the guided
form, and the finance rules (discount rate, basis, perspective, currency
year, horizon and replacement rules).

Until U2 WP10 the same view was also read from three vendored files under
``backend/study_library/`` (technology-data v0.14.0, ``tariffs.csv``,
``finance_defaults.yaml``). WP10 deleted them and their loader; their rows
1-20 are frozen in ``tests/fixtures/legacy_study_library.json`` (gate U2-S1
C8 a). A ledger a study stored before then still loads, runs and is valued:
it names :data:`LIBRARY_VERSION` (read-only), and every reader takes the
pinned pack for what the ledger does not hold. ``study_library/load_profiles/``
stays: the sector load profiles, which the pack does not carry (Q7).

:func:`seed_ledger` turns the library plus the study's intake into an
:class:`AssumptionsLedger` whose every row is ``provenance=library,
status=default``, with ``sensitivity_flag`` set on the question's key drivers.
"""
from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

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
    "ENGINE_ROWS", "INTAKE_DERIVED_ROWS", "LIBRARY_VERSION", "Library",
    "LibraryError", "RULE_DESCRIPTOR", "TechnologyRow",
    "horizon_and_replacements", "key_drivers_of", "load_defaults", "seed_ledger",
]

#: READ-ONLY since U2 WP10: the `ledger_version` of a ledger seeded from the
#: deleted `study_library/` files (a study stored before U2 WP8 seeded from
#: the pack). Nothing seeds it any more.
LIBRARY_VERSION = "technology-data v0.14.0"

# The BESS question's key drivers. S4's template
# (`services/study/questions.py::BESS_AT_SITE.key_drivers`) is THE list and
# the routes seed from it; this alias is kept for the S2 tests and held equal
# to the template by `tests/test_study_questions.py` (gate S2 carry).
BESS_KEY_DRIVERS: tuple[str, ...] = (
    "battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw",
    "demand_charge_price", "energy_price_level", "discount_rate",
)

# (technology, parameter) -> (ledger key, plain label, technical name).
# A pack row with no entry here is refused at load: a key is a contract the
# network pack (S4), the case (S5) and the tornado (S6) read by name.
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
    """A defaults-pack row (or an intake reference into the library) is invalid."""


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
    # U2 WP3: the generic defaults pack this view was read from, and each pack
    # tariff's `illustrative`.
    pack: Any = field(default=None, compare=False)
    tariff_illustrative: Mapping[str, bool] = field(default_factory=dict, compare=False)


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
# The defaults pack the guided study reads, PINNED (coordinating session,
# 2026-10-08): the report states the defaults it used, so the version is an
# input, never "the newest vendored". A bump is a conscious U2 change with its
# own parity check; `test_study_ledger_from_pack` fails when a newer vendored
# pack changes anything the study reads (its mapped and named rows, the pack
# tariffs, the finance fields), and `unmapped_pack_rows` stays strict on the
# pinned pack itself.
PACK_VERSION = "2026-10-05"

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
#: Gate U2-S1 C4: rows 21-34's source names the guided study as the rule's
#: author and says the pinned pack does not carry it (Q7), never the pack.
GUIDED_RULE_SOURCE = "guided study rule {rule}; not in pack {pack}"
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


def unmapped_pack_rows(pack) -> list[str]:
    """
    The pack's cost rows the guided ledger neither maps nor names as not
    seeded. Non-empty refuses the pack (`_pack_technology`): a deliberate
    tripwire, so a new row is a conscious U2 change, never silently dropped.
    """
    mapped = {k for k, _ in _PACK_TECH_ROWS} | set(_PACK_ROWS_NOT_SEEDED)
    return sorted({v.key for v in pack.cost_values} - mapped)


def _pack_technology(pack, finance_year: int) -> tuple[TechnologyRow, ...]:
    by_key = {v.key: v for v in pack.cost_values}
    stray = unmapped_pack_rows(pack)
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
def _load_defaults(version: str) -> Library:
    from services.study import compile as C

    pack = C.load_defaults_pack(version)
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
    `packs.build_site_network` take. `version` None is the pinned
    :data:`PACK_VERSION`, never the newest shipped pack; an unknown one is
    refused by the pack loader (nothing is fetched). Its `version` (and a
    seeded ledger's `ledger_version`) reads
    ``"generic-defaults <pack version> <pack hash[:12]>"``.
    """
    return _load_defaults(version or PACK_VERSION)


def _engine_rows(intake: Mapping[str, Any], tariff: Tariff, tariff_provenance: str,
                 library: Library) -> list[dict[str, Any]]:
    """
    Plan §1.2 rows 21-34: what a minimal single-owner finance case needs and
    GS had no row for, each a guided-study rule (`provenance=library`,
    `status=default`, source :data:`GUIDED_RULE_SOURCE` — the rule id, and
    that the pinned pack does not carry it, until it does, Q7; the rule id
    in `help` too) with its `engine_path`. Non-numeric rules are descriptors
    (value null, `rule_descriptor`) so the report's assumptions appendix
    still lists them.
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
            source = GUIDED_RULE_SOURCE.format(rule=rule, pack=library.version)
            year_of_source = GUIDED_RULES_YEAR
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
