# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""DC template review issue 03: genset CO2 is per MWh of electricity.

Hand check: gas carries 0.20 t per MWh of fuel; a genset at the library heat
rate (2.8 MWh fuel per MWh el) emits 0.20 * 2.8 = 0.56 t per MWh el, so 10 MW
for one hour is 5.6 t. With the default efficiency of 1 it was 2.0 t.
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from models.flex_archetypes import GensetSpec  # noqa: E402
from services.compare.support import _build_snapshot_weights  # noqa: E402
from services.compare.emissions import _compute_emissions_summary  # noqa: E402

HEAT_RATE = GensetSpec(power_mw=1).heat_rate_mwh_fuel_per_mwh_el  # 2.8


def _gas_gens(n):
    return [g for g in n.generators.index if n.generators.at[g, "carrier"] == "gas"]


@pytest.mark.parametrize("builder", [T.build_eh_datacenter, T.build_eh_datacenter_staged])
def test_gas_gensets_carry_efficiency_one_over_heat_rate(builder):
    n = builder()
    gens = _gas_gens(n)
    assert gens
    for g in gens:
        assert n.generators.at[g, "efficiency"] == pytest.approx(1.0 / HEAT_RATE)


def test_genset_co2_is_0_56_t_per_mwh_el():
    n = T.build_eh_datacenter()
    n.generators_t.p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    n.generators_t.p.loc[n.snapshots[0], "genset_1"] = 10.0  # 10 MW for one hour
    res = _compute_emissions_summary(n, [], False, True)
    w = float(_build_snapshot_weights(n, "generators").iloc[0])  # the week stands for a year
    assert res.by_carrier_kt["gas"].total == pytest.approx(5.6 * w / 1000.0)
    assert res.intensity_kg_per_mwh.total == pytest.approx(560.0)


def test_load_spec_heat_rate_is_used():
    from models.flex_archetypes import DataCentreLoadSpec  # noqa: F401
    n = T.build_eh_datacenter()
    assert n.generators.at["genset_1", "efficiency"] == pytest.approx(1 / 2.8)


def test_staged_grid_intensity_is_per_period_and_counted():
    n = T.build_eh_datacenter_staged()
    t = n.generators_t["eh_co2_t_per_mwh"]["grid_supply"]
    assert sorted(t.groupby(level=0).first().tolist()) == [0.22, 0.30, 0.35]
    n.generators_t.p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    n.generators_t.p["grid_supply"] = 1.0  # 1 MW every snapshot
    res = _compute_emissions_summary(n, [], True, True)
    w = _build_snapshot_weights(n, "generators")
    expect = float((w * t).sum()) / 1000.0
    assert res.by_carrier_kt["grid"].total == pytest.approx(expect)
