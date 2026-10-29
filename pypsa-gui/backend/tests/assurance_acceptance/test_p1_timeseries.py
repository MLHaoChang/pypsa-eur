"""A1: steps of one configured time series as checked evidence (get_timeseries).

A step's evidence is the raw value of the series get_timeseries reads (a user
upload before the network column), bound to its timestep (and period) and to a
digest of the raw series taken before the handler ran.
"""

import json
import math

import pandas as pd
import pytest

from services import assurance_records as records
from services import chat_tools
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import (
    approval,
    public_text,
    text_turn,
    tool_turn,
)

ARGS = {"component": "loads", "name": "L1", "attribute": "p_set"}
STEPS = [
    "2026-01-01T00:00:00",
    "2026-01-01T01:00:00",
    "2026-01-01T02:00:00",
    "2026-01-01T03:00:00",
]


def network():
    return PyPSAService.get_active_context().network


def set_series(values, start="2026-01-01", periods=None):
    """Replace the snapshots and L1's p_set series (G1 keeps its static inputs)."""
    n = network()
    hours = pd.date_range(start, periods=len(values) // len(periods or [0]), freq="h")
    if periods:
        n.set_snapshots(pd.MultiIndex.from_product([periods, hours]))
        n.investment_periods = periods
    else:
        n.set_snapshots(hours)
    n.loads_t.p_set["L1"] = values


@pytest.fixture
def series(baseline):
    set_series([4.0, 5.5, math.nan, 6.0])
    return baseline


def series_draft(value=5.5, step=STEPS[1], entity="L1", unit="MW", path=None):
    return json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "series-step",
                    "kind": "observed",
                    "entity_id": entity,
                    "metric": "p_set",
                    "value": value,
                    "unit": unit,
                    "source": {
                        "tool_use_id": "read-series",
                        "path": path or f"/{entity}/{step}",
                    },
                }
            ],
        }
    )


def read_series(harness, draft=None, args=ARGS):
    return harness.run(
        [
            tool_turn("get_timeseries", args, call_id="read-series"),
            text_turn(draft or series_draft()),
            text_turn(draft or series_draft()),
        ]
    )


def series_result(frames):
    return next(
        data
        for event, data in frames
        if data.get("tool_use_id") == "read-series"
        and event in ("tool_result", "tool_error")
    )


def evidence_ids(frames):
    return (
        series_result(frames)
        .get("result", {})
        .get("_assurance", {})
        .get("evidence_ids", [])
    )


def wrap_handler(monkeypatch, after):
    """Run the real get_timeseries, then `after(result)` before capture."""
    real = chat_tools.DISPATCHERS["get_timeseries"]
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        result = real(**kwargs)
        after(result)
        return result

    monkeypatch.setitem(chat_tools.DISPATCHERS, "get_timeseries", handler)
    return calls


def test_series_step_has_step_scoped_project_evidence(series, harness):
    frames = read_series(harness)
    approved = approval(frames)
    assert approved["verdict"] == "passed"
    text = public_text(frames)
    assert f"p_set at {STEPS[1]} = 5.5 MW" in text
    assert "not the whole series" in text
    record = harness.client.get(
        f"/api/chat/evidence/{approved['receipt']['evidence_ids'][0]}"
    ).json()
    assert record["project_id"] == series[1]
    assert record["scope"] == {
        "component_class": "Load",
        "component": "loads",
        "entity_id": "L1",
        "attribute": "p_set",
        "kind": "series",
        "points": 4,
        "certified": 3,
    }
    # The NaN step is not a fact; no step is a series aggregate.
    assert [f["path"] for f in record["facts"]] == [
        f"/L1/{STEPS[0]}",
        f"/L1/{STEPS[1]}",
        f"/L1/{STEPS[3]}",
    ]
    assert record["facts"][1] == {
        "path": f"/L1/{STEPS[1]}",
        "entity_id": "L1",
        "metric": "p_set",
        "value": 5.5,
        "unit": "MW",
    }
    # The model sees only certified steps, built from the evidence.
    shown = series_result(frames)["result"]
    assert shown["points"] == [[STEPS[0], 4.0], [STEPS[1], 5.5], [STEPS[3], 6.0]]
    assert (shown["steps"], shown["certified_steps"]) == (4, 3)


def test_user_upload_wins_and_its_time_zone_is_bound(series, harness):
    berlin = pd.date_range("2026-01-01", periods=4, freq="h", tz="Europe/Berlin")
    network().generators_t.p_max_pu["G1"] = [0.9] * 4
    uploads = PyPSAService.get_user_ts()
    uploads[("generators", "p_max_pu", "G1")] = pd.Series(
        [0.1, 0.2, 0.3, 0.4], index=berlin
    )
    args = {"component": "generators", "name": "G1", "attribute": "p_max_pu"}
    draft = json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "upload-step",
                    "kind": "observed",
                    "entity_id": "G1",
                    "metric": "p_max_pu",
                    "value": 0.2,
                    "unit": "p.u.",
                    "source": {"tool_use_id": "read-series", "path": f"/G1/{STEPS[1]}"},
                }
            ],
        }
    )
    frames = read_series(harness, draft, args)
    assert approval(frames)["verdict"] == "passed"
    assert "0.2 p.u." in public_text(frames)
    # Same wall-clock labels in another zone are other instants: a new digest.
    before = records.active_series_source(args)[0]
    uploads[("generators", "p_max_pu", "G1")] = pd.Series(
        [0.1, 0.2, 0.3, 0.4], index=berlin.tz_localize(None).tz_localize("UTC")
    )
    assert records.active_series_source(args)[0] != before
    evidence_id = evidence_ids(frames)[0]
    assert harness.client.get(f"/api/chat/evidence/{evidence_id}").status_code == 409


@pytest.mark.parametrize(
    "draft",
    [
        series_draft(value=5),  # wrong value
        series_draft(value=0, step=STEPS[2]),  # the NaN step
        series_draft(value=6, step="2026-01-01T04:00:00"),  # never shown
        series_draft(path="/L1/1"),  # a position, not a timestep
        series_draft(unit="kW"),
        series_draft(entity="L2"),
    ],
    ids=["value", "nan-step", "unshown", "position", "unit", "entity"],
)
def test_claim_off_a_shown_step_is_refused(series, harness, draft):
    frames = read_series(harness, draft)
    assert approval(frames)["verdict"] == "limited"
    assert "2026-01-01T0" not in public_text(frames)


@pytest.mark.parametrize(
    "args",
    [
        {**ARGS, "period": 2026},
        {**ARGS, "columns": "L1"},
        {**ARGS, "attribute": "q_set"},
        {**ARGS, "component": "lines"},
        {**ARGS, "name": "L1,G1"},
        {**ARGS, "name": " L1"},
        {**ARGS, "name": ""},
        {**ARGS, "name": 1},
        {"component": "loads", "name": "L1"},
    ],
    ids=[
        "period",
        "extra",
        "attribute",
        "component",
        "comma",
        "padded",
        "empty",
        "number",
        "missing",
    ],
)
def test_inexact_args_are_refused_before_the_handler(
    series, harness, monkeypatch, args
):
    calls = wrap_handler(monkeypatch, lambda result: None)
    frames = read_series(harness, args=args)
    assert not calls
    assert series_result(frames)["error_kind"] == "capability_unsupported"
    assert approval(frames)["verdict"] == "limited"


def test_series_over_the_step_limit_is_refused_with_its_kind(
    baseline, harness, monkeypatch
):
    set_series([1.0] * (records.MAX_SERIES_POINTS + 1))
    calls = wrap_handler(monkeypatch, lambda result: None)
    frames = read_series(harness, series_draft(value=1, step=STEPS[0]))
    assert not calls
    refused = series_result(frames)
    assert refused["error_kind"] == "series_too_long_to_check"
    assert f"{records.MAX_SERIES_POINTS + 1} steps" in refused["message"]
    assert "without checking" in refused["message"]
    assert approval(frames)["verdict"] == "limited"


def test_longest_series_stays_under_the_record_cap(baseline, harness):
    # Values whose repr is as long as a float's gets.
    set_series([-1.2345678901234567e-300 * (i + 1) for i in range(744)])
    assert records.MAX_SERIES_POINTS >= 744
    value = -1.2345678901234567e-300 * 2
    frames = read_series(harness, series_draft(value=value))
    assert approval(frames)["verdict"] == "passed"
    record = harness.client.get(f"/api/chat/evidence/{evidence_ids(frames)[0]}").json()
    assert record["scope"]["certified"] == 744


def test_middle_step_edit_moves_the_source_digest(baseline):
    for length in (64, 744):
        set_series([float(i) for i in range(length)])
        before = records.active_series_source(ARGS)[0]
        network().loads_t.p_set.iloc[length // 2, 0] = 0.5
        assert records.active_series_source(ARGS)[0] != before


def test_series_edit_after_capture_invalidates_the_evidence(series, harness):
    evidence_id = evidence_ids(read_series(harness))[0]
    # Not the claimed step: any edit to the series invalidates it.
    network().loads_t.p_set.iloc[3, 0] = 6.5
    response = harness.client.get(f"/api/chat/evidence/{evidence_id}")
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "evidence_unavailable"
    assert "5.5" not in response.text


def test_project_switch_after_capture_invalidates_the_evidence(series, harness):
    evidence_id = evidence_ids(read_series(harness))[0]
    for path in (
        "/api/projects/Another Baseline",
        "/api/projects/Another Baseline/activate",
    ):
        assert harness.client.post(path).status_code == 200
    response = harness.client.get(f"/api/chat/evidence/{evidence_id}")
    assert response.status_code == 409
    assert "5.5" not in response.text


def test_edit_between_handler_and_capture_yields_no_evidence(
    series, harness, monkeypatch
):
    def edit(result):
        network().loads_t.p_set.iloc[0, 0] = 4.5

    wrap_handler(monkeypatch, edit)
    frames = read_series(harness)
    assert evidence_ids(frames) == []
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize(
    ("shown", "certifies"),
    [
        (5.5, True),
        ("5.5", False),
        (True, False),
        (None, False),
        (5.0, False),
        (math.nan, False),
        (math.inf, False),
    ],
)
def test_shown_value_certifies_only_on_exact_numeric_equality(
    series, harness, monkeypatch, shown, certifies
):
    def show(result):
        result["data"][1][0] = shown

    wrap_handler(monkeypatch, show)
    frames = read_series(harness)
    record = harness.client.get(f"/api/chat/evidence/{evidence_ids(frames)[0]}").json()
    assert (f"/L1/{STEPS[1]}" in {f["path"] for f in record["facts"]}) is certifies
    assert approval(frames)["verdict"] == ("passed" if certifies else "limited")


def test_an_integer_shown_for_an_equal_float_certifies(baseline, harness, monkeypatch):
    set_series([4.0, 6.0, 7.0, 8.0])

    def show(result):
        result["data"][1][0] = 6

    wrap_handler(monkeypatch, show)
    frames = read_series(harness, series_draft(value=6))
    assert approval(frames)["verdict"] == "passed"


@pytest.mark.parametrize("raw", [math.inf, -math.inf])
def test_non_finite_raw_step_never_certifies(series, harness, monkeypatch, raw):
    network().loads_t.p_set.iloc[1, 0] = raw

    def show(result):
        # Even a handler that echoes the raw value cannot certify it.
        result["data"][1][0] = raw

    wrap_handler(monkeypatch, show)
    frames = read_series(harness)
    for evidence_id in evidence_ids(frames):
        record = harness.client.get(f"/api/chat/evidence/{evidence_id}").json()
        assert f"/L1/{STEPS[1]}" not in {f["path"] for f in record["facts"]}
    assert approval(frames)["verdict"] == "limited"


def test_rows_are_mapped_by_timestep_not_position(series, harness, monkeypatch):
    def shift(result):
        result["index"] = result["index"][1:] + result["index"][:1]

    wrap_handler(monkeypatch, shift)
    frames = read_series(harness)
    assert evidence_ids(frames) == []
    assert approval(frames)["verdict"] == "limited"


def test_multi_period_step_path_names_its_period(baseline, harness):
    set_series([1.0, 2.0, 3.0, 4.0], periods=[2030, 2040])
    path = "/L1/2040/2026-01-01T01:00:00"
    frames = read_series(harness, series_draft(value=4, path=path))
    assert approval(frames)["verdict"] == "passed"
    assert "at 2026-01-01T01:00:00 (period 2040) = 4 MW" in public_text(frames)
    # The same timestep without its period is not a fact.
    frames = read_series(harness, series_draft(value=4, path="/L1/2026-01-01T01:00:00"))
    assert approval(frames)["verdict"] == "limited"
