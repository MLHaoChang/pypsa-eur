# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
Report variants "top N by NPV" and "greatest IRR" (business-case 19, K9 / C4).

Pure selection over the decision study's per-option rows (the report's
``question`` section options for the NPV, each option's stored case KPIs for
the IRR). Nothing is recomputed, so the figures equal the compare view.

Order: the ranked figure descending, the other figure descending, then
``option_id``. An option without the ranked figure is not ranked: it is named,
with the reason, and listed after the ranked ones. Options that share the
ranked figure share a rank.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from models.study import DecisionReport, InvestmentCase
from services.study.render_html import num, pct

TOP_NPV = "top_npv"
GREATEST_IRR = "greatest_irr"
VARIANTS = (TOP_NPV, GREATEST_IRR)
DEFAULT_N = 3
MAX_N = 10
SECTION_ID = "option_ranking"

NO_IRR_REASON = "no IRR: the cash flows never turn positive"
NO_NPV_REASON = "no NPV: the study has no figure for this option"


class VariantError(ValueError):
    """A refused variant request: ``kind`` is the error_kind, ``message`` the words."""

    def __init__(self, kind: str, message: str, status: int = 422):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


@dataclass(frozen=True)
class Row:
    option_id: str
    label: str
    npv: float | None
    irr: float | None
    reason: str | None
    rank: int | None  # None: not ranked
    irr_reason: str | None = None


@dataclass(frozen=True)
class Ranking:
    variant: str
    n: int | None
    rule: str
    rows: list[Row]
    unranked: list[Row] = field(default_factory=list)
    study_id: str | None = None
    money: str = "EUR"  # the compare view's money unit, e.g. "EUR 2020"

    def meta(self) -> dict:
        return {"variant": self.variant, "n": self.n, "study_id": self.study_id}


def check_request(variant: str | None, n: int | None) -> None:
    """422 for an unknown variant, a stray ``n`` or an ``n`` outside 1..10."""
    if variant is None:
        if n is not None:
            raise VariantError("report_variant_invalid", "n goes with the variant top_npv only.")
        return
    if variant not in VARIANTS:
        raise VariantError("report_variant_invalid", f"Unknown report variant: {variant}.")
    if variant == GREATEST_IRR and n is not None:
        raise VariantError("report_variant_invalid", "n goes with the variant top_npv only.")
    if variant == TOP_NPV and n is not None and not 1 <= n <= MAX_N:
        raise VariantError("report_variant_invalid", f"n must be between 1 and {MAX_N}.")


def _finite(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and math.isfinite(x) else None


def _plain(reason: str | None) -> str | None:
    """The source's reason when it is a sentence; a code such as ``irr_undefined`` is not."""
    return reason if reason and " " in reason.strip() else None


def option_figures(report: DecisionReport, cases: dict[str, InvestmentCase]) -> list[dict]:
    """Per option: id, label, NPV and IRR as the compare view shows them."""
    out = []
    question = report.sections.get("question")
    for o in ((question.payload or {}).get("options") or []) if question else []:
        case = cases.get(o["option_id"])
        k = case.kpis if case is not None else None
        irr = _finite(k.irr) if k is not None else None
        out.append({"option_id": o["option_id"], "label": o["label"],
                    "npv": _finite(o.get("option_npv")), "irr": irr,
                    "irr_reason": _plain(k.unavailable.get("irr") if k is not None else None)})
    return out


def _names(figures: list[dict]) -> list[str]:
    """The label alone, with the id only where labels clash (one rule for table and refusal)."""
    labels = [f["label"] for f in figures]
    return [f"{f['label']} ({f['option_id']})" if labels.count(f["label"]) > 1 else f["label"]
            for f in figures]


def rank(figures: list[dict], variant: str, n: int | None = None) -> Ranking:
    check_request(variant, n)
    ranked_key, other = ("npv", "irr") if variant == TOP_NPV else ("irr", "npv")
    have = [f for f in figures if f[ranked_key] is not None]
    lacking = sorted((f for f in figures if f[ranked_key] is None), key=lambda f: f["option_id"])
    if len(have) < 2:
        names = ", ".join(_names(lacking)) or "none"
        raise VariantError(
            "report_variant_too_few_options",
            f"Fewer than 2 options have an {ranked_key.upper()} to rank. Left out: {names}.")
    have.sort(key=lambda f: (-f[ranked_key],
                             -(f[other] if f[other] is not None else -math.inf), f["option_id"]))
    limit = (n if n is not None else DEFAULT_N) if variant == TOP_NPV else 1
    rows: list[Row] = []
    rank_no, prev = 0, None
    for i, f in enumerate(have):
        if f[ranked_key] != prev:
            rank_no, prev = i + 1, f[ranked_key]
        if rank_no > limit:
            break
        rows.append(Row(f["option_id"], f["label"], f["npv"], f["irr"], None, rank_no,
                        f["irr_reason"] or NO_IRR_REASON))
    reason = NO_NPV_REASON if ranked_key == "npv" else NO_IRR_REASON
    unranked = [Row(f["option_id"], f["label"], f["npv"], f["irr"],
                    f["irr_reason"] or reason if ranked_key == "irr" else reason, None,
                    f["irr_reason"] or NO_IRR_REASON)
                for f in lacking]
    return Ranking(variant, n if variant == TOP_NPV else None,
                   _rule(variant, limit, len(have), bool(unranked), len(rows) > limit), rows, unranked)


def _rule(variant: str, limit: int, available: int, has_unranked: bool, tied: bool = False) -> str:
    what = "NPV" if variant == TOP_NPV else "IRR"
    text = (f"Ranks the options by {what}, highest first. From the decision study's "
            "option results.")
    if variant == GREATEST_IRR:
        text += " The report shows the option with the greatest IRR."
    elif available <= limit:
        text += f" Shows all {available} options."
    else:
        text += f" The report shows the top {limit}."
    if tied:
        text += " Options that tie on a rank are all shown."
    if has_unranked:
        text += f" Options without an {what} are listed last and not ranked."
    return text


def _cells(x: Row) -> tuple[str, str]:
    """NPV and IRR as the compare view formats them; a missing figure is "—" with its reason."""
    return (num(x.npv) if x.npv is not None else f"— ({NO_NPV_REASON})",
            pct(x.irr) if x.irr is not None else f"— ({x.irr_reason or NO_IRR_REASON})")


def table_rows(r: Ranking) -> list[list[str]]:
    """Rows as the docx table shows them: rank, option, NPV, IRR."""
    everyone = [*r.rows, *r.unranked]
    labels = [x.label for x in everyone]

    def name(x: Row) -> str:  # the label alone, with the id only where labels clash
        return f"{x.label} ({x.option_id})" if labels.count(x.label) > 1 else x.label

    return [[str(x.rank) if x.rank is not None else "—", name(x), *_cells(x)] for x in everyone]
