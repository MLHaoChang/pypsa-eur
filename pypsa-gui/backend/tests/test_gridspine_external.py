"""The client's own dispatch through the action layer (increment 7, A2).

The upload is validated AS IT ARRIVES, which is the posture `upload_readback`
takes and the reason this is a service function rather than a path the caller
names. Two things follow from that and are what this file holds:

  * a client learns their file is wrong immediately, with the producer's own
    message, rather than after queueing a study that dies at its dispatch
    stage minutes later;
  * the caller never names a path. `from_dispatch` had to be retro-fitted with
    `_authorized_dispatch_dir` because it IS a path (CodeQL found it), and
    `from_project` was built as a project name for the same reason. An upload
    has no such hole to close: the bytes arrive, the server chooses where they
    land, and the config records the server's path.

The grid is stubbed. Loading case39 and solving nothing still costs seconds per
test, and what is under test here is the seam — sanitisation, storage, the
config write, the refusal mapping — not the producer, which has its own 32
tests in `tests/gridspine/test_external_source.py`.
"""
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from db.models import Project, User
from services import gridspine_service as gs

CONFIG = {"hours": 2, "k": 1, "window": 24, "overlap": 0, "screen": False}

DISPATCH_CSV = (
    b"unit_id,hour,p_mw,q_mvar,status\n"
    b"G1,0,100.0,10.0,1\nG1,1,101.0,10.0,1\n"
    b"G2,0,100.0,10.0,1\nG2,1,101.0,10.0,1\n"
)
#: Demand sits on L0/L1, the GENERATORS on B0/B1 — disjoint on purpose, the
#: way `tests/gridspine/test_external_source.py` keeps them, because the loads
#: table is checked against the grid's load buses and not against the
#: registry's.
LOADS_CSV = (
    b"bus,hour,p_mw,q_mvar\n"
    b"L0,0,80.0,5.0\nL0,1,81.0,5.0\nL1,0,80.0,5.0\nL1,1,81.0,5.0\n"
)
DISPATCH_UNKNOWN_UNIT = DISPATCH_CSV + b"G99,0,50.0,5.0,1\nG99,1,50.0,5.0,1\n"
LOADS_UNKNOWN_BUS = LOADS_CSV + b"L_TYPO,0,5.0,1.0\nL_TYPO,1,5.0,1.0\n"


@pytest.fixture
def user_and_db(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(User, seeded_identity["user_id"])


@pytest.fixture
def study(user_and_db):
    db, user = user_and_db
    return db.get(
        Project,
        uuid.UUID(gs.create_study(db, user, "External Study", config=CONFIG)["id"]),
    )


@pytest.fixture(autouse=True)
def stub_grid(monkeypatch):
    """case39's registry and demand buses, without loading case39.

    The net is a stand-in rather than `object()` because the driver reads the
    grid's LOAD buses off it (`_demand_buses`) to hand to the producer — two
    buses carrying load, named through `net.bus["name"]` exactly as the real
    thing does.
    """
    from types import SimpleNamespace

    import pandas as pd

    import gridspine.drivers.year_study as ys

    registry = pd.DataFrame(
        {"bus": ["B0", "B1"]}, index=pd.Index(["G1", "G2"], name="unit_id")
    )
    net = SimpleNamespace(
        bus=pd.DataFrame({"name": ["L0", "L1"]}),
        load=pd.DataFrame({"bus": [0, 1]}),
    )
    monkeypatch.setattr(ys, "load_case39_res", lambda: net)
    monkeypatch.setattr(ys, "registry_from_net", lambda _net: registry)


def test_an_upload_is_kept_validated_and_becomes_the_study_source(study):
    summary = gs.upload_external_dispatch(
        study, DISPATCH_CSV, "market_dispatch.csv", LOADS_CSV, "market_loads.csv",
    )
    assert summary["hours"] == 2 and summary["units"] == 2

    # Kept under the study, byte for byte, under the client's own names.
    kept = gs.gridspine_dir(study) / "uploads" / "external"
    assert (kept / "market_dispatch.csv").read_bytes() == DISPATCH_CSV
    assert (kept / "market_loads.csv").read_bytes() == LOADS_CSV

    # And it IS the source now: the config points at the server's paths, never
    # at anything the caller said.
    cfg = gs.read_config(study).to_json()
    assert cfg["from_external"] == str(kept / "market_dispatch.csv")
    assert cfg["from_external_loads"] == str(kept / "market_loads.csv")
    assert cfg["from_dispatch"] is None and cfg["from_network"] is None


def test_a_path_shaped_filename_cannot_escape_the_uploads_directory(study):
    """The increment-6 lesson: the `.csv` fallback masks this unless the name is
    path-shaped AND ends in a permitted suffix."""
    summary = gs.upload_external_dispatch(
        study, DISPATCH_CSV, "../../../../etc/evil.csv", LOADS_CSV, "loads.csv",
    )
    kept = gs.gridspine_dir(study) / "uploads" / "external"
    stored = gs.read_config(study).to_json()["from_external"]
    assert str(kept) in stored
    assert "etc" not in stored.replace(str(kept), "")
    assert summary["hours"] == 2


def test_two_uploads_that_would_collide_keep_both_files(study):
    """One name for both tables would otherwise overwrite the dispatch with the
    loads and then validate the loads against itself."""
    gs.upload_external_dispatch(study, DISPATCH_CSV, "tables.csv", LOADS_CSV, "tables.csv")
    cfg = gs.read_config(study).to_json()
    assert cfg["from_external"] != cfg["from_external_loads"]
    kept = gs.gridspine_dir(study) / "uploads" / "external"
    assert (kept / "tables.csv").read_bytes() == DISPATCH_CSV


def test_an_unnamed_upload_gets_a_usable_fallback(study):
    gs.upload_external_dispatch(study, DISPATCH_CSV, None, LOADS_CSV, None)
    cfg = gs.read_config(study).to_json()
    assert cfg["from_external"].endswith(".csv")
    assert cfg["from_external_loads"].endswith(".csv")


def test_an_excel_upload_keeps_its_suffix(study):
    """The producer dispatches on the suffix, so a stored `.xlsx` that became
    `.csv` would be read with the wrong reader. The refusal message is the
    evidence: it names the file the reader was actually pointed at."""
    with pytest.raises(HTTPException) as exc:
        # Not a real workbook, so the producer refuses it — but the point is the
        # name the reader saw, which the refusal message carries.
        gs.upload_external_dispatch(study, b"PK\x03\x04 not really", "book.xlsx")
    assert "book.xlsx" in str(exc.value.detail)


def test_a_refused_upload_leaves_nothing_behind(study):
    """Validation happens on a staged copy, so a 422 costs the server no disk.

    Storing first and validating second meant an unlimited number of refused
    512 MB pairs could be parked under distinct sanitised names by a caller who
    never once got a 2xx.
    """
    with pytest.raises(HTTPException):
        gs.upload_external_dispatch(
            study, DISPATCH_UNKNOWN_UNIT, "dispatch.csv", LOADS_CSV, "loads.csv",
        )
    kept = gs.gridspine_dir(study) / "uploads" / "external"
    assert [p.name for p in kept.rglob("*") if p.is_file()] == []


def test_a_refused_upload_does_not_overwrite_the_accepted_one(study):
    """The defect this closes: the bytes were written to their final path BEFORE
    validation, so a second upload reusing the first one's filename replaced the
    file the config already pointed at — and then 422'd. The study was left
    queueable on bytes the server had just refused, which is precisely what
    `test_a_refused_file_does_not_become_the_study_source` exists to prevent."""
    gs.upload_external_dispatch(study, DISPATCH_CSV, "dispatch.csv", LOADS_CSV, "loads.csv")
    source = Path(gs.read_config(study).to_json()["from_external"])
    assert source.read_bytes() == DISPATCH_CSV

    with pytest.raises(HTTPException):
        gs.upload_external_dispatch(
            study, DISPATCH_UNKNOWN_UNIT, "dispatch.csv", LOADS_CSV, "loads.csv",
        )
    # Same path, still the accepted bytes, still the study's source.
    assert Path(gs.read_config(study).to_json()["from_external"]) == source
    assert source.read_bytes() == DISPATCH_CSV


def test_a_dispatch_named_loads_csv_is_not_overwritten_by_the_demand(study):
    """The collision guard's own case, which it did not cover: its fallback was
    the fixed name `loads.csv`, so when the DISPATCH had sanitised to that name
    the reassignment landed on the same file again, the demand bytes replaced the
    dispatch, and the demand table was validated against itself."""
    summary = gs.upload_external_dispatch(
        study, DISPATCH_CSV, "loads.csv", LOADS_CSV, "loads.csv",
    )
    assert summary["hours"] == 2 and summary["units"] == 2
    cfg = gs.read_config(study).to_json()
    dispatch, loads = Path(cfg["from_external"]), Path(cfg["from_external_loads"])
    assert dispatch != loads
    assert dispatch.read_bytes() == DISPATCH_CSV
    assert loads.read_bytes() == LOADS_CSV


def test_a_name_too_long_for_the_filesystem_is_not_a_500(study):
    """`_safe_table_name` never truncated, so a 5000-character name reached
    `write_bytes` and came back as an uncaught OSError — a 500 with a traceback
    where the action layer owes a 4xx or a usable file."""
    summary = gs.upload_external_dispatch(
        study, DISPATCH_CSV, "d" * 5000 + ".csv", LOADS_CSV, "l" * 5000 + ".csv",
    )
    assert summary["hours"] == 2
    for field in ("from_external", "from_external_loads"):
        stored = Path(gs.read_config(study).to_json()[field])
        assert len(stored.name) <= 128 and stored.is_file()
        assert stored.suffix == ".csv"


def test_the_source_cannot_be_swapped_while_a_study_is_queued(study, monkeypatch):
    """`update_config` refuses an edit while a job is queued or running because
    the queue snapshotted the DIRECTORY, not the config — and the dispatch source
    is the most consequential config field there is. Both source routes carried
    the same hazard and neither checked: the GUI disables the picker, so the path
    that reaches this is the copilot's `gridspine_set_dispatch_source`, or any
    direct call."""
    monkeypatch.setattr(gs, "_active_job_for", lambda _project: {"status": "queued"})
    with pytest.raises(HTTPException) as exc:
        gs.upload_external_dispatch(study, DISPATCH_CSV, "d.csv", LOADS_CSV, "l.csv")
    assert exc.value.status_code == 409
    assert gs.read_config(study).to_json()["from_external"] is None


def test_a_refused_file_is_a_422_carrying_the_producers_reason(study):
    with pytest.raises(HTTPException) as exc:
        gs.upload_external_dispatch(
            study, DISPATCH_UNKNOWN_UNIT, "dispatch.csv", LOADS_CSV, "loads.csv",
        )
    assert exc.value.status_code == 422
    assert "G99" in str(exc.value.detail)


def test_a_refused_file_does_not_become_the_study_source(study):
    """The config must not point at a file the producer just rejected: the study
    would queue, run, and fail at its dispatch stage for a reason the caller was
    already told at upload."""
    with pytest.raises(HTTPException):
        gs.upload_external_dispatch(
            study, DISPATCH_UNKNOWN_UNIT, "dispatch.csv", LOADS_CSV, "loads.csv",
        )
    assert gs.read_config(study).to_json()["from_external"] is None


def test_dispatch_without_demand_is_refused_with_the_reason(study):
    with pytest.raises(HTTPException) as exc:
        gs.upload_external_dispatch(study, DISPATCH_CSV, "dispatch.csv")
    assert exc.value.status_code == 422
    detail = str(exc.value.detail).lower()
    assert "loads" in detail or "demand" in detail


def test_setting_the_external_source_clears_the_other_two(study, user_and_db):
    db, user = user_and_db
    gs.upload_external_dispatch(study, DISPATCH_CSV, "d.csv", LOADS_CSV, "l.csv")
    gs.set_dispatch_source(db, study, "generate", user=user)
    cfg = gs.read_config(study).to_json()
    assert cfg["from_external"] is None and cfg["from_external_loads"] is None


def test_a_capacity_project_cannot_upload_an_external_dispatch(user_and_db):
    """Every gridspine action refuses a capacity-expansion project, and this one
    must not leave an `uploads/external/` directory behind in it on the way."""
    db, user = user_and_db
    from services import project_registry

    plain = project_registry.create_root(db, user, "Plain External")
    with pytest.raises(HTTPException) as exc:
        gs.upload_external_dispatch(plain, DISPATCH_CSV, "d.csv", LOADS_CSV, "l.csv")
    assert exc.value.status_code == 409
    assert not (project_registry.project_dir(plain) / "gridspine").exists()


def test_the_config_reports_the_clients_filenames_not_just_server_paths(study):
    """`from_network` exposes `from_project` — the NAME — so the UI and copilot
    can say which project rather than which path. Same idea here: what the
    engineer recognises is the file they uploaded, not where the server put it."""
    gs.upload_external_dispatch(
        study, DISPATCH_CSV, "jan_dispatch.csv", LOADS_CSV, "jan_loads.csv",
    )
    cfg = gs.get_config(study)
    assert cfg["from_external_name"] == "jan_dispatch.csv"
    assert cfg["from_external_loads_name"] == "jan_loads.csv"


def test_the_names_are_absent_when_there_is_no_external_source(study):
    cfg = gs.get_config(study)
    assert cfg["from_external_name"] is None
    assert cfg["from_external_loads_name"] is None


def test_the_external_source_cannot_be_patched_through_update_config(study, user_and_db):
    """The whole point of making this an upload is that the caller never names a
    path. `update_config` accepting `from_external` would reopen exactly the hole
    `_authorized_dispatch_dir` had to be written to close for `from_dispatch`."""
    db, user = user_and_db
    with pytest.raises(HTTPException) as exc:
        gs.update_config(study, {"from_external": "/etc/passwd"}, db=db, user=user)
    assert exc.value.status_code == 422
    assert gs.read_config(study).to_json()["from_external"] is None


def test_a_demand_bus_the_grid_does_not_have_is_refused_at_upload(study):
    """The reason this is a 422 here and not a stage error later: the client is
    standing at the upload. A bus name the grid lacks passes every column and
    dtype check, so without the producer's demand-bus check the study would
    queue, rank the year on an inflated total demand, and die at its ranking
    stage with a message about the DC artifact."""
    with pytest.raises(HTTPException) as exc:
        gs.upload_external_dispatch(
            study, DISPATCH_CSV, "dispatch.csv", LOADS_UNKNOWN_BUS, "loads.csv",
        )
    assert exc.value.status_code == 422
    assert "L_TYPO" in str(exc.value.detail)
    assert gs.read_config(study).to_json()["from_external"] is None


def test_the_other_source_route_is_locked_while_a_study_is_queued(study, user_and_db, monkeypatch):
    """The same lock on `set_dispatch_source`: three of the four sources go
    through it, so fixing only the upload would leave the hole open for the
    other three."""
    db, user = user_and_db
    monkeypatch.setattr(gs, "_active_job_for", lambda _project: {"status": "running"})
    with pytest.raises(HTTPException) as exc:
        gs.set_dispatch_source(db, study, "generate", user=user)
    assert exc.value.status_code == 409
