"""
Human-readable, relative storage paths (phase 1b, Task 4 — spec E1/E2).

Before: `projects_root/<org_uuid>/<project_uuid>/`, stored absolute.
After:  `<Project Name>/` locally, `<org_uuid>/<Project Name>/` on the web,
        stored RELATIVE and rejoined by `project_registry.project_dir`.

Three properties carry the risk, and each has its own section below:

  * **the org segment is a parameter, not a constant.** Dropping it locally is
    what makes `Documents/PyPSA GUI/Projects/Belgium Grid/` possible, and it is
    the reason Task 1 had to land first: with projects at the top of the root,
    `legacy_migrate._scan_root(..., pre_auth_layout=True)` classifies every one
    of them as a claimable leftover. `use_org_segment()` and Task 1's gate must
    therefore be the same predicate, and there is a test that says so.

  * **`taken` is directory names union the filesystem.** Reading `Project.name`
    instead lets a project named `Study_1` be handed the directory of a project
    named `Study:1`; ignoring the filesystem lets a new project adopt and then
    overwrite an orphan directory. Both were impossible while paths were UUIDs.

  * **the backstop is a DB constraint, not an assertion.** Four write paths
    allocate directories; a unique index on `(org_id, storage_path)` covers all
    four by construction, and cannot drift the way four hand-written checks do.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from fastapi import HTTPException

import local_bootstrap
import local_mode
from db.models import Organization, Project, User
from services import project_registry
from services.storage_paths import storage_path_for, taken_names, use_org_segment
from settings import get_settings

_ORG = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "Projects"
    root.mkdir()
    monkeypatch.setenv("PROJECTS_ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


# ── storage_path_for ─────────────────────────────────────────────────────────

def test_the_path_is_relative_and_readable():
    """Relative is the whole of E2: an absolute path bakes one machine's home
    directory into the row, so a restored backup or a synced checkout on a
    second machine points at a directory that does not exist."""
    rel = storage_path_for(_ORG, uuid.uuid4(), "Belgium Grid", set(), org_segment=False)
    assert not rel.is_absolute()
    assert rel == Path("Belgium Grid")


def test_org_segment_false_yields_no_org_component():
    rel = storage_path_for(_ORG, uuid.uuid4(), "Belgium Grid", set(), org_segment=False)
    assert rel.parts == ("Belgium Grid",)


def test_org_segment_true_puts_the_uuid_first():
    """Web mode is structurally unchanged — and it is what keeps `_is_uuid_named`
    skipping top-level entries, so `/unclaimed` stays safe there."""
    rel = storage_path_for(_ORG, uuid.uuid4(), "Belgium Grid", set(), org_segment=True)
    assert rel.parts == (str(_ORG), "Belgium Grid")


def test_forbidden_characters_never_reach_the_path():
    rel = storage_path_for(_ORG, uuid.uuid4(), 'Study:1 <draft>', set(), org_segment=False)
    assert rel == Path("Study_1 _draft_")


def test_a_collision_is_suffixed():
    rel = storage_path_for(
        _ORG, uuid.uuid4(), "Belgium Grid", {"Belgium Grid"}, org_segment=False
    )
    assert rel == Path("Belgium Grid (2)")


def test_a_name_that_sanitises_onto_another_projects_directory_is_suffixed():
    """
    `taken` is the set of sibling DIRECTORY names, never `Project.name`.
    Project "Study:1" lives in "Study_1". A later project literally named
    "Study_1" must NOT be handed the same directory — both rows would resolve
    to one path and each save would clobber the other.
    """
    first = storage_path_for(_ORG, uuid.uuid4(), "Study:1", set(), org_segment=False)
    assert first == Path("Study_1")

    second = storage_path_for(
        _ORG, uuid.uuid4(), "Study_1", {first.name}, org_segment=False
    )
    assert second != first
    assert second == Path("Study_1 (2)")


# ── taken_names ──────────────────────────────────────────────────────────────

def test_taken_names_reads_directory_names_never_display_names(db_session, projects_root):
    """
    The row's `name` is "Study:1"; its DIRECTORY is "Study_1". If this returned
    display names, a later project literally named "Study_1" would see no
    collision and be handed the same directory.
    """
    _seed_project(db_session, name="Study:1", storage_path="Study_1")
    assert taken_names(db_session, _ORG, False) == {"Study_1"}


def test_taken_names_unions_the_filesystem(db_session, projects_root):
    """
    A directory with no row is an orphan (`storage_reconcile.orphan_dirs`).
    Handing its name to a new project makes the first save adopt and overwrite
    it: `routers/projects.py` mkdirs with `exist_ok=True` and then
    `_atomic_write_with`s over its `network.nc`. Impossible while paths were
    `<org>/<uuid>`; introduced by this task. Reserving an orphan's name costs
    one suffix — adopting it costs the user's data.
    """
    orphan = projects_root / "Abandoned Study"
    orphan.mkdir()
    (orphan / "network.nc").write_text("irreplaceable", encoding="utf-8")

    assert taken_names(db_session, _ORG, False) == {"Abandoned Study"}

    rel = storage_path_for(
        _ORG, uuid.uuid4(), "Abandoned Study",
        taken_names(db_session, _ORG, False), org_segment=False,
    )
    assert rel == Path("Abandoned Study (2)")
    assert (orphan / "network.nc").read_text(encoding="utf-8") == "irreplaceable"


def test_taken_names_scans_the_org_subtree_when_the_segment_is_on(db_session, projects_root):
    """With `org_segment=True` the siblings live one level down; scanning the
    root itself would find the org UUID directories and nothing else."""
    (projects_root / str(_ORG)).mkdir()
    (projects_root / str(_ORG) / "Belgium Grid").mkdir()
    (projects_root / "Not This One").mkdir()

    assert taken_names(db_session, _ORG, True) == {"Belgium Grid"}


def test_taken_names_survives_a_missing_root(db_session, tmp_path, monkeypatch):
    """First run: the root does not exist yet, and allocation must still work."""
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "nope"))
    get_settings.cache_clear()
    try:
        assert taken_names(db_session, _ORG, False) == set()
    finally:
        get_settings.cache_clear()


# ── use_org_segment must agree with Task 1's gate ────────────────────────────

@pytest.mark.parametrize("local", [True, False])
def test_the_org_segment_and_the_unclaimed_gate_share_one_predicate(monkeypatch, local):
    """
    Task 1 is the SOLE guard against `_scan_root(pre_auth_layout=True)`
    classifying a live local project as claimable. If these two ever diverge —
    projects at the top of the root while `/unclaimed` is still open — one
    `POST /unclaimed/{name}/import` `shutil.move`s a live project.
    """
    if local:
        monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    else:
        monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)

    gate_closed = False
    try:
        local_mode.reject_in_local_mode()
    except HTTPException as exc:
        assert exc.status_code == 404
        gate_closed = True

    # The org segment is dropped exactly when the unclaimed door is closed.
    assert use_org_segment() is not gate_closed
    assert use_org_segment() is not local


# ── Row creation ─────────────────────────────────────────────────────────────

def _seed_project(db, *, name: str, storage_path: str, org_id=_ORG) -> Project:
    if db.get(Organization, org_id) is None:
        db.add(Organization(id=org_id, name=f"Org {org_id}", created_at=_now()))
        db.flush()
    user = db.scalar(sa.select(User).limit(1))
    if user is None:
        user = User(id=uuid.uuid4(), email=f"{uuid.uuid4()}@example.com",
                    password_hash=None, status="active", created_at=_now())
        db.add(user)
        db.flush()
    project = Project(
        id=uuid.uuid4(), org_id=org_id, name=name, created_by=user.id,
        storage_path=storage_path, parent_project_id=None,
        scenario_description=None, created_at=_now(), updated_at=_now(),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def test_two_rows_in_one_org_cannot_share_a_storage_path(db_session, projects_root):
    """
    The backstop is a DB constraint, not four separate assertions. `create_root`,
    `create_scenario`, `rename_project` and the legacy importer all allocate
    directories; a unique index covers every one of them by construction and
    cannot drift the way hand-written checks do.
    """
    _seed_project(db_session, name="A", storage_path="Shared")
    with pytest.raises(sa.exc.IntegrityError):
        _seed_project(db_session, name="B", storage_path="Shared")
    db_session.rollback()


# ── rename ───────────────────────────────────────────────────────────────────

@pytest.fixture
def desktop_layout(monkeypatch):
    """
    The desktop layout: no org segment. The rename logic itself is mode-neutral
    — `test_rename_keeps_the_org_segment_in_web_mode` covers the other side —
    but the assertions read very differently, so the mode is declared rather
    than inherited from whatever the suite happens to be running in.
    """
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    assert not use_org_segment()


def test_rename_moves_the_directory_and_rewrites_the_row(db_session, projects_root, desktop_layout):
    project = _seed_project(db_session, name="Old Name", storage_path="Old Name")
    old_dir = projects_root / "Old Name"
    old_dir.mkdir()
    (old_dir / "network.nc").write_text("payload", encoding="utf-8")

    project_registry.rename_project(db_session, project, "New Name")

    assert project.storage_path == "New Name"
    assert not old_dir.exists()
    assert (projects_root / "New Name" / "network.nc").read_text(encoding="utf-8") == "payload"


def test_rename_onto_an_occupied_directory_allocates_a_free_name(db_session, projects_root, desktop_layout):
    """
    An orphan directory holding someone's `network.nc` is NOT a reason to
    refuse the rename — `taken_names` unions the filesystem, so the rename
    simply gets the next free name and the orphan is left alone. Overwriting it
    is the outcome this whole task exists to prevent.
    """
    project = _seed_project(db_session, name="Old Name", storage_path="Old Name")
    old_dir = projects_root / "Old Name"
    old_dir.mkdir()
    (old_dir / "network.nc").write_text("mine", encoding="utf-8")
    orphan = projects_root / "Taken"
    orphan.mkdir()
    (orphan / "network.nc").write_text("someone else's", encoding="utf-8")

    project_registry.rename_project(db_session, project, "Taken")

    assert project.name == "Taken"
    assert project.storage_path == "Taken (2)"
    assert (projects_root / "Taken (2)" / "network.nc").read_text(encoding="utf-8") == "mine"
    assert (orphan / "network.nc").read_text(encoding="utf-8") == "someone else's"


@pytest.fixture
def owned_session(db_engine):
    """
    A session that OWNS its transaction.

    `db_session` joins an outer transaction its own teardown rolls back, so a
    `db.rollback()` inside the code under test discards the test's seed rows
    as well and the row can no longer be re-read. `rename_project`'s 409 path
    rolls back, so asserting what the row looks like afterwards needs a session
    whose rollback means what a request-scoped session's means.
    """
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=db_engine)() as session:
        yield session


def test_rename_to_a_name_another_row_holds_is_409(owned_session, projects_root, desktop_layout):
    """The pre-existing `UniqueConstraint("org_id","name")` handler. Its old
    comment claimed `storage_path` is UUID-keyed and does not move; E1 makes
    that false, but the handler itself is still exactly right."""
    project = _seed_project(owned_session, name="Old Name", storage_path="Old Name")
    _seed_project(owned_session, name="Rival", storage_path="Rival")
    old_dir = projects_root / "Old Name"
    old_dir.mkdir()
    (old_dir / "network.nc").write_text("mine", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        project_registry.rename_project(owned_session, project, "Rival")
    assert exc.value.status_code == 409

    assert (old_dir / "network.nc").read_text(encoding="utf-8") == "mine"
    owned_session.refresh(project)
    assert project.storage_path == "Old Name"
    assert project.name == "Old Name"


def test_rename_refuses_a_destination_that_appeared_after_allocation(
    db_session, projects_root, desktop_layout, monkeypatch
):
    """
    The `new_dir.exists()` check runs BEFORE the commit, and it is there for
    the window between allocating a name and moving the directory. Simulate it
    with a stale allocator: doing the check after the commit would leave a
    committed row pointing at a directory that never moved.
    """
    project = _seed_project(db_session, name="Old Name", storage_path="Old Name")
    old_dir = projects_root / "Old Name"
    old_dir.mkdir()
    (old_dir / "network.nc").write_text("mine", encoding="utf-8")
    appeared = projects_root / "New Name"
    appeared.mkdir()
    (appeared / "network.nc").write_text("not mine", encoding="utf-8")

    monkeypatch.setattr(
        project_registry, "allocate_storage_path", lambda *a, **k: Path("New Name")
    )

    with pytest.raises(HTTPException) as exc:
        project_registry.rename_project(db_session, project, "New Name")
    assert exc.value.status_code == 409

    assert (old_dir / "network.nc").read_text(encoding="utf-8") == "mine"
    assert (appeared / "network.nc").read_text(encoding="utf-8") == "not mine"
    db_session.refresh(project)
    assert project.storage_path == "Old Name"
    assert project.name == "Old Name"


def test_web_mode_renames_the_row_and_leaves_the_directory(db_session, projects_root, monkeypatch):
    """
    **Web mode does not move the directory**, which is what it did before this
    phase, when `storage_path` was UUID-keyed.

    Four subsystems cache the resolved directory on the resident
    `ProjectContext`, and eviction writes through it with
    `mkdir(parents=True, exist_ok=True)` — so a moved directory is silently
    RECREATED at the old path and the user's work lands in an orphan no row
    references. In local mode there is one process and every cached copy can be
    rebound; across replicas it cannot, and rename takes no project lock.
    """
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    project = _seed_project(
        db_session, name="Old Name", storage_path=f"{_ORG}/Old Name"
    )
    old_dir = projects_root / str(_ORG) / "Old Name"
    old_dir.mkdir(parents=True)
    (old_dir / "network.nc").write_text("payload", encoding="utf-8")

    project_registry.rename_project(db_session, project, "New Name")

    assert project.name == "New Name"
    assert project.storage_path == f"{_ORG}/Old Name"
    assert (old_dir / "network.nc").read_text(encoding="utf-8") == "payload"
    assert project_registry.project_dir(project) == old_dir


def test_a_row_outside_the_projects_root_is_never_relocated_by_a_rename(
    db_session, projects_root, desktop_layout, tmp_path
):
    """
    `stores_absolute_path` says such rows are left alone by design. Rename
    honouring that is not pedantry: on a deployment with projects on a separate
    mount it is the difference between updating one column and a multi-gigabyte
    cross-volume copy inside one HTTP request.
    """
    outside = tmp_path / "elsewhere" / "Old Name"
    outside.mkdir(parents=True)
    (outside / "network.nc").write_text("payload", encoding="utf-8")
    project = _seed_project(db_session, name="Old Name", storage_path=str(outside))

    project_registry.rename_project(db_session, project, "New Name")

    assert project.name == "New Name"
    assert project.storage_path == str(outside)
    assert (outside / "network.nc").read_text(encoding="utf-8") == "payload"
    assert list(projects_root.iterdir()) == []


def test_rename_rebinds_a_resident_context_that_is_not_the_active_one(
    db_session, projects_root, desktop_layout
):
    """
    The regression E1 created. `rekey_context` keys on `org:uuid`, which a
    rename does not change, so a resident-but-not-active context keeps the old
    path — and the next eviction save mkdirs it back into existence and writes
    `network.nc` there, into an orphan directory no row references.
    """
    from services.pypsa_service import PyPSAService

    project = _seed_project(db_session, name="Old Name", storage_path="Old Name")
    old_dir = projects_root / "Old Name"
    old_dir.mkdir()
    (old_dir / "network.nc").write_text("payload", encoding="utf-8")

    key = project_registry.registry_key(project)
    ctx = PyPSAService.build_context()
    project_registry.bind_context(ctx, project)
    PyPSAService.register(key, ctx)
    try:
        assert ctx.storage_dir == str(old_dir)

        project_registry.rename_project(db_session, project, "New Name")

        assert ctx.storage_dir == str(projects_root / "New Name")
        assert ctx.loaded_project == "New Name"
    finally:
        PyPSAService.drop(key)


def test_rename_compensates_the_row_when_the_move_fails(db_session, projects_root, desktop_layout, monkeypatch):
    """
    The row is committed before the directory moves — there is no transaction
    spanning both. If the move fails the row must be put back, or it points at
    a directory that does not exist and the project vanishes from the UI.
    """
    project = _seed_project(db_session, name="Old Name", storage_path="Old Name")
    old_dir = projects_root / "Old Name"
    old_dir.mkdir()
    (old_dir / "network.nc").write_text("payload", encoding="utf-8")

    def _boom(src, dst):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(project_registry.os, "replace", _boom)

    with pytest.raises(OSError):
        project_registry.rename_project(db_session, project, "New Name")

    db_session.refresh(project)
    assert project.storage_path == "Old Name"
    assert (old_dir / "network.nc").read_text(encoding="utf-8") == "payload"
    assert project_registry.project_dir(project) == old_dir


def test_a_no_op_rename_does_not_suffix_itself(db_session, projects_root, desktop_layout):
    """`taken` must exclude the project's OWN directory, or renaming a project
    to a name that sanitises to its current directory allocates ` (2)` and
    moves the data for no reason."""
    project = _seed_project(db_session, name="Study:1", storage_path="Study_1")
    (projects_root / "Study_1").mkdir()

    project_registry.rename_project(db_session, project, "Study/1")

    assert project.storage_path == "Study_1"


# ── Migration 0003 ───────────────────────────────────────────────────────────

_INDEX = "uq_projects_org_id_storage_path"


def _built_db(tmp_path, revision: str):
    """
    A real Alembic-built database.

    Built from 0001 rather than `create_all` + `stamp`: `create_all` emits the
    CURRENT model, which already carries the unique constraint 0003 adds, so a
    database built that way cannot hold the colliding rows the abort test needs
    (constraint #16 — alembic never runs under pytest, so this has to be
    explicit either way).
    """
    url = f"sqlite+pysqlite:///{tmp_path / 'migrate.db'}"
    cfg = local_bootstrap._alembic_config(url)
    command.upgrade(cfg, revision)
    return url, cfg


def _rows(url):
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return dict(
                conn.execute(sa.text("SELECT name, storage_path FROM projects")).all()
            )
    finally:
        engine.dispose()


def _insert(url, rows):
    engine = sa.create_engine(url)
    org_id = str(_ORG)
    user_id = str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO organizations (id, name, created_at) "
                        "VALUES (:i, 'Org', :t)"),
                {"i": org_id, "t": _now().isoformat()},
            )
            conn.execute(
                sa.text("INSERT INTO users (id, email, status, is_super_admin, created_at) "
                        "VALUES (:i, :e, 'active', 0, :t)"),
                {"i": user_id, "e": f"{user_id}@example.com", "t": _now().isoformat()},
            )
            for name, storage_path in rows:
                conn.execute(
                    sa.text(
                        "INSERT INTO projects (id, org_id, name, created_by, storage_path,"
                        " created_at, updated_at) VALUES (:i, :o, :n, :u, :s, :t, :t)"
                    ),
                    {"i": str(uuid.uuid4()), "o": org_id, "n": name, "u": user_id,
                     "s": storage_path, "t": _now().isoformat()},
                )
    finally:
        engine.dispose()


def test_0003_relativises_only_rows_under_the_root(tmp_path, projects_root):
    outside = tmp_path / "elsewhere" / "Legacy Project"
    url, cfg = _built_db(tmp_path, "0002_session_active_project")
    _insert(url, [
        ("under", str(projects_root / str(_ORG) / "abc")),
        ("already-relative", "Belgium Grid"),
        ("outside", str(outside)),
    ])

    command.upgrade(cfg, "0003_relative_storage_path")

    got = _rows(url)
    assert got["under"] == f"{_ORG}/abc"
    assert got["already-relative"] == "Belgium Grid"
    # By design. On this machine the ONLY row there is lives outside the root,
    # so 0003 does not deliver E2 for it — `--rebase-db` does.
    assert got["outside"] == str(outside)


def test_0003_is_idempotent(tmp_path, projects_root):
    url, cfg = _built_db(tmp_path, "0002_session_active_project")
    _insert(url, [("under", str(projects_root / str(_ORG) / "abc"))])

    command.upgrade(cfg, "0003_relative_storage_path")
    first = _rows(url)
    command.stamp(cfg, "0002_session_active_project")
    command.upgrade(cfg, "0003_relative_storage_path")

    assert _rows(url) == first


def test_0003_aborts_loudly_on_colliding_rows(tmp_path, projects_root):
    """
    Two rows that already share a directory must stop the migration, not be
    silently deduplicated — one of them is a project whose data would then be
    unreachable with nothing in the log saying so.
    """
    shared = str(projects_root / str(_ORG) / "abc")
    url, cfg = _built_db(tmp_path, "0002_session_active_project")
    _insert(url, [("a", shared), ("b", shared)])

    with pytest.raises(sa.exc.IntegrityError):
        command.upgrade(cfg, "0003_relative_storage_path")


def test_0003_creates_the_unique_index(tmp_path, projects_root):
    url, cfg = _built_db(tmp_path, "0003_relative_storage_path")
    engine = sa.create_engine(url)
    try:
        indexes = {ix["name"]: ix for ix in sa.inspect(engine).get_indexes("projects")}
    finally:
        engine.dispose()
    assert _INDEX in indexes
    assert indexes[_INDEX]["unique"]
    assert indexes[_INDEX]["column_names"] == ["org_id", "storage_path"]


def test_0003_downgrade_re_absolutises_and_drops_the_index(tmp_path, projects_root):
    """
    Rollback step 2 runs this, so it is load-bearing for recovery. It must drop
    the index too — otherwise a later re-`upgrade`'s `create_index` fails
    against an index that already exists.
    """
    url, cfg = _built_db(tmp_path, "0002_session_active_project")
    absolute = str(projects_root / str(_ORG) / "abc")
    _insert(url, [("under", absolute)])
    command.upgrade(cfg, "0003_relative_storage_path")

    command.downgrade(cfg, "0002_session_active_project")

    assert _rows(url)["under"] == absolute
    engine = sa.create_engine(url)
    try:
        names = {ix["name"] for ix in sa.inspect(engine).get_indexes("projects")}
    finally:
        engine.dispose()
    assert _INDEX not in names

    # And the round trip works, which is what "drops the index" is really for.
    command.upgrade(cfg, "0003_relative_storage_path")
    assert _rows(url)["under"] == f"{_ORG}/abc"


def test_0003_does_not_treat_a_sibling_root_as_being_under_the_root(tmp_path, projects_root):
    """
    `/x/Projects2` must not be rewritten as if it lived under `/x/Projects`.
    A naive `str.removeprefix` implementation passes every other test here.
    """
    sibling = projects_root.parent / f"{projects_root.name}2" / str(_ORG) / "abc"
    url, cfg = _built_db(tmp_path, "0002_session_active_project")
    _insert(url, [("sibling", str(sibling))])

    command.upgrade(cfg, "0003_relative_storage_path")

    assert _rows(url)["sibling"] == str(sibling)


def test_a_case_only_rename_is_not_refused(db_session, projects_root, desktop_layout):
    """
    `exists()` is case- and normalisation-insensitive on APFS and NTFS while
    `Path.__ne__` is neither, so the destination check saw the project's OWN
    directory as occupied and 409'd. A user could not change the case of their
    own project name.
    """
    project = _seed_project(db_session, name="Belgium Grid", storage_path="Belgium Grid")
    (projects_root / "Belgium Grid").mkdir()
    (projects_root / "Belgium Grid" / "network.nc").write_text("payload", encoding="utf-8")

    project_registry.rename_project(db_session, project, "belgium grid")

    assert project.name == "belgium grid"
    assert (project_registry.project_dir(project) / "network.nc").read_text(
        encoding="utf-8"
    ) == "payload"


def test_a_rename_is_refused_while_a_solve_holds_the_directory(
    db_session, projects_root, desktop_layout, monkeypatch
):
    """
    `SolveJob.storage_dir` is captured at enqueue and never revisited, so
    moving the directory under a live job loses the solve — and after the freed
    name is reused, the stale job saves its result over a DIFFERENT project.
    `_rebind_resident_contexts` cannot reach that cache; refusing is the honest
    answer.
    """
    project = _seed_project(db_session, name="Belgium Grid", storage_path="Belgium Grid")
    old_dir = projects_root / "Belgium Grid"
    old_dir.mkdir()
    (old_dir / "network.nc").write_text("payload", encoding="utf-8")

    key = project_registry.registry_key(project)
    monkeypatch.setattr(
        project_registry,
        "_queued_job_blocks_rename",
        lambda p: project_registry.registry_key(p) == key,
    )

    with pytest.raises(HTTPException) as exc:
        project_registry.rename_project(db_session, project, "New Name")
    assert exc.value.status_code == 409
    assert "solve" in exc.value.detail.lower()

    db_session.refresh(project)
    assert project.name == "Belgium Grid"
    assert (old_dir / "network.nc").read_text(encoding="utf-8") == "payload"


def test_the_solve_guard_reads_the_real_queue(db_session, projects_root, desktop_layout):
    """The guard above is monkeypatched; this one drives the real predicate so
    a change to `list_jobs()`'s shape is caught."""
    project = _seed_project(db_session, name="Belgium Grid", storage_path="Belgium Grid")
    assert project_registry._queued_job_blocks_rename(project) is False
