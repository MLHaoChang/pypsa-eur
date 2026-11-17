"""
A versioned model set with a data freeze (GC-01, TRGC 01/02).

Every study a grid-compliance pack exports (TRGC 01/02 onwards) must say
which data its plant model was built from. Freezing takes the campus
description and the study parameters, writes them in one canonical form and
hashes it: the SHA-256 is the ``data_hash`` and the model set's id is
derived from it, so refreezing identical inputs gives the same id.

Canonical form (``canonical``): JSON with sorted keys and no whitespace.

* Dict key order does not matter; list order does (a list is a sequence:
  the order of tap positions or dispatch points is data).
* A tuple is written as a list.
* A number is hashed by its value: an integral float is written as an int,
  so ``1`` and ``1.0`` hash the same. ``True`` stays a boolean, not ``1``.
* numpy scalars become the plain Python value they hold.
* Refused, naming the path: NaN or infinity, a dict key that is not a
  string, and any other type (a pandas object, a set, a numpy array...).
  A frozen input must be plain data, and a NaN has no stable meaning.

The timestamp (``created_utc``) records when the freeze happened. It is not
hashed: two freezes of the same data at different times are the same model
set.

``freeze`` itself lives in ``ingest/aggregate.py``: it builds both variants
(``ingest/campus.build_campus``), and ``schema/`` imports only ``schema/``.
Every export pack embeds ``manifest(model_set, variant)``.

stdlib/numpy only, like every other contract in ``schema/``.
"""

import copy
import dataclasses
import datetime
import hashlib
import json
import math

import numpy as np

from gridspine.schema.contracts import ContractError

#: The two plant models TRGC 01/02 ask for.
VARIANTS = ("detailed", "aggregated")
#: The model set id's prefix and the number of hash digits it carries.
ID_PREFIX = "ms-"
ID_DIGITS = 12


def _plain(obj, path):
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, np.generic):
        return _plain(obj.item(), path)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ContractError(
                f"model set: {path} is {obj}; a frozen value must be finite"
            )
        return int(obj) if obj.is_integer() else obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ContractError(
                    f"model set: {path} has key {k!r} of type {type(k).__name__}; "
                    "keys must be strings"
                )
            out[k] = _plain(v, f"{path}.{k}")
        return out
    if isinstance(obj, (list, tuple)):
        return [_plain(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    raise ContractError(
        f"model set: {path} is a {type(obj).__name__}; only plain JSON data "
        "(dict, list, str, number, bool, None) can be frozen"
    )


def canonical(campus_spec, params) -> str:
    """
    The canonical string of the inputs a model set is frozen from (see
    the module docstring for the rules).
    """
    plain = {
        "campus": _plain(campus_spec, "campus"),
        "params": _plain(params, "params"),
    }
    return json.dumps(
        plain, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def data_hash(campus_spec, params) -> str:
    """SHA-256 (hex) of ``canonical(campus_spec, params)``."""
    return hashlib.sha256(canonical(campus_spec, params).encode("ascii")).hexdigest()


@dataclasses.dataclass(frozen=True)
class ModelSet:
    """
    One frozen model set: its id, the hash of its inputs, when it was
    frozen, the parameters, and the campus description of each variant
    (``specs["aggregated"]`` is None when only the detailed model was
    frozen). ``unit_map`` maps each aggregated unit to the detailed units
    it stands for (empty without an aggregated variant).
    """

    model_set_id: str
    data_hash: str
    created_utc: str
    params: dict
    specs: dict
    unit_map: dict

    def spec(self, variant: str) -> dict:
        """
        A copy of the campus description of ``variant`` (the frozen one
        stays as hashed); refused when the variant is unknown or was not
        frozen.
        """
        _check_variant(self, variant)
        return copy.deepcopy(self.specs[variant])


def _check_variant(ms, variant):
    if variant not in VARIANTS:
        raise ContractError(
            f"model set {ms.model_set_id}: unknown variant {variant!r}; allowed {list(VARIANTS)}"
        )
    if ms.specs.get(variant) is None:
        raise ContractError(
            f"model set {ms.model_set_id}: the {variant} variant was not frozen"
        )


def new_model_set(
    campus_spec, params, *, aggregated=None, unit_map=None, now=None
) -> ModelSet:
    """
    A ``ModelSet`` from the detailed campus description and the study
    parameters (both hashed), the aggregated description derived from them
    (not hashed: it follows from the inputs), and the freeze time ``now``
    (a timezone-aware datetime; default the current UTC time).
    """
    h = data_hash(campus_spec, params)
    now = now or datetime.datetime.now(datetime.UTC)
    if now.tzinfo is None:
        raise ContractError("model set: the freeze time must be timezone-aware")
    created = now.astimezone(datetime.UTC).isoformat(timespec="seconds")
    specs = {
        "detailed": copy.deepcopy(campus_spec),
        "aggregated": None if aggregated is None else copy.deepcopy(aggregated),
    }
    return ModelSet(
        model_set_id=ID_PREFIX + h[:ID_DIGITS],
        data_hash=h,
        created_utc=created,
        params=copy.deepcopy(params),
        specs=specs,
        unit_map=copy.deepcopy(unit_map or {}),
    )


def manifest(ms: ModelSet, variant: str) -> dict:
    """
    What every export pack (GC-04..06) embeds to name the model it was
    written from: ``model_set_id``, ``data_hash``, ``variant`` and
    ``model_hash``. ``data_hash`` names the inputs; ``model_hash`` (the
    SHA-256 of the variant's own campus description, same canonical form)
    names the model itself, so an aggregated model that a later version of
    the aggregation code derives differently from the same inputs does not
    pass under the same manifest.
    """
    _check_variant(ms, variant)
    return {
        "model_set_id": ms.model_set_id,
        "data_hash": ms.data_hash,
        "variant": variant,
        "model_hash": model_hash(ms.specs[variant]),
    }


def model_hash(spec) -> str:
    """SHA-256 (hex) of one variant's campus description, canonical form."""
    return data_hash(spec, {})
