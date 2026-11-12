# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""The business case: a second engine run on the IC case with the user's line items
(business-case spec §3 G1, §7; GK B BC1–BC8, G-C1..G-C8 and the 05 conditions C1–C9).

`build_business_case` compiles `BusinessCaseInputs` onto the IC case:
`counterfactual=()`, so the business case is the project on its own, with its own
hash domain. The IC case is never changed (BC1, C1) and the two are never added.

- Revenue and opex lines become `TemplateLine`s. The key is the line name, the stream
  comes from its category, the amount is quantity × price, signed from the owner. There
  is one template per timeline year, because a line's grid cells and linked series vary
  by year.
- Capex lines become `AssetFinance`s. They depreciate on the pack class strings
  (`slm_<life>`, `db_<rate>_<life>`), and their refresh years are `replacement_capex`.
- `pnl` reads the P&L from the per-stream operating ledger `result.op`, never from
  `op_incremental` (BC4). Its tax is the engine's (BC5).
- `kpis` and `break_even` read the engine's cash series.

The ledger guards (06, spec §3 G2–G4):

- G2: a linked line whose output a ledger stream already prices is refused
  `double_count:<stream>` (`linked_outputs.ledger_clash`), never warned.
- G3: a revenue line on a product bus's served quantity drops that bus's
  `lost_production` lines in the years it is active (the whole bus for
  `served_product`, the read Load's LO-3 share for `served_h2` and `shipped`), so a
  shortfall counts once, as lower served revenue. The line is never refused.
- G4: no reference-purchase value reaches the business case: it lives on the
  counterfactual side, which `counterfactual=()` removes, and the hash reads none.
- `BusinessCase.locked` lists the ledger lines the case carries, each dropped one
  with the line that superseded it (G3-6), for the view's locked rows.

Not here: presets (07) and the route and tools (08)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.optimize import brentq

from models.business_case import (
    CATEGORY_STREAM,
    BusinessCaseInputs,
    DoubleCount,
    LineItem,
    LinkedOutputMissing,
    NameDuplicate,
)
from services.business_case.linked_outputs import (
    LostProductionMap,
    NotEvaluated,
    Series,
    ledger_clash,
)
from services.finance.case import (
    AssetFinance,
    FinanceCase,
    FinanceRefused,
    TemplateLine,
)
from services.finance.cashflow import _templates_by_year, build_operating
from services.finance.engine import _net, run_case
from services.finance.metrics import npv
from services.finance.report import _fallback_hash
from services.finance.timeline import Timeline, build_timeline

#: `TemplateLine.source` and `AssetFinance.component` of every business-case line.
SOURCE = "business_case"
#: The hash domain (G-C6): never the IC's.
HASH_DOMAIN = "business_case/v1"
#: The stream whose revenue the "net of pass-through" KPIs take out (BC2).
PASS_THROUGH_STREAM = CATEGORY_STREAM[("revenue", "cost_recovery")][0]
#: The break-even re-run: |NPV| at the solved price ≤ this × the line's life revenue (C7).
BREAK_EVEN_TOL = 1e-6
#: brentq's tolerances on the price scale (C7).
BREAK_EVEN_XTOL = 1e-12
#: The bracket (BC8): price scales 0, 1, then × 1.5 up to this; a root is searched only
#: between two trials of opposite sign, never extrapolated.
BREAK_EVEN_MAX_SCALE = 1e4
_SIGN = {"revenue": 1.0, "opex": -1.0}


def _finite(v: float | None) -> float | None:
    """`v`, or None (not established) when it is not a finite number: an overflowed
    quantity × price never reaches the engine (GK B #1404 item 3)."""
    return v if v is not None and math.isfinite(v) else None


@dataclass(frozen=True)
class BusinessCase:
    case: FinanceCase
    hash: str
    #: The ledger lines "from the energy model" the case carries (item 4), read-only.
    locked: tuple[LockedRow, ...] = ()


@dataclass(frozen=True)
class LockedRow:
    """One IC ledger line the business case carries, keyed as the ledger keys it.

    `dropped` holds (year, share) for each year a revenue line supersedes part or all
    of it (G3: share 1.0 is the whole line, None not established), and
    `superseded_by` names those lines, so a €0 sale line can't remove a penalty
    silently (G3-6)."""

    stream: str
    key: str
    dropped: tuple[tuple[int, float | None], ...] = ()
    superseded_by: tuple[str, ...] = ()


# ── compile (G1) ─────────────────────────────────────────────────────────────


def _span(ln: LineItem, horizon: tuple[int, int]) -> tuple[int, int]:
    return ln.start_year or horizon[0], ln.end_year or horizon[1]


def _output_id(ln: LineItem) -> str:
    return ln.quantity_source.split(":", 1)[1].rpartition("@")[0]


def _share_base(ln: LineItem) -> tuple[str, str]:
    kind, _, category = ln.quantity_source.split(":", 1)[1].partition("/")
    return kind, category


def _in_base(b: LineItem, kind: str, category: str) -> bool:
    return b.kind == kind and (not category or b.category == category)


def _linked_q(out: Series | NotEvaluated | None, year: int) -> float | None:
    """A linked line's quantity in `year`: never 0 for a value the solve does not have
    (LO-7)."""
    if not isinstance(out, Series):
        return None
    return out.for_year(year)


def _quantity(ln: LineItem, year: int, outputs: dict) -> float | None:
    if ln.source == "linked":
        q = _linked_q(outputs.get(ln.name), year)
        # The line's sold share of the series (GK B BC 07 P3).
        return None if q is None else q * ln.linked_share_at(year)
    return ln.cell("quantity", year).value


def _refuse_clashes(ic: FinanceCase, inputs: BusinessCaseInputs, outputs: dict) -> None:
    """C2: a line named like an IC template key (or a capex line like an IC asset) is
    `name_duplicate`. A linked line without its output is `linked_output_missing`; one
    whose output the IC ledger already prices is `double_count` (G2, C4)."""
    keys = {ln.key for t in ic.templates for ln in t.lines}
    assets = {a.name for a in ic.assets}
    # A difference line (a CfD, a financial PPA) prices its volume even when it nets to 0.
    valued = {
        ln.stream
        for t in ic.templates
        for ln in t.lines
        if ln.amount != 0.0 or ln.price_qty_mwh is not None
    }
    for i, ln in enumerate(inputs.lines):
        if ln.name in keys or (ln.kind == "capex" and ln.name in assets):
            raise NameDuplicate(f"lines[{i}].name")
        if ln.source != "linked":
            continue
        if ln.name not in outputs:
            raise LinkedOutputMissing(_output_id(ln))
        clash = ledger_clash(_output_id(ln), valued)
        if clash is not None and clash[0] == "refuse":
            e = DoubleCount(f"lines[{i}].quantity_source")
            e.stream = clash[1]
            raise e


# ── lost production (G3) ─────────────────────────────────────────────────────

#: The stream a product bus's shortfall is valued in (G3).
LOST_PRODUCTION = "lost_production"


def _slack_of(ln: TemplateLine) -> str | None:
    """The slack of an `asset:<stream>:<Component>:<name>` ledger line."""
    parts = (ln.source_id or "").split(":", 2)
    return parts[2] if ln.source == "asset" and len(parts) == 3 else None


def _generator_opex(ln: TemplateLine) -> bool:
    """An `asset:opex:Generator:<name>` ledger line: the shape a slack's opex takes."""
    return (ln.source_id or "").startswith("opex:Generator:") and (
        _slack_of(ln) is not None
    )


def _is_lost(ln: TemplateLine, lp: LostProductionMap | None) -> bool:
    """A line that values a product shortfall: the `lost_production` stream, or the
    opex of a slack the map names whatever stream it is booked in. A grid-side slack's
    captured opex is booked `energy_import` (`participants`, `grid_side_asset_cost`),
    so the stream alone would miss it and the sale line would count it twice."""
    if ln.stream == LOST_PRODUCTION:
        return True
    return lp is not None and _slack_of(ln) in lp.slack_bus


def _triggers(lines) -> list[tuple[LineItem, str, str]]:
    """(line, output kind, bus or load) of each line that drops `lost_production`:
    a revenue line linked to served product (L1, L2, L2b). An opex line on the same
    quantity (a royalty per t), a typed line and a share never do (GK A G3-2)."""
    out = []
    for ln in lines:
        if ln.kind != "revenue" or ln.source != "linked":
            continue
        oid = _output_id(ln)
        if ledger_clash(oid, (LOST_PRODUCTION,)) != ("drop", LOST_PRODUCTION):
            continue
        kind, _, target = oid.partition(".")
        out.append((ln, kind, target))
    return out


def _drops(
    ic: FinanceCase,
    tl: Timeline,
    lines,
    horizon: tuple[int, int],
    lp: LostProductionMap | None,
) -> dict[int, dict[str, tuple[float | None, tuple[str, ...]]]]:
    """{year: {ledger key: (share dropped, superseding lines)}} of each
    `lost_production` line a revenue line supersedes that year (G3, GK A G3-1..G3-4).

    Per BC year: a line drops its bus's lost production only in the years it runs,
    so a line covering part of a multi-year period drops only those years' share
    (G3-3, A5). `served_product.<bus>` reads the whole bus: its slacks' lines drop
    whole. `served_h2.<load>` and `shipped.<load>` read one Load: each read Load's
    LO-3 share drops once however many lines read it, capped at the whole (A4). A
    year outside every period of the solve has no share: None (not established, as
    the line's own quantity there). A year with nothing dropped is absent (D4).

    A line selling the share s of what it reads (`linked_share_at`) drops s of that
    shortfall, since a shortfall counts once: the drop is f × s, the shares of lines
    reading the same bus or Load summed and capped at 1. A share of 0 drops nothing
    (GK B ruling on #1430).

    A part drop scales the line's amount by (1 − share): it assumes the shortfall is
    valued at one constant price per unit within the period, as the ledger books it
    (GK A N3).

    Fail closed (D3, A6): a triggering line with valued lost production and no map,
    a line on a bus the map could not read, or a `lost_production` line the map
    cannot place on a bus while a line triggers, is refused `linked_output_missing`.
    A bus with no triggering line keeps its lines and is never refused."""
    trig = _triggers(lines)
    out: dict[int, dict] = {}
    if not trig:
        return out
    for t, y in zip(_templates_by_year(ic, tl), tl.years):
        y = int(y)
        active = [
            x for x in trig if _span(x[0], horizon)[0] <= y <= _span(x[0], horizon)[1]
        ]
        if not active:
            continue
        lost = [ln for ln in t.lines if _is_lost(ln, lp)]
        if lp is None:
            # Without the map a grid-side slack's opex, booked `energy_import`, can't
            # be told from a purchase: any Generator opex line refuses (GK B R).
            if lost or any(_generator_opex(ln) for ln in t.lines):
                raise LinkedOutputMissing(LOST_PRODUCTION)
            continue
        if not lost:
            continue
        whole = {target for _, kind, target in active if kind == "served_product"}
        loads = {target for _, kind, target in active if kind != "served_product"}
        read = whole | {lp.load_bus[ld] for ld in loads if ld in lp.load_bus}
        for bus in sorted(read & set(lp.unread)):
            raise LinkedOutputMissing(f"{LOST_PRODUCTION}.{bus}")
        for ln in lost:
            bus = lp.slack_bus.get(_slack_of(ln))
            if bus is None:
                raise LinkedOutputMissing(f"{LOST_PRODUCTION}.{ln.key}")
            on_bus = [
                (b, kind, target)
                for b, kind, target in active
                if b.linked_share_at(y) > 0.0
                and (
                    target == bus
                    if kind == "served_product"
                    else lp.load_bus.get(target) == bus
                )
            ]
            names = tuple(dict.fromkeys(b.name for b, _, _ in on_bus))
            if not names:
                continue
            # A shortfall counts once (G3): a line selling the share s of what it reads
            # drops s of that shortfall; shares on one read sum, capped at the whole.
            sold: dict[tuple[str, str], float] = {}
            for b, kind, target in on_bus:
                k = ("bus" if kind == "served_product" else "load", target)
                sold[k] = min(1.0, sold.get(k, 0.0) + b.linked_share_at(y))
            f: float | None = sum(s for (k, _), s in sold.items() if k == "bus")
            by_load = lp.share.get(_slack_of(ln), {})
            for (k, ld), s in sorted(sold.items()):
                if k != "load":
                    continue
                sh = by_load.get(ld)
                v = sh.for_year(y) if isinstance(sh, Series) else None
                if v is None or not math.isfinite(v):
                    f = None
                    break
                f += v * s
            if f is not None:
                f = min(f, 1.0)
                if f <= 0.0:
                    continue
            out.setdefault(y, {})[ln.key] = (f, names)
    return out


def _dropped(ln: TemplateLine, f: float | None) -> TemplateLine | None:
    """`ln` less the dropped share `f`: gone at 1, not established at None."""
    if f is None:
        return dataclasses.replace(ln, amount=None)
    if f >= 1.0:
        return None
    if ln.amount is None:
        return ln
    return dataclasses.replace(ln, amount=_finite(ln.amount * (1.0 - f)))


def _locked(ic: FinanceCase, tl: Timeline, drops: dict) -> tuple[LockedRow, ...]:
    rows: dict[str, list] = {}
    for t, y in zip(_templates_by_year(ic, tl), tl.years):
        for ln in t.lines:
            row = rows.setdefault(ln.key, [ln.stream, [], []])
            hit = drops.get(int(y), {}).get(ln.key)
            if hit is not None:
                row[1].append((int(y), hit[0]))
                row[2].extend(hit[1])
    return tuple(
        LockedRow(stream, key, tuple(d), tuple(dict.fromkeys(names)))
        for key, (stream, d, names) in rows.items()
    )


def _depreciation_class(ln: LineItem) -> str:
    """The pack class string of a capex line. A life that is not a whole number of years
    is passed as written. The engine then refuses the class by name and leaves tax not
    established (GK B Q3), never rounded."""
    life = float(ln.life_years)
    years = str(int(life)) if life.is_integer() else repr(life)
    if ln.depreciation == "declining_balance":
        return f"db_{ln.rate:g}_{years}"
    return f"slm_{years}"


def _to_base(v: float | None, money_year: int, base: int, r_capex: float | None):
    """Capex in another money year, in base-year money at the capex rate (GK B Q1). A
    missing rate is not established, never 0 %."""
    if v is None or money_year == base:
        return v
    if r_capex is None:
        return None
    return v * (1.0 + r_capex) ** (base - money_year)


def _operating_line(
    ln: LineItem, amount: float | None, money_year: int, price: float | None
) -> TemplateLine:
    stream = CATEGORY_STREAM[(ln.kind, ln.category)][0]
    return TemplateLine(
        key=ln.name,
        stream=stream,
        amount=amount,
        esc_class=ln.escalation,
        indexation=ln.indexation if ln.escalation == "contract" else None,
        source=SOURCE,
        source_id=ln.name,
        price=price,
        money_year=money_year,
    )


def _templates(
    ic: FinanceCase,
    tl: Timeline,
    by_year: dict[int, list[TemplateLine]],
    *,
    with_ic: bool = True,
    drops: dict | None = None,
) -> tuple:
    """One template per timeline year: the IC template in force that year, so its
    energy, storage and money year stay, less the `lost_production` a line supersedes
    that year (G3), plus that year's business-case lines."""
    out = []
    for t, y in zip(_templates_by_year(ic, tl), tl.years):
        own: tuple = ()
        if with_ic:
            hit = (drops or {}).get(int(y), {})
            own = tuple(
                x
                for x in (
                    _dropped(ln, hit[ln.key][0]) if ln.key in hit else ln
                    for ln in t.lines
                )
                if x is not None
            )
        out.append(
            dataclasses.replace(
                t, first_year=int(y), lines=tuple(own) + tuple(by_year.get(int(y), ()))
            )
        )
    return tuple(out)


def build_business_case(
    ic_case: FinanceCase,
    inputs: BusinessCaseInputs,
    outputs: dict | None = None,
    lost_production: LostProductionMap | None = None,
) -> BusinessCase:
    """The business-case `FinanceCase` (G1), its hash (G-C6) and its locked rows.

    `outputs` maps a linked line's name to its `Series` or `NotEvaluated`. The caller
    resolves them with `linked_outputs.resolve` on the run's own solve, and
    `lost_production` with `linked_outputs.lost_production` on the same solve (G3).
    A refusal is raised typed, naming the line and column (G9)."""
    outputs = outputs or {}
    tl = build_timeline(ic_case)
    ops = tl.operating_years
    horizon = (ops[0], ops[-1])
    base = ic_case.base_year
    inputs.require_runnable(horizon=horizon, base_year=base)
    _refuse_clashes(ic_case, inputs, outputs)
    drops = _drops(ic_case, tl, inputs.lines, horizon, lost_production)
    fin = ic_case.inputs
    r_capex = fin.escalation.get("capex")
    lines = inputs.lines

    # Revenue and opex read from typed or linked quantities.
    by_year: dict[int, list[TemplateLine]] = {}
    for ln in lines:
        if ln.kind == "capex" or ln.source == "share_of":
            continue
        first, last = _span(ln, horizon)
        for y in ops:
            if not first <= y <= last:
                continue
            q, pc = _quantity(ln, y, outputs), ln.cell("price", y)
            amount = _finite(
                None if q is None or pc.value is None else _SIGN[ln.kind] * q * pc.value
            )
            # A grid cell is that year's price: no escalation, so its money year is y.
            my = (ln.money_year or base) if pc.escalates else y
            by_year.setdefault(y, []).append(_operating_line(ln, amount, my, pc.value))

    # Nominal values of the non-share operating lines, for the shares that read them.
    # This runs the engine's own escalation (pass 1), so there is no second copy of it.
    nominal: dict[str, np.ndarray | None] = {}
    if any(ln.source == "share_of" and _share_base(ln)[0] != "capex" for ln in lines):
        pass1 = dataclasses.replace(
            ic_case,
            counterfactual=(),
            templates=_templates(ic_case, tl, by_year, with_ic=False),
        )
        nominal = build_operating(pass1, tl).lines

    def operating_base(kind: str, category: str, year: int) -> float | None:
        total = 0.0
        for b in lines:
            if not _in_base(b, kind, category):
                continue
            v = nominal.get(b.name)
            if v is None:
                return None
            total += _SIGN[kind] * float(v[tl.index(year)])
        return total

    # Capex: owner assets, depreciation classes, stages and refreshes, all on fresh
    # copies (C1).
    dep = dict(fin.depreciation_class_by_asset)
    rep = list(fin.replacement_capex)
    cod = dict(ic_case.asset_cod)
    assets = list(ic_case.assets)
    overnight: dict[str, float | None] = {}
    for ln in sorted(
        (x for x in lines if x.kind == "capex"), key=lambda x: x.source == "share_of"
    ):
        first, last = _span(ln, horizon)
        if ln.source == "share_of":
            kind, category = _share_base(ln)
            share = ln.cell("quantity", first).value
            if kind == "capex":
                # The base capex lines, already in base money; they are never shares
                # (G6), so they sort, and are priced, before this line.
                vals = [
                    overnight[b.name]
                    for b in lines
                    if b.source != "share_of" and _in_base(b, kind, category)
                ]
                v = _finite(None if None in vals else share * sum(vals))
            else:
                b = operating_base(kind, category, first)
                v = _finite(
                    _to_base(None if b is None else share * b, first, base, r_capex)
                )
        else:
            q, pc = _quantity(ln, first, outputs), ln.cell("price", first)
            v = None if q is None or pc.value is None else q * pc.value
            v = _finite(
                _to_base(
                    v, (ln.money_year or base) if pc.escalates else first, base, r_capex
                )
            )
        overnight[ln.name] = v
        assets.append(AssetFinance(ln.name, SOURCE, v, math.inf))
        dep[ln.name] = _depreciation_class(ln)
        if first > tl.cod_year:
            # A later stage (P5). With staged CODs every other asset states the case COD,
            # as `cod_year_of` already reads it, so the earliest COD stays the case's.
            for a in assets[:-1]:
                cod.setdefault(a.name, ic_case.cod)
            cod[ln.name] = _cod_in(ic_case.cod, first)
        if ln.refresh_years and v is not None:
            k = 1
            while (y := first + math.floor(k * ln.refresh_years + 1e-9)) <= last:
                rep.append((y, ln.name, v))
                k += 1

    # Shares (G6, one level: their bases are never shares, `require_runnable`).
    for ln in lines:
        if ln.kind == "capex" or ln.source != "share_of":
            continue
        kind, category = _share_base(ln)
        first, last = _span(ln, horizon)
        for y in ops:
            if not first <= y <= last:
                continue
            share = ln.cell("quantity", y).value
            if kind == "capex":
                # Overnight is base money: base money, escalated by the line's class (C3).
                vals = [
                    overnight[b.name]
                    for b in lines
                    if b.source != "share_of" and _in_base(b, kind, category)
                ]
                b, my = (None if None in vals else sum(vals)), base
            else:
                b, my = operating_base(kind, category, y), y
            amount = _finite(None if b is None else _SIGN[ln.kind] * share * b)
            by_year.setdefault(y, []).append(_operating_line(ln, amount, my, None))

    case = dataclasses.replace(
        ic_case,
        inputs=fin.model_copy(
            update={"depreciation_class_by_asset": dep, "replacement_capex": rep}
        ),
        templates=_templates(ic_case, tl, by_year, drops=drops),
        assets=tuple(assets),
        asset_cod=cod,
        counterfactual=(),
        counterfactual_hash=None,
    )
    return BusinessCase(
        case=case,
        hash=business_case_hash(ic_case, inputs, outputs, lost_production),
        locked=_locked(ic_case, tl, drops),
    )


def _cod_in(cod: date, year: int) -> date:
    try:
        return cod.replace(year=year)
    except ValueError:  # 29 February
        return cod.replace(year=year, day=28)


# ── the hash (G-C6, C9) ──────────────────────────────────────────────────────


def _bound(out) -> dict | None:
    if isinstance(out, Series):
        return {
            "id": out.output_id,
            "unit": out.unit,
            "derived": out.derived,
            "per_period": sorted([str(k), v] for k, v in out.per_period.items()),
            "years": sorted([str(k), v] for k, v in out.years.items()),
        }
    if isinstance(out, NotEvaluated):
        return {"id": out.output_id, "not_evaluated": out.reason}
    return None


def _line_blob(ln: LineItem, horizon: tuple[int, int]) -> dict:
    """A line as the digest reads it (GK B BC 07 P1): a field enters only when it can
    change an outcome and is set, so a digest from before the field stays byte-equal. The
    hint and a default's range are display only and never enter."""
    d = ln.model_dump(mode="json", exclude={"hint"})
    # A scalar share of 1.0 reads as None does (`linked_share_at`), and a scalar a year
    # cell shadows in every year of the line is never read: no outcome, no entry (F3).
    first, last = _span(ln, horizon)
    shadowed = bool(ln.linked_share_by_year) and all(
        y in ln.linked_share_by_year for y in range(first, last + 1)
    )
    if d["linked_share"] in (None, 1.0) or shadowed:
        del d["linked_share"]
    if not d["linked_share_by_year"]:
        del d["linked_share_by_year"]
    if not ln.default_keys:
        del d["default_units"]
    for a in d["assumed"].values():
        del a["range_low"], a["range_high"]
    return d


def business_case_hash(
    ic_case: FinanceCase,
    inputs: BusinessCaseInputs,
    outputs: dict | None = None,
    lost_production: LostProductionMap | None = None,
) -> str:
    """The business case's own digest (G-C6). It covers `preset_id@version`, the lines,
    `receivable_days`, each linked line's bound solve, the lost-production map the G3
    drop reads, and the IC case it runs on. No IC digest reads it.

    The IC case enters as its inputs and owner, never its counterfactual: a reference
    purchase (G4) changes no business-case value, so it stays out (GK A G4-2)."""
    outputs = outputs or {}
    ops = build_timeline(ic_case).operating_years
    horizon = (int(ops[0]), int(ops[-1]))
    blob = {
        "domain": HASH_DOMAIN,
        "ic": _fallback_hash(ic_case),
        "preset": inputs.preset,
        "receivable_days": inputs.receivable_days,
        "lines": [_line_blob(ln, horizon) for ln in inputs.lines],
        "bound": {
            ln.name: _bound(outputs.get(ln.name))
            for ln in inputs.lines
            if ln.source == "linked"
        },
    }
    lp = lost_production
    if (
        lp is not None
        and _triggers(inputs.lines)
        and (lp.slack_bus or lp.unread)
        and any(_is_lost(ln, lp) for t in ic_case.templates for ln in t.lines)
    ):
        # Only when a line can drop and the IC case values a shortfall, so a case
        # with no drop keeps its digest (D2, GK B F1). Sorted, periods as int (None on a flat network), shares as
        # repr floats.
        blob["lost_production"] = {
            "slack_bus": sorted([str(k), str(v)] for k, v in lp.slack_bus.items()),
            "load_bus": sorted([str(k), str(v)] for k, v in lp.load_bus.items()),
            "unread": sorted([str(k), str(v)] for k, v in lp.unread.items()),
            "share": sorted(
                [
                    str(slack),
                    str(ld),
                    sorted(
                        (
                            [None if k is None else int(k), float(v)]
                            for k, v in s.per_period.items()
                        ),
                        key=lambda kv: (kv[0] is not None, kv[0] or 0),
                    ),
                ]
                for slack, by_load in lp.share.items()
                for ld, s in by_load.items()
            ),
        }
    raw = json.dumps(blob, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── the P&L (BC3, BC4, BC5) ──────────────────────────────────────────────────


@dataclass(frozen=True)
class PnL:
    years: list[int]
    #: Per ledger stream, per year: the IC's energy-model streams (locked, G2) and the
    #: business-case lines' streams, from `result.op` (BC4).
    streams: dict[str, np.ndarray | None]
    rows: dict[str, np.ndarray | list | None] = field(default_factory=dict)
    #: The streams and rows that overflowed in the engine (a sum or an escalation past
    #: float range): each reads None, never inf (GK B #1404 C).
    not_finite: tuple[str, ...] = ()


def _drop_non_finite(d: dict, prefix: str = "") -> list[str]:
    """Set each array in `d` holding a non-finite value to None; return their names."""
    hit = []
    for k, v in d.items():
        if isinstance(v, np.ndarray) and not np.isfinite(v).all():
            d[k] = None
            hit.append(prefix + k)
    return hit


def _first(d: dict | None):
    return None if not d else next(iter(d.values()))


def cash_bridge(rows: dict) -> dict[str, np.ndarray | None]:
    """Project and equity cash from the P&L rows, as the engine builds them
    (`engine.run_case`). The test ties each year to the engine's series (BC5)."""
    need = ("ebitda", "terminal", "capex", "stage_capex", "replacement", "grant")
    out: dict[str, np.ndarray | None] = dict.fromkeys(
        ("project_pre_tax", "project_post_tax", "equity_pre_tax", "equity_post_tax")
    )
    if any(rows.get(k) is None for k in need):
        return out
    pre = (
        rows["ebitda"]
        + rows["terminal"]
        - rows["capex"]
        - rows["stage_capex"]
        - rows["replacement"]
        + rows["grant"]
    )
    out["project_pre_tax"] = pre
    credits = rows.get("credits")
    if rows.get("tax") is not None and credits is not None:
        out["project_post_tax"] = pre - rows["tax"] + credits
    eq = None
    if rows.get("debt_established"):
        eq = (
            pre
            - rows["fees"]
            - rows["debt_service"]
            + rows["draws"]
            + rows["reserve_interest"]
            - rows["dsra_funding"]
        )
    out["equity_pre_tax"] = eq
    if eq is not None and rows.get("tax_equity") is not None and credits is not None:
        out["equity_post_tax"] = eq - rows["tax_equity"] + credits
    return out


def _working_capital(revenue: np.ndarray, days: float) -> np.ndarray:
    """The cash effect of receivables at `days` of revenue: built up as revenue grows
    and released in the last year, so it sums to 0 over life (BC3). The last year's
    receivables are collected within that year (the case ends there)."""
    level = revenue * days / 365.0
    change = -np.diff(level, prepend=0.0)
    change[-1] += level[-1]
    return change


def pnl(result, *, receivable_days: float | None = None) -> PnL:
    """The P&L by year from the per-stream ledger `result.op` (BC4).

    - EBITDA is `revenue − costs`, the engine's `net_total`, exactly.
    - D&A and tax are the engine's: the first tax layer's depreciation and the unlevered
      tax for the project, the levered tax for equity (BC5). There is no second tax
      computation.
    - Working capital is a cash-bridge row only (BC3)."""
    op, tl = result.op, result.tl
    streams: dict[str, np.ndarray] = {}
    for key, v in op.lines.items():
        if v is None:
            continue
        s = op.line_meta[key].stream
        streams[s] = streams.get(s, np.zeros(tl.n)) + v
    revenue, ebitda = op.revenue, _net(op)
    pt = streams.get(PASS_THROUGH_STREAM, np.zeros(tl.n))
    tax_u, tax_l, debt = result.tax_unlevered, result.tax, result.debt
    dep = None if tax_u is None else _first(tax_u.depreciation)
    terminal = result.terminal
    ebit = (
        None
        if ebitda is None or terminal is None or dep is None
        else ebitda + terminal - dep
    )
    rows: dict = {
        "revenue": revenue,
        "opex": op.costs,
        "ebitda": ebitda,
        "pass_through": pt,
        "revenue_net_of_pass_through": None if revenue is None else revenue - pt,
        "ebitda_net_of_pass_through": None if ebitda is None else ebitda - pt,
        "ebitda_margin": None
        if ebitda is None
        else [_finite_ratio(e, r) for e, r in zip(ebitda, revenue)],
        "terminal": terminal,
        "depreciation": dep,
        "ebit": ebit,
        "tax": None if tax_u is None else tax_u.total_liability,
        "tax_equity": None if tax_l is None else tax_l.total_liability,
        "capex": op.capex,
        "stage_capex": op.stage_capex if op.stage_capex is not None else np.zeros(tl.n),
        "replacement": op.replacement,
        "grant": result.incentives.grant,
        "credits": result.cash.get("credits"),
        "fees": debt.fees,
        "debt_service": debt.service,
        "draws": debt.draws,
        "reserve_interest": debt.reserve_interest,
        "dsra_funding": debt.dsra_funding,
        "debt_established": debt.established(),
    }
    rows["net_income"] = (
        None if ebit is None or rows["tax"] is None else ebit - rows["tax"]
    )
    rows.update(cash_bridge(rows))
    wc = None
    if receivable_days is not None and revenue is not None:
        wc = _working_capital(revenue, receivable_days)
    rows["working_capital_change"] = wc
    for k in ("project", "equity"):
        post = rows[f"{k}_post_tax"]
        rows[f"{k}_after_working_capital"] = (
            None if wc is None or post is None else post + wc
        )
    bad = _drop_non_finite(streams, "stream:") + _drop_non_finite(rows)
    return PnL(
        years=[int(y) for y in tl.years],
        streams=streams,
        rows=rows,
        not_finite=tuple(bad),
    )


# ── KPIs (BC2, BC8, C5) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Kpi:
    #: None unless `status` is "ok"; never inf or NaN (C5).
    value: float | None
    status: str  # "ok", "n/a", "not_established"
    reason: str | None = None
    unit: str | None = None
    basis: dict | None = None


def _finite_ratio(num: float, den: float) -> float | None:
    """Num / den, or None when den is 0 or the ratio is not finite (a tiny divisor)."""
    return None if den == 0.0 else _finite(float(num) / float(den))


def _ok(v, unit=None, basis=None) -> Kpi:
    if v is None or not math.isfinite(v):
        return Kpi(None, "n/a", "not_finite", unit, basis)
    return Kpi(float(v), "ok", None, unit, basis)


def _missing(result, sections: tuple[str, ...]) -> str:
    for s in sections:
        if result.sections.get(s) != "ok":
            why = result.reasons.get(s) or []
            return f"{s}_not_established" + (f": {', '.join(why)}" if why else "")
    return "not_established"


def _ratio(num: float, den: float, unit=None) -> Kpi:
    if den == 0.0:
        return Kpi(None, "n/a", "no_revenue", unit)
    return _ok(_finite_ratio(num, den), unit)


def kpis(
    result,
    p: PnL,
    *,
    capacity: dict[str, float] | None = None,
    capacity_unit: str = "MW",
) -> dict[str, Kpi]:
    """The business case's KPIs (spec §7).

    - EBITDA margin and revenue per MW, gross and net of pass-through (BC2).
    - Project and equity NPV and IRR, and payback, from the engine's post-tax series:
      never from a partial tax (GK B Q3), and working capital never enters them (Q4).
    - DSCR is n/a without debt (BC8).

    Revenue per unit of capacity divides the mean operating-year revenue by the plant's
    capacity, stated in `capacity` (asset → amount in `capacity_unit`: MW, t/yr, ...).
    The KPI names the set and the unit. With no capacity stated it is not established
    (C5)."""
    out: dict[str, Kpi] = {}
    ccy = result.case.inputs.currency
    ops = [result.tl.index(y) for y in result.tl.operating_years]
    rev, ebitda = p.rows["revenue"], p.rows["ebitda"]
    if rev is None or ebitda is None:
        why = (
            "not_finite"
            if {"revenue", "ebitda"} & set(p.not_finite)
            else _missing(result, ("operating",))
        )
        for k in (
            "ebitda_margin_gross",
            "ebitda_margin_net",
            "revenue_per_capacity_gross",
            "revenue_per_capacity_net",
        ):
            out[k] = Kpi(None, "not_established", why)
    else:
        r, e = float(rev[ops].sum()), float(ebitda[ops].sum())
        pt = float(p.rows["pass_through"][ops].sum())
        out["ebitda_margin_gross"] = _ratio(e, r)
        out["ebitda_margin_net"] = _ratio(e - pt, r - pt)
        # Each member finite and > 0, so the basis never holds inf or NaN (C5).
        members = capacity or {}
        # NaN fails `> 0`; an inf member makes the sum inf, which `_finite` drops.
        stated = bool(members) and all(
            isinstance(c, (int, float)) and c > 0.0 for c in members.values()
        )
        cap = _finite(float(sum(members.values()))) if stated else None
        unit = f"{ccy}/{capacity_unit}-year"
        basis = {"assets": sorted(members), "capacity": cap, "unit": capacity_unit}
        for k, v in (("gross", r), ("net", r - pt)):
            if cap is None:
                out[f"revenue_per_capacity_{k}"] = Kpi(
                    None, "not_established", "capacity_not_stated", unit, basis
                )
            else:
                out[f"revenue_per_capacity_{k}"] = _ok(v / len(ops) / cap, unit, basis)
    m = result.metrics
    for name, series, sections in (
        ("project", "project_post_tax", ("operating", "incentives", "tax")),
        ("equity", "equity_post_tax", ("operating", "incentives", "debt", "tax")),
    ):
        if result.cash.get(series) is None:
            why = _missing(result, sections)
            out[f"{name}_npv"] = Kpi(None, "not_established", why, ccy)
            out[f"{name}_irr"] = Kpi(None, "not_established", why)
            continue
        npv_v = m.get(f"{series}_npv")
        rate = "wacc_nominal" if name == "project" else "cost_of_equity"
        out[f"{name}_npv"] = (
            _ok(npv_v, ccy)
            if npv_v is not None
            else Kpi(None, "not_established", f"{rate}_missing", ccy)
        )
        irr_v = m.get(f"{series}_irr")
        why = next(
            (
                f.split(":", 1)[1]
                for f in result.flags
                if f.startswith(f"{series}:irr_not_established")
            ),
            "irr_not_established",
        )
        out[f"{name}_irr"] = _ok(irr_v) if irr_v is not None else Kpi(None, "n/a", why)
    if result.cash.get("equity_post_tax") is None:
        out["payback_years"] = Kpi(
            None,
            "not_established",
            _missing(result, ("operating", "incentives", "debt", "tax")),
        )
    elif m.get("payback_years") is None:
        out["payback_years"] = Kpi(None, "n/a", "never_recovers")
    else:
        out["payback_years"] = _ok(m["payback_years"], "years")
    for k in ("min_dscr", "avg_dscr"):
        if not result.debt.tranches:
            out[k] = Kpi(None, "n/a", "no_debt")
        elif m.get(k) is None:
            out[k] = Kpi(None, "not_established", _missing(result, ("debt",)))
        else:
            out[k] = _ok(m[k])
    return out


# ── break-even (G8 on the BC8 rule, C7) ──────────────────────────────────────


def _scaled(inputs: BusinessCaseInputs, i: int, s: float) -> BusinessCaseInputs:
    """`inputs` with line `i`'s price and price cells × `s`: every other line, as is."""
    ln = inputs.lines[i]
    new = ln.model_copy(
        update={
            "price": None if ln.price is None else ln.price * s,
            "price_by_year": {y: v * s for y, v in ln.price_by_year.items()},
        }
    )
    lines = list(inputs.lines)
    lines[i] = new
    return inputs.model_copy(update={"lines": lines})


def _result_at(ic_case, inputs, bound, i, pack, layers, s):
    bc = build_business_case(ic_case, _scaled(inputs, i, s), *bound)
    return run_case(bc.case, pack, layers=layers, _solve=False)


def _npv_at(ic_case, inputs, outputs, i, pack, layers, s) -> float | None:
    """Project post-tax NPV at `wacc_nominal` with line `i`'s price × `s`; None when not
    established (GK B Q4)."""
    try:
        r = _result_at(ic_case, inputs, outputs, i, pack, layers, s)
    except FinanceRefused:
        return None
    cash = r.cash.get("project_post_tax")
    return None if cash is None else npv(ic_case.inputs.wacc_nominal, cash)


class _NotEstablished(Exception):
    """A trial inside brentq's interval is not established: never a TypeError (500)."""


def _npv_or_raise(*args) -> float:
    v = _npv_at(*args)
    if v is None:
        raise _NotEstablished
    return v


def break_even(
    ic_case: FinanceCase,
    inputs: BusinessCaseInputs,
    outputs: dict | None,
    line: str,
    pack=None,
    *,
    layers=None,
    lost_production: LostProductionMap | None = None,
) -> dict:
    """The price of revenue line `line` at which the project's post-tax NPV is 0 (G8).

    The price scale is tried at 0, 1, then × 1.5 up to `BREAK_EVEN_MAX_SCALE`. A root is
    solved (brentq) only between two trials of opposite sign. No sign change is
    `break_even_not_bracketed`, never an extrapolation (BC8). Any trial not established
    is `break_even_not_established`. A line priced only by grid cells has no single price:
    `price` is None and `price_scale` is the factor on every cell. A share line that
    reads this line moves with it; every other line is the same in every trial (C7).
    `lost_production` is `build_business_case`'s (G3)."""
    bound = (outputs, lost_production)
    out = {
        "status": None,
        "price_scale": None,
        "price": None,
        "money_year": None,
        "basis": "project_post_tax_npv@wacc_nominal",
    }
    i = next((k for k, ln in enumerate(inputs.lines) if ln.name == line), None)
    if (
        i is None
        or inputs.lines[i].kind != "revenue"
        or inputs.lines[i].source == "share_of"
    ):
        out["status"] = "break_even_line_not_revenue"
        return out
    ln = inputs.lines[i]
    out["money_year"] = ln.money_year or ic_case.base_year
    scales = [0.0, 1.0]
    while scales[-1] * 1.5 <= BREAK_EVEN_MAX_SCALE:
        scales.append(scales[-1] * 1.5)
    prev_s, prev_f = None, None
    for s in scales:
        f = _npv_at(ic_case, inputs, bound, i, pack, layers, s)
        if f is None:
            out["status"] = "break_even_not_established"
            return out
        if f == 0.0:
            root = s
            break
        if prev_f is not None and (prev_f < 0.0) != (f < 0.0):
            try:
                # `disp=False`: non-convergence is the result's flag, never an exception,
                # so any RuntimeError the engine raises still propagates (GK B #1404 B).
                root, info = brentq(
                    lambda x: _npv_or_raise(ic_case, inputs, bound, i, pack, layers, x),
                    prev_s,
                    s,
                    xtol=BREAK_EVEN_XTOL,
                    rtol=BREAK_EVEN_XTOL,
                    maxiter=200,
                    full_output=True,
                    disp=False,
                )
            except _NotEstablished:
                out["status"] = "break_even_not_established"
                return out
            if not info.converged:
                out["status"] = "break_even_not_converged"
                return out
            break
        prev_s, prev_f = s, f
    else:
        out["status"] = "break_even_not_bracketed"
        return out
    # The re-run (C7): |NPV| at the solved scale within the tolerance of the line's revenue.
    try:
        r = _result_at(ic_case, inputs, bound, i, pack, layers, root)
    except FinanceRefused:
        out["status"] = "break_even_not_established"
        return out
    v = _npv_at(ic_case, inputs, bound, i, pack, layers, root)
    series = r.op.lines.get(line)
    if v is None or series is None:
        out["status"] = "break_even_not_established"
        return out
    if abs(v) > BREAK_EVEN_TOL * max(abs(float(np.sum(series))), 1.0):
        out["status"] = "break_even_not_converged"
        return out
    out.update(
        status="ok",
        price_scale=root,
        price=None if ln.price is None else ln.price * root,
    )
    return out
