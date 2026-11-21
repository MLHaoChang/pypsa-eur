"""GC-09: the compliance workflow through `/api/grid-compliance`.

The tracker's guards (prerequisites both ways, the freeze, under the lock),
the data freeze and its states, the TSO revision log, the report pack's
manifest and confidentiality (GC-TEXT-1), unreadable files, the edit lock and
unknown deliverables, all through the API on a real project with a network.
"""

import hashlib
import io
import json
import threading
import zipfile
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from db.models import Project
from services import campus_electrical_service as ce
from services import project_registry
from services.grid_compliance import catalogue as cat
from services.grid_compliance import pack as gc_pack
from services.grid_compliance import revision_log, sources, store, tracker
from tests.test_worksheet_foreign_lock import _is_lock_refusal, same_org_other_user  # noqa: F401

#: A distinctive name, so the no-identity check cannot pass by accident.
NAME = "Zq7 Sentinel Plant"
BASE = f"/api/grid-compliance/{NAME}"


@pytest.fixture
def hub(client, api_project, _auth_db):
    """A real project with a saved network, open (resident) in the app."""
    api_project(NAME)
    _engine, session_local = _auth_db
    with session_local() as db:
        project = db.execute(select(Project).where(Project.name == NAME)).scalar_one()
        db.expunge(project)
    return project


@pytest.fixture(autouse=True)
def _no_pdf(monkeypatch):
    monkeypatch.setattr(gc_pack.report_pdf, "pdf_available", lambda: False)


def _network(project):
    from services.pypsa_service import PyPSAService

    return PyPSAService.get_context(project_registry.registry_key(project)).network


def _campus(project, text="campus: {name: x}\n"):
    (ce.campus_dir(project) / ce.CAMPUS_FILE).write_text(text)


def _set(client, code, status):
    return client.put(f"{BASE}/deliverables/{code}/status", json={"status": status})


def _kind(resp):
    return resp.json()["detail"]["error_kind"]


def _row(client, code):
    resp = client.get(BASE)
    assert resp.status_code == 200, resp.text
    return next(r for r in resp.json()["deliverables"] if r["code"] == code)


def _judge(client, *codes):
    for code in codes:
        assert _set(client, code, "judged").status_code == 200, code


def _freeze(client, hub, code):
    if not (ce.campus_dir(hub) / ce.CAMPUS_FILE).is_file():
        _campus(hub)
    resp = client.post(f"{BASE}/deliverables/{code}/freeze")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tracker_file(hub):
    return store.tracker_path(hub)


# ── catalogue and state ───────────────────────────────────────────────────


def test_the_catalogue_is_the_25_trgc_deliverables_with_known_prerequisites():
    assert [d.code for d in cat.DELIVERABLES] == [f"{i:02d}" for i in range(1, 26)]
    assert set(cat.BY_CODE["06"].depends_on) == {"04", "05"}
    assert set(cat.BY_CODE["07"].depends_on) == {"04", "05"}
    assert set(cat.BY_CODE["08"].depends_on) == {"06", "07"}
    for d in cat.DELIVERABLES:
        assert set(d.depends_on) <= set(cat.BY_CODE) and d.code not in d.depends_on
        assert d.phase in cat.PHASES and set(d.packages) <= set(cat.PACKAGE_LABELS)


def test_the_state_lists_every_deliverable_with_its_next_step_and_blockers(client, hub):
    body = client.get(BASE).json()
    assert body["statuses"] == list(cat.STATUSES)
    assert len(body["deliverables"]) == 25
    row = next(r for r in body["deliverables"] if r["code"] == "08")
    assert (
        row["status"] == "not_started" and row["next_step"] == "Collect the data list"
    )
    assert row["blocked_by"] == ["06", "07"] and row["freeze_state"] == "none"
    assert row["feeds"] == [
        {
            "package": "GC-07",
            "label": cat.PACKAGE_LABELS["GC-07"],
            "available": False,
            "state": "pending",
        }
    ]


def test_a_status_is_user_declared_and_a_pending_source_never_reads_judged(client, hub):
    row = _row(client, "06")
    assert row["status"] == "not_started" and row["status_basis"] == "declared"
    _judge(client, "06")
    row = _row(client, "06")
    assert row["status"] == "judged" and row["status_basis"] == "declared"
    assert all(not f["available"] for f in row["feeds"])
    assert _row(client, "07")["status"] == "not_started"


# ── the guards ────────────────────────────────────────────────────────────


def test_submission_waits_for_every_prerequisite_to_be_judged(client, hub):
    _judge(client, "04", "05", "06")
    _set(client, "07", "results_in")
    _freeze(client, hub, "08")  # fresh freeze: only the prerequisite part fails
    resp = _set(client, "08", "submitted")
    assert resp.status_code == 409 and _kind(resp) == "gc_prerequisite_not_judged"
    assert resp.json()["detail"]["blockers"] == ["07"]
    assert _row(client, "08")["status"] == "not_started"
    for later in ("tso_comments", "accepted"):
        assert _kind(_set(client, "08", later)) == "gc_prerequisite_not_judged"
    _judge(client, "07")
    resp = _set(client, "08", "submitted")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "submitted" and resp.json()["blocked_by"] == []
    # work short of submission never waits on prerequisites
    assert _set(client, "25", "results_in").status_code == 200


def test_submission_waits_for_a_fresh_freeze(client, hub):
    _judge(client, "13")  # 14's one prerequisite: only the freeze part fails
    resp = _set(client, "14", "submitted")
    assert resp.status_code == 409 and _kind(resp) == "gc_freeze_not_fresh"
    _freeze(client, hub, "14")
    _campus(hub, "campus: {name: edited}\n")
    resp = _set(client, "14", "submitted")
    assert resp.status_code == 409 and _kind(resp) == "gc_freeze_not_fresh"
    _freeze(client, hub, "14")
    assert _set(client, "14", "submitted").status_code == 200


def test_a_prerequisite_does_not_drop_below_judged_under_a_submitted_dependent(
    client, hub
):
    _judge(client, "04", "05", "06", "07")
    _freeze(client, hub, "08")
    assert _set(client, "08", "submitted").status_code == 200
    resp = _set(client, "07", "results_in")
    assert resp.status_code == 409 and _kind(resp) == "gc_dependent_submitted"
    assert resp.json()["detail"]["blockers"] == ["08"]
    assert _row(client, "07")["status"] == "judged"
    _freeze(client, hub, "07")
    assert _set(client, "07", "accepted").status_code == 200


def _race(monkeypatch, hub, code, status):
    """At lock entry, another writer sets ``code`` to ``status`` in the file."""
    real = store._lock

    @contextmanager
    def racing(path):
        with real(path):
            t = store.load(hub)
            t.deliverables.setdefault(code, store.DeliverableState()).status = status
            path.write_text(t.model_dump_json())
            yield

    monkeypatch.setattr(store, "_lock", racing)


def test_the_demote_guard_reads_statuses_under_the_lock(client, hub, monkeypatch):
    _judge(client, "04", "05", "06", "07")
    _race(monkeypatch, hub, "08", "submitted")
    resp = _set(client, "07", "results_in")
    assert resp.status_code == 409 and _kind(resp) == "gc_dependent_submitted"


def test_the_promote_guard_reads_statuses_under_the_lock(client, hub, monkeypatch):
    _judge(client, "04", "05", "06", "07")
    _freeze(client, hub, "08")
    _race(monkeypatch, hub, "07", "results_in")
    resp = _set(client, "08", "submitted")
    assert resp.status_code == 409 and _kind(resp) == "gc_prerequisite_not_judged"


def test_an_unknown_status_is_refused(client, hub):
    resp = _set(client, "01", "finished")
    assert resp.status_code == 422 and _kind(resp) == "gc_unknown_status"


@pytest.mark.parametrize(
    "method, path, payload",
    [
        ("put", "status", {"status": "data"}),
        ("put", "data", {"items": []}),
        ("post", "freeze", None),
        ("post", "comments", {"date": "2026-11-01", "comment": "x"}),
        ("patch", "comments/C-001", {"response": "x"}),
        ("post", "pack", None),
    ],
)
def test_every_code_route_refuses_an_unknown_deliverable(
    client, hub, method, path, payload
):
    _campus(hub)
    resp = getattr(client, method)(
        f"{BASE}/deliverables/26/{path}",
        **({"json": payload} if payload is not None else {}),
    )
    assert resp.status_code == 404 and _kind(resp) == "gc_unknown_deliverable"


# ── data list and freeze ──────────────────────────────────────────────────


def test_the_data_list_round_trips_and_rejects_bad_items(client, hub):
    assert _row(client, "15")["data"][0]["label"] == "TSO contingency list"
    items = [
        {
            "item_id": "contingencies",
            "label": "TSO contingency list",
            "supplier": "TSO",
            "due": "2026-12-01",
            "status": "received",
        }
    ]
    url = f"{BASE}/deliverables/15/data"
    assert client.put(url, json={"items": items}).status_code == 200
    assert _row(client, "15")["data"] == items
    resp = client.put(url, json={"items": items * 2})
    assert resp.status_code == 422 and _kind(resp) == "gc_duplicate_item"
    for bad in (
        {"due": "next week"},
        {"supplier": "x" * 81},
        {"label": "x" * 201},
        {"status": "lost"},
        {"item_id": "Bad Id"},
    ):
        assert (
            client.put(url, json={"items": [{**items[0], **bad}]}).status_code == 422
        ), bad
    assert _row(client, "15")["data"] == items


def test_freeze_stores_the_model_set_id_and_a_campus_edit_marks_it_stale(client, hub):
    resp = client.post(f"{BASE}/deliverables/04/freeze")
    assert resp.status_code == 409 and _kind(resp) == "gc_no_campus_input"
    row = _freeze(client, hub, "04")
    frozen = row["freeze"]
    assert frozen["model_set_id"] == "ms-" + frozen["data_hash"][:12]
    assert row["freeze_state"] == "fresh" and row["stale"] is False
    assert _row(client, "05")["freeze"] is None

    _campus(hub, "campus: {name: y}\n")
    row = _row(client, "04")
    assert (
        row["freeze_state"] == "stale"
        and row["stale"] is True
        and row["freeze"] == frozen
    )
    again = _freeze(client, hub, "04")
    assert again["freeze"]["model_set_id"] != frozen["model_set_id"]
    assert again["freeze_state"] == "fresh"


def test_the_freeze_covers_the_asset_library(client, hub):
    _freeze(client, hub, "04")
    (ce.campus_dir(hub) / ce.LIBRARY_FILE).write_text("discount_rate: 0.1\n")
    assert _row(client, "04")["freeze_state"] == "stale"


def test_the_freeze_covers_the_network_inputs(client, hub):
    _freeze(client, hub, "04")
    _network(hub).add("Bus", "gc09-extra", v_nom=33.0)
    assert _row(client, "04")["freeze_state"] == "stale"


def test_a_freeze_by_another_method_reads_refreeze_needed(client, hub):
    _judge(client, "13")
    _freeze(client, hub, "14")
    t = store.load(hub)
    t.deliverables["14"].freeze.source = "gc-01"
    _tracker_file(hub).write_text(t.model_dump_json())
    assert _row(client, "14")["freeze_state"] == "refreeze_needed"
    assert _kind(_set(client, "14", "submitted")) == "gc_freeze_not_fresh"


def test_unreadable_inputs_never_read_fresh(client, hub, monkeypatch):
    _judge(client, "13")
    _freeze(client, hub, "14")
    monkeypatch.setattr(tracker, "network_fingerprint", lambda project: None)
    assert _row(client, "14")["freeze_state"] == "unreadable"
    assert _kind(_set(client, "14", "submitted")) == "gc_freeze_not_fresh"
    resp = client.post(f"{BASE}/deliverables/14/freeze")
    assert resp.status_code == 409 and _kind(resp) == "gc_inputs_unreadable"


# ── unreadable or unwritable tracker ──────────────────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[]",
        b'{"schema_version": 2, "deliverables": {}}',
        b'{"schema_version": 1, "deliverables": {"01": {"status": "done"}}}',
        b'{"schema_version": 1, "deliverables": {"99": {}}}',
        b'{"schema_version": 1, "deliverables": {}, "extra": 1}',
        b'{"schema_version": 1, "deliverables": {"06": {"comments": [{"comment_id": "C-001"}]}}}',
        b'{"schema_version": 1, "deliverables": {"14": {"submissions": [{"model_set_id": "x"}]}}}',
    ],
)
def test_an_unreadable_tracker_is_refused_and_never_overwritten(client, hub, content):
    path = _tracker_file(hub)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    for resp in (
        client.get(BASE),
        _set(client, "01", "data"),
        client.post(
            f"{BASE}/deliverables/06/comments",
            json={"date": "2026-11-01", "comment": "x"},
        ),
    ):
        assert resp.status_code == 409 and _kind(resp) == "gc_tracker_unreadable"
    assert path.read_bytes() == content


def test_a_failed_write_stores_nothing_and_the_id_was_never_issued(
    client, hub, monkeypatch
):
    def broken(path, text, encoding="utf-8"):
        raise OSError("disk full")

    monkeypatch.setattr(store, "atomic_write_text", broken)
    resp = client.post(
        f"{BASE}/deliverables/06/comments", json={"date": "2026-11-01", "comment": "x"}
    )
    assert resp.status_code == 503 and _kind(resp) == "gc_tracker_write_failed"
    monkeypatch.undo()
    monkeypatch.setattr(gc_pack.report_pdf, "pdf_available", lambda: False)
    assert _row(client, "06")["comments"] == []
    ok = client.post(
        f"{BASE}/deliverables/06/comments", json={"date": "2026-11-01", "comment": "y"}
    )
    # The failed one was never stored nor returned, so C-001 is first issued here.
    assert ok.json()["comment_id"] == "C-001"


def test_a_busy_lock_is_a_typed_refusal(client, hub, monkeypatch):
    @contextmanager
    def busy(path, **kwargs):
        raise TimeoutError
        yield  # pragma: no cover

    monkeypatch.setattr(store, "file_lock", busy)
    resp = _set(client, "01", "data")
    assert resp.status_code == 409 and _kind(resp) == "gc_tracker_busy"


# ── TSO revision log ──────────────────────────────────────────────────────


def test_the_revision_log_round_trips_with_unique_ids_per_deliverable(client, hub):
    first = {
        "date": "2026-11-01",
        "clause": "TRGC 06 §4.2",
        "comment": "Show recovery time.",
    }
    a = client.post(f"{BASE}/deliverables/06/comments", json=first)
    b = client.post(f"{BASE}/deliverables/06/comments", json={**first, "clause": "§5"})
    c = client.post(f"{BASE}/deliverables/07/comments", json=first)
    assert a.status_code == b.status_code == c.status_code == 201
    assert (a.json()["comment_id"], b.json()["comment_id"], c.json()["comment_id"]) == (
        "C-001",
        "C-002",
        "C-001",
    )
    resp = client.patch(
        f"{BASE}/deliverables/06/comments/C-001",
        json={
            "response": "Added in section 4.",
            "revision": "Rev B",
            "status": "responded",
        },
    )
    assert resp.status_code == 200, resp.text
    log = _row(client, "06")["comments"]
    assert [x["comment_id"] for x in log] == ["C-001", "C-002"]
    assert log[0] | {"updated_at": None} == {
        **a.json(),
        "response": "Added in section 4.",
        "revision": "Rev B",
        "status": "responded",
        "updated_at": None,
    }
    assert log[1]["status"] == "open"


def test_concurrent_comments_get_distinct_ids(client, hub):
    ids, errors = [], []

    def post(i):
        try:
            ids.append(
                revision_log.add(hub, "06", {"date": "2026-11-01", "comment": f"c{i}"})[
                    "comment_id"
                ]
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=post, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(ids) == [f"C-{i:03d}" for i in range(1, 9)]
    assert len(_row(client, "06")["comments"]) == 8


def test_a_comment_is_closed_only_with_a_response_and_fields_are_capped(client, hub):
    url = f"{BASE}/deliverables/06/comments"
    client.post(url, json={"date": "2026-11-01", "comment": "Check settling."})
    resp = client.patch(f"{url}/C-001", json={"status": "closed"})
    assert resp.status_code == 422 and _kind(resp) == "gc_comment_invalid"
    resp = client.patch(f"{url}/C-009", json={"response": "x"})
    assert resp.status_code == 404 and _kind(resp) == "gc_comment_not_found"
    for bad in (
        {"date": "1 Nov"},
        {"comment": ""},
        {"comment": "x" * 4001},
        {"clause": "x" * 65},
        {"revision": "x" * 17},
        {"owner": "x"},
    ):
        body = {"date": "2026-11-01", "comment": "x", **bad}
        assert client.post(url, json=body).status_code == 422, bad
    assert (
        client.patch(f"{url}/C-001", json={"response": "x" * 4001}).status_code == 422
    )
    assert [c["comment_id"] for c in _row(client, "06")["comments"]] == ["C-001"]


# ── report pack ───────────────────────────────────────────────────────────

FORBIDDEN_KEYS = (
    "project",
    "customer",
    "client",
    "consultant",
    "epc",
    "oem",
    "vendor",
    "manufacturer",
    "owner",
)


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
    return resp, zipfile.ZipFile(io.BytesIO(resp.content))


def test_the_pack_lists_every_file_with_its_hash_and_names_no_party(client, hub):
    _freeze(client, hub, "06")
    client.post(
        f"{BASE}/deliverables/06/comments",
        json={"date": "2026-11-01", "clause": "§4", "comment": "Recovery time."},
    )
    resp, z = _pack(client, "06")
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
    assert manifest["freeze_state"] == "fresh"
    assert [(s["package"], s["state"]) for s in manifest["sources"]] == [
        ("GC-04", "pending"),
        ("GC-07", "pending"),
    ]
    assert manifest["skipped"] == [
        {"path": "report/TRGC-06.pdf", "reason": "PDF conversion is not available"}
    ]
    assert b"Recovery time." in z.read("revision_log.csv")

    # GC-TEXT-1: no name field; the project's name is in no file, member name
    # or download name.
    assert not [
        k for k in _keys(manifest) if any(w in k.lower() for w in FORBIDDEN_KEYS)
    ]
    words = [NAME, "Zq7", "Sentinel"]
    assert not any(w in resp.headers["content-disposition"] for w in words)
    for path in z.namelist():
        assert not any(w in path for w in words), path
        blob = z.read(path)
        if path.endswith(".docx"):
            inner = zipfile.ZipFile(io.BytesIO(blob))
            assert not any(w in n for n in inner.namelist() for w in words)
            blob = b"".join(inner.read(n) for n in inner.namelist())
        assert not any(w.encode() in blob for w in words), path
    # package ids stay out of the report text (CODING_STANDARDS UI #2)
    docx = zipfile.ZipFile(io.BytesIO(z.read("report/TRGC-06.docx")))
    assert b"GC-0" not in docx.read("word/document.xml")


def test_the_user_guides_and_datasheets_carry_their_own_sections(client, hub):
    for code, heading in (
        ("09", b"RMS model overview"),
        ("10", b"EMT model overview"),
        ("11", b"Plant and unit data"),
    ):
        _, z = _pack(client, code)
        docx = zipfile.ZipFile(io.BytesIO(z.read(f"report/TRGC-{code}.docx")))
        assert heading in docx.read("word/document.xml"), code


def test_each_pack_is_recorded_and_a_response_can_cite_it(client, hub):
    _, z1 = _pack(client, "11")
    _, z2 = _pack(client, "11")
    first, second = (json.loads(z.read("manifest.json")) for z in (z1, z2))
    assert (first["pack_version"], second["pack_version"]) == (1, 2)
    packs = _row(client, "11")["packs"]
    assert [p["version"] for p in packs] == [1, 2]
    assert packs[1]["digest"] == hashlib.sha256(z2.read("manifest.json")).hexdigest()
    client.post(
        f"{BASE}/deliverables/11/comments",
        json={"date": "2026-11-02", "comment": "Add the transformer datasheet."},
    )
    resp = client.patch(
        f"{BASE}/deliverables/11/comments/C-001",
        json={"response": "Added.", "status": "responded", "pack_version": 2},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pack"] == packs[1]
    bad = client.patch(
        f"{BASE}/deliverables/11/comments/C-001", json={"pack_version": 7}
    )
    assert bad.status_code == 422 and _kind(bad) == "gc_pack_not_found"


def test_a_pack_that_fails_to_build_records_no_version(client, hub, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(gc_pack, "render_document_docx", boom)
    with pytest.raises(RuntimeError):
        gc_pack.build(hub, "11")
    assert _row(client, "11")["packs"] == []


def test_a_pack_made_meanwhile_is_a_conflict(client, hub, monkeypatch):
    real = gc_pack._render

    def render_then_race(*args, **kwargs):
        out = real(*args, **kwargs)
        with store.editing(hub) as t:
            t.deliverables.setdefault("11", store.DeliverableState()).packs.append(
                gc_pack.RevisionRef(version=1, digest="0" * 64)
            )
        return out

    monkeypatch.setattr(gc_pack, "_render", render_then_race)
    resp = client.post(f"{BASE}/deliverables/11/pack")
    assert resp.status_code == 409 and _kind(resp) == "gc_pack_conflict"
    assert [p["digest"] for p in _row(client, "11")["packs"]] == ["0" * 64]


def test_a_package_that_registers_output_lights_up_its_row_and_pack(
    client, hub, monkeypatch
):
    from models.report import Table

    table = Table(
        table_id="escr",
        columns=["case", "ESCR", "verdict"],
        rows=[["Sk min", "3.1", "pass"]],
        caption="Strength at the PoC",
    )
    monkeypatch.setitem(sources.SOURCES, "GC-03", lambda project: [table])
    assert [f["available"] for f in _row(client, "15")["feeds"]] == [True, False]
    _, z = _pack(client, "15")
    assert z.read("tables/GC-03-escr.csv") == b"case,ESCR,verdict\nSk min,3.1,pass\n"
    assert [s["state"] for s in json.loads(z.read("manifest.json"))["sources"]] == [
        "present",
        "pending",
    ]


# ── edit lock and access ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method, path, payload",
    [
        ("put", "deliverables/01/status", {"status": "data"}),
        ("put", "deliverables/01/data", {"items": []}),
        ("post", "deliverables/01/freeze", None),
        ("post", "deliverables/06/comments", {"date": "2026-11-01", "comment": "x"}),
        ("patch", "deliverables/06/comments/C-001", {"response": "x"}),
        ("post", "deliverables/01/pack", None),
    ],
)
def test_every_write_is_refused_under_a_foreign_lock(
    client,
    hub,
    same_org_other_user,  # noqa: F811
    method,
    path,
    payload,
):
    _campus(hub)
    client.post(
        f"{BASE}/deliverables/06/comments", json={"date": "2026-11-01", "comment": "x"}
    )
    before = _tracker_file(hub).read_bytes()
    assert client.post(f"/api/projects/{NAME}/lock").status_code == 200
    resp = getattr(same_org_other_user, method)(
        f"{BASE}/{path}", **({"json": payload} if payload is not None else {})
    )
    assert _is_lock_refusal(resp), f"{resp.status_code} {resp.text[:200]}"
    assert _tracker_file(hub).read_bytes() == before
    assert same_org_other_user.get(BASE).status_code == 200


def test_an_unknown_project_is_not_found(client, hub):
    assert client.get("/api/grid-compliance/No Such Project").status_code == 404


# ── gaps found by scripts/coord/mutants.py ────────────────────────────────


def test_a_deleted_campus_input_makes_the_freeze_unreadable(client, hub):
    _freeze(client, hub, "04")
    (ce.campus_dir(hub) / ce.CAMPUS_FILE).unlink()
    assert _row(client, "04")["freeze_state"] == "unreadable"


def test_a_prerequisite_may_step_back_to_judged_under_a_submitted_dependent(
    client, hub
):
    _judge(client, "04", "05", "06", "07")
    _freeze(client, hub, "07")
    assert _set(client, "07", "accepted").status_code == 200
    _freeze(client, hub, "08")
    assert _set(client, "08", "submitted").status_code == 200
    assert _set(client, "07", "judged").status_code == 200


def test_the_service_refuses_unknown_comment_fields_and_unknown_comments(client, hub):
    with pytest.raises(store.GridComplianceError) as exc:
        revision_log.add(
            hub, "06", {"date": "2026-11-01", "comment": "x", "owner": "y"}
        )
    assert exc.value.kind == "gc_comment_invalid"
    with pytest.raises(store.GridComplianceError) as exc:
        revision_log.update(hub, "06", "C-001", {"vendor": "y"})
    assert exc.value.kind == "gc_comment_invalid"
    # a deliverable never touched before has no comments: 404, not a crash
    resp = client.patch(
        f"{BASE}/deliverables/12/comments/C-001", json={"response": "x"}
    )
    assert resp.status_code == 404 and _kind(resp) == "gc_comment_not_found"


def _document(z, code):
    return zipfile.ZipFile(io.BytesIO(z.read(f"report/TRGC-{code}.docx"))).read(
        "word/document.xml"
    )


def test_the_pack_contents_follow_the_tracker(client, hub, monkeypatch):
    from models.report import Table

    _freeze(client, hub, "15")
    items = [
        {
            "item_id": "contingencies",
            "label": "TSO contingency list",
            "supplier": "TSO",
            "due": None,
            "status": "received",
        }
    ]
    client.put(f"{BASE}/deliverables/15/data", json={"items": items})
    table = Table(
        table_id="escr",
        columns=["case"],
        rows=[["Sk min"]],
        caption="Strength at the PoC",
    )
    monkeypatch.setitem(sources.SOURCES, "GC-03", lambda project: [table])
    _campus(hub, "campus: {name: edited}\n")
    _, z = _pack(client, "15")
    assert z.read("data_list.csv") == (
        b"item_id,label,supplier,due,status\n"
        b"contingencies,TSO contingency list,TSO,,received\n"
    )
    doc = _document(z, "15")
    assert b"Freeze: out of date" in doc
    assert b"Strength at the PoC" in doc
    assert b"No prerequisite deliverable" in doc
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["deliverable"] == {
        "trgc": "15",
        "title": cat.BY_CODE["15"].title,
        "phase": 3,
        "tool": "PSS/E",
    }
    assert (
        manifest["schema_version"] == 1 and manifest["grid_code"] == gc_pack.GRID_CODE
    )
    assert manifest["generated_at"] and manifest["freeze_state"] == "stale"
    assert set(manifest) == {
        "schema_version",
        "deliverable",
        "pack_version",
        "generated_at",
        "grid_code",
        "model_set",
        "submissions",
        "freeze_state",
        "since_submission",
        "sources",
        "skipped",
        "files",
    }
    _, z = _pack(client, "08")
    assert b"Requires TRGC 06" in _document(z, "08")
    assert b"Freeze: " not in _document(z, "08")


# ── GK B2 review: the submission record, the network read, CSV cells ─────


def test_the_model_set_a_deliverable_was_submitted_on_is_kept(client, hub):
    _judge(client, "13")
    submitted_on = _freeze(client, hub, "14")["freeze"]
    resp = _set(client, "14", "submitted")
    assert resp.status_code == 200, resp.text
    assert resp.json()["submissions"] == [submitted_on]

    row = _row(client, "14")
    assert row["freeze_state"] == "submitted"
    assert row["since_submission"] == {"inputs": "unchanged", "reason": None}
    _network(hub).add("Bus", "gc09-after-submission", v_nom=33.0)
    # judged against the submission record: never "out of date", which would
    # ask for a re-freeze the server refuses
    row = _row(client, "14")
    assert row["freeze_state"] == "submitted" and row["stale"] is False
    assert row["since_submission"] == {"inputs": "changed", "reason": None}
    _, z = _pack(client, "14")
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["freeze_state"] == "submitted"
    assert manifest["since_submission"] == {"inputs": "changed", "reason": None}
    doc = _document(z, "14")
    assert b"follows the model set it was submitted on" in doc
    assert b"out of date" not in doc and b"no longer match" not in doc
    # a re-freeze cannot replace the submitted model set ...
    resp = client.post(f"{BASE}/deliverables/14/freeze")
    assert resp.status_code == 409 and _kind(resp) == "gc_freeze_after_submission"
    row = _row(client, "14")
    assert row["freeze"] == submitted_on and row["submissions"] == [submitted_on]
    # ... and later moves are judged against the record, not the edited inputs
    for later in ("tso_comments", "accepted"):
        resp = _set(client, "14", later)
        assert resp.status_code == 200, resp.text
        assert resp.json()["submissions"] == [submitted_on]


def _submit_14(client, hub):
    _judge(client, "13")
    _freeze(client, hub, "14")
    assert _set(client, "14", "submitted").status_code == 200


def _since_in_pack(client):
    _, z = _pack(client, "14")
    return json.loads(z.read("manifest.json"))["since_submission"], _document(z, "14")


def test_unreadable_inputs_are_not_compared_with_the_submission(
    client, hub, monkeypatch
):
    _submit_14(client, hub)
    monkeypatch.setattr(tracker, "network_fingerprint", lambda project: None)
    want = {"inputs": "not_compared", "reason": "The current inputs cannot be read."}
    row = _row(client, "14")
    assert row["freeze_state"] == "submitted" and row["since_submission"] == want
    since, doc = _since_in_pack(client)
    assert since == want
    assert b"were not compared with the submitted record" in doc
    assert b"changed after submission" not in doc


def test_a_record_by_another_method_is_not_compared_with_the_submission(client, hub):
    _submit_14(client, hub)
    t = store.load(hub)
    t.deliverables["14"].submissions[-1].source = "gc-01"
    _tracker_file(hub).write_text(t.model_dump_json())
    want = {
        "inputs": "not_compared",
        "reason": "The submitted record was made by an older freeze method.",
    }
    assert _row(client, "14")["since_submission"] == want
    since, doc = _since_in_pack(client)
    assert since == want
    assert b"were not compared" in doc and b"changed after submission" not in doc


def test_unchanged_inputs_say_so_in_the_pack(client, hub):
    _submit_14(client, hub)
    since, doc = _since_in_pack(client)
    assert since == {"inputs": "unchanged", "reason": None}
    assert b"unchanged since submission" in doc
    assert _row(client, "13")["since_submission"] is None


def test_a_resubmission_keeps_the_earlier_record_next_to_the_new_one(client, hub):
    _judge(client, "13")
    first = _freeze(client, hub, "14")["freeze"]
    assert _set(client, "14", "submitted").status_code == 200
    _campus(hub, "campus: {name: revised}\n")
    assert _set(client, "14", "judged").status_code == 200
    # back below submitted, the edited inputs need a fresh freeze again
    assert _kind(_set(client, "14", "submitted")) == "gc_freeze_not_fresh"
    second = _freeze(client, hub, "14")["freeze"]
    assert second["model_set_id"] != first["model_set_id"]
    assert _set(client, "14", "submitted").status_code == 200
    assert _row(client, "14")["submissions"] == [first, second]
    _, z = _pack(client, "14")
    assert json.loads(z.read("manifest.json"))["submissions"] == [first, second]


class _EditOnRelease:
    """A mutation lock that lets an edit land right after it is released."""

    def __init__(self, ctx, times):
        self.ctx, self.lock, self.times = ctx, ctx.mutation_lock, times

    def acquire(self, blocking=True, timeout=-1):
        return self.lock.acquire(blocking, timeout)

    def release(self):
        self.lock.release()
        if self.times:
            self.times -= 1
            self.ctx.network.add("Bus", f"gc09-late-{self.times}", v_nom=33.0)
            self.ctx.network_revision = (self.ctx.network_revision or 0) + 1


def _ctx(project):
    from services.pypsa_service import PyPSAService

    return PyPSAService.get_context(project_registry.registry_key(project))


def test_the_freeze_reads_the_network_inside_the_edit_bracket(client, hub, monkeypatch):
    _campus(hub)
    ctx = _ctx(hub)
    monkeypatch.setattr(ctx, "mutation_lock", _EditOnRelease(ctx, times=1))
    row = _freeze(client, hub, "04")
    # the edit that landed during the read was read again: the stamp matches
    # the network as it is now, not the state before the edit
    assert row["freeze_state"] == "fresh"
    assert row["freeze"]["data_hash"] == tracker.data_hash(hub)


def test_a_network_that_keeps_moving_during_the_read_is_not_frozen(
    client, hub, monkeypatch
):
    _campus(hub)
    ctx = _ctx(hub)
    monkeypatch.setattr(ctx, "mutation_lock", _EditOnRelease(ctx, times=2))
    resp = client.post(f"{BASE}/deliverables/04/freeze")
    assert resp.status_code == 409 and _kind(resp) == "gc_inputs_unreadable"
    assert _row(client, "04")["freeze"] is None


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_cells_that_a_spreadsheet_reads_as_formulas_are_neutralised(
    client, hub, lead
):
    import csv

    payload = f'{lead}HYPERLINK("http://x")'
    item = {"item_id": "x", "label": payload, "supplier": payload}
    assert (
        client.put(f"{BASE}/deliverables/06/data", json={"items": [item]}).status_code
        == 200
    )
    resp = client.post(
        f"{BASE}/deliverables/06/comments",
        json={"date": "2026-11-01", "clause": payload, "comment": payload},
    )
    assert resp.status_code == 201, resp.text
    short = f"{lead}1+1"  # the revision is capped at 16 characters
    resp = client.patch(
        f"{BASE}/deliverables/06/comments/C-001",
        json={"response": payload, "revision": short},
    )
    assert resp.status_code == 200, resp.text
    _, z = _pack(client, "06")
    data = list(csv.reader(io.StringIO(z.read("data_list.csv").decode())))
    log = list(csv.reader(io.StringIO(z.read("revision_log.csv").decode())))
    assert data[1][1] == data[1][2] == "'" + payload
    assert log[1][2] == log[1][3] == log[1][4] == "'" + payload
    assert log[1][5] == "'" + short


@pytest.mark.parametrize("number", ["-3.1", "+2", "-0.5e-3", "-.5", "+7."])
def test_plain_numbers_in_a_csv_stay_numbers(number):
    assert gc_pack._safe_cell(number) == number


@pytest.mark.parametrize(
    "cell", ["-1+1", "-inf", "+nan", "-1_000", "\t1", "\r-1", "-1 ", "+0x1f", "-1e"]
)
def test_only_a_strict_decimal_escapes_the_formula_guard(cell):
    assert gc_pack._safe_cell(cell) == "'" + cell
    assert gc_pack._safe_cell("Sk min") == "Sk min"


def test_a_source_that_raises_reads_unavailable_never_a_500(
    client, hub, monkeypatch, caplog
):
    def broken(project):
        raise RuntimeError("provider bug")

    monkeypatch.setitem(sources.SOURCES, "GC-03", broken)
    feeds = _row(client, "15")["feeds"]
    assert [(f["state"], f["available"]) for f in feeds] == [
        ("unavailable", False),
        ("pending", False),
    ]
    _, z = _pack(client, "15")
    assert [s["state"] for s in json.loads(z.read("manifest.json"))["sources"]] == [
        "unavailable",
        "pending",
    ]
    assert b"could not be read" in _document(z, "15")
    logged = [
        r
        for r in caplog.records
        if r.name == sources.__name__ and "GC-03 could not be read" in r.getMessage()
    ]
    assert logged and all(
        r.exc_info and "provider bug" in str(r.exc_info[1]) for r in logged
    )


def test_an_invalid_field_is_named_in_the_refusal(client, hub):
    resp = client.put(
        f"{BASE}/deliverables/15/data",
        json={"items": [{"item_id": "x", "label": "y", "due": "next week"}]},
    )
    assert resp.status_code == 422 and _kind(resp) == "gc_invalid"
    assert resp.json()["detail"]["field"] == "due"
    assert resp.json()["detail"]["message"].startswith("due: ")
