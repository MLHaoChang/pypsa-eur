"""The edit drawer's size unit follows the asset's bus (t/h, t on a tonne bus), as the Guided list does."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates")
)
import eh_templates as T  # noqa: E402

from services.asset_schema.fields import size_units  # noqa: E402
from services.flow_service import _size_unit  # noqa: E402


@pytest.fixture(scope="module")
def n():
    return T.BUILDERS["eh_concentrator_staged"]()


def _first(n, table, col, tonne):
    for name in getattr(n, table).index:
        u = _size_unit(n, getattr(n, table).at[name, col], stock=False)
        if (u == "t/h") == tonne and u is not None:
            return name
    pytest.skip(f"no {table} on a {'tonne' if tonne else 'energy'} bus")


def test_a_generator_on_a_tonne_bus_shows_t_per_h(n):
    name = _first(n, "generators", "bus", True)
    assert size_units(n, "Generator", name)["p_nom"] == "t/h"


def test_a_store_on_a_tonne_bus_shows_t(n):
    for name in n.stores.index:
        if _size_unit(n, n.stores.at[name, "bus"], stock=True) == "t":
            u = size_units(n, "Store", name)
            assert u["e_nom"] == "t" and u["e_nom_max"] == "t"
            return
    pytest.skip("no store on a tonne bus")


def test_an_asset_on_an_energy_bus_keeps_the_class_unit(n):
    name = _first(n, "links", "bus0", False)
    assert size_units(n, "Link", name) == {}


def test_a_class_without_a_bus_size_and_an_unknown_name_are_empty(n):
    assert size_units(n, "Line", "x") == {}
    assert size_units(n, "Generator", "nope") == {}


def _two_bus_net(unit0, unit1):
    import pypsa

    n = pypsa.Network()
    n.add("Bus", "b0", carrier="AC", unit=unit0)
    n.add("Bus", "b1", carrier="AC", unit=unit1)
    n.add("Link", "l", bus0="b0", bus1="b1", p_nom=5)
    return n


def test_a_link_follows_its_input_bus_tonne_to_energy():
    assert size_units(_two_bus_net("t", "MWh"), "Link", "l") == {
        "p_nom": "t/h",
        "p_nom_max": "t/h",
        "p_nom_min": "t/h",
    }


def test_a_link_follows_its_input_bus_energy_to_tonne():
    assert size_units(_two_bus_net("MWh", "t"), "Link", "l") == {}
