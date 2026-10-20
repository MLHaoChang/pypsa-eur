"""Renewable profile generator: coordinate set + parameters -> per-unit
capacity-factor time series at hourly/daily/weekly resolution (spec:
docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md).

Public surface kept small on purpose — router, chat tools, and the queue job
all go through `generate_profile` / these data shapes.
"""
from services.profile_gen.engine import (
    InvalidCoordinatesError,
    ProfileGenError,
    ProfilePoint,
    ProfileResult,
    UnknownSourceError,
    UnsupportedResolutionError,
    UnsupportedTechnologyError,
    RESOLUTIONS,
    SUPPORTED_TECHNOLOGIES,
    generate_profile,
    register_source,
)

__all__ = [
    "InvalidCoordinatesError",
    "ProfileGenError",
    "ProfilePoint",
    "ProfileResult",
    "UnknownSourceError",
    "UnsupportedResolutionError",
    "UnsupportedTechnologyError",
    "RESOLUTIONS",
    "SUPPORTED_TECHNOLOGIES",
    "generate_profile",
    "register_source",
]
