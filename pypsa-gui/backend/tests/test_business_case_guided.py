"""Business case 14: "Is this plant worth building?" (GK B (56) plan GO, B1-B9).

`apply_business_case_preset` (the template's preset, every default filled by the server),
the report's list of assumed defaults (stored report, workbook sheet, chat summary), and
the verdict the chat summary carries.
"""

from __future__ import annotations

import copy
import math

import pytest

from harness import confirm, edit_card, edit_card_business_case  # noqa: F401  (adapters)
from services import chat_tools
from services.business_case import presets as P
from services.business_case import preset_apply
from services.business_case.verdict import verdict
from services.chat_tools_schema import TOOL_ROUTES, TOOLS, safety_tier_for
from tests.test_business_case_chat_tools import _cite, _digest, _refused, _say, bc  # noqa: F401

TOOL = "apply_business_case_preset"


@pytest.fixture
def template(monkeypatch):
    """The active project's template id, as `_active_template_meta` reads it."""
    import routers.results as R

    current = {"id": "eh_datacenter"}
    monkeypatch.setattr(R, "_active_template_meta", lambda: dict(current))
    return current


def _args(**kw) -> dict:
    return {"if_match": _digest(), **kw}


def _rows(card) -> dict:
    return {r["label"]: r for r in card["rows"]}


def _stored() -> dict:
    return chat_tools.get_business_case(detail="inputs")["business_case"]


# ── the catalogue ───────────────────────────────────────────────────────────


def test_the_tool_is_a_carded_write_on_the_preset_and_inputs_routes():
    tool = next(t for t in TOOLS if t["name"] == TOOL)
    assert tool["input_schema"]["required"] == ["if_match"]
    assert set(tool["input_schema"]["properties"]) == {"if_match", "variant", "rows"}
    assert safety_tier_for(TOOL) == "write"
    assert TOOL_ROUTES[TOOL] == [
        ("GET", "/api/results/business_case/preset"),
        ("PUT", "/api/results/business_case/inputs"),
    ]
    assert TOOL in confirm.ALWAYS_CARD_TOOLS and TOOL in edit_card.ADAPTERS


# ── B2: only members of the server's own lists ──────────────────────────────


@pytest.mark.parametrize(
    "kw",
    [{}, {"variant": "gpu"}, {"variant": ["gpu_cloud"]},
     {"variant": "gpu_cloud", "rows": ["Staff"]}],
)
def test_a_variant_template_refuses_anything_but_one_of_its_variants(bc, template, kw):
    d = _refused(preset_apply.plan, _args(**kw))
    assert d["error_kind"] == "preset_variant_required" and d["status"] == 422
    assert d["variants"] == {"gpu_cloud": "GPU cloud (GPU-hour)",
                             "colocation": "Colocation (wholesale)"} or set(
        d["variants"]) == {"gpu_cloud", "colocation"}


@pytest.mark.parametrize(
    "kw",
    [{}, {"rows": []}, {"rows": ["Staff", "Staff"]}, {"rows": ["staff"]}, {"rows": ("Staff",)},
     {"rows": ["Staff", 3]}, {"rows": "Staff"}, {"rows": ["Staff"], "variant": "x"}],
)
def test_a_blank_table_refuses_anything_but_offered_labels_each_once(bc, template, kw):
    template["id"] = "eh_site"
    d = _refused(preset_apply.plan, _args(**kw))
    assert d["error_kind"] == "preset_rows_required" and d["status"] == 422
    assert d["offered"] == P.offered_labels("eh_site") and "Staff" in d["offered"]


def test_a_template_with_no_preset_offers_the_generic_rows(bc, template):
    template["id"] = "eh_ipp"
    assert not P.has_preset("eh_ipp")
    d = _refused(preset_apply.plan, _args())
    assert d["error_kind"] == "preset_rows_required"
    assert d["offered"] == P.offered_labels("eh_site")
    out = preset_apply.plan(_args(rows=["Product sales", "Staff"]))
    assert [ln["name"] for ln in out["new"]["inputs"]["lines"]] == ["Product sales", "Staff"]


def test_extra_argument_keys_never_reach_the_write(bc, template):
    plain = preset_apply.plan(_args(variant="gpu_cloud"))
    forged = copy.deepcopy(plain["new"])
    forged["inputs"]["lines"][1]["price"] = 1.0
    extra = preset_apply.plan(_args(
        variant="gpu_cloud", business_case=forged, price=1.0,
        assumed={"price": {"value": 1.0}}, citations=[{"line": "x"}]))
    assert extra["new"] == plain["new"] and extra["rows"] == plain["rows"]


# ── the other refusals, each with its status ────────────────────────────────


def test_a_project_not_made_from_a_template_is_refused(bc, template):
    template.clear()
    d = _refused(preset_apply.plan, _args(variant="gpu_cloud"))
    assert d["error_kind"] == "template_not_known" and d["status"] == 422


def test_a_stale_if_match_is_business_case_changed(bc, template):
    d = _refused(preset_apply.plan, {"if_match": '"0" * 8', "variant": "gpu_cloud"})
    assert d["error_kind"] == "business_case_changed" and d["status"] == 412


@pytest.mark.parametrize("tag", ["", "*", None])
def test_if_match_is_required(bc, template, tag):
    d = _refused(preset_apply.plan, {"if_match": tag, "variant": "gpu_cloud"})
    assert d["error_kind"] == "business_case_if_match_required" and d["status"] == 422


def test_without_finance_inputs_the_currency_is_missing(bc, template):
    import routers.simulation as SIM

    SIM.put_finance(SIM.FinanceIn(finance=None), if_match=None)
    d = _refused(preset_apply.plan, _args(variant="gpu_cloud"))
    assert d["error_kind"] == "finance_inputs_missing" and d["status"] == 422


def test_a_moved_pack_is_preset_invalid(bc, template, monkeypatch):
    def moved(_f):
        raise P.PresetInvalid("eh_datacenter: pack 'a' is now 'b'")

    monkeypatch.setattr(P, "_pack", moved)
    d = _refused(preset_apply.plan, _args(variant="gpu_cloud"))
    assert d["error_kind"] == "preset_invalid" and d["status"] == 422


# ── B1: the card shows (and binds) everything the write stores ──────────────


def test_the_card_shows_variant_pack_every_line_value_source_and_kept_settings(
    bc, template
):
    import routers.results as R

    tid = _say(bc, "It sells at 45 USD/t.")
    chat_tools.set_business_case_lines(
        _digest(),
        {"inputs": {"lines": [dict(name="old line", kind="revenue",
                                   category="product_sale", unit="t", quantity=1.0,
                                   price=45.0, escalation="opex")]},
         "kw_per_gpu": 1.2, "break_even_line": "old line"},
        citations=[_cite(tid, "45 USD/t", line="old line")],
    )
    card = edit_card.preview(TOOL, _args(variant="gpu_cloud"))
    rows = _rows(card)
    assert rows["preset"]["new"].startswith("eh_datacenter preset 'gpu_cloud'")
    stamp = P.pinned_pack("eh_datacenter")
    assert rows["defaults pack"]["new"] == stamp
    assert rows["old line"]["change"] == "removed" and "45" in rows["old line"]["old"]
    assert rows["kw per gpu"]["change"] == "kept" and rows["kw per gpu"]["new"] == 1.2
    assert rows["break-even line"]["old"] == "old line"
    assert rows["break-even line"]["new"] == "GPU-hours"
    assert rows["GPU-hours · price"]["new"] == preset_apply.EMPTY
    assert "linked:it_energy@1" in rows["GPU-hours"]["new"]
    assert rows["Insurance · quantity"]["new"] == 0.005
    assert rows["Insurance · quantity"]["unit"] == "share/year"
    src = rows["Insurance · quantity source"]["new"]
    assert src.startswith("2026, assumed: ")  # the year first: a long source is cut after it
    new = preset_apply.plan(_args(variant="gpu_cloud"))["new"]
    for ln in new["inputs"]["lines"]:  # every stored value and default is on the card
        assert ln["name"] in rows
        for col, a in (ln.get("assumed") or {}).items():
            assert rows[f"{ln['name']} · {col.replace('_', ' ')}"]["new"] == ln[col]
            assert str(a["source_year"]) in rows[f"{ln['name']} · {col.replace('_', ' ')} source"]["new"]
    assert R._state["solver_config"].business_case["inputs"]["lines"][0]["name"] == "old line"


def test_a_pack_change_after_the_card_is_edit_preview_stale(bc, template, monkeypatch):
    card = edit_card.preview(TOOL, _args(variant="gpu_cloud"))
    edit_card.require_current(TOOL, _args(variant="gpu_cloud"), card)  # unchanged: passes
    monkeypatch.setattr(P, "pinned_pack", lambda _t, **_k: "other@2:sha256:" + "0" * 64)
    d = _refused(edit_card.require_current, TOOL, _args(variant="gpu_cloud"), card)
    assert d["error_kind"] == "edit_preview_stale" and d["status"] == 409


def test_a_template_change_after_the_card_is_refused(bc, template):
    card = edit_card.preview(TOOL, _args(variant="gpu_cloud"))
    template["id"] = "eh_h2_hub"
    d = _refused(edit_card.require_current, TOOL, _args(variant="gpu_cloud"), card)
    assert d["error_kind"] == "preset_variant_required"


# ── the write, through the same PUT ─────────────────────────────────────────


def test_apply_writes_through_the_put_and_the_receipt_verifies(bc, template, monkeypatch):
    import routers.results as R
    from tests.test_edit_card import _dispatch

    seen = []
    put = R.put_business_case_inputs
    monkeypatch.setattr(R, "put_business_case_inputs",
                        lambda body, if_match: seen.append(if_match) or put(body, if_match))
    args = _args(variant="gpu_cloud")
    _f, cards, shown = _dispatch(bc["session"], TOOL, args)
    assert len(cards) == 1 and seen
    # A preset card's receipt is long, so the chat spills the result (receipt first).
    assert "to_ask" in shown["spill"]["shape"] and "_edit_receipt" in shown["spill"]["shape"]
    receipt = edit_card.receipt(cards[0]["edit_card"], args)
    assert receipt["outcome"] == "verified"
    stored = _stored()
    assert stored["inputs"]["preset"] == "eh_datacenter.gpu_cloud@1"
    assert stored["break_even_line"] == "GPU-hours"


def test_the_result_names_what_to_ask_and_each_default_compactly(bc, template):
    import json

    result = chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    assert result["digest"] == _digest() and result["lines"] == 9
    asks = {(a.get("line") or a.get("setting"), a.get("column")) for a in result["to_ask"]}
    assert ("GPU-hours", "price") in asks and ("kw_per_gpu", None) in asks
    assert ("IT hardware", "price") in asks and ("IT hardware", "quantity") in asks
    assert ("Insurance", "share") not in asks  # a default is offered, never asked
    assert result["assumed_total"] == len(result["assumed"]) >= 4
    assert all("(2026; " in a for a in result["assumed"])
    assert len(json.dumps(result, default=str)) < 4000, "the tool's own part fits one result"
    summary = chat_tools.get_business_case()
    assert summary["to_ask"] == result["to_ask"]


def _config():
    import routers.results as R

    return copy.deepcopy(R._state["solver_config"].business_case)


def test_a_price_the_user_never_gave_stays_refused_after_the_preset(bc, template):
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    before = _config()
    d = _refused(chat_tools.set_business_case_lines, _digest(),
                 set={"GPU-hours": {"price": 2.5}})
    assert d["error_kind"] == "price_not_from_user" and d["status"] == 422
    tid = _say(bc, "Maybe around three dollars, I am not sure.")
    d = _refused(chat_tools.set_business_case_lines, _digest(),
                 set={"GPU-hours": {"price": 2.5}},
                 citations=[_cite(tid, "2.5 USD", line="GPU-hours", unit="USD/GPU_h")])
    assert d["error_kind"] == "price_not_from_user"
    assert _config() == before, "nothing is written"


def test_keep_all_leaves_each_default_assumed(bc, template):
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    before = _config()
    tid = _say(bc, "GPU-hours sell at 2.5 USD/GPU_h.")
    chat_tools.set_business_case_lines(
        _digest(), set={"GPU-hours": {"price": 2.5}},
        citations=[_cite(tid, "2.5 USD/GPU_h", line="GPU-hours", unit="USD/GPU_h")])
    after = _config()
    # S2: exactly that cell changed; every other value, default and line is ==.
    before["inputs"]["lines"][0]["price"] = 2.5
    assert after == before
    lines = {ln["name"]: ln for ln in after["inputs"]["lines"]}
    assert lines["Insurance"]["assumed"]["quantity"]["status"] == "assumed"
    assert lines["Insurance"]["assumed"]["quantity"]["source_year"] == 2026


def test_a_two_cell_set_changes_exactly_those_cells(bc, template):
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    before = _config()
    tid = _say(bc, "2.5 USD/GPU_h; we buy 1000 units of IT hardware.")
    out = preset_card = edit_card.preview("set_business_case_lines", {
        "if_match": _digest(), "set": {"GPU-hours": {"price": 2.5},
                                       "IT hardware": {"quantity": 1000}},
        "citations": [_cite(tid, "2.5 USD/GPU_h", line="GPU-hours", unit="USD/GPU_h")]})
    changed = {(r["line"], r["label"]) for r in out["rows"] if r["change"] != "unchanged"}
    assert changed == {("GPU-hours", "GPU-hours · Price"),
                       ("IT hardware", "IT hardware · Quantity")}, preset_card["rows"]
    assert not any(r["change"] == "removed" for r in out["rows"])
    chat_tools.set_business_case_lines(
        _digest(), set={"GPU-hours": {"price": 2.5}, "IT hardware": {"quantity": 1000}},
        citations=[_cite(tid, "2.5 USD/GPU_h", line="GPU-hours", unit="USD/GPU_h")])
    before["inputs"]["lines"][0]["price"] = 2.5
    before["inputs"]["lines"][2]["quantity"] = 1000.0
    assert _config() == before


def test_a_non_price_cell_over_a_default_needs_the_users_words(bc, template):
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    before = _config()
    for value in (0.004, 0.005):  # equal to the default still needs the user's word (S3)
        d = _refused(chat_tools.set_business_case_lines, _digest(),
                     set={"Insurance": {"quantity": value}})
        assert d["error_kind"] == "price_not_from_user" and d["status"] == 422
        assert d["reason"] == "no_citation"
        assert _config() == before
    tid = _say(bc, "Insurance is 0.004 share/year of capex.")
    chat_tools.set_business_case_lines(
        _digest(), set={"Insurance": {"quantity": 0.004}},
        citations=[_cite(tid, "0.004 share/year", line="Insurance", unit="share/year",
                         column="quantity")])
    ins = {ln["name"]: ln for ln in _config()["inputs"]["lines"]}["Insurance"]
    assert ins["quantity"] == 0.004 and "quantity" not in ins["assumed"]


def test_a_money_year_over_a_price_default_claims_the_price_too(
    bc, template, monkeypatch
):
    monkeypatch.setattr(preset_apply, "_currency", lambda _cfg: "EUR")  # the pack's money
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    rows = {ln["name"]: ln for ln in _config()["inputs"]["lines"]}
    assert "price" in rows["Facility, shell and M&E"]["assumed"]
    tid = _say(bc, "Use 2025 money.")
    d = _refused(chat_tools.set_business_case_lines, _digest(),
                 set={"Facility, shell and M&E": {"money_year": 2025}},
                 citations=[_cite(tid, "2025", line="Facility, shell and M&E",
                                  unit="year", column="money_year")])
    assert d["error_kind"] == "price_not_from_user" and d["reason"] == "no_citation"
    assert "price" in d["message"]


@pytest.mark.parametrize(
    "kw, kind, status",
    [
        ({"set": {"No such line": {"price": 1.0}}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"unit": "kg"}}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"assumed": {}}}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"price": 1.0}}, "business_case": {"inputs": {}}},
         "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"price": 1.0}}, "clear": True},
         "business_case_set_invalid", 422),
        ({}, "business_case_clear_invalid", 422),
        ({"set": {}}, "empty_edit", 422),
        ({"set": ["GPU-hours"]}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"price": math.nan}}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"price": math.inf}}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"price": True}}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"price_by_year": {"x": 1.0}}}},
         "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {}}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"money_year": 2025.5}}}, "business_case_set_invalid", 422),
        ({"set": {"kw_per_gpu": -1.0}}, "business_case_inputs_invalid", 422),
        ({"set": {"break_even_line": 3}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"price": 10**400}}}, "business_case_set_invalid", 422),
        ({"set": {"kw_per_gpu": 10**400}}, "business_case_set_invalid", 422),
        ({"set": {"GPU-hours": {"price_by_year": {"\u00b2": 1.0}}}},
         "business_case_set_invalid", 422),
    ],
)
def test_each_bad_set_is_a_typed_refusal_and_writes_nothing(bc, template, kw, kind, status):
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    before = _config()
    d = _refused(chat_tools.set_business_case_lines, _digest(), **kw)
    assert (d["error_kind"], d["status"]) == (kind, status), d
    assert _config() == before


def test_a_set_on_a_stale_digest_is_business_case_changed(bc, template):
    stale = _digest()
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    d = _refused(chat_tools.set_business_case_lines, stale,
                 set={"GPU-hours": {"price": 1.0}})
    assert d["error_kind"] == "business_case_changed" and d["status"] == 412


def test_a_set_and_the_whole_object_store_the_same_and_show_the_same_rows(bc, template):
    template["id"] = "eh_microgrid"
    chat_tools.apply_business_case_preset(_digest(), variant="microgrid")
    start = _config()
    tid = _say(bc, "Residential pays 0.25 USD/MWh.")
    cite = [_cite(tid, "0.25 USD/MWh", line="Energy tariff, residential",
                  unit="USD/MWh")]
    patch = {"Energy tariff, residential": {"price": 0.25}}
    whole = copy.deepcopy(start)
    next(ln for ln in whole["inputs"]["lines"]
         if ln["name"] == "Energy tariff, residential")["price"] = 0.25
    by_set = edit_card.preview("set_business_case_lines",
                               {"if_match": _digest(), "set": patch, "citations": cite})
    by_whole = edit_card.preview("set_business_case_lines",
                                 {"if_match": _digest(), "business_case": whole,
                                  "citations": cite})
    assert by_set["rows"] == by_whole["rows"]
    chat_tools.set_business_case_lines(_digest(), set=patch, citations=cite)
    via_set = _config()
    chat_tools.set_business_case_lines(_digest(), business_case=start)
    chat_tools.set_business_case_lines(_digest(), business_case=whole, citations=cite)
    assert _config() == via_set


def test_a_linked_preset_line_the_ic_ledger_prices_is_refused_at_run(bc, template):
    """B3: the clash is the run's own check (`_refuse_clashes`), the same for a preset
    stored here as for lines set by hand."""
    from models.business_case import BusinessCaseInputs
    from services.business_case import linked_outputs as L
    from services.finance.business_case import DoubleCount, build_business_case
    from services.finance.case import TemplateLine
    from tests.test_business_case_engine import _ic

    template["id"] = "eh_microgrid"
    chat_tools.apply_business_case_preset(_digest(), variant="microgrid")
    raw = _stored()["inputs"]
    for d in raw["lines"]:  # the asked values a run needs; the clash is refused before them
        if d["kind"] == "revenue":
            d.update(price=10.0, indexation=0.0)
    ln = next(x for x in BusinessCaseInputs.model_validate(raw).lines
              if x.quantity_source.startswith("linked:served_load"))
    inputs = BusinessCaseInputs(lines=[ln])
    ic = _ic(lines=(TemplateLine("eaas", "eaas_fee", 120.0, "export"),))
    out = L.Series(ln.quantity_source.split(":", 1)[1].rsplit("@", 1)[0], "MWh",
                   {None: 10.0}, {})
    with pytest.raises(DoubleCount) as e:
        build_business_case(ic, inputs, {x.name: out for x in inputs.lines
                                         if x.source == "linked"})
    assert e.value.stream == "eaas_fee"


# ── B4: every default the report relies on ──────────────────────────────────


def _assumed_inputs():
    from models.business_case import BusinessCaseInputs

    return BusinessCaseInputs.model_validate(
        P.preset("eh_datacenter", "gpu_cloud", currency="EUR").model_dump())


def test_the_report_lists_every_assumed_default_with_source_and_year():
    from services.business_case.runner import assumed_defaults

    inputs = _assumed_inputs()
    rows = assumed_defaults(inputs)
    want = sum(len(ln.assumed) for ln in inputs.lines)
    assert len(rows) == want >= 4
    for r in rows:
        assert r["source"] and isinstance(r["source_year"], int) and r["pack"]
        assert r["line"] in {ln.name for ln in inputs.lines}
    assert assumed_defaults(type(inputs)()) == []


def test_the_workbook_sheet_has_the_assumed_defaults_table():
    from openpyxl import Workbook

    from services.results_workbook.business_case import write_business_case_sheet

    report = {"basis": {"label": "the project on its own"}, "kpis": {}, "break_even": {},
              "pnl": {}, "assumed": [{"line": "Insurance", "column": "quantity",
                                      "value": 0.005, "unit": "share/year",
                                      "source": "assumed; X", "source_year": 2026}]}
    wb = Workbook()
    ws = wb[write_business_case_sheet(wb, report, {"stale": False})]
    cells = [[c.value for c in row] for row in ws.iter_rows()]
    at = next(i for i, r in enumerate(cells) if r[0] == "Assumed default")
    assert cells[at + 1][:4] == ["Insurance (quantity)", 0.005, "share/year",
                                 "assumed; X, 2026 (assumed)"]
    report["assumed"] = []
    wb = Workbook()
    ws = wb[write_business_case_sheet(wb, report, {"stale": False})]
    cells = [[c.value for c in row] for row in ws.iter_rows()]
    at = next(i for i, r in enumerate(cells) if r[0] == "Assumed default")
    assert cells[at + 1][0] == "none: every value is the user's"


def test_the_summary_carries_the_assumed_list_its_total_and_the_verdict(bc, monkeypatch):
    from tests.test_business_case_chat_tools import _report

    import routers.results as R

    rep = _report(project_npv={"value": 10.0, "status": "ok"})
    rep["assumed"] = [{"line": f"L{i}", "column": "price", "value": 1.0, "unit": "EUR/MW",
                       "source": "S", "source_year": 2026} for i in range(3)]
    R._state["business_case_report"] = rep
    monkeypatch.setattr(R, "_bc_staleness_now", lambda s, ic_key=None: {"stale": False})
    out = chat_tools.get_business_case()
    assert out["assumed_total"] == 3
    assert out["assumed"][0] == "L0 price: 1 EUR/MW (assumed; S, 2026)"
    assert out["verdict"]["status"] == "worth_building"
    assert out["verdict"]["rests_on_defaults"] == ["L0", "L1", "L2"]


# ── B5: the verdict ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "npv, want",
    [
        ({"status": "ok", "value": 0.0}, "worth_building"),
        ({"status": "ok", "value": 1.0}, "worth_building"),
        ({"status": "ok", "value": -1e-9}, "not_worth_building"),
        ({"status": "ok", "value": math.nan}, "not_established"),
        ({"status": "ok", "value": math.inf}, "not_established"),
        ({"status": "ok", "value": None}, "not_established"),
        ({"status": "ok", "value": True}, "not_established"),
        ({"status": "not_established", "value": 5.0, "reason": "tax"}, "not_established"),
        (None, "not_established"),
    ],
)
def test_the_verdict_reads_only_an_established_project_npv(npv, want):
    kpis = {} if npv is None else {"project_npv": npv}
    v = verdict({"kpis": kpis})
    assert v["status"] == want
    if npv and npv.get("reason"):
        assert v["reason"] == "tax"


@pytest.mark.parametrize(
    "be, pct",
    [
        ({"status": "ok", "price_scale": 0.8, "price": 40.0}, -20.0),
        ({"status": "ok", "price_scale": 1.25, "price": 50.0}, 25.0),
        ({"status": "ok", "price_scale": math.inf, "price": math.inf}, None),
        ({"status": "ok", "price_scale": None, "price": None}, None),
        ({"status": "break_even_not_bracketed", "price_scale": 0.5}, None),
    ],
)
def test_the_break_even_change_is_finite_or_none(be, pct):
    import json

    flip = verdict({"kpis": {}, "break_even": be})["could_flip_it"]
    assert flip["change_to_npv_zero_pct"] == pct
    json.dumps(flip, allow_nan=False)  # never inf or NaN in the JSON


def test_a_verdict_without_defaults_says_none_and_quotes_the_figures():
    v = verdict({"kpis": {"project_npv": {"status": "ok", "value": -3.0, "unit": "EUR"},
                          "ebitda_margin_net": {"status": "ok", "value": 0.3}},
                 "break_even": {"status": "ok", "line": "GPU-hours", "price_scale": 1.1,
                                "basis": "project_post_tax_npv@wacc_nominal"},
                 "assumed": []})
    assert v["status"] == "not_worth_building" and v["rests_on_defaults"] == []
    assert v["figures"]["project_npv"]["value"] == -3.0
    assert set(v["figures"]) == {"project_npv", "ebitda_margin_net"}
    assert v["basis"] == "project_post_tax_npv@wacc_nominal"
    assert v["could_flip_it"]["line"] == "GPU-hours"


# ── review and mutant follow-ups ────────────────────────────────────────────


def test_a_price_over_a_price_default_also_asks_its_money_year(bc, template, monkeypatch):
    monkeypatch.setattr(preset_apply, "_currency", lambda _cfg: "EUR")  # the pack's money
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    before = _config()
    fac = {ln["name"]: ln for ln in before["inputs"]["lines"]}["Facility, shell and M&E"]
    assert "price" in fac["assumed"] and fac["money_year"] is not None
    tid = _say(bc, "The facility costs 10000000 USD/MW.")
    d = _refused(chat_tools.set_business_case_lines, _digest(),
                 set={"Facility, shell and M&E": {"price": 10000000}},
                 citations=[_cite(tid, "10000000 USD/MW", line="Facility, shell and M&E",
                                  unit="USD/MW")])
    assert d["error_kind"] == "price_not_from_user" and d["reason"] == "no_citation"
    assert "money_year" in d["message"] and _config() == before


def test_a_share_is_asked_as_its_quantity_and_set_as_one(bc, template):
    template["id"] = "eh_h2_hub"
    out = chat_tools.apply_business_case_preset(_digest(), variant="h2_offtake")
    ins = next(a for a in out["to_ask"] if a.get("line") == "Insurance")
    assert ins["column"] == "quantity" and ins["share_of"] == "capex"
    chat_tools.set_business_case_lines(_digest(), set={"Insurance": {"quantity": 0.01}})
    assert {ln["name"]: ln for ln in _config()["inputs"]["lines"]}["Insurance"][
        "quantity"] == 0.01
    assert not any(a.get("setting") == "kw_per_gpu" for a in out["to_ask"])


def test_kw_per_gpu_is_asked_only_while_it_is_empty(bc, template):
    chat_tools.apply_business_case_preset(_digest(), variant="gpu_cloud")
    chat_tools.set_business_case_lines(_digest(), set={"kw_per_gpu": 1.2})
    assert not any(a.get("setting") == "kw_per_gpu" for a in chat_tools.get_business_case()
                   ["to_ask"])


def test_a_report_from_before_the_list_reads_not_established(bc, monkeypatch):
    from openpyxl import Workbook

    import routers.results as R
    from services.results_workbook.business_case import write_business_case_sheet
    from tests.test_business_case_chat_tools import _report

    rep = _report()  # no "assumed" key: stored before business case 14
    R._state["business_case_report"] = rep
    monkeypatch.setattr(R, "_bc_staleness_now", lambda s, ic_key=None: {"stale": False})
    out = chat_tools.get_business_case()
    assert out["assumed"] == out["assumed_total"] == "not established"
    assert out["verdict"]["rests_on_defaults"] == "not established"
    wb = Workbook()
    ws = wb[write_business_case_sheet(wb, {**rep, "basis": {"label": "x"}},
                                      {"stale": False})]
    cells = [[c.value for c in row] for row in ws.iter_rows()]
    at = next(i for i, r in enumerate(cells) if r[0] == "Assumed default")
    assert cells[at + 1][0] == "not_established" and "again" in cells[at + 1][3]


def test_the_workbook_row_without_a_unit_is_blank_never_none():
    from openpyxl import Workbook

    from services.results_workbook.business_case import _assumed_rows

    wb = Workbook()
    ws = wb.active
    _assumed_rows(ws, 1, [{"line": "L", "column": "price", "value": 1.0, "unit": None,
                           "source": "S", "source_year": 2026}])
    assert ws.cell(row=2, column=3).value in ("", None) or ws.cell(row=2, column=3).value != "not_established"
    assert ws.cell(row=2, column=3).value != "None"


@pytest.mark.parametrize("labels", [[], ["Nope"], ["Staff", "Staff"]])
def test_offered_case_refuses_unknown_repeated_or_no_labels(labels):
    with pytest.raises(LookupError) as e:
        P.offered_case("eh_site", labels, currency="EUR")
    assert "Staff" in str(e.value) and "Product sales" in str(e.value)


def test_offered_case_keeps_the_picked_order():
    case = P.offered_case("eh_site", ["Staff", "Product sales"], currency="EUR")
    assert [ln.name for ln in case.lines] == ["Staff", "Product sales"]


def test_the_receipt_reads_a_removed_line_and_an_unknown_cell_as_nothing():
    row = {"path": ["inputs", "lines", "gone"]}
    assert preset_apply.read_cell(row, {"inputs": {"lines": []}}) is None
    assert preset_apply.read_cell({"path": ["inputs", "lines", "a", "nope"]},
                                  {"inputs": {"lines": [{"name": "a"}]}}) is None
    assert preset_apply.read_cell({"path": ["inputs", "x"]},
                                  {"inputs": {"lines": []}}) is None
    assert preset_apply.read_cell({"path": ["preset"], "new": "p"}, None) == "p"


def test_a_solve_in_flight_refuses_the_preset(bc, template, monkeypatch):
    import routers.results as R

    args = _args(variant="gpu_cloud")
    with monkeypatch.context() as m:  # undone before the fixture clears the inputs
        m.setattr(R, "_solver_in_flight", lambda: True)
        d = _refused(preset_apply.plan, args)
    assert d["error_kind"] == "solver_in_flight" and d["status"] == 409


@pytest.mark.parametrize("cap", ["rows", "chars"])
def test_an_oversize_preset_card_is_refused(bc, template, monkeypatch, cap):
    monkeypatch.setattr(preset_apply, "CARD_ROW_CAP" if cap == "rows" else "CARD_CHARS", 3)
    d = _refused(preset_apply.plan, _args(variant="gpu_cloud"))
    assert d["error_kind"] == "business_case_card_too_large" and d["status"] == 422


def test_a_pack_unit_mismatch_is_typed(bc, template, monkeypatch):
    from models.business_case import DefaultUnitMismatch

    def bad(*_a, **_k):
        raise DefaultUnitMismatch("lines[1].default_keys[price]")

    monkeypatch.setattr(P, "preset", bad)
    d = _refused(preset_apply.plan, _args(variant="gpu_cloud"))
    assert d["error_kind"] == "default_unit_mismatch" and d["status"] == 422


def test_a_line_named_twice_is_refused(bc, template, monkeypatch):
    template["id"] = "eh_site"
    real = P.offered_case

    def twice(t, labels, **k):
        case = real(t, labels, **k)
        return case.model_copy(update={"lines": case.lines + case.lines[:1]})

    monkeypatch.setattr(P, "offered_case", twice)
    d = _refused(preset_apply.plan, _args(rows=["Staff"]))
    assert d["error_kind"] == "business_case_inputs_invalid" and "twice" in d["message"]


def test_invalid_finance_inputs_are_typed(bc, template, monkeypatch):
    import routers.results as R

    monkeypatch.setattr(R._state["solver_config"], "finance", {"currency": 5})
    d = _refused(preset_apply.plan, _args(variant="gpu_cloud"))
    assert d["error_kind"] == "finance_inputs_invalid" and d["status"] == 422
