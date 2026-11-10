"""
The IT energy series the data-centre business case bills (DC business case 03).

Revenue reads the spec, never the network's loads: ``it_load`` carries the
critical share of the *facility* load and ``cooling`` the rest, so neither is
the IT load. The billable IT energy is

    IT MWh(t) = IT MW in service(t) × u(t) × w(t) − unserved IT MWh(t)

with u(t) from ``dc_load_profile.utilisation`` (the project's timezone) and
w(t) the generators' snapshot weighting: the same series the facility load
``dc_load_profile.hourly_profile`` was built from, divided by PUE(T(t)).

Unserved IT energy (GK A, issue 03 Q4) is the involuntary unserved facility
energy ÷ PUE(T(t)). Facility unserved = the involuntary slack on ``it_bus``
plus the cooling share of the involuntary slack on ``dc_mv`` (pro rata
cooling(t) ÷ every load on ``dc_mv``(t), since the offices sit there too).
DC 07 curtailment and demand response are not involuntary and lower nothing:
holding IT revenue whole under curtailment is the model's claim that the
curtailable share is non-IT load. The unserved IT MWh is a quantity only;
nothing here prices it (no VoLL), and it never enters the IC or adequacy
facts.

``workload_shift_window_h`` moves no load (DC 06), so nothing here reads it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from models.flex_archetypes import DataCentreLoadSpec, DcPhase

#: The data-centre templates' bus and load names the unserved rule reads.
IT_BUS = "it_bus"
MV_BUS = "dc_mv"
COOLING_LOAD = "cooling"

#: A phase without ``rack_kw`` has no rack count (BC3-7).
NOT_SET = "not set"


def phase_in_service(spec: DataCentreLoadSpec, on: date) -> DcPhase | None:
    """The phase in service on ``on``: the last whose COD is on or before it
    (phases are cumulative and ordered by COD), or None before the first COD.
    The data-centre templates build their loads by this rule."""
    live = [p for p in spec.phases if p.cod <= on]
    return live[-1] if live else None


def _timesteps(snapshots: pd.Index) -> pd.DatetimeIndex:
    if isinstance(snapshots, pd.MultiIndex):
        return pd.DatetimeIndex(snapshots.get_level_values(-1))
    return pd.DatetimeIndex(snapshots)


def _periods(snapshots: pd.Index) -> np.ndarray:
    """The period of each snapshot: the investment period of a multi-period
    index, else the snapshot's year."""
    if isinstance(snapshots, pd.MultiIndex):
        return np.asarray(snapshots.get_level_values(0), dtype=int)
    return pd.DatetimeIndex(snapshots).year.to_numpy()


def _phase_index(spec: DataCentreLoadSpec, snapshots: pd.Index) -> np.ndarray:
    """The index into ``spec.phases`` of the phase in service on each snapshot
    (-1 before the first COD); see ``in_service_it_mw`` for the rule."""
    if isinstance(snapshots, pd.MultiIndex):
        days = [date(int(y), 12, 31) for y in _periods(snapshots)]
    else:
        days = [t.date() for t in pd.DatetimeIndex(snapshots)]
    cache: dict[date, int] = {}
    out = np.empty(len(days), dtype=int)
    for i, d in enumerate(days):
        if d not in cache:
            live = phase_in_service(spec, d)
            cache[d] = -1 if live is None else spec.phases.index(live)
        out[i] = cache[d]
    return out


def in_service_it_mw(spec: DataCentreLoadSpec, snapshots) -> np.ndarray:
    """The IT MW in service on each snapshot. A multi-period index takes, for
    the whole period, the phase in service by the period's year end (the staged
    template's rule: one phase per period, by COD year); a single index takes
    each snapshot's own date, so a COD bills from its first in-service snapshot.
    0 before the first COD."""
    idx = _phase_index(spec, snapshots)
    return _it_mw(spec, idx)


def _it_mw(spec: DataCentreLoadSpec, idx: np.ndarray) -> np.ndarray:
    # A trailing 0 so idx -1 (before the first COD) reads 0 MW.
    return np.array([p.it_mw for p in spec.phases] + [0.0])[idx]


def unserved_facility_mw(n) -> pd.Series:
    """The involuntary unserved facility MW on each snapshot of a solved
    data-centre network (GK A, issue 03 Q4): the involuntary slack on
    ``it_bus`` plus the cooling share of the involuntary slack on ``dc_mv``.
    DC curtailment and every other non-involuntary slack carrier are left out.
    Zero when the solve added no slack."""
    from services.adequacy.slack import involuntary_slack_mask

    gens = n.generators
    slack = gens.index[involuntary_slack_mask(gens).to_numpy()]
    p = n.generators_t.p.reindex(index=n.snapshots, columns=slack).fillna(0.0)
    bus = gens.loc[slack, "bus"]
    on_it = p.loc[:, bus.index[bus == IT_BUS]].sum(axis=1)
    on_mv = p.loc[:, bus.index[bus == MV_BUS]].sum(axis=1)
    loads = n.get_switchable_as_dense("Load", "p_set")
    mv_loads = loads.loc[:, n.loads.index[n.loads.bus == MV_BUS]]
    total = mv_loads.sum(axis=1)
    cooling = (mv_loads[COOLING_LOAD] if COOLING_LOAD in mv_loads.columns
               else pd.Series(0.0, index=n.snapshots))
    share = (cooling / total).fillna(0.0)               # 0/0 (no MV load) → 0
    return (on_it + share * on_mv).reindex(n.snapshots).fillna(0.0)


@dataclass(frozen=True)
class ItEnergy:
    """Per period: ``it_mwh`` (IT MW in service × u × w), ``unserved_it_mwh``
    (a quantity, never priced), ``billable_it_mwh`` (their difference) and
    ``contracted_it_kw`` (the in-service IT kW, weighted over the period's
    snapshots, so a phase bills from its COD). ``phases``: per phase its COD,
    contracted IT kW, first in-service snapshot and rack count (``it_mw`` ÷
    ``rack_kw`` × 1000, or "not set")."""

    periods: pd.DataFrame
    phases: list[dict] = field(default_factory=list)
    hourly: pd.DataFrame = field(default_factory=pd.DataFrame)


def it_energy(spec: DataCentreLoadSpec, snapshots, weights, unserved=None, *,
              timezone: str | None = None) -> ItEnergy:
    """The billable IT energy per period (module docstring). ``weights`` are
    the generators' snapshot weightings (h per snapshot); ``unserved`` the
    involuntary unserved facility MW per snapshot (``unserved_facility_mw``),
    or None for a plan with no unserved energy. The unserved IT is capped at
    each snapshot's IT energy, so it never makes billable energy negative."""
    from services.dc_load_profile import ambient_c, pue, utilisation

    steps = _timesteps(snapshots)
    w = (weights.reindex(snapshots).to_numpy(dtype=float) if isinstance(weights, pd.Series)
         else np.broadcast_to(np.asarray(weights, dtype=float), (len(snapshots),)))
    idx = _phase_index(spec, snapshots)
    it_mw = _it_mw(spec, idx)
    u = utilisation(spec, steps, timezone)
    it_mwh = it_mw * u * w
    if unserved is None:
        lost = np.zeros(len(snapshots))
    else:
        facility = (unserved.reindex(snapshots).fillna(0.0).to_numpy(dtype=float)
                    if isinstance(unserved, pd.Series) else np.asarray(unserved, dtype=float))
        lost = np.clip(facility * w / pue(spec, ambient_c(spec, steps, timezone)), 0.0, it_mwh)
    hourly = pd.DataFrame({
        "period": _periods(snapshots), "it_mw": it_mw, "weight": w,
        "it_mwh": it_mwh, "unserved_it_mwh": lost, "billable_it_mwh": it_mwh - lost,
        "it_kw_h": it_mw * 1000.0 * w,
    }, index=snapshots)
    g = hourly.groupby(pd.Index(hourly["period"].to_numpy(), name="period"))
    periods = g[["it_mwh", "unserved_it_mwh", "billable_it_mwh"]].sum()
    hours = g["weight"].sum()
    periods["contracted_it_kw"] = (g["it_kw_h"].sum() / hours).fillna(0.0)  # 0/0 → 0
    phases = []
    for k, p in enumerate(spec.phases):
        live = np.flatnonzero(idx >= k)
        phases.append({
            "cod": p.cod.isoformat(), "it_kw": p.it_mw * 1000.0,
            "first_in_service": snapshots[live[0]] if len(live) else None,
            "rack_count": p.it_mw / p.rack_kw * 1000.0 if p.rack_kw else NOT_SET,
        })
    return ItEnergy(periods=periods, phases=phases, hourly=hourly.drop(columns="it_kw_h"))
