"""Issue 07 — get_loadflow_summary over the CURRENT results.

Real PyPSA networks with hand-set result frames (no solver): the service must
read what the Results tab reads, rank by software-computed utilisation, keep the
active-power proxy distinct from AC apparent power, and refuse (typed) rather
than invent numbers for unsolved / stale / running / infeasible / failed results.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from harness import catalogue, toolsets
from services import chat_tools
from services import loadflow_summary as lf
from services import run_evidence as re_
from services.pypsa_service import PyPSAService

PROJECT = '33333333-3333-3333-3333-333333333333'
OWNER = 'owner-lf'
SNAPSHOTS = pd.date_range('2030-01-01', periods=4, freq='h', name='snapshot')


def _record_fixture_inputs(n: pypsa.Network) -> None:
    """Hand-set result fixtures declare their input source, as the solver does."""
    from services import workflow_tools
    workflow_tools._record_solve_inputs(n, PyPSAService.get_solver_state()['solver_config'])


def _install(n: pypsa.Network) -> None:
    PyPSAService.set_network(n)
    with PyPSAService._registry_lock:
        for k in [k for k in PyPSAService._contexts if k.startswith('scratch:')]:
            PyPSAService._contexts.pop(k, None)
    state = PyPSAService.get_solver_state()
    state.update(status='completed', condition='optimal', objective=1000.0,
                 ac_pf_results=None, ac_pf_convergence=None, ac_pf_convergence_list=None,
                 ac_pf_converged_count=None, ac_pf_total_snapshots=None)
    _record_fixture_inputs(n)


def _frame(**cols) -> pd.DataFrame:
    return pd.DataFrame(cols, index=SNAPSHOTS)


def build_network() -> pypsa.Network:
    """
    L_hot   100 MVA, flows peak 95 at bus0 / -90 at bus1 (bus1 magnitude smaller).
    L_p1    100 MVA, bus0 flow 40 but bus1 flow -60: the second terminal ranks it.
    L_ext   s_nom 10 extendable, s_nom_opt 200: ranked on the OPTIMIZED rating.
    L_dyn   200 MVA with s_max_pu 1.0 but 0.5 in snapshot 2: 80 MW there is 80 %.
    L_zero  s_nom 0: unrated, never ranked as zero loading.
    L_nan   rated but every flow NaN: no flow data.
    T_1     transformer 50 MVA, peak 49.
    """
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    for bus in ('A', 'B', 'C'):
        n.add('Bus', bus)
    for name, s_nom, extra in (
        ('L_hot', 100.0, {}), ('L_p1', 100.0, {}),
        ('L_ext', 10.0, {'s_nom_extendable': True}),
        ('L_dyn', 200.0, {}), ('L_zero', 0.0, {}), ('L_nan', 100.0, {}),
    ):
        n.add('Line', name, bus0='A', bus1='B', x=0.1, r=0.01, s_nom=s_nom, **extra)
    n.add('Transformer', 'T_1', bus0='B', bus1='C', s_nom=50.0, x=0.1, r=0.01)

    n.lines.loc['L_ext', 's_nom_opt'] = 200.0
    n.lines.loc['L_hot', 's_nom_opt'] = 100.0
    n.lines_t.p0 = _frame(
        L_hot=[95.0, 10.0, 20.0, 30.0], L_p1=[40.0, 40.0, 40.0, 40.0],
        L_ext=[150.0, 100.0, 100.0, 100.0], L_dyn=[40.0, 40.0, 80.0, 40.0],
        L_zero=[5.0, 5.0, 5.0, 5.0], L_nan=[np.nan] * 4)
    n.lines_t.p1 = _frame(
        L_hot=[-90.0, -9.0, -19.0, -29.0], L_p1=[-30.0, -60.0, -30.0, -30.0],
        L_ext=[-148.0, -99.0, -99.0, -99.0], L_dyn=[-39.0, -39.0, -79.0, -39.0],
        L_zero=[-5.0] * 4, L_nan=[np.nan] * 4)
    n.lines_t.s_max_pu = _frame(L_dyn=[1.0, 1.0, 0.5, 1.0])
    n.transformers_t.p0 = _frame(T_1=[49.0, 10.0, 10.0, 10.0])
    n.transformers_t.p1 = _frame(T_1=[-48.0, -9.0, -9.0, -9.0])
    n._objective = 0.0
    return n


@pytest.fixture
def solved(monkeypatch):
    n = build_network()
    _install(n)
    state = {'revoked': False, 'project': PROJECT}

    def authority():
        if state['revoked']:
            raise HTTPException(404, 'Project not found')
        return None, state['project'], OWNER

    monkeypatch.setattr(re_, '_authority', authority)
    state['network'] = n
    return state


def _by_name(out):
    return {r['name']: r for r in out['top']}


# ── ranking, terminals, ratings ─────────────────────────────────────────

def test_ranks_by_utilisation_of_effective_limit(solved):
    out = lf.get_loadflow_summary(limit=25)
    assert out['status'] == 'ok' and out['scope'] == 'current'
    order = [r['name'] for r in out['top']]
    # L_dyn: 80 MW / (200 * 0.5) = 80 %; L_ext uses s_nom_opt 200: 150/200 = 75 %.
    assert order == ['T_1', 'L_hot', 'L_dyn', 'L_ext', 'L_p1']
    rows = _by_name(out)
    assert rows['L_dyn']['util_pct'] == pytest.approx(80.0)
    assert rows['L_dyn']['rating_pct'] == pytest.approx(40.0)
    assert rows['L_dyn']['s_max_pu'] == {'min': 0.5, 'max': 1.0}
    assert rows['L_hot']['s_max_pu'] == {'min': 1.0, 'max': 1.0}
    assert rows['L_hot']['util_pct'] == pytest.approx(95.0)
    assert 'flow / (rating * s_max_pu)' in out['ranking_convention']
    assert out['top_shown'] == 5 and out['top_truncated'] is False


def test_both_terminals_are_considered(solved):
    row = _by_name(lf.get_loadflow_summary(limit=25))['L_p1']
    assert 'terminals' not in row  # both terminals are the default
    assert row['peak_flow'] == pytest.approx(60.0)  # |p1| beats |p0| = 40
    assert row['util_pct'] == pytest.approx(60.0)


def test_missing_p1_degrades_to_bus0_only_and_says_so(solved):
    solved['network'].lines_t.p1 = pd.DataFrame(index=SNAPSHOTS)
    out = lf.get_loadflow_summary(limit=25)
    assert _by_name(out)['L_p1']['terminals'] == 'bus0_only'
    assert _by_name(out)['L_p1']['peak_flow'] == pytest.approx(40.0)
    assert any('bus1' in m for m in out['missing_evidence'])


def test_optimized_rating_used_for_extendable_branch(solved):
    rating = _by_name(lf.get_loadflow_summary(limit=25))['L_ext']['rating']
    assert rating == {'value': 200.0, 'basis': 'optimized'}
    out = lf.get_loadflow_summary(limit=25)
    assert out['units']['rating'] == 'MVA'
    assert _by_name(out)['L_hot']['rating'] == {'value': 100.0, 'basis': 'configured'}


def test_native_capacity_expansion_uses_the_same_recorded_branch_as_its_rating(solved):
    view = lf.get_loadflow_view()
    rows = {row['name']: row for row in view['rows']}
    assert rows['L_ext']['capacity'] == {'configured': 10.0, 'optimized': 200.0, 'extendable': True}
    assert rows['L_ext']['rating']['value'] == rows['L_ext']['capacity']['optimized']
    assert rows['L_hot']['capacity'] == {'configured': 100.0, 'optimized': None, 'extendable': False}
    assert all('capacity' not in row for row in lf.get_loadflow_summary()['top'])


def test_zero_or_missing_rating_and_no_data_are_reported_not_ranked(solved):
    out = lf.get_loadflow_summary(limit=25)
    assert 'L_zero' not in _by_name(out) and 'L_nan' not in _by_name(out)
    reasons = {s['name']: s['reason'] for s in out['skipped']}
    assert reasons == {'L_zero': 'rating_missing_or_zero', 'L_nan': 'no_flow_data'}
    assert out['counts']['skipped'] == 2 and out['counts']['ranked'] == 5
    assert any('not ranked' in m for m in out['missing_evidence'])


def test_zero_effective_limit_from_s_max_pu_is_skipped(solved):
    solved['network'].lines.loc['L_hot', 's_max_pu'] = 0.0
    _record_fixture_inputs(solved['network'])
    out = lf.get_loadflow_summary(limit=25)
    reasons = {s['name']: s['reason'] for s in out['skipped']}
    assert reasons['L_hot'] == 'zero_effective_limit'


def test_incomplete_data_counts_populated_snapshots(solved):
    solved['network'].lines_t.p0.loc[SNAPSHOTS[2:], 'L_hot'] = np.nan
    solved['network'].lines_t.p1.loc[SNAPSHOTS[2:], 'L_hot'] = np.nan
    row = _by_name(lf.get_loadflow_summary(limit=25))['L_hot']
    assert row['snapshots_with_data'] == 2
    assert row['util_pct'] == pytest.approx(95.0)


# ── weighting, windows ──────────────────────────────────────────────────

def test_weighted_snapshots_drive_hours_and_mean(solved):
    n = solved['network']
    n.snapshot_weightings.loc[:, 'generators'] = [3.0, 1.0, 1.0, 3.0]
    n.lines_t.p0.loc[SNAPSHOTS[3], 'L_hot'] = 100.0
    n.lines_t.p1.loc[SNAPSHOTS[3], 'L_hot'] = -99.0
    _record_fixture_inputs(n)
    out = lf.get_loadflow_summary(limit=25)
    row = _by_name(out)['L_hot']
    # 95 % (w3), 10 % (w1), 20 % (w1), 100 % (w3): at limit only the last one.
    assert row['h_at_limit'] == pytest.approx(3.0)
    assert row['mean_util_pct'] == pytest.approx((95 * 3 + 10 + 20 + 100 * 3) / 8, abs=0.01)
    assert out['window']['weighted_hours'] == pytest.approx(8.0)


def test_h_over_limit_use_the_time_varying_limit(solved):
    solved['network'].lines_t.p0.loc[SNAPSHOTS[2], 'L_dyn'] = 120.0
    solved['network'].lines_t.p1.loc[SNAPSHOTS[2], 'L_dyn'] = -119.0
    row = _by_name(lf.get_loadflow_summary(limit=25))['L_dyn']
    assert row['h_over_limit'] == pytest.approx(1.0)
    assert row['util_pct'] == pytest.approx(120.0)


def test_window_follow_up_changes_the_ranking_and_reports_exact_window(solved):
    out = lf.get_loadflow_summary(limit=25, window_from=1, window_to=1)
    assert out['window']['from'] == 1 and out['window']['to'] == 1
    assert out['window']['snapshots'] == 1 and out['window']['of'] == 4
    assert out['window']['start'] == str(SNAPSHOTS[1])
    rows = _by_name(out)
    assert rows['L_hot']['util_pct'] == pytest.approx(10.0)
    assert out['top'][0]['name'] == 'L_p1'  # 60 % at snapshot 1
    assert out['ui_reference'] == {'results_tab': 'loadflow', 'source': 'lopf',
                                   'window_from': 1, 'window_to': 1}


@pytest.mark.parametrize('kwargs', [
    {'window_from': 4}, {'window_to': 4}, {'window_from': 3, 'window_to': 2},
    {'window_from': -1}, {'window_to': True}, {'limit': 0}, {'limit': 26}, {'limit': True},
    {'source': 'dc'}, {'run_id': '../etc'},
])
def test_invalid_arguments_are_typed_refusals(solved, kwargs):
    with pytest.raises(HTTPException) as exc:
        lf.get_loadflow_summary(**kwargs)
    assert exc.value.status_code == 422
    assert exc.value.detail['error_kind'] in ('invalid_loadflow_argument', 'invalid_loadflow_window')


# ── proxy vs AC apparent power ──────────────────────────────────────────

def test_lopf_is_an_active_power_proxy_and_never_claims_compliance(solved):
    out = lf.get_loadflow_summary(limit=25)
    assert out['bases_present'] == ['active_power_proxy']
    assert all(r['basis'] == 'active_power_proxy' for r in out['top'])
    assert 'MW (active_power_proxy)' in out['units']['flow']
    assert out['claims']['full_ac_thermal_compliance'] is False
    assert 'N-1' in out['claims']['reason']
    assert any('voltage' in m and 'not assessed' in m for m in out['missing_evidence'])


def _install_ac(n, converged=(True, True, True, True)):
    p0 = n.lines_t.p0.copy()
    snapshot = {
        'lines_t.p0': p0, 'lines_t.p1': n.lines_t.p1.copy(),
        'lines_t.q0': _frame(L_hot=[60.0, 0.0, 0.0, 0.0], L_p1=[0.0] * 4, L_ext=[0.0] * 4,
                             L_dyn=[0.0] * 4, L_zero=[0.0] * 4, L_nan=[np.nan] * 4),
        'lines_t.q1': _frame(L_hot=[-58.0, 0.0, 0.0, 0.0], L_p1=[0.0] * 4, L_ext=[0.0] * 4,
                             L_dyn=[0.0] * 4, L_zero=[0.0] * 4, L_nan=[np.nan] * 4),
        'transformers_t.p0': n.transformers_t.p0.copy(), 'transformers_t.p1': n.transformers_t.p1.copy(),
    }
    state = PyPSAService.get_solver_state()
    state.update(
        ac_pf_results=snapshot,
        ac_pf_convergence={str(s): ok for s, ok in zip(SNAPSHOTS, converged)},
        ac_pf_convergence_list=[{'snapshot': str(s), 'ok': ok} for s, ok in zip(SNAPSHOTS, converged)],
        ac_pf_converged_count=sum(converged), ac_pf_total_snapshots=4)


def test_ac_pf_uses_apparent_power_where_reactive_flows_exist(solved):
    _install_ac(solved['network'])
    out = lf.get_loadflow_summary(source='ac_pf', limit=25)
    rows = _by_name(out)
    assert rows['L_hot']['basis'] == 'ac_apparent_power'
    assert rows['L_hot']['peak_flow'] == pytest.approx(float(np.hypot(95.0, 60.0)), abs=0.01)
    assert rows['L_p1']['basis'] == 'ac_apparent_power'
    assert rows['T_1']['basis'] == 'active_power_proxy'
    assert set(out['bases_present']) == {'ac_apparent_power', 'active_power_proxy'}
    assert any('active-power proxy' in m for m in out['missing_evidence'])
    assert out['claims']['full_ac_thermal_compliance'] is False
    assert out['source'] == 'ac_pf'


def test_ac_pf_excludes_unconverged_snapshots_and_reports_them(solved):
    _install_ac(solved['network'], converged=(False, True, True, True))
    out = lf.get_loadflow_summary(source='ac_pf', limit=25)
    assert out['ac_pf']['unconverged_snapshots_excluded'] == 1
    assert out['ac_pf']['unconverged_in_window'] == 1
    assert _by_name(out)['L_hot']['util_pct'] == pytest.approx(30.0)


def test_ac_pf_with_no_converged_snapshot_in_window_is_unavailable(solved):
    _install_ac(solved['network'], converged=(False, True, True, True))
    out = lf.get_loadflow_summary(source='ac_pf', window_from=0, window_to=0)
    assert out['status'] == 'unavailable' and out['unavailable_kind'] == 'ac_pf_not_converged'


def test_ac_pf_requested_without_a_result_is_typed_unavailable(solved):
    out = lf.get_loadflow_summary(source='ac_pf')
    assert out['status'] == 'unavailable' and out['unavailable_kind'] == 'ac_pf_not_available'


def test_lopf_missing_after_ac_never_substitutes_live_ac_flows(solved):
    _install_ac(solved['network'])
    PyPSAService.get_solver_state()['lopf_results'] = None
    out = lf.get_loadflow_summary(source='lopf')
    assert out['unavailable_kind'] == 'lopf_snapshot_unavailable' and 'top' not in out


def test_partial_lopf_snapshot_never_borrows_the_other_terminal_from_live_ac(solved):
    n = solved['network']
    _install_ac(n)
    PyPSAService.get_solver_state()['lopf_results'] = {'lines_t.p0': n.lines_t.p0.copy()}
    n.lines_t.p1.loc[:, 'L_p1'] = -9000
    out = lf.get_loadflow_summary(source='lopf', limit=25)
    row = _by_name(out)['L_p1']
    assert row['peak_flow'] == 40 and row['terminals'] == 'bus0_only'


def test_unaligned_ac_convergence_cannot_certify_loading(solved):
    _install_ac(solved['network'])
    PyPSAService.get_solver_state()['ac_pf_convergence_list'] = []
    out = lf.get_loadflow_summary(source='ac_pf')
    assert out['status'] == 'unavailable'
    assert out['unavailable_kind'] == 'ac_pf_convergence_unavailable'


def test_string_false_is_not_a_converged_snapshot(solved):
    _install_ac(solved['network'])
    for entry in PyPSAService.get_solver_state()['ac_pf_convergence_list']:
        entry['ok'] = 'false'
    assert lf.get_loadflow_summary(source='ac_pf')['unavailable_kind'] == 'ac_pf_not_converged'


def test_invalid_weights_refuse_instead_of_inventing_weighted_means(solved):
    solved['network'].snapshot_weightings.loc[SNAPSHOTS[0], 'generators'] = -1
    out = lf.get_loadflow_summary()
    assert out['unavailable_kind'] == 'invalid_snapshot_weights'


def test_shared_view_matches_chat_rows_and_preserves_both_terminal_series(solved):
    view = lf.get_loadflow_view(window_from=1, window_to=2)
    summary = lf.get_loadflow_summary(limit=25, window_from=1, window_to=2)
    assert view['top'] == summary['top']
    assert view['evidence_token'] == summary['evidence_token']
    assert view['binding']['window_from'] == 1
    assert view['binding']['window_to'] == 2
    row = next(r for r in view['rows'] if r['name'] == 'L_p1')
    series = next(s for s in view['series'] if s['name'] == 'L_p1')
    assert row['util_pct'] == 60
    assert series['util_pct'] == [60, 40]
    assert len(view['binding']['data_sha256']) == 64


def test_source_digest_changes_for_non_peak_result_and_weight_changes(solved):
    before = lf.get_loadflow_view()['binding']
    solved['network'].lines_t.p0.loc[SNAPSHOTS[1], 'L_hot'] += 0.00000001
    after = lf.get_loadflow_view()['binding']
    assert before['data_sha256'] != after['data_sha256']
    solved['network'].snapshot_weightings.loc[SNAPSHOTS[1], 'generators'] = 7
    _record_fixture_inputs(solved['network'])
    assert lf.get_loadflow_view()['binding']['data_sha256'] != after['data_sha256']


def test_chart_cap_does_not_cap_the_summary_window_or_fabricate_points(solved):
    n = solved['network']
    n.set_snapshots(pd.date_range('2030-01-01', periods=900, freq='h'))
    flow = np.full(900, 40.0)
    flow[-1] = 100.0
    n.lines_t.p0 = pd.DataFrame({name: flow if name == 'L_p1' else np.full(900, np.nan)
                               for name in n.lines.index}, index=n.snapshots)
    n.lines_t.p1 = -n.lines_t.p0
    _record_fixture_inputs(n)
    view = lf.get_loadflow_view()
    row = next(r for r in view['rows'] if r['name'] == 'L_p1')
    trace = next(s for s in view['series'] if s['name'] == 'L_p1')
    assert row['util_pct'] == 100
    assert view['window']['snapshots'] == 900
    assert len(trace['util_pct']) == 800 and max(trace['util_pct']) == 40
    assert len(view['series_index']) == 800 and view['series_truncated']
    json.dumps(view, allow_nan=False)


# ── unsolved / stale / running / infeasible / failed ────────────────────

def test_unsolved_network_is_unavailable(solved):
    solved['network']._objective = None
    out = lf.get_loadflow_summary()
    assert out['status'] == 'unavailable' and out['unavailable_kind'] == 'not_solved'
    assert 'top' not in out


def test_stale_results_after_topology_change_are_unavailable(solved):
    solved['network'].add('Line', 'L_new', bus0='A', bus1='C', s_nom=10.0, x=0.1)
    out = lf.get_loadflow_summary()
    assert out['status'] == 'unavailable' and out['unavailable_kind'] == 'stale_results'
    assert out['freshness'] == 'stale' and 'top' not in out


def test_parameter_edit_without_topology_change_cannot_relabel_old_results(solved):
    solved['network'].lines.loc['L_hot', 's_nom'] = 1000
    for out in (lf.get_loadflow_summary(), lf.get_loadflow_view()):
        assert out['status'] == 'unavailable' and out['unavailable_kind'] == 'solve_inputs_changed'
        assert 'top' not in out and 'binding' not in out


def test_legacy_solve_without_exact_input_provenance_remains_unavailable(solved):
    solved['network'].meta.pop('assistant_solve_exact_input_fingerprint')
    assert lf.get_loadflow_summary()['unavailable_kind'] == 'solve_provenance_missing'
    assert lf.get_loadflow_view()['unavailable_kind'] == 'solve_provenance_missing'


@pytest.mark.parametrize('patch, kind', [
    ({'status': 'running'}, 'solve_in_progress'),
    ({'status': 'failed', 'condition': 'error'}, 'solve_failed'),
    ({'status': 'aborted'}, 'solve_failed'),
    ({'status': 'completed', 'condition': 'infeasible'}, 'solve_infeasible'),
    ({'status': 'failed', 'condition': 'infeasible_or_unbounded'}, 'solve_infeasible'),
])
def test_solve_lifecycle_states_never_return_earlier_numbers(solved, patch, kind):
    PyPSAService.get_solver_state().update(patch)
    out = lf.get_loadflow_summary()
    assert out['status'] == 'unavailable' and out['unavailable_kind'] == kind
    assert 'top' not in out and out['solve']['status'] == patch['status']


def test_network_without_branches_is_unavailable(solved):
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    n.add('Bus', 'A')
    n.add('Generator', 'G', bus='A', p_nom=1.0)
    n.generators_t.p = _frame(G=[1.0] * 4)
    n._objective = 0.0
    _install(n)
    out = lf.get_loadflow_summary()
    assert out['status'] == 'unavailable' and out['unavailable_kind'] == 'no_ac_branches'


# ── authority, historical, bounds, parity ───────────────────────────────

def test_acl_revocation_is_refused_before_any_result_is_read(solved):
    solved['revoked'] = True
    with pytest.raises(HTTPException) as exc:
        lf.get_loadflow_summary()
    assert exc.value.status_code == 404


def test_no_active_project_is_refused(monkeypatch, solved):
    monkeypatch.undo()
    PyPSAService.get_active_context().loaded_project = None
    with pytest.raises(HTTPException) as exc:
        lf.get_loadflow_summary()
    assert exc.value.detail['error_kind'] == 'run_project_required'


def test_historical_run_without_a_loadflow_section_is_typed_unavailable(solved, tmp_path, monkeypatch):
    monkeypatch.setattr(re_, '_authority', lambda: (tmp_path, PROJECT, OWNER))
    re_.publish_run(tmp_path, PROJECT, run_id='run-old', kind='simulation', status='completed',
                    started_at='2026-10-01T10:00:00+00:00', finished_at='2026-10-01T10:05:00+00:00',
                    mode='lopf', fingerprint='fp', metrics={'objective': {'value': 1.0, 'unit': 'EUR'}},
                    sections={'summary': {'k': 1}})
    out = lf.get_loadflow_summary(run_id='run-old')
    assert out['status'] == 'unavailable'
    assert out['unavailable_kind'] == 'historical_loadflow_unavailable'
    assert out['run_id'] == 'run-old' and out['run_is_current'] is False
    # Issue 06 has landed; a past run never invites reading the current results instead.
    assert 'depends_on' not in out and 'current_results_hint' not in out
    assert out['reason']
    assert 'top' not in out


def test_a_run_without_a_fingerprint_is_not_the_current_result(solved, tmp_path, monkeypatch):
    """A missing fingerprint is unknown, not 'unchanged'. The live load flow
    must not be reported as that run, and the reply must not point at the
    current results as a stand-in."""
    monkeypatch.setattr(re_, '_authority', lambda: (tmp_path, PROJECT, OWNER))
    solved['network'].meta.pop('assistant_solve_input_fingerprint', None)
    re_.publish_run(tmp_path, PROJECT, run_id='run-unmarked', kind='simulation', status='completed',
                    started_at='2026-10-01T10:00:00+00:00', finished_at='2026-10-01T10:05:00+00:00',
                    mode='lopf', metrics={'objective': {'value': 1.0, 'unit': 'EUR'}})
    out = lf.get_loadflow_summary(run_id='run-unmarked')
    assert out['status'] == 'unavailable' and out['run_is_current'] is False
    assert out['inputs_changed_since_run'] is None
    assert 'top' not in out and 'current_results_hint' not in out
    assert out['next_step'] == 'Say this run is not the current result and offer to re-run.'


def test_historical_unknown_run_is_unavailable_not_current_results(solved, tmp_path, monkeypatch):
    monkeypatch.setattr(re_, '_authority', lambda: (tmp_path, PROJECT, OWNER))
    out = lf.get_loadflow_summary(run_id='nope')
    assert out['unavailable_kind'] == 'historical_loadflow_unavailable'
    assert out['reason'] == 'run_not_found'
    assert 'top' not in out


def test_historical_run_with_recorded_section_is_returned_as_historical(solved, tmp_path, monkeypatch):
    monkeypatch.setattr(re_, '_authority', lambda: (tmp_path, PROJECT, OWNER))
    re_.publish_run(tmp_path, PROJECT, run_id='run-lf', kind='simulation', status='completed',
                    started_at='2026-10-01T10:00:00+00:00', finished_at='2026-10-01T10:05:00+00:00',
                    mode='lopf', fingerprint='fp', sections={'loadflow': {'top': [{'name': 'X'}]}})
    out = lf.get_loadflow_summary(run_id='run-lf')
    assert out['status'] == 'ok' and out['scope'] == 'historical'
    # The retained summary is returned like a current read, bound to its run.
    assert out['top'] == [{'name': 'X'}] and 'data' not in out
    assert out['run'] == {'run_id': 'run-lf', 'finished_at': '2026-10-01T10:05:00+00:00',
                          'is_current': False}


def test_payload_is_bounded_with_many_branches(solved):
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    n.add('Bus', 'A')
    n.add('Bus', 'B')
    names = [f'{"L" * 70}_{i:03d}' for i in range(150)]
    for i, name in enumerate(names):
        n.add('Line', name, bus0='A', bus1='B', s_nom=100.0 + i, x=0.1)
    n.lines_t.p0 = pd.DataFrame({nm: [50.0 + i] * 4 for i, nm in enumerate(names)}, index=SNAPSHOTS)
    n.lines_t.p1 = -n.lines_t.p0
    n._objective = 0.0
    _install(n)
    out = lf.get_loadflow_summary(limit=25)
    assert 1 <= len(out['top']) <= 25 and out['top_truncated'] is True
    assert out['top_shown'] == len(out['top'])
    assert out['counts']['ranked'] == 150
    assert all(len(r['name']) <= 80 for r in out['top'])
    body = json.dumps(out)
    assert len(body) <= lf.MAX_PAYLOAD_CHARS < 4000  # harness per-result cap
    for key in ('claims', 'missing_evidence', 'ranking_convention', 'window'):
        assert key in out and key in body


def test_evidence_token_changes_when_results_change(solved):
    first = lf.get_loadflow_summary()['evidence_token']
    assert lf.get_loadflow_summary()['evidence_token'] == first
    solved['network'].lines_t.p0.loc[SNAPSHOTS[0], 'L_hot'] = 99.0
    assert lf.get_loadflow_summary()['evidence_token'] != first
    assert lf.get_loadflow_summary(window_from=1)['evidence_token'] != first


def test_values_match_the_compare_loading_summary_for_shared_ratings(solved):
    from services.compare.loading import _compute_loading_summary

    n = solved['network']
    for name in ('L_p1', 'L_ext', 'L_dyn', 'L_zero', 'L_nan'):
        n.lines_t.p1[name] = np.nan
    n.lines_t.p1['L_hot'] = np.nan
    summary = _compute_loading_summary(n, [], False, True)
    compare = {e.name: e.peak_loading.total for e in summary.lines}
    out = lf.get_loadflow_summary(limit=25)
    for row in out['top']:
        if row['name'] in compare and row['s_max_pu']['min'] == row['s_max_pu']['max']:
            assert row['rating_pct'] == pytest.approx(100 * compare[row['name']], abs=0.01)


def test_tool_is_declared_routed_dispatched_and_read_tier():
    declared = {t['name']: t for t in catalogue.TOOLS}
    assert 'get_loadflow_summary' in declared
    assert catalogue.safety_tier_for('get_loadflow_summary') == 'read'
    assert catalogue.TOOL_ROUTES['get_loadflow_summary'] == catalogue._SERVICE_CALL
    assert chat_tools.DISPATCHERS['get_loadflow_summary'] is lf.get_loadflow_summary
    props = declared['get_loadflow_summary']['input_schema']['properties']
    assert set(props) == {'source', 'limit', 'window_from', 'window_to', 'run_id'}
    assert declared['get_loadflow_summary']['input_schema']['required'] == []


def test_tool_is_offered_in_the_results_toolset_and_all():
    for domain in ('all', 'results'):
        names = {t['name'] for t in toolsets.filter_tools(catalogue.TOOLS, domain)}
        assert 'get_loadflow_summary' in names
    names = {t['name'] for t in toolsets.filter_tools(catalogue.TOOLS, 'network')}
    assert 'get_loadflow_summary' not in names
