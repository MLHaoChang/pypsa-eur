"""
The decision report (guided investment study MVP-1, phase S7).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S7;
review v1 S6, N4, N5, N7; the S0-S6 gate carries). Spec §4.7 and §7.

:func:`assemble_decision_report` builds a :class:`models.study.DecisionReport`
from what the last run left on disk — the stored findings and the option
cases rebuilt from the option forks' saved networks — and solves nothing.
:func:`build_decision_report` is the pure half (tests call it directly).

* **Sections** follow spec §7 (:data:`SECTIONS`), each with ``status``,
  the ``facts`` it cites, its chart refs (``figures``), its fixed template
  ``prose`` and a ``payload`` of tables.
* **Facts.** ``report.facts`` maps every ``fact_id`` to a ``Figure`` (value,
  unit, basis, currency year, engine, fidelity); a figure that cannot be
  stated is null with its flag (ADR-0001). The verdict's facts are copied
  from the findings unchanged, so every verdict KPI equals the findings'.
* **Prose** is fixed template paragraphs whose only numbers are
  ``{{fact_id}}`` references. ``ai_paragraphs`` is present and empty.
  :func:`validate_prose` is the renderers' guard: a numeric token
  (a digit not preceded by a letter) outside a reference, or an unresolved reference,
  fails the render naming the section and the paragraph; the units ``CO2``,
  ``H2``, ``N-1`` and ``24/7`` are allowed; a reference to a NULL fact
  renders as "not established" (gate S6 carry), never a rejection.
* **Disclosures** (the BESS set, :data:`DISCLOSURE_RULES`, through the
  generic section helpers of ``services/report_sections.py`` lifted out of
  the adequacy write-up) render BEFORE the first number. Every honesty code
  the report shows carries its human sentence: the tariff's
  ``honesty_help`` for the tariff's codes, :data:`HELP` for the study's own.
  DECISION: disclosure sentences are DATA, rendered outside
  ``validate_prose``'s scope (a supplied tariff's sentence is its author's
  text, like a ledger source); the study's own :data:`HELP` sentences are
  nevertheless pinned digit-free by a test.
* **Stale** (:func:`stale_reasons`): the ledger hash, or any OPTION fork's
  network hash read from disk now, differs from the hashes recorded at
  findings time — through ``services/study/run_hashes.py``, the rule the
  case route's 409 uses; tornado forks are not in the map. A study already
  marked stale (a copied record, a ledger or intake edited during the run)
  carries its reasons into the report.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from models.study import (
    MVP1_BASIS,
    AssumptionsLedger,
    DecisionQuestion,
    DecisionReport,
    DecisionStudy,
    Figure,
    Findings,
    InvestmentCase,
    ProseParagraph,
    ReportDisclosure,
    ReportSection,
    Tariff,
)
from services import report_sections
from services.results.economics_caveats import ZERO_PROFIT_BY_CONSTRUCTION
from services.study import questions as Q

__all__ = [
    "DISCLOSURE_RULES", "HELP", "ProseError", "ReportInputs", "SECTIONS",
    "assemble_decision_report", "build_decision_report", "format_fact", "help_for",
    "money_unit", "report_available", "stale_reasons", "validate_prose", "xml_safe",
    "xml_safe_report",
]

# Spec §7, in order: (section id, heading).
SECTIONS: tuple[tuple[str, str], ...] = (
    ("executive_summary", "Executive summary and recommendation"),
    ("question", "The question, the baseline and the options considered"),
    ("recommended_system", "Recommended system and how it operates"),
    ("economics", "Economics"),
    ("drivers", "What drives the result"),
    ("robustness", "Robustness"),
    ("reliability", "Reliability and resilience"),
    ("assumptions", "Assumptions"),
    ("limitations", "Limitations and next steps"),
    ("appendix", "Appendix"),
)
TITLES = dict(SECTIONS)
_CURRENCIES = frozenset({"EUR", "USD", "GBP", "CHF"})
_RUN_ENGINES = frozenset({"lp", "lp_duals", "bill_calculator", "cash_flow_expander"})

# ── the code -> sentence table (the study's own codes) ────────────────────
#
# Digit-free by test (`test_every_help_sentence_is_digit_free`), although
# disclosures render outside `validate_prose`'s scope (see the docstring).

HELP: dict[str, str] = {
    # the basis and the method
    "basis_real_pre_tax_no_subsidy": (
        "Money is in real terms, before tax and without subsidy, in the currency year "
        "stated beside each figure."),
    "currency_year_stated": "Every money figure states the currency year it is expressed in.",
    "single_year_extrapolated": (
        "One representative year of operation is repeated over the whole horizon."),
    "perfect_foresight_dispatch": (
        "The model dispatches the battery knowing the whole year's load and prices in "
        "advance; a real controller will capture less."),
    "demand_charge_perfect_foresight": (
        "The demand-charge saving assumes the battery knows every month's peak in "
        "advance; a real controller misses some peaks, so this stream is an upper bound."),
    "duals_include_demand_charge": (
        "The model's energy prices include the demand charge's shadow price, so they are "
        "not market prices."),
    "no_degradation": "Battery degradation is not modelled.",
    "inverter_replaced_at_its_lifetime": (
        "The inverter is replaced at the end of its own lifetime, inside the horizon."),
    "battery_upfront_from_ledger_not_back_calculated": (
        "The battery's purchase cost comes from the assumptions ledger; other screens that "
        "back-calculate it from an annualised cost show a different upfront figure."),
    "battery_fom_on_inverter_investment_only": (
        "The battery's fixed operating cost is charged on the inverter investment only."),
    "salvage_annuity_pv_remaining_life": (
        "Equipment with life left at the end of the horizon is credited with the present "
        "value of the annuities the model charged for that remaining life."),
    "salvage_not_computed": "The residual value at the end of the horizon could not be computed.",
    "npv_excludes_uncomputed_salvage": (
        "The NPV leaves out a residual value that could not be computed."),
    "market_revenue_at_duals_excluded_from_cash_flow": (
        "Revenue at the model's prices is reported beside the case and excluded from the "
        "cash flow: the bill already prices the same energy."),
    "lcos_excludes_charging_energy_cost": (
        "The pro forma's levelised cost of storage leaves out the energy bought to charge "
        "the battery."),
    "npv_nonnegative_at_optimum_by_construction": (
        "At the optimiser's chosen size the NPV cannot be negative: the optimiser only "
        "builds what pays for itself, so the sign restates its choice and is not "
        "independent evidence."),
    "irr_and_discounted_payback_bounded_at_optimum_by_construction": (
        "For the same reason the IRR is at least the discount rate and the discounted "
        "payback is within the horizon; the magnitudes inform, the bounds do not."),
    "size_zero_no_investment": (
        "The optimiser sized this battery to zero: no investment is worth making, and its "
        "NPV is not read for a sign."),
    "tornado_method_redispatch_fixed_sizes": (
        "The tornado holds the recommended sizes fixed and re-dispatches them at each "
        "driver's low and high value, so a bar can turn negative."),
    "snapshot_weightings_not_unit": (
        "The model's hours are weighted; the annual figures depend on those weights."),
    # the pack and the tariff
    "synthetic_load_profile": (
        "The load is a synthetic sector profile, not the site's metered load."),
    "synthetic_pv_profile": "The PV output is a synthetic clear-sky profile, not measured output.",
    "tariff_not_chosen_library_default": (
        "No tariff was chosen, so the library's default tariff was used; replace it with "
        "the site's own."),
    "tariff_has_uncoded_notes": (
        "The supplied tariff carries notes that are not codes; read them in the tariff."),
    "partial_billing_period_charged_in_full": (
        "A billing period the modelled hours cover only in part is charged in full."),
    "capacity_charge_prorated_by_hours": (
        "The capacity charge is pro-rated by the modelled hours over a full year."),
    "tariff_illustrative": "The tariff is illustrative, not a published tariff.",
    # the findings
    "battery_value_is_the_option_value": (
        "For a battery-only option the battery's value is the option's value."),
    "battery_removed_same_pv_reference": (
        "The battery's value is the option less a reference with the same PV and no battery."),
    "pv_rows_cancel_exactly": (
        "The PV's costs are the same in the option and in its reference, so they cancel."),
    "reference_is_the_baseline": (
        "The option's PV was sized to zero, so its reference is the grid-only baseline."),
    "battery_value_against_pv_only_reference": (
        "The battery's value is measured against the same PV built alone: it assumes the "
        "site builds the PV."),
    "bess_pv_value_not_attributable_to_battery": (
        "The battery-and-PV option's value could not be split between the battery and the "
        "PV, because its PV-only reference was not computed."),
    "value_streams_need_the_pv_only_reference": (
        "The value streams of a battery-and-PV option need its PV-only reference, which "
        "was not computed."),
    "value_streams_battery_increment_over_pv_only_reference": (
        "The value streams are the battery's increment over the same PV alone, not the "
        "option's full saving."),
    "options_not_established": "Some options were not solved; they are listed in the options table.",
    "options_not_all_judged": (
        "Not every battery option was judged, so the recommendation covers only the "
        "options the study could judge; a different size may be better."),
    "size_at_upper_bound": (
        "A size stopped at its upper limit: the optimiser would have built more, so the "
        "limit, not the economics, set the size."),
    "fork_changed_since_run": (
        "An option's network changed after the run and was left out; re-run the study."),
    "tornado_stale": "The tornado was run on an earlier run or ledger and is not used.",
    "tornado_not_run": "The tornado has not been run, so the verdict is not established.",
    "tornado_aborted": "The tornado was stopped before it reached every driver.",
    "tornado_row_failed": "A tornado bound could not be evaluated.",
    "tornado_not_established": "The tornado is not complete, so the verdict is not established.",
    "tornado_on_another_option": (
        "The tornado ran on a different option than the best one; run it again."),
    "no_battery_candidate": "No option sized a battery above zero with an established value.",
    "no_battery_option_judged": "No battery option could be judged.",
    "best_battery_npv_not_positive_at_centre": (
        "The best battery's NPV is not positive at the centre, so the tornado was not run."),
    "case_not_established": "The option's investment case could not be established.",
    "battery_not_solved": "The option's battery was not solved.",
    "uncoded_note_dropped": "A note that was not a clean code was dropped and is named here.",
    # tornado skip codes
    "row_missing": "The driver has no row in the assumptions ledger.",
    "row_not_modelled_in_tornado": "The tornado does not model this driver.",
    "row_has_no_value": "The driver's row has no value.",
    "row_has_no_range": "The driver's row has no range to evaluate.",
    "not_applicable": "The driver does not apply to this tariff or site.",
    "energy_price_level_no_effect_single_band": (
        "The tariff has one flat energy band, so scaling its spread changes nothing."),
    "tariff_has_no_demand_charge": "The tariff has no demand charge to vary.",
    "range_recentred_on_user_value": (
        "The user's value lies outside the library range, so the range's relative width "
        "was centred on it."),
    # the report's own disclosures
    "market_revenue_at_duals_zero_profit_at_optimum": (
        "The revenue at the model's prices equals the battery's annualised cost at the "
        "optimum: zero profit by construction. It is not a second revenue line and is "
        "excluded from the cash flow."),
    # Gate BC-S7-1: zero profit holds only at an interior optimum.
    "market_revenue_at_duals_exceeds_cost_at_size_limit": (
        "A size limit binds, so the revenue at the model's prices exceeds the annualised "
        "cost by the limit's shadow value: this is not zero profit. It is still excluded "
        "from the cash flow, because the bill already prices the same energy."),
    "market_revenue_at_duals_relation_not_established": (
        "How the revenue at the model's prices compares with the annualised cost is not "
        "established, because the run did not record how each size was limited. It is "
        "excluded from the cash flow."),
    "sector_profiles_have_broad_peaks": (
        "The shipped sector profiles have broad peak plateaus, which a battery cannot "
        "shave cheaply."),
    "intake_changed_since_findings": (
        "The study's answers (site, load, tariff or PV) changed after the findings were "
        "computed; re-run the study."),
    "intake_changed_since_run": (
        "The study's answers changed after the run; re-run the study."),
    "lcos_two_definitions": (
        "Two levelised costs of storage are shown and they answer different questions: "
        "the pro forma's excludes the energy bought to charge the battery, asset "
        "economics' includes it."),
    "battery_only_options_sized_to_zero": (
        "Every battery-only option was sized to zero: at this load and on this tariff no "
        "battery pays for itself on its own."),
    "synthetic_load_understates_peak_shaving": (
        "A smooth synthetic load lacks the short spikes a real meter records, so the "
        "peak-shaving value is likely understated; upload the site's metered load."),
    "value_streams_increment_over_pv_only": (
        "The waterfall shows the battery's increment over the same PV alone, not the "
        "option's full saving against the grid-only baseline."),
    "break_even_at_a_tornado_bound": (
        "At least one tornado bound leaves the battery NPV at break-even, within the "
        "tolerance below zero, so the claim is that it does not turn negative, not that "
        "it stays positive."),
    "robustness_pending": "The tornado did not reach every driver; those not reached are listed.",
    "typical_week_not_in_mvp1": "The typical-week operating chart is not part of this version.",
    "question_has_no_reliability_part": (
        "This question does not ask about reliability or resilience."),
    "no_system_recommended": "The verdict recommends no battery, so there is no system to describe.",
    "breakevens_not_in_mvp1": "Break-even thresholds and the option map are not part of this version.",
    "maturity_not_established": (
        "The study's maturity is not established yet, so its accuracy band is not known."),
    "no_option_named": "The verdict names no option, so there is no case to show.",
    "value_streams_not_established": "The value streams are not established.",
    "verdict_not_established": "The verdict is not established; the reasons are listed.",
    "currency_year_unknown": "No case states a currency year, so the money figure is not shown.",
    "fidelity_unknown": "The run that produced the figure is not recorded, so it is not shown.",
    "not_computed": "The figure was not computed.",
    # stale reasons
    "ledger_changed_since_findings": (
        "The assumptions ledger changed after the findings were computed; re-run the study."),
    "fork_changed_since_findings": (
        "An option's network changed, or is gone, since the findings were computed; "
        "re-run the study."),
    "report_has_no_findings_hashes": "The report does not record what it was computed from.",
    "ledger_changed_during_run": "The assumptions ledger changed while the study was running.",
    "intake_changed_during_run": "The study's answers changed while the study was running.",
    "copied_record_findings_computed_on_the_origin_forks": (
        "This study was copied from another project; its findings and report were computed "
        "on the origin's option networks."),
}

# (prefix, sentence) for codes that carry a qualifier.
_PREFIX_HELP: tuple[tuple[str, str], ...] = (
    ("technology_costs_are_", "The technology costs are projections for a future year."),
    ("tariff_currency_year_", "The tariff's currency year differs from the study's."),
    ("study_currency_year_", "The study's currency year differs from the ledger's."),
    ("needs_attention", "A user row no longer applies after a change and must be reset."),
    ("user_row_not_in_library", "A user row has no library counterpart."),
    ("key_drivers_without_a_row", "A key driver of the question has no ledger row."),
    ("bill_unavailable_", "A bill could not be computed, so the case is not established."),
    ("reference_", "The option's PV-only reference was not computed."),
    ("fork_changed_since_findings", HELP["fork_changed_since_findings"]),
    ("size_at_upper_bound", HELP["size_at_upper_bound"]),
    ("options_not_established", HELP["options_not_established"]),
)
_FALLBACK = "No explanation is recorded for this code."


def help_for(code: str, tariff: Tariff | None = None) -> tuple[str, str]:
    """``(sentence, source)`` for a code: the tariff's, the study's, a prefix, or the fallback."""
    if tariff is not None and code in tariff.honesty_help:
        return tariff.honesty_help[code], "tariff"
    if code in HELP:
        return HELP[code], "study"
    for prefix, text in _PREFIX_HELP:
        if code.startswith(prefix):
            return text, "study"
    return _FALLBACK, "fallback"


# ── the BESS disclosure set (review v1 N7; its own, not the adequacy one) ─
#
# (tag, code): the code is required when the tag is present. A tag is an
# established section id or a condition the assembler found; the rules go
# through `report_sections.disclosures`, the generic helper the adequacy
# write-up uses.

DISCLOSURE_ALWAYS = ("basis_real_pre_tax_no_subsidy", "single_year_extrapolated",
                     "perfect_foresight_dispatch")
DISCLOSURE_RULES: tuple[tuple[str, str], ...] = (
    ("economics", "npv_nonnegative_at_optimum_by_construction"),
    ("economics", "irr_and_discounted_payback_bounded_at_optimum_by_construction"),
    ("duals_interior", "market_revenue_at_duals_zero_profit_at_optimum"),
    ("duals_at_limit", "market_revenue_at_duals_exceeds_cost_at_size_limit"),
    ("duals_unknown", "market_revenue_at_duals_relation_not_established"),
    ("lcos_both", "lcos_two_definitions"),
    ("dc_foresight", "demand_charge_perfect_foresight"),
    ("unjudged", "options_not_all_judged"),
    ("bess_pv_named", "battery_value_against_pv_only_reference"),
    ("streams_increment", "value_streams_increment_over_pv_only"),
    ("break_even", "break_even_at_a_tornado_bound"),
    ("size_at_upper_bound", "size_at_upper_bound"),
    ("battery_only_zero", "battery_only_options_sized_to_zero"),
    ("battery_only_zero_synthetic", "sector_profiles_have_broad_peaks"),
    ("synthetic_load", "synthetic_load_understates_peak_shaving"),
    ("synthetic_pv", "synthetic_pv_profile"),
    ("tariff_default", "tariff_not_chosen_library_default"),
    ("robustness_pending", "robustness_pending"),
)

# ── prose templates (numbers only as {{fact_id}}) ─────────────────────────

_PV = ", measured against the same PV of {{pv_p_nom_mw}} alone"
_VERDICT_PROSE = {
    # Gate S6 [N1]: "positive at every bound" overstated a bound within the
    # tolerance below zero; the claim is "not negative at any bound".
    "recommended": (
        "Recommended: a battery of {{battery_p_nom_mw}} with {{battery_max_hours}} "
        "of storage has a battery NPV of {{battery_npv}} at the centre{pv}, and no tornado "
        "bound turns it negative."),
    "recommended_among_judged": (
        "Recommended among the battery options the study could judge: a battery of "
        "{{battery_p_nom_mw}} with {{battery_max_hours}} of storage has a battery "
        "NPV of {{battery_npv}} at the centre{pv}, and no tornado bound turns it negative. "
        "Other battery options were not judged, so a different size may be better."),
    "marginal": (
        "Marginal: a battery of {{battery_p_nom_mw}} with {{battery_max_hours}} of "
        "storage has a battery NPV of {{battery_npv}} at the centre{pv}, but it turns negative "
        "within the tornado's bounds on the drivers listed."),
    "not_recommended_best": (
        "Not recommended: the best battery option has a battery NPV of {{battery_npv}} at the "
        "centre, which is not positive."),
    "not_recommended_none": (
        "Not recommended: the model sized no battery above zero, or none has a positive "
        "battery NPV, at the centre."),
}
_BREAK_EVEN = (
    "At least one tornado bound leaves the battery NPV at break-even, between zero and minus "
    "{{npv_tolerance_eur}}: not negative, but not positive either.")
_NOT_ESTABLISHED = (
    "The study has not established a verdict. The reasons are listed with the disclosures "
    "above; nothing on this page is a recommendation.")
_BESS_PV = (
    "This option also builds PV. Its total NPV, PV included, is {{option_total_npv}}; the "
    "battery NPV above assumes the site builds that PV.")
_BATTERY_ONLY_BEST = (
    "Without PV, the best battery-only option is a battery of "
    "{{battery_only_best_p_nom_mw}} with {{battery_only_best_max_hours}} of storage, "
    "with a battery NPV of {{battery_only_best_npv}}: the figure to read if the site will not "
    "build PV.")
_BATTERY_ONLY_NONE = (
    "Without PV, no battery-only option sized a battery above zero with an established value, "
    "so the study does not show that a battery pays on its own.")


# ── facts ─────────────────────────────────────────────────────────────────

def _is_money(unit: str) -> bool:
    return unit.split("/")[0].strip().upper() in _CURRENCIES


def _fact(key: str, label: str, value: float | None, unit: str, engine: str, *,
          currency_year: int | None = None, fidelity=None, flag: str | None = None) -> Figure:
    """A Figure with its provenance; null with a flag when it cannot be stated."""
    if value is not None and not (isinstance(value, (int, float)) and math.isfinite(value)):
        value, flag = None, "not_finite"
    money = _is_money(unit)
    if value is not None and money and currency_year is None:
        value, flag = None, "currency_year_unknown"
    if value is not None and engine in _RUN_ENGINES and fidelity is None:
        value, flag = None, "fidelity_unknown"
    return Figure(key=key, label=label, value=None if value is None else float(value),
                  unit=unit, engine=engine, basis=MVP1_BASIS if money else None,
                  currency_year=currency_year if money else None, fidelity=fidelity,
                  unavailable=None if value is not None else (flag or "not_computed"))


def format_fact(fig: Figure | None) -> str:
    """How a fact reads in the rendered report; a null fact is "not established"."""
    if fig is None or fig.value is None:
        return "not established"
    v, unit = float(fig.value), fig.unit
    if _is_money(unit):
        return f"{v:,.0f} {unit}"
    if unit == "per unit":
        return f"{v * 100:.2f} %"
    if unit == "years":
        return f"{v:.1f} years"
    if unit == "MW":
        return f"{v:,.3f} MW"
    if unit in ("h", "%"):
        return f"{v:g} {unit}" if unit == "h" else f"{v:g} %"
    return f"{v:,.4g} {unit}"


# ── validate_prose (review v1 N4) ─────────────────────────────────────────

_REF = re.compile(r"\{\{([a-z_]+)\}\}")
_DIGIT = re.compile(r"(?<![A-Za-z])\d")
# Gate S7 [N1]: a digit glued to a currency or unit prefix ("EUR4M", "MW3")
# is a number too; the letter lookbehind above lets it through. Spelled-out
# numbers ("four point one million") are a documented limitation: the
# templates are the product's own code, and a test pins them.
_GLUED = re.compile(
    r"(?<![A-Za-z])(?:EUR|USD|GBP|CHF|TWh|GWh|MWh|kWh|Wh|GW|MW|kW|bn|k|M|m|h)\d")
# A unit token stands alone: not inside a word or a number (`CO22`, `N-12`,
# `24/7.5`), a sentence's full stop after it allowed.
_UNITS = re.compile(r"(?<![\w./-])(?:CO2|H2|N-1|24/7)(?![\w/]|\.\d)")


class ProseError(ValueError):
    """A paragraph the renderer refuses; names its section and paragraph."""

    def __init__(self, section: str, paragraph: int, reason: str):
        super().__init__(f"section {section!r}, paragraph {paragraph}: {reason}")
        self.section = section
        self.paragraph = paragraph
        self.reason = reason

    @property
    def detail(self) -> dict:
        return {"error_kind": "report_prose_invalid", "message": str(self),
                "section": self.section, "paragraph": self.paragraph}


def _check(text: str, facts: Mapping[str, Figure], section: str, i: int) -> str:
    for ref in _REF.findall(text):
        if ref not in facts:
            raise ProseError(section, i, f"unresolved reference {{{{{ref}}}}}")
    bare = _UNITS.sub("", _REF.sub("", text))
    if "{{" in bare or "}}" in bare:
        raise ProseError(section, i, "a malformed fact reference")
    m = _DIGIT.search(bare) or _GLUED.search(bare)
    if m:
        snippet = bare[max(0, m.start() - 12): m.start() + 12]
        raise ProseError(section, i, f"a number outside a fact reference: {snippet!r}")
    # A null fact reads "not established" (gate S6 carry), never a rejection.
    return _REF.sub(lambda mm: format_fact(facts[mm.group(1)]), text)


def validate_prose(report: DecisionReport) -> dict[str, list[str]]:
    """
    Every section's paragraphs, checked and rendered: ``{section: [text]}``.
    Raises :class:`ProseError` on a numeric token outside a ``{{fact_id}}``
    (the allowlisted units aside) or an unresolved reference.
    """
    out: dict[str, list[str]] = {}
    for sid, section in report.sections.items():
        out[sid] = [_check(p.text, report.facts, sid, i) for i, p in enumerate(section.prose)]
    for i, p in enumerate(report.ai_paragraphs):
        _check(p.text, report.facts, "ai_paragraphs", i)
    return out


# ── stale (review v1 S6; gate S1, S4 and S5 carries) ──────────────────────

def stale_reasons(report: DecisionReport, study: DecisionStudy, ledger: AssumptionsLedger,
                  fork_hash_now: Callable[[str], str | None]) -> list[str]:
    """
    Why the report no longer describes the study (empty when it does).
    ``fork_hash_now(fork_uuid)`` answers the fork's ``network.nc`` hash on
    disk now, or None when the fork is gone or no longer this study's.
    """
    from services.study import run_hashes

    reasons = list(study.stale_reasons) if study.stale else []
    hashes = report.hashes_at_findings
    if hashes is None:
        reasons.append("report_has_no_findings_hashes")
        return list(dict.fromkeys(reasons))
    if not run_hashes.ledger_matches(ledger, hashes):
        reasons.append("ledger_changed_since_findings")
    if not run_hashes.intake_matches(study.intake, hashes):
        reasons.append("intake_changed_since_findings")
    forks = ((report.sections.get("appendix") or ReportSection(status="ok")).payload or {}).get(
        "option_forks") or {}
    for fork_uuid, recorded in sorted(hashes.option_network_hashes.items()):
        if not run_hashes.fork_matches(recorded, fork_hash_now(fork_uuid)):
            reasons.append(f"fork_changed_since_findings:{forks.get(fork_uuid, fork_uuid)}")
    return list(dict.fromkeys(reasons))


def money_unit(report: DecisionReport, *, per_year: bool = False) -> str:
    """
    The unit a money column or axis states beside its numbers (gate BC-S7-3):
    the report's currency and currency year, e.g. ``EUR 2020`` or
    ``EUR/yr 2020``; the year is named as unknown rather than left out.
    """
    currency = next((f.unit.split("/")[0].strip() for f in report.facts.values()
                     if _is_money(f.unit)), "EUR")
    unit = f"{currency}/yr" if per_year else currency
    year = report.currency_year
    return f"{unit} {year}" if year is not None else f"{unit}, currency year unknown"


# XML 1.0 forbids these; python-docx and openpyxl refuse them (gate S7 [N9]).
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def xml_safe(text: str) -> str:
    """User text with the control characters a document cannot hold replaced."""
    return _XML_ILLEGAL.sub("\ufffd", text)


def xml_safe_report(report: DecisionReport) -> DecisionReport:
    """A copy of the report whose every string a DOCX or XLSX writer accepts."""
    def clean(x):
        if isinstance(x, str):
            return xml_safe(x)
        if isinstance(x, list):
            return [clean(v) for v in x]
        if isinstance(x, dict):
            return {clean(k) if isinstance(k, str) else k: clean(v) for k, v in x.items()}
        return x

    return DecisionReport.model_validate(clean(report.model_dump(mode="json", by_alias=True)))


def report_available(report: DecisionReport) -> bool:
    """ADR-0003 payload-level ``available``: the answer (the verdict) is established."""
    s = report.sections.get("executive_summary")
    return s is not None and s.status == "ok"


# ── the builder ───────────────────────────────────────────────────────────

@dataclass
class ReportInputs:
    study: DecisionStudy
    question: DecisionQuestion
    findings: Findings
    cases: dict[str, InvestmentCase]
    ledger: AssumptionsLedger
    tariff: Tariff
    details: Mapping[str, Any] = field(default_factory=dict)  # run details per option
    option_forks: dict[str, str] = field(default_factory=dict)  # fork uuid -> option id
    generated_at: datetime | None = None
    run_intake: dict | None = None     # the intake the run's forks were built from


def _para(*texts: str) -> list[ProseParagraph]:
    return [ProseParagraph(text=t) for t in texts if t]


def _size(findings: Findings, option_id: str, asset: str) -> float | None:
    for o in findings.options:
        if o.option_id == option_id:
            for s in o.sizes:
                if s.asset == asset:
                    return s.p_nom_opt
    return None


def _max_hours(question: DecisionQuestion, option_id: str) -> float | None:
    try:
        return Q.max_hours(Q.option(question, option_id))
    except KeyError:
        return None


def _has_pv(question: DecisionQuestion, option_id: str | None) -> bool:
    if option_id is None:
        return False
    try:
        return Q.PV in Q.option(question, option_id).free_assets
    except KeyError:
        return False


def _lcos_including_charging(details: Mapping[str, Any], option_id: str) -> float | None:
    from services.study import packs, proforma

    econ = (details.get(option_id) or {}).get("asset_economics")
    row = proforma._econ_row(econ, "storage_units", packs.BATTERY_NAME)
    v = None if row is None else row.get("lcos_eur_per_mwh")
    return None if v is None else float(v)


_UPLOADED = ("upload", "uploaded", "measured")


def _load_is_synthetic(intake: Mapping[str, Any] | None, codes: set[str]) -> bool:
    """The pack's own marker, else the intake: anything but an upload is synthetic."""
    if "synthetic_load_profile" in codes:
        return True
    load = (intake or {}).get("load") if isinstance(intake, Mapping) else None
    return isinstance(load, Mapping) and str(load.get("source")) not in _UPLOADED


def _duals_relation(details: Mapping[str, Any], option_id: str, verdict_at_limit: bool) -> str:
    """
    ``interior`` | ``at_limit`` | ``unknown`` for the option's sized assets,
    from the run's own classification (``details[oid]["sizing"]``, written
    by ``services/results/sizing.py::classify_sizing``) or the verdict's
    ``size_at_upper_bound`` disclosure — no second classifier (gate BC-S7-1).
    Zero profit at the duals holds only when every built asset is interior.
    """
    sizing = (details.get(option_id) or {}).get("sizing") or {}
    bindings = [c.get("binding_constraint") for c in sizing.values() if isinstance(c, Mapping)]
    caveats = (details.get(option_id) or {}).get("caveats") or []
    if verdict_at_limit or "at_upper_bound" in bindings or any(
            str(c).startswith("size_at_upper_bound") for c in caveats):
        return "at_limit"
    built = [b for b in bindings if b != "not_built"]
    if built and all(b == "interior" for b in built):
        return "interior"
    return "unknown"


_DUALS_PROSE = {
    "interior": (
        "The market revenue at the model's prices, {{market_revenue_at_duals}}, is not a "
        "second revenue line: at this interior optimum it equals the annualised cost of the "
        "assets that earn it, zero profit by construction, and it is excluded from the cash "
        "flow because the bill already prices the same energy."),
    "at_limit": (
        "The market revenue at the model's prices, {{market_revenue_at_duals}}, is not a "
        "second revenue line. A size limit binds, so it exceeds the annualised cost of the "
        "assets that earn it by the limit's shadow value; it is excluded from the cash flow "
        "because the bill already prices the same energy."),
    "unknown": (
        "The market revenue at the model's prices, {{market_revenue_at_duals}}, is not a "
        "second revenue line; how it compares with the annualised cost is not established. "
        "It is excluded from the cash flow because the bill already prices the same energy."),
}


def _pack_codes(case: InvestmentCase | None) -> set[str]:
    return set(case.honesty_notes) if case is not None else set()


def build_decision_report(inp: ReportInputs) -> DecisionReport:
    """The report from the findings and the cases (see the module docstring)."""
    from services.study import findings as F
    from services.study import packs

    f, v, q = inp.findings, inp.findings.verdict, inp.question
    facts: dict[str, Figure] = {}
    tags: set[str] = set()
    sections: dict[str, ReportSection] = {}
    fidelity = v.headline_kpis[0].fidelity if v.headline_kpis else None
    fidelity = fidelity or next((o.fidelity for o in f.options if o.fidelity), None)
    atts = {a.option_id: a for a in f.battery_attribution}
    named = v.option_id
    named_case = inp.cases.get(named) if named else None
    currency_year = (named_case.currency_year if named_case is not None else None) or next(
        (c.currency_year for c in inp.cases.values() if c.currency_year), None) or next(
        (fig.currency_year for fig in v.facts.values() if fig.currency_year), None)

    # ── 1. executive summary ──────────────────────────────────────────────
    facts.update(v.facts)   # unchanged: the verdict KPIs ARE the findings'
    exec_prose: list[str] = []
    with_pv = "pv_p_nom_mw" in v.facts
    if v.status == "ok" and v.sentence_template in _VERDICT_PROSE:
        exec_prose.append(_VERDICT_PROSE[v.sentence_template].replace("{pv}", _PV if with_pv else ""))
    else:
        exec_prose.append(_NOT_ESTABLISHED)
    if v.class_ is not None and v.class_.value == "recommended" and f.robustness.status == "ok":
        near = [x for r in f.robustness.tornado for x in (r.npv_low, r.npv_high)
                if x is not None and -F.NPV_TOL_EUR <= x <= 0.0]
        if near:
            tags.add("break_even")
            facts["npv_tolerance_eur"] = _fact("npv_tolerance_eur", "Break-even tolerance",
                                               F.NPV_TOL_EUR, "EUR", "method_constant",
                                               currency_year=currency_year)
            exec_prose.append(_BREAK_EVEN)
    named_att = atts.get(named) if named else None
    if named_att is not None and named_att.method == "battery_removed_same_pv":
        tags.add("bess_pv_named")
        facts["option_total_npv"] = _fact(
            "option_total_npv", "Option NPV, PV included", named_att.option_npv, "EUR",
            "cash_flow_expander", currency_year=named_att.currency_year,
            fidelity=named_att.fidelity, flag=named_att.unavailable.get("option_npv"))
        exec_prose.append(_BESS_PV)
    if named_att is not None and named_att.method == "battery_removed_same_pv" or (
            v.status == "ok" and _has_pv(q, named)):
        only = [a for a in f.battery_attribution
                if a.method == "battery_only" and a.status == "ok" and a.battery_npv is not None
                and not F.is_zero_size(a.battery_p_nom_mw)]
        best = max(only, key=lambda a: a.battery_npv, default=None)
        if best is None:
            exec_prose.append(_BATTERY_ONLY_NONE)
        else:
            facts["battery_only_best_npv"] = _fact(
                "battery_only_best_npv", "Best battery-only NPV", best.battery_npv, "EUR",
                "cash_flow_expander", currency_year=best.currency_year, fidelity=best.fidelity)
            facts["battery_only_best_p_nom_mw"] = _fact(
                "battery_only_best_p_nom_mw", "Best battery-only power", best.battery_p_nom_mw,
                "MW", "lp", fidelity=best.fidelity)
            facts["battery_only_best_max_hours"] = _fact(
                "battery_only_best_max_hours", "Best battery-only hours of storage",
                _max_hours(q, best.option_id), "h", "ledger")
            exec_prose.append(_BATTERY_ONLY_BEST)
    if "options_not_all_judged" in v.disclosures or "options_not_all_judged" in v.reasons:
        tags.add("unjudged")
    if "size_at_upper_bound" in v.disclosures:
        tags.add("size_at_upper_bound")
    # Gate S7 [N4]: whenever demand-charge reduction is a non-zero stream,
    # not only when it is the main caveat.
    dc_streams = [s.annual_value for s in f.value_streams
                  if s.key == "demand_charge_reduction"] + [
        s.annual_value for c in inp.cases.values() for s in c.value_streams
        if s.key in ("demand", "capacity")]
    if v.main_caveat == "demand_charge_perfect_foresight" or any(
            x is not None and abs(x) > 0.0 for x in dc_streams):
        tags.add("dc_foresight")
    kpis = [k.key for k in v.headline_kpis]
    sections["executive_summary"] = ReportSection(
        status="ok" if v.status == "ok" else "not_established",
        note=None if v.status == "ok" else "verdict_not_established",
        facts={k: facts[k] for k in facts if k in v.facts or k in (
            "option_total_npv", "battery_only_best_npv", "battery_only_best_p_nom_mw",
            "battery_only_best_max_hours", "npv_tolerance_eur")},
        figures=["value_stream_waterfall"] if f.value_streams else [],
        prose=_para(*exec_prose),
        payload={"verdict_class": v.class_.value if v.class_ is not None else None,
                 "verdict_status": v.status, "option_id": named, "headline_kpis": kpis,
                 "main_caveat": v.main_caveat, "reasons": list(v.reasons),
                 "drivers": list(v.drivers),
                 "maturity_class": inp.study.maturity.class_,
                 "maturity_status": inp.study.maturity.status})

    # ── 2. the question, the baseline, the options ───────────────────────
    options = []
    for o in f.options:
        a = atts.get(o.option_id)
        options.append({
            "option_id": o.option_id, "label": o.label, "solve_status": o.solve_status,
            "battery_mw": _size(f, o.option_id, packs.BATTERY_NAME),
            "pv_mw": _size(f, o.option_id, packs.PV_NAME),
            "battery_npv": a.battery_npv if a else None,
            "option_npv": a.option_npv if a else (inp.cases[o.option_id].kpis.npv
                                                  if o.option_id in inp.cases
                                                  and inp.cases[o.option_id].kpis else None),
            "attribution": a.status if a else None,
            "notes": list(a.notes) if a else [],
        })
    sections["question"] = ReportSection(
        status=f.options_status,
        note=None if f.options_status == "ok" else "options_not_established",
        prose=_para(
            "The question: does this site need a battery, and what is it worth? Every option "
            "is measured against the grid-only baseline: the same load supplied through the "
            "grid connection on the same tariff, with the existing assets and nothing added.",
            "The options considered are listed below with how each was solved, the sizes the "
            "optimiser chose and the value of each battery."),
        payload={"question_title": q.title, "baseline": q.baseline_definition.text,
                 "options": options, "pending_options": list(f.pending_options),
                 "baseline_solve_status": f.baseline.solve_status})

    # ── 3. the recommended system ────────────────────────────────────────
    klass = v.class_.value if v.class_ is not None else None
    explain_rows = [
        {"option_id": e.get("option_id"), "name": e.get("name"),
         "component_class": e.get("component_class"),
         "binding_constraint": (e.get("sizing") or {}).get("binding_constraint"),
         # Only the client-facing note (gate S0): the copilot's narration
         # instructions in `reading_notes` are not report text.
         "reading_notes": [n for n in e.get("reading_notes") or []
                           if n == ZERO_PROFIT_BY_CONSTRUCTION]}
        for e in f.explain if e.get("option_id") == named]
    if v.status != "ok":
        rs = ReportSection(status="not_established", note="verdict_not_established")
    elif klass not in ("recommended", "marginal"):
        rs = ReportSection(status="skipped", note="no_system_recommended")
    else:
        rs = ReportSection(
            status="ok", note="typical_week_not_in_mvp1",
            facts={k: facts[k] for k in ("battery_p_nom_mw", "battery_max_hours", "pv_p_nom_mw")
                   if k in facts},
            prose=_para(
                "The system the verdict names is a battery of {{battery_p_nom_mw}} with "
                "{{battery_max_hours}} of storage" + (
                    ", beside PV of {{pv_p_nom_mw}}" if with_pv else "") + ". The table "
                "below says, per asset, what limited its size."),
            payload={"explain": explain_rows})
    sections["recommended_system"] = rs

    # ── 4. economics (the named option's case) ───────────────────────────
    econ_oid = named if named in inp.cases else (
        f.value_streams_option if f.value_streams_option in inp.cases else None)
    case = inp.cases.get(econ_oid) if econ_oid else None
    if case is None or case.status != "ok" or case.kpis is None:
        sections["economics"] = ReportSection(
            status="skipped" if econ_oid is None and v.status == "ok" else "not_established",
            note="no_option_named" if econ_oid is None else "case_not_established")
    else:
        k, cy, fid = case.kpis, case.currency_year, case.fidelity

        def cf(key, label, value, unit, engine="cash_flow_expander", flag=None):
            facts[key] = _fact(key, label, value, unit, engine, currency_year=cy,
                               fidelity=fid, flag=flag or k.unavailable.get(key.replace(
                                   "case_", "")))
            return key

        keys = [
            cf("case_npv", "Option NPV", k.npv, "EUR"),
            cf("case_irr", "IRR", k.irr, "per unit"),
            cf("case_payback_simple", "Simple payback", k.payback_simple, "years"),
            cf("case_payback_discounted", "Discounted payback", k.payback_discounted, "years"),
            cf("case_capex_total", "Total CAPEX", k.capex_total, "EUR"),
            cf("lcos_excl_charging", "LCOS excluding charging energy (pro forma)", k.lcos,
               "EUR/MWh", flag=k.unavailable.get("lcos")),
            cf("lcos_incl_charging", "LCOS including charging energy (asset economics)",
               _lcos_including_charging(inp.details, econ_oid), "EUR/MWh", "lp",
               flag="asset_economics_row_missing"),
            cf("market_revenue_at_duals", "Market revenue at the model's prices (annual)",
               case.market_revenue_at_duals.annual_value
               if case.market_revenue_at_duals is not None else None, "EUR/yr", "lp_duals",
               flag="not_computed"),
            cf("horizon_years", "Horizon", float(case.horizon_years), "years", "ledger"),
            cf("discount_rate", "Real discount rate", case.discount_rate, "per unit", "ledger"),
        ]
        if facts["lcos_excl_charging"].value is not None and \
                facts["lcos_incl_charging"].value is not None:
            tags.add("lcos_both")
        tags.add("economics")
        duals = _duals_relation(inp.details, econ_oid,
                                "size_at_upper_bound" in v.disclosures and named == econ_oid)
        tags.add(f"duals_{duals}")
        prose = [
            "On a real, pre-tax basis without subsidy, in the currency year stated beside each "
            "figure, the option's NPV is {{case_npv}}, its IRR {{case_irr}}, its simple payback "
            "{{case_payback_simple}} and its discounted payback {{case_payback_discounted}}, "
            "over a horizon of {{horizon_years}} at a real discount rate of {{discount_rate}}. "
            "The total CAPEX is {{case_capex_total}}.",
            "By construction, at the optimiser's chosen size the NPV is not negative, the IRR "
            "is at least the discount rate and the discounted payback is within the horizon: "
            "those signs restate the optimiser's choice and are not independent evidence. The "
            "magnitudes inform; the tornado's bars, at fixed sizes, can be negative.",
            "Two levelised costs of storage answer different questions: "
            "{{lcos_excl_charging}} leaves out the energy bought to charge the battery (the "
            "pro forma's figure) and {{lcos_incl_charging}} includes it (asset economics).",
            _DUALS_PROSE[duals],
        ]
        if _has_pv(q, econ_oid):
            prose.append("These figures are the whole option's, PV included; the battery's "
                         "share is in the executive summary.")
        sections["economics"] = ReportSection(
            status="ok", facts={x: facts[x] for x in keys}, figures=["cash_flow"],
            prose=_para(*prose),
            payload={"option_id": econ_oid, "currency_year": cy,
                     "cash_flow": [y.model_dump(mode="json") for y in case.years],
                     "upfront_gaps": [g.model_dump(mode="json") for g in case.upfront_gaps],
                     "honesty_notes": list(case.honesty_notes)})

    # ── 5. drivers: the value streams and the tornado ────────────────────
    stream_oid = f.value_streams_option
    increment = bool(f.value_streams) and _has_pv(q, stream_oid)
    if increment:
        tags.add("streams_increment")
    streams_label = ("Battery increment over the PV-only reference" if increment
                     else "Full saving against the grid-only baseline")
    stream_case = inp.cases.get(stream_oid) if stream_oid else None
    total = (sum(s.annual_value for s in f.value_streams if s.annual_value is not None)
             if f.value_streams else None)
    facts["value_streams_total"] = _fact(
        "value_streams_total", "Annual saving shown by the streams", total, "EUR/yr",
        "bill_calculator",
        currency_year=stream_case.currency_year if stream_case is not None else currency_year,
        fidelity=stream_case.fidelity if stream_case is not None else fidelity,
        flag="value_streams_not_established")
    rob = f.robustness
    facts["tornado_centre_npv"] = _fact(
        "tornado_centre_npv", "Battery NPV at the centre", rob.npv_centre, "EUR",
        "cash_flow_expander", currency_year=currency_year, fidelity=fidelity,
        flag=rob.note or "tornado_not_run")
    drivers_prose = [
        ("The waterfall shows the battery's increment over the same PV alone, not the "
         "option's full saving: {{value_streams_total}} in total." if increment else
         "The waterfall shows the option's full annual saving against the grid-only "
         "baseline, stream by stream: {{value_streams_total}} in total."),
        "The tornado holds the sizes fixed and re-dispatches them at each driver's low and "
        "high value, around a centre battery NPV of {{tornado_centre_npv}}.",
    ]
    sections["drivers"] = ReportSection(
        status="ok" if f.value_streams_status == "ok" and rob.status == "ok" else "not_established",
        note=None if f.value_streams_status == "ok" and rob.status == "ok" else (
            "value_streams_not_established" if f.value_streams_status != "ok"
            else (rob.note or "tornado_not_established")),
        facts={x: facts[x] for x in ("value_streams_total", "tornado_centre_npv")},
        figures=[x for x, ok in (("value_stream_waterfall", bool(f.value_streams)),
                                 ("tornado", bool(rob.tornado))) if ok],
        prose=_para(*drivers_prose),
        payload={"value_streams_basis": "pv_only_reference" if increment else "baseline",
                 "value_streams_label": streams_label, "value_streams_option": stream_oid,
                 "value_streams": [s.model_dump(mode="json") for s in f.value_streams],
                 "tornado": [r.model_dump(mode="json") for r in rob.tornado],
                 "tornado_centre": rob.npv_centre, "tornado_option": rob.option_id})

    # ── 6. robustness ────────────────────────────────────────────────────
    if rob.pending:
        tags.add("robustness_pending")
    sections["robustness"] = ReportSection(
        status=rob.status, note=rob.note,
        prose=_para(
            "Robustness is tested by the fixed-size tornado. Drivers it skipped are listed "
            "with the reason, and drivers it did not reach are named; break-even thresholds, "
            "the option map and a probability of a positive NPV are not part of this version."),
        payload={"method": rob.method, "pending": list(rob.pending),
                 "skipped": dict(rob.skipped), "solves_charged": rob.solves_charged,
                 "note": rob.note, "breakevens": "breakevens_not_in_mvp1"})

    # ── 7. reliability: not part of this question ────────────────────────
    sections["reliability"] = ReportSection(status="skipped",
                                            note="question_has_no_reliability_part")

    # ── 8. assumptions: the ledger, the edited rows marked ───────────────
    rows = []
    for r in inp.ledger.rows:
        rows.append({
            "key": r.key, "label": r.label, "value": r.value, "unit": r.unit,
            "currency_year": r.currency_year, "provenance": r.provenance, "status": r.status,
            "range_low": r.range.low if r.range else None,
            "range_high": r.range.high if r.range else None,
            "source": r.source, "source_year": r.source_year,
            "edited": r.status == "customised" or r.provenance in ("user", "measured"),
            "sensitivity_flag": bool(r.sensitivity_flag)})
    sections["assumptions"] = ReportSection(
        status="ok",
        prose=_para("Every assumption the figures rest on is listed below with its source. "
                    "Rows marked as edited were changed by a user; every other row is the "
                    "library's."),
        payload={"ledger_version": inp.ledger.ledger_version, "rows": rows,
                 "honesty_notes": list(inp.ledger.honesty_notes)})

    # ── 9. limitations and next steps ────────────────────────────────────
    band = inp.study.maturity.accuracy_band
    facts["accuracy_low_pct"] = _fact("accuracy_low_pct", "Accuracy band, low end",
                                      band.low_pct if band else None, "%", "ledger",
                                      flag="maturity_not_established")
    facts["accuracy_high_pct"] = _fact("accuracy_high_pct", "Accuracy band, high end",
                                       band.high_pct if band else None, "%", "ledger",
                                       flag="maturity_not_established")
    all_codes = set(f.honesty_notes) | set(v.disclosures) | set().union(
        *(_pack_codes(c) for c in inp.cases.values()))
    synthetic = _load_is_synthetic(inp.study.intake, all_codes)
    if synthetic:
        tags.add("synthetic_load")
    if "synthetic_pv_profile" in all_codes:
        tags.add("synthetic_pv")
    if "tariff_not_chosen_library_default" in all_codes:
        tags.add("tariff_default")
    battery_only = [a for a in f.battery_attribution if a.method == "battery_only"]
    if battery_only and all(a.status == "skipped" for a in battery_only):
        tags.add("battery_only_zero")
        if synthetic:
            tags.add("battery_only_zero_synthetic")
    sections["limitations"] = ReportSection(
        status=inp.study.maturity.status,
        note=None if inp.study.maturity.status == "ok" else "maturity_not_established",
        facts={x: facts[x] for x in ("accuracy_low_pct", "accuracy_high_pct")},
        prose=_para(
            "The maturity badge says how far these figures can be trusted; the indicative "
            "accuracy band runs from {{accuracy_low_pct}} to {{accuracy_high_pct}}.",
            "What would tighten it: the site's metered load instead of a sector profile, "
            "quoted equipment costs instead of library defaults, and more than one weather "
            "year."),
        payload={"maturity": inp.study.maturity.model_dump(mode="json", by_alias=True),
                 "synthetic_load": synthetic})

    # ── 10. appendix ─────────────────────────────────────────────────────
    sections["appendix"] = ReportSection(
        status="ok",
        prose=_para("The model, its inputs and what the figures were computed from."),
        payload={
            "model": ("One representative year, hourly, one site bus behind a grid "
                      "connection, a battery with enumerated duration and optional PV, "
                      "sized by a linear optimisation on the tariff."),
            "intake": inp.run_intake if inp.run_intake is not None else inp.study.intake,
            "tariff_id": inp.tariff.tariff_id, "tariff_name": inp.tariff.name,
            "library_version": inp.ledger.ledger_version,
            "ledger_hash": f.hashes.ledger_hash,
            "base_network_hash": f.hashes.base_network_hash,
            "option_network_hashes": dict(f.hashes.option_network_hashes),
            "option_forks": dict(inp.option_forks),
            "fidelity": fidelity.value if fidelity is not None else None,
            "engines": ["lp", "lp_duals", "bill_calculator", "cash_flow_expander", "ledger"],
            "solver_log": "not_in_mvp1",
        })

    # ── disclosures, gaps, what is not established ───────────────────────
    view = [{"id": sid, "status": s.status, "note": s.note} for sid, s in sections.items()]
    tags |= report_sections.present_ids(view)
    # Gate BC-S7-2: the verdict's own disclosures, verbatim, before the rules;
    # a battery NPV never appears without the by-construction pair.
    codes = list(dict.fromkeys([
        *v.disclosures,
        *report_sections.disclosures(tags, DISCLOSURE_RULES, always=DISCLOSURE_ALWAYS)]))
    tariff_codes = [c for c in inp.tariff.honesty_notes if c not in codes]
    disclosures = []
    for code in list(dict.fromkeys([*codes, *tariff_codes])):
        text, source = help_for(code, inp.tariff)
        disclosures.append(ReportDisclosure(code=code, text=text, source=source))
    gaps = []
    if f.pending_options:
        gaps.append("options not solved: " + ", ".join(f.pending_options))
    if rob.pending:
        gaps.append("tornado drivers not reached: " + ", ".join(rob.pending))
    if v.status != "ok":
        gaps.append("verdict not established: " + ", ".join(v.reasons))
    not_est = report_sections.not_established(
        view, lambda s: f"{TITLES[s['id']]}: not established ({s['note'] or 'no reason'})")

    notes = list(dict.fromkeys([*f.honesty_notes, *v.disclosures,
                                *(case.honesty_notes if case is not None else ()),
                                *inp.tariff.honesty_notes]))
    shown = set(notes) | {d.code for d in disclosures} | set(v.reasons) | set(
        rob.skipped.values()) | {s.note for s in sections.values() if s.note}
    shown |= {c for r in rob.tornado for c in r.notes}
    shown |= {c for a in f.battery_attribution for c in a.notes}
    help_map = {c: help_for(c, inp.tariff)[0] for c in sorted(shown) if c}
    return DecisionReport(
        study_id=inp.study.study_id, question_id=inp.study.question_id,
        generated_at=inp.generated_at or datetime.now(tz=UTC),
        study_name=inp.study.name, question_title=q.title,
        basis=MVP1_BASIS, currency_year=currency_year, fidelity=fidelity,
        maturity=inp.study.maturity, sections={sid: sections[sid] for sid, _t in SECTIONS},
        facts=facts, required_disclosures=disclosures, evidence_gaps=gaps,
        not_established=not_est, honesty_help=help_map,
        hashes_at_findings=f.hashes, stale=inp.study.stale,
        stale_reasons=list(inp.study.stale_reasons) if inp.study.stale else [],
        honesty_notes=tuple(notes))


# ── from disk ─────────────────────────────────────────────────────────────

def assemble_decision_report(study: DecisionStudy, base_dir, db, base_uuid: str
                             ) -> tuple[DecisionReport, ReportInputs]:
    """
    The report of the last run, from disk, no solve: the findings
    (``findings.assemble_findings``) and each option's centre case rebuilt
    from its fork's saved network (``findings.centre_attributions``), the
    same networks read once. Refusals are ``findings.FindingsRefused``: 404
    ``study_never_run``, 409 ``ledger_changed_since_run``, 409
    ``fork_changed_since_run`` (the baseline's network missing or changed).
    """
    from services.study import findings as F

    inp = F.load_inputs(study, base_dir, db, base_uuid)
    ctx = F.context_from_disk(inp)
    findings = F.assemble_findings(study, base_dir, db, base_uuid, loaded=(inp, ctx))
    _atts, cases = F.centre_attributions(ctx)
    forks = {str(row.id): oid for oid, row in inp.rows.items()}
    rinp = ReportInputs(study=study, question=inp.question, findings=findings, cases=cases,
                        ledger=inp.ledger, tariff=inp.tariff,
                        details=inp.run.get("details") or {}, option_forks=forks,
                        run_intake=inp.run.get("intake"))
    return build_decision_report(rinp), rinp
