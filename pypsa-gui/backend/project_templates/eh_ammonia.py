"""
The H2 hub to green ammonia, staged (grid-to-process ticket 05, design §3.A): ``eh_ammonia_staged``.

The staged H2 hub with its fuel cell and H2 offtake taken out and a Haber-Bosch loop added: the
electrolysers' H2 goes to ammonia, an NH3 tank holds it, and the site delivers a contracted tonnage per
period. Template data only: no engine change. It is the first template with a committable process unit,
so ``services.process_template_rules.refusals`` runs on it at build:

* ``haber_bosch`` is a fixed-size, committable process unit (never extendable); its restart is
  ``min_down_time``, so ``start_up_cost`` / ``shut_down_cost`` stay 0 on the weighted network;
* ``__buy_nh3`` (role ``buy_in``) is the grey purchase, ``__lost_nh3`` (role ``shortfall``) the contract's
  shortfall penalty, priced above the purchase; the NH3 bus carries ``product_value`` and ``mwh_per_t``.

The NH3 bus is in MWh_NH3 (LHV, 5.166 MWh/t), upstream PyPSA-Eur's convention. ``register(T)`` adds it to
``eh_templates``' registries (``T`` as in ``eh_staged_hubs``); it is called from there.
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib
import sys

import numpy as np
import pandas as pd

YEARS = (2027, 2030, 2035)
HORIZON_END = 2045
DISCOUNT_RATE = 0.07

#: The defaults-pack version that carries the ammonia rows (``haber-bosch``, ``nh3-tank``, ``h2-tank``).
AMMONIA_PACK_VERSION = "2026-10-22"

# ── The numbers, each with its ledger row below ────────────────────────────
#: NH3 energy content (MWh LHV per t): config.default.yaml:1015 (``MWh_NH3_per_tNH3``).
MWH_PER_T = 5.166
#: H2 per t NH3 (MWh_H2/t): config.default.yaml:1018.
H2_MWH_PER_T = 5.93
#: Electricity per MWh_NH3 (HB + compressors): technology-data v0.14.0 ``Haber-Bosch,electricity-input``
#: = 0.2473 MWh_el/MWh_NH3 (D3). config.default.yaml:1019 carries the same number labelled per t.
EL_MWH_PER_MWH = 0.2473
#: ... which is 1.278 MWh_el per t NH3.
EL_MWH_PER_T = EL_MWH_PER_MWH * MWH_PER_T
#: Delivered NH3 per period (t/d, demo).
DELIVERY_TPD = {2027: 50.0, 2030: 100.0, 2035: 150.0}
HB_TPD = 160.0
HB_P_MIN_PU = 0.4
HB_MIN_UP_H = 24
HB_MIN_DOWN_H = 8
HB_RAMP = 0.2
#: Grey NH3 buy-in (EUR/MWh_NH3): config.default.yaml:982 (``imports.price.NH3``).
BUY_EUR_PER_MWH = 97.0
#: The offtake contract's shortfall penalty (EUR/t, demo); above the buy-in price (validator).
LOST_EUR_PER_T = 800.0
LOST_EUR_PER_MWH = LOST_EUR_PER_T / MWH_PER_T
#: Grey NH3 CO2 intensity (t CO2 per t NH3, demo): in the plan it prices `__buy_nh3` (marginal cost) and the case's
#: reference purchase (GREY_NH3) carries it too; ticket 06 must not count it twice.
GREY_CO2_T_PER_T = 2.0

#: Existing hub assets: the fuel cell and the H2 offtake are gone, the H2 tank is a candidate now, and the
#: Haber-Bosch loop is the client's flowsheet, in service from the first period.
EXISTING = ("wind_farm", "solar_park", "site_battery", "electrolyser", "haber_bosch")
#: Upper bound of each extendable tank (MWh; the validator wants a finite one): about 10 days of the 2035
#: Haber-Bosch H2 draw, and about 8 days of the 2035 delivery. The week cannot use more.
TANK_E_NOM_MAX = {"h2_storage": 10000.0, "nh3_tank": 6000.0}

GREY_NH3 = {"load": "nh3_delivery", "price_eur_per_kg": round(BUY_EUR_PER_MWH * MWH_PER_T / 1000.0, 6),
            "lhv_kwh_per_kg": MWH_PER_T, "co2_t_per_kg": GREY_CO2_T_PER_T / 1000.0,
            "source": "upstream PyPSA-Eur default import price (config.default.yaml:982), not a market "
                      "quote; CO2 intensity synthetic demo value"}


def _hubs():
    """``eh_staged_hubs`` (its ledger rows, finance and CO2 path), loaded by file path."""
    key = "_eh_staged_hubs_for_ammonia"
    mod = sys.modules.get(key)
    if mod is None:
        path = pathlib.Path(__file__).resolve().with_name("eh_staged_hubs.py")
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


def co2_price(year: int) -> float:
    """EUR/t CO2 in the period: the staged hubs' path (80 from 2027, 120 from 2030)."""
    path = _hubs().CO2_PRICE["eur_per_t_by_year"]
    return float(path[max(y for y in path if y <= year)])


def buy_cost_eur_per_mwh(year: int) -> float:
    """The grey purchase per MWh_NH3 in a period: the price plus its CO2."""
    return BUY_EUR_PER_MWH + GREY_CO2_T_PER_T / MWH_PER_T * co2_price(year)


def hb_p_nom_mw() -> float:
    """The Haber-Bosch size in MW of electricity (the link's bus0): 160 t/d of NH3."""
    return HB_TPD / 24.0 * EL_MWH_PER_T


def delivery_mw(year: int) -> float:
    return DELIVERY_TPD[year] / 24.0 * MWH_PER_T


def _set_columns(n) -> None:
    """The process columns with their defaults on every row (``models.process_unit``)."""
    n.buses["mwh_per_t"] = float("nan")
    n.buses["product_value"] = float("nan")
    n.generators["process_role"] = ""
    n.links["eh_process"] = False
    n.links["criticality_tier"] = ""
    n.links["throughput_unit"] = ""
    n.links["specific_energy_kwh_per_t"] = float("nan")


def build_eh_ammonia_staged(T) -> "pypsa.Network":  # noqa: F821
    n = T.BUILDERS["eh_h2_hub_staged"]()
    snaps = n.snapshots
    # The fuel cell and the offtake go; the H2 tank is re-added as a candidate below.
    n.remove("Link", "fuel_cell")
    n.remove("Load", "h2_offtake")
    n.remove("Store", "h2_storage")
    if "h2_offtake" in n.loads_t.p_set.columns:
        n.loads_t.p_set.drop(columns="h2_offtake", inplace=True)
    _set_columns(n)

    for carrier in ("NH3", "Haber-Bosch", "NH3 import", "NH3 shortfall"):
        n.add("Carrier", carrier, co2_emissions=0.0)
    n.add("Bus", "nh3", carrier="NH3", unit="MWh", x=0.0, y=0.0)
    n.buses.at["nh3", "eh_poc"] = False
    n.buses.at["nh3", "eh_critical"] = False
    n.buses.at["nh3", "mwh_per_t"] = MWH_PER_T
    n.buses.at["nh3", "product_value"] = BUY_EUR_PER_MWH

    # The Haber-Bosch loop: bus0 electricity (HB loop and compressors; the ASU is not included, see the ledger), bus1 NH3, bus2 the H2 it draws.
    n.add("Link", "haber_bosch", bus0="hub", bus1="nh3", bus2="h2", carrier="Haber-Bosch",
          p_nom=hb_p_nom_mw(), p_nom_extendable=False,
          efficiency=1.0 / EL_MWH_PER_MWH,
          efficiency2=-(H2_MWH_PER_T / MWH_PER_T) / EL_MWH_PER_MWH,
          committable=True, p_min_pu=HB_P_MIN_PU, min_up_time=HB_MIN_UP_H,
          min_down_time=HB_MIN_DOWN_H, ramp_limit_up=HB_RAMP, ramp_limit_down=HB_RAMP,
          start_up_cost=0.0, shut_down_cost=0.0,
          # The week starts with the loop stopped and free to start: PyPSA's default (on for one hour)
          # would force it to run 23 h at its minimum load, above the 2027 delivery.
          up_time_before=0, down_time_before=HB_MIN_DOWN_H)
    n.links.at["haber_bosch", "eh_process"] = True
    n.links.at["haber_bosch", "criticality_tier"] = "process_critical"
    n.links.at["haber_bosch", "throughput_unit"] = "t/d"
    n.links.at["haber_bosch", "specific_energy_kwh_per_t"] = EL_MWH_PER_T * 1000.0
    for e in [a for a in n.links.index if a.startswith("electrolyser")]:
        n.links.at[e, "criticality_tier"] = "process_flexible"

    # Inventories: extendable, priced from the pack; cyclic within each period (the week repeats).
    pv = AMMONIA_PACK_VERSION
    from services.library.defaults_pack.pricing import price_from_pack

    for name, bus, carrier, tech in (("h2_storage", "h2", "H2", "h2-tank"),
                                     ("nh3_tank", "nh3", "NH3", "nh3-tank")):
        n.add("Store", name, bus=bus, carrier=carrier, e_nom=0.0, e_nom_extendable=True,
              e_nom_max=TANK_E_NOM_MAX[name], build_year=YEARS[0], e_cyclic=True)
        price_from_pack(n, "stores", name, tech, version=pv)
    n.stores["e_cyclic_per_period"] = True

    # Delivery and its two slacks, scaled per period like the other staged loads.
    delivery = np.concatenate([np.full(len(snaps.get_level_values(0)[snaps.get_level_values(0) == y]),
                                       delivery_mw(y)) for y in YEARS])
    n.add("Load", "nh3_delivery", bus="nh3", carrier="NH3", p_set=0.0)
    n.loads_t.p_set["nh3_delivery"] = delivery
    peak = delivery_mw(YEARS[-1])
    for g, role, carrier, cost in (("__buy_nh3", "buy_in", "NH3 import", BUY_EUR_PER_MWH),
                                   ("__lost_nh3", "shortfall", "NH3 shortfall", LOST_EUR_PER_MWH)):
        n.add("Generator", g, bus="nh3", carrier=carrier, p_nom=peak, marginal_cost=cost)
        n.generators.at[g, "process_role"] = role
        n.generators_t.p_max_pu[g] = delivery / peak
    # The purchase carries the CO2 of grey ammonia at the case's CO2 price of the period (design §2.1).
    n.generators_t.marginal_cost["__buy_nh3"] = np.concatenate([
        np.full(int((snaps.get_level_values(0) == y).sum()), buy_cost_eur_per_mwh(y)) for y in YEARS])

    # Columns made on an empty frame are object dtype until cast (netCDF cannot write them).
    n.links["eh_process"] = n.links["eh_process"].astype(bool)
    n.buses[["eh_poc", "eh_critical"]] = n.buses[["eh_poc", "eh_critical"]].astype(bool)
    n.links["eh_role"] = n.links["eh_role"].fillna("")
    for frame, cols in ((n.buses, ("mwh_per_t", "product_value")),
                        (n.links, ("specific_energy_kwh_per_t",))):
        for c in cols:
            frame[c] = pd.to_numeric(frame[c], errors="coerce").astype(float)

    # Existing: the Haber-Bosch loop is sunk and in service in every period.
    n.links.loc["haber_bosch", ["overnight_cost", "discount_rate", "lifetime"]] = \
        [0.0, DISCOUNT_RATE, float("inf")]
    n.name = "Green Ammonia Hub (staged growth)"
    return n


def _commercial_and_finance(T) -> dict:
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    H = _hubs()
    base = CommercialConfig.model_validate(
        {"poc_link": "grid_import", "import_tariff": H.SYNTHETIC_TARIFF, "timezone": "UTC",
         "reference_purchases": [GREY_NH3], "co2_price": H.CO2_PRICE})
    vf = VFT.build("single_owner", build_eh_ammonia_staged(T), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    fin = H._h2_finance()
    fin["cod_by_asset"] = {a: f"{YEARS[0]}-01-01" for a in EXISTING}
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(fin).model_dump(mode="json")}


def _ledger(T) -> list[dict]:
    H = _hubs()
    tpd = " / ".join(f"{DELIVERY_TPD[y]:g}" for y in YEARS)
    return [
        *H._COMMON_LEDGER,
        {"item": "NH3 energy content", "value": f"{MWH_PER_T} MWh/t (LHV)", "source": "sourced",
         "note": "upstream PyPSA-Eur config.default.yaml:1015 (MWh_NH3_per_tNH3); the NH3 bus is in "
                 "MWh_NH3 and shows tonnes through this factor"},
        {"item": "H2 per t NH3", "value": f"{H2_MWH_PER_T} MWh_H2/t "
                                          f"({H2_MWH_PER_T / MWH_PER_T:.4f} MWh_H2 per MWh_NH3)",
         "source": "sourced",
         "note": "config.default.yaml:1018, DECHEMA via technology-data (technology-data v0.14.0 has "
                 "1.1484 MWh_H2/MWh_NH3, 0.04 % higher)"},
        {"item": "Haber-Bosch electricity (D3)",
         "value": f"{EL_MWH_PER_MWH} MWh_el per MWh_NH3 = {EL_MWH_PER_T:.4f} MWh_el per t NH3",
         "source": "sourced",
         "note": "technology-data v0.14.0 costs_2030.csv, Haber-Bosch electricity-input "
                 "(DECHEMA 2017 table 11; unit MWh_el/MWh_NH3). config.default.yaml:1019 carries the "
                 "same number labelled per t, which is wrong by the factor 5.166: per t it is "
                 "1.278 MWh. The description cites 5 GJ/t for compressors; the 0.33 MWh/t for the "
                 "air separation unit is listed under nitrogen-input and not added here (upstream "
                 "does not add it either): adding it would raise the figure by 0.0639 "
                 "MWh_el/MWh_NH3 (26 %)"},
        {"item": "Haber-Bosch capex, FOM, lifetime",
         "value": "1842.99 EUR/kW_NH3, 3 %/year, 30 years", "source": "sourced",
         "note": f"technology-data v0.14.0 costs_2030.csv, pack {AMMONIA_PACK_VERSION} "
                 "(haber-bosch). The loop is a fixed-size, sunk unit in this template, so the "
                 "solve does not charge it; the rows are there for the investment case"},
        {"item": "NH3 tank", "value": "211.83 EUR/MWh_NH3, 2 %/year, 20 years", "source": "sourced",
         "note": f"technology-data v0.14.0 'NH3 (l) storage tank incl. liquefaction', pack "
                 f"{AMMONIA_PACK_VERSION} (nh3-tank); extendable, one row built in {YEARS[0]}"},
        {"item": "H2 tank", "value": "60.05 EUR/kWh_H2, 1.11 %/year, 30 years", "source": "sourced",
         "note": f"technology-data v0.14.0 'hydrogen storage tank type 1 including compressor', pack "
                 f"{AMMONIA_PACK_VERSION} (h2-tank); extendable from 0 (the hub's 2000 MWh tank "
                 f"is replaced), one row built in {YEARS[0]}"},
        {"item": "tank size limits", "value": "H2 tank up to 10,000 MWh, NH3 tank up to 6,000 MWh_NH3 "
                                              "(about 1,160 t)", "source": "assumed",
         "note": "finite upper bounds the solver needs; about 10 days of the 2035 H2 draw and 8 days of "
                 "the 2035 delivery, more than the representative week can use"},
        {"item": "electrolyser", "value": "pack electrolysis (1886 EUR/kW_e, 25 y), efficiency 0.68",
         "source": "assumed",
         "note": "the pack's AEC efficiency is 0.6217; the template keeps today's 0.68 (demo)"},
        {"item": "NH3 delivery", "value": f"{tpd} t/d (2027 / 2030 / 2035)", "source": "synthetic",
         "note": "sized so the 2035 Haber-Bosch needs about 37 MW of H2 (about 55 MW of electrolysis), "
                 "within the 200 MW connection; replace with the offtake contract"},
        {"item": "Haber-Bosch size", "value": f"{HB_TPD:g} t/d fixed ({hb_p_nom_mw():.2f} MW_el), "
                                              "in service from 2027", "source": "synthetic",
         "note": "about 7 % headroom over the 2035 delivery, so a stop can be caught up; fixed-size, "
                 "never extendable (a committable and extendable unit is bilinear)"},
        {"item": "Haber-Bosch minimum load", "value": f"p_min_pu {HB_P_MIN_PU}", "source": "synthetic",
         "note": "flexible-HB literature ranges from about 0.2 to 0.7; the sensitivity sweeps it"},
        {"item": "Haber-Bosch min up / min down", "value": f"{HB_MIN_UP_H} h / {HB_MIN_DOWN_H} h",
         "source": "synthetic",
         "note": "min down is the restart time (a warm restart of the synthesis loop); its cost is the "
                 "lost tonnes the shortfall prices, so start_up_cost and shut_down_cost are 0"},
        {"item": "Haber-Bosch ramp", "value": f"{HB_RAMP} p.u. per hour", "source": "synthetic",
         "note": "demo"},
        {"item": "unit commitment in planning",
         "value": "linearised (process_uc auto), then one binary check per period",
         "source": "assumed",
         "note": "the relaxation understates restarts, so the binary operational check runs after "
                 "the plan and reports its per-period gap. Measured on this plan (electrolysis priced "
                 "per MW of electrical input, GS-2b, 2026-10-21): in 2027 no new electrolyser is "
                 "built and the loop stays off for the whole 168 h week, so the 2027 binary check "
                 "costs 41.2 M EUR against 6.9 M linearised"},
        {"item": "grey NH3 buy-in (__buy_nh3)", "value": f"{BUY_EUR_PER_MWH:g} EUR/MWh_NH3 "
                                                         f"(about {BUY_EUR_PER_MWH * MWH_PER_T:.0f} EUR/t)",
         "source": "sourced",
         "note": "upstream PyPSA-Eur default import price, config.default.yaml:982 "
                 "(imports.price.NH3); a model default, not a market quote. In the plan the purchase "
                 "also pays the CO2 of grey ammonia at the period's CO2 price: 128 EUR/MWh_NH3 in "
                 "2027, 143 from 2030 (still below the lost-production value)"},
        {"item": "lost-production value (__lost_nh3)", "value": f"{LOST_EUR_PER_T:g} EUR/t "
                                                                f"({LOST_EUR_PER_MWH:.2f} EUR/MWh_NH3)",
         "source": "synthetic",
         "note": "the offtake contract's shortfall penalty, above the buy-in price (the validator "
                 "checks it); a ledgered input, never a VoLL default"},
        {"item": "NH3 product value", "value": f"{BUY_EUR_PER_MWH:g} EUR/MWh_NH3", "source": "assumed",
         "note": "what a delivered MWh is worth to the client: the grey purchase it replaces"},
        {"item": "grey NH3 CO2 intensity", "value": f"{GREY_CO2_T_PER_T:g} t CO2 per t NH3",
         "source": "synthetic", "note": "stated by the case, as the grey-H2 intensity is"},
        {"item": "CO2 price, tariff, finance", "value": "as the staged H2 hub", "source": "synthetic",
         "note": "eh_staged_hubs.py (CO2_PRICE, SYNTHETIC_TARIFF, _h2_finance)"},
        {"item": "wind, PV, prices, process load", "value": "as the H2 hub", "source": "synthetic",
         "note": "eh_templates.build_eh_h2_hub"},
        {"item": "dropped from the H2 hub", "value": "fuel cell, H2 offtake", "source": "assumed",
         "note": "the H2 now goes to ammonia; process_load stays a fixed Load"},
        {"item": "storage across periods", "value": "cyclic within each period", "source": "assumed",
         "note": "each period is one representative week; delivery is continuous, so the week is enough"},
        {"item": "investment case",
         "value": "compared with buying grey NH3 for the delivery", "source": "assumed",
         "note": "the case's reference purchase is the grey NH3 price above; ticket 06 confirms the "
                 "path"},
    ]


def register(T) -> None:
    """Add the ammonia hub to ``eh_templates``' registries."""
    tid = "eh_ammonia_staged"
    T.BUILDERS[tid] = lambda: build_eh_ammonia_staged(T)
    T.STRESS_SCENARIOS[tid] = copy.deepcopy(T.STRESS_SCENARIOS["eh_h2_hub"])
    T.SOLVER_CONFIG_EXTRA[tid] = dict(T.SOLVER_CONFIG_EXTRA["eh_h2_hub_staged"])
    T._SOLVER_CONFIG_BUILT[tid] = lambda: _commercial_and_finance(T)
    H = _hubs()
    py = H._period_years_static()
    peaks = {y: 40.0 + delivery_mw(y) * (H2_MWH_PER_T / MWH_PER_T) / 0.68
             + delivery_mw(y) * EL_MWH_PER_T / MWH_PER_T for y in YEARS}
    T.TEMPLATE_META[tid] = {
        "id": tid,
        "name": "Green Ammonia Hub (staged growth)",
        "description": (
            "The hydrogen hub making green ammonia for a contracted delivery of 50 → 100 → 150 t/d "
            "over 2027, 2030 and 2035, beside a 40 MW process load on a 200 MW connection. A "
            "fixed 160 t/d Haber-Bosch loop (minimum load, restart time), H2 and NH3 tanks to size, "
            "wind, solar, battery and electrolyser candidates per period; a shortfall is bought in "
            "as grey ammonia or paid as a contract penalty. Multi-period; ready for an Energy Hub "
            "study on a strong grid."),
        "recommended_archetype": "strong_grid",
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "projected_load": H._stages_meta(peaks, "process load plus the electrolysis and Haber-Bosch "
                                                "the delivery needs; synthetic"),
        "energization": [{"year": y, "available_import_mw": 200.0, "connection_mw": 200.0,
                          "source": "assumed", "note": "fully energized"} for y in YEARS],
        "period_years": {str(y): py[y] for y in YEARS},
        "ledger": _ledger(T),
        "study_notes": [
            "Each period is the same representative week, with the period's NH3 delivery.",
            "The Haber-Bosch loop is a fixed-size unit; the plan sizes the grid, storage, "
            "electrolysers and the two tanks.",
            "Planning uses linearised unit commitment; the binary operational check after the solve "
            "reports what the relaxation understates (restarts and shortfall).",
            "A stop costs the tonnes the NH3 tank cannot cover: they are bought in as grey ammonia "
            "or, beyond the contract's cover, paid at the shortfall penalty.",
        ],
        "provenance": T.PROVENANCE,
    }
