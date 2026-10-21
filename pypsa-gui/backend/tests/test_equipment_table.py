"""Equipment tables onto assets, cell by cell (data-to-model slice 1, owner AI-567/567b).

`services/equipment_table.py` maps an uploaded CSV/XLSX onto assets of one class;
`apply_equipment_mapping` writes it through the edit card. Cases are this slice's
own numbering of the #567 study's I-series (research only):

- ET-01 (I08) a two-line table: one card, a row per changed cell naming its
  file/sheet/cell, native readback verified, provenance names the cell;
- ET-02 units: the display unit converts by the schema's scale, any other unit
  or none stays unmapped (I03/I04);
- ET-03 entities and fields: unknown/duplicate names, derived fields, gaps (I04);
- ET-04 (I01) inert file: formulas never evaluated, CSV text never executed,
  oversize refused rather than truncated;
- ET-05 (I06) deny writes nothing; ET-06 (I07) changed bytes refuse;
- ET-07 (I09) a repeat apply changes nothing; ET-08 (I10) a part-way refusal is
  reported as partial; ET-09 one undo reverts the whole table;
- ET-10 money: a cost column needs currency and money year, a different base
  year needs the user's confirmation;
- ET-11 (I07) the HTTP apply refuses a card that is not current.

Gatekeeper review (GO WITH CHANGES), each with its case:

- ET-12 the live loop writes only the cells the card's copy preview accepted, and
  refuses before the first write when the network moved since the card;
- ET-13 one undo through the HTTP route reverts the whole batch;
- ET-14 a zip bomb or a DOCTYPE/entity refuses before openpyxl opens the workbook;
- ET-15 time-series fields stay out; static-or-series fields are written as static;
- ET-16 the card's digest binds the source cell, not the raw text in it;
- ET-18 the 200-cell card bound counts changed cells only, and left-out cells are
  listed up to 50 with the rest counted;
- ET-17 the route sits behind the foreign-lock gate and the undo middleware, both
  tools are lock-gated, the apply is undo-captured, and an anonymous apply is refused.
"""
from __future__ import annotations

import io
import json

import pypsa
import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from services import chat_service, chat_tools, upload_service
from services import equipment_table as T
from services.asset_schema import provenance
from services.pypsa_service import PyPSAService


@pytest.fixture
def grid(tmp_projects_dir, install_network):
    n = pypsa.Network()
    n.set_snapshots(range(2))
    for bus in ("B1", "B2", "B3"):
        n.add("Bus", bus, v_nom=110)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=12.1, r=0.1, s_nom=300, length=10)
    n.add("Line", "L2", bus0="B2", bus1="B3", x=8.0, r=0.1, s_nom=200, length=20)
    n.add("StorageUnit", "Battery", bus="B2", p_nom=20, max_hours=4)
    install_network(n, name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    yield session
    chat_tools.set_chat_session(None)


def _csv(text: str, filename: str = "lines.csv") -> str:
    return upload_service.add_upload("P", text.encode(), filename, "text/csv").file_id


def _xlsx(rows: list[list], sheet: str = "Lines") -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return upload_service.add_upload("P", buf.getvalue(), "lines.xlsx", mime).file_id


def _dispatch(session, name, args, decision="approve"):
    cards, collector = [], []
    gen = chat_service._dispatch_real_tool_call(session, {"id": "tu-1", "name": name, "input": args}, collector)
    for event, data in gen:
        if event == "tool_pending_confirmation":
            cards.append(data)
            session.record_decision(data["confirmation_token"], decision)
    result = None
    if collector and not collector[-1].get("is_error"):
        content = collector[-1]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        result, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    return cards, result, collector


LINES = "name,rating,reactance\nL1,500,10.5\nL2,450,7.25\n"
LINE_COLUMNS = {"rating": {"field": "s_nom", "unit": "MVA"}, "reactance": {"field": "x", "unit": "Ohm"}}


def _args(upload_id, columns=LINE_COLUMNS, klass="Line", **extra):
    preview = chat_tools.preview_equipment_mapping(upload_id, klass, "name", columns, **{
        k: v for k, v in extra.items() if k in ("sheet", "currency", "money_year", "confirm")})
    return preview, {"upload_id": upload_id, "component_class": klass, "name_column": "name", "columns": columns,
                     "source_sha256": preview["source"]["sha256"], **extra}


def test_et01_two_lines_one_card_with_cell_lineage_and_verified_readback(grid):
    uid = _csv(LINES)
    preview, args = _args(uid)
    assert preview["changed_cells"] == 4 and preview["unmapped"] == []
    assert len(preview["source"]["sha256"]) == 64
    assert PyPSAService.get_network().lines.at["L1", "s_nom"] == 300  # preview wrote nothing

    cards, result, _ = _dispatch(grid, "apply_equipment_mapping", args)
    assert len(cards) == 1
    rows = {(r["name"], r["field"]): r for r in cards[0]["edit_card"]["rows"] if r["role"] == "typed"}
    assert set(rows) == {("L1", "s_nom"), ("L1", "x"), ("L2", "s_nom"), ("L2", "x")}
    l2x = rows[("L2", "x")]
    assert (l2x["old"], l2x["new"], l2x["unit"]) == (8.0, 7.25, "Ohm")
    assert l2x["source"] == {"upload_id": uid, "filename": "lines.csv", "sha256": preview["source"]["sha256"], "sheet": "(csv)",
                             "row": 3, "column": "C", "cell": "C3", "header": "reactance",
                             "raw": "7.25", "raw_unit": "Ohm"}

    n = PyPSAService.get_network()
    assert n.lines.at["L1", "s_nom"] == 500 and n.lines.at["L2", "x"] == 7.25
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert result["written"] == {"L1": {"s_nom": 500, "x": 10.5}, "L2": {"s_nom": 450, "x": 7.25}}
    stamp = provenance.get(n, "Line", "L2")["x"]
    assert stamp["status"] == "customised"
    assert stamp["source"] == f"upload:{uid}#{preview['source']['sha256'][:16]} (csv)!C3"


def test_et02_display_unit_converts_by_the_schema_scale_and_other_units_stay_out(grid):
    uid = _csv("name,energy cost,eff\nBattery,150,95\n", "battery.csv")
    preview = chat_tools.preview_equipment_mapping(
        uid, "StorageUnit", "name", {"energy cost": {"field": "inv_energy_overnight", "unit": "EUR/kWh"},
                                     "eff": {"field": "efficiency_store", "unit": "%"}},
        currency="EUR", money_year=2025)
    cells = {c["field"]: c for c in preview["cells"]}
    assert cells["inv_energy_overnight"]["value"] == 150_000  # EUR/MWh stored, exactly
    assert cells["efficiency_store"]["value"] == 0.95

    for unit, kind in (("kVA", "unit_mismatch"), ("", "unit_missing"), (None, "unit_missing")):
        out = chat_tools.preview_equipment_mapping(_csv(LINES), "Line", "name",
                                                   {"rating": {"field": "s_nom", "unit": unit}})
        assert out["cells"] == []
        assert [u["kind"] for u in out["unmapped"]] == [kind]
        assert out["unmapped"][0]["accepted_units"] == ["MVA"]


def test_et03_unknown_and_duplicate_names_derived_fields_and_gaps_stay_unmapped(grid):
    uid = _csv("name,rating,capex\nL1,500,9\nL9,1,1\nL2,,1\nL2,400,1\n,7,\n")
    out = chat_tools.preview_equipment_mapping(uid, "Line", "name", {
        "rating": {"field": "s_nom", "unit": "MVA"}, "capex": {"field": "capital_cost", "unit": "EUR/MVA/yr"}})
    kinds = sorted(u["kind"] for u in out["unmapped"])
    assert kinds == ["component_not_found", "derived_field", "duplicate_entity", "duplicate_entity",
                     "name_missing"]
    assert [(c["name"], c["field"], c["value"]) for c in out["cells"]] == [("L1", "s_nom", 500)]

    gaps = chat_tools.preview_equipment_mapping(_csv("name,rating\nL1,\nL2,1.2.3\nL3x,-4\n"), "Line", "name",
                                                {"rating": {"field": "s_nom", "unit": "MVA"}})
    assert sorted(u["kind"] for u in gaps["unmapped"]) == ["component_not_found", "empty_cell", "unparsed_number"]
    negative = chat_tools.preview_equipment_mapping(_csv("name,rating\nL1,-4\n"), "Line", "name",
                                                    {"rating": {"field": "s_nom", "unit": "MVA"}})
    assert negative["cells"] == [] and negative["unmapped"][0]["kind"] == "invalid_edit_value"


def test_et04_formulas_are_never_evaluated_and_oversize_refuses(grid):
    uid = _xlsx([["name", "rating"], ["L1", "=200+300"], ["L2", 450]])
    out = chat_tools.preview_equipment_mapping(uid, "Line", "name", {"rating": {"field": "s_nom", "unit": "MVA"}},
                                               sheet="Lines")
    assert [(c["name"], c["value"], c["source"]["cell"]) for c in out["cells"]] == [("L2", 450, "B3")]
    assert out["unmapped"][0]["kind"] == "formula_without_value"
    assert out["source"]["sheet"] == "Lines"

    text = chat_tools.preview_equipment_mapping(_csv('name,rating\nL1,"=cmd|\' /C calc\'!A0"\n'), "Line", "name",
                                                {"rating": {"field": "s_nom", "unit": "MVA"}})
    assert text["unmapped"][0]["kind"] == "unparsed_number"

    big = "name,rating\n" + "".join(f"L{i},1\n" for i in range(T.MAX_ROWS + 1))
    with pytest.raises(HTTPException) as exc:
        chat_tools.preview_equipment_mapping(_csv(big), "Line", "name", {"rating": {"field": "s_nom", "unit": "MVA"}})
    assert exc.value.detail["error_kind"] == "table_too_large"


def test_et05_deny_writes_nothing(grid):
    _, args = _args(_csv(LINES))
    cards, result, _ = _dispatch(grid, "apply_equipment_mapping", args, decision="deny")
    assert len(cards) == 1 and result is None
    n = PyPSAService.get_network()
    assert n.lines.at["L1", "s_nom"] == 300 and n.lines.at["L2", "x"] == 8.0


def test_et06_changed_bytes_refuse_before_any_card(grid):
    _, args = _args(_csv(LINES))
    args["source_sha256"] = "0" * 64
    cards, result, collector = _dispatch(grid, "apply_equipment_mapping", args)
    assert cards == [] and result is None
    assert "source_changed" in json.dumps(collector[-1]["content"])
    assert PyPSAService.get_network().lines.at["L1", "s_nom"] == 300


def test_et07_a_repeat_apply_changes_nothing(grid):
    _, args = _args(_csv(LINES))
    _dispatch(grid, "apply_equipment_mapping", args)
    cards, result, collector = _dispatch(grid, "apply_equipment_mapping", args)
    assert cards == [] and result is None
    assert "nothing_to_change" in json.dumps(collector[-1]["content"])


def _card(args):
    from harness import edit_card

    return edit_card.preview("apply_equipment_mapping", args, with_state=False)


def test_et08_a_refusal_part_way_is_reported_as_partial(grid, monkeypatch):
    args = {"upload_id": _csv(LINES), "component_class": "Line", "name_column": "name", "columns": LINE_COLUMNS}
    p, card = T.plan_active(args), _card(args)
    real = chat_tools.update_component

    def update(klass, name, attrs, *a, **k):
        if name == "L2":
            raise HTTPException(409, {"error_kind": "study_in_flight", "message": "a run holds the network"})
        return real(klass, name, attrs, *a, **k)

    monkeypatch.setattr(chat_tools, "update_component", update)
    out = T.apply(p, card, approved_by=None)
    assert out["partial"] is True
    assert out["written"] == {"L1": {"s_nom": 500, "x": 10.5}}
    assert out["failed"]["name"] == "L2" and out["failed"]["error_kind"] == "study_in_flight"
    n = PyPSAService.get_network()
    assert n.lines.at["L1", "s_nom"] == 500 and n.lines.at["L2", "s_nom"] == 200
    assert "x" in provenance.get(n, "Line", "L1") and provenance.get(n, "Line", "L2") == {}


def test_et09_one_undo_reverts_the_whole_table(grid):
    _, args = _args(_csv(LINES))
    _, result, _ = _dispatch(grid, "apply_equipment_mapping", args)
    assert result["_edit_receipt"]["undo"]["available"] is True
    chat_tools.undo_last()
    n = PyPSAService.get_network()
    assert (n.lines.at["L1", "s_nom"], n.lines.at["L2", "s_nom"], n.lines.at["L2", "x"]) == (300, 200, 8.0)


def test_et10_cost_columns_state_their_money_and_a_different_base_year_needs_confirmation(grid):
    uid = _csv("name,energy cost,life\nBattery,150,15\n", "battery.csv")
    columns = {"energy cost": {"field": "inv_energy_overnight", "unit": "EUR/kWh"},
               "life": {"field": "inv_energy_lifetime", "unit": "years"}}
    bare = chat_tools.preview_equipment_mapping(uid, "StorageUnit", "name", columns)
    assert [c["field"] for c in bare["cells"]] == ["inv_energy_lifetime"]
    assert bare["unmapped"][0]["kind"] == "money_basis_missing"
    usd = chat_tools.preview_equipment_mapping(uid, "StorageUnit", "name", columns, currency="USD", money_year=2024)
    assert usd["unmapped"][0]["kind"] == "currency_mismatch"

    n = PyPSAService.get_network()
    p = T.plan(n, T.read_table("P", uid), "StorageUnit", "name", columns,
               money={"currency": "EUR", "money_year": 2020}, base_year=2025,
               fields=__import__("harness.edit_card", fromlist=["x"])._fields("StorageUnit"))
    assert p["missing"] == ["money_year"]
    assert "2020 EUR price data, read as 2025 money: not adjusted for inflation." in p["needs"][0]["text"]
    with pytest.raises(HTTPException) as exc:
        T.require_confirmed(p)
    assert exc.value.detail["error_kind"] == "table_confirmation_needed"

    cards, result, _ = _dispatch(grid, "apply_equipment_mapping", {
        "upload_id": uid, "component_class": "StorageUnit", "name_column": "name", "columns": columns,
        "currency": "EUR", "money_year": 2025, "source_sha256": bare["source"]["sha256"]})
    typed = {r["field"]: r for r in cards[0]["edit_card"]["rows"] if r["role"] == "typed"}
    assert "money" not in typed["inv_energy_lifetime"]
    assert typed["inv_energy_overnight"]["money"] == {"currency": "EUR", "money_year": 2025}
    stamp = provenance.get(PyPSAService.get_network(), "StorageUnit", "Battery")["inv_energy_overnight"]
    assert (stamp["currency"], stamp["currency_year"]) == ("EUR", 2025)



def test_et11_the_http_apply_refuses_a_card_that_is_not_current(client, grid):
    """The UI's apply (its preview route arrives with the manual-mapping form) needs the
    digest of the card the user saw; any other refuses and writes nothing."""
    n = PyPSAService.get_network()
    _, args = _args(_csv(LINES))
    r = client.post("/api/network/equipment_table/apply", json={**args, "digest": "f" * 64})
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "edit_preview_stale", r.text
    assert n.lines.at["L1", "s_nom"] == 300 and n.lines.at["L2", "x"] == 8.0


def test_et12_the_live_loop_writes_only_what_the_copy_accepted_and_refuses_a_moved_network(grid):
    args = {"upload_id": _csv(LINES), "component_class": "Line", "name_column": "name", "columns": LINE_COLUMNS}
    p, card = T.plan_active(args), _card(args)
    n = PyPSAService.get_network()
    n.lines.loc["L2", "x"] = 9.0  # another write after the card was shown
    with pytest.raises(HTTPException) as exc:
        T.apply(p, card, approved_by=None)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "edit_preview_stale"
    assert n.lines.at["L1", "s_nom"] == 300  # nothing written, not even the unmoved asset

    n.lines.loc["L2", "x"] = 8.0
    fewer = {**card, "rows": [r for r in card["rows"] if (r["name"], r.get("field")) != ("L2", "x")]}
    with pytest.raises(HTTPException) as exc:
        T.apply(p, fewer, approved_by=None)
    assert exc.value.detail["error_kind"] == "edit_preview_stale"
    assert n.lines.at["L1", "s_nom"] == 300


def test_et13_one_undo_through_the_http_route_reverts_the_whole_batch(client, grid, session_ctx):
    from tests.test_chat_tools_dispatch import _bound_to

    def lines():
        rows = {ln["name"]: ln for ln in client.get("/api/network/lines").json()}
        return rows["L1"]["s_nom"], rows["L1"]["x"], rows["L2"]["s_nom"], rows["L2"]["x"]

    # A saved project the client's session owns, as a real user's apply runs on.
    assert client.post("/api/projects/P", params={"force": True, "rebind": True}).status_code == 200
    with _bound_to(session_ctx(client)):
        _, args = _args(_csv(LINES))
        digest = _card(args)["digest"]
    r = client.post("/api/network/equipment_table/apply", json={**args, "digest": digest})
    assert r.status_code == 200, r.text
    assert r.json()["written"] == {"L1": {"s_nom": 500, "x": 10.5}, "L2": {"s_nom": 450, "x": 7.25}}
    assert r.json()["receipt"]["outcome"] == "verified"
    assert lines() == (500, 10.5, 450, 7.25)
    assert client.get("/api/network/undo/info").json()["depth"] == 1  # one snapshot for the batch

    assert client.post("/api/network/undo").status_code == 200
    assert lines() == (300, 12.1, 200, 8.0)


def _xlsx_bytes(parts: dict[str, bytes], method=None) -> bytes:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method or zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_et14_a_zip_bomb_or_a_doctype_refuses_before_openpyxl_opens_it(grid, monkeypatch):
    import openpyxl

    opened = []
    monkeypatch.setattr(openpyxl, "load_workbook", lambda *a, **k: opened.append(1) or (_ for _ in ()).throw(
        AssertionError("openpyxl opened an unsafe workbook")))
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    columns = {"rating": {"field": "s_nom", "unit": "MVA"}}

    bomb = upload_service.add_upload("P", _xlsx_bytes({"xl/worksheets/sheet1.xml": b"\0" * (8 * 1024 * 1024)}),
                                     "bomb.xlsx", mime).file_id
    with pytest.raises(HTTPException) as exc:
        chat_tools.preview_equipment_mapping(bomb, "Line", "name", columns)
    assert exc.value.detail["error_kind"] == "table_unsafe"

    xxe = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><worksheet>&e;</worksheet>'
    dtd = upload_service.add_upload("P", _xlsx_bytes({"xl/worksheets/sheet1.xml": xxe}), "xxe.xlsx", mime).file_id
    with pytest.raises(HTTPException) as exc:
        chat_tools.preview_equipment_mapping(dtd, "Line", "name", columns)
    assert exc.value.detail["error_kind"] == "table_unsafe" and "DOCTYPE" in exc.value.detail["message"]

    # Any member name, and an encoding the byte scan cannot read, refuse too.
    for parts in ({"xl/worksheets/sheet1.bin": xxe},
                  {"xl/worksheets/sheet1.xml": xxe.decode().replace("1.0\"?", "1.0\" encoding=\"UTF-16\"?")
                   .encode("utf-16")},
                  {"xl/worksheets/sheet1.xml": b'<?xml version="1.0" encoding="UTF-16LE"?><worksheet/>'}):
        bad = upload_service.add_upload("P", _xlsx_bytes(parts), "x.xlsx", mime).file_id
        with pytest.raises(HTTPException) as exc:
            chat_tools.preview_equipment_mapping(bad, "Line", "name", columns)
        assert exc.value.detail["error_kind"] == "table_unsafe", parts.keys()
    assert opened == []


def test_et14b_a_malformed_sheet_refuses_as_unreadable_not_a_server_error(grid):
    import zipfile

    buf = io.BytesIO()
    wb = Workbook()
    wb.active.title = "Lines"
    wb.save(buf)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item)
            dst.writestr(item, b"<worksheet><sheetData><row" if item.filename.endswith("sheet1.xml") else data)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    uid = upload_service.add_upload("P", out.getvalue(), "broken.xlsx", mime).file_id
    with pytest.raises(HTTPException) as exc:
        chat_tools.preview_equipment_mapping(uid, "Line", "name", {"rating": {"field": "s_nom", "unit": "MVA"}})
    assert exc.value.detail["error_kind"] == "unreadable_table"


def test_et15_time_series_fields_stay_out_and_static_or_series_fields_are_static(grid):
    import pandas as pd

    out = chat_tools.preview_equipment_mapping(_csv("name,flow\nL1,5\n"), "Line", "name",
                                               {"flow": {"field": "p0", "unit": "MW"}})
    assert out["cells"] == [] and out["unmapped"][0]["kind"] == "time_series_not_supported_here"

    n = PyPSAService.get_network()
    n.lines_t.s_max_pu = pd.DataFrame({"L2": [0.5, 0.6]}, index=n.snapshots)
    out = chat_tools.preview_equipment_mapping(_csv("name,smax\nL1,0.9\nL2,0.9\n"), "Line", "name",
                                               {"smax": {"field": "s_max_pu", "unit": "per unit"}})
    assert [(c["name"], c["value"]) for c in out["cells"]] == [("L1", 0.9)]
    assert [(u["name"], u["kind"]) for u in out["unmapped"]] == [("L2", "time_series_not_supported_here")]

    eff = chat_tools.preview_equipment_mapping(_csv("name,eff\nBattery,90\n", "b.csv"), "StorageUnit", "name",
                                               {"eff": {"field": "efficiency_store", "unit": "%"}})
    assert [(c["field"], c["value"]) for c in eff["cells"]] == [("efficiency_store", 0.9)]


def test_et16_the_digest_binds_the_source_cell_not_its_raw_text(grid):
    plain = _csv("name,rating\nL1,500\n")
    padded = _csv("name,rating\nL1,500.0\n")
    args = {"component_class": "Line", "name_column": "name", "columns": {"rating": {"field": "s_nom", "unit": "MVA"}}}
    a, b = _card({**args, "upload_id": plain}), _card({**args, "upload_id": padded})
    typed = [r for r in a["rows"] if r["role"] == "typed"]
    assert typed[0]["source"]["raw"] == "500"  # the card still shows it
    # Different bytes and upload: the cell's lineage differs, so the digest does.
    assert a["digest"] != b["digest"]
    from harness import edit_card

    same_cell = {**a, "rows": [{**r, "source": {**r["source"], "raw": "500.000"}} if r.get("source") else r
                               for r in a["rows"]]}
    assert edit_card._digest(same_cell) == a["digest"]
    moved = {**a, "rows": [{**r, "source": {**r["source"], "cell": "B9"}} if r.get("source") else r
                           for r in a["rows"]]}
    assert edit_card._digest(moved) != a["digest"]


def test_et17_the_route_is_gated_undo_captured_and_refuses_anonymous_callers(anon_client, grid):
    import main

    path = "/api/network/equipment_table/apply"
    assert any(path.startswith(p) for p in main._FOREIGN_LOCK_GATE_PREFIXES)
    assert any(path.startswith(p) for p in main._UNDO_PREFIXES) and path not in main._UNDO_EXCLUDE
    gated = chat_tools._lock_gated_tool_names()
    assert "apply_equipment_mapping" in gated and "apply_equipment_mapping" in chat_tools.UNDO_CAPTURED_TOOLS
    assert "preview_equipment_mapping" not in chat_tools.UNDO_CAPTURED_TOOLS

    _, args = _args(_csv(LINES))
    r = anon_client.post(path, json={**args, "digest": "f" * 64})
    assert r.status_code == 401
    assert PyPSAService.get_network().lines.at["L1", "s_nom"] == 300


def test_et18_the_card_bound_counts_changed_cells_and_long_gap_lists_are_counted(grid, monkeypatch):
    monkeypatch.setattr(T, "MAX_CARD_CELLS", 3)
    monkeypatch.setattr(T, "MAX_LISTED_GAPS", 2)
    # Four mapped cells, two of them already equal to the network: two change.
    same = _csv("name,rating,reactance\nL1,300,12.1\nL2,450,7.25\nL7,1,1\nL8,1,1\nL9,1,1\n")
    out = chat_tools.preview_equipment_mapping(same, "Line", "name", LINE_COLUMNS)
    assert (out["changed_cells"], out["unchanged_cells"]) == (2, 2)
    assert [(c["name"], c["field"]) for c in out["cells"]] == [("L2", "s_nom"), ("L2", "x")]
    assert len(out["unmapped"]) == 2 and out["unmapped_total"] == 3
    assert out["unmapped_by_kind"] == {"component_not_found": 3}

    card = _card({"upload_id": same, "component_class": "Line", "name_column": "name", "columns": LINE_COLUMNS})
    gaps = [r for r in card["rows"] if r.get("gap")]
    assert len(gaps) == 2 and any("1 more cells are left out" in str(r) for r in card["rows"])

    with pytest.raises(HTTPException) as exc:
        chat_tools.preview_equipment_mapping(_csv(LINES), "Line", "name", LINE_COLUMNS)
    assert exc.value.detail["error_kind"] == "mapping_too_large"
