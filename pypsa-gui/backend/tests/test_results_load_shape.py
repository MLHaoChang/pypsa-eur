"""
Load shape (site commercial UX A2): heatmaps, a week, monthly and annual energy.

Worked by hand on small networks, then run on the three Energy Hub templates
the owner reads. A group the network does not model, or the run cannot
establish, is never a zero.
"""
from __future__ import annotations

import logging
import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.results.load_shape import load_shape
from tests.golden import fixture as gf

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

TEMPLATES = ["eh_datacenter", "eh_h2_hub", "eh_microgrid"]


def _solve(n):
    logging.disable(logging.CRITICAL)
    try:
        status, cond = n.optimize(solver_name="highs")
    finally:
        logging.disable(logging.NOTSET)
    assert (status, cond) == ("ok", "optimal")
    return n


def _site(snapshots, load, pv_profile, *, step_hours=1.0, weight=None):
    """Grid + PV + battery serving one load: grid import, solar, export, excess."""
    n = pypsa.Network()
    n.set_snapshots(snapshots)
    w = step_hours if weight is None else weight
    n.snapshot_weightings.loc[:, :] = w
    n.snapshot_weightings["stores"] = step_hours
    n.add("Bus", "site")
    n.add("Bus", "grid")
    n.add("Carrier", ["solar", "grid", "battery"])
    n.add("Generator", "pv", bus="site", carrier="solar", p_nom=10, p_max_pu=pv_profile, marginal_cost=0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=100, p_min_pu=-1, marginal_cost=50)
    n.add("Link", "tie", bus0="grid", bus1="site", p_nom=8, p_min_pu=-1, efficiency=1.0)
    n.add("StorageUnit", "bat", bus="site", carrier="battery", p_nom=2, max_hours=2,
          cyclic_state_of_charge=True)
    n.add("Load", "demand", bus="site", p_set=load)
    return n


def _week_net():
    idx = pd.date_range("2030-03-04", periods=24 * 7, freq="h")           # a Monday
    hour = idx.hour.to_numpy()
    pv = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, 1)
    return _solve(_site(idx, 3.0 + 2.0 * (hour >= 18), pv))


def test_heatmap_cells_are_the_mean_mw_at_that_weekday_and_hour():
    n = _week_net()
    p = load_shape(n)
    assert p["status"] == "ok"
    grid = p["heatmaps"]["import"]
    assert grid["status"] == "ok"
    imp = n.generators_t.p["grid_supply"].clip(lower=0)
    idx = imp.index
    for d, h in [(0, 20), (3, 7), (6, 23)]:
        want = float(imp[(idx.dayofweek == d) & (idx.hour == h)].mean())
        assert grid["mean_mw"][d][h] == pytest.approx(want, abs=1e-3)
        assert grid["snapshots"][d][h] == 1
    # an hour no snapshot covers is empty, not 0
    sub = load_shape(n, window=(0, 5))["heatmaps"]["import"]
    assert sub["mean_mw"][0][12] is None and sub["snapshots"][0][12] == 0
    assert sub["mean_mw"][0][2] is not None


def test_export_heatmap_is_what_left_for_the_grid():
    n = _week_net()
    exp = (-n.generators_t.p["grid_supply"].clip(upper=0))
    assert exp.sum() > 0, "the fixture must export"
    got = load_shape(n)["heatmaps"]["export"]["mean_mw"]
    idx = exp.index
    assert got[0][12] == pytest.approx(float(exp[(idx.dayofweek == 0) & (idx.hour == 12)].mean()), abs=1e-3)


def test_week_profile_has_the_groups_the_network_models_and_names_the_rest():
    p = load_shape(_week_net())
    w = p["week"]
    assert w["status"] == "ok" and len(w["index"]) == 168 and w["selected"] == 0
    assert [x["key"] for x in w["weeks"]] == ["2030-W10"]
    assert w["series"]["solar"]["status"] == "ok" and len(w["series"]["solar"]["mw"]) == 168
    assert w["series"]["battery_soc"]["status"] == "ok"
    for k in ("wind", "genset", "ev", "flexible"):
        assert w["series"][k]["status"] == "not_modelled" and "mw" not in w["series"][k]
    # no lost-load capture: unserved cannot be established, so it is not 0
    assert w["series"]["unserved"]["status"] == "unavailable"


def test_unserved_load_and_flagged_intervals_come_from_the_lost_load_capture():
    n = _week_net()
    shed = pd.DataFrame({"demand": np.zeros(len(n.snapshots))}, index=n.snapshots)
    shed.iloc[10, 0] = 1.5
    p = load_shape(n, lost_load={"lost_load_t": shed})
    w = p["week"]
    assert w["series"]["unserved"]["status"] == "ok"
    assert w["series"]["unserved"]["mw"][10] == pytest.approx(1.5)
    assert 10 in w["flagged"] and "unserved load" in w["flag_basis"]


def test_week_picker_selects_another_week_and_clamps():
    idx = pd.date_range("2030-03-04", periods=24 * 14, freq="h")
    n = _solve(_site(idx, 3.0, np.clip(np.sin((idx.hour.to_numpy() - 6) / 12 * np.pi), 0, 1)))
    a, b = load_shape(n, week=0)["week"], load_shape(n, week=1)["week"]
    assert [w["key"] for w in a["weeks"]] == ["2030-W10", "2030-W11"]
    assert a["index"][0].startswith("2030-03-04") and b["index"][0].startswith("2030-03-11")
    assert load_shape(n, week=99)["week"]["selected"] == 1


def test_a_representative_week_has_no_calendar_months_but_has_an_annual_total():
    idx = pd.date_range("2030-03-04", periods=24 * 7, freq="h")
    pv = np.clip(np.sin((idx.hour.to_numpy() - 6) / 12 * np.pi), 0, 1)
    n = _solve(_site(idx, 3.0, pv, weight=8760 / 168))
    p = load_shape(n)
    assert p["monthly"]["status"] == "unavailable" and "representative" in p["monthly"]["reason"]
    row = p["annual"][0]
    assert row["status"] == "ok"
    assert row["solar"] == pytest.approx(float((n.generators_t.p["pv"] * 8760 / 168).sum()), rel=1e-6)
    assert row["load_served"] == pytest.approx(3.0 * 8760, rel=1e-6)


def test_a_calendar_year_gives_monthly_energy_that_sums_to_the_annual_energy():
    idx = pd.date_range("2030-01-01", periods=24 * 365, freq="h")
    hour = idx.hour.to_numpy()
    pv = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, 1) * (0.5 + 0.5 * np.sin(idx.dayofyear.to_numpy() / 365 * 2 * np.pi))
    n = _solve(_site(idx, 3.0, pv))
    p = load_shape(n)
    m = p["monthly"]
    assert m["status"] == "ok" and [r["month"] for r in m["months"]] == [f"2030-{i:02d}" for i in range(1, 13)]
    ann = p["annual"][0]
    for k in ("solar", "grid", "export", "excess"):
        assert sum(r[k] for r in m["months"]) == pytest.approx(ann[k], rel=1e-9, abs=1e-6)
    assert m["months"][0]["daily_generation_mwh"] == pytest.approx(
        sum(r for r in [m["months"][0]["solar"] + m["months"][0]["grid"]]) / 31, rel=1e-9)
    assert m["average_daily_generation_mwh"] == pytest.approx(
        np.mean([r["daily_generation_mwh"] for r in m["months"]]), rel=1e-9)


def test_missing_dispatch_is_not_established_not_zero():
    n = _week_net()
    n.generators_t.p = n.generators_t.p.drop(columns=["pv"])
    p = load_shape(n)
    assert p["status"] == "not_established" and p["reason"]
    assert "heatmaps" not in p


def test_a_commodity_bus_is_refused_in_the_same_words_as_the_energy_flows():
    n = _week_net()
    n.buses["unit"] = "t/h"
    p = load_shape(n)
    assert p["status"] == "not_established" and "not an energy unit" in p["reason"]


@pytest.mark.parametrize("tid", TEMPLATES)
def test_templates_render_with_every_group_either_data_or_named_unavailable(tid):
    n = T.BUILDERS[tid]()
    probe = n.copy()
    from services.adequacy.energization import apply_energization
    apply_energization(probe)
    probe.snapshots.name = "snapshot" if probe.snapshots.nlevels > 1 else probe.snapshots.name
    _solve(probe)
    p = load_shape(probe)
    assert p["status"] == "ok"
    assert p["heatmaps"]["import"]["status"] == "ok"
    assert p["week"]["status"] == "ok" and len(p["week"]["index"]) == 168
    for name, s in p["series_status"].items():
        assert s["status"] in ("ok", "not_modelled", "unavailable")
        if s["status"] != "ok":
            assert s["reason"]
    assert p["annual"][0]["status"] == "ok"
    assert p["annual"][0]["load_served"] == pytest.approx(
        float((probe.loads_t.p.sum(axis=1) * probe.snapshot_weightings["generators"]).sum()), rel=1e-6)
    assert p["monthly"]["status"] == "unavailable"          # a representative week, not a year


def test_golden_multi_period_network_has_one_annual_row_per_period():
    n = gf.solve_golden_network()
    p = load_shape(n, gf.GOLDEN_PERIODS[1])
    assert p["period"] == gf.GOLDEN_PERIODS[1] and p["periods"] == list(gf.GOLDEN_PERIODS)
    assert [a["period"] for a in p["annual"]] == list(gf.GOLDEN_PERIODS)
    assert all(a["status"] == "ok" for a in p["annual"])


def test_route_204_before_a_solve(client):
    assert client.get("/api/results/load_shape").status_code == 204


def test_route_serves_the_period_and_narrows_the_heatmap_window(client, reset_backend):
    gf.install_golden(gf.solve_golden_network())
    r = client.get("/api/results/load_shape", params={"period": gf.GOLDEN_PERIODS[0], "from": 0, "to": 5, "week": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["window"]["from"] == 0 and body["window"]["to"] == 5
    # the golden network has no grid connection: said so, not drawn as 0
    assert body["heatmaps"]["import"]["status"] == "not_modelled"
    assert body["week"]["status"] == "ok" and body["week"]["selected"] == 0
