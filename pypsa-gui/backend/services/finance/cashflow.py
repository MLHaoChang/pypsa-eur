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
    energy_mwh: dict[str, np.ndarray]          # per asset, degraded
    status: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def established(self, section: str) -> bool:
        return self.status.get(section) == "ok"


def _template_for(case: FinanceCase, year: int) -> Template:
    ts = sorted(case.templates, key=lambda t: t.first_year)
    cur = ts[0]
    for t in ts:
        if t.first_year <= year:
            cur = t
    return cur


def degradation_factor(spec, k: np.ndarray) -> np.ndarray:
    """(1 − d)^(k − 1) for a constant rate; for a list, the product of the
    rates of the years before k (entry j = the step from year j+1 to j+2; the
    last entry repeats). k = 0 (not operating) → 0."""
    k = np.asarray(k, dtype=int)
    out = np.zeros(len(k))
    if isinstance(spec, list):
        steps = np.array(spec, dtype=float)
        for i, kk in enumerate(k):
            if kk <= 0:
                continue
            m = kk - 1
            rates = np.concatenate([steps[:m], np.repeat(steps[-1:], max(0, m - len(steps)))])
            out[i] = float(np.prod(1.0 - rates))
        return out
    d = float(spec)
    return np.where(k > 0, (1.0 - d) ** np.maximum(k - 1, 0), 0.0)


def build_operating(case: FinanceCase, tl: Timeline) -> Operating:
    fin = case.inputs
    years, k = tl.years, tl.operating_k()
    op = tl.operating_mask()
    reasons: dict[str, list[str]] = {"operating": [], "capex": [], "terminal": []}
    flags: list[str] = []
    lines: dict[str, np.ndarray | None] = {}
    meta: dict[str, TemplateLine] = {}
    energy: dict[str, np.ndarray] = {}

    def rate_of(line: TemplateLine) -> float | None:
        if line.esc_class == CONTRACT_CLASS:
            if line.indexation is not None:
                return line.indexation
            return fin.escalation.get("ppa")
        if line.esc_class not in ESCALATION_CLASSES:
            raise ValueError(f"unknown escalation class {line.esc_class!r}")
        return fin.escalation.get(line.esc_class)

    def degr(asset: str | None) -> np.ndarray | None:
        if asset is None:
            return np.where(op, 1.0, 0.0)
        spec = fin.degradation_by_asset.get(asset)
        if spec is None:
            return None
        return degradation_factor(spec, k)

    # Every key over every operating year, from the template in force.
    keys: dict[str, TemplateLine] = {}
    for t in case.templates:
        for ln in t.lines:
            keys.setdefault(ln.key, ln)
    for key, first in keys.items():
        arr = np.zeros(tl.n)
        ok = True
        for i, y in enumerate(years):
            if not op[i]:
                continue
            ln = next((x for x in _template_for(case, int(y)).lines if x.key == key), None)
            if ln is None or ln.amount == 0.0:
                continue
            if ln.amount is None:
                reasons["operating"].append(f"line_not_established:{key}")
                ok = False
                break
            if ln.tenor_years is not None and k[i] > ln.tenor_years:
                continue
            r = rate_of(ln)
            if r is None:
                reasons["operating"].append(f"escalation_missing:{ln.esc_class}:{key}")
                ok = False
                break
            f_deg = degr(ln.degrades_with)
            if f_deg is None:
                reasons["operating"].append(f"degradation_missing:{ln.degrades_with}")
                ok = False
                break
            arr[i] = ln.amount * (1.0 + r) ** (int(y) - tl.base_year) * f_deg[i]
        lines[key] = arr if ok else None
        meta[key] = first
        if first.tenor_years is not None and first.tenor_years < tl.analysis_years:
            flags.append(f"contract_ends:{first.contract_id or key}:"
                         f"{tl.cod_year + first.tenor_years - 1}")
    reasons["operating"] = sorted(set(reasons["operating"]))
    # Generation per asset (degraded) — the PTC and LCOE read it.
    for t in case.templates:
        for a in t.energy_mwh:
            energy.setdefault(a, np.zeros(tl.n))
    for a in energy:
        f = degr(a)
        for i, y in enumerate(years):
            if op[i]:
                e = _template_for(case, int(y)).energy_mwh.get(a, 0.0)
                energy[a][i] = e * (f[i] if f is not None else np.nan)

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
