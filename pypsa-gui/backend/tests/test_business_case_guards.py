"""Business case slice 06: the ledger guards (spec §3 G2–G4).

.scratch/business-case/issues/06-ledger-guards.md: GK A's G3-1..G3-6, G4-1, G4-2 and
G-A1, and GK B's served-load ruling C1/C2 in its ## Comments. The hand-built plant is
04's (two investment periods of 5 years, non-unit weightings, LO-8).
"""

from __future__ import annotations

import dataclasses
from typing import get_args

import numpy as np
import pytest

from models.business_case import DoubleCount, LinkedOutputMissing
from services.business_case import linked_outputs as L
from services.finance.business_case import build_business_case, business_case_hash
from services.finance.case import Template, TemplateLine
from services.finance.engine import run_case
from services.finance.report import ENERGY_BILL_STREAMS
from tests.test_business_case_engine import PACK, _ic, _inputs, _line
from tests.test_business_case_linked_outputs import PERIODS, W, _plant, _solve


def _asset(slack: str, amount: float, stream: str = "lost_production") -> TemplateLine:
    sid = f"opex:Generator:{slack}"
    return TemplateLine(
        f"asset:{sid}", stream, amount, "opex", source="asset", source_id=sid
    )


LOST_NH3 = _asset("__lost_nh3", -150.0)
LOST_CONC = _asset("__lost_conc", -90.0)
BOILER = _asset("boiler", -40.0, stream="fuel")
FOM = TemplateLine("fom", "fom", -20.0, "opex")


def _case(*lines, years: int = 8, by_period=None):
    """The engine test's IC case over 2031–2038, with `lines` (or a template per
    period: {first year: lines})."""
    ic = _ic(lines=lines or (FOM,), analysis_years=years)
    if by_period is not None:
        ic = dataclasses.replace(
            ic,
            templates=tuple(
                Template(y, tuple(ls), energy_mwh={"pv": 10.0})
                for y, ls in sorted(by_period.items())
            ),
        )
    return ic


def _linked(
    name: str, output_id: str, *, kind="revenue", unit="t", category=None, **kw
):
    category = category or ("product_sale" if kind == "revenue" else "feedstock")
    return _line(
        name=name,
        kind=kind,
        category=category,
        unit=unit,
        quantity=None,
        quantity_source=f"linked:{output_id}@1",
        **kw,
    )


def _flat(output_id: str, q: float = 100.0, unit: str = "t") -> L.Series:
    return L.Series(output_id, unit, {None: q}, {})


def _map(**slack_bus) -> L.LostProductionMap:
    return L.LostProductionMap(
        slack_bus=dict(slack_bus),
        load_bus={},
        share={s: {} for s in slack_bus},
        unread={},
    )


def _by_year(bc) -> dict[int, dict[str, TemplateLine]]:
    return {
        int(t.first_year): {ln.key: ln for ln in t.lines} for t in bc.case.templates
    }


OPS = range(2031, 2039)


# ── G2, C1, C2: the ledger streams a linked line meets ───────────────────────


def test_c1_every_bill_stream_prices_grid_import():
    """The served-load ruling's premise (GK B C1): each bill stream is a tariff item
    measured on the grid meter, never on a served load. A new bill stream or meter
    fails here, and `ledger_clash` must then say whether it prices the load."""
    from models.commercial import MeasuredOn, TariffItemKind
    from services.commercial.participants import BillItem, _item_stream

    assert ENERGY_BILL_STREAMS == L.GRID_IMPORT_BILL_STREAMS
    assert set(get_args(MeasuredOn)) == {"import", "export", "net", "peak_import"}
    got = {
        _item_stream(BillItem("i", kind, on, direction), payee)
        for kind in get_args(TariffItemKind)
        for on in get_args(MeasuredOn)
        for direction in ("cost", "revenue")
        for payee in ("retailer", "dso")
    }
    # Export is a ledger stream of its own (G2), never a bill stream.
    assert got - {"energy_export"} == set(L.GRID_IMPORT_BILL_STREAMS)


def test_c2_a_served_load_meets_no_bill_stream_and_is_refused_on_eaas_fee():
    out = "served_load.site_a"
    for stream in ENERGY_BILL_STREAMS:
        assert L.ledger_clash(out, {stream}) is None
    assert L.ledger_clash(out, {"eaas_fee"}) == ("refuse", "eaas_fee")
    assert L.ledger_clash(out, {*ENERGY_BILL_STREAMS, "eaas_fee"}) == (
        "refuse",
        "eaas_fee",
    )
    # Unvalued, an eaas_fee prices nothing.
    assert L.ledger_clash(out, set(ENERGY_BILL_STREAMS)) is None


def test_c2_through_the_build_each_bill_stream_admits_and_eaas_fee_refuses():
    ln = _linked("tariff", "served_load.site_a", unit="MWh")
    outputs = {"tariff": _flat("served_load.site_a", unit="MWh")}
    bills = [TemplateLine(s, s, -10.0, "tariff") for s in ENERGY_BILL_STREAMS]
    for bill in bills:
        build_business_case(_case(bill, FOM), _inputs(ln), outputs)
    fee = TemplateLine("contract:eaas:eaas_fee", "eaas_fee", 50.0, "contract")
    for ic in (_case(fee, FOM), _case(*bills, fee)):
        with pytest.raises(DoubleCount) as e:
            build_business_case(ic, _inputs(ln), outputs)
        assert e.value.stream == "eaas_fee"
        assert e.value.field == "lines[0].quantity_source"
    # A zero fee prices no delivery: admitted, as every other stream at 0.
    build_business_case(
        _case(dataclasses.replace(fee, amount=0.0)), _inputs(ln), outputs
    )


def test_g2_each_export_clash_is_refused_with_its_stream():
    ln = _linked("geo", "export.grid_export", unit="MWh")
    outputs = {"geo": _flat("export.grid_export", unit="MWh")}
    for stream in ("energy_export", "ppa_settlement", "cfd_settlement"):
        with pytest.raises(DoubleCount) as e:
            build_business_case(
                _case(TemplateLine(stream, stream, 30.0, "export")),
                _inputs(ln),
                outputs,
            )
        assert e.value.stream == stream
    # A difference line that nets to 0 still prices its volume.
    cfd = TemplateLine(
        "cfd", "cfd_settlement", 0.0, "contract", price=50.0, price_qty_mwh=10.0
    )
    with pytest.raises(DoubleCount) as e:
        build_business_case(_case(cfd), _inputs(ln), outputs)
    assert e.value.stream == "cfd_settlement"


def test_g2_a_product_line_is_never_refused():
    for out in ("served_product.nh3", "served_h2.h2_offtake", "shipped.shipments"):
        ic = _case(LOST_NH3, TemplateLine("exp", "energy_export", 30.0, "export"))
        bc = build_business_case(
            ic,
            _inputs(_linked("sale", out)),
            {"sale": _flat(out)},
            _map(__lost_nh3="nh3"),
        )
        assert bc.case is not None


# ── G3: lost production ──────────────────────────────────────────────────────


def test_g3_1_only_the_read_bus_drops_a_fuel_line_and_another_bus_stay():
    ic = _case(LOST_NH3, LOST_CONC, BOILER, FOM)
    lpm = _map(__lost_nh3="nh3", __lost_conc="conc")
    bc = build_business_case(
        ic,
        _inputs(_linked("nh3", "served_product.nh3")),
        {"nh3": _flat("served_product.nh3")},
        lpm,
    )
    for y in OPS:
        keys = set(_by_year(bc)[y])
        assert LOST_NH3.key not in keys
        assert {LOST_CONC.key, BOILER.key, FOM.key, "nh3"} <= keys
    assert _by_year(bc)[2031][LOST_CONC.key].amount == LOST_CONC.amount


def _captured(n, lost):
    """`n` without the hand-built `__lost_nh3`: the solve-time slack on a valued bus,
    captured (`services.solver.lost_production`) when `lost` is given."""
    import pandas as pd

    from services.solver import lost_production as LP

    n.remove("Generator", "__lost_nh3")
    n.buses["lost_production_value"] = np.nan
    n.buses.at["nh3", "lost_production_value"] = 150.0
    if lost is None:
        return _solve(n)
    captured = {
        "lost_production": {
            "p_t": pd.DataFrame({"nh3": lost}, index=n.snapshots),
            "value": {"nh3": 150.0},
            "key": {},
        }
    }
    LP.stamp(n, captured)
    return _solve(n, lost_load=captured)


def test_g3_1_a_captured_slack_with_no_generator_is_placed_on_its_bus():
    """G3-1, A2: the slack maps through `_lost_production_slacks`, by its name, when the
    solve captured it and no Generator is left, and its dispatch is the capture's."""
    n = _plant()
    solve = _captured(n, np.tile([0.0, 30.0, 0.0, 5.0], 2))
    ic = _case(LOST_NH3, LOST_CONC, FOM)
    lpm = L.lost_production(n, solve, ic)
    assert lpm.slack_bus == {"__lost_conc": "conc", "__lost_nh3": "nh3"}
    assert lpm.unread == {}
    assert lpm.load_bus == {"nh3_delivery": "nh3", "shipments": "conc"}
    assert lpm.share["__lost_nh3"]["nh3_delivery"].per_period == {2030: 1.0, 2035: 1.0}
    bc = build_business_case(
        ic,
        _inputs(_linked("nh3", "served_h2.nh3_delivery")),
        {"nh3": _flat("served_h2.nh3_delivery")},
        lpm,
    )
    assert LOST_NH3.key not in _by_year(bc)[2031]


def test_a6_an_unread_bus_refuses_only_the_line_on_it():
    """A6: with no capture the slack is not evaluated, never 0. A line on that bus is
    refused; a line on another bus builds and the unread bus keeps its penalty."""
    n = _plant()
    solve = _captured(n, None)
    ic = _case(LOST_NH3, LOST_CONC, FOM)
    lpm = L.lost_production(n, solve, ic)
    assert lpm.unread == {"nh3": "lost_production_capture_missing"}
    with pytest.raises(LinkedOutputMissing) as e:
        build_business_case(
            ic,
            _inputs(_linked("nh3", "served_h2.nh3_delivery")),
            {"nh3": _flat("served_h2.nh3_delivery")},
            lpm,
        )
    assert e.value.field == "lost_production.nh3"
    bc = build_business_case(
        ic,
        _inputs(_linked("conc", "served_product.conc")),
        {"conc": _flat("served_product.conc")},
        lpm,
    )
    for y in OPS:
        assert _by_year(bc)[y][LOST_NH3.key] == LOST_NH3
        assert LOST_CONC.key not in _by_year(bc)[y]


def test_a2_a_shortfall_the_ledger_does_not_name_reads_not_evaluated():
    """A2: the ledger's slacks and `shortfalls` must name the same ones. A shortfall
    with no `lost_production` line makes its bus unread, never ignored."""
    n = _plant()
    ic = _case(LOST_CONC, FOM)
    lpm = L.lost_production(n, _solve(n), ic)
    assert lpm.slack_bus == {"__lost_conc": "conc"}
    assert lpm.unread == {"nh3": "lost_production_not_in_ledger"}
    with pytest.raises(LinkedOutputMissing):
        build_business_case(
            ic,
            _inputs(_linked("nh3", "served_product.nh3")),
            {"nh3": _flat("served_product.nh3")},
            lpm,
        )


def test_a2_a_shortfall_booked_at_no_cost_is_named_and_its_sale_line_builds():
    """G3-1: the ledger's shortfall asset at no cost has no `lost_production` line, but
    it is the ledger's slack: its bus is read, and a sale line on it drops nothing."""
    from types import SimpleNamespace

    from services.commercial.participants import PRODUCT_SLACK_FLAGS

    n = _plant()
    ic = _case(LOST_CONC, FOM)
    asset = SimpleNamespace(
        component="Generator",
        name="__lost_nh3",
        flags=[PRODUCT_SLACK_FLAGS["shortfall"]],
    )
    lpm = L.lost_production(n, _solve(n), ic, SimpleNamespace(assets=[asset]))
    assert lpm.slack_bus == {"__lost_conc": "conc", "__lost_nh3": "nh3"}
    assert lpm.unread == {}
    bc = build_business_case(
        ic,
        _inputs(_linked("nh3", "served_product.nh3")),
        {"nh3": _flat("served_product.nh3")},
        lpm,
    )
    for y in OPS:
        assert _by_year(bc)[y][LOST_CONC.key] == LOST_CONC
    assert all(r.dropped == () for r in bc.locked)


def test_g3_2_an_opex_line_on_the_quantity_and_a_share_never_drop():
    ic = _case(LOST_NH3, FOM)
    royalty = _linked("royalty", "served_product.nh3", kind="opex")
    typed = _line(name="typed_nh3")
    share = _line(
        name="agent",
        kind="opex",
        category="sga",
        unit="share",
        quantity=0.05,
        price=None,
        quantity_source="share_of:revenue/product_sale",
    )
    # No map is needed: none of them triggers the drop.
    bc = build_business_case(
        ic,
        _inputs(royalty, typed, share),
        {"royalty": _flat("served_product.nh3")},
    )
    for y in OPS:
        assert _by_year(bc)[y][LOST_NH3.key].amount == LOST_NH3.amount
    assert all(not r.dropped for r in bc.locked)


def test_g3_no_line_no_drop():
    ic = _case(LOST_NH3, FOM)
    bc = build_business_case(ic, _inputs(_line()), {})
    ref = build_business_case(ic, _inputs(), {})
    for y in OPS:
        assert _by_year(bc)[y][LOST_NH3.key] == LOST_NH3
        assert _by_year(ref)[y][LOST_NH3.key] == LOST_NH3


def test_g3_3_the_penalty_stays_in_the_years_the_line_does_not_cover():
    ic = _case(LOST_NH3, FOM)
    ln = _linked("nh3", "served_product.nh3", start_year=2033, end_year=2035)
    bc = build_business_case(
        ic, _inputs(ln), {"nh3": _flat("served_product.nh3")}, _map(__lost_nh3="nh3")
    )
    for y in OPS:
        assert (LOST_NH3.key in _by_year(bc)[y]) == (not 2033 <= y <= 2035), y
    r = run_case(bc.case, PACK)
    years = [int(y) for y in r.tl.years]
    lost = r.op.lines[LOST_NH3.key]
    for i, y in enumerate(years):
        assert (lost[i] == 0.0) == (y == 2030 or 2033 <= y <= 2035), y


def _two_loads():
    """The concentrate bus with a second Load drawing differently in each period, and
    a shortfall that, in an hour with no draw at all, fills the shed (LO-8)."""
    n = _plant()
    n.add("Load", "shipments_b", bus="conc", p_set=0.0)
    n.loads_t.p_set["shipments"] = [8.0, 0.0, 8.0, 0.0, 8.0, 0.0, 8.0, 0.0]
    n.loads_t.p_set["shipments_b"] = [8.0, 0.0, 24.0, 0.0, 2.0, 0.0, 6.0, 0.0]
    n.generators_t.p["__lost_conc"] = [2.0, 6.0, 8.0, 0.0, 10.0, 3.0, 0.0, 0.0]
    return n


def _want_share(n, load: str) -> dict:
    """The read Load's part of the slack, recomputed here: the hour's p_set share, and
    in an hour with no draw the period's Σ p_set · w share."""
    p = n.generators_t.p["__lost_conc"]
    ps = n.loads_t.p_set[["shipments", "shipments_b"]]
    out = {}
    for per in PERIODS:
        pp, pps = p.xs(per, level=0), ps.xs(per, level=0)
        tot = pps.sum(axis=1)
        hour = pps[load] / tot.where(tot != 0)
        hour[tot == 0] = pps[load].sum() / pps.sum().sum()
        out[per] = float((pp * hour).sum() * W) / float(pp.sum() * W)
    return out


def test_g3_4_a_two_load_bus_drops_only_the_read_loads_share():
    n = _two_loads()
    want = _want_share(n, "shipments")
    assert 0.0 < want[2030] < 1.0 and 0.0 < want[2035] < 1.0
    assert want[2030] != pytest.approx(want[2035])
    lines = (LOST_CONC, BOILER)
    ic = _case(by_period={2031: lines, 2035: lines})
    solve = _solve(n)
    lpm = L.lost_production(n, solve, ic)
    got = lpm.share["__lost_conc"]
    assert got["shipments"].per_period == pytest.approx(want)
    assert got["shipments_b"].per_period == pytest.approx(
        {p: 1.0 - v for p, v in want.items()}
    )
    ln = _linked("ship", "shipped.shipments")
    bc = build_business_case(
        ic, _inputs(ln), {"ship": L.read(n, solve, "shipped.shipments", "t")}, lpm
    )
    for y in OPS:
        per = 2030 if y < 2035 else 2035
        kept = _by_year(bc)[y][LOST_CONC.key].amount
        assert kept == pytest.approx(LOST_CONC.amount * (1.0 - want[per]), rel=1e-12)
        assert _by_year(bc)[y][BOILER.key] == BOILER
    # Both Loads read: the whole slack drops, as served_product on the bus.
    both = _linked("ship_b", "shipped.shipments_b")
    bc = build_business_case(
        ic,
        _inputs(ln, both),
        {
            "ship": L.read(n, solve, "shipped.shipments", "t"),
            "ship_b": L.read(n, solve, "shipped.shipments_b", "t"),
        },
        lpm,
    )
    for y in OPS:
        line = _by_year(bc)[y].get(LOST_CONC.key)
        assert line is None or line.amount == pytest.approx(0.0, abs=1e-9)


def test_a3_the_hourly_split_is_not_the_periods_and_the_read_load_counts_once():
    """A3: the share is the slack's own hourly split, which differs from the period's
    Σ p_set · w share when the Loads' shapes differ. With it the read Load's served
    quantity plus its share of the shortfall is its contracted quantity (G3-5)."""
    from services.results.finance_case import _per_year

    n = _two_loads()
    solve = _solve(n)
    lpm = L.lost_production(n, solve, _case(LOST_CONC))
    share = lpm.share["__lost_conc"]["shipments"].per_period
    served = L.read(n, solve, "shipped.shipments", "t").per_period
    ps = n.loads_t.p_set
    p = n.generators_t.p["__lost_conc"]
    for per in PERIODS:
        flat = (
            ps["shipments"].xs(per, level=0).sum()
            / ps[["shipments", "shipments_b"]].xs(per, level=0).sum().sum()
        )
        assert share[per] != pytest.approx(flat, rel=1e-3)
        contracted = _per_year(n, ps["shipments"], per)
        assert served[per] + share[per] * _per_year(n, p, per) == pytest.approx(
            contracted, rel=1e-12
        )


def test_a3_d1_shares_are_finite_with_idle_loads_and_no_shortfall():
    """D1, A3: a Load that never draws takes 0; a period with no shortfall drops 0
    (never NaN); a bus no Load draws on reads 0 while nothing is short, and is
    unread (never 0) once a shortfall has no Load to split onto (LO-3)."""
    n = _plant()
    n.add("Load", "idle", bus="conc", p_set=0.0)
    n.generators_t.p["__lost_conc"] = [3.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    lpm = L.lost_production(n, _solve(n), _case(LOST_CONC, LOST_NH3))
    got = lpm.share["__lost_conc"]
    assert got["idle"].per_period == {2030: 0.0, 2035: 0.0}
    assert got["shipments"].per_period == {2030: 1.0, 2035: 0.0}
    assert lpm.share["__lost_nh3"]["nh3_delivery"].per_period == {2030: 0.0, 2035: 0.0}
    n.loads.loc[["shipments", "idle"], "p_set"] = 0.0
    lpm = L.lost_production(n, _solve(n), _case(LOST_CONC, LOST_NH3))
    assert lpm.unread == {"conc": "unserved_no_load_to_split"}
    n.generators_t.p["__lost_conc"] = 0.0
    lpm = L.lost_production(n, _solve(n), _case(LOST_CONC, LOST_NH3))
    assert lpm.unread == {}
    assert lpm.share["__lost_conc"]["idle"].per_period == {2030: 0.0, 2035: 0.0}


def test_a4_a_load_read_twice_drops_once_and_the_bus_at_most_whole():
    import json

    n = _two_loads()
    solve = _solve(n)
    ic = _case(LOST_CONC, FOM)
    lpm = L.lost_production(n, solve, ic)
    share = lpm.share["__lost_conc"]["shipments"].per_period
    q = L.read(n, solve, "shipped.shipments", "t")
    twice = _inputs(
        _linked("ship", "shipped.shipments"), _linked("ship2", "shipped.shipments")
    )
    bc = build_business_case(ic, twice, {"ship": q, "ship2": q}, lpm)
    for y in OPS:
        per = 2030 if y < 2035 else 2035
        assert _by_year(bc)[y][LOST_CONC.key].amount == pytest.approx(
            LOST_CONC.amount * (1.0 - share[per]), rel=1e-12
        )
    row = {r.key: r for r in bc.locked}[LOST_CONC.key]
    assert row.superseded_by == ("ship", "ship2")
    both = _inputs(
        _linked("ship", "shipped.shipments"), _linked("all", "served_product.conc")
    )
    bc = build_business_case(
        ic, both, {"ship": q, "all": L.read(n, solve, "served_product.conc", "t")}, lpm
    )
    for y in OPS:
        assert LOST_CONC.key not in _by_year(bc)[y]
    row = {r.key: r for r in bc.locked}[LOST_CONC.key]
    assert row.dropped == tuple((y, 1.0) for y in OPS)
    # D1: every amount and locked share is finite JSON.
    json.dumps(
        [ln.amount for t in bc.case.templates for ln in t.lines]
        + [list(r.dropped) for r in bc.locked],
        allow_nan=False,
    )


def test_a5_a_line_over_part_of_a_period_drops_only_its_years():
    """A5, G3-3 on the multi-period plant (5-year periods, weightings 3): a line over
    2032–2036 drops its Load's share in those years only."""
    n = _two_loads()
    solve = _solve(n)
    ic = _case(by_period={2031: (LOST_CONC, FOM), 2035: (LOST_CONC, FOM)})
    lpm = L.lost_production(n, solve, ic)
    share = lpm.share["__lost_conc"]["shipments"].per_period
    ln = _linked("ship", "shipped.shipments", start_year=2032, end_year=2036)
    bc = build_business_case(
        ic, _inputs(ln), {"ship": L.read(n, solve, "shipped.shipments", "t")}, lpm
    )
    for y in OPS:
        per = 2030 if y < 2035 else 2035
        f = share[per] if 2032 <= y <= 2036 else 0.0
        assert _by_year(bc)[y][LOST_CONC.key].amount == pytest.approx(
            LOST_CONC.amount * (1.0 - f), rel=1e-12
        )
    row = {r.key: r for r in bc.locked}[LOST_CONC.key]
    assert [y for y, _ in row.dropped] == list(range(2032, 2037))


def test_a7_a_served_load_line_never_drops_lost_production():
    ic = _case(LOST_NH3, FOM)
    ln = _linked("tariff", "served_load.nh3_delivery", unit="MWh", category="tariff")
    bc = build_business_case(
        ic, _inputs(ln), {"tariff": _flat("served_load.nh3_delivery", unit="MWh")}
    )
    for y in OPS:
        assert _by_year(bc)[y][LOST_NH3.key] == LOST_NH3


def test_g3_5_a_shortfall_counts_exactly_once_as_lower_served_revenue():
    """GK A G3-5 on a store-bearing bus whose shortfall exceeds the hour's load (LO-8):
    the case with the sale line = the case with neither the line nor lost_production,
    plus served × price; served = contracted − shortfall."""
    from services.results.finance_case import _per_year as shortfall_per_year

    n = _plant()
    n.generators_t.p["__lost_conc"] = np.tile([20.0, 0.0, 3.0, 0.0], 2)
    solve = _solve(n)
    value, price = 90.0, 120.0
    by_period = {
        y: (
            _asset(
                "__lost_conc",
                -value * shortfall_per_year(n, n.generators_t.p["__lost_conc"], per),
            ),
            FOM,
        )
        for y, per in ((2031, 2030), (2035, 2035))
    }
    ic = _case(by_period=by_period)
    lpm = L.lost_production(n, solve, ic)
    ln = _linked("ship", "shipped.shipments", price=price)
    served = L.read(n, solve, "shipped.shipments", "t")
    # The same price on the contracted quantity, for the escalation it carries.
    contracted = 8.0 * 4 * W
    full = run_case(
        build_business_case(
            _case(by_period={y: (FOM,) for y in by_period}),
            _inputs(_line(name="ship", quantity=contracted, price=price)),
            {},
        ).case,
        PACK,
    ).op.lines["ship"]
    with_line = run_case(
        build_business_case(ic, _inputs(ln), {"ship": served}, lpm).case, PACK
    )
    bare_ic = _case(by_period={y: (FOM,) for y in by_period})
    bare = run_case(build_business_case(bare_ic, _inputs(), {}).case, PACK)
    sale = with_line.op.lines["ship"]
    np.testing.assert_allclose(
        with_line.cash["project_pre_tax"],
        bare.cash["project_pre_tax"] + sale,
        rtol=0,
        atol=1e-9,
    )
    assert LOST_CONC.key not in with_line.op.lines or not np.any(
        with_line.op.lines[LOST_CONC.key]
    )
    years = [int(y) for y in with_line.tl.years]
    for i, y in enumerate(years):
        if y == 2030:
            continue
        per = 2030 if y < 2035 else 2035
        short = shortfall_per_year(n, n.generators_t.p["__lost_conc"], per)
        assert short > 8.0 * W  # an hour's shortfall above its load
        assert sale[i] == pytest.approx(
            full[i] * (contracted - short) / contracted, rel=1e-12
        )
    # Without the line the penalty stays: the shortfall is charged by it, once.
    no_line = run_case(build_business_case(ic, _inputs(), {}, lpm).case, PACK)
    assert np.any(no_line.op.lines[LOST_CONC.key])


def test_g3_6_the_locked_rows_show_the_drop_and_its_line_at_any_price():
    ic = _case(LOST_NH3, LOST_CONC, FOM)
    ln = _linked("nh3", "served_product.nh3", price=0.0, start_year=2032)
    bc = build_business_case(
        ic,
        _inputs(ln),
        {"nh3": _flat("served_product.nh3")},
        _map(__lost_nh3="nh3", __lost_conc="conc"),
    )
    rows = {r.key: r for r in bc.locked}
    assert set(rows) == {LOST_NH3.key, LOST_CONC.key, FOM.key}
    assert rows[LOST_NH3.key].stream == "lost_production"
    assert rows[LOST_NH3.key].dropped == tuple((y, 1.0) for y in range(2032, 2039))
    assert rows[LOST_NH3.key].superseded_by == ("nh3",)
    for k in (LOST_CONC.key, FOM.key):
        assert rows[k].dropped == () and rows[k].superseded_by == ()
    assert not any("nh3" == r.key for r in bc.locked)


def test_g3_a_triggering_line_with_no_map_is_refused_never_silently_kept():
    ic = _case(LOST_NH3, FOM)
    inputs = _inputs(_linked("nh3", "served_product.nh3"))
    out = {"nh3": _flat("served_product.nh3")}
    with pytest.raises(LinkedOutputMissing):
        build_business_case(ic, inputs, out)
    with pytest.raises(LinkedOutputMissing):
        build_business_case(ic, inputs, out, _map(other="x"))


def test_g3_the_drop_map_is_in_the_business_case_hash_only():
    from services.finance.report import _fallback_hash

    ic = _case(LOST_NH3, FOM)
    inputs = _inputs(_linked("nh3", "served_product.nh3"))
    out = {"nh3": _flat("served_product.nh3")}
    a = business_case_hash(ic, inputs, out, _map(__lost_nh3="nh3"))
    b = business_case_hash(ic, inputs, out, _map(__lost_nh3="conc"))
    assert a != b and business_case_hash(ic, inputs, out) not in (a, b)
    before = _fallback_hash(ic)
    build_business_case(ic, inputs, out, _map(__lost_nh3="nh3"))
    assert _fallback_hash(ic) == before


def test_d2_a_case_with_no_drop_keeps_its_master_digest():
    """D2: digests computed on master 053362cdf (BC 05) for the same cases."""
    ic = _ic(analysis_years=8)
    assert (
        business_case_hash(ic, _inputs(_line())) == "be59cd9f1273e85dd3e94ab8a8763822"
    )
    ln = _line(name="nh3", quantity=None, quantity_source="linked:served_product.nh3@1")
    out = {"nh3": _flat("served_product.nh3")}
    want = "a1c673f189d7609a52e27ce2e0e5c64d"
    assert business_case_hash(ic, _inputs(ln), out) == want
    # A map with no line that can drop is not in the digest.
    assert business_case_hash(ic, _inputs(_line()), {}, _map(__lost_nh3="nh3")) == (
        "be59cd9f1273e85dd3e94ab8a8763822"
    )


def test_d2_the_map_digest_does_not_depend_on_insertion_order():
    n = _two_loads()
    solve = _solve(n)
    ic = _case(LOST_CONC, LOST_NH3, FOM)
    lpm = L.lost_production(n, solve, ic)
    rev = L.LostProductionMap(
        slack_bus=dict(reversed(list(lpm.slack_bus.items()))),
        load_bus=dict(reversed(list(lpm.load_bus.items()))),
        share={
            s: dict(reversed(list(by.items())))
            for s, by in reversed(list(lpm.share.items()))
        },
        unread=dict(lpm.unread),
    )
    inputs = _inputs(_linked("ship", "shipped.shipments"))
    out = {"ship": L.read(n, solve, "shipped.shipments", "t")}
    assert business_case_hash(ic, inputs, out, lpm) == business_case_hash(
        ic, inputs, out, rev
    )
    assert build_business_case(ic, inputs, out, lpm).hash == business_case_hash(
        ic, inputs, out, lpm
    )


# ── Mutant gaps: the share arithmetic, the digest gate, the map's refusals ───


def _shares(**per_load) -> L.LostProductionMap:
    """A hand map: `__lost_conc` on conc, each Load's share per period (4-year
    periods from 2031 and 2035)."""
    return L.LostProductionMap(
        slack_bus={"__lost_conc": "conc"},
        load_bus={ld: "conc" for ld in per_load},
        share={
            "__lost_conc": {
                ld: L.Series(f"share.{ld}", "", pp, {p: 4.0 for p in pp})
                for ld, pp in per_load.items()
            }
        },
        unread={},
    )


def _ships(*loads):
    return (
        _inputs(*(_linked(ld, f"shipped.{ld}") for ld in loads)),
        {ld: _flat(f"shipped.{ld}") for ld in loads},
    )


def test_a4_shares_that_sum_past_one_drop_the_line_whole_and_show_one():
    """A4, D5 (b): the read Loads' shares are capped at the whole."""
    ic = _case(LOST_CONC, FOM)
    inputs, out = _ships("a", "b")
    lpm = _shares(a={2031: 0.7, 2035: 0.7}, b={2031: 0.6, 2035: 0.6})
    bc = build_business_case(ic, inputs, out, lpm)
    for y in OPS:
        assert LOST_CONC.key not in _by_year(bc)[y]
    row = {r.key: r for r in bc.locked}[LOST_CONC.key]
    assert row.dropped == tuple((y, 1.0) for y in OPS)


def test_d4_a_zero_share_drops_nothing_and_its_years_are_absent():
    ic = _case(LOST_CONC, FOM)
    inputs, out = _ships("a")
    bc = build_business_case(ic, inputs, out, _shares(a={2031: 0.0, 2035: 0.25}))
    row = {r.key: r for r in bc.locked}[LOST_CONC.key]
    assert row.dropped == tuple((y, 0.25) for y in range(2035, 2039))
    for y in range(2031, 2035):
        assert _by_year(bc)[y][LOST_CONC.key] == LOST_CONC


def test_d1_a_year_outside_every_period_has_no_share_and_no_amount():
    """A year the solve has no period for drops a share that is not established:
    the line's amount is None there (never the full penalty, never 0)."""
    ic = _case(LOST_CONC, FOM)
    inputs, out = _ships("a")
    bc = build_business_case(ic, inputs, out, _shares(a={2035: 0.5}))
    for y in range(2031, 2035):
        assert _by_year(bc)[y][LOST_CONC.key].amount is None
    for y in range(2035, 2039):
        assert _by_year(bc)[y][LOST_CONC.key].amount == pytest.approx(
            LOST_CONC.amount * 0.5
        )
    row = {r.key: r for r in bc.locked}[LOST_CONC.key]
    assert row.dropped[:4] == tuple((y, None) for y in range(2031, 2035))


def test_d1_a_not_established_penalty_stays_not_established_under_a_part_drop():
    lost = dataclasses.replace(LOST_CONC, amount=None)
    ic = _case(lost, FOM)
    inputs, out = _ships("a")
    bc = build_business_case(ic, inputs, out, _shares(a={2031: 0.5, 2035: 0.5}))
    for y in OPS:
        assert _by_year(bc)[y][LOST_CONC.key].amount is None


def test_g3_1_only_an_asset_line_names_a_slack():
    from services.finance.business_case import _slack_of

    assert _slack_of(LOST_CONC) == "__lost_conc"
    assert _slack_of(dataclasses.replace(LOST_CONC, source="template")) is None
    assert _slack_of(dataclasses.replace(LOST_CONC, source_id="opex")) is None


def test_d2_an_empty_map_keeps_the_digest_and_an_unread_one_changes_it():
    ic = _case(LOST_NH3, FOM)
    inputs = _inputs(_linked("nh3", "served_product.nh3"))
    out = {"nh3": _flat("served_product.nh3")}
    none = business_case_hash(ic, inputs, out)
    empty = L.LostProductionMap({}, {}, {}, {})
    assert business_case_hash(ic, inputs, out, empty) == none
    unread = L.LostProductionMap({}, {}, {}, {"nh3": "not_own_solve"})
    assert business_case_hash(ic, inputs, out, unread) != none


def test_d2_the_digest_reads_each_share_period():
    ic = _case(LOST_CONC, FOM)
    inputs, out = _ships("a")
    early = _shares(a={2031: 0.5})
    late = _shares(a={2035: 0.5})
    assert business_case_hash(ic, inputs, out, early) != business_case_hash(
        ic, inputs, out, late
    )


def test_a2_a_stale_solve_or_another_network_reads_no_share():
    """LO-9: the map reads only the run's own fresh solve; otherwise each slack's bus
    is not evaluated (never 0)."""
    n = _plant()
    n.generators_t.p["__lost_conc"] = 2.0
    ic = _case(LOST_NH3, LOST_CONC, FOM)
    want = {"conc": "not_own_solve", "nh3": "not_own_solve"}
    stale = dataclasses.replace(_solve(n), fresh=False)
    assert L.lost_production(n, stale, ic).unread == want
    other = dataclasses.replace(_solve(n), network=n.copy())
    assert L.lost_production(n, other, ic).unread == want
    assert L.lost_production(n, _solve(n), ic).unread == {}


@pytest.mark.parametrize(
    "bus, p, flag, reason",
    [
        ("nh3", "p", None, "lost_production_not_read"),
        ("conc", None, None, "lost_production_not_read"),
        ("conc", None, "role_from_name", "role_from_name"),
        ("conc", "p", "lost_production_capture_stale", "lost_production_capture_stale"),
    ],
)
def test_a2_a_shortfall_on_another_bus_or_unreadable_is_not_evaluated(
    monkeypatch, bus, p, flag, reason
):
    import pandas as pd

    from services import process_products

    n = _plant()
    real = {s.name: s for s in process_products.shortfalls(n, _solve(n).lost_load)}

    def fake(n, lost_load):
        s = real["__lost_conc"]
        p_ = pd.Series(1.0, index=n.snapshots) if p else None
        return [
            real["__lost_nh3"],
            dataclasses.replace(s, bus=bus, p=p_, flag=flag),
        ]

    monkeypatch.setattr(process_products, "shortfalls", fake)
    lpm = L.lost_production(n, _solve(n), _case(LOST_NH3, LOST_CONC, FOM))
    assert lpm.unread == {"conc": reason}
    assert "__lost_conc" not in lpm.share and "__lost_nh3" in lpm.share


def test_g3_a_grid_side_slack_booked_as_energy_import_drops_once():
    """GK B (#1411): a product bus the meter search puts on the grid side books its
    captured shortfall `energy_import` (`participants`, `grid_side_asset_cost`). The
    map names the slack, so its line drops however it is booked; a real grid purchase
    on the same stream stays."""
    grid_lost = _asset("__lost_nh3", -150.0, stream="energy_import")
    buy = _asset("grid_buy", -60.0, stream="energy_import")
    ic = _case(grid_lost, buy, FOM)
    bc = build_business_case(
        ic,
        _inputs(_linked("nh3", "served_product.nh3")),
        {"nh3": _flat("served_product.nh3")},
        _map(__lost_nh3="nh3"),
    )
    for y in OPS:
        assert grid_lost.key not in _by_year(bc)[y]
        assert _by_year(bc)[y][buy.key] == buy
    rows = {r.key: r for r in bc.locked}
    assert rows[grid_lost.key].stream == "energy_import"
    assert rows[grid_lost.key].dropped == tuple((y, 1.0) for y in OPS)
    assert rows[buy.key].dropped == ()


def test_d2_a_map_with_nothing_to_drop_keeps_the_digest():
    """GK B F1: on a case that values no shortfall, a slack-only or unread-only map
    hashes as no map."""
    ic = _case(FOM)
    inputs = _inputs(_linked("nh3", "served_product.nh3"))
    out = {"nh3": _flat("served_product.nh3")}
    none = business_case_hash(ic, inputs, out)
    slack = _map(__lost_nh3="nh3")
    unread = L.LostProductionMap({}, {}, {}, {"nh3": "not_own_solve"})
    assert business_case_hash(ic, inputs, out, slack) == none
    assert business_case_hash(ic, inputs, out, unread) == none
    assert build_business_case(ic, inputs, out, slack).hash == none


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_d1_a_nan_or_inf_share_is_not_established_never_a_number(bad):
    """GK B F3: a share that is not finite gives None, in the line and in `locked`."""
    import json

    ic = _case(LOST_CONC, FOM)
    inputs, out = _ships("a")
    bc = build_business_case(ic, inputs, out, _shares(a={2031: bad, 2035: 0.5}))
    for y in range(2031, 2035):
        assert _by_year(bc)[y][LOST_CONC.key].amount is None
    row = {r.key: r for r in bc.locked}[LOST_CONC.key]
    assert row.dropped[:4] == tuple((y, None) for y in range(2031, 2035))
    json.dumps(
        [ln.amount for t in bc.case.templates for ln in t.lines]
        + [list(r.dropped) for r in bc.locked],
        allow_nan=False,
    )


def test_break_even_forwards_the_map_to_every_trial():
    """GK B F4: break-even builds each trial with the map, so the dropped penalty is
    out of the root; with no map a triggering line is refused, never priced with it."""
    from services.finance.business_case import break_even

    ln = _linked("nh3", "served_product.nh3", price=500.0)
    out = {"nh3": _flat("served_product.nh3")}
    got = break_even(
        _case(LOST_NH3, FOM),
        _inputs(ln),
        out,
        "nh3",
        PACK,
        lost_production=_map(__lost_nh3="nh3"),
    )
    want = break_even(_case(FOM), _inputs(ln), out, "nh3", PACK)
    assert want["status"] == "ok"
    assert got["status"] == "ok" and got["price"] == pytest.approx(want["price"])
    with pytest.raises(LinkedOutputMissing):
        break_even(_case(LOST_NH3, FOM), _inputs(ln), out, "nh3", PACK)


def test_bc1_the_ic_case_is_unchanged_by_every_guard():
    """BC1, A9, D4: a build that drops lost production and fills `locked` leaves the
    IC case, its digest and the network the map read byte-equal."""
    import pickle

    from services.finance.report import _fallback_hash

    n = _plant()
    n.generators_t.p["__lost_conc"] = 2.0
    n_before = pickle.dumps(n)
    ic = _case(LOST_NH3, LOST_CONC, BOILER, FOM)
    frozen, digest = pickle.dumps(ic), _fallback_hash(ic)
    lpm = L.lost_production(n, _solve(n), ic)
    bc = build_business_case(
        ic,
        _inputs(_linked("conc", "served_product.conc")),
        {"conc": _flat("served_product.conc")},
        lpm,
    )
    assert any(r.dropped for r in bc.locked)
    assert pickle.dumps(ic) == frozen and _fallback_hash(ic) == digest
    assert pickle.dumps(n) == n_before


# ── G4 and G-A1 on solved templates ──────────────────────────────────────────


def _bc_cash(bc) -> dict:
    r = run_case(bc.case)
    out = {f"op:{k}": v for k, v in r.op.lines.items()}
    out.update({f"cash:{k}": v for k, v in r.cash.items() if v is not None})
    return out


def _assert_byte_equal(a: dict, b: dict) -> None:
    assert a.keys() == b.keys()
    for k in a:
        assert np.asarray(a[k]).tobytes() == np.asarray(b[k]).tobytes(), k


def _no_reference_line(bc) -> None:
    assert bc.case.counterfactual == () and bc.case.counterfactual_hash is None
    for t in bc.case.templates:
        for ln in t.lines:
            assert "purchase" not in ln.key and ln.source != "counterfactual", ln.key
            assert not (ln.source_id or "").startswith("reference_supply:"), ln.key


def _reference_pin(n, solve, build, out_id: str, unit: str):
    """G4-1: the business case on the IC case at the template's reference price and
    at a far higher one; byte-equal, with a sale line on the product bus (G3 on a
    solved template)."""
    ic_lo, ic_hi = build(1.0), build(4.5)
    cf = [
        sum(ln.amount or 0.0 for t in c.counterfactual for ln in t.lines)
        for c in (ic_lo, ic_hi)
    ]
    assert cf[0] != cf[1]  # the reference price reaches the IC's counterfactual
    ln = _line(
        name="sale",
        unit=unit,
        quantity=None,
        quantity_source=f"linked:{out_id}@1",
        start_year=None,
    )
    inputs = _inputs(ln)
    outputs = {"sale": L.read(n, solve, out_id, unit)}
    assert isinstance(outputs["sale"], L.Series)
    bcs = []
    for ic in (ic_lo, ic_hi):
        bc = build_business_case(ic, inputs, outputs, L.lost_production(n, solve, ic))
        _no_reference_line(bc)
        # G3 on a solved template: no lost_production in an operating year.
        r = run_case(bc.case)
        assert not [
            k
            for k, m in r.op.line_meta.items()
            if m.stream == "lost_production" and np.any(r.op.lines[k])
        ]
        bcs.append(bc)
    assert bcs[0].hash == bcs[1].hash  # G4-2
    _assert_byte_equal(_bc_cash(bcs[0]), _bc_cash(bcs[1]))


@pytest.mark.live_solve
def test_g4_ammonia_no_reference_purchase_value_reaches_the_business_case(
    reset_backend,
):
    import routers.results as R
    from models.finance import FinanceInputs
    from services.results.finance_case import build_finance_case
    from services.solver_service import SolverConfig
    from tests import test_eh_ammonia_staged as S

    n, state, _ = S._plan("half_buy_in", S._half_buy_in)
    raw = S._ic_config(S._half_buy_in)
    solve = L.LinkedSolve(network=n, fresh=True, lost_load=state.get("last_lost_load"))

    def build(scale: float):
        cfg = dict(raw, commercial=dict(raw["commercial"]))
        cfg["commercial"]["reference_purchases"] = [
            dict(r, price_eur_per_kg=r["price_eur_per_kg"] * scale)
            for r in raw["commercial"]["reference_purchases"]
        ]
        c = SolverConfig(**cfg)
        return build_finance_case(
            n,
            c,
            FinanceInputs.model_validate(c.finance),
            result_df=R._result_df,
            lost_load=state.get("last_lost_load"),
        )

    _reference_pin(n, solve, build, "served_product.nh3", "t")


@pytest.mark.live_solve
def test_g4_h2_hub_no_reference_purchase_value_reaches_the_business_case(
    reset_backend, monkeypatch
):
    import queue
    import sys
    import threading

    import routers.results as R
    import routers.simulation as sim_router
    from models.finance import FinanceInputs
    from routers.projects import _solver_config_from_dict
    from services.pypsa_service import PyPSAService
    from services.results.finance_case import build_finance_case
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    import eh_templates as T  # on sys.path via the linked-outputs tests

    assert "eh_templates" in sys.modules
    tid = "eh_h2_hub_staged"
    monkeypatch.setattr(I, "load_project_island", lambda: None)
    raw = T.solver_config(tid)
    install_network_into_backend(T.BUILDERS[tid]())
    cfg = _solver_config_from_dict(raw)
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(
        cfg,
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **k: state.update(k),
    )
    assert status in ("ok", "optimal"), (status, cond)
    solve = L.LinkedSolve(network=n, fresh=True, lost_load=state.get("last_lost_load"))

    def build(scale: float):
        r = dict(raw, commercial=dict(raw["commercial"]))
        r["commercial"]["reference_purchases"] = [
            dict(p, price_eur_per_kg=p["price_eur_per_kg"] * scale)
            for p in raw["commercial"]["reference_purchases"]
        ]
        c = _solver_config_from_dict(r)
        return build_finance_case(
            n,
            c,
            FinanceInputs.model_validate(c.finance),
            result_df=R._result_df,
            lost_load=state.get("last_lost_load"),
        )

    _reference_pin(n, solve, build, "served_h2.h2_offtake", "kg")


@pytest.mark.live_solve
def test_g_a1_no_project_line_prices_the_voll_shed_a_served_load_line_reads(
    reset_backend,
):
    """G-A1: on a site that sheds load at the VoLL, an L4 `served_load` line meets no
    project-side line pricing that shed (the served load excludes it, and nothing
    values it), so G3 has nothing to drop there."""
    from tests.test_finance_case_adapter import OWNED, _case as _ic_case, _edge7, _fin
    from tests.test_value_flow_reconciliation import _commercial, _solve as _solved

    n = _edge7()
    n.links.loc["import", "p_nom_extendable"] = False
    n.links.loc["import", "p_nom"] = 1.0
    n.generators.loc["pv", "p_nom_extendable"] = False
    n.generators.loc["pv", "p_nom"] = 5.0
    com = _commercial(OWNED, contracts=[])
    com.pop("connection")
    state: dict = {}
    n, cfg = _solved(n, com, state=state, voll=1000.0)
    ll = state["last_lost_load"]
    w = n.snapshot_weightings.objective.to_numpy(float)
    shed = ll["lost_load_t"]["site_load"].reindex(n.snapshots).fillna(0.0)
    assert float((w * shed.to_numpy(float)).sum()) > 0
    ic = _ic_case(n, cfg, _fin(annualise=True), lost_load=ll)
    solve = L.LinkedSolve(network=n, fresh=True, lost_load=ll)
    served = L.read(n, solve, "served_load.site_load", "MWh")
    assert isinstance(served, L.Series)
    # A8, F7: served + the product shortfall (none on an electric bus) + the Load's own
    # VoLL shed = contracted.
    from services.process_products import shortfalls
    from services.results.finance_case import _per_year

    assert not [x for x in shortfalls(n, ll) if x.bus == "site"]
    p_set = n.get_switchable_as_dense("Load", "p_set")["site_load"]
    for per, q in served.per_period.items():
        assert q + _per_year(n, shed, per) == pytest.approx(
            _per_year(n, p_set, per), rel=1e-9
        )
    ln = _linked("tariff", "served_load.site_load", unit="MWh", category="tariff")
    bc = build_business_case(
        ic, _inputs(ln), {"tariff": served}, L.lost_production(n, solve, ic)
    )
    priced = [
        ln.key
        for t in bc.case.templates
        for ln in t.lines
        if ln.stream == "lost_production"
        or any(w in ln.key.lower() for w in ("shed", "voll", "lost_load", "site_load"))
    ]
    assert priced == []
    assert all(not r.dropped for r in bc.locked)
