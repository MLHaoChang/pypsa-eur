"""
Synthetic load / generator / link profile shapes, and the carrier
classification that picks between them.

Extracted verbatim from `routers/network.py`; `routers.network` re-exports
every name. These are the closed-form shapes behind the downloadable profile
templates — deterministic functions of a snapshot index, with no I/O and no
network. `_template_snapshots` raises `HTTPException` for an unusable snapshot
index, as it always did.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import HTTPException


# Carrier-keyword sets used to classify loads into energy-vector sections.
_ELEC_CARRIERS = {
    "ac", "dc", "electricity", "elec", "low voltage", "medium voltage",
    "high voltage", "lv", "mv", "hv", "ehv",
}


_H2_CARRIERS_LOAD = {"h2", "hydrogen", "h2 pipeline"}


_HEAT_CARRIERS = {
    "heat", "low temperature heat", "urban heat", "rural heat",
    "space heat", "water tank", "central heat", "district heat",
    "low-t heat", "low t heat",
}


def _load_section(n, load_name: str) -> str:
    """
    Classify a load as electricity / hydrogen / heat / other.

    Looks up the load's bus, then the bus's carrier. Empty/AC default carriers
    fall through to 'electricity' (PyPSA's default bus carrier is 'AC').
    """
    try:
        bus = str(n.loads.at[load_name, "bus"]) if "bus" in n.loads.columns else ""
        carrier = str(n.buses.at[bus, "carrier"]).lower().strip() if bus in n.buses.index else ""
    except Exception:
        carrier = ""
    if not carrier:
        return "electricity"
    if carrier in _H2_CARRIERS_LOAD or "hydrogen" in carrier:
        return "hydrogen"
    if carrier in _HEAT_CARRIERS or "heat" in carrier or "water tank" in carrier:
        return "heat"
    if carrier in _ELEC_CARRIERS:
        return "electricity"
    return "other"


def _h2_load_profile(
    snapshots: pd.DatetimeIndex,
    p_max: float,
    noise_seed: int,
    weekend_factor: float = 0.60,
    noise_pct: float = 0.02,
) -> np.ndarray:
    """
    Industrial-style hydrogen demand: roughly flat during weekdays
    (production runs), reduced on weekends. Normalised so a typical weekday
    hour equals p_max.
    """
    rng = np.random.default_rng(noise_seed)
    hours = snapshots.hour.values.astype(float)
    dow = snapshots.dayofweek.values
    # Slight mid-shift dip in the early morning to avoid a perfectly flat line.
    base = 0.95 + 0.05 * np.cos((hours - 14.0) * np.pi / 12.0) * 0.3
    wf = np.where(dow >= 5, weekend_factor, 1.0)
    noise = rng.uniform(-noise_pct, noise_pct, size=len(snapshots))
    values = p_max * base * wf * (1.0 + noise)
    return np.round(np.maximum(values, 0.0), 3)


def _heat_load_profile(
    snapshots: pd.DatetimeIndex,
    p_max: float,
    noise_seed: int,
    weekend_factor: float = 0.95,
    noise_pct: float = 0.04,
) -> np.ndarray:
    """
    Heat demand: pronounced morning + evening peaks, low overnight, weekends
    only marginally lower. Normalised so the evening peak equals p_max.
    Seasonal scaling (winter > summer) is left to the caller — for a 1-week
    template the daily shape is what matters.
    """
    rng = np.random.default_rng(noise_seed)
    hours = snapshots.hour.values.astype(float)
    dow = snapshots.dayofweek.values

    morning = 0.85 * np.exp(-0.5 * ((hours - 7.0) / 1.8) ** 2)
    evening = 1.00 * np.exp(-0.5 * ((hours - 20.0) / 2.0) ** 2)
    daytime = 0.35 * np.exp(-0.5 * ((hours - 13.0) / 4.0) ** 2)
    raw = morning + evening + daytime + 0.10  # baseline overnight floor

    norm = (1.00 * np.exp(-0.5 * ((20.0 - 20.0) / 2.0) ** 2)
            + 0.85 * np.exp(-0.5 * ((20.0 - 7.0) / 1.8) ** 2)
            + 0.35 * np.exp(-0.5 * ((20.0 - 13.0) / 4.0) ** 2)
            + 0.10)
    shape = raw / norm

    wf = np.where(dow >= 5, weekend_factor, 1.0)
    noise = rng.uniform(-noise_pct, noise_pct, size=len(snapshots))
    values = p_max * shape * wf * (1.0 + noise)
    return np.round(np.maximum(values, 0.0), 3)


def _double_peak_profile(
    snapshots: pd.DatetimeIndex,
    p_max: float,
    noise_seed: int,
    weekend_factor: float = 0.80,
    noise_pct: float = 0.03,
) -> np.ndarray:
    """
    Realistic hourly load profile for one week.

    Shape: two Gaussian peaks (morning ~09:00, evening ~19:00) with a soft
    overnight trough, normalised so the evening peak equals p_max.
    Weekend days (Sat/Sun) are scaled by weekend_factor.
    Independent ±noise_pct random variation per load (seeded for reproducibility).
    """
    rng = np.random.default_rng(noise_seed)
    hours = snapshots.hour.values.astype(float)
    dow   = snapshots.dayofweek.values          # 0=Mon … 6=Sun

    morning = 0.75 * np.exp(-0.5 * ((hours - 9.0)  / 2.5) ** 2)
    evening = 1.00 * np.exp(-0.5 * ((hours - 19.0) / 2.5) ** 2)
    trough  = 0.20 * np.exp(-0.5 * ((hours - 3.0)  / 2.0) ** 2)
    raw     = morning + evening + trough

    # Normalise so the theoretical evening peak = 1
    norm = (1.00 * np.exp(-0.5 * ((19.0 - 19.0) / 2.5) ** 2)
          + 0.75 * np.exp(-0.5 * ((19.0 -  9.0) / 2.5) ** 2)
          + 0.20 * np.exp(-0.5 * ((19.0 -  3.0) / 2.0) ** 2))
    shape = raw / norm

    wf    = np.where(dow >= 5, weekend_factor, 1.0)
    noise = rng.uniform(-noise_pct, noise_pct, size=len(snapshots))
    values = p_max * shape * wf * (1.0 + noise)
    return np.round(np.maximum(values, 0.0), 3)


def _shape_for_section(section: str):
    """
    Return the daily-shape function for a given section (defaulting to the
    electrical double-peak shape when no carrier-specific shape is registered).
    """
    if section == "hydrogen":
        return _h2_load_profile
    if section == "heat":
        return _heat_load_profile
    return _double_peak_profile


# ── Template horizon helper ──────────────────────────────────────────────────
# All three template endpoints (loads / generators / links) used to hard-code a
# 168-hour Monday-this-week sample. The user usually wants the template aligned
# with the simulation horizon they're already configured with — otherwise upload
# either truncates or has to be re-shaped.
#
# Resolution rules (first match wins):
#   1. start + end provided   → custom pd.date_range(start, end, freq=freq)
#   2. use_snapshots == True  → n.snapshots (the simulation horizon)
#   3. fallback                → 168 h starting Monday-this-week, hourly
#
# Returns a (snapshots, source_label) tuple. The label is used to make the
# error messages and filenames descriptive ("simulation" / "custom" / "sample").
def _template_snapshots(
    n,
    start: str | None = None,
    end: str | None = None,
    freq: str = "h",
    use_snapshots: bool = True,
) -> tuple[pd.DatetimeIndex, str]:
    if start and end:
        try:
            sns = pd.date_range(start, end, freq=freq)
        except Exception as exc:
            raise HTTPException(400, f"Invalid template range: {exc}")
        if len(sns) == 0:
            raise HTTPException(400, "Template range produced 0 timestamps; check start/end/freq.")
        sns.name = "timestamp"
        return sns, "custom"
    if use_snapshots and len(n.snapshots) > 0:
        # MultiIndex (period, timestep) — `pd.DatetimeIndex(multi)` raises
        # `Cannot create a DatetimeArray from a MultiIndex`. The template
        # represents one operational year that gets replicated per period, so
        # extract period-0's timestep range and use that as the template
        # horizon.
        if isinstance(n.snapshots, pd.MultiIndex):
            first_p = n.snapshots.get_level_values(0).unique()[0]
            mask = n.snapshots.get_level_values(0) == first_p
            sns = pd.DatetimeIndex(n.snapshots[mask].get_level_values(1))
        else:
            sns = pd.DatetimeIndex(n.snapshots)
        sns.name = "timestamp"
        return sns, "simulation"
    try:
        import datetime as _dt
        today = _dt.date.today()
        week_start = today - _dt.timedelta(days=today.weekday())
        sns = pd.date_range(str(week_start), periods=168, freq="h")
    except Exception:
        sns = pd.date_range("2024-01-01", periods=168, freq="h")
    sns.name = "timestamp"
    return sns, "sample"


_RENEWABLE_KW    = {'wind', 'solar', 'ror', 'hydro', 'geothermal', 'wave', 'tidal', 'pv', 'biomass', 'biogas', 'run-of-river'}


# Dispatchable conventional units PLUS sinks / dumps / spills that behave
# like dispatchable units from a UX standpoint: the user wants to tune
# marginal cost / availability / must-run from the Conventional tab.
# 'dump', 'spill', 'sink', 'slack' cover heat-dump generators (carrier
# 'heat-dump' uses 'dump'), wind-spill model patterns, and VOLL slacks.
_CONVENTIONAL_KW = {'coal', 'lignite', 'gas', 'nuclear', 'oil', 'ccgt', 'ocgt', 'chp', 'thermal', 'diesel', 'peat', 'steam',
                    'dump', 'spill', 'sink', 'slack'}


_DR_KW           = {'dr', 'dsm', 'flex', 'dsr', 'interruptible', 'demand_response', 'curtail'}


def _gen_category(carrier: str) -> str:
    """Return 'renewable' | 'conventional' | 'dr' | 'other' based on carrier keyword match."""
    c = (carrier or '').lower()
    if any(k in c for k in _DR_KW):           return 'dr'
    if any(k in c for k in _RENEWABLE_KW):    return 'renewable'
    if any(k in c for k in _CONVENTIONAL_KW): return 'conventional'
    return 'other'


def _profile_meta_for(
    name: str,
    user_series: pd.Series | None,
    network_df: pd.DataFrame | None,
    user_only: bool = False,
) -> dict:
    """
    Build a {has_profile, rows, mean, peak, sum, start, end} block for one generator.

    Prefers the user-uploaded series; falls back to a column on a network _t
    DataFrame if present, UNLESS ``user_only=True``. Returns
    ``{has_profile: False}`` when neither has data (or only the network
    has data and ``user_only`` is set).

    The ``user_only`` flag is for attributes the user expects to control
    exclusively from the Time Series Manager — `marginal_cost` is the
    canonical case: the solver writes per-snapshot CO2 surcharges into
    ``generators_t.marginal_cost`` when ``co2_price_per_period`` is set,
    and a project saved mid-solve can persist those columns into netcdf.
    Surfacing them as "uploaded profiles" misleads the user, so this flag
    restricts has_profile detection to the explicit user upload store.

    `sum` is Σ of the profile values — for a p_max_pu profile that's the
    full-load-equivalent hours; for a load p_set profile it's energy in MWh.
    The GUI labels it per attribute.

    Handles multi-period networks: when `col.index` is a `(period, timestep)`
    MultiIndex, the first/last positions are tuples — calling `.isoformat()`
    on those throws `AttributeError: 'tuple' object has no attribute
    'isoformat'`. Use the timestep level (level 1) for the date stamps so
    the frontend gets a usable ISO string in both shapes.
    """
    s = user_series
    if (
        s is None
        and not user_only
        and network_df is not None
        and not network_df.empty
        and name in network_df.columns
    ):
        s = network_df[name]
    if s is None:
        return {'has_profile': False}
    col = s.dropna()
    if not len(col):
        return {'has_profile': False}
    if isinstance(col.index, pd.MultiIndex):
        # Multi-period: pull the timestep-level (level 1) values.
        ts_level = col.index.get_level_values(-1)
        start_ts = ts_level[0]
        end_ts = ts_level[-1]
    else:
        start_ts = col.index[0]
        end_ts = col.index[-1]
    def _iso(t) -> str:
        if hasattr(t, "isoformat"):
            return t.isoformat()
        return str(t)
    return {
        'has_profile': True,
        'rows': int(len(col)),
        'start': _iso(start_ts),
        'end':   _iso(end_ts),
        'mean':  float(col.mean()),
        'peak':  float(col.max()),
        'sum':   float(col.sum()),
    }


def _solar_cf_profile(snapshots: pd.DatetimeIndex, noise_seed: int) -> np.ndarray:
    """Solar capacity factor (0–1): bell curve peaking at 13:00, zero at night."""
    rng = np.random.default_rng(noise_seed)
    hours = snapshots.hour.values.astype(float)
    raw = np.exp(-0.5 * ((hours - 13.0) / 3.5) ** 2)
    raw = np.where((hours < 5) | (hours > 21), 0.0, raw)
    noise = rng.uniform(-0.04, 0.04, size=len(snapshots))
    return np.round(np.clip(raw * (1.0 + noise), 0.0, 1.0), 3)


def _wind_cf_profile(snapshots: pd.DatetimeIndex, noise_seed: int) -> np.ndarray:
    """Wind capacity factor (0–1): smooth variation around 0.35 mean."""
    rng = np.random.default_rng(noise_seed)
    n = len(snapshots)
    t = np.arange(n)
    base = 0.35 + 0.12 * np.sin(t * 2 * np.pi / 24) + 0.08 * np.sin(t * 2 * np.pi / 72)
    noise = rng.normal(0, 0.06, size=n)
    smooth = np.convolve(noise, np.ones(6) / 6, mode='same')
    return np.round(np.clip(base + smooth, 0.0, 1.0), 3)


def _flat_cf_profile(snapshots: pd.DatetimeIndex, noise_seed: int, base: float = 0.85) -> np.ndarray:
    """Flat capacity factor with minor noise (conventional / DR generators)."""
    rng = np.random.default_rng(noise_seed)
    noise = rng.uniform(-0.03, 0.03, size=len(snapshots))
    return np.round(np.clip(base * (1.0 + noise), 0.0, 1.0), 3)


_H2_CARRIERS = {'h2', 'hydrogen', 'h2 pipeline', 'h2pipeline', 'h2_pipeline'}


def _link_category(n, link_name: str) -> str:
    """Return 'electrolyzer' | 'fuel_cell' | 'other' based on bus carriers."""
    try:
        bus0 = str(n.links.at[link_name, 'bus0']) if 'bus0' in n.links.columns else ''
        bus1 = str(n.links.at[link_name, 'bus1']) if 'bus1' in n.links.columns else ''
        c0 = str(n.buses.at[bus0, 'carrier']).lower() if bus0 in n.buses.index else ''
        c1 = str(n.buses.at[bus1, 'carrier']).lower() if bus1 in n.buses.index else ''
        if c1 in _H2_CARRIERS:   return 'electrolyzer'
        if c0 in _H2_CARRIERS:   return 'fuel_cell'
    except Exception:
        pass
    return 'other'
