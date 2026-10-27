"""
Regional technology-cost packs (GS-2; owner decision 2026-10-21: region-tagged versions of the
in-tree defaults pack for CER EF2026, UK DESNZ 2025 and the EIA reference plants, a sanctioned
exception to IC spec decision 20 for those three sources only).

What is pinned here, on a synthetic region directory (the loader's rules, not any one source):
  * f. a money row the region does not carry is refused, never filled from the EU row, because a
       2020-EUR row read as CAD is a silent ~1.5x error: `MissingRegionalRow`;
  * g. a row without a currency (lifetime, FOM share, efficiency) falls back, flagged;
  * h. every overlay row's currency is the manifest's, and its unit is the currency's own;
  * i. the EU pack, `load_defaults_pack(None)` and the EU hashes are untouched;
  * the base version must exist and its hash must match the manifest's `base_hash`;
  * a region is explicit: `load_defaults_pack(region=...)`, `price_from_pack(..., region=...)`.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from services.library.defaults_pack import loader as D

BASE = "2026-10-22"
FIXTURES = Path(__file__).parent / "fixtures" / "defaults_pack"


def _row(**kw) -> dict:
    row = {c: "" for c in D._TYPED_COLUMNS}
    row.update(capacity_basis="not_applicable", includes_grid_connection="false")
    row.update(kw)
    return row


def _solar_overnight(**kw) -> dict:
    return _row(key="solar-utility.investment.overnight", technology="solar-utility",
                part="investment", basis="per_MW", source_technology="Large Scale Solar PV",
                parameter="overnight", label="Utility PV overnight cost", original_value="1000",
                original_unit="XTS/kW", unit="XTS/MW", currency="XTS", money_year="2024", capacity_basis="output_electric_ac",
                includes_grid_connection="false",
                price_basis="real", source="src_test: Table 1", source_year="2026",
                projection_year="2030", domain="[0, inf)", illustrative="false",
                note="Synthetic.", **kw)


def _solar_lifetime() -> dict:
    return _row(key="solar-utility.investment.lifetime", technology="solar-utility",
                part="investment", basis="per_MW", source_technology="Large Scale Solar PV",
                parameter="lifetime", label="Utility PV lifetime", original_value="35",
                original_unit="years", unit="years", price_basis="real",
                source="src_test: Table 1", source_year="2026", domain="[1, inf)",
                illustrative="false", note="Synthetic.", capacity_basis="output_electric_ac",
                includes_grid_connection="false")


def _write_region(root: Path, rows: list[dict], *, region="xt_test", currency="XTS",
                  base_hash=None, base_version=BASE, date="2026-10-22") -> Path:
    d = root / f"{date}+{region}"
    d.mkdir()
    base_hash = base_hash or D.load_defaults_pack(BASE).hash
    manifest = {
        "pack_id": D.PACK_ID, "version": date, "region": region, "currency": currency,
        "base_version": base_version, "base_hash": base_hash,
        "title": "Synthetic regional overlay",
        "sources": [{"source_id": "src_test", "title": "Synthetic source",
                     "publisher": "Nobody", "url": "https://example.invalid/s",
                     "file": "s.csv", "sha256": "0" * 64, "licence": "test licence",
                     "terms_url": "https://example.invalid/terms",
                     "redistribution_status": "open_verified",
                     "owner_clearance": "2026-10-21, coordinator-relayed (decision G2)"}],
        "source_cells": {"file": "source_cells.csv"},
        "files": {"values": "values.csv"},
    }
    (d / "source_cells.csv").write_text("key,source_file,locator,value,unit\n", encoding="utf-8")
    manifest["source_cells"]["sha256"] = D._file_sha256(d / "source_cells.csv")
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with (d / "values.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=D._TYPED_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return d


@pytest.fixture
def versions(tmp_path, monkeypatch):
    """A versions dir holding the real EU base version and nothing else."""
    root = tmp_path / "versions"
    root.mkdir()
    shutil.copytree(D.VERSIONS_DIR / BASE, root / BASE)
    monkeypatch.setattr(D, "VERSIONS_DIR", root)
    D._cached.cache_clear()
    D._cached_region.cache_clear()
    yield root
    D._cached.cache_clear()
    D._cached_region.cache_clear()


@pytest.fixture
def with_region(versions, monkeypatch):
    monkeypatch.setitem(D.UNIT_CONVERSIONS, ("XTS/kW", "XTS/MW"), 1000.0)
    _write_region(versions, [_solar_overnight(), _solar_lifetime()])
    return versions


# ── f. no fallback across currencies ────────────────────────────────────────


def test_a_money_row_the_region_lacks_is_refused_not_filled_from_the_eu_row(with_region):
    pack = D.load_defaults_pack(region="xt_test")
    assert pack.region_info.currency == "XTS"
    with pytest.raises(D.MissingRegionalRow) as exc:
        pack.value("onwind.investment.overnight")          # an EUR row in the base
    assert exc.value.code == "defaults_pack_regional_row_missing"
    assert exc.value.key == "onwind.investment.overnight"
    assert exc.value.region == "xt_test" and exc.value.currency == "XTS"
    assert "onwind.investment.overnight" not in [v.key for v in pack.cost_values]


def test_pricing_a_technology_the_region_lacks_raises_before_any_write(with_region):
    import pypsa

    from services.library.defaults_pack.pricing import price_from_pack

    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b", p_nom_extendable=True)
    with pytest.raises(D.MissingRegionalRow):
        price_from_pack(n, "generators", "g", "onwind", version="2026-10-22", region="xt_test")
    assert float(n.generators.at["g", "capital_cost"]) == 0.0      # nothing was written


# ── g. a currency-less row falls back, flagged ──────────────────────────────


def test_a_row_without_a_currency_falls_back_to_the_eu_row_and_is_flagged(with_region):
    rows = [_solar_overnight()]                             # no lifetime / fom_share of its own
    shutil.rmtree(with_region / "2026-10-22+xt_test")
    _write_region(with_region, rows)
    pack = D.load_defaults_pack(region="xt_test")
    base = D.load_defaults_pack(BASE)
    life = pack.value("solar-utility.investment.lifetime")
    assert life.value == base.value("solar-utility.investment.lifetime").value
    info = pack.region_info
    assert info.row_source["solar-utility.investment.lifetime"] == "eu_fallback"
    assert info.row_source["solar-utility.investment.overnight"] == "regional"
    fom = pack.value("solar-utility.investment.fom_share")
    assert fom.value == base.value("solar-utility.investment.fom_share").value


def test_the_regional_row_wins_over_the_eu_row(with_region):
    pack = D.load_defaults_pack(region="xt_test")
    assert pack.value("solar-utility.investment.overnight").value == 1_000_000.0
    assert pack.value("solar-utility.investment.overnight").unit == "XTS/MW"
    assert pack.value("solar-utility.investment.lifetime").value == 35.0
    assert pack.region_info.row_source["solar-utility.investment.lifetime"] == "regional"


# ── h. currency discipline ──────────────────────────────────────────────────


def test_every_overlay_row_must_be_in_the_manifests_currency(versions, monkeypatch):
    monkeypatch.setitem(D.UNIT_CONVERSIONS, ("XTS/kW", "XTS/MW"), 1000.0)
    bad = _solar_overnight()
    bad["currency"] = "EUR"
    _write_region(versions, [bad])
    with pytest.raises(D.DefaultsPackError, match="currency"):
        D.load_defaults_pack(region="xt_test")


def test_a_money_unit_of_another_currency_than_its_original_is_refused(versions, monkeypatch):
    monkeypatch.setitem(D.UNIT_CONVERSIONS, ("XTS/kW", "EUR/MW"), 1000.0)
    bad = _solar_overnight()
    bad["unit"] = "EUR/MW"
    _write_region(versions, [bad])
    with pytest.raises(D.DefaultsPackError, match="currency"):
        D.load_defaults_pack(region="xt_test")


def test_no_unit_conversion_turns_one_currency_into_another():
    for (orig, pack_unit) in D.UNIT_CONVERSIONS:
        o, p = D._money_currency(orig), D._money_currency(pack_unit)
        assert o == p, f"{orig!r} -> {pack_unit!r} would change currency"


def test_an_overlay_key_must_exist_in_the_eu_pack_with_the_same_dimension(versions, monkeypatch):
    monkeypatch.setitem(D.UNIT_CONVERSIONS, ("XTS/kW", "XTS/MW"), 1000.0)
    monkeypatch.setitem(D.UNIT_CONVERSIONS, ("XTS/kWh", "XTS/kWh"), 1.0)
    unknown = _solar_overnight()
    unknown["key"] = "solar-utility.investment.novel"
    _write_region(versions, [unknown])
    with pytest.raises(D.DefaultsPackError, match="not a row of the base"):
        D.load_defaults_pack(region="xt_test")
    shutil.rmtree(versions / "2026-10-22+xt_test")
    wrong = _solar_overnight()
    wrong["unit"], wrong["original_unit"] = "XTS/kWh", "XTS/kWh"
    _write_region(versions, [wrong])
    with pytest.raises(D.DefaultsPackError, match="dimension|needs overnight"):
        D.load_defaults_pack(region="xt_test")


def test_a_derived_row_cannot_take_an_input_the_region_does_not_carry(versions, monkeypatch):
    monkeypatch.setitem(D.UNIT_CONVERSIONS, ("XTS/kW", "XTS/MW"), 1000.0)
    derived = _row(key="battery.round_trip_efficiency", technology="battery", part="",
                   parameter="round_trip_efficiency", label="x", original_value="0.9",
                   original_unit="per unit", unit="per unit", source="src_test: T",
                   source_year="2026", illustrative="false", note="x",
                   derived_inputs="battery.power.efficiency", formula_id="square")
    base_keys = {v.key for v in D.load_defaults_pack(BASE).cost_values}
    assert "battery.round_trip_efficiency" in base_keys, "fixture assumes the EU derived row"
    _write_region(versions, [derived])
    with pytest.raises(D.DefaultsPackError, match="derives from unknown"):
        D.load_defaults_pack(region="xt_test")


# ── the base version ────────────────────────────────────────────────────────


def test_a_base_version_that_is_not_vendored_is_refused(versions, monkeypatch):
    monkeypatch.setitem(D.UNIT_CONVERSIONS, ("XTS/kW", "XTS/MW"), 1000.0)
    _write_region(versions, [_solar_overnight()], base_version="2000-01-01")
    with pytest.raises(D.UnknownPackVersion):
        D.load_defaults_pack(region="xt_test")


def test_a_base_hash_that_does_not_match_is_refused(versions, monkeypatch):
    monkeypatch.setitem(D.UNIT_CONVERSIONS, ("XTS/kW", "XTS/MW"), 1000.0)
    _write_region(versions, [_solar_overnight()], base_hash="f" * 64)
    with pytest.raises(D.DefaultsPackError, match="base_hash"):
        D.load_defaults_pack(region="xt_test")


# ── regions are explicit, the EU pack is untouched ──────────────────────────


def test_an_unknown_region_is_refused_and_lists_the_known_ones(with_region):
    with pytest.raises(D.UnknownPackRegion) as exc:
        D.load_defaults_pack(region="nowhere")
    assert exc.value.code == "defaults_pack_region_unknown"
    assert exc.value.known == ("xt_test",)
    assert D.available_regions() == ("xt_test",)


def test_a_region_directory_is_not_a_version_and_the_latest_stays_eu(with_region):
    assert D.available_versions() == (BASE,)
    latest = D.load_defaults_pack()
    assert latest.version == BASE and latest.region_info is None
    with pytest.raises(D.UnknownPackVersion):
        D.load_defaults_pack("2026-10-22+xt_test")


def test_a_regional_pack_has_its_own_version_hash_and_stamp(with_region):
    pack = D.load_defaults_pack(region="xt_test")
    eu = D.load_defaults_pack(BASE)
    assert pack.version == "2026-10-22+xt_test" and pack.hash != eu.hash
    assert D.parse_pack_stamp(pack.stamp) == ("generic_defaults", pack.version, pack.hash)
    again = D.load_defaults_pack(region="xt_test")
    assert again.hash == pack.hash
    assert D.load_defaults_pack("2026-10-22", region="xt_test").hash == pack.hash


def test_the_eu_pack_is_byte_identical_with_regions_present(with_region):
    pinned = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    with_regions = D.load_defaults_pack(None)
    assert with_regions.hash == pinned[BASE]
    assert D._canonical_hash(D.canonical_payload(D.VERSIONS_DIR / BASE)) == pinned[BASE]
    assert with_regions.version == BASE
    assert with_regions.model_dump_json() == D.load_defaults_pack(BASE).model_dump_json()
    assert "region" not in with_regions.model_dump_json()      # the EU dump gained no field


def test_pricing_stamps_the_regional_source_and_never_the_eu_one(with_region):
    import pypsa

    from services.library.defaults_pack.pricing import price_from_pack

    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b", p_nom_extendable=True)
    out = price_from_pack(n, "generators", "g", "solar-utility", version="2026-10-22",
                          region="xt_test")
    pack = D.load_defaults_pack(region="xt_test")
    assert out["pack_version"] == "2026-10-22+xt_test" and out["pack_hash"] == pack.hash
    assert out["overnight_cost"] == 1_000_000.0 and out["lifetime"] == 35.0
    eu = price_from_pack(n, "generators", "g", "solar-utility", version=BASE)
    assert eu["pack_version"] == BASE and eu["overnight_cost"] != out["overnight_cost"]


# ── the shipped regional packs: CA (CER), UK (DESNZ), US (EIA) ──────────────

REGIONS = {"ca_federal": "CAD", "uk": "GBP", "us_federal": "USD"}
SHIPPED = "2026-10-22"
LATEST = "2026-10-26"      # money-year 01: the regional packs re-based on the 2025-money EU pack
CLEARANCE = "2026-10-21, coordinator-relayed (decision G2)"
SOURCE_IDS = {"ca_federal": "cost_canada_cer_ef2026", "uk": "cost_uk_desnz_2025",
              "us_federal": "cost_eia_reference_plants"}


@pytest.fixture(autouse=False)
def shipped_caches():
    D._cached.cache_clear()
    D._cached_region.cache_clear()
    yield
    D._cached.cache_clear()
    D._cached_region.cache_clear()


def test_the_three_regions_ship_with_their_currency_and_are_not_versions(shipped_caches):
    assert D.available_regions() == tuple(sorted(REGIONS))
    # The newest regional packs sit on LATEST (money-year 01); the ones on BASE stay shipped beside them.
    assert BASE in D.available_versions() and not any("+" in v for v in D.available_versions())
    for region, currency in REGIONS.items():
        pack = D.load_defaults_pack(region=region)
        assert pack.version == f"{LATEST}+{region}"
        assert pack.region_info.currency == currency and pack.region_info.base_version == LATEST
        for v in pack.cost_values:
            if v.currency is not None:
                assert v.currency == currency and v.unit.startswith(currency + "/")


def test_regional_hashes_are_pinned_and_the_eu_pins_are_unchanged(shipped_caches):
    pins = json.loads((FIXTURES / "pack_hashes.json").read_text())
    assert pins["generic_defaults"]["2026-10-20"] == \
        "1266cc82fb8826c33281317f8916a7bf10f95c1bf72437e3ba0f77bd993ab231"
    regional = json.loads((FIXTURES / "regional_pack_hashes.json").read_text())[
        "generic_defaults_regional"]
    # the 2026-10-21 versions are legacy (untyped, GS-2b) and stay pinned beside the typed ones
    assert set(regional) == {f"{d}+{r}" for r in REGIONS for d in ("2026-10-21", SHIPPED, LATEST)}
    for region in REGIONS:
        for date in ("2026-10-21", SHIPPED, LATEST):
            pack = D.load_defaults_pack(date, region=region)
            assert pack.hash == regional[pack.version]
            assert pack.region_info.base_hash == \
                pins["generic_defaults"][pack.region_info.base_version]


def test_every_shipped_source_records_licence_hash_and_the_owners_clearance(shipped_caches):
    for region in REGIONS:
        info = D.load_defaults_pack(region=region).region_info
        assert [s["source_id"] for s in info.sources] == [SOURCE_IDS[region]]
        [src] = info.sources
        assert src["owner_clearance"] == CLEARANCE
        assert src["redistribution_status"] == "open_verified"
        assert src["licence"] and src["terms_url"].startswith("https://")
        assert len(src["sha256"]) == 64 and src["size_bytes"] > 0
        assert src["file_url"].startswith("https://") and src["attribution"]


def test_every_regional_row_cites_its_source_table_money_year_and_mapping(shipped_caches):
    for region in REGIONS:
        pack = D.load_defaults_pack(region=region)
        for v in pack.cost_values:
            if pack.region_info.row_source[v.key] != "regional":
                continue
            assert v.source.startswith(SOURCE_IDS[region] + ": ") and v.source_url
            assert v.note and "Maps" in v.note or v.parameter != "overnight"
            assert not v.illustrative and v.price_basis == "real"
            if v.parameter == "overnight":
                assert v.currency_year in (2023, 2024, 2025)
                assert v.original_unit.endswith("/kW") and v.unit.endswith("/MW")


def test_source_cells_back_every_regional_row(shipped_caches):
    for region in REGIONS:
        pack = D.load_defaults_pack(region=region)
        path = D.VERSIONS_DIR / f"{SHIPPED}+{region}" / "source_cells.csv"
        with path.open(encoding="utf-8", newline="") as fh:
            cells = list(csv.DictReader(fh))
        keys = {c["key"] for c in cells}
        regional = {k for k, s in pack.region_info.row_source.items() if s == "regional"}
        assert keys == regional
        by = {}
        for c in cells:
            by.setdefault(c["key"], []).append(c)
        for key in regional:
            v = pack.value(key)
            assert all(c["locator"] and c["source_file"] for c in by[key])
            if v.parameter == "lifetime":
                assert v.original_value == float(by[key][0]["value"])
            if v.parameter == "fom_share" and region != "uk":     # FOM / overnight, as the note says
                fom = next(float(c["value"]) for c in by[key] if "Fixed O&M" in c["locator"])
                over = next(float(c["value"]) for c in by[key] if "Overnight" in c["locator"]
                            or "Capital Cost" in c["locator"].split(",")[-1])
                assert v.original_value == pytest.approx(fom / over * 100, abs=5e-5)


def test_uk_overnight_and_fom_are_recomputed_from_the_annex_cells(shipped_caches):
    pack = D.load_defaults_pack(region="uk")
    path = D.VERSIONS_DIR / f"{SHIPPED}+uk" / "source_cells.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        cells = list(csv.DictReader(fh))

    def cell(key, label):
        return next(float(c["value"]) for c in cells if c["key"] == key and label in c["locator"])

    for tech in ("solar-utility", "onwind", "gas-genset"):
        k = f"{tech}.investment"
        over = cell(f"{k}.overnight", "Pre-development") + cell(f"{k}.overnight", "Construction")
        assert not any("Infrastructure" in c["locator"] for c in cells if c["key"] == f"{k}.overnight")
        assert pack.value(f"{k}.overnight").value == pytest.approx(over * 1000, abs=0.1)
        fom = (cell(f"{k}.fom_share", "Fixed O&M") + cell(f"{k}.fom_share", "Insurance")) / 1000
        assert pack.value(f"{k}.fom_share").original_value == pytest.approx(fom / over * 100,
                                                                              abs=5e-5)


def test_unmapped_technologies_raise_instead_of_borrowing_the_eu_row(shipped_caches):
    for region in REGIONS:
        pack = D.load_defaults_pack(region=region)
        for key in ("battery.power.overnight", "battery.energy.overnight",
                    "electrolysis.investment.overnight", "diesel-genset.investment.overnight",
                    "transformer.tr_132_33_40.investment.overnight"):
            with pytest.raises(D.MissingRegionalRow):
                pack.value(key)


def test_a_us_lifetime_the_study_does_not_state_falls_back_flagged(shipped_caches):
    pack = D.load_defaults_pack(region="us_federal")
    eu = D.load_defaults_pack(BASE)
    assert pack.region_info.row_source["solar-utility.investment.lifetime"] == "eu_fallback"
    assert pack.value("solar-utility.investment.lifetime").value == \
        eu.value("solar-utility.investment.lifetime").value
    assert pack.value("solar-utility.investment.overnight").value == 1_502_000.0


def test_a_ca_project_prices_solar_in_cad_from_its_region_and_an_eu_one_is_unchanged(shipped_caches):
    import pypsa
    from pypsa.costs import annuity

    from services.library.defaults_pack.pricing import price_from_pack

    def net():
        n = pypsa.Network()
        n.add("Bus", "b")
        n.add("Generator", "pv", bus="b", p_nom_extendable=True)
        return n

    ca = price_from_pack(net(), "generators", "pv", "solar-utility", version=SHIPPED,
                         region="ca_federal")
    pack = D.load_defaults_pack(SHIPPED, region="ca_federal")
    rate = float(pack.finance.discount_rate.value)
    over = 2_078_400.0
    assert ca["pack_version"] == f"{SHIPPED}+ca_federal" and ca["pack_hash"] == pack.hash
    assert ca["overnight_cost"] == over and ca["lifetime"] == 30.0
    assert ca["capital_cost"] == pytest.approx(over * annuity(rate, 30.0))
    assert ca["fom_cost"] == pytest.approx(over * 0.01938)
    eu = price_from_pack(net(), "generators", "pv", "solar-utility", version=BASE)
    assert eu["pack_version"] == BASE and eu["overnight_cost"] == pytest.approx(482_478.5)
    with pytest.raises(D.MissingRegionalRow):
        price_from_pack(net(), "generators", "pv", "solar-rooftop", version=SHIPPED,
                        region="ca_federal")


def test_pricing_carries_the_regions_currency_never_an_eur_label(shipped_caches):
    import pypsa

    from services.asset_schema import provenance
    from services.library.defaults_pack.pricing import price_from_pack

    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "pv", bus="b", p_nom_extendable=True)
    out = price_from_pack(n, "generators", "pv", "solar-utility", version=SHIPPED,
                          region="us_federal")
    assert out["currency"] == "USD"
    assert "annualised_cost_per_mw_yr" in out
    assert not any("eur" in k for k in out)
    prov = provenance._root(n)["Generator"]["pv"]
    assert prov["overnight_cost"]["currency"] == "USD" and prov["fom_cost"]["currency"] == "USD"
    assert prov["overnight_cost"]["currency_year"] == 2023
    assert "currency" not in prov["lifetime"]
    # the EU output keeps its label (and names its currency) and records no currency or fallback key
    m = pypsa.Network()
    m.add("Bus", "b")
    m.add("Generator", "pv", bus="b", p_nom_extendable=True)
    eu = price_from_pack(m, "generators", "pv", "solar-utility", version=BASE)
    assert "annualised_cost_eur_per_mw_yr" in eu and eu["currency"] == "EUR"
    assert all("currency" not in f and "fallback" not in f
               for f in provenance._root(m)["Generator"]["pv"].values())


def test_an_eu_fallback_field_and_the_eu_discount_rate_are_recorded_as_such(shipped_caches):
    import pypsa

    from services.asset_schema import provenance
    from services.library.defaults_pack.pricing import price_from_pack

    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "pv", bus="b", p_nom_extendable=True)
    price_from_pack(n, "generators", "pv", "solar-utility", version=SHIPPED, region="us_federal")
    prov = provenance._root(n)["Generator"]["pv"]
    assert prov["lifetime"]["fallback"] == "eu_fallback"          # the study states no lifetime
    assert "fallback" not in prov["overnight_cost"]
    pack = D.load_defaults_pack(SHIPPED, region="us_federal")
    eu = D.load_defaults_pack(BASE)
    assert prov["overnight_cost"]["source"].startswith(f"generic_defaults@{pack.version}#")
    assert prov["discount_rate"]["source"] == f"generic_defaults@{BASE}#{eu.hash[:16]}"


def test_uk_overnight_leaves_out_the_infrastructure_that_includes_the_connection(shipped_caches):
    pack = D.load_defaults_pack(region="uk")
    # solar: pre-development 30 + construction 500 GBP/kW (Annex A D8, D20)
    assert pack.value("solar-utility.investment.overnight").value == pytest.approx(530_000.0)
    assert "left out" in pack.value("solar-utility.investment.overnight").note
