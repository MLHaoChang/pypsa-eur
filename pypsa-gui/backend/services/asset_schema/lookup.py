"""
Catalogue lookups for the chat tools (spec expert-catalogue §7.1, slice 01b).

- `resolve(entry_id)`: an entry by its id, or by one of its `former_ids` (the
  id it had before a rename), so an old id still opens the current entry.
- `search(query, ...)`: the ⌘K palette's ranking (`frontend/src/catalogue/
  searchIndex.ts`) on the server: exact plain name, alias, model name, word
  prefix, then short help and guide text; `tests/test_catalogue_lookup.py` pins
  the order on the same cases the palette's vitest uses.
- `plain_name(scope, model_name)`: the planner's word for one column.
- `asset_type_of(cls, row, bus_carrier)`: the asset type a component is drawn
  as, the first type in table order with a matching rule (`utils/assetTypes.ts`
  `matchType`, without its site-membership port rule, which only canvases need).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any
from collections.abc import Callable

from services.asset_schema import catalogue as C
from services.asset_schema import entries as E
from services.asset_schema.asset_types import ASSET_TYPES, AssetTypeSpec

SCORE = {"plain": 100, "alias": 80, "model": 70, "prefix": 60, "help": 30, "guide": 20}
DEPTH_RANK = {"essentials": 0, "basic": 1, "advanced": 2, "detail": 3}
MIN_TEXT_QUERY = 3


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^\w]+|_", _norm(s)) if w]


def _plain_text(md: str | None) -> str:
    if not md:
        return ""
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"[#*_`>|]", " ", md)
    return _norm(md)


def _prefix_match(q: list[str], ws: list[str]) -> bool:
    return bool(q) and all(any(w.startswith(t) for w in ws) for t in q)


@lru_cache(maxsize=1)
def _by_id() -> dict[str, C.Entry]:
    return {e.id: e for e in C.entries()}


@lru_cache(maxsize=1)
def _by_former_id() -> dict[str, C.Entry]:
    return {f: e for e in C.entries() for f in e.spec.former_ids}


def resolve(entry_id: str) -> tuple[C.Entry | None, bool]:
    """`(entry, renamed)`: the entry `entry_id` names (renamed when it was a former id), else `(None, False)`."""
    e = _by_id().get(entry_id)
    if e is not None:
        return e, False
    e = _by_former_id().get(entry_id)
    return (e, True) if e is not None else (None, False)


@lru_cache(maxsize=1)
def _plain_names() -> dict[tuple[str, str], str]:
    return {(e.scope, e.model_name): e.plain_name for e in C.entries()}


def plain_name(scope: str, model_name: str) -> str | None:
    """The plain name of `model_name` on class or settings scope `scope`, else None."""
    return _plain_names().get((scope, model_name))


def group_name(group_id: str) -> str:
    """A group's plain name (its id when the catalogue has no name for it)."""
    return E.GROUP_NAMES.get(group_id, group_id)


def asset_types_of(scope: str) -> list[str]:
    """The ids of the placeable asset types drawn from class `scope` (none for a settings scope)."""
    return [t.id for t in ASSET_TYPES if scope in t.components and not t.internal]


def asset_type(type_id: str) -> AssetTypeSpec | None:
    return next((t for t in ASSET_TYPES if t.id == type_id), None)


def _match_via(e: C.Entry, q: str, qw: list[str]) -> str | None:
    plain = _norm(e.plain_name)
    aliases = [_norm(a) for a in e.aliases]
    if plain == q:
        return "plain"
    if q in aliases:
        return "alias"
    if e.model_name.lower() == q.replace(" ", "_"):
        return "model"
    if _prefix_match(qw, _words(e.plain_name)) or any(
        _prefix_match(qw, _words(a)) for a in e.aliases
    ):
        return "prefix"
    if len(q) < MIN_TEXT_QUERY:
        return None
    help_ = _norm(e.short_help)
    if q in help_ or _prefix_match(qw, _words(e.short_help)):
        return "help"
    guide = e.guide if hasattr(e, "guide") else C.guide_body(e.id)
    if q in _plain_text(guide):
        return "guide"
    return None


def search(
    query: str,
    *,
    asset_type_id: str | None = None,
    function: str | None = None,
    limit: int = 8,
    pool=None,
) -> list[C.Entry]:
    """The best `limit` entries for `query`, narrowed to one asset type's classes and/or one function.

    Ranked as the palette ranks: entries the function needs first (with a
    function), then match strength, then depth, plain name and id (lower case
    before upper on a tie, as `localeCompare` orders them). `pool` replaces the
    catalogue (the shared case table, `tests/data/catalogue_search_cases.json`)."""
    q = _norm(query)
    if not q:
        return []
    qw = _words(q)
    classes = None
    if asset_type_id is not None:
        t = asset_type(asset_type_id)
        classes = set(t.components) if t is not None else set()
    hits: list[tuple[C.Entry, str]] = []
    for e in C.entries() if pool is None else pool:
        if classes is not None and e.scope not in classes:
            continue
        if function is not None and function not in e.functions:
            continue
        via = _match_via(e, q, qw)
        if via:
            hits.append((e, via))
    hits.sort(
        key=lambda h: (
            -(1 if function and h[0].functions.get(function) == "needs" else 0),
            -SCORE[h[1]],
            DEPTH_RANK.get(h[0].depth, 9),
            h[0].plain_name.casefold(),
            h[0].plain_name.swapcase(),
            h[0].id.casefold(),
            h[0].id.swapcase(),
        )
    )
    return [e for e, _ in hits[:limit]]


def asset_type_of(
    cls: str,
    row: dict[str, Any],
    bus_carrier: Callable[[str], str | None],
    types: tuple[AssetTypeSpec, ...] = ASSET_TYPES,
) -> AssetTypeSpec | None:
    """The first asset type whose rule matches component `row` of class `cls`.

    `bus_carrier(name)` gives a bus's carrier (None for an unknown bus); a Bus
    reads its own. A missing carrier reads as "" (a Bus: "AC"), as in the frontend.
    Unlike the frontend's `matchType` it has no site members, so a rule's `port`
    is not checked: a Link seen only from the bus its rule does not stand at
    still takes that rule's type (`tests/data/asset_type_match_cases.json`
    lists the cases where the two answer differently)."""
    if cls == "Bus":
        carrier = bus_carrier(str(row.get("name", ""))) or row.get("carrier") or "AC"
    else:
        carrier = row.get("carrier") or ""
    carrier = str(carrier)
    has_bus2 = cls == "Link" and bool(row.get("bus2") or "")
    for t in types:
        for m in t.match:
            if m.component != cls:
                continue
            if m.carrier is not None and not re.compile(m.carrier, re.I).search(
                carrier
            ):
                continue
            if m.has_bus2 and not has_bus2:
                continue
            if m.far_carrier is not None:
                far = str(row.get(m.far_carrier[0]) or "")
                if not far or not re.compile(m.far_carrier[1], re.I).search(
                    bus_carrier(far) or "AC"
                ):
                    continue
            return t
    return None


def clear_caches() -> None:
    for f in (_by_id, _by_former_id, _plain_names):
        f.cache_clear()
