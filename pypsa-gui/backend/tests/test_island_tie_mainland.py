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
    for word in ("diesel_new", "mainland", "mainland_supply", "move", "list"):
        assert word in out["reason"].lower(), word


def test_a_storage_unit_the_sidecar_does_not_name_marks_its_bus_mainland_too():
    island = _island("eh_microgrid")
    n = T.BUILDERS["eh_microgrid"]()
    n.add("Bus", "m3")
    n.add("StorageUnit", "m3_store", bus="m3", p_nom=5.0)
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
