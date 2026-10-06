"""Grid-code profiles: the connection-point limits, each with its clause and a
provenance tag (increment 10).

A profile is a small table of limits (steady-state voltage bands by nominal
voltage, the reactive range a TSO may require, the rapid-voltage-change
limit). Every limit carries the clause it comes from and one of two tags:
``code`` for a number the regulation states, and ``assumed`` for an
engineering choice the code leaves open. The same rule as the unit templates
applies: a report must be able to say which rule it applied and how sure that
rule is, so a limit without a clause or tag is refused at load, not defaulted.

A profile may carry one optional key, ``campus_voltage``: a design band
(``v_min``, ``v_max``, clause, tag) for the buses inside the campus. Without
it a study holds those buses to the ``voltage_bands``.

yaml/pandas only, like the rest of ``templates/``; never the unsafe full
``yaml.load``.
"""
import copy
import math
from pathlib import Path

import yaml

from gridspine.schema.contracts import ContractError

_DEFAULT = Path(__file__).parent / "data" / "grid_codes.yaml"
SOURCES = frozenset({"code", "assumed"})
_REQUIRED = ("title", "voltage_bands", "q_range_demand", "rvc_limit_pct")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_limit(name, lim, where):
    if not isinstance(lim, dict):
        raise ContractError(f"{where}: {name} must be a mapping, got {type(lim).__name__}")
    for key in ("clause", "source"):
        if not lim.get(key):
            raise ContractError(f"{where}: {name} has no {key}; every limit states its clause and tag")
    if lim["source"] not in SOURCES:
        raise ContractError(
            f"{where}: {name} has unknown source {lim['source']!r}; allowed {sorted(SOURCES)}"
        )


def _check_profile(name, p):
    where = f"grid-code profile {name!r}"
    missing = [k for k in _REQUIRED if k not in p]
    if missing:
        raise ContractError(f"{where} is missing {missing}")
    for key in ("q_range_demand", "rvc_limit_pct"):
        _check_limit(key, p[key], where)
        if not _is_number(p[key].get("value")) or p[key]["value"] <= 0:
            raise ContractError(f"{where}: {key} value must be a positive number, got {p[key].get('value')!r}")
    bands = p["voltage_bands"]
    if not isinstance(bands, list) or not bands:
        raise ContractError(f"{where}: voltage_bands must be a non-empty list")
    for i, b in enumerate(bands):
        _check_limit(f"voltage_bands[{i}]", b, where)
        for key in ("kv_min", "kv_max", "v_min", "v_max"):
            if not _is_number(b.get(key)):
                raise ContractError(f"{where}: voltage_bands[{i}].{key} must be a number")
        if not (0 < b["v_min"] < 1 < b["v_max"]):
            raise ContractError(
                f"{where}: voltage_bands[{i}] needs v_min < 1 < v_max, got v_min={b['v_min']} v_max={b['v_max']}"
            )
        if not b["kv_min"] < b["kv_max"]:
            raise ContractError(f"{where}: voltage_bands[{i}] kv_min must be below kv_max")
    if "campus_voltage" in p:
        _check_campus_voltage(p["campus_voltage"], where)
    ordered = sorted(bands, key=lambda b: b["kv_min"])
    for a, b in zip(ordered, ordered[1:]):
        if b["kv_min"] < a["kv_max"]:
            raise ContractError(
                f"{where}: voltage bands overlap ({a['kv_min']}-{a['kv_max']} kV and "
                f"{b['kv_min']}-{b['kv_max']} kV)"
            )


def _check_campus_voltage(cv, where):
    _check_limit("campus_voltage", cv, where)
    for key in ("v_min", "v_max"):
        if not _is_number(cv.get(key)):
            raise ContractError(f"{where}: campus_voltage.{key} must be a number")
    if not 0 < cv["v_min"] < cv["v_max"]:
        raise ContractError(
            f"{where}: campus_voltage needs 0 < v_min < v_max, got v_min={cv['v_min']} v_max={cv['v_max']}"
        )


def load_grid_code(name: str, path=None, raw: bool = False) -> dict:
    """One profile, validated. ``raw`` returns an independent copy of the
    mapping exactly as written, which tests use to build broken variants."""
    data = yaml.safe_load(Path(path or _DEFAULT).read_text())
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict) or name not in profiles:
        raise ContractError(
            f"unknown grid-code profile {name!r}; known {sorted(profiles or {})}"
        )
    profile = copy.deepcopy(profiles[name])
    if raw:
        return profile
    _check_profile(name, profile)
    profile["name"] = name
    return profile


def band_for(profile: dict, kv: float) -> dict:
    """The steady-state voltage band for a bus of nominal voltage ``kv``."""
    for b in profile["voltage_bands"]:
        upper_ok = kv <= b["kv_max"] if b.get("kv_max_inclusive") else kv < b["kv_max"]
        if b["kv_min"] <= kv and upper_ok:
            return b
    raise ContractError(
        f"grid-code profile {profile.get('name', '?')!r} sets no voltage band for {kv:g} kV"
    )


def list_grid_codes(path=None) -> dict:
    """``{profile name: title}`` of every profile in the file. This is what
    a study chooses from."""
    data = yaml.safe_load(Path(path or _DEFAULT).read_text())
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        raise ContractError("the grid-code file has no 'profiles' mapping")
    return {name: str(p.get("title", name)) for name, p in profiles.items()}
