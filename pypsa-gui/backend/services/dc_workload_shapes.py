"""DC 05: the hourly mean utilisation u(t) of each workload kind.

``ai_training`` is the series ``dc_load_profile`` already builds, passed in and
returned untouched. ``inference`` is a diurnal request load, a cosine between a
trough and a peak share peaking at ``peak_hour`` on the site clock. ``mixed`` is
the ``training_share`` blend of the two; ``flat`` is a constant. Every
parameter is assumed."""
from __future__ import annotations

import numpy as np

from models.dc_compute_spec import WorkloadMixSpec


def inference_utilisation(hod: np.ndarray, mix: WorkloadMixSpec) -> np.ndarray:
    swing = mix.peak_share - mix.trough_share
    phase = 2.0 * np.pi * (np.asarray(hod, dtype=float) - mix.peak_hour) / 24.0
    return mix.trough_share + swing * 0.5 * (1.0 + np.cos(phase))


def shape_utilisation(kind: str, hod: np.ndarray, *, training: np.ndarray,
                      mix: WorkloadMixSpec) -> np.ndarray:
    """u(t) for ``kind``; ``training`` is the ai_training series on the same snapshots."""
    if kind == "ai_training":
        return training
    if kind == "inference":
        return inference_utilisation(hod, mix)
    if kind == "mixed":
        return mix.training_share * training + (1.0 - mix.training_share) * inference_utilisation(hod, mix)
    if kind == "flat":
        return np.full(len(np.asarray(hod)), mix.flat_share)
    raise ValueError(f"unknown utilisation kind {kind!r}")
