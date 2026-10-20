"""Profile families: who may write which target (time-series generators, ticket 01).

A family is a kind of generated series (renewable capacity factors, loads,
availability bounds, other model series). It owns two rules the apply path
enforces for it:

  * ``targets``: the ``(component_type, attribute)`` pairs it may write. Apply
    refuses anything else, so a hand-made or old meta cannot bypass the rule
    (gatekeeper review 2026-10-20);
  * ``guard``: an optional check on the target component itself, e.g. the
    renewable wind/solar carrier rule.

The bounds of each target live with the target (`apply.TARGETS`), not here: a
p_min_pu is clipped to [0, 1] whoever writes it.

A meta without ``family`` is renewable: every artifact and pending job made
before families existed is a wind/solar capacity factor.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from services.profile_gen.engine import ProfileGenError

DEFAULT_FAMILY = "renewable"

Target = tuple[str, str]
_Guard = Callable[[Any, str, str, Mapping[str, Any]], None]


class UnknownFamilyError(ProfileGenError):
    """The meta names a family nobody registered."""


@dataclass(frozen=True)
class Family:
    name: str
    targets: frozenset[Target]
    # (network, component_type, component, meta) -> None; raises a typed
    # ProfileApplyError to refuse.
    guard: _Guard | None = None
    description: str = ""


_FAMILIES: dict[str, Family] = {}


def register_family(family: Family) -> None:
    """Register (or replace) a family."""
    _FAMILIES[family.name] = family


def family_of(meta: Mapping[str, Any]) -> str:
    return str(meta.get("family") or DEFAULT_FAMILY).strip().lower()


def get_family(name: str) -> Family:
    try:
        return _FAMILIES[name]
    except KeyError:
        raise UnknownFamilyError(
            f"unknown profile family {name!r}; registered: "
            f"{', '.join(sorted(_FAMILIES))}"
        ) from None


def _renewable_guard(n, component_type: str, component: str,
                     meta: Mapping[str, Any]) -> None:
    """Spec §4 carrier guard: a wind/solar capacity factor goes onto a
    wind/solar generator of the same technology, nothing else."""
    from services.profile_gen.apply import CarrierMismatchError

    carrier = str(n.generators.at[component, "carrier"] or "")
    cl = carrier.lower()
    is_wind = "wind" in cl
    is_solar = "solar" in cl or "pv" in cl
    if not (is_wind or is_solar):
        raise CarrierMismatchError(
            f"generator {component!r} has carrier {carrier!r}; generated "
            "profiles apply to wind/solar assets only"
        )
    tech = str(meta.get("technology", "")).strip().lower()
    if tech not in ("wind", "solar"):
        raise CarrierMismatchError(
            f"profile meta carries technology {tech!r}; expected wind/solar"
        )
    if (tech == "wind" and not is_wind) or (tech == "solar" and not is_solar):
        raise CarrierMismatchError(
            f"profile technology {tech!r} does not match generator "
            f"{component!r}'s carrier {carrier!r}"
        )


register_family(Family(
    "renewable", frozenset({("generators", "p_max_pu")}),
    guard=_renewable_guard,
    description="wind/solar capacity factors from weather data",
))
register_family(Family(
    "load", frozenset({("loads", "p_set")}),
    description="demand profiles: archetypes or measured uploads",
))
register_family(Family(
    "availability",
    frozenset({("generators", "p_max_pu"), ("generators", "p_min_pu")}),
    description="maintenance, outages, must-run and derating bounds",
))
register_family(Family(
    "series",
    frozenset({
        ("generators", "marginal_cost"), ("links", "marginal_cost"),
        ("links", "efficiency"), ("storage_units", "inflow"),
    }),
    description="other model series: prices, efficiencies, hydro inflow",
))
