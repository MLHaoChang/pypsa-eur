"""
Characterization of PATCH /api/network/_bulk, written BEFORE the row-wise body
form is added (spec D9, D30).

The endpoint has zero coverage today and every guarantee below is load-bearing:
a partial application is unrecoverable, and the blank-to-sentinel rule is the
only thing that turns "the user cleared a bound" into PyPSA's ±inf rather than
a NaN the solver reads as missing.

Measured facts these tests depend on (PyPSA 1.1.2):
  * `e_sum_min` is a GENERATOR attribute (default -inf), not a Store one.
  * `lifetime` and `p_nom_max` both resolve to +inf when blanked, by two
    different branches of the same if/elif.
"""
from __future__ import annotations

import math

import pypsa
import pytest

from tests.conftest import build_network

BULK = "/api/network/_bulk"


@pytest.fixture
def net(install_network):
    """A two-generator network, installed as the live singleton."""
    n = build_network()
    install_network(n)
    return n


def test_rename_is_refused(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"name": "gas2"},
    })
    assert r.status_code == 400
    assert "rename" in r.json()["detail"].lower()
    assert "gas" in net.generators.index


def test_unknown_name_rejects_the_whole_batch(client, net):
    before = float(net.generators.at["gas", "p_nom"])
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas", "ghost"],
        "updates": {"p_nom": 999.0},
    })
    assert r.status_code == 404
    assert "ghost" in r.json()["detail"]
    # The whole batch is refused — "gas" must be untouched.
    assert float(net.generators.at["gas", "p_nom"]) == before


def test_unknown_column_is_400(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"p_min_pu ": 0.5},          # trailing space, a real typo
    })
    assert r.status_code == 400
    assert "no column" in r.json()["detail"].lower()


def test_unknown_component_class_is_400(client, net):
    r = client.patch(BULK, json={
        "component_class": "Widget", "names": ["gas"], "updates": {"p_nom": 1.0},
    })
    assert r.status_code == 400
    assert "Widget" in r.json()["detail"]


def test_transient_rows_are_409(client, net, monkeypatch):
    from services.pypsa_service import PyPSAService
    monkeypatch.setattr(
        PyPSAService, "get_transient_rows",
        staticmethod(lambda cls: {"gas"} if cls == "Generator" else set()),
    )
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"p_nom": 1.0},
    })
    assert r.status_code == 409
    assert "scaffolding" in r.json()["detail"].lower()


@pytest.mark.parametrize("col,expected", [
    ("p_nom_max", math.inf),     # endswith("_max")
    ("lifetime", math.inf),      # == "lifetime"
    ("e_sum_min", -math.inf),    # == "e_sum_min"
])
def test_blanking_a_bound_writes_its_sentinel(client, net, col, expected):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {col: None},
    })
    assert r.status_code == 200
    assert float(net.generators.at["gas", col]) == expected


def test_blanking_a_plain_numeric_writes_nan(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"p_nom": None},
    })
    assert r.status_code == 200
    assert math.isnan(float(net.generators.at["gas", "p_nom"]))


def test_empty_string_takes_the_same_blank_path_as_null(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"p_nom_max": ""},
    })
    assert r.status_code == 200
    assert math.isinf(float(net.generators.at["gas", "p_nom_max"]))


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True),
    ("false", False), ("FALSE", False), ("0", False), ("no", False),
])
def test_boolean_strings_coerce_case_insensitively(client, net, raw, expected):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"p_nom_extendable": raw},
    })
    assert r.status_code == 200
    assert bool(net.generators.at["gas", "p_nom_extendable"]) is expected


def test_non_numeric_into_a_numeric_column_is_400(client, net):
    before = float(net.generators.at["gas", "p_nom"])
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"p_nom": "12o0"},
    })
    assert r.status_code == 400
    assert "non-numeric" in r.json()["detail"].lower()
    assert float(net.generators.at["gas", "p_nom"]) == before


def test_inf_string_is_accepted_by_float(client, net):
    # The grid sends the STRING "inf" for an infinity token (spec D12); this
    # pins that the endpoint's float(value) already parses it, so D12 needs no
    # backend change.
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"p_nom_max": "inf"},
    })
    assert r.status_code == 200
    assert math.isinf(float(net.generators.at["gas", "p_nom_max"]))


def test_number_into_a_string_column_is_cast_to_str(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {"carrier": 42},
    })
    assert r.status_code == 200
    assert net.generators.at["gas", "carrier"] == "42"


def test_setting_carrier_creates_the_carrier_row(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"],
        "updates": {"carrier": "brand_new_carrier"},
    })
    assert r.status_code == 200
    assert "brand_new_carrier" in net.carriers.index


def test_one_call_writes_exactly_one_changelog_entry(client, net):
    before = len(client.get("/api/changelog/").json())
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas", "solar"],
        "updates": {"p_nom": 123.0, "marginal_cost": 7.0},
    })
    assert r.status_code == 200
    entries = client.get("/api/changelog/").json()
    # Two rows and two fields, still exactly ONE audit entry.
    assert len(entries) == before + 1


def test_response_reports_row_count_and_field_names(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas", "solar"],
        "updates": {"p_nom": 5.0},
    })
    assert r.status_code == 200
    assert r.json() == {"updated": 2, "fields": ["p_nom"]}


def test_every_named_row_receives_the_value(client, net):
    r = client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas", "solar"],
        "updates": {"p_nom": 77.0},
    })
    assert r.status_code == 200
    assert float(net.generators.at["gas", "p_nom"]) == 77.0
    assert float(net.generators.at["solar", "p_nom"]) == 77.0


def test_empty_names_and_empty_updates_are_400(client, net):
    assert client.patch(BULK, json={
        "component_class": "Generator", "names": [], "updates": {"p_nom": 1.0},
    }).status_code == 400
    assert client.patch(BULK, json={
        "component_class": "Generator", "names": ["gas"], "updates": {},
    }).status_code == 400


def test_carrier_class_is_bulk_editable(client, install_network):
    # D16 absorbs the Carriers tab into the shared grid, which requires that
    # Carrier is a valid component_class here. It already is.
    n = pypsa.Network()
    n.add("Carrier", "gas", co2_emissions=0.2)
    install_network(n)
    r = client.patch(BULK, json={
        "component_class": "Carrier", "names": ["gas"],
        "updates": {"co2_emissions": 0.5},
    })
    assert r.status_code == 200
    assert float(n.carriers.at["gas", "co2_emissions"]) == 0.5
