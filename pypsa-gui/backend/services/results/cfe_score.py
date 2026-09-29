"""
`/results/cfe_score`: hourly 24/7 carbon-free energy matching of the site
(Edge Investment Case P2 WP2.5; spec §5 CFE).

    score = Σ_h min(load_h, clean_h) / Σ_h load_h        per investment period

  * `load_h` — the energy of the loads BEHIND the commercial meter (on the
    site side of the import Links, `lp_bindings._meter_sides`); storage
    charging is not load;
  * `clean_h` —
      - on-site generation of clean carriers (`co2_emissions == 0`, or the
        `clean_carriers` override) CONSUMED on site: minus its share of the PoC
        export, pro rata to all on-site generation (the WP2.2a rule);
      - plus the volume of OFF-site PPAs the site buys whose assets are all
        clean (pay-as-produced / sleeved / market-plus-premium: the assets'
        output; baseload: `baseload_mw`); on-site PPA assets are already in
        on-site generation;
      - plus grid import × `ic_grid_cfe_share` (absent ⇒ the grid counts 0,
        flagged `grid_cfe_share_missing`);
  * energy per row = MW × its represented hours (objective weighting), summed
    into local clock hours.

Disclosed limitation: storage-shifted clean energy is not credited (charging
counts as consumption of what charged it; discharge is not clean supply).

Returns None (the route's 204) without a commercial config or before a solve.
Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from services.commercial import lp_bindings as _lp
from services.commercial import settlement_inputs as _SI

_NOTES = ["storage_shifted_clean_not_credited"]


def _frame(n, attr: str, col: str) -> pd.DataFrame | None:
    df = getattr(getattr(n, attr), col, None)
    return None if df is None or df.empty else df


def compute_cfe_score(n, cfg, *, result_df: Callable[..., Any] | None = None,
                      clean_carriers: list[str] | None = None) -> dict | None:
    commercial = getattr(cfg, "commercial", None)
    p = _frame(n, "generators_t", "p")
    if not commercial or p is None:
        return None
    c = _lp._parse(commercial)
    flags: list[str] = []
    notes = list(_NOTES)
    site_buses, _ = _lp._meter_sides(n, c)
    sns = n.snapshots
    w = n.snapshot_weightings.objective.reindex(sns).to_numpy(dtype=float)

    # Loads behind the meter (served power; the set point before a solve).
    loads = [str(l) for l in n.loads.index if str(n.loads.at[l, "bus"]) in site_buses]
    served = _frame(n, "loads_t", "p")
    load_mw = np.zeros(len(sns))
    for l in loads:
        if served is not None and l in served.columns:
            col = served[l].reindex(sns).to_numpy(dtype=float)
            if np.isnan(col).any():
                flags.append(f"load_not_established:{l}")   # never a silent 0 (F12)
            load_mw += np.nan_to_num(col)
        else:
            ps = _frame(n, "loads_t", "p_set")
            load_mw += (ps[l].reindex(sns).to_numpy(dtype=float) if ps is not None
                        and l in ps.columns else float(n.loads.at[l, "p_set"] or 0.0))

    # Clean carriers.
    if clean_carriers is None:
        co2 = (n.carriers["co2_emissions"] if "co2_emissions" in n.carriers.columns
               else pd.Series(dtype=float))
        clean = {str(k) for k, v in co2.items() if float(v) == 0.0}
    else:
        clean = set(clean_carriers)
    unknown = sorted({str(n.generators.at[g, "carrier"]) for g in n.generators.index}
                     - set(map(str, n.carriers.index)))
    if unknown and clean_carriers is None:
        notes.append(f"carriers_not_defined_counted_not_clean:{','.join(unknown)}")

    def gen(names) -> np.ndarray:
        out = np.zeros(len(sns))
        for g in names:
            if g in p.columns:
                out += p[g].reindex(sns).fillna(0.0).to_numpy(dtype=float).clip(min=0.0)
        return out

    onsite = _lp.site_generators(n, c)
    # An on-site asset whose output the site SELLS under a PPA carries its
    # clean attribute away with it (review F12).
    sold = {a for k in c.contracts if k.type == "ppa" and _lp.same_party(k.seller, c.site_party)
            for a in k.asset_ids}
    onsite_clean = [g for g in onsite if str(n.generators.at[g, "carrier"]) in clean
                    and g not in sold]
    if sold & set(onsite):
        notes.append(f"onsite_output_sold_under_ppa_not_credited:{','.join(sorted(sold))}")
    if clean_carriers is None:
        zero = sorted({str(n.generators.at[g, "carrier"]) for g in onsite_clean})
        if zero:
            # PyPSA's default co2_emissions is 0: say which carriers counted as
            # clean on that basis, so a gas unit left at the default shows (F8).
            flags.append(f"clean_by_zero_co2_emissions:{','.join(zero)}")
    total_site = gen(onsite)
    clean_site = gen(onsite_clean)
    p0 = _frame(n, "links_t", "p0")
    exp = (p0[c.export_link].reindex(sns).fillna(0.0).to_numpy(dtype=float).clip(min=0.0)
           if c.export_link and p0 is not None and c.export_link in p0.columns
           else np.zeros(len(sns)))
    share = np.divide(clean_site, total_site, out=np.zeros(len(sns)), where=total_site > 0)
    consumed = clean_site - np.minimum(clean_site, exp * share)

    # Off-site PPAs the site buys, on clean assets.
    offsite = np.zeros(len(sns))
    for k in c.contracts:
        if k.type != "ppa" or not _lp.same_party(k.buyer, c.site_party):
            continue
        assets = list(k.asset_ids)
        if any(a in onsite for a in assets):
            continue   # on-site PPA assets are in on-site generation already
        if any(a not in n.generators.index or str(n.generators.at[a, "carrier"]) not in clean
               for a in assets):
            continue
        if k.kind == "baseload":
            if k.baseload_mw is None:
                flags.append(f"ppa_volume_not_established:{k.id}")
                continue
            offsite += float(k.baseload_mw)
        elif k.kind == "as_consumed_btm":
            continue   # behind the meter by definition: not an off-site volume
        else:
            offsite += gen(assets)
        if k.volume_cap_mwh_per_year is not None:
            notes.append(f"ppa_volume_cap_not_applied:{k.id}")

    # Grid import × its carbon-free share.
    imp = np.zeros(len(sns))
    if p0 is not None:
        for link in _lp.import_links(c):
            if link in p0.columns:
                imp += p0[link].reindex(sns).fillna(0.0).to_numpy(dtype=float).clip(min=0.0)
    cfe, cfe_flags = _SI.grid_cfe_share(n, c)
    if cfe is None:
        flags += cfe_flags
        grid = np.zeros(len(sns))
    else:
        grid = imp * cfe.reindex(sns).to_numpy(dtype=float)

    # Energy per row, summed into local hours, per investment period.
    local = _lp._local_clock(sns, c.timezone)
    hour = np.asarray(local.floor("h").asi8) if local.tz is None else \
        np.asarray(local.asi8 - (local.tz_localize(None).asi8
                                 - local.tz_localize(None).floor("h").asi8))
    multi = isinstance(sns, pd.MultiIndex)
    period = np.asarray(sns.get_level_values(0)) if multi else np.full(len(sns), None)
    frame = pd.DataFrame({"p": period, "h": hour, "load": load_mw * w,
                          "onsite": consumed * w, "offsite": offsite * w, "grid": grid * w})
    per_period: dict = {}
    groups = list(frame.groupby("p", sort=True)) if multi else [(None, frame)]
    for p_key, part in groups:
        hourly = part.groupby("h")[["load", "onsite", "offsite", "grid"]].sum()
        clean_h = hourly["onsite"] + hourly["offsite"] + hourly["grid"]
        load_total = float(hourly["load"].sum())
        matched = float(np.minimum(hourly["load"], clean_h).sum())
        key = "_" if p_key is None else str(int(p_key))
        if load_total <= 0:
            flags.append(f"no_site_load:{key}")
        unknown_load = any(f.startswith("load_not_established:") for f in flags)
        per_period[key] = {
            "score": None if load_total <= 0 or unknown_load else matched / load_total,
            "load_mwh": load_total, "clean_mwh": float(clean_h.sum()),
            "matched_mwh": matched,
            "onsite_clean_consumed_mwh": float(hourly["onsite"].sum()),
            "offsite_ppa_mwh": float(hourly["offsite"].sum()),
            "grid_clean_mwh": float(hourly["grid"].sum()),
            "hours": int(len(hourly)),
        }
    return {"per_period": per_period, "flags": sorted(set(flags)), "notes": notes,
            "clean_carriers": sorted(clean), "site_buses": sorted(site_buses),
            "site_loads": loads, "onsite_clean_generators": onsite_clean}
