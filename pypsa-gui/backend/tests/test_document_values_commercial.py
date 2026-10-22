"""Values from a PDF onto the import tariff and PPA contracts (data-to-model slice 2, PR-B;
owner AI-567b; plan `.scratch/data-to-model/slice-2-plan.md`, gatekeeper GO WITH CHANGES).
The citation rules are PR-A's (`tests/test_document_values.py`); these cases cover what
section 7 adds:

- CDV-01 a GBP quote on an EUR project refuses (`currency_mismatch`), and a stated
  currency other than the project's;
- CDV-02 /MWh into a per_kwh item is the one scale, ×1/1000, exact; the row keeps the raw
  value and unit; the apply writes it through the solver-config route and reads it back;
- CDV-03 no other unit converts: /kWh or /kW/yr into a per_kw_month item refuses;
- CDV-04 money is stated, never assumed: no currency is `money_basis_missing`; a project
  with no finance currency is disclosed on the card;
- CDV-05 a PPA price's money year: written as the contract's `base_year` when unset, a
  different `base_year` refuses, another year than the case's needs the confirmation, and
  nothing is adjusted;
- CDV-06 compare-and-set: a config changed since the card refuses (`edit_preview_stale`)
  on the HTTP apply, and inside `apply` under the lock;
- CDV-07 a library-pinned tariff or contract is refused in place (`library_pinned`), a
  missing item says where to add it;
- CDV-08 a change the commercial preflight rejects writes nothing;
- CDV-09 undo: the receipt says `undo_last` does not revert it, and the tool is not on
  the network's undo stack; the write is behind the foreign-lock gate;
- CDV-10 the preview route is a read, and its card is the one the apply accepts.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services import chat_service, chat_tools, upload_service
from services import document_values as D
from services import document_values_commercial as DC
from services.pypsa_service import PyPSAService
from tests.fixtures.investment_case.edge_15min import build_edge_15min
from tests.test_document_values import pdf_bytes

TARIFF = {"id": "t", "name": "Grid tariff", "jurisdiction": "DE", "valid_from": "2029-01-01",
          "items": [{"id": "energy", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "peak", "rate": 0.2, "start_hour": 8, "end_hour": 20},
                                 {"name": "offpeak", "rate": 0.1}]},
                    {"id": "demand", "kind": "demand", "unit": "per_kw_month",
                     "periods": [{"name": "all", "rate": 10.0}]}]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 55.0,
       "tenor_years": 10, "seller": "Wind BV", "buyer": "site", "asset_ids": ["pv"]}

SHEET = [["Network tariff 2030", "Energy charge peak 72.50 EUR/MWh", "Energy charge off-peak 0.11 EUR/kWh",
          "Demand charge 12 EUR/kW/month", "Annual capacity charge 140 EUR/kW/yr", "Peak energy 0.25 per kWh"],
         ["Power purchase agreement", "Fixed price 48.20 £/MWh", "Fixed price 61.5 EUR/MWh",
          "Contract term 15 years"]]


def _commercial(**extra) -> dict:
    return {"poc_link": "import", "timezone": "UTC", "import_tariff": TARIFF, "contracts": [PPA], **extra}


@pytest.fixture
def site(tmp_projects_dir, install_network):
    install_network(build_edge_15min(), name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": _commercial()})
    _finance(currency="EUR")
    D._PAGES.clear()
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    yield session
    chat_tools.set_chat_session(None)


def _finance(**fields):
    PyPSAService.get_active_context().solver_state["solver_config"].finance = fields or None


def _pdf(pages=SHEET) -> str:
    return upload_service.add_upload("P", pdf_bytes(pages), "tariff.pdf", "application/pdf").file_id


def _stored() -> dict:
    return DC.stored_commercial()


def _preview(uid, citations, **extra):
    out = chat_tools.preview_commercial_document_values(uid, citations, **extra)
    return out, {"upload_id": uid, "citations": citations, **extra, "source_sha256": out["source"]["sha256"]}


def _dispatch(session, name, args, decision="approve"):
    from tests.test_equipment_table import _dispatch as dispatch

    return dispatch(session, name, args, decision)


PEAK = {"target": "tariff", "entity": "energy", "field": "periods[peak].rate", "page": 1,
        "quote": "Energy charge peak 72.50 EUR/MWh", "unit": "EUR/MWh"}
GBP_PRICE = {"target": "ppa", "entity": "ppa1", "field": "price", "page": 2, "quote": "Fixed price 48.20 £/MWh",
             "unit": "/MWh"}
EUR_PRICE = {**GBP_PRICE, "quote": "Fixed price 61.5 EUR/MWh"}


def test_cdv01_a_gbp_quote_on_an_eur_project_refuses(site):
    uid = _pdf()
    out, _ = _preview(uid, [GBP_PRICE], currency="GBP", money_year=2030)
    assert out["cells"] == [] and out["changed_cells"] == 0
    (gap,) = out["unmapped"]
    assert gap["kind"] == "currency_mismatch" and "EUR" in gap["message"] and "GBP" in gap["message"]
    # The quote's own currency against a stated one, too.
    out, _ = _preview(uid, [GBP_PRICE], currency="EUR", money_year=2030)
    assert [g["kind"] for g in out["unmapped"]] == ["currency_mismatch"]
    # And a unit naming another currency than the stated one.
    out, _ = _preview(uid, [{**EUR_PRICE, "unit": "GBP/MWh"}], currency="EUR", money_year=2030)
    assert [g["kind"] for g in out["unmapped"]] == ["currency_mismatch"]
    assert _stored()["contracts"][0]["price"] == 55.0


def test_cdv02_mwh_into_a_per_kwh_item_is_the_one_scale_and_the_apply_reads_it_back(site):
    uid = _pdf()
    out, args = _preview(uid, [PEAK])
    assert out["unmapped"] == [] and out["changed_cells"] == 1
    (cell,) = out["cells"]
    assert (cell["old"], cell["value"], cell["unit"], cell["currency"]) == (0.2, 0.0725, "/kWh", "EUR")
    assert any("÷1000" in note for note in cell["notes"])
    cards, result, _ = _dispatch(site, "apply_commercial_document_values", args)
    (card,) = cards
    (row,) = [r for r in card["edit_card"]["rows"] if r["role"] == "typed"]
    assert (row["old"], row["new"], row["field"]) == (0.2, 0.0725, "periods[peak].rate")
    assert (row["source"]["page"], row["source"]["raw"], row["source"]["raw_unit"]) == (1, "72.50", "EUR/MWh")
    assert len(row["basis"]) == 64  # the stored config's sha256 at preview (compare-and-set)
    assert card["edit_card"]["section"]["id"] == "commercial"
    assert result["written"] == [{"target": "tariff", "entity": "energy", "path": "periods[peak].rate",
                                  "value": 0.0725}]
    assert result["_edit_receipt"]["outcome"] == "verified"
    item = _stored()["import_tariff"]["items"][0]
    assert item["periods"][0]["rate"] == 0.0725 and item["periods"][1]["rate"] == 0.1


def test_cdv03_no_other_unit_converts(site):
    uid = _pdf()
    demand = {"target": "tariff", "entity": "demand", "field": "periods[all].rate", "page": 1}

    def kinds(quote, unit):
        out, _ = _preview(uid, [{**demand, "quote": quote, "unit": unit}])
        return [g["kind"] for g in out["unmapped"]] or out["cells"][0]["value"]

    assert kinds("Demand charge 12 EUR/kW/month", "EUR/kW/month") == 12
    assert kinds("Energy charge off-peak 0.11 EUR/kWh", "EUR/kWh") == ["unit_mismatch"]
    assert kinds("Annual capacity charge 140 EUR/kW/yr", "EUR/kW/yr") == ["unit_mismatch"]  # no implied ÷12
    assert kinds("Demand charge 12 EUR/kW/month", "ct/kW/month") == ["unit_mismatch"]
    # A per_kwh item takes /kWh as written, and /MWh scaled; nothing else.
    off = {"target": "tariff", "entity": "energy", "field": "periods[offpeak].rate", "page": 1,
           "quote": "Energy charge off-peak 0.11 EUR/kWh"}
    out, _ = _preview(uid, [{**off, "unit": "EUR/kWh"}])
    assert out["cells"][0]["value"] == 0.11
    out, _ = _preview(uid, [{**off, "unit": "EUR/Wh"}])
    assert [g["kind"] for g in out["unmapped"]] == ["unit_mismatch"]
    # A PPA share is a fraction: a percentage is not converted.
    share = {"target": "ppa", "entity": "ppa1", "field": "share", "page": 2, "quote": "Contract term 15 years"}
    out, _ = _preview(uid, [{**share, "unit": "%"}])
    assert [g["kind"] for g in out["unmapped"]] == ["unit_mismatch"]


def test_cdv04_money_is_stated_never_assumed(site):
    uid = _pdf()
    bare = {**PEAK, "field": "periods[peak].rate", "quote": "Peak energy 0.25 per kWh", "unit": "/kWh"}
    out, _ = _preview(uid, [bare])
    assert [g["kind"] for g in out["unmapped"]] == ["money_basis_missing"]
    out, _ = _preview(uid, [bare], currency="EUR")
    assert out["cells"][0]["value"] == 0.25 and out["cells"][0]["currency"] == "EUR"
    _finance()  # the project sets no currency: the stated one is recorded and disclosed
    out, _ = _preview(uid, [bare], currency="GBP")
    assert out["cells"][0]["currency"] == "GBP"
    assert any("set no currency" in note for note in out["cells"][0]["notes"])
    # A PPA price without its money year is not read.
    out, _ = _preview(uid, [EUR_PRICE])
    assert [g["kind"] for g in out["unmapped"]] == ["money_basis_missing"]


def test_cdv05_a_ppa_price_money_year(site):
    from services.library import reference_apply as R

    uid = _pdf()
    ctx = PyPSAService.get_active_context()
    base = R.case_base_year(ctx.network, ctx.solver_state["solver_config"])
    out, args = _preview(uid, [EUR_PRICE], money_year=base)
    assert out["missing"] == [] and [c["path"] for c in out["cells"]] == ["price", "base_year"]
    assert out["cells"][1]["value"] == base and out["cells"][1]["old"] is None
    assert any("not adjusted for inflation" in n for n in out["cells"][0]["notes"])
    _, result, _ = _dispatch(site, "apply_commercial_document_values", args)
    assert result["_edit_receipt"]["outcome"] == "verified"
    stored = _stored()["contracts"][0]
    assert (stored["price"], stored["base_year"]) == (61.5, base)

    # Another year than the case's needs the user's confirmation; copied unadjusted.
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": _commercial()})
    out, args = _preview(uid, [EUR_PRICE], money_year=base - 5)
    assert out["missing"] == ["money_year"]
    cards, result, collector = _dispatch(site, "apply_commercial_document_values", args)
    assert cards == [] and _stored()["contracts"][0]["price"] == 55.0
    out, args = _preview(uid, [EUR_PRICE], money_year=base - 5, confirm=["money_year"])
    _, result, _ = _dispatch(site, "apply_commercial_document_values", args)
    assert _stored()["contracts"][0]["price"] == 61.5  # as stated, not escalated

    # A contract whose prices are in another year: refused, nothing adjusted.
    out, _ = _preview(uid, [EUR_PRICE], money_year=base + 1)
    assert [g["kind"] for g in out["unmapped"]] == ["money_year_mismatch"]

    # Two prices of one contract on one card: one money year, one base_year row.
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": _commercial()})
    floor = {**EUR_PRICE, "field": "floor"}
    out, _ = _preview(uid, [EUR_PRICE, floor], money_year=base)
    assert [c["path"] for c in out["cells"]] == ["price", "floor", "base_year"] and out["unmapped"] == []


def test_cdv06_compare_and_set(client, site, session_ctx):
    from tests.test_chat_tools_dispatch import _bound_to

    assert client.post("/api/projects/P", params={"force": True, "rebind": True}).status_code == 200
    with _bound_to(session_ctx(client)):
        chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": _commercial()})
        _finance(currency="EUR")
        uid = _pdf()
        sha = D.read_pages("P", uid)["source"]["sha256"]
    body = {"upload_id": uid, "citations": [PEAK]}
    card = client.post("/api/document_values/commercial/preview", json=body).json()["card"]
    # The contract changed elsewhere since the card (not a value the card shows).
    r = client.put("/api/simulation/solver_config", json={"commercial": _commercial(
        contracts=[{**PPA, "tenor_years": 12}])})
    assert r.status_code == 200, r.text
    r = client.post("/api/simulation/document_values/apply", json={**body, "source_sha256": sha,
                                                                   "digest": card["digest"]})
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "edit_preview_stale"
    with _bound_to(session_ctx(client)):
        assert _stored()["import_tariff"]["items"][0]["periods"][0]["rate"] == 0.2

    # Inside the apply too: a config that moves between the plan and the write.
    with _bound_to(session_ctx(client)):
        args = {**body, "source_sha256": sha}
        p = DC.plan_active(args)
        from harness import edit_card

        card = edit_card.preview("apply_commercial_document_values", args)
        PyPSAService.get_active_context().solver_state["solver_config"].commercial = _commercial(
            contracts=[{**PPA, "tenor_years": 13}])
        with pytest.raises(HTTPException) as exc:
            DC.apply(p, card, approved_by=None)
        assert exc.value.detail["error_kind"] == "edit_preview_stale"
        assert _stored()["import_tariff"]["items"][0]["periods"][0]["rate"] == 0.2


def test_cdv07_library_pinned_and_missing_items_are_refused_in_place(site):
    uid = _pdf()
    ref = {"kind": "tariff", "id": "lib-t", "version": 3, "hash": "a" * 64}
    PyPSAService.get_active_context().solver_state["solver_config"].commercial = {
        **_commercial(), "import_tariff_ref": ref}
    out, _ = _preview(uid, [PEAK])
    (gap,) = out["unmapped"]
    assert gap["kind"] == "library_pinned" and "v3" in gap["message"]
    assert gap["library"] == {"kind": "tariff", "id": "lib-t", "version": 3}
    PyPSAService.get_active_context().solver_state["solver_config"].commercial = _commercial(
        contracts=[{**PPA, "library_ref": {**ref, "kind": "contract"}}])
    out, _ = _preview(uid, [EUR_PRICE], money_year=2030)
    assert [g["kind"] for g in out["unmapped"]] == ["library_pinned"]

    PyPSAService.get_active_context().solver_state["solver_config"].commercial = _commercial()
    out, _ = _preview(uid, [{**PEAK, "entity": "export"}, {**PEAK, "field": "periods[night].rate"},
                            {**EUR_PRICE, "entity": "ppa9"}], money_year=2030)
    messages = [(g["kind"], g["message"]) for g in out["unmapped"]]
    assert [k for k, _ in messages] == ["component_not_found"] * 3
    assert "Add it in Import tariff first" in messages[0][1] and "Add it in Import tariff first" in messages[1][1]
    assert "Add it in Contracts first" in messages[2][1]


def test_cdv08_a_change_the_preflight_rejects_writes_nothing(site, monkeypatch):
    from services.commercial import preflight

    uid = _pdf()
    real = preflight.commercial_findings

    def findings(n, commercial, **kw):
        out = list(real(n, commercial, **kw))
        rate = commercial["import_tariff"]["items"][0]["periods"][0]["rate"]
        if rate != 0.2:
            out.append(("error", "commercial.binding_invalid", "Link", "import", "test refusal"))
        return out

    monkeypatch.setattr(preflight, "commercial_findings", findings)
    _, args = _preview(uid, [PEAK])
    _, result, collector = _dispatch(site, "apply_commercial_document_values", args)
    assert result is None and "commercial_preflight_failed" in str(collector[-1]["content"])
    assert _stored()["import_tariff"]["items"][0]["periods"][0]["rate"] == 0.2


def test_cdv09_undo_is_not_claimed_and_the_write_is_gated(site):
    from harness import edit_card
    from harness.catalogue import TOOL_ROUTES, safety_tier_for

    uid = _pdf()
    _, args = _preview(uid, [PEAK])
    cards, result, _ = _dispatch(site, "apply_commercial_document_values", args)
    rows = cards[0]["edit_card"]["rows"]
    assert any(r["field"] == "undo" and "does not revert" in r["note"] for r in rows if r["role"] == "note")
    assert result["_edit_receipt"]["undo"]["available"] is False
    assert "does not revert" in result["undo"]
    assert "apply_commercial_document_values" not in chat_tools.UNDO_CAPTURED_TOOLS
    assert "apply_commercial_document_values" in chat_tools._lock_gated_tool_names()
    assert edit_card.is_edit("apply_commercial_document_values")
    assert safety_tier_for("preview_commercial_document_values") == "read"
    assert TOOL_ROUTES["apply_commercial_document_values"] == [("POST", "/api/simulation/document_values/apply")]


def test_cdv10_the_preview_route_is_a_read_and_its_card_is_the_one_the_apply_accepts(client, site, session_ctx):
    import main
    from tests.test_chat_tools_dispatch import _bound_to

    path = "/api/document_values/commercial/preview"
    for prefixes in (main._UNDO_PREFIXES, main._DISPATCH_INVALIDATE_PREFIXES, main._STUDY_EDIT_PREFIXES):
        assert not any(path.startswith(p) for p in prefixes)
    assert not any("/api/simulation/document_values/apply".startswith(p) for p in main._UNDO_PREFIXES)
    assert client.post("/api/projects/P", params={"force": True, "rebind": True}).status_code == 200
    with _bound_to(session_ctx(client)):
        chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": _commercial()})
        _finance(currency="EUR")
        uid = _pdf()
    doc = client.post(path, json={"upload_id": uid}).json()
    assert doc["targets"]["tariff"]["items"][0] == {
        "id": "energy", "unit": "/kWh", "fields": ["periods[peak].rate", "periods[offpeak].rate"]}
    assert doc["targets"]["ppa"][0]["id"] == "ppa1" and "card" not in doc
    body = client.post(path, json={"upload_id": uid, "citations": [PEAK]}).json()
    assert body["plan"]["changed_cells"] == 1
    with _bound_to(session_ctx(client)):
        assert _stored()["import_tariff"]["items"][0]["periods"][0]["rate"] == 0.2
    r = client.post("/api/simulation/document_values/apply", json={
        "upload_id": uid, "citations": [PEAK], "source_sha256": body["document"]["source"]["sha256"],
        "digest": body["card"]["digest"]})
    assert r.status_code == 200, r.text
    assert r.json()["receipt"]["outcome"] == "verified"
    with _bound_to(session_ctx(client)):
        assert _stored()["import_tariff"]["items"][0]["periods"][0]["rate"] == 0.0725


def test_cdv11_provenance_records_the_value_and_speaks_for_it_only_while_it_holds(site):
    """Gatekeeper Q1: (i) outside the library item hash, so a pinned tariff never conflicts
    over provenance; (ii) each entry holds the value it wrote, and `document_source` speaks
    for the stored value only while it still equals it; (iii) an entry whose item is gone
    is dropped on the next write and ignored on read."""
    from models.commercial import CommercialConfig
    from services.commercial import binding
    from services.commercial import hashing as H

    uid = _pdf()
    _, args = _preview(uid, [PEAK, EUR_PRICE], money_year=2030, confirm=["money_year"])
    _dispatch(site, "apply_commercial_document_values", args)
    stored = _stored()
    entry = DC.document_source(stored, "tariff:energy/periods[peak].rate")
    assert entry["value"] == 0.0725 and entry["unit"] == "/kWh" and entry["currency"] == "EUR"
    assert entry["source"].startswith("pdf:") and "p1" in entry["source"] and "72.50 EUR/MWh" in entry["source"]
    assert DC.document_source(stored, "ppa:ppa1/price")["currency_year"] == 2030  # B9 reads it

    # (i) The tariff's library digest ignores provenance: a ref to it binds without conflict.
    cfg = CommercialConfig.model_validate(stored)
    ref = {"kind": "tariff", "id": "lib-t", "version": 1, "hash": H.library_item_digest(cfg.import_tariff)}
    pinned = CommercialConfig.model_validate({**stored, "import_tariff_ref": ref})
    assert pinned.provenance
    out = binding.resolve_tariff_ref(pinned, lambda r: stored["import_tariff"])
    assert out.import_tariff.items[0].periods[0].rate == 0.0725

    # (ii) A later hand edit: the PDF no longer speaks for the value.
    edited = {**stored, "import_tariff": {**TARIFF, "items": [
        {**TARIFF["items"][0], "periods": [{**TARIFF["items"][0]["periods"][0], "rate": 0.09},
                                           TARIFF["items"][0]["periods"][1]]}, TARIFF["items"][1]]}}
    assert DC.document_source(edited, "tariff:energy/periods[peak].rate") is None
    assert DC.document_source(edited, "ppa:ppa1/price") is not None

    # (iii) The contract is deleted: its entry is ignored, and dropped by the next write.
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {**stored, "contracts": []}})
    assert DC.document_source(_stored(), "ppa:ppa1/price") is None
    off = {**PEAK, "field": "periods[offpeak].rate", "quote": "Energy charge off-peak 0.11 EUR/kWh",
           "unit": "EUR/kWh"}
    _, args = _preview(uid, [off])
    _dispatch(site, "apply_commercial_document_values", args)
    assert sorted(_stored()["provenance"]) == ["tariff:energy/periods[offpeak].rate",
                                               "tariff:energy/periods[peak].rate"]


def test_cdv12_undo_last_after_the_apply_leaves_the_config_as_written(site):
    uid = _pdf()
    _, args = _preview(uid, [PEAK])
    _, result, _ = _dispatch(site, "apply_commercial_document_values", args)
    assert result["read_back"] is True
    assert result["_edit_receipt"]["undo"]["available"] is False and "does not revert" in result["undo"]
    # The apply took no network undo step, so undo_last has nothing of it to revert.
    with pytest.raises(HTTPException) as exc:
        chat_tools.undo_last()
    assert exc.value.status_code == 409 and "Nothing to undo" in str(exc.value.detail)
    assert _stored()["import_tariff"]["items"][0]["periods"][0]["rate"] == 0.0725


def test_cdv13_preflight_errors_from_before_the_change_are_card_notes(site, monkeypatch):
    from harness import edit_card
    from services.commercial import preflight

    uid = _pdf()
    real = preflight.commercial_findings
    monkeypatch.setattr(preflight, "commercial_findings", lambda n, c, **kw: [
        *real(n, c, **kw), ("error", "commercial.meter_bypass", "Bus", "x", "an old problem")])
    _, args = _preview(uid, [PEAK])
    card = edit_card.preview("apply_commercial_document_values", args)
    notes = [r["note"] for r in card["rows"] if r["role"] == "note" and r["field"] == "preflight"]
    assert notes == ["Already failing before this change (commercial.meter_bypass): an old problem"]
    # An old finding whose text quotes the edited value is still the old finding.
    monkeypatch.setattr(preflight, "commercial_findings", lambda n, c, **kw: [
        *real(n, c, **kw), ("error", "commercial.meter_bypass", "Bus", "x",
                            f"rate {c['import_tariff']['items'][0]['periods'][0]['rate']}")])
    _, args = _preview(uid, [PEAK])
    _, result, _ = _dispatch(site, "apply_commercial_document_values", args)  # not this change's refusal
    assert result["read_back"] is True
