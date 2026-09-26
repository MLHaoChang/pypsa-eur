"""
Germany — jurisdiction pack STUB (WP0.2). Rule tables land in P4 WP4.3a:
KStG/GewSt corporate rate, straight-line / declining-balance depreciation,
Netzentgelte structure (incl. §19 StromNEV atypical use), §17(2b) EnWG FCA
discount slots. Until then every lookup is `not_established`.
"""
from __future__ import annotations

from datetime import date

from services.finance.packs.base import JurisdictionPack, register


def make_pack() -> JurisdictionPack:
    return JurisdictionPack(
        jurisdiction="eu_de",
        country="DE",
        valid_from=date(2026, 1, 1),
        source="stub — no rules yet; P4 WP4.3a fills from cited statutes",
        rules={},
        notes="stub",
    )


register("eu_de", make_pack)
