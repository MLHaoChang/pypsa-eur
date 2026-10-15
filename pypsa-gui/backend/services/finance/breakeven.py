"""What the island capability would have to be worth for the investment to
break even (owner decision 2026-10-15).

On the "site as it stands today" basis the case compares against a baseline
that is solved without the island rows when it cannot meet them: the island
capability is what the new builds buy, and the case does not price it. The
NPV stays as computed; beside it, this states the yearly value that would net
it to 0, and that value per hour of grid outage the site rides through and per
MWh of critical load carried, from the network's own outage data. No value of
lost load is assumed.
"""
from __future__ import annotations

import dataclasses

from services.finance.case import TemplateLine

_KEY = "island_capability_breakeven_unit"


def _flag_value(flags, prefix: str) -> float | None:
    for f in flags:
        if f.startswith(prefix):
            try:
                return float(f[len(prefix):])
            except ValueError:
                return None
    return None


def island_breakeven(result, case) -> dict | None:
    """`{eur_per_year, outage_hours_per_year, eur_per_outage_hour,
    islanded_mwh_per_year, eur_per_mwh_islanded, text}` when the case's
    baseline relaxed the island rows and its pre-tax NPV is established; else
    None. The yearly value is exact for the engine: the NPV of 1 EUR a year
    (base money, escalating as opex, every operating year) comes from the
    engine itself, so the break-even is −NPV / that."""
    from services.finance.engine import run_case

    if "baseline_island_rows_relaxed" not in case.flags:
        return None
    npv = result.metrics.get("project_pre_tax_npv")
    if npv is None:
        return None
    unit = tuple(dataclasses.replace(t, lines=t.lines + (TemplateLine(
        key=_KEY, stream="other", amount=1.0, esc_class="opex",
        period=t.lines[0].period if t.lines else None, money_year=case.base_year),))
        for t in case.templates)
    per_unit = run_case(dataclasses.replace(case, templates=unit)).metrics.get(
        "project_pre_tax_npv")
    if per_unit is None or per_unit - npv <= 0:
        return None
    eur = max(0.0, -npv) / (per_unit - npv)
    hours = _flag_value(case.flags, "island_exposure_hours_per_year:")
    mwh = _flag_value(case.flags, "island_exposure_mwh_per_year:")
    per_h = eur / hours if hours else None
    per_mwh = eur / mwh if mwh else None
    if eur == 0.0:
        text = ("The investment breaks even without valuing the island capability. "
                "No value of lost load is assumed.")
    else:
        parts = [f"For the investment to break even, the island capability would have to be "
                 f"worth about EUR {eur:,.0f} a year ({case.base_year} money, escalating with "
                 "opex)"]
        if per_h is not None:
            parts.append(f", about EUR {per_h:,.0f} per hour of grid outage ridden through "
                         f"({hours:g} h a year expected)")
        if per_mwh is not None:
            parts.append(f", or EUR {per_mwh:,.0f} per MWh of critical load carried through them")
        text = "".join(parts) + ". No value of lost load is assumed."
    return {"eur_per_year": eur, "outage_hours_per_year": hours, "eur_per_outage_hour": per_h,
            "islanded_mwh_per_year": mwh, "eur_per_mwh_islanded": per_mwh, "text": text}
