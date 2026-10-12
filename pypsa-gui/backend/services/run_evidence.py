"""Immutable run evidence (live-voice issue 06, first slice).

Each run is one directory under ``<project>/run_evidence/<run_id>/``:

* ``running.json`` — optional marker written by ``start_run``.
* ``sections/<name>.json`` — bounded result artifacts, published first.
* ``manifest.json`` — the terminal record, published LAST and atomically, then
  never rewritten. Its presence is what makes a run terminal; a crash before it
  leaves at most a ``running.json`` marker and orphan sections, never a manifest
  that references missing bytes.

A manifest records identifiers, mode, fingerprint, timestamps, software-computed
metrics (value + unit) and a SHA-256 per section artifact. Reads re-verify the
artifact hash, so an old run whose artifacts were pruned, lost or altered gets a
typed ``evidence_unavailable`` for THAT run id; no reader ever falls back to the
newest run. The model supplies run ids and section names only — never paths.

Authority is the active project resolved through the project ACL on every read
(``_authority``), the same pattern as ``assistant_tasks`` / ``durable_conversation``.
``compare_project_runs`` computes deltas in software from recorded metrics only.
Two runs are comparable only when both completed, share a mode, and share an input
fingerprint (or the caller opts in with ``allow_input_changes``); otherwise the
answer is ``comparable: false`` with reasons and no deltas.

Completion wiring:

* Foreground ``run_simulation`` / ``run_ac_pf_stage`` — harness handoff +
  detached solver monitor (``start_foreground_run`` / ``publish_foreground_run``).
* Solve-queue / GridSpine — ``start_queue_run`` on claim, ``publish_queue_run``
  on terminal finish.
* Adequacy/study mesh — watcher started from ``_publish_study``.
* Report jobs — ``_set`` / mapping terminal updates call ``publish_report_run``.

Conversational refs / result UI: see ``discuss_project_run`` (chat_tools) and
``durable_conversation.set_discussed_run``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from services.pypsa_service import PyPSAService

MANIFEST_VERSION = 1
MAX_SECTION_BYTES = 16 * 1024
MAX_RUNS_WITH_ARTIFACTS = 20
MAX_MANIFESTS = 200
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
TERMINAL_STATUSES = frozenset({'completed', 'failed', 'interrupted'})
STATUSES = TERMINAL_STATUSES | {'running'}

_LOCK = threading.RLock()
_RUN_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_SECTION = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
_METRIC = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')


def _refuse(kind: str, message: str, status: int = 409, **extra: Any) -> None:
    detail = {'error_kind': kind, 'message': message}
    detail.update(extra)
    raise HTTPException(status, detail)


def _authority() -> tuple[Path, str, str]:
    from services import chat_tools, project_registry

    ctx = PyPSAService.get_active_context()
    if not ctx.loaded_project or not ctx.project_uuid:
        _refuse('run_project_required',
                'Create, save and activate a project before reading run evidence.')
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, ctx.project_uuid)
        return project_registry.project_dir(project), str(project.id), str(user.id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, allow_nan=False, sort_keys=True).encode('utf-8')
    except (TypeError, ValueError):
        _refuse('invalid_run_evidence', 'Run evidence must be finite, JSON-serialisable values.', 422)
    raise AssertionError  # pragma: no cover


def _valid_run_id(run_id: Any) -> bool:
    return isinstance(run_id, str) and _RUN_ID.fullmatch(run_id) is not None


def _run_dir(root: Path, run_id: str) -> Path:
    return root / 'run_evidence' / run_id


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _check_metrics(metrics: dict | None) -> dict:
    out: dict[str, dict] = {}
    for name, entry in (metrics or {}).items():
        if (not isinstance(name, str) or not _METRIC.fullmatch(name)
                or not isinstance(entry, dict) or set(entry) != {'value', 'unit'}
                or isinstance(entry['value'], bool)
                or not isinstance(entry['value'], (int, float))
                or not isinstance(entry['unit'], str)):
            _refuse('invalid_run_evidence',
                    'Each metric must be {"value": number, "unit": string}.', 422)
        out[name] = {'value': entry['value'], 'unit': entry['unit']}
    _json_bytes(out)
    return out


def _header(*, project_id: str, run_id: str, kind: str, started_at: str | None,
            mode: str | None, fingerprint: str | None, job_id: str | None,
            unsaved_inputs: bool, warnings: list[str] | None, actor_id: str | None) -> dict:
    if not _valid_run_id(run_id):
        _refuse('invalid_run_evidence', 'run_id must be 1-64 letters, digits, "_" or "-".', 422)
    if not isinstance(kind, str) or not kind:
        _refuse('invalid_run_evidence', 'kind is required.', 422)
    return {
        'version': MANIFEST_VERSION,
        'run_id': run_id,
        'project_id': project_id,
        'actor_id': actor_id,
        'kind': kind,
        'job_id': job_id,
        'mode': mode,
        'fingerprint': fingerprint,
        'unsaved_inputs': bool(unsaved_inputs),
        'warnings': [str(w) for w in (warnings or [])][:20],
        'started_at': started_at or _now(),
    }


def start_run(root: Path, project_id: str, *, run_id: str, kind: str,
              started_at: str | None = None, mode: str | None = None,
              fingerprint: str | None = None, job_id: str | None = None,
              unsaved_inputs: bool = False, warnings: list[str] | None = None,
              actor_id: str | None = None) -> dict:
    """Record that a run is in flight. A terminal manifest always wins over this marker."""
    marker = _header(project_id=project_id, run_id=run_id, kind=kind, started_at=started_at,
                     mode=mode, fingerprint=fingerprint, job_id=job_id,
                     unsaved_inputs=unsaved_inputs, warnings=warnings, actor_id=actor_id)
    marker['status'] = 'running'
    with _LOCK:
        run_dir = _run_dir(root, run_id)
        if (run_dir / 'manifest.json').exists():
            _refuse('run_already_finalized', 'This run already has a terminal manifest.',
                    run_id=run_id)
        _atomic_write(run_dir / 'running.json', _json_bytes(marker))
    return copy.deepcopy(marker)


def publish_run(root: Path, project_id: str, *, run_id: str, kind: str, status: str,
                finished_at: str | None = None, started_at: str | None = None,
                mode: str | None = None, fingerprint: str | None = None,
                job_id: str | None = None, unsaved_inputs: bool = False,
                warnings: list[str] | None = None, actor_id: str | None = None,
                metrics: dict | None = None, sections: dict[str, Any] | None = None,
                error: dict | None = None) -> dict:
    """Publish the immutable terminal manifest (sections first, manifest last).

    A repeat with identical content returns the stored manifest; a repeat that
    differs is refused with ``run_already_finalized`` and nothing is changed.
    """
    if status not in TERMINAL_STATUSES:
        _refuse('invalid_run_evidence', f'status must be one of {sorted(TERMINAL_STATUSES)}.', 422)
    manifest = _header(project_id=project_id, run_id=run_id, kind=kind, started_at=started_at,
                       mode=mode, fingerprint=fingerprint, job_id=job_id,
                       unsaved_inputs=unsaved_inputs, warnings=warnings, actor_id=actor_id)
    manifest['status'] = status
    manifest['finished_at'] = finished_at or _now()
    manifest['metrics'] = _check_metrics(metrics)
    manifest['error'] = copy.deepcopy(error) if error else None
    _json_bytes(manifest['error'])

    blobs: dict[str, bytes] = {}
    refs: dict[str, dict] = {}
    for name, payload in (sections or {}).items():
        if not isinstance(name, str) or not _SECTION.fullmatch(name):
            _refuse('invalid_run_evidence', 'Section names are lowercase snake_case.', 422)
        blob = _json_bytes(payload)
        if len(blob) > MAX_SECTION_BYTES:
            _refuse('evidence_too_large',
                    f'Section {name!r} exceeds {MAX_SECTION_BYTES} bytes.', 413,
                    section=name)
        blobs[name] = blob
        refs[name] = {'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob)}
    manifest['sections'] = refs

    with _LOCK:
        run_dir = _run_dir(root, run_id)
        existing = _read_manifest_file(run_dir / 'manifest.json')
        if existing is not None:
            if _same_completion(existing, manifest, started_at, finished_at):
                return existing
            _refuse('run_already_finalized',
                    'This run already has a different terminal manifest; manifests are immutable.',
                    run_id=run_id)
        marker = _read_json(run_dir / 'running.json')
        if marker and not started_at:
            manifest['started_at'] = marker.get('started_at', manifest['started_at'])
        for name, blob in blobs.items():
            _atomic_write(run_dir / 'sections' / (name + '.json'), blob)
        _atomic_write(run_dir / 'manifest.json', _json_bytes(manifest))
        (run_dir / 'running.json').unlink(missing_ok=True)
        _enforce_retention(root)
    return copy.deepcopy(manifest)


def _same_completion(existing: dict, new: dict, started_at: str | None,
                     finished_at: str | None) -> bool:
    keys = ['project_id', 'kind', 'status', 'mode', 'fingerprint', 'job_id',
            'unsaved_inputs', 'metrics', 'error', 'sections']
    if started_at:
        keys.append('started_at')
    if finished_at:
        keys.append('finished_at')
    return all(existing.get(k) == new.get(k) for k in keys)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _read_manifest_file(path: Path) -> dict | None:
    return _read_json(path)


def _load_run(root: Path, project_id: str, run_id: str) -> dict | None:
    """Manifest (terminal) or running marker for THIS project; None otherwise."""
    if not _valid_run_id(run_id):
        return None
    run_dir = _run_dir(root, run_id)
    record = _read_manifest_file(run_dir / 'manifest.json') or _read_json(run_dir / 'running.json')
    if record is None or record.get('project_id') != project_id or record.get('run_id') != run_id:
        return None
    return record


def _section_state(root: Path, record: dict, name: str) -> tuple[bytes | None, str | None]:
    """(bytes, None) when the artifact verifies, else (None, reason)."""
    ref = (record.get('sections') or {}).get(name)
    if ref is None:
        return None, 'section_not_recorded'
    path = _run_dir(root, record['run_id']) / 'sections' / (name + '.json')
    try:
        blob = path.read_bytes()
    except OSError:
        return None, 'artifact_missing'
    if hashlib.sha256(blob).hexdigest() != ref.get('sha256') or len(blob) != ref.get('bytes'):
        return None, 'artifact_corrupt'
    return blob, None


def _evidence_available(root: Path, record: dict) -> bool:
    names = list((record.get('sections') or {}))
    return bool(names) and all(_section_state(root, record, n)[1] is None for n in names)


def _all_records(root: Path, project_id: str) -> list[dict]:
    base = root / 'run_evidence'
    records = []
    if not base.is_dir():
        return records
    for child in base.iterdir():
        if child.is_dir():
            record = _load_run(root, project_id, child.name)
            if record is not None:
                records.append(record)
    records.sort(key=lambda r: (r.get('started_at') or '', r['run_id']), reverse=True)
    return records


def _enforce_retention(root: Path) -> None:
    base = root / 'run_evidence'
    entries = []
    for child in base.iterdir():
        record = _read_manifest_file(child / 'manifest.json') if child.is_dir() else None
        if record is not None:
            entries.append((record.get('started_at') or '', child.name, child))
    entries.sort(reverse=True)
    for _, _, child in entries[MAX_MANIFESTS:]:
        shutil.rmtree(child, ignore_errors=True)
    for _, _, child in entries[MAX_RUNS_WITH_ARTIFACTS:MAX_MANIFESTS]:
        shutil.rmtree(child / 'sections', ignore_errors=True)


def _summary(root: Path, record: dict) -> dict:
    return {
        'run_id': record['run_id'],
        'kind': record.get('kind'),
        'status': record['status'],
        'started_at': record.get('started_at'),
        'finished_at': record.get('finished_at'),
        'mode': record.get('mode'),
        'fingerprint': record.get('fingerprint'),
        'job_id': record.get('job_id'),
        'unsaved_inputs': bool(record.get('unsaved_inputs')),
        'evidence_available': _evidence_available(root, record),
    }


def list_project_runs(limit: int = DEFAULT_LIMIT, offset: int = 0, status: str | None = None) -> dict:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        _refuse('invalid_run_limit', f'Choose a run limit of 1-{MAX_LIMIT}.', 422)
    if type(offset) is not int or offset < 0:
        _refuse('invalid_run_offset', 'offset must be a non-negative integer.', 422)
    if status is not None and status not in STATUSES:
        _refuse('invalid_run_status', f'status must be one of {sorted(STATUSES)}.', 422)
    root, project_id, _owner = _authority()
    with _LOCK:
        records = _all_records(root, project_id)
        if status is not None:
            records = [r for r in records if r['status'] == status]
        page = records[offset:offset + limit]
        return {
            'items': [_summary(root, r) for r in page],
            'total_count': len(records),
            'limit': limit,
            'offset': offset,
            'has_more': offset + len(page) < len(records),
            'note': 'Most recent first, scoped to the active project. Use get_run_evidence with a run_id for detail.',
        }


def get_run_evidence(run_id: str, section: str | None = None) -> dict:
    if section is not None and (not isinstance(section, str) or not _SECTION.fullmatch(section)):
        _refuse('invalid_run_section', 'section must be a lowercase snake_case name.', 422)
    root, project_id, _owner = _authority()
    with _LOCK:
        record = _load_run(root, project_id, run_id)
        if record is None:
            _refuse('run_not_found', 'Run not found in this project.', 404)
        out = _summary(root, record)
        out['warnings'] = list(record.get('warnings') or [])
        out['metrics'] = copy.deepcopy(record.get('metrics') or {})
        out['error'] = copy.deepcopy(record.get('error'))
        declared = list((record.get('sections') or {}))
        if section is None:
            out['sections'] = [
                {'name': n, 'available': _section_state(root, record, n)[1] is None}
                for n in declared
            ]
            return out
        if record['status'] == 'running':
            _refuse('evidence_unavailable', 'This run has not finished.', run_id=run_id,
                    section=section, reason='run_not_finished', run_status='running')
        blob, reason = _section_state(root, record, section)
        if reason is not None:
            _refuse('evidence_unavailable',
                    'The requested evidence is not available for this run.',
                    run_id=run_id, section=section, reason=reason, run_status=record['status'],
                    available_sections=[n for n in declared
                                        if _section_state(root, record, n)[1] is None])
        out['section'] = section
        out['data'] = json.loads(blob)
        return out


MAX_COMPARE_METRICS = 20


def _require_run(root: Path, project_id: str, run_id: Any, role: str) -> dict:
    record = _load_run(root, project_id, run_id)
    if record is None:
        _refuse('run_not_found', f'{role} {run_id!r} was not found in this project.', 404,
                run_id=run_id)
    return record


def _require_verified_evidence(root: Path, record: dict) -> None:
    for name in list((record.get('sections') or {})):
        reason = _section_state(root, record, name)[1]
        if reason is not None:
            _refuse('evidence_unavailable',
                    'The recorded evidence for this run is not available, so it cannot be compared.',
                    run_id=record['run_id'], section=name, reason=reason,
                    run_status=record['status'])


def compare_project_runs(run_id: str, baseline_run_id: str, metrics: list[str] | None = None,
                         allow_input_changes: bool = False) -> dict:
    """Software-computed metric deltas ``run - baseline`` between two runs of the active project.

    Only recorded metrics are used; a metric missing on either side or recorded with
    different units is reported, never estimated. Incomparable runs return
    ``comparable: false`` and an empty ``deltas`` mapping.
    """
    if run_id == baseline_run_id:
        _refuse('invalid_run_comparison', 'Choose two different runs to compare.', 422)
    if metrics is not None:
        if (not isinstance(metrics, list) or not 1 <= len(metrics) <= MAX_COMPARE_METRICS
                or any(not isinstance(m, str) or not _METRIC.fullmatch(m) for m in metrics)):
            _refuse('invalid_run_metrics',
                    f'metrics must be 1-{MAX_COMPARE_METRICS} recorded metric names.', 422)
    if not isinstance(allow_input_changes, bool):
        _refuse('invalid_run_comparison', 'allow_input_changes must be true or false.', 422)
    for role, value in (('run_id', run_id), ('baseline_run_id', baseline_run_id)):
        if not _valid_run_id(value):
            _refuse('invalid_run_comparison', f'{role} must be a run_id from list_project_runs.', 422)
    root, project_id, _owner = _authority()
    with _LOCK:
        run = _require_run(root, project_id, run_id, 'run_id')
        base = _require_run(root, project_id, baseline_run_id, 'baseline_run_id')
        reasons: list[dict] = []
        for role, record in (('run', run), ('baseline', base)):
            if record['status'] != 'completed':
                reasons.append({'reason': 'run_not_completed', 'role': role,
                                'run_id': record['run_id'], 'status': record['status']})
        if not reasons:
            _require_verified_evidence(root, run)
            _require_verified_evidence(root, base)
        mode_run, mode_base = run.get('mode'), base.get('mode')
        if not mode_run or not mode_base:
            reasons.append({'reason': 'mode_unknown', 'run': mode_run, 'baseline': mode_base})
        elif mode_run != mode_base:
            reasons.append({'reason': 'mode_mismatch', 'run': mode_run, 'baseline': mode_base})
        fp_run, fp_base = run.get('fingerprint'), base.get('fingerprint')
        fingerprints_match = fp_run is not None and fp_run == fp_base
        if not fingerprints_match and not allow_input_changes:
            reasons.append({'reason': 'fingerprint_mismatch' if fp_run and fp_base
                            else 'fingerprint_unknown',
                            'run': fp_run, 'baseline': fp_base})
        out: dict[str, Any] = {
            'run': _summary(root, run),
            'baseline': _summary(root, base),
            'comparable': not reasons,
            'incomparable_reasons': reasons,
            'mode': mode_run if mode_run == mode_base else None,
            'fingerprints_match': fingerprints_match,
            'inputs_differ': bool(fp_run and fp_base and fp_run != fp_base),
            'warnings': {'run': list(run.get('warnings') or []),
                         'baseline': list(base.get('warnings') or [])},
            'deltas': {},
            'unmatched_metrics': [],
        }
        if reasons:
            out['note'] = ('No deltas are computed for incomparable runs. Inspect each run with '
                           'get_run_evidence instead.')
            return out
        run_metrics = run.get('metrics') or {}
        base_metrics = base.get('metrics') or {}
        names = list(metrics) if metrics is not None else sorted(set(run_metrics) | set(base_metrics))
        for name in names:
            a, b = run_metrics.get(name), base_metrics.get(name)
            if a is None or b is None:
                out['unmatched_metrics'].append({
                    'metric': name,
                    'reason': 'not_recorded_in_both',
                    'recorded_in': [r for r, m in (('run', a), ('baseline', b)) if m is not None]})
                continue
            if a['unit'] != b['unit']:
                out['unmatched_metrics'].append({
                    'metric': name, 'reason': 'units_differ',
                    'run_unit': a['unit'], 'baseline_unit': b['unit']})
                continue
            delta = a['value'] - b['value']
            out['deltas'][name] = {
                'run': {'value': a['value'], 'unit': a['unit']},
                'baseline': {'value': b['value'], 'unit': b['unit']},
                'delta': {'value': delta, 'unit': a['unit']},
                'delta_pct': (delta / b['value'] * 100.0) if b['value'] else None,
            }
        out['note'] = ('delta = run - baseline, computed from recorded metrics only; '
                       'delta_pct is null when the baseline value is zero.')
        return out


_FOREGROUND_KINDS = {'lopf': 'simulation', 'ac_pf': 'ac_pf'}
OBJECTIVE_UNIT = 'EUR'


def foreground_run_id(job_id: str) -> str:
    """``fg:<hex>`` job handle -> a run id that satisfies the run-id grammar."""
    return 'fg-' + str(job_id).split(':', 1)[-1]


def _solver_mode(ctx: Any, kind: str | None) -> str | None:
    if kind == 'ac_pf':
        return 'ac_pf'
    config = ctx.solver_state.get('solver_config')
    mode = getattr(config, 'mode', None)
    return mode if isinstance(mode, str) and mode else kind


def start_foreground_run(ctx: Any, *, job_id: str, tool_name: str,
                         kind: str | None) -> dict | None:
    """Mark a foreground solve as running and return the token for publication.

    Best-effort: returns None when no project/identity authority is available
    (e.g. an unsaved scratch network), so a solve never fails because it cannot
    be recorded.
    """
    from services import chat_tools, dirty_state

    try:
        root, project_id, owner = _authority()
        token = {
            'root': root, 'project_id': project_id, 'actor_id': owner,
            'run_id': foreground_run_id(job_id), 'job_id': job_id,
            'kind': _FOREGROUND_KINDS.get(kind or '', 'simulation'),
            'solver_kind': kind, 'tool_name': tool_name,
            'started_at': _now(), 'unsaved_inputs': dirty_state.is_dirty(ctx),
        }
        start_run(root, project_id, run_id=token['run_id'], kind=token['kind'],
                  started_at=token['started_at'], mode=_solver_mode(ctx, kind),
                  job_id=job_id, unsaved_inputs=token['unsaved_inputs'], actor_id=owner)
        return token
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001 — recording must never break a solve
        import logging
        logging.getLogger(__name__).exception('run evidence start failed')
        return None


def _input_fingerprint(ctx: Any) -> str | None:
    try:
        from services.workflow_tools import _network_input_fingerprint
        return _network_input_fingerprint(ctx)
    except Exception:  # noqa: BLE001 — unknown beats a wrong fingerprint
        return None


def publish_foreground_run(token: dict | None, ctx: Any) -> dict | None:
    """Publish the terminal manifest once the solver state is terminal; else do nothing."""
    if not token:
        return None
    with ctx.solver_state_lock:
        state = dict(ctx.solver_state)
    status = state.get('status')
    if status not in ('completed', 'failed', 'aborted', 'idle'):
        return None
    kind = state.get('kind')
    metrics: dict[str, dict] = {}
    summary: dict[str, Any] = {'solver_kind': kind, 'condition': state.get('condition')}
    run_status = 'completed' if status == 'completed' else (
        'failed' if status == 'failed' else 'interrupted')
    error = None
    if run_status == 'completed':
        objective = state.get('objective')
        if isinstance(objective, (int, float)) and not isinstance(objective, bool):
            metrics['objective'] = {'value': objective, 'unit': OBJECTIVE_UNIT}
        solve_time = state.get('solve_time')
        if isinstance(solve_time, (int, float)) and not isinstance(solve_time, bool):
            metrics['solve_seconds'] = {'value': solve_time, 'unit': 's'}
        for key in ('ac_pf_converged_count', 'ac_pf_total_snapshots'):
            value = state.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                metrics[key] = {'value': value, 'unit': 'snapshots'}
        summary['metrics'] = copy.deepcopy(metrics)
    elif run_status == 'failed':
        error = {'error_kind': 'solver_failed',
                 'message': str(state.get('condition') or 'The solve failed.')[:500]}
    try:
        return publish_run(
            token['root'], token['project_id'], run_id=token['run_id'], kind=token['kind'],
            status=run_status, started_at=token['started_at'],
            mode=_solver_mode(ctx, token['solver_kind']),
            fingerprint=_input_fingerprint(ctx) if run_status == 'completed' else None,
            job_id=token['job_id'], unsaved_inputs=token['unsaved_inputs'],
            actor_id=token['actor_id'], metrics=metrics, error=error,
            sections={'summary': summary} if run_status == 'completed' else None)
    except HTTPException:
        return None


# ── Solve-queue / study / report publication (issue 06 continuation) ─────────

_STUDY_KIND = {
    'fmea_sweep': 'fmea_sweep',
    'frontier': 'frontier',
    'mc': 'monte_carlo',
    'coupling_loop': 'coupling_loop',
    'margin_loop': 'margin_loop',
    'eh_study': 'eh_study',
    'investment_case': 'investment_case',
}

_WORKER_COMPLETED = frozenset({'completed', 'done', 'met'})
_WORKER_FAILED = frozenset({
    'failed', 'budget_exhausted', 'refused', 'unreachable',
})
_WORKER_INTERRUPTED = frozenset({'aborted', 'interrupted', 'idle'})


def map_worker_status(status: str | None) -> str | None:
    """Map family-specific terminal labels onto run-evidence statuses."""
    if status in _WORKER_COMPLETED:
        return 'completed'
    if status in _WORKER_FAILED:
        return 'failed'
    if status in _WORKER_INTERRUPTED:
        return 'interrupted'
    return None


def queue_run_id(job_id: Any) -> str:
    raw = str(job_id).replace('-', '')
    return 'q-' + raw[:60]


def study_run_id(study_key: str, nonce: str | None = None) -> str:
    suffix = (nonce or uuid.uuid4().hex)[:12]
    key = re.sub(r'[^a-z0-9_]', '', study_key.lower())[:24] or 'study'
    return f'ad-{key}-{suffix}'[:64]


def report_run_id(report_id: str | None, nonce: str | None = None) -> str:
    base = re.sub(r'[^A-Za-z0-9_-]', '', str(report_id or ''))[:40]
    if not base:
        base = (nonce or uuid.uuid4().hex)[:12]
    return ('rpt-' + base)[:64]


def _root_for_project(
    project_id: str | None, storage_dir: str | None = None,
) -> Path | None:
    if storage_dir:
        return Path(storage_dir)
    if not project_id:
        return None
    try:
        ctx = PyPSAService.get_active_context()
        if (
            ctx.project_uuid
            and str(ctx.project_uuid) == str(project_id)
            and ctx.storage_dir
        ):
            return Path(ctx.storage_dir)
    except Exception:  # noqa: BLE001
        return None
    return None


def _queue_project_id(job: Any) -> str:
    """SolveJob.project_id is the display name; the UUID lives in project_key."""
    key = getattr(job, 'project_key', None)
    if isinstance(key, str) and ':' in key:
        return key.split(':', 1)[1]
    return str(getattr(job, 'project_id', '') or '')


def start_queue_run(job: Any) -> dict | None:
    """Best-effort running marker when a solve-queue job is claimed."""
    try:
        project_id = _queue_project_id(job)
        root = _root_for_project(project_id, getattr(job, 'storage_dir', None))
        if root is None or not project_id:
            return None
        job_id = str(getattr(job, 'id', '') or '')
        run_id = queue_run_id(job_id)
        kind = 'gridspine' if getattr(job, 'kind', None) == 'gridspine' else 'solve_queue'
        actor = getattr(job, 'enqueued_by_user_id', None)
        started = _now()
        start_run(
            root, project_id, run_id=run_id, kind=kind, started_at=started,
            mode=kind, job_id=job_id,
            actor_id=str(actor) if actor is not None else None,
        )
        token = {
            'root': root, 'project_id': project_id, 'run_id': run_id,
            'job_id': job_id, 'kind': kind, 'started_at': started,
            'actor_id': str(actor) if actor is not None else None,
        }
        try:
            job.run_evidence_token = token  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        return token
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('queue run evidence start failed')
        return None


def publish_queue_run(job: Any) -> dict | None:
    """Publish a terminal solve-queue / GridSpine run from the job record."""
    try:
        token = getattr(job, 'run_evidence_token', None)
        project_id = (token or {}).get('project_id') or _queue_project_id(job)
        root = (
            Path(token['root']) if token and token.get('root')
            else _root_for_project(project_id, getattr(job, 'storage_dir', None))
        )
        if root is None or not project_id:
            return None
        status = map_worker_status(getattr(job, 'status', None))
        if status is None:
            return None
        job_id = str(getattr(job, 'id', '') or '')
        run_id = (token or {}).get('run_id') or queue_run_id(job_id)
        kind = (token or {}).get('kind') or (
            'gridspine' if getattr(job, 'kind', None) == 'gridspine' else 'solve_queue'
        )
        metrics: dict[str, dict] = {}
        summary: dict[str, Any] = {
            'queue_kind': getattr(job, 'kind', None),
            'condition': getattr(job, 'condition', None),
        }
        error = None
        if status == 'completed':
            objective = getattr(job, 'objective', None)
            if isinstance(objective, (int, float)) and not isinstance(objective, bool):
                metrics['objective'] = {'value': objective, 'unit': OBJECTIVE_UNIT}
            solve_time = getattr(job, 'solve_time', None)
            if isinstance(solve_time, (int, float)) and not isinstance(solve_time, bool):
                metrics['solve_seconds'] = {'value': solve_time, 'unit': 's'}
            summary['metrics'] = copy.deepcopy(metrics)
        elif status == 'failed':
            err = getattr(job, 'error', None)
            error = {
                'error_kind': 'solver_failed',
                'message': str(err or 'The queued job failed.')[:500],
            }
        return publish_run(
            root, project_id, run_id=run_id, kind=kind, status=status,
            started_at=(token or {}).get('started_at'),
            mode=kind, job_id=job_id,
            actor_id=(token or {}).get('actor_id'),
            metrics=metrics, error=error,
            sections={'summary': summary} if status == 'completed' else None,
        )
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('queue run evidence publish failed')
        return None


def start_study_run(study_key: str, record: dict) -> dict | None:
    """Mark an adequacy/study mesh worker as a running evidence run."""
    try:
        root, project_id, owner = _authority()
        run_id = study_run_id(study_key)
        kind = _STUDY_KIND.get(study_key, study_key)
        started = _now()
        start_run(
            root, project_id, run_id=run_id, kind=kind, started_at=started,
            mode=study_key, job_id=run_id, actor_id=owner,
        )
        token = {
            'root': root, 'project_id': project_id, 'actor_id': owner,
            'run_id': run_id, 'kind': kind, 'study_key': study_key,
            'started_at': started,
        }
        record['_run_evidence'] = token
        return token
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('study run evidence start failed')
        return None


def publish_study_run(study_key: str, record: dict) -> dict | None:
    """Publish when a study record leaves the running state."""
    try:
        status = map_worker_status(record.get('status'))
        if status is None:
            return None
        token = record.get('_run_evidence')
        if not token:
            # Late publish without a start marker — still attempt under authority.
            try:
                root, project_id, owner = _authority()
            except HTTPException:
                return None
            token = {
                'root': root, 'project_id': project_id, 'actor_id': owner,
                'run_id': study_run_id(study_key),
                'kind': _STUDY_KIND.get(study_key, study_key),
                'started_at': _now(),
            }
        summary = {
            'study_key': study_key,
            'status': record.get('status'),
            'stage': record.get('stage'),
        }
        error = None
        if status == 'failed':
            raw = record.get('status')
            if raw == 'unreachable':
                message = (
                    'The study finished and no plan meets the target (unreachable).'
                )
            else:
                message = str(record.get('error') or 'The study failed.')[:500]
            error = {
                'error_kind': 'study_failed',
                'message': message,
            }
        return publish_run(
            token['root'], token['project_id'], run_id=token['run_id'],
            kind=token['kind'], status=status, started_at=token.get('started_at'),
            mode=study_key, job_id=token['run_id'], actor_id=token.get('actor_id'),
            error=error,
            sections={'summary': summary} if status == 'completed' else None,
        )
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('study run evidence publish failed')
        return None


def watch_study_run(study_key: str, record: dict) -> None:
    """Daemon: wait until ``record['status']`` is terminal, then publish."""
    start_study_run(study_key, record)

    def _watch() -> None:
        deadline = time.monotonic() + 6 * 60 * 60
        while time.monotonic() < deadline:
            if map_worker_status(record.get('status')) is not None:
                publish_study_run(study_key, record)
                return
            time.sleep(0.25)
        # Timed out still running — leave the running marker; do not invent a terminal.

    threading.Thread(
        target=_watch, daemon=True, name=f'run-evidence-{study_key}',
    ).start()


def start_report_run(record: dict) -> dict | None:
    try:
        root, project_id, owner = _authority()
        report_id = record.get('report_id')
        run_id = report_run_id(report_id if isinstance(report_id, str) else None)
        started = _now()
        start_run(
            root, project_id, run_id=run_id, kind='report', started_at=started,
            mode=record.get('mode') if isinstance(record.get('mode'), str) else 'report',
            job_id=run_id, actor_id=owner,
        )
        token = {
            'root': root, 'project_id': project_id, 'actor_id': owner,
            'run_id': run_id, 'kind': 'report', 'started_at': started,
            'report_id': report_id,
        }
        record['_run_evidence'] = token
        return token
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('report run evidence start failed')
        return None


def publish_report_run(record: dict) -> dict | None:
    try:
        status = map_worker_status(record.get('status'))
        if status is None:
            return None
        token = record.get('_run_evidence')
        if not token:
            try:
                root, project_id, owner = _authority()
            except HTTPException:
                return None
            token = {
                'root': root, 'project_id': project_id, 'actor_id': owner,
                'run_id': report_run_id(
                    record.get('report_id')
                    if isinstance(record.get('report_id'), str) else None
                ),
                'kind': 'report', 'started_at': _now(),
            }
        summary = {
            'report_id': record.get('report_id'),
            'mode': record.get('mode'),
            'version': record.get('version'),
            'status': record.get('status'),
        }
        error = None
        if status == 'failed':
            error = {
                'error_kind': 'report_failed',
                'message': str(record.get('error') or 'The report job failed.')[:500],
            }
        return publish_run(
            token['root'], token['project_id'], run_id=token['run_id'],
            kind='report', status=status, started_at=token.get('started_at'),
            mode=summary.get('mode') or 'report', job_id=token['run_id'],
            actor_id=token.get('actor_id'), error=error,
            sections={'summary': summary} if status == 'completed' else None,
        )
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('report run evidence publish failed')
        return None
