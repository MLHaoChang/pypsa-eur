"""Owner decision PF-SAVE-1 (study harness 50, GK A (29) F1-F7): after the AC
power flow the live network, and the network.nc saved from it, keep the LP's
dispatch; the flow's tables live only under `ac_pf_results`."""

from __future__ import annotations

import copy
import queue
import threading

import pandas as pd
import pypsa
import pytest

from services import ac_pf_service
from services.pypsa_service import PyPSAService
from tests.test_ac_pf_second_run import _run_and_pf
from tests import test_project_flow as _flow
from tests.test_run_separability import _wait_idle

# Fixtures shared with the project-flow tests.
site, set_cfg = _flow.site, _flow.set_cfg

PF_ONLY = sorted(ac_pf_service._PF_ONLY_RESULTS)

# The tables only the AC power flow writes, pinned here rather than read from
# the production set, so dropping one from it fails the sweep (GK A 29).
PF_ONLY_PINNED = frozenset(
    {
        "generators_t.q",
        "loads_t.q",
        "storage_units_t.q",
        "stores_t.q",
        "lines_t.q0",
        "lines_t.q1",
        "transformers_t.q0",
        "transformers_t.q1",
        "buses_t.q",
        "buses_t.v_mag_pu",
        "shunt_impedances_t.p",
        "shunt_impedances_t.q",
    }
)


def _ieee14() -> pypsa.Network:
    """Built here, as `test_project_flow.test_r4_ieee14` does: the shipped
    `network.nc` is gitignored and CI builds no template (GK A 2)."""
    import project_templates._build as classic

    return classic.build_ieee14()


def _every_frame(n) -> dict:
    """Every dynamic table of every component, keyed `<list_name>_t.<attr>`."""
    out = {}
    for c in n.components:
        dyn = getattr(n, f"{c.list_name}_t")
        for attr in list(dyn.keys()):
            out[f"{c.list_name}_t.{attr}"] = copy.deepcopy(dyn[attr])
    return out


def _assert_lp_or_emptied(n, before: dict):
    """GK A 1 / GK B M3: every dynamic table equals the LP's bit for bit, or is a
    declared PF-only table and is empty."""
    after = _every_frame(n)
    assert after.keys() == before.keys()
    for key, df in after.items():
        if key in PF_ONLY_PINNED:
            assert df.columns.empty, key
        else:
            pd.testing.assert_frame_equal(df, before[key], check_exact=True, obj=key)


def _reload(client, name):
    assert (
        client.post(f"/api/projects/{name}", params={"force": True}).status_code == 200
    )
    _reopen(client, name)


def _reopen(client, name):
    """Drop the in-memory context and open the project from its files."""
    with PyPSAService._registry_lock:
        for key, ctx in list(PyPSAService._contexts.items()):
            if ctx.loaded_project == name:
                PyPSAService._contexts.pop(key, None)
    assert client.post(f"/api/projects/{name}/activate").status_code == 200


def _assert_live_is_lp(n, lp: dict):
    for key, df in lp.items():
        acc, attr = key.split(".", 1)
        live = getattr(getattr(n, acc), attr)
        pd.testing.assert_frame_equal(live, df, check_exact=True, obj=key)


def test_the_saved_dispatch_is_the_lps_on_a_lossy_template(
    client, site, bound_ctx, project_storage_dir
):
    site(_ieee14(), name="pf_lossy")
    _run_and_pf(client)
    with bound_ctx(client):
        state = PyPSAService.get_solver_state()
        lp = state["lopf_results"]["generators_t.p"].copy()
        pf = state["ac_pf_results"]["generators_t.p"].copy()
        # Live right after the flow, before any save or reload.
        pd.testing.assert_frame_equal(
            PyPSAService.get_network().generators_t.p, lp, check_exact=True
        )
    # The fixture is lossy: the flow's slack moves off the LP dispatch.
    assert (pf - lp).abs().to_numpy().max() > 1e-3
    assert (
        client.post("/api/projects/pf_lossy", params={"force": True}).status_code == 200
    )
    # The saved file itself, read without the app's reload.
    saved = pypsa.Network(project_storage_dir("pf_lossy") / "network.nc").generators_t.p
    pd.testing.assert_frame_equal(
        saved, lp, check_exact=True, check_freq=False, check_names=False
    )
    _reopen(client, "pf_lossy")
    with bound_ctx(client):
        n = PyPSAService.get_network()
        state = PyPSAService.get_solver_state()
        # F1: bit-equal, live and in the record.
        pd.testing.assert_frame_equal(n.generators_t.p, lp, check_exact=True)
        pd.testing.assert_frame_equal(
            state["lopf_results"]["generators_t.p"], lp, check_exact=True
        )
        # The flow's values survive the reload under their own name only.
        pd.testing.assert_frame_equal(
            state["ac_pf_results"]["generators_t.p"], pf, check_exact=True
        )
        assert len(state["ac_pf_results"]["buses_t.v_mag_pu"].columns) == len(n.buses)
        assert n.buses_t.v_mag_pu.columns.empty
    # A load-flow screen reads the flow; the default screens read the LP.
    v = client.get("/api/results/voltages", params={"source": "ac_pf"})
    assert v.status_code == 200 and v.json()
    d = client.get("/api/results/generators")
    assert d.status_code == 200, d.text


def _ieee14_every_pf_table() -> pypsa.Network:
    """ieee14 plus one of each component the flow writes q (or shunt p) for and
    ieee14 lacks: a transformer to a 33 kV bus, a storage unit, a store and a
    shunt, so a prior flow fills all 12 PF-only tables (GK A 30)."""
    n = _ieee14()
    n.add("Bus", "Bus 14 LV", v_nom=33.0, carrier="AC")
    n.add(
        "Transformer",
        "T 14",
        bus0="Bus 14",
        bus1="Bus 14 LV",
        s_nom=100.0,
        x=0.1,
        r=0.01,
    )
    n.add("Load", "Load 14 LV", bus="Bus 14 LV", p_set=5.0, q_set=1.0)
    n.add(
        "StorageUnit",
        "SU 3",
        bus="Bus 3",
        p_nom=20.0,
        max_hours=4.0,
        q_set=2.0,
        carrier="AC",
    )
    n.add("Store", "Store 4", bus="Bus 4", e_nom=50.0, q_set=2.0, carrier="AC")
    n.add("ShuntImpedance", "Shunt 9", bus="Bus 9", g=0.01, b=0.19)
    return n


def _solved_ieee14(build=_ieee14) -> pypsa.Network:
    n = build()
    n.optimize(solver_name="highs")
    return n


def _master_stage(n):
    """The stage as master ran it (no restore): the flow's tables stay live."""
    from services.solver_service import SolverConfig

    real = ac_pf_service._restore_result_state
    ac_pf_service._restore_result_state = lambda n, snap: None
    try:
        return ac_pf_service.run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    finally:
        ac_pf_service._restore_result_state = real


@pytest.mark.parametrize("build", [_ieee14, _ieee14_every_pf_table])
@pytest.mark.parametrize("earlier_flow", [False, True])
def test_every_dynamic_frame_is_the_lps_or_an_emptied_pf_only_one(earlier_flow, build):
    """A sweep over every `n.<c>_t` table on a lossy template, not a list. With
    `earlier_flow` the network carries a previous flow's q, |V| and bus
    injections (saved before PF-SAVE-1) when the LP runs again; on
    `_ieee14_every_pf_table` that is every PF-only table, so dropping any one
    name from `_PF_ONLY_RESULTS` fails here (GK A 30)."""
    from services.solver_service import SolverConfig

    n = _solved_ieee14(build)
    if earlier_flow:
        _master_stage(n)
        assert not n.buses_t.q.columns.empty, "fixture: a flow's q is live"
        n.optimize(solver_name="highs")
        if build is _ieee14_every_pf_table:
            for key in PF_ONLY_PINNED:
                acc, attr = key.split(".", 1)
                df = getattr(getattr(n, acc), attr)
                assert df.abs().to_numpy().max() > 1e-6, f"fixture: {key} is live"
    before = _every_frame(n)
    out = ac_pf_service.run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    _assert_lp_or_emptied(n, before)
    for key in PF_ONLY_PINNED:
        acc, attr = key.split(".", 1)
        assert getattr(getattr(n, acc), attr).columns.empty, key
    # The flow moved the bus injections and wrote q; both are kept under its name.
    pf, lp = out["ac_pf_results"], out["lopf_results"]
    assert (pf["buses_t.p"] - before["buses_t.p"]).abs().to_numpy().max() > 1e-3
    assert not pf["buses_t.q"].columns.empty
    pd.testing.assert_frame_equal(
        lp["buses_t.p"], before["buses_t.p"], check_exact=True
    )


def test_an_output_table_outside_the_record_is_put_back_too(monkeypatch):
    """The restore covers every output table, not only the record's: a table a
    flow writes beyond today's PyPSA set is put back as the LP left it."""
    from services.solver_service import SolverConfig

    n = _solved_ieee14()
    assert "generators_t.mu_upper" not in ac_pf_service._lp_snapshot(n), "fixture"
    before = _every_frame(n)
    real = pypsa.Network.pf

    def _pf_writing_more(self, *a, **k):
        out = real(self, *a, **k)
        self.generators_t["mu_upper"] = self.generators_t.p * 0 + 7.0
        return out

    monkeypatch.setattr(pypsa.Network, "pf", _pf_writing_more)
    ac_pf_service.run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    _assert_lp_or_emptied(n, before)


def test_a_flow_that_does_not_converge_leaves_the_lp_tables():
    """F2: a flow that runs to its iteration limit without converging."""
    from services.solver_service import SolverConfig

    n = _solved_ieee14()
    before = _every_frame(n)
    out = ac_pf_service.run_ac_pf_stage(
        n, SolverConfig(ac_pf_x_tol=1e-300), queue.Queue()
    )
    assert out["ac_pf_converged_count"] == 0, out["ac_pf_convergence_list"][:2]
    _assert_lp_or_emptied(n, before)


def test_bus_net_injection_reads_the_lp_and_the_flow_under_their_own_names(
    client, site, bound_ctx
):
    """GK A 1: the Bus "Net injection" metric (`buses_t.p`) shows the LP's
    values by default and the flow's under source=ac_pf."""
    from tests.test_asset_results_all_classes import BASE

    site(_ieee14(), name="pf_bus_p")
    _run_and_pf(client)
    with bound_ctx(client):
        state = PyPSAService.get_solver_state()
        lp = state["lopf_results"]["buses_t.p"].copy()
        pf = state["ac_pf_results"]["buses_t.p"].copy()
    moved = (pf - lp).abs().max().idxmax()
    assert abs(pf[moved] - lp[moved]).max() > 1e-3, "fixture: lossy"
    for source, want in (("lopf", lp), ("ac_pf", pf)):
        r = client.get(
            f"{BASE}/Bus/{moved}", params={"category": "dispatch", "source": source}
        )
        assert r.status_code == 200, r.text
        assert r.json()["series"]["bus_p"] == want[moved].tolist(), source


def test_the_chained_stage_keeps_the_lp_dispatch_live(client, site, bound_ctx, set_cfg):
    site(_ieee14(), name="pf_chain")
    set_cfg(run_ac_pf_after_lopf=True)
    assert client.post("/api/simulation/run").status_code == 200
    assert _wait_idle(client)["status"] == "completed"
    with bound_ctx(client):
        state = PyPSAService.get_solver_state()
        assert state["condition"] == "lopf+ac_pf_ok"
        _assert_live_is_lp(PyPSAService.get_network(), state["lopf_results"])


@pytest.mark.parametrize("where", ["inside_pf", "after_the_pf_snapshot"])
def test_every_outcome_leaves_the_lp_tables(
    client, site, bound_ctx, monkeypatch, where
):
    """F2: a raise inside the flow and a raise after its snapshot both leave
    the live tables equal to the LP snapshot."""
    site(_ieee14(), name=f"pf_raise_{where}")
    assert client.post("/api/simulation/run").status_code == 200
    _wait_idle(client)
    with bound_ctx(client):
        lp = ac_pf_service._lp_snapshot(PyPSAService.get_network())
    if where == "inside_pf":
        real = pypsa.Network.pf

        def _half(self, *a, **k):
            real(self, *a, **k)
            raise RuntimeError("boom")

        monkeypatch.setattr(pypsa.Network, "pf", _half)
    else:
        real = ac_pf_service._snapshot_result_state
        calls = []

        def _second_raises(n):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real(n)

        monkeypatch.setattr(ac_pf_service, "_snapshot_result_state", _second_raises)
    assert client.post("/api/simulation/run_ac_pf").status_code == 200
    assert _wait_idle(client)["status"] == "failed"
    with bound_ctx(client):
        _assert_live_is_lp(PyPSAService.get_network(), lp)


def test_no_value_of_an_earlier_flow_stays_live(client, site, bound_ctx):
    """F3: LOPF→PF, edit a load, LOPF→PF again, save and reload: the live q
    and |V| equal the second LP snapshot (empty), never the first flow's."""
    site(_ieee14(), name="pf_twice")
    _run_and_pf(client)
    with bound_ctx(client):
        n = PyPSAService.get_network()
        load = n.loads.index[0]
        n.loads.loc[load, "p_set"] = n.loads.at[load, "p_set"] * 1.1
    _run_and_pf(client)
    with bound_ctx(client):
        second_lp = PyPSAService.get_solver_state()["lopf_results"]
    _reload(client, "pf_twice")
    with bound_ctx(client):
        n = PyPSAService.get_network()
        for key in PF_ONLY:
            assert second_lp[key].columns.empty, key
            acc, attr = key.split(".", 1)
            assert getattr(getattr(n, acc), attr).columns.empty, key
        for key in ("generators_t.p", "buses_t.v_ang", "buses_t.p"):
            acc, attr = key.split(".", 1)
            pd.testing.assert_frame_equal(
                getattr(getattr(n, acc), attr),
                second_lp[key],
                check_exact=True,
                check_freq=False,
                obj=key,
            )


def _three_port_hub() -> pypsa.Network:
    from tests.test_ac_pf_link_dispatch import _hub

    return _hub(False, 0.01)


def test_every_link_port_keeps_its_lp_flow():
    """F4: p2 of a 3-port link is restored too, and the flow's p2 is kept."""
    import queue

    from services.solver_service import SolverConfig

    n = _three_port_hub()
    lp = {p: n.links_t[p].copy() for p in ("p0", "p1", "p2")}
    out = ac_pf_service.run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    for p, df in lp.items():
        pd.testing.assert_frame_equal(n.links_t[p], df, check_exact=True, obj=p)
        assert f"links_t.{p}" in out["ac_pf_results"], p
        pd.testing.assert_frame_equal(
            out["lopf_results"][f"links_t.{p}"], df, check_exact=True
        )


def test_the_restore_is_not_an_edit():
    """F7: the PF stage moves no revision and no input fingerprint."""
    import queue

    from services import dirty_state
    from services.solver_service import SolverConfig
    from services.workflow_tools import _network_input_fingerprint

    n = _three_port_hub()
    ctx = PyPSAService.get_active_context()
    PyPSAService.set_network(n)
    target = PyPSAService._transient_target(ensure=False)
    before = (dirty_state.revision(target), _network_input_fingerprint(ctx))
    with PyPSAService.get_lock():
        ac_pf_service.run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    assert (dirty_state.revision(target), _network_input_fingerprint(ctx)) == before


def test_no_reader_sees_the_flow_written_frames(monkeypatch):
    """F6: the flow writes and the restore happen in one lock hold: a reader
    taking the lock while the flow runs waits until the LP tables are back."""
    import queue

    from services.solver_service import SolverConfig

    n = _three_port_hub()
    lp = n.generators_t.p.copy()
    lock = PyPSAService.get_lock()
    seen = {}
    real = pypsa.Network.pf

    def _reader():
        with lock:
            seen["p"] = n.generators_t.p.copy()

    def _pf_then_reader(self, *a, **k):
        out = real(self, *a, **k)
        t = threading.Thread(target=_reader)
        t.start()
        t.join(0.3)
        seen["waited"] = t.is_alive()
        seen["thread"] = t
        return out

    monkeypatch.setattr(pypsa.Network, "pf", _pf_then_reader)
    with lock:
        ac_pf_service.run_ac_pf_stage(n, SolverConfig(), queue.Queue())
    seen["thread"].join(5)
    assert seen["waited"] is True
    pd.testing.assert_frame_equal(seen["p"], lp, check_exact=True)


# Master's record writer (`git show origin/master:pypsa-gui/backend/services/
# ac_pf_service.py`, `_RESULT_DYNAMIC_ATTRS` and `_snapshot_result_state`),
# pinned so a legacy fixture has master's shape: no links, no `buses_t.p`,
# q and |V| kept as they were (GK A 4).
_MASTER_RESULT_DYNAMIC_ATTRS = (
    ("lines_t", ("p0", "p1", "q0", "q1", "loss", "mu_upper", "mu_lower")),
    ("transformers_t", ("p0", "p1", "q0", "q1", "loss", "mu_upper", "mu_lower")),
    ("generators_t", ("p", "q", "status", "start_up", "shut_down")),
    ("storage_units_t", ("p", "state_of_charge")),
    ("stores_t", ("p", "e")),
    ("loads_t", ("p", "q")),
    ("buses_t", ("marginal_price", "v_mag_pu", "v_ang")),
)


def _master_snapshot_result_state(n) -> dict:
    out = {}
    for accessor_name, attrs in _MASTER_RESULT_DYNAMIC_ATTRS:
        try:
            accessor = getattr(n, accessor_name, None)
        except Exception:
            continue
        if accessor is None:
            continue
        for attr in attrs:
            try:
                df = getattr(accessor, attr, None)
            except Exception:
                df = None
            if df is None:
                continue
            try:
                out[f"{accessor_name}.{attr}"] = copy.deepcopy(df)
            except Exception:
                pass
    return out


def _legacy_save(client, site, monkeypatch, project_storage_dir, name, build=None):
    """A project saved before PF-SAVE-1: master's writer keeps the record, the
    stage leaves the flow's tables live (as master did), the real save route
    writes them to network.nc, and metadata.json has no PF-SAVE-1 marker."""
    import json

    from routers.projects import LP_DISPATCH_SAVED

    site((build or _ieee14)(), name=name)
    with monkeypatch.context() as m:
        m.setattr(ac_pf_service, "_lp_snapshot", _master_snapshot_result_state)
        m.setattr(
            ac_pf_service, "_snapshot_result_state", _master_snapshot_result_state
        )
        m.setattr(ac_pf_service, "_restore_result_state", lambda n, snap: None)
        _run_and_pf(client)
    assert (
        client.post(f"/api/projects/{name}", params={"force": True}).status_code == 200
    )
    meta_path = project_storage_dir(name) / "metadata.json"
    meta = json.loads(meta_path.read_text())
    assert meta.pop(LP_DISPATCH_SAVED) is False, "fixture: the flow's |V| is live"
    meta_path.write_text(json.dumps(meta))


def test_a_legacy_project_reloads_with_the_lp_dispatch(
    client, site, bound_ctx, monkeypatch, project_storage_dir
):
    """F5: network.nc carries the flow's dispatch and master's record; the
    record's LP tables win, and an LP table master's record lacks (`buses_t.p`)
    reads as no data, never the flow's."""
    _legacy_save(client, site, monkeypatch, project_storage_dir, "pf_legacy")
    with bound_ctx(client):
        record = PyPSAService.get_solver_state()["lopf_results"]
        assert record.keys() <= {
            f"{acc}.{a}" for acc, attrs in _MASTER_RESULT_DYNAMIC_ATTRS for a in attrs
        }, "fixture: master's record shape"
        assert "buses_t.p" not in record and "generators_t.p" in record
        lp = {
            k: record[k].copy()
            for k in ("generators_t.p", "lines_t.p0", "buses_t.v_ang")
        }
        n = PyPSAService.get_network()
        assert not n.generators_t.p.equals(lp["generators_t.p"]), "fixture: PF live"
        assert not n.buses_t.q.columns.empty, "fixture: PF q live"
    _reload(client, "pf_legacy")
    with bound_ctx(client):
        n = PyPSAService.get_network()
        for key, df in lp.items():
            acc, attr = key.split(".", 1)
            pd.testing.assert_frame_equal(
                getattr(getattr(n, acc), attr), df, check_exact=True, obj=key
            )
        for key in [*PF_ONLY, "buses_t.p"]:
            acc, attr = key.split(".", 1)
            assert getattr(getattr(n, acc), attr).columns.empty, key


def test_a_legacy_projects_links_read_as_no_data_not_the_flows(
    client, site, bound_ctx, monkeypatch, project_storage_dir
):
    """GK A 29: master's record has no link port, so network.nc's links_t
    hold the flow's values; after reload every port is empty and the LP
    source gives no link data, never the flow's under the LP label."""
    _legacy_save(
        client,
        site,
        monkeypatch,
        project_storage_dir,
        "pf_legacy_links",
        build=_three_port_hub,
    )
    with bound_ctx(client):
        assert not PyPSAService.get_network().links_t.p0.columns.empty, "fixture"
        record = PyPSAService.get_solver_state()["lopf_results"]
        assert not any(k.startswith("links_t.") for k in record), "fixture"
    _reload(client, "pf_legacy_links")
    with bound_ctx(client):
        n = PyPSAService.get_network()
        ports = [k for k in n.links_t.keys() if k[1:].isdigit() and k[0] == "p"]
        assert {"p0", "p1", "p2"} <= set(ports)
        for port in ports:
            assert n.links_t[port].columns.empty, port
    r = client.get("/api/results/links", params={"source": "lopf"})
    assert r.status_code in (200, 204), r.text
    assert r.status_code == 204 or not r.json().get("columns"), r.text


def test_a_new_save_keeps_every_link_port_the_lps(client, site, bound_ctx):
    """GK A 29: a PF-SAVE-1 save keeps the LP's flow on every link port."""
    site(_three_port_hub(), name="pf_new_links")
    _run_and_pf(client)
    with bound_ctx(client):
        lp = {
            k: df.copy()
            for k, df in PyPSAService.get_solver_state()["lopf_results"].items()
            if k.startswith("links_t.")
        }
    assert {"links_t.p0", "links_t.p1", "links_t.p2"} <= lp.keys()
    _reload(client, "pf_new_links")
    with bound_ctx(client):
        n = PyPSAService.get_network()
        for key, df in lp.items():
            pd.testing.assert_frame_equal(
                n.links_t[key.split(".", 1)[1]],
                df,
                check_exact=True,
                check_freq=False,
                obj=key,
            )


def test_a_legacy_project_without_its_lp_record_says_so(
    client, site, bound_ctx, monkeypatch, project_storage_dir
):
    """F5: no LP record to restore: the dispatch is disclosed as the flow's."""
    from services import change_log_service

    _legacy_save(client, site, monkeypatch, project_storage_dir, "pf_legacy_nolp")
    pkl = project_storage_dir("pf_legacy_nolp") / "results_state.pkl"
    assert pkl.exists(), "fixture: the save wrote a record"
    pkl.unlink()
    change_log_service.clear()
    _reopen(client, "pf_legacy_nolp")
    assert _flow_warnings() == 1
    # Once per saved file: another open (a background probe's hydrate) is quiet.
    _reopen(client, "pf_legacy_nolp")
    assert _flow_warnings() == 1


def _flow_warnings() -> int:
    from services import change_log_service

    texts = [str(e.get("message") or e) for e in change_log_service.get_all()]
    return sum("Dispatch saved from load flow" in t for t in texts)


def test_a_new_save_without_its_record_is_not_disclosed_as_the_flows(
    client, site, bound_ctx, project_storage_dir
):
    """GK A 3: a PF-SAVE-1 save holds the LP's tables, so a missing record
    (results_state.pkl removed) is no evidence the dispatch is the flow's."""
    from services import change_log_service

    site(_ieee14(), name="pf_new_nolp")
    _run_and_pf(client)
    assert (
        client.post("/api/projects/pf_new_nolp", params={"force": True}).status_code
        == 200
    )
    (project_storage_dir("pf_new_nolp") / "results_state.pkl").unlink()
    change_log_service.clear()
    _reopen(client, "pf_new_nolp")
    _reload(client, "pf_new_nolp")
    assert _flow_warnings() == 0


def test_a_flow_record_without_links_reads_as_no_data(client, site, bound_ctx):
    """F5: an `ac_pf_results` saved before links were kept has no links key:
    the load-flow source gives no data, never the LP's flow under a PF label."""
    site(_three_port_hub(), name="pf_nolinks")
    _run_and_pf(client)
    with bound_ctx(client):
        state = PyPSAService.get_solver_state()
        for key in [k for k in state["ac_pf_results"] if k.startswith("links_t.")]:
            del state["ac_pf_results"][key]
    r = client.get("/api/results/links", params={"source": "ac_pf"})
    assert r.status_code in (200, 204), r.text
    assert r.status_code == 204 or not r.json().get("columns"), r.text
    assert (
        client.get("/api/results/links", params={"source": "lopf"}).status_code == 200
    )


def test_a_flow_on_a_network_carrying_an_earlier_flows_q_and_v_empties_them(
    client, site, bound_ctx, monkeypatch, project_storage_dir
):
    """F3: a network that carries an earlier flow's q and |V| (a project saved before
    PF-SAVE-1 with no record) runs LOPF→PF: the LP snapshot and the live network hold
    those tables empty, never the earlier flow's values."""
    _legacy_save(client, site, monkeypatch, project_storage_dir, "pf_stale_qv")
    (project_storage_dir("pf_stale_qv") / "results_state.pkl").unlink()
    _reopen(client, "pf_stale_qv")
    with bound_ctx(client):
        assert not PyPSAService.get_network().buses_t.v_mag_pu.columns.empty, "fixture"
    _run_and_pf(client)
    with bound_ctx(client):
        n = PyPSAService.get_network()
        lp = PyPSAService.get_solver_state()["lopf_results"]
        for key in PF_ONLY:
            acc, attr = key.split(".", 1)
            assert lp[key].columns.empty, key
            assert getattr(getattr(n, acc), attr).columns.empty, key
