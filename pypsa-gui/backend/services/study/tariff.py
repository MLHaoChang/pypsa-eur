"""
Tariff prices as network data, and the bill calculator (MVP-1 phase S3).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S3)
Review deltas: B2 (prices are permanent network data; the demand charge is a
solver-config field; `demand_charge_eur` recomputed from `links_t.p0`), N13.

Three tariff semantics are pinned here (carried from the S1 gate,
docs/superpowers/notes/2026-09-29-mvp1-s1-gate.md):

* **Band precedence: first match wins.** Energy bands are tried in list
  order; a snapshot takes the price of the first band whose `TimeRule`
  matches it. A band set that leaves any snapshot unpriced is refused
  (`UnpricedHoursError`), never priced at zero.
* **Export pricing is one or the other.** `ExportCompensation.price_per_mwh`
  or `ExportCompensation.series_ref`, never both (`ExportPricingError`).
  Writing prices for an export link with neither is refused too; the bill
  calculator reports the export credit as null with a flag instead.
* **Demand-charge basis.** MVP-1 models one billing-period demand charge.
  `annual_peak` and `ratchet` are refused with `UnsupportedTariffError`
  (code `demand_charge_basis_<basis>`), here and in the LP wrapper
  (`services/solver/objective.py::_wrap_with_demand_charge`). A `measured`
  `CapacityCharge` is the same annual-peak charge under another field and is
  refused too (code `capacity_charge_measured_unsupported`); `contracted` is
  priced on `connection_limit_mw`.

`TimeRule` fields read the snapshot timestamp as given (month 1..12, weekday
0 = Monday, hour of the snapshot's start); snapshots are local clock time.

The bill is one engine (`engine="bill_calculator"`, stated on every bill:
since U2 WP8 the model's default is the Investment Case engine's
`tariff_engine`, and this calculator is the findings' fallback until WP10): the LP wrapper prices
the demand charge from the same spec, and the objective decomposition
recomputes `demand_charge_eur` through `BillCalculator.demand_charge` from
`links_t.p0`, never from `n.model`.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import ValidationError

from models.study import (
    Bill,
    BillComponents,
    DemandCharge,
    EnergyBand,
    ExportCompensation,
    Fidelity,
    Tariff,
    TimeRule,
)

__all__ = [
    "Bill", "BillCalculator", "BillComponents", "DemandChargeSpec",
    "ExportPricingError", "REFUSED_DEMAND_CHARGE_BASES", "TariffError",
    "UnpricedHoursError", "UnsupportedTariffError", "band_prices",
    "billing_period_labels", "demand_charge_config",
    "demand_charge_eur_from_network", "parse_demand_charge_config",
    "tariff_from_ledger", "validate_tariff_intake", "write_tariff_prices",
]

REFUSED_DEMAND_CHARGE_BASES = ("annual_peak", "ratchet")
BillingPeriod = Literal["month", "year"]
_ONE_YEAR_HOURS = (8760.0, 8784.0)
_NETWORK_CHARGE_BASES = ("per_mwh", "per_period", "per_year")


# ── typed errors ──────────────────────────────────────────────────────────

class TariffError(ValueError):
    """A tariff the engine will not price. `code` is stable and machine-read."""

    code: str = "tariff_invalid"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class UnpricedHoursError(TariffError):
    code = "tariff_unpriced_hours"


class ExportPricingError(TariffError):
    code = "tariff_export_pricing"


class UnsupportedTariffError(TariffError):
    code = "tariff_unsupported"


# ── calendar helpers ──────────────────────────────────────────────────────

def _require_datetime_index(index, what: str) -> pd.DatetimeIndex:
    if not isinstance(index, pd.DatetimeIndex):
        raise UnsupportedTariffError(
            f"{what} needs a flat DatetimeIndex of snapshots; got "
            f"{type(index).__name__}. Tariff time rules and billing periods "
            "read the snapshot timestamp.",
            code="tariff_snapshots_not_flat")
    return index


def billing_period_labels(index, billing_period: BillingPeriod) -> np.ndarray:
    """One label per snapshot: `YYYY-MM` for a month, `YYYY` for a year."""
    idx = _require_datetime_index(index, "a billing period")
    if billing_period == "month":
        return np.asarray(idx.strftime("%Y-%m"))
    if billing_period == "year":
        return np.asarray(idx.strftime("%Y"))
    raise TariffError(f"unknown billing period {billing_period!r}",
                      code="tariff_billing_period")


def _ordered_unique(labels: np.ndarray) -> list[str]:
    return list(dict.fromkeys(labels.tolist()))


def _rule_mask(idx: pd.DatetimeIndex, rule: TimeRule) -> np.ndarray:
    mask = np.ones(len(idx), dtype=bool)
    if rule.months:
        mask &= np.isin(idx.month, rule.months)
    if rule.weekdays:
        mask &= np.isin(idx.weekday, rule.weekdays)
    if rule.hours:
        mask &= np.isin(idx.hour, rule.hours)
    return mask


def band_prices(index, bands: list[EnergyBand]) -> pd.Series:
    """
    The energy price of every snapshot, first matching band wins. Refuses
    (`UnpricedHoursError`) when any snapshot matches no band.
    """
    idx = _require_datetime_index(index, "energy-band pricing")
    price = np.full(len(idx), np.nan)
    for band in bands:
        m = _rule_mask(idx, band.applies) & np.isnan(price)
        price[m] = float(band.price_per_mwh)
    unpriced = np.isnan(price)
    if unpriced.any():
        first = ", ".join(str(t) for t in idx[unpriced][:3])
        raise UnpricedHoursError(
            f"{int(unpriced.sum())} of {len(idx)} snapshot(s) match no energy "
            f"band (first: {first}). Bands are matched first-match-wins and "
            "must price every hour; add a catch-all band (`applies: {}`) last.")
    return pd.Series(price, index=idx, name="energy_price")


def _network_per_mwh(index, tariff: Tariff) -> pd.Series:
    adder = 0.0
    for nc in tariff.network_charges:
        if nc.basis not in _NETWORK_CHARGE_BASES:
            raise UnsupportedTariffError(
                f"network charge {nc.label!r} has basis {nc.basis!r}; MVP-1 "
                f"prices {', '.join(_NETWORK_CHARGE_BASES)}",
                code="network_charge_basis_unsupported")
        if nc.basis == "per_mwh":
            adder += float(nc.price)
    return pd.Series(adder, index=index)


def _export_price(index, export: ExportCompensation,
                  series: Mapping[str, pd.Series] | None) -> pd.Series | None:
    """The export credit per MWh (positive), or None when none is given."""
    if export.price_per_mwh is not None and export.series_ref is not None:
        raise ExportPricingError(
            "export compensation gives both price_per_mwh and series_ref; "
            "it is one or the other")
    if export.cap_mw is not None:
        raise UnsupportedTariffError(
            "export compensation cap_mw is not modelled in MVP-1 (a single "
            "link price cannot stop paying above a cap)",
            code="export_cap_unsupported")
    if export.price_per_mwh is not None:
        return pd.Series(float(export.price_per_mwh), index=index)
    if export.series_ref is not None:
        src = (series or {}).get(export.series_ref)
        if src is None:
            raise ExportPricingError(
                f"export series_ref {export.series_ref!r} does not resolve",
                code="tariff_export_series_missing")
        out = pd.Series(src).reindex(index).astype(float)
        if out.isna().any():
            raise ExportPricingError(
                f"export series {export.series_ref!r} does not cover every "
                "snapshot", code="tariff_export_series_missing")
        return out
    return None


def _refuse_basis(basis: str) -> None:
    if basis in REFUSED_DEMAND_CHARGE_BASES:
        raise UnsupportedTariffError(
            f"demand-charge basis {basis!r} is not modelled in MVP-1 (one "
            "billing-period demand charge only); it is refused rather than "
            "solved as a billing-period peak",
            code=f"demand_charge_basis_{basis}")


def _refuse_measured_capacity(tariff: Tariff) -> None:
    """
    A `measured` capacity charge prices the horizon's peak import, which the
    LP never carries: it is the refused `annual_peak` basis arriving through
    `CapacityCharge` (gate S3 BC-S3-2). MVP-1 prices `contracted` only.
    """
    cc = tariff.capacity_charge
    if cc is not None and cc.basis == "measured":
        raise UnsupportedTariffError(
            "a measured capacity charge prices the horizon's peak import, "
            "which the LP does not model in MVP-1; it is refused rather than "
            "billed on a peak the sizing never saw. Use basis 'contracted' "
            "or a billing-period demand charge.",
            code="capacity_charge_measured_unsupported")


def _partial_periods(w: pd.Series, labels: np.ndarray,
                     billing_period: BillingPeriod) -> list[str]:
    """Billing periods the snapshots cover for fewer hours than they last."""
    covered = w.groupby(labels, sort=False).sum()
    out: list[str] = []
    for label, hours in covered.items():
        start = pd.Timestamp(f"{label}-01" if billing_period == "month" else f"{label}-01-01")
        end = start + (pd.DateOffset(months=1) if billing_period == "month"
                       else pd.DateOffset(years=1))
        if float(hours) < (end - start) / pd.Timedelta(hours=1) - 1e-6:
            out.append(str(label))
    return out


# ── prices as network data ────────────────────────────────────────────────

def write_tariff_prices(n, tariff: Tariff, import_link: str,
                        export_link: str | None, *,
                        series: Mapping[str, pd.Series] | None = None) -> dict:
    """
    Write the tariff's energy prices into `n.links_t.marginal_cost` as
    permanent network data: the import link pays the band price plus every
    per-MWh network charge (positive); the export link is credited the
    export price (negative marginal cost). Nothing is reverted after a solve.

    Returns ``{"import": Series, "export": Series | None}``, the prices written.
    """
    idx = _require_datetime_index(n.snapshots, "write_tariff_prices")
    _refuse_measured_capacity(tariff)
    for name in (import_link, export_link):
        if name is not None and name not in n.links.index:
            raise TariffError(f"link {name!r} is not in the network",
                              code="tariff_unknown_link")
    imp = band_prices(idx, tariff.energy_bands) + _network_per_mwh(idx, tariff)
    exp = None
    if export_link is not None:
        credit = _export_price(idx, tariff.export, series)
        if credit is None:
            raise ExportPricingError(
                "an export link needs price_per_mwh or series_ref (one or "
                "the other); the tariff gives neither")
        exp = -credit
    mc = n.links_t.marginal_cost
    mc[import_link] = imp.to_numpy()
    if exp is not None:
        mc[export_link] = exp.to_numpy()
    return {"import": imp, "export": exp}


# ── the demand-charge solver-config spec ──────────────────────────────────

@dataclass(frozen=True)
class DemandChargeSpec:
    """`SolverConfig.demand_charge`, parsed. Only the charge and the links."""

    price_per_mw_per_period: float
    billing_period: BillingPeriod
    import_links: tuple[str, ...]
    basis: str = "billing_period_peak"


_SPEC_KEYS = frozenset({"price_per_mw_per_period", "basis", "ratchet",
                        "billing_period", "import_links"})


def parse_demand_charge_config(raw: Mapping) -> DemandChargeSpec:
    """
    Parse `SolverConfig.demand_charge`:
    ``{"price_per_mw_per_period", "basis", "billing_period", "import_links"}``.
    Refuses `annual_peak` and `ratchet` first (typed, before any other
    check), then unknown keys, a bad price, a bad billing period or an empty
    import-link list (`TariffError`, code `demand_charge_invalid`).
    """
    if not isinstance(raw, Mapping):
        raise TariffError("demand_charge must be a mapping",
                          code="demand_charge_invalid")
    _refuse_basis(str(raw.get("basis", "billing_period_peak")))
    stray = set(raw) - _SPEC_KEYS
    if stray:
        raise TariffError(f"demand_charge has unknown key(s) {sorted(stray)}",
                          code="demand_charge_invalid")
    try:
        dc = DemandCharge.model_validate(
            {k: raw[k] for k in ("price_per_mw_per_period", "basis", "ratchet")
             if k in raw})
    except ValidationError as exc:
        raise TariffError(f"demand_charge is invalid: {exc.errors()[0]['msg']}",
                          code="demand_charge_invalid") from exc
    if not math.isfinite(dc.price_per_mw_per_period):
        raise TariffError("demand_charge price must be finite",
                          code="demand_charge_invalid")
    period = raw.get("billing_period", "month")
    if period not in ("month", "year"):
        raise TariffError(f"demand_charge billing_period {period!r} is not "
                          "'month' or 'year'", code="demand_charge_invalid")
    links = raw.get("import_links")
    if (not isinstance(links, (list, tuple)) or not links
            or not all(isinstance(x, str) and x for x in links)):
        raise TariffError("demand_charge needs a non-empty import_links list",
                          code="demand_charge_invalid")
    return DemandChargeSpec(
        price_per_mw_per_period=float(dc.price_per_mw_per_period),
        billing_period=period, import_links=tuple(dict.fromkeys(links)),
        basis=dc.basis)


# ── the bill ──────────────────────────────────────────────────────────────

def _weights(snapshot_weightings) -> pd.Series:
    if isinstance(snapshot_weightings, pd.DataFrame):
        col = ("generators" if "generators" in snapshot_weightings.columns
               else snapshot_weightings.columns[0])
        w = snapshot_weightings[col]
    else:
        w = pd.Series(snapshot_weightings)
    return w.astype(float)


def _aligned(series, index, what: str) -> pd.Series | None:
    if series is None:
        return None
    out = pd.Series(series).reindex(index).astype(float)
    if out.isna().any():
        raise TariffError(f"{what} series does not cover every snapshot",
                          code="tariff_series_misaligned")
    return out


def _itemised(tariff: Tariff) -> list[str]:
    """
    The bill components this tariff carries a charge for (U2 gate: a zero
    stream with no item is hidden). Energy always; no tax item exists here.
    """
    out = ["energy"]
    if tariff.demand_charge is not None:
        out.append("demand")
    if tariff.capacity_charge is not None:
        out.append("capacity")
    if tariff.fixed_charge_per_period:
        out.append("fixed")
    if tariff.network_charges:
        out.append("network")
    if tariff.export.price_per_mwh is not None or tariff.export.series_ref is not None:
        out.append("export_credit")
    return out


class BillCalculator:
    """
    Prices a site's import and export series under a `Tariff`. `series`
    resolves `ExportCompensation.series_ref`.
    """

    def __init__(self, series: Mapping[str, pd.Series] | None = None):
        self.series = dict(series or {})

    @staticmethod
    def demand_charge(import_mw: pd.Series, price_per_mw_per_period: float,
                      billing_period: BillingPeriod) -> tuple[float, dict[str, float]]:
        """Σ over billing periods of price × that period's peak import (MW)."""
        labels = billing_period_labels(import_mw.index, billing_period)
        peaks = import_mw.groupby(labels, sort=False).max()
        peaks = {str(k): float(v) for k, v in peaks.items()}
        return float(price_per_mw_per_period) * sum(peaks.values()), peaks

    def bill(self, import_mw: pd.Series | None, export_mw: pd.Series | None,
             tariff: Tariff, snapshot_weightings, *,
             fidelity: Fidelity | str | None = None) -> Bill:
        """
        The bill (``models.study.Bill``, the one class; this module only
        re-exports it). ``fidelity`` names the run the dispatch came from —
        the calculator cannot know it, the caller states it (gate S3 N3).
        """
        w = _weights(snapshot_weightings)
        idx = _require_datetime_index(w.index, "the bill")
        imp = _aligned(import_mw, idx, "import")
        exp = _aligned(export_mw, idx, "export")
        if tariff.demand_charge is not None:
            _refuse_basis(tariff.demand_charge.basis)
        _refuse_measured_capacity(tariff)
        # Validate every price before any figure is computed, so a tariff
        # the engine refuses is refused whatever series are present.
        energy_price = band_prices(idx, tariff.energy_bands)
        per_mwh_network = _network_per_mwh(idx, tariff)
        export_price = _export_price(idx, tariff.export, self.series)

        labels = billing_period_labels(idx, tariff.billing_period)
        periods = _ordered_unique(labels)
        hours = float(w.sum())
        years = hours / 8760.0
        flags: dict[str, str] = {}
        notes: list[str] = []
        comp: dict[str, float | None] = {}

        if imp is None:
            comp["energy"] = None
            flags["energy"] = "no_import_series"
        else:
            comp["energy"] = float((imp * w * energy_price).sum())

        peaks: dict[str, float] = {}
        if tariff.demand_charge is None:
            comp["demand"] = 0.0
        elif imp is None:
            comp["demand"] = None
            flags["demand"] = "no_import_series"
        else:
            comp["demand"], peaks = self.demand_charge(
                imp, tariff.demand_charge.price_per_mw_per_period,
                tariff.billing_period)

        cc = tariff.capacity_charge
        if cc is None:
            comp["capacity"] = 0.0
        elif tariff.connection_limit_mw is None:  # contracted (measured refused)
            comp["capacity"] = None
            flags["capacity"] = "no_contracted_mw"
        else:
            comp["capacity"] = (cc.price_per_mw_per_year
                                * float(tariff.connection_limit_mw) * years)
        if cc is not None:
            notes.append("capacity_charge_prorated_by_hours")

        comp["fixed"] = float(tariff.fixed_charge_per_period) * len(periods)

        needs_import = any(nc.basis == "per_mwh" for nc in tariff.network_charges)
        if needs_import and imp is None:
            comp["network"] = None
            flags["network"] = "no_import_series"
        else:
            net = 0.0
            if imp is not None:
                net += float((imp * w * per_mwh_network).sum())
            for nc in tariff.network_charges:
                if nc.basis == "per_period":
                    net += float(nc.price) * len(periods)
                elif nc.basis == "per_year":
                    net += float(nc.price) * years
            comp["network"] = net

        if exp is None:
            comp["export_credit"] = None
            flags["export_credit"] = "no_export_series"
        elif export_price is None:
            # Nothing exported is a real zero whatever the (absent) price.
            if float(exp.abs().sum()) == 0.0:
                comp["export_credit"] = 0.0
            else:
                comp["export_credit"] = None
                flags["export_credit"] = "no_export_price"
        else:
            # `+ 0.0` turns a negated zero into 0.0 (no `-0.0` in JSON).
            comp["export_credit"] = -float((exp * w * export_price).sum()) + 0.0

        components = BillComponents(**comp, unavailable=flags)
        bill_flags: dict[str, str] = {}
        if flags:
            total = None
            bill_flags["total"] = "component_unavailable"
        else:
            total = float(sum(v for v in comp.values() if v is not None))
        if total is None:
            annual = None
            bill_flags["annual_bill"] = "component_unavailable"
        elif any(abs(hours - h) < 1e-6 for h in _ONE_YEAR_HOURS):
            annual = total
        else:
            annual = None
            bill_flags["annual_bill"] = "horizon_not_one_year"
        partial = _partial_periods(w, labels, tariff.billing_period)
        per_period_charge = (
            tariff.demand_charge is not None or bool(tariff.fixed_charge_per_period)
            or any(nc.basis == "per_period" for nc in tariff.network_charges))
        if partial and per_period_charge:
            notes.insert(0, "partial_billing_period_charged_in_full")
        return Bill(
            total=total, annual_bill=annual, by_component=components,
            peak_mw_by_billing_period=peaks, billing_periods=periods,
            horizon_hours=hours, currency=tariff.currency,
            currency_year=tariff.currency_year, unavailable=bill_flags,
            # U2 WP8 (decision B1): GS's calculator names itself; the model's
            # default is the Investment Case engine's `tariff_engine`.
            engine="bill_calculator",
            fidelity=None if fidelity is None else Fidelity(fidelity),
            honesty_notes=tuple(notes), partial_billing_periods=partial,
            itemised_components=_itemised(tariff))


#: `n.meta` key holding the demand-charge config the last successful LOPF
#: solve carried (`None` when it carried none). Written by `run_simulation`;
#: it travels with the network through a save, a reload and a fork copy.
SOLVE_DEMAND_CHARGE_META = "pypsa_gui_solve_demand_charge"


def record_solved_demand_charge(n, raw) -> None:
    """Record on `n` the demand-charge config its solve carried."""
    n.meta[SOLVE_DEMAND_CHARGE_META] = copy.deepcopy(raw) if raw else None


def solved_demand_charge_config(n, fallback):
    """
    The demand-charge config the network's dispatch was solved with, as
    `record_solved_demand_charge` stored it. A network solved before that
    record existed has no key, and `fallback` (the caller's config) is the
    best that can be said for it (gate S3 [N4]).
    """
    meta = getattr(n, "meta", None) or {}
    if SOLVE_DEMAND_CHARGE_META in meta:
        return meta[SOLVE_DEMAND_CHARGE_META]
    return fallback


def demand_charge_eur_from_network(n, raw) -> float | None:
    """
    `demand_charge_eur` after a solve, recomputed from `links_t.p0` by the
    bill calculator (never from `n.model`). ``0.0`` when no demand charge is
    configured (the LP had no such term); ``None`` when one is configured but
    the import links carry no dispatch.
    """
    if not raw:
        return 0.0
    spec = parse_demand_charge_config(raw)
    p0 = getattr(n.links_t, "p0", None)
    if p0 is None or p0.empty or any(ln not in p0.columns for ln in spec.import_links):
        return None
    import_mw = p0[list(spec.import_links)].sum(axis=1)
    total, _ = BillCalculator.demand_charge(
        import_mw, spec.price_per_mw_per_period, spec.billing_period)
    return total


# ── S4: the one tariff the pack, the LP and the bill all read ─────────────

def validate_tariff_intake(tariff: Tariff) -> None:
    """
    Refuse at intake what the engine would refuse at run time (gate S3 N7,
    N-v2-2): a `measured` capacity charge, an `annual_peak` or `ratchet`
    demand charge, an export given as both a price and a series, an export
    cap, and a network-charge basis MVP-1 does not price. Typed
    (`TariffError` and subclasses), so the route answers 422 with the code.
    """
    _refuse_measured_capacity(tariff)
    if tariff.demand_charge is not None:
        _refuse_basis(tariff.demand_charge.basis)
    if tariff.export.price_per_mwh is not None and tariff.export.series_ref is not None:
        raise ExportPricingError(
            "export compensation gives both price_per_mwh and series_ref; "
            "it is one or the other")
    if tariff.export.cap_mw is not None:
        raise UnsupportedTariffError(
            "export compensation cap_mw is not modelled in MVP-1",
            code="export_cap_unsupported")
    for nc in tariff.network_charges:
        if nc.basis not in _NETWORK_CHARGE_BASES:
            raise UnsupportedTariffError(
                f"network charge {nc.label!r} has basis {nc.basis!r}; MVP-1 "
                f"prices {', '.join(_NETWORK_CHARGE_BASES)}",
                code="network_charge_basis_unsupported")


def _ledger_value(ledger, key: str) -> float | None:
    for row in ledger.rows:
        if row.key == key:
            return row.value
    raise TariffError(f"the ledger has no {key!r} row", code="ledger_row_missing")


def tariff_from_ledger(tariff: Tariff, ledger, snapshots,
                       snapshot_weightings=None) -> Tariff:
    """
    The tariff every S4+ engine prices with: the chosen tariff's structure
    (bands, billing period, bases) with the LEDGER's numbers for the two key
    drivers the ledger owns (gate S2 [S8], [S9]):

    * **demand charge price** — `demand_charge_price` replaces the tariff's
      own figure (the ledger is the single source; a tariff without a demand
      charge keeps none, whatever the row says — a stale user value there is
      `needs_attention` and refused before this is reached);
    * **energy price level** — each energy band's price becomes
      ``m + level x (p - m)``, where ``m`` is the bands' time-weighted mean
      over ``snapshots`` (weighted by ``snapshot_weightings``, default 1). The
      average energy price is unchanged; the time-of-use spread scales. A
      single-band tariff is unchanged. Network charges, the demand charge and
      the export credit are not scaled.

    Returns a new `Tariff`; the input is not modified. The pack
    (`write_tariff_prices`), the LP (`demand_charge_config`) and the bill
    (`BillCalculator.bill`) all take THIS object, so they cannot disagree.
    """
    validate_tariff_intake(tariff)
    idx = _require_datetime_index(pd.Index(snapshots) if not isinstance(
        snapshots, pd.DatetimeIndex) else snapshots, "tariff_from_ledger")
    data = tariff.model_dump()
    if tariff.demand_charge is not None:
        price = _ledger_value(ledger, "demand_charge_price")
        if price is None:
            raise TariffError(
                "the tariff has a demand charge but the ledger's "
                "demand_charge_price row has no value", code="ledger_row_missing")
        data["demand_charge"]["price_per_mw_per_period"] = float(price)
    level = _ledger_value(ledger, "energy_price_level")
    level = 1.0 if level is None else float(level)
    if not math.isfinite(level) or level <= 0.0:
        raise TariffError(f"energy_price_level must be > 0, got {level!r}",
                          code="energy_price_level_invalid")
    if level != 1.0 and tariff.energy_bands:
        prices = band_prices(idx, tariff.energy_bands)
        if snapshot_weightings is None:
            w = pd.Series(1.0, index=idx)
        else:
            w = _weights(snapshot_weightings).reindex(idx).fillna(0.0)
        mean = float((prices * w).sum() / w.sum()) if float(w.sum()) > 0 else float(prices.mean())
        for band in data["energy_bands"]:
            band["price_per_mwh"] = mean + level * (float(band["price_per_mwh"]) - mean)
        data["honesty_notes"] = [*data.get("honesty_notes", []),
                                 "energy_bands_scaled_by_energy_price_level"]
    return Tariff.model_validate(data)


def demand_charge_config(tariff: Tariff, import_links) -> dict | None:
    """
    `SolverConfig.demand_charge` from the (ledger-applied) tariff, or None
    when it has no demand charge. The ONE place the LP's billing period is
    set, and it is the tariff's own `billing_period` — the field the bill
    calculator reads (gate S3 [S4]: one billing-period source). Both then
    label snapshots with :func:`billing_period_labels`.
    """
    dc = tariff.demand_charge
    if dc is None:
        return None
    _refuse_basis(dc.basis)
    links = [str(x) for x in (import_links or [])]
    if not links:
        raise TariffError("a demand charge needs at least one import link",
                          code="demand_charge_invalid")
    return {"price_per_mw_per_period": float(dc.price_per_mw_per_period),
            "basis": dc.basis, "billing_period": tariff.billing_period,
            "import_links": links}
