"""The CO2 price reaches an emitter whose marginal_cost is a time series.

PyPSA reads `generators_t.marginal_cost` wherever a generator has a column
there and ignores the scalar. `_apply_modelling_assumptions` step 2 used to:

  * (uniform `co2_price`) bump only the SCALAR, so an emitter with a cost
    series silently lost its CO2 cost;
  * (`co2_price_per_period`) write scalar base + surcharge into the series
    table, overwriting the user's own series.

Both now add the surcharge onto the dense cost — the series where there is
one, the scalar elsewhere — and `restore` puts every value back exactly
(gatekeeper finding on ticket 03, 2026-10-20).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa

from services.solver.assumptions import _apply_modelling_assumptions
from services.solver_service import SolverConfig

# 0.2 tCO2/MWh_th at efficiency 0.5 and 50 EUR/tCO2 → 20 EUR/MWh_el.
SURCHARGE = 0.2 * 50.0 / 0.5


def _dense(n) -> pd.DataFrame:
    return n.get_switchable_as_dense("Generator", "marginal_cost")


def _add(n):
    n.add("Bus", "B")
    n.add("Load", "L", bus="B", p_set=50.0)
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Generator", "G_series", bus="B", carrier="gas", p_nom=100.0,
          marginal_cost=10.0, efficiency=0.5)
    n.add("Generator", "G_scalar", bus="B", carrier="gas", p_nom=100.0,
          marginal_cost=10.0, efficiency=0.5)


def _single():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    _add(n)
    n.generators_t.marginal_cost["G_series"] = [1.0, 2.0, 3.0]
    return n


def _multi():
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=2, freq="h")],
        names=["period", "timestep"])
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2040]
    _add(n)
    n.generators_t.marginal_cost["G_series"] = [1.0, 2.0, 3.0, 4.0]
    return n


def _cfg(**kw) -> SolverConfig:
    return SolverConfig(**kw)


def test_a_uniform_co2_price_reaches_a_cost_series():
    n = _single()
    restore, _ = _apply_modelling_assumptions(n, _cfg(co2_price=50.0), lambda m: None)
    dense = _dense(n)
    assert np.allclose(dense["G_series"], [1 + SURCHARGE, 2 + SURCHARGE, 3 + SURCHARGE])
    assert np.allclose(dense["G_scalar"], 10 + SURCHARGE)
    restore()
    assert list(n.generators_t.marginal_cost["G_series"]) == [1.0, 2.0, 3.0]
    assert "G_scalar" not in n.generators_t.marginal_cost.columns
    assert float(n.generators.at["G_series", "marginal_cost"]) == 10.0
    assert float(n.generators.at["G_scalar", "marginal_cost"]) == 10.0


def test_a_per_period_co2_price_adds_onto_the_users_series():
    n = _multi()
    cfg = _cfg(co2_price=50.0, co2_price_per_period={2030: 50.0, 2040: 100.0})
    restore, _ = _apply_modelling_assumptions(n, cfg, lambda m: None)
    dense = _dense(n)
    assert np.allclose(dense["G_series"],
                       [1 + SURCHARGE, 2 + SURCHARGE, 3 + 2 * SURCHARGE, 4 + 2 * SURCHARGE])
    assert np.allclose(dense["G_scalar"],
                       [10 + SURCHARGE] * 2 + [10 + 2 * SURCHARGE] * 2)
    restore()
    assert list(n.generators_t.marginal_cost["G_series"]) == [1.0, 2.0, 3.0, 4.0]
    assert "G_scalar" not in n.generators_t.marginal_cost.columns
