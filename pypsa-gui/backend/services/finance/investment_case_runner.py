"""
The investment-case study runner (IC P4 WP4.6b; plan C1).

`start_investment_case(body, *, build_case, solver_state, state_update,
publish_study)` validates synchronously (the 422 set), publishes a study record
under `investment_case` and runs the finance engine in a worker thread:

    build_case() → load the tax pack (`fin.tax_pack_id` at `financial_close`)
    → `run_case` → `assemble_finance_sections` → store the report under
    `investment_case_report`.

STATE IS INJECTED (the `eh_study_runner` pattern): the router hands over the
solver state, its atomic `state_update`, its `publish_study` claim and — plan
C1 — `build_case`, a zero-argument closure over
`services.results.finance_case.build_finance_case`. This module never imports
`services.results`, `services.solver*`, `routers` or `services.pypsa_service`
(the tripwire's transitive check imports every `services.finance` module).

A `FinanceRefused` (from the adapter or the engine) is a study status with its
code and a refusal report, never a 500. The stop event is read between stages;
an aborted run stores nothing.

Staleness (WP4.6b): the report's `assumptions_hash` is `assumptions_digest` of
the finance inputs, the value flows, the rest of the commercial config, the
dispatch digest of the solve and the pack hashes. The router recomputes it for
`GET /results/investment_case`; a difference marks the report stale.
"""
from __future__ import annotations

import contextvars as _contextvars
import hashlib
import json
import logging
import threading as _threading
import time
from contextlib import nullcontext
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from models.finance import FinanceInputs
from services.finance.case import FinanceCase, FinanceRefused

logger = logging.getLogger("pypsa_gui.results")

STUDY_KEY = "investment_case"
STAGES: tuple[str, ...] = ("build_case", "load_pack", "run_engine", "assemble", "store")


class InvestmentCaseRequest(BaseModel):
    """Start the single-owner finance run. `owner` None = the adapter's
    choice (the single owner of the value-flow config)."""

    model_config = ConfigDict(extra="forbid")
    owner: str | None = Field(default=None, min_length=1)
    # Word characters, spaces, dots and dashes: the id lands in the xlsx and
    # its filename (WP4.6b review B4).
    case_id: str | None = Field(default=None, min_length=1, max_length=120,
                                pattern=r"^[\w .-]+$")


# ── digests (the If-Match tag and the staleness key) ─────────────────────────


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def json_digest(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()[:32]


def finance_digest(raw: dict | None) -> str:
    """The `If-Match` digest of the stored finance inputs (None has one too)."""
    return json_digest({"finance": raw})


def pack_versions(fin: FinanceInputs | None, *, load_pack=None) -> dict[str, str]:
    """`{tax_pack_id: pack_hash}` for the pack the case uses (`{}` without
    one; `"not_found"` when no version covers `financial_close`)."""
    if fin is None or not fin.tax_pack_id:
        return {}
    if load_pack is None:
        from services.finance.packs.base import load_pack
    from services.finance.packs.base import PackNotFound

    try:
        return {fin.tax_pack_id: load_pack(fin.tax_pack_id, as_of=fin.financial_close).pack_hash}
    except PackNotFound:
        return {fin.tax_pack_id: "not_found"}


def assumptions_digest(*, finance: dict | None, commercial: dict | None, dispatch: str | None,
                       packs: dict[str, str], solver: dict | None = None
                       ) -> tuple[str, dict[str, Any]]:
    """(hash, parts): the report's staleness key and the per-input digests
    (so `GET /results/investment_case` can say WHICH input changed).

    `solver` is the solver config without `finance` / `commercial` (they have
    their own parts): the case reads its discount / inflation rates, the VOLL
    and DSR settings and the investment periods at build time, not only at the
    solve (WP4.6b review B1). The whole config is digested — a field that
    only matters to a solve marks the report stale too, which errs safe."""
    commercial = commercial if isinstance(commercial, dict) else None
    parts = {
        "solver_config": json_digest(None if solver is None else
                                     {k: v for k, v in solver.items()
                                      if k not in ("finance", "commercial")}),
        "finance": finance_digest(finance),
        "value_flows": json_digest((commercial or {}).get("value_flows")),
        "commercial": json_digest(None if commercial is None else
                                  {k: v for k, v in commercial.items() if k != "value_flows"}),
        "dispatch": dispatch,
        "packs": dict(sorted(packs.items())),
    }
    return json_digest(parts), parts


def staleness(stored: dict | None, current: tuple[str, dict] | None,
              *, reason: str | None = None) -> dict[str, Any]:
    """The staleness block of `GET /results/investment_case`. A report whose
    current key cannot be computed is stale (never silently current)."""
    if not stored:
        return {"present": False, "stale": None, "changed": [], "reason": None}
    stored_hash = stored.get("assumptions_hash")
    prov = (((stored.get("sections") or {}).get("project") or {}).get("payload") or {}) \
        .get("provenance") or {}
    stored_parts = prov.get("inputs") or {}
    if current is None:
        return {"present": True, "stale": True, "changed": [],
                "reason": reason or "current_assumptions_not_established",
                "assumptions_hash": stored_hash, "current_hash": None}
    cur_hash, cur_parts = current
    changed = sorted(k for k in cur_parts if stored_parts.get(k) != cur_parts.get(k)) \
        if stored_parts else []
    stale = cur_hash != stored_hash
    return {"present": True, "stale": stale, "changed": changed if stale else [],
            "reason": None if not stale else ("inputs_changed" if changed else "hash_differs"),
            "assumptions_hash": stored_hash, "current_hash": cur_hash}


# ── the study ────────────────────────────────────────────────────────────────


def _422(code: str, message: str, **extra) -> HTTPException:
    return HTTPException(422, {"code": code, "message": message, **extra})


def finance_inputs_or_422(raw: Any) -> FinanceInputs:
    try:
        return FinanceInputs.model_validate(raw)
    except ValidationError as exc:
        errs = exc.errors()
        raise _422("finance_inputs_invalid", str(errs[0].get("msg"))[:300] if errs else str(exc),
                   errors=[{"loc": list(e.get("loc", ())), "msg": str(e.get("msg"))[:200]}
                           for e in errs[:20]]) from exc


def start_investment_case(
    body: InvestmentCaseRequest | dict | None,
    *,
    build_case: Callable[[], FinanceCase],
    solver_state: dict,
    state_update: Callable[..., None],
    publish_study: Callable[[str, dict, _threading.Thread], None],
    assumptions: Callable[[], tuple[str, dict]] | None = None,
    case_hash: Callable[[FinanceCase], str | None] | None = None,
    layers: Callable[[FinanceCase], tuple] | tuple | None = None,
    state_lock=None,
) -> dict:
    """Start the investment-case run in a worker thread (see the module
    docstring). The mesh refusal (409 while a solve or another study runs) is
    the caller's early gate AND `publish_study`'s claim, as for every study.

    `assumptions` → (hash, parts) of the inputs the case is built from (the
    router closes over the same config snapshot as `build_case`); `case_hash`
    → the built `FinanceCase`'s hash, recorded as provenance; `layers` — tests
    only — the tax layers `run_case` uses instead of the pack's."""
    from services.finance.engine import run_case
    from services.finance.packs.base import PackNotFound, load_pack
    from services.finance.report import (
        assemble_finance_sections,
        refused_finance_report,
    )

    if isinstance(body, dict):
        try:
            body = InvestmentCaseRequest.model_validate(body)
        except ValidationError as exc:
            raise _422("request_invalid", str(exc.errors()[0].get("msg"))) from exc
    body = body or InvestmentCaseRequest()
    cfg = solver_state.get("solver_config")
    raw = getattr(cfg, "finance", None)
    if raw is None:
        raise _422("finance_inputs_missing",
                   "set the finance inputs first (PUT /api/simulation/finance)")
    fin = finance_inputs_or_422(raw)
    if fin.tax_pack_id:
        try:
            pack0 = load_pack(fin.tax_pack_id, as_of=fin.financial_close)
        except PackNotFound as exc:
            raise _422("tax_pack_not_found", str(exc)) from exc
        packs = {fin.tax_pack_id: pack0.pack_hash}
    else:
        packs = {}
    lock = state_lock if state_lock is not None else nullcontext()
    case_id = body.case_id or STUDY_KEY

    stop_event = _threading.Event()
    record: dict = {
        "status": "running",
        "study": STUDY_KEY,
        "owner": body.owner,
        "case_id": case_id,
        "stage": "queued",
        "stages_done": [],
        "stages": list(STAGES),
        "tax_pack_id": fin.tax_pack_id,
        "packs": dict(packs),
        "assumptions_hash": None,
        "error": None,
        "error_code": None,
        "flags": [],
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": stop_event,
    }

    def progress(stage: str) -> None:
        with lock:
            done = record["stages_done"]
            if record["stage"] in STAGES and record["stage"] not in done:
                done.append(record["stage"])
            record["stage"] = stage

    def finish(status: str, **kw) -> None:
        with lock:
            if record["stage"] in STAGES and record["stage"] not in record["stages_done"] \
                    and status == "done":
                record["stages_done"].append(record["stage"])
            record.update(status=status, finished_at=time.time(), **kw)

    def aborted() -> bool:
        if stop_event.is_set():
            finish("aborted", error=None)
            return True
        return False

    def worker():
        try:
            a_hash, a_parts = (assumptions() if assumptions is not None
                               else assumptions_digest(finance=raw, commercial=None,
                                                       dispatch=None, packs=packs))
            with lock:
                record["assumptions_hash"] = a_hash
            if aborted():
                return
            progress("build_case")
            try:
                case = build_case()
            except FinanceRefused as exc:
                report = refused_finance_report(exc.code, exc.detail, case_id=case_id,
                                                assumptions_hash=a_hash, packs=packs,
                                                owner=body.owner)
                if aborted():           # an aborted run stores nothing (review B5)
                    return
                state_update(**{"investment_case_report": report.model_dump(mode="json")})
                finish("refused", error=str(exc), error_code=exc.code)
                return
            if aborted():
                return
            progress("load_pack")
            pack = None
            if case.inputs.tax_pack_id:
                pack = load_pack(case.inputs.tax_pack_id, as_of=case.inputs.financial_close)
            if aborted():
                return
            progress("run_engine")
            lay = layers(case) if callable(layers) else layers
            try:
                result = run_case(case, pack, layers=lay)
            except FinanceRefused as exc:
                report = refused_finance_report(exc.code, exc.detail, case_id=case_id,
                                                assumptions_hash=a_hash, packs=packs,
                                                owner=case.owner)
                if aborted():           # an aborted run stores nothing (review B5)
                    return
                state_update(**{"investment_case_report": report.model_dump(mode="json")})
                finish("refused", error=str(exc), error_code=exc.code)
                return
            if aborted():
                return
            progress("assemble")
            try:
                c_hash = case_hash(case) if case_hash is not None else None
            except Exception:                                   # noqa: BLE001
                logger.exception("finance_case_hash failed")
                c_hash = None
            report = assemble_finance_sections(
                result, case, case_id=case_id, assumptions_hash=a_hash, packs=packs,
                provenance={"inputs": a_parts, "finance_case_hash": c_hash,
                            "owner": case.owner})
            if aborted():
                return
            progress("store")
            state_update(**{"investment_case_report": report.model_dump(mode="json")})
            finish("done", error=None, flags=list(result.flags),
                   completeness=dict(report.completeness))
        except Exception as exc:  # noqa: BLE001
            logger.exception("investment_case worker failed")
            finish("failed", error=str(exc)[:500], error_code="internal_error")

    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="finance-investment-case")
    record["thread"] = t
    publish_study(STUDY_KEY, record, t)
    return {"status": "running", "study": STUDY_KEY, "case_id": case_id, "owner": body.owner,
            "tax_pack_id": fin.tax_pack_id}


def public_record(record: dict | None) -> dict | None:
    """The record without the worker handle and the stop event."""
    if not record:
        return None
    return {k: (list(v) if isinstance(v, list) else v) for k, v in record.items()
            if k not in ("thread", "stop_event")}
