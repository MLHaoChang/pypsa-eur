"""Reserve products a backup storage may commit headroom to (DC grid readiness 06).

Generic product shapes only (SAGC-1: no market names, no shipped prices):
``fast_symmetric`` is an FCR-style symmetric product, ``secondary`` and
``tertiary`` are aFRR- and mFRR-style directional ones. The capacity price is a
user Library series (per MW per hour committed, in the project's currency);
nothing market-specific ships with the app.
"""

from __future__ import annotations

from models.commercial import ReserveDirection, ReserveKind, ReserveProduct

__all__ = [
    "CURRENCY_META",
    "PRICE_FRAME",
    "PRICE_PREFIX",
    "ReserveDirection",
    "ReserveKind",
    "ReserveProduct",
    "check_unique",
    "price_column",
]

# The names the binding (investment case) and the solver share; they live here so
# the IC package never imports the solver (test_investment_case_tripwires).
#: The bound price frame (``buses_t``) and its column per product.
PRICE_FRAME = "ic_reserve_price"
PRICE_PREFIX = "ic:reserve:"
#: Each bound price's currency, {product id: ISO code or None} (binding, for the preflight).
CURRENCY_META = "ic_reserve_price_currency"


def price_column(product_id: str) -> str:
    return f"{PRICE_PREFIX}{product_id}"


def check_unique(products: list[ReserveProduct]) -> list[ReserveProduct]:
    """Product ids are unique within a config."""
    ids = [p.id for p in products]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"reserve products: id(s) {dup} appear more than once")
    return products
