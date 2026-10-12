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
refused instead, with the way out: re-run the study, which rebuilds its
networks with the tariff on the engine.

The signature is a decision-study pack network (``n.meta`` carries
``packs.PACK_META_KEY``) whose meter Links carry a non-zero price, static or
per snapshot. Since U2 WP6 the pack writes no Link price (the engine prices
the meter at solve and restores the column), so a network the study writes
today never matches. A network without the pack marker is the user's own and
its Link prices are the user's tariff: never this module's business.

`validation_service.validate_for_run` reports :func:`preflight_findings` as an
error, so the pre-run check and every solve (``run_simulation`` runs the same
preflight) refuse with :data:`CODE`.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

__all__ = ["CODE", "preflight_findings", "tariff_links"]

#: The refusal's stable code (`[VALIDATION] ERROR: ... [<code>]`).
CODE = "study_network_tariff_in_links"

# The pack's own names, held here so the preflight imports nothing heavy;
# `tests/test_u2_wp10_pre_u2_resolve.py` pins them to `services.study.packs`.
PACK_META_KEY = "decision_study_pack"
METER_LINKS = ("grid_import", "grid_export")


def tariff_links(n) -> list[str]:
    """The meter Links of a decision-study network that carry a permanent price."""
    meta = getattr(n, "meta", None)
    if not isinstance(meta, Mapping) or not isinstance(meta.get(PACK_META_KEY), Mapping):
        return []
    links = n.links
    if links.empty:
        return []
    varying = n.links_t.marginal_cost
    out = []
    for link in METER_LINKS:
        if link not in links.index:
            continue
        if link in varying.columns:
            priced = bool(np.any(np.nan_to_num(varying[link].to_numpy(dtype=float)) != 0.0))
        else:
            priced = float(np.nan_to_num(float(links.at[link, "marginal_cost"]))) != 0.0
        if priced:
            out.append(link)
    return out


def _message(links: list[str]) -> str:
    named = " and ".join(f"'{x}'" for x in links)
    return (
        f"This network was built by an earlier version of the decision study: the tariff's "
        f"energy prices are stored as fixed prices on {named}, and its demand charge was a "
        "solver setting this version no longer reads. Solving it now would keep the energy "
        "prices and leave out the demand charge — a cheaper tariff than the study's — or, "
        "with a tariff added in the commercial settings, charge the energy twice. Re-run the "
        "study to rebuild its networks with the tariff on the current engine.")


def preflight_findings(n) -> list[tuple[str, str, str, str, str]]:
    """``(severity, code, component class, name, message)``: one error, or none."""
    links = tariff_links(n)
    if not links:
        return []
    return [("error", CODE, "Link", links[0], _message(links))]
