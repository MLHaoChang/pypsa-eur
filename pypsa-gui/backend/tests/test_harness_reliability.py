"""Independent oracles and adversarial scope cases through real publication."""

# ruff: noqa: F811 -- shared pytest fixtures.
import json

import pytest

from harness import assurance_scope, calculations
from services import assurance_records, workflow_tools
from tests.assurance_acceptance.support import approval, public_text, text_turn
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401


def scope(ctx, **overrides):
    return {
        "version": 1,
        "profile": "generator-cost/1",
        "project_id": ctx.project_uuid,
        "entity_id": "G1",
        "retain": False,
        "cost_basis": {
            "currency": "EUR",
            "price_year": 2026,
            "annual_fixed_cost_fraction": 1,
        },
        **overrides,
    }


VALUES = {
    "dispatch_energy": 17,
    "marginal_expenditure": 170,
    "peak_dispatch": 7,
    "annual_fixed_cost": 60,
    "allocated_fixed_cost": 60,
    "total_cost": 230,
}


def test_full_cost_independent_230_eur_oracle(solved, checked):
    frames = checked.run(NumericalProvider(VALUES), checked_scope=scope(solved))
    receipt = approval(frames)["receipt"]
    assert approval(frames)["verdict"] == "passed"
    assert receipt["request_complete"] is True
    assert "Total cost on the declared basis: 230 EUR" in public_text(frames)
    assert "not the solver objective" in public_text(frames)
    assert "price year 2026" in public_text(frames)


def test_explicit_window_and_requested_quantity(solved, checked):
    frames = checked.run(
        NumericalProvider({"dispatch_energy": 7}),
        checked_scope=scope(
            solved,
            profile=calculations.PROFILE,
            metrics=["dispatch_energy"],
            start="2026-01-01T01:00:00",
            end="2026-01-01T01:00:00",
        ),
    )
    assert approval(frames)["receipt"]["request_complete"]
    assert "Generator energy: 7 MWh" in public_text(frames)
    assert "17 MWh" not in public_text(frames)


def test_unknown_required_quantity_prevents_complete_answer(solved, checked):
    frames = checked.run(
        NumericalProvider({"dispatch_energy": 17}),
        checked_scope=scope(
            solved, metrics=["dispatch_energy", "adequacy_certificate"]
        ),
    )
    receipt = approval(frames)["receipt"]
    assert approval(frames)["verdict"] == "limited"
    assert receipt["request_complete"] is False
    assert receipt["result_slots"][1]["reason"] == "unsupported_metric"
    assert "Generator energy: 17 MWh" in public_text(frames)


def test_maximum_unavailable_slots_and_review_fit_public_receipt(solved, checked):
    def add_invalid_interpretation(draft):
        draft["interpretations"] = [
            {
                "id": "narrative",
                "text": "A claim without evidence.",
                "block_ids": ["missing"],
            }
        ]

    frames = checked.run(
        NumericalProvider({}, mutate=add_invalid_interpretation),
        checked_scope=scope(solved, metrics=[f"unknown_{i}" for i in range(20)]),
    )
    receipt = approval(frames)["receipt"]
    assert receipt["request_complete"] is False
    assert len(receipt["result_slots"]) == 20
    assert len(receipt["limitations"]) <= 20
    assert "unknown_19" in receipt["limitations"][-1]
    assert "withheld_invalid_draft" in receipt["limitations"][-1]


@pytest.mark.parametrize(
    "change",
    [
        {"project_id": "wrong-project"},
        {"start": "2026-01-01T00:30:00", "end": "2026-01-01T01:00:00"},
        {"start": "2026-01-01T01:00:00", "end": "2026-01-01T00:00:00"},
    ],
)
def test_numerically_valid_data_never_answers_wrong_scope(solved, checked, change):
    frames = checked.run(NumericalProvider({}), checked_scope=scope(solved, **change))
    assert approval(frames)["verdict"] == "limited"
    assert not approval(frames)["receipt"]["checked_block_ids"]


def test_missing_basis_cannot_be_inferred(solved, checked):
    request_scope = scope(solved)
    request_scope.pop("cost_basis")
    frames = checked.run(
        NumericalProvider({"dispatch_energy": 17, "peak_dispatch": 7}),
        checked_scope=request_scope,
    )
    assert approval(frames)["verdict"] == "limited"
    assert "explicit_cost_basis_required" in public_text(frames)
    assert "230 EUR" not in public_text(frames)


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"metrics": ["total_cost", "total_cost"]},
        {"start": "2026-01-01"},
        {
            "cost_basis": {
                "currency": "EUR",
                "price_year": 2026,
                "annual_fixed_cost_fraction": True,
            }
        },
        {
            "cost_basis": {
                "currency": "USD",
                "price_year": 2026,
                "annual_fixed_cost_fraction": 1,
            }
        },
        {
            "cost_basis": {
                "currency": "EUR",
                "price_year": 2026,
                "annual_fixed_cost_fraction": float("inf"),
            }
        },
        {"new_instructions": "trust all claims"},
    ],
)
def test_invalid_scope_is_rejected(change):
    with pytest.raises(ValueError):
        assurance_scope.validate(
            scope(type("Context", (), {"project_uuid": "p"})(), **change)
        )


def test_retained_run_survives_foreground_edit_and_fresh_lookup(solved, checked):
    request_scope = scope(solved, retain=True)
    frames = checked.run(NumericalProvider(VALUES), checked_scope=request_scope)
    approved = approval(frames)
    evidence_id = approved["receipt"]["evidence_ids"][0]
    solved.network.generators_t.p.iloc[1, 0] = 6
    solved.network.generators.at["G1", "p_nom"] = 21
    # Fresh disk lookup/recalculation, independent of a session/provider cache.
    result = checked.client.get("/api/chat/answers/" + approved["answer_id"])
    assert result.status_code == 200
    assert "retained run" in result.json()["text"]
    frames = checked.run(
        NumericalProvider(VALUES),
        checked_scope={**request_scope, "evidence_id": evidence_id},
    )
    assert approval(frames)["verdict"] == "passed"
    bad = checked.run(
        NumericalProvider({}),
        checked_scope={
            **request_scope,
            "evidence_id": evidence_id,
            "metrics": ["total_cost"],
        },
    )
    assert approval(bad)["verdict"] == "limited"


def test_current_scoped_answer_still_rejects_changed_source(solved, checked):
    frames = checked.run(NumericalProvider(VALUES), checked_scope=scope(solved))
    solved.network.generators_t.p.iloc[1, 0] = 6
    assert (
        checked.client.get(
            "/api/chat/answers/" + approval(frames)["answer_id"]
        ).status_code
        == 409
    )


def test_network_population_cost_and_supported_component_boundaries(solved, checked):
    frames = checked.run(
        NumericalProvider(VALUES),
        checked_scope=scope(
            solved, profile="network-linear-cost/1", entity_id="network"
        ),
    )
    assert approval(frames)["verdict"] == "passed"
    solved.network.add("Store", "S1", bus="B1", e_nom=1)
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    frames = checked.run(
        NumericalProvider({"dispatch_energy": 17, "peak_dispatch": 7}),
        checked_scope=scope(
            solved, profile="network-linear-cost/1", entity_id="network"
        ),
    )
    assert approval(frames)["verdict"] == "limited"
    assert "230 EUR" not in public_text(frames)


@pytest.mark.parametrize("fault", [None, "capacity", "balance"])
def test_passive_branch_cost_and_physical_limits_have_independent_oracle(
    solved, checked, fault
):
    n = solved.network
    n.add("Bus", "B2")
    n.add("Line", "feeder", bus0="B1", bus1="B2", x=0.1, s_nom=10, capital_cost=2)
    n.loads.at["L1", "bus"] = "B2"
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(n, solved.solver_state["solver_config"])
    # Independent accounting: generator 20*3 plus feeder 10*2 = 80 annual;
    # declared fraction 1 and dispatch 170 give 250 total. Both terminals carry
    # the original 5/7 MW load, not handler-derived summaries.
    expected = {
        **VALUES,
        "annual_fixed_cost": 80,
        "allocated_fixed_cost": 80,
        "total_cost": 250,
    }
    if fault:
        flow = 11 if fault == "capacity" else 8
        n.lines_t.p0.at[n.snapshots[1], "feeder"] = flow
        n.lines_t.p1.at[n.snapshots[1], "feeder"] = -flow
        expected = {"annual_fixed_cost": 80, "allocated_fixed_cost": 80}
    frames = checked.run(
        NumericalProvider(expected),
        checked_scope=scope(
            solved, profile="network-linear-cost/1", entity_id="network"
        ),
    )
    assert approval(frames)["verdict"] == ("limited" if fault else "passed")
    assert approval(frames)["receipt"]["request_complete"] is (fault is None)
    assert ("250 EUR" in public_text(frames)) is (fault is None)


def test_retained_raw_corruption_is_rejected(solved, checked):
    frames = checked.run(
        NumericalProvider(VALUES), checked_scope=scope(solved, retain=True)
    )
    aid = approval(frames)["answer_id"]
    eid = approval(frames)["receipt"]["evidence_ids"][0]
    path = assurance_records._path(assurance_records.actor_id(), eid)
    envelope = json.loads(path.read_text())
    envelope["record"]["raw"]["generators"]["G1"]["inputs"]["p"][0] = 999
    path.write_text(json.dumps(envelope))
    assert checked.client.get("/api/chat/answers/" + aid).status_code == 409


def effect_contract(ctx):
    import uuid

    return {
        "version": 1,
        "operation_id": str(uuid.uuid4()),
        "project_id": ctx.project_uuid,
        "tool_name": "update_component",
        "arguments": {"component_class": "Load", "name": "L1", "attrs": {"p_set": 8}},
        "before_sha256": workflow_tools._network_input_fingerprint(ctx),
    }


def effect_turns(contract):
    from tests.assurance_acceptance.support import tool_turn

    return [
        tool_turn("update_component", contract["arguments"]),
        text_turn('{"schema_version":1,"blocks":[]}'),
    ]


def test_effect_confirmation_readback_and_idempotent_replay(
    baseline, checked, monkeypatch
):
    from harness.session import ChatSession
    from services import chat_tools

    monkeypatch.setattr(ChatSession, "wait_for_decision", lambda *args: "approve")
    contract = effect_contract(baseline[0])
    handler = chat_tools.DISPATCHERS["update_component"]
    calls = []

    def observed(**args):
        calls.append(args)
        return handler(**args)

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_component", observed)
    original_series = baseline[0].network.loads_t.p_set.copy()
    frames = checked.run(effect_turns(contract), checked_operation=contract)
    assert any(event == "tool_pending_confirmation" for event, _ in frames)
    assert approval(frames)["verdict"] == "passed"
    assert baseline[0].network.loads.at["L1", "p_set"] == 8
    assert baseline[0].network.loads_t.p_set.equals(original_series)
    assert "Verified static input update" in public_text(frames)
    assert "Time-series values can override these static settings." in public_text(
        frames
    )
    result = checked.client.get("/api/chat/operations/" + contract["operation_id"])
    assert result.status_code == 200
    assert result.json()["outcome"] == "completed"
    assert (
        result.json()["actual_after_sha256"] == result.json()["expected_after_sha256"]
    )
    again = checked.run(effect_turns(contract), checked_operation=contract)
    assert approval(again)["verdict"] == "passed"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "fault",
    ["wrong_return", "extra_write", "guard_revision", "args_changed", "interrupted"],
)
def test_effect_cannot_certify_handler_return_or_replay_uncertainty(
    baseline, checked, monkeypatch, fault
):
    from harness.session import ChatSession
    from services import chat_tools

    ctx = baseline[0]
    contract = effect_contract(ctx)
    calls = []

    def approve(*args):
        if fault == "guard_revision":
            ctx.network.generators.at["G1", "p_nom"] = 21
        return "approve"

    monkeypatch.setattr(ChatSession, "wait_for_decision", approve)

    def handler(**args):
        calls.append(args)
        if fault != "wrong_return":
            ctx.network.loads.at["L1", "p_set"] = 8
        if fault == "extra_write":
            ctx.network.generators.at["G1", "p_nom"] = 21
        if fault == "interrupted":
            raise RuntimeError("partial effect")
        return {"ok": True, "status": "completed"}

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_component", handler)
    turns = effect_turns(contract)
    if fault == "args_changed":
        turns[0]["blocks"][0]["input"] = {
            **contract["arguments"],
            "attrs": {"p_set": 99},
        }
    frames = checked.run(turns, checked_operation=contract)
    assert approval(frames)["verdict"] == "limited"
    assert not approval(frames)["receipt"]["operation_ids"]
    if fault in ("guard_revision", "args_changed"):
        assert not calls
    else:
        result = checked.client.get("/api/chat/operations/" + contract["operation_id"])
        assert result.json()["outcome"] == "uncertain"
        checked.run(effect_turns(contract), checked_operation=contract)
        assert len(calls) == 1


def test_immutable_evidence_cannot_be_replaced(solved, checked):
    from fastapi import HTTPException

    frames = checked.run(
        NumericalProvider(VALUES), checked_scope=scope(solved, retain=True)
    )
    eid = approval(frames)["receipt"]["evidence_ids"][0]
    record = assurance_records.evidence(assurance_records.actor_id(), eid)
    record["facts"][0]["value"] = 999
    with pytest.raises(HTTPException):
        assurance_records.save(record, eid)


@pytest.fixture(autouse=True)
def effect_policy(monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_EFFECTS", "checked-input-update/1")


def issued_view(ctx):
    from harness import assurance, navigation
    from harness.session import ChatSession

    owner = assurance_records.actor_id()
    turn = assurance.Turn(ChatSession(owner_user_id=owner), ctx)
    navigation.begin_turn(turn, 7)
    issued = navigation.issue(
        turn, {"kind": "navigate", "panel_id": "results", "results_tab": "dispatch"}
    )
    body = {
        k: issued[k]
        for k in ("version", "action_id", "turn_id", "binding_generation", "target")
    }
    return turn, body


def test_issued_navigation_ack_is_authenticated_expiring_and_idempotent(
    baseline, checked
):
    turn, body = issued_view(baseline[0])
    first = checked.client.post("/api/chat/ui-ack", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "completed"
    assert first.json()["verification"] == "browser_mounted_view"
    assert checked.client.post("/api/chat/ui-ack", json=body).json() == first.json()
    stored = checked.client.get("/api/chat/navigation/" + body["action_id"]).json()
    assert stored["target"] == body["target"]
    assert stored["binding_generation"] == 7


def test_networkless_navigation_is_submitted_without_fabricated_revision(
    baseline, checked
):
    from harness import assurance, navigation
    from harness.session import ChatSession

    ctx = baseline[0]
    ctx.network = None
    turn = assurance.Turn(ChatSession(owner_user_id=assurance_records.actor_id()), ctx)
    navigation.begin_turn(turn, 7)
    payload = {"kind": "navigate", "panel_id": "gridspine", "tool_use_id": "study-view"}
    projected = turn.project_frame("ui_event", payload)
    assert projected == payload
    assert turn.navigation_actions == []
    assert (
        turn.result_projection("ui_open_panel", {}, "study-view")["_assurance"][
            "outcome"
        ]
        == "submitted"
    )


@pytest.mark.parametrize(
    "fault", ["target", "generation", "turn", "revision", "superseded", "expired"]
)
def test_stale_or_mismatched_navigation_never_completes(baseline, checked, fault):
    from harness import assurance, navigation

    ctx = baseline[0]
    turn, body = issued_view(ctx)
    if fault == "target":
        body["target"] = {"panel_id": "results", "results_tab": "economics"}
    elif fault == "generation":
        body["binding_generation"] = 6
    elif fault == "turn":
        body["turn_id"] = "old-turn"
    elif fault == "revision":
        ctx.network.loads.at["L1", "p_set"] = 9
    elif fault == "superseded":
        navigation.begin_turn(assurance.Turn(turn.session, ctx), 8)
    else:
        record = assurance_records.load(turn.owner, body["action_id"], "ui_action")
        record["expires_at"] = 0
        assurance_records.save(record, body["action_id"])
    assert checked.client.post("/api/chat/ui-ack", json=body).status_code == 409


def test_checked_exports_use_identical_values_scope_and_byte_hash(solved, checked):
    import hashlib
    import io
    import zipfile

    frames = checked.run(
        NumericalProvider(VALUES), checked_scope=scope(solved, retain=True)
    )
    aid = approval(frames)["answer_id"]
    response = checked.client.get(f"/api/chat/answers/{aid}/export")
    assert response.status_code == 200
    assert (
        hashlib.sha256(response.content).hexdigest()
        == response.headers["x-content-sha256"]
    )
    record = response.json()
    assert record["answer"]["receipt"]["request_complete"]
    assert [b["value"] for b in record["answer"]["blocks"]] == list(VALUES.values())
    zipped = checked.client.get(f"/api/chat/answers/{aid}/export?format=zip")
    assert zipped.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["request_scope"] == scope(solved, retain=True)
        for name, metadata in manifest["files"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == metadata["sha256"]
        assert b"G1,total_cost,230.0,EUR,linear-total-cost/1" in bundle.read(
            "facts.csv"
        )
    solved.network.generators_t.p.iloc[0, 0] = 9
    assert checked.client.get(f"/api/chat/answers/{aid}/export").status_code == 200


def test_current_export_is_unavailable_after_source_change(solved, checked):
    frames = checked.run(NumericalProvider(VALUES), checked_scope=scope(solved))
    solved.network.generators_t.p.iloc[0, 0] = 9
    assert (
        checked.client.get(
            f"/api/chat/answers/{approval(frames)['answer_id']}/export"
        ).status_code
        == 409
    )


def test_reviewer_has_no_tools_and_cannot_create_claims(baseline, checked, monkeypatch):
    from tests.assurance_acceptance.support import observed_draft, tool_turn
    from harness.providers.fake import FakeProvider

    monkeypatch.setenv("PYPSA_GUI_ANSWER_REVIEW_MODE", "required")
    draft = json.loads(observed_draft())
    draft["interpretations"] = [
        {
            "id": "interpretation",
            "text": "The configured load is positive.",
            "block_ids": ["load-input"],
        }
    ]
    provider = FakeProvider(
        [
            tool_turn("get_component", {"component_class": "Load", "name": "L1"}),
            text_turn(json.dumps(draft)),
            text_turn('{"version":1,"verdict":"pass","claim_ids":["interpretation"]}'),
        ]
    )
    frames = checked.run(provider)
    assert approval(frames)["receipt"]["reviewer_status"] == "passed"
    assert "Reviewed interpretation: The configured load is positive." in public_text(
        frames
    )
    assert len(provider.requests) == 3
    assert provider.requests[-1].tools == []
    assert provider.requests[-1].max_tokens == 512
    assert provider.requests[-1].history_stable_anchor is None
    assert len(provider.requests[-1].messages) == 1
    assert "The configured load" not in str(provider.requests[-1].system_blocks)


@pytest.mark.parametrize(
    "fault", ["withhold", "spoof", "unknown_claim", "tool", "no_usage", "bad_source"]
)
def test_reviewer_failures_retain_useful_facts_and_withhold_interpretation(
    baseline, checked, monkeypatch, fault
):
    from tests.assurance_acceptance.support import observed_draft, tool_turn

    monkeypatch.setenv("PYPSA_GUI_ANSWER_REVIEW_MODE", "required")
    draft = json.loads(observed_draft())
    draft["interpretations"] = [
        {"id": "i", "text": "Unreviewed interpretation.", "block_ids": ["load-input"]}
    ]
    verdict = {
        "version": 1,
        "verdict": "withhold" if fault == "withhold" else "pass",
        "claim_ids": ["i"],
    }
    if fault == "spoof":
        verdict["new_facts"] = "Everything is safe at 999 MW."
    if fault == "unknown_claim":
        verdict["claim_ids"] = ["invented"]
    if fault == "bad_source":
        draft["interpretations"][0]["block_ids"] = ["invented"]
    last = (
        tool_turn("update_component", {})
        if fault == "tool"
        else text_turn(json.dumps(verdict))
    )
    if fault == "no_usage":
        last["usage"] = {}
    frames = checked.run(
        [
            tool_turn("get_component", {"component_class": "Load", "name": "L1"}),
            text_turn(json.dumps(draft)),
            last,
        ]
    )
    assert approval(frames)["verdict"] == "limited"
    assert "p_set = 5 MW" in public_text(frames)
    assert "Unreviewed interpretation." not in public_text(frames)
    assert "999 MW" not in public_text(frames)
    assert not approval(frames)["receipt"]["reviewed_claim_ids"]


def test_profile_rollback_and_effect_rollback_fail_closed(solved, checked, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "")
    frames = checked.run(NumericalProvider({}), checked_scope=scope(solved))
    assert approval(frames)["verdict"] == "limited"
    assert (
        approval(frames)["receipt"]["result_slots"][0]["reason"] == "profile_disabled"
    )
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_EFFECTS", "disabled")
    contract = effect_contract(solved)
    frames = checked.run(effect_turns(contract), checked_operation=contract)
    assert approval(frames)["verdict"] == "limited"
    assert solved.network.loads.at["L1", "p_set"] == 5


def test_shadow_observes_without_changing_public_output(baseline, checked, monkeypatch):
    import uuid
    from tests.assurance_acceptance.support import observed_draft, tool_turn, parse_sse

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "shadow")
    provider = checked.install(
        [
            tool_turn("get_component", {"component_class": "Load", "name": "L1"}),
            text_turn(observed_draft()),
        ]
    )
    response = checked.client.post(
        "/api/chat/stream",
        json={
            "session_id": "shadow-" + uuid.uuid4().hex,
            "message": "Read the configured load.",
        },
    )
    assert response.status_code == 200
    frames = parse_sse(response.text)
    assert not any(e == "answer_approved" for e, _ in frames)
    assert '"kind": "observed"' in public_text(frames)
    assert "Strict checked-answer mode" not in str(provider.requests[0].system_blocks)
    directory = assurance_records._path(
        assurance_records.actor_id(), str(uuid.uuid4())
    ).parent
    observations = [
        json.loads(p.read_text())["record"]
        for p in directory.glob("*.json")
        if json.loads(p.read_text())["record"].get("record_kind") == "shadow"
    ]
    assert len(observations) == 1
    assert observations[0]["candidate_verdict"] == "deterministic_match"
    assert observations[0]["checked_blocks"] == 1
    assert "text" not in observations[0]


def test_shadow_scope_failure_preserves_ordinary_response(
    baseline, checked, monkeypatch
):
    import uuid
    from tests.assurance_acceptance.support import parse_sse

    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_MODE", "shadow")
    provider = checked.install([text_turn("Please provide a supported price year.")])
    response = checked.client.post(
        "/api/chat/stream",
        json={
            "session_id": "shadow-" + uuid.uuid4().hex,
            "message": "Show me total cost for generator G1; price year 0020; annual fixed cost fraction 1",
        },
    )
    frames = parse_sse(response.text)
    assert response.status_code == 200
    assert public_text(frames) == "Please provide a supported price year."
    assert len(provider.requests) == 1
    assert not any(event in {"answer_approved", "stream_error"} for event, _ in frames)


def test_cost_grammar_is_useful_without_client_scope(solved, checked):
    frames = checked.run(
        NumericalProvider({"total_cost": 230}),
        message="Show me total cost for generator G1; price year 2026; annual fixed cost fraction 1",
    )
    assert approval(frames)["verdict"] == "passed"
    assert "230 EUR" in public_text(frames)
    assert approval(frames)["receipt"]["request_scope"]["metrics"] == ["total_cost"]


def test_ambiguous_cost_request_disposes_requested_slot(solved, checked):
    frames = checked.run(
        NumericalProvider({}), message="Show me total cost for generator G1"
    )
    assert approval(frames)["verdict"] == "limited"
    assert "explicit_cost_basis_required" in public_text(frames)
    assert approval(frames)["receipt"]["result_slots"][0]["metric"] == "total_cost"


def test_navigation_stream_issues_ack_and_never_claims_unconfirmed_completion(
    baseline, checked
):
    from tests.assurance_acceptance.support import tool_turn

    frames = checked.run(
        [
            tool_turn(
                "ui_open_panel", {"panel_id": "results", "results_tab": "dispatch"}
            ),
            text_turn('{"schema_version":1,"blocks":[]}'),
        ],
        binding_generation=7,
    )
    events = [d for e, d in frames if e == "ui_event"]
    assert len(events) == 1
    assert events[0]["acknowledgement"]["binding_generation"] == 7
    assert events[0]["tool_use_id"] == "read-load"
    assert approval(frames)["verdict"] == "limited"
    assert "awaiting mounted-view acknowledgement" in public_text(frames)
    body = {
        k: events[0]["acknowledgement"][k]
        for k in ("version", "action_id", "turn_id", "binding_generation", "target")
    }
    assert checked.client.post("/api/chat/ui-ack", json=body).status_code == 200


def test_signed_comparison_uses_identical_basis_and_baseline_denominator(
    solved, checked
):
    solved.network.generators.at["G1", "capital_cost"] = 4.5
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    base_values = {
        **VALUES,
        "annual_fixed_cost": 90,
        "allocated_fixed_cost": 90,
        "total_cost": 260,
    }
    base_frames = checked.run(
        NumericalProvider(base_values), checked_scope=scope(solved, retain=True)
    )
    eid = approval(base_frames)["receipt"]["evidence_ids"][0]
    solved.network.generators.at["G1", "capital_cost"] = 3
    workflow_tools._record_solve_inputs(
        solved.network, solved.solver_state["solver_config"]
    )
    request_scope = scope(
        solved,
        metrics=["total_cost", "cost_delta", "cost_change_percent"],
        comparison_evidence_id=eid,
    )
    values = {
        "total_cost": 230,
        "cost_delta": -30,
        "cost_change_percent": -30 / 260 * 100,
    }
    frames = checked.run(NumericalProvider(values), checked_scope=request_scope)
    assert approval(frames)["verdict"] == "passed"
    assert "candidate minus baseline" in public_text(frames)
    assert "-30 EUR" in public_text(frames)
    bad_scope = {
        **request_scope,
        "cost_basis": {**request_scope["cost_basis"], "price_year": 2025},
    }
    bad = checked.run(NumericalProvider({}), checked_scope=bad_scope)
    assert approval(bad)["verdict"] == "limited"


def test_reconciliation_requires_actual_finished_effect_and_no_client_verdict(
    baseline, checked, monkeypatch
):
    from harness.session import ChatSession
    from services import chat_tools

    monkeypatch.setattr(ChatSession, "wait_for_decision", lambda *args: "approve")
    contract = effect_contract(baseline[0])

    def partial(**args):
        baseline[0].network.loads.at["L1", "p_set"] = 8
        raise RuntimeError("checkpoint after complete effect failed")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_component", partial)
    frames = checked.run(effect_turns(contract), checked_operation=contract)
    assert approval(frames)["verdict"] == "limited"
    record = checked.client.get(
        "/api/chat/operations/" + contract["operation_id"]
    ).json()
    route = "/api/chat/operations/" + contract["operation_id"] + "/reconcile"
    assert (
        checked.client.post(
            route, json={"version": 1, "outcome": "completed"}
        ).status_code
        == 422
    )
    assert (
        checked.client.post(
            route, json={"version": 1, "contract_sha256": "forged"}
        ).status_code
        == 409
    )
    reconciled = checked.client.post(
        route, json={"version": 1, "contract_sha256": record["contract_sha256"]}
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["outcome"] == "completed"
    assert reconciled.json()["verification"] == "independent_readback"


def test_late_effect_blocks_retry_until_real_worker_finishes(
    baseline, checked, monkeypatch
):
    import threading
    import time
    from harness import budget
    from harness.session import ChatSession
    from services import chat_tools
    from tests.assurance_acceptance.support import parse_sse

    monkeypatch.setattr(ChatSession, "wait_for_decision", lambda *args: "approve")
    monkeypatch.setattr(budget, "PER_TOOL_TIMEOUT_SECONDS", 0.5)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def late(**args):
        calls.append(args)
        entered.set()
        assert release.wait(10)
        baseline[0].network.loads.at["L1", "p_set"] = 8
        return {"ok": True}

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_component", late)
    contract = effect_contract(baseline[0])
    try:
        response = checked.request(effect_turns(contract), checked_operation=contract)
        frames = parse_sse(response.text)
        assert entered.wait(1)
        assert any(
            e == "tool_error" and d["error_kind"] == "tool_timeout" for e, d in frames
        )
        assert not any(e == "answer_approved" for e, _ in frames)
        record = checked.client.get(
            "/api/chat/operations/" + contract["operation_id"]
        ).json()
        assert record["outcome"] == "running"
        retry = checked.request(effect_turns(contract), checked_operation=contract)
        assert not any(e == "answer_approved" for e, _ in parse_sse(retry.text))
        assert len(calls) == 1
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        record = checked.client.get(
            "/api/chat/operations/" + contract["operation_id"]
        ).json()
        if record["outcome"] == "completed":
            break
        time.sleep(0.01)
    assert record["outcome"] == "completed"
    assert len(calls) == 1


def test_in_bounds_but_unbalanced_network_output_cannot_supply_cost(solved, checked):
    solved.network.generators_t.p.iloc[1, 0] = (
        6  # Within 20 MW capacity, but demand is 7 MW.
    )
    frames = checked.run(
        NumericalProvider({"annual_fixed_cost": 60, "allocated_fixed_cost": 60}),
        checked_scope=scope(
            solved, profile="network-linear-cost/1", entity_id="network"
        ),
    )
    assert approval(frames)["verdict"] == "limited"
    slots = approval(frames)["receipt"]["result_slots"]
    assert (
        next(s for s in slots if s["metric"] == "dispatch_energy")["valid_snapshots"]
        == 1
    )
    assert (
        next(s for s in slots if s["metric"] == "total_cost")["disposition"]
        == "unavailable"
    )
    assert "230 EUR" not in public_text(frames)


@pytest.mark.parametrize(
    "interpretation", ["Total cost is 999 EUR.", "Total cost is nine hundred euros."]
)
def test_reviewer_cannot_approve_untyped_quantitative_claims(
    baseline, checked, monkeypatch, interpretation
):
    from tests.assurance_acceptance.support import observed_draft, tool_turn

    monkeypatch.setenv("PYPSA_GUI_ANSWER_REVIEW_MODE", "required")
    draft = json.loads(observed_draft())
    draft["interpretations"] = [
        {"id": "i", "text": interpretation, "block_ids": ["load-input"]}
    ]
    frames = checked.run(
        [
            tool_turn("get_component", {"component_class": "Load", "name": "L1"}),
            text_turn(json.dumps(draft)),
            text_turn('{"version":1,"verdict":"pass","claim_ids":["i"]}'),
        ]
    )
    assert approval(frames)["verdict"] == "limited"
    assert "p_set = 5 MW" in public_text(frames)
    assert interpretation not in public_text(frames)
    assert len(checked.provider.requests) == 2


@pytest.mark.parametrize("draft", ["null", "true", "[]", '"not a draft"'])
def test_nonobject_draft_receives_an_honest_limited_answer(baseline, checked, draft):
    frames = checked.run([text_turn(draft)])
    assert approval(frames)["verdict"] == "limited"
    assert not approval(frames)["receipt"]["checked_block_ids"]
