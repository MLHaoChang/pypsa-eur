"""
Networks a decision study wrote before it moved onto the Investment Case
engine (U2 plan WP10, gate WP6 W4; docs/superpowers/plans/
2026-10-05-guided-study-u2-engine-rewire.md).

Before U2 a study's base project and option forks carried the tariff in two
pieces: the energy, network and export prices as PERMANENT
``links_t.marginal_cost`` on the meter Links (``grid_import``,
``grid_export``), and the demand charge in ``SolverConfig.demand_charge``,
which the study's own LP wrapper read. U2 deleted that field and the wrapper.
A saved ``solver_config.json`` that still names the field loads without it
(``routers.projects._solver_config_from_dict`` keeps only live keys), and any
save of the project then rewrites the file without it, so the network is the
only reliable witness.

Re-solving such a network in the Expert face would keep the energy prices and
drop the demand charge — a cheaper tariff than the study's, with nothing said
— or, with an engine ``commercial`` block added, price the tariff twice.
Rebuilding the two pieces as an engine tariff is not exact (the Links hold an
8,760-h price series, not the bands and charges it came from), so the solve is
refused instead.

The signature is a decision-study pack network (``n.meta`` carries
``packs.PACK_META_KEY``) WITHOUT the engine mark (``packs.TARIFF_ON_KEY``:
``"engine"`` in the pack record, written by every network the study builds
since U2; final U2 gate, condition U2) whose meter Links carry a non-zero
price, static or per snapshot. A network without the pack marker is the
user's own and its Link prices are the user's tariff; a marked network's Link
price is a price the user typed after the study wrote it, an ordinary Expert
edit. Neither is this module's business.

The way out works for any copy of such a network (the study's base, an
option fork, a Save-As, a scenario, a bundle): clear the two Links' prices
(the refusal lifts, since an unpriced meter Link is not the old tariff; the
network then has no tariff unless one is added in the commercial settings),
or start a new study for the same site. Re-running the old study is not
offered: since U2 the runner rebuilds only the option forks, never the base
project's network, so it cannot clear a pre-U2 base (gate S2 b).

`validation_service.validate_for_run` reports :func:`preflight_findings` as an
error, so the pre-run check and every solve (``run_simulation`` runs the same
preflight) refuse with :data:`CODE`.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

__all__ = ["CODE", "is_pre_u2", "preflight_findings", "tariff_links"]

#: The refusal's stable code (`[VALIDATION] ERROR: ... [<code>]`).
CODE = "study_network_tariff_in_links"

# The pack's own names, held here so the preflight imports nothing heavy;
# `tests/test_u2_wp10_pre_u2_resolve.py` pins them to `services.study.packs`.
PACK_META_KEY = "decision_study_pack"
TARIFF_ON_KEY = "tariff_on"
TARIFF_ON_ENGINE = "engine"
METER_LINKS = ("grid_import", "grid_export")


def is_pre_u2(n) -> bool:
    """A decision-study pack network written before U2: the pack marker, no engine mark."""
    meta = getattr(n, "meta", None)
    if not isinstance(meta, Mapping):
        return False
    pack = meta.get(PACK_META_KEY)
    return isinstance(pack, Mapping) and pack.get(TARIFF_ON_KEY) != TARIFF_ON_ENGINE


def _priced(n) -> list[tuple[str, bool]]:
    """``(link, hourly)`` for each meter Link that carries a non-zero price."""
    links = n.links
    if links.empty:
        return []
    varying = n.links_t.marginal_cost
    out = []
    for link in METER_LINKS:
        if link not in links.index:
            continue
        hourly = link in varying.columns
        if hourly:
            priced = bool(np.any(np.nan_to_num(varying[link].to_numpy(dtype=float)) != 0.0))
        else:
            priced = float(np.nan_to_num(float(links.at[link, "marginal_cost"]))) != 0.0
        if priced:
            out.append((link, hourly))
    return out


def tariff_links(n) -> list[str]:
    """The meter Links of a pre-U2 decision-study network that carry a permanent price."""
    if not is_pre_u2(n):
        return []
    return [link for link, _hourly in _priced(n)]


def _named(links: list[str]) -> str:
    return " and ".join(f"'{x}'" for x in links)


def _message(priced: list[tuple[str, bool]]) -> str:
    links = [link for link, _h in priced]
    hourly = [link for link, h in priced if h]
    static = [link for link, h in priced if not h]
    steps = []
    if hourly:
        its = ("its price is hourly, so the static price in Properties does not replace it"
               if len(hourly) == 1 else
               "their prices are hourly, so the static price in Properties does not replace them")
        steps.append(f"in Time series, Links, Marginal cost, upload a profile of zeros for "
                     f"{_named(hourly)} ({its})")
    if static:
        steps.append(f"in Properties, set the marginal cost of {_named(static)} to 0")
    return (
        "This network was built by an earlier version of the decision study. The tariff's "
        f"energy prices are stored on the meter Links {_named(links)}, and its demand charge "
        "was a solver setting this version no longer reads. Solved as it is, the network "
        "would keep the energy prices and leave out the demand charge, a cheaper tariff "
        "than the study's; with a tariff added in the commercial settings it would charge "
        "the energy twice. To go on, either start a new study for the same site from a "
        "question card, which builds its networks on the current engine, or clear the "
        f"old prices: {'; and '.join(steps)}. With them cleared, no tariff applies to this "
        "network until you add one in the commercial settings.")


def preflight_findings(n) -> list[tuple[str, str, str, str, str]]:
    """``(severity, code, component class, name, message)``: one error, or none."""
    if not is_pre_u2(n):
        return []
    priced = _priced(n)
    if not priced:
        return []
    return [("error", CODE, "Link", priced[0][0], _message(priced))]
