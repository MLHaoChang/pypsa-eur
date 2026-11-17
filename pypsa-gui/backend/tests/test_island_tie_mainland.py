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


@pytest.mark.parametrize("tid", sorted(_MASTER_TIE_STATES))
def test_no_shipped_template_unit_reads_as_on_the_mainland(tid):
    """GK A C2 (#48): no template unit bus is an eh_poc or supply-marked mainland bus."""
    assert S._units_on_mainland(T.BUILDERS[tid](), _island(tid)) is None


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
    island's own feeder (hospital_feeder, site_transformer), so the Link is not a mainland feed.
    SH 50: never silently, the no_tie answer names each such Link and how to mark a grid feed."""
    n, island = _untagged_r4(poc=False)
    out = S.tie_state(n, island)
    assert (out["state"], out["ties"], out["carrying"]) == ("no_tie", [], [])
    assert out["unmarked_links"] == ["hospital_feeder", "subsea_tie"]
    for word in ("hospital_feeder, subsea_tie", "own feeder", "grid_import", "eh_poc"):
        assert word in out["warning"], word


def test_an_unmarked_link_that_cannot_deliver_is_not_named():
    """SH 50: a 0 MW, non-extendable Link is no feed of any kind (#49)."""
    n, island = _untagged_r4(poc=False)
    n.links.at["subsea_tie", "p_nom"] = 0.0
    n.links.at["subsea_tie", "p_nom_extendable"] = False
    assert S.tie_state(n, island)["unmarked_links"] == ["hospital_feeder"]


def test_no_tie_with_no_internal_link_carries_no_warning():
    n, island = _untagged_r4(poc=False)
    n.remove("Link", ["hospital_feeder", "subsea_tie"])
    assert S.tie_state(n, island) == {"state": "no_tie", "ties": [], "carrying": []}


# ── SH 51: every port of a multi-port Link is read ──


def _three_port(bus2):
    """The untagged R4 fixture with subsea_tie given a third port."""
    n, island = _untagged_r4(poc=False)
    n.links["bus2"] = ""
    n.links.at["subsea_tie", "bus2"] = bus2
    return n, island


def test_a_third_port_on_the_eh_poc_bus_marks_the_mainland_feed():
    n, island = _three_port("hospital")
    n.links.at["subsea_tie", "bus0"] = "island"  # bus0 and bus1 are both off the eh_poc bus
    n.links.at["subsea_tie", "bus1"] = "mainland"
    n.buses.at["hospital", "eh_poc"] = True
    out = S.tie_state(n, island)
    assert (out["state"], out["buses"], sorted(out["links"])) == (
        "unit_on_mainland",
        ["hospital"],
        ["hospital_feeder", "subsea_tie"],
    )
    assert "bus hospital, which reads as the mainland's: it is the eh_poc" in out["reason"]
    assert "subsea_tie" in out["reason"]


def test_a_grid_import_link_with_a_port_off_the_island_is_a_tie_not_a_feed():
    n, island = _three_port("elsewhere")
    n.add("Bus", "elsewhere")
    n.links.at["subsea_tie", "eh_role"] = "grid_import"
    out = S.tie_state(n, island)
    assert out["state"] == "open"  # the template's subsea_tie is normally open
    assert "subsea_tie" in out["ties"]


@pytest.mark.parametrize("blank", ["", float("nan")])
def test_a_blank_extra_port_is_ignored(blank):
    n, island = _three_port(blank)
    n.buses.at["mainland", "eh_poc"] = True
    out = S.tie_state(n, island)
    assert (out["state"], out["links"]) == ("unit_on_mainland", ["subsea_tie"])


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


@pytest.mark.parametrize("flag", ["True", "true", "1", 1, True])
def test_an_eh_poc_flag_read_back_from_csv_still_marks_the_grid_side(flag):
    """GK A C1: eh_poc is read through archetypes' own reader, so a CSV round-trip string counts."""
    n, island = _untagged_r4(poc=False)
    n.buses["eh_poc"] = n.buses["eh_poc"].astype(object)
    n.buses.at["mainland", "eh_poc"] = flag
    assert S.tie_state(n, island)["links"] == ["subsea_tie"]


@pytest.mark.parametrize("flag", ["False", "0", "", 0, False])
def test_an_eh_poc_flag_that_reads_false_marks_nothing(flag):
    n, island = _untagged_r4(poc=False)
    n.buses["eh_poc"] = n.buses["eh_poc"].astype(object)
    n.buses.at["mainland", "eh_poc"] = flag
    assert S.tie_state(n, island)["state"] == "no_tie"


def test_a_normally_open_eh_poc_feed_with_capacity_is_still_refused():
    """GK A ruling on C5 (option A): the feed is read by its size alone. subsea_tie is normally
    open (p_max_pu = p_min_pu = 0) with p_nom > 0, so it still marks the mainland."""
    n, island = _untagged_r4(poc=True)
    n.links.at["subsea_tie", "p_max_pu"] = 0.0
    n.links.at["subsea_tie", "p_min_pu"] = 0.0
    assert n.links.at["subsea_tie", "p_nom"] > 0
    assert not S._carries(n, "Link", "subsea_tie")
    out = S.tie_state(n, island)
    assert (out["state"], out["links"]) == ("unit_on_mainland", ["subsea_tie"])


def test_a_link_with_both_ends_on_eh_poc_buses_names_both_buses():
    """GK A C3 (i): both ends read as the grid side, so units on either bus are refused. The
    island's own hospital_feeder now has an eh_poc end too, so it is named as well."""
    n, island = _untagged_r4(poc=True)
    n.buses.at["island", "eh_poc"] = True
    out = S.tie_state(n, island)
    assert out["state"] == "unit_on_mainland"
    assert sorted(out["links"]) == ["hospital_feeder", "subsea_tie"]
    assert out["buses"] == ["island", "mainland"]
    assert "diesel_new" in out["units"]
    for bus in ("island", "mainland"):
        assert (
            f"bus {bus}, which reads as the mainland's: it is the eh_poc"
            in out["reason"]
        )
    assert "the link subsea_tie" in out["reason"]


def test_an_eh_poc_end_that_hosts_a_listed_unit_refuses_that_unit():
    """GK A C3 (ii): the listed unit on the eh_poc end is the one refused, on that bus."""
    n, island = _untagged_r4(poc=True)
    out = S.tie_state(n, island)
    assert (out["units"], out["buses"]) == (["diesel_new"], ["mainland"])
    assert "diesel_new sits on bus mainland" in out["reason"]


@pytest.mark.parametrize("extendable", [False, True])
def test_a_supply_whose_size_reads_nan_stays_detected(extendable):
    """GK A C4: only a size that is exactly 0 leaves a supply out; NaN fails closed."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add(
        "Generator",
        "nan_supply",
        bus="hospital",
        p_nom=0.0,
        p_nom_extendable=extendable,
    )
    n.generators.at["nan_supply", "p_nom_max" if extendable else "p_nom"] = float("nan")
    out = S.tie_state(n, island)
    assert (out["state"], out["buses"]) == ("unit_on_mainland", ["hospital"])


def test_a_supply_with_a_missing_or_non_numeric_size_stays_detected():
    df = __import__("pandas").DataFrame(
        {"p_nom_extendable": [False, False], "p_nom": ["n/a", None]}, index=["a", "b"]
    )
    assert S._can_deliver(df, "a", "p_nom") and S._can_deliver(df, "b", "p_nom")
    assert S._can_deliver(df.drop(columns="p_nom"), "a", "p_nom")
    assert S._can_deliver(df.drop(columns="p_nom_extendable"), "a", "p_nom")


def test_only_a_size_of_exactly_zero_leaves_a_supply_out():
    """GK A C4/C6: a negative size is not "cannot deliver"; the supply stays detected."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add("Generator", "odd_supply", bus="hospital", p_nom=-1.0)
    assert S.tie_state(n, island)["state"] == "unit_on_mainland"


def test_a_grid_import_link_with_no_eh_poc_marker_is_still_refused_by_its_role():
    """eh_role alone marks the feed: the R4 fixture without the eh_poc tag on the mainland bus."""
    n, island = _untagged_r4(poc=False)
    n.links.at["subsea_tie", "eh_role"] = "grid_import"
    out = S.tie_state(n, island)
    assert (out["state"], out["links"]) == ("unit_on_mainland", ["subsea_tie"])
    assert "grid side (bus0) of the grid-import link subsea_tie" in out["reason"]
    assert "eh_poc" not in out["reason"]


@pytest.mark.parametrize("role", [float("nan"), None])
def test_an_eh_role_that_reads_empty_marks_nothing(role):
    n, island = _untagged_r4(poc=False)
    n.links["eh_role"] = n.links["eh_role"].astype(object)
    n.links.at["subsea_tie", "eh_role"] = role
    assert S.tie_state(n, island)["state"] == "no_tie"


def test_a_network_without_an_eh_role_column_reads_the_eh_poc_feed():
    n, island = _untagged_r4(poc=True)
    n.links = n.links.drop(columns="eh_role")
    assert S.tie_state(n, island)["links"] == ["subsea_tie"]


def test_a_zero_capacity_tie_that_may_flow_reads_open():
    """_carries shares the size helper: a 0-MW, non-extendable tie carries nothing although its
    p_max_pu allows flow."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.links.at["subsea_tie", "p_max_pu"] = 1.0
    assert S.tie_state(n, island)["state"] == "closed"
    n.links.at["subsea_tie", "p_nom"] = 0.0
    n.links.at["subsea_tie", "p_nom_extendable"] = False
    assert S.tie_state(n, island) == _MASTER_TIE_STATES["eh_microgrid"]


def test_a_tie_with_no_size_column_fails_closed_to_its_hourly_limits():
    """Review note: a missing size column reads as able to carry; the hourly limits decide."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.links.at["subsea_tie", "p_max_pu"] = 1.0
    n.links = n.links.drop(columns="p_nom_extendable")
    assert S.tie_state(n, island)["carrying"] == ["subsea_tie"]


@pytest.mark.parametrize("flag", [float("nan"), "False"])
def test_an_unreadable_extendable_flag_keeps_a_zero_supply_detected(flag):
    """A non-boolean flag reads as extendable, so the size is p_nom_max (inf): fails closed."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add("Generator", "flag_supply", bus="hospital", p_nom=0.0)
    n.generators["p_nom_extendable"] = n.generators["p_nom_extendable"].astype(object)
    n.generators.at["flag_supply", "p_nom_extendable"] = flag
    assert S.tie_state(n, island)["state"] == "unit_on_mainland"


# ── GK A (28) conditions on SH 54 / 55 ──


def test_a_link_to_a_bus_with_no_sidecar_unit_is_a_tie_not_an_unmarked_feeder():
    """C1: "the island's buses" is one set, the sidecar units' buses (``island_buses``), for both the
    SH 54 warning and the SH 55 port check. A bus without a unit is off the island."""
    n, island = _untagged_r4(poc=False)
    n.add("Bus", "spare")
    n.add("Link", "to_spare", bus0="island", bus1="spare", p_nom=5.0)
    assert "spare" not in S.island_buses(n, island)
    out = S.tie_state(n, island)
    assert out["state"] in ("open", "closed")
    assert "to_spare" in out["ties"]
    assert "unmarked_links" not in out


def test_the_warning_names_five_links_then_the_true_total():
    """C3: at most five names, then "and N more"; ``unmarked_links`` keeps the full list."""
    n, island = _untagged_r4(poc=False)
    extra = [f"feeder_{i}" for i in range(5)]
    for name in extra:
        n.add("Link", name, bus0="island", bus1="hospital", p_nom=1.0)
    out = S.tie_state(n, island)
    every = sorted(["hospital_feeder", "subsea_tie", *extra])
    assert out["unmarked_links"] == every
    assert out["warning"].startswith(", ".join(every[:5]) + " and 2 more join island buses")


def test_a_normally_open_unmarked_internal_link_still_warns():
    """C4: "can deliver" is size only (#1567), so a Link held at 0 every hour is still named."""
    n, island = _untagged_r4(poc=False)
    n.links.at["hospital_feeder", "p_max_pu"] = 0.0
    n.links.at["hospital_feeder", "p_min_pu"] = 0.0
    assert "hospital_feeder" in S.tie_state(n, island)["unmarked_links"]


def test_a_grid_import_link_with_a_third_port_on_eh_poc_names_both_grid_sides():
    """C5: bus0 by the role, and the eh_poc port as well, each with its own words."""
    n, island = _three_port("hospital")
    n.links.at["subsea_tie", "eh_role"] = "grid_import"
    n.buses.at["hospital", "eh_poc"] = True
    out = S.tie_state(n, island)
    assert out["state"] == "unit_on_mainland"
    assert "mainland" in out["buses"] and "hospital" in out["buses"]
    assert "bus mainland, which reads as the mainland's: it is the grid side (bus0) of the grid-import link subsea_tie" in out["reason"]
    assert "bus hospital, which reads as the mainland's: it is the eh_poc" in out["reason"]


# ── SH 56 (owner decision, option 3): a supply never available is left out, and named ──


def _idle_fixture():
    """The template with an unlisted generator on the island bus."""
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add("Generator", "parked", bus="island", p_nom=4.0)
    return n, island


def test_an_unlisted_supply_at_zero_availability_is_left_out_and_named():
    n, island = _idle_fixture()
    n.generators.at["parked", "p_max_pu"] = 0.0
    out = S.tie_state(n, island)
    assert out["state"] == "open"
    assert out["idle_supplies"] == ["parked"]
    for word in ("parked", "0 %", "not read as a mainland supply"):
        assert word in out["idle_warning"], word


def test_a_zero_hourly_availability_series_is_left_out_too():
    n, island = _idle_fixture()
    n.generators_t.p_max_pu["parked"] = 0.0
    assert S.tie_state(n, island)["idle_supplies"] == ["parked"]


@pytest.mark.parametrize("hour_value", [0.2, float("nan")])
def test_any_available_or_unreadable_hour_keeps_the_supply_detected(hour_value):
    n, island = _idle_fixture()
    n.generators_t.p_max_pu["parked"] = 0.0
    n.generators_t.p_max_pu.iloc[0, n.generators_t.p_max_pu.columns.get_loc("parked")] = hour_value
    out = S.tie_state(n, island)
    assert out["state"] == "unit_on_mainland"
    assert "parked" in out["reason"]


def test_an_idle_storage_unit_is_left_out_too():
    n, island = _idle_fixture()
    n.remove("Generator", "parked")
    n.add("StorageUnit", "parked_bess", bus="island", p_nom=2.0, p_max_pu=0.0)
    assert S.tie_state(n, island)["idle_supplies"] == ["parked_bess"]


def test_an_available_unlisted_supply_is_still_refused():
    n, island = _idle_fixture()
    out = S.tie_state(n, island)
    assert out["state"] == "unit_on_mainland" and "parked" in out["reason"]
