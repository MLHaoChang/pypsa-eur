"""
A renewables IPP with a battery behind one grid connection (IPP renewables + BESS ticket 03,
`.scratch/ipp-renewables-bess/spec.md`): ``eh_ipp_hybrid``.

A generation-only site: a 60 MW wind farm and a 50 MWp PV plant (fixed new build, forced-size
candidates so the investment case counts their capex) and a battery candidate (0–50 MW at 2 h)
share a point of interconnection whose export limit, 70 MW, is below the plants' 110 MW. The site
has no demand. One hourly year (2030) is solved against an illustrative day-ahead price: the
battery is sized, the plants curtail at the export limit and at negative prices, and the PPAs
settle after the solve (financial, against the same price; ticket 01).

The default price is illustrative and assumed: three deterministic day-ahead shapes generated
below and shipped as `price_series/` (ticket 02), never market data. Two observed 2024 years
(SMARD DE-LU, Energinet DK1; GS-4) ship beside them, attributed and filling nothing until the
user picks one (`OBSERVED`). ``register(T)`` adds the
template to ``eh_templates``' registries (``T`` as in ``eh_staged_hubs``); it is called from there.

A create body's ``planning`` {start_year, end_year} builds it for another year (E1d, owner
E1D-IPP-1): the start year is the solved year and the COD, the case runs to the end year. The
example's 2030 profiles and shapes and the observed 2024 years are re-dated onto it (hour of year
kept, 29 Feb dropped) and taken as start-year money, no conversion; the ledger says so.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TID = "eh_ipp_hybrid"
YEAR = 2030
#: The defaults-pack version the candidates are priced from.
PACK_VERSION = "2026-10-26"

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
CF_FLOOR = 1e-3        # a capacity factor below this is written as 0 (see `profiles`)

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

#: Two observed day-ahead years shipped beside the shapes, filling nothing (owner Q-GS4 = A,
#: 2026-10-21, superseding Q8 for these two sources): fetched once by
#: `fetch_ipp_market_series.py` into `eh_ipp_hybrid/sources/` (CC BY 4.0, attributed), re-dated
#: onto the template's calendar by `observed_series`.
OBSERVED = {
    "ipp_da_smard_2024": {"source_file": "smard_de_lu_2024.csv", "file": "smard_2024.csv",
                          "region": "DE-LU", "provider": "SMARD",
                          "attribution": "Bundesnetzagentur | SMARD.de"},
    "ipp_da_dk1_2024": {"source_file": "energinet_dk1_2024.csv", "file": "dk1_2024.csv",
                        "region": "DK1", "provider": "Energinet",
                        "attribution": "Source: Energinet (www.energidataservice.dk)"},
}
OBSERVED_YEAR = 2024


def snapshots(year: int = YEAR) -> pd.DatetimeIndex:
    """The year's hours; a leap year's 29 Feb is dropped, so every year has 8760."""
    idx = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left")
    return idx if len(idx) == 8760 else idx[~((idx.month == 2) & (idx.day == 29))]


def _year(planning) -> int:
    return YEAR if planning is None else planning.start_year


def profiles(year: int = YEAR) -> tuple[pd.Series, pd.Series]:
    """(wind CF, PV CF) for the example's year at the reference point, from the deterministic
    synthetic source (`services.profile_gen`; the TS-generators package owns the writers),
    re-dated onto ``year`` (hour of year kept)."""
    from services.profile_gen import ProfilePoint, generate_profile

    point = [ProfilePoint(id="ref", lon=REF_LON, lat=REF_LAT)]
    wind = generate_profile(point, "wind", year=YEAR).series["ref"]
    sun = generate_profile(point, "solar", year=YEAR).series["ref"]
    idx = snapshots(year)
    # A dawn sliver below 0.1 % reads as zero: with half the year dark, a handful of such hours
    # would set the PV median and trip the profile QA's spike rule (`timeseries_spike`).
    pv = sun.to_numpy(dtype=float)
    pv = np.where(pv < CF_FLOOR, 0.0, pv)
    return (pd.Series(wind.to_numpy(dtype=float), index=idx), pd.Series(pv, index=idx))


def price_shape(case: str = "base", year: int = YEAR) -> pd.Series:
    """An illustrative hourly day-ahead price (EUR/MWh, 2030 money, UTC): never market data.
    Another ``year`` gets the same values re-dated (hour of year kept)."""
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
    return pd.Series(np.round(p, 2), index=snapshots(year), name=SERIES[case])


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


def build_eh_ipp_hybrid(load_spec=None, planning=None) -> "pypsa.Network":  # noqa: F821
    """The site for one hourly year: 2030, or the start year of ``planning`` (E1d)."""
    import pypsa

    year = _year(planning)
    n = pypsa.Network()
    n.name = "Renewables IPP with battery"
    idx = snapshots(year)
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
    wind, sun = profiles(year)
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
    # The export meter carries no role: `export_link` names it (7a), and `grid_export` is no
    # Energy Hub role, so the Link form would refuse every edit of a Link tagged with it.
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
ANALYSIS_YEARS = 25
ESCALATION = 0.02                                # every cash class, per year
DEGRADATION = {"wind_farm": 0.003, "pv_plant": 0.005, "bess": 0.0}
HEBESATZ_PCT = 400.0
BESS_DEPRECIATION = "afa_10"


def _placeholder(series: str) -> dict:
    """A ref the create path replaces with the minted series (ticket 02's `apply_refs`); a
    project never keeps it."""
    return {"id": series, "version": 1, "hash": "template-placeholder", "source": "template"}


def import_tariff(year: int = YEAR) -> dict:
    return {"id": "ipp_poi", "name": "POI network charges (illustrative)", "jurisdiction": "DE",
            "valid_from": f"{year}-01-01",
            "items": [
                {"id": "network_charges", "kind": "energy", "unit": "per_kwh",
                 "measured_on": "import",
                 "periods": [{"name": "all", "rate": NETWORK_CHARGE_EUR_PER_MWH / 1000.0}]},
                {"id": "injection", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
                 "direction": "cost", "periods": [{"name": "all", "rate": 0.0}]}]}


def contracts(year: int = YEAR) -> list[dict]:
    return [{"type": "ppa", "id": cid, "kind": "pay_as_produced", "price": price,
             "tenor_years": PPA_TENOR, "seller": SITE_PARTY, "buyer": OFFTAKER,
             "asset_ids": [asset], "share": PPA_SHARE, "settlement": "financial",
             "suspend_on_negative_price": True, "base_year": year,
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


def finance(planning=None) -> dict:
    """The case's inputs: close two years before the COD, the COD the start year, the case to
    the end year, in start-year money (E1d; None: the example's 2030 to 2055)."""
    year = _year(planning)
    analysis = ANALYSIS_YEARS if planning is None else planning.analysis_years
    cod = f"{year}-01-01"
    return {
        "currency": "EUR", "currency_year": year, "price_basis": "nominal",
        "financial_close": f"{year - 2}-01-01",
        "cod_by_asset": {a: cod for a in ("wind_farm", "pv_plant", "bess")},
        "capex_phasing": [0.4, 0.6], "stage_capex_lead_years": 1, "contingency_share": 0.05,
        "escalation": {c: ESCALATION for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
        "degradation_by_asset": DEGRADATION,
        "analysis_years": analysis,
        "annualise": False,                          # a full hourly year
        "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
        "replacement_rule": "part_lifetimes",
        "terminal_value": {"method": "remaining_life_annuity"},
        "wacc_nominal": 0.07, "cost_of_equity": 0.09, "inflation": 0.02,
        # German corporate tax (the DE pack): the municipality's Hebesatz and the battery's
        # depreciation class, which the BMF AfA tables do not list, are stated assumptions.
        "tax_pack_id": "eu_de", "hebesatz_pct": HEBESATZ_PCT,
        # The pack's AfA lives (solar 20, onwind 16 years) stated per asset, the battery's assumed.
        "depreciation_class_by_asset": {"pv_plant": "afa_20", "wind_farm": "afa_16",
                                        "bess": BESS_DEPRECIATION},
        "acquisition_date": cod,
        "solve_ppa": {"contract_id": "ppa_wind", "target_irr": 0.08, "target_year": analysis},
    }


FINANCE: dict = finance()


def commercial_and_finance(planning=None) -> dict:
    """The template's `commercial` and `finance` blocks, validated as the routes store them,
    for ``planning`` (None: the example's)."""
    year = _year(planning)
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs

    commercial = CommercialConfig.model_validate({
        "poc_link": "grid_import", "export_link": "grid_export", "timezone": "UTC",
        "site_party": SITE_PARTY, "import_tariff": import_tariff(year),
        "export_price_ref": _placeholder(SERIES["base"]),
        "import_price_ref": _placeholder(SERIES["base"]),
        "contracts": contracts(year), "value_flows": value_flows()})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(finance(planning)).model_dump(mode="json")}


# ── The shipped price series (ticket 02) ───────────────────────────────────

SERIES_NOTES = ["currency:EUR", "price_basis:real", f"currency_year:{YEAR}", "region:DE-LU"]


def series_meta(year: int = YEAR) -> dict:
    """`price_series/meta.json`: the base shape fills the export, import and PPA references;
    the two case shapes are minted for the cases (§7) and fill nothing. Dated and in money of
    ``year``: every `currency_year` note moves with the case's (`basis_conflicts`)."""
    redated = ("" if year == YEAR else
               f" Re-dated from {YEAR} (hour of year kept, 29 Feb dropped; weekdays and holidays "
               f"fall as in {YEAR}) and taken as {year} money, no conversion.")
    base_targets = ["commercial.export_price_ref", "commercial.import_price_ref",
                    *(f"contract:{cid}.reference_price" for cid, _p in PPA.values())]
    out = []
    for case, label in (("base", "base"), ("low", "low price level (× 0.8)"),
                        ("cannibalised", "deeper solar cannibalisation")):
        out.append({"name": SERIES[case], "file": f"{case}.csv",
                    "label": f"Illustrative DE-LU day-ahead shape, {label} ({year})",
                    "source": "illustrative (assumed)", "vintage_year": year,
                    "description": "Generated by project_templates/eh_ipp.py; not market data. "
                                   "Replace it with your own (upload, or ENTSO-E with your token)."
                                   + redated,
                    "notes": [*SERIES_NOTES[:2], f"currency_year:{year}", *SERIES_NOTES[3:]],
                    "targets": base_targets if case == "base" else []})
    for name, spec in OBSERVED.items():
        out.append({"name": name, "file": spec["file"],
                    "label": f"Observed {spec['region']} day-ahead price {OBSERVED_YEAR}, "
                             f"re-dated to {year}",
                    "source": f"{spec['attribution']} (CC BY 4.0)", "provider": spec["provider"],
                    "vintage_year": OBSERVED_YEAR,
                    "description": (
                        f"Observed {OBSERVED_YEAR} hourly day-ahead prices, {spec['region']} "
                        f"({spec['attribution']}). The data changed: re-dated to the {year} "
                        "calendar (hour of year kept, 29 Feb dropped, UTC kept, so "
                        + ("weekdays shift by one day" if year == YEAR else
                           f"weekdays and holidays fall as in {OBSERVED_YEAR}")
                        + f") and taken as {year} real money (assumed). The "
                        "price–weather correlation not preserved: the prices are "
                        f"{OBSERVED_YEAR}'s, the template's wind and PV profiles are synthetic. "
                        "Fetched once by project_templates/fetch_ipp_market_series.py."),
                    "notes": ["currency:EUR", "price_basis:real", f"currency_year:{year}",
                              f"region:{spec['region']}"],
                    "targets": []})
    return {"series": out}


def observed_series(name: str, year: int = YEAR) -> pd.Series:
    """An observed 2024 year on the template's 2030 snapshots (or ``year``'s): hour of year
    kept, 29 Feb dropped, UTC kept (so 2030's weekdays fall one day later than 2024's). The
    values are the source's, unchanged; taking them as 2030 real money is an assumption (the
    ledger's)."""
    import pathlib

    src = pathlib.Path(__file__).parent / TID / "sources" / OBSERVED[name]["source_file"]
    df = pd.read_csv(src)
    idx = pd.DatetimeIndex(pd.to_datetime(df["timestamp"], utc=True)).tz_convert(None)
    keep = ~((idx.month == 2) & (idx.day == 29))
    values = df["value"].to_numpy(dtype=float)[keep]
    if len(values) != 8760 or (idx[keep].year != OBSERVED_YEAR).any():
        raise ValueError(f"{src.name}: not one full hourly {OBSERVED_YEAR}")
    return pd.Series(values, index=snapshots(year), name=name)


def observed_stats(name: str) -> dict:
    p = observed_series(name)
    return {"mean": float(p.mean()), "negative_hours": int((p < 0).sum()),
            "min": float(p.min()), "max": float(p.max())}


def _write_csv(p: pd.Series, path) -> None:
    pd.DataFrame({"timestamp": p.index.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "value": p.to_numpy()}).to_csv(path, index=False)


def write_price_series(out_dir, planning=None) -> None:
    import json
    import pathlib

    year = _year(planning)
    d = pathlib.Path(out_dir) / "price_series"
    d.mkdir(parents=True, exist_ok=True)
    for case in SERIES:
        _write_csv(price_shape(case, year), d / f"{case}.csv")
    for name, spec in OBSERVED.items():
        _write_csv(observed_series(name, year), d / spec["file"])
    (d / "meta.json").write_text(json.dumps(series_meta(year), indent=2, sort_keys=True) + "\n")


# ── The ledger and the registration ────────────────────────────────────────

def _ledger(planning=None) -> list[dict]:
    st = price_stats("base")
    year = _year(planning)
    analysis = ANALYSIS_YEARS if planning is None else planning.analysis_years
    period = ({"item": "planning period", "value": f"{YEAR}, one hourly year (8760 h)",
               "source": "assumed",
               "note": "the example is built for 2030 (native horizon until free planning years, "
                       "E1)"}
              if planning is None else
              {"item": "planning period",
               "value": f"{year}, one hourly year (8760 h); the case runs to {planning.end_year}",
               "source": "entered",
               "note": f"the entered start year is the solved year and the COD; financial close "
                       f"{year - 2}, {analysis} operating years"})
    return [
        period,
        *([] if planning is None else _redated_rows(year)),
        {"item": "site location", "value": "not set", "source": "assumed",
         "note": f"the template invents no pin; profiles are generated at a DE-LU reference "
                 f"point ({REF_LAT} N, {REF_LON} E) until you set it, and regenerated then"},
        {"item": "bidding zone", "value": "DE-LU", "source": "assumed",
         "note": "re-derived from the pin once it is set"},
        {"item": "export limit", "value": f"{EXPORT_MW:g} MW (grid_export)", "source": "assumed",
         "note": f"from the connection offer; below the plants' {WIND_MW + PV_MWP:g} MW "
                 f"(overbuild {(WIND_MW + PV_MWP) / EXPORT_MW:.2f})"},
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
                 "prices in 2024 averaged roughly 80 EUR/MWh with some 450 negative hours "
                 "(SMARD, Bundesnetzagentur; check the current figures). This shape has fewer, "
                 "so the curtailment and battery value are conservative. Bring your own series"},
        *({"item": f"day-ahead price, observed ({spec['region']} {OBSERVED_YEAR})",
           "value": f"mean {ob['mean']:.1f} EUR/MWh, {ob['negative_hours']} negative hours, "
                    f"min {ob['min']:.0f}, max {ob['max']:.0f}",
           "source": "observed",
           "note": f"{spec['attribution']}, CC BY 4.0; shipped as {name}, filling nothing: pick "
                   "it for the export, import or PPA reference price, or in a case"}
          for name, spec in OBSERVED.items() for ob in (observed_stats(name),)),
        {"item": (f"observed prices re-dated to {YEAR}" if year == YEAR else
                  f"observed prices re-dated from {OBSERVED_YEAR} to {year}"),
         "value": "hour of year, 29 Feb dropped",
         "source": "assumed",
         "note": f"the {OBSERVED_YEAR} values are taken as {year} real money; UTC hours kept, so "
                 + ("weekdays shift by one day" if year == YEAR else
                    f"the weekday and holiday alignment is {OBSERVED_YEAR}'s")
                 + "; price–weather correlation not preserved: prices are "
                 f"{OBSERVED_YEAR}, the profiles are synthetic"},
        {"item": "network charges on import",
         "value": f"{NETWORK_CHARGE_EUR_PER_MWH:g} EUR/MWh; injection 0 EUR/MWh",
         "source": "assumed", "note": "the commodity on import is the day-ahead series"},
        {"item": "PPAs", "value": f"financial pay-as-produced, {PPA_SHARE:.0%} of each plant, "
                                  f"wind {PPA['wind_farm'][1]:g} / PV {PPA['pv_plant'][1]:g} "
                                  f"EUR/MWh, {PPA_TENOR} years",
         "source": "illustrative",
         "note": "settled against the day-ahead series; suspended at negative prices (the offtaker "
                 "accepts curtailment). Prices are an assumed illustration: German PPA price "
                 "indices (LevelTen, Pexapark) put onshore wind at roughly 60-80 and solar at "
                 "45-60 EUR/MWh in 2024-25 (check the current figures); replace with your term "
                 "sheet"},
        {"item": "finance",
         "value": f"{FINANCE['wacc_nominal'] * 100:g} % WACC, {FINANCE['cost_of_equity'] * 100:g} % cost of "
                  f"equity, {ESCALATION * 100:g} % escalation, {analysis} years",
         "source": "assumed",
         "note": f"a nominal, post-tax case in base-year money ({year}), escalated from that year. "
                 f"The defaults pack's {PACK_VERSION} discount rate (7 %) is real and pre-tax, "
                 "so it is not this WACC; the optimisation's discount rate is the WACC (K0)"},
        {"item": "tax", "value": f"German corporate tax (pack eu_de), Hebesatz {HEBESATZ_PCT:g} %",
         "source": "assumed",
         "note": f"the Hebesatz depends on the municipality; the battery's depreciation class "
                 f"({BESS_DEPRECIATION}, 10-year straight line) is assumed because the BMF AfA "
                 "tables do not list battery storage"},
        {"item": "degradation", "value": f"PV {DEGRADATION['pv_plant'] * 100:g} %/yr, wind "
                                         f"{DEGRADATION['wind_farm'] * 100:g} %/yr", "source": "assumed",
         "note": "the faster-degradation case doubles them"},
        {"item": "cases", "value": "8 planned cases (editable once sensitivities land; make any now "
                                   "with Duplicate)", "source": "template",
         "note": "cases.json beside the template"},
    ]


def _redated_rows(year: int) -> list[dict]:
    """The ledger rows an entered start year adds (E1d; gatekeeper's plan conditions 2 and 3)."""
    return [
        {"item": "money year", "value": f"{year} EUR, no conversion", "source": "assumed",
         "note": f"the example's {YEAR} money (illustrative shapes, PPA prices and network "
                 f"charges) and the observed {OBSERVED_YEAR} prices are taken as {year} money, "
                 f"not converted. The case stays nominal at the same "
                 f"{ESCALATION * 100:g} % escalation, which now runs from {year}"},
        {"item": f"illustrative prices and profiles re-dated from {YEAR}",
         "value": "hour of year kept, 29 Feb dropped", "source": "assumed",
         "note": f"the example's {YEAR} wind and PV profiles and day-ahead shapes keep their "
                 f"values; their weekday and holiday alignment is {YEAR}'s, not {year}'s"},
        {"item": "tariff and PPA dates", "value": f"valid from {year}-01-01, PPA base year {year}",
         "source": "assumed",
         "note": f"the example's {YEAR} network charges and PPA prices, re-dated with the series"},
    ]


# ── Pre-built cases (spec §7; ticket 04): data only until the sensitivities slice (S5) ─
#
# Each case is the minimum set of declared changes that must move together (gatekeeper), in
# the experiment contract's path and value rules (`services.experiments._validate_change`):
# a network input `<Component>.<name>.<attribute>`, a commercial one
# `commercial.contracts.<contract id>.<field>` or `commercial.<field>…`, a finance one
# `finance.<field>.<key>`. A price-series change names the series id
# (`commercial.<ref>.id`): S5 resolves it in the org Library to its one entry (minted at
# create) and writes the whole reference, never the id alone.

CASES_VERSION = 1
CHEAP_BATTERY_FACTOR = 0.7
LONGER_PPA_TENOR = 15


def _change(path: str, old, new) -> dict:
    return {"path": path, "value_type": "string" if isinstance(new, str) else "number",
            "old": old, "new": new}


def _price_refs(new: str) -> list[dict]:
    paths = ["commercial.export_price_ref.id", "commercial.import_price_ref.id",
             *(f"commercial.contracts.{cid}.reference_price.id" for cid, _p in PPA.values())]
    return [_change(p, SERIES["base"], new) for p in paths]


def _per_ppa(field: str, value) -> list[dict]:
    return [_change(f"commercial.contracts.{cid}.{field}", old, value(old))
            for cid, old in ((c, {"price": p, "share": PPA_SHARE, "tenor_years": PPA_TENOR}[field])
                             for c, p in PPA.values())]


def cases() -> dict:
    """The base and 8 cases of spec §7 as declared changes (`cases.json`)."""
    n = build_eh_ipp_hybrid()
    parts = {f: float(n.storage_units.at["bess", f])
             for f in ("inv_power_overnight", "inv_energy_overnight")}
    same_series = ("the export, the import (G7) and both PPA references follow one price "
                   "series, so they move together")
    both_ppas = "the same term on both PPAs (wind and PV), as one negotiation"
    return {
        "version": CASES_VERSION, "template": TID,
        "note": "Declared changes, data only: nothing lists or applies them until the "
                "sensitivities slice (S5) turns them into ordinary, editable cases.",
        "base": "pay-as-produced PPAs at 70 %, da_base, a 70 MW export limit, the pack's "
                "battery cost",
        "cases": [
            {"id": "low_price", "label": "Low price level", "answers": "merchant risk",
             "reason": same_series, "changes": _price_refs(SERIES["low"])},
            {"id": "cannibalised", "label": "Cannibalised shape",
             "answers": "shape risk, battery value", "reason": same_series,
             "changes": _price_refs(SERIES["cannibalised"])},
            {"id": "ppa_price_minus_10", "label": "PPA price −10 %",
             "answers": "PPA negotiation", "reason": both_ppas,
             "changes": _per_ppa("price", lambda p: round(p * 0.9, 6))},
            {"id": "merchant_heavy", "label": "Merchant-heavy", "answers": "PPA share vs merchant",
             "reason": both_ppas, "changes": _per_ppa("share", lambda _s: 0.3)},
            {"id": "longer_ppa", "label": "Longer PPA", "answers": "PPA tenor",
             "reason": both_ppas + ". It replaces spec §7's baseload case: a baseload form "
                       "has no financial settlement (owner Q-IPP6 = A)",
             "changes": _per_ppa("tenor_years", lambda _t: LONGER_PPA_TENOR)},
            {"id": "cheap_battery", "label": "Cheap battery", "answers": "battery size",
             "reason": "both upfront parts × 0.7, so the power/energy split is kept; the fixed "
                       "O&M is a share of the power part and moves with it. Applied through the "
                       "asset form, which re-derives capital_cost from the parts",
             "changes": [_change(f"StorageUnit.bess.{f}", v,
                                 round(v * CHEAP_BATTERY_FACTOR, 6))
                         for f, v in parts.items()]},
            {"id": "tight_connection", "label": "Tight connection",
             "answers": "connection limit, battery size",
             "reason": "the export Link's size is the binding limit (no connection agreement "
                       "is set)",
             "changes": [_change("Link.grid_export.p_nom", EXPORT_MW, 55.0)]},
            {"id": "faster_degradation", "label": "Faster degradation",
             "answers": "lifetime revenue", "reason": "both plants age faster together",
             "changes": [_change("finance.degradation_by_asset.pv_plant",
                                 DEGRADATION["pv_plant"], 0.01),
                         _change("finance.degradation_by_asset.wind_farm",
                                 DEGRADATION["wind_farm"], 0.008)]},
        ],
    }


def register(T) -> None:
    """Add the IPP template to ``eh_templates``' registries."""
    T.BUILDERS[TID] = build_eh_ipp_hybrid
    # One hourly year; the case runs its 25 years to 2055 (E1d: a planning entry moves both).
    T.NATIVE_PLANNING[TID] = T.Planning((YEAR,), YEAR + ANALYSIS_YEARS)
    T.SINGLE_YEAR[TID] = True
    T._PLANNED_META[TID] = lambda p, _spec: _meta(p)
    T.STRESS_SCENARIOS[TID] = []
    # No load, so nothing to shed: VoLL is not applicable (spec §8.2) and no shedding slacks
    # are added; a VoLL with no capture would leave the case's served load unknown.
    T.SOLVER_CONFIG_EXTRA[TID] = {"discount_rate": FINANCE["wacc_nominal"], "voll": 0.0}
    T._SOLVER_CONFIG_BUILT[TID] = commercial_and_finance
    T.PRICE_SERIES[TID] = write_price_series
    T.CASES[TID] = cases
    T.TEMPLATE_META[TID] = _meta()


def _meta(planning=None) -> dict:
    """`eh_template.json` for ``planning`` (None: the example's 2030 to 2055)."""
    year = _year(planning)
    analysis = ANALYSIS_YEARS if planning is None else planning.analysis_years
    return {
        "id": TID,
        "name": "Renewables IPP with battery",
        "description": (
            "A 60 MW wind farm and a 50 MWp PV plant with an optional battery behind one 70 MW "
            "grid connection. Size the battery and weigh PPA against merchant revenue on an "
            f"illustrative day-ahead price over a full hourly {year}. Generation-only; financial "
            f"PPAs and a {analysis}-year investment case."),
        "recommended_archetype": "strong_grid",
        # No load: no bus is critical and no adequacy goal applies (spec §8.2).
        "generation_only": True,
        "pack_overrides": {},
        "stages": None,
        "dtc_attribution": None,
        "groups": {"capacity": True, "commercial": True, "handoff": True,
                   "grid": False, "control": False},
        "not_needed": {"loads": "generation-only: export through the POI in section 3"},
        "initial_results_tab": "investment",            # the Investment case view (ticket 05)
        "initial_results_section": "case",
        "ledger": _ledger(planning),
        "study_notes": [
            "One hourly year: the battery is sized against the day-ahead price and the curtailment "
            "it can recover at the export limit.",
            "The PPAs settle after the solve, so they never change the battery the plan picks.",
            "The default price is illustrative; two observed 2024 years ship beside it. Bring "
            "your own series before relying on a result.",
        ],
        "provenance": "synthetic illustrative data (IPP template); prices are assumed shapes",
    }
