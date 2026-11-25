"""The multi-objective front over HTTP (DC 01 PR2a): POST/GET/abort/apply.

The engine is pinned in test_adequacy_front.py; this file pins the route's
refusals, the record, the worker's failure path and apply-a-point (one undo
step, one revision bump, nothing written on a refusal).
"""

from __future__ import annotations

import json
import threading
import time

import pandas as pd
import pypsa
import pytest

URL = "/api/results/front"


def _net(hours: int = 4) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=hours, freq="h"))
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "clean")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    for name, carrier, capex, mc, eff in (
        ("gas", "gas", 50_000.0, 20.0, 0.4),
        ("clean", "clean", 300_000.0, 0.0, 1.0),
    ):
        n.add(
            "Generator",
            name,
            bus="b",
            carrier=carrier,
            p_nom_extendable=True,
            p_nom_max=1000.0,
            capital_cost=capex,
            marginal_cost=mc,
            efficiency=eff,
            outage_rate_value=0.05,
            outage_rate_basis="EFORd",
            mttr_hours=10.0,
        )
    return n


def _setup(client, install_network, network=None):
    n = install_network(network if network is not None else _net())
    r = client.put(
        "/api/simulation/solver_config", json={"solver_name": "highs", "voll": 5000.0}
    )
    assert r.status_code == 200, r.text
    return n


def _poll(client, timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        r = client.get(URL)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"the front never finished: {body!r}")


_BODY = {
    "levels": [100.0],
    "co2_caps_t": [None, 219_000.0],
    "lever": "ens_cap",
    "mc_draws": 10,
}


def _run(client) -> dict:
    r = client.post(URL, json=_BODY)
    assert r.status_code == 200, r.text
    rec = _poll(client)
    assert rec["status"] == "done", rec
    return rec


@pytest.fixture
def gens(session_ctx):
    """The client's own network's generator sizes and flags."""

    def _read(client):
        g = session_ctx(client).network.generators
        return g[["p_nom", "p_nom_extendable"]].copy()

    return _read


def _revision(session_ctx, client):
    from services import dirty_state

    return dirty_state.revision(session_ctx(client))


# --------------------------------------------------------------------------
# Refusals before anything is published
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, status",
    [
        ({}, 422),
        ({**_BODY, "budget_solves": 100}, 422),
        ({**_BODY, "lever": "magic"}, 422),
        ({**_BODY, "mc_draws": True}, 422),
        ({**_BODY, "levels": [20_000.0]}, 422),
        ({**_BODY, "levels": [1.0] * 17}, 422),
    ],
    ids=[
        "no-levels",
        "budget-override",
        "bad-lever",
        "bool-draws",
        "level-range",
        "budget",
    ],
)
def test_a_bad_request_is_refused_and_publishes_nothing(
    client, install_network, session_state, body, status
):
    _setup(client, install_network)
    r = client.post(URL, json=body)
    assert r.status_code == status, r.text
    assert session_state(client).get("front") is None
    assert client.get(URL).status_code == 204


def test_another_running_study_refuses_the_front(
    client, install_network, session_state
):
    _setup(client, install_network)
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    st = session_state(client)
    st["frontier"] = {"status": "running", "points": [], "thread": t}
    try:
        assert client.post(URL, json=_BODY).status_code == 409
    finally:
        release.set()
        t.join(timeout=5)
        st.pop("frontier", None)


def test_abort_with_no_run_is_404(client, install_network):
    _setup(client, install_network)
    assert client.post(f"{URL}/abort").status_code == 404


# --------------------------------------------------------------------------
# The run and its record
# --------------------------------------------------------------------------


def test_a_front_runs_over_http_and_leaves_the_network_alone(
    gens, client, install_network, session_ctx
):
    _setup(client, install_network)
    before, rev = gens(client), _revision(session_ctx, client)
    rec = _run(client)
    pts = rec["result"]["points"]
    assert [p["status"] for p in pts] == ["ok", "ok"]
    assert rec["result"]["solves_planned"] == 2
    assert rec["plan"]["lever"] == "ens_cap"
    assert rec["edited_since_study"] is False
    assert "thread" not in rec and "stop_event" not in rec
    pd.testing.assert_frame_equal(gens(client), before)
    assert _revision(session_ctx, client) == rev
    assert client.post(f"{URL}/abort").status_code == 200


def test_get_front_serialises_with_no_nan(client, install_network, session_state):
    _setup(client, install_network)
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    session_state(client)["front"] = {
        "status": "done",
        "error": None,
        "network_revision": 0,
        "thread": t,
        "result": {
            "points": [
                {"status": "failed", "point": None, "mc": None},
                {
                    "status": "ok",
                    "point": {"total_eur_per_yr": None, "co2_t_per_yr": None},
                    "mc": {"lole_hours": None, "lole_ci": None},
                },
            ]
        },
    }
    r = client.get(URL)
    assert r.status_code == 200, r.text
    json.dumps(r.json(), allow_nan=False)
    assert "NaN" not in r.text and "Infinity" not in r.text


def test_a_worker_that_raises_fails_the_record_and_frees_the_lock(
    client, install_network, monkeypatch
):
    from services.adequacy import front
    from services.pypsa_service import PyPSAService

    _setup(client, install_network)
    real = front.run_front_study

    def boom(network, lock, *a, **kw):
        with lock:
            raise RuntimeError("solver exploded")

    def silent(network, lock, *a, **kw):
        raise RuntimeError()

    monkeypatch.setattr(front, "run_front_study", boom)
    assert client.post(URL, json=_BODY).status_code == 200
    rec = _poll(client)
    assert rec["status"] == "failed" and "solver exploded" in rec["error"]
    lock = PyPSAService.get_lock()
    assert lock.acquire(blocking=False)
    lock.release()
    monkeypatch.setattr(front, "run_front_study", silent)
    assert client.post(URL, json=_BODY).status_code == 200
    assert _poll(client)["error"] == "RuntimeError"
    monkeypatch.setattr(front, "run_front_study", real)
    assert _run(client)["status"] == "done"


# --------------------------------------------------------------------------
# Apply a point
# --------------------------------------------------------------------------


def _capped_index(rec) -> int:
    pts = rec["result"]["points"]
    return next(i for i, p in enumerate(pts) if p["co2_cap_t_per_yr"] == 219_000.0)


def test_apply_fixes_the_point_in_one_undo_step_and_one_bump(
    gens, client, install_network, session_ctx
):
    _setup(client, install_network)
    rec = _run(client)
    i = _capped_index(rec)
    caps = rec["result"]["points"][i]["point"]["capacities"]
    before, rev = gens(client), _revision(session_ctx, client)

    r = client.post(f"{URL}/apply", json={"index": i, "expected_revision": rev})
    assert r.status_code == 200, r.text
    assert r.json()["fixed"] == 2
    assert _revision(session_ctx, client) == rev + 1
    g = gens(client)
    for key, size in caps.items():
        name = key.split(":", 1)[1]
        assert g.at[name, "p_nom"] == pytest.approx(size)
        assert not bool(g.at[name, "p_nom_extendable"])
    assert g.at["gas", "p_nom"] == pytest.approx(50.0, abs=1e-3)

    # Applied is an edit: the record is stale now, so a second apply refuses.
    rev2 = _revision(session_ctx, client)
    again = client.post(f"{URL}/apply", json={"index": i, "expected_revision": rev2})
    assert again.status_code == 409

    u = client.post("/api/network/undo")
    assert u.status_code == 200, u.text
    after_undo = gens(client)
    pd.testing.assert_frame_equal(after_undo, before, check_dtype=False)


def test_a_wrong_revision_is_refused_and_writes_nothing(
    gens, client, install_network, session_ctx
):
    _setup(client, install_network)
    rec = _run(client)
    before, rev = gens(client), _revision(session_ctx, client)
    r = client.post(f"{URL}/apply", json={"index": 0, "expected_revision": rev + 7})
    assert r.status_code == 409, r.text
    pd.testing.assert_frame_equal(gens(client), before)
    assert _revision(session_ctx, client) == rev
    assert rec["status"] == "done"


def test_a_stale_record_is_refused_even_with_the_current_revision(
    gens, client, install_network, session_ctx
):
    _setup(client, install_network)
    _run(client)
    e = client.patch(
        "/api/network/_bulk",
        json={"component_class": "Load", "names": ["l"], "updates": {"p_set": 90.0}},
    )
    assert e.status_code == 200, e.text
    before, rev = gens(client), _revision(session_ctx, client)
    r = client.post(f"{URL}/apply", json={"index": 0, "expected_revision": rev})
    assert r.status_code == 409 and "changed since" in r.text
    pd.testing.assert_frame_equal(gens(client), before)


def _patch_point(session_state, client, i, **changes):
    rec = session_state(client)["front"]
    rec["result"]["points"][i] = {**rec["result"]["points"][i], **changes}


def test_a_failed_point_is_refused(
    gens, client, install_network, session_state, session_ctx
):
    _setup(client, install_network)
    _run(client)
    _patch_point(session_state, client, 0, status="failed", point=None)
    before, rev = gens(client), _revision(session_ctx, client)
    r = client.post(f"{URL}/apply", json={"index": 0, "expected_revision": rev})
    assert r.status_code == 422 and "did not solve" in r.text
    r = client.post(f"{URL}/apply", json={"index": 9, "expected_revision": rev})
    assert r.status_code == 422
    pd.testing.assert_frame_equal(gens(client), before)


@pytest.mark.parametrize(
    "caps, word",
    [
        (
            {"generators:gas": 50.0, "generators:clean": 50.0, "generators:ghost": 1.0},
            "ghost",
        ),
        ({"generators:gas": 50.0}, "clean"),
    ],
    ids=["unknown-asset", "missing-asset"],
)
def test_a_point_that_does_not_match_the_network_writes_nothing(
    client, install_network, session_state, session_ctx, caps, word
):
    _setup(client, install_network)
    rec = _run(client)
    pt = rec["result"]["points"][0]["point"]
    _patch_point(session_state, client, 0, point={**pt, "capacities": caps})
    n = session_ctx(client).network
    frames = {c: getattr(n, c).copy() for c in ("generators", "loads", "buses")}
    rev = _revision(session_ctx, client)
    r = client.post(f"{URL}/apply", json={"index": 0, "expected_revision": rev})
    assert r.status_code == 422 and word in r.text, r.text
    for c, df in frames.items():
        pd.testing.assert_frame_equal(getattr(n, c), df)
    assert _revision(session_ctx, client) == rev


def test_apply_on_a_multi_period_network_is_refused_by_name(
    client, install_network, session_state, session_ctx, monkeypatch
):
    _setup(client, install_network)
    _run(client)
    n = session_ctx(client).network
    mi = pd.MultiIndex.from_product([[2030], n.snapshots])
    monkeypatch.setattr(type(n), "snapshots", property(lambda self: mi))
    before = n.generators.copy()
    rev = _revision(session_ctx, client)
    r = client.post(f"{URL}/apply", json={"index": 0, "expected_revision": rev})
    assert r.status_code == 422 and "multi-period" in r.text
    pd.testing.assert_frame_equal(n.generators, before)


def test_a_failure_after_the_first_write_is_undone(
    gens, client, install_network, session_ctx, monkeypatch
):
    from services import network_bulk

    _setup(client, install_network)
    rec = _run(client)
    before, rev = gens(client), _revision(session_ctx, client)
    real = network_bulk.apply_bulk_update
    calls = []

    def second_fails(body):
        calls.append(body["component_class"])
        real(body)
        raise RuntimeError("validator refused late")

    monkeypatch.setattr(network_bulk, "apply_bulk_update", second_fails)
    with pytest.raises(RuntimeError):
        client.post(f"{URL}/apply", json={"index": 0, "expected_revision": rev})
    assert calls == ["Generator"]
    pd.testing.assert_frame_equal(gens(client), before, check_dtype=False)
    assert rec["status"] == "done"


# --------------------------------------------------------------------------
# The apply checks and the request, unit-level (mutation survivors)
# --------------------------------------------------------------------------


@pytest.fixture
def unit_apply(monkeypatch):
    """`_apply_rows` against a fixed network and revision 5, never stale."""
    from services import dirty_state, study_state
    from services.adequacy import front_runner as frr
    from services.pypsa_service import PyPSAService

    n = _net()
    monkeypatch.setattr(study_state, "edited_since", lambda rec: False)
    monkeypatch.setattr(dirty_state, "revision", lambda *a: 5)
    monkeypatch.setattr(PyPSAService, "get_network", staticmethod(lambda: n))

    def rows(record, index=0, rev=5):
        return frr._apply_rows(record, index, rev)

    return rows


def _rec(points, status="done"):
    return {"status": status, "result": {"points": points}}


_OK = {
    "status": "ok",
    "point": {"capacities": {"generators:gas": 50.0, "generators:clean": 50.0}},
}


def _refused(fn, status, word):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        fn()
    assert e.value.status_code == status, e.value.detail
    assert word in str(e.value.detail)


def test_the_apply_checks_pass_a_good_point(unit_apply):
    rows = unit_apply(_rec([_OK]))
    assert rows == {
        "Generator": [
            {"name": "gas", "updates": {"p_nom": 50.0, "p_nom_extendable": False}},
            {"name": "clean", "updates": {"p_nom": 50.0, "p_nom_extendable": False}},
        ]
    }


@pytest.mark.parametrize(
    "record, index, status, word",
    [
        (None, 0, 404, "no front study"),
        (_rec([_OK], status="running"), 0, 409, "running"),
        ({"status": "done", "result": None}, 0, 409, "done"),
        ({"status": "done", "result": {"aborted": False}}, 0, 422, "no front point 0"),
        (_rec([_OK]), 1, 422, "no front point 1"),
        (_rec([{**_OK, "status": "failed"}]), 0, 422, "did not solve"),
        (_rec([{"status": "ok", "point": None}]), 0, 422, "did not solve"),
        (
            _rec([{"status": "ok", "point": {"total_eur_per_yr": 1.0}}]),
            0,
            422,
            "not in the point",
        ),
        (
            _rec(
                [
                    {
                        "status": "ok",
                        "point": {
                            "capacities": {
                                "generators:gas": None,
                                "generators:clean": 1.0,
                            }
                        },
                    }
                ]
            ),
            0,
            422,
            "no solved size",
        ),
    ],
    ids=[
        "no-record",
        "running",
        "no-result",
        "no-points",
        "index-at-len",
        "failed-with-point",
        "ok-without-point",
        "no-capacities",
        "none-size",
    ],
)
def test_each_apply_refusal(unit_apply, record, index, status, word):
    _refused(lambda: unit_apply(record, index), status, word)


def test_a_table_the_bulk_edit_cannot_write_is_refused():
    from services.adequacy import front_runner as frr

    assert frr._class_of("generators") == "Generator"
    _refused(lambda: frr._class_of("sub_networks"), 422, "sub_networks")


def test_the_request_defaults_and_pins_the_budget():
    from services.adequacy import front_runner as frr
    from services.adequacy.front import DEFAULT_MC_DRAWS, MAX_FRONT_SOLVES

    kw = frr._engine_kwargs(frr.FrontRequest(levels=[1.0]))
    assert kw["mc_draws"] == DEFAULT_MC_DRAWS and kw["co2_caps_t"] == [None]
    assert kw["budget_solves"] == MAX_FRONT_SOLVES
    assert (
        frr._engine_kwargs(frr.FrontRequest(levels=[1.0], mc_draws=7))["mc_draws"] == 7
    )


def test_no_solver_config_is_refused_before_publishing():
    from services.adequacy import front_runner as frr

    _refused(
        lambda: frr.start_front(
            frr.FrontRequest(levels=[1.0]),
            solver_state={},
            publish_study=lambda *a: pytest.fail("published"),
        ),
        422,
        "no solver configuration",
    )
