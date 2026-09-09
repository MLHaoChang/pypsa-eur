"""The gridspine action layer — the ONE set of functions the router and the
copilot both call (increment 4, task 3).

The spec's parity rule is structural, not disciplinary: "every study operation
is a backend function first; UI endpoints and chat tools are both thin
wrappers". These tests hold that seam from the service side — every function
works with no request, no `Depends`, no HTTP — so a later router or chat tool
can only be a wrapper, never a second implementation.

THE KIND CHECK IS THE FIRST ASSERTION OF EVERY ACTION. A capacity-expansion
project reaching a gridspine function is a bug in the caller, and it must
fail loudly rather than write a `gridspine/` directory into an unrelated
project.

Runtime: one real 24-hour study with screening on (the only test here that
solves — ~20 s), reused by every read-side test. Everything else is metadata.
"""
import json
import shutil
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from db.models import Project, User
from services import gridspine_service as gs
from services import project_registry


@pytest.fixture
def user_and_db(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(User, seeded_identity["user_id"])


@pytest.fixture
def study(user_and_db):
    db, user = user_and_db
    return gs.create_study(db, user, "Gridspine Study", config={"hours": 24, "k": 1, "window": 24, "overlap": 0})


@pytest.fixture
def plain_project(user_and_db):
    db, user = user_and_db
    return project_registry.create_root(db, user, "Capacity Project")


#: One real study, solved once and copied into each test's fresh project.
#: The project ROWS cannot be shared — `_reset_tenant_tables` truncates
#: `projects` after every test — but the artifacts can, and they are the
#: expensive half. That the copy works at all is the point of task 2: a study
#: directory is readable with no reference to the run that produced it.
_RUN_CACHE = {}


@pytest.fixture
def ran(user_and_db, tmp_path_factory):
    db, user = user_and_db
    project = gs.create_study(
        db, user, "Gridspine Ran",
        config={"hours": 24, "k": 1, "window": 24, "overlap": 0},
    )
    row = db.get(Project, uuid.UUID(project["id"]))
    target = gs.gridspine_dir(row)
    if "dir" not in _RUN_CACHE:
        gs.run_pipeline(db, row, queued=False)
        cache = tmp_path_factory.mktemp("gridspine_cache") / "gridspine"
        shutil.copytree(target, cache)
        _RUN_CACHE["dir"] = cache
    else:
        shutil.copytree(_RUN_CACHE["dir"], target, dirs_exist_ok=True)
    return db, row


# --------------------------------------------------------------------------
# create / kind
# --------------------------------------------------------------------------

def test_create_study_makes_a_planning_project_carrying_its_config(study, user_and_db):
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    assert row is not None
    assert row.project_kind == gs.PLANNING_DYNAMICS
    assert study["kind"] == gs.PLANNING_DYNAMICS
    config = json.loads((gs.gridspine_dir(row) / "config.json").read_text())
    assert (config["hours"], config["k"], config["window"], config["overlap"]) == (24, 1, 24, 0)
    assert config["outdir"] == str(gs.gridspine_dir(row) / "run")


def test_a_new_study_reports_not_started(study, user_and_db):
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    status = gs.get_stage_status(row)
    assert status["status"] == "not started"
    assert all(s["state"] == "pending" for s in status["stages"].values())


def test_an_ordinary_project_defaults_to_capacity_expansion(plain_project):
    assert plain_project.project_kind is None
    assert gs.kind_of(plain_project) == gs.CAPACITY_EXPANSION


@pytest.mark.parametrize("action", [
    lambda db, p: gs.run_pipeline(db, p, queued=False),
    lambda db, p: gs.get_stage_status(p),
    lambda db, p: gs.list_ranked_snapshots(p),
    lambda db, p: gs.get_assumption_ledger(p),
    lambda db, p: gs.export_handoff_bundle(p, 0),
    lambda db, p: gs.set_dispatch_source(db, p, "generate"),
    lambda db, p: gs.edit_template_param(p, "G_BUS_32", "h_s", 4.0, "datasheet", "user"),
    lambda db, p: gs.get_config(p),
    lambda db, p: gs.update_config(p, {"k": 2}),
])
def test_every_action_refuses_a_project_of_the_wrong_kind(plain_project, user_and_db, action):
    db, _user = user_and_db
    with pytest.raises(HTTPException) as exc:
        action(db, plain_project)
    assert exc.value.status_code == 409
    assert "planning" in str(exc.value.detail).lower()
    assert not (project_registry.project_dir(plain_project) / "gridspine").exists()


def test_the_config_is_validated_at_creation_not_at_run_time(user_and_db):
    db, user = user_and_db
    with pytest.raises(HTTPException) as exc:
        gs.create_study(db, user, "Bad Config", config={"hours": 0})
    assert exc.value.status_code == 422
    from sqlalchemy import select
    assert db.scalar(select(Project).where(Project.name == "Bad Config")) is None


# --------------------------------------------------------------------------
# dispatch source
# --------------------------------------------------------------------------

def test_set_dispatch_source_switches_between_generating_and_resuming(study, user_and_db, ran):
    db, user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    _ran_db, ran_row = ran
    src = gs.gridspine_dir(ran_row) / "run"

    updated = gs.set_dispatch_source(db, row, {"from_dispatch": str(src)}, user=user)
    assert updated["from_dispatch"] == str(src)
    assert json.loads((gs.gridspine_dir(row) / "config.json").read_text())["from_dispatch"] == str(src)

    back = gs.set_dispatch_source(db, row, "generate")
    assert back["from_dispatch"] is None


def test_a_dispatch_source_without_a_dispatch_is_refused(study, user_and_db, tmp_path, ran):
    db, user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    _ran_db, ran_row = ran
    empty = gs.gridspine_dir(ran_row) / "run" / "nothing-here"
    empty.mkdir(parents=True, exist_ok=True)
    with pytest.raises(HTTPException) as exc:
        gs.set_dispatch_source(db, row, {"from_dispatch": str(empty)}, user=user)
    assert exc.value.status_code == 422


# `from_dispatch` names a DIRECTORY, and the study reads whatever CSVs are in
# it: the dispatch and the loads reach the caller again through the ranked
# metrics and the handoff bundle. So the path is authorization-bearing input,
# exactly like `from_project`, and these hold the same line for it — CodeQL
# `py/path-injection` on the two `Path(<client string>)` reads that used to be
# here was right, and this is the fix.

def _org_member(db, project) -> User:
    """A plain member of the project's org: in the org, admin of nothing, the
    creator of nothing. `can_access_project` admits an org admin outright, so
    an admin cannot express "same org, no access" — this user can."""
    from db.models import OrgMembership

    user = User(
        id=uuid.uuid4(), email=f"gridspine-member-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=None, status="active", is_super_admin=False,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(user)
    db.flush()
    db.add(OrgMembership(id=uuid.uuid4(), user_id=user.id, org_id=project.org_id, role="member"))
    db.commit()
    return user


def test_a_dispatch_source_is_the_run_directory_of_a_project_the_caller_can_read(
    study, user_and_db, ran,
):
    db, user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    _ran_db, ran_row = ran

    updated = gs.set_dispatch_source(db, row, {"from_dispatch": str(gs.run_dir(ran_row))}, user=user)
    assert updated["from_dispatch"] == str(gs.run_dir(ran_row))


@pytest.mark.parametrize("action", ["set", "patch"])
def test_a_dispatch_source_outside_the_projects_root_is_refused(
    study, user_and_db, tmp_path, action,
):
    """A finished-looking directory the server can read but no project owns.
    The two CSVs are really there, so the ONLY thing that can refuse this is
    the authorization — not the is-it-finished check."""
    db, user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    (tmp_path / "dispatch.csv").write_text("unit,hour,p_mw\n")
    (tmp_path / "loads.csv").write_text("bus,hour,p_mw\n")

    with pytest.raises(HTTPException) as exc:
        if action == "set":
            gs.set_dispatch_source(db, row, {"from_dispatch": str(tmp_path)}, user=user)
        else:
            gs.update_config(row, {"from_dispatch": str(tmp_path)}, db=db, user=user)
    assert exc.value.status_code == 422
    assert "study directory" in str(exc.value.detail)
    assert json.loads((gs.gridspine_dir(row) / "config.json").read_text())["from_dispatch"] is None


@pytest.mark.parametrize("action", ["set", "patch"])
def test_a_dispatch_source_the_caller_cannot_read_is_refused(study, user_and_db, ran, action):
    """The run directory of a real project in the caller's own org — which the
    caller has no access to. The org filter alone would let this through; the
    ACL is what refuses it."""
    db, _admin = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    _ran_db, ran_row = ran
    outsider = _org_member(db, ran_row)

    with pytest.raises(HTTPException) as exc:
        if action == "set":
            gs.set_dispatch_source(db, row, {"from_dispatch": str(gs.run_dir(ran_row))}, user=outsider)
        else:
            gs.update_config(row, {"from_dispatch": str(gs.run_dir(ran_row))}, db=db, user=outsider)
    assert exc.value.status_code == 422
    assert "study directory" in str(exc.value.detail)


@pytest.mark.parametrize("raw", [{"nested": "object"}, ["a", "list"], 7, "\x00null-byte"])
def test_a_dispatch_source_that_is_not_a_path_at_all_is_refused(study, user_and_db, raw):
    """The chat tool takes `**patch`, so a model can put anything here. Every
    shape gets the one refusal, never a TypeError three frames down."""
    db, user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    with pytest.raises(HTTPException) as exc:
        gs.update_config(row, {"from_dispatch": raw}, db=db, user=user)
    assert exc.value.status_code == 422


def test_a_dispatch_source_needs_an_acting_user(study, user_and_db, ran):
    """No `db`/`user` means nothing to authorize against, so the answer is a
    refusal rather than a raw filesystem read — the same posture
    `from_project` takes."""
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    _ran_db, ran_row = ran
    for call in (
        lambda: gs.set_dispatch_source(db, row, {"from_dispatch": str(gs.run_dir(ran_row))}),
        lambda: gs.update_config(row, {"from_dispatch": str(gs.run_dir(ran_row))}),
    ):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 422
        assert "acting user" in str(exc.value.detail)


def test_a_study_is_not_created_with_a_raw_dispatch_path(user_and_db):
    """`from_dispatch` at creation would store an unauthorized path before the
    project exists to authorize against. Same answer `from_network` gives."""
    db, user = user_and_db
    with pytest.raises(HTTPException) as exc:
        gs.create_study(db, user, "Raw Path Study", config={"from_dispatch": "/etc"})
    assert exc.value.status_code == 422
    assert "set_dispatch_source" in str(exc.value.detail)


# --------------------------------------------------------------------------
# run / read
# --------------------------------------------------------------------------

def test_a_finished_run_reports_done_at_every_stage(ran):
    _db, row = ran
    status = gs.get_stage_status(row)
    assert status["status"] == "completed"
    assert {s["state"] for s in status["stages"].values()} == {"done"}
    assert status["selected_hours"]


def test_ranked_snapshots_come_back_as_rows_with_reasons_and_metrics(ran):
    _db, row = ran
    rows = gs.list_ranked_snapshots(row)
    assert rows and isinstance(rows, list)
    first = rows[0]
    assert set(first) >= {"hour", "reasons", "converged", "n1_severity_ac", "load_mw"}
    assert isinstance(first["reasons"], list) and first["reasons"]
    assert json.dumps(rows)          # the router and the chat tool both serialise this


def test_the_ledger_comes_back_as_data_with_provenance_counts(ran):
    _db, row = ran
    ledger = gs.get_assumption_ledger(row)
    assert set(ledger["provenance_counts"]) == {"measured", "datasheet", "assumed"}
    assert sum(ledger["provenance_counts"].values()) > 0
    assert any("assumed" in e for e in ledger["entries"])
    assert ledger["edits"] == []


def test_export_writes_a_zip_holding_the_whole_bundle(ran, tmp_path):
    import zipfile

    from gridspine.handoff.bundle import BUNDLE_FILES

    _db, row = ran
    hour = gs.get_stage_status(row)["selected_hours"][0]
    path = gs.export_handoff_bundle(row, hour)
    assert path.suffix == ".zip"
    names = zipfile.ZipFile(path).namelist()
    for required in BUNDLE_FILES:
        assert any(n.endswith(required) for n in names), required
    assert any(n.endswith(".raw") for n in names) and any(n.endswith(".dyr") for n in names)


def test_exporting_an_hour_that_was_not_selected_is_a_404(ran):
    _db, row = ran
    with pytest.raises(HTTPException) as exc:
        gs.export_handoff_bundle(row, 4242)
    assert exc.value.status_code == 404


def test_result_figures_are_typed_as_not_available_until_a_read_back_exists(ran):
    """Increment 6 made the figures real; before an upload for a bundle hour
    they still answer a typed fact rather than a missing endpoint."""
    _db, row = ran
    hour = gs.get_stage_status(row)["selected_hours"][0]
    answer = gs.fetch_result_figure(row, "vm", hour)
    assert answer == {"available": False, "name": "vm", "hour": hour,
                      "reason": f"no PowerFactory results uploaded for hour {hour} yet"}
    with pytest.raises(HTTPException) as exc:
        gs.fetch_result_figure(row, "voltage_profile", hour)       # not one of the four figures
    assert exc.value.status_code == 422


# --------------------------------------------------------------------------
# template edits — the ledger provenance the spec asks for
# --------------------------------------------------------------------------

def test_a_template_edit_is_recorded_with_who_made_it(study, user_and_db):
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    gs.edit_template_param(row, "G_BUS_32", "h_s", 4.2, "datasheet", "chat")
    overlay = json.loads((gs.gridspine_dir(row) / "templates_overlay.json").read_text())
    entry = overlay["G_BUS_32"]["h_s"]
    assert entry["value"] == 4.2
    assert entry["source"] == "datasheet"
    assert entry["edited_by"] == "chat"
    assert entry["at"]


def test_an_edit_reaches_the_templates_and_the_ledger(study, user_and_db):
    from gridspine.templates.unit_params import load_unit_templates

    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    def _cell(templates, column):
        rows = templates.params
        hit = rows[(rows["unit_id"] == "G_BUS_32") & (rows["param"] == "h_s")]
        assert len(hit) == 1
        return hit.iloc[0][column]

    before = load_unit_templates()
    gs.edit_template_param(row, "G_BUS_32", "h_s", 4.2, "datasheet", "user")
    after = load_unit_templates(overlay=gs.overlay_path(row))
    assert _cell(before, "value") != 4.2
    assert _cell(after, "value") == 4.2
    assert _cell(after, "source") == "datasheet"
    edits = gs.get_assumption_ledger(row)["edits"]
    assert edits and edits[0]["unit_id"] == "G_BUS_32" and edits[0]["edited_by"] == "user"


@pytest.mark.parametrize("bad", [
    ("NO_SUCH_UNIT", "h_s", 4.0, "datasheet", "user"),
    ("G_BUS_32", "no_such_param", 4.0, "datasheet", "user"),
    ("G_BUS_32", "h_s", 4.0, "invented", "user"),
    ("G_BUS_32", "h_s", 4.0, "datasheet", "someone_else"),
    ("G_BUS_32", "h_s", "not a number", "datasheet", "user"),
])
def test_a_nonsense_edit_is_refused_and_writes_no_overlay(study, user_and_db, bad):
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    with pytest.raises(HTTPException) as exc:
        gs.edit_template_param(row, *bad)
    assert exc.value.status_code in (404, 422)
    assert not gs.overlay_path(row).exists()


# --------------------------------------------------------------------------
# the cage, both directions
# --------------------------------------------------------------------------

def test_the_service_touches_only_the_driver_and_schema_layers_of_gridspine():
    """`drivers/` is the only surface the backend calls (spec, "One tool")."""
    import ast
    import pathlib

    src = pathlib.Path(gs.__file__).read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("gridspine"):
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names if a.name.startswith("gridspine"))
    assert imported, "the service is supposed to call gridspine"
    for module in imported:
        assert module.startswith(("gridspine.drivers", "gridspine.schema", "gridspine.templates")), module
    for banned in ("gridspine.static", "gridspine.producers", "gridspine.handoff", "gridspine.ingest"):
        assert banned not in imported, banned


def test_the_service_does_not_edit_sys_path():
    """D5, taken: gridspine is an installed (editable) distribution, so the
    service imports it like any other package. The `sys.path` insert that
    bridged the gap was a stopgap the desktop build could not reproduce; if it
    comes back, the packaging has regressed and this is where it shows."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(gs.__file__).read_text())
    touches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "path"
        and isinstance(node.value, ast.Name) and node.value.id == "sys"
    ]
    assert not touches, "gridspine_service edits sys.path — the D5 stopgap is back"


# --------------------------------------------------------------------------
# config read / update (follow-up A)
# --------------------------------------------------------------------------

def test_get_config_returns_the_stored_config_with_the_projects_own_paths(study, user_and_db):
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    cfg = gs.get_config(row)
    assert (cfg["hours"], cfg["k"], cfg["window"], cfg["overlap"]) == (24, 1, 24, 0)
    assert cfg["outdir"] == str(gs.run_dir(row))
    assert cfg["templates_overlay"] is None


def test_update_config_changes_only_what_it_is_given_and_persists(study, user_and_db):
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    out = gs.update_config(row, {"k": 3, "screen": False})
    assert (out["k"], out["screen"], out["hours"]) == (3, False, 24)
    again = json.loads((gs.gridspine_dir(row) / "config.json").read_text())
    assert (again["k"], again["screen"]) == (3, False)


@pytest.mark.parametrize("bad, status", [
    ({"window": 25}, 422),
    ({"hours": 0}, 422),
    ({"overlap": 24}, 422),
    ({"outdir": "/elsewhere"}, 422),
    ({"from_dispatch": "/nowhere"}, 422),
    ("not a dict", 422),
])
def test_update_config_refuses_a_bad_patch_and_writes_nothing(study, user_and_db, bad, status):
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    before = (gs.gridspine_dir(row) / "config.json").read_text()
    with pytest.raises(HTTPException) as exc:
        gs.update_config(row, bad)
    assert exc.value.status_code == status
    assert (gs.gridspine_dir(row) / "config.json").read_text() == before


def test_update_config_is_refused_while_a_study_is_queued(study, user_and_db, monkeypatch):
    db, _user = user_and_db
    row = db.get(Project, uuid.UUID(study["id"]))
    monkeypatch.setattr(gs, "_active_job_for", lambda project: {"id": "job", "status": "queued"})
    with pytest.raises(HTTPException) as exc:
        gs.update_config(row, {"k": 2})
    assert exc.value.status_code == 409

