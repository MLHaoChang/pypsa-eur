"""Profile families: who may write which target (time-series generators, ticket 01).

A family is a kind of generated series (renewable capacity factors, loads,
availability bounds, other model series). It owns two rules the apply path
enforces for it:

  * ``targets``: the ``(component_type, attribute)`` pairs it may write. Apply
    refuses anything else, so a hand-made or old meta cannot bypass the rule
    (gatekeeper review 2026-10-20);
  * ``guard``: an optional check on the target component itself, e.g. the
    renewable wind/solar carrier rule.

A family other than renewable may also bring its own ``generate``, which
`engine.generate_profile(family=...)` dispatches to.

The bounds of each target live with the target (`apply.TARGETS`), not here: a
p_min_pu is clipped to [0, 1] whoever writes it.

A meta without ``family`` is renewable: every artifact and pending job made
before families existed is a wind/solar capacity factor.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

DEFAULT_FAMILY = "renewable"

Target = tuple[str, str]
_Guard = Callable[[Any, str, str, Mapping[str, Any]], None]
_Generate = Callable[[list[str], str, Mapping[str, Any], int],
                     "tuple[pd.DataFrame, dict[str, Any]]"]


@dataclass(frozen=True)
class Family:
    name: str
    targets: frozenset[Target]
    # (ctx, component_type, component, meta) -> None; raises a typed
    # ProfileApplyError to refuse.
    guard: _Guard | None = None
    # (ids, kind, params, year) -> (frame, meta_extra): the family's own
    # generator behind `engine.generate_profile(family=...)`. The renewable
    # family generates through the engine's source registry instead.
    generate: _Generate | None = None
    # Where an artifact of this family goes when the caller names no target.
    default_target: Target | None = None


_FAMILIES: dict[str, Family] = {}


def register_family(family: Family) -> None:
    """Register (or replace) a family."""
    _FAMILIES[family.name] = family


def family_of(meta: Mapping[str, Any]) -> str:
    return str(meta.get("family") or DEFAULT_FAMILY).strip().lower()


def get_family(name: str) -> Family:
    # The apply path's error types live in apply.py, which imports this
    # module — hence the import here, as in `_renewable_guard`.
    from services.profile_gen.apply import UnknownFamilyError

    try:
        return _FAMILIES[name]
    except KeyError:
        raise UnknownFamilyError(
            f"unknown profile family {name!r}; registered: "
            f"{', '.join(sorted(_FAMILIES))}"
        ) from None


def default_target(meta: Mapping[str, Any], component_type: str | None,
                   attribute: str | None) -> Target:
    """The (component_type, attribute) an apply writes: the caller's, or the
    family's default. Raises the apply path's UnsupportedTargetError when
    neither names one."""
    stated = meta.get("target")
    if isinstance(stated, (list, tuple)) and len(stated) == 2:
        # A family with several targets (availability, series) says which
        # one this artifact is for; a caller may move it to another component
        # type with the same attribute (a price onto a link), never to
        # another attribute (a floor written as a cap).
        if attribute and attribute != stated[1]:
            from services.profile_gen.apply import UnsupportedTargetError

            raise UnsupportedTargetError(
                f"this {meta.get('kind', 'profile')} artifact is for "
                f"{stated[0]}/{stated[1]}, not {component_type or stated[0]}/{attribute}")
        return str(component_type or stated[0]), str(stated[1])
    if component_type and attribute:
        return component_type, attribute
    family = get_family(family_of(meta))
    if family.default_target is None or (
            component_type and component_type != family.default_target[0]):
        from services.profile_gen.apply import UnsupportedTargetError

        raise UnsupportedTargetError(
            f"a {family.name} profile has no default target here; name "
            "component_type and attribute"
        )
    return family.default_target


def _renewable_guard(ctx, component_type: str, component: str,
                     meta: Mapping[str, Any]) -> None:
    """Spec §4 carrier guard: a wind/solar capacity factor goes onto a
    wind/solar generator of the same technology, nothing else."""
    from services.profile_gen.apply import CarrierMismatchError

    n = ctx.network
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
    default_target=("generators", "p_max_pu"),
))
# The data-centre template's facility load is split across these two loads by
# its `dc_load` spec's critical share (#421); a profile on either would break
# the split.
DC_SPLIT_LOADS = ("it_load", "cooling")


def _load_guard(ctx, component_type: str, component: str,
                meta: Mapping[str, Any]) -> None:
    """On a project created with a `dc_load` spec, that spec owns the
    data-centre load (gatekeeper review 2026-10-20): no data-centre archetype
    anywhere, and no load profile on the split loads."""
    from services.dc_load_profile import read_envelope
    from services.profile_gen.apply import DcLoadOwnedError

    storage = getattr(ctx, "storage_dir", None)
    if not storage or read_envelope(storage) is None:
        return
    if meta.get("archetype") == "data_centre":
        raise DcLoadOwnedError(
            "this project's data-centre load comes from its dc_load spec (the "
            "template's it_load/cooling split); change it there, not with a "
            "data-centre profile"
        )
    if component in DC_SPLIT_LOADS:
        raise DcLoadOwnedError(
            f"{component!r} carries this project's dc_load facility load "
            "(split by its critical share); change the dc_load spec instead"
        )


def _load_generate(ids, archetype, params, year):
    # Imported on first use, as the engine's lazy sources are: the archetype
    # module pulls in the DC model, which a renewable-only caller never needs.
    from services.profile_gen.load_archetypes import generate_frame

    return generate_frame(ids, archetype, params, year)


register_family(Family("load", frozenset({("loads", "p_set")}),
                       guard=_load_guard, generate=_load_generate,
                       default_target=("loads", "p_set")))
_WEATHER = re.compile(r"wind|solar|(?<![a-z])pv(?![a-z])")


def is_weather_carrier(carrier: Any) -> bool:
    """A carrier whose generators carry a weather capacity-factor profile."""
    return bool(_WEATHER.search(str(carrier or "").lower()))


def _availability_guard(ctx, component_type: str, component: str,
                        meta: Mapping[str, Any]) -> None:
    """Wind and solar keep their weather profile (composing an availability
    series with it is its own future plan); a dated maintenance window
    applies only on a single-period network of its year."""
    from services.profile_gen.apply import CarrierMismatchError
    from services.profile_gen.availability import check_dated_windows

    carrier = str(ctx.network.generators.at[component, "carrier"] or "").lower()
    if is_weather_carrier(carrier):
        raise CarrierMismatchError(
            f"generator {component!r} ({carrier}) carries a weather profile; an "
            "availability series would overwrite it")
    check_dated_windows(ctx, meta)


def _availability_generate(ids, kind, params, year):
    # Imported on first use, as `_load_generate` is.
    from services.profile_gen.availability import generate_frame

    return generate_frame(ids, kind, params, year)


register_family(Family(
    "availability",
    frozenset({("generators", "p_max_pu"), ("generators", "p_min_pu")}),
    guard=_availability_guard, generate=_availability_generate,
))


def _series_guard(ctx, component_type, component, meta):
    from services.profile_gen.series import guard

    guard(ctx, component_type, component, meta)


def _series_generate(ids, kind, params, year):
    # Imported on first use, as `_load_generate` is.
    from services.profile_gen.series import generate_frame

    return generate_frame(ids, kind, params, year)


register_family(Family(
    "series",
    frozenset({
        ("generators", "marginal_cost"), ("links", "marginal_cost"),
        ("links", "efficiency"), ("storage_units", "inflow"),
    }),
    guard=_series_guard, generate=_series_generate,
))
