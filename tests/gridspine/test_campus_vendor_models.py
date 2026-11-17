"""The vendor (black-box) model slot on a campus (GC-01, TRGC 01/02)."""
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.campus import VENDOR_MODEL_FIELDS
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_campus import campus_spec

REF = {"file": "bess_plant.dyr", "tool": "PSS/E", "model": "BESSGEN", "version": "3.0", "sha256": "AB" * 32}


def spec_with(block):
    spec = campus_spec()
    spec["campus"]["vendor_models"] = block
    return spec


def test_reference_is_stored_and_hash_lower_cased():
    c = build_campus(spec_with({"bess": dict(REF)}))
    assert c.vendor_models == {"bess": {**REF, "sha256": "ab" * 32}}


def test_hash_with_every_hex_digit_is_accepted():
    sha = "0123456789abcdef" * 4
    assert build_campus(spec_with({"pv": {**REF, "sha256": sha}})).vendor_models["pv"]["sha256"] == sha


def test_campus_without_the_block_has_none():
    assert build_campus(campus_spec()).vendor_models == {}


def test_reference_does_not_change_the_net():
    plain, with_ref = build_campus(campus_spec()), build_campus(spec_with({"bess": dict(REF)}))
    for table in ("bus", "trafo", "line", "load", "sgen", "ext_grid"):
        assert getattr(plain.net, table).equals(getattr(with_ref.net, table))
    assert plain.params.equals(with_ref.params) and plain.units.equals(with_ref.units)


def test_unknown_kind_is_refused():
    with pytest.raises(ContractError, match=r"vendor_models\.turbine: unknown unit kind 'turbine'"):
        build_campus(spec_with({"turbine": dict(REF)}))


@pytest.mark.parametrize("field", VENDOR_MODEL_FIELDS)
def test_empty_field_is_refused(field):
    with pytest.raises(ContractError, match=rf"vendor_models\.bess\.{field}: must be a non-empty string"):
        build_campus(spec_with({"bess": {**REF, field: "  "}}))


def test_non_string_field_is_refused():
    with pytest.raises(ContractError, match=r"vendor_models\.bess\.version: must be a non-empty string"):
        build_campus(spec_with({"bess": {**REF, "version": 3.0}}))


@pytest.mark.parametrize("sha, why", [("a" * 63, "63 characters"), ("g" * 64, "64 characters"),
                                      ("a" * 65, "65 characters")], ids=["63", "non-hex", "65"])
def test_bad_hash_is_refused(sha, why):
    with pytest.raises(ContractError, match=rf"vendor_models\.bess\.sha256: must be 64 hex digits, got {why}"):
        build_campus(spec_with({"bess": {**REF, "sha256": sha}}))


def test_unknown_field_is_refused():
    with pytest.raises(ContractError, match=r"vendor_models\.bess: unknown field\(s\) \['licence'\]"):
        build_campus(spec_with({"bess": {**REF, "licence": "x"}}))


def test_missing_field_is_refused():
    ref = {k: v for k, v in REF.items() if k != "tool"}
    with pytest.raises(ContractError, match=r"vendor_models\.bess: missing field\(s\) \['tool'\]"):
        build_campus(spec_with({"bess": ref}))


def test_block_that_is_not_a_mapping_is_refused():
    with pytest.raises(ContractError, match="vendor_models must be a mapping"):
        build_campus(spec_with(["bess"]))
    with pytest.raises(ContractError, match=r"vendor_models\.bess: must be a mapping"):
        build_campus(spec_with({"bess": "file.dyr"}))
