"""Provenance-graph 13: a value edit through a component PUT route marks the last run stale, never not_run.

Slice 04 (D1 a): "a value edit keeping the dispatch shape reads stale". The route middleware cleared the
dispatch on every accepted `/api/network/*` write, so the flow read `not_run` and Results lost the run.
"""

import pandas as pd
import pypsa
import pytest

from tests.test_project_flow import _flow, _kinds, _wait_idle, site  # noqa: F401


def _all_components_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Carrier", "AC")
    n.add("Carrier", "gas")
    n.add("Bus", "a", carrier="AC")
    n.add("Bus", "b", carrier="AC")
    n.add("Bus", "c", carrier="AC")
    n.add("Generator", "g", bus="a", carrier="gas", p_nom=100.0, marginal_cost=20.0)
    n.add("Load", "l", bus="b", p_set=30.0)
    n.add("Line", "ab", bus0="a", bus1="b", x=0.1, r=0.01, s_nom=200.0)
    n.add("Link", "bc", bus0="b", bus1="c", p_nom=50.0)
    n.add("StorageUnit", "su", bus="b", p_nom=10.0, max_hours=2.0, marginal_cost=1.0)
    n.add("Store", "st", bus="c", e_nom=10.0, e_cyclic=True)
    n.add("Load", "lc", bus="c", p_set=5.0)
    return n


# (route, name, field, new value): each edit changes one number the solve read.
ROUTES = [
    ("generators", "g", "marginal_cost", 25.0),
    ("loads", "l", "p_set", 35.0),
    ("lines", "ab", "s_nom", 150.0),
    ("links", "bc", "p_nom", 40.0),
    ("storage_units", "su", "p_nom_max", 20.0),
    ("stores", "st", "e_nom", 12.0),
]


@pytest.mark.parametrize("route,name,field,value", ROUTES, ids=[r[0] for r in ROUTES])
def test_a_value_edit_marks_the_last_run_stale(client, site, route, name, field, value):  # noqa: F811
    project = site(_all_components_network(), name=f"pg13_{route}")
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    assert _kinds(_flow(client, project))["optimisation"] == "fresh"

    row = next(
        r for r in client.get(f"/api/network/{route}").json() if r["name"] == name
    )
    r = client.put(f"/api/network/{route}/{name}", json={**row, field: value})
    assert r.status_code == 200, r.text

    assert _kinds(_flow(client, project))["optimisation"] == "stale"
    # The figures are withheld, never recombined with the edited inputs (owner decision PG13-STALE-1).
    assert client.get("/api/results/generators").status_code == 204


def test_the_expert_table_bulk_edit_marks_the_last_run_stale(client, site):  # noqa: F811
    """The Expert table saves through `PATCH /_bulk`, not the per-row PUT."""
    project = site(_all_components_network(), name="pg13_bulk")
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "StorageUnit",
            "names": ["su"],
            "updates": {"p_nom_max": 20.0},
        },
    )
    assert r.status_code == 200, r.text
    assert _kinds(_flow(client, project))["optimisation"] == "stale"
    assert client.get("/api/results/generators").status_code == 204


def test_a_value_edit_marks_the_investment_case_out_of_date(
    client, install_network, session_ctx, monkeypatch
):
    """The case priced the run the edit makes stale, so its report reads out of date too (issue 13)."""
    from tests.test_investment_case_routes import (
        STUDY_URL,
        _fake_build,
        _poll,
        _put_fin,
        _s2_inputs,
        _solved,
    )

    _fake_build(monkeypatch)
    _solved(install_network)
    assert _put_fin(client, _s2_inputs()).status_code == 200
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    assert client.post(STUDY_URL, json={}).status_code == 200
    assert _poll(client)["report"]["stale"] is False

    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "Generator",
            "names": ["gas"],
            "updates": {"p_nom_max": 7.0},
        },
    )
    assert r.status_code == 200, r.text
    rep = client.get(STUDY_URL).json()["report"]
    assert rep["stale"] is True and rep["changed"] == ["dispatch"]
    assert rep["verdict"]["state"] == "stale"

    # Every reader of the case's staleness agrees, and new work refuses the stale run (GK B2 F2, F3).
    from fastapi import HTTPException

    from routers.results import _bc_ic_check, _bc_ic_current
    from services import chat_tools

    assert _bc_ic_current() == "investment_case_out_of_date"
    with pytest.raises(HTTPException) as exc:
        _bc_ic_check()
    assert (
        exc.value.status_code == 409
        and exc.value.detail["code"] == "investment_case_missing"
    )
    r = client.post(STUDY_URL, json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_solved"
    with pytest.raises(HTTPException) as exc:
        chat_tools.solve_ppa_price(target_irr=0.1, target_year=10)
    assert exc.value.detail["error_kind"] == "investment_case_not_solved"


# ── Edits the keep-the-run rule must not reach ───────────────────────────────


def _solved_site(client, site, name):  # noqa: F811
    project = site(_all_components_network(), name=name)
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    assert _kinds(_flow(client, project))["optimisation"] == "fresh"
    return project


READERS = [
    "generators",
    "lines",
    "headline_kpis",
    "asset_economics",
    "energy_flows",
    "carrier_kpis",
]


@pytest.mark.parametrize(
    "route,name,field,value",
    [
        ("generators", "g", "marginal_cost", 25.0),
        ("loads", "l", "bus", "a"),
    ],
    ids=["value-edit", "topology-edit"],
)
def test_the_readers_withhold_a_stale_run_and_serve_again_after_a_re_run(
    client,
    site,  # noqa: F811
    route,
    name,
    field,
    value,  # noqa: F811
):
    """Old dispatch must never be recombined with edited inputs (GK B2 F1, owner decision PG13-STALE-1)."""
    project = _solved_site(client, site, f"pg13_readers_{field}")
    before = {r: client.get(f"/api/results/{r}").status_code for r in READERS}
    assert before["generators"] == 200 and before["lines"] == 200, before

    row = next(
        r for r in client.get(f"/api/network/{route}").json() if r["name"] == name
    )
    assert (
        client.put(
            f"/api/network/{route}/{name}", json={**row, field: value}
        ).status_code
        == 200
    )
    assert _kinds(_flow(client, project))["optimisation"] == "stale"
    for reader, status in before.items():
        if status != 200:
            continue
        r = client.get(f"/api/results/{reader}")
        assert r.status_code == 204, (reader, r.status_code)
        assert r.headers.get("x-results-state") == "out_of_date", reader

    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    assert _kinds(_flow(client, project))["optimisation"] == "fresh"
    for reader, status in before.items():
        if status == 200:
            assert client.get(f"/api/results/{reader}").status_code == 200, reader


def test_a_bus_edit_still_clears_the_run(client, site):  # noqa: F811
    """Only the six value-edit routes keep the run; any other route clears it as before."""
    project = _solved_site(client, site, "pg13_bus")
    row = next(r for r in client.get("/api/network/buses").json() if r["name"] == "a")
    r = client.put("/api/network/buses/a", json={**row, "v_nom": 400.0})
    assert r.status_code == 200, r.text
    assert _kinds(_flow(client, project))["optimisation"] == "not_run"


def test_a_bulk_edit_of_another_class_still_clears_the_run(client, site):  # noqa: F811
    project = _solved_site(client, site, "pg13_bulk_bus")
    r = client.patch(
        "/api/network/_bulk",
        json={"component_class": "Bus", "names": ["a"], "updates": {"v_nom": 400.0}},
    )
    assert r.status_code == 200, r.text
    assert _kinds(_flow(client, project))["optimisation"] == "not_run"


def test_deleting_a_component_still_clears_the_run(client, site):  # noqa: F811
    """A write other than PUT on a value-edit route (here DELETE) changes the dispatch shape."""
    project = _solved_site(client, site, "pg13_delete")
    r = client.delete("/api/network/loads/lc")
    assert r.status_code in (200, 204), r.text
    assert _kinds(_flow(client, project))["optimisation"] == "not_run"


def test_ac_power_flow_refuses_a_stale_run(client, site):  # noqa: F811
    """The load flow fixes dispatch from the last solve; after a value edit that dispatch is out of date."""
    project = _solved_site(client, site, "pg13_acpf")
    row = next(r for r in client.get("/api/network/loads").json() if r["name"] == "l")
    assert (
        client.put("/api/network/loads/l", json={**row, "p_set": 35.0}).status_code
        == 200
    )
    assert _kinds(_flow(client, project))["optimisation"] == "stale"
    r = client.post("/api/simulation/run_ac_pf")
    assert r.status_code == 400, r.text
    assert "inputs changed since the last solve" in r.text


@pytest.mark.parametrize(
    "route,name,new",
    [("loads", "l", "l2"), ("generators", "g", "g2")],
    ids=["two-member-class", "only-member-of-class"],
)
def test_a_rename_clears_the_run(client, site, route, name, new):  # noqa: F811
    """A rename drops the row's results; for a class's only member nothing else shows it (GK B2 F4)."""
    project = _solved_site(client, site, f"pg13_rename_{route}")
    row = next(
        r for r in client.get(f"/api/network/{route}").json() if r["name"] == name
    )
    r = client.put(f"/api/network/{route}/{name}", json={**row, "name": new})
    assert r.status_code == 200, r.text
    assert _kinds(_flow(client, project))["optimisation"] == "not_run"


# ── The investment-case verdict merge ────────────────────────────────────────


def _stale_run(monkeypatch, *, dispatch="fresh", state="stale"):
    from types import SimpleNamespace

    from services import dispatch_status
    from services.provenance_graph import runs

    monkeypatch.setattr(dispatch_status, "edit_kept", lambda n: True)
    monkeypatch.setattr(
        runs, "read", lambda cached=True: SimpleNamespace(dispatch=dispatch)
    )
    monkeypatch.setattr(
        runs,
        "results_verdicts",
        lambda read: {"optimisation": SimpleNamespace(state=state)},
    )


def test_a_stale_run_keeps_the_other_verdict_fields(monkeypatch):
    from routers.results import _ic_report_after_stale_run

    _stale_run(monkeypatch)
    report = {
        "present": True,
        "stale": False,
        "verdict": {"state": "fresh", "broken": ["x"]},
    }
    out = _ic_report_after_stale_run(report)
    assert out["stale"] is True and out["changed"] == ["dispatch"]
    assert out["verdict"] == {"state": "stale", "broken": ["x"]}
    # A report with no verdict gets one.
    out = _ic_report_after_stale_run({"present": True, "stale": False})
    assert out["verdict"] == {"state": "stale"}


@pytest.mark.parametrize(
    "report,run",
    [
        ({"present": False, "stale": False}, {}),
        ({"present": True, "stale": True}, {}),
        ({"present": True, "stale": False}, {"state": "fresh"}),
        ({"present": True, "stale": False}, {"dispatch": "none"}),
    ],
    ids=["no-report", "already-stale", "run-fresh", "no-dispatch"],
)
def test_the_report_is_left_alone_unless_the_run_is_stale(monkeypatch, report, run):
    from routers.results import _ic_report_after_stale_run

    _stale_run(monkeypatch, **run)
    assert _ic_report_after_stale_run(report) == report


# ── GK B2 G1-G4 ──────────────────────────────────────────────────────────────

MARKER = "assistant_edit_kept_run"


def _ctx(client, session_ctx):
    """The test client's own project context: `get_network()` unbound returns the empty process foreground."""
    return session_ctx(client)


def test_the_asset_figures_are_withheld_after_a_kept_edit(client, site):  # noqa: F811
    """`/results/asset/...` gated on shape only and served vom 7700 / net -4900 after a marginal-cost edit (G1)."""
    _solved_site(client, site, "pg13_asset")
    url = "/api/results/asset/Generator/g?category=economics"

    def economics():
        body = client.get(url).json()
        status = next(c for c in body["categories"] if c["id"] == "economics")
        return body, status

    body, status = economics()
    assert status["status"] == "ok" and body["scalars"], status
    row = next(
        r for r in client.get("/api/network/generators").json() if r["name"] == "g"
    )
    assert (
        client.put(
            "/api/network/generators/g", json={**row, "marginal_cost": 25.0}
        ).status_code
        == 200
    )
    body, status = economics()
    assert status["status"] == "blocked" and "out of date" in status["reason"], status
    assert not body["scalars"] and not body["series"]


def test_the_verdict_fails_closed_once_a_run_was_kept(
    client, site, session_ctx, monkeypatch
):  # noqa: F811
    """A failed verdict read must withhold, not serve (G2); with no marker nothing was kept (ADR-0001)."""
    from services.provenance_graph import runs

    _solved_site(client, site, "pg13_closed")
    ctx = _ctx(client, session_ctx)
    n = ctx.network

    def boom(ctx=None):
        raise RuntimeError("verdict unreadable")

    monkeypatch.setattr(runs, "read_runs", boom)
    assert runs.kept_run_stale(ctx) is False
    n.meta[MARKER] = True
    assert runs.kept_run_stale(ctx) is True


def test_the_marker_is_dropped_by_a_solve_a_clear_and_an_undone_edit(
    client, site, session_ctx
):  # noqa: F811
    """A leftover marker would withhold billing on a later commercial edit (G4)."""
    from services.dispatch_status import clear_dispatch
    from services.provenance_graph import runs

    project = _solved_site(client, site, "pg13_marker")
    ctx = _ctx(client, session_ctx)

    def edit(mc):
        row = next(
            r for r in client.get("/api/network/generators").json() if r["name"] == "g"
        )
        assert (
            client.put(
                "/api/network/generators/g", json={**row, "marginal_cost": mc}
            ).status_code
            == 200
        )

    # An edit and its undo: the verdict reads fresh again, so the marker goes the next time anything asks.
    edit(25.0)
    assert ctx.network.meta[MARKER] is True and runs.kept_run_stale(ctx) is True
    edit(20.0)
    assert _kinds(_flow(client, project))["optimisation"] == "fresh"
    assert runs.kept_run_stale(ctx) is False and MARKER not in ctx.network.meta
    assert client.get("/api/results/generators").status_code == 200

    # A solve drops it.
    edit(25.0)
    assert MARKER in ctx.network.meta
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    assert MARKER not in ctx.network.meta

    # So does clearing the dispatch.
    edit(30.0)
    assert MARKER in ctx.network.meta
    clear_dispatch(ctx.network)
    assert MARKER not in ctx.network.meta


@pytest.mark.parametrize("kept", [True, False], ids=["kept-and-stale", "not-kept"])
def test_the_business_case_gate_reads_the_merged_verdict(monkeypatch, kept):
    """The BC reads the investment case through `after_stale_run`, not the raw key (G3)."""
    from fastapi import HTTPException

    import routers.results as R
    from services.business_case import runner
    from services.finance import investment_case_runner as icr
    from services.provenance_graph import runs

    monkeypatch.setattr(runner, "ic_report_key", lambda stored: ("a", "c"))
    monkeypatch.setattr(R, "_ic_current_assumptions", lambda: (("k", {}), None))
    monkeypatch.setattr(
        icr,
        "staleness",
        lambda stored, current, reason=None: {"present": True, "stale": False},
    )
    monkeypatch.setattr(runs, "kept_run_stale", lambda ctx=None: kept)
    if kept:
        assert R._bc_ic_current() == "investment_case_out_of_date"
        with pytest.raises(HTTPException) as exc:
            R._bc_ic_check()
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "investment_case_missing"
    else:
        assert R._bc_ic_current() is None
        assert R._bc_ic_check() == ("a", "c")
