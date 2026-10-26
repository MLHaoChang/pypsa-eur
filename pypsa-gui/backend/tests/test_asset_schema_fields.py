"""
S1a (plan 2026-10-13-s1-expert-asset-cards.md): the field descriptors every
asset face renders from, and GET /api/network/asset_schema/{component}.

One descriptor per field, in the spec's fixed group order (uniform asset
parameterisation §3.2). The vocabulary is the study's LedgerRow (label,
technical_name, help, unit), so S2's guided ledger is a projection of it.
"""
from __future__ import annotations

import json
import pathlib
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
    "curtailment_cost", "discount_rate", "v_nom_0", "v_nom_1",
}

# M5: GUI columns one class's create model declares (models/schemas.py `BusCreate.country`, `CarrierCreate.unit`,
# `GlobalConstraintCreate.carrier`), allowed for that class only.
CUSTOM_BY_CLASS = {"Bus": {"country", "mwh_per_t", "product_value"}, "Carrier": {"unit"},
                   "GlobalConstraint": {"carrier"},
                   # Grid-to-process ticket 02: the process-unit columns (models/energy_hub.py EH_CUSTOM_COLUMNS).
                   "Link": {"eh_process", "criticality_tier", "throughput_unit", "specific_energy_kwh_per_t",
                            # Grid-to-process issue 17: the harmonic-screen columns (models/process_unit.py).
                            "rated_mva", "harmonic_source", "harmonic_spectrum", "spectrum_post_filter",
                            "single_phase_share",
                            # PyPSA registers extra ports on first use (`pypsa.constants.RE_PORTS_GE_2`).
                            "bus2", "bus3", "bus4", "efficiency2", "efficiency3", "efficiency4"}}

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
        "control", "e_sum_min", "e_sum_max",
    },
    "StorageUnit": {
        "name", "carrier", "bus", "p_nom", "max_hours", "p_nom_extendable", "p_nom_min",
        "p_nom_max", "efficiency_store", "efficiency_dispatch", "standing_loss",
        "cyclic_state_of_charge", "state_of_charge_initial", "inflow", "marginal_cost",
        "capital_cost", "fom_cost", "discount_rate", "build_year", "lifetime",
        "outage_rate_value", "outage_rate_basis", "mttr_hours",
    },
    # S1c: the Store, Load and Link edit cards as they were before S1c.
    "Store": {
        "name", "bus", "carrier", "e_nom", "e_nom_extendable", "e_nom_min", "e_nom_max", "e_min_pu",
        "e_max_pu", "e_initial", "e_cyclic", "capital_cost", "marginal_cost", "fom_cost", "overnight_cost",
        "discount_rate", "standing_loss", "build_year", "lifetime",
        "outage_rate_value", "outage_rate_basis", "mttr_hours",
    },
    "Load": {"name", "bus", "carrier", "p_set", "q_set", "sign"},
    # M5: the Bus card's edit form before M5 (coordinates as x/y).
    "Bus": {"name", "v_nom", "carrier", "control", "country", "sub_network", "x", "y"},
    "Link": {
        "name", "carrier", "bus0", "bus1", "p_nom", "p_nom_extendable", "p_nom_min", "p_nom_max",
        "p_min_pu", "p_max_pu", "efficiency", "marginal_cost", "capital_cost", "fom_cost", "overnight_cost",
        "discount_rate", "build_year", "lifetime", "outage_rate_value", "outage_rate_basis", "mttr_hours",
    },
    # S1c-2: the Line and Transformer panels before S1c-2. A line's r/x/b are typed per km by the panel's own
    # block (they scale with length) and its single overnight cost is replaced by the per-km part.
    "Line": {
        "name", "s_nom", "length", "capital_cost", "fom_cost", "s_nom_extendable", "s_nom_min", "s_nom_max",
        "discount_rate", "build_year", "lifetime", "outage_rate_value", "outage_rate_basis", "mttr_hours",
    },
    "Transformer": {
        "name", "v_nom_0", "v_nom_1", "s_nom", "r", "x", "tap_ratio", "phase_shift", "s_nom_extendable",
        "s_nom_min", "s_nom_max", "capital_cost", "fom_cost", "overnight_cost", "discount_rate", "build_year",
        "lifetime",
    },
}

ALL = sorted(F.known_components())
ATTR = {"Generator": "generators", "StorageUnit": "storage_units", "Store": "stores", "Load": "loads",
        "Link": "links", "Line": "lines", "Transformer": "transformers",
        # M5: the non-asset components.
        "Bus": "buses", "Carrier": "carriers", "GlobalConstraint": "global_constraints",
        "ShuntImpedance": "shunt_impedances"}
# The capacity each extendable class sizes.
NOM = {"Generator": "p_nom", "StorageUnit": "p_nom", "Link": "p_nom", "Store": "e_nom", "Line": "s_nom",
       "Transformer": "s_nom"}


def _fields(component: str) -> dict[str, F.FieldSpec]:
    return {f.key: f for f in F.fields_for(component)}


def test_groups_are_the_spec_order():
    assert F.GROUPS == (
        "identity", "size", "investment", "fom", "variable", "performance", "replacement", "provenance",
    )


@pytest.mark.parametrize("component", ALL)
def test_fields_follow_group_order(component):
    order = [F.GROUPS.index(f.group) for f in F.fields_for(component)]
    assert order == sorted(order)


@pytest.mark.parametrize("component", ALL)
def test_every_field_is_a_real_column(component):
    n = pypsa.Network()
    attr = ATTR[component]
    pypsa_attrs = set(getattr(n.components, attr).defaults.index)
    allowed = pypsa_attrs | CUSTOM_COLUMNS | set(part_columns(component)) | CUSTOM_BY_CLASS.get(component, set())
    unknown = {f.key for f in F.fields_for(component)} - allowed
    assert not unknown, unknown


@pytest.mark.parametrize("component", sorted(CARD_KEYS))
def test_no_card_field_is_lost(component):
    assert CARD_KEYS[component] - set(_fields(component)) == set()


def test_capital_cost_is_derived_and_labelled_with_its_period():
    for component in sorted(NOM):
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
                ("EUR/kW/yr", "EUR/MW/yr"): 1e-3, ("EUR/kWh/yr", "EUR/MWh/yr"): 1e-3,
                ("%", "fraction"): 100.0}
    for component in ALL:
        for f in F.fields_for(component):
            key = (f.unit_display, f.unit_storage)
            if key in expected:
                assert math.isclose(f.scale, expected[key]), (component, f.key)
            elif f.unit_display == f.unit_storage:
                assert f.scale == 1.0, (component, f.key)


def test_reveal_rules_name_fields_of_the_same_class():
    for component in ALL:
        keys = set(_fields(component))
        for f in F.fields_for(component):
            for k in (f.reveal_when or {}):
                assert k in keys, (component, f.key, k)


def test_extendable_bounds_reveal_only_when_extendable():
    for component, nom in NOM.items():
        fs = _fields(component)
        assert fs[f"{nom}_min"].reveal_when == {f"{nom}_extendable": True}, component
        assert fs[f"{nom}_max"].reveal_when == {f"{nom}_extendable": True}, component
        assert fs[f"{nom}_max"].blank == "clear", component


def test_every_field_has_a_label_and_help():
    for component in ALL:
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


# ── S1b: what a blank field means on save ────────────────────────────────────

@pytest.mark.parametrize("component", ALL)
def test_blank_policy_agrees_with_the_write_model(component):
    """`clear` sends null, so the write model must accept None (an Optional
    field, or a bound that coerces None to inf). `zero` and `keep` fields
    must not be Optional: a blank there would otherwise be ambiguous."""
    from models import schemas as M

    model = getattr(M, f"{component}Create")
    branch = component in ("Link", "Line", "Transformer")
    required = ({"name": "x", "bus0": "a", "bus1": "b"} if branch
                else {"name": "x"} if component in ("Bus", "Carrier")
                else {"name": "x", "constant": 1.0} if component == "GlobalConstraint"
                else {"name": "x", "bus": "b"})
    for f in F.fields_for(component):
        if f.derived or f.key not in model.model_fields:
            continue
        assert f.blank in ("clear", "zero", "empty", "keep"), (f.key, f.blank)
        if f.blank == "empty":
            # `empty` sends "": the model must take it (a `str = ""` column).
            model.model_validate({**required, f.key: ""})
        accepts_none = True
        try:
            model.model_validate({**required, f.key: None})
        except Exception:  # noqa: BLE001 — pydantic.ValidationError
            accepts_none = False
        if f.blank == "clear":
            assert accepts_none, f"{component}.{f.key} is 'clear' but the model refuses None"


def test_m5_text_columns_blank_to_empty_and_constraint_options_clear():
    """M5: a Bus's country or sub-network and a Carrier's display fields save "" when emptied (the old Bus form
    did; null would be refused); a GlobalConstraint's optional carrier, attribute and period save null."""
    bus, car, gc = _fields("Bus"), _fields("Carrier"), _fields("GlobalConstraint")
    assert {k: bus[k].blank for k in ("country", "sub_network")} == {"country": "empty", "sub_network": "empty"}
    assert {car[k].blank for k in ("nice_name", "color", "unit")} == {"empty"}
    assert {gc[k].blank for k in ("carrier_attribute", "carrier", "investment_period")} == {"clear"}
    assert bus["carrier"].blank == "keep" and bus["v_nom"].blank == "keep"


def test_m5_enums_are_the_options_the_forms_offer():
    assert _fields("Bus")["control"].options == ("PQ", "PV", "Slack")
    assert _fields("GlobalConstraint")["sense"].options == ("<=", "==", ">=")
    assert set(_fields("GlobalConstraint")["type"].options) == {
        "primary_energy", "tech_capacity_expansion_limit", "operational_limit",
        "transmission_volume_expansion_limit", "transmission_expansion_cost_limit"}


def test_m5b_constraint_fields_show_for_the_types_that_read_them():
    """M5b: the form shows the summed carrier column only for primary energy, and the carrier only for the two
    carrier-scoped limits; a rule naming several values (a tuple) reveals on any of them."""
    gc = _fields("GlobalConstraint")
    assert gc["carrier_attribute"].reveal_when == {"type": "primary_energy"}
    assert gc["carrier"].reveal_when == {"type": ("tech_capacity_expansion_limit", "operational_limit")}
    for f in F.fields_for("GlobalConstraint"):
        for want in (f.reveal_when or {}).values():
            assert set(want if isinstance(want, tuple) else (want,)) <= set(gc["type"].options), f.key


def test_costs_blank_to_zero_and_bounds_clear():
    g = _fields("Generator")
    for k in ("marginal_cost", "fom_cost", "curtailment_cost", "start_up_cost", "shut_down_cost"):
        assert g[k].blank == "zero", k
    for k in ("p_nom_max", "lifetime", "overnight_cost", "discount_rate", "outage_rate_value", "mttr_hours",
              "e_sum_min", "e_sum_max"):
        assert g[k].blank == "clear", k
    su = _fields("StorageUnit")
    for k in ("inv_power_overnight", "inv_energy_overnight", "inv_power_lifetime", "inv_energy_fom_share"):
        assert su[k].blank == "clear", k
    assert su["p_nom"].blank == "keep"


# ── the frontend's copy of the descriptors (S1b) ─────────────────────────────

FRONTEND_FIXTURE = (pathlib.Path(__file__).resolve().parents[2]
                    / "frontend" / "src" / "test" / "fixtures" / "assetSchema.json")


def fixture_payload() -> dict:
    return {c: F.schema_payload(c) for c in F.known_components()}


def test_the_frontend_fixture_matches_the_descriptors():
    """The cards' tests render from `assetSchema.json`. Regenerate it after a
    descriptor change with:
    `python -c "import json,tests.test_asset_schema_fields as t;
    t.FRONTEND_FIXTURE.write_text(json.dumps(t.fixture_payload(), indent=1, ensure_ascii=False) + chr(10))"`
    run from pypsa-gui/backend."""
    assert json.loads(FRONTEND_FIXTURE.read_text(encoding="utf-8")) == fixture_payload()


# ── S1c-2: the line's per-km part ────────────────────────────────────────────

def test_a_lines_investment_is_typed_per_km_and_its_costs_derived():
    ln = _fields("Line")
    assert ln["inv_per_km_overnight"].unit_display == "EUR/MVA/km" and not ln["inv_per_km_overnight"].derived
    assert not ln["lifetime"].derived                    # the part takes the line's own lifetime
    for derived in ("capital_cost", "fom_cost"):
        assert ln[derived].derived is True, derived
    assert "overnight_cost" not in ln
    assert set(part_columns("Line")) <= set(ln)
