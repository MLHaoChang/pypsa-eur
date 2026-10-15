"""Bounded durable *observations*, never job/result/approval authority.

Registration records an owned origin even with no Activity observer. Only an
identity-bound read can retain a later terminal observation. No background
runner, numerical values, worker objects, arguments or approval tokens live here.
"""
from __future__ import annotations

import math
import time
import uuid

from harness import owned_snapshots


MAX_RECORDS = 512
RETENTION_SECONDS = 30 * 24 * 3600
_TOOLS = {
    'foreground_simulation': {'run_simulation', 'run_ac_pf_stage'},
    'adequacy_study': {'run_fmea_sweep', 'run_frontier_study', 'run_mc_study',
                       'run_coupling_loop', 'run_margin_loop', 'run_eh_study',
                       'run_investment_case'},
    'report_job': {'generate_report', 'regenerate_report_section', 'propose_report_mapping'},
}
_PREFIXES = {'foreground_simulation': 'fg:', 'adequacy_study': 'ad:', 'report_job': 'rpt:'}
_TERMINAL = {
    'foreground_simulation': {'completed', 'failed', 'aborted'},
    'adequacy_study': {'done', 'met', 'failed', 'aborted', 'refused', 'unreachable', 'budget_exhausted'},
    'report_job': {'done', 'failed', 'aborted'},
}
_FIELDS = {'source_id', 'family', 'tool_name', 'native_status', 'created_at', 'observed_at'}


def _validate(record):
    if not isinstance(record, dict) or set(record) != _FIELDS:
        raise ValueError('invalid observation')
    family = record['family']
    if family not in _TOOLS or record['tool_name'] not in _TOOLS[family]:
        raise ValueError('invalid origin family')
    source = record['source_id']
    prefix = _PREFIXES[family]
    if not isinstance(source, str) or not source.startswith(prefix) or uuid.UUID(source[len(prefix):]).hex != source[len(prefix):]:
        raise ValueError('invalid origin id')
    if record['native_status'] not in _TERMINAL[family] | {'running', 'unknown'}:
        raise ValueError('invalid observed status')
    for name in ('created_at', 'observed_at'):
        value = record[name]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 < value <= time.time() + 300):
            raise ValueError('invalid observation time')
    if record['created_at'] > record['observed_at']:
        raise ValueError('invalid observation order')
    return record


def _directory(owner, project):
    return owned_snapshots.directory('chat-activity', owner, project)


def _locked(directory):
    return owned_snapshots.locked(directory)


def _read(directory, owner, project):
    return owned_snapshots.read(directory, owner, project, validate=_validate, max_records=MAX_RECORDS)


def _write(directory, owner, project, records, truncated):
    return owned_snapshots.write(directory, owner, project, records, truncated)


def _update(owner, project, live):
    directory = _directory(owner, project)
    # An empty Activity read must not create an owner store.
    if not live and not directory.exists() and not directory.is_symlink():
        return [], []
    with _locked(directory):
        before, was_truncated = _read(directory, owner, project)
        now = time.time()
        records = {r['source_id']: r for r in before if now - r['observed_at'] < RETENTION_SECONDS}
        prior_incomplete = was_truncated and bool(records)
        for row in live:
            previous = records.get(row['source_id'])
            candidate = _validate(dict(source_id=row['source_id'], family=row['family'],
                                       tool_name=row['tool_name'], native_status=row['native_status'],
                                       created_at=previous['created_at'] if previous else row['registered_at'],
                                       observed_at=max(now, previous['observed_at']) if previous else now))
            if previous:
                if (previous['family'], previous['tool_name']) != (candidate['family'], candidate['tool_name']):
                    raise ValueError('changed activity origin')
                # Losing a source is not a new native outcome. Preserve the
                # last factual state, and never regress an exact terminal read.
                if (previous['native_status'] in _TERMINAL[previous['family']]
                        or candidate['native_status'] in {'unknown', previous['native_status']}):
                    continue
            records[row['source_id']] = candidate
        # Keep the newest *origins*, so old live handles cannot churn the cap
        # by being rediscovered on each poll. The live view remains separate.
        retained = sorted(records.values(), key=lambda r: (r['created_at'], r['source_id']), reverse=True)
        truncated = prior_incomplete or len(retained) > MAX_RECORDS
        warnings = ['activity_history_truncated'] if truncated else []
        retained = retained[:MAX_RECORDS]
        if retained != before or truncated != was_truncated:
            _write(directory, owner, project, retained, truncated)
        return retained, warnings


def remember(owner, project, row):
    """Best-effort origin recording must not alter native work admission."""
    try:
        _update(owner, project, [row])
    except (OSError, ValueError, TypeError, KeyError, TimeoutError):
        # The subsequent read exposes store failure; never store exception bytes.
        pass


def project_rows(owner, project, live):
    """Union exact live reads with retained metadata; no job is reconstructed."""
    try:
        records, warnings = _update(owner, project, live)
    except (OSError, ValueError, TypeError, KeyError, TimeoutError):
        return [dict(row, observation='live') for row in live], ['activity_history_unavailable']
    live_by_id = {r['source_id']: r for r in live}
    rows = []
    for record in records:
        current = live_by_id.pop(record['source_id'], None)
        terminal = record['native_status'] in _TERMINAL[record['family']]
        if (current and current['native_status'] != 'unknown'
                and (not terminal or current['native_status'] == record['native_status'])):
            rows.append(dict(current, observation='live', observed_at=record['observed_at']))
            continue
        rows.append(dict(
            source_id=record['source_id'], family=record['family'],
            title=record['tool_name'].replace('_', ' '), stage=None,
            native_status=record['native_status'] if terminal else 'unknown',
            last_observed_status=record['native_status'], review_required=not terminal,
            observation='retained', observed_at=record['observed_at'],
            reason=('Retained terminal observation; current worker and engineering evidence are not checked.'
                    if terminal else 'Last observed work has no matching live worker. Inspect its native records before retrying; it was not restarted.'),
        ))
    rows.extend(dict(r, observation='live') for r in live_by_id.values())
    return rows, warnings
