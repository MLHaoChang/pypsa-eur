# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-40b: one stale-banner test per reader, each through its runner's real capture (`dirty_state.capture`/`hold`) and a real edit seam.

Per reader: False after the run, True after the edit, None while a re-run is
running (its engine gated), False once it finishes. No test writes
`network_revision` or calls `bump_revision`/`capture`/`hold`.
"""
import contextlib
import pathlib
import threading

from services import chat_tools, dirty_state, study_state
from services.adequacy import campaign, grid_readiness
from services.study import runner as R
from tests.study_s4_support import wait_run
from tests.test_adequacy_coupling_endpoint import LOOP_URL, VOLL, _install_stubs, _poll, _setup, _Stubs
from tests.test_eh_study_edited_since import _edit_bus
from tests.test_eh_study_record_survives_reactivation import STUDY_URL, fake_done, hub_project, poll, run_study
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_margin_elcc import (
    FR_TARGETS,
    ML_DRAWS,
    ML_SEED,
    ML_TARGET_H,
    call,
    elcc_network,
    frontier_network,
    loop_network,
    run_loop,
    wait_for,
)
from tests.test_lc37_d13_stale_readers import edit
from tests.test_lc39_d13_http_stale_readers import flag
from tests.test_project_flow import _put
from tests.test_study_runner import _setup as _study_setup
from tests.test_study_runner import fake, studies_on  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401


@contextlib.contextmanager
def gated(monkeypatch, module, attr: str):
    """Hold the study's engine (`module.attr`) at its first call; yields the reached event."""
    gate, reached = threading.Event(), threading.Event()
    real = getattr(module, attr)

    def held(*a, **k):
        reached.set()
        assert gate.wait(60)
        return real(*a, **k)

    monkeypatch.setattr(module, attr, held)
    try:
        yield reached
    finally:
        gate.set()


def sweep_mod():
    import services.adequacy.sweep as sweep
    return sweep


# ── the HTTP adequacy routes ─────────────────────────────────────────────

def test_frontier_route_frontier_loop_runner_chat_edit(seam, client, monkeypatch):
    """/api/results/frontier, frontier_loop_runner; the chat edit seam."""
    seam(frontier_network(), voll=100.0)
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    wait_for('frontier')
    assert flag(client, 'frontier') is False
    edit('Generator', 'dear', marginal_cost=30.0)
    assert flag(client, 'frontier') is True
    with gated(monkeypatch, sweep_mod(), '_solve_once') as reached:
        call('run_frontier_study', targets_permyriad=FR_TARGETS)
        assert reached.wait(30)
        assert flag(client, 'frontier') is None
    wait_for('frontier')
    assert flag(client, 'frontier') is False


def test_mc_route_mc_loop_runner_http_edit(seam, client, monkeypatch):
    """/api/results/mc, mc_loop_runner; the HTTP edit middleware (PUT a load)."""
    import services.adequacy.mc as mc

    seam(elcc_network(100.0))
    study = {'seed': 3, 'draws': 200, 'elcc_assets': [{'kind': 'generator', 'name': 'P'}]}
    call('run_mc_study', **study)
    wait_for('mc')
    assert flag(client, 'mc') is False
    _put(client, 'loads', 'l', p_set=95.0)
    assert flag(client, 'mc') is True
    with gated(monkeypatch, mc, 'mc_adequacy') as reached:
        call('run_mc_study', **study)
        assert reached.wait(30)
        assert flag(client, 'mc') is None
    wait_for('mc')
    assert flag(client, 'mc') is False


def test_margin_loop_route_margin_loop_runner_update_solver_config(seam, client, monkeypatch):
    """/api/results/margin_loop, margin_loop_runner; `chat_tools.update_solver_config`."""
    run_loop(seam, 100.0)
    assert flag(client, 'margin_loop') is False
    chat_tools.update_solver_config({'voll': 5000.0})
    assert flag(client, 'margin_loop') is True
    with gated(monkeypatch, sweep_mod(), '_solve_once') as reached:
        call('run_margin_loop', target_lole_h=ML_TARGET_H, draws=ML_DRAWS, seed=ML_SEED, max_solves=8)
        assert reached.wait(30)
        assert flag(client, 'margin_loop') is None
    wait_for('margin_loop')
    assert flag(client, 'margin_loop') is False


def test_margin_loop_hold_keeps_its_own_record_fresh_and_an_earlier_frontier_stale(seam, client):
    """/api/results/margin_loop and /frontier: margin_loop_runner's `restore='final'` hold."""
    seam(loop_network(100.0), voll=100.0)()
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    wait_for('frontier')
    assert flag(client, 'frontier') is False
    call('run_margin_loop', target_lole_h=ML_TARGET_H, draws=ML_DRAWS, seed=ML_SEED,
         max_solves=8, restore='final')
    assert wait_for('margin_loop')['status'] == 'met'
    assert flag(client, 'margin_loop') is False
    assert flag(client, 'frontier') is True


def test_coupling_loop_route_coupling_loop_runner_solver_config_put(client, install_network, monkeypatch):
    """/api/results/coupling_loop, coupling_loop_runner; `PUT /api/simulation/solver_config`."""
    stubs = _Stubs(lole_seq=[0.5])
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network)
    body = {'target_lole_h': 1.0, 'draws': 16, 'seed': 5}
    assert client.post(LOOP_URL, json=body).status_code == 200
    assert _poll(client)['edited_since_study'] is False
    r = client.put('/api/simulation/solver_config', json={'solver_name': 'highs', 'voll': VOLL + 1})
    assert r.status_code == 200, r.text
    assert client.get(LOOP_URL).json()['edited_since_study'] is True
    with gated(monkeypatch, sweep_mod(), '_solve_once') as reached:
        r = client.post(LOOP_URL, json=body)
        assert r.status_code == 200, r.text
        assert reached.wait(30)
        assert client.get(LOOP_URL).json()['edited_since_study'] is None
    assert _poll(client)['edited_since_study'] is False


def test_coupling_loop_hold_keeps_its_own_record_fresh_and_the_earlier_one_stale(
        client, install_network, monkeypatch, session_ctx):
    """/api/results/coupling_loop: coupling_loop_runner's `restore='final'` hold;
    the earlier coupling record (its own real capture) reads stale."""
    _install_stubs(monkeypatch, _Stubs(lole_seq=[0.5]))
    _setup(client, install_network)
    ctx = session_ctx(client)
    body = {'target_lole_h': 1.0, 'draws': 16, 'seed': 5}
    assert client.post(LOOP_URL, json=body).status_code == 200
    assert _poll(client)['edited_since_study'] is False
    earlier = ctx.solver_state['coupling_loop']
    assert isinstance(earlier['network_revision'], int)
    assert client.post(LOOP_URL, json={**body, 'restore': 'final'}).status_code == 200
    out = _poll(client)
    assert out['status'] == 'met' and out['edited_since_study'] is False
    assert ctx.solver_state['coupling_loop'] is not earlier
    assert study_state.edited_since(earlier, revision=dirty_state.revision(ctx)) is True


def test_eh_study_route_eh_study_runner_http_edit(client, install_network, monkeypatch):
    """/api/results/eh_study, eh_study_runner; the HTTP edit middleware (PUT a bus)."""
    gate, reached = threading.Event(), threading.Event()
    fake_done(monkeypatch, gate=gate, reached=reached)
    gate.set()
    hub_project(client, install_network, 'lc40b-eh')
    assert run_study(client)['edited_since_study'] is False
    _edit_bus(client)
    assert client.get(STUDY_URL).json()['edited_since_study'] is True
    gate.clear()
    reached.clear()
    try:
        assert client.post(STUDY_URL, json={'archetype': 'weak_flexible'}).status_code == 200
        assert reached.wait(10)
        assert client.get(STUDY_URL).json()['edited_since_study'] is None
    finally:
        gate.set()
    assert poll(client)['edited_since_study'] is False


# ── the decision study's campaign entries ────────────────────────────────

def test_decision_study_campaign_entry_study_runner_http_edit(
        client, api_project, studies_on, fake, session_ctx):
    """Campaign entries (`campaign._snapshot`), study/runner `start_study_run`; the HTTP edit middleware."""
    sid = _study_setup(client, api_project, 'lc40b-ds')
    r = client.post('/api/projects/lc40b-ds/activate')
    assert r.status_code == 200, r.text
    ctx = session_ctx(client)
    R._on_ctx(ctx, campaign.start, 'LC-40b decision study', 20)

    def flags():
        return [e['edited_since_study'] for e in R._on_ctx(ctx, campaign.status)['entries']
                if e['study'] == R.STUDY_KEY]

    run_url = f'/api/projects/lc40b-ds/studies/{sid}/run'
    assert client.post(run_url, json={'fidelity': 'quick_screen'}).status_code == 202
    assert wait_run(client, 'lc40b-ds', sid)['status'] == 'done'
    assert flags() == [False]
    _edit_bus(client)
    assert flags() == [True]

    gate, reached = threading.Event(), threading.Event()
    fake.on_call = lambda _i: (reached.set(), gate.wait(60))
    try:
        assert client.post(run_url, json={'fidelity': 'quick_screen'}).status_code == 202
        assert reached.wait(30)
        assert flags() == [True, None]
    finally:
        gate.set()
    assert wait_run(client, 'lc40b-ds', sid)['status'] == 'done'
    assert flags() == [True, False]


# ── the chat readers on the EH study record ──────────────────────────────

def test_grid_readiness_reader_eh_study_runner_solver_config_put(
        client, install_network, monkeypatch, bound_ctx, session_ctx):
    """`grid_readiness._active_study` (the scorecard's study flag), eh_study_runner;
    `PUT /api/simulation/solver_config`."""
    gate, reached = threading.Event(), threading.Event()
    fake_done(monkeypatch, gate=gate, reached=reached)
    gate.set()
    hub_project(client, install_network, 'lc40b-gr')
    project_dir = pathlib.Path(session_ctx(client).storage_dir)

    def edited():
        with bound_ctx(client):
            record, flag_ = grid_readiness._active_study(project_dir)
        return flag_ if record is not None else 'no record'

    run_study(client)
    assert edited() is False
    r = client.put('/api/simulation/solver_config', json={'solver_name': 'highs', 'voll': 2000.0})
    assert r.status_code == 200, r.text
    assert edited() is True
    gate.clear()
    reached.clear()
    try:
        assert client.post(STUDY_URL, json={'archetype': 'weak_flexible'}).status_code == 200
        assert reached.wait(10)
        assert edited() == 'no record'               # a running record is not read: (None, None)
    finally:
        gate.set()
    poll(client)
    assert edited() is False


def test_flow_reader_eh_study_runner_http_edit(client, install_network, monkeypatch):
    """`GET /api/projects/{name}/flow` results freshness (`flow_service._freshness`), eh_study_runner;
    the HTTP edit middleware (PUT a bus)."""
    gate, reached = threading.Event(), threading.Event()
    fake_done(monkeypatch, gate=gate, reached=reached)
    gate.set()
    name = hub_project(client, install_network, 'lc40b-flow')

    def freshness():
        r = client.get(f'/api/projects/{name}/flow')
        assert r.status_code == 200, r.text
        return next(s for s in r.json()['sections'] if s['section'] == 'results')['freshness']

    run_study(client)
    assert freshness() == 'fresh'
    _edit_bus(client)
    assert freshness() == 'stale'
    gate.clear()
    reached.clear()
    try:
        assert client.post(STUDY_URL, json={'archetype': 'weak_flexible'}).status_code == 200
        assert reached.wait(10)
        assert freshness() == 'running'
    finally:
        gate.set()
    poll(client)
    assert freshness() == 'fresh'
