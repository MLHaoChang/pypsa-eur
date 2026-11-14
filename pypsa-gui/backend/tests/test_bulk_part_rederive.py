"""
TCU 08: a bulk edit of a composite cost part re-derives the asset's cost columns, exactly as the single edit does.

A single edit (PUT) runs `derive.apply_parts`, so `capital_cost`, `fom_cost` and `lifetime` (hashed, LP-read
columns) follow the parts. The bulk route wrote the raw part columns only: the solve staging re-derived
`capital_cost` behind it, so the LP moved while the input fingerprint did not (old results read as current), and
`fom_cost` / `lifetime` never moved at all. GK B rulings: "a bulk write applies its side effects per target"
(#1466 D1), "a bulk path runs the same validators as the single edit" (#738).

Each case edits the same value through PUT on one network and through the bulk route on its twin, and compares
the derived columns; the bulk side also checks that the cost and the fingerprint moved.
"""
from __future__ import annotations

import math

import pytest

from tests.conftest import build_network

BULK = "/api/network/_bulk"

BATTERY = {
    "inv_power_overnight": 300_000.0, "inv_power_lifetime": 10.0, "inv_power_fom_share": 0.01,
    "inv_energy_overnight": 150_000.0, "inv_energy_lifetime": 20.0, "inv_energy_fom_share": 0.02,
}
LINE = {"inv_per_km_overnight": 432.0, "inv_per_km_fom_share": 0.02}
DERIVED = ("capital_cost", "fom_cost", "lifetime")


def _network():
    from services.asset_schema.derive import apply_parts

    n = build_network()
    n.add("Bus", "B2")
    for name in ("bess", "bess2"):
        n.add("StorageUnit", name, bus="B1", p_nom=10.0, max_hours=2.0, p_nom_extendable=True)
        apply_parts(n, "StorageUnit", name, BATTERY, discount_rate=0.07)
    n.add("Line", "ab", bus0="B1", bus1="B2", s_nom_extendable=True, length=100.0, x=0.1, lifetime=40.0)
    apply_parts(n, "Line", "ab", LINE, discount_rate=0.07)
    return n


def _fingerprint():
    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _network_input_fingerprint

    return _network_input_fingerprint(PyPSAService.get_active_context())


def _derived(n, cls_attr, name):
    df = getattr(n, cls_attr)
    return {c: float(df.at[name, c]) for c in DERIVED}


CASES = [
    # (class, attr, route, name, column, new value, the derived column the edit must move)
    ("StorageUnit", "storage_units", "storage_units", "bess", "inv_power_overnight", 500_000.0, "capital_cost"),
    ("StorageUnit", "storage_units", "storage_units", "bess", "inv_power_lifetime", 25.0, "lifetime"),
    ("StorageUnit", "storage_units", "storage_units", "bess", "inv_energy_fom_share", 0.05, "fom_cost"),
    ("Line", "lines", "lines", "ab", "inv_per_km_overnight", 600.0, "capital_cost"),
    ("Line", "lines", "lines", "ab", "inv_per_km_fom_share", 0.04, "fom_cost"),
]


@pytest.mark.parametrize("row_form", [False, True], ids=["names", "rows"])
@pytest.mark.parametrize("cls,attr,route,name,col,value,moves", CASES, ids=[c[4] for c in CASES])
def test_bulk_part_edit_rederives_like_put(client, install_network, cls, attr, route, name, col, value, moves,
                                           row_form):
    put_net = install_network(_network())
    buses = {"bus": "B1"} if cls == "StorageUnit" else {"bus0": "B1", "bus1": "B2"}
    r = client.put(f"/api/network/{route}/{name}", json={"name": name, **buses, col: value})
    assert r.status_code == 200, r.text
    expected = _derived(put_net, attr, name)

    n = install_network(_network())
    before = _derived(n, attr, name)
    fp_before = _fingerprint()
    body = ({"component_class": cls, "rows": [{"name": name, "updates": {col: value}}]} if row_form
            else {"component_class": cls, "names": [name], "updates": {col: value}})
    r = client.patch(BULK, json=body)
    assert r.status_code == 200, r.text

    after = _derived(n, attr, name)
    assert after[moves] != pytest.approx(before[moves])
    assert after == pytest.approx(expected, rel=1e-12)
    assert _fingerprint() != fp_before


def test_bulk_rederives_every_target_on_its_own_row(client, install_network):
    """Per target (#1466 D1): two batteries with different energy scales each get their own cost."""
    n = install_network(_network())
    n.storage_units.at["bess2", "max_hours"] = 4.0
    r = client.patch(BULK, json={"component_class": "StorageUnit", "names": ["bess", "bess2"],
                                 "updates": {"inv_energy_overnight": 200_000.0}})
    assert r.status_code == 200, r.text
    one, two = (float(n.storage_units.at[t, "capital_cost"]) for t in ("bess", "bess2"))
    from services.asset_schema.derive import derive_composite

    for t, got in (("bess", one), ("bess2", two)):
        row = n.storage_units.loc[t]
        want = derive_composite("StorageUnit", row, scales={"max_hours": float(row["max_hours"])},
                                discount_rate=0.07, asset_lifetime=row["lifetime"])["capital_cost"]
        assert got == pytest.approx(want, rel=1e-12)
    assert two > one


def test_bulk_scale_edit_rederives(client, install_network):
    """A bulk `max_hours` or `length` edit rescales the part, as the single edit does."""
    n = install_network(_network())
    su, line = float(n.storage_units.at["bess", "capital_cost"]), float(n.lines.at["ab", "capital_cost"])
    assert client.patch(BULK, json={"component_class": "StorageUnit", "names": ["bess"],
                                    "updates": {"max_hours": 4.0}}).status_code == 200
    assert client.patch(BULK, json={"component_class": "Line", "names": ["ab"],
                                    "updates": {"length": 200.0}}).status_code == 200
    assert float(n.storage_units.at["bess", "capital_cost"]) > su
    assert float(n.lines.at["ab", "capital_cost"]) == pytest.approx(2 * line, rel=1e-12)


def test_bulk_refuses_a_priced_part_without_a_lifetime_and_writes_nothing(client, install_network):
    """The single edit's validator, before the first write: a 422 leaves every target as it was."""
    n = install_network(_network())
    before = n.storage_units.copy()
    r = client.patch(BULK, json={"component_class": "StorageUnit", "names": ["bess2", "bess"],
                                 "updates": {"inv_power_lifetime": None}})
    assert r.status_code == 422, r.text
    assert "lifetime" in r.json()["detail"]
    cols = ["inv_power_lifetime", *DERIVED]
    assert n.storage_units[cols].equals(before[cols])


def test_bulk_on_an_unpriced_row_leaves_its_typed_cost(client, install_network):
    """A battery typed the old way (a bare capital_cost, no parts) keeps what the bulk wrote."""
    n = build_network()
    n.add("StorageUnit", "plain", bus="B1", p_nom=10.0, max_hours=2.0, capital_cost=1234.0)
    n = install_network(n)
    r = client.patch(BULK, json={"component_class": "StorageUnit", "names": ["plain"],
                                 "updates": {"capital_cost": 999.0}})
    assert r.status_code == 200, r.text
    assert float(n.storage_units.at["plain", "capital_cost"]) == 999.0
    assert not math.isnan(float(n.storage_units.at["plain", "capital_cost"]))
