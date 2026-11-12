"""homer-parity 03c: the grid-extension break-even distance."""

from __future__ import annotations

import math

import pytest

from services.finance.grid_extension_breakeven import (
    BREAK_EVEN,
    GRID_CHEAPER_EVERYWHERE,
    NOT_ESTABLISHED,
    OFFGRID_CHEAPER_EVERYWHERE,
    grid_extension_break_even,
)


def test_equals_closed_form_on_linear_fixture():
    r = grid_extension_break_even(
        eur_per_km=60_000.0,
        offgrid_npc=2_500_000.0,
        grid_supply_npc=400_000.0,
        fixed_eur=100_000.0,
    )
    assert r["status"] == BREAK_EVEN
    assert r["km"] == pytest.approx((2_500_000 - 400_000 - 100_000) / 60_000, rel=1e-9)


def test_capex_only_default_names_its_basis():
    r = grid_extension_break_even(eur_per_km=50_000.0, offgrid_npc=1_000_000.0)
    assert r["km"] == pytest.approx(20.0, rel=1e-9)
    assert r["basis"] == "grid_extension_cost_only_vs_offgrid_npc"


@pytest.mark.parametrize("off", [1_000.0, 0.0])
def test_grid_cheaper_at_any_distance_is_no_break_even(off):
    r = grid_extension_break_even(
        eur_per_km=1.0, offgrid_npc=off, grid_supply_npc=1_000.0
    )
    assert r["status"] == GRID_CHEAPER_EVERYWHERE
    assert r["km"] is None and "No break-even" in r["message"]


def test_offgrid_cheaper_at_any_distance():
    r = grid_extension_break_even(
        eur_per_km=0.0, offgrid_npc=1_000.0, grid_supply_npc=10.0
    )
    assert r["status"] == OFFGRID_CHEAPER_EVERYWHERE and r["km"] is None
    far = grid_extension_break_even(eur_per_km=1.0, offgrid_npc=1e9, max_km=100.0)
    assert far["status"] == OFFGRID_CHEAPER_EVERYWHERE


def test_break_even_at_the_cap_is_found():
    r = grid_extension_break_even(eur_per_km=10.0, offgrid_npc=1000.0, max_km=100.0)
    assert r["status"] == BREAK_EVEN and r["km"] == pytest.approx(100.0)


@pytest.mark.parametrize(
    "kw",
    [
        {"eur_per_km": -1.0},
        {"eur_per_km": math.nan},
        {"eur_per_km": None},
        {"offgrid_npc": math.inf},
        {"fixed_eur": -5.0},
        {"max_km": 0.0},
        {"eur_per_km": True},
    ],
)
def test_bad_input_is_not_established_never_zero(kw):
    base = {"eur_per_km": 1.0, "offgrid_npc": 100.0}
    r = grid_extension_break_even(**{**base, **kw})
    assert r["status"] == NOT_ESTABLISHED and r["km"] is None and r["invalid"]
