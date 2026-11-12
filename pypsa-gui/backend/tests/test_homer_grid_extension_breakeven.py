"""homer-parity 03c: the grid-extension break-even distance (GK B conditions C1-C5)."""

from __future__ import annotations

import math

import pytest

from services.finance.grid_extension_breakeven import (
    BEYOND_MAX_KM,
    BREAK_EVEN,
    GRID_CHEAPER_EVERYWHERE,
    NOT_ESTABLISHED,
    OFFGRID_CHEAPER_EVERYWHERE,
    grid_extension_break_even,
)

B = {
    "currency": "EUR",
    "currency_year": 2025,
    "discount_rate": 0.07,
    "horizon_years": 20,
}


def run(**kw):
    base = dict(
        eur_per_km=60_000.0,
        offgrid_npc=2_500_000.0,
        grid_supply_npc=400_000.0,
        offgrid_basis=B,
        grid_basis={"currency": "EUR", "currency_year": 2025},
    )
    return grid_extension_break_even(**{**base, **kw})


def test_c1_closed_form_by_hand():
    # (2_500_000 - 400_000 - 100_000) / 60_000 = 2_000_000 / 60_000 = 100/3
    r = run(fixed_eur=100_000.0)
    assert r["status"] == BREAK_EVEN
    assert r["km"] == pytest.approx(100 / 3, rel=1e-9)


def test_c2_offgrid_cheaper_everywhere():
    r = run(grid_supply_npc=3_000_000.0)
    assert r["status"] == OFFGRID_CHEAPER_EVERYWHERE and r["km"] is None
    assert "No break-even" in r["message"]


def test_c2_grid_cheaper_everywhere():
    r = run(eur_per_km=0.0)
    assert r["status"] == GRID_CHEAPER_EVERYWHERE and r["km"] is None
    assert "No break-even" in r["message"]


def test_c2_beyond_max_km_reports_km():
    r = run(max_km=10.0)
    assert r["status"] == BEYOND_MAX_KM
    assert r["km"] == pytest.approx(2_100_000 / 60_000, rel=1e-9)
    assert run(max_km=35.0)["status"] == BREAK_EVEN  # km* = 35 exactly: not beyond
    assert run(max_km=34.9)["status"] == BEYOND_MAX_KM


def test_c2_boundary_grid_cost_equals_offgrid_is_break_even_at_zero():
    r = run(grid_supply_npc=2_500_000.0)
    assert r["status"] == BREAK_EVEN and r["km"] == 0.0
    r = run(grid_supply_npc=2_000_000.0, fixed_eur=500_000.0, eur_per_km=0.0)
    assert r["status"] == BREAK_EVEN and r["km"] == 0.0


def test_c3_grid_supply_npc_is_required():
    r = run(grid_supply_npc=None)
    assert r["status"] == NOT_ESTABLISHED and r["reason"] == "grid_supply_npc_required"
    with pytest.raises(TypeError):
        grid_extension_break_even(
            eur_per_km=1.0, offgrid_npc=1.0, offgrid_basis=B, grid_basis=B
        )


def test_c4_matching_basis_gives_a_value_and_echoes_it():
    r = run()
    assert r["km"] == pytest.approx(2_100_000 / 60_000, rel=1e-9)
    assert r["offgrid_basis"] == B


@pytest.mark.parametrize(
    "grid_basis,reason",
    [
        ({"currency": "EUR", "currency_year": 2023}, "basis_mismatch"),
        ({"currency": "USD", "currency_year": 2025}, "basis_mismatch"),
        ({"currency": "EUR"}, "basis_missing"),
        (None, "basis_missing"),
    ],
)
def test_c4_basis_mismatch_or_missing_refuses(grid_basis, reason):
    r = run(grid_basis=grid_basis)
    assert r["status"] == NOT_ESTABLISHED and r["reason"] == reason and r["km"] is None


@pytest.mark.parametrize(
    "kw",
    [
        {"eur_per_km": math.nan},
        {"eur_per_km": math.inf},
        {"eur_per_km": -1.0},
        {"offgrid_npc": math.nan},
        {"offgrid_npc": -math.inf},
        {"grid_supply_npc": math.inf},
        {"grid_supply_npc": math.nan},
        {"fixed_eur": math.inf},
        {"fixed_eur": -5.0},
        {"max_km": math.inf},
        {"max_km": 0.0},
        {"max_km": math.nan},
        {"eur_per_km": True},
    ],
)
def test_c5_bad_numbers_are_not_established_never_zero(kw):
    r = run(**kw)
    assert r["status"] == NOT_ESTABLISHED and r["km"] is None and r["invalid"]


@pytest.mark.parametrize(
    "kw",
    [
        {"eur_per_km": 1e-320},
        {"offgrid_npc": 1.7e308, "grid_supply_npc": -1.7e308},
        {"offgrid_npc": -1.7e308, "grid_supply_npc": 1.7e308},
    ],
)
def test_result_must_be_finite_never_inf_or_nan(kw):
    r = run(**kw)
    assert r["status"] == NOT_ESTABLISHED and r["km"] is None
    assert r["reason"] == "result_not_finite"


@pytest.mark.parametrize("key", ["currency", "currency_year"])
def test_offgrid_basis_missing_a_key_is_basis_missing(key):
    r = run(offgrid_basis={k: v for k, v in B.items() if k != key})
    assert r["status"] == NOT_ESTABLISHED and r["reason"] == "basis_missing"
