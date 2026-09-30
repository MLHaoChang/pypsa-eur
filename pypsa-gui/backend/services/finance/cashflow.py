"""
Operating cashflows over the finance axis (IC P4 plan WP4.1; C3–C6, C12, C14).

From a `FinanceCase`: each template line escalated from the base year by its
class (or its contract's own indexation), scaled by its asset's degradation,
stopped after its contract's tenor; capex over the construction years with
contingency and phasing; replacement capex; the terminal value; EBITDA.
Every array is indexed by `Timeline.years` (index 0 = the financial-close
year). A value that cannot be established is None with a reason — never a
substituted 0 (ADR-0001).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from models.finance import ESCALATION_CLASSES
from services.finance.case import CONTRACT_CLASS, FinanceCase, Template, TemplateLine
from services.finance.timeline import Timeline

# The escalation class of each ledger stream (plan WP4.1). Contract streams
# use the contract's own indexation (CONTRACT_CLASS), else `ppa`.
STREAM_CLASS: dict[str, str] = {
    "energy_import": "tariff", "network_capacity": "tariff", "network_energy": "tariff",
    "demand_charge": "tariff", "retail_fixed": "tariff", "certificates": "tariff",
    "tax": "tariff",                       # levies (P3 pin: deductible opex)
    "energy_export": "export", "ancillary": "export",
    "ppa_settlement": CONTRACT_CLASS, "cfd_settlement": CONTRACT_CLASS, "lease": CONTRACT_CLASS,
    "eaas_fee": CONTRACT_CLASS, "dr_availability": CONTRACT_CLASS, "dr_activation": CONTRACT_CLASS,
    "fom": "opex", "vom": "opex", "other": "opex",
    "fuel": "fuel", "capex": "capex",
}


def esc_class_for(stream: str) -> str:
    """The escalation class of a ledger stream (`STREAM_CLASS`); `incentive`
    and `debt_service` are finance-side and have none (ValueError)."""
    try:
        return STREAM_CLASS[stream]
    except KeyError:
        raise ValueError(f"no escalation class for stream {stream!r}") from None


@dataclass
class Operating:
    tl: Timeline
    lines: dict[str, np.ndarray | None]
    line_meta: dict[str, TemplateLine]
    revenue: np.ndarray | None
    costs: np.ndarray | None
    ebitda: np.ndarray | None                  # operating revenue − costs + terminal value
    capex: np.ndarray | None                   # initial capex (construction years; or y0)
    replacement: np.ndarray                    # replacement capex (escalated)
    terminal: np.ndarray | None
    energy_mwh: dict[str, np.ndarray | None]   # per asset, degraded (None = not established)
    status: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def established(self, section: str) -> bool:
        return self.status.get(section) == "ok"


def _templates_by_year(case: FinanceCase, tl: Timeline) -> list[Template | None]:
    """The template in force per axis year (plan C3): the latest whose first
    year is ≤ y; years before the first template use the first."""
    ts = sorted(case.templates, key=lambda t: t.first_year)
    out: list[Template | None] = []
    for y in tl.years:
        cur = ts[0]
        for t in ts:
            if t.first_year <= y:
                cur = t
        out.append(cur)
    return out


def degradation_factor(spec, k: np.ndarray) -> np.ndarray:
    """The generation factor per operating year k (k = 0: not operating → 0).
    A constant rate d: (1 − d)^(k − 1) (SAM, verified). A list: annual STEPS —
    entry j is the loss from operating year j+1 to j+2, compounded, the last
    entry repeating (so year 1 is 1.0). This is not SAM's schedule semantics
    (SAM's list is a cumulative loss vs nameplate incl. year 1); stated in the
    `FinanceInputs.degradation_by_asset` docs (WP4.1 review #8)."""
    k = np.asarray(k, dtype=int)
    out = np.zeros(len(k))
    if isinstance(spec, list):
        steps = np.array(spec, dtype=float)
        kmax = int(k.max()) if len(k) else 0
        cum = [1.0]
        for m in range(1, max(kmax, 1)):
            cum.append(cum[-1] * (1.0 - steps[min(m - 1, len(steps) - 1)]))
        for i, kk in enumerate(k):
            if kk > 0:
                out[i] = cum[kk - 1]
        return out
    d = float(spec)
    return np.where(k > 0, (1.0 - d) ** np.maximum(k - 1, 0), 0.0)


def build_operating(case: FinanceCase, tl: Timeline) -> Operating:
    """Operating cash, capex, replacement, terminal value and EBITDA over the
    axis (plan WP4.1). Unknown inputs make their section not established with
    the reason (plan C12); flags carry disclosures (`contract_ends:<id>:<year>`)."""
    fin = case.inputs
    years, k = tl.years, tl.operating_k()
    op = tl.operating_mask()
    reasons: dict[str, list[str]] = {"operating": [], "capex": [], "terminal": []}
    flags: list[str] = []
    lines: dict[str, np.ndarray | None] = {}
    meta: dict[str, TemplateLine] = {}
    energy: dict[str, np.ndarray | None] = {}
    by_year = _templates_by_year(case, tl)
    line_by_year = [({ln.key: ln for ln in t.lines} if t is not None else {}) for t in by_year]
    money_year = [(t.money_year if t.money_year is not None else case.base_year) for t in by_year]
    deg_cache: dict[str | None, np.ndarray | None] = {None: np.where(op, 1.0, 0.0)}

    def degr(asset: str | None) -> np.ndarray | None:
        if asset not in deg_cache:
            spec = fin.degradation_by_asset.get(asset)
            deg_cache[asset] = None if spec is None else degradation_factor(spec, k)
        return deg_cache[asset]

    def rate_of(line: TemplateLine) -> tuple[float | None, str]:
        if line.esc_class == CONTRACT_CLASS:
            if line.indexation is not None:
                return line.indexation, "contract"
            return fin.escalation.get("ppa"), "ppa"
        if line.esc_class not in ESCALATION_CLASSES:
            raise ValueError(f"unknown escalation class {line.esc_class!r}")
        return fin.escalation.get(line.esc_class), line.esc_class

    keys: dict[str, TemplateLine] = {}
    for t in case.templates:
        for ln in t.lines:
            keys.setdefault(ln.key, ln)
    ends: dict[str, int] = {}
    for key, first in keys.items():
        arr = np.zeros(tl.n)
        ok = True
        for i, y in enumerate(years):
            if not op[i]:
                continue
            ln = line_by_year[i].get(key)
            if ln is None or ln.amount == 0.0:
                continue
            if ln.tenor_years is not None:
                if k[i] > ln.tenor_years:
                    continue
                if ln.tenor_years < tl.analysis_years:
                    ends[ln.contract_id or key] = tl.cod_year + ln.tenor_years - 1
            if ln.amount is None:
                reasons["operating"].append(f"line_not_established:{key}")
                ok = False
                break
            r, rclass = rate_of(ln)
            if r is None:
                reasons["operating"].append(f"escalation_missing:{rclass}:{key}")
                ok = False
                break
            f_deg = degr(ln.degrades_with)
            if f_deg is None:
                reasons["operating"].append(f"degradation_missing:{ln.degrades_with}")
                ok = False
                break
            arr[i] = ln.amount * (1.0 + r) ** (int(y) - money_year[i]) * f_deg[i]
        lines[key] = arr if ok else None
        meta[key] = first
    flags.extend(f"contract_ends:{cid}:{year}" for cid, year in sorted(ends.items()))
    # Generation per asset (degraded) — the PTC and LCOE read it. An asset
    # without a degradation entry is not established (plan C5, C12).
    assets_e = sorted({a for t in case.templates for a in t.energy_mwh})
    for a in assets_e:
        f = degr(a)
        if f is None:
            energy[a] = None
            reasons["operating"].append(f"degradation_missing:{a}")
            continue
        arr = np.zeros(tl.n)
        for i in range(tl.n):
            t = by_year[i]
            if op[i] and t is not None:
                arr[i] = t.energy_mwh.get(a, 0.0) * f[i]
        energy[a] = arr
    reasons["operating"] = sorted(set(reasons["operating"]))

    if reasons["operating"]:
        revenue = costs = None
    else:
        vals = [v for v in lines.values() if v is not None]
        revenue = np.sum([np.clip(v, 0, None) for v in vals], axis=0) if vals else np.zeros(tl.n)
        costs = np.sum([np.clip(-v, 0, None) for v in vals], axis=0) if vals else np.zeros(tl.n)

    # Capex (plan C6).
    capex: np.ndarray | None = np.zeros(tl.n)
    total = 0.0
    for a in case.assets:
        if a.overnight_cost is None:
            reasons["capex"].append(f"overnight_cost_missing:{a.name}")
        else:
            total += a.overnight_cost
    if fin.contingency_share is None:
        reasons["capex"].append("contingency_share_missing")
    if reasons["capex"]:
        capex = None
    else:
        total *= 1.0 + fin.contingency_share
        idx = tl.construction_years or [tl.y0]
        for y, share in zip(idx, fin.capex_phasing):
            capex[tl.index(y)] += total * share
    replacement = np.zeros(tl.n)
    r_capex = fin.escalation.get("capex")
    for year, asset, amount in fin.replacement_capex:
        if not (tl.cod_year <= year < tl.cod_year + tl.analysis_years):
            reasons["capex"].append(f"replacement_outside_axis:{asset}:{year}")
            continue
        if r_capex is None:
            reasons["capex"].append("escalation_missing:capex")
            continue
        replacement[tl.index(year)] += amount * (1.0 + r_capex) ** (year - tl.base_year)
    if any(r.startswith(("replacement_outside_axis", "escalation_missing")) for r in reasons["capex"]):
        capex = None
    reasons["capex"] = sorted(set(reasons["capex"]))

    # Terminal value at the last operating year (plan WP4.1; SAM salvage =
    # `fixed`, not escalated, inside EBITDA and taxed).
    terminal: np.ndarray | None = np.zeros(tl.n)
    tv = fin.terminal_value
    last = tl.n - 1
    if tv.method == "fixed":
        if tv.value is None:
            reasons["terminal"].append("terminal_value_missing")
            terminal = None
        else:
            terminal[last] = tv.value
    elif tv.method == "multiple_of_ebitda":
        if tv.value is None or revenue is None:
            reasons["terminal"].append("terminal_value_missing" if tv.value is None
                                       else "terminal_needs_operating")
            terminal = None
        else:
            terminal[last] = tv.value * float(revenue[last] - costs[last])
    elif tv.method == "book_value":
        # The remaining tax basis: filled by the tax engine (WP4.3a).
        terminal = None
        reasons["terminal"].append("book_value_needs_tax_basis")

    ebitda = None
    if revenue is not None and terminal is not None:
        ebitda = revenue - costs + terminal
    status = {s: ("ok" if not reasons[s] else "not_established") for s in reasons}
    return Operating(tl=tl, lines=lines, line_meta=meta, revenue=revenue, costs=costs,
                     ebitda=ebitda, capex=capex, replacement=replacement, terminal=terminal,
                     energy_mwh=energy, status=status, reasons=reasons, flags=flags)
