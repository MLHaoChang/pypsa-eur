"""
Resolve a jurisdiction pack and the case inputs into tax layers (IC P4 plan
WP4.3a; C7, C11, C12).

`resolve_tax_layers(pack, fin, assets, cod)` returns the layers, the flags and
the list of what is MISSING — a missing case input or pack rule makes the tax
section not established (the caller reports it); nothing is assumed.

Depreciation class strings (per asset, `FinanceInputs.depreciation_class_by_
asset`, or the pack's default for the carrier): `macrs_<n>` (the pack's Table
A-1), `sl_<n>` (straight-line, half-year convention), `afa_<n>` (straight-line
pro rata by month from COD, §7 Abs. 1 EStG), `db_<rate>_<n>` (declining
balance at `rate`, switching to straight-line).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from models.finance import FinanceInputs
from services.finance.packs.base import JurisdictionPack
from services.finance.tax import (
    DepreciationClass, InterestCap, LossRule, TaxLayer, declining_balance, sl_half_year,
    sl_pro_rata,
)


@dataclass(frozen=True)
class OwnerAsset:
    name: str
    carrier: str
    basis: float            # depreciable basis (installed cost incl. contingency, + IDC)


@dataclass
class ResolvedTax:
    layers: tuple[TaxLayer, ...] = ()
    flags: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)


def _sched_dict(v) -> float | dict[int, float]:
    return {int(k): float(x) for k, x in v.items()} if isinstance(v, dict) else float(v)


def schedule_for(cls: str, pack: JurisdictionPack, cod: date) -> tuple[float, ...]:
    kind, _, rest = cls.partition("_")
    if kind == "macrs":
        table = pack.rule("macrs_half_year_percent")
        if table.status != "ok" or rest not in table.value:
            raise KeyError(f"depreciation class {cls!r}: no MACRS table in pack "
                           f"{pack.jurisdiction!r}")
        return tuple(p / 100.0 for p in table.value[rest])
    if kind == "sl":
        return sl_half_year(int(rest))
    if kind == "afa":
        return sl_pro_rata(int(rest), months_first_year=13 - cod.month)
    if kind == "db":
        rate, _, years = rest.partition("_")
        return declining_balance(float(rate), int(years))
    raise KeyError(f"unknown depreciation class {cls!r}")


def _default_class(pack: JurisdictionPack, fin: FinanceInputs, carrier: str) -> str | None:
    if pack.jurisdiction != "eu_de":
        return None                 # US: the 2025 act changed energy classes — the case states it
    lives = pack.rule("afa_useful_life_years")
    if lives.status != "ok" or carrier not in lives.value:
        return None
    n = int(lives.value[carrier])
    deg = pack.rule("degressive_afa")
    acq = fin.acquisition_date
    if deg.status == "ok" and acq is not None and \
            date.fromisoformat(deg.value["acquired_from"]) <= acq <= date.fromisoformat(
                deg.value["acquired_to"]):
        rate = min(deg.value["multiple_of_sl"] / n, deg.value["max_rate"])
        return f"db_{rate:g}_{n}"
    return f"afa_{n}"


def _classes(pack, fin, assets, cod, res: ResolvedTax, *, bonus_macrs: float = 0.0):
    total = sum(a.basis for a in assets)
    out = []
    for a in assets:
        cls = fin.depreciation_class_by_asset.get(a.name) or _default_class(pack, fin, a.carrier)
        if cls is None:
            res.missing.append(f"depreciation_class:{a.name}")
            continue
        try:
            sched = schedule_for(cls, pack, cod)
        except (KeyError, ValueError) as e:
            res.missing.append(f"depreciation_class:{a.name}:{e}")
            continue
        share = a.basis / total if total else 0.0
        bonus = bonus_macrs if cls.startswith("macrs_") else 0.0
        out.append(DepreciationClass(name=f"{a.name}:{cls}", share=share, schedule=sched,
                                     bonus=bonus))
    return tuple(out)


def _us_bonus(pack, fin, cod, res: ResolvedTax) -> float:
    rule = pack.rule("bonus_depreciation")
    if rule.status != "ok":
        res.missing.append("pack_rule:bonus_depreciation")
        return 0.0
    if fin.acquisition_date is None:
        res.missing.append("acquisition_date")
        return 0.0
    if fin.acquisition_date > date.fromisoformat(rule.value["acquired_after"]):
        return float(rule.value["share"])
    by_year = rule.value["earlier_by_placed_in_service_year"]
    return float(by_year.get(str(cod.year), 0.0))


def resolve_tax_layers(pack: JurisdictionPack, fin: FinanceInputs, assets: list[OwnerAsset],
                       cod: date) -> ResolvedTax:
    res = ResolvedTax()
    if fin.tax_losses is None:
        res.missing.append("tax_losses")
    carry = fin.tax_losses == "carryforward"

    def need(name: str):
        r = pack.rule(name)
        if r.status != "ok":
            res.missing.append(f"pack_rule:{name}")
            return None
        res.sources[name] = r.source
        return r.value

    if pack.jurisdiction == "us_federal":
        rate, nol, cap = need("corporate_rate"), need("nol"), need("interest_limit")
        bonus = _us_bonus(pack, fin, cod, res)
        fed_classes = _classes(pack, fin, assets, cod, res, bonus_macrs=bonus)
        cap_obj = None
        if carry and cap is not None:
            if fin.small_business_163j is None:
                res.missing.append("small_business_163j")
            elif not fin.small_business_163j:
                cap_obj = InterestCap(share=cap["share"], carryforward=cap["carryforward"])
        layers = []
        if fin.state_rate is None:
            res.missing.append("state_rate")
        elif fin.state_rate > 0:
            # State packs are slots: the state layer decouples from bonus (as
            # most states do) and, in carryforward mode, carries losses without
            # a limit — both stated.
            res.flags.append("state_bonus_decoupled")
            if carry:
                res.flags.append("state_loss_rule_not_modelled")
            layers.append(TaxLayer(
                name="state", rate=fin.state_rate,
                depreciation=_classes(pack, fin, assets, cod, ResolvedTax(), bonus_macrs=0.0),
                deductible_in_later_layers=True, loss=LossRule(), interest_cap=cap_obj))
        if rate is not None and nol is not None:
            layers.append(TaxLayer(
                name="federal", rate=rate, depreciation=fed_classes,
                loss=LossRule(allowance=nol["allowance"], limit_share=nol["limit_share"],
                              years=nol["years"]),
                interest_cap=cap_obj))
        res.layers = tuple(layers)
    elif pack.jurisdiction == "eu_de":
        messzahl, gew_loss, addback = need("gewst_messzahl"), need("gewst_loss"), \
            need("gewst_interest_addback")
        kst, solz, kst_loss, zins = need("kst_rate"), need("solz_share"), need("kst_loss"), \
            need("zinsschranke")
        deductible = need("gewst_deductible")
        classes = _classes(pack, fin, assets, cod, res)
        if fin.hebesatz_pct is None:
            res.missing.append("hebesatz_pct")
        cap_obj = (InterestCap(share=zins["share"], freigrenze=zins["freigrenze"],
                               carryforward=False) if carry and zins is not None else None)
        if None not in (messzahl, gew_loss, addback, kst, solz, kst_loss, zins, deductible) \
                and fin.hebesatz_pct is not None:
            res.layers = (
                TaxLayer(name="gewst", rate=messzahl * fin.hebesatz_pct / 100.0,
                         depreciation=classes, deductible_in_later_layers=bool(deductible),
                         loss=LossRule(allowance=gew_loss["allowance"],
                                       limit_share=_sched_dict(gew_loss["limit_share"]),
                                       years=gew_loss["years"]),
                         interest_addback_share=addback["share"],
                         interest_addback_allowance=addback["allowance"],
                         interest_cap=cap_obj),
                TaxLayer(name="kst", rate=_sched_dict(kst), depreciation=classes,
                         surcharge_share=float(solz),
                         loss=LossRule(allowance=kst_loss["allowance"],
                                       limit_share=_sched_dict(kst_loss["limit_share"]),
                                       years=kst_loss["years"]),
                         interest_cap=cap_obj),
            )
            if carry:
                res.flags.append("gewst_addback_interest_only")
    else:
        res.missing.append(f"pack_not_resolvable:{pack.jurisdiction}")
    res.missing = sorted(set(res.missing))
    return res
