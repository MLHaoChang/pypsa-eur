"""
Grid-to-process ticket 04: results and compare economics read every Link port, and the process view.

One small solved Haber-Bosch chain, shaped as the A template's (design §3.A): bus0 = electricity, bus1 = NH3 in MWh
(LHV, 5.166 MWh/t), bus2 = H2 drawn at 5.93 MWh_H2 per t NH3. The NH3 bus has a tank, a delivery Load, a grey buy-in
(`__buy_nh3`) capped below the delivery, and a shortfall slack (`__lost_nh3`), so every booking the ticket names is
exercised by a real dispatch rather than a hand-set frame.
"""
import pandas as pd
import pypsa
import pytest

from services.asset_results import compute as C
from services.asset_results.applicability import resolve_metric
from services.asset_results.registry import metric_for
from services.compare.economics import _compute_economics_summary
from services.results.process_view import compute_process_view

MWH_PER_T = 5.166
H2_MWH_PER_T = 5.93
HB_EFF = 4.0                       # MWh_NH3 per MWh_el
HB_EFF2 = -(H2_MWH_PER_T / MWH_PER_T) * HB_EFF
LOST_EUR_PER_T = 800.0
VALUE_EUR_PER_MWH = 120.0


def _hb_network(*, lost_mc: float | None = LOST_EUR_PER_T / MWH_PER_T, product_value: float | None = VALUE_EUR_PER_MWH,
               roles: bool = True, slack_carrier: str = "NH3"):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2027-01-01", periods=6, freq="h"))
    n.add("Carrier", ["AC", "H2", "NH3"])
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Bus", "nh3", carrier="NH3", unit="MWh")
    n.buses["mwh_per_t"] = float("nan")
    n.buses.loc["nh3", "mwh_per_t"] = MWH_PER_T
    if product_value is not None:
        n.buses["product_value"] = float("nan")
        n.buses.loc["nh3", "product_value"] = product_value
    # Electricity is cut in hours 2 and 3: the HB stops, the tank and the capped buy-in cover what they can, the
    # rest is shortfall.
    n.add("Generator", "grid", bus="el", carrier="AC", p_nom=100.0, marginal_cost=50.0,
          p_max_pu=[1, 1, 0, 0, 1, 1])
    n.add("Generator", "h2_supply", bus="h2", carrier="H2", p_nom=200.0, marginal_cost=100.0)
    n.add("Link", "elz", bus0="el", bus1="h2", carrier="H2 electrolysis", p_nom=20.0, efficiency=0.68,
          marginal_cost=1.0, capital_cost=1000.0)
    n.add("Link", "hb", bus0="el", bus1="nh3", bus2="h2", carrier="Haber-Bosch", p_nom=10.0,
          efficiency=HB_EFF, efficiency2=HB_EFF2, capital_cost=5000.0, p_min_pu=0.0)
    n.links["eh_process"] = False
    n.links.loc["hb", "eh_process"] = True
    n.add("Store", "nh3_tank", bus="nh3", carrier="NH3", e_nom=20.0, e_cyclic=True)
    n.add("Load", "nh3_delivery", bus="nh3", carrier="NH3", p_set=30.0)
    n.add("Generator", "__buy_nh3", bus="nh3", carrier="NH3", p_nom=5.0, marginal_cost=97.0)
    if lost_mc is not None:
        n.add("Generator", "__lost_nh3", bus="nh3", carrier=slack_carrier, p_nom=1000.0, marginal_cost=lost_mc)
    if roles:
        n.generators["process_role"] = ""
        n.generators.loc["__buy_nh3", "process_role"] = "buy_in"
        if lost_mc is not None:
            n.generators.loc["__lost_nh3", "process_role"] = "shortfall"
    n.optimize(solver_name="highs")
    return n


@pytest.fixture(scope="module")
def hb():
    return _hb_network()


def _econ(n):
    return _compute_economics_summary(n, [], False, True, prices_from_state=False)


def _ctx(n, name):
    return C.build_ctx(n, "Link", name, source="lopf", sns=n.snapshots)


# ── 1. per-port flows ───────────────────────────────────────────────────────

def test_the_h2_drawn_is_5_93_over_5_166_times_the_nh3_made(hb):
    ctx = _ctx(hb, "hb")
    p1, p2 = C.raw("p1")(ctx), C.raw("p2")(ctx)
    made = -p1
    assert float(made.sum()) > 1.0
    assert list(p2.values) == pytest.approx(list((made * H2_MWH_PER_T / MWH_PER_T).values))


@pytest.mark.parametrize("port", ["p1", "p2", "p3", "p4"])
def test_every_port_is_a_link_metric(port):
    assert metric_for("Link", port) is not None


def test_a_port_the_link_does_not_use_is_not_applicable(hb):
    pre = C.preconditions(hb, "Link", "elz")
    st = resolve_metric(metric_for("Link", "p2"), "Link", pre)
    assert st.status == "na"
    assert "bus2" in st.reason
    pre_hb = C.preconditions(hb, "Link", "hb")
    assert pre_hb["port2"].status == "ok"
    assert pre_hb["port3"].status == "na"


# ── 2. compare economics ────────────────────────────────────────────────────

def _lcoh(econ, name):
    return next(e for e in econ.per_asset_lcoh if e.name == name)


def test_an_input_port_is_priced_at_its_bus_price(hb):
    entry = _lcoh(_econ(hb), "hb")
    lam = hb.buses_t.marginal_price
    p0, p2 = hb.links_t.p0["hb"], hb.links_t.p2["hb"]
    want = float((p0.clip(lower=0) * lam["el"]).sum() + (p2.clip(lower=0) * lam["h2"]).sum())
    assert entry.input_cost_meur.total == pytest.approx(want / 1e6)


def test_the_bus1_output_is_valued_at_the_product_value(hb):
    entry = _lcoh(_econ(hb), "hb")
    made = float((-hb.links_t.p1["hb"]).clip(lower=0).sum())
    assert entry.output_value_meur is not None
    assert entry.output_value_meur.total == pytest.approx(made * VALUE_EUR_PER_MWH / 1e6)
    assert entry.output_value_flag is None


def test_output_value_is_unavailable_without_a_product_value():
    n = _hb_network(product_value=None)
    entry = _lcoh(_econ(n), "hb")
    assert entry.output_value_meur is None
    assert entry.output_value_flag == "product_value_missing"


def test_shortfall_is_lost_revenue_and_never_an_expense(hb):
    econ = _econ(hb)
    lost_mwh = float(hb.generators_t.p["__lost_nh3"].sum())
    buy_mwh = float(hb.generators_t.p["__buy_nh3"].sum())
    assert lost_mwh > 1.0 and buy_mwh > 1.0
    nh3 = econ.by_carrier["nh3"]
    lost_eur = lost_mwh * LOST_EUR_PER_T / MWH_PER_T
    assert nh3.lost_production_meur.total == pytest.approx(lost_eur / 1e6)
    # The buy-in is the only expense on the NH3 carrier; the slack is not in it, nor in dispatch.
    assert nh3.gen_cost_meur.total == pytest.approx(buy_mwh * 97.0 / 1e6)
    assert nh3.opex_meur.total == pytest.approx(buy_mwh * 97.0 / 1e6)
    assert nh3.dispatch_gwh.total == pytest.approx(buy_mwh / 1000.0)


def test_lost_revenue_is_unavailable_when_the_slack_has_no_value():
    n = _hb_network(lost_mc=0.0)
    nh3 = _econ(n).by_carrier["nh3"]
    assert nh3.lost_production_meur is None


def test_a_single_port_link_reads_as_before(hb):
    entry = _lcoh(_econ(hb), "elz")
    p0 = hb.links_t.p0["elz"]
    lam = hb.buses_t.marginal_price["el"]
    assert entry.output_gwh.total == pytest.approx(float((p0.abs() * 0.68).sum()) / 1000.0)
    assert entry.input_cost_meur.total == pytest.approx(float((p0.clip(lower=0) * lam).sum()) / 1e6)
    # The electrolyser's H2 bus is no product bus with a value: unavailable, not zero.
    assert entry.output_value_meur is None


# ── 3. process view ─────────────────────────────────────────────────────────

def _product(view, bus):
    return next(p for p in view["products"] if p["bus"] == bus)


def test_the_process_view_reports_production_in_t_and_mwh(hb):
    nh3 = _product(compute_process_view(hb), "nh3")
    made = float((-hb.links_t.p1["hb"]).sum())
    assert nh3["unit"] == "MWh"
    assert nh3["production"]["mwh"] == pytest.approx(made)
    assert nh3["production"]["t"] == pytest.approx(made / MWH_PER_T)
    assert nh3["delivery"]["mwh"] == pytest.approx(30.0 * 6)
    assert nh3["delivery"]["t"] == pytest.approx(30.0 * 6 / MWH_PER_T)


def test_the_process_view_carries_the_inventory_level(hb):
    nh3 = _product(compute_process_view(hb), "nh3")
    inv = nh3["inventory"]
    assert [s["name"] for s in inv] == ["nh3_tank"]
    assert inv[0]["e"] == pytest.approx(list(hb.stores_t.e["nh3_tank"].values))


def test_the_process_view_prices_lost_production_at_its_value(hb):
    nh3 = _product(compute_process_view(hb), "nh3")
    lost_mwh = float(hb.generators_t.p["__lost_nh3"].sum())
    assert nh3["lost"]["t"] == pytest.approx(lost_mwh / MWH_PER_T)
    assert nh3["lost"]["eur"] == pytest.approx(lost_mwh / MWH_PER_T * LOST_EUR_PER_T)
    assert nh3["lost"]["eur_flag"] is None
    assert nh3["buy_in"]["t"] == pytest.approx(float(hb.generators_t.p["__buy_nh3"].sum()) / MWH_PER_T)


def test_lost_eur_is_null_and_flagged_without_a_value():
    n = _hb_network(lost_mc=None)
    nh3 = _product(compute_process_view(n), "nh3")
    assert nh3["lost"]["t"] == 0.0
    assert nh3["lost"]["eur"] is None
    assert nh3["lost"]["eur_flag"] == "lost_production_value_missing"


def test_running_hours_and_starts_per_process_unit(hb):
    units = compute_process_view(hb)["process_units"]
    assert [u["name"] for u in units] == ["hb"]
    p0 = hb.links_t.p0["hb"]
    assert units[0]["running_hours"] == pytest.approx(float((p0 > 1e-6).sum()))
    # Off in hours 2-3, back on in 4: one start in the modelled snapshots.
    assert units[0]["starts"] == 1
    assert units[0]["starts_basis"] == "modelled_snapshots"


def test_a_material_product_has_no_mwh():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2027-01-01", periods=2, freq="h"))
    n.add("Carrier", ["AC", "ore"])
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "ore", carrier="ore", unit="t")
    n.add("Generator", "grid", bus="el", p_nom=100.0, marginal_cost=10.0)
    n.add("Generator", "mine", bus="ore", p_nom=100.0)
    n.add("Load", "ore_out", bus="ore", p_set=5.0)
    n.add("Load", "el_load", bus="el", p_set=1.0)
    n.optimize(solver_name="highs")
    ore = _product(compute_process_view(n), "ore")
    assert ore["delivery"]["t"] == pytest.approx(10.0)
    assert ore["delivery"]["mwh"] is None
    assert ore["delivery"]["mwh_flag"] == "material_product"


def test_a_network_without_products_has_an_empty_view():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2027-01-01", periods=2, freq="h"))
    n.add("Bus", "el")
    n.add("Generator", "grid", bus="el", p_nom=10.0, marginal_cost=1.0)
    n.add("Load", "l", bus="el", p_set=1.0)
    n.optimize(solver_name="highs")
    assert compute_process_view(n) == {"products": [], "process_units": []}


# ── gatekeeper conditions (2026-10-19) ──────────────────────────────────────

def test_the_economics_still_sum_to_the_objective_with_the_shortfall_moved_out_of_opex(hb):
    """Nothing here is extendable, so the objective is all variable cost: every carrier's opex plus the lost
    production booked beside it. Without the lost-production term the sum would fall short by exactly that."""
    econ = _econ(hb)
    opex = sum(c.opex_meur.total for c in econ.by_carrier.values())
    lost = sum(c.lost_production_meur.total for c in econ.by_carrier.values() if c.lost_production_meur)
    assert lost > 0
    assert (opex + lost) * 1e6 == pytest.approx(float(hb.objective) + float(hb.objective_constant or 0.0), rel=1e-9)


def test_the_investment_case_reads_none_of_the_figures_this_ticket_changes():
    """Ticket 04 must not change what the investment case books; treating lost tonnes as lost revenue there is
    ticket 06/11's. The staged-template NPV pins cannot catch it (no shortfall generator), so pin the boundary."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    sources = [*(root / "services" / "finance").rglob("*.py"), *(root / "services" / "commercial").rglob("*.py"),
               *(root / "services" / "study").rglob("*.py"), root / "services" / "results" / "finance_case.py",
               root / "models" / "commercial.py"]
    forbidden = re.compile(r"compare\.economics|economics_by_carrier|CarrierEconomics|AssetLCOHEntry|per_asset_lcoh"
                           r"|_compute_economics_summary|process_view|process_products|lost_production")
    hits = [str(p.relative_to(root)) for p in sources if p.exists() and forbidden.search(p.read_text())]
    assert len(sources) > 3 and hits == []


def test_the_process_view_is_strict_json_when_a_series_holds_nan():
    import json
    n = _hb_network()
    n.stores_t.e.iloc[1, 0] = float("nan")
    n.loads_t.p.iloc[2, 0] = float("nan")
    view = compute_process_view(n)
    json.dumps(view, allow_nan=False)
    nh3 = _product(view, "nh3")
    assert nh3["inventory"][0]["e"][1] is None
    assert nh3["inventory"][0]["flag"] == "non_finite_values"
    assert nh3["delivery"]["value"] is None and nh3["delivery"]["t"] is None
    assert nh3["delivery"]["flag"] == "non_finite_values"


def test_the_process_view_endpoint_is_strict_json(monkeypatch, hb):
    import json

    import routers.results as R
    from services.pypsa_service import PyPSAService
    monkeypatch.setattr(PyPSAService, "get_network", staticmethod(lambda: hb))
    monkeypatch.setattr(R, "_dispatch_ready", lambda n: True)
    payload = R.get_process_view()
    json.dumps(payload, allow_nan=False)
    assert payload["available"] is True
    assert [p["bus"] for p in payload["products"]] == ["nh3"]


def test_every_quantity_names_its_weighting(hb):
    nh3 = _product(compute_process_view(hb), "nh3")
    for key in ("production", "delivery", "buy_in", "lost"):
        assert nh3[key]["weighting"] == "generators"
    assert nh3["lost"]["eur_weighting"] == "objective"
    assert nh3["inventory"][0]["weighting"] == "none"


def test_the_role_column_decides_and_a_name_alone_is_flagged():
    from services.process_products import generator_role
    n = _hb_network(roles=False)
    assert generator_role(n, "__lost_nh3") == ("shortfall", "role_from_name")
    assert _product(compute_process_view(n), "nh3")["role_flag"] == "role_from_name"
    assert _econ(n).by_carrier["nh3"].lost_production_flag == "role_from_name"
    n.generators["process_role"] = ""
    n.generators.loc["__lost_nh3", "process_role"] = "shortfall"
    n.generators.loc["__buy_nh3", "process_role"] = "buy_in"
    assert generator_role(n, "__lost_nh3") == ("shortfall", None)
    assert _product(compute_process_view(n), "nh3")["role_flag"] is None
    # A generator tagged by its column books by its role whatever its name.
    n.generators.loc["grid", "process_role"] = ""
    assert generator_role(n, "grid") == ("", None)


def test_a_bad_process_role_is_refused():
    from services.adequacy.eh_columns import coerce_eh_value
    assert coerce_eh_value("Generator", "process_role", " shortfall ") == "shortfall"
    with pytest.raises(ValueError, match="process role"):
        coerce_eh_value("Generator", "process_role", "lost")


def test_linearised_uc_reports_fractional_status_and_starts_unrounded():
    n = _hb_network()
    n.links.loc["hb", "committable"] = True
    sns = n.snapshots
    n.links_t.status = pd.DataFrame({"hb": [1.0, 0.6, 0.0, 0.0, 0.5, 1.0]}, index=sns)
    n.links_t.start_up = pd.DataFrame({"hb": [0.0, 0.0, 0.0, 0.0, 0.5, 0.2]}, index=sns)
    unit = compute_process_view(n)["process_units"][0]
    assert unit["flag"] == "linearised_uc"
    assert unit["running_hours"] == pytest.approx(3.1)
    assert unit["running_hours_basis"] == "status"
    assert unit["starts"] == pytest.approx(0.7)
    assert unit["modelled_hours"] == pytest.approx(6.0)


def test_the_shortfall_is_booked_under_the_product_bus_carrier_whatever_the_slack_carrier():
    n = _hb_network(slack_carrier="shortfall")
    econ = _econ(n)
    assert "shortfall" not in econ.by_carrier
    lost_mwh = float(n.generators_t.p["__lost_nh3"].sum())
    assert econ.by_carrier["nh3"].lost_production_meur.total == pytest.approx(
        lost_mwh * LOST_EUR_PER_T / MWH_PER_T / 1e6)
    assert econ.by_carrier["nh3"].lost_production_flag is None


def test_a_nan_in_the_process_unit_flow_is_flagged_not_read_as_off():
    import json
    n = _hb_network()
    n.links_t.p0.loc[n.snapshots[0], "hb"] = float("nan")
    view = compute_process_view(n)
    json.dumps(view, allow_nan=False)
    unit = view["process_units"][0]
    assert unit["running_hours"] is None and unit["starts"] is None
    assert unit["flag"] == "non_finite_values"


def test_running_hours_split_by_investment_period():
    unit = compute_process_view(_hb_network())["process_units"][0]
    # A flat network has no periods; the split is empty, the total stands alone (as in compare economics).
    assert unit["running_hours_by_period"] == {}


def test_the_shared_role_predicate_reads_the_column_never_the_name():
    from models.process_unit import product_role_generators
    n = _hb_network(roles=False)
    assert list(product_role_generators(n, "shortfall")) == []
    n.generators["process_role"] = ""
    n.generators.loc["__lost_nh3", "process_role"] = "shortfall"
    n.generators.loc["grid", "process_role"] = "buy_in"
    assert list(product_role_generators(n, "shortfall")) == ["__lost_nh3"]
    assert list(product_role_generators(n, "buy_in")) == ["grid"]
    assert list(product_role_generators(n, "lost")) == []
