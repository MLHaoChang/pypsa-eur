"""DC grid-readiness walkthrough 04: the scorecard reads the project's live
network, so an edit shows in its verdict before the project is saved (it read
only the saved netCDF, and an SCR of 8.3 passed for a 60 MVA grid until Ctrl+S)."""

from __future__ import annotations

import pytest

BASE = "/api/campus-electrical"


@pytest.fixture
def hub(client, tmp_projects_dir, project_storage_dir):
    r = client.post(
        "/api/projects/from_template/eh_datacenter", params={"name": "live_hub"}
    )
    assert r.status_code == 200, r.text
    return r.json().get("name", "live_hub")


def _scr(client, name) -> dict:
    r = client.get(f"{BASE}/{name}/grid-readiness")
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["rows"] if x["id"] == "scr")


def test_an_unsaved_fault_level_edit_reaches_the_scr_row(client, hub):
    before = _scr(client, hub)
    bus = before["target"]["entity"]
    assert before["project_value"] > 3.0, before
    rows = client.get("/api/network/buses").json()
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    row = next(b for b in rows if b["name"] == bus)
    sk = float(row["eh_sk_mva"])
    r = client.put(
        f"/api/network/buses/{bus}", json={**row, "eh_sk_mva": sk * 60 / 250}
    )
    assert r.status_code == 200, r.text
    after = _scr(client, hub)
    assert after["project_value"] == pytest.approx(
        before["project_value"] * 60 / 250, rel=1e-3
    )
    assert after["verdict"] != "pass"


# ── GK A L1–L6 ──────────────────────────────────────────────────────────────


def _project(name):
    import db.session as S
    from db.models import Project

    with S.SessionLocal() as db:
        p = db.query(Project).filter(Project.name == name).one()
        db.expunge(p)
        return p


def _edit_fault(client, name, factor):
    bus = _scr(client, name)["target"]["entity"]
    rows = client.get("/api/network/buses").json()
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    row = next(b for b in rows if b["name"] == bus)
    r = client.put(
        f"/api/network/buses/{bus}",
        json={**row, "eh_sk_mva": float(row["eh_sk_mva"]) * factor},
    )
    assert r.status_code == 200, r.text


def _get(client, name):
    r = client.get(f"{BASE}/{name}/grid-readiness")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_busy_network_reads_the_saved_file_and_says_out_of_date(
    client, hub, monkeypatch
):
    import threading
    import time

    from services.adequacy import grid_readiness as G
    from services.pypsa_service import PyPSAService

    from services import project_registry

    saved = _scr(client, hub)["project_value"]
    _edit_fault(client, hub, 60 / 250)
    # The lock the edit routes take: the project's own context's (PyPSAService.get_lock in its request).
    ctx = PyPSAService.get_context(project_registry.registry_key(_project(hub)))
    assert ctx is not None
    monkeypatch.setattr(G, "LIVE_LOCK_TIMEOUT_S", 0.3)
    held, done = threading.Event(), threading.Event()

    def hold():
        with ctx.mutation_lock:
            held.set()
            done.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        start = time.monotonic()
        out = _get(client, hub)
        assert time.monotonic() - start < 5
    finally:
        done.set()
        t.join()
    assert out["network"] == "out_of_date"
    assert next(r for r in out["rows"] if r["id"] == "scr")["project_value"] == saved
    assert _get(client, hub)["network"] == "live"


def test_another_resident_project_is_never_read(client, hub):
    r = client.post(
        "/api/projects/from_template/eh_datacenter", params={"name": "live_other"}
    )
    assert r.status_code == 200, r.text
    other = r.json().get("name", "live_other")
    before = _scr(client, hub)["project_value"]
    _edit_fault(client, other, 60 / 250)  # the active project is the one just made
    assert _scr(client, other)["project_value"] == pytest.approx(
        before * 60 / 250, rel=1e-3
    )
    assert _scr(client, hub)["project_value"] == before


def test_a_same_named_project_in_another_org_is_never_read(
    client, other_org_client, hub
):
    """GK A L2: the live context is found by org and uuid, never by name. Two
    orgs each hold a resident project of the same name; an edit in one leaves
    the other's scorecard as it was."""
    r = other_org_client.post(
        "/api/projects/from_template/eh_datacenter", params={"name": hub}
    )
    assert r.status_code == 200, r.text
    twin = r.json().get("name", hub)
    assert twin == hub
    before = _scr(client, hub)["project_value"]
    assert _scr(other_org_client, twin)["project_value"] == pytest.approx(before)
    _edit_fault(other_org_client, twin, 60 / 250)
    assert _scr(other_org_client, twin)["project_value"] == pytest.approx(
        before * 60 / 250, rel=1e-3
    )
    assert _scr(client, hub)["project_value"] == before
    # And the other way round.
    _edit_fault(client, hub, 60 / 250)
    assert _scr(client, hub)["project_value"] == pytest.approx(
        before * 60 / 250, rel=1e-3
    )
    assert _scr(other_org_client, twin)["project_value"] == pytest.approx(
        before * 60 / 250, rel=1e-3
    )


def test_the_saved_source_is_todays_answer_plus_its_name(client, hub):
    import pypsa

    from services import campus_electrical_service as ce
    from services import project_registry
    from services.adequacy import grid_readiness as G

    p = _project(hub)
    _edit_fault(client, hub, 60 / 250)
    d = project_registry.project_dir(p)
    out = G.grid_readiness_for(p, source="saved")
    assert out.pop("network") == "saved"
    profile = ce.DEFAULTS["profile"]
    want = G.grid_readiness(
        d,
        pypsa.Network(str(d / ce.NETWORK_FILE)),
        profile,
        profile_dirs=(d / ce.SUBDIR / ce.GRID_CODES_SUBDIR,),
        eh_study=G._active_study(d)[0],
        study_edited=G._active_study(d)[1],
    )
    want["profile"]["since_run"] = ce.grid_code_since_run(p, profile)
    assert out == want


def test_the_report_reads_the_saved_project_while_the_screen_reads_live(client, hub):
    from routers import reports
    from routers.deps import AuthorizedProject
    from services import project_registry

    p = _project(hub)
    saved = _scr(client, hub)["project_value"]
    _edit_fault(client, hub, 60 / 250)
    assert _get(client, hub)["network"] == "live"
    assert _scr(client, hub)["project_value"] != saved
    ap = AuthorizedProject(
        name=p.name,
        directory=project_registry.project_dir(p),
        uuid=str(p.id),
        org_id=str(p.org_id),
        registry_key=project_registry.registry_key(p),
    )
    gr = reports._current_chapter_sources(ap)["grid_readiness"]
    assert gr["network"] == "saved"
    assert next(r for r in gr["rows"] if r["id"] == "scr")["project_value"] == saved


def test_a_project_not_loaded_reads_its_saved_file(client, hub, monkeypatch):
    from services.pypsa_service import PyPSAService

    saved = _scr(client, hub)["project_value"]
    _edit_fault(client, hub, 60 / 250)
    monkeypatch.setattr(PyPSAService, "get_context", classmethod(lambda cls, key: None))
    out = _get(client, hub)
    assert out["network"] == "saved"
    assert next(r for r in out["rows"] if r["id"] == "scr")["project_value"] == saved


def test_rows_reading_solved_sizes_say_when_an_edit_followed_the_solve(client, hub):
    """A solve stamps the edit counter on the network; a size edit after it makes
    the rows that rate a unit by its solved size say the solve is not current."""
    from services import dirty_state, project_registry
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_context(project_registry.registry_key(_project(hub)))
    assert ctx is not None
    ctx.network._solved_network_revision = dirty_state.revision(ctx)
    assert "after the solve" not in _scr(client, hub)["note"]
    _edit_fault(client, hub, 0.9)
    assert "edited after the solve" in _scr(client, hub)["note"]


def test_a_network_with_no_solve_stamp_makes_no_stale_claim(client, hub):
    _edit_fault(client, hub, 0.9)
    assert "after the solve" not in _scr(client, hub)["note"]
