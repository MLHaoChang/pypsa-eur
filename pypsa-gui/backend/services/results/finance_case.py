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
  `template_annualised:<factor>` — except a monthly-billed item (a demand
  charge, a ratchet: billed once per month present), scaled by 12 / the
  months present (`template_annualised_monthly:<item>:<factor>`), or None
  when its periods are restricted to some months
  (`annualise_monthly_item_not_established:<item>`). A template that
  represents a year (factor 1: a week weighted to 8,760 h) but bills fewer
  than 12 months makes every monthly item's line None on both sides
  (`monthly_item_months_missing:<item>:<months present>`; the operating cash
  is then not established), or with `annualise` scales it by 12 / the months
  present as above (U1 follow-up g, owner rule). A leap weight-majority
  year's 8,784-h template repeats every operating year (+0.27 % in common
  years; stated).
* **The templates (C3, P3 WP3.1 mapping)** — one per period (`first_year` =
  `money_year` = the period year; flat: the weight-majority year): the owner's
  `basis == "cash"` ledger lines (`annuity` / `model_only` are not converted),
  signed from the owner (+ when it is the payee), `None` kept; a line with an
  unknown party that could be the owner is a None line
  (`ledger_party_unknown:<key>`). Money years: contract lines in the period
  year (P2 indexes them to it), every other line in the BASE year
  (`TemplateLine.money_year`; P2 escalates nothing else between periods).
  P3's blocking input flags (`participants._blocking`: drift, a partial
  tariff, an unbilled period, an unsettled contract, an export split not
  established) put a None line `ledger_input_not_established:<flag>` in every
  period; the ledger's and the conservation check's flags join `case.flags`.
  Key
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
  export with no split when the owner owns all site generation is split by
  generation share (`export_degrades_by_generation_share:<key>`, first order:
  stored-energy export degrades too); a pay-as-produced / sleeved /
  as-consumed PPA, a CfD or a per-MWh-only EaaS on exactly one owner
  generator, and an owner generator's own vom / fuel line degrade with it;
  any other generation-linked line is a None line flagged
  `degradation_link_unknown:<key>` (never an undegraded number), unless none
  of its owner generators degrades.
  `energy_mwh` = the owner's GENERATORS' output for one year of the period
  (objective weights, which never carry the period's years), never storage.
* **The counterfactual (C13)** — only when the owner is the site party (it
  pays the bill): per period the tariff bill rated by `billing.rate_meter` on
  the served load (site-side electric loads' demand − the actual DSR shed per
  bus − the VoLL shed per load + the actual electric draw of non-owner site
  conversion Links — a heat pump, an electrolyser —
  `counterfactual_includes_conversion_load:<mwh>`; export 0; shed disclosed
  `load_shed_excluded:<mwh>`), the commodity on the meter basis (cross-checked
  against the ledger's `commodity_from_grid_side_generator` line to
  max(a cent, 1e-9 relative)) KEYED LIKE the actual commodity line, the
  owner's connection-fee lines copied, the cost lines of assets outside the
  owner's copied (a boiler's gas), and the lines of contracts that name none
  of the owner's assets copied (they would exist without the investment;
  flagged `counterfactual_keeps_contract:<id>`). Contracts on the owner's
  assets are absent. An owner conversion Link beside a non-electric site
  load (the owner's heat pump) → `counterfactual_not_established:
  owner_conversion_load`. `counterfactual_hash` = sha256[:16] of the tariff,
  the served load, the connection and the commodity.
* **The first-order bill effect of degradation (C5)** — per owner generator
  with a degradation entry: S = (counterfactual − actual) items billed per
  kWh on the import volume (energy, levies, certificates — by volume basis,
  not stream) + commodity, g = its share of on-site generation;
  `bill_degradation:<a>` = +S·g degrading with it and
  `bill_degradation_base:<a>` = −S·g (`source="degradation"`: value, never
  an asset cost in the LCOE): in operating year k they net −S·g·(1 − f_k). A
  negative S is flagged `degradation_bill_value_negative:<period>`.
* **Assets (C6; IC S0b S1, S2)** — every owner asset's investment comes
  from `asset_schema.access.upfront_parts` (the one accessor): its parts
  (`AssetFinance.parts`: a two-part battery's power and energy, else one
  `investment` part), each part's cost = its upfront per unit × the optimised
  capacity, `overnight_cost` their sum. A typed `overnight_cost` of 0 is an
  established 0. A cost the accessor back-calculates from the annuitised
  `capital_cost` is NOT established (it can include fixed O&M; rule C12):
  None, flagged `upfront_only_from_capital_cost:<a>`; the config's
  `discount_rate` is passed so the back-calculation is detected. The
  owner's PoC meter Links (import members, export Link) with no typed
  `overnight_cost` are not investments — no capex, no COD, no LP rate in the
  gate — flagged
  `meter_link_not_investment:<name>` (GS Q5: `single_owner` assigns them to
  the site party); a typed meter Link stays an asset.
* **Storage (the LCOS, owner decision 6)** — per owner storage asset on a
  site electric bus, `Template.storage[a]` = a `StorageYear`: the year's
  discharge and charge and the charging cost at what the site actually paid
  in the dispatch (`_storage_years`: the grid share of each interval's charge
  at the committed import price + the supply's price; the on-site-surplus
  share at the export revenue forgone), scaled by the annualise factor.
* **Dates, LP basis, hash** — `base_year` the modelled / first period year;
  COD from `fin.cod_by_asset`, else 1 January of the asset's `build_year`
  (`cod_from_build_year:<a>`; PyPSA's 0 is absent — IC S0b S3), one date for
  every owner asset; `LpBasis` from the
  config and the assets' own `discount_rate`; `finance_case_hash`.
* **Extra (campus) assets, IC G2** — `extra_assets` (`ExtraOwnerAsset`s, a
  campus study's chosen equipment): each is one `AssetFinance` after the
  network assets (`campus:<kind>`, no carrier, overnight = Σ upfront per unit ×
  quantity, in the case's money: the caller converts), with one
  `extra_asset_fom:<name>` template line per period (Σ fom_share × overnight,
  `opex`, after the annualise scaling); never in the counterfactual; COD by
  year against the case COD (`cod_from_build_year:<name>`, `cod_mismatch`,
  `extra_asset_staged_build:<name>`); refused
  `extra_asset_duplicates_network_asset:<name>`, `extra_asset_duplicate:<name>`,
  `extra_asset_derived_upfront:<name>`; flagged
  `extra_asset_may_double_count:<name>` beside an owned network Transformer or
  Line.

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
from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd

from services.asset_schema import schema as _schema
from services.asset_schema.access import upfront_parts
from services.asset_schema.derive import has_parts, number
from services.commercial import billing as _billing
from services.commercial import contracts as _K
from services.commercial import lp_bindings as _lp
from services.commercial import participants as P
from services.commercial.lp_bindings import same_party
from services.finance.case import (
    CONTRACT_CLASS, DEGRADATION_SOURCE, UPFRONT_FROM_CAPITAL_COST, AssetFinance, AssetPart,
    ExtraOwnerAsset, FinanceCase, FinanceRefused, LpBasis, StorageYear, Template, TemplateLine,
)
from services.finance.cashflow import esc_class_for

ANNUAL_TOL = 0.005                      # plan C3: within 0.5 % of a year
COMMODITY_TOL = 0.01                    # plan C13: the ledger cross-check, a cent …
COMMODITY_REL_TOL = 1e-9                # … or relative, for a large ledger (review round 1)
_EPS_MW = 1e-6
BUILD_YEAR_MIN, BUILD_YEAR_MAX = 1900, 2200     # a COD from build_year (S3): `currency_year`'s bounds

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


def _contract_link(c, owner_gens: set[str]) -> tuple[bool, str | None, list[str]]:
    """(generation-linked, the degrading owner generator or None, the owner
    generators it names). A single-asset pay-as-produced, sleeved or
    as-consumed PPA, a single-asset CfD and a per-MWh-only EaaS on one asset
    degrade with that asset (as-consumed: first order — the consumed volume
    falls with the generation; WP4.6a review B8)."""
    if c is None:
        return False, None, []
    t = getattr(c, "type", None)
    assets = list(getattr(c, "asset_ids", []) or [])
    on_owner = [a for a in assets if a in owner_gens]
    if not on_owner:
        return False, None, []
    single = assets[0] if len(assets) == 1 else None
    if t == "ppa":
        if c.kind == "baseload":
            return False, None, on_owner            # financial: a fixed MW, not the output
        if c.kind in ("pay_as_produced", "sleeved", "as_consumed_btm"):
            return True, single, on_owner           # volume = the asset's (consumed) output
        return True, None, on_owner
    if t == "cfd":
        return True, single, on_owner               # difference × generation
    if t == "eaas" and getattr(c, "fee_eur_per_mwh", None) is not None:
        # Per MWh delivered: degrades with one asset when there is no fixed fee
        # beside it on the same line.
        fixed = getattr(c, "fee_eur_per_year", None) is not None
        return True, (None if fixed else single), on_owner
    return False, None, on_owner


def _degrades(fin, assets) -> bool:
    """Whether any of `assets` degrades (a missing entry counts: unknown)."""
    for a in assets:
        spec = fin.degradation_by_asset.get(a)
        if spec is None:
            return True
        vals = spec if isinstance(spec, list) else [spec]
        if any(float(v) != 0.0 for v in vals):
            return True
    return False


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


def _could_involve(ln, owner: str, contracts: dict, owned_names: set[str]) -> bool:
    """A line with an unknown party: could the unknown party be the owner?
    A contract line — unless the contract is known, names none of the owner's
    assets and does not name the owner as a party; an asset line — when the
    asset is the owner's (or unnamed); any other line — yes (WP4.6a review B6)."""
    if ln.source == "contract":
        c = contracts.get(ln.contract_id)
        if c is None:
            return True
        if any(a in owned_names for a in (getattr(c, "asset_ids", None) or [])):
            return True
        try:
            parties = P._contract_parties(c)
        except AttributeError:
            return True
        return any(p is not None and same_party(p, owner) for _r, p in parties)
    if ln.source == "asset":
        return ln.asset is None or ln.asset in owned_names
    return True


def _owner_lines(ledger, inputs, owner: str, k: str, flags: list[str], *,
                 contracts: dict | None = None,
                 owned_names: set[str] | None = None) -> dict[str, _Acc]:
    """The owner's cash lines of period `k`, summed per key and signed from the
    owner. A line with an unknown party that could be the owner is a None
    line flagged `ledger_party_unknown:<key>` — never dropped (review B6)."""
    contracts = contracts or {}
    owned_names = owned_names or set()
    out: dict[str, _Acc] = {}
    for ln in ledger.periods.get(k, []):
        if ln.basis != "cash":
            continue                           # annuity / model_only: not converted (P3 pin)
        sign = _owner_sign(ln, owner)
        key = _line_key(ln, inputs)
        if sign is None:
            if (ln.payer is not None and ln.payee is not None) or \
                    not _could_involve(ln, owner, contracts, owned_names):
                continue                       # between other parties
            flags.append(f"ledger_party_unknown:{key}")
            v, other = None, (ln.payer or ln.payee or "unknown")
        else:
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


def _template_lines(accs: dict[str, _Acc], *, k: str, year: int, base_year: int, inputs, vf,
                    owner: str, owned: set[tuple[str, str]], owner_gens: set[str],
                    contracts: dict, fin, gen: dict[str, float | None],
                    site_gens: list[str], gen_links: list[str],
                    flags: list[str]) -> list[TemplateLine]:
    """The owner's template lines of one period. Money years (review B4):
    contract lines stay in the template's (the period year — P2 indexes their
    prices to it); every other line is in the BASE year (P2 does not escalate
    tariffs, connection fees, export prices or costs between periods)."""
    split = (inputs.export_split or {}).get(k) or {}
    owners = {(o.component, o.asset_id): o.owner for o in vf.asset_owners}
    lines: list[TemplateLine] = []
    for key, a in accs.items():
        base = dict(stream=a.stream, esc_class=a.esc_class, tariff_item=a.tariff_item,
                    counterparty=a.counterparty, source=a.source, source_id=a.source_id,
                    period=k)
        if a.source == "contract":
            c = contracts.get(a.contract_id)
            linked, asset, on_owner = _contract_link(c, owner_gens)
            amount = a.amount
            if linked and asset is None:
                # Several assets, or a mixed fee: which generation scales it is
                # not known — a None line, never an undegraded number (C12,
                # review B8), unless none of its generators degrades.
                flags.append(f"degradation_link_unknown:{key}")
                if _degrades(fin, on_owner):
                    amount = None
            pct = getattr(c, "indexation_pct_per_year", None) if c is not None else None
            stream_p2 = key.rsplit(":", 1)[-1]
            lines.append(TemplateLine(
                key=key, amount=amount, contract_id=a.contract_id, degrades_with=asset,
                indexation=None if pct is None else float(pct) / 100.0,
                tenor_years=getattr(c, "tenor_years", None) if c is not None else None,
                price=_indexed_price(c, year) if stream_p2 == "ppa_energy" else None,
                changes_dispatch=bool(getattr(c, "changes_dispatch", False)), **base))
            continue
        base["money_year"] = base_year
        sid = "export_price" if a.source == "export_price" else a.source_id
        # The split was established for this source (possibly with no part
        # of the owner's generation in it).
        parts = split.get(sid) if a.source in ("export_price", "bill") else None
        has_split = parts is not None
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
        if a.stream == "energy_export" and owner_gens and a.amount is not None:
            lines += _export_by_generation(key, a, base, fin=fin, gen=gen, site_gens=site_gens,
                                           gen_links=gen_links, owner_gens=owner_gens,
                                           flags=flags)
            continue
        degrades = None
        if a.source == "asset" and not _is_commodity(a) and a.stream in ("vom", "fuel"):
            comp = a.source_id.split(":")[1] if a.source_id.count(":") >= 2 else None
            if comp == "Generator" and a.asset in owner_gens:
                degrades = a.asset                  # Σ w·p·mc of the asset itself
        lines.append(TemplateLine(key=key, amount=a.amount, degrades_with=degrades, **base))
    return lines


def _export_by_generation(key: str, a: _Acc, base: dict, *, fin, gen, site_gens, gen_links,
                          owner_gens, flags) -> list[TemplateLine]:
    """An export line with no per-asset split (C5, review B8). No site
    generation in the period: storage export, nothing degrades. The owner owns
    ALL site generation (Generators only): the line is split by each
    generator's share of the period's generation, each part degrading with it
    — first order, stated (`export_degrades_by_generation_share:<key>`; export
    of stored energy is degraded with it). Otherwise (other parties'
    generation, a converting Link) a None line `degradation_link_unknown`,
    unless none of the owner's generators degrades."""
    site_owner = [g for g in site_gens if g in owner_gens]
    vals = [gen.get(g) for g in site_owner]
    if any(v is None for v in vals):
        flags.append(f"degradation_link_unknown:{key}")
        return [TemplateLine(key=key, amount=None, **base)]
    total = float(sum(vals))
    all_owned = set(site_gens) <= owner_gens and not gen_links
    if total <= 0.0 and all_owned:
        return [TemplateLine(key=key, amount=a.amount, **base)]
    if all_owned:
        flags.append(f"export_degrades_by_generation_share:{key}")
        return [TemplateLine(key=f"{key}:{g}", amount=a.amount * float(gen[g]) / total,
                             degrades_with=g, **base)
                for g in sorted(site_owner) if float(gen[g]) > 0.0]
    flags.append(f"degradation_link_unknown:{key}")
    amount = a.amount if not _degrades(fin, sorted(owner_gens)) else None
    return [TemplateLine(key=key, amount=amount, **base)]


# ── generation ───────────────────────────────────────────────────────────────

def _gen_frame(n, result_df) -> pd.DataFrame | None:
    p = result_df(n, "generators_t", "p") if result_df is not None else None
    if p is None:
        p = getattr(n.generators_t, "p", None)
    return p


def _generation(n, result_df, names, mask) -> dict[str, float | None]:
    """Σ w·max(p, 0) per generator over the period (MWh); None when the
    generator has no solved column or a NaN in it — never a silent 0."""
    p = _gen_frame(n, result_df)
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)[mask]
    out: dict[str, float | None] = {}
    for g in names:
        if p is None or g not in p.columns:
            out[g] = None
            continue
        v = p[g].reindex(n.snapshots).to_numpy(dtype=float)[mask]
        out[g] = None if np.isnan(v).any() else float((w * np.clip(v, 0.0, None)).sum())
    return out


def _site_generation(n, parsed, result_df, mask) -> float | None:
    """On-site electric generation of the period (MWh): the site's Generators
    and converting Links, the export split's definition; None when unknown."""
    gens = _lp.site_generators(n, parsed)
    vals = list(_generation(n, result_df, gens, mask).values())
    if any(v is None for v in vals):
        return None
    total = float(sum(vals))
    links = _lp.site_link_generation(n, parsed, lambda k: getattr(n.links_t, f"p{k}", None))
    if links is not None and not links.empty:
        w = n.snapshot_weightings.objective.to_numpy(dtype=float)[mask]
        arr = links.to_numpy(dtype=float)[mask]
        if np.isnan(arr).any():
            return None
        total += float((w[:, None] * np.clip(arr, 0.0, None)).sum())
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


def _conversion_draw(n, parsed, sides, owned) -> tuple[np.ndarray, list[str], list[str], bool]:
    """Site conversion load (review B2): the electric draw of site Links that
    take site electricity into a non-electric bus (a heat pump, an
    electrolyser) — Σ over their ports on site-side electric buses of p_k
    (PyPSA: + = withdrawn from the bus). Returns (the non-owner Links' draw MW
    per snapshot, those Links, the OWNER's such Links, whether a draw is
    unknown). Meter Links and converting (generating) Links are not load."""
    electric = _lp._electric_bus_test(n, parsed)
    meter = set(_lp.import_links(parsed)) | ({parsed.export_link} if parsed.export_link else set())
    generating = set(_lp.site_generating_links(n, parsed))
    draw = np.zeros(len(n.snapshots))
    names: list[str] = []
    owner: list[str] = []
    unknown = False
    for link in n.links.index:
        name = str(link)
        if name in meter or name in generating:
            continue
        ports = []
        for k in range(5):
            col = f"bus{k}"
            if col in n.links.columns:
                b = str(n.links.at[name, col]).strip()
                if b and b != "nan":
                    ports.append((k, b))
        e_ports = [k for k, b in ports if b in sides.site and electric(b)]
        if not e_ports or all(electric(b) for _k, b in ports):
            continue                              # no site electric port, or a plain branch
        if ("Link", name) in owned:
            owner.append(name)
            continue
        tot = np.zeros(len(n.snapshots))
        for k in e_ports:
            df = getattr(n.links_t, f"p{k}", None)
            if df is None or name not in getattr(df, "columns", []):
                unknown = True
                break
            tot += df[name].reindex(n.snapshots).to_numpy(dtype=float)
        else:
            if np.isnan(tot).any():
                unknown = True
                continue
            draw += tot
            names.append(name)
    return draw, sorted(names), sorted(owner), unknown


def _s_items(tariff) -> set[str]:
    """The C5 S items (review B7): billed per kWh on the IMPORT volume (an
    import or net meter, a cost) — energy, levies, certificates alike; demand,
    capacity and fixed charges are shaved by storage / the peak, not in
    proportion to the generation, so they do not degrade with it."""
    if tariff is None:
        return set()
    return {it.id for it in tariff.items
            if it.unit == "per_kwh" and it.measured_on in ("import", "net")
            and it.direction == "cost"}


def _monthly_items(tariff) -> dict[str, bool]:
    """Items billed per month on a peak (demand charges, ratchets; P3's
    `demand_charge` stream) → whether their periods are restricted to some
    months (then an annualised partial year cannot say what the other months
    bill)."""
    if tariff is None:
        return {}
    return {it.id: any(getattr(per, "months", None) for per in it.periods)
            for it in tariff.items
            if it.kind == "demand" or (it.measured_on == "peak_import" and it.kind != "capacity")}


def _months_present(n, parsed, mask) -> int:
    """Distinct billing months of the period's snapshots on the tariff clock
    (as `bill_site` / `rate` see them)."""
    ts = _times(n, mask)
    idx = ts.tz_localize("UTC") if parsed.timezone and ts.tz is None else ts
    local = idx.tz_convert(parsed.timezone) if (idx.tz is not None and parsed.timezone) else idx
    return len(set(local.strftime("%Y-%m")))


def _c5_pair(g: str, s: float | None, share: float | None, k: str, base_year: int,
             flags: list[str]) -> tuple[TemplateLine, TemplateLine]:
    """The first-order bill effect of `g`'s degradation (C5): +S·g degrading
    with it and −S·g not; in operating year k they net −S·g·(1 − f_k). Marked
    `DEGRADATION_SOURCE` (value, never the investment's own cost — review
    B1). A negative S (the generation RAISES the volume bill) is flagged."""
    v = None if (s is None or share is None) else s * share
    if s is not None and s < 0:
        flags.append(f"degradation_bill_value_negative:{k}")
    common = dict(stream="energy_import", esc_class="tariff", source=DEGRADATION_SOURCE,
                  source_id=g, period=k, money_year=base_year)
    return (TemplateLine(key=f"bill_degradation:{g}", amount=v, degrades_with=g, **common),
            TemplateLine(key=f"bill_degradation_base:{g}", amount=None if v is None else -v,
                         **common))


def _cf_none(key: str, flag: str, k: str, base_year: int) -> TemplateLine:
    return TemplateLine(key=key, stream="other", amount=None, esc_class="tariff",
                        source="counterfactual", source_id=flag, period=k, money_year=base_year)


def _asset_of(a: _Acc) -> tuple[str, str] | None:
    """(component, name) of an `asset:<stream>:<Component>:<name>` line."""
    parts = a.source_id.split(":", 2)
    return (parts[1], parts[2]) if len(parts) == 3 else None


def _counterfactual(n, cfg, parsed, sides, inputs, vf, ledger_accs: dict[str, dict[str, _Acc]],
                    templates_actual: dict[str, list[TemplateLine]], owned, contracts,
                    lost_load, base_year: int, flags: list[str]
                    ) -> tuple[dict[str, list[TemplateLine]], dict[str, float | None], str]:
    """Per period the counterfactual lines and S (avoided import value:
    counterfactual − actual per-kWh import items + commodity), for C5; and the
    counterfactual's hash (C13)."""
    from services.commercial import hashing as _H

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
    # Site conversion load (review B2): it exists without the investment; P4
    # does not re-dispatch, so its actual draw is the counterfactual's.
    draw, converters, owner_conv, draw_unknown = _conversion_draw(n, parsed, sides, owned)
    if converters:
        served = np.clip(served + draw, 0.0, None)
        flags.append(f"counterfactual_includes_conversion_load:{_num(float((w_all * draw).sum()))}")
        flags.append("counterfactual_conversion_load_links:" + ",".join(converters))
    others = _non_owner_site_assets(n, parsed, sides, owned)
    blockers = [f"counterfactual_not_established:{r}" for r in shed_reasons]
    if others:
        blockers.append("counterfactual_not_established:non_owner_site_assets")
        flags.append("counterfactual_removes_non_owner_assets:" + ",".join(others))
    if draw_unknown:
        blockers.append("counterfactual_not_established:conversion_load_unknown")
    electric = _lp._electric_bus_test(n, parsed)
    if owner_conv and any(str(n.loads.at[ld, "bus"]) in sides.site
                          and not electric(str(n.loads.at[ld, "bus"])) for ld in n.loads.index):
        # The owner's heat pump (say) serves a non-electric load: the site
        # without it has no stated source for that load.
        blockers.append("counterfactual_not_established:owner_conversion_load")
        flags.append("counterfactual_owner_conversion_links:" + ",".join(owner_conv))
    flags += blockers

    # The tariff bill on the served-load meter (C13; `rate_meter` = bill_site's rating).
    items = inputs.bill_items
    payees = P.resolve_tariff_payees(items, vf, inputs.retailer)
    s_items = _s_items(parsed.import_tariff)
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
    hash_commodity: dict[str, float | None] = {}
    hash_connection: dict[str, list] = {}
    for p in _periods(n):
        k = _key(p)
        m = _mask(n, p)
        lines: list[TemplateLine] = []
        for b in blockers:
            lines.append(_cf_none(f"counterfactual:{b.split(':', 1)[1]}", b, k, base_year))
        blocked = bool(blockers)
        if any(a.source == "allocation" for a in ledger_accs[k].values()):
            blocked = True
            flags.append("counterfactual_not_established:hub_allocation")
            lines.append(_cf_none("counterfactual:hub_allocation",
                                  "counterfactual_not_established:hub_allocation", k, base_year))
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
                    source="bill", source_id=item_id, period=k, money_year=base_year))
                # S counts the items billed per kWh on the import volume (plan
                # C5; review B7: levies and certificates too, by volume basis).
                if item_id in s_items:
                    a = (inputs.bill.get(k) or {}).get(item_id)
                    cf_import_bill = None if (cf_import_bill is None or v is None) \
                        else cf_import_bill + v
                    act_import_bill = None if (act_import_bill is None or a is None) \
                        else act_import_bill + a
        # commodity — keyed like the actual commodity line(s) (review B1), so
        # the engine nets it as a saving, never an asset cost.
        commodity = [(key, a) for key, a in ledger_accs[k].items() if _is_commodity(a)]
        cf_comm: float | None = 0.0
        act_comm: float | None = 0.0
        if commodity:
            ledger_comm = None if any(a.amount is None for _k, a in commodity) \
                else -sum(a.amount for _k, a in commodity)           # the site pays: > 0
            reason = why
            if reason is None and mc is None:
                reason = "no_priced_supply_generator"
            if reason is None:
                actual = float((w_all[m] * imp_actual[m] * mc[m]).sum())
                tol = max(COMMODITY_TOL, COMMODITY_REL_TOL * abs(ledger_comm or 0.0))
                if ledger_comm is None or abs(actual - ledger_comm) > tol:
                    reason = "ledger_cross_check"
            if reason is None:
                cf_comm = float((w_all[m] * served[m] * mc[m]).sum())
                act_comm = ledger_comm
            else:
                cf_comm = act_comm = None
                flags += ["counterfactual_commodity_not_established",
                          f"counterfactual_commodity_not_established:{reason}"]
            amounts = [a.amount for _k, a in commodity]
            tot = sum(amounts) if all(x is not None for x in amounts) else None
            for key, a in commodity:
                share = (a.amount / tot) if (tot not in (None, 0.0)) else 1.0 / len(commodity)
                lines.append(TemplateLine(
                    key=key, stream=a.stream, amount=None if cf_comm is None
                    else -cf_comm * share, esc_class=a.esc_class, counterparty=a.counterparty,
                    source="counterfactual", source_id="commodity", period=k,
                    money_year=base_year))
        elif supply and why is None and mc is not None and \
                float((w_all[m] * served[m] * np.abs(mc[m])).sum()) > 0.0:
            # The supply is priced but the actual side imports nothing: no
            # ledger commodity line to key or cross-check the counterfactual's.
            flags.append("counterfactual_commodity_omitted:no_actual_import")
        hash_commodity[k] = cf_comm
        # connection fees, the contracts that exist without the assets, and the
        # costs of assets outside the owner's (they exist without it too —
        # review B2); the commodity is the meter's, above.
        not_owned = {key for key, a in ledger_accs[k].items()
                     if a.source == "asset" and not _is_commodity(a)
                     and _asset_of(a) not in owned}
        for ln in templates_actual[k]:
            if ln.source == "connection":
                lines.append(ln)
                hash_connection.setdefault(k, []).append([ln.key, ln.amount])
            elif ln.source == "asset" and ln.key in not_owned:
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
        # A blocked counterfactual leaves S unknown too: its bill-item sum alone
        # would carry a number (even a negative one) into the C5 pair (C12;
        # WP4.6a review round 2).
        avoided[k] = None if blocked or None in (cf_import_bill, act_import_bill, cf_comm,
                                                 act_comm) else \
            (cf_import_bill + cf_comm) - (act_import_bill + act_comm)
    # C13: what the counterfactual was built from.
    blob = {
        "tariff": None if parsed.import_tariff is None else _H.digest(parsed.import_tariff),
        "served_load": hashlib.sha256(np.ascontiguousarray(np.round(served, 9)).tobytes())
        .hexdigest(),
        "connection": {"agreement": None if parsed.connection is None
                       else _H.digest(parsed.connection), "lines": hash_connection},
        "commodity": {"supply": supply, "why": why,
                      "mc": None if mc is None else hashlib.sha256(
                          np.ascontiguousarray(np.round(mc, 9)).tobytes()).hexdigest(),
                      "amount": hash_commodity},
    }
    cf_hash = hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()) \
        .hexdigest()[:16]
    return out, avoided, cf_hash


# ── assets, dates ────────────────────────────────────────────────────────────

def _capacity(df, name: str, col: str) -> float | None:
    opt = _fin(df.at[name, f"{col}_opt"]) if f"{col}_opt" in df.columns else None
    return opt if opt is not None else _fin(df.at[name, col]) if col in df.columns else None


def _parts(n, comp: str, name: str, df, cap: float | None, *, discount_rate: float | None
           ) -> tuple[tuple[AssetPart, ...], list[str]]:
    """The asset's investment parts (IC S0b plan S1, S2), each part's cost = its
    upfront per unit × the capacity (None without one), and the flags. Capex
    comes only from `asset_schema.access.upfront_parts`, except a typed
    `overnight_cost` of exactly 0 on a single-part asset, read first: an
    established part of 0 (the accessor would fall through to `capital_cost`).
    A part back-calculated from `capital_cost` is NOT established — it can
    include fixed O&M (IC rule C12, owner decision 2026-10-06): no parts,
    flagged `upfront_only_from_capital_cost:<a>`. A non-finite upfront cost is
    not established (the `_fin` rule); an infinite lifetime stays `math.inf`."""
    if comp not in _schema.CLASS_ATTR:
        return (), []
    row = df.loc[name]
    life = number(row.get("lifetime"))
    if _fin(row.get("overnight_cost")) == 0.0 and not has_parts(comp, row):
        return (AssetPart("investment", 0.0, life, None),), []
    got = upfront_parts(n, comp, name, discount_rate=discount_rate)
    if got is None:
        return (), []
    if any(p.derived_from_capital_cost for p in got):
        return (), [f"{UPFRONT_FROM_CAPITAL_COST}:{name}"]
    return tuple(AssetPart(p.name, None if (cap is None or not math.isfinite(p.upfront_per_unit))
                           else p.upfront_per_unit * cap, p.lifetime, p.fom_share)
                 for p in got), []


def _assets(n, owned_list, *, discount_rate: float | None = None
            ) -> tuple[tuple[AssetFinance, ...], dict[str, float | None], list[str]]:
    """The owner's `AssetFinance`s with their parts (S1, S2), their own
    discount rates, and the flags (`upfront_only_from_capital_cost:<a>`).
    `discount_rate` is the config's: passed so a back-calculation from
    `capital_cost` is detected and flagged rather than read as None."""
    assets, rates, flags = [], {}, []
    for comp, name in owned_list:
        df = getattr(n, _frame_of(comp) or "", None)
        if df is None or name not in df.index:
            raise FinanceRefused("owner_asset_missing", f"{comp} {name!r} is not in the network")
        cap = _capacity(df, name, _CAPACITY_COLS[comp])
        parts, f = _parts(n, comp, name, df, cap, discount_rate=discount_rate)
        flags += f
        costs = [p.overnight_cost for p in parts]
        overnight = None if (not parts or any(c is None for c in costs)) else float(sum(costs))
        life = _fin(df.at[name, "lifetime"]) if "lifetime" in df.columns else None
        carrier = str(df.at[name, "carrier"]) if "carrier" in df.columns else None
        assets.append(AssetFinance(name=name, component=comp, overnight_cost=overnight,
                                   lifetime_years=life, carrier=carrier or None, parts=parts))
        rates[name] = _fin(df.at[name, "discount_rate"]) if "discount_rate" in df.columns \
            else None
    return tuple(assets), rates, flags


def _build_year(n, comp: str, name: str) -> int | None:
    """An asset's `build_year` as a year, or None when absent (S3, review B2):
    PyPSA's default 0, NaN, a non-integer value or one outside 1900..2200 (the
    bounds of `currency_year`; review r1 note 1 — `date()` refuses above 9999)
    is absent."""
    df = getattr(n, _frame_of(comp) or "", None)
    if df is None or name not in df.index or "build_year" not in df.columns:
        return None
    by = _fin(df.at[name, "build_year"])
    if by is None or not float(by).is_integer() or not BUILD_YEAR_MIN <= by <= BUILD_YEAR_MAX:
        return None
    return int(by)


def _cod(n, fin, owned_list) -> tuple[date, list[str]]:
    """The case's COD and its flags (S3): each owner asset's `cod_by_asset`
    entry, else 1 January of its `build_year` (flagged
    `cod_from_build_year:<a>`), else `cod_missing`; one COD for all assets
    (`cod_mismatch` otherwise — staged builds are P5)."""
    dates: dict[str, date] = {}
    flags: list[str] = []
    missing = []
    for comp, name in owned_list:
        if name in fin.cod_by_asset:
            dates[name] = fin.cod_by_asset[name]
            continue
        by = _build_year(n, comp, name)
        if by is None:
            missing.append(name)
            continue
        dates[name] = date(by, 1, 1)
        flags.append(f"cod_from_build_year:{name}")
    if missing:
        raise FinanceRefused("cod_missing", f"cod_by_asset names no date for {missing} and they "
                             "have no build_year")
    distinct = set(dates.values())
    if len(distinct) > 1:
        raise FinanceRefused("cod_mismatch",
                             f"the owner's assets have different CODs {sorted(distinct)} (P5)")
    return next(iter(distinct)), flags


def _scale(lines, f: float, monthly: dict[str, float | None] | None = None
           ) -> tuple[TemplateLine, ...]:
    """Annualise (C3): every line × f, except a monthly-billed item's lines
    (`monthly`: item → 12 / the months present, or None when that cannot be
    stated) — review B5. Also on a template that represents a year (f == 1)
    with fewer than 12 billing months (U1 follow-up g)."""
    if f == 1.0 and not monthly:
        return tuple(lines)
    monthly = monthly or {}
    out = []
    for ln in lines:
        fx = monthly[ln.tariff_item] if ln.tariff_item in monthly else f
        amount = None if (ln.amount is None or fx is None) else ln.amount * fx
        out.append(dataclasses.replace(ln, amount=amount))
    return tuple(out)


# ── meter Links (GS Q5) ──────────────────────────────────────────────────────

def _uncosted_meter_links(n, parsed, owned_list) -> list[str]:
    """The owner's PoC meter Links (the import members and the export Link)
    with no typed `overnight_cost` and no LP-sized cost: under `single_owner`
    they are the owner's (who carries the connection is a commercial fact),
    but they are not part of the investment — their money is the connection
    lines. A meter Link with a typed cost (a new connection built in the
    case), or an extendable one with a `capital_cost` (a connection the LP
    sized — review B2), stays an asset: the latter reads
    `overnight_cost_missing` (C12), never a silently dropped capex."""
    meter = set(_lp.import_links(parsed)) | ({parsed.export_link} if parsed.export_link
                                             else set())
    out = []
    for comp, name in owned_list:
        if comp != "Link" or name not in meter or name not in n.links.index:
            continue
        typed = _fin(n.links.at[name, "overnight_cost"]) if "overnight_cost" in n.links.columns \
            else None
        ext = bool(n.links.at[name, "p_nom_extendable"]) \
            if "p_nom_extendable" in n.links.columns else False
        cc = _fin(n.links.at[name, "capital_cost"]) if "capital_cost" in n.links.columns else None
        if typed is None and (not ext or not cc):
            out.append(name)
    return sorted(out)


# ── storage throughput and charging cost (the LCOS; owner decision 6) ──────────

_STORAGE_FRAMES = {"StorageUnit": ("storage_units", "storage_units_t"),
                   "Store": ("stores", "stores_t")}


def _frame_col(n, result_df, frame_t: str, attr: str, name: str) -> np.ndarray | None:
    df = result_df(n, frame_t, attr) if result_df is not None else None
    if df is None:
        df = getattr(getattr(n, frame_t), attr, None)
    if df is None or name not in getattr(df, "columns", []):
        return None
    out = df[name].reindex(n.snapshots).to_numpy(dtype=float)
    return None if np.isnan(out).any() else out


def _charge_discharge(n, result_df, comp: str, name: str
                      ) -> tuple[np.ndarray | None, np.ndarray | None]:
    """(charge MW ≥ 0, discharge MW ≥ 0) at the asset's bus per snapshot: a
    StorageUnit's `p_store` / `p_dispatch` (else its net `p` split by sign),
    a Store's `p` split by sign; None when not solved."""
    _frame, frame_t = _STORAGE_FRAMES[comp]
    if comp == "StorageUnit":
        ch = _frame_col(n, result_df, frame_t, "p_store", name)
        di = _frame_col(n, result_df, frame_t, "p_dispatch", name)
        if ch is not None and di is not None:
            return np.clip(ch, 0.0, None), np.clip(di, 0.0, None)
    p = _frame_col(n, result_df, frame_t, "p", name)
    if p is None:
        return None, None
    return np.clip(-p, 0.0, None), np.clip(p, 0.0, None)


def _committed_price(n, link: str | None, solved: dict) -> np.ndarray | None:
    """The per-interval €/MWh the solve committed on a meter Link
    (`links_t["ic_energy_price"]`): 0 for a Link the solve deliberately left
    unpriced; None for a priced Link whose record is gone (as
    `energy_cost_rows`)."""
    prices = _lp._frame(n, _lp.ENERGY_PRICE_ATTR)
    if link in prices.columns:
        out = prices[link].reindex(n.snapshots).to_numpy(dtype=float)
        return None if np.isnan(out).any() else out
    if link in n.links.index and link not in (solved.get("priced") or []):
        return np.zeros(len(n.snapshots))
    return None


def _storage_years(n, parsed, sides, owned_list, result_df, accs: dict[str, dict[str, _Acc]],
                   flags: list[str], factors: dict[str, float] | None = None
                   ) -> dict[str, dict[str, StorageYear]]:
    """Per period and owner storage asset on a site-side electric bus: the
    year's discharge and charge and what the charged energy cost the site in
    the dispatch (`StorageYear`).

    Per interval the site's import I (Σ max(p0, 0) over the import members)
    covers the charge of all site-side storage C first: the grid share
    g = min(1, I / C). The grid part is priced at the interval's import price —
    the flow-weighted committed tariff price on the members plus the grid
    supply's price (the commodity the site pays; 0 with no priced supply) —
    escalating as `tariff`. The rest of the charge came from on-site surplus
    (PV): priced at the export revenue it would otherwise have earned (−the
    committed net price on the export Link: export price − export tariff
    items, floored at 0 per interval — at a negative net price the site would
    curtail, forgoing nothing: `lcos_surplus_price_floored:<asset>`, review
    B1), escalating as `export`; with no export Link the surplus would have
    been curtailed: 0, flagged. The surplus MWh in the flags is annualised
    (× the template factor). A price that is not per interval (convex
    energy tiers, a group net-import term), not committed, or a supply price
    that is not one series makes that part None, flagged
    `lcos_charge_price_not_established:<part>:<asset>` (plan C12)."""
    factors = factors or {}
    electric = _lp._electric_bus_test(n, parsed)
    storage = []          # every site-side electric storage (owner or not)
    for comp, (frame, _t) in _STORAGE_FRAMES.items():
        df = getattr(n, frame)
        for name in df.index:
            bus = str(df.at[name, "bus"])
            if bus in sides.site and electric(bus):
                storage.append((comp, str(name)))
    owner_storage = [(c, a) for c, a in owned_list if c in _STORAGE_FRAMES]
    out: dict[str, dict[str, StorageYear]] = {_key(p): {} for p in _periods(n)}
    if not owner_storage:
        return out
    for c, a in owner_storage:
        if (c, a) not in storage:
            flags.append(f"lcos_not_applicable:{a}")            # not on a site electric bus
    flows = {sa: _charge_discharge(n, result_df, *sa) for sa in storage}
    total_charge = np.zeros(len(n.snapshots))
    charge_known = True
    for (ch, _di) in flows.values():
        if ch is None:
            charge_known = False
        else:
            total_charge += ch
    solved = n.meta.get(_lp.META_LINKS) or {}
    p0 = n.links_t.p0
    members = [m for m in _lp.import_links(parsed) if m in n.links.index]
    imp = np.zeros(len(n.snapshots))
    weighted = np.zeros(len(n.snapshots))
    imp_reason = None
    if not solved:
        imp_reason = "import_not_committed"
    elif n.meta.get(_lp.META_TIERS) or n.meta.get(_lp.META_GROUP_NET):
        imp_reason = "import_not_per_interval"
    for mlink in members:
        if mlink not in p0.columns:
            imp_reason = imp_reason or "import_not_solved"
            continue
        f_l = np.clip(p0[mlink].reindex(n.snapshots).to_numpy(dtype=float), 0.0, None)
        price = _committed_price(n, mlink, solved)
        if price is None:
            imp_reason = imp_reason or "import_not_committed"
            price = np.zeros(len(n.snapshots))
        imp += f_l
        weighted += f_l * price
    import_price = np.divide(weighted, imp, out=np.zeros_like(imp), where=imp > 0)
    supply, why = _supply(n, parsed, sides)
    if supply and why is None:
        g0 = supply[0]
        import_price = import_price + _series(n, "generators_t", "marginal_cost", g0,
                                              n.generators.at[g0, "marginal_cost"])
    elif supply:
        imp_reason = imp_reason or f"supply_price:{why}"
    surplus_price: np.ndarray | None
    if parsed.export_link is None or parsed.export_link not in n.links.index:
        surplus_price = np.zeros(len(n.snapshots))
        no_export = True
    else:
        no_export = False
        net = _committed_price(n, parsed.export_link, solved) if solved else None
        surplus_price = None if net is None else -net
    # A negative net export price (a negative market price, or export tariff
    # items with no price): the site would curtail rather than export, so the
    # surplus forgoes no revenue — floored at 0, never a negative charging
    # cost (review B1). A negative IMPORT price is real money and stays.
    floored = np.zeros(len(n.snapshots), dtype=bool) if surplus_price is None \
        else surplus_price < 0.0
    if floored.any():
        surplus_price = np.clip(surplus_price, 0.0, None)
    share = np.divide(np.minimum(imp, total_charge), total_charge,
                      out=np.zeros_like(total_charge), where=total_charge > 1e-12)
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    for comp, name in owner_storage:
        if (comp, name) not in storage:
            continue
        ch, di = flows[(comp, name)]
        for p in _periods(n):
            k = _key(p)
            m = _mask(n, p)
            om = tuple(sorted(key for key, acc in accs.get(k, {}).items()
                              if acc.source == "asset" and acc.stream in ("fom", "vom")
                              and _asset_of(acc) == (comp, name)))
            if ch is None or di is None or not charge_known:
                flags.append(f"lcos_dispatch_not_established:{name}")
                out[k][name] = StorageYear(0.0, 0.0, None, None, om_keys=om)
                continue
            w = w_all[m]
            grid = w * ch[m] * share[m]
            sur = w * ch[m] * (1.0 - share[m])
            imp_cost: float | None = float((grid * import_price[m]).sum())
            if imp_reason is not None and grid.sum() > 1e-9:
                imp_cost = None
                flags.append(f"lcos_charge_price_not_established:import:{name}")
                flags.append(f"lcos_charge_price_not_established:{imp_reason}")
            sur_mwh = float(sur.sum())
            if surplus_price is None:
                sur_cost = None if sur_mwh > 1e-9 else 0.0
                if sur_cost is None:
                    flags.append(f"lcos_charge_price_not_established:surplus:{name}")
            else:
                sur_cost = float((sur * surplus_price[m]).sum())
                if float(sur[floored[m]].sum()) > 1e-9:
                    flags.append(f"lcos_surplus_price_floored:{name}")
            if sur_mwh > 1e-9:
                flags.append(("lcos_charge_from_surplus_unpriced:" if no_export else
                              "lcos_charge_from_surplus_at_export_price:")
                             + f"{name}:{_num(sur_mwh * factors.get(k, 1.0))}")
            out[k][name] = StorageYear(
                discharge_mwh=float((w * di[m]).sum()), charge_mwh=float((w * ch[m]).sum()),
                charge_import_cost=imp_cost, charge_surplus_cost=sur_cost, om_keys=om)
    return out


def _scale_storage(years: dict[str, StorageYear], f: float, base_year: int
                   ) -> dict[str, StorageYear]:
    def x(v):
        return None if v is None else v * f
    return {a: dataclasses.replace(sy, discharge_mwh=sy.discharge_mwh * f,
                                   charge_mwh=sy.charge_mwh * f,
                                   charge_import_cost=x(sy.charge_import_cost),
                                   charge_surplus_cost=x(sy.charge_surplus_cost),
                                   money_year=base_year)
            for a, sy in years.items()}


# ── extra (campus) assets, IC G2 ─────────────────────────────────────────────

def _extra_assets(extras: tuple[ExtraOwnerAsset, ...], owned_list, invest_list
                  ) -> tuple[tuple[AssetFinance, ...], list[str]]:
    """Each extra asset's `AssetFinance` (plan G-5: `ExtraOwnerAsset.asset_finance`)
    and the flags; refused (G-9) when a name is an owner-owned network component
    (`extra_asset_duplicates_network_asset:<name>`), repeats among the extras
    (`extra_asset_duplicate:<name>`), or a part's upfront cost was back-calculated
    from a `capital_cost` (`extra_asset_derived_upfront:<name>`: not established by
    S1, a caller error here). A transformer or cable beside an owned network
    Transformer or Line is flagged `extra_asset_may_double_count:<name>` (the names
    differ; the overlap is a judgement)."""
    owned_names = {name for _c, name in owned_list}
    seen: set[str] = set()
    out, flags = [], []
    network_lines = any(c in ("Transformer", "Line") for c, _a in invest_list)
    for e in extras:
        if e.name in owned_names:
            raise FinanceRefused(f"extra_asset_duplicates_network_asset:{e.name}",
                                 f"{e.name!r} is also an owner-owned network component")
        if e.name in seen:
            raise FinanceRefused(f"extra_asset_duplicate:{e.name}",
                                 f"{e.name!r} is passed twice as an extra asset")
        seen.add(e.name)
        if any(p.derived_from_capital_cost for p in e.parts):
            raise FinanceRefused(f"extra_asset_derived_upfront:{e.name}",
                                 "an upfront cost back-calculated from capital_cost is not "
                                 "established (it can include fixed O&M); pass the typed cost")
        if e.kind in ("transformer", "cable") and network_lines:
            flags.append(f"extra_asset_may_double_count:{e.name}")
        out.append(e.asset_finance())
    return tuple(out), flags


def _extra_cod_flags(fin, extras: tuple[ExtraOwnerAsset, ...], cod: date) -> list[str]:
    """G-7: an extra asset is taken at the case COD (from the network owner assets,
    `_cod`). A typed `cod_by_asset` entry must equal it (`cod_mismatch`); without one
    its `build_year` is compared BY YEAR: the COD's year is accepted at the case COD
    (`cod_from_build_year:<name>`), an earlier year is `cod_mismatch`, a later one
    `extra_asset_staged_build:<name>` (P5) — never moved."""
    flags = []
    for e in extras:
        if e.name in fin.cod_by_asset:
            if fin.cod_by_asset[e.name] != cod:
                raise FinanceRefused("cod_mismatch", f"cod_by_asset[{e.name!r}] "
                                     f"{fin.cod_by_asset[e.name]} differs from the case COD {cod}")
            continue
        if e.build_year < cod.year:
            raise FinanceRefused("cod_mismatch", f"{e.name!r} is built in {e.build_year}, "
                                 f"before the case COD {cod}")
        if e.build_year > cod.year:
            raise FinanceRefused(f"extra_asset_staged_build:{e.name}",
                                 f"{e.name!r} is built in {e.build_year}, after the case COD "
                                 f"{cod} (staged builds are P5)")
        flags.append(f"cod_from_build_year:{e.name}")
    return flags


def _extra_fom_lines(extra_finance: tuple[AssetFinance, ...], k: str, base_year: int
                     ) -> tuple[TemplateLine, ...]:
    """G-6: an extra asset has no LP cost row, so its fixed O&M (Σ fom_share ×
    overnight a year, base-year money, escalated by `opex`) is one template line
    per asset and period — `stream="fom"`, the closed `CashflowLine` stream set."""
    return tuple(TemplateLine(key=f"extra_asset_fom:{a.name}", stream="fom",
                              amount=-sum(p.fom_share * p.overnight_cost for p in a.parts),
                              esc_class="opex", source="extra_asset_fom", source_id=a.name,
                              period=k, money_year=base_year)
                 for a in extra_finance)


# ── the entry point ──────────────────────────────────────────────────────────

def build_finance_case(n, cfg, fin, *, result_df, lost_load=None,
                       owner: str | None = None,
                       extra_assets: Sequence[ExtraOwnerAsset] = ()) -> FinanceCase:
    """The owner's `FinanceCase` from the solved network `n`, its solver
    config `cfg` and the stored `FinanceInputs` `fin` (see the module
    docstring); `FinanceRefused(code)` when it cannot be stated.
    `extra_assets` (IC G2): equipment the owner buys outside the network
    (a campus study's choice), added as owner capex — see `_extra_assets`."""
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
    # GS Q5: the owner's uncosted meter Links are not investments (no capex,
    # no COD), disclosed — never an `overnight_cost_missing` on the case.
    meter_skip = _uncosted_meter_links(n, parsed, owned_list)
    flags += [f"meter_link_not_investment:{m}" for m in meter_skip]
    invest_list = [(c, a) for c, a in owned_list if not (c == "Link" and a in meter_skip)]
    extras = tuple(extra_assets)
    extra_finance, extra_flags = _extra_assets(extras, owned_list, invest_list)

    # Staged builds are P5 (plan C2).
    if periods[0] is not None:
        first = periods[0]
        for comp, name in invest_list:
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
    owned_names = {name for _c, name in owned_list}
    base_year = info[_key(periods[0])][0]
    site_gens = _lp.site_generators(n, parsed)
    gen_links = _lp.site_generating_links(n, parsed)
    gens: dict[str, dict[str, float | None]] = {}
    site_total: dict[str, float | None] = {}
    for p in periods:
        m = _mask(n, p)
        gens[_key(p)] = _generation(n, result_df, sorted(owner_gens), m)
        site_total[_key(p)] = _site_generation(n, parsed, result_df, m)
    accs: dict[str, dict[str, _Acc]] = {}
    actual: dict[str, list[TemplateLine]] = {}
    for p in periods:
        k = _key(p)
        accs[k] = _owner_lines(ledger, inputs, owner, k, flags, contracts=contracts,
                               owned_names=owned_names)
        actual[k] = _template_lines(accs[k], k=k, year=info[k][0], base_year=base_year,
                                    inputs=inputs, vf=vf, owner=owner, owned=owned,
                                    owner_gens=owner_gens, contracts=contracts, fin=fin,
                                    gen=gens[k], site_gens=site_gens, gen_links=gen_links,
                                    flags=flags)

    # C13: the counterfactual, when the owner is the site party that pays the bill.
    cf_lines: dict[str, list[TemplateLine]] = {}
    avoided: dict[str, float | None] = {}
    cf_hash = None
    if same_party(owner, inputs.site_party):
        sides = P.classify_buses(n, parsed)
        cf_lines, avoided, cf_hash = _counterfactual(n, cfg, parsed, sides, inputs, vf, accs,
                                                     actual, owned, contracts, lost_load,
                                                     base_year, flags)

    # The storage LCOS's throughput and charging cost (owner decision 6).
    storage_years = _storage_years(n, parsed, P.classify_buses(n, parsed), owned_list,
                                   result_df, accs, flags, factors) \
        if any(c in _STORAGE_FRAMES for c, _a in owned_list) else {}

    # P3's blocking input flags make the money of every period unknown (P3:
    # "the blocking ones make every period None"; review B3).
    blocking = P._blocking(inputs.input_flags)
    monthly_items = _monthly_items(parsed.import_tariff)

    # C5: degradation's first-order bill effect, per owner generator.
    templates, counterfactual = [], []
    for p in periods:
        k = _key(p)
        year, _hours = info[k]
        gen = gens[k]
        lines = list(actual[k])
        for b in blocking:
            lines.append(TemplateLine(key=f"ledger_input_not_established:{b}", stream="other",
                                      amount=None, esc_class="opex", source="ledger_input",
                                      source_id=b, period=k, money_year=base_year))
        for g in sorted(owner_gens):
            if gen[g] is None:
                flags.append(f"generation_not_established:{g}")
                lines.append(TemplateLine(key=f"generation_not_established:{g}", stream="other",
                                          amount=None, esc_class="opex", source="generation",
                                          source_id=g, period=k, money_year=base_year))
        if k in avoided:
            st = site_total[k]
            for g in sorted(owner_gens):
                if g not in fin.degradation_by_asset or gen[g] is None or gen[g] <= 0 or \
                        (st is not None and st <= 0):
                    continue
                share = None if st is None else gen[g] / st
                lines += _c5_pair(g, avoided[k], share, k, base_year, flags)
                flags += ["degradation_bill_first_order", "degradation_bill_volume_items_only"]
        f = factors[k]
        monthly: dict[str, float | None] = {}
        months = _months_present(n, parsed, _mask(n, p)) if monthly_items else 12
        if monthly_items and (f != 1.0 or months < 12):
            # A monthly item is billed for the months present only. A template
            # that represents a year (f == 1, a week weighted to 8,760 h) with
            # fewer than 12 billing months would book that month's charge as
            # the year's: None unless `annualise` (U1 follow-up g, owner rule).
            for item, restricted in monthly_items.items():
                if f == 1.0 and not fin.annualise:
                    monthly[item] = None
                    flags.append(f"monthly_item_months_missing:{item}:{months}")
                elif restricted or months <= 0:
                    monthly[item] = None
                    flags.append(f"annualise_monthly_item_not_established:{item}")
                else:
                    monthly[item] = 12.0 / months
                    flags.append(f"template_annualised_monthly:{item}:{_num(12.0 / months)}")
        # G-6: the extra assets' fixed O&M, after `_scale` (never annualised).
        templates.append(Template(first_year=year,
                                  lines=_scale(lines, f, monthly) + _extra_fom_lines(
                                      extra_finance, k, base_year),
                                  energy_mwh={g: e * f for g, e in gen.items() if e is not None},
                                  money_year=year,
                                  storage=_scale_storage(storage_years.get(k, {}), f, base_year)))
        if k in cf_lines:
            counterfactual.append(Template(first_year=year,
                                           lines=_scale(cf_lines[k], f, monthly),
                                           money_year=year))

    if conservation.ok is False:
        flags.append("ledger_conservation_failed")
    elif conservation.ok is None:
        flags.append("ledger_conservation_not_established")
    flags += list(ledger.flags) + list(conservation.flags)

    lp_rate = _fin(getattr(cfg, "discount_rate", None))
    assets, rates, asset_flags = _assets(n, invest_list, discount_rate=lp_rate)
    cod, cod_flags = _cod(n, fin, invest_list)
    flags += asset_flags + cod_flags + extra_flags + _extra_cod_flags(fin, extras, cod)
    lp = LpBasis(discount_rate=lp_rate,
                 inflation_rate=_fin(getattr(cfg, "inflation_rate", None)),
                 auto_discount_periods=bool(getattr(cfg, "auto_discount_periods", False)),
                 asset_discount_rates=rates)
    return FinanceCase(inputs=fin, owner=owner, base_year=base_year, cod=cod,
                       templates=tuple(templates), assets=assets + extra_finance,
                       flags=tuple(dict.fromkeys(flags)),
                       counterfactual=tuple(counterfactual), lp_basis=lp,
                       counterfactual_hash=cf_hash, conservation_ok=conservation.ok,
                       extra_assets=extras)


# ── the hash ─────────────────────────────────────────────────────────────────

def _canon(o):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        out = {f.name: _canon(getattr(o, f.name)) for f in dataclasses.fields(o)}
        # IC G2 plan G-5: exactly `FinanceCase.extra_assets == ()` is omitted, so
        # a case without extras keeps its S0b hash (no generic empty-tuple rule).
        if isinstance(o, FinanceCase) and o.extra_assets == ():
            del out["extra_assets"]
        return out
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
