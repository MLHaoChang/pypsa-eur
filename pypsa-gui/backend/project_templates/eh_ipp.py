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
    n.buses.at["poi", "eh_poc"] = True
    for frame in (n.generators, n.links, n.storage_units):
        frame["eh_role"] = ""
    n.generators.at["grid", "eh_role"] = "grid_supply"
    n.links.at["grid_import", "eh_role"] = "grid_import"
    n.links.at["grid_export", "eh_role"] = "grid_export"
