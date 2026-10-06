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
    # MW delivered at bus1 ONLY (−p1; the seam's `links_p1_output`, not the
    # all-ports `links_output`).
    link_output: pd.DataFrame | None = None
    step_hours: np.ndarray | None = None     # interval length; default: the index's steps
    site_party: str = "site"
    # The Generators behind the PoC meter (an `as_consumed_btm` PPA's share of
    # export is taken among these; a grid-side or other member's generator is
    # not on-site). None ⇒ that PPA is not established.
    site_generators: list[str] | None = None
    # MW each converting Link behind the meter (a CHP, a fuel cell) delivers
    # to site-side electric buses, from every port
    # (`lp_bindings.site_link_generation`): site generation too (IC P3 gate,
    # condition 2). None ⇒ no such Link.
    site_link_generation: pd.DataFrame | None = None


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
    # Any NaN output makes the interval NaN (skipna=False): the amount is then
    # refused below, never a partial sum (review round 2).
    return inputs.generators[list(contract.asset_ids)].sum(axis=1, skipna=False).to_numpy(
        dtype=float)


def _parties(*names) -> list[str]:
    return ["party_not_established"] if any(n is None for n in names) else []


def _on_index(series, inputs: SettlementInputs) -> np.ndarray | None:
    """`series` on the period's flat index: a (period, timestep) series (what
    `settlement_inputs.reference_price` returns on a multi-period network) is
    cut to this period first. None when any row is missing or NaN (ADR-0001)."""
    s = pd.Series(series)
    if isinstance(s.index, pd.MultiIndex):
        try:
            s = (s.xs(inputs.period, level=0) if inputs.period is not None
                 else s.droplevel(0))
        except KeyError:
            return None  # the series does not cover this period (review round 2)
    out = s.reindex(inputs.index).to_numpy(dtype=float)
    return None if np.isnan(out).any() else out


def _ref(contract, inputs: SettlementInputs) -> tuple[np.ndarray | None, list[str]]:
    series, flags = inputs.references.get(contract.id, (None, ["reference_price_missing"]))
    if series is None:
        return None, list(flags) or ["reference_price_missing"]
    out = _on_index(series, inputs)
    return (None, ["reference_price_missing"]) if out is None else (out, [])


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

    if kind != "baseload" and np.isnan(gen).any():
        return [line("ppa_energy", None, None, ["generation_not_established"])]
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
        exp = None if inputs.export_mw is None else _on_index(inputs.export_mw, inputs)
        if exp is None:
            return [line("ppa_energy", None, None, ["export_not_established"])]
        site = inputs.site_generators
        if not site or any(g not in inputs.generators.columns for g in site):
            return [line("ppa_energy", None, None, ["site_generators_not_established"])]
        # Export is attributed among the ELECTRIC generation BEHIND the meter
        # only: its Generators on electric buses and its converting Links.
        total = inputs.generators[list(site)].sum(axis=1, skipna=False).to_numpy(dtype=float)
        links = inputs.site_link_generation
        if links is not None and not links.empty:
            total = total + links.clip(lower=0.0).sum(axis=1, skipna=False).to_numpy(dtype=float)
        if np.isnan(total).any():
            return [line("ppa_energy", None, None, ["generation_not_established"])]
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
        # The sleeving party delivers ALL the generation, capped or not: the
        # fee is on gen (the plan's table), not on the PPA-settled volume.
        fee = getattr(contract, "sleeving_fee_eur_per_mwh", None)
        party = getattr(contract, "sleeving_party", None)
        sleeved = float((mw * w).sum())
        if fee is None:
            lines.append(line("ppa_sleeving_fee", sleeved, None,
                              ["sleeving_fee_not_established"], payee=party))
        else:
            lines.append(line("ppa_sleeving_fee", sleeved, float(fee * sleeved), payee=party))
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
    if np.isnan(gen).any():
        return [Line(inputs.period, contract.id, payer, payee, "cfd_difference", None, None,
                     sorted(set(flags + ["generation_not_established"])))]
    strike_y = indexed(contract.strike, getattr(contract, "indexation_pct_per_year", 0.0),
                       getattr(contract, "base_year", None), y)
    mwh = gen * w
    # §51 EEG style: intervals at a negative price are not supported — the
    # PAYMENT is suspended there; a monthly capture price is still the whole
    # month's generation-weighted reference (review 2.2a #1).
    paid = (np.where(ref < 0, 0.0, mwh) if getattr(contract, "suspend_on_negative_price", False)
            else mwh)
    if getattr(contract, "reference", "interval") == "monthly_capture":
        months = np.asarray(inputs.index.strftime("%Y-%m"))
        diff = np.zeros(len(mwh))
        for m in np.unique(months):
            sel = months == m
            vol = float(mwh[sel].sum())
            if vol > 0:
                diff[sel] = strike_y - float((ref[sel] * mwh[sel]).sum()) / vol
        amount = float((diff * paid).sum())
    else:
        amount = float(((strike_y - ref) * paid).sum())
    return [Line(inputs.period, contract.id, payer, payee, "cfd_difference", float(paid.sum()),
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
    # share of its bus's load in that interval. Nothing unknown becomes 0
    # (ADR-0001; review 2.2b #1).
    def unknown(flag):
        return out + [Line(inputs.period, contract.id, payer, payee, "dr_activation", None,
                           None, sorted(set(parties + [flag])))]

    act = np.zeros(len(inputs.index))
    bus_of = pd.Series(inputs.load_bus)
    for load in contract.load_ids:
        bus = inputs.load_bus[load]
        if bus not in frame.columns:
            return unknown("dr_bus_not_dsr_enabled")
        on_bus = [l for l in bus_of.index[bus_of == bus] if l in inputs.loads.columns]
        # skipna=False: a NaN on ANOTHER load of the bus must not shrink the
        # bus load and inflate this load's share (review round 2).
        bus_load = inputs.loads[on_bus].reindex(inputs.index).sum(axis=1, skipna=False) \
            .to_numpy(dtype=float)
        mine = inputs.loads[load].reindex(inputs.index).to_numpy(dtype=float)
        dsr = frame[bus].reindex(inputs.index).to_numpy(dtype=float)
        if np.isnan(dsr).any() or np.isnan(mine).any() or np.isnan(bus_load).any():
            return unknown("dr_activation_not_established")
        if ((dsr > 1e-9) & (bus_load <= 0)).any():
            return unknown("dr_attribution_not_established")   # activation with no load
        share = np.divide(mine, bus_load, out=np.zeros_like(mine), where=bus_load > 0)
        act += dsr * share
    w = np.asarray(inputs.weights, dtype=float)
    mwh = float((act * w).sum())
    ev_flags = list(parties)
    step = _step_hours(inputs)
    events, longest = _events(act, step, inputs.index)
    represented = _represented_events(act, step, w, inputs.index)
    # `max_events` is per calendar year: the sampled count is scaled by the
    # year's hours over the sampled hours, and the estimate is disclosed.
    # Each sampled event stands for w/step events at its start row (unequally
    # weighted representative periods); the represented total is scaled to a
    # year by its represented hours (review round 2).
    sampled_h = float(np.asarray(step, dtype=float).sum())
    year_h = _hours_in_year(modelled_year(inputs))
    total_w = float(w.sum())
    per_year = represented * year_h / total_w if total_w > 0 else float(events)
    if abs(sampled_h - year_h) > 1e-6:
        ev_flags.append("dr_events_extrapolated")
    if contract.max_events is not None and per_year > contract.max_events + 1e-9:
        ev_flags.append("dr_max_events_exceeded")
    if contract.max_duration_h is not None and longest > contract.max_duration_h + 1e-9:
        ev_flags.append("dr_max_duration_exceeded")
    out.append(Line(inputs.period, contract.id, payer, payee, "dr_activation", mwh,
                    float(contract.activation_eur_per_mwh * mwh), sorted(set(ev_flags))))
    return out


def _step_hours(inputs: SettlementInputs) -> np.ndarray:
    """Each row's real interval length in hours: `step_hours`, else the
    index's steps (the median in-stretch step for the last row and across
    gaps) — never the represented-hour weights (review 2.2b #2)."""
    if inputs.step_hours is not None:
        return np.broadcast_to(np.asarray(inputs.step_hours, dtype=float),
                               (len(inputs.index),)).copy()
    idx = inputs.index
    if len(idx) < 2:
        return np.ones(len(idx))
    d = np.diff(idx.asi8) / 3.6e12
    typical = float(np.median(d[d > 0])) if (d > 0).any() else 1.0
    step = np.append(d, typical)
    return np.where((step > 0) & (step <= typical + 1e-9), step, typical)


def _represented_events(active_mw, step_h, weights, index) -> float:
    """Σ over events of w/step at the event's first row: the number of events
    the sampled ones stand for in the represented hours."""
    on = np.asarray(active_mw) > 1e-9
    step = np.broadcast_to(np.asarray(step_h, dtype=float), on.shape)
    w = np.broadcast_to(np.asarray(weights, dtype=float), on.shape)
    t = index.asi8 / 3.6e12
    total = 0.0
    for i, flag in enumerate(on):
        starts = flag and (i == 0 or not on[i - 1]
                           or t[i] - t[i - 1] > float(step[i - 1]) + 1e-9)
        if starts:
            total += float(w[i]) / float(step[i]) if step[i] > 0 else 1.0
    return total


def _events(active_mw: np.ndarray, step_h, index: pd.DatetimeIndex | None = None
            ) -> tuple[int, float]:
    """(number of events, the longest in hours): an event is a maximal run of
    consecutive intervals with activation > 0. Rows adjacent in the index but
    not in time (a gap longer than the row's step, e.g. two representative
    days) end a run."""
    on = np.asarray(active_mw) > 1e-9
    step = np.broadcast_to(np.asarray(step_h, dtype=float), on.shape)
    t = None if index is None else index.asi8 / 3.6e12
    events, longest, run = 0, 0.0, 0.0
    for i, flag in enumerate(on):
        contiguous = (i > 0 and (t is None or t[i] - t[i - 1] <= float(step[i - 1]) + 1e-9))
        if flag:
            if run == 0.0 or not contiguous:
                events += 1
                run = 0.0
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
        delivered += src[a].reindex(inputs.index).to_numpy(dtype=float)
    if np.isnan(delivered).any():
        # An unknown delivery is not a zero one (ADR-0001; WP2.5 review R2-3).
        return [Line(inputs.period, contract.id, contract.customer, contract.provider,
                     "eaas_fee", None, None, ["delivery_not_established"])]
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
