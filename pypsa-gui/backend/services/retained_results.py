"""LC-03 (LC-C3/C4): the common adapter for results kept with a past run.

A result view keeps its facts for past runs through one adapter: at a run's
completion, while that run's results are still the current ones, `capture`
reads the view's native read and returns it; the run-evidence store keeps it
as an immutable, hashed manifest section. Later, `historical` answers that
run from the section alone, never from the current results, and a run
without it refuses with its exact run ID (LC-T08).

`run_binding` decides when a past run IS the current results (its newest
completed run, solved from the current inputs) so the live read answers.

One adapter per family; the registry entry (`result_views.ResultView.
historical_section`) names the section. Adding a family is one `Retained`
row, its capture, and its read's `run_id` argument.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from fastapi import HTTPException

from services import run_evidence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Retained:
    """One family's retained section.

    `capture(token)` runs on the solver monitor thread with the run's own
    project context and actor bound; it returns the section, or None when the
    read did not answer (no section is stored: the run then refuses).
    `refusal` is the unavailable_kind a past run without the section answers.
    """

    section: str
    view_id: str
    capture: Callable[[dict], dict | None]
    refusal: str
    label: str


def _capture_loadflow(token: dict) -> dict | None:
    """The same oracle get_loadflow_summary reads (looked up at call time)."""
    from services import loadflow_summary

    source = 'ac_pf' if token.get('solver_kind') == 'ac_pf' else 'lopf'
    out = loadflow_summary.get_loadflow_summary(source=source, limit=loadflow_summary.MAX_LIMIT)
    return out if isinstance(out, dict) and out.get('status') == 'ok' else None


# The keys get_results adds about the CURRENT inputs (LC-12): never kept, since
# a past run's answer must not carry today's freshness.
FRESHNESS_KEYS = frozenset({'inputs_changed_since_solve', 'results_current', 'freshness_note'})
SUMMARY_KINDS = ('statistics', 'cost_breakdown')


def _capture_summary(token: dict) -> dict | None:
    """The Summary tab's two reads, exactly as get_results answers them, only
    while they describe this run's own optimal solve of its inputs."""
    from services import chat_tools
    from services.workflow_tools import solve_freshness

    if solve_freshness().get('results_current') is not True:
        return None
    out = {}
    for kind in SUMMARY_KINDS:
        payload = chat_tools.get_results(result_kind=kind)
        if isinstance(payload, dict):
            if payload.get('status') == 'no_data' or payload.get('available') is False:
                return None
            payload = {k: v for k, v in payload.items() if k not in FRESHNESS_KEYS}
        elif not isinstance(payload, list) or not payload:
            return None
        out[kind] = payload
    return out


ADAPTERS: tuple[Retained, ...] = (
    Retained('loadflow', 'loadflow', _capture_loadflow,
             'historical_loadflow_unavailable', 'load flow'),
    Retained('results_summary', 'summary', _capture_summary,
             'historical_results_unavailable', 'summary results'),
)


def adapter(section: str) -> Retained:
    return next(a for a in ADAPTERS if a.section == section)


def capture(ctx: Any, token: dict) -> tuple[dict[str, dict], list[str]]:
    """Every family's section for a completed run, and the ones not captured.

    Binds the run's project context and actor once. A failure, a refusal or a
    section over the store's size cap stores nothing for that family (an
    oversized section would refuse the whole manifest).
    """
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    sections: dict[str, dict] = {}
    missed: list[str] = []
    binding = PyPSAService.bind_request_context(ctx)
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(token.get('actor_id'))
    try:
        for a in ADAPTERS:
            try:
                out = a.capture(token)
            except Exception:  # noqa: BLE001 — a capture failure never breaks a solve
                logger.exception('%s capture failed for %s', a.section, token.get('run_id'))
                out = None
            if out is not None and len(run_evidence._json_bytes(out)) > run_evidence.MAX_SECTION_BYTES:
                logger.warning('%s for %s exceeds the section cap; not kept',
                               a.section, token.get('run_id'))
                out = None
            if out is None:
                missed.append(a.section)
            else:
                sections[a.section] = out
    finally:
        chat_tools.set_acting_user(previous)
        PyPSAService.reset_request_context(binding)
    return sections, missed


def unavailable(kind: str, message: str, **extra: Any) -> dict:
    return {'status': 'unavailable', 'unavailable_kind': kind, 'message': message, **extra}


def historical(section: str, run_id: str) -> tuple[dict | None, dict | None]:
    """(record, None) with the run's kept section, or (None, typed unavailable).

    A missing, pruned, corrupt or unfinished section refuses with the exact run
    ID and the store's reason; the current results never stand in.
    """
    a = adapter(section)
    try:
        return run_evidence.get_run_evidence(run_id, section), None
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get('error_kind') in ('run_not_found', 'evidence_unavailable'):
            return None, unavailable(
                a.refusal,
                f'No {a.label} was kept for this run: it was solved before {a.label} was '
                'captured, ran outside a chat-started solve, or its section is missing, '
                'corrupt or not finished. The current results are not this run.',
                run_id=run_id, reason=detail.get('reason') or detail.get('error_kind'),
                scope='historical')
        raise


def run_ref(run_id: str, record: dict, *, current: bool) -> dict:
    return {'run_id': run_id, 'finished_at': record.get('finished_at'), 'is_current': current}


def run_binding(run_id: str) -> tuple[dict | None, dict]:
    """Is `run_id` the solve the current results came from?

    Only when it is the project's newest completed run, its recorded inputs are
    the ones the current results were solved from, and nothing changed since.
    Otherwise the current results are not that run and are never reported as it.
    """
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    try:
        record = run_evidence.get_run_evidence(run_id)
    except HTTPException:
        return None, {'run_is_current': False, 'inputs_changed_since_run': None}
    latest = run_evidence.list_project_runs(limit=1, status='completed')['items']
    recorded = record.get('fingerprint')
    n = PyPSAService.get_network()
    # None = unknown: a run without a recorded fingerprint cannot be compared.
    changed = None if not isinstance(recorded, str) else (
        recorded != workflow_tools._network_input_fingerprint(PyPSAService.get_active_context()))
    if (record.get('status') == 'completed' and changed is False
            and latest and latest[0]['run_id'] == run_id
            and getattr(n, 'meta', {}).get('assistant_solve_input_fingerprint') == recorded):
        return (run_ref(run_id, record, current=True),
                {'run_is_current': True, 'inputs_changed_since_run': False})
    return None, {'run_is_current': False, 'inputs_changed_since_run': changed}
