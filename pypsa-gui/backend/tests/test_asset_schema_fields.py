"""
S1a (plan 2026-10-13-s1-expert-asset-cards.md): the field descriptors every
asset face renders from, and GET /api/network/asset_schema/{component}.

One descriptor per field, in the spec's fixed group order (uniform asset
parameterisation §3.2). The vocabulary is the study's LedgerRow (label,
technical_name, help, unit), so S2's guided ledger is a projection of it.
"""
from __future__ import annotations

import json
import math

import pypsa
import pytest

from services.asset_schema import fields as F
from services.asset_schema.schema import part_columns
from tests.conftest import build_network

ROUTE = "/api/network/asset_schema"

# Columns the GUI adds to PyPSA's own (outage occurrence, curtailment penalty,
# S0's investment parts). A descriptor may name these or a PyPSA attribute.
CUSTOM_COLUMNS = {
    "outage_rate_value", "outage_rate_basis", "mttr_hours", "p_max_pu_includes_outages",
    "curtailment_cost", "discount_rate",
}

# Every key the hand-written edit cards let a user change today. S1 must not
# lose one (capital_cost moves to a derived row; overnight_cost on a battery
# is replaced by its parts).
CARD_KEYS = {
    "Generator": {
        "name", "carrier", "bus", "p_nom", "p_nom_extendable", "p_nom_min", "p_nom_max",
        "p_min_pu", "p_max_pu", "efficiency", "marginal_cost", "capital_cost", "fom_cost",
        "overnight_cost", "discount_rate", "curtailment_cost", "build_year", "lifetime",
        "committable", "ramp_limit_up", "ramp_limit_down", "start_up_cost", "shut_down_cost",
        "min_up_time", "min_down_time",
        "outage_rate_value", "outage_rate_basis", "mttr_hours", "p_max_pu_includes_outages",
    },
    "StorageUnit": {
        "name", "carrier", "bus", "p_nom", "max_hours", "p_nom_extendable", "p_nom_min",
        "p_nom_max", "efficiency_store", "efficiency_dispatch", "standing_loss",
        "cyclic_state_of_charge", "state_of_charge_initial", "inflow", "marginal_cost",
        "capital_cost", "fom_cost", "discount_rate", "build_year", "lifetime",
        "outage_rate_value", "outage_rate_basis", "mttr_hours",
    },
}


def _fields(component: str) -> dict[str, F.FieldSpec]:
    return {f.key: f for f in F.fields_for(component)}


def test_groups_are_the_spec_order():
    assert F.GROUPS == (
        "identity", "size", "investment", "fom", "variable", "performance", "replacement", "provenance",
    )


@pytest.mark.parametrize("component", ["Generator", "StorageUnit"])
def test_fields_follow_group_order(component):
    order = [F.GROUPS.index(f.group) for f in F.fields_for(component)]
    assert order == sorted(order)


@pytest.mark.parametrize("component", ["Generator", "StorageUnit"])
def test_every_field_is_a_real_column(component):
    n = pypsa.Network()
    attr = {"Generator": "generators", "StorageUnit": "storage_units"}[component]
    pypsa_attrs = set(getattr(n.components, attr).defaults.index)
    allowed = pypsa_attrs | CUSTOM_COLUMNS | set(part_columns(component))
    unknown = {f.key for f in F.fields_for(component)} - allowed
    assert not unknown, unknown


@pytest.mark.parametrize("component", sorted(CARD_KEYS))
def test_no_card_field_is_lost(component):
    assert CARD_KEYS[component] - set(_fields(component)) == set()


def test_capital_cost_is_derived_and_labelled_with_its_period():
    for component in ("Generator", "StorageUnit"):
        cc = _fields(component)["capital_cost"]
        assert cc.derived is True
        assert cc.unit_display.endswith("/yr")
        assert "CAPEX" not in cc.label


def test_battery_parts_are_typed_and_their_sums_derived():
    su = _fields("StorageUnit")
    for col in part_columns("StorageUnit"):
        assert col in su and not su[col].derived, col
        assert su[col].group in ("investment", "fom")
    assert su["inv_power_overnight"].part == "power"
    assert su["inv_energy_overnight"].part == "energy"
    # The battery's single-part columns are written by derive, never typed.
    for derived in ("capital_cost", "fom_cost", "lifetime"):
        assert su[derived].derived is True, derived
    assert "overnight_cost" not in su


def test_display_scale_matches_the_units():
    expected = {("EUR/kW", "EUR/MW"): 1e-3, ("EUR/kWh", "EUR/MWh"): 1e-3,
                ("EUR/kW/yr", "EUR/MW/yr"): 1e-3, ("%", "fraction"): 100.0}
    for component in ("Generator", "StorageUnit"):
        for f in F.fields_for(component):
            key = (f.unit_display, f.unit_storage)
            if key in expected:
                assert math.isclose(f.scale, expected[key]), (component, f.key)
            elif f.unit_display == f.unit_storage:
                assert f.scale == 1.0, (component, f.key)


def test_reveal_rules_name_fields_of_the_same_class():
    for component in ("Generator", "StorageUnit"):
        keys = set(_fields(component))
        for f in F.fields_for(component):
            for k in (f.reveal_when or {}):
                assert k in keys, (component, f.key, k)


def test_extendable_bounds_reveal_only_when_extendable():
    for component in ("Generator", "StorageUnit"):
        fs = _fields(component)
        assert fs["p_nom_min"].reveal_when == {"p_nom_extendable": True}
        assert fs["p_nom_max"].reveal_when == {"p_nom_extendable": True}


def test_every_field_has_a_label_and_help():
    for component in ("Generator", "StorageUnit"):
        for f in F.fields_for(component):
            assert f.label.strip(), f.key
            assert f.help.strip(), f.key


def test_unknown_class_raises():
    with pytest.raises(KeyError):
        F.fields_for("Widget")


# ── route ────────────────────────────────────────────────────────────────────

def test_route_serves_groups_in_order(client, install_network):
    install_network(build_network())
    r = client.get(f"{ROUTE}/StorageUnit")
    assert r.status_code == 200
    body = r.json()
    assert body["component"] == "StorageUnit"
    groups = [g["id"] for g in body["groups"]]
    assert groups == [g for g in F.GROUPS if g in groups]
    first = body["groups"][0]["fields"][0]
    assert set(first) >= {
        "key", "technical_name", "label", "help", "unit_display", "unit_storage", "scale",
        "kind", "required", "key_param", "derived", "reveal_when", "part", "options",
    }
    json.dumps(body)                                  # JSON-safe


def test_route_unknown_class_is_400_and_lists_known(client, install_network):
    install_network(build_network())
    r = client.get(f"{ROUTE}/Widget")
    assert r.status_code == 400
    assert "StorageUnit" in r.json()["detail"]
