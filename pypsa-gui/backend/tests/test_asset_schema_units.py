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
            assert u["e_nom"] == "t" and u["e_nom_max"] == "t" and u["e_initial"] == "t"
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


# ── The route the drawer calls: GET /api/network/asset_schema/{class}/units/{name} ──


def _tonne_generator(n):
    return _first(n, "generators", "bus", True)


def test_the_units_route_answers_t_per_h_for_a_generator_on_a_tonne_bus(
    client, install_network, n
):
    name = _tonne_generator(n)
    install_network(n)
    r = client.get(f"/api/network/asset_schema/Generator/units/{name}")
    assert r.status_code == 200
    body = r.json()
    assert body["component"] == "Generator" and body["name"] == name
    assert body["units"]["p_nom"] == "t/h"


def test_the_units_route_is_empty_for_an_asset_on_an_energy_bus(
    client, install_network, n
):
    name = _first(n, "links", "bus0", False)
    install_network(n)
    r = client.get(f"/api/network/asset_schema/Link/units/{name}")
    assert r.status_code == 200 and r.json()["units"] == {}


def test_the_units_route_refuses_an_unknown_class_and_an_unknown_asset(
    client, install_network, n
):
    install_network(n)
    assert client.get("/api/network/asset_schema/Nope/units/x").status_code == 400
    r = client.get("/api/network/asset_schema/Generator/units/no-such-generator")
    assert r.status_code == 404


# ── Cost units on a tonne bus (ui-modern 05d): per tonne, scale 1 (no kW / kWh factor) ──


def test_a_generator_on_a_tonne_bus_prices_per_tonne(n):
    from services.asset_schema.fields import cost_units

    name = _first(n, "generators", "bus", True)
    c = cost_units(n, "Generator", name)
    assert c["overnight_cost"] == {"unit": "EUR/(t/h)", "scale": 1.0}
    assert c["fom_cost"] == {"unit": "EUR/(t/h)/yr", "scale": 1.0}
    assert c["marginal_cost"] == {"unit": "EUR/t", "scale": 1.0}
    assert "fuel_price" not in c and "start_up_cost" not in c


def test_a_store_on_a_tonne_bus_prices_per_tonne_of_stock(n):
    from services.asset_schema.fields import cost_units

    for name in n.stores.index:
        if _size_unit(n, n.stores.at[name, "bus"], stock=True) == "t":
            c = cost_units(n, "Store", name)
            assert (
                c["overnight_cost"]["unit"] == "EUR/t"
                and c["fom_cost"]["unit"] == "EUR/t/yr"
            )
            return
    pytest.skip("no store on a tonne bus")


def test_an_asset_on_an_energy_bus_keeps_the_per_energy_cost_units(n):
    from services.asset_schema.fields import cost_units

    assert cost_units(n, "Link", _first(n, "links", "bus0", False)) == {}
    assert cost_units(n, "Generator", "nope") == {} and cost_units(n, "Line", "x") == {}


def test_the_units_route_carries_the_cost_units(client, install_network, n):
    name = _tonne_generator(n)
    install_network(n)
    body = client.get(f"/api/network/asset_schema/Generator/units/{name}").json()
    assert body["costs"]["marginal_cost"] == {"unit": "EUR/t", "scale": 1.0}
    energy = _first(n, "links", "bus0", False)
    assert (
        client.get(f"/api/network/asset_schema/Link/units/{energy}").json()["costs"]
        == {}
    )


def test_a_link_prices_on_its_input_bus_and_a_storage_unit_on_its_bus():
    from services.asset_schema.fields import cost_units

    assert cost_units(_two_bus_net("t", "MWh"), "Link", "l")["overnight_cost"] == {
        "unit": "EUR/(t/h)",
        "scale": 1.0,
    }
    assert (
        cost_units(_two_bus_net("MWh", "t"), "Link", "l") == {}
    )  # bus1 is not the size bus
    import pypsa

    m = pypsa.Network()
    m.add("Bus", "b", carrier="X", unit="t")
    m.add("StorageUnit", "s", bus="b", p_nom=1)
    c = cost_units(m, "StorageUnit", "s")
    assert c["capital_cost"] == {"unit": "EUR/(t/h)/yr", "scale": 1.0}
    assert c["inv_energy_overnight"] == {"unit": "EUR/t", "scale": 1.0}
    assert c["marginal_cost"] == {"unit": "EUR/t", "scale": 1.0}


# ── GK B2 follow-ups on #1565: a creation form's bus, and the Economics rows ──


def test_the_units_for_a_bus_serve_an_asset_not_yet_created(client, install_network, n):
    install_network(n)
    tonne_bus = n.generators.at[_tonne_generator(n), "bus"]
    energy_bus = n.links.at[_first(n, "links", "bus0", False), "bus0"]
    body = client.get(
        "/api/network/asset_schema/Generator/units/new", params={"bus": tonne_bus}
    ).json()
    assert body["units"]["p_nom"] == "t/h"
    assert body["costs"]["overnight_cost"] == {"unit": "EUR/(t/h)", "scale": 1.0}
    body = client.get(
        "/api/network/asset_schema/Generator/units/new", params={"bus": energy_bus}
    ).json()
    assert body["units"] == {} and body["costs"] == {}


def test_an_economics_row_says_whether_the_asset_sits_on_a_tonne_bus(n):
    from services.asset_schema.access import asset_cost_rows

    rows = {(r["component"], r["name"]): r["tonne_bus"] for r in asset_cost_rows(n)}
    assert rows[("Generator", _tonne_generator(n))] is True
    assert rows[("Link", _first(n, "links", "bus0", False))] is False


def test_the_units_for_a_bus_refuse_an_unknown_bus_and_an_empty_one(
    client, install_network, n
):
    install_network(n)
    url = "/api/network/asset_schema/Generator/units/new"
    assert client.get(url, params={"bus": "no-such-bus"}).status_code == 404
    assert client.get(url, params={"bus": ""}).status_code == 400
    assert client.get(url, params={"bus": "  "}).status_code == 400


def test_the_staged_concentrator_economics_row_pins_value_unit_and_flag(n):
    """Economics shows this store as 25 EUR/t: the backend sends the stored 25.0, basis EUR/MWh and the tonne flag."""
    from services.asset_schema.access import asset_cost_rows

    row = next(
        r for r in asset_cost_rows(n) if r["name"] == "coarse_ore_stockpile_new_2027"
    )
    assert (
        row["component"],
        row["basis"],
        row["tonne_bus"],
        row["upfront_per_unit"],
    ) == ("Store", "EUR/MWh", True, 25.0)


# ── The server refuses a cost write whose assumed basis is not the asset's (GK B2 F2″) ──


def _put_generator(client, name, changes, basis):
    row = next(
        r for r in client.get("/api/network/generators").json() if r["name"] == name
    )
    headers = {"X-Cost-Basis": basis} if basis else {}
    return client.put(
        f"/api/network/generators/{name}", json={**row, **changes}, headers=headers
    )


def _stored(client, name):
    return next(
        r for r in client.get("/api/network/generators").json() if r["name"] == name
    )


def test_a_cost_written_under_the_wrong_basis_is_refused_and_nothing_is_stored(
    client, install_network, n
):
    """The race and the restore case: the form assumed energy for an asset whose bus is in tonnes."""
    install_network(n)
    name = _tonne_generator(n)
    before = _stored(client, name)
    r = _put_generator(client, name, {"overnight_cost": 123.4}, "energy")
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert (d["error_kind"], d["claimed"], d["actual"]) == (
        "cost_basis_mismatch",
        "energy",
        "tonne",
    )
    assert _stored(client, name) == before


def test_a_matching_basis_and_a_write_without_one_are_accepted(
    client, install_network, n
):
    install_network(n)
    name = _tonne_generator(n)
    assert (
        _put_generator(client, name, {"overnight_cost": 123.4}, "tonne").status_code
        == 200
    )
    assert _stored(client, name)["overnight_cost"] == 123.4
    assert (
        _put_generator(client, name, {"overnight_cost": 55.5}, None).status_code == 200
    )


def test_moving_the_bus_and_pricing_in_one_save_is_checked_against_the_new_bus(
    client, install_network, n
):
    """A save that moves a tonne asset to an energy bus with costs typed per tonne is refused."""
    install_network(n)
    name = _tonne_generator(n)
    energy_bus = n.links.at[_first(n, "links", "bus0", False), "bus0"]
    before = _stored(client, name)
    r = _put_generator(
        client, name, {"bus": energy_bus, "overnight_cost": 123.4}, "tonne"
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["actual"] == "energy"
    assert "Save the bus change on its own first" in r.json()["detail"]["message"]
    assert _stored(client, name) == before
    ok = _put_generator(
        client, name, {"bus": energy_bus, "overnight_cost": 123400.0}, "energy"
    )
    assert ok.status_code == 200, ok.text


def test_an_unknown_basis_value_is_a_400(client, install_network, n):
    install_network(n)
    r = _put_generator(client, _tonne_generator(n), {}, "kg")
    assert r.status_code == 400


def test_a_snapshot_restore_that_puts_the_asset_back_on_a_tonne_bus_refuses_the_energy_claim(
    client, install_network, n
):
    """The project-restore case: the form loaded the asset on an energy bus, then a snapshot restore rewound the
    network to the tonne bus. The stale form's claim (energy) is refused and nothing is stored."""
    name = _tonne_generator(n)
    energy_bus = n.links.at[_first(n, "links", "bus0", False), "bus0"]
    install_network(
        n.copy(), name="restore05d"
    )  # the PUT below must not move the shared fixture's bus
    r = client.post("/api/projects/restore05d", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    assert (
        client.post(
            "/api/projects/restore05d/snapshots", json={"label": "tonne"}
        ).status_code
        == 200
    )
    moved = _put_generator(client, name, {"bus": energy_bus}, None)
    assert moved.status_code == 200, moved.text
    assert (
        _stored(client, name)["bus"] == energy_bus
    )  # the form now reads this asset as per energy

    snap_id = client.get("/api/projects/restore05d/snapshots").json()[0]["id"]
    restored = client.post(f"/api/projects/restore05d/snapshots/{snap_id}/restore")
    assert restored.status_code == 200, restored.text
    before = _stored(client, name)
    assert before["bus"] != energy_bus

    r = client.put(
        f"/api/network/generators/{name}",
        json={**before, "bus": before["bus"], "overnight_cost": 123400.0},
        headers={"X-Cost-Basis": "energy"},
    )
    assert r.status_code == 409, r.text
    assert (r.json()["detail"]["claimed"], r.json()["detail"]["actual"]) == (
        "energy",
        "tonne",
    )
    assert _stored(client, name) == before


def test_a_create_under_the_wrong_basis_is_refused_and_nothing_is_added(
    client, install_network, n
):
    """The create path (the creation form's POST) checks the claim too: a generator and a store on a tonne bus."""
    install_network(n)
    tonne_bus = n.generators.at[_tonne_generator(n), "bus"]
    r = client.post(
        "/api/network/generators",
        json={"name": "g05d", "bus": tonne_bus, "overnight_cost": 123400.0},
        headers={"X-Cost-Basis": "energy"},
    )
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert (d["error_kind"], d["claimed"], d["actual"]) == (
        "cost_basis_mismatch",
        "energy",
        "tonne",
    )
    names = [g["name"] for g in client.get("/api/network/generators").json()]
    assert "g05d" not in names

    r = client.post(
        "/api/network/stores",
        json={"name": "s05d", "bus": tonne_bus, "capital_cost": 5.0},
        headers={"X-Cost-Basis": "energy"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["actual"] == "tonne"
    assert "s05d" not in [s["name"] for s in client.get("/api/network/stores").json()]

    ok = client.post(
        "/api/network/stores",
        json={"name": "s05d", "bus": tonne_bus, "capital_cost": 5.0},
        headers={"X-Cost-Basis": "tonne"},
    )
    assert ok.status_code == 201, ok.text
