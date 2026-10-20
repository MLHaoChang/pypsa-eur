"""Other model series: prices, inflow, heat-pump COP (ticket 03).

One column per component id over the source year's hours:

  * ``tou`` → marginal_cost (generators or links): ``periods``, the tariff
    engine's own band model (`models.commercial.TariffPeriod`: name, rate,
    months, weekdays, [start_hour, end_hour) on the local clock), first
    match wins (`tariff_engine.period_rates`), every hour must be matched.
    ``currency``, ``price_basis`` and ``currency_year`` travel to apply,
    which refuses a price off the project's money basis. A link's price is
    per MWh of its bus0 flow (p0). A price on the point-of-connection Links
    is refused: a tariff belongs in the commercial configuration, which
    already prices them.
  * ``inflow`` → storage_units inflow: ``annual_mwh`` over a monthly shape —
    ``shape`` ("snowmelt", "rain_fed", "flat") or ``monthly`` (12 shares) —
    as a source-year-mean-normalised series (``per_unit_mean``), so apply's
    energy record holds the stated energy.
  * ``cop`` → links efficiency: η · T_sink / (T_sink − T_source) in kelvin,
    clipped to [1, cop_max]. ``source`` "air" takes T_source from the one
    assumed ambient (`dc_load_profile.ambient_c`, the diurnal term on
    ``timezone``'s wall clock); "ground" a constant ``source_c``.
  * ``part_load`` → refused: a part-load efficiency curve depends on the
    dispatch, which the linear LP cannot take as a time series; it needs a
    piecewise or committable formulation of its own.

Market price bundles (package C2) do not exist yet; a price series is the
user's own (``tou`` here, or an upload) until they do.

Every series here is stated, not measured: ``provenance: synthetic/assumed``.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from services.profile_gen.apply import ProfileApplyError

PROVENANCE = "synthetic/assumed"
KINDS = ("tou", "inflow", "cop", "part_load")
_AMBIENT = {"ambient_annual_mean_c": 10.0, "ambient_seasonal_amplitude_k": 9.0,
            "ambient_diurnal_amplitude_k": 5.0}
#: Monthly inflow shapes, January first (assumed, illustrative).
INFLOW_SHAPES = {
    "snowmelt": [0.3, 0.3, 0.6, 1.4, 2.4, 2.2, 1.4, 0.9, 0.7, 0.6, 0.5, 0.4],
    "rain_fed": [1.5, 1.4, 1.3, 1.0, 0.8, 0.6, 0.5, 0.5, 0.7, 1.0, 1.3, 1.5],
    "flat": [1.0] * 12,
}
_DEFAULTS: dict[str, dict[str, Any]] = {
    "tou": {"periods": None, "currency": None, "price_basis": None,
            "currency_year": None, "component_type": "generators"},
    "inflow": {"annual_mwh": None, "shape": None, "monthly": None},
    "cop": {"source": "air", "sink_c": 45.0, "source_c": 10.0, "eta": 0.45,
            "cop_max": 7.0, **_AMBIENT},
}
_COMMON = {"timezone": None}


class SeriesError(ProfileApplyError):
    """Base of the series family's typed refusals."""


class UnknownSeriesKindError(SeriesError):
    """No series kind of that name."""


class InvalidSeriesParamsError(SeriesError):
    """A series kind's parameters are missing, unknown or out of range."""


class PartLoadNotASeriesError(SeriesError):
    """A part-load efficiency curve is not a time series."""


class PocTariffError(SeriesError):
    """A price series on a point-of-connection Link, which the tariff prices."""


class CopBusError(SeriesError):
    """A COP on a link whose bus0 is not electricity (efficiency scales p0)."""


_ELECTRIC = {"ac", "dc", "electricity", "low voltage", "lv", "mv", "hv"}


def guard(ctx, component_type: str, component: str, meta: Mapping[str, Any]) -> None:
    """The series family's checks on the target component."""
    n = ctx.network
    if (component_type, meta.get("target", [None, None])[1]) == ("links", "marginal_cost"):
        state = getattr(ctx, "solver_state", None) or {}
        cfg = state.get("solver_config") if isinstance(state, Mapping) else None
        commercial = getattr(cfg, "commercial", None) or {}
        get = commercial.get if isinstance(commercial, Mapping) else (
            lambda k: getattr(commercial, k, None))
        if component in {get("poc_link"), get("export_link")} - {None}:
            raise PocTariffError(
                f"{component!r} is the point-of-connection link: its energy "
                "price comes from the tariff in the commercial configuration "
                "(import and export prices are added there); set the tariff "
                "instead of a price series")
    if meta.get("kind") == "cop" and component_type == "links":
        bus0 = str(n.links.at[component, "bus0"])
        carrier = str(n.buses.at[bus0, "carrier"] or "").lower() if bus0 in n.buses.index else ""
        if carrier not in _ELECTRIC:
            raise CopBusError(
                f"{component!r}'s bus0 {bus0!r} carries {carrier or 'nothing'}, "
                "not electricity; a COP scales the electric input p0")


def _num(p: Mapping[str, Any], key: str, lo: float | None = None,
         hi: float | None = None) -> float:
    raw = p[key]
    if isinstance(raw, bool):
        raise InvalidSeriesParamsError(f"{key} must be a number, got {raw!r}")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise InvalidSeriesParamsError(f"{key} must be a number, got {raw!r}") from None
    if not np.isfinite(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise InvalidSeriesParamsError(f"{key} must be in [{lo}, {hi}], got {v!r}")
    return v


def _local(idx: pd.DatetimeIndex, timezone: str | None) -> pd.DatetimeIndex:
    if not timezone:
        return idx
    try:
        return idx.tz_localize("UTC").tz_convert(timezone).tz_localize(None)
    except Exception as exc:  # noqa: BLE001 — pytz/zoneinfo raise many types
        raise InvalidSeriesParamsError(f"timezone {timezone!r}: {exc}") from None


def _tou(local: pd.DatetimeIndex, p: Mapping[str, Any]) -> tuple[np.ndarray, dict]:
    import pydantic

    from models.commercial import TariffPeriod
    from services.commercial.tariff_engine import period_rates

    raw = p["periods"]
    if not isinstance(raw, list) or not raw:
        raise InvalidSeriesParamsError("tou takes a non-empty list of periods")
    try:
        periods = [TariffPeriod.model_validate(x) for x in raw]
    except pydantic.ValidationError as exc:
        err = exc.errors()[0]
        raise InvalidSeriesParamsError(
            f"periods: {'.'.join(map(str, err['loc']))}: {err['msg']}") from None
    price = period_rates(periods, local)
    if np.isnan(price).any():
        first = local[np.isnan(price)][0]
        raise InvalidSeriesParamsError(
            f"the periods must price every hour; {first} matches none (add a "
            "catch-all period last)")
    currency = str(p["currency"] or "")
    meta = {"unit": f"{currency}/MWh", "price_basis": p["price_basis"],
            "currency_year": p["currency_year"],
            "price_min": float(price.min()), "price_max": float(price.max())}
    return price, meta


def _inflow(local: pd.DatetimeIndex, p: Mapping[str, Any]) -> tuple[np.ndarray, dict]:
    annual = _num(p, "annual_mwh", 0.0)
    if annual == 0.0:
        raise InvalidSeriesParamsError("annual_mwh must be positive")
    if (p["shape"] is None) == (p["monthly"] is None):
        raise InvalidSeriesParamsError("inflow takes one of shape or monthly")
    if p["shape"] is not None:
        if p["shape"] not in INFLOW_SHAPES:
            raise InvalidSeriesParamsError(
                f"shape must be one of {', '.join(INFLOW_SHAPES)}, got {p['shape']!r}")
        shares = INFLOW_SHAPES[p["shape"]]
    else:
        shares = p["monthly"]
        if not isinstance(shares, list) or len(shares) != 12:
            raise InvalidSeriesParamsError("monthly must be a list of 12 shares")
    weights = np.array([_num({"m": v}, "m", 0.0) for v in shares])
    if not weights.sum() > 0:
        raise InvalidSeriesParamsError("the monthly shares hold no inflow")
    v = weights[local.month.to_numpy() - 1]
    return v / v.mean(), {"unit": "per_unit_mean", "per_unit_mean_of": "source_year",
                          "scale_mw": annual / len(local)}


def _cop(idx: pd.DatetimeIndex, p: Mapping[str, Any], timezone) -> tuple[np.ndarray, dict]:
    from services.dc_load_profile import ambient_c

    source = p["source"]
    if source not in ("air", "ground"):
        raise InvalidSeriesParamsError(f"source must be air or ground, got {source!r}")
    eta = _num(p, "eta", 0.0, 1.0)
    if eta == 0.0:
        raise InvalidSeriesParamsError("eta must be in (0, 1]")
    cop_max = _num(p, "cop_max", 1.0, 20.0)
    sink = _num(p, "sink_c", 0.0, 120.0) + 273.15
    extra: dict[str, Any] = {}
    if source == "air":
        amb = {k: _num(p, k) for k in _AMBIENT}
        t_src = ambient_c(SimpleNamespace(**amb), idx, timezone) + 273.15
        extra["ambient"] = amb
    else:
        t_src = np.full(len(idx), _num(p, "source_c", -30.0, 60.0) + 273.15)
        if sink <= t_src[0]:
            raise InvalidSeriesParamsError(
                "a ground source at or above the sink temperature lifts no heat")
    lift = sink - t_src
    cop = np.where(lift > 0, eta * sink / np.where(lift > 0, lift, 1.0), cop_max)
    return np.clip(cop, 1.0, cop_max), {"unit": "ratio", **extra}


_TARGETS = {"inflow": ["storage_units", "inflow"], "cop": ["links", "efficiency"]}


def generate_frame(ids: list[str], kind: str, params: Mapping[str, Any],
                   year: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    if kind == "part_load":
        raise PartLoadNotASeriesError(
            "a part-load efficiency curve depends on the dispatch, which the "
            "linear LP cannot take as a time series; it needs a piecewise or "
            "committable formulation")
    if kind not in _DEFAULTS:
        raise UnknownSeriesKindError(
            f"unknown series kind {kind!r}; available: {', '.join(KINDS)}")
    allowed = {**_COMMON, **_DEFAULTS[kind]}
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise InvalidSeriesParamsError(
            f"{kind} does not take {', '.join(unknown)}; it takes {', '.join(sorted(allowed))}")
    p = {**allowed, **dict(params)}
    idx = pd.date_range(f"{year}-01-01T00:00", f"{year}-12-31T23:00", freq="h")
    tz = p["timezone"]
    local = _local(idx, tz)
    if kind == "tou":
        values, extra = _tou(local, p)
        ctype = p["component_type"]
        if ctype not in ("generators", "links"):
            raise InvalidSeriesParamsError(
                f"a price goes on generators or links, not {ctype!r}")
        target = [ctype, "marginal_cost"]
    elif kind == "inflow":
        values, extra = _inflow(local, p)
        target = _TARGETS[kind]
    else:
        values, extra = _cop(idx, p, tz)
        target = _TARGETS[kind]
    frame = pd.DataFrame({i: values for i in ids}, index=idx)
    stored = {k: v for k, v in p.items() if v is not None}
    meta = {"kind": kind, "params": stored, "target": target,
            "provenance": PROVENANCE,
            "source_time_basis": (f"UTC (from {tz} wall clock)" if tz
                                  else "UTC (assumed; site clock unknown)"),
            **extra}
    return frame, meta
