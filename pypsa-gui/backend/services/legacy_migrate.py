from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from db.models import Organization, OrgMembership, Project, ProjectMembership, User
from services import project_registry
from services.storage_paths import storage_path_for, taken_names, use_org_segment
from services.tenancy_service import ConflictError, ValidationError
from settings import get_settings


@dataclass(frozen=True)
class LegacyProjectInfo:
    name: str
    parent_project: str | None
    scenario_description: str | None
    has_network: bool
    descendant_names: list[str]


@dataclass
class LegacyClaimResult:
    root: Project
    claimed: list[Project]
    warnings: list[str]


@dataclass(frozen=True)
class _LegacyEntry:
    name: str
    path: Path
    metadata: dict


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _legacy_root() -> Path:
    return get_settings().legacy_root


def _projects_root() -> Path:
    return get_settings().projects_root


def _is_uuid_named(name: str) -> bool:
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return True


def _contains_any_file(directory: Path) -> bool:
    try:
        return any(path.is_file() for path in directory.rglob("*"))
    except OSError:
        return False


def _is_within(candidate: Path, ancestor: Path) -> bool:
    """True when ``candidate`` is ``ancestor`` itself or lives underneath it."""
    resolved_candidate = candidate.absolute()
    resolved_ancestor = ancestor.absolute()
    return resolved_candidate == resolved_ancestor or resolved_ancestor in resolved_candidate.parents


def _read_metadata(project_dir: Path) -> dict:
    path = project_dir / "metadata.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _scan_root(root: Path, *, pre_auth_layout: bool) -> dict[str, _LegacyEntry]:
    """
    Collect the candidate legacy project directories directly under ``root``.

    ``pre_auth_layout`` marks a root that ALSO holds live org-scoped storage
    (``projects_root``), where only the flat leftovers from before auth are
    claimable — see ``_scan_legacy_entries``.
    """
    if not root.exists():
        return {}

    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return {}

    entries: dict[str, _LegacyEntry] = {}
    for directory in children:
        if not directory.is_dir():
            continue
        if pre_auth_layout:
            if directory.name.startswith("."):
                continue
            if _is_uuid_named(directory.name):
                continue
            if not _contains_any_file(directory):
                continue
        entries[directory.name] = _LegacyEntry(
            name=directory.name,
            path=directory,
            metadata=_read_metadata(directory),
        )
    return entries


def _scan_legacy_entries() -> dict[str, _LegacyEntry]:
    """
    Find every unclaimed pre-auth project across both roots.

    ``legacy_root`` is the dedicated parking area an operator can drop projects
    into. ``projects_root`` is scanned too because a user who ran the app
    before auth has their projects sitting there as flat ``<project-name>/``
    directories; once auth is on, that same root gains a
    ``<org_uuid>/<project_uuid>/`` layout. A UUID-named directory is therefore
    an org storage root (never a legacy project) and is skipped, as are hidden
    directories and empty ones. On a name collision ``legacy_root`` wins.
    """
    entries = _scan_root(_projects_root(), pre_auth_layout=True)
    entries.update(_scan_root(_legacy_root(), pre_auth_layout=False))
    return {name: entries[name] for name in sorted(entries)}


def _descendants(entries: dict[str, _LegacyEntry], root_name: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    frontier = [root_name]
    while frontier:
        next_frontier: list[str] = []
        for parent_name in frontier:
            for candidate_name, entry in entries.items():
                if candidate_name in seen or candidate_name == root_name:
                    continue
                if entry.metadata.get("parent_project") != parent_name:
                    continue
                seen.add(candidate_name)
                out.append(candidate_name)
                next_frontier.append(candidate_name)
        frontier = next_frontier
    return out


def list_legacy_projects() -> list[LegacyProjectInfo]:
    entries = _scan_legacy_entries()
    return [
        LegacyProjectInfo(
            name=name,
            parent_project=entry.metadata.get("parent_project"),
            scenario_description=entry.metadata.get("scenario_description"),
            has_network=(entry.path / "network.nc").exists(),
            descendant_names=_descendants(entries, name),
        )
        for name, entry in entries.items()
    ]


def _membership_in_org(db: DBSession, user_id: uuid.UUID, org_id: uuid.UUID) -> OrgMembership | None:
    membership = db.scalar(select(OrgMembership).where(OrgMembership.user_id == user_id))
    if membership is None or membership.org_id != org_id:
        return None
    return membership


def _validate_claim_request(
    db: DBSession,
    *,
    org_id: uuid.UUID,
    owner_id: uuid.UUID,
    member_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    organization = db.get(Organization, org_id)
    if organization is None:
        raise ValidationError("Organization not found")

    if db.get(User, owner_id) is None or _membership_in_org(db, owner_id, org_id) is None:
        raise ValidationError("Owner must belong to the target organization")

    unique_member_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = {owner_id}
    for member_id in member_ids:
        if member_id in seen:
            continue
        if db.get(User, member_id) is None or _membership_in_org(db, member_id, org_id) is None:
            raise ValidationError(f"User {member_id} is not a member of the target organization")
        seen.add(member_id)
        unique_member_ids.append(member_id)
    return [owner_id, *unique_member_ids]


def claim_legacy_project(
    db: DBSession,
    legacy_name: str,
    org_id: uuid.UUID,
    owner_id: uuid.UUID,
    member_ids: list[uuid.UUID],
    include_descendants: bool = True,
) -> LegacyClaimResult:
    entries = _scan_legacy_entries()
    root_entry = entries.get(legacy_name)
    if root_entry is None:
        raise ValidationError("Legacy project not found")

    assigned_user_ids = _validate_claim_request(
        db,
        org_id=org_id,
        owner_id=owner_id,
        member_ids=member_ids,
    )

    claim_names = [legacy_name]
    if include_descendants:
        claim_names.extend(_descendants(entries, legacy_name))

    for name in claim_names:
        exists = db.scalar(select(Project).where(Project.org_id == org_id, Project.name == name))
        if exists is not None:
            raise ConflictError(f"Project '{name}' already exists in the target organization")

    project_map: dict[str, Project] = {}
    # `taken` ACCUMULATES across the loop. Seeded once from the database and
    # the filesystem, then added to after each allocation — without that, two
    # legacy names that sanitise alike (`Study:1` and `Study/1`) are both
    # handed `Study_1` inside a single call.
    segment = use_org_segment()
    taken = taken_names(db, org_id, segment)
    for name in claim_names:
        entry = entries[name]
        project_id = uuid.uuid4()
        relative = storage_path_for(
            org_id, project_id, name, taken, org_segment=segment
        )
        taken.add(relative.name)
        project_map[name] = Project(
            id=project_id,
            org_id=org_id,
            name=name,
            created_by=owner_id,
            storage_path=str(relative),
            parent_project_id=None,
            scenario_description=entry.metadata.get("scenario_description"),
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(project_map[name])

    db.flush()

    warnings: list[str] = []
    for name, project in project_map.items():
        parent_name = entries[name].metadata.get("parent_project")
        if parent_name is None:
            continue
        parent = project_map.get(parent_name)
        if parent is None:
            warnings.append(f"Parent '{parent_name}' for '{name}' was not claimed; imported as a root")
            continue
        project.parent_project_id = parent.id

    for user_id in assigned_user_ids:
        db.add(
            ProjectMembership(
                project_id=project_map[legacy_name].id,
                user_id=user_id,
                assigned_by=owner_id,
                assigned_at=_now(),
            )
        )

    moved_paths: list[tuple[Path, Path]] = []
    try:
        for name in claim_names:
            source = entries[name].path
            # `project_dir`, never `ensure_project_dir`: the very next line
            # treats an existing destination as a conflict, so materialising it
            # here would make every claim fail.
            destination = project_registry.project_dir(project_map[name])
            if destination.exists():
                raise ConflictError(f"Storage path already exists for legacy project '{name}'")
            # A source inside projects_root is moved into a sibling subtree of
            # that same root (projects_root/<org>/<project>). That is safe, but
            # a destination nested UNDER the source would make the move recurse
            # into itself, so refuse it outright.
            if _is_within(destination, source):
                raise ConflictError(
                    f"Storage path for legacy project '{name}' is nested inside its source directory"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved_paths.append((destination, source))

        db.commit()
    except Exception:
        db.rollback()
        for destination, source in reversed(moved_paths):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        raise

    claimed = [project_map[name] for name in claim_names]
    for project in claimed:
        db.refresh(project)
    return LegacyClaimResult(root=project_map[legacy_name], claimed=claimed, warnings=warnings)
