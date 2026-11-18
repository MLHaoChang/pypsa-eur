"""The harness chapter of a run's report (study harness issue 07, design §3
stage 7): gates per stage, the change ledger, the source ledger, the
envelope's grant and refusals, and the gaps.

Pure: the caller passes the run's task record, its envelope (as stored) and
the chapters the run asked for. The chapter's tables carry the full record,
code-rendered; its payload, the only part a model narrates, carries counts
and states (at most `network_chapters.MAX_FACTS` numbers) and never a model-
or web-written string (a research row's title or quote stays in its table).

SC-15: every chapter the run asked for is either established or named in
`gaps`; so is each part of the approved request the hand-off found not
covered (HH-09A, `coverage`).
"""

from __future__ import annotations

from typing import Any

from harness.gate_words import rule_words
from services.reports import formatting as F

SECTION_ID = "harness_run"
TITLE = "How this study was run"
SOURCE = "the harness run record (task run block and its envelope)"


def _text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return (
            ", ".join(
                f"{k}: {F.fmt_number(v) if isinstance(v, (int, float)) else v}"
                for k, v in value.items()
            )
            if isinstance(value, dict)
            else ", ".join(map(str, value))
        )
    if value is None or isinstance(value, (bool, int, float)):
        return F.fmt_number(value)
    return F.fmt_text(str(value))


def _target(t: dict) -> str:
    return f"{t.get('component')} {t.get('name')} {t.get('attr')}"


def gates_rows(run: dict) -> list[list[str]]:
    rows = []
    for name, stage in (run.get("stages") or {}).items():
        gate = stage.get("gate") or {}
        if not gate.get("rules"):
            rows.append(
                [name, stage.get("status", ""), "", "", stage.get("reason") or "", ""]
            )
        for r in gate.get("rules") or []:
            fact = (r.get("facts") or [{}])[0]
            rows.append(
                [
                    name,
                    stage.get("status", ""),
                    rule_words(r.get("rule", "")),
                    r.get("verdict", ""),
                    f"{fact.get('fact', '')} = {_text(fact.get('value'))}"
                    + (f" {fact['unit']}" if fact.get("unit") else ""),
                    r.get("reason") or r.get("note") or "",
                ]
            )
    return rows


def ledger_rows(run: dict) -> list[list[str]]:
    return [
        [
            r.get("id", ""),
            r.get("stage", ""),
            r.get("case") or "",
            _target(r.get("target") or {}),
            _text(r.get("old")),
            _text(r.get("new")),
            r.get("unit") or "",
            "yes" if r.get("approval") else "envelope" if r.get("receipt") else "",
            "yes" if r.get("reverted") else "no",
        ]
        for r in run.get("ledger") or []
    ]


def source_rows(run: dict) -> list[list[str]]:
    return [
        [
            (r.get("for") or {}).get("gap", ""),
            _text(r.get("value")),
            r.get("unit", ""),
            r.get("basis", ""),
            r.get("status", ""),
            r.get("verification", ""),
            r.get("url", ""),
        ]
        for r in (run.get("research") or {}).get("rows") or []
    ]


def refusal_rows(envelope: dict | None) -> list[list[str]]:
    return [
        [a.get("at", ""), a.get("tool", ""), a.get("reason", "")]
        for a in (envelope or {}).get("audit") or []
        if a.get("event") == "refused"
    ]


def gaps(
    requested: list[tuple[str, str, str | None]], coverage: list[dict]
) -> list[str]:
    """`requested`: (id, title, note) for each requested chapter not
    established; `coverage`: the hand-off's per-part rows."""
    out = [
        f"Chapter not established: {title}" + (f" ({note})" if note else "")
        for _, title, note in requested
    ]
    out += [
        f"Request part {c.get('status')}: {c.get('part')}"
        for c in coverage
        if c.get("status") in ("gap", "partly")
    ]
    return out


def build(
    task: dict,
    envelope: dict | None,
    missing_chapters: list[tuple[str, str, str | None]],
    coverage: list[dict] | None = None,
) -> tuple[dict, dict[str, tuple[list[str], list[list[str]], str]]]:
    """(payload, tables): tables by id as (columns, rows, caption)."""
    from harness import run_handoff

    run = task.get("run") or {}
    coverage = coverage or []
    gap_lines = gaps(missing_chapters, coverage)
    from harness import run_site

    site_gap = run_site.gap_line(task)
    if site_gap:
        gap_lines.append(site_gap)  # issue 33 (GK B (49)): never a silent default
    refusals = refusal_rows(envelope)
    payload = {
        "mode": run.get("mode"),
        "status": run.get("status"),
        "stages": [
            {"stage": n, "status": s.get("status")}
            for n, s in (run.get("stages") or {}).items()
        ],
        "envelope_state": (envelope or {}).get("state"),
        "counts": {
            "ledger_rows": len(run.get("ledger") or []),
            "source_rows": len((run.get("research") or {}).get("rows") or []),
            "envelope_refusals": len(refusals),
            "gaps": len(gap_lines),
            # Study harness 12c (G8): the result gate's rules by verdict; a rule
            # not evaluated is its own count, never a pass.
            **{
                f"result_checks_{v}": n
                for v, n in run_handoff.checks(run)["counts"].items()
            },
        },
        "gaps": gap_lines,
    }
    tables = {
        "harness_gates": (
            ["Stage", "Status", "Rule", "Verdict", "Fact", "Note"],
            gates_rows(run),
            "Exit gate per stage: each rule, its verdict and the native fact it read",
        ),
        "harness_ledger": (
            [
                "Row",
                "Stage",
                "Case",
                "Target",
                "Old",
                "New",
                "Unit",
                "Approved",
                "Reverted",
            ],
            ledger_rows(run),
            "Change ledger: every write the run made",
        ),
        "harness_sources": (
            ["Gap", "Value", "Unit", "Basis", "Status", "Verification", "URL"],
            source_rows(run),
            "Source ledger: every researched value, rejected ones included",
        ),
        "harness_envelope": (
            ["At", "Tool", "Refusal"],
            refusals,
            f"Envelope {(envelope or {}).get('state') or 'none'}: the calls it refused",
        ),
        "harness_gaps": (
            ["Gap"],
            [[g] for g in gap_lines],
            "What this report does not establish",
        ),
    }
    return payload, tables
