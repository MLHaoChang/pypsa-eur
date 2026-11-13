"""Grid-extension break-even route (homer-parity 03d).

`POST /api/projects/{name}/studies/{study_id}/grid-extension-break-even` answers how
far the grid could be extended before an off-grid system is cheaper, from one 03a
supply architecture. Read-only: it writes nothing.

The 03a row holds an annual cost, not a net present cost, so the off-grid NPC is
DERIVED here: `total_cost_eur_per_year x annuity(rate, horizon)`, with the rate and
horizon the study's own ledger states (read through the readers `compile.py` uses).
That is an annualised approximation and the result says so. The caller types the grid
side and declares its basis; every field of that basis must equal the study's, and a
mismatch refuses (`basis_mismatch`), never converts.
"""

from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from routers.studies import (
    _base_row,
    _load,
    _project_dir,
    _refuse_unless_enabled,
    require_decision_studies_enabled,
)
from services.finance.grid_extension_breakeven import grid_extension_break_even
from services.study import compile as study_compile

router = APIRouter(dependencies=[Depends(require_decision_studies_enabled)])

RATE_ROW = "discount_rate"
HORIZON_ROW = "battery_storage_lifetime_years"
APPROXIMATION = (
    "annualised approximation: the off-grid NPC is the 03a row's annual cost times the "
    "annuity factor at the study's discount rate and horizon, not a dispatch-by-year "
    "net present cost"
)

_Money = Annotated[float, Field(allow_inf_nan=False)]
_NonNeg = Annotated[float, Field(ge=0, allow_inf_nan=False)]
_Pos = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class GridBasis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(max_length=8)
    currency_year: int = Field(ge=1900, le=2200)
    discount_rate: _Money
    horizon_years: int = Field(ge=1, le=200)


class GridExtensionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    architecture_id: str = Field(min_length=1, max_length=64)
    eur_per_km: _NonNeg
    grid_supply_npc: _Money
    grid_basis: GridBasis
    fixed_eur: _NonNeg = 0.0
    max_km: _Pos = 10_000.0


def _refuse(status: int, detail: dict) -> HTTPException:
    return HTTPException(status, detail=detail)


def study_assumptions(study) -> dict:
    """The study's discount rate, horizon and currency year, or a typed refusal."""
    ledger = study.ledger
    if ledger is None or study.currency_year is None:
        raise _refuse(
            409,
            {
                "error_kind": "assumptions_not_set",
                "message": "set the discount rate and horizon: the study has no stored assumptions "
                "ledger or no currency year",
            },
        )
    try:
        rate = study_compile._ledger_value(ledger, RATE_ROW)
        horizon = study_compile._whole_years(ledger, HORIZON_ROW)
    except study_compile.CompileError as exc:
        raise _refuse(
            409,
            {
                "error_kind": "assumptions_not_set",
                "message": f"set the discount rate and horizon: {exc}",
            },
        ) from None
    if rate is None:
        raise _refuse(
            409,
            {
                "error_kind": "assumptions_not_set",
                "message": "set the discount rate and horizon: no discount rate",
            },
        )
    rate = float(rate)
    if (False):
        raise _refuse(
            422,
            {
                "error_kind": "discount_rate_invalid",
                "message": f"the study's discount rate {rate!r} is not usable",
            },
        )
    return {
        "currency": "EUR",
        "currency_year": int(study.currency_year),
        "discount_rate": rate,
        "horizon_years": horizon,
    }


def annuity_factor(rate: float, horizon: int) -> float:
    """Present value of 1 per year for `horizon` years; `horizon` at a zero rate."""
    try:
        f = float(horizon) if rate == 0.0 else (1.0 - (1.0 + rate) ** -horizon) / rate
    except (OverflowError, ZeroDivisionError):
        f = math.inf
    if not math.isfinite(f) or f <= 0.0:
        raise _refuse(
            422,
            {
                "error_kind": "npc_not_finite",
                "message": "the annuity factor is not a finite positive number",
            },
        )
    return f


def _row(rec: dict | None, architecture_id: str) -> dict:
    if rec is None:
        raise _refuse(
            404,
            {
                "error_kind": "supply_architectures_never_run",
                "message": "the supply-architecture comparison has not been run",
            },
        )
    if rec.get("status") == "running":
        raise _refuse(
            409,
            {
                "error_kind": "supply_architectures_running",
                "message": "the comparison is still running",
            },
        )
    if rec.get("stale"):
        raise _refuse(
            409,
            {
                "error_kind": "supply_architectures_stale",
                "message": "the base project changed since the comparison; compare the architectures again",
            },
        )
    rows = rec.get("rows")
    if not isinstance(rows, list):
        raise _refuse(
            409,
            {
                "error_kind": "supply_architectures_not_evaluated",
                "message": "the comparison has no readable table",
            },
        )
    row = next((r for r in rows if r.get("architecture_id") == architecture_id), None)
    if row is None:
        raise _refuse(
            404,
            {
                "error_kind": "architecture_unknown",
                "message": "no such supply architecture in the table",
            },
        )
    cost = row.get("total_cost_eur_per_year")
    ok_cost = (
        isinstance(cost, (int, float))
        and not isinstance(cost, bool)
        and math.isfinite(cost)
        and cost >= 0
    )
    if row.get("status") != "ok" or not ok_cost:
        raise _refuse(
            409,
            {
                "error_kind": "architecture_not_solved",
                "message": "that architecture has no solved annual cost, so it has no off-grid NPC",
                "architecture_status": row.get("status"),
            },
        )
    return row


def evaluate(study, rec: dict | None, body: GridExtensionRequest) -> dict:
    """The break-even result for `body` against the stored 03a `rec` (pure: no I/O)."""
    row = _row(rec, body.architecture_id)
    basis = study_assumptions(study)
    declared = body.grid_basis.model_dump()
    bad = [k for k in basis if declared[k] != basis[k]]
    if bad:
        raise _refuse(
            422,
            {
                "error_kind": "basis_mismatch",
                "message": "grid_basis must equal the study's basis on every field; nothing is converted",
                "fields": sorted(bad),
                "expected": basis,
            },
        )
    cost = float(row["total_cost_eur_per_year"])
    npc = cost * annuity_factor(basis["discount_rate"], basis["horizon_years"])
    if not math.isfinite(npc):
        raise _refuse(
            422,
            {
                "error_kind": "npc_not_finite",
                "message": "the derived off-grid NPC is not finite",
            },
        )
    offgrid_basis = {**basis, "approximation": "annualised"}
    out = grid_extension_break_even(
        eur_per_km=body.eur_per_km,
        offgrid_npc=npc,
        grid_supply_npc=body.grid_supply_npc,
        offgrid_basis=offgrid_basis,
        grid_basis={
            "currency": declared["currency"],
            "currency_year": declared["currency_year"],
        },
        fixed_eur=body.fixed_eur,
        max_km=body.max_km,
    )
    return {
        **out,
        "architecture_id": row["architecture_id"],
        "total_cost_eur_per_year": cost,
        "offgrid_npc": npc,
        "basis": basis,
        "approximation": APPROXIMATION,
    }


@router.post("/{study_id}/grid-extension-break-even")
def grid_extension_break_even_route(
    study_id: str,
    body: GridExtensionRequest,
    project: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> dict:
    """Break-even grid-extension distance for one stored 03a architecture. Writes nothing."""
    from services.study import supply_architecture_runner

    _refuse_unless_enabled()
    study = _load(project, study_id)
    base = _base_row(project, db, user)
    if base is None:
        raise HTTPException(404, f"Project '{project.name}' not found")
    rec = supply_architecture_runner.get(
        study_id, base_row=base, base_dir=_project_dir(project)
    )
    return evaluate(study, rec, body)
