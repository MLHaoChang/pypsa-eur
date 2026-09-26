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


def test_hash_is_stable_across_processes_fixture_pin():
    pinned = json.loads((FIXTURES / "pack_hashes.json").read_text())
    for jur, h in pinned.items():
        assert P.load_pack(jur, as_of=date(2026, 6, 1)).pack_hash == h, jur


def test_packs_import_nothing_from_routers_or_solver_service():
    import inspect
    for name in ("base", "eu_de", "us_federal"):
        mod = __import__(f"services.finance.packs.{name}", fromlist=["x"])
        src = inspect.getsource(mod)
        assert "routers" not in src
        assert "solver_service" not in src
