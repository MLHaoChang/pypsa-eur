"""Authenticated read-only Activity snapshots, scoped to one authorized project."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.models import User
from db.session import get_db
from deps import require_user
from services import assistant_activity, project_registry
from services.pypsa_service import PyPSAService

router = APIRouter()


@router.get('/decisions/{decision_id}')
def inspect_decision(
    decision_id: str,
    project_id: str = Query(max_length=120),
    revision: int = Query(ge=1, le=1000000),
    digest: str = Query(min_length=64, max_length=64),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    from harness import attention
    from harness.session import pending_confirmation_origins

    project = project_registry.resolve_project(db, user, project_id)
    owner, pid = str(user.id), str(project.id)
    records, _warnings = attention.project_records(owner, pid, pending_confirmation_origins(owner, pid))
    record = next((r for r in records if r['source_id'] == decision_id), None)
    if record is None:
        raise HTTPException(404, 'Decision observation is unavailable.')
    if record['revision'] != revision or record['decision_digest'] != digest:
        raise HTTPException(409, 'Decision observation changed. Refresh Activity before inspecting it.')
    return record


@router.get('/activity')
def get_activity(
    project_id: str | None = Query(default=None, max_length=120),
    filter: Literal['all', 'running', 'needs_you', 'scheduled', 'completed'] = 'all',
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=160),
    page_token: str | None = Query(default=None, max_length=160),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    target = project_id or PyPSAService.get_active_context().project_uuid
    if not target:
        raise HTTPException(409, {'error_kind': 'activity_project_required',
                                  'message': 'Activate a saved project to view its activity.'})
    project = project_registry.resolve_project(db, user, target)
    return assistant_activity.snapshot(db=db, user=user, project=project, filter_name=filter,
                                       limit=limit, cursor=cursor, page_token=page_token)
