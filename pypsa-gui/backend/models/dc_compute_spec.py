"""DC 05: the compute layer's inputs, kept apart from the load spec they extend.

``WorkloadMixSpec`` holds the assumed shape parameters of the non-training
workload kinds; ``ComputeSpec`` is the optional compute block that derives IT MW
per phase. Neither feeds the LP unless the load spec says so: the compute block
is reported next to the typed IT MW and the typed value always wins."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.schemas import Finite

_FORBID = ConfigDict(extra="forbid", allow_inf_nan=False)


class WorkloadMixSpec(BaseModel):
    """Shares of the phase's IT MW for the inference, mixed and flat kinds.
    Every default is assumed (``services.dc_load_profile.SOURCES``)."""
    model_config = _FORBID
    #: Inference request load: lowest and highest hourly share, peak hour (site clock).
    trough_share: Finite = Field(default=0.40, ge=0, le=1)
    peak_share: Finite = Field(default=0.85, ge=0, le=1)
    peak_hour: int = Field(default=20, ge=0, le=23, strict=True)
    #: Mixed: share of the load that is training (the rest is inference).
    training_share: Finite = Field(default=0.50, ge=0, le=1)
    #: Flat: constant share.
    flat_share: Finite = Field(default=0.80, ge=0, le=1)

    @model_validator(mode="after")
    def _trough_below_peak(self) -> "WorkloadMixSpec":
        if self.trough_share > self.peak_share:
            raise ValueError("trough_share must not exceed peak_share")
        return self


class ComputeSpec(BaseModel):
    """Accelerators × kW per accelerator (host share included) × utilisation,
    one accelerator count per phase (cumulative, as ``DcPhase.it_mw`` is)."""
    model_config = _FORBID
    kw_per_accelerator: Finite = Field(gt=0)
    utilisation: Finite = Field(gt=0, le=1)
    accelerator_counts: list[Annotated[int, Field(gt=0)]] = Field(min_length=1)
