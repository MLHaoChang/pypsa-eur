"""
`services.adequacy.process_loss` (grid-to-process ticket 08, design §5): an outage turned into tonnes, then EUR.

Hand-built "solved" networks (dispatch written in, no solve), so each rule is pinned on numbers that can be read
off. The ammonia template's acceptance check (a 12 h grid outage at full Haber-Bosch load) is at the end.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy.process_loss import outage_loss

H = 24
RATE_MWH = 34.44                      # the unit's output at full load, MWh_NH3 per hour
MWH_PER_T = 5.166


def _net(*, output=RATE_MWH, delivery=RATE_MWH, level=100.0, tier="process_critical", weight=1.0,
         value=800.0 / MWH_PER_T, e_min_pu=0.0, min_down=8):
    """One process unit hub → nh3 at a constant output, a tank at a constant level, a delivery Load and a
    shortfall Generator priced at `value` (EUR/MWh_NH3; None: no shortfall Generator)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2035-01-01", periods=H, freq="h"))
    n.snapshot_weightings.loc[:, "stores"] = weight
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.buses["mwh_per_t"] = [float("nan"), MWH_PER_T]
    n.add("Link", "hb", bus0="hub", bus1="nh3", p_nom=10.0, efficiency=output / 8.5, min_down_time=min_down)
    n.links["eh_process"] = True
    n.links["criticality_tier"] = tier
    n.add("Store", "tank", bus="nh3", e_nom=1000.0, e_min_pu=e_min_pu)
    n.add("Load", "delivery", bus="nh3", p_set=delivery)
    n.generators["process_role"] = ""
    if value is not None:
        n.add("Generator", "short", bus="nh3", p_nom=100.0, marginal_cost=value)
        n.generators.loc["short", "process_role"] = "shortfall"
    n.links_t.p0["hb"] = pd.Series(8.5, index=n.snapshots)
    n.links_t.p1["hb"] = pd.Series(-output, index=n.snapshots)
    n.stores_t.e["tank"] = pd.Series(level, index=n.snapshots)
    n.loads_t.p["delivery"] = pd.Series(delivery, index=n.snapshots)
    return n


def _one(result, key):
    (row,) = result[key]
    return row


def test_a_process_critical_stop_loses_the_outage_plus_its_restart_less_the_tank():
    r = outage_loss(_net(), None, 5, 12.0)
    u, p = _one(r, "units"), _one(r, "products")
    rate_t = RATE_MWH / MWH_PER_T
    assert u["restart_snapshots"] == 8 and u["restart_h"] == 8.0
    assert u["gross_t"] == pytest.approx(20 * rate_t)
    # Delivery equals the output here, so undelivered = gross − the tank's usable level.
    assert p["undelivered_t"] == pytest.approx(20 * rate_t - 100.0 / MWH_PER_T)
    assert p["covered_t"] == pytest.approx(100.0 / MWH_PER_T)
    assert p["lost_eur"] == pytest.approx(p["undelivered_t"] * 800.0)
    assert p["value_eur_per_t"] == pytest.approx(800.0)


def test_undelivered_tonnes_follow_the_delivery_not_the_production_when_the_tank_fills():
    """The unit makes more than is delivered (the tank is filling): what the offtake misses is the delivery
    in the window less the tank, not the production lost (gatekeeper)."""
    delivery = 0.75 * RATE_MWH
    p = _one(outage_loss(_net(delivery=delivery), None, 5, 12.0), "products")
    assert p["undelivered_t"] == pytest.approx((20 * delivery - 100.0) / MWH_PER_T)
    assert p["undelivered_t"] < 20 * RATE_MWH / MWH_PER_T - 100.0 / MWH_PER_T


def test_a_flexible_unit_loses_the_outage_only_and_a_deferrable_one_nothing():
    rate_t = RATE_MWH / MWH_PER_T
    flex = outage_loss(_net(tier="process_flexible", level=0.0), None, 5, 12.0)
    assert _one(flex, "units")["gross_t"] == pytest.approx(12 * rate_t)
    assert _one(flex, "products")["undelivered_t"] == pytest.approx(12 * rate_t)
    later = outage_loss(_net(tier="deferrable", level=0.0), None, 5, 12.0)
    assert _one(later, "units")["gross_t"] == 0.0 and later["products"] == []


def test_a_safety_unit_is_unserved_electricity_not_tonnes():
    r = outage_loss(_net(tier="safety"), None, 5, 12.0)
    u = _one(r, "units")
    assert u["unserved_mwh"] == pytest.approx(12 * 8.5)
    assert u["gross_t"] == 0.0 and r["products"] == []


def test_a_unit_without_a_tier_is_treated_as_process_critical_and_flagged():
    r = outage_loss(_net(tier=""), None, 5, 12.0)
    assert "tier_unset:hb" in r["flags"]
    assert _one(r, "units")["restart_snapshots"] == 8


def test_the_restart_is_converted_from_snapshots_to_elapsed_hours():
    """min_down_time counts snapshots: on 2-hour snapshots, 8 of them are 16 h, and 12 h is 6 snapshots."""
    r = outage_loss(_net(weight=2.0, level=0.0), None, 0, 12.0)
    u = _one(r, "units")
    assert r["outage_snapshots"] == 6
    assert (u["restart_snapshots"], u["restart_h"], u["down_h"]) == (8, 16.0, 28.0)


def test_the_tank_counts_from_its_level_at_the_start_and_only_above_its_minimum():
    n = _net(e_min_pu=0.05)
    n.stores_t.e["tank"] = pd.Series(np.arange(H, dtype=float) * 10.0 + 100.0, index=n.snapshots)
    p = _one(outage_loss(n, None, 5, 12.0), "products")
    # e(t) is end-of-snapshot: the level when the outage starts at snapshot 5 is e(4) = 140, less 5 % of 1000.
    assert p["inventory_t"] == pytest.approx((140.0 - 50.0) / MWH_PER_T)
    # At the first snapshot it wraps to the period's last (the week repeats).
    p0 = _one(outage_loss(n, None, 0, 12.0), "products")
    assert p0["inventory_t"] == pytest.approx((100.0 + 23 * 10.0 - 50.0) / MWH_PER_T)


def test_covered_tonnes_never_exceed_what_was_lost():
    p = _one(outage_loss(_net(tier="process_flexible", level=5000.0), None, 5, 2.0), "products")
    assert p["covered_t"] == pytest.approx(2 * RATE_MWH / MWH_PER_T)
    assert p["undelivered_t"] == 0.0 and p["lost_eur"] == 0.0


def test_without_a_lost_production_value_the_eur_is_unavailable_and_flagged():
    p = _one(outage_loss(_net(value=None), None, 5, 12.0), "products")
    assert p["undelivered_t"] > 0
    assert p["lost_eur"] is None and p["flag"] == "lost_production_value_missing"


def test_units_on_one_tank_are_capped_together():
    n = _net(level=200.0, delivery=2 * RATE_MWH)
    n.add("Link", "hb2", bus0="hub", bus1="nh3", p_nom=10.0, efficiency=RATE_MWH / 8.5, min_down_time=8)
    n.links.loc["hb2", ["eh_process", "criticality_tier"]] = [True, "process_critical"]
    n.links_t.p0["hb2"] = 8.5
    n.links_t.p1["hb2"] = -RATE_MWH
    r = outage_loss(n, None, 5, 12.0)
    (p,) = r["products"]
    assert p["undelivered_t"] == pytest.approx((2 * 20 * RATE_MWH - 200.0) / MWH_PER_T)
    # Only one stops: the other keeps running and its output counts against the delivery.
    one = _one(outage_loss(n, None, 5, 12.0, units=["hb"]), "products")
    assert one["running_t"] == pytest.approx(20 * RATE_MWH / MWH_PER_T)
    assert one["undelivered_t"] == pytest.approx((20 * RATE_MWH - 200.0) / MWH_PER_T)


# ── the A template (acceptance, ticket 08) ─────────────────────────────────


@pytest.mark.live_solve
def test_a_12_hour_grid_outage_at_full_haber_bosch_load_in_the_ammonia_template(reset_backend):
    """Lost NH3 = (outage + restart) × rate, less what the NH3 tank covers: the undelivered tonnes the
    shortfall prices, from the solved delivery and the Haber-Bosch rate."""
    tpl = pathlib.Path(__file__).resolve().parents[1] / "project_templates"
    sys.path.insert(0, str(tpl))
    import eh_ammonia as A  # noqa: PLC0415
    import eh_templates as T  # noqa: PLC0415

    from services.adequacy.energization import apply_energization

    n = T.BUILDERS["eh_ammonia_staged"]()
    n.snapshots.name = "snapshot"
    apply_energization(n)
    status, cond = n.optimize(solver_name="highs", multi_investment_periods=True,
                              linearized_unit_commitment=True)
    assert (status, cond) == ("ok", "optimal")
    sns = n.snapshots[n.snapshots.get_level_values(0) == 2035]
    p0 = n.links_t.p0["haber_bosch"].loc[2035].to_numpy()
    full = np.isclose(p0, n.links.at["haber_bosch", "p_nom"], rtol=1e-6)
    # A start with the loop at full load for the whole 20-snapshot window.
    start = next(i for i in range(len(sns)) if all(full[(i + k) % len(sns)] for k in range(20)))
    r = outage_loss(n, 2035, start, 12.0, units=["haber_bosch"])
    u, p = _one(r, "units"), _one(r, "products")
    rate_t = A.HB_TPD / 24.0
    assert u["rate_t_h"] == pytest.approx(rate_t, rel=1e-6)
    assert (u["restart_snapshots"], u["restart_h"]) == (A.HB_MIN_DOWN_H, float(A.HB_MIN_DOWN_H))
    assert u["gross_t"] == pytest.approx((12 + A.HB_MIN_DOWN_H) * rate_t, rel=1e-6)
    window = [(start + k) % len(sns) for k in range(20)]
    delivered_t = float(n.loads_t.p["nh3_delivery"].loc[2035].to_numpy()[window].sum()) / A.MWH_PER_T
    level_t = float(n.stores_t.e["nh3_tank"].loc[2035].to_numpy()[(start - 1) % len(sns)]) / A.MWH_PER_T
    assert p["undelivered_t"] == pytest.approx(max(0.0, delivered_t - level_t), rel=1e-9, abs=1e-9)
    assert p["lost_eur"] == pytest.approx(p["undelivered_t"] * A.LOST_EUR_PER_T, rel=1e-9, abs=1e-6)


# ── the failure-mode sweep's link outages (ticket 08b) ─────────────────────

from services.adequacy.process_loss import (  # noqa: E402
    expected_outage_loss,
    lost_production_summary,
    units_cut_off,
)


def _supplied(n, *, local=False):
    """`_net`'s unit fed through an import Link from a grid Generator; `local` adds a battery on the unit's bus."""
    n.add("Bus", "grid")
    n.add("Generator", "supply", bus="grid", p_nom=100.0)
    n.add("Link", "imp", bus0="grid", bus1="hub", p_nom=100.0)
    n.links.loc["imp", ["eh_process", "criticality_tier"]] = [False, ""]
    if local:
        n.add("StorageUnit", "bess", bus="hub", p_nom=10.0)
    return n


def test_an_import_outage_cuts_off_the_units_with_no_other_source():
    assert units_cut_off(_supplied(_net()), "imp") == ["hb"]
    assert units_cut_off(_supplied(_net(), local=True), "imp") == []      # a path is not a capacity: stated
    assert units_cut_off(_supplied(_net()), "elsewhere") == []


def test_a_process_units_own_outage_stops_that_unit_however_well_its_bus_is_supplied():
    assert units_cut_off(_supplied(_net(), local=True), "hb") == ["hb"]


def _period_net(year, **kw):
    """`_net` as one investment period `year` (a MultiIndex), the dispatch carried over."""
    n = _net(**kw)
    p1, p0, e, lp = (n.links_t.p1.copy(), n.links_t.p0.copy(), n.stores_t.e.copy(), n.loads_t.p.copy())
    idx = pd.MultiIndex.from_arrays([[year] * H, n.snapshots], names=["period", "timestep"])
    n.set_snapshots(idx)
    n.investment_periods = [year]
    for frame, src in ((n.links_t.p1, p1), (n.links_t.p0, p0), (n.stores_t.e, e), (n.loads_t.p, lp)):
        for c in src.columns:
            frame[c] = src[c].to_numpy()
    return n


def test_the_expected_loss_is_the_mean_event_times_the_occurrence_on_the_years_own_hours():
    """Constant dispatch: every start gives the same event, so the mean is that event; occurrence = the year's
    hours × q / MTTR (8784 in a leap year, never a literal 8760)."""
    n = _supplied(_period_net(2028))
    out = expected_outage_loss(n, "imp", 12.0, 0.001)
    assert out["stopped_units"] == ["hb"]
    (row,) = out["by_period"]["2028"]["products"]
    event = outage_loss(n, 2028, 5, 12.0, units=["hb"])["products"][0]["undelivered_t"]
    occ = 8784.0 * 0.001 / 12.0
    assert out["by_period"]["2028"]["occurrence_per_year"] == pytest.approx(occ)
    assert row["undelivered_t_per_event"] == pytest.approx(event)
    assert row["lost_t_per_year"] == pytest.approx(occ * event)
    assert row["lost_eur_per_year"] == pytest.approx(occ * event * 800.0)
    assert "supplied" not in out["source_set"] and "StorageUnits" in out["source_set"]


def test_an_outage_longer_than_the_period_is_one_cycle_and_flagged():
    n = _supplied(_period_net(2027))
    p = expected_outage_loss(n, "imp", 100.0, 0.001)["by_period"]["2027"]
    assert p["outage_hours"] == float(H) and p["flags"] == ["outage_longer_than_period"]


def test_no_unit_cut_off_gives_no_period_rows():
    out = expected_outage_loss(_supplied(_net(), local=True), "imp", 12.0, 0.001)
    assert out["stopped_units"] == [] and out["by_period"] == {}


def test_a_solves_own_lost_production_per_product():
    n = _net()
    n.generators_t.p["short"] = pd.Series(2.0, index=n.snapshots)
    (row,) = lost_production_summary(n)
    assert row["lost_t"] == pytest.approx(2.0 * H / MWH_PER_T)
    assert row["lost_eur"] == pytest.approx(2.0 * H * 800.0 / MWH_PER_T)
    assert lost_production_summary(_net(value=None)) == []     # no shortfall, no bus value: nothing to report


def test_the_sweep_row_is_not_established_without_the_base_dispatch():
    from services.adequacy.sweep import _process_loss_rows

    n = _supplied(_net())
    rows = [{"failure_mode": {}, "meta": {"name": "imp", "mttr_hours": 12.0, "q": 0.001}},
            {"failure_mode": None, "meta": {"name": "imp"}}]
    _process_loss_rows(n, rows, restored=False)
    assert rows[0]["process_loss"] == {"status": "not_established", "reason": "base_not_restored"}
    assert "process_loss" not in rows[1]
    _process_loss_rows(n, rows, restored=True)
    assert rows[0]["process_loss"]["status"] == "ok" and rows[0]["process_loss"]["stopped_units"] == ["hb"]


@pytest.mark.live_solve
def test_the_class_b_sweep_reports_the_lost_production_of_an_import_outage(reset_backend):
    """A site whose only supply is its import Link (FOR 0.001, MTTR 12 h): the sweep's row for that outage carries
    the unit it cuts off and its lost NH3 per year at the lost-production value, beside the electrical figures."""
    import threading

    from services.adequacy.sweep import run_class_b_sweep
    from services.solver_service import SolverConfig

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2035-01-01", periods=H, freq="h"))
    for b in ("grid", "hub"):
        n.add("Bus", b, carrier="AC")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.buses["mwh_per_t"] = [float("nan"), float("nan"), MWH_PER_T]
    n.add("Generator", "supply", bus="grid", p_nom=100.0, marginal_cost=50.0)
    n.add("Link", "imp", bus0="grid", bus1="hub", p_nom=100.0)
    n.links["outage_rate_value"] = float("nan")
    n.links["mttr_hours"] = float("nan")
    n.links.loc["imp", ["outage_rate_value", "mttr_hours"]] = [0.001, 12.0]
    n.add("Link", "hb", bus0="hub", bus1="nh3", p_nom=10.0, efficiency=RATE_MWH / 8.5, min_down_time=8)
    n.links["eh_process"] = n.links.index == "hb"
    n.links["criticality_tier"] = ["", "process_critical"]
    n.add("Load", "delivery", bus="nh3", p_set=0.5 * RATE_MWH)
    n.add("Generator", "short", bus="nh3", p_nom=100.0, marginal_cost=800.0 / MWH_PER_T)
    n.generators["process_role"] = ["", "shortfall"]
    rows, restore = run_class_b_sweep(n, threading.Lock(), SolverConfig(voll=1000.0))
    (row,) = [r for r in rows if r["meta"]["name"] == "imp"]
    pl = row["process_loss"]
    assert pl["status"] == "ok" and pl["stopped_units"] == ["hb"]
    (prod,) = pl["by_period"]["_"]["products"]
    occ = 8760.0 * 0.001 / 12.0
    # No tank: the 12 h outage plus the 8 h restart leaves 20 h of the delivery undelivered, whatever the start.
    assert prod["undelivered_t_per_event"] == pytest.approx(20 * 0.5 * RATE_MWH / MWH_PER_T, rel=1e-6)
    assert prod["lost_eur_per_year"] == pytest.approx(occ * prod["undelivered_t_per_event"] * 800.0, rel=1e-6)


@pytest.mark.live_solve
def test_the_dtc_stress_row_carries_its_own_re_solves_lost_production(reset_backend):
    """Islanding a site whose process unit needs more than the local generator gives: the stress re-solve curtails
    the unit, and the row reports that solve's own lost tonnes and EUR (never a feed-forward on top)."""
    import queue
    import threading

    from models.energy_hub import DtcConfig
    from services.adequacy import dtc as D
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2035-01-01", periods=4, freq="h"))
    for b in ("grid", "hub"):
        n.add("Bus", b, carrier="AC")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.buses["mwh_per_t"] = [float("nan"), float("nan"), MWH_PER_T]
    n.buses["eh_poc"] = n.buses.index == "grid"
    n.buses["eh_critical"] = False
    n.add("Generator", "remote", bus="grid", p_nom=100.0, marginal_cost=10.0)
    n.add("Generator", "local", bus="hub", p_nom=4.0, marginal_cost=20.0)
    n.add("Link", "import_poc", bus0="grid", bus1="hub", p_nom=100.0)
    n.add("Link", "hb", bus0="hub", bus1="nh3", p_nom=10.0, efficiency=0.5)
    n.links["eh_role"] = ["grid_import", ""]
    n.links["eh_process"] = n.links.index == "hb"
    n.links["criticality_tier"] = ["", "process_critical"]
    n.add("Load", "delivery", bus="nh3", p_set=4.0)
    n.add("Generator", "short", bus="nh3", p_nom=100.0, marginal_cost=800.0 / MWH_PER_T)
    n.generators["process_role"] = ["", "", "shortfall"]
    PyPSAService.set_network(n)
    table = D.run_dtc_stress(
        n, SolverConfig(voll=1000.0), lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        dtc=DtcConfig(critical_bus_ids=["hub"], islanding_contingencies=["import_poc"]))
    (row,) = [r for r in table["contingencies"] if r["status"] in ("ok", "optimal")]
    (lost,) = row["lost_production"]
    # 4 MW local → at most 2 MW of NH3 out of the 4 MW delivered: 2 MW short for 4 h.
    assert lost["product"] == "nh3" and lost["source"] == "generator"
    assert lost["lost_t"] == pytest.approx(2.0 * 4 / MWH_PER_T, rel=1e-6)
    assert lost["lost_eur"] == pytest.approx(lost["lost_t"] * 800.0, rel=1e-6)
