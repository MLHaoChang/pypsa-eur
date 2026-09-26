"""
Edge Investment Case — jurisdiction pack loader (Phase 0, WP0.2).

Spec: docs/superpowers/specs/2026-09-26-edge-investment-case-design.md §11, decision 2
Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP0.2

Loader, hashing and `not_established` semantics only. The two v1 packs are
STUBS (valid_from + source, empty rule tables): rules land in P4.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from services.finance.packs import base as P

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "investment_case"


def test_v1_pack_registry_is_exactly_eu_de_and_us_federal():
    assert set(P.available_jurisdictions()) == {"eu_de", "us_federal"}


def test_unknown_jurisdiction_raises_pack_not_found():
    with pytest.raises(P.PackNotFound):
        P.load_pack("eu_nl", as_of=date(2026, 1, 1))
    with pytest.raises(P.PackNotFound):
        P.load_pack("ca_federal", as_of=date(2026, 1, 1))


def test_loaded_pack_carries_provenance_fields():
    pk = P.load_pack("eu_de", as_of=date(2026, 6, 1))
    assert pk.jurisdiction == "eu_de"
    assert isinstance(pk.valid_from, date)
    assert pk.source and isinstance(pk.source, str)
    assert isinstance(pk.pack_hash, str) and len(pk.pack_hash) == 16


def test_as_of_before_valid_from_raises():
    pk = P.load_pack("us_federal", as_of=date(2026, 6, 1))
    with pytest.raises(P.PackNotFound):
        P.load_pack("us_federal", as_of=pk.valid_from.replace(year=pk.valid_from.year - 1))


def test_missing_rule_is_not_established_never_a_default_number():
    pk = P.load_pack("eu_de", as_of=date(2026, 6, 1))
    r = pk.rule("corporate_rate")
    assert isinstance(r, P.RuleLookup)
    assert r.status == "not_established"
    assert r.value is None
    assert "corporate_rate" in r.reason and "eu_de" in r.reason
    # An unknown rule name behaves the same way, and never raises.
    r2 = pk.rule("this_rule_does_not_exist")
    assert r2.status == "not_established" and r2.value is None


def test_present_rule_is_ok_with_value_and_source():
    pk = P.load_pack("eu_de", as_of=date(2026, 6, 1))
    pk2 = pk.with_rule("corporate_rate", 0.2983, source="test override")
    r = pk2.rule("corporate_rate")
    assert r.status == "ok" and r.value == 0.2983 and r.source == "test override"
    # Immutability: the original pack is untouched.
    assert pk.rule("corporate_rate").status == "not_established"


def test_hash_is_sha256_of_canonical_json_truncated_to_16():
    pk = P.load_pack("eu_de", as_of=date(2026, 6, 1))
    import hashlib
    payload = json.dumps(pk.canonical_payload(), sort_keys=True,
                         separators=(",", ":"), default=str)
    assert pk.pack_hash == hashlib.sha256(payload.encode()).hexdigest()[:16]


def test_hash_changes_when_any_field_changes():
    pk = P.load_pack("eu_de", as_of=date(2026, 6, 1))
    changed = pk.with_rule("corporate_rate", 0.3, source="x")
    assert changed.pack_hash != pk.pack_hash
    changed2 = pk.with_rule("corporate_rate", 0.3, source="y")
    assert changed2.pack_hash != changed.pack_hash  # source is part of the hash


def test_hash_changes_when_pack_level_fields_change():
    import dataclasses
    pk = P.load_pack("eu_de", as_of=date(2026, 6, 1))
    for changed in (
        dataclasses.replace(pk, source="other"),
        dataclasses.replace(pk, valid_from=pk.valid_from.replace(day=2)),
        dataclasses.replace(pk, notes="edited"),
        dataclasses.replace(pk, valid_to=date(2030, 12, 31)),
        dataclasses.replace(pk, country="AT"),
    ):
        assert changed.pack_hash != pk.pack_hash


def test_rule_values_must_be_json_native_or_date():
    from decimal import Decimal
    pk = P.load_pack("eu_de", as_of=date(2026, 6, 1))
    with pytest.raises(TypeError):
        pk.with_rule("corporate_rate", Decimal("0.21"), source="x")
    with pytest.raises(TypeError):
        pk.with_rule("corporate_rate", object(), source="x")
    with pytest.raises(TypeError):
        pk.with_rule("corporate_rate", 0.21, source="")
    dated = pk.with_rule("itc_begin_construction_by", date(2026, 7, 4), source="OBBBA")
    assert dated.canonical_payload()["rules"]["itc_begin_construction_by"]["value"] == "2026-07-04"
    # A date and its ISO string hash identically ON PURPOSE (same canonical
    # bytes), and a float and its string do NOT collide because the string is
    # quoted in JSON.
    assert pk.with_rule("r", 0.21, source="x").pack_hash != \
        pk.with_rule("r", "0.21", source="x").pack_hash


def test_valid_to_and_versioned_registry():
    import dataclasses
    old = dataclasses.replace(
        P.load_pack("us_federal", as_of=date(2026, 6, 1)),
        valid_from=date(2020, 1, 1), valid_to=date(2025, 12, 31),
        notes="synthetic prior version")
    P.register("us_federal", lambda: old)
    try:
        assert P.load_pack("us_federal", as_of=date(2023, 1, 1)).valid_to == date(2025, 12, 31)
        assert P.load_pack("us_federal", as_of=date(2026, 6, 1)).valid_to is None
        with pytest.raises(P.PackNotFound):
            P.load_pack("us_federal", as_of=date(2019, 1, 1))
    finally:
        P._REGISTRY["us_federal"] = [f for f in P._REGISTRY["us_federal"] if f() is not old]


def test_packs_carry_the_country_join_key_tariffs_use():
    assert P.load_pack("eu_de", as_of=date(2026, 6, 1)).country == "DE"
    assert P.load_pack("us_federal", as_of=date(2026, 6, 1)).country == "US"


def test_hash_is_stable_across_processes_fixture_pin():
    pinned = json.loads((FIXTURES / "pack_hashes.json").read_text())
    for jur, h in pinned.items():
        assert P.load_pack(jur, as_of=date(2026, 6, 1)).pack_hash == h, jur


def test_packs_import_nothing_from_routers_or_solver_service():
    import inspect
    for name in ("base", *P.BUILTIN_PACK_MODULES):
        mod = __import__(f"services.finance.packs.{name}", fromlist=["x"])
        src = inspect.getsource(mod)
        assert "routers" not in src
        assert "solver_service" not in src
