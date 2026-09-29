"""
Tariff prices as network data and the bill calculator (MVP-1 phase S3).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S3)

Unit level, no solve. Pins the three tariff semantics the S1 gate carried to
S3 (docs/superpowers/notes/2026-09-29-mvp1-s1-gate.md):

* band precedence is FIRST MATCH WINS, and a band set that leaves any hour
  unpriced is refused (`UnpricedHoursError`), in the bill calculator and in
  `write_tariff_prices` alike;
* export pricing is `price_per_mwh` OR `series_ref`, one or the other;
* `annual_peak` and `ratchet` demand-charge bases are refused with a typed
  error (MVP-1's honest scope is one billing-period demand charge).

Every bill component is `None` with an `unavailable` flag when the series it
needs is absent (ADR-0001); a component the tariff does not have is a real
`0.0`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from models.study import Tariff
from services.study.tariff import (
    BillCalculator,
    ExportPricingError,
    UnpricedHoursError,
    UnsupportedTariffError,
    write_tariff_prices,
)

JAN_FEB = pd.date_range("2030-01-01", "2030-02-28 23:00", freq="h")


def _tariff(**over) -> Tariff:
    base = dict(
        tariff_id="t", name="toy", source="illustrative", currency="EUR",
        currency_year=2026, billing_period="month",
        energy_bands=[{"label": "flat", "price_per_mwh": 100.0, "applies": {}}],
        demand_charge=None, fixed_charge_per_period=0.0, network_charges=[],
        export={"price_per_mwh": None, "series_ref": None, "cap_mw": None},
    )
    base.update(over)
    return Tariff.model_validate(base)


def _w(index) -> pd.Series:
    return pd.Series(1.0, index=index)


# ── energy bands ──────────────────────────────────────────────────────────

def test_flat_band_prices_every_hour_at_one_price():
    imp = pd.Series(2.0, index=JAN_FEB)
    bill = BillCalculator().bill(imp, pd.Series(0.0, index=JAN_FEB),
                                 _tariff(), _w(JAN_FEB))
    assert bill.by_component.energy == pytest.approx(100.0 * 2.0 * len(JAN_FEB))
    assert bill.engine == "bill_calculator"
    assert bill.currency_year == 2026
    assert bill.billing_periods == ["2030-01", "2030-02"]


def test_two_band_time_of_use():
    t = _tariff(energy_bands=[
        {"label": "peak", "price_per_mwh": 180.0,
         "applies": {"weekdays": [0, 1, 2, 3, 4], "hours": list(range(8, 20))}},
        {"label": "off-peak", "price_per_mwh": 95.0, "applies": {}},
    ])
    imp = pd.Series(1.0, index=JAN_FEB)
    peak = (JAN_FEB.weekday < 5) & (JAN_FEB.hour >= 8) & (JAN_FEB.hour < 20)
    expected = 180.0 * peak.sum() + 95.0 * (~peak).sum()
    bill = BillCalculator().bill(imp, pd.Series(0.0, index=JAN_FEB), t, _w(JAN_FEB))
    assert bill.by_component.energy == pytest.approx(expected)


def test_first_matching_band_wins():
    """
    `{}` matches every hour, so a band listed AFTER it never applies; the
    same two bands in the other order price the peak hours at the peak.
    """
    always_first = _tariff(energy_bands=[
        {"label": "always", "price_per_mwh": 50.0, "applies": {}},
        {"label": "peak", "price_per_mwh": 999.0, "applies": {"hours": [12]}},
    ])
    peak_first = _tariff(energy_bands=[
        {"label": "peak", "price_per_mwh": 999.0, "applies": {"hours": [12]}},
        {"label": "always", "price_per_mwh": 50.0, "applies": {}},
    ])
    imp = pd.Series(1.0, index=JAN_FEB)
    exp0 = pd.Series(0.0, index=JAN_FEB)
    calc = BillCalculator()
    assert calc.bill(imp, exp0, always_first, _w(JAN_FEB)).by_component.energy \
        == pytest.approx(50.0 * len(JAN_FEB))
    noon = int((JAN_FEB.hour == 12).sum())
    assert calc.bill(imp, exp0, peak_first, _w(JAN_FEB)).by_component.energy \
        == pytest.approx(999.0 * noon + 50.0 * (len(JAN_FEB) - noon))


def test_bands_that_leave_an_hour_unpriced_are_refused():
    t = _tariff(energy_bands=[
        {"label": "day", "price_per_mwh": 100.0, "applies": {"hours": list(range(6, 22))}},
    ])
    imp = pd.Series(1.0, index=JAN_FEB)
    with pytest.raises(UnpricedHoursError) as exc:
        BillCalculator().bill(imp, pd.Series(0.0, index=JAN_FEB), t, _w(JAN_FEB))
    assert exc.value.code == "tariff_unpriced_hours"
    n = _network()
    with pytest.raises(UnpricedHoursError):
        write_tariff_prices(n, t, "grid_import", "grid_export")


# ── demand, fixed and network charges ─────────────────────────────────────

def test_monthly_demand_charge_charges_each_billing_periods_peak():
    t = _tariff(demand_charge={"price_per_mw_per_period": 9500.0,
                               "basis": "billing_period_peak"})
    imp = pd.Series(3.0, index=JAN_FEB)
    imp.loc["2030-01-15 18:00"] = 10.0
    imp.loc["2030-02-03 09:00"] = 7.0
    bill = BillCalculator().bill(imp, pd.Series(0.0, index=JAN_FEB), t, _w(JAN_FEB))
    assert bill.peak_mw_by_billing_period == {"2030-01": 10.0, "2030-02": 7.0}
    assert bill.by_component.demand == pytest.approx(9500.0 * (10.0 + 7.0))


def test_yearly_billing_period_charges_the_one_peak():
    t = _tariff(billing_period="year",
                demand_charge={"price_per_mw_per_period": 9500.0})
    imp = pd.Series(3.0, index=JAN_FEB)
    imp.loc["2030-01-15 18:00"] = 10.0
    imp.loc["2030-02-03 09:00"] = 7.0
    bill = BillCalculator().bill(imp, pd.Series(0.0, index=JAN_FEB), t, _w(JAN_FEB))
    assert bill.peak_mw_by_billing_period == {"2030": 10.0}
    assert bill.by_component.demand == pytest.approx(9500.0 * 10.0)


def test_no_demand_charge_in_the_tariff_is_a_real_zero():
    imp = pd.Series(3.0, index=JAN_FEB)
    bill = BillCalculator().bill(imp, pd.Series(0.0, index=JAN_FEB), _tariff(), _w(JAN_FEB))
    assert bill.by_component.demand == 0.0
    assert "demand" not in bill.by_component.unavailable


@pytest.mark.parametrize("dc", [
    {"price_per_mw_per_period": 1.0, "basis": "annual_peak"},
    {"price_per_mw_per_period": 1.0, "basis": "ratchet",
     "ratchet": {"months": 11, "share": 0.8}},
])
def test_annual_peak_and_ratchet_bases_are_refused(dc):
    t = _tariff(demand_charge=dc)
    imp = pd.Series(3.0, index=JAN_FEB)
    with pytest.raises(UnsupportedTariffError) as exc:
        BillCalculator().bill(imp, pd.Series(0.0, index=JAN_FEB), t, _w(JAN_FEB))
    assert exc.value.code == f"demand_charge_basis_{dc['basis']}"


def test_fixed_and_per_mwh_network_charges():
    t = _tariff(fixed_charge_per_period=120.0,
                network_charges=[{"label": "levy", "price": 4.0, "basis": "per_mwh"},
                                 {"label": "meter", "price": 10.0, "basis": "per_period"}])
    imp = pd.Series(2.0, index=JAN_FEB)
    bill = BillCalculator().bill(imp, pd.Series(0.0, index=JAN_FEB), t, _w(JAN_FEB))
    assert bill.by_component.fixed == pytest.approx(2 * 120.0)
    assert bill.by_component.network == pytest.approx(4.0 * 2.0 * len(JAN_FEB) + 2 * 10.0)


def test_snapshot_weightings_scale_energy_not_peaks():
    idx = pd.date_range("2030-01-01", periods=24 * 31 // 3, freq="3h")
    t = _tariff(demand_charge={"price_per_mw_per_period": 100.0})
    imp = pd.Series(2.0, index=idx)
    bill = BillCalculator().bill(imp, pd.Series(0.0, index=idx), t,
                                 pd.Series(3.0, index=idx))
    assert bill.by_component.energy == pytest.approx(100.0 * 2.0 * 3.0 * len(idx))
    assert bill.by_component.demand == pytest.approx(100.0 * 2.0)


# ── export ────────────────────────────────────────────────────────────────

def test_export_credit_is_negative_and_uses_the_price():
    t = _tariff(export={"price_per_mwh": 40.0})
    exp = pd.Series(0.0, index=JAN_FEB)
    exp.iloc[:10] = 5.0
    bill = BillCalculator().bill(pd.Series(0.0, index=JAN_FEB), exp, t, _w(JAN_FEB))
    assert bill.by_component.export_credit == pytest.approx(-40.0 * 50.0)


def test_export_series_ref_resolves_through_the_calculators_series():
    t = _tariff(export={"series_ref": "spot"})
    spot = pd.Series(np.linspace(10.0, 70.0, len(JAN_FEB)), index=JAN_FEB)
    exp = pd.Series(1.0, index=JAN_FEB)
    bill = BillCalculator(series={"spot": spot}).bill(
        pd.Series(0.0, index=JAN_FEB), exp, t, _w(JAN_FEB))
    assert bill.by_component.export_credit == pytest.approx(-float(spot.sum()))


def test_export_price_and_series_ref_together_are_refused():
    t = _tariff(export={"price_per_mwh": 40.0, "series_ref": "spot"})
    with pytest.raises(ExportPricingError):
        BillCalculator(series={"spot": pd.Series(1.0, index=JAN_FEB)}).bill(
            pd.Series(0.0, index=JAN_FEB), pd.Series(1.0, index=JAN_FEB), t,
            _w(JAN_FEB))
    with pytest.raises(ExportPricingError):
        write_tariff_prices(_network(), t, "grid_import", "grid_export")


def test_export_link_without_any_export_price_is_refused_when_writing():
    with pytest.raises(ExportPricingError):
        write_tariff_prices(_network(), _tariff(), "grid_import", "grid_export")


# ── ADR-0001: absent series → null with a flag ────────────────────────────

def test_absent_import_series_nulls_the_import_components_with_a_flag():
    t = _tariff(demand_charge={"price_per_mw_per_period": 100.0},
                network_charges=[{"label": "levy", "price": 4.0, "basis": "per_mwh"}],
                export={"price_per_mwh": 40.0})
    bill = BillCalculator().bill(None, pd.Series(1.0, index=JAN_FEB), t, _w(JAN_FEB))
    c = bill.by_component
    assert c.energy is None and c.unavailable["energy"] == "no_import_series"
    assert c.demand is None and c.unavailable["demand"] == "no_import_series"
    assert c.network is None and c.unavailable["network"] == "no_import_series"
    assert c.export_credit == pytest.approx(-40.0 * len(JAN_FEB))
    assert bill.total is None and bill.unavailable["total"] == "component_unavailable"
    assert bill.peak_mw_by_billing_period == {}


def test_absent_export_series_nulls_the_export_credit_with_a_flag():
    t = _tariff(export={"price_per_mwh": 40.0})
    bill = BillCalculator().bill(pd.Series(1.0, index=JAN_FEB), None, t, _w(JAN_FEB))
    assert bill.by_component.export_credit is None
    assert bill.by_component.unavailable["export_credit"] == "no_export_series"
    assert bill.by_component.energy == pytest.approx(100.0 * len(JAN_FEB))


def test_annual_bill_only_for_a_one_year_horizon():
    two_months = BillCalculator().bill(
        pd.Series(1.0, index=JAN_FEB), pd.Series(0.0, index=JAN_FEB), _tariff(),
        _w(JAN_FEB))
    assert two_months.total == pytest.approx(100.0 * len(JAN_FEB))
    assert two_months.annual_bill is None
    assert two_months.unavailable["annual_bill"] == "horizon_not_one_year"
    year = pd.date_range("2030-01-01", periods=8760, freq="h")
    full = BillCalculator().bill(pd.Series(1.0, index=year),
                                 pd.Series(0.0, index=year), _tariff(), _w(year))
    assert full.annual_bill == pytest.approx(full.total)
    assert full.total == pytest.approx(100.0 * 8760)


# ── write_tariff_prices ───────────────────────────────────────────────────

def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(JAN_FEB)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=10.0)
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=10.0)
    return n


def test_write_tariff_prices_writes_permanent_link_prices(tmp_path):
    t = _tariff(
        energy_bands=[
            {"label": "peak", "price_per_mwh": 180.0, "applies": {"hours": list(range(8, 20))}},
            {"label": "off", "price_per_mwh": 95.0, "applies": {}},
        ],
        network_charges=[{"label": "levy", "price": 4.0, "basis": "per_mwh"}],
        export={"price_per_mwh": 40.0},
    )
    n = _network()
    write_tariff_prices(n, t, "grid_import", "grid_export")
    mc = n.links_t.marginal_cost
    day = (JAN_FEB.hour >= 8) & (JAN_FEB.hour < 20)
    expected_imp = np.where(day, 184.0, 99.0)
    np.testing.assert_allclose(mc["grid_import"].to_numpy(), expected_imp)
    np.testing.assert_allclose(mc["grid_export"].to_numpy(), -40.0)
    # Permanent network data: survives a save and load.
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    m = pypsa.Network(path)
    np.testing.assert_allclose(m.links_t.marginal_cost["grid_import"].to_numpy(), expected_imp)
    np.testing.assert_allclose(m.links_t.marginal_cost["grid_export"].to_numpy(), -40.0)


def test_write_tariff_prices_refuses_a_non_datetime_index():
    n = pypsa.Network()
    n.set_snapshots(range(24))
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=10.0)
    with pytest.raises(UnsupportedTariffError):
        write_tariff_prices(n, _tariff(), "grid_import", None)


# ── preflight: export price above import price (review v1 N13) ───────────

def _cycling_network(export_price: float) -> pypsa.Network:
    idx = pd.date_range("2030-01-01", periods=24, freq="h")
    n = pypsa.Network()
    n.set_snapshots(idx)
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "grid_supply", bus="grid", p_nom=100.0, p_min_pu=-1.0)
    imp = pd.Series(np.where(idx.hour == 3, 30.0, 80.0), index=idx)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=10.0, marginal_cost=imp)
    n.add("Link", "grid_export", bus0="site", bus1="grid", p_nom=10.0,
          marginal_cost=-export_price)
    n.add("Load", "l", bus="site", p_set=1.0)
    return n


def test_preflight_warns_when_export_price_exceeds_import_price():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    hit = [i for i in validate_for_run(_cycling_network(40.0), SolverConfig())
           if i.code == "tariff_export_exceeds_import"]
    assert len(hit) == 1
    assert hit[0].severity == "warning"
    assert "grid_import" in hit[0].message and "grid_export" in hit[0].message
    clean = [i for i in validate_for_run(_cycling_network(25.0), SolverConfig())
             if i.code == "tariff_export_exceeds_import"]
    assert clean == []
