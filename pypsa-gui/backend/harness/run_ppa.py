# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 14 S-C (GK B (61) Q-SC1 = A, S1-S8; GK B (56) C8, C9): the
PPA price for the template's target return, as the runner's native step.

`ppa_price` runs after the run's own `run_investment_case` step, solves
nothing and makes no tool call (`solve_ppa_price` is never listed). The case
already priced the PPA when `finance.solve_ppa` is set (`finance.engine.run_case`);
this step reads that price from the report THAT step stored:

- the target is frozen at solve-stage entry from the project's
  `finance.solve_ppa` (`freeze`); with none set the price is not evaluated
  with that reason, never a code default (S5);
- the report is the run's own: the live case record's `started_at` is the
  one the run's case step settled on, and the stored report's
  `assumptions_hash` is that record's (S1);
- only status `ok` with no `solve_ppa_*` flag and a finite positive price
  counts (an allow-list, S2); anything else is not evaluated with its reason;
- the record `run.ppa_price` binds the run, the case, its assumptions hash,
  the network revision and input fingerprint, and the target (S4).

`read` is the one normaliser the hand-off KPI reads (C9): a malformed record
is not evaluated, a binding that no longer holds is stale, and nothing else
(retained results included) ever stands in for it.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

VERSION = 1
SOLVES = 0
IC_TOOL = "run_investment_case"
IC_STUDY = "investment_case"
SOURCE_PATH = "finance.solve_ppa.target_irr"
SOLVED, NOT_EVALUATED, STALE = "solved", "not_evaluated", "stale"
STATES = (SOLVED, NOT_EVALUATED, STALE)
NOT_SET = "no target set"
NO_PRICE = "no price found for it"
# As the finance panel (FinanceInputsEditor) words the target.
IRR_WORDS = "Target equity IRR (post-tax)"
UNREADABLE = "the run's PPA price record cannot be read"
# S2: each status `finance.engine.solve_ppa` emits other than `ok`, in words.
_STATUS_WORDS = (
    (
        "solve_ppa_contract_not_found",
        "the contract to price is not in the investment case",
    ),
    (
        "solve_ppa_ambiguous_contract",
        "the investment case has several PPAs and none is named",
    ),
    ("solve_ppa_not_owner_sold", "the contract is not one the owner sells"),
    ("solve_ppa_not_linear", "the contract's settlement does not scale with its price"),
    (
        "solve_ppa_needs_redispatch",
        "the contract changes the dispatch, so a price alone cannot be solved",
    ),
    (
        "solve_ppa_price_unknown",
        "the contract's price is not set in the investment case",
    ),
    (
        "solve_ppa_cash_not_established",
        "the owner's cash is not established at a zero price",
    ),
    ("solve_ppa_no_root", "none in the search range reaches the target return"),
    (
        "solve_ppa_irr_ambiguous",
        "the price found gives more than one return, so the target is not established",
    ),
)
_FLAG_WORDS = {
    "solve_ppa_target_year_clamped": "the target year lies past the case's last year",
}


def _run_actions():
    from services import run_actions

    return run_actions


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ── the target (S5) ──────────────────────────────────────────────────────────


def target_of(finance) -> tuple[dict | None, str | None]:
    """`(target, None)` from a finance block's `solve_ppa`, else `(None,
    reason)`: none set, or a value that is not a finite return, a whole
    year or a named contract. Never a default."""
    sp = finance.get("solve_ppa") if isinstance(finance, dict) else None
    if sp is None:
        return None, NOT_SET
    irr, year, contract = (
        sp.get(k) if isinstance(sp, dict) else None
        for k in ("target_irr", "target_year", "contract_id")
    )
    if not _finite(irr) or not -1 < irr < 10:
        return None, f"{NOT_SET} (the target return is not a number)"
    if not _whole(year) or year < 1:
        return None, f"{NOT_SET} (the target year is not a whole year)"
    if contract is not None and (not isinstance(contract, str) or not contract.strip()):
        return None, f"{NOT_SET} (the target names no contract)"
    return {
        "target_irr": float(irr),
        "target_year": year,
        "contract_id": contract,
    }, None


def _finance(ctx):
    cfg = ctx.solver_state.get("solver_config")
    raw = cfg.get("finance") if isinstance(cfg, dict) else getattr(cfg, "finance", None)
    return raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw


def _context(task):
    """The run's bound context (the runner's, else the active one), or None
    when it is not the run's project."""
    from services import assistant_tasks
    from services.pypsa_service import PyPSAService

    ctx = assistant_tasks.RUN_CONTEXT.get()
    if ctx is None:
        try:
            ctx = PyPSAService.get_active_context()
        except Exception:  # noqa: BLE001 — no context is no target, never a default
            return None
    return (
        ctx
        if str(getattr(ctx, "project_uuid", "")) == str(task.get("project_id"))
        else None
    )


def freeze(task) -> dict:
    """At solve-stage entry: the target from the project's investment
    settings (its value, year and contract), or none with the reason."""
    ctx = _context(task)
    if ctx is None:
        target, missing = None, "the run's project is not open"
    else:
        try:
            target, missing = target_of(_finance(ctx))
        except Exception:  # noqa: BLE001 — an unreadable config is no target
            target, missing = (
                None,
                f"{NOT_SET} (the investment settings cannot be read)",
            )
    frozen = {
        "kind": _run_actions().PPA_KIND,
        "version": VERSION,
        "target": target,
        "missing": missing,
        "source_path": SOURCE_PATH,
        "solves": SOLVES,
    }
    return {**frozen, "digest": _run_actions().digest(core(frozen))}


def core(frozen) -> dict:
    return {
        k: frozen.get(k)
        for k in ("kind", "version", "target", "missing", "source_path", "solves")
    }


def valid(frozen) -> bool:
    target = frozen.get("target")
    return (
        frozen.get("version") == VERSION
        and frozen.get("solves") == SOLVES
        and frozen.get("source_path") == SOURCE_PATH
        and (
            target_of({"solve_ppa": target})[0] == target
            if target is not None
            else isinstance(frozen.get("missing"), str)
        )
    )


def _percent(value) -> str:
    return f"{value * 100:.4g}%"


def _contract_words(contract_id) -> str:
    ra = _run_actions()
    if contract_id is None:
        return "the investment case's one PPA"
    return (
        ra.SHIPPED_CONTRACT_WORDS.get(contract_id)
        or f"the contract “{ra._display_name(contract_id)}”"
    )


def card(frozen) -> dict:
    """S5: what the step reads, in words: the target return with its unit,
    the year, the contract and where the target comes from."""
    ra = _run_actions()
    target = frozen.get("target")
    if target is None:
        missing = frozen.get("missing") or NOT_SET
        rows = [ra._note("target", f"{IRR_WORDS}: {missing}")]
        says = f"PPA price · not evaluated: {missing}"
    else:
        rows = [
            ra._note("target", f"{IRR_WORDS}: {_percent(target['target_irr'])}"),
            ra._note("year", f"Reached in operating year {target['target_year']}"),
            ra._note(
                "contract", f"Contract priced: {_contract_words(target['contract_id'])}"
            ),
        ]
        says = f"PPA price for a target equity IRR (post-tax) of {_percent(target['target_irr'])}"
    rows += [
        ra._note(
            "solves",
            "Runs no solve: it reads the price from this run's investment case",
        ),
        # UX: the engine key appears only here, in the campus card's Source style.
        ra._note("source", f"Source: investment settings · {SOURCE_PATH}"),
    ]
    return {
        "version": 1,
        "kind": "native",
        "tool": ra.PPA_PRICE,
        "rows": rows,
        "sets": [],
        "says": says,
        "complete": True,
        "digest": frozen.get("digest"),
    }


# ── the run's own case (S1) ──────────────────────────────────────────────────


def _ic_id(step) -> dict | None:
    """The case a completed case step settled on: its job and `started_at`."""
    if not isinstance(step, dict) or step.get("status") != "completed":
        return None
    result, job = step.get("result") or {}, step.get("job") or {}
    started, job_id = result.get("started_at"), result.get("job_id")
    if (
        result.get("study") != IC_STUDY
        or not _finite(started)
        or job.get("started_at") != started
        or not isinstance(job_id, str)
        or job.get("job_id") != job_id
    ):
        return None
    return {"job_id": job_id, "started_at": started}


def _own_ic(task, before=None) -> dict | None:
    """The run's latest case step (before step `before`), not superseded."""
    steps = task.get("steps") or []
    if before is not None:
        steps = steps[
            : next(
                (i for i, s in enumerate(steps) if s.get("id") == before), len(steps)
            )
        ]
    step = next(
        (
            s
            for s in reversed(steps)
            if s.get("tool") == IC_TOOL and not s.get("superseded")
        ),
        None,
    )
    return _ic_id(step)


def _live(ctx) -> dict | None:
    """The case record on the context now (None: none)."""
    with ctx.solver_state_lock:
        record = ctx.solver_state.get(IC_STUDY)
        if not isinstance(record, dict):
            return None
        return {k: record.get(k) for k in ("status", "started_at", "assumptions_hash")}


def _ic_reads(ctx) -> tuple[dict | None, dict | None]:
    """(the case's status body, its full stored report) on `ctx`."""
    from functools import partial

    from fastapi import Response

    from routers.results import get_investment_case, get_investment_case_report
    from services.run_gates import _on

    body = _on(ctx, get_investment_case)
    report = _on(ctx, partial(get_investment_case_report, detail="full"))
    return (
        body if isinstance(body, dict) and not isinstance(body, Response) else None,
        report
        if isinstance(report, dict) and not isinstance(report, Response)
        else None,
    )


def _same_case(ctx, started) -> bool:
    """Re-read after the report reads (review-gk: check the identity of the
    record you publish): still the run's own case, and done."""
    live = _live(ctx)
    return (
        live is not None
        and live.get("started_at") == started
        and live.get("status") == "done"
    )


def _payload(report) -> dict:
    payload = ((report.get("sections") or {}).get("project") or {}).get("payload")
    return payload if isinstance(payload, dict) else {}


def _results_current(ctx) -> bool:
    from services import workflow_tools

    return workflow_tools.solve_freshness(ctx).get("results_current") is True


def _fingerprint(ctx):
    from services import run_gates

    return run_gates.fingerprint(ctx)


def _revision(ctx):
    from services import dirty_state

    return dirty_state.revision(ctx)


def _current_hash(body) -> str | None:
    """The report's hash when the case's current inputs still give it."""
    state = (body or {}).get("report") or {}
    if state.get("stale") is not False or state.get("current_hash") != state.get(
        "assumptions_hash"
    ):
        return None
    return state.get("current_hash")


# ── the step ─────────────────────────────────────────────────────────────────


def _status_words(status, flags) -> str | None:
    """None only for a solved status (S2: an allow-list); else why not, in
    plain words (UX: never a raw status)."""
    if status == "ok":
        bad = sorted(f for f in flags if str(f).startswith("solve_ppa_"))
        if not bad:
            return None
        why = _FLAG_WORDS.get(
            bad[0], "the price found does not give a unique target return"
        )
    elif status is None:
        return NO_PRICE
    else:
        why = next(
            (w for key, w in _STATUS_WORDS if str(status).startswith(key)),
            "the investment case could not solve it",
        )
    return f"{NO_PRICE} ({why})"


def evaluate(task, step_id, frozen, ctx) -> dict:
    """The step's record: `solved` with the price and every binding, else
    `not_evaluated` or `stale` with the reason."""
    target = frozen.get("target") or {}
    rec = {
        "version": VERSION,
        "run_id": task.get("task_id"),
        "step": step_id,
        "ic": None,
        "assumptions_hash": None,
        "network_revision": None,
        "fingerprint": None,
        "target_irr": target.get("target_irr"),
        "target_year": target.get("target_year"),
        "contract_id": target.get("contract_id"),
        "source_path": SOURCE_PATH,
        "price": None,
        "currency": None,
        "money_year": None,
    }

    def out(status, reason):
        return {**rec, "status": status, "reason": reason}

    if not target:
        return out(NOT_EVALUATED, frozen.get("missing") or NOT_SET)
    ic = _own_ic(task, before=step_id)
    if ic is None:
        return out(NOT_EVALUATED, "the run's investment case did not finish")
    rec["ic"] = ic
    if target_of(_finance(ctx))[0] != target:
        return out(
            STALE,
            "the PPA target in the investment settings changed since this step was planned",
        )
    revision, fingerprint = _revision(ctx), _fingerprint(ctx)
    live = _live(ctx)
    if live is None or live.get("started_at") != ic["started_at"]:
        return out(STALE, "a newer investment case replaced this run's own")
    if live.get("status") != "done":
        return out(NOT_EVALUATED, "the run's investment case did not finish")
    body, report = _ic_reads(ctx)
    if not _same_case(ctx, ic["started_at"]):
        return out(
            STALE, "a newer investment case started while the PPA price was read"
        )
    if body is None or report is None:
        return out(NOT_EVALUATED, "the investment case has no report")
    own = report.get("assumptions_hash")
    if not isinstance(own, str) or own != live.get("assumptions_hash"):
        return out(STALE, "the stored investment case is not this run's own")
    rec["assumptions_hash"] = own
    if _current_hash(body) != own:
        return out(STALE, "the investment case's inputs changed since it ran")
    if not _results_current(ctx):
        return out(STALE, "the solve's results no longer describe the network")
    payload = _payload(report)
    flags = payload.get("flags") if isinstance(payload.get("flags"), list) else []
    why = _status_words(payload.get("solve_ppa_status"), flags)
    if why is not None:
        return out(NOT_EVALUATED, why)
    price, currency, year = (
        payload.get(k)
        for k in ("solved_ppa_price", "currency", "solved_ppa_price_money_year")
    )
    if not _finite(price) or price <= 0:
        return out(
            NOT_EVALUATED, f"{NO_PRICE} (the price found is not a positive number)"
        )
    if not isinstance(currency, str) or not currency or not _whole(year):
        return out(
            NOT_EVALUATED, f"{NO_PRICE} (the price has no currency or money year)"
        )
    if (
        fingerprint is None
        or _fingerprint(ctx) != fingerprint
        or _revision(ctx) != revision
    ):
        return out(STALE, "the network changed while the PPA price was read")
    rec.update(
        network_revision=revision,
        fingerprint=fingerprint,
        price=float(price),
        currency=currency,
        money_year=year,
    )
    return out(SOLVED, None)


def dispatch(path, step, frozen, attempt, instance, ctx) -> str:
    """Read the price and keep `run.ppa_price` while this attempt still
    holds the run (C3). Nothing is reserved, charged or solved (S3)."""
    from harness import runner

    path = Path(path)
    record = evaluate(json.loads(path.read_text()), step["id"], frozen, ctx)
    wrote = {}

    def done(t):
        s = next(x for x in t["steps"] if x["id"] == step["id"])
        if not _run_actions()._held(t, s, attempt, instance):
            return False
        t["run"]["ppa_price"] = record
        s.update(
            status="completed",
            result={"status": record["status"], "reason": record["reason"]},
        )
        wrote["ok"] = True

    runner._update(path, done)
    if not wrote:
        logger.info("run %s: the PPA price was not kept (run taken over)", path.stem)
        return "taken_over"
    return _run_actions().PPA_PRICE


# ── the read-back (C9, S6) ───────────────────────────────────────────────────


def shape_ok(rec) -> bool:
    """A record of this version with every field of its type; a solved one
    with every binding and a finite positive price."""
    if (
        not isinstance(rec, dict)
        or rec.get("version") != VERSION
        or rec.get("status") not in STATES
        or rec.get("source_path") != SOURCE_PATH
        or not isinstance(rec.get("run_id"), str)
    ):
        return False
    if rec["status"] != SOLVED:
        return isinstance(rec.get("reason"), str) and bool(rec["reason"])
    ic = rec.get("ic")
    return (
        isinstance(ic, dict)
        and isinstance(ic.get("job_id"), str)
        and _finite(ic.get("started_at"))
        and isinstance(rec.get("assumptions_hash"), str)
        and bool(rec["assumptions_hash"])
        and _whole(rec.get("network_revision"))
        and isinstance(rec.get("fingerprint"), str)
        and bool(rec["fingerprint"])
        and target_of(
            {
                "solve_ppa": {
                    k: rec.get(k) for k in ("target_irr", "target_year", "contract_id")
                }
            }
        )[0]
        is not None
        and _finite(rec.get("price"))
        and rec["price"] > 0
        and isinstance(rec.get("currency"), str)
        and bool(rec["currency"])
        and _whole(rec.get("money_year"))
    )


def _no(state, reason) -> dict:
    return {"state": state, "reason": reason, "value": None, "unit": None}


def _check(task, rec, ctx) -> dict:
    if rec["run_id"] != task.get("task_id"):
        return _no(STALE, "the PPA price record is another run's")
    if _own_ic(task) != rec["ic"]:
        return _no(
            STALE, "the run's investment case ran again after the PPA price was read"
        )
    if ctx is None or str(getattr(ctx, "project_uuid", "")) != str(
        task.get("project_id")
    ):
        return _no(NOT_EVALUATED, "the run's project is not open")
    target = {k: rec[k] for k in ("target_irr", "target_year", "contract_id")}
    if target_of(_finance(ctx))[0] != target:
        return _no(
            STALE,
            "the PPA target in the investment settings changed after the price was read",
        )
    if _revision(ctx) != rec["network_revision"]:
        return _no(STALE, "the network was edited after the PPA price was read")
    if _fingerprint(ctx) != rec["fingerprint"]:
        return _no(STALE, "the network's inputs changed after the PPA price was read")
    live = _live(ctx)
    if live is None or live.get("started_at") != rec["ic"]["started_at"]:
        return _no(STALE, "a newer investment case replaced this run's own")
    body, report = _ic_reads(ctx)
    if not _same_case(ctx, rec["ic"]["started_at"]):
        return _no(
            STALE, "a newer investment case started while the PPA price was read"
        )
    if report is None or report.get("assumptions_hash") != rec["assumptions_hash"]:
        return _no(STALE, "the stored investment case is not this run's own")
    if _current_hash(body) != rec["assumptions_hash"]:
        return _no(
            STALE, "the investment case's inputs changed after the PPA price was read"
        )
    payload = _payload(report)
    if (
        payload.get("solved_ppa_price") != rec["price"]
        or payload.get("currency") != rec["currency"]
        or payload.get("solved_ppa_price_money_year") != rec["money_year"]
    ):
        return _no(STALE, "the stored PPA price is not the one this run read")
    return {
        "state": "read",
        "reason": None,
        "value": rec["price"],
        "unit": f"{rec['currency']}/MWh ({rec['money_year']} money)",
    }


def money(price, currency, year) -> str:
    """UX: the one money notation (`chapters.fmt_eur_short`, the twin of the
    frontend's `utils/money.ts`) with the unit and money year in words."""
    from services.reports.chapters import fmt_eur_short

    amount = fmt_eur_short(price)
    if currency != "EUR":
        amount = amount.replace("€", currency, 1)
    return f"{amount} per MWh, {year} money"


def _when(ts) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).strftime("%-d %b %Y, %H:%M UTC")


def words(got, rec) -> str:
    """The price, or its state with its plain reason, as the user reads it."""
    if got["state"] == "read":
        return money(rec["price"], rec["currency"], rec["money_year"])
    if got["state"] == STALE:
        text = f"Out of date: {got['reason']}"
        ic = rec.get("ic") if isinstance(rec, dict) else None
        if isinstance(ic, dict) and _finite(ic.get("started_at")):
            text += f". It was solved against the investment case of {_when(ic['started_at'])}"
        return text
    return f"Not evaluated: {got['reason']}"


def read(task, ctx) -> dict:
    """`{state, reason, value, unit, text}` for the run's `run.ppa_price`:
    `read` only for a solved record whose every binding still holds now; a
    missing or malformed record is `not_evaluated`, a broken binding `stale`."""
    got = _read_state(task, ctx)
    run = task.get("run") if isinstance(task, dict) else None
    rec = run.get("ppa_price") if isinstance(run, dict) else None
    try:
        text = words(got, rec)
    except Exception:  # noqa: BLE001 — words never turn a read into a 500
        text = f"Not evaluated: {UNREADABLE}"
    return {**got, "text": text}


def _read_state(task, ctx) -> dict:
    task = task if isinstance(task, dict) else {}
    rec = (
        (task.get("run") or {}).get("ppa_price")
        if isinstance(task.get("run"), dict)
        else None
    )
    if rec is None:
        return _no(NOT_EVALUATED, "the run has not read the PPA price")
    if not shape_ok(rec):
        return _no(NOT_EVALUATED, UNREADABLE)
    if rec["status"] != SOLVED:
        # `settled`: the run's own step judged it not evaluated (the hand-off
        # reads that back as such); a stale record never is.
        settled = rec["status"] == NOT_EVALUATED and rec["run_id"] == task.get(
            "task_id"
        )
        return {**_no(rec["status"], rec["reason"]), "settled": settled}
    try:
        return _check(task, rec, ctx)
    except Exception:  # noqa: BLE001 — an unreadable binding is stated, never a 500
        logger.info(
            "run %s: the PPA price binding could not be read",
            task.get("task_id"),
            exc_info=True,
        )
        return _no(NOT_EVALUATED, UNREADABLE)


# ── what the user sees (UX, GK B (61) (a)) ───────────────────────────────────

UI_STATES = {"read": "priced", NOT_EVALUATED: "not_evaluated", STALE: "out_of_date"}


def _recorded(rec) -> dict:
    """The step's own record as the user reads it (no binding re-read)."""
    if not shape_ok(rec):
        got = _no(NOT_EVALUATED, UNREADABLE)
    elif rec["status"] == SOLVED:
        got = {"state": "read", "reason": None}
    else:
        got = _no(rec["status"], rec["reason"])
    return {**got, "text": words(got, rec)}


def monitor(task) -> dict | None:
    """The run monitor's PPA price block: the step's card lines, then the
    price or its state with its plain reason; at hand-off, the KPI row the
    hand-off read (`Out of date` when its binding no longer held). None for
    a run with no PPA price step."""
    ra = _run_actions()
    step = ra.latest(task, ra.PPA_PRICE)
    if step is None:
        return None
    card = step.get("card") if isinstance(step.get("card"), dict) else {}
    lines = [
        r["note"]
        for r in card.get("rows") or []
        if isinstance(r, dict) and isinstance(r.get("note"), str)
    ]
    run = task.get("run") or {}
    kpis = (run.get("handoff") or {}).get("kpis") or []
    kpi = next(
        (r for r in kpis if isinstance(r, dict) and r.get("source") == "run.ppa_price"),
        None,
    )
    if kpi is not None and isinstance(kpi.get("text"), str):
        state, text = UI_STATES.get(kpi.get("status"), "not_evaluated"), kpi["text"]
    elif step.get("status") == "completed" and "ppa_price" in run:
        got = _recorded(run["ppa_price"])
        state, text = UI_STATES[got["state"]], got["text"]
    else:
        state, text = "pending", "Not read yet"
    return {"title": "PPA price", "lines": lines, "state": state, "text": text}


def gap_line(task) -> str | None:
    """The report's line for a run whose PPA price is not established; None
    when it is, or when the run has no PPA price step (as `run_site.gap_line`)."""
    shown = monitor(task or {})
    if shown is None or shown["state"] in ("priced", "pending"):
        return None
    return f"PPA price · {shown['text']}"
