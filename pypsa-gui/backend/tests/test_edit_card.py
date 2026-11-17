"""The edit card seam (harness/edit_card.py, LC-04 / owner Q3).

Every write-tier tool is classified as an edit or a named non-edit; each edit
shows its card in Expert as in Guided; the card's rows come from the native
handler on an isolated copy (so derived costs are the native derive's, checked
here against a hand calculation); the receipt is a native read-back; a card
whose inputs moved refuses. The OJ-05 journey is
tests/test_oj05_selected_line_edit_journey.py.
"""
import json

from fastapi import HTTPException
import pandas as pd
import pypsa
import pytest

from harness import edit_card
from harness.confirm import _safety_tier_for
from services import change_log_service, chat_service, chat_tools
from services import chat_tools_schema
from services.pypsa_service import PyPSAService


def test_every_write_tool_is_an_edit_or_a_named_non_edit():
    writes = {t["name"] for t in chat_tools_schema.TOOLS if _safety_tier_for(t["name"]) == "write"}
    edits = {name for name in writes if edit_card.is_edit(name)}
    named = set(edit_card.NOT_EDITS)
    assert not edits & named, edits & named
    assert writes - edits - named == set(), "classify each new write tool in harness/edit_card.py"
    assert named <= writes, named - writes
    assert set(edit_card.SETTINGS_EDITS) <= writes, set(edit_card.SETTINGS_EDITS) - writes


def test_carded_actions_are_confirmed_tiers_with_their_own_card():
    """GRIDCODE-CHAT-1: a destructive or execution tool that shows a content card, never the
    bare arguments card, and is gated as an edit."""
    from harness.confirm import DESTRUCTIVE_TIERS

    for name in edit_card.CARDED_ACTIONS:
        assert _safety_tier_for(name) in DESTRUCTIVE_TIERS, name
        assert edit_card.is_edit(name) and name in edit_card.ADAPTERS, name


# Q3-CARDS-1: every settings edit has its own old → new adapter; a new edit tool without one is listed here.
_NO_ADAPTER_YET = frozenset()


def test_every_settings_edit_has_an_adapter():
    missing = set(edit_card.SETTINGS_EDITS) - set(edit_card.ADAPTERS)
    assert missing == _NO_ADAPTER_YET, missing ^ _NO_ADAPTER_YET


def test_no_settings_edit_is_waiting_for_an_adapter():
    assert _NO_ADAPTER_YET == frozenset()
    writes = {t["name"] for t in chat_tools_schema.TOOLS if _safety_tier_for(t["name"]) == "write"}
    assert {name for name in edit_card.SETTINGS_EDITS if name in writes} <= set(edit_card.ADAPTERS)


@pytest.fixture
def grid(install_network, monkeypatch):
    n = pypsa.Network()
    n.set_snapshots(range(2))
    for bus in ("B1", "B2"):
        n.add("Bus", bus, v_nom=110)
    n.add("Line", "L1", bus0="B1", bus1="B2", x=12.1, r=0.1, s_nom=300, length=10)
    n.add("StorageUnit", "Battery", bus="B2", p_nom=20, max_hours=4)
    n.add("Store", "H2 tank", bus="B2", e_nom=500)
    install_network(n, name=None)
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    yield session
    chat_tools.set_chat_session(None)


def _dispatch(session, name, args, decision="approve", before_decision=None):
    frames, collector, cards = [], [], []
    gen = chat_service._dispatch_real_tool_call(session, {"id": "tu-1", "name": name, "input": args}, collector)
    for event, data in gen:
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            cards.append(data)
            if before_decision:
                before_decision()
            session.record_decision(data["confirmation_token"], decision)
    result = None
    if collector and not collector[-1].get("is_error"):
        content = collector[-1]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        result, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    return frames, cards, result


def _annuity(rate, years):
    return rate / (1 - (1 + rate) ** -years)


def test_storage_energy_cost_shows_the_derived_annual_cost(grid):
    """150 EUR/kWh for 15 years on a 4 h battery: capital_cost is the native
    derive's, equal to the hand calculation 150 000 EUR/MWh × 4 h × annuity(7 %, 15)."""
    frames, cards, result = _dispatch(grid, "update_component", {
        "component_class": "StorageUnit", "name": "Battery",
        "attrs": {"inv_energy_overnight": 150_000.0, "inv_energy_lifetime": 15.0}})
    assert len(cards) == 1
    rows = {row["field"]: row for row in cards[0]["edit_card"]["rows"] if row["role"] != "note"}
    hand = 150_000.0 * 4 * _annuity(0.07, 15)
    assert hand == pytest.approx(65_876.77, abs=0.01)
    assert rows["inv_energy_overnight"]["role"] == "typed"
    assert rows["inv_energy_overnight"]["unit"] == "EUR/MWh"
    assert rows["inv_energy_overnight"]["display"]["new"] == pytest.approx(150.0)
    assert rows["inv_energy_overnight"]["display"]["unit"] == "EUR/kWh"
    assert rows["capital_cost"]["role"] == "derived"
    assert rows["capital_cost"]["new"] == pytest.approx(hand, rel=1e-9)
    receipt = result["_edit_receipt"]
    assert receipt["outcome"] == "verified"
    assert float(PyPSAService.get_network().storage_units.at["Battery", "capital_cost"]) == pytest.approx(hand, rel=1e-9)


def test_store_overnight_cost_shows_both_units_and_no_annual_cost(grid):
    frames, cards, result = _dispatch(grid, "update_component", {
        "component_class": "Store", "name": "H2 tank", "attrs": {"overnight_cost": 12_000.0, "lifetime": 30.0}})
    rows = {row["field"]: row for row in cards[0]["edit_card"]["rows"] if row["role"] != "note"}
    assert rows["overnight_cost"]["new"] == 12_000.0 and rows["overnight_cost"]["unit"] == "EUR/MWh"
    assert rows["overnight_cost"]["display"]["new"] == pytest.approx(12.0)
    assert rows["overnight_cost"]["display"]["unit"] == "EUR/kWh"
    # A Store's annualised capital_cost is not written by the edit: the solver
    # derives it from overnight_cost at solve time, so the card claims nothing.
    assert "capital_cost" not in rows
    assert result["_edit_receipt"]["outcome"] == "verified"


@pytest.mark.parametrize("field,value", [("x", 15.0), ("r", 0.2), ("length", 25.0), ("s_nom", 450.0)])
def test_line_fields_are_whole_line_values(grid, field, value):
    frames, cards, result = _dispatch(grid, "update_component", {
        "component_class": "Line", "name": "L1", "attrs": {field: value}})
    typed = [row for row in cards[0]["edit_card"]["rows"] if row["role"] == "typed"]
    assert [(row["field"], row["new"]) for row in typed] == [(field, value)]
    assert float(PyPSAService.get_network().lines.at["L1", field]) == value


@pytest.mark.parametrize("klass,name,attrs,code", [
    ("StorageUnit", "Battery", {"capital_cost": 1.0}, "derived_field"),
    ("Store", "H2 tank", {"capital_cost": 1.0}, "derived_field"),
    ("Line", "L1", {"p0": 1.0}, "read_only_field"),
    ("Line", "L1", {"s_nom": True}, "invalid_edit_value"),
    ("Line", "L9", {"s_nom": 1.0}, "component_not_found"),
])
def test_unsupported_edits_refuse_before_a_card(grid, klass, name, attrs, code):
    frames, cards, _ = _dispatch(grid, "update_component", {"component_class": klass, "name": name, "attrs": attrs})
    assert cards == []
    errors = [d for e, d in frames if e == "tool_error"]
    assert errors and errors[0]["error_kind"] == code, errors


def test_the_preview_leaves_the_project_and_change_log_alone(grid):
    log_before = len(change_log_service.get_all())
    card = edit_card.preview("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 999.0}})
    assert card["rows"][0]["new"] == 999.0
    assert float(PyPSAService.get_network().lines.at["L1", "s_nom"]) == 300.0
    assert len(change_log_service.get_all()) == log_before


def test_a_card_whose_inputs_moved_applies_nothing(grid):
    def someone_else_edits():
        PyPSAService.get_network().lines.at["L1", "s_nom"] = 350.0

    frames, cards, result = _dispatch(grid, "update_component", {
        "component_class": "Line", "name": "L1", "attrs": {"s_nom": 400.0}}, before_decision=someone_else_edits)
    assert len(cards) == 1 and result is None
    assert float(PyPSAService.get_network().lines.at["L1", "s_nom"]) == 350.0
    errors = [d for e, d in frames if e == "tool_error"]
    assert errors and "edit_preview_stale" in json.dumps(errors[0]), errors


def test_bulk_edit_card_lists_every_component(grid):
    PyPSAService.get_network().add("Line", "L2", bus0="B1", bus1="B2", x=12.1, r=0.1, s_nom=300)
    frames, cards, result = _dispatch(grid, "bulk_update_components", {
        "component_class": "Line", "names": ["L1", "L2"], "updates": {"s_nom": 500.0}})
    typed = [(r["name"], r["old"], r["new"]) for r in cards[0]["edit_card"]["rows"] if r["role"] == "typed"]
    assert typed == [("L1", 300.0, 500.0), ("L2", 300.0, 500.0)]
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_solver_settings_show_old_and_new(grid):
    old = PyPSAService.get_active_context().solver_state["solver_config"].voll
    frames, cards, result = _dispatch(grid, "update_solver_config", {"partial": {"voll": 4321.0}})
    card = cards[0]["edit_card"]
    assert card["kind"] == "settings_edit"
    assert [(r["field"], r["old"], r["new"]) for r in card["rows"]] == [("voll", old, 4321.0)]
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_an_edit_without_an_adapter_still_shows_a_card(grid):
    frames, cards, result = _dispatch(grid, "create_carrier", {"name": "renamed"})
    card = cards[0]["edit_card"]
    assert card["kind"] == "arguments" and card["complete"] is False
    assert card["rows"] == [{"component_class": "", "name": "", "field": "name", "label": "name", "old": None,
                             "old_known": False, "new": "renamed", "unit": "", "role": "typed"}]
    assert result["_edit_receipt"]["outcome"] == "uncertain"


def test_a_non_edit_write_runs_without_a_card_in_expert(grid):
    frames, cards, _ = _dispatch(grid, "discuss_project_run", {"run_id": "missing"})
    assert cards == []


TYPE = "243-AL1/39-ST1A 110.0"  # a PyPSA standard line type


def _typed(grid):
    PyPSAService.get_network().lines.at["L1", "type"] = TYPE


@pytest.mark.parametrize("field", ["r", "x", "b"])
def test_a_typed_line_refuses_the_fields_its_type_sets(grid, field):
    _typed(grid)
    frames, cards, _ = _dispatch(grid, "update_component", {"component_class": "Line", "name": "L1",
                                                            "attrs": {field: 1.0}})
    errors = [d for e, d in frames if e == "tool_error"]
    assert cards == [] and errors[0]["error_kind"] == "type_owned_field"
    assert TYPE in errors[0]["message"]


def test_a_typed_line_length_shows_what_the_type_sets_at_solve(grid):
    """Hand oracle from PyPSA's shipped type table: r = r_per_length × length / num_parallel."""
    _typed(grid)
    per_km = PyPSAService.get_network().line_types.loc[TYPE]
    frames, cards, result = _dispatch(grid, "update_component", {"component_class": "Line", "name": "L1",
                                                                 "attrs": {"length": 20.0}})
    rows = {row["field"]: row for row in cards[0]["edit_card"]["rows"] if row["role"] != "note"}
    assert rows["length"]["role"] == "typed" and rows["length"]["new"] == 20.0
    assert rows["r"]["role"] == "at_solve" and rows["r"]["new"] == pytest.approx(per_km["r_per_length"] * 20.0)
    assert rows["x"]["role"] == "at_solve" and rows["x"]["new"] == pytest.approx(per_km["x_per_length"] * 20.0)
    # Read back verifies what the write stores now, not what the solve will set.
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_an_extendable_capacity_refuses_and_names_the_bounds(grid):
    lines = PyPSAService.get_network().lines
    lines.at["L1", "s_nom_extendable"] = True
    lines.at["L1", "s_nom_max"] = 800.0
    frames, cards, _ = _dispatch(grid, "update_component", {"component_class": "Line", "name": "L1",
                                                            "attrs": {"s_nom": 400.0}})
    errors = [d for e, d in frames if e == "tool_error"]
    assert cards == [] and errors[0]["error_kind"] == "extendable_capacity"
    assert "s_nom_max=800.0" in errors[0]["message"]


def test_making_it_fixed_in_the_same_edit_is_allowed(grid):
    PyPSAService.get_network().lines.at["L1", "s_nom_extendable"] = True
    frames, cards, result = _dispatch(grid, "update_component", {"component_class": "Line", "name": "L1",
                                                                 "attrs": {"s_nom": 400.0, "s_nom_extendable": False}})
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_no_solver_output_appears_on_the_card(grid):
    card = edit_card.preview("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 400.0}})
    assert [row["field"] for row in card["rows"]] == ["s_nom"]


def test_a_capacity_is_shown_in_its_bus_unit(grid):
    network = PyPSAService.get_network()
    network.add("Bus", "NH3", unit="t/h")
    network.add("Store", "NH3 tank", bus="NH3", e_nom=50)
    network.add("Link", "Synth", bus0="NH3", bus1="B1", p_nom=10)
    store = edit_card.preview("update_component", {"component_class": "Store", "name": "NH3 tank",
                                                   "attrs": {"e_nom": 80.0}})
    link = edit_card.preview("update_component", {"component_class": "Link", "name": "Synth",
                                                  "attrs": {"p_nom": 12.0}})
    line = edit_card.preview("update_component", {"component_class": "Line", "name": "L1",
                                                  "attrs": {"s_nom": 400.0}})
    assert store["rows"][0]["unit"] == "t"
    assert link["rows"][0]["unit"] == "t/h"
    assert line["rows"][0]["unit"] == "MVA"


def test_a_parts_edit_shows_the_native_clear_of_an_imported_overnight_cost(grid):
    """GK reverse trap: a row with PyPSA's overnight_cost set would keep using it
    at solve time. The native handler clears it on a parts edit; the card shows
    that clear, and the read-back confirms it."""
    PyPSAService.get_network().storage_units.at["Battery", "overnight_cost"] = 1.2e6
    frames, cards, result = _dispatch(grid, "update_component", {
        "component_class": "StorageUnit", "name": "Battery",
        "attrs": {"inv_energy_overnight": 150_000.0, "inv_energy_lifetime": 15.0}})
    rows = {r["field"]: r for r in cards[0]["edit_card"]["rows"] if r["role"] != "note"}
    assert (rows["overnight_cost"]["old"], rows["overnight_cost"]["new"]) == (1.2e6, None)
    assert rows["overnight_cost"]["role"] == "follows"
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert PyPSAService.get_network().storage_units["overnight_cost"].isna()["Battery"]


def test_a_receipt_after_a_solve_reports_results_stale(grid):
    """The solve records its input fingerprint; an edit after it makes the receipt say the results are stale."""
    from services.provenance_graph import runs

    meta = PyPSAService.get_network().meta
    meta["assistant_solve_input_fingerprint"] = edit_card._fingerprint()
    # provenance-graph 04a: both recorded fingerprints, as a solve writes them (`_record_solve_inputs`).
    meta["assistant_solve_exact_input_fingerprint"] = runs.active_input_fingerprint(exact=True)
    assert edit_card._results_stale() is False
    frames, cards, result = _dispatch(grid, "update_component", {"component_class": "Line", "name": "L1",
                                                                 "attrs": {"s_nom": 400.0}})
    assert result["_edit_receipt"]["outcome"] == "verified"
    assert result["_edit_receipt"]["results_stale"] is True


def test_the_receipt_names_a_section_state_that_changed(grid, monkeypatch):
    """PR-2: the card names its flow section; the receipt adds the section's
    state after the write and says when it changed. The section-state read is
    stubbed here; OJ-05 reads the real flow."""
    states = iter([{"id": "connection", "label": "Connection & grid", "state": "incomplete"},
                   {"id": "connection", "label": "Connection & grid", "state": "complete"}])
    monkeypatch.setattr(edit_card, "_section_state", lambda section_id: next(states))
    frames, cards, result = _dispatch(grid, "update_component", {"component_class": "Line", "name": "L1",
                                                                 "attrs": {"s_nom": 400.0}})
    card = cards[0]["edit_card"]
    assert card["section"] == {"id": "connection", "label": "Connection & grid"}
    assert card["rows"][0]["target"] == {"section": "connection", "panel": None, "component": "Line",
                                         "entity": "L1", "field": "s_nom"}
    assert result["_edit_receipt"]["section"] == {"id": "connection", "label": "Connection & grid",
                                                  "state": "complete", "changed": True}


def test_the_section_state_does_not_bind_the_card(grid, monkeypatch):
    """A section's state moving between card and approval does not make the card stale."""
    states = iter(["incomplete", "complete"])
    monkeypatch.setattr(edit_card, "_section_state",
                        lambda section_id: {"id": section_id, "label": "x", "state": next(states)})
    first = edit_card.preview("update_component", {"component_class": "Line", "name": "L1", "attrs": {"s_nom": 9.0}})
    first["section_state"] = edit_card._section_state("connection")  # the state moved on
    edit_card.require_current("update_component", {"component_class": "Line", "name": "L1",
                                                   "attrs": {"s_nom": 9.0}}, first)


@pytest.mark.parametrize("partial,kind", [
    ({"solve_strategy": "rolling", "rolling_horizon": 24, "rolling_overlap": 24}, "rolling_overlap_exceeds_horizon"),
    ({"solve_strategy": "rolling", "rolling_horizon": 0}, "rolling_horizon_invalid"),
    ({"solve_strategy": "rolling", "rolling_horizon": 48, "rolling_overlap": -1}, "rolling_overlap_invalid"),
])
def test_solver_card_refuses_an_unusable_rolling_window(grid, partial, kind):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("update_solver_config", {"partial": partial})
    assert exc.value.detail["error_kind"] == kind


def test_solver_card_allows_turning_rolling_off_with_a_stale_window(grid):
    cfg = PyPSAService.get_active_context().solver_state["solver_config"]
    cfg.solve_strategy, cfg.rolling_horizon, cfg.rolling_overlap = "rolling", 24, 24  # legacy pair
    card = edit_card.preview("update_solver_config", {"partial": {"solve_strategy": "full"}})
    assert [r["field"] for r in card["rows"]] == ["solve_strategy"]


def test_solver_card_shows_a_load_growth_rate_and_the_multipliers_it_derives(grid):
    # homer-parity 04a: the rate in Model Horizon's words, the multiplier it
    # derives beside it, and no row for the derived-entry bookkeeping.
    cfg = PyPSAService.get_active_context().solver_state["solver_config"]
    cfg.multi_investment_periods, cfg.investment_periods = True, [2030, 2035]
    card = edit_card.preview("update_solver_config", {"partial": {
        "load_growth_pct_per_year": {"electrical": 2.0}, "load_growth_base_year": 2030}})
    rows = {r["field"]: r for r in card["rows"]}
    assert set(rows) == {"load_growth_pct_per_year.electrical", "load_growth_base_year",
                         "load_scalers_by_carrier.electrical.2035"}
    assert rows["load_growth_pct_per_year.electrical"]["label"] == "Load growth electrical"
    assert rows["load_growth_pct_per_year.electrical"]["unit"] == "% per year"
    assert rows["load_scalers_by_carrier.electrical.2035"]["new"] == 1.104081


def _grown(**kw):
    cfg = PyPSAService.get_active_context().solver_state["solver_config"]
    cfg.multi_investment_periods, cfg.investment_periods = True, kw.pop("periods", [2030, 2035])
    cfg.load_growth_pct_per_year, cfg.load_growth_base_year = {"electrical": 2.0}, 2030
    cfg.load_scalers_by_carrier = {"electrical": {"2035": 1.104081}}
    cfg.load_scalers_derived = {"electrical": ["2035"]}
    return cfg


def test_solver_card_shows_the_stored_start_year_beside_a_rate_only_change(grid):
    _grown()
    card = edit_card.preview("update_solver_config", {"partial": {
        "load_growth_pct_per_year": {"electrical": 3.0}}})
    rows = {r["field"]: r for r in card["rows"]}
    assert set(rows) == {"load_growth_pct_per_year.electrical", "load_growth_base_year",
                         "load_scalers_by_carrier.electrical.2035"}
    assert rows["load_growth_base_year"]["old"] == rows["load_growth_base_year"]["new"] == 2030
    assert rows["load_scalers_by_carrier.electrical.2035"]["new"] == 1.159274
    # the start year the change leaves shows as unchanged: no "2030 → 2030" (UX)
    assert rows["load_growth_base_year"]["unchanged"] is True
    assert "unchanged" not in rows["load_growth_pct_per_year.electrical"]
    assert "Growth from year" not in card["says"]
    assert "2030 \u2192 2030" not in card["says"]


def test_the_cards_load_growth_labels_are_model_horizons():
    """The card says the rate and its start year in the panel's words (UX parity)."""
    import pathlib

    panel = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
             / "modelHorizon" / "StepPeriodEconomics.tsx").read_text()
    label, unit, _ = edit_card._SOLVER_WORDS["load_growth_pct_per_year"]
    assert f"{label} ({unit})" in panel  # "Load growth (% per year)"
    label, _, _ = edit_card._SOLVER_WORDS["load_growth_base_year"]
    assert f'aria-label="{label}"' in panel  # "Growth from year"


def test_solver_card_shows_the_stored_rate_beside_a_start_year_only_change(grid):
    _grown()
    card = edit_card.preview("update_solver_config", {"partial": {"load_growth_base_year": 2025}})
    rows = {r["field"]: r for r in card["rows"]}
    # 2030 grows from 2025 now: a period the change adds reads "not set" before it
    assert set(rows) == {"load_growth_pct_per_year.electrical", "load_growth_base_year",
                         "load_scalers_by_carrier.electrical.2030", "load_scalers_by_carrier.electrical.2035"}
    assert rows["load_scalers_by_carrier.electrical.2030"]["old"] is None
    assert rows["load_growth_pct_per_year.electrical"]["new"] == 2.0


def test_solver_card_says_each_load_multiplier_period_in_words(grid):
    """UX (#1604): one row per period, in words ("2035: 1.104 → 1.159"), never the stored JSON."""
    _grown()
    card = edit_card.preview("update_solver_config", {"partial": {
        "load_growth_pct_per_year": {"electrical": 3.0}}})
    row = next(r for r in card["rows"] if r["field"] == "load_scalers_by_carrier.electrical.2035")
    assert row["label"] == "Load multiplier electrical 2035:"
    assert (row["old"], row["new"], row["unit"]) == (1.104081, 1.159274, "\u00d7")
    assert row["display"] == {"old": 1.104, "new": 1.159, "unit": "\u00d7"}
    assert "2035: 1.104 \u2192 1.159" in card["says"]
    assert "{" not in card["says"]


def test_a_load_multiplier_card_reads_back_each_period_after_it_applies(grid):
    """The receipt verifies the per-period rows against the stored config."""
    _grown()
    frames, cards, result = _dispatch(grid, "update_solver_config", {"partial": {
        "load_growth_pct_per_year": {"electrical": 3.0}}})
    fields = [r["field"] for r in cards[0]["edit_card"]["rows"]]
    assert "load_scalers_by_carrier.electrical.2035" in fields
    assert result["_edit_receipt"]["outcome"] == "verified"


def test_a_growth_input_the_handler_changes_anyway_is_not_marked_unchanged(grid, monkeypatch):
    """GK B: `unchanged` only when the value really is the same, not merely left out of the change."""
    _grown()
    real = chat_tools.update_solver_config

    def moves_the_start_year(partial):
        out = real(partial)
        PyPSAService.get_active_context().solver_state["solver_config"].load_growth_base_year = 2025
        return out
    monkeypatch.setattr(chat_tools, "update_solver_config", moves_the_start_year)
    card = edit_card.preview("update_solver_config", {"partial": {
        "load_growth_pct_per_year": {"electrical": 3.0}}})
    row = next(r for r in card["rows"] if r["field"] == "load_growth_base_year")
    assert (row["old"], row["new"]) == (2030, 2025)
    assert "unchanged" not in row


_GROWN_2040 = {"2035": (1.104081, None), "2040": (None, 1.218994)}
_CLEARED = {"2035": (1.104081, None)}


@pytest.mark.parametrize("tool,args,new", [
    ("set_investment_periods", {"periods": [2030, 2040]}, _GROWN_2040),
    ("set_multi_period_snapshots", {"periods": [2040, 2030], "operational_from": "2025-01-01",
                                    "operational_to": "2025-01-01 01:00"}, _GROWN_2040),
    ("set_investment_periods", {"periods": []}, _CLEARED),
    ("set_snapshots", {"start": "2025-01-01", "end": "2025-01-01 01:00"}, _CLEARED),
])
def test_a_time_axis_card_shows_the_load_multipliers_its_period_change_rederives(grid, tool, args, new):
    _grown(periods=[])  # the network's periods rule (the config sets no override)
    PyPSAService.get_network().set_snapshots(
        pd.MultiIndex.from_product([[2030, 2035], [0, 1]], names=["period", "timestep"]))
    PyPSAService.get_network().investment_periods = [2030, 2035]
    card = edit_card.preview(tool, args)
    rows = {r["field"]: r for r in card["rows"]}
    # one derived row per period it changes, in the solver card's words (UX #1604)
    scalers = {f: r for f, r in rows.items() if f.startswith("load_scalers_by_carrier.")}
    assert {f: (r["old"], r["new"]) for f, r in scalers.items()} == {
        f"load_scalers_by_carrier.electrical.{p}": v for p, v in new.items()}
    assert {r["role"] for r in scalers.values()} == {"derived"}
    assert rows["load_scalers_by_carrier.electrical.2035"]["label"] == "Load multiplier electrical 2035:"
    assert rows["load_scalers_by_carrier.electrical.2035"]["display"]["old"] == 1.104
    assert set(args) <= set(rows)          # the arguments stay on the card
    assert card["complete"] is False       # the network change itself is not read back


def test_a_time_axis_card_refuses_a_period_the_load_growth_cannot_follow(grid):
    _grown(periods=[])
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("set_investment_periods", {"periods": [2025, 2035]})
    assert exc.value.detail["error_kind"] == "load_growth_invalid"


def test_a_time_axis_card_without_a_rate_shows_only_its_arguments(grid):
    card = edit_card.preview("set_investment_periods", {"periods": [2030, 2040]})
    assert [r["field"] for r in card["rows"]] == ["periods"]


def test_solver_card_refuses_an_invalid_load_growth_rate(grid):
    with pytest.raises(HTTPException) as exc:
        edit_card.preview("update_solver_config", {"partial": {
            "load_growth_pct_per_year": {"electrical": 2.0}}})
    assert exc.value.detail["error_kind"] == "load_growth_invalid"
