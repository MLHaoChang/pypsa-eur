"""
IC S3 seeds, step 2 (plan `2026-10-13-ic-engine-asks.md` §7; owner 2026-10-13, Q1 a and Q2 a): every
investment candidate of the Energy Hub templates is priced from the generic defaults pack
(`TEMPLATE_PACK_VERSION`), through the asset schema, at the pack's discount rate; no annualised
`capital_cost` literal is left.

  * a single-part asset (a genset, wind, PV, the fuel cell) carries the pack's `overnight_cost`,
    `lifetime`, `fom_cost` (= fom_share × overnight) and `discount_rate`, so PyPSA and the GUI's
    periodised costs annuitise it (the study's PV pattern);
  * a battery carries the pack's two parts through `derive.apply_parts` (a power part per MW and an
    energy part per MWh scaled by `max_hours`), so `capital_cost` is the derived two-annuity value;
  * a Link's `p_nom` is its INPUT: the fuel cell's pack price is per kW of electricity out, so its
    per-MW cost is the pack's × the Link's efficiency (PyPSA-Eur's convention).

Written red first: the templates carried annualised literals.
"""
from __future__ import annotations

import importlib.util
import math
import pathlib

import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "project_templates"

#: (template, component, name) → the pack technology that prices it.
PRICED = {
    ("eh_datacenter", "generators", "genset_new"): "gas-genset",
    ("eh_datacenter", "storage_units", "bess_new"): "battery",
    ("eh_h2_hub", "generators", "wind_new"): "onwind",
    ("eh_h2_hub", "generators", "solar_new"): "solar-utility",
    ("eh_h2_hub", "links", "fuel_cell"): "fuel-cell",
    ("eh_h2_hub", "storage_units", "bess_new"): "battery",
    ("eh_microgrid", "generators", "diesel_new"): "diesel-genset",
    ("eh_microgrid", "storage_units", "bess_new"): "battery",
}


def _eh():
    spec = importlib.util.spec_from_file_location("_eh_templates_pack", TEMPLATES / "eh_templates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def eh():
    return _eh()


@pytest.fixture(scope="module")
def nets(eh):
    return {tid: eh.BUILDERS[tid]() for tid in ("eh_datacenter", "eh_h2_hub", "eh_microgrid")}


@pytest.fixture(scope="module")
def pack(eh):
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(eh.TEMPLATE_PACK_VERSION)


def _v(pack, key):
    return next(v.value for v in pack.cost_values if v.key == key)


def test_the_templates_name_the_pack_they_are_priced_from(eh, pack):
    assert eh.TEMPLATE_PACK_VERSION == "2026-10-13"
    assert pack.version == eh.TEMPLATE_PACK_VERSION


@pytest.mark.parametrize("key", sorted(PRICED), ids=lambda k: f"{k[0]}:{k[2]}")
def test_each_candidate_is_priced_from_the_pack(key, nets, pack):
    tid, comp, name = key
    tech = PRICED[key]
    row = getattr(nets[tid], comp).loc[name]
    rate = pack.finance.discount_rate.value
    assert row["discount_rate"] == pytest.approx(rate)
    if tech == "battery":
        from services.solver.periodized_costs import _annuity

        power, energy = _v(pack, "battery.power.overnight"), _v(pack, "battery.energy.overnight")
        assert row["inv_power_overnight"] == pytest.approx(power)
        assert row["inv_energy_overnight"] == pytest.approx(energy)
        assert row["inv_power_lifetime"] == _v(pack, "battery.power.lifetime")
        assert row["inv_energy_lifetime"] == _v(pack, "battery.energy.lifetime")
        want = (power * _annuity(rate, row["inv_power_lifetime"])
                + energy * row["max_hours"] * _annuity(rate, row["inv_energy_lifetime"]))
        assert row["capital_cost"] == pytest.approx(want, rel=1e-12)
        assert row["fom_cost"] == pytest.approx(power * _v(pack, "battery.power.fom_share"))
        assert math.isnan(row["overnight_cost"])
        return
    overnight = _v(pack, f"{tech}.investment.overnight")
    if comp == "links":                       # p_nom is the input; the pack prices the output
        overnight *= row["efficiency"]
    assert row["overnight_cost"] == pytest.approx(overnight, rel=1e-12)
    assert row["lifetime"] == _v(pack, f"{tech}.investment.lifetime")
    assert row["fom_cost"] == pytest.approx(
        overnight * _v(pack, f"{tech}.investment.fom_share"), rel=1e-12)


def test_no_annualised_literal_is_left_on_a_candidate(nets):
    for (tid, comp, name), tech in PRICED.items():
        row = getattr(nets[tid], comp).loc[name]
        if tech != "battery":
            # PyPSA prefers `overnight_cost`; a left-over literal would only mislead a reader.
            assert row["capital_cost"] == 0.0, (tid, name)


def test_every_extendable_asset_of_a_template_is_priced(nets):
    for tid, n in nets.items():
        for comp in ("generators", "storage_units", "links", "stores"):
            df = getattr(n, comp)
            col = "e_nom_extendable" if comp == "stores" else "p_nom_extendable"
            for name in df.index[df[col].astype(bool)]:
                assert (tid, comp, name) in PRICED, f"{tid}: extendable {comp} {name} not pack-priced"
