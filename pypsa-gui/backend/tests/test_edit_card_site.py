"""Old → new edit cards for the site, commercial and finance tools (Q3-CARDS-1, slice 2, group `site`)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from harness import edit_card, edit_card_site  # noqa: F401  (the adapters register on import)
from services import chat_service, chat_tools
from tests.fixtures.investment_case.edge_15min import build_edge_15min
from tests.test_edit_card import _dispatch

ORACLES = Path(__file__).parent / "fixtures" / "investment_case" / "oracles"
TARIFF = {"id": "nl-tou", "name": "NL TOU", "jurisdiction": "NL", "valid_from": "2030-01-01",
          "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
                                 {"name": "day", "rate": 0.20}]}]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 20.0,
       "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"]}


def _cells(card):
    return [(r["field"], r["old"], r["new"]) for r in card["rows"] if r["role"] == "typed"]


def _refusal(tool, args, status=None):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview(tool, args)
    if status is not None:
        assert exc.value.status_code == status
    return exc.value.detail


def _commercial():
    from routers.simulation import get_solver_config

    return (get_solver_config() or {}).get("commercial")


def _edge_net():
    n = build_edge_15min()
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    return n


@pytest.fixture
def session():
    chat = chat_service.ChatSession()
    chat_tools.set_chat_session(chat)
    yield chat
    chat_tools.set_chat_session(None)


@pytest.fixture
def edge(client, install_network, session):
    """An edge network with a one-way export Link, no commercial config; the state is reset after."""
    import routers.simulation as sim

    install_network(_edge_net(), name="card_site")
    yield session
    sim.put_finance(sim.FinanceIn(finance=None), if_match=None)
    chat_tools.update_solver_config({"commercial": None})


@pytest.fixture
def no_user():
    previous = chat_tools.acting_user_id()
    chat_tools.set_acting_user(None)
    yield
    chat_tools.set_acting_user(previous)


# ── set_site_connection ─────────────────────────────────────────────────────

def test_site_connection_not_set_then_old_to_new_with_a_verified_receipt(edge):
    before = _commercial()
    card = edit_card.preview("set_site_connection", {"poc_link": "import"})
    assert card["kind"] == "settings_edit" and card["complete"] is True
    assert _cells(card) == [("poc_link", None, "import")]
    assert card["rows"][0]["label"] == "Point of connection"
    assert _commercial() == before, "a preview writes nothing"
    frames, cards, result = _dispatch(edge, "set_site_connection", {"poc_link": "import"})
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert _commercial()["poc_link"] == "import"


def test_site_connection_shows_a_named_unchanged_value_and_refuses_a_no_op(edge):
    chat_tools.set_site_connection("import", timezone="Europe/Berlin")
    card = edit_card.preview("set_site_connection",
                             {"poc_link": "import", "export_link": "export", "timezone": "Europe/Berlin"})
    assert _cells(card) == [("poc_link", "import", "import"), ("export_link", None, "export"),
                            ("timezone", "Europe/Berlin", "Europe/Berlin")]
    assert [r["label"] for r in card["rows"]] == ["Point of connection", "Export link", "Site time zone"]
    # An omitted export_link keeps the stored one and is not shown; an explicit null clears it.
    chat_tools.set_site_connection("import", export_link="export")
    cleared = edit_card.preview("set_site_connection", {"poc_link": "import", "export_link": None})
    assert _cells(cleared) == [("poc_link", "import", "import"), ("export_link", "export", None)]
    assert "export_link" not in [r["field"] for r in edit_card.preview(
        "set_site_connection", {"poc_link": "import", "timezone": "UTC"})["rows"]]
    assert _refusal("set_site_connection", {"poc_link": "import", "export_link": "export"})["error_kind"] == "empty_edit"


def test_site_connection_refuses_what_the_handler_refuses(edge):
    assert _refusal("set_site_connection", {"poc_link": "ghost"}, 422)["error_kind"] == "site_connection_link_missing"
    assert _refusal("set_site_connection", {"poc_link": "import", "export_link": "import"},
                    422)["error_kind"] == "site_connection_invalid"
    assert _refusal("set_site_connection", {"poc_link": "export"}, 422)["error_kind"] == "site_connection_wrong_direction"
    assert _refusal("set_site_connection", {"poc_link": 3}, 422)["error_kind"] == "invalid_edit_value"


def test_site_connection_without_a_user_is_refused_first(edge, no_user):
    assert _refusal("set_site_connection", {"poc_link": "import"}, 401)["error_kind"] == "no_acting_user"


# ── attach_tariff ───────────────────────────────────────────────────────────

def _library_tariff(client, name="nl", payload=TARIFF):
    r = client.put(f"/api/library/items/tariff/{name}", json={"payload": payload, "meta": {"source": "pytest"}})
    assert r.status_code == 200, r.text
    return r.json()


def test_attach_tariff_shows_the_inline_tariff_then_the_pinned_item(edge, client, install_network):
    ref = _library_tariff(client)
    install_network(_edge_net(), name="card_site")  # the chat's active network, after the PUT
    assert _refusal("attach_tariff", {"name": "nl"}, 409)["error_kind"] == "no_commercial_config"
    chat_tools.update_solver_config({"commercial": {"poc_link": "import", "import_tariff": {**TARIFF, "id": "inline"}}})
    assert _refusal("attach_tariff", {"name": "nl"}, 409)["error_kind"] == "inline_tariff_would_be_replaced"
    before = _commercial()
    card = edit_card.preview("attach_tariff", {"name": "nl", "replace_inline": True})
    assert _cells(card) == [("import_tariff_ref", "NL TOU (inline)", "nl v1")]
    assert card["rows"][0]["label"] == "Import tariff"
    assert _commercial() == before, "a preview writes nothing"
    frames, cards, result = _dispatch(edge, "attach_tariff", {"name": "nl", "replace_inline": True})
    assert result["_edit_receipt"]["outcome"] == "verified" and result["import_tariff_ref"] == ref
    # The same item again is nothing to change; a newer version is old → new.
    assert _refusal("attach_tariff", {"name": "nl"})["error_kind"] == "empty_edit"
    _library_tariff(client, payload={**TARIFF, "valid_from": "2031-01-01"})
    install_network(_edge_net(), name="card_site")
    chat_tools.update_solver_config({"commercial": {"poc_link": "import", "import_tariff_ref": ref}})
    assert _cells(edit_card.preview("attach_tariff", {"name": "nl"})) == [("import_tariff_ref", "nl v1", "nl v2")]
    assert _refusal("attach_tariff", {"name": "nl", "version": 9}, 404)


def test_attach_tariff_without_a_user_is_refused_first(edge, no_user):
    assert _refusal("attach_tariff", {"name": "nl"}, 401)["error_kind"] == "no_acting_user"


# ── import_urdb_tariff ──────────────────────────────────────────────────────

@pytest.fixture
def urdb_project(client, install_network, tmp_projects_dir, session):
    install_network(build_edge_15min(), name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")
    return session


def _upload(data: bytes, name="rate.json"):
    from services import upload_service

    return upload_service.add_upload("P", data, name, "application/json").file_id


def _library_names():
    return chat_tools.list_library_items("tariff")["items"]


def test_urdb_import_shows_what_it_creates_then_what_it_replaces(urdb_project):
    file_id = _upload((ORACLES / "r1_leap_year.reopt.json").read_bytes())
    args = {"file_id": file_id, "name": "r1", "valid_from": "2023-01-01"}
    listed = _library_names()
    card = edit_card.preview("import_urdb_tariff", args)
    by_field = {r["field"]: r for r in card["rows"]}
    first = by_field["tariff.r1"]
    assert (first["old"], first["new"], first["label"]) == (None, "v1", "Library tariff · r1")
    assert first["words"] == {"old": "not in the Library", "new": "v1 from the upload"}
    assert by_field["r1.items"]["old"] is None and by_field["r1.items"]["new"] == 4
    assert _library_names() == listed, "a preview writes nothing"
    frames, cards, result = _dispatch(urdb_project, "import_urdb_tariff", args)
    assert result["_edit_receipt"]["outcome"] == "verified"
    # The same rate again is already the Library's latest version.
    assert _refusal("import_urdb_tariff", args)["error_kind"] == "empty_edit"
    # A different rate under the name is the next version, and the items row shows old → new.
    rate = json.loads((ORACLES / "r2_tiered_tou_demand.urdb.json").read_text())
    other = _upload(json.dumps({"items": [rate]}).encode())
    again = edit_card.preview("import_urdb_tariff", {"file_id": other, "name": "r1", "valid_from": "2023-01-01",
                                                      "accept_partial": True})
    rows = {r["field"]: r for r in again["rows"]}
    assert (rows["tariff.r1"]["old"], rows["tariff.r1"]["new"]) == ("v1", "v2")
    assert rows["r1.items"]["old"] == 4


def test_urdb_import_refuses_what_the_handler_refuses(urdb_project):
    rate = json.loads((ORACLES / "r2_tiered_tou_demand.urdb.json").read_text())
    bad = _upload(json.dumps({"items": [{**rate, "mincharge": 5}]}).encode())
    assert _refusal("import_urdb_tariff", {"file_id": bad, "name": "x", "valid_from": "2017-01-01"},
                    422)["error_kind"] == "urdb_refused"
    partial = edit_card.preview("import_urdb_tariff", {"file_id": bad, "name": "x", "valid_from": "2017-01-01",
                                                       "accept_partial": True})
    assert [r["new"] for r in partial["rows"] if r["field"] == "x.unsupported_fields"] == ["1 item"]
    assert _refusal("import_urdb_tariff", {"file_id": _upload(b"not json"), "name": "x"},
                    422)["error_kind"] == "urdb_upload_unreadable"


def test_urdb_import_without_a_user_is_refused_before_the_file_is_read(urdb_project, no_user):
    assert _refusal("import_urdb_tariff", {"file_id": "nope", "name": "x"}, 401)["error_kind"] == "no_acting_user"


# ── set_finance_inputs ──────────────────────────────────────────────────────

def _finance():
    from tests.fixtures.investment_case.sam import sam_case as S

    return S.to_finance_case("s2").inputs.model_dump(mode="json")


def _digest():
    return chat_tools.get_finance_inputs()["digest"]


def test_finance_first_save_shows_the_named_fields_capped_and_verifies(edge):
    digest = _digest()
    finance = _finance()
    card = edit_card.preview("set_finance_inputs", {"finance": finance, "if_match": digest})
    typed = [r for r in card["rows"] if r["role"] == "typed"]
    assert len(typed) == edit_card.STUDY_LEAF_CAP and all(r["old"] is None for r in typed)
    assert [r["role"] for r in card["rows"]].count("note") == 1
    assert chat_tools.get_finance_inputs()["finance"] is None, "a preview writes nothing"
    # (the whole case echoes back past the chat's result cap, so the receipt is checked on a short one)
    small = {"currency": "EUR", "financial_close": "2030-01-01", "analysis_years": 20}
    frames, cards, result = _dispatch(edge, "set_finance_inputs", {"finance": small, "if_match": digest})
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert [r["old"] for r in cards[0]["edit_card"]["rows"]] == [None, None, None]


def test_finance_replacement_shows_only_what_differs_in_the_panels_words(edge):
    chat_tools.set_finance_inputs(_digest(), _finance())
    stored = chat_tools.get_finance_inputs()["finance"]
    changed = {**stored, "wacc_nominal": 0.0912, "escalation": {**stored["escalation"], "capex": 0.031}}
    args = {"finance": changed, "if_match": _digest()}
    card = edit_card.preview("set_finance_inputs", args)
    assert _cells(card) == [("escalation.capex", stored["escalation"].get("capex"), 0.031),
                            ("wacc_nominal", stored.get("wacc_nominal"), 0.0912)]
    assert [r["label"] for r in card["rows"]] == ["Escalation · capex", "WACC"]
    digest_before = _digest()
    frames, cards, result = _dispatch(edge, "set_finance_inputs", args)
    assert result["_edit_receipt"]["outcome"] == "verified" and _digest() != digest_before
    assert _refusal("set_finance_inputs", {"finance": changed, "if_match": _digest()})["error_kind"] == "empty_edit"
    assert _refusal("set_finance_inputs", {"finance": changed, "if_match": digest_before},
                    412)["error_kind"] == "finance_changed"


def test_finance_clear_and_the_refusals(edge):
    assert _refusal("set_finance_inputs", {"clear": True, "if_match": _digest()})["error_kind"] == "empty_edit"
    chat_tools.set_finance_inputs(_digest(), _finance())
    args = {"clear": True, "if_match": _digest()}
    card = edit_card.preview("set_finance_inputs", args)
    (row,) = card["rows"]
    assert (row["label"], row["new"]) == ("Finance inputs", None) and row["old"].endswith("fields")
    frames, cards, result = _dispatch(edge, "set_finance_inputs", args)
    assert result["_edit_receipt"]["outcome"] == "verified" and chat_tools.get_finance_inputs()["finance"] is None
    assert _refusal("set_finance_inputs", {"finance": _finance(), "if_match": "*"}, 422)[
        "error_kind"] == "finance_if_match_required"
    assert _refusal("set_finance_inputs", {"if_match": _digest()}, 422)["error_kind"] == "finance_clear_invalid"
    assert _refusal("set_finance_inputs", {"finance": {"currency": "EUR", "nonsense": 1},
                                           "if_match": _digest()}, 422)["error_kind"] == "invalid_edit_value"


def test_finance_without_a_user_is_refused_first(edge, no_user):
    assert _refusal("set_finance_inputs", {"clear": True, "if_match": "x"}, 401)["error_kind"] == "no_acting_user"


# ── define_participants ─────────────────────────────────────────────────────

@pytest.fixture
def parties(edge):
    chat_tools.update_solver_config({"commercial": {"poc_link": "import", "import_tariff": {
        "id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
        "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": [{"name": "all", "rate": 0.2}]}]},
        "contracts": [PPA]}})
    return edge


def _flows():
    import routers.simulation as sim

    return sim.get_value_flows()


def test_participants_template_shows_the_new_parties_and_verifies(parties):
    before = _flows()
    card = edit_card.preview("define_participants", {"template": "btm_ppa"})
    by_label = {r["label"]: r for r in card["rows"]}
    assert (by_label["Template"]["old"], by_label["Template"]["new"]) == (None, "btm_ppa")
    assert by_label["Participant · site"]["new"].startswith("offtaker")
    assert _flows() == before, "a preview writes nothing"
    frames, cards, result = _dispatch(parties, "define_participants", {"template": "btm_ppa"})
    assert result["_edit_receipt"]["outcome"] == "verified"
    # The same template again changes nothing; another needs the user's replace.
    assert _refusal("define_participants", {"template": "btm_ppa"})["error_kind"] == "empty_edit"
    assert _refusal("define_participants", {"template": "single_owner"}, 409)[
        "error_kind"] == "value_flows_would_be_replaced"
    swap = edit_card.preview("define_participants", {"template": "single_owner", "replace": True})
    assert _cells(swap)[0] == ("template", "btm_ppa", "single_owner")
    assert any(r["old"] and r["new"] is None for r in swap["rows"]), "a party that goes shows old → not set"
    cleared = edit_card.preview("define_participants", {"clear": True, "replace": True})
    assert all(r["new"] is None for r in cleared["rows"] if r["role"] == "typed")


def test_participants_refuse_what_the_handler_refuses(parties):
    assert _refusal("define_participants", {"template": "landlord_tenant"}, 409)[
        "error_kind"] == "value_flows_drafts_need_pricing"
    assert _flows()["value_flows"] is None
    assert _refusal("define_participants", {})["error_kind"] == "value_flows_invalid"


def test_participants_without_a_user_are_refused_first(parties, no_user):
    assert _refusal("define_participants", {"template": "btm_ppa"}, 401)["error_kind"] == "no_acting_user"


# ── set_project_sites ───────────────────────────────────────────────────────

def _site(site_id="s1", name="Plant", **extra):
    return {"id": site_id, "name": name, "buses": ["site"], "origin": {"lng": 4.0, "lat": 52.0},
            "boundary": [[4.0, 52.0], [4.1, 52.0], [4.1, 52.1], [4.0, 52.1]], "placements": {}, **extra}


def _sites_file(name):
    return chat_tools._authorized_project(name).directory / "sites.json"


def _bytes(path):
    return path.read_bytes() if path.exists() else None


@pytest.fixture
def outsider(second_identity):
    chat_tools.set_acting_user(second_identity["user_id"])
    yield


def test_sites_show_an_added_site_then_only_what_differs(client, api_project, session):
    name = api_project("card-sites")
    path = _sites_file(name)
    before = _bytes(path)
    doc = {"version": 1, "sites": [_site()]}
    card = edit_card.preview("set_project_sites", {"name": name, "sites": doc})
    (added,) = card["rows"]
    assert (added["label"], added["old"], added["new"]) == (
        "Site · Plant", None, "4 vertices boundary, 1 bus, 0 placements")
    assert _bytes(path) == before, "a preview writes nothing"
    frames, cards, result = _dispatch(session, "set_project_sites", {"name": name, "sites": doc})
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert _refusal("set_project_sites", {"name": name, "sites": doc})["error_kind"] == "empty_edit"
    moved = {"version": 1, "sites": [_site(name="Plant B", placements={"Generator:pv": {"x": 1.5, "y": 2, "heading": 0}})]}
    card = edit_card.preview("set_project_sites", {"name": name, "sites": moved})
    assert [(r["label"], r["old"], r["new"]) for r in card["rows"]] == [
        ("Site · Plant · Name", "Plant", "Plant B"),  # named as the stored document names it
        ("Site · Plant · Placement Generator:pv", None, "x 1.5, y 2, heading 0")]
    frames, cards, result = _dispatch(session, "set_project_sites", {"name": name, "sites": moved})
    assert result["_edit_receipt"]["outcome"] == "verified"
    gone = edit_card.preview("set_project_sites", {"name": name, "sites": {"version": 1, "sites": []}})
    assert [(r["old"], r["new"]) for r in gone["rows"] if r["role"] == "typed"] == [
        ("4 vertices boundary, 1 bus, 1 placement", None)]
    assert any(r["role"] == "note" and "map context" in r["note"] for r in gone["rows"])


def test_sites_refuse_what_the_route_refuses_and_an_outsider(client, api_project, session, request):
    name = api_project("card-sites-bad")
    bad = _refusal("set_project_sites", {"name": name, "sites": {"version": 1, "sites": "no"}}, 422)
    assert bad["error_kind"] == "invalid_edit_value"
    request.getfixturevalue("outsider")
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("set_project_sites", {"name": name, "sites": {"version": 1, "sites": [_site()]}})
    assert exc.value.status_code in (403, 404)
