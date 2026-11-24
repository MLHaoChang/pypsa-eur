# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""DC template review issue 03: genset CO2 is per MWh of electricity.

Hand check: gas carries 0.20 t per MWh of fuel. With no genset in the load spec
the template reads the defaults pack's ``gas-genset.investment.efficiency``, 0.41
(the row the backup costing reads too), so a genset emits 0.20 / 0.41 =
0.487805 t per MWh el: 10 MW for one hour is 4.87805 t. With the default
efficiency of 1 it was 2.0 t. A CO2 price of 50 EUR/t adds 50 * 0.20 / 0.41 =
24.39 EUR per MWh el to the 165 EUR/MWh el marginal cost, once.
"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

from models.flex_archetypes import GensetSpec  # noqa: E402
from services.compare.emissions import _compute_emissions_summary  # noqa: E402
from services.compare.support import _build_snapshot_weights  # noqa: E402

PACK_EFFICIENCY = 0.41  # defaults pack 2026-10-26, gas-genset.investment.efficiency
GAS_T_PER_MWH_FUEL = 0.20


def _gas_gens(n):
    return [g for g in n.generators.index if n.generators.at[g, "carrier"] == "gas"]


def _dispatch(n, **mw):
    p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    for g, v in mw.items():
        p[g] = v
    n.generators_t.p = p


@pytest.mark.parametrize(
    "builder", [T.build_eh_datacenter, T.build_eh_datacenter_staged]
)
def test_gas_gensets_carry_the_packs_efficiency(builder):
    n = builder()
    gens = _gas_gens(n)
    assert gens
    for g in gens:
        assert n.generators.at[g, "efficiency"] == pytest.approx(PACK_EFFICIENCY)


def test_the_heat_rate_comes_from_the_spec_then_the_pack_then_the_default(monkeypatch):
    spec = SimpleNamespace(
        gensets=[GensetSpec(power_mw=1.0, heat_rate_mwh_fuel_per_mwh_el=3.0)]
    )
    assert T._genset_efficiency(spec) == pytest.approx(1 / 3.0)
    assert T._genset_efficiency(SimpleNamespace(gensets=[])) == pytest.approx(
        PACK_EFFICIENCY
    )
    assert T._genset_efficiency(None) == pytest.approx(PACK_EFFICIENCY)
    from services.library.defaults_pack import loader

    monkeypatch.setattr(
        loader, "load_defaults_pack", lambda *a, **k: SimpleNamespace(cost_values=[])
    )
    assert T._genset_efficiency(None) == pytest.approx(1 / 2.8)


def test_the_template_and_the_backup_costing_read_one_heat_rate():
    from services.adequacy.dc_backup import EFFICIENCY_KEY, pack_prices

    assert pack_prices(T.TEMPLATE_PACK_VERSION)[EFFICIENCY_KEY] == pytest.approx(
        PACK_EFFICIENCY
    )


def test_genset_co2_is_per_mwh_el():
    n = T.build_eh_datacenter()
    _dispatch(n, genset_1=10.0)
    n.generators_t.p.iloc[1:] = 0.0  # 10 MW for the first hour only
    res = _compute_emissions_summary(n, [], False, True)
    w = float(
        _build_snapshot_weights(n, "generators").iloc[0]
    )  # the week stands for a year
    t_per_mwh_el = GAS_T_PER_MWH_FUEL / PACK_EFFICIENCY
    assert res.by_carrier_kt["gas"].total == pytest.approx(
        10.0 * t_per_mwh_el * w / 1000.0
    )
    assert res.intensity_kg_per_mwh.total == pytest.approx(1000.0 * t_per_mwh_el)


def test_a_co2_price_adds_its_euro_per_mwh_el_once():
    from services.solver.assumptions import _apply_modelling_assumptions
    from services.solver_service import SolverConfig

    n = T.build_eh_datacenter()
    before = float(n.generators.at["genset_1", "marginal_cost"])
    restore, _ = _apply_modelling_assumptions(
        n, SolverConfig(co2_price=50.0), lambda *a, **k: None
    )
    try:
        assert n.generators.at["genset_1", "marginal_cost"] == pytest.approx(
            before + 50.0 * GAS_T_PER_MWH_FUEL / PACK_EFFICIENCY
        )
        assert before == 165.0
    finally:
        restore()
    assert n.generators.at["genset_1", "marginal_cost"] == before


def test_staged_grid_intensity_is_per_period_and_counted():
    n = T.build_eh_datacenter_staged()
    t = n.generators_t["eh_co2_t_per_mwh"]["grid_supply"]
    assert sorted(t.groupby(level=0).first().tolist()) == [0.22, 0.30, 0.35]
    _dispatch(n, grid_supply=1.0)  # 1 MW every snapshot
    res = _compute_emissions_summary(n, [], True, True)
    w = _build_snapshot_weights(n, "generators")
    assert res.by_carrier_kt["grid"].total == pytest.approx(
        float((w * t).sum()) / 1000.0
    )


def _both(n):
    from services.results.emissions import compute_emissions

    cmp_ = _compute_emissions_summary(n, [], True, True)
    res = compute_emissions(
        n,
        "lopf",
        result_df=lambda n_, comp, attr, src: getattr(getattr(n_, comp), attr),
    )
    return cmp_, res


def test_results_emissions_agrees_with_compare_on_the_staged_grid():
    n = T.build_eh_datacenter_staged()
    _dispatch(n, grid_supply=1.0, genset_1=2.0)
    cmp_, res = _both(n)
    assert res["total_tCO2"] == pytest.approx(cmp_.total_kt.total * 1000.0, rel=1e-6)


def test_a_period_with_no_override_falls_back_to_the_carrier_in_both_views():
    n = T.build_eh_datacenter_staged()
    _dispatch(n, grid_supply=1.0)
    ovr = n.generators_t["eh_co2_t_per_mwh"]
    first = n.snapshots.get_level_values(0)[0]
    ovr.loc[ovr.index.get_level_values(0) == first, "grid_supply"] = float(
        "nan"
    )  # no figure for the first period
    cmp_, res = _both(n)
    w = _build_snapshot_weights(n, "generators")
    expect_t = float(
        (w * ovr["grid_supply"].fillna(0.35)).sum()
    )  # carrier 0.35 where missing
    assert res["total_tCO2"] == pytest.approx(expect_t, rel=1e-9)
    assert cmp_.total_kt.total * 1000.0 == pytest.approx(expect_t, rel=1e-9)
    # the carrier breakdown names the grid too
    assert res["by_carrier"]


def test_a_period_mean_is_weighted_by_the_snapshot_weight():
    n = T.build_eh_datacenter_staged()
    _dispatch(n, grid_supply=1.0)
    ovr = n.generators_t["eh_co2_t_per_mwh"]
    first = n.snapshots.get_level_values(0)[0]
    idx = ovr.index[ovr.index.get_level_values(0) == first]
    ovr.loc[idx[: len(idx) // 2], "grid_supply"] = 0.10  # not constant in the period
    cmp_, res = _both(n)
    assert res["total_tCO2"] == pytest.approx(cmp_.total_kt.total * 1000.0, rel=1e-6)


def test_the_pinned_digests_move_only_by_the_gas_efficiency(monkeypatch):
    """With the gas efficiency back at 1 the single-year template is master's, byte
    for byte: the digests re-pinned for this change return to their old literals."""
    import project_templates.eh_templates as P
    from tests.test_energization_per_period import _fps

    for m in (T, P):
        monkeypatch.setattr(m, "_genset_efficiency", lambda load_spec: 1.0)
    assert _fps(T.build_eh_datacenter()) == (
        "4ae621820bfa2bbdf0295d5fe6d9557ed89025a5197cd4826898bf550e13a0bd",
        "9766df8fdb39a263d938f2b2ded7d83f52be73968322a43128d6389b0715141b",
    )
