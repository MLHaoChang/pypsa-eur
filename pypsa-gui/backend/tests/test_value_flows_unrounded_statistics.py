"""
The asset cost lines are unrounded (IC engine ask 4, owner 2026-10-13).

`results.value_flows._asset_costs` reads `n.statistics.capex / fom / opex`,
which PyPSA rounds to five decimals unless told otherwise. The decision
study's FOM therefore sat 7.2e-7 EUR/yr off its own asset economics (the
`fom_rounded_by_pypsa_statistics` rows of `tests/fixtures/u2_deltas.json`).
The lines now carry the full value.

Written red first: the lines were rounded.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest


def _net():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", "site")
    n.add("StorageUnit", "battery", bus="site", p_nom=1.0, p_nom_opt=1.2345678901,
          p_nom_extendable=True, capital_cost=12_345.678901234, fom_cost=557.3585892776363,
          max_hours=2.0, marginal_cost=1.234567891)
    n.storage_units_t.p = pd.DataFrame({"battery": [0.1111111, -0.2222222, 0.3333333]},
                                       index=n.snapshots)
    n.storage_units_t.p_dispatch = n.storage_units_t.p.clip(lower=0)
    return n


def test_the_asset_cost_lines_are_not_rounded():
    from services.commercial import participants as P
    from services.results import value_flows as VF
    from services.solver_service import SolverConfig, with_periodized_cost_defaults

    n, cfg = _net(), SolverConfig()
    sides = P.MeterSides(site={"site"}, grid=set(), bypass=set(), meter_links=set())
    [cost] = VF._asset_costs(n, cfg, None, sides)
    with with_periodized_cost_defaults(n, cfg, for_back_calculation=True):
        want_fom = float(n.statistics.fom(groupby=False, round=0).sum())
        want_capex = float(n.statistics.capex(groupby=False, round=0).sum())
        want_opex = float(n.statistics.opex(groupby=False, round=0).sum())
    assert round(want_fom, 5) != want_fom                     # the probe has digits to lose
    assert sum(cost.fom.values()) == pytest.approx(want_fom, rel=1e-15)
    assert sum(cost.capex.values()) == pytest.approx(want_capex, rel=1e-15)
    assert sum(cost.opex.values()) == pytest.approx(want_opex, rel=1e-15)
