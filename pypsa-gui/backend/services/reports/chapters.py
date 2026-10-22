"""
The report's investment-case, island (control) and grid-readiness chapters
(E2E demo plan 2026-10-14 D8, data center first; DC grid readiness 02).

Pure, as ``evidence``: the route reads the stored investment case report
(``routers.results.get_investment_case_report``) and, for a hub project, the
campus island report and the GFM settings, and passes them here. Each
becomes one ``EvidenceSection`` with code-rendered tables, its numbers in the
number audit's facts (IRRs as percent, ``*_pct``, so "8.5 %" verifies), and
the disclosures a reader must see beside them: the counterfactual's basis,
the island break-even as a threshold, the island study's screening caveat,
the GFM settings as heuristic screening, capacity needed, a re-solve owed.

A chapter is built only when its source was passed (``None`` = the project
has no such source), so a report without them keeps its evidence hash; a
source passed empty is a chapter stated not established.
"""
from __future__ import annotations

from typing import Any

from services.reports import formatting as F

INVESTMENT = "investment_case"
ISLAND = "island"
GRID_READINESS = "grid_readiness"

TITLES = {
    INVESTMENT: "Investment case",
    ISLAND: "Islanding and control",
    GRID_READINESS: "Grid readiness (screening)",
}

#: The island report tables a chapter shows (the hourly and per-event ones
#: stay in the campus panel and its export).
ISLAND_TABLES = ("requirements", "costs", "static_summary", "freq_summary")

NO_INVESTMENT = ("no investment case has been stored for this project: run it from the "
                 "Investment tab")
NO_GRID_READINESS = ("no grid-readiness scorecard for this project: it needs a saved network")
NO_ISLAND = ("no island check and no grid-forming recommendation for this project: run the "
             "campus study with island requirements")


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _pct(v: Any) -> float | None:
    x = _num(v)
    return None if x is None else round(100.0 * x, 4)


def _g(v: Any) -> str:
    x = _num(v)
    return F.NOT_ESTABLISHED if x is None else f"{x:g}"


# ── the investment case ─────────────────────────────────────────────────────


def investment_payload(report: dict | None, *, stale: bool | None = None) -> dict | None:
    """The chapter's facts from a stored investment case report (JSON), or
    None when it has none."""
    if not isinstance(report, dict) or not report.get("sections"):
        return None
    p = ((report.get("sections") or {}).get("project") or {}).get("payload") or {}
    cf = p.get("counterfactual") if isinstance(p.get("counterfactual"), dict) else {}
    be = p.get("island_breakeven") if isinstance(p.get("island_breakeven"), dict) else {}
    irr_pre = report.get("project_irr_pre_tax", p.get("project_pre_tax_irr"))
    irr_post = report.get("project_irr_post_tax", p.get("project_post_tax_irr"))
    return {
        "currency": p.get("currency"),
        "owner": p.get("owner"),
        "cod_year": p.get("cod_year"),
        "analysis_years": p.get("analysis_years"),
        "npv_at_wacc_eur": _num(report.get("npv_at_wacc")),
        "project_pre_tax_npv_eur": _num(p.get("project_pre_tax_npv")),
        "project_irr_pre_tax_pct": _pct(irr_pre),
        "project_irr_post_tax_pct": _pct(irr_post),
        "payback_years": _num(p.get("payback_years")),
        "lcoe_eur_per_mwh": _num(report.get("lcoe_finance_consistent_eur_per_mwh")),
        "min_dscr": _num(report.get("min_dscr")),
        "basis_statement": p.get("basis_statement"),
        "compared_with": cf.get("basis") if cf.get("present", True) else None,
        "no_counterfactual": cf.get("basis") if cf and not cf.get("present", True) else None,
        "counterfactual_notes": [str(n) for n in cf.get("notes") or []],
        "island_breakeven_eur_per_year": _num(be.get("eur_per_year")),
        "island_breakeven_text": be.get("text"),
        "completeness": dict(report.get("completeness") or {}),
        "stale": stale,
    }


def investment_table(payload: dict, source_path: str):
    from services.reports.evidence import EvidenceTable

    cur = payload.get("currency") or "EUR"
    rows = [
        ("NPV at the WACC, after tax", F.fmt_number(payload.get("npv_at_wacc_eur"), unit=cur)),
        ("NPV before tax", F.fmt_number(payload.get("project_pre_tax_npv_eur"), unit=cur)),
        ("Project IRR before tax", F.fmt_number(payload.get("project_irr_pre_tax_pct"), unit="%", digits=1)),
        ("Project IRR after tax", F.fmt_number(payload.get("project_irr_post_tax_pct"), unit="%", digits=1)),
        ("Payback (years)", F.fmt_number(payload.get("payback_years"), digits=1)),
        ("LCOE", F.fmt_number(payload.get("lcoe_eur_per_mwh"), unit="€/MWh", digits=1)),
        ("Minimum DSCR", F.fmt_number(payload.get("min_dscr"), digits=2)),
        ("Compared with", F.fmt_text(payload.get("compared_with")) if payload.get("compared_with")
         else "nothing: incremental = total" if payload.get("no_counterfactual")
         else F.fmt_text(None)),
        ("Price basis", F.fmt_text(payload.get("basis_statement"))),
    ]
    if payload.get("island_breakeven_text"):
        rows.append(("Island capability break-even", F.fmt_text(payload["island_breakeven_text"])))
    return EvidenceTable(table_id=INVESTMENT, columns=["Item", "Value"], rows=[list(r) for r in rows],
                         caption="Investment case: headline figures", source_path=source_path)


def investment_disclosures(payload: dict) -> list[str]:
    out = []
    if payload.get("stale"):
        out.append("Investment case — the inputs changed since the case ran; its figures are the "
                   "last run's.")
    if payload.get("compared_with"):
        out.append(f"Investment case — the incremental figures are {payload['compared_with']}.")
    elif payload.get("no_counterfactual"):
        out.append(f"Investment case — {payload['no_counterfactual']}.")
    out += [f"Investment case — {n}" for n in payload.get("counterfactual_notes") or []]
    if payload.get("island_breakeven_text"):
        out.append("Investment case — the island break-even is a threshold the island capability "
                   "would have to reach, not a value the case earns; it is not in the NPV.")
    return out


# ── the island and its control ──────────────────────────────────────────────


def island_payload(source: dict | None) -> dict | None:
    """The chapter's facts from ``{report: island_report(...), gfm:
    get_settings(...)}``, or None when neither has anything."""
    if not isinstance(source, dict):
        return None
    report = source.get("report") if isinstance(source.get("report"), dict) else None
    gfm = source.get("gfm") if isinstance(source.get("gfm"), dict) else None
    tables = {t["id"]: {"title": t.get("title"), "model": t.get("model"), "columns": list(t.get("columns") or []),
                        "rows": [list(r) for r in t.get("rows") or []]}
              for t in (report or {}).get("tables") or [] if t.get("id") in ISLAND_TABLES}
    rec = (gfm or {}).get("recommendation") if gfm else None
    # the last apply's record outlives the run's recommendation (#310 review)
    applied = (gfm or {}).get("applied") or (rec or {}).get("applied")
    if not tables and not rec and not applied:
        return None
    out: dict[str, Any] = {"caveat": (report or {}).get("caveat"),
                           "missing": list((report or {}).get("missing") or []), "tables": tables}
    if rec:
        out["gfm"] = {
            "state": gfm.get("state"), "status": rec.get("status"), "label": rec.get("label"),
            "simulations": rec.get("simulations"), "max_simulations": rec.get("max_simulations"),
            "margin_pct": _pct(rec.get("margin")),
            "settings": rec.get("settings") or {}, "capacity": list(rec.get("capacity") or []),
            "applied": applied,
        }
    elif gfm:
        out["gfm"] = {"state": gfm.get("state"), "reason": gfm.get("reason")}
        if applied:                       # only with a record: other reports keep their evidence hash
            out["gfm"]["applied"] = applied
    return out


def island_tables(payload: dict, source_path: str) -> dict:
    from services.reports.evidence import EvidenceTable

    out = {}
    for tid in ISLAND_TABLES:
        t = payload["tables"].get(tid)
        if not t:
            continue
        rows = [[_g(v) if _num(v) is not None else F.fmt_text(v) for v in r] for r in t["rows"]]
        out[f"island_{tid}"] = EvidenceTable(
            table_id=f"island_{tid}", columns=t["columns"], rows=rows,
            caption=f"{t['title']} ({t['model']})" if t.get("model") else t["title"],
            source_path=f"{source_path}/tables/{tid}")
    gfm = payload.get("gfm") or {}
    rows = []
    for unit, s in sorted((gfm.get("settings") or {}).items()):
        cur, rec = s.get("current") or {}, s.get("recommended") or {}
        for field in ("droop_pct", "tau_f_s", "H_v_s", "i_max_pu", "H_equivalent_s"):
            if field in cur:
                rows.append([unit, s.get("model", ""), field, _g(cur[field]), _g(rec.get(field, cur[field]))])
    if rows:
        out["island_gfm_settings"] = EvidenceTable(
            table_id="island_gfm_settings",
            # an incomplete search is never a verdict (#303): its settings are the best so far
            columns=["Unit", "Control", "Setting", "Current",
                     "Best found so far" if gfm.get("status") == "incomplete" else "Recommended"],
            rows=rows, caption="Grid-forming settings (heuristic screening)",
            source_path=f"{source_path}/gfm/settings")
    # delta None: no finite amount passes (a collapse); the row's note says what it needs instead
    cap = [[c.get("case", ""), c.get("kind", ""),
            f"+{_g(c.get('delta'))} {c.get('unit', '')}".strip() if _num(c.get("delta")) is not None
            else F.fmt_text(c.get("note") or "no finite amount"),
            F.fmt_text(c.get("binding"))] for c in gfm.get("capacity") or []]
    if cap:
        out["island_gfm_capacity"] = EvidenceTable(
            table_id="island_gfm_capacity", columns=["Case", "Kind", "Needed", "Binding check"], rows=cap,
            caption="Capacity needed where no grid-forming setting passes",
            source_path=f"{source_path}/gfm/capacity")
    return out


_GFM_STATUS = {
    "recommended": "grid-forming settings within the bounds make every case pass with margin",
    "unchanged": "the current grid-forming settings already pass with margin",
    "not_achievable": "no grid-forming setting within the bounds passes: capacity is needed",
    "incomplete": "the grid-forming search stopped at its simulation cap before it could decide",
}


def island_disclosures(payload: dict) -> list[str]:
    out = []
    if payload.get("caveat"):
        out.append(f"Islanding and control — {payload['caveat']}")
    if payload.get("missing"):
        out.append("Islanding and control — not established: " + ", ".join(payload["missing"]) + ".")
    gfm = payload.get("gfm") or {}
    if gfm.get("status"):
        line = f"Islanding and control — {gfm.get('label') or 'heuristic screening'}: {_GFM_STATUS.get(gfm['status'], gfm['status'])}"
        if gfm.get("state") == "stale":
            line += " (the campus run changed since; the settings are the last recommendation's)"
        out.append(line + ".")
    elif gfm.get("reason"):
        out.append(f"Islanding and control — grid-forming settings: {gfm['reason']}.")
    applied = gfm.get("applied")
    if isinstance(applied, dict) and applied.get("hub_resolve_needed"):
        out.append("Islanding and control — the recommended settings were applied to the island data; "
                   "Re-solve the hub: these settings change its island rows.")
    elif isinstance(applied, dict):
        out.append("Islanding and control — the recommended settings were applied to the island data; "
                   "island constraints are off, so the hub solve does not read them.")
    return out


# ── the grid-readiness scorecard (DC grid readiness 02) ────────────────────

_VERDICT_WORD = {"pass": "Pass", "warn": "Warn", "fail": "Fail", "unknown": "Unknown"}


def grid_readiness_payload(source: dict | None) -> dict | None:
    """The chapter's facts from ``grid_readiness(...)``: its rows (limit and
    project value as numbers where they are), the profile and the counts;
    None when there is no scorecard."""
    if not isinstance(source, dict) or not isinstance(source.get("rows"), list):
        return None
    rows = []
    for r in source["rows"]:
        value = r.get("project_value")
        if isinstance(value, dict):          # LOLE: {lole_h_per_year, ci_h_per_year, horizon_years}
            value = value.get("lole_h_per_year")
        row = {"id": r.get("id"), "requirement": r.get("requirement"), "limit": r.get("limit"),
               "unit": r.get("unit"), "project_value": value, "verdict": r.get("verdict"),
               "assumed": bool(r.get("assumed")), "source": r.get("source")}
        after = r.get("after_battery")
        if isinstance(after, dict):          # DC 03: the value once the battery/UPS has smoothed
            row["after_battery"] = {"value": after.get("value"), "verdict": after.get("verdict")}
        rows.append(row)
    return {"profile": dict(source.get("profile") or {}), "rows": rows,
            "summary": dict(source.get("summary") or {}), "envelope": bool(source.get("envelope"))}


def _cell(v: Any, unit: str | None) -> str:
    x = _num(v)
    if x is not None:
        return f"{x:.4g}{f' {unit}' if unit else ''}"
    return F.NOT_ESTABLISHED if v is None else F.fmt_text(v)


def _after_cell(row: dict) -> str:
    """``3.2 MW/min, Pass`` once smoothed; "not known yet" without a value;
    blank for a row the battery/UPS does not change."""
    after = row.get("after_battery")
    if after is None:
        return ""
    if _num(after.get("value")) is None:
        return "not known yet"
    verdict = _VERDICT_WORD.get(after.get("verdict"), str(after.get("verdict")))
    return f"{_cell(after['value'], row['unit'])}, {verdict}"


def grid_readiness_table(payload: dict, source_path: str):
    from services.reports.evidence import EvidenceTable

    # The after-battery column only when a row has one, so a report without a
    # smoothing battery/UPS keeps its table (and its evidence hash).
    mitigated = any("after_battery" in r for r in payload["rows"])
    rows = []
    for r in payload["rows"]:
        cells = [F.fmt_text(r["requirement"]), _cell(r["limit"], r["unit"]),
                 _cell(r["project_value"], r["unit"]), _VERDICT_WORD.get(r["verdict"], str(r["verdict"]))]
        if mitigated:
            cells.append(_after_cell(r))
        rows.append([*cells, "assumed" if r["assumed"] else "", F.fmt_text(r["source"])])
    columns = ["Requirement", "Limit", "Project", "Verdict",
               *(["After battery/UPS"] if mitigated else []), "Assumed", "Source"]
    title = payload["profile"].get("title") or payload["profile"].get("id")
    return EvidenceTable(table_id=GRID_READINESS, columns=columns,
                         rows=rows, caption=f"Grid-readiness screening against {title}",
                         source_path=f"{source_path}/rows")


def grid_readiness_disclosures(payload: dict) -> list[str]:
    head = "Grid readiness"
    s = payload.get("summary") or {}
    out = [f"{head} — an advisory screening, not a compliance study: nothing in it blocks a run, and a "
           "fail is a red flag to take to the network operator. Unknown means an input is missing, "
           "never a pass.",
           f"{head} — {s.get('red_flags', 0)} red flag(s), {s.get('unknown', 0)} unknown, "
           f"{s.get('assumed', 0)} on an assumed limit."]
    if payload.get("envelope"):
        out.append(f"{head} — the ramp, oscillation and voltage-change rows read the AI-training load "
                   "envelope IT-side at the point of connection, before UPS/BESS smoothing: before "
                   "mitigation, not final.")
    if any("after_battery" in r for r in payload["rows"]):
        out.append(f"{head} — {s.get('pass_after_mitigation', 0)} of {s.get('red_flags', 0)} red flag(s) "
                   "pass after the site's battery/UPS smoothing (a closed-form screen, not a dynamic "
                   "study); the Verdict column stays before mitigation.")
    if any(str(r["id"]).startswith("dynamic:") for r in payload["rows"]):
        out.append(f"{head} — the grid code's dynamic requirements need a dynamic (EMT) study; a "
                   "steady-state screening never passes them.")
    assumed = [str(r["requirement"]) for r in payload["rows"] if r["assumed"] and r["limit"] is not None]
    if assumed:
        out.append(f"{head} — assumed limits, to confirm with the network operator: "
                   + "; ".join(assumed) + ".")
    return out
