"""
The AI-training load profile and its dynamic envelope (DC grid readiness 01).

Two outputs from one `DataCentreLoadSpec` with an `AiWorkloadSpec`:

* ``hourly_profile``: the facility load per snapshot for PyPSA,
  IT MW × u(t) × PUE(T(t)). u(t) is the hourly mean utilisation (the full
  step less the checkpoint dips' duty, the idle hours at the idle share); the
  oscillation has zero mean and stays out. PUE(T) = ``pue_at_design`` +
  slope × max(0, T − ``pue_design_temp_c``); UPS and distribution losses are
  inside PUE. The ambient T is keyed to the snapshot dates.
* ``dynamic_envelope``: per phase, the sub-hourly behaviour the LP never sees
  (max ΔP/Δt, swing, checkpoint dip, oscillation band). Every value is
  IT-side at the POC, before any UPS/BESS smoothing (grid readiness 03 and 04
  apply that), without the cooling overhead. Grid readiness 02 and gridspine
  read it back with ``read_envelope``.

Time basis (the profile_gen rule, `profile_gen.apply.resolve_time_basis`):
with ``commercial.timezone`` set the naive snapshots are UTC and are shifted
to that zone's wall clock before the diurnal terms map; otherwise they are the
site clock, recorded as assumed.

Every default is assumed; ``SOURCES`` says where its order of magnitude comes
from. Nothing here is a measured or vendor figure.
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd

from models.flex_archetypes import AiWorkloadSpec, DataCentreLoadSpec, DcPhase

#: The key in a project's ``eh_template.json`` that carries the spec, the
#: envelope and their ledger.
META_KEY = "dc_load"
ENVELOPE_SCHEMA = 1

_LLAMA3 = ("Meta, 'The Llama 3 Herd of Models' (2024), §3.3.4: synchronised "
           "power swings of tens of MW across a training cluster")
_STABILISATION = ("Choukse et al., 'Power Stabilization for AI Training Datacenters', "
                  "arXiv:2508.14318 (2025): job-synchronised swings, checkpoint dips, "
                  "oscillation in the sub-hertz to hertz range")
_NERC = ("NERC, 'Characteristics and Risks of Emerging Large Loads' white paper "
         "(2025): large-load ramps and oscillations as a grid-reliability concern")

#: Where each assumed default's order of magnitude comes from.
SOURCES: dict[str, str] = {
    "full_share": f"assumed; {_STABILISATION}",
    "idle_share": f"assumed; {_STABILISATION}",
    "ramp_s": f"assumed; {_LLAMA3}",
    "checkpoint_interval_s": f"assumed; {_LLAMA3}",
    "checkpoint_duration_s": f"assumed; {_LLAMA3}",
    "oscillation_amplitude_share": f"assumed; {_STABILISATION}",
    "oscillation_period_s": f"assumed; {_STABILISATION}; {_NERC}",
    "idle_hours_per_day": "assumed; illustrative daily restart/maintenance window",
    "pue": "assumed; PUE at or below the design temperature, slope above it",
    "ambient": "assumed; temperate (Central European) site, no weather data",
    "rack_kw": "assumed or as entered; carried into the envelope only",
}


class DcLoadError(ValueError):
    """A spec the template cannot apply. ``field`` names the input."""

    def __init__(self, message: str, field: str):
        super().__init__(message)
        self.field = field


def _local_index(snapshots: pd.DatetimeIndex, timezone: str | None) -> pd.DatetimeIndex:
    if timezone:
        return snapshots.tz_localize("UTC").tz_convert(timezone).tz_localize(None)
    return snapshots


def time_basis(timezone: str | None) -> str:
    return (f"UTC snapshots, mapped on the {timezone} clock (commercial.timezone)"
            if timezone else "site clock (assumed)")


def ambient_c(spec: DataCentreLoadSpec, snapshots, timezone: str | None = None) -> np.ndarray:
    """The assumed ambient temperature (°C) on each snapshot."""
    t = _local_index(pd.DatetimeIndex(snapshots), timezone)
    hod = t.hour.to_numpy() + t.minute.to_numpy() / 60.0
    doy = t.dayofyear.to_numpy()
    return (spec.ambient_annual_mean_c
            + spec.ambient_seasonal_amplitude_k * np.cos(2 * np.pi * (doy - 200) / 365.0)
            + spec.ambient_diurnal_amplitude_k * np.cos(2 * np.pi * (hod - 15.0) / 24.0))


def pue(spec: DataCentreLoadSpec, temp_c: np.ndarray) -> np.ndarray:
    excess = np.maximum(0.0, np.asarray(temp_c, dtype=float) - spec.pue_design_temp_c)
    return spec.pue_at_design + spec.pue_temperature_slope_per_k * excess


def workload(spec: DataCentreLoadSpec) -> AiWorkloadSpec:
    return spec.ai_workload or AiWorkloadSpec()


def training_utilisation(w: AiWorkloadSpec) -> float:
    """The hourly mean while training: the full step less the checkpoint duty."""
    duty = w.checkpoint_duration_s / w.checkpoint_interval_s
    return w.full_share - (w.full_share - w.idle_share) * duty


def utilisation(spec: DataCentreLoadSpec, snapshots, timezone: str | None = None) -> np.ndarray:
    """u(t), the hourly mean share of IT MW."""
    w = workload(spec)
    hod = _local_index(pd.DatetimeIndex(snapshots), timezone).hour.to_numpy()
    idle = ((hod - w.idle_start_hour) % 24) < w.idle_hours_per_day
    return np.where(idle, w.idle_share, training_utilisation(w))


def hourly_profile(spec: DataCentreLoadSpec, snapshots, it_mw: float,
                   timezone: str | None = None) -> np.ndarray:
    """The facility load (MW) on each snapshot: IT MW × u(t) × PUE(T(t))."""
    u = utilisation(spec, snapshots, timezone)
    return it_mw * u * pue(spec, ambient_c(spec, snapshots, timezone))


def _phase_envelope(w: AiWorkloadSpec, phase: DcPhase) -> dict:
    it = phase.it_mw
    swing = it * (w.full_share - w.idle_share)
    amp = it * w.oscillation_amplitude_share
    return {
        "cod": phase.cod.isoformat(),
        "it_mw": it,
        "rack_kw": phase.rack_kw,
        "rack_count": (None if phase.rack_kw is None
                       else int(np.ceil(it * 1000.0 / phase.rack_kw))),
        "swing_mw": swing,
        "max_dp_dt_mw_per_s": swing / w.ramp_s,
        "max_dp_dt_mw_per_min": swing / w.ramp_s * 60.0,
        "checkpoint": {
            "interval_s": w.checkpoint_interval_s,
            "duration_s": w.checkpoint_duration_s,
            "depth_mw": swing if w.checkpoint_duration_s > 0 else 0.0,
            "dp_dt_mw_per_s": (swing / w.ramp_s) if w.checkpoint_duration_s > 0 else 0.0,
        },
        "oscillation": {
            "amplitude_mw": amp,
            "peak_to_peak_mw": 2.0 * amp,
            "period_s": w.oscillation_period_s,
            "frequency_hz": 1.0 / w.oscillation_period_s,
        },
    }


def dynamic_envelope(spec: DataCentreLoadSpec) -> dict:
    """The per-phase dynamic envelope and its maximum over the phases."""
    w = workload(spec)
    phases = [_phase_envelope(w, p) for p in spec.phases]
    worst = max(phases, key=lambda p: p["swing_mw"])
    return {
        "schema": ENVELOPE_SCHEMA,
        "basis": ("IT side at the POC, before any UPS/BESS smoothing; cooling "
                  "overhead excluded; not used in the LP"),
        "units": {"swing_mw": "MW", "max_dp_dt_mw_per_s": "MW/s",
                  "max_dp_dt_mw_per_min": "MW/min", "amplitude_mw": "MW (peak)",
                  "peak_to_peak_mw": "MW", "period_s": "s", "frequency_hz": "Hz",
                  "rack_kw": "kW per rack"},
        "source": "assumed",
        "phases": phases,
        "max": {
            "swing_mw": worst["swing_mw"],
            "max_dp_dt_mw_per_s": max(p["max_dp_dt_mw_per_s"] for p in phases),
            "oscillation_amplitude_mw": max(p["oscillation"]["amplitude_mw"] for p in phases),
            "oscillation_period_s": w.oscillation_period_s,
        },
        "sources": SOURCES,
    }


def ledger(spec: DataCentreLoadSpec, timezone: str | None) -> list[dict]:
    """Ledger rows for every assumed input the profile and envelope read."""
    w = workload(spec)
    rows = [
        ("AI workload steps",
         f"full {w.full_share:g}, idle {w.idle_share:g} of IT MW; ramp {w.ramp_s:g} s",
         "full_share"),
        ("checkpoints",
         f"every {w.checkpoint_interval_s:g} s for {w.checkpoint_duration_s:g} s, down to idle",
         "checkpoint_interval_s"),
        ("oscillation",
         f"peak amplitude {w.oscillation_amplitude_share:g} of IT MW at a "
         f"{w.oscillation_period_s:g} s period; envelope only (zero mean)",
         "oscillation_period_s"),
        ("idle hours",
         f"{w.idle_hours_per_day} h/day from {w.idle_start_hour:02d}:00 ({time_basis(timezone)})",
         "idle_hours_per_day"),
        ("PUE",
         f"{spec.pue_at_design:g} at or below {spec.pue_design_temp_c:g} °C, "
         f"+{spec.pue_temperature_slope_per_k:g} per K above; UPS and distribution losses "
         "inside PUE", "pue"),
        ("ambient temperature",
         f"{spec.ambient_annual_mean_c:g} °C mean, ±{spec.ambient_seasonal_amplitude_k:g} K "
         f"seasonal (peak day 200), ±{spec.ambient_diurnal_amplitude_k:g} K diurnal (peak "
         f"15:00); keyed to the snapshot dates on the {time_basis(timezone)}", "ambient"),
        ("load split",
         f"critical share {spec.critical_share:g} of the facility load on it_load (critical "
         f"it_bus), the rest on cooling; curtailable share {spec.curtailable_share:g} recorded, "
         "not modelled (no curtailable load in the template)", "pue"),
        ("rack density", ", ".join(
            f"{p.cod.year}: {p.rack_kw:g} kW/rack" if p.rack_kw else f"{p.cod.year}: not set"
            for p in spec.phases), "rack_kw"),
    ]
    return [{"item": item, "value": value, "source": "assumed", "note": SOURCES[key]}
            for item, value, key in rows]


def meta_block(spec: DataCentreLoadSpec, timezone: str | None) -> dict:
    """What a project's ``eh_template.json`` carries under ``META_KEY``."""
    return {"spec": spec.model_dump(mode="json"), "time_basis": time_basis(timezone),
            "envelope": dynamic_envelope(spec), "ledger": ledger(spec, timezone)}


def read_envelope(project_dir) -> dict | None:
    """A project's stored dynamic envelope, or None (no opt-in, no file)."""
    path = pathlib.Path(project_dir) / "eh_template.json"
    try:
        meta = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    block = meta.get(META_KEY) if isinstance(meta, dict) else None
    return block.get("envelope") if isinstance(block, dict) else None
