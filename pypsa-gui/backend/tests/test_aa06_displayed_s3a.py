"""Assistant-assurance 06, A5.2 S3a: Energy flow, Load shape and Curtailment
prove the data they displayed.

Each view calls its tab's existing route function and records the period,
window and week it was called with; the ACK re-reads exactly that view.
Expected figures come from the hand network below, never from the adapter.
"""

from __future__ import annotations

import copy
import logging

import pandas as pd
import pypsa
import pytest

from harness import displayed_adapters, navigation
from harness.navigation import OrdinaryTurn
from harness.session import ChatSession
from routers.results_displayed import canonical
from services import assurance_records, chat_tools
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

TABS = ["flows", "loadshape", "curtailment"]


# ── Hand network ────────────────────────────────────────────────────────────
# Load 6 MW for 3 h. PV (10 MW, free) is available at 100 %, 50 %, 0 %; the
# grid (50 €/MWh) fills the rest through a lossless tie.
#   PV used      6, 5, 0  -> 11 MWh      grid import 0, 1, 6 -> 7 MWh
#   PV available 10, 5, 0 -> curtailed 4, 0, 0 MW               load 18 MWh
# Window from 1 to 2: PV 5 MWh, grid 7 MWh, curtailed 0, 0.


def _site(load=6.0):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-05", periods=3, freq="h"))
    n.add("Bus", "site")
    n.add("Bus", "grid")
    n.add("Carrier", ["solar", "grid"])
    n.add(
        "Generator",
        "pv",
        bus="site",
        carrier="solar",
        p_nom=10,
        p_max_pu=[1.0, 0.5, 0.0],
        marginal_cost=0,
    )
    n.add(
        "Generator",
        "grid_supply",
        bus="grid",
        carrier="grid",
        p_nom=100,
        marginal_cost=50,
    )
    n.add("Link", "tie", bus0="grid", bus1="site", p_nom=100, efficiency=1.0)
    n.add("Load", "demand", bus="site", p_set=load)
    logging.disable(logging.CRITICAL)
    try:
        assert n.optimize(solver_name="highs")[0] == "ok"
    finally:
        logging.disable(logging.NOTSET)
    return n


@pytest.fixture
def solved(baseline):  # noqa: F811
    ctx, _project_id, _root = baseline
    ctx.network = _site()
    ctx.solver_state.update(status="completed", condition="optimal")
    return ctx


def _turn(ctx, generation=3):
    owner = assurance_records.actor_id()
    turn = OrdinaryTurn(ChatSession(owner_user_id=owner), ctx, generation)
    navigation.begin_turn(turn, generation)
    return turn


def _issue(ctx, tab, generation=3):
    turn = _turn(ctx, generation)
    return turn, navigation.issue(
        turn, {"kind": "navigate", "panel_id": "results", "results_tab": tab}
    )


def _ack_body(ack):
    keys = (
        "version",
        "action_id",
        "turn_id",
        "binding_generation",
        "target",
        "source_binding",
    )
    return {k: ack[k] for k in keys}


def _outcome(client, action_id):
    return client.get(f"/api/chat/navigation/{action_id}").json()["outcome"]


def _rows(flows):
    return {(r["source"], r["destination"]): r["mwh"] for r in flows["rows"]}


# ── Hand oracle and byte-equal parity with the old routes ───────────────────


def test_flows_view_is_the_old_route_byte_equal_and_the_hand_oracle(client, solved):
    body = client.get("/api/results/displayed/flows").json()
    old = client.get("/api/results/energy_flows").json()
    assert canonical(body["data"]) == canonical(old)
    assert _rows(body["data"]) == {
        ("solar", "demand"): 11.0,
        ("Grid import", "demand"): 7.0,
    }
    windowed = client.get(
        "/api/results/displayed/flows", params={"from": 1, "to": 2}
    ).json()
    assert canonical(windowed["data"]) == canonical(
        client.get("/api/results/energy_flows", params={"from": 1, "to": 2}).json()
    )
    assert _rows(windowed["data"]) == {
        ("solar", "demand"): 5.0,
        ("Grid import", "demand"): 7.0,
    }


def test_loadshape_view_is_the_old_route_byte_equal_and_the_hand_oracle(client, solved):
    body = client.get("/api/results/displayed/loadshape").json()
    assert canonical(body["data"]) == canonical(
        client.get("/api/results/load_shape").json()
    )
    annual = body["data"]["annual"][0]
    assert (
        annual["load_served"],
        annual["solar"],
        annual["grid"],
        annual["excess"],
    ) == (
        18.0,
        11.0,
        7.0,
        4.0,
    )
    params = {"from": 1, "to": 2, "week": 0}
    assert canonical(
        client.get("/api/results/displayed/loadshape", params=params).json()["data"]
    ) == canonical(client.get("/api/results/load_shape", params=params).json())


@pytest.mark.parametrize("window", [None, {"from": 1, "to": 2}])
def test_curtailment_view_is_both_old_routes_byte_equal_per_part(
    client, solved, window
):
    body = client.get("/api/results/displayed/curtailment", params=window).json()
    assert set(body["data"]) == {"curtailment", "generators"}
    assert canonical(body["data"]["curtailment"]) == canonical(
        client.get("/api/results/curtailment", params=window).json()
    )
    assert canonical(body["data"]["generators"]) == canonical(
        client.get("/api/results/generators", params=window).json()
    )
    curtailed = [row[0] for row in body["data"]["curtailment"]["data"]]
    assert curtailed == ([4.0, 0.0, 0.0] if window is None else [0.0, 0.0])


def test_a_generators_204_leaves_that_part_null(client, solved, monkeypatch):
    from fastapi import Response

    from routers import results

    monkeypatch.setattr(
        results, "get_generator_results", lambda *_a: Response(status_code=204)
    )
    body = client.get("/api/results/displayed/curtailment").json()
    assert body["data"]["generators"] is None
    assert body["data"]["curtailment"]["columns"] == ["pv"]


@pytest.mark.parametrize("tab", TABS)
def test_an_unsolved_project_answers_204_like_the_old_route(client, baseline, tab):  # noqa: F811
    assert client.get(f"/api/results/displayed/{tab}").status_code == 204


# ── The binding records the call; the ACK re-reads exactly it ───────────────


@pytest.mark.parametrize(
    "tab,params,recorded",
    [
        ("flows", {}, {"period": None, "window_from": None, "window_to": None}),
        (
            "flows",
            {"from": 1, "to": 2},
            {"period": None, "window_from": 1, "window_to": 2},
        ),
        (
            "loadshape",
            {"week": 0},
            {"period": None, "window_from": None, "window_to": None, "week": 0},
        ),
        ("curtailment", {"from": 0, "to": 1}, {"window_from": 0, "window_to": 1}),
    ],
)
def test_the_binding_records_the_arguments_the_route_was_called_with(
    client, solved, tab, params, recorded
):
    binding = client.get(f"/api/results/displayed/{tab}", params=params).json()[
        "binding"
    ]
    assert {k: binding[k] for k in recorded} == recorded
    assert set(binding) == displayed_adapters.ADAPTERS[tab].binding_keys
    # The ACK's re-read of this binding is this same view, not the default one.
    assert displayed_adapters.ADAPTERS[tab].reread(binding)["binding"] == binding


@pytest.mark.parametrize("tab", TABS)
def test_ack_of_the_rendered_default_binding_is_displayed_data(client, solved, tab):
    _, ack = _issue(solved, tab)
    assert (
        ack["source_binding"]
        == client.get(f"/api/results/displayed/{tab}").json()["binding"]
    )
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "browser_displayed_data"


def _resolve(ctx):
    # The same inputs, other figures (another solver path): only results change.
    ctx.network.generators_t.p.loc[:, "grid_supply"] += 1.0
    ctx.network.links_t.p1.loc[:, "tie"] -= 1.0
    ctx.network.links_t.p0.loc[:, "tie"] += 1.0


def _input_edit(ctx):
    ctx.network.loads.loc["demand", "p_set"] = 5.0


@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize("change", [_resolve, _input_edit])
def test_ack_after_a_resolve_or_an_input_edit_is_refused(client, solved, tab, change):
    _, ack = _issue(solved, tab)
    before = ack["source_binding"]["data_sha256"]
    change(solved)
    if change is _resolve:  # the fixture change moves this tab's payload
        assert (
            client.get(f"/api/results/displayed/{tab}").json()["binding"]["data_sha256"]
            != before
        )
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "ui_ack_mismatch"
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize("tab", TABS)
def test_ack_after_a_project_switch_is_refused(client, solved, tab):
    _, ack = _issue(solved, tab)
    original = solved.project_uuid
    solved.project_uuid = "00000000-0000-4000-8000-000000000001"
    try:
        assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 409
    finally:
        solved.project_uuid = original
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize(
    "tab,field,value",
    [
        ("flows", "window_from", 1),
        ("flows", "window_to", 1),
        ("flows", "period", 2026),
        ("loadshape", "week", 1),
        ("curtailment", "window_from", 1),
        ("curtailment", "read_sha256", "0" * 64),
    ],
)
def test_ack_of_another_window_period_or_week_is_refused(
    client, solved, tab, field, value
):
    """The browser showed another window than the one the turn opened: 409."""
    _, ack = _issue(solved, tab)
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"][field] = value
    assert client.post("/api/chat/ui-ack", json=body).status_code == 409
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize(
    "tab,change",
    [
        ("loadshape", {"source": "ac_pf"}),  # a source the tab does not offer
        ("flows", {"source": "lopf"}),
        ("curtailment", {"source": "lopf"}),
        ("flows", {"window_from": 2, "window_to": 1}),  # starts after it ends
        ("flows", {"window_from": "0"}),  # not an index
        ("flows", {"window_from": -1}),
        ("loadshape", {"week": 1.5}),
        ("curtailment", {"window_from": True}),
        ("curtailment", {"run_id": "run-2"}),  # an extra key, as in S2
    ],
)
def test_a_malformed_or_foreign_binding_is_refused_422(client, solved, tab, change):
    _, ack = _issue(solved, tab)
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"].update(change)
    assert client.post("/api/chat/ui-ack", json=body).status_code == 422
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize(
    "change",
    [
        {"window_from": 0},  # index 0 is a bound, not a missing one
        {"window_from": 0, "window_to": 0},  # a one-snapshot window
        {"window_from": 2, "window_to": 2},
        {"window_to": 0},
    ],
)
def test_an_index_of_0_or_a_window_with_from_equal_to_is_a_valid_binding(
    client, solved, tab, change
):
    binding = client.get(f"/api/results/displayed/{tab}").json()["binding"]
    adapter = displayed_adapters.ADAPTERS[tab]
    assert displayed_adapters.valid_binding(adapter, {**binding, **change})
    if "period" in binding:
        assert displayed_adapters.valid_binding(adapter, {**binding, "period": 0})


def test_a_missing_index_key_is_refused_422(client, solved):
    _, ack = _issue(solved, "loadshape")
    body = copy.deepcopy(_ack_body(ack))
    del body["source_binding"]["week"]
    assert client.post("/api/chat/ui-ack", json=body).status_code == 422


@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize(
    "params", [{"from": 2, "to": 1}, {"from": -1}, {"to": "x"}, {"source": "ac_pf"}]
)
def test_the_view_refuses_a_malformed_window_or_a_source_it_lacks(
    client, solved, tab, params
):
    response = client.get(f"/api/results/displayed/{tab}", params=params)
    assert response.status_code == 422
    if "source" in params:
        # Refused, never ignored (GK S3 condition 2).
        assert response.json()["detail"]["error_kind"] == "source_not_offered"


@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize(
    "params,recorded",
    [
        ({"from": 1}, {"window_from": 1, "window_to": None}),
        ({"to": 1}, {"window_from": None, "window_to": 1}),
        ({"from": 1, "to": 1}, {"window_from": 1, "window_to": 1}),
        ({"from": 0, "to": 0}, {"window_from": 0, "window_to": 0}),
    ],
)
def test_a_half_or_one_snapshot_window_is_served_and_bound(
    client, solved, tab, params, recorded
):
    """Only `?from=`, only `?to=`, or from == to is a window, not a 422 or a 500."""
    response = client.get(f"/api/results/displayed/{tab}", params=params)
    assert response.status_code == 200, response.text
    binding = response.json()["binding"]
    assert {k: binding[k] for k in recorded} == recorded
    assert displayed_adapters.valid_binding(displayed_adapters.ADAPTERS[tab], binding)


# ── One guard per bound read (GK B 72, condition 1) ─────────────────────────


@pytest.mark.parametrize(
    "tab,routes",
    [
        ("flows", ["get_energy_flows"]),
        ("loadshape", ["get_load_shape"]),
        ("curtailment", ["get_curtailment", "get_generator_results"]),
    ],
)
def test_every_part_is_read_inside_one_network_guard(
    client, solved, monkeypatch, tab, routes
):
    import contextlib

    import harness.durable as durable
    from routers import results

    real_guard = durable.network_guard
    state = {"depth": 0, "entered": 0}
    seen = []

    @contextlib.contextmanager
    def counting_guard(*a, **k):
        state["entered"] += 1
        state["depth"] += 1
        try:
            with real_guard(*a, **k):
                yield
        finally:
            state["depth"] -= 1

    monkeypatch.setattr(durable, "network_guard", counting_guard)
    for name in routes:
        real = getattr(results, name)

        def spy(*a, _real=real, _name=name):
            seen.append((_name, state["depth"]))
            return _real(*a)

        monkeypatch.setattr(results, name, spy)
    assert client.get(f"/api/results/displayed/{tab}").status_code == 200
    assert state["entered"] == 1
    assert seen == [(name, 1) for name in routes]


def test_a_resolve_between_the_two_curtailment_parts_cannot_split_the_solve(
    client, solved, monkeypatch
):
    """A solve that lands between the parts must wait for the guard; here it is
    simulated by changing the network after the first part's read: the second
    part then disagrees, and so does every later re-read, so the ACK refuses."""
    from routers import results

    _, ack = _issue(solved, "curtailment")
    real = results.get_curtailment

    def then_resolve(*a):
        out = real(*a)
        _resolve(solved)
        return out

    monkeypatch.setattr(results, "get_curtailment", then_resolve)
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 409


# ── Per-family guards: fingerprint, NaN, lock, outsider ─────────────────────


@pytest.mark.parametrize("tab", TABS)
def test_the_binding_input_hash_is_the_plain_network_fingerprint(client, solved, tab):
    from services import workflow_tools

    served = client.get(f"/api/results/displayed/{tab}").json()["binding"]
    assert served["input_sha256"] == workflow_tools._network_input_fingerprint(solved)
    _input_edit(solved)
    again = client.get(f"/api/results/displayed/{tab}").json()["binding"]
    assert again["input_sha256"] == workflow_tools._network_input_fingerprint(solved)
    assert again["input_sha256"] != served["input_sha256"]


@pytest.mark.parametrize(
    "tab,route",
    [
        ("flows", "get_energy_flows"),
        ("loadshape", "get_load_shape"),
        ("curtailment", "get_curtailment"),
    ],
)
def test_a_non_finite_value_is_null_in_the_data_its_hash_and_the_ack(
    client, solved, monkeypatch, tab, route
):
    import hashlib

    from routers import results

    payload = {
        "status": "ok",
        "plants": {},
        "total": float("nan"),
        "peak": float("-inf"),
    }
    monkeypatch.setattr(results, route, lambda *_a: payload)
    body = client.get(f"/api/results/displayed/{tab}").json()
    data = body["data"][tab] if tab == "curtailment" else body["data"]
    assert data["total"] is None and data["peak"] is None
    encoded = canonical(body["data"])
    assert "NaN" not in encoded and "Infinity" not in encoded
    assert (
        body["binding"]["data_sha256"] == hashlib.sha256(encoded.encode()).hexdigest()
    )
    _, ack = _issue(solved, tab)
    assert ack["source_binding"] == body["binding"]
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "browser_displayed_data"


@pytest.mark.parametrize("tab", TABS)
def test_a_held_network_lock_answers_409(client, solved, tab):
    import threading

    from services.pypsa_service import PyPSAService

    lock = PyPSAService.get_lock()
    held, release = threading.Event(), threading.Event()

    def hold():
        with lock:
            held.set()
            release.wait(10)

    worker = threading.Thread(target=hold)
    worker.start()
    assert held.wait(5)
    try:
        response = client.get(f"/api/results/displayed/{tab}")
    finally:
        release.set()
        worker.join()
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "assurance_context_changed"


@pytest.mark.parametrize("tab", [*TABS, "emissions"])
def test_a_reader_without_access_to_the_open_project_gets_404(_auth_db, solved, tab):
    from fastapi.testclient import TestClient

    import main
    from tests.conftest import attach_session
    from tests.test_chat_session_ownership import _other_org_user

    uid, session_local = _other_org_user(_auth_db)
    with TestClient(main.app) as c:
        outsider = attach_session(c, session_local, uid)
        response = outsider.get(f"/api/results/displayed/{tab}")
    assert response.status_code == 404
    assert response.json()["detail"]["error_kind"] == "not_authorized"


# ── What the words may claim ────────────────────────────────────────────────


def test_showing_after_the_turn_read_the_same_curtailment(client, solved):
    turn, ack = _issue(solved, "curtailment")
    turn.note_read("get_results", chat_tools.get_results(result_kind="curtailment"))
    assert set(turn.reads) == {"curtailment"}
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 200
    assert navigation.display(turn, ack, wait=0) == {
        "verification": "browser_displayed_data",
        "say": "showing",
    }


def test_a_curtailment_read_before_a_resolve_is_opened_not_showing(client, solved):
    turn = _turn(solved)
    turn.note_read("get_results", chat_tools.get_results(result_kind="curtailment"))
    _resolve_curtailment(solved)
    ack = navigation.issue(
        turn, {"kind": "navigate", "panel_id": "results", "results_tab": "curtailment"}
    )
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 200
    out = navigation.display(turn, ack, wait=0)
    assert out["verification"] == "browser_displayed_data" and out["say"] == "opened"
    assert out["differs_from_read"]["read"] != out["differs_from_read"]["shown"]


def test_a_generators_read_is_not_taken_for_the_curtailment_shown(client, solved):
    turn, ack = _issue(solved, "curtailment")
    turn.note_read("get_results", chat_tools.get_results(result_kind="generators"))
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 200
    assert navigation.display(turn, ack, wait=0)["say"] == "opened"


def _resolve_curtailment(ctx):
    ctx.network.generators_t.p.loc[:, "pv"] -= 1.0
    ctx.network.generators_t.p.loc[:, "grid_supply"] += 1.0


@pytest.mark.parametrize(
    "tab,tool", [("flows", "get_energy_flows"), ("loadshape", "get_load_shape")]
)
def test_a_bounded_summary_read_never_makes_the_words_say_showing(
    client, solved, tab, tool
):
    """The flows and load-shape reads are bounded summaries, not the payload the
    tab hashed, so the ACK proves the data shown but the words stay "opened"."""
    turn, ack = _issue(solved, tab)
    turn.note_read(tool, chat_tools.DISPATCHERS[tool]())
    assert tab not in turn.reads
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 200
    out = navigation.display(turn, ack, wait=0)
    assert out["verification"] == "browser_displayed_data" and out["say"] == "opened"


@pytest.mark.parametrize(
    "result",
    [None, "text", 3, [], {"index": [], "columns": []}, {"status": "ok", "plants": {}}],
)
def test_a_non_curtailment_read_has_no_ref(result):
    assert displayed_adapters.ADAPTERS["curtailment"].read_ref(result) is None


def test_a_payload_with_exactly_the_series_keys_has_a_ref():
    """{index, columns, data} and nothing else is the curtailment read's shape."""
    import hashlib

    series = {"index": ["t0"], "columns": ["pv"], "data": [[4.0]]}
    expected = hashlib.sha256(canonical(series).encode()).hexdigest()
    assert displayed_adapters.ADAPTERS["curtailment"].read_ref(series) == {
        "read_sha256": expected
    }


def test_the_curtailment_read_ref_is_the_hash_of_the_part_shown(client, solved):
    import hashlib

    body = client.get("/api/results/displayed/curtailment").json()
    ref = displayed_adapters.ADAPTERS["curtailment"].read_ref(
        chat_tools.get_results(result_kind="curtailment")
    )
    expected = hashlib.sha256(
        canonical(body["data"]["curtailment"]).encode()
    ).hexdigest()
    assert (
        ref
        == {"read_sha256": expected}
        == {"read_sha256": body["binding"]["read_sha256"]}
    )
    # The whole view's hash covers both parts, so it is not the read's.
    assert body["binding"]["data_sha256"] != expected


def test_the_s3a_views_are_listed_as_displayed_data():
    from services import result_views

    claimed = {
        v.tab for v in result_views.RESULT_VIEWS if v.verification == "displayed_data"
    }
    assert set(TABS) <= claimed
    displayed_adapters.check_registry(result_views.RESULT_VIEWS)
