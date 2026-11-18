"""Saved personal restrictions, separate from project data and model context."""
import uuid

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from db.models import ChatRestriction
from services import project_acl, project_registry, project_write_gate
from services.pypsa_service import PyPSAService

MODES = frozenset({'standard', 'confirm_changes', 'read_only'})
INITIAL_REVISION = uuid.UUID(int=0)


def _unavailable():
    return HTTPException(503, {'error_kind': 'restriction_unavailable',
                               'message': 'Saved chat restrictions are unavailable. No tool was admitted.'})


def read(db, owner, project):
    """Missing rules preserve server floors; unreadable rules never default."""
    try:
        row = db.get(ChatRestriction, (owner, project))
        if row is None:
            return {'mode': 'standard', 'revision': str(INITIAL_REVISION)}
        if (row.mode not in MODES or not isinstance(row.revision, uuid.UUID)
                or row.revision == INITIAL_REVISION):
            raise _unavailable()
        return {'mode': row.mode, 'revision': str(row.revision)}
    except (SQLAlchemyError, ValueError, TypeError):
        raise _unavailable() from None


def current_project(db, user, expected):
    """Authenticate the exact current binding; the body cannot rebind it."""
    project = project_registry.resolve_project(db, user, expected)
    if str(project.id) != PyPSAService.get_active_context().project_uuid:
        raise HTTPException(409, 'Project binding changed. Refresh access before saving restrictions.')
    return project


def save(db, user, project, revision, mode):
    """Atomic current-revision save; standard is a tombstone, never deletion."""
    if mode not in MODES:
        raise HTTPException(422, 'Unknown restriction mode.')
    previous = read(db, user.id, project.id)
    if previous['revision'] != str(revision):
        raise HTTPException(409, 'Restrictions changed. Refresh access before saving again.')
    next_revision = uuid.uuid4()
    try:
        if revision == INITIAL_REVISION:
            db.add(ChatRestriction(user_id=user.id, project_id=project.id,
                                   revision=next_revision, mode=mode))
        else:
            changed = db.execute(update(ChatRestriction).where(
                ChatRestriction.user_id == user.id, ChatRestriction.project_id == project.id,
                ChatRestriction.revision == revision,
            ).values(revision=next_revision, mode=mode)).rowcount
            if changed != 1:
                db.rollback()
                raise HTTPException(409, 'Restrictions changed. Refresh access before saving again.')
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Restrictions changed. Refresh access before saving again.') from None
    except SQLAlchemyError:
        db.rollback()
        raise _unavailable() from None
    return {'mode': mode, 'revision': str(next_revision)}


def capture(session):
    """Read fresh authoritative owner/project state at ordinary tool admission."""
    from services import chat_tools

    project_id = PyPSAService.get_active_context().project_uuid
    if not project_id:
        return None  # Unsaved drafts have no saved-project rules.
    with chat_tools._acting() as (db, user):
        if session.owner_user_id is not None and session.owner_user_id != str(user.id):
            raise HTTPException(403, 'Chat ownership changed.')
        project = current_project(db, user, project_id)
        # The caller's project role, read fresh at every admission and again at
        # execution (require_current), so an editor demoted to viewer mid-turn
        # is refused at the next write (project-viewer-role 01, GK B R3).
        role = project_acl.project_role(db, user, project)
        return {**read(db, user.id, project.id), 'owner_id': str(user.id), 'project_id': str(project.id),
                'project_role': role}


def _require_role(snapshot, tier):
    # A viewer's write is refused whatever personal rule is saved: a saved
    # 'standard' rule never loosens the role (project-viewer-role 01).
    if snapshot is not None and snapshot.get('project_role') != project_acl.EDIT and tier != 'read':
        raise project_write_gate.refusal()


def require_allowed(snapshot, tier):
    _require_role(snapshot, tier)
    if snapshot is not None and snapshot['mode'] == 'read_only' and tier != 'read':
        raise HTTPException(403, {'error_kind': 'user_restriction',
                                  'message': 'Your saved project rule blocks non-read tools. Change it in Access and approvals to request this action.'})


def require_current(snapshot, session):
    """A later rule (even a change-and-restore) needs fresh tool admission."""
    fresh = capture(session)
    if fresh != snapshot:
        # Edit rights lost since admission refuse with the role's own kind,
        # not a generic revocation (project-viewer-role 01, GK B R3). An
        # unchanged role is never re-judged here: a viewer's read was admitted.
        if (snapshot or {}).get('project_role') == project_acl.EDIT:
            _require_role(fresh, 'write')
        raise HTTPException(409, {'error_kind': 'confirmation_revoked',
                                  'message': 'Your project restrictions changed. Request and review the action again.'})
