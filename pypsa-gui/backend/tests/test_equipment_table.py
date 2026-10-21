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
    assert stamp["source"] == f"upload:{preview['source']['sha256'][:16]}/lines.csv#(csv)!C3"


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


def test_et08_a_refusal_part_way_is_reported_as_partial(grid, monkeypatch):
    p = T.plan_active({"upload_id": _csv(LINES), "component_class": "Line", "name_column": "name",
                       "columns": LINE_COLUMNS})
    real = chat_tools.update_component

    def update(klass, name, attrs, *a, **k):
        if name == "L2":
            raise HTTPException(409, {"error_kind": "study_in_flight", "message": "a run holds the network"})
        return real(klass, name, attrs, *a, **k)

    monkeypatch.setattr(chat_tools, "update_component", update)
    out = T.apply(p, approved_by=None)
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
