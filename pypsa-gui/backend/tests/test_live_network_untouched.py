"""
P22.9-BE bug 3 — a study or sweep must leave the live network's tables as the
user left them (spec 2026-09-27-guided-mode §2.1), plus the bug-2 backend
evidence (§2.2).

Found in the Expert click-through: after an FMEA sweep on the data-center
template every bus read ``control`` Slack, a ``sub_network`` id and a
``generator`` name it did not have before. The sweep solves the LIVE network in
place, and PyPSA's optimize post-processing runs ``determine_network_topology``
when no ``SubNetwork`` exists — writing those three Bus columns and adding
``SubNetwork`` rows. The closing base re-solve restored dispatch and
capacities, not those columns. ``preserve_bus_topology`` now wraps the whole
sweep, including that closing re-solve.

Every check here goes through HTTP (``TestClient``), so the network compared is
the one the session context resolves — the one the user's tables show.
"""
from __future__ import annotations

import time

import pytest

from tests.test_energy_hub_templates_e2e import _poll, _project_from_template

TID = "eh_datacenter"


def _tables(client) -> dict:
    out = {}
    for comp in ("buses", "links"):
        r = client.get(f"/api/network/{comp}")
        assert r.status_code == 200, r.text
        out[comp] = sorted(r.json(), key=lambda row: row["name"])
    return out


# The one column the sweep's closing base re-solve is MEANT to write: it is
# the user's own config solved (the same solve that leaves dispatch `fresh`,
# see the bug-2 test below), so the Link rows carry that solve's `p_nom_opt`
# exactly as a foreground solve would. It is an optimisation OUTPUT, not
# something the user set, and it is pinned separately rather than skipped:
# on a fixed link it must equal the link's own `p_nom`.
_RESOLVE_OUTPUTS = {"links": {"p_nom_opt"}}


def _assert_tables_equal(after: dict, before: dict, *,
                         closing_resolve: bool = False) -> None:
    for comp in ("buses", "links"):
        assert [r["name"] for r in after[comp]] == [
            r["name"] for r in before[comp]], comp
        skip = _RESOLVE_OUTPUTS.get(comp, set()) if closing_resolve else set()
        for a, b in zip(after[comp], before[comp]):
            diff = {k: (b.get(k), a.get(k)) for k in set(a) | set(b)
                    if k not in skip and a.get(k) != b.get(k)}
            assert not diff, f"{comp}/{a['name']} changed (before, after): {diff}"
    if closing_resolve:
        for a in after["links"]:
            if not a["p_nom_extendable"]:
                assert a["p_nom_opt"] == pytest.approx(a["p_nom"]), a["name"]


def _start_sweep(client, name: str) -> None:
    reg = client.get(f"/api/projects/{name}/stress_scenarios").json()
    assert reg["error"] is None and reg["scenarios"]
    r = client.post("/api/results/fmea_sweep",
                    json={"scenarios": reg["scenarios"]})
    assert r.status_code == 200, r.text


def _run_sweep(client, name: str) -> dict:
    _start_sweep(client, name)
    sweep = _poll(client, "/api/results/fmea_sweep")
    assert sweep["status"] == "done", sweep
    assert sweep["base_restored"] is True, sweep
    return sweep


@pytest.fixture
def template(client, tmp_path, monkeypatch, tmp_projects_dir):
    return _project_from_template(client, tmp_path, monkeypatch, TID)


@pytest.mark.live_solve
def test_fmea_sweep_leaves_the_live_tables_equal(client, template, session_ctx):
    before = _tables(client)
    live = session_ctx(client).network
    sub_before = sorted(live.sub_networks.index)
    _run_sweep(client, template)
    _assert_tables_equal(_tables(client), before, closing_resolve=True)
    # No table serves SubNetwork, so the rows the topology pass added are
    # checked on the session's own network object.
    assert sorted(session_ctx(client).network.sub_networks.index) == sub_before


@pytest.mark.live_solve
def test_fmea_sweep_restores_topology_columns_after_abort(client, template):
    before = _tables(client)
    _start_sweep(client, template)
    r = client.post("/api/results/fmea_sweep/abort")
    assert r.status_code == 200, r.text
    sweep = _poll(client, "/api/results/fmea_sweep")
    # The stop event is checked between contingencies, after the base solve,
    # so the base solve (the one that writes the topology) always ran.
    assert sweep["status"] == "aborted", sweep
    # The closing re-solve still runs on the abort path.
    assert sweep["base_restored"] is True, sweep
    _assert_tables_equal(_tables(client), before, closing_resolve=True)


@pytest.mark.live_solve
def test_eh_study_leaves_the_live_tables_equal(client, template):
    # A GUARD: the study runs on a private copy and passes without the fix.
    # Kept so a future stage that solves the live object fails here.
    meta = client.get(f"/api/projects/{template}/eh_template").json()
    body = {"archetype": meta["recommended_archetype"],
            "stages": ["apply_pack", "ens_solve", "frontier", "fmea_top",
                       "assemble"],
            "budget_solves": 60}
    if meta["pack_overrides"]:
        body["pack_overrides"] = meta["pack_overrides"]
    before = _tables(client)
    r = client.post("/api/results/eh_study", json=body)
    assert r.status_code == 200, r.text
    study = _poll(client, "/api/results/eh_study")
    assert study["status"] == "done", study.get("error")
    _assert_tables_equal(_tables(client), before)


@pytest.mark.live_solve
def test_a_foreground_solve_still_works_after_a_sweep(client, template):
    _run_sweep(client, template)
    r = client.post("/api/simulation/run")
    assert r.status_code in (200, 202), r.text
    deadline = time.time() + 600
    while time.time() < deadline:
        st = client.get("/api/simulation/status").json()
        if st.get("status") in ("completed", "failed", "aborted"):
            break
        time.sleep(0.2)
    assert st["status"] == "completed", st
    assert st["condition"] == "optimal", st
    assert st["dispatch"] == "fresh", st


@pytest.mark.live_solve
def test_after_a_sweep_status_reports_dispatch_fresh_without_a_foreground_condition(
        client, template):
    # Bug 2's backend state, pinned: the sweep's closing base re-solve leaves
    # dispatch on the live network (`fresh`) but is not recorded as a
    # foreground solve (`condition` / `solve_time` null). The Expert greeting
    # (P22.9-FE) says exactly this; if the backend ever starts recording the
    # re-solve as a foreground solve, this fails and that copy is revisited.
    st = client.get("/api/simulation/status").json()
    assert st["condition"] is None and st["solve_time"] is None, st
    _run_sweep(client, template)
    st = client.get("/api/simulation/status").json()
    assert st["dispatch"] == "fresh", st
    assert st["condition"] is None, st
    assert st["solve_time"] is None, st


def test_preserve_bus_topology_restores_on_an_exception():
    # The unit contract under the HTTP tests: the columns come back and the
    # added SubNetwork rows go, even when the body raises.
    import pandas as pd
    import pypsa

    from services.adequacy.sweep import preserve_bus_topology

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", ["a", "b"])
    n.add("Generator", "g", bus="a", p_nom=10.0)
    n.add("Line", "l", bus0="a", bus1="b", x=0.1, s_nom=10.0)
    before = n.buses[["control", "sub_network", "generator"]].copy()
    line_before = n.lines["sub_network"].copy()
    assert n.sub_networks.empty
    with pytest.raises(RuntimeError, match="boom"):
        with preserve_bus_topology(n):
            n.determine_network_topology()
            n.buses.loc[:, "control"] = "Slack"
            assert not n.sub_networks.empty
            raise RuntimeError("boom")
    assert n.buses[["control", "sub_network", "generator"]].equals(before)
    assert n.lines["sub_network"].equals(line_before)
    assert n.sub_networks.empty


@pytest.mark.live_solve
def test_frontier_sweep_restores_bus_topology():
    # `run_frontier_sweep` solves the network it is handed in place (the
    # frontier route hands it the live one), closing re-solve included.
    import queue

    from services.adequacy.frontier import run_frontier_sweep
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig
    from tests.test_adequacy_frontier import VOLL, _network

    n = _network()
    PyPSAService.set_network(n)
    cols = ["control", "sub_network", "generator"]
    before = n.buses[cols].copy()
    res = run_frontier_sweep(
        n, PyPSAService.get_lock(),
        SolverConfig(solver_name="highs", voll=VOLL), [200.0, 50.0],
        log_queue=queue.SimpleQueue())
    assert res["base_restored"] is True, res
    assert n.buses[cols].equals(before), n.buses[cols]
    assert n.sub_networks.empty


@pytest.mark.live_solve
def test_coupling_loop_leaves_the_live_buses_equal(client, install_network):
    # The loop runners solve the LIVE network per iterate and in the closing
    # restore; the worker runs inside `preserve_bus_topology`.
    from tests.test_adequacy_coupling_endpoint import (
        DRAWS, LOOP_URL, SEED, _poll as _poll_loop, _setup)

    _setup(client, install_network)
    before = _tables(client)
    r = client.post(LOOP_URL, json={"target_lole_h": 4.0, "draws": DRAWS,
                                    "seed": SEED, "max_solves": 2,
                                    "eps0": 50.0})
    assert r.status_code == 200, r.text
    body = _poll_loop(client, timeout=600.0)
    assert body["base_restored"] is True, body
    assert _tables(client)["buses"] == before["buses"]


def _branchy_network():
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    n.add("Bus", "hv", v_nom=110.0)
    n.add("Bus", "mv1", v_nom=20.0)
    n.add("Bus", "mv2", v_nom=20.0)
    n.add("Generator", "grid", bus="hv", p_nom=100.0, marginal_cost=20.0)
    n.add("Transformer", "tr", bus0="hv", bus1="mv1", x=0.1, s_nom=80.0)
    n.add("Line", "ln", bus0="mv1", bus1="mv2", x=0.05, r=0.01, s_nom=60.0)
    n.add("Load", "ld", bus="mv2", p_set=30.0)
    return n


@pytest.mark.live_solve
def test_contingency_sweep_restores_branch_sub_network():
    # `determine_network_topology` also writes `sub_network` on every passive
    # branch (Line, Transformer); the sweep must put those back too.
    import queue

    from services.adequacy.sweep import run_contingency_sweep
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = _branchy_network()
    PyPSAService.set_network(n)
    bus_cols = ["control", "sub_network", "generator"]
    before_bus = n.buses[bus_cols].copy()
    before_branch = {c: n.static(c)["sub_network"].copy()
                     for c in sorted(n.passive_branch_components)}
    assert set(before_branch) >= {"Line", "Transformer"}
    res = run_contingency_sweep(
        n, PyPSAService.get_lock(),
        SolverConfig(solver_name="highs", voll=3000.0), [],
        log_queue=queue.SimpleQueue())
    assert res["base_restored"] is True, res
    assert n.buses[bus_cols].equals(before_bus), n.buses[bus_cols]
    for c, before in before_branch.items():
        after = n.static(c)["sub_network"]
        assert after.equals(before), (c, before.tolist(), after.tolist())
    assert n.sub_networks.empty
