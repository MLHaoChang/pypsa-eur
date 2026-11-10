"""gridspine-library-eu 05: an API read of a tariff shows the currency the gate reads.

A copied `generic` pack tariff states no currency; the currency gate reads EUR from its
`pack_hash` stamp (`binding.resolved_tariff_currency`, issue 03). The resolved payload
shows that currency and says it came from the stamp. Nothing is written onto the
tariff: the stored payload and every hash stay as they are.
"""

from __future__ import annotations

import copy

from models import commercial as M
from services.commercial import hashing as H

GEN = "tou_reference_illustrative"


def _generic_copy_payload() -> dict:
    from services.library.defaults_pack.loader import load_defaults_pack

    tariff = load_defaults_pack().pack_tariff(GEN)
    assert tariff.currency is None and tariff.pack_hash  # a stamped copy stating none
    return tariff.model_dump(mode="json")


def _hashes(payload: dict) -> dict:
    t = M.Tariff.model_validate(payload)
    cfg = M.CommercialConfig(poc_link="import", import_tariff=t)
    return {
        "lib": H.library_item_digest(t),
        "v1": H.digest(cfg, version=1),
        "v2": H.digest(cfg, version=2),
        "dump": t.model_dump(mode="json"),
    }


def test_a_copied_generic_tariff_reads_eur_from_its_stamp_and_nothing_is_written():
    stored = _generic_copy_payload()
    before, hashes = copy.deepcopy(stored), _hashes(stored)

    out = M.tariff_payload_resolved(stored)

    assert out["currency"] == "EUR"
    assert out["currency_source"] == "stamp"
    # Nothing is written onto the tariff: the stored payload and its hashes are unchanged.
    assert stored == before and "currency" not in stored
    assert _hashes(stored) == hashes
    # A client re-sending the read's extra key writes nothing new.
    assert "currency_source" not in M.Tariff.model_validate(out).model_dump(mode="json")


def test_a_tariff_that_states_a_currency_shows_that_one():
    stored = {**_generic_copy_payload(), "currency": "USD"}
    out = M.tariff_payload_resolved(stored)
    assert out["currency"] == "USD"
    assert "currency_source" not in out


def test_an_implied_currency_and_an_unstamped_generic_tariff_are_as_before():
    stored = _generic_copy_payload()
    nl = {**stored, "jurisdiction": "NL", "pack_hash": None}
    assert M.tariff_payload_resolved(nl) == {**nl, "currency": "EUR"}
    bare = {**stored, "pack_hash": None}
    assert M.tariff_payload_resolved(bare) == bare
