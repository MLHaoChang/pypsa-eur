"""The model set and data freeze contract (GC-01, ``schema/model_set.py``)."""
import datetime
import hashlib

import numpy as np
import pandas as pd
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.schema.model_set import VARIANTS, canonical, data_hash, manifest, new_model_set
from tests.gridspine.test_campus import campus_spec

PARAMS = {"study": {"dispatch_mw": [10.0, 20.0], "pf": 0.95}, "case": "max"}
T0 = datetime.datetime(2026, 11, 17, 8, 0, tzinfo=datetime.timezone.utc)
SHA = "ab" * 32


def with_vendor(spec, sha=SHA):
    spec["campus"]["vendor_models"] = {"pv": {"file": "pv_plant.dyr", "tool": "PSS/E", "model": "PVGEN",
                                              "version": "1.2", "sha256": sha}}
    return spec


def test_hash_of_a_fixed_input_is_the_literal():
    """Computed once by hand: hashlib on the literal canonical string. A
    change to ``canonical()`` changes it."""
    expected_text = '{"campus":{"a":1},"params":{"b":[2.5,true]}}'
    assert canonical({"a": 1.0}, {"b": (2.5, True)}) == expected_text
    assert hashlib.sha256(expected_text.encode()).hexdigest() == \
        "7284c972380a916258451963b7befbc80ecd4f14087e9053f01ef77105d63cde"
    assert data_hash({"a": 1.0}, {"b": (2.5, True)}) == \
        "7284c972380a916258451963b7befbc80ecd4f14087e9053f01ef77105d63cde"


def test_same_inputs_same_hash():
    assert data_hash(campus_spec(), PARAMS) == data_hash(campus_spec(), dict(PARAMS))


def test_dict_key_order_does_not_matter():
    reordered = {"case": "max", "study": {"pf": 0.95, "dispatch_mw": [10.0, 20.0]}}
    spec = campus_spec()
    spec_rev = {"campus": dict(reversed(list(spec["campus"].items())))}
    assert data_hash(spec_rev, reordered) == data_hash(spec, PARAMS)


def test_list_order_matters():
    swapped = {"study": {"dispatch_mw": [20.0, 10.0], "pf": 0.95}, "case": "max"}
    assert data_hash(campus_spec(), swapped) != data_hash(campus_spec(), PARAMS)


def test_int_and_integral_float_hash_the_same_but_not_bool():
    assert data_hash({"a": 1}, {}) == data_hash({"a": 1.0}, {})
    assert data_hash({"a": True}, {}) != data_hash({"a": 1}, {})


def test_numpy_scalars_and_tuples_are_plain_values():
    assert data_hash({"a": np.float64(2.5), "n": np.int64(3), "b": np.bool_(True)}, {"t": (1, 2)}) == \
        data_hash({"a": 2.5, "n": 3, "b": True}, {"t": [1, 2]})


def test_changed_nested_param_changes_the_hash():
    changed = {"study": {"dispatch_mw": [10.0, 20.0], "pf": 0.9}, "case": "max"}
    assert data_hash(campus_spec(), changed) != data_hash(campus_spec(), PARAMS)


def test_changed_campus_value_changes_the_hash():
    spec = campus_spec()
    spec["campus"]["units"]["PV1"]["p_mw"]["value"] = 15.5
    assert data_hash(spec, PARAMS) != data_hash(campus_spec(), PARAMS)


def test_changed_vendor_model_changes_the_hash():
    assert data_hash(with_vendor(campus_spec(), "cd" * 32), PARAMS) != data_hash(with_vendor(campus_spec()), PARAMS)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"])
def test_non_finite_is_refused_naming_the_path(bad):
    with pytest.raises(ContractError, match=r"params\.study\.pf is .*finite"):
        data_hash(campus_spec(), {"study": {"pf": bad}})


@pytest.mark.parametrize("bad, kind", [(pd.Series([1.0]), "Series"), (pd.DataFrame({"a": [1]}), "DataFrame"),
                                       ({1, 2}, "set"), (np.array([1.0]), "ndarray")])
def test_other_types_are_refused_naming_the_path(bad, kind):
    with pytest.raises(ContractError, match=rf"params\.x is a {kind}; only plain JSON data"):
        data_hash({}, {"x": bad})


def test_non_string_key_is_refused():
    with pytest.raises(ContractError, match=r"params has key 1 of type int"):
        data_hash({}, {1: "a"})


def test_created_utc_is_outside_the_hash():
    a = new_model_set(campus_spec(), PARAMS, now=T0)
    b = new_model_set(campus_spec(), PARAMS, now=T0 + datetime.timedelta(days=3))
    assert (a.model_set_id, a.data_hash) == (b.model_set_id, b.data_hash)
    assert a.created_utc == "2026-11-17T08:00:00+00:00" != b.created_utc


def test_naive_freeze_time_is_refused():
    with pytest.raises(ContractError, match="timezone-aware"):
        new_model_set(campus_spec(), PARAMS, now=datetime.datetime(2026, 11, 17))


def test_model_set_id_is_derived_from_the_hash():
    ms = new_model_set(campus_spec(), PARAMS, now=T0)
    assert ms.model_set_id == "ms-" + ms.data_hash[:12]
    assert ms.data_hash == data_hash(campus_spec(), PARAMS)


def test_model_set_holds_copies_of_its_inputs():
    spec, params = campus_spec(), {"a": [1]}
    ms = new_model_set(spec, params, now=T0)
    spec["campus"]["name"] = "OTHER"
    params["a"].append(2)
    assert ms.spec("detailed")["campus"].get("name") != "OTHER"
    assert ms.params == {"a": [1]}


def test_manifest_names_model_set_hash_and_variant():
    ms = new_model_set(campus_spec(), PARAMS, aggregated=campus_spec(), unit_map={"EQ_PV": ["PV1"]}, now=T0)
    for variant in VARIANTS:
        assert manifest(ms, variant) == {"model_set_id": ms.model_set_id, "data_hash": ms.data_hash,
                                         "variant": variant}
    assert ms.unit_map == {"EQ_PV": ["PV1"]}


def test_manifest_refuses_unknown_variant():
    ms = new_model_set(campus_spec(), PARAMS, now=T0)
    with pytest.raises(ContractError, match="unknown variant 'reduced'"):
        manifest(ms, "reduced")


def test_manifest_refuses_a_variant_not_frozen():
    ms = new_model_set(campus_spec(), PARAMS, now=T0)
    assert ms.specs["aggregated"] is None
    with pytest.raises(ContractError, match="aggregated variant was not frozen"):
        manifest(ms, "aggregated")
    with pytest.raises(ContractError, match="aggregated variant was not frozen"):
        ms.spec("aggregated")
