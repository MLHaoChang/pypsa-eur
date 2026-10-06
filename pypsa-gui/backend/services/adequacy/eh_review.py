"""
Energy Hub study review for the assistant (plan 2026-09-26 P22).

Reads the stored ReferenceDesignReport (export shape) plus the study record
and returns FINDINGS: each has a severity, the evidence it read (numbers
quoted from the report, never re-computed or invented), a plain-language
recommendation and — where the change is concrete — an ``action``: an
existing chat tool and its exact arguments. The assistant presents findings
and OFFERS actions; it applies one only when the user asks, through the
normal confirmation card of that (write / execution) tool. Nothing here
changes any state.

Rules are deliberately conservative: a finding needs evidence in the report,
and an action is emitted only when its arguments are fully determined (a
tag whose value only the user knows gets a recommendation, not a guess).
"""
from __future__ import annotations

import copy
import math
from typing import Any

from models.energy_hub import MAX_EH_BUDGET_SOLVES

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
_MC_MAX_DRAWS = 2000


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fmt(v, unit: str = "", digits: int = 2) -> str:
    f = _num(v)
    if f is None:
        return "n/a"
    s = f"{f:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{f:,.0f}"
    return f"{s}{(' ' + unit) if unit else ''}"


class _Rerun:
    """Builds ``run_eh_study`` arguments from the previous request."""

    def __init__(self, record: dict | None, report: dict) -> None:
        rec = record or {}
        self.base: dict[str, Any] = {
            "archetype": rec.get("archetype") or report.get("archetype"),
        }
        if rec.get("stages"):
            self.base["stages"] = list(rec["stages"])
        if rec.get("budget_solves"):
            self.base["budget_solves"] = int(rec["budget_solves"])
        if rec.get("pack_overrides"):
            self.base["pack_overrides"] = copy.deepcopy(rec["pack_overrides"])
        if rec.get("dtc_attribution"):
            self.base["dtc_attribution"] = rec["dtc_attribution"]
        for key in ("mc", "dsr_buses", "dtc_config"):
            if rec.get(key):
                self.base[key] = copy.deepcopy(rec[key])

    def with_(self, *, pack: dict | None = None, levers: dict | None = None,
              add_stages: list[str] | None = None, **top) -> dict:
        args = copy.deepcopy(self.base)
        if pack or levers:
            po = args.setdefault("pack_overrides", {})
            po.update(pack or {})
            if levers:
                po.setdefault("levers", {}).update(levers)
        if add_stages:
            from models.energy_hub import EH_PIPELINE_STAGES
            wanted = (set(args.get("stages") or
                          ("apply_pack", "ens_solve", "mc_certify", "assemble"))
                      | set(add_stages))
            args["stages"] = [s for s in EH_PIPELINE_STAGES if s in wanted]
        if "mc" in top:
            top = {**top, "mc": {**(args.get("mc") or {}), **top["mc"]}}
        args.update(top)
        return args


def _action(tool: str, args: dict, effect: str) -> dict:
    return {"tool": tool, "args": args, "effect": effect}


def review_report(report: dict, record: dict | None = None,
                  template: dict | None = None) -> dict[str, Any]:
    """Findings + next steps for one stored EH report (see module doc)."""
    sections = report.get("sections") or {}
    completeness = report.get("completeness") or {}
    pipeline = report.get("pipeline") or {}
    rerun = _Rerun(record, report)
    findings: list[dict] = []

    def add(fid: str, severity: str, title: str, evidence: dict,
            recommendation: str, actions: list[dict] | None = None) -> None:
        findings.append({"id": fid, "severity": severity, "title": title,
                         "evidence": evidence,
                         "recommendation": recommendation,
                         "actions": actions or []})

    pack_ens = _num(report.get("ens_cap_permyriad"))
    budget = int(pipeline.get("budget_solves") or 0)
    consumed = int(pipeline.get("solves_consumed") or 0)

    # ── certification ──────────────────────────────────────────────────
    cert = (sections.get("certification") or {}).get("payload") or {}
    verdict = cert.get("verdict")
    lole = _num(report.get("mc_lole_h") if report.get("mc_lole_h") is not None
                else cert.get("lole_h_per_year"))
    target = _num(cert.get("target_lole_h"))
    ci = cert.get("lole_ci")
    ev = {"verdict": verdict, "lole_h_per_year": lole, "target_lole_h": target,
          "lole_ci_per_horizon": ci, "draws": cert.get("draws"),
          "converged": cert.get("converged")}
    if verdict == "fail":
        achieved0 = _num(report.get("achieved_ens_permyriad"))
        energy_limited = (achieved0 is not None and pack_ens
                          and achieved0 >= 0.5 * pack_ens)
        ev["achieved_ens_permyriad"] = achieved0
        if energy_limited:
            tighter = max(round((pack_ens or 10.0) / 4.0, 4), 0.01)
            add("certification_fail", "high",
                f"Not certified: LOLE {_fmt(lole, 'h/yr')} exceeds the "
                f"{_fmt(target, 'h/yr')} target",
                ev,
                "The plan sits near its ENS limit, so it sheds energy by design "
                "and the sampled LOLE follows. Tighten the ENS target so the "
                "expansion buys more capacity, then re-certify.",
                [_action("run_eh_study",
                         rerun.with_(pack={"ens_cap_permyriad": tighter}),
                         f"re-plan at ENS {tighter:g} ‱ (was "
                         f"{_fmt(pack_ens, '‱')}), then re-certify")])
        else:
            # P19–P22 gate: the plan already serves (nearly) all demand, so a
            # tighter ENS target changes nothing — the LOLE is OUTAGE-driven
            # (unit / import unavailability the deterministic solve never
            # sees). Only the user can choose which firm capacity to add.
            add("certification_fail", "high",
                f"Not certified: LOLE {_fmt(lole, 'h/yr')} exceeds the "
                f"{_fmt(target, 'h/yr')} target — outage-driven",
                ev,
                "The deterministic plan already serves the demand "
                f"(achieved ENS {_fmt(achieved0, '‱')}), so tightening the "
                "ENS target would not change it: the LOLE comes from outages "
                "(generator and import unavailability) the plan does not "
                "anticipate. Add firm local capacity — e.g. an N+1 unit, a "
                "higher p_nom_min on an expansion candidate, storage sized "
                "for the outage duration — or reduce the dominant outage "
                "rate. Sizing islanded operation (dtc_planning) shows how "
                "much local capacity keeps the critical demand on.",
                [_action("run_eh_study",
                         rerun.with_(add_stages=["dtc_stress", "dtc_planning"]),
                         "size the local capacity that islanded operation "
                         "needs (dtc_stress + dtc_planning)")])
    elif verdict == "inconclusive":
        draws = int(cert.get("draws") or 500)
        more = min(_MC_MAX_DRAWS, max(draws * 2, draws + 1))
        acts = []
        if more > draws:
            acts.append(_action("run_eh_study", rerun.with_(mc={"draws": more}),
                                f"re-run the MC with {more} draws (was {draws}) "
                                "to narrow the confidence interval"))
        add("certification_inconclusive", "medium",
            f"Certification inconclusive: the LOLE interval straddles "
            f"{_fmt(target, 'h/yr')}",
            ev,
            "Not a failure: the sample cannot yet tell pass from fail. More MC "
            "draws narrow the interval; a slightly tighter plan moves it clear.",
            acts)
    elif verdict == "pass":
        add("certification_pass", "info",
            f"Certified: LOLE {_fmt(lole, 'h/yr')} is below the "
            f"{_fmt(target, 'h/yr')} target with 95 % confidence", ev,
            "The plan meets the LOLE standard. Check the frontier for whether "
            "a looser (cheaper) ENS target would still pass.")
    elif lole is not None and target is None:
        add("certification_no_target", "info",
            f"LOLE {_fmt(lole, 'h/yr')} reported but not certified (no target)",
            ev,
            "Set a LOLE target to get a pass / fail verdict (3 h/yr is a "
            "common planning standard).",
            [_action("run_eh_study",
                     rerun.with_(pack={"target_lole_h": 3.0,
                                       "certification_metric": "mc_lole"}),
                     "certify the plan against 3 h/yr")])

    # ── ENS target ─────────────────────────────────────────────────────
    achieved = _num(report.get("achieved_ens_permyriad"))
    if achieved is not None and pack_ens is not None and achieved > pack_ens * 1.0001:
        add("ens_target_missed", "high",
            f"ENS target missed: {_fmt(achieved, '‱')} vs {_fmt(pack_ens, '‱')}",
            {"achieved_ens_permyriad": achieved, "ens_cap_permyriad": pack_ens},
            "The expansion could not reach the energy target with the "
            "candidate set: raise candidates' p_nom_max or add firm / storage "
            "candidates.")

    # ── not-established sections ───────────────────────────────────────
    for sec, status in completeness.items():
        if status != "not_established" or sec == "certification":
            continue
        note = str((sections.get(sec) or {}).get("note") or "")
        low = note.lower()
        acts: list[dict] = []
        rec = f"The {sec} section was requested but could not be established."
        if "budget" in low and budget:
            more = min(MAX_EH_BUDGET_SOLVES, max(budget * 2, consumed + 10))
            if more > budget:
                acts.append(_action("run_eh_study",
                                    rerun.with_(budget_solves=more),
                                    f"re-run with budget {more} (was {budget})"))
            rec = "The solve budget ran out before this stage could finish."
        elif "voll" in low:
            acts.append(_action("update_solver_config",
                                {"partial": {"voll": 5000.0}},
                                "set VOLL to 5000 €/MWh (frontier and fmea_top "
                                "need VOLL > 0)"))
            rec = "This stage needs a value of lost load (VOLL) > 0."
        elif "eh_critical" in low or "critical bus" in low or "no critical" in low:
            rec = ("Tag the bus(es) whose demand must survive islanding as "
                   "critical (Properties → Energy Hub → Critical bus, or "
                   "update_component Bus attrs {eh_critical: true}).")
        elif "eh_sk_mva" in low or "scr" in low:
            rec = ("Enter the grid short-circuit level (eh_sk_mva) and the "
                   "inverter capacity (eh_ibr_mva) on the PoC bus(es) named in "
                   "the note — values from the grid operator's connection "
                   "study.")
        elif "outage data" in low or "occurrence" in low:
            rec = ("Add outage data (outage_rate_value, mttr_hours) to the "
                   "Links whose failure matters — without it they cannot be "
                   "ranked.")
        elif "infeasible" in low:
            rec = ("Islanded, the candidate set cannot meet the target: raise "
                   "candidates' p_nom_max, add firm or storage candidates, or "
                   "relax the ENS target.")
        elif "storageunit" in low or "inapplicable" in low:
            rec = ("No applicable lever: add a StorageUnit (for "
                   "storage_duration) or tag an import Link (for import_cap).")
        add(f"not_established_{sec}", "medium",
            f"{sec}: not established", {"section": sec, "note": note},
            rec, acts)

    # ── frontier knee ──────────────────────────────────────────────────
    fr = (sections.get("frontier") or {}).get("payload") or {}
    ok_points = [p for p in (fr.get("points") or []) if p.get("status") == "ok"]
    knee = fr.get("knee_index")
    # Owner's Q3 rule: a knee needs three solved points — a report stored
    # before the rule may carry one from two, and must not yield a finding.
    if isinstance(knee, int) and 0 <= knee < len(ok_points) \
            and len(ok_points) >= 3 and pack_ens:
        kp = ok_points[knee]
        kt = _num(kp.get("target_permyriad"))
        if kt is not None and abs(kt - pack_ens) > 1e-9:
            cheaper = kt > pack_ens
            add("frontier_knee", "info",
                f"Frontier knee at {_fmt(kt, '‱')} (pack target "
                f"{_fmt(pack_ens, '‱')})",
                {"knee_target_permyriad": kt, "knee_cost_eur":
                 kp.get("total_system_cost_eur"), "ens_cap_permyriad": pack_ens},
                ("At your VOLL, reliability beyond the knee costs more than the "
                 "shed energy it avoids — the pack target is stricter than the "
                 "economic optimum. Keep it if it is a requirement.") if cheaper
                else ("Tightening towards the knee still pays at your VOLL."),
                [] if cheaper else [_action(
                    "run_eh_study", rerun.with_(pack={"ens_cap_permyriad": kt}),
                    f"re-plan at the knee target {kt:g} ‱")])

    # ── fmea_top concentration ─────────────────────────────────────────
    rows = ((sections.get("fmea_top") or {}).get("payload") or {}).get("rows") or []
    crit = [(r, _num(r.get("criticality_eur_per_year")) or 0.0) for r in rows]
    total = sum(c for _, c in crit)
    if total > 0:
        top, c = max(crit, key=lambda rc: rc[1])
        share = c / total
        if share >= 0.5:
            add("fmea_dominant_mode", "medium",
                f"{top.get('name')} carries {share:.0%} of ranked Link risk",
                {"mode": top.get("name"), "criticality_eur_per_year": c,
                 "share": round(share, 3), "occurrence_per_year":
                 top.get("occurrence_per_year"), "severity_eur":
                 top.get("severity_eur")},
                f"One failure mode dominates: consider a redundant path or a "
                f"spare for {top.get('name')}, or reduce its outage rate / "
                "repair time. The redundancy stage prices GENERIC N+1 "
                "generation / conversion / storage options (indicative "
                f"costs), not a spare {top.get('name')} itself — use it as "
                "an order of magnitude.",
                [_action("run_eh_study",
                         rerun.with_(levers={"redundancy": True},
                                     add_stages=["redundancy"]),
                         "price generic N / N+1 / storage scenarios "
                         "(indicative)")])

    # ── DtC ────────────────────────────────────────────────────────────
    dtc = (sections.get("dtc") or {}).get("payload") or {}
    stress = dtc.get("stress") if dtc.get("mode") == "stress+planning" else (
        dtc if dtc.get("mode") == "stress_fixed_plan" else None)
    planning = dtc.get("planning") if dtc.get("mode") == "stress+planning" else (
        dtc if dtc.get("mode") == "planning" else None)
    if stress:
        worst = None
        for row in stress.get("contingencies") or []:
            cu = _num(row.get("critical_unserved_mwh"))
            if cu is not None and cu > 1e-6 and (worst is None or cu > worst[1]):
                worst = (row, cu)
        if worst:
            row, cu = worst
            acts = []
            if not planning:
                acts.append(_action(
                    "run_eh_study", rerun.with_(add_stages=["dtc_planning"])
                    if rerun.base.get("stages") else rerun.with_(
                        stages=["apply_pack", "ens_solve", "dtc_stress",
                                "dtc_planning", "assemble"]),
                    "size what islanded operation needs (dtc_planning)"))
            add("dtc_critical_unserved", "high",
                f"Critical demand unserved when {row.get('contingency')} is lost: "
                f"{_fmt(cu, 'MWh')}",
                {"contingency": row.get("contingency"),
                 "critical_unserved_mwh": cu,
                 "noncritical_unserved_mwh": row.get("noncritical_unserved_mwh"),
                 "critical_unserved_by_load": row.get("critical_unserved_by_load")},
                "The plan cannot keep critical demand on when islanded. "
                "DtC planning sizes the extra local capacity (and its cost) "
                "that would.", acts)
        if stress.get("priority_exact") is False:
            add("dtc_priority_caveat", "low",
                "Per-Load priority may invert on lossy paths",
                {"lossy_links": stress.get("priority_caveat_links"),
                 "line_losses": stress.get("priority_caveat_line_losses")},
                "Critical Loads behind the named lossy Links may be shed first; "
                "read their per-Load numbers with that caveat.")
    if planning:
        solved = [c for c in planning.get("contingencies") or []
                  if c.get("status") in ("ok", "optimal")]
        for c in solved:
            built = _num(c.get("built_p_nom_mw"))
            if built and built > 1e-6:
                add(f"dtc_planning_{c.get('contingency')}", "info",
                    f"Islanded operation needs +{_fmt(built, 'MW')} local "
                    f"capacity ({c.get('contingency')} lost)",
                    {"contingency": c.get("contingency"), "built_p_nom_mw": built,
                     "cost_at_target_eur": c.get("cost_at_target_eur")},
                    "This is the extra capacity the islanded, critical-only "
                    "plan builds — compare its cost with the value of keeping "
                    "critical demand on.")

    # ── levers ─────────────────────────────────────────────────────────
    lev = (sections.get("levers") or {}).get("payload") or {}
    # Options looser than the pack / connection permits are not candidates.
    opts = [o for o in lev.get("options") or []
            if o.get("status") in ("ok", "optimal") and not o.get("ineffective")
            and o.get("meets_target") is not False
            and not o.get("exceeds_pack_cap")
            and _num(o.get("cost_at_target_eur")) is not None]
    if len(opts) >= 2:
        best = min(opts, key=lambda o: _num(o["cost_at_target_eur"]))
        worst_cost = max(_num(o["cost_at_target_eur"]) for o in opts)
        add("levers_best_option", "info",
            f"Cheapest lever option: {best.get('kind')} = "
            f"{_fmt(best.get('value'), best.get('unit') or '')}",
            {"kind": best.get("kind"), "value": best.get("value"),
             "cost_at_target_eur": best.get("cost_at_target_eur"),
             "spread_eur": worst_cost - _num(best["cost_at_target_eur"])},
            "Of the compared options meeting the target within the pack's "
            "limits, this one is cheapest.")
    for o in lev.get("options") or []:
        if o.get("ineffective"):
            add(f"lever_ineffective_{o.get('kind')}_{o.get('value')}", "low",
                f"Lever option {o.get('kind')}={o.get('value')} had no effect",
                {"reason": o.get("ineffective_reason")},
                "This option changed nothing in the solve (e.g. the Link was "
                "already islanded) and is excluded from the comparison.")

    # ── gates ──────────────────────────────────────────────────────────
    gates = sections.get("gates") or {}
    if gates.get("status") == "ok" and (gates.get("payload") or {}).get("scr") == "warn":
        add("scr_warn", "medium", "Weak grid: SCR below the warning threshold",
            {"note": gates.get("note")},
            "The hub's inverter share is large for the grid strength at the "
            "PoC: plan grid-forming capability or an EMT study before relying "
            "on this connection.")

    # ── budget headroom ────────────────────────────────────────────────
    if budget and consumed >= 0.9 * budget:
        more = min(MAX_EH_BUDGET_SOLVES, budget * 2)
        add("budget_tight", "low",
            f"Budget nearly spent ({consumed}/{budget} solves)",
            {"solves_consumed": consumed, "budget_solves": budget},
            "Stages may have been truncated; a larger budget lets every "
            "requested stage finish.",
            [_action("run_eh_study", rerun.with_(budget_solves=more),
                     f"re-run with budget {more}")] if more > budget else [])

    # ── study notes (disclosures) ──────────────────────────────────────
    for note in report.get("notes") or []:
        low = str(note).lower()
        if "dsr_buses" in low or "dsr stays off" in low:
            add("dsr_off", "low", "DSR opted in by the pack but not applied",
                {"note": note},
                "Name the buses whose demand may be curtailed (e.g. cooling, "
                "offices) as dsr_buses — never a critical bus.")

    findings.sort(key=lambda f: SEVERITY_ORDER.get(f["severity"], 9))
    summary = {
        "archetype": report.get("archetype"),
        "certified": report.get("certified"),
        "verdict": verdict,
        "mc_lole_h_per_year": lole,
        "target_lole_h": target,
        "ens_cap_permyriad": pack_ens,
        "achieved_ens_permyriad": achieved,
        "cost_at_target_eur": report.get("cost_at_target_eur"),
        "solves": f"{consumed}/{budget}" if budget else None,
        "completeness": completeness,
    }
    next_steps = [f["title"] for f in findings
                  if f["severity"] in ("high", "medium")][:5]
    if template and template.get("study_notes"):
        summary["template"] = template.get("name")
    return {
        "summary": summary,
        "findings": findings,
        "next_steps": next_steps,
        "how_to_apply": (
            "Each action names an existing tool and its exact arguments. "
            "Offer it; run it only if the user agrees — write and execution "
            "tools ask for confirmation."),
    }


_STALE_SOURCE = "study record (the stored report was cleared by a later solve)"
_RUNNING_MESSAGE = ("the EH study is still running — poll "
                    "get_adequacy_results('eh_study') first")


def review_latest(store: dict, record: dict | None, *,
                  no_data_message: str = "no Energy Hub study has been run "
                                         "in this session") -> dict[str, Any]:
    """Review the latest EH study — the ONE source for the chat tool
    ``review_eh_study`` and ``GET /api/results/eh_review`` (P24).

    ``{'status': 'running'|'no_data'|'ok', ...}``: ``running`` while the
    study record says so; otherwise the stored report (the export shape of
    ``/eh_reference_design``), falling back to the study record's copy when
    a later solve cleared the store — ``stale`` is then true (a boolean, so
    no client parses the ``source`` prose); ``no_data`` when neither exists.
    """
    from services.adequacy.eh_report import eh_reference_design_http_payload

    record = record if isinstance(record, dict) else None
    if record and record.get("status") == "running":
        return {"status": "running", "message": _RUNNING_MESSAGE}
    body, status = eh_reference_design_http_payload(store)
    source, stale = "stored report", False
    if status == 204 or not isinstance(body, dict):
        body = (record or {}).get("report")
        source, stale = _STALE_SOURCE, True
    if not isinstance(body, dict):
        return {"status": "no_data", "message": no_data_message}
    out = review_report(body, record)
    out["source"] = source
    out["stale"] = stale
    out["status"] = "ok"
    return out
