"""campus-library-gaps 03 (owner CLG03-SOURCE-1; GK A plan C4): the defaults pack version 2026-11-13 is
2026-11-10 byte for byte except the manifest's version, its campus sha256 and the block that says why.

The five 220/110 kV campus transformers took sourced load and no-load losses in
`gridspine/templates/data/campus_assets.yaml`. The pack carries no loss, so every row, the finance
defaults, the tariffs and the load profiles are 2026-11-10's; only the library's pin moves.
`tests/gridspine/test_campus_assets.py` proves the YAML differs from the old pin only in those losses.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

NEW, PREV = "2026-11-13", "2026-11-10"
REPO = pathlib.Path(__file__).resolve().parents[3]
CAMPUS_YAML = REPO / "gridspine" / "templates" / "data" / "campus_assets.yaml"
OPTCL_URL = "https://www.optcl.co.in/writereaddata/Tender/110325040316TenderSpecification%20(2).pdf"


def _dir(version):
    from services.library.defaults_pack import loader

    return loader.version_dir(version)


def _files(version):
    root = _dir(version)
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_every_file_but_the_manifest_is_2026_11_10s_byte_for_byte():
    new, prev = _files(NEW), _files(PREV)
    assert set(new) == set(prev)
    assert {f for f in new if new[f] != prev[f]} == {"manifest.json"}


def test_the_manifest_changes_only_the_version_the_campus_pin_and_records_why():
    prev = json.loads((_dir(PREV) / "manifest.json").read_text(encoding="utf-8"))
    cur = json.loads((_dir(NEW) / "manifest.json").read_text(encoding="utf-8"))
    why = cur.pop("campus_220_110_losses")
    assert why["from"] == PREV and why["source"] == OPTCL_URL
    assert "campus-library-gaps 03" in why["why"] and "autotransformer" in why["why"]
    seed, prev_seed = (
        cur["seeded_from"]["campus_equipment"],
        prev["seeded_from"]["campus_equipment"],
    )
    assert (
        seed.pop("sha256")
        == hashlib.sha256(CAMPUS_YAML.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    )
    assert (
        prev_seed.pop("sha256")
        == "5058116ad6b169fad78677e5638b957ca86ece4e30d63465538c5dc50bb6c23f"
    )
    assert (cur.pop("version"), prev.pop("version")) == (NEW, PREV)
    assert cur == prev


def test_the_rows_are_2026_11_10s_and_the_new_version_is_pinned_and_the_default():
    from services.library.defaults_pack.loader import load_defaults_pack

    new, prev = load_defaults_pack(NEW), load_defaults_pack(PREV)
    assert (
        new.cost_values == prev.cost_values
        and new.finance == prev.finance
        and new.tariffs == prev.tariffs
    )
    pins = json.loads(
        (
            pathlib.Path(__file__).parent / "fixtures/defaults_pack/pack_hashes.json"
        ).read_text()
    )
    assert (
        new.hash == pins["generic_defaults"][NEW]
        and prev.hash == pins["generic_defaults"][PREV]
    )
    assert load_defaults_pack().version == NEW
