"""
The guided study's ONE seam onto the Investment Case engine (U2).

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md §2
(C4, C5), §3.2 (`RatingResult` + the export line → `Bill`), WP5.

Every bill the guided face shows is IC's: the tariff engine through the
frozen facade's `billing.bill_site` (the solved PoC meter) or
`billing.rate_meter` (a meter series without a solve: the intake preview on
the unsolved baseline pack, the baseline at a perturbed tariff), plus the
value-flow export line (`cost_rows.commercial_cost_terms`'s
`block["energy_export"]`, cross-checked with `value_flows.export_revenue`).
The adapter only MAPS the engine's figures onto GS's `Bill` view shape; it
prices nothing itself, and a figure the engine cannot give is null with a
flag (ADR-0001).

Stage 1 (WP5): `bill` / `bill_meter`. The GS `BillCalculator` path keeps
running in parallel until the switch-over (WP6 LP, WP8 findings / runner /
routes); the case (`option_case`, WP7) and the workbook (WP9) follow.
"""
from __future__ import annotations

import calendar
import contextlib
import math
import re
from collections.abc import Mapping

import numpy as np
import pandas as pd

from models.study import Bill, BillComponents, Fidelity
from services.study import compile as C

__all__ = [
    "BILL_COMPONENTS", "BILL_COMPONENT_KEYS", "KIND_COMPONENT", "bill", "bill_meter",
    "component_of",
]

#: The guided bill's seven components and their labels (moved from
#: `proforma.BILL_COMPONENTS`; owner decision 7 adds "Taxes & levies").
BILL_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("energy", "Energy charges"),
    ("demand", "Demand charge on peak import"),
    ("capacity", "Capacity charge"),
    ("fixed", "Fixed charges"),
    ("network", "Network charges"),
    ("export_credit", "Export credit"),
    ("taxes_levies", "Taxes & levies"),
)
BILL_COMPONENT_KEYS: tuple[str, ...] = tuple(k for k, _ in BILL_COMPONENTS)
#: An item the compiler did not name (an Expert's) is mapped by its kind
#: (§3.2 "no clean mapping" 2; decision 7 for `tax_levy` / `certificate`).
KIND_COMPONENT: Mapping[str, str] = {
    "energy": "energy", "demand": "demand", "capacity": "capacity", "fixed": "fixed",
    "tax_levy": "taxes_levies", "certificate": "taxes_levies"}

# The adapter's own guards (§3.2 "Why findings.STREAMS ... stay correct").
if BILL_COMPONENT_KEYS != C.BILL_COMPONENT_KEYS:
    raise ImportError("engine_adapter.BILL_COMPONENTS must be compile's seven keys")
if tuple(BillComponents._figure_fields) != BILL_COMPONENT_KEYS:
    raise ImportError("models.study.BillComponents must carry the seven bill components")
if not set(KIND_COMPONENT.values()) <= set(BILL_COMPONENT_KEYS):
    raise ImportError("KIND_COMPONENT maps outside the seven bill components")

_ONE_YEAR_HOURS = (8760.0, 8784.0)
_SUM_TOL = 1e-9
_CODE_RE = re.compile(r"[a-z]+(_[a-z]+)*")
# IC note → the guided bill's digit-free code (§3.2 `honesty_notes`).
_NOTE_MAP = {"capacity_prorated_by_represented_hours": "capacity_charge_prorated_by_hours"}


def component_of(item, item_component: Mapping[str, str]) -> str:
    """
    The bill component a tariff item is: by the compiled id first, a
    `network:*` id next, an export revenue item to `export_credit`, else by
    kind.
    """
    if item.id in item_component:
        return item_component[item.id]
    if item.id.startswith(C.NETWORK_PREFIX):
        return "network"
    if item.direction == "revenue" or item.measured_on == "export":
        return "export_credit"
    return KIND_COMPONENT[item.kind]


def _needs_import(item) -> bool:
    """
    Whether the item's quantity is the import meter (its component is not
    established without one); a fixed item or a contracted capacity is not.
    """
    if item.kind == "fixed":
        return False
    if item.kind == "capacity":
        return item.measured_on == "peak_import"
    return item.measured_on in ("import", "net", "peak_import")


def _weights(n) -> pd.Series:
    return n.snapshot_weightings.objective.astype(float)


def _partial_months(n) -> list[str]:
    """Calendar months the snapshots cover for fewer hours than they last."""
    w = _weights(n)
    idx = pd.DatetimeIndex(n.snapshots)
    covered = w.groupby(idx.strftime("%Y-%m")).sum()
    out = []
    for label, hours in covered.items():
        y, m = int(label[:4]), int(label[5:])
        if float(hours) < calendar.monthrange(y, m)[1] * 24.0 - 1e-6:
            out.append(str(label))
    return out


def _notes(res, partial: list[str], has_fixed: bool) -> tuple[str, ...]:
    out: list[str] = []
    for codes in (res.notes or {}).values():
        for code in codes:
            if code.startswith("resolution:"):
                code = "bill_resolution_differs_from_settlement"
            code = _NOTE_MAP.get(code, code)
            if not _CODE_RE.fullmatch(code):
                head = code.split(":", 1)[0]
                if not _CODE_RE.fullmatch(head):
                    continue
                code = head
            out.append(code)
    if partial and has_fixed:
        # IC pro-rates a partial month's fixed items by its covered hours.
        out.append("fixed_charge_prorated_on_partial_period")
    return tuple(sorted(dict.fromkeys(out)))


def _itemised(compiled: C.CompiledCommercial, items) -> list[str]:
    """
    The components the compiled tariff carries an item for, plus the export
    credit when the config prices export (the value-flow line).
    """
    found = {component_of(i, compiled.item_component) for i in items}
    if compiled.config.export_price_ref is not None or "export_series_not_minted" in compiled.notes:
        found.add("export_credit")
    return [k for k in BILL_COMPONENT_KEYS if k in found]


def _bill_notes(compiled: C.CompiledCommercial, res, partial: list[str],
                has_fixed: bool) -> tuple[str, ...]:
    """
    The engine's notes as codes, plus the compile notes a bill must carry
    (gate U2-S1 C2: a capacity charge billed on the assumed connection size).
    """
    out = list(_notes(res, partial, has_fixed)) if res is not None else []
    if C.CAPACITY_ASSUMED_CONNECTION in compiled.notes:
        out.append(C.CAPACITY_ASSUMED_CONNECTION)
    return tuple(sorted(dict.fromkeys(out)))


def _flag_codes(flags) -> str:
    """`SiteBill.flags` as one `unavailable["total"]` value (digit-free heads)."""
    heads = []
    for f in flags:
        head = str(f).split(":", 1)[0]
        if head not in heads:
            heads.append(head)
    return "+".join(heads)


def _bill(n, compiled: C.CompiledCommercial, site_bill, *, export_credit: float | None,
          export_flag: str | None, import_missing: bool = False,
          fidelity: Fidelity | str | None = None) -> Bill:
    """`SiteBill` (one flat period) + the export line → GS's `Bill` (§3.2)."""
    cfg = compiled.config
    items = list(cfg.import_tariff.items) if cfg.import_tariff is not None else []
    comp: dict[str, float | None] = {k: 0.0 for k in BILL_COMPONENT_KEYS}
    flags: dict[str, str] = {}
    res = site_bill.per_period.get(None) if site_bill.per_period else None
    for comp_key in set(compiled.item_component.values()):
        if comp_key not in BILL_COMPONENT_KEYS:
            raise ValueError(f"compiled item component {comp_key!r} is not a bill component")

    def null(key: str, code: str) -> None:
        comp[key] = None
        flags.setdefault(key, code)

    for item in items:
        key = component_of(item, compiled.item_component)
        if import_missing and _needs_import(item):
            null(key, "no_import_series")
            continue
        if res is None:
            null(key, "period_not_billed")
            continue
        if item.id in (res.unsupported_items or []):
            null(key, "item_unsupported")
            continue
        v = res.per_item.get(item.id)
        if v is None or not math.isfinite(float(v)):
            null(key, "item_not_rated")
            continue
        if comp[key] is not None:
            comp[key] = float(comp[key]) + float(v)

    # The value-flow export line (not a tariff item): summed with any export
    # revenue item the tariff carries.
    if export_credit is None:
        null("export_credit", export_flag or "export_line_not_established")
    elif comp["export_credit"] is not None:
        comp["export_credit"] = float(comp["export_credit"]) + float(export_credit)
    for k, v in comp.items():
        if v is not None:
            comp[k] = float(v) + 0.0          # no `-0.0` in JSON

    components = BillComponents(**comp, unavailable=flags)
    bill_flags: dict[str, str] = {}
    total: float | None = None
    if flags:
        bill_flags["total"] = "component_unavailable"
    elif site_bill.flags:
        bill_flags["total"] = _flag_codes(site_bill.flags)
    else:
        total = float(sum(comp.values()))
        engine_total = None if res is None or res.total is None else \
            float(res.total) + float(export_credit or 0.0)
        if engine_total is None or abs(total - engine_total) > _SUM_TOL * max(1.0, abs(total)):
            total = None
            bill_flags["total"] = "bill_components_do_not_sum"
    hours = float(_weights(n).sum())
    if total is None:
        annual = None
        bill_flags["annual_bill"] = bill_flags["total"]
    elif any(abs(hours - h) < 1e-6 for h in _ONE_YEAR_HOURS):
        annual = total
    else:
        annual = None
        bill_flags["annual_bill"] = "horizon_not_one_year"

    peaks: dict[str, float] = {}
    if res is not None and not import_missing and not res.demand_lines.empty:
        g = res.demand_lines.groupby("month")["peak_kw"].max()
        peaks = {str(m): float(v) / 1000.0 for m, v in g.items() if math.isfinite(float(v))}
    months = [str(m) for m in (res.monthly.index if res is not None else [])]
    if not months:
        months = sorted(set(pd.DatetimeIndex(n.snapshots).strftime("%Y-%m")))
    meta = compiled.tariff_meta
    periods = (sorted({m[:4] for m in months}) if meta.get("billing_period") == "year"
               else months)
    partial = _partial_months(n)
    has_fixed = any(i.kind == "fixed" for i in items)
    return Bill(
        total=total, annual_bill=annual, by_component=components,
        peak_mw_by_billing_period=peaks, billing_periods=periods, horizon_hours=hours,
        currency=meta.get("currency") or "EUR", currency_year=meta.get("currency_year"),
        engine="tariff_engine", unavailable=bill_flags,
        fidelity=None if fidelity is None else Fidelity(fidelity),
        honesty_notes=_bill_notes(compiled, res, partial, has_fixed),
        partial_billing_periods=partial, itemised_components=_itemised(compiled, items))


def _export_line(n, compiled: C.CompiledCommercial) -> tuple[float | None, str | None]:
    """
    The export credit of a dispatched network (negative: a credit): the
    committed `commercial_cost_terms` block's `energy_export` when the solve
    recorded one, else the value-flow ledger's line (`export_revenue`); the
    two must agree to 1e-9 when both exist.
    """
    cfg = compiled.config
    p0 = getattr(n.links_t, "p0", None)
    link = cfg.export_link
    if not link or cfg.export_price_ref is None:
        flow = 0.0 if p0 is None or link not in getattr(p0, "columns", []) else \
            float(np.nansum(np.abs(p0[link].to_numpy(dtype=float))))
        return (0.0, None) if flow == 0.0 else (None, "no_export_price")
    from services.commercial.cost_rows import commercial_cost_terms
    from services.results.value_flows import export_revenue

    rev = export_revenue(n, cfg)
    line = rev.get("_") if rev else None
    if rev and set(rev) != {"_"}:
        return None, "export_line_not_flat"
    if line is None:
        return None, "export_line_not_established"
    line = -float(line)
    try:
        block = commercial_cost_terms(n, compiled.commercial()).get("block") or {}
    except Exception:  # noqa: BLE001 — a network the solve did not record
        block = {}
    committed = block.get("energy_export")
    if committed is None:
        return line, None
    if abs(float(committed) - line) > _SUM_TOL * max(1.0, abs(line)):
        return None, "export_line_cross_check_failed"
    return float(committed), None


def bill(n, compiled: C.CompiledCommercial, *, fidelity: Fidelity | str | None = None) -> Bill:
    """
    The solved PoC meter's bill (C4): `billing.bill_site` on the network's
    dispatch with the compiled config, plus the export line. The network must
    carry the compiled config bound (`compile.bind_on_network`) for its export
    price. A dispatch the engine did not solve with this config (no solve
    record, a config changed since) is billed component by component, but its
    total is not established (`unavailable["total"]` names the engine's flags).
    """
    from services.commercial.billing import bill_site

    sb = bill_site(n, compiled.commercial())
    credit, flag = _export_line(n, compiled)
    return _bill(n, compiled, sb, export_credit=credit, export_flag=flag, fidelity=fidelity)


def _series(x, n) -> np.ndarray:
    if isinstance(x, pd.Series):
        x = x.reindex(n.snapshots)
    arr = np.asarray(x, dtype=float)
    if arr.shape != (len(n.snapshots),):
        raise ValueError(f"a meter series needs one value per snapshot ({len(n.snapshots)})")
    return arr


@contextlib.contextmanager
def _poc_sized_as_built(n, poc: str):
    """
    WORKAROUND (adapter-side; engine ask): `billing.rate_meter` rates capacity
    items on the PoC's `p_nom_opt` when it is finite, and an UNSOLVED network
    carries PyPSA's default `p_nom_opt = 0` — so a contracted capacity of the
    intake preview (the unsolved baseline pack) would bill 0. For a
    non-extendable PoC Link the built size IS `p_nom`; it is lent to
    `p_nom_opt` for the call and restored after. A solved network
    (`p_nom_opt == p_nom`) is untouched.
    """
    links = n.links
    if poc not in links.index or "p_nom_opt" not in links.columns or \
            bool(links.at[poc, "p_nom_extendable"]) or \
            float(links.at[poc, "p_nom_opt"]) == float(links.at[poc, "p_nom"]):
        yield
        return
    saved = links.at[poc, "p_nom_opt"]
    links.at[poc, "p_nom_opt"] = float(links.at[poc, "p_nom"])
    try:
        yield
    finally:
        links.at[poc, "p_nom_opt"] = saved


def bill_meter(n, compiled: C.CompiledCommercial, import_mw, export_mw, *,
               fidelity: Fidelity | str | None = None) -> Bill:
    """
    A meter series' bill WITHOUT a solve (C4): `billing.rate_meter` on the
    network's axis — the intake preview on the unsolved baseline pack
    (import = load, export = 0) and the baseline at a perturbed tariff. An
    absent series nulls the components it feeds (`no_import_series`,
    `no_export_series`). Export is the value-flow line, which needs the
    dispatch on the network: nothing exported is a real 0.0, a non-zero
    export series is not established here (`export_line_needs_the_dispatch`).
    """
    from services.commercial.billing import rate_meter

    zeros = np.zeros(len(n.snapshots))
    imp = zeros if import_mw is None else _series(import_mw, n)
    exp = zeros if export_mw is None else _series(export_mw, n)
    with _poc_sized_as_built(n, compiled.config.poc_link):
        sb = rate_meter(n, compiled.commercial(), imp, exp)
    if export_mw is None:
        credit, flag = None, "no_export_series"
    elif float(np.nansum(np.abs(exp))) == 0.0:
        credit, flag = 0.0, None
    else:
        credit, flag = None, "export_line_needs_the_dispatch"
    return _bill(n, compiled, sb, export_credit=credit, export_flag=flag,
                 import_missing=import_mw is None, fidelity=fidelity)
