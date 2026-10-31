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

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SERVICES = _BACKEND / "services"
# The chat harness (`harness/`) holds the tool catalogue since 2026-10-05;
# its files are keyed `harness/<path>` so the pin follows the move.
_HARNESS = _BACKEND / "harness"
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
    "adequacy/sizing_loop_runner.py": (1, "AS-06: a RangeIndex has no step, so the tsam view reads it as hourly "
                                          "(documented in _PeriodView); a timestamped index keeps its own step"),
    "adequacy/levers.py": (2, "annualise to MWh/yr: Σ w·p × 8760 / Σ w and p_nom × 8760 — "
                              "8760 h/yr, weights in hours; a unit"),
    "solver/adequacy.py": (3, "EH import-energy cap: E MWh/yr × Σ w / 8760 per period "
                              "(formula, code, message) — a unit"),
    "solver_service.py": (1, "comment restating the solver/adequacy.py cap formula"),
    "solver/envelope_floors.py": (4, "AS-03 genset run-hour cap: H h/yr × Σ w / 8760 per period "
                                     "(docstring, code) and the cap's [0, 8760] h/yr range — a unit"),
    "asset_results/compute.py": (2, "prose explaining why Σweights/8760 is avoided"),
    "asset_schema/fields.py": (1, "MTTR help text restating the occurrence.py formula "
                                  "(8760 × rate / MTTR events/yr) — prose, a unit (S1a)"),
    "harness/loop.py": (1, "prompt text about CSV row counts"),
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
    "harness/catalogue.py": (3, "tool descriptions (row counts, default hours)"),
    "harness/sensitivity_comparison.py": (1, "HOURS_PER_YEAR = 8760 h/yr annualises; not a snapshot step"),
    "legacy_import.py": (1, "prose describing a legacy fixture"),
    "profile_gen/load_archetypes.py": (1, "an archetype is drawn on the source year's "
                                          "HOURS by design (load shapes are hourly only); "
                                          "apply aligns it onto the snapshots and refuses "
                                          "a sub-hourly axis"),
    "profile_gen/load_upload.py": (1, "resamples a meter export to HOURLY power, the load "
                                      "family's axis: sub-hourly readings are averaged and a "
                                      "coarser step is refused, never assumed"),
    "profile_gen/availability.py": (1, "an availability bound is drawn on the source year's "
                                       "HOURS like every generated profile; apply aligns it "
                                       "onto the snapshots"),
    "profile_gen/current.py": (1, "a series read from a snapshot window is padded onto its "
                                 "source year, the hourly axis every source has (spec §4)"),
    "profile_gen/series.py": (1, "a price/inflow/COP series is drawn on the source "
                                 "year's HOURS like every generated profile; apply aligns "
                                 "it onto the snapshots"),
    "profile_shapes.py": (2, "168 h week template used ONLY when there are no snapshots "
                             "to follow; with snapshots the template follows the axis"),
    "results/finance_case.py": (2, "IC P4 C3: a period is a year when its represented hours "
                                   "(Σ snapshot weights, any resolution) are 8760 h (8784 in a leap "
                                   "year); `annualise` scales by 8760 / those hours — units"),
    "results/asset_economics.py": (1, "capacity factor = energy / (8760 × p_nom × years) — a unit"),
    "serialization.py": (1, "prose about payload size"),
    "solver/myopic.py": (1, "prose: nyears = Σ hours / 8760"),
    "solver/process_uc.py": (1, "YEAR_HOURS: the year time base is 8,760 hourly snapshots per period by "
                                "construction (eh_site.stage_network); is_year_base counts snapshots, so a "
                                "sub-hourly year is not taken for it and keeps linearised UC"),
    "solver/objective.py": (2, "prose: PyPSA's nyears = Σ weights / 8760"),
    "time_aggregation_service.py": (3, "prose about tsam's 8760-hour year; the period length "
                                       "is converted to STEPS via the axis step hours and weights "
                                       "rescale to the period's original hours (WP1.0 review #8)"),
    "timeseries_qa.py": (1, "prose example"),
    "user_timeseries.py": (2, "annual HOURLY reference for representative-week sampling; the "
                              "sampler refuses sub-hourly axes (not_supported_for_freq)"),
    "validation_service.py": (4, "prose + the Σ weight × hours ≈ 8760 sanity message"),
    # The guided investment study (GS branch, merged in U2 WP0). Its site pack
    # IS hourly by design (MVP-1: one non-leap year of 8760 hourly steps,
    # weights 1, a leap year refused); `study/tariff.py` went in U2 WP10.
    "study/packs.py": (7, "the site pack's own axis: 8760 hourly steps of one non-leap year "
                          "(HOURS, the date_range and the uploaded-load check against it), a "
                          "leap year refused, and prose — a declared hourly model, not an "
                          "assumption about someone else's axis"),
    "study/runner.py": (2, "prose: each option is one 8760 h LP (deadline sizing, fidelity)"),
    "study/compile.py": (1, "with no snapshot axis given, an hourly calendar of one non-leap "
                            "year enumerates every (month, weekday, hour) a tariff band can "
                            "key on, to refuse unpriced hours; band rules read the hour of day "
                            "only, so the check is complete at any resolution — an enumeration, "
                            "not a step (U2 WP4)"),
    "study/engine_adapter.py": (1, "a bill is annual when Σ objective weights (hours at any "
                                   "resolution) is a 365- or 366-day year, 8760 or 8784 h — a "
                                   "unit, not a step (GS's annual-bill rule, U2 WP5)"),
    # The renewable profile generator (spec: profile generation via atlite /
    # synthetic sources). Its product IS an hourly weather-year axis by
    # design; the apply path pins that shape before aligning to a network.
    "profile_gen/synthetic.py": (3, "the generator's own axis: one full weather year of "
                                    "hourly steps (8760, or 8784 with Feb 29 in a leap year) — "
                                    "a declared hourly product, like study/packs.py, not an "
                                    "assumption about someone else's axis"),
    "profile_gen/atlite_source.py": (1, "prose: the axis length contract is the cutout's, "
                                        "explicitly not assumed to be 8760"),
    "profile_gen/apply.py": (1, "_ONE_HOUR (Timedelta(hours=1)) verifies the generated axis "
                                "is contiguous hourly before alignment — a shape check on the "
                                "artifact, not an assumption about the network's resolution"),
    "profile_gen/shape.py": (2, "FLH = mean CF x the WEATHER year's hours (8760 or 8784) and "
                                "peak_shift's hourly-axis check — both about the generated "
                                "artifact's own calendar, before alignment to any network"),
    "profile_gen/compare.py": (1, "FLH = generator-weighted mean CF times the calendar year's "
                                  "8760 or 8784 hours — an annual unit conversion, not a "
                                  "snapshot-step assumption"),
}


def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for root, prefix in ((_SERVICES, ""), (_HARNESS, "harness/")):
        for p in sorted(root.rglob("*.py")):
            n = sum(1 for line in p.read_text().splitlines() if _PATTERN.search(line))
            if n:
                out[prefix + str(p.relative_to(root))] = n
    return out


def test_every_hourly_assumption_site_is_listed_with_a_reason():
    found = _scan()
    new = {f: c for f, c in found.items() if f not in ALLOWED}
    assert not new, f"new hourly-assumption sites — review and add with a reason: {new}"
    grown = {f: (c, ALLOWED[f][0]) for f, c in found.items() if c > ALLOWED[f][0]}
    assert not grown, f"more sites than allowed (found, allowed): {grown}"


def test_the_inventory_is_pinned():
    # U2 WP10: GS's `study/tariff.py` (3 sites) is deleted.
    # S1a: `asset_schema/fields.py` (1 site, MTTR help text) is added.
    # Profile generator: synthetic.py (3), atlite_source.py (1), apply.py (1) added.
    # RP-06: profile_gen/shape.py (2) added.
    # TS UI: profile_gen/current.py (1) added.
    # TS 03: profile_gen/series.py (1) added.
    # TS 02: profile_gen/availability.py (1) added.
    # TS 01 B3: profile_gen/load_upload.py (1) added.
    # TS 01 B1: profile_gen/load_archetypes.py (1) added.
    # G-a: profile_gen/compare.py (1) annualises weighted CF in calendar-year hours.
    # LC-18: harness/sensitivity_comparison.py (1) annualises in h/yr.
    # G2P-10: solver/process_uc.py (1) recognises the hourly year time base.
    # AS-03: solver/envelope_floors.py (4) prorates the annual run-hour cap.
    # AS-06: adequacy/sizing_loop_runner.py (1) reads a RangeIndex as hourly for tsam.
    assert sum(c for c, _ in ALLOWED.values()) == 93
    assert len(ALLOWED) == 49
    assert all(reason for _, reason in ALLOWED.values())
