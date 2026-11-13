"""Business case 08b: the chat tools (GK B (54) plan GO, D1-D9).

`run_business_case`, `get_business_case`, `set_business_case_lines` and
`abort_business_case`; the card (a diff with each price's source, in Guided and
Expert); the price citations through the run intake's own reader; the job,
campaign and envelope wiring.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import HTTPException

from harness import edit_card, edit_card_business_case  # noqa: F401  (the adapter registers)
from harness.session import TURN_AUTHOR_KEY
from services import chat_service, chat_tools, run_intake
from services.business_case import chat_edit
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for

TOOL = "set_business_case_lines"
NAMES = ("run_business_case", "get_business_case", TOOL, "abort_business_case")
_HOST_GET = chat_edit._Host.get  # the real reader, before `bc` stubs it


def _finance():
    from tests.fixtures.investment_case.sam import sam_case as S

    return S.to_finance_case("s2").inputs.model_dump(mode="json")


CUR = "USD"  # the s2 case's currency


def _line(**kw) -> dict:
    base = dict(
        name="product",
        kind="revenue",
        category="product_sale",
        unit="t",
        quantity=10.0,
        price=45.0,
        escalation="opex",
    )
    base.update(kw)
    return base


def _linked(**kw) -> dict:
    return _line(
        name="power",
        unit="MWh",
        quantity=None,
        price=None,
        quantity_source="linked:dc_it_energy@1",
        linked_share=0.8,
        linked_share_by_year={"2028": 0.9},
        **kw,
    )


def _opex(**kw) -> dict:
    base = dict(
        name="maint",
        kind="opex",
        category="maintenance",
        unit="MWh",
        quantity=5.0,
        price=None,
        escalation="opex",
        default_keys={"price": "maint_k"},
        default_units={"price": f"{CUR}/MWh"},
    )
    base.update(kw)
    return base


def _bc(*lines, **kw) -> dict:
    return {"inputs": {"lines": list(lines)}, **kw}


@pytest.fixture
def bc(client, install_network, tmp_path, monkeypatch):
    """A network, the finance inputs, a chat session, and the run intake's host
    holding the acting user's turns (`host.turns`, appended by `_say`)."""
    import routers.results as R
    import routers.simulation as SIM
    from tests.conftest import build_network

    install_network(build_network(), name="bc08b")
    assert SIM.put_finance(SIM.FinanceIn(finance=_finance()), if_match=None)
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    host_dir = tmp_path / "bchost"
    host_dir.mkdir()
    turns: list = []
    owner = str(chat_tools.acting_user_id())

    def host():
        return run_intake.Host(
            name=host_dir.name, dir=host_dir, owner_id=owner, turns=tuple(turns)
        )

    monkeypatch.setattr(chat_edit._Host, "get", lambda self: host())
    monkeypatch.setattr(run_intake, "host_for", lambda ctx, o, p: host())
    yield {"session": session, "turns": turns, "owner": owner, "dir": host_dir}
    chat_tools.set_chat_session(None)
    R.put_business_case_inputs(R.BusinessCaseIn(business_case=None), if_match=None)
    SIM.put_finance(SIM.FinanceIn(finance=None), if_match=None)
    R._state.pop("business_case_report", None)
    R._state.pop("business_case", None)


def _say(env, text, *, owner=None, origin="user") -> str:
    record = {
        "ts": time.time() + len(env["turns"]),
        "session_id": "s-1",
        "user": text,
        "assistant": [],
        TURN_AUTHOR_KEY: owner or env["owner"],
    }
    if origin is not None:
        record[run_intake.ORIGIN_KEY] = origin
    env["turns"].append(record)
    return run_intake.turn_id(record)


def _digest() -> str:
    return chat_tools.get_business_case(detail="inputs")["digest"]


def _cite(tid, quote, line="product", unit=f"{CUR}/t", year=None, **kw) -> dict:
    c = {"line": line, "turn_id": tid, "quote": quote, "unit": unit, **kw}
    if year is not None:
        c["year"] = year
    return c


def _args(business_case, citations=(), if_match=None) -> dict:
    return {
        "business_case": business_case,
        "citations": list(citations),
        "if_match": if_match if if_match is not None else _digest(),
    }


def _refused(fn, *a, **kw) -> dict:
    with pytest.raises(HTTPException) as exc:
        fn(*a, **kw)
    d = exc.value.detail
    d = dict(d) if isinstance(d, dict) else {"detail": d}
    d["status"] = exc.value.status_code
    return d


def _store(env, business_case, citations=()) -> dict:
    return chat_tools.set_business_case_lines(
        _digest(), business_case, citations=list(citations)
    )


def _rows(card) -> dict:
    return {r["label"]: r for r in card["rows"]}


# ── the catalogue (D5) ──────────────────────────────────────────────────────


def test_the_four_tools_their_tiers_routes_and_no_project_argument():
    tools = {t["name"]: t for t in TOOLS if t["name"] in NAMES}
    assert set(tools) == set(NAMES) and set(NAMES) <= set(chat_tools.DISPATCHERS)
    assert {n: safety_tier_for(n) for n in NAMES} == {
        "run_business_case": "execution_long_running",
        "get_business_case": "read",
        TOOL: "write",
        "abort_business_case": "destructive",
    }
    for t in tools.values():
        assert not any(
            "project" in p for p in t["input_schema"].get("properties", {})
        ), t["name"]
    assert tools[TOOL]["input_schema"]["required"] == ["if_match"]
    assert "sold share" in tools[TOOL]["description"].lower()
    assert TOOL_ROUTES["set_business_case_lines"] == [
        ("PUT", "/api/results/business_case/inputs")
    ]
    # Q3: the preset stays out of chat.
    assert not any("/preset" in path for n in NAMES for _, path in TOOL_ROUTES[n])


def test_the_runs_are_never_listed_and_bound_context_is_unchanged():
    from services import run_envelope, run_profiles

    assert {"run_business_case", "abort_business_case"} <= run_envelope.NEVER_LISTED
    assert run_profiles.BOUND_CONTEXT_TOOLS & run_envelope.NEVER_LISTED == {
        "run_eh_study"
    }


def test_the_card_shows_in_guided_and_expert_and_nothing_approves_it_for_the_user():
    from harness import confirm

    assert edit_card.is_edit(TOOL) and TOOL in edit_card.SETTINGS_EDITS
    assert TOOL in confirm.ALWAYS_CARD_TOOLS


def test_campaign_charges_zero_solves():
    from services.adequacy import campaign

    assert "business_case" in campaign.CHARGEABLE
    assert campaign.estimate_solves(None, "business_case") == 0


# ── the request (D3) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("tag", ["", "  ", "*", '"*"', None])
def test_if_match_is_required_and_star_is_refused(bc, tag):
    d = _refused(chat_tools.set_business_case_lines, tag, _bc(_line(price=None)))
    assert d["error_kind"] == "business_case_if_match_required" and d["status"] == 422
    assert chat_tools.get_business_case(detail="inputs")["business_case"] is None


def test_a_stale_digest_is_business_case_changed_through_the_tool(bc):
    stale = _digest()
    _store(bc, _bc(_line(price=None)))
    d = _refused(
        chat_tools.set_business_case_lines, stale, _bc(_line(price=None, quantity=3.0))
    )
    assert d["error_kind"] == "business_case_changed" and d["status"] == 412


def test_a_stale_digest_at_the_route_maps_to_business_case_changed(bc, monkeypatch):
    """The route's own under-lock 412 (the tag moved after the plan) keeps its kind."""
    import routers.results as R

    stale = _digest()
    monkeypatch.setattr(
        chat_edit, "plan", lambda a: {"new": _bc(_line(price=None)), "tag": stale}
    )
    R.put_business_case_inputs(
        R.BusinessCaseIn(business_case=_bc(_line(price=None, quantity=2.0))),
        if_match=None,
    )
    d = _refused(chat_tools.set_business_case_lines, stale, _bc(_line(price=None)))
    assert d["error_kind"] == "business_case_changed" and d["status"] == 412


def test_clear_and_business_case_are_exclusive(bc):
    d = _refused(chat_tools.set_business_case_lines, _digest())
    assert d["error_kind"] == "business_case_clear_invalid"
    d = _refused(
        chat_tools.set_business_case_lines,
        _digest(),
        _bc(_line(price=None)),
        clear=True,
    )
    assert d["error_kind"] == "business_case_clear_invalid"


def test_invalid_inputs_name_the_field(bc):
    d = _refused(
        chat_tools.set_business_case_lines,
        _digest(),
        _bc(_line(price=None, no_such_field=1)),
    )
    assert d["error_kind"] == "business_case_inputs_invalid" and d["errors"]


# ── the card (D1) ───────────────────────────────────────────────────────────


def test_a_first_save_shows_every_value_and_each_price_source(bc):
    tid = _say(bc, "We sell the product at 45 USD/t.")
    bcfg = _bc(_line(), _linked(), _opex(), kw_per_gpu=1.2)
    card = edit_card.preview(TOOL, _args(bcfg, [_cite(tid, "45 USD/t")]))
    rows = _rows(card)
    assert card["kind"] == "settings_edit" and card["complete"] is True
    price = rows["product · Price"]
    assert (price["old"], price["new"], price["unit"], price["change"]) == (
        None,
        45.0,
        f"{CUR}/t",
        "added",
    )
    assert (
        price["price_source"] == "you wrote '45 USD/t'"
        and price["source"]["turn_id"] == tid
    )
    assert rows["power · Sold share"]["new"] == 0.8
    assert rows["power · Sold share 2028"]["new"] == 0.9
    assert rows["maint · Default key price"]["new"] == "maint_k"
    assert rows["maint · Default unit price"]["new"] == f"{CUR}/MWh"
    assert rows["kW per GPU"]["new"] == 1.2
    assert not any("lease" in r["label"].lower() for r in card["rows"])
    # kw_per_gpu, and capacity_unit's stored default: the card shows what is stored
    assert card["says"] == "business case: 3 lines added, 2 settings changed"
    assert rows["Capacity unit"]["new"] == "MW"
    assert chat_tools.get_business_case(detail="inputs")["business_case"] is None, (
        "preview writes nothing"
    )


def test_a_replacement_diffs_old_to_new_and_a_removal_is_never_silent(bc):
    t1 = _say(bc, "Product price is 45 USD/t.")
    _store(bc, _bc(_line(), _linked(), _opex()), [_cite(t1, "45 USD/t")])
    t2 = _say(bc, "Make that 47.5 USD/t from now on.")
    new = _bc(_line(price=47.5), _opex(quantity=6.0))  # `power` removed
    card = edit_card.preview(TOOL, _args(new, [_cite(t2, "47.5 USD/t")]))
    rows = _rows(card)
    assert (rows["product · Price"]["old"], rows["product · Price"]["new"]) == (
        45.0,
        47.5,
    )
    assert rows["product · Price"]["change"] == "changed"
    assert (rows["maint · Quantity"]["old"], rows["maint · Quantity"]["new"]) == (
        5.0,
        6.0,
    )
    removed = [r for r in card["rows"] if r["line"] == "power"]
    assert removed and all(
        r["change"] == "removed" and r["new"] is None for r in removed
    )
    assert {
        "power · Sold share",
        "power · Sold share 2028",
        "power · Quantity source",
    } <= {r["label"] for r in removed}
    assert card["says"] == "business case: 2 lines changed, 1 line removed"


def test_an_unchanged_price_keeps_its_stored_source(bc):
    t1 = _say(bc, "45 USD/t for the product.")
    _store(bc, _bc(_line()), [_cite(t1, "45 USD/t")])
    card = edit_card.preview(
        TOOL, _args(_bc(_line(quantity=12.0)))
    )  # no citation needed
    price = _rows(card)["product · Price"]
    assert (price["change"], price["price_source"], price["old"], price["new"]) == (
        "unchanged",
        "unchanged",
        45.0,
        45.0,
    )


def test_a_preset_default_price_reads_assumed(bc):
    import routers.results as R

    assumed = {
        "price": {
            "key": "maint_k",
            "value": 3.0,
            "unit": f"{CUR}/MWh",
            "source": "pack",
            "source_year": 2024,
            "pack": "pack@1#h",
        }
    }
    R.put_business_case_inputs(
        R.BusinessCaseIn(business_case=_bc(_opex(price=3.0, assumed=assumed))),
        if_match=None,
    )
    card = edit_card.preview(
        TOOL, _args(_bc(_opex(price=3.0, assumed=assumed, quantity=9.0)))
    )
    assert _rows(card)["maint · Price"]["price_source"] == "preset default (assumed)"


def test_an_oversize_card_is_refused_never_cut(bc):
    lines = [_opex(name=f"o{i}", default_keys={}, default_units={}) for i in range(15)]
    d = _refused(edit_card.preview, TOOL, _args(_bc(*lines)))
    assert (
        d["error_kind"] == "business_case_card_too_large"
        and d["rows"] > chat_edit.CARD_ROW_CAP
    )


def test_a_no_op_is_refused(bc):
    _store(bc, _bc(_line(price=None)))
    d = _refused(edit_card.preview, TOOL, _args(_bc(_line(price=None))))
    assert d["error_kind"] == "empty_edit"


# ── each digest-bound value (D2) ────────────────────────────────────────────


def _bound_case(bc):
    tid = _say(bc, "45 USD/t, 46 USD/t at most; 47 USD/t in 2029, else 48 USD/t.")
    cites = [_cite(tid, "45 USD/t"), _cite(tid, "47 USD/t", year=2029)]
    base = _bc(_line(price_by_year={"2029": 47.0}), _linked(), _opex(), kw_per_gpu=1.2)
    return base, cites, tid


def _mut(base, line, **kw):
    import copy

    out = copy.deepcopy(base)
    for ln in out["inputs"]["lines"]:
        if ln["name"] == line:
            ln.update(kw)
    return out


@pytest.mark.parametrize(
    "value",
    [
        "price",
        "price_by_year",
        "unit",
        "linked_share",
        "linked_share_by_year",
        "default_units",
        "default_keys",
        "kw_per_gpu",
    ],
)
def test_each_bound_value_changed_after_the_card_is_edit_preview_stale(bc, value):
    base, cites, tid = _bound_case(bc)
    card = edit_card.preview(TOOL, _args(base, cites))
    digest = card["digest"]
    edit_card.require_current(
        TOOL, _args(base, cites, if_match=_digest()), card
    )  # unchanged: passes
    changed, more = {
        "price": (_mut(base, "product", price=46.0), [_cite(tid, "46 USD/t")]),
        "price_by_year": (
            _mut(base, "product", price_by_year={"2029": 48.0}),
            [_cite(tid, "48 USD/t", year=2029)],
        ),
        "unit": (_mut(base, "product", unit="kg"), []),
        "linked_share": (_mut(base, "power", linked_share=0.7), []),
        "linked_share_by_year": (
            _mut(base, "power", linked_share_by_year={"2028": 0.95}),
            [],
        ),
        "default_units": (
            _mut(base, "maint", default_units={"price": f"{CUR}/kWh"}),
            [],
        ),
        "default_keys": (_mut(base, "maint", default_keys={"price": "other_k"}), []),
        "kw_per_gpu": ({**base, "kw_per_gpu": 1.3}, []),
    }[value]
    if value == "unit":  # the price's unit moves with it; the old quote no longer reads
        tid2 = _say(bc, "45 USD/kg, and 47 USD/kg in 2029.")  # noqa: F841
        more = [
            _cite(tid2, "45 USD/kg", unit=f"{CUR}/kg"),
            _cite(tid2, "47 USD/kg", unit=f"{CUR}/kg", year=2029),
        ]
    keep = [c for c in cites if not any(c.get("year") == m.get("year") for m in more)]
    d = _refused(edit_card.require_current, TOOL, _args(changed, keep + more), card)
    assert d["error_kind"] == "edit_preview_stale" and d["status"] == 409
    assert card["digest"] == digest


def test_a_cited_turn_edited_after_the_card_refuses(bc):
    base, cites, tid = _bound_case(bc)
    card = edit_card.preview(TOOL, _args(base, cites))
    bc["turns"][-1]["user"] += " (edited)"
    d = _refused(edit_card.require_current, TOOL, _args(base, cites), card)
    assert (
        d["error_kind"] == "price_not_from_user" and d["reason"] == "turn_not_citable"
    )


# ── each citation path and refusal (Q1, D2) ─────────────────────────────────


def test_a_new_price_without_a_citation_is_not_from_user(bc):
    d = _refused(chat_tools.set_business_case_lines, _digest(), _bc(_line()))
    assert d["error_kind"] == "price_not_from_user" and d["reason"] == "no_citation"
    assert d["line"] == "product" and d["year"] is None
    assert chat_tools.get_business_case(detail="inputs")["business_case"] is None


def test_a_price_by_year_needs_its_own_citation(bc):
    tid = _say(bc, "45 USD/t.")
    d = _refused(
        chat_tools.set_business_case_lines,
        _digest(),
        _bc(_line(price_by_year={"2029": 47.0})),
        citations=[_cite(tid, "45 USD/t")],
    )
    assert d["error_kind"] == "price_not_from_user" and d["year"] == 2029


@pytest.mark.parametrize(
    "case, reason",
    [
        ("quote_not_in_turn", "quote_not_found"),
        ("another_author", "turn_not_citable"),
        ("not_typed", "turn_not_citable"),
        ("number_differs", "value_differs"),
        ("no_unit", "unit_missing"),
        ("value_supplied", "intake_value_supplied"),
    ],
)
def test_each_citation_refusal(bc, case, reason):
    mine = _say(bc, "We sell it at 45 USD/t.")
    cite = {
        "quote_not_in_turn": _cite(mine, "40 USD/t"),
        "another_author": _cite(_say(bc, "45 USD/t", owner="someone-else"), "45 USD/t"),
        "not_typed": _cite(_say(bc, "45 USD/t", origin=None), "45 USD/t"),
        "number_differs": _cite(_say(bc, "It is 44 USD/t."), "44 USD/t"),
        "no_unit": _cite(_say(bc, "It is 45 per tonne."), "45 per tonne"),
        "value_supplied": _cite(mine, "45 USD/t", value=45.0),
    }[case]
    d = _refused(
        chat_tools.set_business_case_lines, _digest(), _bc(_line()), citations=[cite]
    )
    assert d["error_kind"] == "price_not_from_user" and d["reason"] == reason, d
    assert chat_tools.get_business_case(detail="inputs")["business_case"] is None


def test_the_text_path_writes_and_the_receipt_verifies(bc):
    tid = _say(bc, "The product sells at 45 USD/t.")
    args = _args(_bc(_line()), [_cite(tid, "45 USD/t")])
    from tests.test_edit_card import _dispatch

    frames, cards, result = _dispatch(bc["session"], TOOL, args)
    assert len(cards) == 1, "the card shows in Expert mode too"
    shown = _rows(cards[0]["edit_card"])["product · Price"]
    assert shown["price_source"] == "you wrote '45 USD/t'"
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert result["lines"] == 1 and result["digest"] == _digest()
    stored = chat_tools.get_business_case(detail="inputs")["business_case"]
    assert stored["inputs"]["lines"][0]["price"] == 45.0


def test_the_table_path(bc):
    import io

    from openpyxl import Workbook

    from services import upload_service

    wb = Workbook()
    ws = wb.active
    ws.title = "Prices"
    ws.append(["item", "price (USD/t)"])
    ws.append(["product", 45])
    buf = io.BytesIO()
    wb.save(buf)
    fid = upload_service.add_upload(
        bc["dir"].name,
        buf.getvalue(),
        "p.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        project_dir=bc["dir"],
    ).file_id
    cite = {"line": "product", "upload_id": fid, "sheet": "Prices", "cell": "B2"}
    card = edit_card.preview(TOOL, _args(_bc(_line()), [cite]))
    price = _rows(card)["product · Price"]
    assert (
        price["source"]["kind"] == "table"
        and price["price_source"] == "p.xlsx Prices!B2"
    )
    bad = {**cite, "cell": "A2"}
    d = _refused(edit_card.preview, TOOL, _args(_bc(_line()), [bad]))
    assert d["error_kind"] == "price_not_from_user"


def test_the_pdf_path(bc):
    from services import upload_service
    from tests.test_document_values import pdf_bytes

    fid = upload_service.add_upload(
        bc["dir"].name,
        pdf_bytes([["Offer: product at 45 USD/t."]]),
        "offer.pdf",
        "application/pdf",
        project_dir=bc["dir"],
    ).file_id
    cite = {
        "line": "product",
        "upload_id": fid,
        "page": 1,
        "quote": "product at 45 USD/t",
        "unit": f"{CUR}/t",
    }
    card = edit_card.preview(TOOL, _args(_bc(_line()), [cite]))
    price = _rows(card)["product · Price"]
    assert price["source"]["kind"] == "pdf" and price["price_source"].startswith(
        "offer.pdf, page 1"
    )
    d = _refused(edit_card.preview, TOOL, _args(_bc(_line(price=46.0)), [cite]))
    assert d["error_kind"] == "price_not_from_user" and d["reason"] == "value_differs"


# ── get_business_case (D4) ──────────────────────────────────────────────────


def test_get_before_anything_is_no_data(bc):
    out = chat_tools.get_business_case()
    assert out["status"] == "no_data" and out["kind"] == "business_case"
    assert (
        _refused(chat_tools.get_business_case, detail="pnl")["error_kind"]
        == "business_case_request_invalid"
    )


def test_inputs_carry_the_digest_and_the_citable_turns(bc):
    tid = _say(bc, "45 USD/t.")
    _say(bc, "not mine", owner="someone-else")
    out = chat_tools.get_business_case(detail="inputs")
    assert out["status"] == "not_set" and out["digest"]
    assert out["citable_turns"] == [{"turn_id": tid, "text": "45 USD/t.", "cut": False}]


def _report(**kpis) -> dict:
    return {
        "inputs_digest": "x",
        "ic_assumptions_hash": "h",
        "currency": CUR,
        "basis": {"id": "project_on_its_own"},
        "pnl": {"years": [2027, 2028]},
        "kpis": kpis
        or {"project_npv": {"value": 12.5, "status": "ok", "reason": None}},
        "break_even": {"status": "no_root", "value": None},
        "outputs": {},
        "flags": [],
    }


def test_a_stale_report_reads_stale_with_what_changed_and_no_figures(bc):
    import routers.results as R

    R._state["business_case_report"] = _report()
    out = chat_tools.get_business_case()
    assert out["report"]["stale"] is True and "kpis" not in out
    assert out["report"]["reason"] == "investment_case_missing"
    assert "STALE" in out["figures"]


def test_a_current_report_keeps_not_established_and_says_what_it_cut(bc, monkeypatch):
    import routers.results as R

    monkeypatch.setattr(
        R,
        "_bc_staleness_now",
        lambda stored, ic_key=None: {
            "present": True,
            "stale": False,
            "changed": [],
            "reason": None,
        },
    )
    rep = _report(
        margin={
            "value": None,
            "status": "not_established",
            "reason": "kw_per_gpu_missing",
        },
        npv={"value": 3.0, "status": "ok", "reason": None},
    )
    R._state["business_case_report"] = rep
    out = chat_tools.get_business_case()
    assert out["report"]["stale"] is False
    assert out["kpis"]["margin"] == {
        "value": "not established",
        "status": "not_established",
        "reason": "kw_per_gpu_missing",
    }
    assert out["break_even"]["value"] == "not established" and "truncated" not in out
    rep["outputs"] = {f"out{i}": {"state": "ok", "note": "x" * 200} for i in range(40)}
    out = chat_tools.get_business_case()
    assert out["truncated"] == ["outputs"] and "outputs" in out["truncated_note"]
    assert out["kpis"]["npv"]["value"] == 3.0


def test_inputs_too_large_list_the_cut_lines(bc, monkeypatch):
    monkeypatch.setattr(chat_tools, "_BC_INPUTS_CHARS", 900)
    _store(bc, _bc(*[_line(name=f"l{i}", price=None) for i in range(6)]))
    out = chat_tools.get_business_case(detail="inputs")
    kept = [ln["name"] for ln in out["business_case"]["inputs"]["lines"]]
    assert kept and out["truncated"]["lines"] == [f"l{i}" for i in range(len(kept), 6)]
    assert "do NOT send" in out["warning"]


# ── run and abort (D5, D6) ──────────────────────────────────────────────────


def test_run_maps_the_routes_refusals(bc):
    d = _refused(chat_tools.run_business_case)
    assert d["error_kind"] == "business_case_inputs_missing" and d["status"] == 422
    _store(bc, _bc(_line(price=None)))
    d = _refused(chat_tools.run_business_case)
    assert d["error_kind"] == "investment_case_missing" and d["reason"] == "no_report"


def test_run_while_a_study_runs_is_busy(bc, monkeypatch):
    import routers.results as R

    monkeypatch.setattr(R, "_study_mesh_blocker", lambda key: "a solve is running")
    d = _refused(chat_tools.run_business_case)
    assert d["error_kind"] == "business_case_busy" and d["status"] == 409


def test_abort_without_a_run_is_404(bc):
    import routers.results as R

    R._state.pop("business_case", None)
    assert _refused(chat_tools.abort_business_case)["status"] == 404


def test_a_campaign_run_solves_nothing_and_charges_zero(
    client, install_network, monkeypatch, session_state, session_ctx
):
    """D5: the run values the IC's own solve; no LP runs while it does."""
    import linopy

    from services.pypsa_service import PyPSAService

    from services.adequacy import campaign
    from tests.test_business_case_routes import _bc_poll, _put, _with_ic

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, {"inputs": {"lines": [_line(price=50.0)]}}).status_code == 200

    def no_lp(*a, **k):
        raise AssertionError("the business case ran an LP")

    monkeypatch.setattr(linopy.Model, "solve", no_lp)
    token = PyPSAService._request_ctx.set(session_ctx(client))  # the client's project
    campaign.start("bc 08b no solve", 3)
    try:
        out = chat_tools.run_business_case()
        assert out["status"] == "running" and out["campaign"]["solves_charged"] == 0
        assert _bc_poll(client)["status"] == "done"
        assert campaign.status()["spent_solves"] == 0
    finally:
        campaign.end()
        PyPSAService._request_ctx.reset(token)


def test_the_job_wiring():
    from harness import activity_history, job_adapters as ja, job_holds

    assert ja.STUDY_TOOL_KEYS["run_business_case"] == "business_case"
    assert job_holds.JOB_KINDS["run_business_case"] == job_holds.INPROC
    assert "run_business_case" in activity_history._TOOLS["adequacy_study"]
    handle = ja.StudyJob(
        job_id="ad:x",
        family="adequacy_study",
        study_key="business_case",
        tool_name="run_business_case",
        tool_use_id="tu",
        owner_user_id=None,
        project_uuid=None,
        created_at=0.0,
    )
    assert (
        ja._study_job_view(handle, {"status": "running"})["monitor"]
        == "get_business_case"
    )


def test_wait_to_done_names_the_business_case_tools(
    client, seeded_identity, session_ctx
):
    from harness import job_adapters as ja

    assert (
        client.post(
            "/api/projects/Bc08b", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    ctx = session_ctx(client)
    ja._reset_for_tests()
    owner = str(seeded_identity["user_id"])
    from services.pypsa_service import PyPSAService

    token = PyPSAService._request_ctx.set(
        ctx
    )  # the request context the HTTP path binds
    try:
        with ctx.solver_state_lock:
            ctx.solver_state["business_case"] = {
                "status": "running",
                "started_at": time.time(),
            }
            job = ja.register_study_job(
                study_key="business_case",
                tool_name="run_business_case",
                tool_use_id="tu-bc",
                owner_user_id=owner,
                project_uuid=ctx.project_uuid,
                ctx=ctx,
            )
        running = ja.wait_study_job(job, 0, actor_id=owner, stop=threading.Event())
        assert running["job"]["status"] == "running" and running["terminal"] is False
        assert "abort_business_case" in running["note"]
        assert running["job"]["monitor"] == "get_business_case"
        with ctx.solver_state_lock:
            ctx.solver_state["business_case"]["status"] = "done"
        done = ja.wait_study_job(job, 1, actor_id=owner, stop=threading.Event())
        assert done["terminal"] is True and done["completed"] is True
    finally:
        PyPSAService._request_ctx.reset(token)
        ja._reset_for_tests()


@pytest.mark.parametrize("change", [{"unit": "kg"}, {"money_year": 2030}])
def test_a_price_whose_unit_or_money_year_changes_needs_a_new_citation(bc, change):
    """45 USD/t never silently becomes 45 USD/kg (or 45 in another year's money)."""
    t1 = _say(bc, "45 USD/t for the product.")
    _store(bc, _bc(_line()), [_cite(t1, "45 USD/t")])
    d = _refused(edit_card.preview, TOOL, _args(_bc(_line(**change))))
    assert d["error_kind"] == "price_not_from_user" and d["reason"] == "no_citation"
    unit = change.get("unit", "t")
    t2 = _say(bc, f"Yes, 45 USD/{unit}.")
    card = edit_card.preview(
        TOOL,
        _args(
            _bc(_line(**change)), [_cite(t2, f"45 USD/{unit}", unit=f"{CUR}/{unit}")]
        ),
    )
    price = _rows(card)["product · Price"]
    assert (
        price["change"] == "changed"
        and price["price_source"] == f"you wrote '45 USD/{unit}'"
    )


def test_duplicate_line_names_are_refused_never_shadowed(bc):
    d = _refused(
        edit_card.preview,
        TOOL,
        _args(_bc(_line(price=None), _line(price=None, quantity=1.0))),
    )
    assert (
        d["error_kind"] == "business_case_inputs_invalid" and "product" in d["message"]
    )


def test_an_envelope_never_admits_the_run_on_a_model_turn():
    """#1123 / SH-14 B2: no runs entry, the harness's own or a profile's, lists it."""
    from services import run_envelope

    for tool in ("run_business_case", "abort_business_case"):
        with pytest.raises(run_envelope.EnvelopeError):
            run_envelope._check_run({"tool": tool})


def test_an_int_and_a_float_price_of_one_value_are_unchanged(bc):
    t1 = _say(bc, "45 USD/t.")
    _store(bc, _bc(_line(price=45.0)), [_cite(t1, "45 USD/t")])
    card = edit_card.preview(TOOL, _args(_bc(_line(price=45, quantity=11.0))))
    assert _rows(card)["product · Price"]["change"] == "unchanged"


# ── the diff, the request and the read-back, at their edges ─────────────────


def test_a_number_compares_by_value_but_a_flag_or_a_text_never_equals_one():
    assert chat_edit.rows({"k": 1}, {"k": 1.0}, [], None) == []
    for old, new in ((True, 1), (1, True), (1, "1"), (1, 2)):
        (row,) = chat_edit.rows({"k": old}, {"k": new}, [], None)
        assert row["change"] == "changed", (old, new)
    # An emptied list is a removal, never a change to [].
    (row,) = chat_edit.rows({"k": [1]}, {"k": []}, [], None)
    assert (row["change"], row["new"]) == ("removed", None)
    assert chat_edit.says([]) == "business case: no change"


def test_a_case_level_setting_inside_inputs_shows_under_its_own_name(bc):
    _store(bc, {"inputs": {"lines": [_line(price=None)], "receivable_days": 30.0}})
    new = {"inputs": {"lines": [_line(price=None)], "receivable_days": 45.0}}
    card = edit_card.preview(TOOL, _args(new))
    (row,) = card["rows"]
    assert (row["label"], row["old"], row["new"], row["unit"], row["change"]) == (
        "Receivable days",
        30.0,
        45.0,
        "",
        "changed",
    )
    assert row["path"] == ["inputs", "receivable_days"]
    assert card["says"] == "business case: 1 setting changed"


def test_a_changed_setting_shows_old_and_new(bc):
    _store(bc, _bc(_line(price=None), kw_per_gpu=1.2))
    card = edit_card.preview(TOOL, _args(_bc(_line(price=None), kw_per_gpu=1.3)))
    (row,) = card["rows"]
    assert (row["label"], row["old"], row["new"]) == ("kW per GPU", 1.2, 1.3)


def test_a_quantity_reads_in_its_line_unit_and_other_values_carry_none(bc):
    card = edit_card.preview(
        TOOL, _args(_bc(_opex(quantity_by_year={"2029": 7.0}), _linked()))
    )
    rows = _rows(card)
    assert rows["maint · Quantity"]["unit"] == "MWh"
    assert rows["maint · Quantity 2029"]["unit"] == "MWh"
    for label in ("power · Sold share", "maint · Kind", "maint · Default key price"):
        assert rows[label]["unit"] == "", label


def test_a_removed_price_reads_in_the_case_currency_or_a_placeholder():
    old = _bc(_line(), _line(name="fee", unit="lump", price=9.0))
    for finance, cur in ((None, "currency"), (_finance(), CUR)):
        rows = _rows({"rows": chat_edit.rows(old, None, [], finance)})
        assert rows["product · Price"]["unit"] == f"{cur}/t"
        assert rows["fee · Price"]["unit"] == cur
        assert rows["product · Price"]["price_source"] == "removed"


def test_a_changed_line_shows_only_what_changed_and_its_prices(bc):
    import routers.results as R

    fee = _line(name="fee", unit="lump", price=9.0)
    R.put_business_case_inputs(
        R.BusinessCaseIn(business_case=_bc(_line(), fee)), if_match=None
    )
    card = edit_card.preview(TOOL, _args(_bc(_line(quantity=12.0), fee)))
    # `fee` is unchanged: none of its rows, not even its price, shows.
    assert set(_rows(card)) == {"product · Quantity", "product · Price"}
    assert card["says"] == "business case: 1 line changed"


def test_only_the_assumed_price_reads_preset_default(bc):
    import routers.results as R

    assumed = {
        "price": {
            "key": "maint_k",
            "value": 3.0,
            "unit": f"{CUR}/MWh",
            "source": "pack",
            "source_year": 2024,
            "pack": "pack@1#h",
        }
    }
    line = _opex(price=3.0, assumed=assumed, price_by_year={"2029": 3.5})
    R.put_business_case_inputs(R.BusinessCaseIn(business_case=_bc(line)), if_match=None)
    card = edit_card.preview(TOOL, _args(_bc({**line, "quantity": 9.0})))
    rows = _rows(card)
    assert rows["maint · Price"]["price_source"] == "preset default (assumed)"
    assert rows["maint · Price 2029"]["price_source"] == "unchanged"


def test_clear_shows_every_removed_value_and_needs_something_stored(bc):
    _store(bc, _bc(_line(price=None)))
    card = edit_card.preview(TOOL, {"clear": True, "if_match": _digest()})
    assert card["rows"] and all(
        r["change"] == "removed" and r["new"] is None for r in card["rows"]
    )
    assert card["says"].startswith("business case: 1 line removed")
    chat_tools.set_business_case_lines(_digest(), clear=True)
    assert chat_tools.get_business_case(detail="inputs")["business_case"] is None
    d = _refused(edit_card.preview, TOOL, {"clear": True, "if_match": _digest()})
    assert d["error_kind"] == "empty_edit" and "nothing to clear" in d["message"]


def test_a_business_case_that_is_not_an_object_is_refused(bc):
    d = _refused(chat_tools.set_business_case_lines, _digest(), [_line(price=None)])
    assert d["error_kind"] == "business_case_inputs_invalid"
    assert d["message"] == "`business_case` is an object"


def test_the_card_caps_are_exact(bc, monkeypatch):
    args = _args(_bc(_opex()))
    out = chat_edit.plan(args)["rows"]
    n, chars = len(out), len(chat_edit.json.dumps(out, default=str))
    assert n < chat_edit.CARD_ROW_CAP and chars < chat_edit.CARD_CHARS
    for cap, at in (("CARD_ROW_CAP", n), ("CARD_CHARS", chars)):
        with monkeypatch.context() as m:
            m.setattr(chat_edit, cap, at)
            assert chat_edit.plan(args)["rows"] == out, cap
            m.setattr(chat_edit, cap, at - 1)
            d = _refused(chat_edit.plan, args)
            assert d["error_kind"] == "business_case_card_too_large", cap
            assert d["rows"] == n


def test_a_citation_counts_only_for_its_own_line(bc):
    tid = _say(bc, "45 USD/t.")
    other = _cite(tid, "45 USD/t", line="other")
    d = _refused(edit_card.preview, TOOL, _args(_bc(_line()), ["junk", other]))
    assert d["error_kind"] == "price_not_from_user" and d["reason"] == "no_citation"
    card = edit_card.preview(
        TOOL, _args(_bc(_line()), ["junk", _cite(tid, "45 USD/t")])
    )
    assert _rows(card)["product · Price"]["source"]["turn_id"] == tid


def test_the_intake_host_is_read_once_and_only_for_a_saved_project(bc, monkeypatch):
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(chat_edit._Host, "get", _HOST_GET)
    tid = _say(bc, "45 USD/t; 47 USD/t in 2029.")
    args = _args(
        _bc(_line(price_by_year={"2029": 47.0})),
        [_cite(tid, "45 USD/t"), _cite(tid, "47 USD/t", year=2029)],
    )
    ctx = PyPSAService.get_active_context()
    assert not getattr(ctx, "project_uuid", None)
    d = _refused(edit_card.preview, TOOL, args)
    assert d["error_kind"] == "price_not_from_user" and d["reason"] == "not_found"
    calls = []
    stub = run_intake.host_for
    monkeypatch.setattr(ctx, "project_uuid", "p-1", raising=False)
    monkeypatch.setattr(
        run_intake, "host_for", lambda c, o, p: calls.append(p) or stub(c, o, p)
    )
    card = edit_card.preview(TOOL, args)
    assert {"product · Price", "product · Price 2029"} <= set(_rows(card))
    assert calls == ["p-1"]


def test_the_receipt_reads_back_a_removed_line_and_a_nested_setting_by_path():
    row = {"path": ["inputs", "lines", "power", "linked_share"]}
    assert chat_edit.read_cell(row, _bc(_linked())) == 0.8
    assert chat_edit.read_cell(row, _bc(_line())) is None
    assert chat_edit.read_cell({"path": ["a", "b", "c"]}, {"a": {"b": {"c": 5}}}) == 5
