"""
A flat export price series in the Library (IC U1 follow-up, item e; plan "one engine, two
faces" §4 rule C3).

IC prices export only through `CommercialConfig.export_price_ref`, a Library series in the
org. A constant export price (the guided study's, or a defaults-pack tariff's
`meta.export.price_per_mwh`) is minted here as a flat series ON THE PROJECT'S SNAPSHOT AXIS,
so `commercial.binding.bind_commercial` aligns it one to one and covers every snapshot:

    ref = put_flat_export_series(db, org_id, f"study_{sid}_export", 40.0,
                                 snapshots=n.snapshots, source=pack.stamp)
    commercial["export_price_ref"] = ref.model_dump()

The series is stored naive when the snapshots are naive, which `align_to_snapshots` reads on
the snapshot clock in both timezone modes (with `commercial.timezone` set, naive snapshots are
UTC; without it they are the site clock), so a flat price is never shifted. A multi-period
snapshot MultiIndex contributes its timestamp level.

Minting is idempotent on content (`put_series`): the same price on the same axis returns the
same version; a changed price is the next version. `put_series` ROLLS BACK `db` on a version
race, so the caller must not hold other unsaved work in the same session.
"""
from __future__ import annotations

import math
from pathlib import Path
from uuid import UUID

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session as DBSession

from models.commercial import PriceSeriesRef
from services.library import series_store

__all__ = ["FLAT_EXPORT_NOTE", "put_flat_export_series"]

FLAT_EXPORT_NOTE = "flat_export_price"


def _axis(snapshots) -> pd.DatetimeIndex:
    idx = snapshots.get_level_values(-1) if isinstance(snapshots, pd.MultiIndex) else snapshots
    idx = pd.DatetimeIndex(idx)
    if len(idx) == 0:
        raise ValueError("snapshots are empty: a flat export series needs the axis it covers")
    if not idx.is_monotonic_increasing or idx.has_duplicates:
        raise ValueError("snapshots must be strictly increasing timestamps")
    return idx


def put_flat_export_series(db: DBSession, org_id: UUID, name: str, eur_per_mwh: float, *,
                           snapshots, source: str = "flat export price",
                           vintage_year: int | None = None, description: str | None = None,
                           created_by: UUID | None = None,
                           root: Path | None = None) -> PriceSeriesRef:
    """Store `eur_per_mwh` (EUR/MWh, the unit `ic_export_price` is in; may be negative)
    at every timestamp of `snapshots` as Library series `name`, and return the ref
    `CommercialConfig.export_price_ref` takes. Refuses a non-finite or non-numeric price
    and an empty or unordered axis BEFORE anything is written."""
    if isinstance(eur_per_mwh, bool) or not isinstance(eur_per_mwh, (int, float, np.number)):
        raise TypeError(f"eur_per_mwh must be a number, got {type(eur_per_mwh).__name__}")
    price = float(eur_per_mwh)
    if not math.isfinite(price):
        raise ValueError(f"eur_per_mwh must be finite, got {eur_per_mwh!r}")
    idx = _axis(snapshots)
    series = pd.Series(np.full(len(idx), price), index=idx, name=name)
    meta = {"source": source, "vintage_year": vintage_year, "unit": "EUR/MWh",
            "label": f"Flat export price {price:g} EUR/MWh",
            "description": description, "notes": [FLAT_EXPORT_NOTE]}
    return series_store.put_series(db, org_id, name, series, meta, created_by=created_by,
                                   root=root)
