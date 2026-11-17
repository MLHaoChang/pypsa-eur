"""
The Parameter catalogue: the one reader every consumer calls (spec expert-catalogue §3.1).

It assembles `fields.py` rows (through `entries.py`), `asset_types.py`, the
plain groups and `functions.py` into one project-free payload, with the long
text from `guide/<id>.md`. Nothing here reads a project or a network: the
tonne-bus unit stays per asset on the units route (`fields.size_units`).

Guide ids: an entry's id, `asset_type.<id>`, `group.<id>`, `function.<id>`.
A guide id is only ever a key into `guide_ids()`; it is never joined into a path.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from services.asset_schema import entries as E
from services.asset_schema.asset_types import ASSET_TYPES, functions_of as type_functions
from services.asset_schema.fields import FieldSpec, fields_for, known_components
from services.asset_schema.functions import FUNCTION_HELP, FUNCTION_NAMES, Function

GUIDE_DIR = pathlib.Path(__file__).parent / "guide"
IDS_FILE = pathlib.Path(__file__).parent / "catalogue.ids.txt"

#: `?depth=` → the depths it shows (each depth includes the ones above it).
DEPTH_FILTER = {
    "essentials": ("essentials",),
    "basic": ("essentials", "basic"),
    "advanced": ("essentials", "basic", "advanced"),
    "detail": E.DEPTHS,
    "all": E.DEPTHS,
}


class CatalogueError(ValueError):
    """The catalogue cannot be assembled (an id collision): a build-time bug, never a user error."""


@dataclass(frozen=True)
class Entry:
    id: str
    scope: str
    spec: FieldSpec
    plain_name: str
    short_help: str
    group: str
    order: int
    depth: str
    functions: dict[str, str]
    aliases: tuple[str, ...]
    default: dict[str, Any]

    @property
    def model_name(self) -> str:
        return self.spec.key


def _json_number(v: Any) -> Any:
    if isinstance(v, float) and math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return v


@lru_cache(maxsize=1)
def _engine_defaults() -> dict[tuple[str, str], Any]:
    """PyPSA's built-in default per (class, column); NaN and empty defaults are left out."""
    import pypsa

    from services.asset_schema.schema import CLASS_ATTR

    n = pypsa.Network()
    out: dict[tuple[str, str], Any] = {}
    for cls in known_components():
        comp = getattr(n.components, CLASS_ATTR[cls], None)
        if comp is None:
            continue
        for col, row in comp.defaults.iterrows():
            v = row["default"]
            if hasattr(v, "item"):
                v = v.item()
            if v is None or v == "" or isinstance(v, float) and math.isnan(v):
                continue
            if isinstance(v, (bool, int, float, str)):
                out[(cls, str(col))] = v
    return out


def _default_of(scope: str, spec: FieldSpec) -> dict[str, Any]:
    """Shown, never written (§3.2). `library` defaults arrive with the Library packs (slice 03)."""
    v = _engine_defaults().get((scope, spec.key))
    if v is None or spec.kind in ("bus", "carrier"):
        return {"value": None, "source": "none"}
    return {"value": _json_number(v), "source": "engine"}


@lru_cache(maxsize=1)
def entries() -> tuple[Entry, ...]:
    out: list[Entry] = []
    seen: dict[str, str] = {}
    for scope in known_components():
        for order, spec in enumerate(fields_for(scope)):
            eid = E.entry_id_of(scope, spec)
            folded = eid.lower()
            if folded in seen:
                # Refused, never suffixed or truncated (#1127): set `id=` on one of the rows.
                raise CatalogueError(f"entry id {eid!r} ({scope}.{spec.key}) collides with {seen[folded]}")
            seen[folded] = f"{scope}.{spec.key}"
            group = E.group_of(scope, spec)
            out.append(Entry(
                id=eid, scope=scope, spec=spec, plain_name=E.plain_name_of(scope, spec),
                short_help=E.short_help_of(scope, spec), group=group, order=order,
                depth=E.depth_of(scope, spec, group), functions=E.functions_of(scope, spec, group),
                aliases=spec.aliases or E.ALIASES.get(spec.key, ()), default=_default_of(scope, spec),
            ))
    return tuple(out)


def entry_by_id() -> dict[str, Entry]:
    return {e.id: e for e in entries()}


def guide_ids() -> tuple[str, ...]:
    """Every id that has exactly one guide file: entries, asset types, groups, functions."""
    return (tuple(e.id for e in entries())
            + tuple(f"asset_type.{t.id}" for t in ASSET_TYPES)
            + tuple(f"group.{g}" for g in E.GROUP_IDS)
            + tuple(f"function.{f.value}" for f in Function))


@lru_cache(maxsize=1)
def _guide_bodies() -> dict[str, str]:
    return {gid: (GUIDE_DIR / f"{gid}.md").read_text("utf-8") if (GUIDE_DIR / f"{gid}.md").is_file() else ""
            for gid in guide_ids()}


def guide_body(guide_id: str) -> str | None:
    """The guide markdown for a known id, else None. The id is a dict key, never a path."""
    return _guide_bodies().get(guide_id)


def _entry_dict(e: Entry) -> dict[str, Any]:
    s = e.spec
    return {
        "id": e.id, "model_name": s.key, "scope": e.scope, "plain_name": e.plain_name,
        "short_help": e.short_help, "group": e.group, "legacy_group": s.group, "order": e.order,
        "depth": e.depth, "unit_display": s.unit_display, "unit_storage": s.unit_storage, "scale": s.scale,
        "kind": s.kind, "options": list(s.options), "options_plain": list(s.options_plain or s.options),
        "range": None, "default": e.default, "functions": e.functions, "aliases": list(e.aliases),
        "reveal_when": ({k: list(v) if isinstance(v, tuple) else v for k, v in s.reveal_when.items()}
                        if s.reveal_when else None),
        "derived": s.derived, "required": s.required, "blank": s.blank, "part": s.part,
        "related": list(s.related), "former_ids": list(s.former_ids),
        "guide": guide_body(e.id),
    }


def _type_dict(t) -> dict[str, Any]:
    groups = [g for g in E.GROUP_IDS if any(e.group == g for e in entries() if e.scope in t.components)]
    return {
        "id": t.id, "plain_name": t.plain_name, "plural": t.plural, "category": t.category,
        "components": list(t.components), "match": [m.as_dict() for m in t.match], "icon": t.icon,
        "symbols": dict(t.symbols), "ports": [p.as_dict() for p in t.ports], "groups": groups,
        "functions": list(type_functions(t)), "only_for": list(t.only_for),
        "palette": {"category": t.category}, "colour": t.colour, "internal": t.internal,
        "guide": guide_body(f"asset_type.{t.id}"),
    }


def payload(*, function: str | None = None, depth: str | None = None) -> dict[str, Any]:
    """The `GET /api/catalogue` body. Filters narrow entries and asset types; groups and functions stay whole.
    ValueError on an unknown filter value (the route turns it into a 422)."""
    if function is not None and function not in {f.value for f in Function}:
        raise ValueError(f"Unknown function '{function}'. Expected one of: {', '.join(f.value for f in Function)}.")
    if depth is not None and depth not in DEPTH_FILTER:
        raise ValueError(f"Unknown depth '{depth}'. Expected one of: {', '.join(DEPTH_FILTER)}.")
    shown = DEPTH_FILTER[depth] if depth else E.DEPTHS
    rows = [e for e in entries() if e.depth in shown and (function is None or function in e.functions)]
    types = [t for t in ASSET_TYPES if function is None or function in type_functions(t)]
    return {
        "version": content_hash(),
        "groups": [{"id": g, "plain_name": E.GROUP_NAMES[g], "short_help": E.GROUP_HELP[g], "order": i,
                    "guide": guide_body(f"group.{g}")} for i, g in enumerate(E.GROUP_IDS)],
        "functions": [{"id": f.value, "plain_name": FUNCTION_NAMES[f], "short_help": FUNCTION_HELP[f],
                       "guide": guide_body(f"function.{f.value}")} for f in Function],
        "asset_types": [_type_dict(t) for t in types],
        "entries": [_entry_dict(e) for e in rows],
    }


def payload_bytes(*, function: str | None = None, depth: str | None = None) -> bytes:
    return _payload_bytes(function, depth)


@lru_cache(maxsize=64)
def _payload_bytes(function: str | None, depth: str | None) -> bytes:
    return json.dumps(payload(function=function, depth=depth), ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


@lru_cache(maxsize=1)
def content_hash() -> str:
    """sha256 of the whole catalogue's content (the unfiltered payload without this field)."""
    raw = json.dumps(_unversioned(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unversioned() -> dict[str, Any]:
    return {
        "groups": [[g, E.GROUP_NAMES[g], E.GROUP_HELP[g], guide_body(f"group.{g}")] for g in E.GROUP_IDS],
        "functions": [[f.value, FUNCTION_NAMES[f], FUNCTION_HELP[f], guide_body(f"function.{f.value}")]
                      for f in Function],
        "asset_types": [_type_dict(t) for t in ASSET_TYPES],
        "entries": [_entry_dict(e) for e in entries()],
    }


def model_names() -> dict[str, dict[str, str]]:
    """`frontend/src/catalogue/modelNames.generated.json`: model name → plain name per scope, for the raw-name guard."""
    out: dict[str, dict[str, str]] = {}
    for e in entries():
        out.setdefault(e.scope, {})[e.model_name] = e.plain_name
    return out
