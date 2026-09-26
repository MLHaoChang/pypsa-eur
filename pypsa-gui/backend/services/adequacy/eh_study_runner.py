"""Energy Hub study HTTP runner — validation, record, worker, publish.

Thin handlers in ``routers/results.py`` keep mesh refusal and the FastAPI
decorator; this module owns the synchronous 422 set through ``publish_study``.
State is injected so this module never imports ``routers.*``.
"""
from __future__ import annotations

import contextvars as _contextvars
import logging
import queue as _queue
import threading as _threading
import time

from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict, Field, ValidationError

from models.energy_hub import (
    DEFAULT_EH_BUDGET_SOLVES,
    MAX_EH_BUDGET_SOLVES,
    ArchetypePack,
    DtcConfig,
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.results")

_PACK_FACTORY = {
    "strong_grid": default_strong_grid_pack,
    "weak_flexible": default_weak_flexible_pack,
    "off_grid": default_off_grid_pack,
}


class EhStudyRequest(_BaseModel):
    """Start an EH reference-design study for one archetype pack.

    The P13 knobs arrive as plain objects and are validated in
    ``start_eh_study`` (not by FastAPI), so the HTTP route and the chat tool
    — which builds this model directly — refuse with the same 422 and the
    same field path.
    """

    archetype: str | None = None
    stages: list[str] | None = None
    budget_solves: int | None = None
    pack_overrides: dict[str, Any] | None = None
    dtc_config: dict[str, Any] | None = None
    dsr_buses: list[str] | None = None
    mc: dict[str, Any] | None = None


class LeverOverrides(_BaseModel):
    model_config = ConfigDict(extra="forbid")
    redundancy: bool | None = None
    import_cap: bool | None = None
    storage_duration: bool | None = None


class PackOverrides(_BaseModel):
    """What a caller may change on the factory pack (plan P13). Anything
    else is refused rather than silently ignored. ``null`` means "keep the
    pack's value" — overrides set, they never clear."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    ens_cap_permyriad: float | None = Field(default=None, gt=0)
    target_lole_h: float | None = Field(default=None, ge=0)
    certification_metric: Literal["mc_lole", "none"] | None = None
    # > 0: a 0 MW cap on a fixed import Link is refused by preflight
    # (link_p_nom_invalid) AFTER the worker starts; an islanded hub is the
    # off_grid archetype, not a weak_flexible cap of zero.
    import_p_nom_mw: float | None = Field(default=None, gt=0)
    mc_certify_required: bool | None = None
    frontier_default: bool | None = None
    dtc_stress_default: bool | None = None
    dtc_planning_default: bool | None = None
    levers: LeverOverrides | None = None


class McOptions(_BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    draws: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0)
    cov_target: float | None = Field(default=None, gt=0, le=1)


class DtcConfigRequest(_BaseModel):
    """Request-side DtC config: a mistyped key is refused, not ignored."""

    model_config = ConfigDict(extra="forbid")
    critical_bus_ids: list[str] = Field(default_factory=list)
    critical_load_ids: list[str] = Field(default_factory=list)
    islanding_contingencies: list[str] = Field(default_factory=list)


def _validation_422(prefix: str, exc: ValidationError) -> HTTPException:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in (prefix, *err.get("loc", ())) if x != "")
        parts.append(f"{loc}: {err.get('msg')}")
    return HTTPException(422, "; ".join(parts))


def apply_pack_overrides(pack: ArchetypePack, overrides: dict | None, *,
                         raise_http: bool = False) -> ArchetypePack:
    """Merge caller overrides onto a factory pack and RE-VALIDATE the result
    (so pack-level rules — e.g. an AvailabilityTarget needs a target — hold
    for the merged pack, not just for each field). ``pack_hash`` follows."""
    if not overrides:
        return pack
    try:
        ov = PackOverrides.model_validate(overrides)
        merged = pack.model_dump(mode="python")
        avail = merged["availability"]
        for key in ("ens_cap_permyriad", "target_lole_h", "certification_metric"):
            val = getattr(ov, key)
            if val is not None:
                avail[key] = val
        if ov.import_p_nom_mw is not None:
            merged["import_overlay"]["import_p_nom_mw"] = ov.import_p_nom_mw
        for key in ("mc_certify_required", "frontier_default",
                    "dtc_stress_default", "dtc_planning_default"):
            val = getattr(ov, key)
            if val is not None:
                merged[key] = val
        if ov.levers is not None:
            for key, val in ov.levers.model_dump(exclude_none=True).items():
                merged["levers"][key] = val
        out = ArchetypePack.model_validate(merged)
    except ValidationError as exc:
        if raise_http:
            raise _validation_422("pack_overrides", exc) from exc
        raise
    # Accepted-then-ignored is refused: the report would claim a parameter
    # the driver never applied (decision 6 / P3c-B1 honesty; P13 gate).
    problems: list[str] = []
    if ov.import_p_nom_mw is not None and out.archetype != "weak_flexible":
        problems.append(
            f"pack_overrides.import_p_nom_mw: only weak_flexible applies an "
            f"import cap; {out.archetype} does not")
    if out.archetype == "off_grid" and out.levers.import_cap:
        problems.append(
            "pack_overrides.levers.import_cap: off_grid islands the import "
            "Links, so an import-cap lever is a no-op there")
    a = out.availability
    if a.certification_metric == "none" and (
            a.target_lole_h is not None or out.mc_certify_required):
        problems.append(
            "pack_overrides.certification_metric: 'none' conflicts with the "
            "pack's LOLE target / mc_certify_required, which still certify — "
            "overrides cannot clear a target")
    if problems:
        if raise_http:
            raise HTTPException(422, "; ".join(problems))
        raise ValueError("; ".join(problems))
    return out


def normalised_overrides(overrides: dict | None) -> dict | None:
    """The validated overrides as the study used them (None-free)."""
    if not overrides:
        return None
    return PackOverrides.model_validate(overrides).model_dump(
        exclude_none=True) or None


def _validate_dtc_config(raw: dict, n) -> DtcConfig:
    try:
        dtc = DtcConfig.model_validate(
            DtcConfigRequest.model_validate(raw).model_dump())
    except ValidationError as exc:
        raise _validation_422("dtc_config", exc) from exc
    missing = (
        [f"bus {b!r}" for b in dtc.critical_bus_ids if b not in n.buses.index]
        + [f"load {x!r}" for x in dtc.critical_load_ids if x not in n.loads.index]
        + [f"link {x!r}" for x in dtc.islanding_contingencies
           if x not in n.links.index])
    if missing:
        raise HTTPException(
            422, "dtc_config names components not on the network: "
            + ", ".join(missing))
    return dtc


def start_eh_study(
    body: EhStudyRequest | None,
    *,
    solver_state: dict,
    state_update,
    publish_study,
):
    """
    Start the Energy Hub reference-design study in a worker thread.

    ASYNCHRONOUS BY CONSTRUCTION: pack apply + ENS solve (+ optional
    redundancy / levers / DtC stages) must never block a request. 409 while
    another study or a foreground solve is running (mesh refuse is the
    caller's job; this publishes under the claim).
    """
    from services.adequacy.eh_study import run_eh_study

    body = body or EhStudyRequest()
    archetype = getattr(body, "archetype", None)
    if archetype is None:
        raise HTTPException(
            422,
            "archetype is required: one of strong_grid, weak_flexible, off_grid")
    if archetype not in _PACK_FACTORY:
        raise HTTPException(
            422,
            f"unknown archetype {archetype!r}; expected one of "
            f"{', '.join(_PACK_FACTORY)}")

    budget = body.budget_solves
    if budget is None:
        budget = DEFAULT_EH_BUDGET_SOLVES
    try:
        budget = int(budget)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "budget_solves must be an integer") from exc
    if not (1 <= budget <= MAX_EH_BUDGET_SOLVES):
        raise HTTPException(
            422,
            f"budget_solves must be between 1 and {MAX_EH_BUDGET_SOLVES}; "
            f"got {budget}")

    from services.adequacy import mc as _mc

    stages = body.stages
    if stages is not None:
        from services.adequacy.eh_study import validate_stages
        try:
            stages = list(validate_stages(stages))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    cfg = solver_state.get("solver_config")
    if cfg is None:
        raise HTTPException(422, "solver settings are not configured")

    n = PyPSAService.get_network()
    if n is None:
        raise HTTPException(422, "no network is loaded")
    lock = PyPSAService.get_lock()

    # P13: every refusal happens HERE, before the worker exists.
    pack = apply_pack_overrides(
        _PACK_FACTORY[archetype](), body.pack_overrides, raise_http=True)
    with lock:  # component names read from the live network
        dtc_config = (_validate_dtc_config(body.dtc_config, n)
                      if body.dtc_config is not None else None)
        bus_names = set(map(str, n.buses.index))
    dsr_buses = None
    if body.dsr_buses is not None:
        dsr_buses = list(dict.fromkeys(str(b) for b in body.dsr_buses))
        if not pack.dsr_opt_in:
            raise HTTPException(
                422, f"dsr_buses only apply to packs with dsr_opt_in "
                f"(weak_flexible); {archetype!r} does not opt into DSR")
        unknown = [b for b in dsr_buses if b not in bus_names]
        if unknown:
            raise HTTPException(
                422, f"dsr_buses names buses not on the network: {unknown}")
    try:
        mc_opts = McOptions.model_validate(body.mc or {})
    except ValidationError as exc:
        raise _validation_422("mc", exc) from exc
    if mc_opts.draws is not None and mc_opts.draws > _mc.MAX_DRAWS:
        raise HTTPException(
            422, f"mc.draws must be at most {_mc.MAX_DRAWS} (the MC draw cap); "
            f"got {mc_opts.draws}")
    mc_kwargs = {k: v for k, v in (("mc_draws", mc_opts.draws),
                                   ("mc_seed", mc_opts.seed),
                                   ("mc_cov_target", mc_opts.cov_target))
                 if v is not None}

    stop_event = _threading.Event()
    log_queue: _queue.SimpleQueue = _queue.SimpleQueue()
    record: dict = {
        "status": "running",
        "study": "eh_study",
        "archetype": archetype,
        "stages": list(stages) if stages is not None else None,
        "budget_solves": budget,
        "pack_overrides": normalised_overrides(body.pack_overrides),
        "report": None,
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": stop_event,
    }

    def worker():
        try:
            report = run_eh_study(
                n,
                pack,
                cfg,
                lock=lock,
                stop_event=stop_event,
                log_queue=log_queue,
                stages=stages,
                budget_solves=budget,
                state_update=state_update,
                store=solver_state,
                dtc_config=dtc_config,
                dsr_buses=dsr_buses,
                **mc_kwargs,
            )
            pipeline = getattr(report, "pipeline", None)
            aborted = bool(getattr(pipeline, "aborted", False))
            # A stage that ran and produced no evidence (infeasible ENS
            # solve) is a FAILED study with a partial report — not an abort
            # the user asked for.
            failed = [
                rec for rec in (getattr(pipeline, "stages", None) or [])
                if getattr(rec, "status", None) == "failed"
            ]
            payload = (report.model_dump(mode="json")
                       if hasattr(report, "model_dump") else report)
            if aborted or stop_event.is_set():
                status, error = "aborted", None
            elif failed:
                status = "failed"
                error = "; ".join(rec.note or rec.stage for rec in failed)
            else:
                status, error = "done", None
            with PyPSAService.get_solver_state_lock():
                record.update(
                    status=status,
                    report=payload,
                    error=error,
                    finished_at=time.time(),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("eh_study worker failed")
            with PyPSAService.get_solver_state_lock():
                record.update(
                    status="failed",
                    report=None,
                    error=str(exc),
                    finished_at=time.time(),
                )

    _ctx = _contextvars.copy_context()
    t = _threading.Thread(
        target=lambda: _ctx.run(worker), daemon=True, name="adequacy-eh-study")
    record["thread"] = t
    publish_study("eh_study", record, t)
    return {
        "status": "running",
        "study": "eh_study",
        "archetype": archetype,
        "budget_solves": budget,
    }
