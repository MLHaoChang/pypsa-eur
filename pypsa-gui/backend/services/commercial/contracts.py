"""
Contract settlement on a solved dispatch (Edge Investment Case P2 WP2.2a/b).

`settle(contract, inputs)` returns the contract's settlement lines for ONE
investment period (a flat network is the single period `None`), amounts for the
represented hours of that period-year (Σ objective weights), unweighted by the
period's years (P4 applies them). A line is
`(period, contract_id, payer, payee, value_stream, quantity_mwh, amount, flags)`
with `amount` ≥ 0 meaning the payer pays the payee; a signed settlement
(a CfD difference, a financial baseload PPA) keeps its sign, so a negative
amount means the payee pays the payer. An amount that cannot be computed is
`None` with a flag (ADR-0001), never 0.

Indexation (P2): a price is indexed to the MODELLED year y — the investment
period, else the snapshots' majority year — as
`price × (1 + indexation_pct_per_year / 100) ** (y − base_year)`; `base_year`
absent ⇒ y (no indexation). P4 escalates from the modelled year onward, never
both.

| Kind | Interval formula (gen = the contracted Generators' output, MW; w = represented hours) |
|---|---|
| PPA pay_as_produced, fixed | buyer → seller price_y × gen × w; a volume cap (pro-rated to the represented share of the year) is filled chronologically and the excess is not settled (`ppa_excess_mwh`) |
| PPA market_plus_premium | clamp(ref + premium_y, floor, cap) × gen × w |
| PPA baseload | financial: (price_y − ref) × baseload_mw × w |
| PPA as_consumed_btm | consumed = gen − the gen's pro-rata share of PoC export (capped at gen); BESS charging from the PV counts as consumed |
| PPA sleeved | as pay_as_produced, plus buyer → sleeving_party sleeving_fee × gen × w |
| CfD | counterparty → generator_owner (strike_y − ref) × gen × w; `monthly_capture`: the month's generation-weighted ref; `suspend_on_negative_price`: intervals with ref < 0 settle 0 |
| DR availability (2.2b) | counterparty → site availability × contracted_mw × represented hours / the calendar year's hours (8784 in a leap year) |
| DR activation (2.2b) | counterparty → site activation × the bus's DSR dispatch attributed pro rata to the named loads' share of the bus load; events (maximal runs with activation > 0) checked against max_events / max_duration_h and flagged |
| Lease (2.2b) | lessee → lessor annual_payment × represented hours / the year's hours |
| EaaS (2.2b) | customer → provider fee/MWh × delivered (Generator p > 0, StorageUnit discharge, Link output) + fee/year × the year fraction |
| Retail (2.2b) | no lines: the import tariff's bill, payer customer, payee retailer (`retail_parties`) |

Pure service: imports neither routers nor `solver_service`; the caller
(`services/results/billing.py`) builds the inputs from the physical seam and
the settlement readers.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from services.commercial.tariff_engine import _HOURS_PER_YEAR


class ContractError(ValueError):
    """A contract that cannot settle on this network (refused, not flagged)."""


@dataclass
class SettlementInputs:
    """One period's settlement inputs. Frames are MW per snapshot on `index`
    (a flat DatetimeIndex on the site clock)."""

    period: int | None
    index: pd.DatetimeIndex
    weights: np.ndarray                      # represented hours per row
    generators: pd.DataFrame                 # every Generator's output, MW
    export_mw: pd.Series | None = None       # PoC export on the commercial meter
    references: dict = field(default_factory=dict)   # contract id → (Series | None, flags)
    modelled_year: int | None = None
    # WP2.2b: DR, lease, EaaS.
    loads: pd.DataFrame | None = None        # every load's served MW
    load_bus: dict = field(default_factory=dict)      # load → bus
    dsr: tuple = (None, ["dr_activation_not_established"])   # (bus frame | None, flags)
    storage_discharge: pd.DataFrame | None = None
    link_output: pd.DataFrame | None = None  # MW delivered at bus1
    step_hours: np.ndarray | None = None     # interval length (events); default = weights
    site_party: str = "site"


@dataclass
class Line:
    period: int | None
    contract_id: str
    payer: str | None
    payee: str | None
    value_stream: str
    quantity_mwh: float | None
    amount: float | None
    flags: list[str] = field(default_factory=list)


def modelled_year(inputs: SettlementInputs) -> int:
    if inputs.modelled_year is not None:
        return int(inputs.modelled_year)
    if inputs.period is not None:
        return int(inputs.period)
    years = pd.Series(inputs.weights, index=inputs.index).groupby(inputs.index.year).sum()
    return int(years.idxmax())


def indexed(price: float, pct: float, base_year: int | None, year: int) -> float:
    base = year if base_year is None else int(base_year)
    return float(price) * (1.0 + float(pct or 0.0) / 100.0) ** (year - base)


def _hours_in_year(year: int) -> float:
    return 8784.0 if calendar.isleap(year) else _HOURS_PER_YEAR


def _gen(contract, inputs: SettlementInputs) -> np.ndarray:
    missing = [a for a in contract.asset_ids if a not in inputs.generators.columns]
    if missing:
        raise ContractError(f"contract {contract.id!r}: asset(s) {missing} are not Generators "
                            "of this network")
    return inputs.generators[list(contract.asset_ids)].sum(axis=1).to_numpy(dtype=float)


def _parties(*names) -> list[str]:
    return ["party_not_established"] if any(n is None for n in names) else []


def _ref(contract, inputs: SettlementInputs) -> tuple[np.ndarray | None, list[str]]:
    series, flags = inputs.references.get(contract.id, (None, ["reference_price_missing"]))
    if series is None:
        return None, list(flags) or ["reference_price_missing"]
    return pd.Series(series).reindex(inputs.index).to_numpy(dtype=float), []


def _ppa(contract, inputs: SettlementInputs) -> list[Line]:
    y = modelled_year(inputs)
    w = np.asarray(inputs.weights, dtype=float)
    gen = _gen(contract, inputs)
    pricing = getattr(contract, "pricing", "fixed")
    kind = contract.kind
    base = getattr(contract, "base_year", None)
    idx = contract.indexation_pct_per_year
    buyer, seller = contract.buyer, contract.seller
    lines: list[Line] = []

    def line(stream, qty, amount, flags=(), payer=buyer, payee=seller):
        return Line(inputs.period, contract.id, payer, payee, stream,
                    None if qty is None else float(qty),
                    None if amount is None else float(amount),
                    sorted(set(list(flags) + _parties(payer, payee))))

    ref = None
    ref_flags: list[str] = []
    if kind == "baseload" or pricing == "market_plus_premium":
        ref, ref_flags = _ref(contract, inputs)
        if ref is None:
            return [line("ppa_energy", None, None, ref_flags)]

    if kind == "baseload":
        mw = getattr(contract, "baseload_mw", None)
        if mw is None:
            return [line("ppa_energy", None, None, ["baseload_mw_not_established"])]
        price_y = indexed(contract.price, idx, base, y)
        qty = mw * w
        return [line("ppa_energy", qty.sum(), float(((price_y - ref) * qty).sum()))]

    if kind == "as_consumed_btm":
        if inputs.export_mw is None:
            return [line("ppa_energy", None, None, ["export_not_established"])]
        total = inputs.generators.sum(axis=1).to_numpy(dtype=float)
        exp = pd.Series(inputs.export_mw).reindex(inputs.index).to_numpy(dtype=float)
        share = np.divide(gen, total, out=np.zeros_like(gen), where=total > 0)
        attributed = np.minimum(gen, np.clip(exp, 0.0, None) * share)
        mw = gen - attributed
    else:  # pay_as_produced, sleeved
        mw = gen
    mwh = mw * w

    if pricing == "market_plus_premium":
        premium_y = indexed(contract.premium_eur_per_mwh, idx, base, y)
        eff = ref + premium_y
        if contract.floor is not None:
            eff = np.maximum(eff, contract.floor)
        if contract.cap is not None:
            eff = np.minimum(eff, contract.cap)
    else:
        eff = np.full(len(mwh), indexed(contract.price, idx, base, y))

    excess = 0.0
    cap = contract.volume_cap_mwh_per_year
    if cap is not None:
        # The annual cap pro-rated to the represented share of the year,
        # filled chronologically; the rest is not settled under the PPA.
        cap_eff = cap * float(w.sum()) / _hours_in_year(y)
        cum = np.cumsum(mwh)
        settled = np.clip(cap_eff - (cum - mwh), 0.0, mwh)
        excess = float(mwh.sum() - settled.sum())
        mwh = settled
    lines.append(line("ppa_energy", mwh.sum(), float((eff * mwh).sum())))
    if excess > 1e-9:
        lines.append(line("ppa_excess_mwh", excess, 0.0, ["ppa_volume_cap_exceeded"]))
    if kind == "sleeved":
        fee = getattr(contract, "sleeving_fee_eur_per_mwh", None)
        party = getattr(contract, "sleeving_party", None)
        if fee is None:
            lines.append(line("ppa_sleeving_fee", mwh.sum(), None,
                              ["sleeving_fee_not_established"], payee=party))
        else:
            lines.append(line("ppa_sleeving_fee", mwh.sum(), float(fee * mwh.sum()),
                              payee=party))
    return lines


def _cfd(contract, inputs: SettlementInputs) -> list[Line]:
    y = modelled_year(inputs)
    w = np.asarray(inputs.weights, dtype=float)
    gen = _gen(contract, inputs)
    payer, payee = getattr(contract, "counterparty", None), getattr(contract, "generator_owner", None)
    flags = _parties(payer, payee)
    ref, ref_flags = _ref(contract, inputs)
    if ref is None:
        return [Line(inputs.period, contract.id, payer, payee, "cfd_difference", None, None,
                     sorted(set(flags + ref_flags)))]
    strike_y = indexed(contract.strike, getattr(contract, "indexation_pct_per_year", 0.0),
                       getattr(contract, "base_year", None), y)
    mwh = gen * w
    if getattr(contract, "suspend_on_negative_price", False):
        mwh = np.where(ref < 0, 0.0, mwh)          # §51 EEG style: no support at negative prices
    if getattr(contract, "reference", "interval") == "monthly_capture":
        months = np.asarray(inputs.index.strftime("%Y-%m"))
        amount = 0.0
        for m in np.unique(months):
            sel = months == m
            vol = float(mwh[sel].sum())
            if vol > 0:
                capture = float((ref[sel] * mwh[sel]).sum()) / vol
                amount += (strike_y - capture) * vol
    else:
        amount = float(((strike_y - ref) * mwh).sum())
    return [Line(inputs.period, contract.id, payer, payee, "cfd_difference", float(mwh.sum()),
                 float(amount), flags)]


def _year_fraction(inputs: SettlementInputs) -> float:
    """Represented hours / the modelled calendar year's hours (8784 in a leap year)."""
    return float(np.asarray(inputs.weights, dtype=float).sum()) / _hours_in_year(
        modelled_year(inputs))


def _dr(contract, inputs: SettlementInputs) -> list[Line]:
    if contract.asset_ids:
        # Activating a BESS or a generator is a P5 archetype matter.
        raise ContractError(f"DR contract {contract.id!r}: asset_ids are refused in P2; name "
                            "load_ids")
    payer, payee = getattr(contract, "counterparty", None), inputs.site_party
    parties = _parties(payer, payee)
    out: list[Line] = []
    mw = getattr(contract, "contracted_mw", None)
    if mw is None:
        out.append(Line(inputs.period, contract.id, payer, payee, "dr_availability", None, None,
                        sorted(set(parties + ["contracted_mw_not_established"]))))
    else:
        out.append(Line(inputs.period, contract.id, payer, payee, "dr_availability", None,
                        float(contract.availability_eur_per_mw_year * mw
                              * _year_fraction(inputs)), parties))
    frame, flags = inputs.dsr
    missing = [l for l in contract.load_ids if l not in inputs.load_bus]
    if missing:
        raise ContractError(f"DR contract {contract.id!r}: load(s) {missing} are not loads of "
                            "this network")
    if frame is None or inputs.loads is None:
        out.append(Line(inputs.period, contract.id, payer, payee, "dr_activation", None, None,
                        sorted(set(parties + (list(flags) or
                                              ["dr_activation_not_established"])))))
        return out
    # Activated MW on each named load: the bus's DSR dispatch × the load's
    # share of its bus's load in that interval.
    act = np.zeros(len(inputs.index))
    bus_of = pd.Series(inputs.load_bus)
    for load in contract.load_ids:
        bus = inputs.load_bus[load]
        if bus not in frame.columns:
            continue
        on_bus = [l for l in bus_of.index[bus_of == bus] if l in inputs.loads.columns]
        bus_load = inputs.loads[on_bus].sum(axis=1).to_numpy(dtype=float)
        mine = inputs.loads[load].to_numpy(dtype=float)
        share = np.divide(mine, bus_load, out=np.zeros_like(mine), where=bus_load > 0)
        act += frame[bus].reindex(inputs.index).fillna(0.0).to_numpy(dtype=float) * share
    w = np.asarray(inputs.weights, dtype=float)
    mwh = float((act * w).sum())
    ev_flags = list(parties)
    events, longest = _events(act, inputs.step_hours if inputs.step_hours is not None else w)
    if contract.max_events is not None and events > contract.max_events:
        ev_flags.append("dr_max_events_exceeded")
    if contract.max_duration_h is not None and longest > contract.max_duration_h + 1e-9:
        ev_flags.append("dr_max_duration_exceeded")
    out.append(Line(inputs.period, contract.id, payer, payee, "dr_activation", mwh,
                    float(contract.activation_eur_per_mwh * mwh), sorted(set(ev_flags))))
    return out


def _events(active_mw: np.ndarray, step_h) -> tuple[int, float]:
    """(number of events, the longest in hours): an event is a maximal run of
    consecutive intervals with activation > 0."""
    on = np.asarray(active_mw) > 1e-9
    step = np.broadcast_to(np.asarray(step_h, dtype=float), on.shape)
    events, longest, run = 0, 0.0, 0.0
    for i, flag in enumerate(on):
        if flag:
            if run == 0.0:
                events += 1
            run += float(step[i])
            longest = max(longest, run)
        else:
            run = 0.0
    return events, longest


def _lease(contract, inputs: SettlementInputs) -> list[Line]:
    return [Line(inputs.period, contract.id, contract.lessee, contract.lessor, "lease_payment",
                 None, float(contract.annual_payment * _year_fraction(inputs)), [])]


def _eaas(contract, inputs: SettlementInputs) -> list[Line]:
    """Delivered MWh of the named assets: Generator p > 0, StorageUnit
    discharge, Link output at bus1."""
    w = np.asarray(inputs.weights, dtype=float)
    delivered = np.zeros(len(inputs.index))
    frames = [inputs.generators.clip(lower=0.0), inputs.storage_discharge, inputs.link_output]
    for a in contract.asset_ids:
        src = next((f for f in frames if f is not None and a in f.columns), None)
        if src is None:
            raise ContractError(f"EaaS contract {contract.id!r}: asset {a!r} is not a Generator, "
                                "StorageUnit or Link of this network")
        delivered += src[a].reindex(inputs.index).fillna(0.0).to_numpy(dtype=float)
    mwh = float((delivered * w).sum())
    amount = 0.0
    if contract.fee_eur_per_mwh is not None:
        amount += contract.fee_eur_per_mwh * mwh
    if contract.fee_eur_per_year is not None:
        amount += contract.fee_eur_per_year * _year_fraction(inputs)
    return [Line(inputs.period, contract.id, contract.customer, contract.provider, "eaas_fee",
                 mwh, float(amount), [])]


def retail_parties(contract, tariff) -> tuple[str, str]:
    """(payer, payee) of the import tariff's lines under a retail contract:
    the retail bill IS the tariff's bill (WP2.1b), no extra lines. The
    contract names the payload's `Tariff.id`, else it is refused."""
    if tariff is None or contract.tariff_id != tariff.id:
        raise ContractError(f"retail contract {contract.id!r} names tariff "
                            f"{contract.tariff_id!r}, but the import tariff is "
                            f"{getattr(tariff, 'id', None)!r}")
    return contract.customer, contract.retailer


def settle(contract, inputs: SettlementInputs) -> list[Line]:
    """The contract's settlement lines for one period (see module docstring).
    A retail contract has none of its own (`retail_parties`)."""
    kind = getattr(contract, "type", None)
    if kind == "ppa":
        return _ppa(contract, inputs)
    if kind == "cfd":
        return _cfd(contract, inputs)
    if kind == "dr":
        return _dr(contract, inputs)
    if kind == "lease":
        return _lease(contract, inputs)
    if kind == "eaas":
        return _eaas(contract, inputs)
    if kind == "retail":
        return []
    raise ContractError(f"contract {contract.id!r}: unknown type {kind!r}")
