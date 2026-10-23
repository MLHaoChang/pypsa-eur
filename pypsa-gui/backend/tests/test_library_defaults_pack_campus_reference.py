"""
Campus-IC 03, part A (`.scratch/campus-ic-extra-owner-assets/issues/03-defaults-pack-with-cited-campus-costs.md`):
the defaults pack version 2026-10-23 = 2026-10-22 with the 243 campus cost rows taken from the cited
reference costs (`docs/superpowers/research/2026-10-12-campus-asset-reference-costs.yaml`, "the proposal").

- A new version, never an edit: 2026-10-22 loads and keeps its pin; every other row is unchanged.
- Each campus row carries the proposal's value, 2025 EUR, a non-empty source, a `source_url` for a
  `literature` value, `illustrative` exactly when the value is `assumed`, and the proposal's note.
- The manifest pins the sha256 of the library as part B ships it (the proposal with its header rewritten),
  so the parity test (`test_library_defaults_pack_campus.py`) follows the YAML once it ships.
"""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from tests.test_library_defaults_pack_campus import KINDS as CAMPUS_KINDS
from tests.test_library_defaults_pack_campus import _cost_mismatches

NEW, PREV = "2026-10-23", "2026-10-22"
FIXTURES = Path(__file__).parent / "fixtures" / "defaults_pack"
REPO = Path(__file__).resolve().parents[3]
PROPOSAL = REPO / "docs/superpowers/research/2026-10-12-campus-asset-reference-costs.yaml"
# sha256 of the shipped library: the proposal with its header rewritten from "proposal" to "shipped"
# (shipped as `gridspine/templates/data/campus_assets.yaml` by part B; tests/gridspine pins the same sha).
SHIPPED_SHA256 = "97ce51fbc297140e4a1946b1d56f9e715038652413a117d5f0e51fb623582c97"
ASSUMED = "assumed (gridspine campus_assets.yaml)"
URLS = {
    "EI-NVL": "https://ei.se/download/18.61d7efeb18b048db98c4e48/1697184391450/"
              "Normv%C3%A4rdeslista-2024-2027.xlsx",
    "DEA-TRANS": "https://ens.dk/media/7680/download",
    "DEA-DIST": "https://ens.dk/media/6440/download",
}
KINDS = {listing: kind for listing, (kind, _basis) in CAMPUS_KINDS.items()}


@pytest.fixture(scope="module")
def new():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(NEW)


@pytest.fixture(scope="module")
def prev():
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(PREV)


def _rows(version: str) -> list[dict]:
    from services.library.defaults_pack import loader

    with (loader.version_dir(version) / "values.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _proposal() -> dict:
    import yaml

    return yaml.safe_load(PROPOSAL.read_text(encoding="utf-8"))


def _proposal_costs() -> dict[str, dict[str, dict]]:
    """The proposal's {value, source, note} by technology, then parameter."""
    out: dict[str, dict[str, dict]] = {}
    for listing, kind in KINDS.items():
        for e in _proposal()[listing]:
            capex = e.get("capex_eur_per_km") if kind == "cable" else e.get("capex_eur")
            out[f"{kind}.{e['id'].lower()}"] = {"overnight": capex, "lifetime": e["lifetime_a"],
                                                "fom_share": e["opex_frac"]}
    return out


def _is_campus(row: dict) -> bool:
    return row["technology"].split(".", 1)[0] in KINDS.values()


# ── a new version, never an edit ────────────────────────────────────────────


def test_both_versions_load_and_are_pinned_and_the_new_one_is_the_latest(new, prev):
    from services.library.defaults_pack import loader

    pinned = json.loads((FIXTURES / "pack_hashes.json").read_text())["generic_defaults"]
    assert pinned[PREV] == "4f8b7ae8b86c5001f56b774b8e8c3962be027f107f18566bcbf0b8b0f211100a"
    assert prev.hash == pinned[PREV] and new.hash == pinned[NEW] and new.hash != prev.hash
    versions = loader.available_versions()
    assert versions[-1] == NEW and versions[-2] == PREV
    assert new.typed and NEW not in loader.LEGACY_VERSIONS


def test_every_other_row_is_unchanged_and_the_campus_rows_keep_their_place(new, prev):
    old_rows, new_rows = _rows(PREV), _rows(NEW)
    assert [r["key"] for r in new_rows] == [r["key"] for r in old_rows]
    assert [r for r in new_rows if not _is_campus(r)] == [r for r in old_rows if not _is_campus(r)]
    assert sum(_is_campus(r) for r in new_rows) == 3 * 81
    for o, n in zip(old_rows, new_rows):                 # the row's identity and typing stay
        for col in ("key", "technology", "part", "basis", "capacity_basis",
                    "includes_grid_connection", "source_technology", "parameter", "label",
                    "original_unit", "unit", "currency", "price_basis", "domain"):
            assert n[col] == o[col], (o["key"], col)
    assert new.finance.model_dump() == prev.finance.model_dump()
    assert {t: p.tariff.model_dump() for t, p in new.tariffs.items()} == \
        {t: p.tariff.model_dump() for t, p in prev.tariffs.items()}
    assert new.load_profiles == prev.load_profiles


# ── the campus rows ─────────────────────────────────────────────────────────


def test_each_campus_row_is_the_proposal_value_with_its_provenance(new):
    want = _proposal_costs()
    campus = [v for v in new.cost_values if v.technology in want]
    assert len(campus) == 3 * 81 and len(want) == 81
    for v in campus:
        got = want[v.technology][v.parameter]
        assert v.value == float(got["value"]), v.key
        assert v.illustrative is (got["source"] == "assumed"), v.key
        assert v.note == got.get("note"), v.key
        assert v.source.strip(), v.key
        if got["source"] == "assumed":
            assert v.source == ASSUMED and v.source_url is None, v.key
        else:
            assert got["source"] == "literature", v.key
            key = got["note"].split("]", 1)[0].lstrip("[")
            assert v.source_url == URLS[key], v.key
        if v.unit.startswith("EUR"):
            assert (v.currency, v.money_year) == ("EUR", 2025), v.key


def test_a_transformer_a_cable_and_switchgear_by_hand(new):
    (tr,) = new.cost_parts("transformer.tr_132_33_40")
    assert (tr.overnight.value, tr.overnight.unit) == (1_453_600.0, "EUR/unit")
    assert tr.overnight.source == "Ei Normvärdeslista elnät 2024-2027 (R-NR-T-5-5), converted"
    assert tr.overnight.illustrative is False and tr.overnight.money_year == 2025
    assert tr.fom_share.source.startswith("Danish Energy Agency") and tr.fom_share.value == 0.015
    (cb,) = new.cost_parts("cable.cb_33_al95")                    # assumed: no three-core 95 mm² row
    assert cb.overnight.source == ASSUMED and cb.overnight.illustrative is True
    assert cb.overnight.unit == "EUR/km"


def test_the_manifest_pins_the_shipped_library_and_its_2025_money(new):
    seed = new.seeded_from["campus_equipment"]
    assert seed["path"] == "gridspine/templates/data/campus_assets.yaml"
    assert seed["sha256"] == SHIPPED_SHA256
    assert (seed["entries"], seed["currency"], seed["price_year"]) == (81, "EUR", 2025)
    assert _proposal()["price_year"] == 2025


def test_the_new_version_matches_the_proposal_and_catches_a_one_cent_change(new):
    lib = _proposal()
    assert _cost_mismatches(new, lib) == []
    cent = copy.deepcopy(lib)
    tr = next(e for e in cent["transformers"] if e["id"] == "TR_132_33_40")
    tr["capex_eur"]["value"] += 0.01
    assert _cost_mismatches(new, cent) == ["TR_132_33_40.capex: pack 1453600.0, YAML 1453600.01"]


def test_assumption_rows_carry_the_campus_rows_with_the_new_stamp(new):
    want = _proposal_costs()
    rows = [r for r in new.assumption_rows() if r.key.rsplit(".", 2)[0] in want]
    assert len(rows) == 3 * 81
    assert all(r.pack_version == NEW and r.pack_hash == new.hash for r in rows)
