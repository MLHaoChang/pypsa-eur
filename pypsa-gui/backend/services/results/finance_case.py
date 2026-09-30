"""
The finance adapter: a solved network and its P3 value-flow ledger → a plain
`FinanceCase` (Edge Investment Case P4, plan C1, WP4.6a).

`build_finance_case(n, cfg, fin, *, result_df, lost_load=None, owner=None)` is
the ONE place the finance engine's input is read from a network (plan C1: the
engine imports nothing from `services.results`; the router injects a
`build_case` callable). It refuses with `FinanceRefused(code)` — stable codes:

  value_flows_not_configured, value_flows_invalid, owner_has_no_assets,
  owner_ambiguous, staged_build_not_supported, template_not_annual:<hours>,
  cod_missing, cod_mismatch, counterfactual_not_established:lossy_poc,
  owner_asset_missing, stream_unmapped:<stream>.

What it builds:

* **The owner** — the given `owner`, else the single distinct owner of
  `vf.asset_owners`; the owner's assets are its `asset_owners` entries
  (`same_party`). A multi-period owner asset built after the first period is
  a staged build (P5).
* **The annual check (C3)** — Σ objective weights per period within 0.5 % of
  8,760 (8,784 in a leap weight-majority year), else refused
  `template_not_annual:<hours>`; `fin.annualise` scales every template line
  (actual and counterfactual) and the energy by 8,760 / hours, flagged
  `template_annualised:<factor>`.
* **The templates (C3, P3 WP3.1 mapping)** — one per period (`first_year` =
  `money_year` = the period year; flat: the weight-majority year): the owner's
  `basis == "cash"` ledger lines (`annuity` / `model_only` are not converted),
  signed from the owner (+ when it is the payee), `None` kept. Key
  `<source>:<source_id>`, except contract lines, whose ledger `source_id`
  carries a settlement row index that differs between periods: they are keyed
  `contract:<id>:<P2 stream>` (stable across periods). Lines of one key in a
  period are summed. Contract lines carry the contract's class, indexation
  (P2's percent as a fraction; a type without the field → None, the engine's
  `ppa`), tenor, price (the money-year indexed price, only where the amount is
  proportional to it) and `changes_dispatch`.
* **Degradation links (C5)** — the export split (`LedgerInputs.export_split`,
  `export_revenue_to="asset_owner"`) is decomposed per owner Generator
  (`<key>:<asset>`, `degrades_with` the asset; the rest stays on `<key>`);
  a pay-as-produced / sleeved PPA or a CfD on exactly one owner generator and
  an owner generator's own vom / fuel line degrade with it; other
  generation-linked lines are flagged `degradation_link_unknown:<key>`.
  `energy_mwh` = the owner's GENERATORS' output for one year of the period
  (objective weights, which never carry the period's years), never storage.
* **The counterfactual (C13)** — only when the owner is the site party (it
  pays the bill): per period the tariff bill rated by `billing.rate_meter` on
  the served load (site-side electric loads' demand − the actual DSR shed per
  bus − the VoLL shed per load; export 0; shed disclosed
  `load_shed_excluded:<mwh>`), the commodity on the meter basis (cross-checked
  against the ledger's `commodity_from_grid_side_generator` line to a cent),
  the owner's connection-fee lines copied, and the lines of contracts that
  name none of the owner's assets copied (they would exist without the
  investment; flagged `counterfactual_keeps_contract:<id>`). Contracts on the
  owner's assets are absent.
* **The first-order bill effect of degradation (C5)** — per owner generator
  with a degradation entry: S = (counterfactual − actual) energy-volume bill items +
  commodity, g = its share of on-site generation; `bill_degradation:<a>` =
  +S·g degrading with it and `bill_degradation_base:<a>` = −S·g: in operating
  year k they net −S·g·(1 − f_k).
* **Assets (C6)** — `overnight_cost` = the TYPED `overnight_cost` column ×
  the optimised capacity, None when not typed (never back-calculated from the
  annuitised `capital_cost`).
* **Dates, LP basis, hash** — `base_year` the modelled / first period year;
  COD from `fin.cod_by_asset` (every owner asset, one date); `LpBasis` from the
  config and the assets' own `discount_rate`; `finance_case_hash`.

Refusal vs None: a lossy PoC chain REFUSES the case (a counterfactual that
cannot be stated makes every return meaningless, and an empty one would read
as "no counterfactual" — incremental = total). Any other counterfactual term
that cannot be established (the commodity, the shed, other parties' site
generation, a hub allocation, a period the tariff cannot rate) is a `None`
line with its flag: the engine then reports the incremental cash
`not_established` with the reason (plan C12).

Imports no router.
"""
from __future__ import annotations

import calendar
import dataclasses
import hashlib
import json
import math
from datetime import date

import numpy as np
import pandas as pd

from services.commercial import billing as _billing
from services.commercial import contracts as _K
from services.commercial import lp_bindings as _lp
from services.commercial import participants as P
from services.commercial.lp_bindings import same_party
from services.finance.case import (
    CONTRACT_CLASS, AssetFinance, FinanceCase, FinanceRefused, LpBasis, Template, TemplateLine,
)
from services.finance.cashflow import esc_class_for

ANNUAL_TOL = 0.005                      # plan C3: within 0.5 % of a year
COMMODITY_TOL = 0.01                    # plan C13: the ledger cross-check, a cent
_EPS_MW = 1e-6

_CAPACITY_COLS = {"Generator": "p_nom", "StorageUnit": "p_nom", "Store": "e_nom", "Link": "p_nom",
                  "Line": "s_nom", "Transformer": "s_nom"}


def _num(x: float) -> str:
    """A flag's number: two decimals, trailing zeros dropped (168.0 → "168")."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _fin(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _periods(n) -> list:
    if isinstance(n.snapshots, pd.MultiIndex):
        return [int(p) for p in n.snapshots.get_level_values(0).unique()]
    return [None]


def _key(p) -> str:
    return "_" if p is None else str(int(p))


def _mask(n, p) -> np.ndarray:
    if p is None:
        return np.ones(len(n.snapshots), dtype=bool)
    return np.asarray(n.snapshots.get_level_values(0)) == p


def _times(n, mask) -> pd.DatetimeIndex:
    s = n.snapshots[mask]
    return pd.DatetimeIndex(s.get_level_values(-1) if isinstance(s, pd.MultiIndex) else s)


def _series(n, frame_t: str, attr: str, name: str, static, mask=None) -> np.ndarray:
    """A component attribute per snapshot: the time-varying column where
    present, else the static value."""
    store = getattr(getattr(n, frame_t), attr, None)
    if store is not None and name in getattr(store, "columns", []):
        out = store[name].reindex(n.snapshots).to_numpy(dtype=float)
    else:
        out = np.full(len(n.snapshots), float(static))
    return out if mask is None else out[mask]


def _frame_of(component: str):
    return P._COMPONENT_FRAMES.get(component)


# ── owner ────────────────────────────────────────────────────────────────────

def _resolve_owner(vf, owner: str | None) -> tuple[str, list[tuple[str, str]]]:
    distinct: list[str] = []
    for o in vf.asset_owners:
        if not any(same_party(o.owner, d) for d in distinct):
            distinct.append(o.owner)
    if owner is None:
        if not distinct:
            raise FinanceRefused("owner_has_no_assets", "value_flows.asset_owners is empty")
        if len(distinct) > 1:
            raise FinanceRefused("owner_ambiguous",
                                 f"asset_owners name several owners {sorted(distinct)}: "
                                 "state the owner")
        owner = distinct[0]
    assets = [(o.component, o.asset_id) for o in vf.asset_owners if same_party(o.owner, owner)]
    if not assets:
        raise FinanceRefused("owner_has_no_assets", f"{owner!r} owns no asset in asset_owners")
    return P.canonical_party(owner, vf), assets


# ── C3: the annual check ─────────────────────────────────────────────────────

def _period_year_and_hours(n, p) -> tuple[int, float, int]:
    """(the period's year, Σ objective weights, the weight-majority calendar
    year of its snapshots)."""
    m = _mask(n, p)
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)[m]
    ts = _times(n, m)
    years = pd.Series(w).groupby(np.asarray(ts.year)).sum()
    majority = int(years.idxmax())
    return (majority if p is None else int(p)), float(w.sum()), majority


# ── the ledger → template lines ──────────────────────────────────────────────

def _contract_stream(inputs: P.LedgerInputs, source_id: str) -> str:
    cid, _, tail = source_id.rpartition(":")
    if tail == "unsettled":
        return "unsettled"
    try:
        return str(inputs.settlement[int(tail)].get("value_stream"))
    except (ValueError, IndexError):
        return tail


def _line_key(ln: P.ValueFlowLine, inputs: P.LedgerInputs) -> str:
    if ln.source == "contract":
        return f"contract:{ln.contract_id}:{_contract_stream(inputs, ln.source_id)}"
    return f"{ln.source}:{ln.source_id}"


def _owner_sign(ln: P.ValueFlowLine, owner: str) -> float | None:
    if same_party(ln.payee, owner):
        return 1.0
    if same_party(ln.payer, owner):
        return -1.0
    return None


def _esc(stream: str) -> str:
    try:
        return esc_class_for(stream)
    except ValueError:
        raise FinanceRefused(f"stream_unmapped:{stream}",
                             "a ledger stream with no escalation class") from None


def _indexed_price(c, year: int) -> float | None:
    """The money-year price the amount is proportional to: a fixed-price PPA
    settled on volume (pay-as-produced, as-consumed, sleeved energy)."""
    if getattr(c, "type", None) != "ppa" or c.kind == "baseload" or \
            getattr(c, "pricing", "fixed") != "fixed":
        return None
    return _K.indexed(c.price, c.indexation_pct_per_year, c.base_year, year)


def _contract_link(c, owner_gens: set[str]) -> tuple[bool, str | None]:
    """(generation-linked, the degrading owner generator or None)."""
    if c is None:
        return False, None
    t = getattr(c, "type", None)
    assets = list(getattr(c, "asset_ids", []) or [])
    on_owner = [a for a in assets if a in owner_gens]
    if not on_owner:
        return False, None
    if t == "ppa":
        if c.kind == "baseload":
            return False, None                      # financial: a fixed MW, not the output
        if c.kind in ("pay_as_produced", "sleeved") and len(assets) == 1:
            return True, assets[0]                  # volume = the asset's generation
        return True, None                           # as-consumed, or several assets
    if t == "cfd":
        return True, (assets[0] if len(assets) == 1 else None)   # difference × generation
    if t == "eaas" and c.fee_eur_per_mwh is not None:
        return True, None                           # per MWh delivered, plus any fixed fee
    return False, None


@dataclasses.dataclass
class _Acc:
    """Lines of one key in one period, summed."""

    stream: str
    esc_class: str
    amount: float | None
    source: str
    source_id: str
    tariff_item: str | None = None
    contract_id: str | None = None
    counterparty: str = "external"
    asset: str | None = None
    flags: list[str] = dataclasses.field(default_factory=list)

    def add(self, v: float | None) -> None:
        self.amount = None if (self.amount is None or v is None) else self.amount + v


def _owner_lines(ledger, inputs, owner: str, k: str, flags: list[str]) -> dict[str, _Acc]:
    out: dict[str, _Acc] = {}
    for ln in ledger.periods.get(k, []):
        if ln.basis != "cash":
            continue                           # annuity / model_only: not converted (P3 pin)
        sign = _owner_sign(ln, owner)
        if sign is None:
            if ln.amount is None and ln.payer is None and ln.payee is None:
                flags.append(f"ledger_line_party_unknown:{ln.source}:{ln.source_id}")
            continue
        key = _line_key(ln, inputs)
        v = None if ln.amount is None else sign * ln.amount
        other = ln.payer if sign > 0 else ln.payee
        if key in out:
            out[key].add(v)
            out[key].flags += [f for f in ln.flags if f not in out[key].flags]
        else:
            esc = CONTRACT_CLASS if ln.source == "contract" else _esc(ln.value_stream)
            out[key] = _Acc(stream=ln.value_stream, esc_class=esc, amount=v, source=ln.source,
                            source_id=ln.source_id, tariff_item=ln.tariff_item,
                            contract_id=ln.contract_id, counterparty=other or "external",
                            asset=ln.asset, flags=list(ln.flags))
    return out


def _is_commodity(acc: _Acc) -> bool:
    return acc.source == "asset" and "commodity_from_grid_side_generator" in acc.flags


def _template_lines(accs: dict[str, _Acc], *, k: str, year: int, inputs, vf, owner: str,
                    owned: set[tuple[str, str]], owner_gens: set[str], contracts: dict,
                    flags: list[str]) -> list[TemplateLine]:
    split = (inputs.export_split or {}).get(k) or {}
    owners = {(o.component, o.asset_id): o.owner for o in vf.asset_owners}
    lines: list[TemplateLine] = []
    for key, a in accs.items():
        base = dict(stream=a.stream, esc_class=a.esc_class, tariff_item=a.tariff_item,
                    counterparty=a.counterparty, source=a.source, source_id=a.source_id,
                    period=k)
        sid = "export_price" if a.source == "export_price" else a.source_id
        # The split was established for this source (possibly with no part
        # of the owner's generation in it).
        parts = split.get(sid) if a.source in ("export_price", "bill") else None
        has_split = parts is not None
        if a.source == "contract":
            c = contracts.get(a.contract_id)
            linked, asset = _contract_link(c, owner_gens)
            if linked and asset is None:
                flags.append(f"degradation_link_unknown:{key}")
            pct = getattr(c, "indexation_pct_per_year", None) if c is not None else None
            stream_p2 = key.rsplit(":", 1)[-1]
            lines.append(TemplateLine(
                key=key, amount=a.amount, contract_id=a.contract_id, degrades_with=asset,
                indexation=None if pct is None else float(pct) / 100.0,
                tenor_years=getattr(c, "tenor_years", None) if c is not None else None,
                price=_indexed_price(c, year) if stream_p2 == "ppa_energy" else None,
                changes_dispatch=bool(getattr(c, "changes_dispatch", False)), **base))
            continue
        if has_split and a.amount is not None:
            # The export split per owner generator (C5): each part degrades
            # with its asset; the rest (storage export, no site generation,
            # other owners' assets) stays on the key.
            sign = 1.0 if a.source == "export_price" else -1.0
            rest = a.amount
            for akey, v in sorted(parts.items(), key=lambda kv: str(kv[0])):
                if akey is None or tuple(akey) not in owned or \
                        not same_party(owners.get(tuple(akey)), owner):
                    continue
                comp, name = tuple(akey)
                amt = sign * float(v)
                rest -= amt
                lines.append(TemplateLine(key=f"{key}:{name}", amount=amt,
                                          degrades_with=name if comp == "Generator" else None,
                                          **base))
            if abs(rest) > 1e-9:
                lines.append(TemplateLine(key=key, amount=rest, **base))
            continue
        degrades = None
        if a.source == "asset" and not _is_commodity(a) and a.stream in ("vom", "fuel"):
            comp = a.source_id.split(":")[1] if a.source_id.count(":") >= 2 else None
            if comp == "Generator" and a.asset in owner_gens:
                degrades = a.asset                  # Σ w·p·mc of the asset itself
        if a.stream == "energy_export" and owner_gens:
            flags.append(f"degradation_link_unknown:{key}")
        lines.append(TemplateLine(key=key, amount=a.amount, degrades_with=degrades, **base))
    return lines


# ── generation ───────────────────────────────────────────────────────────────

def _gen_frame(n, result_df) -> pd.DataFrame | None:
    p = result_df(n, "generators_t", "p") if result_df is not None else None
    if p is None:
        p = getattr(n.generators_t, "p", None)
    return p


def _generation(n, result_df, names, mask) -> dict[str, float]:
    p = _gen_frame(n, result_df)
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)[mask]
    out = {}
    for g in names:
        if p is None or g not in p.columns:
            out[g] = 0.0
            continue
        v = np.clip(p[g].reindex(n.snapshots).to_numpy(dtype=float)[mask], 0.0, None)
        out[g] = float((w * v).sum())
    return out


def _site_generation(n, parsed, result_df, mask) -> float:
    """On-site electric generation of the period (MWh): the site's Generators
    and converting Links, the export split's definition."""
    gens = _lp.site_generators(n, parsed)
    total = sum(_generation(n, result_df, gens, mask).values())
    links = _lp.site_link_generation(n, parsed, lambda k: getattr(n.links_t, f"p{k}", None))
    if links is not None and not links.empty:
        w = n.snapshot_weightings.objective.to_numpy(dtype=float)[mask]
        total += float((w[:, None] * links.clip(lower=0.0).to_numpy(dtype=float)[mask]).sum())
    return total


# ── C13: the counterfactual ──────────────────────────────────────────────────

def _link_eff_is_one(n, link: str) -> bool:
    eff = _series(n, "links_t", "efficiency", link, n.links.at[link, "efficiency"])
    return bool(np.allclose(eff, 1.0, atol=1e-12))


def _lossy_poc(n, parsed, sides, load_buses: set[str]) -> bool:
    """A PoC import chain with efficiency ≠ 1: an import Link, or a site load
    reachable from the meter's site bus only through a lossy Link."""
    members = [m for m in _lp.import_links(parsed) if m in n.links.index]
    if any(not _link_eff_is_one(n, m) for m in members):
        return True
    meter = set(members) | ({parsed.export_link} if parsed.export_link else set())
    lossless: dict[str, set[str]] = {}
    anyway: dict[str, set[str]] = {}

    def edge(a, b, ok, both):
        for g in ((anyway,) + ((lossless,) if ok else ())):
            g.setdefault(a, set()).add(b)
            if both:
                g.setdefault(b, set()).add(a)

    for comp in ("lines", "transformers"):
        df = getattr(n, comp)
        for name in df.index:
            edge(str(df.at[name, "bus0"]), str(df.at[name, "bus1"]), True, True)
    for name in n.links.index:
        if name in meter:
            continue
        b0, b1 = str(n.links.at[name, "bus0"]), str(n.links.at[name, "bus1"])
        rev = float(n.links.at[name, "p_min_pu"]) < 0
        edge(b0, b1, _link_eff_is_one(n, name), rev)

    def reach(graph):
        seen, todo = set(), [str(n.links.at[m, "bus1"]) for m in members]
        while todo:
            b = todo.pop()
            if b in seen:
                continue
            seen.add(b)
            todo.extend(graph.get(b, ()))
        return seen

    clean, dirty = reach(lossless), reach(anyway)
    return any(b in dirty and b not in clean for b in load_buses)


def _site_loads(n, parsed, sides) -> list[str]:
    electric = _lp._electric_bus_test(n, parsed)
    return [str(ld) for ld in n.loads.index
            if str(n.loads.at[ld, "bus"]) in sides.site and electric(str(n.loads.at[ld, "bus"]))]


def _served_load(n, cfg, loads: list[str], lost_load) -> tuple[np.ndarray, np.ndarray,
                                                                  list[str]]:
    """(served load MW, shed MW, reasons it is not established) per snapshot."""
    from services.commercial import settlement_inputs as _SI

    demand = np.zeros(len(n.snapshots))
    for ld in loads:
        demand += _series(n, "loads_t", "p_set", ld, n.loads.at[ld, "p_set"])
    shed = np.zeros(len(n.snapshots))
    reasons: list[str] = []
    buses = {str(n.loads.at[ld, "bus"]) for ld in loads}
    frame = n.buses_t.get(_SI.DSR_ATTR) if hasattr(n.buses_t, "get") else None
    if frame is not None and not frame.empty:
        dsr, _f = _SI.dsr_activation(n)
        if dsr is None:
            reasons.append("dsr_shed_unknown")
        else:
            for b in dsr.columns:
                if str(b) in buses:
                    shed += np.clip(dsr[b].to_numpy(dtype=float), 0.0, None)
    elif float(getattr(cfg, "dsr_price_eur_per_mwh", 0.0) or 0.0) > 0 and \
            float(getattr(cfg, "dsr_share_of_load", 0.0) or 0.0) > 0 and \
            buses & {str(b) for b in (getattr(cfg, "dsr_buses", None) or [])}:
        reasons.append("dsr_shed_unknown")          # DSR configured, no committed record
    ll = (lost_load or {}).get("lost_load_t") if isinstance(lost_load, dict) else None
    if ll is not None and not getattr(ll, "empty", True):
        for ld in loads:
            if ld in ll.columns:
                shed += np.clip(ll[ld].reindex(n.snapshots).fillna(0.0).to_numpy(dtype=float),
                                0.0, None)
    elif float(getattr(cfg, "voll", 0.0) or 0.0) > 0 and lost_load is None:
        reasons.append("voll_shed_unknown")         # VoLL slacks, no capture handed in
    return np.clip(demand - shed, 0.0, None), shed, reasons


def _is_sink(n, g: str) -> bool:
    """An uncosted export sink: it never supplies (p_max_pu 0), absorbs
    (p_min_pu < 0) and costs nothing (WP4.0 review B1)."""
    row = n.generators.loc[g]
    pmax = _series(n, "generators_t", "p_max_pu", g, row["p_max_pu"])
    pmin = _series(n, "generators_t", "p_min_pu", g, row["p_min_pu"])
    mc = _series(n, "generators_t", "marginal_cost", g, row["marginal_cost"])
    return bool(np.all(pmax <= 0.0) and np.any(pmin < 0.0) and np.all(mc == 0.0))


def _supply(n, parsed, sides) -> tuple[list[str], str | None]:
    """(the priced grid-side supply generators, why the commodity price is not
    established or None)."""
    gens = [str(g) for g in n.generators.index
            if str(n.generators.at[g, "bus"]) in sides.grid and not _is_sink(n, str(g))]
    if not gens:
        return [], None
    quad = "marginal_cost_quadratic"
    for g in gens:
        q = _series(n, "generators_t", quad, g, n.generators.at[g, quad]) \
            if quad in n.generators.columns else np.zeros(1)
        if np.any(q != 0.0):
            return gens, "quadratic_cost"
    mcs = [_series(n, "generators_t", "marginal_cost", g, n.generators.at[g, "marginal_cost"])
           for g in gens]
    if any(not np.allclose(m, mcs[0]) for m in mcs[1:]):
        return gens, "several_priced_generators"
    return gens, None


def _supply_capacity(n, gens: list[str]) -> np.ndarray:
    cap = np.zeros(len(n.snapshots))
    for g in gens:
        pn = _fin(n.generators.at[g, "p_nom_opt"]) if "p_nom_opt" in n.generators.columns \
            else None
        pn = pn if pn is not None else float(n.generators.at[g, "p_nom"])
        cap += pn * _series(n, "generators_t", "p_max_pu", g, n.generators.at[g, "p_max_pu"])
    return cap


def _non_owner_site_assets(n, parsed, sides, owned: set[tuple[str, str]]) -> list[str]:
    """Site-side generation and storage the owner does not own: the served-load
    meter removes it too, so the counterfactual would not be 'the site
    without the owner's assets'."""
    out = [f"Generator:{g}" for g in _lp.site_generators(n, parsed) if ("Generator", g) not in owned]
    for comp, frame in (("StorageUnit", "storage_units"), ("Store", "stores")):
        df = getattr(n, frame)
        for name in df.index:
            if str(df.at[name, "bus"]) in sides.site and (comp, str(name)) not in owned:
                out.append(f"{comp}:{name}")
    for link in _lp.site_generating_links(n, parsed):
        if ("Link", str(link)) not in owned:
            out.append(f"Link:{link}")
    return sorted(out)


def _cf_none(key: str, flag: str, k: str) -> TemplateLine:
    return TemplateLine(key=key, stream="other", amount=None, esc_class="tariff",
                        source="counterfactual", source_id=flag, period=k)


def _counterfactual(n, cfg, parsed, sides, inputs, vf, ledger_accs: dict[str, dict[str, _Acc]],
                    templates_actual: dict[str, list[TemplateLine]], owned, contracts,
                    lost_load, flags: list[str]) -> tuple[dict[str, list[TemplateLine]],
                                                          dict[str, float | None]]:
    """Per period the counterfactual lines and S (avoided import value:
    counterfactual − actual import bill + commodity), for C5."""
    loads = _site_loads(n, parsed, sides)
    if _lossy_poc(n, parsed, sides, {str(n.loads.at[ld, "bus"]) for ld in loads}):
        raise FinanceRefused("counterfactual_not_established:lossy_poc",
                             "the PoC import chain has an efficiency ≠ 1: the served load is "
                             "not the metered import")
    served, shed, shed_reasons = _served_load(n, cfg, loads, lost_load)
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    shed_mwh = float((w_all * shed).sum())
    if shed_mwh > 0.0:
        flags.append(f"load_shed_excluded:{_num(shed_mwh)}")
    others = _non_owner_site_assets(n, parsed, sides, owned)
    blockers = [f"counterfactual_not_established:{r}" for r in shed_reasons]
    if others:
        blockers.append("counterfactual_not_established:non_owner_site_assets")
        flags.append("counterfactual_removes_non_owner_assets:" + ",".join(others))
    flags += blockers

    # The tariff bill on the served-load meter (C13; `rate_meter` = bill_site's rating).
    items = inputs.bill_items
    payees = P.resolve_tariff_payees(items, vf, inputs.retailer)
    bill = None
    if parsed.import_tariff is not None:
        bill = _billing.rate_meter(n, parsed, served, np.zeros(len(n.snapshots)))
        cap = float(bill.provenance["capacity_basis"]["p_nom_mw"])
        if served.max(initial=0.0) > cap + _EPS_MW:
            flags.append("counterfactual_exceeds_connection")
        flags += [f"counterfactual_bill:{f}" for f in bill.flags]

    # The commodity on the meter basis, cross-checked against the ledger.
    supply, why = _supply(n, parsed, sides)
    scratch: list[str] = []
    p0 = n.links_t.p0
    imp_actual = np.sum([_billing._flow(p0, m, scratch) for m in _lp.import_links(parsed)],
                        axis=0)
    mc = None
    if supply and why is None:
        g0 = supply[0]
        mc = _series(n, "generators_t", "marginal_cost", g0, n.generators.at[g0, "marginal_cost"])
        if np.any(served > _supply_capacity(n, supply) + _EPS_MW):
            flags.append("counterfactual_exceeds_supply")

    out: dict[str, list[TemplateLine]] = {}
    avoided: dict[str, float | None] = {}
    for p in _periods(n):
        k = _key(p)
        m = _mask(n, p)
        lines: list[TemplateLine] = []
        for b in blockers:
            lines.append(_cf_none(f"counterfactual:{b.split(':', 1)[1]}", b, k))
        if any(a.source == "allocation" for a in ledger_accs[k].values()):
            flags.append("counterfactual_not_established:hub_allocation")
            lines.append(_cf_none("counterfactual:hub_allocation",
                                  "counterfactual_not_established:hub_allocation", k))
        # bill
        cf_import_bill: float | None = 0.0
        act_import_bill: float | None = 0.0
        if bill is not None:
            res = bill.per_period.get(p)
            if res is None:
                flags.append(f"counterfactual_bill_not_established:{k}")
            for item_id, it in items.items():
                v = None if res is None else _fin(res.per_item_sampled.get(item_id))
                stream = P._item_stream(it, payees[item_id])
                lines.append(TemplateLine(
                    key=f"bill:{item_id}", stream=stream, amount=None if v is None else -v,
                    esc_class=_esc(stream), tariff_item=item_id, counterparty=payees[item_id],
                    source="bill", source_id=item_id, period=k))
                # S counts the energy-volume items only (plan C5 review): demand,
                # capacity and fixed charges are shaved by storage / the peak,
                # not in proportion to the PV's energy, so they do not degrade
                # with it.
                if stream in ("energy_import", "network_energy"):
                    a = (inputs.bill.get(k) or {}).get(item_id)
                    cf_import_bill = None if (cf_import_bill is None or v is None) \
                        else cf_import_bill + v
                    act_import_bill = None if (act_import_bill is None or a is None) \
                        else act_import_bill + a
        # commodity
        commodity = [a for a in ledger_accs[k].values() if _is_commodity(a)]
        cf_comm: float | None = 0.0
        act_comm: float | None = 0.0
        if commodity:
            ledger_comm = None if any(a.amount is None for a in commodity) \
                else -sum(a.amount for a in commodity)           # the site pays: > 0
            reason = why
            if reason is None and mc is None:
                reason = "no_priced_supply_generator"
            if reason is None:
                actual = float((w_all[m] * imp_actual[m] * mc[m]).sum())
                if ledger_comm is None or abs(actual - ledger_comm) > COMMODITY_TOL:
                    reason = "ledger_cross_check"
            if reason is None:
                cf_comm = float((w_all[m] * served[m] * mc[m]).sum())
                act_comm = ledger_comm
            else:
                cf_comm = act_comm = None
                flags += ["counterfactual_commodity_not_established",
                          f"counterfactual_commodity_not_established:{reason}"]
            lines.append(TemplateLine(
                key="counterfactual:commodity", stream="energy_import",
                amount=None if cf_comm is None else -cf_comm, esc_class="tariff",
                counterparty="market", source="counterfactual", source_id="commodity", period=k))
        # connection fees and the contracts that exist without the assets
        for ln in templates_actual[k]:
            if ln.source == "connection":
                lines.append(ln)
            elif ln.source == "contract":
                c = contracts.get(ln.contract_id)
                on_owner = c is not None and any(
                    (comp, a) in owned for a in (getattr(c, "asset_ids", None) or [])
                    for comp in ("Generator", "StorageUnit", "Store", "Link"))
                if c is not None and not on_owner:
                    lines.append(ln)
                    flags.append(f"counterfactual_keeps_contract:{ln.contract_id}")
        out[k] = lines
        avoided[k] = None if None in (cf_import_bill, act_import_bill, cf_comm, act_comm) else \
            (cf_import_bill + cf_comm) - (act_import_bill + act_comm)
    return out, avoided


# ── assets, dates ────────────────────────────────────────────────────────────

def _capacity(df, name: str, col: str) -> float | None:
    opt = _fin(df.at[name, f"{col}_opt"]) if f"{col}_opt" in df.columns else None
    return opt if opt is not None else _fin(df.at[name, col]) if col in df.columns else None


def _assets(n, owned_list) -> tuple[tuple[AssetFinance, ...], dict[str, float | None]]:
    assets, rates = [], {}
    for comp, name in owned_list:
        df = getattr(n, _frame_of(comp) or "", None)
        if df is None or name not in df.index:
            raise FinanceRefused("owner_asset_missing", f"{comp} {name!r} is not in the network")
        typed = _fin(df.at[name, "overnight_cost"]) if "overnight_cost" in df.columns else None
        cap = _capacity(df, name, _CAPACITY_COLS[comp])
        overnight = None if (typed is None or cap is None) else typed * cap
        life = _fin(df.at[name, "lifetime"]) if "lifetime" in df.columns else None
        carrier = str(df.at[name, "carrier"]) if "carrier" in df.columns else None
        assets.append(AssetFinance(name=name, component=comp, overnight_cost=overnight,
                                   lifetime_years=life, carrier=carrier or None))
        rates[name] = _fin(df.at[name, "discount_rate"]) if "discount_rate" in df.columns \
            else None
    return tuple(assets), rates


def _cod(fin, owned_list) -> date:
    names = [name for _c, name in owned_list]
    missing = [a for a in names if a not in fin.cod_by_asset]
    if missing:
        raise FinanceRefused("cod_missing", f"cod_by_asset names no date for {missing}")
    dates = {fin.cod_by_asset[a] for a in names}
    if len(dates) > 1:
        raise FinanceRefused("cod_mismatch",
                             f"the owner's assets have different CODs {sorted(dates)} (P5)")
    return next(iter(dates))


def _scale(lines, f: float) -> tuple[TemplateLine, ...]:
    if f == 1.0:
        return tuple(lines)
    return tuple(dataclasses.replace(ln, amount=None if ln.amount is None else ln.amount * f)
                 for ln in lines)


# ── the entry point ──────────────────────────────────────────────────────────

def build_finance_case(n, cfg, fin, *, result_df, lost_load=None,
                       owner: str | None = None) -> FinanceCase:
    """The owner's `FinanceCase` from the solved network `n`, its solver
    config `cfg` and the stored `FinanceInputs` `fin` (see the module
    docstring); `FinanceRefused(code)` when it cannot be stated."""
    from services.results.value_flows import value_flow_ledger

    try:
        got = value_flow_ledger(n, cfg, result_df=result_df, lost_load=lost_load)
    except P.ValueFlowsInvalid as exc:
        raise FinanceRefused("value_flows_invalid", str(exc)[:300]) from exc
    if got is None:
        raise FinanceRefused("value_flows_not_configured",
                             "no commercial config, no value_flows, or no solve")
    inputs, vf, ledger, conservation = got
    parsed = _lp._parse(cfg.commercial)
    owner, owned_list = _resolve_owner(vf, owner)
    owned = set(owned_list)
    owner_gens = {name for comp, name in owned_list if comp == "Generator"}
    periods = _periods(n)
    flags: list[str] = []

    # Staged builds are P5 (plan C2).
    if periods[0] is not None:
        first = periods[0]
        for comp, name in owned_list:
            df = getattr(n, _frame_of(comp) or "", None)
            if df is not None and name in df.index and "build_year" in df.columns:
                by = _fin(df.at[name, "build_year"])
                if by is not None and by > first:
                    raise FinanceRefused("staged_build_not_supported",
                                         f"{comp} {name!r} is built in {int(by)}, after the "
                                         f"first period {first} (P5)")

    # C3: the template must be a year.
    info: dict[str, tuple[int, float]] = {}
    factors: dict[str, float] = {}
    for p in periods:
        year, hours, majority = _period_year_and_hours(n, p)
        target = 8784.0 if calendar.isleap(majority) else 8760.0
        k = _key(p)
        info[k] = (year, hours)
        if abs(hours - target) <= ANNUAL_TOL * target:
            factors[k] = 1.0
        elif fin.annualise and hours > 0:
            factors[k] = 8760.0 / hours
            flags.append(f"template_annualised:{_num(factors[k])}")
        else:
            raise FinanceRefused(f"template_not_annual:{_num(hours)}",
                                 f"period {k} represents {hours:g} h, not a year: set "
                                 "annualise to scale it (stated as an approximation)")

    contracts = {c.id: c for c in parsed.contracts}
    accs: dict[str, dict[str, _Acc]] = {}
    actual: dict[str, list[TemplateLine]] = {}
    for p in periods:
        k = _key(p)
        accs[k] = _owner_lines(ledger, inputs, owner, k, flags)
        actual[k] = _template_lines(accs[k], k=k, year=info[k][0], inputs=inputs, vf=vf,
                                    owner=owner, owned=owned, owner_gens=owner_gens,
                                    contracts=contracts, flags=flags)

    # C13: the counterfactual, when the owner is the site party that pays the bill.
    cf_lines: dict[str, list[TemplateLine]] = {}
    avoided: dict[str, float | None] = {}
    if same_party(owner, inputs.site_party):
        sides = P.classify_buses(n, parsed)
        cf_lines, avoided = _counterfactual(n, cfg, parsed, sides, inputs, vf, accs, actual,
                                            owned, contracts, lost_load, flags)

    # C5: degradation's first-order bill effect, per owner generator.
    templates, counterfactual = [], []
    for p in periods:
        k = _key(p)
        year, _hours = info[k]
        m = _mask(n, p)
        gen = _generation(n, result_df, sorted(owner_gens), m)
        lines = list(actual[k])
        if k in avoided:
            site_total = _site_generation(n, parsed, result_df, m)
            for g in sorted(owner_gens):
                if g not in fin.degradation_by_asset or site_total <= 0 or gen[g] <= 0:
                    continue
                s = avoided[k]
                v = None if s is None else s * gen[g] / site_total
                common = dict(stream="energy_import", esc_class="tariff", source="degradation",
                              source_id=g, period=k)
                lines.append(TemplateLine(key=f"bill_degradation:{g}", amount=v,
                                          degrades_with=g, **common))
                lines.append(TemplateLine(key=f"bill_degradation_base:{g}",
                                          amount=None if v is None else -v, **common))
                flags += ["degradation_bill_first_order", "degradation_bill_energy_items_only"]
        f = factors[k]
        templates.append(Template(first_year=year, lines=_scale(lines, f),
                                  energy_mwh={g: e * f for g, e in gen.items()},
                                  money_year=year))
        if k in cf_lines:
            counterfactual.append(Template(first_year=year, lines=_scale(cf_lines[k], f),
                                           money_year=year))

    if conservation.ok is False:
        flags.append("ledger_conservation_failed")
    elif conservation.ok is None:
        flags.append("ledger_conservation_not_established")

    assets, rates = _assets(n, owned_list)
    base_year = info[_key(periods[0])][0]
    lp = LpBasis(discount_rate=_fin(getattr(cfg, "discount_rate", None)),
                 inflation_rate=_fin(getattr(cfg, "inflation_rate", None)),
                 auto_discount_periods=bool(getattr(cfg, "auto_discount_periods", False)),
                 asset_discount_rates=rates)
    return FinanceCase(inputs=fin, owner=owner, base_year=base_year, cod=_cod(fin, owned_list),
                       templates=tuple(templates), assets=assets,
                       flags=tuple(dict.fromkeys(flags)),
                       counterfactual=tuple(counterfactual), lp_basis=lp)


# ── the hash ─────────────────────────────────────────────────────────────────

def _canon(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {f.name: _canon(getattr(o, f.name)) for f in dataclasses.fields(o)}
    if hasattr(o, "model_dump"):
        return _canon(o.model_dump(mode="json"))
    if isinstance(o, dict):
        return {str(k): _canon(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_canon(v) for v in o]
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, date):
        return o.isoformat()
    return o


def finance_case_hash(case: FinanceCase) -> str:
    """sha256[:16] of the case's canonical JSON (the report's staleness)."""
    blob = json.dumps(_canon(case), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
