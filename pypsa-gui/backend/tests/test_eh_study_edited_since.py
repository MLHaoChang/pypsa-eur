"""
P33b 10b — "the network has been edited since the last study".

A per-context edit counter (`ProjectContext.network_revision`) is bumped at the
three edit seams — the HTTP undo-prefix chokepoint on a 2xx (B1), the chat
dispatch site for tools that write the live network (B2) and `set_network`
(B3) — captured on the EH study record when the study starts, and compared at
read time into `edited_since_study: true | false | null` on `/eh_study` and
`/eh_review` (one source, so `review_eh_study` carries it too).

The signal is "an edit seam was crossed", so the field and its sentences say
"edited since", never "changed" or "differs". A Solve, the study's private
copy and the solver's transient rows never cross a seam, so neither can
false-positive; the tests below pin both directions.
"""
from __future__ import annotations

import time

import pytest

from services import chat_tools_schema as S
from tests.test_chat_edits_are_captured import _dispatch
from tests.test_eh_study_record_survives_reactivation import (
    fake_done,
    hub_project,
)
from tests.test_undo_rekeys_the_session_context import _as_session

UNDO_INFO = "/api/network/undo/info"


def _rev(client) -> int:
    body = client.get(UNDO_INFO).json()
    assert isinstance(body.get("network_revision"), int), body
    return body["network_revision"]


def _first_bus(client):
    return client.get("/api/network/buses").json()[0]


def _edit_bus(client, dx=0.001):
    bus = _first_bus(client)
    r = client.put(f"/api/network/buses/{bus['name']}",
                   json={**bus, "x": float(bus.get("x") or 0.0) + dx})
    assert r.status_code == 200, r.text
    return bus


# ── B1: the HTTP seam ──────────────────────────────────────────────────────

def test_undo_info_carries_the_revision_and_its_other_keys_are_unchanged(
        client, install_network):
    hub_project(client, install_network, "undo-info-keys")
    body = client.get(UNDO_INFO).json()
    assert set(body) == {"depth", "unsaved", "memory_bytes", "max_bytes",
                         "max_steps", "network_revision"}
    assert isinstance(body["network_revision"], int)


def test_an_asset_write_bumps_by_one(client, install_network):
    hub_project(client, install_network, "bump-one")
    r0 = _rev(client)
    _edit_bus(client)
    assert _rev(client) == r0 + 1


def test_a_refused_edit_and_a_read_never_bump(client, install_network):
    hub_project(client, install_network, "refused")
    r0 = _rev(client)
    r = client.put("/api/network/buses/does-not-exist",
                   json={"name": "does-not-exist", "x": 1.0})
    assert r.status_code == 404, r.text
    assert _rev(client) == r0
    assert client.get("/api/network/buses").status_code == 200
    assert _rev(client) == r0


def test_the_study_itself_and_a_foreground_solve_never_bump(
        client, install_network, monkeypatch, session_state):
    from tests.test_solver_run_api import _run_and_join

    fake_done(monkeypatch)
    hub_project(client, install_network, "never-bump")
    r0 = _rev(client)
    r = client.post("/api/results/eh_study", json={"archetype": "weak_flexible"})
    assert r.status_code == 200, r.text
    deadline = time.time() + 30
    while client.get("/api/results/eh_study").json().get("status") == "running":
        assert time.time() < deadline
        time.sleep(0.05)
    t = (session_state(client).get("eh_study") or {}).get("thread")
    if t is not None:
        t.join(2.0)
    assert _rev(client) == r0, "the study bumped the edit counter"

    _run_and_join(client, session_state)
    assert _rev(client) == r0, "a foreground Solve bumped the edit counter"


def test_undo_bumps_on_the_context_later_requests_read(client, install_network):
    """An undo is an edit (+1 on top of the edit's +1), read on a FRESH request
    — so it measures the context later requests read (step 0)."""
    hub_project(client, install_network, "undo-plus-two")
    r0 = _rev(client)
    _edit_bus(client)
    assert client.post("/api/network/undo").status_code == 200
    assert _rev(client) == r0 + 2


def test_a_new_network_does_not_bump_the_project_it_left(
        client, install_network, api_project):
    """`POST /api/network/reset` ("New") is under the HTTP seam's prefix; the
    bump lands on the context the request ENDED on (the fresh draft), never on
    the project the user left — otherwise that project's study would read
    "edited" when nothing touched it."""
    a = hub_project(client, install_network, "left-alone")
    r0 = _rev(client)
    assert client.post("/api/network/reset").status_code == 200
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    assert _rev(client) == r0


# ── B2: the chat seam ──────────────────────────────────────────────────────

def _chat(client, _auth_db, name, args):
    with _as_session(client, _auth_db):
        frames, _ = _dispatch(name, args)
    return [ev for ev, _p in frames]


def test_chat_edit_tools_bump_and_execution_and_read_tools_do_not(
        client, install_network, monkeypatch, _auth_db):
    from services import chat_service

    monkeypatch.setattr(chat_service, "AUTO_APPROVE_TIERS",
                        frozenset({"destructive", "execution",
                                   "execution_long_running"}))
    fake_done(monkeypatch)
    hub_project(client, install_network, "chat-bumps")
    bus = _first_bus(client)

    r0 = _rev(client)
    ev = _chat(client, _auth_db, "update_component", {
        "component_class": "Bus", "name": bus["name"],
        "attrs": {"x": float(bus.get("x") or 0.0) + 0.5}})
    assert "tool_result" in ev, ev
    assert _rev(client) == r0 + 1

    ev = _chat(client, _auth_db, "batch_create_components", {
        "component_class": "Bus", "components": [{"name": "chat-new-bus"}]})
    assert "tool_result" in ev, ev
    assert _rev(client) == r0 + 2

    ev = _chat(client, _auth_db, "run_eh_study", {"archetype": "weak_flexible"})
    assert "tool_result" in ev, ev
    deadline = time.time() + 30
    while client.get("/api/results/eh_study").json().get("status") == "running":
        assert time.time() < deadline
        time.sleep(0.05)
    assert _rev(client) == r0 + 2

    for name, args in (("review_eh_study", {}),
                       ("get_adequacy_results", {"kind": "eh_study"})):
        ev = _chat(client, _auth_db, name, args)
        assert "tool_result" in ev or "tool_error" in ev, (name, ev)
    assert _rev(client) == r0 + 2


def test_a_chat_edit_tool_that_raises_or_times_out_still_bumps(
        client, install_network, monkeypatch, _auth_db):
    import threading

    from services import chat_service, chat_tools

    hub_project(client, install_network, "chat-raise")
    r0 = _rev(client)

    def boom(**kw):
        raise RuntimeError("handler failed part-way")

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_component", boom)
    ev = _chat(client, _auth_db, "update_component", {
        "component_class": "Bus", "name": "x", "attrs": {"x": 1.0}})
    assert "tool_error" in ev, ev
    assert _rev(client) == r0 + 1

    release = threading.Event()

    def hang(**kw):
        release.wait(10)
        return {}

    monkeypatch.setitem(chat_tools.DISPATCHERS, "update_component", hang)
    monkeypatch.setattr(chat_service, "PER_TOOL_TIMEOUT_SECONDS", 0.2)
    try:
        with _as_session(client, _auth_db):
            frames, _ = _dispatch("update_component", {
                "component_class": "Bus", "name": "x", "attrs": {"x": 1.0}})
    finally:
        release.set()
    kinds = [p.get("error_kind") for ev, p in frames if ev == "tool_error"]
    assert "tool_timeout" in kinds, frames
    assert _rev(client) == r0 + 2


def _tool_route_edits(name):
    from main import _UNDO_PREFIXES
    routes = S.TOOL_ROUTES[name]
    return any(isinstance(r, tuple) and r[0] != "GET"
               and r[1].startswith(_UNDO_PREFIXES) for r in routes)


def test_tool_edits_network_is_derived_from_tool_routes_plus_one_pinned_list():
    from main import _UNDO_PREFIXES

    assert S.NETWORK_EDIT_PREFIXES == _UNDO_PREFIXES, (
        "the chat predicate's prefixes drifted from the HTTP seam's")
    for name in ("update_component", "undo_last", "delete_component",
                 "batch_create_components", "apply_demand_from_excel"):
        assert S.tool_edits_network(name) is True, name
    for name in ("run_eh_study", "review_eh_study", "run_simulation",
                 "gridspine_run_pipeline", "export_to_excel"):
        assert S.tool_edits_network(name) is False, name


@pytest.mark.parametrize("name", sorted(S.TOOL_ROUTES))
def test_every_tool_with_a_network_write_route_edits_the_network(name):
    if _tool_route_edits(name):
        assert S.tool_edits_network(name) is True


@pytest.mark.parametrize("name", sorted(S.TOOL_ROUTES))
def test_every_service_call_writer_is_classified_exactly_once(name):
    routes = S.TOOL_ROUTES[name]
    is_service_call = list(routes) == list(S._SERVICE_CALL)
    tier = S.safety_tier_for(name)
    in_net = name in S._SERVICE_CALL_NETWORK_WRITES
    in_non = name in S._SERVICE_CALL_NON_NETWORK_WRITES
    if is_service_call and tier in ("write", "destructive"):
        assert in_net + in_non == 1, (
            f"{name} is a {tier} service-call tool and must be classified in "
            "exactly one of _SERVICE_CALL_NETWORK_WRITES / "
            "_SERVICE_CALL_NON_NETWORK_WRITES")
    else:
        assert not (in_net or in_non), (
            f"{name} is listed as a service-call writer but is not one")


def test_the_service_call_lists_name_only_existing_tools():
    service = {n for n, r in S.TOOL_ROUTES.items() if list(r) == list(S._SERVICE_CALL)}
    assert S._SERVICE_CALL_NETWORK_WRITES <= service
    assert S._SERVICE_CALL_NON_NETWORK_WRITES <= service
    assert not (S._SERVICE_CALL_NETWORK_WRITES & S._SERVICE_CALL_NON_NETWORK_WRITES)


# ── B3: an in-process replace ──────────────────────────────────────────────

def test_set_network_bumps_the_carried_revision(client, install_network):
    import pypsa

    from services import dirty_state
    from services.pypsa_service import PyPSAService

    install_network(pypsa.Network())
    r0 = dirty_state.revision()
    n = pypsa.Network()
    n.add("Bus", "b")
    PyPSAService.set_network(n)
    assert dirty_state.revision() == r0 + 1


# ── Step 3: capture on the record, compared at read time ─────────────────

STUDY_URL = "/api/results/eh_study"
REVIEW_URL = "/api/results/eh_review"


def _tool(ctx):
    from tests.test_eh_review_route import _tool as review_tool
    return review_tool(ctx)


def _done_study(client, install_network, monkeypatch, session_state, name):
    from tests.test_eh_study_record_survives_reactivation import run_study
    fake_done(monkeypatch)
    hub_project(client, install_network, name)
    rec = run_study(client)
    t = (session_state(client).get("eh_study") or {}).get("thread")
    if t is not None:
        t.join(5)
    return rec


def _both(client):
    study = client.get(STUDY_URL)
    review = client.get(REVIEW_URL)
    assert study.status_code == 200, study.text
    assert review.status_code == 200, review.text
    return study.json(), review.json()


def test_a_finished_study_carries_the_revision_and_reads_unedited(
        client, install_network, monkeypatch, session_state, session_ctx):
    rec = _done_study(client, install_network, monkeypatch, session_state, "carries")
    assert rec["network_revision"] == _rev(client)
    study, review = _both(client)
    assert study["edited_since_study"] is False
    assert review["edited_since_study"] is False
    assert review["stale"] is False
    assert review == _tool(session_ctx(client))


def test_an_asset_write_after_the_study_reads_edited_on_both_routes(
        client, install_network, monkeypatch, session_state, session_ctx):
    _done_study(client, install_network, monkeypatch, session_state, "edited-after")
    r0 = _rev(client)
    _edit_bus(client)
    study, review = _both(client)
    assert study["edited_since_study"] is True
    assert review["edited_since_study"] is True
    assert review["stale"] is False
    assert _rev(client) == r0 + 1
    assert review == _tool(session_ctx(client))


def test_a_solve_after_the_study_is_stale_but_not_edited(
        client, install_network, monkeypatch, session_state, session_ctx):
    from services.adequacy.eh_report import EH_REPORT_STORE_KEY

    _done_study(client, install_network, monkeypatch, session_state, "stale-only")
    session_ctx(client).solver_state.pop(EH_REPORT_STORE_KEY, None)
    study, review = _both(client)
    assert review["stale"] is True
    assert review["edited_since_study"] is False
    assert study["edited_since_study"] is False
    _edit_bus(client)
    study, review = _both(client)
    assert review["stale"] is True
    assert review["edited_since_study"] is True
    assert study["edited_since_study"] is True


def test_undo_bumps_and_keeps_the_finished_record(
        client, install_network, monkeypatch, session_state):
    _done_study(client, install_network, monkeypatch, session_state, "undo-keeps")
    r0 = _rev(client)
    _edit_bus(client)
    assert client.post("/api/network/undo").status_code == 200
    # Fresh requests: the context later requests read (step 0).
    r = client.get(STUDY_URL)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    assert r.json()["edited_since_study"] is True
    assert _rev(client) == r0 + 2


def test_an_edit_during_a_running_study_reads_edited_when_it_finishes(
        client, install_network, monkeypatch, session_state):
    import threading

    gate, reached = threading.Event(), threading.Event()
    fake_done(monkeypatch, gate=gate, reached=reached)
    hub_project(client, install_network, "edit-during")
    try:
        r = client.post(STUDY_URL, json={"archetype": "weak_flexible"})
        assert r.status_code == 200, r.text
        assert reached.wait(10)
        assert client.get(STUDY_URL).json()["status"] == "running"
        assert client.get(STUDY_URL).json().get("edited_since_study") is None
        _edit_bus(client)
    finally:
        gate.set()
    deadline = time.time() + 30
    while client.get(STUDY_URL).json().get("status") == "running":
        assert time.time() < deadline
        time.sleep(0.05)
    study, review = _both(client)
    assert study["status"] == "done"
    assert study["edited_since_study"] is True
    assert review["edited_since_study"] is True


def test_a_record_without_a_revision_reads_unavailable(
        client, install_network, monkeypatch, session_state, session_ctx):
    _done_study(client, install_network, monkeypatch, session_state, "unavailable")
    rec = session_state(client)["eh_study"]
    rec.pop("network_revision", None)
    study, review = _both(client)
    assert study["edited_since_study"] is None
    assert review["edited_since_study"] is None
    assert review == _tool(session_ctx(client))


def test_edited_since_is_null_for_running_and_non_dict_records():
    from services.study_state import edited_since
    assert edited_since(None, revision=3) is None
    assert edited_since("x", revision=3) is None
    assert edited_since({"status": "running", "network_revision": 3}, revision=3) is None
    assert edited_since({"status": "done"}, revision=3) is None
    assert edited_since({"status": "done", "network_revision": 3}, revision=3) is False
    assert edited_since({"status": "done", "network_revision": 2}, revision=3) is True
    assert edited_since({"status": "failed", "network_revision": 2}, revision=3) is True


def test_the_revision_survives_save_and_reload(
        client, install_network, monkeypatch, session_state, project_storage_dir,
        api_project):
    import json

    other = api_project("rev-other")
    _done_study(client, install_network, monkeypatch, session_state, "rev-persist")
    _edit_bus(client)
    rev = _rev(client)
    r = client.post("/api/projects/rev-persist")
    assert r.status_code == 200, r.text
    meta = json.loads((project_storage_dir("rev-persist") / "metadata.json").read_text())
    assert meta["network_revision"] == rev
    assert client.get(f"/api/projects/{other}").status_code == 200
    assert client.get("/api/projects/rev-persist").status_code == 200
    assert _rev(client) == rev
    assert client.get(STUDY_URL).json()["edited_since_study"] is True


# ── Gate S-2: three rules the first gate found unpinned ───────────────────

def _study_then_save(client, install_network, monkeypatch, session_state, name, edits_before=2):
    from tests.test_eh_study_record_survives_reactivation import run_study
    fake_done(monkeypatch)
    hub_project(client, install_network, name)
    for _ in range(edits_before):
        _edit_bus(client)
    rec = run_study(client)
    t = (session_state(client).get("eh_study") or {}).get("thread")
    if t is not None:
        t.join(5)
    assert client.post(f"/api/projects/{name}").status_code == 200
    return rec


def test_load_and_import_restore_the_projects_own_counter(
        client, install_network, monkeypatch, session_state, api_project):
    """Q4a: switching to ANOTHER project must compare its record against ITS
    saved counter — not against the counter of the project being left, nor 0."""
    rec = _study_then_save(client, install_network, monkeypatch, session_state, "own-b")
    saved = rec["network_revision"]
    assert saved >= 2
    api_project("own-a")
    for _ in range(5):
        _edit_bus(client)
    assert _rev(client) != saved

    assert client.get("/api/projects/own-b").status_code == 200
    assert _rev(client) == saved
    assert client.get(STUDY_URL).json()["edited_since_study"] is False

    bundle = client.get("/api/projects/own-b/bundle").content
    assert client.get("/api/projects/own-a").status_code == 200
    r = client.post("/api/projects/import_bundle",
                    files={"file": ("b.zip", bundle, "application/zip")},
                    params={"name": "own-b-imported"})
    assert r.status_code == 200, r.text
    assert _rev(client) == saved
    assert client.get(STUDY_URL).json()["edited_since_study"] is False


def test_a_counter_below_the_captured_one_reads_edited(
        client, install_network, monkeypatch, session_state, registry_key_for,
        project_storage_dir):
    """Q3a: `!=`, not `>`. A metadata.json without the counter (an older save, a
    hand copy) hydrates to 0 beside a record captured at N > 0 — the network on
    disk is not known to be the studied one, so it must read edited."""
    import json
    rec = _study_then_save(client, install_network, monkeypatch, session_state, "below")
    assert rec["network_revision"] > 0
    meta_path = project_storage_dir("below") / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.pop("network_revision")
    meta_path.write_text(json.dumps(meta))
    from services.pypsa_service import PyPSAService
    with PyPSAService._registry_lock:
        PyPSAService._contexts.pop(registry_key_for("below"), None)
    assert client.post("/api/projects/below/activate").status_code == 200
    assert _rev(client) == 0
    assert client.get(STUDY_URL).json()["edited_since_study"] is True


def test_an_io_import_is_an_edit(client, install_network, tmp_path):
    """Q2a: the HTTP seam covers `/api/io/` as well as `/api/network/`."""
    import pypsa
    hub_project(client, install_network, "io-edit")
    assert client.post("/api/network/reset").status_code == 200   # a draft (OPEN-ITEMS 12)
    r0 = _rev(client)
    n = pypsa.Network()
    n.add("Bus", "imported")
    path = tmp_path / "in.nc"
    n.export_to_netcdf(path)
    r = client.post("/api/io/import/netcdf",
                    files={"file": ("in.nc", path.read_bytes(), "application/x-netcdf")})
    assert r.status_code == 200, r.text
    assert [b["name"] for b in client.get("/api/network/buses").json()] == ["imported"]
    assert _rev(client) == r0 + 1
