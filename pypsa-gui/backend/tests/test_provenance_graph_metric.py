"""
"Why this number" for a calculation metric (provenance-graph issue 09, PG-03b; GK B (54) Y1–Y9).

A metric an evidence record answered walks to what it was computed from: one pin per raw source key, the window
and the value its formula gives now, each compared with the open network. The run it was computed in is not
compared until slice 04, so the outcome is never fresh. It reads only (COPILOT-1).
"""

# ruff: noqa: F811 -- pytest fixtures imported from shared project setup.
from __future__ import annotations

import json
import threading

import pytest
from fastapi import HTTPException

from harness import calculations
from services import assurance_records as records
from services import chat_tools
from services.provenance_graph import explain as E
from services.provenance_graph import metric as M
from services.provenance_graph.explain import TargetUnknown, explain_metric
from services.pypsa_service import PyPSAService
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import solved  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

METRIC = "dispatch_energy"


@pytest.fixture(autouse=True)
def fact_store(tmp_path):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
        patch.setenv(
            "PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", ",".join(calculations.PROFILES)
        )
        yield


def _scope(ctx, **options):
    return {
        "version": 1,
        "profile": "generator-dispatch/1",
        "project_id": ctx.project_uuid,
        "entity_id": "G1",
        "retain": False,
        **options,
    }


@pytest.fixture
def captured(solved, checked):
    owner = records.actor_id()
    record = records.capture_scoped(owner, _scope(solved))
    return solved, owner, record


def _steps(out):
    return {s["label"]: s for s in out["steps"]}


def test_an_unchanged_network_reads_every_pin_fresh_and_the_outcome_uncompared(
    captured,
):
    ctx, owner, record = captured
    out = explain_metric(owner, record["evidence_id"], METRIC)
    assert list(out)[0] == "outcome"
    steps = _steps(out)
    pinned = [s for s in out["steps"] if s["kind"] == "input"]
    assert pinned and all(s["state"] == "fresh" for s in pinned), [
        (s["label"], s["state"], s["broken"]) for s in pinned
    ]
    assert {
        "window",
        "generators/G1/capacity",
        "generators/G1/inputs/p",
        "generators/G1/inputs/marginal_cost",
        "generators/G1/index",
        "value (generator-energy/1)",
    } <= set(steps)
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [f"run:{record['evidence_id']}"]
    assert out["steps"][-1]["via"]["edge"] == "computed_in"
    assert out["steps"][-1]["state"] == "uncompared"


def _why(owner, record, metric=METRIC):
    return explain_metric(owner, record["evidence_id"], metric)


def _with_record(monkeypatch, record, **changes):
    """`assurance_records.load` returning the checked record with `changes` (a retained or foreign record)."""
    changed = json.loads(json.dumps(record))
    for key, value in changes.items():
        if key == "retain":
            changed["request_scope"]["retain"] = value
        else:
            changed[key] = value
    real = records.load
    monkeypatch.setattr(
        records, "load", lambda owner, rid, kind: (real(owner, rid, kind), changed)[1]
    )
    return changed


# --- states (X2, Y3): fresh, stale, broken, uncompared -------------------------------------------------------


def test_a_sub_precision_edit_reads_stale_with_only_the_compared_fields(captured):
    ctx, owner, record = captured
    ctx.network.generators.at["G1", "marginal_cost"] += 1e-9
    out = _why(owner, record)
    assert out["outcome"]["state"] == "stale"
    step = _steps(out)["generators/G1/inputs/marginal_cost"]
    assert step["state"] == "stale"
    assert step["broken"]["reason"] == "changed"
    assert set(step["broken"]["compared"]) == {
        "field",
        "recorded",
        "current_field",
        "current",
    }
    assert step["broken"]["uncompared"] == []
    assert (
        step["broken"]["compared"]["recorded"] != step["broken"]["compared"]["current"]
    )
    # The solve no longer matches its inputs, so the metric is not answered now: the value pin cannot be read.
    value = _steps(out)["value (generator-energy/1)"]
    assert value["state"] == "stale"
    assert (
        value["broken"]["reason"]
        == f"cannot be read now: '{METRIC}' is not answered now"
    )
    # Every other input still compared and matched: the stale edge is named, not the whole walk.
    others = [
        s for s in out["steps"] if s["kind"] == "input" and s not in (step, value)
    ]
    assert all(s["state"] == "fresh" for s in others), [
        (s["label"], s["state"]) for s in others
    ]


@pytest.mark.parametrize(
    ("column", "leaf"), [("p_nom", "capacity"), ("capital_cost", "capital_cost")]
)
def test_a_sub_precision_scalar_edit_reads_its_pin_stale(captured, column, leaf):
    """Y3 for a scalar pin: compared exactly, never rounded (GK B (55) F1)."""
    ctx, owner, record = captured
    ctx.network.generators.at["G1", column] += 1e-9
    step = _steps(_why(owner, record))[f"generators/G1/{leaf}"]
    assert step["state"] == "stale"
    compared = step["broken"]["compared"]
    assert compared["current"] - compared["recorded"] == pytest.approx(1e-9, rel=1e-3)


def test_a_changed_dispatch_breaks_the_value_pin_exactly(captured):
    ctx, owner, record = captured
    ctx.network.generators_t.p.loc[:, "G1"] *= 2
    out = _why(owner, record)
    value = _steps(out)["value (generator-energy/1)"]
    assert value["state"] == "stale"
    recorded = value["broken"]["compared"]["recorded"]
    assert recorded == record["facts"][0]["value"]
    assert value["broken"]["compared"]["current"] == 2 * recorded
    assert _steps(out)["generators/G1/inputs/p"]["state"] == "stale"
    assert out["outcome"]["state"] == "stale"


def test_an_unchanged_network_still_reads_fresh_after_a_save_and_reload(
    captured, tmp_path
):
    """Y3: the series digest is taken over the canonical form, so dtype or index changes of a round trip do not
    read as an edit."""
    import pypsa

    ctx, owner, record = captured
    path = tmp_path / "round.nc"
    ctx.network.export_to_netcdf(path)
    ctx.network = pypsa.Network(path)
    out = _why(owner, record)
    pinned = [s for s in out["steps"] if s["kind"] == "input"]
    assert all(s["state"] == "fresh" for s in pinned), [
        (s["label"], s["state"], s["broken"]) for s in pinned
    ]


def test_a_retained_record_compares_nothing_with_the_open_network(
    captured, monkeypatch
):
    ctx, owner, record = captured
    _with_record(monkeypatch, record, retain=True)
    monkeypatch.setattr(calculations, "scoped_source", _never)
    ctx.network.generators.at["G1", "marginal_cost"] += (
        5  # not compared: the run is retained
    )
    out = _why(owner, record)
    pinned = [s for s in out["steps"] if s["kind"] == "input"]
    assert pinned and all(s["state"] == "uncompared" for s in pinned)
    assert all(s["via"]["recorded"] == {"unrecorded": M.RETAINED} for s in pinned)
    assert out["outcome"]["state"] == "uncompared"
    assert set(out["outcome"]["uncompared"]) >= {s["node"] for s in pinned}


def test_the_outcome_is_never_fresh_until_the_run_is_compared(captured):
    ctx, owner, record = captured
    out = _why(owner, record)
    assert out["outcome"]["state"] != "fresh"
    run = out["steps"][-1]
    assert run["node"] == f"run:{record['evidence_id']}" and run["kind"] == "run"
    assert run["via"] == {
        "edge": "computed_in",
        "from": out["node"],
        "recorded": {"unrecorded": M.RUN_UNCOMPARED},
    }
    assert run["node"] in out["outcome"]["uncompared"]


def _never(*_a, **_k):
    raise AssertionError("the open network must not be read")


# --- Y1 busy lock, Y2 another project ------------------------------------------------------------------------


def test_a_busy_network_reads_uncompared_never_stale_and_does_not_wait_unbounded(
    captured,
):
    import time

    ctx, owner, record = captured
    held, release = threading.Event(), threading.Event()
    lock = (
        PyPSAService.get_lock()
    )  # this context's lock, held from another thread (a solve or an edit)

    def hold():
        with lock:
            held.set()
            release.wait(30)

    t = threading.Thread(target=hold)
    t.start()
    try:
        held.wait(5)
        began = time.monotonic()
        out = _why(owner, record)
        assert time.monotonic() - began < 10
    finally:
        release.set()
        t.join()
    pinned = [s for s in out["steps"] if s["kind"] == "input"]
    assert pinned and all(s["state"] == "uncompared" for s in pinned)
    assert all(s["via"]["recorded"] == {"unrecorded": M.BUSY} for s in pinned)
    assert out["outcome"]["state"] == "uncompared"


def test_a_record_for_another_project_never_reads_the_open_network(
    captured, monkeypatch
):
    ctx, owner, record = captured
    _with_record(monkeypatch, record, project_id="00000000-0000-4000-8000-000000000000")
    monkeypatch.setattr(calculations, "scoped_source", _never)
    monkeypatch.setattr(records, "scoped_read", _never)
    out = _why(owner, record)
    pinned = [s for s in out["steps"] if s["kind"] == "input"]
    assert pinned and all(s["state"] == "stale" for s in pinned)
    broken = pinned[0]["broken"]
    assert broken["reason"] == f"cannot be read now: {M.OTHER_PROJECT}"
    assert broken["uncompared"] == [
        "current_field",
        "current",
    ]  # X2: only what was compared is listed
    assert out["outcome"]["state"] == "stale"


@pytest.mark.parametrize(
    "error", [ValueError("missing_dispatch"), RuntimeError("missing_dispatch")]
)
def test_an_unreadable_source_reads_stale_never_a_500(captured, monkeypatch, error):
    ctx, owner, record = captured

    def refuse(*_a, **_k):
        raise error

    monkeypatch.setattr(records, "scoped_read", refuse)
    out = _why(owner, record)
    pinned = [s for s in out["steps"] if s["kind"] == "input"]
    assert all(s["state"] == "stale" for s in pinned)
    assert pinned[0]["broken"]["reason"] == "cannot be read now: missing_dispatch"


def test_each_pin_reads_the_source_once(captured, monkeypatch):
    ctx, owner, record = captured
    calls = []
    real = calculations.scoped_calculate
    monkeypatch.setattr(
        calculations, "scoped_calculate", lambda *a: (calls.append(1), real(*a))[1]
    )
    _why(owner, record)
    assert len(calls) == 1


# --- Y4 the run-key deny-list --------------------------------------------------------------------------------


def test_the_run_keys_are_pinned_and_never_compared_as_inputs():
    assert M.RUN_KEYS == {
        "input_fingerprint",
        "solved_fingerprint",
        "status",
        "condition",
        "valid_solve",
        "profile",
    }
    raw = {
        "profile": "p",
        "status": "ok",
        "a": {"input_fingerprint": "x", "b": [1], "c": {}},
    }
    assert M.raw_paths(raw) == [("a", "b"), ("a", "c")]
    assert M.raw_paths({}) == [] and M.raw_paths({"status": "ok"}) == []


def test_two_paths_never_share_a_node():
    """A name holding "/" is escaped, so no pin is merged into another and dropped."""
    paths = [("generators", "a/b", "p"), ("generators", "a", "b", "p"), ("g", "a~1b")]
    assert len({M.label(p) for p in paths}) == 3


def test_a_source_key_the_record_lacks_is_one_more_pin_and_fails_closed(
    captured, monkeypatch
):
    ctx, owner, record = captured
    real = records.scoped_read
    monkeypatch.setattr(
        records, "scoped_read", lambda *a: {**real(*a), "new_input": 1.0}
    )
    out = _why(owner, record)
    step = _steps(out)["new_input"]
    assert step["state"] == "stale" and step["broken"]["reason"] == "not recorded"


def test_a_null_input_is_compared_as_missing_not_as_unrecorded():
    assert M.pinned(None) == M.MISSING
    assert M.pinned([1.0, None]) != M.pinned([1.0, 0.0])
    assert M.pinned(1) == 1 and M.pinned("G1") == "G1"
    with pytest.raises(ValueError):
        M.pinned(float("nan"))


# --- Y5 typed refusals -----------------------------------------------------------------------------------------


def _status(call):
    with pytest.raises(TargetUnknown) as refused:
        call()
    return refused.value.status


def test_each_refusal_has_its_own_status(captured, monkeypatch):
    import uuid

    ctx, owner, record = captured
    eid = record["evidence_id"]
    assert _status(lambda: explain_metric(None, eid, METRIC)) == 401
    assert _status(lambda: explain_metric(owner, eid, "no_such_metric")) == 422
    assert _status(lambda: explain_metric(owner, str(uuid.uuid4()), METRIC)) == 404
    assert _status(lambda: explain_metric(owner, "not-a-uuid", METRIC)) == 404
    unanswered = next(
        m
        for m in calculations.DEFINITIONS
        if m not in {f["metric"] for f in record["facts"]}
    )
    assert _status(lambda: explain_metric(owner, eid, unanswered)) == 404
    with monkeypatch.context() as m:
        m.setattr(records.time, "time", lambda: record["expires_at"] + 1)
        assert _status(lambda: explain_metric(owner, eid, METRIC)) == 404
    with monkeypatch.context() as m:
        m.setattr(
            records,
            "load",
            lambda *a: (_ for _ in ()).throw(records.AccessRevoked(403, "Forbidden")),
        )
        assert (
            _status(lambda: explain_metric(owner, eid, METRIC)) == 404
        )  # revoked reads as never existed
    with monkeypatch.context() as m:
        _with_record(m, record, source_kind="calculated_result")
        assert _status(lambda: explain_metric(owner, eid, METRIC)) == 422


def test_another_owners_record_reads_as_unknown(captured):
    """No existence oracle: a record sealed for another owner, at this owner's own path, is the same 404."""
    import uuid

    ctx, owner, record = captured
    rid = str(uuid.uuid4())
    path = records._path(owner, rid)
    envelope = records.seal(
        {**record, "evidence_id": rid, "owner_id": str(uuid.uuid4())}, rid
    )
    path.write_bytes(json.dumps(envelope).encode())
    with pytest.raises(TargetUnknown) as other:
        explain_metric(owner, rid, METRIC)
    with pytest.raises(TargetUnknown) as unknown:
        explain_metric(owner, str(uuid.uuid4()), METRIC)
    assert other.value.status == unknown.value.status == 404
    assert (
        str(other.value).replace(rid, "ID") == str(unknown.value).split("'")[0] + "'ID'"
    )


def test_only_a_refused_read_is_turned_into_a_refusal(captured, monkeypatch):
    """The catching site takes the store's HTTP refusals only: any other failure is not hidden as a 404."""
    ctx, owner, record = captured

    def boom(*_a):
        raise RuntimeError("store broke")

    monkeypatch.setattr(records, "load", boom)
    with pytest.raises(RuntimeError):
        explain_metric(owner, record["evidence_id"], METRIC)

    def server(*_a):
        raise HTTPException(500, "store broke")

    monkeypatch.setattr(records, "load", server)
    with pytest.raises(HTTPException) as raised:
        explain_metric(owner, record["evidence_id"], METRIC)
    assert raised.value.status_code == 500


# --- Y7 shape, X5 truncation ------------------------------------------------------------------------------------


def test_the_shape_names_what_the_stamp_lacks_and_never_fills_it(captured):
    ctx, owner, record = captured
    out = _why(owner, record)
    assert list(out) == ["outcome", "target", "node", "steps"]
    assert out["target"] == {"evidence_id": record["evidence_id"], "metric": METRIC}
    head = out["steps"][0]
    assert head["node"] == out["node"] and head["value"] == record["facts"][0]["value"]
    assert head["unit"] == "MWh" and head["stamp_missing"] == []
    assert head["stamp"]["source"] == f"evidence:{record['evidence_id']}"
    assert head["stamp"]["formula_id"] == "generator-energy/1"
    inputs = [
        s
        for s in out["steps"]
        if s["kind"] == "input" and not s["label"].startswith("value")
    ]
    assert all(s["unit"] is None and s["stamp_missing"] == ["unit"] for s in inputs)


def test_a_long_walk_is_cut_and_marked_truncated(captured, monkeypatch):
    ctx, owner, record = captured
    whole = _why(owner, record)
    monkeypatch.setattr(E, "MAX_STEPS", 5)
    out = _why(owner, record)
    assert out["outcome"]["truncated"] is True and out["outcome"]["complete"] is False
    assert (
        len(out["steps"]) == 5
        and out["outcome"]["steps_total"] == whole["outcome"]["steps_total"] > 5
    )
    assert (
        out["steps"][-1]["via"]["edge"] == "computed_in"
    )  # why it is never fresh survives the cut
    assert (
        whole["outcome"]["truncated"] is False and whole["outcome"]["complete"] is True
    )


def test_a_walk_exactly_at_the_cap_is_complete(captured, monkeypatch):
    ctx, owner, record = captured
    total = _why(owner, record)["outcome"]["steps_total"]
    monkeypatch.setattr(E, "MAX_STEPS", total)
    out = _why(owner, record)
    assert out["outcome"]["truncated"] is False and out["outcome"]["complete"] is True
    assert len(out["steps"]) == total


# --- X1/Y8 read-only, X6 route and tool ----------------------------------------------------------------------


def _snapshot(ctx, record_path):
    n = ctx.network
    return (
        json.dumps(
            {
                "meta": n.meta,
                "generators": n.generators.to_json(),
                "p": n.generators_t.p.to_json(),
            },
            sort_keys=True,
            default=str,
        ),
        json.dumps(ctx.user_ts_meta, sort_keys=True, default=str),
        record_path.read_bytes(),
        record_path.stat().st_mtime_ns,
        sorted(p.name for p in record_path.parent.iterdir()),
    )


def test_the_route_and_the_tool_answer_the_same_and_write_nothing(captured, client):
    ctx, owner, record = captured
    eid = record["evidence_id"]
    path = records._path(owner, eid)
    before = _snapshot(ctx, path)
    via_route = client.get(f"/api/chat/evidence/{eid}/why/{METRIC}")
    assert via_route.status_code == 200, via_route.text
    via_tool = chat_tools.DISPATCHERS["why_this_number"](evidence_id=eid, metric=METRIC)
    assert via_tool == via_route.json()
    assert _snapshot(ctx, path) == before


def test_the_route_refuses_with_a_typed_kind(captured, client):
    import uuid

    ctx, owner, record = captured
    for path, status in (
        (f"/api/chat/evidence/{uuid.uuid4()}/why/{METRIC}", 404),
        (f"/api/chat/evidence/{record['evidence_id']}/why/no_such_metric", 422),
    ):
        response = client.get(path)
        assert response.status_code == status, response.text
        assert response.json()["detail"]["error_kind"] == "why_target_unknown"


def test_the_tool_with_no_acting_user_refuses_401(captured):
    ctx, owner, record = captured
    token = chat_tools._ACTING_USER_ID.set(None)
    try:
        with pytest.raises(HTTPException) as refused:
            chat_tools.DISPATCHERS["why_this_number"](
                evidence_id=record["evidence_id"], metric=METRIC
            )
    finally:
        chat_tools._ACTING_USER_ID.reset(token)
    assert refused.value.status_code == 401
    assert refused.value.detail["error_kind"] == "why_target_unknown"


def test_the_tool_refuses_mixed_targets():
    for args in (
        {
            "evidence_id": "e",
            "metric": METRIC,
            "component": "Generator",
            "name": "g",
            "field": "f",
        },
        {"evidence_id": "e"},
        {"metric": METRIC},
    ):
        with pytest.raises(HTTPException) as refused:
            chat_tools.DISPATCHERS["why_this_number"](**args)
        assert refused.value.detail["error_kind"] == "invalid_tool_args"


def test_the_tool_through_the_harness_dispatch_path(captured):
    from harness.loop import dispatch_tool_call
    from harness.session import ChatSession

    ctx, owner, record = captured
    session = ChatSession(model="m", owner_user_id=owner)
    collector: list[dict] = []
    tu = {
        "id": "tu_1",
        "name": "why_this_number",
        "input": {"evidence_id": record["evidence_id"], "metric": METRIC},
    }
    frames = list(
        dispatch_tool_call(
            session, tu, collector, offered_tool_names={"why_this_number"}
        )
    )
    assert "tool_pending_confirmation" not in [e for e, _ in frames]
    (result,) = collector
    assert result.get("is_error") is not True, result
    text = (
        result["content"]
        if isinstance(result["content"], str)
        else json.dumps(result["content"])
    )
    # A long answer is spilled to a page; its preview, and the dict, start with the outcome (X5).
    assert text.index("outcome") < text.index("steps") and "uncompared" in text


# --- guards the mutant run named (X7) ------------------------------------------------------------------------


def test_a_series_is_pinned_as_the_digest_of_its_canonical_form():
    import hashlib

    for series in ([1.0, 2.0], (1.0, 2.0), {"a": 1}):
        assert (
            M.pinned(series)
            == "sha256:" + hashlib.sha256(records._canonical(series)).hexdigest()
        )


@pytest.mark.parametrize("generators", [["G1"], {}, "G1", None])
def test_only_a_generator_mapping_offers_its_first_generators_index(generators):
    assert M._index_paths({"generators": generators}) == [
        ("index",),
        ("storage_unit", "index"),
    ]
    assert M._index_paths({"generators": {"G1": {}}})[-1] == (
        "generators",
        "G1",
        "index",
    )


def test_a_window_no_held_index_gives_reads_stale(captured, monkeypatch):
    ctx, owner, record = captured
    _with_record(monkeypatch, record, window={**record["window"], "snapshots": 999})
    step = _steps(_why(owner, record))["window"]
    assert step["state"] == "stale"
    assert (
        step["broken"]["reason"]
        == "cannot be read now: the record's window matches no index it holds"
    )


def test_a_record_holding_nulls_still_answers_or_refuses_typed(captured, monkeypatch):
    ctx, owner, record = captured
    with monkeypatch.context() as m:
        _with_record(m, record, raw=None, validity=None)
        out = _why(owner, record)
        assert out["steps"][0]["stamp"]["as_of"] is None
        assert (
            _steps(out)["window"]["state"] == "stale"
        )  # no raw holds the window's index
        held_now = [s for s in out["steps"] if s["label"].startswith("generators/")]
        # Every leaf only the current source has is one more pin, read "not recorded" (fails closed).
        assert held_now and all(
            s["broken"]["reason"] == "not recorded" for s in held_now
        )
    with monkeypatch.context() as m:
        _with_record(m, record, facts=None)
        assert _status(lambda: _why(owner, record)) == 404


def test_a_signed_out_store_read_refuses_401(captured, monkeypatch):
    ctx, owner, record = captured

    def revoked(*_a):
        raise records.AccessRevoked(401, "Authentication required")

    monkeypatch.setattr(records, "load", revoked)
    assert _status(lambda: _why(owner, record)) == 401


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        # Only the lock's own refusal is "busy"; any other refusal is an unreadable source: stale.
        (
            HTTPException(409, {"error_kind": "evidence_unavailable"}),
            "evidence_unavailable",
        ),
        (
            HTTPException(503, {"error_kind": "assurance_context_changed"}),
            "assurance_context_changed",
        ),
        (HTTPException(500, "store broke"), "store broke"),
        (RuntimeError(), "RuntimeError"),
    ],
)
def test_a_refused_read_that_is_not_the_busy_lock_reads_stale(
    captured, monkeypatch, error, reason
):
    ctx, owner, record = captured

    def refuse(*_a, **_k):
        raise error

    monkeypatch.setattr(records, "scoped_read", refuse)
    out = _why(owner, record)
    step = _steps(out)["generators/G1/capacity"]
    assert step["state"] == "stale"
    assert step["broken"]["reason"] == f"cannot be read now: {reason}"
    assert out["outcome"]["state"] == "stale"
