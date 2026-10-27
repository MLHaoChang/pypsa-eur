"""
Energy Hub project templates (plan 2026-09-26 P19).

Three ready-to-run hubs — one per archetype pack — so an EH study needs no
hand-parameterisation. Each network already carries the P14 tags
(``eh_poc`` / ``eh_critical`` / ``eh_sk_mva`` / ``eh_ibr_mva`` / ``eh_role``),
occurrence data on the thermal fleet and on the Links the archetype needs,
and a small extendable candidate set so the ENS-capped expansion has
something to size. ``TEMPLATE_META`` is written beside each ``network.nc``
as ``eh_template.json`` together with the template's stress-scenario
registry, and both are copied into a project created from the template.

Data are SYNTHETIC and illustrative: representative-week profiles with the
right shape and order of magnitude, costs in the range of public cost
catalogues. They are for learning and demonstrating the workflow, not for a
real project decision — ``TEMPLATE_META[...]["provenance"]`` says so.

Horizon: one representative 168 h week weighted 8760/168 per snapshot for
cost and energy (``objective``, ``generators``), so ``nyears ≈ 1`` (no CAPEX
scaling warning) and every MTTR here (≤ 72 h) fits the MC's MTTR floor (P11,
decision Q2). The ``stores`` weighting stays 1: PyPSA reads it as the hours
each snapshot lasts in the state-of-charge balance, and a snapshot here lasts
one hour (plan 2026-10-07 campus island, S0). Weighting it 52.14 made every
hourly dispatch move 52 hours of stored energy.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd
import pypsa

HOURS = 168
SNAPSHOTS = pd.date_range("2030-07-01", periods=HOURS, freq="h")
WEIGHT = 8760.0 / HOURS

_H = np.arange(HOURS)
_HOD = _H % 24
_DAY = _H // 24

PROVENANCE = ("synthetic illustrative data (P19 template) — representative "
              "week, public-catalogue-range costs; replace with project data "
              "before any decision")


def _solar(peak_hour: float = 13.0, width: float = 3.2,
           cloud: tuple[float, ...] = (1.0, 0.9, 0.55, 0.8, 1.0, 0.95, 0.7)
           ) -> np.ndarray:
    """Per-unit PV availability: a daylight bell times a daily cloud factor."""
    bell = np.exp(-((_HOD - peak_hour) ** 2) / (2 * width ** 2))
    bell[(_HOD < 6) | (_HOD > 20)] = 0.0
    return np.clip(bell * np.array(cloud)[_DAY], 0.0, 1.0)


def _wind(mean: float = 0.38, seed: int = 7) -> np.ndarray:
    """Per-unit wind availability: a slow synoptic swing plus gusts."""
    rng = np.random.default_rng(seed)
    synoptic = mean + 0.25 * np.sin(2 * np.pi * _H / 96.0 + 0.8)
    gust = rng.normal(0.0, 0.07, HOURS)
    return np.clip(synoptic + gust, 0.02, 0.98)


def _day_shape(base: float, peak: float, peak_hour: float = 15.0,
               width: float = 4.5) -> np.ndarray:
    return base + (peak - base) * np.exp(-((_HOD - peak_hour) ** 2) / (2 * width ** 2))


def _base(name: str, snapshots=SNAPSHOTS) -> pypsa.Network:
    n = pypsa.Network()
    n.name = name
    n.set_snapshots(snapshots)
    n.snapshot_weightings.loc[:, ["objective", "generators"]] = WEIGHT
    n.snapshot_weightings.loc[:, "stores"] = 1.0          # hours per snapshot (S0)
    return n


def _outage(n, component: str, name: str, rate: float, mttr_h: float) -> None:
    """Forced-outage rate (FOR basis) and MTTR on one asset."""
    df = getattr(n, component)
    for col, default in (("outage_rate_value", float("nan")),
                         ("mttr_hours", float("nan"))):
        if col not in df.columns:
            df[col] = default
    df.at[name, "outage_rate_value"] = rate
    df.at[name, "mttr_hours"] = mttr_h


def _tag_columns(n) -> None:
    """Typed EH tag columns (P14) with their defaults on every row."""
    n.buses["eh_poc"] = False
    n.buses["eh_critical"] = False
    n.buses["eh_sk_mva"] = float("nan")
    n.buses["eh_ibr_mva"] = float("nan")
    n.links["eh_role"] = ""


# ── Investment costs from the generic defaults pack (IC S3 seeds) ─────────
# Owner 2026-10-13: every candidate is priced from the pack through the asset
# schema (overnight parts; PyPSA or `derive.apply_parts` annuitises them at
# the pack's discount rate), never from an annualised literal.
TEMPLATE_PACK_VERSION = "2026-10-26"


def _price(n, component: str, name: str, technology: str) -> None:
    """Price one candidate from the pack (`defaults_pack.pricing.price_from_pack`)."""
    from services.library.defaults_pack.pricing import price_from_pack

    price_from_pack(n, component, name, technology, version=TEMPLATE_PACK_VERSION)


# ── Data Center Energy Hub (weak_flexible) ─────────────────────────────────


def build_eh_datacenter(phases=None, load_spec=None, planning=None) -> pypsa.Network:
    """A 50 MW hyperscale campus behind a weak, capped grid connection.

    Critical IT sits on its own bus behind the site transformer (a near
    loss-free Link, so the P16 per-Load priority is exact); cooling and
    offices share the MV bus and are the non-critical demand. A UPS battery
    rides on the IT bus, a four-unit gas genset fleet on the MV bus.

    ``phases`` (a ``_DcPlan``) makes it the staged-growth campus
    (``_staged_datacenter``); None is the single-year template, unchanged.
    ``load_spec`` (a checked ``DataCentreLoadSpec``, ``dc_load_spec``) opts in
    to the AI-training profile; None keeps today's load bit for bit.
    ``planning`` (single-year, E1d) dates the week in its start year.
    """
    snaps = _week(planning)
    n = _base("Data Center Energy Hub", snaps)
    for c, co2 in (("AC", 0.0), ("gas", 0.20), ("solar", 0.0),
                   ("battery", 0.0), ("grid", 0.35)):
        n.add("Carrier", c, co2_emissions=co2)
    n.add("Bus", "grid", v_nom=132.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "dc_mv", v_nom=33.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "it_bus", v_nom=11.0, carrier="AC", x=0.0, y=0.0)

    # Wholesale supply beyond the PoC (not firm capacity of the hub).
    price = 70.0 + 40.0 * np.exp(-((_HOD - 19) ** 2) / 8.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid",
          p_nom=500.0, marginal_cost=0.0)
    n.generators_t.marginal_cost = pd.DataFrame(
        {"grid_supply": price}, index=snaps)

    # The weak connection: 40 MW is the physical rating, so the FMEA
    # worksheet (saved network) and the EH study (pack cap) see one system.
    n.add("Link", "grid_import", bus0="grid", bus1="dc_mv", p_nom=40.0,
          efficiency=0.995, carrier="AC")
    n.add("Link", "site_transformer", bus0="dc_mv", bus1="it_bus", p_nom=80.0,
          efficiency=0.99, carrier="AC")

    rng = np.random.default_rng(11)
    it = 36.0 + rng.normal(0.0, 0.6, HOURS)
    n.add("Load", "it_load", bus="it_bus", carrier="AC")
    n.add("Load", "cooling", bus="dc_mv", carrier="AC")
    n.add("Load", "offices", bus="dc_mv", carrier="AC")
    n.loads_t.p_set = pd.DataFrame({
        "it_load": it,
        "cooling": _day_shape(8.0, 14.0),
        "offices": _day_shape(0.4, 1.6, peak_hour=12.0, width=3.0),
    }, index=snaps)

    for i in range(1, 5):
        g = f"genset_{i}"
        n.add("Generator", g, bus="dc_mv", carrier="gas", p_nom=10.0,
              marginal_cost=165.0)
        _outage(n, "generators", g, 0.03, 24.0)
    n.add("Generator", "rooftop_pv", bus="dc_mv", carrier="solar", p_nom=4.0,
          marginal_cost=0.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"rooftop_pv": _solar()}, index=snaps)
    # The UPS carries the IT load it protects (about 36 MW) for a realistic
    # 15 minutes: 40 MW / 10 MWh, enough for the island plan's 600 s bridge
    # (owner decision, plan 2026-10-07 campus island, S0).
    n.add("StorageUnit", "ups_battery", bus="it_bus", carrier="battery",
          p_nom=40.0, max_hours=0.25, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)

    # Candidates the ENS-capped expansion may build, priced from the pack.
    n.add("Generator", "genset_new", bus="dc_mv", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=40.0, marginal_cost=165.0)
    _price(n, "generators", "genset_new", "gas-genset")
    _outage(n, "generators", "genset_new", 0.03, 24.0)
    n.add("StorageUnit", "bess_new", bus="it_bus", carrier="battery",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=30.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)
    _price(n, "storage_units", "bess_new", "battery")

    _tag_columns(n)
    n.buses.at["grid", "eh_poc"] = True
    # SCR gate inputs at the PoC: a 250 MVA fault level against ~30 MVA of
    # inverter-based resources (PV + BESS inverters; the UPS feeds no fault
    # upstream, its rectifier blocks it) → SCR ≈ 8, which passes. The connection is CAPACITY-weak (40 MW), not SCR-weak.
    n.buses.at["grid", "eh_sk_mva"] = 250.0
    n.buses.at["grid", "eh_ibr_mva"] = 30.0
    n.buses.at["dc_mv", "eh_sk_mva"] = 220.0
    n.buses.at["dc_mv", "eh_ibr_mva"] = 25.0
    n.buses.at["it_bus", "eh_critical"] = True
    n.links.at["grid_import", "eh_role"] = "grid_import"
    # The import is firm only with its own outage data (decision 6 / Q7).
    _outage(n, "links", "grid_import", 0.002, 12.0)
    # Site transformer failure islands the IT bus onto its UPS: a Class-B
    # mode fmea_top should rank.
    _outage(n, "links", "site_transformer", 0.005, 72.0)
    if phases is not None:
        return _staged_datacenter(n, phases, load_spec)
    if load_spec is not None:
        _apply_dc_profile(n, load_spec, snaps, _single_year_phase(load_spec, planning).it_mw,
                          timezone=_dc_timezone("eh_datacenter"))
    return n


# ── Planning years (E1; workspace reconciliation §3.3, owner Q3 = B) ─────────
#
# A staged example is built for the years a create body's ``planning`` enters:
# the start year and the later investment dates are the periods, the end year
# the horizon. Without dates the example's own spacing is shifted to the
# start year and a stage at or after the end year is dropped (owner decision
# E1-stages, 2026-10-21). Each period's years run to the next period, the
# last to the end year; the solve's ``auto_discount_periods`` derives the
# objective weights from them, so nothing here discounts. A growth value the
# example has no figure for in an entered year (IT MW, energized MW) is the
# example's value for the same stage, marked a placeholder in the metadata
# (``growth_inputs``) and the ledger, never interpolated.

#: The bounds of an entered year and the most periods (the generic site's).
PLANNING_YEAR_MIN, PLANNING_YEAR_MAX, PLANNING_MAX_PERIODS = 2025, 2060, 3
_PLANNING_KEYS = ("start_year", "end_year", "dates")


class PlanningError(ValueError):
    """A refused ``planning`` entry, naming its field (``planning.<key>``)."""

    def __init__(self, message: str, field: str):
        super().__init__(message)
        self.field = field


class Planning(NamedTuple):
    """Investment periods ``years`` (increasing) and the horizon ``end_year``.
    (A NamedTuple, not a dataclass: this file is loaded by path without a
    ``sys.modules`` entry, which a dataclass's annotation check needs.)"""

    years: tuple[int, ...]
    end_year: int

    @property
    def start_year(self) -> int:
        return self.years[0]

    @property
    def analysis_years(self) -> int:
        return self.end_year - self.start_year

    @property
    def period_years(self) -> dict[int, int]:
        """The years each period stands for: to the next, the last to the end year."""
        return {y: e - y for y, e in zip(self.years, (*self.years[1:], self.end_year))}


#: The single-year examples' horizon end: the staged templates' (E1d).
SINGLE_YEAR_HORIZON_END = 2045

#: The years each example is built for when no ``planning`` is entered.
NATIVE_PLANNING: dict[str, Planning] = {
    "eh_datacenter_staged": Planning((2027, 2030, 2035), 2045),
    **{tid: Planning((int(SNAPSHOTS[0].year),), SINGLE_YEAR_HORIZON_END)
       for tid in ("eh_datacenter", "eh_h2_hub", "eh_microgrid")},
}
#: The single-year examples (E1d): one period, the start year, which dates the
#: modelled week (or year); the end year is the horizon. True where the
#: example ships an investment case, which needs a later end year.
SINGLE_YEAR: dict[str, bool] = {"eh_datacenter": False, "eh_h2_hub": False,
                                "eh_microgrid": True}


def _week(planning: Planning | None = None) -> pd.DatetimeIndex:
    """The representative week: ``SNAPSHOTS``, or the same week (1 July on)
    in the entered start year of a single-year example (E1d)."""
    if planning is None or planning.start_year == SNAPSHOTS[0].year:
        return SNAPSHOTS
    return pd.date_range(f"{planning.start_year}-07-01", periods=HOURS, freq="h")


def _planning_year(raw: dict, key: str) -> int:
    field = f"planning.{key}"
    if key not in raw:
        raise PlanningError(f"{field}: required", field)
    y = raw[key]
    if isinstance(y, bool) or not isinstance(y, int) \
            or not PLANNING_YEAR_MIN <= y <= PLANNING_YEAR_MAX:
        raise PlanningError(f"{field}: a whole year from {PLANNING_YEAR_MIN} to "
                            f"{PLANNING_YEAR_MAX}, got {y!r}", field)
    return y


def planning_spec(template_id: str, raw) -> Planning:
    """The checked ``planning`` entry for an example, or ``PlanningError``."""
    if not isinstance(raw, dict):
        raise PlanningError("planning: must be an object {start_year, end_year, dates?}",
                            "planning")
    extra = sorted(set(raw) - set(_PLANNING_KEYS))
    if extra:
        raise PlanningError(f"planning.{extra[0]}: unknown key", f"planning.{extra[0]}")
    start, end = _planning_year(raw, "start_year"), _planning_year(raw, "end_year")
    if template_id in SINGLE_YEAR:
        return _single_year_planning(template_id, raw, start, end)
    if end == start:
        raise PlanningError("planning.end_year: one year is a single-period study; this is a "
                            "staged example, so use the single-year example or a later end year",
                            "planning.end_year")
    if end < start:
        raise PlanningError(f"planning.end_year: after the start year ({start}), got {end}",
                            "planning.end_year")
    native = NATIVE_PLANNING[template_id]
    if "dates" not in raw:
        years = tuple(start + y - native.start_year for y in native.years
                      if start + y - native.start_year < end)
        return Planning(years, end)
    dates = raw["dates"]
    if not isinstance(dates, list) or any(isinstance(d, bool) or not isinstance(d, int)
                                          for d in dates):
        raise PlanningError(f"planning.dates: a list of whole years, got {dates!r}",
                            "planning.dates")
    years = (start, *dates)
    if len(years) > PLANNING_MAX_PERIODS:
        raise PlanningError(f"planning.dates: at most {PLANNING_MAX_PERIODS} periods with the "
                            f"start year, got {list(years)}", "planning.dates")
    if list(years) != sorted(set(years)) or years[-1] >= end:
        raise PlanningError(f"planning.dates: increasing years after the start year ({start}) "
                            f"and before the end year ({end}), got {dates}", "planning.dates")
    return Planning(years, end)


def _single_year_planning(template_id: str, raw: dict, start: int, end: int) -> Planning:
    """A single-year example: no dates; the end year at or after the start
    year, after it where the example's investment case runs to it (E1d)."""
    if "dates" in raw:
        raise PlanningError("planning.dates: a single-year example has one period, the start "
                            "year; use its staged example for investment dates", "planning.dates")
    if SINGLE_YEAR[template_id] and end <= start:
        raise PlanningError(f"planning.end_year: the investment case runs from the start year "
                            f"to the end year, so it must be after the start year ({start}), "
                            f"got {end}", "planning.end_year")
    if end < start:
        raise PlanningError(f"planning.end_year: the start year ({start}) or later, got {end}",
                            "planning.end_year")
    return Planning((start,), end)


def _is_native(template_id: str, planning: Planning | None) -> bool:
    return planning is None or planning == NATIVE_PLANNING[template_id]


def _by_stage(planning: Planning, native: Planning, values: dict[int, float]):
    """``[(year, value, set)]``: each entered period takes the example's value
    for the same stage; it is ``set`` only where the year is the example's."""
    out = []
    for i, y in enumerate(planning.years):
        ny = native.years[min(i, len(native.years) - 1)]
        out.append((y, values[ny], y == ny))
    return out


def _years_text(years) -> str:
    years = [str(y) for y in years]
    return years[0] if len(years) == 1 else f"{', '.join(years[:-1])} and {years[-1]}"


# ── Data Center Energy Hub, staged growth (E2E demo plan 2026-10-14, slice 8) ─
#
# The same campus planned from a projected load: three investment periods, the
# IT load ramping as the data halls are fitted out, and the utility energizing
# a 55 MW connection in steps. Everything else is the single-year template.
# Synthetic illustrative numbers, like the rest of this file.

#: The projected load: (period year, IT MW, PUE). Cooling scales with the IT
#: load (the single-year template's 8-14 MW profile is for 36 MW of IT).
DC_PHASES = ((2027, 12.0, 1.3), (2030, 24.0, 1.3), (2035, 36.0, 1.3))
#: The staged campus's PoC rating (MW): sized to its 2035 peak (about 52.5 MW
#: modelled), so the grid-only comparison of the investment case can run (owner
#: decision 2026-10-15). The single-year template keeps its weak 40 MW.
_DC_POC_MW = 55.0
#: The utility's energization schedule of the connection (assumed): the import
#: available in each period, in MW. 2027 is energized below that period's peak
#: (about 18.4 MW), so the early grid limit drives the first build and the
#: grid-only comparison sheds then (owner decision 2026-10-15).
DC_ENERGIZATION_MW = {2027: 15.0, 2030: _DC_POC_MW, 2035: _DC_POC_MW}
#: Years each period stands for (2027-29, 2030-34, 2035-44): the horizon ends in
#: 2045, before any candidate's pack lifetime (25 years) runs out.
DC_PERIOD_YEARS = {2027: 3, 2030: 5, 2035: 10}
_DC_IT_MW = 36.0                                   # the single-year template's IT load


class _DcPlan(NamedTuple):
    """The staged campus for one ``Planning``: per period (year, IT MW, PUE),
    the energized import (MW) and whether each growth value is the example's
    own (``set``) or its same-stage placeholder."""

    planning: Planning
    phases: tuple
    energization: dict
    it_set: dict
    energized_set: dict


def _dc_plan(planning: Planning | None = None) -> _DcPlan:
    native = NATIVE_PLANNING["eh_datacenter_staged"]
    p = planning or native
    it = _by_stage(p, native, {y: v for y, v, _pue in DC_PHASES})
    pue = _by_stage(p, native, {y: v for y, _it, v in DC_PHASES})
    en = _by_stage(p, native, DC_ENERGIZATION_MW)
    return _DcPlan(p, tuple((y, it_mw, pue_y)
                            for (y, it_mw, _set), (_year, pue_y, _pue_set) in zip(it, pue)),
                   {y: mw for y, mw, _set in en}, {y: is_set for y, _mw, is_set in it},
                   {y: is_set for y, _mw, is_set in en})

# ── The staged campus's commercial and finance inputs (slice D4) ────────────
# Inline only (no Library refs a fresh project would not have), so the
# investment case computes on a project made from the template. Synthetic
# illustrative numbers; the ledger in TEMPLATE_META says what each one is.

#: The existing campus, in service at the first period (overnight cost 0).
DC_EXISTING = ("genset_1", "genset_2", "genset_3", "genset_4", "rooftop_pv", "ups_battery",
               "site_transformer")
#: A synthetic network-use charge on the metered import (EUR/kWh), on top of
#: the wholesale price the grid supply carries.
DC_TARIFF: dict = {
    "id": "dc_network_tariff_synthetic",
    "name": "Synthetic network tariff (illustrative)",
    "jurisdiction": "DE",
    "valid_from": "2027-01-01",
    "items": [{"id": "network_energy", "kind": "energy", "unit": "per_kwh",
               "periods": [{"name": "day", "rate": 0.035, "start_hour": 8, "end_hour": 20},
                           {"name": "night", "rate": 0.020}]}],
}


def _dc_tariff(plan: _DcPlan) -> dict:
    """The tariff, valid from the first period."""
    return {**DC_TARIFF, "valid_from": f"{plan.planning.start_year}-01-01"}


def _dc_finance(plan: _DcPlan) -> dict:
    """The finance inputs over the planning horizon: close the year before the
    first period, every existing asset in service then, the case running to
    the end year (Σ of the periods' years, so the LP and the case count the
    same years)."""
    p = plan.planning
    return {
        "currency": "EUR", "currency_year": 2025, "price_basis": "nominal",
        "financial_close": f"{p.start_year - 1}-01-01",
        "cod_by_asset": {a: f"{p.start_year}-01-01" for a in DC_EXISTING},
        "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
        "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
        # Every owned generator and battery needs an entry (the engine never reads
        # a missing one as 0): PV 0.5 %/yr, the gensets and batteries none (the
        # batteries on the no-degradation basis, as the guided case).
        "degradation_by_asset": {
            **{a: 0.0 for a in DC_EXISTING if a not in ("rooftop_pv", "site_transformer")},
            "rooftop_pv": 0.005,
            **{f"{k}_new_{y}": 0.0 for y in p.years for k in ("genset", "bess")}},
        "analysis_years": p.analysis_years,                         # 2027-2044 natively
        "annualise": False,                                         # each period weighs 8,760 h
        "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
        "replacement_rule": "part_lifetimes",
        "terminal_value": {"method": "remaining_life_annuity"},
        "wacc_nominal": 0.07, "cost_of_equity": 0.07, "inflation": 0.02,
    }


DC_FINANCE: dict = _dc_finance(_dc_plan())


def _staged_datacenter(n: pypsa.Network, plan: _DcPlan, load_spec=None) -> pypsa.Network:
    """The single-year campus as a plan over ``plan``'s periods: the week
    repeated in each period, the load scaled to its phases, the import capped
    per period by its energization (an absolute MW schedule on the
    ``_DC_POC_MW`` link), and the two expansion candidates replaced by one
    candidate per period with that period as its ``build_year`` (explicit
    rows, not vintage bounds, so the finance adapter sees each stage)."""
    phases = plan.phases
    years = [int(y) for y, _it, _pue in phases]
    period_years = plan.planning.period_years
    flat = {
        ("generators_t", "p_max_pu"): n.generators_t.p_max_pu.copy(),
        ("generators_t", "marginal_cost"): n.generators_t.marginal_cost.copy(),
        ("loads_t", "p_set"): n.loads_t.p_set.copy(),
    }
    snaps = pd.MultiIndex.from_product([years, SNAPSHOTS], names=["period", "timestep"])
    n.set_snapshots(snaps)
    n.investment_periods = years
    n.snapshot_weightings.loc[:, ["objective", "generators"]] = WEIGHT
    n.snapshot_weightings.loc[:, "stores"] = 1.0                    # hours per snapshot (S0)
    n.investment_period_weightings["years"] = [float(period_years[y]) for y in years]
    n.investment_period_weightings["objective"] = [float(period_years[y]) for y in years]
    snaps = n.snapshots                                 # PyPSA's own index (names and all)
    for (frame, attr), df in flat.items():
        tiled = pd.DataFrame(np.tile(df.to_numpy(), (len(years), 1)), index=snaps, columns=df.columns)
        getattr(n, frame)[attr] = tiled
    p_set = n.loads_t.p_set
    if load_spec is None:
        for y, it_mw, _pue in phases:
            scale = it_mw / _DC_IT_MW
            for load in ("it_load", "cooling"):
                p_set.loc[y, load] = (p_set.loc[y, load] * scale).to_numpy()
    else:
        it_by_year = {p.cod.year: p.it_mw for p in load_spec.phases}
        tz = _dc_timezone("eh_datacenter_staged")
        for y in years:
            _apply_dc_profile(n, load_spec, SNAPSHOTS, it_by_year[y], timezone=tz, period=y)
    # The energization schedule in MW (`services.adequacy.energization`): a
    # solve turns it into p_max_pu = min(own, E / p_nom), so a lever or an
    # overlay that rewrites p_nom composes as min(E, cap) instead of
    # rescaling a stored fraction (gatekeeper review of #266).
    n.links_t["eh_energization_mw"] = pd.DataFrame(
        {"grid_import": np.repeat([plan.energization[y] for y in years], len(SNAPSHOTS))},
        index=snaps)
    n.remove("Generator", "genset_new")
    n.remove("StorageUnit", "bess_new")
    n.links.at["grid_import", "p_nom"] = _DC_POC_MW
    # The investment case's counterfactual bill is the served load at the
    # meter (`finance_case._lossy_poc`): with a lossy PoC chain it is not the
    # metered import, and the case is refused. Losses are not modelled in the
    # staged plan (the ledger says so); the single-year template keeps them.
    n.links.loc[["grid_import", "site_transformer"], "efficiency"] = 1.0
    # The existing campus is in service at the first period: a typed
    # overnight cost of 0 is an established 0 (no capex, never a cost
    # back-calculated from capital_cost). The PoC meter link stays untyped:
    # it is not an investment.
    for frame in ("generators", "storage_units", "links"):
        df = getattr(n, frame)
        rows = [a for a in DC_EXISTING if a in df.index]
        # PyPSA annuitises a typed overnight cost and needs the rate to (the
        # template's 7 %; 0 × anything is 0, so the LP is unchanged).
        df.loc[rows, ["overnight_cost", "discount_rate"]] = [0.0, 0.07]
    for y in years:
        g, b = f"genset_new_{y}", f"bess_new_{y}"
        n.add("Generator", g, bus="dc_mv", carrier="gas", p_nom=0.0, p_nom_extendable=True,
              p_nom_max=40.0, marginal_cost=165.0, build_year=y)
        _price(n, "generators", g, "gas-genset")
        _outage(n, "generators", g, 0.03, 24.0)
        n.add("StorageUnit", b, bus="it_bus", carrier="battery", p_nom=0.0, p_nom_extendable=True,
              p_nom_max=30.0, max_hours=4.0, efficiency_store=0.95, efficiency_dispatch=0.95,
              cyclic_state_of_charge=True, build_year=y)
        _price(n, "storage_units", b, "battery")
    # Storage cycles within each period: each period's week stands for 3-10
    # years, so no state of charge is carried into the next period
    # (gatekeeper review of #304; the same rule as eh_site.stage_network).
    n.storage_units["cyclic_state_of_charge"] = True
    n.storage_units["cyclic_state_of_charge_per_period"] = True
    n.name = "Data Center Energy Hub (staged growth)"
    return n


def build_eh_datacenter_staged(load_spec=None, planning: Planning | None = None) -> pypsa.Network:
    """The staged campus for ``planning`` (None: the example's 2027 / 2030 /
    2035 to 2045)."""
    return build_eh_datacenter(phases=_dc_plan(planning), load_spec=load_spec)


# ── The AI-training load profile, opt-in (DC grid readiness 01) ─────────────
#
# A project created with a ``dc_load`` body replaces the data center's IT and
# cooling profiles with `services.dc_load_profile`'s facility load: the
# critical share on it_load (the critical it_bus), the rest on cooling.
# Offices are kept. Without the body the template is unchanged.

def _dc_timezone(template_id: str) -> str | None:
    """The template's ``commercial.timezone`` (the staged campus: UTC), so its
    naive snapshots are UTC mapped on that zone's clock; None (the single-year
    template has no commercial block) means the site clock, assumed."""
    commercial = solver_config(template_id).get("commercial") or {}
    return commercial.get("timezone") or None


def dc_load_preset(template_id: str, planning: Planning | None = None) -> dict:
    """The opt-in spec's defaults for ``template_id`` (all assumed): today's
    phases, PUE 1.3 at or below 20 °C plus 0.02 per K above, 75 % critical.
    The single-year campus's phase is in service from its week's year."""
    phases = ([{"cod": f"{y}-01-01", "it_mw": it} for y, it, _pue in DC_PHASES]
              if template_id == "eh_datacenter_staged"
              else [{"cod": f"{_week(planning)[0].year}-01-01", "it_mw": _DC_IT_MW}])
    return {"phases": phases, "utilisation": "ai_training", "pue_at_design": 1.3,
            "pue_temperature_slope_per_k": 0.02, "redundancy": "N+1",
            "critical_share": 0.75, "curtailable_share": 0.0}


def _single_year_phase(spec, planning: Planning | None = None):
    week = _week(planning)[0].date()
    live = [p for p in spec.phases if p.cod <= week]
    return live[-1] if live else None


def _apply_dc_profile(n, spec, snapshots, it_mw: float, *, timezone, period=None) -> None:
    from services.dc_load_profile import hourly_profile

    facility = hourly_profile(spec, snapshots, it_mw, timezone=timezone)
    p_set = n.loads_t.p_set
    rows = slice(None) if period is None else period
    p_set.loc[rows, "it_load"] = spec.critical_share * facility
    p_set.loc[rows, "cooling"] = (1.0 - spec.critical_share) * facility


def dc_load_spec(template_id: str, raw: dict | None, planning: Planning | None = None):
    """The checked opt-in spec from a create body's ``dc_load`` (merged over
    ``dc_load_preset``), or ``DcLoadError`` naming the field. The staged
    campus needs one phase per period of ``planning`` (None: the example's)."""
    import pydantic

    from models.flex_archetypes import DataCentreLoadSpec
    from services.dc_load_profile import DcLoadError

    if not isinstance(raw, dict):
        raise DcLoadError("dc_load: must be an object", "dc_load")
    merged = {**dc_load_preset(template_id, planning), **raw}
    merged.setdefault("ai_workload", None)
    if merged["ai_workload"] is None:
        merged["ai_workload"] = {}
    try:
        spec = DataCentreLoadSpec.model_validate(merged)
    except pydantic.ValidationError as exc:
        err = exc.errors()[0]
        field = ".".join(["dc_load", *(str(x) for x in err["loc"])])
        raise DcLoadError(f"{field}: {err['msg']}", field) from None
    if spec.utilisation != "ai_training":
        raise DcLoadError("dc_load.utilisation: only 'ai_training' is modelled",
                          "dc_load.utilisation")
    if spec.critical_share <= 0:
        raise DcLoadError("dc_load.critical_share: must be > 0 (it_load is the critical load)",
                          "dc_load.critical_share")
    if template_id == "eh_datacenter_staged":
        expected = list(_dc_plan(planning).planning.years)
        got = [p.cod.year for p in spec.phases]
        if got != expected:
            raise DcLoadError(f"dc_load.phases: the staged template's periods are {expected}; "
                              f"one phase per period with that COD year is needed, got {got}",
                              "dc_load.phases")
    elif _single_year_phase(spec, planning) is None:
        raise DcLoadError(f"dc_load.phases: no phase is in service by the modelled week "
                          f"({_week(planning)[0].date()})", "dc_load.phases")
    _check_dc_peak(template_id, spec, planning)
    return spec


def firm_genset_mw(n, template_id: str) -> tuple[float, str]:
    """The committed genset fleet's firm MW and the rule that gave it: the
    island requirements' genset N+k (``genset_redundancy_n``) takes out the
    k largest units; without it the whole fleet counts
    (``services.adequacy.site_peak.firm_genset_mw`` on the template's sidecar)."""
    from services.adequacy.site_peak import firm_genset_mw as firm

    return firm(n, ISLAND_CONFIG.get(template_id, {"units": {}}))


def _check_dc_peak(template_id: str, spec, planning: Planning | None = None) -> None:
    """Each hour's modelled load must fit the energized import (after the
    PoC Link's efficiency) plus the committed fleet's firm genset MW; above that
    the plan could only shed (gatekeeper review of DC grid readiness 01).
    The one check the grid-readiness scorecard also reports
    (``services.adequacy.site_peak.peak_check``)."""
    from services.adequacy.site_peak import describe, peak_check, unset_periods
    from services.dc_load_profile import DcLoadError

    if planning is None:
        n = BUILDERS[template_id](load_spec=spec)
    else:
        n = BUILDERS[template_id](load_spec=spec, planning=planning)
    # A period whose energization is a placeholder is unknown, never refused.
    unset = unset_periods(template_meta(template_id, planning, load_spec=spec))
    for row in peak_check(n, island_config(template_id, planning), unset=unset):
        if row["headroom_mw"] is not None and row["headroom_mw"] < -1e-9:
            raise DcLoadError(
                f"dc_load.phases: the {row['period']} peak is {row['load_mw']:.1f} MW (at "
                f"{row['binding_snapshot']}), above {describe(row)}", "dc_load.phases")


def dc_load_meta(template_id: str, spec) -> dict:
    """The ``dc_load`` block a project created with the spec carries in its
    ``eh_template.json`` (spec, dynamic envelope, ledger)."""
    from services.dc_load_profile import meta_block

    return meta_block(spec, _dc_timezone(template_id))


# ── Industrial Hydrogen Hub (strong_grid) ──────────────────────────────────


def build_eh_h2_hub(load_spec=None, planning=None) -> pypsa.Network:
    """Grid-connected industrial site with wind, PV and an H₂ chain.

    Electrolyser → H₂ store → fuel cell carry the ``eh_role`` conversion
    roles (redundancy scenarios, multi-energy report); the process load is
    the critical demand and an H₂ offtake is the second energy carrier.
    ``planning`` (E1d) dates the week in its start year; ``load_spec`` is unused.
    """
    snaps = _week(planning)
    n = _base("Industrial Hydrogen Hub", snaps)
    for c, co2 in (("AC", 0.0), ("H2", 0.0), ("wind", 0.0), ("solar", 0.0),
                   ("grid", 0.35), ("electrolysis", 0.0), ("fuel cell", 0.0)):
        n.add("Carrier", c, co2_emissions=co2)
    n.add("Bus", "grid", v_nom=220.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "hub", v_nom=110.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "h2", carrier="H2", x=0.0, y=0.0)

    price = 60.0 + 35.0 * np.exp(-((_HOD - 18) ** 2) / 10.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid",
          p_nom=1000.0, marginal_cost=0.0)
    n.generators_t.marginal_cost = pd.DataFrame(
        {"grid_supply": price}, index=snaps)
    n.add("Link", "grid_import", bus0="grid", bus1="hub", p_nom=200.0,
          efficiency=0.995, carrier="AC")

    n.add("Generator", "wind_farm", bus="hub", carrier="wind", p_nom=120.0,
          marginal_cost=0.0)
    n.add("Generator", "solar_park", bus="hub", carrier="solar", p_nom=80.0,
          marginal_cost=0.0)
    n.add("Generator", "wind_new", bus="hub", carrier="wind", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=300.0)
    _price(n, "generators", "wind_new", "onwind")
    n.add("Generator", "solar_new", bus="hub", carrier="solar", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=150.0)
    _price(n, "generators", "solar_new", "solar-utility")
    wind, sun = _wind(), _solar()
    n.generators_t.p_max_pu = pd.DataFrame(
        {"wind_farm": wind, "wind_new": wind,
         "solar_park": sun, "solar_new": sun}, index=snaps)

    n.add("Link", "electrolyser", bus0="hub", bus1="h2", p_nom=60.0,
          efficiency=0.68, carrier="electrolysis")
    n.add("Store", "h2_storage", bus="h2", carrier="H2", e_nom=2000.0,
          e_cyclic=True)
    # Extendable fuel cell: the H₂ chain is the hub's firm backup when the
    # grid connection is lost (DtC planning sizes it).
    n.add("Link", "fuel_cell", bus0="h2", bus1="hub", p_nom=10.0,
          p_nom_extendable=True, p_nom_min=10.0, p_nom_max=80.0,
          efficiency=0.50, carrier="fuel cell")
    _price(n, "links", "fuel_cell", "fuel-cell")
    n.add("Carrier", "battery", co2_emissions=0.0)
    n.add("StorageUnit", "site_battery", bus="hub", carrier="battery",
          p_nom=20.0, max_hours=2.0, efficiency_store=0.95,
          efficiency_dispatch=0.95, cyclic_state_of_charge=True)
    n.add("StorageUnit", "bess_new", bus="hub", carrier="battery", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=200.0, max_hours=6.0,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          cyclic_state_of_charge=True)
    _price(n, "storage_units", "bess_new", "battery")

    n.add("Load", "process_load", bus="hub", carrier="AC")
    n.add("Load", "h2_offtake", bus="h2", carrier="H2", p_set=25.0)
    rng = np.random.default_rng(5)
    n.loads_t.p_set = pd.DataFrame(
        {"process_load": 40.0 + rng.normal(0.0, 1.5, HOURS)}, index=snaps)

    _tag_columns(n)
    n.buses.at["grid", "eh_poc"] = True
    n.buses.at["grid", "eh_sk_mva"] = 5000.0
    n.buses.at["hub", "eh_critical"] = True
    n.buses.at["hub", "eh_sk_mva"] = 900.0
    n.buses.at["hub", "eh_ibr_mva"] = 180.0
    n.links.at["grid_import", "eh_role"] = "grid_import"
    n.links.at["electrolyser", "eh_role"] = "electrolyser"
    n.links.at["fuel_cell", "eh_role"] = "fuel_cell"
    # Link outage data → Class-B contingencies for fmea_top.
    _outage(n, "links", "grid_import", 0.002, 12.0)
    _outage(n, "links", "electrolyser", 0.04, 48.0)
    _outage(n, "links", "fuel_cell", 0.05, 36.0)
    return n


# ── Island Microgrid (off_grid) ─────────────────────────────────────────────

#: The existing island, in service from the first operating year (overnight
#: cost 0); the subsea tie is the PoC meter, not an investment.
MG_EXISTING = ("pv_plant", "wind_turbines", "diesel_1", "diesel_2", "diesel_3",
               "island_battery", "hospital_feeder")
MG_CANDIDATES = ("diesel_new", "bess_new")
#: The representative week is dated 2030 (SNAPSHOTS): the first operating
#: year. The case runs to the staged templates' 2045 horizon end.
MG_FIRST_YEAR = int(SNAPSHOTS[0].year)
MG_HORIZON_END = 2045
#: The staged microgrid's finance inputs (`eh_staged_hubs._mg_finance`) on
#: the plain network's names, one COD for every asset (a single period has
#: no stage). Synthetic illustrative numbers; the ledger says what each is.
def _mg_years(planning: Planning | None) -> tuple[int, int]:
    """(first operating year, horizon end) of the single-year microgrid."""
    return ((MG_FIRST_YEAR, MG_HORIZON_END) if planning is None
            else (planning.start_year, planning.end_year))


def _mg_finance(planning: Planning | None = None) -> dict:
    """``MG_FINANCE`` for an entered start and end year (E1d): close a year
    before the start, every COD the start, the case to the end year."""
    first, end = _mg_years(planning)
    return {
        "currency": "EUR", "currency_year": 2025, "price_basis": "nominal",
        "financial_close": f"{first - 1}-01-01",
        "cod_by_asset": {a: f"{first}-01-01" for a in (*MG_EXISTING, *MG_CANDIDATES)},
        "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
        "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
        # Every owned generator and battery: PV 0.5 %/yr, the rest none.
        "degradation_by_asset": {
            **{a: (0.005 if a == "pv_plant" else 0.0) for a in MG_EXISTING if a != "hospital_feeder"},
            **{a: 0.0 for a in MG_CANDIDATES}},
        "analysis_years": end - first,                                # 2030-2044 natively
        "annualise": False,                                           # the week weighs 8,760 h
        "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
        "replacement_rule": "part_lifetimes",
        "terminal_value": {"method": "remaining_life_annuity"},
        "wacc_nominal": 0.07, "cost_of_equity": 0.07, "inflation": 0.02,
    }


MG_FINANCE: dict = _mg_finance()


def build_eh_microgrid(load_spec=None, planning=None) -> pypsa.Network:
    """A small island system that must run without its mainland tie.

    The ``off_grid`` pack islands the tagged tie Link; PV, wind, a battery
    and a diesel fleet (with outage data) must carry a critical hospital
    feeder and residential demand. The hospital sits behind a feeder Link
    (η = 0.98 ≥ 1/1.05, so the P16 per-Load priority stays exact).
    ``planning`` (E1d) dates the week in its start year; ``load_spec`` is unused.
    """
    snaps = _week(planning)
    n = _base("Island Microgrid", snaps)
    for c, co2 in (("AC", 0.0), ("solar", 0.0), ("wind", 0.0),
                   ("diesel", 0.27), ("battery", 0.0), ("grid", 0.35)):
        n.add("Carrier", c, co2_emissions=co2)
    n.add("Bus", "mainland", v_nom=33.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "island", v_nom=11.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "hospital", v_nom=11.0, carrier="AC", x=0.0, y=0.0)

    n.add("Generator", "mainland_supply", bus="mainland", carrier="grid",
          p_nom=100.0, marginal_cost=110.0)
    # Normally OPEN (p_max_pu = 0): the island runs on its own resources, so
    # the FMEA worksheet sweeps the same islanded system the off_grid study
    # assesses. Set p_max_pu = 1 to study tie-connected operation.
    n.add("Link", "subsea_tie", bus0="mainland", bus1="island", p_nom=20.0,
          p_max_pu=0.0, efficiency=0.97, carrier="AC")
    n.add("Link", "hospital_feeder", bus0="island", bus1="hospital",
          p_nom=10.0, efficiency=0.98, carrier="AC")

    n.add("Generator", "pv_plant", bus="island", carrier="solar", p_nom=12.0)
    n.add("Generator", "wind_turbines", bus="island", carrier="wind", p_nom=6.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"pv_plant": _solar(), "wind_turbines": _wind(mean=0.33, seed=3)},
        index=snaps)
    for i in range(1, 4):
        g = f"diesel_{i}"
        n.add("Generator", g, bus="island", carrier="diesel", p_nom=3.0,
              marginal_cost=260.0)
        _outage(n, "generators", g, 0.05, 48.0)
    n.add("StorageUnit", "island_battery", bus="island", carrier="battery",
          p_nom=6.0, max_hours=4.0, efficiency_store=0.94,
          efficiency_dispatch=0.94, cyclic_state_of_charge=True)
    n.add("Generator", "diesel_new", bus="island", carrier="diesel", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=12.0, marginal_cost=260.0)
    _price(n, "generators", "diesel_new", "diesel-genset")
    _outage(n, "generators", "diesel_new", 0.05, 48.0)
    n.add("StorageUnit", "bess_new", bus="island", carrier="battery",
          p_nom=0.0, p_nom_extendable=True, p_nom_max=15.0, max_hours=4.0,
          efficiency_store=0.94, efficiency_dispatch=0.94,
          cyclic_state_of_charge=True)
    _price(n, "storage_units", "bess_new", "battery")

    n.add("Load", "residential", bus="island", carrier="AC")
    n.add("Load", "hospital_load", bus="hospital", carrier="AC")
    n.loads_t.p_set = pd.DataFrame({
        "residential": _day_shape(3.5, 7.5, peak_hour=19.0, width=3.5),
        "hospital_load": 1.5 + 0.2 * np.sin(2 * np.pi * _HOD / 24.0),
    }, index=snaps)

    _tag_columns(n)
    n.buses.at["mainland", "eh_poc"] = True
    n.buses.at["hospital", "eh_critical"] = True
    n.buses.at["island", "eh_sk_mva"] = 45.0
    n.buses.at["island", "eh_ibr_mva"] = 20.0
    n.links.at["subsea_tie", "eh_role"] = "grid_import"
    # Feeder outage data → a Class-B contingency for fmea_top once the tie
    # is islanded (the tie itself is closed by the pack).
    _outage(n, "links", "hospital_feeder", 0.01, 8.0)
    # The existing island is in service and sunk: a typed overnight cost of 0
    # is an established 0 for the investment case (no capex), and an infinite
    # lifetime keeps it active. Neither row is extendable and 0 annuitises to
    # 0, so the LP is unchanged; the staged copy sets the same values.
    for frame in ("generators", "storage_units", "links"):
        df = getattr(n, frame)
        rows = [a for a in MG_EXISTING if a in df.index]
        df.loc[rows, ["overnight_cost", "discount_rate"]] = [0.0, 0.07]
        df.loc[rows, "lifetime"] = float("inf")
    return n


# ── metadata + sidecars ─────────────────────────────────────────────────────

BUILDERS = {
    "eh_datacenter": build_eh_datacenter,
    "eh_datacenter_staged": build_eh_datacenter_staged,
    "eh_h2_hub": build_eh_h2_hub,
    "eh_microgrid": build_eh_microgrid,
}

# What the GUI preselects and the assistant recommends for each template.
# ``pack_overrides`` / ``stages`` / ``dtc_attribution`` are EhStudyRequest
# fields verbatim, so the panel can post them unchanged.

#: The H2 hubs' island sidecar, said in their ledgers (both variants; each
#: adds its own "island rows" row with what turning the rows on costs).
H2_ISLAND_LEDGER = [
    {"item": "island sidecar",
     "value": "seamless: the batteries form the island and carry the 40 MW process load; "
              "no genset, no UPS",
     "source": "assumed",
     "note": "the campus run's island checks and the report's islanding chapter read it"},
    {"item": "island unit data",
     "value": "batteries: the data center's bess_new block (droop 4 %, tau_f 0.1 s, i_max "
              "1.2 pu, k_gfm_min 1.0, pf 0.95); fuel cell, wind and PV: gridspine's drafted "
              "grid-following default (Q(U), pf 0.95); the data center's frequency limits; "
              "sustained 1 h, bridge 600 s",
     "source": "assumed",
     "note": "demo values copied from the data center and gridspine, not from a datasheet or "
             "a grid code"},
]

#: The growth inputs a staged example reads by year: kind → (label, unit,
#: flow section, component, entity, field).
_GROWTH_KINDS = {
    "it_mw": ("IT load", "MW", "loads", "Load", "it_load", "p_set"),
    "energized_mw": ("Energized import", "MW", "connection", "Link", "grid_import",
                     "eh_energization_mw"),
    # The staged hubs (E1b): the H2 offtake (MW of H2, not an electric load) and
    # the microgrid's load scale per load (×, the example's 2027 load = 1).
    "h2_offtake_mw": ("H2 offtake", "MW", "loads", "Load", "h2_offtake", "p_set"),
    "load_scale": ("Load scale", "×", "loads", "Load", None, "p_set"),
    # The ammonia hub (E1c): the contracted NH3 delivery (t/d; the load is in
    # MWh_NH3 at 5.166 MWh/t), not an electric load.
    "nh3_delivery_tpd": ("NH3 delivery", "t/d", "loads", "Load", "nh3_delivery", "p_set"),
}


#: The growth kinds the site's electric peak reads (``site_peak.PEAK_INPUTS``;
#: a test pins the two equal). The H2 offtake and the NH3 delivery are not: they are loads
#: on the H2 and NH3 buses.
_PEAK_KINDS = ("it_mw", "energized_mw", "load_scale")


def _growth_value_text(value) -> str:
    """A growth value in the ledger's words: a number, or ``load ×scale`` per load."""
    if isinstance(value, dict):
        return ", ".join(f"{k} ×{v:g}" for k, v in value.items())
    return f"{value:g}"


def _growth_rows(kind: str, staged, *, source_set: str = "example", overrides=None):
    """``growth_inputs`` rows for one kind: ``staged`` is ``[(year, the
    example's same-stage value, set)]``; ``overrides`` ({year: value}) are
    entered values (set, ``source_set``)."""
    rows = []
    for y, v, is_set in staged:
        if overrides is not None and y in overrides:
            rows.append({"kind": kind, "year": y, "value": float(overrides[y]), "set": True,
                         "example_value": v, "source": source_set})
        else:
            rows.append({"kind": kind, "year": y, "value": v, "set": bool(is_set),
                         "example_value": v, "source": "example" if is_set else "unset"})
    return rows


def _in_words(label: str) -> str:
    """``Energized import`` → ``energized import``; an acronym stays (``IT load``)."""
    return label if label[1:2].isupper() or label[1:2].isdigit() else label[:1].lower() + label[1:]


def _placeholder_ledger(growth: list[dict], native: Planning) -> list[dict]:
    """One assumed ledger row per placeholder growth value (the flow lists it
    as a warning to confirm; gatekeeper E1 condition 3). It says which stage
    the value comes from, and that it stays a placeholder until the user edits
    the series it names (``services.growth_placeholders``)."""
    out = []
    years: dict[str, list[int]] = {}
    for g in growth:
        years.setdefault(g["kind"], []).append(g["year"])
    for g in growth:
        if g["set"]:
            continue
        label, unit, section, component, entity, field = _GROWTH_KINDS[g["kind"]]
        y = g["year"]
        stage = years[g["kind"]].index(y) + 1
        value = (_growth_value_text(g["example_value"]) if isinstance(g["example_value"], dict)
                 else f"{g['example_value']:g} {unit}")
        if y in native.years:
            why = (f"the example's {y} value is not used: this planning puts {y} in stage "
                   f"{stage}, so it takes {value}")
        else:
            why = f"the example has no {_in_words(label)} for {y}; it takes its stage {stage} value, {value}"
        how = (f"editing every load's {y} series sets it" if g["kind"] == "load_scale"
               else f"editing the {_in_words(label)} series for {y} sets it")
        out.append({
            "item": f"placeholder {g['kind']} {y}",
            "label": f"{label} {y} (placeholder)",
            "value": (_growth_value_text(g["value"]) if isinstance(g["value"], dict)
                      else g["value"]), "unit": unit,
            "source": "assumed",
            "note": (f"not set: {why} (the example is built for {_years_text(native.years)}). A "
                     "placeholder, never interpolated: "
                     + ("the peak check and the scorecard read this period unknown until "
                        "it is set" if g["kind"] in _PEAK_KINDS else
                        "the investment case flags it until it is set")
                     + f"; {how}"),
            "section": section, "panel": None, "component": component, "entity": entity,
            "field": field})
    return out


def _dc_staged_meta(plan: _DcPlan, load_spec=None) -> dict:
    """The staged campus's ``eh_template.json`` for ``plan``. The example's own
    years give the shipped metadata; other years add ``growth_inputs`` (each
    growth value set or a placeholder), a placeholder ledger row per unset
    value and the entered planning period."""
    p = plan.planning
    native = NATIVE_PLANNING["eh_datacenter_staged"]
    is_native = p == native
    years = list(p.years)
    y0, end = years[0], p.end_year
    e0 = plan.energization[y0]
    it_mw = {y: it for y, it, _pue in plan.phases}
    spec_it = ({ph.cod.year: ph.it_mw for ph in load_spec.phases}
               if load_spec is not None else None)
    shown_it = spec_it if spec_it is not None else it_mw
    until = f"{e0:g} MW until {years[1]}" if len(years) > 1 else f"{e0:g} MW"
    # Each candidate battery's inverter lives 10 years; list those that end
    # inside the horizon.
    ends = [f"bess_new_{y}'s in {y + 10}" for y in years if y + 10 < end]
    if ends:
        ends[0] = ends[0].replace("'s in", "'s inverter ends in", 1)
        inverter_note = (f"{' and '.join(ends)}, inside the {end} horizon; the template's finance "
                         "inputs re-buy it at the end of its lifetime (replacement_rule "
                         "part_lifetimes)")
    else:
        inverter_note = (f"no candidate battery's inverter ends inside the {end} horizon "
                         "(replacement_rule part_lifetimes re-buys one that does)")

    def _projected_note(y):
        if spec_it is not None:
            return "the dc_load phase entered at create"
        if plan.it_set[y]:
            return "synthetic illustrative fit-out ramp; replace with the client's load projection"
        return f"placeholder: the example's same-stage IT load; not set for {y}"

    if is_native:
        stage_note = ("Load is picked up in 2.5 MW blocks after an islanding (assumed): the island "
                      "rows then follow the load, so the plan stages — a small genset in 2027 and "
                      "2030, the bulk in 2035.")
        cap_note = (f"The energization schedule caps the import at {e0:g} MW in "
                    f"{y0}, below that period's peak: the plan builds on site for it.")
    else:
        stage_note = ("Load is picked up in 2.5 MW blocks after an islanding (assumed): the island "
                      "rows then follow the load, so the plan stages.")
        cap_note = ("The energization schedule caps the import per period ("
                    + ", ".join(f"{plan.energization[y]:g} MW in {y}" for y in years)
                    + "): where it is below a period's peak the plan builds on site for it.")
    meta = {
        "id": "eh_datacenter_staged",
        "name": "Data Center Energy Hub (staged growth)",
        "description": (
            "The data center campus planned from a projected load: IT grows "
            f"{' → '.join(f'{shown_it[y]:g}' for y in years)} MW over {_years_text(years)} while "
            f"the utility energizes a {_DC_POC_MW:g} MW connection in steps "
            f"({until}). "
            "Genset and battery candidates per period, so the plan says what "
            "to build when. Multi-period; tagged for an Energy Hub study."),
        "recommended_archetype": "weak_flexible",
        "pack_overrides": {"import_p_nom_mw": _DC_POC_MW},
        "stages": None,
        "dtc_attribution": "per_load",
        "projected_load": [
            {"year": y, "it_mw": shown_it[y], "pue": pue, "note": _projected_note(y)}
            for y, _it, pue in plan.phases],
        "energization": [
            {"year": y, "available_import_mw": plan.energization[y], "connection_mw": _DC_POC_MW,
             "source": "assumed",
             "note": ("utility energization schedule (assumed)" if plan.energized_set[y] else
                      f"placeholder: the example's same-stage energization; not set for {y}")}
            for y in years],
        "period_years": {str(y): n for y, n in p.period_years.items()},
        "ledger": [
            {"item": "discount rate", "value": "7 % nominal (the solver settings' discount_rate)",
             "source": "assumed",
             "note": "the solve discounts each period's costs at it (auto_discount_periods)"},
            {"item": "PUE", "value": "1.3 design figure",
             "source": "assumed",
             "note": "metadata: cooling is the single-year template's profile scaled with the IT "
                     f"load, so the modelled peak-to-IT ratio is about 1.46 in {years[-1]}"},
            {"item": "battery inverter replacement", "value": "after 10 years (inv_power_lifetime)",
             "source": "assumed",
             "note": inverter_note},
            {"item": "PoC and transformer losses", "value": "efficiency 1.0 on grid_import and "
                                                            "site_transformer",
             "source": "assumed",
             "note": "losses are not modelled in the staged plan (the single-year template keeps "
                     "0.995 and 0.99), so the investment case's counterfactual bill (the served "
                     "load at the meter) is established; about 1.5 % of the import is left out"},
            {"item": "import tariff", "value": f"{DC_TARIFF['id']}: network energy charge "
                                               "35 EUR/MWh 08-20 h, 20 EUR/MWh otherwise",
             "source": "synthetic",
             "note": "an illustrative network-use charge on the metered import, on top of the "
                     "wholesale price the grid supply carries; no demand charge (the template "
                     "models one July week, so a monthly item would be billed for one month); "
                     "replace with the site's tariff; synthetic, illustrative; no money year of "
                     "its own; read in the case's money (2025)"},
            {"item": "tariff clock", "value": "UTC",
             "source": "assumed",
             "note": "the representative week's hours are the site clock; stating them as UTC "
                     "reads each tariff window on the snapshot hour as is"},
            {"item": "existing campus", "value": "gensets 1-4, rooftop_pv, ups_battery, "
                                                 f"site_transformer: COD {y0}-01-01, overnight cost 0",
             "source": "assumed",
             "note": "in service at the first period and sunk: no capex in the case; only the "
                     "candidates the plan builds are investments, each a stage at its period"},
            {"item": "money year", "value": "pack prices (2025 EUR) are read as real base-year EUR",
               "source": "assumed",
               "note": "held flat to the base year, then escalated from it at the case's rates; "
                       "nothing is converted with a price index"
                       + ("" if is_native else
                          "; entering other planning years does not re-date the pack's money "
                          "year (per-period cost sourcing is a separate follow-up)")},
            {"item": "finance inputs", "value": "nominal EUR, escalation "
                                                "2 %/yr, inflation 2 %, all equity at 7 %, "
                                                f"close {y0 - 1}-01-01, {p.analysis_years} years "
                                                f"to {end}",
             "source": "assumed",
             "note": "a later stage's capex is paid one year before its COD; pre-tax (no tax "
                     "pack); the terminal value is the remaining-life annuity of each part"},
            {"item": "depreciation of the staged assets",
             "value": "straight-line AfA (not degressive)",
             "source": "assumed",
             "note": "a deliberate deviation: a dated degressive election on a later stage is refused by "
                     "the investment case (P5), so the staged assets are planned on straight-line AfA"},
        ],
        "study_notes": [
            "Each period is the same representative July week, with the period's load and import cap.",
            "Candidates are one row per period (build_year = the period): the plan says what to build when.",
            cap_note,
            stage_note,
            "After a solve the investment case runs on the shipped commercial config and finance "
            f"inputs: the existing campus is in service in {y0} at no capex, each candidate the plan "
            "builds is a stage with its own COD, and an unbuilt later candidate is not an investment.",
            "PoC and transformer losses are not modelled in the staged plan (efficiency 1.0), so the "
            "investment case can compare against the grid-only site.",
            f"The {_DC_POC_MW:g} MW connection carries the {years[-1]} peak. The investment case "
            "compares against the site as it stands today: with the plan's new builds removed, "
            f"the site still meets its load (the {e0:g} MW energized in {y0} "
            "included), and the island capability the new builds buy is shown as a break-even "
            "beside the NPV.",
        ],
        "provenance": PROVENANCE,
    }
    if is_native:
        return meta
    growth = [*_growth_rows("it_mw", [(y, it_mw[y], plan.it_set[y]) for y in years],
                            source_set="dc_load", overrides=spec_it),
              *_growth_rows("energized_mw",
                            [(y, plan.energization[y], plan.energized_set[y]) for y in years])]
    meta["growth_inputs"] = growth
    meta["planning"] = {"start_year": y0, "end_year": end, "periods": years,
                        "native_periods": list(native.years), "native_end_year": native.end_year}
    tariff_year = int(DC_TARIFF["valid_from"][:4])
    held = [] if y0 >= tariff_year else [
        {"item": "import tariff before its year",
         "value": f"{DC_TARIFF['id']}'s rates from {y0}-01-01", "source": "assumed",
         "note": (f"assumed, held from {tariff_year}: the synthetic tariff is dated "
                  f"{DC_TARIFF['valid_from']}, so its rates are held from the start year; "
                  "replace with the site's tariff for those years")}]
    meta["ledger"] = [
        {"item": "planning period",
         "value": f"{' / '.join(map(str, years))} → {end} (entered)", "source": "entered",
         "note": (f"the example is built for {_years_text(native.years)} → {native.end_year}; "
                  "each period's years run to the next, the last to the end year, and the solve "
                  "discounts them (auto_discount_periods)")},
        *meta["ledger"], *held, *_placeholder_ledger(growth, native)]
    return meta


def _mg_single_meta(planning: Planning | None = None) -> dict:
    """The single-year microgrid's metadata for ``planning`` (None: the
    example's week in 2030 and its case to 2045)."""
    first, end = _mg_years(planning)
    return {
        "id": "eh_microgrid",
        "name": "Island Microgrid",
        "description": (
            "Island system that must run without its 20 MW subsea tie: PV, "
            "wind, battery, three diesel units, a critical hospital feeder "
            "and residential demand. Tagged for an off-grid study."),
        "recommended_archetype": "off_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "study_notes": [
            "The off_grid pack islands the subsea tie; the island must be "
            "adequate on its own resources.",
            "The subsea tie is normally open (p_max_pu = 0), so the FMEA "
            "worksheet sweeps the same islanded system; set it to 1 to "
            "study tie-connected operation.",
            "Class-C stress scenarios (dunkelflaute, heatwave) are "
            "preloaded for the FMEA sweep.",
            "After a solve the investment case compares the plan with the island as it stands "
            "today (the existing diesels, PV, wind and battery, no grid), re-solved without the "
            "candidates. The representative week stands for each of the "
            f"{end - first} operating years ({first}-{end - 1}).",
        ],
        "ledger": [
            *([] if planning is None else [
                {"item": "planning period",
                 "value": f"{first} → {end} (entered)", "source": "entered",
                 "note": f"the example is built for {MG_FIRST_YEAR} → {MG_HORIZON_END}; the "
                         "week is dated in the start year and the case runs to the end year"}]),
            {"item": "investment case",
             "value": "compared with the island as it stands today: the existing diesels, PV, "
                      "wind and battery, without the candidates, and no grid",
             "source": "assumed",
             "note": "the staged microgrid's basis (owner decision M2e a); the investment-case "
                     "route re-solves the island without the candidates"},
            {"item": "point of connection", "value": "subsea_tie, normally open (p_max_pu 0), "
                                                     "no tariff, clock UTC",
             "source": "assumed",
             "note": "as the staged microgrid: it seeds the site/grid split and carries nothing"},
            {"item": "single-year horizon",
             "value": f"one representative week weighted 8,760/168 h, repeated for each of the "
                      f"{end - first} operating years "
                      f"{first}-{end - 1}, escalated and degraded",
             "source": "assumed",
             "note": f"the week is dated {first}, the first operating year; the case "
                     f"ends in {end}, "
                     + ("the staged templates' horizon end" if planning is None
                        else "the entered end year")
                     + ". The week "
                     "already weighs a year, so nothing is annualised"},
            {"item": "dates", "value": f"every COD {first}-01-01, financial close "
                                       f"{first - 1}-01-01",
             "source": "assumed",
             "note": "a single period has no stage, so every asset shares one COD; close one year "
                     "before it and capex paid then, as the staged templates"},
            {"item": "existing island", "value": ", ".join(MG_EXISTING)
                                                 + ": overnight cost 0, lifetime unlimited",
             "source": "assumed",
             "note": "in service and sunk: no capex in the case; only the candidates the plan "
                     "builds are investments. The solve is unchanged"},
            {"item": "finance inputs",
             "value": f"the staged microgrid's: pack prices (2025 EUR) taken as real "
                      f"{first} EUR (the base year), escalation 2 %/yr from "
                      f"{first}, inflation 2 %, all equity at 7 %, pre-tax; PV degrades "
                      "0.5 %/yr, the rest not",
             "source": "assumed",
             "note": f"the week's running costs and the pack's capex are held flat to "
                     f"{first} (the first operating year) and escalate from there, the "
                     "staged microgrid's rule from its first period (2027); the terminal value "
                     "is the remaining-life annuity of each part"},
            {"item": "battery inverter replacement", "value": "after 10 years (inv_power_lifetime)",
             "source": "assumed",
             "note": (f"bess_new's inverter is re-bought in {first + 9} (replacement_rule "
                      "part_lifetimes); the cash then changes sign twice, so the IRR is flagged "
                      "irr_multiple_sign_changes. The NPV is unaffected") if end > first + 9
                     else f"the case ends in {end}, before bess_new's inverter is re-bought"},
            {"item": "island sidecar",
             "value": "seamless: the staged microgrid's blocks on the plain unit names (diesel "
                      "droop, grid-forming batteries, PV and wind Q(U)); the tie is open, so "
                      "there is no import to lose",
             "source": "assumed",
             "note": "eh_staged_hubs._mg_island_single_year (UX review N2, decision F); the "
                     "island checks and the report's islanding chapter read it"},
            {"item": "island unit data",
             "value": "the staged microgrid's: diesel droop 4 %, H 1.5 s; batteries droop 4 %, "
                      "tau_f 0.1 s, i_max 1.2 pu, k_gfm_min 1.0, pf 0.95; PV and wind Q(U), "
                      "pf 0.95; sustained 24 h, bridge 600 s",
             "source": "assumed",
             "note": "demo values copied from the staged microgrid, not from a datasheet or a "
                     "grid code"},
            {"item": "transition RoCoF limit",
             "value": "2 Hz/s over 500 ms (the steady island keeps 1 Hz/s)",
             "source": "assumed",
             "note": "the staged microgrid's demo value (eh_staged_hubs.MG_TRANSITION_ROCOF), "
                     "relaxed from the data center's 1 Hz/s so a tuned grid-forming battery can "
                     "meet it; not taken from a grid code"},
            {"item": "island rows", "value": "off (island_constraints false)",
             "source": "assumed",
             "note": "the plan is not sized for the island requirements; the island checks "
                     "report what islanding would need. Turn them on to plan for it (owner: "
                     "island data on, rows off, the user opts in)"},
            {"item": "island break-even", "value": "not applicable",
             "source": "assumed",
             "note": "the island rows are off, so the comparison is not relaxed for them and the "
                     "case does not price an island capability"},
            {"item": "diesel running cost", "value": "260 EUR/MWh (fuel and variable O&M)",
             "source": "synthetic",
             "note": f"the template's marginal cost, in {first} money, escalating "
                     "2 %/yr (the case books it as opex); replace with the island's fuel "
                     "contract"},
        ],
        "provenance": PROVENANCE,
    }


TEMPLATE_META: dict[str, dict] = {
    "eh_datacenter": {
        "id": "eh_datacenter",
        "name": "Data Center Energy Hub",
        "description": (
            "50 MW hyperscale campus behind a weak 40 MW grid connection: "
            "critical IT with UPS battery, gas genset fleet, rooftop PV, "
            "genset/battery expansion candidates. Tagged and ready for an "
            "Energy Hub study."),
        "recommended_archetype": "weak_flexible",
        "pack_overrides": {"import_p_nom_mw": 40.0},
        "stages": None,
        "dtc_attribution": "per_load",
        "study_notes": [
            "IT load is critical (it_bus); cooling and offices are not.",
            "The grid import carries its own outage data, so the MC can "
            "credit it as a firm unit (decision 6).",
            "Try the energy import budget (Pack settings → Import energy) "
            "to see a capped-import plan.",
        ],
        "provenance": PROVENANCE,
    },
    # Built from the plan below (E1): the example's own years.
    "eh_datacenter_staged": None,
    "eh_h2_hub": {
        "id": "eh_h2_hub",
        "name": "Industrial Hydrogen Hub",
        "description": (
            "Grid-connected industrial site: 120 MW wind, 80 MW PV, 60 MW "
            "electrolyser, H₂ storage, an extendable fuel cell and a site "
            "battery, 40 MW critical process load and 25 MW H₂ offtake. "
            "Tagged for an Energy Hub study."),
        "recommended_archetype": "strong_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "study_notes": [
            "Electrolyser and fuel cell carry conversion roles and outage "
            "data: fmea_top ranks them, redundancy can add a spare.",
            "The frontier runs by default for strong_grid.",
            "Island rows are off: the plan is not sized to island; the island checks "
            "report what islanding would need.",
        ],
        "ledger": [
            *H2_ISLAND_LEDGER,
            {"item": "island rows", "value": "off (island_constraints false)",
             "source": "assumed",
             "note": "the plan is not sized to island; turn them on to plan for islanding. "
                     "Measured on this template (live solves, 2026-10-15): the solve's "
                     "objective goes from 7.0 M to 8.8 M EUR at 1 h sustained (26 MW of new "
                     "battery), 11.4 M at 4 h (42 MW) and 28.9 M at 24 h (170 MW), and the grid "
                     "import falls from about 93 MW to 2-10 MW (owner: island data on, rows "
                     "off, the user opts in)"},
        ],
        "provenance": PROVENANCE,
    },
    "eh_microgrid": None,                   # _mg_single_meta, below
}
TEMPLATE_META["eh_datacenter_staged"] = _dc_staged_meta(_dc_plan())
TEMPLATE_META["eh_microgrid"] = _mg_single_meta()
#: The data center's and H2 hub's own metadata, before the grid-readiness rows
#: are added below (a planned copy gets them again from ``template_meta``).
_SINGLE_YEAR_META = {tid: dict(TEMPLATE_META[tid]) for tid in ("eh_datacenter", "eh_h2_hub")}


def _single_year_meta(template_id: str, planning: Planning) -> dict:
    """The data center's or H2 hub's metadata for entered years (E1d): the
    shipped metadata and a ledger row for the dated week. One period and no
    investment case, so the end year is recorded only."""
    import copy

    meta = copy.deepcopy(_SINGLE_YEAR_META[template_id])
    meta["ledger"] = [
        {"item": "planning period",
         "value": f"{planning.start_year} → {planning.end_year} (entered)", "source": "entered",
         "note": f"the representative week is dated {_week(planning)[0].date()} (the "
                 f"example's {SNAPSHOTS[0].date()}); a single period with no investment case, "
                 "so the end year is recorded only"},
        *meta.get("ledger", [])]
    return meta

# Preloaded Class-C registry per template (the stress.py schema).
STRESS_SCENARIOS: dict[str, list[dict]] = {
    "eh_datacenter_staged": [
        {"id": "heatwave", "name": "Heatwave (cooling + PV derate)",
         "kind": "parametric", "frequency_per_year": 0.3,
         "electrical_load_multiplier": 1.12,
         "renewable_availability_multiplier": 0.85},
        {"id": "dunkelflaute", "name": "Dark, still week",
         "kind": "parametric", "frequency_per_year": 0.5,
         "electrical_load_multiplier": 1.0,
         "renewable_availability_multiplier": 0.1},
    ],
    "eh_datacenter": [
        {"id": "heatwave", "name": "Heatwave (cooling + PV derate)",
         "kind": "parametric", "frequency_per_year": 0.3,
         "electrical_load_multiplier": 1.12,
         "renewable_availability_multiplier": 0.85},
        {"id": "dunkelflaute", "name": "Dark, still week",
         "kind": "parametric", "frequency_per_year": 0.5,
         "electrical_load_multiplier": 1.0,
         "renewable_availability_multiplier": 0.1},
    ],
    "eh_h2_hub": [
        {"id": "low_wind_week", "name": "Low-wind week",
         "kind": "parametric", "frequency_per_year": 1.0,
         "electrical_load_multiplier": 1.0,
         "renewable_availability_multiplier": 0.35},
    ],
    "eh_microgrid": [
        {"id": "dunkelflaute", "name": "Dunkelflaute",
         "kind": "parametric", "frequency_per_year": 0.4,
         "electrical_load_multiplier": 1.05,
         "renewable_availability_multiplier": 0.1},
        {"id": "heatwave", "name": "Heatwave (A/C peak)",
         "kind": "parametric", "frequency_per_year": 0.2,
         "electrical_load_multiplier": 1.25,
         "renewable_availability_multiplier": 0.9},
    ],
}

# Solver settings a project from these templates starts with: frontier and
# fmea_top need VOLL > 0 (a template otherwise starts from the default 0).
SOLVER_CONFIG: dict = {"voll": 5000.0}
# Per-template additions. The island rows (campus island I2a) are opt-in per
# project and ship enabled on the templates that carry an island sidecar
# (owner Q5).
SOLVER_CONFIG_EXTRA: dict[str, dict] = {
    "eh_datacenter": {"island_constraints": True},
    # The staged campus is multi-period; the solve discounts the periods
    # (auto_discount_periods) at the config's discount rate.
    "eh_datacenter_staged": {"island_constraints": True, "multi_investment_periods": True,
                             "auto_discount_periods": True, "discount_rate": 0.07},
}


def _staged_commercial_and_finance(planning: Planning | None = None) -> dict:
    """The staged template's `commercial` and `finance` blocks for
    ``planning`` (None: the example's years), stored as the routes store them
    (validated, dumped): the value flows are the `single_owner` template
    built on the template's network."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    plan = _dc_plan(planning)
    base = CommercialConfig.model_validate(
        {"poc_link": "grid_import", "import_tariff": _dc_tariff(plan), "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_datacenter_staged(planning=planning), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(_dc_finance(plan)).model_dump(mode="json")}


def _microgrid_commercial_and_finance(planning: Planning | None = None) -> dict:
    """The single-year island's commercial config: the open subsea tie is the
    PoC (it carries nothing), no tariff, the single-owner value flows on the
    plain network. The case compares against the island as it stands today,
    which the investment-case route re-solves without the candidates."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate({"poc_link": "subsea_tie", "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_microgrid(planning=planning), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(_mg_finance(planning))
                                    .model_dump(mode="json")}


#: Template blocks that need the backend's models (built on first use).
_SOLVER_CONFIG_BUILT = {"eh_datacenter_staged": _staged_commercial_and_finance,
                        "eh_microgrid": _microgrid_commercial_and_finance}
#: Built blocks by template id, or by (id, Planning) for entered years.
_BUILT_CACHE: dict[str | tuple, dict] = {}


def solver_config(template_id: str, planning: Planning | None = None) -> dict:
    """The solver settings written beside one template, for ``planning`` on a
    staged example (None or the example's own years: the shipped settings)."""
    import copy

    built = {}
    key = template_id if _planned(template_id, planning) is None else (template_id, planning)
    if template_id in _SOLVER_CONFIG_BUILT:
        if key not in _BUILT_CACHE:
            _BUILT_CACHE[key] = (_SOLVER_CONFIG_BUILT[template_id]() if key == template_id
                                 else _SOLVER_CONFIG_BUILT[template_id](planning))
        built = copy.deepcopy(_BUILT_CACHE[key])
    return {**SOLVER_CONFIG, **SOLVER_CONFIG_EXTRA.get(template_id, {}), **built}


def _planned(template_id: str, planning: Planning | None) -> Planning | None:
    """``planning`` when it differs from the template's own years, else None
    (the shipped template)."""
    if template_id not in NATIVE_PLANNING or _is_native(template_id, planning):
        return None
    return planning

# The island sidecar (plan 2026-10-07 campus island, I1): control modes, unit
# dynamics and island requirements, keyed by PyPSA name. Its schema is
# gridspine's (gridspine/schema/island.py), which this module does not
# import (it loads without gridspine on the path), so the content is a
# plain mapping and tests/test_island_config.py validates it through
# gridspine. Every number is {value, source}; all are "assumed" until the
# client's datasheets arrive. Only templates listed here ship the file.
def _a(value):
    return {"value": value, "source": "assumed"}


def _genset_island(**extra):
    return {"kind": "genset", "governor": "droop", "droop_pct": _a(4.0), "q_droop_pct": _a(4.0),
            "H_s": _a(1.5), "T_g_s": _a(0.5), "ramp_pu_per_s": _a(0.2), "start_s": _a(10.0),
            "sync_s": _a(5.0), "p_min_pu": _a(0.3), "load_step_max_pct": _a(50.0),
            "cos_phi_r": _a(0.8), "xd_sat": _a(2.0), "excitation": "avr_pmg",
            "neutral_earthing": "solid", **extra}


_LIMITS = {"rocof_hz_per_s": _a(1.0), "f_min_hz": _a(47.5), "f_max_hz": _a(51.5), "qss_band_hz": _a(1.0)}

ISLAND_CONFIG: dict[str, dict] = {
    "eh_datacenter": {
        "units": {
            **{f"genset_{i}": _genset_island() for i in range(1, 5)},
            "genset_new": _genset_island(unit_mw=_a(10.0)),
            "rooftop_pv": {"kind": "pv", "control": "gfl_qu", "pf_rated": _a(0.95),
                           "qu_points": _a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])},
            "bess_new": {"kind": "bess", "control": "gfm_droop", "pf_rated": _a(0.95),
                         "droop_pct": _a(4.0), "q_droop_pct": _a(4.0), "tau_f_s": _a(0.1),
                         "i_max_pu": _a(1.2), "k_gfm_min": _a(1.0)},
            "ups_battery": {"kind": "ups", "it_load": ["it_load"], "walk_in_s": _a(15.0),
                            "f_window_hz": _a(2.0)},
            # Plan Q1: the critical load is the IT and the cooling it needs.
            "it_load": {"kind": "load", "critical": True},
            "cooling": {"kind": "load", "critical": True},
            "offices": {"kind": "load", "critical": False},
        },
        "requirements": {
            "bridge_s": _a(600.0), "sustained_h": _a(24.0), "fuel_autonomy_h": _a(24.0),
            # ups_bridged only, in 5 MW blocks (owner, 2026-10-13, campus island
            # I2b): 15 MW blocks need ~210 MW of H 1.5 s gensets to hold 1 Hz/s,
            # and seamless at 30 MW of BESS cannot form the island at 0⁺. The
            # 4 × 10 MW fleet plus genset_new holds a 5 MW block; seamless stays
            # a sidecar option, and I2c compares the two.
            "genset_redundancy_n": _a(1), "scenarios": ["ups_bridged"],
            "pickup_blocks": _a([5.0] * 11),
            "frequency_limits": {"transition": dict(_LIMITS), "steady_island": dict(_LIMITS)},
            "rocof_window_ms": _a(500.0), "gfm_margin": _a(0.1), "ride_through_storage": ["bess_new"],
        },
    },
}

def _h2_hub_island(bess_new, wind_new, solar_new) -> dict:
    """The H2 hub's island sidecar: no genset and no UPS, so its batteries
    form the island (``seamless``) and carry the critical process load. The
    battery block is the data center's ``bess_new`` block and the fuel cell
    gets gridspine's drafted default (``producers/campus.py``
    ``DEFAULT_ISLAND``), so draft and sidecar agree; all demo values. The
    island rows ship off (owner, 2026-10-16): the plan is not sized to
    island, and the island checks report what islanding would need."""
    import copy

    bess = copy.deepcopy(ISLAND_CONFIG["eh_datacenter"]["units"]["bess_new"])
    qu = _a([[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]])

    def gfl(kind):
        return {"kind": kind, "control": "gfl_qu", "pf_rated": _a(0.95), "qu_points": copy.deepcopy(qu)}

    units = {"site_battery": copy.deepcopy(bess), "fuel_cell": gfl("fuel_cell"),
             "process_load": {"kind": "load", "critical": True}}
    for name, kind in (("wind_farm", "wind"), *((w, "wind") for w in wind_new),
                       ("solar_park", "pv"), *((s, "pv") for s in solar_new)):
        units[name] = gfl(kind)
    for b in bess_new:
        units[b] = copy.deepcopy(bess)
    return {"units": units, "requirements": {
        "bridge_s": _a(600.0), "sustained_h": _a(1.0), "scenarios": ["seamless"],
        "frequency_limits": {"transition": dict(_LIMITS), "steady_island": dict(_LIMITS)},
        "rocof_window_ms": _a(500.0), "gfm_margin": _a(0.1),
        "ride_through_storage": ["site_battery", *bess_new]}}


ISLAND_CONFIG["eh_h2_hub"] = _h2_hub_island(["bess_new"], ["wind_new"], ["solar_new"])


def _staged_island(cfg: dict, years=None) -> dict:
    """The single-year island sidecar with the expansion candidates renamed
    per period (``genset_new_<y>``, ``bess_new_<y>``; ``years`` None: the
    example's)."""
    import copy

    years = list(years) if years is not None else [y for y, _it, _pue in DC_PHASES]
    out = copy.deepcopy(cfg)
    units = out["units"]
    gen, bess = units.pop("genset_new"), units.pop("bess_new")
    for y in years:
        units[f"genset_new_{y}"] = copy.deepcopy(gen)
        units[f"bess_new_{y}"] = copy.deepcopy(bess)
    out["requirements"]["ride_through_storage"] = [f"bess_new_{y}" for y in years]
    # The halls are fitted out in smaller steps: 2.5 MW pickup blocks (the
    # single-year campus's 5 MW blocks need 36.7 MW more genset inertia in
    # every period, whatever its load, so the plan could not stage).
    out["requirements"]["pickup_blocks"] = {"value": [2.5] * 22, "source": "assumed"}
    return out


ISLAND_CONFIG["eh_datacenter_staged"] = _staged_island(ISLAND_CONFIG["eh_datacenter"])

#: Staged examples built for entered years: id → (meta, island) for a Planning
#: (the staged hubs add theirs when they register).
_PLANNED_META = {"eh_datacenter_staged": lambda p, spec: _dc_staged_meta(_dc_plan(p), spec),
                 "eh_datacenter": lambda p, _spec: _single_year_meta("eh_datacenter", p),
                 "eh_h2_hub": lambda p, _spec: _single_year_meta("eh_h2_hub", p),
                 "eh_microgrid": lambda p, _spec: _mg_single_meta(p)}
_PLANNED_ISLAND = {"eh_datacenter_staged":
                   lambda p: _staged_island(ISLAND_CONFIG["eh_datacenter"], p.years)}


def _register_staged_hubs() -> None:
    """The staged H2 hub and microgrid (``eh_staged_hubs.py``, generic-site
    E2E H1/M1) add themselves to the registries above. Loaded by file path
    like this module; it gets a view of this module's names, never imports
    it."""
    import importlib.util
    import pathlib
    import types

    path = pathlib.Path(__file__).resolve().with_name("eh_staged_hubs.py")
    if not path.is_file():           # this module copied on its own (a test's tmp dir)
        return
    spec = importlib.util.spec_from_file_location("_eh_staged_hubs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register(types.SimpleNamespace(**globals()))


_register_staged_hubs()


def _register_ammonia() -> None:
    """The ammonia hub (``eh_ammonia.py``, grid-to-process ticket 05) adds itself to the registries,
    after the staged hubs it starts from."""
    import importlib.util
    import pathlib
    import types

    path = pathlib.Path(__file__).resolve().with_name("eh_ammonia.py")
    if not path.is_file():           # this module copied on its own (a test's tmp dir)
        return
    spec = importlib.util.spec_from_file_location("_eh_ammonia", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register(types.SimpleNamespace(**globals()))


_register_ammonia()

#: Templates that ship price series (`price_series/`, minted at create; IPP ticket 02): id →
#: a writer of the directory, called by `write_sidecars`.
PRICE_SERIES: dict = {}
#: Templates that ship pre-built sensitivity cases (IPP ticket 04): id → a builder of the
#: `cases.json` document (declared changes, data only until the sensitivities slice, S5).
CASES: dict = {}
CASES_FILE = "cases.json"


def _register_ipp() -> None:
    """The renewables IPP (``eh_ipp.py``, IPP renewables + BESS ticket 03)."""
    import importlib.util
    import pathlib
    import types

    path = pathlib.Path(__file__).resolve().with_name("eh_ipp.py")
    if not path.is_file():           # this module copied on its own (a test's tmp dir)
        return
    spec = importlib.util.spec_from_file_location("_eh_ipp", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register(types.SimpleNamespace(**globals()))


_register_ipp()

# ── Grid-readiness markings (DC grid readiness 02, UX review) ───────────────
#
# The connection limits the grid-readiness scorecard checks against that no
# grid code here states: the connection ramp limit, the oscillation limit,
# the PoC Link's efficiency, the PoC fault level and the reliability goal.
# They are ledger rows like every other template assumption, so the project
# flow lists them "to check"; each names the flow section and field it
# belongs to (``section``, ``panel``, ``component``, ``entity``, ``field``,
# the flow's target shape). The scorecard reads its limits from these rows
# and marks a row ``assumed`` while its source is not "code". All values are
# placeholders until the network operator's connection offer arrives.

#: A connection ramp limit of 10 % of the connection capacity per minute.
RAMP_SHARE_PER_MIN = 0.10
#: Peak-to-peak oscillation limit, % of the connection, in the band of concern.
OSCILLATION_LIMIT_PCT = 1.0
OSCILLATION_BAND_HZ = (0.1, 2.0)

#: tid → (import Link, connection MW, PoC Link efficiency, PoC bus, PoC Sk MVA
#: or None, LOLE goal h/yr or None). Pinned against the built networks by
#: tests/test_grid_readiness.py.
_GRID_READINESS_SITE = {
    "eh_datacenter": ("grid_import", 40.0, 0.995, "grid", 250.0, 3.0),
    "eh_datacenter_staged": ("grid_import", _DC_POC_MW, 1.0, "grid", 250.0, 3.0),
    "eh_h2_hub": ("grid_import", 200.0, 0.995, "grid", 5000.0, None),
    "eh_h2_hub_staged": ("grid_import", 200.0, 1.0, "grid", 5000.0, None),
    "eh_microgrid": ("subsea_tie", 20.0, 0.97, "mainland", None, 3.0),
    "eh_microgrid_staged": ("subsea_tie", 20.0, 0.97, "mainland", None, 3.0),
}


def _gr_row(item, label, value, unit, note, section, component=None, entity=None, field=None,
            panel=None) -> dict:
    return {"item": item, "label": label, "value": value, "unit": unit, "source": "assumed",
            "note": note,
            "section": section, "panel": panel, "component": component, "entity": entity,
            "field": field or item}


def grid_readiness_ledger(template_id: str) -> list[dict]:
    """The template's grid-readiness ledger rows."""
    link, mw, eff, bus, sk, lole = _GRID_READINESS_SITE[template_id]
    rows = [
        _gr_row("connection_ramp", "Connection ramp limit", round(RAMP_SHARE_PER_MIN * mw, 6), "MW/min",
                f"assumed: {RAMP_SHARE_PER_MIN:.0%} of the {mw:g} MW connection per minute; RfG/DCC "
                "set no ramp limit for a demand facility, so this is a placeholder of the order a "
                "network operator writes into a connection offer for large loads", "connection",
                "Link", link),
        _gr_row("oscillation", "Power oscillation limit", OSCILLATION_LIMIT_PCT, "% of connection, peak-to-peak",
                f"assumed: peak-to-peak power oscillation at most {OSCILLATION_LIMIT_PCT:g} % of the "
                f"connection in the {OSCILLATION_BAND_HZ[0]:g}-{OSCILLATION_BAND_HZ[1]:g} Hz band "
                "(forced-oscillation concern for large loads, NERC 2025 large-loads white paper); "
                "no code value", "connection", "Link", link),
        _gr_row("import_efficiency", "Import efficiency", eff, "p.u.",
                f"assumed: the {link} Link's efficiency (losses = 1 - efficiency); what reaches the "
                "site side of the PoC Link. Losses further in (transformers, UPS) are not in it",
                "connection", "Link", link, "efficiency"),
    ]
    if sk is not None:
        rows.append(_gr_row("fault_level", "Fault level at the PoC", sk, "MVA",
                            f"assumed: short-circuit power at the PoC ({bus}); the SCR and "
                            "rapid-voltage-change screenings read it", "connection", "Bus", bus,
                            "eh_sk_mva"))
    if lole is not None:
        rows.append(_gr_row("lole_goal", "Reliability goal (LOLE)", lole, "h/yr",
                            f"assumed: the recommended pack's target_lole_h ({lole:g} h/yr); the "
                            "reliability study certifies the site against it", "scenarios",
                            None, None, "target_lole_h", panel="simparams"))
    return rows


for _tid in _GRID_READINESS_SITE.keys() & TEMPLATE_META.keys():   # staged hubs: when registered
    TEMPLATE_META[_tid]["ledger"] = [*(TEMPLATE_META[_tid].get("ledger") or []),
                                     *grid_readiness_ledger(_tid)]

# Sidecar file names copied by POST /api/projects/from_template/<id>.
META_FILE = "eh_template.json"
STRESS_FILE = "adequacy_stress_scenarios.json"
SOLVER_FILE = "solver_config.json"
SIDECAR_FILES = (META_FILE, STRESS_FILE, SOLVER_FILE)
# Written only for the templates in ISLAND_CONFIG.
ISLAND_FILE = "island_config.json"
# services.adequacy.stress.SCHEMA — not imported, so this module loads
# without the backend's packages at import time. A test pins the two equal.
SCHEMA = 1


def template_meta(template_id: str, planning: Planning | None = None, *, load_spec=None) -> dict:
    """The template's ``eh_template.json`` content for ``planning`` (None or
    the example's own years: the shipped metadata)."""
    import copy

    p = _planned(template_id, planning)
    if p is None:
        return copy.deepcopy(TEMPLATE_META[template_id])
    meta = _PLANNED_META[template_id](p, load_spec)
    if template_id in _GRID_READINESS_SITE:
        meta["ledger"] = [*meta["ledger"], *grid_readiness_ledger(template_id)]
    return meta


def island_config(template_id: str, planning: Planning | None = None) -> dict | None:
    """The template's island sidecar for ``planning``, or None when it has none."""
    p = _planned(template_id, planning)
    if p is not None and template_id in _PLANNED_ISLAND:
        return _PLANNED_ISLAND[template_id](p)
    return ISLAND_CONFIG.get(template_id)


def write_sidecars(out_dir, template_id: str, planning: Planning | None = None, *,
                   load_spec=None) -> None:
    """Write the template's metadata and stress registry beside network.nc,
    and its island sidecar when it has one, for ``planning`` on a staged
    example (None: the example's own years)."""
    import json
    import pathlib

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / META_FILE).write_text(json.dumps(
        template_meta(template_id, planning, load_spec=load_spec), indent=2,
        sort_keys=True) + "\n")
    (out_dir / STRESS_FILE).write_text(json.dumps(
        {"__schema__": SCHEMA, "scenarios": STRESS_SCENARIOS[template_id]},
        indent=2, sort_keys=True) + "\n")
    (out_dir / SOLVER_FILE).write_text(
        json.dumps(solver_config(template_id, planning), indent=2, sort_keys=True) + "\n")
    if template_id in PRICE_SERIES:
        PRICE_SERIES[template_id](out_dir, _planned(template_id, planning))
    if template_id in CASES:
        (out_dir / CASES_FILE).write_text(
            json.dumps(CASES[template_id](), indent=1, ensure_ascii=False) + "\n")
    island = island_config(template_id, planning)
    if island is not None:
        (out_dir / ISLAND_FILE).write_text(json.dumps(island, indent=2, sort_keys=True) + "\n")
