# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The caveat under an implausibly high "LCOE (real)" (te-24, owner decision LCOE-CAVEAT-1).

Above one fixed bound the figure keeps its value and gains one muted sentence. The
sentence names the bound. It gives a cause only when that cause is true for this
project: an EH site template with a must-stay-on share whose design holds genset
capacity (UX 32 rule 53). Any other case gets the neutral line. The headline card
carries the sentence, so the Guided tile, the Expert strip and Investment's
"LCOE (real)" row all show the same words.
"""

from __future__ import annotations

# Owner decision LCOE-CAVEAT-1 (option A): one fixed bound, in EUR per MWh.
LCOE_CAVEAT_BOUND_EUR_PER_MWH = 300.0

# The fuels the generic EH site builds its gensets on (`project_templates.eh_site._FUEL`).
BACKUP_CARRIERS = frozenset({"gas", "diesel"})

# The site-type words as the generic EH site names them (`eh_site._LABEL`), lower case.
_SITE_WORD = {
    "industrial": "an industrial plant",
    "commercial": "a commercial building",
    "hospital": "a hospital",
    "campus": "a campus",
}


def _bound_words() -> str:
    from services.reports.chapters import fmt_eur_short

    return f"Above {fmt_eur_short(LCOE_CAVEAT_BOUND_EUR_PER_MWH)}/MWh"


def critical_share(template: dict | None) -> float | None:
    """The generic EH site's must-stay-on share, None when the template has none."""
    params = (template or {}).get("params")
    share = params.get("critical_share") if isinstance(params, dict) else None
    if (
        (template or {}).get("site_type")
        and isinstance(share, (int, float))
        and share > 0
    ):
        return float(share)
    return None


def backup_mw(n) -> float:
    """The genset capacity in the design: the gas and diesel Generators' optimised
    capacity, or their entered capacity where the run did not optimise it."""
    gens = getattr(n, "generators", None)
    if gens is None or gens.empty or "carrier" not in gens.columns:
        return 0.0
    sel = gens[gens["carrier"].astype(str).isin(BACKUP_CARRIERS)]
    if sel.empty:
        return 0.0
    col = "p_nom_opt" if "p_nom_opt" in sel.columns else "p_nom"
    vals = sel[col].fillna(sel["p_nom"]) if col == "p_nom_opt" else sel[col]
    return float(vals.clip(lower=0).sum())


def lcoe_caveat(value, unit, *, template: dict | None, n) -> str | None:
    """The sentence under an LCOE above the bound, or None.

    Only a figure in EUR per MWh is compared: the bound is in EUR, and no exchange
    rate is guessed."""
    if not isinstance(value, (int, float)) or unit != "EUR/MWh":
        return None
    if not value > LCOE_CAVEAT_BOUND_EUR_PER_MWH:
        return None
    site = _SITE_WORD.get(
        str((template or {}).get("site_type") or ""), "a site like this"
    )
    if critical_share(template) is not None and backup_mw(n) > 0:
        return (
            f"{_bound_words()}, high for {site}: the backup kept for the "
            "critical load is costed but rarely runs."
        )
    return f"{_bound_words()}, high for {site}."
