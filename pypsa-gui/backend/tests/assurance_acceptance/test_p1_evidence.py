"""A1: real configured inputs, scoped evidence and anonymous refusal."""

import json
import math
import uuid

import pytest
from fastapi.testclient import TestClient

import main
from services import chat_tools
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.support import (
    approval,
    public_text,
    text_turn,
    tool_turn,
)
from tests.conftest import attach_session


def test_real_input_fact_has_resolvable_project_entity_unit_evidence(baseline, harness):
    approved = approval(harness.load_fact())
    assert approved["verdict"] == "passed"
    ids = approved["receipt"]["evidence_ids"]
    assert ids, "An observed fact has no server-owned evidence"
    response = harness.client.get(f"/api/chat/evidence/{ids[0]}")
    assert response.status_code == 200
    record = response.json()
    assert record["project_id"] == baseline[1]
    assert record["source_kind"] == "configured_input"
    assert any(
        fact["entity_id"] == "L1"
        and fact["metric"] == "p_set"
        and fact["value"] == 5
        and fact["unit"] == "MW"
        for fact in record["facts"]
    )


def test_another_organization_cannot_read_an_approved_fact(
    baseline, harness, other_org_client
):
    approved = approval(harness.load_fact())
    for path in (
        f"/api/chat/answers/{approved['answer_id']}",
        f"/api/chat/evidence/{approved['receipt']['evidence_ids'][0]}",
    ):
        response = other_org_client.get(path)
        assert response.status_code == 404
        assert "L1" not in response.text and "p_set" not in response.text


@pytest.mark.parametrize(
    "path", ["/api/chat/answers/unknown", "/api/chat/evidence/unknown"]
)
def test_anonymous_requests_are_refused_by_global_auth(anon_client, path):
    assert anon_client.get(path).status_code == 401


def test_evidence_capture_is_attached_to_real_tool_result(baseline, harness):
    frames = harness.load_fact()
    result = next(data["result"] for event, data in frames if event == "tool_result")
    assert result["p_set"] == 5
    assert result.get("_assurance", {}).get("evidence_ids"), (
        "Raw result lacks captured evidence IDs"
    )


def page_draft(entity="L1", value=5, path="/L1/p_set"):
    return json.dumps(
        {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "page-input",
                    "kind": "observed",
                    "entity_id": entity,
                    "metric": "p_set",
                    "value": value,
                    "unit": "MW",
                    "source": {"tool_use_id": "list-loads", "path": path},
                }
            ],
        }
    )


def list_loads(harness, draft, **args):
    return harness.run(
        [
            tool_turn(
                "list_components",
                {"component_class": "Load", **args},
                call_id="list-loads",
            ),
            text_turn(draft),
            text_turn(draft),
        ]
    )


def test_listed_input_row_has_page_scoped_project_evidence(baseline, harness):
    approved = approval(list_loads(harness, page_draft()))
    assert approved["verdict"] == "passed"
    ids = approved["receipt"]["evidence_ids"]
    record = harness.client.get(f"/api/chat/evidence/{ids[0]}").json()
    assert record["project_id"] == baseline[1]
    assert record["source_kind"] == "configured_input"
    # One page of one class; a page is never a class-wide aggregate.
    assert record["scope"] == {
        "component_class": "Load",
        "kind": "static_page",
        "offset": 0,
        "returned": 1,
        "total_count": 1,
        "has_more": False,
    }
    assert {
        "path": "/L1/p_set",
        "entity_id": "L1",
        "metric": "p_set",
        "value": 5,
        "unit": "MW",
    } in record["facts"]


def page_result(frames):
    return next(
        data["result"]
        for event, data in frames
        if event == "tool_result" and data["tool_use_id"] == "list-loads"
    )


def page_evidence_ids(frames):
    return page_result(frames).get("_assurance", {}).get("evidence_ids", [])


def add_loads(*names, p_set=7):
    network = PyPSAService.get_active_context().network
    for name in names:
        network.add("Load", name, bus="B1", p_set=p_set)


def wrap_handler(monkeypatch, after):
    """Run the real list_components, then `after(result)` before capture."""
    real = chat_tools.DISPATCHERS["list_components"]

    def handler(**kwargs):
        result = real(**kwargs)
        after(result)
        return result

    monkeypatch.setitem(chat_tools.DISPATCHERS, "list_components", handler)


def test_page_answer_names_its_rows_and_is_never_class_wide(baseline, harness):
    add_loads("L2")
    frames = list_loads(
        harness, page_draft("L2", 7, "/L2/p_set"), offset=1, limit=1
    )
    assert approval(frames)["verdict"] == "passed"
    text = public_text(frames)
    assert "rows 2..2 of 2" in text and "not the whole class" in text
    record = harness.client.get(
        f"/api/chat/evidence/{page_evidence_ids(frames)[0]}"
    ).json()
    assert record["scope"]["offset"] == 1 and record["scope"]["returned"] == 1
    assert {f["entity_id"] for f in record["facts"]} == {"L2"}


def test_claim_on_a_row_off_the_page_is_refused(baseline, harness):
    add_loads("L2")
    # L1 exists but the page shows only L2; neither entity nor position aliases.
    for draft in (
        page_draft("L1", 5, "/L1/p_set"),
        page_draft("L2", 7, "/0/p_set"),
        page_draft("L1", 5, "/L2/p_set"),
    ):
        frames = list_loads(harness, draft, offset=1, limit=1)
        assert approval(frames)["verdict"] == "limited"
        assert "L1" not in public_text(frames)


@pytest.mark.parametrize(
    "args",
    [{"limit": 0}, {"limit": 1001}, {"offset": -1}, {"offset": "1"}, {"limit": True}],
)
def test_out_of_range_page_is_refused_before_the_handler(
    baseline, harness, monkeypatch, args
):
    calls = []
    monkeypatch.setitem(
        chat_tools.DISPATCHERS, "list_components", lambda **kw: calls.append(kw)
    )
    frames = list_loads(harness, page_draft(), **args)
    assert not calls
    assert any(
        event == "tool_error" and data["error_kind"] == "capability_unsupported"
        for event, data in frames
    )
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize("offset", [1, 5])
def test_offset_past_the_end_yields_no_evidence_and_no_claim(
    baseline, harness, offset
):
    frames = list_loads(harness, page_draft(), offset=offset)
    assert "items" not in page_result(frames)
    assert page_evidence_ids(frames) == []
    assert approval(frames)["verdict"] == "limited"
    text = public_text(frames)
    assert "L1" not in text and "5 MW" not in text


def test_input_edit_after_capture_invalidates_page_evidence(baseline, harness):
    evidence_id = page_evidence_ids(list_loads(harness, page_draft()))[0]
    # q_set is not the claimed fact; the class source digest still moves.
    PyPSAService.get_active_context().network.loads.at["L1", "q_set"] = 0.5
    response = harness.client.get(f"/api/chat/evidence/{evidence_id}")
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "evidence_unavailable"
    assert "p_set" not in response.text


def test_project_switch_after_capture_invalidates_page_evidence(baseline, harness):
    evidence_id = page_evidence_ids(list_loads(harness, page_draft()))[0]
    for path in ("/api/projects/Another Baseline", "/api/projects/Another Baseline/activate"):
        assert harness.client.post(path).status_code == 200
    response = harness.client.get(f"/api/chat/evidence/{evidence_id}")
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "evidence_unavailable"
    assert "p_set" not in response.text


def test_edit_between_handler_and_capture_yields_no_evidence(
    baseline, harness, monkeypatch
):
    def edit(result):
        PyPSAService.get_active_context().network.loads.at["L1", "q_set"] = 0.5

    wrap_handler(monkeypatch, edit)
    frames = list_loads(harness, page_draft())
    assert page_evidence_ids(frames) == []
    assert approval(frames)["verdict"] == "limited"


@pytest.mark.parametrize(
    ("shown", "certifies"),
    [
        (5, True),
        (5.0, True),
        ("5", False),
        (True, False),
        (None, False),
        (5.5, False),
        (math.nan, False),
        (math.inf, False),
    ],
)
def test_shown_value_certifies_only_on_exact_numeric_equality(
    baseline, harness, monkeypatch, shown, certifies
):
    def show(result):
        result["items"][0]["p_set"] = shown

    wrap_handler(monkeypatch, show)
    frames = list_loads(harness, page_draft())
    ids = page_evidence_ids(frames)
    if not ids:
        assert not certifies
        return
    record = harness.client.get(f"/api/chat/evidence/{ids[0]}").json()
    paths = {f["path"]: f["value"] for f in record["facts"]}
    assert ("/L1/p_set" in paths) is certifies
    if certifies:
        assert paths["/L1/p_set"] == 5.0 and type(paths["/L1/p_set"]) is float


@pytest.mark.parametrize("raw", [math.nan, math.inf, -math.inf])
def test_non_finite_raw_input_never_certifies(baseline, harness, monkeypatch, raw):
    network = PyPSAService.get_active_context().network
    network.loads.at["L1", "p_set"] = raw

    def show(result):
        # Even a handler that echoes the raw value cannot certify it.
        result["items"][0]["p_set"] = raw

    wrap_handler(monkeypatch, show)
    frames = list_loads(harness, page_draft())
    for evidence_id in page_evidence_ids(frames):
        record = harness.client.get(f"/api/chat/evidence/{evidence_id}").json()
        assert all(f["metric"] != "p_set" for f in record["facts"])
    assert approval(frames)["verdict"] == "limited"


def test_names_with_pointer_characters_resolve_only_when_escaped(baseline, harness):
    PyPSAService.get_active_context().network.remove("Load", "L1")
    add_loads("a/b~c", "a", "b", "b~c")
    escaped = page_draft("a/b~c", 7, "/a~1b~0c/p_set")
    assert approval(list_loads(harness, escaped))["verdict"] == "passed"
    for draft in (
        page_draft("a/b~c", 7, "/a/b~c/p_set"),
        page_draft("a", 7, "/a~1b~0c/p_set"),
        page_draft("b", 7, "/a~1b~0c/p_set"),
        page_draft("b~c", 7, "/a~1b~0c/p_set"),
    ):
        assert approval(list_loads(harness, draft))["verdict"] == "limited"


def test_largest_page_stays_under_the_stored_evidence_cap(baseline, harness):
    # Long names make the widest rows; the handler packs pages by size, and
    # the stored page record must stay under save()'s 128 KiB cap.
    add_loads(*(f"Load-{i:04d}-" + "x" * 120 for i in range(1, 1000)))
    frames = list_loads(harness, page_draft(), limit=chat_tools.MAX_PAGE_SIZE)
    result = page_result(frames)
    record = harness.client.get(
        f"/api/chat/evidence/{page_evidence_ids(frames)[0]}"
    ).json()
    assert record["scope"]["returned"] == result["returned"]
    assert record["scope"]["total_count"] == 1000
    assert len(record["facts"]) == 2 * record["scope"]["returned"]
    assert len(json.dumps(record).encode()) < 128 * 1024


def test_repeated_identical_page_calls_each_keep_evidence(baseline, harness):
    call = {"component_class": "Load"}
    frames = harness.run(
        [
            tool_turn("list_components", call, call_id="list-loads"),
            tool_turn("list_components", call, call_id="list-again"),
            text_turn(page_draft()),
            text_turn(page_draft()),
        ]
    )
    results = {
        data["tool_use_id"]: data["result"]
        for event, data in frames
        if event == "tool_result"
    }
    assert all(r["_assurance"]["evidence_ids"] for r in results.values())
    assert approval(frames)["verdict"] == "passed"


@pytest.fixture
def same_org_client(_auth_db, seeded_identity):
    from datetime import UTC, datetime

    from db.models import OrgMembership, User

    _engine, session_local = _auth_db
    with session_local() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"colleague-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="unused",
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=UTC),
        )
        db.add(user)
        db.flush()
        db.add(
            OrgMembership(
                id=uuid.uuid4(),
                user_id=user.id,
                org_id=seeded_identity["org_id"],
                role="admin",
            )
        )
        db.commit()
        user_id = user.id
    with TestClient(main.app) as c:
        yield attach_session(c, session_local, user_id)


def test_page_evidence_is_owner_scoped(
    baseline, harness, other_org_client, same_org_client
):
    evidence_id = page_evidence_ids(list_loads(harness, page_draft()))[0]
    for client in (other_org_client, same_org_client):
        for path in (
            f"/api/chat/evidence/{evidence_id}",
            f"/api/chat/evidence/{uuid.uuid4()}",
        ):
            response = client.get(path)
            assert response.status_code == 404
            assert "L1" not in response.text and "p_set" not in response.text


def test_page_evidence_does_not_compute_the_network_fingerprint(
    baseline, harness, monkeypatch
):
    from services import workflow_tools

    def slow(*args, **kwargs):
        raise AssertionError("page evidence must not copy the whole network")

    monkeypatch.setattr(workflow_tools, "_network_input_fingerprint", slow)
    frames = list_loads(harness, page_draft())
    assert approval(frames)["verdict"] == "passed"
    evidence_id = page_evidence_ids(frames)[0]
    assert harness.client.get(f"/api/chat/evidence/{evidence_id}").status_code == 200
