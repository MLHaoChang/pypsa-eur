"""LC-03 (LC-C3/C4): the common adapter for results kept with a past run.

A result view keeps its facts for past runs through one adapter: at a run's
completion, while that run's results are still the current ones, `capture`
reads the view's native read and returns it; the run-evidence store keeps it
as an immutable, hashed manifest section. Later, `historical` answers that
run from the section alone, never from the current results, and a run
without it refuses with its exact run ID (LC-T08).

`run_binding` decides when a past run IS the current results (its newest
completed run, solved from the current inputs) so the live read answers.

One adapter per family. A family is one `Retained` row and its capture, the
registry entry that names its section (`result_views.ResultView.
historical_section`, held equal to these rows by tests/test_retained_summary.py)
and its read's `run_id` argument. A get_results family names its `kinds`; each
kind is kept by one family only, and `KEPT_KINDS` maps it to that section.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from fastapi import HTTPException

from services import run_evidence
from services.results import dispatch_digest, prices_digest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Digest:
    """A digest family's module (services/results/*_digest.py) on its adapter.

    `build` digests the live network ({'status': 'ok', ...} or unavailable with
    a reason); `for_kind` is one get_results kind's answer from a digest;
    `fields` are what it keeps, named when a run_id read asks for more.
    """

    build: Callable[[], dict]
    for_kind: Callable[[dict, str], Any]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class Retained:
    """One family's retained section.

    `capture(token)` runs on the solver monitor thread with the run's own
    project context and actor bound; it returns the section, or None when the
    read did not answer (no section is stored: the run then refuses).
    `refusal` is the unavailable_kind a past run without the section answers.
    `kinds` are the get_results kinds the section keeps (none for other reads).
    `digest` is set when the section keeps a bounded digest rather than the
    reads themselves; a run_id read of its kinds then answers that digest for
    the current run too, so a run has one shape.
    """

    section: str
    capture: Callable[[dict], dict | None]
    refusal: str
    label: str
    kinds: tuple[str, ...] = ()
    digest: Digest | None = None


def _capture_loadflow(token: dict) -> dict | None:
    """The same oracle get_loadflow_summary reads (looked up at call time)."""
    from services import loadflow_summary

    source = 'ac_pf' if token.get('solver_kind') == 'ac_pf' else 'lopf'
    out = loadflow_summary.get_loadflow_summary(source=source, limit=loadflow_summary.MAX_LIMIT)
    return out if isinstance(out, dict) and out.get('status') == 'ok' else None


SUMMARY_SECTION = 'results_summary'
CAPACITY_SECTION = 'results_capacity'
DISPATCH_SECTION = 'results_dispatch'
PRICES_SECTION = 'results_prices'


def _capture_kinds(kinds: tuple[str, ...]) -> dict | None:
    """These get_results reads as the results API serves them (the route's own
    payload, so nothing the chat layer adds about the current inputs is kept),
    only while they describe this run's own optimal solve."""
    from services import chat_tools
    from services.workflow_tools import solve_freshness

    if solve_freshness().get('results_current') is not True:
        return None
    out = {}
    for kind in kinds:
        payload = chat_tools._payload_or_no_data(
            kind, chat_tools._resolve_results_handler(kind)(), chat_tools._RESULTS_NO_DATA_MESSAGE)
        if isinstance(payload, dict):
            if payload.get('status') == 'no_data' or payload.get('available') is False:
                return None
        elif not isinstance(payload, list) or not payload:
            return None
        out[kind] = payload
    return out


def _results_family(section: str, refusal: str, label: str, kinds: tuple[str, ...]) -> Retained:
    return Retained(section, lambda token: _capture_kinds(kinds), refusal, label, kinds)


def _dispatch_digest() -> dict:
    from services.pypsa_service import PyPSAService

    return dispatch_digest.dispatch_digest(PyPSAService.get_network())


def _prices_digest() -> dict:
    """The Prices tab's reads: the prices digest, price_drivers as the route serves it."""
    from routers.results import _result_df
    from services import chat_tools
    from services.pypsa_service import PyPSAService

    drivers = chat_tools._payload_or_no_data(
        'price_drivers', chat_tools._resolve_results_handler('price_drivers')(),
        chat_tools._RESULTS_NO_DATA_MESSAGE)
    return prices_digest.prices_digest(PyPSAService.get_network(), result_df=_result_df, price_drivers=drivers)


def _digest_family(section: str, label: str, kinds: tuple[str, ...], build: Callable[[], dict],
                   module) -> Retained:
    return Retained(section, lambda token: _capture_digest(build),
                    'historical_results_unavailable', label, kinds,
                    Digest(build, module.for_kind, module.FIELDS))


def _capture_digest(build: Callable[[], dict]) -> dict | None:
    """A digest family's section, under the same rule as `_capture_kinds`."""
    from services.workflow_tools import solve_freshness

    if solve_freshness().get('results_current') is not True:
        return None
    out = build()
    return out if out.get('status') == 'ok' else None


ADAPTERS: tuple[Retained, ...] = (
    Retained('loadflow', _capture_loadflow,
             'historical_loadflow_unavailable', 'load flow'),
    _results_family(SUMMARY_SECTION, 'historical_results_unavailable', 'summary results',
                    ('statistics', 'cost_breakdown')),
    # cost_breakdown, the Capacity Expansion tab's other read, is kept with Summary.
    _results_family(CAPACITY_SECTION, 'historical_results_unavailable', 'capacity results',
                    ('economics_by_carrier',)),
    _digest_family(DISPATCH_SECTION, 'dispatch results', ('generators', 'storage_dispatch'),
                   _dispatch_digest, dispatch_digest),
    _digest_family(PRICES_SECTION, 'price results', ('prices', 'price_drivers'),
                   _prices_digest, prices_digest),
)

# get_results kind -> the one section that keeps it.
KEPT_KINDS: dict[str, str] = {k: a.section for a in ADAPTERS for k in a.kinds}


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
