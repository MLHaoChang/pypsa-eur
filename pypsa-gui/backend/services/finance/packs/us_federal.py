"""
United States (federal) — jurisdiction pack (IC P4 plan WP4.3a / WP4.4; C11).

Every rule cites its statute or official publication. A rule that could not be
cited is ABSENT (lookup → `not_established`), never guessed — notably the
MACRS class of a technology (the 2025 reconciliation act changed the 5-year
class for some energy property; until a cited rule lands, the case states the
class per asset: `FinanceInputs.depreciation_class_by_asset`).

Version as of 2026-01-01 (financial close on or after that date). Rates that
change per tax year inside the case are schedules `{from_year: value}`;
values fixed once by a case date (bonus by acquisition date) are rules with
their dates. Incentive rules (WP4.4): the §48E ITC and §45Y PTC with the PWA
multiplier, the published PTC inflation factors, the phase-out after 2032,
the 2025 act's wind / solar termination and the FEOC date. State packs are
slots: the state layer's rate is a case input.
"""
from __future__ import annotations

from datetime import date

from services.finance.packs.base import JurisdictionPack, Rule, register

_PUB946 = "IRS Publication 946 (2024), Appendix A, Table A-1 (3-, 5-, 7-, 10-, 15- and 20-year " \
          "property, half-year convention)"

# Table A-1, percent of basis per recovery year (half-year convention).
_MACRS_HALF_YEAR = {
    "3": [33.33, 44.45, 14.81, 7.41],
    "5": [20.00, 32.00, 19.20, 11.52, 11.52, 5.76],
    "7": [14.29, 24.49, 17.49, 12.49, 8.93, 8.92, 8.93, 4.46],
    "10": [10.00, 18.00, 14.40, 11.52, 9.22, 7.37, 6.55, 6.55, 6.56, 6.55, 3.28],
    "15": [5.00, 9.50, 8.55, 7.70, 6.93, 6.23, 5.90, 5.90, 5.91, 5.90, 5.91, 5.90, 5.91, 5.90,
           5.91, 2.95],
    "20": [3.750, 7.219, 6.677, 6.177, 5.713, 5.285, 4.888, 4.522, 4.462, 4.461, 4.462, 4.461,
           4.462, 4.461, 4.462, 4.461, 4.462, 4.461, 4.462, 4.461, 2.231],
}


def make_pack() -> JurisdictionPack:
    rules = {
        "corporate_rate": Rule(0.21, "26 U.S.C. §11(b) (as amended by Pub. L. 115-97, §13001)"),
        "macrs_half_year_percent": Rule(_MACRS_HALF_YEAR, _PUB946),
        # Bonus depreciation (§168(k)): 100 % for property ACQUIRED after
        # 2025-01-19 (the 2025 reconciliation act, Pub. L. 119-21, amending
        # §168(k)); property acquired earlier under a binding contract keeps
        # the TCJA phase-down by the year placed in service.
        "bonus_depreciation": Rule(
            {"acquired_after": "2025-01-19", "share": 1.0,
             "earlier_by_placed_in_service_year": {"2023": 0.8, "2024": 0.6, "2025": 0.4,
                                                    "2026": 0.2, "2027": 0.0}},
            "26 U.S.C. §168(k)(1), (6)(A) as amended by Pub. L. 115-97 §13201 and Pub. L. 119-21 "
            "§70301 (2025)"),
        # NOL: indefinite carryforward, deduction limited to 80 % of taxable
        # income (losses arising in tax years after 2017).
        "nol": Rule({"allowance": 0.0, "limit_share": 0.8, "years": None},
                    "26 U.S.C. §172(a)(2), (b)(1)(A)(ii)"),
        # Business-interest limit: 30 % of adjusted taxable income; ATI on an
        # EBITDA basis for tax years beginning after 2024-12-31 (the 2025 act);
        # the small-business exemption (§163(j)(3), the §448(c) gross-receipts
        # test) is a case input (`small_business_163j`). Disallowed interest
        # carries forward indefinitely (§163(j)(2)).
        "interest_limit": Rule({"share": 0.3, "ati_basis": "ebitda", "carryforward": True},
                               "26 U.S.C. §163(j)(1), (2), (3), (8)(A)(v) as amended by Pub. L. "
                               "119-21 (2025)"),
        # ── incentives (WP4.4) ──
        # Clean electricity ITC: 6 %, or 30 % when the prevailing-wage and
        # apprenticeship requirements are met (or the facility is < 1 MW —
        # the case states `pwa_met`). Energy-community and domestic-content
        # bonuses are not modelled (flagged).
        "clean_electricity_itc": Rule({"base": 0.06, "alternative": 0.30},
                                      "26 U.S.C. §48E(a)(2)(A), (d)(3)–(4)"),
        # Clean electricity PTC: 0.3 ¢/kWh (1.5 ¢ with PWA) × the inflation
        # adjustment factor of the calendar year of sale, rounded to 0.05 ¢
        # (0.1 ¢); for the 10 years from placed in service. Published factors
        # by calendar year; later years are projected (flagged).
        "clean_electricity_ptc": Rule(
            {"base_cents_per_kwh": 0.3, "alternative_cents_per_kwh": 1.5,
             "rounding_cents": {"base": 0.05, "alternative": 0.1}, "term_years": 10,
             "inflation_adjustment_factor": {"2026": 2.0570}},
            "26 U.S.C. §45Y(a)(2), (b)(1)(B), (c); IRS notice, Publication of Inflation Adjustment "
            "Factor and Applicable Amounts for the Clean Electricity Production Credit for "
            "Calendar Year 2026, 91 FR 56942 (FR Doc. 2026-18105, 2026-09-04): factor 2.0570, "
            "0.6 / 3.1 ¢/kWh"),
        # Phase-out by the calendar year construction begins, after the
        # applicable year 2032: 100 %, 75 %, 50 %, then 0 %.
        "clean_electricity_phase_out": Rule(
            {"applicable_year": 2032, "shares_after": [1.0, 0.75, 0.5], "later": 0.0},
            "26 U.S.C. §45Y(d)(2)–(3) and §48E(e)(2)–(3) as amended by Pub. L. 119-21 (2025)"),
        # Wind and solar: no credit for property placed in service after
        # 2027-12-31 unless construction began by 2026-07-04 (12 months after
        # enactment); beginning of construction per Notice 2025-42 (physical
        # work test). Storage at such a facility is not affected.
        "wind_solar_termination": Rule(
            {"carriers": ["solar", "onwind", "offwind-ac", "offwind-dc", "offwind-float"],
             "placed_in_service_by": "2027-12-31", "unless_construction_begins_by": "2026-07-04"},
            "26 U.S.C. §45Y(d)(4) and §48E(e)(4) as added by Pub. L. 119-21 (2025), effective for "
            "facilities the construction of which begins after the date which is 12 months after "
            "2025-07-04; IRS Notice 2025-42 (beginning of construction)"),
        # Prohibited foreign entities: no credit with material assistance
        # from a prohibited foreign entity for construction beginning after
        # 2025-12-31 (the case states `feoc_flag` per incentive).
        "feoc_material_assistance": Rule(
            {"construction_begins_after": "2025-12-31"},
            "26 U.S.C. §45Y(b)(1)(E) and §48E(b)(6) (material assistance from a prohibited "
            "foreign entity) as amended by Pub. L. 119-21 (2025)"),
        # The ITC reduces the depreciable basis by 50 % of the credit.
        "itc_basis_reduction": Rule(0.5, "26 U.S.C. §50(c)(1), (3)"),
    }
    return JurisdictionPack(
        jurisdiction="us_federal", country="US", valid_from=date(2026, 1, 1),
        source="US federal income tax: IRC (26 U.S.C.) as amended through Pub. L. 119-21 (2025); "
               "IRS Pub. 946 (2024)",
        rules=rules, notes="P4 WP4.3a: corporate rate, MACRS tables, bonus, NOL, §163(j)")


def macrs_fractions(pack: JurisdictionPack, recovery_class: str) -> tuple[float, ...]:
    """The Table A-1 schedule of a recovery class as fractions (sum 1)."""
    table = pack.rule("macrs_half_year_percent")
    if table.status != "ok" or recovery_class not in table.value:
        raise KeyError(f"MACRS class {recovery_class!r} not in the pack")
    pct = table.value[recovery_class]
    return tuple(p / 100.0 for p in pct)


register("us_federal", make_pack)
