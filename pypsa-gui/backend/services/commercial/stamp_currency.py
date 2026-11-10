"""Registers the defaults-pack stamp lookup the `Tariff` model reads when a tariff
read is sent back (gridspine-library-eu 05, GK A G5-1). Models never import services,
so this module hands `models.commercial` the lookup; `services.library.items` imports
it, so it is registered whenever the app is.
"""

from __future__ import annotations

from types import SimpleNamespace

from models import commercial as M
from services.library.defaults_pack.loader import (
    DefaultsPackError,
    stamped_tariff_currency,
)


def stamp_currency(pack_hash: str, tariff_id: str) -> str | None:
    """The currency `pack_hash`'s pack names for tariff `tariff_id`, or None on a lookup
    miss; `DefaultsPackError` when the pack cannot be read (GLE3-1)."""
    return stamped_tariff_currency(SimpleNamespace(id=tariff_id, pack_hash=pack_hash))


M.register_stamp_currency(stamp_currency, DefaultsPackError)
