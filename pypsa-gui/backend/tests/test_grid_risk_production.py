"""
Grid-to-process ticket 13: "Grid risk to production" (`services.results.grid_risk_production`).

One small solved chain in tonnes: grid → PoC import Link → site bus → site transformer Link → mill bus → mill (a
process unit, 1 t of concentrate per MWh) → concentrate bus (t) with a delivery Load and a shortfall Generator. The
grid is cut in hour 2, so the solve itself loses tonnes (the connection line). The campus run's two CSVs are written
by hand in the shape `restart_campus` writes them (12b).
"""

import math
from pathlib import Path

import pandas as pd
import pypsa
import pytest

from gridspine.static.restart_sequence import NO_BLOCK
from services.results import grid_risk_production as G
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures

LPV = 100.0  # EUR/t
Q_IMPORT, MTTR_IMPORT = 0.002, 12.0
Q_TRAFO, MTTR_TRAFO = 0.005, 72.0
RATE = 5.0  # t/h, the mill's rated output
DEMAND = 4.0  # t/h


def _outage(n, name, q, mttr):
    for col in ("outage_rate_value", "mttr_hours"):
        if col not in n.links.columns:
            n.links[col] = float("nan")
    n.links.at[name, "outage_rate_value"] = q
    n.links.at[name, "mttr_hours"] = mttr


def _network(*, lpv=LPV, periods=None, trafo=True, process=True, weights=None):
    n = pypsa.Network()
    if periods:
        sns = pd.date_range("2030-01-01", periods=6, freq="h")
        n.set_snapshots(pd.MultiIndex.from_product([periods, sns]))
        n.set_investment_periods(periods)
    else:
        n.set_snapshots(pd.date_range("2030-01-01", periods=6, freq="h"))
    if weights is not None:  # hours each snapshot stands for (a 3-hourly network: 3)
        n.snapshot_weightings.loc[:, :] = pd.Series(weights, index=n.snapshots).values[
            :, None
        ]
    n.add("Carrier", ["AC", "concentrate"])
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "site", carrier="AC")
    n.add("Bus", "mill_bus", carrier="AC")
    n.add("Bus", "concentrate", carrier="concentrate", unit="t")
    n.add(
        "Generator",
        "grid_supply",
        bus="grid",
        carrier="AC",
        p_nom=100.0,
        marginal_cost=10.0,
        p_max_pu=[1, 1, 0, 1, 1, 1] * (len(periods) if periods else 1),
    )
    n.add("Link", "grid_import", bus0="grid", bus1="site", carrier="AC", p_nom=50.0)
    n.links["eh_role"] = ""
    n.links.at["grid_import", "eh_role"] = "grid_import"
    _outage(n, "grid_import", Q_IMPORT, MTTR_IMPORT)
    feed = "site"
    if trafo:
        n.add(
            "Link", "site_trafo", bus0="site", bus1="mill_bus", carrier="AC", p_nom=50.0
        )
        _outage(n, "site_trafo", Q_TRAFO, MTTR_TRAFO)
        feed = "mill_bus"
    n.add(
        "Link",
        "mill",
        bus0=feed,
        bus1="concentrate",
        carrier="milling",
        p_nom=RATE,
        efficiency=1.0,
    )
    n.links["eh_process"] = False
    n.links.at["mill", "eh_process"] = process
    n.add("Load", "offtake", bus="concentrate", carrier="concentrate", p_set=DEMAND)
    n.add(
        "Generator",
        "__lost_concentrate",
        bus="concentrate",
        carrier="concentrate",
        p_nom=1000.0,
        marginal_cost=lpv if lpv is not None else 0.0,
    )
    n.generators["process_role"] = ""
    n.generators.at["__lost_concentrate", "process_role"] = "shortfall"
    n.optimize(solver_name="highs")
    if lpv is None:  # no lost-production value: the accessor reads None, never 0
        n.generators.at["__lost_concentrate", "marginal_cost"] = float("nan")
    return n


def _write_campus(
    run, *, outside_rate=RATE, in_block_lost_t=3.0, ride_t=7.5, incomplete=False
):
    run.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "level": "unit",
                "unit": "mill",
                "lost_t_per_year": ride_t,
                "verdict": "known",
                "note": "",
            },
            {
                "level": "total",
                "lost_t_per_year": ride_t,
                "incomplete": incomplete,
                "unknown_count": int(incomplete),
                "known_count": 1,
                "dip_stats_source": "demo",
                "note": "demo",
            },
        ]
    ).to_csv(run / G.DIP_TRIPS_CSV, index=False)
    pd.DataFrame(
        [
            {
                "level": "unit",
                "transition": "island",
                "unit": "mill",
                "block": None,
                "rate_tph": outside_rate,
                "mass_pull": 1.0,
                "lost_t": None,
                "verdict": "fail",
                "note": f"island on gensets; {NO_BLOCK}: 9 MW is above every pickup block",
            },
            {
                "level": "unit",
                "transition": "island",
                "unit": "pump",
                "block": 1,
                "rate_tph": 2.0,
                "mass_pull": 1.0,
                "lost_t": in_block_lost_t,
                "verdict": "pass",
                "note": "motor start pass",
            },
            # no rated MW: no block either, but not "outside every block": unknown, never counted as lost for the outage
            {
                "level": "unit",
                "transition": "island",
                "unit": "crusher",
                "block": None,
                "rate_tph": 1.0,
                "mass_pull": 1.0,
                "lost_t": None,
                "verdict": "unknown",
                "note": "no rated MW to place in a pickup block",
            },
            # a trip-transition row with no block is not an island unit: never counted in the island line
            {
                "level": "unit",
                "transition": "trip",
                "unit": "mill",
                "block": None,
                "rate_tph": RATE,
                "mass_pull": 1.0,
                "lost_t": None,
                "verdict": "fail",
                "note": f"{NO_BLOCK}: 9 MW",
            },
        ]
    ).to_csv(run / G.RESTART_CSV, index=False)
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
    assert G.NETWORK_FILE in files["connection"] and any(
        "__lost_concentrate" in f for f in files["connection"]
    )


def test_ride_through_is_the_csv_total_as_written(net, tmp_path):
    out = G.grid_risk_production(
        net, campus=_write_campus(tmp_path / "run", ride_t=7.5)
    )
    rt = _line(out, "ride_through")
    assert rt["status"] == "ok"
    assert rt["by_period"]["_"]["t_per_year"] == 7.5
    assert rt["by_period"]["_"]["eur_per_year"] == pytest.approx(7.5 * LPV)


def test_an_incomplete_ride_through_total_is_partial_and_a_lower_bound(net, tmp_path):
    rt = _line(
        G.grid_risk_production(
            net, campus=_write_campus(tmp_path / "run", incomplete=True)
        ),
        "ride_through",
    )
    assert rt["status"] == "partial" and G.LOWER_BOUND in rt["flags"]


def test_island_counts_only_the_island_units_outside_every_block(net, tmp_path):
    out = G.grid_risk_production(
        net, campus=_write_campus(tmp_path / "run", in_block_lost_t=3.0)
    )
    isl = _line(out, "island")
    events = _year_hours() * Q_IMPORT / MTTR_IMPORT
    outside, inside = isl["rows"]
    assert outside["units"] == ["mill"] and inside["units"] == ["pump"]
    assert outside["by_period"]["_"]["t_per_year"] == pytest.approx(
        RATE * 1.0 * MTTR_IMPORT * events
    )
    assert inside["by_period"]["_"]["t_per_year"] == pytest.approx(3.0 * events)
    assert isl["by_period"]["_"]["t_per_year"] == pytest.approx(
        (RATE * MTTR_IMPORT + 3.0) * events
    )
    assert G.CONSERVATIVE in isl["flags"]
    assert (
        isl["status"] == "partial" and G.LOWER_BOUND in isl["flags"]
    )  # the crusher is unknown


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
    assert [r["key"] for r in n1["rows"]] == ["site_trafo"]  # never the PoC import Link
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


def test_the_connection_line_weights_each_snapshot_by_the_hours_it_stands_for():
    """GK A: a 3-hourly network (non-unit generator weightings, the cut hour weighted 3): each snapshot's lost
    tonnes count for its hours, and the year is the weighted span scaled to 8760 h."""
    weights = [3.0, 3.0, 3.0, 1.0, 1.0, 3.0]
    n = _network(weights=weights)
    con = _line(G.grid_risk_production(n), "connection")
    x = n.generators_t.p["__lost_concentrate"].clip(lower=0)
    w = n.snapshot_weightings.generators
    assert list(w) == weights and float(x.iloc[2]) > 0
    want = float((x * w).sum()) * _year_hours() / sum(weights)
    assert want != pytest.approx(
        float(x.sum()) * _year_hours() / sum(weights)
    )  # the weighting matters here
    assert con["by_period"]["_"]["t_per_year"] == pytest.approx(want)
    assert con["by_period"]["_"]["eur_per_year"] == pytest.approx(want * LPV)


def test_two_periods_give_per_period_lines_never_a_horizon_sum():
    n = _network(periods=[2030, 2040])
    out = G.grid_risk_production(n)
    assert out["periods"] == ["2030", "2040"]
    con = _line(out, "connection")
    lost_2030 = float(
        n.generators_t.p["__lost_concentrate"].loc[2030].clip(lower=0).sum()
    )
    assert con["by_period"]["2030"]["t_per_year"] == pytest.approx(
        lost_2030 * 8760.0 / 6.0
    )
    assert con["by_period"]["2040"]["t_per_year"] == pytest.approx(
        float(n.generators_t.p["__lost_concentrate"].loc[2040].clip(lower=0).sum())
        * 8784.0
        / 6.0
    )
    assert set(out["total"]["by_period"]) == {"2030", "2040"}


def test_the_total_is_the_sum_of_the_lines_labelled_an_upper_bound(net, tmp_path):
    out = G.grid_risk_production(net, campus=_write_campus(tmp_path / "run"))
    tot = out["total"]
    assert (
        tot["label"] == "upper bound (lines may overlap)"
        and tot["overlap_rule"] == "upper_bound"
    )
    assert tot["incomplete"] is False
    s = sum(ln["by_period"]["_"]["t_per_year"] for ln in out["lines"])
    assert tot["by_period"]["_"]["t_per_year"] == pytest.approx(s)


def test_an_lp_shortfall_hour_that_is_also_an_island_hour_counts_in_both_lines(
    net, tmp_path
):
    """The grid is cut in hour 2: the LP loses that hour's tonnes (connection), and a grid outage is also the island
    event, whose outside-block unit is the same mill. Under the upper-bound rule both lines carry it."""
    out = G.grid_risk_production(net, campus=_write_campus(tmp_path / "run"))
    con, isl = _line(out, "connection"), _line(out, "island")
    hour2 = float(net.generators_t.p["__lost_concentrate"].iloc[2])
    assert hour2 > 0
    assert con["by_period"]["_"]["t_per_year"] >= hour2 * _year_hours() / 6.0 - 1e-9
    assert "mill" in isl["rows"][0]["units"] and isl["by_period"]["_"]["t_per_year"] > 0
    tot = out["total"]["by_period"]["_"]["t_per_year"]
    assert tot == pytest.approx(
        con["by_period"]["_"]["t_per_year"]
        + isl["by_period"]["_"]["t_per_year"]
        + _line(out, "ride_through")["by_period"]["_"]["t_per_year"]
        + _line(out, "n_1")["by_period"]["_"]["t_per_year"]
    )
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
    assert (
        campus.run_dir is not None and not campus.stale and campus.stamp.get("run_at")
    )
    n = pypsa.Network(str(nc))
    out = G.grid_risk_production(n, campus=campus)
    rt = _line(out, "ride_through")
    total = next(
        r
        for r in ce.get_state(hub)["results"]["dips_restart"]["dip_trips"]
        if r["level"] == "total"
    )
    assert rt["by_period"]["_"]["t_per_year"] == pytest.approx(
        float(total["lost_t_per_year"])
    )
    assert (
        rt["status"] == "partial"
    )  # the pump has no curve: the total is a lower bound
    assert rt["source_files"] == [G.DIP_TRIPS_CSV] and rt["stamp"] == campus.stamp
    assert project_registry.project_dir(hub) / ce.SUBDIR / "run" == campus.run_dir


def test_no_campus_run_reads_as_an_empty_campus_run(hub):  # noqa: F811
    assert G.campus_run_of(hub) == G.CampusRun()


@pytest.mark.parametrize("tid", ["eh_ammonia_staged", "eh_concentrator_staged"])
def test_on_a_and_b_every_line_names_its_source_or_its_missing_input(tid):
    import pathlib
    import sys

    sys.path.insert(
        0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates")
    )
    import eh_templates as T

    out = G.grid_risk_production(T.BUILDERS[tid]())
    assert [ln["key"] for ln in out["lines"]] == list(G.LINE_KEYS)
    for ln in out["lines"]:
        assert ln["source_files"], ln["key"]
        assert ln["status"] != "missing" or ln["missing"], ln["key"]
    # Neither example has a site transformer Link: N-1 names the missing input (GK A, Q2).
    assert "site transformer" in _line(out, "n_1")["missing"][0]


# ── the frontend's words (UX 10, condition 5): parity with the asset form ─────

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _ts_map(path, name):
    import re

    text = path.read_text()
    body = text[text.index(f"{name}: Record<string, string> = {{") :]
    body = body[: body.index("\n}")]
    return dict(re.findall(r"^\s+(\w+): ('[^']*'|\w+),", body, flags=re.M))


def test_every_missing_word_has_the_forms_word_in_the_frontend(net, tmp_path):
    from services.asset_schema.fields import fields_for

    labels = _ts_map(_FRONTEND / "utils" / "fieldLabels.ts", "FIELD_LABELS")
    places = _ts_map(_FRONTEND / "pages" / "results" / "GridRiskTab.tsx", "PLACE_WORDS")
    words = {
        G.CAMPUS_RUN[1],
        "site_transformer",
        "grid_import",
        "run",
        "ride_through_curve",
        "outage_rate_value",
        "mttr_hours",
        "lost_production_value",
        "mwh_per_t",
        "marginal_cost",
    }
    # Every word the module can emit (a no-campus run, no site transformer, no value) is among them.
    for n, campus in ((net, None), (_network(trafo=False, lpv=None), None)):
        out = G.grid_risk_production(n, campus=campus)
        emitted = {m["word"] for ln in out["lines"] for m in ln["missing_inputs"]} | {
            i["word"] for i in out["eur_inputs"]
        }
        assert emitted <= words, emitted - words
    form = {}
    for comp in ("Link", "Bus", "Generator"):
        form.update({f.key: f.label for f in fields_for(comp)})
    for w in sorted(words):
        if w in places:
            continue
        assert w in labels, f"{w}: no word in FIELD_LABELS"
        if w in (
            "outage_rate_value",
            "mttr_hours",
            "lost_production_value",
            "ride_through_curve",
            "mwh_per_t",
        ):
            assert labels[w].strip("'") == form[w], (w, labels[w], form[w])


# --- G2P #23: lines 1-2 priced with several shortfall products (GK A plan GO, G23-1..5) ---------------------------

LPV_ACID = 40.0  # EUR/t


def _two_products(acid_lpv=LPV_ACID):
    """The chain plus a second shortfall product, acid (t), so the campus lines cannot take one product's value."""
    n = _network()
    n.add("Carrier", "acid")
    n.add("Bus", "acid", carrier="acid", unit="t")
    n.add(
        "Generator",
        "__lost_acid",
        bus="acid",
        carrier="acid",
        p_nom=1000.0,
        marginal_cost=acid_lpv if acid_lpv is not None else float("nan"),
    )
    n.generators.at["__lost_acid", "process_role"] = "shortfall"
    return n


@pytest.fixture(scope="module")
def net2():
    return _two_products()


def _write_products(run, dip=None, island=None, total=None, product_column=True):
    """Campus CSVs in restart_campus's shape. ``dip``: (unit, lost t/yr, product); ``island``: (unit, outside
    every block?, rate or lost t, product)."""
    run.mkdir(parents=True, exist_ok=True)
    dip = dip or [("mill", 7.5, "concentrate"), ("pump", 2.5, "acid")]
    island = island or [
        ("mill", True, RATE, "concentrate"),
        ("pump", False, 3.0, "acid"),
    ]
    rows = [
        {
            "level": "unit",
            "unit": u,
            "lost_t_per_year": t,
            "verdict": "known",
            "note": "",
            "product": p,
        }
        for u, t, p in dip
    ]
    known = sum(t or 0.0 for _, t, _ in dip)
    rows.append(
        {
            "level": "total",
            "lost_t_per_year": known if total is None else total,
            "incomplete": False,
            "unknown_count": 0,
            "known_count": len(dip),
            "dip_stats_source": "demo",
            "note": "demo",
        }
    )
    restart = [
        {
            "level": "unit",
            "transition": "island",
            "unit": u,
            "block": None if out else 1,
            "rate_tph": v if out else 2.0,
            "mass_pull": 1.0,
            "lost_t": None if out else v,
            "verdict": "fail" if out else "pass",
            "note": f"island on gensets; {NO_BLOCK}: above every pickup block"
            if out
            else "motor start pass",
            "product": p,
        }
        for u, out, v, p in island
    ]
    frames = [pd.DataFrame(rows), pd.DataFrame(restart)]
    if not product_column:
        frames = [f.drop(columns="product") for f in frames]
    frames[0].to_csv(run / G.DIP_TRIPS_CSV, index=False)
    frames[1].to_csv(run / G.RESTART_CSV, index=False)
    return G.CampusRun(run_dir=run, stamp={"file": G.RESTART_CSV})


def _campus_cells(n, campus):
    out = G.grid_risk_production(n, campus=campus)
    rt, isl = _line(out, "ride_through"), _line(out, "island")
    outside, inside = isl["rows"]
    return {
        "ride": rt["by_period"]["_"],
        "island": isl["by_period"]["_"],
        "outside": outside["by_period"]["_"],
        "inside": inside["by_period"]["_"],
    }


def test_several_products_price_each_unit_row_at_its_own_product(net2, tmp_path):
    c = _campus_cells(net2, _write_products(tmp_path / "run"))
    events = _year_hours() * Q_IMPORT / MTTR_IMPORT
    assert c["ride"]["eur_per_year"] == pytest.approx(7.5 * LPV + 2.5 * LPV_ACID)
    assert c["outside"]["eur_per_year"] == pytest.approx(
        RATE * MTTR_IMPORT * events * LPV
    )
    assert c["inside"]["eur_per_year"] == pytest.approx(3.0 * events * LPV_ACID)
    assert c["island"]["eur_per_year"] == pytest.approx(
        (RATE * MTTR_IMPORT * LPV + 3.0 * LPV_ACID) * events
    )
    assert all(v["eur_flag"] is None for v in c.values())
    assert c["ride"]["t_per_year"] == 10.0  # tonnes unchanged


def test_a_campus_run_from_before_the_product_column_stays_unavailable(net2, tmp_path):
    c = _campus_cells(net2, _write_products(tmp_path / "run", product_column=False))
    for k, v in c.items():
        assert v["t_per_year"] is not None, k
        assert v["eur_per_year"] is None and v["eur_flag"] == G.SEVERAL_PRODUCTS, k


def test_an_unpriced_product_never_prices_at_zero(tmp_path):
    c = _campus_cells(_two_products(acid_lpv=None), _write_products(tmp_path / "run"))
    assert c["ride"]["eur_per_year"] is None and c["ride"]["eur_flag"] == G.LPV_MISSING
    assert (
        c["inside"]["eur_per_year"] is None and c["inside"]["eur_flag"] == G.LPV_MISSING
    )
    assert (
        c["island"]["eur_per_year"] is None and c["island"]["eur_flag"] == G.LPV_MISSING
    )
    assert c["outside"]["eur_per_year"] == pytest.approx(  # all concentrate: priced
        RATE * MTTR_IMPORT * _year_hours() * Q_IMPORT / MTTR_IMPORT * LPV
    )


def test_ride_through_unit_rows_that_do_not_sum_to_the_total_are_not_priced(
    net2, tmp_path
):
    c = _campus_cells(net2, _write_products(tmp_path / "run", total=11.0))
    assert c["ride"]["t_per_year"] == 11.0
    assert (
        c["ride"]["eur_per_year"] is None
        and c["ride"]["eur_flag"] == G.SEVERAL_PRODUCTS
    )


def test_ride_through_a_row_with_tonnes_and_no_product_gives_none(net2, tmp_path):
    dip = [("mill", 7.5, "concentrate"), ("pump", 2.5, None)]
    c = _campus_cells(net2, _write_products(tmp_path / "run", dip=dip))
    assert (
        c["ride"]["eur_per_year"] is None
        and c["ride"]["eur_flag"] == G.SEVERAL_PRODUCTS
    )
    assert (
        c["outside"]["eur_per_year"] is not None
        and c["inside"]["eur_per_year"] is not None
    )


def test_ride_through_a_row_with_zero_tonnes_and_no_product_does_not_poison_the_sum(
    net2, tmp_path
):
    dip = [("mill", 7.5, "concentrate"), ("pump", 2.5, "acid"), ("fan", 0.0, None)]
    c = _campus_cells(net2, _write_products(tmp_path / "run", dip=dip))
    assert c["ride"]["eur_per_year"] == pytest.approx(7.5 * LPV + 2.5 * LPV_ACID)


def test_island_outside_blocks_a_row_with_no_product_gives_none(net2, tmp_path):
    island = [("mill", True, RATE, None), ("pump", False, 3.0, "acid")]
    c = _campus_cells(net2, _write_products(tmp_path / "run", island=island))
    assert (
        c["outside"]["eur_per_year"] is None
        and c["outside"]["eur_flag"] == G.SEVERAL_PRODUCTS
    )
    assert (
        c["inside"]["eur_per_year"] is not None
        and c["ride"]["eur_per_year"] is not None
    )
    assert (
        c["island"]["eur_per_year"] is None
        and c["island"]["eur_flag"] == G.SEVERAL_PRODUCTS
    )


def test_island_in_block_a_row_with_no_product_gives_none(net2, tmp_path):
    island = [("mill", True, RATE, "concentrate"), ("pump", False, 3.0, None)]
    c = _campus_cells(net2, _write_products(tmp_path / "run", island=island))
    assert (
        c["inside"]["eur_per_year"] is None
        and c["inside"]["eur_flag"] == G.SEVERAL_PRODUCTS
    )
    assert c["outside"]["eur_per_year"] is not None
    assert c["island"]["eur_per_year"] is None


def test_island_rows_with_zero_tonnes_and_no_product_do_not_poison_the_sum(
    net2, tmp_path
):
    island = [
        ("mill", True, RATE, "concentrate"),
        ("pump", False, 3.0, "acid"),
        ("fan", True, 0.0, None),
        ("valve", False, 0.0, None),
    ]
    c = _campus_cells(net2, _write_products(tmp_path / "run", island=island))
    events = _year_hours() * Q_IMPORT / MTTR_IMPORT
    assert c["outside"]["eur_per_year"] == pytest.approx(
        RATE * MTTR_IMPORT * events * LPV
    )
    assert c["inside"]["eur_per_year"] == pytest.approx(3.0 * events * LPV_ACID)


def test_several_products_price_no_sum_they_are_not_given_the_rows_of():
    prices = G._Prices(None, G.SEVERAL_PRODUCTS, {"concentrate": LPV})
    assert prices.eur(None, []) == (None, None)  # no tonnes: no EUR, and no flag
    assert prices.eur(5.0) == (
        None,
        G.SEVERAL_PRODUCTS,
    )  # no unit rows: never priced as a whole
    assert prices.eur(5.0, [(5.0, "concentrate")]) == (pytest.approx(5.0 * LPV), None)


def test_a_row_naming_a_product_with_no_shortfall_is_not_priced(net2, tmp_path):
    dip = [("mill", 7.5, "concentrate"), ("pump", 2.5, "slurry")]
    c = _campus_cells(net2, _write_products(tmp_path / "run", dip=dip))
    assert (
        c["ride"]["eur_per_year"] is None
        and c["ride"]["eur_flag"] == G.SEVERAL_PRODUCTS
    )


def test_a_product_with_no_tonne_factor_is_unpriced_never_zero(tmp_path):
    n = _two_products()
    n.buses.at["acid", "unit"] = "MWh"  # no mwh_per_t: its tonnes have no price
    c = _campus_cells(n, _write_products(tmp_path / "run"))
    assert c["ride"]["eur_per_year"] is None and c["ride"]["eur_flag"] == G.LPV_MISSING


# --- G2P #22: N-1 per-transformer sub-rows (GK A (17) G22-1..6, UX (13)) -------------------------------------------


def _two_trafos(periods=None):
    """The chain with a second site transformer in parallel, site_trafo_b, with its own outage data."""
    n = _network(periods=periods)
    n.add(
        "Link", "site_trafo_b", bus0="site", bus1="mill_bus", carrier="AC", p_nom=50.0
    )
    n.links.at["site_trafo_b", "eh_process"] = False
    n.links.at["site_trafo_b", "eh_role"] = ""
    _outage(n, "site_trafo_b", Q_TRAFO, MTTR_TRAFO)
    return n


def _prod(t, eur, flag=None, product="concentrate"):
    return {
        "product": product,
        "lost_t_per_year": t,
        "lost_eur_per_year": eur,
        "flag": flag,
    }


_SCREEN_FLAGS = {
    "line": ["no_inventory_credit"],
    "period": ["feedstock_inventory_not_credited"],
}


def _fake_loss(monkeypatch, by_link):
    """Stand in for the process-loss screen: ``by_link[link]`` is {period key: (products, bound)}."""
    monkeypatch.setattr(G.PL, "units_cut_off", lambda n, link, period=None: ["mill"])

    def loss(n, link, mttr, q, lost_load=None):
        return {
            "stopped_units": ["mill"],
            "flags": _SCREEN_FLAGS["line"],  # one shared list: never changed in place
            "by_period": {
                k: {
                    "occurrence_per_year": 1.0,
                    "outage_hours": mttr,
                    "products": prods,
                    "flags": _SCREEN_FLAGS["period"],
                    **({"bound": bound} if bound else {}),
                }
                for k, (prods, bound) in by_link[link].items()
            },
        }

    monkeypatch.setattr(G.PL, "expected_outage_loss", loss)


def _n1(n):
    return _line(G.grid_risk_production(n), "n_1")


def _fold_ref(prods):
    """Today's line rule, written out: t None if any t is None; EUR None with the last unpriced product's flag."""
    t = eur = 0.0
    ef = None
    for p in prods:
        t = (
            None
            if p["lost_t_per_year"] is None
            else (None if t is None else t + p["lost_t_per_year"])
        )
        if p["lost_eur_per_year"] is None:
            eur, ef = None, p["flag"] or G.LPV_MISSING
        elif eur is not None:
            eur += p["lost_eur_per_year"]
    return {
        "t_per_year": t,
        "eur_per_year": eur if t is not None else None,
        "eur_flag": ef,
    }


@pytest.fixture(scope="module")
def net_b():
    return _two_trafos()


def test_each_site_transformer_is_a_row_with_its_own_fold_and_the_line_folds_them_all(
    net_b, monkeypatch
):
    # Float sums that depend on order: the line must fold every product in today's order, never re-add row sums.
    a = [_prod(0.1, 0.1), _prod(0.2, 1e16, product="acid")]
    b = [_prod(0.3, 0.3), _prod(1e16, 0.2, product="acid")]
    _fake_loss(
        monkeypatch, {"site_trafo": {"_": (a, None)}, "site_trafo_b": {"_": (b, None)}}
    )
    n1 = _n1(net_b)
    assert [r["key"] for r in n1["rows"]] == [
        "site_trafo",
        "site_trafo_b",
    ]  # the backend's order
    assert [r["label"] for r in n1["rows"]] == [
        "site_trafo out of service",
        "site_trafo_b out of service",
    ]
    for r, prods in zip(n1["rows"], (a, b)):
        cell = r["by_period"]["_"]
        assert {
            k: cell[k] for k in ("t_per_year", "eur_per_year", "eur_flag")
        } == _fold_ref(prods)
        assert (
            cell["products"] == prods and cell["occurrence_per_year"] == 1.0
        )  # existing keys untouched
    assert n1["by_period"]["_"] == _fold_ref(a + b)  # exact bits, as master computes it
    assert G.LOWER_BOUND not in n1["flags"]  # no row is a lower bound
    rows = [r["by_period"]["_"] for r in n1["rows"]]
    assert n1["by_period"]["_"]["t_per_year"] == pytest.approx(
        sum(c["t_per_year"] for c in rows)
    )


def test_the_n_1_line_is_the_flat_fold_never_the_sum_of_the_row_cells(
    net_b, monkeypatch
):
    # G22-1: 1e16 + 1 + 1 + 1 folded flat is 1e16; the row sums 1e16 + 2 give 1.0000000000000002e16.
    a = [_prod(1e16, 1e16), _prod(1.0, 1.0, product="acid")]
    b = [_prod(1.0, 1.0), _prod(1.0, 1.0, product="acid")]
    _fake_loss(
        monkeypatch, {"site_trafo": {"_": (a, None)}, "site_trafo_b": {"_": (b, None)}}
    )
    n1 = _n1(net_b)
    rows = [r["by_period"]["_"] for r in n1["rows"]]
    line = n1["by_period"]["_"]
    assert line["t_per_year"] == 1e16 and line["eur_per_year"] == 1e16  # exact bits
    assert (
        rows[0]["t_per_year"] + rows[1]["t_per_year"] == 1.0000000000000002e16
    )  # the row-sum grouping differs, so this test tells them apart


def test_a_row_with_unknown_tonnes_has_no_eur_even_when_priced(net_b, monkeypatch):
    a = [_prod(None, 50.0), _prod(1.0, 10.0)]
    b = [_prod(2.0, 20.0)]
    _fake_loss(
        monkeypatch, {"site_trafo": {"_": (a, None)}, "site_trafo_b": {"_": (b, None)}}
    )
    ra, rb = (r["by_period"]["_"] for r in _n1(net_b)["rows"])
    assert ra["t_per_year"] is None and ra["eur_per_year"] is None
    assert rb["t_per_year"] == 2.0 and rb["eur_per_year"] == 20.0


def test_a_row_with_an_unpriced_product_has_no_eur_and_names_why(net_b, monkeypatch):
    a = [_prod(1.0, None, flag="mttr_missing"), _prod(1.0, 10.0)]
    b = [_prod(2.0, 20.0)]
    _fake_loss(
        monkeypatch, {"site_trafo": {"_": (a, None)}, "site_trafo_b": {"_": (b, None)}}
    )
    n1 = _n1(net_b)
    ra, rb = (r["by_period"]["_"] for r in n1["rows"])
    assert (
        ra["t_per_year"] == 2.0
        and ra["eur_per_year"] is None
        and ra["eur_flag"] == "mttr_missing"
    )
    assert rb["eur_per_year"] == 20.0 and rb["eur_flag"] is None
    assert n1["by_period"]["_"]["eur_per_year"] is None


def test_the_last_unpriced_products_flag_wins_and_no_flag_reads_missing_value(
    net_b, monkeypatch
):
    a = [_prod(1.0, None, flag="mttr_missing"), _prod(1.0, None, flag=None)]
    b = [_prod(1.0, None, flag=None), _prod(1.0, None, flag="mttr_missing")]
    _fake_loss(
        monkeypatch, {"site_trafo": {"_": (a, None)}, "site_trafo_b": {"_": (b, None)}}
    )
    ra, rb = (r["by_period"]["_"] for r in _n1(net_b)["rows"])
    assert ra["eur_flag"] == G.LPV_MISSING and rb["eur_flag"] == "mttr_missing"


def test_a_rows_lower_bound_sits_in_that_periods_row_cell_only(monkeypatch):
    n = _two_trafos(periods=[2030, 2040])
    p = [_prod(1.0, 10.0)]
    _fake_loss(
        monkeypatch,
        {
            "site_trafo": {"2030": (p, "lower"), "2040": (p, None)},
            "site_trafo_b": {"2030": (p, None), "2040": (p, None)},
        },
    )
    n1 = _n1(n)
    ra, rb = n1["rows"]
    assert G.LOWER_BOUND in ra["by_period"]["2030"]["flags"]
    assert G.LOWER_BOUND not in ra["by_period"]["2040"]["flags"]
    assert all(G.LOWER_BOUND not in c["flags"] for c in rb["by_period"].values())
    assert G.LOWER_BOUND not in ra["flags"]  # never the row-level flags
    assert ra["flags"] == [
        "no_inventory_credit"
    ]  # the row-level flags as the screen gave them
    assert G.LOWER_BOUND in n1["flags"]  # the line still says so
    assert _SCREEN_FLAGS == {
        "line": ["no_inventory_credit"],
        "period": ["feedstock_inventory_not_credited"],
    }


def test_the_n_1_line_names_why_it_is_a_lower_bound(monkeypatch):
    """G2P-13: the line's bound comes from an outage longer than the period, so its flags say that."""
    p = [_prod(1.0, 10.0)]
    _fake_loss(
        monkeypatch,
        {"site_trafo": {"_": (p, "lower")}, "site_trafo_b": {"_": (p, "lower")}},
    )
    n1 = _n1(_two_trafos())
    assert n1["flags"] == [
        "no_inventory_credit",
        G.PL.OUTAGE_LONGER_THAN_PERIOD,
        G.LOWER_BOUND,
    ]  # the reason once, before the bound it explains
    assert n1["status"] == "partial"
    _fake_loss(
        monkeypatch,
        {"site_trafo": {"_": (p, None)}, "site_trafo_b": {"_": (p, None)}},
    )
    n1 = _n1(_two_trafos())
    assert n1["flags"] == ["no_inventory_credit"] and n1["status"] == "ok"
    assert _SCREEN_FLAGS == {
        "line": ["no_inventory_credit"],
        "period": ["feedstock_inventory_not_credited"],
    }


def test_one_long_outage_period_flags_the_line_once_and_moves_no_figure(monkeypatch):
    """GK A N28-2: one transformer, one of two periods bounded; the figures are those of the unbounded line."""
    p = [_prod(1.0, 10.0)]

    def run(bound):
        _fake_loss(monkeypatch, {"site_trafo": {"2030": (p, bound), "2040": (p, None)}})
        return _n1(_network(periods=[2030, 2040]))

    n1, plain = run("lower"), run(None)
    assert n1["flags"].count(G.PL.OUTAGE_LONGER_THAN_PERIOD) == 1
    assert n1["flags"].count(G.LOWER_BOUND) == 1 and n1["status"] == "partial"
    assert G.PL.OUTAGE_LONGER_THAN_PERIOD not in plain["flags"]
    assert G.LOWER_BOUND not in plain["flags"] and plain["status"] == "ok"
    assert n1["by_period"] == plain["by_period"]  # no figure moves with the flag
    assert {
        k: (c["t_per_year"], c["eur_per_year"]) for k, c in n1["by_period"].items()
    } == {
        "2030": (1.0, 10.0),
        "2040": (1.0, 10.0),
    }
    cell = n1["rows"][0]["by_period"]
    assert cell["2030"]["flags"] == ["feedstock_inventory_not_credited", G.LOWER_BOUND]
    assert cell["2040"]["flags"] == ["feedstock_inventory_not_credited"]
    assert _SCREEN_FLAGS == {
        "line": ["no_inventory_credit"],
        "period": ["feedstock_inventory_not_credited"],
    }  # the screen's own lists are never changed


def test_a_skipped_transformer_gets_no_row_and_the_basis_says_so(monkeypatch):
    n = _two_trafos()
    n.links.at["site_trafo_b", "mttr_hours"] = float("nan")
    p = [_prod(1.0, 10.0)]
    _fake_loss(
        monkeypatch, {"site_trafo": {"_": (p, None)}, "site_trafo_b": {"_": (p, None)}}
    )
    n1 = _n1(n)
    assert [r["key"] for r in n1["rows"]] == ["site_trafo"]
    assert (
        "credited. It covers only the transformers listed. Not counted: site_trafo_b ("
        in n1["basis"]
    )
    assert "—" not in n1["basis"]


def test_with_every_transformer_listed_the_basis_names_no_skip(net_b, monkeypatch):
    p = [_prod(1.0, 10.0)]
    _fake_loss(
        monkeypatch, {"site_trafo": {"_": (p, None)}, "site_trafo_b": {"_": (p, None)}}
    )
    assert "only the transformers listed" not in _n1(net_b)["basis"]


def test_a_row_with_no_entry_for_a_period_adds_nothing_to_that_period(monkeypatch):
    n = _two_trafos(periods=[2030, 2040])
    p = [_prod(1.0, 10.0)]
    _fake_loss(
        monkeypatch,
        {
            "site_trafo": {"2030": (p, None), "2040": (p, None)},
            "site_trafo_b": {"2030": (p, None)},
        },
    )
    n1 = _n1(n)
    assert (
        n1["by_period"]["2030"]["t_per_year"] == 2.0
        and n1["by_period"]["2040"]["t_per_year"] == 1.0
    )


def test_island_outside_blocks_prices_rate_times_mass_pull_per_product(net2, tmp_path):
    campus = _write_products(tmp_path / "run")
    df = pd.read_csv(campus.run_dir / G.RESTART_CSV)
    df.loc[df["unit"] == "mill", "mass_pull"] = (
        0.5  # 0.5 t of concentrate per t of the mill's output
    )
    df.to_csv(campus.run_dir / G.RESTART_CSV, index=False)
    c = _campus_cells(net2, campus)
    events = _year_hours() * Q_IMPORT / MTTR_IMPORT
    assert c["outside"]["t_per_year"] == pytest.approx(
        RATE * 0.5 * MTTR_IMPORT * events
    )
    assert c["outside"]["eur_per_year"] == pytest.approx(
        RATE * 0.5 * MTTR_IMPORT * events * LPV
    )
