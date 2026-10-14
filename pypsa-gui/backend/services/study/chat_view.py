"""
What the study chat tools hand the assistant (U3): short, plain views over
the study routes' own payloads.

The chat harness caps a tool result at about 4,000 characters
(`harness/results.py`), and a findings payload with its tornado, its option
cases and its sizing explanations is many times that. These views keep what a
novice's question needs — the verdict in words, its figures, the options in
one line each, what is not established and why — and name the route that
carries the rest. Every code is turned into its sentence through the report's
own table (`report.help_for`), so the assistant and the report say the same
thing; a figure the engine could not state reads "not established", never 0
(ADR-0001).

Pure functions over JSON dicts: nothing here reads a network or the store.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from models.study import Figure
from services.study.report import format_fact, help_for

__all__ = ["RUNNING", "findings_digest", "key_parameters", "ledger_row_digest", "render_sentence", "run_digest",
           "verdict_explanation"]

_REF = re.compile(r"\{\{([a-z_]+)\}\}")
# The findings' sentence templates write the unit after these facts
# ("{{battery_p_nom_mw}} MW", "{{battery_max_hours}} hours"), so the value
# goes in bare; every other fact reads as the report formats it.
_UNIT_FOLLOWS = frozenset({"MW", "h"})


def _figure(raw: Any) -> Figure | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        return Figure.model_validate(raw)
    except ValueError:
        return None


def _bare(fig: Figure) -> str:
    v = float(fig.value)  # type: ignore[arg-type]
    return f"{v:,.3f}".rstrip("0").rstrip(".") if fig.unit == "MW" else f"{v:g}"


def render_sentence(sentence: str | None, facts: Mapping[str, Any]) -> str | None:
    """The verdict sentence with each ``{{fact_id}}`` replaced by its figure."""
    if not sentence:
        return None

    def sub(m: re.Match) -> str:
        fig = _figure(facts.get(m.group(1)))
        if fig is None or fig.value is None:
            return "not established"
        return _bare(fig) if fig.unit in _UNIT_FOLLOWS else format_fact(fig)

    return _REF.sub(sub, sentence)


def _explained(codes: Sequence[str] | None) -> list[dict[str, str]]:
    return [{"code": c, "means": help_for(c)[0]} for c in dict.fromkeys(codes or ())]


def _kpi(raw: Any) -> dict[str, Any] | None:
    fig = _figure(raw)
    if fig is None:
        return None
    out: dict[str, Any] = {"label": fig.label, "reads": format_fact(fig)}
    if fig.unavailable:
        out["why_not"] = fig.unavailable
    return out


def _verdict_core(verdict: Mapping[str, Any]) -> dict[str, Any]:
    facts = verdict.get("facts") or {}
    out: dict[str, Any] = {
        "status": verdict.get("status"),
        "class": verdict.get("class"),
        "sentence": render_sentence(verdict.get("sentence"), facts),
        "option_id": verdict.get("option_id"),
        "headline": [k for k in (_kpi(f) for f in verdict.get("headline_kpis") or []) if k],
    }
    if verdict.get("status") != "ok":
        out["not_established_because"] = _explained(verdict.get("reasons"))
    return out


def _option_line(opt: Mapping[str, Any], attribution: Mapping[str, Any] | None) -> dict[str, Any]:
    sizes = {s.get("asset"): s.get("p_nom_opt") for s in opt.get("sizes") or []
             if isinstance(s, Mapping)}
    line: dict[str, Any] = {"option_id": opt.get("option_id"), "label": opt.get("label"),
                            "solve_status": opt.get("solve_status"), "sizes_mw": sizes}
    if attribution is not None:
        line["battery_npv"] = attribution.get("battery_npv")
        line["battery_status"] = attribution.get("status")
        if attribution.get("battery_npv") is None:
            line["battery_npv_why_not"] = (attribution.get("unavailable") or {}).get("battery_npv")
    return line


def findings_digest(findings: Mapping[str, Any]) -> dict[str, Any]:
    """The `GET .../findings` payload, cut to what a chat answer needs."""
    verdict = findings.get("verdict") or {}
    robustness = findings.get("robustness") or {}
    attrib = {a.get("option_id"): a for a in findings.get("battery_attribution") or []
              if isinstance(a, Mapping)}
    streams = [{"label": s.get("label"), "annual_value": s.get("annual_value")}
               for s in findings.get("value_streams") or []
               if isinstance(s, Mapping) and s.get("annual_value") is not None]
    out: dict[str, Any] = {
        "available": bool(findings.get("available")),
        "verdict": _verdict_core(verdict),
        "options_status": findings.get("options_status"),
        "options": [_option_line(o, attrib.get(o.get("option_id")))
                    for o in findings.get("options") or [] if isinstance(o, Mapping)],
        "robustness": {"status": robustness.get("status"),
                       "option_id": robustness.get("option_id"),
                       "pending": robustness.get("pending") or []},
        "value_streams_option": findings.get("value_streams_option"),
        "value_streams": streams,
        "completeness": findings.get("completeness") or {},
        "more": ("explain_verdict says why; the decision panel shows the tornado, the "
                 "option cases and the sizing explanations."),
    }
    if findings.get("pending_options"):
        out["pending_options"] = findings["pending_options"]
    if robustness.get("status") != "ok":
        out["next_step"] = (
            "run_decision_study with stage='tornado' tests the best option against "
            "each key assumption's range; the verdict needs it.")
    return out


def verdict_explanation(findings: Mapping[str, Any]) -> dict[str, Any]:
    """Why the verdict says what it says: the sentence, its drivers and caveats in words."""
    verdict = findings.get("verdict") or {}
    robustness = findings.get("robustness") or {}
    out = _verdict_core(verdict)
    flips = {r.get("key") for r in robustness.get("tornado") or [] if isinstance(r, Mapping)
             and any(isinstance(v, (int, float)) and v < 0
                     for v in (r.get("npv_low"), r.get("npv_high")))}
    labels = {r.get("key"): r.get("label") for r in robustness.get("tornado") or []
              if isinstance(r, Mapping)}
    out["drivers"] = [{"key": k, "label": labels.get(k) or k,
                       "turns_npv_negative": k in flips}
                      for k in verdict.get("drivers") or []]
    if verdict.get("main_caveat"):
        out["main_caveat"] = {"code": verdict["main_caveat"],
                              "means": help_for(verdict["main_caveat"])[0]}
    out["disclosures"] = _explained(verdict.get("disclosures"))
    if verdict.get("status") == "ok" and verdict.get("reasons"):
        out["also_note"] = _explained(verdict.get("reasons"))
    named = verdict.get("option_id")
    sizing = [e for e in findings.get("explain") or []
              if isinstance(e, Mapping) and (named is None or e.get("option_id") == named)]
    # The sizing explanation's own plain notes (`explain.reading_notes`); its
    # dispatch tables and bus prices stay in the panel.
    out["sizing"] = [{k: e.get(k) for k in ("option_id", "component_class", "name",
                                            "reading_notes", "unavailable")
                      if e.get(k) is not None} for e in sizing]
    return out


def ledger_row_digest(ledger_payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    """One row of a `.../ledger` payload after an edit, with the study's maturity."""
    rows = (ledger_payload.get("ledger") or {}).get("rows") or []
    row = next((r for r in rows if isinstance(r, Mapping) and r.get("key") == key), None)
    out: dict[str, Any] = {"maturity": ledger_payload.get("maturity")}
    if row is not None:
        out["row"] = {k: row.get(k) for k in ("key", "label", "value", "unit", "status",
                                              "provenance", "source", "engine_path")}
    out["next_step"] = ("The last run no longer matches the assumptions: run_decision_study "
                        "re-runs the options.")
    return out


#: Run and tornado statuses that mean "not finished yet".
RUNNING = frozenset({"queued", "running", "aborting"})


def run_digest(rec: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A run or tornado record (`runner` / `tornado_runner`) without its campaign log."""
    if not isinstance(rec, Mapping):
        return None
    keep = ("kind", "status", "fidelity", "current", "options", "solved", "pending",
            "solves_charged", "solves_estimated", "budget_exhausted", "error")
    return {k: rec[k] for k in keep if k in rec and rec[k] not in (None, [], {})}


def key_parameters(ledger: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """
    The ledger's key parameters (``sensitivity_flag`` rows, owner decision 4):
    what the assistant offers the user one by one, each with its default and
    where the default comes from. Everything else stays the defaults pack's.
    """
    rows = (ledger or {}).get("rows") or []
    keep = ("key", "label", "value", "unit", "source", "source_year", "status", "help")
    return [{k: r.get(k) for k in keep if r.get(k) is not None}
            for r in rows if isinstance(r, Mapping) and r.get("sensitivity_flag")]
