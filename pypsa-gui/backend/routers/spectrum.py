# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Spectrum input (AS-09, `services.adequacy.spectrum`).

* `POST /api/spectrum/trace` reads an uploaded power trace into its PSD, band energy and f_c, and returns the
  tier split of the open network's split storages as a dry run (each store's `smoothing_tier` before and
  after). It changes nothing, so it sits outside /api/network: no undo snapshot, no revision bump.
* `POST /api/network/spectrum/tier_split` writes that split for a given f_c. Under /api/network, the
  foreign-lock gate and the auto-undo middleware cover it like any other network edit.

The trace is read in memory and never stored (SAGC-1).
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from services import change_log_service
from services.adequacy import spectrum as S
from services.pypsa_service import PyPSAService
from services.upload_guard import read_capped

#: Mounted at /api/spectrum.
analysis_router = APIRouter()
#: Mounted at /api/network.
router = APIRouter()


def _refuse(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail={"error_kind": "bad_trace", "message": str(exc)})


def _no_store_note(split: dict) -> list[str]:
    if split and not any(v["eligible"] for v in split.values()):
        bound = next(iter(split.values()))["latency_bound_s"]
        return [f"no split storage responds within {bound:.3g} s, so none can sit in the smoothing tier; a power "
                "or energy floor is refused until one can"]
    return []


@analysis_router.post("/trace")
async def analyse_trace(
    file: UploadFile = File(...),
    unit: str | None = Form(None),
    band_lo_hz: float = Form(S.BAND_HZ[0]),
    band_hi_hz: float = Form(S.BAND_HZ[1]),
    limit: float = Form(S.IN_BAND_LIMIT),
):
    """Read a power trace (UTF-8 CSV/TSV: time in s, then active power with its unit) and return its spectrum,
    f_c, the equivalent (A, T) and the dry-run tier split of the open network. Changes nothing."""
    blob = await read_capped(file, max_bytes=S.MAX_TRACE_BYTES)
    try:
        # Parsing a capped upload is up to seconds of pandas: off the event loop, with the analysis.
        out = await run_in_threadpool(
            lambda: S.analyse(S.read_trace(blob, unit), (band_lo_hz, band_hi_hz), limit))
    except S.TraceError as exc:
        raise _refuse(exc) from exc
    f_c = out["f_c_hz"]
    out["tier_split"] = S.tier_split(PyPSAService.get_network(), f_c) if f_c else {}
    out["notes"] += _no_store_note(out["tier_split"])
    return out


class TierSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    f_c_hz: float = Field(gt=0, allow_inf_nan=False)
    #: Per-store response latency, s, in place of the technology's assumed control.
    latency_s: dict[str, float] | None = None


@router.post("/spectrum/tier_split")
def apply_tier_split(body: TierSplitRequest):
    """Set each split storage's `smoothing_tier` flag from f_c: on when its latency is at most 1/(2π f_c), off
    when not. Returns each store's flag before and after, its latency and the reason."""
    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        try:
            split = S.apply_tier_split(n, body.f_c_hz, body.latency_s)
        except S.TraceError as exc:
            raise _refuse(exc) from exc
        fast = sorted(k for k, v in split.items() if v["eligible"])
        changed = sorted(k for k, v in split.items() if v["before"] != v["after"])
        change_log_service.log(
            "update", "Store", ", ".join(split),
            f"Smoothing tier from f_c {body.f_c_hz:.3g} Hz (latency bound "
            f"{S.latency_bound_s(body.f_c_hz):.3g} s): {', '.join(fast) or 'none'} in the tier; "
            f"changed {', '.join(changed) or 'none'}.")
    return {"f_c_hz": body.f_c_hz, "tier_split": split, "notes": _no_store_note(split)}
