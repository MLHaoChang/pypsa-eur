"""
Canada (federal) — jurisdiction pack (IC P4 plan WP4.3b / WP4.4; C7, C11).

Every rule cites the Income Tax Act (ITA), the Income Tax Regulations (Reg.) or
the official publication. Absent (lookup → `not_established`): the EIFEL
interest limit (s. 18.2 — its excluded-entity tests are not modelled; flagged
in carryforward mode) and any class other than 43.1 / 43.2 (the case states
the class per asset).

Version as of 2026-01-01 (enacted law; the 2025 budget's proposals are not
in). Layers: federal (15 %) and a provincial slot (the case's `state_rate`),
not deductible from each other, on the same CCA. CCA: declining balance with
the half-year rule, or the enhanced first-year allowance for classes 43.1 /
43.2 (acquired after 2018-11-20, available for use before 2028: 100 % before
2024, 75 % in 2024–2025, 55 % in 2026–2027). The Clean Technology ITC (30 %,
15 % in 2034, refundable) reduces the capital cost by 100 % of the credit
(s. 13(7.1)).
"""
from __future__ import annotations

from datetime import date

from services.finance.packs.base import JurisdictionPack, Rule, register

_NRCAN = ("Natural Resources Canada, Tax Incentives for Clean Energy Technologies described in "
          "Class 43.1 and 43.2")


def make_pack() -> JurisdictionPack:
    rules = {
        "federal_rate": Rule(0.15, "ITA s. 123(1)(a) (38 %), s. 124(1) (10 % abatement), "
                                   "s. 123.4(2) (13 % general rate reduction)"),
        "cca_classes": Rule(
            {"43.1": {"rate": 0.30},
             "43.2": {"rate": 0.50, "acquired_after": "2005-02-22", "acquired_before": "2025-01-01"}},
            f"Reg. 1100(1)(a) and Schedule II, Classes 43.1 and 43.2; {_NRCAN}: 30 % / 50 % "
            "declining balance; Class 43.2 for property acquired after February 22, 2005 and "
            "before 2025"),
        "half_year_rule": Rule(0.5, "Reg. 1100(2) (one half of the net addition in the year "
                                    "of acquisition)"),
        "enhanced_first_year_43": Rule(
            {"acquired_after": "2018-11-20",
             "by_available_for_use_year": {"2023": 1.0, "2024": 0.75, "2025": 0.75, "2026": 0.55,
                                           "2027": 0.55}},
            f"Reg. 1100(2) (accelerated investment incentive property; Classes 43.1 and 43.2); "
            f"{_NRCAN}: property acquired after November 20, 2018 and available for use before "
            "2028, 100 % with a phase-out after 2023 (75 % in 2024–2025, 55 % in 2026–2027)"),
        "non_capital_loss": Rule({"allowance": 0.0, "limit_share": 1.0, "years": 20},
                                 "ITA s. 111(1)(a) (non-capital losses, 20 taxation years "
                                 "forward)"),
        "clean_technology_itc": Rule(
            {"available_from": "2023-03-28",
             "rate_by_available_for_use_year": {"2023": 0.30, "2034": 0.15, "2035": 0.0},
             "labour_requirements_reduction": 0.10},
            "ITA s. 127.45 (Clean Technology Investment Tax Credit: 30 % for property available "
            "for use before 2034, 15 % in 2034), s. 127.46 (labour requirements: the rate "
            "reduced by 10 percentage points when not met); CRA, Clean Technology ITC"),
        "itc_capital_cost_reduction": Rule(1.0, "ITA s. 13(7.1) (government assistance reduces "
                                                "the capital cost), s. 127(11.1)"),
    }
    return JurisdictionPack(
        jurisdiction="ca_federal", country="CA", valid_from=date(2026, 1, 1),
        source="Canadian federal income tax: ITA and Income Tax Regulations (enacted through "
               "2025); CRA; Natural Resources Canada",
        rules=rules, notes="P4 WP4.3b: federal rate, CCA 43.1 / 43.2, losses, Clean Tech ITC")


register("ca_federal", make_pack)
