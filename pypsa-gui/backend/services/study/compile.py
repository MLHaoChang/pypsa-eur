"""
The guided study's compiler: ledger rows and the guided tariff form into the
Investment Case engine's inputs (U2, rules C1-C6).

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §1
(the ledger compile table), §2 (C1, C12), §3.1 (the tariff map), WP4.
Parent: docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md
§4 (compile rules C1-C6).

`compile.py` is the ONLY code that turns guided inputs into engine inputs, and
with `engine_adapter.py` the only study module that touches IC: it imports
IC's MODELS (`models.commercial`), the frozen facade's flat export series
helper and `value_flow_templates.build`, and — outside the frozen list, an
engine ask (Q15) — `binding.bind_commercial`, the pure binder the route uses,
because a runner fork is not a loaded context.

What it compiles (WP4):

* :func:`tariff_to_engine` — the guided form (`models.study.Tariff`, owner
  decision 8) into an IC `Tariff` with FIXED item ids (§3.1): `energy` (one
  period per band and contiguous hour run, band order kept, so IC's first
  match equals GS's), `network:energy:<i>` / `network:fixed:<i>`, `demand`
  (a monthly peak; a year-billed one is IC's annual measured peak, `capacity`
  on `peak_import`), `capacity` (contracted, on the PoC size), `fixed`.
  `settlement="h"` on EVERY item (WP1). Money per MW / MWh becomes per kW /
  kWh (÷ 1000). GS's refusals are kept, typed, with the same codes.
* :func:`apply_ledger` — the ledger's demand-charge price and energy-price
  level on the form (the `energy_price_level` rule, moved from
  `tariff.tariff_from_ledger`).
* :func:`commercial_from_ledger` / :func:`commercial_from_form` — the
  `CommercialConfig` (PoC `grid_import`, export `grid_export`, `timezone`
  None, `site_party` "site", the tariff inline with `import_tariff_ref` None —
  a pack tariff is copied, owner decision D3), the export price as a minted
  flat series (C3, `export_price_ref`), an export cap as
  `ConnectionAgreement.export_cap_mw` (row 34), the item → bill-component map
  the adapter reads, the tariff's metadata and a digest.
* :func:`mint_export_series` — the study's export series under the owner's
  name ``decision-study:<base_uuid>:<study_id>:export`` (C3), idempotent on
  content; :func:`delete_export_series` its delete (WORKAROUND, see there);
  :func:`discard_export_series_of_failed_creation` the creation rollback's
  delete, once the base is gone (gate U2-WP6 W2); :func:`sweep_orphan_export_series`
  the startup sweep of a series whose base project row is gone (W1: the
  series is kept while the base project uses it).
* :func:`bind_on_network` — C6: the commercial block bound on the fork's
  IN-MEMORY network (`binding.bind_commercial`) before the runner writes it.
* :func:`with_value_flows` — row 28: `single_owner` on the option's network.
  The PoC meter Links it makes the owner's are left uncosted: IC's D11 skips
  them (`meter_link_not_investment:*`, no capex, no COD) — gate C5, WP7.
* :func:`finance_from_ledger` (WP7, C1 C2 C4) — the option's `FinanceInputs`
  (real pre-tax basis, part-lifetime replacements, the remaining-life
  terminal value) and :func:`currency_year_of`, the one-currency-year rule.
* :func:`solver_config` (WP6, C6) — the option fork's explicit `SolverConfig`:
  the compiled `CommercialConfig` on `commercial`, so IC prices the LP
  (`materialise_poc_prices`) and carries the demand charge
  (`add_demand_terms`); no GS `demand_charge`.
* :func:`library_series_resolver` (WP6, WORKAROUND) — resolves the minted
  export series in the base project's org for :func:`bind_on_network`.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd

from models.commercial import CommercialConfig, ConnectionAgreement, PriceSeriesRef
from models.commercial import Tariff as EngineTariffModel
from models.study import AssumptionsLedger, Tariff

__all__ = [
    "BILL_COMPONENT_KEYS", "CAPACITY_ASSUMED_CONNECTION", "CompileError", "CompiledCommercial", "EXPORT_LINK",
    "EXPORT_SERIES_SOURCE", "EngineTariff", "NETWORK_PREFIX", "POC_LINK", "SETTLEMENT",
    "SITE_PARTY", "apply_ledger", "bind_on_network", "commercial_from_form",
    "commercial_from_ledger", "delete_export_series",
    "discard_export_series_of_failed_creation", "export_series_name",
    "library_series_resolver", "mint_export_series", "parse_export_series_name",
    "solver_config", "sweep_orphan_export_series", "tariff_to_engine",
    "with_value_flows", "CompiledFinance", "FINANCE_RULES_FROM_DEFAULTS",
    "GUIDED_FINANCE_DEFAULTS", "currency_year_of", "finance_from_ledger",
]

POC_LINK = "grid_import"
EXPORT_LINK = "grid_export"
SITE_PARTY = "site"
SETTLEMENT = "h"
NETWORK_PREFIX = "network:"
#: A contracted capacity charge with no stated MW, rated on the PoC size.
CAPACITY_ASSUMED_CONNECTION = "capacity_charge_assumed_connection_size"
EXPORT_SERIES_SOURCE = "decision_study"
#: The guided bill's seven components (owner decision 7 adds `taxes_levies`);
#: `engine_adapter.BILL_COMPONENTS` carries their labels.
BILL_COMPONENT_KEYS = ("energy", "demand", "capacity", "fixed", "network", "export_credit",
                       "taxes_levies")
REFUSED_DEMAND_CHARGE_BASES = ("annual_peak", "ratchet")
NETWORK_CHARGE_BASES = ("per_mwh", "per_period", "per_year")
_KW_PER_MW = 1000.0
_NOTE_CODE_RE = re.compile(r"[a-z]+(_[a-z]+)*")


class CompileError(ValueError):
    """
    A guided input the engine will not be given. `code` is stable and
    machine-read; GS's tariff codes are kept (`demand_charge_basis_<b>`,
    `capacity_charge_measured_unsupported`, `tariff_unpriced_hours`, ...).
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class EngineTariff:
    """
    An IC tariff compiled from the guided form, with the bill component
    each of its items is (§3.2) and the compile notes (digit-free codes).
    """

    tariff: EngineTariffModel
    item_component: Mapping[str, str]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledCommercial:
    """
    What `compile` hands the engine (plan §2 C1): the commercial config,
    the item → component map the adapter bills by, the tariff's metadata IC's
    `Tariff` has no field for (currency, money year, source, illustrative,
    honesty codes and help, the export terms), compile notes and a digest of
    the config and the map (sha256; the compiled hash of C10).
    """

    config: CommercialConfig
    item_component: Mapping[str, str]
    tariff_meta: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    digest: str = ""

    def commercial(self) -> dict:
        """The config as `SolverConfig.commercial` stores it."""
        return self.config.model_dump(mode="json")


def _digest(config: CommercialConfig, item_component: Mapping[str, str]) -> str:
    payload = {"config": config.model_dump(mode="json"),
               "item_component": sorted(item_component.items())}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str)
                          .encode()).hexdigest()


def _finish(config: CommercialConfig, item_component: Mapping[str, str],
            tariff_meta: Mapping[str, Any], notes: Iterable[str]) -> CompiledCommercial:
    return CompiledCommercial(config=config, item_component=dict(item_component),
                              tariff_meta=dict(tariff_meta),
                              notes=tuple(dict.fromkeys(notes)),
                              digest=_digest(config, item_component))


# ── the guided form → an IC tariff (§3.1) ─────────────────────────────────

def _flat_index(snapshots) -> pd.DatetimeIndex | None:
    if snapshots is None:
        return None
    if not isinstance(snapshots, pd.DatetimeIndex):
        raise CompileError(
            "tariff_snapshots_not_flat",
            f"tariff time rules read the snapshot timestamp; got {type(snapshots).__name__}, "
            "not a flat DatetimeIndex")
    return snapshots


def _rule_mask(idx: pd.DatetimeIndex, applies) -> np.ndarray:
    mask = np.ones(len(idx), dtype=bool)
    if applies.months:
        mask &= np.isin(idx.month, applies.months)
    if applies.weekdays:
        mask &= np.isin(idx.weekday, applies.weekdays)
    if applies.hours:
        mask &= np.isin(idx.hour, applies.hours)
    return mask


def _band_prices(idx: pd.DatetimeIndex, bands) -> np.ndarray:
    """
    First match wins (the S1-gate semantics IC's `_rates` shares); NaN where
    no band matches.
    """
    price = np.full(len(idx), np.nan)
    for band in bands:
        m = _rule_mask(idx, band.applies) & np.isnan(price)
        price[m] = float(band.price_per_mwh)
    return price


def _refuse_unpriced(form: Tariff, idx: pd.DatetimeIndex | None) -> None:
    if idx is None:
        # No axis: every (month, weekday, hour) must be priced; one non-leap
        # year holds every combination.
        idx = pd.date_range("2001-01-01", "2001-12-31 23:00", freq="h")
    price = _band_prices(idx, form.energy_bands)
    unpriced = np.isnan(price)
    if unpriced.any():
        first = ", ".join(str(t) for t in idx[unpriced][:3])
        raise CompileError(
            "tariff_unpriced_hours",
            f"{int(unpriced.sum())} of {len(idx)} snapshot(s) match no energy band "
            f"(first: {first}). Bands are matched first-match-wins and must price every "
            "hour; add a catch-all band (`applies: {}`) last.")


def _runs(hours: list[int]) -> list[tuple[int, int]]:
    """Contiguous [start, end) runs of a band's hour-starting list."""
    out: list[tuple[int, int]] = []
    start = prev = None
    for h in sorted(set(hours)):
        if start is None:
            start = prev = h
        elif h == prev + 1:
            prev = h
        else:
            out.append((start, prev + 1))
            start = prev = h
    if start is not None:
        out.append((start, prev + 1))
    return out


def _refuse_unsupported(form: Tariff) -> None:
    cc = form.capacity_charge
    if cc is not None and cc.basis == "measured":
        raise CompileError(
            "capacity_charge_measured_unsupported",
            "a measured capacity charge prices the horizon's peak import under another "
            "field (the refused annual_peak basis); use basis 'contracted' or a "
            "billing-period demand charge")
    dc = form.demand_charge
    if dc is not None and dc.basis in REFUSED_DEMAND_CHARGE_BASES:
        raise CompileError(
            f"demand_charge_basis_{dc.basis}",
            f"demand-charge basis {dc.basis!r} is not modelled in the guided study (one "
            "billing-period demand charge only); it is refused rather than solved as a "
            "billing-period peak")
    for nc in form.network_charges:
        if nc.basis not in NETWORK_CHARGE_BASES:
            raise CompileError(
                "network_charge_basis_unsupported",
                f"network charge {nc.label!r} has basis {nc.basis!r}; the guided study "
                f"prices {', '.join(NETWORK_CHARGE_BASES)}")
    if form.export.price_per_mwh is not None and form.export.series_ref is not None:
        raise CompileError("tariff_export_pricing",
                           "export compensation gives both price_per_mwh and series_ref; "
                           "it is one or the other")


def _item(item_id: str, kind: str, unit: str, rate: float, *, name: str = "all",
          measured_on: str = "import") -> dict:
    return {"id": item_id, "kind": kind, "unit": unit, "settlement": SETTLEMENT,
            "measured_on": measured_on, "periods": [{"name": name, "rate": rate}]}


def tariff_to_engine(form: Tariff, *, snapshots=None, connection_mw: float | None = None,
                     jurisdiction: str = "generic", valid_from: date | None = None
                     ) -> EngineTariff:
    """
    The guided form compiled into an IC `Tariff` (§3.1; see the module
    docstring). `snapshots` (a flat DatetimeIndex) is the axis the unpriced-hour
    check reads; `connection_mw` is the intake's connection, against which a
    contracted capacity's own limit is checked (IC rates capacity on the PoC
    size, so a tariff stating another MW is refused, `capacity_basis_mismatch`).
    """
    idx = _flat_index(snapshots)
    _refuse_unsupported(form)
    _refuse_unpriced(form, idx)
    year_billed = form.billing_period == "year"
    items: list[dict] = []
    comp: dict[str, str] = {}
    notes: list[str] = []

    periods = []
    for band in form.energy_bands:
        base = {"name": band.label, "rate": float(band.price_per_mwh) / _KW_PER_MW,
                "months": list(band.applies.months), "weekdays": list(band.applies.weekdays)}
        runs = _runs(band.applies.hours) if band.applies.hours else [None]
        for r in runs:
            periods.append(dict(base) if r is None or r == (0, 24)
                           else {**base, "start_hour": r[0], "end_hour": r[1]})
    items.append({"id": "energy", "kind": "energy", "unit": "per_kwh",
                  "settlement": SETTLEMENT, "periods": periods})
    comp["energy"] = "energy"

    for i, nc in enumerate(form.network_charges):
        if nc.basis == "per_mwh":
            iid = f"{NETWORK_PREFIX}energy:{i}"
            items.append(_item(iid, "energy", "per_kwh", float(nc.price) / _KW_PER_MW,
                               name=nc.label))
        else:
            iid = f"{NETWORK_PREFIX}fixed:{i}"
            monthly = float(nc.price) / 12.0 if (nc.basis == "per_year" or year_billed) \
                else float(nc.price)
            if nc.basis == "per_year":
                notes.append("network_per_year_billed_monthly")
            items.append(_item(iid, "fixed", "per_month", monthly, name=nc.label))
        comp[iid] = "network"

    dc = form.demand_charge
    if dc is not None:
        rate = float(dc.price_per_mw_per_period) / _KW_PER_MW
        if year_billed:
            # IC's annual measured peak (`lp_bindings._capacity_spec`).
            items.append(_item("demand", "capacity", "per_kw_year", rate,
                               measured_on="peak_import"))
            notes.append("demand_year_billed_as_annual_peak")
        else:
            items.append(_item("demand", "demand", "per_kw_month", rate))
        comp["demand"] = "demand"

    cc = form.capacity_charge
    if cc is not None:
        limit = form.connection_limit_mw
        if limit is not None and connection_mw is not None \
                and not math.isclose(float(limit), float(connection_mw), rel_tol=1e-9):
            raise CompileError(
                "capacity_basis_mismatch",
                f"the tariff contracts {limit} MW but the site's connection is "
                f"{connection_mw} MW; the engine rates capacity on the connection")
        items.append(_item("capacity", "capacity", "per_kw_year",
                           float(cc.price_per_mw_per_year) / _KW_PER_MW))
        comp["capacity"] = "capacity"
        if limit is None:
            # Gate U2-S1 C2: IC rates it on the PoC size; the bill says so.
            notes.append(CAPACITY_ASSUMED_CONNECTION)

    if form.fixed_charge_per_period:
        monthly = float(form.fixed_charge_per_period) / (12.0 if year_billed else 1.0)
        items.append(_item("fixed", "fixed", "per_month", monthly))
        comp["fixed"] = "fixed"

    tariff = EngineTariffModel.model_validate({
        "id": form.tariff_id, "name": form.name, "jurisdiction": jurisdiction,
        "valid_from": valid_from or date(int(form.currency_year or 2020), 1, 1),
        "valid_to": None, "items": items})
    return EngineTariff(tariff=tariff, item_component=comp, notes=tuple(dict.fromkeys(notes)))


# ── the ledger's rows on the form ─────────────────────────────────────────

def _ledger_value(ledger: AssumptionsLedger, key: str) -> float | None:
    for row in ledger.rows:
        if row.key == key:
            return row.value
    raise CompileError("ledger_row_missing", f"the ledger has no {key!r} row")


def apply_ledger(form: Tariff, ledger: AssumptionsLedger, snapshots,
                 snapshot_weightings=None) -> Tariff:
    """
    The form with the LEDGER's numbers for the two key drivers it owns (moved
    from `tariff.tariff_from_ledger`, gate S2 [S8], [S9]): `demand_charge_price`
    replaces the tariff's own (a tariff without a demand charge keeps none),
    and every energy band becomes ``m + level x (p - m)``, `m` the bands'
    time-weighted mean over `snapshots`. Network charges, the demand charge and
    the export credit are not scaled. A new form; the input is not modified.
    """
    idx = _flat_index(snapshots)
    data = form.model_dump()
    if form.demand_charge is not None:
        price = _ledger_value(ledger, "demand_charge_price")
        if price is None:
            raise CompileError("ledger_row_missing",
                               "the tariff has a demand charge but the ledger's "
                               "demand_charge_price row has no value")
        data["demand_charge"]["price_per_mw_per_period"] = float(price)
    level = _ledger_value(ledger, "energy_price_level")
    level = 1.0 if level is None else float(level)
    if not math.isfinite(level) or level <= 0.0:
        raise CompileError("energy_price_level_invalid",
                           f"energy_price_level must be > 0, got {level!r}")
    if level != 1.0 and form.energy_bands:
        _refuse_unpriced(form, idx)
        prices = _band_prices(idx, form.energy_bands)
        if snapshot_weightings is None:
            w = np.ones(len(idx))
        else:
            sw = snapshot_weightings
            if isinstance(sw, pd.DataFrame):
                sw = sw["generators"] if "generators" in sw.columns else sw.iloc[:, 0]
            w = pd.Series(sw).astype(float).reindex(idx).fillna(0.0).to_numpy()
        mean = float((prices * w).sum() / w.sum()) if w.sum() > 0 else float(prices.mean())
        for band in data["energy_bands"]:
            band["price_per_mwh"] = mean + level * (float(band["price_per_mwh"]) - mean)
        data["honesty_notes"] = [*data.get("honesty_notes", []),
                                 "energy_bands_scaled_by_energy_price_level"]
    return Tariff.model_validate(data)


# ── the commercial config ─────────────────────────────────────────────────

def _honesty(form: Tariff) -> tuple[list[str], dict[str, str]]:
    coded = [n for n in form.honesty_notes if _NOTE_CODE_RE.fullmatch(n)]
    if len(coded) != len(form.honesty_notes):
        coded.append("tariff_has_uncoded_notes")
    return coded, dict(form.honesty_help)


def _as_ref(export_series) -> PriceSeriesRef | None:
    if export_series is None or isinstance(export_series, PriceSeriesRef):
        return export_series
    return PriceSeriesRef.model_validate(export_series)


def commercial_from_form(form: Tariff, snapshots, *, connection_mw: float | None = None,
                         export_series=None, jurisdiction: str = "generic",
                         valid_from: date | None = None, illustrative: bool | None = None,
                         pack_stamp: str | None = None) -> CompiledCommercial:
    """
    The commercial config of one guided form on `snapshots` (§1.2 constants,
    §3.1). `export_series` is the study's minted export series
    (:func:`mint_export_series`, a `PriceSeriesRef` or its dict): a tariff
    with an export price and no minted series compiles with no
    `export_price_ref`, noted `export_series_not_minted` (it cannot be bound);
    a tariff with no export price at all is refused `tariff_export_pricing`
    (gate U2-S1 C1; a stated 0.0 is a price). An export cap needs the
    connection (row 34).
    """
    idx = _flat_index(snapshots)
    _refuse_unsupported(form)
    eng = tariff_to_engine(form, snapshots=idx, connection_mw=connection_mw,
                           jurisdiction=jurisdiction, valid_from=valid_from)
    notes = list(eng.notes)
    if form.export.price_per_mwh is None and form.export.series_ref is None:
        # Gate U2-S1 C1: the site pack always builds the export Link. A form
        # that states no export compensation is refused, as GS refused writing
        # it: an absent price is not a zero (ADR-0001), and an unpriced export
        # Link would escape the engine's import-to-export cycling check, which
        # reads the export price (owner decision 10). A stated 0.0 compiles.
        raise CompileError(
            "tariff_export_pricing",
            "the site exports through grid_export but the tariff states no export "
            "compensation; give price_per_mwh (0 if export is not paid) or series_ref")
    ref = _as_ref(export_series)
    if ref is None:
        notes.append("export_series_not_minted")
    connection = None
    if form.export.cap_mw is not None:
        if connection_mw is None:
            raise CompileError("export_cap_needs_connection",
                               "an export cap needs the site's connection MW (the "
                               "connection agreement's import cap)")
        year = int(idx[0].year) if idx is not None and len(idx) else int(form.currency_year)
        connection = ConnectionAgreement(kind="firm", import_cap_mw=float(connection_mw),
                                         export_cap_mw=float(form.export.cap_mw),
                                         available_from=date(year, 1, 1))
    config = CommercialConfig(
        poc_link=POC_LINK, import_tariff_id=form.tariff_id, import_tariff=eng.tariff,
        import_tariff_ref=None, export_price_ref=ref, export_link=EXPORT_LINK,
        timezone=None, connection=connection, site_party=SITE_PARTY)
    codes, helps = _honesty(form)
    meta = {
        "tariff_id": form.tariff_id, "name": form.name, "currency": form.currency,
        "currency_year": form.currency_year, "source": form.source,
        "source_year": form.source_year, "billing_period": form.billing_period,
        "illustrative": (form.source.strip().lower() == "illustrative"
                         if illustrative is None else bool(illustrative)),
        "honesty_notes": codes, "honesty_help": helps,
        "export_price_per_mwh": form.export.price_per_mwh,
        "export_series_name": form.export.series_ref, "export_cap_mw": form.export.cap_mw,
        "jurisdiction": jurisdiction, "pack_stamp": pack_stamp,
    }
    return _finish(config, eng.item_component, meta, notes)


def _connection_of(intake: Mapping[str, Any]) -> float | None:
    site = intake.get("site") if isinstance(intake, Mapping) else None
    try:
        v = float((site or {}).get("connection_mw"))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def commercial_from_ledger(intake: Mapping[str, Any] | None, ledger: AssumptionsLedger,
                           defaults, snapshots, *, snapshot_weightings=None,
                           export_series=None) -> CompiledCommercial:
    """
    Plan §2 C1: the study's commercial config from its intake (the tariff
    choice or the user's form, the connection), its ledger (rows 16-18) and
    the defaults (`library.load_defaults()` or the legacy `load_library()`).
    A ledger priced on another tariff than the intake chose is refused
    `ledger_tariff_stale` (moved from `packs.effective_tariff`). A pack seed
    takes the pack's `jurisdiction` and `valid_from` (Q9); a user tariff the
    site zone and 1 January of its money year.
    """
    from services.study import library as study_library

    intake = intake or {}
    try:
        form, provenance = study_library._intake_tariff(intake, defaults)
    except study_library.LibraryError as exc:
        raise CompileError("tariff_invalid", str(exc)) from None
    rows = [r for r in ledger.rows if r.key == "tariff"]
    if not rows or rows[0].technical_name != form.tariff_id:
        held = rows[0].technical_name if rows else None
        raise CompileError(
            "ledger_tariff_stale",
            f"the ledger is priced on tariff {held!r} but the intake chose "
            f"{form.tariff_id!r}; re-seed the ledger before running")
    applied = apply_ledger(form, ledger, snapshots, snapshot_weightings)
    pack = getattr(defaults, "pack", None)
    jurisdiction, valid_from, illustrative, stamp = "generic", None, None, None
    if provenance == "library" and pack is not None:
        pt = pack.pack_tariff(form.tariff_id)
        jurisdiction, valid_from, stamp = pt.jurisdiction, pt.valid_from, pack.stamp
        illustrative = defaults.tariff_illustrative.get(form.tariff_id)
    elif provenance == "library":
        jurisdiction = "DE" if form.tariff_id.startswith("de_") else "generic"
    else:
        zone = str((intake.get("site") or {}).get("zone") or "").strip()
        jurisdiction = zone if len(zone) >= 2 else "generic"
    return commercial_from_form(applied, snapshots, connection_mw=_connection_of(intake),
                                export_series=export_series, jurisdiction=jurisdiction,
                                valid_from=valid_from, illustrative=illustrative,
                                pack_stamp=stamp)


# ── C3: the study's export series (owner decision 2026-10-05) ─────────────

def export_series_name(base_uuid: UUID | str, study_id: str) -> str:
    """
    ``decision-study:<base_uuid>:<study_id>:export``: the base project's uuid
    is in the name, so a copied study in the same org mints its own series.
    """
    return f"decision-study:{base_uuid}:{study_id}:export"


_EXPORT_SERIES_RE = re.compile(
    r"decision-study:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r":([0-9a-f]{32}):export")


def parse_export_series_name(name: object) -> tuple[UUID, str] | None:
    """
    ``(base_uuid, study_id)`` of a name :func:`export_series_name` mints (a
    canonical lower-case uuid, a 32-hex study id), else None. Strict: the
    startup sweep deletes by it, so anything else is not a study's series.
    """
    m = _EXPORT_SERIES_RE.fullmatch(name) if isinstance(name, str) else None
    return None if m is None else (UUID(m.group(1)), m.group(2))


def mint_export_series(db, org_id: UUID, *, base_uuid: UUID | str, study_id: str,
                       study_name: str, base_name: str, tariff: Tariff, snapshots,
                       series: Mapping[str, pd.Series] | None = None,
                       created_by: UUID | None = None, root: Path | None = None
                       ) -> PriceSeriesRef | None:
    """
    Mint (or re-use) the study's export price series in the org's Library
    (C3), through IC's flat export series helper for a constant price
    (`put_flat_export_series`, U1 e) or `series_store.put_series` for a
    market-indexed series the caller resolves (`series[tariff.export.series_ref]`).
    Idempotent on content: an unchanged price adds no version; a changed one is
    the next version. None when the tariff has no export price.

    The owner's label (``"<study name> — export price (kept while project
    <base name> exists)"``, gate U2-WP6 W1: the base pins the series, so it
    outlives the study) travels in the series' `description`: the helper
    writes its own `label` and takes none (engine ask). `source` is
    ``"decision_study"``. `put_series` versions on the payload alone, so a
    re-mint under a new label adds no version and the stored one keeps the
    label it was minted with.
    """
    if tariff.export.price_per_mwh is not None and tariff.export.series_ref is not None:
        raise CompileError("tariff_export_pricing", "export price and series_ref together")
    name = export_series_name(base_uuid, study_id)
    label = f"{study_name} — export price (kept while project {base_name} exists)"
    if tariff.export.price_per_mwh is not None:
        from services.library.export_series import put_flat_export_series

        return put_flat_export_series(db, org_id, name, float(tariff.export.price_per_mwh),
                                      snapshots=snapshots, source=EXPORT_SERIES_SOURCE,
                                      description=label, created_by=created_by, root=root)
    if tariff.export.series_ref is not None:
        from services.library import series_store

        src = (series or {}).get(tariff.export.series_ref)
        if src is None:
            raise CompileError("tariff_export_series_missing",
                               f"export series_ref {tariff.export.series_ref!r} does not "
                               "resolve")
        idx = pd.DatetimeIndex(snapshots)
        aligned = pd.Series(src).reindex(idx).astype(float)
        if aligned.isna().any():
            raise CompileError("tariff_export_series_missing",
                               f"export series {tariff.export.series_ref!r} does not cover "
                               "every snapshot")
        return series_store.put_series(db, org_id, name, aligned,
                                       {"source": EXPORT_SERIES_SOURCE, "unit": "EUR/MWh",
                                        "label": label, "description": label},
                                       created_by=created_by, root=root)
    return None


def delete_export_series(db, org_id: UUID, *, base_uuid: UUID | str, study_id: str,
                         project_dirs: Iterable[Path] | None,
                         root: Path | None = None) -> dict[str, Any]:
    """
    Delete EVERY version of the study's export series (owner decision
    2026-10-05), unless a project of the org still pins it. Since WP6 the base
    project pins it, so the study delete keeps it while the base uses it
    (amended rule, gate U2-WP6 W1) and :func:`sweep_orphan_export_series`
    deletes it once the base row is gone.

    `project_dirs` is REQUIRED (gate U2-S1 C3a): the caller enumerates every
    project directory of the org except the study's own forks (the base
    project is always among them). None or an empty list is refused
    `export_series_pin_sources_missing` — never a delete that skipped the pin
    check. A pin is read from each directory's `library_refs.json` sidecar AND
    from its `solver_config.json` (a ref copied into a config not saved since,
    C3b). A pinned series is kept: ``{"deleted_versions": 0, "kept":
    "export_series_kept_in_use"}``; an unreadable config or sidecar keeps it
    too (``"export_series_pins_unreadable"``).

    The Library access is the WORKAROUND :func:`_ic_internals_delete_series`
    (the one place GS touches IC internals for it); it COMMITS `db`, as
    `series_store.put_series` does, so the caller holds no other unsaved work
    in that session.
    """
    dirs = [Path(d) for d in (project_dirs or ())]
    if not dirs:
        raise CompileError("export_series_pin_sources_missing",
                           "pass every project directory of the org (except the study's "
                           "forks): a delete never skips the pin check")
    return _ic_internals_delete_series(db, org_id, export_series_name(base_uuid, study_id),
                                       project_dirs=dirs, root=root)


def discard_export_series_of_failed_creation(db, org_id: UUID, *, base_uuid: UUID | str,
                                              study_id: str,
                                              other_project_dirs: Iterable[Path],
                                              root: Path | None = None) -> dict[str, Any]:
    """
    Delete every version of the series a study CREATION minted before it
    failed (gate U2-WP6 W2), once its rollback has removed the base project's
    row and directory.

    Unlike :func:`delete_export_series`, the base is not among the pin
    sources: it no longer exists, and its own binding is what the rollback is
    undoing. Every OTHER project directory of the org is still checked, so the
    pin rule stays honest (none can pin the series in practice, since its name
    embeds the dead base's uuid). An org with no other project has nothing that
    could pin it, so an empty list is accepted here, and only here. None is
    refused (`export_series_pin_sources_missing`): the caller enumerates the
    org, never skips it. COMMITS `db`.
    """
    if other_project_dirs is None:
        raise CompileError("export_series_pin_sources_missing",
                           "pass every other project directory of the org (possibly "
                           "none): a delete never skips the pin check")
    return _ic_internals_delete_series(db, org_id, export_series_name(base_uuid, study_id),
                                       project_dirs=[Path(d) for d in other_project_dirs],
                                       root=root)


def _ic_internals_delete_series(db, org_id: UUID, name: str, *, project_dirs: list[Path],
                                root: Path | None) -> dict[str, Any]:
    """
    WORKAROUND — IC INTERNALS (gate U2-S1 C3e; owner question pending). Until
    IC adds `series_store.delete_series(db, org_id, name, *,
    refuse_if_pinned=True)` and `pinned_by(db, org_id, name)` to the frozen
    facade, this reads and deletes IC's Library rows directly: `db.models.
    LibraryItem` (kind "series", ORG-SCOPED and name-exact), the pin sidecars
    (`library.bundle_pins`), the projects' commercial configs, and the payload
    files (`storage_paths.library_file`). A payload file goes only when no
    remaining row of the org points at it (two studies on the same base
    minting the same price share one content-addressed file). Nothing else in
    the guided study touches these names; replace this function, not its
    callers, when the facade lands.
    """
    import json

    from sqlalchemy import select

    from db.models import LibraryItem
    from services.library import bundle_pins
    from services.storage_paths import library_file

    for d in project_dirs:
        pins, issues = bundle_pins.read_pins(d)
        if issues:  # a sidecar that cannot be read might pin it
            return {"deleted_versions": 0, "kept": "export_series_pins_unreadable"}
        if any(p.get("id") == name for p in pins):
            return {"deleted_versions": 0, "kept": "export_series_kept_in_use"}
        cfg_path = d / "solver_config.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {"deleted_versions": 0, "kept": "export_series_pins_unreadable"}
            if any(r.id == name for r in bundle_pins.collect_refs(cfg)):
                return {"deleted_versions": 0, "kept": "export_series_kept_in_use"}
    rows = db.scalars(select(LibraryItem).where(
        LibraryItem.org_id == org_id, LibraryItem.kind == "series",
        LibraryItem.name == name)).all()
    paths = {r.path for r in rows}
    for r in rows:
        db.delete(r)
    db.commit()
    if root is None:
        from settings import get_settings

        root = Path(get_settings().projects_root)
    for rel in paths:
        still = db.scalars(select(LibraryItem).where(
            LibraryItem.org_id == org_id, LibraryItem.path == rel)).first()
        if still is None:
            library_file(root, org_id, rel).unlink(missing_ok=True)
    return {"deleted_versions": len(rows), "kept": None}


def sweep_orphan_export_series(db, *, root: Path | None = None) -> list[str]:
    """
    Delete every version of each study export series whose BASE PROJECT ROW
    no longer exists (gate U2-WP6 W1, owner 2026-10-06: deleting the base is
    what makes the series go; project delete is not GS-owned). Returns the
    names deleted.

    Run by the startup sweep (`forks.sweep_leftover_forks`). Strict: only a
    name :func:`parse_export_series_name` reads whose latest version's source
    is ``"decision_study"``; a malformed name is skipped. Never a series whose
    base uuid names ANY project row (also of another org: the conservative
    reading of "never while the base exists"). The pin check stays honest:
    every remaining project directory of the series' org is passed to
    :func:`_ic_internals_delete_series`, so a project that copied the ref
    keeps it. Listing the org's series (`series_store.list_series`) is outside
    the frozen facade, like :func:`library_series_resolver` (Q15). A failure
    on one series is logged and the sweep goes on. COMMITS `db`.
    """
    import logging

    from sqlalchemy import select

    from db.models import Organization, Project
    from services import project_registry
    from services.library import series_store

    log = logging.getLogger(__name__)
    swept: list[str] = []
    for org_id in db.scalars(select(Organization.id)).all():
        dirs: list[Path] | None = None
        for ref in series_store.list_series(db, org_id):
            parsed = parse_export_series_name(ref.id)
            if parsed is None or ref.source != EXPORT_SERIES_SOURCE:
                continue
            if db.get(Project, parsed[0]) is not None:
                continue
            try:
                if dirs is None:
                    dirs = [project_registry.project_dir(p) for p in db.scalars(
                        select(Project).where(Project.org_id == org_id)).all()]
                out = _ic_internals_delete_series(db, org_id, ref.id, project_dirs=dirs,
                                                  root=root)
            except Exception:  # noqa: BLE001 — one series never stops the sweep
                db.rollback()
                log.exception("study: could not sweep export series %s", ref.id)
                continue
            if out["kept"] is None:
                swept.append(ref.id)
                log.info("study: swept export series %s (%d versions; its base is gone)",
                         ref.id, out["deleted_versions"])
            else:
                log.info("study: export series %s of a gone base kept (%s)", ref.id,
                         out["kept"])
    return swept


# ── C6: bind on the fork's network; row 28 ────────────────────────────────

def bind_on_network(n, compiled: CompiledCommercial, *,
                    resolve_ref: Callable[[object], pd.Series],
                    project_dir: Path | None = None) -> CompiledCommercial:
    """
    C6: apply the compiled commercial block to a study-owned fork's IN-MEMORY
    network the way `PUT /solver_config` does (`binding.bind_commercial`: the
    export series written into `links_t["ic_export_price"]`, the FCA registry),
    BEFORE the runner writes the fork (the binding survives the netCDF round
    trip, WP1). `resolve_ref` resolves the export series in the fork's org.
    A refusal of the binder is a `CompileError` with its code.
    """
    if "export_series_not_minted" in compiled.notes:
        raise CompileError("export_series_not_minted",
                           "the tariff prices export but no export series was minted for "
                           "the study; mint it before binding")
    _refuse_inactive_meter_links(n, compiled.config)
    from services.commercial import binding

    try:
        bound = binding.bind_commercial(n, compiled.config, project_dir=project_dir,
                                        resolve_ref=resolve_ref)
    except binding.BindingRefusal as exc:
        raise CompileError(exc.code, str(exc)) from exc
    config = CommercialConfig.model_validate(bound) if bound is not None else compiled.config
    return _finish(config, compiled.item_component, compiled.tariff_meta, compiled.notes)


def _refuse_inactive_meter_links(n, config: CommercialConfig) -> None:
    """
    WORKAROUND (engine ask, U2 WP6): IC's `validate_for_network` accepts an
    INACTIVE PoC or export Link, and the solve then fails with a raw KeyError
    from the linopy model (no typed refusal). GS's demand wrapper refused it
    (`demand_charge_inactive_import_link`); the study keeps a typed refusal,
    with the pack's code, until the engine refuses it itself.
    """
    for link in (config.poc_link, config.export_link):
        if link and link in n.links.index and "active" in n.links.columns \
                and not bool(n.links.at[link, "active"]):
            raise CompileError("import_link_inactive",
                               f"{link} is inactive; the engine prices and meters the "
                               "site through it")


def with_value_flows(compiled: CompiledCommercial, n) -> CompiledCommercial:
    """
    Row 28: the `single_owner` value flows built on the option's network
    (`value_flow_templates.build`); the owner is the site party.
    """
    from services.commercial import value_flow_templates as VFT

    vf = VFT.build("single_owner", n, compiled.config).config.model_dump(mode="json")
    config = compiled.config.model_copy(update={"value_flows": vf})
    return _finish(config, compiled.item_component, compiled.tariff_meta, compiled.notes)


# ── WP7: the finance inputs (rows 1, 4, 6, 20-32; C1, C2, C4) ─────────────

#: The guided rules of plan §1.2 rows 21-32 for a ledger seeded before they
#: were rows (the legacy `study_library` ledger, until WP8 seeds production
#: from the pack): the SAME constants `library._engine_rows` seeds, applied
#: here and disclosed `finance_rules_from_guided_defaults`.
GUIDED_FINANCE_DEFAULTS: Mapping[str, float] = {
    "contingency_share": 0.0,
    **{f"escalation_{c}": 0.0 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
    "pv_degradation_pct_per_year": 0.0,
}
FINANCE_RULES_FROM_DEFAULTS = "finance_rules_from_guided_defaults"


@dataclass(frozen=True)
class CompiledFinance:
    """
    What `compile` hands the finance engine (plan §2 C6, WP7): the
    `FinanceInputs` of one option, the compile notes (digit-free codes) and
    a digest (sha256 of the inputs; the compiled hash of C10).
    """

    inputs: Any                     # models.finance.FinanceInputs
    notes: tuple[str, ...] = ()
    digest: str = ""

    def finance(self) -> dict:
        """The inputs as `SolverConfig.finance` stores them."""
        return self.inputs.model_dump(mode="json")


def _row_or_default(ledger: AssumptionsLedger, key: str, notes: list[str]) -> float | None:
    """
    A row's value (None when the row is null: the engine then says what is
    missing); a ledger without the row takes the guided rule, disclosed.
    """
    for row in ledger.rows:
        if row.key == key:
            return None if row.value is None else float(row.value)
    notes.append(FINANCE_RULES_FROM_DEFAULTS)
    return GUIDED_FINANCE_DEFAULTS[key]


def _whole_years(ledger: AssumptionsLedger, key: str) -> int:
    """Gate S2 nit, kept (the pro forma's rule): a lifetime is whole years."""
    v = _ledger_value(ledger, key)
    if v is None or not math.isfinite(float(v)) or v <= 0 or abs(v - round(v)) > 1e-9:
        raise CompileError(
            "lifetime_not_whole_years",
            f"{key} is {v!r}; the case books whole years, so a lifetime must be a "
            "whole number of years")
    return int(round(v))


def currency_year_of(ledger: AssumptionsLedger, tariff_meta: Mapping[str, Any],
                     study_year: int | None = None) -> int:
    """
    The study's ONE currency year (gate S2 BC-S2-4, moved from the pro
    forma): a money row, the tariff or the study in another year is refused
    `currency_year_mixed` (nothing is converted); a tariff in another
    currency `currency_mixed`; none stated `currency_year_unstated`.
    """
    years: dict[int, list[str]] = {}
    for row in ledger.rows:
        if row.currency_year is not None and row.unit.startswith("EUR"):
            years.setdefault(int(row.currency_year), []).append(row.key)
    currency = tariff_meta.get("currency") or "EUR"
    if currency != "EUR":
        raise CompileError("currency_mixed",
                           f"the tariff is in {currency}; the ledger is in EUR")
    if tariff_meta.get("currency_year") is not None:
        years.setdefault(int(tariff_meta["currency_year"]), []).append(
            f"tariff {tariff_meta.get('tariff_id')}")
    if study_year is not None:
        years.setdefault(int(study_year), []).append("study")
    if len(years) > 1:
        detail = "; ".join(f"{y}: {', '.join(sorted(k)[:4])}" for y, k in sorted(years.items()))
        raise CompileError(
            "currency_year_mixed",
            f"the case would mix currency years ({detail}); nothing is converted — "
            "re-enter the values in one currency year")
    if not years:
        raise CompileError("currency_year_unstated",
                           "no money row, tariff or study states a currency year")
    return next(iter(years))


def finance_from_ledger(ledger: AssumptionsLedger, *, model_year: int,
                        owned_assets: Iterable[str], tariff_meta: Mapping[str, Any],
                        study_currency_year: int | None = None) -> CompiledFinance:
    """
    The finance inputs of one option (plan §1.1 rows 1, 4, 6, 20; §1.2 rows
    21-32; C1, C2, C4):

    * C4 — real, pre-tax, no subsidy: `price_basis="real"`, every escalation
      class from its row (0.0; a null row leaves the class out, so the engine
      says `escalation_missing:<class>`), `inflation=None`, `wacc_nominal` =
      `cost_of_equity` = the ledger's real `discount_rate` (all equity, row
      26), `tax_pack_id=None`, no incentives, no debt.
    * Timing — financial close 1 January of `model_year - 1` (row 21, one
      construction year, `capex_phasing=[1.0]`), every owner asset's COD 1
      January of `model_year` (row 22), `analysis_years` = the storage
      lifetime (row 27, whole years), `contingency_share` (row 23).
    * C1 — replacements follow the parts (`replacement_rule="part_lifetimes"`,
      IC D9): the inverter part is re-bought in its last service year, no
      hand-booked `replacement_capex`.
    * C2 — the terminal value is the engine's `remaining_life_annuity` (IC
      D10): each part's purchase still alive at the horizon valued on the
      annuity the LP charged for it, GS's salvage rule, computed by the engine.
    * Degradation — PV at row 30 (÷ 100); the battery at 0.0 (rows 14-15 are
      not used: the `no_degradation` basis).

    `owned_assets` are the value flows' owner assets (the COD covers them all;
    the meter Links IC's D11 skips need none, and an extra entry is unread).
    Refused, typed: a second currency year, a lifetime that is not whole
    years, a missing discount rate.
    """
    from models.finance import ESCALATION_CLASSES, FinanceInputs, TerminalValueRule

    notes: list[str] = []
    currency_year = currency_year_of(ledger, tariff_meta, study_currency_year)
    rate = _ledger_value(ledger, "discount_rate")
    if rate is None or not math.isfinite(float(rate)):
        raise CompileError("ledger_row_missing", "the case needs a discount rate (row 20)")
    horizon = _whole_years(ledger, "battery_storage_lifetime_years")
    _whole_years(ledger, "battery_inverter_lifetime_years")
    rows = {r.key: r for r in ledger.rows}
    close = rows.get("financial_close_year")
    close_year = int(close.value) if close is not None and close.value is not None \
        else int(model_year) - 1
    cod = rows.get("cod_year")
    cod_year = int(cod.value) if cod is not None and cod.value is not None else int(model_year)
    escalation = {}
    for c in ESCALATION_CLASSES:
        v = _row_or_default(ledger, f"escalation_{c}", notes)
        if v is not None:
            escalation[c] = v
    owned = list(dict.fromkeys(owned_assets))
    degradation: dict[str, float] = {}
    if "pv" in owned:
        pv = _row_or_default(ledger, "pv_degradation_pct_per_year", notes)
        if pv is not None:
            degradation["pv"] = pv / 100.0
    if "battery" in owned:
        degradation["battery"] = 0.0
    inputs = FinanceInputs(
        currency=str(tariff_meta.get("currency") or "EUR"), currency_year=currency_year,
        price_basis="real", financial_close=date(close_year, 1, 1), capex_phasing=[1.0],
        cod_by_asset={a: date(cod_year, 1, 1) for a in owned},
        contingency_share=_row_or_default(ledger, "contingency_share", notes),
        escalation=escalation, degradation_by_asset=degradation, inflation=None,
        analysis_years=horizon, annualise=False,
        replacement_rule="part_lifetimes", replacement_capex=[],
        terminal_value=TerminalValueRule(method="remaining_life_annuity"),
        wacc_nominal=float(rate), cost_of_equity=float(rate),
        tax_pack_id=None, incentives=[], debt=[])
    digest = hashlib.sha256(json.dumps(inputs.model_dump(mode="json"), sort_keys=True,
                                       default=str).encode()).hexdigest()
    return CompiledFinance(inputs=inputs, notes=tuple(dict.fromkeys(notes)), digest=digest)


# ── C6 / WP6: the option fork's solver config ─────────────────────────────

def solver_config(ledger: AssumptionsLedger | None, compiled: CompiledCommercial, *,
                  discount_rate: float | None = None, default_lifetime: float | None = None,
                  finance: Mapping[str, Any] | None = None, **overrides):
    """
    The explicit `SolverConfig` of an option fork (plan §2 C3, WP6): lopf on
    HiGHS, one flat horizon, the full strategy, no SCLOPF, no AC power flow,
    no user code; `discount_rate` and the fallback lifetime from the ledger
    (rows 20 and 6) unless given; `commercial` = the compiled config (C6), so
    the engine prices the PoC at solve time and carries the demand charge in
    the LP; GS's `demand_charge` is never set. `finance` is WP7's
    (`compile.finance_from_ledger`). Nothing is inherited from the base
    project. `overrides` set any other `SolverConfig` field (a test's
    objective scale, an Expert's strategy).
    """
    from services.solver_service import SolverConfig

    def row(key: str) -> float | None:
        if ledger is None:
            return None
        v = _ledger_value(ledger, key)
        return None if v is None else float(v)

    rate = discount_rate if discount_rate is not None else row("discount_rate")
    if rate is None:
        raise CompileError("ledger_row_missing",
                           "the solver config needs a discount rate (ledger row "
                           "discount_rate)")
    life = default_lifetime if default_lifetime is not None else \
        row("battery_storage_lifetime_years")
    fields: dict[str, Any] = dict(
        solver_name="highs", mode="lopf", multi_investment_periods=False,
        solve_strategy="full", sclopf=False, run_ac_pf_after_lopf=False,
        extra_functionality_code="", discount_rate=float(rate),
        commercial=compiled.commercial(), demand_charge=None,
        finance=None if finance is None else dict(finance))
    if life is not None:
        fields["default_lifetime"] = float(life)
    fields.update(overrides)
    return SolverConfig(**fields)


def library_series_resolver(db, org_id: UUID, *, root: Path | None = None
                            ) -> Callable[[object], pd.Series]:
    """
    WORKAROUND (engine ask Q15: `series_store.resolve` is outside the frozen
    facade): the resolver :func:`bind_on_network` takes, reading a minted
    series back from the org's Library — the study's fork lives in its base
    project's org (`project_registry.create_scenario`), so this is the org
    `binding.context_resolvers` would use for a loaded fork. A ref the
    Library cannot give is `CompileError("library_ref_stale")`.
    """
    from services.library import series_store

    def resolve(ref) -> pd.Series:
        ref = _as_ref(ref)
        try:
            return series_store.resolve(db, org_id, ref, root=root)
        except (series_store.LibraryRefNotFound, series_store.LibraryRefStale) as exc:
            raise CompileError("library_ref_stale", str(exc)) from exc

    return resolve
