"""
Saved Results charts for the study report (ui-modern 02, slice 7a) -
`/api/projects/{name}/report-charts`.

  GET    /{name}/report-charts            {"charts": [row...], "limit": 12}
  POST   /{name}/report-charts            multipart `spec` (JSON string) + `png` -> 201 row
  DELETE /{name}/report-charts/{id}       204
  GET    /{name}/report-charts/{id}.png   the picture, inline

The same posture as `routers/reports.py`: `ProjectAccessDep` on every route (a
project of another org is a 404), the project's edit-lock check on the two
writes, and no path built from client input: an id is matched against
`chart_snapshots.ID_RE` first, and an unknown, malformed, ghost or foreign id
all give one 404 body. Both writes are human-only (`services/not_via_chat.py`).

Errors are `{error_kind, message}`: 411 `length_required`, 413
`chart_spec_too_large` / `chart_png_too_large` (the message names the cap,
and nothing is read past it), 415 `chart_png_type`, 422
`chart_snapshot_invalid`, 409 `chart_limit_reached` (with the true `total`) /
`chart_busy`, 404 `chart_not_found`.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession
from starlette.datastructures import UploadFile
from starlette.responses import Response

from db.models import User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from routers.reports import _check_lock
from services import site_service
from services.reports import chart_snapshots as cs
from services.reports import store

router = APIRouter()

#: What the body may hold before anything is read: both parts plus multipart framing.
MAX_BODY_BYTES = cs.MAX_SPEC_BYTES + site_service.MAX_THUMB_BYTES + 64 * 1024


def _err(status: int, kind: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"error_kind": kind, "message": message, **extra})


def _not_found() -> HTTPException:
    return _err(404, "chart_not_found", "No such saved chart in this project.")


def _closing() -> HTTPException:
    return _err(
        409,
        "report_busy",
        "This project is being deleted or moved; nothing was saved.",
    )


def _row(snapshot: cs.ChartSnapshot, current_fp: str | None) -> dict:
    return {
        "id": snapshot.id,
        "view": snapshot.view,
        "title": snapshot.title,
        "figure": snapshot.figure,
        "unit": snapshot.unit,
        "source_line": snapshot.source_line,
        "period": snapshot.period,
        "out_of_date_when_added": snapshot.out_of_date_when_added,
        "created_at": snapshot.created_at,
        "earlier_solve": cs.earlier_solve(snapshot, current_fp),
        "rows_total": snapshot.rows_total,
    }


@router.get("/{name}/report-charts")
def list_report_charts(project: AuthorizedProject = ProjectAccessDep) -> dict:
    fingerprint = cs.active_solve_fingerprint()
    return {
        "charts": [_row(s, fingerprint) for s in cs.list_snapshots(project.directory)],
        "limit": cs.MAX_CHARTS,
    }


@router.post("/{name}/report-charts", status_code=201)
async def add_report_chart(
    request: Request,
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    _check_lock(project, db, user)
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise _err(
            411, "length_required", "The request needs a Content-Length."
        ) from None
    if length > MAX_BODY_BYTES:
        raise _err(
            413,
            "chart_spec_too_large",
            f"The request is over the {MAX_BODY_BYTES // 1024} KB cap for a saved chart.",
        )
    form = await request.form(max_files=1, max_fields=1, max_part_size=MAX_BODY_BYTES)
    spec_text, upload = form.get("spec"), form.get("png")
    if not isinstance(spec_text, str) or not isinstance(upload, UploadFile):
        raise _err(
            422, "chart_snapshot_invalid", "Send a `spec` (JSON text) and a `png` file."
        )
    if (upload.content_type or "").split(";")[0].strip().lower() != "image/png":
        raise _err(
            415, "chart_png_type", "The chart picture must be sent as image/png."
        )
    if len(spec_text.encode()) > cs.MAX_SPEC_BYTES:
        raise _err(
            413,
            "chart_spec_too_large",
            f"The chart spec is over the {cs.MAX_SPEC_BYTES // (1024 * 1024)} MB cap.",
        )
    png = await upload.read(site_service.MAX_THUMB_BYTES + 1)
    try:
        spec = cs.ChartSpec.model_validate(json.loads(spec_text))
        snapshot = cs.add(
            project.directory,
            spec,
            png,
            solve_fingerprint=cs.active_solve_fingerprint(),
        )
    except store.ReportProjectGone:
        raise _not_found() from None
    except store.ReportBusy as exc:
        raise _closing() from exc
    except cs.ChartPngTooLarge as exc:
        raise _err(413, "chart_png_too_large", str(exc)) from exc
    except cs.ChartLimitReached as exc:
        raise _err(
            409, "chart_limit_reached", str(exc), total=exc.total, limit=cs.MAX_CHARTS
        ) from exc
    except cs.ChartBusy as exc:
        raise _err(
            409, "chart_busy", "Saved charts are being written; try again in a moment."
        ) from exc
    except (
        cs.ChartSnapshotError,
        ValueError,
    ) as exc:  # json errors and pydantic's are ValueErrors
        raise _err(
            422, "chart_snapshot_invalid", f"The chart is not a valid snapshot: {exc}"
        ) from exc
    return _row(snapshot, cs.active_solve_fingerprint())


@router.delete("/{name}/report-charts/{chart_id}", status_code=204)
def delete_report_chart(
    chart_id: str,
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> Response:
    _check_lock(project, db, user)
    try:
        cs.delete(project.directory, chart_id)
    except (cs.ChartNotFound, store.ReportProjectGone):
        raise _not_found() from None
    except store.ReportBusy as exc:
        raise _closing() from exc
    except cs.ChartBusy as exc:
        raise _err(
            409, "chart_busy", "Saved charts are being written; try again in a moment."
        ) from exc
    return Response(status_code=204)


@router.get("/{name}/report-charts/{chart_id}.png")
def get_report_chart_png(
    chart_id: str, project: AuthorizedProject = ProjectAccessDep
) -> Response:
    try:
        data = cs.png_bytes(project.directory, chart_id)
    except cs.ChartNotFound:
        raise _not_found() from None
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "Cache-Control": "no-store",
        },
    )
