"""
U3 S2 and rule 5 (parent plan §3 rule 5, §10 S2): the guided ledger as a view
of S1a's per-field provenance store (`services/asset_schema/provenance`).

* The pack stamps every asset field it writes from a ledger row through
  `provenance.record`: `default` with the defaults pack's id and hash as the
  source, or the row's own status and source when the guided user changed it.
* An Expert edit is a field the store calls `customised` whose value no longer
  matches what the ledger compiles to; its row reads `customised`,
  `changed_by: expert`, naming the option. A view only: nothing is written.
* The mapping names only real, typed (non-derived) S1a fields.
"""
from __future__ import annotations

import math

import pytest

from models.study import AssumptionsLedger
from services.asset_schema import fields as asset_fields
from services.asset_schema import provenance
from services.study import asset_view, ledger as study_ledger, packs
from services.study import library as study_library
from services.study import questions as Q

INTAKE = {
    "site": {"zone": "DE", "connection_mw": 2.0},
    "load": {"source": "sector_profile", "profile": "commercial_office", "annual_mwh": 4000.0},
    "pv": {"enabled": True, "kind": "rooftop"},
}


@pytest.fixture(scope="module")
def library():
    return study_library.load_defaults()


@pytest.fixture(scope="module")
def ledger(library) -> AssumptionsLedger:
    return study_library.seed_ledger(Q.BESS_AT_SITE, INTAKE, library)


@pytest.fixture(scope="module")
def bess_pv(ledger, library):
    return packs.build_site_network(INTAKE, ledger, "bess_pv_2h", library=library)


def test_every_mapped_field_is_a_real_typed_s1a_field():
    for m in asset_view.LEDGER_FIELDS:
        specs = {f.key: f for f in asset_fields.fields_for(m.component)}
        assert m.field in specs, (m.key, m.component, m.field)
        assert not specs[m.field].derived, f"{m.field} is derived; a person never types it"


def test_the_mapping_covers_the_rows_the_pack_reads(ledger):
    keys = {m.key for m in asset_view.LEDGER_FIELDS}
    assert {"battery_inverter_eur_per_kw", "battery_storage_eur_per_kwh",
            "battery_inverter_lifetime_years", "battery_storage_lifetime_years",
            "battery_inverter_fom_pct_per_year", "battery_round_trip_efficiency",
            "discount_rate", "pv_rooftop_eur_per_kw", "pv_rooftop_lifetime_years"} <= keys
    assert keys <= {r.key for r in ledger.rows}


def test_the_pack_stamps_its_defaults_with_the_pack_id(bess_pv, ledger):
    got = provenance.get(bess_pv, "StorageUnit", packs.BATTERY_NAME)
    entry = got["inv_power_overnight"]
    assert entry["status"] == "default"
    assert entry["source"] == ledger.ledger_version
    assert ledger.ledger_version.startswith("generic-defaults ")
    row = next(r for r in ledger.rows if r.key == "battery_inverter_eur_per_kw")
    assert entry["currency_year"] == row.currency_year
    if row.range is not None:
        assert entry["range"] == pytest.approx([row.range.low * 1000.0, row.range.high * 1000.0])
    pv = provenance.get(bess_pv, "Generator", packs.PV_NAME)
    assert pv["overnight_cost"]["status"] == "default"
    # Every mapped field on an asset the option builds is stamped, from the PV
    # kind the intake chose (rooftop here), never from the other kind's rows.
    for m in asset_view.LEDGER_FIELDS:
        if m.key.startswith("pv_utility_"):
            continue
        rec = provenance.get(bess_pv, m.component, m.name)
        assert m.field in rec, (m.component, m.name, m.field)
    assert pv["overnight_cost"]["source"] == ledger.ledger_version
    assert bess_pv.meta[packs.PACK_META_KEY]["pv_kind"] == "rooftop"


def test_the_stamped_value_is_the_one_the_network_carries(bess_pv, ledger):
    """The mapping's scale is the pack's: the view compares like with like."""
    for m in asset_view.LEDGER_FIELDS:
        if m.key.startswith("pv_utility_"):
            continue
        table = getattr(bess_pv, "storage_units" if m.component == "StorageUnit" else "generators")
        assert float(table.at[m.name, m.field]) == pytest.approx(
            asset_view.compiled_value(m, ledger), rel=1e-9), m.field


def test_a_guided_edit_is_stamped_customised_with_its_source(library):
    led = study_library.seed_ledger(Q.BESS_AT_SITE, INTAKE, library)
    led = study_ledger.apply_user_row(led, "battery_storage_eur_per_kwh", 150.0, unit="EUR/kWh",
                                      changed_by="u", changed_at=None, source="supplier quote")
    n = packs.build_site_network(INTAKE, led, "bess_2h", library=library)
    entry = provenance.get(n, "StorageUnit", packs.BATTERY_NAME)["inv_energy_overnight"]
    assert entry["status"] == "customised"
    assert entry["source"] == "supplier quote"


def test_the_baseline_stamps_nothing(ledger, library):
    n = packs.build_site_network(INTAKE, ledger, "none", library=library)
    assert provenance.get(n, "StorageUnit", packs.BATTERY_NAME) == {}
    assert provenance.get(n, "Generator", packs.PV_NAME) == {}


# ── rule 5: Expert edits ─────────────────────────────────────────────────

def _copy(n):
    return n.copy()


def test_an_untouched_fork_has_no_expert_edits(bess_pv, ledger):
    assert asset_view.expert_edits(bess_pv, ledger) == []


def test_an_expert_edit_is_a_customised_field_whose_value_moved(bess_pv, ledger):
    n = _copy(bess_pv)
    n.storage_units.at[packs.BATTERY_NAME, "inv_power_overnight"] *= 1.2
    provenance.mark_customised(n, "StorageUnit", packs.BATTERY_NAME, ["inv_power_overnight"])
    edits = asset_view.expert_edits(n, ledger)
    assert [e.key for e in edits] == ["battery_inverter_eur_per_kw"]
    assert edits[0].field == "inv_power_overnight"


def test_a_stamp_without_a_change_or_a_change_without_a_stamp_is_not_an_expert_edit(bess_pv, ledger):
    same = _copy(bess_pv)
    provenance.mark_customised(same, "StorageUnit", packs.BATTERY_NAME, ["inv_power_overnight"])
    assert asset_view.expert_edits(same, ledger) == []
    # A value the pack wrote, still `default` in the store, was not a person's edit.
    moved = _copy(bess_pv)
    moved.storage_units.at[packs.BATTERY_NAME, "inv_power_overnight"] *= 1.2
    assert asset_view.expert_edits(moved, ledger) == []


def test_the_round_trip_efficiency_reads_through_its_square_root(bess_pv, ledger):
    n = _copy(bess_pv)
    n.storage_units.at[packs.BATTERY_NAME, "efficiency_store"] = 0.9
    provenance.mark_customised(n, "StorageUnit", packs.BATTERY_NAME, ["efficiency_store"])
    assert [e.key for e in asset_view.expert_edits(n, ledger)] == ["battery_round_trip_efficiency"]
    rte = next(r.value for r in ledger.rows if r.key == "battery_round_trip_efficiency")
    assert float(bess_pv.storage_units.at[packs.BATTERY_NAME, "efficiency_dispatch"]) == \
        pytest.approx(math.sqrt(rte))


def test_marking_shows_the_row_customised_by_expert_and_names_the_option(bess_pv, ledger):
    n = _copy(bess_pv)
    n.storage_units.at[packs.BATTERY_NAME, "inv_power_overnight"] *= 1.2
    provenance.mark_customised(n, "StorageUnit", packs.BATTERY_NAME, ["inv_power_overnight"])
    viewed = asset_view.mark_expert_edits(ledger, {"bess_pv_2h": asset_view.expert_edits(n, ledger)})
    row = next(r for r in viewed.rows if r.key == "battery_inverter_eur_per_kw")
    assert row.status == "customised" and row.changed_by == "expert"
    assert "changed_in_expert:battery_inverter_eur_per_kw:bess_pv_2h" in viewed.honesty_notes
    # A view: the value is still the ledger's, and the input ledger is unchanged.
    orig = next(r for r in ledger.rows if r.key == "battery_inverter_eur_per_kw")
    assert row.value == orig.value and orig.status == "default"
    assert asset_view.mark_expert_edits(ledger, {}) is ledger


def test_the_stamps_survive_the_netcdf_round_trip(bess_pv, tmp_path):
    import pypsa

    p = tmp_path / "n.nc"
    bess_pv.export_to_netcdf(p)
    back = pypsa.Network(p)
    assert provenance.get(back, "StorageUnit", packs.BATTERY_NAME)["inv_power_overnight"]["status"] \
        == "default"
