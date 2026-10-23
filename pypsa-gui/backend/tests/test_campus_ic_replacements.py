"""
Campus-IC 04: the investment case takes the campus study's replacements
without buying them twice (`.scratch/campus-ic-extra-owner-assets/issues/04-campus-replacements.md`,
the plan and the GK's six points in `04-plan.md` beside it).

The renewals of a campus lineage are folded into its first purchase
(`ExtraOwnerAsset.renewals`, one `CampusRenewal` per part and purchase): the
replacement schedule pays each one in `build_year − stage_capex_lead_years`
(`source="campus_study"`, under both rules), the IC's own `part_lifetimes`
rule leaves the lineage alone until its last purchase's life ends, and the
terminal value takes the last purchase alive at the horizon.

The hand cases run the engine on a 30-year axis from COD 2030 (horizon 2060),
`capex` escalating 2 % a year from base year 2030: a transformer pair (2 units
at 1,000 each, L = 12.5) bought in 2030 and renewed in 2043 and 2055, as the
campus study's `c14_rows` lists it.
"""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from models.finance import ESCALATION_CLASSES, FinanceInputs
from services.finance.case import FinanceRefused
from tests.test_campus_electrical_service import c14_rows, fake_c14, hub, user_and_db  # noqa: F401
from tests.test_finance_case_extra_assets import HASH, _ext, _hash_fixture, _part

AMOUNT = 2_000.0                        # 2 units × 1,000 a unit, unescalated
R_CAPEX = 0.02
PURCHASE_ROWS_HASH = "ddb1b7615306745c"   # master a27d8b67, before 04


def _renewal(build_year, *, part="investment", amount=AMOUNT, name=None, service_to=None):
    from services.finance.case import CampusRenewal

    return CampusRenewal(part=part, build_year=build_year, amount=amount,
                         name=name or f"campus:TR#{build_year}", service_to=service_to)


def _trf(renewals=(_renewal(2043, service_to=2055), _renewal(2055, service_to=2060)), *,
         service_to=2043, build_year=2030, existing_renewal=False, parts=None, life=12.5, **over):
    from services.finance.case import ExtraOwnerAsset

    kw = dict(name="trf", kind="transformer", basis="lump", quantity=2,
              parts=parts if parts is not None else (_part(1_000.0, life, 0.01),),
              build_year=build_year, source="campus_study", source_hash=HASH,
              renewals=tuple(renewals), service_to=service_to, existing_renewal=existing_renewal)
    kw.update(over)
    return ExtraOwnerAsset(**kw)


def _inputs(**over) -> FinanceInputs:
    kw = dict(financial_close=date(2029, 1, 1), analysis_years=30, contingency_share=0.0,
              escalation={c: R_CAPEX for c in ESCALATION_CLASSES}, tax_losses="offset_other_income",
              financing_fee_tax="not_deducted", wacc_nominal=0.07, cost_of_equity=0.10,
              inflation=0.02, terminal_value={"method": "remaining_life_annuity"})
    kw.update(over)
    return FinanceInputs(**kw)


def _case(*extras, asset_cod=None, templates=None, **fin):
    """A hand case of `extras`; with a staged `asset_cod`, a network asset `pv`
    (no capex, L 40) opens the case at COD 2030."""
    from services.finance.case import AssetFinance, FinanceCase, LpBasis, Template, TemplateLine

    network = (AssetFinance("pv", "Generator", 0.0, 40.0, "solar"),) if asset_cod else ()
    if asset_cod:
        asset_cod = {"pv": date(2030, 1, 1), **asset_cod}

    return FinanceCase(
        inputs=_inputs(**fin), owner="site", base_year=2030, cod=date(2030, 1, 1),
        templates=templates or (Template(2030, (TemplateLine("bill", "energy_import", -1000.0,
                                                             "tariff"),), money_year=2030),),
        assets=network + tuple(e.asset_finance() for e in extras), extra_assets=tuple(extras),
        lp_basis=LpBasis(discount_rate=0.07), asset_cod=asset_cod or {})


def _run(case):
    from services.finance.engine import run_case

    return run_case(case)


def _items(r, asset="trf"):
    return [(x.year, x.part, x.source, v) for x, v in r.op.replacement_items if x.asset == asset]


def _esc(year):
    return AMOUNT * (1 + R_CAPEX) ** (year - 2030)


# ── GK point 1: a renewal validates itself ───────────────────────────────────


@pytest.mark.parametrize("renewals,why", [
    ((_renewal(2043, part="other"),), "names no part"),
    ((_renewal(2030),), "not after the first purchase"),
    ((_renewal(2020),), "before the first purchase"),
    ((_renewal(2201),), "outside 1900..2200"),
    ((_renewal(2043.0),), "not an int"),
    ((_renewal(True),), "a bool is no year"),
    ((_renewal(2043, amount=-1.0),), "below 0"),
    ((_renewal(2043, amount=float("nan")),), "not finite"),
    ((_renewal(2043, amount=float("inf")),), "not finite"),
    ((_renewal(2043, amount="2000"),), "a string is no amount"),
    ((_renewal(2055), _renewal(2043)), "not increasing"),
    ((_renewal(2043), _renewal(2043)), "not strictly increasing"),
    (({"part": "investment", "build_year": 2043, "amount": 1.0},), "not a CampusRenewal"),
])
def test_an_invalid_renewal_is_refused_naming_renewals(renewals, why):
    with pytest.raises(ValueError, match=r"^extra_asset_invalid:trf:renewals"):
        _trf(renewals)


@pytest.mark.parametrize("over,field", [
    ({"service_to": 2030}, "service_to"),            # not after the build year
    ({"service_to": float("nan")}, "service_to"),
    ({"service_to": "2043"}, "service_to"),
    ({"service_to": None}, "service_to"),            # renewals need the lineage's service_to
    ({"existing_renewal": 1}, "existing_renewal"),
    ({"renewals": [_renewal(2043)]}, "renewals"),    # a tuple, as every other frozen field
])
def test_the_lineage_fields_validate_themselves(over, field):
    with pytest.raises(ValueError, match=rf"^extra_asset_invalid:trf:{field}"):
        dataclasses.replace(_trf(), **over)


def test_the_renewal_amount_is_the_whole_purchase_unescalated():
    """GK point 1: amount = upfront_per_unit × quantity in the case's money, the
    basis of `asset_finance`'s overnight cost; per part."""
    e = _trf()
    assert e.asset_finance().overnight_cost == AMOUNT
    assert [(r.part, r.amount) for r in e.renewals] == [("investment", AMOUNT)] * 2


# ── d: the cash year, both rules, lead 1 and lead 2 ──────────────────────────


@pytest.mark.parametrize("rule", ["fixed", "part_lifetimes"])
@pytest.mark.parametrize("lead", [1, 2])
def test_each_renewal_is_bought_once_in_its_build_year_less_the_lead(rule, lead):
    """Issue test 2: each campus purchase appears once in the capex; no
    generated replacement for a campus asset; no refusal for a renewal."""
    r = _run(_case(_trf(), replacement_rule=rule, stage_capex_lead_years=lead))
    assert r.op.status["capex"] == "ok"
    want = [(2043 - lead, "investment", "campus_study", pytest.approx(_esc(2043 - lead))),
            (2055 - lead, "investment", "campus_study", pytest.approx(_esc(2055 - lead)))]
    assert _items(r) == want
    assert not any(f.startswith(("part_not_replaced", "campus_life_ends_unreplaced",
                                 "part_lifetime_rounded")) for f in r.op.flags)


def test_a_campus_renewal_is_a_replacement_for_the_lifetime_check():
    """a: under `fixed`, a 12.5-y asset on a 30-y axis is not refused
    `asset_lifetime_short`: the campus renewals replace it (`counts_as_replaced`)."""
    from services.finance.replacements import counts_as_replaced, schedule
    from services.finance.timeline import build_timeline

    case = _case(_trf())
    assert counts_as_replaced(case, case.assets[0], schedule(case, build_timeline(case)))
    assert _run(case).op.status["capex"] == "ok"


@pytest.mark.parametrize("rule", ["fixed", "part_lifetimes"])
def test_a_replacement_capex_entry_for_a_campus_asset_is_refused(rule):
    """a: the grid study already replaces it, under either rule."""
    case = _case(_trf(), replacement_rule=rule, replacement_capex=[(2042, "trf", 5.0)])
    with pytest.raises(FinanceRefused) as exc:
        _run(case)
    assert exc.value.code == "replacement_rule_conflict:trf"
    assert "grid study already replaces it" in exc.value.detail


def test_a_campus_renewal_tax_vintage_starts_in_its_cash_year():
    """GK point 6: the tax vintages read the one schedule."""
    from services.finance.engine import _replacement_vintages
    from services.finance.timeline import build_timeline

    for lead in (1, 2):
        case = _case(_trf(), stage_capex_lead_years=lead)
        tl = build_timeline(case)
        got = [(tl.years[i], v, a) for i, v, a in _replacement_vintages(case, tl)]
        assert got == [(2043 - lead, pytest.approx(_esc(2043 - lead)), "trf"),
                       (2055 - lead, pytest.approx(_esc(2055 - lead)), "trf")]


# ── b: the terminal value ────────────────────────────────────────────────────


def _term(r, asset="trf", part="investment"):
    (t,) = [t for t in r.op.terminal_terms if (t.asset, t.part) == (asset, part)]
    return t


@pytest.mark.parametrize("rule", ["fixed", "part_lifetimes"])
@pytest.mark.parametrize("lead", [1, 2])
def test_the_terminal_value_takes_the_last_renewal(rule, lead):
    """b, GK point 4: start = the renewal's build year (its first service year);
    base = its amount escalated to its cash year. Lead 2 moves the base, not the start."""
    r = _run(_case(_trf(), replacement_rule=rule, stage_capex_lead_years=lead))
    t = _term(r)
    assert (t.start_year, t.remaining_years) == (2055, 7.5)
    assert t.base == pytest.approx(_esc(2055 - lead))
    crf = 0.07 / (1 - 1.07 ** -12.5)
    apf = (1 - 1.07 ** -7.5) / 0.07
    assert t.value == pytest.approx(_esc(2055 - lead) * crf * apf, rel=1e-12)


def test_a_part_of_a_renewed_lineage_without_a_renewal_is_not_established():
    """b: renewals are per part; a part with none gets `terminal_part_unknown`."""
    from services.asset_schema.access import UpfrontPart

    parts = (UpfrontPart("winding", 800.0, 12.5, 0.01), UpfrontPart("tank", 200.0, 40.0, 0.0))
    e = _trf((_renewal(2043, part="winding", amount=1_600.0, service_to=2055),
              _renewal(2055, part="winding", amount=1_600.0, service_to=2060)), parts=parts)
    r = _run(_case(e))
    assert "terminal_part_unknown:trf:tank" in r.op.reasons["terminal"]
    assert [x[:3] for x in _items(r)] == [(2042, "winding", "campus_study"),
                                          (2054, "winding", "campus_study")]


# ── c: off the axis ──────────────────────────────────────────────────────────


def test_a_renewal_at_or_after_the_horizon_is_dropped_and_flagged():
    """c: a 20-y case (horizon 2050) drops the 2055 renewal; the last purchase on
    the axis (2043) carries the terminal value; its successor exists, so no f flag."""
    r = _run(_case(_trf(), analysis_years=20))
    assert [x[0] for x in _items(r)] == [2042]
    assert "campus_renewal_after_horizon:trf:2055" in r.op.flags
    assert not any(f.startswith("campus_life_ends_unreplaced") for f in r.op.flags)
    t = _term(r)
    assert (t.start_year, t.remaining_years) == (2043, 5.5)


def test_a_renewal_paid_before_the_asset_cod_is_refused():
    """c, issue test 3: a renewal before COD stays refused."""
    case = _case(_trf((_renewal(2031),), service_to=2031), stage_capex_lead_years=2)
    with pytest.raises(FinanceRefused) as exc:
        _run(case)
    assert exc.value.code == "campus_renewal_before_cod:trf:2031"


# ── f / GK point 3: the life of the last purchase ends inside the horizon ────


def test_under_fixed_a_lineage_ending_inside_the_horizon_is_flagged_and_not_bought():
    """f, issue test 4: the 2043 purchase (L 12.5) ends in 2055.5, before 2060."""
    r = _run(_case(_trf((_renewal(2043, service_to=2060),))))
    assert "campus_life_ends_unreplaced:trf:2055:fixed" in r.op.flags
    assert [x[0] for x in _items(r)] == [2042]
    assert not any(f.startswith("part_not_replaced:trf") for f in r.op.flags)


@pytest.mark.parametrize("lead", [1, 2])
def test_under_part_lifetimes_the_ic_rule_resumes_where_the_last_life_ends(lead):
    """GK point 3: the IC rule resumes at the END of the lineage's last purchase
    (2043 + 12.5 = 2055.5), paid in its first service year (half up, 2056) less
    the lead, as a renewal; the terminal value takes that purchase."""
    r = _run(_case(_trf((_renewal(2043, service_to=2060),)), replacement_rule="part_lifetimes",
                   stage_capex_lead_years=lead))
    assert "campus_life_ends_unreplaced:trf:2055:part_lifetimes" in r.op.flags
    assert [(x[0], x[2]) for x in _items(r)] == [(2043 - lead, "campus_study"),
                                                 (2056 - lead, "part_lifetimes")]
    assert _items(r)[1][3] == pytest.approx(_esc(2056 - lead))
    t = _term(r)
    assert (t.start_year, t.remaining_years) == (2055.5, 8.0)
    assert t.base == pytest.approx(_esc(2056 - lead))


def test_a_service_to_cut_by_the_study_horizon_is_not_the_end_of_life():
    """GK point 3's pinned case, service_to < build_year + L. The study's
    `service_to` of a purchase with no successor is its horizon's end (one year
    for a single-period project: 2031), not its end of life. The IC resumes at
    build_year + L, so the Data Center (no campus renewal) is bought as an asset
    without lineage is, at lead 1."""
    campus = _run(_case(_trf((), service_to=2031), replacement_rule="part_lifetimes"))
    plain = _run(_case(_trf((), service_to=None), replacement_rule="part_lifetimes"))
    assert [(x[0], x[3]) for x in _items(campus)] == [(x[0], x[3]) for x in _items(plain)] \
        == [(2042, pytest.approx(_esc(2042))), (2054, pytest.approx(_esc(2054)))]
    assert (_term(campus).start_year, _term(campus).base) == \
        (_term(plain).start_year, _term(plain).base)
    assert "campus_life_ends_unreplaced:trf:2042:part_lifetimes" in campus.op.flags


# ── GK point 2: an existing asset's first renewal is a stage ─────────────────


def test_an_existing_assets_first_renewal_off_a_period_is_a_stage_not_refused():
    """GK point 2: replaces None, action renew: a P5 stage at COD = build_year
    (2043 in a 2030/2040/2050 project). P5-a's `staged_cod_not_active_period`
    exempts it (the kept asset serves until then); a first purchase off a
    period is still refused."""
    from services.results.finance_case import _stage_rules

    cod = date(2030, 1, 1)
    e = _trf((), build_year=2043, service_to=2060, existing_renewal=True)
    _stage_rules(None, [2030, 2040, 2050], 2030, [], {}, (e,), {"trf": date(2043, 1, 1)}, cod)
    bought = dataclasses.replace(e, existing_renewal=False)
    with pytest.raises(FinanceRefused) as exc:
        _stage_rules(None, [2030, 2040, 2050], 2030, [], {}, (bought,),
                     {"trf": date(2043, 1, 1)}, cod)
    assert exc.value.code == "staged_cod_not_active_period:trf"


def _fom_templates():
    from services.finance.case import Template, TemplateLine

    def t(year):
        return Template(year, (TemplateLine("bill", "energy_import", -1000.0, "tariff"),
                               TemplateLine(key="extra_asset_fom:trf", stream="fom", amount=-20.0,
                                            esc_class="opex", source="extra_asset_fom",
                                            source_id="trf", period=str(year),
                                            money_year=2030)), money_year=year)
    return (t(2030), t(2040), t(2050))


def test_a_stage_off_a_period_pays_no_fom_before_its_cod():
    """GK point 2: the adapter keeps the FOM line in the period holding the
    stage's COD (2040 for 2043), and the cash flow starts it in 2043."""
    from services.results.finance_case import _drop_fom_before_stages

    kept = _drop_fom_before_stages(_fom_templates(), {"trf": 2043})
    assert [any(ln.source == "extra_asset_fom" for ln in t.lines) for t in kept] == \
        [False, True, True]
    e = _trf((), build_year=2043, service_to=2060, existing_renewal=True, life=40.0)
    r = _run(_case(e, asset_cod={"trf": date(2043, 1, 1)}, templates=kept))
    fom = r.op.lines["extra_asset_fom:trf"]
    years = list(r.tl.years)
    assert all(fom[years.index(y)] == 0.0 for y in range(2030, 2043))
    assert fom[years.index(2043)] == pytest.approx(-20.0 * 1.02 ** 13)
    # a stage on a period keeps the templates it had (and so its hash)
    assert _drop_fom_before_stages(_fom_templates(), {"trf": 2040}) == tuple(
        dataclasses.replace(t, lines=tuple(ln for ln in t.lines
                                           if not (ln.source == "extra_asset_fom"
                                                   and t.first_year < 2040)))
        for t in _fom_templates())


@pytest.mark.parametrize("lead", [1, 2])
def test_the_stage_and_its_folded_renewals_are_paid_by_one_rule(lead):
    """d: the existing asset's first renewal (a stage) is paid build_year − lead,
    as each renewal folded into it."""
    e = _trf((_renewal(2055, service_to=2060),), build_year=2043, service_to=2055,
             existing_renewal=True)
    r = _run(_case(e, asset_cod={"trf": date(2043, 1, 1)}, stage_capex_lead_years=lead))
    assert [(a, c, y) for a, c, y, _v in r.op.stage_items] == [("trf", 2043, 2043 - lead)]
    assert r.op.stage_items[0][3] == pytest.approx(_esc(2043 - lead))
    assert [x[0] for x in _items(r)] == [2055 - lead]


# ── GK point 5: the hash ─────────────────────────────────────────────────────


def test_a_case_with_extras_but_no_renewals_keeps_its_hash():
    from services.results.finance_case import _canon, finance_case_hash

    plain = _ext()
    assert finance_case_hash(_hash_fixture(extra_assets=(plain,))) == "03e09dc020aa2c04"
    assert {"renewals", "service_to", "existing_renewal"}.isdisjoint(_canon(plain))
    renewed = _trf()
    canon = _canon(renewed)
    assert canon["renewals"][0] == {"part": "investment", "build_year": 2043, "amount": AMOUNT,
                                    "name": "campus:TR#2043", "service_to": 2055}
    assert finance_case_hash(_hash_fixture(extra_assets=(renewed,))) != \
        finance_case_hash(_hash_fixture(extra_assets=(_trf(()),)))


# ── a capex bound moves the renewals with their asset ────────────────────────


def test_scale_capex_moves_the_renewals():
    from services.finance.case import scale_capex

    case = _case(_trf())
    assert [r.amount for r in scale_capex(case, 1.5).extra_assets[0].renewals] == [3_000.0] * 2
    assert [r.amount for r in scale_capex(case, 1.5, asset="trf").extra_assets[0].renewals] \
        == [3_000.0] * 2
    assert [r.amount for r in scale_capex(case, 1.5, asset="trf", part="investment")
            .extra_assets[0].renewals] == [3_000.0] * 2


# ── the route: folding the producer's list (c, e; issue tests 1, 2) ──────────


def _fold(resp):
    import routers.results as R

    return R._ic_campus_from_response(resp, None, unsaved=False)


def test_the_route_folds_each_lineage_into_its_first_purchase(hub):  # noqa: F811
    from services import campus_electrical_service as ce

    fake_c14(hub, c14_rows())
    src = _fold(ce.owner_assets_response(hub, replacements=True))
    assert src.refusal is None, src.detail
    by = {e.name: e for e in src.extras}
    assert list(by) == ["campus:TR_132_33_63#1", "campus:CB_33_AL240#1",
                        "campus:TR_132_33_40#1", "campus:CB_33_AL240#2"]
    trf = by["campus:TR_132_33_63#1"]
    per = trf.quantity * trf.parts[0].upfront_per_unit
    assert [(r.build_year, r.name, r.service_to, r.amount) for r in trf.renewals] == [
        (2043, "campus:TR_132_33_63#2", 2055, per), (2055, "campus:TR_132_33_63#3", 2060, per)]
    assert (trf.service_to, trf.existing_renewal) == (2043, False)
    assert by["campus:CB_33_AL240#1"].renewals == ()
    assert (by["campus:TR_132_33_40#1"].existing_renewal,
            by["campus:TR_132_33_40#1"].build_year) == (True, 2040)
    assert by["campus:CB_33_AL240#2"].existing_renewal is True


def test_a_table_without_lineage_folds_nothing_and_keeps_its_digest(hub):  # noqa: F811
    """Issue test 1, e: a producer table from before C14 gives the same list
    either way; the case and the digest are what they were."""
    import routers.results as R
    from services import campus_electrical_service as ce
    from tests.test_campus_electrical_service import fake_investment, purchase_rows

    fake_investment(hub, purchase_rows())
    resp = ce.owner_assets_response(hub, replacements=True)
    assert resp == ce.owner_assets_response(hub)
    src = _fold(resp)
    assert all(e.renewals == () and e.service_to is None and e.existing_renewal is False
               for e in src.extras)
    bare = [{k: v for k, v in a.items() if k != "meta"} for a in resp["assets"]]
    tags = R._ic_cost_tags(resp["assets"])
    summary = R._ic_campus_summary(resp, tags, [])
    old = R._ic_campus_digest(resp["source_hash"], False, (), bare, [], summary)
    for a in resp["assets"]:            # a pre-C14 table states no lineage
        assert a["meta"]["lineage"] is None
    assert src.digest == old


def test_the_lineage_meta_enters_the_digest(hub):  # noqa: F811
    """e: replaces / service_to / purchase / lineage join the digest when stated."""
    from services import campus_electrical_service as ce

    fake_c14(hub, c14_rows())
    resp = ce.owner_assets_response(hub, replacements=True)
    base = _fold(resp).digest
    moved = {**resp, "assets": [{**a, "meta": {**a["meta"], "service_to": 2059}}
                                if a["name"] == "campus:CB_33_AL240#1" else a
                                for a in resp["assets"]]}
    assert _fold(moved).digest != base


def _c14_resp(hub, mutate):  # noqa: F811
    from services import campus_electrical_service as ce

    fake_c14(hub, c14_rows())
    resp = ce.owner_assets_response(hub, replacements=True)
    assets = [{**a, "meta": dict(a["meta"])} for a in resp["assets"]]
    mutate({a["name"]: a for a in assets})
    return {**resp, "assets": assets}


@pytest.mark.parametrize("mutate", [
    lambda by: by["campus:TR_132_33_63#2"].update(quantity=3.0),
    lambda by: by["campus:TR_132_33_63#2"].update(kind="statcom"),
    lambda by: by["campus:TR_132_33_63#2"].update(basis="per_bay"),
    lambda by: by["campus:TR_132_33_63#2"].update(
        parts=[{**by["campus:TR_132_33_63#2"]["parts"][0], "name": "other"}]),
    lambda by: by["campus:TR_132_33_63#2"]["meta"].update(replaces="campus:NOPE#1"),
    lambda by: by["campus:TR_132_33_63#2"]["meta"].update(lineage="another"),
    lambda by: by["campus:TR_132_33_63#2"]["meta"].update(replaces=None, action="buy"),
    lambda by: by["campus:TR_132_33_63#1"]["meta"].update(service_to=None),
], ids=["quantity", "kind", "basis", "part names", "replaces no entry", "other lineage",
        "two first purchases", "renewed with no service_to"])
def test_a_renewal_unlike_its_lineage_is_refused_with_its_label_only(hub, mutate):  # noqa: F811
    src = _fold(_c14_resp(hub, mutate))
    assert src.refusal == "campus_assets_invalid"
    assert src.detail.startswith("'campus:TR_132_33_63#")
    assert "3.0" not in src.detail and "statcom" not in src.detail


@pytest.mark.parametrize("rule", ["fixed", "part_lifetimes"])
def test_the_c14_study_through_the_case_buys_each_campus_purchase_once(hub, rule):  # noqa: F811
    """Issue test 2 on `c14_rows` (horizon 2060, no escalation, no contingency):
    the capex, the stages and the replacements sum, by hand, to every purchase
    the study lists, plus the IC's own purchases after a lineage's last life
    ends; the campus renewals are paid once each, in build_year − 1."""
    from services import campus_electrical_service as ce
    from services.finance.replacements import _resumed, lineages
    from services.finance.timeline import build_timeline

    fake_c14(hub, c14_rows())
    resp = ce.owner_assets_response(hub, replacements=True)
    src = _fold(resp)
    assert src.refusal is None, src.detail
    stages = {e.name: date(e.build_year, 1, 1) for e in src.extras if e.build_year > 2030}
    zero = {c: 0.0 for c in ESCALATION_CLASSES}
    case = _case(*src.extras, asset_cod=stages, replacement_rule=rule, escalation=zero)
    r = _run(case)
    assert r.op.status["capex"] == "ok", r.op.reasons
    renewals = sorted((a["name"], a["build_year"]) for a in resp["assets"] if a["meta"]["replaces"])
    by_renewal = {rn.name: e.name for e in src.extras for rn in e.renewals}
    assert sorted((x.asset, x.year) for x, _v in r.op.replacement_items if x.source == "campus_study") \
        == sorted((by_renewal[n], y - 1) for n, y in renewals)
    tl = build_timeline(case)
    own = sum(p.overnight_cost for a in case.assets if a.name in lineages(case)
              for p in a.parts for _s in _resumed(case, tl, lineages(case)[a.name], a, p)) \
        if rule == "part_lifetimes" else 0.0
    listed = sum(a["quantity"] * a["parts"][0]["upfront_per_unit"] for a in resp["assets"])
    paid = r.op.capex.sum() + r.op.stage_capex.sum() + r.op.replacement.sum()
    assert paid == pytest.approx(listed + own, rel=1e-12)
    if rule == "fixed":
        assert all(x.source != "part_lifetimes" for x, _v in r.op.replacement_items)


# ── coordinator follow-up (GK on the point-3 reading and point-2 choices) ────


def test_the_data_center_single_period_gets_no_flag_and_no_extra_purchase():
    """Flag f reads the last purchase's build_year + L, never a horizon
    `service_to`: the Data Center's equipment (L 40, a 25-y case, service_to
    2031 = the one-year study horizon's end) is neither flagged nor bought
    again, exactly as an asset without a lineage."""
    kw = dict(replacement_rule="part_lifetimes", analysis_years=25)
    campus = _run(_case(_trf((), service_to=2031, life=40.0), **kw))
    plain = _run(_case(_trf((), service_to=None, life=40.0), **kw))
    assert _items(campus) == _items(plain) == []
    assert not any(f.startswith("campus_life_ends_unreplaced") for f in campus.op.flags)
    assert (_term(campus).start_year, _term(campus).base) == \
        (_term(plain).start_year, _term(plain).base)


@pytest.mark.parametrize("rule", ["fixed", "part_lifetimes"])
def test_a_last_purchase_whose_life_ends_inside_the_axis_is_flagged(rule):
    """The last purchase (2043) + L 12.5 = 2055.5 < 2060; its `service_to`
    (2060, the study horizon) is not read."""
    r = _run(_case(_trf((_renewal(2043, service_to=2060),)), replacement_rule=rule))
    assert f"campus_life_ends_unreplaced:trf:2055:{rule}" in r.op.flags


def test_the_existing_renewal_stage_carries_contingency_and_its_renewals_none():
    """Point 2's choice: an existing asset's first renewal is a P5 stage, paid
    with contingency like every stage; the renewals folded into it are
    replacements, paid without (the IC's replacement rule)."""
    e = _trf((_renewal(2055, service_to=2060),), build_year=2043, service_to=2055,
             existing_renewal=True)
    r = _run(_case(e, asset_cod={"trf": date(2043, 1, 1)}, contingency_share=0.1))
    assert r.op.stage_items[0][3] == pytest.approx(_esc(2042) * 1.1)
    assert _items(r)[0][3] == pytest.approx(_esc(2054))


def test_a_capex_bound_of_1_1_raises_a_renewals_cash_by_ten_percent():
    from services.finance.case import scale_capex

    base = _run(_case(_trf()))
    up = _run(scale_capex(_case(_trf()), 1.1))
    assert [v for *_x, v in _items(up)] == pytest.approx([1.1 * v for *_x, v in _items(base)])


def test_renewals_are_no_table_rows_but_cash_flow_lines():
    """Open item: the report's campus rows list each lineage once (its first
    purchase); each renewal is in the replacement schedule and a cash flow
    line of its own (`<asset>:<part>`)."""
    from services.finance.report import assemble_finance_sections

    case = _case(_trf())
    r = _run(case)
    rep = assemble_finance_sections(r, case)
    rows = rep.sections["project"].payload["extra_assets"]
    assert [row["name"] for row in rows] == ["trf"]
    lines = [ln for ln in rep.cashflow_lines if ln.provenance.source_id == "trf:investment"]
    assert [ln.year for ln in lines] == [2042, 2054]
    assert [ln.amount for ln in lines] == pytest.approx([-_esc(2042), -_esc(2054)])
    assert [(x.year, x.source) for x, _v in r.op.replacement_items] == \
        [(2042, "campus_study"), (2054, "campus_study")]


# ── mp-code-review (R5): the scope of a campus lineage ───────────────────────


def test_a_lineage_without_renewals_takes_a_replacement_capex_entry():
    """Finding 1: the conflict is with the study's renewals; a lineage that has
    none (a Data Center asset) keeps its stated `fixed` entry."""
    r = _run(_case(_trf((), service_to=2031), replacement_capex=[(2042, "trf", 5.0)]))
    assert [(x.year, x.source) for x, _v in r.op.replacement_items] == [(2042, "fixed")]


def test_only_a_part_with_campus_renewals_skips_part_not_replaced():
    """Finding 2, plan a: under `fixed`, a part the study does not renew is
    flagged `part_not_replaced` as in any asset."""
    from services.asset_schema.access import UpfrontPart

    parts = (UpfrontPart("winding", 800.0, 12.5, 0.01), UpfrontPart("tank", 200.0, 20.0, 0.0))
    e = _trf((_renewal(2043, part="winding", amount=1_600.0, service_to=2055),
              _renewal(2055, part="winding", amount=1_600.0, service_to=2060)), parts=parts)
    flags = _run(_case(e)).op.flags
    assert "part_not_replaced:trf:tank" in flags
    assert "part_not_replaced:trf:winding" not in flags


def test_a_purchase_rows_case_keeps_its_hash(hub):  # noqa: F811
    """Issue test 1: the case built from a pre-C14 table (`purchase_rows`) through
    the route hashes as before 04 (the value master gave)."""
    from services import campus_electrical_service as ce
    from services.results.finance_case import finance_case_hash
    from tests.test_campus_electrical_service import fake_investment, purchase_rows

    fake_investment(hub, purchase_rows())
    src = _fold(ce.owner_assets_response(hub, replacements=True))
    assert finance_case_hash(_hash_fixture(extra_assets=tuple(src.extras))) == PURCHASE_ROWS_HASH
