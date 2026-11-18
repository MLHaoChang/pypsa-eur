"""Every entity of one component type, in bounded answer sections (issue 10, S6a).

generator-dispatch/1 (Generators) and branch-loading/1 (Lines) admit entity_id "*"
with their class; every other profile treats "*" as a plain name, as before.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import json
import re

import pandas as pd
import pytest

from harness import assurance_scope, calculations
from harness.assurance_shapes import RECEIPTS
from services import workflow_tools
from tests.assurance_acceptance.support import approval, public_text, text_turn
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider
from tests.test_workflow_tools import baseline  # noqa: F401

GEN, BRANCH = "generator-dispatch/1", "branch-loading/1"

# K2: recorded on master 4a00498b, before this change, from the same fixtures.
MASTER_GENERATOR = (
    "Generator G1; requested snapshots (2026-01-01T00:00:00 to 2026-01-01T02:00:00).\n"
    "Generator energy: 28 MWh.\nMarginal dispatch expenditure: 160 EUR.\nPeak generator output: 8 MW.\n"
    "Energy uses generator snapshot weights; expenditure uses objective snapshot weights. "
    "Expenditure excludes fixed, startup, shutdown and quadratic costs; it is not total system cost. "
    "Currency follows the model's EUR convention; no price year is inferred."
)
MASTER_BRANCH = (
    "Branch L12; requested snapshots (2026-01-01T00:00:00 to 2026-01-01T02:00:00).\n"
    "1 Lines and 0 Transformers; 0 Links excluded.\nPeak loading against the rating: 100 %.\n"
    "Weighted hours above the rating: 0 h.\nPeak active-power flow: 8 MW.\n"
    "Rating (s_nom_opt if extendable, else s_nom): 8 MVA.\n"
    "Active power (P only), from the LOPF: an active-power proxy, not an AC thermal assessment; "
    "no N-1 and no voltage limits. Flow is the larger terminal magnitude; the limit is the rating "
    "times s_max_pu per snapshot. Hours use generator snapshot weights. Lines and Transformers only; "
    "Links are excluded. A branch with any missing flow or zero limit in the window is unavailable."
)


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv(
        "PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", f"{GEN},{BRANCH},bus-price/1"
    )


class Echo(NumericalProvider):
    """Returns the offered ledger, after an optional edit."""

    def __init__(self, mutate=None):
        super().__init__({}, mutate)

    def stream(self, request):
        from harness.providers.fake import FakeProvider

        content = request.messages[-1]["content"]
        text = (
            content
            if isinstance(content, str)
            else "\n".join(b.get("text", "") for b in content)
        )
        found = re.findall(
            r'<untrusted_data>(\{"schema_version".*?\})</untrusted_data>', text
        )
        ledger = json.loads(found[0]) if found else {"schema_version": 1, "blocks": []}
        if self.mutate:
            self.mutate(ledger)
        self._turns.append(text_turn(json.dumps(ledger)))
        yield from FakeProvider.stream(self, request)


def solved(ctx):
    n = ctx.network
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    ctx.solver_state.update(status="completed", condition="optimal")
    workflow_tools._record_solve_inputs(n, ctx.solver_state["solver_config"])
    return ctx


@pytest.fixture
def grid(baseline):
    """The branch-loading fixture: G1 at B1 feeds L2 at B2 over L12; G2 at B2."""
    ctx = baseline[0]
    n = ctx.network
    n.set_snapshots(pd.date_range("2026-01-01", periods=3, freq="h"))
    n.loads_t.p_set = pd.DataFrame({"L1": [0, 0, 0]}, index=n.snapshots)
    n.add("Bus", "B2", v_nom=110)
    n.add("Load", "L2", bus="B2", p_set=[4, 9, 6])
    n.add("Generator", "G2", bus="B2", p_nom=20, marginal_cost=100)
    n.add("Line", "L12", bus0="B1", bus1="B2", s_nom=8, x=0.1, r=0.01)
    n.lines_t.s_max_pu = pd.DataFrame({"L12": [1, 1, 0.5]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, "generators"] = [2, 1, 3]
    return ctx


def fleet(ctx, count, lines=0):
    """`count` generators in all (G1 kept), names chosen so string order is not
    insertion order; optionally `lines` parallel lines L12 + Lx01.. between B1 and B2."""
    n = ctx.network
    for i in range(2, count):
        n.add("Generator", f"X{count - i:03d}", bus="B1", p_nom=1, marginal_cost=50 + i)
    for i in range(1, lines):
        n.add("Line", f"Lx{i:02d}", bus0="B1", bus1="B2", s_nom=8, x=0.1, r=0.01)
    return solved(ctx)


def every(ctx, profile=GEN, **options):
    return {
        "version": 1,
        "profile": profile,
        "project_id": ctx.project_uuid,
        "entity_id": "*",
        "component_class": assurance_scope.EVERY_ENTITY.get(profile, "Generator"),
        "retain": False,
        **options,
    }


def ask(checked, scope, mutate=None, message="Check them all."):
    frames = checked.run(Echo(mutate), message=message, checked_scope=scope)
    return frames, approval(frames)


def sections_of(record):
    return record["receipt"].get("sections")


# --- K2: one-entity answers are byte-identical to master. ---


def test_one_generator_answer_is_byte_identical_to_master(grid, checked):
    ctx = solved(grid)
    scope = {
        "version": 1,
        "profile": GEN,
        "project_id": ctx.project_uuid,
        "entity_id": "G1",
        "retain": False,
    }
    frames, record = ask(checked, scope)
    assert public_text(frames) == MASTER_GENERATOR
    assert (
        record["receipt"]["policy_version"] == "1"
        and "sections" not in record["receipt"]
    )


def test_one_line_answer_is_byte_identical_to_master(grid, checked):
    ctx = solved(grid)
    scope = {
        "version": 1,
        "profile": BRANCH,
        "project_id": ctx.project_uuid,
        "entity_id": "L12",
        "retain": False,
    }
    frames, _ = ask(checked, scope)
    assert public_text(frames) == MASTER_BRANCH


# --- K1: the scope form, its grammar and its closure. ---


def test_grammar_names_every_generator_and_every_line():
    found = assurance_scope.from_message(
        "Show me the key results for every generator", "p", {}
    )
    assert found == {
        "version": 1,
        "profile": GEN,
        "project_id": "p",
        "entity_id": "*",
        "component_class": "Generator",
        "retain": False,
    }
    found = assurance_scope.from_message(
        "show the loading for all lines; from 2026-01-01T00:00 to 2026-01-01T02:00",
        "p",
        {},
    )
    assert found["profile"] == BRANCH and found["component_class"] == "Line"
    assert found["start"] == "2026-01-01T00:00"
    assert (
        assurance_scope.from_message("show the loading for every generator", "p", {})
        is None
    )


@pytest.mark.parametrize(
    "extra", [{"retain": True}, {"chart": ["p"]}, {"comparison_evidence_id": "0" * 36}]
)
def test_the_population_form_refuses_anything_beyond_its_scope(extra):
    with pytest.raises(ValueError, match="unsupported_population_scope"):
        assurance_scope.validate(
            {
                "version": 1,
                "profile": GEN,
                "project_id": "p",
                "entity_id": "*",
                "component_class": "Generator",
                "retain": False,
                **extra,
            }
        )


def test_a_transformer_or_another_profile_keeps_master_behaviour():
    scope = {
        "version": 1,
        "profile": BRANCH,
        "project_id": "p",
        "entity_id": "*",
        "component_class": "Transformer",
        "retain": False,
    }
    assert not assurance_scope.every_entity(assurance_scope.validate(scope))
    price = {
        "version": 1,
        "profile": "bus-price/1",
        "project_id": "p",
        "entity_id": "*",
        "retain": False,
    }
    assert not assurance_scope.every_entity(assurance_scope.validate(price))
    with pytest.raises(ValueError, match="unsupported_component_class"):
        assurance_scope.validate({**price, "component_class": "Generator"})


def test_another_profile_still_refuses_a_population(grid, checked):
    ctx = solved(grid)
    scope = {
        "version": 1,
        "profile": "bus-price/1",
        "project_id": ctx.project_uuid,
        "entity_id": "*",
        "retain": False,
    }
    frames, record = ask(checked, scope)
    receipt = record["receipt"]
    assert receipt["policy_version"] == "1" and receipt["evidence_ids"] == []
    assert (
        receipt["request_scope"]["reason"] == "unknown_bus"
    )  # as on master: "*" is a plain name


def test_the_entity_list_is_resolved_sorted_and_recorded(grid, checked):
    from services import assurance_records as records

    ctx = fleet(grid, 5)
    _, record = ask(checked, every(ctx))
    evidence = records.evidence(
        records.actor_id(), record["receipt"]["evidence_ids"][0]
    )
    assert evidence["raw"]["entities"] == ["G1", "G2", "X001", "X002", "X003"]
    assert evidence["request_scope"]["entity_id"] == "*"
    # A replay recalculates the recorded set, never a fresh read of the network.
    facts, slots = calculations.scoped_calculate(
        evidence["raw"], evidence["request_scope"]
    )
    assert facts == evidence["facts"] and slots == evidence["slots"]


# --- Sections in the real turn. ---


def test_twenty_slots_is_one_section_without_a_heading(grid, checked):
    ctx = fleet(grid, 10)
    frames, record = ask(
        checked, every(ctx, metrics=["dispatch_energy", "peak_dispatch"])
    )
    sections = sections_of(record)
    assert (
        record["receipt"]["policy_version"] == "4"
        and "result_slots" not in record["receipt"]
    )
    assert [s["heading"] for s in sections] == [None]
    assert len(sections[0]["result_slots"]) == 20
    text = public_text(frames)
    assert text.startswith("All 10 generators; requested snapshots")
    assert "of 10" not in text
    RECEIPTS["4"].model_validate(record["receipt"])


def test_twenty_one_slots_is_two_sections(grid, checked):
    ctx = fleet(grid, 7)
    frames, record = ask(checked, every(ctx))
    sections = sections_of(record)
    assert [s["heading"] for s in sections] == [
        "Generators 1–7 of 7",
        "Generators 7 of 7",
    ]
    assert [len(s["result_slots"]) for s in sections] == [20, 1]
    text = public_text(frames).split("\n")
    assert text[1] == "Generators 1–7 of 7" and "Generators 7 of 7" in text
    assert text[2] == "G1, Generator energy: 28 MWh."
    assert (
        len(record["receipt"]["checked_block_ids"]) == 21
        and record["receipt"]["request_complete"]
    )


def test_every_line_is_sectioned_with_its_links_line(grid, checked):
    ctx = fleet(grid, 2, lines=6)
    frames, record = ask(checked, every(ctx, BRANCH))
    assert [s["heading"] for s in sections_of(record)] == [
        "Lines 1–5 of 6",
        "Lines 6 of 6",
    ]
    text = public_text(frames)
    assert (
        "All 6 lines; requested snapshots" in text and "\n0 Links excluded.\n" in text
    )


def test_a_hundred_slots_is_five_sections(grid, checked):
    ctx = fleet(grid, 100)
    frames, record = ask(checked, every(ctx, metrics=["dispatch_energy"]))
    headings = [s["heading"] for s in sections_of(record)]
    assert headings == [f"Generators {a}–{a + 19} of 100" for a in (1, 21, 41, 61, 81)]


def test_a_hundred_and_one_slots_is_refused_before_any_calculation(
    grid, checked, monkeypatch
):
    ctx = fleet(grid, 101)
    calls = []
    real = calculations.source
    monkeypatch.setattr(calculations, "source", lambda *a: calls.append(a) or real(*a))
    frames, record = ask(checked, every(ctx, metrics=["dispatch_energy"]))
    assert public_text(frames) == (
        "I can check up to 100 values in one answer and this needs 101: "
        "ask for one carrier or one bus at a time."
    )
    assert calls == [] and record["receipt"]["evidence_ids"] == []
    assert record["receipt"]["request_scope"]["reason"] == "too_many_values"


def test_the_refusal_names_the_real_count(grid, checked):
    ctx = fleet(grid, 47)
    frames, _ = ask(checked, every(ctx))
    assert public_text(frames) == (
        "I can check up to 100 values in one answer and this needs 141: "
        "ask for one carrier or one bus at a time."
    )


# --- K3: the gate in the real turn. ---


def withheld(record):
    return record["receipt"]["evidence_ids"] == [] and record["verdict"] == "limited"


def test_a_value_moved_to_another_section_is_withheld(grid, checked):
    ctx = fleet(grid, 7)

    def move(ledger):
        # Same counts: section 2's one value swapped for one of section 1's.
        first, second = ledger["sections"][0]["blocks"], ledger["sections"][1]["blocks"]
        first[0], second[0] = second[0], first[0]

    _, record = ask(checked, every(ctx), move)
    assert withheld(record)


def test_a_changed_heading_or_value_is_withheld(grid, checked):
    ctx = fleet(grid, 7)

    def heading(ledger):
        ledger["sections"][0]["heading"] = "Generators 1–8 of 7"

    def value(ledger):
        ledger["sections"][1]["blocks"][0]["value"] += 1

    assert withheld(ask(checked, every(ctx), heading)[1])
    assert withheld(ask(checked, every(ctx), value)[1])


def test_sections_outside_a_population_scope_are_withheld(grid, checked):
    ctx = solved(grid)
    scope = {
        "version": 1,
        "profile": GEN,
        "project_id": ctx.project_uuid,
        "entity_id": "G1",
        "retain": False,
    }

    def add(ledger):
        ledger["sections"] = [{"heading": None, "blocks": ledger["blocks"]}]
        ledger["blocks"] = []

    assert withheld(ask(checked, scope, add)[1])
