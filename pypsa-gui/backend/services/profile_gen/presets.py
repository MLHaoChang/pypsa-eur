"""Profile presets (profile_gen Task 4).

Spec §3.3: `profile_presets.yaml`, per-field provenance (gridspine templates
v2 discipline), validated at LOAD time. Turbine/panel configs are embedded
dicts — never atlite library names — so presets resolve offline by
construction and can never trigger a runtime OEDB download.

Lookup is by (name, technology): `default` exists once per technology, so
bare-name lookup of an ambiguous name is a typed refusal asking for the
technology.
"""
from __future__ import annotations

import copy
import pathlib
from dataclasses import dataclass
from typing import Any, Mapping

import yaml

from services.profile_gen.engine import (
    SUPPORTED_TECHNOLOGIES,
    ProfileGenError,
)

_DEFAULT_PATH = pathlib.Path(__file__).resolve().parent / "data" / "profile_presets.yaml"
_SOURCE_TYPES = ("synthetic", "atlite")
_REQUIRED_KEYS = ("name", "technology", "source_type", "params", "provenance")
_TURBINE_KEYS = ("V", "POW", "hub_height", "P")


class PresetError(ProfileGenError):
    """Base for preset failures."""


class InvalidPresetError(PresetError):
    """The preset file failed load-time validation."""


class UnknownPresetError(PresetError):
    """No preset (or an ambiguous bare name) for the requested lookup."""


@dataclass(frozen=True)
class ProfilePreset:
    name: str
    technology: str
    source_type: str
    params: dict[str, Any]
    provenance: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        """Router/chat-facing shape: everything except the bulky params body."""
        return {
            "name": self.name,
            "technology": self.technology,
            "source_type": self.source_type,
            "params_fields": sorted(self.params),
            "provenance": self.provenance,
        }


# ---------------------------------------------------------------------------
# Load + validate
# ---------------------------------------------------------------------------


def _fail(where: str, msg: str) -> None:
    raise InvalidPresetError(f"{where}: {msg}")


def _validate_entry(raw: Any, index: int) -> ProfilePreset:
    where = f"preset #{index} ({(raw or {}).get('name', '?')!r})"
    if not isinstance(raw, Mapping):
        _fail(where, "entry must be a mapping")
    for key in _REQUIRED_KEYS:
        if key not in raw:
            _fail(where, f"missing required key {key!r}")
    name, tech = str(raw["name"]), str(raw["technology"])
    source_type = str(raw["source_type"])
    if tech not in SUPPORTED_TECHNOLOGIES:
        _fail(where, f"unknown technology {tech!r}; "
                     f"supported: {', '.join(SUPPORTED_TECHNOLOGIES)}")
    if source_type not in _SOURCE_TYPES:
        _fail(where, f"unknown source_type {source_type!r}; "
                     f"supported: {', '.join(_SOURCE_TYPES)}")
    params, provenance = raw["params"], raw["provenance"]
    if not isinstance(params, Mapping) or not isinstance(provenance, Mapping):
        _fail(where, "params and provenance must be mappings")

    # Per-field provenance is mandatory — never silently filled (mutation
    # target: filling it here turns the refusal test red).
    missing_prov = [k for k in params if k not in provenance]
    if missing_prov:
        _fail(where, f"params field(s) {', '.join(map(repr, missing_prov))} "
                     "lack a provenance entry")

    if source_type == "atlite":
        if tech == "wind":
            t = params.get("turbine")
            if not isinstance(t, Mapping) or not set(_TURBINE_KEYS) <= set(t):
                _fail(where, "atlite wind presets need an embedded turbine "
                             "dict with V/POW/hub_height/P — atlite library "
                             "names are refused (offline by construction)")
            if len(t["V"]) != len(t["POW"]):
                _fail(where, "turbine V and POW must have equal length")
        else:
            if not isinstance(params.get("panel"), Mapping):
                _fail(where, "atlite solar presets need an embedded panel dict")
            if not isinstance(params.get("orientation"), Mapping):
                _fail(where, "atlite solar presets need an orientation dict")

    return ProfilePreset(
        name=name,
        technology=tech,
        source_type=source_type,
        params=copy.deepcopy(dict(params)),
        provenance=copy.deepcopy(dict(provenance)),
    )


def load_presets(path: str | pathlib.Path) -> dict[tuple[str, str], ProfilePreset]:
    """Load and validate a preset file. Validation failures are typed
    refusals at LOAD time — a bad preset never reaches the engine."""
    raw = yaml.safe_load(pathlib.Path(path).read_text())
    if not isinstance(raw, list):
        raise InvalidPresetError(f"{path}: top level must be a list of presets")
    out: dict[tuple[str, str], ProfilePreset] = {}
    for i, entry in enumerate(raw):
        p = _validate_entry(entry, i)
        key = (p.name, p.technology)
        if key in out:
            raise InvalidPresetError(
                f"{path}: duplicate preset for name={p.name!r} "
                f"technology={p.technology!r}"
            )
        out[key] = p
    return out


_default_cache: dict[tuple[str, str], ProfilePreset] | None = None


def _shipped() -> dict[tuple[str, str], ProfilePreset]:
    global _default_cache
    if _default_cache is None:
        _default_cache = load_presets(_DEFAULT_PATH)
    return _default_cache


def reset_presets_cache_for_tests() -> None:
    global _default_cache
    _default_cache = None


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def get_preset(name: str, technology: str | None = None) -> ProfilePreset:
    presets = _shipped()
    if technology is not None:
        key = (name, technology)
        if key in presets:
            return presets[key]
        available = sorted(n for (n, t) in presets if t == technology)
        raise UnknownPresetError(
            f"unknown {technology} preset {name!r}; available: "
            f"{', '.join(available)}"
        )
    matches = [p for (n, _), p in presets.items() if n == name]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise UnknownPresetError(
            f"preset {name!r} exists for several technologies "
            f"({', '.join(sorted(p.technology for p in matches))}) — "
            "pass technology= to disambiguate"
        )
    available = sorted({n for (n, _) in presets})
    raise UnknownPresetError(
        f"unknown preset {name!r}; available: {', '.join(available)}"
    )


def list_presets(technology: str | None = None) -> list[ProfilePreset]:
    presets = sorted(_shipped().values(), key=lambda p: (p.technology, p.name))
    if technology is None:
        return presets
    return [p for p in presets if p.technology == technology]


def resolve_params(name: str, technology: str | None = None,
                   overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A fresh params dict for the engine: preset params + `preset` marker,
    with user overrides applied. The shipped preset is never mutated."""
    p = get_preset(name, technology)
    params = copy.deepcopy(p.params)
    for k, v in (overrides or {}).items():
        params[k] = v
    params["preset"] = p.name
    return params
