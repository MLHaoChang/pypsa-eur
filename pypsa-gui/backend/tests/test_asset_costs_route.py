"""
Economics in Expert, E1 (plan 2026-10-14-economics-in-expert.md): `GET /api/network/asset_costs` lists every
investable asset with its upfront parts, the one view of what each asset costs.

WHAT THIS PINS. The parts are `upfront_parts`' (the same accessor the LP and the finance engine read), per unit of
the class's sizing variable: a battery's energy part scaled by `max_hours`, a line's per-km part by its length. An
asset priced only by a typed annual `capital_cost` is back-calculated at the global rate and flagged; an unpriced
asset is listed with no parts; a Load, which has no investment, is not listed. JSON has no inf, so a line that never
retires sends its lifetime as null.

The expected numbers are computed here from the inputs and PyPSA's own `annuity`, not from the code under test.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest
from pypsa.costs import annuity

RATE = 0.07   # the write path's fallback global rate


def _network() -> pypsa.Network:
    from services.asset_schema.derive import apply_parts

    n = pypsa.Network()
    n.set_snapshots(pd.DatetimeIndex(["2025-01-01"], name="snapshot"))
    n.add("Bus", "a", x=10.0, y=50.0)
    n.add("Bus", "b", x=11.0, y=50.0)
    n.add("Generator", "ocgt", bus="a", carrier="gas", p_nom=50.0, overnight_cost=500_000.0, lifetime=25.0)
    n.add("Generator", "legacy", bus="a", carrier="coal", p_nom=10.0, capital_cost=80_000.0, lifetime=30.0)
    n.add("Generator", "free", bus="a", carrier="solar", p_nom=5.0)
    n.add("Generator", "forever", bus="a", carrier="hydro", p_nom=5.0, overnight_cost=1e6)    # lifetime inf
    n.add("StorageUnit", "bat", bus="a", carrier="battery", p_nom=20.0, max_hours=4.0)
    apply_parts(n, "StorageUnit", "bat", {
        "inv_power_overnight": 150_000.0, "inv_power_lifetime": 15.0, "inv_power_fom_share": 0.01,
        "inv_energy_overnight": 200_000.0, "inv_energy_lifetime": 15.0, "inv_energy_fom_share": 0.02,
    }, discount_rate=RATE)
    n.add("Line", "ab", bus0="a", bus1="b", s_nom=100.0, length=50.0, x=0.1, lifetime=40.0)
    apply_parts(n, "Line", "ab", {"inv_per_km_overnight": 400.0, "inv_per_km_fom_share": 0.02}, discount_rate=RATE)
    n.add("Load", "demand", bus="a", p_set=10.0)
    return n


@pytest.fixture
def costs(client, install_network):
    install_network(_network())
    r = client.get("/api/network/asset_costs")
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {(a["component"], a["name"]): a for a in body["assets"]}


def test_a_two_part_battery_lists_both_parts_per_mw(costs):
    _, rows = costs
    bat = rows[("StorageUnit", "bat")]
    parts = {p["name"]: p for p in bat["parts"]}
    assert parts["power"]["upfront_per_unit"] == pytest.approx(150_000.0)
    assert parts["energy"]["upfront_per_unit"] == pytest.approx(200_000.0 * 4.0)     # per MWh × max_hours
    assert bat["basis"] == "EUR/MW"
    assert bat["upfront_per_unit"] == pytest.approx(950_000.0)
    assert bat["upfront_total"] == pytest.approx(950_000.0 * 20.0)


def test_a_line_part_is_per_mva_at_its_length(costs):
    _, rows = costs
    line = rows[("Line", "ab")]
    [part] = line["parts"]
    assert part["upfront_per_unit"] == pytest.approx(400.0 * 50.0)
    assert part["lifetime"] == 40.0
    assert line["basis"] == "EUR/MVA"
    assert line["capital_cost"] == pytest.approx(400.0 * 50.0 * annuity(RATE, 40.0), rel=1e-9)


def test_a_typed_overnight_cost_is_one_part(costs):
    _, rows = costs
    [part] = rows[("Generator", "ocgt")]["parts"]
    assert part == {"name": "investment", "upfront_per_unit": 500_000.0, "lifetime": 25.0, "fom_share": None,
                    "derived_from_capital_cost": False}


def test_a_bare_capital_cost_is_back_calculated_and_flagged(costs):
    body, rows = costs
    [part] = rows[("Generator", "legacy")]["parts"]
    assert part["derived_from_capital_cost"] is True
    assert part["upfront_per_unit"] == pytest.approx(80_000.0 / annuity(body["discount_rate"], 30.0), rel=1e-9)


def test_an_unpriced_asset_is_listed_without_parts(costs):
    _, rows = costs
    free = rows[("Generator", "free")]
    assert free["parts"] == [] and free["upfront_per_unit"] is None and free["upfront_total"] is None


def test_a_part_that_never_retires_sends_its_lifetime_as_null_not_inf(costs):
    _, rows = costs
    [part] = rows[("Generator", "forever")]["parts"]
    assert part["lifetime"] is None and part["upfront_per_unit"] == 1e6


def test_a_load_has_no_investment_and_is_not_listed(costs):
    _, rows = costs
    assert not any(cls == "Load" for cls, _ in rows)


def test_the_rows_are_strict_json_whatever_the_response_encoder():
    """The rows hold no inf or NaN themselves (a strict `json.dumps` passes), so no encoder has to fix them."""
    import json

    from services.asset_schema.access import asset_cost_rows

    json.dumps(asset_cost_rows(_network(), discount_rate=RATE), allow_nan=False)


def test_each_row_carries_its_assets_provenance(client, install_network):
    """The table's chips come with the rows (one request, not one per asset): an Expert edit stamps
    `customised` and the listing shows it."""
    install_network(_network())
    r = client.put("/api/network/generators/ocgt", json={"name": "ocgt", "bus": "a", "overnight_cost": 450_000.0})
    assert r.status_code == 200, r.text
    rows = {a["name"]: a for a in client.get("/api/network/asset_costs").json()["assets"]}
    assert rows["ocgt"]["provenance"]["overnight_cost"]["status"] == "customised"
    assert rows["free"]["provenance"] == {}
