"""Read two owned single-case receipts; no execution or comparison override."""
import copy
from dataclasses import asdict
import math
import json

from harness.durable import network_guard
from harness import experiment_execution as engine
from services import experiments as ex

_ERRORS = {
    'selection': {'error_kind': 'invalid_experiment_comparison',
                  'message': 'Select two distinct owned network case receipts.'},
    'stale': {'error_kind': 'experiment_comparison_changed',
              'message': 'The comparison sources changed; inspect the current report before exporting.'},
}


def _basis(record, data):
    from services import chat_tools, project_registry, run_evidence, workflow_tools as wt
    with chat_tools._acting() as (db, user):
        child = project_registry.resolve_project(db, user, data['case_project_id'])
        saved = wt._load_context(child)
        if wt._network_input_fingerprint(saved, exact=True) != record['preflight']['case_checks'][0]['exact_input_fingerprint']:
            raise ValueError()
        manifest = run_evidence._load_run(project_registry.project_dir(child), str(child.id), data['run_id'])
        if not manifest or ex._digest(manifest) != data['evidence_sha256']:
            raise ValueError()
        n = saved.network
        return dict(mode=manifest['mode'], objective_basis='recorded_solver_objective',
            window='full_prepared_horizon', objective_unit=data['objective_unit'],
            snapshots_sha256=ex._digest(wt._exact_frame_json(n.snapshots.to_series().to_frame())),
            weights_sha256=ex._digest([wt._exact_frame_json(n.snapshot_weightings),
                wt._exact_frame_json(n.investment_period_weightings)]),
            solver_config_sha256=ex._digest(asdict(saved.solver_state['solver_config'])))


def _case(root, record):
    out = dict(experiment_id=record['experiment_id'], plan_sha256=record.get('plan_sha256'),
        preflight_sha256=ex._digest(record.get('preflight')), status='planned',
        result_available=False, case_project_id=None, job_id=None, run_id=None,
        evidence_sha256=None, cancelled=False, last_error=None,
        case_effects_reserved=False, retry_available=False,
        declared_cases=copy.deepcopy(ex._plan(record)['cases']))
    if '_case_id' in record:
        out['case_id'] = record['_case_id']
    basis = None
    if record['domain'] != 'network' or len(record['cases']) != 1 or not record.get('preflight'):
        out.update(status='unavailable', last_error='experiment_adapter_unavailable')
        return out, basis
    try:
        ledger = engine._read(root, record)
        if ledger is not None:
            data = engine._observe(record, ledger)
            view = engine._view(record, data, root=root, ledger=ledger)
            out.update({k: view[k] for k in ('status', 'case_project_id', 'job_id', 'run_id',
                'evidence_sha256', 'cancelled', 'last_error', 'case_effects_reserved')})
            if data['status'] == 'completed':
                if record.get('baseline_run_id'):
                    from services import run_evidence
                    source = run_evidence.get_run_evidence(record['baseline_run_id'])
                    expected = record['preflight']['baseline']
                    if (source['status'] != 'completed' or not source['evidence_available']
                            or source['fingerprint'] != expected['input_fingerprint']
                            or ex._digest(source) != expected['evidence_sha256']):
                        raise ValueError()
                basis = _basis(record, data)
                out.update(result_available=True, objective=data['objective'], objective_unit=data['objective_unit'])
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, ex.HTTPException):
        out.update(status='unavailable', result_available=False, last_error='experiment_source_unavailable')
        out.pop('objective', None)
        out.pop('objective_unit', None)
    return out, basis


def compare_experiments(experiment_id: str, baseline_experiment_id: str,
                        case_id: str | None = None, baseline_case_id: str | None = None) -> dict:
    with network_guard():
        root, project, owner = ex._authority()
        with ex._LOCK, ex._transaction(root):
            ex._load_index(root)
            # Resolve both scopes before producing any source details.
            record, reference = [ex._read_record(root, eid, project, owner)
                                 for eid in (experiment_id, baseline_experiment_id)]
            if (len(record['cases']) == 2 and case_id is None
                    or len(reference['cases']) == 2 and baseline_case_id is None
                    or experiment_id == baseline_experiment_id and case_id == baseline_case_id):
                raise ex.HTTPException(422, dict(_ERRORS['selection']))
            from harness.experiment_two_case import select_case
            if case_id is not None:
                record = select_case(record, case_id)
            if baseline_case_id is not None:
                reference = select_case(reference, baseline_case_id)
            case, case_basis = _case(root, record)
            base, base_basis = _case(root, reference)
            reasons = []
            for role, result in (('case', case), ('baseline_case', base)):
                if not result['result_available']:
                    reasons.append(role + '_unavailable')
            a, b = record.get('preflight'), reference.get('preflight')
            if not a or not b or a['exact_input_fingerprint'] != b['exact_input_fingerprint']:
                reasons.append('prepared_baselines_differ')
            if a and b and a['baseline'] != b['baseline']:
                reasons.append('baseline_evidence_differs')
            if case_basis is not None and base_basis is not None and case_basis != base_basis:
                reasons.append('comparison_basis_differs')
            out = dict(version=1, protocol='network-case-comparison/1', project_id=project,
                case=case, baseline_case=base, comparable=not reasons,
                status='completed' if not reasons else 'partial' if case['result_available'] or base['result_available'] else 'unavailable',
                incomparable_reasons=reasons, basis=case_basis if not reasons else None,
                inputs_differ=(a['case_checks'][0]['exact_input_fingerprint'] != b['case_checks'][0]['exact_input_fingerprint'])
                    if a and b and len(record['cases']) == len(reference['cases']) == 1 else None,
                deltas={}, note='Delta is case minus baseline_case from verified recorded solver objectives only. '
                    'Declared input changes are shown for both cases. No input was restored, no work was executed '
                    'and no checked-answer or full engineering-cost certification is implied.')
            if not reasons:
                delta = case['objective'] - base['objective']
                pct = delta / base['objective'] * 100 if base['objective'] else None
                if not math.isfinite(delta) or pct is not None and not math.isfinite(pct):
                    out.update(comparable=False, status='unavailable', basis=None, incomparable_reasons=['nonfinite_delta'])
                else:
                    out['deltas']['objective'] = dict(case=dict(value=case['objective'], unit=case['objective_unit']),
                        baseline_case=dict(value=base['objective'], unit=base['objective_unit']),
                        delta=dict(value=delta, unit=case['objective_unit']), delta_pct=pct)
            out['comparison_sha256'] = ex._digest(out)
            return out


def export_experiment_comparison(experiment_id: str, baseline_experiment_id: str, comparison_sha256: str,
                                case_id: str | None = None, baseline_case_id: str | None = None) -> dict:
    from services.chat_tools import _save_agent_export
    with network_guard():
        root, _project, _owner = ex._authority()
        with ex._LOCK, ex._transaction(root):
            report = compare_experiments(experiment_id, baseline_experiment_id, case_id, baseline_case_id)
            if comparison_sha256 != report['comparison_sha256']:
                raise ex.HTTPException(409, dict(_ERRORS['stale']))
            payload = json.dumps(report, sort_keys=True, allow_nan=False, indent=2).encode('utf-8')
            meta = _save_agent_export(payload, 'experiment_comparison_' + report['comparison_sha256'][:16] + '.json',
                                      'application/json')
            return dict(meta, comparison_sha256=report['comparison_sha256'], comparable=report['comparable'])
