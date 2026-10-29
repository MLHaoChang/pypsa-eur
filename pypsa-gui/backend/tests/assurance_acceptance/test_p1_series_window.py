"""S4 SERIES-WINDOW: a series record is a certified-row bitmask bound to the
index and the certified values, not one stored fact per step.

A record stays small for a full hourly year of full-precision values, and a
lookup rebuilds its facts from the live series. It is unavailable unless the
row count, the mask, the index digest and the values digest all hold.
"""

import base64
import json
import math

import numpy as np
import pandas as pd
import pytest

from routers.network_time_axis import index_labels
from services import assurance_records as records
from tests.assurance_acceptance.support import approval
from tests.assurance_acceptance.test_p1_integrity import record_path, rewrite
from tests.assurance_acceptance.test_p1_timeseries import (
    ARGS,
    STEPS,
    evidence_ids,
    network,
    read_series,
    series_draft,
    set_series,
)

# The longest name the guard accepts, with characters that need escaping.
LONG_NAME = ("Load~/é-" * 16)[:128]


def full_year(steps, name):
    values = np.random.default_rng(20261029).standard_normal(steps) * 1e3
    n = network()
    n.set_snapshots(pd.date_range("2028-01-01", periods=steps, freq="h"))
    n.add("Load", name, bus="B1", p_set=1)
    n.loads_t.p_set[name] = values
    return values


def stored(evidence_id):
    return json.loads(record_path(evidence_id).read_text(encoding="utf-8"))["record"]


@pytest.mark.parametrize("steps", [8760, records.MAX_SERIES_POINTS])
def test_full_precision_hourly_year_is_captured_in_a_small_record(
    baseline, harness, steps
):
    assert records.MAX_SERIES_POINTS == 8784
    values = full_year(steps, LONG_NAME)
    label = index_labels(network().snapshots)[0][8000]
    (evidence_id,) = evidence_ids(read_series(harness, args={**ARGS, "name": LONG_NAME}))
    assert "facts" not in stored(evidence_id)
    assert stored(evidence_id)["series"]["rows"] == steps
    # Far under save()'s 128 KiB: the per-step facts would be several MiB.
    assert len(record_path(evidence_id).read_bytes()) < 16 * 1024
    record = harness.client.get(f"/api/chat/evidence/{evidence_id}").json()
    assert record["scope"]["certified"] == steps
    assert "series" not in record
    fact = record["facts"][8000]
    assert fact["path"] == records.pointer(LONG_NAME, label)
    assert fact["value"] == float(values[8000])


def test_a_step_deep_in_a_full_precision_year_certifies(baseline, harness):
    # A draft path is at most 128 characters, so the cited name is short.
    name = "a/b~c"
    values = full_year(8760, name)
    label = index_labels(network().snapshots)[0][8000]
    draft = series_draft(
        value=float(values[8000]), entity=name, path=records.pointer(name, label)
    )
    frames = read_series(harness, draft, {**ARGS, "name": name})
    assert approval(frames)["verdict"] == "passed"


def test_long_named_series_of_744_steps_is_now_captured(baseline, harness):
    # Refused before S4: its per-step facts overflowed the record.
    name = "é~/" * 42
    network().add("Load", name, bus="B1", p_set=1)
    set_series([1.0] * 744)
    network().loads_t.p_set[name] = 1.0
    (evidence_id,) = evidence_ids(read_series(harness, args={**ARGS, "name": name}))
    record = harness.client.get(f"/api/chat/evidence/{evidence_id}").json()
    assert record["scope"]["certified"] == 744


# --- the record's bindings ---------------------------------------------------


@pytest.fixture
def captured(baseline, harness):
    set_series([4.0, 5.5, math.nan, 6.0])
    frames = read_series(harness)
    assert approval(frames)["verdict"] == "passed"
    (evidence_id,) = evidence_ids(frames)
    path = f"/api/chat/evidence/{evidence_id}"
    response = harness.client.get(path)
    assert response.status_code == 200
    # Rows 0, 1 and 3 certify; the missing step does not.
    assert [f["value"] for f in response.json()["facts"]] == [4.0, 5.5, 6.0]
    return evidence_id, path


def assert_unavailable(harness, path):
    response = harness.client.get(path)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error_kind"] == "evidence_unavailable"


def resealed(harness, captured, change):
    evidence_id, path = captured
    rewrite(evidence_id, lambda record: change(record["series"]))
    assert_unavailable(harness, path)


def mask(*octets):
    return base64.b64encode(bytes(octets)).decode("ascii")


def test_a_flipped_mask_bit_is_unavailable(harness, captured):
    # Row 2 (the missing step) marked certified.
    resealed(harness, captured, lambda s: s.update(certified=mask(0b1111)))


def test_a_cleared_mask_bit_is_unavailable(harness, captured):
    resealed(harness, captured, lambda s: s.update(certified=mask(0b0011)))


def test_an_empty_mask_is_unavailable(harness, captured):
    resealed(harness, captured, lambda s: s.update(certified=mask(0)))


def test_stray_bits_past_the_last_row_are_unavailable(harness, captured):
    resealed(harness, captured, lambda s: s.update(certified=mask(0b10001011)))


@pytest.mark.parametrize("octets", [(), (0b1011, 0)])
def test_a_mask_of_the_wrong_length_is_unavailable(harness, captured, octets):
    resealed(harness, captured, lambda s: s.update(certified=mask(*octets)))


def test_a_mask_that_is_not_base64_is_unavailable(harness, captured):
    resealed(harness, captured, lambda s: s.update(certified="C*=="))


@pytest.mark.parametrize("rows", [3, 5, "4", True, None])
def test_a_row_count_other_than_the_live_one_is_unavailable(harness, captured, rows):
    resealed(harness, captured, lambda s: s.update(rows=rows))


@pytest.mark.parametrize("key", ["index_sha256", "values_sha256"])
def test_a_rewritten_digest_is_unavailable(harness, captured, key):
    resealed(harness, captured, lambda s: s.update({key: "0" * 64}))


def test_the_index_digest_binds_the_labels(harness, captured):
    # The labels of a shifted index, sealed into a valid record.
    shifted = [STEPS[1], STEPS[2], STEPS[3], "2026-01-01T04:00:00"]
    resealed(
        harness,
        captured,
        lambda s: s.update(
            index_sha256=records._series_digest("series-index/1", [shifted, None])
        ),
    )


def test_the_values_digest_binds_the_certified_values(harness, captured):
    resealed(
        harness,
        captured,
        lambda s: s.update(
            values_sha256=records._series_digest(
                "series-values/1", [4.0, 5.5, None, 6.5]
            )
        ),
    )


@pytest.mark.parametrize("key", ["rows", "certified", "index_sha256", "values_sha256"])
def test_a_missing_binding_is_unavailable(harness, captured, key):
    resealed(harness, captured, lambda s: s.pop(key))


def test_an_extra_binding_is_unavailable(harness, captured):
    resealed(harness, captured, lambda s: s.update(facts=[]))


@pytest.mark.parametrize(
    "edit",
    [
        lambda n: n.set_snapshots(n.snapshots + pd.Timedelta(hours=1)),
        lambda n: n.set_snapshots(n.snapshots[[1, 0, 2, 3]]),
        lambda n: n.loads_t.p_set.iloc.__setitem__((3, 0), 6.5),
        lambda n: n.set_snapshots(n.snapshots[:3]),
        lambda n: n.set_snapshots(n.snapshots.append(pd.DatetimeIndex(["2026-01-01T04:00"]))),
    ],
    ids=["relabelled", "reordered", "value", "one-short", "one-long"],
)
def test_a_live_series_edit_is_unavailable(harness, captured, edit):
    edit(network())
    assert_unavailable(harness, captured[1])


def test_the_old_per_step_record_is_unavailable(harness, captured):
    evidence_id, path = captured
    facts = harness.client.get(path).json()["facts"]

    def old(record):
        del record["series"]
        record["facts"] = facts
        record["adapter_version"] = "series-input/1"

    rewrite(evidence_id, old)
    assert_unavailable(harness, path)
    assert records.INPUT_ADAPTERS["series"] == "series-input/2"


# --- the pinned encoding -----------------------------------------------------


def test_the_digest_input_is_pinned_and_domain_separated():
    value = [["2026-01-01T00:00:00"], None]
    canonical = b'[["2026-01-01T00:00:00"],null]'
    assert records._canonical(value) == canonical
    index = records._series_digest("series-index/1", value)
    assert index == records.hashlib.sha256(b"series-index/1\n" + canonical).hexdigest()
    assert index != records._series_digest("series-values/1", value)
    with pytest.raises(ValueError):
        records._series_digest("series-values/1", [math.nan])


def test_the_mask_is_one_bit_per_row_least_significant_first():
    steps = [{"value": float(i)} for i in range(10)]
    certified = [True, False, True, True] + [False] * 5 + [True]
    window = records._series_window(["t"] * 10, None, steps, certified)
    assert window["rows"] == 10
    assert base64.b64decode(window["certified"]) == bytes([0b00001101, 0b00000010])
    assert window["values_sha256"] == records._series_digest(
        "series-values/1", [0.0, None, 2.0, 3.0, None, None, None, None, None, 9.0]
    )
