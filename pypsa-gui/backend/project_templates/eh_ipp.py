"""
A renewables IPP with a battery behind one grid connection (IPP renewables + BESS ticket 03,
`.scratch/ipp-renewables-bess/spec.md`): ``eh_ipp_hybrid``.

A generation-only site: a 60 MW wind farm and a 50 MWp PV plant (fixed new build, forced-size
candidates so the investment case counts their capex) and a battery candidate (0–50 MW at 2 h)
share a point of interconnection whose export limit, 70 MW, is below the plants' 110 MW. The site
has no demand. One hourly year (2030) is solved against an illustrative day-ahead price: the
battery is sized, the plants curtail at the export limit and at negative prices, and the PPAs
settle after the solve (financial, against the same price; ticket 01).

Every price here is illustrative and assumed: three deterministic day-ahead shapes generated
below and shipped as `price_series/` (ticket 02), never market data. ``register(T)`` adds the
template to ``eh_templates``' registries (``T`` as in ``eh_staged_hubs``); it is called from there.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TID = "eh_ipp_hybrid"
YEAR = 2030
#: The defaults-pack version the candidates are priced from.
PACK_VERSION = "2026-10-20"

# ── The plant and the connection (owner Q-IPP2 = A, Q-IPP3 = A) ────────────
WIND_MW = 60.0
PV_MWP = 50.0
EXPORT_MW = 70.0
IMPORT_MW = 20.0
BESS_MAX_MW = 50.0
BESS_HOURS = 2.0
#: One-way efficiency (≈ 0.88 round trip).
BESS_EFF = 0.938
#: Battery throughput cost per MWh discharged (StorageUnit `marginal_cost` prices p_dispatch
#: only); against unrealistic cycling, illustrative.
BESS_THROUGHPUT_EUR_PER_MWH = 2.0
#: The grid slack's size: well above both meter Links, so only they bind.
GRID_SLACK_MW = 500.0

#: Where the synthetic profiles are generated until the user sets the pin: a reference point
#: in the DE-LU bidding zone (assumed; the template sets no pin).
REF_LAT, REF_LON = 51.2, 10.4

# ── Prices (illustrative, assumed; spec §2.3) ──────────────────────────────
#: Network charges and levies on import (EUR/MWh; the commodity is the DA series).
NETWORK_CHARGE_EUR_PER_MWH = 20.0
#: The base shape's parameters (EUR/MWh): level, winter uplift, evening and morning peaks, the
#: solar-hour dip per unit PV capacity factor, the wind effect per unit wind deviation, noise.
SHAPE = {"level": 78.0, "winter": 14.0, "evening": 55.0, "morning": 15.0,
         "solar_dip": 90.0, "wind_dip": 90.0, "noise": 10.0}
#: The cannibalised case: a deeper solar dip.
CANNIBALISED_SOLAR_DIP = 130.0
LOW_LEVEL_FACTOR = 0.8
PRICE_SEED = 2030

SERIES = {"base": "ipp_da_base", "low": "ipp_da_low", "cannibalised": "ipp_da_cannibalised"}


def snapshots() -> pd.DatetimeIndex:
    return pd.date_range(f"{YEAR}-01-01", periods=8760, freq="h")


def profiles() -> tuple[pd.Series, pd.Series]:
    """(wind CF, PV CF) for the year at the reference point, from the deterministic synthetic
    source (`services.profile_gen`; the TS-generators package owns the writers)."""
    from services.profile_gen import ProfilePoint, generate_profile

    point = [ProfilePoint(id="ref", lon=REF_LON, lat=REF_LAT)]
    wind = generate_profile(point, "wind", year=YEAR).series["ref"]
    sun = generate_profile(point, "solar", year=YEAR).series["ref"]
    idx = snapshots()
    return (pd.Series(wind.to_numpy(dtype=float), index=idx),
            pd.Series(sun.to_numpy(dtype=float), index=idx))


def price_shape(case: str = "base") -> pd.Series:
    """An illustrative hourly day-ahead price (EUR/MWh, 2030 money, UTC): never market data."""
    wind, sun = profiles()
    idx = wind.index
    h = idx.hour.to_numpy()
    doy = idx.dayofyear.to_numpy()
    s = dict(SHAPE)
    if case == "cannibalised":
        s["solar_dip"] = CANNIBALISED_SOLAR_DIP
    rng = np.random.default_rng(PRICE_SEED)
    p = (s["level"] + s["winter"] * np.cos(2 * np.pi * (doy - 15) / 365.25)
         + s["evening"] * np.exp(-((h - 19) ** 2) / 6.0)
         + s["morning"] * np.exp(-((h - 8) ** 2) / 4.0)
         - s["solar_dip"] * sun.to_numpy()
         - s["wind_dip"] * (wind.to_numpy() - wind.mean())
         + rng.normal(0.0, s["noise"], len(idx)))
    if case == "low":
        p = p * LOW_LEVEL_FACTOR
    return pd.Series(np.round(p, 2), index=idx, name=SERIES[case])


def price_stats(case: str = "base") -> dict:
    """The shape's key numbers, for the ledger."""
    p = price_shape(case)
    wind, sun = profiles()
    return {"mean": float(p.mean()), "negative_hours": int((p < 0).sum()),
            "min": float(p.min()), "max": float(p.max()),
            "pv_capture_rate": float((p * sun).sum() / sun.sum() / p.mean()),
            "wind_capture_rate": float((p * wind).sum() / wind.sum() / p.mean())}


def _price(n, component: str, name: str, technology: str) -> None:
    from services.library.defaults_pack.pricing import price_from_pack

    price_from_pack(n, component, name, technology, version=PACK_VERSION)


def build_eh_ipp_hybrid() -> "pypsa.Network":  # noqa: F821
    import pypsa

    n = pypsa.Network()
    n.name = "Renewables IPP with battery"
    idx = snapshots()
    n.set_snapshots(idx)
    for c in ("AC", "grid", "wind", "solar", "battery"):
        n.add("Carrier", c, co2_emissions=0.0)
    n.add("Bus", "poi", carrier="AC", v_nom=110.0)
    n.add("Bus", "grid", carrier="AC", v_nom=110.0)
    # The market side of the meter: a two-way slack at no cost (the prices are on the meter).
    n.add("Generator", "grid", bus="grid", carrier="grid", p_nom=GRID_SLACK_MW,
          p_min_pu=-1.0, p_max_pu=1.0, marginal_cost=0.0)
    n.add("Link", "grid_import", bus0="grid", bus1="poi", carrier="AC", p_nom=IMPORT_MW,
          efficiency=1.0, marginal_cost=0.0)
    n.add("Link", "grid_export", bus0="poi", bus1="grid", carrier="AC", p_nom=EXPORT_MW,
          efficiency=1.0, marginal_cost=0.0)
    wind, sun = profiles()
    # Forced-size candidates (G5): the investment case counts their capex and the
    # counterfactual (the site without its candidates) is empty.
    for name, carrier, mw, tech in (("wind_farm", "wind", WIND_MW, "onwind"),
                                    ("pv_plant", "solar", PV_MWP, "solar-utility")):
        n.add("Generator", name, bus="poi", carrier=carrier, p_nom=mw, p_nom_extendable=True,
              p_nom_min=mw, p_nom_max=mw, marginal_cost=0.0)
        _price(n, "generators", name, tech)
    n.generators_t.p_max_pu = pd.DataFrame({"wind_farm": wind.to_numpy(),
                                            "pv_plant": sun.to_numpy()}, index=idx)
    n.add("StorageUnit", "bess", bus="poi", carrier="battery", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=BESS_MAX_MW, max_hours=BESS_HOURS,
          efficiency_store=BESS_EFF, efficiency_dispatch=BESS_EFF,
          cyclic_state_of_charge=True, marginal_cost=BESS_THROUGHPUT_EUR_PER_MWH)
    _price(n, "storage_units", "bess", "battery")
    _tag(n)
    return n


def _tag(n) -> None:
    """The energy-hub tags the meter, the readiness checks and the finance adapter read."""
    for col, default in (("eh_poc", False), ("eh_critical", False)):
        n.buses[col] = default
    # The point of connection is the grid-side bus (the energy-hub convention).
    n.buses.at["grid", "eh_poc"] = True
    for frame in (n.generators, n.links, n.storage_units):
        frame["eh_role"] = ""
    n.generators.at["grid", "eh_role"] = "grid_supply"
    n.links.at["grid_import", "eh_role"] = "grid_import"
    n.links.at["grid_export", "eh_role"] = "grid_export"
    # Outage data on the meter (a POI outage costs the export): the FMEA ranks it.
    for link in ("grid_import", "grid_export"):
        for col, default in (("outage_rate_value", float("nan")), ("mttr_hours", float("nan"))):
            if col not in n.links.columns:
                n.links[col] = default
        n.links.at[link, "outage_rate_value"] = 0.002
        n.links.at[link, "mttr_hours"] = 12.0


# ── Commercial and finance (spec §6, §8.3a; every value lives in section 7) ─

SITE_PARTY = "ipp"
OFFTAKER = "offtaker"
#: Illustrative EU pay-as-produced PPA prices (EUR/MWh, 2030 money), the share and the tenor.
PPA = {"wind_farm": ("ppa_wind", 65.0), "pv_plant": ("ppa_pv", 50.0)}
PPA_SHARE = 0.7
PPA_TENOR = 10
COD = f"{YEAR}-01-01"
FINANCIAL_CLOSE = f"{YEAR - 2}-01-01"
ANALYSIS_YEARS = 25
DEGRADATION = {"wind_farm": 0.003, "pv_plant": 0.005, "bess": 0.0}


def _placeholder(series: str) -> dict:
    """A ref the create path replaces with the minted series (ticket 02's `apply_refs`); a
    project never keeps it."""
    return {"id": series, "version": 1, "hash": "template-placeholder", "source": "template"}


def import_tariff() -> dict:
    return {"id": "ipp_poi", "name": "POI network charges (illustrative)", "jurisdiction": "DE",
            "valid_from": f"{YEAR}-01-01",
            "items": [
                {"id": "network_charges", "kind": "energy", "unit": "per_kwh",
                 "measured_on": "import",
                 "periods": [{"name": "all", "rate": NETWORK_CHARGE_EUR_PER_MWH / 1000.0}]},
                {"id": "injection", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
                 "direction": "cost", "periods": [{"name": "all", "rate": 0.0}]}]}


def contracts() -> list[dict]:
    return [{"type": "ppa", "id": cid, "kind": "pay_as_produced", "price": price,
             "tenor_years": PPA_TENOR, "seller": SITE_PARTY, "buyer": OFFTAKER,
             "asset_ids": [asset], "share": PPA_SHARE, "settlement": "financial",
             "suspend_on_negative_price": True, "base_year": YEAR,
             "reference_price": _placeholder(SERIES["base"])}
            for asset, (cid, price) in PPA.items()]


def value_flows() -> dict:
    from models.commercial import DEFAULT_EXTERNALS

    return {"template": "custom",
            "participants": [{"id": SITE_PARTY, "name": "IPP (developer)", "role": "developer"}],
            "externals": [*DEFAULT_EXTERNALS, OFFTAKER],
            "asset_owners": [{"asset_id": a, "component": c, "owner": SITE_PARTY}
                             for a, c in (("wind_farm", "Generator"), ("pv_plant", "Generator"),
                                          ("bess", "StorageUnit"))],
            "export_revenue_to": "site_party"}


FINANCE: dict = {
    "currency": "EUR", "currency_year": YEAR, "price_basis": "nominal",
    "financial_close": FINANCIAL_CLOSE,
    "cod_by_asset": {a: COD for a in ("wind_farm", "pv_plant", "bess")},
    "capex_phasing": [0.4, 0.6], "stage_capex_lead_years": 1, "contingency_share": 0.05,
    "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
    "degradation_by_asset": DEGRADATION,
    "analysis_years": ANALYSIS_YEARS,
    "annualise": False,                          # a full hourly year
    "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
    "replacement_rule": "part_lifetimes",
    "terminal_value": {"method": "remaining_life_annuity"},
    "wacc_nominal": 0.07, "cost_of_equity": 0.09, "inflation": 0.02,
    "solve_ppa": {"contract_id": "ppa_wind", "target_irr": 0.08, "target_year": ANALYSIS_YEARS},
}


def commercial_and_finance() -> dict:
    """The template's `commercial` and `finance` blocks, validated as the routes store them."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs

    commercial = CommercialConfig.model_validate({
        "poc_link": "grid_import", "export_link": "grid_export", "timezone": "UTC",
        "site_party": SITE_PARTY, "import_tariff": import_tariff(),
        "export_price_ref": _placeholder(SERIES["base"]),
        "import_price_ref": _placeholder(SERIES["base"]),
        "contracts": contracts(), "value_flows": value_flows()})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(FINANCE).model_dump(mode="json")}


# ── The shipped price series (ticket 02) ───────────────────────────────────

SERIES_NOTES = ["currency:EUR", "price_basis:real", f"currency_year:{YEAR}", "region:DE-LU"]


def series_meta() -> dict:
    """`price_series/meta.json`: the base shape fills the export, import and PPA references;
    the two case shapes are minted for the cases (§7) and fill nothing."""
    base_targets = ["commercial.export_price_ref", "commercial.import_price_ref",
                    *(f"contract:{cid}.reference_price" for cid, _p in PPA.values())]
    out = []
    for case, label in (("base", "base"), ("low", "low price level (× 0.8)"),
                        ("cannibalised", "deeper solar cannibalisation")):
        out.append({"name": SERIES[case], "file": f"{case}.csv",
                    "label": f"Illustrative DE-LU day-ahead shape, {label} ({YEAR})",
                    "source": "illustrative (assumed)", "vintage_year": YEAR,
                    "description": "Generated by project_templates/eh_ipp.py; not market data. "
                                   "Replace it with your own (upload, or ENTSO-E with your token).",
                    "notes": list(SERIES_NOTES),
                    "targets": base_targets if case == "base" else []})
    return {"series": out}


def write_price_series(out_dir) -> None:
    import json
    import pathlib

    d = pathlib.Path(out_dir) / "price_series"
    d.mkdir(parents=True, exist_ok=True)
    for case in SERIES:
        p = price_shape(case)
        pd.DataFrame({"timestamp": p.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "value": p.to_numpy()}).to_csv(d / f"{case}.csv", index=False)
    (d / "meta.json").write_text(json.dumps(series_meta(), indent=2, sort_keys=True) + "\n")


# ── The ledger and the registration ────────────────────────────────────────

def _ledger() -> list[dict]:
    st = price_stats("base")
    return [
        {"item": "planning period", "value": f"{YEAR}, one hourly year (8760 h)",
         "source": "assumed",
         "note": "the example is built for 2030 (native horizon until free planning years, E1)"},
        {"item": "site location", "value": "not set", "source": "assumed",
         "note": f"the template invents no pin; profiles are generated at a DE-LU reference "
                 f"point ({REF_LAT} N, {REF_LON} E) until you set it, and regenerated then"},
        {"item": "bidding zone", "value": "DE-LU", "source": "assumed",
         "note": "re-derived from the pin once it is set"},
        {"item": "export limit", "value": f"{EXPORT_MW:g} MW (grid_export)", "source": "assumed",
         "note": "from the connection offer; below the plants' 110 MW (overbuild 1.57)"},
        {"item": "import limit", "value": f"{IMPORT_MW:g} MW (grid_import)", "source": "assumed",
         "note": "auxiliaries and battery grid charging"},
        {"item": "wind farm", "value": f"{WIND_MW:g} MW, fixed new build", "source": "pack",
         "note": f"onwind costs from defaults pack {PACK_VERSION}; forced size (the IC counts "
                 "the capex)"},
        {"item": "PV plant", "value": f"{PV_MWP:g} MWp, fixed new build", "source": "pack",
         "note": f"solar-utility costs from defaults pack {PACK_VERSION}"},
        {"item": "battery", "value": f"candidate 0–{BESS_MAX_MW:g} MW at {BESS_HOURS:g} h, "
                                     f"round trip {BESS_EFF ** 2:.2f}", "source": "pack",
         "note": f"battery power and energy parts from defaults pack {PACK_VERSION}; throughput "
                 f"cost {BESS_THROUGHPUT_EUR_PER_MWH:g} EUR per MWh discharged (illustrative)"},
        {"item": "wind and PV profiles", "value": "synthetic, deterministic", "source": "generated",
         "note": "services.profile_gen synthetic source; replace with ERA5 or measured data"},
        {"item": "day-ahead price (base)",
         "value": f"mean {st['mean']:.1f} EUR/MWh, {st['negative_hours']} negative hours, PV "
                  f"capture rate {st['pv_capture_rate']:.2f}, wind {st['wind_capture_rate']:.2f}",
         "source": "illustrative",
         "note": "an assumed shape, not market data (owner Q8 = A). For scale: German day-ahead "
                 "prices in 2024 averaged roughly 80 EUR/MWh with several hundred negative hours "
                 "(SMARD, Bundesnetzagentur; check the current figures). Bring your own series"},
        {"item": "network charges on import",
         "value": f"{NETWORK_CHARGE_EUR_PER_MWH:g} EUR/MWh; injection 0 EUR/MWh",
         "source": "assumed", "note": "the commodity on import is the day-ahead series"},
        {"item": "PPAs", "value": f"financial pay-as-produced, {PPA_SHARE:.0%} of each plant, "
                                  f"wind {PPA['wind_farm'][1]:g} / PV {PPA['pv_plant'][1]:g} "
                                  f"EUR/MWh, {PPA_TENOR} years",
         "source": "illustrative",
         "note": "settled against the day-ahead series; suspended at negative prices (the offtaker "
                 "accepts curtailment). Prices are an assumed illustration within the range of "
                 "public European PPA price indices; replace with your term sheet"},
        {"item": "finance", "value": "7 % WACC, 9 % cost of equity, 2 % escalation, 25 years",
         "source": "assumed",
         "note": "base-year money (2030), held flat to the base year then escalated (label only)"},
        {"item": "degradation", "value": "PV 0.5 %/yr, wind 0.3 %/yr", "source": "assumed",
         "note": "the faster-degradation case doubles them"},
        {"item": "cases", "value": "8 planned cases (editable once sensitivities land; make any now "
                                   "with Duplicate)", "source": "template",
         "note": "cases.json beside the template"},
    ]


def register(T) -> None:
    """Add the IPP template to ``eh_templates``' registries."""
    T.BUILDERS[TID] = build_eh_ipp_hybrid
    T.STRESS_SCENARIOS[TID] = []
    T.SOLVER_CONFIG_EXTRA[TID] = {"discount_rate": 0.07}
    T._SOLVER_CONFIG_BUILT[TID] = commercial_and_finance
    T.PRICE_SERIES[TID] = write_price_series
    T.TEMPLATE_META[TID] = {
        "id": TID,
        "name": "Renewables IPP with battery",
        "description": (
            "A 60 MW wind farm and a 50 MWp PV plant with an optional battery behind one 70 MW "
            "grid connection. Size the battery and weigh PPA against merchant revenue on an "
            "illustrative day-ahead price over a full hourly 2030. Generation-only; financial "
            "PPAs and a 25-year investment case."),
        "recommended_archetype": "strong_grid",
        # No load: no bus is critical and no adequacy goal applies (spec §8.2).
        "generation_only": True,
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "groups": {"capacity": True, "commercial": True, "handoff": True,
                   "grid": False, "control": False},
        "not_needed": {"loads": "generation-only: export through the POI in section 3"},
        "initial_results_tab": "commercial",
        "ledger": _ledger(),
        "study_notes": [
            "One hourly year: the battery is sized against the day-ahead price and the curtailment "
            "it can recover at the export limit.",
            "The PPAs settle after the solve, so they never change the battery the plan picks.",
            "Every price is illustrative: bring your own series before relying on a result.",
        ],
        "provenance": "synthetic illustrative data (IPP template); prices are assumed shapes",
    }
