"""Time to power of a data-centre project (DC 27; owner decision DC27-TTP-1).

First power is the first MW the grid delivers to a data-centre load in
service. A stated ``connection_date`` (the grid connection's energisation
date) wins. Otherwise it is the staged build's first serving stage: the first
model period whose energised import is above 0 MW for the whole period (the
schedule's minimum in it) while a data-centre phase is in service by the
period's end. Its date is the later of the period's start and the first
phase's COD, so it is only as fine as the period: the true first power is no
later and may be up to a period earlier (``resolution_months``). A period
whose energisation is the example's same-stage placeholder is never serving
evidence. Grid-delivered power only: on-site generation that could carry
the load before energisation is not counted.

The figure is calendar months from the stated ``project_start``, a partial
month rounded up, so the wait is never understated. A missing input reads
not available with its reason (``REASONS``), never 0.
"""

from __future__ import annotations

import calendar
from datetime import date

import pandas as pd

from services.adequacy import energization

ROLE = "grid_import"
#: Each not-available reason and its words.
REASONS = {
    "no_dc_load_spec": "the project was not created with a data-centre load",
    "no_project_start": "the project start date is not set",
    "no_serving_stage": ("no connection date is set and no stage of the build has grid power "
                         "while data-centre load is in service"),
    "power_before_start": "first power falls before the project start date",
    "energisation_unset": ("a stage's energised MW is the example's placeholder, not a stated "
                           "value, so it cannot show when power arrives"),
    "import_link_missing": "the network has no grid import link",
    "import_link_ambiguous": "the network has more than one grid import link",
    "import_unsized": "the grid import is sized by the solve, so its energised MW is not stated",
}


class StagesUnavailable(ValueError):
    """The saved network gives no stages; ``reason`` is a ``REASONS`` key."""

    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def add_months(d: date, k: int) -> date:
    """``d`` plus ``k`` calendar months, the day clamped to the month's end."""
    y, m = divmod(d.month - 1 + k, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def months_between(start: date, end: date) -> int:
    """Calendar months from ``start`` to ``end`` (``end`` ≥ ``start``), a
    partial month rounded up."""
    k = (end.year - start.year) * 12 + end.month - start.month
    # ``start`` plus k months falls in ``end``'s month; one month fewer falls before it.
    return k + 1 if add_months(start, k) < end else k


def _import_link(n) -> str | None:
    roles = n.links["eh_role"] if "eh_role" in n.links.columns else pd.Series(dtype=object)
    links = list(roles.index[roles == ROLE])
    if not links:
        raise StagesUnavailable("import_link_missing")
    scheduled = [link for link in links if link in energization.energized_links(n)]
    if len(scheduled) > 1 or (not scheduled and len(links) > 1):
        raise StagesUnavailable("import_link_ambiguous", ", ".join(scheduled or links))
    return scheduled[0] if scheduled else links[0]


def _unset_periods(meta: dict | None) -> set[str]:
    return {str(g.get("year")) for g in (meta or {}).get("growth_inputs") or []
            if isinstance(g, dict) and g.get("kind") == "energized_mw" and g.get("set") is False}


def import_stages(n, meta: dict | None) -> list[dict]:
    """The grid import's stages of the saved network: per model period, its
    start, end (exclusive), the schedule's minimum MW in it and whether that
    MW is stated (``set``) or a placeholder. A single-period network is one
    stage, the modelled year. ``StagesUnavailable`` names why there are none."""
    link = _import_link(n)
    by_period = energization.energized_mw_by_period(n, link)
    unset = _unset_periods(meta)

    def energized(period):
        # A period the schedule leaves without a finite value has no
        # schedule limit (`services.adequacy.energization`): the link's own
        # fixed size, never 0; a size the solve chooses is not stated.
        mw = (by_period or {}).get(period)
        if mw is not None:
            return float(mw)
        if n.links.at[link, "p_nom_extendable"]:
            raise StagesUnavailable("import_unsized", link)
        return float(n.links.at[link, "p_nom"])

    if isinstance(n.snapshots, pd.MultiIndex):
        years = n.investment_period_weightings["years"]
        out = []
        for p in pd.unique(n.snapshots.get_level_values(0)):
            start = date(int(p), 1, 1)
            months = int(round(float(years.loc[p]) * 12))
            out.append({"period": str(p), "start": start.isoformat(),
                        "end": add_months(start, months).isoformat(),
                        "energized_mw": energized(str(p)), "set": str(p) not in unset})
        return out
    year = pd.DatetimeIndex(n.snapshots)[0].year
    mw = energized("all")
    return [{"period": "all", "start": date(year, 1, 1).isoformat(),
             "end": date(year + 1, 1, 1).isoformat(), "energized_mw": float(mw), "set": True}]


def _not_available(reason: str, detail: str | None = None) -> dict:
    return {"status": "not_available", "reason": reason, "detail": detail, "words": REASONS[reason]}


def _date(v) -> date | None:
    return None if v is None else date.fromisoformat(str(v))


def time_to_power(spec: dict | None, stages: list[dict], stage_reason: dict | None = None) -> dict:
    """The fact from the stored spec's dates and phases and the import's
    stages (``import_stages``, or ``stage_reason`` when it refused)."""
    if spec is None:
        return _not_available("no_dc_load_spec")
    start = _date(spec.get("project_start"))
    if start is None:
        return _not_available("no_project_start")
    connection = _date(spec.get("connection_date"))
    if connection is not None:
        first, source, period, resolution = connection, "connection_date", None, 0
    else:
        if stage_reason is not None:
            return _not_available(stage_reason["reason"], stage_reason.get("detail"))
        cods = sorted(date.fromisoformat(str(p["cod"])) for p in spec.get("phases") or [])
        served = None
        for s in stages:
            s_start, s_end = date.fromisoformat(s["start"]), date.fromisoformat(s["end"])
            in_service = bool(cods) and cods[0] < s_end
            if not in_service:
                continue
            if not s["set"]:
                return _not_available("energisation_unset", s["period"])
            if s["energized_mw"] > 0:
                served = s
                first = max(s_start, cods[0])
                break
        if served is None:
            return _not_available("no_serving_stage")
        source, period = "staged_build", served["period"]
        resolution = months_between(date.fromisoformat(served["start"]), date.fromisoformat(served["end"]))
    if first < start:
        return _not_available("power_before_start", first.isoformat())
    return {"status": "available", "months": months_between(start, first), "first_power": first.isoformat(),
            "source": source, "stage": period, "resolution_months": resolution}
