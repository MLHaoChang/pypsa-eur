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


def _base(name: str) -> pypsa.Network:
    n = pypsa.Network()
    n.name = name
    n.set_snapshots(SNAPSHOTS)
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
TEMPLATE_PACK_VERSION = "2026-10-13"


def _price(n, component: str, name: str, technology: str) -> None:
    """Price one candidate from the pack (`defaults_pack.pricing.price_from_pack`)."""
    from services.library.defaults_pack.pricing import price_from_pack

    price_from_pack(n, component, name, technology, version=TEMPLATE_PACK_VERSION)


# ── Data Center Energy Hub (weak_flexible) ─────────────────────────────────


def build_eh_datacenter(phases=None, load_spec=None) -> pypsa.Network:
    """A 50 MW hyperscale campus behind a weak, capped grid connection.

    Critical IT sits on its own bus behind the site transformer (a near
    loss-free Link, so the P16 per-Load priority is exact); cooling and
    offices share the MV bus and are the non-critical demand. A UPS battery
    rides on the IT bus, a four-unit gas genset fleet on the MV bus.

    ``phases`` (``DC_PHASES``) makes it the staged-growth campus
    (``_staged_datacenter``); None is the single-year template, unchanged.
    ``load_spec`` (a checked ``DataCentreLoadSpec``, ``dc_load_spec``) opts in
    to the AI-training profile; None keeps today's load bit for bit.
    """
    n = _base("Data Center Energy Hub")
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
        {"grid_supply": price}, index=SNAPSHOTS)

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
    }, index=SNAPSHOTS)

    for i in range(1, 5):
        g = f"genset_{i}"
        n.add("Generator", g, bus="dc_mv", carrier="gas", p_nom=10.0,
              marginal_cost=165.0)
        _outage(n, "generators", g, 0.03, 24.0)
    n.add("Generator", "rooftop_pv", bus="dc_mv", carrier="solar", p_nom=4.0,
          marginal_cost=0.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"rooftop_pv": _solar()}, index=SNAPSHOTS)
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
        _apply_dc_profile(n, load_spec, SNAPSHOTS, _single_year_phase(load_spec).it_mw,
                          timezone=_dc_timezone("eh_datacenter"))
    return n


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
DC_FINANCE: dict = {
    "currency": "EUR", "currency_year": 2020, "price_basis": "nominal",
    "financial_close": "2026-01-01",
    "cod_by_asset": {a: "2027-01-01" for a in DC_EXISTING},
    "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
    "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
    # Every owned generator and battery needs an entry (the engine never reads
    # a missing one as 0): PV 0.5 %/yr, the gensets and batteries none (the
    # batteries on the no-degradation basis, as the guided case).
    "degradation_by_asset": {
        **{a: 0.0 for a in DC_EXISTING if a not in ("rooftop_pv", "site_transformer")},
        "rooftop_pv": 0.005,
        **{f"{k}_new_{y}": 0.0 for y, _it, _pue in DC_PHASES for k in ("genset", "bess")}},
    "analysis_years": sum(DC_PERIOD_YEARS.values()),            # 2027-2044
    "annualise": False,                                         # each period weighs 8,760 h
    "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
    "replacement_rule": "part_lifetimes",
    "terminal_value": {"method": "remaining_life_annuity"},
    "wacc_nominal": 0.07, "cost_of_equity": 0.07, "inflation": 0.02,
}


def _staged_datacenter(n: pypsa.Network, phases, load_spec=None) -> pypsa.Network:
    """The single-year campus as a three-period plan: the week repeated in each
    period, the load scaled to ``phases``, the import capped per period by
    ``DC_ENERGIZATION_MW`` (an absolute MW schedule on the ``_DC_POC_MW`` link), and the two expansion
    candidates replaced by one candidate per period with that period as its
    ``build_year`` (explicit rows, not vintage bounds, so the finance adapter
    sees each stage)."""
    years = [int(y) for y, _it, _pue in phases]
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
    n.investment_period_weightings["years"] = [float(DC_PERIOD_YEARS[y]) for y in years]
    n.investment_period_weightings["objective"] = [float(DC_PERIOD_YEARS[y]) for y in years]
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
        {"grid_import": np.repeat([DC_ENERGIZATION_MW[y] for y in years], len(SNAPSHOTS))},
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


def build_eh_datacenter_staged(load_spec=None) -> pypsa.Network:
    return build_eh_datacenter(phases=DC_PHASES, load_spec=load_spec)


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


def dc_load_preset(template_id: str) -> dict:
    """The opt-in spec's defaults for ``template_id`` (all assumed): today's
    phases, PUE 1.3 at or below 20 °C plus 0.02 per K above, 75 % critical."""
    phases = ([{"cod": f"{y}-01-01", "it_mw": it} for y, it, _pue in DC_PHASES]
              if template_id == "eh_datacenter_staged"
              else [{"cod": "2030-01-01", "it_mw": _DC_IT_MW}])
    return {"phases": phases, "utilisation": "ai_training", "pue_at_design": 1.3,
            "pue_temperature_slope_per_k": 0.02, "redundancy": "N+1",
            "critical_share": 0.75, "curtailable_share": 0.0}


def _single_year_phase(spec):
    week = SNAPSHOTS[0].date()
    live = [p for p in spec.phases if p.cod <= week]
    return live[-1] if live else None


def _apply_dc_profile(n, spec, snapshots, it_mw: float, *, timezone, period=None) -> None:
    from services.dc_load_profile import hourly_profile

    facility = hourly_profile(spec, snapshots, it_mw, timezone=timezone)
    p_set = n.loads_t.p_set
    rows = slice(None) if period is None else period
    p_set.loc[rows, "it_load"] = spec.critical_share * facility
    p_set.loc[rows, "cooling"] = (1.0 - spec.critical_share) * facility


def dc_load_spec(template_id: str, raw: dict | None):
    """The checked opt-in spec from a create body's ``dc_load`` (merged over
    ``dc_load_preset``), or ``DcLoadError`` naming the field."""
    import pydantic

    from models.flex_archetypes import DataCentreLoadSpec
    from services.dc_load_profile import DcLoadError

    if not isinstance(raw, dict):
        raise DcLoadError("dc_load: must be an object", "dc_load")
    merged = {**dc_load_preset(template_id), **raw}
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
        expected = [y for y, _it, _pue in DC_PHASES]
        got = [p.cod.year for p in spec.phases]
        if got != expected:
            raise DcLoadError(f"dc_load.phases: the staged template's periods are {expected}; "
                              f"one phase per period with that COD year is needed, got {got}",
                              "dc_load.phases")
    elif _single_year_phase(spec) is None:
        raise DcLoadError(f"dc_load.phases: no phase is in service by the modelled week "
                          f"({SNAPSHOTS[0].date()})", "dc_load.phases")
    _check_dc_peak(template_id, spec)
    return spec


def firm_genset_mw(n, template_id: str) -> tuple[float, str]:
    """The committed genset fleet's firm MW and the rule that gave it: the
    island requirements' genset N+k (``genset_redundancy_n``) takes out the
    k largest units; without it the whole fleet counts
    (``services.adequacy.site_peak.firm_genset_mw`` on the template's sidecar)."""
    from services.adequacy.site_peak import firm_genset_mw as firm

    return firm(n, ISLAND_CONFIG.get(template_id, {"units": {}}))


def _check_dc_peak(template_id: str, spec) -> None:
    """Each period's modelled peak must fit the energized import (after the
    PoC Link's losses) plus the committed fleet's firm genset MW; above that
    the plan could only shed (gatekeeper review of DC grid readiness 01).
    The one check the grid-readiness scorecard also reports
    (``services.adequacy.site_peak.peak_check``)."""
    from services.adequacy.site_peak import describe, peak_check
    from services.dc_load_profile import DcLoadError

    n = BUILDERS[template_id](load_spec=spec)
    for row in peak_check(n, ISLAND_CONFIG.get(template_id, {"units": {}})):
        if row["headroom_mw"] < -1e-9:
            raise DcLoadError(
                f"dc_load.phases: the {row['period']} peak is {row['peak_mw']:.1f} MW, above "
                f"{describe(row)}", "dc_load.phases")


def dc_load_meta(template_id: str, spec) -> dict:
    """The ``dc_load`` block a project created with the spec carries in its
    ``eh_template.json`` (spec, dynamic envelope, ledger)."""
    from services.dc_load_profile import meta_block

    return meta_block(spec, _dc_timezone(template_id))


# ── Industrial Hydrogen Hub (strong_grid) ──────────────────────────────────


def build_eh_h2_hub() -> pypsa.Network:
    """Grid-connected industrial site with wind, PV and an H₂ chain.

    Electrolyser → H₂ store → fuel cell carry the ``eh_role`` conversion
    roles (redundancy scenarios, multi-energy report); the process load is
    the critical demand and an H₂ offtake is the second energy carrier.
    """
    n = _base("Industrial Hydrogen Hub")
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
        {"grid_supply": price}, index=SNAPSHOTS)
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
         "solar_park": sun, "solar_new": sun}, index=SNAPSHOTS)

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
        {"process_load": 40.0 + rng.normal(0.0, 1.5, HOURS)}, index=SNAPSHOTS)

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
MG_FINANCE: dict = {
    "currency": "EUR", "currency_year": 2020, "price_basis": "nominal",
    "financial_close": f"{MG_FIRST_YEAR - 1}-01-01",
    "cod_by_asset": {a: f"{MG_FIRST_YEAR}-01-01" for a in (*MG_EXISTING, *MG_CANDIDATES)},
    "capex_phasing": [1.0], "stage_capex_lead_years": 1, "contingency_share": 0.0,
    "escalation": {c: 0.02 for c in ("opex", "fuel", "tariff", "ppa", "export", "capex")},
    # Every owned generator and battery: PV 0.5 %/yr, the rest none.
    "degradation_by_asset": {
        **{a: (0.005 if a == "pv_plant" else 0.0) for a in MG_EXISTING if a != "hospital_feeder"},
        **{a: 0.0 for a in MG_CANDIDATES}},
    "analysis_years": MG_HORIZON_END - MG_FIRST_YEAR,             # 2030-2044
    "annualise": False,                                           # the week weighs 8,760 h
    "tax_losses": "offset_other_income", "financing_fee_tax": "not_deducted",
    "replacement_rule": "part_lifetimes",
    "terminal_value": {"method": "remaining_life_annuity"},
    "wacc_nominal": 0.07, "cost_of_equity": 0.07, "inflation": 0.02,
}


def build_eh_microgrid() -> pypsa.Network:
    """A small island system that must run without its mainland tie.

    The ``off_grid`` pack islands the tagged tie Link; PV, wind, a battery
    and a diesel fleet (with outage data) must carry a critical hospital
    feeder and residential demand. The hospital sits behind a feeder Link
    (η = 0.98 ≥ 1/1.05, so the P16 per-Load priority stays exact).
    """
    n = _base("Island Microgrid")
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
        index=SNAPSHOTS)
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
    }, index=SNAPSHOTS)

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
    "eh_datacenter_staged": {
        "id": "eh_datacenter_staged",
        "name": "Data Center Energy Hub (staged growth)",
        "description": (
            "The data center campus planned from a projected load: IT grows "
            "12 → 24 → 36 MW over 2027, 2030 and 2035 while the utility "
            f"energizes a {_DC_POC_MW:g} MW connection in steps "
            f"({DC_ENERGIZATION_MW[2027]:g} MW until 2030). "
            "Genset and battery candidates per period, so the plan says what "
            "to build when. Multi-period; tagged for an Energy Hub study."),
        "recommended_archetype": "weak_flexible",
        "pack_overrides": {"import_p_nom_mw": _DC_POC_MW},
        "stages": None,
        "dtc_attribution": "per_load",
        "projected_load": [
            {"year": y, "it_mw": it, "pue": pue,
             "note": "synthetic illustrative fit-out ramp; replace with the client's load projection"}
            for y, it, pue in DC_PHASES],
        "energization": [
            {"year": y, "available_import_mw": DC_ENERGIZATION_MW[y], "connection_mw": _DC_POC_MW,
             "source": "assumed", "note": "utility energization schedule (assumed)"}
            for y, _it, _pue in DC_PHASES],
        "period_years": {str(y): DC_PERIOD_YEARS[y] for y, _it, _pue in DC_PHASES},
        "ledger": [
            {"item": "discount rate", "value": "7 % nominal (the solver settings' discount_rate)",
             "source": "assumed",
             "note": "the solve discounts each period's costs at it (auto_discount_periods)"},
            {"item": "PUE", "value": "1.3 design figure",
             "source": "assumed",
             "note": "metadata: cooling is the single-year template's profile scaled with the IT "
                     "load, so the modelled peak-to-IT ratio is about 1.46 in 2035"},
            {"item": "battery inverter replacement", "value": "after 10 years (inv_power_lifetime)",
             "source": "assumed",
             "note": "bess_new_2027's inverter ends in 2037 and bess_new_2030's in 2040, inside the "
                     "2045 horizon; the template's finance inputs re-buy it at the end of its "
                     "lifetime (replacement_rule part_lifetimes)"},
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
                     "replace with the site's tariff"},
            {"item": "tariff clock", "value": "UTC",
             "source": "assumed",
             "note": "the representative week's hours are the site clock; stating them as UTC "
                     "reads each tariff window on the snapshot hour as is"},
            {"item": "existing campus", "value": "gensets 1-4, rooftop_pv, ups_battery, "
                                                 "site_transformer: COD 2027-01-01, overnight cost 0",
             "source": "assumed",
             "note": "in service at the first period and sunk: no capex in the case; only the "
                     "candidates the plan builds are investments, each a stage at its period"},
            {"item": "money year", "value": "pack prices (2020 price data) are read as real base-year EUR",
               "source": "assumed",
               "note": "held flat to the base year, then escalated from it at the case's rates; "
                       "nothing is converted with a price index"},
            {"item": "finance inputs", "value": "nominal EUR, escalation "
                                                "2 %/yr, inflation 2 %, all equity at 7 %, "
                                                "close 2026-01-01, 18 years to 2045",
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
            f"The energization schedule caps the import at {DC_ENERGIZATION_MW[2027]:g} MW in "
            "2027, below that period's peak: the plan builds on site for it.",
            "Load is picked up in 2.5 MW blocks after an islanding (assumed): the island rows then "
            "follow the load, so the plan stages — a small genset in 2027 and 2030, the bulk in 2035.",
            "After a solve the investment case runs on the shipped commercial config and finance "
            "inputs: the existing campus is in service in 2027 at no capex, each candidate the plan "
            "builds is a stage with its own COD, and an unbuilt later candidate is not an investment.",
            "PoC and transformer losses are not modelled in the staged plan (efficiency 1.0), so the "
            "investment case can compare against the grid-only site.",
            f"The {_DC_POC_MW:g} MW connection carries the 2035 peak. The investment case "
            "compares against the site as it stands today: with the plan's new builds removed, "
            f"the site still meets its load (the {DC_ENERGIZATION_MW[2027]:g} MW energized in 2027 "
            "included), and the island capability the new builds buy is shown as a break-even "
            "beside the NPV.",
        ],
        "provenance": PROVENANCE,
    },
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
    "eh_microgrid": {
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
            "candidates. The representative week stands for each of the 15 operating years "
            f"({MG_FIRST_YEAR}-{MG_HORIZON_END - 1}).",
        ],
        "ledger": [
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
                      f"{MG_HORIZON_END - MG_FIRST_YEAR} operating years "
                      f"{MG_FIRST_YEAR}-{MG_HORIZON_END - 1}, escalated and degraded",
             "source": "assumed",
             "note": f"the week is dated {MG_FIRST_YEAR}, the first operating year; the case "
                     f"ends in {MG_HORIZON_END}, the staged templates' horizon end. The week "
                     "already weighs a year, so nothing is annualised"},
            {"item": "dates", "value": f"every COD {MG_FIRST_YEAR}-01-01, financial close "
                                       f"{MG_FIRST_YEAR - 1}-01-01",
             "source": "assumed",
             "note": "a single period has no stage, so every asset shares one COD; close one year "
                     "before it and capex paid then, as the staged templates"},
            {"item": "existing island", "value": ", ".join(MG_EXISTING)
                                                 + ": overnight cost 0, lifetime unlimited",
             "source": "assumed",
             "note": "in service and sunk: no capex in the case; only the candidates the plan "
                     "builds are investments. The solve is unchanged"},
            {"item": "finance inputs",
             "value": f"the staged microgrid's: pack prices (2020 price data) taken as real "
                      f"{MG_FIRST_YEAR} EUR (the base year), escalation 2 %/yr from "
                      f"{MG_FIRST_YEAR}, inflation 2 %, all equity at 7 %, pre-tax; PV degrades "
                      "0.5 %/yr, the rest not",
             "source": "assumed",
             "note": f"the week's running costs and the pack's capex are held flat to "
                     f"{MG_FIRST_YEAR} (the first operating year) and escalate from there, the "
                     "staged microgrid's rule from its first period (2027); the terminal value "
                     "is the remaining-life annuity of each part"},
            {"item": "battery inverter replacement", "value": "after 10 years (inv_power_lifetime)",
             "source": "assumed",
             "note": "bess_new's inverter is re-bought in 2039 (replacement_rule part_lifetimes); "
                     "the cash then changes sign twice, so the IRR is flagged "
                     "irr_multiple_sign_changes. The NPV is unaffected"},
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
             "note": f"the template's marginal cost, in {MG_FIRST_YEAR} money, escalating "
                     "2 %/yr (the case books it as opex); replace with the island's fuel "
                     "contract"},
        ],
        "provenance": PROVENANCE,
    },
}

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


def _staged_commercial_and_finance() -> dict:
    """The staged template's `commercial` and `finance` blocks, stored as the
    routes store them (validated, dumped): the value flows are the
    `single_owner` template built on the template's network."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate(
        {"poc_link": "grid_import", "import_tariff": DC_TARIFF, "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_datacenter_staged(), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(DC_FINANCE).model_dump(mode="json")}


def _microgrid_commercial_and_finance() -> dict:
    """The single-year island's commercial config: the open subsea tie is the
    PoC (it carries nothing), no tariff, the single-owner value flows on the
    plain network. The case compares against the island as it stands today,
    which the investment-case route re-solves without the candidates."""
    from models.commercial import CommercialConfig
    from models.finance import FinanceInputs
    from services.commercial import value_flow_templates as VFT

    base = CommercialConfig.model_validate({"poc_link": "subsea_tie", "timezone": "UTC"})
    vf = VFT.build("single_owner", build_eh_microgrid(), base).config
    commercial = base.model_copy(update={"value_flows": vf.model_dump(mode="json")})
    return {"commercial": commercial.model_dump(mode="json"),
            "finance": FinanceInputs.model_validate(MG_FINANCE).model_dump(mode="json")}


#: Template blocks that need the backend's models (built on first use).
_SOLVER_CONFIG_BUILT = {"eh_datacenter_staged": _staged_commercial_and_finance,
                        "eh_microgrid": _microgrid_commercial_and_finance}
_BUILT_CACHE: dict[str, dict] = {}


def solver_config(template_id: str) -> dict:
    """The solver settings written beside one template."""
    import copy

    built = {}
    if template_id in _SOLVER_CONFIG_BUILT:
        if template_id not in _BUILT_CACHE:
            _BUILT_CACHE[template_id] = _SOLVER_CONFIG_BUILT[template_id]()
        built = copy.deepcopy(_BUILT_CACHE[template_id])
    return {**SOLVER_CONFIG, **SOLVER_CONFIG_EXTRA.get(template_id, {}), **built}

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


def _staged_island(cfg: dict) -> dict:
    """The single-year island sidecar with the expansion candidates renamed
    per period (``genset_new_<y>``, ``bess_new_<y>``)."""
    import copy

    out = copy.deepcopy(cfg)
    units = out["units"]
    gen, bess = units.pop("genset_new"), units.pop("bess_new")
    for y, _it, _pue in DC_PHASES:
        units[f"genset_new_{y}"] = copy.deepcopy(gen)
        units[f"bess_new_{y}"] = copy.deepcopy(bess)
    out["requirements"]["ride_through_storage"] = [f"bess_new_{y}" for y, _it, _pue in DC_PHASES]
    # The halls are fitted out in smaller steps: 2.5 MW pickup blocks (the
    # single-year campus's 5 MW blocks need 36.7 MW more genset inertia in
    # every period, whatever its load, so the plan could not stage).
    out["requirements"]["pickup_blocks"] = {"value": [2.5] * 22, "source": "assumed"}
    return out


ISLAND_CONFIG["eh_datacenter_staged"] = _staged_island(ISLAND_CONFIG["eh_datacenter"])


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


def write_sidecars(out_dir, template_id: str) -> None:
    """Write the template's metadata and stress registry beside network.nc,
    and its island sidecar when it has one."""
    import json
    import pathlib

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / META_FILE).write_text(
        json.dumps(TEMPLATE_META[template_id], indent=2, sort_keys=True) + "\n")
    (out_dir / STRESS_FILE).write_text(json.dumps(
        {"__schema__": SCHEMA, "scenarios": STRESS_SCENARIOS[template_id]},
        indent=2, sort_keys=True) + "\n")
    (out_dir / SOLVER_FILE).write_text(
        json.dumps(solver_config(template_id), indent=2, sort_keys=True) + "\n")
    if template_id in ISLAND_CONFIG:
        (out_dir / ISLAND_FILE).write_text(
            json.dumps(ISLAND_CONFIG[template_id], indent=2, sort_keys=True) + "\n")
