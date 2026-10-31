"""AS-03 site-wide envelope floors, the `SolverConfig.envelope_floors` field.

The rows themselves are `services/solver/envelope_floors.py`. A None value adds no row; values are assumed
engineering inputs (SAGC-1).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EnvelopeFloors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Sum of the fast-tier smoothing power, MW (S + A of the envelope).
    power_floor_mw: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    #: Sum of the fast-tier smoothing energy, usable MWh (summed event energies).
    energy_floor_mwh: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    #: Backup reserve the backup_reserve stores hold through their dischargers, AC MWh.
    backup_mwh: float | None = Field(default=None, ge=0, allow_inf_nan=False)
