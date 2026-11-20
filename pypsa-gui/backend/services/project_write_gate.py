"""The read-only project viewer role, enforced on every HTTP write.

Project-viewer-role 01 (owner decision VIEWER-ROLE-1). A project member is an
editor or a viewer (`project_acl.project_role`). A viewer may open a project and
read everything in it; every write is refused with one kind,
``project_view_only``.

`require_project_write` is an app-level dependency (``main.app``), so it runs
before every handler. It denies by default: every POST/PUT/PATCH/DELETE route
has a row in `RULES` (`tests/test_viewer_role_gate.py` fails on a route with no
row and on a row whose route is gone), and a route the table does not know is
treated as a write to the active project.

A row is one of:

* ``write <target>``: refused for a viewer of the target project. The target
  is declared, never inferred (GK B R1):
  - ``path:<param>``: the project named by that path parameter. Ids nested
    under it (snapshot, report, study, upload, site) live in that project's
    own storage, so the path project is the one written.
  - ``active``: the caller's active project (the network routes and every
    route that names no project).
  - ``handler``: the project is named in the body or by a job id, which a
    dependency cannot see; the handler calls `require_writable` itself.
* ``allow <reason>``: may run for a viewer, with the reason written down.
* ``none <reason>``: writes no project (account, org and personal state, or a
  new project the caller creates).

A caller with no access to the target, or in another org, is never told
``project_view_only``: the gate passes them through and the handler's own 404
stands (GK B R2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from db.models import Project, User
from db.session import get_db
from services import project_acl

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
KIND = "project_view_only"
MESSAGE = "You can view this project but not change it. Ask its owner for edit access."


def refusal() -> HTTPException:
    # The kind is spelled out: the tool-error manifest test derives kinds from literals.
    return HTTPException(403, {"error_kind": "project_view_only", "message": MESSAGE})


def require_writable(db: DBSession, user: User | None, project: Project | None) -> None:
    """Refuse `user` writing `project` when they only view it.

    No user (local mode), no project, or no access at all passes: the caller's
    own 401/404 decides those, so a stranger never learns the project exists.
    """
    if user is None or project is None:
        return
    if project_acl.project_role(db, user, project) == project_acl.VIEW:
        raise refusal()


def is_viewer(db: DBSession, user: User | None, project_uuid: str | None) -> bool:
    """True when `user` only views the project with id `project_uuid`."""
    import uuid

    if user is None or not project_uuid:
        return False
    project = db.get(Project, uuid.UUID(str(project_uuid)))
    return (
        project is not None
        and project_acl.project_role(db, user, project) == project_acl.VIEW
    )


@dataclass(frozen=True)
class Rule:
    kind: str  # "write" | "allow" | "none"
    detail: str  # write: the target; allow/none: the reason


def _parse(table: str) -> dict[tuple[str, str], Rule]:
    rules: dict[tuple[str, str], Rule] = {}
    for raw in table.strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        method, path, kind, detail = line.split(None, 3)
        key = (method, path)
        if key in rules:
            raise ValueError(f"route listed twice: {method} {path}")
        if kind not in {"write", "allow", "none"}:
            raise ValueError(f"unknown kind {kind} for {method} {path}")
        if kind == "write" and not re.fullmatch(r"active|handler|path:\w+", detail):
            raise ValueError(f"unknown write target {detail} for {method} {path}")
        rules[key] = Rule(kind, detail)
    return rules


from services._project_write_rules import TABLE  # noqa: E402  (data module)

RULES = _parse(TABLE)
_FAIL_CLOSED = Rule("write", "active")


@cache
def _full_paths(app) -> dict[int, tuple[tuple[str, re.Pattern], ...]]:
    """Map each route object to the full path(s) it is mounted at.

    Inside a dependency `request.scope["route"]` is the router's own APIRoute,
    whose `path` lacks the include prefix; FastAPI's effective route contexts
    carry the full path.
    """
    found: dict[int, list[tuple[str, re.Pattern]]] = {}

    def walk(routes):
        for route in routes:
            effective = getattr(route, "effective_route_contexts", None)
            if callable(effective):
                walk(effective())
                continue
            original = getattr(route, "original_route", route)
            path = getattr(route, "path", None)
            if path is None:
                continue
            pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")
            found.setdefault(id(original), []).append((path, pattern))

    walk(app.routes)
    return {k: tuple(v) for k, v in found.items()}


def route_path(request: Request) -> str | None:
    route = request.scope.get("route")
    if route is None:
        return None
    candidates = _full_paths(request.app).get(id(route), ())
    url_path = request.scope.get("path", "")
    for path, pattern in candidates:
        if pattern.match(url_path):
            return path
    return candidates[0][0] if candidates else getattr(route, "path", None)


def rule_for(method: str, path: str | None) -> Rule:
    """The route's row; a route the table does not know is a write to the active project."""
    return RULES.get((method, path), _FAIL_CLOSED)


def _path_projects(db: DBSession, user: User, value: str) -> list[Project]:
    """Every project `value` could name: by id and by exact name.

    Handlers resolve `{name}` either way (`resolve_project` with or without
    `name_only`); checking both means a viewer cannot reach a project through
    the reading the gate did not take.
    """
    from services import project_registry

    found = []
    for name_only in (False, True):
        project = project_registry.find_project(db, user, value, name_only=name_only)
        if project is not None and project not in found:
            found.append(project)
    return found


def _active_project(db: DBSession) -> Project | None:
    import uuid

    from services.pypsa_service import PyPSAService

    try:  # an unsaved (scratch) context has no project id: ValueError
        return db.get(
            Project, uuid.UUID(str(PyPSAService.get_active_context().project_uuid))
        )
    except ValueError:
        return None


def require_project_write(request: Request, db: DBSession = Depends(get_db)) -> None:
    """App-level dependency: refuse a viewer's write before the handler runs."""
    method = request.method.upper()
    if method not in WRITE_METHODS:
        return
    rule = rule_for(method, route_path(request))
    if rule.kind != "write" or rule.detail == "handler":
        return
    from deps import optional_user

    user = optional_user(request, db)
    if user is None:
        return
    if rule.detail == "active":
        projects = [p for p in [_active_project(db)] if p is not None]
    else:
        value = request.path_params.get(rule.detail.split(":", 1)[1])
        projects = _path_projects(db, user, str(value)) if value is not None else []
    for project in projects:
        require_writable(db, user, project)
