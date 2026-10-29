"""
The mining concentrator, staged (grid-to-process ticket 10, design §3.B): ``eh_concentrator_staged``.

A copper concentrator behind a weak, staged connection, on the hourly ``year`` time base (D4, ticket 09):
each period is one chronological year of 8,760 hours, so the concentrate shed carries product across its
21-day shipping cycle. Template data only: the network is built here and staged by
``eh_site.stage_network(time_base="year")``.

* the mine is an ore Generator on ``ore_rom`` with a fixed schedule as its upper bound (ore the plant
  cannot take is not mined; the shortfall shows at the vessel);
* the crusher, the coarse-ore stockpile, 2 (then 3) mill lines and flotation are process Links with
  bus0 = electricity, so each efficiency is t/MWh = 1000 / kWh/t and bus2 draws the feed;
* the concentrate shed (``delivery_interval_h`` 504) feeds one vessel every 21 days; the concentrate
  has no buy-in, so ``__lost_concentrate`` (role ``shortfall``) is the only slack;
* the connection is 25 MW ``non_firm_dynamic`` in 2027 (its envelope, 15 MW in 300 h, is the PoC's
  ``p_max_pu`` over that period), then 25 MW firm in 2030 and 35 MW firm in 2035 (energization);
* PV and BESS candidates per period; a PoC fault level of 150 MVA.

The commercial and finance blocks follow the generic site's industrial preset (``eh_site``). Every number
has its ledger row (``_ledger``), labelled ``demo`` or ``sourced``. ``register(T)`` adds the template to
``eh_templates``' registries; it is built for the example's years only.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import numpy as np
import pandas as pd

TID = "eh_concentrator_staged"
NAME = "Mining Concentrator (staged growth)"
YEARS = (2027, 2030, 2035)
HORIZON_END = 2045
DISCOUNT_RATE = 0.07
#: The chronological year every period holds: 8,760 h without a leap day (``eh_site.year_snapshots``).
YEAR_START = "2030-01-01"
HOURS = 8760

# ── The numbers, each with its ledger row below ────────────────────────────
#: Ore feed (t/h) per period: the mine's schedule.
FEED_TPH = {2027: 800.0, 2030: 1000.0, 2035: 1200.0}
#: Specific energies (kWh per t of ore).
CRUSH_KWH_T = 0.8
FLOT_KWH_T = 2.0
#: Bond's third theory (Bond, 1952): W = 10·Wi·(1/√P80 − 1/√F80), sizes in µm.
BOND_WI = 14.0
BOND_F80_UM = 2000.0
BOND_P80_UM = 150.0
SAG_KWH_T = 8.0


def bond_kwh_t(wi: float = BOND_WI, f80: float = BOND_F80_UM, p80: float = BOND_P80_UM) -> float:
    """The ball-mill duty by Bond's equation (kWh/t)."""
    return 10.0 * wi * (1.0 / np.sqrt(p80) - 1.0 / np.sqrt(f80))


#: Grinding (SAG + ball) per t of ore, rounded as the design does: ≈ 8.3 + 8 → 16 kWh/t.
GRIND_KWH_T = 16.0
SERVICES_MW = 3.0
MILL_TPH = 550.0
#: Mill lines and the year each enters service.
MILLS = {"mill_1": YEARS[0], "mill_2": YEARS[0], "mill_3": 2035}
CRUSHER_HEADROOM = 1.4
STOCKPILE_H = 24.0
MILL_P_MIN_PU = 0.6
MILL_MIN_UP_H = 12
MILL_MIN_DOWN_H = 4
FLOT_P_MIN_PU = 0.5
#: Flotation lines (t/h of slurry) and the year each enters service: one for the first two mill lines,
#: one with the third.
FLOTATION = {"flotation": (2 * MILL_TPH, YEARS[0]), "flotation_2035": (MILL_TPH, 2035)}
HEAD_GRADE = 0.006
RECOVERY = 0.88
CONC_GRADE = 0.28
#: Mass pull to concentrate: head × recovery / concentrate grade (≈ 1.89 %).
MASS_PULL = HEAD_GRADE * RECOVERY / CONC_GRADE
SHIP_CYCLE_H = 504
SHIP_LOAD_H = 24
#: Vessels a year: one every 21 days from the cycle's last day (hours 480–503 of each cycle).
VESSELS = HOURS // SHIP_CYCLE_H
SHED_T = 40_000.0
LOST_EUR_PER_T = 1300.0
#: The connection per period (MW): import cap, and the 2027 non-firm envelope's curtailed level.
CONNECTION_MW = {2027: 25.0, 2030: 25.0, 2035: 35.0}
NON_FIRM_YEAR = 2027
ENVELOPE_LOW_MW = 15.0
#: The envelope's low hours (300 h): two 150-h winter events, from these hours of the year.
ENVELOPE_EVENTS = ((14 * 24, 150), (320 * 24, 150))
FAULT_LEVEL_MVA = 150.0
SITE_KV = 33.0
#: Candidate bounds (MW) and the BESS duration.
PV_MAX_MW = 40.0
BESS_MAX_MW = 20.0
BESS_HOURS = 4.0
#: The stockpile extension: a candidate in every period (design: the pile is extendable), priced per t live.
STOCKPILE_EUR_PER_T = 25.0
STOCKPILE_LIFETIME = 25.0
STOCKPILE_MAX_T = 60_000.0

#: HiGHS interior point: on the three-period year the default (dual simplex) did not finish in 25 min, IPM (with
#: crossover) did in about 20 (ledgered under "solver").
SOLVER_OPTIONS = {"solver": "ipm"}

#: The sunk flowsheet, in service from its build year.
EXISTING = ("grid_import", "mine", "crusher", *MILLS, *FLOTATION, "coarse_ore_stockpile",
            "concentrate_shed")


def _site():
    """``eh_site`` (``stage_network``, the industrial preset), loaded by file path."""
    key = "_eh_site_for_concentrator"
    mod = sys.modules.get(key)
    if mod is None:
        path = pathlib.Path(__file__).resolve().with_name("eh_site.py")
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


def year_index() -> pd.DatetimeIndex:
    return pd.date_range(YEAR_START, periods=HOURS, freq="h")


def conc_tph(year: int) -> float:
    """Nameplate concentrate (t/h) at the period's feed."""
    return FEED_TPH[year] * MASS_PULL


def vessel_t(year: int) -> float:
    """One vessel (t): the year's nameplate concentrate over its vessels."""
    return conc_tph(year) * HOURS / VESSELS


def shipments_profile() -> np.ndarray:
    """1 in the loading hours of each vessel, else 0 (per unit of the load-out rate)."""
    ship = np.zeros(HOURS)
    for k in range(VESSELS):
        end = (k + 1) * SHIP_CYCLE_H
        ship[end - SHIP_LOAD_H:end] = 1.0
    return ship


def envelope_mw() -> np.ndarray:
    """The 2027 non-firm envelope (MW): the import cap, and its low level in the events."""
    mw = np.full(HOURS, CONNECTION_MW[NON_FIRM_YEAR])
    for start, hours in ENVELOPE_EVENTS:
        mw[start:start + hours] = ENVELOPE_LOW_MW
    return mw


def _profiles() -> dict[str, np.ndarray]:
    """Synthetic hourly year profiles (demo): summer-high PV and a wholesale price with a daily and a
    winter peak."""
    idx = year_index()
    hod = idx.hour.to_numpy()
    doy = idx.dayofyear.to_numpy()
    season = 0.75 + 0.25 * np.cos(2 * np.pi * (doy - 172) / 365)
    cloud = 0.75 + 0.25 * np.cos(2 * np.pi * doy / 7.3)
    pv = np.round(np.clip(np.sin(np.pi * (hod - 6) / 12), 0, None) * season * cloud, 6)
    price = (70.0 + 40.0 * np.exp(-((hod - 19) ** 2) / 8.0)
             + 15.0 * np.cos(2 * np.pi * (doy - 15) / 365))
    return {"pv": pv, "price": price}


def mill_p_nom_mw() -> float:
    return MILL_TPH * GRIND_KWH_T / 1000.0


def crusher_p_nom_mw() -> float:
    return CRUSHER_HEADROOM * max(FEED_TPH.values()) * CRUSH_KWH_T / 1000.0


def _set_columns(n) -> None:
    """The process columns with their defaults on every row (``models.process_unit``)."""
    n.buses["mwh_per_t"] = float("nan")
    n.buses["product_value"] = float("nan")
    n.generators["process_role"] = ""
    n.links["eh_process"] = False
    n.links["criticality_tier"] = ""
    n.links["throughput_unit"] = ""
    n.links["specific_energy_kwh_per_t"] = float("nan")
    n.stores["delivery_interval_h"] = 0.0


def _process(n, name, kwh_t, tier, **attrs) -> None:
    """A process Link drawing electricity on bus0: t/MWh = 1000 / kWh/t on bus1 and drawn on bus2."""
    eff = 1000.0 / kwh_t
    n.add("Link", name, bus0="site", efficiency2=-eff, **{"efficiency": eff, **attrs})
    n.links.at[name, "eh_process"] = True
    n.links.at[name, "criticality_tier"] = tier
    n.links.at[name, "throughput_unit"] = "t/h"
    n.links.at[name, "specific_energy_kwh_per_t"] = kwh_t


def build_eh_concentrator_staged(T, *, years=YEARS, time_base: str = "year") -> pypsa.Network:  # noqa: F821
    """The template over ``years`` (the example's by default; a test plans a subset). ``time_base="week"``
    builds the same network on one representative week (the first of July), which the validator refuses:
    the shed's 21-day cycle does not fit in it (design §2.4)."""
    S = _site()
    years = list(years)
    last = YEARS[-1]
    if time_base == "year":
        cut, idx = slice(None), year_index()
    else:
        start = (pd.Timestamp(S.SNAPSHOTS[0]) - pd.Timestamp(YEAR_START)) // pd.Timedelta(hours=1)
        cut, idx = slice(start, start + len(S.SNAPSHOTS)), S.SNAPSHOTS
    h = len(idx)
    prof = {k: v[cut] for k, v in _profiles().items()}

    n = T._base(NAME, snapshots=idx)
    for c, co2 in (("AC", 0.0), ("grid", 0.35), ("solar", 0.0), ("battery", 0.0), ("ore", 0.0),
                   ("slurry", 0.0), ("concentrate", 0.0), ("crushing", 0.0), ("grinding", 0.0),
                   ("flotation", 0.0), ("concentrate shortfall", 0.0)):
        n.add("Carrier", c, co2_emissions=co2)
    n.add("Bus", "grid", v_nom=110.0, carrier="AC", x=0.0, y=0.0)
    n.add("Bus", "site", v_nom=SITE_KV, carrier="AC", x=0.0, y=0.0)
    for bus, carrier in (("ore_rom", "ore"), ("ore_fine", "ore"), ("slurry", "slurry"),
                         ("concentrate", "concentrate")):
        n.add("Bus", bus, carrier=carrier, unit="t", x=0.0, y=0.0)
    T._tag_columns(n)
    _set_columns(n)

    # Wholesale supply beyond the PoC; the PoC is loss-free and capped per period by energization.
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=10.0 * max(CONNECTION_MW.values()),
          marginal_cost=0.0)
    n.generators_t.marginal_cost = pd.DataFrame({"grid_supply": prof["price"]}, index=idx)
    n.add("Link", "grid_import", bus0="grid", bus1="site", p_nom=max(CONNECTION_MW.values()),
          efficiency=1.0, carrier="AC")
    n.links.at["grid_import", "eh_role"] = "grid_import"
    T._outage(n, "links", "grid_import", 0.002, 12.0)
    n.buses.at["grid", "eh_poc"] = True
    n.buses.at["grid", "eh_sk_mva"] = FAULT_LEVEL_MVA
    n.buses.at["site", "eh_sk_mva"] = 0.9 * FAULT_LEVEL_MVA
    n.buses.at["grid", "eh_ibr_mva"] = PV_MAX_MW + BESS_MAX_MW
    n.buses.at["site", "eh_ibr_mva"] = PV_MAX_MW + BESS_MAX_MW

    # The mine: the period's feed is the upper bound of a fixed schedule (scaled per period below).
    n.add("Generator", "mine", bus="ore_rom", carrier="ore", p_nom=FEED_TPH[last], marginal_cost=0.0)
    n.generators_t.p_max_pu["mine"] = np.ones(h)

    _process(n, "crusher", CRUSH_KWH_T, "process_flexible", bus1="ore_fine", bus2="ore_rom",
             carrier="crushing", p_nom=crusher_p_nom_mw())
    for mill, build_year in MILLS.items():
        _process(n, mill, GRIND_KWH_T, "process_critical", bus1="slurry", bus2="ore_fine",
                 carrier="grinding", p_nom=mill_p_nom_mw(), build_year=build_year,
                 committable=True, p_min_pu=MILL_P_MIN_PU, min_up_time=MILL_MIN_UP_H,
                 min_down_time=MILL_MIN_DOWN_H, start_up_cost=0.0, shut_down_cost=0.0,
                 up_time_before=0, down_time_before=MILL_MIN_DOWN_H)
    for flot, (tph, build_year) in FLOTATION.items():
        _process(n, flot, FLOT_KWH_T, "process_critical", bus1="concentrate", bus2="slurry",
                 carrier="flotation", p_nom=tph * FLOT_KWH_T / 1000.0, build_year=build_year,
                 committable=True, p_min_pu=FLOT_P_MIN_PU, start_up_cost=0.0, shut_down_cost=0.0,
                 up_time_before=0, down_time_before=0)
        # bus1 is concentrate: the mass pull on the slurry drawn.
        n.links.at[flot, "efficiency"] = MASS_PULL * 1000.0 / FLOT_KWH_T

    n.add("Store", "coarse_ore_stockpile", bus="ore_fine", carrier="ore",
          e_nom=STOCKPILE_H * FEED_TPH[years[0]])
    n.add("Store", "concentrate_shed", bus="concentrate", carrier="concentrate", e_nom=SHED_T)
    n.stores.at["concentrate_shed", "delivery_interval_h"] = float(SHIP_CYCLE_H)

    # The shipments at their last-period size (each period scales them), and the shortfall slack.
    peak = vessel_t(last) / SHIP_LOAD_H
    ship = shipments_profile()[cut]
    n.add("Load", "shipments", bus="concentrate", carrier="concentrate")
    n.add("Load", "site_services", bus="site", carrier="AC", p_set=SERVICES_MW)
    n.loads_t.p_set = pd.DataFrame({"shipments": peak * ship}, index=idx)
    n.add("Generator", "__lost_concentrate", bus="concentrate", carrier="concentrate shortfall",
          p_nom=peak, marginal_cost=LOST_EUR_PER_T)
    n.generators.at["__lost_concentrate", "process_role"] = "shortfall"
    n.generators_t.p_max_pu["__lost_concentrate"] = ship
    n.buses.at["concentrate", "product_value"] = LOST_EUR_PER_T

    def candidates(net, y):
        v = f"pv_new_{y}"
        net.add("Generator", v, bus="site", carrier="solar", p_nom=0.0, p_nom_extendable=True,
                p_nom_max=PV_MAX_MW, marginal_cost=0.0, build_year=y)
        T._price(net, "generators", v, "solar-utility")
        net.generators_t.p_max_pu[v] = np.tile(prof["pv"], len(years))
        b = f"bess_new_{y}"
        net.add("StorageUnit", b, bus="site", carrier="battery", p_nom=0.0, p_nom_extendable=True,
                p_nom_max=BESS_MAX_MW, max_hours=BESS_HOURS, efficiency_store=0.95,
                efficiency_dispatch=0.95, build_year=y)
        T._price(net, "storage_units", b, "battery")
        s = f"coarse_ore_stockpile_new_{y}"
        net.add("Store", s, bus="ore_fine", carrier="ore", e_nom=0.0, e_nom_extendable=True,
                e_nom_max=STOCKPILE_MAX_T, build_year=y, lifetime=STOCKPILE_LIFETIME)
        # Overnight, annuitised by PyPSA at the discount rate (as the pack's PV rows).
        net.stores.loc[s, ["overnight_cost", "discount_rate"]] = [STOCKPILE_EUR_PER_T, DISCOUNT_RATE]

    S.stage_network(n, years, load_scale={y: {"shipments": vessel_t(y) / vessel_t(last)} for y in years},
                    energization={"grid_import": [CONNECTION_MW[y] for y in years]},
                    weights=S.period_years(years, HORIZON_END), existing=EXISTING,
                    candidates=candidates, time_base=time_base)
    snaps = n.snapshots
    period = snaps.get_level_values(0)
    # Per-period schedules: the mine's feed, and the shortfall slack's bound (the vessel of the period).
    for y in years:
        at = period == y
        n.generators_t.p_max_pu.loc[at, "mine"] = FEED_TPH[y] / FEED_TPH[last]
        n.generators_t.p_max_pu.loc[at, "__lost_concentrate"] = ship * vessel_t(y) / vessel_t(last)
    # The 2027 non-firm envelope on the PoC, p.u. of its p_nom (composes with energization: min of the two).
    pu = np.ones(len(snaps))
    pu[period == NON_FIRM_YEAR] = envelope_mw()[cut] / n.links.at["grid_import", "p_nom"]
    n.links_t.p_max_pu["grid_import"] = pu
    # The crusher's capacity follows the period's feed (1.4 × feed).
    n.links_t.p_max_pu["crusher"] = np.concatenate(
        [np.full(h, FEED_TPH[y] / FEED_TPH[last]) for y in years])

    # Existing stores: sunk, in service from the first period.
    for s in ("coarse_ore_stockpile", "concentrate_shed"):
        n.stores.loc[s, ["overnight_cost", "discount_rate", "lifetime"]] = [0.0, DISCOUNT_RATE, float("inf")]
    n.links.loc[[*MILLS, *FLOTATION, "crusher"], ["overnight_cost", "discount_rate"]] = [0.0, DISCOUNT_RATE]

    # Columns made on an empty frame are object dtype until cast (netCDF cannot write them).
    n.links["eh_process"] = n.links["eh_process"].eq(True)
    for c in ("eh_poc", "eh_critical"):
        n.buses[c] = n.buses[c].eq(True)
    for c in ("eh_role", "criticality_tier", "throughput_unit"):
        n.links[c] = n.links[c].fillna("")
    n.generators["process_role"] = n.generators["process_role"].fillna("")
    for frame, cols in ((n.buses, ("mwh_per_t", "product_value", "eh_sk_mva", "eh_ibr_mva")),
                        (n.links, ("specific_energy_kwh_per_t",)),
                        (n.stores, ("delivery_interval_h",))):
        for c in cols:
            frame[c] = pd.to_numeric(frame[c], errors="coerce").astype(float)
    n.name = NAME
    return n


def _finance(T) -> dict:
    """The generic site's industrial finance (``eh_site.finance_inputs``), on this template's assets."""
    S = _site()
    fin = S.finance_inputs(S.site_params({"site_type": "industrial"}))
    first = YEARS[0]
    owned = [a for a in EXISTING if a != "grid_import"]
    cand = [f"{k}_new_{y}" for y in YEARS for k in ("pv", "bess")]
    fin["cod_by_asset"] = {a: f"{max(first, MILLS.get(a, first), FLOTATION.get(a, (0, first))[1])}-01-01"
                           for a in owned}
    fin["degradation_by_asset"] = {**{a: 0.0 for a in owned},
                                   **{a: (0.005 if a.startswith("pv_new") else 0.0) for a in cand}}
    fin["analysis_years"] = HORIZON_END - first
    return fin


def _commercial_and_finance(T) -> dict:
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    S = _site()
    base = CommercialConfig.model_validate(
        {"poc_link": "grid_import", "import_tariff": S.SITE_TARIFF, "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_concentrator_staged(T), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(_finance(T)).model_dump(mode="json")}


def _ledger() -> list[dict]:
    feed = " / ".join(f"{FEED_TPH[y]:g}" for y in YEARS)
    vessels = " / ".join(f"{vessel_t(y):,.0f}" for y in YEARS)
    return [
        {"item": "time base", "value": f"hourly chronological year ({HOURS} h from {YEAR_START}) per period",
         "source": "demo",
         "note": "stage_network(time_base='year'): every weighting 1, so the shed carries "
                 "concentrate across the 21-day cycle; the representative-week build is refused "
                 "(inventory_cadence_needs_year). The same synthetic year repeats in each period"},
        {"item": "ore feed (mine)", "value": f"{feed} t/h ({' / '.join(map(str, YEARS))})", "source": "demo",
         "note": "about 19–29 kt/d, a mid-size copper concentrator. The mine is an ore Generator whose fixed "
                 "schedule is a ceiling: ore the plant cannot take is not mined (the process view's "
                 "unused_supply on ore_rom, per period), and the lost concentrate shows at the next vessel"},
        {"item": "grinding specific energy (Bond)",
         "value": f"W = 10·Wi·(1/√P80 − 1/√F80) = {bond_kwh_t():.2f} kWh/t ball-mill duty "
                  f"(Wi {BOND_WI:g} kWh/t, F80 {BOND_F80_UM / 1000:g} mm, P80 {BOND_P80_UM:g} µm)",
         "source": "sourced",
         "note": "the method is sourced (Bond's third theory of comminution, Bond 1952); Wi, F80 and P80 "
                 "are demo"},
        {"item": "SAG specific energy", "value": f"{SAG_KWH_T:g} kWh/t", "source": "demo", "note": ""},
        {"item": "grinding per mill line", "value": f"{GRIND_KWH_T:g} kWh/t (SAG + ball, rounded)",
         "source": "demo", "note": "one Link per line: efficiency 62.5 t/MWh"},
        {"item": "crushing", "value": f"{CRUSH_KWH_T:g} kWh/t", "source": "demo", "note": ""},
        {"item": "flotation", "value": f"{FLOT_KWH_T:g} kWh/t of slurry", "source": "demo",
         "note": "one line sized for the first two mill lines (1,100 t/h), one with the third (550 t/h, "
                 "2035); no slurry inventory, so flotation follows the mills (D7)"},
        {"item": "site services", "value": f"{SERVICES_MW:g} MW fixed", "source": "demo",
         "note": "pumps, water, offices. The life-safety share has no tier: criticality tiers are on "
                 "process Links, not Loads"},
        {"item": "mill lines", "value": f"2 × {MILL_TPH:g} t/h; a third from 2035", "source": "demo",
         "note": "10–38 % headroom over feed, so a stop can be caught up; "
                 f"{mill_p_nom_mw():.1f} MW each"},
        {"item": "crusher capacity", "value": f"{CRUSHER_HEADROOM:g} × the period's feed", "source": "demo",
         "note": "it can run in cheap or sunny hours only when the mine schedule varies; with this flat "
                 "schedule it follows the mine"},
        {"item": "coarse-ore stockpile", "value": f"{STOCKPILE_H:g} h live at the 2027 feed "
                                                  f"({STOCKPILE_H * FEED_TPH[YEARS[0]]:,.0f} t)",
         "source": "demo",
         "note": f"an extension is a candidate in every period ({STOCKPILE_EUR_PER_T:g} EUR/t live, "
                 f"{STOCKPILE_LIFETIME:g} years, up to {STOCKPILE_MAX_T:,.0f} t)"},
        {"item": "mill p_min_pu, min up / min down", "value": f"{MILL_P_MIN_PU:g}, {MILL_MIN_UP_H} h / "
                                                              f"{MILL_MIN_DOWN_H} h",
         "source": "demo",
         "note": "restart after a stop: re-establishing the charge and the circuit; its cost is the lost "
                 "tonnes, so start_up_cost and shut_down_cost are 0"},
        {"item": "flotation p_min_pu", "value": f"{FLOT_P_MIN_PU:g}", "source": "demo",
         "note": "follows the mills"},
        {"item": "mass pull to concentrate",
         "value": f"{MASS_PULL:.4%} (head {HEAD_GRADE:.1%} Cu × recovery {RECOVERY:g} / "
                  f"concentrate grade {CONC_GRADE:.0%})",
         "source": "demo", "note": "the formula is standard metallurgical accounting; the numbers are demo"},
        {"item": "shipments",
         "value": f"one vessel every 21 days ({VESSELS} a year, loaded out over {SHIP_LOAD_H} h): "
                  f"{vessels} t ({' / '.join(map(str, YEARS))})",
         "source": "demo",
         "note": "the design's 25 kt per vessel is about 2.6 times the concentrate 1,000 t/h of ore makes "
                 "in 21 days (9.5 kt), so each vessel is the year's nameplate concentrate over its "
                 "vessels; the shed's delivery_interval_h is 504"},
        {"item": "concentrate shed", "value": f"{SHED_T:,.0f} t", "source": "demo",
         "note": "fixed: the design marks it extendable, but it holds more than three of the largest "
                 "vessels, so an extension is never built"},
        {"item": "lost-production value (__lost_concentrate)", "value": f"{LOST_EUR_PER_T:g} EUR/t concentrate "
                                                                        f"(≈ {LOST_EUR_PER_T * MASS_PULL:.0f} "
                                                                        "EUR/t ore)",
         "source": "demo",
         "note": "NSR minus variable cost, the client's own number. The concentrate has no buy-in, so "
                 "this is the only slack"},
        {"item": "grid connection",
         "value": "2027: 25 MW non_firm_dynamic, its envelope 15 MW in 300 h and 25 MW otherwise; "
                  "2030: 25 MW firm; 2035: 35 MW firm",
         "source": "demo",
         "note": "the envelope is in the LP as grid_import's p_max_pu over 2027 (two 150-h winter events, "
                 "from 15 January and 17 November), the stages as its energization. The project's "
                 "connection agreement is one per project, so the commercial block keeps the industrial "
                 "preset's"},
        {"item": "PoC fault level", "value": f"{FAULT_LEVEL_MVA:g} MVA (SCR about 6 against 25 MW)",
         "source": "demo", "note": "weak, so the motor-start check bites"},
        {"item": "PV, BESS candidates", "value": "pack prices (solar-utility, battery)", "source": "sourced",
         "note": f"defaults pack {T_PACK}; per period, PV up to {PV_MAX_MW:g} MW, BESS up to "
                 f"{BESS_MAX_MW:g} MW × {BESS_HOURS:g} h"},
        {"item": "PV and price profiles", "value": "synthetic hourly year", "source": "demo",
         "note": "summer-high PV with a weekly cloud cycle; wholesale price 55–125 EUR/MWh with an "
                 "evening and a winter peak"},
        {"item": "tariff, finance", "value": "as the generic site's industrial preset", "source": "demo",
         "note": "eh_site.py (SITE_TARIFF, finance_inputs)"},
        {"item": "unit commitment in planning",
         "value": "plan solved as LP; unit commitment checked in 4 sampled weeks per period",
         "source": "demo",
         "note": "linearised UC on 26,280 snapshots did not finish within the 30-min budget on the year base, "
                 "so the plan dispatches the mills and flotation without their minimum load and min "
                 "up / down; the binary operational check then re-solves one week per season of each "
                 "period with them committable, and reports the gap. The 12 weeks solve side by side in "
                 "worker processes, one per core less the plan's (at least 1)"},
        {"item": "solver", "value": "HiGHS interior point (solver_options solver=ipm)",
         "source": "sourced",
         "note": "own measurement: 3-period year plain LP, 595,688 cols × 1,322,776 rows, 4 cores: dual simplex "
                 "hit its 1,500 s limit unsolved; IPM with crossover optimal in 1,212 s at 2.6 GB "
                 "(shared machine); IPM without crossover, alone, optimal in 1,137 s at 2.3 GB"},
    ]


T_PACK = "2026-10-26"


def register(T) -> None:
    """Add the concentrator to ``eh_templates``' registries."""
    global T_PACK
    T_PACK = T.TEMPLATE_PACK_VERSION
    T.BUILDERS[TID] = lambda load_spec=None, planning=None: build_eh_concentrator_staged(T)
    T.STRESS_SCENARIOS[TID] = []
    T.SOLVER_CONFIG_EXTRA[TID] = {"multi_investment_periods": True, "auto_discount_periods": True,
                                  "discount_rate": DISCOUNT_RATE, "solver_options": dict(SOLVER_OPTIONS)}
    T._SOLVER_CONFIG_BUILT[TID] = lambda planning=None: _commercial_and_finance(T)
    T.TEMPLATE_META[TID] = _meta(T)


def _meta(T) -> dict:
    """The concentrator's ``eh_template.json``."""
    S_py = {y: e - y for y, e in zip(YEARS, (*YEARS[1:], HORIZON_END))}
    peaks = {y: SERVICES_MW + FEED_TPH[y] * (CRUSH_KWH_T + GRIND_KWH_T + FLOT_KWH_T) / 1000.0 for y in YEARS}
    return {
        "id": TID,
        "name": NAME,
        "description": (
            "A copper concentrator on a weak, staged grid connection: the mine feeds "
            f"{' → '.join(f'{FEED_TPH[y]:g}' for y in YEARS)} t/h of ore over {T._years_text(YEARS)} "
            "through a crusher, a coarse-ore stockpile, 2 (then 3) mill lines and flotation to a "
            "concentrate shed that loads a vessel every 21 days. The 2027 connection is non-firm "
            "(25 MW, curtailed to 15 MW in 300 h), firm from 2030; PV, BESS and stockpile-extension candidates per period. "
            "Planned on the hourly chronological year, so the shed carries across shipments."),
        "recommended_archetype": "weak_flexible",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "projected_load": [{"year": y, "peak_mw": round(peaks[y], 2),
                            "note": "crushing, grinding and flotation at the period's feed, plus site "
                                    "services; demo"} for y in YEARS],
        "energization": [{"year": y, "available_import_mw": CONNECTION_MW[y],
                          "connection_mw": CONNECTION_MW[y], "source": "demo",
                          "note": ("non-firm: 15 MW in 300 h" if y == NON_FIRM_YEAR else "firm")}
                         for y in YEARS],
        "period_years": {str(y): S_py[y] for y in YEARS},
        "time_base": "year",
        "ledger": _ledger(),
        "study_notes": [
            "Each period is one chronological year of hours, so the concentrate shed fills over 21 "
            "days and empties at each vessel.",
            "The mills and flotation are fixed-size units; the plan sizes PV, BESS and the stockpile "
            "extension.",
            "Planning dispatches the process units as a plain LP; the binary operational check after "
            "the solve re-solves one week per season with their minimum load and min up / down.",
            "A shortfall at a vessel is lost concentrate, valued at 1,300 EUR/t.",
        ],
        "provenance": ("synthetic illustrative data — an hourly chronological year per period, "
                       "public-catalogue-range costs; replace with project data before any decision"),
    }
