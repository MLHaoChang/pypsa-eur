"""
S1c-2 (plan 2026-10-13-s1-expert-asset-cards.md): a Line's investment is typed per km.

WHAT THIS PINS. Line costs are published per MVA and km (PyPSA-Eur's technology data: "HVAC overhead",
EUR/MW/km). A line's upfront cost per MVA is that figure times its length, so a typed `overnight_cost` per MVA
goes stale the moment the length changes (an edit, "Recalculate from coordinates", a bus moved on the map, or
"Derive lengths from geometry"). The Line is therefore a one-part composite (`schema.COMPOSITE`): the part
`inv_per_km_*` is scaled by `length`, exactly as a battery's energy part is scaled by `max_hours`, and every
writer of `length` re-derives the cost columns.

The expected numbers are computed with PyPSA's own `annuity`, not the code under test.
"""
from __future__ import annotations

import math

import pandas as pd
import pypsa
import pytest
from pypsa.costs import annuity as pypsa_annuity

from tests.test_lengths_from_geometry import routed_project  # noqa: F401  (fixture)

RATE = 0.07                    # the write path's fallback global rate (network_crud._global_discount_rate)
PER_KM = 432.0                 # EUR/MVA/km, HVAC overhead
LIFE = 40.0
FOM_SHARE = 0.02
LENGTH = 100.0

PART = {"inv_per_km_overnight": PER_KM, "inv_per_km_fom_share": FOM_SHARE}   # the line's own lifetime: LIFE


def _annual(length: float = LENGTH, rate: float = RATE) -> float:
    return PER_KM * length * pypsa_annuity(rate, LIFE)


def _line_network(length: float = LENGTH) -> pypsa.Network:
    from services.asset_schema.derive import apply_parts

    n = pypsa.Network()
    n.set_snapshots(pd.DatetimeIndex(["2025-01-01"], name="snapshot"))
    n.snapshot_weightings.loc[:, :] = 8760.0
    n.add("Bus", "a", x=10.0, y=50.0)
    n.add("Bus", "b", x=11.0, y=50.0)
    n.add("Line", "ab", bus0="a", bus1="b", s_nom_extendable=True, length=length, x=0.1, lifetime=LIFE)
    apply_parts(n, "Line", "ab", PART, discount_rate=RATE)
    return n


# ── derive ─────────────────────────────────────────────────────────────────

def test_a_line_part_is_scaled_by_its_length():
    from services.asset_schema.derive import derive_composite

    d = derive_composite("Line", PART, scales={"length": LENGTH}, discount_rate=RATE, asset_lifetime=LIFE)
    assert d["capital_cost"] == pytest.approx(_annual(), rel=1e-12)
    assert d["fom_cost"] == pytest.approx(PER_KM * LENGTH * FOM_SHARE, rel=1e-12)
    assert d["lifetime"] == LIFE
    assert math.isnan(d["overnight_cost"])


def test_part_scales_reads_each_parts_scale_column():
    from services.asset_schema.derive import part_scales

    assert part_scales("Line", {"length": 12.5, "max_hours": 4.0}) == {"length": 12.5}
    assert part_scales("StorageUnit", {"length": 12.5, "max_hours": 4.0}) == {"max_hours": 4.0}
    assert part_scales("Generator", {"length": 12.5}) == {}


def test_upfront_parts_of_a_line_are_per_mva():
    from services.asset_schema.access import upfront_parts

    [part] = upfront_parts(_line_network(), "Line", "ab")
    assert part.upfront_per_unit == pytest.approx(PER_KM * LENGTH)
    assert part.lifetime == LIFE


def test_the_solve_fill_rederives_a_line_with_its_length_and_reverts():
    from services.solver_service import SolverConfig, fill_periodized_cost_defaults

    n = _line_network()
    stored = float(n.lines.at["ab", "capital_cost"])
    revert = fill_periodized_cost_defaults(n, SolverConfig(discount_rate=0.10))
    try:
        during = float(n.lines.at["ab", "capital_cost"])
    finally:
        revert()

    assert during == pytest.approx(_annual(rate=0.10), rel=1e-9)
    assert float(n.lines.at["ab", "capital_cost"]) == stored


# ── the write path ─────────────────────────────────────────────────────────

def _two_buses(install_network) -> None:
    n = pypsa.Network()
    n.add("Bus", "a", x=10.0, y=50.0)
    n.add("Bus", "b", x=11.0, y=50.0)
    install_network(n)


def _row(client, name: str = "ab") -> dict:
    return {r["name"]: r for r in client.get("/api/network/lines").json()}[name]


def test_creating_a_line_with_a_per_km_part_derives_its_costs(client, install_network):
    _two_buses(install_network)
    r = client.post("/api/network/lines", json={
        "name": "ab", "bus0": "a", "bus1": "b", "length": LENGTH, "lifetime": LIFE, "s_nom_extendable": True, **PART})
    assert r.status_code == 201, r.text

    row = _row(client)
    assert row["capital_cost"] == pytest.approx(_annual(), rel=1e-9)
    assert row["fom_cost"] == pytest.approx(PER_KM * LENGTH * FOM_SHARE, rel=1e-9)
    assert row["lifetime"] == LIFE
    assert row["inv_per_km_overnight"] == PER_KM


def test_editing_the_length_alone_rederives_the_cost(client, install_network):
    _two_buses(install_network)
    client.post("/api/network/lines", json={
        "name": "ab", "bus0": "a", "bus1": "b", "length": LENGTH, "lifetime": LIFE, "s_nom_extendable": True, **PART})

    r = client.put("/api/network/lines/ab", json={"name": "ab", "bus0": "a", "bus1": "b", "length": 2 * LENGTH})
    assert r.status_code == 200, r.text
    assert _row(client)["capital_cost"] == pytest.approx(_annual(length=2 * LENGTH), rel=1e-9)


def test_recalculating_lengths_from_coordinates_rederives_the_cost(client, install_network):
    _two_buses(install_network)
    client.post("/api/network/lines", json={
        "name": "ab", "bus0": "a", "bus1": "b", "length": LENGTH, "lifetime": LIFE, "s_nom_extendable": True, **PART})

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    row = _row(client)
    assert row["length"] != LENGTH                       # 1° of longitude at 50° N, ~72 km
    assert row["capital_cost"] == pytest.approx(_annual(length=row["length"]), rel=1e-9)


def test_a_line_without_a_part_is_written_exactly_as_before(client, install_network):
    _two_buses(install_network)
    r = client.post("/api/network/lines", json={
        "name": "ab", "bus0": "a", "bus1": "b", "length": LENGTH, "capital_cost": 1234.0})
    assert r.status_code == 201, r.text
    client.post("/api/network/lines/recalculate_lengths")
    assert _row(client)["capital_cost"] == 1234.0


def test_the_part_takes_the_lines_own_lifetime_and_an_unpriced_line_keeps_its_typed_one(client, install_network):
    """No `inv_per_km_lifetime` column: an existing line with no investment cost still sets its retirement."""
    from services.asset_schema.schema import part_columns

    assert part_columns("Line") == ["inv_per_km_overnight", "inv_per_km_fom_share"]
    _two_buses(install_network)
    client.post("/api/network/lines", json={"name": "ab", "bus0": "a", "bus1": "b", "length": LENGTH})
    r = client.put("/api/network/lines/ab", json={"name": "ab", "bus0": "a", "bus1": "b", "lifetime": 30.0})
    assert r.status_code == 200, r.text
    assert _row(client)["lifetime"] == 30.0


def test_a_priced_part_on_a_line_without_a_lifetime_is_a_422(client, install_network):
    """A line's lifetime defaults to inf (never retires), which cannot annualise an upfront cost."""
    _two_buses(install_network)
    r = client.post("/api/network/lines", json={
        "name": "ab", "bus0": "a", "bus1": "b", "length": LENGTH, "inv_per_km_overnight": PER_KM})
    assert r.status_code == 422, r.text
    assert "lifetime" in r.text


def test_a_bus_move_rederives_the_cost_of_every_touching_line():
    """The map's bus drag rewrites `length` through `_recompute_lengths_for_bus` (lock held by its caller)."""
    from services.network_geometry import _recompute_lengths_for_bus

    n = _line_network()
    n.buses.at["b", "x"] = 12.0                          # the drag
    _recompute_lengths_for_bus(n, "b")
    length = float(n.lines.at["ab", "length"])
    assert length != LENGTH
    assert float(n.lines.at["ab", "capital_cost"]) == pytest.approx(_annual(length=length), rel=1e-9)


def test_lengths_from_geometry_rederive_the_cost(client, routed_project):
    """`POST /lengths/from_geometry`, the map's explicit "Use geometry" act."""
    r = client.put("/api/network/lines/L1", json={"name": "L1", "bus0": "A", "bus1": "B", "lifetime": LIFE, **PART})
    assert r.status_code == 200, r.text

    r = client.post("/api/network/lengths/from_geometry", json={"keys": ["line:L1"]})
    assert r.status_code == 200, r.text
    row = _row(client, "L1")
    assert row["capital_cost"] == pytest.approx(_annual(length=row["length"]), rel=1e-9)
