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

# Phase 1 adds "demand_response" here. Nothing else may grow this set without
# auditing every is_slack_carrier() call site's intent — in particular the
# solve-log cost decomposition must split DSR out of voll_shed_*, not lump it
# in (spec §4.4).
SLACK_CARRIERS: frozenset[str] = frozenset(
    {INVOLUNTARY_SLACK_CARRIER, LEGACY_SLACK_CARRIER}
)

# What current builds create slack names with (f"{VOLL_SLACK_PREFIX}{bus}").
VOLL_SLACK_PREFIX = "__voll_"
LEGACY_SLACK_PREFIX = "voll_slack_"
SLACK_NAME_PREFIXES: tuple[str, ...] = (VOLL_SLACK_PREFIX, LEGACY_SLACK_PREFIX)


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


def slack_generator_mask(generators: pd.DataFrame) -> pd.Series:
    """
    Boolean mask over ``generators`` (a ``n.generators``-shaped frame)
    selecting every slack row: carrier in SLACK_CARRIERS OR name carrying a
    slack prefix. Tolerates an absent ``carrier`` column (name matching still
    applies) and an empty frame.
    """
    name_str = generators.index.astype(str)
    mask = pd.Series(
        [n.startswith(SLACK_NAME_PREFIXES) for n in name_str],
        index=generators.index,
        dtype=bool,
    )
    if "carrier" in generators.columns:
        mask |= generators["carrier"].astype(str).isin(SLACK_CARRIERS)
    return mask
