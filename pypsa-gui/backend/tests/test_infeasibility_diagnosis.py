"""
Solver explainability: heuristic "why infeasible" hints + binding global-
constraint shadow prices. Pure analysis over a pypsa.Network — no solve needed.
"""
from __future__ import annotations

import queue

import pandas as pd
import pypsa

from services.solver_service import (
    SolverConfig,
    _diagnose_infeasibility,
    _log_global_constraint_shadow_prices,
)


def _drain(q: queue.SimpleQueue) -> list[str]:
    out = []
    while not q.empty():
        out.append(str(q.get()))
    return out


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    return n


def test_diagnose_capacity_shortfall():
    n = _net()
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=1000.0)
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=10.0)
    q: queue.SimpleQueue = queue.SimpleQueue()
    _diagnose_infeasibility(n, SolverConfig(voll=0.0), q)
    lines = _drain(q)
    assert any("Peak demand" in ln and "exceeds" in ln for ln in lines), lines


def test_diagnose_islanded_load():
    n = _net()
    n.add("Bus", "B1")
    n.add("Bus", "ISLAND")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="B1", carrier="gas", p_nom=5000.0)
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Load", "L2", bus="ISLAND", p_set=50.0)
    q: queue.SimpleQueue = queue.SimpleQueue()
    _diagnose_infeasibility(n, SolverConfig(voll=0.0), q)
    lines = _drain(q)
    assert any("ISLAND" in ln and "never be served" in ln for ln in lines), lines


def test_diagnose_global_constraint_mentioned():
    n = _net()
    n.add("Bus", "B")
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=5000.0)
    n.add("Load", "L", bus="B", p_set=100.0)
    n.add("GlobalConstraint", "co2", type="primary_energy",
          carrier_attribute="co2_emissions", sense="<=", constant=0.0)
    q: queue.SimpleQueue = queue.SimpleQueue()
    _diagnose_infeasibility(n, SolverConfig(voll=0.0), q)
    lines = _drain(q)
    assert any("Global constraint 'co2'" in ln for ln in lines), lines


def test_diagnose_fallback_when_no_obvious_cause():
    # Feasible-looking network (enough capacity, connected, no constraint) — the
    # diagnostic still runs (caller only invokes it on a confirmed-infeasible
    # solve) and emits the generic "check bounds/reserves/ramps" fallback.
    n = _net()
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=5000.0)
    n.add("Load", "L", bus="B", p_set=100.0)
    q: queue.SimpleQueue = queue.SimpleQueue()
    _diagnose_infeasibility(n, SolverConfig(voll=0.0), q)
    lines = _drain(q)
    assert any("No obvious structural cause" in ln for ln in lines), lines


def test_diagnose_no_capacity_hint_when_voll_set():
    # With a VOLL, demand-over-capacity is priced (lost load), not infeasible —
    # so the capacity-shortfall hint must NOT fire.
    n = _net()
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Load", "L", bus="B", p_set=1000.0)
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=10.0)
    q: queue.SimpleQueue = queue.SimpleQueue()
    _diagnose_infeasibility(n, SolverConfig(voll=1000.0), q)
    lines = _drain(q)
    assert not any("Peak demand" in ln for ln in lines), lines


def test_global_constraint_shadow_price_binding_and_not():
    n = _net()
    n.add("Bus", "B")
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("GlobalConstraint", "co2", type="primary_energy",
          carrier_attribute="co2_emissions", sense="<=", constant=100.0)
    # Non-binding (mu = 0) → no line.
    n.global_constraints.loc["co2", "mu"] = 0.0
    q: queue.SimpleQueue = queue.SimpleQueue()
    _log_global_constraint_shadow_prices(n, q)
    assert _drain(q) == []
    # Binding (mu ≠ 0) → a [GLOBAL-CONSTRAINT] line with the shadow price.
    n.global_constraints.loc["co2", "mu"] = -55.0
    q = queue.SimpleQueue()
    _log_global_constraint_shadow_prices(n, q)
    lines = _drain(q)
    assert any(ln.startswith("[GLOBAL-CONSTRAINT]") and "BINDS" in ln for ln in lines), lines
