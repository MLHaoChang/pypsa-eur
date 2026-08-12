import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

UUID = Uuid


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="invited")
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrgMembership(Base):
    __tablename__ = "org_memberships"
    __table_args__ = (UniqueConstraint("user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32))


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("org_id", "name"),
        # Phase 1b: `storage_path` carries the project's readable NAME, so two
        # rows can now be allocated the same directory and each save would
        # clobber the other. Four write paths allocate one (create_root,
        # create_scenario, rename_project, the legacy importer); a constraint
        # covers all four by construction, where four hand-written checks
        # drift. Named to match migration 0003's index.
        UniqueConstraint(
            "org_id", "storage_path", name="uq_projects_org_id_storage_path"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    storage_path: Mapped[str] = mapped_column(Text)
    parent_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scenario_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Scenario category: 'baseline' | 'scenario' | 'stress', or NULL for a
    # project that has never been categorised.
    #
    # This used to be smuggled as a `[type]` prefix on `scenario_description`,
    # written by one dialog and decoded by one panel — so every other surface
    # rendered the marker as prose, nothing could query by category, and the
    # value could not be corrected after creation without rewriting the
    # description around it. Migration 0004 lifts the prefix out of existing
    # rows into this column.
    #
    # Deliberately a plain string, not a DB enum: the set is presentational and
    # will grow (a user asking for 'sensitivity' should not need a migration on
    # two backends), and an unknown value must degrade to "no badge" rather
    # than break the row. `_SCENARIO_TYPES` in routers/projects.py is the
    # validating edge.
    scenario_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    assigned_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectLock(Base):
    __tablename__ = "project_locks"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    holder_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    purpose: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Which project this session is looking at (Step 0b).
    #
    # This is the POINTER, not the payload. ~110 routes name no project at all
    # (`GET /api/network/buses`, `GET /api/results/cost_breakdown`) because they
    # resolved through a PROCESS-GLOBAL active project — so there was nothing
    # for an ACL to bind to, and on a shared process two users fought over one
    # slot. Moving the pointer here makes it per-user, ACL-checkable, and able
    # to survive a replica change; Step 3 separately moves the resident
    # `pypsa.Network` out of process memory.
    #
    # NULL means "no project bound yet" — the New Project / post-reset state,
    # which is the DEFAULT on first load, not an edge case. ON DELETE SET NULL
    # so deleting a project drops every session's pointer to it rather than
    # dangling (which needs `PRAGMA foreign_keys=ON`, added in Step 0a).
    active_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )


class SolveJobRow(Base):
    """
    One queued solve, persisted.

    The queue used to be purely in-process: ids from `itertools.count(1)`, a
    dict, and nothing on disk. A restart lost every queued job with no trace,
    two replicas both issued id 1, and a shared instance could not say who
    queued a solve.

    `status` is a plain string, not a DB enum, following `Project.scenario_type`:
    the set is presentational and grows (`interrupted` is added in this same
    increment), an unknown value must degrade rather than break the row, and a
    new member should not need a migration on two backends.

    `solver_config` is the JSON snapshot the job was ENQUEUED with. The
    dispatcher used to read `ctx.solver_state["solver_config"]` at RUN time, so
    a `PUT /api/simulation/solver_config` after enqueue silently changed what a
    queued job solved — and durability widens that window to overnight.

    `dismissed_by_user_id` is per-user hiding. Only the enqueuer may dismiss, so
    the column can hold one id and still mean "hidden from that user's listing
    only" without affecting anyone else's.
    """

    __tablename__ = "solve_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The human-readable project NAME, matching `SolveJob.project_id` and the
    # width of `projects.name`.
    project_id: Mapped[str] = mapped_column(String(64))
    # `org_uuid:project_uuid` — the registry identity, and the only tenancy
    # information a job carries.
    project_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    storage_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    enqueued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    solver_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[float | None] = mapped_column(Float, nullable=True)
    solve_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
