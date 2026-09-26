"""
Jurisdiction pack loader, hashing and `not_established` semantics (WP0.2).

A pack is an immutable, dated table of rules. Looking up a rule the pack does
not carry returns `RuleLookup(status="not_established")` with a reason — it
never returns a default number and never raises (ADR-0001: an unknown value
ships as null + flag, not as 0). The hash is sha256 of the canonical JSON
payload truncated to 16 hex chars, the same recipe as
`services/adequacy/archetypes.pack_hash`, and it covers rule values AND their
sources, so a re-sourced identical value is a different pack.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Callable, Literal, Mapping

RuleStatus = Literal["ok", "not_established"]


class PackNotFound(LookupError):
    """Unknown jurisdiction, or `as_of` predates the pack's validity."""


@dataclass(frozen=True)
class RuleLookup:
    name: str
    status: RuleStatus
    value: Any = None
    source: str | None = None
    reason: str | None = None


_JSON_NATIVE = (int, float, str, bool, type(None), list, dict)


@dataclass(frozen=True)
class Rule:
    value: Any
    source: str

    def __post_init__(self) -> None:
        # The hash is sha256 of canonical JSON with NO `default=` fallback, so
        # a value must be JSON-native (or a `date`, converted explicitly).
        # Anything else (Decimal, numpy scalars, objects) would either hash by
        # `repr` — unstable across processes — or collide with a string.
        if not isinstance(self.value, _JSON_NATIVE + (date,)):
            raise TypeError(
                f"rule value must be JSON-native or a date, got "
                f"{type(self.value).__name__}")
        if not isinstance(self.source, str) or not self.source:
            raise TypeError("rule source must be a non-empty string")

    def json_value(self) -> Any:
        return self.value.isoformat() if isinstance(self.value, date) else self.value


@dataclass(frozen=True)
class JurisdictionPack:
    """One dated rule table.

    `jurisdiction` is the pack id (`eu_de`, `us_federal`); `country` is the
    ISO-3166 code tariffs carry in `Tariff.jurisdiction` (`DE`, `US`) — the
    join key P1 uses to bind a tariff to its pack. `valid_to` is None for an
    open-ended pack; P4 packs with dated cliffs (OBBBA) register several
    versions per jurisdiction and `load_pack` picks the one covering `as_of`.
    `notes` IS part of the hash: use it for substantive provenance only, not
    for editorial comments, or every pinned report re-hashes.
    """

    jurisdiction: str
    country: str
    valid_from: date
    source: str
    rules: Mapping[str, Rule] = field(default_factory=dict)
    valid_to: date | None = None
    notes: str | None = None

    # -- lookups -----------------------------------------------------------

    def rule(self, name: str) -> RuleLookup:
        r = self.rules.get(name)
        if r is None:
            return RuleLookup(
                name=name, status="not_established",
                reason=(f"pack {self.jurisdiction!r} (valid from {self.valid_from}) "
                        f"carries no rule {name!r}"))
        return RuleLookup(name=name, status="ok", value=r.value, source=r.source)

    def with_rule(self, name: str, value: Any, *, source: str) -> "JurisdictionPack":
        rules = dict(self.rules)
        rules[name] = Rule(value=value, source=source)
        return replace(self, rules=rules)

    # -- hashing -----------------------------------------------------------

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "jurisdiction": self.jurisdiction,
            "country": self.country,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "source": self.source,
            "notes": self.notes,
            "rules": {k: {"value": v.json_value(), "source": v.source}
                      for k, v in sorted(self.rules.items())},
        }

    @property
    def pack_hash(self) -> str:
        # No `default=`: every value is JSON-native by construction (Rule
        # validates), so the bytes are the same in every process.
        blob = json.dumps(self.canonical_payload(), sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# -- registry ----------------------------------------------------------------

_REGISTRY: dict[str, list[Callable[[], JurisdictionPack]]] = {}
# One source of truth for the built-in packs; the tripwire test iterates it.
BUILTIN_PACK_MODULES: tuple[str, ...] = ("eu_de", "us_federal")
_loaded = False


def register(jurisdiction: str, factory: Callable[[], JurisdictionPack]) -> None:
    """Register one dated version of a pack. Idempotent per factory object."""
    versions = _REGISTRY.setdefault(jurisdiction, [])
    if factory not in versions:
        versions.append(factory)


def available_jurisdictions() -> tuple[str, ...]:
    _ensure_builtin_packs_imported()
    return tuple(sorted(_REGISTRY))


def load_pack(jurisdiction: str, *, as_of: date) -> JurisdictionPack:
    _ensure_builtin_packs_imported()
    versions = _REGISTRY.get(jurisdiction)
    if not versions:
        raise PackNotFound(
            f"no jurisdiction pack {jurisdiction!r}; available: "
            f"{', '.join(sorted(_REGISTRY)) or 'none'}")
    packs = [f() for f in versions]
    covering = [p for p in packs
                if p.valid_from <= as_of and (p.valid_to is None or as_of <= p.valid_to)]
    if not covering:
        spans = ", ".join(
            f"{p.valid_from}..{p.valid_to or 'open'}" for p in packs)
        raise PackNotFound(
            f"pack {jurisdiction!r} has no version covering as_of {as_of} "
            f"(versions: {spans})")
    # Several covering versions would be a registration error; take the
    # latest valid_from deterministically.
    return max(covering, key=lambda p: p.valid_from)


def _ensure_builtin_packs_imported() -> None:
    # Import side effect registers each pack; kept lazy so `base` has no
    # import-time dependency on the jurisdiction modules. Driven by
    # BUILTIN_PACK_MODULES so the registry and the tripwire share one list.
    global _loaded
    if _loaded:
        return
    import importlib
    for m in BUILTIN_PACK_MODULES:
        importlib.import_module(f"{__package__}.{m}")
    _loaded = True
