"""
``suggest_eh_setup`` (guided-mode spec §6.3): a READ tool that proposes the
Energy Hub tags an untagged network needs — the grid import Link, the
point-of-connection bus, the critical buses — as ready-to-run
``update_component`` / ``bulk_update_components`` actions, and lists the
thermal-like units without outage data as questions for the user.

It never applies anything: the assistant presents the suggestions and the
write tools ask the user to confirm.

The recovery tests compare against each template builder's OWN tagging, read
from an untouched build — no literal bus or link names — so a template that
changes its tags keeps this test honest.
"""
from __future__ import annotations

import copy
import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import chat_service
from services import chat_tools as T
from services.adequacy.eh_setup import suggest_eh_setup
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, _SERVICE_CALL
from tests._tool_actions import _validate_action, _validate_eh_tag_action

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as TPL  # noqa: E402

TEMPLATES = sorted(TPL.BUILDERS)


def _strip(n: pypsa.Network) -> pypsa.Network:
    n.links["eh_role"] = ""
    n.buses["eh_poc"] = False
    n.buses["eh_critical"] = False
    return n


def _builder_tags(n: pypsa.Network) -> dict[str, set[str]]:
    return {
        "import_link": set(n.links.index[n.links["eh_role"] == "grid_import"]),
        "poc_bus": set(n.buses.index[n.buses["eh_poc"].astype(bool)]),
        "critical_bus": set(n.buses.index[n.buses["eh_critical"].astype(bool)]),
    }


def _proposed(out: dict) -> dict[str, set[str]]:
    got: dict[str, set[str]] = {"import_link": set(), "poc_bus": set(),
                                "critical_bus": set()}
    for s in out["suggestions"]:
        if s["kind"] in got:
            got[s["kind"]].add(s["name"])
    return got


def _apply(n: pypsa.Network, actions: list[dict]) -> None:
    """What the confirmed write tools would do (test-side, on a copy)."""
    attr = {"Bus": "buses", "Link": "links"}
    for a in actions:
        args = a["args"]
        df = getattr(n, attr[args["component_class"]])
        if a["tool"] == "update_component":
            for col, v in args["attrs"].items():
                df.at[args["name"], col] = v
        else:
            for name in args["names"]:
                for col, v in args["updates"].items():
                    df.at[name, col] = v


def _frames(n: pypsa.Network) -> dict[str, pd.DataFrame]:
    return {c: getattr(n, c).copy(deep=True)
            for c in ("buses", "links", "generators", "loads", "storage_units")}


# ── recovery on the three templates ─────────────────────────────────────────


@pytest.mark.parametrize("tid", TEMPLATES)
def test_recovers_the_builders_tags_on_a_stripped_template(tid):
    want = _builder_tags(TPL.BUILDERS[tid]())
    assert all(want.values()), want          # every template tags all three
    n = _strip(TPL.BUILDERS[tid]())
    out = suggest_eh_setup(n)
    assert out["status"] == "ok"
    assert _proposed(out) == want
    assert out["already"] == {"import_links": [], "poc_buses": [], "critical_buses": []}


@pytest.mark.parametrize("tid", TEMPLATES)
def test_applying_the_actions_reproduces_the_builders_tagging(tid):
    built = TPL.BUILDERS[tid]()
    n = _strip(TPL.BUILDERS[tid]())
    out = suggest_eh_setup(n)
    tagged = copy.deepcopy(n)
    _apply(tagged, out["actions"])
    # grid_import on the import link(s); the other roles were stripped and are
    # not this tool's business (conversion roles are a user decision).
    assert (set(tagged.links.index[tagged.links["eh_role"] == "grid_import"])
            == set(built.links.index[built.links["eh_role"] == "grid_import"]))
    pd.testing.assert_series_equal(tagged.buses["eh_poc"].astype(bool),
                                   built.buses["eh_poc"].astype(bool))
    pd.testing.assert_series_equal(tagged.buses["eh_critical"].astype(bool),
                                   built.buses["eh_critical"].astype(bool))


@pytest.mark.parametrize("tid", TEMPLATES)
def test_every_action_validates_against_its_tool_schema(tid):
    out = suggest_eh_setup(_strip(TPL.BUILDERS[tid]()))
    assert out["actions"]
    for a in out["actions"]:
        _validate_action(a)
        _validate_eh_tag_action(a)
        assert isinstance(a["effect"], str) and a["effect"]
    for s in out["suggestions"]:
        if s["action"] is not None:
            _validate_eh_tag_action(s["action"])
            assert s["reason"] and s["confidence"] in ("high", "medium", "low")


@pytest.mark.parametrize("tid", TEMPLATES)
def test_the_network_is_untouched(tid):
    n = _strip(TPL.BUILDERS[tid]())
    before = _frames(n)
    loads_t = n.loads_t.p_set.copy(deep=True)
    suggest_eh_setup(n)
    suggest_eh_setup(n, archetype="off_grid")
    for name, df in _frames(n).items():
        pd.testing.assert_frame_equal(df, before[name], check_like=False)
    pd.testing.assert_frame_equal(n.loads_t.p_set, loads_t)


def test_a_tagged_template_needs_nothing_and_reports_what_is_there():
    n = TPL.build_eh_datacenter()
    want = _builder_tags(n)
    out = suggest_eh_setup(n)
    assert _proposed(out) == {"import_link": set(), "poc_bus": set(),
                              "critical_bus": set()}
    assert out["actions"] == []
    assert set(out["already"]["import_links"]) == want["import_link"]
    assert set(out["already"]["poc_buses"]) == want["poc_bus"]
    assert set(out["already"]["critical_buses"]) == want["critical_bus"]


def test_confidence_on_the_datacenter():
    out = suggest_eh_setup(_strip(TPL.build_eh_datacenter()))
    conf = {s["kind"]: s["confidence"] for s in out["suggestions"]
            if s["kind"] != "outage_data"}
    # name-matched import link, a PoC behind it, a name-matched critical bus
    assert conf == {"import_link": "high", "poc_bus": "high", "critical_bus": "high"}


def test_counts_the_network():
    n = TPL.build_eh_h2_hub()
    out = suggest_eh_setup(n)
    assert out["network"] == {"buses": len(n.buses), "links": len(n.links),
                              "loads": len(n.loads), "generators": len(n.generators)}


# ── bulk collapse, fallbacks, no network ────────────────────────────────────


def _two_critical() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(4))
    for b in ("utility", "site", "hospital_a", "clinic_b"):
        n.add("Bus", b, carrier="AC")
    n.add("Generator", "utility_supply", bus="utility", p_nom=100.0, marginal_cost=50.0)
    n.add("Link", "tie", bus0="utility", bus1="site", p_nom=50.0)
    n.add("Link", "feeder_a", bus0="site", bus1="hospital_a", p_nom=20.0)
    n.add("Link", "feeder_b", bus0="site", bus1="clinic_b", p_nom=20.0)
    n.add("Load", "ward", bus="hospital_a", p_set=5.0)
    n.add("Load", "surgery", bus="clinic_b", p_set=3.0)
    return n


def test_two_critical_buses_collapse_into_one_bulk_action():
    n = _two_critical()
    out = suggest_eh_setup(n)
    crit = [s for s in out["suggestions"] if s["kind"] == "critical_bus"]
    assert {s["name"] for s in crit} == {"hospital_a", "clinic_b"}
    bulk = [a for a in out["actions"] if a["tool"] == "bulk_update_components"]
    assert len(bulk) == 1
    assert bulk[0]["args"]["component_class"] == "Bus"
    assert sorted(bulk[0]["args"]["names"]) == ["clinic_b", "hospital_a"]
    assert bulk[0]["args"]["updates"] == {"eh_critical": True}
    # the single ones stay single
    singles = {(a["args"]["component_class"], a["args"]["name"])
               for a in out["actions"] if a["tool"] == "update_component"}
    assert singles == {("Link", "tie"), ("Bus", "utility")}
    for a in out["actions"]:
        _validate_eh_tag_action(a)
    # no action is listed twice (the per-suggestion actions are the singles)
    assert len(out["actions"]) == 3


def test_without_a_name_match_the_largest_load_bus_is_critical_medium():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(3))
    for b in ("up", "a", "b"):
        n.add("Bus", b, carrier="AC")
    n.add("Generator", "big", bus="up", p_nom=100.0)
    n.add("Link", "l_a", bus0="up", bus1="a", p_nom=50.0)
    n.add("Link", "l_ab", bus0="a", bus1="b", p_nom=50.0)
    n.add("Load", "small", bus="a", p_set=2.0)
    n.add("Load", "large", bus="b")
    n.loads_t.p_set = pd.DataFrame({"large": [1.0, 9.0, 3.0]}, index=n.snapshots)
    out = suggest_eh_setup(n)
    crit = [s for s in out["suggestions"] if s["kind"] == "critical_bus"]
    assert [(s["name"], s["confidence"]) for s in crit] == [("b", "medium")]


def test_no_import_link_the_biggest_loadless_supply_bus_is_a_low_poc():
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.add("Bus", "gen_bus", carrier="AC")
    n.add("Bus", "load_bus", carrier="AC")
    n.add("Line", "ln", bus0="gen_bus", bus1="load_bus", x=0.1, s_nom=100.0)
    n.add("Generator", "g", bus="gen_bus", p_nom=10.0)
    n.add("Load", "d", bus="load_bus", p_set=5.0)
    out = suggest_eh_setup(n)
    assert _proposed(out)["import_link"] == set()
    poc = [s for s in out["suggestions"] if s["kind"] == "poc_bus"]
    assert [(s["name"], s["confidence"]) for s in poc] == [("gen_bus", "low")]


def test_a_link_with_a_conversion_role_is_never_proposed_as_the_import():
    n = TPL.build_eh_h2_hub()
    n.links["eh_role"] = ""
    n.links.at["electrolyser", "eh_role"] = "electrolyser"
    n.links.at["fuel_cell", "eh_role"] = "fuel_cell"
    out = suggest_eh_setup(n)
    assert "electrolyser" not in _proposed(out)["import_link"]
    assert "fuel_cell" not in _proposed(out)["import_link"]


@pytest.mark.parametrize("net", [None, pypsa.Network()])
def test_no_network(net):
    out = suggest_eh_setup(net)
    assert out["status"] == "no_network"
    assert out["suggestions"] == [] and out["actions"] == []


# ── outage data ─────────────────────────────────────────────────────────────


def test_outage_data_lists_the_datacenter_gensets_without_it():
    n = _strip(TPL.build_eh_datacenter())
    gens = [g for g in n.generators.index if g.startswith("genset_")]
    assert gens
    # NaN alone is not enough: 'gas' has a carrier default that
    # resolve_outage_params lends. Rename the carrier the way the readiness
    # test does (still 'conventional' to _gen_category).
    n.generators.loc[gens, "outage_rate_value"] = np.nan
    n.generators.loc[gens, "carrier"] = "gas_unrated"
    out = suggest_eh_setup(n)
    od = [s for s in out["suggestions"] if s["kind"] == "outage_data"]
    assert {s["name"] for s in od if s["component_class"] == "Generator"} == set(gens)
    for s in od:
        assert s["proposed"] is None and s["action"] is None
        assert "outage_rate_value" in s["reason"] and "mttr_hours" in s["reason"]
    # one note line per unit, no action for any of them
    assert sum(1 for line in out["notes"] if any(g in line for g in gens)) == len(gens)
    assert not any(a["args"].get("name") in gens for a in out["actions"])


def test_outage_data_ignores_slack_and_renewables_and_rated_units():
    n = _strip(TPL.build_eh_microgrid())
    n.add("Generator", "voll_slack", bus="island", carrier="load_slack", p_nom=1e6)
    out = suggest_eh_setup(n)
    od = {s["name"] for s in out["suggestions"] if s["kind"] == "outage_data"}
    assert "voll_slack" not in od
    assert "pv_plant" not in od and "wind_turbines" not in od
    assert not any(d.startswith("diesel_") for d in od)          # rated
    # the proposed import link has no outage data on the microgrid
    assert "subsea_tie" in od


# ── the tool ────────────────────────────────────────────────────────────────


def test_registered_as_a_read_tool_with_a_service_route():
    tool = next(t for t in TOOLS if t["name"] == "suggest_eh_setup")
    assert tool["description"].rstrip().endswith("Safety: read.")
    assert tool["input_schema"]["properties"]["archetype"]["enum"] == [
        "strong_grid", "weak_flexible", "off_grid"]
    assert tool["input_schema"].get("required", []) == []
    assert TOOL_ROUTES["suggest_eh_setup"] == _SERVICE_CALL
    assert chat_service._safety_tier_for("suggest_eh_setup") == "read"


def test_dispatcher_reads_the_live_network_and_leaves_it(install_network):
    n = _strip(TPL.build_eh_microgrid())
    install_network(n)
    from services.pypsa_service import PyPSAService
    live = PyPSAService.get_network()
    before = _frames(live)
    out = T.suggest_eh_setup(archetype="off_grid")
    assert out["status"] == "ok"
    assert _proposed(out) == _builder_tags(TPL.build_eh_microgrid())
    for name, df in _frames(PyPSAService.get_network()).items():
        pd.testing.assert_frame_equal(df, before[name])
