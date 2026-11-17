"""GC-09: the compliance workflow through `/api/grid-compliance`.

The tracker's dependency guard both ways, the data freeze and its staleness,
the TSO revision log round-trip, and the report pack's manifest and
confidentiality (GC-TEXT-1), all through the API on a real project.
"""

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import select

from db.models import Project, User
from services import campus_electrical_service as ce
from services import project_registry
from services.grid_compliance import catalogue as cat
from services.grid_compliance import pack as gc_pack
from services.grid_compliance import sources

NAME = "Compliance Hub"
BASE = f"/api/grid-compliance/{NAME}"


@pytest.fixture
def hub(client, _auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        project_registry.create_root(db, user, NAME)
        project = db.execute(select(Project).where(Project.name == NAME)).scalar_one()
        db.expunge(project)
    return project


def _campus(project, text="campus: {name: x}\n"):
    (ce.campus_dir(project) / ce.CAMPUS_FILE).write_text(text)


def _set(client, code, status):
    return client.put(f"{BASE}/deliverables/{code}/status", json={"status": status})


def _row(client, code):
    resp = client.get(BASE)
    assert resp.status_code == 200, resp.text
    return next(r for r in resp.json()["deliverables"] if r["code"] == code)


# ── catalogue ─────────────────────────────────────────────────────────────


def test_the_catalogue_is_the_25_trgc_deliverables_with_known_prerequisites():
    assert [d.code for d in cat.DELIVERABLES] == [f"{i:02d}" for i in range(1, 26)]
    assert set(cat.BY_CODE["06"].depends_on) == {"04", "05"}
    assert set(cat.BY_CODE["07"].depends_on) == {"04", "05"}
    assert set(cat.BY_CODE["08"].depends_on) == {"06", "07"}
    for d in cat.DELIVERABLES:
        assert set(d.depends_on) <= set(cat.BY_CODE) and d.code not in d.depends_on
        assert d.phase in cat.PHASES


def test_the_state_lists_every_deliverable_with_its_next_step_and_blockers(client, hub):
    body = client.get(BASE).json()
    assert body["statuses"] == list(cat.STATUSES)
    assert len(body["deliverables"]) == 25
    row = next(r for r in body["deliverables"] if r["code"] == "08")
    assert row["status"] == "not_started" and row["next_step"] == "Collect the data list"
    assert row["blocked_by"] == ["06", "07"]
    assert row["feeds"] == [{"package": "GC-07", "available": False}]


# ── status guard, both ways ───────────────────────────────────────────────


def test_a_deliverable_is_not_submitted_while_a_prerequisite_is_not_judged(client, hub):
    _set(client, "06", "judged")
    _set(client, "07", "results_in")
    resp = _set(client, "08", "submitted")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_kind"] == "gc_prerequisite_not_judged"
    assert resp.json()["detail"]["blockers"] == ["07"]
    assert _row(client, "08")["status"] == "not_started"
    # every status at or past submitted is guarded, not only "submitted"
    for later in ("tso_comments", "accepted"):
        assert _set(client, "08", later).status_code == 409


def test_a_deliverable_is_submitted_once_every_prerequisite_is_judged(client, hub):
    _set(client, "04", "judged")
    _set(client, "05", "judged")
    _set(client, "06", "judged")
    assert _set(client, "07", "accepted").status_code == 200
    resp = _set(client, "08", "submitted")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "submitted" and resp.json()["blocked_by"] == []
    # work short of submission never waits on prerequisites
    assert _set(client, "25", "results_in").status_code == 200


def test_a_prerequisite_does_not_drop_below_judged_under_a_submitted_dependent(client, hub):
    _set(client, "04", "judged")
    _set(client, "05", "judged")
    _set(client, "06", "judged")
    _set(client, "07", "judged")
    _set(client, "08", "submitted")
    resp = _set(client, "07", "results_in")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_kind"] == "gc_dependent_submitted"
    assert resp.json()["detail"]["blockers"] == ["08"]
    # moving it forward is fine
    assert _set(client, "07", "accepted").status_code == 200


def test_unknown_deliverable_and_status_are_refused(client, hub):
    assert _set(client, "26", "data").status_code == 404
    resp = _set(client, "01", "finished")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_kind"] == "gc_unknown_status"


# ── data list and freeze ──────────────────────────────────────────────────


def test_the_data_list_round_trips_and_rejects_duplicate_ids(client, hub):
    assert _row(client, "15")["data"][0]["label"] == "TSO contingency list"
    items = [{"item_id": "contingencies", "label": "TSO contingency list",
              "supplier": "TSO", "due": "2026-12-01", "status": "received"}]
    resp = client.put(f"{BASE}/deliverables/15/data", json={"items": items})
    assert resp.status_code == 200, resp.text
    assert _row(client, "15")["data"] == items
    resp = client.put(f"{BASE}/deliverables/15/data", json={"items": items * 2})
    assert resp.status_code == 422
    bad = [{**items[0], "due": "next week"}]
    assert client.put(f"{BASE}/deliverables/15/data", json={"items": bad}).status_code == 422


def test_freeze_stores_the_model_set_id_and_a_later_input_edit_marks_it_stale(client, hub):
    resp = client.post(f"{BASE}/deliverables/04/freeze")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_kind"] == "gc_no_campus_input"

    _campus(hub)
    resp = client.post(f"{BASE}/deliverables/04/freeze")
    assert resp.status_code == 200, resp.text
    frozen = resp.json()["freeze"]
    assert frozen["model_set_id"] == "ms-" + frozen["data_hash"][:12]
    assert resp.json()["stale"] is False
    assert _row(client, "04")["freeze"] == frozen and _row(client, "05")["freeze"] is None

    _campus(hub, "campus: {name: y}\n")
    row = _row(client, "04")
    assert row["stale"] is True and row["freeze"] == frozen
    # refreezing the edited input gives another id and clears the flag
    again = client.post(f"{BASE}/deliverables/04/freeze").json()
    assert again["freeze"]["model_set_id"] != frozen["model_set_id"]
    assert again["stale"] is False


def test_the_freeze_hash_covers_the_asset_library_too(client, hub):
    _campus(hub)
    client.post(f"{BASE}/deliverables/04/freeze")
    (ce.campus_dir(hub) / ce.LIBRARY_FILE).write_text("discount_rate: 0.1\n")
    assert _row(client, "04")["stale"] is True


# ── TSO revision log ──────────────────────────────────────────────────────


def test_the_revision_log_round_trips_with_unique_ids_per_deliverable(client, hub):
    first = {"date": "2026-11-01", "clause": "TRGC 06 §4.2", "comment": "Show recovery time."}
    a = client.post(f"{BASE}/deliverables/06/comments", json=first)
    b = client.post(f"{BASE}/deliverables/06/comments", json={**first, "clause": "§5"})
    c = client.post(f"{BASE}/deliverables/07/comments", json=first)
    assert a.status_code == b.status_code == c.status_code == 201
    assert (a.json()["comment_id"], b.json()["comment_id"], c.json()["comment_id"]) == \
        ("C-001", "C-002", "C-001")

    resp = client.patch(f"{BASE}/deliverables/06/comments/C-001",
                        json={"response": "Added in section 4.", "revision": "Rev B",
                              "status": "responded"})
    assert resp.status_code == 200, resp.text
    log = _row(client, "06")["comments"]
    assert [c["comment_id"] for c in log] == ["C-001", "C-002"]
    assert log[0] | {"updated_at": None} == {
        **a.json(), "response": "Added in section 4.", "revision": "Rev B",
        "status": "responded", "updated_at": None}
    assert log[1]["status"] == "open"


def test_a_comment_is_closed_only_with_a_response_and_must_exist(client, hub):
    client.post(f"{BASE}/deliverables/06/comments",
                json={"date": "2026-11-01", "comment": "Check settling."})
    resp = client.patch(f"{BASE}/deliverables/06/comments/C-001", json={"status": "closed"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_kind"] == "gc_comment_invalid"
    assert client.patch(f"{BASE}/deliverables/06/comments/C-009",
                        json={"response": "x"}).status_code == 404
    assert client.post(f"{BASE}/deliverables/06/comments",
                       json={"date": "1 Nov", "comment": "x"}).status_code == 422
    assert client.post(f"{BASE}/deliverables/06/comments",
                       json={"date": "2026-11-01", "comment": ""}).status_code == 422


# ── report pack ───────────────────────────────────────────────────────────

FORBIDDEN_KEYS = ("project", "customer", "client", "consultant", "epc", "oem",
                  "vendor", "manufacturer", "owner")


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def _pack(client, code):
    resp = client.post(f"{BASE}/deliverables/{code}/pack")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(resp.content))


def test_the_pack_lists_every_file_with_its_hash_and_names_no_party(client, hub, monkeypatch):
    monkeypatch.setattr(gc_pack.report_pdf, "pdf_available", lambda: False)
    _campus(hub)
    client.post(f"{BASE}/deliverables/06/freeze")
    client.post(f"{BASE}/deliverables/06/comments",
                json={"date": "2026-11-01", "clause": "§4", "comment": "Recovery time."})
    z = _pack(client, "06")
    manifest = json.loads(z.read("manifest.json"))
    listed = {f["path"]: f for f in manifest["files"]}
    assert set(listed) == set(z.namelist()) - {"manifest.json"}
    assert {"report/TRGC-06.docx", "data_list.csv", "revision_log.csv"} <= set(listed)
    for path, entry in listed.items():
        data = z.read(path)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["bytes"] == len(data)
    assert manifest["deliverable"]["trgc"] == "06"
    assert manifest["model_set"]["model_set_id"].startswith("ms-")
    assert manifest["pending"] == ["GC-04", "GC-07"]
    assert manifest["skipped"] == ["report/TRGC-06.pdf: PDF conversion is not available"]
    assert b"Recovery time." in z.read("revision_log.csv")

    # GC-TEXT-1: no name field, and the project's name appears in no file.
    assert not [k for k in _keys(manifest) if any(w in k.lower() for w in FORBIDDEN_KEYS)]
    for path in z.namelist():
        blob = z.read(path)
        if path.endswith(".docx"):
            inner = zipfile.ZipFile(io.BytesIO(blob))
            blob = b"".join(inner.read(n) for n in inner.namelist() if n.endswith(".xml"))
        assert NAME.encode() not in blob, path


def test_each_pack_is_recorded_and_a_response_can_cite_it(client, hub, monkeypatch):
    monkeypatch.setattr(gc_pack.report_pdf, "pdf_available", lambda: False)
    first = json.loads(_pack(client, "11").read("manifest.json"))
    second = json.loads(_pack(client, "11").read("manifest.json"))
    assert (first["pack_version"], second["pack_version"]) == (1, 2)
    packs = _row(client, "11")["packs"]
    assert [p["version"] for p in packs] == [1, 2]
    client.post(f"{BASE}/deliverables/11/comments",
                json={"date": "2026-11-02", "comment": "Add the transformer datasheet."})
    resp = client.patch(f"{BASE}/deliverables/11/comments/C-001",
                        json={"response": "Added.", "status": "responded", "pack_version": 2})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pack"] == packs[1]
    bad = client.patch(f"{BASE}/deliverables/11/comments/C-001", json={"pack_version": 7})
    assert bad.status_code == 422
    assert bad.json()["detail"]["error_kind"] == "gc_pack_not_found"


def test_a_package_that_registers_output_lights_up_its_row_and_pack(client, hub, monkeypatch):
    from models.report import Table

    monkeypatch.setattr(gc_pack.report_pdf, "pdf_available", lambda: False)
    table = Table(table_id="escr", columns=["case", "ESCR", "verdict"],
                  rows=[["Sk min", "3.1", "pass"]], caption="Strength at the PoC")
    monkeypatch.setitem(sources.SOURCES, "GC-03", lambda project: [table])
    feeds = _row(client, "15")["feeds"]
    assert feeds == [{"package": "GC-03", "available": True},
                     {"package": "GC-04", "available": False}]
    z = _pack(client, "15")
    assert z.read("tables/GC-03-escr.csv") == b"case,ESCR,verdict\nSk min,3.1,pass\n"
    assert json.loads(z.read("manifest.json"))["pending"] == ["GC-04"]


def test_writes_are_refused_while_another_user_holds_the_edit_lock(client, hub, monkeypatch):
    from fastapi import HTTPException
    import routers.projects as projects

    def locked(db, project, user):
        raise HTTPException(status_code=423, detail="locked")

    monkeypatch.setattr(projects, "_check_project_lock", locked)
    assert _set(client, "01", "data").status_code == 423
    assert client.post(f"{BASE}/deliverables/01/freeze").status_code == 423
    assert client.post(f"{BASE}/deliverables/01/pack").status_code == 423
    assert client.get(BASE).status_code == 200


def test_another_orgs_project_is_not_found(client, hub):
    assert client.get("/api/grid-compliance/No Such Project").status_code == 404
