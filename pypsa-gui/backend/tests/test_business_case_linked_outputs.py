"""Business case slice 04: linked model outputs and unit conversions.

Spec .scratch/business-case/spec.md §3 G5 and §5; GK A's LO-1..LO-10 and P1..P8 in
.scratch/business-case/issues/04-linked-outputs.md. The hand-built plant has two
investment periods of 5 years and 3 h snapshots (LO-8), with the solve's frames
written in, as `test_dc_it_energy._solved_like` does.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from types import SimpleNamespace
from typing import get_args

import numpy as np
import pandas as pd
import pypsa
import pytest

from models import business_case as B
from models.commercial import ReferencePurchase, ValueStreamKind
from services.business_case import linked_outputs as L

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_ammonia as A  # noqa: E402
import eh_templates as T  # noqa: E402

PERIODS, HOURS, W, YEARS = (2030, 2035), 4, 3.0, 5.0
LHV = ReferencePurchase.model_fields["lhv_kwh_per_kg"].default


def _plant(periods=PERIODS):
    """A solved-like plant: electric, H2, ammonia (MWh with mwh_per_t) and concentrate (t) buses."""
    n = pypsa.Network()
    hours = pd.date_range("2030-01-01", periods=HOURS, freq="h")
    if periods:
        n.set_snapshots(pd.MultiIndex.from_product([periods, hours]))
        n.investment_periods = list(periods)
        n.investment_period_weightings["years"] = YEARS
    else:
        n.set_snapshots(hours)
    n.snapshot_weightings.loc[:, :] = W
    for carrier in (
        "AC",
        "H2",
        "NH3",
        "concentrate",
        "electrolysis",
        "wind",
        "load_shedding",
        "demand_response",
    ):
        n.add("Carrier", carrier)
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.add("Bus", "conc", carrier="concentrate", unit="t")
    n.buses["mwh_per_t"] = np.nan
    n.buses.at["nh3", "mwh_per_t"] = A.MWH_PER_T
    n.add("Load", "site_a", bus="hub", p_set=10.0)
    n.add("Load", "site_b", bus="hub", p_set=0.0)
    n.loads_t.p_set["site_b"] = np.tile([30.0, 0.0, 10.0, 0.0], max(len(periods), 1))
    n.add("Load", "h2_offtake", bus="h2", p_set=25.0)
    n.add("Load", "nh3_delivery", bus="nh3", p_set=20.0)
    n.add("Load", "shipments", bus="conc", p_set=8.0)
    n.add(
        "Link",
        "electrolyser",
        bus0="hub",
        bus1="h2",
        p_nom=60.0,
        efficiency=0.68,
        carrier="electrolysis",
    )
    n.add("Link", "h2_import", bus0="grid", bus1="h2", p_nom=5.0, carrier="H2")
    n.add(
        "Link",
        "grid_export",
        bus0="hub",
        bus1="grid",
        p_nom=50.0,
        efficiency=0.9,
        carrier="AC",
    )
    n.add(
        "Generator",
        "wind",
        bus="hub",
        carrier="wind",
        p_nom=5.0,
        p_nom_extendable=True,
        build_year=2030,
    )
    n.add(
        "Generator",
        "old",
        bus="hub",
        carrier="wind",
        p_nom=7.0,
        build_year=2025,
        lifetime=8,
    )
    n.add("Generator", "shed_hub", bus="hub", carrier="load_shedding", p_nom=100.0)
    n.add("Generator", "dsr", bus="hub", carrier="demand_response", p_nom=100.0)
    n.add(
        "Generator",
        "__lost_nh3",
        bus="nh3",
        carrier="NH3 shortfall",
        p_nom=100.0,
        marginal_cost=150.0,
    )
    n.add(
        "Generator",
        "__lost_conc",
        bus="conc",
        carrier="concentrate shortfall",
        p_nom=100.0,
        marginal_cost=90.0,
    )
    n.generators["process_role"] = ""
    n.generators.loc[["__lost_nh3", "__lost_conc"], "process_role"] = "shortfall"
    n.generators.at["wind", "p_nom_opt"] = 42.0
    n.generators_t.p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    n.links_t.p0 = pd.DataFrame(0.0, index=n.snapshots, columns=n.links.index)
    n.links_t.p1 = pd.DataFrame(0.0, index=n.snapshots, columns=n.links.index)
    return n


def _capture(n, **cols):
    return {
        "lost_load_t": pd.DataFrame(
            {k: np.asarray(v, dtype=float) for k, v in cols.items()}, index=n.snapshots
        )
    }


def _solve(n, lost_load="empty", **kw):
    if lost_load == "empty":
        lost_load = _capture(n)
    return L.LinkedSolve(network=n, fresh=True, lost_load=lost_load, **kw)


def _per_year(n, x):
    """Σ x · w within each period (the 5 years cancel)."""
    x = pd.Series(x, index=n.snapshots) if not isinstance(x, pd.Series) else x
    if isinstance(n.snapshots, pd.MultiIndex):
        return {p: float(x.xs(p, level=0).sum() * W) for p in n.investment_periods}
    return {None: float(x.sum() * W)}


def _value(got):
    assert isinstance(got, L.Series), got
    return got.per_period


def _approx(d):
    return {k: pytest.approx(v) for k, v in d.items()}


# ── catalogue and versions ─────────────────────────────────────────────────────


def test_the_catalogue_of_the_plant():
    n = _plant()
    ids = {s.id: s for s in L.catalogue(n, _solve(n, export_link="grid_export"))}
    assert {i for i in ids if not i.startswith("capacity.")} == {
        "served_product.nh3",
        "served_product.conc",
        "shipped.shipments",
        "served_h2.h2_offtake",
        "served_load.site_a",
        "served_load.site_b",
        "derived_o2.electrolyser",
        "export.grid_export",
    }
    assert ids["served_product.nh3"].units == ("MWh", "t") and ids[
        "served_product.conc"
    ].units == ("t",)
    assert (
        ids["derived_o2.electrolyser"].derived
        and not ids["served_h2.h2_offtake"].derived
    )
    # Slacks and product-role generators are not assets with a capacity.
    assert {i for i in ids if i.startswith("capacity.")} == {
        "capacity.Generator.wind",
        "capacity.Generator.old",
        "capacity.Link.electrolyser",
        "capacity.Link.h2_import",
        "capacity.Link.grid_export",
    }
    # Every id fits 02's quantity-source pattern, and every unit is a line unit.
    for s in ids.values():
        B.LineItem(
            name="x",
            kind="revenue",
            category="product_sale",
            unit=s.units[0],
            price=1.0,
            escalation="contract",
            indexation=0.0,
            quantity_source=f"linked:{s.id}@{L.CATALOGUE_VERSION}",
        )
        assert set(s.units) <= set(get_args(B.Unit))


def test_a_name_the_source_pattern_cannot_carry_is_not_offered():
    n = _plant()
    n.add("Load", "AC load 1", bus="hub", p_set=1.0)
    assert not any("AC load 1" in s.id for s in L.catalogue(n))


def test_every_reader_accepts_each_catalogue_version():
    """#1132 F1: a stored line's version always has a reader, on the plant and on the data centre."""
    n = _plant()
    _spec, dc, dc_solve = _dc()
    cases = [(n, _solve(n, export_link="grid_export")), (dc, dc_solve)]
    assert set(L.READERS) == set(L.VERSIONS) and L.CATALOGUE_VERSION in L.VERSIONS
    kinds = {s.kind for net, solve in cases for s in L.catalogue(net, solve)}
    for version in L.VERSIONS:
        assert set(L.READERS[version]) == kinds
        read = set()
        for net, solve in cases:
            for s in L.catalogue(net, solve):
                for unit in s.units:
                    got = L.read(
                        net, solve, s.id, unit, version=version, kw_per_gpu=1.3
                    )
                    assert isinstance(got, L.Series), (s.id, unit, got)
                    read.add(s.kind)
        assert read == kinds


def test_an_unknown_id_or_version_is_linked_output_missing():
    n = _plant()
    solve = _solve(n)
    for output_id, version in (
        ("served_h2.nope", "1"),
        ("served_h2.h2_offtake", "0"),
        ("export.grid_export", "1"),
        ("it_energy", "1"),
        ("heat.hub", "1"),
    ):
        with pytest.raises(B.LinkedOutputMissing) as e:
            L.read(n, solve, output_id, "MWh", version=version)
        assert (
            e.value.code == "business_case_refused:linked_output_missing"
            and e.value.field == output_id
        )
    with pytest.raises(B.UnitNotAllowed):
        L.read(n, solve, "served_h2.h2_offtake", "t")
    assert {"linked_output_missing", "double_count"} <= set(B.REFUSAL_KINDS)


def test_resolve_reads_a_linked_line_in_its_unit():
    n = _plant()
    line = B.LineItem(
        name="h2",
        kind="revenue",
        category="product_sale",
        unit="kg",
        price=5.0,
        escalation="contract",
        indexation=0.0,
        quantity_source="linked:served_h2.h2_offtake@1",
    )
    got = L.resolve(line, n, _solve(n))
    assert got.unit == "kg" and got.output_id == "served_h2.h2_offtake"
    assert _value(got) == _approx(_per_year(n, 25.0 * 1000.0 / LHV))


# ── LO-1, P5: units from the constants the code has ──────────────────────────────


def test_h2_kg_by_the_commercial_lhv_or_the_lines_own():
    n = _plant()
    solve = _solve(n)
    mwh = _value(L.read(n, solve, "served_h2.h2_offtake", "MWh_H2"))
    assert mwh == _approx(_per_year(n, 25.0))
    assert LHV == 33.33
    assert _value(L.read(n, solve, "served_h2.h2_offtake", "kg")) == _approx(
        {p: v * 1000.0 / LHV for p, v in mwh.items()}
    )
    assert _value(
        L.read(n, solve, "served_h2.h2_offtake", "kg", lhv_kwh_per_kg=39.4)
    ) == _approx({p: v * 1000.0 / 39.4 for p, v in mwh.items()})
    assert (
        L.read(n, solve, "served_h2.h2_offtake", "kg", lhv_kwh_per_kg=0.0).reason
        == "lhv_not_positive"
    )


def test_tonnes_by_the_bus_mwh_per_t_only():
    n = _plant()
    solve = _solve(n)
    mwh = _value(L.read(n, solve, "served_product.nh3", "MWh"))
    assert mwh == _approx(_per_year(n, 20.0))
    assert A.MWH_PER_T == 5.166
    assert _value(L.read(n, solve, "served_product.nh3", "t")) == _approx(
        {p: v / A.MWH_PER_T for p, v in mwh.items()}
    )
    # P5: no fallback to the carrier catalogue's factor: without the bus column there is no t unit.
    n.buses.at["nh3", "mwh_per_t"] = np.nan
    (spec,) = [s for s in L.catalogue(n) if s.id == "served_product.nh3"]
    assert spec.units == ("MWh",)
    with pytest.raises(B.UnitNotAllowed):
        L.read(n, solve, "served_product.nh3", "t")


def test_gpu_hours_by_the_preset_rows_kw_per_gpu_and_never_a_default():
    spec, n, solve = _dc()
    mwh = _value(L.read(n, solve, "it_energy", "MWh"))
    assert _value(L.read(n, solve, "it_energy", "GPU_h", kw_per_gpu=1.3)) == _approx(
        {p: v * 1000.0 / 1.3 for p, v in mwh.items()}
    )
    assert L.read(n, solve, "it_energy", "GPU_h").reason == "kw_per_gpu_missing"


def test_o2_is_derived_at_the_physical_constant():
    """Q1: 7.94 kg/kg, never a preset's assumed 8; quantity only."""
    n = _plant()
    n.links_t.p1["electrolyser"] = -30.0
    got = L.read(n, _solve(n), "derived_o2.electrolyser", "t")
    assert L.O2_KG_PER_KG_H2 == 7.94 == pytest.approx(31.998 / (2 * 2.016), abs=5e-3)
    assert got.derived
    assert _value(got) == _approx(_per_year(n, 30.0 * 1000.0 / LHV * 7.94 / 1000.0))


# ── LO-2, LO-8, P4: served product, never the unit's output ────────────────────


def test_served_h2_is_the_offtake_less_its_shortfall_never_the_electrolyser():
    n = _plant()
    n.links_t.p1[
        "electrolyser"
    ] = -40.0  # the electrolyser makes more than is delivered (the rest is stored)
    lost = np.tile([5.0, 0.0, 25.0, 0.0], 2)
    got = _value(
        L.read(
            n, _solve(n, _capture(n, h2_offtake=lost)), "served_h2.h2_offtake", "MWh_H2"
        )
    )
    assert got == _approx(_per_year(n, 25.0 - lost))
    assert got != _approx(_per_year(n, 40.0))


def test_shipped_is_the_shipments_load_and_an_hour_may_fall_short_by_more_than_its_load():
    """LO-8: a shortfall filling the shed exceeds the hour's 8 t; the year's sum is not clipped."""
    n = _plant()
    lost = np.tile([20.0, 0.0, 0.0, 0.0], 2)
    n.generators_t.p["__lost_conc"] = lost
    got = _value(L.read(n, _solve(n), "shipped.shipments", "t"))
    assert (
        got
        == _approx(_per_year(n, 8.0 - lost))
        == _approx({2030: (32.0 - 20.0) * W, 2035: (32.0 - 20.0) * W})
    )
    assert _value(L.read(n, _solve(n), "served_product.conc", "t")) == got


def test_served_plus_the_product_shortfall_is_the_contracted_quantity():
    """P4 (a), G3: the shortfall counts exactly once, with ProductShortfall's own per-year number."""
    from services.process_products import shortfalls
    from services.results.finance_case import _per_year as shortfall_per_year

    n = _plant()
    n.generators_t.p["__lost_nh3"] = np.tile([0.0, 35.0, 5.0, 0.0], 2)
    solve = _solve(n)
    served = _value(L.read(n, solve, "served_product.nh3", "MWh"))
    (s,) = [s for s in shortfalls(n, solve.lost_load) if s.bus == "nh3"]
    for p in PERIODS:
        assert served[p] + shortfall_per_year(n, s.p, p) == pytest.approx(
            _per_year(n, 20.0)[p]
        )
        assert shortfall_per_year(n, s.p, p) == pytest.approx(40.0 * W)


def test_a_shortfall_capture_that_is_missing_or_stale_is_not_evaluated():
    n = _plant()
    n.remove("Generator", "__lost_nh3")
    n.buses["lost_production_value"] = np.nan
    n.buses.at["nh3", "lost_production_value"] = 150.0
    got = L.read(n, _solve(n), "served_product.nh3", "MWh")
    assert (
        isinstance(got, L.NotEvaluated)
        and got.reason == "lost_production_capture_missing"
    )
    stale = {
        **_capture(n),
        "lost_production": {
            "p_t": pd.DataFrame({"nh3": 0.0}, index=n.snapshots),
            "value": {"nh3": 150.0},
            "key": {"nh3": "another-network"},
        },
    }
    assert (
        L.read(n, _solve(n, stale), "served_product.nh3", "MWh").reason
        == "lost_production_capture_stale"
    )


# ── LO-3, P4: the electric load served ───────────────────────────────────────


def test_a_bus_slack_is_split_pro_rata_by_p_set_and_nothing_is_lost():
    n = _plant()
    shed = np.tile(
        [8.0, 3.0, 4.0, 0.0], 2
    )  # hour 2: site_b is 0, so all of it is site_a's
    n.generators_t.p["shed_hub"] = shed
    n.generators_t.p["dsr"] = 99.0  # demand response is not unserved
    solve = _solve(n)
    a = _value(L.read(n, solve, "served_load.site_a", "MWh"))
    b = _value(L.read(n, solve, "served_load.site_b", "MWh"))
    b_set = np.tile([30.0, 0.0, 10.0, 0.0], 2)
    a_lost = shed * 10.0 / (10.0 + b_set)
    assert a == _approx(_per_year(n, 10.0 - a_lost))
    assert b == _approx(_per_year(n, b_set - (shed - a_lost)))
    for p in PERIODS:  # conservation: Σ served = Σ p_set − the bus slack
        assert a[p] + b[p] == pytest.approx(_per_year(n, 10.0 + b_set - shed)[p])


def test_a_bus_slack_in_an_hour_with_no_load_is_still_counted():
    """P4 (b): Σ p_set(t) = 0 with slack ≠ 0 (a store filling): no divide by zero, nothing lost. F3: that
    hour's slack is split by each Load's share of the period's Σ p_set · w, so site_a, which draws nothing
    all period, takes none of it and never reads a negative served."""
    n = _plant()
    n.loads.at["site_a", "p_set"] = 0.0
    shed = np.tile([0.0, 6.0, 0.0, 0.0], 2)  # hour 2: both loads are 0
    n.generators_t.p["shed_hub"] = shed
    solve = _solve(n)
    a = _value(L.read(n, solve, "served_load.site_a", "MWh"))
    b = _value(L.read(n, solve, "served_load.site_b", "MWh"))
    b_set = np.tile([30.0, 0.0, 10.0, 0.0], 2)
    for p in PERIODS:
        assert a[p] + b[p] == pytest.approx(_per_year(n, b_set - shed)[p])
        # site_a's period share is 0 (it draws nothing), site_b's 1: all 6 MW are site_b's.
        assert a[p] == pytest.approx(0.0)
        assert b[p] == pytest.approx(_per_year(n, b_set - shed)[p])


def test_a_slack_in_both_the_capture_and_the_generators_frame_counts_once():
    """P4 (c)."""
    n = _plant()
    lost = np.tile([4.0, 0.0, 0.0, 1.0], 2)
    n.add("Generator", "__voll_site_a", bus="hub", carrier="load_shedding", p_nom=100.0)
    n.generators_t.p["__voll_site_a"] = lost
    both = _value(
        L.read(n, _solve(n, _capture(n, site_a=lost)), "served_load.site_a", "MWh")
    )
    assert both == _approx(_per_year(n, 10.0 - lost))
    # The same slack, left only as a Generator row, is that Load's own; a legacy bus-keyed column is the bus's.
    assert _value(L.read(n, _solve(n), "served_load.site_a", "MWh")) == both
    n.remove("Generator", "__voll_site_a")
    n.loads.at["site_b", "p_set"] = 0.0
    n.loads_t.p_set.drop(columns="site_b", inplace=True)
    assert (
        _value(L.read(n, _solve(n, _capture(n, hub=lost)), "served_load.site_a", "MWh"))
        == both
    )


def test_unserved_is_a_quantity_and_no_output_prices_it():
    """GK A standing point 2 and DC 03: nothing in the catalogue is an unserved quantity."""
    spec, n, solve = _dc()
    ids = [s.id for s in L.catalogue(n, solve)] + [s.id for s in L.catalogue(_plant())]
    assert not [
        i
        for i in ids
        if "unserved" in i or "lost" in i or "shortfall" in i.split(".", 1)[0]
    ]


# ── P3: the lost-load capture ──────────────────────────────────────────────────

SERVED = (
    ("served_product.nh3", "MWh", 20.0),
    ("served_h2.h2_offtake", "MWh_H2", 25.0),
    ("shipped.shipments", "t", 8.0),
    ("served_load.site_a", "MWh", 10.0),
)


@pytest.mark.parametrize(("output_id", "unit", "p_set"), SERVED)
def test_an_absent_capture_is_never_zero_unserved(output_id, unit, p_set):
    n = _plant()
    for lost_load in (None, {}, {"lost_production": {}}):
        got = L.read(n, _solve(n, lost_load), output_id, unit)
        assert isinstance(got, L.NotEvaluated) and got.reason == "no_lost_load_capture"
    other = pd.DataFrame(
        {"site_a": 0.0}, index=pd.date_range("2031-01-01", periods=8, freq="h")
    )
    assert (
        L.read(n, _solve(n, {"lost_load_t": other}), output_id, unit).reason
        == "lost_load_capture_other_solve"
    )
    renamed = _capture(n, a_load_of_another_network=np.zeros(8))
    assert (
        L.read(n, _solve(n, renamed), output_id, unit).reason
        == "lost_load_capture_other_solve"
    )
    # An explicit empty capture of this solve, or a solve with VoLL 0 (no transient slack in the LP): all served.
    for solve in (_solve(n), _solve(n, None, voll=0.0)):
        assert _value(L.read(n, solve, output_id, unit)) == _approx(_per_year(n, p_set))
    assert isinstance(
        L.read(n, _solve(n, None, voll=5000.0), output_id, unit), L.NotEvaluated
    )


# ── LO-5, P7: export ───────────────────────────────────────────────────────────


def test_export_is_read_at_the_grid_side_after_link_losses():
    n = _plant()
    p0 = np.tile([10.0, 0.0, 20.0, 5.0], 2)
    n.links_t.p0["grid_export"] = p0
    n.links_t.p1["grid_export"] = -0.9 * p0
    solve = _solve(n, export_link="grid_export")
    got = _value(L.read(n, solve, "export.grid_export", "MWh"))
    billed = _per_year(n, p0)  # what the export readers bill: p0, before losses
    assert got == _approx({p: 0.9 * v for p, v in billed.items()})
    assert all(v > 0 for v in got.values())  # positive = delivered to the grid
    (spec,) = [s for s in L.catalogue(n, solve) if s.kind == "export"]
    assert "grid side" in spec.label and "after link losses" in spec.label


# ── LO-6, P8: capacity ─────────────────────────────────────────────────────────


def test_capacity_is_the_active_assets_and_an_extendable_one_reads_p_nom_opt():
    n = _plant()
    solve = _solve(n)
    assert _value(L.read(n, solve, "capacity.Generator.wind", "MW_year")) == {
        2030: 42.0,
        2035: 42.0,
    }
    assert _value(L.read(n, solve, "capacity.Generator.wind", "kW_year")) == {
        2030: 42000.0,
        2035: 42000.0,
    }
    assert _value(L.read(n, solve, "capacity.Generator.wind", "kW_month")) == {
        2030: 504000.0,
        2035: 504000.0,
    }
    # Built 2025 with 8 years of life: in service in 2030, retired by 2035.
    assert _value(L.read(n, solve, "capacity.Generator.old", "MW_year")) == {
        2030: 7.0,
        2035: 0.0,
    }
    n.generators.at["wind", "p_nom_opt"] = np.nan
    got = L.read(n, solve, "capacity.Generator.wind", "MW_year")
    assert (
        isinstance(got, L.NotEvaluated) and got.reason == "capacity_not_solved"
    )  # never p_nom (5)
    n.generators.at["wind", "p_nom_opt"] = 0.0  # declined by the LP
    assert _value(L.read(n, solve, "capacity.Generator.wind", "MW_year")) == {
        2030: 0.0,
        2035: 0.0,
    }


def test_capacity_on_a_flat_network_reads_the_all_block():
    n = _plant(periods=())
    solve = _solve(n)
    assert _value(L.read(n, solve, "capacity.Generator.old", "MW_year")) == {None: 7.0}
    n.generators.at["old", "active"] = False
    assert _value(L.read(n, solve, "capacity.Generator.old", "MW_year")) == {None: 0.0}


# ── Q3: the period key ─────────────────────────────────────────────────────────


def test_a_flat_network_has_the_one_key_none_and_periods_expand_to_their_years():
    flat = _plant(periods=())
    got = L.read(flat, _solve(flat), "served_load.site_a", "MWh")
    assert got.per_period == {None: pytest.approx(10.0 * HOURS * W)} and got.years == {}
    assert got.for_year(2031) == got.for_year(2090) == pytest.approx(10.0 * HOURS * W)
    n = _plant()
    n.loads_t.p_set["site_a"] = np.r_[np.full(4, 10.0), np.full(4, 20.0)]
    got = L.read(n, _solve(n), "served_load.site_a", "MWh")
    assert got.years == {2030: 5.0, 2035: 5.0}
    assert [got.for_year(y) for y in (2029, 2030, 2034, 2035, 2039, 2040)] == [
        None,
        *[pytest.approx(v) for v in (120.0, 120.0, 240.0, 240.0)],
        None,
    ]


# ── LO-7, P2: the run's own solve ──────────────────────────────────────────────


def _ctx(n, as_dict=False, **state):
    from services import workflow_tools

    from services.solver_service import SolverConfig

    config = {"voll": 0.0, "commercial": {"export_link": "grid_export"}}
    ctx = SimpleNamespace(
        network=n,
        user_ts={},
        user_ts_unreadable=False,
        solver_state={
            # Held live as a SolverConfig; a saved one is a mapping.
            "solver_config": config if as_dict else SolverConfig(**config),
            "status": "completed",
            "condition": "optimal",
            "ac_pf_results": None,
            "lopf_results": None,
            "last_lost_load": None,
            **state,
        },
    )
    n.meta["assistant_solve_input_fingerprint"] = (
        workflow_tools._network_input_fingerprint(ctx)
    )
    n.meta["assistant_solve_exact_input_fingerprint"] = (
        workflow_tools._network_input_fingerprint(ctx, exact=True)
    )
    return ctx


def _read_all(n, solve):
    return [L.read(n, solve, s.id, s.units[0]) for s in L.catalogue(n, solve)]


def test_bind_reads_the_solve_of_the_context():
    n = _plant()
    solve = L.bind(_ctx(n))
    assert (
        solve.fresh
        and solve.network is n
        and solve.voll == 0.0
        and solve.export_link == "grid_export"
    )
    got = _read_all(n, solve)
    assert got and all(isinstance(g, L.Series) for g in got)
    saved = L.bind(_ctx(n, as_dict=True))  # the config as saved: a mapping
    assert (saved.fresh, saved.voll, saved.export_link) == (True, 0.0, "grid_export")


def test_inputs_edited_after_the_solve_are_not_evaluated():
    """P2 (a), (b): the stamp on the network is stale; bind re-hashes the inputs and does not trust it."""
    n = _plant()
    ctx = _ctx(n)
    n.loads.at["site_a", "p_set"] = 11.0
    solve = L.bind(ctx)
    assert not solve.fresh and n.meta["assistant_solve_exact_input_fingerprint"]
    got = _read_all(n, solve)
    assert got and all(g == L.NotEvaluated(g.output_id, "not_own_solve") for g in got)


@pytest.mark.parametrize(
    "state",
    [
        {
            "ac_pf_results": {"converged": True}
        },  # P2 (c): the live frames are a power flow's
        {"lopf_results": {"x": 1}},
        {
            "status": "failed",
            "condition": None,
        },  # P2 (d): a failed re-solve on the same inputs
        {"status": "completed", "condition": "infeasible"},
        {"status": "running"},
    ],
)
def test_a_solve_that_is_not_a_completed_optimal_lp_is_not_evaluated(state):
    n = _plant()
    solve = L.bind(_ctx(n, **state))
    got = _read_all(n, solve)
    assert not solve.fresh and got and all(isinstance(g, L.NotEvaluated) for g in got)


def test_an_unsolved_network_and_another_networks_solve_are_not_evaluated():
    n = _plant()
    ctx = _ctx(n)
    n.meta.clear()
    assert all(isinstance(g, L.NotEvaluated) for g in _read_all(n, L.bind(ctx)))
    other = _plant()
    solve = L.bind(_ctx(other))
    assert solve.fresh
    assert all(
        g == L.NotEvaluated(g.output_id, "not_own_solve") for g in _read_all(n, solve)
    )


# ── LO-10, P8: the data centre ─────────────────────────────────────────────────


def _dc(tid="eh_datacenter_staged"):
    spec = T.dc_load_spec(tid, {})
    n = T.BUILDERS[tid](load_spec=spec)
    n.generators_t.p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    return spec, n, _solve(n, dc=(spec, T._dc_timezone(tid)))


def test_it_energy_is_dc_03s_read_called_once_and_per_year(monkeypatch):
    from services import dc_it_energy as E

    spec, n, solve = _dc()
    want = E.it_energy(
        spec,
        n.snapshots,
        n.snapshot_weightings.generators,
        E.unserved_facility_mw(n),
        timezone=solve.dc[1],
    ).periods
    assert len(want) > 1  # multi-period
    calls = []
    real = E.it_energy
    monkeypatch.setattr(
        E, "it_energy", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    got = _value(L.read(n, solve, "it_energy", "MWh"))
    assert calls == [1]
    assert list(got) == [int(p) for p in want.index]
    assert list(got.values()) == pytest.approx(want["billable_it_mwh"].tolist())
    kw = _value(L.read(n, solve, "contracted_it_kw", "kW_month"))
    assert calls == [1, 1]
    # kW × 12 months, whatever the period's length in years.
    assert list(kw.values()) == pytest.approx(
        (want["contracted_it_kw"] * 12.0).tolist()
    )
    assert set(n.investment_period_weightings["years"]) != {1.0}


def test_unserved_it_energy_lowers_the_billable_quantity_and_is_not_an_output():
    spec, n, solve = _dc()
    full = _value(L.read(n, solve, "it_energy", "MWh"))
    n.add("Generator", "shed_it", bus="it_bus", carrier="load_shedding", p_nom=100.0)
    n.generators_t.p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    n.generators_t.p["shed_it"] = 1.0
    short = _value(L.read(n, solve, "it_energy", "MWh"))
    assert all(short[p] < full[p] for p in full)
    assert [
        s.id
        for s in L.catalogue(n, solve)
        if s.kind in ("it_energy", "contracted_it_kw")
    ] == ["it_energy", "contracted_it_kw"]


# ── the BUILT templates (GK A standing point 3, P1, P6) ───────────────────────────


def _built(tid):
    n = T.BUILDERS[tid]()
    n.generators_t.p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    n.links_t.p1 = pd.DataFrame(0.0, index=n.snapshots, columns=n.links.index)
    export = (T.solver_config(tid).get("commercial") or {}).get("export_link")
    return n, _solve(n, export_link=export)


def _load_per_year(n, loads):
    from services.period_utils import (
        period_years_map,
        snapshot_weights,
        years_for_period,
    )

    x = n.get_switchable_as_dense("Load", "p_set")[loads].sum(
        axis=1
    ) * snapshot_weights(n, "generators")
    years = period_years_map(n)
    return {
        int(p): float(v) / years_for_period(years, p)
        for p, v in x.groupby(level=0).sum().items()
    }


def test_built_h2_hub_offers_the_offtake_and_o2_for_the_electrolyser_fleet():
    n, solve = _built("eh_h2_hub_staged")
    ids = {s.id for s in L.catalogue(n, solve)}
    fleet = [
        str(k) for k in n.links.index if n.links.at[k, "carrier"] == "electrolysis"
    ]
    assert len(fleet) > 1 and {f"derived_o2.{k}" for k in fleet} == {
        i for i in ids if i.startswith("derived_o2.")
    }
    assert "served_h2.h2_offtake" in ids
    # The billed series is the built load (identity, zero slack).
    assert _value(L.read(n, solve, "served_h2.h2_offtake", "MWh_H2")) == _approx(
        _load_per_year(n, ["h2_offtake"])
    )
    # P1: a Link into the H2 bus that is not an electrolyser is not offered.
    n.add("Link", "h2_truck", bus0="hub", bus1="h2", p_nom=1.0, carrier="H2")
    assert "derived_o2.h2_truck" not in {s.id for s in L.catalogue(n, solve)}


def test_built_ammonia_offers_its_product_and_no_served_h2():
    """P6: the plant sells ammonia; its H2 goes through the haber_bosch Link."""
    n, solve = _built("eh_ammonia_staged")
    ids = {s.id for s in L.catalogue(n, solve)}
    assert not [i for i in ids if i.startswith("served_h2.")]
    assert "served_product.nh3" in ids and [
        i for i in ids if i.startswith("derived_o2.")
    ]
    mwh = _value(L.read(n, solve, "served_product.nh3", "MWh"))
    assert mwh == _approx(_load_per_year(n, ["nh3_delivery"]))
    assert _value(L.read(n, solve, "served_product.nh3", "t")) == _approx(
        {p: v / A.MWH_PER_T for p, v in mwh.items()}
    )


def test_built_concentrator_ships_tonnes_from_the_shipments_load():
    n, solve = _built("eh_concentrator_staged")
    assert "shipped.shipments" in {s.id for s in L.catalogue(n, solve)}
    assert _value(L.read(n, solve, "shipped.shipments", "t")) == _approx(
        _load_per_year(n, ["shipments"])
    )


def test_built_ipp_hybrid_offers_the_export_link_of_its_commercial_config():
    n, solve = _built("eh_ipp_hybrid")
    assert solve.export_link == "grid_export"
    assert "export.grid_export" in {s.id for s in L.catalogue(n, solve)}
    assert "export.grid_export" not in {s.id for s in L.catalogue(n)}


# ── LO-4, G2/G3: the ledger rule (slice 06 applies it) ────────────────────────────


def test_the_ledger_rule_for_each_stream_that_could_clash():
    from services.finance.report import ENERGY_BILL_STREAMS

    streams = set(get_args(ValueStreamKind))
    for stream in ("energy_export", "ppa_settlement", "cfd_settlement"):
        assert stream in streams
        assert L.ledger_clash("export.grid_export", {stream, "fom"}) == (
            "refuse",
            stream,
        )
    assert ENERGY_BILL_STREAMS and set(ENERGY_BILL_STREAMS) <= streams
    # The bill prices the site's grid import, which is no catalogue output: a served load is what the
    # plant sells (spec §6.7, a microgrid's tariff revenue), and the export quantity is not the bill's.
    for stream in ENERGY_BILL_STREAMS:
        assert L.ledger_clash("served_load.site_a", {stream}) is None
        assert L.ledger_clash("export.grid_export", {stream}) is None
    assert not [s.id for s in L.catalogue(_plant()) if "import" in s.kind]
    # G3: a product line is never refused; the business case drops lost_production instead.
    assert "lost_production" in streams
    for output_id in (
        "served_product.nh3",
        "served_h2.h2_offtake",
        "shipped.shipments",
    ):
        assert L.ledger_clash(output_id, {"lost_production", "energy_import"}) == (
            "drop",
            "lost_production",
        )
        assert L.ledger_clash(output_id, {"energy_import", "energy_export"}) is None
    for output_id in (
        "capacity.Link.electrolyser",
        "derived_o2.electrolyser",
        "it_energy",
        "contracted_it_kw",
    ):
        assert L.ledger_clash(output_id, streams) is None
    assert (
        L.ledger_clash(
            "served_load.site_a", {"lost_production", "energy_export", "fom"}
        )
        is None
    )
    assert B.DoubleCount("energy_export").code == "business_case_refused:double_count"


# ── LO-9: post-solve only ──────────────────────────────────────────────────────


def _frames(n) -> dict:
    out = {"meta": repr(sorted(n.meta.items()))}
    for c in n.components:
        out[c.name] = c.static.to_csv()
        for attr, frame in c.dynamic.items():
            out[f"{c.name}.{attr}"] = frame.to_csv()
    return out


def test_reading_every_output_leaves_the_network_untouched():
    n = _plant()
    n.generators_t.p["shed_hub"] = 2.0
    n.generators_t.p["__lost_nh3"] = 1.0
    solve = _solve(n, _capture(n, site_a=np.ones(8)), export_link="grid_export")
    before = _frames(n)
    assert all(isinstance(g, L.Series) for g in _read_all(n, solve))
    assert _frames(n) == before


def _imported_names(source: str, package: str) -> list[str]:
    """Every dotted name `source` imports, in any form: `import a.b`, `from a import b` (as `a` and
    `a.b`), and relative imports resolved against `package` (#1404 GK B item 4)."""
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".")
            base = ".".join(parts[: len(parts) - node.level + 1]) if node.level else ""
            module = ".".join(x for x in (base, node.module or "") if x)
            out.append(module)
            out += [f"{module}.{a.name}" for a in node.names]
    return out


def test_the_import_scan_sees_every_form():
    pkg = "services.results"
    for src in (
        "import services.finance.business_case",
        "from services.finance import business_case",
        "from services.finance.business_case import pnl",
        "from ..finance import business_case",
        "from .. import business_case",
        "from models.business_case import LineItem",
    ):
        assert any("business_case" in n for n in _imported_names(src, pkg)), src
    assert not any(
        "business_case" in n for n in _imported_names("from . import x", pkg)
    )


def test_no_engine_imports_the_business_case():
    """Nothing here can enter the LP, the IC, its hash or the adequacy facts: no engine module imports
    `services.business_case` or the line-item model. The one exception is the business case's own engine
    run, `services/finance/business_case.py` (BC 05): it reads the engine, and the engine never reads it."""
    own = BACKEND / "services/finance/business_case.py"
    offenders = []
    for folder in (
        "services/finance",
        "services/solver",
        "services/adequacy",
        "services/results",
        "services/commercial",
    ):
        for path in sorted((BACKEND / folder).rglob("*.py")):
            if path == own:
                continue
            rel = path.relative_to(BACKEND).with_suffix("")
            package = ".".join(rel.parts[:-1])
            if any(
                "business_case" in n for n in _imported_names(path.read_text(), package)
            ):
                offenders.append(str(path.relative_to(BACKEND)))
    assert not offenders


@pytest.mark.live_solve
def test_the_investment_case_is_byte_equal_with_and_without_linked_reads(reset_backend):
    """LO-9 pin, on the solved ammonia plant: the IC lines and the network are the same before and after
    every linked read; and G3's identity holds on a real solve (P4 a)."""
    from models.finance import FinanceInputs
    from services.results.finance_case import build_finance_case
    from tests.test_lost_production_slack import _half_buy_in, _live
    from tests.test_lost_production_slack import _solve as solved

    n, state, cfg = solved("hand_built", _half_buy_in)

    def case():
        return build_finance_case(
            n,
            cfg,
            FinanceInputs.model_validate(cfg.finance),
            result_df=_live,
            lost_load=state.get("last_lost_load"),
        )

    before, frames = case(), _frames(n)
    solve = L.LinkedSolve(
        network=n,
        fresh=True,
        lost_load=state.get("last_lost_load"),
        export_link=(cfg.commercial or {}).get("export_link")
        if isinstance(cfg.commercial, dict)
        else getattr(cfg.commercial, "export_link", None),
    )
    got = {s.id: L.read(n, solve, s.id, s.units[0]) for s in L.catalogue(n, solve)}
    after = case()
    assert repr(after) == repr(before) and _frames(n) == frames
    served = _value(got["served_product.nh3"])
    # ProductShortfall's own number (`finance_case._product_shortfall`): the slack Generator's dispatch per year.
    from services.results.finance_case import _per_year as shortfall_per_year

    shortfall = {
        p: shortfall_per_year(n, n.generators_t.p["__lost_nh3"], p) for p in served
    }
    assert shortfall and any(v > 0 for v in shortfall.values())
    contracted = _load_per_year(n, ["nh3_delivery"])
    for p, v in shortfall.items():
        assert served[p] + v == pytest.approx(contracted[p])


# ── review findings (PR body) ───────────────────────────────────────────────────


def test_the_it_energy_reads_the_solves_capture_not_only_the_generator_rows():
    """P3 for L7: the per-Load VoLL slacks are removed after the solve, so the facility's unserved MW
    comes from the capture too; and an absent capture is not evaluated."""
    from services import dc_it_energy as E

    spec, n, solve = _dc()
    full = _value(L.read(n, solve, "it_energy", "MWh"))
    for lost_load in (None, {}):
        got = L.read(n, _solve(n, lost_load, dc=solve.dc), "it_energy", "MWh")
        assert got == L.NotEvaluated("it_energy", "no_lost_load_capture")
    it_load = next(iter(n.loads.index[n.loads["bus"] == E.IT_BUS]))
    captured = _solve(
        n, _capture(n, **{it_load: np.full(len(n.snapshots), 5.0)}), dc=solve.dc
    )
    short = _value(L.read(n, captured, "it_energy", "MWh"))
    assert all(short[p] < full[p] for p in full)
    # With the slack left as Generator rows (DC 03's own case) the facility MW is DC 03's, exactly.
    n.add("Generator", "shed_it", bus=E.IT_BUS, carrier="load_shedding", p_nom=100.0)
    n.add("Generator", "shed_mv", bus=E.MV_BUS, carrier="load_shedding", p_nom=100.0)
    n.generators_t.p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    n.generators_t.p["shed_it"] = 2.0
    n.generators_t.p["shed_mv"] = 3.0
    pd.testing.assert_series_equal(
        L._facility_unserved(n, solve), E.unserved_facility_mw(n), check_names=False
    )
    assert E.unserved_facility_mw(n).min() > 2.0


def test_a_vintage_expanded_asset_reads_each_periods_own_builds():
    n = _plant()
    n.meta["vintage_results"] = {
        "Generator": {
            "wind": {
                "initial_capacity": 2.0,
                "periods": [{"build_year": 2035, "p_nom_opt": 40.0}],
            }
        }
    }
    got = _value(L.read(n, _solve(n), "capacity.Generator.wind", "MW_year"))
    assert got == {2030: 2.0, 2035: 42.0}


def test_a_feeding_or_missing_load_takes_no_share_of_the_bus_slack():
    n = _plant()
    n.loads_t.p_set["site_b"] = np.tile([-9.0, np.nan, 10.0, 0.0], 2)
    shed = np.full(8, 2.0)
    n.generators_t.p["shed_hub"] = shed
    a = _value(L.read(n, _solve(n), "served_load.site_a", "MWh"))
    a_lost = np.tile([2.0, 2.0, 1.0, 2.0], 2)
    assert a == _approx(_per_year(n, 10.0 - a_lost))


def test_a_shortfall_generator_on_an_involuntary_carrier_counts_once():
    n = _plant()
    n.generators.at["__lost_nh3", "carrier"] = "load_shedding"
    n.generators_t.p["__lost_nh3"] = 5.0
    got = _value(L.read(n, _solve(n), "served_product.nh3", "MWh"))
    assert got == _approx(_per_year(n, 15.0))


def test_for_year_takes_the_latest_period_that_has_started():
    s = L.Series("x", "MWh", {2030: 1.0, 2035: 2.0}, {2030: 10.0, 2035: 3.0})
    assert [s.for_year(y) for y in (2029, 2030, 2034, 2035, 2037, 2038)] == [
        None,
        1.0,
        1.0,
        2.0,
        2.0,
        None,
    ]


# ── guards the mutation run found untested ─────────────────────────────────────


def test_a_bus_asset_or_export_link_the_source_pattern_cannot_carry_is_not_offered():
    n = _plant()
    n.add("Bus", "slag heap", carrier="concentrate", unit="t")
    n.add("Load", "slag_out", bus="slag heap", p_set=1.0)
    n.add("Generator", "wind farm", bus="hub", carrier="wind", p_nom=1.0)
    n.add("Link", "grid export 2", bus0="hub", bus1="grid", p_nom=1.0, carrier="AC")
    ids = [s.id for s in L.catalogue(n, _solve(n, export_link="grid export 2"))]
    assert "shipped.slag_out" in ids
    assert not [
        i for i in ids if "slag heap" in i or "wind farm" in i or "grid export" in i
    ]


def test_a_product_bus_with_no_load_and_an_export_link_the_network_lacks_are_not_offered():
    n = _plant()
    n.add("Bus", "ore", carrier="concentrate", unit="t")
    ids = {s.id for s in L.catalogue(n, _solve(n, export_link="no_such_link"))}
    assert "served_product.ore" not in ids
    assert not [i for i in ids if i.startswith("export.")]


def test_a_link_into_a_bus_the_network_lacks_is_no_electrolyser():
    n = _plant()
    n.add(
        "Link",
        "electrolyser_b",
        bus0="hub",
        bus1="nowhere",
        p_nom=1.0,
        carrier="electrolysis",
    )
    ids = {s.id for s in L.catalogue(n)}
    assert "derived_o2.electrolyser_b" not in ids
    assert "capacity.Link.electrolyser_b" in ids


def test_a_link_named_like_a_shortfall_slack_still_offers_its_capacity():
    """The product-role rule hides Generators only."""
    n = _plant()
    n.add("Link", "__lost_bypass", bus0="hub", bus1="grid", p_nom=1.0, carrier="AC")
    assert "capacity.Link.__lost_bypass" in {s.id for s in L.catalogue(n)}


def test_a_capture_naming_every_load_and_bus_of_the_network_is_this_solves():
    n = _plant()
    names = [*map(str, n.loads.index), *map(str, n.buses.index)]
    capture = _capture(n, **{k: np.zeros(8) for k in names})
    got = L.read(n, _solve(n, capture), "served_load.site_a", "MWh")
    assert _value(got) == _approx(_per_year(n, 10.0))


def test_a_capture_column_of_another_bus_or_of_a_load_named_like_this_bus_is_not_its_slack():
    n = _plant()
    lost = np.full(8, 5.0)
    got = L.read(n, _solve(n, _capture(n, grid=lost)), "served_load.site_a", "MWh")
    assert _value(got) == _approx(_per_year(n, 10.0))
    n.add(
        "Load", "hub", bus="grid", p_set=6.0
    )  # a Load on grid, named like the hub bus
    got = L.read(n, _solve(n, _capture(n, hub=lost)), "served_load.site_a", "MWh")
    assert _value(got) == _approx(_per_year(n, 10.0))


def test_an_involuntary_slack_generator_with_no_dispatch_column_adds_nothing():
    n = _plant()
    n.add("Generator", "shed_late", bus="hub", carrier="load_shedding", p_nom=100.0)
    assert "shed_late" not in n.generators_t.p.columns
    got = L.read(n, _solve(n), "served_load.site_a", "MWh")
    assert _value(got) == _approx(_per_year(n, 10.0))


def test_a_shortfall_on_another_product_bus_does_not_lower_this_ones():
    n = _plant()
    n.generators_t.p["__lost_conc"] = 5.0
    got = L.read(n, _solve(n), "served_product.nh3", "MWh")
    assert _value(got) == _approx(_per_year(n, 20.0))


def test_a_shortfall_generator_with_no_dispatch_is_not_evaluated():
    n = _plant()
    n.generators_t.p = n.generators_t.p.drop(columns="__lost_nh3")
    got = L.read(n, _solve(n), "served_product.nh3", "MWh")
    assert got == L.NotEvaluated("served_product.nh3", "lost_production_not_read")


def test_a_non_finite_lhv_is_refused():
    n = _plant()
    n.links_t.p1["electrolyser"] = -30.0
    for output_id in ("served_h2.h2_offtake", "derived_o2.electrolyser"):
        unit = "kg" if output_id.startswith("served_h2") else "t"
        got = L.read(n, _solve(n), output_id, unit, lhv_kwh_per_kg=float("inf"))
        assert got == L.NotEvaluated(output_id, "lhv_not_positive")


def test_a_built_asset_reads_its_p_nom_whatever_its_p_nom_opt():
    n = _plant()
    n.generators.at["old", "p_nom_opt"] = np.nan
    got = L.read(n, _solve(n), "capacity.Generator.old", "MW_year")
    assert _value(got) == {2030: 7.0, 2035: 0.0}


@pytest.mark.parametrize(
    ("output_id", "link"),
    [
        ("export.grid_export", "grid_export"),
        ("derived_o2.electrolyser", "electrolyser"),
    ],
)
def test_a_link_with_no_dispatch_column_is_not_evaluated(output_id, link):
    n = _plant()
    n.links_t.p1 = n.links_t.p1.drop(columns=link)
    unit = "MWh" if output_id.startswith("export") else "t"
    got = L.read(n, _solve(n, export_link="grid_export"), output_id, unit)
    assert got == L.NotEvaluated(output_id, "link_not_dispatched")


def test_it_energy_periods_that_differ_from_the_networks_are_not_evaluated(
    monkeypatch,
):
    from services import dc_it_energy as E

    spec, n, solve = _dc()
    real = E.it_energy
    monkeypatch.setattr(
        E,
        "it_energy",
        lambda *a, **k: SimpleNamespace(periods=real(*a, **k).periods.iloc[:-1]),
    )
    for output_id, unit in (("it_energy", "MWh"), ("contracted_it_kw", "kW_month")):
        got = L.read(n, solve, output_id, unit)
        assert got == L.NotEvaluated(output_id, "it_energy_periods_mismatch")


@pytest.mark.parametrize("kw", [float("inf"), float("nan"), 0.0, -1.3])
def test_a_non_finite_zero_or_negative_kw_per_gpu_is_refused(kw):
    spec, n, solve = _dc()
    got = L.read(n, solve, "it_energy", "GPU_h", kw_per_gpu=kw)
    assert got == L.NotEvaluated("it_energy", "kw_per_gpu_missing")


def test_a_non_finite_quantity_is_not_evaluated_never_a_number():
    n = _plant()
    n.links_t.p1["grid_export"] = np.r_[-np.inf, np.zeros(7)]
    got = L.read(n, _solve(n, export_link="grid_export"), "export.grid_export", "MWh")
    assert got == L.NotEvaluated("export.grid_export", "not_finite")


# ── GK A second pass: B1, F2-F6 ────────────────────────────────────────────────


def _lost_production(n, bus, value, p):
    """A solve's lost-production capture on `bus` (`services.solver.lost_production`), keyed to this network."""
    from services.process_products import capture_key

    if "lost_production_value" not in n.buses.columns:
        n.buses["lost_production_value"] = np.nan
    n.buses.at[bus, "lost_production_value"] = value
    return {
        **_capture(n),
        "lost_production": {
            "p_t": pd.DataFrame({bus: p}, index=n.snapshots),
            "value": {bus: value},
            "key": {bus: capture_key(n, bus, value)},
        },
    }


@pytest.mark.parametrize(
    ("bus", "output_ids", "contracted"),
    [
        ("h2", ["served_h2.h2_offtake"], 25.0),
        (
            "hub",
            ["served_load.site_a", "served_load.site_b"],
            10.0 + np.tile([30.0, 0.0, 10.0, 0.0], 2),
        ),
    ],
)
def test_a_lost_production_slack_on_any_bus_lowers_what_it_serves(
    bus, output_ids, contracted
):
    """B1: `lost_production_value` is a field of every bus and the solve adds a slack on each; served plus
    ProductShortfall's own per-year quantity is the contracted quantity, the slack counted once."""
    from services.process_products import shortfalls
    from services.results.finance_case import _per_year as shortfall_per_year

    n = _plant()
    lost = np.tile([5.0, 5.0, 0.0, 0.0], 2)
    solve = _solve(n, _lost_production(n, bus, 100.0, lost))
    (s,) = [s for s in shortfalls(n, solve.lost_load) if s.bus == bus]
    assert s.flag is None
    unit = "MWh_H2" if bus == "h2" else "MWh"
    served = [_value(L.read(n, solve, o, unit)) for o in output_ids]
    for p in PERIODS:
        assert shortfall_per_year(n, s.p, p) == pytest.approx(10.0 * W)
        assert sum(v[p] for v in served) + shortfall_per_year(
            n, s.p, p
        ) == pytest.approx(_per_year(n, contracted)[p])


def test_tonnes_by_this_buses_own_mwh_per_t():
    """F2: the factor is the bus's own `mwh_per_t`, never the ammonia template's constant."""
    n = _plant()
    n.buses.at["nh3", "mwh_per_t"] = 4.0
    solve = _solve(n)
    mwh = _value(L.read(n, solve, "served_product.nh3", "MWh"))
    assert _value(L.read(n, solve, "served_product.nh3", "t")) == _approx(
        {p: v / 4.0 for p, v in mwh.items()}
    )


def test_a_bus_slack_in_a_period_with_no_load_is_not_evaluated():
    """F3: no Load on the bus draws in the whole period, so the slack has no one to be split to."""
    n = _plant()
    n.loads.at["site_a", "p_set"] = 0.0
    n.loads_t.p_set["site_b"] = np.r_[np.zeros(4), [30.0, 0.0, 10.0, 0.0]]
    n.generators_t.p["shed_hub"] = np.tile([0.0, 6.0, 0.0, 0.0], 2)
    for load in ("site_a", "site_b"):
        got = L.read(n, _solve(n), f"served_load.{load}", "MWh")
        assert got == L.NotEvaluated(f"served_load.{load}", "unserved_no_load_to_split")
    # The same period with no slack in it reads 0 served.
    n.generators_t.p["shed_hub"] = np.r_[np.zeros(4), [0.0, 6.0, 0.0, 0.0]]
    got = _value(L.read(n, _solve(n), "served_load.site_b", "MWh"))
    assert got == _approx({2030: 0.0, 2035: (40.0 - 6.0) * W})


def test_a_capture_that_is_not_a_frame_is_not_evaluated():
    """F4."""
    n = _plant()
    got = L.read(n, _solve(n, {"lost_load_t": [1.0, 2.0]}), "served_load.site_a", "MWh")
    assert got == L.NotEvaluated("served_load.site_a", "lost_load_capture_other_solve")


def test_a_capture_with_a_missing_value_in_a_used_column_is_not_evaluated():
    """F5: a NaN unserved MW is unknown, never 0 (served)."""
    n = _plant()
    lost = np.r_[np.nan, np.zeros(7)]
    for col in ("site_a", "hub"):
        got = L.read(
            n, _solve(n, _capture(n, **{col: lost})), "served_load.site_a", "MWh"
        )
        assert got == L.NotEvaluated(
            "served_load.site_a", "lost_load_capture_not_finite"
        )
    # A NaN in a column this read does not use leaves it alone.
    ok = L.read(n, _solve(n, _capture(n, h2_offtake=lost)), "served_load.site_a", "MWh")
    assert _value(ok) == _approx(_per_year(n, 10.0))


def test_it_energy_periods_are_matched_by_label_never_by_position(monkeypatch):
    """F6."""
    from services import dc_it_energy as E

    spec, n, solve = _dc()
    want = _value(L.read(n, solve, "it_energy", "MWh"))
    real = E.it_energy

    def reorder(*a, **k):
        return SimpleNamespace(periods=real(*a, **k).periods.iloc[::-1])

    monkeypatch.setattr(E, "it_energy", reorder)
    assert _value(L.read(n, solve, "it_energy", "MWh")) == _approx(want)

    def relabel(*a, **k):
        periods = real(*a, **k).periods
        return SimpleNamespace(periods=periods.set_axis(periods.index + 1))

    monkeypatch.setattr(E, "it_energy", relabel)
    for output_id, unit in (("it_energy", "MWh"), ("contracted_it_kw", "kW_month")):
        got = L.read(n, solve, output_id, unit)
        assert got == L.NotEvaluated(output_id, "it_energy_periods_mismatch")


def test_an_edit_below_the_lossy_fingerprints_precision_is_still_not_evaluated():
    """P2: only the exact fingerprint sees a 1e-12 edit; the lossy one rounds it away."""
    from services import workflow_tools

    n = _plant()
    ctx = _ctx(n)
    n.loads.at["site_a", "p_set"] = 10.0 + 1e-12
    assert n.meta[
        "assistant_solve_input_fingerprint"
    ] == workflow_tools._network_input_fingerprint(ctx)
    assert not L.bind(ctx).fresh


def test_it_energy_with_a_period_the_network_lacks_is_not_evaluated(monkeypatch):
    """F6: an extra period is a mismatch, never dropped by the label match."""
    from services import dc_it_energy as E

    spec, n, solve = _dc()
    real = E.it_energy

    def extra(*a, **k):
        periods = real(*a, **k).periods
        row = periods.iloc[[-1]].set_axis([periods.index.max() + 50])
        return SimpleNamespace(periods=pd.concat([periods, row]))

    monkeypatch.setattr(E, "it_energy", extra)
    assert L.read(n, solve, "it_energy", "MWh") == L.NotEvaluated(
        "it_energy", "it_energy_periods_mismatch"
    )


def test_it_energy_on_a_flat_network_reads_its_one_period_as_none(monkeypatch):
    """F6: DC 03 keys a flat network's one period by the snapshots' year; the read keys it None."""
    from services import dc_it_energy as E

    n = _plant(periods=())
    solve = _solve(n, dc=(None, "UTC"))
    frame = pd.DataFrame(
        {"billable_it_mwh": [12.0], "contracted_it_kw": [3.0]}, index=[2030]
    )
    monkeypatch.setattr(E, "it_energy", lambda *a, **k: SimpleNamespace(periods=frame))
    assert _value(L.read(n, solve, "it_energy", "MWh")) == {None: 12.0}


def test_a_bus_slack_in_an_hour_with_no_load_on_a_flat_network_is_split_by_its_one_period():
    """F3 on a flat network: the one period is every snapshot, so the idle hour's slack goes to the Load
    that draws in that period (kills the mutant that keys the period by one row)."""
    n = _plant(periods=())
    n.loads.at["site_a", "p_set"] = 0.0
    shed = np.array([0.0, 6.0, 0.0, 0.0])  # hour 2: both loads are 0
    n.generators_t.p["shed_hub"] = shed
    solve = _solve(n)
    b_set = np.array([30.0, 0.0, 10.0, 0.0])
    assert _value(L.read(n, solve, "served_load.site_a", "MWh")) == _approx({None: 0.0})
    assert _value(L.read(n, solve, "served_load.site_b", "MWh")) == _approx(
        _per_year(n, b_set - shed)
    )


def test_served_plus_the_product_shortfall_plus_the_loads_own_voll_is_contracted():
    """GK A F7 pin: on a bus with both a lost-production slack and a Load's own VoLL slack, served +
    ProductShortfall's per-year quantity + that VoLL slack = contracted, each slack counted once."""
    from services.process_products import shortfalls
    from services.results.finance_case import _per_year as shortfall_per_year

    n = _plant()
    lost = np.tile([5.0, 5.0, 0.0, 0.0], 2)
    voll = np.tile([2.0, 0.0, 3.0, 0.0], 2)
    capture = _lost_production(n, "hub", 100.0, lost)
    capture["lost_load_t"] = _capture(n, site_a=voll)["lost_load_t"]
    solve = _solve(n, capture)
    (s,) = [s for s in shortfalls(n, solve.lost_load) if s.bus == "hub"]
    served = [
        _value(L.read(n, solve, o, "MWh"))
        for o in ("served_load.site_a", "served_load.site_b")
    ]
    contracted = 10.0 + np.tile([30.0, 0.0, 10.0, 0.0], 2)
    for p in PERIODS:
        assert sum(v[p] for v in served) + shortfall_per_year(n, s.p, p) + _per_year(
            n, voll
        )[p] == pytest.approx(_per_year(n, contracted)[p])
        assert _per_year(n, voll)[p] > 0 and shortfall_per_year(n, s.p, p) > 0
