# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Spectrum input (AS-09): upload a power trace, read its PSD, band energy and f_c, and optionally set the
AS-03 smoothing tier from f_c (`services.adequacy.spectrum`). The trace is read in memory and never stored."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from services import change_log_service
from services.adequacy import spectrum as S
from services.pypsa_service import PyPSAService
from services.upload_guard import read_capped

router = APIRouter()


def _refuse(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail={"error_kind": "bad_trace", "message": str(exc)})


@router.post("/spectrum/trace")
async def upload_trace(
    file: UploadFile = File(...),
    unit: str | None = Form(None),
    band_lo_hz: float = Form(S.BAND_HZ[0]),
    band_hi_hz: float = Form(S.BAND_HZ[1]),
    limit: float = Form(S.IN_BAND_LIMIT),
    apply: bool = Form(False),
):
    """Read a power trace (CSV/TSV: time, then active power with its unit) and return its spectrum, f_c, the
    equivalent (A, T) and the tier split of the open network's split storages. With `apply`, set each split
    storage's `smoothing_tier` flag from that split."""
    blob = await read_capped(file)
    try:
        trace = S.read_trace(blob, unit)
        out = await run_in_threadpool(S.analyse, trace, (band_lo_hz, band_hi_hz), limit)
    except S.TraceError as exc:
        raise _refuse(exc) from exc
    n = PyPSAService.get_network()
    f_c = out["f_c_hz"]
    out["tier_split"] = S.tier_split(n, f_c) if f_c else {}
    out["applied"] = False
    if apply:
        if not f_c:
            raise _refuse(S.TraceError("the trace has no energy in the band, so it gives no f_c to apply"))
        with PyPSAService.get_lock():
            try:
                split = S.apply_tier_split(n, f_c)
            except S.TraceError as exc:
                raise _refuse(exc) from exc
            fast = sorted(k for k, v in split.items() if v["eligible"])
            change_log_service.log(
                "update", "Store", ", ".join(split),
                f"Spectrum f_c {f_c:.3g} Hz (latency bound {out['fast_latency_s']:.3g} s): "
                f"smoothing tier {', '.join(fast) or 'none'}; trace not stored.")
        out["tier_split"], out["applied"] = split, True
    return out
