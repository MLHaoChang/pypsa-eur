"""Stage 8 of a harness run: the hand-off (study harness issue 07, design §3
stage 8).

One native read step, run by the runner on the run's bound context, writes
`run.handoff`:

- `kpis`: each `profile.kpis` entry read back from its native source
  (`retained_results`), its value, unit and source from that reader only;
  no number from model text enters it (GK B C14);
- `coverage`: each part of the approved request (the report, each profile
  chapter, each brief case, the stated targets) `covered`, `partly` or
  `gap` (HH-09A). `covered` comes only from a receipt or a passing gate; a
  missing or unreadable one is a `gap`, never `covered` (GK B C13);
- `revert`: "Revert the run's changes" as one LC-16 preview
  (`assistant_tools.preview_project_changes`) over the ledger's
  non-reverted rows on the run's own project; explore-fork rows are not the
  project's and are left out; a row LC-16 cannot preview is listed "by
  hand". The harness never applies it: `apply_project_changes` stays the
  user's card, and the preview is refused as stale once the project changed
  (`assistant_tools.preview_confirmation`'s `_preview_fingerprint` check,
  `preview_stale`) (GK B C7);
- `resume`: where the user picks the run up (its report and revert preview).

`kpis_read` is the stage's gate fact: it reads every KPI natively again and
holds only when each one read and equals the stored row (GK B C3).
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

KIND = "handoff"
PREVIEW_CLASSES = frozenset(
    {"Generator", "Load", "Line", "Link", "StorageUnit", "Store", "Bus", "Transformer"}
)
COVERED, PARTLY, GAP = "covered", "partly", "gap"


# ── KPIs (C14) ───────────────────────────────────────────────────────────────


def _number(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        return None
    return float(value)


def read_kpis(profile: dict) -> list[dict]:
    """Each profile KPI from its retained results section on the bound
    context: `{id, label, value, unit, source, status}`, `status` "read" or
    why it is not (`not_current`, `unreadable`)."""
    from services import retained_results
    from services.workflow_tools import solve_freshness

    try:
        current = solve_freshness().get("results_current") is True
    except Exception:  # noqa: BLE001 -- unreadable freshness: nothing is read
        current = False
    sections: dict = {}
    rows = []
    for kpi in profile.get("kpis") or []:
        row = {
            "id": kpi["id"],
            "label": kpi["label"],
            "unit": kpi["unit"],
            "value": None,
            "source": f"services.retained_results.{kpi['section']}:{'.'.join(kpi['path'])}",
        }
        if not current:
            rows.append({**row, "status": "not_current"})
            continue
        value = None
        try:
            if kpi["section"] not in sections:
                sections[kpi["section"]] = retained_results.adapter(
                    kpi["section"]
                ).capture({})
            value = sections[kpi["section"]]
            for key in kpi["path"]:
                value = value[key]
        except Exception:  # noqa: BLE001 -- an unreadable KPI is stated, never guessed
            value = None
        value = _number(value)
        rows.append(
            {
                **row,
                "value": value,
                "status": "read" if value is not None else "unreadable",
            }
        )
    return rows


def kpis_read(task: dict, profile: dict) -> bool | None:
    """`handoff.kpis_read`: None with no hand-off record or no KPI to read;
    True only when every profile KPI reads natively now and equals its row."""
    stored = ((task.get("run") or {}).get("handoff") or {}).get("kpis")
    if not isinstance(stored, list) or not profile.get("kpis"):
        return None
    fresh = read_kpis(profile)
    if any(r["status"] != "read" for r in fresh):
        return False
    by_id = {r.get("id"): r for r in stored if isinstance(r, dict)}
    return all(
        by_id.get(r["id"], {}).get("value") == r["value"]
        and by_id[r["id"]].get("status") == "read"
        for r in fresh
    )


# ── Coverage (HH-09A, C13) ───────────────────────────────────────────────────


def _part(part, kind, read):
    """One request part; a read that raises is a gap (no receipt)."""
    try:
        status, basis = read()
    except Exception:  # noqa: BLE001 -- an unreadable receipt is a gap, never covered
        logger.info("coverage of %s could not be read", part, exc_info=True)
        status, basis = GAP, "its receipt could not be read"
    if status not in (COVERED, PARTLY, GAP):
        status, basis = GAP, "no receipt"
    return {"part": part, "kind": kind, "status": status, "basis": basis}


def _case(run, name):
    case = next((c for c in run.get("cases") or [] if c.get("name") == name), None)
    if case is None:
        return GAP, "no case of this name ran"
    if case.get("status") == "passed":
        return COVERED, "its case gate passed"
    if case.get("status") in ("failed", "not_runnable"):
        return PARTLY, f"its case settled {case['status']}"
    return GAP, "its case did not settle"


NOT_EVALUATED = "not_evaluated"
VERDICTS = ("pass", "fail", NOT_EVALUATED)


def checks(run: dict) -> dict:
    """Study harness 12c (G8): the result gate's rules counted by verdict,
    and each rule not evaluated (a `warn` one included) named with its
    reason. A not-evaluated rule is its own state, never counted as met; an
    unknown verdict counts as not evaluated."""
    gate = (((run.get("stages") or {}).get("result_gate") or {}).get("gate")) or {}
    counts = dict.fromkeys(VERDICTS, 0)
    unevaluated = []
    for r in gate.get("rules") or []:
        verdict = r.get("verdict") if r.get("verdict") in VERDICTS else NOT_EVALUATED
        counts[verdict] += 1
        if verdict == NOT_EVALUATED:
            unevaluated.append(
                {
                    "rule": r.get("rule"),
                    "severity": r.get("severity"),
                    "reason": r.get("reason"),
                }
            )
    return {"counts": counts, NOT_EVALUATED: unevaluated}


def _checks_not_evaluated(n: int) -> str:
    return f"{n} check{'' if n == 1 else 's'} not evaluated"


def _targets(run):
    stage = (run.get("stages") or {}).get("result_gate") or {}
    gate = stage.get("gate") or {}
    if stage.get("status") == "passed" and gate.get("passed") is True:
        # G8: a gate that passed with rules not evaluated is never all met. The
        # basis counts them; the report's gates table beside it names each (UX (19)).
        n = len(checks(run)[NOT_EVALUATED])
        if n:
            return (
                PARTLY,
                f"the result gate passed; {_checks_not_evaluated(n)} (see the gates table)",
            )
        return COVERED, "the result gate passed"
    return GAP, "the result gate did not pass"


def coverage(task: dict, profile: dict, project_dir) -> list[dict]:
    """Each part of the approved request. With `project_dir` None (the
    report's launch) the report and its chapters are not yet parts: the
    report's harness chapter names the missing chapters itself."""
    from harness import run_report

    run = task.get("run") or {}
    brief = task.get("brief") or {}
    parts = []
    if project_dir is not None:
        doc = None

        def report():
            nonlocal doc
            state = run_report.status(task, project_dir)
            if state != "ready":
                return GAP, f"the report is {state or 'not read'}"
            doc = run_report.saved(task, project_dir)
            return (
                (COVERED, "the run's report was saved")
                if doc is not None
                else (GAP, "the report is not read")
            )

        parts.append(_part("Report", "deliverable", report))
        for sid in profile.get("chapters") or []:

            def chapter(sid=sid):
                sec = next(
                    (
                        s
                        for s in (doc.sections if doc is not None else [])
                        if s.section_id == sid
                    ),
                    None,
                )
                return (
                    (COVERED, "established in the report")
                    if sec is not None and sec.status == "ok"
                    else (GAP, "not established in the report")
                )

            parts.append(_part(f"Chapter {sid}", "deliverable", chapter))
    for case in brief.get("cases") or []:
        name = case.get("name") if isinstance(case, dict) else case
        parts.append(_part(f"Case {name}", "case", lambda name=name: _case(run, name)))
    parts.append(_part("Stated targets", "target", lambda: _targets(run)))
    return parts


# ── Revert preview (C7) ──────────────────────────────────────────────────────


def revert_rows(run: dict) -> tuple[list[dict], list[dict]]:
    """(previewable, by_hand) among the ledger's non-reverted rows on the
    run's own project; explore-fork rows (with a `case`) are excluded."""
    rows = [
        r
        for r in run.get("ledger") or []
        if not r.get("reverted") and not r.get("case")
    ]
    previewable, by_hand = [], []
    for r in rows:
        target = r.get("target") or {}
        ok = (
            target.get("component") in PREVIEW_CLASSES
            and target.get("name")
            and target.get("attr")
            and not isinstance(r.get("old"), bool)
            and _number(r.get("old")) is not None
        )
        (previewable if ok else by_hand).append(r)
    return previewable, by_hand


def revert_preview(run: dict) -> dict:
    """One LC-16 preview restoring each previewable target to its value
    before the run's first write to it."""
    from fastapi import HTTPException

    from services import assistant_tools

    previewable, by_hand = revert_rows(run)
    out = {
        "rows": [r["id"] for r in previewable],
        "by_hand": [r["id"] for r in by_hand],
        "preview_id": None,
    }
    first: dict = {}
    for r in previewable:
        t = r["target"]
        first.setdefault((t["component"], t["name"], t["attr"]), r["old"])
    if not first:
        return out
    changes = [
        {"component_class": c, "names": [n], "attribute": a, "value": old}
        for (c, n, a), old in first.items()
    ]
    try:
        preview = assistant_tools.preview_project_changes(changes)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        # The preview could not be made: every row is then the user's by hand.
        return {
            "rows": [],
            "by_hand": out["rows"] + out["by_hand"],
            "preview_id": None,
            "error": str(detail.get("error_kind") or exc.status_code),
        }
    return {**out, "preview_id": preview["preview_id"]}


# ── The step ─────────────────────────────────────────────────────────────────


def run_step(path, ctx, step) -> str:
    """The hand-off step (the runner, on the run's bound context): read
    back, cover, preview, then complete the step with `run.handoff`."""
    from harness import run_report, runner
    from services import run_profiles

    path = Path(path)
    task = json.loads(path.read_text())
    if str(getattr(ctx, "project_uuid", "")) != task["project_id"]:
        raise runner._Stop(
            "needs_user", "project_changed"
        )  # never another project's read-back
    profile = run_profiles.resolve(task["run"]["profile"])
    project_dir = (
        path.parent.parent
    )  # the task file's project (as `_advance_run` reads its template)
    report = run_report.report_step(task)
    handoff = {
        "kpis": read_kpis(profile),
        "coverage": coverage(task, profile, project_dir),
        "checks": checks(task["run"]),
        "revert": revert_preview(task["run"]),
        "resume": {
            "task_id": task["task_id"],
            "stage": "handoff",
            "report_id": ((report or {}).get("job") or {}).get("report_id"),
        },
    }
    handoff["resume"]["revert_preview_id"] = handoff["revert"].get("preview_id")

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if s["status"] != "pending":
            return False
        t["run"]["handoff"] = handoff
        s["status"] = "completed"

    runner._update(path, done)
    return KIND


def gaps(task: dict) -> int:
    """The count of request parts the hand-off found a `gap` (spec item 3:
    any gap is named; a `partly` part is not counted as not covered). The
    completion notice carries the count only."""
    rows = ((task.get("run") or {}).get("handoff") or {}).get("coverage") or []
    return sum(1 for r in rows if isinstance(r, dict) and r.get("status") == GAP)
