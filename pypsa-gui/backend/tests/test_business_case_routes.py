"""
Business case 08a: the business-case inputs route, the run route and its job, and the
stored report's staleness (spec §7; GK B BC10 and the 08a plan conditions C1–C8).

The investment case runs on the SAM S2 oracle case through the IC router's adapter seam
(`_ic_build_case`, as in `test_investment_case_routes`); the business case then runs on
the same rebuilt case.
"""

from __future__ import annotations

import dataclasses
import threading

import pytest

from tests.conftest import build_network
from tests.fixtures.investment_case.sam import sam_case as S
from tests.test_investment_case_routes import (
    STUDY_URL as IC_URL,
)
from tests.test_investment_case_routes import (
    _fake_build,
    _live_thread,
    _poll,
    _put_fin,
    _s2_inputs,
    _solved,
)

BC_URL = "/api/results/business_case"
INPUTS_URL = "/api/results/business_case/inputs"
ABORT_URL = "/api/results/business_case/abort"
CFG_URL = "/api/simulation/solver_config"


def _line(**kw) -> dict:
    base = dict(
        name="product",
        kind="revenue",
        category="product_sale",
        unit="t",
        quantity=10.0,
        price=50.0,
        escalation="opex",
    )
    base.update(kw)
    return base


def _bc(*lines, **kw) -> dict:
    return {"inputs": {"lines": list(lines or [_line()])}, **kw}


def _put(client, bc, if_match=None):
    headers = {"If-Match": if_match} if if_match is not None else {}
    return client.put(INPUTS_URL, json={"business_case": bc}, headers=headers)


def _bc_poll(client, timeout=30.0):
    import time

    deadline = time.time() + timeout
    body = {}
    while time.time() < deadline:
        r = client.get(BC_URL)
        if r.status_code == 200:
            body = r.json()
            if body.get("status") not in ("running",):
                return body
        time.sleep(0.05)
    raise AssertionError(f"business case did not settle: {body}")


def _with_ic(client, install_network, monkeypatch, **fake):
    """A solved network, the finance inputs and a current investment-case report."""
    calls = _fake_build(monkeypatch, **fake)
    _solved(install_network)
    assert _put_fin(client, _s2_inputs()).status_code == 200
    assert client.post(IC_URL, json={}).status_code == 200
    assert _poll(client)["status"] == "done"
    return calls


# ── the inputs route (C8) ────────────────────────────────────────────────────


def test_get_before_any_put_is_not_set(client, install_network):
    install_network(build_network())
    body = client.get(INPUTS_URL).json()
    assert (
        body["business_case"] is None and body["status"] == "not_set" and body["digest"]
    )


def test_put_validates_stores_and_the_digest_guards_the_edit(
    client, install_network, session_state
):
    install_network(build_network())
    d0 = client.get(INPUTS_URL).json()["digest"]
    r = _put(client, _bc(), if_match=d0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["digest"] != d0
    stored = session_state(client)["solver_config"].business_case
    assert stored["inputs"]["lines"][0]["name"] == "product"
    # A stale If-Match is refused, nothing written.
    r = _put(client, None, if_match=d0)
    assert (
        r.status_code == 412 and r.json()["detail"]["code"] == "business_case_changed"
    )
    assert session_state(client)["solver_config"].business_case == stored
    # The current digest clears it.
    assert _put(client, None, if_match=body["digest"]).status_code == 200
    assert session_state(client)["solver_config"].business_case is None


@pytest.mark.parametrize(
    "bad, where",
    [
        (_bc(_line(category="nope")), "lines"),  # a LineItem validator
        (
            {"inputs": {"lines": [], "receivable_days": 400.0}},
            "receivable_days",
        ),  # BusinessCaseInputs field
        (_bc(kw_per_gpu=0.0), "kw_per_gpu"),
        (_bc(kw_per_gpu=float("inf")), "kw_per_gpu"),
        (_bc(capacity={"pv": -1.0}), "capacity"),
        (_bc(break_even_line="missing"), "break_even_line"),
        (
            _bc(
                _line(kind="opex", category="maintenance", name="m"),
                break_even_line="m",
            ),
            "break_even_line",
        ),
        ({"inputs": {"lines": []}, "bogus": 1}, "bogus"),
    ],
)
def test_put_runs_every_validator_and_names_the_field(
    client, install_network, session_state, bad, where
):
    """C8: the PUT runs `BusinessCaseInputs`' validators and the run parameters' own."""
    from models.business_case import BusinessCaseInputs

    install_network(build_network())
    if where in ("lines", "receivable_days"):
        with pytest.raises(Exception):  # the model itself refuses it too
            BusinessCaseInputs.model_validate(bad["inputs"])
    r = client.put(
        INPUTS_URL, content=_json(bad), headers={"content-type": "application/json"}
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "business_case_inputs_invalid"
    assert any(
        where in ".".join(e["loc"]) or where in e["msg"] for e in detail["errors"]
    )
    assert session_state(client)["solver_config"].business_case is None


def _json(bc) -> str:
    import json

    return json.dumps({"business_case": bc}, allow_nan=True)


def test_put_body_is_closed_and_required(client, install_network):
    install_network(build_network())
    assert client.put(INPUTS_URL, json={}).status_code == 422
    assert (
        client.put(INPUTS_URL, json={"business_case": None, "x": 1}).status_code == 422
    )


def test_put_is_refused_while_a_solve_is_in_flight(
    client, install_network, session_state
):
    install_network(build_network())
    with _live_thread(session_state(client), "thread", record=False):
        r = _put(client, _bc())
        assert r.status_code == 409 and r.json()["detail"]["code"] == "solver_in_flight"
    assert session_state(client)["solver_config"].business_case is None


def test_the_solver_config_put_keeps_the_inputs_and_refuses_a_change(
    client, install_network, session_state
):
    install_network(build_network())
    assert _put(client, _bc()).status_code == 200
    stored = session_state(client)["solver_config"].business_case
    r = client.put(CFG_URL, json={"voll": 1234.0})
    assert r.status_code == 200, r.text
    full = client.get(CFG_URL).json()
    assert full["business_case"] == stored
    assert client.put(CFG_URL, json=full).status_code == 200
    for changed in (_bc(_line(price=1.0)), None):
        r = client.put(CFG_URL, json={"voll": 9.0, "business_case": changed})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "business_case_via_dedicated_route"
    cfg = session_state(client)["solver_config"]
    assert cfg.business_case == stored and cfg.voll == 1234.0


# ── C1: in no fingerprint, no digest, no IC key ──────────────────────────────


def test_c1_the_inputs_move_no_fingerprint_digest_or_ic_key(
    client, install_network, session_ctx, session_state, monkeypatch
):
    from services import dirty_state
    from services.adequacy.report import _config_hash
    from services.reliability_goal import goal_free_config
    from services.workflow_tools import _network_input_fingerprint

    _with_ic(client, install_network, monkeypatch)
    ctx = session_ctx(client)
    cfg0 = ctx.solver_state["solver_config"]

    def keys():
        c = session_ctx(client)
        cfg = c.solver_state["solver_config"]
        return (
            _network_input_fingerprint(c),
            _network_input_fingerprint(c, exact=True),
            goal_free_config(cfg),
            _config_hash(cfg),
            client.get(IC_URL).json()["report"]["current_hash"],
            dict(session_state(client)["investment_case_report"]),
        )

    before, rev = keys(), dirty_state.revision(ctx)
    assert _put(client, _bc(kw_per_gpu=1.2, capacity={"pv": 5.0})).status_code == 200
    assert session_ctx(client).solver_state["solver_config"].business_case is not None
    assert keys() == before
    assert dirty_state.revision(session_ctx(client)) == rev  # no network-revision bump
    # The pins bite: a real LP field moves each one.
    session_ctx(client).solver_state["solver_config"] = dataclasses.replace(
        cfg0, voll=cfg0.voll + 1.0
    )
    after = keys()
    for i in range(5):
        assert after[i] != before[i], i


def test_c1_goal_free_config_drops_only_the_named_field():
    from services.reliability_goal import NOT_LP_FIELDS, goal_free_config

    assert NOT_LP_FIELDS == frozenset({"business_case"})
    d = {"voll": 1.0, "business_case": {"x": 1}, "finance": {"y": 2}}
    assert goal_free_config(d) == {"voll": 1.0, "finance": {"y": 2}}


# ── C2: one config reader ────────────────────────────────────────────────────


def test_c2_a_legacy_config_without_the_field_loads_and_the_inputs_round_trip(
    client, install_network, session_ctx
):
    from routers.projects import _solver_config_from_dict
    from services.solver_service import SolverConfig

    legacy = dataclasses.asdict(SolverConfig())
    legacy.pop("business_case")
    assert _solver_config_from_dict(legacy).business_case is None

    install_network(build_network(), name="bc_persist")
    assert (
        client.post(
            "/api/projects/bc_persist", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    assert _put(client, _bc()).status_code == 200
    stored = session_ctx(client).solver_state["solver_config"].business_case
    assert (
        client.post(
            "/api/projects/bc_persist", params={"expect": "bc_persist"}
        ).status_code
        == 200
    )
    install_network(build_network())
    assert session_ctx(client).solver_state["solver_config"].business_case is None
    assert client.get("/api/projects/bc_persist").status_code == 200
    assert session_ctx(client).solver_state["solver_config"].business_case == stored
    assert client.get(INPUTS_URL).json()["status"] == "ok"


def test_c2_an_unloadable_stored_value_is_shown_and_the_run_refuses_it(
    client, install_network, session_state, monkeypatch
):
    _with_ic(client, install_network, monkeypatch)
    st = session_state(client)
    st["solver_config"] = dataclasses.replace(
        st["solver_config"], business_case={"inputs": {"lines": "x"}}
    )
    body = client.get(INPUTS_URL).json()
    assert body["status"] == "business_case_inputs_invalid" and body["message"]
    r = client.post(BC_URL)
    assert (
        r.status_code == 422
        and r.json()["detail"]["code"] == "business_case_inputs_invalid"
    )
    assert st.get("business_case") is None


# ── the run (C3–C6) ──────────────────────────────────────────────────────────


def test_post_without_inputs_or_ic_refuses(
    client, install_network, monkeypatch, session_state
):
    _fake_build(monkeypatch)
    _solved(install_network)
    r = client.post(BC_URL)
    assert (
        r.status_code == 422
        and r.json()["detail"]["code"] == "business_case_inputs_missing"
    )
    assert _put(client, _bc()).status_code == 200
    r = client.post(BC_URL)  # C3: no report
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "investment_case_missing" and d["reason"] == "no_report"
    assert "run the investment case first" in d["message"]
    assert session_state(client).get("business_case") is None


def test_c3_a_stale_ic_report_refuses(
    client, install_network, monkeypatch, session_state
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    assert _put_fin(client, {**_s2_inputs(), "analysis_years": 3}).status_code == 200
    r = client.post(BC_URL)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "investment_case_missing" and "finance" in d["changed"]
    assert session_state(client).get("business_case") is None


def test_c3_an_unreadable_ic_key_refuses(
    client, install_network, monkeypatch, session_state
):
    import routers.results as R

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    monkeypatch.setattr(R, "_ic_current_assumptions", lambda: (None, "network_busy"))
    r = client.post(BC_URL)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "network_busy"


def test_c3_a_rebuilt_case_with_another_hash_refuses(
    client, install_network, monkeypatch, session_state
):
    import routers.results as R

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    monkeypatch.setattr(R, "_ic_case_hash", lambda case: "another-hash")
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert (
        body["status"] == "refused" and body["error_code"] == "investment_case_missing"
    )
    assert body["refusal"]["reason"] == "case_hash_differs"
    assert session_state(client).get("business_case_report") is None


def test_c4_an_edit_after_the_route_check_refuses_never_mixes(
    client, install_network, monkeypatch, session_state
):
    """The IC key re-read under the network lock moved since the route's check: the run
    refuses before reading a single output."""
    import routers.results as R
    from services.business_case import linked_outputs as L

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    real = R._ic_current_assumptions
    seen = {"n": 0}

    def moving():
        seen["n"] += 1
        key, why = real()
        return ((key[0] + "-moved", key[1]) if seen["n"] > 1 else key), why

    monkeypatch.setattr(R, "_ic_current_assumptions", moving)
    read = []
    monkeypatch.setattr(L, "bind", lambda *a, **k: read.append(1))
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert (
        body["status"] == "refused" and body["error_code"] == "investment_case_missing"
    )
    assert read == [] and session_state(client).get("business_case_report") is None


def test_c4_a_network_edit_after_the_ic_refuses(
    client, install_network, monkeypatch, session_ctx
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    n = session_ctx(client).network
    n.generators_t.p.iloc[0, 0] += 1.0  # a re-solve or a restored result
    r = client.post(BC_URL)
    assert (
        r.status_code == 409 and r.json()["detail"]["code"] == "investment_case_missing"
    )


def test_a_run_stores_the_report_and_it_reads_stale_on_its_own_inputs(
    client, install_network, monkeypatch, session_state
):
    _with_ic(client, install_network, monkeypatch)
    assert (
        _put(client, _bc(break_even_line="product", capacity={"pv": 2.0})).status_code
        == 200
    )
    r = client.post(BC_URL)
    assert r.status_code == 200 and r.json()["status"] == "running"
    body = _bc_poll(client)
    assert body["status"] == "done", body
    assert body["stages_done"] == [
        "rebuild_ic_case",
        "read_outputs",
        "build",
        "run_engine",
        "break_even",
        "store",
    ]
    assert "thread" not in body and "stop_event" not in body
    rep = body["report"]
    assert rep["basis"]["label"] == "the project on its own"
    assert rep["ic_case_hash"] == "case-hash-for-test"
    assert set(rep["kpis"]) >= {
        "ebitda_margin_gross",
        "ebitda_margin_net",
        "project_npv",
        "equity_irr",
        "payback_years",
        "min_dscr",
        "revenue_per_capacity_gross",
    }
    assert rep["kpis"]["revenue_per_capacity_gross"]["status"] == "ok"
    assert rep["pnl"]["years"] and "revenue" in rep["pnl"]["rows"]
    assert rep["break_even"]["line"] == "product"
    assert body["staleness"] == {
        "present": True,
        "stale": False,
        "changed": [],
        "reason": None,
    }
    stored = session_state(client)["business_case_report"]
    assert stored["business_case_hash"] == body["business_case_hash"]
    # Its own inputs moved: stale, naming them; the IC report is untouched.
    ic_before = dict(session_state(client)["investment_case_report"])
    assert _put(client, _bc(_line(price=60.0))).status_code == 200
    st = client.get(BC_URL).json()["staleness"]
    assert st["stale"] is True and st["changed"] == ["business_case_inputs"]
    assert session_state(client)["investment_case_report"] == ic_before
    # The IC moved: stale, naming it.
    assert (
        _put(client, _bc(break_even_line="product", capacity={"pv": 2.0})).status_code
        == 200
    )
    assert client.get(BC_URL).json()["staleness"]["stale"] is False
    assert _put_fin(client, {**_s2_inputs(), "analysis_years": 3}).status_code == 200
    assert client.get(BC_URL).json()["staleness"]["changed"] == ["investment_case"]


def test_204_before_a_run(client, install_network):
    install_network(build_network())
    assert client.get(BC_URL).status_code == 204


def test_c6_a_refusal_is_a_typed_record_never_a_500_or_a_report(
    client, install_network, monkeypatch, session_state
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(_line(price=None))).status_code == 200  # saved partial
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert body["status"] == "refused" and body["error_code"] == "business_case_refused"
    assert body["refusal"]["refusal"] == "price_not_established"
    assert body["refusal"]["field"].startswith("lines[0]")
    assert session_state(client).get("business_case_report") is None


def test_c5_409_during_a_solve_or_another_study(
    client, install_network, monkeypatch, session_state
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    with _live_thread(session_state(client), "thread", record=False):
        r = client.post(BC_URL)
        assert r.status_code == 409 and "solve" in r.json()["detail"]
    with _live_thread(session_state(client), "investment_case"):
        assert client.post(BC_URL).status_code == 409
    assert session_state(client).get("business_case") is None


def test_c5_a_solve_starting_at_the_claim_is_refused_under_the_lock(
    client, install_network, monkeypatch, session_state
):
    """K1b: the early gate passed; a solve that starts as the run claims the mesh is seen
    by the re-check under the lock, so no run starts."""
    import routers.results as R

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    st = session_state(client)
    real = R._refuse_if_mesh_busy
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    calls = {"n": 0}

    def gate(key):
        calls["n"] += 1
        if calls["n"] == 2:  # the claim's re-check, inside the lock
            st["thread"] = t
        return real(key)

    monkeypatch.setattr(R, "_refuse_if_mesh_busy", gate)
    try:
        r = client.post(BC_URL)
        assert r.status_code == 409, r.text
        assert calls["n"] == 2 and st.get("business_case") is None
    finally:
        st["thread"] = None
        release.set()
        t.join(timeout=5)


def test_c5_two_concurrent_posts_start_one_run(
    client, install_network, monkeypatch, session_state
):
    hold = threading.Event()
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    _fake_build(monkeypatch, hold=hold)
    import routers.results as R

    monkeypatch.setattr(R, "_ic_case_hash", lambda case: "case-hash-for-test")
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def post():
        barrier.wait()
        codes.append(client.post(BC_URL).status_code)

    ts = [threading.Thread(target=post) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    try:
        assert sorted(codes) == [200, 409], codes
    finally:
        hold.set()
    assert _bc_poll(client)["status"] == "done"


def test_abort_mid_run_stores_nothing(
    client, install_network, monkeypatch, session_state
):
    hold = threading.Event()
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    _fake_build(monkeypatch, hold=hold)
    assert client.post(BC_URL).status_code == 200
    assert client.post(BC_URL).status_code == 409  # holds the mesh
    assert client.post(IC_URL, json={}).status_code == 409
    assert client.post(ABORT_URL).status_code == 200
    hold.set()
    assert _bc_poll(client)["status"] == "aborted"
    assert session_state(client).get("business_case_report") is None


def test_the_ic_case_is_the_ic_runs_own_case(client, install_network, monkeypatch):
    """BC1: the business case reads the investment case and never changes its report."""
    calls = _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    assert client.post(BC_URL).status_code == 200
    assert _bc_poll(client)["status"] == "done"
    assert len(calls) == 2 and calls[0] == calls[1]  # the same adapter inputs
    assert S.to_finance_case("s2") == S.to_finance_case("s2")


# ── C6: not established, never 0 (API, KPI, workbook) ────────────────────────


def test_c6_kw_per_gpu_reaches_the_read_and_its_absence_is_not_established(
    client, install_network, monkeypatch, session_state
):
    from services.business_case import linked_outputs as L

    _with_ic(client, install_network, monkeypatch)
    seen = []

    def fake_resolve(line, n, solve, **params):
        seen.append(params)
        return L.NotEvaluated("it_energy", "kw_per_gpu_missing")

    monkeypatch.setattr(L, "resolve", fake_resolve)
    gpu = _line(
        name="gpu", unit="GPU_h", quantity=None, quantity_source="linked:it_energy@1"
    )
    for kw in (None, 1.25):
        assert _put(client, _bc(gpu, kw_per_gpu=kw)).status_code == 200
        assert client.post(BC_URL).status_code == 200
        body = _bc_poll(client)
        assert seen[-1]["kw_per_gpu"] == kw
        assert body["status"] == "done", body
        rep = body["report"]
        assert rep["outputs"]["gpu"] == {
            "status": "not_established",
            "reason": "kw_per_gpu_missing",
        }
        for name in ("ebitda_margin_gross", "revenue_per_capacity_gross"):
            assert rep["kpis"][name]["value"] is None
            assert rep["kpis"][name]["status"] != "ok"


def test_c6_a_break_even_without_a_root_is_not_established_in_api_and_workbook():
    """The runner passes break-even's status through and the sheet writes the status, never
    a price, for every break_even_* kind."""
    import io

    import openpyxl

    from services.results_workbook.business_case import write_business_case_sheet

    for status in (
        "break_even_not_bracketed",
        "break_even_not_established",
        "break_even_not_converged",
    ):
        report = {
            "basis": {"label": "the project on its own"},
            "currency": "USD",
            "kpis": {
                "project_npv": {
                    "value": None,
                    "status": "not_established",
                    "reason": "wacc_nominal_missing",
                    "unit": "USD",
                },
                "min_dscr": {"value": None, "status": "n/a", "reason": "no_debt"},
            },
            "break_even": {
                "status": status,
                "price": 12.0,
                "price_scale": 2.0,
                "line": "product",
            },
            "pnl": {"years": [2031], "rows": {"revenue": [None]}, "streams": {}},
        }
        wb = openpyxl.Workbook()
        write_business_case_sheet(wb, report, {"stale": False})
        wb = openpyxl.load_workbook(io.BytesIO(_save(wb)))
        cells = {
            r[0].value: [c.value for c in r[1:]]
            for r in wb["Business case"].iter_rows()
            if r[0].value
        }
        assert cells["Price at NPV 0"][:2] == ["not_established", status]
        assert cells["Price scale at NPV 0"][0] == "not_established"
        assert cells["project_npv"][:2] == ["not_established", "wacc_nominal_missing"]
        assert cells["min_dscr"][:2] == ["n/a", "no_debt"]
        assert cells["revenue"][0] == "not_established"


def _save(wb) -> bytes:
    import io

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── C7: the workbook sheet ───────────────────────────────────────────────────


def _sheet(client) -> dict:
    import io

    import openpyxl

    r = client.get(
        "/api/results/workbook.xlsx", params={"views": "investment", "stored": "true"}
    )
    assert r.status_code == 200, r.text
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    about = {
        row[0].value: row[1].value for row in wb["About"].iter_rows() if row[0].value
    }
    rows = {
        row[0].value: [c.value for c in row[1:]]
        for row in wb["Business case"].iter_rows()
        if row[0].value
    }
    return {"about": about["Business case"], "rows": rows}


def test_c7_the_sheet_reads_the_stored_report_only(
    client, install_network, monkeypatch, session_state
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(break_even_line="product")).status_code == 200
    # Nothing stored: not established, and the export never runs it.
    got = _sheet(client)
    assert (
        got["about"].startswith("not_established")
        and "run the business case" in got["about"]
    )
    assert got["rows"]["Business case"][0] == got["about"]
    assert "ebitda_margin_gross" not in got["rows"]
    assert session_state(client).get("business_case") is None
    assert session_state(client).get("business_case_report") is None
    # A current report: its figures.
    assert client.post(BC_URL).status_code == 200
    assert _bc_poll(client)["status"] == "done"
    got = _sheet(client)
    assert got["about"].startswith("current")
    rep = session_state(client)["business_case_report"]
    k = rep["kpis"]["ebitda_margin_gross"]
    assert got["rows"]["ebitda_margin_gross"][0] == (
        k["value"]
        if k["status"] == "ok"
        else ("n/a" if k["status"] == "n/a" else "not_established")
    )
    assert got["rows"]["Revenue line"][0] == "product"
    # Stale: labelled with what changed, its figures not shown.
    assert _put(client, _bc(_line(price=70.0))).status_code == 200
    got = _sheet(client)
    assert got["about"].startswith("stale") and "business_case_inputs" in got["about"]
    assert "ebitda_margin_gross" not in got["rows"]


# ── the preset route: DefaultUnitMismatch's catching site (C6) ───────────────

PRESET_URL = "/api/results/business_case/preset"


def _dc_project(client, install_network, monkeypatch):
    import routers.results as R

    install_network(build_network())
    monkeypatch.setattr(R, "_active_template_meta", lambda: {"id": "eh_datacenter"})
    assert _put_fin(client, _s2_inputs()).status_code == 200


def test_preset_lists_the_variants_and_returns_a_variant_read_only(
    client, install_network, monkeypatch, session_state
):
    _dc_project(client, install_network, monkeypatch)
    body = client.get(PRESET_URL).json()
    assert (
        set(body["variants"]) == {"gpu_cloud", "colocation"}
        and body["business_case"] is None
    )
    r = client.get(PRESET_URL, params={"variant": "gpu_cloud"})
    assert r.status_code == 200, r.text
    lines = r.json()["business_case"]["inputs"]["lines"]
    assert lines and all(
        ln.get("price") is None for ln in lines if ln["kind"] == "revenue"
    )
    assert session_state(client)["solver_config"].business_case is None  # read-only
    # The client stores it through the PUT.
    assert _put(client, r.json()["business_case"]).status_code == 200
    r = client.get(PRESET_URL, params={"variant": "nope"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "preset_not_found"


def test_preset_refusals(client, install_network, monkeypatch):
    import routers.results as R

    install_network(build_network())
    monkeypatch.setattr(R, "_active_template_meta", lambda: None)
    r = client.get(PRESET_URL)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "template_not_known"
    monkeypatch.setattr(R, "_active_template_meta", lambda: {"id": "eh_datacenter"})
    r = client.get(PRESET_URL, params={"variant": "gpu_cloud"})
    assert (
        r.status_code == 422 and r.json()["detail"]["code"] == "finance_inputs_missing"
    )


def test_c6_a_pack_row_in_another_unit_is_422_never_a_filled_default(
    client, install_network, monkeypatch, session_state
):
    """Fail-closed at the catching site: the real `with_cost_defaults` raises on a row whose
    unit is not the line's `default_units`; the route answers 422 naming the field."""
    from types import SimpleNamespace

    import models.business_case as M

    _dc_project(client, install_network, monkeypatch)
    monkeypatch.setattr(
        M,
        "_pack_row",
        lambda pack, key: SimpleNamespace(
            value=1.0, currency=None, unit="not-the-unit", money_year=None
        ),
    )
    r = client.get(PRESET_URL, params={"variant": "gpu_cloud"})
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["code"] == "default_unit_mismatch"
    assert d["field"].startswith("lines[") and "default_keys[" in d["field"]
    assert "business_case" not in r.json()
    assert session_state(client)["solver_config"].business_case is None


# ── review round: the gaps the 08a spec review named ─────────────────────────


def test_c2_an_unbuildable_config_is_422_never_a_silent_default(
    client, install_network, monkeypatch, session_state
):
    import routers.projects as P

    install_network(build_network())

    def boom(data):
        raise TypeError("unexpected keyword 'x'")

    monkeypatch.setattr(P, "_solver_config_from_dict", boom)
    r = _put(client, _bc())
    assert (
        r.status_code == 422 and r.json()["detail"]["code"] == "solver_config_invalid"
    )
    assert session_state(client)["solver_config"].business_case is None


def test_c4_lost_production_reads_the_ic_runs_own_live_solve(
    client, install_network, monkeypatch, session_ctx
):
    from services.business_case import linked_outputs as L

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    seen = []
    real = L.lost_production

    def spy(n, solve, case, inputs=None):
        seen.append((n, solve))
        return real(n, solve, case, inputs)

    monkeypatch.setattr(L, "lost_production", spy)
    assert client.post(BC_URL).status_code == 200
    assert _bc_poll(client)["status"] == "done"
    ctx = session_ctx(client)
    ((n, solve),) = seen
    assert n is ctx.network and solve.network is ctx.network
    assert solve.lost_load is ctx.solver_state.get("last_lost_load")


def test_an_unreadable_commercial_config_refuses_never_reads_as_none(
    client, install_network, monkeypatch, session_state
):
    """GK #1079: a commercial config the model refuses is a typed refusal, never "no
    shortfall assets". The IC key is pinned so the route's check passes."""
    import routers.results as R
    from services.business_case import linked_outputs as L

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    stored_key = client.get(IC_URL).json()["report"]["current_hash"]
    monkeypatch.setattr(R, "_ic_current_assumptions", lambda: ((stored_key, {}), None))
    called = []
    monkeypatch.setattr(L, "lost_production", lambda *a, **k: called.append(1))
    st = session_state(client)
    st["solver_config"] = dataclasses.replace(
        st["solver_config"], commercial={"poc_link": 12, "bogus": True}
    )
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert (
        body["status"] == "refused"
        and body["error_code"] == "commercial_config_invalid"
    )
    assert called == [] and st.get("business_case_report") is None


def test_an_abort_landing_at_the_store_stores_nothing(
    client, install_network, monkeypatch, session_state
):
    import services.business_case.runner as RN

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    st = session_state(client)
    real = RN.report_body

    def late_abort(**kw):
        st["business_case"]["stop_event"].set()  # after the last stage check
        return real(**kw)

    monkeypatch.setattr(RN, "report_body", late_abort)
    assert client.post(BC_URL).status_code == 200
    assert _bc_poll(client)["status"] == "aborted"
    assert st.get("business_case_report") is None


def test_c6_a_break_even_without_a_root_is_not_established_end_to_end(
    client, install_network, monkeypatch, session_state
):
    import services.finance.business_case as BCM

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(break_even_line="product")).status_code == 200
    monkeypatch.setattr(
        BCM,
        "break_even",
        lambda *a, **k: {
            "status": "break_even_not_bracketed",
            "price_scale": None,
            "price": None,
            "money_year": None,
        },
    )
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert body["status"] == "done", body
    be = body["report"]["break_even"]
    assert be["status"] == "break_even_not_bracketed" and be["price"] is None
    assert be["line"] == "product"
    rows = _sheet(client)["rows"]
    assert rows["Price at NPV 0"][:2] == ["not_established", "break_even_not_bracketed"]


def test_c6_kw_per_gpu_missing_reads_not_established_in_the_workbook(
    client, install_network, monkeypatch
):
    from services.business_case import linked_outputs as L

    _with_ic(client, install_network, monkeypatch)
    monkeypatch.setattr(
        L,
        "resolve",
        lambda line, n, solve, **p: L.NotEvaluated("it_energy", "kw_per_gpu_missing"),
    )
    gpu = _line(
        name="gpu", unit="GPU_h", quantity=None, quantity_source="linked:it_energy@1"
    )
    assert _put(client, _bc(gpu)).status_code == 200
    assert client.post(BC_URL).status_code == 200
    assert _bc_poll(client)["status"] == "done"
    rows = _sheet(client)["rows"]
    assert rows["ebitda_margin_gross"][0] == "not_established"
    assert rows["revenue"][0] == "not_established"


def test_a_report_without_a_current_ic_report_reads_stale(
    client, install_network, monkeypatch, session_state
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    assert client.post(BC_URL).status_code == 200
    assert _bc_poll(client)["status"] == "done"
    session_state(client)["investment_case_report"] = None
    st = client.get(BC_URL).json()["staleness"]
    assert st["stale"] is True and st["reason"] == "investment_case_missing"


# ── mutation-run gaps: every stage's abort check, the validators, the sheet cells ──


def _abort_in(st, fn):
    def wrapped(*a, **k):
        st["business_case"]["stop_event"].set()
        return fn(*a, **k)

    return wrapped


@pytest.mark.parametrize(
    "stage", ["rebuild_ic_case", "read_outputs", "build", "run_engine", "break_even"]
)
def test_an_abort_in_each_stage_stores_nothing_and_finishes_no_stage(
    client, install_network, monkeypatch, session_state, stage
):
    import routers.results as R
    import services.finance.business_case as BCM

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(break_even_line="product")).status_code == 200
    st = session_state(client)
    if stage == "rebuild_ic_case":
        real_build = R._ic_build_case
        monkeypatch.setattr(
            R,
            "_ic_build_case",
            lambda *a, **k: _abort_in(st, real_build(*a, **k)),
        )
    elif stage == "read_outputs":
        real_read = R._bc_read_solve
        monkeypatch.setattr(R, "_bc_read_solve", lambda h: _abort_in(st, real_read(h)))
    else:
        name = {"build": "build_business_case", "run_engine": "kpis"}.get(
            stage, "break_even"
        )
        monkeypatch.setattr(BCM, name, _abort_in(st, getattr(BCM, name)))
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert body["status"] == "aborted", body
    assert body["stage"] == stage and stage not in body["stages_done"]
    assert st.get("business_case_report") is None


def test_a_stored_ic_report_without_a_case_hash_refuses(
    client, install_network, monkeypatch, session_state
):
    import routers.results as R

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    rep = session_state(client)["investment_case_report"]
    rep["sections"]["project"]["payload"]["provenance"]["finance_case_hash"] = None
    monkeypatch.setattr(R, "_ic_case_hash", lambda case: None)
    r = client.post(BC_URL)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "investment_case_missing"
    assert session_state(client).get("business_case") is None


@pytest.mark.parametrize(
    "capacity", [{"": 1.0}, {"pv": 0.0}, {"pv": float("inf")}, {"pv": float("nan")}]
)
def test_a_capacity_must_name_an_asset_and_be_finite_above_0(capacity):
    from pydantic import ValidationError

    from services.business_case.runner import BusinessCaseConfig

    with pytest.raises(ValidationError, match="capacity of"):
        BusinessCaseConfig.model_validate({**_bc(), "capacity": capacity})


def test_the_sheet_cells_name_each_reason_and_unit():
    import openpyxl

    from services.results_workbook.business_case import (
        business_case_text,
        write_business_case_sheet,
    )

    assert "investment_case_missing" in business_case_text(
        {"basis": {}},
        {"stale": True, "changed": [], "reason": "investment_case_missing"},
    )
    assert "not readable now" in business_case_text({"basis": {}}, {"stale": True})
    assert business_case_text({"basis": {}}, None).startswith("stale")
    report = {
        "basis": {"label": "the project on its own"},
        "kpis": {
            "a": {"status": "ok", "value": 1.5, "unit": "EUR/MW"},
            "b": {"status": "not_established", "reason": None, "unit": None},
        },
    }
    wb = openpyxl.Workbook()
    write_business_case_sheet(wb, report, {"stale": False})
    rows = {
        r[0].value: [c.value for c in r[1:]]
        for r in wb["Business case"].iter_rows()
        if r[0].value
    }
    assert rows["a"][:3] == [1.5, "ok", "EUR/MW"]
    assert rows["b"][:2] == ["not_established", "not_established"]
    assert rows["b"][2] in (None, "")
    assert rows["Price at NPV 0"][:2] == ["not_established", "not_established"]


# ── GK B (54) D1, D2, M8: the under-lock re-checks, each pinned at lock entry ──


class _EnteringLock:
    """Wraps a lock; `on_enter` runs as the lock is taken, so a test can change the
    world exactly between a check made before the lock and one made under it."""

    def __init__(self, real, on_enter):
        self._real, self._on_enter = real, on_enter

    def acquire(self, *a, **k):
        self._on_enter()
        return self._real.acquire(*a, **k)

    def release(self):
        return self._real.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


@pytest.mark.parametrize(
    "at_lock_entry, reason",
    [("moved", "inputs_changed"), ("unreadable", "network_busy")],
)
def test_c4_the_ic_key_is_rechecked_under_the_network_lock(
    client, install_network, monkeypatch, session_state, at_lock_entry, reason
):
    """The IC key moves (or turns unreadable) as the worker takes the network lock: only
    a re-check made under the lock sees it, and the run refuses before any read."""
    import routers.results as R
    from services.business_case import linked_outputs as L
    from services.pypsa_service import PyPSAService

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    real_key = R._ic_current_assumptions
    state = {"armed": False, "moved": False}

    def key():
        if not state["moved"]:
            return real_key()
        if at_lock_entry == "unreadable":
            return None, "network_busy"
        (h, *rest), why = real_key()
        return (h + "-moved", *rest), why

    def on_enter():
        if state["armed"]:
            state["moved"] = True

    real_build = R._ic_build_case

    def build(*a, **k):
        inner = real_build(*a, **k)

        def rebuild():
            state["armed"] = True  # in the worker, after the route's check
            return inner()

        return rebuild

    real_get_lock = PyPSAService.get_lock
    monkeypatch.setattr(R, "_ic_current_assumptions", key)
    monkeypatch.setattr(R, "_ic_build_case", build)
    monkeypatch.setattr(
        PyPSAService,
        "get_lock",
        classmethod(lambda cls: _EnteringLock(real_get_lock(), on_enter)),
    )
    read = []
    monkeypatch.setattr(L, "bind", lambda *a, **k: read.append(1))
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert body["status"] == "refused", body
    assert body["error_code"] == "investment_case_missing"
    assert body["refusal"]["reason"] == reason
    assert state["moved"] and read == []
    assert session_state(client).get("business_case_report") is None


def test_the_put_rechecks_the_solve_under_the_state_lock(
    client, install_network, monkeypatch, session_state
):
    """A solve that starts as the PUT takes the state lock: the re-check under the lock
    refuses it, and nothing is stored."""
    import contextlib

    from services.pypsa_service import PyPSAService
    from tests.test_investment_case_routes import _live_thread

    install_network(build_network())
    st = session_state(client)
    stack = contextlib.ExitStack()
    real_get = PyPSAService.get_solver_state_lock
    entered = {"n": 0}

    def on_enter():
        import sys

        f = sys._getframe(1)
        while f is not None and f.f_code.co_filename == __file__:
            f = f.f_back  # past this wrapper, to the code that takes the lock
        if f is not None and f.f_code.co_name == "put_business_case_inputs":
            entered["n"] += 1
            if entered["n"] == 1:  # the PUT's own `with`, after its early check
                stack.enter_context(_live_thread(st, "thread", record=False))

    monkeypatch.setattr(
        PyPSAService,
        "get_solver_state_lock",
        classmethod(lambda cls: _EnteringLock(real_get(), on_enter)),
    )
    try:
        r = _put(client, _bc())
    finally:
        stack.close()
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "solver_in_flight"
    assert entered["n"] == 1
    assert st["solver_config"].business_case is None


def test_c3_a_stored_ic_report_without_its_assumptions_hash_is_no_report(
    client, install_network, monkeypatch, session_state
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    session_state(client)["investment_case_report"]["assumptions_hash"] = None
    r = client.post(BC_URL)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "no_report"
