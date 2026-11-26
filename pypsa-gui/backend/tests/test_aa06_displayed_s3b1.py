"""Assistant-assurance 06, A5.2 S3b-1: Prices, Capacity expansion and Storage
cycling prove the data they displayed.

Each view calls its tab's existing route functions under one guard and records
the source, window, threshold and row cap it called them with; the ACK re-reads
exactly that view. On a MIP the prices are withheld: the binding hashes the
withheld answer and the words say "unavailable: <reason>", never "showing".
Expected figures come from the hand network below, never from the adapter.
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
from harness.solve_validity import DUALS_NOT_MARGINAL
from routers.results_displayed import canonical
from services import chat_tools
from tests.test_aa06_displayed_s3a import _ack_body, _issue, _outcome, _turn
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

TABS = ["prices", "capex", "storage"]
WINDOWED = ["prices", "storage"]


# ── Hand network ────────────────────────────────────────────────────────────
# One bus. Load 6, 5, 6 MW. PV (10 MW, free, capital cost 10 €/MW) is available
# at 100 %, 50 %, 0 %; the grid supply (50 €/MWh) fills the rest; a 2 MW, 2 MWh
# battery (one hour) stores PV's spare at t0 and serves it at t2.
#   battery p   -2, 0, +2 MW         grid 0, 0, 4 MWh -> opex 200 €
#   price t0 = 0 (PV spare), t2 = 50 (the grid is marginal)
#   capex: PV 10 MW x 10 €/MW/a = 100 €/a, over 3 of 8760 h
# MIP twin: the grid supply is committable, so the solve is integer and its
# bus duals are not marginal prices (`solved_price_limit` withholds them).


def _site(committable=False):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2026-01-05", periods=3, freq="h"))
    n.add("Bus", "site")
    n.add("Carrier", ["solar", "grid", "battery"])
    n.add(
        "Generator",
        "pv",
        bus="site",
        carrier="solar",
        p_nom=10,
        p_max_pu=[1.0, 0.5, 0.0],
        marginal_cost=0,
        capital_cost=10,
    )
    n.add(
        "Generator",
        "grid_supply",
        bus="site",
        carrier="grid",
        p_nom=100,
        marginal_cost=50,
        committable=committable,
        p_min_pu=0.0,
    )
    n.add(
        "StorageUnit",
        "battery",
        bus="site",
        carrier="battery",
        p_nom=2,
        max_hours=1,
    )
    n.add("Load", "demand", bus="site", p_set=[6.0, 5.0, 6.0])
    logging.disable(logging.CRITICAL)
    try:
        assert n.optimize(solver_name="highs")[0] == "ok"
    finally:
        logging.disable(logging.NOTSET)
    return n


def _solved(ctx, committable=False):
    ctx.network = _site(committable)
    ctx.solver_state.update(status="completed", condition="optimal")
    return ctx


@pytest.fixture
def solved(baseline):  # noqa: F811
    return _solved(baseline[0])


@pytest.fixture
def mip(baseline):  # noqa: F811
    return _solved(baseline[0], committable=True)


def _get(client, tab, **params):
    return client.get(f"/api/results/displayed/{tab}", params=params or None)


def _sha(payload):
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


# ── Hand oracle and byte-equal parity with the old routes ───────────────────


@pytest.mark.parametrize("window", [None, {"from": 1, "to": 2}])
def test_prices_view_is_both_old_routes_byte_equal_and_the_hand_oracle(
    client, solved, window
):
    body = _get(client, "prices", **(window or {})).json()
    assert set(body["data"]) == {"prices", "price_drivers"}
    old = client.get(
        "/api/results/prices", params={"source": "lopf", **(window or {})}
    ).json()
    assert canonical(body["data"]["prices"]) == canonical(old)
    assert canonical(body["data"]["price_drivers"]) == canonical(
        client.get(
            "/api/results/price_drivers", params={"threshold": 2000, "limit": 200}
        ).json()
    )
    prices = [row[0] for row in body["data"]["prices"]["data"]]
    assert (prices[0], prices[-1]) == (
        (0.0, 50.0) if window is None else (prices[0], 50.0)
    )
    assert body["binding"]["price_limit"] is None
    # The part the chat read names is the prices part the tab renders.
    assert body["binding"]["read_sha256"] == _sha(body["data"]["prices"])


def test_the_drivers_part_is_read_at_the_threshold_and_cap_asked(client, solved):
    body = _get(client, "prices", threshold=40, limit=5).json()
    assert canonical(body["data"]["price_drivers"]) == canonical(
        client.get(
            "/api/results/price_drivers", params={"threshold": 40, "limit": 5}
        ).json()
    )
    rows = body["data"]["price_drivers"]["rows"]
    assert any(r["price"] == 50.0 and r["bus"] == "site" for r in rows)
    assert (body["binding"]["threshold"], body["binding"]["limit"]) == (40.0, 5)


def test_capex_view_is_both_old_routes_byte_equal_and_the_hand_oracle(client, solved):
    body = _get(client, "capex").json()
    assert set(body["data"]) == {"cost_breakdown", "economics_by_carrier"}
    cost = client.get("/api/results/cost_breakdown").json()
    econ = client.get("/api/results/economics_by_carrier").json()
    assert canonical(body["data"]["cost_breakdown"]) == canonical(cost)
    assert canonical(body["data"]["economics_by_carrier"]) == canonical(econ)
    # Annualised capex over the modelled 3 of 8760 hours, as the route reports it.
    assert body["data"]["cost_breakdown"]["capex"] == pytest.approx(
        100.0 * 3 / 8760, rel=1e-3
    )
    assert body["data"]["cost_breakdown"]["opex"] == pytest.approx(200.0)
    assert body["binding"]["cost_breakdown_sha256"] == _sha(cost)
    assert body["binding"]["economics_by_carrier_sha256"] == _sha(econ)


@pytest.mark.parametrize("window", [None, {"from": 1, "to": 2}])
def test_storage_view_is_the_old_route_byte_equal_and_the_hand_oracle(
    client, solved, window
):
    body = _get(client, "storage", **(window or {})).json()
    assert canonical(body["data"]) == canonical(
        client.get("/api/results/storage_dispatch", params=window).json()
    )
    p = [round(row[0], 6) for row in body["data"]["data"]]
    assert p == ([-2.0, 0.0, 2.0] if window is None else [0.0, 2.0])


@pytest.mark.parametrize("tab", TABS)
def test_an_unsolved_project_answers_204_like_the_old_route(client, baseline, tab):  # noqa: F811
    assert _get(client, tab).status_code == 204


def test_a_drivers_204_leaves_that_part_null(client, solved, monkeypatch):
    from fastapi import Response

    from routers import results

    monkeypatch.setattr(
        results, "get_price_drivers", lambda *_a: Response(status_code=204)
    )
    body = _get(client, "prices").json()
    assert body["data"]["price_drivers"] is None
    assert body["data"]["prices"]["columns"] == ["site"]


# ── MIP: withheld prices bind, and the words say so (condition 3) ───────────


def test_a_mip_binds_the_withheld_answer_and_no_dual_derived_number(client, mip):
    body = _get(client, "prices", threshold=0).json()
    withheld = {"status": "unavailable", "reason": DUALS_NOT_MARGINAL}
    # Every dual-derived part is withheld the same way: prices and drivers.
    assert body["data"] == {"prices": withheld, "price_drivers": withheld}
    assert body["data"]["prices"] == client.get("/api/results/prices").json()
    assert body["binding"]["price_limit"] == DUALS_NOT_MARGINAL
    assert body["binding"]["read_sha256"] == _sha(withheld)
    assert body["binding"]["data_sha256"] == _sha(body["data"])


def _ack_ok(client, ack):
    response = client.post("/api/chat/ui-ack", json=_ack_body(ack))
    assert response.status_code == 200, response.text
    assert response.json()["verification"] == "browser_displayed_data"


def test_a_mip_read_and_ack_say_unavailable_with_the_reason_never_showing(client, mip):
    turn, ack = _issue(mip, "prices")
    read = chat_tools.get_results(result_kind="prices")
    ref = displayed_adapters.ADAPTERS["prices"].read_ref(read)
    # The read of the withheld prices is the withheld payload's hash.
    assert ref == {"read_sha256": ack["source_binding"]["read_sha256"]}
    turn.note_read("get_results", read, {"result_kind": "prices"})
    assert turn.reads["prices"] == ref
    assert navigation.display(turn, ack, wait=0)["say"] == "opened"  # before the ACK
    _ack_ok(client, ack)
    assert navigation.display(turn, ack, wait=0) == {
        "verification": "browser_displayed_data",
        "say": f"unavailable: {DUALS_NOT_MARGINAL}",
    }


def test_a_mip_without_a_read_still_says_unavailable(client, mip):
    turn, ack = _issue(mip, "prices")
    _ack_ok(client, ack)
    assert navigation.display(turn, ack, wait=0)["say"] == (
        f"unavailable: {DUALS_NOT_MARGINAL}"
    )


def test_the_lp_twin_binds_its_prices_and_says_showing(client, solved):
    turn, ack = _issue(solved, "prices")
    assert ack["source_binding"]["price_limit"] is None
    turn.note_read(
        "get_results",
        chat_tools.get_results(result_kind="prices"),
        {"result_kind": "prices"},
    )
    _ack_ok(client, ack)
    assert navigation.display(turn, ack, wait=0) == {
        "verification": "browser_displayed_data",
        "say": "showing",
    }


# ── The binding records the call; the ACK re-reads exactly it ───────────────


@pytest.mark.parametrize(
    "tab,params,recorded",
    [
        (
            "prices",
            {},
            {
                "source": "lopf",
                "window_from": None,
                "window_to": None,
                "threshold": 2000.0,
                "limit": 200,
            },
        ),
        (
            "prices",
            {"source": "lopf", "from": 0, "to": 1, "threshold": 300, "limit": 7},
            {
                "source": "lopf",
                "window_from": 0,
                "window_to": 1,
                "threshold": 300.0,
                "limit": 7,
            },
        ),
        ("storage", {}, {"source": "lopf", "window_from": None, "window_to": None}),
        (
            "storage",
            {"from": 1},
            {"source": "lopf", "window_from": 1, "window_to": None},
        ),
        ("capex", {}, {}),
    ],
)
def test_the_binding_records_the_arguments_the_route_was_called_with(
    client, solved, tab, params, recorded
):
    binding = _get(client, tab, **params).json()["binding"]
    assert {k: binding[k] for k in recorded} == recorded
    assert set(binding) == displayed_adapters.ADAPTERS[tab].binding_keys
    assert displayed_adapters.ADAPTERS[tab].reread(binding)["binding"] == binding


@pytest.mark.parametrize("tab", TABS)
def test_ack_of_the_rendered_default_binding_is_displayed_data(client, solved, tab):
    _, ack = _issue(solved, tab)
    assert ack["source_binding"] == _get(client, tab).json()["binding"]
    _ack_ok(client, ack)


def _resolve(ctx):
    # The same inputs, other figures: more grid, less PV, and the battery idle.
    n = ctx.network
    n.generators_t.p.loc[:, "grid_supply"] += 1.0
    n.generators_t.p.loc[:, "pv"] -= 1.0
    n.storage_units_t.p.loc[:, "battery"] = 0.0
    n.buses_t.marginal_price.loc[:, "site"] += 1.0


def _input_edit(ctx):
    ctx.network.loads.loc["demand", "p_set"] = 5.0


@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize("change", [_resolve, _input_edit])
def test_ack_after_a_resolve_or_an_input_edit_is_refused(client, solved, tab, change):
    _, ack = _issue(solved, tab)
    change(solved)
    if change is _resolve:  # the fixture change moves this tab's payload
        assert (
            _get(client, tab).json()["binding"]["data_sha256"]
            != ack["source_binding"]["data_sha256"]
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
        ("prices", "window_from", 1),
        ("prices", "window_to", 1),
        ("prices", "threshold", 40.0),
        ("prices", "limit", 1),
        ("prices", "read_sha256", "0" * 64),
        ("prices", "price_limit", DUALS_NOT_MARGINAL),  # an LP is not withheld
        ("storage", "window_from", 1),
        ("storage", "window_to", 0),
        ("capex", "cost_breakdown_sha256", "0" * 64),
        ("capex", "economics_by_carrier_sha256", "0" * 64),
    ],
)
def test_ack_of_another_window_threshold_or_part_is_refused_409(
    client, solved, tab, field, value
):
    _, ack = _issue(solved, tab)
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"][field] = value
    assert client.post("/api/chat/ui-ack", json=body).status_code == 409
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize(
    "tab,change",
    [
        # A changed source: the tabs offer the LP solve only, so any other
        # source is one they do not offer, refused 422 before any re-read.
        ("prices", {"source": "ac_pf"}),
        ("storage", {"source": "ac_pf"}),
        ("capex", {"source": "lopf"}),  # no source choice at all
        ("prices", {"window_from": 2, "window_to": 1}),
        ("storage", {"window_from": 2, "window_to": 1}),
        ("prices", {"window_from": "0"}),
        ("prices", {"threshold": "2000"}),
        ("prices", {"threshold": True}),
        ("prices", {"threshold": None}),
        ("prices", {"threshold": float("inf")}),
        ("prices", {"limit": -1}),
        ("prices", {"limit": 2.5}),
        ("prices", {"price_limit": 3}),
        ("prices", {"source": None}),
        ("storage", {"window_to": -1}),
        ("capex", {"run_id": "run-2"}),  # an extra key
        ("prices", {"week": 0}),
    ],
)
def test_a_malformed_or_foreign_binding_is_refused_422(client, solved, tab, change):
    _, ack = _issue(solved, tab)
    body = copy.deepcopy(_ack_body(ack))
    body["source_binding"].update(change)
    if change.get("threshold") == float("inf"):
        assert not displayed_adapters.valid_binding(
            displayed_adapters.ADAPTERS[tab], body["source_binding"]
        )
        return
    assert client.post("/api/chat/ui-ack", json=body).status_code == 422
    assert _outcome(client, ack["action_id"]) == "submitted"


@pytest.mark.parametrize("tab", WINDOWED)
@pytest.mark.parametrize(
    "change",
    [
        {"window_from": 0},
        {"window_from": 0, "window_to": 0},
        {"window_from": 2, "window_to": 2},
        {"window_to": 0},
    ],
)
def test_an_index_of_0_or_a_window_with_from_equal_to_is_a_valid_binding(
    client, solved, tab, change
):
    binding = _get(client, tab).json()["binding"]
    adapter = displayed_adapters.ADAPTERS[tab]
    assert displayed_adapters.valid_binding(adapter, {**binding, **change})
    if tab == "prices":
        assert displayed_adapters.valid_binding(adapter, {**binding, "limit": 0})
        assert displayed_adapters.valid_binding(adapter, {**binding, "threshold": 0})
        assert displayed_adapters.valid_binding(
            adapter, {**binding, "price_limit": DUALS_NOT_MARGINAL}
        )


@pytest.mark.parametrize("tab", WINDOWED)
@pytest.mark.parametrize(
    "params", [{"from": 2, "to": 1}, {"from": -1}, {"to": "x"}, {"source": "ac_pf"}]
)
def test_the_view_refuses_a_malformed_window_or_a_source_it_lacks(
    client, solved, tab, params
):
    response = _get(client, tab, **params)
    assert response.status_code == 422
    kind = {"from": "invalid_window", "source": "source_not_offered"}
    if params.get("from") == 2 or "source" in params:
        expected = kind["source" if "source" in params else "from"]
        assert response.json()["detail"]["error_kind"] == expected


@pytest.mark.parametrize(
    "params", [{"threshold": "nan"}, {"threshold": "inf"}, {"limit": -1}]
)
def test_the_prices_view_refuses_a_non_finite_threshold_or_a_negative_cap(
    client, solved, params
):
    assert _get(client, "prices", **params).status_code == 422


def test_the_capex_view_refuses_a_source(client, solved):
    response = _get(client, "capex", source="lopf")
    assert response.status_code == 422
    assert response.json()["detail"]["error_kind"] == "source_not_offered"


@pytest.mark.parametrize("tab", ["prices", "storage"])
def test_a_view_called_directly_refuses_a_source_it_lacks(solved, tab):
    from fastapi import HTTPException

    from routers import results_displayed

    with pytest.raises(HTTPException) as err:
        results_displayed.VIEWS[tab](source="ac_pf")
    assert err.value.status_code == 422
    assert err.value.detail["error_kind"] == "source_not_offered"
    with pytest.raises(HTTPException) as err:
        results_displayed.VIEWS[tab](window_from=2, window_to=1)
    assert err.value.detail["error_kind"] == "invalid_window"


@pytest.mark.parametrize("tab", WINDOWED)
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
    response = _get(client, tab, **params)
    assert response.status_code == 200, response.text
    binding = response.json()["binding"]
    assert {k: binding[k] for k in recorded} == recorded
    assert displayed_adapters.valid_binding(displayed_adapters.ADAPTERS[tab], binding)


# ── One guard per bound read (condition 1) ──────────────────────────────────


@pytest.mark.parametrize(
    "tab,routes",
    [
        ("prices", ["get_prices", "get_price_drivers"]),
        ("capex", ["get_cost_breakdown", "get_economics_by_carrier"]),
        ("storage", ["get_storage_dispatch_results"]),
    ],
)
def test_every_part_is_read_inside_one_network_guard(
    client, solved, monkeypatch, tab, routes
):
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
            seen.append((_name, state["depth"], a))
            return _real(*a)

        monkeypatch.setattr(results, name, spy)
    assert _get(client, tab).status_code == 200
    assert state["entered"] == 1
    assert [(name, depth) for name, depth, _a in seen] == [(n, 1) for n in routes]
    # Each route is called with exactly what the binding records.
    calls = {name: a for name, _d, a in seen}
    if tab == "prices":
        assert calls == {
            "get_prices": ("lopf", None, None),
            "get_price_drivers": (2000.0, 200),
        }
    if tab == "storage":
        assert calls == {"get_storage_dispatch_results": ("lopf", None, None)}


def _scarcity(ctx):
    # A re-solve whose prices pass the drivers' default threshold of 2000.
    ctx.network.buses_t.marginal_price.loc[:, "site"] = 3000.0


@pytest.mark.parametrize(
    "tab,first,change",
    [("prices", "get_prices", _scarcity), ("capex", "get_cost_breakdown", _resolve)],
)
def test_a_resolve_between_the_two_parts_cannot_split_the_solve(
    client, solved, monkeypatch, tab, first, change
):
    """A solve landing between the parts must wait for the guard; simulated by
    changing the network after the first part's read, the second part then
    disagrees with the issued binding, so the ACK refuses."""
    from routers import results

    _, ack = _issue(solved, tab)
    real = getattr(results, first)

    def then_resolve(*a):
        out = real(*a)
        change(solved)
        return out

    monkeypatch.setattr(results, first, then_resolve)
    assert client.post("/api/chat/ui-ack", json=_ack_body(ack)).status_code == 409


# ── Per-family guards: fingerprint, NaN, lock, outsider ─────────────────────


@pytest.mark.parametrize("tab", TABS)
def test_the_binding_input_hash_is_the_plain_network_fingerprint(client, solved, tab):
    from services import workflow_tools

    served = _get(client, tab).json()["binding"]
    assert served["input_sha256"] == workflow_tools._network_input_fingerprint(solved)
    _input_edit(solved)
    again = _get(client, tab).json()["binding"]
    assert again["input_sha256"] == workflow_tools._network_input_fingerprint(solved)
    assert again["input_sha256"] != served["input_sha256"]


@pytest.mark.parametrize(
    "tab,route,part",
    [
        ("prices", "get_prices", "prices"),
        ("capex", "get_cost_breakdown", "cost_breakdown"),
        ("storage", "get_storage_dispatch_results", None),
    ],
)
def test_a_non_finite_value_is_null_in_the_data_its_hash_and_the_ack(
    client, solved, monkeypatch, tab, route, part
):
    from routers import results

    payload = {"index": [], "total": float("nan"), "peak": float("-inf")}
    monkeypatch.setattr(results, route, lambda *_a: payload)
    body = _get(client, tab).json()
    data = body["data"][part] if part else body["data"]
    assert data["total"] is None and data["peak"] is None
    encoded = canonical(body["data"])
    assert "NaN" not in encoded and "Infinity" not in encoded
    assert body["binding"]["data_sha256"] == _sha(body["data"])
    _, ack = _issue(solved, tab)
    assert ack["source_binding"] == body["binding"]
    _ack_ok(client, ack)


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
        response = _get(client, tab)
    finally:
        release.set()
        worker.join()
    assert response.status_code == 409
    assert response.json()["detail"]["error_kind"] == "assurance_context_changed"


@pytest.mark.parametrize("tab", TABS)
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


@pytest.mark.parametrize("kind", ["cost_breakdown", "economics_by_carrier"])
def test_a_capex_read_claims_the_part_it_names_and_says_showing(client, solved, kind):
    turn, ack = _issue(solved, "capex")
    read = chat_tools.get_results(result_kind=kind)
    ref = displayed_adapters.ADAPTERS["capex"].read_ref(read)
    key = f"{kind}_sha256"
    # The hash of the part named, and no claim on the other part.
    assert ref == {key: ack["source_binding"][key]}
    turn.note_read("get_results", read, {"result_kind": kind})
    _ack_ok(client, ack)
    # S3b-2: the words name the part read, never the whole tab.
    out = navigation.display(turn, ack, wait=0)
    assert (out["say"], out["parts"]) == (f"showing {kind}", [kind])


def test_a_capex_read_of_one_part_does_not_claim_the_other(client, solved):
    turn, ack = _issue(solved, "capex")
    read = chat_tools.get_results(result_kind="cost_breakdown")
    turn.note_read("get_results", read, {"result_kind": "cost_breakdown"})
    assert set(turn.reads["capex"]) == {"cost_breakdown_sha256"}
    # Its hash put under the other part's key is not that part.
    turn.reads["capex"] = {
        "economics_by_carrier_sha256": turn.reads["capex"]["cost_breakdown_sha256"]
    }
    _ack_ok(client, ack)
    assert navigation.display(turn, ack, wait=0)["say"] == "opened"


def test_a_capex_read_of_an_unknown_part_key_is_opened(client, solved):
    turn, ack = _issue(solved, "capex")
    turn.reads["capex"] = {
        "cost_breakdown_sha256": ack["source_binding"]["cost_breakdown_sha256"],
        "other_sha256": "0" * 64,
    }
    _ack_ok(client, ack)
    assert navigation.display(turn, ack, wait=0)["say"] == "opened"


def test_a_storage_read_never_makes_the_words_say_showing(client, solved):
    turn, ack = _issue(solved, "storage")
    for kind in ("storage", "storage_dispatch"):
        turn.note_read(
            "get_results",
            chat_tools.get_results(result_kind=kind),
            {"result_kind": kind},
        )
    assert "storage" not in turn.reads
    _ack_ok(client, ack)
    out = navigation.display(turn, ack, wait=0)
    assert out["verification"] == "browser_displayed_data" and out["say"] == "opened"


def test_a_prices_read_of_another_window_is_opened(client, solved):
    """The read is the whole horizon; the tab shows a window: not the same data."""
    turn = _turn(solved)
    turn.note_read(
        "get_results",
        chat_tools.get_results(result_kind="prices"),
        {"result_kind": "prices"},
    )
    shown = _get(client, "prices", **{"from": 1, "to": 2}).json()["binding"]
    assert turn.reads["prices"] != {"read_sha256": shown["read_sha256"]}


# ── _is_curtailment: a plain series of another kind is not curtailment ──────


def test_a_later_read_of_another_series_does_not_replace_the_curtailment_ref(
    client, solved
):
    turn = _turn(solved)
    curtailment = chat_tools.get_results(result_kind="curtailment")
    turn.note_read("get_results", curtailment, {"result_kind": "curtailment"})
    ref = turn.reads["curtailment"]
    for kind in ("generators", "storage_dispatch", "prices"):
        series = chat_tools.get_results(result_kind=kind)
        assert {"index", "columns", "data"} <= set(series)
        turn.note_read("get_results", series, {"result_kind": kind})
    assert turn.reads["curtailment"] == ref


def test_a_prices_payload_is_not_taken_for_curtailment_by_its_shape(client, solved):
    """Without the call's kind: the prices payload has more than a plain series' keys."""
    prices = chat_tools.get_results(result_kind="prices")
    assert displayed_adapters.ADAPTERS["curtailment"].read_ref(prices) is None
    assert displayed_adapters.ADAPTERS["prices"].read_ref(prices) is not None


@pytest.mark.parametrize(
    "result",
    [
        {"index": [], "columns": [], "data": [], "data_adjusted": []},
        {"index": [], "columns": [], "data": [], "source": "lp"},
    ],
)
def test_a_series_with_extra_keys_has_no_curtailment_ref(result):
    assert displayed_adapters.ADAPTERS["curtailment"].read_ref(result) is None


def test_a_curtailment_series_with_periods_and_range_has_a_ref():
    series = {
        "index": ["t0"],
        "periods": [2026],
        "columns": ["pv"],
        "data": [[4.0]],
        "range": {"from": 0, "to": 0},
        "results_current": True,
    }
    assert displayed_adapters.ADAPTERS["curtailment"].read_ref(series) is not None


@pytest.mark.parametrize(
    "result",
    [
        None,
        {"status": "unavailable"},
        {"status": "ok", "reason": "x"},
        {"status": "unavailable", "reason": "x", "extra": 1},
        {"index": [], "columns": [], "data": []},
    ],
)
def test_a_non_prices_read_has_no_prices_ref(result):
    assert displayed_adapters.ADAPTERS["prices"].read_ref(result) is None


@pytest.mark.parametrize(
    "result",
    [
        None,
        {"capex": 1, "opex": 1},
        {"available": True},
        {"available": True, "by_carrier": {}, "extra": 1},
        {"total_tCO2": 1.0, "by_carrier": {}},
    ],
)
def test_a_non_capex_read_has_no_capex_ref(result):
    assert displayed_adapters.ADAPTERS["capex"].read_ref(result) is None


def test_the_s3b1_views_are_listed_as_displayed_data():
    from services import result_views

    claimed = {
        v.tab for v in result_views.RESULT_VIEWS if v.verification == "displayed_data"
    }
    assert set(TABS) <= claimed
    displayed_adapters.check_registry(result_views.RESULT_VIEWS)
    # The storage view's reads stay the state of charge (follow-up: its dispatch).
    storage = result_views.for_results_tab("storage")
    assert [r.call() for r in storage.reads] == [
        {"tool": "get_results", "args": {"result_kind": "storage"}}
    ] or all("storage_dispatch" not in str(r.call()) for r in storage.reads)


# ── The real loop path passes the call's args to note_read (GK B 72) ────────


def _loop_read(turn, args):
    import uuid

    from harness.session import ChatSession
    from services import chat_service

    session = ChatSession(session_id=uuid.uuid4().hex)
    session.navigation_turn = turn
    tool_use = {"id": uuid.uuid4().hex, "name": "get_results", "input": args}
    list(chat_service._dispatch_real_tool_call(session, tool_use, []))


def test_the_loop_passes_the_result_kind_so_a_generators_read_is_not_curtailment(
    client, solved
):
    """A generators series has curtailment's key set; only the call's
    result_kind, which the loop must pass, tells the two apart."""
    turn, _ = _issue(solved, "curtailment")
    _loop_read(turn, {"result_kind": "generators"})
    assert "curtailment" not in turn.reads
    _loop_read(turn, {"result_kind": "curtailment"})
    assert set(turn.reads["curtailment"]) == {"read_sha256"}
