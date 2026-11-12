"""BC-ALL slice 07: presets, offered rows and the DC presets (GK B plan GO, P1–P12).

Spec: .scratch/business-case/spec.md §4, §6.1, §6.2; issue
.scratch/business-case/issues/07-presets-and-dc.md.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest
from pydantic import ValidationError

from models.business_case import (
    BusinessCaseInputs,
    DefaultUnitMismatch,
    LineItem,
)
from services.business_case import presets as P
from services.business_case.linked_outputs import LostProductionMap, Series
from services.finance.business_case import (
    build_business_case,
    business_case_hash,
    kpis,
    pnl,
)
from services.finance.case import TemplateLine
from services.finance.engine import run_case
from services.library.defaults_pack.loader import load_defaults_pack
from tests.test_business_case_engine import PACK, _at, _capex, _ic, _inputs, _line

BACKEND = pathlib.Path(__file__).resolve().parents[1]
DC = ("eh_datacenter", "eh_datacenter_staged")
VARIANTS = ("gpu_cloud", "colocation")
CAP = {"pv": 2.0, "battery": 1.0}


def _all_lines():
    for t in DC:
        for v in VARIANTS:
            for i, ln in enumerate(P.preset(t, v, currency="EUR").lines):
                yield f"{t}.{v}[{i}]", ln
    for o in P.offered_rows("eh_site"):
        yield f"eh_site.offered[{o.label}]", o.line


# ── what a preset holds (spec §4, §6; P9) ────────────────────────────────────


def test_every_preset_and_offered_row_validates_under_02():
    rows = list(_all_lines())
    assert len(rows) == 2 * 18 + 10
    for where, ln in rows:
        assert LineItem.model_validate(ln.model_dump()) == ln, where


def test_every_default_is_sourced_ranged_assumed_and_stamped():
    pack = load_defaults_pack("2026-11-10")
    seen = 0
    for where, ln in _all_lines():
        assert set(ln.assumed) <= set(ln.default_keys), where
        for col, d in ln.assumed.items():
            row = pack.value(d.key)
            assert d.status == "assumed" and d.source == row.source != "", where
            assert d.source_year == row.source_year and d.pack == pack.stamp, where
            assert d.range_low is not None and d.range_high is not None, where
            assert d.range_low <= d.value <= d.range_high, where
            assert d.unit == ln.default_units[col], where
            seen += 1
    assert seen == 2 * (
        7 + 6
    )  # gpu_cloud: facility 2, IT 1, 4 shares; colocation 2 + 4


def test_no_revenue_row_has_a_price_or_a_default():
    for where, ln in _all_lines():
        if ln.kind == "revenue":
            assert ln.price is None and ln.price_by_year == {}, where
            assert ln.default_keys == {} and ln.assumed == {}, where


def test_each_dc_revenue_row_has_a_hint_never_a_value():
    """BCA-SCOPE-1 Q1. R3's price is the project's own tariff: no public range."""
    for t in DC:
        for v in VARIANTS:
            for ln in P.preset(t, v, currency="EUR").lines:
                if ln.kind != "revenue" or ln.category == "cost_recovery":
                    assert ln.hint is None
                    continue
                h = ln.hint
                assert h is not None and h.source and 2020 <= h.year <= 2026
                assert h.low <= h.high and ln.price is None


def test_p9_nothing_unsourced_gets_a_default():
    """eh_site's shares and DC staff and network are typed: no borrowed DC row."""
    for o in P.offered_rows("eh_site"):
        assert o.line.default_keys == {} and o.line.assumed == {}
    for v in VARIANTS:
        c = P.preset("eh_datacenter", v, currency="EUR")
        for ln in c.lines:
            if ln.category in ("staff", "network"):
                assert ln.price is None and ln.default_keys == {}
    refused = [
        (r.kind, r.field)
        for r in BusinessCaseInputs(
            lines=[
                o.line
                for o in P.offered_rows("eh_site")
                if o.line.category in ("maintenance", "facility")
            ]
        ).refusals(horizon=(2031, 2035), base_year=2031)
    ]
    assert ("quantity_missing", "lines[0].quantity") in refused


def test_the_dc_rows_are_spec_6_2():
    g = P.preset("eh_datacenter", "gpu_cloud", currency="EUR")
    c = P.preset("eh_datacenter", "colocation", currency="EUR")
    assert g.preset == "eh_datacenter.gpu_cloud@1"
    assert c.preset == "eh_datacenter.colocation@1"
    assert P.preset("eh_datacenter_staged", "gpu_cloud", currency="EUR") == g
    r1, r2, r3 = g.lines[0], c.lines[0], c.lines[1]
    assert (r1.category, r1.unit, r1.quantity_source) == (
        "service_fee",
        "GPU_h",
        "linked:it_energy@1",
    )
    assert r1.preset_rule.requires == ["it_hardware", "facility"]
    assert r1.preset_rule.price_includes_energy
    assert (r2.category, r2.unit, r2.quantity_source) == (
        "lease",
        "kW_month",
        "linked:contracted_it_kw@1",
    )
    assert r2.preset_rule.requires == ["facility"]
    assert r2.preset_rule.excludes == ["it_hardware"]
    assert (r3.category, r3.unit, r3.source) == ("cost_recovery", "MWh", "typed")
    fac = g.lines[1]
    assert (fac.unit, fac.price, fac.money_year, fac.life_years) == (
        "MW",
        9.469e6,
        2025,
        20.0,
    )
    it = g.lines[2]
    assert (it.category, it.life_years, it.price) == ("it_hardware", 5.0, None)
    assert not any(ln.category == "it_hardware" for ln in c.lines)
    shares = {ln.category: (ln.quantity_source, ln.quantity) for ln in g.lines[5:]}
    assert shares == {
        "maintenance": ("share_of:capex/facility", 0.015),
        "insurance": ("share_of:capex", 0.005),
        "property_tax": ("share_of:capex", 0.01),
        "sga": ("share_of:revenue", 0.15),
    }


def test_another_currency_leaves_the_facility_price_not_established():
    g = P.preset("eh_datacenter", "gpu_cloud", currency="USD")
    assert g.lines[1].price is None and "price" not in g.lines[1].assumed
    assert g.lines[1].life_years == 20.0  # a life has no currency


def test_eh_site_is_a_blank_table_with_the_offered_rows_of_6_1():
    assert P.preset("eh_site", currency="EUR") == BusinessCaseInputs()
    assert P.variants("eh_site") == {}
    rows = P.offered_rows("eh_site")
    assert [(o.line.kind, o.line.category) for o in rows] == [
        ("revenue", "product_sale"),
        ("revenue", "service_fee"),
        ("revenue", "capacity_payment"),
        ("opex", "staff"),
        ("opex", "maintenance"),
        ("opex", "insurance"),
        ("opex", "property_tax"),
        ("opex", "sga"),
        ("capex", "facility"),
        ("opex", "land"),
    ]
    assert rows[0].units == ["t", "unit"] and rows[2].sources == [
        "typed",
        "linked:capacity",
    ]
    rows[0].line.name = "edited"
    assert P.offered_rows("eh_site")[0].line.name == "Product sales"


def test_applying_copies_so_an_edit_stays_in_the_case():
    a = P.preset("eh_datacenter", "gpu_cloud", currency="EUR")
    a.lines[0].name = "mine"
    assert (
        P.preset("eh_datacenter", "gpu_cloud", currency="EUR").lines[0].name != "mine"
    )


# ── G7 through the preset (spec §3) ──────────────────────────────────────────


def _filled(variant: str) -> BusinessCaseInputs:
    """The preset with every user column typed (#1320's fixture numbers)."""
    c = P.preset("eh_datacenter", variant, currency="EUR")
    typed = {
        "GPU-hours": {"price": 2.5, "indexation": -0.05, "linked_share": 0.8},
        "Colocation lease": {
            "price": 180.0,
            "indexation": 0.02,
            "linked_share_by_year": {2031: 0.5, 2032: 0.75},
        },
        "Power pass-through": {"quantity": 40_000.0, "price": 120.0},
        "Facility, shell and M&E": {"quantity": 10.0},
        "IT hardware": {"quantity": 1000.0, "price": 30_000.0},
        "Staff": {"price": 4.0},
        "Network and bandwidth": {"price": 1.5},
    }
    lines = [
        ln.model_copy(update=typed.get(ln.name, {})) for ln in c.lines
    ]  # model_copy skips validation: re-validate below
    return BusinessCaseInputs.model_validate(
        {**c.model_dump(), "lines": [ln.model_dump() for ln in lines]}
    )


def _kinds(case: BusinessCaseInputs) -> list[tuple[str, str]]:
    return [
        (r.kind, r.field) for r in case.refusals(horizon=(2031, 2035), base_year=2031)
    ]


def test_a_filled_preset_has_no_refusal():
    for v in VARIANTS:
        assert _kinds(_filled(v)) == [], v


def test_g7_gpu_cloud_without_it_hardware_is_it_capex_missing():
    c = _filled("gpu_cloud")
    c.lines = [ln for ln in c.lines if ln.category != "it_hardware"]
    assert _kinds(c) == [
        ("it_capex_missing", "lines[0].preset_rule.requires[it_hardware]")
    ]


def test_g7_colocation_with_it_hardware_is_capex_not_owned():
    c = _filled("colocation")
    it = _filled("gpu_cloud").lines[2]
    c.lines.append(it)
    assert _kinds(c) == [("capex_not_owned", f"lines[{len(c.lines) - 1}].category")]


def test_g7_a_pass_through_beside_gpu_hours_is_refused():
    c = _filled("gpu_cloud")
    c.lines.append(_filled("colocation").lines[1])
    assert _kinds(c) == [
        ("pass_through_price_includes_energy", f"lines[{len(c.lines) - 1}].category")
    ]


# ── parity: a preset is just pre-filled lines (spec §4) ──────────────────────


def _outputs(it_gpu_h: float = 1.0e6, kw: float = 5000.0) -> dict:
    gpu = Series("it_energy", "GPU_h", {None: it_gpu_h}, {})
    ckw = Series("contracted_it_kw", "kW_month", {None: kw}, {})
    return {
        "GPU-hours": gpu,
        "Colocation lease": ckw,
        "Staff": ckw,
        "Network and bandwidth": ckw,
    }


def _hand(variant: str) -> BusinessCaseInputs:
    """The same lines typed by hand, with no preset, no hint and no default record."""
    rows = []
    for ln in _filled(variant).lines:
        d = ln.model_dump()
        d.update(hint=None, assumed={}, default_keys={}, default_units={})
        rows.append(d)
    return BusinessCaseInputs.model_validate({"lines": rows})


@pytest.mark.parametrize("variant", VARIANTS)
def test_parity_a_preset_runs_as_the_same_lines_typed_by_hand(variant):
    out = _outputs()
    a = run_case(build_business_case(_ic(), _filled(variant), out).case, PACK)
    b = run_case(build_business_case(_ic(), _hand(variant), out).case, PACK)
    pa, pb = pnl(a), pnl(b)
    assert pa.rows.keys() == pb.rows.keys()
    for k in pa.rows:
        np.testing.assert_array_equal(pa.rows[k], pb.rows[k], err_msg=k)
    ka, kb = kpis(a, pa, capacity=CAP), kpis(b, pb, capacity=CAP)
    assert {k: v.value for k, v in ka.items()} == {k: v.value for k, v in kb.items()}


def test_parity_r1_is_gpu_hours_times_sold_share_times_price_by_hand():
    out = _outputs()
    r = run_case(build_business_case(_ic(), _filled("gpu_cloud"), out).case, PACK)
    for y in range(2031, 2036):
        assert r.op.lines["GPU-hours"][_at(r, y)] == pytest.approx(
            1.0e6 * 0.8 * 2.5 * (1 - 0.05) ** (y - 2031)
        )


def test_parity_r2_is_contracted_kw_times_lease_up_times_price_by_hand():
    out = _outputs()
    r = run_case(build_business_case(_ic(), _filled("colocation"), out).case, PACK)
    share = {2031: 0.5, 2032: 0.75}
    for y in range(2031, 2036):
        assert r.op.lines["Colocation lease"][_at(r, y)] == pytest.approx(
            5000.0 * share.get(y, 1.0) * 180.0 * 1.02 ** (y - 2031)
        )


def test_parity_the_facility_default_is_2025_money_converted_at_the_capex_rate():
    bc = build_business_case(_ic(), _filled("gpu_cloud"), _outputs())
    fac = next(a for a in bc.case.assets if a.name == "Facility, shell and M&E")
    assert fac.overnight_cost == pytest.approx(10.0 * 9.469e6 * 1.02 ** (2031 - 2025))
    dep = bc.case.inputs.depreciation_class_by_asset
    assert dep["Facility, shell and M&E"] == "slm_20" and dep["IT hardware"] == "slm_5"


# ── P1: the digest ───────────────────────────────────────────────────────────


def _pin_case(**over) -> BusinessCaseInputs:
    from models.business_case import CostDefault

    d = CostDefault(
        key="dc-business-case.facility.lifetime",
        value=20.0,
        unit="years",
        source="assumed; x",
        source_year=2026,
        pack="generic_defaults@2026-11-10:sha256:e228",
        **over.pop("range", {}),
    )
    nh3 = dict(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")
    nh3.update(over.pop("nh3", {}))
    rev = over.pop("rev", {})
    return _inputs(
        _line(**rev),
        _line(**nh3),
        _capex(
            life_years=20.0,
            assumed={"life_years": d},
            **over.pop("capex", {}),
        ),
        _line(
            name="maint",
            kind="opex",
            category="maintenance",
            unit="share",
            quantity_source="share_of:capex",
            quantity=0.015,
            price=None,
        ),
        preset="eh_datacenter.gpu_cloud@1",
        receivable_days=30,
    )


_NH3 = {"nh3": Series("served_product.nh3", "t", {None: 100.0}, {})}
#: business_case_hash of `_pin_case()` on master 4a4c8f45, before BC 07's fields.
PINNED = "6a7c8349f6fda23c406495e9e48cc4d8"


def test_p1_a_digest_from_before_bc07_is_byte_equal():
    assert business_case_hash(_ic(), _pin_case(), _NH3) == PINNED


def test_p1_a_hint_or_a_range_never_moves_the_digest():
    hint = {"low": 1.0, "high": 2.0, "unit": "USD/t", "source": "s", "year": 2025}
    assert business_case_hash(_ic(), _pin_case(rev={"hint": hint}), _NH3) == PINNED
    rng = {"range": {"range_low": 15.0, "range_high": 30.0}}
    assert business_case_hash(_ic(), _pin_case(**rng), _NH3) == PINNED


@pytest.mark.parametrize(
    "nh3", [{"linked_share": 0.5}, {"linked_share_by_year": {2032: 0.5}}]
)
def test_p1_a_linked_share_moves_the_digest(nh3):
    assert business_case_hash(_ic(), _pin_case(nh3=nh3), _NH3) != PINNED


def test_p1_default_units_enter_beside_their_keys():
    key = {"default_keys": {"life_years": "dc-business-case.facility.lifetime"}}
    a = _pin_case(capex={**key, "default_units": {"life_years": "years"}})
    b = _pin_case(capex={**key, "default_units": {"life_years": "yr"}})
    assert business_case_hash(_ic(), b, _NH3) != business_case_hash(_ic(), a, _NH3)


# ── P2: a hint is display only ───────────────────────────────────────────────


def test_p2_a_hint_changes_no_cashflow_pnl_or_kpi():
    hint = {"low": 1.0, "high": 2.0, "unit": "USD/t", "source": "s", "year": 2025}
    a = run_case(build_business_case(_ic(), _inputs(_line()), {}).case, PACK)
    b = run_case(build_business_case(_ic(), _inputs(_line(hint=hint)), {}).case, PACK)
    pa, pb = pnl(a), pnl(b)
    for k in pa.rows:
        np.testing.assert_array_equal(pa.rows[k], pb.rows[k], err_msg=k)
    assert {k: v.value for k, v in kpis(a, pa, capacity=CAP).items()} == {
        k: v.value for k, v in kpis(b, pb, capacity=CAP).items()
    }


@pytest.mark.parametrize(
    "kw, loc",
    [
        (dict(kind="opex", category="staff", hint={}), "hint"),
        (
            dict(hint={"low": 3.0, "high": 2.0, "unit": "u", "source": "s", "year": 1}),
            "hint",
        ),
        (
            dict(hint={"low": 1.0, "high": 2.0, "unit": "u", "source": "", "year": 1}),
            "hint",
        ),
        (
            dict(
                hint={
                    "low": 1.0,
                    "high": 2.0,
                    "unit": "u",
                    "source": "s",
                    "year": 1,
                    "value": 1,
                }
            ),
            "hint",
        ),
    ],
)
def test_p2_a_hint_is_revenue_only_ordered_and_sourced(kw, loc):
    base = {"low": 1.0, "high": 2.0, "unit": "u", "source": "s", "year": 2025}
    if kw.get("hint") == {}:
        kw = {**kw, "hint": base}
    with pytest.raises(ValidationError) as e:
        BusinessCaseInputs(lines=[{**_line().model_dump(), **kw}])
    assert e.value.errors()[0]["loc"][:3] == ("lines", 0, loc)


# ── P3: linked_share ─────────────────────────────────────────────────────────

_LINKED = dict(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")


@pytest.mark.parametrize(
    "kw, loc",
    [
        ({"linked_share": 1.01}, "linked_share"),
        ({"linked_share": -0.01}, "linked_share"),
        ({"linked_share": math.nan}, "linked_share"),
        ({"linked_share": math.inf}, "linked_share"),
        ({"linked_share_by_year": {2032: 1.5}}, "linked_share_by_year"),
        ({"linked_share_by_year": {2032: math.nan}}, "linked_share_by_year"),
    ],
)
def test_p3_a_share_is_finite_in_0_1(kw, loc):
    with pytest.raises(ValidationError) as e:
        BusinessCaseInputs(lines=[{**_line(**_LINKED).model_dump(), **kw}])
    assert e.value.errors()[0]["loc"][:3] == ("lines", 0, loc)


@pytest.mark.parametrize(
    "kw, loc",
    [
        ({"linked_share": 0.5}, "linked_share"),
        ({"linked_share_by_year": {2032: 0.5}}, "linked_share_by_year"),
    ],
)
def test_p3_a_typed_or_share_line_takes_no_linked_share(kw, loc):
    with pytest.raises(ValidationError) as e:
        BusinessCaseInputs(lines=[{**_line().model_dump(), **kw}])
    assert e.value.errors()[0]["loc"][:3] == ("lines", 0, loc)


def test_p3_boundaries_run_and_a_year_outside_the_span_is_refused():
    for s in (0.0, 1.0):
        BusinessCaseInputs(lines=[_line(**_LINKED, linked_share=s)])
    c = _inputs(_line(**_LINKED, linked_share_by_year={2036: 0.5, 2031: 0.2}))
    assert _kinds(c) == [("year_out_of_range", "lines[0].linked_share_by_year[2036]")]


def test_p3_a_cell_wins_over_the_scalar_and_none_reads_one():
    ln = _line(**_LINKED, linked_share=0.5, linked_share_by_year={2032: 0.25})
    assert ln.linked_share_at(2031) == 0.5 and ln.linked_share_at(2032) == 0.25
    assert _line(**_LINKED).linked_share_at(2031) == 1.0
    assert _line(**_LINKED, linked_share=0.0).linked_share_at(2031) == 0.0
    r = run_case(build_business_case(_ic(), _inputs(ln), _NH3).case, PACK)
    full = run_case(
        build_business_case(_ic(), _inputs(_line(**_LINKED)), _NH3).case, PACK
    )
    for y in range(2031, 2036):
        i = _at(r, y)
        want = {2032: 0.25}.get(y, 0.5)
        assert r.op.lines["nh3"][i] == pytest.approx(want * full.op.lines["nh3"][i])
        assert full.op.lines["nh3"][i] == pytest.approx(
            100.0 * 50.0 * 1.02 ** (y - 2031)
        )


def test_p3_a_not_evaluated_series_stays_not_established_with_a_share():
    from services.business_case.linked_outputs import NotEvaluated

    ln = _line(**_LINKED, linked_share=0.0)
    bc = build_business_case(
        _ic(), _inputs(ln), {"nh3": NotEvaluated("served_product.nh3", "x")}
    )
    amounts = [
        tl.amount for t in bc.case.templates for tl in t.lines if tl.key == "nh3"
    ]
    assert amounts and all(a is None for a in amounts)


# ── P4: the share composes with the G3 drop ──────────────────────────────────


def _lost_ic():
    lp = TemplateLine(
        "asset:opex:Generator:__lost_h2",
        "lost_production",
        -50.0,
        "fuel",
        source="asset",
        source_id="opex:Generator:__lost_h2",
    )
    return lp, _ic(lines=(lp, TemplateLine("fom", "fom", -20.0, "opex")))


def test_p4_a_sold_share_scales_the_revenue_and_never_the_drop():
    """served_h2 on one load with a lost share f = 0.4 and a sold share s = 0.5: the
    revenue line books series × s × price; the lost-production line keeps (1 − f) of
    its amount, whatever s is."""
    lp, ic = _lost_ic()
    lpm = LostProductionMap(
        slack_bus={"__lost_h2": "h2"},
        load_bus={"offtake": "h2"},
        share={"__lost_h2": {"offtake": Series("x", "share", {None: 0.4}, {})}},
        unread={},
    )
    s = {"h2": Series("served_h2.offtake", "kg", {None: 1000.0}, {})}

    def line(**kw):
        return _line(
            name="h2",
            unit="kg",
            quantity=None,
            quantity_source="linked:served_h2.offtake@1",
            price=4.0,
            **kw,
        )

    for share in (None, 0.5):
        kw = {} if share is None else {"linked_share": share}
        bc = build_business_case(ic, _inputs(line(**kw)), s, lpm)
        r = run_case(bc.case, PACK)
        for y in range(2031, 2036):
            i = _at(r, y)
            assert r.op.lines["h2"][i] == pytest.approx(
                1000.0 * (share or 1.0) * 4.0 * 1.02 ** (y - 2031)
            )
        lost = [
            tl.amount
            for t in bc.case.templates
            if t.first_year >= 2031  # the construction year carries no BC line
            for tl in t.lines
            if tl.key == lp.key
        ]
        assert lost and all(a == pytest.approx(-50.0 * (1 - 0.4)) for a in lost)


def test_p4_a_whole_bus_drop_is_whole_with_any_share():
    lp, ic = _lost_ic()
    lpm = LostProductionMap(
        slack_bus={"__lost_h2": "h2"}, load_bus={}, share={"__lost_h2": {}}, unread={}
    )
    ln = _line(
        name="h2",
        quantity=None,
        quantity_source="linked:served_product.h2@1",
        linked_share=0.3,
    )
    bc = build_business_case(
        ic, _inputs(ln), {"h2": Series("served_product.h2", "t", {None: 10.0}, {})}, lpm
    )
    keys = {tl.key for t in bc.case.templates if t.first_year >= 2031 for tl in t.lines}
    assert "h2" in keys and lp.key not in keys


# ── P5: MW ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        lambda: _line(unit="MW"),
        lambda: _line(kind="opex", category="staff", unit="MW"),
        lambda: _capex(
            unit="MW", quantity=None, quantity_source="linked:capacity.Generator.pv@1"
        ),
    ],
)
def test_p5_mw_is_refused_off_a_typed_capex_line(line):
    assert ("unit_not_allowed", "lines[0].unit") in _kinds(_inputs(line()))


def test_p5_mw_runs_on_a_typed_capex_line():
    assert _kinds(_inputs(_capex(unit="MW", quantity=10.0))) == []


# ── P6: a default's unit ─────────────────────────────────────────────────────


class _UnitPack:
    """The 2026-11-10 pack with one row's unit changed (a later pack's edit)."""

    def __init__(self, key: str, unit: str):
        self._p = load_defaults_pack("2026-11-10")
        self._key, self._unit = key, unit
        self.stamp = self._p.stamp

    def value(self, key):
        row = self._p.value(key)
        return row.model_copy(update={"unit": self._unit}) if key == self._key else row


def test_p6_a_pack_row_in_another_unit_is_refused_and_never_filled():
    c = P.preset("eh_datacenter", "gpu_cloud", currency="EUR")
    blank = c.model_copy(deep=True)
    blank.lines[1] = blank.lines[1].model_copy(
        update={"price": None, "money_year": None, "assumed": {}}
    )
    pack = _UnitPack("dc-business-case.facility.overnight", "EUR/kW")
    with pytest.raises(DefaultUnitMismatch) as e:
        blank.with_cost_defaults(pack, currency="EUR")
    assert (e.value.kind, e.value.field) == (
        "default_unit_mismatch",
        "lines[1].default_keys[price]",
    )
    assert blank.lines[1].price is None
    # The right unit fills.
    ok = blank.with_cost_defaults(_UnitPack("x", "y"), currency="EUR")
    assert ok.lines[1].price == 9.469e6


def test_p6_default_units_must_name_the_default_keys_columns():
    for units in ({}, {"life_years": "years", "price": "EUR/MW"}):
        with pytest.raises(ValidationError, match="default_units"):
            _capex(
                life_years=None,
                default_keys={"life_years": "k"},
                default_units=units,
            )


def test_p6_a_revenue_line_takes_no_default_units():
    with pytest.raises(ValidationError) as e:
        BusinessCaseInputs(
            lines=[{**_line().model_dump(), "default_units": {"price": "x"}}]
        )
    assert e.value.errors()[0]["loc"][:3] == ("lines", 0, "default_units")


# ── P7: the loader ───────────────────────────────────────────────────────────


@pytest.fixture
def presets_dir(tmp_path):
    d = tmp_path / "business_case_presets"
    shutil.copytree(P.PRESETS_DIR, d)
    return d


def _edit(d: pathlib.Path, name: str, fn) -> None:
    p = d / f"{name}.json"
    data = json.loads(p.read_text())
    fn(data)
    p.write_text(json.dumps(data))


@pytest.mark.parametrize(
    "fn",
    [
        lambda d: d.update(extra=1),
        lambda d: d.update(version="2"),
        lambda d: d.update(id="eh_other"),
        lambda d: d["variants"]["gpu_cloud"]["lines"][0].update(price_typo=1.0),
        lambda d: d["variants"]["gpu_cloud"]["lines"][1]["default_units"].pop("price"),
        lambda d: d["variants"]["gpu_cloud"]["lines"][0].update(price=float("nan")),
        lambda d: d.update(pack=d["pack"][:-1] + "0"),
    ],
)
def test_p7_a_broken_file_is_refused_at_call(presets_dir, fn):
    _edit(presets_dir, "eh_datacenter", fn)
    with pytest.raises(P.PresetInvalid):
        P.preset("eh_datacenter", "gpu_cloud", currency="EUR", directory=presets_dir)


def test_p7_a_file_that_is_not_json_is_refused(presets_dir):
    (presets_dir / "eh_site.json").write_text("{not json")
    with pytest.raises(P.PresetInvalid):
        P.offered_rows("eh_site", directory=presets_dir)


def test_p7_an_offered_line_outside_its_units_is_refused(presets_dir):
    _edit(presets_dir, "eh_site", lambda d: d["offered"][0].update(units=["unit"]))
    with pytest.raises(P.PresetInvalid):
        P.offered_rows("eh_site", directory=presets_dir)


def test_p7_unknown_template_or_variant_is_a_lookup_error():
    with pytest.raises(LookupError):
        P.preset("eh_unknown", currency="EUR")
    with pytest.raises(LookupError):
        P.preset("eh_datacenter", "retail", currency="EUR")
    with pytest.raises(LookupError):
        P.preset("eh_datacenter", currency="EUR")  # a DC preset names its variant
    with pytest.raises(LookupError):
        P.preset("eh_site", "gpu_cloud", currency="EUR")


def test_p7_a_template_in_two_files_is_refused(presets_dir):
    _edit(presets_dir, "eh_site", lambda d: d["templates"].append("eh_datacenter"))
    with pytest.raises(LookupError):
        P.variants("eh_datacenter", directory=presets_dir)


def test_p7_the_models_import_with_a_broken_presets_dir(tmp_path):
    """Nothing is read at import: a broken file cannot stop the app."""
    code = (
        "import pathlib, services.business_case.presets as P, models.business_case;"
        f"P.PRESETS_DIR = pathlib.Path({str(tmp_path)!r});"
        "print('ok')"
    )
    (tmp_path / "eh_site.json").write_text("{broken")
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.stdout.strip() == "ok", out.stderr[-2000:]


# ── P8: the pinned pack ──────────────────────────────────────────────────────


def test_p8_each_preset_pins_the_2026_11_10_pack_byte_equal():
    stamp = load_defaults_pack("2026-11-10").stamp
    for f in sorted(P.PRESETS_DIR.glob("*.json")):
        assert json.loads(f.read_text())["pack"] == stamp, f.name
    hashes = json.loads(
        (BACKEND / "tests/fixtures/defaults_pack/pack_hashes.json").read_text()
    )
    assert stamp.endswith(hashes["generic_defaults"]["2026-11-10"]), (
        "2026-11-10 changed"
    )


#: Unpinned `load_defaults_pack()` callers in the business-case code (GK B P8). None:
#: the presets pin their pack; BC 08's route will choose its own and list it here.
UNPINNED_BC_CALLERS: list[str] = []


def test_p8_unpinned_load_defaults_pack_callers_are_listed():
    files = [*(BACKEND / "services/business_case").glob("*.py")]
    files += [
        BACKEND / "services/finance/business_case.py",
        BACKEND / "models/business_case.py",
    ]
    found = sorted(
        str(f.relative_to(BACKEND))
        for f in files
        if re.search(r"load_defaults_pack\(\s*(\)|region)", f.read_text())
    )
    assert found == UNPINNED_BC_CALLERS
