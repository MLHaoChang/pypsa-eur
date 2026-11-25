"""The front's built-capacity units (DC 01 PR3, UX 30): read off each asset's
bus, never guessed. A tonne bus reads t/h (t for a Store); an unknown bus unit
reads None, which the panel shows with no unit."""

from __future__ import annotations

import pypsa

from services.adequacy import front as fr


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "ore", carrier="ore")
    n.buses.loc["ore", "unit"] = "t"
    n.add("Generator", "gt", bus="el")
    n.add("StorageUnit", "bess", bus="el")
    n.add("Store", "tank", bus="el")
    n.add("Store", "pile", bus="ore")
    n.add("Link", "crusher", bus0="ore", bus1="el")
    n.add("Generator", "mystery", bus="el")
    # A size bus that cannot be read: no unit is known.
    n.generators.loc["mystery", "bus"] = "nowhere"
    n.add("Line", "l1", bus0="el", bus1="el", x=0.1, r=0.01)
    return n


def test_units_follow_the_bus():
    keys = [
        "generators:gt",
        "storage_units:bess",
        "stores:tank",
        "stores:pile",
        "links:crusher",
        "generators:mystery",
        "lines:l1",
        "generators:gone",
        "nonsense:gt",
        "carriers:AC",
    ]
    assert fr.capacity_units(_net(), keys) == {
        "generators:gt": "MW",
        "storage_units:bess": "MW",
        "stores:tank": "MWh",
        "stores:pile": "t",
        "links:crusher": "t/h",
        "generators:mystery": None,
        "lines:l1": "MVA",
        "generators:gone": None,
        "nonsense:gt": None,
        "carriers:AC": None,
    }


def test_the_currency_is_the_finance_inputs():
    class Cfg:
        finance = {"currency": "GBP"}

    assert fr._currency(Cfg()) == "GBP"
    assert fr._currency(object()) == "EUR"

    class Odd:
        finance = {"currency": "EURO"}

    assert fr._currency(Odd()) == "EUR"
