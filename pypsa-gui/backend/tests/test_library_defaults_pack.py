"""
The generic defaults pack (IC U1 follow-up, item a; plan "one engine, two faces" §3 rule 4).

Plan: docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md §3 rule 4, §4 C1-C4
Spec: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md decision 20 (amended
      2026-10-05 for this pack only)

What is pinned here:
  * every value row carries a source, a year and an `illustrative` flag;
  * technology costs use the asset schema's part vocabulary (S0): the battery has a
    `power` part (per_MW) and an `energy` part (per_MWh); units are converted
    explicitly from the catalogue's EUR/kW and EUR/kWh, the original kept;
  * derived values come from a CLOSED formula registry (`derive`);
  * both seed tariffs are valid IC `Tariff`s and RATE through the engine's oracle
    (`commercial.tariff_engine.rate`) to hand-computed bills;
  * `pack_tariff` returns an independent copy stamped with the pack id, version and hash
    (owner decision R3: copied inline, never resolved by a Library ref);
  * the pack's hash is pinned per version (`fixtures/defaults_pack/pack_hashes.json`),
    the way the tax packs are pinned.

Written red first: `services.library.defaults_pack` did not exist.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "defaults_pack"
VERSION = "2026-10-05"


@pytest.fixture(scope="module")
def pack():
    from services.library.defaults_pack import load_defaults_pack

    return load_defaults_pack(VERSION)


# ── loading, versions, errors ───────────────────────────────────────────────


def test_loads_the_vendored_version_and_none_means_the_latest(pack):
    from services.library import defaults_pack as D

    assert pack.pack_id == "generic_defaults" and pack.version == VERSION
    assert VERSION in D.available_versions()
    assert D.load_defaults_pack().version == max(D.available_versions())


def test_an_unknown_version_is_refused_not_fetched():
    from services.library import defaults_pack as D

    with pytest.raises(D.UnknownPackVersion) as exc:
        D.load_defaults_pack("1999-01-01")
    assert exc.value.code == "defaults_pack_version_unknown"
    assert VERSION in str(exc.value)
    assert isinstance(exc.value, D.DefaultsPackLookupError)


def test_unknown_ids_raise_typed_errors(pack):
    from services.library import defaults_pack as D

    cases = [(lambda: pack.cost_parts("fusion"), D.UnknownTechnology),
             (lambda: pack.cost_part("battery", "flux"), D.UnknownCostPart),
             (lambda: pack.value("battery.power.colour"), D.UnknownPackValue),
             (lambda: pack.pack_tariff("nope"), D.UnknownPackTariff),
             (lambda: pack.tariff_meta("nope"), D.UnknownPackTariff),
             (lambda: pack.load_profile("nope"), D.UnknownLoadProfile),
             (lambda: D.derive("cube", [2.0]), D.UnknownFormula)]
    for call, err in cases:
        with pytest.raises(err) as exc:
            call()
        assert isinstance(exc.value, D.DefaultsPackLookupError)
        assert exc.value.code.startswith("defaults_pack_")
        assert "known:" in str(exc.value)          # the message lists what exists


def test_the_pack_imports_nothing_from_routers_solver_or_the_network():
    import inspect

    from services.library.defaults_pack import loader

    src = inspect.getsource(loader)
    for banned in ("routers", "solver_service", "requests", "urllib", "httpx"):
        assert banned not in src, banned


# ── provenance on every row ─────────────────────────────────────────────────


def test_every_value_row_has_a_source_a_year_and_an_illustrative_flag(pack):
    rows = pack.values()
    assert len(rows) >= 15
    for v in rows:
        assert v.source.strip(), v.key
        assert isinstance(v.source_year, int) and 2000 <= v.source_year <= 2100, v.key
        assert isinstance(v.illustrative, bool), v.key
        assert v.unit.strip(), v.key
    money = [v for v in rows if v.unit.startswith("EUR")]
    assert money and all(v.currency == "EUR" and v.currency_year == 2020 for v in money)


def test_illustrative_flags(pack):
    # Catalogue rows (technology-data v0.14.0 / DEA) are sourced: not illustrative.
    for tech in ("battery", "solar-utility", "solar-rooftop"):
        for part in pack.cost_parts(tech):
            assert part.overnight.illustrative is False
    # Stated assumptions, seed tariffs and synthetic profiles ARE illustrative.
    assert pack.finance.sizing_limit.illustrative is True
    assert pack.finance.discount_rate.illustrative is False
    for tid in pack.tariff_ids():
        assert pack.tariff_meta(tid).illustrative is True
    for pid in pack.load_profile_ids():
        prof = pack.load_profile(pid)
        assert prof.synthetic is True and prof.illustrative is True


def test_every_assumption_row_carries_provenance_and_the_pack_stamp(pack):
    rows = pack.assumption_rows()
    sections = {r.section for r in rows}
    assert sections == {"technology", "finance", "tariff", "load_profile"}
    for r in rows:
        assert r.source.strip() and isinstance(r.source_year, int), r.key
        assert isinstance(r.illustrative, bool), r.key
        assert (r.pack_id, r.pack_version, r.pack_hash) == (pack.pack_id, pack.version,
                                                            pack.hash)
    keys = {r.key for r in rows}
    # every value row, every tariff item, both export prices and both profiles
    assert {v.key for v in pack.values()} <= keys
    assert "tariff.de_industrial_illustrative.items.network:energy" in keys
    assert "tariff.de_industrial_illustrative.export_price" in keys
    assert "tariff.tou_reference_illustrative.export_price" in keys
    assert "load_profile.commercial_office" in keys
    assert "finance.discount_rate" in keys
    assert len(keys) == len(rows)                     # one row per key


# ── technology costs: the S0 part vocabulary and explicit conversions ──────


def test_the_battery_has_a_power_part_and_an_energy_part(pack):
    parts = {p.part: p for p in pack.cost_parts("battery")}
    assert set(parts) == {"power", "energy"}
    power, energy = parts["power"], parts["energy"]
    assert (power.basis, power.overnight.unit) == ("per_MW", "EUR/MW")
    assert (energy.basis, energy.overnight.unit) == ("per_MWh", "EUR/MWh")
    assert power.source_technology == "battery inverter"
    assert energy.source_technology == "battery storage"
    assert power.lifetime.value == 10.0 and energy.lifetime.value == 25.0
    assert power.efficiency is not None and power.efficiency.value == 0.96
    # technology-data carries the battery's FOM on the inverter only: the energy part's
    # FOM is NOT established (None), never a silent 0 (ADR-0001).
    assert energy.fom_share is None and energy.efficiency is None
    assert pack.cost_part("battery", "power") == power


def test_unit_conversions_by_hand(pack):
    power = pack.cost_part("battery", "power")
    # 213.9279 EUR/kW x 1000 kW/MW = 213 927.9 EUR/MW
    assert power.overnight.original_value == 213.9279
    assert power.overnight.original_unit == "EUR/kW"
    assert math.isclose(power.overnight.value, 213927.9, rel_tol=1e-12)
    assert math.isclose(power.overnight.range.low, 149749.5, rel_tol=1e-12)
    assert math.isclose(power.overnight.range.high, 278106.3, rel_tol=1e-12)
    assert power.overnight.range.source == "assumed"
    # 0.3375 %/year / 100 = 0.003375 of the overnight cost per year
    assert power.fom_share.original_unit == "%/year" and power.fom_share.unit == "share/year"
    assert math.isclose(power.fom_share.value, 0.003375, rel_tol=1e-12)
    energy = pack.cost_part("battery", "energy")
    # 189.861 EUR/kWh x 1000 kWh/MWh = 189 861 EUR/MWh
    assert energy.overnight.original_unit == "EUR/kWh"
    assert math.isclose(energy.overnight.value, 189861.0, rel_tol=1e-12)
    pv = pack.cost_part("solar-utility", "investment")
    # 482.4785 EUR/kW_e x 1000 = 482 478.5 EUR/MW; FOM 2.4757 %/year -> 0.024757
    assert pv.basis == "per_MW" and pv.overnight.original_unit == "EUR/kW_e"
    assert math.isclose(pv.overnight.value, 482478.5, rel_tol=1e-12)
    assert math.isclose(pv.fom_share.value, 0.024757, rel_tol=1e-12)
    assert pv.lifetime.value == 40.0 and pv.lifetime.original_unit == "years"
    roof = pack.cost_part("solar-rooftop", "investment")
    assert math.isclose(roof.overnight.value, 883813.8, rel_tol=1e-12)
    for part in (power, energy, pv, roof):
        assert part.overnight.projection_year == 2030
        assert "technology-data/refs/tags/v0.14.0" in part.overnight.source_url


def test_derived_round_trip_efficiency_comes_from_the_closed_registry(pack):
    from services.library import defaults_pack as D

    rte = pack.value("battery.round_trip_efficiency")
    assert rte.derived is not None
    assert rte.derived.formula_id == "square"
    assert rte.derived.inputs == ["battery.power.efficiency"]
    assert math.isclose(rte.value, 0.96 * 0.96, rel_tol=1e-12)
    assert D.derive("square", [0.9]) == pytest.approx(0.81)
    assert set(D.DERIVED_FORMULAS) == {"square"}
    with pytest.raises(D.DefaultsPackError):
        D.derive("square", [0.9, 0.8])               # wrong arity


def test_placeholders_are_not_established_not_zero(pack):
    deg = pack.value("battery.energy.degradation_calendar")
    assert deg.value is None and deg.status == "not_available"
    assert "not_used" in (deg.note or "")


def test_finance_defaults_keep_every_source(pack):
    f = pack.finance
    assert (f.basis.terms, f.basis.tax, f.basis.subsidy) == ("real", "pre", "excl")
    assert f.currency == "EUR" and f.currency_year == 2020
    assert f.currency_year_source.strip() and f.basis_source.strip()
    assert f.perspective == "site_owner" and f.perspective_source.strip()
    dr = f.discount_rate
    assert dr.value == 0.07 and dr.range.low == 0.049 and dr.range.high == 0.091
    assert dr.source_year == 2026 and "pypsa-eur" in dr.source_url
    assert f.horizon_rule.horizon_years == "battery.energy.lifetime"
    [rule] = f.replacement_rules
    assert (rule.every_years, rule.cost) == ("battery.power.lifetime", "battery.power.overnight")
    assert f.sizing_limit.value == 2.0
    assert pack.value("finance.discount_rate") == dr


# ── tariffs: valid IC Tariffs, rated to hand-computed bills ─────────────────


def test_both_tariffs_are_valid_ic_tariffs_and_validate_as_library_payloads(pack):
    from models.commercial import Tariff
    from services.library.items import validate_payload

    assert pack.tariff_ids() == ["de_industrial_illustrative", "tou_reference_illustrative"]
    assert pack.default_tariff_id == "de_industrial_illustrative"
    for tid in pack.tariff_ids():
        t = pack.pack_tariff(tid)
        assert isinstance(t, Tariff)
        validate_payload("tariff", t.model_dump(mode="json"))
        assert t.valid_from.isoformat() == "2020-01-01" and t.valid_to is None
    assert pack.pack_tariff("de_industrial_illustrative").jurisdiction == "DE"
    assert pack.pack_tariff("tou_reference_illustrative").jurisdiction == "generic"


def test_tariff_item_mapping(pack):
    de = {i.id: i for i in pack.pack_tariff("de_industrial_illustrative").items}
    assert set(de) == {"energy", "network:energy", "demand", "fixed"}
    assert (de["energy"].kind, de["energy"].unit, de["energy"].periods[0].rate) == (
        "energy", "per_kwh", pytest.approx(0.110))
    assert (de["network:energy"].kind, de["network:energy"].periods[0].rate) == (
        "energy", pytest.approx(0.020))
    assert (de["demand"].kind, de["demand"].unit, de["demand"].periods[0].rate) == (
        "demand", "per_kw_month", pytest.approx(9.0))
    assert de["demand"].ratchet is None and de["demand"].measured_on == "import"
    assert (de["fixed"].kind, de["fixed"].unit, de["fixed"].periods[0].rate) == (
        "fixed", "per_month", 400.0)
    meta = pack.tariff_meta("de_industrial_illustrative")
    assert meta.export.price_per_mwh == 40.0 and meta.export.series is None
    assert meta.billing_period == "month" and meta.currency == "EUR"
    assert meta.currency_year == 2020 and meta.default is True
    codes = [h.code for h in meta.honesty]
    assert codes == ["tariff_illustrative", "tariff_demand_charge_monthly_peak_not_annual"]
    assert all(h.help.strip() for h in meta.honesty)
    # The GS seed row each item came from stays as provenance.
    [src] = meta.items["demand"]
    assert (src.original_price, src.original_unit) == (9000.0, "EUR/MW/month")
    tou = pack.tariff_meta("tou_reference_illustrative")
    assert tou.export.price_per_mwh == 30.0 and tou.default is False
    assert pack.export_price_eur_per_mwh("tou_reference_illustrative") == 30.0


def _january_hourly(mw: float = 1.0, peak_mw: float | None = None) -> pd.DataFrame:
    idx = pd.date_range("2030-01-01", periods=31 * 24, freq="h")   # naive wall clock
    imp = np.full(len(idx), mw)
    if peak_mw is not None:
        imp[100] = peak_mw
    return pd.DataFrame({"import_mw": imp, "export_mw": np.zeros(len(idx))}, index=idx)


def test_de_industrial_rates_to_a_hand_computed_bill(pack):
    from services.commercial.tariff_engine import rate

    tariff = pack.pack_tariff("de_industrial_illustrative")
    res = rate(_january_hourly(1.0, peak_mw=3.0), tariff, step_hours=1.0, timezone=None)
    # 743 h at 1 MW + 1 h at 3 MW = 746 MWh = 746 000 kWh
    kwh = 743 * 1000 + 3000
    assert res.per_item["energy"] == pytest.approx(0.110 * kwh)            # 82 060
    assert res.per_item["network:energy"] == pytest.approx(0.020 * kwh)    # 14 920
    assert res.per_item["demand"] == pytest.approx(9.0 * 3000)             # 27 000 (3 MW peak)
    assert res.per_item["fixed"] == pytest.approx(400.0)                   # one full month
    assert res.total == pytest.approx(82060 + 14920 + 27000 + 400)
    assert res.complete


def test_tou_reference_rates_to_a_hand_computed_bill_and_covers_every_hour(pack):
    from services.commercial.tariff_engine import rate

    tariff = pack.pack_tariff("tou_reference_illustrative")
    res = rate(_january_hourly(1.0), tariff, step_hours=1.0, timezone=None)
    # January 2030: Tue 1st .. Thu 31st -> 23 weekdays, 8 weekend days.
    # Peak (weekday 08-20): 23 x 12 h = 276 MWh at 160 EUR/MWh = 44 160
    # Off-peak weekday nights: 23 x 12 h = 276 MWh at 90 = 24 840
    # Weekends: 8 x 24 h = 192 MWh at 90 = 17 280
    assert res.per_item["energy"] == pytest.approx(44160 + 24840 + 17280)
    assert res.per_item["fixed"] == pytest.approx(50.0)
    assert res.total == pytest.approx(86330.0)
    assert res.complete and not any(res.flags.values())     # no unrated hour


def test_tou_bands_partition_every_hour_of_the_week(pack):
    from services.commercial.tariff_engine import rate

    tariff = pack.pack_tariff("tou_reference_illustrative")
    idx = pd.date_range("2030-01-07", periods=7 * 24, freq="h")         # Mon .. Sun
    d = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    res = rate(d, tariff, step_hours=1.0, timezone=None)
    lines = res.lines[res.lines["tariff_item"] == "energy"]
    assert len(lines) == 168 and lines["rate"].notna().all()
    assert sorted(set(np.round(lines["rate"], 6))) == [0.09, 0.16]
    assert int((np.round(lines["rate"], 6) == 0.16).sum()) == 5 * 12


# ── pack tariffs are stamped copies ─────────────────────────────────────────


def test_pack_tariff_is_an_independent_stamped_copy(pack):
    from services.library import defaults_pack as D

    a = pack.pack_tariff("de_industrial_illustrative")
    b = pack.pack_tariff("de_industrial_illustrative")
    assert a == b and a is not b
    assert a.pack_hash == f"generic_defaults@{VERSION}:sha256:{pack.hash}" == pack.stamp
    assert D.parse_pack_stamp(a.pack_hash) == ("generic_defaults", VERSION, pack.hash)
    a.items[0].periods[0].rate = 99.0
    a.items.pop()
    a.name = "edited"
    c = pack.pack_tariff("de_industrial_illustrative")
    assert c == b and c.items[0].periods[0].rate == pytest.approx(0.110)
    # A fresh load is independent of the first one too.
    again = D.load_defaults_pack(VERSION)
    assert again.pack_tariff("de_industrial_illustrative") == b
    assert again.hash == pack.hash


def test_mutating_a_loaded_pack_does_not_leak_into_the_next_load():
    from services.library import defaults_pack as D

    first = D.load_defaults_pack(VERSION)
    with pytest.raises(Exception):
        first.version = "x"                                 # frozen
    first.tariffs["de_industrial_illustrative"].tariff.items[0].periods[0].rate = 5.0
    second = D.load_defaults_pack(VERSION)
    assert second.pack_tariff("de_industrial_illustrative").items[0].periods[0].rate == \
        pytest.approx(0.110)


def test_a_stamped_copy_binds_inline_in_a_commercial_config(pack):
    from models.commercial import CommercialConfig

    t = pack.pack_tariff("de_industrial_illustrative")
    cfg = CommercialConfig.model_validate({"poc_link": "grid", "import_tariff":
                                           t.model_dump(mode="json")})
    assert cfg.import_tariff.pack_hash == pack.stamp
    assert cfg.import_tariff_ref is None


# ── load profiles ───────────────────────────────────────────────────────────


def test_load_profiles_are_pack_files_with_a_manifest_hash(pack):
    import hashlib

    from services.library.defaults_pack import loader

    assert pack.load_profile_ids() == ["commercial_office", "industrial_two_shift"]
    for pid in pack.load_profile_ids():
        prof = pack.load_profile(pid)
        raw = (loader.version_dir(VERSION) / prof.file).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == prof.sha256
        assert prof.unit == "shape, scaled to annual MWh"
        assert prof.source.startswith("synthetic") and prof.source_year == 2026
        assert len(prof.factors) == 12 * 2 * 24


def test_load_profile_series_is_scaled_to_annual_mwh(pack):
    idx = pd.date_range("2030-01-01", periods=8760, freq="h")
    s = pack.load_profile_series("commercial_office", idx, annual_mwh=1000.0)
    assert isinstance(s, pd.Series) and s.index.equals(idx)
    assert s.sum() == pytest.approx(1000.0)
    # Shape: a Monday 10:00 in July is above that Monday's 03:00.
    assert s.loc["2030-07-01 10:00"] > s.loc["2030-07-01 03:00"]
    shape = pack.load_profile_series("industrial_two_shift", idx)
    assert shape.loc["2030-01-01 00:00"] == pytest.approx(0.4)          # the raw factor
    with pytest.raises(ValueError):
        pack.load_profile_series("commercial_office", idx, annual_mwh=-1.0)


# ── the hash pin ────────────────────────────────────────────────────────────


def test_every_version_is_pinned_and_its_hash_matches():
    """Pinned per VERSION, like the tax packs (`fixtures/investment_case/pack_hashes.json`):
    a changed row changes its version's hash and this fails until the pin is re-reviewed;
    a new version cannot slip in unpinned."""
    from services.library import defaults_pack as D

    pinned = json.loads((FIXTURES / "pack_hashes.json").read_text())
    assert set(pinned) == {"generic_defaults"}
    assert set(pinned["generic_defaults"]) == set(D.available_versions())
    for version, h in pinned["generic_defaults"].items():
        p = D.load_defaults_pack(version)
        assert len(h) == 64
        assert p.hash == h, version


def test_the_hash_covers_content_and_a_profile_byte_change_is_refused(tmp_path):
    import shutil

    from services.library.defaults_pack import loader

    src = loader.version_dir(VERSION)
    dst = tmp_path / VERSION
    shutil.copytree(src, dst)
    base = loader.parse_pack_dir(dst)
    assert base.hash == loader.parse_pack_dir(src).hash   # location does not enter the hash
    # A changed value changes the hash.
    values = dst / "values.csv"
    values.write_text(values.read_text().replace("213.9279", "213.9280"))
    assert loader.parse_pack_dir(dst).hash != base.hash
    # A profile whose bytes no longer match the manifest is refused.
    prof = dst / "load_profiles" / "commercial_office.csv"
    prof.write_text(prof.read_text().replace("0.3675", "0.3676", 1))
    with pytest.raises(loader.DefaultsPackError, match="sha256"):
        loader.parse_pack_dir(dst)


def test_a_derived_row_that_disagrees_with_its_formula_is_refused(tmp_path):
    import shutil

    from services.library.defaults_pack import loader

    dst = tmp_path / VERSION
    shutil.copytree(loader.version_dir(VERSION), dst)
    values = dst / "values.csv"
    text = values.read_text()
    assert ",square," in text
    values.write_text(text.replace(",square,", ",cube,"))
    with pytest.raises(loader.DefaultsPackError, match="cube"):
        loader.parse_pack_dir(dst)
