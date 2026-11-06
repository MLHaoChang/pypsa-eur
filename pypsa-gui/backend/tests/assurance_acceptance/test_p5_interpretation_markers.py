"""Interpretation markers (AA-03 S5, A2.5): unsupported wording is withheld.

A model interpretation that claims a whole population, a cause or an optimum is
withheld before any reviewer call; the checked facts, derived entries and
predicates of the same draft still publish. The lists are English only
(AA03-FREEZE-1 item 5); other languages stay pending under ticket 09.
"""

from __future__ import annotations

import json

import pytest

from harness import interpretation_markers as markers
from tests.assurance_acceptance.support import approval, public_text
from tests.assurance_acceptance.test_p4_population_predicates import (
    add_loads,
    ask,
    draft,
    raw,
)

ALL_MARKERS = [
    (name, marker) for name, words in markers.LISTS.items() for marker in words
]

# Recorded, not a skip: no non-English marker list exists (ticket 09).
_pending = {
    "non_english_markers": "ticket 09: marker lists are English only (AA03-FREEZE-1 item 5)",
}


# ── the lists ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "marker"), ALL_MARKERS)
def test_every_marker_hits_its_list(name, marker):
    text = f"The configured inputs show that {marker} applies here."
    assert markers.find(text) is not None
    # Each list catches its own wording on its own.
    assert markers._PATTERNS[name].search(text.casefold())


@pytest.mark.parametrize(
    "text",
    [
        "ALL loads are configured.",
        "Every Load Is Configured.",
        "ＡＬＬ loads are configured.",  # fullwidth ALL, NFKC
        "It is the least-cost choice.",
        "That is nobody's input.",
        "The value rises due\nto the input.",
        "The value is driven  by the input.",
        "It leads -to a change.",
        "As a\tresult, it changes.",
    ],
)
def test_case_width_hyphen_apostrophe_and_spacing_variants_hit(text):
    assert markers.find(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "The overall input is configured.",
        "The install date is recorded.",
        "The topology is configured.",
        "The bottomless tank is configured.",
        "thereforeX is a name.",
        "The configured load is positive.",
        "The allocation is configured.",
        "Thusly configured.",
    ],
)
def test_a_marker_inside_a_longer_word_does_not_hit(text):
    assert markers.find(text) is None


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("population", "All loads are configured."),
        ("causal", "The load is set because of the input."),
        ("optimality", "The configured load is optimal."),
    ],
)
def test_find_names_the_list(name, text):
    assert markers.find(text) == name


def test_non_english_wording_is_a_named_pending_row():
    assert "ticket 09" in _pending["non_english_markers"]
    # Documents the gap: German "alle" is not on an English list.
    assert markers.find("Alle Lasten sind konfiguriert.") is None


# ── end to end ───────────────────────────────────────────────────────────


@pytest.fixture
def reviewer_spy(monkeypatch):
    calls = []

    def review(turn, provider, model, payload, ctx):
        calls.append(payload)
        return "passed"

    monkeypatch.setattr("harness.reviewer.review", review)
    monkeypatch.setenv("PYPSA_GUI_ANSWER_REVIEW_MODE", "required")
    return calls


def _with_interpretation(text):
    data = json.loads(draft(raw("all")))
    data["interpretations"] = [{"id": "i1", "text": text, "block_ids": ["fact-1"]}]
    return json.dumps(data)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("population", "Every configured load meets the bound."),
        ("causal", "The bound holds because the loads are configured."),
        ("optimality", "The configured loads are optimal."),
    ],
)
def test_a_marker_withholds_only_the_interpretation(
    baseline, harness, reviewer_spy, name, text
):
    add_loads(L2=7, L3=9)
    assert markers.find(text) == name
    frames = ask(harness, _with_interpretation(text))
    approved = approval(frames)
    # Facts and the predicate publish unchanged; the reviewer is never called,
    # so review mode "required" cannot overrule the marker. A withheld
    # interpretation is a limitation, as for any other withheld one.
    assert approved["verdict"] == "limited"
    receipt = approved["receipt"]
    assert "Interpretation withheld: withheld_invalid_draft" in receipt["limitations"]
    assert receipt["reviewer_status"] == "withheld_invalid_draft"
    assert receipt["policy_version"] == "3"
    assert [p["id"] for p in receipt["predicates"]] == ["p1"]
    assert reviewer_spy == []
    shown = public_text(frames)
    assert "every one of all 3 Load rows" in shown
    assert text not in shown and "Reviewed interpretation" not in shown


def test_a_clean_interpretation_still_reaches_the_reviewer(
    baseline, harness, reviewer_spy
):
    add_loads(L2=7, L3=9)
    text = "The configured load is positive."
    frames = ask(harness, _with_interpretation(text))
    receipt = approval(frames)["receipt"]
    assert receipt["reviewer_status"] == "passed"
    assert len(reviewer_spy) == 1
    assert "Reviewed interpretation: " + text in public_text(frames)


def test_a_marker_in_a_derived_draft_keeps_the_derived_entry(
    baseline, harness, reviewer_spy
):
    add_loads(L2=7, L3=9)
    data = json.loads(draft(raw("all")))
    data["derived"] = [
        {
            "id": "d1",
            "kind": "derived",
            "formula_id": "unit_convert/1",
            "operands": ["fact-1"],
            "to_unit": "kW",
            "value": 5000,
            "unit": "kW",
        }
    ]
    data["interpretations"] = [
        {"id": "i1", "text": "Hence the load is set.", "block_ids": ["fact-1"]}
    ]
    frames = ask(harness, json.dumps(data))
    receipt = approval(frames)["receipt"]
    assert receipt["reviewer_status"] == "withheld_invalid_draft"
    assert [d["id"] for d in receipt["derived"]] == ["d1"]
    assert [p["id"] for p in receipt["predicates"]] == ["p1"]
    assert reviewer_spy == []


def test_a_hostile_entity_name_alone_does_not_hit(baseline, harness, reviewer_spy):
    # Entity ids are replaced before the check, as for the number rule: a load
    # named "Top" is a name, not a ranking claim.
    add_loads(Top=7)
    data = json.loads(draft(raw("all")))
    data["blocks"].append(
        {
            "id": "fact-2",
            "kind": "observed",
            "entity_id": "Top",
            "metric": "p_set",
            "value": 7,
            "unit": "MW",
            "source": {"tool_use_id": "list-loads", "path": "/Top/p_set"},
        }
    )
    data["interpretations"] = [
        {"id": "i1", "text": "Load Top is configured.", "block_ids": ["fact-2"]}
    ]
    frames = ask(harness, json.dumps(data))
    assert approval(frames)["receipt"]["reviewer_status"] == "passed"
    assert len(reviewer_spy) == 1


def test_the_marker_runs_with_review_disabled_too(baseline, harness, monkeypatch):
    monkeypatch.delenv("PYPSA_GUI_ANSWER_REVIEW_MODE", raising=False)
    add_loads(L2=7, L3=9)
    frames = ask(harness, _with_interpretation("The loads are the cheapest."))
    assert approval(frames)["receipt"]["reviewer_status"] == "withheld_invalid_draft"


# ── the checked-answer prompt (GK B (29) M7) ─────────────────────────────


def test_the_prompt_describes_derived_predicates_and_markers():
    import re

    from harness import derived, predicates
    from harness.assurance import PROMPT

    named = set(re.findall(r"\b[a-z_]+/1\b", PROMPT))
    assert set(derived.FORMULAS) <= named
    for quantifier in predicates.QUANTIFIERS:
        assert f'"{quantifier}"' in PROMPT
    for example in (
        "all",
        "every",
        "none",
        "top",
        "largest",
        "because",
        "due to",
        "leads to",
        "optimal",
        "best",
        "cheapest",
    ):
        assert example in PROMPT
        assert markers.find(example) is not None
    assert "such an interpretation is withheld" in PROMPT
    assert "only a predicate states a whole-class claim" in PROMPT
