"""
WP3 — the number audit (`services/reports/number_audit.py`).

A check, not a rewrite (assessment §3.3): every numeric token in the prose
is matched against the flattened evidence within the token's OWN displayed
precision; a miss is flagged, never edited. Years, ordinals and section ids
are exempt by pattern. Pure — no I/O.
"""
from __future__ import annotations

import pytest

from services.reports.evidence import NumberFact
from services.reports.number_audit import audit, extract_numbers

_FACTS = [
    NumberFact(path="/headline/mc_lole_h", value=3.21, unit="h"),
    NumberFact(path="/headline/cost_at_target_eur", value=1_234_567.89, unit="€"),
    NumberFact(path="/headline/ens_cap_permyriad", value=10.0, unit="‱"),
    NumberFact(path="/sections/sizing/payload/total_p_nom_mw", value=250.0, unit="MW"),
    NumberFact(path="/sections/fmea_top/payload/top/0/rank", value=1, unit="count"),
]


# ── tokeniser ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, value, unit", [
    ("1,234.5 MWh", 1234.5, "MWh"),
    ("1.234,5 MWh", 1234.5, "MWh"),
    ("1 234,5 MWh", 1234.5, "MWh"),
    ("1,234,568 €", 1234568.0, "€"),
    ("1.234.568 €", 1234568.0, "€"),
    ("12 %", 12.0, "%"),
    ("12%", 12.0, "%"),
    ("€ 3.2M", 3.2e6, "€"),
    ("3.2 M€", 3.2e6, "€"),
    ("€3.2k", 3200.0, "€"),
    ("2.5 ‱", 2.5, "‱"),
    ("3.2 h/yr", 3.2, "h/yr"),
    ("250 MW", 250.0, "MW"),
    ("42.5 €/MWh", 42.5, "€/MWh"),
    ("6.1 €/kg", 6.1, "€/kg"),
    ("0.05", 0.05, None),
])
def test_extract_numbers_reads_separators_units_and_scales(text, value, unit):
    tokens = extract_numbers(f"The value is {text} in this run.")
    assert len(tokens) == 1, tokens
    tok = tokens[0]
    assert tok.value == pytest.approx(value)
    assert tok.unit_hint == unit
    assert tok.text.strip() == text
    assert f"The value is {text}"[tok.start:tok.end].strip() == text


def test_extract_numbers_exempts_years_ordinals_and_section_ids():
    text = ("1. In 2030 the plan (see sec:fmea_top and sec:v2_1) meets 3.2 h/yr; "
            "2) the 2035 case was skipped.")
    tokens = extract_numbers(text)
    assert [t.text.strip() for t in tokens] == ["3.2 h/yr"]


def test_a_year_with_a_unit_is_a_number():
    tokens = extract_numbers("costing 2030 €")
    assert len(tokens) == 1 and tokens[0].value == 2030


def test_no_numbers_gives_empty_lists():
    result = audit(["Nothing numeric here."], _FACTS)
    assert result.verified == [] and result.unverified == []
    assert extract_numbers("") == []


# ── matching ────────────────────────────────────────────────────────────────

def test_rounded_display_verifies_within_its_own_precision():
    result = audit(["MC LOLE was 3.2 h/yr."], _FACTS)
    assert [v.text for v in result.verified] == ["3.2 h/yr"]
    assert result.verified[0].path == "/headline/mc_lole_h"
    assert result.unverified == []


def test_a_wrong_number_is_unverified():
    result = audit(["MC LOLE was 4.0 h/yr."], _FACTS)
    assert result.verified == []
    assert result.unverified == ["4.0 h/yr"]


def test_precision_is_the_tokens_not_the_facts():
    # 3.2 matches 3.21 and 3.15; 3.21 does not match 3.3.
    assert audit(["3.2 h"], [NumberFact(path="/a", value=3.15, unit="h")]).unverified == []
    assert audit(["3.2 h"], [NumberFact(path="/a", value=3.21, unit="h")]).unverified == []
    assert audit(["3.21 h"], [NumberFact(path="/a", value=3.3, unit="h")]).unverified == ["3.21 h"]


def test_years_are_exempt():
    result = audit(["By 2030 the MC LOLE is 3.2 h/yr."], _FACTS)
    assert [v.text for v in result.verified] == ["3.2 h/yr"]
    assert result.unverified == []


def test_thousands_separators_both_ways_verify_a_rounded_cost():
    for text in ("1.234.568 €", "1,234,568 €", "1 234 568 €", "€1,234,568"):
        result = audit([f"The cost at target is {text}."], _FACTS)
        assert [v.path for v in result.verified] == ["/headline/cost_at_target_eur"], text
        assert result.unverified == [], text


def test_percent_never_matches_a_euro_fact():
    facts = [NumberFact(path="/x/cost_eur", value=12.0, unit="€")]
    result = audit(["Roughly 12 % of demand."], facts)
    assert result.verified == [] and result.unverified == ["12 %"]
    # And a permyriad fact matches a permyriad token.
    result = audit(["capped at 10 ‱."], _FACTS)
    assert [v.path for v in result.verified] == ["/headline/ens_cap_permyriad"]


def test_scaled_suffix_expands_before_matching():
    result = audit(["about € 1.2M over the period"], _FACTS)
    assert [v.path for v in result.verified] == ["/headline/cost_at_target_eur"]
    result = audit(["about € 1.3M over the period"], _FACTS)
    assert result.unverified == ["€ 1.3M"]


def test_unit_per_year_normalises_to_the_facts_unit():
    # `h/yr` in prose, `h` on the fact (from the key `mc_lole_h`).
    assert audit(["3.21 h/yr"], _FACTS).verified[0].path == "/headline/mc_lole_h"
    # A bare number matches a fact of any unit; a unit-bearing token needs a
    # compatible fact.
    assert audit(["3.21"], _FACTS).unverified == []
    assert audit(["3.21 MW"], _FACTS).unverified == ["3.21 MW"]


def test_bullets_and_repeats_are_audited_once_per_distinct_text():
    result = audit(["3.2 h/yr and again 3.2 h/yr", "250 MW"], _FACTS)
    assert [v.text for v in result.verified] == ["3.2 h/yr", "250 MW"]
