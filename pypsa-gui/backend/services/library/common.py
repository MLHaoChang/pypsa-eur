"""The shipped (`common`) Library items: the same for every organization, read-only.

Each is a JSON file under `common_items/<kind>/<name>.json` holding `payload` (validated
as its kind, stored canonically like any Library item) and `meta` (unhashed: scope,
licence, attribution, source and its cells, notes). Its ref is version 1 with the
canonical payload's sha256, pinned in `PINNED`, so a pin to a common item keeps
resolving and a changed file is refused. A new edition is a new name, never an edit.
Only sources the owner cleared for `common` are shipped here (the terms index in
.scratch/gridspine-library-research/terms/); user-scoped sources arrive by import.
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

from models import commercial as M

COMMON_DIR = Path(__file__).with_name("common_items")

# The canonical payload hash of every shipped item; a test pins these.
PINNED: dict[tuple[str, str], str] = {
    # EU-C09: ACM Tarievenbesluit TenneT 2026 (owner clearance, common, 2026-10-21).
    ("tariff", "nl-tennet-2026-ehs"): "78fc38dd84734834663ebbeae3e630fe888fc3daeffaa7515d0e03fa95716c5c",
    ("tariff", "nl-tennet-2026-hs"): "b866852dc70e65828e411f28c5fae72da87f760795fb27c5b8cb7304acc32b5a",
    # SCU06 S2a: illustrative EU PPA/CfD templates (owner Q11 = A), not market data. Each
    # cites its value ranges in `meta.notes`; party and asset fields hold placeholders
    # (`services.commercial.placeholders`) that the server refuses to save or settle.
    ("contract", "eu-cfd"): "2a19864b8bd9191fc53d798a510bb43581bddced8654db2746eb6e3f5f93bc85",
    ("contract", "eu-ppa-as-consumed-btm"): "1b26b5579e520a7f46cfaad3633b88174f010853704a93a51365dac5c2769a4b",
    ("contract", "eu-ppa-baseload"): "631646fbf8e8dc82231176cfc84c7300a53b8ff50a374db1be3a8b790d655e9c",
    ("contract", "eu-ppa-pay-as-produced"): "a395ffdfccde46698d9ed1fa1ff76f5f152d0419ef8fc5a8dd55b623ae31f57d",
    ("contract", "eu-ppa-sleeved"): "2fd70afa1a86d47873542da3f2094c5b8cfcfb79bf767d1d400eb9a1bb8bb771",
}


class CommonItemError(ValueError):
    """A shipped item does not match its pin or does not validate."""


@functools.lru_cache(maxsize=1)
def _items() -> dict[tuple[str, str], tuple[dict, dict, M.LibraryItemRef]]:
    from services.library import items as I

    out = {}
    for path in sorted(COMMON_DIR.glob("*/*.json")):
        kind, name = path.parent.name, path.stem
        raw = json.loads(path.read_text("utf-8"))
        model = I.validate_payload(kind, raw["payload"])
        data = I._canonical_bytes(kind, model, raw["payload"])
        digest = hashlib.sha256(data).hexdigest()
        if PINNED.get((kind, name)) != digest:
            raise CommonItemError(f"common {kind} {name!r} does not match its pinned hash")
        meta = I.ItemMeta.model_validate(raw.get("meta") or {})
        if meta.scope != "common":
            raise CommonItemError(f"common {kind} {name!r} must declare scope 'common'")
        ref = M.LibraryItemRef(kind=kind, id=name, version=1, hash=digest)
        out[(kind, name)] = (json.loads(data), meta.model_dump(), ref)
    return out


def names(kind: str) -> list[str]:
    return sorted(name for k, name in _items() if k == kind)


def is_common(kind: str, name: str) -> bool:
    return (kind, name) in _items()


def payload(kind: str, name: str) -> dict:
    """A fresh copy of the stored (canonical) payload."""
    return json.loads(json.dumps(_items()[(kind, name)][0]))


def meta(kind: str, name: str) -> dict:
    return json.loads(json.dumps(_items()[(kind, name)][1]))


def ref(kind: str, name: str) -> M.LibraryItemRef:
    return _items()[(kind, name)][2]
