"""
Campus-library-gaps 01 (`.scratch/campus-library-gaps/issues/01-h2-hub-220-110kv-transformer.md`, owner Q-CL-1):
the defaults pack version 2026-11-06 = 2026-10-30 byte for byte plus the five 220/110 kV campus transformers the
shipped library (`gridspine/templates/data/campus_assets.yaml`) gains, TR_220_110_125/160/200/250/315.

Their costs come from Ei's Normvärdeslista rows R-NR-T-8-1..5 (245/123-145 kV, 120-315 MVA, SEK 2022): 200, 250 and
315 MVA read straight, 125 and 160 MVA log-log interpolated between the bracketing rows (120 and 150 MVA are anchors
only; GK A, CL-01 Q3 (c)), converted to 2025 EUR as every other campus row (ECB 2022 rate, euro-area C27.1 producer prices); lifetime and fixed O&M are the DEA rows the other
transformers cite. The manifest pins the library's new sha256, so the pack's campus rows stay a full transcription
of it (GK A, CL-01 plan GO).

Every 2026-10-30 row is kept, unchanged and in order; the finance defaults, tariffs and load profiles are the same
bytes. The new version becomes the default of `load_defaults_pack()` for every caller that names no version.
"""
from __future__ import annotations

import json
import pathlib

import pytest
import yaml

NEW, PREV = "2026-11-06", "2026-10-30"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "defaults_pack"
REPO = pathlib.Path(__file__).resolve().parents[3]
CAMPUS_YAML = REPO / "gridspine" / "templates" / "data" / "campus_assets.yaml"
EI_URL = ("https://ei.se/download/18.61d7efeb18b048db98c4e48/1697184391450/"
          "Normv%C3%A4rdeslista-2024-2027.xlsx")
#: unit -> (the Ei rows it is priced from, 2025 EUR as shipped)
UNITS = {
    "TR_220_110_125": (("R-NR-T-8-1", "R-NR-T-8-2"), 3608000.0),
    "TR_220_110_160": (("R-NR-T-8-2", "R-NR-T-8-3"), 4212800.0),
    "TR_220_110_200": (("R-NR-T-8-3",), 4841800.0),
    "TR_220_110_250": (("R-NR-T-8-4",), 5371900.0),
    "TR_220_110_315": (("R-NR-T-8-5",), 5902000.0),
}
PARTS = ("overnight", "lifetime", "fom_share")
NEW_KEYS = [f"transformer.{u.lower()}.investment.{p}" for u in UNITS for p in PARTS]


@pytest.fixture(scope="module")
def new():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(NEW)


@pytest.fixture(scope="module")
def prev():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(PREV)


def _dir(version):
    from services.library.defaults_pack import loader

    return loader.version_dir(version)


def _lines(version):
    return (_dir(version) / "values.csv").read_bytes().split(b"\n")


def test_the_new_version_follows_is_the_default_and_is_pinned(new, prev):
    from services.library.defaults_pack import loader
    from services.library.defaults_pack.loader import load_defaults_pack

    pins = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    versions = loader.available_versions()
    assert versions[versions.index(PREV) + 1] == NEW == versions[-1]
    assert load_defaults_pack().version == NEW
    assert new.hash == pins[NEW] != prev.hash == pins[PREV]
    assert new.typed and NEW not in loader.LEGACY_VERSIONS


def test_every_previous_row_is_byte_equal_and_only_the_220_110_rows_are_added():
    old, cur = _lines(PREV), _lines(NEW)
    added = [line for line in cur if line.split(b",", 1)[0].decode() in NEW_KEYS]
    assert [line.split(b",", 1)[0].decode() for line in added] == NEW_KEYS
    assert [line for line in cur if line not in added] == old
    # they sit with the other campus transformers, after the last one of 2026-10-30
    last_tr = max(i for i, line in enumerate(cur) if line.startswith(b"transformer.tr_20_0p4_2p5."))
    assert cur[last_tr + 1:last_tr + 1 + len(added)] == added


def test_the_other_files_are_the_same_bytes():
    for name in ("finance.yaml", "tariffs.json"):
        assert (_dir(NEW) / name).read_bytes() == (_dir(PREV) / name).read_bytes(), name
    old_profiles = sorted(p.relative_to(_dir(PREV)) for p in (_dir(PREV) / "load_profiles").rglob("*") if p.is_file())
    new_profiles = sorted(p.relative_to(_dir(NEW)) for p in (_dir(NEW) / "load_profiles").rglob("*") if p.is_file())
    assert new_profiles == old_profiles
    for rel in old_profiles:
        assert (_dir(NEW) / rel).read_bytes() == (_dir(PREV) / rel).read_bytes(), rel


def test_the_manifest_changes_only_the_version_the_campus_pin_and_records_why():
    old = json.loads((_dir(PREV) / "manifest.json").read_text(encoding="utf-8"))
    cur = json.loads((_dir(NEW) / "manifest.json").read_text(encoding="utf-8"))
    why = cur.pop("campus_220_110_transformers")
    assert why["from"] == PREV and why["rows"] == NEW_KEYS and why["source"] == EI_URL
    seed, old_seed = cur["seeded_from"]["campus_equipment"], old["seeded_from"]["campus_equipment"]
    import hashlib
    assert seed.pop("sha256") == hashlib.sha256(CAMPUS_YAML.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert seed.pop("entries") == 86 == old_seed.pop("entries") + len(UNITS)
    old_seed.pop("sha256")
    assert cur.pop("version") == NEW
    old.pop("version")
    assert cur == old


def test_the_220_110_rows_are_the_library_transcribed_with_their_sources(new):
    lib = {e["id"]: e for e in yaml.safe_load(CAMPUS_YAML.read_text())["transformers"]}
    rows = {r.key: r for r in new.cost_values}
    for unit, (codes, eur) in UNITS.items():
        e = lib[unit]
        k = f"transformer.{unit.lower()}.investment"
        capex, life, fom = rows[f"{k}.overnight"], rows[f"{k}.lifetime"], rows[f"{k}.fom_share"]
        assert capex.value == eur == e["capex_eur"]["value"]
        assert life.value == e["lifetime_a"]["value"] == 40
        assert fom.value == e["opex_frac"]["value"] == 0.015
        assert all(c in capex.source and c in capex.note for c in codes)
        assert ("interpolated" in capex.source) == (len(codes) == 2)
        assert capex.source_url == EI_URL and not capex.illustrative
        assert capex.note == e["capex_eur"]["note"] and "linspann" in capex.note
        assert (capex.currency, capex.money_year) == ("EUR", 2025)


def test_the_new_rows_are_shaped_as_the_132_33_100_rows(new):
    """Same part, basis, units and DEA sources as the other campus transformers; only the id, value and
    Ei row differ."""
    rows = {r.key: r for r in new.cost_values}
    keep = ("part", "basis", "capacity_basis", "unit", "original_unit", "domain", "money_year", "currency")
    for unit in UNITS:
        for p in PARTS:
            ref, row = rows[f"transformer.tr_132_33_100.investment.{p}"], rows[f"transformer.{unit.lower()}.investment.{p}"]
            assert {f: getattr(row, f) for f in keep} == {f: getattr(ref, f) for f in keep}, (unit, p)
            if p != "overnight":
                assert (row.source, row.source_url, row.note) == (ref.source, ref.source_url, ref.note)
