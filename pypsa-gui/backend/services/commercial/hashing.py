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
    grows a field after recipe 1, the field is listed in `FIELDS_AFTER_V1` with
    its default and dropped from recipe-1 dumps while it holds that default, so
    a stored recipe-1 hash still compares equal.
  * recipe 2 — `model_dump(mode="json", exclude_defaults=True)`: a new optional
    field never changes a hash. Fields added from now on need no list entry.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

HASH_VERSION = 2

# Fields added to the commercial models after recipe 1, with their defaults.
# Append here in the WP that adds a field (P2: WP2.1a-i/ii/iii, WP2.2a/b, …).
FIELDS_AFTER_V1: dict[str, Any] = {}


def _drop_later_fields(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _drop_later_fields(v) for k, v in obj.items()
                if not (k in FIELDS_AFTER_V1 and v == FIELDS_AFTER_V1[k])}
    if isinstance(obj, list):
        return [_drop_later_fields(v) for v in obj]
    return obj


def canonical(obj: Any, *, version: int = HASH_VERSION) -> Any:
    """The JSON-able form of `obj` (a pydantic model, a list of them, or plain
    data) that recipe `version` hashes."""
    if isinstance(obj, list):
        return [canonical(o, version=version) for o in obj]
    if hasattr(obj, "model_dump"):
        if version >= 2:
            return obj.model_dump(mode="json", exclude_defaults=True)
        return _drop_later_fields(obj.model_dump(mode="json"))
    return obj


def digest(obj: Any, *, version: int = HASH_VERSION) -> str:
    """16-hex sha256 of the canonical JSON (sorted keys)."""
    raw = json.dumps(canonical(obj, version=version), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def version_of(record: dict | None) -> int:
    """The recipe a committed record was hashed with (1 when it predates versions)."""
    return int((record or {}).get("hash_version") or 1)
