# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
# ruff: noqa: F811  (pytest fixtures imported from sibling test modules)
"""Study harness 34 (GK B (70) plan GO; Q1-Q3 (a), S1-S7): eh_site@5 makes the
genset fuel price a research gap. A verified row in EUR/MWh of genset
electrical output, once the user confirms it, is written through one carded
bulk edit to every genset of the site's one fuel carrier. A row in another
unit is refused, not converted. A grant without research keeps the template's
price, and the report names it. eh_site@1-@4 stay byte-equal (L8)."""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib

import pandas as pd
import pypsa
import pytest
from harness import research
from services import chat_tools, run_envelope, run_profiles, source_ledger
from services.reports import harness_chapter as H
from tests.test_assistant_tasks_study_brief import baseline, helpers_state, session  # noqa: F401
from tests.test_harness_envelope import granted, profile  # noqa: F401 -- pytest fixtures
from tests.test_harness_research import BOOT, Inner, _hold, _profile, _rows, page
from tests.test_harness_run_skeleton import site  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401

REF = "eh_site@5"
GAP = run_profiles.FUEL_GAP
URL = "https://fuel.example.org/genset-costs-2026"
QUOTE = "Gas genset fuel cost: EUR 172.40 per MWh of electrical output"
PAGE = "Genset fuel costs 2026.\n" + QUOTE + ", excluding VAT."
# sha256 of json.dumps(PROFILES[ref], sort_keys=True) on master 941d516a1 (L8).
PINNED = {
    "eh_site@1": "8d8f09dec396579ec12bd9344959ac6ecd5cef1eaa0a47de737bd83bca51cf46",
    "eh_site@2": "2eefcb14bc7d5f05a465be591bf54238d9e4f1c7bf7948cff935c461b381f9f8",
    "eh_site@3": "446df3bb5862d095fe54bd3a52ca3fe010dc2dca16776da366ee952efa92e9a6",
    "eh_site@4": "85e2b14fd93680c4a1bacc6b28309a1ff06c5ed676ae445f4c943b511f7f0b45",
}


def _plain(ref):
    return json.loads(json.dumps(run_profiles.PROFILES[ref]))


# ── the profile (L8) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("ref", sorted(PINNED))
def test_earlier_versions_are_byte_equal(ref):
    blob = json.dumps(run_profiles.PROFILES[ref], sort_keys=True).encode()
    assert hashlib.sha256(blob).hexdigest() == PINNED[ref]


def test_v5_differs_from_v4_only_by_the_fuel_gap():
    v4, v5 = _plain("eh_site@4"), _plain(REF)
    assert (v4.pop("version"), v5.pop("version")) == (4, 5)
    gaps = v5["research"].pop("gaps")
    assert gaps[:-1] == v4["research"].pop("gaps")
    assert gaps[-1] == {
        "key": "genset_fuel_price",
        "stage": "inputs",
        "tool": "bulk_update_components",
        "target": {
            "component": "Generator",
            "name": "genset*",
            "attr": "marginal_cost",
        },
        "unit": "EUR/MWh",
        "basis": "genset fuel cost per MWh electrical output, excl. VAT",
        "question": "What is the published fuel cost of this site's gensets (their fuel carrier, "
        "gas or diesel) per MWh of genset electrical output?",
    }
    assert v5 == v4  # nothing else moved


def test_v5_loads_and_its_inputs_stage_researches_the_tariff_and_the_fuel_price():
    profile = run_profiles.resolve(REF)
    assert research.check_block(profile["research"]) is profile["research"]
    assert [g["key"] for g in research.gaps_for(profile, "inputs")] == [
        "import_tariff",
        "genset_fuel_price",
    ]
    assert (
        research.gaps_for(run_profiles.resolve("eh_site@4"), "inputs")[-1]["key"]
        == "import_tariff"
    )


def test_start_task_offers_v5():
    from harness import catalogue, guards

    tool = next(t for t in catalogue.TOOLS if t["name"] == "start_task")
    enum = tool["input_schema"]["properties"]["run"]["properties"]["profile"]["enum"]
    assert enum[-1] == REF
    args = {
        "title": "t",
        "request_id": "r",
        "steps": [],
        "run": {"profile": REF, "mode": "stepwise"},
    }
    assert guards.check("start_task", args, "write") is None


# ── the unit (Q1, S5) ────────────────────────────────────────────────────────


def ledger_gap():
    return research.gaps_for({"research": {"gaps": [GAP]}}, "inputs")[0]


def proposal(**over):
    return {
        "value": 172.4,
        "unit": GAP["unit"],
        "basis": GAP["basis"],
        "url": URL,
        "quote": QUOTE,
        **over,
    }


def test_a_verified_row_in_the_gaps_unit():
    row = source_ledger.propose(
        proposal(), ledger_gap(), [page(URL, PAGE)], turn_id="t"
    )
    assert (row["verification"], row["value"], row["unit"]) == (
        "verified",
        172.4,
        "EUR/MWh",
    )
    assert "jurisdiction" not in row  # only a tariff names one


@pytest.mark.parametrize("unit", ["EUR/L", "EUR/MWh_th"])
def test_a_price_in_another_unit_is_refused_not_converted(unit):
    with pytest.raises(source_ledger.LedgerError) as exc:
        source_ledger.propose(
            proposal(unit=unit), ledger_gap(), [page(URL, PAGE)], turn_id="t"
        )
    assert exc.value.kind == "research_unit_mismatch"
    assert str(exc.value) == (
        "This gap takes a value in EUR/MWh (genset fuel cost per MWh electrical output, excl. VAT); "
        f"{unit!r} ('genset fuel cost per MWh electrical output, excl. VAT') is refused, not converted."
    )


# ── the write (S1, S2, S5) ───────────────────────────────────────────────────


def _network(**extra):
    """Three gas gensets (one a curve genset), a gas CHP and a gas supply
    Generator on the same carrier (never gensets: GK B (70) N1), PV."""
    n = pypsa.Network()
    n.add("Bus", "site_mv")
    for c in ("gas", "diesel", "solar"):
        n.add("Carrier", c)
    n.add(
        "Generator",
        "genset_1",
        bus="site_mv",
        carrier="gas",
        p_nom=2.0,
        marginal_cost=165.0,
    )
    n.add(
        "Generator",
        "genset_new_2030",
        bus="site_mv",
        carrier="gas",
        marginal_cost=165.0,
    )
    n.add(
        "Generator",
        "gas_chp",
        bus="site_mv",
        carrier="gas",
        p_nom=1.0,
        marginal_cost=90.0,
    )
    n.add("Generator", "gas supply", bus="site_mv", carrier="gas", marginal_cost=40.0)
    n.add(
        "Generator",
        "genset_2",
        bus="site_mv",
        carrier="gas",
        p_nom=2.0,
        marginal_cost=165.0,
    )
    n.add("Generator", "site_pv", bus="site_mv", carrier="solar", p_nom=1.0)
    for col in ("fuel_curve_slope", "fuel_curve_no_load", "fuel_price"):
        n.generators[col] = float("nan")
    n.generators.loc["genset_2", ["fuel_curve_slope", "fuel_price"]] = [0.25, 0.9]
    for name, carrier in extra.items():
        n.add("Generator", name, bus="site_mv", carrier=carrier, marginal_cost=260.0)
    return n


@pytest.fixture
def fuel_run(granted, profile, monkeypatch):
    """A granted run whose profile carries the fuel gap, on `_network()`."""
    real = run_profiles.PROFILES["eh_site@1"]
    monkeypatch.setitem(
        run_profiles.PROFILES,
        "eh_site@1",
        {**real, "research": {"gaps": [*real["research"]["gaps"], copy.deepcopy(GAP)]}},
    )
    net = {"n": _network()}
    monkeypatch.setattr(research, "_active_network", lambda: net["n"])
    task, path, _ = granted()
    return task, path, net


def _row(**over):
    return source_ledger.propose(
        proposal(**over), ledger_gap(), [page(URL, PAGE)], turn_id="t"
    )


def test_confirming_a_row_adds_one_carded_bulk_write_to_every_gas_genset_but_the_curve_one(
    fuel_run,
):
    task, path, _ = fuel_run
    row = _row()
    _rows(path, row)
    research.settle(path, row["id"], "confirmed", by="u1")
    step = json.loads(path.read_text())["steps"][-1]
    assert step == {
        "id": f"inputs.research.{row['id']}.fuel",
        "tool": "bulk_update_components",
        "status": "pending",
        "args": {
            "component_class": "Generator",
            # gensets only: never the CHP or the gas supply (N1), nor the curve genset (S2)
            "names": ["genset_1", "genset_new_2030"],
            "updates": {"marginal_cost": 172.4},
            "research_ref": row["id"],
        },
    }
    assert run_envelope._research(step["args"])  # never admitted: the user's card (R4)


def test_a_mixed_fuel_site_refuses_the_write_and_the_run_goes_on(fuel_run):
    """S1: nothing is written; every row of the gap is rejected with the kind,
    so the run never waits on a confirmation that cannot succeed."""
    task, path, net = fuel_run
    net["n"] = _network(genset_3="diesel")
    row, other = _row(), _row(value=180.0)
    _rows(path, row, other)
    steps = json.loads(path.read_text())["steps"]
    prices = net["n"].generators.marginal_cost.copy()
    with pytest.raises(research.ResearchError) as exc:
        research.settle(path, row["id"], "confirmed", by="u1")
    assert exc.value.kind == "research_mixed_fuel"
    assert str(exc.value) == (
        "The site's gensets burn diesel, gas; one researched price is never written to two fuels. "
        "Nothing was written."
    )
    record = json.loads(path.read_text())
    assert record["steps"] == steps
    assert [
        (r["status"], r["rejected_reason"]) for r in record["run"]["research"]["rows"]
    ] == [("rejected", "research_mixed_fuel"), ("rejected", "research_mixed_fuel")]
    assert record["run"]["waiting"] is None
    pd.testing.assert_series_equal(net["n"].generators.marginal_cost, prices)


THERMAL = "Natural gas wholesale price (Hs, thermal): EUR 40.50 per MWh"


@pytest.mark.parametrize(
    "quote",
    [
        THERMAL,
        "Gas price: EUR 40.50 per MWh_th",
        "Gas: EUR 40.50 per MWh (GCV)",
        "Genset fuel cost: EUR 172.40 per MWh",
    ],
)
def test_a_quote_not_per_mwh_of_electrical_output_is_refused(fuel_run, quote):
    """Review F1: a fuel-energy price passes the ledger's EUR + MWh check, so the
    fuel gap refuses it at settle (and `record_source` refuses it at once)."""
    task, path, net = fuel_run
    row = {**_row(), "quote": quote}
    _rows(path, row)
    before = path.read_text()
    with pytest.raises(research.ResearchError) as exc:
        research.settle(path, row["id"], "confirmed", by="u1")
    assert (exc.value.kind, str(exc.value)) == (
        "research_unit_mismatch",
        "A genset fuel price is quoted per MWh of electrical output; a quote that does not say so, "
        "or prices fuel energy, is refused, not converted.",
    )
    assert path.read_text() == before


@pytest.mark.parametrize(
    "quote, value",
    [
        (QUOTE, 172.4),
        ("Diesel genset: EUR 260 per MWhe", 260),
        ("EUR 260/MWh_el", 260.0),
    ],
)
def test_a_quote_per_mwh_of_electrical_output_passes(quote, value):
    research._check_electrical(quote, value)


def test_record_source_refuses_a_thermal_quote_for_the_fuel_gap(monkeypatch):
    from harness import session as harness_session

    chat = harness_session.ChatSession(session_id="run:fuel")
    turn = research.Turn(
        "unused", "rt_1", {GAP["key"]: ledger_gap()}, Inner(), (BOOT, lambda: 0.0)
    )
    monkeypatch.setitem(research._TURNS, chat.session_id, turn)
    chat_tools.set_chat_session(chat)
    try:
        with pytest.raises(research.ResearchError) as exc:
            research.record_source(
                GAP["key"], 40.5, GAP["unit"], GAP["basis"], URL, THERMAL
            )
    finally:
        chat_tools.set_chat_session(None)
    assert exc.value.kind == "research_unit_mismatch" and turn.rows == []


def test_no_open_network_is_a_typed_refusal(monkeypatch):
    from services.pypsa_service import PyPSAService

    monkeypatch.setattr(PyPSAService, "get_active_context", staticmethod(lambda: None))
    with pytest.raises(research.ResearchError) as exc:
        research._active_network()
    assert exc.value.kind == "research_not_available"


@pytest.mark.parametrize(
    "over, kind, message",
    [
        (
            {"unit": "EUR/L"},
            "research_unit_mismatch",
            "A researched genset fuel price takes EUR/MWh (genset fuel cost per MWh electrical output, "
            "excl. VAT); EUR/L is refused, not converted.",
        ),
        ({"value": 0.0}, "invalid_source_row", "A genset fuel price is above 0."),
    ],
)
def test_a_row_the_write_cannot_take_is_refused_and_nothing_is_written(
    fuel_run, over, kind, message
):
    task, path, net = fuel_run
    row = {**_row(), **over}
    _rows(path, row)
    before, prices = path.read_text(), net["n"].generators.marginal_cost.copy()
    with pytest.raises(research.ResearchError) as exc:
        research.settle(path, row["id"], "confirmed", by="u1")
    assert (exc.value.kind, str(exc.value)) == (kind, message)
    assert path.read_text() == before
    pd.testing.assert_series_equal(net["n"].generators.marginal_cost, prices)


def test_a_range_is_never_written_as_a_fuel_price(fuel_run):
    task, path, net = fuel_run
    row = {**_row(), "value": None, "range": {"low": 150.0, "high": 180.0}}
    with pytest.raises(research.ResearchError) as exc:
        research._fuel_steps(row, GAP, "inputs.research.x")
    assert (exc.value.kind, str(exc.value)) == (
        "research_range_not_applicable",
        "A sourced range is read by a case change, never written as a fuel price.",
    )
    _rows(path, row)
    steps = json.loads(path.read_text())["steps"]
    research.settle(path, row["id"], "confirmed", by="u1")
    assert json.loads(path.read_text())["steps"] == steps


def test_a_site_whose_gensets_all_run_on_a_curve_gets_no_write(fuel_run):
    task, path, net = fuel_run
    gens = net["n"].generators
    gens.loc[gens.carrier == "gas", ["fuel_curve_slope", "fuel_price"]] = [0.25, 0.9]
    row = _row()
    _rows(path, row)
    steps = json.loads(path.read_text())["steps"]
    research.settle(path, row["id"], "confirmed", by="u1")
    assert json.loads(path.read_text())["steps"] == steps


def test_the_tariff_gap_still_writes_its_tariff(fuel_run):
    from tests.test_harness_research import (
        ledger_gap as tariff_gap,
        proposal as tariff,
        page as tariff_page,
    )

    task, path, _ = fuel_run
    row = source_ledger.propose(tariff(), tariff_gap(), [tariff_page()], turn_id="t")
    _rows(path, row)
    research.settle(path, row["id"], "confirmed", by="u1")
    assert [s["tool"] for s in json.loads(path.read_text())["steps"][-2:]] == [
        "put_library_item",
        "attach_tariff",
    ]


# ── research_ref on the bulk edit (S3) ───────────────────────────────────────


def test_research_ref_is_inert_when_absent_and_only_echoed_when_present(
    session, monkeypatch
):
    from routers import network

    seen = []

    def handler(body):
        seen.append(copy.deepcopy(body))
        return {"updated": len(body["names"]), "undo": "u1"}

    monkeypatch.setattr(network, "bulk_update", handler)
    args = ("Generator", ["genset_1"], {"marginal_cost": 170.0})
    assert chat_tools.bulk_update_components(*args) == {"updated": 1, "undo": "u1"}
    assert chat_tools.bulk_update_components(*args, research_ref="src_1") == {
        "updated": 1,
        "undo": "u1",
        "research_ref": "src_1",
    }
    body = {
        "component_class": "Generator",
        "names": ["genset_1"],
        "updates": {"marginal_cost": 170.0},
    }
    assert seen == [body, body]  # the ref never reaches the write


def test_the_bulk_edit_runs_the_fuel_curve_guard(session):
    """S2: the bulk path refuses a raw marginal_cost on a curve genset, as the single edit does."""
    from fastapi import HTTPException
    from services.pypsa_service import PyPSAService

    n = PyPSAService.get_active_context().network
    name = str(n.generators.index[0])
    for col, v in (
        ("fuel_curve_slope", 0.25),
        ("fuel_curve_no_load", 0.0),
        ("fuel_price", 0.9),
    ):
        n.generators[col] = float("nan")
        n.generators.at[name, col] = v
    with pytest.raises(HTTPException) as exc:
        chat_tools.bulk_update_components(
            "Generator", [name], {"marginal_cost": 999.0}, research_ref="src_1"
        )
    assert exc.value.status_code == 422 and "set by the fuel curve" in exc.value.detail


# ── the report (S4) ──────────────────────────────────────────────────────────


KEPT = "Genset fuel price not researched: template value kept (165 EUR/MWh, gas)"


def _task(steps=(), rows=()):
    return {
        "run": {"profile": REF, "research": {"steps": list(steps), "rows": list(rows)}}
    }


def _fuel(n=None):
    return research.fuel_prices(
        n if n is not None else _network(), run_profiles.resolve(REF)
    )


@pytest.mark.parametrize(
    "steps, rows, line",
    [
        (
            [{"stage": "inputs", "status": "not_granted"}],
            [],
            KEPT + ": research not granted",
        ),
        (
            [{"stage": "inputs", "status": "not_available"}],
            [],
            KEPT + ": research not available",
        ),
        (
            [{"stage": "inputs", "status": "error"}],
            [],
            KEPT + ": research not available",
        ),
        (
            [{"stage": "inputs", "status": "cleanup_failed"}],
            [],
            KEPT + ": research not available",
        ),
        (
            [{"stage": "inputs", "status": "cap_reached"}],
            [],
            KEPT + ": research cost cap reached",
        ),
        ([{"stage": "inputs", "status": "done"}], [], KEPT + ": no verified source"),
        (
            [{"stage": "inputs", "status": "done"}],
            [
                {
                    "for": {"gap": "genset_fuel_price"},
                    "status": "proposed",
                    "verification": "unverified",
                }
            ],
            KEPT + ": no verified source",
        ),
        (
            [{"stage": "inputs", "status": "done"}],
            [
                {
                    "for": {"gap": "genset_fuel_price"},
                    "status": "rejected",
                    "verification": "verified",
                }
            ],
            KEPT + ": the researched sources were rejected",
        ),
        (
            [],
            [
                {
                    "for": {"gap": "genset_fuel_price"},
                    "status": "confirmed",
                    "value": 172.4,
                }
            ],
            KEPT + ": the researched value was not written",
        ),
    ],
)
def test_the_gap_line_names_why_the_template_price_was_kept(steps, rows, line):
    assert research.fuel_gap_line(_task(steps, rows), _fuel()) == line


def test_a_written_price_leaves_no_line_but_names_what_it_left_out():
    n = _network()
    n.generators.loc[["genset_1", "genset_new_2030"], "marginal_cost"] = 172.4
    task = _task(
        rows=[
            {"for": {"gap": "genset_fuel_price"}, "status": "confirmed", "value": 172.4}
        ]
    )
    assert research.fuel_gap_line(task, _fuel(n)) == (
        "Genset fuel price researched; left out, as their fuel curve sets their cost: genset_2; "
        "not written to the other gas generators: gas_chp, gas supply"
    )
    n.remove("Generator", ["gas_chp", "gas supply"])
    assert research.fuel_gap_line(task, _fuel(n)) == (
        "Genset fuel price researched; left out, as their fuel curve sets their cost: genset_2"
    )
    n.generators.loc["genset_2", ["fuel_curve_slope", "fuel_price"]] = float("nan")
    n.generators.at["genset_2", "marginal_cost"] = 172.4
    assert research.fuel_gap_line(task, _fuel(n)) is None


def test_the_gap_line_names_a_mixed_fuel_site_and_an_all_curve_site():
    assert research.fuel_gap_line(_task(), _fuel(_network(genset_3="diesel"))) == (
        "Genset fuel price not researched: template value kept: the gensets burn more than one fuel"
    )
    n = _network()
    gens = n.generators
    gens.loc[gens.carrier == "gas", ["fuel_curve_slope", "fuel_price"]] = [0.25, 0.9]
    confirmed = [
        {"for": {"gap": "genset_fuel_price"}, "status": "confirmed", "value": 172.4}
    ]
    assert research.fuel_gap_line(_task(rows=confirmed), _fuel(n)) == (
        "Genset fuel price not researched: template value kept: every genset's cost is set by its fuel curve"
    )


def test_a_v4_run_gets_no_fuel_line_and_its_report_is_unchanged(tmp_path):
    from harness import run_report

    v4 = run_profiles.resolve("eh_site@4")
    assert research.fuel_prices(_network(), v4) is None
    task = {
        "run": {
            "profile": "eh_site@4",
            "research": {
                "steps": [{"stage": "inputs", "status": "not_granted"}],
                "rows": [],
            },
        }
    }
    source = run_report.harness_source(tmp_path / "t.json", task, v4, _network())
    assert set(source) == {"task", "envelope", "chapters", "coverage"}
    payload, _ = H.build(task, None, [])
    assert payload["gaps"] == []


def test_a_v5_run_without_research_keeps_the_price_and_the_report_says_so(
    fuel_run, tmp_path
):
    from harness import run_report
    from harness import session as harness_session

    task, path, net = fuel_run
    _hold(path)
    run = harness_session.ChatSession(session_id="run:" + task["task_id"])
    inner = Inner()
    out = research.research_turn(
        path,
        run,
        boot=BOOT,
        stage="inputs",
        profile=run_profiles.resolve(REF),
        provider_profile=_profile(),
        base_provider=inner,
        reserving=lambda p: p,
    )
    assert out["status"] == "not_granted" and inner.requests == []
    record = json.loads(path.read_text())
    assert record["run"]["research"]["steps"][-1] == {
        "stage": "inputs",
        "status": "not_granted",
        "gaps": ["import_tariff", "genset_fuel_price"],
    }
    assert (
        net["n"].generators.loc[["genset_1", "genset_new_2030"], "marginal_cost"]
        == 165.0
    ).all()
    source = run_report.harness_source(
        path, record, run_profiles.resolve(REF), net["n"]
    )
    payload, _ = H.build(record, source["envelope"], [], fuel=source["fuel"])
    assert payload["gaps"][-1] == KEPT + ": research not granted"


def test_the_gap_line_names_a_site_without_gensets_and_unreadable_prices():
    n = _network()
    n.remove("Generator", ["genset_1", "genset_new_2030", "genset_2"])
    confirmed = [
        {"for": {"gap": "genset_fuel_price"}, "status": "confirmed", "value": 172.4}
    ]
    assert research.fuel_gap_line(_task(rows=confirmed), _fuel(n)) == (
        "Genset fuel price not researched: the site has no genset"
    )
    n = _network()
    n.generators["marginal_cost"] = n.generators["marginal_cost"].astype(object)
    n.generators.at["genset_1", "marginal_cost"] = "n/a"
    fuel = _fuel(n)
    assert fuel == {"key": "genset_fuel_price", "error": "fuel_read_failed"}
    assert research.fuel_gap_line(_task(), fuel) == (
        "Genset fuel price not researched: template value kept: "
        "the gensets' prices could not be read"
    )


def test_a_run_record_without_research_or_steps_reads_as_no_source():
    """The None-guards on a record the research step never touched."""
    for task in (
        {},
        {"run": None},
        {"run": {"research": None}},
        {"run": {"research": {"rows": None, "steps": None}}},
        {
            "run": {
                "research": {"rows": [{"for": None, "status": "proposed"}], "steps": []}
            }
        },
    ):
        assert research.fuel_gap_line(task, _fuel()) == KEPT + ": no verified source"
    for prof in ({}, {"research": None}, {"research": {"gaps": None}}):
        assert research.fuel_prices(_network(), prof) is None


def test_a_row_without_a_value_is_never_written(fuel_run):
    with pytest.raises(research.ResearchError) as exc:
        research._fuel_steps({**_row(), "value": None}, GAP, "inputs.research.x")
    assert exc.value.kind == "research_range_not_applicable"


def test_a_chp_and_a_fuel_supply_generator_are_never_written(fuel_run):
    """GK B (70) N1: the carrier's other Generators keep their own price."""
    task, path, net = fuel_run
    row = _row()
    _rows(path, row)
    research.settle(path, row["id"], "confirmed", by="u1")
    names = json.loads(path.read_text())["steps"][-1]["args"]["names"]
    assert "gas_chp" not in names and "gas supply" not in names
    assert research.fuel_gensets(net["n"], GAP)[3] == ["gas_chp", "gas supply"]


@pytest.mark.parametrize(
    "quote",
    [
        # N2: an electrical marker does not save a quote that also prices fuel energy
        "Gas genset: EUR 172.40 per MWh of electrical output (LHV fuel basis)",
        # N3: the electrical unit must be the stated value's own
        "Grid electricity price EUR 200/MWh; diesel EUR 172.40 per MWh",
        "EUR 172.40 per MWh; genset efficiency 38 % per MWh electrical",
        # N3a: an electrical unit beyond the value's window is not its own
        "diesel EUR 172.40 per MWh; the site notes that the comparable grid tariff is "
        "quoted per MWh electrical",
    ],
)
def test_the_electrical_unit_belongs_to_the_value_and_no_fuel_energy_is_priced(quote):
    with pytest.raises(research.ResearchError) as exc:
        research._check_electrical(quote, 172.4)
    assert exc.value.kind == "research_unit_mismatch"


@pytest.mark.parametrize(
    "quote",
    [
        "Grid electricity price EUR 200/MWh; gas genset EUR 172.40 per MWh electrical",
        "gas genset fuel cost 172,40 EUR/MWhe",
        "EUR 172.40 per MWh of genset electrical output",
        "EUR 172.40 per electrical MWh",
    ],
)
def test_the_value_stated_per_mwh_electrical_passes(quote):
    research._check_electrical(quote, 172.4)


@pytest.mark.parametrize("value", [None, "172.4", True, float("nan")])
def test_a_value_that_is_not_a_number_is_refused(value):
    with pytest.raises(research.ResearchError) as exc:
        research._check_electrical(QUOTE, value)
    assert exc.value.kind == "research_unit_mismatch"


def test_a_v4_report_is_byte_equal_to_masters():
    """GK B (70) N4: the evidence of a @4 run, as master 941d516a1 built it."""
    from services.reports import evidence

    task = {
        "run": {
            "profile": "eh_site@4",
            "mode": "stepwise",
            "status": "done",
            "stages": {"inputs": {"status": "passed"}},
            "research": {
                "steps": [
                    {
                        "stage": "inputs",
                        "status": "not_granted",
                        "gaps": ["import_tariff"],
                    }
                ],
                "rows": [],
            },
        },
        "steps": [],
    }
    ev = evidence.collect_evidence(
        study_report=None,
        eh_report=None,
        worksheet=None,
        harness_run={
            "task": task,
            "envelope": None,
            "chapters": ["sizing", "mc", "investment_case"],
            "coverage": [],
        },
    )
    fixture = (
        pathlib.Path(__file__).parent
        / "fixtures"
        / "sh34_eh_site_v4_evidence_master.json"
    )
    assert ev.model_dump_json() + "\n" == fixture.read_text()
