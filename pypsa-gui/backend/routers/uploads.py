"""
Chatbot upload REST API (Phase A + Phase C).

Mounted at ``/api/projects/{name}/uploads``:

  POST   /                       multipart upload (25 MB cap, MIME sniff, sha256 dedup)
  GET    /                       list project's uploads, newest-first
  GET    /{file_id}/meta          meta.json (UploadMeta)
  GET    /{file_id}/blob          raw bytes (inline Content-Disposition)
  DELETE /{file_id}               remove upload dir; always HTTP 200
  GET    /{file_id}/signature     Phase C HMAC token (5-min TTL) for multi-user mode

The router is thin — every endpoint does (1) validate, (2) call
``services.upload_service`` under the per-project lock, (3) shape the
response. All business rules live in `upload_service`.

Phase C scope: signature endpoint + nothing else here (the multimodal
content-block builder lives in `upload_service`; the wire-in to Anthropic
lives in `chat_service.run_turn`).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
import secrets
import time

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.responses import FileResponse, JSONResponse

from services import upload_service
from services.upload_guard import read_capped

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Phase C — signature endpoint ──────────────────────────────────────────
#
# Per-process random secret. Server restart invalidates every in-flight
# signature; the chat panel re-requests a fresh signature before sending
# attachments. The single-user posture currently in pypsa-gui means the
# signature is advisory — the multimodal wire-in in chat_service does
# NOT verify it. Phase C ships the endpoint so the contract is in place
# for the multi-user mode that will require signing.
_UPLOAD_SIG_SECRET = secrets.token_bytes(32)
_UPLOAD_SIG_TTL_SECONDS = 5 * 60


def _make_upload_signature(
    session_id: str, project: str, file_id: str, sha256: str,
    version: int, expires_at: int,
) -> str:
    payload = f"{session_id}|{project}|{file_id}|{sha256}|{version}|{expires_at}".encode()
    return hmac.new(_UPLOAD_SIG_SECRET, payload, hashlib.sha256).hexdigest()


# Office formats (xlsx/docx) are ZIP archives internally — python-magic
# reports `application/zip` for them because the magic bytes are `PK\x03\x04`
# (the standard zip header). Upgrading via filename extension lets the
# upload flow accept these without dropping the magic-byte safety net for
# truly mislabelled binaries: an `.xlsx` rename of a real EXE still sniffs
# as `application/x-dosexec` and gets rejected by the allowlist below.
_ZIP_EXTENSION_UPGRADES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xltx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _sniff_mime(blob: bytes, declared: str | None, filename: str = "") -> str:
    """
    Magic-byte MIME sniff via python-magic. Falls back to the declared
    MIME (from the multipart Content-Type) when python-magic isn't
    available — a soft-fail so the upload feature is usable in a
    pure-pip environment without libmagic.

    Special case: Office Open XML formats (xlsx, docx, xlsm, xltx, dotx)
    are zip archives. libmagic reports `application/zip` for them; we
    upgrade to the proper Office MIME based on the filename extension
    so the allowlist check accepts them. The upgrade only applies when
    libmagic already says `zip` — a renamed-binary attempt (e.g. `.xlsx`
    on top of a real EXE) sniffs as `application/x-dosexec` and never
    enters this upgrade path.
    """
    sniffed = ""
    try:
        import magic  # python-magic
        sniffed = magic.from_buffer(blob, mime=True) or ""
    except (ImportError, OSError):
        # libmagic missing on this platform — fall through to declared MIME
        pass
    if not sniffed:
        sniffed = declared or "application/octet-stream"
    # Apply the Office-zip upgrade.
    if sniffed == "application/zip" and filename:
        import os as _os
        ext = _os.path.splitext(filename)[1].lower()
        upgraded = _ZIP_EXTENSION_UPGRADES.get(ext)
        if upgraded:
            return upgraded
    return sniffed


def _validate_filename(filename: str | None) -> str:
    """
    Reject obviously-malicious filenames before any disk work. The
    canonical sanitiser in `filename_service.safe_upload_filename`
    handles the rendering form; here we just refuse the worst cases
    early so the error_kind is precise.
    """
    if not filename or not filename.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "invalid_filename",
                "message": "filename is empty",
            },
        )
    # Path-traversal guard. The on-disk path is the sha256 file_id, but
    # rejecting `..` here surfaces a clearer error than letting the
    # sanitiser silently strip it later.
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "invalid_filename",
                "message": "filename contains path-separator characters",
            },
        )
    return filename


@router.post("/{name}/uploads")
async def post_upload(name: str, file: UploadFile = File(...)) -> JSONResponse:
    """
    Upload a file to ``projects/<name>/uploads/``. Idempotent on bytes
    (same SHA256 returns the existing entry's meta). Validates size,
    MIME (sniffed from bytes), and quota before writing.
    """
    filename = _validate_filename(file.filename)

    # Bound the read to 25 MB + 1 byte. read_capped from upload_guard
    # already raises HTTPException(413) on overflow with a sensible
    # message; we override the detail to carry error_kind.
    try:
        blob_bytes = await read_capped(file, upload_service.MAX_FILE_BYTES)
    except HTTPException:
        raise HTTPException(
            status_code=413,
            detail={
                "error_kind": "file_too_large",
                "message": (
                    f"file exceeds the {upload_service.MAX_FILE_BYTES // (1024*1024)} MB per-file cap"
                ),
            },
        )

    if len(blob_bytes) == 0:
        raise HTTPException(
            status_code=400,
            detail={"error_kind": "empty_file", "message": "uploaded file is empty"},
        )

    # MIME sniff. Always derive the sniffed value; the declared MIME from
    # the multipart Content-Type is treated as an UNVERIFIED hint.
    declared_mime = file.content_type or mimetypes.guess_type(filename)[0]
    sniffed_mime = _sniff_mime(blob_bytes, declared_mime, filename)

    # The SNIFFED MIME is authoritative — that's what determines how the
    # downstream tools (and Anthropic) treat the bytes. We allowlist-check
    # the sniffed value; a mismatch between declared and sniffed is a
    # diagnostic signal but not, by itself, a security failure (e.g. CSV
    # files declare text/csv but python-magic typically sniffs them as
    # text/plain). The allowlist already rejects mislabelled binaries —
    # an EXE renamed `.pdf` sniffs as application/x-dosexec and is refused.
    if sniffed_mime not in upload_service.ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail={
                "error_kind": "unsupported_mime",
                "message": (
                    f"sniffed MIME {sniffed_mime!r} not in allowlist; supported: "
                    f"{', '.join(sorted(upload_service.ALLOWED_MIME_TYPES))}"
                ),
            },
        )

    meta = upload_service.add_upload(
        name=name,
        file_bytes=blob_bytes,
        filename=filename,
        sniffed_mime=sniffed_mime,
    )
    return JSONResponse(content=meta.model_dump(), status_code=200)


@router.get("/{name}/uploads")
def get_uploads(name: str) -> list[dict]:
    """List uploads for `name`, newest first."""
    return [m.model_dump() for m in upload_service.list_uploads(name)]


@router.get("/{name}/uploads/{file_id}/meta")
def get_upload_meta_route(name: str, file_id: str) -> dict:
    return upload_service.get_upload_meta(name, file_id).model_dump()


@router.get("/{name}/uploads/{file_id}/blob")
def get_upload_blob(name: str, file_id: str) -> FileResponse:
    """
    Stream the blob. Content-Disposition: inline so a browser previews
    images/PDFs in a new tab instead of forcing a download dialog. The
    UI's download button on agent_export chips sets ?download=1 to
    flip it to attachment.
    """
    path = upload_service.get_upload_path(name, file_id)
    meta = upload_service.get_upload_meta(name, file_id)
    return FileResponse(
        path=str(path),
        media_type=meta.mime,
        filename=meta.filename,
        headers={"Content-Disposition": f'inline; filename="{meta.filename}"'},
    )


@router.delete("/{name}/uploads/{file_id}")
def delete_upload_route(name: str, file_id: str) -> JSONResponse:
    """
    Idempotent delete. Always returns HTTP 200 — the JSON body's
    `deleted` field distinguishes success vs not_found / in_use.
    """
    resp = upload_service.delete_upload(name, file_id)
    return JSONResponse(content=resp.model_dump(exclude_none=True), status_code=200)


@router.get("/{name}/uploads/{file_id}/signature")
def get_upload_signature(name: str, file_id: str, session_id: str) -> dict:
    """
    Phase C — return an HMAC token binding ``(session_id, project, file_id,
    sha256, version)`` to a 5-minute expiry. The chat panel attaches the
    token to its multimodal turn so a future multi-user backend can
    refuse stale / cross-session references.

    The single-user backend currently treats the token as advisory: it is
    issued but not verified. ``GET /{file_id}/signature?session_id=...`` returns:

        {
          "file_id": str,
          "signature": hex,
          "expires_at": epoch_seconds,
          "sha256": full_hex,
          "version": int
        }

    Refresh by re-requesting before the expiry — the secret rotates on
    server restart, so a long-lived token from a previous boot is rejected.
    """
    meta = upload_service.get_upload_meta(name, file_id)
    expires_at = int(time.time()) + _UPLOAD_SIG_TTL_SECONDS
    sig = _make_upload_signature(
        session_id=session_id,
        project=name,
        file_id=file_id,
        sha256=meta.sha256,
        version=meta.version,
        expires_at=expires_at,
    )
    return {
        "file_id": file_id,
        "signature": sig,
        "expires_at": expires_at,
        "sha256": meta.sha256,
        "version": meta.version,
    }
