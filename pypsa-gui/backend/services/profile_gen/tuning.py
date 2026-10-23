"""Preset-backed tuning metadata, validation and post-source power losses."""

from __future__ import annotations

import copy
import math
from numbers import Real
from typing import Any
from collections.abc import Mapping

import pandas as pd

from services.profile_gen.engine import ProfileGenError, UnsupportedTechnologyError


class InvalidTuningError(ProfileGenError):
    """A tuning value has the wrong type or lies outside its stated bounds."""


# The path is shared by schema lookup and the native-parameter mapping.
_FIELDS = {
    "wind": (
        dict(
            key="hub_height",
            label="Hub height",
            unit="m",
            type="number",
            min=20,
            max=200,
            path=["turbine", "hub_height"],
            sources=["atlite"],
        ),
        dict(
            key="cut_out",
            label="Cut-out speed",
            unit="m/s",
            type="number",
            min=15,
            max=35,
            path=["turbine", "V"],
            sources=["atlite"],
        ),
        dict(
            key="derate",
            label="Power derate",
            unit="fraction",
            type="number",
            min=0,
            max=0.5,
            path=["derate"],
            sources=["synthetic", "atlite"],
        ),
        dict(
            key="losses",
            label="Losses",
            unit="fraction",
            type="number",
            min=0,
            max=0.5,
            path=["losses"],
            sources=["synthetic", "atlite"],
        ),
    ),
    "solar": (
        dict(
            key="tilt",
            label="Tilt",
            unit="°",
            type="number",
            min=0,
            max=90,
            path=["orientation", "slope"],
            sources=["atlite"],
        ),
        dict(
            key="azimuth",
            label="Azimuth",
            unit="°",
            type="number",
            min=0,
            max=360,
            path=["orientation", "azimuth"],
            sources=["atlite"],
        ),
        dict(
            key="tracking",
            label="Tracking",
            unit=None,
            type="string",
            options=["none", "horizontal"],
            path=["tracking"],
            sources=["atlite"],
        ),
        dict(
            key="inverter_efficiency",
            label="Inverter efficiency",
            unit="fraction",
            type="number",
            min=0.8,
            max=1.0,
            path=["panel", "inverter_efficiency"],
            sources=["atlite"],
        ),
        dict(
            key="losses",
            label="Losses",
            unit="fraction",
            type="number",
            min=0,
            max=0.5,
            path=["losses"],
            sources=["synthetic", "atlite"],
        ),
    ),
}


def _fields(technology):
    try:
        return _FIELDS[technology]
    except KeyError as exc:
        raise UnsupportedTechnologyError(
            f"unsupported tuning technology {technology!r}; choose wind or solar"
        ) from exc


def _cut_out(turbine):
    return max(v for v, power in zip(turbine["V"], turbine["POW"]) if power > 0)


def schema(technology, preset):
    """A fresh list of fields, derived from the selected shipped preset."""
    fields = copy.deepcopy(_fields(technology))
    for field in fields:
        key, path = field["key"], field["path"]
        value = preset.params
        for part in path:
            value = value.get(part) if isinstance(value, Mapping) else None
        if key == "cut_out" and preset.params.get("turbine"):
            value = _cut_out(preset.params["turbine"])
        if value is None and key in ("losses", "derate"):
            value, provenance = 0, "tool default: none applied"
        elif value is None:
            provenance = "not used by the synthetic source"
        else:
            provenance = preset.provenance[path[0]]
        field.update(preset_value=value, provenance=provenance)
    return fields


def validate(technology: str, params: Mapping[str, Any]) -> None:
    """Validate only this technology's tuning fields; other overrides survive."""
    for field in _fields(technology):
        key = field["key"]
        if key not in params:
            continue
        value = params[key]
        if field["type"] == "string":
            valid = isinstance(value, str) and value in field["options"]
            bounds = f"one of {field['options']}"
        else:
            valid = (
                isinstance(value, Real)
                and not isinstance(value, bool)
                and math.isfinite(value)
                and field["min"] <= value <= field["max"]
            )
            bounds = f"a finite number in [{field['min']}, {field['max']}]"
        if not valid:
            raise InvalidTuningError(f"{key}={value!r}: expected {bounds}")


def apply_to_params(
    technology: str, params: Mapping[str, Any], *, keys=None
) -> dict[str, Any]:
    """Map flat settings onto fresh embedded atlite configs without mutation."""
    out = copy.deepcopy(dict(params))
    selected = set(params) if keys is None else set(keys)
    tuned = {
        f["key"]: out[f["key"]]
        for f in _fields(technology)
        if f["key"] in out and f["key"] in selected
    }
    if not tuned:
        return out
    ignored = []
    for field in _fields(technology):
        key, path = field["key"], field["path"]
        if key not in tuned or "synthetic" in field["sources"]:
            continue
        if not out.get("turbine" if technology == "wind" else "panel"):
            ignored.append(key)
            continue
        if key == "cut_out":
            turbine = out["turbine"]
            cut, original = tuned[key], _cut_out(turbine)
            pairs = [
                (v, p)
                for v, p in zip(turbine["V"], turbine["POW"])
                if v < cut and v <= original
            ]
            if not pairs:
                raise InvalidTuningError(
                    f"cut_out={cut!r}: turbine curve has no point below the requested cut-out"
                )
            pairs += [(cut, pairs[-1][1]), (cut + 0.01, 0.0)]
            turbine["V"], turbine["POW"] = map(list, zip(*pairs))
        elif len(path) == 1:
            out[key] = (
                None if key == "tracking" and tuned[key] == "none" else tuned[key]
            )
        else:
            out[path[0]][path[1]] = tuned[key]
    out["tuning"] = tuned
    if ignored:
        out["tuning_not_applied"] = ignored
    return out


def ignored_by_source(technology, tuning, source):
    return [
        f["key"]
        for f in _fields(technology)
        if f["key"] in tuning and source not in f["sources"]
    ]


def post_source(
    frame: pd.DataFrame, technology: str, params: Mapping[str, Any]
) -> pd.DataFrame:
    """Apply the power cap, then losses, before temporal aggregation."""
    return frame.clip(upper=1.0 - params.get("derate", 0.0)) * (
        1.0 - params.get("losses", 0.0)
    )
