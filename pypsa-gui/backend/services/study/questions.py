"""
Question templates (guided investment study MVP-1, phase S4).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S4)
Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md §4.2

MVP-1 ships one template, :data:`BESS_AT_SITE`: "Do I need a battery at my
grid-connected site, and what is it worth?". A template is data
(:class:`models.study.DecisionQuestion`); the network it builds is
``services/study/packs.py::build_site_network`` and the runner that solves its
options is ``services/study/runner.py``.

Three rules the template pins:

* **The key drivers are THE list.** ``BESS_AT_SITE.key_drivers`` is what the
  ledger's ``sensitivity_flag`` and the maturity badge read (gate S2: it
  replaces the ``library.BESS_KEY_DRIVERS`` stand-in, which is now an alias
  held equal by a test) and what the S6 tornado perturbs.
* **``none`` omits, never zeroes** (review v2 BC-1): the baseline option has
  no StorageUnit and no PV Generator at all, because preflight errors on a
  non-extendable asset with ``p_nom = 0`` and a size fixed at zero is not a
  "without" case anyway.
* **Durations are enumerated** (spec decision 5, §8.1 amended): one option
  per battery duration, ``max_hours`` in ``{1, 2, 4}``; the PV option exists
  only when the intake enables PV.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.study import (
    BaselineDefinition,
    DecisionQuestion,
    DiscreteChoice,
    OptionSpec,
)

__all__ = [
    "BESS_AT_SITE", "QUESTIONS", "get_question", "max_hours", "option",
    "options_for", "pv_enabled",
]

# Asset handles the pack understands (`packs.py`): the battery StorageUnit and
# the optional PV Generator. The grid supply, the import and export links and
# the load are the site itself, present in every option.
BATTERY = "battery"
PV = "pv"


def _bess(option_id: str, hours: int, label: str, one_line: str) -> OptionSpec:
    return OptionSpec(
        option_id=option_id, label=label, one_line=one_line,
        free_assets=[BATTERY],
        omitted_assets=[PV],
        discrete_choices=[DiscreteChoice(key="max_hours", values=[hours])],
    )


BESS_AT_SITE = DecisionQuestion(
    question_id="bess_at_site",
    title="Do I need a battery at my site, and what is it worth?",
    one_line=("Grid-connected site with a tariff and a load: size a battery "
              "at 1, 2 and 4 hours (and with PV when enabled) against the "
              "grid-only baseline."),
    archetype="grid_connected_site",
    # `site` carries the zone; `connection_limit` is `intake.site.connection_mw`
    # (packs.py reads both); `tariff` falls back to the library default with
    # an honesty note; `load` is an upload or a synthetic sector profile.
    mandatory_inputs=["site", "tariff", "load", "connection_limit"],
    network_pack="site_bess_v1",
    baseline_definition=BaselineDefinition(
        text=("Grid supply only: the site's load served through its grid "
              "connection under the chosen tariff, no battery and no PV."),
        fixed_assets=[],
    ),
    options=[
        OptionSpec(
            option_id="none", label="Without (grid only)",
            one_line="The baseline: no battery, no PV (omitted, not zeroed).",
            omitted_assets=[BATTERY, PV],
        ),
        _bess("bess_1h", 1, "Battery, 1 hour",
              "The LP sizes a 1-hour battery (MW; MWh = MW x 1 h)."),
        _bess("bess_2h", 2, "Battery, 2 hours",
              "The LP sizes a 2-hour battery (MW; MWh = MW x 2 h)."),
        _bess("bess_4h", 4, "Battery, 4 hours",
              "The LP sizes a 4-hour battery (MW; MWh = MW x 4 h)."),
        OptionSpec(
            option_id="bess_pv_2h", label="Battery (2 hours) with PV",
            one_line=("The LP sizes a 2-hour battery and PV together "
                      "(offered only when PV is enabled at intake)."),
            free_assets=[BATTERY, PV],
            discrete_choices=[DiscreteChoice(key="max_hours", values=[2])],
        ),
    ],
    headline_metrics=["npv", "payback", "sizes"],
    value_streams=["demand_charge_reduction", "energy_shift", "export_credit"],
    key_drivers=[
        "battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw",
        "demand_charge_price", "energy_price_level", "discount_rate",
    ],
    report_template_id="bess_at_site_v1",
)

QUESTIONS: Mapping[str, DecisionQuestion] = {BESS_AT_SITE.question_id: BESS_AT_SITE}


def get_question(question_id: str | None) -> DecisionQuestion | None:
    """The template with that id, or None (a custom question has no template)."""
    return QUESTIONS.get(question_id or "")


def option(question: DecisionQuestion, option_id: str) -> OptionSpec:
    for o in question.options:
        if o.option_id == option_id:
            return o
    raise KeyError(f"{question.question_id} has no option {option_id!r}")


def max_hours(opt: OptionSpec) -> float | None:
    """The option's enumerated battery duration, or None without a battery."""
    if BATTERY not in opt.free_assets:
        return None
    for choice in opt.discrete_choices:
        if choice.key == "max_hours":
            [value] = choice.values
            return float(value)
    raise ValueError(f"option {opt.option_id!r} has a battery but no max_hours")


def pv_enabled(intake: Mapping[str, Any] | None) -> bool:
    pv = (intake or {}).get("pv") if isinstance(intake, Mapping) else None
    return isinstance(pv, Mapping) and bool(pv.get("enabled"))


def options_for(question: DecisionQuestion,
                intake: Mapping[str, Any] | None) -> list[OptionSpec]:
    """The options this intake runs: the PV option only when PV is enabled."""
    with_pv = pv_enabled(intake)
    return [o for o in question.options if with_pv or PV not in o.free_assets]
