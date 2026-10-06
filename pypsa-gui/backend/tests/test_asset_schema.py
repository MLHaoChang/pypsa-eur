"""
The asset parameter schema, S0 core (plan `docs/superpowers/plans/2026-10-05-asset-schema-s0.md`).

WHAT THIS PINS. A battery's investment has two parts with different lifetimes: the power part (inverter,
per MW) and the energy part (storage block, per MWh, scaled by `max_hours`). PyPSA 1.1.2 can carry only one
`overnight_cost` and one `lifetime` per asset, and when `overnight_cost` is set it IGNORES `capital_cost`
(`pypsa.costs.periodized_cost`). So a two-part battery keeps its parts in their own columns, leaves PyPSA's
`overnight_cost` empty, and carries a derived two-annuity `capital_cost`. Every reader that needs the upfront
cost (reports, the capex budget, the finance engine) reads the parts, never a single-lifetime back-calculation.

The expected numbers are computed with PyPSA's own `annuity`, not the code under test.
"""
from __future__ import annotations

import math

import pandas as pd
import pypsa
import pytest
from pypsa.costs import annuity as pypsa_annuity

RATE = 0.07
INVERTER_EUR_PER_MW = 213_900.0   # 213.9 EUR/kW
INVERTER_LIFE = 10.0
STORAGE_EUR_PER_MWH = 189_900.0   # 189.9 EUR/kWh
STORAGE_LIFE = 25.0
HOURS = 4.0
INVERTER_FOM_SHARE = 0.0034

PARTS = {
    "inv_power_overnight": INVERTER_EUR_PER_MW,
    "inv_power_lifetime": INVERTER_LIFE,
    "inv_power_fom_share": INVERTER_FOM_SHARE,
    "inv_energy_overnight": STORAGE_EUR_PER_MWH,
    "inv_energy_lifetime": STORAGE_LIFE,
    "inv_energy_fom_share": 0.0,
}


def _two_annuity(rate: float = RATE, hours: float = HOURS) -> float:
    return (INVERTER_EUR_PER_MW * pypsa_annuity(rate, INVERTER_LIFE)
            + hours * STORAGE_EUR_PER_MWH * pypsa_annuity(rate, STORAGE_LIFE))


def _battery_network(**parts) -> pypsa.Network:
    """One bus, one full-year snapshot (horizon factor 1), one extendable battery carrying `parts`."""
    from services.asset_schema.derive import apply_parts

    n = pypsa.Network()
    n.set_snapshots(pd.DatetimeIndex(["2025-01-01"], name="snapshot"))
    n.snapshot_weightings.loc[:, :] = 8760.0
    n.add("Bus", "site")
    n.add("StorageUnit", "battery", bus="site", p_nom_extendable=True, max_hours=HOURS)
    apply_parts(n, "StorageUnit", "battery", {**PARTS, **parts}, discount_rate=RATE)
    return n


# ── one annuity ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rate", [0.0, 0.01, 0.07, 0.15])
@pytest.mark.parametrize("life", [1.0, 10.0, 25.0, 40.0])
def test_the_gui_annuity_is_pypsas_annuity(rate, life):
    from services.solver.periodized_costs import _annuity

    assert _annuity(rate, life) == pytest.approx(pypsa_annuity(rate, life), rel=1e-12, abs=1e-15)


# ── derive ─────────────────────────────────────────────────────────────────

def test_a_two_part_battery_derives_the_two_annuity_capital_cost():
    from services.asset_schema.derive import derive_composite

    d = derive_composite("StorageUnit", PARTS, max_hours=HOURS, discount_rate=RATE)

    assert d["capital_cost"] == pytest.approx(_two_annuity(), rel=1e-12)
    # about 95.7 EUR/kW/yr on the library's numbers, as the assessment computed
    assert d["capital_cost"] / 1000 == pytest.approx(95.7, abs=0.1)
    assert d["fom_cost"] == pytest.approx(INVERTER_FOM_SHARE * INVERTER_EUR_PER_MW)
    assert d["lifetime"] == STORAGE_LIFE      # the retiring part; the inverter is a replacement
    assert math.isnan(d["overnight_cost"])     # left empty, or PyPSA would ignore capital_cost


def test_the_derived_cost_is_not_the_blended_single_annuity():
    """The defect plan C1 v1.2 would have shipped: one blended overnight cost over the storage life."""
    from services.asset_schema.derive import derive_composite

    blended = (INVERTER_EUR_PER_MW + HOURS * STORAGE_EUR_PER_MWH) * pypsa_annuity(RATE, STORAGE_LIFE)
    d = derive_composite("StorageUnit", PARTS, max_hours=HOURS, discount_rate=RATE)

    assert d["capital_cost"] > blended * 1.1


def test_duration_and_rate_both_move_the_derived_cost():
    from services.asset_schema.derive import derive_composite

    base = derive_composite("StorageUnit", PARTS, max_hours=HOURS, discount_rate=RATE)["capital_cost"]
    longer = derive_composite("StorageUnit", PARTS, max_hours=2 * HOURS, discount_rate=RATE)["capital_cost"]
    dearer = derive_composite("StorageUnit", PARTS, max_hours=HOURS, discount_rate=0.10)["capital_cost"]

    assert longer == pytest.approx(_two_annuity(hours=2 * HOURS), rel=1e-12)
    assert dearer == pytest.approx(_two_annuity(rate=0.10), rel=1e-12)
    assert longer > base and dearer > base


def test_a_priced_part_without_a_lifetime_is_refused():
    from services.asset_schema.derive import derive_composite

    with pytest.raises(ValueError, match="lifetime"):
        derive_composite("StorageUnit", {**PARTS, "inv_energy_lifetime": None},
                         max_hours=HOURS, discount_rate=RATE)


def test_a_class_without_composite_parts_is_refused():
    from services.asset_schema.derive import derive_composite

    with pytest.raises(ValueError, match="Generator"):
        derive_composite("Generator", PARTS, max_hours=HOURS, discount_rate=RATE)


# ── access ─────────────────────────────────────────────────────────────────

def test_upfront_parts_of_a_two_part_battery():
    from services.asset_schema.access import upfront_parts, upfront_per_unit

    n = _battery_network()
    parts = upfront_parts(n, "StorageUnit", "battery")

    assert [p.name for p in parts] == ["power", "energy"]
    assert parts[0].upfront_per_unit == pytest.approx(INVERTER_EUR_PER_MW)
    assert parts[1].upfront_per_unit == pytest.approx(HOURS * STORAGE_EUR_PER_MWH)   # per MW of power
    assert parts[0].lifetime == INVERTER_LIFE and parts[1].lifetime == STORAGE_LIFE
    assert not any(p.derived_from_capital_cost for p in parts)
    assert upfront_per_unit(n, "StorageUnit")["battery"] == pytest.approx(
        INVERTER_EUR_PER_MW + HOURS * STORAGE_EUR_PER_MWH)


def test_upfront_parts_of_a_single_part_asset_is_its_typed_overnight_cost():
    from services.asset_schema.access import upfront_parts

    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Generator", "pv", bus="site", overnight_cost=482_500.0, lifetime=40.0)
    parts = upfront_parts(n, "Generator", "pv")

    assert len(parts) == 1 and parts[0].upfront_per_unit == 482_500.0
    assert parts[0].lifetime == 40.0 and not parts[0].derived_from_capital_cost


def test_a_capital_cost_only_asset_is_back_calculated_and_flagged():
    from services.asset_schema.access import upfront_parts

    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Generator", "gas", bus="site", capital_cost=50_000.0, lifetime=30.0)
    parts = upfront_parts(n, "Generator", "gas", discount_rate=RATE)

    assert len(parts) == 1 and parts[0].derived_from_capital_cost
    assert parts[0].upfront_per_unit == pytest.approx(50_000.0 / pypsa_annuity(RATE, 30.0))


def test_an_unpriced_asset_has_no_upfront_parts():
    from services.asset_schema.access import upfront_parts

    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Generator", "free", bus="site")

    assert upfront_parts(n, "Generator", "free") is None


# ── the solve-time fill ────────────────────────────────────────────────────

def test_the_solve_fill_rederives_with_the_current_discount_rate_and_reverts():
    from services.solver_service import SolverConfig, fill_periodized_cost_defaults

    n = _battery_network()
    stored = float(n.storage_units.at["battery", "capital_cost"])
    revert = fill_periodized_cost_defaults(n, SolverConfig(discount_rate=0.10))
    try:
        during = float(n.storage_units.at["battery", "capital_cost"])
        lp = float(n.c["StorageUnit"].periodized_cost.sel(name="battery"))   # what the LP charges
    finally:
        revert()

    assert during == pytest.approx(_two_annuity(rate=0.10), rel=1e-9)
    assert lp == pytest.approx(_two_annuity(rate=0.10) + INVERTER_FOM_SHARE * INVERTER_EUR_PER_MW, rel=1e-9)
    assert float(n.storage_units.at["battery", "capital_cost"]) == stored


def test_an_asset_discount_rate_overrides_the_global_one_in_the_fill():
    from services.solver_service import SolverConfig, fill_periodized_cost_defaults

    n = _battery_network()
    n.storage_units.at["battery", "discount_rate"] = 0.05
    revert = fill_periodized_cost_defaults(n, SolverConfig(discount_rate=0.10))
    try:
        during = float(n.storage_units.at["battery", "capital_cost"])
    finally:
        revert()

    assert during == pytest.approx(_two_annuity(rate=0.05), rel=1e-9)


def test_upfront_cost_series_reads_the_parts_not_a_single_lifetime_back_calculation():
    from services.solver_service import SolverConfig, upfront_cost_series, with_periodized_cost_defaults

    n = _battery_network()
    with with_periodized_cost_defaults(n, SolverConfig(discount_rate=RATE), for_back_calculation=True):
        upfront = upfront_cost_series(n, "StorageUnit")["battery"]

    assert upfront == pytest.approx(INVERTER_EUR_PER_MW + HOURS * STORAGE_EUR_PER_MWH)


def test_a_solve_charges_the_two_annuity_cost(tmp_path):
    """End to end: a real solve charges the battery its two-annuity cost plus FOM, not a blended annuity."""
    from services.solver_service import SolverConfig, fill_periodized_cost_defaults

    n = _battery_network()
    n.add("Generator", "grid", bus="site", p_nom=100.0, marginal_cost=0.0)
    n.add("Load", "load", bus="site", p_set=10.0)
    revert = fill_periodized_cost_defaults(n, SolverConfig(discount_rate=RATE))
    try:
        n.optimize(solver_name="highs")
        coef = float(n.c["StorageUnit"].periodized_cost.sel(name="battery"))
    finally:
        revert()

    assert coef == pytest.approx(_two_annuity() + INVERTER_FOM_SHARE * INVERTER_EUR_PER_MW, rel=1e-9)


# ── the write path ─────────────────────────────────────────────────────────

def test_creating_a_battery_with_parts_through_the_api_derives_its_costs(client, install_network):
    n = pypsa.Network()
    n.add("Bus", "site")
    install_network(n)

    r = client.post("/api/network/storage_units", json={
        "name": "battery", "bus": "site", "p_nom_extendable": True, "max_hours": HOURS, **PARTS})
    assert r.status_code == 201, r.text

    row = {s["name"]: s for s in client.get("/api/network/storage_units").json()}["battery"]
    assert row["capital_cost"] == pytest.approx(_two_annuity(), rel=1e-9)
    assert row["lifetime"] == STORAGE_LIFE
    assert row["overnight_cost"] is None or math.isnan(row["overnight_cost"])
    assert row["inv_power_overnight"] == INVERTER_EUR_PER_MW
    assert row["inv_energy_lifetime"] == STORAGE_LIFE


def test_updating_duration_alone_rederives_the_cost(client, install_network):
    n = pypsa.Network()
    n.add("Bus", "site")
    install_network(n)
    client.post("/api/network/storage_units", json={
        "name": "battery", "bus": "site", "p_nom_extendable": True, "max_hours": HOURS, **PARTS})

    r = client.put("/api/network/storage_units/battery", json={"name": "battery", "bus": "site",
                                                                "max_hours": 2 * HOURS})
    assert r.status_code == 200, r.text

    row = {s["name"]: s for s in client.get("/api/network/storage_units").json()}["battery"]
    assert row["capital_cost"] == pytest.approx(_two_annuity(hours=2 * HOURS), rel=1e-9)


def test_a_battery_without_parts_is_written_exactly_as_before(client, install_network):
    n = pypsa.Network()
    n.add("Bus", "site")
    install_network(n)

    r = client.post("/api/network/storage_units", json={
        "name": "legacy", "bus": "site", "capital_cost": 80_000.0, "max_hours": HOURS})
    assert r.status_code == 201, r.text

    row = {s["name"]: s for s in client.get("/api/network/storage_units").json()}["legacy"]
    assert row["capital_cost"] == 80_000.0


def test_the_parts_survive_a_netcdf_round_trip(tmp_path):
    n = _battery_network()
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    m = pypsa.Network(path)

    for col, value in PARTS.items():
        assert float(m.storage_units.at["battery", col]) == pytest.approx(value)
    assert float(m.storage_units.at["battery", "capital_cost"]) == pytest.approx(_two_annuity(), rel=1e-9)


def test_a_priced_part_without_a_lifetime_is_a_422_and_leaves_the_asset_untouched(client, install_network):
    """Validated before the remove + re-add of an update, so a bad edit cannot delete the asset."""
    n = pypsa.Network()
    n.add("Bus", "site")
    install_network(n)
    client.post("/api/network/storage_units", json={
        "name": "battery", "bus": "site", "p_nom_extendable": True, "max_hours": HOURS, **PARTS})

    r = client.put("/api/network/storage_units/battery", json={
        "name": "battery", "bus": "site", "inv_energy_lifetime": None})
    assert r.status_code == 422, r.text
    assert "lifetime" in r.text

    row = {s["name"]: s for s in client.get("/api/network/storage_units").json()}["battery"]
    assert row["capital_cost"] == pytest.approx(_two_annuity(), rel=1e-9)
    assert row["inv_energy_lifetime"] == STORAGE_LIFE


def test_creating_with_a_priced_part_and_no_lifetime_is_a_422(client, install_network):
    n = pypsa.Network()
    n.add("Bus", "site")
    install_network(n)

    r = client.post("/api/network/storage_units", json={
        "name": "battery", "bus": "site", "max_hours": HOURS,
        "inv_power_overnight": INVERTER_EUR_PER_MW})
    assert r.status_code == 422, r.text
    assert "battery" not in {s["name"] for s in client.get("/api/network/storage_units").json()}


# ── the capex budget ───────────────────────────────────────────────────────

def test_the_capex_budget_charges_a_battery_its_upfront_parts():
    from services.solver.objective import budget_coefficient

    n = _battery_network()
    coef, derived = budget_coefficient(n, "StorageUnit", "battery", RATE)

    assert coef == pytest.approx(INVERTER_EUR_PER_MW + HOURS * STORAGE_EUR_PER_MWH)
    assert not derived


def test_the_capex_budget_never_uses_an_annualised_cost_as_upfront():
    """The old fallback put `capital_cost` (EUR/MW/yr) straight into a budget of EUR."""
    from services.solver.objective import budget_coefficient

    n = pypsa.Network()
    n.add("Bus", "site")
    n.add("Generator", "gas", bus="site", capital_cost=50_000.0, lifetime=30.0, p_nom_extendable=True)
    coef, derived = budget_coefficient(n, "Generator", "gas", RATE)

    assert derived
    assert coef == pytest.approx(50_000.0 / pypsa_annuity(RATE, 30.0))
