"""The reserve margin's point-of-connection cap (E1-E7, G1-G3, option y).

``reserve_margin_facts`` credits firm capacity copper-plate. Generation that
reaches the site only through a ``grid_import`` Link (``eh_role``) cannot
deliver more than that Link carries, so such members are credited as a group:
``min(U, C_L) x A_up x A_L``, per investment period. These tests pin the rule,
its facts, and that the three readers (the preflight, the LP wrapper and the
margin loop) all see the capped number.
"""

from __future__ import annotations

import math
import queue

import pandas as pd
import pypsa
import pytest

from services.solver_service import (
    SolverConfig,
    _wrap_with_reserve_margin,
    reserve_margin_facts,
)
from services.validation_service import _check_reserve_margin

M = 0.1


def _cfg(margin=M):
    return SolverConfig(solver_name="highs", voll=5000.0, reserve_margin=margin)


def _outage(n, comp, name, rate, mttr=10.0):
    df = getattr(n, comp)
    df.at[name, "outage_rate_value"] = rate
    df.at[name, "outage_rate_basis"] = "FOR"
    df.at[name, "mttr_hours"] = mttr


def _net(
    site_load=30.0,
    up=(("up", 100.0, 0.01),),
    links=(("poc", 40.0, 0.002),),
    critical=True,
    hours=4,
):
    """The grid bus feeds the site through grid_import links; generators sit upstream."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=hours, freq="h"))
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "site", carrier="AC")
    n.add("Load", "site_load", bus="site", p_set=site_load)
    for name, p_nom, q in up:
        n.add("Generator", name, bus="grid", carrier="gas", p_nom=p_nom)
        _outage(n, "generators", name, q)
    for name, p_nom, q in links:
        n.add("Link", name, bus0="grid", bus1="site", p_nom=p_nom, carrier="AC")
        if q is not None:
            _outage(n, "links", name, q)
    n.buses["eh_critical"] = False
    if critical:
        n.buses.at["site", "eh_critical"] = True
    n.links["eh_role"] = ""
    for name, _p, _q in links:
        n.links.at[name, "eh_role"] = "grid_import"
    return n


def _per(facts, P="ALL"):
    return facts["stash"]["periods"][str(P)]


def _asset(facts, name, P="ALL"):
    rows = [
        a
        for a in facts["stash"]["assets"]
        if a["name"] == name and a["period"] == str(P)
    ]
    assert len(rows) == 1, rows
    return rows[0]


# ── E1: one generator behind one Link ─────────────────────────────────────


def test_a_single_generator_is_capped_at_the_link():
    f = reserve_margin_facts(_net(), _cfg())
    credit = 40.0 * 0.99 * 0.998
    assert _per(f)["firm_fixed_mw"] == pytest.approx(credit)
    assert _per(f)["max_achievable_mw"] == pytest.approx(credit)


def test_a_link_larger_than_the_fleet_passes_the_fleet_times_the_link():
    f = reserve_margin_facts(_net(links=(("poc", 400.0, 0.002),)), _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(100.0 * 0.99 * 0.998)


# ── E2: groups, not members; parallel links ───────────────────────────────


def test_two_generators_behind_one_link_are_capped_as_a_group():
    f = reserve_margin_facts(
        _net(up=(("a", 30.0, 0.01), ("b", 30.0, 0.05))),
        _cfg(),
    )
    a_up = (0.99 * 30 + 0.95 * 30) / 60.0
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40.0 * a_up * 0.998)
    # Each capped alone would be 30 MW apiece: far above the link.
    assert _per(f)["firm_fixed_mw"] < (0.99 * 30 + 0.95 * 30) * 0.998


def test_two_parallel_links_each_carry_their_own_derate():
    f = reserve_margin_facts(
        _net(links=(("poc_a", 20.0, 0.002), ("poc_b", 30.0, 0.01))),
        _cfg(),
    )
    a_l = (0.998 * 20 + 0.99 * 30) / 50.0
    assert _per(f)["firm_fixed_mw"] == pytest.approx(50.0 * 0.99 * a_l)
    cap = _asset(f, "up")["cap"]
    assert cap["links"] == ["poc_a", "poc_b"]
    assert cap["link_mw"] == pytest.approx(50.0)
    assert cap["link_availability"] == pytest.approx(a_l)


# ── E3: topology decides ──────────────────────────────────────────────────


def test_a_generator_with_a_line_path_to_the_site_stays_uncapped():
    n = _net()
    n.add("Line", "tie", bus0="grid", bus1="site", x=0.1, r=0.01, s_nom=1000.0)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(99.0)
    assert "cap" not in _asset(f, "up")


def test_a_generator_with_a_plain_link_path_stays_uncapped():
    n = _net()
    n.add("Link", "bypass", bus0="site", bus1="grid", p_nom=5.0, carrier="AC")
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(99.0)


def test_an_isolated_generator_is_credited_as_today():
    n = _net()
    n.add("Bus", "island", carrier="AC")
    n.add("Generator", "lone", bus="island", carrier="gas", p_nom=10.0)
    _outage(n, "generators", "lone", 0.05)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998 + 9.5)
    assert "cap" not in _asset(f, "lone")


def test_a_site_member_keeps_its_plain_credit():
    n = _net()
    n.add("Generator", "genset", bus="site", carrier="gas", p_nom=10.0)
    _outage(n, "generators", "genset", 0.03)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998 + 9.7)
    assert "cap" not in _asset(f, "genset")


# ── G2: loads upstream; no eh_critical marker ─────────────────────────────


def test_an_upstream_component_with_its_own_load_is_still_capped():
    n = _net()
    n.add("Load", "grid_demand", bus="grid", p_set=50.0)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998)


@pytest.mark.parametrize("drop_column", [False, True])
def test_without_an_eh_critical_bus_the_link_bus1_side_is_the_site(drop_column):
    n = _net(critical=False)
    if drop_column:
        n.buses = n.buses.drop(columns="eh_critical")
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998)


# ── G1: a link without outage data ────────────────────────────────────────


def test_a_link_without_outage_data_is_taken_as_available_and_says_so():
    f = reserve_margin_facts(_net(links=(("poc", 40.0, None),)), _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40 * 0.99)
    cap = _asset(f, "up")["cap"]
    assert cap["link_availability"] == pytest.approx(1.0)
    assert "link availability taken as 1: no outage data" in cap["reason"]


def test_a_link_with_an_unusable_rate_is_unpriceable():
    n = _net()
    n.links.at["poc", "outage_rate_value"] = 1.5
    f = reserve_margin_facts(n, _cfg())
    assert "poc" in f["unpriceable"]


# ── E5: the facts ─────────────────────────────────────────────────────────


def test_each_capped_member_carries_the_cap_and_keeps_its_capacity():
    f = reserve_margin_facts(
        _net(up=(("a", 30.0, 0.01), ("b", 30.0, 0.05))),
        _cfg(),
    )
    credit = 40.0 * ((0.99 * 30 + 0.95 * 30) / 60.0) * 0.998
    for g in ("a", "b"):
        row = _asset(f, g)
        assert row["capacity_mw"] == 30.0
        cap = row["cap"]
        assert cap["links"] == ["poc"]
        assert cap["link_mw"] == pytest.approx(40.0)
        assert cap["link_availability"] == pytest.approx(0.998)
        assert cap["group_mw"] == pytest.approx(60.0)
        assert cap["credited_mw"] == pytest.approx(credit)
        assert cap["reason"] == "capped at grid_import poc 40 MW × 0.998"


# ── E4 / G3: per period ───────────────────────────────────────────────────


def _two_periods(n):
    snaps = n.snapshots
    n.set_investment_periods([2030, 2035])
    return n, snaps


def test_a_period_without_the_link_credits_nothing_and_says_why():
    n, _ = _two_periods(_net())
    n.links.at["poc", "build_year"] = 2035
    n.links.at["poc", "lifetime"] = 100
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == 0.0
    assert _per(f, 2030)["max_achievable_mw"] == 0.0
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998)
    cap = _asset(f, "up", 2030)["cap"]
    assert cap["credited_mw"] == 0.0 and cap["link_mw"] == 0.0
    assert "no import capacity in period 2030" in cap["reason"]


def test_a_zero_rated_link_credits_nothing_and_says_why():
    f = reserve_margin_facts(_net(links=(("poc", 0.0, 0.002),)), _cfg())
    assert _per(f)["firm_fixed_mw"] == 0.0
    assert "no import capacity" in _asset(f, "up")["cap"]["reason"]


def test_the_staged_campus_caps_each_period_at_its_own_import():
    from project_templates import eh_templates as T

    n = T.build_eh_datacenter_staged()
    f = reserve_margin_facts(n, _cfg())
    periods = [int(p) for p in f["stash"]["periods"]]
    assert periods == sorted(T.DC_ENERGIZATION_MW)
    a_l = 1.0 - 0.002
    a_up = 1.0 - T.GRID_SUPPLY_FOR
    for P in periods:
        cap = _asset(f, "grid_supply", P)["cap"]
        c_l = min(T._DC_POC_MW, T.DC_ENERGIZATION_MW[P])
        assert cap["link_mw"] == pytest.approx(c_l)
        assert cap["credited_mw"] == pytest.approx(c_l * a_up * a_l)
    assert _asset(f, "grid_supply", 2027)["cap"]["link_mw"] == 15.0
    # With the PoC gone in the first period, nothing comes through then.
    n.links.at["grid_import", "build_year"] = 2030
    n.links.at["grid_import", "lifetime"] = 100
    f2 = reserve_margin_facts(n, _cfg())
    cap = _asset(f2, "grid_supply", 2027)["cap"]
    assert cap["credited_mw"] == 0.0
    assert "no import capacity in period 2027" in cap["reason"]
    drop = _per(f, 2027)["firm_fixed_mw"] - _per(f2, 2027)["firm_fixed_mw"]
    assert drop == pytest.approx(15.0 * a_up * a_l)


def test_the_single_year_campus_no_longer_credits_the_grid_beyond_the_poc():
    from project_templates import eh_templates as T

    f = reserve_margin_facts(T.build_eh_datacenter(), _cfg())
    cap = _asset(f, "grid_supply")["cap"]
    assert cap["link_mw"] == 40.0
    assert cap["credited_mw"] == pytest.approx(40.0 * (1 - T.GRID_SUPPLY_FOR) * 0.998)


# ── option (y): an extendable behind the import is refused by name ────────


def _extendable_upstream():
    n = _net()
    n.add(
        "Generator",
        "up_new",
        bus="grid",
        carrier="gas",
        p_nom_extendable=True,
        p_nom_max=100.0,
        capital_cost=1000.0,
    )
    _outage(n, "generators", "up_new", 0.05)
    return n


def test_an_extendable_behind_the_import_is_refused_by_name():
    f = reserve_margin_facts(_extendable_upstream(), _cfg())
    assert f["import_group_extendable"] == [{"members": ["up_new"], "links": ["poc"]}]
    # ...and never credited, in the LP terms or the ceiling.
    assert all(m["name"] != "up_new" for m, _d in f["terms"]["ALL"])
    assert _per(f)["max_achievable_mw"] == pytest.approx(40 * 0.99 * 0.998)
    issues = [
        i
        for i in _check_reserve_margin(_extendable_upstream(), _cfg())
        if i.code == "reserve_margin_import_group_extendable"
    ]
    assert len(issues) == 1 and issues[0].severity == "error"
    assert "up_new" in issues[0].message and "poc" in issues[0].message


def test_the_lp_wrapper_refuses_an_extendable_behind_the_import():
    n = _extendable_upstream()
    n.optimize.create_model()
    fn = _wrap_with_reserve_margin(n, None, _cfg(), log_queue=queue.SimpleQueue())
    with pytest.raises(ValueError, match="up_new"):
        fn(n, n.snapshots)


def test_the_templates_carry_no_extendable_behind_their_import():
    from project_templates import eh_templates as T

    for build in (T.build_eh_datacenter, T.build_eh_datacenter_staged):
        assert not reserve_margin_facts(build(), _cfg())["import_group_extendable"]


# ── E6: one test per reader ───────────────────────────────────────────────


def test_the_preflight_passes_and_fails_at_the_capped_margin():
    credit = 40.0 * 0.99 * 0.998  # 39.52 MW
    ok = _check_reserve_margin(_net(site_load=35.0), _cfg())  # needs 38.5
    assert not [i for i in ok if i.code == "reserve_margin_unreachable"]
    bad = _check_reserve_margin(_net(site_load=37.0), _cfg())  # needs 40.7
    errs = [i for i in bad if i.code == "reserve_margin_unreachable"]
    assert len(errs) == 1, [i.code for i in bad]
    assert f"{credit:,.1f} MW" in errs[0].message


def test_the_lp_constraint_builds_on_the_capped_constant():
    n = _net(site_load=50.0)
    n.add(
        "Generator",
        "peaker",
        bus="site",
        carrier="gas",
        p_nom_extendable=True,
        p_nom_max=100.0,
        capital_cost=1000.0,
        marginal_cost=50.0,
    )
    _outage(n, "generators", "peaker", 0.05)
    cfg = _cfg()
    fn = _wrap_with_reserve_margin(n, None, cfg, log_queue=queue.SimpleQueue())
    n.optimize(solver_name="highs", extra_functionality=fn)
    st = n._reserve_margin_targets
    firm = 40.0 * 0.99 * 0.998
    assert st["periods"]["ALL"]["firm_fixed_mw"] == pytest.approx(firm)
    need = (55.0 - firm) / 0.95
    assert float(n.generators.at["peaker", "p_nom_opt"]) == pytest.approx(
        need, rel=1e-6
    )


def _loop_setup(client, install_network, network):
    install_network(network)
    r = client.put(
        "/api/simulation/solver_config",
        json={"solver_name": "highs", "voll": 3000.0},
    )
    assert r.status_code == 200, r.text


def test_the_margin_loop_refuses_with_the_validators_sentence(client, install_network):
    _loop_setup(client, install_network, _extendable_upstream())
    r = client.post("/api/results/margin_loop", json={"target_lole_h": 1.0})
    assert r.status_code == 422, r.text
    issues = [
        i
        for i in _check_reserve_margin(_extendable_upstream(), _cfg(0.15))
        if i.code == "reserve_margin_import_group_extendable"
    ]
    assert r.json()["detail"] == {
        "error_kind": "unsupported_scope",
        "message": issues[0].message,
    }


def test_the_margin_loop_ceiling_reads_the_capped_fleet(client, install_network):
    # 39.5 MW of capped firm capacity against a 50 MW peak: no margin at all
    # is reachable, which the copper plate (99 MW) would have hidden.
    _loop_setup(client, install_network, _net(site_load=50.0))
    r = client.post("/api/results/margin_loop", json={"target_lole_h": 1.0})
    assert r.status_code == 422, r.text
    assert "%" in str(r.json()["detail"])


# ── no grid_import Link: bit-identical to before ──────────────────────────


def _plain():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "battery")
    for b in ("a", "b", "c"):
        n.add("Bus", b, carrier="AC")
    n.add("Line", "ab", bus0="a", bus1="b", x=0.1, r=0.01, s_nom=100.0)
    n.add("Link", "bc", bus0="b", bus1="c", p_nom=20.0, carrier="AC")
    n.add("Load", "l", bus="c", p_set=[30.0, 50.0, 45.0, 40.0])
    n.add("Generator", "g1", bus="a", carrier="gas", p_nom=60.0)
    _outage(n, "generators", "g1", 0.05)
    n.add(
        "Generator",
        "g2",
        bus="b",
        carrier="gas",
        p_nom_extendable=True,
        p_nom_max=40.0,
    )
    _outage(n, "generators", "g2", 0.03)
    n.add("StorageUnit", "s", bus="c", carrier="battery", p_nom=10.0, max_hours=2.0)
    _outage(n, "storage_units", "s", 0.02)
    n.links["eh_role"] = ""
    return n


#: Computed by ``reserve_margin_facts`` on the parent commit (no cap).
_PLAIN_BEFORE = {
    "periods": {
        "ALL": {
            "peak_mw": 50.0,
            "required_mw": 55.00000000000001,
            "firm_fixed_mw": 61.9,
            "max_achievable_mw": 100.7,
            "n_peak_hours": 1,
        }
    },
    "assets": [
        ("g1", 60.0, 0.95),
        ("g2", None, 0.97),
        ("s", 10.0, 0.49),
    ],
    "terms": [("g2", 0.97)],
}


def test_a_network_without_a_grid_import_link_is_unchanged():
    f = reserve_margin_facts(_plain(), _cfg())
    per = _per(f)
    for k, v in _PLAIN_BEFORE["periods"]["ALL"].items():
        assert per[k] == v, k
    assert [
        (a["name"], a["capacity_mw"], a["derate"]) for a in f["stash"]["assets"]
    ] == _PLAIN_BEFORE["assets"]
    assert all("cap" not in a for a in f["stash"]["assets"])
    assert [(m["name"], d) for m, d in f["terms"]["ALL"]] == _PLAIN_BEFORE["terms"]
    assert f["unpriceable"] == [] and f["import_group_extendable"] == []
    assert math.isfinite(per["firm_fixed_mw"])


# ── edges the mutation run found unpinned (E7) ────────────────────────────


def test_a_network_without_an_eh_role_column_is_unchanged():
    n = _plain()
    n.links = n.links.drop(columns="eh_role")
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == _PLAIN_BEFORE["periods"]["ALL"]["firm_fixed_mw"]


@pytest.mark.parametrize("end", ["bus0", "bus1"])
def test_a_grid_import_tag_on_a_non_electrical_link_is_ignored(end):
    n = _plain()
    n.add("Carrier", "heat")
    n.add("Bus", "h", carrier="heat")
    ends = {"bus0": "b", "bus1": "c"}
    ends[end] = "h"
    n.add("Link", "hx", p_nom=5.0, carrier="heat", **ends)
    n.links.at["hx", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == _PLAIN_BEFORE["periods"]["ALL"]["firm_fixed_mw"]


def test_a_grid_import_inside_the_upstream_side_caps_nothing():
    # grid -Line- mid -poc-> site, plus a tagged Link grid -> mid that the
    # Line bypasses: only `poc` separates anything.
    n = _net()
    n.add("Bus", "mid", carrier="AC")
    n.links.at["poc", "bus0"] = "mid"
    n.add("Line", "gm", bus0="grid", bus1="mid", x=0.1, r=0.01, s_nom=1000.0)
    n.add("Link", "shadow", bus0="grid", bus1="mid", p_nom=30.0, carrier="AC")
    n.links.at["shadow", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _asset(f, "up")["cap"]["links"] == ["poc"]
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998)


def test_a_grid_import_between_two_site_components_caps_nothing():
    n = _net()
    n.add("Bus", "site2", carrier="AC")
    n.buses.at["site2", "eh_critical"] = True
    n.add("Link", "inner", bus0="site", bus1="site2", p_nom=10.0, carrier="AC")
    n.links.at["inner", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _asset(f, "up")["cap"]["links"] == ["poc"]


def test_a_negative_link_rating_counts_as_zero():
    f = reserve_margin_facts(_net(links=(("poc", -5.0, 0.002),)), _cfg())
    cap = _asset(f, "up")["cap"]
    assert cap["link_mw"] == 0.0 and cap["credited_mw"] == 0.0


def test_a_period_with_nothing_built_upstream_credits_nothing():
    n, _ = _two_periods(_net())
    n.generators.at["up", "build_year"] = 2035
    n.generators.at["up", "lifetime"] = 100
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == 0.0
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998)


def test_link_rate_bounds_are_zero_inclusive_and_one_exclusive():
    f0 = reserve_margin_facts(_net(links=(("poc", 40.0, 0.0),)), _cfg())
    assert "poc" not in f0["unpriceable"]
    assert _per(f0)["firm_fixed_mw"] == pytest.approx(40 * 0.99)
    f1 = reserve_margin_facts(_net(links=(("poc", 40.0, 1.0),)), _cfg())
    assert "poc" in f1["unpriceable"]
    assert _per(f1)["firm_fixed_mw"] == 0.0
