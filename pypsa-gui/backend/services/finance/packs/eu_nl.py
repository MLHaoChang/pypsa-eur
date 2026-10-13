"""
Netherlands — jurisdiction pack (IC P4 plan WP4.3b; C7, C11).

Every rule cites its statute or the Belastingdienst's publication. A rule that
could not be cited is ABSENT (lookup → `not_established`): the useful lives of
energy assets (the Wet IB 2001 sets no table — the case states the class per
asset) and the Energie-investeringsaftrek (EIA, a yearly-changing list — an
incentive slot, absent).

Version as of 2026-01-01. One layer, the vennootschapsbelasting (VPB), with
two brackets; losses carry forward without a time limit, €1m in full plus 50 %
of the excess (art. 20); the earnings-stripping rule caps net interest at the
higher of 24.5 % of fiscal EBITDA and €1m, the excess carried forward (art.
15b); depreciation at most 20 % of cost a year (art. 3.30 lid 2 Wet IB 2001,
via art. 8 Wet Vpb). The residual value (restwaarde) and the one-year loss
carryback are not modelled (flagged).
"""
from __future__ import annotations

from datetime import date

from services.finance.packs.base import JurisdictionPack, Rule, register

_BD_2026 = ("Belastingdienst, Tarieven voor de vennootschapsbelasting / Veranderingen "
            "vennootschapsbelasting 2026")


def make_pack() -> JurisdictionPack:
    rules = {
        "vpb_brackets": Rule([[0.0, 0.19], [200_000.0, 0.258]],
                             f"art. 22 Wet Vpb 1969; {_BD_2026}: t/m € 200.000 19,0 %, "
                             "meer dan € 200.000 25,8 %"),
        "vpb_loss": Rule({"allowance": 1_000_000.0, "limit_share": 0.5, "years": None},
                         "art. 20 lid 2 Wet Vpb 1969 (from 2022: €1 mln in full, 50 % of the "
                         "excess; carryforward without a time limit)"),
        "earnings_stripping": Rule({"share": 0.245, "allowance": 1_000_000.0,
                                    "carryforward": True},
                                   f"art. 15b Wet Vpb 1969 (24,5 % from 2025); {_BD_2026}: "
                                   "rente niet aftrekbaar voor zover het saldo meer is dan 24,5 % "
                                   "van de winst, en meer dan € 1.000.000"),
        "depreciation_max_rate": Rule(0.20, "art. 3.30 lid 2 Wet IB 2001 (via art. 8 Wet Vpb "
                                            "1969): ten hoogste 20 % van de aanschaffingskosten "
                                            "per jaar"),
    }
    return JurisdictionPack(
        jurisdiction="eu_nl", country="NL", valid_from=date(2026, 1, 1),
        source="Dutch corporate income tax: Wet Vpb 1969, Wet IB 2001 (2026); Belastingdienst",
        rules=rules, notes="P4 WP4.3b: VPB brackets, losses, earnings stripping, 20 % cap")


register("eu_nl", make_pack)
