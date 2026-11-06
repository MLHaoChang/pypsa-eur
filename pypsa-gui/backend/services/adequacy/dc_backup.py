"""
Battery-based backup in place of diesel (DC grid readiness 03).

A data-centre project's ``DataCentreLoadSpec.backup`` picks how the critical
load is backed on loss of grid: ``genset`` (the template's fleet, unchanged),
``bess`` (battery strings instead of the gensets) or ``hybrid`` (the gensets
plus strings for the critical load the firm gensets leave uncovered).

Sizing (``size_bess``): the shortfall S is the hourly critical peak less the
firm gensets (none in ``bess`` mode). Strings of ``string_mw`` cover it:
N = ceil(S / string_mw) strings, N+1 one more, 2N twice N. Each string
stores string_mw × (autonomy_h + ride_through_s / 3600) of deliverable
energy after depth of discharge, the one-way discharge efficiency
(√RTE) and the end-of-horizon retention. Retention is 1 when augmentation
restores nameplate every year; otherwise the strings are oversized up front
for calendar fade over the horizon and cycle fade at the assumed test
discharges. The ride-through seconds are counted in the energy only: the
hourly re-dispatch does not resolve them.

``apply_backup`` edits a network and its island sidecar to the chosen
mode (no PyPSA import: the caller passes the network); ``backup_cost_delta``
prices the result against gensets.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

from models.flex_archetypes import BackupSpec, RedundancyTier

#: Where each assumed default comes from (generic engineering; labelled
#: "assumed" in every row until the project replaces it).
SOURCES: dict[str, str] = {
    "autonomy_h": "assumed: hours of critical-load autonomy the backup is sized for "
                  "(generic data-centre practice, replace with the project's design basis)",
    "ride_through_s": "assumed: the DC template's island bridge time (sidecar bridge_s)",
    "string_mw": "assumed: one battery string (PCS block) per 5 MW, generic MV BESS practice",
    "round_trip_efficiency": "assumed: Li-ion AC round-trip efficiency, generic",
    "depth_of_discharge": "assumed: usable share of nameplate energy, generic Li-ion",
    "calendar_fade_per_year": "assumed: Li-ion calendar fade, generic (the defaults pack's "
                              "degradation rows are blank placeholders)",
    "cycle_fade_per_full_cycle": "assumed: Li-ion fade per full cycle, generic",
    "test_cycles_per_year": "assumed: one full-depth test discharge a month",
    "augmentation_policy": "assumed: augmentation restores nameplate every year",
    "redundancy": "the DC load spec's redundancy tier",
    "genset_run_hours_per_year": "assumed: genset test and maintenance run hours a year",
    "fuel_price_per_mwh_fuel": "assumed: fuel price per MWh of fuel",
    "heat_rate_mwh_fuel_per_mwh_el": "the genset spec's heat rate",
}

#: Keys whose value came from the spec's defaults (assumed) unless set.
_BACKUP_KEYS = ("autonomy_h", "ride_through_s", "string_mw", "test_cycles_per_year")
_BESS_KEYS = ("round_trip_efficiency", "depth_of_discharge", "calendar_fade_per_year",
              "cycle_fade_per_full_cycle", "augmentation_policy")


class DcBackupError(ValueError):
    """A backup that cannot be sized or priced, naming the field."""

    def __init__(self, message: str, field: str):
        super().__init__(message)
        self.field = field


def _string_counts(shortfall_mw: float, string_mw: float, redundancy: RedundancyTier) -> tuple[int, int]:
    n = math.ceil(shortfall_mw / string_mw - 1e-9) if shortfall_mw > 1e-9 else 0
    if n == 0:
        return 0, 0
    total = {"N": n, "N+1": n + 1, "2N": 2 * n}[redundancy]
    return n, total


def retention(backup: BackupSpec, years: int) -> float:
    """End-of-horizon share of nameplate energy the strings keep: 1 when
    augmentation restores it every year, else calendar × cycle fade."""
    b = backup.bess
    if b.augmentation_policy == "restore_to_nameplate":
        return 1.0
    cycles = backup.test_cycles_per_year * years
    return (1.0 - b.calendar_fade_per_year) ** years * (1.0 - b.cycle_fade_per_full_cycle * cycles)


def _inputs(backup: BackupSpec, redundancy: RedundancyTier) -> list[dict[str, Any]]:
    set_backup = backup.model_fields_set
    set_bess = backup.bess.model_fields_set if "bess" in set_backup else set()
    rows = [{"key": k, "value": getattr(backup, k), "assumed": k not in set_backup,
             "source": SOURCES[k]} for k in _BACKUP_KEYS]
    rows += [{"key": k, "value": getattr(backup.bess, k), "assumed": k not in set_bess,
              "source": SOURCES[k]} for k in _BESS_KEYS]
    rows.append({"key": "redundancy", "value": redundancy, "assumed": False,
                 "source": SOURCES["redundancy"]})
    return rows


def size_bess(critical_mw: Iterable[float] | float, *, firm_genset_mw: float,
              backup: BackupSpec, redundancy: RedundancyTier, years: int) -> dict[str, Any]:
    """The backup strings for ``backup.mode`` over a horizon of ``years``.

    ``critical_mw`` is the hourly critical load; ``firm_genset_mw`` the
    committed fleet's firm MW (ignored in ``bess`` mode, where the gensets
    go). Returns the shortfall, string counts, power and energy (nameplate),
    the retention and the labelled inputs. ``genset`` mode sizes nothing."""
    peak = float(np.max(np.atleast_1d(np.asarray(critical_mw, dtype=float)), initial=0.0))
    base = {"mode": backup.mode, "critical_peak_mw": peak, "string_mw": backup.string_mw,
            "redundancy": redundancy, "duration_h": backup.autonomy_h + backup.ride_through_s / 3600.0,
            "inputs": _inputs(backup, redundancy)}
    if backup.mode == "genset":
        return {**base, "firm_genset_mw": firm_genset_mw, "shortfall_mw": 0.0, "strings_n": 0,
                "strings": 0, "p_mw": 0.0, "e_mwh_per_string": 0.0, "e_mwh": 0.0,
                "retention": 1.0, "eta_dispatch": 1.0}
    g = 0.0 if backup.mode == "bess" else max(0.0, float(firm_genset_mw))
    short = max(0.0, peak - g)
    keep = retention(backup, years)
    if keep <= 1e-6:
        raise DcBackupError(
            f"backup.bess: the strings fade to {keep:.3g} of nameplate within {years} years "
            "without augmentation; set an augmentation policy or lower the fade",
            "backup.bess.augmentation_policy")
    eta_d = math.sqrt(backup.bess.round_trip_efficiency)
    per_string = (backup.string_mw * base["duration_h"]
                  / (backup.bess.depth_of_discharge * eta_d * keep))
    n, total = _string_counts(short, backup.string_mw, redundancy)
    return {**base, "firm_genset_mw": g, "shortfall_mw": short, "strings_n": n, "strings": total,
            "p_mw": total * backup.string_mw, "e_mwh_per_string": per_string if total else 0.0,
            "e_mwh": total * per_string, "retention": keep, "eta_dispatch": eta_d}


# ── Investment case: the cost delta against gensets ─────────────────────

#: The pack rows the cost delta reads (``pack_prices``).
PACK_KEYS: tuple[str, ...] = (
    "gas-genset.investment.overnight", "gas-genset.investment.fom_share",
    "gas-genset.investment.lifetime",
    "battery.power.overnight", "battery.power.fom_share", "battery.power.lifetime",
    "battery.energy.overnight", "battery.energy.fom_share", "battery.energy.lifetime",
)

#: Pack rows a blank value means 0 for: technology-data books the battery's
#: fixed O&M on the inverter (power part) only, so the pack leaves the
#: storage part's FOM blank (its own row says so).
BLANK_IS_ZERO: tuple[str, ...] = ("battery.energy.fom_share",)

#: The honest limits of the comparison, shown with it.
COST_NOTES: tuple[str, ...] = (
    "real terms in the pack's money year, no escalation",
    "genset run hours are charged at full output",
    "test-cycle charging losses of the strings are not charged",
    "hybrid keeps the gensets in both legs: its delta is the strings alone",
    "fade and augmentation are the backup spec's (the pack's degradation rows are blank)",
)


def pack_prices(version: str, region: str | None = None) -> dict[str, Any]:
    """The cost delta's prices from defaults pack ``version``: each of
    ``PACK_KEYS`` plus the discount rate, currency, money year and the
    pack's identity. A missing or blank row is refused (``DcBackupError``)."""
    from services.library.defaults_pack.loader import load_defaults_pack

    pack = load_defaults_pack(version, region=region)
    rows = {r.key: r for r in pack.cost_values}
    prices: dict[str, Any] = {}
    blank: list[str] = []
    for key in PACK_KEYS:
        row = rows.get(key)
        if row is not None and row.value is None and key in BLANK_IS_ZERO:
            prices[key] = 0.0
            blank.append(key)
            continue
        if row is None or row.value is None:
            raise DcBackupError(f"defaults pack {pack.version} has no value for {key!r}",
                                "defaults_pack")
        prices[key] = float(row.value)
    money_year = next((rows[k].money_year for k in PACK_KEYS
                       if getattr(rows[k], "money_year", None)), None)
    currency = pack.region_info.currency if pack.region_info is not None else pack.finance.currency
    return {**prices, "discount_rate": float(pack.finance.discount_rate.value),
            "currency": currency, "money_year": money_year,
            "source": f"defaults pack {pack.pack_id} {pack.version}", "blank_as_zero": blank}


def _repurchase_years(life: float, years: int) -> list[int]:
    """Years (1..years-1) a part bought in year 0 is bought again."""
    if not life or life <= 0 or not math.isfinite(life):
        return []
    k, out = 1, []
    while round(k * life) < years:
        out.append(int(round(k * life)))
        k += 1
    return out


def _augmentation(backup: BackupSpec, e_mwh: float, years: int) -> np.ndarray:
    """MWh of cells bought each year (index 0..years) to restore fade."""
    b = backup.bess
    fade = (b.calendar_fade_per_year + b.cycle_fade_per_full_cycle * backup.test_cycles_per_year) * e_mwh
    out = np.zeros(years + 1)
    if b.augmentation_policy == "restore_to_nameplate":
        out[1:] = fade
    elif b.augmentation_policy == "fixed_years":
        last = 0
        for y in sorted(set(b.augmentation_years)):
            if 1 <= y <= years:
                out[y] = fade * (y - last)
                last = y
    return out


def genset_fleet_mw(critical_peak_mw: float, unit_mw: float, redundancy: RedundancyTier) -> float:
    """The genset fleet a genset backup needs for the critical peak: units of
    ``unit_mw`` at the redundancy tier (the same rule as the strings)."""
    _n, total = _string_counts(critical_peak_mw, unit_mw, redundancy)
    return total * unit_mw


def backup_cost_delta(sizing: dict[str, Any], backup: BackupSpec, *, genset_mw: float,
                      heat_rate: float, run_hours_per_year: float, years: int,
                      prices: dict[str, Any], rate: float | None = None) -> dict[str, Any]:
    """NPV of the backup in ``sizing``'s mode against an all-genset backup.

    Cash runs year 0 (capex, undiscounted) to ``years`` (operation), end of
    year discounting (``services.finance.metrics.npv``) at ``rate`` (None:
    the pack's). Genset leg: ``genset_mw`` × overnight, FOM, fuel for the
    run hours (MW × hours × heat rate × fuel price), repurchase at lifetime.
    Battery leg: power and energy overnight, each part's FOM and repurchase,
    and the augmentation MWh at the energy price. In ``bess`` mode the
    genset leg is avoided; in ``hybrid`` it is kept in both, so the delta is
    the strings alone; ``genset`` mode's delta is 0. ``delta`` > 0: the
    chosen backup costs more."""
    from services.finance.metrics import npv

    r = prices["discount_rate"] if rate is None else rate
    t = years + 1

    def leg(capex: float, fom: float, life: float) -> np.ndarray:
        c = np.zeros(t)
        c[0] = capex
        c[1:] += fom * capex
        for y in _repurchase_years(life, t):
            c[y] += capex
        return c

    g_capex = genset_mw * prices["gas-genset.investment.overnight"]
    genset = leg(g_capex, prices["gas-genset.investment.fom_share"],
                 prices["gas-genset.investment.lifetime"])
    fuel = genset_mw * run_hours_per_year * heat_rate * backup.fuel_price_per_mwh_fuel
    genset[1:] += fuel
    p, e = sizing["p_mw"], sizing["e_mwh"]
    power = leg(p * prices["battery.power.overnight"], prices["battery.power.fom_share"],
                prices["battery.power.lifetime"])
    energy = leg(e * prices["battery.energy.overnight"], prices["battery.energy.fom_share"],
                 prices["battery.energy.lifetime"])
    aug_mwh = _augmentation(backup, e, years)
    aug = aug_mwh * prices["battery.energy.overnight"]
    bess = power + energy + aug
    zero = np.zeros(t)
    mode = sizing["mode"]
    chosen = {"genset": genset, "bess": bess, "hybrid": genset + bess}[mode]
    lines = [
        {"leg": "genset", "line": "capex", "npv": npv(r, np.r_[g_capex, np.zeros(years)])},
        {"leg": "genset", "line": "fom_and_repurchase", "npv": npv(r, genset - np.r_[g_capex, [fuel] * years])},
        {"leg": "genset", "line": "fuel", "npv": npv(r, np.r_[0.0, [fuel] * years])},
        {"leg": "bess", "line": "power_capex_fom_repurchase", "npv": npv(r, power)},
        {"leg": "bess", "line": "energy_capex_fom_repurchase", "npv": npv(r, energy)},
        {"leg": "bess", "line": "augmentation", "npv": npv(r, aug)},
    ]
    if mode == "genset":
        lines = [x for x in lines if x["leg"] == "genset"]
        bess = zero
    npv_genset, npv_chosen = npv(r, genset), npv(r, chosen)
    return {
        "mode": mode, "years": years, "discount_rate": r,
        "discount_rate_source": "pack" if rate is None else "finance case",
        "currency": prices.get("currency"), "money_year": prices.get("money_year"),
        "source": prices.get("source"),
        "genset_mw": genset_mw, "run_hours_per_year": run_hours_per_year,
        "run_hours_avoided_per_year": run_hours_per_year if mode == "bess" else 0.0,
        "fuel_mwh_avoided_per_year": genset_mw * run_hours_per_year * heat_rate if mode == "bess" else 0.0,
        "augmentation_mwh": float(aug_mwh.sum()),
        "npv_genset": npv_genset, "npv_chosen": npv_chosen,
        "delta": None if npv_genset is None or npv_chosen is None else npv_chosen - npv_genset,
        "lines": lines, "notes": list(COST_NOTES),
    }


# ── The network and island sidecar in the chosen mode ───────────────────

STRING_PREFIX = "backup_bess_"


def critical_loads(island: dict | None) -> list[str]:
    """The loads the island sidecar marks critical."""
    units = (island or {}).get("units") or {}
    return [u for u, d in units.items() if d.get("kind") == "load" and d.get("critical")]


def genset_units(n, island: dict | None) -> list[str]:
    """The sidecar's genset units that are Generators of ``n``."""
    units = (island or {}).get("units") or {}
    return [u for u, d in units.items() if d.get("kind") == "genset" and u in n.generators.index]


def string_bus(n, island: dict | None) -> str:
    """Where the strings connect: the gensets' bus (they replace or join the
    fleet), else the bus of the largest critical load."""
    gens = genset_units(n, island)
    if gens:
        return str(n.generators.at[gens[0], "bus"])
    crit = [c for c in critical_loads(island) if c in n.loads.index]
    if not crit:
        raise DcBackupError("backup: the island sidecar marks no critical load in the network",
                            "backup.mode")
    from services.adequacy.site_peak import named_load

    return str(n.loads.at[max(crit, key=lambda c: float(named_load(n, [c]).max())), "bus"])


def apply_backup(n, island: dict, sizing: dict[str, Any], backup: BackupSpec,
                 *, in_service: int | None = None) -> dict:
    """Edit ``n`` in place to ``sizing``'s mode and return the matching
    island sidecar (a copy). ``genset`` changes nothing.

    ``bess`` removes the sidecar's gensets (committed and candidates) and
    their N+k requirement; ``bess`` and ``hybrid`` add the strings as
    committed StorageUnits ``backup_bess_<i>`` at the gensets' bus, each
    ``string_mw`` with the usable energy string_mw × duration / η_dispatch
    (depth of discharge and the fade allowance are in the nameplate, not in
    the model), grid-forming in the sidecar and listed as ride-through
    storage with ``sustained_h`` = ``autonomy_h``. ``in_service`` (None: all
    of ``sizing["strings"]``) adds fewer, for the proof with the redundant
    strings out."""
    import copy

    out = copy.deepcopy(island)
    mode = sizing["mode"]
    if mode == "genset":
        return out
    units, req = out.setdefault("units", {}), out.setdefault("requirements", {})
    bus = string_bus(n, island)
    if mode == "bess":
        for g in genset_units(n, island):
            n.remove("Generator", g)
        for u in [u for u, d in units.items() if d.get("kind") == "genset"]:
            units.pop(u)
        req.pop("genset_redundancy_n", None)
        req.pop("fuel_autonomy_h", None)
        # No genset joins: the strings form the island from 0+.
        req["scenarios"] = ["seamless"]
    count = sizing["strings"] if in_service is None else min(in_service, sizing["strings"])
    eta = sizing["eta_dispatch"]
    hours = sizing["duration_h"] / eta
    gfm = {"kind": "bess", "control": "gfm_droop", "pf_rated": {"value": 0.95, "source": "assumed"},
           "droop_pct": {"value": 4.0, "source": "assumed"},
           "q_droop_pct": {"value": 4.0, "source": "assumed"},
           "tau_f_s": {"value": 0.1, "source": "assumed"},
           "i_max_pu": {"value": 1.2, "source": "assumed"},
           "k_gfm_min": {"value": 1.0, "source": "assumed"}}
    names = [f"{STRING_PREFIX}{i}" for i in range(1, count + 1)]
    carrier = "battery" if "battery" in n.carriers.index else ""
    for name in names:
        n.add("StorageUnit", name, bus=bus, carrier=carrier, p_nom=backup.string_mw,
              max_hours=hours, efficiency_store=eta, efficiency_dispatch=eta,
              cyclic_state_of_charge=True)
        units[name] = copy.deepcopy(gfm)
    if "cyclic_state_of_charge_per_period" in n.storage_units.columns:
        n.storage_units.loc[names, "cyclic_state_of_charge_per_period"] = True
    req["ride_through_storage"] = [*(req.get("ride_through_storage") or []), *names]
    req["sustained_h"] = {"value": backup.autonomy_h, "source": SOURCES["autonomy_h"]}
    return out


# ── Assessment: sizing, the DtC proof per leg, the cost delta ───────────

#: Critical unmet below this is served (MWh over the window).
SERVED_TOL_MWH = 1e-6


def outage_window(critical: np.ndarray, autonomy_h: float, hours_per_snapshot: float = 1.0
                  ) -> tuple[int, int]:
    """``(start, snapshots)``: the window of ``autonomy_h`` (rounded up to
    whole snapshots) starting at the critical-peak snapshot, moved earlier
    when it would run past the horizon."""
    k = max(1, math.ceil(autonomy_h / hours_per_snapshot - 1e-9))
    k = min(k, critical.size)
    start = int(np.argmax(critical)) if critical.size else 0
    return min(start, critical.size - k), k


def _proof(n, island: dict, sizing: dict, backup: BackupSpec, window: tuple[int, int], *,
           cfg, lock, stop_event, log_queue) -> dict[str, Any]:
    """The windowed DtC stress on a copy of ``n`` in ``sizing``'s mode with
    only the N strings in service (the redundant ones out)."""
    from models.energy_hub import DtcConfig
    from services.adequacy.dtc import run_dtc_stress
    from services.adequacy.site_peak import import_links

    nn = n.copy()
    isl = apply_backup(nn, island, sizing, backup, in_service=sizing["strings_n"])
    crit = [c for c in critical_loads(isl) if c in nn.loads.index]
    links = import_links(nn)
    if not crit or not links:
        return {"verdict": "unknown", "reason": "no critical load or no import link to island",
                "table": None}
    dtc = DtcConfig(critical_load_ids=crit, islanding_contingencies=links,
                    attribution="per_load", outage_start=window[0], outage_hours=window[1])
    table = run_dtc_stress(nn, cfg, lock=lock, stop_event=stop_event, log_queue=log_queue,
                           dtc=dtc)
    rows = table["contingencies"]
    solved = [r for r in rows if r.get("status") in ("ok", "optimal")]
    if not rows or len(solved) < len(rows):
        verdict, reason = "unknown", "a contingency re-dispatch did not solve"
    elif any(r.get("critical_unserved_window_mwh") is None for r in solved):
        verdict, reason = "unknown", "the re-dispatch captured no Load-keyed unserved energy"
    else:
        unmet = max(float(r["critical_unserved_window_mwh"]) for r in solved)
        verdict = "pass" if unmet <= SERVED_TOL_MWH else "fail"
        reason = (f"critical load served through the {window[1]} h islanded window"
                  if verdict == "pass"
                  else f"{unmet:.3g} MWh of critical load unserved in the {window[1]} h window")
    outside = max((float(r.get("critical_unserved_outside_window_mwh") or 0.0) for r in solved),
                  default=0.0)
    return {"verdict": verdict, "reason": reason, "strings_in_service": sizing["strings_n"],
            "window_unserved_mwh": None if verdict == "unknown" else unmet,
            # Critical load the plan sheds on grid (outside the window): a
            # campus whose gensets also cover its peak above the import
            # sheds it without them (the site-peak check says the same).
            "on_grid_unserved_mwh": outside, "table": table}


def assess(n, island: dict, spec, *, years: int, prices: dict[str, Any],
           rate: float | None = None, cfg=None, lock=None, stop_event=None,
           log_queue=None) -> dict[str, Any]:
    """Size ``spec.backup`` on ``n`` (a campus in genset form: the sidecar's
    gensets in it, no strings), prove the genset baseline and the chosen
    mode on the windowed DtC stress, and price the mode against gensets."""
    import queue
    import threading

    from models.flex_archetypes import GensetSpec
    from services.adequacy.site_peak import firm_genset_mw, named_load

    if any(str(s).startswith(STRING_PREFIX) for s in n.storage_units.index):
        raise DcBackupError("backup: the network already carries backup strings; assess a "
                            "campus in genset form", "backup.mode")
    backup = spec.backup
    critical = named_load(n, critical_loads(island))
    g, g_rule = firm_genset_mw(n, island)
    sizing = size_bess(critical, firm_genset_mw=g, backup=backup,
                       redundancy=spec.redundancy, years=years)
    sizing["firm_genset_rule"] = g_rule
    hours = float(n.snapshot_weightings["stores"].iloc[0]) if len(n.snapshots) else 1.0
    window = outage_window(critical, backup.autonomy_h, hours)
    run = {"cfg": cfg, "lock": lock or threading.Lock(),
           "stop_event": stop_event or threading.Event(),
           "log_queue": log_queue or queue.SimpleQueue()}
    genset_sizing = size_bess(critical, firm_genset_mw=g, backup=backup.model_copy(
        update={"mode": "genset"}), redundancy=spec.redundancy, years=years)
    proofs = {"genset": _proof(n, island, genset_sizing, backup, window, **run)}
    if backup.mode != "genset":
        proofs[backup.mode] = _proof(n, island, sizing, backup, window, **run)
    gens = genset_units(n, island)
    unit = max((float(n.generators.at[u, "p_nom"]) for u in gens), default=0.0) or backup.string_mw
    gspec = spec.gensets[0] if spec.gensets else GensetSpec(power_mw=unit)
    run_h = (gspec.run_hours_cap_per_year if gspec.run_hours_cap_per_year is not None
             else backup.genset_run_hours_per_year)
    fleet = genset_fleet_mw(sizing["critical_peak_mw"], unit, spec.redundancy)
    cost = backup_cost_delta(sizing, backup, genset_mw=fleet,
                             heat_rate=gspec.heat_rate_mwh_fuel_per_mwh_el,
                             run_hours_per_year=run_h, years=years, prices=prices, rate=rate)
    cost["genset_unit_mw"] = unit
    return {"mode": backup.mode, "sizing": sizing,
            "window": {"start": window[0], "hours": window[1],
                       "first": str(n.snapshots[window[0]])},
            "proofs": proofs, "verdict": proofs[backup.mode]["verdict"], "cost": cost}
