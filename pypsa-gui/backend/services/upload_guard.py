"""
Upload / archive safety helpers for the import endpoints.

Two process-wide guards against hostile or oversized uploads:

* ``read_capped`` — bounds an ``await file.read()`` so a multi-GB upload can't
  OOM the process (every import endpoint buffers the whole body into RAM).
* ``safe_extract`` — refuses zip members whose path escapes the destination
  (zip-slip / ``../`` traversal) before extracting.

Kept dependency-light (only fastapi's HTTPException + stdlib) so any router can
import it without a cycle.
"""
from __future__ import annotations

import os
import zipfile

from fastapi import HTTPException, UploadFile

# 512 MB — generous headroom for a large clustered network.nc / Excel workbook,
# while bounding the worst-case in-memory buffer. Override via env for big rigs.
# Parse defensively: a malformed env value must NOT crash backend startup (this
# module is imported at app load by the import routers) — fall back to the
# default instead.
_FALLBACK_MAX_UPLOAD_BYTES = 512 * 1024 * 1024
try:
    _DEFAULT_MAX_UPLOAD_BYTES = int(
        os.environ.get("PYPSA_GUI_MAX_UPLOAD_BYTES", str(_FALLBACK_MAX_UPLOAD_BYTES))
    )
    if _DEFAULT_MAX_UPLOAD_BYTES <= 0:
        _DEFAULT_MAX_UPLOAD_BYTES = _FALLBACK_MAX_UPLOAD_BYTES
except (TypeError, ValueError):
    _DEFAULT_MAX_UPLOAD_BYTES = _FALLBACK_MAX_UPLOAD_BYTES
_CHUNK = 1024 * 1024  # 1 MB read granularity


async def read_capped(file: UploadFile, max_bytes: int = _DEFAULT_MAX_UPLOAD_BYTES) -> bytes:
    """
    Read the whole upload but abort with HTTP 413 the moment it exceeds
    ``max_bytes``. Reads in 1 MB chunks so a hostile stream is rejected early
    rather than after the full body has been buffered.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                413,
                f"Upload exceeds the {max_bytes // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def safe_extract(zf: zipfile.ZipFile, dest: str) -> None:
    """
    Extract every member of ``zf`` into ``dest``, refusing any member whose
    resolved path escapes ``dest`` (zip-slip). Validates all members up front so
    a single bad entry rejects the whole archive with a clear 400 before any
    file is written.
    """
    dest_real = os.path.realpath(dest)
    for member in zf.namelist():
        target = os.path.realpath(os.path.join(dest, member))
        if target != dest_real and not target.startswith(dest_real + os.sep):
            raise HTTPException(400, f"Refusing unsafe zip member path: {member!r}")
    zf.extractall(dest)
