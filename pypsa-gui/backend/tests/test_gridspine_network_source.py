"""A study fed by the SOLVED NETWORK of one of the user's own projects
(increment 5, D3) — the flow the product exists for: solve a capacity-expansion
project in the GUI, save it, study its dispatch.

The service never takes a path from a caller for this. `{"from_project":
name}` is resolved under the acting user like every other lookup (404 for
unknown and foreign alike), refused for anything that is not a solved, saved
capacity-expansion project, and stored as the absolute path of that project's
`network.nc` — which the driver then checks against the detailed grid's unit
set (the identity map; `tests/gridspine/test_network_source.py`).

The source network is the IEEE 39-bus TEMPLATE the GUI bundles, solved here
once (a 24 h MILP, seconds) and copied into each test's project — so this file
also holds the template to the shape the identity map needs.

Runtime: one 24 h exact unit commitment for the fixture; one study of it with
k=1 and no screening (~10 s).
"""
import json
import shutil
import uuid

import pytest
from fastapi import HTTPException

from db.models import Project, User
from project_templates._build import build_ieee39
from services import gridspine_service as gs
from services import project_registry

CONFIG = {"hours": 24, "k": 1, "window": 24, "overlap": 0, "screen": False}
_NC = {}


def _solved_template_nc(tmp_path_factory):
    """`build_ieee39()` solved and saved, once per session."""
    if "path" not in _NC:
        n = build_ieee39()
        status, condition = n.optimize(solver_name="highs")
        assert (status, condition) == ("ok", "optimal")
        path = tmp_path_factory.mktemp("solved39") / "network.nc"
        n.export_to_netcdf(str(path))
        _NC["path"] = path
    return _NC["path"]


@pytest.fixture
def user_and_db(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(User, seeded_identity["user_id"])


@pytest.fixture
def study(user_and_db):
    db, user = user_and_db
    created = gs.create_study(db, user, "Network Study", config=CONFIG)
    return db.get(Project, uuid.UUID(created["id"]))


@pytest.fixture
def solved(user_and_db, tmp_path_factory):
    """A capacity-expansion project holding the solved template network."""
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Solved 39")
    shutil.copy(_solved_template_nc(tmp_path_factory), project_registry.ensure_project_dir(row) / "network.nc")
    return row


def _nc(row):
    return str(project_registry.project_dir(row) / "network.nc")


# --------------------------------------------------------------------------
# the template is the detailed grid
# --------------------------------------------------------------------------

def test_the_ieee39_template_is_the_detailed_grids_unit_set_on_its_buses():
    from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net

    registry = registry_from_net(load_case39_res())
    n = build_ieee39()
    assert set(n.generators.index) == set(registry.index)
    for unit in registry.index:
        assert n.generators.at[unit, "bus"] == registry.at[unit, "bus"], unit
    assert len(n.snapshots) == 24 and hasattr(n.snapshots, "hour")   # a DatetimeIndex, as the GUI expects
    assert set(n.generators["carrier"]) == {"thermal", "import", "wind", "solar"}
    assert n.generators_t.p.empty                                      # shipped UNSOLVED, like every template


# --------------------------------------------------------------------------
# choosing the source
# --------------------------------------------------------------------------

def test_a_solved_project_becomes_the_source_by_name_and_reads_back_by_name(user_and_db, study, solved):
    db, user = user_and_db
    out = gs.set_dispatch_source(db, study, {"from_project": "Solved 39"}, user=user)
    assert out["from_network"] == _nc(solved)
    assert out["from_dispatch"] is None
    stored = json.loads((gs.gridspine_dir(study) / "config.json").read_text())
    assert stored["from_network"] == _nc(solved)
    assert gs.get_config(study, db=db)["from_project"] == "Solved 39"
    assert gs.get_config(study)["from_project"] is None      # no db, no name — never a guess


def test_an_unknown_or_foreign_project_is_404_like_every_lookup(user_and_db, study):
    db, user = user_and_db
    with pytest.raises(HTTPException) as exc:
        gs.set_dispatch_source(db, study, {"from_project": "No Such Project"}, user=user)
    assert exc.value.status_code == 404


def test_the_study_itself_and_another_study_are_refused(user_and_db, study):
    db, user = user_and_db
    gs.create_study(db, user, "Other Study", config=CONFIG)
    with pytest.raises(HTTPException) as exc:
        gs.set_dispatch_source(db, study, {"from_project": "Network Study"}, user=user)
    assert exc.value.status_code == 422 and "own dispatch source" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        gs.set_dispatch_source(db, study, {"from_project": "Other Study"}, user=user)
    # refused for its KIND, not for lacking a network — the reason a user can act on
    assert exc.value.status_code == 422 and "capacity-expansion" in exc.value.detail


def test_a_project_without_a_saved_network_is_refused(user_and_db, study):
    db, user = user_and_db
    project_registry.create_root(db, user, "Never Saved")
    with pytest.raises(HTTPException) as exc:
        gs.set_dispatch_source(db, study, {"from_project": "Never Saved"}, user=user)
    assert exc.value.status_code == 422
    assert "no saved network" in exc.value.detail


def test_an_unsolved_project_is_refused_with_the_reason(user_and_db, study):
    db, user = user_and_db
    row = project_registry.create_root(db, user, "Unsolved 39")
    build_ieee39().export_to_netcdf(str(project_registry.ensure_project_dir(row) / "network.nc"))
    with pytest.raises(HTTPException) as exc:
        gs.set_dispatch_source(db, study, {"from_project": "Unsolved 39"}, user=user)
    assert exc.value.status_code == 422
    assert "not solved" in exc.value.detail


def test_from_project_needs_an_acting_user(user_and_db, study, solved):
    db, _user = user_and_db
    with pytest.raises(HTTPException) as exc:
        gs.set_dispatch_source(db, study, {"from_project": "Solved 39"})
    assert exc.value.status_code == 422


def test_a_raw_network_path_cannot_be_set_at_creation_or_by_patch(user_and_db, study):
    db, user = user_and_db
    with pytest.raises(HTTPException) as exc:
        gs.create_study(db, user, "Raw Path", config={**CONFIG, "from_network": "/etc/passwd"})
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        gs.update_config(study, {"from_network": "/etc/passwd"})
    assert exc.value.status_code == 422


# --------------------------------------------------------------------------
# running from it, and one source at a time
# --------------------------------------------------------------------------

def test_the_study_runs_from_the_projects_network_and_the_source_is_exclusive(user_and_db, study, solved):
    db, user = user_and_db
    gs.set_dispatch_source(db, study, {"from_project": "Solved 39"}, user=user)
    status = gs.run_pipeline(db, study, queued=False)
    assert status["status"] == "completed", status
    manifest = json.loads((gs.run_dir(study) / "manifest.json").read_text())
    assert manifest["dispatch_source"]["network"] == _nc(solved)
    assert manifest["window"] is None                          # the solve happened in the GUI, not here

    # a study directory as the source clears the network …
    out = gs.update_config(study, {"from_dispatch": str(gs.run_dir(study))}, db=db, user=user)
    assert out["from_dispatch"] == str(gs.run_dir(study)) and out["from_network"] is None
    # … the project clears the directory …
    out = gs.set_dispatch_source(db, study, {"from_project": "Solved 39"}, user=user)
    assert out["from_network"] == _nc(solved) and out["from_dispatch"] is None
    # … and "generate" clears both
    out = gs.set_dispatch_source(db, study, "generate", user=user)
    assert out["from_network"] is None and out["from_dispatch"] is None
