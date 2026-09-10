"""
Slack-generator identification — the single place that knows how LP slack
generators are named and carried.

The VOLL slack convention (name ``__voll_<bus>``, carrier ``load_shedding``,
created in ``solver_service._apply_modelling_assumptions`` step 3, transient
for the duration of one solve) used to be tested inline at every consumer:
the AC-PF pre-strip, the solve-log cost decomposition, and the price-drivers
diagnosis each carried their own ``carrier == "load_shedding"`` /
``name.startswith("__voll_")``. That was survivable with exactly one slack
carrier. Phase 1 of the adequacy work (design spec §4.4) adds a second tier —
``demand_response``, priced below VoLL and NOT counted as unserved energy —
and any site still testing equality with one string would silently let the
new tier set marginal prices, appear as a real generator in results, or be
lumped into the VOLL-shed cost bucket.

So: consumers test membership via the helpers below, never equality with a
literal. The literals themselves may appear ONLY in this module (and, as
plain keyword values, at the creation site) — enforced by the source guard in
``tests/test_adequacy_slack.py``.

The legacy ``voll_slack`` spellings exist because earlier builds used
``voll_slack`` for both name and carrier; a netcdf produced by an older build
that crashed pre-restore still carries them, and the AC-PF strip must clean
those up too (defence in depth — match on carrier OR name prefix, so neither
side can drift away from the other unnoticed).
"""
from __future__ import annotations

import pandas as pd

# The involuntary-curtailment tier: priced at VoLL, counts as unserved energy.
INVOLUNTARY_SLACK_CARRIER = "load_shedding"
# Pre-restore netcdfs from older builds used this for both name and carrier.
LEGACY_SLACK_CARRIER = "voll_slack"
# The voluntary tier (spec §4.4): contracted demand response, priced at its
# compensation (well below VoLL), a RESOURCE — it does NOT count as unserved
# energy, does not enter the ENS cap, and must not land in voll_shed_*.
DSR_SLACK_CARRIER = "demand_response"

# Nothing else may grow these sets without auditing every call site's intent —
# the cost decomposition and the capture must SPLIT the tiers, and the ENS cap
# must sum the involuntary tier only (spec §4.4).
INVOLUNTARY_SLACK_CARRIERS: frozenset[str] = frozenset(
    {INVOLUNTARY_SLACK_CARRIER, LEGACY_SLACK_CARRIER}
)
SLACK_CARRIERS: frozenset[str] = INVOLUNTARY_SLACK_CARRIERS | {DSR_SLACK_CARRIER}

# What current builds create slack names with (f"{PREFIX}{bus}").
VOLL_SLACK_PREFIX = "__voll_"
LEGACY_SLACK_PREFIX = "voll_slack_"
DSR_SLACK_PREFIX = "__dsr_"
INVOLUNTARY_SLACK_PREFIXES: tuple[str, ...] = (VOLL_SLACK_PREFIX, LEGACY_SLACK_PREFIX)
SLACK_NAME_PREFIXES: tuple[str, ...] = (
    VOLL_SLACK_PREFIX, LEGACY_SLACK_PREFIX, DSR_SLACK_PREFIX,
)


def is_slack_carrier(carrier: object) -> bool:
    """True when ``carrier`` marks an LP slack generator, not a real asset."""
    return isinstance(carrier, str) and carrier in SLACK_CARRIERS


def is_slack_name(name: object) -> bool:
    """True when the generator NAME follows a slack naming convention."""
    return isinstance(name, str) and name.startswith(SLACK_NAME_PREFIXES)


def strip_slack_prefix(name: str) -> str:
    """``__voll_<bus>`` → ``<bus>`` (prefix removal, not substring replace),
    so the bus name stands alone in result payloads."""
    return name.removeprefix(VOLL_SLACK_PREFIX)


def _tier_mask(generators: pd.DataFrame, carriers: frozenset[str],
               prefixes: tuple[str, ...]) -> pd.Series:
    name_str = generators.index.astype(str)
    mask = pd.Series(
        [n.startswith(prefixes) for n in name_str],
        index=generators.index,
        dtype=bool,
    )
    if "carrier" in generators.columns:
        mask |= generators["carrier"].astype(str).isin(carriers)
    return mask


def slack_generator_mask(generators: pd.DataFrame) -> pd.Series:
    """
    EVERY slack row, both tiers: carrier in SLACK_CARRIERS OR name carrying a
    slack prefix. This is the mask for hide-from-results / strip-before-PF /
    not-a-real-asset semantics. Tolerates an absent ``carrier`` column (name
    matching still applies) and an empty frame.
    """
    return _tier_mask(generators, SLACK_CARRIERS, SLACK_NAME_PREFIXES)


def involuntary_slack_mask(generators: pd.DataFrame) -> pd.Series:
    """
    The INVOLUNTARY tier only — what counts as unserved energy: the ENS cap
    sums these, the lost-load capture reports these, VoLL prices these.
    Demand response is deliberately excluded (spec §4.4).
    """
    return _tier_mask(
        generators, INVOLUNTARY_SLACK_CARRIERS, INVOLUNTARY_SLACK_PREFIXES)
