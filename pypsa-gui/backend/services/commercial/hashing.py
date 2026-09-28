"""
Versioned content hashes for the commercial layer's drift checks (IC P2 WP2.0
review, condition 1).

Every committed commercial record (demand info, tier volumes, `ic_poc_links`,
the connection-fee and agreement records) stores a content hash of the config
the solve bound, and `cost_rows` compares it with the current config to flag
`config_changed_since_solve`. A hash must therefore be recomputed with the SAME
recipe that made it, or a schema change would flip every solved project:

  * recipe 1 — `model_dump(mode="json")`, every field. Records without a
    `hash_version` were made by it (P1 and P2 up to this change). When a model
    grows a field after recipe 1, register it in `FIELDS_AFTER_V1` as
    `(model class name, field)` with its default: it is dropped from recipe-1
    dumps while it holds that default, so a stored recipe-1 hash still compares
    equal. `tests/test_commercial_hash_versions.py` pins the recipe-1 field
    inventory and fails on an unregistered new field.
  * recipe 2 — `model_dump(mode="json", exclude_defaults=True)`: a new optional
    field never changes a hash (they still need their `FIELDS_AFTER_V1` entry
    for recipe-1 comparisons). **Changing an existing default** is invisible to
    recipe 2 (a config relying on the old default hashes the same while its
    meaning changes): it must bump `HASH_VERSION`.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

HASH_VERSION = 2

# Fields added to the commercial models after recipe 1: (model class name,
# field) → default. Append in the WP that adds a field (P2: WP2.1a-i/ii/iii,
# WP2.2a/b, …); the inventory test fails until it is here.
FIELDS_AFTER_V1: dict[tuple[str, str], Any] = {
    ("CommercialConfig", "power_factor"): None,          # WP2.1a-i
    ("TariffPeriod", "tier_rates"): None,                # WP2.1a-ii
    ("Ratchet", "months"): None,                         # WP2.1a-iii
    ("Ratchet", "cyclic_year"): False,                   # WP2.1a-iii
}


def _recipe_1(obj: Any) -> Any:
    """The full JSON dump of `obj`, minus the fields registered as added after
    recipe 1 while they hold their default — walked per model, so the same
    field name on two models (e.g. `months`) never collides."""
    if isinstance(obj, list):
        return [_recipe_1(o) for o in obj]
    if not hasattr(obj, "model_dump"):
        return obj
    out = obj.model_dump(mode="json")
    name = type(obj).__name__
    for field in type(obj).model_fields:
        value = getattr(obj, field)
        key = (name, field)
        if key in FIELDS_AFTER_V1 and out.get(field) == FIELDS_AFTER_V1[key]:
            out.pop(field, None)
        elif hasattr(value, "model_dump") or (isinstance(value, list) and value
                                              and hasattr(value[0], "model_dump")):
            out[field] = _recipe_1(value)
    return out


def canonical(obj: Any, *, version: int = HASH_VERSION) -> Any:
    """The JSON-able form of `obj` (a pydantic model, a list of them, or plain
    data) that recipe `version` hashes."""
    if isinstance(obj, list):
        return [canonical(o, version=version) for o in obj]
    if hasattr(obj, "model_dump"):
        if version >= 2:
            return obj.model_dump(mode="json", exclude_defaults=True)
        return _recipe_1(obj)
    return obj


def digest(obj: Any, *, version: int = HASH_VERSION) -> str:
    """16-hex sha256 of the canonical JSON (sorted keys)."""
    raw = json.dumps(canonical(obj, version=version), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def version_of(record: dict | None) -> int:
    """The recipe a committed record was hashed with (1 when it predates versions)."""
    return int((record or {}).get("hash_version") or 1)
