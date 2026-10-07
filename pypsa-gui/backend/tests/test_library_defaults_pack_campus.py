"""
IC G1 (plan `docs/superpowers/plans/2026-10-07-ic-g1-g2-campus-equipment.md` §4 WP-G1; G-1 … G-3): the
defaults pack version 2026-10-07 = 2026-10-05 unchanged plus the campus equipment cost rows, on the new bases
`lump` (EUR/unit) and `per_bay` (EUR/bay) and the existing `per_km` (EUR/km).

- G-1. A new version, never an edit: both versions load and are pinned, 2026-10-05's pin is unchanged, every
  2026-10-05 row is in 2026-10-07 unchanged; the latest version is 2026-10-07.
- G-2. The bases and their unit conversions; the basis / overnight-unit rule covers `lump`.
- G-3. One `investment` part per campus entry, `technology = "<kind>.<id lowercased>"`, three rows (overnight,
  lifetime, fom_share), illustrative, `source = "assumed (gridspine campus_assets.yaml placeholder)"`, money in
  2026 EUR. The parity test reads the campus YAML AS PINNED by the manifest's sha256.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

NEW, OLD = "2026-10-07", "2026-10-05"
FIXTURES = Path(__file__).parent / "fixtures" / "defaults_pack"
REPO = Path(__file__).resolve().parents[3]
CAMPUS_YAML = "gridspine/templates/data/campus_assets.yaml"
SOURCE = "assumed (gridspine campus_assets.yaml placeholder)"
KINDS = {"transformers": ("transformer", "lump"), "cables": ("cable", "per_km"),
         "capacitor_banks": ("capacitor_bank", "lump"), "shunt_reactors": ("shunt_reactor", "lump"),
         "statcoms": ("statcom", "lump"), "switchgear": ("switchgear", "per_bay")}


@pytest.fixture(scope="module")
def new():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(NEW)


@pytest.fixture(scope="module")
def old():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(OLD)


def _rows(version: str) -> list[dict]:
    from services.library.defaults_pack import loader

    with (loader.version_dir(version) / "values.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ── G-1: a new version, never an edit ───────────────────────────────────────


def test_both_versions_load_and_are_pinned_and_the_latest_is_the_new_one(new, old):
    from services.library.defaults_pack import loader

    pinned = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    assert pinned[OLD] == "962a0184861c1c594fdf4029ed5e7d55e241ab1aadfad5d52412df76ad2a4f78"
    assert old.hash == pinned[OLD] and new.hash == pinned[NEW] and new.hash != old.hash
    assert loader.available_versions()[-2:] == (OLD, NEW)
    assert loader.load_defaults_pack().version == NEW


def test_every_old_row_is_in_the_new_version_unchanged(new, old):
    old_rows, new_rows = _rows(OLD), _rows(NEW)
    assert new_rows[:len(old_rows)] == old_rows                    # first, in order, as text
    assert [v.model_dump() for v in new.cost_values[:len(old.cost_values)]] == \
        [v.model_dump() for v in old.cost_values]
    assert new.finance.model_dump(exclude={"currency_year_source"}) == \
        old.finance.model_dump(exclude={"currency_year_source"})
    assert {t: p.tariff.model_dump() for t, p in new.tariffs.items()} == \
        {t: p.tariff.model_dump() for t, p in old.tariffs.items()}
    assert new.load_profiles == old.load_profiles


def test_the_manifest_pins_the_campus_yaml_by_sha256(new):
    seed = new.seeded_from["campus_equipment"]
    assert seed["path"] == CAMPUS_YAML
    assert len(seed["sha256"]) == 64
    # The GS seed provenance is kept.
    assert new.seeded_from["branch"] == "claude/edge-tool-ux-research-n0n2l6"


def test_the_finance_defaults_state_that_rows_carry_their_own_money_year(new):
    assert "own" in new.finance.currency_year_source and "2026" in new.finance.currency_year_source


# ── G-2, G-3: the bases and the rows ────────────────────────────────────────


def test_a_transformer_a_cable_and_switchgear_by_hand(new):
    (tr,) = new.cost_parts("transformer.tr_132_33_40")
    assert (tr.part, tr.basis, tr.source_technology) == ("investment", "lump", "TR_132_33_40")
    assert (tr.overnight.value, tr.overnight.unit) == (1_800_000.0, "EUR/unit")
    assert (tr.overnight.original_value, tr.overnight.original_unit) == (1_800_000.0, "EUR")
    assert (tr.lifetime.value, tr.lifetime.unit) == (40.0, "years")
    assert (tr.fom_share.value, tr.fom_share.unit) == (0.015, "share/year")
    assert tr.efficiency is None
    (cb,) = new.cost_parts("cable.cb_33_al95")
    assert (cb.basis, cb.overnight.value, cb.overnight.unit) == ("per_km", 90_000.0, "EUR/km")
    (sg,) = new.cost_parts("switchgear.sg_132_31p5")
    assert (sg.basis, sg.overnight.value, sg.overnight.unit) == ("per_bay", 450_000.0, "EUR/bay")
    # The YAML's own note on a cost number is carried (SG_110_31p5's capex is "per bay").
    assert new.cost_part("switchgear.sg_110_31p5", "investment").overnight.note == "per bay"


def test_the_new_unit_conversions_are_one():
    from services.library.defaults_pack.loader import UNIT_CONVERSIONS

    for k in (("EUR", "EUR/unit"), ("EUR/km", "EUR/km"), ("EUR/bay", "EUR/bay"),
              ("share/year", "share/year")):
        assert UNIT_CONVERSIONS[k] == 1.0


def test_campus_money_rows_are_in_2026_and_catalogue_rows_keep_2020(new):
    campus = [v for v in new.cost_values if v.source == SOURCE]
    catalogue = [v for v in new.cost_values if v.source != SOURCE]
    assert len(campus) == 3 * 81
    for v in campus:
        assert v.illustrative is True and v.source_year == 2026 and v.price_basis == "real", v.key
        if v.unit.startswith("EUR"):
            assert (v.currency, v.currency_year) == ("EUR", 2026), v.key
    money = [v for v in catalogue if v.unit.startswith("EUR")]
    assert money and all((v.currency, v.currency_year) == ("EUR", 2020) for v in money)


def test_the_campus_rows_match_the_yaml_as_pinned_by_the_manifest(new):
    """G-3, review note 8: if the campus YAML changes, a price change needs a new pack version — this
    says so rather than failing on a number."""
    import yaml

    path = REPO / CAMPUS_YAML
    digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    pinned = new.seeded_from["campus_equipment"]["sha256"]
    if digest != pinned:
        pytest.fail(f"{CAMPUS_YAML} changed (sha256 {digest[:12]}…, the pack pins {pinned[:12]}…): "
                    "a price change needs a new pack version, never an edit of 2026-10-07")
    lib = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert (lib["currency"], lib["price_year"]) == ("EUR", 2026)
    seen = 0
    for listing, (kind, basis) in KINDS.items():
        for e in lib[listing]:
            seen += 1
            tech = f"{kind}.{e['id'].lower()}"
            (p,) = new.cost_parts(tech)
            capex = e["capex_eur_per_km"] if kind == "cable" else e["capex_eur"]
            assert (p.part, p.basis, p.source_technology) == ("investment", basis, e["id"]), tech
            assert p.overnight.value == float(capex["value"]), tech
            assert p.lifetime.value == float(e["lifetime_a"]["value"]), tech
            assert p.fom_share.value == float(e["opex_frac"]["value"]), tech
            assert p.overnight.note == capex.get("note"), tech
            for v in (p.overnight, p.lifetime, p.fom_share):
                assert v.source == SOURCE and v.illustrative is True, v.key
                assert v.label.strip(), v.key
    assert seen == 81
    campus_techs = {t for t in new.technologies() if t.split(".", 1)[0] in
                    {k for k, _ in KINDS.values()}}
    assert len(campus_techs) == 81


def test_the_domains_of_the_campus_rows(new):
    want = {"overnight": "[0, inf)", "lifetime": "[1, inf)", "fom_share": "[0, 1]"}
    for v in new.cost_values:
        if v.source == SOURCE:
            assert v.domain == want[v.parameter], v.key


def test_the_loader_refuses_a_lump_part_whose_overnight_unit_is_not_eur_per_unit(tmp_path):
    from services.library.defaults_pack import loader

    dst = tmp_path / NEW
    shutil.copytree(loader.version_dir(NEW), dst)
    values = dst / "values.csv"
    with values.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        header = list(rows[0])
    for r in rows:                                    # a cable re-labelled lump: its unit is EUR/km
        if r["technology"] == "cable.cb_33_al95":
            r["basis"] = "lump"
    with values.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(loader.DefaultsPackError, match="basis lump needs overnight in EUR/unit"):
        loader.parse_pack_dir(dst)


def test_assumption_rows_carry_the_campus_rows_with_the_new_stamp(new):
    rows = [r for r in new.assumption_rows() if r.source == SOURCE]
    assert len(rows) == 3 * 81
    assert all(r.pack_version == NEW and r.pack_hash == new.hash and r.illustrative for r in rows)
