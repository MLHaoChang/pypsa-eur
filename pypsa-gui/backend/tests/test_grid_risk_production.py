"""
Grid-to-process ticket 13: "Grid risk to production" (`services.results.grid_risk_production`).

One small solved chain in tonnes: grid → PoC import Link → site bus → site transformer Link → mill bus → mill (a
process unit, 1 t of concentrate per MWh) → concentrate bus (t) with a delivery Load and a shortfall Generator. The
grid is cut in hour 2, so the solve itself loses tonnes (the connection line). The campus run's two CSVs are written
by hand in the shape `restart_campus` writes them (12b).
"""
import math

import pandas as pd
import pypsa
import pytest

from gridspine.static.restart_sequence import NO_BLOCK
from services.results import grid_risk_production as G
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures

LPV = 100.0                       # EUR/t
Q_IMPORT, MTTR_IMPORT = 0.002, 12.0
Q_TRAFO, MTTR_TRAFO = 0.005, 72.0
RATE = 5.0                        # t/h, the mill's rated output
DEMAND = 4.0                      # t/h


def _outage(n, name, q, mttr):
    for col in ("outage_rate_value", "mttr_hours"):
        if col not in n.links.columns:
            n.links[col] = float("nan")
    n.links.at[name, "outage_rate_value"] = q
    n.links.at[name, "mttr_hours"] = mttr


def _network(*, lpv=LPV, periods=None, trafo=True, process=True):
    n = pypsa.Network()
    if periods:
        sns = pd.date_range("2030-01-01", periods=6, freq="h")
        n.set_snapshots(pd.MultiIndex.from_product([periods, sns]))
        n.set_investment_periods(periods)
    else:
        n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    n.add("Carrier", ["AC", "concentrate"])
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "site", carrier="AC")
    n.add("Bus", "mill_bus", carrier="AC")
    n.add("Bus", "concentrate", carrier="concentrate", unit="t")
    n.add("Generator", "grid_supply", bus="grid", carrier="AC", p_nom=100.0, marginal_cost=10.0,
          p_max_pu=[1, 1, 0, 1, 1, 1] * (len(periods) if periods else 1))
    n.add("Link", "grid_import", bus0="grid", bus1="site", carrier="AC", p_nom=50.0)
    n.links["eh_role"] = ""
    n.links.at["grid_import", "eh_role"] = "grid_import"
    _outage(n, "grid_import", Q_IMPORT, MTTR_IMPORT)
    feed = "site"
    if trafo:
        n.add("Link", "site_trafo", bus0="site", bus1="mill_bus", carrier="AC", p_nom=50.0)
        _outage(n, "site_trafo", Q_TRAFO, MTTR_TRAFO)
        feed = "mill_bus"
    n.add("Link", "mill", bus0=feed, bus1="concentrate", carrier="milling", p_nom=RATE, efficiency=1.0)
    n.links["eh_process"] = False
    n.links.at["mill", "eh_process"] = process
    n.add("Load", "offtake", bus="concentrate", carrier="concentrate", p_set=DEMAND)
    n.add("Generator", "__lost_concentrate", bus="concentrate", carrier="concentrate", p_nom=1000.0,
          marginal_cost=lpv if lpv is not None else 0.0)
    n.generators["process_role"] = ""
    n.generators.at["__lost_concentrate", "process_role"] = "shortfall"
    n.optimize(solver_name="highs")
    if lpv is None:                       # no lost-production value: the accessor reads None, never 0
        n.generators.at["__lost_concentrate", "marginal_cost"] = float("nan")
    return n


def _write_campus(run, *, outside_rate=RATE, in_block_lost_t=3.0, ride_t=7.5, incomplete=False):
    run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"level": "unit", "unit": "mill", "lost_t_per_year": ride_t, "verdict": "known", "note": ""},
        {"level": "total", "lost_t_per_year": ride_t, "incomplete": incomplete, "unknown_count": int(incomplete),
         "known_count": 1, "dip_stats_source": "demo", "note": "demo"},
    ]).to_csv(run / G.DIP_TRIPS_CSV, index=False)
    pd.DataFrame([
        {"level": "unit", "transition": "island", "unit": "mill", "block": None, "rate_tph": outside_rate,
         "mass_pull": 1.0, "lost_t": None, "verdict": "fail",
         "note": f"island on gensets; {NO_BLOCK}: 9 MW is above every pickup block"},
        {"level": "unit", "transition": "island", "unit": "pump", "block": 1, "rate_tph": 2.0, "mass_pull": 1.0,
         "lost_t": in_block_lost_t, "verdict": "pass", "note": "motor start pass"},
        # no rated MW: no block either, but not "outside every block": unknown, never counted as lost for the outage
        {"level": "unit", "transition": "island", "unit": "crusher", "block": None, "rate_tph": 1.0,
         "mass_pull": 1.0, "lost_t": None, "verdict": "unknown", "note": "no rated MW to place in a pickup block"},
        # a trip-transition row with no block is not an island unit: never counted in the island line
        {"level": "unit", "transition": "trip", "unit": "mill", "block": None, "rate_tph": RATE, "mass_pull": 1.0,
         "lost_t": None, "verdict": "fail", "note": f"{NO_BLOCK}: 9 MW"},
    ]).to_csv(run / G.RESTART_CSV, index=False)
    return G.CampusRun(run_dir=run, stamp={"file": G.RESTART_CSV})


@pytest.fixture(scope="module")
def net():
    return _network()


def _line(out, key):
    return next(ln for ln in out["lines"] if ln["key"] == key)


def _year_hours():
    return 8760.0


def test_a_network_with_no_process_unit_omits_the_section():
    assert G.grid_risk_production(_network(process=False)) is None


def test_every_line_names_its_source_file(net, tmp_path):
    out = G.grid_risk_production(net, campus=_write_campus(tmp_path / "run"))
    files = {ln["key"]: ln["source_files"] for ln in out["lines"]}
    assert files["ride_through"] == [G.DIP_TRIPS_CSV]
    assert G.RESTART_CSV in files["island"] and G.NETWORK_FILE in files["island"]
    assert files["n_1"] == [G.NETWORK_FILE]
    assert G.NETWORK_FILE in files["connection"] and any("__lost_concentrate" in f for f in files["connection"])


def test_ride_through_is_the_csv_total_as_written(net, tmp_path):
    out = G.grid_risk_production(net, campus=_write_campus(tmp_path / "run", ride_t=7.5))
    rt = _line(out, "ride_through")
    assert rt["status"] == "ok"
    assert rt["by_period"]["_"]["t_per_year"] == 7.5
    assert rt["by_period"]["_"]["eur_per_year"] == pytest.approx(7.5 * LPV)


def test_an_incomplete_ride_through_total_is_partial_and_a_lower_bound(net, tmp_path):
    rt = _line(G.grid_risk_production(net, campus=_write_campus(tmp_path / "run", incomplete=True)),
               "ride_through")
    assert rt["status"] == "partial" and G.LOWER_BOUND in rt["flags"]


def test_island_counts_only_the_island_units_outside_every_block(net, tmp_path):
    out = G.grid_risk_production(net, campus=_write_campus(tmp_path / "run", in_block_lost_t=3.0))
    isl = _line(out, "island")
    events = _year_hours() * Q_IMPORT / MTTR_IMPORT
    outside, inside = isl["rows"]
    assert outside["units"] == ["mill"] and inside["units"] == ["pump"]
    assert outside["by_period"]["_"]["t_per_year"] == pytest.approx(RATE * 1.0 * MTTR_IMPORT * events)
    assert inside["by_period"]["_"]["t_per_year"] == pytest.approx(3.0 * events)
    assert isl["by_period"]["_"]["t_per_year"] == pytest.approx((RATE * MTTR_IMPORT + 3.0) * events)
    assert G.CONSERVATIVE in isl["flags"]
    assert isl["status"] == "partial" and G.LOWER_BOUND in isl["flags"]      # the crusher is unknown


def test_island_without_a_campus_run_is_missing_not_zero(net):
    out = G.grid_risk_production(net)
    for key in ("ride_through", "island"):
        ln = _line(out, key)
        assert ln["status"] == "missing" and ln["missing"]
        assert ln["by_period"] == {}
    assert out["total"]["incomplete"] is True


def test_n_1_is_expected_outage_loss_of_the_site_transformer(net):
    from services.adequacy.process_loss import expected_outage_loss

    n1 = _line(G.grid_risk_production(net), "n_1")
    ref = expected_outage_loss(net, "site_trafo", MTTR_TRAFO, Q_TRAFO)
    want = sum(p["lost_t_per_year"] for p in ref["by_period"]["_"]["products"])
    assert n1["status"] in ("ok", "partial")
    assert [r["key"] for r in n1["rows"]] == ["site_trafo"]          # never the PoC import Link
    assert n1["by_period"]["_"]["t_per_year"] == pytest.approx(want)
    assert want > 0


def test_n_1_without_a_site_transformer_is_missing():
    n1 = _line(G.grid_risk_production(_network(trafo=False)), "n_1")
    assert n1["status"] == "missing" and "site transformer" in n1["missing"][0]


def test_connection_line_is_the_solves_own_lost_tonnes_annualised(net):
    con = _line(G.grid_risk_production(net), "connection")
    lost = float(net.generators_t.p["__lost_concentrate"].clip(lower=0).sum())
    assert lost > 0
    want = lost * _year_hours() / 6.0
    assert con["by_period"]["_"]["t_per_year"] == pytest.approx(want)
    assert con["by_period"]["_"]["eur_per_year"] == pytest.approx(want * LPV)


def test_two_periods_give_per_period_lines_never_a_horizon_sum():
    n = _network(periods=[2030, 2040])
    out = G.grid_risk_production(n)
    assert out["periods"] == ["2030", "2040"]
    con = _line(out, "connection")
    lost_2030 = float(n.generators_t.p["__lost_concentrate"].loc[2030].clip(lower=0).sum())
    assert con["by_period"]["2030"]["t_per_year"] == pytest.approx(lost_2030 * 8760.0 / 6.0)
    assert con["by_period"]["2040"]["t_per_year"] == pytest.approx(
        float(n.generators_t.p["__lost_concentrate"].loc[2040].clip(lower=0).sum()) * 8784.0 / 6.0)
    assert set(out["total"]["by_period"]) == {"2030", "2040"}


def test_the_total_is_the_sum_of_the_lines_labelled_an_upper_bound(net, tmp_path):
    out = G.grid_risk_production(net, campus=_write_campus(tmp_path / "run"))
    tot = out["total"]
    assert tot["label"] == "upper bound (lines may overlap)" and tot["overlap_rule"] == "upper_bound"
    assert tot["incomplete"] is False
    s = sum(ln["by_period"]["_"]["t_per_year"] for ln in out["lines"])
    assert tot["by_period"]["_"]["t_per_year"] == pytest.approx(s)


def test_an_lp_shortfall_hour_that_is_also_an_island_hour_counts_in_both_lines(net, tmp_path):
    """The grid is cut in hour 2: the LP loses that hour's tonnes (connection), and a grid outage is also the island
    event, whose outside-block unit is the same mill. Under the upper-bound rule both lines carry it."""
    out = G.grid_risk_production(net, campus=_write_campus(tmp_path / "run"))
    con, isl = _line(out, "connection"), _line(out, "island")
    hour2 = float(net.generators_t.p["__lost_concentrate"].iloc[2])
    assert hour2 > 0
    assert con["by_period"]["_"]["t_per_year"] >= hour2 * _year_hours() / 6.0 - 1e-9
    assert "mill" in isl["rows"][0]["units"] and isl["by_period"]["_"]["t_per_year"] > 0
    tot = out["total"]["by_period"]["_"]["t_per_year"]
    assert tot == pytest.approx(con["by_period"]["_"]["t_per_year"] + isl["by_period"]["_"]["t_per_year"]
                                + _line(out, "ride_through")["by_period"]["_"]["t_per_year"]
                                + _line(out, "n_1")["by_period"]["_"]["t_per_year"])
    assert out["total"]["label"] == G.OVERLAP_LABEL


def test_no_lost_production_value_gives_tonnes_and_a_null_eur(tmp_path):
    n = _network(lpv=None)
    out = G.grid_risk_production(n, campus=_write_campus(tmp_path / "run"))
    for ln in out["lines"]:
        c = ln["by_period"]["_"]
        assert c["t_per_year"] is not None and math.isfinite(c["t_per_year"]), ln["key"]
        assert c["eur_per_year"] is None and c["eur_flag"] == G.LPV_MISSING, ln["key"]
    assert out["total"]["by_period"]["_"]["eur_per_year"] is None


def test_a_stale_campus_run_flags_its_lines(net, tmp_path):
    run = _write_campus(tmp_path / "run")
    stale = G.CampusRun(run_dir=run.run_dir, stale=True, out_of_date=("mill",))
    out = G.grid_risk_production(net, campus=stale)
    assert _line(out, "ride_through")["stale"] and _line(out, "island")["stale"]
    assert G.STALE in _line(out, "island")["flags"]
    assert not _line(out, "connection")["stale"]


# ── the real files: a campus run (12b-3) and the two example templates ─────

def test_the_file_names_are_the_campus_studys():
    from gridspine.drivers import campus_study as cs

    assert (G.DIP_TRIPS_CSV, G.RESTART_CSV) == (cs.DIP_TRIPS_CSV, cs.RESTART_CSV)


def test_a_real_campus_run_feeds_the_campus_lines(hub):  # noqa: F811
    from services import campus_electrical_service as ce, project_registry
    from tests.test_campus_dips_restart import _run, _with_units

    nc = _with_units(hub)
    _run(hub)
    campus = G.campus_run_of(hub)
    assert campus.run_dir is not None and not campus.stale and campus.stamp.get("run_at")
    n = pypsa.Network(str(nc))
    out = G.grid_risk_production(n, campus=campus)
    rt = _line(out, "ride_through")
    total = next(r for r in ce.get_state(hub)["results"]["dips_restart"]["dip_trips"] if r["level"] == "total")
    assert rt["by_period"]["_"]["t_per_year"] == pytest.approx(float(total["lost_t_per_year"]))
    assert rt["status"] == "partial"                 # the pump has no curve: the total is a lower bound
    assert rt["source_files"] == [G.DIP_TRIPS_CSV] and rt["stamp"] == campus.stamp
    assert project_registry.project_dir(hub) / ce.SUBDIR / "run" == campus.run_dir


def test_no_campus_run_reads_as_an_empty_campus_run(hub):  # noqa: F811
    assert G.campus_run_of(hub) == G.CampusRun()


@pytest.mark.parametrize("tid", ["eh_ammonia_staged", "eh_concentrator_staged"])
def test_on_a_and_b_every_line_names_its_source_or_its_missing_input(tid):
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
    import eh_templates as T

    out = G.grid_risk_production(T.BUILDERS[tid]())
    assert [ln["key"] for ln in out["lines"]] == list(G.LINE_KEYS)
    for ln in out["lines"]:
        assert ln["source_files"], ln["key"]
        assert ln["status"] != "missing" or ln["missing"], ln["key"]
    # Neither example has a site transformer Link: N-1 names the missing input (GK A, Q2).
    assert "site transformer" in _line(out, "n_1")["missing"][0]
