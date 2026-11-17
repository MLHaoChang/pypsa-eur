"""Study harness #46 (GK A N5): a sidecar unit on a mainland bus is refused by name
(``unit_on_mainland``), never read as ``no_tie``; every shipped template reads the tie it read
before."""

from __future__ import annotations

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from services.adequacy import island_config as IC  # noqa: E402
from services.adequacy import island_survival as S  # noqa: E402


def _island(tid):
    return IC.validate(T.ISLAND_CONFIG[tid])


# ── #46 (GK A N5): a sidecar unit on a mainland bus is refused by name, never no_tie ──

_MASTER_TIE_STATES = {
    "eh_datacenter": {
        "state": "closed",
        "ties": ["grid_import"],
        "carrying": ["grid_import"],
    },
    "eh_h2_hub": {
        "state": "closed",
        "ties": ["grid_import", "electrolyser", "fuel_cell"],
        "carrying": ["grid_import", "electrolyser", "fuel_cell"],
    },
    "eh_datacenter_staged": {
        "state": "closed",
        "ties": ["grid_import"],
        "carrying": ["grid_import"],
    },
    "eh_microgrid_staged": {"state": "open", "ties": ["subsea_tie"], "carrying": []},
    "eh_microgrid": {"state": "open", "ties": ["subsea_tie"], "carrying": []},
    "eh_h2_hub_staged": {
        "state": "closed",
        "ties": [
            "grid_import",
            "electrolyser",
            "fuel_cell",
            "electrolyser_new_2027",
            "electrolyser_new_2030",
            "electrolyser_new_2035",
        ],
        "carrying": [
            "grid_import",
            "electrolyser",
            "fuel_cell",
            "electrolyser_new_2027",
            "electrolyser_new_2030",
            "electrolyser_new_2035",
        ],
    },
}


@pytest.mark.parametrize("tid", sorted(_MASTER_TIE_STATES))
def test_every_shipped_template_reads_the_tie_it_read_before_46(tid):
    assert S.tie_state(T.BUILDERS[tid](), _island(tid)) == _MASTER_TIE_STATES[tid]


def test_a_sidecar_genset_on_the_mainland_bus_is_refused_by_name():
    """The issue fixture: before #46 the mainland bus joined the island and the tie read no_tie."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.generators.at["diesel_new", "bus"] = "mainland"
    out = S.tie_state(n, island)
    assert out["state"] == "unit_on_mainland"
    assert (out["units"], out["buses"], out["links"]) == (
        ["diesel_new"],
        ["mainland"],
        ["subsea_tie"],
    )
    for word in (
        "diesel_new",
        "mainland",
        "mainland_supply",
        "subsea_tie",
        "move",
        "list",
    ):
        assert word in out["reason"].lower(), word


@pytest.mark.parametrize("component", ["StorageUnit", "Store"])
def test_a_storage_the_sidecar_does_not_name_marks_its_bus_mainland_too(component):
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add("Bus", "m3")
    # #49: a supply at 0 capacity delivers nothing, so the storage gets some.
    n.add(
        component,
        "m3_store",
        bus="m3",
        **{"p_nom" if component == "StorageUnit" else "e_nom": 1.0},
    )
    n.add("Link", "tie_m3", bus0="m3", bus1="island", p_nom=5.0)
    n.generators.at["diesel_new", "bus"] = "m3"
    out = S.tie_state(n, island)
    assert (out["state"], out["units"], out["buses"]) == (
        "unit_on_mainland",
        ["diesel_new"],
        ["m3"],
    )
    assert "m3_store" in out["reason"]


def test_a_sidecar_unit_on_a_bus_with_no_other_supply_reads_as_before():
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add("Bus", "aux")
    n.add("Load", "aux_load", bus="aux", p_set=0.1)
    n.add("Line", "aux_feeder", bus0="island", bus1="aux", x=0.1, s_nom=5.0)
    n.generators.at["diesel_new", "bus"] = "aux"
    assert S.tie_state(n, island) == {
        "state": "open",
        "ties": ["subsea_tie"],
        "carrying": [],
    }


def test_a_grid_import_link_inside_the_island_is_refused_by_name():
    """R4: the mainland's supply arrives only through a Link, so no supply sits on the mainland bus;
    the grid-import Link with both ends on the island's buses names it."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.remove("Generator", "mainland_supply")
    n.generators.at["diesel_new", "bus"] = "mainland"
    out = S.tie_state(n, island)
    assert out["state"] == "unit_on_mainland"
    assert (out["units"], out["buses"], out["links"]) == (
        ["diesel_new"],
        ["mainland"],
        ["subsea_tie"],
    )
    assert "subsea_tie" in out["reason"] and "diesel_new" in out["reason"]
    # The island's buses are exactly the Link's two ends: still refused.
    n.loads.at["hospital_load", "bus"] = "island"
    assert S.island_buses(n, island) == {"island", "mainland"}
    assert S.tie_state(n, island)["links"] == ["subsea_tie"]


@pytest.mark.parametrize(
    "slack",
    [
        {"name": "hospital_shed", "carrier": "load_shedding"},
        {"name": "__dsr_hospital_load", "carrier": "AC"},
        {"name": "__dsr_hospital_store", "carrier": "AC", "component": "Store"},
        {"name": "__dsr_hospital_su", "carrier": "AC", "component": "StorageUnit"},
    ],
)
def test_an_lp_slack_on_an_island_bus_is_not_a_mainland_supply(slack):
    """GK A S1: a load-shedding or DSR slack generator is not a real asset, so it never marks
    an island bus as the mainland's; the tie reads as on master."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    if slack["carrier"] not in n.carriers.index:
        n.add("Carrier", slack["carrier"])
    component = slack.get("component", "Generator")
    n.add(component, slack["name"], bus="hospital", carrier=slack["carrier"])
    assert S.tie_state(n, island) == _MASTER_TIE_STATES["eh_microgrid"]


# ── #48: a mainland fed through a Link without eh_role == grid_import ──


def _untagged_r4(poc: bool):
    """The R4 fixture with the Link's eh_role cleared: the mainland's supply arrives only through
    subsea_tie, and diesel_new sits on the mainland bus."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.remove("Generator", "mainland_supply")
    n.generators.at["diesel_new", "bus"] = "mainland"
    n.links.at["subsea_tie", "eh_role"] = ""
    n.buses.at["mainland", "eh_poc"] = poc
    return n, island


def test_an_untagged_link_from_the_eh_poc_bus_is_refused_by_name():
    n, island = _untagged_r4(poc=True)
    out = S.tie_state(n, island)
    assert (out["state"], out["units"], out["buses"], out["links"]) == (
        "unit_on_mainland",
        ["diesel_new"],
        ["mainland"],
        ["subsea_tie"],
    )
    for word in ("subsea_tie", "eh_poc", "diesel_new", "mainland"):
        assert word in out["reason"], word


def test_an_eh_poc_link_end_on_bus1_names_bus1_as_the_grid_side():
    n, island = _untagged_r4(poc=False)
    n.links.at["subsea_tie", "bus0"], n.links.at["subsea_tie", "bus1"] = (
        "island",
        "mainland",
    )
    n.buses.at["mainland", "eh_poc"] = True
    out = S.tie_state(n, island)
    assert (out["state"], out["buses"], out["links"]) == (
        "unit_on_mainland",
        ["mainland"],
        ["subsea_tie"],
    )


def test_an_untagged_link_with_no_grid_marker_reads_as_the_islands_own_feeder():
    """#48 decision: with no eh_role and no eh_poc, topology cannot tell a mainland feed from the
    island's own feeder (hospital_feeder, site_transformer), so the Link is not a mainland feed."""
    n, island = _untagged_r4(poc=False)
    assert S.tie_state(n, island) == {"state": "no_tie", "ties": [], "carrying": []}


def test_an_internal_link_with_no_end_on_the_eh_poc_bus_is_not_a_feed():
    """hospital_feeder (island → hospital) stays the island's own while mainland is eh_poc."""
    assert (
        S.tie_state(T.BUILDERS["eh_microgrid"](), _island("eh_microgrid"))["state"]
        == "open"
    )


# ── #49: an unlisted supply that cannot deliver is left out ──


@pytest.mark.parametrize(
    "component,nom",
    [("Generator", "p_nom"), ("StorageUnit", "p_nom"), ("Store", "e_nom")],
)
def test_an_unlisted_supply_that_cannot_deliver_is_left_out(component, nom):
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add(component, "dead_supply", bus="hospital", **{nom: 0.0})
    assert S.tie_state(n, island) == _MASTER_TIE_STATES["eh_microgrid"]


@pytest.mark.parametrize(
    "component,nom",
    [("Generator", "p_nom"), ("StorageUnit", "p_nom"), ("Store", "e_nom")],
)
def test_an_unlisted_supply_that_can_deliver_is_still_refused(component, nom):
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add(component, "live_supply", bus="hospital", **{nom: 0.5})
    out = S.tie_state(n, island)
    assert (out["state"], out["buses"]) == ("unit_on_mainland", ["hospital"])
    assert "live_supply" in out["reason"]


@pytest.mark.parametrize(
    "component,nom",
    [("Generator", "p_nom"), ("StorageUnit", "p_nom"), ("Store", "e_nom")],
)
@pytest.mark.parametrize("nom_max,refused", [(5.0, True), (0.0, False)])
def test_an_extendable_unlisted_supply_is_read_by_its_max(
    component, nom, nom_max, refused
):
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add(
        component,
        "ext_supply",
        bus="hospital",
        **{nom: 0.0, f"{nom}_extendable": True, f"{nom}_max": nom_max},
    )
    state = S.tie_state(n, island)["state"]
    assert (state == "unit_on_mainland") is refused


def test_the_issue_fixture_with_a_zero_capacity_mainland_supply_reads_the_link_only():
    """#49 on the #46 fixture: mainland_supply at 0 MW names no supply; the grid-import Link
    still marks the bus."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.generators.at["mainland_supply", "p_nom"] = 0.0
    n.generators.at["mainland_supply", "p_nom_extendable"] = False
    n.generators.at["diesel_new", "bus"] = "mainland"
    out = S.tie_state(n, island)
    assert (out["state"], out["links"]) == ("unit_on_mainland", ["subsea_tie"])
    assert "mainland_supply" not in out["reason"]


def test_a_zero_capacity_grid_import_link_is_not_a_feed():
    n, island = _untagged_r4(poc=False)
    n.links.at["subsea_tie", "eh_role"] = "grid_import"
    n.links.at["subsea_tie", "p_nom"] = 0.0
    n.links.at["subsea_tie", "p_nom_extendable"] = False
    assert S.tie_state(n, island)["state"] == "no_tie"
