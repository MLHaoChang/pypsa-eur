# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""BC-ALL slice 12: the IPP hybrid and microgrid presets (spec §6.6, §6.7).

Locked ledger streams are never lines (G2); unserved microgrid load earns no tariff and no
VoLL appears (B4); GK B ruling C3 puts the EaaS-fee help in the preset."""

from __future__ import annotations

import pytest

from models.business_case import LineItem
from services.business_case import linked_outputs as L
from services.business_case import presets as P
from services.finance.business_case import DoubleCount, build_business_case, pnl
from services.finance.case import TemplateLine
from services.finance.engine import run_case
from tests.test_business_case_engine import PACK, _ic, _inputs, _line
from tests.test_business_case_guards import OPS, _by_year
from tests.test_business_case_linked_outputs import _built

IPP = "eh_ipp_hybrid"
MG = ("eh_microgrid", "eh_microgrid_staged")
CASES = {IPP: "ipp_hybrid", MG[0]: "microgrid", MG[1]: "microgrid"}


def _ids(template: str) -> set[str]:
    n, solve = _built(template)
    return {s.id for s in L.catalogue(n, solve)}


def _linked_ids(template: str) -> set[str]:
    rows = P.preset(template, CASES[template], currency="EUR").lines + [
        o.line for o in P.offered_rows(template)
    ]
    return {
        ln.quantity_source.removeprefix("linked:").rpartition("@")[0]
        for ln in rows
        if ln.quantity_source.startswith("linked:")
    }


def test_the_ipp_rows_are_spec_6_6():
    case = P.preset(IPP, "ipp_hybrid", currency="EUR")
    by = {ln.name: ln for ln in case.lines}
    assert case.preset == "eh_ipp_hybrid.ipp_hybrid@1"
    cap = by["Capacity payment (BESS, firm capacity)"]
    assert (cap.kind, cap.category, cap.unit) == (
        "revenue",
        "capacity_payment",
        "kW_year",
    )
    assert cap.quantity_source == "linked:capacity.StorageUnit.bess@1"
    # linked_share is the contracted share only; the de-rating lives in the typed price (#1430 F2).
    assert (
        cap.linked_share is None and "de-rating" in cap.note and "nameplate" in cap.note
    )
    goo = by["Guarantees of origin"]
    assert goo.quantity_source == "typed" and goo.unit == "MWh"
    assert "never above the renewable generation" in goo.note
    assert (goo.hint.low, goo.hint.high, goo.hint.unit) == (0.3, 2.0, "EUR/MWh")
    assert (cap.hint.low, cap.hint.high, cap.hint.unit) == (20.0, 60.0, "GBP/kW_year")
    leases = [ln for ln in case.lines if ln.category == "land"]
    assert {ln.quantity_source for ln in leases} == {
        "linked:capacity.Generator.wind_farm@1",
        "linked:capacity.Generator.pv_plant@1",
    }
    assert all(ln.unit == "MW_year" and ln.price is None for ln in leases)
    assert by["Insurance"].quantity_source == "share_of:capex"
    # Revenue prices are never a default; PPAs, export and CfD are not lines (G2).
    assert all(ln.price is None for ln in case.lines if ln.kind == "revenue")
    assert not [
        ln for ln in case.lines if "ppa" in ln.name.lower() or "cfd" in ln.name.lower()
    ]
    assert not [
        ln for ln in case.lines if ln.quantity_source.startswith("linked:export")
    ]
    assert not [ln for ln in case.lines if ln.assumed]


@pytest.mark.parametrize("template", MG)
def test_the_microgrid_rows_are_spec_6_7(template):
    case = P.preset(template, "microgrid", currency="EUR")
    by = {ln.name: ln for ln in case.lines}
    assert case.preset == "eh_microgrid.microgrid@1"
    for name, load in (
        ("Energy tariff, residential", "residential"),
        ("Energy tariff, hospital", "hospital_load"),
    ):
        ln = by[name]
        assert (ln.kind, ln.category, ln.unit) == ("revenue", "tariff", "MWh")
        assert ln.quantity_source == f"linked:served_load.{load}@1"
        assert ln.price is None
    # The EaaS fee is off by default: an offered row, typed, never a line.
    assert not [ln for ln in case.lines if ln.category == "service_fee"]
    (eaas,) = [o for o in P.offered_rows(template) if o.line.category == "service_fee"]
    assert (eaas.line.unit, eaas.line.quantity_source) == ("unit_month", "typed")
    # GK B ruling C3: the help names the locked rows and says they stop duplication.
    assert (
        "from the energy model" in eaas.line.note
        and "stop duplication" in eaas.line.note
    )
    assert {ln.category for ln in case.lines if ln.kind == "opex"} == {
        "staff",
        "maintenance",
        "insurance",
    }


@pytest.mark.parametrize("template", CASES)
def test_every_linked_id_exists_on_the_built_template(template):
    ids = _linked_ids(template)
    assert ids and ids <= _ids(template)


def test_g2_a_linked_export_line_on_the_ipp_is_refused_double_count():
    assert "export.grid_export" in _ids(
        IPP
    )  # the catalogue offers it; the ledger prices it
    ic = _ic(lines=(TemplateLine("export", "energy_export", 120.0, "export"),))
    ln = _line(
        name="GOs on export",
        category="other",
        unit="MWh",
        quantity=None,
        quantity_source="linked:export.grid_export@1",
    )
    s = L.Series("export.grid_export", "MWh", {None: 10.0}, {})
    with pytest.raises(DoubleCount) as e:
        build_business_case(ic, _inputs(ln), {ln.name: s})
    assert e.value.stream == "energy_export"


def _ipp_filled() -> list[LineItem]:
    rows = []
    for ln in P.preset(IPP, "ipp_hybrid", currency="USD").lines:
        d = ln.model_dump()
        if ln.kind == "revenue":
            d.update(price=10.0, indexation=0.0)
            if ln.quantity_source == "typed":
                d.update(quantity=100.0)
        elif ln.kind == "capex":
            d.update(quantity=1.0, price=1000.0, life_years=10.0)
        elif ln.category == "insurance":
            d.update(quantity=0.01)
        else:
            d.update(price=5.0)
        rows.append(LineItem.model_validate(d))
    return rows


def test_the_ipp_business_case_revenue_includes_the_ppa_exactly_once():
    ppa = TemplateLine("asset:ppa_wind", "ppa_settlement", 5000.0, "ppa")
    ic = _ic(lines=(ppa, TemplateLine("fom", "fom", -20.0, "opex")), analysis_years=8)
    base = build_business_case(ic, _inputs(), {})
    rows = _ipp_filled()
    series = {
        "Capacity payment (BESS, firm capacity)": L.Series(
            "capacity.StorageUnit.bess", "kW_year", {None: 10000.0}, {}
        ),
        "Land lease, wind farm": L.Series(
            "capacity.Generator.wind_farm", "MW_year", {None: 40.0}, {}
        ),
        "Land lease, PV plant": L.Series(
            "capacity.Generator.pv_plant", "MW_year", {None: 60.0}, {}
        ),
    }
    bc = build_business_case(ic, _inputs(*rows), series)
    r, r0 = run_case(bc.case, PACK), run_case(base.case, PACK)
    for y in OPS:
        keys = _by_year(bc)[y]
        assert [k for k, v in keys.items() if v.stream == "ppa_settlement"] == [
            "asset:ppa_wind"
        ]
        assert (
            keys["asset:ppa_wind"].amount == _by_year(base)[y]["asset:ppa_wind"].amount
        )
    typed = {ln.name: ln for ln in rows}
    # Revenue = the locked PPA + the two preset revenue lines; the PPA is not added again.
    i = [int(v) for v in r.tl.years].index(2031)
    added = pnl(r).rows["revenue"][i] - pnl(r0).rows["revenue"][i]
    assert added > 0
    gos = typed["Guarantees of origin"]
    cap = typed["Capacity payment (BESS, firm capacity)"]
    assert added == pytest.approx(
        cap.price * 10000.0 + gos.price * gos.quantity, rel=0.05
    )


def test_b4_unserved_microgrid_load_earns_no_tariff_and_no_voll_appears():
    case = P.preset("eh_microgrid", "microgrid", currency="USD")
    rows = []
    for ln in case.lines:
        d = ln.model_dump()
        if ln.kind == "revenue":
            d.update(price=100.0, indexation=0.0)
        rows.append(LineItem.model_validate(d))
    revenue = [r for r in rows if r.kind == "revenue"]
    full = {
        r.name: L.Series(r.quantity_source[7:-2], "MWh", {None: 1000.0}, {})
        for r in revenue
    }
    short = {
        r.name: L.Series(r.quantity_source[7:-2], "MWh", {None: 600.0}, {})
        for r in revenue
    }
    ic = _ic(lines=(TemplateLine("fom", "fom", -20.0, "opex"),), analysis_years=8)
    a = build_business_case(ic, _inputs(*revenue), full)
    b = build_business_case(ic, _inputs(*revenue), short)
    for y in OPS:
        for name in full:
            assert _by_year(b)[y][name].amount == pytest.approx(
                _by_year(a)[y][name].amount * 0.6
            )
        # The 400 MWh unserved is priced by no line: no VoLL, shed or lost-load row.
        assert not [
            k
            for k in _by_year(b)[y]
            if any(w in k.lower() for w in ("voll", "lost_load", "shed", "lost_"))
        ]


def test_c3_a_served_load_line_against_a_valued_eaas_fee_is_refused():
    ic = _ic(lines=(TemplateLine("eaas", "eaas_fee", 100.0, "tariff"),))
    ln = _line(
        name="Tariff",
        category="tariff",
        unit="MWh",
        quantity=None,
        quantity_source="linked:served_load.residential@1",
    )
    s = L.Series("served_load.residential", "MWh", {None: 10.0}, {})
    with pytest.raises(DoubleCount) as e:
        build_business_case(ic, _inputs(ln), {ln.name: s})
    assert e.value.stream == "eaas_fee"


def _filled(template: str) -> list[LineItem]:
    if template == IPP:
        return _ipp_filled()
    rows = []
    for ln in P.preset(template, "microgrid", currency="USD").lines:
        d = ln.model_dump()
        if ln.kind == "revenue":
            d.update(price=100.0, indexation=0.0)
        elif ln.kind == "capex":
            d.update(quantity=1.0, price=1000.0, life_years=10.0)
        elif ln.category in ("insurance", "maintenance"):
            d.update(quantity=0.01)
        elif ln.category == "staff":
            d.update(quantity=2.0, price=50.0)
        rows.append(LineItem.model_validate(d))
    return rows


@pytest.mark.parametrize("template", CASES)
def test_f3_every_share_has_a_plant_capex_base_that_is_not_a_replacement_lump(template):
    lines = _filled(template)
    shares = [ln for ln in lines if ln.source == "share_of"]
    assert shares
    for ln in shares:
        kind, _, cat = ln.quantity_source.split(":", 1)[1].partition("/")
        base = [b for b in lines if b.kind == kind and (not cat or b.category == cat)]
        assert [
            b
            for b in base
            if b.refresh_years is None and "replacement" not in b.name.lower()
        ], ln.name


@pytest.mark.parametrize("template", CASES)
def test_g2_each_capex_line_is_in_at_most_one_maintenance_share_base(template):
    lines = _filled(template)
    shares = [
        ln for ln in lines if ln.source == "share_of" and ln.category == "maintenance"
    ]
    for cap in (ln for ln in lines if ln.kind == "capex"):
        bases = []
        for sh in shares:
            kind, _, cat = sh.quantity_source.split(":", 1)[1].partition("/")
            if kind == "capex" and (not cat or cap.category == cat):
                bases.append(sh.name)
        assert len(bases) <= 1, (cap.name, bases)
