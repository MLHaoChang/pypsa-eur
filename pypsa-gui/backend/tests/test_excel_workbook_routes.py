"""The Excel input workbook's app wiring (excel-input-workbook issue 02, GK B F1-F3 and G1-G9).

The template and a project's workbook download; the preview writes nothing; the
import creates a new project, all or nothing, at the workbook's own discount rate,
without touching the open network; /api/io/import/excel reads older layouts through
the same reader and refuses the input workbook (IMPORT-FLOW-1).
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import uuid
import zipfile

import pandas as pd
import pypsa
import pytest

from db.models import Project
from services import excel_workbook
from settings import get_settings
from tests.test_bulk_part_rederive import BATTERY, _set_rate
from tests.test_lc06_d22_undo_checkpoints import fingerprint
from tests.test_owned_cost_writers import _expected
from tests.test_viewer_role_gate import KIND as VIEW_ONLY
from tests.test_viewer_role_gate import as_user, baseline, helpers_state, world  # noqa: F401

MIME = excel_workbook.MIME


def _network(*, stale: bool = True) -> pypsa.Network:
    """One bus, a generator, a load and a battery whose investment parts own its costs."""
    from services.asset_schema.derive import apply_parts

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "B1", v_nom=110.0)
    n.add("Generator", "G1", bus="B1", p_nom=20.0, marginal_cost=10.0)
    n.add("Load", "L1", bus="B1", p_set=5.0)
    n.add("StorageUnit", "s", bus="B1", p_nom=10.0, max_hours=2.0)
    apply_parts(n, "StorageUnit", "s", BATTERY, discount_rate=0.07)
    if stale:
        n.storage_units.loc["s", ["capital_cost", "fom_cost", "lifetime"]] = [
            1.0,
            2.0,
            3.0,
        ]
    return n


def _workbook(n=None, *, rate: float | None = 0.08) -> bytes:
    solver = {"multi_investment_periods": False, "investment_periods": []}
    if rate is not None:
        solver["discount_rate"] = rate
    return excel_workbook._write(
        n or _network(), {"solver_config.json": solver}, template=False
    )


def _older_layout() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, rows in {"buses": [["name", "v_nom"], ["B9", 20.0]]}.items():
        ws = wb.create_sheet(sheet)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _preview(client, data: bytes, filename: str = "wb.xlsx"):
    return client.post("/api/workbook/preview", files={"file": (filename, data, MIME)})


def _import(client, data: bytes, name: str | None = None, sha: str | None = None):
    form = {"expected_sha256": sha or _sha(data)}
    if name is not None:
        form["name"] = name
    return client.post(
        "/api/workbook/import", files={"file": ("wb.xlsx", data, MIME)}, data=form
    )


def _kind(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("error_kind") if isinstance(detail, dict) else None


@pytest.fixture
def store(_auth_db):
    """The project rows and folders, to show a refused call left nothing behind."""
    _, session_local = _auth_db
    root = get_settings().projects_root

    def state():
        from pathlib import Path

        with session_local() as db:
            rows = sorted(str(p.id) for p in db.query(Project).all())
        dirs = (
            sorted(str(p) for p in Path(root).rglob("*") if p.is_dir())
            if Path(root).exists()
            else []
        )
        return rows, dirs

    return state


def _saved(session_local, project_id: str):
    from services import project_registry

    with session_local() as db:
        return project_registry.project_dir(db.get(Project, uuid.UUID(project_id)))


def _saved_network(directory) -> pypsa.Network:
    n = pypsa.Network()
    n.import_from_netcdf(str(directory / "network.nc"))
    return n


# ── template (F2b) ───────────────────────────────────────────────────────────


def _members(data: bytes) -> dict:
    """Every part of the .xlsx but its save time (docProps/core.xml), which openpyxl stamps."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {
            i.filename: zf.read(i)
            for i in zf.infolist()
            if i.filename != "docProps/core.xml"
        }


def test_the_template_is_the_same_whichever_project_is_open(client, install_network):
    install_network(_network())
    first = client.get("/api/workbook/template")
    other = pypsa.Network()
    other.add("Bus", ["X1", "X2", "X3"])
    install_network(other)
    second = client.get("/api/workbook/template")
    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"] == MIME
    assert _members(first.content) == _members(second.content)


def test_not_carried_is_the_about_sheet_s_list(client):
    import openpyxl

    wb = openpyxl.load_workbook(
        io.BytesIO(client.get("/api/workbook/template").content)
    )
    col = [c.value for c in wb["About"]["A"]]
    start = col.index("Not in this workbook") + 1
    about = [v[2:] for v in col[start:] if isinstance(v, str) and v.startswith("• ")]
    r = _preview(client, _workbook())
    assert r.status_code == 200, r.text
    assert r.json()["not_carried"] == about
    assert any(
        "last saved" in item for item in about
    )  # G8: unsaved edits are not in it


def test_the_template_shows_the_form_s_input_fields_and_no_derived_one(client):
    import openpyxl

    from services.asset_schema.fields import fields_for

    ws = openpyxl.load_workbook(
        io.BytesIO(client.get("/api/workbook/template").content)
    )["Generators"]
    keys = [c.value for c in ws[3]]
    labels = dict(zip(keys, [c.value for c in ws[1]], strict=True))
    form = {f.key: f for f in fields_for("Generator")}
    assert keys[0] == "name" and keys.count("name") == 1
    assert labels["p_nom"] == form["p_nom"].label
    assert form["capital_cost"].derived and "capital_cost" not in keys


# ── preview ──────────────────────────────────────────────────────────────────


def test_a_preview_writes_nothing_and_leaves_the_open_network(
    client, install_network, session_ctx, store
):
    install_network(_network(stale=False))
    before, fp = store(), fingerprint(session_ctx(client).network)
    data = _workbook()
    r = _preview(client, data, "My grid.xlsx")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sha256"] == _sha(data)
    assert body["summary"]["buses"] == 1 and body["summary"]["storage_units"] == 1
    assert body["report"]["source_files"] == ["My grid.xlsx"]
    assert store() == before
    assert fingerprint(session_ctx(client).network) == fp


# ── G1: the workbook's rate, never the open project's ───────────────────────


@pytest.mark.parametrize("rate", [0.08, None], ids=["workbook-8pc", "no-rate-default"])
def test_priced_rows_derive_at_the_workbook_s_rate_not_the_open_project_s(
    client, install_network, session_ctx, _auth_db, rate
):
    from services.solver_service import SolverConfig

    install_network(_network(stale=False))
    _set_rate(0.03)
    want = rate if rate is not None else SolverConfig().discount_rate
    assert want not in (0.03,) and _expected(BATTERY, want) != _expected(BATTERY, 0.03)
    data = _workbook(rate=rate)
    preview = _preview(client, data)
    assert preview.status_code == 200, preview.text
    assert preview.json()["rederived"] == ["Storage 's'"]
    r = _import(client, data, name=f"Rate {rate}")
    assert r.status_code == 200, r.text
    assert r.json()["rederived"] == ["Storage 's'"]
    su = _saved_network(_saved(_auth_db[1], r.json()["project_id"])).storage_units
    for col, v in _expected(BATTERY, want).items():
        assert su.at["s", col] == pytest.approx(v, rel=1e-12), col
    saved_cfg = json.loads(
        (_saved(_auth_db[1], r.json()["project_id"]) / "solver_config.json").read_text()
    )
    assert saved_cfg["discount_rate"] == want


def test_a_workbook_rate_the_rate_rule_refuses_creates_nothing(client, store):
    before = store()
    r = _import(client, _workbook(rate=-1.0), name="Bad rate")
    assert r.status_code == 422 and _kind(r) == "import_unreadable", r.text
    assert "discount rate" in r.json()["detail"]["message"]
    assert store() == before


def test_an_older_layout_imports_as_a_new_project_named_after_its_file(
    client, _auth_db
):
    r = client.post(
        "/api/workbook/import",
        files={"file": ("C:/Users/me/Grid A.xlsx", _older_layout(), MIME)},
        data={"expected_sha256": _sha(_older_layout())},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Grid A"
    assert r.json()["report"]["source_files"] == ["Grid A.xlsx"]
    folder = _saved(_auth_db[1], r.json()["project_id"])
    cfg = json.loads((folder / "solver_config.json").read_text())
    from services.solver_service import SolverConfig

    assert cfg["discount_rate"] == SolverConfig().discount_rate
    assert not (folder / "user_ts.json").exists()


def test_a_file_name_with_no_stem_suggests_a_plain_name(client):
    r = _preview(client, _workbook(), " .xlsx")
    assert r.status_code == 200 and r.json()["suggested_name"] == "Imported workbook"


def test_an_unreadable_file_s_message_names_the_uploaded_file(client):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "notes"
    buf = io.BytesIO()
    wb.save(buf)
    r = _preview(client, buf.getvalue(), "Notes 2030.xlsx")
    assert r.status_code == 422 and _kind(r) == "import_unreadable", r.text
    assert r.json()["detail"]["message"].startswith(
        "Notes 2030.xlsx has none of the sheets"
    )


def test_a_build_without_gridspine_answers_503(client, monkeypatch):
    import sys

    import gridspine.ingest

    monkeypatch.setitem(sys.modules, "gridspine.ingest.excel_export", None)
    monkeypatch.delattr(gridspine.ingest, "excel_export")
    r = client.get("/api/workbook/template")
    assert r.status_code == 503 and "not available in this build" in r.text


# ── G2: the open network's gates do not apply, because it is never touched ──


def test_an_import_while_a_solve_and_a_harness_run_hold_the_open_project(
    client, install_network, session_ctx, monkeypatch
):
    from routers import simulation
    from services import harness_locks

    n = install_network(_network(stale=False))
    fp = fingerprint(n)
    monkeypatch.setattr(simulation, "_solver_in_flight", lambda: True)
    monkeypatch.setattr(harness_locks, "refuses_active", lambda: True)
    held = client.post(
        "/api/io/import/excel", files={"file": ("x.xlsx", _older_layout(), MIME)}
    )
    assert held.status_code == 409, (
        held.text
    )  # the rerouted /api/io route keeps every gate
    data = _workbook()
    assert _preview(client, data).status_code == 200
    r = _import(client, data, name="During a run")
    assert r.status_code == 200, r.text
    after = session_ctx(client).network
    assert after is n and fingerprint(after) == fp


# ── G3: the upload cap ──────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ["preview", "import"])
def test_one_byte_over_the_upload_cap_is_413_and_creates_nothing(
    client, store, monkeypatch, route
):
    from services import upload_guard

    data = _workbook()
    monkeypatch.setattr(upload_guard.read_capped, "__defaults__", (len(data) - 1,))
    before = store()
    r = (
        _preview(client, data)
        if route == "preview"
        else _import(client, data, name="Too big")
    )
    assert r.status_code == 413, r.text
    assert _kind(r) is None  # the upload cap, not the workbook's expansion limit
    assert store() == before
    monkeypatch.setattr(upload_guard.read_capped, "__defaults__", (len(data),))
    assert (
        _preview(client, data)
        if route == "preview"
        else _import(client, data, name="Fits")
    ).status_code == 200


def test_a_workbook_past_a_read_limit_is_import_expansion_too_large(
    client, store, monkeypatch
):
    from gridspine.ingest import excel_layout as L

    monkeypatch.setitem(L.LIMITS, "workbook_cells", 5)
    before = store()
    for r in (
        _preview(client, _workbook()),
        _import(client, _workbook(), name="Expands"),
    ):
        assert r.status_code == 413 and _kind(r) == "import_expansion_too_large", r.text
    assert store() == before


# ── G4: who may create ──────────────────────────────────────────────────────


def test_a_viewer_of_the_open_project_creates_their_own_but_cannot_replace_it(
    world,  # noqa: F811
    as_user,  # noqa: F811
    store,
):
    viewer = as_user(world["viewer"])
    assert viewer.post(f"/api/projects/{world['project']}/activate").status_code == 200
    from tests.test_viewer_role_gate import _state

    before = _state(world)
    refused = viewer.post(
        "/api/io/import/excel", files={"file": ("x.xlsx", _older_layout(), MIME)}
    )
    assert refused.status_code == 403 and _kind(refused) == VIEW_ONLY, refused.text
    assert _state(world) == before
    r = _import(viewer, _workbook(), name="Viewer's own")
    assert r.status_code == 200, r.text
    with world["session_local"]() as db:
        row = db.get(Project, uuid.UUID(r.json()["project_id"]))
        assert row.created_by == world["viewer"]
    assert _state(world) == before


def test_an_import_is_in_the_change_log_of_the_new_project_s_org(client):
    r = _import(client, _workbook(), name="Logged")
    assert r.status_code == 200, r.text
    log = client.get("/api/changelog/").json()
    entries = log if isinstance(log, list) else log.get("entries", [])
    assert any(
        "Imported Excel workbook 'wb.xlsx' as 'Logged'" in e["description"]
        for e in entries
    )


# ── G5: all or nothing ──────────────────────────────────────────────────────


def test_a_failed_sidecar_write_removes_the_row_and_the_folder(
    client, store, monkeypatch
):
    from services import atomic_io

    def fail(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_io, "atomic_write_text", fail)
    before = store()
    r = _import(client, _workbook(), name="Half written")
    assert r.status_code == 500 and _kind(r) == "import_write_failed", r.text
    assert store() == before


def test_a_refusal_after_the_row_exists_keeps_its_status_and_leaves_nothing(
    client, store, monkeypatch
):
    from fastapi import HTTPException

    def refuse(*a, **k):
        raise HTTPException(422, {"error_kind": "invalid_edit_value", "message": "no"})

    monkeypatch.setattr(excel_workbook, "_write_files", refuse)
    before = store()
    r = _import(client, _workbook(), name="Refused late")
    assert r.status_code == 422 and _kind(r) == "invalid_edit_value", r.text
    assert store() == before


def test_a_taken_name_overwrites_nothing_and_leaves_nothing(
    client, install_network, store, _auth_db
):
    from tests.test_viewer_role_gate import _disk

    first = _import(client, _workbook(), name="Taken")
    assert first.status_code == 200, first.text
    folder = _saved(_auth_db[1], first.json()["project_id"])
    files, before = _disk(folder), store()
    again = _import(client, _workbook(_network(stale=False)), name="Taken")
    assert again.status_code == 409, again.text
    assert store() == before and _disk(folder) == files


# ── G6: the previewed bytes, checked again ──────────────────────────────────


def test_a_file_that_changed_since_the_preview_is_409_import_file_changed(
    client, store
):
    data = _workbook()
    sha = _preview(client, data).json()["sha256"]
    before = store()
    r = _import(client, _workbook(rate=0.05), name="Changed", sha=sha)
    assert r.status_code == 409 and _kind(r) == "import_file_changed", r.text
    assert store() == before


def test_an_import_runs_every_check_again(client, store):
    bad = b"not a workbook at all"
    before = store()
    for r in (_preview(client, bad), _import(client, bad, name="Garbage")):
        assert r.status_code == 422 and _kind(r) == "import_unreadable", r.text
    empty = client.get("/api/workbook/template").content  # no nodes
    r = _import(client, empty, name="Empty")
    assert r.status_code == 422 and _kind(r) == "import_unreadable", r.text
    assert store() == before


def test_a_reader_crash_is_logged_and_refused_as_unreadable(
    client, store, monkeypatch, caplog
):
    from gridspine.ingest import excel_import

    def crash(*a, **k):
        raise KeyError("a reader bug")

    monkeypatch.setattr(excel_import, "read_excel", crash)
    before = store()
    with caplog.at_level("WARNING", logger="services.excel_workbook"):
        r = _preview(client, _workbook())
    assert r.status_code == 422 and _kind(r) == "import_unreadable", r.text
    assert [
        rec
        for rec in caplog.records
        if rec.name == "services.excel_workbook" and rec.exc_info
    ]
    assert store() == before


def test_every_excel_upload_goes_through_prepare(client, monkeypatch):
    seen = []

    def spy(data, filename, *, new_project):
        seen.append(new_project)
        raise RuntimeError("prepare")

    monkeypatch.setattr(excel_workbook, "prepare", spy)
    data = _workbook()
    for call in (
        lambda: client.post(
            "/api/io/import/excel", files={"file": ("x.xlsx", data, MIME)}
        ),
        lambda: _preview(client, data),
        lambda: _import(client, data, name="Spied"),
    ):
        with pytest.raises(RuntimeError, match="prepare"):
            call()
    assert seen == [False, True, True]


# ── G7: /api/io/import/excel refuses the input workbook (IMPORT-FLOW-1) ──────


def test_the_io_route_refuses_the_input_workbook_and_keeps_the_open_network(
    client, install_network, session_ctx
):
    n = install_network(_network(stale=False))
    fp = fingerprint(n)
    r = client.post(
        "/api/io/import/excel", files={"file": ("x.xlsx", _workbook(), MIME)}
    )
    assert r.status_code == 422 and _kind(r) == "import_as_new_project", r.text
    assert session_ctx(client).network is n and fingerprint(n) == fp


def test_the_chat_import_excel_tool_gets_the_same_refusal(client, install_network):
    import base64

    from fastapi import HTTPException

    from services import chat_tools

    install_network(_network(stale=False))
    with pytest.raises(HTTPException) as exc:
        chat_tools.import_excel(base64.b64encode(_workbook()).decode())
    assert exc.value.detail["error_kind"] == "import_as_new_project"


def test_the_io_route_returns_the_reader_s_report(client):
    r = client.post(
        "/api/io/import/excel", files={"file": ("x.xlsx", _older_layout(), MIME)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["report"]["source_files"] == ["x.xlsx"] and r.json()["buses"] == 1


# ── G8: a project's workbook ────────────────────────────────────────────────


def test_a_project_s_workbook_round_trips_as_saved(
    client, install_network, session_ctx, _auth_db
):
    from services.workflow_tools import _network_input_fingerprint

    install_network(_network(stale=False))
    ctx = session_ctx(client)
    ctx.user_ts[("loads", "p_set", "L1")] = pd.Series(
        [4.0, 6.0], index=ctx.network.snapshots
    )
    assert client.post("/api/projects/WB Source").status_code == 200
    from services import project_registry

    with _auth_db[1]() as db:
        src = db.query(Project).filter_by(name="WB Source").one()
        src_id, src_dir = str(src.id), project_registry.project_dir(src)

    def inputs(ctx):
        """The input fingerprint without the in-memory upload store, whose saved form user_ts.json is compared."""
        bare = copy.copy(ctx)
        bare.user_ts = {}
        return _network_input_fingerprint(bare, exact=True)

    want = inputs(session_ctx(client))
    ctx.network.add("Bus", "Unsaved")  # an edit after the save is not in the workbook
    got = client.get(f"/api/projects/{src_id}/workbook")
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == MIME
    assert 'filename="WB Source.xlsx"' in got.headers["content-disposition"]
    data = got.content
    preview = _preview(
        client, data, "WB Source.xlsx"
    ).json()  # the name the download gave it
    assert preview["summary"]["buses"] == 1 and preview["suggested_name"] == "WB Source"
    assert preview["report"]["unmapped"] == []
    made = _import(client, data, name="WB Copy", sha=preview["sha256"])
    assert made.status_code == 200, made.text
    copy_dir = _saved(_auth_db[1], made.json()["project_id"])
    assert json.loads((copy_dir / "user_ts.json").read_text()) == json.loads(
        (src_dir / "user_ts.json").read_text()
    )
    ctx.network.remove("Bus", "Unsaved")
    assert (
        client.post(f"/api/projects/{made.json()['project_id']}/activate").status_code
        == 200
    )
    copied = session_ctx(client)
    assert copied.loaded_project == "WB Copy" and set(copied.user_ts) == {
        ("loads", "p_set", "L1")
    }
    assert inputs(copied) == want


@pytest.mark.parametrize("broken", ["solver_config.json", "user_ts.json", None])
def test_a_project_s_saved_sidecars_are_read_or_refused_by_name(
    client,
    baseline,  # noqa: F811
    broken,
):
    _, project_id, path = baseline
    (path / "user_ts.json").unlink(missing_ok=True)
    if broken is None:
        (path / "solver_config.json").unlink()  # never saved: the defaults
    else:
        (path / broken).write_text("{not json")
    r = client.get(f"/api/projects/{project_id}/workbook")
    if broken is None:
        assert r.status_code == 200, r.text
        assert _preview(client, r.content).status_code == 200
    else:
        words = {
            "solver_config.json": "saved solver settings cannot be read",
            "user_ts.json": "saved time series (user_ts.json) cannot be read",
        }[broken]
        assert r.status_code == 422 and words in r.json()["detail"], r.text


def test_a_cross_org_workbook_is_the_same_404_as_no_project(
    client,
    other_org_client,
    baseline,  # noqa: F811
):
    _, project_id, _ = baseline
    assert client.get(f"/api/projects/{project_id}/workbook").status_code == 200
    real = other_org_client.get(f"/api/projects/{project_id}/workbook")
    fake = other_org_client.get(f"/api/projects/{uuid.uuid4()}/workbook")
    assert real.status_code == fake.status_code == 404
    assert real.content == fake.content == b'{"detail":"Project not found"}'
    assert dict(real.headers) == dict(fake.headers)
