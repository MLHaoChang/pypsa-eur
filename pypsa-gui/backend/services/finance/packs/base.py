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


@dataclass(frozen=True)
class Rule:
    value: Any
    source: str


@dataclass(frozen=True)
class JurisdictionPack:
    jurisdiction: str
    valid_from: date
    source: str
    rules: Mapping[str, Rule] = field(default_factory=dict)
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
            "valid_from": self.valid_from.isoformat(),
            "source": self.source,
            "notes": self.notes,
            "rules": {k: {"value": v.value, "source": v.source}
                      for k, v in sorted(self.rules.items())},
        }

    @property
    def pack_hash(self) -> str:
        blob = json.dumps(self.canonical_payload(), sort_keys=True,
                          separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# -- registry ----------------------------------------------------------------

_REGISTRY: dict[str, Callable[[], JurisdictionPack]] = {}


def register(jurisdiction: str, factory: Callable[[], JurisdictionPack]) -> None:
    _REGISTRY[jurisdiction] = factory


def available_jurisdictions() -> tuple[str, ...]:
    _ensure_builtin_packs_imported()
    return tuple(sorted(_REGISTRY))


def load_pack(jurisdiction: str, *, as_of: date) -> JurisdictionPack:
    _ensure_builtin_packs_imported()
    factory = _REGISTRY.get(jurisdiction)
    if factory is None:
        raise PackNotFound(
            f"no jurisdiction pack {jurisdiction!r}; available: "
            f"{', '.join(sorted(_REGISTRY)) or 'none'}")
    pack = factory()
    if as_of < pack.valid_from:
        raise PackNotFound(
            f"pack {jurisdiction!r} is valid from {pack.valid_from}; "
            f"as_of {as_of} predates it")
    return pack


def _ensure_builtin_packs_imported() -> None:
    # Import side effect registers the pack; kept lazy so `base` has no
    # import-time dependency on the jurisdiction modules.
    from services.finance.packs import eu_de, us_federal  # noqa: F401
