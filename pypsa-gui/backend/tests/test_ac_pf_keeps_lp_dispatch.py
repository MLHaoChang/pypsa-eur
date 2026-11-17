"""Owner decision PF-SAVE-1 (study harness 50, GK A (29) F1-F7): after the AC
power flow the live network, and the network.nc saved from it, keep the LP's
dispatch; the flow's tables live only under `ac_pf_results`."""

from __future__ import annotations

import pathlib
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

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "project_templates"
PF_ONLY = sorted(ac_pf_service._PF_ONLY_RESULTS)


def _ieee14() -> pypsa.Network:
    path = TEMPLATES / "ieee14" / "network.nc"
    if not path.exists():
        pytest.skip("ieee14 template not built (project_templates/_build.py)")
    return pypsa.Network(path)


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
        pd.testing.assert_frame_equal(
            n.generators_t.p, second_lp["generators_t.p"], check_exact=True
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


def _legacy_save(client, site, monkeypatch, name):
    """A project saved before PF-SAVE-1: the stage leaves the flow's tables
    live (as master did) and the real save route writes them to network.nc."""
    site(_ieee14(), name=name)
    with monkeypatch.context() as m:
        m.setattr(ac_pf_service, "_restore_result_state", lambda n, snap: None)
        _run_and_pf(client)
    assert (
        client.post(f"/api/projects/{name}", params={"force": True}).status_code == 200
    )


def test_a_legacy_project_reloads_with_the_lp_dispatch(
    client, site, bound_ctx, monkeypatch
):
    """F5: network.nc carries the flow's dispatch; the record's LP tables win."""
    _legacy_save(client, site, monkeypatch, "pf_legacy")
    with bound_ctx(client):
        state = PyPSAService.get_solver_state()
        lp = state["lopf_results"]["generators_t.p"].copy()
        assert not PyPSAService.get_network().generators_t.p.equals(lp), (
            "fixture: PF live"
        )
    _reload(client, "pf_legacy")
    with bound_ctx(client):
        n = PyPSAService.get_network()
        pd.testing.assert_frame_equal(n.generators_t.p, lp, check_exact=True)
        assert n.buses_t.v_mag_pu.columns.empty


def test_a_legacy_project_without_its_lp_record_says_so(
    client, site, bound_ctx, monkeypatch, project_storage_dir
):
    """F5: no LP record to restore: the dispatch is disclosed as the flow's."""
    from services import change_log_service

    _legacy_save(client, site, monkeypatch, "pf_legacy_nolp")
    pkl = project_storage_dir("pf_legacy_nolp") / "results_state.pkl"
    assert pkl.exists(), "fixture: the save wrote a record"
    pkl.unlink()
    change_log_service.clear()
    _reopen(client, "pf_legacy_nolp")
    texts = [str(e.get("message") or e) for e in change_log_service.get_all()]
    assert any("Dispatch saved from load flow" in t for t in texts), texts


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
    _legacy_save(client, site, monkeypatch, "pf_stale_qv")
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
