# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Issue template-e2e 20 (GK A 40, follow-up b): section 5's site assets
(`flow_service._site_assets`) and the report's grid supply
(`eh_report._connection`) read a generator on an import Link's grid-side bus
by one rule, `flow_service.site_equipment_on`: grid supply only when that
bus holds nothing else of the site's. On a user-edited network the two
readers give the same answer."""

import pathlib
import sys

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "project_templates"))

import eh_templates as T  # noqa: E402

from services import flow_service as F  # noqa: E402
from services.adequacy import eh_report as R  # noqa: E402

REQUEST = {"archetype": "datacenter"}


def _dc():
    n = T.BUILDERS["eh_datacenter"]()
    cap = F.import_capacity(n, REQUEST)
    assert cap.mw is not None and cap.links, cap.refusal
    grid_bus = str(n.links.at[cap.links[0], "bus0"])
    grid_gens = set(n.generators.index[n.generators.bus == grid_bus])
    assert grid_gens, "the DC template supplies its grid bus"
    return n, cap, grid_bus, grid_gens


def _readers(n, cap):
    """(the generators section 5 leaves out of the site's assets, the report's
    grid supply, the report's count reasons)."""
    site = {
        name
        for klass, name in F._site_assets(n, list(cap.links), set(cap.links))
        if klass == "Generator"
    }
    left_out = set(n.generators.index) - site
    payload, supply = R._connection(n, REQUEST)
    return left_out, supply, payload["grid_supply_counted"]


def test_a_bus_that_holds_only_the_grid_is_grid_supply_in_both():
    n, cap, grid_bus, grid_gens = _dc()
    assert F.site_equipment_on(n, grid_bus, set(cap.links)) == []
    left_out, supply, counted = _readers(n, cap)
    assert supply == grid_gens
    assert left_out == grid_gens
    assert counted == []


def test_a_load_on_the_connection_bus_keeps_the_grid_generator_in_both():
    n, cap, grid_bus, grid_gens = _dc()
    n.add("Load", "user_load", bus=grid_bus, p_set=1.0)
    assert F.site_equipment_on(n, grid_bus, set(cap.links)) == ["load user_load"]
    left_out, supply, counted = _readers(n, cap)
    assert supply == set()
    assert left_out == set(), "section 5 counts the generator, as the report does"
    assert counted == [
        f"{g} is counted: bus {grid_bus} also holds load user_load"
        for g in sorted(grid_gens)
    ]


def test_an_extra_generator_on_the_connection_bus_is_grid_supply_in_both():
    n, cap, grid_bus, grid_gens = _dc()
    n.add("Generator", "user_gen", bus=grid_bus, p_nom=3.0, carrier="gas")
    # A generator is not site equipment on the bus: it is behind the connection.
    assert F.site_equipment_on(n, grid_bus, set(cap.links)) == []
    left_out, supply, counted = _readers(n, cap)
    assert supply == grid_gens | {"user_gen"}
    assert left_out == grid_gens | {"user_gen"}
    assert counted == []


@pytest.mark.parametrize("extra", ["load", "store"])
def test_an_extra_load_and_generator_together_count_both_in_both(extra):
    n, cap, grid_bus, grid_gens = _dc()
    n.add("Generator", "user_gen", bus=grid_bus, p_nom=3.0, carrier="gas")
    if extra == "load":
        n.add("Load", "user_x", bus=grid_bus, p_set=1.0)
    else:
        n.add("Store", "user_x", bus=grid_bus, e_nom=1.0)
    left_out, supply, counted = _readers(n, cap)
    assert supply == set() and left_out == set()
    assert {c.split(" is counted")[0] for c in counted} == grid_gens | {"user_gen"}
    assert all(c.endswith(f"also holds {extra} user_x") for c in counted)


def test_the_results_row_says_not_established_in_the_readiness_cards_own_sentence():
    """UX 34 (ah): one cause sentence per reason. The readiness card shows
    `import_capacity`'s refusal (flow_service's eh_connection_limit item);
    the report row, and Guided/Expert after it, show the same sentence."""
    from services.reports import evidence as E

    n, cap, _bus, _gens = _dc()
    n.links.loc[list(cap.links), "p_nom_extendable"] = True
    card = F.import_capacity(n, REQUEST).refusal
    assert card and "eh_" not in card
    payload = R.sizing_summary_from_network(n, REQUEST)
    assert payload["grid_connection_refusal"] == card
    assert E._connection_cell(payload) == f"Not established: {card}"
