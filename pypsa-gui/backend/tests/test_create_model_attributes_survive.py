"""
An attribute the API declares must reach the network.

`_drop_unknown_extras` whitelists a payload key against PyPSA's attribute
catalog OR the columns already on the frame. Neither arm knows about the
attributes the GUI adds on purpose — the Create models declare them, and their
own comments say why they exist ("Custom GUI columns, same pattern as
curtailment_cost: stored on the component DataFrame, no PyPSA meaning, netCDF
round-trip for free").

So on a network that does not already carry the column — every network built
from scratch — the FIRST asset created through the API loses them silently,
behind a 201. Measured on the pinned PyPSA, the casualties are:

  Bus          country
  Carrier      unit
  Generator    outage_rate_value, outage_rate_basis, mttr_hours,
               p_max_pu_includes_outages, curtailment_cost, unit
  Line/Link/Store/StorageUnit
               outage_rate_value, outage_rate_basis, mttr_hours
  Link         bus2, bus3, bus4, efficiency2, efficiency3
  Transformer  v_nom_0, v_nom_1

The adequacy ones are the expensive ones: a generator created with an explicit
outage rate reads back as having none, so the whole occurrence chain falls
through to the per-carrier default library and the user's number is gone with
nothing to show it ever arrived.

It is invisible on networks imported from PyPSA-Eur, which arrive with the
columns already present and so pass on the second arm. Only the from-scratch
path bites — which is the path a new user takes.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import models.schemas as schemas
from routers.network import (
    create_bus,
    create_generator,
    create_line,
    create_link,
)
from services.network_crud import _drop_unknown_extras


def _bare_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    return n


@pytest.fixture
def live(install_network):
    return install_network(_bare_network())


def test_bus_country_survives(live):
    create_bus(schemas.BusCreate(name="B", country="DE", v_nom=380.0))
    assert "country" in live.buses.columns, "bus `country` was dropped"
    assert live.buses.at["B", "country"] == "DE"


def test_generator_occurrence_attributes_survive(live):
    create_bus(schemas.BusCreate(name="B", v_nom=380.0))
    create_generator(schemas.GeneratorCreate(
        name="G", bus="B", carrier="gas", p_nom=100.0,
        outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=48.0,
    ))
    for attr, want in [("outage_rate_value", 0.05),
                       ("outage_rate_basis", "EFORd"),
                       ("mttr_hours", 48.0)]:
        assert attr in live.generators.columns, f"generator `{attr}` was dropped"
        assert live.generators.at["G", attr] == want


def test_branch_occurrence_attributes_survive(live):
    create_bus(schemas.BusCreate(name="B0", v_nom=380.0))
    create_bus(schemas.BusCreate(name="B1", v_nom=380.0))
    create_line(schemas.LineCreate(
        name="LN", bus0="B0", bus1="B1", x=0.1, r=0.01, s_nom=100.0,
        outage_rate_value=0.03, mttr_hours=12.0,
    ))
    assert live.lines.at["LN", "outage_rate_value"] == pytest.approx(0.03)
    assert live.lines.at["LN", "mttr_hours"] == pytest.approx(12.0)


def test_multi_output_link_buses_survive(live):
    for b in ("B0", "B1", "B2"):
        create_bus(schemas.BusCreate(name=b, v_nom=380.0))
    create_link(schemas.LinkCreate(
        name="LK", bus0="B0", bus1="B1", bus2="B2",
        p_nom=100.0, efficiency2=0.4,
    ))
    assert live.links.at["LK", "bus2"] == "B2", "a multi-output link lost bus2"
    assert live.links.at["LK", "efficiency2"] == pytest.approx(0.4)


def test_an_undeclared_key_is_still_dropped(live):
    # The control, and the reason the whitelist exists at all: `extra='allow'`
    # without it lets ANY key reach `n.add()`. Widening the filter to the
    # declared surface must not widen it to an arbitrary one.
    kept = _drop_unknown_extras(
        "Bus", "buses", {"v_nom": 380.0, "definitely_not_an_attribute": 1},
    )
    assert kept == {"v_nom": 380.0}


@pytest.mark.parametrize("component_class,attr,model_name", [
    ("Bus", "buses", "BusCreate"),
    ("Carrier", "carriers", "CarrierCreate"),
    ("Generator", "generators", "GeneratorCreate"),
    ("Load", "loads", "LoadCreate"),
    ("Line", "lines", "LineCreate"),
    ("Link", "links", "LinkCreate"),
    ("StorageUnit", "storage_units", "StorageUnitCreate"),
    ("Store", "stores", "StoreCreate"),
    ("Transformer", "transformers", "TransformerCreate"),
    ("ShuntImpedance", "shunt_impedances", "ShuntImpedanceCreate"),
])
def test_every_declared_field_survives_the_filter(live, component_class, attr, model_name):
    # The general statement, so a field added to a Create model tomorrow is
    # covered without anyone remembering this file exists.
    model = getattr(schemas, model_name)
    declared = {f: 1.0 for f in model.model_fields if f != "name"}
    kept = _drop_unknown_extras(component_class, attr, dict(declared))
    missing = sorted(set(declared) - set(kept))
    assert missing == [], f"{component_class}: declared fields dropped: {missing}"
