# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""The business-case run (business case 08a; spec §7; GK B BC10 and the 08a plan C1–C9).

The stored inputs are `BusinessCaseConfig`: the line items (`BusinessCaseInputs`) and the
run's parameters (the linked-output parameters `kw_per_gpu` and `lhv_kwh_per_kg`, the
plant's stated capacity for revenue per unit of capacity, and the revenue line to solve a
break-even on). They sit on `SolverConfig.business_case`, written only by
`PUT /api/results/business_case/inputs`, and are in no solve fingerprint (C1).

`start_business_case` runs the business case on the stored investment case. The router
checks synchronously that a current investment-case report exists (C3: missing, stale or
not readable now is 409 `investment_case_missing`). The worker then:

1. rebuilds the IC case and refuses unless its hash is the stored report's (C3);
2. under the network lock, re-reads the IC staleness key and refuses if it moved since the
   route checked it, then reads the linked outputs and the lost production from that same
   solve (C4: never a mix of two solves);
3. builds the business case, runs the engine, reads the P&L, KPIs and break-even;
4. stores the report under `business_case_report`, keyed for staleness on its own inputs
   digest and the IC key it ran on.

An aborted run stores nothing. A typed refusal (`BusinessCaseRefused`, `FinanceRefused`)
is a `refused` record naming its code and field, never a 500 and never a stored zero."""

from __future__ import annotations

import contextvars as _contextvars
import dataclasses
import logging
import math
import threading as _threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from models.business_case import BusinessCaseInputs, BusinessCaseRefused
from services.finance.investment_case_runner import json_digest

logger = logging.getLogger(__name__)

STUDY_KEY = "business_case"
REPORT_KEY = "business_case_report"
STAGES = (
    "rebuild_ic_case",
    "read_outputs",
    "build",
    "run_engine",
    "break_even",
    "store",
)
#: The business case's basis label (spec §7): `counterfactual=()`, never "versus the site
#: today", which is the investment case's.
BASIS = {"id": "project_on_its_own", "label": "the project on its own"}

_FiniteGt0 = Field(default=None, gt=0.0, allow_inf_nan=False)


class BusinessCaseConfig(BaseModel):
    """The stored business-case inputs: the lines plus the run's parameters."""

    model_config = ConfigDict(extra="forbid")

    inputs: BusinessCaseInputs = Field(default_factory=BusinessCaseInputs)
    #: kW of IT load per GPU, for the DC linked outputs that count GPUs. None: such a
    #: line reads `kw_per_gpu_missing`, never a default.
    kw_per_gpu: float | None = _FiniteGt0
    #: kWh per kg (lower heating value), for the H2 linked outputs in energy units.
    lhv_kwh_per_kg: float | None = _FiniteGt0
    #: The plant's capacity (asset → amount in `capacity_unit`) for revenue per unit of
    #: capacity; empty: that KPI is not established.
    capacity: dict[str, float] = Field(default_factory=dict)
    capacity_unit: str = Field(default="MW", min_length=1, max_length=16)
    #: The revenue line whose price the break-even solves for; None: not requested.
    break_even_line: str | None = None

    @model_validator(mode="after")
    def _checked(self) -> BusinessCaseConfig:
        for k, v in self.capacity.items():
            if not k or not math.isfinite(v) or v <= 0.0:
                raise ValueError(f"capacity[{k!r}] must be a finite amount > 0")
        if self.break_even_line is not None:
            kinds = {ln.name: ln.kind for ln in self.inputs.lines}
            if kinds.get(self.break_even_line) != "revenue":
                raise ValueError(
                    f"break_even_line {self.break_even_line!r} names no revenue line"
                )
        return self


def config_digest(raw: dict | None) -> str:
    """The `If-Match` digest of the stored inputs, and the report's staleness key on them."""
    return json_digest(raw)


def _422(code: str, message: str, **extra) -> HTTPException:
    return HTTPException(422, {"code": code, "message": message, **extra})


def config_or_422(raw: Any) -> BusinessCaseConfig:
    """`raw` validated by `BusinessCaseConfig`, which runs every `BusinessCaseInputs` and
    `LineItem` validator (C8), or 422 `business_case_inputs_invalid` with the field paths."""
    try:
        return BusinessCaseConfig.model_validate(raw)
    except ValidationError as exc:
        errs = exc.errors()
        raise _422(
            "business_case_inputs_invalid",
            str(errs[0].get("msg")),
            errors=[
                {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg")}
                for e in errs[:20]
            ],
        ) from exc


def inputs_state(raw: dict | None) -> dict:
    """The body of `GET /results/business_case/inputs`. `status`: `not_set`, `ok` or
    `business_case_inputs_invalid` (a stored value the current model refuses is shown,
    never dropped; the run refuses it)."""
    out: dict[str, Any] = {
        "business_case": raw,
        "digest": config_digest(raw),
        "status": "not_set" if raw is None else "ok",
    }
    if raw is not None:
        try:
            BusinessCaseConfig.model_validate(raw)
        except ValidationError as exc:
            out.update(
                status="business_case_inputs_invalid",
                message=str(exc.errors()[0]["msg"]),
            )
    return out


def refusal_detail(exc: BusinessCaseRefused) -> dict:
    """A typed refusal's wire form. `default_unit_mismatch` is its own code (C6); every
    other refusal is `business_case_refused` with its kind."""
    if exc.kind == "default_unit_mismatch":
        return {
            "code": "default_unit_mismatch",
            "field": exc.field,
            "message": f"a cost default's unit is not the unit the line names "
            f"({exc.field}); the default was not filled",
        }
    return {
        "code": "business_case_refused",
        "refusal": exc.kind,
        "field": exc.field,
        "message": str(exc),
    }


def ic_report_key(stored: dict | None) -> tuple[str | None, str | None]:
    """(assumptions hash, finance case hash) of a stored IC report; None for a missing,
    refused or partial one."""
    if not stored:
        return None, None
    prov = (
        ((stored.get("sections") or {}).get("project") or {}).get("payload") or {}
    ).get("provenance") or {}
    return stored.get("assumptions_hash"), prov.get("finance_case_hash")


# ── the report's wire form ───────────────────────────────────────────────────


def _jsonable(v: Any) -> Any:
    """Arrays and floats to JSON: a non-finite number reads None, never inf or NaN."""
    if v is None:
        return None
    if isinstance(v, np.ndarray):
        return [_jsonable(x) for x in v.tolist()]
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, np.integer):
        return int(v)
    return v


def _output_state(v: Any) -> dict:
    from services.business_case.linked_outputs import NotEvaluated

    if isinstance(v, NotEvaluated):
        return {"status": "not_established", "reason": v.reason}
    return {"status": "ok", "unit": getattr(v, "unit", None)}


def report_body(
    *,
    bc,
    result,
    p,
    k: dict,
    be: dict,
    outputs: dict,
    raw: dict,
    ic_hash: str,
    ic_case_hash: str | None,
) -> dict:
    """The stored report: P&L by year by stream, the KPIs (gross and net of
    pass-through), break-even, the locked rows and each linked output's state."""
    return {
        "business_case_hash": bc.hash,
        "inputs_digest": config_digest(raw),
        "ic_assumptions_hash": ic_hash,
        "ic_case_hash": ic_case_hash,
        "currency": result.case.inputs.currency,
        "basis": dict(BASIS),
        "pnl": {
            "years": [int(y) for y in p.years],
            "streams": _jsonable(p.streams),
            "rows": _jsonable(p.rows),
            "not_finite": list(p.not_finite),
        },
        "kpis": {name: _jsonable(dataclasses.asdict(v)) for name, v in k.items()},
        "break_even": _jsonable(be),
        "locked": [_jsonable(dataclasses.asdict(r)) for r in bc.locked],
        "outputs": {name: _output_state(v) for name, v in sorted(outputs.items())},
        "flags": list(result.flags),
        "created_at": time.time(),
    }


def staleness(
    stored: dict | None,
    *,
    inputs_raw: dict | None,
    ic_current: tuple[str | None, str | None],
) -> dict:
    """Whether the stored report still describes the current inputs and investment case.
    `ic_current` is (the current IC key, why it is None): None when no current IC report
    exists or its key is not readable now. Either is stale, never silently current."""
    if not stored:
        return {"present": False, "stale": None, "changed": [], "reason": None}
    changed = []
    if stored.get("inputs_digest") != config_digest(inputs_raw):
        changed.append("business_case_inputs")
    cur, why = ic_current
    if cur is None:
        return {
            "present": True,
            "stale": True,
            "changed": changed,
            "reason": why or "current_assumptions_not_established",
        }
    if stored.get("ic_assumptions_hash") != cur:
        changed.append("investment_case")
    return {
        "present": True,
        "stale": bool(changed),
        "changed": changed,
        "reason": "inputs_changed" if changed else None,
    }


# ── the study ────────────────────────────────────────────────────────────────


class RunRefused(Exception):
    """A typed refusal of the run (its record's `error_code` and `refusal`)."""

    def __init__(self, code: str, message: str, **detail):
        super().__init__(message)
        self.code, self.detail = code, {"code": code, "message": message, **detail}


def start_business_case(
    cfg: BusinessCaseConfig,
    *,
    raw: dict,
    ic_hash: str,
    ic_case_hash: str | None,
    rebuild_ic_case: Callable[[], Any],
    case_hash: Callable[[Any], str | None],
    read_solve: Callable[[Any, BusinessCaseConfig], tuple[dict, Any]],
    state_update: Callable[..., None],
    publish_study: Callable[[str, dict, _threading.Thread], None],
    load_pack: Callable[[Any], Any] | None = None,
    state_lock=None,
) -> dict:
    """Start the business-case run in a worker thread (module docstring).

    `rebuild_ic_case` → the IC `FinanceCase` (the IC route's adapter); `case_hash` its
    hash; `read_solve(case, cfg)` → (outputs by line name, lost production), read under
    the network lock after re-checking the IC key, raising `StaleInvestmentCase` when it
    moved; `load_pack(case)` → the case's tax pack or None."""
    from services.finance.case import FinanceRefused

    lock = state_lock if state_lock is not None else nullcontext()
    stop_event = _threading.Event()
    record: dict = {
        "status": "running",
        "study": STUDY_KEY,
        "stage": "queued",
        "stages_done": [],
        "stages": list(STAGES),
        "error": None,
        "error_code": None,
        "refusal": None,
        "ic_assumptions_hash": ic_hash,
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
            if (
                status == "done"
                and record["stage"] in STAGES
                and record["stage"] not in record["stages_done"]
            ):
                record["stages_done"].append(record["stage"])
            record.update(status=status, finished_at=time.time(), **kw)

    def aborted() -> bool:
        if stop_event.is_set():
            finish("aborted", error=None)
            return True
        return False

    def refused(code: str, message: str, detail: dict) -> None:
        if not aborted():  # an aborted run stores nothing
            finish("refused", error=message, error_code=code, refusal=detail)

    def worker():
        from services.finance.business_case import (
            break_even,
            build_business_case,
            kpis,
            pnl,
        )
        from services.finance.engine import run_case

        try:
            progress("rebuild_ic_case")
            case = rebuild_ic_case()
            if aborted():
                return
            got = case_hash(case)
            if ic_case_hash is None or got != ic_case_hash:
                raise RunRefused(
                    "investment_case_missing",
                    "the investment case changed since it was run; run the "
                    "investment case first",
                    reason="case_hash_differs",
                )
            progress("read_outputs")
            outputs, lost = read_solve(case, cfg)
            if aborted():
                return
            progress("build")
            bc = build_business_case(case, cfg.inputs, outputs, lost)
            if aborted():
                return
            progress("run_engine")
            pack = load_pack(case) if load_pack is not None else None
            result = run_case(bc.case, pack)
            p = pnl(result, receivable_days=cfg.inputs.receivable_days)
            k = kpis(
                result,
                p,
                capacity=cfg.capacity or None,
                capacity_unit=cfg.capacity_unit,
            )
            if aborted():
                return
            progress("break_even")
            if cfg.break_even_line is None:
                be = {"status": "not_requested", "line": None}
            else:
                be = {
                    **break_even(
                        case,
                        cfg.inputs,
                        outputs,
                        cfg.break_even_line,
                        pack,
                        lost_production=lost,
                    ),
                    "line": cfg.break_even_line,
                }
            if aborted():
                return
            progress("store")
            body = report_body(
                bc=bc,
                result=result,
                p=p,
                k=k,
                be=be,
                outputs=outputs,
                raw=raw,
                ic_hash=ic_hash,
                ic_case_hash=got,
            )
            # The stop check, the store and the finish are one step under the state lock,
            # which the abort route reads the record under: an abort seen here stores
            # nothing; one that reads the record after this step sees it finished.
            with lock:
                if aborted():
                    return
                state_update(**{REPORT_KEY: body})
                finish("done", error=None, business_case_hash=bc.hash)
        except RunRefused as exc:
            refused(exc.code, str(exc), exc.detail)
        except BusinessCaseRefused as exc:
            d = refusal_detail(exc)
            refused(d["code"], d["message"], d)
        except FinanceRefused as exc:
            refused(
                exc.code,
                str(exc),
                {"code": exc.code, "message": str(exc), "detail": exc.detail},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("business_case worker failed")
            finish("failed", error=str(exc)[:500], error_code="internal_error")

    _ctx = _contextvars.copy_context()
    t = _threading.Thread(
        target=lambda: _ctx.run(worker), daemon=True, name="finance-business-case"
    )
    record["thread"] = t
    publish_study(STUDY_KEY, record, t)
    return {"status": "running", "study": STUDY_KEY, "ic_assumptions_hash": ic_hash}


class StaleInvestmentCase(RunRefused):
    """The IC key moved between the route's check and the solve read (C4)."""

    def __init__(self, reason: str):
        super().__init__(
            "investment_case_missing",
            "the investment case is out of date (the project changed since it "
            "was run); run the investment case first",
            reason=reason,
        )
