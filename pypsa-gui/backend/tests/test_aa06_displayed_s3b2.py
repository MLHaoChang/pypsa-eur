"""Assistant-assurance 06, A5.2 S3b-2: Dispatch and the multi-period Overview
prove the data they displayed.

Dispatch reads its eleven parts under one guard for one source (it offers the
LP dispatch and the AC power flow, the global toggle) and one window; the
binding records both and a hash per part a get_results read can name, so a
read of one part claims only it. The Overview reads its six whole-horizon
parts under one guard; its chat read (get_vintage_results) is not these
payloads, so the words say "opened". Expected figures come from the hand
networks below, never from the adapter.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import logging

import pandas as pd
import pypsa
import pytest

from harness import displayed_adapters, navigation
from routers.results_displayed import canonical
from services import chat_tools
from tests.test_aa06_displayed_s3a import _ack_body, _issue, _outcome, _turn
from tests.test_aa06_displayed_s3b1 import _input_edit, _resolve, _site
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

TABS = ["dispatch", "overview"]
PARTS = displayed_adapters.DISPATCH_PARTS
# Each part, the old route it is, and whether that route takes a source.
ROUTES = {
    "cost_breakdown": ("cost_breakdown", False),
    "generators": ("generators", True),
    "curtailment": ("curtailment", False),
    "storage": ("storage", True),
    "storage_dispatch": ("storage_dispatch", True),
    "store_dispatch": ("store_dispatch", True),
    "store_energy": ("store_energy", True),
    "loads": ("loads", True),
    "links": ("links", True),
    "lost_load": ("lost_load", False),
    "economics_by_carrier": ("economics_by_carrier", False),
}
WINDOWED = {"generators", "curtailment", "storage", "storage_dispatch"}
WINDOWED |= {"store_dispatch", "store_energy", "loads", "links", "lost_load"}
OVERVIEW_PARTS = (
    "generators",
    "storage_dispatch",
    "loads",
    "curtailment",
    "lost_load",
    "cost_breakdown",
)


# ── Hand networks ───────────────────────────────────────────────────────────
# Dispatch: S3b-1's site (load 6, 5, 6 MW; PV 10 MW at 100/50/0 %; grid at
# 50 €/MWh; a 2 MW, 2 MWh battery):
#   pv 8, 5, 0   grid 0, 0, 4   battery -2, 0, +2   curtailment pv 2, 0, 0
# Its AC power flow record holds generators only, at 0.9 of the LP (so the
# sources differ, and the flow's other tables are no data, PF-SAVE-1).
#
# Overview: two investment periods (2030, 2040) of two hours, 10 years each.
# Load 6, 5 MW each period; PV 10 MW free at 100 %, 0 %; grid at 50 €/MWh:
#   pv 6, 0, 6, 0   grid 0, 5, 0, 5


def _solved(ctx):
    ctx.network = _site()
    ctx.solver_state.update(status="completed", condition="optimal")
    return ctx


@pytest.fixture
def solved(baseline):  # noqa: F811
    return _solved(baseline[0])


@pytest.fixture
def with_pf(solved):
    pf = solved.network.generators_t.p * 0.9
    solved.solver_state["ac_pf_results"] = {"generators_t.p": pf}
    yield solved
    solved.solver_state.pop("ac_pf_results", None)


def _periods():
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], [0, 1]]))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings.loc[:, "years"] = 10
    n.investment_period_weightings.loc[:, "objective"] = 1
    n.add("Bus", "site")
    n.add("Carrier", ["solar", "grid"])
    pv = pd.Series([1.0, 0.0, 1.0, 0.0], index=n.snapshots)
    n.add(
        "Generator",
        "pv",
        bus="site",
        carrier="solar",
        p_nom=10,
        p_max_pu=pv,
        marginal_cost=0,
        build_year=2030,
        lifetime=100,
    )
    n.add(
        "Generator",
        "grid_supply",
        bus="site",
        carrier="grid",
        p_nom=100,
        marginal_cost=50,
        build_year=2030,
        lifetime=100,
    )
    load = pd.Series([6.0, 5.0, 6.0, 5.0], index=n.snapshots)
    n.add("Load", "demand", bus="site", p_set=load)
    logging.disable(logging.CRITICAL)
    try:
        status = n.optimize(solver_name="highs", multi_investment_periods=True)
        assert status[0] == "ok"
    finally:
        logging.disable(logging.NOTSET)
    return n


@pytest.fixture
def periods(baseline):  # noqa: F811
    ctx = baseline[0]
    ctx.network = _periods()
    ctx.solver_state.update(status="completed", condition="optimal")
    return ctx


@pytest.fixture
def ctx_for(request):
    """The solved fixture a tab is issued on: the Overview is multi-period only."""

    def pick(tab):
        return request.getfixturevalue("periods" if tab == "overview" else "solved")

    return pick


def _get(client, tab, **params):
    return client.get(f"/api/results/displayed/{tab}", params=params or None)


def _sha(payload):
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def _old(client, part, source=None, window=None):
    route, sourced = ROUTES[part]
    params = dict(window or {}) if part in WINDOWED else {}
    if sourced and source:
        params["source"] = source
    response = client.get(f"/api/results/{route}", params=params or None)
    return None if response.status_code == 204 else response.json()


def _col(payload, name):
    i = payload["columns"].index(name)
    return [round(row[i], 6) for row in payload["data"]]


def _ack_ok(client, ack):
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "browser_displayed_data"


# ── Hand oracle and byte-equal parity with the old routes ───────────────────


@pytest.mark.parametrize("window", [None, {"from": 1, "to": 2}])
def test_dispatch_view_is_every_old_route_byte_equal_and_the_hand_oracle(
    client, solved, window
):
    body = _get(client, "dispatch", **(window or {})).json()
    assert tuple(body["data"]) == PARTS
    for part in PARTS:
        assert canonical(body["data"][part]) == canonical(
            _old(client, part, "lopf", window)
        ), part
        assert body["binding"][f"{part}_sha256"] == _sha(body["data"][part])
    data = body["data"]
    cut = slice(None) if window is None else slice(1, 3)
    assert _col(data["generators"], "pv") == [8.0, 5.0, 0.0][cut]
    assert _col(data["generators"], "grid_supply") == [0.0, 0.0, 4.0][cut]
    assert _col(data["storage_dispatch"], "battery") == [-2.0, 0.0, 2.0][cut]
    assert _col(data["loads"], "demand") == [6.0, 5.0, 6.0][cut]
    assert _col(data["curtailment"], "pv") == [2.0, 0.0, 0.0][cut]
    # No stores or links: those routes answer 204, so the parts are null.
    assert data["store_dispatch"] is None and data["links"] is None
    assert data["cost_breakdown"]["opex"] == pytest.approx(200.0)
    assert body["binding"]["data_sha256"] == _sha(data)


def test_dispatch_view_of_the_ac_power_flow_is_the_old_routes_at_that_source(
    client, with_pf
):
    body = _get(client, "dispatch", source="ac_pf").json()
    assert body["binding"]["source"] == "ac_pf"
    for part in PARTS:
        assert canonical(body["data"][part]) == canonical(
            _old(client, part, "ac_pf")
        ), part
    assert _col(body["data"]["generators"], "pv") == [7.2, 4.5, 0.0]
    # The flow's record has no storage table: no data, not the LP's values.
    assert body["data"]["storage_dispatch"] is None
    lp = _get(client, "dispatch").json()["binding"]
    assert lp["generators_sha256"] != body["binding"]["generators_sha256"]
    assert lp["data_sha256"] != body["binding"]["data_sha256"]


def test_overview_view_is_every_old_route_byte_equal_and_the_hand_oracle(
    client, periods
):
    body = _get(client, "overview").json()
    assert tuple(body["data"]) == OVERVIEW_PARTS
    for part in OVERVIEW_PARTS:
        assert canonical(body["data"][part]) == canonical(_old(client, part)), part
    gens = body["data"]["generators"]
    assert gens["periods"] == [2030, 2030, 2040, 2040]
    assert _col(gens, "pv") == [6.0, 0.0, 6.0, 0.0]
    assert _col(gens, "grid_supply") == [0.0, 5.0, 0.0, 5.0]
    # Whole horizon: no window was asked, so no part carries a range.
    assert all("range" not in (body["data"][p] or {}) for p in OVERVIEW_PARTS)
    assert set(body["binding"]) == displayed_adapters.ADAPTERS["overview"].binding_keys
    assert body["binding"]["data_sha256"] == _sha(body["data"])


@pytest.mark.parametrize("tab", TABS)
def test_an_unsolved_project_answers_204_like_the_old_routes(client, baseline, tab):  # noqa: F811
    assert _get(client, tab).status_code == 204


def _no_data(monkeypatch, names):
    from fastapi import Response

    from routers import results

    for name in names:
        monkeypatch.setattr(results, name, lambda *_a: Response(status_code=204))


_SOLVED_ROUTES = [
    "get_generator_results",
    "get_storage_results",
    "get_storage_dispatch_results",
    "get_store_dispatch_results",
    "get_store_energy_results",
    "get_load_results",
]


def test_dispatch_with_none_of_its_solved_series_answers_204(
    client, solved, monkeypatch
):
    """The tab's "not solved" is no series it counts (hasResults), whatever the
    aggregates say: the view answers 204, as the tab then shows not solved."""
    _no_data(monkeypatch, _SOLVED_ROUTES)
    assert _get(client, "dispatch").status_code == 204


# The hand site has no stores, so those two routes answer 204 of themselves.
_SITE_SERIES = {
    "get_generator_results": "generators",
    "get_storage_results": "storage",
    "get_storage_dispatch_results": "storage_dispatch",
    "get_load_results": "loads",
}


@pytest.mark.parametrize("kept", _SITE_SERIES)
def test_dispatch_with_any_one_solved_series_binds_the_rest_as_null(
    client, solved, monkeypatch, kept
):
    _no_data(monkeypatch, [r for r in _SOLVED_ROUTES if r != kept])
    body = _get(client, "dispatch").json()
    assert body["status"] == "ok"
    nulls = {p for p in PARTS if body["data"][p] is None}
    assert nulls == (
        set(_SITE_SERIES.values()) - {_SITE_SERIES[kept]}
        | {"store_dispatch", "store_energy", "links"}
    )
    for part in nulls:
        assert body["binding"][f"{part}_sha256"] == _sha(None)


@pytest.mark.parametrize(
    "gone,status",
    [
        (["get_generator_results", "get_load_results", "get_cost_breakdown"], 204),
        (["get_generator_results", "get_load_results"], 200),
        (["get_generator_results", "get_cost_breakdown"], 200),
        (["get_load_results", "get_cost_breakdown"], 200),
    ],
)
def test_the_overview_answers_204_only_with_none_of_its_three_gates(
    client, periods, monkeypatch, gone, status
):
    """Its "not solved" is no generators, loads or cost breakdown (the tab's check)."""
    _no_data(monkeypatch, gone)
    response = _get(client, "overview")
    assert response.status_code == status
    if status == 200:
        data = response.json()["data"]
        assert data["curtailment"] is not None and data["lost_load"] is not None
        for name, part in [
            ("get_generator_results", "generators"),
            ("get_load_results", "loads"),
            ("get_cost_breakdown", "cost_breakdown"),
        ]:
            assert (data[part] is None) == (name in gone)


# ── The binding records the call; the ACK re-reads exactly it ───────────────


@pytest.mark.parametrize(
    "params,recorded",
    [
        ({}, {"source": "lopf", "window_from": None, "window_to": None}),
        (
            {"source": "lopf"},
            {"source": "lopf", "window_from": None, "window_to": None},
        ),
        (
            {"source": "ac_pf", "from": 0, "to": 1},
            {"source": "ac_pf", "window_from": 0, "window_to": 1},
        ),
        ({"from": 1}, {"source": "lopf", "window_from": 1, "window_to": None}),
    ],
)
def test_the_dispatch_binding_records_the_arguments_the_routes_were_called_with(
    client, with_pf, params, recorded
):
    binding = _get(client, "dispatch", **params).json()["binding"]
    assert {k: binding[k] for k in recorded} == recorded
    adapter = displayed_adapters.ADAPTERS["dispatch"]
    assert set(binding) == adapter.binding_keys
    assert adapter.reread(binding)["binding"] == binding


@pytest.mark.parametrize("tab", TABS)
def test_ack_of_the_rendered_default_binding_is_displayed_data(client, ctx_for, tab):
    ctx = ctx_for(tab)
    _, ack = _issue(ctx, tab)
    assert ack["source_binding"] == _get(client, tab).json()["binding"]
    _ack_ok(client, ack)


def _resolve_periods(ctx):
    n = ctx.network
    n.generators_t.p.loc[:, "grid_supply"] += 1.0
    n.generators_t.p.loc[:, "pv"] -= 1.0


def _input_edit_periods(ctx):
    ctx.network.loads.loc["demand", "p_set"] = 4.0


@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize("kind", ["resolve", "input_edit"])
def test_ack_after_a_resolve_or_an_input_edit_is_refused(client, ctx_for, tab, kind):
    ctx = ctx_for(tab)
    change = {
        ("dispatch", "resolve"): _resolve,
        ("dispatch", "input_edit"): _input_edit,
        ("overview", "resolve"): _resolve_periods,
        ("overview", "input_edit"): _input_edit_periods,
    }[tab, kind]
    _, ack = _issue(ctx, tab)
    change(ctx)
    if kind == "resolve":  # the fixture change moves this tab's payload
        assert (
            _get(client, tab).json()["binding"]["data_sha256"]
            != ack["source_binding"]["data_sha256"]
        )
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "ui_ack_mismatch"
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize("tab", TABS)
def test_ack_after_a_project_switch_is_refused(client, ctx_for, tab):
    ctx = ctx_for(tab)
    _, ack = _issue(ctx, tab)
    original = ctx.project_uuid
    ctx.project_uuid = "00000000-0000-4000-8000-000000000001"
    try:
        assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 409
    finally:
        ctx.project_uuid = original
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "ac_pf"),  # a changed source the tab offers: 409, not 422
        ("window_from", 1),
        ("window_to", 1),
        ("generators_sha256", "0" * 64),
        ("economics_by_carrier_sha256", "0" * 64),
    ],
)
def test_ack_of_another_source_window_or_part_is_refused_409(
    client, with_pf, field, value
):
    _, ack = _issue(with_pf, "dispatch")
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"][field] = value
    assert displayed_adapters.valid_binding(
        displayed_adapters.ADAPTERS["dispatch"], body["source_binding"]
    )
    response = client.post("/api/chat/ui-ack", json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "ui_ack_mismatch"
    assert _outcome(client, ack["action_id"]) == "submitted"


def test_the_ac_power_flow_view_rereads_at_its_source_and_a_flow_change_moves_it(
    client, with_pf
):
    """The ACK re-reads at the recorded source: a changed flow record (not the
    LP) moves the ac_pf binding, and leaves the LP binding as it was."""
    adapter = displayed_adapters.ADAPTERS["dispatch"]
    pf = _get(client, "dispatch", source="ac_pf").json()["binding"]
    lp = _get(client, "dispatch").json()["binding"]
    assert adapter.reread(pf)["binding"] == pf
    with_pf.solver_state["ac_pf_results"]["generators_t.p"] *= 0.5
    assert adapter.reread(pf)["binding"] != pf
    assert adapter.reread(lp)["binding"] == lp


@pytest.mark.parametrize(
    "tab,change",
    [
        ("dispatch", {"source": "dc"}),  # a source the tab does not offer
        ("dispatch", {"source": None}),
        ("dispatch", {"window_from": 2, "window_to": 1}),
        ("dispatch", {"window_from": "0"}),
        ("dispatch", {"window_to": -1}),
        ("dispatch", {"generators_sha256": None}),
        ("dispatch", {"run_id": "run-2"}),  # an extra key
        ("dispatch", {"week": 0}),
        ("overview", {"source": "lopf"}),  # no source choice at all
        ("overview", {"window_from": 0}),
        ("overview", {"generators_sha256": "0" * 64}),
    ],
)
def test_a_malformed_or_foreign_binding_is_refused_422(client, ctx_for, tab, change):
    ctx = ctx_for(tab)
    _, ack = _issue(ctx, tab)
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"].update(change)
    assert client.post("/api/chat/ui-ack", json=body).status_code == 422
    assert _outcome(client, ack["action_id"]) == "submitted"


def test_a_binding_without_a_part_hash_is_refused_422(client, solved):
    _, ack = _issue(solved, "dispatch")
    body = copy.deepcopy(_ack_body(ack))
    del body["source_binding"]["lost_load_sha256"]
    assert client.post("/api/chat/ui-ack", json=body).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"window_from": 0},
        {"window_from": 0, "window_to": 0},
        {"window_from": 2, "window_to": 2},
        {"window_to": 0},
        {"source": "ac_pf"},
    ],
)
def test_an_index_of_0_a_window_with_from_equal_to_or_ac_pf_is_a_valid_binding(
    client, solved, change
):
    binding = _get(client, "dispatch").json()["binding"]
    adapter = displayed_adapters.ADAPTERS["dispatch"]
    assert displayed_adapters.valid_binding(adapter, {**binding, **change})


@pytest.mark.parametrize(
    "params,kind",
    [
        ({"from": 2, "to": 1}, "invalid_window"),
        ({"from": -1}, None),
        ({"to": "x"}, None),
        ({"source": "dc"}, "source_not_offered"),
        ({"source": ""}, "source_not_offered"),
    ],
)
def test_the_dispatch_view_refuses_a_malformed_window_or_a_source_it_lacks(
    client, solved, params, kind
):
    response = _get(client, "dispatch", **params)
    assert response.status_code == 422
    if kind:
        assert response.json()["detail"]["error_kind"] == kind


@pytest.mark.parametrize("source", ["lopf", "ac_pf"])
def test_the_overview_view_refuses_any_source(client, periods, source):
    response = _get(client, "overview", source=source)
    assert response.status_code == 422
    assert response.json()["detail"]["error_kind"] == "source_not_offered"


def test_the_dispatch_view_called_directly_refuses_a_source_it_lacks(solved):
    from fastapi import HTTPException

    from routers import results_displayed

    with pytest.raises(HTTPException) as err:
        results_displayed.dispatch_view(source="dc")
    assert err.value.status_code == 422
    assert err.value.detail["error_kind"] == "source_not_offered"
    with pytest.raises(HTTPException) as err:
        results_displayed.dispatch_view(window_from=2, window_to=1)
    assert err.value.detail["error_kind"] == "invalid_window"
    # No AC power flow on record: refused, never the LP data under an ac_pf label.
    with pytest.raises(HTTPException) as err:
        results_displayed.dispatch_view(source="ac_pf")
    assert err.value.status_code == 422
    assert err.value.detail["error_kind"] == "source_not_available"


def test_ac_pf_without_a_flow_on_record_is_refused_and_binds_nothing(client, solved):
    response = _get(client, "dispatch", source="ac_pf")
    assert response.status_code == 422
    assert response.json()["detail"]["error_kind"] == "source_not_available"
    assert response.json()["detail"]["message"] == (
        "The AC power flow has not run yet. Run it, or show the LP dispatch."
    )
    assert "binding" not in response.json()


def test_an_ack_of_ac_pf_without_a_flow_on_record_is_refused(client, solved):
    _, ack = _issue(solved, "dispatch")
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"]["source"] = "ac_pf"
    response = client.post("/api/chat/ui-ack", json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "ui_ack_mismatch"
    assert _outcome(client, ack["action_id"]) == "submitted"


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
    client, solved, params, recorded
):
    response = _get(client, "dispatch", **params)
    assert response.status_code == 200, response.text
    binding = response.json()["binding"]
    assert {k: binding[k] for k in recorded} == recorded
    adapter = displayed_adapters.ADAPTERS["dispatch"]
    assert displayed_adapters.valid_binding(adapter, binding)
    assert adapter.reread(binding)["binding"] == binding


# ── One guard per bound read (condition 1) ──────────────────────────────────

_DISPATCH_CALLS = {
    "get_cost_breakdown": (),
    "get_generator_results": ("ac_pf", 0, 1),
    "get_curtailment": (0, 1),
    "get_storage_results": ("ac_pf", 0, 1),
    "get_storage_dispatch_results": ("ac_pf", 0, 1),
    "get_store_dispatch_results": ("ac_pf", 0, 1),
    "get_store_energy_results": ("ac_pf", 0, 1),
    "get_load_results": ("ac_pf", 0, 1),
    "get_link_results": ("ac_pf", 0, 1),
    "get_lost_load": (0, 1),
    "get_economics_by_carrier": (),
}
_OVERVIEW_CALLS = {
    "get_generator_results": ("lopf", None, None),
    "get_storage_dispatch_results": ("lopf", None, None),
    "get_load_results": ("lopf", None, None),
    "get_curtailment": (None, None),
    "get_lost_load": (None, None),
    "get_cost_breakdown": (),
}


@pytest.mark.parametrize(
    "tab,params,calls",
    [
        ("dispatch", {"source": "ac_pf", "from": 0, "to": 1}, _DISPATCH_CALLS),
        ("overview", {}, _OVERVIEW_CALLS),
    ],
)
def test_every_part_is_read_inside_one_network_guard(
    client, ctx_for, monkeypatch, tab, params, calls
):
    import harness.durable as durable
    from routers import results

    ctx = ctx_for(tab)
    ctx.solver_state["ac_pf_results"] = {
        "generators_t.p": ctx.network.generators_t.p * 0.9
    }
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
    for name in calls:
        real = getattr(results, name)

        def spy(*a, _real=real, _name=name):
            seen.append((_name, state["depth"], a))
            return _real(*a)

        monkeypatch.setattr(results, name, spy)
    try:
        assert _get(client, tab, **params).status_code == 200
    finally:
        ctx.solver_state.pop("ac_pf_results", None)
    assert state["entered"] == 1
    assert sorted((name, depth) for name, depth, _a in seen) == sorted(
        (n, 1) for n in calls
    )
    # Each route is called with exactly what the binding records.
    assert {name: a for name, _d, a in seen} == calls


@pytest.mark.parametrize(
    "tab,first,change",
    [
        ("dispatch", "get_cost_breakdown", _resolve),
        ("overview", "get_generator_results", _resolve_periods),
    ],
)
def test_a_resolve_between_two_parts_cannot_split_the_solve(
    client, ctx_for, monkeypatch, tab, first, change
):
    """A solve landing between the parts must wait for the guard; simulated by
    changing the network after the first part's read, the later parts then
    disagree with the issued binding, so the ACK refuses."""
    from routers import results

    ctx = ctx_for(tab)
    _, ack = _issue(ctx, tab)
    real = getattr(results, first)

    def then_resolve(*a):
        out = real(*a)
        change(ctx)
        return out

    monkeypatch.setattr(results, first, then_resolve)
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 409


# ── Per-family guards: fingerprint, NaN, lock, outsider ─────────────────────


@pytest.mark.parametrize("tab", TABS)
def test_the_binding_input_hash_is_the_plain_network_fingerprint(client, ctx_for, tab):
    from services import workflow_tools

    ctx = ctx_for(tab)
    served = _get(client, tab).json()["binding"]
    assert served["input_sha256"] == workflow_tools._network_input_fingerprint(ctx)
    (_input_edit if tab == "dispatch" else _input_edit_periods)(ctx)
    again = _get(client, tab).json()["binding"]
    assert again["input_sha256"] == workflow_tools._network_input_fingerprint(ctx)
    assert again["input_sha256"] != served["input_sha256"]


@pytest.mark.parametrize("tab", TABS)
def test_a_non_finite_value_is_null_in_the_data_its_hash_and_the_ack(
    client, ctx_for, monkeypatch, tab
):
    from routers import results

    ctx = ctx_for(tab)
    payload = {"index": [], "total": float("nan"), "peak": float("-inf")}
    monkeypatch.setattr(results, "get_load_results", lambda *_a: payload)
    body = _get(client, tab).json()
    data = body["data"]["loads"]
    assert data["total"] is None and data["peak"] is None
    encoded = canonical(body["data"])
    assert "NaN" not in encoded and "Infinity" not in encoded
    assert body["binding"]["data_sha256"] == _sha(body["data"])
    if tab == "dispatch":
        assert body["binding"]["loads_sha256"] == _sha(data)
    _, ack = _issue(ctx, tab)
    assert ack["source_binding"] == body["binding"]
    _ack_ok(client, ack)


@pytest.mark.parametrize("tab", TABS)
def test_a_held_network_lock_answers_409(client, ctx_for, tab):
    import threading

    from services.pypsa_service import PyPSAService

    ctx_for(tab)
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
        response = _get(client, tab)
    finally:
        release.set()
        worker.join()
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "assurance_context_changed"


@pytest.mark.parametrize("tab", TABS)
def test_a_reader_without_access_to_the_open_project_gets_404(_auth_db, ctx_for, tab):
    from fastapi.testclient import TestClient

    import main
    from tests.conftest import attach_session
    from tests.test_chat_session_ownership import _other_org_user

    ctx_for(tab)
    uid, session_local = _other_org_user(_auth_db)
    with TestClient(main.app) as c:
        outsider = attach_session(c, session_local, uid)
        response = outsider.get(f"/api/results/displayed/{tab}")
    assert response.status_code == 404
    assert response.json()["detail"]["error_kind"] == "not_authorized"


# ── What the words may claim ────────────────────────────────────────────────


def _read(turn, kind, **kw):
    read = chat_tools.get_results(result_kind=kind, **kw)
    turn.note_read("get_results", read, {"result_kind": kind, **kw})
    return read


@pytest.mark.parametrize("kind", PARTS)
def test_a_dispatch_read_claims_the_part_it_names(client, solved, kind):
    """Each part's whole-horizon LP read is the part the default view shows."""
    turn, ack = _issue(solved, "dispatch")
    read = _read(turn, kind)
    key = f"{kind}_sha256"
    if read.get("status") == "no_data":  # a part with no data claims nothing
        assert "dispatch" not in turn.reads
        return
    # The hash of the part named, and no claim on any other part.
    assert turn.reads["dispatch"] == {key: ack["source_binding"][key]}
    _ack_ok(client, ack)
    # The words name the one part read, never the whole tab.
    assert navigation.display(turn, ack, wait=0) == {
        "verification": "browser_displayed_data",
        "say": f"showing {kind}",
        "parts": [kind],
    }


def test_a_generators_and_a_storage_dispatch_read_say_showing(client, solved):
    """The view's own reads (result_views): each, the last one read, is shown."""
    turn, ack = _issue(solved, "dispatch")
    _read(turn, "generators")
    assert set(turn.reads["dispatch"]) == {"generators_sha256"}
    _read(turn, "storage_dispatch")
    assert set(turn.reads["dispatch"]) == {"storage_dispatch_sha256"}
    _ack_ok(client, ack)
    out = navigation.display(turn, ack, wait=0)
    assert (out["say"], out["parts"]) == (
        "showing storage_dispatch",
        ["storage_dispatch"],
    )


def test_a_read_of_several_parts_names_each_and_all_parts_say_plain_showing(
    client, solved
):
    """Two parts matched: both named. Every part matched: the whole view is
    what was read, so the words say "showing" without a part list."""
    turn, ack = _issue(solved, "dispatch")
    binding = ack["source_binding"]
    _ack_ok(client, ack)
    two = ("generators_sha256", "loads_sha256")
    turn.reads["dispatch"] = {k: binding[k] for k in two}
    out = navigation.display(turn, ack, wait=0)
    assert (out["say"], out["parts"]) == (
        "showing generators, loads",
        ["generators", "loads"],
    )
    refs = displayed_adapters.ADAPTERS["dispatch"].ref_keys
    turn.reads["dispatch"] = {k: binding[k] for k in refs}
    assert navigation.display(turn, ack, wait=0) == {
        "verification": "browser_displayed_data",
        "say": "showing",
    }


def test_a_dispatch_read_of_one_part_does_not_claim_another(client, solved):
    turn, ack = _issue(solved, "dispatch")
    _read(turn, "generators")
    # Its hash put under another part's key is not that part.
    turn.reads["dispatch"] = {
        "storage_dispatch_sha256": turn.reads["dispatch"]["generators_sha256"]
    }
    _ack_ok(client, ack)
    out = navigation.display(turn, ack, wait=0)
    assert out["say"] == "opened" and "differs_from_read" in out


def test_a_dispatch_read_without_its_kind_claims_nothing(solved):
    """The series share one shape: only the call's kind can name the part."""
    turn = _turn(solved)
    read = chat_tools.get_results(result_kind="generators")
    turn.note_read("get_results", read)
    assert "dispatch" not in turn.reads
    adapter = displayed_adapters.ADAPTERS["dispatch"]
    assert adapter.read_ref(read) is None
    assert adapter.kind_read_ref(read, None) is None
    assert adapter.kind_read_ref(read, "prices") is None
    assert adapter.kind_read_ref(None, "generators") is None
    assert adapter.kind_read_ref({"status": "no_data"}, "generators") is None


def test_a_read_of_another_kind_does_not_replace_the_dispatch_ref(solved):
    turn = _turn(solved)
    _read(turn, "generators")
    ref = turn.reads["dispatch"]
    for kind in ("prices", "emissions", "lines"):
        _read(turn, kind)
    assert turn.reads["dispatch"] == ref


def test_a_read_of_the_flow_does_not_claim_the_lp_view(client, with_pf):
    """The tab issues its default (LP) view; a read of the AC flow is not it."""
    turn, ack = _issue(with_pf, "dispatch")
    _read(turn, "generators", source="ac_pf")
    _ack_ok(client, ack)
    assert navigation.display(turn, ack, wait=0)["say"] == "opened"


def test_a_dispatch_read_of_the_horizon_is_not_a_windowed_view(client, solved):
    turn = _turn(solved)
    _read(turn, "generators")
    shown = _get(client, "dispatch", **{"from": 1, "to": 2}).json()["binding"]
    assert turn.reads["dispatch"] != {"generators_sha256": shown["generators_sha256"]}


def test_the_overview_says_opened_whatever_the_turn_read(client, periods):
    turn, ack = _issue(periods, "overview")
    turn.note_read("get_vintage_results", chat_tools.get_vintage_results(), {})
    for kind in ("generators", "cost_breakdown", "loads"):
        _read(turn, kind)
    assert "overview" not in turn.reads
    _ack_ok(client, ack)
    out = navigation.display(turn, ack, wait=0)
    assert out["verification"] == "browser_displayed_data" and out["say"] == "opened"


def test_the_s3b2_views_are_listed_as_displayed_data():
    from services import result_views

    claimed = {
        v.tab for v in result_views.RESULT_VIEWS if v.verification == "displayed_data"
    }
    assert set(TABS) <= claimed
    displayed_adapters.check_registry(result_views.RESULT_VIEWS)
    dispatch = result_views.for_results_tab("dispatch")
    kinds = {r.call()["args"].get("result_kind") for r in dispatch.reads}
    assert kinds <= set(PARTS)
    overview = result_views.for_results_tab("overview")
    assert [r.call()["tool"] for r in overview.reads] == ["get_vintage_results"]
