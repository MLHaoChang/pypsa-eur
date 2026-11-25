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
    # The bypass carries power toward the site (bus0 grid -> bus1 site).
    n = _net()
    n.add("Link", "bypass", bus0="grid", bus1="site", p_nom=5.0, carrier="AC")
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


# ── G2: the site is the eh_critical buses, else the imports' bus1 side ──


def test_an_upstream_component_with_its_own_load_is_still_capped():
    n = _net()
    n.add("Load", "grid_demand", bus="grid", p_set=50.0)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998)


@pytest.mark.parametrize("critical", [True, False])
def test_a_zero_mw_load_upstream_never_makes_a_site(critical):
    # GK A (38) (1): with a flag the site is the flagged buses only; without
    # one it is the imports' bus1 side. A Load never makes a site.
    n = _net(critical=critical)
    n.add("Load", "grid_aux", bus="grid", p_set=0.0)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(40 * 0.99 * 0.998)
    assert _asset(f, "up")["cap"]["links"] == ["poc"]


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
    # 40 MW at the PoC's 0.995 efficiency: what reaches dc_mv.
    assert cap["link_mw"] == pytest.approx(40.0 * 0.995)
    assert cap["credited_mw"] == pytest.approx(
        40.0 * 0.995 * (1 - T.GRID_SUPPLY_FOR) * 0.998
    )


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
    assert _asset(f, "up")["cap"]["reason"] == "capped at grid_import poc 40 MW × 0.998"
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


# ── GK A on #1998 (1): the topology per period ────────────────────────────

CAPPED = 40 * 0.99 * 0.998  # 39.52 MW


def test_a_line_built_later_is_no_path_before_it_exists():
    n, _ = _two_periods(_net())
    n.add("Line", "tie", bus0="grid", bus1="site", x=0.1, r=0.01, s_nom=1000.0)
    n.lines.at["tie", "build_year"] = 2035
    n.lines.at["tie", "lifetime"] = 100
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == pytest.approx(CAPPED)
    assert _asset(f, "up", 2030)["cap"]["links"] == ["poc"]
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(99.0)
    assert "cap" not in _asset(f, "up", 2035)


def test_a_line_retired_early_is_no_path_after_it_goes():
    n, _ = _two_periods(_net())
    n.add("Line", "tie", bus0="grid", bus1="site", x=0.1, r=0.01, s_nom=1000.0)
    n.lines.at["tie", "build_year"] = 2025
    n.lines.at["tie", "lifetime"] = 7  # gone in 2032
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == pytest.approx(99.0)
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(CAPPED)


def test_a_transformer_built_later_is_no_path_before_it_exists():
    n, _ = _two_periods(_net())
    n.add("Transformer", "tx", bus0="grid", bus1="site", x=0.1, s_nom=1000.0)
    n.transformers.at["tx", "build_year"] = 2035
    n.transformers.at["tx", "lifetime"] = 100
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == pytest.approx(CAPPED)
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(99.0)


def test_a_plain_link_built_later_is_no_path_before_it_exists():
    n, _ = _two_periods(_net())
    n.add("Link", "bypass", bus0="grid", bus1="site", p_nom=5.0, carrier="AC")
    n.links.at["bypass", "build_year"] = 2035
    n.links.at["bypass", "lifetime"] = 100
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == pytest.approx(CAPPED)
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(99.0)


# ── GK A on #1998 (2): imports in series ──────────────────────────────────


def _series(c1=40.0, q1=0.002, c2=30.0, q2=0.01):
    """Grid -l1-> mid -l2-> site, both tagged grid_import."""
    n = _net(links=())
    n.add("Bus", "mid", carrier="AC")
    n.add("Link", "l1", bus0="grid", bus1="mid", p_nom=c1, carrier="AC")
    n.add("Link", "l2", bus0="mid", bus1="site", p_nom=c2, carrier="AC")
    _outage(n, "links", "l1", q1)
    _outage(n, "links", "l2", q2)
    n.links["eh_role"] = "grid_import"
    return n


def test_a_chain_of_imports_is_capped_at_its_tightest_link():
    f = reserve_margin_facts(_series(), _cfg())
    credit = min(100.0, 40.0, 30.0) * 0.99 * 0.998 * 0.99
    assert _per(f)["firm_fixed_mw"] == pytest.approx(credit)
    cap = _asset(f, "up")["cap"]
    assert cap["links"] == ["l1", "l2"]
    assert cap["link_mw"] == pytest.approx(30.0)
    assert cap["link_availability"] == pytest.approx(0.998 * 0.99)
    assert cap["credited_mw"] == pytest.approx(credit)


def test_the_tightest_link_of_a_chain_may_be_the_first():
    f = reserve_margin_facts(_series(c1=20.0, c2=60.0), _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(20.0 * 0.99 * 0.998 * 0.99)
    assert _asset(f, "up")["cap"]["link_mw"] == pytest.approx(20.0)


def test_a_member_between_two_imports_shares_the_last_link():
    n = _series()
    n.add("Generator", "mid_g", bus="mid", carrier="gas", p_nom=20.0)
    _outage(n, "generators", "mid_g", 0.05)
    f = reserve_margin_facts(n, _cfg())
    # up arrives at mid as min(100, 40) at 0.99 x 0.998; mid_g adds 20 at 0.95.
    u = 40.0 + 20.0
    du = 40.0 * 0.99 * 0.998 + 20.0 * 0.95
    credit = min(u, 30.0) * (du / u) * 0.99
    assert _per(f)["firm_fixed_mw"] == pytest.approx(credit)
    mid = _asset(f, "mid_g")["cap"]
    assert mid["links"] == ["l2"] and mid["link_mw"] == pytest.approx(30.0)
    assert mid["credited_mw"] == pytest.approx(credit)
    assert _asset(f, "up")["cap"]["credited_mw"] == pytest.approx(credit)


def test_a_chain_whose_first_link_is_not_built_credits_nothing_upstream():
    n, _ = _two_periods(_series())
    n.links.at["l1", "build_year"] = 2035
    n.links.at["l1", "lifetime"] = 100
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == 0.0
    assert "no import capacity in period 2030" in _asset(f, "up", 2030)["cap"]["reason"]
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(30 * 0.99 * 0.998 * 0.99)


# ── GK A on #1998 (3): the direction and availability of a path ───────────


@pytest.mark.parametrize(
    "attrs, uncapped",
    [
        ({"bus0": "site", "bus1": "grid"}, False),  # export only
        ({"bus0": "site", "bus1": "grid", "p_min_pu": -1.0}, True),
        ({"bus0": "grid", "bus1": "site", "p_max_pu": 0.0}, False),
        ({"bus0": "grid", "bus1": "site", "p_max_pu": 0.0, "p_min_pu": -1.0}, False),
    ],
)
def test_a_plain_link_is_a_path_only_toward_the_site(attrs, uncapped):
    n = _net()
    n.add("Link", "bypass", p_nom=5.0, carrier="AC", **attrs)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(99.0 if uncapped else CAPPED)


def test_a_plain_link_that_is_shut_for_an_hour_is_no_path():
    n = _net()
    n.add("Link", "bypass", bus0="grid", bus1="site", p_nom=5.0, carrier="AC")
    n.links_t.p_max_pu["bypass"] = [1.0, 0.0, 1.0, 1.0]
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(CAPPED)


def test_a_normally_open_import_credits_nothing_and_says_why():
    n = _net()
    n.links.at["poc", "p_max_pu"] = 0.0
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == 0.0
    cap = _asset(f, "up")["cap"]
    assert cap["link_mw"] == 0.0 and cap["credited_mw"] == 0.0
    assert "no import capacity" in cap["reason"]


def test_an_import_is_credited_at_its_p_max_pu():
    n = _net()
    n.links.at["poc", "p_max_pu"] = 0.5
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(20 * 0.99 * 0.998)
    assert _asset(f, "up")["cap"]["link_mw"] == pytest.approx(20.0)


def test_a_time_varying_import_rating_is_taken_at_its_period_minimum():
    n, _ = _two_periods(_net())
    n.links_t.p_max_pu["poc"] = [1.0, 0.5, 0.75, 1.0, 1.0, 0.25, 1.0, 1.0]
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == pytest.approx(20 * 0.99 * 0.998)
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(10 * 0.99 * 0.998)
    reason = _asset(f, "up", 2035)["cap"]["reason"]
    assert "lowest p_max_pu in the period" in reason


def test_a_static_import_rating_does_not_mention_a_minimum():
    f = reserve_margin_facts(_net(), _cfg())
    assert "lowest p_max_pu" not in _asset(f, "up")["cap"]["reason"]


# ── GK A on #1998 (4): an extendable import is refused by name ────────────


def _extendable_import():
    n = _net()
    n.links.at["poc", "p_nom_extendable"] = True
    n.links.at["poc", "p_nom_min"] = 0.0
    n.links.at["poc", "p_nom_max"] = 100.0
    n.links.at["poc", "capital_cost"] = 1000.0
    return n


IMPORT_EXT = "reserve_margin_import_link_extendable"


def test_an_extendable_import_is_refused_by_name():
    f = reserve_margin_facts(_extendable_import(), _cfg())
    assert f["import_link_extendable"] == ["poc"]
    issues = [
        i
        for i in _check_reserve_margin(_extendable_import(), _cfg())
        if i.code == IMPORT_EXT
    ]
    assert len(issues) == 1 and issues[0].severity == "error"
    assert "poc" in issues[0].message and "extendable" in issues[0].message


def test_a_fixed_import_is_not_refused():
    f = reserve_margin_facts(_net(), _cfg())
    assert f["import_link_extendable"] == []
    assert not [
        i for i in _check_reserve_margin(_net(), _cfg()) if i.code == IMPORT_EXT
    ]


def test_an_extendable_import_with_nothing_behind_it_is_not_refused():
    n = _extendable_import()
    n.remove("Generator", "up")
    assert reserve_margin_facts(n, _cfg())["import_link_extendable"] == []


def test_the_lp_wrapper_refuses_an_extendable_import():
    n = _extendable_import()
    n.optimize.create_model()
    fn = _wrap_with_reserve_margin(n, None, _cfg(), log_queue=queue.SimpleQueue())
    with pytest.raises(ValueError, match="poc"):
        fn(n, n.snapshots)


def test_the_front_preflight_refuses_an_extendable_import():
    import threading

    from services.adequacy import front as fr

    with pytest.raises(fr.FrontConfigError, match="poc") as exc:
        fr._margin_preflight(
            _extendable_import(), threading.Lock(), _cfg(None), 0.1, "why"
        )
    assert "extendable" in str(exc.value)


def test_the_margin_loop_refuses_an_extendable_import(client, install_network):
    _loop_setup(client, install_network, _extendable_import())
    r = client.post("/api/results/margin_loop", json={"target_lole_h": 1.0})
    assert r.status_code == 422, r.text
    issues = [
        i
        for i in _check_reserve_margin(_extendable_import(), _cfg(0.15))
        if i.code == IMPORT_EXT
    ]
    assert r.json()["detail"] == {
        "error_kind": "unsupported_scope",
        "message": issues[0].message,
    }


def test_the_templates_carry_no_extendable_import():
    from project_templates import eh_templates as T

    for build in T.BUILDERS.values():
        f = reserve_margin_facts(build(), _cfg())
        assert not f["import_link_extendable"]


# ── GK A on #1998 (5): the import's efficiency ────────────────────────────


def test_an_import_is_credited_at_its_delivered_side():
    n = _net()
    n.links.at["poc", "efficiency"] = 0.5
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(20 * 0.99 * 0.998)
    assert _asset(f, "up")["cap"]["link_mw"] == pytest.approx(20.0)


def test_an_energized_import_is_capped_before_its_efficiency():
    n = _net()
    n.links.at["poc", "efficiency"] = 0.5
    n.links.at["poc", "p_max_pu"] = 0.75  # 30 MW sent, 15 MW delivered
    f = reserve_margin_facts(n, _cfg())
    assert _asset(f, "up")["cap"]["link_mw"] == pytest.approx(15.0)


# ── GK A on #1998 (6): the surfaces agree ─────────────────────────────────


def test_a_capped_asset_row_reports_its_share_of_the_cap():
    from services.adequacy.report import reserve_margin_payload

    n = _net(up=(("a", 30.0, 0.01), ("b", 30.0, 0.05)))
    f = reserve_margin_facts(n, _cfg())
    payload = reserve_margin_payload(n, f["stash"])
    rows = {r["name"]: r for r in payload["assets"]}
    credit = _per(f)["firm_fixed_mw"]
    du = 0.99 * 30 + 0.95 * 30
    assert rows["a"]["firm_mw"] == pytest.approx(credit * 0.99 * 30 / du)
    assert rows["b"]["firm_mw"] == pytest.approx(credit * 0.95 * 30 / du)
    assert payload["by_period"][0]["firm_mw"] == pytest.approx(credit)


def test_the_campus_rows_sum_to_its_period_firm():
    from project_templates import eh_templates as T
    from services.adequacy.report import reserve_margin_payload

    for build in (T.build_eh_datacenter, T.build_eh_datacenter_staged):
        n = build()
        f = reserve_margin_facts(n, _cfg())
        payload = reserve_margin_payload(n, f["stash"])
        for per in payload["by_period"]:
            rows = [r for r in payload["assets"] if r["period"] == per["period"]]
            assert sum(r["firm_mw"] for r in rows) == pytest.approx(per["firm_mw"])
            gs = [r for r in rows if r["name"] == "grid_supply"][0]
            assert gs["firm_mw"] < 60.0


def test_a_link_with_an_unusable_rate_is_refused_for_that_reason():
    n = _net()
    n.links.at["poc", "outage_rate_value"] = 1.5
    issues = [
        i
        for i in _check_reserve_margin(n, _cfg())
        if i.code == "reserve_margin_unpriceable_assets"
    ]
    assert len(issues) == 1
    assert "outage rate outside [0, 1)" in issues[0].message
    assert "(no outage data, no availability profile)" not in issues[0].message


# ── GK A on #1998 (7) / GK A (38) (1): which buses are the site ──────────


def test_two_sites_behind_their_own_imports_are_both_sites():
    n = _net()
    n.add("Bus", "site_b", carrier="AC")
    n.buses.at["site_b", "eh_critical"] = True
    n.add("Load", "load_b", bus="site_b", p_set=10.0)
    n.add("Link", "poc_b", bus0="grid", bus1="site_b", p_nom=30.0, carrier="AC")
    _outage(n, "links", "poc_b", 0.01)
    n.links.at["poc_b", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    a_l = (0.998 * 40 + 0.99 * 30) / 70.0
    assert _per(f)["firm_fixed_mw"] == pytest.approx(70.0 * 0.99 * a_l)
    assert _asset(f, "up")["cap"]["links"] == ["poc", "poc_b"]


def test_without_a_flag_each_import_bus1_side_is_a_site():
    # Neither site is flagged eh_critical; each is a site as an import's bus1.
    n = _net(critical=False)
    n.add("Bus", "site_b", carrier="AC")
    n.add("Load", "load_b", bus="site_b", p_set=10.0)
    n.add("Link", "poc_b", bus0="grid", bus1="site_b", p_nom=30.0, carrier="AC")
    n.links.at["poc_b", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _asset(f, "up")["cap"]["links"] == ["poc", "poc_b"]


def test_an_eh_critical_bus_without_a_load_is_still_a_site():
    n = _net()
    n.add("Bus", "site_b", carrier="AC", eh_critical=True)
    n.buses.at["site_b", "eh_critical"] = True
    n.add("Link", "poc_b", bus0="grid", bus1="site_b", p_nom=30.0, carrier="AC")
    n.links.at["poc_b", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _asset(f, "up")["cap"]["links"] == ["poc", "poc_b"]


def test_without_any_load_or_flag_the_link_bus1_side_is_the_site():
    n = _net(critical=False)
    n.remove("Load", "site_load")
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(CAPPED)


# ── edges the second mutation run found unpinned ──────────────────────────


def test_a_bus_fed_one_way_from_upstream_is_an_island():
    # grid -> zfar can only send away from the site: zfar's generator never
    # reaches it, so it keeps its plain credit and joins no group.
    n = _net()
    n.add("Bus", "zfar", carrier="AC")
    n.add("Link", "out", bus0="grid", bus1="zfar", p_nom=50.0, carrier="AC")
    n.add("Generator", "far_g", bus="zfar", carrier="gas", p_nom=10.0)
    _outage(n, "generators", "far_g", 0.05)
    f = reserve_margin_facts(n, _cfg())
    assert "cap" not in _asset(f, "far_g")
    assert _per(f)["firm_fixed_mw"] == pytest.approx(CAPPED + 9.5)


def test_a_one_way_feeder_into_the_upstream_side_joins_its_group():
    n = _net(up=(), links=(("poc", 400.0, 0.002),))
    n.add("Bus", "far", carrier="AC")
    n.add("Link", "feed", bus0="far", bus1="grid", p_nom=50.0, carrier="AC")
    n.add("Generator", "far_g", bus="far", carrier="gas", p_nom=20.0)
    _outage(n, "generators", "far_g", 0.05)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(20 * 0.95 * 0.998)
    assert _asset(f, "far_g")["cap"]["links"] == ["poc"]


def test_buses_joined_by_a_line_are_one_group_through_both_imports():
    n = _net()
    n.add("Bus", "mid", carrier="AC")
    n.add("Line", "gm", bus0="grid", bus1="mid", x=0.1, r=0.01, s_nom=1000.0)
    n.add("Link", "poc_m", bus0="mid", bus1="site", p_nom=30.0, carrier="AC")
    _outage(n, "links", "poc_m", 0.01)
    n.links.at["poc_m", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    a_l = (0.998 * 40 + 0.99 * 30) / 70.0
    assert _per(f)["firm_fixed_mw"] == pytest.approx(70.0 * 0.99 * a_l)
    assert _asset(f, "up")["cap"]["links"] == ["poc", "poc_m"]


def test_a_stage_leading_two_ways_is_credited_through_its_direct_import():
    # grid -poc-> site and grid -l1-> mid -l2-> site: only poc is counted.
    n = _net()
    n.add("Bus", "mid", carrier="AC")
    n.add("Link", "l1", bus0="grid", bus1="mid", p_nom=30.0, carrier="AC")
    n.add("Link", "l2", bus0="mid", bus1="site", p_nom=20.0, carrier="AC")
    n.links.loc[["l1", "l2"], "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(CAPPED)
    cap = _asset(f, "up")["cap"]
    assert cap["links"] == ["poc"]
    assert "leads elsewhere through other paths, which are not counted" in cap["reason"]


def test_a_stage_leading_two_ways_and_none_into_the_site_credits_nothing():
    n = _net(links=())
    for m in ("m1", "m2"):
        n.add("Bus", m, carrier="AC")
        n.add("Link", f"in_{m}", bus0="grid", bus1=m, p_nom=30.0, carrier="AC")
        n.add("Link", f"out_{m}", bus0=m, bus1="site", p_nom=30.0, carrier="AC")
    n.links["eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == 0.0
    cap = _asset(f, "up")["cap"]
    assert cap["credited_mw"] == 0.0 and cap["link_mw"] == 0.0
    assert "no single path to the site" in cap["reason"]


def test_a_grid_import_into_a_non_electrical_bus_is_ignored_upstream():
    n = _net()
    n.add("Carrier", "heat")
    n.add("Bus", "h", carrier="heat")
    n.add("Link", "hx", bus0="grid", bus1="h", p_nom=5.0, carrier="heat")
    n.links.at["hx", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(CAPPED)
    assert _asset(f, "up")["cap"]["links"] == ["poc"]


def test_a_group_of_only_extendables_credits_nothing():
    n = _extendable_upstream()
    n.remove("Generator", "up")
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == 0.0
    assert _asset(f, "up_new")["cap"]["member_credit_mw"] == 0.0


def test_a_flagged_site_ignores_an_unflagged_load_bus_behind_its_own_import():
    # With a flag, an unflagged Load bus is not a site: the import into it is
    # an internal hop of the upstream side, and its own generator is capped.
    n = _net()
    n.add("Bus", "other", carrier="AC")
    n.add("Load", "other_load", bus="other", p_set=10.0)
    n.add("Link", "poc_o", bus0="grid", bus1="other", p_nom=30.0, carrier="AC")
    n.links.at["poc_o", "eh_role"] = "grid_import"
    f = reserve_margin_facts(n, _cfg())
    assert _asset(f, "up")["cap"]["links"] == ["poc"]


# ── GK A (38) (2): a branch with no rating is no path ─────────────────────


@pytest.mark.parametrize(
    "kind, attrs, ts",
    [
        ("Link", {"bus0": "grid", "bus1": "site", "p_nom": 0.0}, None),
        ("Line", {"s_nom": 0.0}, None),
        ("Line", {"s_nom": 100.0, "s_max_pu": 0.0}, None),
        ("Line", {"s_nom": 100.0}, ("lines_t", "s_max_pu", [1.0, 0.0, 1.0, 1.0])),
        ("Transformer", {"s_nom": 0.0}, None),
        ("Transformer", {"s_nom": 100.0, "s_max_pu": 0.0}, None),
        ("Line", {"s_nom": 0.0, "s_nom_extendable": True, "s_nom_max": 0.0}, None),
        (
            "Link",
            {
                "bus0": "grid",
                "bus1": "site",
                "p_nom": 0.0,
                "p_nom_extendable": True,
                "p_nom_max": 0.0,
            },
            None,
        ),
    ],
)
def test_a_branch_rated_zero_is_no_path(kind, attrs, ts):
    n = _net()
    if kind == "Link":
        n.add("Link", "b", carrier="AC", **attrs)
    else:
        n.add(kind, "b", bus0="grid", bus1="site", x=0.1, **attrs)
    if ts is not None:
        getattr(n, ts[0])[ts[1]]["b"] = ts[2]
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(CAPPED)


@pytest.mark.parametrize(
    "kind, attrs",
    [
        ("Line", {"s_nom": 0.0, "s_nom_extendable": True, "s_nom_max": 50.0}),
        ("Transformer", {"s_nom": 0.0, "s_nom_extendable": True, "s_nom_max": 50.0}),
        (
            "Link",
            {
                "bus0": "grid",
                "bus1": "site",
                "p_nom": 0.0,
                "p_nom_extendable": True,
                "p_nom_max": 50.0,
            },
        ),
    ],
)
def test_an_extendable_branch_the_solve_may_build_is_a_path(kind, attrs):
    # GK A (38) (2): an extendable branch counts at its *_nom_max, which the
    # solve may build, so what is behind it keeps plain credit.
    n = _net()
    if kind == "Link":
        n.add("Link", "b", carrier="AC", **attrs)
    else:
        n.add(kind, "b", bus0="grid", bus1="site", x=0.1, **attrs)
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(99.0)


def test_a_branch_whose_rating_cannot_be_read_is_no_path():
    # Fail closed: an unreadable rating is not a rating.
    n = _net()
    n.add("Line", "b", bus0="grid", bus1="site", x=0.1, s_nom=100.0)
    n.lines["s_nom"] = n.lines["s_nom"].astype(object)
    n.lines.at["b", "s_nom"] = "n/a"
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(CAPPED)


def test_an_unflagged_chain_is_capped_at_its_last_link():
    # GK A (38): with no flag, only the chain's end is a site: mid is the
    # bus0 of l2 (active), so it is not, and up is capped at C2 < C1.
    n = _series(c1=40.0, c2=30.0)
    n.buses["eh_critical"] = False
    f = reserve_margin_facts(n, _cfg())
    assert _per(f)["firm_fixed_mw"] == pytest.approx(30.0 * 0.99 * 0.998 * 0.99)
    assert _asset(f, "up")["cap"]["links"] == ["l1", "l2"]


def test_an_unflagged_chain_end_is_judged_per_period():
    # l2 is not built in 2030: mid is then the end of the only active chain
    # hop, so l1 alone caps up; in 2035 the chain runs on to the site.
    n, _ = _two_periods(_series(c1=40.0, c2=30.0))
    n.buses["eh_critical"] = False
    n.links.at["l2", "build_year"] = 2035
    n.links.at["l2", "lifetime"] = 100
    f = reserve_margin_facts(n, _cfg())
    assert _per(f, 2030)["firm_fixed_mw"] == pytest.approx(40.0 * 0.99 * 0.998)
    assert _per(f, 2035)["firm_fixed_mw"] == pytest.approx(30.0 * 0.99 * 0.998 * 0.99)
