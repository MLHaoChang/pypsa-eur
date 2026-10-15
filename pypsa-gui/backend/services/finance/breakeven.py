"""What the island capability would have to be worth for the investment to
break even (owner decision 2026-10-15).

On the "site as it stands today" basis the case compares against a baseline
that is solved without the island rows when it cannot meet them: the island
capability is what the new builds buy, and the case does not price it. The
NPV stays as computed; beside it, this states the yearly value that would net
it to 0, a threshold, never added to the NPV, the cash flows or the hash.
Per outage hour and per MWh of critical load only from the outage hours the
user states with their source (`FinanceInputs.island_outage`): with none,
those read "needs an outage duration". No value of lost load is assumed.
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
    """`{eur_per_year, outage_hours_per_year, outage_source, eur_per_outage_hour,
    islanded_mwh_per_year, eur_per_mwh_islanded, needs_outage_duration, text}`
    (the per-hour and per-MWh figures None without the user's outage hours;
    `eur_per_year` 0 when the NPV is already positive) when the case's
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
    outage = getattr(case.inputs, "island_outage", None)
    hours = float(outage.hours_per_year) if outage is not None else None
    source = outage.source if outage is not None else None
    load_mw = _flag_value(case.flags, "island_critical_load_mw:")
    mwh = hours * load_mw if hours and load_mw else None
    per_h = eur / hours if hours else None
    per_mwh = eur / mwh if mwh else None
    if eur == 0.0:
        text = ("Already positive without valuing the island capability. "
                "No value of lost load is assumed.")
    else:
        text = (f"The island capability must be worth at least EUR {eur:,.0f} a year "
                f"({case.base_year} money, escalating with opex) for the plan to break even")
        if per_h is not None:
            text += (f" (≈ EUR {per_h:,.0f} per avoided outage hour at {hours:g} h a year, "
                     f"source: {source}")
            if per_mwh is not None:
                text += f"; ≈ EUR {per_mwh:,.0f} per MWh of critical load carried"
            text += ")"
        text += ". "
        if per_h is None:
            text += ("Per outage hour: needs an outage duration (enter the expected outage "
                     "hours a year and their source). ")
        text += "No value of lost load is assumed."
    return {"eur_per_year": eur, "outage_hours_per_year": hours, "outage_source": source,
            "eur_per_outage_hour": per_h, "islanded_mwh_per_year": mwh,
            "eur_per_mwh_islanded": per_mwh, "needs_outage_duration": hours is None,
            "text": text}
