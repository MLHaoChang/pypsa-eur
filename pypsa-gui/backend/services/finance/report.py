"""
`InvestmentCaseReport` persistence helpers (P0 WP0.5); the single-owner
finance sections (`assemble_finance_sections`, IC P4 WP4.6b); the full
assembler `assemble_investment_case_report` joins this module in P7.

Mirrors `services/adequacy/eh_report.py`: the report lives in the project's
solver state under `investment_case_report` as a JSON dict (never a model
object, so `results_state.pkl` stays readable by the restricted unpickler),
and the HTTP view is the stable `export_investment_case` projection.
"""
from __future__ import annotations

from typing import Any

from models.finance import InvestmentCaseReport, export_investment_case

IC_REPORT_STORE_KEY = "investment_case_report"


def store_ic_report(store: dict, report: InvestmentCaseReport) -> None:
    store[IC_REPORT_STORE_KEY] = report.model_dump(mode="json")


def load_ic_report(store: dict) -> InvestmentCaseReport | None:
    raw = store.get(IC_REPORT_STORE_KEY)
    if not raw:
        return None
    if isinstance(raw, InvestmentCaseReport):
        return raw
    return InvestmentCaseReport.model_validate(raw)


def ic_report_http_payload(store: dict) -> tuple[dict[str, Any] | None, int]:
    """(body, status) for GET /results/investment_case/report (204 when absent)."""
    report = load_ic_report(store)
    if report is None:
        return None, 204
    return export_investment_case(report), 200


# ── billing frames and commercial terms (review WP0.5 #1) ────────────────────
#
# Both ride `results_state.pkl`, which is read back through a RESTRICTED
# unpickler (bundles can come from anyone). That allow-list admits UTC and
# naive datetime indexes but refuses pytz / zoneinfo objects, pandas
# Timestamp / Period scalars — and one refused value drops EVERY side result
# on reload. The billing pass rates in local time (DST moves TOU windows), so
# the store side converts to naive UTC and records the zone as a string; the
# load side restores it. Keys and values of the commercial terms become plain
# JSON (ISO strings, lists, floats).

BILLING_FRAMES_STORE_KEY = "billing_frames"
COMMERCIAL_TERMS_STORE_KEY = "last_commercial_terms"


def _frame_to_store(df):
    import pandas as pd

    idx = df.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        out = df.copy()
        out.index = idx.tz_convert("UTC").tz_localize(None)
        return {"__tz__": str(idx.tz), "frame": out}
    return {"__tz__": None, "frame": df}


def _frame_from_store(entry):
    if not isinstance(entry, dict) or "frame" not in entry:
        return entry  # legacy / hand-written: pass through
    df, tz = entry["frame"], entry.get("__tz__")
    if tz:
        df = df.copy()
        df.index = df.index.tz_localize("UTC").tz_convert(tz)
    return df


def store_billing_frames(store: dict, frames: dict | None) -> None:
    store[BILLING_FRAMES_STORE_KEY] = (
        None if frames is None
        else {str(k): _frame_to_store(v) for k, v in frames.items()})


def load_billing_frames(store: dict) -> dict | None:
    raw = store.get(BILLING_FRAMES_STORE_KEY)
    if raw is None:
        return None
    return {k: _frame_from_store(v) for k, v in raw.items()}


def _jsonable(x: Any) -> Any:
    import numpy as np
    import pandas as pd

    if isinstance(x, dict):
        return {_jsonable_key(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (pd.Timestamp, pd.Period)):
        return x.isoformat() if isinstance(x, pd.Timestamp) else str(x)
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    raise TypeError(f"commercial terms value of type {type(x).__name__} is not JSON-able")


def _jsonable_key(k: Any) -> str:
    v = _jsonable(k)
    return v if isinstance(v, str) else str(v)


def store_commercial_terms(store: dict, terms: dict | None) -> None:
    store[COMMERCIAL_TERMS_STORE_KEY] = None if terms is None else _jsonable(terms)



# ── the single-owner finance sections (IC P4 WP4.6b) ─────────────────────────
#
# P4 fills the sections the finance engine establishes — `project`, `debt`,
# `tax`, `participants` (the single owner and its counterparties) and `gates`
# (the WACC gate, plan C10); every other section of `IC_REPORT_SECTIONS` is
# `skipped` with the phase that fills it. Headlines are None whenever the engine
# did not establish them (plan C12, ADR-0001) — never 0.

P4_SECTIONS: tuple[str, ...] = ("project", "debt", "tax", "participants", "gates")
_SKIPPED_NOTES: dict[str, str] = {
    "design": "P5+: the design solve is not part of the single-owner finance run (IC P4)",
    "commercial": "P5+: the commercial summary joins the report in P7 (IC P4 values the "
                  "stored ledger only)",
    "dispatch_modes": "P5+: perfect-foresight vs realistic dispatch is not valued in P4",
    "tax_equity": "P7: tax-equity structures are not modelled in P4",
    "uncertainty": "P6: uncertainty is not modelled in P4",
}
# The dispatch the P4 finance case is valued on: the LP solve (perfect foresight).
_MODE = "pf"


def _num(x) -> float | None:
    """A finite float, else None (NaN / inf / None never become a number)."""
    import math

    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _series(arr) -> list[float | None] | None:
    if arr is None:
        return None
    return [_num(v) for v in list(arr)]


def _fallback_hash(case) -> str:
    import hashlib
    import json

    blob = json.dumps({"inputs": case.inputs.model_dump(mode="json"), "owner": case.owner},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _cashflow_stream(stream: str) -> str:
    from typing import get_args

    from models.finance import CashflowStream

    allowed: set[str] = set()
    for arg in get_args(CashflowStream):
        allowed.update(get_args(arg) or (arg,))
    return stream if stream in allowed else "other"


def _cashflow_lines(result, case, packs: dict[str, str]) -> list:
    """One `CashflowLine` per (year, stream line) with a non-zero amount: the
    owner's operating lines (their ledger drill-down in the provenance), the
    capex, replacement, terminal value, incentives, financing flows and the
    corporate tax. The counterfactual is not a cash line (C13: returns are on
    the owner's cash minus it — the project payload carries it per year)."""
    from models.finance import CashflowLine, Provenance

    tl, op, owner = result.tl, result.op, case.owner
    years = [int(y) for y in tl.years]
    pack_hash = next(iter(packs.values()), None) if packs else None
    out: list[CashflowLine] = []

    def emit(stream, arr, *, counterparty="external", asset=None, tariff_item=None,
             source="finance", source_id=None, contract_id=None, period=None, sign=1.0,
             ph=None):
        if arr is None:
            return
        for i, y in enumerate(years):
            v = _num(arr[i])
            if v is None or v == 0.0:
                continue
            out.append(CashflowLine(
                year=y, participant=owner, counterparty=counterparty,
                value_stream=_cashflow_stream(stream), tariff_item=tariff_item, asset=asset,
                amount=sign * v,
                provenance=Provenance(source=source, mode=_MODE, pack_hash=ph,
                                      source_id=source_id, contract_id=contract_id,
                                      period=period)))

    for key, arr in (op.lines or {}).items():
        ln = op.line_meta.get(key)
        if ln is None:
            continue
        emit(ln.stream, arr, counterparty=ln.counterparty, asset=ln.degrades_with,
             tariff_item=ln.tariff_item, source=ln.source, source_id=ln.source_id or key,
             contract_id=ln.contract_id, period=ln.period)
    emit("capex", op.capex, sign=-1.0, source="capex")
    emit("capex", op.replacement, sign=-1.0, source="replacement_capex")
    emit("terminal_value", op.terminal, source="terminal_value")
    inc = result.incentives
    emit("incentive", getattr(inc, "itc", None), counterparty="government", source="itc")
    emit("incentive", getattr(inc, "ptc", None), counterparty="government", source="ptc")
    emit("incentive", getattr(inc, "grant", None), counterparty="government", source="grant")
    debt = result.debt
    if debt is not None and debt.established():
        emit("financing_fee", debt.fees, counterparty="lender", sign=-1.0, source="debt")
        emit("interest", debt.interest, counterparty="lender", sign=-1.0, source="debt")
        emit("principal", debt.principal, counterparty="lender", sign=-1.0, source="debt")
        emit("principal", debt.draws, counterparty="lender", source="debt_draws")
        emit("reserve", debt.dsra_funding, counterparty="reserve", sign=-1.0, source="dsra")
        emit("reserve", debt.reserve_interest, counterparty="reserve", source="reserve_interest")
    if result.tax is not None:
        for layer, arr in result.tax.liability.items():
            emit("corporate_tax", arr, counterparty="tax_authority", sign=-1.0,
                 source=f"tax:{layer}", ph=pack_hash)
    return out


def _project_payload(result, case) -> dict[str, Any]:
    m, fin = result.metrics, case.inputs
    cash = result.cash
    return {
        "owner": case.owner,
        "currency": fin.currency,
        "years": [int(y) for y in result.tl.years],
        "cod_year": result.tl.cod_year,
        "base_year": result.tl.base_year,
        "analysis_years": result.tl.analysis_years,
        # Unlevered project returns at the WACC (plan C9).
        "project_pre_tax_irr": _num(m.get("project_pre_tax_irr")),
        "project_post_tax_irr": _num(m.get("project_post_tax_irr")),
        "project_pre_tax_npv": _num(m.get("project_pre_tax_npv")),
        "project_post_tax_npv": _num(m.get("project_post_tax_npv")),
        # Equity returns at the cost of equity; SAM's "project" return is this one.
        "equity_pre_tax_irr": _num(m.get("equity_pre_tax_irr")),
        "equity_post_tax_irr": _num(m.get("equity_post_tax_irr")),
        "equity_pre_tax_npv": _num(m.get("equity_pre_tax_npv")),
        "equity_post_tax_npv": _num(m.get("equity_post_tax_npv")),
        # The owner's total cash with the investment (C13), always reported.
        "lifecycle_npv": _num(m.get("lifecycle_npv")),
        "payback_years": _num(m.get("payback_years")),
        "lcoe_nominal_per_mwh": _num(m.get("lcoe_nominal_per_mwh")),
        "lcoe_real_per_mwh": _num(m.get("lcoe_real_per_mwh")),
        "solved_ppa_price": _num(m.get("solved_ppa_price")),
        "solve_ppa_status": m.get("solve_ppa_status"),
        "solved_ppa_price_money_year": m.get("solved_ppa_price_money_year"),
        "cash": {k: _series(v) for k, v in cash.items()},
        "incremental_net": _series(result.op_incremental.get("net")),
        "counterfactual_net": _series(result.op_incremental.get("counterfactual")),
        "has_counterfactual": bool(case.counterfactual),
        "operating_status": dict(getattr(result.op, "status", {}) or {}),
        "incentives_status": result.sections.get("incentives"),
        "incentives": [{"kind": ln.kind, "assets": list(ln.assets), "share": _num(ln.share),
                        "rate": _num(ln.rate), "cash": _series(ln.cash)}
                       for ln in getattr(result.incentives, "lines", []) or []],
        "cfads_definition": "CFADS = EBITDA − major-equipment reserve funding (off in P4: "
                            "CFADS = EBITDA); DSRA movements and reserve interest sit below it",
        "flags": list(result.flags),
    }


def _project_reasons(result) -> list[str]:
    op = result.op
    out = []
    for key in ("operating", "capex", "terminal"):
        out += [f"{key}:{r}" for r in (getattr(op, "reasons", {}) or {}).get(key, [])]
    out += [f"counterfactual:{r}" for r in result.reasons.get("counterfactual", [])]
    out += [f"incentives:{r}" for r in result.reasons.get("incentives", [])]
    return out


def _debt_payload(result) -> dict[str, Any]:
    d, m = result.debt, result.metrics
    return {
        "amount": _num(d.amount) if d.established() else None,
        "idc_total": _num(d.idc_total),
        "min_dscr": _num(m.get("min_dscr")), "avg_dscr": _num(m.get("avg_dscr")),
        "min_dscr_senior": _num(m.get("min_dscr_senior")),
        "avg_dscr_senior": _num(m.get("avg_dscr_senior")),
        "llcr": _num(m.get("llcr")), "plcr": _num(m.get("plcr")),
        "tranches": [{"index": t.index, "kind": t.tranche.kind, "amount": _num(t.amount),
                      "principal_drawn": _num(t.principal_drawn),
                      "rate": t.tranche.rate, "tenor_years": t.tranche.tenor_years,
                      "sculpting": t.tranche.sculpting, "balance": _series(t.balance),
                      "interest": _series(t.interest), "principal": _series(t.principal),
                      "flags": list(t.flags)} for t in d.tranches],
        "cfads": _series(d.cfads), "interest": _series(d.interest),
        "principal": _series(d.principal), "service": _series(d.service),
        "draws": _series(d.draws), "idc": _series(d.idc), "fees": _series(d.fees),
        "dsra_balance": _series(d.dsra_balance), "dsra_funding": _series(d.dsra_funding),
        "reserve_interest": _series(d.reserve_interest),
        "dscr": _series(d.dscr), "dscr_senior": _series(d.dscr_senior),
        "sources": {k: _num(v) for k, v in d.sources.items()},
        "uses": {k: _num(v) for k, v in d.uses.items()},
        "iterations": d.iterations, "residual": _num(d.residual),
        "flags": list(d.flags),
    }


def _tax_payload(result, case, packs: dict[str, str]) -> dict[str, Any]:
    fin, t = case.inputs, result.tax
    out: dict[str, Any] = {"tax_pack_id": fin.tax_pack_id, "packs": dict(packs),
                           "tax_losses": fin.tax_losses,
                           "financing_fee_tax": fin.financing_fee_tax}
    if t is not None:
        out.update({
            "layers": {name: {"depreciation": _series(t.depreciation.get(name)),
                              "taxable": _series(t.taxable.get(name)),
                              "liability": _series(t.liability.get(name)),
                              "loss_pool": _series(t.loss_pool.get(name)),
                              "remaining_basis": _num(t.remaining_basis.get(name))}
                       for name in t.liability},
            "total_liability": _series(t.total_liability),
            "unlevered_total_liability": _series(getattr(result.tax_unlevered,
                                                         "total_liability", None)),
            "flags": list(t.flags),
        })
    return out


def _participants_payload(result, case) -> dict[str, Any]:
    by_cp: dict[str, list[float]] = {}
    n = result.tl.n
    for key, arr in (result.op.lines or {}).items():
        ln = result.op.line_meta.get(key)
        if ln is None or arr is None:
            continue
        acc = by_cp.setdefault(ln.counterparty, [0.0] * n)
        for i in range(n):
            acc[i] += float(arr[i])
    return {
        "structure": "single_owner",
        "owner": case.owner,
        "counterparties": sorted(by_cp),
        "operating_cash_by_counterparty": {k: _series(v) for k, v in sorted(by_cp.items())},
        "declared_participants": [p.model_dump(mode="json") for p in case.inputs.participants],
    }


def _state(status: str, payload=None, note: str | None = None):
    from models.finance import IcSectionState

    return IcSectionState(status=status, payload=payload, note=note)


def _pipeline(*, finance: str, note: str | None = None):
    from models.finance import IcStudyPipeline

    pl = IcStudyPipeline()
    for rec in pl.stages:
        if rec.stage == "finance":
            rec.status, rec.note = finance, note
        elif rec.stage == "assemble":
            rec.status = "run" if finance == "run" else finance
        else:
            rec.status = "skipped"
            rec.note = "not part of the P4 single-owner finance run"
    pl.aborted = finance == "aborted"
    return pl


def _skipped_sections() -> dict:
    return {name: _state("skipped", note=note) for name, note in _SKIPPED_NOTES.items()}


def assemble_finance_sections(result, case, *, case_id: str = "investment_case",
                              assumptions_hash: str | None = None,
                              packs: dict[str, str] | None = None,
                              provenance: dict[str, Any] | None = None) -> InvestmentCaseReport:
    """The P4 report from a `FinanceResult` (`services.finance.engine.run_case`)
    and its `FinanceCase`. `assumptions_hash` is the staleness key the runner
    computed from the inputs (finance, value flows, commercial, dispatch
    digest, packs); `provenance` (the input digests, the `FinanceCase` hash)
    is recorded in the project payload."""
    from models.finance import GatesBlock, IC_REPORT_SECTIONS

    packs = dict(packs or {})
    m = result.metrics
    sections: dict = _skipped_sections()

    project_ok = result.cash.get("ebitda") is not None
    payload = _project_payload(result, case)
    if provenance:
        payload["provenance"] = dict(provenance)
    reasons = _project_reasons(result)
    sections["project"] = _state("ok" if project_ok else "not_established", payload,
                                 None if project_ok else "; ".join(reasons) or "not established")

    debt_ok = result.sections.get("debt") == "ok"
    sections["debt"] = _state("ok" if debt_ok else "not_established", _debt_payload(result),
                              None if debt_ok else "; ".join(result.reasons.get("debt", []))
                              or "not established")

    tax_ok = result.sections.get("tax") == "ok"
    sections["tax"] = _state("ok" if tax_ok else "not_established",
                             _tax_payload(result, case, packs),
                             None if tax_ok else "; ".join(result.reasons.get("tax", []))
                             or "not established")

    op_ok = (getattr(result.op, "status", {}) or {}).get("operating") == "ok"
    sections["participants"] = _state(
        "ok" if op_ok else "not_established", _participants_payload(result, case),
        None if op_ok else "; ".join((getattr(result.op, "reasons", {}) or {})
                                     .get("operating", [])) or "not established")

    gate = result.gate or {}
    consistent = gate.get("wacc_vs_discount_rate_consistent")
    sections["gates"] = _state(
        "ok" if consistent is not None else "not_established", _jsonable(gate),
        None if consistent is not None else
        "the WACC gate needs wacc_nominal and the LP's discount rate: "
        + ", ".join(f"{k}={v}" for k, v in (gate.get("legs") or {}).items() if v is None))

    assert set(sections) == set(IC_REPORT_SECTIONS)
    pl = _pipeline(finance="run")
    return InvestmentCaseReport(
        case_id=case_id,
        assumptions_hash=assumptions_hash or _fallback_hash(case),
        packs=packs,
        project_irr_pre_tax=_num(m.get("project_pre_tax_irr")),
        project_irr_post_tax=_num(m.get("project_post_tax_irr")),
        npv_at_wacc=_num(m.get("project_post_tax_npv")),
        lcoe_finance_consistent_eur_per_mwh=_num(m.get("lcoe_nominal_per_mwh")),
        ppa_price_for_target_irr_eur_per_mwh=_num(m.get("solved_ppa_price")),
        min_dscr=_num(m.get("min_dscr")), avg_dscr=_num(m.get("avg_dscr")),
        llcr=_num(m.get("llcr")), plcr=_num(m.get("plcr")),
        gates=GatesBlock(wacc_vs_discount_rate_consistent=consistent),
        sections=sections,
        completeness={k: v.status for k, v in sections.items()},
        pipeline=pl,
        cashflow_lines=_cashflow_lines(result, case, packs),
    )


def refused_finance_report(code: str, detail: str = "", *, case_id: str = "investment_case",
                           assumptions_hash: str, packs: dict[str, str] | None = None,
                           owner: str | None = None) -> InvestmentCaseReport:
    """The report of a case the engine refused (`FinanceRefused`): every P4
    section `not_established` with the code, every headline None — so the
    stored report never keeps showing a previous run's numbers as current."""
    note = f"refused:{code}" + (f" ({detail})" if detail else "")
    sections: dict = _skipped_sections()
    for name in P4_SECTIONS:
        sections[name] = _state("not_established",
                                {"refusal": {"code": code, "detail": detail}, "owner": owner}
                                if name == "project" else None, note)
    return InvestmentCaseReport(
        case_id=case_id, assumptions_hash=assumptions_hash, packs=dict(packs or {}),
        sections=sections, completeness={k: v.status for k, v in sections.items()},
        pipeline=_pipeline(finance="run", note=note))
