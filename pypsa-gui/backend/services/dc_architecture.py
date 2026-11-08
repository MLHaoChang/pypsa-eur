"""Power-architecture comparison for a data-centre project (DC grid readiness 05).

Four architectures carry the power from the MV bus to the rack: ``ac`` (LV
double-conversion UPS), ``mv_ups``, ``dc_800v`` (a central rectifier to an
800 VDC bus) and ``sst`` (a solid-state transformer, MV AC to 800 VDC). Each
is a chain of conversion stages from defaults pack ``ARCH_PACK_VERSION``:

* the stages up to the rack input are inside the PUE boundary (IT metered at
  the rack input, The Green Grid PUE category 3); their product is
  ``eta_to_rack``;
* the ``rack`` stage (server PSU, or the 800 VDC to 54 VDC shelf) is beyond it
  and changes the IT energy, not the PUE.

``pue_at_design`` is the PUE of the project's OWN architecture (GK A Q-A1), so
no create moves a load. A variant v reads (GK A 15)::

    Facility_v = IT_v · (PUE(T) − 1/η_own + 1/η_v),   IT_v = IT_own · η_rack(own)/η_rack(v)

with IT_own · u = Facility_own / PUE(T) from the saved network's loads.
Assumed second-order effects (GK A C4): the cooling of a variant's changed
loss heat is not shifted, and each stage's efficiency is flat over part load.

``compare`` is a screen on the saved network: no solve, nothing written. Its
cost legs reuse DC-03's NPV machinery (``dc_backup``; GK B K6) and value the
facility energy at the tariff's energy component or a request price (K5).
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import Any

import numpy as np

from models.flex_archetypes import DataCentreLoadSpec

#: The pack the chains and capex rows come from; pinned, never the moving default.
ARCH_PACK_VERSION = "2026-11-07"
ARCHITECTURES: tuple[str, ...] = ("ac", "mv_ups", "dc_800v", "sst")
TECHNOLOGY = {
    "ac": "dc-arch-ac",
    "mv_ups": "dc-arch-mv-ups",
    "dc_800v": "dc-arch-dc-800v",
    "sst": "dc-arch-sst",
}
LABEL = {
    "ac": "AC (LV UPS)",
    "mv_ups": "MV UPS",
    "dc_800v": "800 VDC",
    "sst": "Solid-state transformer",
}
RACK_STAGE = "rack"
BOUNDARY = (
    "IT is metered at the rack input (The Green Grid PUE category 3): a PUE measured at "
    "the UPS output would put the distribution stages inside IT"
)
SECOND_ORDER = (
    "assumed: the cooling of a variant's changed loss heat is not shifted, and each "
    "stage's efficiency is flat over part load"
)
#: The energy weighting the MWh read (GK A C5): the load's own weighting.
WEIGHTING = "generators"

#: Which inputs the low and high cost ends range (GK B K4).
RANGE_NOTE = (
    "the low and high ends vary the overnight capex and the other architectures' stage "
    "efficiencies; lifetime and fixed O&M stay at their central values, and this "
    "project's own chain stays central because its PUE is stated"
)

#: What each architecture CAN offer the grid, in gridspine's SERVICES words:
#: ``capability`` counts (as assumed); ``needs_storage`` counts only with
#: storage declared on the DC bus (GK A C6), otherwise it reads conditional.
ARCH_SERVICES: dict[str, dict[str, str]] = {
    "ac": {},
    "mv_ups": {
        "voltage_control": "capability",
        "fast_frequency_response": "capability",
        "frequency_containment": "capability",
    },
    "dc_800v": {
        "voltage_control": "capability",
        "fast_frequency_response": "needs_storage",
    },
    "sst": {
        "voltage_control": "capability",
        "fast_frequency_response": "needs_storage",
        "frequency_containment": "needs_storage",
        "grid_forming": "needs_storage",
        "synthetic_inertia": "needs_storage",
    },
}
SERVICE_WHY = {
    "ac": "the LV UPS rectifiers are unidirectional power-factor-corrected front ends",
    "mv_ups": "a bidirectional MV inverter with its own UPS storage",
    "dc_800v": "an active-front-end rectifier; frequency response needs storage on the DC bus",
    "sst": "a fully controllable MV converter; active-power services need storage on the DC bus",
}
#: The one RMS caveat per surface (GK A C6).
RMS_CAVEAT = (
    "An architecture's grid services are an assumed capability: an RMS study (EMT where "
    "the code asks) must show them before they count"
)


class DcArchitectureError(ValueError):
    """A refusal with its own error kind (``tool-error-kinds.d``) and field."""

    def __init__(self, message: str, kind: str, field: str):
        super().__init__(message)
        self.kind, self.field = kind, field


#: Each refusal's error kind, written as ``error_kind`` literals so the
#: manifest guard (``tests/test_tool_error_kind_manifest.py``) sees them; an
#: undeclared kind is a KeyError.
_KINDS = {
    "invalid": {"error_kind": "dc_architecture_invalid"},
    "no_spec": {"error_kind": "dc_architecture_no_spec"},
    "pue_infeasible": {"error_kind": "dc_architecture_pue_infeasible"},
    "pack_row": {"error_kind": "dc_architecture_pack_row"},
    "no_energy_price": {"error_kind": "dc_architecture_no_energy_price"},
    "currency_mismatch": {"error_kind": "dc_architecture_currency_mismatch"},
}


def _err(message: str, kind: str, field: str) -> DcArchitectureError:
    return DcArchitectureError(message, _KINDS[kind]["error_kind"], field)


# ── the chains ────────────────────────────────────────────────────────────


def _pack(version: str = ARCH_PACK_VERSION):
    from services.library.defaults_pack.loader import load_defaults_pack

    return load_defaults_pack(version)


def chain(architecture: str, pack=None, *, pick: str = "value") -> dict[str, Any]:
    """The architecture's stages and efficiencies. ``pick`` reads each stage's
    ``value`` or its range end (``low``/``high``). A missing stage row is
    refused (``dc_architecture_pack_row``)."""
    if architecture not in TECHNOLOGY:
        raise _err(
            f"Architecture: {architecture} is not one of {', '.join(ARCHITECTURES)}",
            "invalid",
            "architecture",
        )
    pack = pack or _pack()
    tech = TECHNOLOGY[architecture]
    prefix = f"{tech}.stage."
    stages = []
    for r in pack.cost_values:
        if not r.key.startswith(prefix) or r.parameter != "efficiency":
            continue
        val = (
            r.value
            if pick == "value"
            else (getattr(r.range, pick, None) if r.range else None)
        )
        if val is None or not math.isfinite(val) or not 0 < val <= 1:
            raise _err(
                f"Defaults pack {pack.version}: {r.key} has no usable {pick}",
                "pack_row",
                "defaults_pack",
            )
        name = r.label.split(": ", 1)[-1].removesuffix(" efficiency")
        stages.append(
            {
                "stage": r.key[len(prefix) :].rsplit(".", 1)[0],
                "label": name,
                "efficiency": float(val),
                "range": None
                if r.range is None
                else [float(r.range.low), float(r.range.high)],
                "source": r.source,
                "key": r.key,
            }
        )
    rack = [s for s in stages if s["stage"] == RACK_STAGE]
    to_rack = [s for s in stages if s["stage"] != RACK_STAGE]
    if len(rack) != 1 or not to_rack:
        raise _err(
            f"Defaults pack {pack.version}: {tech} needs stage rows up to the rack and "
            "one rack stage",
            "pack_row",
            "defaults_pack",
        )
    return {
        "architecture": architecture,
        "technology": tech,
        "label": LABEL[architecture],
        "stages": stages,
        "eta_to_rack": float(np.prod([s["efficiency"] for s in to_rack])),
        "eta_rack": rack[0]["efficiency"],
    }


def is_set(spec_raw: dict | None) -> bool:
    """Whether the stored spec names an architecture (unset reads "ac", assumed)."""
    return isinstance(spec_raw, dict) and "architecture" in spec_raw


def dc_bus_storage(spec: DataCentreLoadSpec) -> bool:
    """Storage on the DC bus is declared when the backup has battery strings
    (DC-03 ``bess``/``hybrid``): under a DC architecture they sit on that bus
    (assumed)."""
    return spec.backup.mode != "genset"


def services(architecture: str, storage: bool) -> dict[str, list[str]]:
    """``{"counted": [...], "conditional": [...]}`` in gridspine's SERVICES
    order: the services the architecture can offer, and those that wait for
    DC-bus storage (never counted without it)."""
    from services.adequacy.island_config import SERVICES

    offer = ARCH_SERVICES[architecture]
    unknown = set(offer) - set(SERVICES)
    if SERVICES and unknown:
        raise _err(
            f"Architecture services {', '.join(sorted(unknown))} are not gridspine services",
            "invalid",
            "architecture",
        )
    order = list(SERVICES) or list(offer)
    counted = [
        s
        for s in order
        if offer.get(s) == "capability" or (offer.get(s) == "needs_storage" and storage)
    ]
    conditional = [s for s in order if offer.get(s) == "needs_storage" and not storage]
    return {"counted": counted, "conditional": conditional}


def ledger_rows(spec: DataCentreLoadSpec) -> list[tuple[str, str, str]]:
    """The ledger lines an explicitly set architecture carries in place of the
    "UPS and distribution losses" line (GK A C1, C2)."""
    c = chain(spec.architecture)
    stages = ", ".join(
        f"{s['label']} {s['efficiency']:g}"
        for s in c["stages"]
        if s["stage"] != RACK_STAGE
    )
    rack = next(s for s in c["stages"] if s["stage"] == RACK_STAGE)
    return [
        (
            "power architecture",
            f"{c['label']}: {stages}; {c['eta_to_rack']:.4f} to the rack, inside PUE; "
            f"{rack['label']} {rack['efficiency']:g} beyond it",
            "architecture",
        ),
        ("PUE boundary", BOUNDARY, "architecture"),
    ]


def check_pue(pue_series, eta_own: float, field: str = "pue_at_design") -> None:
    """GK A C3: PUE(T) = 1/η_own + overhead, so the overhead PUE(T) − 1/η_own
    below 0 at any snapshot means the stated PUE cannot hold the own chain plus
    a non-negative overhead: refused, never clipped."""
    pue_min = float(np.min(np.asarray(pue_series, dtype=float)))
    if pue_min - 1.0 / eta_own < -1e-12:
        raise _err(
            f"PUE: a PUE of {pue_min:g} cannot include the architecture's own "
            f"conversion losses (1/{eta_own:.4f} = {1.0 / eta_own:.4f}) plus a "
            "non-negative overhead",
            "pue_infeasible",
            field,
        )


def variant_facility(facility_own, pue_series, own: dict, var: dict) -> np.ndarray:
    """Facility MW of variant ``var`` on each snapshot from the own facility
    load (GK A 15). The own variant returns the own load unchanged."""
    p = np.asarray(facility_own, dtype=float)
    if own is var or (
        own["architecture"] == var["architecture"]
        and own["eta_to_rack"] == var["eta_to_rack"]
        and own["eta_rack"] == var["eta_rack"]
    ):
        return p.copy()
    pue = np.asarray(pue_series, dtype=float)
    it_own = p / pue
    it_v = it_own * own["eta_rack"] / var["eta_rack"]
    return it_v * (pue - 1.0 / own["eta_to_rack"] + 1.0 / var["eta_to_rack"])


def pue_series(spec: DataCentreLoadSpec, snapshots, timezone: str | None) -> np.ndarray:
    """PUE(T) on the network's snapshots (the timestep level of a multi-period index)."""
    import pandas as pd

    from services.dc_load_profile import ambient_c, pue

    idx = (
        snapshots.get_level_values(-1)
        if isinstance(snapshots, pd.MultiIndex)
        else snapshots
    )
    return np.asarray(
        pue(spec, ambient_c(spec, pd.DatetimeIndex(idx), timezone)), dtype=float
    )


# ── energy prices (GK B K5) ───────────────────────────────────────────────

NOT_VALUED = (
    "demand, capacity, fixed and connection charges and tax or levy items are not "
    "valued: only the tariff's energy component prices the facility energy"
)


def tariff_energy_rate(
    commercial: dict | None, snapshots, finance_currency: str
) -> dict | None:
    """The tariff's energy component in currency/MWh on each snapshot, or None
    when no tariff can be read. Energy items only (``kind`` energy, per kWh, on
    import, a cost, untiered). A tariff currency (stated, else its defaults-pack
    stamp) other than the finance currency is refused (GS-01 gate)."""
    import pandas as pd

    from models.commercial import CommercialConfig
    from services.commercial.tariff_engine import period_rates

    if not commercial:
        return None
    try:
        cfg = CommercialConfig.model_validate(commercial)
    except ValueError:
        return None
    tariff = cfg.import_tariff
    if tariff is None:
        return None
    from services.commercial.binding import (
        resolved_tariff_currency,
        tariff_currency_mismatch,
    )

    _, cur_source = resolved_tariff_currency(tariff)
    why = tariff_currency_mismatch(tariff, finance_currency)
    if why:
        raise _err(f"Energy price: {why}", "currency_mismatch", "energy_price_per_mwh")
    items = [i for i in tariff.items if i.kind == "energy"]
    if not items or any(
        i.unit != "per_kwh"
        or i.tiers
        or i.measured_on != "import"
        or i.direction != "cost"
        for i in items
    ):
        return None
    idx = (
        snapshots.get_level_values(-1)
        if isinstance(snapshots, pd.MultiIndex)
        else snapshots
    )
    local = pd.DatetimeIndex(idx)
    if cfg.timezone:
        local = local.tz_localize("UTC").tz_convert(cfg.timezone)
    rate = np.zeros(len(local))
    covered = np.zeros(len(local), dtype=bool)
    for i in items:
        r = np.asarray(period_rates(i.periods, local), dtype=float)
        covered |= ~np.isnan(r)
        rate += np.nan_to_num(r, nan=0.0)
    return {
        "rate": rate * 1000.0,
        # An hour no energy item rates is unpriced, never priced at 0 (GK B K5).
        "covered": covered,
        "source": f"tariff {tariff.id}: energy items "
        f"{', '.join(i.id for i in items)} (per kWh × 1000)",
        "currency": finance_currency,  # the gate passed: resolved == finance
        "currency_note": {
            "stated": None,
            "stamp": "the tariff states no currency: read from its defaults-pack stamp",
        }.get(
            cur_source,
            "the tariff states no currency: read as the finance currency (assumed)",
        ),
    }


# ── cost legs (GK B K4–K6) ────────────────────────────────────────────────


def capex_rows(architecture: str, pack=None) -> dict[str, Any]:
    """Overnight per MW of IT (value, low, high), lifetime, FOM share, money
    year, currency. Fails closed on a missing or blank row, a non-real row or
    mixed money years (GK B K6)."""
    pack = pack or _pack()
    tech = TECHNOLOGY[architecture]
    rows = {r.key: r for r in pack.cost_values}
    out: dict[str, Any] = {}
    for p in ("overnight", "lifetime", "fom_share"):
        key = f"{tech}.investment.{p}"
        r = rows.get(key)
        if r is None or r.value is None or not math.isfinite(r.value):
            raise _err(
                f"Defaults pack {pack.version} has no value for {key}",
                "pack_row",
                "defaults_pack",
            )
        if r.price_basis != "real":
            raise _err(
                f"Defaults pack {pack.version}: {key} is not real money",
                "pack_row",
                "defaults_pack",
            )
        out[p] = float(r.value)
        if p == "overnight":
            if r.range is None:
                raise _err(
                    f"Defaults pack {pack.version}: {key} has no range",
                    "pack_row",
                    "defaults_pack",
                )
            out["overnight_low"], out["overnight_high"] = (
                float(r.range.low),
                float(r.range.high),
            )
            out["currency"], out["money_year"] = r.currency, r.currency_year
    return out


def _check_money_years(legs: dict[str, dict]) -> tuple[str, int]:
    years = {c["money_year"] for c in legs.values()}
    curs = {c["currency"] for c in legs.values()}
    if len(years) != 1 or len(curs) != 1:
        raise _err(
            f"The architecture capex rows are in different money years "
            f"({', '.join(map(str, sorted(years)))}) or currencies ({', '.join(sorted(curs))})",
            "pack_row",
            "defaults_pack",
        )
    return next(iter(curs)), next(iter(years))


def leg_npv(
    *,
    overnight: float,
    fom: float,
    life: float,
    it_mw: float,
    energy_cost: float,
    years: int,
    r: float,
) -> dict[str, float]:
    """One architecture's NPV over ``years`` at the real rate ``r``: capex on
    ``it_mw`` in year 0, repurchased at its lifetime with the remaining-life
    credit at the horizon end (``dc_backup._part``), FOM, and the facility
    energy cost each year 1..years (constant in real terms)."""
    from services.adequacy.dc_backup import _part
    from services.finance.metrics import npv

    part = _part(overnight * it_mw, fom, life, years, r)
    energy = np.r_[0.0, [energy_cost] * years]
    capex = part["capex"] + part["residual"]
    return {
        "capex": npv(r, capex),
        "fom": npv(r, part["fom"]),
        "energy": npv(r, energy),
        "total": npv(r, capex + part["fom"] + energy),
    }


def delta_interval(c: dict, o: dict) -> dict[str, Any]:
    """Candidate ``c`` against ``o`` at the central values and the range ends
    (K4): low = c at its optimistic end against o at its pessimistic one."""
    central = c["npv"]["value"]["total"] - o["npv"]["value"]["total"]
    low = c["npv"]["low"]["total"] - o["npv"]["high"]["total"]
    high = c["npv"]["high"]["total"] - o["npv"]["low"]["total"]
    verdict = (
        "not_distinguishable"
        if low <= 0.0 <= high
        else "cheaper"
        if high < 0.0
        else "dearer"
    )
    return {"central": central, "low": low, "high": high, "verdict": verdict}


def summary(variants: list[dict], storage: bool = False) -> dict[str, Any]:
    """The "Cheapest over the horizon · most grid services" line. A cheapest
    architecture is named only when it is distinguishably cheaper than every
    other one on the ranges (K4)."""
    best = min(variants, key=lambda v: v["npv"]["value"]["total"])
    clear = all(
        delta_interval(best, v)["verdict"] == "cheaper"
        for v in variants
        if v is not best
    )
    most = max(variants, key=lambda v: len(v["services"]["counted"]))
    ties = [
        v
        for v in variants
        if len(v["services"]["counted"]) == len(most["services"]["counted"])
    ]
    return {
        "cheapest": best["architecture"] if clear else None,
        "cheapest_text": (
            f"Cheapest over the horizon: {best['label']}"
            if clear
            else "Cheapest over the horizon: not distinguishable on the assumed costs"
        ),
        "most_services": [v["architecture"] for v in ties],
        # What it counts: the services an architecture offers as the project is
        # backed; those waiting for DC-bus storage count only with that storage.
        "most_services_text": (
            "Most grid services with the declared DC-bus storage: "
            if storage
            else "Most grid services without added storage: "
        )
        + ", ".join(v["label"] for v in ties),
    }


# ── the screen ────────────────────────────────────────────────────────────


def _weighted(w: np.ndarray, x: np.ndarray) -> float:
    return float(np.sum(w * x))


def compare(
    n,
    spec: DataCentreLoadSpec,
    *,
    own_set: bool,
    timezone: str | None,
    years: int,
    it_mw: float,
    island: dict | None,
    unset_periods=(),
    commercial: dict | None = None,
    energy_price_per_mwh: float | None = None,
    rate_kwargs: dict | None = None,
    island_offered: list[str] | None = None,
    finance_currency: str | None = None,
) -> dict[str, Any]:
    """Every architecture against the project's own on the saved network ``n``
    (read, never changed). See the module docstring for the algebra."""
    from services.adequacy.dc_backup import (
        DcBackupError,
        _check_years,
        real_rate,
    )
    from services.adequacy.grid_readiness import service_verdict
    from services.adequacy.site_peak import peak_check

    try:
        _check_years(years)
    except DcBackupError as exc:
        raise _err(str(exc), "invalid", "planning") from None
    for name in ("it_load", "cooling"):
        if name not in n.loads_t.p_set.columns:
            raise _err(
                f"Network: the saved network has no {name} load series",
                "invalid",
                "network",
            )
    pack = _pack()
    own_arch = spec.architecture
    snaps = n.snapshots
    w = n.snapshot_weightings[WEIGHTING].reindex(snaps).to_numpy(dtype=float)
    # A multi-period network weights each period to a full year: the yearly
    # figures and the energy cost are the final period's (the full build-out,
    # as the capex on the final IT MW), never the sum over the periods.
    in_basis = np.ones(len(snaps), dtype=bool)
    energy_basis = "the modelled year"
    if snaps.nlevels > 1:
        last = snaps.get_level_values(0).max()
        in_basis = np.asarray(snaps.get_level_values(0) == last)
        w = np.where(in_basis, w, 0.0)
        energy_basis = f"the final period, {last} (full build-out)"
    facility_own = (
        (n.loads_t.p_set["it_load"] + n.loads_t.p_set["cooling"])
        .reindex(snaps)
        .to_numpy(dtype=float)
    )
    pue_t = pue_series(spec, snaps, timezone)
    chains = {
        pick: {a: chain(a, pack, pick=pick) for a in ARCHITECTURES}
        for pick in ("value", "low", "high")
    }
    own = chains["value"][own_arch]
    check_pue(pue_t, own["eta_to_rack"])

    # Energy price (K5): in the project's finance-case currency, else the pack's.
    finance_currency = finance_currency or pack.finance.currency
    if energy_price_per_mwh is not None:
        if not math.isfinite(energy_price_per_mwh) or energy_price_per_mwh < 0:
            raise _err(
                "Energy price: enter a price of 0 or more per MWh",
                "invalid",
                "energy_price_per_mwh",
            )
        rate_t = np.full(len(snaps), float(energy_price_per_mwh))
        price = {
            "source": "request (assumed)",
            "assumed": True,
            "weighting": "flat",
            "currency": finance_currency,
        }
    else:
        tr = tariff_energy_rate(commercial, snaps, finance_currency)
        if tr is None:
            raise _err(
                "Energy price: the project's tariff has no energy rate. Enter a price "
                "per MWh above.",
                "no_energy_price",
                "energy_price_per_mwh",
            )
        uncovered = int(np.sum(~tr["covered"] & in_basis))
        if uncovered:
            raise _err(
                f"Energy price: the project's tariff has no energy rate for {uncovered} "
                f"of the {int(np.sum(in_basis))} modelled hours. Enter a price per MWh "
                "above.",
                "no_energy_price",
                "energy_price_per_mwh",
            )
        rate_t = tr["rate"]
        price = {
            "source": tr["source"],
            "assumed": False,
            "currency": tr["currency"],
            "currency_note": tr["currency_note"],
        }

    # Discount rate: DC-03's rule (finance case made real, else the pack's).
    rk = rate_kwargs or {"rate": None}
    if rk.get("rate") is None:
        r, rate_source = (
            float(pack.finance.discount_rate.value),
            "pack discount rate (real, assumed)"
            + (f": {rk['rate_note']}" if rk.get("rate_note") else ""),
        )
    elif rk.get("rate_is_real"):
        r, rate_source = (
            float(rk["rate"]),
            f"finance case wacc {rk['rate']:g} (real price basis)",
        )
    else:
        r = real_rate(float(rk["rate"]), rk.get("inflation"))
        rate_source = (
            f"finance case nominal wacc {rk['rate']:g} made real with its inflation "
            f"{rk.get('inflation') or 0.0:g}"
        )
    if not math.isfinite(r) or r <= -1.0:
        raise _err(
            f"Discount rate: the real rate {r:g} is not usable",
            "invalid",
            "discount_rate",
        )

    capex = {a: capex_rows(a, pack) for a in ARCHITECTURES}
    currency, money_year = _check_money_years(capex)
    if price["currency"] != currency:
        raise _err(
            f"Energy price: the price is in {price['currency']}, the architecture costs in "
            f"{currency}",
            "currency_mismatch",
            "energy_price_per_mwh",
        )
    storage = dc_bus_storage(spec)
    variants = []
    for a in ARCHITECTURES:
        legs, facility = {}, None
        for pick in ("value", "low", "high"):
            # The low-cost end pairs low capex with high efficiency, and the high-cost end the
            # reverse; every leg starts from the saved (own, central) load.
            eff_pick = {"value": "value", "low": "high", "high": "low"}[pick]
            # The own load is the saved one at the stated PUE (Q-A1): only the
            # variants move with the efficiency ends.
            fac = (
                facility_own
                if a == own_arch
                else variant_facility(facility_own, pue_t, own, chains[eff_pick][a])
            )
            ov = capex[a]["overnight" if pick == "value" else f"overnight_{pick}"]
            legs[pick] = leg_npv(
                overnight=ov,
                fom=capex[a]["fom_share"],
                life=capex[a]["lifetime"],
                it_mw=it_mw,
                energy_cost=_weighted(w, fac * rate_t),
                years=years,
                r=r,
            )
            if pick == "value":
                facility = fac
        it_v = (facility_own / pue_t) * own["eta_rack"] / chains["value"][a]["eta_rack"]
        it_mwh = _weighted(w, it_v)
        energy = _weighted(w, facility)
        # Conversion losses: MV bus to rack input plus the in-rack stage.
        loss = _weighted(
            w,
            it_v / chains["value"][a]["eta_to_rack"]
            - it_v * chains["value"][a]["eta_rack"],
        )
        c = chains["value"][a]
        svc = services(a, storage)
        variant = {
            "architecture": a,
            "label": LABEL[a],
            "own": a == own_arch,
            "eta_to_rack": c["eta_to_rack"],
            "eta_rack": c["eta_rack"],
            "stages": c["stages"],
            "effective_pue": energy / it_mwh if it_mwh > 0 else None,
            "facility_mwh": energy,
            "it_mwh": it_mwh,
            "loss_mwh": loss,
            "peak_mw": float(np.max(facility)) if len(facility) else None,
            "capex": {
                "overnight_per_mw": capex[a]["overnight"],
                "range_per_mw": [capex[a]["overnight_low"], capex[a]["overnight_high"]],
                "total": capex[a]["overnight"] * it_mw,
                "lifetime": capex[a]["lifetime"],
                "fom_share": capex[a]["fom_share"],
            },
            "npv": legs,
            "services": svc,
            "services_why": SERVICE_WHY[a],
        }
        variant["peak"] = _peak(n, island, facility, spec, unset_periods, peak_check)
        offered = island_offered
        for key, wanted in (
            ("frequency_support", None),
            ("voltage_control", ("voltage_control",)),
        ):
            variant.setdefault("verdicts", {})[key] = service_verdict(
                offered,
                svc["counted"],
                wanted=wanted,
                conditional=svc["conditional"],
                architecture=LABEL[a],
            )
        variants.append(variant)
    own_v = next(v for v in variants if v["own"])
    for v in variants:
        v["npv_delta"] = delta_interval(v, own_v)
    variants.sort(key=lambda v: (not v["own"], ARCHITECTURES.index(v["architecture"])))
    return {
        "own": own_arch,
        "own_set": own_set,
        "pack_version": pack.version,
        "boundary": BOUNDARY,
        "second_order": SECOND_ORDER,
        "weighting": WEIGHTING,
        "energy_basis": energy_basis,
        "rms_caveat": RMS_CAVEAT,
        "dc_bus_storage": storage,
        "price": {
            **price,
            "weighted_mean_per_mwh": _price_mean(w, facility_own, rate_t),
            "weighted_mean_basis": "weighted by this project's facility energy",
            "time_mean_per_mwh": float(np.mean(np.asarray(rate_t)[in_basis]))
            if in_basis.any()
            else None,
            "real": "held constant in real terms at the finance real rate",
            "not_valued": NOT_VALUED,
        },
        "currency": currency,
        "money_year": money_year,
        "years": years,
        "discount_rate": r,
        "discount_rate_source": rate_source,
        "it_mw": it_mw,
        "capex_note": (
            "capex on the final IT MW, bought in year 0 on every leg; boundary MV bus "
            "to rack input, the in-rack conversion inside"
        ),
        "range_note": RANGE_NOTE,
        "variants": variants,
        "summary": summary(variants, storage),
    }


def _price_mean(w, facility, rate) -> float | None:
    e = float(np.sum(w * facility))
    return float(np.sum(w * facility * rate)) / e if e > 0 else None


def _peak(n, island, facility, spec, unset, peak_check) -> dict[str, Any]:
    """The variant's peak against the energized import (``site_peak.peak_check``
    on a copy of the network with the variant's load; per snapshot, unweighted)."""
    m = n.copy()
    p = m.loads_t.p_set
    p["it_load"] = spec.critical_share * facility
    p["cooling"] = (1.0 - spec.critical_share) * facility
    m.loads_t.p_set = p
    try:
        rows = peak_check(m, island, unset=unset)
    except Exception as exc:  # noqa: BLE001 - the screen reports, never fails, on a peak read
        return {
            "verdict": "unknown",
            "note": f"the peak check could not be read ({exc})",
            "rows": [],
        }
    heads = [r["headroom_mw"] for r in rows if r.get("headroom_mw") is not None]
    note = None
    if not heads:
        verdict = "unknown"
        note = (
            "the network has no import connection to check"
            if not rows
            else "the energization date of a period is not set"
        )
    elif min(heads) < -1e-9:
        verdict = "fail"
    else:
        verdict = "pass"
    return {
        "verdict": verdict,
        "note": note,
        "min_headroom_mw": min(heads) if heads else None,
        "rows": [
            {
                k: r.get(k)
                for k in ("period", "load_mw", "headroom_mw", "binding_snapshot")
            }
            for r in rows
        ],
    }


# ── the project ───────────────────────────────────────────────────────────


def compare_project(
    project_dir, tpl, *, energy_price_per_mwh: float | None = None
) -> dict:
    """``compare`` on the project's saved network, stored spec, island sidecar
    and solver config; reads only (GK A C8, GK B K8)."""
    import pypsa

    from routers.projects import _solver_config_from_dict
    from services.adequacy import dc_backup
    from services.adequacy.site_peak import unset_periods
    from services.pypsa_service import PyPSAService

    d = pathlib.Path(project_dir)
    try:
        tid, spec, planning = dc_backup.project_spec(d, tpl)
    except dc_backup.DcBackupError as exc:
        raise _err(
            str(exc).replace("backup:", "Power architecture:"), "no_spec", "dc_load"
        ) from None
    meta = dc_backup._meta(d)
    raw = (meta.get("dc_load") or {}).get("spec")
    nc = d / "network.nc"
    if not nc.exists():
        raise _err("Network: the project has no saved network", "no_spec", "network")
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        n.import_from_netcdf(str(nc))
    try:
        island = json.loads((d / "island_config.json").read_text())
    except (OSError, ValueError):
        island = None
    cfg_path = d / "solver_config.json"
    try:
        cfg = _solver_config_from_dict(
            json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        )
    except (TypeError, ValueError) as exc:
        raise _err(
            f"Solver settings: the project's saved settings cannot be used ({exc})",
            "invalid",
            "solver_config",
        ) from None
    finance = getattr(cfg, "finance", None)
    try:
        rate_kwargs = dc_backup.finance_rate(finance)
    except dc_backup.DcBackupError as exc:
        raise _err(str(exc), "invalid", exc.field) from None
    from models.finance import FinanceInputs

    # finance_rate has read the case already, so it validates here.
    finance_currency = (
        FinanceInputs.model_validate(finance).currency if finance else None
    )
    plan = planning or tpl.NATIVE_PLANNING[tid]
    offered = None
    if isinstance(island, dict):
        from services.adequacy.island_config import (
            ContractError,
            offered_services,
            validate,
        )

        try:
            got = offered_services(validate(island))
            offered = got["services"] if got else None
        except (ContractError, ValueError):
            offered = None
    commercial = getattr(cfg, "commercial", None)
    if commercial is not None and hasattr(commercial, "model_dump"):
        commercial = commercial.model_dump(mode="json")
    it_mw = max(float(p.it_mw) for p in spec.phases)
    out = compare(
        n,
        spec,
        own_set=is_set(raw),
        timezone=tpl._dc_timezone(tid),
        years=plan.analysis_years,
        it_mw=it_mw,
        island=island,
        unset_periods=unset_periods(meta),
        commercial=commercial,
        energy_price_per_mwh=energy_price_per_mwh,
        rate_kwargs=rate_kwargs,
        island_offered=offered,
        finance_currency=finance_currency,
    )
    return {**out, "basis": dc_backup._basis(nc)}
