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


def _staged_with_override(values_by_period=None):
    n = T.build_eh_datacenter_staged()
    _dispatch(n, grid_supply=1.0)
    ovr = n.generators_t["eh_co2_t_per_mwh"]
    for period, v in (values_by_period or {}).items():
        ovr.loc[ovr.index.get_level_values(0) == period, "grid_supply"] = v
    return n, ovr


def _expected_t(n, ovr, periods=None):
    w = _build_snapshot_weights(n, "generators")
    col = ovr["grid_supply"].fillna(0.35)
    if periods is not None:
        keep = ovr.index.get_level_values(0).isin(periods)
        return float((w[keep] * col[keep]).sum())
    return float((w * col).sum())


def test_the_single_year_template_has_no_override_and_both_views_agree():
    n = T.build_eh_datacenter()
    assert "eh_co2_t_per_mwh" not in n.generators_t
    _dispatch(n, grid_supply=1.0, genset_1=2.0)
    cmp_, res = _both_flat(n)
    w = float(_build_snapshot_weights(n, "generators").iloc[0])
    n_snaps = len(n.snapshots)
    expect = n_snaps * w * (1.0 * 0.35 + 2.0 * GAS_T_PER_MWH_FUEL / PACK_EFFICIENCY)
    assert res["total_tCO2"] == pytest.approx(expect)
    assert cmp_.total_kt.total * 1000.0 == pytest.approx(expect)


def _both_flat(n):
    from services.results.emissions import compute_emissions

    cmp_ = _compute_emissions_summary(n, [], False, True)
    res = compute_emissions(
        n,
        "lopf",
        result_df=lambda n_, comp, attr, src: getattr(getattr(n_, comp), attr),
    )
    return cmp_, res


def test_an_overridden_generator_on_a_zero_intensity_carrier_is_still_counted():
    n, ovr = _staged_with_override()
    n.carriers.loc["grid", "co2_emissions"] = 0.0
    n.carriers.loc["solar", "co2_emissions"] = 0.0
    cmp_, res = _both(n)
    expect = _expected_t(n, ovr)
    assert cmp_.by_carrier_kt["grid"].total * 1000.0 == pytest.approx(expect)
    by_carrier = {r["carrier"]: r["tCO2"] for r in res["by_carrier"]}
    assert by_carrier["grid"] == pytest.approx(expect)
    assert "solar" not in by_carrier  # zero intensity and no override: not listed
    assert by_carrier["gas"] == 0.0  # non-zero intensity, no dispatch: listed at zero


def test_an_all_zero_override_lists_no_carrier():
    n, _ = _staged_with_override({2027: 0.0, 2030: 0.0, 2035: 0.0})
    cmp_, res = _both(n)
    assert "grid" not in cmp_.by_carrier_kt
    assert res["total_tCO2"] == 0.0


def test_the_override_is_weighted_by_snapshot_weight_in_both_views():
    n, ovr = _staged_with_override()
    first = n.snapshots.get_level_values(0)[0]
    idx = ovr.index[ovr.index.get_level_values(0) == first]
    ovr.loc[idx[: len(idx) // 2], "grid_supply"] = 0.10  # not constant
    for col in ("generators", "objective"):  # not uniform either
        n.snapshot_weightings.loc[idx[: len(idx) // 3], col] *= 3.0
    cmp_, res = _both(n)
    expect = _expected_t(n, ovr)
    assert cmp_.total_kt.total * 1000.0 == pytest.approx(expect, rel=1e-9)
    assert res["total_tCO2"] == pytest.approx(expect, rel=1e-9)


def test_a_period_of_zero_weight_adds_nothing_and_stays_finite():
    n, ovr = _staged_with_override()
    first = n.snapshots.get_level_values(0)[0]
    zero = n.snapshots.get_level_values(0) == first
    for col in ("generators", "objective"):
        n.snapshot_weightings.loc[zero, col] = 0.0
    cmp_, res = _both(n)
    expect = _expected_t(
        n, ovr, periods=[p for p in set(n.snapshots.get_level_values(0)) if p != first]
    )
    assert res["total_tCO2"] == pytest.approx(expect, rel=1e-9)
    assert cmp_.total_kt.total * 1000.0 == pytest.approx(expect, rel=1e-9)


def test_the_per_period_rows_carry_energy_times_the_periods_intensity():
    n, ovr = _staged_with_override()
    _, res = _both(n)
    for row in res["by_period"]:
        assert row["total_tCO2"] == pytest.approx(
            _expected_t(n, ovr, periods=[row["period"]]), rel=1e-9
        )
    assert len(res["by_period"]) == 3


def test_a_pack_row_with_no_value_falls_to_the_default(monkeypatch):
    from services.adequacy.dc_backup import EFFICIENCY_KEY
    from services.library.defaults_pack import loader

    blank = SimpleNamespace(key=EFFICIENCY_KEY, value=None)
    monkeypatch.setattr(
        loader,
        "load_defaults_pack",
        lambda *a, **k: SimpleNamespace(cost_values=[blank]),
    )
    assert T._genset_efficiency(None) == pytest.approx(1 / 2.8)


# ── one hand figure across every surface (owner decision DC03-GRIDCO2-1) ────
# 1000 MW from the grid on the first 84 of period 2027's 168 snapshots, where the
# grid intensity is 0.10 t/MWh el; every snapshot stands for 8760/168 h. By hand:
# 3 years x 84 x (8760/168) h x 1000 MW x 0.10 t/MWh = 1,314,000 t, priced at 50 EUR/t = 65.7 MEUR.
HAND_T = 3 * 84 * (8760 / 168) * 1000.0 * 0.10  # period 2027 stands for 3 years
PRICE = 50.0


def _hand_net():
    n = T.build_eh_datacenter_staged()
    first = n.snapshots.get_level_values(0)[0]
    in_first = n.snapshots.get_level_values(0) == first
    half = n.snapshots[in_first][:84]
    p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    p.loc[half, "grid_supply"] = 1000.0
    n.generators_t.p = p
    n.generators_t["eh_co2_t_per_mwh"].loc[half, "grid_supply"] = 0.10
    return n, half


def test_every_co2_surface_reads_the_same_per_snapshot_intensity():
    from services.asset_results import compute as C
    from services.solver.assumptions import _apply_modelling_assumptions
    from services.solver.diagnostics import _log_cost_decomposition_post_solve
    from services.solver_service import SolverConfig

    n, half = _hand_net()
    cmp_, res = _both(n)
    assert cmp_.total_kt.total * 1000.0 == pytest.approx(HAND_T, rel=1e-9)
    assert res["total_tCO2"] == pytest.approx(HAND_T, rel=1e-9)
    first = res["by_period"][0]
    assert first["total_tCO2"] == pytest.approx(HAND_T, rel=1e-9)
    # the asset results
    ctx = C.build_ctx(n, "Generator", "grid_supply", source="lopf", sns=n.snapshots)
    assert C.gen_co2_total(ctx) == pytest.approx(HAND_T, rel=1e-9)
    # the solve diagnostics: emitted tonnes and the surcharge, to the log's own rounding
    lines: list[str] = []
    cfg = SolverConfig(co2_price=PRICE)
    _log_cost_decomposition_post_solve(n, cfg, n.snapshots, None, lines.append)
    row = next(x for x in lines if "2027:" in x)
    assert f"CO2_surcharge={HAND_T * PRICE / 1e6:.2f}M€ ({HAND_T / 1e3:.1f} kt)" in row
    # the LP's CO2 price: 50 EUR/t x 0.10 t/MWh on the half, x the carrier's 0.35 elsewhere
    before = n.get_switchable_as_dense(
        "Generator", "marginal_cost", inds=["grid_supply"]
    )["grid_supply"].copy()
    restore, _ = _apply_modelling_assumptions(n, cfg, lambda *a, **k: None)
    try:
        after = n.get_switchable_as_dense(
            "Generator", "marginal_cost", inds=["grid_supply"]
        )["grid_supply"]
        step = after - before
        assert step.loc[half].to_numpy() == pytest.approx(PRICE * 0.10)
        rest = step.drop(half)
        assert rest.to_numpy() == pytest.approx(
            PRICE
            * n.generators_t["eh_co2_t_per_mwh"]["grid_supply"].drop(half).to_numpy()
        )
    finally:
        restore()
    assert n.get_switchable_as_dense(
        "Generator", "marginal_cost", inds=["grid_supply"]
    )["grid_supply"].equals(before)


def test_a_negative_intensity_carrier_without_an_override_is_not_counted():
    # master's guard: a generator whose carrier intensity is <= 0 emits nothing here
    n = T.build_eh_datacenter()
    n.carriers.loc["gas", "co2_emissions"] = -0.5
    _dispatch(n, genset_1=-1.0)
    n.generators_t.p.iloc[1:] = 0.0
    cmp_ = _compute_emissions_summary(n, [], False, True)
    assert "gas" not in cmp_.by_carrier_kt
    assert cmp_.total_kt.total == 0.0


def test_the_results_view_sums_each_snapshot_not_a_period_mean():
    # non-constant dispatch x non-constant intensity: 2 MW only where the intensity is 0.10
    n, ovr = _staged_with_override()
    first = n.snapshots.get_level_values(0)[0]
    idx = ovr.index[ovr.index.get_level_values(0) == first]
    ovr.loc[idx[: len(idx) // 2], "grid_supply"] = 0.10
    p = pd.DataFrame(0.0, index=n.snapshots, columns=n.generators.index)
    p.loc[idx[: len(idx) // 2], "grid_supply"] = 2.0
    n.generators_t.p = p
    cmp_, res = _both(n)
    expect = 3 * (len(idx) // 2) * (8760 / 168) * 2.0 * 0.10  # 2027 stands for 3 years
    assert res["total_tCO2"] == pytest.approx(expect, rel=1e-9)
    assert cmp_.total_kt.total * 1000.0 == pytest.approx(expect, rel=1e-9)


def test_master_digests_return_with_the_gas_efficiency_at_one(monkeypatch, tmp_path):
    """The single-year digest and the reserve-attestation fingerprints, re-pinned for
    this change, are master's old literals again when the efficiency is 1."""
    import project_templates.eh_templates as P
    import tests.test_reserve_attestation as RA
    from tests.test_energy_hub_template_staged import _digest

    for m in (T, P):
        monkeypatch.setattr(m, "_genset_efficiency", lambda load_spec: 1.0)
    monkeypatch.setattr(
        RA,
        "MASTER_FP_PLAIN",
        "4cd91e31fcad778afaf0ef46e0fbde89f7eec6b185126a43389b720fe7a17f0f",
    )
    monkeypatch.setattr(
        RA,
        "MASTER_FP_PRODUCTS",
        "c3146876a727aa76625fc9d1e4ac8cb749b2637d6a58423c049f512fee62b92b",
    )
    RA.test_b3_without_an_attestation_the_floors_digest_and_fingerprints_are_masters(
        tmp_path
    )
    import numpy as np

    n = T.build_eh_datacenter()
    for frame in ("generators", "storage_units", "links"):
        df = getattr(n, frame)
        rows = [a for a in T.DC_EXISTING if a in df.index]
        df.loc[rows, ["overnight_cost", "discount_rate"]] = np.nan
    assert _digest(n) == "32b282c3eb61543e"


# ── branches the mutant runs left open ─────────────────────────────────────
def test_the_helper_reads_the_snapshots_it_is_asked_for_and_fills_the_default():
    from services.co2_override import has_override, override_series

    n, half = _hand_net()
    assert has_override(n, "grid_supply") and not has_override(n, "genset_1")
    assert override_series(n, "genset_1", 0.5) is None
    full = override_series(n, "grid_supply", 0.5)
    assert list(full.index) == list(n.snapshots)
    sub = n.snapshots[10:13]
    assert list(override_series(n, "grid_supply", 0.5, sub).index) == list(sub)
    n.generators_t["eh_co2_t_per_mwh"].loc[sub[0], "grid_supply"] = float("nan")
    assert (
        override_series(n, "grid_supply", 0.5, sub).iloc[0] == 0.5
    )  # no figure: the default


def test_the_results_view_counts_a_plain_generator_at_its_carrier_per_period():
    n, _ = _hand_net()
    n.generators_t.p["genset_1"] = 10.0
    _, res = _both(n)
    row = next(
        r for r in res["by_period"][0]["by_generator"] if r["name"] == "genset_1"
    )
    assert row["tCO2"] == pytest.approx(
        3 * 8760 * 10.0 * GAS_T_PER_MWH_FUEL / PACK_EFFICIENCY
    )


def test_the_diagnostics_count_every_emitter_and_a_zero_intensity_overridden_one():
    from services.solver.diagnostics import _log_cost_decomposition_post_solve
    from services.solver_service import SolverConfig

    def kt_of_2027(n):
        lines: list[str] = []
        _log_cost_decomposition_post_solve(
            n, SolverConfig(co2_price=PRICE), n.snapshots, None, lines.append
        )
        row = next(x for x in lines if "2027:" in x)
        return row.split("kt)")[0].rsplit("(", 1)[1].strip()

    n, _ = _hand_net()
    n.generators_t.p["genset_1"] = 10.0
    gas_t = 3 * 8760 * 10.0 * GAS_T_PER_MWH_FUEL / PACK_EFFICIENCY
    assert (
        kt_of_2027(n) == f"{(HAND_T + gas_t) / 1e3:.1f}"
    )  # a plain emitter is counted with the override
    n.carriers.loc["grid", "co2_emissions"] = 0.0
    assert (
        kt_of_2027(n) == f"{(HAND_T + gas_t) / 1e3:.1f}"
    )  # and an overridden one on a zero carrier


def _price_step(n, cfg):
    from services.solver.assumptions import _apply_modelling_assumptions

    cols = list(n.generators.index)
    before = n.get_switchable_as_dense("Generator", "marginal_cost", inds=cols)
    restore, _ = _apply_modelling_assumptions(n, cfg, lambda *a, **k: None)
    try:
        after = n.get_switchable_as_dense("Generator", "marginal_cost", inds=cols)
        step = after - before
        has_series = list(n.generators_t.marginal_cost.columns)
    finally:
        restore()
    again = n.get_switchable_as_dense("Generator", "marginal_cost", inds=cols)
    assert again.equals(before)  # the price is transient
    return step, has_series


def test_the_lp_price_reads_a_per_period_price_times_the_per_snapshot_intensity():
    from services.solver_service import SolverConfig

    n, half = _hand_net()
    prices = {2027: 100.0, 2030: 200.0, 2035: 300.0}
    step, _ = _price_step(n, SolverConfig(co2_price_per_period=prices))
    s = step["grid_supply"]
    assert s.loc[half].to_numpy() == pytest.approx(100.0 * 0.10)
    period_2030 = s.index.get_level_values(0) == 2030
    assert s[period_2030].to_numpy() == pytest.approx(
        200.0 * 0.30
    )  # the 2030 intensity


def test_the_lp_price_of_an_overridden_generator_without_a_cost_series_is_transient():
    from services.solver_service import SolverConfig

    n, _ = _hand_net()
    n.generators_t["eh_co2_t_per_mwh"]["genset_1"] = 0.30
    assert "genset_1" not in n.generators_t.marginal_cost.columns
    step, during = _price_step(n, SolverConfig(co2_price=PRICE))
    assert step["genset_1"].to_numpy() == pytest.approx(PRICE * 0.30)
    assert "genset_1" in during  # priced per snapshot while the solve runs
    assert (
        "genset_1" not in n.generators_t.marginal_cost.columns
    )  # dropped again on restore
    assert n.generators.at["genset_1", "marginal_cost"] == 165.0


def test_a_zero_efficiency_emitter_is_not_priced(monkeypatch):
    import numpy as np

    from services.solver_service import SolverConfig

    n, _ = _hand_net()
    n.generators.at["genset_2", "efficiency"] = 0.0
    step, _ = _price_step(n, SolverConfig(co2_price=PRICE))
    assert np.isfinite(step["genset_2"]).all() and (step["genset_2"] == 0.0).all()


def test_an_overridden_fuel_curve_generator_is_priced_at_its_override(monkeypatch):
    # GK A (36) @a8f34cb4: the override wins on every surface; the slope is only
    # the fallback on a snapshot the override leaves empty.
    from services import fuel_curve
    from services.solver_service import SolverConfig

    n, _ = _hand_net()
    n.generators_t["eh_co2_t_per_mwh"]["genset_1"] = 0.30
    real = fuel_curve.curve_slope
    monkeypatch.setattr(
        fuel_curve,
        "curve_slope",
        lambda row: 2.0 if row.name == "genset_1" else real(row),
    )
    step, during = _price_step(n, SolverConfig(co2_price=PRICE))
    assert step["genset_1"].to_numpy() == pytest.approx(PRICE * 0.30)
    assert "genset_1" in during


# ── GK A (36) @a8f34cb4: an override wins over a fuel curve, on every surface ──

W_H = 8760 / 168  # each staged snapshot's weight


def _grid_step(n, cfg):
    return _price_step(n, cfg)[0]["grid_supply"]


def _five(n, cfg):
    """The tonnes compare, results, asset results and diagnostics count (t)."""
    from services.asset_results import compute as C
    from services.solver.diagnostics import _log_cost_decomposition_post_solve

    cmp_, res = _both(n)
    ctx = C.build_ctx(n, "Generator", "grid_supply", source="lopf", sns=n.snapshots)
    lines: list[str] = []
    _log_cost_decomposition_post_solve(n, cfg, n.snapshots, None, lines.append)
    row = next(x for x in lines if "2027:" in x)
    return cmp_.total_kt.total * 1000.0, res["total_tCO2"], C.gen_co2_total(ctx), row


def test_an_overridden_curve_genset_counts_its_override_and_the_slope_only_on_a_gap():
    from services.solver_service import SolverConfig

    n, half = _hand_net()
    n.generators.loc["grid_supply", "fuel_curve_slope"] = 3.0
    gap = half[0]
    n.generators_t["eh_co2_t_per_mwh"].loc[gap, "grid_supply"] = float("nan")
    # 83 snapshots at the override 0.10, one at the carrier's 0.35 x the slope 3.
    want = 3 * W_H * 1000.0 * (83 * 0.10 + 1 * 0.35 * 3.0)
    cfg = SolverConfig(co2_price=PRICE)
    cmp_t, res_t, asset_t, diag = _five(n, cfg)
    assert cmp_t == pytest.approx(want, rel=1e-9)
    assert res_t == pytest.approx(want, rel=1e-9)
    assert asset_t == pytest.approx(want, rel=1e-9)
    assert f"({want / 1e3:.1f} kt)" in diag
    step = _grid_step(n, cfg)
    assert step.loc[half[1:]].to_numpy() == pytest.approx(PRICE * 0.10)
    assert step.loc[gap] == pytest.approx(PRICE * 0.35 * 3.0)


@pytest.mark.parametrize("carrier", ["solar", "no_such_carrier"])
def test_an_overridden_generator_on_a_carrier_without_co2_is_counted_and_priced(
    carrier,
):
    from services.solver_service import SolverConfig

    n, half = _hand_net()
    n.generators.loc["grid_supply", "carrier"] = carrier
    n.generators.loc["grid_supply", "fuel_curve_slope"] = 3.0
    cfg = SolverConfig(co2_price=PRICE)
    _, res_t, asset_t, _ = _five(n, cfg)
    assert res_t == pytest.approx(HAND_T, rel=1e-9)
    assert asset_t == pytest.approx(HAND_T, rel=1e-9)
    assert _grid_step(n, cfg).loc[half].to_numpy() == pytest.approx(PRICE * 0.10)


def test_the_per_period_price_does_not_divide_an_override_by_efficiency():
    from services.solver_service import SolverConfig

    n, half = _hand_net()
    n.generators.loc["grid_supply", "efficiency"] = 0.5
    first = int(n.snapshots.get_level_values(0)[0])
    cfg = SolverConfig(
        co2_price=PRICE,
        co2_price_per_period={str(first): 2 * PRICE},
        multi_investment_periods=True,
    )
    step = _grid_step(n, cfg)
    assert step.loc[half].to_numpy() == pytest.approx(2 * PRICE * 0.10)


def test_a_zero_intensity_carrier_with_an_override_is_priced_on_the_uniform_path():
    from services.solver_service import SolverConfig

    n, half = _hand_net()
    n.generators.loc["grid_supply", "carrier"] = "solar"
    step = _grid_step(n, SolverConfig(co2_price=PRICE))
    assert step.loc[half].to_numpy() == pytest.approx(PRICE * 0.10)
