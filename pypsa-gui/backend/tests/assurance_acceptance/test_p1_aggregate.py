"""A1.5: a complete-source aggregate as checked evidence (get_aggregate_load).

The peak and mean of the summed load p_set are computed from the raw series of
every target load, over every step, before the handler runs. A partial sum (a
load with no profile, a missing step, an unknown or repeated name, misaligned
indexes) is refused instead of being certified as the whole.
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
from tests.assurance_acceptance.test_p1_integrity import rewrite

SECTION = {"section": "electricity"}
# Members peak at steps 0 and 2; the sum peaks at step 1.
L1 = [5.0, 4.0, 1.0]
L2 = [1.0, 4.0, 5.0]
PEAK, MEAN = 8.0, 20.0 / 3.0


def network():
    return PyPSAService.get_active_context().network


@pytest.fixture
def loads(baseline):
    n = network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.add("Bus", "H", carrier="H2")
    n.add("Load", "L2", bus="B1")
    n.add("Load", "H1", bus="H")
    n.loads_t.p_set["L1"] = L1
    n.loads_t.p_set["L2"] = L2
    n.loads_t.p_set["H1"] = [100.0] * 3
    return baseline


def draft(metric="peak", value=PEAK, unit="MW", entity="electricity", path=None):
    return json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "aggregate",
                    "kind": "observed",
                    "entity_id": entity,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "source": {
                        "tool_use_id": "read-aggregate",
                        "path": path or f"/{metric}",
                    },
                }
            ],
        }
    )


def read_aggregate(harness, text=None, args=SECTION):
    return harness.run(
        [
            tool_turn("get_aggregate_load", args, call_id="read-aggregate"),
            text_turn(text or draft()),
            text_turn(text or draft()),
        ]
    )


def aggregate_result(frames):
    return next(
        data
        for event, data in frames
        if data.get("tool_use_id") == "read-aggregate"
        and event in ("tool_result", "tool_error")
    )


def wrap_handler(monkeypatch, after=lambda result: None):
    """Run the real get_aggregate_load, then `after(result)` before capture."""
    real = chat_tools.DISPATCHERS["get_aggregate_load"]
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        result = real(**kwargs)
        after(result)
        return result

    monkeypatch.setitem(chat_tools.DISPATCHERS, "get_aggregate_load", handler)
    return calls


def checked(harness, text=None, args=SECTION):
    frames = read_aggregate(harness, text, args)
    approved = approval(frames)
    assert approved["verdict"] == "passed"
    (evidence_id,) = approved["receipt"]["evidence_ids"]
    return frames, evidence_id


def evidence_status(harness, evidence_id):
    return harness.client.get(f"/api/chat/evidence/{evidence_id}").status_code


def test_section_peak_is_checked_over_every_load_and_step(loads, harness):
    shown = chat_tools.DISPATCHERS["get_aggregate_load"](section="electricity")
    assert (shown["peak"], shown["mean"]) == (PEAK, MEAN)
    frames, evidence_id = checked(harness)
    text = public_text(frames)
    assert "Aggregate load (section electricity, 2 loads, 3 steps): peak = 8 MW" in text
    assert "sum over every load and step" in text
    record = harness.client.get(f"/api/chat/evidence/{evidence_id}").json()
    assert record["project_id"] == loads[1]
    assert record["scope"] == {
        "kind": "aggregate_load",
        "selector": "section",
        "entity_id": "electricity",
        "component_class": "Load",
        "attribute": "p_set",
        "loads": 2,
        "steps": 3,
    }
    assert record["facts"] == [
        {"path": "/peak", "entity_id": "electricity", "metric": "peak",
         "value": PEAK, "unit": "MW"},
        {"path": "/mean", "entity_id": "electricity", "metric": "mean",
         "value": MEAN, "unit": "MW"},
    ]
    # The model sees the certified aggregate, not the unchecked step values.
    shown = aggregate_result(frames)["result"]
    assert (shown["peak"], shown["mean"], shown["loads"], shown["steps"]) == (
        PEAK, MEAN, 2, 3,
    )
    assert "values" not in shown


def test_mean_is_checked_too(loads, harness):
    frames, _ = checked(harness, draft(metric="mean", value=MEAN))
    assert f"mean = {MEAN!r} MW" in public_text(frames)


@pytest.mark.parametrize(
    ("args", "entity", "peak"),
    [
        ({}, "all loads", 108.0),
        ({"names": "L2,L1"}, "L2,L1", PEAK),
        ({"section": " Electricity "}, "electricity", PEAK),
    ],
    ids=["all", "names", "mixed-case-section"],
)
def test_all_loads_and_named_loads_are_checked(loads, harness, args, entity, peak):
    frames, _ = checked(harness, draft(value=peak, entity=entity), args)
    assert approval(frames)["verdict"] == "passed"


def test_peak_past_the_truncated_result_is_the_global_peak(loads, harness):
    """A full year is far over the shown-result budget; the peak is still global."""
    n = network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=8760, freq="h"))
    n.loads_t.p_set["L1"] = [1.0] * 8759 + [50.0]
    n.loads_t.p_set["L2"] = [2.0] * 8760
    frames, _ = checked(harness, draft(value=52.0))
    assert "peak = 52 MW" in public_text(frames)
    assert "8760 steps" in public_text(frames)


@pytest.mark.parametrize(
    ("change", "args", "gap"),
    [
        (lambda n: n.add("Load", "L3", bus="B1", p_set=7), SECTION,
         "1 of 3 loads have no profile"),
        (lambda n: n.loads_t.p_set.__setitem__("L2", [1.0, math.nan, 5.0]), SECTION,
         "1 missing or non-finite step"),
        (lambda n: n.loads_t.p_set.__setitem__("L2", [1.0, math.inf, 5.0]), SECTION,
         "1 missing or non-finite step"),
        (lambda n: n.loads_t.p_set.__setitem__(
            "L2", pd.Series(["1", "4", "5"], index=n.snapshots, dtype=object)),
         SECTION, "a step that is not a number"),
        (lambda n: None, {"names": "L1,L9"}, "no load named L9"),
        (lambda n: None, {"section": "heat"}, "no loads"),
    ],
    ids=["no-profile", "nan", "inf", "string", "unknown-name", "empty"],
)
def test_incomplete_coverage_is_refused_before_the_handler(
    loads, harness, monkeypatch, change, args, gap
):
    change(network())
    calls = wrap_handler(monkeypatch)
    frames = read_aggregate(harness, args=args)
    assert not calls
    refused = aggregate_result(frames)
    assert refused["error_kind"] == "aggregate_not_checkable"
    assert gap in refused["message"]
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize(
    "uploads",
    [
        {"L2": pd.date_range("2026-01-01", periods=3, freq="h", tz="Europe/Berlin")},
        # Both members agree with each other, on an older snapshot set.
        {
            "L1": pd.date_range("2025-01-01", periods=3, freq="h"),
            "L2": pd.date_range("2025-01-01", periods=3, freq="h"),
        },
    ],
    ids=["one-member", "older-snapshots"],
)
def test_a_member_off_the_network_snapshots_is_refused(
    loads, harness, monkeypatch, uploads
):
    for name, index in uploads.items():
        PyPSAService.get_user_ts()[("loads", "p_set", name)] = pd.Series(
            L1 if name == "L1" else L2, index=index
        )
    calls = wrap_handler(monkeypatch)
    frames = read_aggregate(harness)
    assert not calls
    refused = aggregate_result(frames)
    assert refused["error_kind"] == "aggregate_not_checkable"
    assert "do not share the network's snapshots" in refused["message"]


@pytest.mark.parametrize(
    "args",
    [
        {"section": "electricity", "names": "L1"},
        {"names": "L1,L1"},
        {"names": "L1, L2"},
        {"names": "L1,"},
        {"section": 7},
        {"section": None},
        {"section": ""},
        {"section": "  "},
        {"names": ""},
        {"period": 2026},
    ],
    ids=[
        "both", "duplicate", "padded", "empty-name", "number", "null-section",
        "empty-section", "blank-section", "empty-names", "extra",
    ],
)
def test_inexact_args_are_refused_before_the_handler(loads, harness, monkeypatch, args):
    calls = wrap_handler(monkeypatch)
    frames = read_aggregate(harness, args=args)
    assert not calls
    assert aggregate_result(frames)["error_kind"] == "capability_unsupported"
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize(
    "text",
    [
        draft(value=9.0),
        draft(unit="kW"),
        draft(entity="all loads"),
        draft(metric="max", path="/peak"),
        draft(value=5.0, path="/L1/2026-01-01T00:00:00"),
    ],
    ids=["value", "unit", "entity", "metric", "member-step"],
)
def test_a_claim_off_the_aggregate_is_refused(loads, harness, text):
    frames = read_aggregate(harness, text)
    assert approval(frames)["verdict"] == "limited"
    assert "Aggregate load" not in public_text(frames)


@pytest.mark.parametrize(
    "shown",
    [{"peak": 9.0}, {"total_loads": 3}, {"loads_with_profile": 1}],
    ids=["peak", "total-loads", "loads-with-profile"],
)
def test_any_shown_mismatch_stores_no_evidence(loads, harness, monkeypatch, shown):
    """An edit between the guard and the handler leaves the answer unchecked,
    even for the statistic that still matches."""
    wrap_handler(monkeypatch, lambda result: result.update(shown))
    frames = read_aggregate(harness, draft(metric="mean", value=MEAN))
    assert approval(frames)["verdict"] == "limited"
    assert aggregate_result(frames)["result"]["_assurance"]["evidence_ids"] == []


def test_facts_equal_the_handlers_shown_values_bit_for_bit(loads, harness):
    """Fifty loads of awkward floats: the guard sums exactly as the handler."""
    n = network()
    n.set_snapshots(pd.date_range("2026-01-01", periods=24, freq="h"))
    for i in range(50):
        name = f"X{i}"
        n.add("Load", name, bus="B1")
        n.loads_t.p_set[name] = [0.1 * (i + 1) + 1e-9 * k + 1 / 3 for k in range(24)]
    n.loads_t.p_set["L1"] = [0.7] * 24
    n.loads_t.p_set["L2"] = [1 / 7] * 24
    shown = chat_tools.DISPATCHERS["get_aggregate_load"](section="electricity")
    frames, evidence_id = checked(harness, draft(value=shown["peak"]))
    record = harness.client.get(f"/api/chat/evidence/{evidence_id}").json()
    assert {f["metric"]: f["value"] for f in record["facts"]} == {
        "peak": shown["peak"],
        "mean": shown["mean"],
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda n: n.loads_t.p_set.__setitem__("L1", [5.0, 4.0, 2.0]),
        lambda n: (n.add("Load", "L3", bus="B1"),
                   n.loads_t.p_set.__setitem__("L3", [0.0, 0.0, 0.0])),
        lambda n: n.loads.__setitem__("bus", ["H", "B1", "H"]),
    ],
    ids=["edit", "member-added", "member-moved"],
)
def test_a_change_after_capture_makes_the_aggregate_unavailable(loads, harness, change):
    _, evidence_id = checked(harness)
    assert evidence_status(harness, evidence_id) == 200
    change(network())
    assert evidence_status(harness, evidence_id) == 409


def test_an_upload_over_a_member_makes_the_aggregate_unavailable(loads, harness):
    _, evidence_id = checked(harness)
    index = network().snapshots
    PyPSAService.get_user_ts()[("loads", "p_set", "L1")] = pd.Series(L1, index=index)
    # Same values from another source: still a different source.
    assert evidence_status(harness, evidence_id) == 409


def test_a_rewritten_fact_makes_the_aggregate_unavailable(loads, harness):
    _, evidence_id = checked(harness)
    rewrite(evidence_id, lambda record: record["facts"][0].update(value=9.0))
    assert evidence_status(harness, evidence_id) == 409


def test_another_project_makes_the_aggregate_unavailable(loads, harness):
    _, evidence_id = checked(harness)
    response = harness.client.post("/api/projects/Other Project")
    assert response.status_code == 200, response.text
    assert evidence_status(harness, evidence_id) == 409


def test_the_source_digest_covers_the_target_set(loads):
    ctx = PyPSAService.get_active_context()
    with records.network_guard():
        before = records.aggregate_source(ctx, SECTION)[0]
        assert records.aggregate_source(ctx, {"names": "L1,L2"})[0] != before
        assert records.aggregate_source(ctx, {"names": "L2,L1"})[0] != (
            records.aggregate_source(ctx, {"names": "L1,L2"})[0]
        )
