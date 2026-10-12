"""
Storage LCOS (owner decision 6; IC U1 follow-up): one definition for the
expert workbench and the guided study.

    LCOS = (PV of the storage asset's capex + replacements + its own O&M
            + the cost of the energy charged) / PV of the energy discharged

per owner storage asset and in total (Σ numerators / Σ denominators), at the
case's `wacc_nominal`, end-of-year discounting with index 0 (the financial
close) undiscounted — the engine's convention (SAM). The real LCOS discounts
the energy at the real rate (1 + WACC) / (1 + inflation) − 1, as the LCOE
does; on a `price_basis="real"` case the cash is already real and the real
LCOS is the nominal one.

The terms, all from the `FinanceCase` (the engine reads no network, plan C1):

* capex — the asset's share of the installed capex (its `overnight_cost` ×
  (1 + contingency), phased like the case's), and its own replacements from
  the one schedule (`replacements.schedule`: the `replacement_capex` entries,
  or its parts under `part_lifetimes` — IC S0b plan S4), escalated as the cash
  engine books them;
* O&M — the asset's own template lines (`StorageYear.om_keys`: fom, vom),
  escalated and degraded as the operating cash has them;
* charging — `StorageYear.charge_import_cost` escalated with `tariff` and
  `charge_surplus_cost` with `export`, from the line's money year; the
  adapter priced the charged energy at what the site actually paid for it in
  the dispatch (see `CHARGING_BASIS`);
* discharge — `StorageYear.discharge_mwh`;
* the remaining-life value — under the `remaining_life_annuity` terminal
  value only (IC S0b plan S6), the asset's own per-part terms at the last
  operating year, netted from its capex (GS's LCOS nets the inverter salvage,
  `proforma.py:536-542`); `pv_terminal` is None under the other methods.

Throughput (discharge and charging cost) scales with the asset's degradation
(`degradation_by_asset`, first order: a faded battery cycles less energy);
an asset with no entry is not established, as for a generator's energy
(plan C5, C12). Not a tax or financing metric: no tax, debt or incentive is in
it. A lease or contract on the asset is not O&M and is not counted.

Every unknown term makes the asset's LCOS None with a reason
(`lcos_not_established:<reason>` joins the run's flags), never a 0. A real
LCOS with no inflation stated is None with `real_not_established:
inflation_missing` in the block's reasons (flag
`lcos_real_not_established:inflation_missing`). A case
without storage reads `reasons == ["no_storage"]` and adds no flag.
"""
from __future__ import annotations

import numpy as np

from services.finance.case import FinanceCase
from services.finance.cashflow import Operating, _templates_by_year, degradation_factor
from services.finance.replacements import on_axis, remaining_life_terms, schedule
from services.finance.timeline import Timeline

LCOS_BASIS = ("(PV of the storage asset's capex + replacements + its own O&M + the cost of the "
              "energy charged) / PV of the energy discharged, at the WACC, index 0 (the "
              "financial close) undiscounted; per storage asset and in total; under the "
              "remaining_life_annuity terminal value, the capex is net of the PV of the asset's "
              "own remaining-life terms at the last operating year (as GS's LCOS nets the "
              "inverter salvage)")
CHARGING_BASIS = (
    "charging energy priced at what the site actually paid for it in the dispatch: per "
    "interval, the share of the charge the site imported (up to its import) at that "
    "interval's committed import price (the tariff's per-kWh items materialised on the PoC "
    "plus the grid supply's price); the rest came from on-site surplus (PV) and is priced at "
    "the export revenue the site forwent (the committed net export price; 0 with no export "
    "route, where the surplus would have been curtailed)")


def _pv(arr: np.ndarray, rate: float) -> float:
    return float(np.sum(arr / (1.0 + rate) ** np.arange(len(arr))))


def storage_lcos(case: FinanceCase, op: Operating, tl: Timeline) -> dict:
    """The LCOS block: `assets` {name: {...}}, the totals, `reasons`, `flags`,
    the basis statements and the discount rate."""
    fin = case.inputs
    names = sorted({a for t in case.templates for a in t.storage})
    out: dict = {"assets": {}, "lcos_nominal_per_mwh": None, "lcos_real_per_mwh": None,
                 "reasons": [], "flags": [], "basis": LCOS_BASIS,
                 "charging_basis": CHARGING_BASIS, "discount_rate": fin.wacc_nominal,
                 "price_basis": fin.price_basis}
    if not names:
        out["reasons"] = ["no_storage"]
        return out
    r = fin.wacc_nominal
    real_rate = None
    flags: list[str] = []
    if fin.price_basis == "real":
        real_rate = r
        flags.append("lcos_real_equals_nominal:real_basis")
    elif fin.inflation is None:
        flags.append("lcos_real_not_established:inflation_missing")
    elif r is not None:
        real_rate = (1.0 + r) / (1.0 + fin.inflation) - 1.0
    by_year = _templates_by_year(case, tl)
    k = tl.operating_k()
    opm = tl.operating_mask()
    n = tl.n
    assets = {a.name: a for a in case.assets}
    total_overnight = sum(a.overnight_cost for a in case.assets if a.overnight_cost is not None)
    r_capex = fin.escalation.get("capex")
    rla = fin.terminal_value.method == "remaining_life_annuity"
    tv_terms, tv_reasons = remaining_life_terms(case, tl) if rla else ((), [])
    all_reasons: list[str] = []
    num_tot = den_tot = den_real_tot = 0.0
    established = True
    for a in names:
        rs: list[str] = []
        if r is None:
            rs.append("wacc_nominal_missing")
        spec = fin.degradation_by_asset.get(a)
        f = None if spec is None else degradation_factor(spec, k)
        if f is None:
            rs.append(f"degradation_missing:{a}")
        dis, charge, om = np.zeros(n), np.zeros(n), np.zeros(n)
        dis_raw = charge_raw = 0.0
        keys: set[str] = set()
        for i, y in enumerate(tl.years):
            t = by_year[i]
            sy = t.storage.get(a) if (opm[i] and t is not None) else None
            if sy is None:
                continue
            keys |= set(sy.om_keys)
            if dis_raw == 0.0:
                dis_raw, charge_raw = sy.discharge_mwh, sy.charge_mwh
            if f is None:
                continue
            my = sy.money_year if sy.money_year is not None else \
                (t.money_year if t.money_year is not None else case.base_year)
            dis[i] = sy.discharge_mwh * f[i]
            if sy.charge_import_cost is None or sy.charge_surplus_cost is None:
                rs.append(f"charging_cost_not_established:{a}")
                continue
            c = 0.0
            for amount, cls in ((sy.charge_import_cost, "tariff"),
                                (sy.charge_surplus_cost, "export")):
                if amount == 0.0:
                    continue
                g = fin.escalation.get(cls)
                if g is None:
                    rs.append(f"escalation_missing:{cls}")
                    continue
                c += amount * (1.0 + g) ** (int(y) - my)
            charge[i] = c * f[i]
        for key in sorted(keys):
            arr = op.lines.get(key)
            if arr is None:
                rs.append(f"om_not_established:{key}")
                continue
            om += np.clip(-arr, 0.0, None)
        cap, repl = np.zeros(n), np.zeros(n)
        af = assets.get(a)
        if op.capex is None:
            rs.append("capex_not_established")
        elif af is None or af.overnight_cost is None:
            rs.append(f"overnight_cost_missing:{a}")
        elif total_overnight > 0:
            cap = op.capex * (af.overnight_cost / total_overnight)
        if op.capex is not None:
            for x in schedule(case, tl):             # the one schedule (IC S0b plan S4)
                if x.asset == a and on_axis(tl, x.year):
                    repl[tl.index(x.year)] += x.amount * (1.0 + (r_capex or 0.0)) ** \
                        (x.year - tl.base_year)
        # Under `remaining_life_annuity` the asset's own remaining-life terms
        # are netted from its capex (IC S0b plan S6, GS's LCOS); not otherwise.
        tv = None
        if rla:
            mine = [x for x in tv_reasons
                    if x in ("terminal_needs_lp_rate", "escalation_missing:capex")
                    or x.split(":")[1:2] == [a]]
            rs += mine
            tv = np.zeros(n)
            tv[n - 1] = sum(t.value for t in tv_terms if t.asset == a)
        rs = sorted(set(rs))
        rec: dict = {"lcos_nominal_per_mwh": None, "lcos_real_per_mwh": None,
                     "discharge_mwh_year1": dis_raw, "charge_mwh_year1": charge_raw,
                     "pv_capex": None, "pv_replacement": None, "pv_terminal": None, "pv_om": None,
                     "pv_charging_cost": None, "pv_discharge_mwh": None, "reasons": rs}
        if r is not None:
            rec.update(pv_capex=_pv(cap, r), pv_replacement=_pv(repl, r), pv_om=_pv(om, r),
                       pv_charging_cost=_pv(charge, r), pv_discharge_mwh=_pv(dis, r),
                       pv_terminal=None if tv is None else _pv(tv, r))
            if not rs and rec["pv_discharge_mwh"] <= 0.0:
                rs.append(f"no_discharge:{a}")
        if not rs:
            num = (rec["pv_capex"] + rec["pv_replacement"] - (rec["pv_terminal"] or 0.0)
                   + rec["pv_om"] + rec["pv_charging_cost"])
            rec["lcos_nominal_per_mwh"] = num / rec["pv_discharge_mwh"]
            num_tot += num
            den_tot += rec["pv_discharge_mwh"]
            if real_rate is not None:
                den_real = _pv(dis, real_rate)
                rec["lcos_real_per_mwh"] = num / den_real if den_real > 0 else None
                den_real_tot += den_real
        else:
            established = False
        all_reasons += rs
        out["assets"][a] = rec
    nominal_reasons = sorted(set(all_reasons))
    # The real LCOS's own reason joins the block's reasons (the headline reads
    # them) but never the nominal `lcos_not_established:*` flags.
    real_reasons = ["real_not_established:inflation_missing"] \
        if "lcos_real_not_established:inflation_missing" in flags else []
    out["reasons"] = nominal_reasons + real_reasons
    if established and den_tot > 0:
        out["lcos_nominal_per_mwh"] = num_tot / den_tot
        if real_rate is not None and den_real_tot > 0:
            out["lcos_real_per_mwh"] = num_tot / den_real_tot
    out["flags"] = sorted(set(flags + [f"lcos_not_established:{x}" for x in nominal_reasons]))
    return out


__all__ = ["storage_lcos", "LCOS_BASIS", "CHARGING_BASIS"]
