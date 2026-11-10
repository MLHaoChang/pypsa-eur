import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
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
    # Scenario category: 'baseline' | 'scenario' | 'stress' | 'sensitivity', or NULL for a
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
    # What KIND of study this project is: 'capacity_expansion' (the existing
    # flow), 'planning_dynamics' (gridspine), or 'connection' (later). NULL
    # means capacity_expansion — every project that existed before migration
    # 0006 is one, and backfilling a literal would only invent a distinction
    # between "chose the default" and "predates the column".
    #
    # A plain string for `scenario_type`'s reason: the set is presentational
    # and will grow, an unknown value must degrade to the default rather than
    # break the row, and `services/gridspine_service.PROJECT_KINDS` is the
    # validating edge. Orthogonal to `scenario_type`: a planning → dynamics
    # project can still be a baseline or a stress scenario.
    project_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
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


class ChatRestriction(Base):
    """Current personal project rule; never an imported preference or grant."""

    __tablename__ = 'chat_restrictions'
    __table_args__ = (CheckConstraint("mode IN ('standard', 'confirm_changes', 'read_only')",
                                    name='ck_chat_restrictions_mode'),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)


class AssuranceRecordSeq(Base):
    """High-water mark of a mutable assistant-assurance record (issue 02, REPLAY).

    Kept in the DB, outside the record store, so restoring an older sealed
    record file, or the whole store, cannot pass for the current version.
    `epoch` starts a fresh chain when a pruned record id is written again.
    """

    __tablename__ = 'assurance_record_seq'

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    epoch: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


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
    # What this job RUNS: 'solve' (a PyPSA network solve) or 'gridspine' (a
    # planning → dynamics study). NULL means 'solve' — every row written
    # before migration 0007 is one, and the restore path reads it that way, so
    # a restart cannot turn a study into a network solve or the reverse.
    #
    # A plain string, like `status` above and for the same reasons: the set
    # will grow, an unknown value must degrade rather than break the row, and
    # `services/solve_queue.KINDS` is the validating edge.
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    enqueued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    solver_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    # HH-10 (study harness 05): the captured input of an envelope-admitted
    # solve, as JSON (`services/solve_capture.py`); NULL for every other job.
    capture: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class LibraryItem(Base):
    """
    One VERSION of an org-scoped Library item (Edge Investment Case WP1.1a).

    Price curves, dynamic operating envelopes, meter history and forecast
    pairs are not component attributes, so they live here rather than in the
    component-bound `_user_ts` store (spec decision 17). A row is immutable:
    changed content is a new `version`, so a project pinned to v1 keeps v1.
    `path` is relative to the org's library directory, never absolute (same
    reason as `Project.storage_path`). `kind` is a plain string ('series'
    today; tariffs and contracts join in P2) — the validating edge is
    `services/library`.
    """

    __tablename__ = "library_items"
    __table_args__ = (UniqueConstraint("org_id", "kind", "name", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column()
    hash: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssistantJobHold(Base):
    """An assistant job call whose outcome is unknown (assistant-assurance
    issue 16; `harness/job_hold_store.py`).

    Written when the in-chat hold opens and deleted on the evidence that clears
    it, so a new chat, another tab, a second replica or a restart can read it.
    `owner_replica_id` is the process that holds the call's evidence (the
    worker future or solve thread); `lease_until` is that owner's liveness
    lease (wall time; NULL until the owner renews it).
    """

    __tablename__ = 'assistant_job_holds'

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    opened: Mapped[str] = mapped_column(String(32), nullable=False)
    opened_at: Mapped[float] = mapped_column(Float, nullable=False)
    opened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    owner_replica_id: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)


class MemoryItem(Base):
    """One assistant-memory item (assistant-memory plan §1; `services/memory_store.py`).

    Spans projects, so it lives here and not in a project directory. The body
    lives in `MemoryItemVersion`; `current_version` names the live one. A
    confirmed or retired item is never deleted, so a `memory:<id>@v<n>`
    provenance stamp always resolves. `kind`, `status` and `origin` are plain
    strings; the store is the validating edge.
    """

    __tablename__ = "memory_items"
    __table_args__ = (
        Index("ix_memory_items_user_status", "user_id", "status"),
        Index("ix_memory_items_org_scope_status", "org_id", "owner_scope", "status"),
        Index("ix_memory_items_org_kind", "org_id", "project_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    owner_scope: Mapped[str] = mapped_column(String(8))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    project_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    concept_keys: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    current_version: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(16))
    promoted_from: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # A user's nomination to their org (§5): NULL, 'pending' or 'declined'.
    nomination: Mapped[str | None] = mapped_column(String(16), nullable=True)
    recall_count: Mapped[int] = mapped_column(Integer, default=0)
    apply_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryItemVersion(Base):
    """One immutable version of a memory item's body (plan §1)."""

    __tablename__ = "memory_item_versions"
    __table_args__ = (UniqueConstraint("item_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_items.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    body_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(String(160))
    evidence_json: Mapped[str] = mapped_column(Text)
    edited_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryRevision(Base):
    """Per-owner memory revision (plan §3.1, §9 M5): bumped on every write
    that changes what a prompt block shows, never on a counter update.
    `scope_key` is ``user:<uuid>`` or ``org:<uuid>``."""

    __tablename__ = "memory_revisions"

    scope_key: Mapped[str] = mapped_column(String(48), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
