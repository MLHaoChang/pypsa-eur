"""
`defaults_pack.pricing.price_from_pack` (IC S3 seeds; owner 2026-10-13): the one way a template or a
redundancy scenario prices an asset from the generic defaults pack, through the asset schema, at the
pack's discount rate.

  * a battery (StorageUnit) gets the pack's two parts (`derive.apply_parts`): `capital_cost` derived,
    `overnight_cost` empty;
  * any other asset one `investment` part on PyPSA's own columns (`overnight_cost`, `lifetime`,
    `fom_cost` = fom_share × overnight), `capital_cost` 0;
  * a Link's `p_nom` is its input, so a price per kW of output is scaled by its efficiency;
  * the return value is what was written plus the pack's id, version and hash (for a disclosure).

Written red first: the module did not exist.
"""
from __future__ import annotations

import math

import pypsa
import pytest

VERSION = "2026-10-22"


def _pack():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(VERSION)


def _v(key):
    return next(r.value for r in _pack().cost_values if r.key == key)


def _net():
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Bus", "h2")
    n.add("Generator", "g", bus="b", p_nom_extendable=True, capital_cost=60.0)
    n.add("StorageUnit", "s", bus="b", p_nom_extendable=True, max_hours=4.0, capital_cost=80.0)
    n.add("Link", "fc", bus0="h2", bus1="b", p_nom_extendable=True, efficiency=0.5,
          capital_cost=40.0)
    return n


def test_a_single_part_asset_gets_the_packs_overnight_part():
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    out = price_from_pack(n, "generators", "g", "gas-genset", version=VERSION)
    row, rate = n.generators.loc["g"], _pack().finance.discount_rate.value
    assert row["overnight_cost"] == pytest.approx(_v("gas-genset.investment.overnight"))
    assert row["lifetime"] == _v("gas-genset.investment.lifetime")
    assert row["fom_cost"] == pytest.approx(
        _v("gas-genset.investment.overnight") * _v("gas-genset.investment.fom_share"))
    assert row["discount_rate"] == rate and row["capital_cost"] == 0.0
    assert out["technology"] == "gas-genset" and out["pack_version"] == VERSION
    assert out["pack_hash"] == _pack().hash and out["overnight_cost"] == row["overnight_cost"]


def test_a_link_is_priced_per_mw_of_input():
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(n, "links", "fc", "fuel-cell", version=VERSION)
    assert n.links.at["fc", "overnight_cost"] == pytest.approx(
        0.5 * _v("fuel-cell.investment.overnight"))


def test_a_battery_gets_the_packs_two_parts():
    from services.library.defaults_pack.pricing import price_from_pack
    from services.solver.periodized_costs import _annuity

    n = _net()
    out = price_from_pack(n, "storage_units", "s", "battery", version=VERSION)
    row, rate = n.storage_units.loc["s"], _pack().finance.discount_rate.value
    power, energy = _v("battery.power.overnight"), _v("battery.energy.overnight")
    want = power * _annuity(rate, 10.0) + energy * 4.0 * _annuity(rate, 25.0)
    assert row["capital_cost"] == pytest.approx(want, rel=1e-12)
    assert math.isnan(row["overnight_cost"]) and row["discount_rate"] == rate
    assert out["capital_cost"] == pytest.approx(want, rel=1e-12)


def test_an_unknown_technology_is_refused():
    from services.library.defaults_pack.pricing import price_from_pack

    with pytest.raises(KeyError, match="nuclear"):
        price_from_pack(_net(), "generators", "g", "nuclear", version=VERSION)


# ── provenance (S1a, gatekeeper 2026-10-13): typed fields only, never derived ones ──


def _row(key):
    return next(r for r in _pack().cost_values if r.key == key)


def test_a_single_part_asset_records_each_typed_field_it_wrote():
    from services.asset_schema import provenance
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(n, "generators", "g", "gas-genset", version=VERSION)
    got = provenance.get(n, "Generator", "g")
    assert set(got) == {"overnight_cost", "lifetime", "fom_cost", "discount_rate"}   # no capital_cost
    pack = _pack()
    source = f"{pack.pack_id}@{VERSION}#{pack.hash[:16]}"
    assert all(e["status"] == "default" and e["source"] == source for e in got.values())
    over = _row("gas-genset.investment.overnight")
    assert got["overnight_cost"]["range"] == [over.range.low, over.range.high]
    assert got["overnight_cost"]["currency_year"] == 2020
    assert got["overnight_cost"]["source_year"] == over.source_year
    life = _row("gas-genset.investment.lifetime")
    assert got["lifetime"]["range"] == [life.range.low, life.range.high]
    assert got["overnight_cost"]["illustrative"] is False


def test_a_battery_records_its_six_part_fields_never_the_derived_ones():
    from services.asset_schema import provenance
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(n, "storage_units", "s", "battery", version=VERSION)
    got = provenance.get(n, "StorageUnit", "s")
    assert set(got) == {f"inv_{p}_{f}" for p in ("power", "energy")
                        for f in ("overnight", "lifetime", "fom_share")} | {"discount_rate"}
    assert not {"capital_cost", "fom_cost", "lifetime"} & set(got)


def test_a_link_records_what_it_wrote_with_the_efficiency_scaled_range():
    from services.asset_schema import provenance
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    price_from_pack(n, "links", "fc", "fuel-cell", version=VERSION)
    got = provenance.get(n, "Link", "fc")
    assert set(got) == {"overnight_cost", "lifetime", "fom_cost", "discount_rate"}
    over = _row("fuel-cell.investment.overnight")
    assert got["overnight_cost"]["range"] == pytest.approx([0.5 * over.range.low, 0.5 * over.range.high])


# ── the disclosed annual charge (gatekeeper note on #198, option a) ──


def test_a_single_part_asset_discloses_its_annuity_fom_and_lp_charge():
    """`capital_cost` is the annuity alone (as everywhere in the GUI), `fom_cost` beside it,
    and `annualised_cost_eur_per_mw_yr` is what PyPSA's periodized cost charges per MW-year."""
    from pypsa.costs import periodized_cost

    from services.library.defaults_pack.pricing import price_from_pack
    from services.solver.periodized_costs import _annuity

    n = _net()
    out = price_from_pack(n, "generators", "g", "gas-genset", version=VERSION)
    row = n.generators.loc["g"]
    annuity = row["overnight_cost"] * _annuity(row["discount_rate"], row["lifetime"])
    assert out["capital_cost"] == pytest.approx(annuity, rel=1e-12) and out["capital_cost"] > 0
    assert out["fom_cost"] == pytest.approx(row["fom_cost"])
    charged = periodized_cost(0.0, row["overnight_cost"], row["discount_rate"], row["lifetime"],
                              row["fom_cost"])
    assert out["annualised_cost_eur_per_mw_yr"] == pytest.approx(charged, rel=1e-12)
    assert row["capital_cost"] == 0.0               # the column PyPSA ignores is still 0


def test_a_battery_discloses_its_derived_annuity_and_fom():
    from services.library.defaults_pack.pricing import price_from_pack

    n = _net()
    out = price_from_pack(n, "storage_units", "s", "battery", version=VERSION)
    row = n.storage_units.loc["s"]
    assert out["capital_cost"] == pytest.approx(row["capital_cost"])
    assert out["annualised_cost_eur_per_mw_yr"] == pytest.approx(row["capital_cost"] + row["fom_cost"])
