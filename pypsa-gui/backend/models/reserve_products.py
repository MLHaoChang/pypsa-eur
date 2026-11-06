"""Reserve products a backup storage may commit headroom to (DC grid readiness 06).

Generic product shapes only (SAGC-1: no market names, no shipped prices):
``fast_symmetric`` is an FCR-style symmetric product, ``secondary`` and
``tertiary`` are aFRR- and mFRR-style directional ones. The capacity price is a
user Library series (per MW per hour committed, in the project's currency);
nothing market-specific ships with the app.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from models.commercial import PriceSeriesRef

ReserveKind = Literal["fast_symmetric", "secondary", "tertiary"]
ReserveDirection = Literal["symmetric", "up", "down"]


class ReserveProduct(BaseModel):
    """One reserve product the eligible backup storage may commit to.

    ``sustain_h`` is how long each committed MW must be deliverable (the
    energy it reserves is MW × ``sustain_h``); there is no default, the user
    states it. ``storages`` empty means every eligible storage."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: ReserveKind
    direction: ReserveDirection
    sustain_h: float = Field(gt=0, le=24, allow_inf_nan=False)
    capacity_price: PriceSeriesRef
    max_commit_mw: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    storages: list[str] = Field(default_factory=list)
    counterparty: str = Field(default="tso", min_length=1)

    @model_validator(mode="after")
    def _direction_fits_kind(self) -> "ReserveProduct":
        if self.kind == "fast_symmetric" and self.direction != "symmetric":
            raise ValueError("reserve product: a fast_symmetric product is symmetric")
        if self.kind != "fast_symmetric" and self.direction == "symmetric":
            raise ValueError(f"reserve product: a {self.kind} product is up or down, not symmetric")
        if len(set(self.storages)) != len(self.storages):
            raise ValueError("reserve product: a storage is named twice")
        return self

    @property
    def up(self) -> bool:
        return self.direction in ("symmetric", "up")

    @property
    def down(self) -> bool:
        return self.direction in ("symmetric", "down")


def check_unique(products: list[ReserveProduct]) -> list[ReserveProduct]:
    """Product ids are unique within a config."""
    ids = [p.id for p in products]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"reserve products: id(s) {dup} appear more than once")
    return products
