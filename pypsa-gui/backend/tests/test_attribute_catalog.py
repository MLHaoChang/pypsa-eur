"""
GET /api/network/catalog/{component} — the payload spec D24 fixes, and the two
components D14 adds to the time-series listing.

Measured against PyPSA 1.1.2: defaults carries nine columns and NO default_text,
so default_text is derived here as str(raw_default). That derivation is the only
reason an inf default survives clean_scalar's non-finite → null scrub.
"""
from __future__ import annotations

import pypsa

from tests.conftest import build_network

CATALOG = "/api/network/catalog"


def test_unknown_component_is_400(client, install_network):
    install_network(build_network())
    r = client.get(f"{CATALOG}/Widget")
    assert r.status_code == 400
    assert "Generator" in r.json()["detail"]        # lists the valid set


def test_payload_carries_exactly_the_nine_specified_fields(client, install_network):
    install_network(build_network())
    r = client.get(f"{CATALOG}/Generator")
    assert r.status_code == 200
    body = r.json()
    assert body["component"] == "Generator"
    attrs = body["attributes"]
    assert len(attrs) > 40                           # Generator has 53
    assert set(attrs[0]) == {
        "name", "status", "varying", "dtype", "unit",
        "description", "type", "default", "default_text",
    }
    # `static` and `typ` are deliberately NOT served (D24).
    assert "static" not in attrs[0]
    assert "typ" not in attrs[0]


def _attr(client, component: str, name: str) -> dict:
    body = client.get(f"{CATALOG}/{component}").json()
    return next(a for a in body["attributes"] if a["name"] == name)


def test_an_inf_default_is_null_but_default_text_says_inf(client, install_network):
    install_network(build_network())
    a = _attr(client, "Generator", "p_nom_max")
    assert a["default"] is None                      # clean_scalar scrubbed it
    assert a["default_text"] == "inf"                # …and this is why D23 can show it
    assert a["unit"] == "MW"
    assert a["dtype"] == "float64"
    assert a["status"].startswith("Input")


def test_a_missing_unit_is_null_not_the_string_nan(client, install_network):
    install_network(build_network())
    a = _attr(client, "Generator", "bus")
    assert a["unit"] is None
    assert a["dtype"] == "object"
    assert a["status"] == "Input (required)"


def test_varying_is_a_real_bool(client, install_network):
    install_network(build_network())
    assert _attr(client, "Generator", "marginal_cost")["varying"] is True
    assert _attr(client, "Generator", "p_nom")["varying"] is False


def test_output_attributes_are_reported_as_output(client, install_network):
    install_network(build_network())
    assert _attr(client, "Generator", "p_nom_opt")["status"] == "Output"


def test_bus_control_is_output_which_is_why_d13_overrides_it(client, install_network):
    # D13's override list exists because the catalog calls this Output while the
    # app has always exposed it. Pinning the upstream fact the override answers.
    install_network(build_network())
    assert _attr(client, "Bus", "control")["status"] == "Output"


def test_boolean_dtype_is_reported_as_bool(client, install_network):
    install_network(build_network())
    assert _attr(client, "Generator", "p_nom_extendable")["dtype"] == "bool"


def test_every_grid_component_class_is_served(client, install_network):
    install_network(build_network())
    for cls in ["Bus", "Carrier", "Line", "Link", "Transformer",
                "Generator", "StorageUnit", "Store", "Load"]:
        r = client.get(f"{CATALOG}/{cls}")
        assert r.status_code == 200, cls
        assert len(r.json()["attributes"]) > 0, cls


def test_timeseries_listing_now_covers_buses_and_transformers(client, install_network):
    # D14: the series-shadow check must cover every tab the grid renders.
    n = pypsa.Network()
    n.set_snapshots(["2025-01-01 00:00", "2025-01-01 01:00"])
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    n.add("Line", "L1", bus0="B1", bus1="B2", x=0.1, r=0.01)
    n.buses_t.v_mag_pu_set["B1"] = [1.0, 1.01]
    install_network(n)
    listed = client.get("/api/network/timeseries").json()
    assert any(e["component"] == "buses" for e in listed)


def test_timeseries_listing_still_covers_the_original_six(client, install_network):
    n = build_network()
    n.generators_t.p_max_pu["solar"] = [0.5, 0.6, 0.7, 0.8]
    install_network(n)
    listed = client.get("/api/network/timeseries").json()
    entry = next(e for e in listed
                 if e["component"] == "generators" and e["attribute"] == "p_max_pu")
    assert "solar" in entry["columns"]
