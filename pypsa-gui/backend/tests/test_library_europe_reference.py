"""Source-cell oracles and boundary checks for optional European references."""
import json

import pytest

from services.library import europe_reference as R
from services.library.defaults_pack.loader import CostPart, DefaultsPackError, load_defaults_pack


@pytest.mark.parametrize("technology,year", R.available_europe_technology_references())
def test_every_reference_retains_source_and_native_round_trip(technology, year):
    ref = R.load_europe_technology_reference(technology, year)
    assert ref.licence == "CC-BY-4.0"
    assert len(ref.source_hash) == 64
    assert ref.source_sheet and ref.attribution
    assert "benchmark" in ref.applicability
    assert ref.unmapped_terms
    assert CostPart.model_validate_json(ref.cost_part.model_dump_json()) == ref.cost_part
    for parameter in ("overnight", "fom_share", "lifetime"):
        native = getattr(ref.cost_part, parameter)
        assert native.original_value == ref.source_cells[parameter]["value"]
        assert native.value == pytest.approx(native.original_value * native.conversion_factor)
        assert native.projection_year == year
        assert ref.source_cells[parameter]["cell"] in native.source


def test_wind_million_scaling_and_absolute_annual_om_oracle():
    ref = R.load_europe_technology_reference("onwind", 2030)
    # DEA 20 Onshore turbines!E17 and E25, 2020 EUR.
    part = ref.cost_part
    assert part.overnight.value == pytest.approx(1_146_644.89966)
    assert part.overnight.value * part.fom_share.value == pytest.approx(16_662.66796409161)
    assert part.lifetime.value == 30
    assert ref.currency_year == 2020
    assert "connection" in ref.included_costs
    assert any("Variable O&M" in term for term in ref.unmapped_terms)
    assert part.efficiency is None


def test_pv_ac_basis_and_money_year_are_not_historical_generic_defaults():
    ref = R.load_europe_technology_reference("solar-utility", 2030)
    # D17 AC vs D18 DC; source does not give the same number for them.
    assert ref.cost_part.overnight.value == pytest.approx(556_220.8489115281)
    assert ref.cost_part.overnight.value != pytest.approx(427_862.1914704062)
    assert ref.currency_year == 2025
    assert "AC output" in ref.capacity_basis
    assert ref.cost_part.overnight.value * ref.cost_part.fom_share.value == pytest.approx(9880)


def test_commercial_rooftop_is_a_separate_archetype():
    ref = R.load_europe_technology_reference("solar-rooftop-commercial", 2030)
    assert ref.cost_part.overnight.value == pytest.approx(647_346.6413938957)
    assert ref.cost_part.overnight.value * ref.cost_part.fom_share.value == pytest.approx(2330)
    assert "comm.&industrial" in ref.source_sheet


def test_electrolyser_power_and_efficiency_use_electrical_input_lhv():
    ref = R.load_europe_technology_reference("electrolysis-aec-100mw", 2025)
    # C27 EUR/kW total input and C15 percentage LHV efficiency.
    assert ref.cost_part.overnight.value == pytest.approx(1_254_040.85660402)
    assert ref.cost_part.fom_share.value == pytest.approx(0.05)
    assert ref.cost_part.efficiency.value == pytest.approx(0.6060545454545454)
    assert ref.cost_part.lifetime.value == 25
    assert "input" in ref.capacity_basis
    assert any("Stack replacement is excluded" in term for term in ref.unmapped_terms)


@pytest.mark.parametrize("technology,year", [
    ("onwind", 2035), ("solar-utility", 2031), ("electrolysis-soec-100mw", 2030),
    ("solar-rooftop", 2030), ("solar-utility", True), ("onwind", "2030"),
])
def test_unknown_or_ambiguous_selection_is_refused(technology, year):
    with pytest.raises(DefaultsPackError):
        R.load_europe_technology_reference(technology, year)


def test_modified_reference_bytes_are_refused(tmp_path, monkeypatch):
    replacement = tmp_path / "changed.json"
    raw = json.loads(R._DATA_FILE.read_text())
    raw["entries"][0]["cells"]["overnight"]["value"] = 1
    replacement.write_text(json.dumps(raw))
    monkeypatch.setattr(R, "_DATA_FILE", replacement)
    with pytest.raises(DefaultsPackError, match="pinned release"):
        R.load_europe_technology_reference("onwind", 2025)


def test_calls_are_independent_and_do_not_replace_default_pack():
    before = load_defaults_pack()
    ref = R.load_europe_technology_reference("onwind", 2030)
    ref.source_cells["overnight"]["value"] = -1
    ref.unmapped_terms.clear()
    again = R.load_europe_technology_reference("onwind", 2030)
    assert again.source_cells["overnight"]["value"] == pytest.approx(1.14664489966)
    assert again.unmapped_terms
    after = load_defaults_pack()
    assert (before.pack_id, before.version, before.hash) == (after.pack_id, after.version, after.hash)


def test_only_explicit_source_projection_years_are_available():
    selectors = R.available_europe_technology_references()
    assert len(selectors) == len(set(selectors)) == 24
    assert ("onwind", 2035) not in selectors
    assert ("solar-utility", 2035) in selectors
