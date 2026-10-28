"""LC-18: one read for a base and its sensitivity cases (OJ-29, OJ-30).

`compare_sensitivities` reads the saved base and up to `MAX_CASES` of its LC-17
sensitivity cases (children holding `sensitivity_case.json`) through the same
results-summary engines the Compare view and `compare_scenarios` read
(`services/compare/`). Every value, signed delta (case − base) and delta % is
computed here, with the Compare view's label and unit, so the assistant copies
a table instead of assembling one from pairwise calls (LC-C3).

A case that cannot be compared with the base is listed with its reasons and no
deltas: unsolved, solved on inputs that changed since, inputs or base changed
since it was branched, or a different horizon, mode or solver settings
(`experiment_comparison.basis_digests`, the experiment comparison's own basis).

`rank_by` orders the base and the comparable cases. The answer is a qualified
preference (with its margin and the tie tolerance) or no defensible preference,
never a bare winner; `why` holds at most `MAX_FACTS` facts: the preferred
case's declared changes beside its delta on the ranked metric (#567 H2).

`show` is the existing Compare navigation (`ui_open_panel(compare, compare_a,
compare_b, compare_tab)`) for base vs the preferred case, offered, never
opened. `export_sensitivity_comparison` writes the same report as JSON only
while its `comparison_sha256` is still current.
"""
from __future__ import annotations

import json
import math

from fastapi import HTTPException

MAX_CASES = 8
MAX_ROWS = 12          # declared changes listed per case
MAX_FACTS = 12         # facts in `why` (#567 H2)
TIE_REL = 0.001        # a lead under 0.1 % of |base| is a tie
HOURS_PER_YEAR = 8760

# Compare view tab labels (CompareView.tsx TABS), held equal by a parity test.
TAB_LABELS = {
    "overview": "Overview", "capacity": "Capacity expansion", "dispatch": "Dispatch",
    "loading": "Line loading", "prices": "Prices", "emissions": "Emissions",
    "economics": "Economics", "curtailment": "Curtailment", "lost_load": "Lost load",
    "storage_cycling": "Storage cycling",
}

# name: (label as the Compare view writes it, unit, Compare tab, absolute tie
# tolerance in the unit, used when the base value is 0).
METRICS = {
    "total_cost_meur": ("Total annuitised CAPEX + Operational expenditure", "M€", "economics", 1e-6),
    "opex_meur": ("Operational expenditure", "M€", "dispatch", 1e-6),
    "capex_meur_total": ("Total annuitised CAPEX", "M€", "capacity", 1e-6),
    "capacity_mw_total": ("Total operational capacity", "MW", "capacity", 1e-3),
    "dispatch_gwh_total": ("Energy dispatched", "GWh", "dispatch", 1e-6),
    "emissions_kt": ("Total CO₂ emissions", "kt", "emissions", 1e-6),
    "curtailment_gwh": ("Total curtailed energy", "GWh", "curtailment", 1e-6),
    "lost_load_mwh": ("Total lost load", "MWh", "lost_load", 1e-3),
    "peak_line_loading_pct": ("Peak loading", "%", "loading", 1e-3),
}
RANK_ORDERS = ("lowest_first", "highest_first")

# Plain-word reasons; the assistant shows "<case>: <text>".
_REASONS = {
    "case_unsolved": "not solved yet",
    "case_stale": "its inputs changed since its last solve, so its results are out of date",
    "case_inputs_changed": "its inputs changed since it was branched from the base (inputs include finance)",
    "baseline_changed": "the base changed since this case was branched (inputs include finance)",
    "mode_differs": "solved in a different mode from the base",
    "horizon_differs": "a different time horizon or snapshot weighting from the base",
    "solver_settings_differ": "different solver settings from the base",
    "baseline_unavailable": "the base has no current results to compare with",
}
_BASE_REASONS = {
    "case_unsolved": "the base is not solved yet",
    "case_stale": "the base's inputs changed since its last solve, so its results are out of date",
}


def _refuse(kind: str, message: str, status: int = 422):
    raise HTTPException(status, {"error_kind": kind, "message": message})


def _number(value: float) -> str:
    """Four significant figures, fixed point, thousands separated."""
    a = abs(value)
    if a == 0:
        return "0"
    if a >= 1000:
        return f"{value:,.0f}"
    places = min(12, max(0, 3 - math.floor(math.log10(a))))
    text = f"{value:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _signed(value: float) -> str:
    if value == 0:
        return "0"
    return ("+" if value > 0 else "−") + _number(abs(value))


def _with_unit(text: str, unit: str) -> str:
    return f"{text} {unit}"


def _check_args(baseline_project_id, case_project_ids, metrics, rank_by, rank_order, compare_case):
    if not isinstance(baseline_project_id, str) or not baseline_project_id.strip() or len(baseline_project_id) > 256:
        _refuse("invalid_sensitivity_comparison", "baseline_project_id must be the base project's id or name.")
    if case_project_ids is not None:
        if (not isinstance(case_project_ids, list) or not 1 <= len(case_project_ids) <= MAX_CASES
                or any(not isinstance(c, str) or not c.strip() or len(c) > 256 for c in case_project_ids)):
            _refuse("invalid_sensitivity_comparison",
                    f"case_project_ids must list 1-{MAX_CASES} sensitivity case ids; compare more in batches.")
        if len(set(case_project_ids)) != len(case_project_ids):
            _refuse("invalid_sensitivity_comparison", "Each case may appear once.")
    if metrics is not None and (not isinstance(metrics, list) or not 1 <= len(metrics) <= len(METRICS)
                                or any(m not in METRICS for m in metrics) or len(set(metrics)) != len(metrics)):
        _refuse("invalid_sensitivity_comparison", "metrics must name metrics from the list: " + ", ".join(METRICS))
    if rank_by is not None and rank_by not in METRICS:
        _refuse("invalid_sensitivity_comparison", "rank_by must be one of: " + ", ".join(METRICS))
    if rank_order not in RANK_ORDERS:
        _refuse("invalid_sensitivity_comparison", "rank_order must be lowest_first or highest_first.")
    if compare_case is not None and (not isinstance(compare_case, str) or len(compare_case) > 256):
        _refuse("invalid_sensitivity_comparison", "compare_case must be one of the compared case ids.")


def _resolve(db, user, ref: str):
    """The project as the acting user may open it; any other is not found, with no detail."""
    from services import project_registry
    try:
        return project_registry.resolve_project(db, user, ref)
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            _refuse("sensitivity_project_not_found", "No project with that id is open to you.", 404)
        raise


def _projects(baseline_project_id, case_project_ids):
    """(base, [(case, spec)]): access first, then the child relation."""
    from services import chat_tools, project_registry, workflow_tools as wt
    with chat_tools._acting() as (db, user):
        base = _resolve(db, user, baseline_project_id)
        if case_project_ids is None:
            children = []
            for child in project_registry.direct_children(db, base):
                try:
                    child = project_registry.resolve_project(db, user, str(child.id))
                except HTTPException:
                    continue            # a case the user cannot open is not listed
                spec = wt._read_json(project_registry.project_dir(child) / wt.SENSITIVITY_CASE_FILE)
                if spec:
                    children.append((child, spec))
            if not children:
                _refuse("no_sensitivity_cases", "This base has no sensitivity cases you can open; run a sweep first.", 404)
            if len(children) > MAX_CASES:
                _refuse("invalid_sensitivity_comparison",
                        f"This base has {len(children)} cases; name up to {MAX_CASES} in case_project_ids.")
            return base, sorted(children, key=lambda pair: pair[0].name.lower())
        cases = []
        for ref in case_project_ids:
            child = _resolve(db, user, ref)
            spec = (wt._read_json(project_registry.project_dir(child) / wt.SENSITIVITY_CASE_FILE)
                    if child.parent_project_id == base.id else None)
            if not spec or spec.get("baseline_id") != str(base.id):
                _refuse("not_a_sensitivity_case",
                        f"“{child.name}” is not a sensitivity case of “{base.name}”; compare_scenarios compares two projects.")
            if child.id == base.id:
                _refuse("invalid_sensitivity_comparison", "The base cannot be one of its own cases.")
            cases.append((child, spec))
        return base, cases


def _read(project):
    """The saved project's results summary, context and basis digests."""
    from harness.experiment_comparison import basis_digests
    from services import chat_tools, workflow_tools as wt
    summary = chat_tools._model_to_dict(chat_tools.get_project_results_summary(str(project.id)))
    ctx = wt._load_context(project)
    cfg = ctx.solver_state.get("solver_config")
    cfg = cfg if isinstance(cfg, dict) else getattr(cfg, "__dict__", {})
    return {
        "summary": summary if isinstance(summary, dict) else {},
        "ctx": ctx,
        "fingerprint": wt._network_input_fingerprint(ctx),
        "fresh": wt.solve_freshness(ctx).get("results_current") is True,
        "mode": (cfg.get("mode"), bool((summary or {}).get("is_multi_period"))),
        "basis": basis_digests(ctx),
        "hours": float(ctx.network.snapshot_weightings["objective"].sum()),
    }


def _total(cpv):
    from services.chat_tools import _cpv_total
    return _cpv_total(cpv)


def _values(read) -> dict:
    """metric -> (value or None, why unavailable or None, basis note or None)."""
    from services.chat_tools import _scenario_headlines
    summary = read["summary"]
    head = _scenario_headlines(summary)
    solved = head["has_solve"]
    out = {}

    def block(key):
        b = summary.get(key)
        return b if isinstance(b, dict) and b.get("available") else None

    for name in ("opex_meur", "capex_meur_total", "capacity_mw_total", "dispatch_gwh_total"):
        out[name] = (head.get(name), None if head.get(name) is not None else "not saved for this run", None)
    if head["is_multi_period"]:
        out["total_cost_meur"] = (None, "multi-period: annuitised CAPEX and undiscounted OPEX do not add", None)
    elif head["opex_meur"] is None or head["capex_meur_total"] is None:
        out["total_cost_meur"] = (None, "not saved for this run", None)
    else:
        note = None
        if abs(read["hours"] - HOURS_PER_YEAR) > 0.01 * HOURS_PER_YEAR:
            # services/compare pro-rates annuitised CAPEX to the horizon (nyears),
            # so both terms cover the modelled hours: the LP objective's basis.
            note = f"CAPEX and OPEX both cover the modelled {_number(read['hours'])} h, not a year"
        out["total_cost_meur"] = (head["capex_meur_total"] + head["opex_meur"], None, note)
    for name, key, field, what in (("emissions_kt", "emissions", "total_kt", "emissions"),
                                   ("curtailment_gwh", "curtailment", "total_gwh", "curtailment"),
                                   ("lost_load_mwh", "lost_load", "total_mwh", "lost load")):
        b = block(key) if solved else None
        value = _total(b.get(field)) if b else None
        out[name] = (value, None if value is not None else f"no {what} saved for this run", None)
    loading = block("loading") if solved else None
    peaks = [_total(line.get("peak_loading")) for line in (loading or {}).get("lines") or []]
    peaks = [p for p in peaks if p is not None]
    out["peak_line_loading_pct"] = ((max(peaks) * 100, None, None) if peaks
                                    else (None, "no line loading saved for this run", None))
    for name, (value, why, note) in out.items():
        if value is not None and not math.isfinite(value):
            out[name] = (None, "not a finite number for this run", note)
    return out


def _reasons(base_read, read, spec, base_fingerprint) -> list[str]:
    reasons = []
    if not read["summary"].get("has_solve"):
        reasons.append("case_unsolved")
    elif not read["fresh"]:
        reasons.append("case_stale")
    if read["fingerprint"] != spec.get("input_fingerprint"):
        reasons.append("case_inputs_changed")
    if base_fingerprint != spec.get("baseline_fingerprint"):
        reasons.append("baseline_changed")
    if read["mode"] != base_read["mode"]:
        reasons.append("mode_differs")
    if (read["basis"]["snapshots_sha256"], read["basis"]["weights_sha256"]) != (
            base_read["basis"]["snapshots_sha256"], base_read["basis"]["weights_sha256"]):
        reasons.append("horizon_differs")
    if read["basis"]["solver_config_sha256"] != base_read["basis"]["solver_config_sha256"] and "mode_differs" not in reasons:
        reasons.append("solver_settings_differ")
    return reasons


def _declared(base_network, spec) -> tuple[list[str], bool]:
    """The case's input changes against the base, as the sweep card wrote them."""
    from harness import sweep_card
    rows = []
    for change in ((spec.get("case") or {}).get("changes") or []):
        try:
            found = sweep_card._rows(base_network, change)
        except (HTTPException, KeyError, TypeError):
            found = [{"label": change.get("attribute"), "name": n, "base": None,
                      "case": change.get("value"), "unit": ""} for n in change.get("names") or []]
        for row in found:
            shown = row.get("display") or row
            unit = shown.get("unit") or ""
            base = "not in the base" if shown.get("base") is None else _number(shown["base"]) if isinstance(
                shown["base"], (int, float)) and not isinstance(shown["base"], bool) else str(shown["base"])
            case = _number(shown["case"]) if isinstance(shown["case"], (int, float)) and not isinstance(
                shown["case"], bool) else str(shown["case"])
            rows.append(f"{row['name']} · {row['label']} · {base} → {case}{(' ' + unit) if unit else ''}")
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


def _cell(metric, base_value, value):
    _label, unit, _tab, _tol = METRICS[metric]
    cell = {"value": value, "display": _with_unit(_number(value), unit)}
    if base_value is None:
        return cell
    delta = value - base_value
    cell["delta"] = delta
    cell["delta_display"] = _with_unit(_signed(delta), unit)
    if base_value == 0:
        cell["delta_pct"] = None
        cell["delta_pct_note"] = "zero base: no percentage"
    else:
        pct = delta / abs(base_value) * 100
        cell["delta_pct"] = pct
        cell["delta_display"] += f" ({_signed(pct)} %)"
    return cell


def _rank(metric, order, entries, base_value):
    label, unit, _tab, abs_tol = METRICS[metric]
    tol = TIE_REL * abs(base_value) if base_value not in (None, 0) else abs_tol
    ranked = sorted((e for e in entries if e["value"] is not None),
                    key=lambda e: e["value"], reverse=order == "highest_first")
    out = {"metric": metric, "label": label, "unit": unit, "order": order,
           "tie_tolerance": tol, "tie_tolerance_display": _with_unit(_number(tol), unit)
           + (" (0.1 % of the base)" if base_value not in (None, 0) else " (absolute: the base is zero)"),
           "ranking": [{"name": e["name"], "project_id": e["project_id"], "value": e["value"],
                        "display": _with_unit(_number(e["value"]), unit)} for e in ranked]}
    word = "lowest" if order == "lowest_first" else "highest"
    if len(ranked) < 2:
        out["preference"] = None
        out["no_defensible_preference"] = "fewer than two comparable results have this metric"
        return out
    first, second = ranked[0], ranked[1]
    margin = abs(second["value"] - first["value"])
    if margin < tol:
        out["preference"] = None
        out["no_defensible_preference"] = (
            f"{first['name']} and {second['name']} differ by {_with_unit(_number(margin), unit)}, "
            f"within the tie tolerance of {out['tie_tolerance_display']}")
        return out
    out["preference"] = {
        "name": first["name"], "project_id": first["project_id"], "is_base": first["is_base"],
        "margin": margin, "margin_display": _with_unit(_number(margin), unit), "runner_up": second["name"],
        "qualifier": (f"{word.capitalize()} {label} among the {len(ranked)} comparable results, "
                      f"{_with_unit(_number(margin), unit)} ahead of {second['name']}"),
    }
    return out


def _report(baseline_project_id, case_project_ids, metrics, rank_by, rank_order, compare_case) -> dict:
    from services import experiments as ex
    _check_args(baseline_project_id, case_project_ids, metrics, rank_by, rank_order, compare_case)
    chosen = list(metrics or METRICS)
    if rank_by is not None and rank_by not in chosen:
        chosen.append(rank_by)
    base, cases = _projects(baseline_project_id, case_project_ids)
    base_read = _read(base)
    base_values = _values(base_read)
    base_reasons = (["case_unsolved"] if not base_read["summary"].get("has_solve")
                    else [] if base_read["fresh"] else ["case_stale"])
    base_ok = not base_reasons
    out = {
        "version": 1,
        "metrics": [{"metric": m, "label": METRICS[m][0], "unit": METRICS[m][1],
                     "tab": METRICS[m][2], "tab_label": TAB_LABELS[METRICS[m][2]]} for m in chosen],
        "base": {"name": base.name, "project_id": str(base.id), "comparable": base_ok,
                 "unavailable": [f"{base.name}: {_BASE_REASONS[r]}" for r in base_reasons],
                 "values": {}},
        "cases": [],
    }
    for m in chosen:
        value, why, note = base_values[m]
        out["base"]["values"][m] = ({"value": value, "display": _with_unit(_number(value), METRICS[m][1])}
                                    if value is not None and base_ok else {"value": None, "unavailable": why or "base not comparable"})
        if note and value is not None:
            out["base"]["values"][m]["basis_note"] = note
    entries = [{"name": base.name, "project_id": str(base.id), "is_base": True,
                "values": {m: base_values[m][0] if base_ok else None for m in chosen}}]
    for project, spec in cases:
        read = _read(project)
        reasons = (["baseline_unavailable"] if not base_ok else []) + _reasons(
            base_read, read, spec, base_read["fingerprint"])
        rows, truncated = _declared(base_read["ctx"].network, spec)
        case = {"name": project.name, "project_id": str(project.id), "comparable": not reasons,
                "declared_changes": rows}
        if truncated:
            case["declared_changes_truncated"] = True
        if reasons:
            case["reasons"] = reasons
            case["not_comparable"] = [f"{project.name}: {_REASONS[r]}" for r in reasons]
        else:
            values = _values(read)
            case["values"] = {}
            for m in chosen:
                value, why, note = values[m]
                base_value = base_values[m][0]
                if value is None:
                    case["values"][m] = {"value": None, "unavailable": f"{project.name}: {why}"}
                    continue
                case["values"][m] = _cell(m, base_value, value)
                if base_value is None:
                    case["values"][m]["delta_unavailable"] = f"{base.name}: {base_values[m][1]}"
                if note:
                    case["values"][m]["basis_note"] = note
            entries.append({"name": project.name, "project_id": str(project.id), "is_base": False,
                            "values": {m: values[m][0] for m in chosen}, "rows": rows})
        out["cases"].append(case)
    comparable = sum(c["comparable"] for c in out["cases"])
    out["status"] = ("unavailable" if not base_ok or not comparable
                     else "completed" if comparable == len(out["cases"]) else "partial")
    if rank_by is not None:
        ranking = _rank(rank_by, rank_order,
                        [{**e, "value": e["values"][rank_by]} for e in entries], entries[0]["values"][rank_by])
        out["rank"] = ranking
        preferred = ranking.get("preference")
        if preferred and not preferred["is_base"]:
            entry = next(e for e in entries if e["project_id"] == preferred["project_id"])
            cell = next(c for c in out["cases"] if c["project_id"] == preferred["project_id"])["values"][rank_by]
            facts = entry["rows"][:MAX_FACTS - 1]
            if "delta_display" in cell:
                facts.append(f"{METRICS[rank_by][0]}: {cell['delta_display']} against {base.name}")
            out["why"] = {"case": preferred["name"], "facts": facts,
                          "note": "Declared input changes beside the ranked delta; the solve, not the "
                                  "assistant, links them."}
    target = None
    if compare_case is not None:
        target = next((e for e in entries[1:] if compare_case in (e["project_id"], e["name"])), None)
        if target is None:
            _refuse("invalid_sensitivity_comparison", "compare_case must be one of the comparable cases.")
    elif out.get("rank", {}).get("preference") and not out["rank"]["preference"]["is_base"]:
        target = next(e for e in entries if e["project_id"] == out["rank"]["preference"]["project_id"])
    if target is not None:
        tab = METRICS[rank_by][2] if rank_by else "overview"
        out["show"] = {
            "tool": "ui_open_panel",
            "args": {"panel_id": "compare", "compare_a": base.name, "compare_b": target["name"], "compare_tab": tab},
            "offer": f"Open Compare for {base.name} vs {target['name']} on {TAB_LABELS[tab]}?",
            "note": "Offer it; open only on the user's yes.",
        }
    out["note"] = ("Every value and delta (case − base) is computed from each project's saved results "
                   "with the Compare view's own engines. Show the labels, units and display strings as "
                   "given; list each not_comparable line as written. No input was changed.")
    out["comparison_sha256"] = ex._digest(out)
    return out


def compare_sensitivities(baseline_project_id: str, case_project_ids: list[str] | None = None,
                          metrics: list[str] | None = None, rank_by: str | None = None,
                          rank_order: str = "lowest_first", compare_case: str | None = None) -> dict:
    return _report(baseline_project_id, case_project_ids, metrics, rank_by, rank_order, compare_case)


def export_sensitivity_comparison(baseline_project_id: str, comparison_sha256: str,
                                  case_project_ids: list[str] | None = None, metrics: list[str] | None = None,
                                  rank_by: str | None = None, rank_order: str = "lowest_first",
                                  compare_case: str | None = None) -> dict:
    from services.chat_tools import _save_agent_export
    report = _report(baseline_project_id, case_project_ids, metrics, rank_by, rank_order, compare_case)
    if comparison_sha256 != report["comparison_sha256"]:
        _refuse("sensitivity_comparison_changed",
                "The cases or their results changed since that comparison; compare again and export the new one.", 409)
    payload = json.dumps(report, sort_keys=True, allow_nan=False, indent=2).encode("utf-8")
    meta = _save_agent_export(payload, "sensitivity_comparison_" + report["comparison_sha256"][:16] + ".json",
                              "application/json")
    return dict(meta, comparison_sha256=report["comparison_sha256"], status=report["status"])
