"""
A carrier-scoped global constraint binds the carrier the user picked.

PyPSA (1.1) reads the carrier of an `operational_limit` and a
`tech_capacity_expansion_limit` from `carrier_attribute`
(`define_operational_limit` queries `carrier == glc.carrier_attribute`;
`define_tech_capacity_expansion_limit` groups by it). `carrier` is not a
GlobalConstraint attribute at all, but Solver Settings' form, its fuel-limit
grid and the chat's create_component all wrote the carrier there, so every
such limit was silently dropped from the LP: a 40 MWh gas cap let gas run
200 MWh.

The service now writes the carrier into `carrier_attribute` for these two
types and keeps `carrier` as a mirror (the tables and the fuel grid read it);
a project saved before the fix is repaired as it loads, in memory.
"""
from __future__ import annotations

import logging

import pypsa
import pytest

from models.schemas import GlobalConstraintCreate
from services import chat_tools
from services.network_crud import _merge_partial_update
from services.network_global_constraints import (
    apply_create_global_constraint,
    apply_update_global_constraint,
    repair_carrier_scoped_limits,
)
from services.pypsa_service import PyPSAService


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(range(4))
    n.add("Carrier", ["gas", "coal"])
    n.add("Bus", "b")
    n.add("Generator", "gas", bus="b", carrier="gas", p_nom=100, marginal_cost=10)
    n.add("Generator", "coal", bus="b", carrier="coal", p_nom=100, marginal_cost=50)
    n.add("Load", "l", bus="b", p_set=50)
    return n


def _row(n: pypsa.Network, name: str) -> dict:
    gc = n.global_constraints
    return {k: gc.at[name, k] for k in ("type", "carrier_attribute", "carrier") if k in gc.columns}


def _create(**body) -> None:
    apply_create_global_constraint(GlobalConstraintCreate(**body))


def _update(name: str, **body) -> None:
    apply_update_global_constraint(name, GlobalConstraintCreate(name=name, **body),
                                   merge_partial_update=_merge_partial_update)


@pytest.mark.parametrize("kind", ["operational_limit", "tech_capacity_expansion_limit"])
def test_a_carrier_scoped_limit_stores_its_carrier_where_pypsa_reads_it(install_network, kind):
    n = install_network(_network())
    _create(name="lim", type=kind, sense="<=", constant=40.0, carrier="gas")
    assert _row(n, "lim")["carrier_attribute"] == "gas"
    assert _row(n, "lim")["carrier"] == "gas"


def test_a_limit_given_the_attribute_only_gets_the_mirror(install_network):
    n = install_network(_network())
    _create(name="lim", type="operational_limit", sense="<=", constant=40.0, carrier_attribute="coal")
    assert _row(n, "lim") == {"type": "operational_limit", "carrier_attribute": "coal", "carrier": "coal"}


def test_a_primary_energy_cap_is_left_alone(install_network):
    n = install_network(_network())
    _create(name="co2", type="primary_energy", sense="<=", constant=1e6, carrier_attribute="co2_emissions")
    row = _row(n, "co2")
    assert row["carrier_attribute"] == "co2_emissions"
    assert not (isinstance(row.get("carrier"), str) and row["carrier"])     # no mirror on this type


def test_an_edited_carrier_moves_both_columns(install_network):
    n = install_network(_network())
    _create(name="lim", type="operational_limit", sense="<=", constant=40.0, carrier="gas")
    _update("lim", carrier="coal")
    assert _row(n, "lim")["carrier_attribute"] == "coal"
    assert _row(n, "lim")["carrier"] == "coal"


def test_a_cap_switched_to_an_operational_limit_takes_the_carrier(install_network):
    """The form clears the attribute a type does not read: the carrier it sends wins."""
    n = install_network(_network())
    _create(name="lim", type="primary_energy", sense="<=", constant=1e6, carrier_attribute="co2_emissions")
    _update("lim", type="operational_limit", carrier="gas", carrier_attribute=None)
    assert _row(n, "lim")["carrier_attribute"] == "gas"


def test_the_chat_create_component_path_is_fixed_too(install_network):
    n = install_network(_network())
    chat_tools.create_component("GlobalConstraint", "lim",
                                attrs={"type": "operational_limit", "sense": "<=", "constant": 40.0,
                                       "carrier": "gas"})
    assert _row(n, "lim")["carrier_attribute"] == "gas"


def test_a_project_saved_before_the_fix_is_repaired_as_it_loads(tmp_path):
    """The repair runs on the in-memory network only: the file changes on the next save."""
    old = _network()
    old.add("GlobalConstraint", "lim", type="operational_limit", sense="<=", constant=40.0)
    old.global_constraints["carrier"] = ""
    old.global_constraints.loc["lim", "carrier"] = "gas"
    old.add("GlobalConstraint", "co2", type="primary_energy", sense="<=", constant=1e6,
            carrier_attribute="co2_emissions")
    path = tmp_path / "network.nc"
    old.export_to_netcdf(str(path))
    before = path.read_bytes()

    n = pypsa.Network()
    PyPSAService.import_network_from_netcdf(n, path)
    assert n.global_constraints.at["lim", "carrier_attribute"] == "gas"
    assert n.global_constraints.at["co2", "carrier_attribute"] == "co2_emissions"
    assert path.read_bytes() == before


def test_the_repair_counts_and_is_idempotent():
    """PyPSA fills an unset `carrier_attribute` with "co2_emissions", so that is what an old row holds."""
    n = _network()
    n.add("GlobalConstraint", "lim", type="tech_capacity_expansion_limit", sense="<=", constant=40.0)
    n.global_constraints["carrier"] = ""
    n.global_constraints.loc["lim", "carrier"] = "gas"
    assert n.global_constraints.at["lim", "carrier_attribute"] == "co2_emissions"
    assert repair_carrier_scoped_limits(n) == 1
    assert repair_carrier_scoped_limits(n) == 0
    assert n.global_constraints.at["lim", "carrier_attribute"] == "gas"


def test_the_repair_keeps_an_attribute_that_names_a_carrier():
    """A network built outside the app with the PyPSA column set right is not overridden."""
    n = _network()
    n.add("GlobalConstraint", "lim", type="operational_limit", sense="<=", constant=40.0, carrier_attribute="coal")
    n.global_constraints["carrier"] = ""
    n.global_constraints.loc["lim", "carrier"] = "gas"
    assert repair_carrier_scoped_limits(n) == 0
    assert n.global_constraints.at["lim", "carrier_attribute"] == "coal"


def test_the_40_mwh_gas_cap_binds_in_the_solve(install_network):
    """The measured defect: the same cap stored in `carrier` let gas run 200 MWh."""
    n = install_network(_network())
    _create(name="gas_cap", type="operational_limit", sense="<=", constant=40.0, carrier="gas")
    logging.disable(logging.CRITICAL)
    try:
        n.optimize(solver_name="highs", log_to_console=False)
    finally:
        logging.disable(logging.NOTSET)
    assert n.generators_t.p["gas"].sum() == pytest.approx(40.0, abs=1e-6)
