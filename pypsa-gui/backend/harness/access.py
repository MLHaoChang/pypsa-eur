"""Read-only current access projection; never an action capability or reservation."""
from datetime import UTC, datetime
import os
import time

from fastapi import HTTPException

from db.models import ProjectLock
from harness import budget, confirm, restrictions, session as sessions
from services import project_registry
from services.pypsa_service import PyPSAService


def snapshot(*, db, user, session, project_id, ui_mode, checked):
    ctx = PyPSAService.get_active_context()
    project = None
    if project_id is not None:
        project = project_registry.resolve_project(db, user, project_id)
        if str(project.id) != ctx.project_uuid:
            raise HTTPException(409, 'Project binding changed. Refresh the current project.')
    elif ctx.project_uuid:
        project = project_registry.resolve_project(db, user, ctx.project_uuid)

    rule = restrictions.read(db, user.id, project.id) if project is not None else None
    lock_state = 'unlocked' if project is not None else 'not_applicable'
    blockers = []
    if project is not None:
        # Unlike get_lock, this read must not delete an expired row or commit.
        lock = db.get(ProjectLock, project.id)
        if lock is not None:
            expiry = lock.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry > datetime.now(UTC):
                lock_state = 'yours' if lock.holder_user_id == user.id else 'other'
                if lock_state == 'other':
                    blockers.append('project_writes_locked')

    session_view = None
    if session is not None:
        if not session._lock.acquire(blocking=False):
            raise HTTPException(409, 'Chat state is changing. Refresh access shortly.')
        try:
            used = session.usage_acc['output_tokens']
            missing_usage_turns_exhausted = (not session.usage_reported
                                             and session.turns_started >= budget.MAX_TURNS_PER_SESSION)
            session_view = {'id': session.session_id,
                            'output_tokens_used': used if session.usage_reported else None,
                            'turn_in_flight': session._turn_in_flight}
        finally:
            session._lock.release()
        if used >= budget.MAX_OUTPUT_TOKENS_PER_SESSION:
            blockers.append('session_output_budget_exhausted')
        if missing_usage_turns_exhausted:
            blockers.append('session_unreported_usage_turn_limit')

    mode = os.environ.get('PYPSA_GUI_ANSWER_ASSURANCE_MODE', 'disabled')
    if mode not in {'disabled', 'shadow', 'pilot', 'strict'}:
        mode = 'unavailable'
        blockers.append('chat_policy_unavailable')
    checks = mode == 'strict' or (mode == 'pilot' and checked)
    if checked and mode not in {'pilot', 'strict'}:
        blockers.append('requested_checks_unavailable')
    return {
        'version': 1, 'read_only': True, 'observed_at': time.time(),
        'scope': {'project_id': str(project.id) if project else None,
                  'project_name': project.name if project else None},
        'selection': {'ui_mode': ui_mode, 'checked_requested': checked},
        'restrictions': rule,
        'confirmation_rules': [
            {'tier': tier,
             'native': confirm.confirmation_policy(tier, guided=ui_mode == 'guided',
                                                   restriction_mode=rule['mode'] if rule else 'standard'),
             'external': confirm.confirmation_policy(tier, guided=ui_mode == 'guided', external=True,
                                                     restriction_mode=rule['mode'] if rule else 'standard')}
            for tier in ('read', 'write', 'destructive', 'execution', 'execution_long_running')
        ],
        'project_lock': lock_state, 'blockers': blockers, 'session': session_view,
        'checking': {'server_mode': mode, 'effective_for_selection': checks,
                     'scope': 'bounded_profiles'},
        'limits': {
            'output_tokens_per_turn': budget.MAX_OUTPUT_TOKENS_PER_TURN,
            'tool_calls_per_turn': budget.MAX_TOOL_CALLS_PER_TURN,
            'output_tokens_per_session': budget.MAX_OUTPUT_TOKENS_PER_SESSION,
            'turns_without_reported_usage': budget.MAX_TURNS_PER_SESSION,
            'daily_project_tokens': (budget.PYPSA_GUI_CHAT_DAILY_TOKEN_CAP
                                     if budget.PYPSA_GUI_CHAT_DAILY_TOKEN_CAP > 0 else None),
            'confirmation_ttl_seconds': sessions.CONFIRMATION_TTL_SECONDS,
        },
    }
