"""
Every component class end-to-end, on one solved multi-class network.

The per-class compute functions are the bulk of this feature, and each one
individually is two lines that a unit test would only restate. What actually
has to hold is the property the user complained about: open any asset, and
every category that applies to it shows real numbers rather than an empty
panel. So these tests drive the real endpoint against a real HiGHS solve and
assert on the payload.
"""
import pandas as pd
import pypsa
import pytest

BASE = "/api/results/asset"

# Categories each class must populate, and the ones that are structurally
# empty for it. Mirrors registry EXPECTED_EMPTY — asserted here through the
# HTTP layer rather than against the registry, so a metric that resolves but
# computes to None still fails.
POPULATED: dict[str, tuple[str, ...]] = {
    "Bus": ("capacity", "dispatch", "prices", "economics"),
    "Generator": ("capacity", "dispatch", "prices", "economics"),
    "Load": ("dispatch", "prices", "economics"),
    "Line": ("capacity", "loadflow", "prices", "economics"),
    "Transformer": ("capacity", "loadflow", "prices", "economics"),
    "Link": ("capacity", "dispatch", "loadflow", "prices", "economics"),
    "StorageUnit": ("capacity", "dispatch", "storage", "prices", "economics"),
    "Store": ("capacity", "dispatch", "storage", "prices", "economics"),
}

ASSETS: dict[str, str] = {
    "Bus": "B2",
    "Generator": "gas",
    "Load": "load_b2",
    "Line": "line_12",
    "Transformer": "trafo_23",
    "Link": "electrolyser",
    "StorageUnit": "battery",
    "Store": "h2_store",
}


def build_multiclass_network(*, solve: bool = True) -> pypsa.Network:
    """
    A network carrying one of every component class, sized so each of them
    actually does something over the horizon: the battery arbitrages a daily
    price swing, the electrolyser runs on cheap hours, the line and the
    transformer both carry flow, and the store buffers hydrogen.
    """
    n = pypsa.Network()
    sns = pd.date_range("2025-01-01", periods=24, freq="h")
    n.set_snapshots(sns)

    # Three AC buses (two at 380 kV, one at 110 kV behind a transformer) plus
    # a hydrogen bus so the Link has somewhere to convert into.
    n.add("Bus", "B1", v_nom=380.0, carrier="AC")
    n.add("Bus", "B2", v_nom=380.0, carrier="AC")
    n.add("Bus", "B3", v_nom=110.0, carrier="AC")
    n.add("Bus", "H2", v_nom=1.0, carrier="hydrogen")

    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "solar")
    n.add("Carrier", "hydrogen")

    # A daily solar shape: zero at night, peaking at midday. Sized to OVERTOP
    # total demand around noon — that is what drives the marginal price down
    # to zero for part of the day, and without a price swing the battery
    # never cycles and half these assertions would pass on empty results.
    solar_pu = [0.0] * 6 + [0.15, 0.35, 0.6, 0.8, 0.95, 1.0,
                            1.0, 0.95, 0.8, 0.6, 0.35, 0.15] + [0.0] * 6
    n.add("Generator", "solar", bus="B1", carrier="solar", p_nom=700.0,
          marginal_cost=0.0, capital_cost=60_000.0,
          p_max_pu=pd.Series(solar_pu, index=sns))
    n.add("Generator", "gas", bus="B1", carrier="gas", p_nom=500.0,
          marginal_cost=80.0, capital_cost=100_000.0, efficiency=0.5)

    load_mw = [220.0 + 60.0 * (1 if 7 <= h <= 21 else 0) for h in range(24)]
    n.add("Load", "load_b2", bus="B2", p_set=pd.Series(load_mw, index=sns))
    n.add("Load", "load_b3", bus="B3", p_set=80.0)
    n.add("Load", "load_h2", bus="H2", p_set=8.0)

    n.add("Line", "line_12", bus0="B1", bus1="B2", x=0.1, r=0.01, s_nom=600.0,
          capital_cost=1_000.0)
    n.add("Transformer", "trafo_23", bus0="B2", bus1="B3", x=0.1, r=0.01,
          s_nom=250.0, capital_cost=2_000.0)

    n.add("StorageUnit", "battery", bus="B2", p_nom=80.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.95, marginal_cost=0.5,
          capital_cost=20_000.0, cyclic_state_of_charge=True)
    n.add("Link", "electrolyser", bus0="B1", bus1="H2", carrier="hydrogen",
          p_nom=60.0, efficiency=0.7, marginal_cost=0.2, capital_cost=15_000.0)
    n.add("Store", "h2_store", bus="H2", e_nom=400.0, e_cyclic=True,
          capital_cost=500.0, marginal_cost=0.1)

    if solve:
        n.optimize(solver_name="highs")
    return n


@pytest.fixture
def multiclass(install_network):
    n = build_multiclass_network(solve=True)
    install_network(n)
    return n


def _get(client, cls, name, **params):
    r = client.get(f"{BASE}/{cls}/{name}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_network_actually_uses_every_component(multiclass):
    """
    Guard on the FIXTURE, not the feature. Every assertion below is only
    meaningful if these components carry non-zero results; a fixture that
    silently stopped dispatching the battery would turn the storage
    assertions into "None == None" and pass forever.
    """
    n = multiclass
    assert n.storage_units_t.p_dispatch["battery"].sum() > 0, "battery never discharged"
    assert n.storage_units_t.p_store["battery"].sum() > 0, "battery never charged"
    assert n.links_t.p0["electrolyser"].sum() > 0, "electrolyser never ran"
    assert n.lines_t.p0["line_12"].abs().sum() > 0, "line carried no flow"
    assert n.transformers_t.p0["trafo_23"].abs().sum() > 0, "transformer carried no flow"
    assert n.stores_t.e["h2_store"].abs().sum() > 0, "store never held energy"
    assert n.generators_t.p["gas"].sum() > 0, "gas never ran"


@pytest.mark.parametrize("cls", list(ASSETS))
def test_every_asset_lists_and_resolves_its_categories(client, multiclass, cls):
    name = ASSETS[cls]
    body = _get(client, cls, name, category="summary")
    by_id = {c["id"]: c for c in body["categories"]}
    assert [c["id"] for c in body["categories"]] == [
        "summary", "capacity", "dispatch", "storage",
        "loadflow", "prices", "economics", "emissions"]
    for cat in POPULATED[cls]:
        assert by_id[cat]["status"] == "ok", \
            f"{cls}/{cat} is {by_id[cat]['status']}: {by_id[cat].get('reason')}"
    # Whatever is not ok must say why — a greyed tab with no reason is the
    # exact failure this feature exists to prevent.
    for cat, entry in by_id.items():
        if entry["status"] != "ok":
            assert entry.get("reason"), f"{cls}/{cat} is greyed with no reason"
            if entry["status"] == "na":
                assert "remedy" not in entry, "na must never carry a remedy"


@pytest.mark.parametrize("cls", list(ASSETS))
def test_every_populated_category_returns_real_numbers(client, multiclass, cls):
    """
    The complaint this feature answers: categories that resolve `ok` but come
    back with nothing in them. Ask for every applicable metric and require
    that at least one scalar or one series column actually carries a value.
    """
    name = ASSETS[cls]
    for cat in POPULATED[cls]:
        body = _get(client, cls, name, category=cat)
        scalars = {k: v for k, v in body["scalars"].items() if v is not None}
        series = {c["id"] for c in body["columns"]}
        assert scalars or series, f"{cls}/{cat} resolved ok but returned nothing"
        for col in series:
            values = body["series"][col]
            assert len(values) == len(body["index"])
            assert any(v is not None for v in values), \
                f"{cls}/{cat}/{col} is entirely null"


@pytest.mark.parametrize("cls", list(ASSETS))
def test_summary_carries_headline_kpis_from_the_other_tabs(client, multiclass, cls):
    body = _get(client, cls, ASSETS[cls], category="summary")
    headline = body["headline"]
    assert headline, f"{cls} summary has no headline KPIs"
    for row in headline:
        assert row["label"] and row["category_label"]
        assert row["status"] in ("ok", "blocked", "na")
        if row["status"] == "ok":
            assert "value" in row, f"{cls} headline {row['id']} is ok with no value"
        else:
            assert row.get("reason"), f"{cls} headline {row['id']} greyed with no reason"
    # At least one must have actually computed, or the tab is still empty.
    assert any(r["status"] == "ok" for r in headline), \
        f"{cls} headline KPIs are all unavailable"
    # And they must come from more than one source tab — the whole point is
    # aggregating across categories, not restating one of them.
    ok_cats = {r["category"] for r in headline if r["status"] == "ok"}
    assert len(ok_cats) >= 2, f"{cls} headline draws from only {ok_cats}"


def test_headline_ids_are_absent_from_the_summary_category_itself(
        client, multiclass):
    """
    Headlines are lifted from OTHER categories. If one ever landed in
    `summary` itself it would render twice on the same tab.
    """
    body = _get(client, "Generator", "gas", category="summary")
    summary_ids = {m["id"] for m in body["metrics"]}
    for row in body["headline"]:
        assert row["id"] not in summary_ids
        assert row["category"] != "summary"


# ── Item 8: a bus must show voltage, load, generation, prices, capacity ─────

def test_bus_reports_generation_load_and_capacity_matching_the_network(
        client, multiclass):
    n = multiclass
    body = _get(client, "Bus", "B1", category="dispatch")
    gen = body["series"]["bus_generation"]
    expected = (n.generators_t.p[["solar", "gas"]].sum(axis=1)).tolist()
    assert gen == pytest.approx(expected)

    cap = _get(client, "Bus", "B1", category="capacity")
    # B1 carries solar (700) + gas (500); the battery and store sit elsewhere.
    assert cap["scalars"]["bus_gen_p_nom"] == pytest.approx(1200.0)
    by_carrier = cap["scalars"]["bus_capacity_by_carrier"]
    assert set(by_carrier) == {"solar", "gas"}
    assert sum(by_carrier.values()) == pytest.approx(1200.0)
    # The battery sits at B2, so it must NOT be counted into B1's totals.
    assert cap["scalars"]["bus_storage_p_nom_opt"] in (None, 0.0)


def test_bus_load_matches_the_loads_attached_to_it(client, multiclass):
    n = multiclass
    body = _get(client, "Bus", "B2", category="dispatch")
    assert body["series"]["bus_load"] == pytest.approx(
        n.loads_t.p["load_b2"].tolist())
    # …and only those: B3's load must not leak into B2's total.
    assert body["scalars"]["bus_peak_load"] == pytest.approx(280.0)


def test_bus_prices_are_populated_and_ordered(client, multiclass):
    body = _get(client, "Bus", "B2", category="prices")
    s = body["scalars"]
    assert s["bus_price_min"] <= s["bus_price_mean"] <= s["bus_price_max"]
    assert any(v is not None for v in body["series"]["bus_marginal_price"])


def test_bus_load_flow_is_blocked_on_ac_pf_not_missing(client, multiclass):
    """The voltage tab exists for a bus; it just needs stage 2 to have run."""
    body = _get(client, "Bus", "B2", category="summary")
    lf = next(c for c in body["categories"] if c["id"] == "loadflow")
    assert lf["status"] == "blocked"
    assert lf["remedy"]["action"] == "run_ac_pf"
    ids = {m["id"] for m in _get(client, "Bus", "B2", category="loadflow")["metrics"]}
    assert {"bus_v_mag_pu", "bus_v_ang", "bus_v_min", "bus_v_max"} <= ids


# ── Item 6: lines and transformers must have a populated load-flow tab ──────

@pytest.mark.parametrize("cls,name,attr", [
    ("Line", "line_12", "lines_t"),
    ("Transformer", "trafo_23", "transformers_t"),
])
def test_branch_load_flow_carries_flow_loading_and_losses(
        client, multiclass, cls, name, attr):
    n = multiclass
    body = _get(client, cls, name, category="loadflow")
    frame = getattr(n, attr)
    assert body["series"]["p0"] == pytest.approx(frame.p0[name].tolist())
    assert body["series"]["p1"] == pytest.approx(frame.p1[name].tolist())
    # losses = p0 + p1, by PyPSA's "power into the branch at each end" sign.
    assert body["series"]["losses"] == pytest.approx(
        (frame.p0[name] + frame.p1[name]).tolist())

    s_nom = float(n.df(cls).at[name, "s_nom_opt"])
    assert body["series"]["loading"] == pytest.approx(
        (frame.p0[name].abs() / s_nom * 100.0).tolist())
    assert 0.0 <= body["scalars"]["max_loading"] <= 100.0 + 1e-6
    assert body["scalars"]["gross_transfer_mwh"] > 0
    assert body["scalars"]["mean_loading"] <= body["scalars"]["max_loading"]


@pytest.mark.parametrize("cls,name", [
    ("Line", "line_12"), ("Transformer", "trafo_23")])
def test_branch_hours_metrics_sum_to_the_horizon(client, multiclass, cls, name):
    """
    Congested / reverse / idle hours are weighted hour-counts, not snapshot
    counts. On a flat-weighted 24-snapshot network the total horizon is 24 h,
    so no single hour-count may exceed it.
    """
    s = _get(client, cls, name, category="loadflow")["scalars"]
    for key in ("congested_hours", "reverse_hours", "idle_hours"):
        assert 0.0 <= s[key] <= 24.0, f"{key} = {s[key]} is outside the horizon"


# ── Item 7: links and storage must not come back empty ──────────────────────

def test_link_dispatch_reports_throughput_and_realised_efficiency(
        client, multiclass):
    n = multiclass
    body = _get(client, "Link", "electrolyser", category="dispatch")
    p0 = n.links_t.p0["electrolyser"]
    assert body["series"]["p0"] == pytest.approx(p0.tolist())
    # Delivery is sign-flipped p1 so it reads as a positive output.
    assert body["series"]["link_output"] == pytest.approx(
        (-n.links_t.p1["electrolyser"]).tolist())
    s = body["scalars"]
    assert s["energy_in_mwh"] > 0
    assert s["energy_out_mwh"] > 0
    # efficiency=0.7, and with no time-varying override the realised value
    # must land on it exactly.
    assert s["mean_efficiency"] == pytest.approx(0.7, rel=1e-6)
    assert s["losses_mwh"] == pytest.approx(s["energy_in_mwh"] - s["energy_out_mwh"])


def test_link_emissions_use_primary_energy_not_output(client, multiclass):
    """
    A link's co2_emissions applies to what it WITHDRAWS (p0), unlike a
    generator where the electrical output is divided by efficiency first.
    """
    n = multiclass
    n.carriers.loc["hydrogen", "co2_emissions"] = 0.1
    body = _get(client, "Link", "electrolyser", category="emissions")
    assert body["series"]["co2_rate"] == pytest.approx(
        (n.links_t.p0["electrolyser"] * 0.1).tolist())


def test_storage_unit_reports_state_of_charge_and_cycling(client, multiclass):
    n = multiclass
    body = _get(client, "StorageUnit", "battery", category="storage")
    soc = n.storage_units_t.state_of_charge["battery"]
    assert body["series"]["state_of_charge"] == pytest.approx(soc.tolist())

    s = body["scalars"]
    capacity = 80.0 * 4.0
    assert body["series"]["soc_pu"] == pytest.approx((soc / capacity).tolist())
    assert s["soc_min"] == pytest.approx(float(soc.min()))
    assert s["soc_max"] == pytest.approx(float(soc.max()))
    assert s["full_cycles"] > 0
    # Realised round-trip must not exceed the product of the two one-way
    # efficiencies — if it does, the sign convention is inverted somewhere.
    assert 0 < s["round_trip_efficiency"] <= 0.95 * 0.95 + 1e-6


def test_storage_unit_dispatch_splits_charge_from_discharge(client, multiclass):
    n = multiclass
    body = _get(client, "StorageUnit", "battery", category="dispatch")
    assert body["series"]["p_dispatch"] == pytest.approx(
        n.storage_units_t.p_dispatch["battery"].tolist())
    assert body["series"]["p_store"] == pytest.approx(
        n.storage_units_t.p_store["battery"].tolist())
    s = body["scalars"]
    assert s["energy_discharged_mwh"] > 0 and s["energy_charged_mwh"] > 0
    assert s["charge_hours"] + s["discharge_hours"] <= 24.0


def test_storage_unit_economics_buys_low_and_sells_high(client, multiclass):
    """
    An optimal battery cannot pay more per MWh charged than it earns per MWh
    discharged — if it did, the LP would simply not have cycled it.
    """
    s = _get(client, "StorageUnit", "battery", category="prices")["scalars"]
    assert s["capture_price"] is not None and s["charge_price"] is not None
    assert s["captured_spread"] == pytest.approx(
        s["capture_price"] - s["charge_price"])
    assert s["captured_spread"] >= -1e-6


def test_store_reports_energy_level_and_balance(client, multiclass):
    n = multiclass
    body = _get(client, "Store", "h2_store", category="storage")
    e = n.stores_t.e["h2_store"]
    assert body["series"]["e"] == pytest.approx(e.tolist())
    s = body["scalars"]
    assert s["e_min"] == pytest.approx(float(e.min()))
    assert s["e_max"] == pytest.approx(float(e.max()))

    d = _get(client, "Store", "h2_store", category="dispatch")["scalars"]
    assert d["energy_in_mwh"] > 0 and d["energy_out_mwh"] > 0


def test_load_shows_demand_and_cost(client, multiclass):
    n = multiclass
    body = _get(client, "Load", "load_b2", category="dispatch")
    assert body["series"]["load_p"] == pytest.approx(
        n.loads_t.p["load_b2"].tolist())
    s = body["scalars"]
    assert s["peak_mw"] == pytest.approx(280.0)
    assert s["energy_mwh"] == pytest.approx(float(n.loads_t.p["load_b2"].sum()))
    assert 0 < s["load_factor"] <= 1.0
    econ = _get(client, "Load", "load_b2", category="economics")["scalars"]
    assert econ["energy_cost_eur"] > 0


def test_load_demand_is_visible_before_a_solve(client, install_network):
    """
    Load is a model INPUT. On an unsolved network `loads_t.p` is empty, so
    the demand profile has to fall back to `p_set` — otherwise the one thing
    a user can definitely inspect pre-solve comes back blank.
    """
    install_network(build_multiclass_network(solve=False))
    body = _get(client, "Load", "load_b2", category="dispatch")
    assert body["series"]["load_p"][:8] == pytest.approx(
        [220.0] * 7 + [280.0])
    assert body["scalars"]["peak_mw"] == pytest.approx(280.0)


# ── AC power flow: the one path that needs stage 2 ──────────────────────────

def test_ac_pf_lights_up_bus_voltage_and_branch_reactive_power(
        client, install_network):
    """
    Runs a REAL `n.pf()` on a purely-AC network and stamps the result exactly
    the way ac_pf_service does, so the `source_override='ac_pf'` path is
    exercised end to end rather than mocked.
    """
    from services.ac_pf_service import _snapshot_result_state
    from routers.simulation import _state

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "A", v_nom=380.0, carrier="AC")
    n.add("Bus", "B", v_nom=380.0, carrier="AC")
    n.add("Line", "AB", bus0="A", bus1="B", x=0.1, r=0.01, s_nom=500.0)
    n.add("Generator", "g", bus="A", p_nom=400.0, marginal_cost=50.0,
          control="Slack")
    n.add("Load", "l", bus="B", p_set=200.0, q_set=40.0)
    n.optimize(solver_name="highs")
    install_network(n)

    lopf = _snapshot_result_state(n)
    n.pf()
    _state["lopf_results"] = lopf
    _state["ac_pf_results"] = _snapshot_result_state(n)
    try:
        body = _get(client, "Bus", "B", category="loadflow", source="ac_pf")
        lf = next(c for c in body["categories"] if c["id"] == "loadflow")
        assert lf["status"] == "ok"
        assert body["series"]["bus_v_mag_pu"] == pytest.approx(
            n.buses_t.v_mag_pu["B"].tolist())
        assert body["scalars"]["bus_v_min"] > 0

        line = _get(client, "Line", "AB", category="loadflow", source="ac_pf")
        assert line["series"]["q0"] == pytest.approx(n.lines_t.q0["AB"].tolist())
        assert any(v for v in line["series"]["q1"])
    finally:
        _state["lopf_results"] = None
        _state["ac_pf_results"] = None
