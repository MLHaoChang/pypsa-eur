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
balance at `rate`, switching to straight-line, the first year pro rata by
month from COD — §7 Abs. 2 EStG; WP4.3a review B1), `slm_<n>` (straight-line
pro rata by month from COD — the Dutch time-proportional convention),
`cca_<class>` (Canadian CCA: the pack's class rate, declining balance, the
half-year rule or the enhanced first-year allowance by acquisition and
available-for-use dates — WP4.3b). Every schedule is checked
(years ≥ 1, 0 < rate ≤ 1, entries ≥ 0, summing to 1 — review B7).

Whenever anything is MISSING the resolver returns NO layers (review B3): a
partial set (a missing asset's share undepreciated, a missing state layer,
bonus taken as 0) would look usable and be wrong.

Deviation from plan C7 (review B8, recorded): the layer STRUCTURE (which
layers, their order and deductibility wiring, the US state slot) is code per
jurisdiction here; the pack carries the rates, schedules and rules it cites
and its hash covers those. A new jurisdiction (WP4.3b) adds a branch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from models.finance import FinanceInputs
from services.finance.packs.base import JurisdictionPack
from services.finance.tax import (
    DepreciationClass, InterestCap, LossRule, TaxLayer, cca_declining, declining_balance,
    normalised, sl_half_year, sl_pro_rata,
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


def _years(v: str, cls: str) -> int:
    n = int(v)
    if n < 1:
        raise ValueError(f"depreciation class {cls!r}: years must be ≥ 1")
    return n


def _cca(rest: str, pack: JurisdictionPack, cod: date, acquired: date | None) -> tuple[float, ...]:
    classes = pack.rule("cca_classes")
    if classes.status != "ok" or rest not in classes.value:
        raise KeyError(f"CCA class {rest!r} is not in pack {pack.jurisdiction!r}")
    c = classes.value[rest]
    if acquired is None:
        raise ValueError("CCA needs acquisition_date")
    if "acquired_before" in c and not (
            date.fromisoformat(c["acquired_after"]) < acquired < date.fromisoformat(c["acquired_before"])):
        raise ValueError(f"CCA class {rest} applies to property acquired after {c['acquired_after']} "
                         f"and before {c['acquired_before']}")
    first = None
    progs = pack.rule("first_year_programs")
    for prog in (progs.value if progs.status == "ok" else []):
        if rest not in prog["classes"] or acquired <= date.fromisoformat(prog["acquired_after"]):
            continue
        if "acquired_before" in prog and acquired >= date.fromisoformat(prog["acquired_before"]):
            continue
        by = {int(k): v for k, v in prog["by_available_for_use_year"].items()}
        if cod.year <= max(by):
            first = by.get(cod.year, by[min(by)] if cod.year < min(by) else None)
        break
    return cca_declining(float(c["rate"]), first)


def schedule_for(cls: str, pack: JurisdictionPack, cod: date,
                 acquired: date | None = None) -> tuple[float, ...]:
    kind, _, rest = cls.partition("_")
    months = 13 - cod.month                     # months in the year of COD, COD's month included
    if kind == "macrs":
        table = pack.rule("macrs_half_year_percent")
        if table.status != "ok" or rest not in table.value:
            raise KeyError(f"depreciation class {cls!r}: no MACRS table in pack "
                           f"{pack.jurisdiction!r}")
        # Table A-1 is rounded to 2–3 decimals of a percent.
        return normalised([p / 100.0 for p in table.value[rest]], tol=1e-4)
    if kind == "sl":
        return normalised(sl_half_year(_years(rest, cls)))
    if kind in ("afa", "slm"):
        return normalised(sl_pro_rata(_years(rest, cls), months_first_year=months))
    if kind == "cca":
        return normalised(_cca(rest, pack, cod, acquired))
    if kind == "db":
        rate, _, years = rest.partition("_")
        return normalised(declining_balance(float(rate), _years(years, cls),
                                            months_first_year=months))
    raise KeyError(f"unknown depreciation class {cls!r}")


def _default_class(pack: JurisdictionPack, fin: FinanceInputs, carrier: str,
                   res: ResolvedTax) -> str | None:
    if pack.jurisdiction != "eu_de":
        return None                 # US: the 2025 act changed energy classes — the case states it
    lives = pack.rule("afa_useful_life_years")
    if lives.status != "ok" or carrier not in lives.value:
        return None
    res.sources["afa_useful_life_years"] = lives.source
    n = int(lives.value[carrier])
    deg = pack.rule("degressive_afa")
    acq = fin.acquisition_date
    if deg.status == "ok":
        if acq is None:
            # The date decides linear vs degressive — never assumed (review B2).
            res.missing.append("acquisition_date")
            return None
        res.sources["degressive_afa"] = deg.source
        if date.fromisoformat(deg.value["acquired_from"]) <= acq <= date.fromisoformat(
                deg.value["acquired_to"]):
            rate = min(deg.value["multiple_of_sl"] / n, deg.value["max_rate"])
            # Degressive AfA is an election (§7 Abs. 2 "kann"): taken, stated.
            res.flags.append("degressive_afa_elected")
            return f"db_{rate!r}_{n}"
    return f"afa_{n}"


def _classes(pack, fin, assets, cod, res: ResolvedTax, *, bonus_macrs: float = 0.0):
    total = sum(a.basis for a in assets)
    out = []
    for a in assets:
        cls = fin.depreciation_class_by_asset.get(a.name) or _default_class(pack, fin, a.carrier,
                                                                             res)
        if cls is None:
            res.missing.append(f"depreciation_class:{a.name}")
            continue
        try:
            sched = schedule_for(cls, pack, cod, fin.acquisition_date)
            cap = pack.rule("depreciation_max_rate")
            if cap.status == "ok" and max(sched) > cap.value + 1e-12:
                raise ValueError(f"exceeds the {cap.value:.0%} a year cap ({cap.source})")
            if cls.startswith("macrs_"):
                res.sources["macrs_half_year_percent"] = pack.rule("macrs_half_year_percent").source
        except (KeyError, ValueError, ZeroDivisionError) as e:
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
    res.sources["bonus_depreciation"] = rule.source
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
        itc_red = need("itc_basis_reduction")
        if itc_red is not None and itc_red != 0.5:
            # The tax engine reduces by exactly ½ the credit (SAM, §50(c)(3)).
            res.missing.append(f"pack_rule:itc_basis_reduction:unsupported_share:{itc_red}")
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
            # most states do), follows the federal interest limit and, in
            # carryforward mode, carries losses without a limit — all stated.
            if bonus > 0:
                res.flags.append("state_bonus_decoupled")
            if cap_obj is not None:
                res.flags.append("state_interest_limit_follows_federal")
            if carry:
                res.flags.append("state_loss_rule_not_modelled")
            layers.append(TaxLayer(
                name="state", rate=fin.state_rate,
                depreciation=_classes(pack, fin, assets, cod, ResolvedTax(), bonus_macrs=0.0),
                deductible_in_later_layers=True, loss=LossRule(), interest_cap=cap_obj))
        if rate is not None and nol is not None:
            # The federal basis is reduced by 50 % of an ITC (§50(c)); the
            # state slot is not (a state that conforms is not modelled — WP4.5
            # flags it when an ITC is claimed).
            layers.append(TaxLayer(
                name="federal", rate=rate, depreciation=fed_classes,
                itc_basis_reduction=True,
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
        elif fin.hebesatz_pct < 200.0:
            res.missing.append("hebesatz_pct:below_statutory_minimum_200")   # §16 Abs. 4 GewStG
        cap_obj = (InterestCap(share=zins["share"], freigrenze=zins["freigrenze"],
                               carryforward=bool(zins["carryforward"]),
                               simplified_flag="zinsschranke_simplified")
                   if carry and zins is not None else None)
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
    elif pack.jurisdiction == "eu_nl":
        brackets, loss, strip = need("vpb_brackets"), need("vpb_loss"), need("earnings_stripping")
        need("depreciation_max_rate")
        classes = _classes(pack, fin, assets, cod, res)
        if None not in (brackets, loss, strip):
            cap_obj = (InterestCap(share=strip["share"], allowance=strip["allowance"],
                                   carryforward=bool(strip["carryforward"])) if carry else None)
            res.layers = (TaxLayer(
                name="vpb", rate=float(brackets[-1][1]), depreciation=classes,
                brackets=tuple((float(t), float(r)) for t, r in brackets),
                loss=LossRule(allowance=loss["allowance"], limit_share=loss["limit_share"],
                              years=loss["years"]),
                interest_cap=cap_obj),)
            res.flags.append("nl_residual_value_not_modelled")
            if not carry:
                # Offset mode assumes other income, yet a positive year takes
                # the stand-alone brackets (WP4.3b review, non-binding #1).
                res.flags.append("nl_brackets_standalone_in_offset_mode")
            if carry:
                res.flags.append("nl_loss_carryback_not_modelled")
    elif pack.jurisdiction == "ca_federal":
        rate, loss, red = need("federal_rate"), need("non_capital_loss"), \
            need("itc_capital_cost_reduction")
        need("cca_classes")
        classes = _classes(pack, fin, assets, cod, res)
        if fin.state_rate is None:
            res.missing.append("state_rate")          # the provincial rate (0 = none)
        if None not in (rate, loss, red):
            lr = LossRule(allowance=loss["allowance"], limit_share=loss["limit_share"],
                          years=loss["years"])
            itc = dict(itc_basis_reduction=True, itc_basis_reduction_share=float(red["share"]),
                       itc_basis_reduction_lag=int(red["lag_years"]))
            layers = [TaxLayer(name="federal", rate=rate, depreciation=classes,
                               deductible_in_later_layers=False, loss=lr, **itc)]
            if fin.state_rate:
                layers.append(TaxLayer(name="provincial", rate=fin.state_rate,
                                       depreciation=classes, deductible_in_later_layers=False,
                                       loss=lr, **itc))
                res.flags.append("provincial_layer_follows_federal_cca_and_losses")
            res.layers = tuple(layers)
            if carry:
                res.flags += ["ca_eifel_not_modelled", "ca_loss_carryback_not_modelled"]
            if pack.valid_from < date(2026, 3, 26) and fin.acquisition_date is not None and \
                    fin.acquisition_date >= date(2025, 1, 1):
                # S.C. 2026, c. 3 later changed this retroactively (from 2025).
                res.flags.append("ca_first_year_superseded_retroactively")
    else:
        res.missing.append(f"pack_not_resolvable:{pack.jurisdiction}")
    res.missing = sorted(set(res.missing))
    res.flags = sorted(set(res.flags))
    if res.missing:
        res.layers = ()                      # never a partial, usable-looking set (review B3)
    return res
