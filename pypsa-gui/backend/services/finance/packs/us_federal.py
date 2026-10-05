"""
United States (federal) — jurisdiction pack STUB (WP0.2). Rule tables land in
P4 WP4.3a/WP4.4: 21 % corporate rate, MACRS 5/7/15-yr + bonus, ITC/PTC with
OBBBA begin-construction / placed-in-service dates, FEOC flag, storage ITC
runway. State packs are slots, not v1 content. Until then every lookup is
`not_established`.
"""
from __future__ import annotations

from datetime import date

from services.finance.packs.base import JurisdictionPack, register


def make_pack() -> JurisdictionPack:
    return JurisdictionPack(
        jurisdiction="us_federal",
        country="US",
        valid_from=date(2026, 1, 1),
        source="stub — no rules yet; P4 WP4.3a/WP4.4 fill from cited statutes",
        rules={},
        notes="stub",
    )


register("us_federal", make_pack)
