"""
IC S0b (plan `docs/superpowers/plans/2026-10-06-ic-s0b-replacements-terminal.md` §4 WP-A; S1–S3): the
finance adapter reads every owner asset's investment through `asset_schema.access.upfront_parts`.

- S1. One accessor: a two-part battery (no `overnight_cost`, rule C1) is established from its parts; a
  typed `overnight_cost` of 0 stays an established 0; a cost back-calculated from `capital_cost` is not
  established (`overnight_cost_missing:<a>` with `upfront_only_from_capital_cost:<a>`).
- S2. The parts on the case: `AssetFinance.parts` (each part's cost = its upfront per unit × the asset's
  capacity), `overnight_cost` their sum; `__post_init__` refuses parts that disagree with it;
  `effective_parts` and `scale_capex` are the one source of capex truth.
- S3. COD from `build_year` when `cod_by_asset` names none (PyPSA's 0 is absent); a typed entry wins.

The battery: power 200,000 EUR/MW · 10 y, energy 150,000 EUR/MWh · 4 h · 15 y, 2 MW → power
400,000 + energy 4 × 150,000 × 2 = 1,200,000 = 1.6 MEUR.
"""
from __future__ import annotations

import dataclasses
import math
from datetime import date

import pandas as pd
import pytest

from services.finance.case import AssetFinance, AssetPart, FinanceRefused

RATE = 0.07
PARTS = {"inv_power_overnight": 200_000.0, "inv_power_lifetime": 10.0, "inv_power_fom_share": 0.01,
         "inv_energy_overnight": 150_000.0, "inv_energy_lifetime": 15.0, "inv_energy_fom_share": 0.0}


def _net(*, battery_parts=True, battery_p_nom_opt=2.0):
    """An unsolved network carrying what the adapter reads: a two-part battery written through
    `apply_parts`, a PV with a typed `overnight_cost`, a wind turbine priced only by `capital_cost`."""
    import pypsa

    from services.asset_schema.derive import apply_parts

    n = pypsa.Network()
    n.set_snapshots(pd.DatetimeIndex(["2030-01-01"], name="snapshot"))
    n.add("Bus", "site")
    n.add("StorageUnit", "bess", bus="site", p_nom=1.0, max_hours=4.0, carrier="battery")
    if battery_parts:
        apply_parts(n, "StorageUnit", "bess", PARTS, discount_rate=RATE)
    n.storage_units.loc["bess", "p_nom_opt"] = battery_p_nom_opt
    n.add("Generator", "pv", bus="site", p_nom=3.0, carrier="solar", overnight_cost=700_000.0,
          lifetime=30.0)
    n.generators.loc["pv", "p_nom_opt"] = 3.0
    n.add("Generator", "wind", bus="site", p_nom=1.0, carrier="wind", capital_cost=90_000.0,
          lifetime=25.0)
    n.generators.loc["wind", "p_nom_opt"] = 1.0
    return n


def _assets(n, owned, *, discount_rate=RATE):
    from services.results.finance_case import _assets as adapter_assets

    assets, _rates, flags = adapter_assets(n, owned, discount_rate=discount_rate)
    return {a.name: a for a in assets}, flags


def _cod(n, fin, owned):
    from services.results.finance_case import _cod as adapter_cod

    return adapter_cod(n, fin, owned)


def _fin(**over):
    from models.finance import FinanceInputs

    kw = dict(financial_close=date(2029, 1, 1), analysis_years=25)
    kw.update(over)
    return FinanceInputs(**kw)


# ── S1, S2: the parts ────────────────────────────────────────────────────────


def test_a_two_part_battery_reads_both_parts():
    assets, flags = _assets(_net(), [("StorageUnit", "bess")])
    bess = assets["bess"]
    assert [p.name for p in bess.parts] == ["power", "energy"]
    power, energy = bess.parts
    assert power.overnight_cost == pytest.approx(400_000.0)
    assert energy.overnight_cost == pytest.approx(1_200_000.0)
    assert (power.lifetime_years, energy.lifetime_years) == (10.0, 15.0)
    assert (power.fom_share, energy.fom_share) == (0.01, 0.0)
    assert bess.overnight_cost == pytest.approx(1_600_000.0)
    assert bess.lifetime_years == 15.0                       # derive_composite's longest part
    assert not any(f.startswith(("overnight_cost_missing", "upfront_only")) for f in flags)


def test_the_two_part_battery_runs_with_its_capex_established():
    from services.finance.case import FinanceCase, Template, TemplateLine
    from services.finance.engine import run_case

    assets, _ = _assets(_net(), [("StorageUnit", "bess")])
    fin = _fin(cod_by_asset={"bess": date(2030, 1, 1)}, contingency_share=0.0,
               escalation={"opex": 0.0, "capex": 0.0}, replacement_capex=[
                   (2039, "bess", 400_000.0), (2044, "bess", 1_200_000.0),
                   (2049, "bess", 400_000.0)])
    case = FinanceCase(inputs=fin, owner="o", base_year=2030, cod=date(2030, 1, 1),
                       templates=(Template(2030, (TemplateLine("bill", "other", 300_000.0,
                                                               "opex"),)),),
                       assets=(assets["bess"],))
    r = run_case(case)
    assert r.op.status["capex"] == "ok", r.op.reasons
    assert float(r.op.capex.sum()) == pytest.approx(1_600_000.0)


def test_a_single_part_generator_is_its_typed_overnight_cost():
    assets, _ = _assets(_net(), [("Generator", "pv")])
    pv = assets["pv"]
    assert pv.overnight_cost == pytest.approx(700_000.0 * 3.0)          # unchanged from today
    assert pv.parts == (AssetPart("investment", pytest.approx(2_100_000.0), 30.0, None),)


def test_a_typed_zero_overnight_cost_stays_an_established_zero():
    n = _net()
    n.generators.loc["wind", "overnight_cost"] = 0.0                    # capital_cost stays 90,000
    assets, flags = _assets(n, [("Generator", "wind")])
    assert assets["wind"].overnight_cost == 0.0
    assert assets["wind"].parts == (AssetPart("investment", 0.0, 25.0, None),)
    assert not any(f.startswith("upfront_only_from_capital_cost") for f in flags)


def test_a_cost_only_from_capital_cost_is_not_established():
    from services.finance.case import FinanceCase, Template, TemplateLine
    from services.finance.engine import run_case

    assets, flags = _assets(_net(), [("Generator", "wind")])
    wind = assets["wind"]
    assert wind.overnight_cost is None and wind.parts == ()
    assert "upfront_only_from_capital_cost:wind" in flags
    fin = _fin(cod_by_asset={"wind": date(2030, 1, 1)}, contingency_share=0.0)
    case = FinanceCase(inputs=fin, owner="o", base_year=2030, cod=date(2030, 1, 1),
                       templates=(Template(2030, (TemplateLine("x", "other", 1.0, "opex"),)),),
                       assets=(wind,), flags=tuple(flags))
    reasons = run_case(case).op.reasons["capex"]
    assert {"overnight_cost_missing:wind", "upfront_only_from_capital_cost:wind"} <= set(reasons)
    # Without a discount rate the accessor cannot back-calculate: not established, unflagged.
    assets, flags = _assets(_net(), [("Generator", "wind")], discount_rate=None)
    assert assets["wind"].overnight_cost is None
    assert not any(f.startswith("upfront_only_from_capital_cost") for f in flags)


def test_an_infinite_lifetime_is_none_on_the_asset_and_inf_on_the_part():
    n = _net()
    n.generators.loc["pv", "lifetime"] = float("inf")
    assets, _ = _assets(n, [("Generator", "pv")])
    assert assets["pv"].lifetime_years is None
    assert math.isinf(assets["pv"].parts[0].lifetime_years)


def test_no_capacity_keeps_the_parts_with_no_cost():
    n = _net()
    n.storage_units.loc["bess", "p_nom_opt"] = float("nan")
    n.storage_units.loc["bess", "p_nom"] = float("nan")
    assets, _ = _assets(n, [("StorageUnit", "bess")])
    bess = assets["bess"]
    assert bess.overnight_cost is None
    assert [p.overnight_cost for p in bess.parts] == [None, None]


# ── S2: one source of truth ──────────────────────────────────────────────────


def test_post_init_refuses_parts_that_disagree_with_the_overnight_cost():
    parts = (AssetPart("power", 400_000.0, 10.0, None), AssetPart("energy", 1_200_000.0, 15.0, None))
    AssetFinance("bess", "StorageUnit", 1_600_000.0, 15.0, "battery", parts)          # agrees
    with pytest.raises(ValueError, match="parts"):
        AssetFinance("bess", "StorageUnit", 1_500_000.0, 15.0, "battery", parts)
    with pytest.raises(ValueError, match="parts"):
        AssetFinance("bess", "StorageUnit", None, 15.0, "battery", parts)            # None one way
    with pytest.raises(ValueError, match="parts"):                                   # … and the other
        AssetFinance("bess", "StorageUnit", 1_600_000.0, 15.0, "battery",
                     (AssetPart("power", None, 10.0, None), parts[1]))
    AssetFinance("bess", "StorageUnit", None, 15.0, "battery",
                 (AssetPart("power", None, 10.0, None), parts[1]))
    AssetFinance("w", "Generator", 0.0, 25.0, None, (AssetPart("investment", 0.0, 25.0, None),))
    # A hand case with no parts is not checked (the SAM fixtures).
    AssetFinance("pv", "Generator", 123.0, 30.0)
    # `dataclasses.replace(overnight_cost=…)` on an asset with parts is refused.
    a = AssetFinance("bess", "StorageUnit", 1_600_000.0, 15.0, "battery", parts)
    with pytest.raises(ValueError):
        dataclasses.replace(a, overnight_cost=1_760_000.0)


def test_effective_parts():
    from services.finance.case import effective_parts

    parts = (AssetPart("power", 400_000.0, 10.0, 0.01),)
    assert effective_parts(AssetFinance("b", "StorageUnit", 400_000.0, 10.0, None, parts)) == parts
    assert effective_parts(AssetFinance("pv", "Generator", 5.0, 30.0)) == \
        (AssetPart("investment", 5.0, 30.0, None),)


def test_scale_capex_scales_the_parts_the_total_and_fixed_replacements():
    from services.finance.case import FinanceCase, Template, TemplateLine, scale_capex

    assets, _ = _assets(_net(), [("StorageUnit", "bess"), ("Generator", "pv")])
    fin = _fin(cod_by_asset={}, replacement_capex=[(2039, "bess", 400_000.0)])
    case = FinanceCase(inputs=fin, owner="o", base_year=2030, cod=date(2030, 1, 1),
                       templates=(Template(2030, (TemplateLine("x", "other", 1.0, "opex"),)),),
                       assets=(assets["bess"], assets["pv"], AssetFinance("x", "Generator", None)))
    s = scale_capex(case, 1.1)
    bess, pv, x = s.assets
    assert bess.overnight_cost == pytest.approx(1_760_000.0)
    assert [p.overnight_cost for p in bess.parts] == [pytest.approx(440_000.0),
                                                      pytest.approx(1_320_000.0)]
    assert pv.overnight_cost == pytest.approx(2_310_000.0)
    assert x.overnight_cost is None
    assert s.inputs.replacement_capex == [(2039, "bess", pytest.approx(440_000.0))]
    assert case.assets[0].overnight_cost == pytest.approx(1_600_000.0)     # the input is untouched


# ── S3: COD from build_year ──────────────────────────────────────────────────


def test_cod_from_build_year():
    n = _net()
    n.storage_units.loc["bess", "build_year"] = 2030
    cod, flags = _cod(n, _fin(), [("StorageUnit", "bess")])
    assert cod == date(2030, 1, 1)
    assert flags == ["cod_from_build_year:bess"]


def test_a_typed_cod_wins_over_build_year():
    n = _net()
    n.storage_units.loc["bess", "build_year"] = 2030
    cod, flags = _cod(n, _fin(cod_by_asset={"bess": date(2031, 7, 1)}), [("StorageUnit", "bess")])
    assert cod == date(2031, 7, 1) and flags == []


@pytest.mark.parametrize("by", [0, float("nan"), -2030, 2030.5])
def test_an_absent_build_year_is_cod_missing(by):
    n = _net()
    n.storage_units.loc["bess", "build_year"] = by                     # PyPSA's default is 0
    with pytest.raises(FinanceRefused) as exc:
        _cod(n, _fin(), [("StorageUnit", "bess")])
    assert exc.value.code == "cod_missing"


def test_different_build_years_are_cod_mismatch():
    n = _net()
    n.storage_units.loc["bess", "build_year"] = 2030
    n.generators.loc["pv", "build_year"] = 2031
    with pytest.raises(FinanceRefused) as exc:
        _cod(n, _fin(), [("StorageUnit", "bess"), ("Generator", "pv")])
    assert exc.value.code == "cod_mismatch"
    # A typed entry against a different build_year on another asset: also a mismatch.
    with pytest.raises(FinanceRefused) as exc:
        _cod(n, _fin(cod_by_asset={"bess": date(2030, 1, 1)}),
             [("StorageUnit", "bess"), ("Generator", "pv")])
    assert exc.value.code == "cod_mismatch"


# ── through the adapter on a solved site ─────────────────────────────────────


@pytest.mark.live_solve
def test_the_adapter_on_a_solved_two_part_battery(reset_backend):
    """The storage-and-meters site with its battery typed as two parts (no `overnight_cost`):
    the case carries the parts and the engine's capex is established — the U2 gap closed."""
    from services.asset_schema.derive import apply_parts
    from services.finance.engine import run_case
    from tests.test_finance_case_storage_and_meters import (
        _build, _commercial, _fin as site_fin, _single_owner, _site,
    )
    from tests.test_value_flow_reconciliation import _solve

    n = _site()
    n.storage_units.loc["bess", ["overnight_cost", "p_nom", "max_hours"]] = [float("nan"), 2.0, 4.0]
    apply_parts(n, "StorageUnit", "bess", PARTS, discount_rate=RATE)
    n.storage_units.loc["bess", "build_year"] = 2030
    n, cfg = _solve(n, _commercial(_single_owner(n)))
    case = _build(n, cfg, site_fin(cod_by_asset={"pv": date(2030, 1, 1)},
                                   replacement_capex=[(2039, "bess", 400_000.0)]))
    bess = {a.name: a for a in case.assets}["bess"]
    assert [p.name for p in bess.parts] == ["power", "energy"]
    assert bess.overnight_cost == pytest.approx(1_600_000.0)
    assert "cod_from_build_year:bess" in case.flags
    r = run_case(case)
    assert r.op.status["capex"] == "ok", r.op.reasons
    assert float(r.op.capex.sum()) == pytest.approx(1_600_000.0 + 800_000.0 * 2)
