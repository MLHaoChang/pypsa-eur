"""
Where the backend assumes HOURLY data (Edge Investment Case P1 WP1.0).

15-minute settlement makes an hourly assumption a silent 4× error. This test
does not remove them — most are correct (8760 as HOURS PER YEAR is right at
any resolution once snapshot weights are in hours) or are prose. It PINS them:
every site is listed with a reason, and a new site fails the test until
someone decides whether it is safe and says why.
"""
from __future__ import annotations

import pathlib
import re

_SERVICES = pathlib.Path(__file__).resolve().parent.parent / "services"
_PATTERN = re.compile(r'8760|freq="h"|Timedelta\(hours=1\)')

# file (relative to services/) -> (count, reason)
ALLOWED: dict[str, tuple[int, str]] = {
    "adequacy/copt.py": (1, "8760 h/yr in the occurrence-rate formula — a unit, not a step"),
    "adequacy/mc.py": (2, "prose about horizon length and accumulator size"),
    "adequacy/metrics.py": (4, "HOURS_PER_YEAR constant + prose; weights are hours (WP1.0)"),
    "adequacy/occurrence.py": (2, "8760 h/yr in events/yr = 8760·rate/MTTR — a unit"),
    "adequacy/sweep.py": (3, "same occurrence formula as occurrence.py — a unit"),
    "adequacy/eh_stages.py": (1, "common-mode occurrence 8760·q/MTTR events/yr — the "
                                 "occurrence.py formula, a unit"),
    "adequacy/eh_study.py": (1, "modelled hours = nyears × 8760 (nyears is Σ weights / 8760, "
                                "weights in hours) — a unit"),
    "adequacy/levers.py": (2, "annualise to MWh/yr: Σ w·p × 8760 / Σ w and p_nom × 8760 — "
                              "8760 h/yr, weights in hours; a unit"),
    "solver/adequacy.py": (3, "EH import-energy cap: E MWh/yr × Σ w / 8760 per period "
                              "(formula, code, message) — a unit"),
    "solver_service.py": (1, "comment restating the solver/adequacy.py cap formula"),
    "asset_results/compute.py": (2, "prose explaining why Σweights/8760 is avoided"),
    "chat_service.py": (1, "prompt text about CSV row counts"),
    "solver/periodized_costs.py": (3, "HOURS_PER_YEAR converts Σ objective weights to years "
                                      "(PyPSA's n.nyears) to scale annual FOM and capital cost "
                                      "to the modelled horizon — a unit, not a step (FOM merge, "
                                      "IC P2 WP2.0)"),
    "commercial/tariff_engine.py": (4, "8760 h/yr converts a capacity item's €/kW-year to the "
                                       "represented hours (Σ energy hours / 8760, the LP fee's "
                                       "nyears) — a unit, not a step (IC P2 WP2.1a-i)"),
    "commercial/lp_bindings.py": (1, "Σ weights ≈ 8760 h tells whether a period's snapshots "
                                     "REPRESENT a whole year (then every month is billed) — a "
                                     "unit test, not a step (IC WP1.5a review #2)"),
    "commercial/billing.py": (1, "Σ weights ≈ 8760 h tells whether a period's snapshots "
                                 "REPRESENT a whole year (then its calendar year is the billing "
                                 "period) — the lp_bindings rule, a unit test, not a step "
                                 "(IC P2 WP2.1b)"),
    "commercial/connection.py": (4, "8760 h/yr converts an ANNUAL connection fee to the "
                                    "operating time the snapshots represent (Σ weights / 8760) "
                                    "— a unit, not a step (IC WP1.4a)"),
    "chat_tools.py": (2, "prose / output-budget guidance"),
    "chat_tools_schema.py": (3, "tool descriptions (row counts, default hours)"),
    "legacy_import.py": (1, "prose describing a legacy fixture"),
    "profile_shapes.py": (2, "168 h week template used ONLY when there are no snapshots "
                             "to follow; with snapshots the template follows the axis"),
    "results/finance_case.py": (2, "IC P4 C3: a period is a year when its represented hours "
                                   "(Σ snapshot weights, any resolution) are 8760 h (8784 in a leap "
                                   "year); `annualise` scales by 8760 / those hours — units"),
    "results/asset_economics.py": (1, "capacity factor = energy / (8760 × p_nom × years) — a unit"),
    "serialization.py": (1, "prose about payload size"),
    "solver/myopic.py": (1, "prose: nyears = Σ hours / 8760"),
    "solver/objective.py": (2, "prose: PyPSA's nyears = Σ weights / 8760"),
    "time_aggregation_service.py": (3, "prose about tsam's 8760-hour year; the period length "
                                       "is converted to STEPS via the axis step hours and weights "
                                       "rescale to the period's original hours (WP1.0 review #8)"),
    "timeseries_qa.py": (1, "prose example"),
    "user_timeseries.py": (2, "annual HOURLY reference for representative-week sampling; the "
                              "sampler refuses sub-hourly axes (not_supported_for_freq)"),
    "validation_service.py": (4, "prose + the Σ weight × hours ≈ 8760 sanity message"),
}


def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in sorted(_SERVICES.rglob("*.py")):
        n = sum(1 for line in p.read_text().splitlines() if _PATTERN.search(line))
        if n:
            out[str(p.relative_to(_SERVICES))] = n
    return out


def test_every_hourly_assumption_site_is_listed_with_a_reason():
    found = _scan()
    new = {f: c for f, c in found.items() if f not in ALLOWED}
    assert not new, f"new hourly-assumption sites — review and add with a reason: {new}"
    grown = {f: (c, ALLOWED[f][0]) for f, c in found.items() if c > ALLOWED[f][0]}
    assert not grown, f"more sites than allowed (found, allowed): {grown}"


def test_the_inventory_is_pinned():
    assert sum(c for c, _ in ALLOWED.values()) == 61
    assert len(ALLOWED) == 30
    assert all(reason for _, reason in ALLOWED.values())
