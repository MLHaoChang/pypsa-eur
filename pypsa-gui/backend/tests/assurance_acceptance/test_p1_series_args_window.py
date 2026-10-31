# ruff: noqa: F811, F401
"""AS02-WINDOW-1: get_timeseries start/end args, a checked window of one series."""

import hashlib
import json

import pytest

from routers.network_time_axis import index_labels
from services import assurance_records as records
from tests.assurance_acceptance.support import approval, public_text
from tests.assurance_acceptance.test_p1_series_window import full_year, stored
from tests.assurance_acceptance.test_p1_timeseries import (
    ARGS,
    STEPS,
    evidence_ids,
    network,
    read_series,
    series,
    series_draft,
    series_result,
    wrap_handler,
)

WINDOW = {"start": 1, "end": 3}


def test_route_returns_only_the_window_and_refuses_bad_ones(series, harness):
    url = "/api/network/timeseries/loads/p_set?columns=L1"
    whole = harness.client.get(url).json()
    part = harness.client.get(f"{url}&start=1&end=3").json()
    assert part["index"] == whole["index"][1:3] and part["data"] == whole["data"][1:3]
    for query in (
        "start=1",
        "end=3",
        "start=3&end=3",
        "start=-1&end=2",
        "start=0&end=5",
        "start=x&end=2",
    ):
        assert harness.client.get(f"{url}&{query}").status_code == 422, query


def test_unwindowed_record_is_unchanged(series, harness):
    (evidence_id,) = evidence_ids(read_series(harness))
    record = stored(evidence_id)
    assert "window" not in record["scope"] and len(record["validity"]["warnings"]) == 2
    assert records.series_args(record["scope"]) == ARGS


def test_windowed_step_certifies_and_names_the_window(series, harness):
    args = {**ARGS, **WINDOW}
    frames = read_series(harness, series_draft(), args)
    assert approval(frames)["verdict"] == "passed"
    text = public_text(frames)
    assert f"rows 1 to 3 ({STEPS[1]} to {STEPS[2]}), not the whole series" in text
    shown = series_result(frames)["result"]
    assert shown["window"] == {
        "start": 1,
        "end": 3,
        "first": STEPS[1],
        "last": STEPS[2],
    }
    assert (shown["steps"], shown["certified_steps"]) == (2, 1)
    assert shown["points"] == [[STEPS[1], 5.5]]
    (evidence_id,) = evidence_ids(frames)
    record = stored(evidence_id)
    assert record["scope"]["window"] == shown["window"]
    assert records.series_args(record["scope"]) == args


def test_a_step_outside_the_window_is_not_evidence(series, harness):
    frames = read_series(
        harness, series_draft(value=4.0, step=STEPS[0]), {**ARGS, **WINDOW}
    )
    assert approval(frames)["verdict"] != "passed"


def test_window_moves_the_source_digest(series):
    ctx = records.PyPSAService.get_active_context()
    digests = {
        records._series_source(ctx, {**ARGS, **w})[0]
        for w in (
            {},
            {"start": 0, "end": 2},
            {"start": 1, "end": 3},
            {"start": 1, "end": 4},
        )
    }
    assert len(digests) == 4


def test_edit_inside_the_window_invalidates_but_outside_does_not(series, harness):
    (evidence_id,) = evidence_ids(read_series(harness, args={**ARGS, **WINDOW}))
    scope = stored(evidence_id)["scope"]
    ctx = records.PyPSAService.get_active_context()
    live = lambda: records._series_source(ctx, records.series_args(scope))[0]  # noqa: E731
    before = live()
    network().loads_t.p_set.loc[network().snapshots[3], "L1"] = 99.0
    assert live() == before
    network().loads_t.p_set.loc[network().snapshots[2], "L1"] = 99.0
    assert live() != before


@pytest.mark.parametrize(
    "extra",
    [
        {"start": 1},
        {"end": 3},
        {"start": True, "end": 3},
        {"start": 1, "end": True},
        {"start": -1, "end": 3},
        {"start": 3, "end": 3},
        {"start": 3, "end": 1},
        {"start": 1.0, "end": 3},
        {"start": "1", "end": 3},
        {"start": 1, "end": 3, "period": 0},
    ],
)
def test_guard_refuses_malformed_windows_before_the_handler(
    series, harness, monkeypatch, extra
):
    calls = wrap_handler(monkeypatch, lambda result: None)
    frames = read_series(harness, args={**ARGS, **extra})
    assert not calls
    assert series_result(frames)["error_kind"] == "capability_unsupported"


def test_window_past_the_last_row_is_refused_not_clipped(series, harness):
    frames = read_series(harness, args={**ARGS, "start": 2, "end": 5})
    assert "result" not in series_result(frames) or not evidence_ids(frames)
    assert approval(frames)["verdict"] != "passed"


def test_a_window_makes_a_long_series_checkable(baseline, harness):
    n = records.MAX_SERIES_POINTS + 2000
    values = full_year(n, "L1")
    label = index_labels(network().snapshots)[0][9000]
    args = {**ARGS, "start": 8000, "end": 10000}
    frames = read_series(
        harness,
        series_draft(value=float(values[9000]), path=records.pointer("L1", label)),
        args,
    )
    assert approval(frames)["verdict"] == "passed"
    assert stored(evidence_ids(frames)[0])["series"]["rows"] == 2000
    whole = read_series(harness, args=ARGS)
    assert series_result(whole)["error_kind"] == "series_too_long_to_check"
    oversize = read_series(harness, args={**ARGS, "start": 0, "end": n})
    assert series_result(oversize)["error_kind"] == "series_too_long_to_check"


def test_catalogue_declares_the_window_args():
    from harness import catalogue

    tool = next(t for t in catalogue.TOOLS if t["name"] == "get_timeseries")
    props = tool["input_schema"]["properties"]
    assert props["start"]["type"] == props["end"]["type"] == "integer"
    assert tool["input_schema"]["required"] == ["component", "name", "attribute"]


@pytest.mark.parametrize(
    "start, end, ok",
    [
        (0, 1, True),
        (1, 3, True),
        (True, 3, False),
        (1, True, False),
        (-1, 3, False),
        (3, 3, False),
        (4, 3, False),
        (1.0, 3, False),
        ("1", 3, False),
        (None, 3, False),
    ],
)
def test_window_validity_is_exact(start, end, ok):
    # The catalogue schema already turns away some of these first; this pins the guard's own rule.
    assert records.valid_series_window(start, end) is ok


def test_unwindowed_source_digest_and_series_fact_are_pinned(series):
    """Byte-equal to master's: the same call, taken before the window args existed."""
    ctx = records.PyPSAService.get_active_context()
    digest, found = records._series_source(ctx, ARGS)
    series_ = found
    expected = hashlib.sha256(
        json.dumps(
            {"project_id": ctx.project_uuid, "args": ARGS, "source": "network"},
            sort_keys=True,
        ).encode()
    )
    expected.update(f"values:{series_.dtype}:{len(series_)}".encode())
    expected.update(f"datetime[ns]:{series_.index.tz}".encode())
    expected.update(series_.index.as_unit("ns").asi8.tobytes())
    expected.update(series_.to_numpy(dtype="float64").tobytes())
    assert digest == expected.hexdigest()
    _labels, _periods, steps = records._series_steps(ARGS, found)
    assert steps[1] == {
        "path": f"/L1/{STEPS[1]}",
        "entity_id": "L1",
        "metric": "p_set",
        "value": 5.5,
        "unit": "MW",
    }
