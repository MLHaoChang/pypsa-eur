"""
Canada (federal) — jurisdiction pack (IC P4 plan WP4.3b / WP4.4; C7, C11).

Every rule cites the Income Tax Act (ITA), the Income Tax Regulations (Reg.) or
the official publication. Absent (lookup → `not_established`): the EIFEL
interest limit (s. 18.2 — its excluded-entity tests are not modelled; flagged
in carryforward mode) and any class other than 43.1 / 43.2 (the case states
the class per asset).

Two versions (C11: the law state at financial close):
- 2026-01-01 — the accelerated investment incentive for Classes 43.1 / 43.2
  as then enacted (acquired after 2018-11-20, available for use before 2028:
  100 % before 2024, 75 % in 2024–2025, 55 % in 2026–2027);
- 2026-03-26 — the Budget 2025 Implementation Act, No. 1 (S.C. 2026, c. 3,
  royal assent 2026-03-26, the first-year amendments deemed in force from
  2025-01-01): the old incentive closes to property acquired before 2025
  (Reg. 1104(4)); Class 43.1 property acquired after 2024 (Reg. 1104(4.01))
  takes 100 % if available for use before 2030, 75 % in 2030–2031, 55 % in
  2032–2033 (Reg. 1100(2) A.1(b)). WP4.3b review B1.

Layers: federal (15 %) and a provincial slot (the case's `state_rate`), not
deductible from each other, on the same CCA (provinces start from federal
UCC). The Clean Technology ITC (30 %, 15 % in 2034, refundable) on clean
technology property acquired from 2023-03-28 reduces the capital cost by the
credit — in the FOLLOWING year (s. 13(7.1)(e), s. 127.45(6)); a negative UCC
is recaptured (s. 13(1)). WP4.3b review B2–B4.
"""
from __future__ import annotations

from datetime import date

from services.finance.packs.base import JurisdictionPack, Rule, register

_NRCAN = ("Natural Resources Canada, Tax Incentives for Clean Energy Technologies described in "
          "Class 43.1 and 43.2")
_C3 = "Budget 2025 Implementation Act, No. 1, S.C. 2026, c. 3 (royal assent 2026-03-26)"
_AIIP_YEARS = {"2023": 1.0, "2024": 0.75, "2025": 0.75, "2026": 0.55, "2027": 0.55}


def _common() -> dict:
    return {
        "federal_rate": Rule(0.15, "ITA s. 123(1)(a) (38 %), s. 124(1) (10 % abatement), "
                                   "s. 123.4(1)–(2) (13 % general rate reduction)"),
        "cca_classes": Rule(
            {"43.1": {"rate": 0.30},
             "43.2": {"rate": 0.50, "acquired_after": "2005-02-22", "acquired_before": "2025-01-01"}},
            f"Reg. 1100(1)(a) and Schedule II, Classes 43.1 and 43.2; {_NRCAN}: 30 % / 50 % "
            "declining balance; Class 43.2 for property acquired after February 22, 2005 and "
            "before 2025"),
        "half_year_rule": Rule(0.5, "Reg. 1100(2) (one half of the net addition in the year "
                                    "of acquisition)"),
        "non_capital_loss": Rule({"allowance": 0.0, "limit_share": 1.0, "years": 20},
                                 "ITA s. 111(1)(a) (non-capital losses, 20 taxation years "
                                 "forward; the 3-year carryback is not modelled)"),
        "clean_technology_itc": Rule(
            {"acquired_from": "2023-03-28",
             "rate_by_available_for_use_year": {"2023": 0.30, "2034": 0.15, "2035": 0.0},
             "labour_requirements_reduction": 0.10},
            "ITA s. 127.45(1) (specified percentage: nil for property acquired before "
            "2023-03-28; 30 % for property available for use before 2034, 15 % in 2034), "
            "s. 127.45(2) (refundable), s. 127.46(1)–(2) (labour requirements: 10 points less "
            "unless elected); CRA, Clean Technology ITC"),
        # Which property is "clean technology property" (s. 127.45(1)), mapped
        # to the network's carriers (this pack's mapping, stated); fail closed:
        # a carrier in neither list is not established (WP4.3b review B4).
        "clean_technology_property": Rule(
            {"eligible": ["solar", "solar rooftop", "solar-hsat", "pv", "onwind", "offwind",
                          "offwind-ac", "offwind-dc", "offwind-float", "wind", "ror",
                          "geothermal", "battery", "battery storage", "home battery", "bess",
                          "battery charger", "battery discharger"],
             "not_eligible": ["gas", "ocgt", "ccgt", "gas chp", "gas_chp", "chp", "diesel", "oil",
                              "coal", "lignite"]},
            "ITA s. 127.45(1) \"clean technology property\" para. (d): (d)(i) Class 43.1 "
            "(d)(ii), (iii.1), (v), (vi), (xiv) (solar, wind, small hydro ≤ 50 MW — large "
            "reservoir `hydro` is left unclassified), (d)(ii) Class 43.1 (d)(xviii), (xix) "
            "(electrical energy storage, no fossil fuel), (d)(iii) Class 43.1 (d)(i), (d)(v) "
            "geothermal (Class 43.1 (d)(vii)); cogeneration and fossil-assisted property "
            "excluded; carrier mapping: this pack"),
        "itc_capital_cost_reduction": Rule(
            {"share": 1.0, "lag_years": 1},
            "ITA s. 13(7.1)(e) (assistance deducted for a taxation year ending before — the "
            "capital cost is reduced from the following year), s. 127.45(6) (the Clean "
            "Technology ITC deemed so deducted), s. 13(1) (recapture of a negative UCC)"),
    }


def make_pack() -> JurisdictionPack:
    rules = _common()
    rules["first_year_programs"] = Rule(
        [{"name": "aiip", "classes": ["43.1", "43.2"], "acquired_after": "2018-11-20",
          "by_available_for_use_year": _AIIP_YEARS}],
        f"Reg. 1100(2) and 1104(4) (accelerated investment incentive property; Classes 43.1 and "
        f"43.2), as enacted at 2026-01-01; {_NRCAN}: acquired after November 20, 2018, available "
        "for use before 2028 — 100 %, 75 % in 2024–2025, 55 % in 2026–2027")
    return JurisdictionPack(
        jurisdiction="ca_federal", country="CA", valid_from=date(2026, 1, 1),
        source="Canadian federal income tax: ITA and Income Tax Regulations as enacted at "
               "2026-01-01; CRA; Natural Resources Canada",
        rules=rules, notes="P4 WP4.3b: federal rate, CCA 43.1 / 43.2, losses, Clean Tech ITC")


def make_pack_2026_03_26() -> JurisdictionPack:
    rules = _common()
    rules["first_year_programs"] = Rule(
        [{"name": "aiip", "classes": ["43.1", "43.2"], "acquired_after": "2018-11-20",
          "acquired_before": "2025-01-01", "by_available_for_use_year": _AIIP_YEARS},
         {"name": "raiip", "classes": ["43.1"], "acquired_after": "2024-12-31",
          "by_available_for_use_year": {"2029": 1.0, "2030": 0.75, "2031": 0.75, "2032": 0.55,
                                        "2033": 0.55}}],
        f"Reg. 1100(2) A and A.1(b) (Class 43.1 factor 2⅓ / 1½ / 5⁄6: 100 % available for use "
        f"before 2030, 75 % in 2030–2031, 55 % in 2032–2033), Reg. 1104(4) (accelerated "
        f"investment incentive property: acquired after November 20, 2018 and before 2025), "
        f"Reg. 1104(4.01) (acquired after 2024, available for use before 2034), as amended by "
        f"{_C3}, deemed in force from 2025-01-01")
    return JurisdictionPack(
        jurisdiction="ca_federal", country="CA", valid_from=date(2026, 3, 26),
        source=f"Canadian federal income tax: ITA and Income Tax Regulations as amended by {_C3}; "
               "CRA; Natural Resources Canada",
        rules=rules, notes="P4 WP4.3b review: the reaccelerated first-year allowance")


register("ca_federal", make_pack)
register("ca_federal", make_pack_2026_03_26)
