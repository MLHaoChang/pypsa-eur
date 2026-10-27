"""Old → new edit cards for the Library, map-import and weather tools (Q3-CARDS-1, group "library")."""
from __future__ import annotations

import copy
import json

import pytest
from fastapi import HTTPException

import harness.edit_card_library as cards  # noqa: F401 — registers the adapters
from harness import edit_card
from services import chat_service, chat_tools, library_acl, upload_service
from services.pypsa_service import PyPSAService
from tests.study_s4_support import dir_hash
from tests.test_assistant_b2a_tools import TARIFF, _csv
from tests.test_map_geometry_import import A_NEAR, B_NEAR, BEND, SQUARE, fc, line, polygon

TOOLS = ("put_library_item", "import_library_series", "import_meter_data", "import_map_geometry", "set_weather_choice")


def _dispatch(session, name, args, decision="approve"):
    frames, collector, shown = [], [], []
    gen = chat_service._dispatch_real_tool_call(session, {"id": "tu-1", "name": name, "input": args}, collector)
    for event, data in gen:
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            shown.append(data)
            session.record_decision(data["confirmation_token"], decision)
    result = None
    if collector and not collector[-1].get("is_error"):
        content = collector[-1]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        result, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    return frames, shown, result, collector


def _cells(card):
    return [(r["field"], r["old"], r["new"]) for r in card["rows"] if r["role"] != "note"]


def _refusal(tool, args):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview(tool, args)
    return exc.value


@pytest.fixture
def bound(client, session_ctx, api_project):
    """A real project, open in the chat tools' session, with a chat session to dispatch in."""
    name = api_project("lib-card")
    token = PyPSAService.bind_request_context(session_ctx(client))
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    try:
        yield name, session
    finally:
        chat_tools.set_chat_session(None)
        PyPSAService.reset_request_context(token)


def _upload(name, data: bytes, filename="s.csv"):
    return upload_service.add_upload(name, data, filename, "text/csv").file_id


def test_each_tool_has_an_adapter():
    for tool in TOOLS:
        assert tool in edit_card.ADAPTERS and edit_card.ADAPTERS[tool].kind == "settings_edit", tool


# ── set_weather_choice ──────────────────────────────────────────────────────

def test_weather_choice_shows_source_and_year_old_to_new_and_verifies(bound):
    name, session = bound
    args = {"project_id": name, "source": "atlite", "year": 2020}
    _, shown, result, _ = _dispatch(session, "set_weather_choice", args)
    card = shown[0]["edit_card"]
    assert card["complete"] and card["kind"] == "settings_edit"
    assert _cells(card) == [("source", None, "atlite"), ("year", None, 2020)]
    assert [r["label"] for r in card["rows"]] == ["Weather for generated profiles · Source",
                                                  "Weather for generated profiles · Year"]
    assert card["rows"][0]["words"] == {"old": "not set", "new": "ERA5"}
    assert result["_edit_receipt"]["outcome"] == "verified"
    # A year change alone still names the source it keeps (old = new).
    again = edit_card.preview("set_weather_choice", {**args, "year": 2019})
    assert _cells(again) == [("source", "atlite", "atlite"), ("year", 2020, 2019)]
    assert again["rows"][0]["words"] == {"old": "ERA5", "new": "ERA5"}
    assert _refusal("set_weather_choice", args).detail["error_kind"] == "empty_edit"


def test_weather_choice_refuses_what_the_handler_refuses(bound):
    name, _ = bound
    assert _refusal("set_weather_choice", {"project_id": name, "source": "atlite", "year": 1800}
                    ).detail["error_kind"] == "weather_choice"
    assert _refusal("set_weather_choice", {"project_id": name, "source": "moon", "year": 2020}
                    ).detail["error_kind"] == "invalid_edit_value"


def test_weather_choice_preview_writes_nothing_and_an_outsider_is_refused(bound, project_storage_dir, second_identity,
                                                                           seeded_identity):
    name, _ = bound
    before = dir_hash(project_storage_dir(name))
    edit_card.preview("set_weather_choice", {"project_id": name, "source": "synthetic", "year": 2021})
    assert dir_hash(project_storage_dir(name)) == before
    chat_tools.set_acting_user(second_identity["user_id"])
    try:
        assert _refusal("set_weather_choice", {"project_id": name, "source": "synthetic", "year": 2021}
                        ).status_code in (403, 404)
    finally:
        chat_tools.set_acting_user(str(seeded_identity["user_id"]))


# ── put_library_item ────────────────────────────────────────────────────────

def _tariff(**over):
    body = copy.deepcopy(TARIFF)
    body.update(over)
    return body


def test_put_library_item_new_then_changed_leaf_then_unchanged(bound):
    _, session = bound
    args = {"kind": "tariff", "name": "card-tariff", "payload": TARIFF, "meta": {"description": "first"}}
    _, shown, result, _ = _dispatch(session, "put_library_item", args)
    card = shown[0]["edit_card"]
    cells = {f: (o, n) for f, o, n in _cells(card)}
    assert cells["version"] == (None, "v1")
    assert cells["payload.jurisdiction"] == (None, "DE")
    assert cells["payload.items"] == (None, "1 items")  # a list is summarised, never dumped
    assert cells["meta.description"] == (None, "first")
    assert result["_edit_receipt"]["outcome"] == "verified"
    changed = {**args, "payload": _tariff(jurisdiction="FR"), "meta": {"description": "second"}}
    card = edit_card.preview("put_library_item", changed)
    cells = {f: (o, n) for f, o, n in _cells(card)}
    assert cells["version"] == ("v1", "v2")
    assert cells["payload.jurisdiction"] == ("DE", "FR")
    assert cells["meta.description"] == ("first", "second")
    assert not any(f.startswith("payload.") and f != "payload.jurisdiction" for f in cells)  # only what differs
    assert "Tariff 'card-tariff' · Jurisdiction" in card["says"]
    _, _, result, _ = _dispatch(session, "put_library_item", changed)
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert _refusal("put_library_item", changed).detail["error_kind"] == "empty_edit"


def test_put_library_item_refuses_what_the_route_refuses(bound):
    bad = {"kind": "tariff", "name": "x", "payload": {"nonsense": 1}}
    assert _refusal("put_library_item", bad).detail["error_kind"] == "invalid_edit_value"
    assert _refusal("put_library_item", {**bad, "kind": "widget"}).detail["error_kind"] == "unknown_library_kind"
    assert _refusal("put_library_item", {**bad, "payload": TARIFF, "name": "a/b"}).detail["error_kind"] == "invalid_edit_value"


def test_put_library_item_without_write_is_refused_before_any_old_version(bound, monkeypatch):
    from services.library import items as I

    monkeypatch.setattr(library_acl, "can_write", lambda db, user, org: False)
    monkeypatch.setattr(I, "latest_ref", lambda *a, **k: pytest.fail("an old value was read before the access check"))
    assert _refusal("put_library_item", {"kind": "tariff", "name": "t", "payload": TARIFF}).status_code == 403


def test_library_previews_write_nothing(bound, monkeypatch):
    from services.library import items as I
    from services.library import series_store as S

    name, _ = bound
    fid = _upload(name, _csv(n=24, freq="h"))
    monkeypatch.setattr(I, "_write", lambda *a, **k: pytest.fail("a preview wrote an item"))
    monkeypatch.setattr(S, "_write_payload", lambda *a, **k: pytest.fail("a preview wrote a series"))
    monkeypatch.setattr(I, "put_item", lambda *a, **k: pytest.fail("a preview stored an item"))
    monkeypatch.setattr(S, "put_series", lambda *a, **k: pytest.fail("a preview stored a series"))
    edit_card.preview("put_library_item", {"kind": "tariff", "name": "nw", "payload": TARIFF})
    edit_card.preview("import_library_series", {"file_id": fid, "name": "nw"})
    edit_card.preview("import_meter_data", {"file_id": fid, "name": "nw-m", "unit": "kW"})


def test_secret_like_keys_only_say_set():
    row = cards._row("Library tariff", "meta.api_token", "t", "old-secret", "new-secret")
    assert (row["old"], row["new"]) == ("set", "set (changed)")
    assert cards._row("x", "meta.api_token", "t", None, "s")["old"] is None
    assert cards._settle(row, "set", "new-secret") == "set (changed)"
    assert cards._settle(row, "set", "old-secret") == "set"  # the same cell on another value is not the new one
    listed = cards._row("x", "payload.items", "t", [1, 2], [3, 4])
    assert (listed["old"], listed["new"]) == ("2 items", "2 items (changed)")


def test_rows_beyond_the_cap_become_one_summary_note():
    rows = cards._capped([{"role": "typed"}] * (edit_card.STUDY_LEAF_CAP + 3), "fields")
    assert len(rows) == edit_card.STUDY_LEAF_CAP + 1
    assert rows[-1]["role"] == "note" and rows[-1]["note"] == "3 more fields change."


# ── import_library_series / import_meter_data ───────────────────────────────

def test_import_series_new_then_replaced_then_unchanged(bound):
    name, session = bound
    first = _upload(name, _csv(n=24, freq="h"))
    _, shown, result, _ = _dispatch(session, "import_library_series", {"file_id": first, "name": "card-series"})
    cells = {f: (o, n) for f, o, n in _cells(shown[0]["edit_card"])}
    assert cells["version"] == (None, "v1") and cells["points"] == (None, 24)
    assert cells["first"] == (None, "2030-01-01T00:00:00") and cells["timezone"] == (None, "no zone")
    assert result["_edit_receipt"]["outcome"] == "verified"
    longer = _upload(name, _csv(n=48, freq="h"), "longer.csv")
    _, shown, result, _ = _dispatch(session, "import_library_series", {"file_id": longer, "name": "card-series"})
    cells = {f: (o, n) for f, o, n in _cells(shown[0]["edit_card"])}
    assert cells["version"] == ("v1", "v2") and cells["points"] == (24, 48)
    assert cells["last"] == ("2030-01-01T23:00:00", "2030-01-02T23:00:00")
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert _refusal("import_library_series", {"file_id": longer, "name": "card-series"}).detail["error_kind"] == "empty_edit"


def test_import_series_refuses_what_the_route_refuses(bound, monkeypatch):
    import routers.library as L

    name, _ = bound
    bad = _upload(name, b"when,price\n2030-01-01,1\n")
    refused = _refusal("import_library_series", {"file_id": bad, "name": "x"})
    assert refused.status_code == 422 and "timestamp,value" in str(refused.detail)
    ok = _upload(name, _csv(n=24, freq="h"))
    assert _refusal("import_library_series", {"file_id": ok, "name": "x", "timezone": "Europe/Berlin"}).status_code == 422
    real = L.read_capped
    monkeypatch.setattr(L, "read_capped", lambda f: real(f, max_bytes=16))
    assert _refusal("import_library_series", {"file_id": ok, "name": "x"}).status_code == 413


def test_import_series_without_write_is_refused_before_the_file_or_old_version(bound, monkeypatch):
    import routers.library as L
    from services.library import series_store as S

    name, _ = bound
    ok = _upload(name, _csv(n=24, freq="h"))
    monkeypatch.setattr(library_acl, "can_write", lambda db, user, org: False)
    monkeypatch.setattr(S, "latest_ref", lambda *a, **k: pytest.fail("an old value was read before the access check"))
    monkeypatch.setattr(L, "_parsed", lambda *a, **k: pytest.fail("the file was parsed before the access check"))
    for tool, extra in (("import_library_series", {}), ("import_meter_data", {"unit": "kW"})):
        assert _refusal(tool, {"file_id": ok, "name": "x", **extra}).status_code == 403


def test_an_upload_of_another_project_is_refused(bound):
    _, session = bound
    assert _refusal("import_library_series", {"file_id": "0" * 64, "name": "x"}).status_code == 404


def test_import_meter_data_shows_the_series_and_how_the_file_is_read(bound):
    name, session = bound
    fid = _upload(name, _csv(n=31 * 96, value=lambda i: 2.5))
    args = {"file_id": fid, "name": "card-meter", "unit": "kWh_per_interval", "timezone": "Europe/Berlin"}
    _, shown, result, _ = _dispatch(session, "import_meter_data", args)
    cells = {f: (o, n) for f, o, n in _cells(shown[0]["edit_card"])}
    assert cells["version"] == (None, "v1") and cells["points"] == (None, 31 * 96)
    assert cells["source_unit"] == (None, "kWh_per_interval") and cells["settlement"] == (None, "15min")
    assert cells["label"] == (None, "start") and cells["timezone"] == (None, "Europe/Berlin")
    assert "_edit_receipt" in result["spill"]["shape"]  # the meter answer is large: its receipt rides with it
    assert edit_card.receipt(shown[0]["edit_card"], args)["outcome"] == "verified"
    assert _refusal("import_meter_data", args).detail["error_kind"] == "empty_edit"
    other = _upload(name, _csv(n=31 * 96, value=lambda i: 2.5), "m2.csv")
    clash = _refusal("import_meter_data", {**args, "file_id": other, "settlement": "30min"})
    assert clash.detail["error_kind"] == "meter_meta_conflict" and clash.status_code == 409
    refused = _refusal("import_meter_data", {**args, "unit": "MWh", "name": "m3"})
    assert refused.status_code == 422


# ── import_map_geometry ─────────────────────────────────────────────────────

@pytest.fixture
def geo(client, session_ctx, project_storage_dir, api_project):
    from tests.test_map_geometry_import import A, B, _bus

    name = api_project("lib-card-geo")
    _bus(client, "A", A)
    _bus(client, "B", B)
    assert client.post("/api/network/lines", json={"name": "L1", "bus0": "A", "bus1": "B", "length": 1.0,
                                                    "r": 0.3, "x": 1.7, "b": 1e-5, "s_nom": 100.0}).status_code == 201
    token = PyPSAService.bind_request_context(session_ctx(client))
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    try:
        yield name, session, project_storage_dir(name)
    finally:
        chat_tools.set_chat_session(None)
        PyPSAService.reset_request_context(token)


def _geo_file(name, *features):
    return upload_service.add_upload(name, json.dumps(fc(*features)).encode(), "g.geojson", "text/plain").file_id


def test_map_import_apply_shows_routes_sites_and_lengths_old_to_new(geo):
    name, session, pdir = geo
    chat_tools.update_project_settings(name, True)  # derive lengths from geometry
    fid = _geo_file(name, line([A_NEAR, BEND, B_NEAR], name="L1"), polygon(SQUARE, name="Plot"))
    args = {"name": name, "file_id": fid, "apply": True}
    before = dir_hash(pdir)
    _, shown, result, _ = _dispatch(session, "import_map_geometry", args)
    card = shown[0]["edit_card"]
    cells = {f: (o, n) for f, o, n in _cells(card)}
    assert cells["route:line:L1"] == ("straight line", "1 bend points")
    site = next(v for k, v in cells.items() if k.startswith("site:"))
    assert site[0] is None and site[1].startswith("Plot · 4 vertices")
    length = next(r for r in card["rows"] if r["field"] == "length")
    assert (length["component_class"], length["name"], length["old"], length["unit"]) == ("Line", "L1", 1.0, "km")
    assert length["new"] != 1.0
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert dir_hash(pdir) != before
    again = _geo_file(name, line([A_NEAR, BEND, B_NEAR], name="L1"))
    assert _refusal("import_map_geometry", {"name": name, "file_id": again, "apply": True}
                    ).detail["error_kind"] == "empty_edit"


def test_map_import_without_apply_is_a_note_and_writes_nothing(geo):
    name, _, pdir = geo
    fid = _geo_file(name, line([A_NEAR, BEND, B_NEAR], name="L1"), polygon(SQUARE, name="Plot"))
    before = dir_hash(pdir)
    lengths = PyPSAService.get_network().lines["length"].copy()
    card = edit_card.preview("import_map_geometry", {"name": name, "file_id": fid})
    assert [r["role"] for r in card["rows"]] == ["note"] and "Nothing is written" in card["rows"][0]["note"]
    edit_card.preview("import_map_geometry", {"name": name, "file_id": fid, "apply": True})
    assert dir_hash(pdir) == before
    assert PyPSAService.get_network().lines["length"].equals(lengths)


def test_map_import_refuses_what_the_route_refuses_and_an_outsider(geo, second_identity, seeded_identity):
    name, _, _ = geo
    fid = _geo_file(name, line([A_NEAR, BEND, B_NEAR], name="L1"))
    assert _refusal("import_map_geometry", {"name": name, "file_id": fid, "tolerance_m": 0}).status_code == 422
    bad = upload_service.add_upload(name, b"{not json", "bad.geojson", "text/plain").file_id
    assert _refusal("import_map_geometry", {"name": name, "file_id": bad}).status_code == 422
    assert _refusal("import_map_geometry", {"name": name, "file_id": "0" * 64}).status_code == 404
    chat_tools.set_acting_user(second_identity["user_id"])
    try:
        assert _refusal("import_map_geometry", {"name": name, "file_id": fid, "apply": True}
                        ).status_code in (403, 404)
    finally:
        chat_tools.set_acting_user(str(seeded_identity["user_id"]))


# ── a "(changed)" row verifies only when the store holds the new value ───────

def _outcome(tool, args):
    """The receipt of a fresh card for `args`, read straight from the store (nothing is run)."""
    card = edit_card.preview(tool, args)
    return card, edit_card.receipt(card, args)["outcome"]


def test_put_library_item_changed_rows_are_uncertain_until_the_store_holds_the_new_value(bound):
    _, session = bound
    first = {"kind": "tariff", "name": "fp-tariff", "payload": TARIFF, "meta": {"description": "a" * 130 + "A"}}
    _dispatch(session, "put_library_item", first)
    changed = copy.deepcopy(TARIFF)
    changed["items"][0]["periods"][0]["rate"] = 0.21  # an edit inside the list keeps its count
    args = {**first, "payload": changed, "meta": {"description": "a" * 130 + "B"}}
    card, outcome = _outcome("put_library_item", args)
    cells = {r["field"]: r for r in card["rows"]}
    assert cells["payload.items"]["old"] == "1 items" and cells["payload.items"]["new"] == "1 items (changed)"
    assert cells["meta.description"]["new"].endswith(" (changed)") and cells["meta.description"]["fingerprint"]
    assert len(cells["meta.description"]["old"]) <= 122
    assert outcome == "uncertain", "the store still holds the old item"
    _, _, result, _ = _dispatch(session, "put_library_item", args)
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert edit_card.receipt(card, args)["outcome"] == "verified"


def test_a_secret_rotation_reads_back_only_when_stored():
    row = cards._row("Library tariff", "meta.api_token", "t", "old-secret", "new-secret")
    assert "old-secret" not in json.dumps(row) and "new-secret" not in json.dumps(row)
    assert cards._settle(row, "set", "old-secret") == "set"
    assert cards._settle(row, "set", "new-secret") == row["new"]


def test_map_import_route_with_the_same_bend_count_verifies_only_once_written(geo):
    name, session, _ = geo
    old = [[6.3, 53.2], [6.5, 53.5], [6.7, 53.2]]
    new = [[6.3, 53.3], [6.5, 53.6], [6.7, 53.3]]
    for points in (old, new):
        fid = _geo_file(name, line([A_NEAR, *points, B_NEAR], name="L1"))
        args = {"name": name, "file_id": fid, "apply": True}
        card, outcome = _outcome("import_map_geometry", args)
        row = next(r for r in card["rows"] if r["field"] == "route:line:L1")
        if points is old:
            assert (row["old"], row["new"]) == ("straight line", "3 bend points")
        else:
            assert (row["old"], row["new"]) == ("3 bend points", "3 bend points (changed)")
        assert outcome == "uncertain", "the layout does not hold these points yet"
        _, _, result, _ = _dispatch(session, "import_map_geometry", args)
        assert result["_edit_receipt"]["outcome"] == "verified"
        assert edit_card.receipt(card, args)["outcome"] == "verified"


def test_map_import_site_receipt_needs_the_site_the_card_computed(geo):
    from services import site_service

    name, session, pdir = geo
    # A stored site of the same name, vertex count and bus count, but another boundary.
    other = [[5.98, 52.98], [6.02, 52.98], [6.02, 53.02], [5.98, 53.02]]
    site_service.write_sites(pdir, {"version": 1, "sites": [{
        "id": "pre", "name": "Plot", "buses": ["A"], "boundary": other,
        "origin": {"lng": 6.0, "lat": 53.0}, "placements": {}}]})
    fid = _geo_file(name, polygon(SQUARE, name="Plot"))
    args = {"name": name, "file_id": fid, "apply": True}
    card, outcome = _outcome("import_map_geometry", args)
    (row,) = [r for r in card["rows"] if r["field"].startswith("site:")]
    assert row["new"].startswith("Plot · 4 vertices · 1 buses") and row["prior"] == 0
    assert outcome == "uncertain", "a stored site with the same name and counts is not this site"
    _, _, result, _ = _dispatch(session, "import_map_geometry", args)
    assert result["_edit_receipt"]["outcome"] == "verified"
    # The same file again adds an identical second site; with the first one stored, the unwritten
    # store must not pass for the written one.
    again = {"name": name, "file_id": _geo_file(name, polygon(SQUARE, name="Plot")), "apply": True}
    card, outcome = _outcome("import_map_geometry", again)
    (row,) = [r for r in card["rows"] if r["field"].startswith("site:")]
    assert row["prior"] == 1 and outcome == "uncertain"
    _, _, result, _ = _dispatch(session, "import_map_geometry", again)
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert len(site_service.read_sites(pdir)["sites"]) == 3


def test_apply_leaves_the_first_document_unwritten_when_the_second_is_bad(tmp_path):
    from services import map_geometry_import as mgi, map_layout_service as ml, site_service as ss

    ml.write_map_layout(tmp_path, {"version": 1, "routes": {}, "bubbles": {}})
    layout_before = (tmp_path / "map_layout.json").read_bytes()
    bad_site = {"id": "s1", "name": "Plot", "buses": ["A"], "boundary": SQUARE[:2],  # two vertices: not a boundary
                "origin": {"lng": 6.0, "lat": 53.0}}
    report = {"routes": [{"key": "line:L1", "points": [BEND]}], "sites": [bad_site]}
    with pytest.raises(ss.SitesInvalid):
        mgi.plan_apply(tmp_path, report)
    with pytest.raises(ss.SitesInvalid):
        mgi.apply_report(tmp_path, report)
    assert (tmp_path / "map_layout.json").read_bytes() == layout_before
    assert not (tmp_path / "sites.json").exists()
