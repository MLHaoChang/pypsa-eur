"""
The generic site template ``eh_site`` (generic-site E2E, slice G1).

One parametrized site for anything that is not a data center: the user picks a
site type (industrial, commercial, hospital, campus, other), whose preset fills
every parameter, and may override any of them — the load per growth stage and
its weekly shape, the grid connection and when it is energized, and the on-site
options (gensets, PV, battery, UPS) with the candidates the plan may build.

It runs the same five stages as the staged data center
(``eh_templates._staged_datacenter``) and follows its rules:

* multi-period: one investment period per growth stage, the representative
  July week repeated in each, both period weightings the period's years;
* the energization schedule in MW in ``links_t["eh_energization_mw"]``
  (``services.adequacy.energization``), never as ``p_max_pu``;
* one candidate row per period with ``build_year`` = the period (never
  vintage bounds: the finance adapter refuses later builds made through them);
* the existing site typed at an overnight cost of 0, in service at the first
  period;
* a loss-free PoC chain (efficiency 1.0), so the investment case's grid-only
  comparison is established;
* the commercial and finance blocks built here and bound at create.

**Physically plausible by construction** (owner, Q1): every stage's modelled
peak (the maximum of the summed ``p_set``) is at or below the connection the
utility has energized in that stage, so the grid-only comparison runs under
its own connection. ``site_params`` refuses a stage that is not, naming the
stage and the shortfall.

Synthetic illustrative numbers, like ``eh_templates``; the ledger in the
template metadata says what each one is. The industrial and commercial load
shapes are the defaults pack's stated shapes (July).

Loaded by FILE PATH, like ``eh_templates.py`` (the directory ships as data):
the helpers it shares with the data center come from that file the same way.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd
import pypsa


def _load_eh_templates():
    key = "_eh_templates_for_site"
    mod = sys.modules.get(key)
    if mod is None:
        path = pathlib.Path(__file__).resolve().with_name("eh_templates.py")
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


_T = _load_eh_templates()
SNAPSHOTS = _T.SNAPSHOTS
HOURS = _T.HOURS
WEIGHT = _T.WEIGHT
_HOD, _DAY = _T._HOD, _T._DAY
_a = _T._a

TEMPLATE_ID = "eh_site"
SITE_TYPES = ("industrial", "commercial", "hospital", "campus", "other")
DISCOUNT_RATE = 0.07
#: The pack version the shapes and prices come from (the data center's).
PACK_VERSION = _T.TEMPLATE_PACK_VERSION


class SiteParamsError(ValueError):
    """A parameter set the template refuses. ``field`` is the parameter's
    path (``stages[1].peak_mw``), the message's text up to its first colon
    unless given."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field if field is not None else message.split(":", 1)[0]


# ── The presets (synthetic illustrative numbers) ────────────────────────────
#
# Each preset is a complete parameter set. The peaks are the modelled peak of
# the summed load in each stage; every one is at or below the connection.

_LABEL = {"industrial": "Industrial plant", "commercial": "Commercial building",
          "hospital": "Hospital", "campus": "Campus", "other": "Site"}

PRESETS: dict[str, dict] = {
    "industrial": {
        "stages": [{"year": 2027, "peak_mw": 8.0}, {"year": 2030, "peak_mw": 12.0},
                   {"year": 2035, "peak_mw": 15.0}],
        "load_profile": "industrial", "critical_share": 0.4,
        "connection_mw": 20.0,
        "existing": {"gensets": {"count": 2, "unit_mw": 2.0, "fuel": "gas"}, "pv_mw": 1.0,
                     "battery": {"mw": 0.0, "mwh": 0.0}, "ups": {"mw": None, "minutes": 15.0}},
        "candidates": {"genset": {"enabled": True, "max_mw": 12.0},
                       "bess": {"enabled": True, "max_mw": 8.0},
                       "pv": {"enabled": True, "max_mw": 5.0}},
    },
    "commercial": {
        "stages": [{"year": 2027, "peak_mw": 2.0}, {"year": 2030, "peak_mw": 3.0},
                   {"year": 2035, "peak_mw": 4.0}],
        "load_profile": "commercial", "critical_share": 0.15,
        "connection_mw": 5.0,
        "existing": {"gensets": {"count": 2, "unit_mw": 0.5, "fuel": "diesel"}, "pv_mw": 0.5,
                     "battery": {"mw": 0.0, "mwh": 0.0}, "ups": {"mw": None, "minutes": 15.0}},
        "candidates": {"genset": {"enabled": True, "max_mw": 3.0},
                       "bess": {"enabled": True, "max_mw": 3.0},
                       "pv": {"enabled": True, "max_mw": 2.0}},
    },
    "hospital": {
        "stages": [{"year": 2027, "peak_mw": 4.0}, {"year": 2030, "peak_mw": 5.0},
                   {"year": 2035, "peak_mw": 6.0}],
        "load_profile": "hospital", "critical_share": 0.5,
        "connection_mw": 8.0,
        "existing": {"gensets": {"count": 3, "unit_mw": 2.0, "fuel": "diesel"}, "pv_mw": 0.3,
                     "battery": {"mw": 0.0, "mwh": 0.0}, "ups": {"mw": None, "minutes": 15.0}},
        "candidates": {"genset": {"enabled": True, "max_mw": 6.0},
                       "bess": {"enabled": True, "max_mw": 4.0},
                       "pv": {"enabled": True, "max_mw": 1.0}},
    },
    "campus": {
        "stages": [{"year": 2027, "peak_mw": 10.0}, {"year": 2030, "peak_mw": 14.0},
                   {"year": 2035, "peak_mw": 18.0}],
        "load_profile": "campus", "critical_share": 0.2,
        "connection_mw": 25.0,
        "existing": {"gensets": {"count": 2, "unit_mw": 3.0, "fuel": "gas"}, "pv_mw": 2.0,
                     "battery": {"mw": 2.0, "mwh": 4.0}, "ups": {"mw": None, "minutes": 15.0}},
        "candidates": {"genset": {"enabled": True, "max_mw": 12.0},
                       "bess": {"enabled": True, "max_mw": 10.0},
                       "pv": {"enabled": True, "max_mw": 6.0}},
    },
    "other": {
        "stages": [{"year": 2027, "peak_mw": 5.0}, {"year": 2030, "peak_mw": 7.0},
                   {"year": 2035, "peak_mw": 9.0}],
        "load_profile": "flat", "critical_share": 0.3,
        "connection_mw": 12.0,
        "existing": {"gensets": {"count": 2, "unit_mw": 2.0, "fuel": "diesel"}, "pv_mw": 1.0,
                     "battery": {"mw": 0.0, "mwh": 0.0}, "ups": {"mw": None, "minutes": 15.0}},
        "candidates": {"genset": {"enabled": True, "max_mw": 8.0},
                       "bess": {"enabled": True, "max_mw": 5.0},
                       "pv": {"enabled": True, "max_mw": 3.0}},
    },
}

#: The weekly load shapes a site may use: the pack's stated shapes for
#: industry and offices, synthetic bells for the rest.
LOAD_PROFILES = ("industrial", "commercial", "hospital", "campus", "flat")
_PACK_PROFILE = {"industrial": "industrial_two_shift", "commercial": "commercial_office"}
_FUEL = {"gas": ("gas", 0.20, 165.0, "gas-genset"),
         "diesel": ("diesel", 0.27, 260.0, "diesel-genset")}
HORIZON_END = 2045
#: Bounds that keep a site inside what the template is built for.
MAX_STAGES = 3
MAX_GENSETS = 8


def _shape(profile: str) -> np.ndarray:
    """The weekly shape (168 h, Monday first), maximum 1."""
    weekday = (_DAY < 5)
    if profile in _PACK_PROFILE:
        from services.library.defaults_pack.loader import load_defaults_pack

        pack = load_defaults_pack(PACK_VERSION)
        raw = pack.load_profile_series(_PACK_PROFILE[profile], SNAPSHOTS).to_numpy(dtype=float)
    elif profile == "hospital":
        # Around the clock: a 70 % base, a weekday daytime peak (theatres,
        # outpatients), weekends a tenth lower.
        raw = 0.7 + 0.3 * np.exp(-((_HOD - 11.0) ** 2) / (2 * 4.0 ** 2))
        raw = np.where(weekday, raw, 0.9 * raw)
    elif profile == "campus":
        # Teaching day with an evening shoulder; weekends at 60 %.
        raw = (0.45 + 0.55 * np.exp(-((_HOD - 14.0) ** 2) / (2 * 4.0 ** 2))
               + 0.1 * np.exp(-((_HOD - 20.0) ** 2) / (2 * 1.5 ** 2)))
        raw = np.where(weekday, raw, 0.6 * raw)
    elif profile == "flat":
        raw = 0.8 + 0.2 * np.exp(-((_HOD - 14.0) ** 2) / (2 * 5.0 ** 2))
    else:
        raise SiteParamsError(f"load_profile: unknown {profile!r}; allowed {list(LOAD_PROFILES)}")
    return raw / raw.max()


#: What the Guided form shows for each site type (label, a typical example).
SITE_TYPE_TEXT = {
    "industrial": ("Industrial plant", "a factory or process plant"),
    "commercial": ("Commercial building", "offices, retail or a hotel"),
    "hospital": ("Hospital", "a hospital or clinic that must keep its critical care on"),
    "campus": ("University or campus", "several buildings on one connection"),
    "other": ("Other site", "anything else: start from round numbers"),
}
LOAD_PROFILE_TEXT = {
    "industrial": "Two shifts on weekdays",
    "commercial": "Office hours on weekdays",
    "hospital": "Around the clock, busier by day",
    "campus": "Teaching day with an evening shoulder",
    "flat": "Nearly flat",
}


def form_schema() -> dict:
    """What the Guided form needs to show the defaults before anything is
    created: each site type's full preset (as ``site_params`` resolves it),
    the load shapes and the bounds the checks apply."""
    return {
        "site_types": [{"id": t, "label": SITE_TYPE_TEXT[t][0], "example": SITE_TYPE_TEXT[t][1]}
                       for t in SITE_TYPES],
        "presets": {t: site_params({"site_type": t}) for t in SITE_TYPES},
        "load_profiles": [{"id": k, "label": LOAD_PROFILE_TEXT[k]} for k in LOAD_PROFILES],
        "bounds": {"max_stages": MAX_STAGES, "year_min": 2025, "year_max": 2060,
                   "peak_mw_max": 500.0, "connection_mw_max": 1000.0,
                   "max_gensets": MAX_GENSETS, "unit_mw_max": 50.0, "ups_minutes_max": 240.0,
                   "ups_minutes_min": BRIDGE_S / 60.0, "horizon_end": HORIZON_END},
    }


# ── Parameters ──────────────────────────────────────────────────────────────


def _num(where: str, v, *, lo: float = 0.0, lo_open: bool = False, hi: float | None = None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise SiteParamsError(f"{where}: expected a number, got {v!r}")
    v = float(v)
    if v < lo or (lo_open and v <= lo) or (hi is not None and v > hi):
        bound = f"> {lo:g}" if lo_open else f">= {lo:g}"
        if hi is not None:
            bound += f" and <= {hi:g}"
        raise SiteParamsError(f"{where}: must be {bound}, got {v:g}")
    return v


def _merge(base: dict, over: dict, where: str = "") -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k not in base:
            raise SiteParamsError(f"{where}{k}: unknown parameter; allowed {sorted(base)}")
        if isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v, f"{where}{k}.")
        else:
            out[k] = copy.deepcopy(v)
    return out


def site_params(raw: dict | None = None) -> dict:
    """The full, checked parameter set: ``raw["site_type"]``'s preset
    (default ``industrial``) with ``raw``'s other keys laid over it. Raises
    ``SiteParamsError`` naming the field, or — for a stage whose peak is above
    the connection energized in it — the stage and the shortfall."""
    raw = dict(raw or {})
    site_type = raw.pop("site_type", "industrial")
    if site_type not in SITE_TYPES:
        raise SiteParamsError(f"site_type: unknown {site_type!r}; allowed {list(SITE_TYPES)}")
    base = {"site_type": site_type, "name": None, "fault_level_mva": None,
            "energization": None, "horizon_end": HORIZON_END, **PRESETS[site_type]}
    p = _merge(base, raw)
    p["site_type"] = site_type

    stages = p["stages"]
    if not isinstance(stages, list) or not 1 <= len(stages) <= MAX_STAGES:
        raise SiteParamsError(f"stages: 1 to {MAX_STAGES} growth stages, got {stages!r}")
    out_stages = []
    for i, s in enumerate(stages):
        if not isinstance(s, dict) or set(s) - {"year", "peak_mw"} or "year" not in s \
                or "peak_mw" not in s:
            raise SiteParamsError(f"stages[{i}]: expected {{year, peak_mw}}, got {s!r}")
        y = s["year"]
        if isinstance(y, bool) or not isinstance(y, int) or not 2025 <= y <= 2060:
            raise SiteParamsError(f"stages[{i}].year: a year from 2025 to 2060, got {y!r}")
        out_stages.append({"year": y, "peak_mw": _num(f"stages[{i}].peak_mw", s["peak_mw"],
                                                      lo_open=True, hi=500.0)})
    years = [s["year"] for s in out_stages]
    if years != sorted(set(years)):
        raise SiteParamsError(f"stages: the years must increase, got {years}")
    p["stages"] = out_stages
    he = p["horizon_end"]
    if isinstance(he, bool) or not isinstance(he, int) or he <= years[-1]:
        raise SiteParamsError(f"horizon_end: a year after the last stage ({years[-1]}), got {he!r}")

    if p["load_profile"] not in LOAD_PROFILES:
        raise SiteParamsError(
            f"load_profile: unknown {p['load_profile']!r}; allowed {list(LOAD_PROFILES)}")
    p["critical_share"] = _num("critical_share", p["critical_share"], lo_open=True, hi=1.0)
    conn = p["connection_mw"] = _num("connection_mw", p["connection_mw"], lo_open=True, hi=1000.0)
    fl = p["fault_level_mva"]
    p["fault_level_mva"] = (20.0 * conn if fl is None
                            else _num("fault_level_mva", fl, lo_open=True))

    energ = p["energization"]
    if energ is None:
        energ = [{"year": y, "mw": conn} for y in years]
    if not isinstance(energ, list) or [e.get("year") if isinstance(e, dict) else None
                                       for e in energ] != years:
        raise SiteParamsError(f"energization: one {{year, mw}} per stage year {years}, got {energ!r}")
    p["energization"] = [{"year": e["year"], "mw": _num(f"energization[{i}].mw", e["mw"], hi=conn)}
                         for i, e in enumerate(energ)]

    ex = p["existing"]
    g = ex["gensets"]
    cnt = g["count"]
    if isinstance(cnt, bool) or not isinstance(cnt, int) or not 0 <= cnt <= MAX_GENSETS:
        raise SiteParamsError(f"existing.gensets.count: 0 to {MAX_GENSETS}, got {cnt!r}")
    g["unit_mw"] = _num("existing.gensets.unit_mw", g["unit_mw"], lo_open=True, hi=50.0)
    if g["fuel"] not in _FUEL:
        raise SiteParamsError(f"existing.gensets.fuel: one of {sorted(_FUEL)}, got {g['fuel']!r}")
    ex["pv_mw"] = _num("existing.pv_mw", ex["pv_mw"])
    ex["battery"]["mw"] = _num("existing.battery.mw", ex["battery"]["mw"])
    ex["battery"]["mwh"] = _num("existing.battery.mwh", ex["battery"]["mwh"])
    if (ex["battery"]["mw"] > 0) != (ex["battery"]["mwh"] > 0):
        raise SiteParamsError("existing.battery: give both mw and mwh, or neither")
    critical_peak = p["critical_share"] * out_stages[-1]["peak_mw"]
    # None: a UPS sized to the critical load it protects, with a 10 % margin.
    ex["ups"]["mw"] = (round(1.1 * critical_peak, 3) if ex["ups"]["mw"] is None
                       else _num("existing.ups.mw", ex["ups"]["mw"]))
    ex["ups"]["minutes"] = _num("existing.ups.minutes", ex["ups"]["minutes"], hi=240.0)
    for k, c in p["candidates"].items():
        if not isinstance(c.get("enabled"), bool):
            raise SiteParamsError(f"candidates.{k}.enabled: true or false, got {c.get('enabled')!r}")
        c["max_mw"] = _num(f"candidates.{k}.max_mw", c["max_mw"])

    # Q1: the grid-only comparison must run under its own connection.
    for i, (s, e) in enumerate(zip(out_stages, p["energization"])):
        if s["peak_mw"] > e["mw"] + 1e-9:
            raise SiteParamsError(
                f"stage {s['year']}: the peak load {s['peak_mw']:g} MW is above the "
                f"{e['mw']:g} MW of grid connection energized in {s['year']} "
                f"(short by {s['peak_mw'] - e['mw']:g} MW). Raise the connection or its "
                f"energization, or lower the peak.", field=f"stages[{i}].peak_mw")

    if 0.0 < ex["ups"]["mw"] < critical_peak - 1e-9:
        raise SiteParamsError(
            f"existing.ups.mw: the UPS carries the critical load it protects, "
            f"{critical_peak:g} MW in {years[-1]}; got {ex['ups']['mw']:g} MW")
    if ex["ups"]["mw"] > 0 and ex["ups"]["minutes"] * 60.0 < BRIDGE_S:
        raise SiteParamsError(
            f"existing.ups.minutes: the UPS bridges the {BRIDGE_S / 60:g} min until the gensets "
            f"carry the load; got {ex['ups']['minutes']:g} min")
    name = p["name"]
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise SiteParamsError(f"name: a non-empty string, got {name!r}")
    return p


# ── The staging (the generic form of `_staged_datacenter`) ─────────────────


def period_years(years: list[int], horizon_end: int) -> dict[int, int]:
    """The years each period stands for: to the next stage, the last to the horizon end."""
    ends = [*years[1:], horizon_end]
    return {y: e - y for y, e in zip(years, ends)}


def stage_network(n: pypsa.Network, years: list[int], *, load_scale: dict[int, dict[str, float]],
                  energization: dict[str, list[float]], weights: dict[int, int],
                  existing: tuple[str, ...], candidates) -> pypsa.Network:
    """A single-period network ``n`` (one representative week) as a plan over
    ``years``: the week repeated per period with each load scaled by
    ``load_scale[year][load]``, the import Links capped per period by
    ``energization[link]`` (MW, one per year), ``existing`` typed at an
    overnight cost of 0, and ``candidates(n, year)`` adding that period's
    candidate rows (``build_year`` = year)."""
    flat = {(frame, attr): getattr(n, frame)[attr].copy()
            for frame, attr in (("generators_t", "p_max_pu"), ("generators_t", "marginal_cost"),
                                ("loads_t", "p_set"))}
    snaps = pd.MultiIndex.from_product([years, SNAPSHOTS], names=["period", "timestep"])
    n.set_snapshots(snaps)
    n.investment_periods = years
    n.snapshot_weightings.loc[:, ["objective", "generators"]] = WEIGHT
    n.snapshot_weightings.loc[:, "stores"] = 1.0                    # hours per snapshot (S0)
    n.investment_period_weightings["years"] = [float(weights[y]) for y in years]
    n.investment_period_weightings["objective"] = [float(weights[y]) for y in years]
    snaps = n.snapshots                                 # PyPSA's own index (names and all)
    for (frame, attr), df in flat.items():
        tiled = pd.DataFrame(np.tile(df.to_numpy(), (len(years), 1)), index=snaps,
                             columns=df.columns)
        getattr(n, frame)[attr] = tiled
    p_set = n.loads_t.p_set
    for y in years:
        for load, scale in load_scale[y].items():
            p_set.loc[y, load] = (p_set.loc[y, load] * scale).to_numpy()
    n.links_t["eh_energization_mw"] = pd.DataFrame(
        {link: np.repeat(mw, len(SNAPSHOTS)) for link, mw in energization.items()}, index=snaps)
    for frame in ("generators", "storage_units", "links"):
        df = getattr(n, frame)
        rows = [a for a in existing if a in df.index]
        df.loc[rows, ["overnight_cost", "discount_rate"]] = [0.0, DISCOUNT_RATE]
    for y in years:
        candidates(n, y)
    return n


# ── The network ─────────────────────────────────────────────────────────────

#: The UPS bridge until the gensets carry the critical load (s), as the data center.
BRIDGE_S = 600.0


def existing_assets(p: dict) -> tuple[str, ...]:
    ex = p["existing"]
    out = [f"genset_{i}" for i in range(1, ex["gensets"]["count"] + 1)]
    if ex["pv_mw"] > 0:
        out.append("site_pv")
    if ex["battery"]["mw"] > 0:
        out.append("site_battery")
    if ex["ups"]["mw"] > 0:
        out.append("ups_battery")
    out.append("critical_feeder")
    return tuple(out)


def candidate_names(p: dict) -> list[str]:
    return [f"{k}_new_{s['year']}" for s in p["stages"]
            for k in ("genset", "bess", "pv") if p["candidates"][k]["enabled"]]


def build_eh_site(params: dict | None = None) -> pypsa.Network:
    """The generic site for ``params`` (checked through ``site_params``).

    Two buses behind the PoC: the site's MV bus (the general load, the
    gensets, PV and the site battery) and a critical bus behind a loss-free
    feeder (the critical share of the load, the UPS and the battery
    candidates), tagged ``eh_critical`` so DtC attributes per load."""
    p = site_params(params)
    ex, cand = p["existing"], p["candidates"]
    years = [s["year"] for s in p["stages"]]
    last_peak = p["stages"][-1]["peak_mw"]
    fuel_carrier, fuel_co2, fuel_mc, fuel_tech = _FUEL[ex["gensets"]["fuel"]]

    n = _T._base(f"{_LABEL[p['site_type']]} Energy Hub")
    for c, co2 in (("AC", 0.0), (fuel_carrier, fuel_co2), ("solar", 0.0), ("battery", 0.0),
                   ("grid", 0.35)):
        n.add("Carrier", c, co2_emissions=co2)
    v_grid = 110.0 if p["connection_mw"] > 30.0 else 20.0
    v_site = 20.0 if p["connection_mw"] > 10.0 else 10.0
    n.add("Bus", "grid", v_nom=v_grid, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "site_mv", v_nom=v_site, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "critical", v_nom=v_site, carrier="AC", x=0.0, y=0.0)

    # Wholesale supply beyond the PoC (not firm capacity of the site).
    price = 70.0 + 40.0 * np.exp(-((_HOD - 19) ** 2) / 8.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid",
          p_nom=10.0 * p["connection_mw"], marginal_cost=0.0)
    n.generators_t.marginal_cost = pd.DataFrame({"grid_supply": price}, index=SNAPSHOTS)
    # The PoC and the critical feeder are loss-free (the ledger says so).
    n.add("Link", "grid_import", bus0="grid", bus1="site_mv", p_nom=p["connection_mw"],
          efficiency=1.0, carrier="AC")
    n.add("Link", "critical_feeder", bus0="site_mv", bus1="critical",
          p_nom=round(2.0 * p["critical_share"] * last_peak, 3), efficiency=1.0, carrier="AC")

    # The load at its LAST-stage size; each period scales it to its own peak.
    shape = _shape(p["load_profile"])
    share = p["critical_share"]
    n.add("Load", "critical_load", bus="critical", carrier="AC")
    n.add("Load", "general_load", bus="site_mv", carrier="AC")
    n.loads_t.p_set = pd.DataFrame({"critical_load": share * last_peak * shape,
                                    "general_load": (1.0 - share) * last_peak * shape},
                                   index=SNAPSHOTS)

    for i in range(1, ex["gensets"]["count"] + 1):
        g = f"genset_{i}"
        n.add("Generator", g, bus="site_mv", carrier=fuel_carrier, p_nom=ex["gensets"]["unit_mw"],
              marginal_cost=fuel_mc)
        _T._outage(n, "generators", g, 0.03, 24.0)
    sun = _T._solar()
    pmax = {}
    if ex["pv_mw"] > 0:
        n.add("Generator", "site_pv", bus="site_mv", carrier="solar", p_nom=ex["pv_mw"],
              marginal_cost=0.0)
        pmax["site_pv"] = sun
    if ex["battery"]["mw"] > 0:
        n.add("StorageUnit", "site_battery", bus="site_mv", carrier="battery",
              p_nom=ex["battery"]["mw"], max_hours=ex["battery"]["mwh"] / ex["battery"]["mw"],
              efficiency_store=0.95, efficiency_dispatch=0.95, cyclic_state_of_charge=True)
    if ex["ups"]["mw"] > 0:
        n.add("StorageUnit", "ups_battery", bus="critical", carrier="battery",
              p_nom=ex["ups"]["mw"], max_hours=ex["ups"]["minutes"] / 60.0,
              efficiency_store=0.95, efficiency_dispatch=0.95, cyclic_state_of_charge=True)
    n.generators_t.p_max_pu = pd.DataFrame(pmax, index=SNAPSHOTS)

    _T._tag_columns(n)
    ibr = ex["pv_mw"] + ex["battery"]["mw"] + sum(
        cand[k]["max_mw"] for k in ("bess", "pv") if cand[k]["enabled"])
    n.buses.at["grid", "eh_poc"] = True
    n.buses.at["grid", "eh_sk_mva"] = p["fault_level_mva"]
    n.buses.at["grid", "eh_ibr_mva"] = ibr
    n.buses.at["site_mv", "eh_sk_mva"] = 0.9 * p["fault_level_mva"]
    n.buses.at["site_mv", "eh_ibr_mva"] = ibr
    n.buses.at["critical", "eh_critical"] = True
    n.links.at["grid_import", "eh_role"] = "grid_import"
    _T._outage(n, "links", "grid_import", 0.002, 12.0)
    _T._outage(n, "links", "critical_feeder", 0.005, 72.0)

    def candidates(net, y):
        if cand["genset"]["enabled"]:
            g = f"genset_new_{y}"
            net.add("Generator", g, bus="site_mv", carrier=fuel_carrier, p_nom=0.0,
                    p_nom_extendable=True, p_nom_max=cand["genset"]["max_mw"],
                    marginal_cost=fuel_mc, build_year=y)
            _T._price(net, "generators", g, fuel_tech)
            _T._outage(net, "generators", g, 0.03, 24.0)
        if cand["bess"]["enabled"]:
            b = f"bess_new_{y}"
            net.add("StorageUnit", b, bus="critical", carrier="battery", p_nom=0.0,
                    p_nom_extendable=True, p_nom_max=cand["bess"]["max_mw"], max_hours=4.0,
                    efficiency_store=0.95, efficiency_dispatch=0.95,
                    cyclic_state_of_charge=True, build_year=y)
            _T._price(net, "storage_units", b, "battery")
        if cand["pv"]["enabled"]:
            v = f"pv_new_{y}"
            net.add("Generator", v, bus="site_mv", carrier="solar", p_nom=0.0,
                    p_nom_extendable=True, p_nom_max=cand["pv"]["max_mw"], marginal_cost=0.0,
                    build_year=y)
            _T._price(net, "generators", v, "solar-rooftop")
            net.generators_t.p_max_pu[v] = np.tile(sun, len(years))

    scale = {s["year"]: {"critical_load": s["peak_mw"] / last_peak,
                         "general_load": s["peak_mw"] / last_peak} for s in p["stages"]}
    stage_network(n, years, load_scale=scale,
                  energization={"grid_import": [e["mw"] for e in p["energization"]]},
                  weights=period_years(years, p["horizon_end"]),
                  existing=existing_assets(p), candidates=candidates)
    # Storage cycles within each period: one week stands for several years,
    # so no energy is carried from one period into the next (gatekeeper
    # review of #304; set here, not in the shared stage_network).
    if len(n.storage_units):
        n.storage_units["cyclic_state_of_charge"] = True
        n.storage_units["cyclic_state_of_charge_per_period"] = True
    n.name = p["name"] or f"{_LABEL[p['site_type']]} Energy Hub"
    return n


# ── Commercial and finance (as the staged data center, slice D4) ────────────

SITE_TARIFF: dict = {
    "id": "site_network_tariff_synthetic",
    "name": "Synthetic network tariff (illustrative)",
    "jurisdiction": "DE",
    "valid_from": "2027-01-01",
    "items": [{"id": "network_energy", "kind": "energy", "unit": "per_kwh",
               "periods": [{"name": "day", "rate": 0.035, "start_hour": 8, "end_hour": 20},
                           {"name": "night", "rate": 0.020}]}],
}


def finance_inputs(p: dict) -> dict:
    years = [s["year"] for s in p["stages"]]
    first = years[0]
    existing = existing_assets(p)
    owned_gen = [a for a in existing if a != "critical_feeder"]
    cand = candidate_names(p)
    return {
        "currency": "EUR", "currency_year": 2020, "price_basis": "nominal",
        "financial_close": f"{first - 1}-01-01",
        "cod_by_asset": {a: f"{first}-01-01" for a in existing},
        "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
        "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
        # Every owned generator and battery needs an entry: PV 0.5 %/yr, the
        # gensets and batteries none.
        "degradation_by_asset": {
            **{a: (0.005 if a == "site_pv" else 0.0) for a in owned_gen},
            **{a: (0.005 if a.startswith("pv_new") else 0.0) for a in cand}},
        "analysis_years": p["horizon_end"] - first,
        "annualise": False,
        "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
        "replacement_rule": "part_lifetimes",
        "terminal_value": {"method": "remaining_life_annuity"},
        "wacc_nominal": DISCOUNT_RATE, "cost_of_equity": DISCOUNT_RATE, "inflation": 0.02,
    }


def commercial_and_finance(p: dict, n: pypsa.Network | None = None) -> dict:
    """The ``commercial`` and ``finance`` blocks, stored as the routes store
    them: the ``single_owner`` value flows built on the site's network."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate(
        {"poc_link": "grid_import", "import_tariff": SITE_TARIFF, "timezone": "UTC"})
    vf = VFT.build("single_owner", n if n is not None else build_eh_site(p), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(finance_inputs(p)).model_dump(mode="json")}


def solver_config(p: dict, n: pypsa.Network | None = None) -> dict:
    cfg = {**_T.SOLVER_CONFIG, "multi_investment_periods": True, "auto_discount_periods": True,
           "discount_rate": DISCOUNT_RATE, **commercial_and_finance(p, n)}
    if p["existing"]["ups"]["mw"] > 0:
        cfg["island_constraints"] = True
    return cfg


# ── The island sidecar ──────────────────────────────────────────────────────


def island_config(p: dict) -> dict | None:
    """Control modes, unit dynamics and island requirements (gridspine's
    schema), or None for a site without a UPS (no island rows: the bridge
    the ``ups_bridged`` scenario needs is missing)."""
    ex, cand = p["existing"], p["candidates"]
    if ex["ups"]["mw"] <= 0:
        return None
    years = [s["year"] for s in p["stages"]]
    units: dict[str, dict] = {}
    for i in range(1, ex["gensets"]["count"] + 1):
        units[f"genset_{i}"] = _T._genset_island()
    pv = {"kind": "pv", "control": "gfl_qu", "pf_rated": _a(0.95),
          "qu_points": _a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])}
    bess = {"kind": "bess", "control": "gfm_droop", "pf_rated": _a(0.95), "droop_pct": _a(4.0),
            "q_droop_pct": _a(4.0), "tau_f_s": _a(0.1), "i_max_pu": _a(1.2),
            "k_gfm_min": _a(1.0)}
    if ex["pv_mw"] > 0:
        units["site_pv"] = copy.deepcopy(pv)
    if ex["battery"]["mw"] > 0:
        units["site_battery"] = copy.deepcopy(bess)
    for y in years:
        if cand["genset"]["enabled"]:
            units[f"genset_new_{y}"] = _T._genset_island(unit_mw=_a(ex["gensets"]["unit_mw"]))
        if cand["bess"]["enabled"]:
            units[f"bess_new_{y}"] = copy.deepcopy(bess)
        if cand["pv"]["enabled"]:
            units[f"pv_new_{y}"] = copy.deepcopy(pv)
    units["ups_battery"] = {"kind": "ups", "it_load": ["critical_load"], "walk_in_s": _a(15.0),
                            "f_window_hz": _a(2.0)}
    units["critical_load"] = {"kind": "load", "critical": True}
    units["general_load"] = {"kind": "load", "critical": False}
    limits = copy.deepcopy(_T._LIMITS)
    n_gen = sum(u["kind"] == "genset" for u in units.values())
    # Pickup blocks of about 5 % of the existing genset fleet (at least
    # 0.1 MW): a block the fleet's inertia holds at 1 Hz/s; enough blocks to
    # pick up the last stage's critical peak.
    fleet = ex["gensets"]["count"] * ex["gensets"]["unit_mw"]
    block = round(max(0.1, 0.05 * fleet), 2)
    critical_peak = p["critical_share"] * p["stages"][-1]["peak_mw"]
    count = max(1, math.ceil(critical_peak * 1.05 / block))
    rts = [u for u, spec in units.items() if spec["kind"] == "bess"]
    req = {
        "bridge_s": _a(BRIDGE_S), "sustained_h": _a(24.0), "fuel_autonomy_h": _a(24.0),
        "genset_redundancy_n": _a(1 if n_gen >= 2 else 0), "scenarios": ["ups_bridged"],
        "pickup_blocks": _a([block] * count),
        "frequency_limits": {"transition": dict(limits), "steady_island": dict(limits)},
        "rocof_window_ms": _a(500.0), "gfm_margin": _a(0.1),
    }
    if rts:
        req["ride_through_storage"] = rts
    if p["site_type"] == "hospital":
        # A hospital's safety supply starts within 10 s (IEC 60364-7-710,
        # NFPA 110 Type 10): the gensets' start and sync are tightened.
        for name, u in units.items():
            if u["kind"] == "genset":
                u["start_s"], u["sync_s"] = _a(7.0), _a(3.0)
    return {"units": units, "requirements": req}


# ── The metadata and stress registry ────────────────────────────────────────

_STRESS_HEAT = {"id": "heatwave", "name": "Heatwave (cooling + PV derate)", "kind": "parametric",
                "frequency_per_year": 0.3, "electrical_load_multiplier": 1.1,
                "renewable_availability_multiplier": 0.85}
_STRESS_DARK = {"id": "dunkelflaute", "name": "Dark, still week", "kind": "parametric",
                "frequency_per_year": 0.5, "electrical_load_multiplier": 1.0,
                "renewable_availability_multiplier": 0.1}
_STRESS_PROD = {"id": "production_peak", "name": "Production peak (all lines running)",
                "kind": "parametric", "frequency_per_year": 1.0,
                "electrical_load_multiplier": 1.05, "renewable_availability_multiplier": 1.0}


def stress_scenarios(p: dict) -> list[dict]:
    t = p["site_type"]
    out = [copy.deepcopy(_STRESS_DARK)]
    if t in ("commercial", "hospital", "campus", "other"):
        out.insert(0, copy.deepcopy(_STRESS_HEAT))
    if t == "industrial":
        out.append(copy.deepcopy(_STRESS_PROD))
    return out


def entered_fields(raw: dict | None) -> list[str]:
    """The parameters the user set (dotted paths; a list is one value), so
    the report can tell them from the preset's defaults."""
    out: list[str] = []

    def walk(d, prefix):
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v, f"{prefix}{k}.")
            else:
                out.append(f"{prefix}{k}")
    walk(dict(raw or {}), "")
    return sorted(out)


def template_meta(p: dict, entered: list[str] | None = None) -> dict:
    years = [s["year"] for s in p["stages"]]
    weights = period_years(years, p["horizon_end"])
    label = _LABEL[p["site_type"]]
    conn = p["connection_mw"]
    stages_txt = " → ".join(f"{s['peak_mw']:g}" for s in p["stages"])
    capped = [e for e in p["energization"] if e["mw"] < conn]
    profile_note = (f"the defaults pack's {_PACK_PROFILE[p['load_profile']]} shape (July), stated "
                    "synthetic" if p["load_profile"] in _PACK_PROFILE
                    else f"a synthetic {p['load_profile']} shape")
    notes = [
        f"The {label.lower()}'s peak grows {stages_txt} MW over {', '.join(map(str, years))}; "
        f"each period is the same representative July week at that period's peak.",
        f"Every stage's peak is at or below the grid connection energized in it, so the "
        f"grid-only comparison runs under its own {conn:g} MW connection.",
        "Candidates are one row per period (build_year = the period): the plan says what to "
        "build when.",
        "After a solve the investment case runs on the shipped commercial config and finance "
        "inputs: the existing site is in service in the first period at no capex.",
        "PoC and feeder losses are not modelled (efficiency 1.0), so the investment case can "
        "compare against the grid-only site.",
    ]
    if capped:
        notes.insert(2, "The energization schedule caps the import at "
                     + ", ".join(f"{e['mw']:g} MW in {e['year']}" for e in capped) + ".")
    if p["existing"]["ups"]["mw"] <= 0:
        notes.append("No UPS: the island rows (ups_bridged) are off for this site.")
    return {
        "id": TEMPLATE_ID,
        "name": f"{label} Energy Hub (generic site)",
        "site_type": p["site_type"],
        "site_type_label": SITE_TYPE_TEXT[p["site_type"]][0],
        "params": copy.deepcopy(p),
        "entered": list(entered or []),
        "description": (
            f"A generic {label.lower()} planned from a projected load: the peak grows "
            f"{stages_txt} MW behind a {conn:g} MW grid connection, with the existing on-site "
            "supply and genset, battery and PV candidates per period. Multi-period; tagged "
            "for an Energy Hub study."),
        "recommended_archetype": "weak_flexible",
        "pack_overrides": {"import_p_nom_mw": conn},
        "stages": None,
        "dtc_attribution": "per_load",
        "projected_load": [
            {"year": s["year"], "peak_mw": s["peak_mw"],
             "note": "modelled peak of the site load; synthetic preset unless set by the user"}
            for s in p["stages"]],
        "energization": [
            {"year": e["year"], "available_import_mw": e["mw"], "connection_mw": conn,
             "source": "assumed", "note": "utility energization schedule (assumed)"}
            for e in p["energization"]],
        "period_years": {str(y): weights[y] for y in years},
        "ledger": [
            {"item": "load shape", "value": p["load_profile"], "source": "synthetic",
             "note": profile_note + "; replace with the site's metered load"},
            {"item": "critical share", "value": f"{p['critical_share']:g} of the load",
             "source": "assumed",
             "note": "on its own bus behind a loss-free feeder, served first (DtC per load)"},
            {"item": "discount rate", "value": f"{DISCOUNT_RATE:.0%} nominal",
             "source": "assumed", "note": "the solve discounts each period's costs at it"},
            {"item": "PoC and feeder losses", "value": "efficiency 1.0 on grid_import and "
                                                       "critical_feeder",
             "source": "assumed",
             "note": "losses are not modelled, so the investment case's counterfactual bill "
                     "(the served load at the meter) is established"},
            {"item": "import tariff", "value": f"{SITE_TARIFF['id']}: network energy charge "
                                               "35 EUR/MWh 08-20 h, 20 EUR/MWh otherwise",
             "source": "synthetic",
             "note": "an illustrative network-use charge on top of the wholesale price; no "
                     "demand charge; replace with the site's tariff"},
            {"item": "existing site", "value": ", ".join(existing_assets(p))
                                               + f": COD {years[0]}-01-01, overnight cost 0",
             "source": "assumed", "note": "in service at the first period and sunk"},
            {"item": "finance inputs", "value": "nominal EUR (2020 money), escalation 2 %/yr, "
                                                f"all equity at 7 %, to {p['horizon_end']}",
             "source": "assumed", "note": "pre-tax; a later stage's capex is paid a year "
                                          "before its COD"},
            {"item": "fault level at the PoC", "value": f"{p['fault_level_mva']:g} MVA",
             "source": "assumed", "note": "20 × the connection unless set"},
            {"item": "power factor and load damping",
             "value": "PF 0.98, load damping D = 0 (gridspine defaults)",
             "source": "assumed",
             "note": "the electrical and frequency studies use the data center's defaults; "
                     "PF 0.98 is optimistic for a motor-heavy site, D = 0 is conservative "
                     "(per-site values are a later slice)"},
        ],
        "study_notes": notes,
        "provenance": _T.PROVENANCE,
    }


def write_sidecars(out_dir, p: dict, n: pypsa.Network | None = None,
                   cfg: dict | None = None, entered: list[str] | None = None) -> None:
    """The template metadata, stress registry, solver settings (``cfg``, else
    built on ``n``) and (with a UPS) the island sidecar, beside ``network.nc``."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def dump(name, obj):
        (out_dir / name).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")

    dump(_T.META_FILE, template_meta(p, entered))
    dump(_T.STRESS_FILE, {"__schema__": _T.SCHEMA, "scenarios": stress_scenarios(p)})
    dump(_T.SOLVER_FILE, cfg if cfg is not None else solver_config(p, n))
    isl = island_config(p)
    if isl is not None:
        dump(_T.ISLAND_FILE, isl)
