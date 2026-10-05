"""
The generic defaults pack: a versioned, hashed, in-tree set of illustrative and catalogue
defaults (IC U1 follow-up, item a).

Plan: docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md §3 rule 4, §4 C1-C4
Spec: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md decision 20 (amended
      2026-10-05 for this pack only)

One version is one directory, ``versions/<YYYY-MM-DD>/``, holding:

* ``manifest.json``: pack id, version, what it was seeded from, and the load profiles
  (file, sha256 of its LF-normalised bytes, synthetic / illustrative flags, source, unit);
* ``values.csv``: technology values in the asset schema's part vocabulary (S0:
  ``power`` per_MW, ``energy`` per_MWh, single-part ``investment``). Each row is
  transcribed in the CATALOGUE's unit (``original_value``, ``original_unit``) and the
  loader converts it to the pack unit through the closed :data:`UNIT_CONVERSIONS`
  table, so the source number and its conversion are both on the row;
* ``finance.yaml``: the finance defaults, every number with its source;
* ``tariffs.json``: IC :class:`~models.commercial.Tariff` payloads, each beside a
  :class:`PackTariffMeta`. Each item's seed row (in EUR/MWh, EUR/MW/month, EUR/month)
  is kept and checked against the IC rate through :data:`TARIFF_CONVERSIONS`;
* ``load_profiles/*.csv``: synthetic month x daytype x hour shapes.

The pack hash is the sha256 of the canonical JSON of the RAW files (`canonical_payload`:
CSV rows as strings, the YAML / JSON as loaded, the manifest without `files`, each profile
file's sha256), never of a model's dump, so a model change cannot re-hash a shipped
version. Validation is separate. It is pinned per version in
``tests/fixtures/defaults_pack/pack_hashes.json``.

Nothing is downloaded: an unknown version is refused. Lookups of unknown ids raise a
:class:`DefaultsPackLookupError` subclass carrying a stable ``code``.

A pack tariff is COPIED inline into a project's commercial config (owner decision R3):
:meth:`DefaultsPack.pack_tariff` returns a deep copy with ``Tariff.pack_hash`` set to
:attr:`DefaultsPack.stamp` (``generic_defaults@<version>:sha256:<hash>``). The guided
study must leave ``CommercialConfig.import_tariff_ref = None``: a ref would be resolved
in the org Library, where the pack tariff is not, and fail ``library_ref_stale``.
"""
from __future__ import annotations

import csv
import functools
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml
from pydantic import (BaseModel, ConfigDict, Field, PrivateAttr, ValidationError,
                      field_validator)

from models.commercial import Tariff

__all__ = [
    "PACK_ID", "VERSIONS_DIR", "DERIVED_FORMULAS", "UNIT_CONVERSIONS", "TARIFF_CONVERSIONS",
    "AssumptionRow", "CostPart", "DefaultsPack", "DefaultsPackError", "DefaultsPackLookupError",
    "Derived", "FinanceDefaults", "LoadProfile", "PackRange", "PackTariff", "PackTariffMeta",
    "PackValue", "UnknownCostPart", "UnknownFormula", "UnknownLoadProfile", "UnknownPackTariff",
    "UnknownPackValue", "UnknownPackVersion", "UnknownTechnology",
    "available_versions", "canonical_payload", "derive", "load_defaults_pack", "parse_pack_dir",
    "parse_pack_stamp",
    "version_dir",
]

PACK_ID = "generic_defaults"
VERSIONS_DIR = Path(__file__).resolve().parent / "versions"
_VERSION_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STAMP_RE = re.compile(r"(?P<id>[a-z0-9_]+)@(?P<version>\d{4}-\d{2}-\d{2}):sha256:(?P<hash>[0-9a-f]{64})")
_NOTE_CODE_RE = re.compile(r"[a-z]+(_[a-z]+)*")
_DAYTYPES = ("weekday", "weekend")


# ── errors ──────────────────────────────────────────────────────────────────


class DefaultsPackError(ValueError):
    """A vendored pack file is invalid (or a helper got a bad argument)."""


class DefaultsPackLookupError(LookupError):
    """An id the pack does not carry. ``code`` is stable; the message lists what exists."""

    code = "defaults_pack_unknown"

    def __init__(self, what: str, known: Sequence[str]):
        super().__init__(f"{what} (known: {', '.join(sorted(known)) or 'none'})")
        self.known = tuple(sorted(known))


class UnknownPackVersion(DefaultsPackLookupError):
    code = "defaults_pack_version_unknown"


class UnknownTechnology(DefaultsPackLookupError):
    code = "defaults_pack_technology_unknown"


class UnknownCostPart(DefaultsPackLookupError):
    code = "defaults_pack_cost_part_unknown"


class UnknownPackValue(DefaultsPackLookupError):
    code = "defaults_pack_value_unknown"


class UnknownPackTariff(DefaultsPackLookupError):
    code = "defaults_pack_tariff_unknown"


class UnknownLoadProfile(DefaultsPackLookupError):
    code = "defaults_pack_load_profile_unknown"


class UnknownFormula(DefaultsPackLookupError):
    code = "defaults_pack_formula_unknown"


# ── closed registries ───────────────────────────────────────────────────────

# Derived values: formula id -> (arity, function). CLOSED: a pack row naming any other id
# is refused at load, and the guided ledger refreshes derived rows through `derive`, so
# there is one definition of each formula.
DERIVED_FORMULAS: Mapping[str, tuple[int, Callable[..., float]]] = {
    # round-trip efficiency = one-way inverter efficiency squared (charge and discharge
    # each pass the inverter once)
    "square": (1, lambda x: x * x),
}


def derive(formula_id: str, values: Sequence[float]) -> float:
    """The value of the closed formula `formula_id` on `values` (in its input order)."""
    if formula_id not in DERIVED_FORMULAS:
        raise UnknownFormula(f"no derived formula {formula_id!r}", list(DERIVED_FORMULAS))
    arity, fn = DERIVED_FORMULAS[formula_id]
    vals = [float(v) for v in values]
    if len(vals) != arity:
        raise DefaultsPackError(f"formula {formula_id!r} takes {arity} input(s), got {len(vals)}")
    return float(fn(*vals))


# (catalogue unit, pack unit) -> factor. pack value = catalogue value x factor.
UNIT_CONVERSIONS: Mapping[tuple[str, str], float] = {
    ("EUR/kW", "EUR/MW"): 1000.0,            # 1 MW = 1000 kW
    ("EUR/kW_e", "EUR/MW"): 1000.0,          # technology-data's electric-kW unit
    ("EUR/kWh", "EUR/MWh"): 1000.0,
    ("%/year", "share/year"): 0.01,          # FOM in % of overnight cost -> S0 fom_share
    ("%/full cycle", "share/full cycle"): 0.01,
    ("years", "years"): 1.0,
    ("per unit", "per unit"): 1.0,
}

# (seed tariff unit, IC TariffItem.unit) -> factor. IC rate = seed price x factor.
TARIFF_CONVERSIONS: Mapping[tuple[str, str], float] = {
    ("EUR/MWh", "per_kwh"): 1e-3,
    ("EUR/MW/month", "per_kw_month"): 1e-3,
    ("EUR/MW/year", "per_kw_year"): 1e-3,
    ("EUR/month", "per_month"): 1.0,
}

_BASIS_UNIT = {"per_MW": "EUR/MW", "per_MWh": "EUR/MWh", "per_km": "EUR/km"}


# ── models ──────────────────────────────────────────────────────────────────

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class PackRange(BaseModel):
    model_config = _FROZEN
    low: float
    high: float
    source: Literal["assumed", "source"]


class Derived(BaseModel):
    model_config = _FROZEN
    inputs: list[str] = Field(min_length=1)
    formula_id: str


class PackValue(BaseModel):
    """One value with its provenance (plan §3 rule 4: value, unit, range, source, year,
    `illustrative`). `value` is in the PACK unit; `original_*` is the catalogue's own."""

    model_config = _FROZEN
    key: str
    label: str
    technology: str | None = None
    part: str | None = None
    basis: Literal["per_MW", "per_MWh", "per_km"] | None = None
    source_technology: str | None = None
    parameter: str
    value: float | None
    unit: str
    original_value: float | None
    original_unit: str
    conversion_factor: float
    range: PackRange | None = None
    currency: str | None = None
    currency_year: int | None = None
    price_basis: Literal["real", "nominal"] | None = None
    source: str = Field(min_length=1)
    source_year: int
    source_url: str | None = None
    projection_year: int | None = None
    domain: str | None = None
    illustrative: bool
    derived: Derived | None = None
    note: str | None = None
    status: Literal["ok", "not_available"] = "ok"


class CostPart(BaseModel):
    """One investment part in the asset schema's vocabulary (S0 `PartSpec`): `overnight`
    per unit of `basis`, a lifetime, a FOM share of the overnight cost per year, and an
    efficiency where the part has one. A field the catalogue does not carry is None
    (not established), never 0."""

    model_config = _FROZEN
    technology: str
    part: Literal["power", "energy", "investment"]
    basis: Literal["per_MW", "per_MWh", "per_km"]
    source_technology: str
    overnight: PackValue
    lifetime: PackValue | None = None
    fom_share: PackValue | None = None
    efficiency: PackValue | None = None


class TariffSourceRow(BaseModel):
    model_config = _FROZEN
    component: str
    label: str
    original_price: float
    original_unit: str
    basis: str | None = None
    periods: list[str] = Field(min_length=1)


class TariffExport(BaseModel):
    """Export compensation. Not an IC tariff item: a constant price is minted into a flat
    Library series (`services.library.export_series.put_flat_export_series`)."""

    model_config = _FROZEN
    price_per_mwh: float | None = None
    series: str | None = None
    cap_mw: float | None = None
    label: str | None = None


class HonestyNote(BaseModel):
    model_config = _FROZEN
    code: str
    help: str = Field(min_length=1)

    @field_validator("code")
    @classmethod
    def _snake(cls, v: str) -> str:
        if not _NOTE_CODE_RE.fullmatch(v):
            raise ValueError(f"honesty code must be snake_case without digits, got {v!r}")
        return v


class PackTariffMeta(BaseModel):
    model_config = _FROZEN
    currency: str
    currency_year: int
    source: str = Field(min_length=1)
    source_year: int
    source_url: str | None = None
    illustrative: bool
    billing_period: Literal["month", "year"]
    default: bool
    jurisdiction_rationale: str = Field(min_length=1)
    export: TariffExport
    honesty: list[HonestyNote] = Field(default_factory=list)
    items: dict[str, list[TariffSourceRow]]


class PackTariff(BaseModel):
    model_config = ConfigDict(frozen=True)
    tariff: Tariff
    meta: PackTariffMeta


class LoadProfile(BaseModel):
    """A synthetic shape: `factors` holds 12 months x (weekday, weekend) x 24 hours, in
    that order (month 1 weekday hour 0 first)."""

    model_config = _FROZEN
    id: str
    label: str
    file: str
    sha256: str = Field(min_length=64, max_length=64)
    source: str = Field(min_length=1)
    source_year: int
    synthetic: bool
    illustrative: bool
    unit: str
    note: str | None = None
    factors: tuple[float, ...]


class FinanceBasis(BaseModel):
    model_config = _FROZEN
    terms: Literal["real", "nominal"]
    tax: Literal["pre", "post"]
    subsidy: Literal["excl", "incl"]


class HorizonRule(BaseModel):
    model_config = _FROZEN
    horizon_years: str
    source: str


class ReplacementRule(BaseModel):
    model_config = _FROZEN
    key: str
    part: str
    every_years: str
    within: Literal["horizon_exclusive"]
    cost: str
    source: str


class FinanceDefaults(BaseModel):
    model_config = _FROZEN
    seed_library: str
    currency: str
    currency_year: int
    currency_year_source: str
    basis: FinanceBasis
    basis_source: str
    perspective: str
    perspective_source: str
    discount_rate: PackValue
    horizon_rule: HorizonRule
    replacement_rules: list[ReplacementRule]
    degradation: str
    sizing_limit: PackValue


class AssumptionRow(BaseModel):
    """One row of the report's assumptions appendix: every pack number with its provenance
    and the pack version it came from."""

    model_config = _FROZEN
    section: Literal["technology", "finance", "tariff", "load_profile"]
    key: str
    label: str
    value: float | None
    unit: str
    original_value: float | None = None
    original_unit: str | None = None
    range_low: float | None = None
    range_high: float | None = None
    range_source: str | None = None
    currency: str | None = None
    currency_year: int | None = None
    source: str
    source_year: int
    source_url: str | None = None
    illustrative: bool
    note: str | None = None
    pack_id: str
    pack_version: str
    pack_hash: str


class DefaultsPack(BaseModel):
    """One parsed pack version. Every load returns an independent copy; `pack_tariff`
    returns an independent stamped copy of one tariff."""

    model_config = ConfigDict(frozen=True)
    pack_id: str
    version: str
    title: str
    seeded_from: dict[str, Any]
    hash: str
    cost_values: list[PackValue]
    finance: FinanceDefaults
    tariffs: dict[str, PackTariff]
    load_profiles: dict[str, LoadProfile]
    # The tariff payloads exactly as the file holds them (JSON text: immutable), so
    # `pack_tariff` copies from the pristine parse, never from the mutable model tree.
    _tariff_json: tuple[tuple[str, str], ...] = PrivateAttr(default=())

    # -- identity ----------------------------------------------------------

    @property
    def stamp(self) -> str:
        """What a copied tariff carries in `Tariff.pack_hash`: id, version and hash."""
        return f"{self.pack_id}@{self.version}:sha256:{self.hash}"

    # -- values and cost parts ----------------------------------------------

    def values(self) -> list[PackValue]:
        """Every value row: technology values, then the finance values."""
        return [*self.cost_values, self.finance.discount_rate, self.finance.sizing_limit]

    def value(self, key: str) -> PackValue:
        for v in self.values():
            if v.key == key:
                return v
        raise UnknownPackValue(f"no pack value {key!r}", [v.key for v in self.values()])

    def technologies(self) -> list[str]:
        return sorted({v.technology for v in self.cost_values if v.technology})

    def cost_parts(self, technology: str) -> list[CostPart]:
        if technology not in self.technologies():
            raise UnknownTechnology(f"no technology {technology!r} in the defaults pack",
                                    self.technologies())
        return _cost_parts(self.cost_values, technology)

    def cost_part(self, technology: str, part: str) -> CostPart:
        parts = {p.part: p for p in self.cost_parts(technology)}
        if part not in parts:
            raise UnknownCostPart(f"technology {technology!r} has no part {part!r}", list(parts))
        return parts[part]

    # -- tariffs -----------------------------------------------------------

    def tariff_ids(self) -> list[str]:
        return list(self.tariffs)

    @property
    def default_tariff_id(self) -> str:
        return next(t for t, p in self.tariffs.items() if p.meta.default)

    def _pack_tariff(self, tariff_id: str) -> PackTariff:
        if tariff_id not in self.tariffs:
            raise UnknownPackTariff(f"no pack tariff {tariff_id!r}", list(self.tariffs))
        return self.tariffs[tariff_id]

    def pack_tariff(self, tariff_id: str) -> Tariff:
        """A deep COPY of the tariff, stamped with the pack id, version and hash in
        `Tariff.pack_hash`, to write inline into `CommercialConfig.import_tariff`
        (leave `import_tariff_ref` None)."""
        self._pack_tariff(tariff_id)                      # the typed error for an unknown id
        raw = dict(self._tariff_json)[tariff_id]
        t = Tariff.model_validate(json.loads(raw))
        t.pack_hash = self.stamp
        return t

    def tariff_is_unchanged(self, tariff: Tariff | Mapping[str, Any]) -> bool:
        """True when `tariff` is this pack's tariff of the same id, as shipped: everything
        but `pack_hash` equal, and a stamp, when present, naming this pack and version.
        False for an edited copy, an unknown id, another version's (or a non-pack) stamp,
        or an invalid payload. The guided ledger's `customised` detection (plan §3 rule 5)."""
        if not isinstance(tariff, Tariff):
            try:
                tariff = Tariff.model_validate(tariff)
            except (ValidationError, TypeError, ValueError):
                return False
        if tariff.id not in self.tariffs:
            return False
        if tariff.pack_hash is not None:
            # A stamp names the pack and version the copy came from: another version's
            # tariff is not this version's, even with the same content.
            try:
                pid, version, _ = parse_pack_stamp(tariff.pack_hash)
            except DefaultsPackError:
                return False
            if (pid, version) != (self.pack_id, self.version):
                return False
        ours = self.pack_tariff(tariff.id)
        return (tariff.model_dump(mode="json", exclude={"pack_hash"})
                == ours.model_dump(mode="json", exclude={"pack_hash"}))

    def tariff_meta(self, tariff_id: str) -> PackTariffMeta:
        return self._pack_tariff(tariff_id).meta.model_copy(deep=True)

    def export_price_eur_per_mwh(self, tariff_id: str) -> float | None:
        return self._pack_tariff(tariff_id).meta.export.price_per_mwh

    # -- load profiles -----------------------------------------------------

    def load_profile_ids(self) -> list[str]:
        return sorted(self.load_profiles)

    def load_profile(self, profile_id: str) -> LoadProfile:
        if profile_id not in self.load_profiles:
            raise UnknownLoadProfile(f"no load profile {profile_id!r}", list(self.load_profiles))
        return self.load_profiles[profile_id]

    def load_profile_series(self, profile_id: str, index, *,
                            annual_mwh: float | None = None, weights=None) -> pd.Series:
        """The shape on `index`, in MW when `annual_mwh` is given.

        `index` is a DatetimeIndex or a snapshot MultiIndex (period, timestamp); the
        result is on `index` as given.

        Clock: each row's month, weekday/weekend and hour are read on the index's OWN
        clock (a tz-aware index on its zone's wall clock, a naive one as is). Pass the
        SITE clock: with `commercial.timezone` set the network's snapshots are UTC-naive,
        so convert them first (`idx.tz_localize("UTC").tz_convert(tz)`).

        Scaling: `annual_mwh` is per YEAR, so each investment period is scaled on its own:
        in every period the MW values are `shape x annual_mwh / sum(shape x w)` over that
        period's rows, so each period's energy `sum(MW x w)` is `annual_mwh`. The periods
        are the MultiIndex level 0 of `index` (or of `weights`); on a plain index, a
        timestamp that does not increase starts a new period (one weather year repeated).
        `w` is the hours each row stands for: `weights` (the network's snapshot
        weightings: a scalar, an array, or a Series on `index` or on a snapshot
        MultiIndex whose last level is the timestamps); by default the index's step in
        hours (a fixed `freq`, else the median step; a non-fixed `freq` such as `MS`
        needs `weights`). Representative periods need their weightings passed. Without
        `annual_mwh`, the raw factors."""
        prof = self.load_profile(profile_id)
        stamps, periods = _timestamps_and_periods(index, weights)
        f = np.asarray(prof.factors, dtype=float).reshape(12, 2, 24)
        shape = f[np.asarray(stamps.month) - 1, (np.asarray(stamps.weekday) >= 5).astype(int),
                  np.asarray(stamps.hour)]
        if annual_mwh is not None:
            if isinstance(annual_mwh, bool) or not isinstance(annual_mwh, (int, float, np.number)) \
                    or not math.isfinite(annual_mwh) or annual_mwh < 0:
                raise ValueError(f"annual_mwh must be a finite number >= 0, got {annual_mwh!r}")
            w = _row_hours(stamps, weights, periods)
            out = np.empty_like(shape)
            for g in np.unique(periods):
                sel = periods == g
                energy = float((shape[sel] * w[sel]).sum())
                if not energy > 0:
                    raise ValueError("the shape carries no energy on this index and these "
                                     "weights" + (f" (period {g})" if len(np.unique(periods)) > 1
                                                  else ""))
                out[sel] = shape[sel] * (float(annual_mwh) / energy)
            shape = out
        elif weights is not None:
            raise ValueError("weights scale the energy: pass them with annual_mwh")
        return pd.Series(shape, index=index, name=profile_id)

    # -- the report's assumptions appendix ----------------------------------

    def assumption_rows(self) -> list[AssumptionRow]:
        """Every pack row with its provenance, stamped with this pack's id, version and
        hash: technology and finance values, every tariff item rate (in the IC unit, the
        seed price as the original), each tariff's export price, and each load profile."""
        stamp = {"pack_id": self.pack_id, "pack_version": self.version, "pack_hash": self.hash}
        rows: list[AssumptionRow] = []
        for v in self.values():
            rows.append(AssumptionRow(
                section="finance" if v.key.startswith("finance.") else "technology",
                key=v.key, label=v.label, value=v.value, unit=v.unit,
                original_value=v.original_value, original_unit=v.original_unit,
                range_low=v.range.low if v.range else None,
                range_high=v.range.high if v.range else None,
                range_source=v.range.source if v.range else None,
                currency=v.currency, currency_year=v.currency_year, source=v.source,
                source_year=v.source_year, source_url=v.source_url,
                illustrative=v.illustrative, note=v.note, **stamp))
        for tid, pt in self.tariffs.items():
            meta = pt.meta
            items = {i.id: i for i in pt.tariff.items}
            for item_id, src_rows in meta.items.items():
                item = items[item_id]
                for src in src_rows:
                    key = f"tariff.{tid}.items.{item_id}"
                    if len(src_rows) > 1:
                        key += f".{src.periods[0]}"
                    rate = next(p.rate for p in item.periods if p.name == src.periods[0])
                    rows.append(AssumptionRow(
                        section="tariff", key=key, label=f"{pt.tariff.name}: {src.label}",
                        value=rate, unit=_ic_unit_label(meta.currency, item.unit),
                        original_value=src.original_price, original_unit=src.original_unit,
                        currency=meta.currency, currency_year=meta.currency_year,
                        source=meta.source, source_year=meta.source_year,
                        source_url=meta.source_url, illustrative=meta.illustrative,
                        note="; ".join(h.code for h in meta.honesty) or None, **stamp))
            if meta.export.price_per_mwh is not None:
                rows.append(AssumptionRow(
                    section="tariff", key=f"tariff.{tid}.export_price",
                    label=f"{pt.tariff.name}: {meta.export.label or 'Export price'}",
                    value=meta.export.price_per_mwh, unit=f"{meta.currency}/MWh",
                    original_value=meta.export.price_per_mwh,
                    original_unit=f"{meta.currency}/MWh", currency=meta.currency,
                    currency_year=meta.currency_year, source=meta.source,
                    source_year=meta.source_year, source_url=meta.source_url,
                    illustrative=meta.illustrative,
                    note="flat export series (export_price_ref), not a tariff item", **stamp))
        for pid in self.load_profile_ids():
            p = self.load_profiles[pid]
            rows.append(AssumptionRow(
                section="load_profile", key=f"load_profile.{pid}", label=p.label, value=None,
                unit=p.unit, source=p.source, source_year=p.source_year,
                illustrative=p.illustrative,
                note=("synthetic; " if p.synthetic else "") + (p.note or ""), **stamp))
        return rows


def _timestamps_and_periods(index, weights) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """The rows' timestamps and a period label per row (see `load_profile_series`)."""
    if isinstance(index, pd.MultiIndex):
        stamps = pd.DatetimeIndex(index.get_level_values(-1))
        periods = np.asarray(pd.factorize(index.get_level_values(0))[0])
    elif isinstance(index, pd.DatetimeIndex):
        stamps = index
        if isinstance(weights, pd.Series) and isinstance(weights.index, pd.MultiIndex) \
                and len(weights) == len(index) \
                and pd.DatetimeIndex(weights.index.get_level_values(-1)).equals(index):
            periods = np.asarray(pd.factorize(weights.index.get_level_values(0))[0])
        else:
            # a timestamp that does not increase starts a new period
            steps = np.diff(index.asi8)
            periods = np.concatenate([[0], np.cumsum(steps <= 0)]) if len(index) else \
                np.zeros(0, dtype=int)
    else:
        raise ValueError("index must be a DatetimeIndex or a snapshot MultiIndex")
    if len(stamps) == 0:
        raise ValueError("index must not be empty")
    return stamps, periods


def _row_hours(stamps: pd.DatetimeIndex, weights, periods: np.ndarray) -> np.ndarray:
    """The hours each row stands for (see `load_profile_series`)."""
    n = len(stamps)
    if weights is None:
        if stamps.freq is not None:
            try:
                step = pd.Timedelta(stamps.freq).total_seconds() / 3600.0
            except ValueError:
                raise ValueError(f"the index's freq {stamps.freqstr!r} is not a fixed length: "
                                 "pass weights (the hours each row stands for)") from None
            return np.full(n, step)
        diffs = np.diff(stamps.asi8)
        same = periods[1:] == periods[:-1]
        within = diffs[same & (diffs > 0)]
        if len(within) == 0:
            raise ValueError("an index with no step (one row per period): pass weights")
        return np.full(n, float(np.median(within)) / 3.6e12)
    if isinstance(weights, pd.Series):
        if weights.index.equals(stamps) or (
                isinstance(weights.index, pd.MultiIndex) and len(weights) == n
                and pd.DatetimeIndex(weights.index.get_level_values(-1)).equals(stamps)):
            arr = weights.to_numpy(dtype=float)
        else:
            arr = weights.reindex(stamps).to_numpy(dtype=float)
    elif np.ndim(weights) == 0:
        arr = np.full(n, float(weights))
    else:
        arr = np.asarray(weights, dtype=float)
    if arr.shape != (n,):
        raise ValueError(f"weights must give one value per index row ({n}), got {arr.shape}")
    if not np.isfinite(arr).all() or (arr < 0).any():
        raise ValueError("weights must be finite hours >= 0 covering every index row")
    return arr


def _ic_unit_label(currency: str, unit: str) -> str:
    return {"per_kwh": f"{currency}/kWh", "per_kw_month": f"{currency}/kW/month",
            "per_kw_year": f"{currency}/kW/year", "per_month": f"{currency}/month",
            "per_day": f"{currency}/day", "per_kva_year": f"{currency}/kVA/year"}[unit]


def parse_pack_stamp(stamp: str) -> tuple[str, str, str]:
    """`generic_defaults@2026-10-05:sha256:<hash>` -> (pack id, version, hash)."""
    m = _STAMP_RE.fullmatch(stamp or "")
    if m is None:
        raise DefaultsPackError(f"not a defaults pack stamp: {stamp!r}")
    return m["id"], m["version"], m["hash"]


# ── parsing ─────────────────────────────────────────────────────────────────

_VALUE_COLUMNS = (
    "key", "technology", "part", "basis", "source_technology", "parameter", "label",
    "original_value", "original_unit", "unit", "range_low", "range_high", "range_source",
    "currency", "currency_year", "price_basis", "source", "source_year", "source_url",
    "projection_year", "domain", "illustrative", "derived_inputs", "formula_id", "note",
)
_PART_PARAMETERS = ("overnight", "lifetime", "fom_share", "efficiency")
_DOMAIN_RE = re.compile(r"([\[(])\s*(-?inf|-?[0-9.]+)\s*,\s*(-?inf|-?[0-9.]+)\s*([\])])")


def _opt_float(text: str, where: str) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        v = float(text)
    except ValueError:
        raise DefaultsPackError(f"{where}: {text!r} is not a number") from None
    if not math.isfinite(v):
        raise DefaultsPackError(f"{where}: {text!r} is not finite")
    return v


def _opt_int(text: str, where: str) -> int | None:
    v = _opt_float(text, where)
    if v is not None and v != int(v):
        raise DefaultsPackError(f"{where}: {text!r} is not a whole number")
    return None if v is None else int(v)


def _bool(text: str, where: str) -> bool:
    t = (text or "").strip().lower()
    if t not in ("true", "false"):
        raise DefaultsPackError(f"{where}: illustrative must be true or false, got {text!r}")
    return t == "true"


def _in_domain(domain: str, v: float, where: str) -> None:
    m = _DOMAIN_RE.fullmatch(domain.strip())
    if m is None:
        raise DefaultsPackError(f"{where}: domain {domain!r} is not an interval")
    lo, hi = float(m[2]), float(m[3])
    ok_lo = v >= lo if m[1] == "[" else v > lo
    ok_hi = v <= hi if m[4] == "]" else v < hi
    if not (ok_lo and ok_hi):
        raise DefaultsPackError(f"{where}: {v} is outside {domain}")


def _check_value(v: PackValue, where: str) -> None:
    if v.status == "ok" and v.value is None:
        raise DefaultsPackError(f"{where}: {v.key} has no value")
    if v.domain:
        for x in (v.value, *((v.range.low, v.range.high) if v.range else ())):
            if x is not None:
                _in_domain(v.domain, x, where)
    if v.range is not None and v.range.low > v.range.high:
        raise DefaultsPackError(f"{where}: {v.key} range low > high")


def _parse_values(path: Path) -> list[PackValue]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != _VALUE_COLUMNS:
            raise DefaultsPackError(f"{path.name}: header {reader.fieldnames} != "
                                    f"{list(_VALUE_COLUMNS)}")
        raw_rows = list(reader)
    out: list[PackValue] = []
    for line, raw in enumerate(raw_rows, start=2):
        where = f"{path.name} line {line}"
        r = {k: (raw[k] or "").strip() for k in _VALUE_COLUMNS}
        conv = (r["original_unit"], r["unit"])
        if conv not in UNIT_CONVERSIONS:
            raise DefaultsPackError(f"{where}: no conversion {conv[0]!r} -> {conv[1]!r} "
                                    f"(known: {sorted(UNIT_CONVERSIONS)})")
        factor = UNIT_CONVERSIONS[conv]
        original = _opt_float(r["original_value"], where)
        low, high = _opt_float(r["range_low"], where), _opt_float(r["range_high"], where)
        if (low is None) != (high is None):
            raise DefaultsPackError(f"{where}: {r['key']} has half a range")
        derived = None
        if r["formula_id"] or r["derived_inputs"]:
            if r["formula_id"] not in DERIVED_FORMULAS:
                raise DefaultsPackError(f"{where}: {r['key']} names derived formula "
                                        f"{r['formula_id']!r}, not in the closed registry "
                                        f"{sorted(DERIVED_FORMULAS)}")
            derived = Derived(inputs=[s for s in r["derived_inputs"].split(";") if s],
                              formula_id=r["formula_id"])
        note = r["note"] or None
        status = "ok" if original is not None else "not_available"
        if status == "not_available" and not note:
            raise DefaultsPackError(f"{where}: {r['key']} has no value and no note saying why")
        try:
            v = PackValue(
                key=r["key"], label=r["label"], technology=r["technology"] or None,
                part=r["part"] or None, basis=r["basis"] or None,
                source_technology=r["source_technology"] or None, parameter=r["parameter"],
                value=None if original is None else original * factor, unit=r["unit"],
                original_value=original, original_unit=r["original_unit"],
                conversion_factor=factor,
                range=(None if low is None else
                       PackRange(low=low * factor, high=high * factor,
                                 source=r["range_source"] or "assumed")),
                currency=r["currency"] or None,
                currency_year=_opt_int(r["currency_year"], where),
                price_basis=r["price_basis"] or None, source=r["source"],
                source_year=_opt_int(r["source_year"], where),
                source_url=r["source_url"] or None,
                projection_year=_opt_int(r["projection_year"], where),
                domain=r["domain"] or None, illustrative=_bool(r["illustrative"], where),
                derived=derived, note=note, status=status)
        except ValidationError as exc:
            raise DefaultsPackError(f"{where}: {exc.errors()[0]['loc']}: "
                                    f"{exc.errors()[0]['msg']}") from None
        if v.unit.startswith("EUR") and (v.currency is None or v.currency_year is None):
            raise DefaultsPackError(f"{where}: {v.key} is money and needs a currency and year")
        _check_value(v, where)
        out.append(v)
    by_key = {v.key: v for v in out}
    if len(by_key) != len(out):
        raise DefaultsPackError(f"{path.name}: duplicate keys")
    # A derived row must agree with its formula (to the transcription's 4 decimals); the
    # pack then carries the formula's exact value, so the two cannot drift.
    for i, v in enumerate(out):
        if v.derived is None:
            continue
        missing = [k for k in v.derived.inputs if k not in by_key]
        if missing:
            raise DefaultsPackError(f"{path.name}: {v.key} derives from unknown {missing}")
        exact = derive(v.derived.formula_id, [by_key[k].value for k in v.derived.inputs])
        if v.value is None or not math.isclose(v.value, exact, rel_tol=0, abs_tol=5e-5):
            raise DefaultsPackError(f"{path.name}: {v.key}={v.value} but "
                                    f"{v.derived.formula_id} gives {exact}")
        out[i] = v.model_copy(update={"value": exact})
    # Every part row names a basis the part's overnight unit agrees with.
    for tech in {v.technology for v in out if v.part}:
        _cost_parts(out, tech)
    return out


def _cost_parts(values: Sequence[PackValue], technology: str) -> list[CostPart]:
    by_part: dict[str, dict[str, PackValue]] = {}
    for v in values:
        if v.technology != technology or v.part is None or v.parameter not in _PART_PARAMETERS:
            continue
        by_part.setdefault(v.part, {})[v.parameter] = v
    out = []
    for part, fields in by_part.items():
        if "overnight" not in fields:
            raise DefaultsPackError(f"{technology}.{part} has no overnight cost")
        overnight = fields["overnight"]
        if {(f.basis, f.source_technology) for f in fields.values()} != \
                {(overnight.basis, overnight.source_technology)}:
            raise DefaultsPackError(f"{technology}.{part}: rows disagree on basis or "
                                    "source technology")
        if overnight.basis is None or overnight.unit != _BASIS_UNIT[overnight.basis]:
            raise DefaultsPackError(f"{technology}.{part}: basis {overnight.basis} needs "
                                    f"overnight in {_BASIS_UNIT.get(overnight.basis)}, got "
                                    f"{overnight.unit}")
        out.append(CostPart(technology=technology, part=part, basis=overnight.basis,
                            source_technology=overnight.source_technology or technology,
                            **fields))
    order = {"power": 0, "energy": 1, "investment": 2}
    return sorted(out, key=lambda p: order[p.part])


def _finance_value(spec: Mapping[str, Any], parameter: str, where: str) -> PackValue:
    try:
        rng = spec.get("range")
        v = PackValue(
            key=spec["key"], label=spec["label"], parameter=parameter,
            value=float(spec["value"]), unit=spec["unit"],
            original_value=float(spec["value"]), original_unit=spec["unit"],
            conversion_factor=1.0, range=None if rng is None else PackRange(**rng),
            price_basis=spec.get("price_basis"), source=" ".join(str(spec["source"]).split()),
            source_year=int(spec["source_year"]), source_url=spec.get("source_url"),
            domain=spec.get("domain"), illustrative=spec["illustrative"])
    except (KeyError, TypeError, ValidationError) as exc:
        raise DefaultsPackError(f"{where}: {parameter}: {exc}") from None
    _check_value(v, where)
    return v


def _parse_finance(path: Path, keys: set[str]) -> FinanceDefaults:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DefaultsPackError(f"{path.name}: not a mapping")
    body = {k: (" ".join(v.split()) if isinstance(v, str) else v) for k, v in raw.items()}
    body["discount_rate"] = _finance_value(raw.get("discount_rate") or {}, "discount_rate",
                                           path.name)
    body["sizing_limit"] = _finance_value(raw.get("sizing_limit") or {},
                                          "sizing_limit_connection_multiple", path.name)
    try:
        fin = FinanceDefaults.model_validate(body)
    except ValidationError as exc:
        raise DefaultsPackError(f"{path.name}: {exc.errors()[0]['loc']}: "
                                f"{exc.errors()[0]['msg']}") from None
    # The rules name pack keys: each must exist, so a renamed row cannot orphan a rule.
    refs = [fin.horizon_rule.horizon_years]
    for rule in fin.replacement_rules:
        refs += [rule.every_years, rule.cost]
    missing = sorted(set(refs) - keys)
    if missing:
        raise DefaultsPackError(f"{path.name}: rules name unknown pack keys {missing}")
    return fin


def _parse_tariffs(path: Path) -> dict[str, PackTariff]:
    from services.library.items import validate_payload

    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, PackTariff] = {}
    for entry in raw.get("tariffs") or []:
        payload = entry.get("tariff") or {}
        where = f"{path.name} tariff {payload.get('id')!r}"
        if payload.get("pack_hash") is not None:
            raise DefaultsPackError(f"{where}: pack_hash is stamped by the loader; leave it null")
        try:
            tariff = validate_payload("tariff", payload)
            meta = PackTariffMeta.model_validate(entry.get("meta") or {})
        except (ValueError, ValidationError) as exc:
            raise DefaultsPackError(f"{where}: {exc}") from None
        items = {i.id: i for i in tariff.items}
        if set(meta.items) != set(items):
            raise DefaultsPackError(f"{where}: meta.items {sorted(meta.items)} != tariff items "
                                    f"{sorted(items)}")
        for item_id, rows in meta.items.items():
            item = items[item_id]
            named = {p.name for p in item.periods}
            covered: set[str] = set()
            for src in rows:
                conv = (src.original_unit, item.unit)
                if conv not in TARIFF_CONVERSIONS:
                    raise DefaultsPackError(f"{where}: no conversion {conv[0]!r} -> {conv[1]!r}")
                expected = src.original_price * TARIFF_CONVERSIONS[conv]
                for name in src.periods:
                    rates = [p.rate for p in item.periods if p.name == name]
                    if not rates:
                        raise DefaultsPackError(f"{where}: {item_id} has no period {name!r}")
                    for r in rates:
                        if not math.isclose(r, expected, rel_tol=1e-12, abs_tol=1e-12):
                            raise DefaultsPackError(
                                f"{where}: {item_id}/{name} rate {r} != {src.original_price} "
                                f"{src.original_unit} x {TARIFF_CONVERSIONS[conv]}")
                    covered.add(name)
            if covered != named:
                raise DefaultsPackError(f"{where}: {item_id} periods without a seed row: "
                                        f"{sorted(named - covered)}")
        if tariff.id in out:
            raise DefaultsPackError(f"{where}: duplicate tariff id")
        out[tariff.id] = PackTariff(tariff=tariff, meta=meta)
    if not out:
        raise DefaultsPackError(f"{path.name}: no tariff")
    defaults = [t for t, p in out.items() if p.meta.default]
    if len(defaults) != 1:
        raise DefaultsPackError(f"{path.name}: exactly one tariff is the default, got {defaults}")
    return out


def _file_sha256(path: Path) -> str:
    # LF-normalised: a CRLF checkout of the same text is the same content.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _parse_profile(directory: Path, spec: Mapping[str, Any]) -> LoadProfile:
    where = f"load profile {spec.get('id')!r}"
    rel = Path(str(spec.get("file") or ""))
    if rel.is_absolute() or ".." in rel.parts:
        raise DefaultsPackError(f"{where}: file must be relative to the version directory")
    path = directory / rel
    if not path.is_file():
        raise DefaultsPackError(f"{where}: {rel} is missing")
    digest = _file_sha256(path)
    if digest != spec.get("sha256"):
        raise DefaultsPackError(f"{where}: {rel} sha256 {digest} does not match the manifest "
                                f"({spec.get('sha256')})")
    table: dict[tuple[int, str, int], float] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != ("month", "daytype", "hour", "factor"):
            raise DefaultsPackError(f"{where}: header {reader.fieldnames}")
        for line, r in enumerate(reader, start=2):
            try:
                k = (int(r["month"]), r["daytype"], int(r["hour"]))
                f = float(r["factor"])
            except (TypeError, ValueError):
                raise DefaultsPackError(f"{where}: line {line} is not month,daytype,hour,factor") \
                    from None
            if not (math.isfinite(f) and f >= 0) or k in table:
                raise DefaultsPackError(f"{where}: line {line}: bad or duplicate factor")
            table[k] = f
    want = [(m, d, h) for m in range(1, 13) for d in _DAYTYPES for h in range(24)]
    if set(table) != set(want):
        raise DefaultsPackError(f"{where}: needs exactly 12 x 2 x 24 factors")
    try:
        return LoadProfile(**{**spec, "factors": tuple(table[k] for k in want)})
    except ValidationError as exc:
        raise DefaultsPackError(f"{where}: {exc}") from None


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    try:
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DefaultsPackError(f"pack content is not canonical JSON: {exc}") from None
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def canonical_payload(directory: Path) -> dict[str, Any]:
    """What the pack hash covers: the RAW files, never a model's dump, so a model change
    (a new default field) cannot re-hash a shipped version (review B4; the tax packs'
    `canonical_payload` recipe). `values.csv` rows as strings (header first),
    `finance.yaml` and `tariffs.json` as loaded (`_comment` dropped), the manifest
    without `files` (file names, not content), and each load profile file's sha256 of
    its LF-normalised bytes."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files") or {}
    with (directory / files.get("values", "values.csv")).open(encoding="utf-8",
                                                               newline="") as fh:
        rows = [list(r) for r in csv.reader(fh)]
    finance = yaml.safe_load((directory / files.get("finance", "finance.yaml"))
                             .read_text(encoding="utf-8"))
    tariffs = json.loads((directory / files.get("tariffs", "tariffs.json"))
                         .read_text(encoding="utf-8"))
    if isinstance(tariffs, dict):
        tariffs.pop("_comment", None)
    profile_files = {}
    for spec in manifest.get("load_profiles") or []:
        rel = str(spec.get("file") or "")
        path = directory / rel
        if not rel or not path.is_file():
            raise DefaultsPackError(f"load profile file {rel!r} is missing")
        profile_files[rel] = _file_sha256(path)
    return {"manifest": {k: v for k, v in manifest.items() if k != "files"},
            "values": rows, "finance": finance, "tariffs": tariffs,
            "load_profile_files": profile_files}


def parse_pack_dir(directory: Path) -> DefaultsPack:
    """Parse and check one version directory (any location: the hash covers content only)."""
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise DefaultsPackError(f"{directory}: no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("pack_id") != PACK_ID:
        raise DefaultsPackError(f"manifest pack_id {manifest.get('pack_id')!r} != {PACK_ID!r}")
    version = str(manifest.get("version") or "")
    if not _VERSION_RE.fullmatch(version):
        raise DefaultsPackError(f"manifest version {version!r} is not YYYY-MM-DD")
    files = manifest.get("files") or {}
    values = _parse_values(directory / files.get("values", "values.csv"))
    finance = _parse_finance(directory / files.get("finance", "finance.yaml"),
                             {v.key for v in values})
    tariffs = _parse_tariffs(directory / files.get("tariffs", "tariffs.json"))
    profiles = {}
    for spec in manifest.get("load_profiles") or []:
        prof = _parse_profile(directory, spec)
        if prof.id in profiles:
            raise DefaultsPackError(f"duplicate load profile {prof.id!r}")
        profiles[prof.id] = prof
    for tid, pt in tariffs.items():
        if (pt.meta.currency, pt.meta.currency_year) != (finance.currency, finance.currency_year):
            raise DefaultsPackError(f"tariff {tid!r} is in {pt.meta.currency} "
                                    f"{pt.meta.currency_year}; the pack is in "
                                    f"{finance.currency} {finance.currency_year}")
    # Validation (above) and hashing (here) are separate: the hash reads the files only.
    digest = _canonical_hash(canonical_payload(directory))
    raw_tariffs = json.loads((directory / files.get("tariffs", "tariffs.json"))
                             .read_text(encoding="utf-8"))
    pristine = tuple((e["tariff"]["id"], json.dumps(e["tariff"], sort_keys=True))
                     for e in raw_tariffs["tariffs"])
    pack = DefaultsPack(pack_id=PACK_ID, version=version,
                        title=str(manifest.get("title") or ""),
                        seeded_from=manifest.get("seeded_from") or {}, hash=digest,
                        cost_values=values, finance=finance, tariffs=tariffs,
                        load_profiles=profiles)
    pack._tariff_json = pristine
    return pack


# ── versions ────────────────────────────────────────────────────────────────


def available_versions() -> tuple[str, ...]:
    """Every vendored version, oldest first."""
    if not VERSIONS_DIR.is_dir():
        return ()
    return tuple(sorted(p.name for p in VERSIONS_DIR.iterdir()
                        if p.is_dir() and _VERSION_RE.fullmatch(p.name)
                        and (p / "manifest.json").is_file()))


def version_dir(version: str) -> Path:
    if version not in available_versions():
        raise UnknownPackVersion(f"defaults pack version {version!r} is not vendored; nothing "
                                 "is downloaded", available_versions())
    return VERSIONS_DIR / version


@functools.lru_cache(maxsize=8)
def _cached(version: str) -> DefaultsPack:
    pack = parse_pack_dir(version_dir(version))
    if pack.version != version:
        raise DefaultsPackError(f"directory {version} holds manifest version {pack.version}")
    return pack


def load_defaults_pack(version: str | None = None) -> DefaultsPack:
    """The vendored pack `version` (None: the latest). Each call returns an independent
    deep copy, so a caller's edit never reaches the next caller."""
    if version is None:
        versions = available_versions()
        if not versions:
            raise UnknownPackVersion("no defaults pack version is vendored", ())
        version = versions[-1]
    return _cached(version).model_copy(deep=True)
