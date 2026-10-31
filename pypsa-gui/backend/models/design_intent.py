"""
Asset sizing loop AS-01 — design-intent presets A-E.

One intent per scenario, as metadata beside the DC architecture. Data model
only; the LP is untouched. Hard constraints become technology bounds
(services/design_intent.py); soft ones are a ranking in the comparison.
Spec: .scratch/asset-sizing-loop/spec.md. Limits here are assumed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

DesignIntent = Literal["A", "B", "C", "D", "E"]
DESIGN_INTENTS: tuple[str, ...] = ("A", "B", "C", "D", "E")

#: technologies offered, which of them are extendable, services that become constraints.
_PRESETS: dict[str, dict] = {
    "A": {"label": "Replace diesel",
          "technologies": ("energy_bess_gfm", "fast_tier", "grid"),
          "extendable": {"energy_bess_gfm", "fast_tier"},
          "services": ("backup_autonomy", "ride_through_no_transfer", "smoothing", "gfm_mva")},
    "B": {"label": "Keep diesel + MV smoothing BESS",
          "technologies": ("genset", "mv_bess_high_c", "lv_ups", "grid"),
          "extendable": {"mv_bess_high_c"},
          "services": ("smoothing", "ramp", "genset_autonomy", "ups_bridge")},
    "C": {"label": "Keep diesel, no new BESS",
          "technologies": ("genset", "lv_ups", "gpu_power_floor", "statcom", "grid"),
          "extendable": {"statcom"},
          "services": ("ramp_by_gpu_floor", "flicker_by_statcom", "residual_shown")},
    "D": {"label": "No UPS (BESS-EPS in parallel)",
          "technologies": ("bess_gfm", "genset", "grid"),
          "extendable": {"bess_gfm"},
          "services": ("bridge_reserve", "seamless_dp_headroom", "grid_code_warning")},
    "E": {"label": "MV UPS (series)",
          "technologies": ("mv_ups_storage", "genset", "grid"),
          "extendable": {"mv_ups_storage"},
          "services": ("rectifier_ramp_is_grid_ramp", "ups_ride_through", "q_from_mv_ups")},
}


def preset(intent: str) -> dict:
    """The preset table row for ``intent``; KeyError for an unknown one."""
    return _PRESETS[intent]


class DesignIntentSpec(BaseModel):
    intent: DesignIntent
    #: Intent A backup energy = critical load x this many hours (AS-AUTONOMY-1).
    backup_autonomy_h: float = Field(default=4.0, gt=0)
    #: Intents B/C: annual run-hour cap on the genset; None = uncapped.
    genset_run_hours_cap: float | None = Field(default=None, ge=0, le=8760)
    #: Intent D: bridge reserve energy = critical load x this many hours.
    bridge_reserve_h: float = Field(default=0.25, gt=0)

    @model_validator(mode="after")
    def _known(self):
        preset(self.intent)
        if self.genset_run_hours_cap is not None and self.intent not in ("B", "C"):
            raise ValueError("genset_run_hours_cap applies to intents B and C only")
        return self
