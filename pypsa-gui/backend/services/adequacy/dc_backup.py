"""
Battery-based backup in place of diesel (DC grid readiness 03).

A data-centre project's ``DataCentreLoadSpec.backup`` picks how the critical
load is backed on loss of grid: ``genset`` (the template's fleet, unchanged),
``bess`` (battery strings instead of the gensets) or ``hybrid`` (the gensets
plus strings for the critical load the firm gensets leave uncovered).

Sizing (``size_bess``): the shortfall S is the hourly critical peak less the
firm gensets (``site_peak.firm_genset_mw``, already N+k firm; none in
``bess`` mode). Strings of ``string_mw`` cover it: N = ceil(S / string_mw)
strings, N+1 one more, 2N twice N (``redundant_units``, the genset leg of the
cost delta reads the same function). Each string's usable energy is
string_mw × (autonomy_h + ride_through_s / 3600) / η_dispatch; its nameplate
is that over depth of discharge and the end-of-horizon retention. Retention
is 1 when augmentation restores nameplate every year; otherwise calendar fade
over the horizon and cycle fade at the assumed test discharges. The model
carries the usable energy only, so neither depth of discharge nor the fade
allowance leaks into the proof. The ride-through seconds are in the energy
only: the hourly re-dispatch cannot prove them.

Outside an outage the strings hold their charge: their discharge is off
(``p_max_pu`` 0 as a time series) in every normal solve, so they neither
arbitrage nor shave the import peak. The proof (``assess``) islands the
import for the autonomy window at the span of largest critical energy and
lets the strings discharge there only, with the N strings in service (the
redundant ones out). ``backup_cost_delta`` prices the mode against gensets.
"""
from __future__ import annotations

import json
import math
import pathlib
from typing import Any, Iterable

import numpy as np

from models.flex_archetypes import BackupSpec, RedundancyTier
from services.adequacy.site_peak import BACKUP_STRING_PREFIX as STRING_PREFIX

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
    "fuel_price_per_mwh_fuel": "assumed: fuel price per MWh of fuel (the defaults pack has "
                               "no fuel row)",
    "heat_rate_mwh_fuel_per_mwh_el": "the genset spec's heat rate",
}

_BACKUP_KEYS = ("autonomy_h", "ride_through_s", "string_mw", "test_cycles_per_year")
_BESS_KEYS = ("round_trip_efficiency", "depth_of_discharge", "calendar_fade_per_year",
              "cycle_fade_per_full_cycle", "augmentation_policy")
_UNITS = {"autonomy_h": "h", "ride_through_s": "s", "string_mw": "MW",
          "test_cycles_per_year": "full cycles/year", "round_trip_efficiency": "per unit",
          "depth_of_discharge": "per unit", "calendar_fade_per_year": "share of nameplate/year",
          "cycle_fade_per_full_cycle": "share of nameplate/full cycle",
          "augmentation_policy": "", "redundancy": ""}


class DcBackupError(ValueError):
    """A backup that cannot be sized, proved or priced, naming the field."""

    def __init__(self, message: str, field: str):
        super().__init__(message)
        self.field = field


def _finite(name: str, *values) -> None:
    for v in values:
        arr = np.asarray(v, dtype=float)
        if not np.isfinite(arr).all():
            raise DcBackupError(f"{name}: not a finite number", name)


def _check_years(years) -> None:
    if not isinstance(years, (int, np.integer)) or years < 1:
        raise DcBackupError(f"analysis_years: the horizon must be at least 1 year, got {years}",
                            "analysis_years")


def redundant_units(shortfall_mw: float, unit_mw: float, redundancy: RedundancyTier
                    ) -> tuple[int, int]:
    """``(N, total)`` units of ``unit_mw`` covering ``shortfall_mw`` at the
    tier: N = ceil(shortfall / unit), N+1 = N + 1, 2N = 2 × N. The strings
    and the genset leg of the cost delta both read it."""
    _finite("unit_mw", unit_mw)
    _finite("shortfall_mw", shortfall_mw)
    if unit_mw <= 0:
        raise DcBackupError("unit_mw: must be > 0", "unit_mw")
    n = math.ceil(shortfall_mw / unit_mw - 1e-9) if shortfall_mw > 1e-9 else 0
    if n == 0:
        return 0, 0
    return n, {"N": n, "N+1": n + 1, "2N": 2 * n}[redundancy]


def test_cycles(backup: BackupSpec, years: int) -> float:
    """Full-depth test cycles over the horizon, the cycle fade's clock."""
    return backup.test_cycles_per_year * years


test_cycles.__test__ = False  # not a pytest test


def retention(backup: BackupSpec, years: int) -> float:
    """End-of-horizon share of nameplate energy the strings keep: 1 when
    augmentation restores it every year, else calendar × cycle fade."""
    b = backup.bess
    if b.augmentation_policy == "restore_to_nameplate":
        return 1.0
    return ((1.0 - b.calendar_fade_per_year) ** years
            * (1.0 - b.cycle_fade_per_full_cycle * test_cycles(backup, years)))


def _inputs(backup: BackupSpec, redundancy: RedundancyTier, years: int) -> list[dict[str, Any]]:
    set_backup = backup.model_fields_set
    set_bess = backup.bess.model_fields_set if "bess" in set_backup else set()
    rows = [{"key": k, "value": getattr(backup, k), "unit": _UNITS[k],
             "assumed": k not in set_backup, "source": SOURCES[k]} for k in _BACKUP_KEYS]
    rows += [{"key": k, "value": getattr(backup.bess, k), "unit": _UNITS[k],
              "assumed": k not in set_bess, "source": SOURCES[k]} for k in _BESS_KEYS]
    rows.append({"key": "redundancy", "value": redundancy, "unit": "", "assumed": False,
                 "source": SOURCES["redundancy"]})
    cycles = test_cycles(backup, years)
    rows.append({"key": "retention", "value": retention(backup, years), "unit": "per unit",
                 "assumed": "test_cycles_per_year" not in set_backup,
                 "source": (f"{backup.bess.augmentation_policy}: calendar fade over {years} years "
                            f"and cycle fade at {cycles:g} assumed test cycles "
                            f"({backup.test_cycles_per_year:g} a year)")})
    return rows


def size_bess(critical_mw: Iterable[float] | float, *, firm_genset_mw: float,
              backup: BackupSpec, redundancy: RedundancyTier, years: int) -> dict[str, Any]:
    """The backup strings for ``backup.mode`` over a horizon of ``years``.

    ``critical_mw`` is the hourly critical load; ``firm_genset_mw`` the
    committed fleet's firm MW (ignored in ``bess`` mode, where the gensets
    go). Returns the shortfall, string counts, power, nameplate and usable
    energy, the retention and the labelled inputs. ``genset`` mode sizes
    nothing. Non-finite inputs, a horizon under a year and fade past
    nameplate are refused (``DcBackupError``)."""
    crit = np.atleast_1d(np.asarray(critical_mw, dtype=float))
    _finite("critical_load_mw", crit)
    _finite("firm_genset_mw", firm_genset_mw)
    _check_years(years)
    b = backup.bess
    _finite("backup", backup.autonomy_h, backup.ride_through_s, backup.string_mw,
            backup.test_cycles_per_year, b.round_trip_efficiency, b.depth_of_discharge,
            b.calendar_fade_per_year, b.cycle_fade_per_full_cycle)
    peak = float(np.max(crit, initial=0.0))
    base = {"mode": backup.mode, "critical_peak_mw": peak, "string_mw": backup.string_mw,
            "redundancy": redundancy, "years": years,
            "duration_h": backup.autonomy_h + backup.ride_through_s / 3600.0,
            "inputs": _inputs(backup, redundancy, years),
            "notes": ["ride_through_seconds_in_energy_only: the hourly re-dispatch cannot "
                      "prove the sub-hour bridge"]}
    if backup.mode == "genset":
        return {**base, "firm_genset_mw": firm_genset_mw, "shortfall_mw": 0.0, "strings_n": 0,
                "strings": 0, "p_mw": 0.0, "e_mwh_per_string": 0.0, "e_usable_mwh_per_string": 0.0,
                "e_mwh": 0.0, "retention": 1.0, "eta_dispatch": 1.0}
    g = 0.0 if backup.mode == "bess" else max(0.0, float(firm_genset_mw))
    short = max(0.0, peak - g)
    keep = retention(backup, years)
    if keep <= 1e-6:
        raise DcBackupError(
            f"backup.bess: the strings fade to {keep:.3g} of nameplate within {years} years "
            "without augmentation; set an augmentation policy or lower the fade",
            "backup.bess.augmentation_policy")
    eta_d = math.sqrt(b.round_trip_efficiency)
    usable = backup.string_mw * base["duration_h"] / eta_d
    nameplate = usable / (b.depth_of_discharge * keep)
    n, total = redundant_units(short, backup.string_mw, redundancy)
    return {**base, "firm_genset_mw": g, "shortfall_mw": short, "strings_n": n, "strings": total,
            "p_mw": total * backup.string_mw,
            "e_mwh_per_string": nameplate if total else 0.0,
            "e_usable_mwh_per_string": usable if total else 0.0,
            "e_mwh": total * nameplate, "retention": keep, "eta_dispatch": eta_d}


# ── The outage window ───────────────────────────────────────────────────


def _periods(n) -> np.ndarray:
    idx = n.snapshots
    if getattr(idx, "nlevels", 1) > 1:
        return np.asarray(idx.get_level_values(0))
    return np.zeros(len(idx), dtype=int)


def outage_window(critical: np.ndarray, hours: np.ndarray, autonomy_h: float,
                  periods: np.ndarray | None = None) -> dict[str, Any]:
    """The islanding window: for each start, the fewest snapshots whose
    hours (``hours``, the ``stores`` weighting: hours per snapshot) sum to at
    least ``autonomy_h``, within one investment period; the start with the
    largest critical energy over its window wins (the worst case for an
    energy-limited battery; ties: the earliest). A window that would run
    past the end of its period is not a candidate (the window shifts back,
    never shortens). ``{start, snapshots, hours, energy_mwh}``. A horizon
    (or every period) shorter than ``autonomy_h`` is refused."""
    crit = np.asarray(critical, dtype=float)
    w = np.asarray(hours, dtype=float)
    _finite("critical_load_mw", crit)
    _finite("snapshot hours", w)
    per = np.zeros(crit.size, dtype=int) if periods is None else np.asarray(periods)
    best: dict[str, Any] | None = None
    for s in range(crit.size):
        acc, e, k = 0.0, 0.0, 0
        while s + k < crit.size and per[s + k] == per[s] and acc < autonomy_h - 1e-9:
            acc += w[s + k]
            e += crit[s + k] * w[s + k]
            k += 1
        if acc < autonomy_h - 1e-9:
            continue
        if best is None or e > best["energy_mwh"] + 1e-9:
            best = {"start": s, "snapshots": k, "hours": acc, "energy_mwh": e}
    if best is None:
        raise DcBackupError(
            f"backup.autonomy_h: a {autonomy_h:g} h outage is longer than the modelled "
            f"horizon ({float(w.sum()):g} h)", "backup.autonomy_h")
    return best


# ── The network and island sidecar in the chosen mode ───────────────────


def network_window(n, critical: np.ndarray, autonomy_h: float) -> dict[str, Any]:
    """``outage_window`` on network ``n``: elapsed hours are the ``stores``
    weightings (1 h per snapshot in the DC templates), never ``generators``,
    which annualise a representative week (GK A ruling 2)."""
    window = outage_window(critical, n.snapshot_weightings["stores"].to_numpy(dtype=float),
                           autonomy_h, _periods(n))
    window["first"] = str(n.snapshots[window["start"]])
    return window


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
                 *, in_service: int | None = None, energy_scale: float = 1.0) -> dict:
    """Edit ``n`` in place to ``sizing``'s mode and return the matching
    island sidecar (a copy). ``genset`` changes nothing.

    ``bess`` removes the sidecar's gensets (committed and candidates) and
    their N+k and fuel requirements, and the island is formed seamlessly by
    the strings; ``bess`` and ``hybrid`` add the strings as committed
    StorageUnits ``backup_bess_<i>`` at the gensets' bus, each ``string_mw``
    with the usable energy only (``max_hours`` = usable / string_mw), √RTE
    each way, discharge held off (``p_max_pu`` 0 at every snapshot; the
    proof opens its window), grid-forming in the sidecar and listed as
    ride-through storage with ``sustained_h`` = ``autonomy_h``.
    ``in_service`` (None: all of ``sizing["strings"]``) adds fewer, for the
    proof with the redundant strings out; ``energy_scale`` scales the usable
    energy (tests)."""
    import copy

    import pandas as pd

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
    hours = sizing["e_usable_mwh_per_string"] * energy_scale / backup.string_mw
    a = lambda v: {"value": v, "source": "assumed"}  # noqa: E731
    gfm = {"kind": "bess", "control": "gfm_droop", "pf_rated": a(0.95), "droop_pct": a(4.0),
           "q_droop_pct": a(4.0), "tau_f_s": a(0.1), "i_max_pu": a(1.2), "k_gfm_min": a(1.0)}
    names = [f"{STRING_PREFIX}{i}" for i in range(1, count + 1)]
    carrier = "battery" if "battery" in n.carriers.index else ""
    for name in names:
        n.add("StorageUnit", name, bus=bus, carrier=carrier, p_nom=backup.string_mw,
              max_hours=hours, efficiency_store=eta, efficiency_dispatch=eta,
              cyclic_state_of_charge=True)
        units[name] = copy.deepcopy(gfm)
    if names:
        if "cyclic_state_of_charge_per_period" in n.storage_units.columns:
            n.storage_units.loc[names, "cyclic_state_of_charge_per_period"] = True
        hold = pd.DataFrame(0.0, index=n.snapshots, columns=names)
        n.storage_units_t.p_max_pu = pd.concat(
            [n.storage_units_t.p_max_pu.drop(columns=names, errors="ignore"), hold], axis=1)
    req["ride_through_storage"] = [*(req.get("ride_through_storage") or []), *names]
    req["sustained_h"] = {"value": backup.autonomy_h, "source": "assumed"}
    return out


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
    "real terms in the pack's money year at a real discount rate, no escalation",
    "horizon end: remaining_life_annuity on every purchase of both legs "
    "(finance.replacements.TerminalTerm: base × annuity(r, L) × annuity_pv_factor(r, remaining))",
    "genset run hours are charged at full output",
    "test-cycle charging losses of the strings are not charged",
    "hybrid keeps the gensets in both legs: its delta is the strings alone",
    "fade and augmentation are the backup spec's (the pack's degradation rows are blank)",
)


def pack_prices(version: str, region: str | None = None) -> dict[str, Any]:
    """The cost delta's prices from defaults pack ``version``: each of
    ``PACK_KEYS`` plus the discount rate (real), currency, money year and the
    pack's identity. A missing or blank row (other than ``BLANK_IS_ZERO``)
    is refused (``DcBackupError``)."""
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
            raise DcBackupError(f"defaults pack {pack.version} has no value for {key}",
                                "defaults_pack")
        prices[key] = float(row.value)
    money_year = next((rows[k].money_year for k in PACK_KEYS
                       if getattr(rows[k], "money_year", None)), None)
    currency = pack.region_info.currency if pack.region_info is not None else pack.finance.currency
    return {**prices, "discount_rate": float(pack.finance.discount_rate.value),
            "currency": currency, "money_year": money_year,
            "source": f"defaults pack {pack.pack_id} {pack.version}", "blank_as_zero": blank}


def real_rate(rate: float, inflation: float | None) -> float:
    """(1 + r) / (1 + inflation) − 1, the IC engine's real rate
    (``services.finance.engine``, real LCOE; ``SolverConfig.inflation_rate``)."""
    return (1.0 + rate) / (1.0 + (inflation or 0.0)) - 1.0


def _repurchase_years(life: float, years: int) -> list[int]:
    """Years (1..years) a part bought in year 0 is bought again (one bought
    in the final year serves the residual only)."""
    k, out = 1, []
    while round(k * life) <= years:
        out.append(int(round(k * life)))
        k += 1
    return out


def _augmentation(backup: BackupSpec, e_mwh: float, years: int) -> np.ndarray:
    """MWh of cells bought each year (index 0..years) to restore fade, on
    one fade curve: calendar fade plus cycle fade at the test cycles, a
    share of nameplate a year."""
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
    ``unit_mw`` at the redundancy tier (``redundant_units``, as the strings)."""
    _n, total = redundant_units(critical_peak_mw, unit_mw, redundancy)
    return total * unit_mw


def _part(cost: float, fom: float, life: float, years: int, r: float,
          extra: Iterable[tuple[int, float]] = ()) -> dict[str, np.ndarray]:
    """One part's cash (index 0..years): purchases (year 0, each lifetime,
    and ``extra`` (year, cost)), fixed O&M on the year-0 cost, and the
    remaining-life credit of every purchase at the horizon end."""
    from services.finance.replacements import annuity, annuity_pv_factor

    if not life or life < 1:
        raise DcBackupError(f"lifetime: a part's lifetime must be at least a year, got {life}",
                            "defaults_pack")
    t = years + 1
    capex, fom_c, residual = np.zeros(t), np.zeros(t), np.zeros(t)
    buys = [(0, cost), *((y, cost) for y in _repurchase_years(life, years)), *extra]
    for y, c in buys:
        capex[y] += c
        remaining = y + life - years
        if c and remaining > 0:
            residual[years] -= c * annuity(r, life) * annuity_pv_factor(r, min(remaining, life))
    fom_c[1:] = fom * cost
    return {"capex": capex, "fom": fom_c, "residual": residual}


def backup_cost_delta(sizing: dict[str, Any], backup: BackupSpec, *, genset_mw: float,
                      heat_rate: float, run_hours_per_year: float, years: int,
                      prices: dict[str, Any], rate: float | None = None,
                      inflation: float | None = None, run_hours_assumed: bool = True,
                      heat_rate_assumed: bool = False) -> dict[str, Any]:
    """NPV of the backup in ``sizing``'s mode against an all-genset backup.

    Cash runs year 0 (capex, undiscounted) to ``years`` (operation), end of
    year discounting (``services.finance.metrics.npv``) at a real rate: the
    pack's (assumed, real) when ``rate`` is None, else ``rate`` made real
    with ``inflation`` (the solver config's nominal rate and inflation, as
    the IC engine does). Each leg's purchases get the remaining-life credit
    at the horizon end (``_part``). Genset leg: ``genset_mw`` × overnight,
    FOM, repurchase at lifetime, fuel = run hours × MW × heat rate
    (MWh_th/MWh_e) × fuel price (per MWh_th). Battery leg: power and energy
    overnight, FOM, repurchase, and the augmentation MWh at the energy price.
    ``bess`` avoids the genset leg; ``hybrid`` keeps it in both, so its delta
    is the strings alone; ``genset``'s delta is 0. ``delta`` > 0: the chosen
    backup costs more. Missing or non-finite inputs are refused."""
    from services.finance.metrics import npv

    _check_years(years)
    for key in (*PACK_KEYS, "discount_rate"):
        if key not in prices:
            raise DcBackupError(f"prices: no value for {key}", "defaults_pack")
    _finite("prices", *(prices[k] for k in PACK_KEYS), prices["discount_rate"])
    _finite("backup cost inputs", genset_mw, heat_rate, run_hours_per_year,
            backup.fuel_price_per_mwh_fuel, sizing["p_mw"], sizing["e_mwh"])
    if rate is None:
        r, basis = float(prices["discount_rate"]), "pack discount rate (real, assumed)"
    else:
        _finite("discount_rate", rate, inflation or 0.0)
        r = real_rate(float(rate), inflation)
        basis = (f"solver config rate {rate:g} made real with its inflation {inflation or 0.0:g}: "
                 "(1 + r) / (1 + i) − 1")
    if r <= -1.0:
        raise DcBackupError(f"discount_rate: the real rate {r:g} is not above −100 %",
                            "discount_rate")
    cur = prices.get("currency") or ""
    g = _part(genset_mw * prices["gas-genset.investment.overnight"],
              prices["gas-genset.investment.fom_share"], prices["gas-genset.investment.lifetime"],
              years, r)
    fuel_mwh_th = genset_mw * run_hours_per_year * heat_rate
    fuel = np.r_[0.0, [fuel_mwh_th * backup.fuel_price_per_mwh_fuel] * years]
    p, e = sizing["p_mw"], sizing["e_mwh"]
    aug_mwh = _augmentation(backup, e, years)
    e_price = prices["battery.energy.overnight"]
    pw = _part(p * prices["battery.power.overnight"], prices["battery.power.fom_share"],
               prices["battery.power.lifetime"], years, r)
    en = _part(e * e_price, prices["battery.energy.fom_share"], prices["battery.energy.lifetime"],
               years, r, extra=[(y, m * e_price) for y, m in enumerate(aug_mwh) if m])
    mode = sizing["mode"]
    genset_cash = g["capex"] + g["fom"] + g["residual"] + fuel
    bess_cash = sum(part[k] for part in (pw, en) for k in ("capex", "fom", "residual"))
    if mode == "genset":
        bess_cash = np.zeros(years + 1)
    chosen = {"genset": genset_cash, "bess": bess_cash, "hybrid": genset_cash + bess_cash}[mode]

    def line(leg, name, cash, unit=cur):
        return {"leg": leg, "line": name, "unit": unit, "npv": npv(r, cash)}

    lines = [line("genset", "capex_and_repurchase", g["capex"]), line("genset", "fom", g["fom"]),
             line("genset", "fuel", fuel), line("genset", "residual_value", g["residual"])]
    if mode != "genset":
        lines += [line("bess", "power_capex_and_repurchase", pw["capex"]),
                  line("bess", "power_fom", pw["fom"]),
                  line("bess", "energy_capex_and_repurchase", en["capex"] - aug_mwh * e_price),
                  line("bess", "energy_fom", en["fom"]),
                  line("bess", "augmentation", aug_mwh * e_price),
                  line("bess", "residual_value", pw["residual"] + en["residual"])]
    inputs = [
        {"key": "genset_mw", "value": genset_mw, "unit": "MW", "assumed": False,
         "source": "redundant_units on the critical peak (as the strings)"},
        {"key": "run_hours_per_year", "value": run_hours_per_year, "unit": "h/year",
         "assumed": run_hours_assumed, "source": SOURCES["genset_run_hours_per_year"]
         if run_hours_assumed else "the genset spec's run_hours_cap_per_year"},
        {"key": "heat_rate", "value": heat_rate, "unit": "MWh_th/MWh_e",
         "assumed": heat_rate_assumed, "source": SOURCES["heat_rate_mwh_fuel_per_mwh_el"]},
        {"key": "fuel_price", "value": backup.fuel_price_per_mwh_fuel, "unit": f"{cur}/MWh_th",
         "assumed": "fuel_price_per_mwh_fuel" not in backup.model_fields_set,
         "source": SOURCES["fuel_price_per_mwh_fuel"]},
        {"key": "discount_rate", "value": r, "unit": "real, per year", "assumed": rate is None,
         "source": basis},
        *({"key": k, "value": prices[k], "unit": "", "assumed": False,
           "source": prices.get("source") or "prices"} for k in PACK_KEYS),
    ]
    npv_genset, npv_chosen = npv(r, genset_cash), npv(r, chosen)
    return {
        "mode": mode, "years": years, "discount_rate": r, "discount_rate_source": basis,
        "currency": prices.get("currency"), "money_year": prices.get("money_year"),
        "source": prices.get("source"),
        "genset_mw": genset_mw, "run_hours_per_year": run_hours_per_year,
        "run_hours_avoided_per_year": run_hours_per_year if mode == "bess" else 0.0,
        "fuel_mwh_th_avoided_per_year": fuel_mwh_th if mode == "bess" else 0.0,
        "augmentation_mwh": float(aug_mwh.sum()),
        "npv_genset": npv_genset, "npv_bess": npv(r, bess_cash), "npv_chosen": npv_chosen,
        "delta": None if npv_genset is None or npv_chosen is None else npv_chosen - npv_genset,
        "lines": lines, "inputs": inputs, "notes": list(COST_NOTES),
    }


# ── Assessment: sizing, the DtC proof per leg, the cost delta ───────────

#: Critical unmet at or below this is served (MWh over the window).
SERVED_TOL_MWH = 1e-6


def _proof(n, island: dict, sizing: dict, backup: BackupSpec, window: dict[str, Any], *,
           cfg, lock, stop_event, log_queue, energy_scale: float = 1.0) -> dict[str, Any]:
    """The windowed DtC stress on a copy of ``n`` in ``sizing``'s mode with
    only the N strings in service (the redundant ones out), their discharge
    open inside the window only. A critical unmet that cannot be read is
    ``not_shown``, never served."""
    from models.energy_hub import DtcConfig
    from services.adequacy.dtc import run_dtc_stress
    from services.adequacy.site_peak import import_links

    nn = n.copy()
    isl = apply_backup(nn, island, sizing, backup, in_service=sizing["strings_n"],
                       energy_scale=energy_scale)
    strings = [s for s in nn.storage_units.index if str(s).startswith(STRING_PREFIX)]
    s, k = window["start"], window["snapshots"]
    if strings:
        cols = nn.storage_units_t.p_max_pu.columns.get_indexer(strings)
        nn.storage_units_t.p_max_pu.iloc[s:s + k, cols] = 1.0
    crit = [c for c in critical_loads(isl) if c in nn.loads.index]
    links = import_links(nn)
    base = {"strings_in_service": sizing["strings_n"], "window_unserved_mwh": None,
            "on_grid_unserved_mwh": None, "table": None}
    if not crit or not links:
        return {**base, "verdict": "not_shown",
                "reason": "no critical load or no import link to island"}
    dtc = DtcConfig(critical_load_ids=crit, islanding_contingencies=links,
                    attribution="per_load", outage_start=s, outage_snapshots=k)
    table = run_dtc_stress(nn, cfg, lock=lock, stop_event=stop_event, log_queue=log_queue,
                           dtc=dtc)
    rows = table["contingencies"]
    solved = [r for r in rows if r.get("status") in ("ok", "optimal")]
    unread = [r for r in solved if r.get("critical_unserved_window_mwh") is None]
    if not rows or len(solved) < len(rows) or unread:
        why = ("a contingency re-dispatch did not solve" if len(solved) < len(rows) or not rows
               else "the re-dispatch captured no Load-keyed unserved energy")
        return {**base, "verdict": "not_shown", "reason": why, "table": table}
    unmet = max(float(r["critical_unserved_window_mwh"]) for r in solved)
    outside = max(float(r.get("critical_unserved_outside_window_mwh") or 0.0) for r in solved)
    verdict = "pass" if unmet <= SERVED_TOL_MWH else "fail"
    reason = (f"critical load served through the {window['hours']:g} h islanded window"
              if verdict == "pass"
              else f"{unmet:.3g} MWh of critical load unserved in the {window['hours']:g} h window")
    # Critical load the plan sheds on grid (outside the window): a campus
    # whose gensets also cover its peak above the import sheds it without them.
    return {**base, "verdict": verdict, "reason": reason, "window_unserved_mwh": unmet,
            "on_grid_unserved_mwh": outside, "table": table}


def assess(n, island: dict, spec, *, years: int, prices: dict[str, Any],
           rate: float | None = None, inflation: float | None = None, cfg=None, lock=None,
           stop_event=None, log_queue=None) -> dict[str, Any]:
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
    window = network_window(n, critical, backup.autonomy_h)
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
    capped = gspec.run_hours_cap_per_year is not None
    run_h = gspec.run_hours_cap_per_year if capped else backup.genset_run_hours_per_year
    fleet = genset_fleet_mw(sizing["critical_peak_mw"], unit, spec.redundancy)
    cost = backup_cost_delta(sizing, backup, genset_mw=fleet,
                             heat_rate=gspec.heat_rate_mwh_fuel_per_mwh_el,
                             run_hours_per_year=run_h, years=years, prices=prices, rate=rate,
                             inflation=inflation, run_hours_assumed=not capped,
                             heat_rate_assumed=not spec.gensets)
    cost["genset_unit_mw"] = unit
    return {"mode": backup.mode, "sizing": sizing, "window": window, "proofs": proofs,
            "verdict": proofs[backup.mode]["verdict"], "cost": cost,
            "notes": ["outage_window_soc_is_perfect_foresight: the strings' charge entering "
                      "the window is the re-dispatch's choice (they hold it outside)",
                      "ride_through_seconds_in_energy_only"]}


# ── The project: stored setting and assessment ──────────────────────────


META_FILE = "eh_template.json"
DC_TEMPLATES = ("eh_datacenter", "eh_datacenter_staged")


def _meta(project_dir) -> dict:
    try:
        meta = json.loads((pathlib.Path(project_dir) / META_FILE).read_text())
    except (OSError, ValueError):
        meta = None
    if not isinstance(meta, dict):
        raise DcBackupError("backup: the project has no Energy Hub template metadata",
                            "project")
    return meta


def project_spec(project_dir, tpl) -> tuple[str, Any, Any]:
    """``(template id, DataCentreLoadSpec, Planning | None)`` of a data-centre
    project created with a ``dc_load`` body; ``DcBackupError`` otherwise."""
    from models.flex_archetypes import DataCentreLoadSpec

    meta = _meta(project_dir)
    tid = meta.get("id")
    block = meta.get("dc_load") if isinstance(meta.get("dc_load"), dict) else {}
    raw = block.get("spec")
    if tid not in DC_TEMPLATES or not isinstance(raw, dict):
        raise DcBackupError("backup: only a data-centre project created with a dc_load spec "
                            "has a backup setting", "dc_load")
    plan = meta.get("planning")
    planning = (tpl.Planning(tuple(int(y) for y in plan["periods"]), int(plan["end_year"]))
                if isinstance(plan, dict) and plan.get("periods") else None)
    return tid, DataCentreLoadSpec.model_validate(raw), planning


def save_backup(project_dir, tpl, backup: dict) -> dict:
    """Store ``backup`` as the project's ``dc_load.spec.backup`` after the
    create body's own checks (``dc_load_spec``: the BackupSpec validators,
    the site-peak refusal in the new mode). Writes ``eh_template.json``
    only; the saved network is never touched (the setting is read by
    ``assess`` and nothing at solve time). Returns the stored backup."""
    from services.atomic_io import atomic_write_text
    from services.dc_load_profile import DcLoadError

    tid, spec, planning = project_spec(project_dir, tpl)
    if not isinstance(backup, dict):
        raise DcBackupError("backup: must be an object", "backup")
    raw = {**spec.model_dump(mode="json"), "backup": backup}
    try:
        checked = tpl.dc_load_spec(tid, raw, planning)
    except DcLoadError as exc:
        raise DcBackupError(str(exc), exc.field) from None
    meta = _meta(project_dir)
    meta["dc_load"]["spec"]["backup"] = checked.backup.model_dump(mode="json")
    atomic_write_text(pathlib.Path(project_dir) / META_FILE, json.dumps(meta, indent=2,
                                                                        sort_keys=True) + "\n")
    return meta["dc_load"]["spec"]["backup"]


def assess_project(project_dir, tpl, mode: str | None = None) -> dict[str, Any]:
    """``assess`` on a private copy of the project's saved network and island
    sidecar, in ``mode`` (None: the stored setting). The solver config is
    read through ``routers.projects._solver_config_from_dict``; the solves
    run ``run_dtc_stress`` → ``run_simulation`` → ``build_lp_chain`` under
    ``PyPSAService.solving_context`` on a private context. Reads only:
    network.nc and eh_template.json are left byte for byte."""
    import pypsa

    from routers.projects import _solver_config_from_dict
    from services.adequacy.island_config import ContractError, validate
    from services.project_context import ProjectContext
    from services.pypsa_service import PyPSAService

    d = pathlib.Path(project_dir)
    tid, spec, planning = project_spec(d, tpl)
    if mode is not None:
        try:
            spec = spec.model_copy(update={"backup": BackupSpec.model_validate(
                {**spec.backup.model_dump(mode="json"), "mode": mode})})
        except ValueError as exc:
            raise DcBackupError(f"backup.mode: {exc}", "backup.mode") from None
    try:
        island = validate(json.loads((d / "island_config.json").read_text()))
    except (OSError, ValueError, ContractError) as exc:
        raise DcBackupError(f"backup: the project's island sidecar cannot be read ({exc})",
                            "island_config") from None
    nc = d / "network.nc"
    if not nc.exists():
        raise DcBackupError("backup: the project has no saved network", "network")
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        n.import_from_netcdf(str(nc))
    cfg_path = d / "solver_config.json"
    try:
        cfg = _solver_config_from_dict(json.loads(cfg_path.read_text()) if cfg_path.exists() else {})
    except (TypeError, ValueError) as exc:
        raise DcBackupError(f"solver_config: the project's saved solver settings cannot be "
                            f"used ({exc})", "solver_config") from None
    plan = planning or tpl.NATIVE_PLANNING[tid]
    prices = pack_prices(tpl.TEMPLATE_PACK_VERSION)
    with PyPSAService.solving_context(ProjectContext(network=n)):
        out = assess(n, island, spec, years=plan.analysis_years, prices=prices,
                     rate=float(cfg.discount_rate), inflation=float(cfg.inflation_rate or 0.0),
                     cfg=cfg, lock=PyPSAService.get_lock())
    return {**out, "basis": _basis(nc)}


#: What the assessment's re-dispatch does not reproduce from a normal solve
#: (``dtc.run_dtc_stress``), named in the result (GK A condition 7).
NOT_REPRODUCED = (
    "extendable capacity is frozen at its solved size (the nameplate on an unsolved network)",
    "the ENS cap, zone multiple and reserve margin are stripped for the islanded re-dispatch",
)


def _basis(nc: pathlib.Path) -> dict[str, Any]:
    """Which saved network the assessment read: its save time and a digest,
    and the pre-solve steps it does not reproduce."""
    import hashlib
    from datetime import datetime, timezone

    data = nc.read_bytes()
    return {"network_saved_at": datetime.fromtimestamp(nc.stat().st_mtime, timezone.utc).isoformat(),
            "network_sha256": hashlib.sha256(data).hexdigest()[:12],
            "not_reproduced": list(NOT_REPRODUCED)}
