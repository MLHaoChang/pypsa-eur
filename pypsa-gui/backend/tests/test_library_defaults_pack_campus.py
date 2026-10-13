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


COST_FIELDS = ("capex", "lifetime_a", "opex_frac")        # capex: capex_eur, or capex_eur_per_km


def _campus_parts(pack) -> dict[str, tuple[str, object]]:
    """The pack's campus parts by campus id: (kind, part)."""
    kinds = {k for k, _ in KINDS.values()}
    out = {}
    for tech in pack.technologies():
        kind = tech.split(".", 1)[0]
        if kind in kinds:
            (p,) = pack.cost_parts(tech)
            out[p.source_technology] = (kind, p)
    return out


def _cost_mismatches(pack, lib: dict) -> list[str]:
    """Gate r1 note 4: only the COST fields of the pack's campus entries are compared with a
    campus YAML (`capex_eur` / `capex_eur_per_km`, `lifetime_a`, `opex_frac`); an id the pack
    has that the YAML lacks is a mismatch. Ratings, notes, new keys (a pack-key pointer, §8) and
    entries the pack does not have are not."""
    listing_of = {kind: listing for listing, (kind, _b) in KINDS.items()}
    out = []
    for cid, (kind, p) in sorted(_campus_parts(pack).items()):
        entry = next((e for e in lib.get(listing_of[kind]) or [] if e.get("id") == cid), None)
        if entry is None:
            out.append(f"{cid}: missing from the YAML")
            continue
        capex = entry.get("capex_eur_per_km" if kind == "cable" else "capex_eur") or {}
        for name, want, got in (("capex", p.overnight.value, capex.get("value")),
                                ("lifetime_a", p.lifetime.value, (entry.get("lifetime_a") or {})
                                 .get("value")),
                                ("opex_frac", p.fom_share.value, (entry.get("opex_frac") or {})
                                 .get("value"))):
            if got is None or float(got) != want:
                out.append(f"{cid}.{name}: pack {want}, YAML {got}")
    return out


def _yaml():
    import yaml

    return yaml.safe_load((REPO / CAMPUS_YAML).read_text(encoding="utf-8"))


def test_the_pack_rows_are_the_transcription(new):
    """G-3: 81 entries, one `investment` part each, on the kind's basis, illustrative with the
    stated source and a label; the manifest's sha256 is the provenance of the transcription."""
    parts = _campus_parts(new)
    assert len(parts) == 81
    basis_of = dict(KINDS.values())
    for cid, (kind, p) in parts.items():
        assert (p.part, p.basis) == ("investment", basis_of[kind]), cid
        assert p.technology == f"{kind}.{cid.lower()}", cid
        for v in (p.overnight, p.lifetime, p.fom_share):
            assert v.source == SOURCE and v.illustrative is True and v.label.strip(), v.key
    assert len(new.seeded_from["campus_equipment"]["sha256"]) == 64


def test_the_campus_cost_fields_match_the_current_yaml(new):
    """G-3, review note 8, gate r1 note 4: the campus session edits the YAML (§8: it points at the
    pack key), so only a cost value that differs, or an id that is gone, fails — and says why."""
    lib = _yaml()
    assert (lib["currency"], lib["price_year"]) == ("EUR", 2026)
    bad = _cost_mismatches(new, lib)
    if bad:
        pytest.fail(f"{CAMPUS_YAML}: a price change needs a new pack version, never an edit of "
                    f"2026-10-07 ({len(bad)}: {'; '.join(bad[:5])})")


def test_the_parity_check_ignores_non_cost_edits_and_catches_a_price_change(new):
    import copy

    lib = _yaml()
    edited = copy.deepcopy(lib)
    for listing in KINDS:
        for e in edited[listing]:
            e["pack_key"] = "transformer.x"                      # §8's pointer, a new key
            e.setdefault("note", "an edited note")
    edited["transformers"][0]["s_mva"] = {"value": 99.0, "source": "datasheet"}   # a rating
    edited["cables"].append({"id": "CB_NEW", "capex_eur_per_km": {"value": 1.0}})  # not in the pack
    assert _cost_mismatches(new, edited) == []
    priced = copy.deepcopy(lib)
    tr = next(e for e in priced["transformers"] if e["id"] == "TR_132_33_40")
    tr["capex_eur"]["value"] = 1_900_000
    assert _cost_mismatches(new, priced) == ["TR_132_33_40.capex: pack 1800000.0, YAML 1900000"]
    gone = copy.deepcopy(lib)
    gone["switchgear"] = [e for e in gone["switchgear"] if e["id"] != "SG_132_31p5"]
    assert _cost_mismatches(new, gone) == ["SG_132_31p5: missing from the YAML"]


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
