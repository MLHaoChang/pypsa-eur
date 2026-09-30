"""
Germany — jurisdiction pack (IC P4 plan WP4.3a; C7, C11).

Every rule cites its statute. A rule that could not be cited is ABSENT
(lookup → `not_established`), never guessed — notably the useful life of an
asset class the BMF AfA tables do not list (battery storage): the case states
it (`FinanceInputs.depreciation_class_by_asset`) or the tax section is not
established.

Version as of 2026-01-01. Corporate tax is three layers (plan C7):
- GewSt: 3.5 % Messzahl × the municipality's Hebesatz (a case input) on the
  Gewerbeertrag = profit + 25 % of financing costs above €200k (§8 Nr. 1
  GewStG — interest only here; rent and lease shares are not modelled, stated)
  with its own loss pool (€1m + 60 %, §10a GewStG); not deductible (§4 Abs. 5b
  EStG);
- KSt: 15 %, falling by one point a year from 2028 to 10 % in 2032 (schedule),
  with SolZ 5.5 % on top; loss carryforward €1m + 70 % (2024–2027), 60 % from
  2028 (§10d EStG).
The Zinsschranke (§4h EStG) is a Freigrenze: above €3m net interest the 30 %
cap applies to ALL net interest; the escape and stand-alone clauses and the
EBITDA carryforward are not modelled — a case above the Freigrenze is flagged
`zinsschranke_simplified` (never a silent low tax).
"""
from __future__ import annotations

from datetime import date

from services.finance.packs.base import JurisdictionPack, Rule, register

_INVEST_2025 = ("Gesetz für ein steuerliches Investitionssofortprogramm zur Stärkung des "
                "Wirtschaftsstandorts Deutschland vom 14.07.2025 (BGBl. 2025 I Nr. 161)")
_WACHSTUM = "Wachstumschancengesetz vom 27.03.2024 (BGBl. 2024 I Nr. 108)"


def make_pack() -> JurisdictionPack:
    rules = {
        "kst_rate": Rule({"2026": 0.15, "2028": 0.14, "2029": 0.13, "2030": 0.12, "2031": 0.11,
                          "2032": 0.10},
                         f"§23 Abs. 1 KStG as amended by {_INVEST_2025}"),
        "solz_share": Rule(0.055, "§4 Satz 1 SolZG 1995"),
        "gewst_messzahl": Rule(0.035, "§11 Abs. 2 GewStG"),
        "gewst_deductible": Rule(False, "§4 Abs. 5b EStG"),
        "gewst_interest_addback": Rule({"share": 0.25, "allowance": 200_000.0},
                                       "§8 Nr. 1 Buchst. a GewStG (Entgelte für Schulden; the "
                                       "Freibetrag of €200,000 applies to the sum of Nr. 1 a–f)"),
        "gewst_loss": Rule({"allowance": 1_000_000.0, "limit_share": 0.6, "years": None},
                           "§10a Satz 1–2 GewStG"),
        "kst_loss": Rule({"allowance": 1_000_000.0, "limit_share": {"2024": 0.7, "2028": 0.6},
                          "years": None},
                         f"§10d Abs. 2 EStG (via §8 Abs. 1 KStG) as amended by {_WACHSTUM}"),
        "zinsschranke": Rule({"share": 0.3, "freigrenze": 3_000_000.0},
                             "§4h Abs. 1 Satz 1, Abs. 2 Satz 1 Buchst. a EStG (via §8a KStG)"),
        # Degressive AfA for movables acquired 2025-07-01 … 2027-12-31: up to 3×
        # the straight-line rate, at most 30 %, switch to straight-line allowed.
        "degressive_afa": Rule({"acquired_from": "2025-07-01", "acquired_to": "2027-12-31",
                                "multiple_of_sl": 3.0, "max_rate": 0.30},
                               f"§7 Abs. 2 EStG as amended by {_INVEST_2025}"),
        # Useful lives (years) from the BMF AfA table for general assets
        # (BMF-Schreiben vom 15.12.2000, BStBl. I 2000 S. 1532): only classes
        # the table names.
        "afa_useful_life_years": Rule({"solar": 20},
                                      "AfA-Tabelle für die allgemein verwendbaren Anlagegüter "
                                      "(BMF-Schreiben vom 15.12.2000): Photovoltaikanlagen 20 "
                                      "Jahre"),
        # Straight-line AfA is pro rata by month in the year of acquisition.
        "afa_first_year": Rule("pro_rata_months", "§7 Abs. 1 Satz 4 EStG"),
    }
    return JurisdictionPack(
        jurisdiction="eu_de", country="DE", valid_from=date(2026, 1, 1),
        source="German corporate taxation: KStG, SolZG, GewStG, EStG as amended through "
               "BGBl. 2025 I Nr. 161; BMF AfA-Tabelle AV (2000)",
        rules=rules, notes="P4 WP4.3a: KSt path, SolZ, GewSt, losses, Zinsschranke, AfA")


register("eu_de", make_pack)
